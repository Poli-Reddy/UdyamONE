# Data dictionary

## v3 additions

The original `orders` columns are preserved. New tables are created idempotently on startup:

| Table | Purpose |
|---|---|
| `decision_plans` | Order ID, accepted plan JSON, SHA-256 access-token hash, accepted timestamp |
| `activation_tasks` | Order/task key plus dependencies, product/location, state, version, evidence, source and forecast inputs |
| `activation_events` | Order/event key, immutable event payload and recorded timestamp |
| `plan_revisions` | Archived accepted plan and task snapshot keyed by old fingerprint |
| `payment_intents` | One reference/amount/currency/test link/status per order; no card data |
| `provider_events` | Payment webhook event key and raw-body digest for deduplication |
| `registry_evidence` | Scoped provider result, mode, consent version and timestamp per order/identifier kind; no full registration identifier |
| `whatsapp_sessions` | Sender phone, structured conversational state, last update; STOP/24-hour lazy expiry |
| `whatsapp_receipts` | Inbound message ID, delivery-attempt status, timestamp; no transcript |
| `interventions` | Random pilot assignment, blocker skill, timestamp, operator-reported cost/outcome/note |
| `auth_otp_challenges` | Short-lived hashed customer sign-in challenges; no plaintext OTP retained |
| `customer_sessions` | Hashed opaque customer sessions, phone binding, expiry and revocation |
| `order_customers` | Order-to-verified-phone ownership binding |
| `consent_events` | Purpose, notice version, order reference, outcome and finding codes |

Plan JSON includes reviewed locations, owned services, deferred requirements, current/new costs, assumptions, open questions and a SHA-256 fingerprint. A fingerprint detects stale acceptance; it is not an identity signature. See `PRIVACY.md` for retention limits and `INTEGRATIONS.md` for provider scope.

All data is synthetic. Names, GSTINs, Udyam numbers, addresses and outcomes are fictional test data created for the hackathon.

## `data/product_catalogue.csv` (25 products)

| Column | Type | Meaning |
|---|---|---|
| `product_id` | string | Stable ID (`P01`…`P25`). The engine never invents IDs. |
| `category` | string | Connectivity, Mobility, Collaboration, Security, IoT, etc. |
| `product_name` | string | Display name |
| `description` | string | Plain-language description |
| `unit` | string | Billing unit: `per site`, `per user`, `per line`, `per camera`, `per counter`, `per account`, `per channel`, `per device`, `per vehicle`, `per IP`, `per pack`, `per number` |
| `price_min_inr_per_month` | int | Catalogue minimum monthly price per unit |
| `price_max_inr_per_month` | int | Catalogue maximum monthly price per unit |
| `one_time_setup_inr` | int | Setup per unit |
| `min_contract_months` | int | Minimum contract; quote uses the longest in the bundle |
| `available_city_tiers` | `1;2` | Eligible city tiers |
| `min_employees` | int | Minimum employee count |
| `min_sites` | int | Minimum number of sites |
| `requires_gstin` | `Y/N` | `Y` = cannot be sold in the GST-free lane |
| `requires_one_of` | `P01\|P02\|P03` | Dependency: at least one base product must be present |
| `typical_install_days` | int | Used for the tracker’s activation estimate |
| `target_segments` | string | `micro;prof;multi;mfg` preference hint (the engine’s explicit need map is the primary selector) |

**Price point used by the quote:** `min + 0.30 × (max − min)` for micro retail, `min + 0.50 × (max − min)` otherwise. This is deliberately inside the published range, not a made-up number.

## `data/smb_leads.csv` (5,000 leads)

| Group | Columns | Notes |
|---|---|---|
| Identity | `lead_id`, `created_date`, `business_name`, `business_type`, `segment`, `city`, `city_tier`, `state`, `preferred_language` | Segment values match the case’s four segments |
| Size | `employee_count`, `num_sites`, `annual_turnover_band` | Used by segment inference and eligibility |
| Compliance | `has_gstin`, `gstin`, `has_udyam`, `udyam_number` | GST-free leads are the target of the new lane |
| Current state | `current_internet`, `current_provider`, `monthly_telecom_spend_inr`, `products_of_interest` | Spend enables savings comparison |
| Contact | `contact_name`, `contact_role` | Synthetic |
| Funnel | `stage_reached`, `outcome`, `drop_reason` | Stages: Contacted, Needs assessed, Quote accepted, KYC complete, Activated; outcome Activated/Dropped |
| Dates | `contacted_date`, `needs_assessed_date`, `quote_accepted_date`, `kyc_complete_date`, `activated_date`, `days_lead_to_activation` | Used for verified funnel stats |
| Economics | `bundle_products`, `monthly_bundle_value_inr`, `one_time_setup_inr`, `contract_months`, `churned_within_6_months`, `nps_score`, `acquisition_cost_inr`, `customer_follow_up_calls` | Activated leads have bundle economics; dropped leads do not |

**Verified pack stats (computed, not invented):**

| Metric | Value |
|---|---|
| Leads | 5,000 |
| Activated | 368 (7.36%) |
| Average days to activation | 17.6 |
| Blended CAC | ₹15,053 |
| Average follow-up calls | 3.01 |
| GST activation rate | 9.6% vs 2.0% without GST |
| 6-month churn | 15.2% |
| Cancelled/suspended GSTINs in registry | 167 cancelled, 101 suspended |
| Micro retail | 4.06% activation, ₹2,470 median bundle, 22.7% churn |
| Professional offices | 11.07%, ₹6,575, 13.8% |
| Small multi-site | 10.99%, ₹28,080, 10.8% |
| Small manufacturers | 12.80%, ₹15,910, 8.1% |

## `data/mock_gstin_registry.csv` (3,534 rows)

| Column | Meaning |
|---|---|
| `gstin` | 15-character synthetic GSTIN (primary key) |
| `legal_name` | Registered legal name; used for KYC name match |
| `trade_name` | Trade name; used for fallback and PAN-letter check |
| `constitution` | Proprietorship, LLP, Private Limited, etc. |
| `principal_place_of_business` | Synthetic address |
| `state` | State |
| `registration_date` | Date |
| `status` | Active / Cancelled / Suspended; non-Active is rejected |

## `data/mock_udyam_registry.csv` (2,723 rows)

| Column | Meaning |
|---|---|
| `udyam_number` | `UDYAM-XX-00-0000000` (primary key) |
| `enterprise_name` | Enterprise name; cross-checked with GST/address/signatory |
| `enterprise_type` | Micro / Small / Medium |
| `major_activity` | Manufacturing / Services / Trading |
| `nic_description` | Activity description |
| `state`, `city` | Location |
| `date_of_registration` | Date |

## `kyc_samples/`

47 PNG images for 12 applicants (`K01`…`K12`), four document types each where present:

| Suffix | Document | Used in standard lane | Used in GST-free lane |
|---|---|---|---|
| `gst_certificate` | GST registration certificate | Yes | No (must not be uploaded) |
| `udyam_certificate` | Udyam registration certificate | Yes | Yes |
| `signatory_id` | Authorised signatory ID | Yes | Yes |
| `address_proof` | Electricity bill / address proof | Yes | Yes |

Expected answer key: **K01 REJECTED, K02 VERIFIED, K03 VERIFIED, K04 ACTION_REQUIRED, K05 ACTION_REQUIRED, K06 REJECTED, K07 REJECTED, K08 VERIFIED, K09 VERIFIED, K10 VERIFIED, K11 ACTION_REQUIRED, K12 VERIFIED.**

## Orders database (PostgreSQL pilot / local `orders.db`)

Table `orders`:

| Column | Meaning |
|---|---|
| `id` | `UO-YYYYMMDD-XXXXXXXX` server-generated order ID |
| `created_at` | UTC ISO timestamp |
| `business_name`, `contact_name`, `phone`, `email`, `address`, `language` | Checkout fields |
| `segment` | Engine-inferred segment |
| `lane` | Standard / GST-free |
| `items_json` | Frozen quote items at order time |
| `monthly`, `setup`, `contract_months` | Server-recomputed totals |
| `status` | `placed`, `quote_accepted`, `kyc_pending`, `kyc_complete`, `install_scheduled`, `activated`, `cancelled` |
| `kyc_status` | `pending`, `verified`, `action_required`, `rejected` |
| `kyc_verdict` | Latest KYC verdict |
| `partner_code` | Optional attribution |
| `consent`, `consent_version` | Consent flag and notice version |
| `profile_json` | Engine input profile used at order time |

## `consent_log.jsonl`

One JSON object per KYC attempt. **No PII.**

```json
{
  "ts": "2026-07-15T09:00:00+00:00",
  "purpose": "KYC verification",
  "notice": "v1",
  "lane": "gst_free",
  "order_id": "UO-20260715-AB12CD",
  "verdict": "VERIFIED",
  "codes": []
}
```

## Derived / computed values

- `segment`: `sites >= 3` → multi-site; manufacturer keywords → manufacturer; professional keywords → professional; micro keywords → micro; unknown type falls back to employee size.
- `base connectivity`: manufacturer → leased line / fibre by size; multi-site → fibre per site; professional → fibre by size; micro → 5G AirFiber, upgraded to fibre when failover/static-IP/SD-WAN needs it.
- `quantity`: from the catalogue `unit` (per site, per user, per camera, per line, etc.).
- `monthly`: sum of `price_point × qty`.
- `setup`: sum of `one_time_setup_inr × qty`.
- `contract_months`: max `min_contract_months`.
- `expected_value`: propensity × segment median activated bundle.
