# Local case data

The public repository does not redistribute the Airtel case study, its data pack or document samples. Obtain the authorised pack through the competition and keep it local.

Place the following files in this directory before starting the application:

| File | Used by |
| --- | --- |
| `product_catalogue.csv` | Catalogue eligibility, pricing, contracts and dependencies |
| `smb_leads.csv` | Historical funnel analysis and the assistance propensity model |
| `mock_gstin_registry.csv` | Local GSTIN verification |
| `mock_udyam_registry.csv` | Local Udyam verification |

Preserve the supplied filenames, column names and encodings. `DATA_DIR` can point to another directory containing these files. The default is `data`, relative to the application working directory.

Place the authorised KYC sample PNG files directly in the repository's `kyc_samples/` directory for the OCR examples and full verification tests. Preserve their original `Kxx_...png` filenames. These examples use the fixed `UDYAM_TODAY=2026-07-15` sample date; this is not a live verification date.

The organiser describes the supplied data as synthetic exercise data. Do not substitute real customer documents or treat the catalogue as current Airtel tariffs.
