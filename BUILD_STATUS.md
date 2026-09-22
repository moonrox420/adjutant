# Adjutant v2.0 execution status

## Current implementation checkpoint — September 22

The supported image default is `gemini-3.1-flash-image`; retired Imagen models are rejected.
Studio jobs now generate five concept-specific copy/image sets with individual checkpoints,
duplicate-copy repair, four rendered sizes per concept, saved inline edits, cancellation,
and failed-job retries. The actual local Ollama model produced five distinct validated copy
bundles. Google image generation has not been authenticated.

Business/Agency conversion preserves brands, seats and white-label settings, with immutable
history and database-enforced owner/admin authorization. Reconnecting an ad account increments
its authorization generation and requires new first-launch consent; previous grants remain
immutable history.

The Meta square Facebook feed construction path now runs as a durable campaign worker. It
uploads the stored PNG, creates the campaign/ad set/creative/ad in paused state, checks remote
identity, ancestry, copy, image association, targeting and budgets, and persists each write
before proceeding. A lost create response reconciles by the operation's unique name; it does
not repeat the POST. The form reaches the actual API and database, and records appear in Jobs.
This is not full Meta lifecycle completion: launch/resume, metrics, remaining formats and
objectives, comprehensive quota handling and live provider verification still require work.
The other nine channels' construction paths remain internal work.

Evidence: 65 Studio/launch/campaign checks passed, including real PostgreSQL and simulated
provider HTTP; the deployment browser workflow passed; the preceding eight-workflow browser
run passed. These are local execution results, not authenticated platform results. The full backend run passed 278 checks; after the last recovery change, 32 relevant checks
passed. All nine browser tests and the production Next build passed. Migrations through 037
are applied to both databases, and the restarted API and running web server return HTTP 200.

The matrix now has 489 rows, including all 25 requested operations for each platform, and
records the additional UI/API/domain/persistence/job/provider/authentication/evidence columns.
Its JSON is the state-count source. Work continues against every internal FAIL row.

The product is **not complete**. The v2 specification governs the work. The console now uses
account-bound first-launch review instead of the recurring approval queue. Signed authorization
and persistent grants are implemented. Durable managed-campaign pause calls and remote state
read-back are now wired; full deployment and the autonomous runner remain incomplete.
Legacy approval APIs remain for existing records. No advertising platform has passed
live campaign verification.

The [requirement traceability matrix](docs/verification/traceability.md) and its
[JSON source](docs/verification/traceability.json) record PASS, FAIL and BLOCKED_EXTERNAL_AUTH.
The [consolidated external setup list](docs/verification/external-authorization.md) separates
credentials and consent from missing internal implementation.

## Execution paths changed and exercised

- A global pause control commits a durable stop request, stops local jobs, permanently invalidates
  unconsumed launch tokens, and dispatches managed campaigns to provider pause endpoints in parallel.
  Paused state is read back independently before persistence. Reports survive reloads and identify
  each account/campaign with missing credentials, failed calls, missing ancestry, or unverified state.
  Campaigns outside Adjutant's inventory are not covered; remote resume is not implemented.
- Campaign pause/read-back protocols exist for all ten channels. Amazon's implemented campaign
  families are Sponsored Products and Sponsored Brands. Provider contract tests do not establish
  live operation. A recovered running job was exercised through real TCP HTTP against an explicitly
  local provider emulator, followed by SQL state, audit and activity persistence.
- First-launch review includes attached creative, the plan, selected accounts and guardrails.
  The isolated signing service issues a scoped, expiring authorization. The public-key-only gateway
  consumes it once; replay produces an immutable security alert and transactional outbox event.
  A consumed account grant survives later plan edits. Changed unconsumed plans, creative,
  guardrail versions, account selection, revoked approvers and local stops reject consumption.
- All nine guardrail fields can be edited with version checks. The database validates initial
  launch caps and channel shares, increments guardrail versions, synchronizes legacy ceilings,
  and rejects blocked claims in Studio writes. Execution-time rate, growth and escalation checks
  still require the missing runner and cannot be claimed operational from these settings.
- URL/prompt generation no longer requires brand confirmation or source citations. Meta, Google
  and TikTok copy are validated and stored with revisioned edits; real local Ollama inference was
  exercised. Google image generation is wired but has not succeeded against a real Google account.
- Studio jobs persist their brief, copy checkpoint, image checkpoint, attempts and state. A
  PostgreSQL advisory lock prevents two workers from owning the same job. Cancellation terminates
  the dedicated copy process, waits for it to exit and records the result. Completed drafts and
  job completion commit together. Session revocation and local stop include Studio cancellation.
- Generated background imagery is composed with separate editable text into real PNG and SVG
  output at 1:1, 4:5, 9:16 and 16:9. Text bounds and contrast are checked. Renditions can be
  downloaded and attached to a campaign plan's creative records. Full platform conformance is absent.
- Brand understanding has an editable, optimistic-versioned API and UI. Google provider settings
  are encrypted per brand. A queued job rejects an intervening image-model change.
- All ten channel cards have developer application setup, OAuth state/consent handling, encrypted
  token storage, discovery, selection, refresh/reauthorization and disconnect handling. Protocol
  tests isolate provider responses. This does not establish live account access or campaign support.
- Google account discovery walks manager hierarchies. Reddit discovery follows paginated business
  and account queries. Discovery errors are saved; tokens and OAuth query credentials are redacted
  from provider/access logs. Callback navigation restores the brand that initiated consent.
- Channel objective arrays are decoded correctly. Connection and key-presence labels distinguish
  observed account access from saved configuration. Signing-service status is probed. Email status
  identifies file delivery when external SMTP delivery has not been selected.
- Signup presents Business and Agency with equal radio controls and no default. The API requires
  an explicit account type. CI checks reject channel-specific branching outside adapters.

## Remaining internal defects

- Production campaign creation, creative association, targeting, budgets, launch, general status
  synchronization, metrics and error synchronization are missing across the required channels.
- Once-per-brand/new-channel activation, all nine database-enforced autonomy guardrails, scoped
  escalations and reversible remote actions are not connected to an execution engine.
- Kill-switch coverage is limited to recorded campaign roots and their managed descendants.
  Account-wide remote inventory discovery, isolated child-object control, Amazon Sponsored Display
  pause support, exact reversible call sequences, and remote resume remain internal defects.
  No provider pause has been verified against an authorized real ad account.
- Hourly ingestion, comparable metric history/backfill, fatigue/winner detection, creative refresh,
  budget reallocation and the unattended autonomous execution loop are incomplete.
- Five distinct creative concepts, the complete placement registry, platform safe areas, all
  format conformance and video generation remain incomplete.
- Agency conversion/rollups/bulk operations, jurisdictional disclosures, full policy rejection
  handling, billing/dunning and weekly result summaries remain incomplete.
- Exhaustive role-by-endpoint and tenant-table CRUD verification, all required live provider
  conformance runs and the seven-day unattended acceptance run have not been completed.

These are FAIL records, not credential blockers. No supplied API key can substitute for them.

## External authorization

No ad-platform application credentials/consent, Gemini key, or SMTP credentials were available in
this runtime. The current Google SDK cannot run the requested legacy Imagen model through its
Developer API; the UI exposes supported Gemini image-model selection. External setup and live
verification remain necessary after the corresponding internal path is complete.

## Local verification

Evidence is recorded in docs/verification/runtime-evidence.md. Backend tests use the dedicated
adjutant_test PostgreSQL database. Browser tests use ports 3001/8001 and that dedicated database.
The working database and user data have been preserved. Forward migrations are applied through
scripts/upgrade.py; previously applied migration files are not edited.

Changes remain uncommitted. A local passing test/build does not establish a successful remote CI
run or final PRD acceptance. Historical v1 documentation deletions and earlier foundation changes
were already present in the working tree and are included in git diff statistics.
