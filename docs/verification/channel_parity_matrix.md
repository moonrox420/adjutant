# Published Channel Capability & Parity Matrix (Slice 10)

This published parity matrix documents every capability across all ten supported advertising channels in Adjutant v2. All capability declarations are versioned and enforced declaratively via the `channel_capability` database registry and verified by the offline adapter conformance suite (`tests/conformance/test_adapter_conformance.py`).

---

## 1. Comprehensive Channel Parity Table

| Channel | Registry Version | Hierarchy Levels | Budget Levels | Bidding Strategies | Direct Review Required | Adapter Status |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Meta** (`meta`) | `2026.09` | `campaign` &rarr; `ad_group` &rarr; `ad` | `campaign`, `ad_group` | `lowest_cost`, `cost_cap`, `bid_cap`, `roas_goal` | **Yes** (Advanced Access review) | `production_ready` |
| **Google Ads** (`google_ads`) | `2026.09` | `campaign` &rarr; `ad_group` &rarr; `ad` | `campaign` | `maximize_conversions`, `target_cpa`, `target_roas`, `maximize_clicks` | **No** (Standard Token) | `production_ready` |
| **YouTube** (`youtube`) | `2026.09` | `campaign` &rarr; `ad_group` &rarr; `ad` | `campaign`, `ad_group` | `target_cpm`, `target_cpv`, `maximize_conversions`, `target_cpa` | **No** (Shared Google Ads API) | `production_ready` |
| **TikTok** (`tiktok`) | `2026.09` | `campaign` &rarr; `ad_group` &rarr; `ad` | `campaign`, `ad_group` | `lowest_cost`, `cost_cap`, `bid_cap`, `value_optimization` | **Yes** (Data-security review) | `production_ready` |
| **LinkedIn** (`linkedin`) | `2026.09` | `campaign` &rarr; `ad_group` &rarr; `ad` | `campaign` | `maximum_delivery`, `cost_cap`, `manual_bidding` | **Yes** (Standard tier allowlist) | `production_ready` |
| **Microsoft** (`microsoft`) | `2026.09` | `campaign` &rarr; `ad_group` &rarr; `ad` | `campaign`, `ad_group` | `enhanced_cpc`, `maximize_conversions`, `target_cpa`, `target_roas`, `manual_cpc` | **No** (Developer token) | `production_ready` |
| **Reddit** (`reddit`) | `2026.09` | `campaign` &rarr; `ad_group` &rarr; `ad` | `campaign`, `ad_group` | `lowest_cost`, `bid_cap`, `cost_cap` | **No** (Open API access) | `production_ready` |
| **Pinterest** (`pinterest`) | `2026.09` | `campaign` &rarr; `ad_group` &rarr; `ad` | `campaign`, `ad_group` | `automatic_bid`, `target_cpc`, `maximize_conversions` | **Yes** (Standard access review) | `production_ready` |
| **Snapchat** (`snapchat`) | `2026.09` | `campaign` &rarr; `ad_squad` &rarr; `ad` | `campaign`, `ad_squad` | `lowest_cost`, `target_cost`, `max_bid` | **No** (Open API access) | `production_ready` |
| **Amazon Ads** (`amazon_ads`) | `2026.09` | `campaign` &rarr; `ad_group` &rarr; `ad` | `campaign` | `dynamic_bids_down_only`, `dynamic_bids_up_and_down`, `fixed_bids` | **No** (Profile scope resolved) | `production_ready` |

---

## 2. Supported Campaign Objectives by Channel

| Channel | Awareness | Traffic | Leads | Sales | App Promotion | Video Views | Engagement | Store Visits |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Meta** | &check; | &check; | &check; | &check; | &check; | &mdash; | &check; | &mdash; |
| **Google Ads** | &check; | &check; | &check; | &check; | &check; | &mdash; | &mdash; | &mdash; |
| **YouTube** | &check; | &check; | &check; | &check; | &mdash; | &check; | &mdash; | &mdash; |
| **TikTok** | &check; | &check; | &check; | &check; | &check; | &check; | &check; | &mdash; |
| **LinkedIn** | &check; | &check; | &check; | &mdash; | &mdash; | &check; | &check; | &mdash; |
| **Microsoft** | &check; | &check; | &check; | &check; | &mdash; | &mdash; | &mdash; | &mdash; |
| **Reddit** | &check; | &check; | &check; | &check; | &check; | &check; | &check; | &mdash; |
| **Pinterest** | &check; | &check; | &mdash; | &check; | &mdash; | &check; | &check; | &mdash; |
| **Snapchat** | &check; | &check; | &check; | &check; | &check; | &check; | &check; | &mdash; |
| **Amazon Ads** | &check; | &check; | &mdash; | &check; | &mdash; | &mdash; | &mdash; | &mdash; |

---

## 3. Placement & Creative Text Limits

| Channel | Placement / Format | Headline Max Length | Description / Body Max Length | Allowed Aspect Ratios |
| :--- | :--- | :--- | :--- | :--- |
| **Meta** | Facebook / Instagram Feed | 40 characters | 125 characters (primary text) | `1:1`, `4:5`, `9:16` |
| **Google Ads** | Responsive Search & Display | 30 characters | 90 characters | `1:1`, `1.91:1`, `4:5` |
| **YouTube** | Responsive Video / Shorts | 30 characters | 90 characters | `16:9`, `9:16`, `1:1` |
| **TikTok** | In-Feed Ads / Spark Ads | 100 characters | 100 characters | `9:16`, `1:1`, `16:9` |
| **LinkedIn** | Sponsored Content / Single Image | 70 characters | 100 characters (600 commentary) | `1:1`, `1.91:1`, `4:5` |
| **Microsoft** | Responsive Search Ads | 30 characters | 90 characters | `1:1`, `1.91:1` |
| **Reddit** | Promoted Post / Feed | 300 characters | N/A (Headline is post title) | `1:1`, `16:9`, `4:5` |
| **Pinterest** | Standard Pin / Video Pin | 100 characters | 500 characters | `2:3`, `1:1`, `9:16` |
| **Snapchat** | Snap Ads / Story Ads | 34 characters | 100 characters (brand: 25) | `9:16` |
| **Amazon Ads** | Sponsored Products / Brands | 50 characters | 150 characters | `1:1`, `16:9` |

---

## 4. Quota Models & API Rate Governance

- **Meta:** `points_per_ad_account_hour` (`base + 40 * active_ads`). Read via `X-Ad-Account-Usage` header.
- **Google Ads:** `operations_per_day` (token-based standard/basic access).
- **YouTube:** Shares Google Ads API quota model (`operations_per_day`).
- **TikTok:** `per_app_and_advertiser` dynamic quota on active ad groups.
- **LinkedIn:** `write_account_allowlist` with standard and development tiers.
- **Microsoft:** `requests_per_minute` over REST API v13.
- **Reddit:** `requests_per_minute` (OAuth2 token bucket).
- **Pinterest:** Undisclosed; rate limit probing prohibited by platform rules; adheres to HTTP 429 backoff headers.
- **Snapchat:** `requests_per_minute` with documented per-endpoint limits.
- **Amazon Ads:** `requests_per_second` (profile-scoped v3 endpoints).

---

## 5. Platform Access Application Sequencing

Channels requiring direct developer platform review and allowlisting:
1. **Meta:** Advanced Access for `ads_management`, `pages_manage_ads`.
2. **TikTok:** Developer Verification & Data Security Review.
3. **LinkedIn:** Standard Tier Application Review.
4. **Pinterest:** Standard Access Application.

Open API Channels (zero review gate for standard advertising operations):
1. **Reddit Ads API v3**
2. **Snapchat Marketing API v1**
3. **Google Ads API v25** (with Developer Token)
4. **Microsoft Advertising API v13**
5. **YouTube** (via Google Ads)
6. **Amazon Advertising API**
