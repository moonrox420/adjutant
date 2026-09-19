# Adjutant — Event-Driven Workflow & Service Contracts

**Companion to:** Adjutant PRD v1.0 · Technical Architecture v1.0
**Version:** 1.0 (Draft for engineering review)
**Date:** September 15, 2026
**Status:** Pre-build
**Machine-readable companion:** `adjutant-events.json` (46 events, 14 topics, JSON Schema 2020-12)

---

## 1. What This Document Decides

The architecture document named the services. This one defines the wire between them: who may call whom, what shape the message takes, which failures are retryable, and what a consumer is obligated to do. Three things are settled here that are expensive to change later.

1. **The orchestration/choreography boundary.** Which state transitions are driven by a durable workflow and which by an event. Getting this wrong produces a system where nobody can say why an ad went live.
2. **The event envelope and schema-evolution policy.** Cheap now, near-impossible once 46 event types have production consumers.
3. **Retryability.** Every error in the taxonomy is classified retryable or terminal. An `InsufficientTokenScope` that gets retried is a security event, not a transient fault.

---

## 2. The Orchestration/Choreography Boundary

This is the single most important rule in the document.

> **Temporal owns sequencing. The event bus owns notification.**
> No business-critical state transition may depend solely on an event being delivered.

Concretely:

| Concern | Mechanism | Why |
|---|---|---|
| "The plan was approved, so start deploying" | **Temporal signal** | Losing this loses money or launches nothing. It needs exactly-once, durable, replayable semantics with the workflow's full prior context. |
| "The plan was approved, so refresh the dashboard, meter the event, notify the client" | **Event** (`plan.approved`) | Losing one delivery is recoverable and no spend depends on it. |
| "Deployment finished, so arm monitoring" | **Both** — workflow starts the child; the event is the observability record | Monitoring must start even if the bus is down. |
| "Fatigue detected, so generate a refresh" | **Event consumed by a workflow starter**, with a reconciliation sweep | A missed fatigue event costs one refresh cycle, and the hourly `MonitorBrandWorkflow` re-derives it. Self-healing. |
| "Kill switch engaged" | **Synchronous call, then event fan-out, then verification read** | See §2.1. |

### 2.1 The kill-switch exception

`brand.kill_switch.engaged` is the only event with a hard latency SLO (60 seconds to full fan-out). It is nonetheless **not** the authority. `channel-gateway` reads the `brand_kill_switch` row on every single egress call, so a brand stays killed even if the bus never delivers anything. The event exists to make the pause fan-out fast, not to make it correct. Any channel that cannot be verified paused within five minutes raises a page.

This is the general pattern: **events accelerate, databases authorize.**

### 2.2 Why approval waits are not a state machine on a cron

An approval that takes four days is a workflow that sleeps four days and resumes with its full closure intact — the plan, the briefs, the channel slices, the reasoning. The alternative is persisting a `state` column and rebuilding context on every poll, which produces the question "which step was this in?" in a 2 a.m. incident. Temporal's `workflow.wait_condition` with a timer is the whole implementation.

---

## 3. Temporal Workflow Contracts

### 3.1 Workflow inventory

| Workflow | ID convention | Input | Terminal states | Max duration |
|---|---|---|---|---|
| `OnboardBrandWorkflow` | `onboard/{brand_id}` | `OnboardBrandInput` | `confirmed`, `abandoned` | 14 days |
| `CampaignPlanWorkflow` | `plan/{brand_id}/{plan_request_id}` | `PlanRequest` | `deployed`, `rejected`, `expired`, `voided` | 30 days |
| `CreativeSetWorkflow` | `creative/{creative_set_id}` | `CreativeSetRequest` | `approved`, `rejected`, `spec_failed` | 14 days |
| `DeploymentWorkflow` | `deploy/{deployment_id}` | `DeploymentRequest` | `live`, `partial`, `failed` | 6 hours |
| `MonitorBrandWorkflow` | `monitor/{brand_id}` | `brand_id` | long-running, cron schedule | continuous |
| `OptimizationWorkflow` | `optimize/{proposal_id}` | `ProposalExecution` | `applied`, `rejected_by_guardrail`, `failed` | 2 hours |
| `ReportWorkflow` | `report/{brand_id}/{period}` | `ReportRequest` | `delivered`, `failed` | 4 hours |

Workflow IDs are deterministic and reuse-policy `REJECT_DUPLICATE`. Starting `deploy/{deployment_id}` twice is a no-op rather than a double launch — the first line of defense against duplicate spend, before idempotency keys even come into play.

### 3.2 Signals, queries, and updates

Signals are the resume mechanism. Queries are read-only and must never mutate. Updates are synchronous-with-validation, used where the caller needs a verdict rather than fire-and-forget.

| Workflow | Signals | Queries | Updates |
|---|---|---|---|
| `OnboardBrandWorkflow` | `brand_graph_confirmed(GraphConfirmation)`, `abandon(Reason)` | `onboarding_status() -> OnboardingStatus` | `submit_graph_correction(Correction) -> ValidationResult` |
| `CampaignPlanWorkflow` | `plan_decision(ApprovalDecision)`, `plan_voided(HashChange)`, `kill_switch(KillScope)` | `plan_state() -> PlanState`, `blocking_on() -> BlockingReason` | `revise_budget(BudgetRevision) -> ValidationResult` |
| `CreativeSetWorkflow` | `creative_decision(ApprovalDecision)`, `revalidate_specs(SpecVersionBump)` | `creative_progress() -> CreativeProgress` | — |
| `DeploymentWorkflow` | `retry_channel(Channel)`, `rollback(RollbackRequest)`, `kill_switch(KillScope)` | `deployment_progress() -> ChannelProgressMap` | — |
| `MonitorBrandWorkflow` | `pause_monitoring()`, `resume_monitoring()`, `force_scan()` | `last_scan() -> ScanSummary` | — |
| `OptimizationWorkflow` | `proposal_decision(ApprovalDecision)` | `execution_state() -> ExecState` | — |

**Signal idempotency.** Signals may be delivered more than once. Every handler is written against a guard on the current state, not against an assumption of freshness:

```python
@workflow.signal
async def plan_decision(self, d: ApprovalDecision) -> None:
    if self._decision is not None:          # already decided; ignore replays
        workflow.logger.info("duplicate plan_decision ignored", extra={"token": d.token_id})
        return
    if d.subject_hash != self._plan_hash:   # decided against a stale version
        self._stale_decisions.append(d)     # recorded, never acted on
        return
    self._decision = d
```

The second guard matters more than the first. A reviewer who approves a plan, then someone edits the plan, then the approval signal lands — that must not deploy. The hash check makes it structurally impossible rather than procedurally unlikely.

### 3.3 Activity contracts

Retry policy is part of the contract, not an implementation detail. Timeouts below are `start_to_close`.

| Activity | Service | Timeout | Retries | Heartbeat | Idempotency key |
|---|---|---|---|---|---|
| `IngestBrandSources` | brand-svc | 10 min | 3, exp backoff | 30 s | `hash(brand_id, source_urls)` |
| `DraftBrandGraph` | agent-runtime | 5 min | 3 (schema retries) | — | `agent_run_id` |
| `RunStrategist` | agent-runtime | 8 min | 3 | — | `agent_run_id` |
| `CompliancePreCheck` | compliance-svc | 60 s | 5 | — | `hash(subject_id, subject_hash, corpus_version)` |
| `IssueApprovalRequest` | approval-svc | 10 s | 5 | — | `hash(subject_id, subject_hash, stage)` |
| `GenerateCopy` / `GenerateArt` | agent-runtime | 6 min | 3 | — | `agent_run_id` |
| `CommitSceneGraph` | creative-svc | 30 s | 5 | — | `creative_hash` |
| `RenderRendition` | render-svc | 4 min | 3 | 20 s | `hash(scene_graph, spec_version)` |
| `ValidateRendition` | render-svc | 30 s | 2 | — | `content_hash` |
| `ChannelPreflight` | channel-gateway | 90 s | 3 | — | `hash(deployment_id, channel)` |
| `UploadChannelAsset` | channel-gateway | 5 min | 5 | 30 s | `hash(brand_id, content_hash, channel)` |
| `ChannelBuild` | channel-gateway | 10 min | 5 | 30 s | `hash(brand_id, plan_hash, channel, object_path, attempt_class)` |
| `ChannelVerify` | channel-gateway | 2 min | 8 | — | read-only |
| `ChannelMutate` | channel-gateway | 3 min | 5 | — | `hash(proposal_id, channel, mutation_path)` |
| `SyncMetrics` | analytics-svc | 15 min | 5 | 60 s | `hash(brand_id, channel, window)` |
| `ScanFatigue` | analytics-svc | 3 min | 3 | — | `hash(brand_id, window_end)` |
| `RenderReport` | analytics-svc | 10 min | 3 | 60 s | `hash(brand_id, period, template_version)` |

**Non-retryable error classes.** These are registered in every relevant retry policy's `non_retryable_error_types`. Retrying them is either pointless or actively dangerous:

`TokenInvalid` · `TokenExpired` · `TokenVoided` · `TokenReplayed` · `InsufficientTokenScope` · `SpendCapExceeded` · `SubjectHashMismatch` · `ComplianceBlocked` · `RenditionInfeasible` · `SpecValidationFailed` · `PreflightFailed` · `BrandKilled` · `TenantContextMissing` · `GenerationCeilingExceeded` · `ChannelPolicyRejection`

The security-relevant ones — every `Token*` and `SpendCapExceeded` — also emit to `adj.audit.v1` before raising. A retry storm against the spend choke point should show up as an alert, not as backoff noise.

**Long-poll pattern for `ChannelVerify`.** Platforms return success then take minutes to make an object readable. Verify uses eight retries with exponential backoff to ~5 minutes, and a failure to verify marks the channel `partial`, never `live`. We do not infer liveness from a write response.

---

## 4. Event Bus Design

### 4.1 Envelope

Every message on every topic carries the v1 envelope (full schema in `adjutant-events.json`). Required fields: `event_id`, `event_type`, `event_version`, `occurred_at`, `produced_at`, `producer`, `brand_id`, `payload`.

```json
{
  "event_id": "01931f3e-8c2a-7c4e-9b11-6f0a2d4e8a01",
  "event_type": "deployment.channel.completed",
  "event_version": 1,
  "occurred_at": "2026-09-15T20:41:02.115Z",
  "produced_at": "2026-09-15T20:41:02.338Z",
  "producer": "channel-gateway@1.4.2",
  "brand_id": "aaaaaaaa-0000-0000-0000-000000000001",
  "account_id": "11111111-1111-1111-1111-111111111111",
  "actor": { "kind": "system" },
  "trace": { "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
             "workflow_id": "deploy/7c1f...", "run_id": "9ab3..." },
  "causation_id": "01931f3e-8b7d-7a19-8f52-c3d9e1b40000",
  "correlation_id": "dddddddd-0000-0000-0000-000000000004",
  "schema_ref": "https://schemas.adjutant.dev/deployment.channel.completed/v1.json",
  "payload": { "...": "..." }
}
```

Four fields do real work and are worth defending in review:

- **`occurred_at` vs `produced_at`.** The gap is outbox relay lag, and it is the metric that tells you the bus is behind before consumers start reporting stale data.
- **`brand_id` is never null.** It is the partition key and the tenant key. Platform-scoped events (`compliance.corpus.updated`) use the reserved nil UUID so the field is never optional and no consumer needs a null branch.
- **`causation_id` and `correlation_id` are distinct.** Causation is the immediate parent; correlation is stable across a plan's entire life. "Show me everything that happened because of this approval" is a correlation query; "what directly triggered this" is causation.
- **`redaction`.** When a producer strips a field before publish (recipient emails, for example), it declares the stripped paths. A consumer seeing an absent field then knows the difference between "not applicable" and "removed for privacy".

### 4.2 Topics and partitioning

Topics are coarse — one per domain, not one per event type — so that ordering holds across related events. `plan.drafted`, `plan.approved`, and `plan.voided` on the same topic and the same partition key means a consumer can never see the void before the approval.

Partition key is `brand_id` on every topic. This gives total ordering per tenant, which is the only ordering guarantee any consumer actually needs, and it distributes cleanly at 25,000 brands. The known cost: a single very large agency brand can create a hot partition. Accepted for V1; the mitigation if it bites is a composite key of `brand_id` plus a bounded sub-key on `adj.metrics.v1` only, since that is the only high-volume topic.

### 4.3 Transactional outbox

**Services never publish directly from a code path that also writes Postgres.** Dual-write between a database and a broker has no correct failure mode. Every producer writes to an outbox table in the same transaction as its state change, and a relay publishes from there.

```sql
-- Schema addendum. Not present in adjutant-schema.sql v1.0; shipped as ticket PLAT-14.
CREATE TABLE event_outbox (
    id              bigserial PRIMARY KEY,
    event_id        uuid        NOT NULL UNIQUE,
    brand_id        uuid        NOT NULL,
    event_type      text        NOT NULL,
    event_version   int         NOT NULL DEFAULT 1,
    topic           text        NOT NULL,
    partition_key   text        NOT NULL,
    envelope        jsonb       NOT NULL,
    occurred_at     timestamptz NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    published_at    timestamptz,
    publish_attempts int        NOT NULL DEFAULT 0,
    last_error      text,
    CONSTRAINT outbox_envelope_has_payload
        CHECK (envelope ? 'payload' AND envelope ? 'event_type')
);

-- Relay scan path: only unpublished rows, oldest first.
CREATE INDEX event_outbox_unpublished
    ON event_outbox (created_at)
    WHERE published_at IS NULL;

-- Poison detection: anything tried more than 10 times needs a human.
CREATE INDEX event_outbox_stuck
    ON event_outbox (publish_attempts DESC)
    WHERE published_at IS NULL AND publish_attempts > 10;
```

The relay claims batches with `FOR UPDATE SKIP LOCKED`, publishes, then marks published. A crash between publish and mark produces a duplicate, which is exactly why consumers dedupe on `event_id`. At-least-once plus consumer idempotency is a correct system; exactly-once delivery is not available and pretending otherwise is how dual-write bugs get shipped.

Published rows are reaped after 7 days by a partition-drop job.

### 4.4 Consumer obligations

A service is not permitted to consume a topic without meeting all of these. This list is the code-review checklist for any new consumer.

1. **Dedupe on `event_id`**, with a retention window at least 2× the topic's retention or 24 hours, whichever is greater.
2. **Validate the envelope and reject on failure.** No partial interpretation of a malformed message. Rejects go to `<topic>.dlq`.
3. **Tolerate unknown payload fields.** Additive changes must not break you; see §4.5.
4. **Tolerate out-of-order across partitions,** and assume total order only within one `brand_id`.
5. **Never treat an event as authorization.** If a decision moves money, changes permissions, or writes to a channel, re-read the authoritative row first. Events carry `token_id` for correlation and never carry a usable token.
6. **Register the subscription** in the consumer registry. An unregistered consumer is invisible to the schema-change impact analysis, which makes it the thing that breaks silently.
7. **Emit consumer lag** as a first-class metric per `(topic, consumer_group, partition)`.

### 4.5 Schema evolution

| Change | Allowed? | How |
|---|---|---|
| Add an optional payload field | Yes, in place | No version bump. Consumers already tolerate unknown fields. |
| Add a value to a payload enum | Yes, with notice | Announce one release ahead. Consumers must have a default branch for unrecognized enum values. |
| Make an optional field required | No | New `event_version`. |
| Remove or rename a field | No | New `event_version`. |
| Narrow a type or constraint | No | New `event_version`. |
| Change the meaning of a field | No, and never silently | New `event_version`, and a new field name even if the old one is freed. Semantic drift on a stable name is the worst bug class here. |

**Versioning mechanics.** A new version publishes to the same topic with `event_version: 2`. Producers dual-publish v1 and v2 for a minimum of 30 days or until the consumer registry shows no v1 consumers, whichever is longer. CI enforces backward compatibility against the previous published schema; the check is a required status on every pull request that touches `adjutant-events.json`.

### 4.6 Dead letters

Every topic has `<topic>.dlq`. Messages land there on envelope validation failure, payload schema failure, or after a consumer exhausts its retries. A DLQ message retains the original envelope plus a `dlq_reason`, `consumer_group`, and `attempt_count`. Nonzero DLQ depth on `adj.approval.v1`, `adj.control.v1`, or `adj.audit.v1` pages immediately; other topics alert at depth > 100 sustained for 10 minutes.

---

## 5. Event Catalog

46 events across 14 topics. `adjutant-events.json` is the source of truth; the tables below are generated from it.

### `adj.brand.v1` — Brand lifecycle

| Event | Producer → committed consumers | Purpose | Required payload | Ret. |
|---|---|---|---|---|
| **`brand.created`** | core-api → orchestrator · brand-svc · analytics-svc · billing-svc | Starts OnboardBrandWorkflow and provisions tenant storage prefixes and vector namespace. | `brand_id`, `account_id`, `account_type`, `display_name` | 90d |
| **`brand.graph.drafted`** | brand-svc → orchestrator · core-api | Draft Brand Graph is ready for human confirmation. Carries the confidence surface the UI uses to order review. | `brand_id`, `graph_version`, `assertion_count`, `low_confidence_paths` | 90d |
| **`brand.graph.confirmed`** | brand-svc → orchestrator · creative-svc · compliance-svc · analytics-svc | Human confirmed the graph. Gate for any generation: no creative may be produced against an unconfirmed graph (BG-4). | `brand_id`, `graph_version`, `confirmed_by`, `confirmed_paths` | 90d |
| **`brand.kit.updated`** | brand-svc → creative-svc · render-svc | Palette, typeface, logo or tone changed. Invalidates the render cache for affected scene graphs. | `brand_id`, `kit_version`, `changed_fields` | 90d |


### `adj.control.v1` — Control plane

| Event | Producer → committed consumers | Purpose | Required payload | Ret. |
|---|---|---|---|---|
| **`brand.kill_switch.engaged`** | core-api → channel-gateway · orchestrator · analytics-svc · core-api | Highest-priority control event. Gateway must refuse all egress for this brand and fan out pause mutations. | `brand_id`, `engaged_by`, `scope`, `reason` | 10y |

> **`brand.kill_switch.engaged`** — Kill switch is ALSO a synchronous call. The event is the fan-out mechanism, never the authority. Gateway reads brand_kill_switch on every egress call regardless.


### `adj.plan.v1` — Plan lifecycle

| Event | Producer → committed consumers | Purpose | Required payload | Ret. |
|---|---|---|---|---|
| **`plan.drafted`** | orchestrator → compliance-svc · core-api · analytics-svc | Strategist produced a plan. Triggers compliance pre-check before it can reach a human. | `brand_id`, `plan_id`, `plan_hash`, `channels`, `monthly_budget_usd`, `objective` | 90d |
| **`plan.compliance_checked`** | compliance-svc → orchestrator · core-api | Plan-level verdict. A block verdict prevents the plan from entering the approval queue. | `brand_id`, `plan_id`, `plan_hash`, `verdict`, `corpus_version` | 90d |
| **`plan.approved`** | approval-svc → orchestrator · core-api · analytics-svc | Notification only. The authoritative resume signal is the Temporal signal, not this event (see contract doc §3.2). | `brand_id`, `plan_id`, `plan_hash`, `approval_request_id`, `token_id` | 10y |
| **`plan.rejected`** | approval-svc → orchestrator · analytics-svc · learning-svc | Rejection reasons are training signal for the Strategist; this is the feedback loop for approval-accept rate. | `brand_id`, `plan_id`, `approval_request_id`, `decision` | 10y |
| **`plan.voided`** | approval-svc → orchestrator · channel-gateway · core-api | The plan mutated after approval. Every outstanding token for it is dead. Emitted by the same transaction that voids them. | `brand_id`, `plan_id`, `old_plan_hash`, `new_plan_hash`, `voided_token_ids` | 10y |


### `adj.creative.v1` — Creative pipeline

| Event | Producer → committed consumers | Purpose | Required payload | Ret. |
|---|---|---|---|---|
| **`creative.set.requested`** | orchestrator → agent-runtime · core-api | One brief fanned out into a creative set. Carries the generation budget ceiling for cost control. | `brand_id`, `creative_set_id`, `plan_id`, `brief_id`, `channels`, `usd_generation_ceiling` | 90d |
| **`creative.concept.generated`** | agent-runtime → creative-svc · analytics-svc | A distinct concept (angle + hook + visual direction), before any rendering. | `brand_id`, `creative_set_id`, `concept_id`, `angle` | 90d |
| **`creative.scene_graph.committed`** | creative-svc → render-svc · compliance-svc · core-api | Immutable scene graph version pinned. Render fan-out keys off this. | `brand_id`, `creative_id`, `creative_set_id`, `scene_graph_version`, `creative_hash`, `target_specs` | 90d |
| **`creative.rendition.rendered`** | render-svc → creative-svc · compliance-svc · channel-gateway | One rendition produced. Content-addressed, so a repeat of the same (graph, spec) is a cache hit and emits with cache_hit=true. | `brand_id`, `creative_id`, `rendition_id`, `channel`, `placement`, `content_hash`, `s3_key` | 90d |
| **`creative.spec_validation.failed`** | render-svc → orchestrator · creative-svc · core-api | Hard stop. A creative that fails spec validation cannot enter the approval queue (CR-2). Reviewers never see broken renditions. | `brand_id`, `creative_id`, `channel`, `placement`, `failure_code` | 90d |
| **`creative.set.approved`** | approval-svc → orchestrator · core-api | Notification of creative-set approval. Token id included for audit correlation only. | `brand_id`, `creative_set_id`, `creative_hash`, `approval_request_id`, `token_id` | 10y |


### `adj.compliance.v1` — Compliance

| Event | Producer → committed consumers | Purpose | Required payload | Ret. |
|---|---|---|---|---|
| **`compliance.check.completed`** | compliance-svc → orchestrator · core-api · analytics-svc | Asset-level verdict with the full finding set and the disclosures actually applied. | `brand_id`, `compliance_record_id`, `subject_type`, `subject_id`, `verdict`, `corpus_version` | 10y |
| **`compliance.override.recorded`** | compliance-svc → core-api · analytics-svc · security-audit | An Owner overrode a block. Permanently retained, separately alertable, and surfaced in the audit export. | `brand_id`, `compliance_record_id`, `overridden_by`, `justification` | 10y |
| **`compliance.corpus.updated`** | compliance-svc → orchestrator · core-api | Policy corpus version bump, including rules promoted from the rejection-intelligence loop. | `corpus_version`, `channels_affected`, `change_summary` | 10y |


### `adj.approval.v1` — Approval and spend authority

| Event | Producer → committed consumers | Purpose | Required payload | Ret. |
|---|---|---|---|---|
| **`approval.requested`** | approval-svc → core-api · notification-svc · analytics-svc | Something needs a human. Drives the queue UI, the notification fan-out, and the queue-latency SLO. | `brand_id`, `approval_request_id`, `subject_type`, `subject_id`, `subject_hash`, `stage`, `expires_at` | 90d |
| **`approval.decided`** | approval-svc → orchestrator · core-api · analytics-svc | Terminal human decision at one stage. pending_client transitions require a prior internal approval. | `brand_id`, `approval_request_id`, `stage`, `decision`, `decided_by`, `decided_at` | 10y |
| **`approval.token.issued`** | approval-svc → channel-gateway · security-audit | A spend authority now exists. Payload carries token metadata only — never the signature or the serialized token. | `brand_id`, `token_id`, `approval_request_id`, `subject_type`, `subject_id`, `subject_hash`, `scopes`, `usd_daily_cap`, `usd_total_cap`, `expires_at` | 10y |
| **`approval.token.consumed`** | channel-gateway → approval-svc · analytics-svc · security-audit | One (token, channel, operation) tuple burned, with the USD actually committed against the caps. | `brand_id`, `token_id`, `channel`, `operation`, `idem_key`, `usd_committed` | 10y |
| **`approval.token.voided`** | approval-svc → channel-gateway · orchestrator · security-audit | Token killed before use. Gateway drops it from its local validity cache immediately. | `brand_id`, `token_id`, `reason` | 10y |
| **`approval.expired`** | approval-svc → orchestrator · core-api · notification-svc · analytics-svc | Nobody decided in time. Feeds the abandonment metric that tells us whether the queue is a bottleneck. | `brand_id`, `approval_request_id`, `subject_type`, `stage`, `age_seconds` | 90d |

> **`approval.token.issued`** — The signature and nonce are deliberately absent. Publishing a usable token on a topic with 90-day retention would make the bus a spend-authority store.


### `adj.deployment.v1` — Deployment

| Event | Producer → committed consumers | Purpose | Required payload | Ret. |
|---|---|---|---|---|
| **`deployment.started`** | orchestrator → channel-gateway · core-api · analytics-svc | Deployment record opened. Channels proceed as independent units of success from here. | `brand_id`, `deployment_id`, `plan_id`, `plan_hash`, `token_id`, `channels` | 90d |
| **`deployment.channel.preflight_failed`** | channel-gateway → orchestrator · core-api · notification-svc | One channel is blocked before any spend. Other channels continue; this is not a deployment-wide failure. | `brand_id`, `deployment_id`, `channel`, `failure_code`, `remediation` | 90d |
| **`deployment.channel.completed`** | channel-gateway → orchestrator · core-api · analytics-svc | Per-channel terminal outcome with verified native ids. Verification is a read-back, not an assumption from the write response. | `brand_id`, `deployment_id`, `channel`, `outcome`, `objects` | 90d |
| **`deployment.completed`** | orchestrator → analytics-svc · core-api · notification-svc · orchestrator | Arms MonitorBrandWorkflow. Terminal for the deployment aggregate. | `brand_id`, `deployment_id`, `outcome`, `channel_outcomes` | 10y |


### `adj.channel.v1` — Channel state

| Event | Producer → committed consumers | Purpose | Required payload | Ret. |
|---|---|---|---|---|
| **`channel.connection.health_changed`** | channel-gateway → core-api · orchestrator · notification-svc · approval-svc | Credential or account health transition. A revoked connection voids outstanding tokens scoped to that channel. | `brand_id`, `connection_id`, `channel`, `from_health`, `to_health` | 90d |
| **`channel.object.state_changed`** | channel-gateway → analytics-svc · core-api · orchestrator | Structure sync found drift between our intent and the platform's reality. Includes changes made outside Adjutant. | `brand_id`, `campaign_object_id`, `channel`, `native_id`, `from_state`, `to_state`, `detected_by` | 90d |
| **`channel.rejection.received`** | channel-gateway → compliance-svc · core-api · orchestrator · notification-svc | Platform disapproval captured verbatim. Input to the rejection-intelligence loop that grows the policy corpus. | `brand_id`, `channel`, `native_id`, `raw_reason` | 10y |
| **`channel.quota.exhausted`** | channel-gateway → core-api · orchestrator · analytics-svc | Governor bucket empty. Surfaces real queue position to the user instead of a spinner. | `brand_id`, `channel`, `bucket_key`, `queue_depth`, `estimated_drain_seconds` | 30d |


### `adj.metrics.v1` — Metrics and detection

| Event | Producer → committed consumers | Purpose | Required payload | Ret. |
|---|---|---|---|---|
| **`metric.batch.ingested`** | analytics-svc → orchestrator · analytics-svc | Watermark event for a completed metric window. Downstream scans trigger off watermarks, never off wall-clock. | `brand_id`, `channel`, `window_start`, `window_end`, `row_count`, `watermark_complete` | 14d |
| **`metric.anomaly.detected`** | analytics-svc → orchestrator · core-api · notification-svc | Statistically significant deviation, with the significance evidence attached so downstream can refuse weak signals. | `brand_id`, `campaign_object_id`, `channel`, `metric`, `direction`, `magnitude_pct`, `confidence` | 90d |
| **`fatigue.detected`** | analytics-svc → orchestrator · creative-svc · core-api | Compound fatigue only. Consumers must reject any message with fewer than three signals fired. | `brand_id`, `campaign_object_id`, `channel`, `signals_fired`, `window_days`, `format_class` | 90d |

> **`fatigue.detected`** — minItems: 3 on signals_fired is the schema-level mirror of the fatigue_requires_compound_signals database constraint. Enforced in two places on purpose.


### `adj.optimization.v1` — Optimization

| Event | Producer → committed consumers | Purpose | Required payload | Ret. |
|---|---|---|---|---|
| **`optimization.proposal.created`** | agent-runtime → approval-svc · core-api · orchestrator | The Optimizer proposes; it never writes. Its tool allowlist contains no write verbs. | `brand_id`, `proposal_id`, `kind`, `targets`, `expected_effect`, `auto_approve_eligible` | 90d |
| **`optimization.proposal.decided`** | approval-svc → orchestrator · analytics-svc · learning-svc | Human or rule decision. auto_approved is recorded distinctly from human_approved for audit and for autonomy-graduation analysis. | `brand_id`, `proposal_id`, `decision` | 10y |
| **`optimization.executed`** | channel-gateway → analytics-svc · core-api · learning-svc | Mutation applied and verified, with the guardrail re-check result recorded at execution time. | `brand_id`, `proposal_id`, `action_id`, `channel`, `outcome` | 10y |
| **`optimization.reverted`** | channel-gateway → analytics-svc · core-api · notification-svc | Rollback executed by walking action.revert_path in reverse. | `brand_id`, `action_id`, `reverted_by_action_id`, `reason` | 10y |

> **`optimization.executed`** — rejected_by_guardrail is the expected outcome for a proposal approved Monday that violates a ceiling by Wednesday. It is not an error.


### `adj.cost.v1` — Cost metering

| Event | Producer → committed consumers | Purpose | Required payload | Ret. |
|---|---|---|---|---|
| **`agent.run.completed`** | agent-runtime → analytics-svc · billing-svc | Per-invocation cost meter. This topic is the sole input to margin-per-brand; nothing else may claim generation cost. | `brand_id`, `agent_run_id`, `agent_name`, `model`, `usd_cost`, `outcome` | 400d |
| **`cost.budget.threshold_crossed`** | analytics-svc → agent-runtime · core-api · notification-svc | Generation budget guard. Crossing hard degrades the brand to a cheaper model tier rather than silently burning margin. | `brand_id`, `period`, `threshold`, `usd_spent`, `usd_ceiling`, `action_taken` | 400d |


### `adj.learning.v1` — Learning

| Event | Producer → committed consumers | Purpose | Required payload | Ret. |
|---|---|---|---|---|
| **`learning.signal.recorded`** | analytics-svc → creative-svc · agent-runtime | Outcome attached to creative attributes. Global-scope signals must be de-identified and past the k-anonymity threshold. | `brand_id`, `signal_id`, `scope`, `sample_size` | 400d |

> **`learning.signal.recorded`** — Consumers must drop any scope=global message with deidentified=false. Mirrors the global_signals_deidentified constraint.


### `adj.report.v1` — Reporting

| Event | Producer → committed consumers | Purpose | Required payload | Ret. |
|---|---|---|---|---|
| **`report.generated`** | analytics-svc → core-api · notification-svc | Report artifact ready. Cross-channel aggregates carry their methodology annotation as a first-class field. | `brand_id`, `report_id`, `period_start`, `period_end`, `s3_key` | 90d |
| **`report.delivered`** | notification-svc → core-api · analytics-svc | Delivery receipt per recipient. | `brand_id`, `report_id`, `channel_medium`, `outcome` | 400d |

> **`report.delivered`** — recipient_email is redacted at publish time. The envelope's redaction block records that it happened.


### `adj.audit.v1` — Audit mirror

| Event | Producer → committed consumers | Purpose | Required payload | Ret. |
|---|---|---|---|---|
| **`action.recorded`** | core-api → analytics-svc · security-audit | Mirror of an append-only audit-ledger row, for real-time security monitoring. The database remains the record of truth. | `brand_id`, `action_id`, `action_type`, `actor_kind`, `target_kind` | 10y |

> **`action.recorded`** — Emitted from the transactional outbox in the same transaction as the ledger insert, so the bus cannot disagree with the ledger.


---

## 6. Synchronous Service Contracts

Internal calls are gRPC with protobuf; the public surface is REST plus GraphQL at `core-api`. Every internal call carries the tenant context and the trace context in metadata. A call arriving without tenant context fails `TenantContextMissing` — it does not fall back to a permissive default.

### 6.1 `approval-svc`

The choke point. This service is small on purpose: the fewer lines of code that can issue a spend authority, the more completely they can be audited.

| Method | Request → Response | Errors | P95 | Idempotent |
|---|---|---|---|---|
| `CreateApprovalRequest` | `(brand_id, subject_type, subject_id, subject_hash, stage, usd_daily, usd_total, expires_at)` → `ApprovalRequest` | `ComplianceBlocked`, `SubjectHashMismatch`, `ApproverThresholdUnmet` | 80 ms | Yes, on `hash(subject_id, subject_hash, stage)` |
| `Decide` | `(approval_request_id, decision, decided_by, reason_codes)` → `Decision` | `AlreadyDecided`, `Expired`, `InsufficientRole`, `ClientStageBeforeInternal` | 120 ms | Yes, on `(request_id, stage)` |
| `IssueToken` | `(approval_request_id)` → `SignedToken` | `NotApproved`, `SubjectHashMismatch`, `ExpiryBeyondMax` | 60 ms | Yes, returns the existing live token |
| `ValidateToken` | `(token, channel, operation, usd_requested)` → `ValidationResult` | `TokenInvalid`, `TokenExpired`, `TokenVoided`, `TokenReplayed`, `InsufficientTokenScope`, `SpendCapExceeded` | **15 ms** | Read-only |
| `ConsumeToken` | `(token_id, channel, operation, idem_key, usd_committed)` → `ConsumptionReceipt` | `TokenReplayed`, `SpendCapExceeded`, `TokenVoided` | 40 ms | Yes, on `(token_id, channel, operation, idem_key)` |
| `VoidTokens` | `(subject_id, subject_hash, reason)` → `VoidResult` | — | 50 ms | Yes |

`ValidateToken` has the tightest latency budget in the system because it sits inline before every channel write. It is served from Redis with a Postgres write-through, and it fails closed: a Redis outage means no writes happen, not that writes proceed unchecked.

**Never exposed, on any surface:** a method that issues a token without an approved `approval_request`, or that extends an expiry. Re-approval issues a new token. There is no refresh path to audit.

### 6.2 `channel-gateway`

| Method | Request → Response | Errors | P95 | Idempotent |
|---|---|---|---|---|
| `GetCapabilities` | `(channel, version?)` → `CapabilityRegistryEntry` | `UnknownChannel` | 10 ms | Read-only |
| `GetSpecs` | `(channel, placements[])` → `SpecRegistryEntry[]` | `UnknownPlacement` | 10 ms | Read-only |
| `Connect` | `(brand_id, channel, auth_grant, credential_mode)` → `ChannelConnection` | `AuthGrantInvalid`, `ByokRequiresLocalStorage`, `AccountNotAccessible` | 2 s | No |
| `Preflight` | `(deployment_id, channel, plan_slice)` → `PreflightResult` | `PreflightFailed`, `ConnectionUnhealthy` | 3 s | Yes |
| `Build` | `(token, channel, plan_slice, creatives, idem_key)` → `BuildResult` | all `Token*`, `SpendCapExceeded`, `QuotaExhausted`, `ChannelPolicyRejection`, `ChannelUnavailable`, `BrandKilled` | 45 s | **Yes, mandatory** |
| `Mutate` | `(token, channel, mutation, idem_key)` → `MutationResult` | as `Build`, plus `GuardrailViolation` | 8 s | **Yes, mandatory** |
| `Verify` | `(channel, native_ids[])` → `ObjectState[]` | `ChannelUnavailable` | 5 s | Read-only |
| `ReadStructure` | `(connection_id, since?)` → `CampaignObject[]` | `ConnectionUnhealthy` | 30 s | Read-only |
| `AcquireQuota` | `(channel, ad_account_id, cost, priority)` → `QuotaLease` | `QuotaExhausted` | 5 ms | No |

`Build` and `Mutate` are the only methods that accept a token and the only ones permitted to write. Both assert token validity locally before the first outbound HTTP call — the check is never delegated to the caller. Both require `idem_key`; the signature makes it non-optional so it cannot be forgotten.

**Adapter conformance is a contract, not a convention.** Every adapter passes the same suite before its channel ships: capability declaration matches observed behavior, all nine write paths idempotent under induced timeout, verification catches a silently-failed write, rejection capture preserves verbatim text, quota model respected without empirical probing. This is how "no platform is a higher priority than another" gets verified rather than asserted.

### 6.3 `compliance-svc`

| Method | Request → Response | Errors | P95 | Idempotent |
|---|---|---|---|---|
| `CheckPlan` | `(brand_id, plan_id, plan_hash)` → `ComplianceRecord` | `CorpusUnavailable` | 400 ms | Yes, on `(subject, hash, corpus_version)` |
| `CheckCreative` | `(brand_id, creative_id, creative_hash, target_geos[])` → `ComplianceRecord` | `CorpusUnavailable`, `LikenessServiceUnavailable` | 2.5 s | Yes |
| `ApplyDisclosures` | `(creative_id, regimes[])` → `DisclosurePlan` | `DisclosureInfeasible` | 300 ms | Yes |
| `SubstantiateClaims` | `(brand_id, claims[])` → `SubstantiationResult` | — | 150 ms | Read-only |
| `RecordOverride` | `(compliance_record_id, overridden_by, justification)` → `Override` | `InsufficientRole`, `JustificationTooShort` | 60 ms | No |
| `IngestRejection` | `(brand_id, channel, native_id, raw_reason)` → `Classification` | — | 200 ms | Yes, on `(channel, native_id, hash(raw_reason))` |

`CheckCreative` is blocking authority. There is no code path from creative to channel that bypasses it, including admin tooling and backfills. `DisclosureInfeasible` — a required disclosure that cannot be placed without violating the creative's safe zones — is terminal and routes back to creative generation, never waived.

### 6.4 `creative-svc` and `render-svc`

| Method | Service | Request → Response | Errors | P95 |
|---|---|---|---|---|
| `CommitSceneGraph` | creative-svc | `(brand_id, creative_set_id, scene_graph)` → `CommittedCreative` | `SceneGraphInvalid`, `BrandGraphUnconfirmed`, `TextLayerRasterized` | 90 ms |
| `DeriveRendition` | creative-svc | `(creative_id, channel, placement)` → `RenditionSpec` | `RenditionInfeasible` | 120 ms |
| `Render` | render-svc | `(scene_graph, spec, spec_version)` → `RenditionAsset` | `RenditionInfeasible`, `AssetUnavailable`, `FontUnavailable` | 3.5 s (0 ms on cache hit) |
| `Validate` | render-svc | `(rendition_id)` → `ValidationReport` | `SpecValidationFailed` | 400 ms |

`Render` is a pure function keyed on `hash(scene_graph, spec_version)`. Same inputs, same bytes, always. This is what makes nine renditions nine renders of one graph rather than nine generations, and what makes a text edit nearly free to re-render.

`BrandGraphUnconfirmed` blocks generation against an unconfirmed Brand Graph. `TextLayerRasterized` is a structural violation of the creative model and fails the commit — text renders typographically or not at all.

### 6.5 `agent-runtime`

| Method | Request → Response | Errors | P95 |
|---|---|---|---|
| `Invoke` | `(agent_name, brand_id, input, model_tier?, usd_ceiling)` → `AgentResult` | `SchemaValidationExhausted`, `GenerationCeilingExceeded`, `ToolNotAllowlisted`, `ModelUnavailable` | varies by agent |
| `DescribeAgent` | `(agent_name)` → `AgentBundle` | `UnknownAgent` | 5 ms |

Every invocation writes an `agent_run` row and emits `agent.run.completed` with USD cost and brand attribution. An agent whose structured output fails schema validation three times raises `SchemaValidationExhausted` and escalates to a human task — it does not degrade to unstructured text.

**Tool allowlists are enforced here, at dispatch.** The Copywriter has no channel-gateway verb. The Optimizer has read verbs and `create_proposal`, and no write verb exists in its list. `agent-runtime` additionally has **no network route** to ad platforms, so a prompt-injected agent cannot reach one even with a forged tool call. Capability is removed, not merely denied.

### 6.6 `analytics-svc`

| Method | Request → Response | Errors | P95 |
|---|---|---|---|
| `IngestMetrics` | `(brand_id, channel, window, facts[])` → `IngestReceipt` | `WindowAlreadySealed` | 1.5 s |
| `QueryPerformance` | `(brand_id, dimensions[], metrics[], window)` → `ResultSet` | `ComparabilityViolation` | 600 ms |
| `ScoreFatigue` | `(brand_id, window)` → `FatigueScore[]` | — | 900 ms |
| `MarginByBrand` | `(brand_id, period)` → `MarginRollup` | — | 200 ms |

`ComparabilityViolation` is raised when a caller requests a cross-channel aggregate over `caveated` metrics without accepting a methodology annotation. **There is no code path that returns a single blended ROAS without one.** The PRD names blended-metric misinterpretation as a top-five risk; this error is where the mitigation actually lives. It is more useful as a hard error than as a tooltip.

### 6.7 Error taxonomy

Every error carries a stable code, a gRPC status, an HTTP status, and a retryability classification. Retryability is part of the contract; a caller must not infer it.

| Code | gRPC | HTTP | Retryable | Notes |
|---|---|---|---|---|
| `TenantContextMissing` | `PERMISSION_DENIED` | 403 | No | Bug or attack. Alerts. |
| `TokenInvalid` / `TokenExpired` / `TokenVoided` | `PERMISSION_DENIED` | 403 | **No** | Emits to audit topic. |
| `TokenReplayed` | `ALREADY_EXISTS` | 409 | **No** | Security alert, not a warning. |
| `InsufficientTokenScope` | `PERMISSION_DENIED` | 403 | **No** | Security alert. |
| `SpendCapExceeded` | `FAILED_PRECONDITION` | 422 | **No** | Audit + notify approver. |
| `SubjectHashMismatch` | `ABORTED` | 409 | No | Expected whenever a subject was edited after approval. |
| `ComplianceBlocked` | `FAILED_PRECONDITION` | 422 | No | Terminal; routes to remediation. |
| `RenditionInfeasible` / `SpecValidationFailed` | `INVALID_ARGUMENT` | 422 | No | Routes to creative revision. |
| `PreflightFailed` | `FAILED_PRECONDITION` | 422 | No | Per-channel; others proceed. |
| `BrandKilled` | `FAILED_PRECONDITION` | 423 | No | Hard stop on all egress. |
| `GuardrailViolation` | `FAILED_PRECONDITION` | 422 | No | Execution-time re-check failed. Expected, not exceptional. |
| `QuotaExhausted` | `RESOURCE_EXHAUSTED` | 429 | **Yes**, after lease | Governor supplies the wait; caller never spins. |
| `ChannelUnavailable` | `UNAVAILABLE` | 502 | **Yes** | Exponential backoff with jitter. |
| `ChannelPolicyRejection` | `FAILED_PRECONDITION` | 422 | No | Feeds rejection intelligence. |
| `ConnectionUnhealthy` | `FAILED_PRECONDITION` | 424 | No | Needs user reauth. |
| `GenerationCeilingExceeded` | `RESOURCE_EXHAUSTED` | 429 | No | Degrades model tier instead. |
| `SchemaValidationExhausted` | `INTERNAL` | 500 | No | Escalates to human task. |
| `ComparabilityViolation` | `INVALID_ARGUMENT` | 400 | No | Caller must accept annotation. |
| `AlreadyDecided` / `ClientStageBeforeInternal` | `FAILED_PRECONDITION` | 409 | No | Approval state-machine guards. |

---

## 7. Webhook Ingress

`POST /webhooks/{channel}` is the single ingress. Contract for every channel:

1. **Verify the signature before parsing the body.** Unverified payloads are dropped and counted, never parsed.
2. **Dedupe on the platform's delivery id**, persisted for 7 days.
3. **Return 200 within 500 ms.** Enqueue to `adj.channel.v1` and do the work asynchronously. Platforms disable endpoints that are slow or erroring.
4. **Never trust the payload as state.** A webhook is a hint to reconcile. The handler enqueues a `ReadStructure` or `Verify` against the platform API and lets the read-back be authoritative. A webhook claiming an ad is active does not make it active in our model.
5. **Replay tolerance.** Handlers are idempotent, since platforms redeliver.

Push availability differs materially across the nine channels, and for several the only reliable mechanism is polling. Rather than assert per-channel behavior here, the Capability Registry carries a `change_notification` block per channel — `{ mechanism: webhook | polling, poll_interval_seconds, verified_on }` — populated by **ticket GW-9**, which audits each platform's current push support during Phase 0. Channels without push get a declared structure-sync cadence. No business logic anywhere reads a channel name to decide how it learns about change; it reads the registry.

---

## 8. Observability Contract

Every span carries `brand_id`, `channel` where applicable, `token_id` where applicable, `usd_cost` on model and channel calls, and the Temporal `workflow_id` / `run_id`. Required dashboards and their alert conditions:

| Signal | Alert |
|---|---|
| Outbox relay lag (`produced_at − occurred_at`) P99 | > 30 s for 5 min |
| Consumer lag per `(topic, group)` | > 10,000 messages or > 5 min |
| DLQ depth on `adj.approval.v1`, `adj.control.v1`, `adj.audit.v1` | > 0, pages |
| `ValidateToken` P95 | > 25 ms |
| `Token*` / `SpendCapExceeded` error rate | any sustained nonzero rate, pages |
| Approval-queue latency P50 and abandonment rate | P50 > 4 h, or abandonment > 10% |
| Governor queue depth per channel | drain estimate > 15 min |
| Deployment P95 (5 channels, 12 ads) | > 8 min excluding governor queueing |
| Margin per brand, daily rollup | any brand negative for 3 consecutive days |
| Kill-switch fan-out completion | any channel unverified at 5 min, pages |

The approval-queue latency pair is the one to watch in alpha. The PRD names the approval queue becoming a bottleneck as the top product risk, and these two numbers are its leading indicators — well before it shows up as churn.

---

## 9. Open Contract Questions

| # | Question | Blocking |
|---|---|---|
| C1 | Does `adj.metrics.v1` stay JSON, or move to Avro for the ~100M rows/day envelope? JSON costs roughly 3× the bytes and adds parse CPU on the hot ingest path. | Phase 2 |
| C2 | Should `approval.decided` be a Temporal update rather than a signal, so the UI gets a synchronous verdict instead of optimistic state plus a query? Better UX, tighter coupling to workflow availability. | Phase 1 |
| C3 | Do agency clients get a read-only outbound webhook feed of their own brands' events? Pull-through demand exists; the tenant-filtering surface is new attack area. | Phase 3 |
| C4 | Hot-partition mitigation on `brand_id` for very large agency brands: composite key on metrics only, or a generalized sub-key scheme? | Phase 2, sooner if a design partner is large |
| C5 | Is a single `adj.channel.v1` topic right, or does per-channel partitioning isolate one platform's outage from the others' consumer lag? | Phase 2 |
| C6 | Who owns the consumer registry and enforces registration — CI check against a committed manifest, or a runtime broker-side allowlist? | Phase 1 |

---

*Sources for the platform constraints underlying these contracts: [Meta Marketing API rate limiting](https://developers.facebook.com/docs/marketing-api/overview/rate-limiting/), [Google Ads Performance Max asset groups](https://developers.google.com/google-ads/api/performance-max/asset-groups), [LinkedIn marketing API access tiers](https://learn.microsoft.com/en-us/linkedin/marketing/integrations/marketing-tiers?view=li-lms-2026-08), [Microsoft Advertising API platform evolution](https://about.ads.microsoft.com/en/blog/post/april-2026/evolving-the-microsoft-advertising-api-platform), [Reddit Ads API v3](https://ads-api.reddit.com/docs/v3/), [Pinterest developer guidelines](https://policy.pinterest.com/en/developer-guidelines), [Snapchat Marketing API](https://developers.snap.com/marketing-api/Ads-API/introduction), [TikTok API for Business](https://business-api.tiktok.com/portal/docs?id=100025), [Amazon Ads Sponsored Products v3](https://advertising.amazon.com/API/docs/en-us/guides/sponsored-products/overview), and [EU AI Act Article 50 transparency obligations](https://commission.europa.eu/news-and-media/news/safer-and-more-transparent-ai-2026-08-02_en).*
