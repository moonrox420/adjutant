# Integration audit — September 21, 2026

This initial audit traces the console before the integration repairs below. It is retained as
a historical defect inventory. Current outcomes, including unresolved internal defects, are in
[the traceability matrix](verification/traceability.md) and [BUILD_STATUS](../BUILD_STATUS.md).

The original audit traced the console, registered FastAPI routes, provider calls, persistence,
and background workers. The local API's OpenAPI document and three service health endpoints
were checked. Configuration was inspected without exposing credentials. No advertising
account was contacted, no campaigns were launched, and no application behavior was changed.

The application currently provides local account, brand, planning, approval, and creative-draft
functions. It is not yet the autonomous advertising runner described in the v2 specification.
Healthy services and passing tests do not establish that missing integrations exist.

## Missing implementation and disconnected flows

| Surface | Actual execution path | Missing connection |
|---|---|---|
| Channels | `api.py` returns capability-registry rows with constant `connection_status="not_connected"` and `live_adapter_available=False`. | No ad-platform authorization, account selection, token refresh, or production adapter. Adding a token alone cannot make this screen operational. |
| Deployment readiness | `deployment.py` checks stored approvals, connections, renditions, and gateway authority. Its adapter check is always false. | No campaign/ad-set/ad construction, publication endpoint, or channel mutation execution. |
| Ad Studio to launch | `campaign_api.py` saves `studio_draft`, `brand_context`, image objects, and a scene-graph document. | No attachment to the strategy plan's creative/concept/rendition pipeline. Deployment preflight reads different tables. |
| Finished creative output | Studio displays one square background image with editable HTML copy and stores native text layers. | No production renderer/compositor for downloadable finished ads, five-concept generation, all required aspect-ratio renditions, or overflow/contrast/safe-area validation. |
| Brand understanding | Quick generation extracts and versions understanding. The older Brand intelligence screen edits assertions. | No UI/API to edit the generated understanding document into a new version. Ingest fetches the supplied page; it is not a complete scheduled website crawl. |
| Approvals | Existing routes submit and decide approvals for plan revisions, with legacy internal/client handling. | Not replaced by v2 first-launch brand/channel activation. Approval does not launch anything. |
| Guardrails | Budget ceilings and spend-authority checks exist; Studio checks stored blocked phrases before image generation and persistence. | No complete v2 autonomous action guardrail path, and no console editor for the entire v2 guardrail record. There is no remote execution to constrain yet. |
| Stop brand operations | Records a local stop, voids approval tokens, requests cancellation of tracked generation jobs, and returns `remote_pause_verified=False`. | No remote pause or verification. No registered console/API action to release the local stop. Studio inference is not a tracked `agent_run`; later gate checks prevent saving after a stop, but do not provide immediate provider cancellation. |
| Measurement and optimization | Historical schema includes metrics and fatigue tables. | No live metric ingestion/normalization, diagnosis, refresh, budget reallocation, or continuous brand advertising loop in the application code. |
| Background jobs | Existing consumer handles local activity and account mail. S0 workflow demonstrates durable waiting/checkpoint/object-storage execution. | Studio generation runs in the HTTP request, outside both the supervised job list and the durable workflow runner. Interrupted Studio requests are failed/released, not resumed through image generation. |
| Activity | Existing plan/approval actions write audit events; job UI reads `agent_run`. | Studio generation/edit routes persist their own records but do not emit the existing activity/audit events or appear in the job list. |

## Configured code paths that cannot currently complete

- **Quick generation:** current settings have no Gemini API key and select
  `imagen-3.0-generate-002`. The Google SDK integration exists, including a configurable
  Gemini `generate_content` image path. However, `require_configuration()` runs before ingest
  and copy generation, so the current console's quick-generation request cannot produce a
  partial copy-only result. The earlier successful live Ollama check exercised the copy
  provider directly; it was not a successful live URL-to-image endpoint run.
- **Image model:** the installed SDK rejects Imagen generation in Developer API key mode.
  The default was retained from the explicit model requirement. A working, authorized image
  model configuration and key are still required; no live Google image was verified.
- **Account emails:** current delivery mode is `file`. Verification/reset messages are written
  to a local mailbox. SMTP delivery is implemented but is not the active configuration.

## Misleading readiness presentation

- The overview's green **Operational readiness / Connected** badge is literal JSX, not an
  aggregate readiness check.
- **Approval authority / Active** is also literal JSX. `/api/status` reports signing as
  `ready` without checking the separate service on that request.
- **Ad platform connections / Not connected** and the Channels statuses are constants,
  not live probes of an account integration.
- Studio sets the legacy `brand_graph_confirmed_at` timestamp automatically, while the UI
  labels that timestamp **Confirmed**. That does not represent a human confirmation.

## Implemented local paths

The code registers real handlers and database operations for login/logout, account lifecycle,
brand creation, assertion edits, website-page import, strategy-plan generation and editing,
legacy plan approval, budget ceilings, audit export, credential encryption, local activity,
and supervised strategy jobs. Studio has real provider calls, typed validation, image storage,
and revisioned copy saving. These implementation facts do not imply live channel support.

Previously reported automated tests cover those local paths and provider contracts, using
mocked external responses where stated. This audit did not rerun that suite or assert that
every existing feature had a new live acceptance run.

## Delivery implications

Removing the Channels warning would conceal missing implementation. The required work is to
complete the creative output and data linkage, implement one real paused-account adapter
(S4), replace the legacy approval path with first-launch activation (S5), and implement actual
launch plus remote pause (S6). Measurement and autonomous operation remain subsequent slices.
The user's explicit no-confirmation generation instruction overrides the contradictory S2
confirmation check still present in the specification; it must not be reintroduced.
