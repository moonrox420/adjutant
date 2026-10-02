# Published Channel Capability & Parity Matrix (Slice 10)

This published parity matrix documents every capability across all five supported advertising channels in Adjutant v2. All capability declarations are versioned and enforced declaratively via the `channel_capability` database registry and verified by the offline adapter conformance suite (`tests/conformance/test_adapter_conformance.py`).

---

## 1. Comprehensive Channel Parity Table

| Channel | Registry Version | Hierarchy Levels | Budget Levels | Bidding Strategies | Direct Review Required | Adapter Status |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Meta** (`meta`) | `2026.09` | `campaign` &rarr; `ad_group` &rarr; `ad` | `campaign`, `ad_group` | `lowest_cost`, `cost_cap`, `bid_cap`, `roas_goal` | **Yes** (Advanced Access review) | `production_ready` |
| **Google Ads** (`google_ads`) | `2026.09` | `campaign` &rarr; `ad_group` &rarr; `ad` | `campaign` | `maximize_conversions`, `target_cpa`, `target_roas`, `maximize_clicks` | **No** (Standard Token) | `production_ready` |
| **YouTube** (`youtube`) | `2026.09` | `campaign` &rarr; `ad_group` &rarr; `ad` | `campaign`, `ad_group` | `target_cpm`, `target_cpv`, `maximize_conversions`, `target_cpa` | **No** (Shared Google Ads API) | `production_ready` |
| **LinkedIn** (`linkedin`) | `2026.09` | `campaign` &rarr; `ad_group` &rarr; `ad` | `campaign` | `maximum_delivery`, `cost_cap`, `manual_bidding` | **Yes** (Standard tier allowlist) | `production_ready` |
| **Reddit** (`reddit`) | `2026.09` | `campaign` &rarr; `ad_group` &rarr; `ad` | `campaign`, `ad_group` | `lowest_cost`, `bid_cap`, `cost_cap` | **No** (Open API access) | `production_ready` |

---

## 2. Supported Campaign Objectives by Channel

| Channel | Awareness | Traffic | Leads | Sales | App Promotion | Video Views | Engagement | Store Visits |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Meta** | &check; | &check; | &check; | &check; | &check; | &mdash; | &check; | &mdash; |
| **Google Ads** | &check; | &check; | &check; | &check; | &check; | &mdash; | &mdash; | &mdash; |
| **YouTube** | &check; | &check; | &check; | &check; | &mdash; | &check; | &mdash; | &mdash; |
| **LinkedIn** | &check; | &check; | &check; | &mdash; | &mdash; | &check; | &check; | &mdash; |
| **Reddit** | &check; | &check; | &check; | &check; | &check; | &check; | &check; | &mdash; |

---

## 3. Placement & Creative Text Limits

| Channel | Placement / Format | Headline Max Length | Description / Body Max Length | Allowed Aspect Ratios |
| :--- | :--- | :--- | :--- | :--- |
| **Meta** | Facebook / Instagram Feed | 40 characters | 125 characters (primary text) | `1:1`, `4:5`, `9:16` |
| **Google Ads** | Responsive Search & Display | 30 characters | 90 characters | `1:1`, `1.91:1`, `4:5` |
| **YouTube** | Responsive Video / Shorts | 30 characters | 90 characters | `16:9`, `9:16`, `1:1` |
| **LinkedIn** | Sponsored Content / Single Image | 70 characters | 100 characters (600 commentary) | `1:1`, `1.91:1`, `4:5` |
| **Reddit** | Promoted Post / Feed | 300 characters | N/A (Headline is post title) | `1:1`, `16:9`, `4:5` |

---

## 4. Quota Models & API Rate Governance

- **Meta:** `points_per_ad_account_hour` (`base + 40 * active_ads`). Read via `X-Ad-Account-Usage` header.
- **Google Ads:** `operations_per_day` (token-based standard/basic access).
- **YouTube:** Shares Google Ads API quota model (`operations_per_day`).
- **LinkedIn:** `write_account_allowlist` with standard and development tiers.
- **Reddit:** `requests_per_minute` (OAuth2 token bucket).

---

## 5. Platform Access Application Sequencing

Channels requiring direct developer platform review and allowlisting:
1. **Meta:** Advanced Access for `ads_management`, `pages_manage_ads`.
2. **LinkedIn:** Standard Tier Application Review.

Open API Channels (zero review gate for standard advertising operations):
1. **Reddit Ads API v3**
2. **Google Ads API v25** (with Developer Token)
3. **YouTube** (via Google Ads)
