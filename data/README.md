# Authorised case data

The application expects the four organiser-provided synthetic CSV files in this directory:

- `product_catalogue.csv` — 25 products, prices, eligibility and dependencies
- `smb_leads.csv` — 5,000 synthetic leads and funnel outcomes
- `mock_gstin_registry.csv` — 3,534 synthetic GST registrations
- `mock_udyam_registry.csv` — 2,723 synthetic Udyam registrations

The data is fictional challenge material, not Airtel operating data or current tariffs. Do not publish or use it as a production commercial source. The full OCR test suite also requires the 47 authorised sample images in `kyc_samples/`.

For fixture date checks set `UDYAM_TODAY=2026-07-15`. Production must omit this override and use the current date.
