# Runtime verification record — September 22, 2026

## September 22 continuation

- `.local/test-concept-full.log`: 259 backend checks passed before the subsequent account,
  reconnection and campaign changes.
- `.local/test-reconnection-concepts.log`: 80 checks passed using the real database, signer
  and gateway for the reconnection-generation and first-launch invalidation paths.
- `.local/test-account-conversion-roles.log`: 7 account-conversion checks passed, including
  a 50-brand round trip, seat/settings preservation, immutable history and denied roles.
- `.local/browser-conversion-concepts-final.log`: 8 browser workflows passed in 36.0 seconds.
- `.local/real-concept-copy-final.json`: five validated, distinct bundles from the real
  configured Ollama model. Initial long-copy and duplicate-concept failures are retained in
  `.local/real-concept-copy.log` and `.local/real-concept-copy-second.log`; bounded repair
  passed in `.local/real-concept-repair.log`. This does not verify Google image generation.
- `.local/test-campaign-worker-final.log`: 65 tests passed in 86.99 seconds. Includes actual
  PostgreSQL execution, real TCP HTTP to an explicitly simulated Meta server, journal recovery
  after a lost response, independent-read mismatch rejection, cancellation, shutdown recovery,
  source ancestry validation, creation limits and rejection of false completion records.
- `.local/browser-campaign-build-first.log`: 1 test passed in 10.0 seconds. The campaign
  setup form queued a build through actual APIs, cancelled it, and retained its state after
  reload. Provider execution is deliberately separate from this browser queue test.
- TypeScript and both channel-parity checks passed after the campaign form was added.

No real ad-provider campaign, Google image or external email was created in these checks.
Earlier evidence below is historical and does not certify later changes.

This is an execution record, not a product-completion claim. The requirement matrix remains
the source for PASS, FAIL and BLOCKED_EXTERNAL_AUTH. No live advertising-platform mutation,
Google image generation, or external SMTP delivery was verified.

## Managed-campaign pause verification

- Complete backend regression after the test-isolation correction: **252 passed**, four
  dependency warnings, 227.17 seconds; `.local/test-full-remote-stop-final.log`.
- Final stop/readiness regression: **21 passed**, three dependency warnings, 9.02 seconds;
  `.local/test-remote-stop-readiness.log`. Stopping the remote worker makes readiness return
  HTTP 503 instead of reporting the service ready. The current core API includes this check.
- The API commits stop runs and per-campaign work to PostgreSQL. The background runner recovers
  queued/running work using session advisory locks and performs bounded parallel provider calls.
  Remote state is independently fetched after mutation; failures never write a paused local state.
- `tests/test_remote_stop.py` covers protocols for all ten channels, wrong account/identity,
  authorization failure, partial provider failure, redirect rejection, unconfirmed mutations,
  missing credentials across all channels, previously selected accounts, missing parents, tenant
  boundaries, and recovery of a persisted running job. **20 passed** in 7.97 seconds;
  `.local/test-remote-stop-tcp.log`.
- The recovered-job case uses real TCP HTTP against a local provider emulator. Other protocol
  cases explicitly use controlled responses. Neither is a live advertising-platform test.
- The combined stop/account/HTTP/control-plane regression passed **71 tests** in 51.03 seconds;
  `.local/test-remote-stop-first.log`.
- All **six Playwright tests passed** in 31.9 seconds; `.local/browser-remote-stop-final.log`.
  The new browser path exercises the global stop control, named authorization failures for all
  ten channels, report persistence after reload, and local release. A failed assertion initially
  matched a notice too narrowly because its container includes a dismiss button; the assertion
  now checks the status region's content. No application failure was hidden by that change.
- Next.js production build passed; `.local/web-build-remote-stop.log`. TypeScript, Prettier,
  Python/TypeScript channel-boundary checks, and both Node checker tests passed.
- Migrations 027–029 bind canonical asset metadata and placement specifications into launch
  review, persist remote stop jobs, and permanently void unconsumed launch tokens on stop.
  They are applied to the working database. The API, gateway and signer were restarted and
  all three authenticated readiness probes returned HTTP 200.
- No ad tokens, brand image keys, or environment Gemini key were available in the working
  configuration when rechecked. No real advertising mutation or paid enable operation occurred.

The first full post-change run found a test-isolation defect: the new canonical-placement test
left a changed specification active in the shared test registry. It caused 17 dependent fixture
errors and one render failure. The two synthetic records were retired only in `adjutant_test`,
and the test now inserts its isolated specification already retired. Validation was not weakened.

The whole kill-switch requirement remains FAIL: only recorded campaign roots and their managed
descendants are covered. Remote account-wide inventory, child-only control, Amazon Sponsored
Display, exact revert call sequences, remote resume, and real-provider verification remain absent.

## First-launch authorization verification

The recurring approval queue has been removed from the console. First-launch review displays
the plan, selected accounts, attached copy/renders, and all guardrails. The signer and gateway
run as separate database identities; only the gateway can consume the signed authorization.
Account grants survive subsequent plan revisions. No platform execution is implied by a grant.

- Complete backend suite before remote-stop changes: **229 passed**, four dependency warnings,
  191.91 seconds; `.local/test-full-launch-acceptance.log`.
- Final regression after binding account selection into the review hash: **17 passed**,
  three dependency warnings, 32.19 seconds; `.local/test-launch-scope-final.log`.
- Canonical asset/placement review identity regression: **19 passed**, 36.37 seconds;
  `.local/test-review-assets.log`. A later full run exposed the test isolation issue recorded above.
- Actual PNG/SVG files are rendered and attached before authorization in these tests. Their
  source image is explicitly a local test fixture; this does not verify Gemini generation.
- Real HTTP is exercised between the core API, signing service, and gateway. Tests verify
  signature consumption, duplicate-request reconciliation, concurrent single-use enforcement,
  persisted replay security alerts/outbox events, atomic token invalidation on plan edits,
  account changes after review, guardrail versions, revocation, tenant isolation, blocked claims,
  and preview-file integrity failures.
- Production Next.js build and TypeScript compilation passed; `.local/web-build-launch.log`.
- Final full Playwright suite: **five passed**, 27.9 seconds;
  `.local/browser-launch-final.log`.
- The browser workflow exercised review without an account, actionable denial, all-guardrail
  editing and reload persistence, deployment preflight, audit export, and mobile overflow.
  `.local/browser-launch-workflow.log`: one passed, 11.8 seconds.
- All 75 Python source/test/script files pass Ruff formatting; lint, Python/TypeScript channel
  parity, both Node checker tests, and console Prettier checks pass.
- Forward migrations 022–026 are applied to both the isolated test database and the working
  database. The local API, gateway and signer were restarted. All three `/readyz` checks return
  HTTP 200, and the running API exposes the guardrail and first-launch routes.

The first full regression run detected excessive signer access to the legacy connection secret
column, a missing gateway audit-return privilege, and excess worker access to two trigger
functions. Grants were narrowed/corrected. The existing security tests were retained unchanged;
the 26-test focused security suite and the subsequent complete suite passed.

## Earlier automated checkpoint

| Check | Result | Local evidence |
|---|---|---|
| Complete Python suite | **212 passed**, 4 dependency deprecation warnings, 202.90 seconds | `.local/test-full-v2-05.log` |
| Final Studio/gateway regression after preflight correction | **40 passed**, 56.08 seconds | `.local/test-preflight-final.log` |
| Playwright browser suite | **5 passed**, 36.2 seconds | `.local/browser-v2-acceptance.log` |
| Next.js production build and TypeScript compilation | Passed | `.local/web-build-v2-final.log` |
| Ruff lint | Passed | `ruff check src scripts tests` |
| Ruff format | 72 files passed | `ruff format --check src scripts tests` |
| Python adapter-boundary check | Passed; 7 checker tests included in Python suite | `scripts/check_channel_parity.py` |
| TypeScript adapter-boundary check | Passed; 2 Node tests passed | `web/scripts/check-channel-parity.mjs` |
| Console formatting | Passed | Prettier check over app, components, lib, tests and configuration |

The preceding full run had one failure in a newly added test that expected FastAPI's default
`detail` error shape. The API intentionally returns its established `error.code/message` shape.
The assertion was corrected, and the complete 212-test suite was rerun successfully. No failing
test was skipped or disabled.

## Actual local effects exercised

- Real PostgreSQL migrations, forced RLS, non-superuser runtime enforcement, tenant isolation,
  version conflicts, encrypted credential storage, token consumption and immutable audit behavior.
- Actual worker subprocess death and restart, checkpoint recovery, concurrent worker ownership,
  idempotent local object writes and supervised consumer recovery.
- Studio enqueue, scheduler execution, copy-checkpoint recovery without repeating copy, cancellation
  before provider work, and termination/reaping of the actual dedicated inference child.
- Logout, logout-all and local brand stop during Studio execution: the response includes Studio
  jobs and reports cancellation verified only after the job has stopped. Completed jobs cannot be
  relabelled cancelled by a late request. Disabled workers reject enqueue.
- Real PNG/SVG rendering at four aspect ratios, measured overflow rejection, text contrast checks,
  asset downloads, campaign-plan creative attachment and audit writes.
- Deployment preflight distinguishes attached assets from channel-validated/approved creative.
  A Studio attachment alone does not pass creative approval or overall launch readiness.
- API context editing with preserved prior versions, encrypted per-brand image configuration,
  changed-model rejection for queued jobs, and blocked-claim checks before rendering/persistence.
- All ten platform setup flows through the API with actual encrypted database writes, actor-bound
  single-use OAuth state, expiry/replay rejection, account selection, refreshed-token persistence
  after a failed discovery request, local disconnect and secret-safe error responses.
- Provider HTTP error handling and protocol construction, including Google manager hierarchy
  pagination, Reddit shared-account discovery/pagination, revocation requests and log redaction.
  Provider HTTP responses in these tests are controlled test transports, not live account evidence.
- A real local Ollama copy-generation canary produced a validated Meta/Google/TikTok bundle;
  the saved output is `.local/studio-copy-canary.json`. This does not verify Google visuals.

## Browser paths exercised

1. Signup with Business/Agency equally presented and neither selected; email verification through
   the actual local mail queue; login; mobile logout; password recovery; cross-tab revocation.
2. Ad Studio on an unconfirmed brand; Meta/Google/TikTok preview editing and saving; image-generation
   error display. Studio provider/job responses are isolated browser fixtures for this test.
3. All ten developer application forms; real encrypted API persistence; no false authorized status;
   reload; OAuth return navigation selects the original brand even when another brand exists.
4. In-flight strategy generation and logout, with process-exit verification and no saved late draft.
5. Brand edits, plan creation, legacy signed approval, reload persistence, deployment preflight and
   audit export. This verifies legacy behavior, not v2 autonomous activation or remote deployment.

## Working runtime

Migration 021 was applied through `scripts/upgrade.py`; the existing database, accounts, passwords
and campaigns were preserved. Only the API was stopped and restarted. After restart:

- `GET http://127.0.0.1:8000/healthz`: HTTP 200, service alive.
- `GET http://127.0.0.1:8000/readyz`: HTTP 200, including active Studio supervisor check.
- OpenAPI includes Studio jobs, platform authorization and rendition rendering routes.
- Workflow execution is enabled. Gemini key is absent. Configured image model is the legacy
  `imagen-3.0-generate-002`. Email transport is `file`; SMTP host is absent.
- A final working-database count found zero saved visual-provider settings, zero channel developer
  applications, zero channel tokens and zero selected ad accounts. No secret values were printed.
- Evidence containing configuration-presence booleans only: `.local/runtime-v2-final.json`.

## External verification

| External system | Verification performed | Missing evidence |
|---|---|---|
| Meta, Google Ads, YouTube, TikTok, LinkedIn, Microsoft, Reddit, Pinterest, Snapchat, Amazon Ads | Official provider contracts inspected; local protocol tests only | No authorized-account consent, real campaign creation, mutation, read-back or metrics run |
| Google image provider | Installed SDK request and image-byte validation with test transport | No real authenticated image generation |
| Email provider | Local queue/consumer delivery and retries; SMTP boundary tests | No authenticated external mail delivery |
| Remote CI | CI definitions updated and checks run locally | No remote CI run for these uncommitted changes |

## Reproduce locally

Run backend and browser tests sequentially: both use the dedicated test database. Commands below
are PowerShell commands from the repository root. They do not test live ad accounts.

```powershell
.\.venv\Scripts\python.exe -m pytest -q --basetemp=.local/verification-tests -p no:cacheprovider
.\.venv\Scripts\python.exe -m ruff check src scripts tests
.\.venv\Scripts\python.exe -m ruff format --check src scripts tests
.\.venv\Scripts\python.exe scripts/check_channel_parity.py
Set-Location web
npm run build
node --test scripts/check-channel-parity.test.mjs
node scripts/check-channel-parity.mjs
npm test
```

The complete missing internal work is listed in [BUILD_STATUS](../../BUILD_STATUS.md) and the
[traceability matrix](traceability.md). External setup is consolidated in
[external-authorization.md](external-authorization.md).
