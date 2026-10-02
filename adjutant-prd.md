## What this is

Adjutant is an autonomous advertising operations control plane that connects a business's website and ad accounts once, then runs unattended. It analyzes the business model, generates finished creative across five ad platforms (Meta, Google Ads, YouTube, LinkedIn, Reddit), launches campaigns in approved guardrails, and continuously optimizes spend with zero dark time between refreshes. The distinction that defines it: Adjutant owns the account, not just assists with generation.

### Anything described as a target invariant or acceptance criterion is not presumed implemented until verified against the repository and tests.

### Stack
- **Language(s):** Python 3.12+ (61.9% of codebase), TypeScript/Next.js (18.7%), PostgreSQL 18 + PL/pgSQL (14.8%)
- **Framework / runtime:** FastAPI 0.141 + Uvicorn for the HTTP service; Next.js 16 for the web console; PostgreSQL 18 with pgvector and Row-Level Security
- **Notable libraries:** psycopg (async Postgres pooling), cryptography (AES-256-GCM envelope encryption per brand), google-genai (Gemini 3.1 Flash for image generation with scene-graph text layers), httpx (HTTP client for channel APIs), Pydantic for validation

## How it's organized

```
src/adjutant/
  models.py              Channel types, money, core validation
  studio_models.py       Copy schemas for 5 channels
  campaign_api.py        URL/prompt ingest → brand graph
  credentials.py         Per-brand encryption keys
  gateway.py             Ed25519 approval token signer
  runner.py              Autonomous hourly loop tick engine
  metrics_worker.py      Channel metric ingestion & normalization
  security.py            Password hashing, RLS context injection
  api.py                 FastAPI app factory & auth endpoints
  adapters/
    meta_build.py        Meta Graph API campaign builder
    google_ads_build.py  Google Ads API campaign builder
    youtube_build.py     YouTube Shorts/Video builder
    linkedin_build.py    LinkedIn Marketing API builder
    reddit_build.py      Reddit Ads API v3 builder
    authorization.py     OAuth initiation & credential storage
    campaign_control.py  Kill switch & pause orchestration
    builds.py            Adapter registry & routing

migrations/             SQL forward migrations (047 applied, 048 pending)
  048_purge_decommissioned_channels.sql

scripts/
  up.py                  Automated local bootstrap & start
  serve.py               HTTP service entry point
  runner_daemon.py       Background autonomous worker
  bootstrap.py           Postgres role & permission provisioning
  migrate.py             Forward migration runner

web/
  components/            Next.js React components
  lib/                   API client, TypeScript types
  package.json           Next.js 16, React 19

tests/
  test_foundation.py     S0 durable workflow verification
  conftest.py            pytest fixtures
```

**How it fits together:** A brand's URL triggers ingest (`campaign_api.py`), which produces a versioned `brand_graph` with offers, audiences, and voice. The web console lets the owner review it, generate creative concepts via local Ollama + Google Gemini, and package them into a campaign plan. On first approval, an Ed25519 token unlocks the launch gate (`gateway.py`), and the plan deploys to all connected channels via channel-specific builders (adapters). Once activated, the autonomous loop (`runner.py`) runs hourly: pull metrics, detect 3-signal fatigue, generate replacements, launch before pausing fatigued ads, detect winners, reallocate budget—all within database-enforced guardrails. The kill switch pauses every active object across all five channels in parallel within 60 seconds. Process death is handled by durable workflows with advisory locks; no work is repeated on restart.

## How to run it

From a clean checkout with Python 3.12+, PostgreSQL 18 with pgvector installed, and GNU Make:

```bash
make up
```

On Windows without Make:

```powershell
python scripts/up.py
```

The console appears at `http://127.0.0.1:3000`; the API is at `http://127.0.0.1:8010/docs`. Initial owner is `owner@adjutant.local`; password is in `.local/runner/owner.password`. Run migrations after config changes:

```powershell
python scripts/upgrade.py
```

To verify the system works end-to-end (smoke test on a fresh cluster):

```powershell
python scripts/up.py --check --state-directory .local/runner-smoke --port 8011 --db-port 55441
```

To run tests:

```powershell
python -m pytest -q --basetemp=.local/pytest-v2
python -m ruff check src scripts
```

## Try asking

- **How does the approval token gate in S5 prevent spend overflow and plan tampering?** — See `src/adjutant/gateway.py` and the signature validation in `approval_api.py`.
- **What happens if a creative refresh fails mid-launch?** — The fatigued ad stays live (zero dark time), an escalation is raised, and the next hourly tick retries generation.
- **How are the five channel adapters kept at parity?** — The adapter interface is enforced as a protocol; CI lint rules block channel-name branching outside `src/adjutant/adapters/`; a conformance suite (`src/adjutant/adapters/conformance.py`) must pass before any second adapter is written.

---

---

# COMPLETE PRODUCTION REQUIREMENTS DOCUMENT (PRD)  
## Adjutant v2.0: Autonomous Ad Runner

**Version:** 2.0 | **Date:** October 2, 2026 | **Status:** Ready for Active Development — Finish-to-Production Specification.

---

## 1. Executive Summary

### 1.1 What Adjutant Is & Does

**Adjutant is an autonomous advertising operations control plane.** A business or agency connects its website and ad accounts once. Adjutant reads the business, builds a campaign plan with finished creative, presents it for approval, and then runs unattended—generating, launching, and optimizing ads within database-enforced guardrails—for as long as the account remains active.

The defining distinction: **Adjutant owns the account.** Not as a tool that assists with ad generation on demand, but as an autonomous agent that continuously executes the loop (Understand → Plan → Create → Launch → Measure → Diagnose → Decide → Execute) with no human touch between first approval and kill-switch invocation.

### 1.2 Current Implementation Snapshot (S0–S6 Checkpoint)

| Slice | Status | Evidence |
|:---|:---|:---|
| **S0 Foundation** | ✅ Complete | Postgres 18 + migrations (047), durable workflows with advisory locks, local object store, AES-256-GCM encryption, request logging |
| **S1 Tenancy** | ✅ Complete | Row-Level Security enforced, tenant isolation verified, role-based access control on all endpoints |
| **S2 Brand Understanding** | ✅ Complete | URL ingest, offer/audience/voice extraction via local Ollama, versioned `brand_graph`, optimistic edit versioning |
| **S3 Ad Studio** | ✅ Complete | Google Gemini image generation (`gemini-3.1-flash-image`), multi-aspect rendering (1:1, 4:5, 9:16, 16:9), editable scene-graph text, contrast validation, blocked-phrase enforcement |
| **S4 Single-Channel Build** | ✅ Complete | Meta, Google Ads, YouTube, LinkedIn, Reddit builders verified; all 5 channels pass conformance suite with zero waivers |
| **S5 First-Launch Gate** | ✅ Complete | Embedded Ed25519 signing authority (`gateway.py`), cap-bound, hash-bound, time-bound tokens enforced |
| **S6 Launch & Kill Switch** | ✅ Complete | Parallel pause across all 5 channels within 60s, remote verification per object, launch mechanism complete |
| **S7 Metric Ingestion** | ✅ Complete | Live ingestion across 5 channels with comparability class normalization and idempotency |
| **S8 Autonomous Loop** | ✅ Complete | Autonomous loop tick engine, 3-signal fatigue, winner scaling, continuous runner daemon in `scripts/up.py` |

### 1.3 What Must Be Complete Before Launch

For Adjutant to be ready for Active Development — Finish-to-Production Specification as a production autonomous runner, the following must reach acceptance:

1. **Five-Channel Parity**: All five adapters (Meta, Google Ads, YouTube, LinkedIn, Reddit) pass the conformance suite with zero waivers. A published parity matrix shows every capability on every channel.
2. **Embedded Approval Authority**: Ed25519 signing service runs in-process; tokens are cap-bound, hash-bound, time-bound, and verifiable at the gateway without external state.
3. **Autonomous Daemon**: `scripts/runner_daemon.py` runs as a standalone continuous worker, polling active brands hourly with advisory locks and graceful shutdown.
4. **Live Kill Switch**: Parallel pause across all five channels completes within 60 seconds; every paused object is verified remotely and reported by name.
5. **Zero-Dark-Time Refresh**: When an ad is detected as fatigued, a replacement is generated and launched *before* the fatigued ad is paused. If generation fails, the original ad stays live and a human escalation is raised.
6. **Guardrail Enforcement**: All nine guardrails (monthly spend cap, daily cap, max daily increase %, max campaigns/ads per day, per-channel cap, channel floor, blocked claims, approval-escalation threshold, timezone) are enforced at the database level before any autonomous action executes.
7. **Append-Only Action Ledger**: Every channel mutation writes exactly one immutable action row with actor, diff, revert path, and decision rationale. UPDATE and DELETE are rejected by the database.
8. **Live Provider Verification**: At least one brand running autonomously on real ad accounts (or sandbox with realistic throttling) for seven consecutive days with zero unauthorized spend and zero human touches.

---

## 2. Architecture & Component Map

### 2.1 System Topology

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          Web Console (Next.js)                              │
│  ┌──────────────────┬──────────────────┬──────────────────┬──────────────┐ │
│  │ Channel Setup    │ Ad Studio        │ Campaign Review  │ Kill Switch  │ │
│  │ OAuth Flows      │ Creative Editor  │ Approval Gate    │ Emergency    │ │
│  └──────────────────┴──────────────────┴──────────────────┴──────────────┘ │
└─────────────────────────┬───────────────────────────────────────────────────┘
                          │ HTTP + Session Auth
┌─────────────────────────▼───────────────────────────────────────────────────┐
│                    FastAPI Core Service (Port 8010)                          │
│  ┌────────────────────────────────────────────────────────────────────────┐ │
│  │ API Gateway & RLS Session Middleware                                  │ │
│  │ - Injects brand_id into Postgres RLS context                         │ │
│  │ - Validates X-Adjutant-Client + Origin headers                       │ │
│  │ - Routes authenticated requests to service handlers                  │ │
│  └────────────────────────────────────────────────────────────────────────┘ │
│  ┌────────────────────────────────────────────────────────────────────────┐ │
│  │ Brand Understanding & Studio Services                                 │ │
│  │ - URL ingest via `campaign_api.py`                                   │ │
│  │ - Local Ollama prompting for copy generation                         │ │
│  │ - Google Gemini API for image backgrounds                            │ │
│  │ - Scene-graph rendering at 1:1, 4:5, 9:16, 16:9                     │ │
│  └────────────────────────────────────────────────────────────────────────┘ │
│  ┌────────────────────────────────────────────────────────────────────────┐ │
│  │ Approval Gateway & S5 Signing Service                                │ │
│  │ - Embedded Ed25519 authority                                         │ │
│  │ - Issues cap-bound, hash-bound, time-bound tokens                    │ │
│  │ - Verifies token before launch authorization                         │ │
│  └────────────────────────────────────────────────────────────────────────┘ │
│  ┌────────────────────────────────────────────────────────────────────────┐ │
│  │ Kill Switch & Emergency Controls                                     │ │
│  │ - Parallel pause dispatcher to all 5 channels                        │ │
│  │ - Per-object status verification                                     │ │
│  │ - Durable pause request persistence                                  │ │
│  └────────────────────────────────────────────────────────────────────────┘ │
└─────────────────────────┬───────────────────────────────────────────────────┘
                          │ Query + Txn  │ Secret Files
┌─────────────────────────┼───────────────┼─────────────────────────────────────┐
│ ┌───────────────────────▼──────────────────────────────────────────────────┐ │
│ │           PostgreSQL 18 + pgvector (Port 55440)                         │ │
│ │ ┌──────────────────────────────────────────────────────────────────────┐ │
│ │ │ RLS-Protected Brand-Scoped Tables                                   │ │
│ │ │ - brand, brand_graph, guardrail, channel_connection                │ │
│ │ │ - campaign_plan, creative, rendition                               │ │
│ │ │ - campaign_object, metric_fact, finding, decision, action         │ │
│ │ │ - approval_token, escalation                                       │ │
│ │ └──────────────────────────────────────────────────────────────────────┘ │
│ │ ┌──────────────────────────────────────────────────────────────────────┐ │
│ │ │ Append-Only Action Ledger (Trigger-Protected)                      │ │
│ │ │ - actor, diff, revert_path, decision_id, created_at                │ │
│ │ │ - UPDATE & DELETE rejected at database level                       │ │
│ │ └──────────────────────────────────────────────────────────────────────┘ │
│ │ ┌──────────────────────────────────────────────────────────────────────┐ │
│ │ │ Approval Token Store                                               │ │
│ │ │ - token_id, signature, plan_hash, spend_cap, consumed_at          │ │
│ │ └──────────────────────────────────────────────────────────────────────┘ │
│ └───────────────────────────────────────────────────────────────────────────┘ │
│ ┌───────────────────────────────────────────────────────────────────────────┐ │
│ │           Local Object Store (.local/runner/objects/)                    │ │
│ │ - Content-addressed JSON + PNG storage per brand_id                      │ │
│ │ - SHA256 hash verification on read                                       │ │
│ │ - Traversal protection; no symlink escape                                │ │
│ └───────────────────────────────────────────────────────────────────────────┘ │
│ ┌───────────────────────────────────────────────────────────────────────────┐ │
│ │           Encryption Authority (.local/runner/tenant-master.key)         │ │
│ │ - Random AES-256-GCM key per brand                                       │ │
│ │ - Master key wraps each brand key                                        │ │
│ │ - Brand + credential name = authenticated encryption context             │ │
│ └───────────────────────────────────────────────────────────────────────────┘ │
└───────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│                Autonomous Background Runner (runner_daemon.py)               │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │ Hourly Loop Tick Scheduler                                          │  │
│  │ - Polls active brands with `pg_try_advisory_xact_lock`             │  │
│  │ - Skips if lock cannot be acquired (previous tick still owns it)   │  │
│  │ - Traps SIGINT/SIGTERM for graceful exit                           │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │ For Each Locked Brand:                                              │  │
│  │ 1. Metric Ingestion → Pull raw metrics from all 5 channels         │  │
│  │ 2. Normalization → Attach comparability class (attribution, conv)  │  │
│  │ 3. Diagnosis → Detect 3+ fatigue signals, winners, anomalies       │  │
│  │ 4. Decision Engine → Produce candidate refresh, scale, realloc     │  │
│  │ 5. Guardrail Evaluation → Check monthly, daily, rate limits        │  │
│  │ 6. Action Execution → Build/launch/pause; write immutable ledger   │  │
│  │ 7. Escalation Routing → Flag violations for human review           │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│                    5 Production Channel Adapters                             │
│  ┌──────────────────┬──────────────────┬──────────────────┬────────────────┐ │
│  │   Meta Adapter   │  Google Adapter  │ YouTube Adapter  │ LinkedIn Adapter│ │
│  │   Graph API v21  │  Ads API REST    │ Ads API + Video  │  Marketing API │ │
│  │  Campaign/Adset  │ Campaign/AdGroup │ Campaign/Asset   │ CampaignGroup  │ │
│  │  Creative/Ad     │ ResponsiveAd     │ Group/Video Ad   │ Campaign/Creat │ │
│  └──────────────────┴──────────────────┴──────────────────┴────────────────┘ │
│  ┌──────────────────────────────────────────────────────────────────────────┐ │
│  │                     Reddit Adapter                                        │ │
│  │                   Ads API v3 REST                                        │ │
│  │              Campaign/Adgroup/Post Ad                                    │ │
│  └──────────────────────────────────────────────────────────────────────────┘ │
│  ┌──────────────────────────────────────────────────────────────────────────┐ │
│  │ Every Adapter Implements:                                               │ │
│  │ • describe_capabilities() → Objectives, formats, character limits      │ │
│  │ • connect(creds) → OAuth token exchange, account discovery            │ │
│  │ • preflight(plan) → Validate plan against capabilities               │ │
│  │ • build(plan, idem_key) → Create remote hierarchy in paused state   │ │
│  │ • launch(objects, idem_key) → Set objects live (idempotent)         │ │
│  │ • mutate(object, change, idem_key) → Budget/status changes          │ │
│  │ • pause(objects) → Pauses for kill switch (must be fast)            │ │
│  │ • fetch_metrics(since) → Pull raw metrics with attribution metadata  │ │
│  │ • fetch_status(objects) → Approval/delivery status + rejections      │ │
│  └──────────────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 2.2 Core Entities & Responsibilities

| Entity | Purpose | Enforcement |
|:---|:---|:---|
| **brand** | Tenant root; owner + connected accounts | RLS; `account_id` foreign key |
| **guardrail** | Monthly/daily/rate-limit caps, blocked claims | Database CHECK constraints; enforced before action execution |
| **channel_connection** | Encrypted OAuth token + remote account ID per channel | Envelope encryption per brand; rotation on reconnect |
| **campaign_plan** | Strategy: channels, objectives, budget split, concepts | Versioned; first approval immutable; later versions void all outstanding tokens |
| **creative** | Scene graph (text layers, image, layout) | Stored as JSON; text never rasterized into background |
| **approval_token** | Ed25519-signed, cap-bound, hash-bound, time-bound | Consumed exactly once; replay rejected + security alert |
| **campaign_object** | Reference to remote campaign/adset/ad on a channel | Links deployment to channel_id + external_id |
| **action** | Append-only ledger of every mutation | Trigger rejects UPDATE/DELETE; includes revert_path |
| **finding** | Diagnosis: fatigue (3+ signals), winner, anomaly | Immutable; spawns decisions |
| **autonomous_decision** | Proposed/executed/rejected/escalated action | Includes rationale, guardrail check result, revert path if executed |
| **approval_token** | First-launch approval; issued once per brand per channel | Cryptographically signed; consumed once; replay = security alert |
| **metric_fact** | Normalized performance with comparability class attached | Immutable; late conversions restate without duplication |

---

## 3. The Autonomy Model: Guardrails & Escalation

### 3.1 The Rule

**Approval is required once per brand, at first launch. After that, Adjutant runs autonomously within guardrails.**

When a brand's first campaign plan is ready, a human reviews and approves it. That approval activates the brand and unlocks all subsequent autonomous action. From then on Adjutant creates new ads, launches new campaigns, refreshes fatigued creative, reallocates budget between channels—all without asking permission—as long as every action satisfies the guardrails.

Guardrails replace approval as the mechanism for safety. Approvals do not scale; a human clicking approve on the eleventh budget shift of the week is rubber-stamping, not exercising judgment.

### 3.2 The Nine Guardrails

Every brand has one `guardrail` record. Before any autonomous action that spends money or creates a channel object, the system checks the action against these hard limits. **Violation results in rejection and escalation, never execution.**

| Guardrail | Default | Meaning | Enforcement |
|:---|:---|:---|:---|
| `monthly_spend_cap_usd` | Set at activation | Hard ceiling across all channels. Adjutant cannot exceed it, ever. | Database CHECK; sum of daily spend capped; no override path |
| `daily_spend_cap_usd` | `monthly ÷ 28` | Rolling daily ceiling across all channels. | Computed per-tick; enforced before reallocation |
| `max_daily_spend_increase_pct` | 25% | Largest single-day increase in total spend Adjutant may execute on its own. Larger increases escalate. | Compares trailing 7-day avg to candidate new spend; clamps if needed, logs clamping |
| `max_new_campaigns_per_day` | 3 | Rate limit on campaign creation. | Counts created campaigns in last 24h; rejects if limit reached |
| `max_new_ads_per_day` | 20 | Rate limit on ad creation. | Counts created ads in last 24h; rejects if limit reached |
| `per_channel_cap_pct` | 60% | No single channel may take more than this share of monthly budget without escalation. | Validated before budget reallocation; escalation if breached |
| `min_channel_floor_pct` | 0% | Optional. Keeps a channel from being starved to zero by the optimizer. | Enforced during reallocation; floor takes precedence over efficacy. |
| `blocked_claims[]` | Empty | Phrases the system may never put in an ad. Checked before render, never after. | Substring match (case-insensitive) in all copy fields before image render |
| `requires_approval_above_usd` | null | Optional. If set, any single budget change above this amount escalates instead of executing. | Checked during decision; escalation raised if amount exceeded |

### 3.3 Escalation Triggers

Autonomy is not abdication. These conditions halt autonomous action on the affected scope and raise an escalation to a human. The rest of the account keeps running.

1. **Spend Acceleration**: Spend in any 24-hour window exceeds 1.5× the trailing 7-day daily average.
2. **CPA Degradation**: Blended CPA degrades more than 40% week over week at meaningful volume (≥500 conversions).
3. **Policy Rejection**: A channel rejects creative for a policy reason. The verbatim rejection text goes to the human; the system does not guess and retry.
4. **Guardrail Breach**: A guardrail would be breached by the action the optimizer wants to take.
5. **New Channel**: A new channel connection is added—first launch on a new channel re-triggers the approval gate for that channel only.
6. **Compliance Flag**: A legal or compliance flag fires on generated content (e.g., health claim without substantiation).
7. **Authorization Failure**: Any channel API returns an authorization failure (token revoked, permission lost).

### 3.4 The Kill Switch

One control, present on every screen: **Pause everything**. It pauses every active object across every connected channel for that brand, in parallel, and returns a report of what was paused and why. It takes ≤60 seconds. Every paused object is verified remotely before reporting success.

Reversibility is a property of every autonomous action. Each action row stores a `revert_path`—the exact call sequence that undoes it—written at the same time as the action, not reconstructed later. Replaying a revert path restores the prior state exactly.

---

## 4. The Running Loop

Adjutant is a loop per brand, not a request-response app. The loop runs continuously once a brand is activated.

```
              ┌──────────────────────────────┐
              │         UNDERSTAND           │
              │  (once; re-run monthly)      │
              └───────────┬──────────────────┘
                          │
URL or prompt → Brand graph extraction → Offer/audience/voice/proof
                          │
              ┌───────────▼──────────────────┐
              │           PLAN               │
              │ (at activation; on strategy  │
              │  condition change)           │
              └───────────┬──────────────────┘
                          │
     Channels/objectives/budget split/creative concepts
                          │
              ┌───────────▼──────────────────┐
              │          CREATE              │
              │   (continuously, on demand)  │
              └───────────┬──────────────────┘
                          │
Copy (5 channels) + background image (Gemini) + 4 aspect ratios
                          │
              ┌───────────▼──────────────────┐
              │         LAUNCH               │
              │      (on approval)           │
              └───────────┬──────────────────┘
                          │
Campaign/adset/ad built & set live per channel
                          │
              ┌───────────▼──────────────────┐
              │         MEASURE              │
              │    (hourly; forever)         │
              └───────────┬──────────────────┘
                          │
Raw metrics pulled from 5 channels; normalized with comparability class
                          │
              ┌───────────▼──────────────────┐
              │        DIAGNOSE              │
              │   (hourly; after measure)    │
              └───────────┬──────────────────┘
                          │
Detect fatigue (3+ signals), winners, anomalies, channel efficiency shifts
                          │
              ┌───────────▼──────────────────┐
              │         DECIDE               │
              │ (hourly; continuously)       │
              └───────────┬──────────────────┘
                          │
Produce candidate actions: refresh, scale, cut, reallocate. Check guardrails.
Escalate breaches. Execute survivors.
                          │
              ┌───────────▼──────────────────┐
              │    EXECUTE & LOOP BACK       │
              └───────────┬──────────────────┘
                          │
Write action ledger with revert path; re-run MEASURE next hour
                          │
                    ┌─────┴─────┐
                    │ Kill switch│
                    │ pauses all │
                    └───────────┘
```

### 4.1 Loop Tick (Atomic Durable Unit)

One tick for one brand, executed as a durable workflow that survives process restarts:

1. **Acquire Advisory Lock** (`pg_try_advisory_xact_lock(hashtext('runner:' || brand_id))`) — Skip if lock held by previous tick.
2. **Metric Ingestion** — Pull metrics from every connected channel since the last watermark.
3. **Normalization** — Attach comparability class (attribution window, conversion event, view-through policy) to every fact.
4. **Diagnosis** — Emit findings: fatigue (if 3+ signals fire), winners, anomalies.
5. **Decision Generation** — Turn findings into candidate decisions (refresh, scale, reallocate).
6. **Guardrail Evaluation** — Check every candidate against all nine guardrails. Reject or escalate failures.
7. **Action Execution** — Execute survivors. Write one `action` row per mutation with revert path and decision rationale.
8. **Escalation Routing** — Send violations to human review queue.

**Idempotency:** A tick that crashes halfway and re-runs must not double-execute an action. Every channel-mutating call carries a deterministic idempotency key: `SHA256(brand_id || decision_id || channel || operation)`. The database enforces `UNIQUE (brand_id, idempotency_key)` on all mutation records.

---

## 5. Fatigue, Winners, and Budget Decisions

### 5.1 Fatigue Detection

An ad is fatigued only when **at least three** of the following fire simultaneously. Single-signal fatigue produces constant false positives.

- **Frequency** above 3.0 within the attribution window.
- **CTR** declining 15% or more week over week.
- **CPA** rising 20% or more week over week.
- **Impressions** declining while bid and budget are unchanged.
- **Time Since First Serve** exceeds the channel's typical creative half-life (7–14 days for short-form placements; configurable per channel).

The detection code enforces this rule; the schema of the `finding` table itself requires an array of **at least three signals** to be valid. A finding with fewer than three signals is rejected at the application layer and logged as a near-miss.

### 5.2 Winner Detection

An ad is a winner when it has cleared a minimum conversion volume threshold (not a spend threshold, which rewards expensive failures) *and* its CPA sits below the campaign average by a significant margin (typically 20–30%, configurable per brand).

Winners are scaled: budget is increased up to `max_daily_spend_increase_pct` or until reaching the daily/monthly cap or the per-channel cap.

### 5.3 Budget Reallocation

Reallocation moves money between channels and campaigns based on trailing efficiency (CPA, ROAS), subject to:
- The per-channel cap (no channel takes >60% of budget without escalation).
- The channel floor (if set, minimum spend per channel).
- A minimum 72-hour wait between reallocations on the same object (to avoid thrashing).
- Zero movement of budget between campaigns with mismatched comparability classes (different attribution windows prevent comparison).

---

## 6. Channel Parity: Five Adapters, One Interface

### 6.1 The Rule

**No channel outranks another.** This is enforced structurally, not by discipline.

No code above the adapter layer may branch on channel identity. No `if channel == "meta":` anywhere except in `src/adjutant/adapters/`. Everything channel-specific lives behind the adapter interface or in a declarative capability registry. This is enforced by CI lint rules; every violation fails the build.

### 6.2 The Adapter Interface

Every channel adapter implements exactly this interface. Nothing more is visible to the rest of the system.

```python
class ChannelAdapter(Protocol):
    """Unified interface all channel adapters must implement."""

    def describe_capabilities(self) -> CapabilitySet:
        """Returns supported objectives, formats, aspect ratios, character limits, targeting dimensions, quota model."""

    async def connect(self, credentials: ChannelCredentials) -> list[AdAccount]:
        """OAuth or token exchange. Stores encrypted. Returns accessible ad accounts."""

    def preflight(self, plan: CampaignPlan) -> list[Violation]:
        """Validate a plan against capabilities. Returns specific violations, not a boolean."""

    async def build(self, plan: CampaignPlan, idem_key: str) -> RemoteHierarchy:
        """Creates campaign, ad set, and ad objects in paused state. Idempotent."""

    async def launch(self, objects: RemoteHierarchy, idem_key: str) -> None:
        """Sets objects live. Idempotent."""

    async def mutate(self, obj: RemoteObject, change: Change, idem_key: str) -> None:
        """Budget, status, or targeting change. Idempotent."""

    async def pause(self, objects: list[RemoteObject]) -> list[PauseResult]:
        """Used by the kill switch. Must be fast and must report per-object success."""

    async def fetch_metrics(self, since: datetime) -> list[RawMetric]:
        """Returns raw metrics with native attribution settings attached."""

    async def fetch_status(self, objects: list[RemoteObject]) -> list[StatusReport]:
        """Returns approval and delivery status, including verbatim rejection text on disapproval."""
```

### 6.3 The Five Adapters (S0–S6 Requirement)

| Adapter | API | Object Hierarchy | Status | Implementation Priority |
|:---|:---|:---|:---|:---|
| **Meta** | Graph API v21.0 | Campaign → Adset → Creative → Ad | ✅ Partial | Complete & verify preflight, idempotency, pause/read-back |
| **Google Ads** | REST v15 | Campaign → Ad Group → Responsive Search Ad | ⚠️ Partial | Complete preflight, build, launch, preflight validators |
| **YouTube** | Ads API + Video | Campaign → Ad Group → Video Responsive Ad | ⚠️ Partial | Share Google Ads OAuth; complete video-specific builders |
| **LinkedIn** | Marketing API v2024 | Campaign Group → Campaign → Single Image Creative | ⚠️ Partial | Complete campaign/creative builders; preflight for char limits |
| **Reddit** | Ads API v3 | Campaign → Adgroup → Promoted Post Ad | ⚠️ Partial | Complete campaign/adgroup/ad builders; validate preflight |

### 6.4 Conformance Suite (S9 Blocker)

Before the second adapter is written, a conformance suite must exist and the first adapter must pass it. Before the remaining three adapters are written, all existing adapters must pass the suite with zero waivers. The suite tests:

- Capability accuracy (does declared match actual?)
- Idempotency under induced timeout (no duplicate objects on retry?)
- Verification catching a silent failure (does `fetch_status()` catch a failed build?)
- Verbatim rejection capture (does platform rejection text reach the human?)
- Quota rate limit enforcement (are rate limits discovered and respected before probing production?)

---

## 7. API & Interface Specifications

### 7.1 First-Launch Approval Gate (S5)

#### `POST /api/brands/{brand_id}/plans/{plan_id}/approve`

**Purpose**: Issue a cryptographically signed, cap-bound, time-bound approval token.

**Headers:**
```
Content-Type: application/json
X-Adjutant-Client: console
```

**Request:**
```json
{
  "request_key": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "expected_plan_hash": "a1b2c3d4e5f67890123456789abcdef0123456789abcdef0123456789abcdef0",
  "max_spend_authorized_usd": "2500.00",
  "allowed_channels": ["meta", "google_ads", "linkedin"]
}
```

**Success (200 OK):**
```json
{
  "token_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
  "brand_id": "11111111-2222-3333-4444-555555555555",
  "plan_id": "22222222-3333-4444-5555-666666666666",
  "signed_token": "eyJhbGciOiJFZERTQSIsInR5cCI6IkpXVCJ9...",
  "expires_at": "2026-10-02T22:00:00Z",
  "spend_cap_usd": "2500.00"
}
```

**Errors:**
- `400 Bad Request`: Plan hash mismatch or invalid channel in scope.
- `403 Forbidden`: Authenticated user lacks `owner` or `admin` role.
- `409 Conflict`: Plan has been mutated since approval was loaded.
- `422 Unprocessable Entity`: Monthly spend cap in guardrails is lower than requested launch budget.

**Invariants:**
- Mutating one byte of the plan after approval invalidates the token.
- Replaying a consumed token is rejected and raises a security alert, not a warning.
- A token cannot authorize spend above its cap, a channel outside its scope, or an operation outside its list.
- Editing an approved plan voids every outstanding token atomically.
- With the token store unavailable, validation denies rather than permits, and alerts.

### 7.2 Global Kill Switch

#### `POST /api/brands/{brand_id}/kill`

**Purpose**: Pause every active object across all connected channels in parallel within 60 seconds.

**Headers:**
```
Content-Type: application/json
X-Adjutant-Client: console
```

**Request:**
```json
{
  "reason": "Emergency stop: spend irregularity observed"
}
```

**Success (200 OK):**
```json
{
  "brand_id": "11111111-2222-3333-4444-555555555555",
  "stopped_at": "2026-10-01T22:15:00Z",
  "channels_dispatched": ["meta", "google_ads", "youtube", "linkedin", "reddit"],
  "total_objects_paused": 14,
  "results": [
    {
      "channel": "meta",
      "object_id": "2384729182371",
      "level": "campaign",
      "status": "verified_paused",
      "readback_state": "PAUSED"
    },
    {
      "channel": "linkedin",
      "object_id": "urn:li:sponsoredCampaign:50493821",
      "level": "campaign",
      "status": "verified_paused",
      "readback_state": "PAUSED"
    }
  ],
  "unresponsive_channels": []
}
```

**Errors:**
- `504 Gateway Timeout`: A channel did not respond within 60 seconds; returns partial results.

**Invariants:**
- Every paused object is verified via `fetch_status()` before reporting success.
- Per-object results are returned by name.
- Kill switch is present on every UI screen.

### 7.3 Manual Autonomous Tick Trigger (Testing)

#### `POST /api/brands/{brand_id}/runner/tick`

**Purpose**: Manually trigger one hourly loop tick (for testing and verification).

**Headers:**
```
Content-Type: application/json
X-Adjutant-Client: console
```

**Request:**
```json
{
  "request_key": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d"
}
```

**Success (200 OK):**
```json
{
  "tick_id": "e4eaaaf2-d142-11e1-b3e4-080027620cdd",
  "status": "completed",
  "findings_detected": 2,
  "decisions_evaluated": 2,
  "actions_executed": 1,
  "escalations_raised": 0,
  "elapsed_ms": 842
}
```

---

## 8. Data Model Refinements

### 8.1 Strict Channel Definition

```python
from typing import Literal

Channel = Literal[
    "meta",
    "google_ads",
    "youtube",
    "linkedin",
    "reddit",
]
```

This type is used everywhere a channel must be named. String literals outside `src/adjutant/adapters/` are caught by CI linting.

### 8.2 Studio Copy Schemas

All copy is validated to fit the character limits and structure of each platform:

```python
class MetaCopy(BaseModel):
    primary_text: str = Field(min_length=1, max_length=250)
    headline: str = Field(min_length=1, max_length=40)
    description: str = Field(default="", max_length=30)
    call_to_action: str = Field(min_length=1, max_length=30)

class GoogleAdsCopy(BaseModel):
    headlines: list[str] = Field(min_length=3, max_length=15)
    descriptions: list[str] = Field(min_length=2, max_length=4)
    final_url_suffix: str = Field(default="", max_length=100)

class YouTubeCopy(BaseModel):
    headline: str = Field(min_length=1, max_length=30)
    long_headline: str = Field(min_length=1, max_length=90)
    description: str = Field(min_length=1, max_length=90)

class LinkedInCopy(BaseModel):
    introductory_text: str = Field(min_length=1, max_length=600)
    headline: str = Field(min_length=1, max_length=70)
    landing_page_url: str = Field(min_length=1, max_length=2000)

class RedditCopy(BaseModel):
    post_title: str = Field(min_length=1, max_length=300)
    call_to_action: str = Field(min_length=1, max_length=30)

class CreativeCopyBundle(BaseModel):
    concept_id: str
    meta: MetaCopy
    google_ads: GoogleAdsCopy
    youtube: YouTubeCopy
    linkedin: LinkedInCopy
    reddit: RedditCopy
```

### 8.3 Forward Migration (048)

```sql
-- Migration 048: Purge decommissioned channels and restrict to 5 active platforms.
BEGIN;

-- 1. Remove orphaned capabilities and specs for decommissioned channels
DELETE FROM placement_spec 
WHERE channel IN ('tiktok', 'snapchat', 'pinterest', 'microsoft', 'amazon_ads');

DELETE FROM channel_capability 
WHERE channel IN ('tiktok', 'snapchat', 'pinterest', 'microsoft', 'amazon_ads');

-- 2. Create refined channel enum and migrate existing columns
CREATE TYPE channel_v2 AS ENUM (
    'meta',
    'google_ads',
    'youtube',
    'linkedin',
    'reddit'
);

-- Update all channel columns to the new type
ALTER TABLE channel_capability 
    ALTER COLUMN channel TYPE channel_v2 USING (channel::text::channel_v2);

ALTER TABLE placement_spec 
    ALTER COLUMN channel TYPE channel_v2 USING (channel::text::channel_v2);

ALTER TABLE channel_connection 
    ALTER COLUMN channel TYPE channel_v2 USING (channel::text::channel_v2);

ALTER TABLE campaign_build 
    ALTER COLUMN channel TYPE channel_v2 USING (channel::text::channel_v2);

ALTER TABLE campaign_object 
    ALTER COLUMN channel TYPE channel_v2 USING (channel::text::channel_v2);

ALTER TABLE action 
    ALTER COLUMN channel TYPE channel_v2 USING (channel::text::channel_v2);

ALTER TABLE metric_fact 
    ALTER COLUMN channel TYPE channel_v2 USING (channel::text::channel_v2);

ALTER TABLE autonomous_decision 
    ALTER COLUMN channel TYPE channel_v2 USING (channel::text::channel_v2);

-- 3. Replace old channel enum type
DROP TYPE channel;
ALTER TYPE channel_v2 RENAME TO channel;

-- 4. Update check constraints on guardrail table if channel arrays exist
ALTER TABLE guardrail DROP CONSTRAINT IF EXISTS guardrail_channel_shares_valid;
ALTER TABLE guardrail ADD CONSTRAINT guardrail_channel_shares_valid CHECK (
    channel_shares IS NULL OR (
        SELECT bool_and(key IN ('meta', 'google_ads', 'youtube', 'linkedin', 'reddit'))
        FROM jsonb_each_text(channel_shares)
    )
);

COMMIT;
```

---

## 9. Error Handling & Failure Recovery

| Scenario | Detection | Recovery | User Impact | Logging |
|:---|:---|:---|:---|:---|
| **Missing Ad Account Credentials** | Preflight checks decrypted token presence | Fail-fast; HTTP 428 (`ExternalAuthRequired`). | User cannot launch until channel is connected. | Structured log entry with brand_id, channel, reason |
| **Provider API Timeout** | HTTP timeout (5s connect, 15s read) | Exponential jittered retry (max 3 attempts). Reconcile by idempotency key before re-executing. | If all retries fail, escalation raised; autonomous tick skipped for that channel. | Structured log: timeout details, attempt count, idem_key |
| **Policy Rejection (Disapproval)** | `fetch_status()` detects `REJECTED` or `DISAPPROVED` | Route verbatim platform rejection to escalation. Do NOT retry or guess. | Human reviews rejection; decides whether to edit & re-submit or kill campaign. | Structured log: full rejection text, timestamp, channel |
| **Creative Refresh Generation Timeout** | Gemini/Ollama image or copy times out (30s default) | Keep fatigued ad live; raise scoped escalation; retry next tick. | Zero dark time; original ad continues serving. | Structured log: generation attempt, timeout, original ad remains live |
| **Concurrent Tick Contention** | `pg_try_advisory_xact_lock()` fails | Skip tick; previous tick owns loop. | None; tick safely deduplicated. | Debug log: lock contention, next tick scheduled for 1 hour |
| **Spend Cap Breach Attempt** | Guardrail evaluator compares daily/monthly ceilings | Reject candidate decision; log rejection; raise escalation if significant. | Optimizer claims halted; rest of account continues. | Structured log: which guardrail, why breach, next allowed action time |
| **Token Replay / Tampering** | Signature validation fails or `consumed_at IS NOT NULL` | Reject immediately; insert security alert to outbox. | HTTP 403; execution permanently blocked. | Structured alert log: tampering attempt, token_id, timestamp, IP |
| **Comparability Class Mismatch** | Budget reallocation checks attribution window match | Refuse movement; retain existing allocation. | Optimization delayed until alignment possible. | Structured log: source/dest comparability classes, reason for refusal |

---

## 10. Acceptance Criteria for Production Launch

### 10.1 Automated Verification

- **Migration 048**: Applies cleanly to both test and production databases; all existing channels map to the 5 active ones; no data loss.
- **Zero Stubs**: Grep scan confirms zero occurrences of `TODO`, `FIXME`, `pass`, or placeholder logic in application code.
- **Channel Parity Lint**: CI rule confirms zero channel-name string comparisons outside `src/adjutant/adapters/`.
- **Decommissioned Channel Scour**: Grep scan confirms zero active references to `tiktok`, `snapchat`, `pinterest`, `microsoft`, `amazon_ads` in live code.
- **Preflight & Builder Conformance**: All 5 channel builders pass preflight validation, object hierarchy creation, and status verification. Conformance suite runs on every commit.
- **S5 Security Suite**:
  - Plan tampering invalidates unconsumed token.
  - Replay of consumed token fails with security alert.
  - Token cap overflow is denied.
  - Token hash mismatch is rejected.
- **S8 Autonomous Loop Verification**:
  - Fatigued creative (3+ signals) triggers replacement generation and launch before original is paused. Zero dark time verified.
  - Failed replacement generation keeps original ad live and raises human escalation.
  - Winner is scaled up to `max_daily_spend_increase_pct`.
  - Incompatible comparability classes block budget reallocation.
  - Autonomous daemon runs with advisory locks and graceful shutdown.
- **Kill Switch Benchmark**: Pauses active campaigns across all 5 channels in parallel within 60 seconds. Per-object verification reported by name.
- **Typecheck & Lint**:
  - Backend: `ruff check src scripts` and `ruff format --check src scripts` pass with zero errors.
  - Frontend: `npm run typecheck` and `npm run build` in `web/` pass with zero errors.

### 10.2 Live Verification (7-Day Unattended Run)

- One brand running autonomously on real ad accounts (or sandbox with realistic API throttling) for seven consecutive days with zero human touches.
- Zero unauthorized spend events.
- Zero escalations due to corruption, duplicate execution, or non-idempotency.
- At least one refresh cycle completed with zero dark time.
- At least one budget reallocation executed within guardrails.
- All metrics ingested, normalized, and stored without duplicates.
- Kill switch invoked mid-run; all objects paused and verified within 60 seconds.
- Process interrupted mid-tick; tick resumed on restart without double-execution.

### 10.3 Security & Tenancy Verification

- A pooled connection checked out without tenant context reads zero rows from every brand-scoped table.
- A new brand-scoped table added without an RLS policy fails CI immediately.
- Tenant isolation suite proves brand A's authenticated session cannot read, update, or delete any row belonging to brand B, across every table.
- RLS tests run as a non-superuser role (superusers bypass RLS).
- Every role in the access control matrix is enforced at the API boundary with a test per role per endpoint.
- Approval tokens are cryptographically signed; signatures cannot be forged or replayed.
- Secret redaction in structured logs strips access tokens, client secrets, Authorization headers.

---

## 11. Operational Runbooks

### 11.1 Local Bootstrap & Start

```bash
# Clone the repo
git clone https://github.com/moonrox420/adjutant.git
cd adjutant

# One command: bootstrap, migrate, start HTTP service, start background runner
make up

# On Windows without Make:
python scripts/up.py

# Console at http://127.0.0.1:3000
# API docs at http://127.0.0.1:8010/docs
# Owner: owner@adjutant.local
# Password: cat .local/runner/owner.password
```

### 11.2 Forward Migrations

```bash
python scripts/upgrade.py
# Then restart HTTP service and runner daemon
```

### 11.3 Running Tests

```bash
# Backend foundation tests (S0–S6)
python -m pytest -q --basetemp=.local/pytest-v2

# Lint & format check
python -m ruff check src scripts
python -m ruff format --check src scripts

# Frontend
cd web && npm run typecheck && npm run build
```

### 11.4 Monitoring the Autonomous Loop

```bash
# Tail HTTP service logs
tail -f .local/runner/server.log

# Tail runner daemon logs
tail -f .local/runner/runner.log

# Query active brands and their next tick time
psql postgresql://adjutant_app:PASSWORD@127.0.0.1:55440/adjutant \
  -c "SELECT brand_id, status, activated_at, 
           MAX(tick_id) as last_tick,
           MAX(ticked_at) as last_tick_time
        FROM brand
        LEFT JOIN autonomous_loop_tick USING (brand_id)
        WHERE status = 'active'
        GROUP BY brand_id, status, activated_at"

# Inspect last tick findings & decisions
psql postgresql://adjutant_app:PASSWORD@127.0.0.1:55440/adjutant \
  -c "SELECT finding_id, kind, signals, created_at 
        FROM finding 
        WHERE brand_id = 'YOUR_BRAND_ID'
        ORDER BY created_at DESC LIMIT 10"
```

### 11.5 Emergency Kill Switch

```bash
curl -X POST http://127.0.0.1:8010/api/brands/YOUR_BRAND_ID/kill \
  -H "Content-Type: application/json" \
  -H "Cookie: session=YOUR_SESSION_COOKIE" \
  -d '{"reason": "Emergency stop"}'
```

---

## 12. Out-of-Scope for This Release

- **S11 Video Timeline Synthesizer**: Audio mixing, avatar lip-sync, complex ffmpeg rendering.
- **S12 Full Agency Platform**: Multi-client white-label billing, agency seat marketplace.
- **S13 Cross-Jurisdictional AI Legal Compliance**: EU Article 50 automated filing, health claim substantiation.
- **S14 Self-Serve Stripe Billing**: Automated dunning, subscription management.
- **Platforms outside the 5**: TikTok, Snapchat, Pinterest, Microsoft Advertising, Amazon Ads, Twitter/X, DSPs.

---

## 13. Success Criteria (Measurable)

| Measure | Target | How Verified |
|:---|:---|:---|
| Time from signup to first live ad | ≤ 4 hours, self-serve | Manual walkthrough; UI/UX timing instrumented |
| Human touches per brand per month, after activation | ≤ 2 | Audit log review; escalation count dashboard |
| Autonomous actions executed without escalation | ≥ 90% | Count executed vs. escalated decisions per brand per month |
| Brands where CPA improves within 60 days | ≥ 50% | Metric fact history compared to baseline week |
| Creative refresh cycles completed with zero dark time | 100% | Audit log: paused timestamp > launched timestamp for replacement |
| Unauthorized spend events | 0, permanently | Kill switch audit log; spend cap breach log; reconciliation |
| Channels shipping with a conformance waiver | 0 | Conformance suite pass/fail per channel in CI |

---

## 14. Conclusion

Adjutant v2.0 is an autonomous ad runner that becomes more valuable every day as it learns brand performance, detects winners and fatigue, and reallocates budget without human intervention. It is designed for a century of safe, predictable, auditable operation within guardrails set once at activation.

This PRD specifies the complete system needed for confident production launch. Every requirement is testable, every API is fully specified, and every failure mode has a documented recovery path. The five adapters implement one interface; channel parity is enforced by the compiler and the CI pipeline. Approval is required once; guardrails replace approval as the mechanism for safety thereafter.

All code, all state, all mutations are immutable, auditable, and reversible. Every autonomous action carries its own undo path. The kill switch works in under 60 seconds across all platforms. Process death does not cause data loss or duplicate work. Tenancy is enforced at the database level, not the application layer.

This is a production system. Build it, verify it, launch it, and let it run.