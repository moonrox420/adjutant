# Adjutant — Autonomous Ad Creation & Campaign Operations Platform

**Product Requirements Document**
**Version:** 1.0 (Draft for review)
**Date:** September 15, 2026
**Author:** Dustin Hill
**Status:** Pre-build — pending engineering sizing and design partner validation

---

## 1. TL;DR

Adjutant is an autonomous advertising operator. A business connects its website, brand assets, and ad accounts; Adjutant researches the business, generates a full multi-channel campaign plan with finished creative (static, video, and copy), and — after a human approves — launches it, monitors it, and continuously refreshes and reallocates it.

Two account types share one engine:

- **Business plan** — self-serve, single-brand, for SMBs who cannot afford a paid-media agency.
- **Agency plan** — multi-tenant, many client brands, client-level permissions, white-label reporting, and cross-client rollups.

The user picks their account type at signup. Neither is a second-class citizen; they are two front doors to the same platform.

V1 ships with **no channel hierarchy** — Meta, Google/YouTube, TikTok, LinkedIn, Microsoft, Reddit, Pinterest, Snapchat, and Amazon Ads are all treated as first-class destinations with equal feature parity commitments in the channel adapter layer.

V1 is **autonomous in creation and optimization, human-gated at launch**. Every net-new ad, every net-new campaign, and every budget change above a user-defined threshold requires explicit human approval before money moves.

---

## 2. Problem Statement

### Who has the problem

**SMB owners and operators** (1–200 employees, $2K–$100K/month potential ad budgets) who need paid acquisition but have no in-house media buyer, no creative team, and no time. Small businesses invest roughly 2–4% of revenue in advertising, around 37% of their marketing budget ([DesignRush](https://www.designrush.com/agency/ad-agencies/trends/advertising-cost-for-a-small-business)) — but the expertise to spend it well is priced out of reach. PPC agencies typically charge 10–20% of monthly ad spend or $1,000–$5,000 flat retainers ([ClickTrack Marketing](https://www.clicktrackmarketing.com/blog/how-much-do-ppc-agencies-charge)), and agency fees often consume 30–50% of the total marketing budget ([CiteHarbor](https://citeharbor.com/how-much-of-your-revenue-should-you-spend-on-a-marketing-agency/)). A business with $5K/month in spend is paying $1,000–$3,000/month for account management that is largely mechanical.

**Small and mid-sized agencies** (3–50 people, 10–200 client accounts) whose margins are destroyed by manual labor: creative production, campaign build-out, weekly optimization passes, and client reporting. Existing enterprise automation is not an option — Smartly.io is built for teams running $250K+ in annual spend with $4,000–$5,000/month minimums ([adlibrary.com review](https://adlibrary.com/posts/smartly-io-review-2026), [Ryze AI](https://www.get-ryze.ai/blog/smartly-io-review-2026-creative-automation)). The mid-market has no equivalent.

### How often and how painful

Four recurring pain loops:

1. **Creative supply is the binding constraint.** Platform algorithms now do the targeting; creative volume and freshness determine performance. Meta Advantage+ removes classic interest targeting entirely and steers budget, audience, and placement algorithmically ([Enalitica](https://enalitica.com/blog/meta-advantage-plus-sales-campaigns)) — which means the only remaining lever is creative, and it is the one SMBs cannot produce at volume. Creative fatigue compounds this: Reels-format creative fatigues in 7–14 days at moderate spend, and the warning signals are frequency above 3.0, CTR decaying 15% week over week, and rising CPA ([inBeat](https://inbeat.agency/blog/facebook-creative-fatigue), [adlibrary.com](https://adlibrary.com/posts/facebook-ad-creative-refresh-frequency)). An SMB producing one ad a month is structurally unable to compete.

2. **Cross-channel build-out is N× manual work.** Every channel has a different object hierarchy, asset spec, and objective taxonomy. Building the same campaign on Meta, Google, and TikTok is three separate builds with three separate asset renditions.

3. **Optimization is mechanical but unbounded.** Watching frequency, CPA drift, ad-set overlap, and budget pacing across dozens of ad sets is high-frequency, low-creativity work — exactly the work a machine should do, and exactly the work that gets skipped when an agency owner is also selling.

4. **Compliance is a moving target.** All major platforms now require disclosure of realistic AI-generated ad content — TikTok requires an AIGC label, Meta applies "AI info" labels and mandates self-disclosure for social-issue/electoral/political ads using AI-generated realistic media, and Google added a "How this ad was made" panel in My Ad Center ([Prizmad](https://prizmad.com/guides/ai-ad-disclosure-labels), [AppVids](https://appvids.com/blog/ai-ads-disclosure-rules)). EU AI Act Article 50 transparency obligations took effect 2 August 2026, requiring clear, visible labelling plus machine-readable marks on certain AI-generated or manipulated content — including deepfake-style content even where no deception was intended ([European Commission](https://commission.europa.eu/news-and-media/news/safer-and-more-transparent-ai-2026-08-02_en), [Bird & Bird](https://www.twobirds.com/en/insights/2026/european-commission-adopts-final-guidelines-on-ai-act-article-50-transparency-obligations-first-impr)). The FTC has no standalone AI-advertising rule but applies Section 5 and the Endorsement Guides (16 CFR Part 255) to fabricated endorsements, synthetic testimonials, and voice clones regardless of how they were produced ([ThousandAds](https://www.thousandads.com/blog/ftc-ai-generated-ads-2026), [Disclose AI](https://discloseai.net/disclosure-law-hub)). A tool that generates AI creative at volume without compliance rails is a liability engine.

### Cost of not solving

The addressable pool is enormous and growing: global ad spend is forecast to pass $1 trillion in 2026, growing 5.1%, with digital at 68.7% of total investment and social growing 11.4% ([Dentsu](https://www.dentsu.com/news-releases/global-ad-spend-set-to-surpass-one-trillion-for-the-first-time-in-2026-as-the-algorithmic-era-redefines-growth)). Point solutions in this space are creative generators, not operators — AdCreative.ai starts around $20–$39/month and generates static banners, social images, copy, and (on higher tiers) short video ([Creatify review](https://creatify.ai/review/adcreative-ai), [Hackceleration](https://hackceleration.com/labs/adcreativeai-pricing)). They hand you assets and stop. Nothing in the mid-market closes the loop from research → creative → launch → optimization → reporting.

### Insight

The unsolved problem is not "generate an ad." It is **owning the operating loop**: understanding a business well enough to have a point of view, producing enough creative to feed algorithmic channels, translating one strategy into nine platform-native builds, and grinding the optimization cycle forever — with a compliance layer and a human approval gate so the owner stays accountable.

---

## 3. Goals

| # | Goal | Metric | Target (12 months post-GA) | Baseline |
|---|---|---|---|---|
| G1 | Collapse time from signup to first live campaign | Median hours from account creation to first approved ad serving | ≤ 4 hours (self-serve), ≤ 24 hours (agency onboarding a client) | Agency onboarding today: 2–4 weeks |
| G2 | Make creative volume a non-issue | Median distinct creative variants live per active brand per 30 days | ≥ 24 | SMB baseline: 1–3/month |
| G3 | Beat the human baseline on efficiency | Median CPA/CAC change vs. brand's own trailing 60-day pre-Adjutant baseline, measured at day 90 | ≥ 15% improvement in ≥ 60% of brands | N/A |
| G4 | Make autonomous operation trustworthy | Approval-accept rate on AI-proposed launches | ≥ 75% approved without edit | N/A |
| G5 | Reclaim agency margin | Median hours/week of manual ops per client account, self-reported | ≤ 1.5 (from ~6) | Design-partner interviews |
| G6 | Zero compliance incidents | Platform policy violations resulting in account restriction, per 1,000 brand-months | < 1.0 | N/A |
| G7 | Business viability | Net revenue retention across both account types | ≥ 110% | N/A |

### Guardrail metrics (must not degrade)

- Ad disapproval rate ≤ 3% of submitted ads.
- Unauthorized spend events (money moved without a valid approval record): **0. Non-negotiable.**
- P95 creative generation latency ≤ 6 minutes for a static set, ≤ 20 minutes for a video set.

---

## 4. Non-Goals

| Non-goal | Rationale |
|---|---|
| **Full spend autonomy in V1** | Launch decisions stay human-gated. Trust must be earned with a performance record before we ask anyone to hand over the credit card. Graduated autonomy is a V2 roadmap item, not a V1 feature. |
| **Being a DSP or ad network** | We operate on top of platform APIs. We do not buy inventory, hold media, or take a spread on spend. This keeps us out of media-arbitrage regulation and channel conflict with the platforms. |
| **Organic social management** | No organic scheduling, community management, or inbox. Adjacent, distracting, and a different buyer. Organic content repurposing into paid is in scope; organic publishing is not. |
| **Full MMM / incrementality science** | V1 reports platform-attributed and first-party-attributed results. Media mix modeling and geo-lift testing require spend scale and statistical rigor we will not have at launch. |
| **Landing page / website building** | We read the site to inform creative, and we support UTM and conversion-tracking configuration. We do not build or host destinations. |
| **Regulated verticals at GA** | Political and electoral advertising, pharmaceutical, gambling, crypto, and financial-services-with-income-claims are blocked at onboarding. Political ads carry mandatory AI-disclosure regimes ([BG Blur](https://www.bgblur.com/blog/ai-ad-disclosure-meta-google-tiktok-ny-law-platform-policies)) and Meta prohibits income and business-opportunity claims outright ([Stackmatix](https://www.stackmatix.com/blog/meta-ads-income-claims-policy)). Not a V1 fight. |
| **Human-in-the-loop creative services** | We are not selling designer hours. If a brand needs bespoke art direction, they upload it as a reference or brand asset. |

---

## 5. Users & Personas

### P1 — Rhonda, SMB owner (Business plan)
Owns a 3-location HVAC company, $8K/month potential ad budget. Fired her agency after a year of "we're optimizing." Cannot tell a good ad from a bad one but knows exactly what a good lead is worth. Wants to approve things, not build them. **Success looks like:** more booked calls per dollar, and a weekly one-screen summary she actually reads.

### P2 — Marcus, agency owner (Agency plan)
Runs a 7-person performance shop with 34 clients, average $6K/month spend. His team's time goes to campaign build-out, creative requests, and monthly reporting decks. **Success looks like:** taking on 60 clients without adding headcount, plus white-label reporting his clients think is bespoke.

### P3 — Priya, in-house marketing manager (Business plan, upper tier)
Sole marketer at a 60-person DTC brand, $45K/month spend, owns the number. Sophisticated enough to have opinions on creative and structure; wants an operator, not a black box. **Success looks like:** overriding the AI when she disagrees, and having the AI learn from that override.

### P4 — Dev, agency media buyer (Agency plan, seat-level)
Executes across 12 accounts. Highest-frequency user of the platform. **Success looks like:** an approval queue he can clear in 20 minutes each morning, with enough context per item to decide without opening the platform's native ads manager.

### P5 — Brand/compliance reviewer (both plans, permissioned seat)
May be a franchise marketing director, a legal reviewer, or a brand manager. Does not build. Vetoes. **Success looks like:** every asset that touches the public passing through a rule set they control.

---

## 6. User Stories

Ordered by priority. P0 stories gate launch.

### Onboarding & brand intelligence
- **P0** — As a business owner, I want to enter my website URL and have the platform learn my products, pricing, offers, audience, and brand voice, so I do not fill out a 40-field intake form.
- **P0** — As an agency owner, I want to onboard a client by connecting their ad accounts and site and have a brand profile generated in under 30 minutes, so onboarding stops being a two-week project.
- **P0** — As a user, I want to review and correct the platform's understanding of my business before it creates anything, so wrong assumptions don't get baked into 30 ads.
- **P0** — As a user, I want to upload logos, fonts, color codes, product photos, and existing creative so generated ads look like my brand and not like stock output.
- **P1** — As an agency owner, I want to clone a brand profile and strategy template across similar clients (e.g., 9 dental practices) so I don't rebuild strategy per client.
- **P1** — As a user, I want the platform to ingest my past ad performance from connected accounts and tell me what has already worked, so it starts from evidence rather than a blank page.

### Strategy & planning
- **P0** — As a user, I want a proposed campaign plan — objective, channel split, budget allocation, audience approach, and creative concepts — with the reasoning shown, so I can judge it before spending.
- **P0** — As a user, I want to set a hard monthly budget ceiling per brand and per channel that the platform can never exceed, so autonomy cannot bankrupt me.
- **P0** — As a user, I want to state my target CPA or ROAS and have plans built against it, so the platform optimizes toward my economics.
- **P1** — As a user, I want to define blocklists — claims I can't make, competitors I won't name, words I hate — that constrain every generation, so I'm not repeatedly rejecting the same mistake.
- **P1** — As a user, I want the platform to recommend which channels fit my business rather than defaulting to all of them, so I don't spread $2K across nine platforms.
- **P2** — As a user, I want seasonal and promotional calendars so the platform plans around my known peaks.

### Creative generation
- **P0** — As a user, I want the platform to generate complete, platform-native ad sets — correct aspect ratios, safe zones, character limits, and format per destination — so I am not resizing anything.
- **P0** — As a user, I want multiple distinct creative *concepts* (not just color variants) per campaign, each with a stated hypothesis, so testing is meaningful.
- **P0** — As a user, I want generated static image ads with on-brand layout, legible text, and my logo correctly placed.
- **P0** — As a user, I want generated ad copy variants — primary text, headlines, descriptions, CTAs — within each platform's character limits.
- **P0** — As a user, I want to edit any generated asset (text inline, image swap, crop) without regenerating the whole set.
- **P1** — As a user, I want generated short-form video ads (6s–30s) assembled from my product imagery, stock/generated footage, captions, and voiceover, so I can run video without a production budget.
- **P1** — As a user, I want the platform to build variants from a proven winner — same concept, new hook or framing — so I can scale what works instead of restarting.
- **P1** — As an ecommerce brand, I want catalog-driven creative generated per product or product group from my product feed.
- **P2** — As a user, I want UGC-style and spokesperson video formats with synthetic presenters, with disclosure handled automatically.
- **P2** — As a user, I want localized creative (copy + on-image text) for multiple languages and markets.

### Approval & governance
- **P0** — As a reviewer, I want a single queue of everything awaiting approval, with the creative, targeting, budget, and rationale on one screen, so I can decide in seconds.
- **P0** — As a reviewer, I want to approve, reject with a reason, or edit-then-approve, and have rejection reasons train future generation, so the system converges on my taste.
- **P0** — As an account owner, I want to control who can approve spend, at what dollar thresholds, so a junior buyer can't launch a $10K/day campaign.
- **P0** — As a user, I want an immutable audit log of every action — who or what did it, when, what changed, and which approval authorized it — so I can answer "why did this run?" months later.
- **P0** — As an agency owner, I want optional client-side approval, where my client approves creative in a branded portal before anything launches.
- **P1** — As a reviewer, I want bulk approval with a policy filter (e.g., "approve all static creative under $50/day"), so the queue doesn't become the bottleneck.
- **P1** — As a compliance reviewer, I want automated pre-flight policy checks flagged before the item reaches me, so I only review real risk.

### Launch & channel operations
- **P0** — As a user, I want one approved plan to deploy to every selected channel with correct native structure, so I never touch a platform's native ads manager.
- **P0** — As a user, I want conversion tracking, pixels, and UTM taxonomy configured and validated before launch, so results are measurable from ad one.
- **P0** — As a user, I want clear failure reporting when a platform rejects an ad, including the reason and a proposed fix, so a rejection isn't a silent dead end.
- **P1** — As a user, I want scheduled launch windows and staged rollouts, so a new concept starts at 10% of budget.

### Optimization & monitoring
- **P0** — As a user, I want continuous monitoring with proactive alerts on overspend, pacing miss, zero-delivery, tracking breakage, and CPA drift, so I learn about problems from the platform and not from my bank balance.
- **P0** — As a user, I want the platform to detect creative fatigue from compound signals — frequency, engagement decay, cost-per-result trend — and propose refreshes before performance collapses.
- **P0** — As a user, I want proposed optimization actions (pause, budget shift, bid change, new variant) with expected impact, which I approve or auto-approve within preset limits.
- **P1** — As a user, I want statistically valid creative testing — hold-outs, minimum sample thresholds, no calling a winner on 40 clicks — so "winners" are real.
- **P1** — As a user, I want cross-channel budget reallocation proposals based on comparable efficiency, so money follows performance across platforms.
- **P2** — As a user, I want the platform to detect and act on external signals — a competitor's new offer, a weather event, a stock-out — with budget or messaging changes.

### Reporting
- **P0** — As a business owner, I want a weekly plain-language summary: what ran, what it cost, what it produced, what the platform changed and why, and what it plans next.
- **P0** — As an agency owner, I want white-labeled client reports on my logo and domain, generated automatically on a schedule.
- **P0** — As an agency owner, I want a cross-client portfolio view — total spend, pacing, performance outliers, accounts needing attention — so I triage 60 clients in one screen.
- **P1** — As a user, I want creative-level performance insights aggregated by concept, hook, format, and visual attribute, so I learn what works about my ads and not just which ad won.
- **P1** — As a user, I want to connect my CRM or ecommerce platform so reporting reflects revenue and lead quality, not just platform-reported conversions.

---

## 7. Product Architecture

### 7.1 Layer model

```
┌──────────────────────────────────────────────────────────────────┐
│  CLIENT SURFACES                                                 │
│  Business console │ Agency console │ White-label client portal   │
│  Approval queue (web + mobile) │ Slack/email approval actions    │
└──────────────────────────────────────────────────────────────────┘
                              │
┌──────────────────────────────────────────────────────────────────┐
│  ORCHESTRATION LAYER                                             │
│  Agent scheduler │ Task graph │ Approval state machine │         │
│  Policy & guardrail engine │ Audit ledger (append-only)          │
└──────────────────────────────────────────────────────────────────┘
                              │
┌──────────────────────────────────────────────────────────────────┐
│  AGENT LAYER (specialized, tool-using)                           │
│  Research │ Strategist │ Copywriter │ Art Director │ Video       │
│  Builder │ Analyst │ Optimizer │ Compliance │ Reporter           │
└──────────────────────────────────────────────────────────────────┘
                              │
┌──────────────────────────────────────────────────────────────────┐
│  KNOWLEDGE & DATA LAYER                                          │
│  Brand Graph │ Creative library (+embeddings) │ Performance      │
│  warehouse │ Creative-outcome learning store │ Policy corpus     │
└──────────────────────────────────────────────────────────────────┘
                              │
┌──────────────────────────────────────────────────────────────────┐
│  CHANNEL ADAPTER LAYER (uniform interface, per-channel impl.)    │
│  Meta │ Google+YouTube │ TikTok │ LinkedIn │ Microsoft │ Reddit  │
│  Pinterest │ Snapchat │ Amazon Ads                               │
│  Capability registry │ Spec registry │ Rate-limit governor       │
└──────────────────────────────────────────────────────────────────┘
```

### 7.2 Agent responsibilities

| Agent | Input | Output | Notes |
|---|---|---|---|
| **Research** | URL, business name, connected accounts | Brand Graph draft: offerings, pricing, ICP, differentiators, competitors, voice, proof assets | Reads site, reviews, competitor ads, past account performance. Never invents claims — every claim carries a source pointer. |
| **Strategist** | Brand Graph, budget, goal, constraints | Campaign plan: objectives, channel split, structure, audience approach, creative brief set, test design | Produces the artifact the human approves at the plan level. |
| **Copywriter** | Creative brief, voice profile, channel spec | Copy variants within exact character limits, per placement | Claim-substantiation check on every output. |
| **Art Director** | Brief, brand kit, product assets | Static compositions per aspect ratio with safe-zone-correct layout | Layout is templated + generative; text is rendered as a typographic layer, never baked into a diffusion image, to guarantee legibility. |
| **Video** | Brief, product assets, script | 6s/15s/30s cuts, vertical + square + horizontal, captions, voiceover, end card | Assembly-first architecture (scene graph → render), with generative b-roll as one scene source. |
| **Builder** | Approved plan + approved creative | Live campaign objects on each channel | Idempotent, transactional, with rollback. Owns rate-limit budgeting. |
| **Analyst** | Channel metrics + first-party conversions | Normalized performance model, fatigue scores, test significance | Owns the cross-channel metric normalization contract. |
| **Optimizer** | Analyst output, guardrails | Proposed actions with projected impact and confidence | Cannot execute a spend-affecting action without an approval token. |
| **Compliance** | Any outbound asset or targeting config | Pass / flag / block + rationale and policy citation | Runs pre-approval and pre-submission. Blocking authority over every other agent. |
| **Reporter** | Performance + action history | Weekly digest, client report, portfolio view | Plain-language narrative, not a metrics dump. |

### 7.3 Key architectural decisions

**ADR-1: Channel adapters implement a uniform capability interface; no channel-specific logic leaks upward.**
Every adapter declares its capabilities (objectives, formats, targeting dimensions, budget granularity, creative specs) into a **Capability Registry**. The Strategist plans against the registry, never against a hard-coded channel. Consequence: adding a tenth channel is an adapter + registry entry, not a replan of the product. This is what makes "no platform is higher priority" structurally true rather than aspirational.

**ADR-2: Creative is generated as a structured document, not a flat image.**
Every creative is a **scene graph** — layers, typographic elements, product cutouts, background, logo placement, motion track. Renditions for each aspect ratio are derived from the graph. Consequence: a 1:1 → 9:16 resize is a deterministic re-layout, not a regeneration, and text never degrades. Also makes on-brand editing possible without a round trip to a model.

**ADR-3: Approval is a state machine with cryptographic approval tokens.**
No adapter accepts a write operation without a valid, unexpired, scope-matched approval token referencing a specific plan hash and creative hash. If the creative changes, the hash changes and the token is void. Consequence: "unauthorized spend" becomes an architecturally prevented class of bug, not a policy we hope holds.

**ADR-4: Per-channel rate-limit governor with a shared token bucket per ad account.**
Rate limits are real and asymmetric. Meta's ads management quota per ad account per hour is roughly `base + 40 × active ads`, where base is 300 in the development tier and 100,000 in the standard tier — and standard tier requires Advanced Access to Ads Management Standard Access via App Review ([Meta Marketing API rate limiting](https://developers.facebook.com/docs/marketing-api/overview/rate-limiting/)). LinkedIn's Development tier allows POST operations for only 5 ad accounts and creation of 1 test ad account, with unlimited GETs; Standard tier lifts account caps and requires demonstrating a built, tested integration ([LinkedIn Marketing API access tiers](https://learn.microsoft.com/en-us/linkedin/marketing/integrations/marketing-tiers?view=li-lms-2026-08)). The governor must model each channel's quota shape independently, queue writes, and degrade gracefully — never fail a launch silently.

**ADR-5: Every optimization action is reversible and logged with its rationale.**
Actions are stored as diffs with a revert path. A user can ask "undo everything from Tuesday" and get it.

**ADR-6: Creative learning is per-brand-plus-global, with strict tenant isolation.**
A brand's own performance data trains its own creative selection. Aggregate, de-identified pattern learning (e.g., "problem-first hooks outperform feature-first in home services at 15s") feeds a global model. No brand's creative, copy, audience, or performance data is ever exposed to another tenant. Agency tenants are isolated per client by default with explicit opt-in for cross-client learning inside the same agency.

---

## 8. Channel Coverage Requirements

All V1 channels are **equal priority**. The Definition of Done for a V1 channel adapter is identical across all nine:

**Channel DoD:** OAuth connect + token refresh + revocation handling · read campaign structure · read performance at ad level · create campaign/ad group/ad · upload creative · update budget/bid/status · pause/resume/archive · report rejection reasons verbatim with fix mapping · declared entry in Capability and Spec registries · rate-limit model implemented and load-tested · sandbox/test-account path for CI.

| Channel | Surfaces covered | Access notes |
|---|---|---|
| **Meta** | Facebook, Instagram, Audience Network, Messenger; Advantage+ sales campaigns | Standard tier requires Advanced Access via App Review; quota scales with active ad count ([Meta docs](https://developers.facebook.com/docs/marketing-api/overview/rate-limiting/)) |
| **Google Ads** | Search, Display, Demand Gen, Performance Max | PMax structure: campaign → 1–100 asset groups, each with ≥1 final URL; asset groups are not shareable across campaigns; assets link via `AssetGroupAsset` with an `AssetFieldType` ([Google PMax asset groups](https://developers.google.com/google-ads/api/performance-max/asset-groups)) |
| **YouTube** | In-stream skippable/non-skippable, in-feed, Shorts, bumper — via Google Ads video campaigns and PMax video assets | Google mixes assets across YouTube, Gmail, and Search automatically in PMax ([Google](https://developers.google.com/google-ads/api/performance-max)) |
| **TikTok** | In-feed, Spark Ads, Smart+ | Documented hierarchy: campaign → ad group → ad, with batch creative management; requires developer app, authorization, terms signing, account verification, and data-security review ([TikTok API for Business](https://business-api.tiktok.com/portal/docs?id=100025)) |
| **LinkedIn** | Sponsored content, document, lead gen forms, video | Two tiers; Development tier caps POSTs at 5 ad accounts ([Microsoft Learn](https://learn.microsoft.com/en-us/linkedin/marketing/integrations/marketing-tiers?view=li-lms-2026-08)) |
| **Microsoft Advertising** | Bing Search, Audience Network, Shopping | **Build REST-only.** New features are REST-exclusive as of October 1, 2026 and the SOAP API is fully deprecated January 31, 2027 ([Microsoft Advertising](https://about.ads.microsoft.com/en/blog/post/april-2026/evolving-the-microsoft-advertising-api-platform)) |
| **Reddit** | Feed, conversation placements | API open to all developers, no allowlisting; ad groups and CBO campaigns require a `conversion_pixel_id` as of July 13, 2026; new objective enums rolled out September 21, 2026 — map legacy enums ([Reddit Ads API](https://ads-api.reddit.com/docs/v3/)) |
| **Pinterest** | Standard, video, idea, shopping ads | Ads API sits inside v5 Standard tier; requires a linked privacy policy at application, credentials must stay private, and rate-limit probing is prohibited ([Pinterest developer guidelines](https://policy.pinterest.com/en/developer-guidelines), [Blotato](https://www.blotato.com/blog/pinterest-api-pricing)) |
| **Snapchat** | Single image/video, story, collection, dynamic product ads, AR lenses (P2) | Marketing API open to all developers; supports campaign management, creative automation, DPA from catalogs, audiences, lead gen ([Snap for Developers](https://developers.snap.com/marketing-api/Ads-API/introduction)) |
| **Amazon Ads** | Sponsored Products, Sponsored Brands | Requires Login-with-Amazon client ID and profile-scoped headers; use Campaign Management v3, not deprecated v2 ([Amazon Ads API](https://advertising.amazon.com/API/docs/en-us/guides/sponsored-products/overview)) |

### Deferred channels (explicit "won't have" for V1)
Programmatic DSP / CTV, X, Spotify, Nextdoor, Yelp, retail media beyond Amazon (Walmart Connect, Instacart), and OOH. Retail media is the fastest-growing segment at 14.1% forecast 2026 growth ([Dentsu](https://www.dentsu.com/news-releases/global-ad-spend-set-to-surpass-one-trillion-for-the-first-time-in-2026-as-the-algorithmic-era-redefines-growth)) and is the strongest V2 candidate.

---

## 9. Functional Requirements

### 9.1 Account types & tenancy — P0

| ID | Requirement | Priority |
|---|---|---|
| ACC-1 | Signup presents two paths with equal prominence: **Business** (one brand, optional additional brands) and **Agency** (unlimited client brands, seats, roles). No path is defaulted or upsold over the other. | P0 |
| ACC-2 | Account type is changeable post-signup without data loss or re-onboarding (Business → Agency migration converts the brand into the first client). | P0 |
| ACC-3 | Agency accounts support Brand (client) workspaces with per-workspace connections, budgets, approvals, and data isolation. | P0 |
| ACC-4 | Role model: Owner, Admin, Buyer, Creative, Reviewer, Client-Viewer, Client-Approver. Spend-approval authority is a per-role, per-dollar-threshold permission. | P0 |
| ACC-5 | Agency white-label: custom logo, colors, domain (CNAME), and sender identity on client portals and reports. | P0 |
| ACC-6 | Cross-client portfolio dashboard for Agency accounts with drill-down and exception surfacing. | P0 |
| ACC-7 | Bring-your-own-credentials support where a platform requires or prefers it, with credentials scoped per tenant and encrypted at rest with per-tenant keys. | P1 |

**Acceptance criteria — ACC-4**
- Given a Buyer with a $500/day approval threshold, when they attempt to approve a plan with $900/day budget, then approval is blocked with an escalation path to an Admin, and the attempt is logged.
- Given a Client-Approver seat, when they open the portal, then they see only their own brand's creative and approvals, and no spend-reallocation controls.
- Given an Owner revokes a seat, when that user attempts any API or UI action, then all sessions and tokens are invalidated within 60 seconds.

### 9.2 Onboarding & Brand Graph — P0

| ID | Requirement | Priority |
|---|---|---|
| BG-1 | Ingest a website URL and produce a structured Brand Graph: offerings, pricing signals, offers/promos, ICP hypotheses, differentiators, proof points, tone-of-voice profile, prohibited claims. | P0 |
| BG-2 | Every Brand Graph assertion stores a provenance pointer (source URL or connected-account record). Assertions without provenance are surfaced as "unverified — please confirm." | P0 |
| BG-3 | Ingest brand kit: logo files (with transparency), color palette, typefaces, product imagery, existing creative. Auto-extract palette and fonts from the site as a starting point. | P0 |
| BG-4 | Ingest trailing 12 months of connected-account performance and produce a "what already worked" summary at concept, format, audience, and channel level. | P0 |
| BG-5 | Human confirmation gate: no creative generation until the user confirms or edits the Brand Graph. | P0 |
| BG-6 | Vertical templates (home services, dental, ecommerce/DTC, local retail, B2B SaaS, restaurants, fitness, professional services, real estate, auto) pre-seeding structure, objectives, and creative patterns. ≥10 at GA. | P1 |
| BG-7 | Brand Graph cloning with field-level diff for agencies onboarding similar clients. | P1 |
| BG-8 | Competitor creative monitoring from public ad libraries, feeding the Strategist. | P2 |

**Acceptance criteria — BG-1**
- Given a functioning ecommerce site with ≥10 products, when onboarding runs, then a Brand Graph is produced within 10 minutes with ≥80% field completion and every populated field carrying provenance.
- Given a single-page site with minimal copy, when onboarding runs, then the system explicitly lists what it could not determine and asks targeted questions rather than inventing content.
- Given a site behind authentication or a paywall, when ingestion fails, then the user is offered a manual intake path within the same flow.

### 9.3 Strategy & planning — P0

| ID | Requirement | Priority |
|---|---|---|
| ST-1 | Generate a campaign plan containing: objective, per-channel budget allocation with rationale, campaign/ad-set structure per channel, audience approach, creative brief set, test design, and projected outcome range with stated confidence. | P0 |
| ST-2 | Every plan is rendered in both a technical view (exact objects to be created per channel) and a plain-language view. | P0 |
| ST-3 | Hard budget guardrails: monthly ceiling per brand, per channel, and per campaign; daily maximum; and a global kill switch. Ceilings are enforced at the adapter layer, not just the UI. | P0 |
| ST-4 | Goal input in the user's own terms — target CPA, target ROAS, lead volume, or "spend $X efficiently" — translated into per-channel bid strategies. | P0 |
| ST-5 | Channel recommendation logic: refuse to spread budget below per-channel viability minimums; recommend fewer channels for small budgets with the reasoning shown. | P1 |
| ST-6 | Constraint library per brand: banned claims, banned words, required disclaimers, competitor mention policy, required legal footers. Enforced at generation time. | P1 |
| ST-7 | Promotional calendar with date-bounded offers and auto-expiry of time-limited creative. | P2 |

**Acceptance criteria — ST-3**
- Given a $3,000 monthly ceiling and $2,950 spent, when the Optimizer proposes a budget increase, then the proposal is rejected pre-approval with "would exceed monthly ceiling" and the user is offered a ceiling-raise flow requiring explicit confirmation.
- Given a user triggers the global kill switch, when it is confirmed, then all campaigns across all channels are paused, and a verification read confirms paused state per channel within 5 minutes, with any channel failure escalated by alert.

### 9.4 Creative generation — P0

| ID | Requirement | Priority |
|---|---|---|
| CR-1 | Generate ≥3 distinct creative **concepts** per campaign, each with a written hypothesis, and ≥3 executions per concept. | P0 |
| CR-2 | Generate every required rendition per target placement from the scene graph: correct aspect ratio, resolution, safe zones, file size, and duration per channel spec registry. | P0 |
| CR-3 | Text in static creative is rendered as a typographic layer over the composition. Text is never generated inside a diffusion image. | P0 |
| CR-4 | Copy generation respects exact per-placement character limits, with truncation-preview validation before submission. | P0 |
| CR-5 | Brand fidelity enforcement: logo from the uploaded asset (never redrawn), palette locked to brand colors, typeface from brand kit with licensed fallback. | P0 |
| CR-6 | Inline editing of any element — copy text, image swap, crop, layer reposition, color — with re-render across all renditions in ≤30 seconds. | P0 |
| CR-7 | Creative library with versioning, tagging, search, and performance data attached to every asset. | P0 |
| CR-8 | Video generation: 6s/15s/30s cuts, vertical/square/horizontal, auto-captions, optional synthetic voiceover, brand end card. Assembled via scene graph. | P1 |
| CR-9 | Catalog-driven generation from a product feed (Google Merchant Center, Shopify, CSV), including per-SKU and per-collection creative. | P1 |
| CR-10 | Winner-scaling: generate variants of a proven creative along a single axis (hook, format, offer framing, visual treatment) while holding the rest constant. | P1 |
| CR-11 | Localization: copy translation plus on-image text re-layout for RTL and length-variant languages. | P2 |
| CR-12 | Synthetic presenter / UGC-style video with mandatory disclosure metadata. | P2 |
| CR-13 | Repurpose existing organic content (user-supplied) into paid formats. | P2 |

**Acceptance criteria — CR-2, CR-3**
- Given an approved brief targeting Meta Feed, Meta Reels, TikTok In-Feed, YouTube Shorts, and Pinterest, when generation completes, then every rendition passes automated spec validation (ratio, min resolution, max file size, duration, safe-zone text clearance) with zero failures before it can enter the approval queue.
- Given any generated static creative, when it is inspected, then all rendered text is selectable vector/text-layer data in the scene graph, no text is clipped or overflowing its bounding box, no word is broken mid-word across lines, and text/background contrast ratio is ≥4.5:1.
- Given a 9:16 rendition derived from a 1:1 master, when compared, then logo, primary text, and CTA all sit within the platform's declared safe zone with no element cropped.

### 9.5 Compliance & policy engine — P0

| ID | Requirement | Priority |
|---|---|---|
| CP-1 | Pre-submission policy check of every creative and targeting config against a maintained policy corpus per channel, returning pass/flag/block with the specific policy cited. | P0 |
| CP-2 | Claim substantiation: any performance, income, health, or superlative claim must map to a Brand Graph proof point with provenance, or it is blocked. Meta prohibits income, earnings, and business-opportunity claims implying guaranteed returns ([Stackmatix](https://www.stackmatix.com/blog/meta-ads-income-claims-policy)). | P0 |
| CP-3 | AI-content provenance: every generated asset carries internal metadata recording which models and inputs produced it, and whether it contains realistic depictions of people, voices, or events. | P0 |
| CP-4 | Automated AI-disclosure handling per channel and jurisdiction: apply platform-required AI labels (e.g., TikTok AIGC labelling, Meta self-disclosure for social-issue/electoral/political ads) and EU AI Act Article 50 visible labelling plus machine-readable marking where applicable ([Prizmad](https://prizmad.com/guides/ai-ad-disclosure-labels), [European Commission](https://commission.europa.eu/news-and-media/news/safer-and-more-transparent-ai-2026-08-02_en)). | P0 |
| CP-5 | Hard block on synthetic depictions of identifiable real people without a recorded, verifiable consent artifact. Google prohibits deepfake-style content depicting real people, with enforcement including account-level action ([AuditSocials summary of Google policy](https://www.auditsocials.com/blog/google-ads-ai-generated-content-label-policy-2026)); the FTC's Endorsement Guides reach fabricated endorsements, virtual influencers, voice clones, and synthetic testimonials regardless of production method ([AuditSocials](https://www.auditsocials.com/blog/ftc-ai-endorsement-rules-16-cfr-255-state-equivalents-2026)). | P0 |
| CP-6 | Restricted-vertical gate at onboarding: block political/electoral, pharma, gambling, crypto, adult, weapons, and income-claim business opportunities. Prohibited categories cannot be advertised under any wording; restricted categories require pre-authorization ([Meta Advertising Standards](https://transparency.meta.com/policies/ad-standards/)). | P0 |
| CP-7 | Rejection intelligence: capture every platform rejection verbatim, classify it, propose a specific remediation, and feed it back into the policy corpus and generation constraints. | P0 |
| CP-8 | Jurisdiction resolution: determine the applicable disclosure regime from targeting geography, and apply the strictest applicable rule. | P1 |
| CP-9 | Named-entity guardrails: block unauthorized use of third-party trademarks and competitor brand names in creative unless the brand has recorded authorization. | P1 |
| CP-10 | Compliance audit export: per-asset report of models used, disclosures applied, policy checks passed, and approver identity. | P1 |

**Acceptance criteria — CP-4, CP-5**
- Given creative containing a photorealistic AI-generated human and targeting an EU member state, when it is submitted, then a visible AI label and machine-readable provenance mark are applied, and submission without them is architecturally impossible.
- Given creative containing a likeness resembling a named public figure, when compliance runs, then the asset is hard-blocked with the policy citation, no override is available to any role below Owner, and any Owner override is logged with a mandatory written justification.
- Given a brand in a restricted vertical attempts onboarding, when the vertical is detected, then account creation completes but campaign creation is disabled with an explanation and a waitlist option.

### 9.6 Approval workflow — P0

| ID | Requirement | Priority |
|---|---|---|
| AP-1 | Unified approval queue with item types: plan, creative set, launch, budget change, structural change. Each item shows creative preview, targeting, budget, projected impact, and AI rationale on one screen. | P0 |
| AP-2 | Actions per item: approve, reject with structured reason, edit-then-approve, request changes, defer. | P0 |
| AP-3 | Rejection reasons are structured (off-brand, wrong claim, wrong audience, wrong offer, poor quality, compliance concern, other + free text) and feed generation constraints for that brand. | P0 |
| AP-4 | Approval tokens bind to a plan hash + creative hash + scope + expiry. Any post-approval mutation voids the token and returns the item to the queue. | P0 |
| AP-5 | Approve via email and Slack without entering the app, with the same audit guarantees. | P0 |
| AP-6 | Agency two-stage approval: internal approval, then optional client approval in the white-label portal, configurable per client. | P0 |
| AP-7 | Immutable append-only audit ledger: actor (human or named agent), timestamp, action, before/after diff, authorizing approval token, channel API request/response IDs. Exportable. | P0 |
| AP-8 | Auto-approve rules for bounded optimization actions: user defines action types and dollar thresholds that execute without review (e.g., "pause any ad over 2× target CPA with ≥50 conversions of evidence"). | P0 |
| AP-9 | Bulk approval with filters and a mandatory diff summary before confirming. | P1 |
| AP-10 | SLA behavior: items unapproved after N days expire rather than launching stale creative, with re-freshening offered. | P1 |

**Acceptance criteria — AP-4, AP-8**
- Given an approved creative set, when a user edits one headline after approval, then the launch is halted, the token is voided, and the item reappears in the queue flagged "changed after approval" with the diff.
- Given a direct database or API attempt to create an ad without a valid token, when the adapter receives it, then the write is refused, the attempt is logged as a security event, and an alert fires.
- Given auto-approve is enabled for pauses only, when the Optimizer proposes a budget increase, then it enters the manual queue regardless of size.

### 9.7 Launch & deployment — P0

| ID | Requirement | Priority |
|---|---|---|
| LN-1 | Deploy an approved plan across all selected channels, building native objects per channel in dependency order, idempotently. | P0 |
| LN-2 | Partial-failure handling: if one channel fails, successfully deployed channels remain live, the failed channel is reported with reason and retry option, and no duplicate objects are created on retry. | P0 |
| LN-3 | Pre-flight validation: tracking pixel present and firing, conversion actions configured, UTM taxonomy applied, landing page reachable and returning 200, budget/billing valid on the ad account. Channel-specific prerequisites enforced — e.g., Reddit requires `conversion_pixel_id` on ad groups and CBO campaigns as of July 13, 2026 ([Reddit Ads API](https://ads-api.reddit.com/docs/v3/)). | P0 |
| LN-4 | Rate-limit-aware write queue per ad account per channel, modeling each channel's distinct quota shape, with backoff, prioritization, and user-visible queue status. | P0 |
| LN-5 | Rollback: revert a deployment to pre-launch state within 10 minutes of a request. | P0 |
| LN-6 | Staged rollout: launch a new concept at a configurable share of budget, with automatic scale-up gated on threshold performance and human approval. | P1 |
| LN-7 | Scheduled launch windows including dayparting and timezone-correct scheduling per market. | P1 |

**Acceptance criteria — LN-1, LN-2**
- Given a plan spanning Meta, Google, and TikTok, when deployment runs and TikTok returns a 5xx, then Meta and Google campaigns are live, the TikTok item shows a retryable failure with the raw error, and retry produces no duplicate campaigns.
- Given a deployment is retried after a timeout where the create actually succeeded, when the retry runs, then the adapter detects the existing object via idempotency key and reconciles rather than duplicating.

### 9.8 Monitoring & optimization — P0

| ID | Requirement | Priority |
|---|---|---|
| OP-1 | Performance sync from every channel at ≤60-minute intervals, normalized into a unified metric model with explicit documentation of cross-channel comparability limits. | P0 |
| OP-2 | Anomaly detection with alerting: overspend/pacing deviation, zero or collapsed delivery, tracking breakage, CPA/ROAS drift beyond tolerance, ad disapproval, account restriction, payment failure. | P0 |
| OP-3 | Creative fatigue scoring from compound signals — frequency trend, engagement-rate decay, cost-per-result trend — not elapsed time alone, with format-calibrated thresholds (short-form video fatigues materially faster than static feed) ([adlibrary.com](https://adlibrary.com/posts/ad-creative-refresh-frequency), [inBeat](https://inbeat.agency/blog/facebook-creative-fatigue)). | P0 |
| OP-4 | Optimizer proposals with action, rationale, evidence, projected impact, confidence, and reversal path. | P0 |
| OP-5 | Automatic creative refresh pipeline: fatigue detected → new executions generated from the winning concept → queued for approval before the fatigued creative's performance floor is breached. | P0 |
| OP-6 | Statistical discipline on test conclusions: minimum sample and minimum conversion thresholds per decision type; no winner declared below them, with the reason shown. | P1 |
| OP-7 | Cross-channel budget reallocation proposals based on normalized marginal efficiency, with comparability caveats stated. | P1 |
| OP-8 | Learning loop: attach outcomes to creative attributes (hook type, format, offer, visual treatment, length) and use them to weight future generation, per brand and in aggregate. | P1 |
| OP-9 | External-signal responses (inventory, weather, competitor offers, seasonality). | P2 |

**Acceptance criteria — OP-3, OP-5**
- Given an ad whose frequency crosses 3.0 while CTR declines ≥15% week over week and CPA rises ≥20%, when the fatigue scan runs, then the ad is flagged fatigued with the three contributing signals shown, and a refresh set is generated and queued within 24 hours.
- Given fatigue is detected on a top performer, when the refresh is generated, then variants preserve the winning concept and vary only the declared axis, and the original is not paused until a replacement is approved and live.

### 9.9 Reporting — P0

| ID | Requirement | Priority |
|---|---|---|
| RP-1 | Weekly plain-language digest: what ran, spend, results against goal, what the platform changed and why, what it plans next, and what needs the user's attention. | P0 |
| RP-2 | White-label client reports: agency branding, custom domain, scheduled delivery, PDF and web link. | P0 |
| RP-3 | Agency portfolio view: all clients, spend, pacing, goal attainment, exceptions ranked by urgency. | P0 |
| RP-4 | Full transparency into every autonomous action taken in the period, with rationale. | P0 |
| RP-5 | Creative performance analytics aggregated by concept, hook, format, and visual attribute — not just per ad ID. | P1 |
| RP-6 | First-party data integration (CRM, ecommerce, call tracking) so reported outcomes reflect revenue and lead quality. | P1 |
| RP-7 | Scheduled exports and a read API for agency BI stacks. | P2 |

---

## 10. Data Model (core entities)

| Entity | Key fields | Notes |
|---|---|---|
| `Account` | id, type (business\|agency), plan, billing, white_label_config | Type is mutable |
| `Brand` | id, account_id, brand_graph_id, brand_kit_id, vertical, restricted_flags, guardrails | The tenant unit for isolation |
| `BrandGraph` | assertions[] {field, value, provenance_uri, confidence, human_confirmed_at} | Provenance is mandatory |
| `BrandKit` | logos[], palette, typefaces, product_assets[], reference_creative[] | |
| `ChannelConnection` | brand_id, channel, ad_account_id, token_ref, scopes, access_tier, health | Tier tracked because it changes quota behavior |
| `Plan` | brand_id, objective, goal_targets, allocations[], structure[], briefs[], plan_hash, state | Hash-bound to approvals |
| `CreativeConcept` | plan_id, hypothesis, axis_definitions | |
| `Creative` | concept_id, scene_graph, renditions[], compliance_record, ai_provenance, creative_hash, version | Scene graph is the source of truth |
| `Rendition` | creative_id, channel, placement, ratio, spec_validation_result, asset_uri | |
| `ApprovalToken` | subject_type, subject_hash, scope, approver_id, dollar_limit, issued_at, expires_at, signature | Void on hash change |
| `CampaignObject` | brand_id, channel, native_id, level, parent_id, idempotency_key, state | Mirrors channel hierarchy |
| `Action` | actor (human\|agent:name), type, target, diff, rationale, approval_token_id, revert_path, executed_at | Append-only |
| `MetricFact` | campaign_object_id, channel, date_hour, impressions, clicks, spend, conversions, value, frequency, normalized_fields | |
| `ComplianceRecord` | creative_id, checks[], verdicts[], policy_citations[], disclosures_applied[], overrides[] | |
| `LearningSignal` | brand_id, creative_attributes, outcome_metrics, scope (brand\|global) | Tenant-isolated by default |

---

## 11. Success Metrics

### Leading indicators (weeks 1–8 post-launch)

| Metric | Target | Method |
|---|---|---|
| Onboarding completion (signup → Brand Graph confirmed) | ≥ 70% | Funnel instrumentation |
| Activation (signup → first approved live ad) | ≥ 50% within 7 days | Funnel |
| Median time to first live ad | ≤ 4 hours self-serve | Event timestamps |
| Approval-accept rate without edit | ≥ 75% | Approval queue events |
| Creative edit rate before approval | ≤ 30% and falling week over week | Approval events |
| Ad disapproval rate | ≤ 3% of submissions | Channel responses |
| Spec validation failure rate pre-queue | ≤ 1% | Validation logs |
| Channels connected per brand | ≥ 2.5 median | Connection records |
| Auto-approve adoption | ≥ 40% of brands enable ≥1 rule by day 30 | Settings events |

### Lagging indicators (months 3–12)

| Metric | Target | Method |
|---|---|---|
| CPA/ROAS improvement vs. pre-Adjutant 60-day baseline at day 90 | ≥15% better in ≥60% of brands | Cohort analysis on connected history |
| Logo retention, month 6 | ≥ 85% Business / ≥ 92% Agency | Billing |
| Net revenue retention | ≥ 110% | Billing |
| Spend under management growth | 15% MoM through month 12 | Platform data |
| Agency clients per agency account | +50% within 6 months of adoption | Platform data |
| Self-reported manual ops hours per client per week | ≤ 1.5 | In-product survey |
| Compliance incidents per 1,000 brand-months | < 1.0 | Incident log |
| NPS | ≥ 40 | Quarterly survey |
| Support tickets per active brand per month | ≤ 0.5 | Helpdesk |

### Counter-metrics watched for autonomy harm
- Share of brands whose CPA worsened >10% at day 90 — **investigate above 15%**.
- Reverted autonomous actions as a share of all autonomous actions — **investigate above 10%**.
- Approval-queue abandonment (items expiring unreviewed) — **investigate above 20%**; signals queue fatigue, our most likely failure mode.

---

## 12. Packaging (directional, for validation)

| | Business | Agency |
|---|---|---|
| Entry | Single brand, 2 channels, capped monthly creative volume | 10 client brands, all channels, seats |
| Growth | Multi-brand, all channels, video generation, higher volume | 40 brands, white-label portal + reports, client approval flows |
| Scale | High spend tier, first-party data integrations, priority support | Unlimited brands, read API, SSO, dedicated support |
| Pricing basis | Flat subscription + creative volume tier. **No percent-of-spend.** | Per-brand pricing with volume bands |

Deliberate decision: **do not price on percent of spend.** It misaligns incentives (we would profit from telling you to spend more), triggers agency channel conflict, and invites comparison to the very fee structure we are displacing.

---

## 13. Phased Roadmap

### Phase 0 — Foundations (weeks 1–8)
Channel adapter framework, Capability + Spec registries, rate-limit governor, approval state machine with token binding, audit ledger, tenancy and roles. Meta + Google/YouTube adapters to full DoD. Platform app-review applications filed for every channel requiring elevated access — **start day one; App Review is the long pole** (Meta Advanced Access, LinkedIn Standard tier, TikTok data-security review, Pinterest Standard access).

### Phase 1 — Closed alpha (weeks 9–16)
Brand Graph ingestion, Strategist, Copywriter, Art Director (static only), Builder, approval queue, launch, basic monitoring. 8–12 design partners: 6 SMBs, 4 agencies, spanning 4 verticals. Channels: Meta, Google, YouTube. **Alpha exit gate:** ≥60% approval-accept rate, zero unauthorized-spend events, ≤5% disapproval rate.

### Phase 2 — Channel parity + video (weeks 17–28)
Remaining seven adapters to full DoD. Video agent. Optimizer with auto-approve rules. Fatigue detection and refresh pipeline. Agency console, white-label, client portal. Compliance engine to full P0 scope. **Beta exit gate:** ≥75% approval-accept rate, demonstrated CPA improvement in ≥50% of beta brands at day 60, one clean compliance audit.

### Phase 3 — GA (weeks 29–36)
Vertical templates (≥10), catalog-driven creative, winner-scaling, creative analytics, first-party data integrations, cross-channel reallocation, self-serve billing, support tooling.

### Phase 4 — Post-GA (months 10–18)
Graduated autonomy tiers earned through performance history. Retail media expansion (Walmart Connect, Instacart). Programmatic/CTV. Localization. Synthetic presenter video. Read API and BI exports. MMM/incrementality exploration once spend scale supports it.

---

## 14. Risks

| Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|
| **Platform API access denied or revoked** | Existential | Medium | File App Review for every channel in Phase 0. Maintain bring-your-own-credentials fallback where permitted (Pinterest explicitly allows end-user-key apps if credentials are stored client-side, not server-side — [Pinterest guidelines](https://policy.pinterest.com/en/developer-guidelines)). Build to published partner terms, never against undocumented behavior. Never probe rate limits — Pinterest prohibits it outright. |
| **Platforms bundle this natively for free** | High | High | Google already generates PMax text, image, logo, and video assets from a website with a few clicks ([Google Ads Help](https://support.google.com/google-ads/answer/14150602?hl=en)), and Meta's Advantage+ automates budget, audience, and placement. Our defensibility is cross-channel, not single-channel: one strategy deployed natively to nine platforms with unified governance, unified creative supply, and an audit trail. Single-platform AI tools cannot do that by construction. |
| **Autonomous action causes real financial harm** | Severe | Medium | Adapter-level hard ceilings, token-bound writes, reversible actions, global kill switch, human launch gate in V1, and a published incident/remediation policy. |
| **AI creative underperforms human creative** | High | Medium | Volume plus rigorous testing is the thesis, not per-asset brilliance. Instrument honestly against each brand's own baseline. If a cohort underperforms at day 90, escalate rather than obscure. |
| **Compliance violation gets a client's ad account banned** | Severe | Medium | Blocking compliance agent, restricted-vertical gate, mandatory provenance, automated disclosure, no synthetic real-person likeness without consent artifact, rejection-feedback loop. Policy corpus maintained as a first-class product surface with an owner. |
| **Approval queue becomes the bottleneck it was meant to remove** | High | High | This is the most likely product failure. Mitigate with one-screen decisions, Slack/email approval, bulk approval with diffs, auto-approve rules from day one, and abandonment as a tracked counter-metric. |
| **Regulatory shift on AI-generated advertising** | Medium | High | Already in motion: EU AI Act Article 50 live since 2 August 2026, Google's disclosure panel rolling out through 2026, state-level AI laws accumulating. Architect disclosure as a configurable jurisdiction-resolved policy layer, not hard-coded per channel. |
| **Cross-channel metric normalization misleads users** | Medium | High | Attribution windows and conversion definitions differ per platform. Publish comparability limits in-product, prefer first-party conversion data for cross-channel decisions, and never present a single blended ROAS without a stated methodology. |
| **Microsoft SOAP deprecation breaks the adapter** | Low | Low | Build REST-only from day one. New features are REST-exclusive from October 1, 2026; SOAP fully deprecated January 31, 2027 ([Microsoft Advertising](https://about.ads.microsoft.com/en/blog/post/april-2026/evolving-the-microsoft-advertising-api-platform)). |
| **Generation cost per brand exceeds subscription revenue** | High | Medium | Meter generation, tier creative volume by plan, cache and derive renditions from scene graphs rather than regenerating, route to cheaper models for low-stakes variants. Track gross margin per brand weekly from alpha. |
| **Agency channel conflict — we replace our own buyers** | Medium | Medium | Position as capacity, not replacement; price per brand, not per spend; ship agency-only features (white-label, portfolio, client approval) that a direct-to-SMB product would not have. |

---

## 15. Open Questions

| # | Question | Owner | Blocking? |
|---|---|---|---|
| Q1 | Which platform App Reviews will actually approve us pre-revenue, and what is the realistic timeline per channel? | Eng/BD | **Blocking Phase 2** |
| Q2 | Do we require our own platform-level ad accounts, or strictly bring-your-own? Affects billing, liability, and access tier. | Legal/Finance | **Blocking Phase 0** |
| Q3 | What is the minimum viable monthly spend per channel below which we refuse to deploy? Needs empirical answer from alpha. | Product/Data | Non-blocking |
| Q4 | Is video generation P0 or P1 in practice? Short-form video is where creative volume matters most and where fatigue is fastest — the P1 designation may be wrong. | Product | Non-blocking, revisit at alpha exit |
| Q5 | Do we support a "fully manual" mode for agencies who want the creative engine but not the operator? Large potential segment, real scope cost. | Product | Non-blocking |
| Q6 | What is our liability posture when an autonomous action wastes spend? Cap, credit, or disclaim? | Legal | **Blocking GA** |
| Q7 | Who owns the policy corpus, and what is the SLA for reflecting a platform policy change? | Ops | **Blocking Phase 2** |
| Q8 | Do we allow cross-client learning within an agency tenant by default (opt-out) or off by default (opt-in)? Material to both performance and trust. | Product/Legal | Non-blocking |
| Q9 | Should approval expiry auto-refresh creative, or hard-expire? Affects queue abandonment behavior. | Design | Non-blocking |
| Q10 | What proof artifact satisfies consent for a real-person likeness, and who verifies it? | Legal/Compliance | **Blocking P2 synthetic presenter work** |

---

## 16. Appendix — Channel Technical Notes for Engineering

- **Meta:** Ads management quota per ad account per hour ≈ `base + 40 × active ads`, base 300 (dev tier) or 100,000 (standard tier); standard tier requires Advanced Access to Ads Management Standard Access through App Review. Access tier is readable from the `X-Ad-Account-Usage` header via `ads_api_access_tier`. Custom audience quota is separately bounded. Required permissions include `ads_read`, `ads_management`, `business_management`, `read_insights`. ([Meta docs](https://developers.facebook.com/docs/marketing-api/overview/rate-limiting/))
- **Google Ads:** PMax campaign → 1–100 asset groups; asset groups are not shareable across campaigns; `Asset` objects are shareable and may carry different `AssetFieldType` values in different asset groups; ≥1 final URL required per asset group; in non-retail PMax, asset groups and their assets must be created together. ([Google docs](https://developers.google.com/google-ads/api/performance-max/asset-groups))
- **TikTok:** Integration path is TikTok for Business account → developer registration → developer app → authorization → terms signing → account verification → data-security/US-data-security review. Hierarchy: campaign → ad group → ad, with async copy tasks and dynamic quota endpoints for active ad groups. ([TikTok docs](https://business-api.tiktok.com/portal/docs?id=100025))
- **LinkedIn:** Development tier = 1 test ad account creatable, POST on ≤5 ad accounts, unlimited GET. Standard tier = unlimited account creation and updates. Must build and test on Development before applying for Standard. ([Microsoft Learn](https://learn.microsoft.com/en-us/linkedin/marketing/integrations/marketing-tiers?view=li-lms-2026-08))
- **Microsoft Advertising:** REST only. SOAP → REST transition window opened April 1, 2026; REST-exclusive features from October 1, 2026; SOAP fully deprecated January 31, 2027. ([Microsoft](https://about.ads.microsoft.com/en/blog/post/april-2026/evolving-the-microsoft-advertising-api-platform))
- **Reddit:** API open to all developers, no allowlisting; OAuth2 with access + refresh tokens. `conversion_pixel_id` required on ad groups and CBO campaigns since July 13, 2026. New campaign objective enums rolled out September 21, 2026 with legacy enums still supported — maintain a mapping table. ([Reddit Ads API](https://ads-api.reddit.com/docs/v3/))
- **Pinterest:** Ads API lives inside v5 Standard access; application requires a linked, compliant privacy policy; credentials must not be shared; bring-your-own-key apps must store end-user credentials locally, not server-side; rate-limit probing is prohibited without authorization. ([Pinterest guidelines](https://policy.pinterest.com/en/developer-guidelines))
- **Snapchat:** Marketing API open to all developers. Entities: Organizations → Ad Accounts → Campaigns → Ad Squads → Ads. Ad creation is `POST /v1/adsquads/{ad_squad_id}/ads`. Supports media upload, creative build, DPA from catalogs, custom audiences from hashed first-party data, lead gen forms, and hourly/daily/total reporting granularity. ([Snap docs](https://developers.snap.com/marketing-api/Ads-API/ads))
- **Amazon Ads:** Use Sponsored Products Campaign Management v3 (v2 is deprecated). Requires `Amazon-Advertising-API-ClientId` from a Login with Amazon account and a profile-scoped `Amazon-Advertising-API-Scope` header. ([Amazon Ads API](https://advertising.amazon.com/API/docs/en-us/guides/sponsored-products/overview))
