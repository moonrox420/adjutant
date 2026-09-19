# Adjutant

A local campaign control plane built from the supplied Adjutant requirements. The implemented path is
**brand → sourced evidence → human confirmation → manual or Ollama plan → signed human approval → deployment preflight**.
This is not the completed nine-channel advertising operator. See [BUILD_STATUS.md](BUILD_STATUS.md)
for the remaining product scope. No advertising platform accounts are connected and no live ads are launched.

## Open the installed workspace

```powershell
Set-Location C:\Users\droxa\adjutant
.\.venv\Scripts\python.exe scripts/upgrade.py
.\scripts\dev.ps1
```

Open <http://localhost:3000>. The initial account is `owner@adjutant.local`; its generated password is
in `.local/owner.password`. Existing passwords and workspaces are preserved by bootstrap. Consumer
accounts can now be created from the sign-in screen. Test data lives in the separate `adjutant_test`
database, not the working `adjutant` database.

The API listens on <http://127.0.0.1:8000>, with interactive documentation at
<http://127.0.0.1:8000/docs>. `/healthz` identifies the service; `/readyz` checks PostgreSQL access.
Logs are `.local/api.stderr.log`, `.local/web.stderr.log`, and `.local/postgres.log`. The startup
script opens no terminal windows. API startup supervises the local background consumer.
The separately credentialed gateway listens on <http://127.0.0.1:8002>. Its logs are
`.local/gateway.stdout.log` and `.local/gateway.stderr.log`. `upgrade.py` updates existing installations
without creating accounts, replacing passwords, or reinitializing PostgreSQL. Run it after updates
that introduce migrations or service permissions, then restart the API and gateway.

## Fresh Windows setup

**For a new installation only.** If this workspace already runs, use `scripts/dev.ps1` from
the preceding section. Do not rerun `initdb` against `.local/postgres`, empty that directory,
or start a second PostgreSQL server. Before reinstalling web dependencies with `npm ci`, stop
this project's Next.js server: Windows locks its loaded native SWC binary, and an interrupted
install can leave `node_modules` incomplete. Bootstrap preserves an existing account's password;
entering a different password at its prompt does not reset that account.

Repair or reinstall web dependencies with project-scoped process shutdown:

```powershell
Set-Location C:\Users\droxa\adjutant
.\scripts\stop.ps1 -Service web
Push-Location web
try { npm ci } finally { Pop-Location }
.\scripts\dev.ps1
```

`stop.ps1` checks each process's command line and creation time before stopping it, then verifies exit.
It targets this project's selected services and leaves PostgreSQL and Ollama running.

Prerequisites: Python 3.12+, Node.js 20.9+, PostgreSQL 18 with pgvector. The verified installation uses
Python 3.14.7, Node.js 20.20.2, and PostgreSQL 18.4.

```powershell
Set-Location C:\Users\droxa\adjutant
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m pip install -e . --no-deps
New-Item -ItemType Directory -Force .local | Out-Null
.\.venv\Scripts\python.exe -c "import pathlib,secrets; p=pathlib.Path('.local/postgres.password'); p.write_text(secrets.token_urlsafe(32)) if not p.exists() else None"
& 'C:\Program Files\PostgreSQL\18\bin\initdb.exe' -D .local/postgres -U adjutant_admin -A scram-sha-256 --encoding=UTF8 --pwfile=.local/postgres.password
& 'C:\Program Files\PostgreSQL\18\bin\pg_ctl.exe' -D .local/postgres -l .local/postgres.log -o '-h 127.0.0.1 -p 55439' -w start
.\.venv\Scripts\python.exe scripts/bootstrap.py --email owner@adjutant.local
Push-Location web
npm ci
Pop-Location
.\scripts\dev.ps1
```

Run `initdb` only for a new cluster. Bootstrap validates migration checksums, preserves existing
credentials and signing keys, and never deletes user data. Choose `--account-type agency` on initial
bootstrap for a multi-brand workspace. Business workspaces permit one brand. The operator bootstrap
creates a verified account; self-service registration requires email verification.

To apply an upgrade, install the updated dependencies, rerun bootstrap with your existing owner
email, and restart this project's API. Bootstrap will add the restricted worker role and its URL to
an existing `.env` without replacing other settings. Do not edit applied migration files.

## Accounts and delivery

Choose **Create an account**. Enter a full name, workspace name, email, and matching passphrase of
15–128 characters. Passwords are scrypt-hashed and preserved exactly, including spaces. New users
cannot sign in until they verify their email. Verification and recovery tokens are random, stored
hashed, expire after one hour, and work once. Links use the configured public origin and carry the
token in the URL fragment; the browser submits it explicitly rather than activating it on a GET.
Resending invalidates the preceding token. Duplicate signup and recovery requests use generic
responses, and rate limits persist in PostgreSQL.

This installation uses `ADJUTANT_MAIL_TRANSPORT=file`. The real consumer writes `.eml` messages to
`.local/mail`. Open the newest email with a mail reader or text editor and follow its account link.
The UI explicitly identifies local delivery: **nothing is sent to an external inbox in file mode**.
Mail files contain secret account links and must remain private. Token-bearing bodies are removed
from the database queue after successful delivery.

**Forgot password?** sends a recovery link. Resetting revokes all sessions and requests cancellation
of their generation work. **Account → Sign out** revokes the current session; **Sign out everywhere**
revokes every session for that user. Logout waits up to eight seconds for worker exit acknowledgements
and reports pending verification honestly. Other open tabs return to sign-in. Account controls work
on mobile. **Activity** shows jobs, Stop controls, actual exit verification, and consumer health.

Self-service signup creates a Business workspace with owner approval caps of $1,000/day and $30,000
total. Invitations, account conversion, email changes, OIDC, and MFA remain outside this implementation.
The locally provisioned `owner@adjutant.local` address is a development identity; use a valid email
address for self-service verification and recovery.

## Consumer deployment email configuration

Configure a real SMTP provider and HTTPS origin in the server environment:

```dotenv
ADJUTANT_PUBLIC_ORIGIN=https://your-adjutant-domain.example
ADJUTANT_SECURE_COOKIES=true
ADJUTANT_MAIL_TRANSPORT=smtp
ADJUTANT_MAIL_FROM=Adjutant <accounts@your-adjutant-domain.example>
ADJUTANT_SMTP_HOST=smtp.your-provider.example
ADJUTANT_SMTP_PORT=587
ADJUTANT_SMTP_SECURITY=starttls
ADJUTANT_SMTP_USERNAME=your-provider-login
ADJUTANT_SMTP_PASSWORD=your-provider-secret
```

Keep the restricted API and worker database URLs, or provision equivalent roles with
`scripts.bootstrap.provision_runtime` and `provision_worker` after migrations. Never put these
credentials in browser environment variables. Implicit TLS uses `SMTP_SECURITY=ssl` and the
provider's TLS port. Certificate verification is mandatory. Non-loopback consumer origins require
HTTPS, secure cookies, SMTP, and `ADJUTANT_WORKER_DATABASE_URL` at startup. External provider delivery
has not been exercised in this installation; local file delivery and retry behavior have been tested.

Mail delivery retries up to eight times with exponential backoff. Failures store the exception class,
not SMTP responses or credentials. File delivery uses a stable message ID and atomic replacement,
so retries produce one final file. SMTP is **at least once**: a crash after the provider accepts an
email but before commit can cause a duplicate. A stable Message-ID and single-use token limit the
consequences; there is no claim of exactly-once external email delivery. Fix delivery configuration
and request a fresh link after an exhausted or expired delivery. Operator diagnostics are in the
API log and the private `mail_outbox` table.

## Use the campaign workflow

1. Add a brand with a website, industry, and daily/monthly ceilings.
2. In **Brand intelligence**, optionally import its public homepage. Save useful facts with source
   URLs. Imported text stays unconfirmed; source URLs, retrieval time, and content hashes are retained.
3. Confirm the facts. Restricted verticals stay blocked even after confirmation.
4. In **Campaign plans**, create a plan manually or choose **Generate draft** with Local Ollama or Ollama Cloud.
5. Submit the exact revision to **Approvals**. Approve, reject with feedback, or request changes.
6. Review immutable history and jobs in **Activity**. Change ceilings under **Guardrails**.

Approval signs the exact plan revision. It does not launch a campaign. Plan edits void outstanding
tokens; new review requires a new revision. Brand fact changes require reconfirmation and invalidate
approvals. Expired or rejected revisions must be edited before resubmission.

The brand stop control cancels local generation, blocks planning/approval, and voids tokens. It does
not claim to pause remote ads. Brand-stop release is not implemented; use **Stop generation** to stop
an individual job while leaving the brand available for another draft.

## Local and cloud Ollama

Ollama's `/api/tags` and `/api/chat` endpoints supply models and generation. The Local provider
excludes cloud-backed models and models advertising only embedding capabilities. The installed local model is
`mirage335/Llama-3-NeuralDaredevil-8B-abliterated-virtuoso:latest`.

The generation form lets you select the provider for each request. These `.env` settings keep the
local endpoint and model independent from the cloud configuration:

```dotenv
ADJUTANT_OLLAMA_PROVIDER=local
ADJUTANT_OLLAMA_URL=http://localhost:11434
ADJUTANT_OLLAMA_MODEL=mirage335/Llama-3-NeuralDaredevil-8B-abliterated-virtuoso:latest
ADJUTANT_OLLAMA_CLOUD_MODEL=
ADJUTANT_OLLAMA_CLOUD_API_KEY=
```

For Cloud, create an [Ollama API key](https://ollama.com/settings/keys), enter it in
`ADJUTANT_OLLAMA_CLOUD_API_KEY`, and restart the API. The cloud model list comes from Ollama;
`ADJUTANT_OLLAMA_CLOUD_MODEL` optionally selects its default. Set `ADJUTANT_OLLAMA_PROVIDER=cloud`
only if you want Cloud selected by default. The application never automatically switches providers.
The current `.env` preserves your existing owner login credentials and leaves the cloud key empty.
After changing provider settings, run `.\scripts\dev.ps1 -Restart` from the project root to load them.

Cloud generation sends the brief, confirmed facts, and budget context to `https://ollama.com`.
Its bearer key is sent only to that fixed HTTPS origin, never to the browser, local Ollama, logs,
or process arguments. The owned worker receives it over its private parent pipe. Local generation
does not receive the cloud key. Redirect following is disabled.

[Ollama Cloud currently does not support structured outputs](https://docs.ollama.com/capabilities/structured-outputs).
Cloud requests therefore include the schema in the prompt and validate the returned JSON locally;
local requests additionally use Ollama's schema-constrained `format`. Invalid output is never saved.
Cloud authentication and error paths have automated contract tests; real cloud inference requires
your API key and has not been verified in this workspace.

## Generation and process lifecycle

Generation uses confirmed facts and budget ceilings, validates structured output, and permits at most
three schema attempts. Money uses fixed decimal strings; the provider schema avoids unsupported
lookahead expressions. The API independently validates precision, positivity, supported channels,
allocation sums, and ceilings. One run per brand may be active, with 30 starts per rolling day.

Each run has an owned subprocess and a 470-second deadline. Logout, password reset, session expiry,
job cancellation, and brand stop prevent late draft persistence and end that worker. The API records
the actual OS exit code and verification timestamp. A parent-pipe watchdog exits the child if its
API disappears. The background consumer marks jobs with heartbeats older than 30 seconds interrupted,
without inventing exit proof for an unavailable supervisor. Interrupted work is not automatically
re-executed; the user can request a new draft. Temporal recovery is not implemented.

Only the dedicated inference child is terminated. The shared Ollama service remains available.
Generation receives no platform credentials, tools, or spend permission. Process lifetime management
is not an operating-system security sandbox for arbitrary generated code; this application never
executes model-generated code.

## Spend gateway and deployment checks

The gateway uses `adjutant_gateway`, a restricted PostgreSQL role. It can read spend prerequisites,
lock same-brand authority, and append reservations. It cannot issue or change approval tokens,
delete reservations, or read login credentials, sessions, and mail. Public verification keys are
exported to `.local/approval-public-keys.json`; the gateway does not load the private signing key.
Core still issues approvals in-process; extracting an independent approval service remains required.

Gateway validation checks the Ed25519 signature, trusted key, signed/relational claim agreement,
expiry, approved revision hash, operation/channel scope, brand readiness, stop state, allocation,
current ceilings, cumulative commitments, and replay. Database locking serializes concurrent
reservations, and a database trigger independently enforces cumulative total and daily caps.
Brand and channel ceilings also include existing reservations from other approved plans. Reservations
remain counted until a reconciliation/release lifecycle is implemented; they do not disappear when a
token expires. This conservative boundary currently has no live platform egress.
The authenticated internal endpoints are `/internal/spend/validate` and `/internal/spend/reserve`.

Approved plan cards expose **Check deployment readiness**. This reads current prerequisites and
calls the gateway's validation endpoint without reserving authority. Missing channel access,
approved renditions, or an installed adapter remain blockers. A passing spend check does not prove
that platform deployment is available. Live adapters and verification at actual advertising egress
are not implemented yet; no live ad writes are enabled.

## Durable background consumer

Bootstrap creates a restricted `adjutant_worker` role and saves its password in `.local/worker.password`.
The API supervises a separate consumer process through `ADJUTANT_WORKER_DATABASE_URL`. Its role can
access mail and narrow activity/recovery functions, but cannot read credentials, sessions, or raw
brand tables. Application runtime roles reject superuser and BYPASSRLS privileges.

The local activity consumer commits a unique receipt and indexed progress marker in one transaction.
Competing workers skip locked rows. Killed processes release uncommitted work for replay; committed
events are not applied twice. Consumer exits are verified before replacement, and missing heartbeats
trigger restart. The UI shows recent receipts and health. This projection does not mark events
published to Redpanda or perform advertising platform effects. Broker relay, additional domain
consumers, and broker DLQ/replay tooling remain future work.

If the supervisor loses its database connection, it still terminates its owned child. When the
exit acknowledgement cannot be persisted, that consumer record retains null exit fields even
after a replacement starts. A missing acknowledgement is not proof that the process is alive
or dead. The regression suite kills the supervisor's real PostgreSQL connection, blocks its
reconnection temporarily, and independently observes child exit and replacement recovery.

The worker's only callable application `SECURITY DEFINER` functions are `consume_activity_batch`
and `reap_abandoned_jobs`. They intentionally operate across tenants but return integer counts.
Tests constrain their changes, batch bounds, pinned search paths, temporary-table shadowing,
function replacement permissions, and direct access to private tenant data.

The nine events currently emitted by the API have a bundled registry in
`src/adjutant/event_registry.json`, included in the Python wheel. UUID and date-time formats are
validated using JSON Schema's format dependencies. `ADJUTANT_REGISTRY_PATH` can override the
packaged default. The original full-platform design registry is absent from this checkout;
the bundled runtime contracts do not claim to restore every future platform event.

## Verification

The backend suite requires a real `adjutant_test` PostgreSQL database; it refuses a working-database URL.
It covers tenant isolation, approval integrity, registration, expiry/replay, session revocation,
real subprocess cancellation, consumer crash windows, durable deduplication, and mail retry behavior.

```powershell
Set-Location C:\Users\droxa\adjutant
.\scripts\check.ps1
Push-Location web
npx playwright install chromium
npx playwright test
Pop-Location
```

Playwright starts isolated API/console servers on ports 8001/3001. It exercises the complete account
journey, the existing brand-to-approval workflow, and logout during active generation. The last
workflow checks the running worker's PID, account-page cancellation acknowledgement, rejected old
session, persisted OS exit proof after signing in again, and absence of a late draft.
Account test emails go to `.local/browser-mail`.
Run browser and database suites sequentially because both use the dedicated test database. Screenshots
are written under `.local`; failure traces are under `web/test-results` and the HTML report is under
`web/playwright-report`. Browser and backend lifecycle tests use controlled HTTP inference endpoints
with real worker processes for deterministic cancellation. They do not verify model quality or live
Ollama availability. The live canary uses Ollama:

```powershell
.\.venv\Scripts\python.exe scripts/live_canary.py --model mirage335/Llama-3-NeuralDaredevil-8B-abliterated-virtuoso:latest
```

The live canary writes only test-database records. Inference success does not prove live ad delivery.
Two dependency deprecation warnings currently come from FastAPI/Starlette's HTTPX test adapter.

GitHub Actions provisions separate PostgreSQL services for backend and console jobs, installs Chromium,
and runs all three browser workflows after the console build. Browser failure diagnostics are retained
for seven days. These gates are configured in the repository; local Windows results do not establish
that a particular commit has passed GitHub Actions.

BallPython 2.0.0 was also run locally in read-only mode:

```powershell
.\.venv\Scripts\ballpython.exe check --json src scripts tests
.\.venv\Scripts\ballpython.exe scan --json src scripts tests
.\.venv\Scripts\ballpython.exe taint --json src scripts
```

Security and taint scans returned no findings. The code check reported twelve unresolved-import
diagnostics: eleven references to Python's `__file__` and one forward reference to `issue_token`,
which is defined in the same module. These were reviewed as false positives; no automatic fixes
were applied. BallPython is an optional local analysis tool, not a replacement for the runtime tests.

## Deployment boundaries

The API and console include Dockerfiles and a GitHub Actions workflow for source, build, database,
and browser checks.
Container builds and a remote CI run have not been verified in this environment. Provision PostgreSQL,
apply checksum-validated migrations, and mount the existing Ed25519 signing key readable by the API
user. Do not regenerate a deployed approval key silently. Database backups, HTTPS termination,
retention, SMTP credentials, and production monitoring require operator configuration.

The supplied specification documents remain the source of product scope. Temporal, Redpanda,
platform adapters, creative rendering, production spend egress, measurement, optimization, and
infrastructure are not represented by fake service responses. See [BUILD_STATUS.md](BUILD_STATUS.md).
# adjutant
