# Build status

This repository began with specification documents and no application source. Version 0.1.0 is a
working local **brand → evidence → confirmation → draft plan → signed human approval** path.
It is not Phase 0 complete, a closed-alpha release, or the finished 199-ticket product.

## Verified on Windows, 2026-09-19

- Backend: 87 tests passed against dedicated PostgreSQL `adjutant_test`, including native process
  cancellation/recovery, gateway isolation, replay, and concurrent cross-plan brand-budget enforcement.
- Browser: both end-to-end workflows passed, covering account lifecycle, provider selection/setup
  errors, campaign approval persistence, deployment preflight, and mobile layout checks.
- Production console build, TypeScript, Python lint/format, and dependency consistency checks passed.
- The installed local Ollama model produced a schema-valid draft that was saved and submitted for
  approval in the test database. No advertising campaign was launched.
- Native Windows stop/restart commands verified project server exits and successful API/gateway/web
  startup; the existing PostgreSQL process remained running and working account credentials were preserved.
- Real Ollama Cloud inference, SMTP-provider delivery, advertising APIs, and cloud infrastructure
  remain unverified. Missing cloud keys or external accounts are not represented as working integrations.

## Implemented and locally exercised

- FastAPI backend and Next.js/React console with real PostgreSQL persistence.
- Account registration, input validation, single-use email verification and recovery, exact scrypt
  passwords, expiring opaque sessions, persistent rate limits, origin checks, and mutation role checks.
  Current-session and all-session logout, mobile Account controls, and cross-tab sign-out.
  Owner bootstrap, self-service Business single-brand workspaces, Agency multi-brand.
- Owned generation subprocesses, cancellation on logout/reset/expiry/stop, final session checks before
  saving drafts, persisted OS exit verification, and parent-pipe loss detection.
- Supervised local PostgreSQL consumer: durable activity progress and duplicate receipts, transaction
  recovery after forced process kills, local email delivery, bounded SMTP retries, and consumer health.
  SMTP provider delivery remains unverified; local email files and crash recovery have been exercised.
- Transaction-local tenant context on pooled connections. Runtime connections reject superuser and
  BYPASSRLS roles. RLS applies to tenant tables, child partitions, and caller-invoked views.
- Website import with public-IP checks, pinned DNS resolution, redirect revalidation, HTML-only and
  response-size bounds; immutable source evidence records and manually confirmed brand facts.
- Manual and Ollama-generated campaign plans with decimal budgets, channel allocation validation,
  confirmed-brand prerequisite, restricted-vertical blocking, and revision hashes.
- Plan approval with internal/client stages, separate client identity, role spend caps, absolute
  expiry, Ed25519 signatures, edit invalidation, rejection feedback, and concurrency serialization.
- Append-only audit records and event-registry-validated outbox writes committed with domain changes.
- Budget-limit editing and a local kill switch with explicitly unverified remote pause state.
- Responsive console, actual browser workflow verification, and persistent data after reload.
- Explicit Local Ollama/Ollama Cloud selection, separate cloud-key configuration, bearer authentication
  restricted to Ollama's HTTPS origin, and validated cloud responses without unsupported structured-output
  parameters. Cloud contract/error tests run locally; actual cloud inference needs the user's API key.
- Separate gateway process and database role using public verification keys. Signature, revision,
  scope, expiry, allocation, ceiling, cumulative-cap, and replay validation with atomic reservations.
  Gateway role cannot issue approvals or read login credentials. Concurrent and cross-brand negative tests.
- Deployment preflight from approved plan cards, without reserving authority or claiming missing
  platform access/creative/adapter prerequisites are ready. Windows upgrade and startup checks preserve data.

## Supplied schema corrections

- Caller-level RLS on operational views; account, seat, identity, and direct partition policies.
- Portfolio aggregate joins no longer multiply reported spend.
- Compliance-flag view predicate is parenthesized correctly.
- Replay uniqueness is `(token, channel, operation)` and cannot be bypassed with a new idempotency key.
- Token claims cannot be mutated after issuance; voided tokens cannot be reactivated.
- Plan content changes require a new hash. Spend audit entries require live same-brand authority.
- Cross-brand plan/child references are rejected for allocations, concepts, and deployments.
- Nonblank confirmation provenance and unique brand-level ceiling scope.

These are focused corrections, not a declaration that every supplied schema invariant is complete.
The full schema contains future service tables that have not all been integrated into this runtime.

## Still required by the original product

| Area | Remaining implementation |
|---|---|
| Spend path | Extract independent approval-service identity; actual adapter egress verification; Redis failure behavior; reconciliation, rollback, full APRV-8 release gate |
| Platforms | All nine live adapters (Google/YouTube share one platform family), OAuth, credentials, account access, registry refresh, conversion preflight, verified launch/pause |
| Orchestration | Temporal workflows and workers, durable human signals, retry/replay tests, worker versioning |
| Events | Redpanda provisioning, broker outbox relay, additional domain consumers, broker DLQ and replay tooling |
| Creative | Brand asset upload, scene graph, static/video rendering, rendition validation, creative review |
| Compliance | Evidence-backed claim substantiation, policy corpus, likeness/consent, disclosure application, platform rejection learning |
| Measurement | Metric adapters, watermarks, ClickHouse normalization, comparability annotations, compound fatigue/anomaly detection |
| Optimization | Proposals, bounded auto-approval, allocation shifts, tests/outcome attribution, winner scaling |
| Agency | Invite/seat management, OIDC/MFA, account conversion, client portal, white labeling and portfolio controls |
| Reporting | First-party connectors, reports/PDFs, report delivery, billing, support tools |
| Operations | Global pause verification and release flow, audit export, retention/partition management, migrations/restore drills |
| Infrastructure | Terraform/EKS/KMS/S3, network boundaries, Redis governor, telemetry stack, secrets rotation, production deployment |

Platform access applications and credentials require the business's platform accounts and cannot be
validated with local fixtures. No platform applications were submitted, no ad accounts connected,
and no live campaigns were created by this build.

The next dependency-ordered engineering work is the remaining approval-service/adapter egress boundary
and full negative suite, followed by Temporal and a conformant Meta adapter. Creative and measurement work
must follow the prerequisite gates in the supplied implementation roadmap.
