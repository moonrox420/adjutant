# Adjutant — Ad Runner: Product Spec and Build Plan

**One file. Everything needed to build the system.**
Author: Dustin Hill · Version 2.0 · September 21, 2026
Supersedes: the v1.0 PRD, architecture, and roadmap documents.

---

## 0. How to Use This File

This is the single source of truth. It contains what Adjutant is (§1–§7) and how to build it (§8). There is no companion document to fetch.

**If you are an AI agent building this system, read this:**

1. Build in slice order. Slices are numbered `S0` through `S14`. A slice may only start when every slice in its **Depends on** line is complete.
2. A slice is complete when every numbered check under **Done when** passes as an automated test committed to the repo. Not when the code looks right. Not when it runs once by hand. When the test passes in CI.
3. **Do not** lines are scope fences. They exist because the listed work is either premature or actively harmful at that point. Respect them.
4. Every slice is vertical. Each one ends with the system doing something observably more than it did before. There is no slice whose only output is infrastructure nobody uses yet.
5. Where this file specifies an invariant — a spend cap, an idempotency rule, a tenancy boundary — enforce it in the database, not only in application code. Application code gets bypassed.

There are no weeks, no story points, no team assignments in this document. Slices are ordered by dependency, not by calendar.

---

## 1. What Adjutant Is

Adjutant is an **ad runner**. A business connects its website and its ad accounts. Adjutant figures out what the business sells and to whom, produces finished ads, puts them live, watches how they perform, and then keeps working: refreshing creative that is wearing out, shifting budget toward what is converting, and killing what is not. It does this continuously, on its own, across every major paid channel.

The product is not a generator with an export button. Generation is one step inside a loop that never stops running.

**The distinction that defines the product:** a tool produces ads when asked. A runner owns the account. Adjutant owns the account.

### The user's day

A business owner connects their site and their Meta and Google accounts on a Tuesday. Adjutant reads the business, builds a campaign plan with finished creative, and presents it. The owner reviews it once and approves. From that moment the owner's involvement is exception-based: they get a weekly result summary, and they get pulled in only when something requires a decision a machine should not make alone. Three weeks later, four of the original six ads have been retired and replaced, budget has moved twice between channels, and nobody asked them to click anything.

---

## 2. The Autonomy Model

This is the most important section in the document. Get it wrong and the product is an assistant.

### The rule

**Approval is required once per brand, at first launch. After that, Adjutant runs autonomously within guardrails.**

When a brand's first campaign plan is ready, a human reviews and approves it. That approval activates the brand. From then on Adjutant creates new ads, launches new campaigns, refreshes fatigued creative, adjusts budgets, and pauses losers without asking — as long as it stays inside the brand's guardrails.

Approvals do not scale and they do not make a system safe. A human clicking approve on the eleventh budget shift of the week is not exercising judgment; they are rubber-stamping. **Guardrails replace approvals.** Guardrails are checked by the system on every action, every time, and cannot be bypassed by a tired person at 11pm.

### Guardrails

Every brand has a guardrail record. Every action that spends money or creates a channel object is checked against it before execution and rejected if it fails. These are hard limits enforced at the database level.

| Guardrail | Default | Meaning |
|---|---|---|
| `monthly_spend_cap_usd` | set at activation, required | Hard ceiling across all channels. Adjutant cannot exceed it, ever, for any reason. |
| `daily_spend_cap_usd` | monthly ÷ 28 | Rolling daily ceiling across all channels. |
| `max_daily_spend_increase_pct` | 25% | Largest single-day increase in total spend Adjutant may make on its own. |
| `max_new_campaigns_per_day` | 3 | Rate limit on campaign creation. |
| `max_new_ads_per_day` | 20 | Rate limit on ad creation. |
| `per_channel_cap_pct` | 60% | No single channel may take more than this share of monthly budget without escalation. |
| `min_channel_floor_pct` | 0% | Optional. Keeps a channel from being starved to zero by the optimizer. |
| `blocked_claims` | brand-supplied list | Phrases the system may never put in an ad. Checked before render, not after. |
| `requires_approval_above_usd` | null | Optional. If set, any single budget change above this amount escalates instead of executing. |

### Escalation triggers

Autonomy is not abdication. These conditions stop autonomous action on the affected scope and raise an escalation to a human. The rest of the account keeps running.

1. Spend in any 24-hour window exceeds 1.5× the trailing 7-day daily average.
2. Blended CPA degrades more than 40% week over week at meaningful volume.
3. A channel rejects creative for a policy reason (the verbatim rejection text goes to the human; the system does not guess and retry).
4. A guardrail would be breached by the action the optimizer wants to take.
5. A new channel connection is added — first launch on a new channel re-triggers the approval gate for that channel only.
6. A legal or compliance flag fires on generated content.
7. Any channel API returns an authorization failure, which usually means a token or permission broke.

### The kill switch

One control, present on every screen: **Pause everything**. It pauses every active object across every connected channel for that brand, in parallel, and returns a report of what was paused and what failed to pause. It must complete in under 60 seconds or tell the user precisely which channel did not respond. A kill switch that might have worked is not a kill switch.

Reversibility is a property of every autonomous action. Each action row stores a `revert_path` — the exact call sequence that undoes it — written at the same time as the action, not reconstructed later.

---

## 3. The Running Loop

Adjutant is a loop per brand, not a request-response app. The loop runs continuously once a brand is activated.

```
                    ┌──────────────────────────────────────┐
                    │                                      │
  brand URL  →  UNDERSTAND  →  PLAN  →  CREATE  →  LAUNCH ─┤
                    ↑                                      │
                    │                                      ↓
                 DECIDE  ←────  DIAGNOSE  ←────────────  MEASURE
```

| Stage | What happens | Cadence |
|---|---|---|
| **Understand** | Read the site, extract offers, audience, voice, proof, constraints. Build the brand graph. | Once at onboarding; re-run monthly and on site change |
| **Plan** | Choose channels, objectives, budget split, campaign structure, and creative concepts. | At activation; re-planned when strategy-level conditions change |
| **Create** | Generate copy and creative for every required format on every target channel. | Continuously, driven by refresh demand |
| **Launch** | Build the objects on each channel and set them live. | On demand |
| **Measure** | Pull metrics from every channel, normalize, store. | Hourly |
| **Diagnose** | Detect fatigue, winners, losers, anomalies, and channel-level efficiency shifts. | Hourly, after measurement |
| **Decide** | Produce and execute actions: refresh, scale, cut, reallocate. Check guardrails first. | Hourly for tactical, daily for budget, weekly for strategy |

### Loop tick

A single tick for one brand, executed as a durable workflow that survives process restarts:

1. Pull metrics from every connected channel since the last watermark.
2. Normalize into a common fact table with a comparability class attached (see §5).
3. Run diagnosis. Emit findings.
4. Turn findings into candidate decisions.
5. Check every candidate against guardrails. Reject or escalate the ones that fail.
6. Execute survivors. Write an action row with a revert path for each.
7. If a refresh decision fired, enqueue creative generation so replacements exist before the fatigued ad is cut.

The loop is idempotent. A tick that crashes halfway and re-runs must not double-execute an action. Every channel-mutating call carries an idempotency key derived from `(brand_id, decision_id, channel, operation)`.

---

## 4. Fatigue, Winners, and Budget

The decision logic is the product's actual intelligence. Specifying it loosely produces a system that thrashes.

### Fatigue

An ad is fatigued only when **at least three** of the following fire simultaneously. Single-signal fatigue detection produces constant false positives and a system that replaces working ads.

- Frequency above 3.0 within the attribution window.
- CTR declining 15% or more week over week.
- CPA rising 20% or more week over week.
- Impressions declining while bid and budget are unchanged.
- Time since first serve exceeds the channel's typical creative half-life (7–14 days for short-form video placements at moderate spend; configurable per channel).

Requiring three signals is a hard rule, enforced in the detection code and in the schema of the fatigue finding itself, which must carry an array of at least three signals to be valid.

### Winners

An ad is a winner when it has cleared a minimum conversion volume threshold — not a minimum spend threshold, which rewards expensive failures — and its CPA sits below the campaign average by a statistically meaningful margin. Scale winners by no more than the `max_daily_spend_increase_pct` guardrail per day. Doubling a winner's budget overnight resets the channel's learning phase and destroys the thing being scaled.

### Budget reallocation

Reallocation moves money between channels and campaigns based on trailing efficiency, subject to: the per-channel cap, the channel floor, a minimum 72 hours between reallocations on the same object, and a requirement that the source and destination metrics share a comparability class. Moving budget from a channel measuring 7-day click conversions to one measuring 1-day view conversions, on the basis of their reported CPAs, is a category error the system must refuse to make.

---

## 5. Channel Parity

**No channel outranks another.** This is a product commitment and it is enforced structurally, not by discipline.

### The rule

No code above the adapter layer may branch on channel identity. No `if channel == "meta"`. Everything channel-specific lives behind the adapter interface or in the capability registry. This is enforced by a CI lint rule that fails the build on channel-name string comparisons outside `adapters/`.

### V1 channels

Meta, Google, YouTube, TikTok, LinkedIn, Microsoft, Reddit, Pinterest, Snapchat, Amazon Ads. All ten reach the same definition of done. No channel ships with a conformance waiver.

### The adapter interface

Every channel adapter implements exactly this. Nothing more is visible to the rest of the system.

| Method | Contract |
|---|---|
| `describe_capabilities()` | Returns supported objectives, formats, aspect ratios, character limits, targeting dimensions, and the quota model. Declarative data, not code. |
| `connect(credentials)` | OAuth or token exchange. Stores encrypted. Returns the accessible ad accounts. |
| `preflight(plan)` | Validates a plan against capabilities before anything is built. Returns specific violations, not a boolean. |
| `build(plan, idem_key)` | Creates campaign, ad set, and ad objects in paused state. Idempotent. |
| `launch(objects, idem_key)` | Sets objects live. Idempotent. |
| `mutate(object, change, idem_key)` | Budget, status, or targeting change. Idempotent. |
| `pause(objects)` | Used by the kill switch. Must be fast and must report per-object success. |
| `fetch_metrics(since)` | Returns raw metrics with the channel's native attribution settings attached. |
| `fetch_status(objects)` | Returns approval and delivery status, including verbatim rejection text on disapproval. |

### Comparability classes

Every metric fact carries the attribution window, conversion event definition, and view-through policy it was measured under. Facts from different comparability classes may be displayed side by side only with an explicit annotation, and may never be silently summed into a blended number. A cross-channel CPA with no methodology attached is a lie with a decimal point.

### Quota discipline

Each adapter declares its rate-limit model from the platform's published documentation, with a citation and a `verified_on` date in the registry entry. Discovering limits by probing production endpoints is prohibited; it gets applications banned. A test asserts the system never probes.

---

## 6. Data Model

Core entities. `brand_id` is the tenant key on every brand-scoped table, with row-level security forced on, so a query that forgets its tenant context returns zero rows rather than everything.

| Entity | Purpose | Key fields |
|---|---|---|
| `account` | Top-level customer. Type is `business` or `agency`. | `id`, `type`, `billing_status` |
| `brand` | One advertised business. An agency account has many. | `id`, `account_id`, `status`, `activated_at` |
| `brand_graph` | Structured understanding of the business. Versioned. | `brand_id`, `version`, `offers[]`, `audiences[]`, `voice`, `proof[]`, `constraints[]` |
| `guardrail` | The autonomy limits from §2. One per brand. | `brand_id`, all cap fields, `blocked_claims[]` |
| `channel_connection` | Encrypted credentials and the ad account reference. | `brand_id`, `channel`, `external_account_id`, `status` |
| `campaign_plan` | A strategy: channels, objectives, budget split, concepts. | `brand_id`, `version`, `status`, `total_budget_usd` |
| `creative` | A scene graph, never a flat image. Text is a text layer. | `brand_id`, `concept_id`, `scene_graph`, `status` |
| `rendition` | One creative rendered to one channel's spec. | `creative_id`, `channel`, `format`, `asset_uri`, `validated` |
| `deployment` | A plan pushed to channels. Links to channel objects. | `plan_id`, `channel`, `state`, `idem_key` |
| `channel_object` | Our record of a remote campaign, ad set, or ad. | `deployment_id`, `channel`, `external_id`, `level`, `status` |
| `metric_fact` | Normalized performance, with comparability class. | `brand_id`, `channel_object_id`, `date`, `metrics`, `comparability_class` |
| `finding` | A diagnosis: fatigue, winner, anomaly, inefficiency. | `brand_id`, `kind`, `signals[]`, `subject_ref` |
| `decision` | A proposed or executed action with its rationale. | `brand_id`, `finding_id`, `kind`, `params`, `state` |
| `action` | Append-only ledger. Every mutation, forever. | `brand_id`, `actor`, `diff`, `revert_path`, `decision_id` |
| `approval` | First-launch sign-off. Rare by design. | `brand_id`, `plan_id`, `approver`, `scope`, `expires_at` |
| `escalation` | A human decision request from a §2 trigger. | `brand_id`, `trigger`, `context`, `state` |

The `action` table rejects UPDATE and DELETE at the database level. It is the record of what the machine did with someone's money, and it is not editable.

Creative is stored as a **scene graph** — layers with typed content, positions, and constraints — not as pixels. Text is never rasterized into a background. This is what makes a single concept renderable into nine channels' aspect ratios and character limits without regenerating it, and what makes a headline swap cheap instead of a new image generation.

---

## 7. Account Types

Two front doors, equal prominence, one engine. The signup screen presents both with neither defaulted nor upsold.

**Business** — one brand, optionally a few. Self-serve. The owner is the approver.

**Agency** — many client brands, seats, roles, white-label reporting, cross-client rollups. The agency can optionally require client sign-off on a brand's first launch, which is the only place a second approval stage exists.

An account converts between types without data loss. Roles: owner, admin, media buyer, client approver, client viewer.

---

## 8. Build Plan

Fourteen slices, ordered by dependency. Each one leaves the system doing more than it did before.

---

### S0 — Skeleton that can run one thing

**Depends on:** nothing

**Build:** Monorepo. Postgres with migrations. A durable workflow runner for the loop (Temporal or equivalent — the requirement is that a workflow survives process death and resumes, not a specific vendor). One HTTP service. Object storage for assets. Secrets encrypted with a per-tenant key. Structured logging with a trace ID on every request. A CI pipeline that runs migrations and tests on every commit.

**Done when:**
1. `make up` from a clean checkout produces a working local environment with no manual steps.
2. A trivial workflow started, then killed mid-execution by terminating its worker, resumes and completes when the worker restarts.
3. CI runs migrations against an empty database and all tests pass.
4. A secret written through the credential helper is unreadable in the database and absent from logs, verified by a test that greps log output for the plaintext value.

**Do not:** build an admin UI, a service mesh, or more than one service.

---

### S1 — Tenancy that cannot leak

**Depends on:** S0

**Build:** `account` and `brand` tables. Row-level security with `FORCE ROW LEVEL SECURITY` on every brand-scoped table. Tenant context injected at connection checkout, not passed as a query parameter. Authentication with the role matrix from §7. A leak test suite that runs on every migration.

**Done when:**
1. A pooled connection checked out without tenant context reads zero rows from every brand-scoped table.
2. A new brand-scoped table added without an RLS policy fails CI.
3. The leak suite proves brand A's authenticated session cannot read, update, or delete any row belonging to brand B, across every table.
4. RLS tests run as a non-superuser role. Superusers bypass RLS, so a test run as the database owner is a false pass and must be explicitly prevented.
5. Every role in §7 is enforced at the API boundary with a test per role per endpoint.

**Do not:** proceed to any other slice until check 4 is genuinely satisfied. A tenancy leak discovered after launch is an extinction event for an agency product.

---

### S2 — Understand a business from a URL

**Depends on:** S1

**Build:** Brand ingest. Given a URL, crawl the site, extract offers, pricing, audience signals, tone, proof points, and existing visual identity. Produce a versioned `brand_graph`. Present it to the user for confirmation and editing.

**Done when:**
1. Ten real SMB sites across different verticals each produce a brand graph with at least one offer, one audience, and a voice description.
2. The graph is editable by the user and edits create a new version rather than mutating the old one.
3. An unconfirmed brand graph cannot be used for generation — enforced in code and covered by a test.
4. Re-running ingest on an unchanged site produces no spurious diff.

**Do not:** generate any creative yet.

---

### S3 — Make one ad

**Depends on:** S2

**Build:** Creative generation producing a scene graph: headline, body, call to action, background image, logo placement, and layout. A renderer that turns the scene graph into an image at a specified aspect ratio with text drawn as text. A validator that checks the rendition for overflow, clipping, contrast, and safe-area violations.

**Done when:**
1. One brand graph produces five distinct concepts, not five recolors of one concept.
2. A single scene graph renders correctly at 1:1, 4:5, 9:16, and 16:9 without text overflow or clipping at any ratio.
3. The validator catches a deliberately overflowing headline and blocks the rendition.
4. No `blocked_claims` phrase can appear in generated copy — verified by a test that sets a blocked phrase and confirms generation output never contains it.
5. Text is selectable-as-text in the scene graph representation at all times; a test asserts no code path rasterizes copy into the background layer.

**Do not:** build video. Do not build the approval UI.

---

### S4 — Put it live on one channel

**Depends on:** S3

**Build:** The adapter interface from §5, and the first adapter implemented against it. Capability registry. Preflight validation. Campaign, ad set, and ad construction in paused state. Idempotency keys on every mutating call.

**Done when:**
1. A plan builds a real campaign structure in a real sandbox ad account, in paused state.
2. A build interrupted by an induced timeout and retried produces exactly one set of objects, not two. Tested at every object level.
3. Preflight rejects a plan exceeding the channel's character limits with a specific violation message naming the field, not a generic failure.
4. The capability registry is data, and the adapter reads its limits from it rather than hardcoding them.
5. No channel-name string comparison exists outside `adapters/`, enforced by a CI lint rule.

**Do not:** launch anything live yet. Do not build a second adapter.

---

### S5 — The first-launch approval gate

**Depends on:** S4

**Build:** The one approval in the system. A plan review surface showing every ad as it will appear, the budget split, and the guardrails being set. Approval issues a signed, scoped, cap-bound, hash-bound token: bound to the exact plan content, limited to the specific channels and operations, capped in dollars, and expiring. Single-use per operation. Editing the plan voids outstanding tokens in the same transaction.

**Done when:**
1. A plan cannot go live without a valid token. Enforced in the gateway, not the UI.
2. Mutating one byte of the plan after approval invalidates the token.
3. Replaying a consumed token is rejected and raises a security alert, not a warning.
4. A token cannot authorize spend above its cap, a channel outside its scope, or an operation outside its list.
5. Editing an approved plan voids every outstanding token atomically.
6. With the token store unavailable, validation denies rather than permits, and alerts.
7. A negative suite covering hash mutation, scope escalation, cap overflow, replay, expiry, and cross-brand token use all fail closed. **This suite blocks every release from this point forward, permanently.**

**Do not:** add approval requirements anywhere else. This gate fires once per brand and once per newly connected channel. That is the whole design.

---

### S6 — It is alive

**Depends on:** S5

**Build:** Launch execution. Brand activation on first approval. The append-only action ledger with revert paths. The kill switch. Escalation records and notification.

**Done when:**
1. An approved plan goes live on a real ad account and serves impressions.
2. Every mutation writes exactly one action row with actor, diff, and a populated revert path. UPDATE and DELETE on that table are rejected by the database.
3. The kill switch pauses every live object for a brand in under 60 seconds and reports per-object results, including failures by name.
4. Executing a revert path restores the prior state exactly.
5. A brand is `active` only after a first approval, and the state transition is recorded.

**Do not:** build optimization yet. The system launches but does not yet manage.

---

### S7 — Measure honestly

**Depends on:** S6

**Build:** Hourly metric sync with per-channel watermarks. Raw fact storage. Normalization into a common schema with comparability class attached. Backfill on gaps. Late-arriving conversion handling.

**Done when:**
1. Metrics land hourly with no duplicates across re-runs, verified by replaying a sync window.
2. Every fact carries attribution window, conversion event, and view-through policy.
3. Attempting to sum metrics across different comparability classes raises rather than returning a number.
4. A 48-hour outage backfills completely on recovery without duplicating facts.
5. Restating late conversions updates the fact without corrupting the history of what was known when.

**Do not:** build dashboards. Build the data correctness first.

---

### S8 — It runs itself

**Depends on:** S7

This is the slice that makes the product an ad runner. Everything before it was setup.

**Build:** The loop tick from §3. Guardrail records and enforcement. Fatigue detection with the three-signal rule. Winner detection. Budget reallocation. Decision execution. Escalation triggers.

**Done when:**
1. The loop runs hourly per active brand, unattended, for seven consecutive days on a live account without human intervention.
2. A fatigued ad is detected, a replacement is generated and launched, and the fatigued ad is paused — in that order, with no gap where the brand has no live creative.
3. A two-signal fatigue condition does **not** trigger a refresh. Tested explicitly.
4. A decision that would breach any guardrail is rejected and recorded, never executed. Tested for every guardrail in §2.
5. Every escalation trigger in §2 fires its escalation and halts autonomous action on that scope only, leaving the rest of the account running.
6. Budget never moves between objects with mismatched comparability classes.
7. A budget increase exceeding `max_daily_spend_increase_pct` is clamped, not rejected, and the clamp is logged.
8. A tick that crashes mid-execution and resumes does not double-execute any action.

**Do not:** add channels, video, or an agency surface until the loop is stable on one channel.

---

### S9 — Prove the adapter contract

**Depends on:** S8

**Build:** A conformance suite that every adapter must pass — capability accuracy, idempotency under induced timeout, verification catching a silent failure, verbatim rejection capture, quota respect without probing. Then the second adapter, built against the suite. Cassette record-and-replay so adapter tests run in CI without touching a live account.

**Done when:**
1. The conformance suite exists and is executable before the second adapter is written.
2. The second adapter passes every test with zero waivers.
3. The first adapter also passes, unchanged. If it does not, the suite encoded the first channel's assumptions and must be fixed.
4. The full loop from S8 runs across two channels, reallocating between them.
5. Adapter tests run in CI against cassettes with no live account touched.

**Do not:** grant a waiver. Ever. The first waiver is the moment channel parity stops being real.

---

### S10 — All ten channels

**Depends on:** S9

**Build:** The remaining eight adapters. Platform access applications filed for every channel that requires review.

**Done when:**
1. All ten channels pass the conformance suite with zero waivers.
2. A published parity matrix shows every capability across every channel.
3. The loop reallocates across all connected channels.
4. Access application status is tracked and visible, with filing dates and current state.

**Note on sequencing:** file every platform access application at the start of this slice, not at the end. Meta Advanced Access, LinkedIn's Standard tier, TikTok's data-security review, and Pinterest's standard access all take weeks to months and are outside your control. Build against sandboxes and cassettes while waiting. Ship the channels with open API access first — [Reddit's Ads API](https://ads-api.reddit.com/docs/v3/) and [Snapchat's Marketing API](https://developers.snap.com/marketing-api/Ads-API/introduction) do not gate on allowlisting the way Meta and LinkedIn do.

---

### S11 — Video

**Depends on:** S9

**Build:** Video generation as a timeline of scene graphs. Render pipeline with per-channel duration, aspect, and caption requirements. Hook variation. Burned-in captions where required.

**Done when:**
1. One concept renders to 9:16, 1:1, and 16:9 with correct safe areas for each channel's overlay chrome.
2. Three hook variants generate from one concept without re-rendering the body.
3. A render failure degrades to static creative rather than leaving the brand with nothing to serve.
4. Render cost and duration per video are tracked as metrics from the first render.

---

### S12 — Agency platform

**Depends on:** S8

**Build:** Multi-brand management, seats and roles, optional client sign-off on first launch, white-label reporting, cross-client rollups, bulk operations across brands.

**Done when:**
1. A client approver can approve only their own brand and can read nothing else in the agency.
2. Cross-client rollups never mix comparability classes without annotation.
3. A bulk operation across 50 brands is atomic per brand — partial failure affects only the brands that failed, and reports which.
4. Converting a business account to agency and back loses no data and orphans no brands.

---

### S13 — Compliance surface

**Depends on:** S8

**Build:** AI-disclosure labeling per jurisdiction and per channel. Claim substantiation checks before render. Regulated-vertical rules. Policy rejection capture and routing.

**Done when:**
1. Generated content carries the disclosure each target jurisdiction and channel requires, applied at render time.
2. An unsubstantiated superlative or health claim blocks the render with a specific reason.
3. A channel policy rejection routes the verbatim platform text to a human and does not auto-retry.
4. A compliance export reproduces byte-identical output for a fixed window, signed.

Disclosure obligations are live and jurisdiction-specific: the [EU AI Act Article 50 transparency guidelines](https://www.twobirds.com/en/insights/2026/european-commission-adopts-final-guidelines-on-ai-act-article-50-transparency-obligations-first-impr), [Google's AI-generated content label policy](https://www.auditsocials.com/blog/google-ads-ai-generated-content-label-policy-2026), and [FTC endorsement rules at 16 CFR 255](https://www.auditsocials.com/blog/ftc-ai-endorsement-rules-16-cfr-255-state-equivalents-2026) each impose different requirements. Treat the rule set as data, not code, because it changes faster than release cycles.

---

### S14 — Self-serve

**Depends on:** S8

**Build:** Signup with both account types at equal prominence. Billing. Onboarding that reaches first live ad without a human touch. Result reporting. Support tooling.

**Done when:**
1. A new user signs up, connects a site and an ad account, and reaches an approvable plan in under four hours with no assistance.
2. Both account types are presented equally at signup, with neither defaulted nor upsold.
3. Billing handles failure, dunning, and cancellation without stranding live campaigns spending money.
4. A weekly result summary sends automatically and is comprehensible to someone who has never bought media.

**Pricing constraint:** never price as a percentage of ad spend. It aligns the product's revenue against the customer's efficiency, and it is the specific thing that makes agencies expensive. Flat tiers by brand count and spend band.

---

## 9. Success Criteria

The product works if these are true, measured on real accounts.

| Measure | Target |
|---|---|
| Time from signup to first live ad | ≤ 4 hours, self-serve |
| Human touches per brand per month, after activation | ≤ 2 |
| Autonomous actions executed without escalation | ≥ 90% |
| Brands where CPA improves within 60 days | ≥ 50% |
| Creative refresh cycles completed with zero dark time | 100% |
| Unauthorized spend events | 0, permanently |
| Channels shipping with a conformance waiver | 0 |

The second row is the real one. If a business owner is touching their account more than twice a month after activation, this is not an ad runner and the autonomy model has failed regardless of what any other number says.

---

## 10. Out of Scope

Named so the boundary is deliberate rather than discovered: connected TV and programmatic display, retail media beyond Amazon, a public API, marketing mix modeling, synthetic presenters and avatars, mobile applications, SOC 2 certification, EU data residency, and migration tooling from competitor platforms. All are plausible. None are in this build.

---

## Appendix — Channel Technical Notes

Constraints that shape the adapters, with sources. Re-verify before implementing each adapter; these change.

- **Meta** — rate limits are formula-driven and scale with active ad volume; Advanced Access requires App Review. [Marketing API rate limiting](https://developers.facebook.com/docs/marketing-api/overview/rate-limiting/), [ad standards](https://transparency.meta.com/policies/ad-standards/)
- **Google / YouTube** — Performance Max uses asset groups rather than conventional ad objects, which does not map onto the other channels' structure and must be modeled explicitly. [Asset groups](https://developers.google.com/google-ads/api/performance-max/asset-groups), [generative assets](https://support.google.com/google-ads/answer/14150602?hl=en)
- **LinkedIn** — tiered API access; Standard tier requires application and review. [Marketing API tiers](https://learn.microsoft.com/en-us/linkedin/marketing/integrations/marketing-tiers?view=li-lms-2026-08)
- **Microsoft** — mid-migration from SOAP to REST. Build REST-only. [Platform evolution](https://about.ads.microsoft.com/en/blog/post/april-2026/evolving-the-microsoft-advertising-api-platform)
- **Reddit** — v3 API, open developer access. [Ads API docs](https://ads-api.reddit.com/docs/v3/)
- **Pinterest** — standard access requires application; developer guidelines restrict automated behavior. [Developer guidelines](https://policy.pinterest.com/en/developer-guidelines)
- **Snapchat** — open Marketing API access. [Ads API](https://developers.snap.com/marketing-api/Ads-API/ads)
- **TikTok** — developer app registration plus a data-security review. [API for Business](https://business-api.tiktok.com/portal/docs?id=100025)
- **Amazon Ads** — Sponsored Products v3; catalog-driven, structurally unlike the social channels. [SP v3 overview](https://advertising.amazon.com/API/docs/en-us/guides/sponsored-products/overview)

Market context for prioritization: global ad spend passed one trillion dollars as algorithmic buying reshaped the market ([Dentsu](https://www.dentsu.com/news-releases/global-ad-spend-set-to-surpass-one-trillion-for-the-first-time-in-2026-as-the-algorithmic-era-redefines-growth)), while [Meta Advantage+](https://enalitica.com/blog/meta-advantage-plus-sales-campaigns) removed classic interest targeting entirely — making creative volume the binding constraint and validating the loop's emphasis on continuous refresh over targeting sophistication. Creative fatigue timelines are documented at 7–14 days for short-form placements ([inBeat](https://inbeat.agency/blog/facebook-creative-fatigue)).
