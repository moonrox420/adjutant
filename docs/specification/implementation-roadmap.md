# Adjutant — Implementation Roadmap & Engineering Tickets

**Companion to:** Adjutant PRD v1.0 · Technical Architecture v1.0 · Service Contracts v1.0
**Version:** 1.0 (Draft for engineering review)
**Date:** September 15, 2026
**Status:** Pre-build
**Machine-readable companion:** `adjutant-tickets.csv` — 199 tickets, importable into Jira or Linear

---

## 1. How to Read This

199 tickets across 29 epics and 4 phases, totaling 970 story points. Every ticket carries a squad, a size, explicit dependencies, the PRD requirement IDs it satisfies, and acceptance criteria written so that "done" is not a matter of opinion.

Sizing is Fibonacci points, not days: XS=1, S=2, M=3, L=5, XL=8. A point is roughly a half-day of one engineer's productive throughput including review and test, which nets out at **2.5 points per engineer per week** after meetings, incidents, and context switching. That number is the one to argue with first — everything downstream depends on it.

Eight tickets are marked **release gates**. A gate is not a milestone to celebrate; it is a condition that blocks the next phase from starting. `APRV-8`, the negative security suite for the spend path, is the strictest: a regression there blocks any release, at any time, in any phase.

The dependency graph is machine-verified. Every dependency resolves to a real ticket, there are no cycles, and no ticket depends on work scheduled in a later phase.

---

## 2. Team Shape

| Squad | Owns | Points | Recommended headcount |
|---|---|---|---|
| **Squad Spend** | approval-svc, channel-gateway, adapters, deployment | 323 | 6 |
| **Squad Creative** | agent-runtime, brand-svc, creative-svc, render-svc | 229 | 4 |
| **Squad Platform** | infra, Postgres, Temporal, analytics-svc, observability | 212 | 4 |
| **Squad Surface** | web console, agency platform, notifications, billing | 124 | 3 |
| **Compliance pod** | compliance-svc, policy corpus, disclosure regimes | 55 | 1 engineer + fractional counsel |
| **Platform Access** | App Review and platform access applications | 27 | PM-led, no engineering |

Squad Spend carries a third of the program. That is the correct concentration — it owns every code path that can move money — but it makes Spend the binding constraint on the whole schedule, and it means a single departure there is a program risk rather than a squad risk.

---

## 3. The Schedule Does Not Fit, and Here Is the Arithmetic

The PRD proposes 8 / 8 / 12 / 8 weeks for Phases 0 through 3. Loading the tickets against that calendar:

| Phase | Weeks | Tickets | Points | Points/week required | Capacity at 18 engineers | Utilization |
|---|---|---|---|---|---|---|
| **0 — Foundations** | 8 | 81 | 364 | **45.5** | 45 | **101%** |
| **1 — Closed alpha** | 8 | 54 | 250 | 31.2 | 45 | 69% |
| **2 — Parity + video** | 12 | 43 | 239 | 19.9 | 45 | 44% |
| **3 — GA** | 8 | 21 | 117 | 14.6 | 45 | 32% |
| **Total** | 36 | 199 | 970 | 26.9 | 45 | 60% |

At the program level 970 points over 36 weeks is comfortable. The problem is not total scope — it is distribution. And the aggregate number hides the real constraint, which only appears when you load each squad separately:

| Squad | P0 pts/wk | P1 pts/wk | P2 pts/wk | P3 pts/wk | Capacity |
|---|---|---|---|---|---|
| Squad Spend | **25.1** | 3.5 | 7.0 | 1.2 | 15.0 |
| Squad Platform | **14.9** | 3.9 | 1.6 | 5.4 | 10.0 |
| Squad Creative | **0.0** | **16.2** | 4.8 | 5.2 | 10.0 |
| Squad Surface | 2.9 | 6.8 | 2.2 | 2.6 | 7.5 |
| Compliance pod | 0.0 | 0.4 | 4.3 | 0.0 | 2.5 |

Three findings, in order of how much they should change the plan.

**Finding 1: Squad Spend needs 25 points a week in Phase 0 and can produce 15.** The approval state machine, the gateway framework, the governor, the audit ledger, and the first adapter to full definition-of-done are 201 points of deeply interdependent work. Throwing people at it has limited effect, because the chain `PLAT-10 → GW-3 → ADPT-1 → ADPT-2` is serial by construction. Phase 0 as specified requires roughly ten engineers on the spend surface alone.

**Finding 2: Squad Creative is completely idle in Phase 0, then 62% oversubscribed in Phase 1.** This is a sequencing artifact, not a real constraint, and it is free to fix. Roughly 56 points of Creative work depends only on Phase 0 platform tickets and can start in week 1: the agent runtime shell (`AGENT-1`, `AGENT-2`, `AGENT-3`), the scene graph and static render pipeline (`CRTV-1` through `CRTV-6`), and brand ingest (`BG-1`, `BG-2`, `BG-9`). Pulling those forward uses idle capacity and removes the Phase 1 overload entirely.

**Finding 3: Phase 2 and Phase 3 are under-loaded at 44% and 32%.** There is real slack in the back half, which is where App Review delays and the seven remaining adapters will consume it. Leaving it as slack is defensible. Pretending it is fully committed work is not.

### 3.1 Recommendation

1. **Extend Phase 0 from 8 weeks to 12.** This is the honest number. The PRD's 8 weeks is optimistic by roughly 50% on the spend surface, and no amount of staffing fixes a serial dependency chain.
2. **Staff Squad Spend at 6, not 4.** It owns 33% of the points and 100% of the paths that can cause unauthorized spend.
3. **Move Creative's foundational 56 points into Phase 0** so the squad is not idle for two months and Phase 1 is not oversubscribed.
4. **Move the Google/YouTube adapter (`ADPT-6` through `ADPT-10`, 28 points) into early Phase 1.** The PRD only requires Meta, Google, and YouTube live by the end of Phase 1, not by the end of Phase 0. Phase 0 needs one adapter proven to full definition-of-done to validate the framework; the second one validates nothing new about the framework and can slide.
5. **Plan GA for approximately week 40, not week 36.** Say this now rather than discovering it in month eight.

None of this changes the total scope. It changes whether the dates are real.

### 3.2 The constraint that is not on this chart

App Review is the long pole and it is not an engineering constraint. `ACCESS-1` through `ACCESS-5` are filed in week 1 precisely because their latency is measured in weeks-to-months and is entirely outside our control. Meta Advanced Access, LinkedIn Standard tier, TikTok's data-security review, and Pinterest standard access all gate live write access on channels whose adapters may be finished and idle.

The mitigation is sequencing, not effort: build against sandboxes and cassettes (`GW-12`), ship the channels that require no allowlisting first — Reddit's Ads API is open to all developers and Snapchat's is too — and treat every access grant as a discovered date rather than a planned one. `ACCESS-7` exists to make that visible weekly instead of quarterly.

---

## 4. Critical Path

The longest dependency chain in the program is 74 points across 15 tickets:

```
PLAT-10  service scaffold
  → GW-3    adapter protocol
    → ADPT-1  Meta connect
      → ADPT-2  Meta read
        → MON-2  metric sync + watermarks
          → MON-3  raw ingest + partitions
            → MON-4  normalization + comparability_class
              → MON-5  anomaly detection
                → OPT-1  Optimizer agent
                  → OPT-2  proposal model
                    → OPT-3  auto-approve rules
                      → OPT-4  execution-time guardrail re-check
                        → OPT-5  OptimizationWorkflow
                          → OPT-7  outcome attribution
                            → ANL-1  creative analytics
```

This chain says something worth noting: **the critical path runs through measurement, not through creation.** Getting an ad live is comparatively shallow work. Knowing whether it worked, in a way that can safely drive an automated decision, is the deep chain. Any slip in `MON-2` through `MON-4` propagates all the way to GA, and those three tickets are easy to underestimate because they look like plumbing.

Per-phase longest chains: Phase 0 is 44 points over 8 tickets, Phase 1 is 55 over 10, Phase 2 is 66 over 14, Phase 3 is 74 over 15. The chain lengthens steadily, which means late-phase slips have less room to absorb.

### 4.1 Sequencing rules

Six rules that the dependency graph enforces and that review should defend:

1. **Nothing writes to a channel before `APRV-8` passes.** The negative security suite gates the first live write, not the last.
2. **`GW-11` (conformance suite) precedes every adapter but the first.** Meta is built alongside the suite; every subsequent adapter is built against it. Otherwise the suite encodes Meta's shape and channel parity quietly becomes Meta parity.
3. **`CRTV-6` (rendition validator) precedes `UI-3` (approval queue).** A reviewer must never see a broken rendition, so validation must exist before the queue does.
4. **`BG-4` (graph confirmation) precedes any generation.** `BG-5` enforces it in code; the schedule enforces it in order.
5. **`MON-4` (comparability classes) precedes any cross-channel aggregate.** Blended metrics without methodology annotation is a top-five PRD risk, and the only reliable prevention is that the capability does not exist before the guardrail does.
6. **`AUD-1` (audit ledger) precedes `DEP-1` (deployment).** Every mutation is auditable from the first one, not retrofitted after a hundred of them.

---

## 5. Phase 0 — Foundations

**Weeks 1–8 as specified; 12 weeks recommended (§3.1)** · 81 tickets · 364 points · 45.5 points/week required

Build the parts that are expensive to change: tenancy, the approval state machine, the adapter framework, and the audit ledger. Exactly one channel reaches full definition-of-done, because the goal of Phase 0 is a proven framework, not coverage. Nothing writes to a live ad account until `APRV-8` is green.

Squad load: Squad Spend 201 · Squad Platform 119 · Squad Surface 23 · Platform Access (PM-led) 21

### Platform foundations — `PLAT`

17 tickets · 76 points

| ID | Title | Sq | Pts | Depends on | PRD | Acceptance criteria |
|---|---|---|---|---|---|---|
| `PLAT-1` | Terraform baseline: VPC, EKS, node groups, RDS, Redis, S3, KMS | Platform | 8 | — | — | terraform apply from zero produces a working empty environment in a fresh AWS account; api, agent, and render node groups scale independently. |
| `PLAT-2` | Redpanda cluster and topic provisioning as code | Platform | 5 | `PLAT-1` | — | All 14 topics plus matching .dlq topics exist with retention read from adjutant-events.json. Topic creation outside Terraform is denied. |
| `PLAT-3` | Schema registry plus CI backward-compatibility gate | Platform | 5 | `PLAT-2` | — | A PR that removes a payload field or narrows a type without bumping event_version fails CI. Adding an optional field passes. |
| `PLAT-4` | CI/CD with expand/contract migration gate | Platform | 5 | `PLAT-1` | — | A migration that drops or renames a column in the same release as its code change is rejected by CI. |
| `PLAT-5` | OpenTelemetry pipeline to Grafana/Tempo/Loki/Mimir | Platform | 5 | `PLAT-1` | — | A single request produces one connected trace across core-api, orchestrator, and one adapter. |
| `PLAT-6` | Span attribute contract enforcement | Platform | 3 | `PLAT-5` | — | Any span missing brand_id, or any model/channel call span missing usd_cost, fails the contract test. |
| `PLAT-7` | Apply schema and run the invariant suite in CI | Platform | 3 | `PLAT-1`, `PLAT-4` | — | adjutant-schema.sql loads clean and all 14 invariant tests fail closed on every pipeline run. A regression blocks merge. |
| `PLAT-8` | Temporal cluster, namespace per environment, worker versioning | Platform | 5 | `PLAT-1` | — | A worker deploy mid-workflow does not break in-flight executions; replay test passes against recorded histories. |
| `PLAT-9` | KMS CMK, per-tenant DEK provisioning, rotation runbook | Platform | 5 | `PLAT-1` | ACC-6 | A database dump without KMS access yields no usable channel credential. Rotation is executed once in staging end to end. |
| `PLAT-10` | Service scaffold template | Platform | 3 | — | — | A new service is generated with otel, tenant-context middleware, healthz, and a passing test suite in under 10 minutes. |
| `PLAT-11` | Internal gRPC transport with tenant and trace metadata propagation | Platform | 5 | `PLAT-10` | — | A call without tenant metadata is rejected with TenantContextMissing at the interceptor, before any handler runs. |
| `PLAT-12` | Local dev environment | Platform | 3 | — | — | One command brings up Postgres, Redis, Redpanda, and Temporal with seeded fixtures. |
| `PLAT-13` | Event envelope library (Python and TypeScript) | Platform | 5 | `PLAT-3` | — | Publish validates against the registry; consume dedupes on event_id and routes validation failures to the DLQ. Both languages share the conformance fixtures. |
| `PLAT-14` | event_outbox table and relay service | Platform | 5 | `PLAT-13` | — | No service publishes directly. Relay claims with FOR UPDATE SKIP LOCKED, and a kill -9 between publish and mark produces a duplicate that the consumer dedupes rather than a lost event. |
| `PLAT-15` | Consumer registry manifest and CI registration check | Platform | 3 | `PLAT-3` | — | A service that subscribes to a topic without a manifest entry fails CI. |
| `PLAT-16` | DLQ inspection and replay tooling | Platform | 3 | `PLAT-13` | — | An operator can view, filter, and selectively replay DLQ messages with the original envelope intact. |
| `PLAT-17` | Load-test harness for governor and deployment fan-out | Platform | 5 | `PLAT-2` | — | Harness simulates 500 brands deploying simultaneously and reports governor queue behavior and 429 count. |

### Tenancy and security — `SEC`

10 tickets · 43 points

| ID | Title | Sq | Pts | Depends on | PRD | Acceptance criteria |
|---|---|---|---|---|---|---|
| `SEC-1` | Tenant context injection on connection checkout | Platform | 5 | `PLAT-7` | ACC-6 | A pooled connection returned without tenant context can read zero rows from every brand-scoped table. |
| `SEC-2` | Cross-tenant leak suite on every migration | Platform | 3 | `SEC-1` | ACC-6 | Every migration runs the leak suite; a new table without RLS fails the build. |
| `SEC-3` | AuthN (OIDC) and the full role matrix | Surface | 8 | `PLAT-11` | ACC-3, ACC-4 | All seven roles enforced at the API boundary with a table-driven test per role per endpoint. |
| `SEC-4` | Agency-to-client role scoping | Surface | 5 | `SEC-3` | ACC-2, ACC-5 | A client_approver can approve only their own brand's subjects and can read nothing else in the agency. |
| `SEC-5` | S3 prefix isolation with IAM conditions | Platform | 3 | `PLAT-1` | ACC-6 | A credential scoped to one brand prefix cannot list or read another brand's objects. |
| `SEC-6` | Channel credential encryption with no-log assertions | Spend | 5 | `PLAT-9` | ACC-6 | A test that greps logs, spans, and agent context for a known token value finds zero occurrences. |
| `SEC-7` | agent-runtime egress lockdown | Platform | 3 | `PLAT-1` | — | A NetworkPolicy test confirms agent-runtime pods cannot reach any ad platform host. Capability removed, not denied. |
| `SEC-8` | Account type mutability without data loss | Surface | 5 | `SEC-3` | ACC-1 | A business account converts to agency and back with zero row loss and no orphaned brands. |
| `SEC-9` | Signed immutable audit export | Platform | 3 | `AUD-2` | ACC-7 | Export is byte-reproducible for a fixed window and carries a verifiable signature. |
| `SEC-10` | Threat model and pen-test scope for the spend path | Spend | 3 | — | — | Written threat model reviewed and signed off; external pen-test scope covers every path that can reach Build or Mutate. |

### Approval and spend authority — `APRV`

11 tickets · 50 points

| ID | Title | Sq | Pts | Depends on | PRD | Acceptance criteria |
|---|---|---|---|---|---|---|
| `APRV-1` | Approval state machine | Spend | 8 | `PLAT-7` | AP-1, AP-2, AP-3 | All transitions and side exits implemented; an illegal transition raises rather than being silently ignored. pending_client without a prior internal approval is impossible. |
| `APRV-2` | Ed25519 signing via KMS with overlapping-validity rotation | Spend | 5 | `PLAT-9` | AP-5 | Key rotation completes with zero token-validation failures for tokens signed by the outgoing key. |
| `APRV-3` | IssueToken: hash binding, scope derivation, cap binding, bounded expiry | Spend | 5 | `APRV-1`, `APRV-2` | AP-4, AP-5 | Token is issuable only from an approved request; expiry beyond 72h is rejected by the database, not just the service. |
| `APRV-4` | ValidateToken: Redis-backed, fail-closed | Spend | 5 | `APRV-3` | AP-5 | P95 under 15 ms at 1,000 rps. With Redis unavailable, validation denies rather than permits, and the denial is alerted. |
| `APRV-5` | ConsumeToken single-consumption with write-through | Spend | 5 | `APRV-4` | AP-5, LN-2 | A replay of the same (token, channel, operation, idem_key) is rejected and raises a security alert, not a warning. |
| `APRV-6` | Token voiding on subject mutation | Spend | 3 | `APRV-3` | AP-4 | Editing an approved plan voids every outstanding token in the same transaction and emits plan.voided. Verified by the schema invariant suite. |
| `APRV-7` | Approver spend-threshold enforcement | Spend | 3 | `SEC-3`, `APRV-1` | ACC-4 | A reviewer whose threshold is below the requested daily spend cannot be an eligible approver. |
| `APRV-8` **GATE** | Negative security suite for the spend path | Spend | 5 | `APRV-5` | AP-5 | Hash mutation, scope escalation, cap overflow, replay, expiry, and cross-brand token use all fail closed. Any regression blocks release. |
| `APRV-9` | Expiry timers, reminders, abandonment metric | Spend | 3 | `APRV-1` | AP-8 | Requests expire on an absolute deadline; reminder cadence configurable; abandonment rate emitted as a metric from day one. |
| `APRV-10` | Two-stage internal-then-client approval with agency delegation | Spend | 5 | `APRV-1`, `SEC-4` | AP-2, ACC-5 | An agency can require client sign-off per brand; the client stage is unreachable before internal approval. |
| `APRV-11` | Chaos tests: Redis loss, KMS unavailable, clock skew | Spend | 3 | `APRV-4` | — | Each fault produces a denial and an alert. No fault produces a permitted write. |

### Audit ledger — `AUD`

4 tickets · 18 points

| ID | Title | Sq | Pts | Depends on | PRD | Acceptance criteria |
|---|---|---|---|---|---|---|
| `AUD-1` | Append-only ledger writer with revert_path capture | Platform | 5 | `PLAT-7` | AP-6, LN-5 | Every spend-affecting action writes one row with actor, diff, token id, and a populated revert_path. UPDATE and DELETE are rejected by the database. |
| `AUD-2` | action.recorded emission from the same transaction | Platform | 3 | `AUD-1`, `PLAT-14` | AP-6 | The bus can never disagree with the ledger; a crash after commit still publishes via the outbox. |
| `AUD-3` | Reversibility engine | Spend | 5 | `AUD-1` | LN-5 | Rollback walks revert_path in reverse within the window and verifies each step against the platform. |
| `AUD-4` | Audit timeline UI | Surface | 5 | `AUD-1`, `SEC-3` | AP-6, ACC-7 | Per-brand chronological view showing actor, action, diff, token, and USD impact, filterable and exportable. |

### Channel gateway framework — `GW`

16 tickets · 74 points

| ID | Title | Sq | Pts | Depends on | PRD | Acceptance criteria |
|---|---|---|---|---|---|---|
| `GW-1` | Capability Registry loader and versioning | Spend | 5 | `PLAT-7` | CP-1 | Registry YAML validates against a schema at load; an entry missing a required field fails startup. All 10 channels seeded. |
| `GW-2` | Spec Registry loader | Spend | 5 | `GW-1` | CR-2 | Placement specs, safe zones, character limits, and codec constraints are readable by render-svc and the validator. Nothing hard-codes a dimension. |
| `GW-3` | ChannelAdapter protocol and adapter registry | Spend | 3 | `PLAT-10` | CP-1 | A static check fails the build on any channel-name conditional above the adapter layer. |
| `GW-4` | Rate-limit governor: Redis and Lua token buckets | Spend | 8 | `PLAT-1` | LN-6 | Buckets refill per each channel's declared quota_model. Bucket arithmetic is atomic in a single round trip. |
| `GW-5` | Governor priority queueing with visible queue position | Spend | 5 | `GW-4` | LN-6 | Deployment outranks optimization outranks backfill. The user sees 'queued behind 3 deployments', not a spinner. |
| `GW-6` | write_account_allowlist constraint kind | Spend | 3 | `GW-4` | CP-1 | LinkedIn's development-tier five-account write limit is modeled as a constraint kind rather than as a rate. |
| `GW-7` | Idempotency key derivation and read-by-key reconciliation | Spend | 5 | `GW-3` | LN-2 | A create that succeeded behind a 504 is adopted on retry, not duplicated. Verified by an induced-timeout test per adapter. |
| `GW-8` | Token assertion at the adapter boundary | Spend | 3 | `APRV-4`, `GW-3` | AP-5 | Every adapter asserts validity locally before its first outbound HTTP call. A test that removes the caller's check still cannot produce a write. |
| `GW-9` | Per-channel change-notification audit | Spend | 3 | `GW-1` | — | Each channel's registry entry carries mechanism (webhook or polling), poll interval, and the date verified. No code reads a channel name to decide. |
| `GW-10` | Webhook ingress | Spend | 5 | `GW-9` | — | Signature verified before parse; deduped on delivery id; 200 returned under 500 ms; payload treated as a reconcile hint, never as state. |
| `GW-11` **GATE** | Adapter conformance suite (the executable Channel DoD) | Spend | 8 | `GW-3`, `GW-7` | CP-1, CP-2 | One shared suite every adapter must pass: capability accuracy, idempotency under induced timeout, verification catching a silent failure, verbatim rejection capture, quota respect without probing. |
| `GW-12` | Cassette record and replay harness | Spend | 5 | `GW-11` | — | No CI path touches a production ad account. Cassettes are refreshable on demand. |
| `GW-13` | Kill switch with verification and paging | Spend | 5 | `GW-4` | LN-7 | Row checked on every egress call; fan-out complete within 60 s; any channel unverified at 5 minutes pages rather than toasts. |
| `GW-14` | Structure sync and external-drift detection | Spend | 5 | `GW-3` | OP-8 | Changes made outside Adjutant are detected and surfaced as drift with changed_externally set. |
| `GW-15` | Verbatim rejection capture and classification hook | Spend | 3 | `GW-3` | LN-4 | Platform text stored unmodified; classification is a separate derived field that never overwrites the original. |
| `GW-16` | Governor observability dashboard | Spend | 3 | `GW-5` | — | Queue depth, drain estimate, and observed 429 count per channel per ad account. |

### Meta and Google to full DoD — `ADPT`

11 tickets · 61 points

| ID | Title | Sq | Pts | Depends on | PRD | Acceptance criteria |
|---|---|---|---|---|---|---|
| `ADPT-1` | Meta: connect and refresh | Spend | 5 | `GW-3` | CP-2 | OAuth and system-user paths both work; token refresh is automatic and a revocation surfaces as connection health within one sync. |
| `ADPT-2` | Meta: read_structure and read_metrics | Spend | 5 | `ADPT-1` | CP-2 | Full hierarchy and hourly metrics land correctly, including restatements of prior windows. |
| `ADPT-3` | Meta: build and asset upload, idempotent | Spend | 8 | `ADPT-2`, `GW-7` | LN-1, LN-2 | Campaign, ad set, and ad created with idem keys; induced timeout on each level reconciles rather than duplicating. |
| `ADPT-4` | Meta: quota model from documented formula | Spend | 5 | `GW-4`, `ADPT-1` | LN-6 | Bucket capacity computed as base + 40 x active_ads with tier read from the X-Ad-Account-Usage header; recomputed on every structure sync. Limits are never probed empirically. |
| `ADPT-5` | Meta: verify, rejections, and Advantage+ targeting constraints | Spend | 5 | `ADPT-3` | CP-2, LN-4 | Read-back verification; rejections captured verbatim; the Strategist cannot plan interest targeting where Advantage+ removes it. |
| `ADPT-6` | Google Ads: connect, refresh, account hierarchy | Spend | 5 | `GW-3` | CP-2 | Manager-account hierarchies resolve correctly; a brand can be linked to a single child account unambiguously. |
| `ADPT-7` | Google Ads: read_structure and read_metrics | Spend | 5 | `ADPT-6` | CP-2 | Structure and hourly metrics land, including asset-group-level reporting. |
| `ADPT-8` | Google Ads: Performance Max asset-group build | Spend | 8 | `ADPT-7`, `GW-7` | LN-1, LN-2 | Asset groups respect the 1-to-100-per-campaign bound, are never treated as shareable across campaigns, and always carry at least one final URL. |
| `ADPT-9` | YouTube placements and video asset handling | Spend | 5 | `ADPT-8` | CP-2 | Video assets upload and attach correctly; YouTube-specific placements are declared in the capability registry rather than special-cased. |
| `ADPT-10` | Google: verify, rejections, AI-content label policy | Spend | 5 | `ADPT-8` | CR-11, LN-4 | Read-back verification; verbatim rejection capture; AI-generated content is labeled per current Google Ads policy on submission. |
| `ADPT-11` **GATE** | Meta and Google pass the full conformance suite | Spend | 5 | `GW-11`, `ADPT-5`, `ADPT-10` | CP-1 | Both adapters green on every conformance test with zero waivers. No channel ships with a waiver. |

### Temporal wiring — `ORCH`

5 tickets · 18 points

| ID | Title | Sq | Pts | Depends on | PRD | Acceptance criteria |
|---|---|---|---|---|---|---|
| `ORCH-1` | Workers, task queues per pool, versioning strategy | Platform | 5 | `PLAT-8` | — | Agent and render pools have separate queues and autoscaling; a mid-flight deploy does not break executions. |
| `ORCH-2` | Retry policy library and non-retryable error registry | Platform | 3 | `ORCH-1` | — | All 15 terminal error types are registered non-retryable. A test asserts no Token error is ever retried. |
| `ORCH-3` | Workflow ID conventions with REJECT_DUPLICATE | Platform | 2 | `ORCH-1` | LN-2 | Starting deploy/{deployment_id} twice is a no-op rather than a double launch. |
| `ORCH-4` | Signal idempotency and stale-hash guard library | Platform | 3 | `ORCH-1` | AP-4 | A duplicate signal is ignored; a signal whose subject_hash no longer matches is recorded and never acted on. |
| `ORCH-5` | Replay-safety CI test against recorded histories | Platform | 5 | `ORCH-1` | — | Any non-deterministic change to workflow code fails CI before it can break in-flight executions. |

### Platform access program — `ACCESS`

7 tickets · 24 points

| ID | Title | Sq | Pts | Depends on | PRD | Acceptance criteria |
|---|---|---|---|---|---|---|
| `ACCESS-1` | File Meta Advanced Access / App Review | Access | 3 | — | CP-2 | Submitted in week 1 with demo assets and privacy documentation. This is the long pole; nothing waits on engineering to start it. |
| `ACCESS-2` | File LinkedIn Standard tier application | Access | 3 | — | CP-2 | Submitted in week 1. Until granted, development tier permits writes on only five ad accounts and one creatable test account, which constrains alpha design-partner selection. |
| `ACCESS-3` | TikTok developer app, terms, verification, data-security review | Access | 5 | — | CP-2 | All four steps of the integration path initiated in week 1; data-security review documentation drafted. |
| `ACCESS-4` | Pinterest standard access application | Access | 3 | — | CP-2 | Submitted in week 1. Developer guidelines reviewed and the rate-limit-probing prohibition recorded as a build constraint. |
| `ACCESS-5` | Snapchat, Reddit, Microsoft, Amazon developer onboarding | Access | 5 | — | CP-2 | Accounts created and access confirmed. Reddit and Snapchat require no allowlisting, so these are the lowest-risk parity wins. |
| `ACCESS-6` | Microsoft REST-only posture | Spend | 3 | `ACCESS-5` | CP-2 | The adapter targets REST exclusively from the first line of code, given SOAP's announced deprecation timeline. |
| `ACCESS-7` | Access-status tracker and weekly review | Access | 2 | `ACCESS-1` | — | Per-channel status, blocker, owner, and contingency reviewed weekly and visible to the whole team. |


## 6. Phase 1 — Closed Alpha

**Weeks 9–16 as specified** · 54 tickets · 250 points · 31.2 points/week required

Three channels live with 10 design-partner brands. The full loop closes for the first time: brand ingest, generation, human approval, deployment, measurement, and a first optimization proposal. This is where the approval-accept rate becomes a real number instead of a target.

Squad load: Squad Creative 130 · Squad Surface 54 · Squad Platform 31 · Squad Spend 28 · Platform Access (PM-led) 4 · Compliance pod 3

### Brand Graph — `BG`

10 tickets · 43 points

| ID | Title | Sq | Pts | Depends on | PRD | Acceptance criteria |
|---|---|---|---|---|---|---|
| `BG-1` | Site ingest and structured extraction | Creative | 5 | `PLAT-10` | BG-1 | Given a domain, the crawler extracts offerings, pricing signals, geography, and tone candidates with a source URL per field. |
| `BG-2` | Assertion writer with provenance enforcement | Creative | 5 | `BG-1`, `PLAT-7` | BG-2, BG-5 | An assertion cannot be written in confirmed state without a provenance URI. Enforced by the database, verified by the invariant suite. |
| `BG-3` | Confidence scoring and low-confidence surfacing | Creative | 3 | `BG-2` | BG-3 | Fields below threshold are ordered for human review; the user is not asked to re-confirm high-confidence extractions. |
| `BG-4` | Brand Graph confirmation UI | Surface | 5 | `BG-3`, `SEC-3` | BG-4 | A user confirms or corrects a draft graph in under 10 minutes for a typical SMB; corrections write with human provenance. |
| `BG-5` | Generation gate on unconfirmed graph | Creative | 2 | `BG-4` | BG-4 | CommitSceneGraph raises BrandGraphUnconfirmed when the graph is not confirmed. No bypass exists, including in admin tooling. |
| `BG-6` | Palette, typeface, and logo extraction into the brand kit | Creative | 5 | `BG-1` | BG-6 | Extracted palette and typefaces are usable as style_refs; logo is stored as a protected asset that generation may never recreate. |
| `BG-7` | Ad account history ingest for warm start | Creative | 5 | `ADPT-2`, `ADPT-7` | BG-7 | Prior campaign performance informs the first plan rather than starting cold. |
| `BG-8` | Brand Graph clone with field-level diff | Creative | 5 | `BG-2` | BG-8, ACC-2 | An agency clones a graph to a similar client and sees a field-level diff before committing. |
| `BG-9` | Claim-to-assertion substantiation index | Compliance | 3 | `BG-2` | BG-5, CR-9 | SubstantiateClaims resolves a claim string to a provenanced assertion or returns unresolved. It is a graph lookup, not a model judgment. |
| `BG-10` | OnboardBrandWorkflow end to end | Creative | 5 | `BG-4`, `BG-6`, `ORCH-4` | BG-1 | Signup to confirmed graph runs as one durable workflow that survives a deploy mid-onboarding and waits up to 14 days for confirmation. |

### Agent runtime and first three agents — `AGENT`

10 tickets · 50 points

| ID | Title | Sq | Pts | Depends on | PRD | Acceptance criteria |
|---|---|---|---|---|---|---|
| `AGENT-1` | Agent bundle definition format | Creative | 3 | `PLAT-10` | — | Prompt, tool allowlist, model tier, output schema, USD ceiling, and retry policy are declared as data and loaded at startup. |
| `AGENT-2` | Invoke with schema validation and bounded retries | Creative | 5 | `AGENT-1` | — | Invalid structured output retries up to three times with the error fed back, then raises SchemaValidationExhausted and escalates to a human task. It never degrades to unstructured text. |
| `AGENT-3` | Tool allowlist enforcement at dispatch | Creative | 3 | `AGENT-1` | — | The Copywriter has no channel-gateway verb; the Optimizer's list contains no write verb. A forged tool call is refused at dispatch. |
| `AGENT-4` | Per-invocation cost metering | Creative | 3 | `AGENT-2`, `PLAT-14` | — | Every invocation writes agent_run and emits agent.run.completed with model, tokens, latency, USD, and brand attribution. |
| `AGENT-5` | Model tier policy and per-brand generation ceiling | Creative | 5 | `AGENT-4` | — | Exceeding the monthly ceiling degrades to a cheaper tier and notifies. It never silently burns margin, and never halts without telling anyone. |
| `AGENT-6` | Strategist agent | Creative | 8 | `GW-1`, `AGENT-2` | ST-1, ST-2, ST-3, ST-6 | Produces a channel-allocated plan validated against the Capability Registry, with written rationale, for a brand it has never seen. |
| `AGENT-7` | Copywriter agent | Creative | 5 | `GW-2`, `AGENT-2` | CR-3, CR-4 | Copy respects per-field character limits read from the Spec Registry and brand tone from the graph. No truncation occurs downstream. |
| `AGENT-8` | Art Director agent | Creative | 8 | `AGENT-2`, `CRTV-1` | CR-1, CR-5 | Emits a valid scene graph with distinct visual concepts, never a flat image, and never rasterizes a text layer. |
| `AGENT-9` | Prompt-injection hardening on ingested content | Creative | 5 | `BG-1`, `SEC-7` | — | Adversarial instructions embedded in a crawled page cannot alter agent behavior. A red-team corpus is part of CI. |
| `AGENT-10` | Agent evaluation harness | Creative | 5 | `AGENT-6`, `AGENT-7`, `AGENT-8` | — | Golden briefs across four verticals scored on regression; a prompt or model change that degrades quality fails the build. |

### Scene graph and static render — `CRTV`

9 tickets · 45 points

| ID | Title | Sq | Pts | Depends on | PRD | Acceptance criteria |
|---|---|---|---|---|---|---|
| `CRTV-1` | Scene graph schema and validation | Creative | 5 | `PLAT-7` | CR-1, CR-5 | A graph with a rasterized text layer is rejected at commit with TextLayerRasterized. |
| `CRTV-2` | Scene graph versioning and creative_hash | Creative | 3 | `CRTV-1` | AP-4 | Any mutation produces a new version and a new hash, which voids outstanding approval tokens. |
| `CRTV-3` | Anchor-preserving re-layout engine | Creative | 8 | `CRTV-1`, `GW-2` | CR-2, CR-6 | Anchors resolve against each target ratio's safe zone and text shrinks within its declared range. A layer that cannot fit raises RenditionInfeasible rather than cropping. |
| `CRTV-4` | render-svc static pipeline with font management | Creative | 8 | `CRTV-1` | CR-2 | Deterministic typographic rendering: identical graph and spec always yield identical bytes. Missing font raises rather than substituting silently. |
| `CRTV-5` | Content-addressed render cache | Creative | 3 | `CRTV-4` | — | Re-render after an unrelated edit is a cache hit. Nine renditions are nine renders of one graph, never nine generations. |
| `CRTV-6` | Rendition validator | Creative | 5 | `CRTV-4`, `GW-2` | CR-2 | Programmatic assertions for clipped text, mid-word breaks, contrast below 4.5:1, logo outside safe zone, byte and duration bounds. |
| `CRTV-7` | Infeasible-rendition routing | Creative | 3 | `CRTV-6` | CR-2 | A spec-validation failure routes back to creative revision and cannot enter the approval queue. Reviewers never see a broken rendition. |
| `CRTV-8` | Golden-image diff suite across all nine ratios | Creative | 5 | `CRTV-6` | CR-2 | Every ratio covered; a rendering regression fails CI with a visual diff artifact attached. |
| `CRTV-9` | CreativeSetWorkflow | Creative | 5 | `AGENT-7`, `AGENT-8`, `CRTV-6`, `ORCH-4` | CR-1 | Copy, art, and video agents run in parallel, then render, validate, compliance-check, and wait for approval as one durable execution. |

### Console and approval queue — `UI`

9 tickets · 41 points

| ID | Title | Sq | Pts | Depends on | PRD | Acceptance criteria |
|---|---|---|---|---|---|---|
| `UI-1` | App shell, auth, brand switcher, dual-mode navigation | Surface | 5 | `SEC-3` | ACC-1 | Business and agency modes share one shell; neither is visually or navigationally subordinate. |
| `UI-2` | Signup with dual account-type choice | Surface | 3 | `SEC-8` | ACC-1 | Both account types are presented with equal prominence and equal copy weight. Neither is a default or an upsell. |
| `UI-3` | Approval queue list | Surface | 8 | `APRV-1`, `UI-1` | AP-1, AP-7 | P95 under 250 ms at 500 pending items. Keyboard-first, batch selection, and sorted by a defensible priority rather than by creation time. |
| `UI-4` | Approval detail view | Surface | 8 | `UI-3`, `CRTV-6` | AP-1, AP-3, ST-6 | Side-by-side renditions, plan diff against the prior version, compliance findings with citations, and the agent's written rationale on one screen. |
| `UI-5` | Bulk approve with per-item exclusion | Surface | 5 | `UI-4` | AP-7 | A reviewer approves 40 creatives and excludes 3 in a single action, and the issued token covers exactly the approved set. |
| `UI-6` | Change-request flow with reason codes | Surface | 3 | `UI-4` | AP-3 | Structured reason codes plus free text; both flow into plan.rejected as Strategist training signal. |
| `UI-7` | Governor queue position and deployment progress | Surface | 3 | `GW-5`, `DEP-1` | LN-6 | Per-channel progress with real queue position. No indefinite spinner exists anywhere in the deploy path. |
| `UI-8` | Kill switch UI | Surface | 3 | `GW-13` | LN-7 | One action, explicit confirmation, then live per-channel verification status until every channel is confirmed paused. |
| `UI-9` | Console accessibility and contrast audit | Surface | 3 | `UI-4` | — | WCAG AA on the approval path. We cannot enforce 4.5:1 in ads and ship a console that fails it. |

### Deployment — `DEP`

6 tickets · 31 points

| ID | Title | Sq | Pts | Depends on | PRD | Acceptance criteria |
|---|---|---|---|---|---|---|
| `DEP-1` | DeploymentWorkflow with independent per-channel activities | Spend | 8 | `ORCH-2`, `GW-8`, `ADPT-11` | LN-1, LN-2 | Channels are independent units of success. A TikTok 5xx does not roll back live Meta campaigns. |
| `DEP-2` | Preflight orchestration | Spend | 5 | `DEP-1` | LN-3 | Pixel, conversion action, UTM, landing-page reachability, and billing validated per channel before any spend. A failing channel is blocked while others proceed. |
| `DEP-3` | Verification read-back with long poll | Spend | 5 | `DEP-1` | LN-1 | Liveness is never inferred from a write response. Unverifiable objects mark the channel partial, never live. |
| `DEP-4` | Retry reconciliation | Spend | 5 | `GW-7`, `DEP-3` | LN-2 | A retry reusing the same idem key adopts a pre-existing object. Induced-timeout test per level per channel produces zero duplicates. |
| `DEP-5` | Rollback within window | Spend | 5 | `AUD-3`, `DEP-3` | LN-5 | A launch is fully reversible within 10 minutes across every channel it touched, with each reversal verified. |
| `DEP-6` | Deployment audit rows | Platform | 3 | `AUD-1`, `DEP-1` | AP-6 | One ledger row per mutation carrying the authorizing token id, the diff, and a revert path. |

### Basic monitoring — `MON`

7 tickets · 33 points

| ID | Title | Sq | Pts | Depends on | PRD | Acceptance criteria |
|---|---|---|---|---|---|---|
| `MON-1` | MonitorBrandWorkflow on an hourly schedule | Platform | 5 | `ORCH-1`, `DEP-1` | OP-1 | Runs hourly per brand, self-heals a missed cycle, and re-derives rather than depending on event delivery. |
| `MON-2` | Metric sync with watermark events | Platform | 5 | `ADPT-2`, `ADPT-7` | OP-1 | Downstream scans trigger off watermarks, never off wall-clock time. Restatements invalidate derived rollups for the window. |
| `MON-3` | Raw metric ingest and partition management | Platform | 5 | `PLAT-7`, `MON-2` | OP-1 | Monthly partitions created ahead of need; raw channel metrics land immutable. |
| `MON-4` | Normalization layer with comparability_class | Platform | 5 | `MON-3` | RP-4 | Every normalized metric carries direct, caveated, or first_party_only. No aggregate crosses classes without an annotation. |
| `MON-5` | Anomaly detection with significance evidence | Platform | 5 | `MON-4` | OP-2 | Every anomaly carries confidence and sample size so downstream consumers can refuse weak signals. |
| `MON-6` | Notification fan-out | Surface | 3 | `MON-5`, `PLAT-13` | AP-8, OP-9 | Email and in-app delivery with per-user cadence controls and suppression. |
| `MON-7` | Performance dashboard | Surface | 5 | `MON-4` | RP-1, RP-4 | Per-brand, per-channel performance with methodology annotations rendered inline, not hidden in a tooltip. |

### Alpha exit — `GATE1`

3 tickets · 7 points

| ID | Title | Sq | Pts | Depends on | PRD | Acceptance criteria |
|---|---|---|---|---|---|---|
| `GATE1-1` | Alpha exit-gate instrumentation | Platform | 3 | `MON-4`, `APRV-9` | — | Approval-accept rate, unauthorized-spend counter, and platform disapproval rate are live dashboards before the first design partner onboards. |
| `GATE1-2` | Design partner onboarding runbook | Access | 3 | `BG-10` | — | Repeatable path for 6 SMBs and 4 agencies across 4 verticals, sized against LinkedIn's five-account development-tier write limit if Standard tier has not landed. |
| `GATE1-3` **GATE** | Alpha exit review | Access | 1 | `GATE1-1`, `GATE1-2` | — | Gate: at least 60% approval-accept rate, zero unauthorized-spend events, no more than 5% disapproval rate. A miss delays Phase 2 rather than being waived. |


## 7. Phase 2 — Channel Parity, Video, Compliance

**Weeks 17–28 as specified** · 43 tickets · 239 points · 19.9 points/week required

The remaining six channels reach conformance with zero waivers, video generation ships, the optimizer moves from proposing to acting inside pre-authorized bounds, and the compliance surface is externally audited. The agency platform lands here, which is the first phase where Squad Surface carries real weight.

Squad load: Squad Spend 84 · Squad Creative 57 · Compliance pod 52 · Squad Surface 26 · Squad Platform 19 · Platform Access (PM-led) 1

### Remaining seven channels — `ADPT2`

8 tickets · 59 points

| ID | Title | Sq | Pts | Depends on | PRD | Acceptance criteria |
|---|---|---|---|---|---|---|
| `ADPT2-1` | TikTok adapter to full DoD | Spend | 8 | `GW-11`, `ACCESS-5` | CP-1, CP-2 | Passes the full conformance suite with zero waivers. Dynamic quota endpoint queried rather than assumed; Spark Ads and batch creative management declared in the registry. |
| `ADPT2-2` | LinkedIn adapter to full DoD | Spend | 8 | `GW-11`, `ACCESS-5` | CP-1, CP-2 | Passes the full conformance suite with zero waivers. Access tier recorded on the connection; development tier's five-account write limit enforced as a constraint, not discovered at runtime. |
| `ADPT2-3` | Microsoft adapter to full DoD | Spend | 8 | `GW-11`, `ACCESS-5` | CP-1, CP-2 | Passes the full conformance suite with zero waivers. REST only. No SOAP code path exists, given the announced deprecation. |
| `ADPT2-4` | Reddit adapter to full DoD | Spend | 8 | `GW-11`, `ACCESS-5` | CP-1, CP-2 | Passes the full conformance suite with zero waivers. conversion_pixel_id required on creation; current objective enum set loaded from the registry. |
| `ADPT2-5` | Pinterest adapter to full DoD | Spend | 8 | `GW-11`, `ACCESS-5` | CP-1, CP-2 | Passes the full conformance suite with zero waivers. Rate limits declared from documentation and adjusted only on observed 429 headers. Probing is prohibited and the test suite asserts we never do it. |
| `ADPT2-6` | Snapchat adapter to full DoD | Spend | 8 | `GW-11`, `ACCESS-5` | CP-1, CP-2 | Passes the full conformance suite with zero waivers. Ad creation via the documented ad-squad path; no allowlisting needed, so this is an early parity win. |
| `ADPT2-7` | Amazon adapter to full DoD | Spend | 8 | `GW-11`, `ACCESS-5` | CP-1, CP-2 | Passes the full conformance suite with zero waivers. Sponsored Products v3 only; v2 is deprecated and no v2 path is written. |
| `ADPT2-8` **GATE** | Channel parity report | Spend | 3 | `ADPT2-1`, `ADPT2-2`, `ADPT2-3`, `ADPT2-4`, `ADPT2-5`, `ADPT2-6`, `ADPT2-7` | CP-1 | Published matrix of conformance results across all nine channels. Any waiver is a release blocker, which is how equal priority stays true. |

### Video pipeline — `VID`

6 tickets · 37 points

| ID | Title | Sq | Pts | Depends on | PRD | Acceptance criteria |
|---|---|---|---|---|---|---|
| `VID-1` | Video scene graph extension | Creative | 8 | `CRTV-1` | CR-7, CR-8 | Timeline, audio bed, and caption layers extend the same graph model. Video is not a separate creative system. |
| `VID-2` | FFmpeg assembly worker and GPU node-group decision | Creative | 8 | `VID-1`, `PLAT-1` | CR-7 | Resolves open technical question T2/T3 with a measured cost-per-render comparison, not an opinion. |
| `VID-3` | Video agent | Creative | 8 | `VID-1`, `AGENT-2` | CR-7, CR-8 | Script to shotlist to assembly, producing platform-appropriate cuts at every required duration. |
| `VID-4` | Captions and burn-in with disclosure support | Creative | 5 | `VID-2`, `CMPL-6` | CR-8, CR-11 | Captions render typographically; a required AI or paid-partnership disclosure can be placed without violating safe zones. |
| `VID-5` | Duration, codec, and bitrate validation | Creative | 3 | `VID-2`, `GW-2` | CR-2 | Per-placement bounds read from the Spec Registry; a violation fails validation rather than being submitted and rejected. |
| `VID-6` | Video perceptual regression suite | Creative | 5 | `VID-2` | — | Frame-sampled perceptual diffs catch assembly regressions across every target duration. |

### Optimizer — `OPT`

7 tickets · 35 points

| ID | Title | Sq | Pts | Depends on | PRD | Acceptance criteria |
|---|---|---|---|---|---|---|
| `OPT-1` | Optimizer agent, propose-only | Creative | 5 | `AGENT-3`, `MON-5` | OP-3, OP-6 | Its tool allowlist contains no write verb. A test asserts the Optimizer cannot reach Build or Mutate even with a forged call. |
| `OPT-2` | Proposal model with evidence and revert path | Spend | 5 | `OPT-1`, `AUD-1` | OP-6, LN-5 | Every proposal carries the anomaly or fatigue event ids that justify it and a populated revert path before it can be queued. |
| `OPT-3` | Auto-approve rule engine | Spend | 5 | `APRV-3`, `OPT-2` | OP-4, AP-9 | Rules are scoped and USD-capped. auto_approved is recorded distinctly from human_approved, so autonomy graduation can be analyzed later. |
| `OPT-4` | Execution-time guardrail re-check | Spend | 5 | `OPT-3`, `GW-8` | OP-5 | Ceilings are re-read immediately before the HTTP call. A proposal approved Monday that violates a Wednesday ceiling returns GuardrailViolation, which is an expected outcome and not an error. |
| `OPT-5` | OptimizationWorkflow | Spend | 5 | `OPT-4`, `ORCH-4` | OP-5, OP-7 | Proposal to guardrail re-check to mutation to verification to reversible ledger row, as one durable execution. |
| `OPT-6` | Winner scaling | Creative | 5 | `OPT-5` | OP-7 | A proven creative or audience scales with explicit spend bounds and a stated expected effect. |
| `OPT-7` | Outcome attribution and learning signal write | Platform | 5 | `OPT-5`, `MON-4` | OP-7 | Each executed optimization gets a measured outcome written back as a learning signal, de-identified before any global-scope write. |

### Fatigue and refresh — `FAT`

4 tickets · 16 points

| ID | Title | Sq | Pts | Depends on | PRD | Acceptance criteria |
|---|---|---|---|---|---|---|
| `FAT-1` | Compound fatigue scorer | Platform | 5 | `MON-4` | OP-2 | Requires at least three simultaneous signals. Enforced in the database constraint, the event schema, and the scorer. No single-signal or time-elapsed trigger exists anywhere. |
| `FAT-2` | Format-calibrated thresholds | Platform | 3 | `FAT-1`, `GW-2` | OP-2 | Short-form video thresholds trip materially earlier than static feed, with values sourced from the Spec Registry rather than hard-coded. |
| `FAT-3` | Refresh pipeline | Creative | 5 | `FAT-1`, `CRTV-9` | OP-2, CR-12 | Detected fatigue produces a new creative set that enters the normal approval path. Refresh is never auto-launched. |
| `FAT-4` | Fatigue backtest against alpha data | Platform | 3 | `FAT-2` | OP-2 | Thresholds validated against real alpha performance history before they gate any production refresh. |

### Compliance to full P0 — `CMPL`

10 tickets · 52 points

| ID | Title | Sq | Pts | Depends on | PRD | Acceptance criteria |
|---|---|---|---|---|---|---|
| `CMPL-1` | Vertical gate | Compliance | 3 | `PLAT-7` | CR-9 | Restricted-vertical flags per brand short-circuit the pipeline before any other stage runs. |
| `CMPL-2` | Lexical and claim-pattern scanner | Compliance | 5 | `CMPL-1` | CR-9, CR-10 | Banned words and regulated-claim patterns for income, health, superlative, and guarantee language, each with a policy citation. |
| `CMPL-3` | Claim substantiation as a hard block | Compliance | 5 | `BG-9`, `CMPL-2` | CR-9 | An unresolved claim in a generated asset blocks. It is a graph lookup, not a model judgment, so the verdict is explainable and reproducible. |
| `CMPL-4` | Likeness detection and consent artifacts | Compliance | 8 | `CMPL-2` | CR-13 | Resolves open question T4. A realistic person with no consent artifact is a hard block with Owner-only override and mandatory written justification. |
| `CMPL-5` | Jurisdiction resolution | Compliance | 5 | `CMPL-2` | CR-11 | Union of targeted geos maps to applicable disclosure regimes and the strictest wins, including EU AI Act Article 50 obligations now in force. |
| `CMPL-6` | Disclosure application | Compliance | 8 | `CMPL-5`, `CRTV-3` | CR-11 | Writes a visible label layer, sets the platform AI-content flag, and attaches provenance metadata. An asset requiring a disclosure it did not receive cannot be marked compliance_pass. |
| `CMPL-7` | Policy corpus store with a named owner and change SLA | Compliance | 5 | `CMPL-2` | CR-10 | Versioned, citation-backed rules per channel with an accountable owner. A corpus bump can force re-check of everything pending in the queue. |
| `CMPL-8` | Rejection intelligence loop | Compliance | 5 | `GW-15`, `CMPL-7` | LN-4 | A recurring rejection pattern above threshold is promoted into the corpus and into that brand's generation constraints. The corpus improves from production, not from reading changelogs. |
| `CMPL-9` | Owner-only override with alerting | Compliance | 3 | `CMPL-4` | CR-13 | Justification minimum length enforced by the database; every override alerts and appears in the audit export. |
| `CMPL-10` **GATE** | Compliance audit export and one clean external audit | Compliance | 5 | `CMPL-6`, `SEC-9` | — | Beta exit requires one clean external compliance audit of the disclosure and substantiation paths. |

### Agency platform — `AGY`

6 tickets · 36 points

| ID | Title | Sq | Pts | Depends on | PRD | Acceptance criteria |
|---|---|---|---|---|---|---|
| `AGY-1` | Agency portfolio console | Surface | 8 | `UI-1`, `SEC-4` | ACC-2, RP-6 | Cross-client performance, pending approvals, and health in one view that stays usable at 60 clients. |
| `AGY-2` | White-label theming | Surface | 5 | `AGY-1` | ACC-2, RP-5 | Agency-only, enforced by the database constraint. A business account cannot hold a white-label configuration. |
| `AGY-3` | Client portal | Surface | 8 | `AGY-2`, `APRV-10` | ACC-5, AP-2 | client_viewer and client_approver see exactly one brand and can approve without access to anything else in the agency. |
| `AGY-4` | Cross-client reuse with explicit opt-in | Creative | 5 | `BG-8` | ACC-2, BG-8 | Templates and learnings cross client boundaries only on explicit opt-in. Default is full isolation per the tenancy model. |
| `AGY-5` | Per-client billing attribution | Surface | 5 | `AGENT-4` | — | Generation and platform cost attributable per client brand, so an agency can bill through accurately. |
| `AGY-6` | Bulk cross-client operations with per-client token scoping | Spend | 5 | `AGY-1`, `APRV-3` | ACC-2, AP-5 | A bulk action across 20 clients issues 20 separately scoped tokens. One token can never authorize spend for a brand it was not issued for. |

### Beta exit — `GATE2`

2 tickets · 4 points

| ID | Title | Sq | Pts | Depends on | PRD | Acceptance criteria |
|---|---|---|---|---|---|---|
| `GATE2-1` | Beta exit-gate instrumentation | Platform | 3 | `GATE1-1`, `MON-7` | — | Approval-accept rate and day-60 CPA delta per beta brand are live and trustworthy before the review. |
| `GATE2-2` **GATE** | Beta exit review | Access | 1 | `GATE2-1`, `ADPT2-8`, `CMPL-10` | — | Gate: at least 75% approval-accept rate, demonstrated CPA improvement in at least 50% of beta brands at day 60, one clean compliance audit. |


## 8. Phase 3 — General Availability

**Weeks 29–36 as specified; ~week 40 recommended** · 21 tickets · 117 points · 14.6 points/week required

Self-serve signup, billing, vertical templates, and the support surface. Engineering load drops sharply and deliberately: the back half of this phase absorbs App Review slips and alpha or beta defect debt.

Squad load: Squad Platform 43 · Squad Creative 42 · Squad Surface 21 · Squad Spend 10 · Platform Access (PM-led) 1

### Vertical templates — `VERT`

2 tickets · 13 points

| ID | Title | Sq | Pts | Depends on | PRD | Acceptance criteria |
|---|---|---|---|---|---|---|
| `VERT-1` | Vertical template framework | Creative | 5 | `BG-8` | BG-8, ST-7 | A template seeds objectives, channel mix, creative angles, and compliance constraints for a vertical. |
| `VERT-2` | Ten vertical templates authored and reviewed | Creative | 8 | `VERT-1` | ST-7 | Each template reviewed by someone with real domain experience in that vertical, not generated and shipped. |

### Catalog-driven creative — `CAT`

3 tickets · 21 points

| ID | Title | Sq | Pts | Depends on | PRD | Acceptance criteria |
|---|---|---|---|---|---|---|
| `CAT-1` | Catalog and feed ingest | Creative | 8 | `BG-2` | CR-12 | Product, price, and inventory ingest with scheduled refresh and staleness detection. |
| `CAT-2` | Catalog-driven generation | Creative | 8 | `CAT-1`, `CRTV-3` | CR-12 | Scene graphs template over catalog rows; a thousand SKUs do not become a thousand generations. |
| `CAT-3` | Per-channel catalog ad mapping | Spend | 5 | `CAT-2`, `GW-1` | CP-1 | Catalog ad support read from the Capability Registry per channel. No channel-name branching. |

### Analytics and reporting — `ANL`

4 tickets · 24 points

| ID | Title | Sq | Pts | Depends on | PRD | Acceptance criteria |
|---|---|---|---|---|---|---|
| `ANL-1` | Creative analytics at attribute level | Platform | 8 | `OPT-7` | RP-3 | Performance attributable to creative attributes (hook type, format, angle), not just to ad ids. |
| `ANL-2` | ReportWorkflow with PDF render and white-label | Surface | 5 | `MON-7`, `AGY-2` | RP-1, RP-2, RP-5 | Scheduled weekly and monthly reports, narrated, rendered, and delivered, with agency branding where configured. |
| `ANL-3` | Methodology annotation propagation | Platform | 3 | `MON-4`, `ANL-2` | RP-4 | Annotations reach the UI and the PDF. No surface can display a blended cross-channel figure without one. |
| `ANL-4` | Resolve the analytics store decision | Platform | 8 | `MON-3` | — | Resolves open question T1 with measured cost and latency at projected volume, then executes the chosen path. |

### First-party data — `FPD`

3 tickets · 18 points

| ID | Title | Sq | Pts | Depends on | PRD | Acceptance criteria |
|---|---|---|---|---|---|---|
| `FPD-1` | First-party connectors | Platform | 8 | `MON-4` | RP-3, OP-7 | CRM, ecommerce, and call-tracking ingest feeding first_party_only metrics. |
| `FPD-2` | Offline conversion upload | Spend | 5 | `FPD-1`, `GW-1` | OP-7 | Per-channel offline conversion upload where the Capability Registry declares support. |
| `FPD-3` | First-party revenue as a goal metric | Creative | 5 | `FPD-1`, `AGENT-6` | ST-2, OP-7 | The Strategist can optimize against first-party revenue instead of platform-attributed conversions. |

### Cross-channel reallocation — `XCH`

2 tickets · 11 points

| ID | Title | Sq | Pts | Depends on | PRD | Acceptance criteria |
|---|---|---|---|---|---|---|
| `XCH-1` | Cross-channel reallocation proposals | Creative | 8 | `OPT-5`, `MON-4` | OP-8 | Reallocation reasons only over comparable metrics and states its comparability assumptions in the proposal itself. |
| `XCH-2` | ComparabilityViolation enforcement end to end | Platform | 3 | `ANL-3` | RP-4 | Any caller requesting a cross-channel aggregate over caveated metrics without accepting an annotation gets a hard error. Verified from API through UI. |

### Billing — `BILL`

3 tickets · 16 points

| ID | Title | Sq | Pts | Depends on | PRD | Acceptance criteria |
|---|---|---|---|---|---|---|
| `BILL-1` | Self-serve billing and plan tiers | Surface | 8 | `SEC-8` | — | Entry, growth, and scale tiers with seat and brand metering for both account types. No percent-of-spend pricing anywhere in the model. |
| `BILL-2` | Generation overage metering | Platform | 5 | `AGENT-4`, `BILL-1` | — | Overage billed from the same agent_run meter that drives margin-per-brand. One meter, not two. |
| `BILL-3` | Dunning and downgrade paths | Surface | 3 | `BILL-1` | — | Failed payment pauses generation before it pauses live campaigns, so a billing lapse never silently wastes a client's ad spend. |

### Support and operations — `SUP`

4 tickets · 14 points

| ID | Title | Sq | Pts | Depends on | PRD | Acceptance criteria |
|---|---|---|---|---|---|---|
| `SUP-1` | Support tooling | Surface | 5 | `AUD-4` | ACC-7 | Impersonation is fully audited and visibly flagged to the user afterward. Brand health view for triage. |
| `SUP-2` | Operational runbooks and drills | Platform | 5 | `GW-13`, `PLAT-16` | LN-7 | Runbooks for channel outage, quota storm, token incident, outbox backlog, and a rehearsed kill-switch drill. |
| `SUP-3` | Status page and incident comms | Platform | 3 | `PLAT-5` | — | Per-channel degradation is visible to customers without a human writing an update first. |
| `GATE3-1` **GATE** | GA readiness review | Access | 1 | `BILL-1`, `SUP-2`, `XCH-2`, `ANL-3` | — | Gate: every P0 requirement met, all nine channels at parity with zero waivers, security and compliance sign-off. |

---

## 9. Release Gates

| Gate | Phase | Condition |
|---|---|---|
| `APRV-8` | 0 | Hash mutation, scope escalation, cap overflow, replay, expiry, and cross-brand token use all fail closed. **Blocks every release, permanently, not just Phase 0.** |
| `GW-11` | 0 | Conformance suite exists and is executable before the second adapter starts. |
| `ADPT-11` | 0 | Meta and Google green on every conformance test with zero waivers. |
| `GATE1-3` | 1 | Alpha exit: ≥60% approval-accept rate, zero unauthorized-spend events, ≤5% disapproval rate. |
| `ADPT2-8` | 2 | All nine channels conformant with zero waivers. Any waiver is a release blocker. |
| `CMPL-10` | 2 | One clean external compliance audit of the disclosure and substantiation paths. |
| `GATE2-2` | 2 | Beta exit: ≥75% approval-accept rate, CPA improvement in ≥50% of beta brands at day 60, clean compliance audit. |
| `GATE3-1` | 3 | GA: every P0 requirement met, nine channels at parity, security and compliance sign-off. |

**The waiver policy is the whole point of `ADPT2-8`.** The user constraint was that no platform ranks above another. That is enforceable only if a channel cannot ship with a conformance exception, because the first waiver granted under launch pressure is the moment channel parity becomes a marketing claim instead of a property of the system.

---

## 10. Definition of Done

Beyond each ticket's stated acceptance criteria, four ticket classes carry additional mandatory conditions.

**Any ticket touching the spend path** (`approval-svc`, `channel-gateway` write methods, token handling):
- Negative tests before positive tests, committed in the same PR.
- Two reviewers, one from outside Squad Spend.
- Emits to `adj.audit.v1` on every denial.
- `APRV-8` re-run green.

**Any new channel adapter:**
- Full conformance suite green, zero waivers.
- Capability and Spec Registry entries committed, with a `verified_on` date.
- Cassettes recorded and replayable in CI with no production ad account touched.
- Quota model sourced from documentation with a citation in the registry entry. Empirical probing is prohibited and the test suite asserts we do not do it.
- Induced-timeout idempotency test at every object level.

**Any new event type or schema change:**
- `adjutant-events.json` updated; CI compatibility gate green.
- Consumer registry manifest updated.
- Producer publishes through the outbox, never directly.
- Retention and PII classification explicitly set, not defaulted.

**Any new brand-scoped table:**
- RLS enabled with `FORCE ROW LEVEL SECURITY`.
- Cross-tenant leak suite extended to cover it.
- Invariant test added if the table carries a business rule.

---

## 11. What Is Deliberately Not Here

Naming the exclusions is part of the estimate. Everything below is real work that a reasonable person might expect to find in a 970-point roadmap and that is intentionally absent:

- **Phase 4 post-GA scope** from the PRD — graduated autonomy tiers, retail media expansion, programmatic and CTV, localization, synthetic presenters, read API, MMM. Months 10 through 18, not estimated here.
- **Mobile applications.** The console is responsive web. Approval-on-phone is a real need and a separate project.
- **SOC 2 readiness.** Enterprise agencies will ask. It needs an owner and roughly a quarter, and it is not in these points.
- **Data residency.** EU-resident storage for EU brands is architecturally invasive and is not scoped. Worth deciding before the first EU enterprise deal, not after.
- **Migration tooling from competitor platforms.** A meaningful acquisition lever and not in Phase 0 through 3.
- **The 18 engineers themselves.** This roadmap assumes a fully staffed, onboarded team from week 1. If hiring is in progress, add the ramp before using any of these dates.

---

## 12. Traceability

Every ticket's `prd_requirements` column maps to PRD §9 requirement IDs, covering the `ACC`, `BG`, `ST`, `CR`, `AP`, `LN`, `OP`, `RP`, and `CP` families. The CSV is the queryable artifact: filter by requirement ID to see every ticket implementing it, or filter by `release_gate` to see the eight conditions that block phase transitions.

Open technical questions from the architecture document are resolved by specific tickets rather than left to drift: **T1** (analytics store) by `ANL-4`, **T2** and **T3** (render infrastructure) by `VID-2`, **T4** (likeness detection) by `CMPL-4`. **T5** (vector store at 30M embeddings) and **T6** (delegated agency signing keys) remain open and unscheduled, which is the correct treatment for questions that do not block a phase.

---

*Platform constraints referenced in acceptance criteria are sourced from [Meta Marketing API rate limiting](https://developers.facebook.com/docs/marketing-api/overview/rate-limiting/), [Google Ads Performance Max asset groups](https://developers.google.com/google-ads/api/performance-max/asset-groups), [LinkedIn marketing API access tiers](https://learn.microsoft.com/en-us/linkedin/marketing/integrations/marketing-tiers?view=li-lms-2026-08), [Microsoft Advertising API platform evolution](https://about.ads.microsoft.com/en/blog/post/april-2026/evolving-the-microsoft-advertising-api-platform), [Reddit Ads API v3](https://ads-api.reddit.com/docs/v3/), [Pinterest developer guidelines](https://policy.pinterest.com/en/developer-guidelines), [Snapchat Marketing API](https://developers.snap.com/marketing-api/Ads-API/introduction), [TikTok API for Business](https://business-api.tiktok.com/portal/docs?id=100025), [Amazon Ads Sponsored Products v3](https://advertising.amazon.com/API/docs/en-us/guides/sponsored-products/overview), [EU AI Act Article 50 guidelines](https://www.twobirds.com/en/insights/2026/european-commission-adopts-final-guidelines-on-ai-act-article-50-transparency-obligations-first-impr), and [Google Ads AI-generated content label policy](https://www.auditsocials.com/blog/google-ads-ai-generated-content-label-policy-2026).*
