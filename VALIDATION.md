# Verification record

Reviewed on 7 November 2026 using Windows, Python 3.11.18, FastAPI/SQLite and RapidOCR CPU.

## Automated checks

| Check | Result |
| --- | --- |
| Complete Python suite | 84 tests passed |
| Frontend/API contract suite | 15 tests passed |
| Publication source check | Application and test files match the tested workspace |

The Python suite covers catalogue rules, inventory and location scope, budget blocking, order access tokens, KYC consent and identity binding, activation dependencies, recovery acceptance, provider signatures, payment reconciliation, WhatsApp state and assistance records. The frontend suite checks rendering contracts, escaping, access headers, staff entry, preferences and voice fallbacks.

All automated order tests use disposable databases and consent logs. The organiser-provided case data and OCR samples are required for the complete suite and are not redistributed in the public repository.

## Browser and voice review

The recorded local browser review covered the customer planning/order/tracker journey, private-code reopening, staff workspace, assistance simulation, partner attribution, KYC, bill extraction and failure recovery. Desktop and a 390 x 844 mobile viewport were checked. A live Tamil speech request returned MP3 audio.

That browser review was completed before this documentation/publication pass; it is not a new browser certification. Physical microphone permission and human listening quality should be checked on the demonstration device.

## Limits

- Provider adapters were tested with controlled responses. Live Cashfree, Razorpay and Meta account acceptance tests remain outstanding.
- Airtel provisioning is not connected. Operator evidence does not certify telecom activation.
- No production load benchmark, independent penetration test, compliance certification or measured business uplift is claimed.
- The current test environment reports one Starlette/HTTPX deprecation warning; the suite completes successfully.

Production requirements are documented in [PRIVACY.md](PRIVACY.md) and [INTEGRATIONS.md](INTEGRATIONS.md).
