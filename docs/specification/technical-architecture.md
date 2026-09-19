# Adjutant — Technical Architecture

**Companion to:** Adjutant PRD v1.0
**Version:** 1.0 (Draft for engineering review)
**Date:** September 15, 2026
**Status:** Pre-build

---

## 1. Architectural Principles

Seven principles drive every decision below. When a tradeoff is ambiguous, resolve it in favor of the higher-numbered principle.

1. **Money movement is a privileged operation.** Any code path that can cause spend passes through a single choke point that requires a cryptographically valid approval token. There is no second path. Not for admins, not for internal tools, not for migrations.
2. **Channel differences stop at the adapter boundary.** No `if channel == "meta"` above the adapter layer, ever. Channel behavior is declared as data in registries, not encoded as branches in business logic.
3. **Everything an agent does is a durable, replayable, reversible event.** Agents are non-deterministic; the system around them must not be.
4. **Tenant isolation is enforced by the database, not by application code.** Row-level security is on by default and application bugs cannot leak across tenants.
5. **Creative is structured data that renders to pixels, never pixels that we hope are correct.**
6. **External APIs are hostile.** They rate-limit, they return success then don't, they change enums on a Monday. Every write is idempotent and every read is reconciled.
7. **Cost per brand is a first-class metric.** Generation is our largest variable cost. If it isn't metered at the call site, it isn't controlled.

---

## 2. System Context

```
                        ┌──────────────────────────────┐
                        │  Users                       │
                        │  SMB owner · Agency staff ·  │
                        │  Client approver · Reviewer   │
                        └──────────────┬───────────────┘
                                       │ HTTPS / WebSocket
                        ┌──────────────▼───────────────┐
                        │  Edge (CDN + WAF)            │
                        └──────────────┬───────────────┘
                                       │
   ┌───────────────────────────────────▼──────────────────────────────────┐
   │  ADJUTANT PLATFORM                                                   │
   │                                                                      │
   │  api-gateway ─ core-api ─ orchestrator(Temporal) ─ agent-runtime      │
   │       │           │              │                      │            │
   │  approval-svc  brand-svc    creative-svc          compliance-svc     │
   │       │           │              │                      │            │
   │  channel-gateway (adapters + governor)  ─  render-svc  ─ analytics-svc│
   │                                                                      │
   │  Postgres · ClickHouse · Redis · Redpanda · S3 · Vector store         │
   └───────┬──────────────────────────────────────────────────┬───────────┘
           │                                                  │
   ┌───────▼────────────────────┐                  ┌──────────▼──────────┐
   │  Ad platform APIs (9)      │                  │  Model providers    │
   │  Meta · Google/YouTube ·   │                  │  LLM · image · video│
   │  TikTok · LinkedIn · MSFT ·│                  │  · TTS · embeddings │
   │  Reddit · Pinterest ·      │                  └─────────────────────┘
   │  Snapchat · Amazon         │                  ┌─────────────────────┐
   └────────────────────────────┘                  │  First-party data   │
                                                   │  CRM · ecom · calls │
                                                   └─────────────────────┘
```

---

## 3. Technology Choices

| Concern | Choice | Rationale |
|---|---|---|
| Orchestration | **Temporal** | Agent workflows run for hours, span human approval waits of days, and must survive deploys. Durable execution with built-in retries and signal-based human-in-the-loop is exactly the shape of this problem. Hand-rolling this on queues + cron is the classic mistake. |
| Agent runtime & adapters | **Python 3.12 / FastAPI** | Model SDKs, image/video tooling, and data work all live here. |
| Web app | **TypeScript / Next.js (App Router) + React** | Approval queue is latency-sensitive and interaction-heavy; server components keep first paint fast. |
| Transactional store | **Postgres 16** | RLS for tenant isolation, native partitioning, `jsonb` for scene graphs and channel payloads, strong constraint support. |
| Analytics store | **ClickHouse** | Hourly metric facts across 9 channels × N ad objects is a columnar workload. Postgres will not hold this past a few thousand brands. |
| Cache / rate governor | **Redis (Valkey) + Lua** | Token-bucket arithmetic must be atomic. Lua scripts give single-round-trip atomicity. |
| Event bus | **Redpanda** (Kafka API) | Metric ingest, audit fan-out, fatigue scanning, webhook fan-in. Kafka semantics without ZooKeeper operational tax. |
| Object storage | **S3** + CloudFront | Creative masters, renditions, brand assets, report PDFs. |
| Vector store | **pgvector** | Creative embeddings and Brand Graph retrieval. Co-locating with Postgres avoids a second consistency domain until scale demands otherwise. |
| Render workers | **Python + Skia/Cairo (static), FFmpeg (video)** on GPU-optional pool | Deterministic typographic rendering from the scene graph. |
| Secrets | **AWS KMS + per-tenant data keys** | Channel OAuth tokens are the crown jewels. Envelope encryption, per-tenant DEK. |
| IaC / runtime | **Terraform + EKS** | Standard. Agent and render pools are separate node groups with separate autoscaling. |
| Observability | **OpenTelemetry → Grafana/Tempo/Loki/Mimir** | Every agent step, model call, and channel call is a span with cost attributes attached. |

---

## 4. Service Decomposition

### 4.1 `core-api`
Public REST + GraphQL surface. Owns authn/authz, request validation, and tenant-context injection. Sets `app.current_brand_ids` and `app.current_actor` as Postgres session variables on every connection checkout so RLS applies. Holds no business logic beyond authorization.

### 4.2 `orchestrator` (Temporal workflows)
Owns the long-lived processes. Key workflows:

| Workflow | Trigger | Shape |
|---|---|---|
| `OnboardBrandWorkflow` | Signup / agency adds client | Site ingest → Brand Graph draft → account history ingest → **wait for human confirmation signal** → seed vertical template |
| `CampaignPlanWorkflow` | User requests campaign, or scheduled cadence | Strategist → compliance pre-check on plan → **wait for plan approval signal** → fan out `CreativeSetWorkflow` per brief |
| `CreativeSetWorkflow` | Child of plan workflow, or refresh | Copywriter ∥ Art Director ∥ Video → scene graph → render all renditions → spec validation → compliance check → **wait for creative approval signal** |
| `DeploymentWorkflow` | Approved plan + approved creative | Pre-flight validation → per-channel `ChannelBuildActivity` (parallel, independently retryable) → verification read-back → mark live |
| `MonitorBrandWorkflow` | Cron, hourly per brand | Metric sync → anomaly scan → fatigue scan → emit proposals → auto-approve eligible / queue rest |
| `OptimizationWorkflow` | Proposal accepted | Guardrail re-check at execution time → channel mutation → verification → record reversible action |
| `ReportWorkflow` | Cron, weekly/monthly | Aggregate → narrate → render PDF → deliver |

**Why approval waits live in Temporal:** an approval that takes four days is a workflow that sleeps four days and resumes with full context. No polling, no state reconstruction, no "which step was this in?" archaeology.

### 4.3 `agent-runtime`
Executes one agent turn as a stateless Temporal activity. Each agent is a declared bundle: system prompt, tool allowlist, model tier policy, output JSON schema, token/cost ceiling, and retry policy. Hard rules:

- Structured output validated against a JSON Schema; validation failure retries with the error fed back, max 3, then escalates to a human task.
- Tool allowlists are per-agent. The Copywriter cannot call the channel gateway. The Optimizer can *propose* but its tool list contains no write verbs.
- Every invocation writes an `agent_run` row with model, tokens, latency, USD cost, and brand attribution. This is how §1.7 gets enforced.

### 4.4 `brand-svc`
Brand Graph CRUD, provenance enforcement (an assertion without a provenance URI cannot be written in `confirmed` state), brand kit asset management, palette/typeface extraction, vertical templates, Brand Graph cloning with field-level diff.

### 4.5 `creative-svc` + `render-svc`
`creative-svc` owns the scene graph: construction, mutation, versioning, and derivation of renditions per placement. `render-svc` is a pure function — scene graph + target spec → asset bytes. Deterministic and cacheable: the same graph and spec always yield the same output, keyed by `hash(scene_graph, spec_version)`.

**Scene graph model:**

```json
{
  "version": 3,
  "canvas": { "master_ratio": "1:1", "bleed_px": 64 },
  "layers": [
    { "id": "bg",    "type": "image",     "source": {"kind":"generated","asset_id":"..."},
      "fit": "cover", "z": 0 },
    { "id": "prod",  "type": "cutout",    "source": {"kind":"brand_asset","asset_id":"..."},
      "anchor": "center-right", "scale": 0.62, "z": 10 },
    { "id": "hook",  "type": "text",      "content": "Fix it before it floods",
      "style_ref": "headline_primary", "max_lines": 2, "shrink_to_fit": true,
      "anchor": "top-left", "safe_zone": "strict", "z": 20 },
    { "id": "logo",  "type": "brand_mark","asset_id": "...", "anchor": "bottom-left",
      "min_px": 88, "z": 30 },
    { "id": "cta",   "type": "button",    "label": "Book today",
      "style_ref": "cta_solid", "anchor": "bottom-right", "z": 30 }
  ],
  "style_refs": { "headline_primary": {"typeface_id":"...","weight":700,
                   "size_range_pt":[28,64],"color":"#0B2B4A","contrast_min":4.5} },
  "rules": { "text_layers_never_rasterized": true,
             "logo_never_regenerated": true,
             "reflow_policy": "anchor_preserving" }
}
```

Rendition derivation is an **anchor-preserving re-layout**, not a scale: anchors resolve against the target ratio's safe zone, text shrinks within its declared size range, and any layer that cannot fit its safe zone raises a `RenditionInfeasible` error rather than silently cropping. That error is a spec-validation failure, and spec-validation failures cannot enter the approval queue (PRD CR-2).

### 4.6 `approval-svc`
The choke point from §1.1. Owns the approval state machine and is the **only** issuer of approval tokens.

**States:** `draft → pending_internal → pending_client → approved → consumed` with side-exits `rejected`, `changes_requested`, `expired`, `voided`.

**Token format** — Ed25519-signed, short-lived:

```
payload = {
  tok: uuid,
  sub_type: "plan" | "creative_set" | "deployment" | "budget_change" | "structural_change",
  sub_id: uuid,
  sub_hash: sha256,              # plan_hash or creative_hash
  brand_id: uuid,
  scopes: ["channel:meta","channel:google","op:create","op:budget_set"],
  usd_daily_cap: 500.00,
  usd_total_cap: 15000.00,
  approver_id: uuid,
  approval_chain: [uuid, uuid],   # internal, then client
  iat, exp,                       # exp ≤ 72h
  nonce
}
signature = Ed25519(payload, key=KMS_approval_signing_key)
```

Invariants, all enforced in `channel-gateway`, not in the UI:

- `sub_hash` must equal the current hash of the subject. Any mutation changes the hash and silently invalidates every outstanding token — this is how "changed after approval" (PRD AP-4) becomes structural rather than procedural.
- Tokens are single-consumption per `(tok, channel, operation)` tuple, tracked in Redis with a Postgres write-through. Replay is rejected.
- Requested operation must be in `scopes`; requested spend must be within both caps.
- Expiry is absolute. No refresh, no extension. Re-approval issues a new token.

### 4.7 `channel-gateway`
The only egress to ad platforms. Three internal parts:

**(a) Capability Registry** — declarative, versioned, seeded per channel:

```yaml
channel: meta
version: 2026.09
objectives: [awareness, traffic, engagement, leads, app_promotion, sales]
hierarchy: [campaign, ad_set, ad]
budget_levels: [campaign, ad_set]
bid_strategies: [lowest_cost, cost_cap, bid_cap, roas_goal]
targeting_dimensions: [geo, age, gender, language, custom_audience,
                       lookalike, placement, advantage_plus_auto]
supports: { catalog_ads: true, lead_forms: true, dynamic_creative: true,
            staged_rollout: true, dayparting: false }
quota_model:
  kind: points_per_ad_account_hour
  formula: "base + 40 * active_ads"
  base: { dev: 300, standard: 100000 }
  headers: { usage: "X-Ad-Account-Usage", tier_field: "ads_api_access_tier" }
prerequisites: [pixel_configured, billing_valid]
```

The Strategist plans against this registry. Adding a tenth channel is a YAML entry plus an adapter class.

**(b) Spec Registry** — placement-level creative specs (ratio, min/max resolution, max bytes, duration bounds, safe-zone insets, character limits per text field, codec constraints). Versioned, because platforms change specs without notice. `render-svc` and the validator both read from here; nothing hard-codes a dimension.

**(c) Rate-limit Governor** — Redis token buckets keyed `{channel}:{ad_account_id}`, refilled per that channel's declared `quota_model`. Meta's bucket grows with active ad count, so the governor recomputes capacity on every structure sync. LinkedIn's Development tier caps *writes to 5 ad accounts* — that is modeled as a distinct constraint kind (`write_account_allowlist`), not a rate. Writes enqueue with priority: `deployment > optimization > backfill`. Governor state is user-visible, surfacing "queued behind 3 deployments" rather than a spinner.

**Adapter interface** — every channel implements exactly this:

```python
class ChannelAdapter(Protocol):
    channel: Channel
    def connect(self, auth_grant) -> ChannelConnection: ...
    def refresh(self, conn) -> ChannelConnection: ...
    def read_structure(self, conn, since=None) -> list[CampaignObject]: ...
    def read_metrics(self, conn, window, level) -> list[MetricFact]: ...
    def preflight(self, conn, plan_slice) -> PreflightResult: ...
    def build(self, conn, plan_slice, creatives, token,
              idem_key: str) -> BuildResult: ...
    def upload_asset(self, conn, rendition, idem_key) -> ChannelAssetRef: ...
    def mutate(self, conn, mutation: Mutation, token,
               idem_key: str) -> MutationResult: ...
    def verify(self, conn, native_ids) -> list[ObjectState]: ...
    def rejections(self, conn, native_ids) -> list[Rejection]: ...
```

`build` and `mutate` are the only methods accepting a token, and they are the only methods permitted to write. Adapter code asserts token validity locally before the first HTTP call — the check is not delegated to a caller.

**Idempotency:** every write carries `idem_key = sha256(brand_id, plan_hash, channel, object_path, attempt_class)`. Where the channel supports a native idempotency or client-request-id field, it is populated. Where it does not, the adapter does a read-by-name-and-key reconciliation before creating. This is how PRD LN-2's "retry produces no duplicate campaigns" holds against a channel that returned 504 after committing.

**Non-negotiable:** the governor never probes limits empirically. Pinterest explicitly prohibits testing its rate limits or abuse-prevention systems without authorization, so all quota models are declared from documentation and adjusted only on observed 429 headers.

### 4.8 `compliance-svc`
Blocking authority over every outbound asset. Pipeline, executed in order, short-circuiting on block:

1. **Vertical gate** — brand's restricted-vertical flags.
2. **Lexical & claim scan** — banned-word lists, regulated-claim patterns (income, health, superlative, guarantee).
3. **Claim substantiation** — every detected claim must resolve to a `brand_graph_assertion` with provenance. Unresolved claim in a generated asset = block. This is a graph lookup, not a model judgment.
4. **Likeness detection** — face/voice presence, public-figure similarity check. Realistic person + no `consent_artifact` = hard block, Owner-only override with mandatory written justification, logged.
5. **Jurisdiction resolution** — union of targeted geos → applicable disclosure regimes → strictest wins.
6. **Disclosure application** — writes required disclosure into the creative (visible label layer) and into the submission payload (platform AI-content flags), plus machine-readable provenance marks where required. An asset requiring a disclosure it did not receive cannot be marked `compliance_pass`.
7. **Policy-corpus check** — channel policy rules with citations, versioned corpus with a named owner and a change SLA.

The **rejection intelligence loop** closes it: every platform rejection is captured verbatim into `channel_rejection`, classified, mapped to a remediation, and — if the pattern recurs above threshold — promoted into the policy corpus and into that brand's generation constraints. The compliance corpus improves from production feedback rather than from someone reading changelogs.

### 4.9 `analytics-svc`
Metric ingest → normalization → ClickHouse. Owns the **normalization contract**, which is deliberately conservative:

- Channel-native metrics land raw and immutable in `metric_fact_raw`.
- Normalized fields are computed into a separate layer with an explicit `comparability_class`: `direct` (impressions, clicks, spend), `caveated` (platform-attributed conversions — differing windows and models), `first_party_only` (revenue, qualified leads).
- Any cross-channel aggregate over `caveated` metrics carries a methodology annotation through to the UI. There is no code path that emits a single blended ROAS without one. PRD §14 names this as a top-five risk; the data model is where it gets prevented.
- Fatigue scoring runs on a rolling window and is **compound**: requires simultaneous frequency rise, engagement decay, and cost-per-result rise, with format-calibrated thresholds from the spec registry (short-form video thresholds trip far earlier than static feed). No single-signal or time-elapsed trigger exists.

---

## 5. Critical Path: Approved Plan → Live Ads

```
1. approval-svc          issues token T (hash-bound, scoped, capped)
2. DeploymentWorkflow    starts; records deployment row (state=preflight)
3. per channel, parallel:
     preflight()         pixel · conversion action · UTM · landing 200 · billing
                         → any failure: channel marked blocked, others proceed
4.   governor.acquire()  token-bucket reservation; queue if exhausted
5.   upload_asset()      renditions → channel asset refs (idempotent)
6.   build()             ┌ assert_token_valid(T, channel, op, usd)
                         ├ create campaign  (idem_key)
                         ├ create ad_group  (idem_key)
                         └ create ad        (idem_key)
7.   verify()            read-back; states must match intended
8.   rejections()        capture verbatim; classify; propose remediation
9. reconcile             mark deployment live | partial | failed
10. audit ledger         one action row per mutation, with token id + diff
                         + revert_path
11. emit event            deployment.completed → MonitorBrandWorkflow armed
```

**Failure semantics.** Channels are independent units of success. TikTok 5xx does not roll back live Meta campaigns (PRD LN-2). A failed channel is retryable and the retry reuses the same `idem_key`, so a create that actually succeeded behind a timeout reconciles instead of duplicating. Rollback (LN-5) walks the `action.revert_path` entries in reverse within the 10-minute window.

---

## 6. Multi-Tenancy & Security

### 6.1 Isolation model
`brand_id` is the tenant key — not `account_id`. An agency's 60 clients are 60 tenants under one account, because PRD §7.3 ADR-6 requires client-level isolation with opt-in cross-client learning.

Enforcement is layered:

1. **Postgres RLS on every brand-scoped table**, `FORCE ROW LEVEL SECURITY` so even the table owner is subject to it. Policies read `current_setting('app.current_brand_ids')`.
2. **Connection-level context injection** in `core-api` on checkout; a connection without tenant context can read nothing.
3. **S3 prefix isolation** `s3://adjutant-assets/{brand_id}/...` with IAM conditions.
4. **Per-tenant DEKs** for channel OAuth tokens, envelope-encrypted under a KMS CMK. A database dump without KMS access yields no usable channel credentials.
5. **Vector namespace per brand** for creative embeddings; the learning store writes brand-scoped signals by default and global signals only after de-identification and k-anonymity thresholds.

### 6.2 Channel credential handling
Tokens never enter logs, spans, or agent context. `agent-runtime` has no network path to ad platforms — it can only request channel operations through `channel-gateway`, which resolves credentials server-side. This means a prompt-injected agent cannot exfiltrate a token, because it never holds one.

Where a channel prefers or requires bring-your-own-key with client-side storage (Pinterest permits end-user-key apps only if credentials are stored locally rather than server-side), that mode is a distinct `credential_mode` on the connection with its own code path.

### 6.3 Spend-authority enforcement
Three independent checks, all required:

| Layer | Check |
|---|---|
| `approval-svc` | Approver's role threshold ≥ requested daily spend (PRD ACC-4) |
| Token | `usd_daily_cap` / `usd_total_cap` bound into the signed payload |
| `channel-gateway` | Live re-read of brand/channel/campaign ceilings at execution time, immediately before the HTTP call |

The third check exists because a proposal approved on Monday may violate a ceiling by Wednesday. Guardrails are re-evaluated at execution, never trusted from approval time.

### 6.4 Global kill switch
`POST /brands/{id}/kill` writes a `brand_kill_switch` row that the gateway checks on **every** egress call, fans out pause mutations to all channels at highest governor priority, then runs a verification read per channel within 5 minutes. Any channel that cannot be verified paused raises a page, not a toast.

---

## 7. Cost Control

Generation is the dominant variable cost and the most likely path to negative gross margin per brand (PRD §14).

- Every model call is metered at the call site into `agent_run` with USD cost and brand attribution. Margin per brand is a daily materialized rollup, reviewable from alpha.
- **Model tiering by stakes:** concept ideation and strategy use frontier tier; the twentieth copy variant on a proven concept uses a cheap tier. Declared per agent, overridable per plan tier.
- **Derive, don't regenerate.** Nine renditions of one creative are nine renders of one scene graph, not nine generations. Render output is content-addressed and cached on `hash(scene_graph, spec_version)`, making re-renders after a text edit nearly free.
- **Budget ceilings per brand per month** on generation spend, enforced in `agent-runtime` before dispatch. Exceeding it degrades to cheaper tiers and notifies, rather than silently burning margin.

---

## 8. Environments & Testing

| Concern | Approach |
|---|---|
| Channel integration tests | Sandbox/test accounts where offered (TikTok sandbox accounts, LinkedIn's single creatable test ad account); recorded-cassette replay for the rest. No CI path touches a production ad account. |
| Contract tests per adapter | The Channel DoD from PRD §8 is executable: a shared conformance suite every adapter must pass, so "equal priority" is verified rather than asserted. |
| Approval-token security tests | Explicit negative suite: hash mutation, scope escalation, cap overflow, replay, expiry, cross-brand token use. All must fail closed. Any regression here blocks release. |
| Rendition validation | Golden-image diffs plus programmatic assertions: no clipped text, no mid-word breaks, contrast ≥ 4.5:1, logo within safe zone, all nine ratios. |
| Load testing | Governor behavior under simultaneous deployment across 500 brands; verify queueing and zero 429-triggered failures. |
| Migration safety | Expand/contract only; RLS policies are part of the migration and tested by a cross-tenant leak suite that runs on every migration. |

The schema ships with an executable invariant suite (`adjutant-schema-tests.sql`) covering all 14 negative cases: cross-tenant read, cross-tenant write, missing tenant context, audit-ledger UPDATE, audit-ledger DELETE, spend action without an authorizing token, token auto-void on subject-hash mutation, token replay, token expiry beyond 72h, unprovenanced confirmed assertion, single-signal fatigue flag, duplicate create under a reused idempotency key, server-side token on a BYOK connection, and a global learning signal that is not de-identified. Every one must fail closed. Verified against PostgreSQL 18.

---

## 9. Scale Targets (18-month planning envelope)

| Dimension | Target |
|---|---|
| Brands | 25,000 |
| Connected ad accounts | 90,000 |
| Active ad objects | 4M |
| Hourly metric facts | ~100M rows/day into ClickHouse |
| Creative renditions stored | 30M |
| Concurrent Temporal workflows | 60,000 |
| Deployment P95 (5 channels, 12 ads) | ≤ 8 minutes excluding governor queueing |
| Approval-queue API P95 | ≤ 250 ms |

---

## 10. Build Sequence (maps to PRD roadmap phases)

**Phase 0 — Foundations.** Postgres schema + RLS + leak suite · approval-svc with token signing and the full negative test suite · channel-gateway skeleton with Capability/Spec registries and governor · Temporal wiring · audit ledger · Meta and Google adapters to full DoD · **all platform App Review applications filed** (the long pole, per PRD Q1).

**Phase 1 — Closed alpha.** Brand Graph ingest with provenance enforcement · Strategist, Copywriter, Art Director agents · scene graph + render-svc (static) · approval queue UI · DeploymentWorkflow · basic monitoring.

**Phase 2 — Parity + video.** Remaining seven adapters through the conformance suite · video pipeline · Optimizer + auto-approve rules · compound fatigue detection + refresh pipeline · compliance-svc to full P0 · agency console, white-label, client portal.

**Phase 3 — GA.** Vertical templates · catalog-driven generation · winner-scaling · creative analytics · first-party data connectors · cross-channel reallocation with comparability annotations · billing · support tooling.

---

## 11. Open Technical Questions

| # | Question | Blocking |
|---|---|---|
| T1 | ClickHouse from day one, or Postgres partitioned tables until ~2,000 brands? Dual-store cost vs. a painful later migration. | Phase 1 |
| T2 | Does render-svc need GPU, or is CPU Skia sufficient for static at target throughput? Determines node-group cost model. | Phase 1 |
| T3 | Video assembly in-house (FFmpeg scene graph) vs. a managed render API — control and margin vs. time to market. | Phase 2 |
| T4 | Public-figure likeness detection: build, buy, or rely on a conservative reject-on-any-realistic-face policy in V1? Ties to PRD Q10. | Phase 2 |
| T5 | pgvector adequate at 30M creative embeddings, or dedicated vector store? | Phase 3 |
| T6 | Can approval tokens be delegated to an agency's own signing key for client-side attestation, or does that break our audit guarantee? | Non-blocking |
