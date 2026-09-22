# Consolidated external authorization requirements

No advertising account, Gemini image generation, or external SMTP delivery has been live-verified.
This list does not account for missing internal implementation: campaign lifecycle adapters,
complete remote pause/resume coverage, metrics and the autonomous loop still have FAIL records in the traceability
matrix. Supplying credentials alone cannot make those paths operational.

Managed-campaign pause requests and independent read-back are now wired for the ten channels,
but have only protocol-contract and local-emulator verification. Full account inventory coverage,
Amazon Sponsored Display control, and remote resume still need internal implementation.

## Advertising platforms

In the product, select the brand, open **Channels**, expand **Set up developer application**, save
the application credentials, and use **Authorize**. Register the exact callback URL displayed
for that brand and channel in the provider's developer console. Complete consent, discover
accounts, then select the returned account. Secrets are encrypted before persistence and are
not returned to the browser. Do not put secrets in issue comments or chat.

Providers that do not permit loopback callbacks require a reachable HTTPS application origin,
registered redirect URI, and `ADJUTANT_SECURE_COOKIES=true`. Set `ADJUTANT_PUBLIC_ORIGIN` to that
origin; the configuration validator also requires the external-delivery settings below for a
non-loopback deployment. No public deployment has been provisioned by this work.

| Platform | Required external setup/action | Product channel |
|---|---|---|
| Meta | Marketing API application ID/secret, permissions requested by the consent flow, authorized business/ad account and relevant Page access. Complete any Meta app review required for those accounts. | `meta` |
| Google Ads | OAuth web client ID/secret, Ads developer token with access appropriate to the test or production account, consent from an account/manager user. Discovery walks manager hierarchies. | `google_ads` |
| YouTube | Google OAuth client ID/secret and Ads developer token; consent includes Ads and YouTube upload permissions. An eligible Ads account and YouTube channel are needed for eventual video deployment verification. | `youtube` |
| TikTok | TikTok for Business developer application ID/secret and advertiser authorization for Marketing API access. | `tiktok` |
| LinkedIn | Developer client ID/secret and Advertising API product access, user consent for `r_ads`, `rw_ads`, `r_ads_reporting`, and an accessible ad account. Refresh tokens depend on the application's granted program access; otherwise reauthorization is required. | `linkedin` |
| Microsoft Advertising | Entra application client ID/secret, registered web redirect, Microsoft Advertising developer token, and consent from an Advertising account user. | `microsoft` |
| Reddit | OAuth application ID/secret, registered redirect and consent for `adsread`, `adsedit`, `identity`; a business with an accessible ad account. | `reddit` |
| Pinterest | Developer application ID/secret with the Ads/Boards/Pins scopes requested by the product and consent from an ad-account user. Complete provider access approval where required. | `pinterest` |
| Snapchat | Marketing API OAuth client ID/secret, app access, and organization/ad-account consent for `snapchat-marketing-api`. | `snapchat` |
| Amazon Ads | Approved Ads API application, Login with Amazon client ID/secret, advertising campaign-management consent, profile access and correct region (`NA`, `EU`, `FE`). | `amazon_ads` |

Each channel card links to its provider's official setup documentation. Google/YouTube and
Microsoft show an additional developer-token field; Amazon shows region selection.

Live verification still must create and read back a real paused/draft campaign, inspect its
creative, targeting and budget, test permitted state changes, collect native metrics, and
verify disconnect behavior. None of those external campaign tests has been performed.
Any operation that would enable spending needs the brand's explicit first-launch approval.

Disconnect deletes Adjutant's encrypted token and invalidates pending OAuth states. Where a
provider has a supported revocation endpoint, the response is checked. Where consent removal
requires the provider's UI, the product returns its consent-removal link and reports remote
revocation as unconfirmed. TikTok's revocation contract remains an internal verification defect.

## Google visuals

Open **Ad Studio → Google image generation**, enter a Gemini API key, choose a supported image
model, and save. Configuration is stored encrypted per brand. A server-wide key may instead be
set as `GEMINI_API_KEY`; the server model setting is `ADJUTANT_GEMINI_IMAGE_MODEL`.

The default is `gemini-3.1-flash-image`, using `google-genai` and `generate_content` with image
output and the requested aspect ratio. Retired Imagen configuration is rejected. The model
remains configurable. A successful authenticated generation, not key presence, establishes
provider operation. The application validates image decoding, MIME type and size, persists
the actual returned bytes and reports provider errors without substituting an image.

References: [Google image generation](https://ai.google.dev/gemini-api/docs/image-generation),
[Google model deprecations](https://ai.google.dev/gemini-api/docs/deprecations#imagen-models).

## External email

Set these server environment fields and restart the API, whose supervised consumer owns delivery:

| Setting | Required value |
|---|---|
| `ADJUTANT_MAIL_TRANSPORT` | `smtp` |
| `ADJUTANT_MAIL_FROM` | Sender verified by the chosen mail provider |
| `ADJUTANT_SMTP_HOST` | Provider's SMTP hostname |
| `ADJUTANT_SMTP_PORT` | Provider's submission port |
| `ADJUTANT_SMTP_SECURITY` | `starttls` or `ssl`, matching the provider |
| `ADJUTANT_SMTP_USERNAME` | Provider's SMTP username when authentication is required |
| `ADJUTANT_SMTP_PASSWORD` | Provider's SMTP password/application credential |
| `ADJUTANT_WORKER_DATABASE_URL` | Existing provisioned worker connection; keep its secret private |

Provide an authorized recipient through the normal account/invitation flow for a real delivery
test. Current configuration writes `.eml` files locally. Queue retry and recovery have been
tested, but external delivery has not. The weekly performance-summary generator is a separate
missing internal feature; SMTP configuration does not implement it.
