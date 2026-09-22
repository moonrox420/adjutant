# Adjutant v2.0

Adjutant is an autonomous ad runner. The governing specification is
[Adjutant — Ad Runner: Product Spec and Build Plan](docs/specification/Adjutant%20%E2%80%94%20Ad%20Runner%20%20Spec%20and%20Build%20Plan.md),
version 2.0, September 21, 2026, by Dustin Hill. Build in dependency order, S0–S14.
The v1.0 roadmap and its recurring approval and source-URL gates are superseded.

Approval is required at a brand's first launch and at the first launch on a newly connected channel.
Subsequent operation is autonomous within database-enforced guardrails. Those launch and optimization
slices have not been implemented under v2.0 yet. Image generation in S3 uses Google Gemini
through `google-genai`; it must preserve copy as scene-graph text layers.

## Ad Studio integration

Current evidence and defects are tracked in the [requirement matrix](docs/verification/traceability.md).
The [consolidated authorization checklist](docs/verification/external-authorization.md) covers all
ten ad platforms, Google visuals and external email. This repository does not yet provide the
complete autonomous runner; missing internal campaign/loop implementations remain FAIL.

Open **Campaign plans → Ad Studio** in the existing console. Enter a public URL or a campaign
prompt and select **Generate**. Adjutant reads the URL automatically, generates Meta, Google,
and TikTok copy with the configured local Ollama model, and calls Google for each concept's image.
Studio queues five distinct creative directions, persists each completed concept, and renders
each to 1:1, 4:5, 9:16 and 16:9. The concept selector opens each saved ad for editing and attachment.
Retrying a failed job preserves its completed concepts and image checkpoints.
There is no brand-confirmation or citation gate. Copy and images are persisted; **Save copy edits**
stores inline changes with revision conflict checks. TikTok output is a written concept, not video.

Set `GEMINI_API_KEY` to your actual Google key in the server `.env` file and keep
`ADJUTANT_OLLAMA_MODEL` set to an installed completion model. The key stays on the server.
The default is `gemini-3.1-flash-image`, a
[supported Gemini image model](https://ai.google.dev/gemini-api/docs/models/gemini-3.1-flash-image).
The installed `google-genai` SDK uses `generate_content` with image output and an explicit
aspect ratio. Retired Imagen and non-image model names are rejected before generation.
To override the model, configure:

```dotenv
ADJUTANT_GEMINI_IMAGE_MODEL=gemini-3.1-flash-image
```

Image failures return actionable errors; no placeholder image or synthetic success is substituted.
No Gemini key was configured during integration verification. Provider-contract tests use test
responses and are not evidence of live Google generation.

Apply the forward migration and restart after configuration changes:

```powershell
Set-Location C:\Users\droxa\adjutant
.\.venv\Scripts\python.exe scripts/upgrade.py
.\scripts\dev.ps1 -Restart
```

The console is at <http://localhost:3000>. The authenticated API exposes:

- `POST /api/studio/jobs` with `brand_id`, `url_or_prompt`, and UUID `request_key`; the console uses
  durable jobs that recover copy/image checkpoints after worker restart.
- `GET /api/brands/{brand_id}/studio/jobs/{job_id}` and `DELETE` at the same path for status/cancellation.
- `POST /api/campaigns/generate-quick` remains available for synchronous compatibility.
- `GET /api/brands/{brand_id}/studio/latest` to recover the latest saved bundle.
- `PUT /api/brands/{brand_id}/studio/{draft_id}` to save copy with `expected_revision`.
- `POST /api/brands/{brand_id}/studio/{draft_id}/render` creates real PNG and editable SVG renditions.
- `POST /api/brands/{brand_id}/studio/{draft_id}/attach` connects the current revision to a plan.
- `GET/PUT /api/brands/{brand_id}/understanding` reads or versions edits to brand understanding.
- `GET/PUT /api/brands/{brand_id}/visual-provider` reads model/key-presence metadata or saves encrypted settings.
- `POST /api/brands/{brand_id}/kill` stops local work and durably requests provider pauses for
  recorded campaign roots; the response identifies verified and unconfirmed results.
- `GET /api/brands/{brand_id}/remote-stop` returns the persisted per-campaign pause report.
- `POST /api/brands/{brand_id}/resume` releases the local stop after pause processing completes;
  it does not enable remote campaigns.

The Channels screen exposes developer application setup, OAuth consent, account discovery and
selection for all ten platforms. Account access is reported only after provider discovery.
No platform campaign-deployment operation has been live-verified. The job monitor includes
Studio jobs, whose cancellation is included in logout and local stop verification.

URL ingest retains the existing public-address and redirect validation. Blocked phrases are
checked before image generation and before persistence. Automatically accepted brand context is
versioned without inventing human sign-off. Generation is limited to one active request per brand
and 30 daily requests. Durable jobs resume from persisted checkpoints. An explicitly disabled
workflow worker rejects queued generation rather than accepting work it cannot execute.

## S0 foundation

The foundation provides one HTTP service, PostgreSQL migrations, a durable checkpoint workflow,
local immutable object storage, envelope-encrypted credentials with a random key per brand, and
JSON request logs with trace IDs. It does not launch ads. See [BUILD_STATUS.md](BUILD_STATUS.md)
for acceptance evidence and dependency gates.

## Start locally

Prerequisites: Python 3.12+, PostgreSQL 18 with pgvector installed, and GNU Make for `make up`.
PostgreSQL tools can be on PATH; the launcher also recognizes the standard PostgreSQL 18 Windows
and Debian installation directories. A fresh checkout automatically creates `.venv`, installs
`requirements.lock`, initializes PostgreSQL, runs migrations, provisions a restricted application
role and an owner, creates encryption authority, and starts the HTTP service with its workflow worker.

```sh
make up
```

On Windows without Make, the identical entry point is:

```powershell
Set-Location C:\Users\droxa\adjutant
python scripts/up.py
```

An already active virtual environment is reused and must have the project dependencies installed.
Open <http://127.0.0.1:8010/docs>. The initial owner is `owner@adjutant.local`; its generated password
is in `.local/runner/owner.password`. `/healthz` checks the process and `/readyz` checks PostgreSQL
and the workflow worker. The worker runs inside the HTTP service. No approval service, gateway,
frontend, or advertising account is required for S0.

The new cluster uses port 55440 and `.local/runner/postgres`. It is separate from the pre-existing
v1 installation on port 55439. Existing passwords, keys, and databases are never overwritten.
Ctrl+C stops the HTTP service; PostgreSQL and all persisted state remain. Start again to resume work.
Stop only the new cluster when needed:

```powershell
& 'C:\Program Files\PostgreSQL\18\bin\pg_ctl.exe' -D .local/runner/postgres -m fast -w stop
```

## Exercise a durable workflow

Sign in through `POST /api/auth/login`, read your account ID from `GET /api/me`, and create a brand
with `POST /api/brands`. Send `X-Adjutant-Client: console` and `Origin: http://127.0.0.1:8010` on writes;
retain the session cookie from login. The OpenAPI document describes the request fields.

- `POST /api/brands/{brand_id}/workflows`: supply a UUID `request_key` and `delay_seconds` from 0 to 300.
  Retrying the same key and input returns the same workflow; changing the input returns HTTP 409.
- `GET /api/brands/{brand_id}/workflows/{id}`: inspect durable progress and the originating trace ID.
- `GET /api/brands/{brand_id}/workflows/{id}/result`: retrieve the persisted result after completion.
- `PUT /api/brands/{brand_id}/credentials/{name}`: owners/admins store a `value` encrypted under the
  brand's key. The endpoint never returns plaintext. Credential reads are internal operations only.

The demonstration workflow commits a waiting checkpoint, then writes a content-addressed result and
commits its completion checkpoint. Row locks and unique checkpoints prevent competing workers from
committing a step twice. Process death releases uncommitted locks; restarting resumes persisted work.
An object written before a database rollback is reused on retry. This proves local step recovery;
external advertising mutations and their idempotency contracts belong to S4 and later slices.

## Storage and secrets

Assets are stored under `.local/runner/objects/{brand_id}/{sha256}`. Writes use a temporary file,
flush, and atomic replacement; reads verify hashes and reject traversal. The backend is a local
filesystem object store, not a cloud dependency.

Each brand gets a random AES-256-GCM data key. That key is wrapped by the local master key in
`.local/runner/tenant-master.key`. Brand and credential identity are authenticated encryption context,
so moving ciphertext between brands or names fails authentication. Database rows contain ciphertext
only. The master key is outside PostgreSQL; back it up with the database and preserve it across restarts.
Secret files use owner-only creation permissions on POSIX and inherit the workspace ACL on Windows.

Logs contain structured request metadata and generated trace IDs, excluding bodies, cookies, query
strings, and exception details. HTTP access logging is disabled in the S0 launcher. The result endpoint
checks tenant access before reading a stored object.

## Verification

The empty-cluster startup check runs the same launch path, signs in through real HTTP, writes and
reads an encrypted credential, runs a workflow to completion, checks retry identity and stored output,
and shuts down the test service and cluster:

```powershell
.\.venv\Scripts\python.exe scripts/up.py --check --state-directory .local/runner-smoke --port 8011 --db-port 55441
```

In a clean environment, CI runs `make up UP_ARGS=--check` with PostgreSQL 18 and pgvector. The separate
backend job applies migrations to an empty test database and runs the regression suite. The foundation
tests kill real worker processes both between steps and after object write but before database commit.

The regression suite uses the existing dedicated `adjutant_test` database provisioned by
`scripts/bootstrap.py`; it refuses the working database. In this installed workspace:

```powershell
.\.venv\Scripts\python.exe -m pytest -q --basetemp=.local/pytest-v2 -o cache_dir=.local/pytest-v2-cache
.\.venv\Scripts\python.exe -m ruff check src scripts tests
```

Use a fresh `--basetemp` directory if permissions from a different Windows identity prevent reuse.
The S0 test module is `tests/test_foundation.py`. A passing local run is not a remote CI result.

## Existing installation

The existing console and its database remain available through `scripts/dev.ps1`. Ad Studio removes
the confirmation and citation requirements for generation. Older launch approval behavior has not
yet been migrated to the v2 autonomy model. Applied migrations are historical records and remain
immutable; future slices change behavior through new migrations and focused implementation changes.
The former unregistered quick-generation stub has been replaced by `src/adjutant/campaign_api.py`
and registered on the existing FastAPI app. Ad Studio does not create approval queues or publish ads.
