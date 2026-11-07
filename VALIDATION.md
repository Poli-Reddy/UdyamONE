# Verification record

Environment: Windows, Python 3.11.18, FastAPI, local SQLite and PostgreSQL 17 compatibility review, RapidOCR CPU backend. Automated local order tests use disposable databases. PostgreSQL migrations and the non-root production container were also exercised with disposable audit services.

## Results

### Current final review

- The current complete Python suite passed **91/91**, including the 12-applicant OCR answer key and production-foundation controls.
- The frontend/API contract suite passed **17/17** under Node. Combined inline, Settings and decision JavaScript also passed syntax checking.
- Real-browser QA used installed Chrome 156, the current FastAPI app and a disposable database. Customer planning/order/tracker, private-code reopening, staff entry, Back-office, assistance simulation, WhatsApp preview, partner QR, activation failure/recovery, standalone K03 KYC, bill extraction and pilot assignment all completed successfully.
- Desktop and emulated 390×844 mobile views rendered without horizontal document/nav overflow. Chrome's accessibility tree contained no unnamed interactive controls in the reviewed planning form. No JavaScript console exceptions or HTTP responses ≥400 occurred in the complete journey.
- Settings now contains language, one Voice toggle, privacy and staff access. There is no gender/source/speed selector or Test/Play button. Enabling Voice and changing language generated zero speech requests; advancing to the next real advisor question generated one.
- A live Tamil neural speech request returned HTTP 200, 22,896 bytes of MP3 audio and `ta-IN-PallaviNeural`. Human listening quality and physical microphone permission still need the pre-demo device check.
- Recovery price deltas now put the sign before the currency symbol (`-₹100`).
- The final judge-demo rehearsal used matching Ganesh Woodcraft / Joseph Singh checkout data and the bound K03 documents. Chrome completed OTP sign-in, a ₹2,011 Fibre/Email/CCTV order, genuine K03 verification, Email evidence completion, Fibre failure, customer-only AirFiber recovery to ₹1,911, preserved Email progress and the zero-lift ₹0 assistance policy.
- That rehearsal exposed Chrome's stricter `v`-mode parsing of the formatted-phone pattern. The punctuation is now escaped correctly; a clean reload accepts both plain and formatted numbers with no console warning.
- Prices remain identified as illustrative at plan review; verification, activation and payment limitations are shown where they affect the customer. This presentation update does not turn the supplied catalogue or simulated integrations into live services.

### Earlier complete review

| Check | Result |
|---|---|
| Original baseline before implementation | 32 passed, including the 12-applicant OCR answer key |
| Targeted backend review: planning, activation, providers, contact validation and staff access | 43 passed |
| Previous complete suite after demo review | 75 passed |
| Current complete Python suite | **91 passed** |
| Legacy scenario lab | 8/8 passed, including 300 generated lead profiles |
| JavaScript syntax | Extension and combined original/extension scripts both passed `node --check` |
| Frontend presentation/API contracts | **17 passed** using actual backend response fixtures |
| Local HTTP workflow | Passed: existing-service reuse, budget blocking, no-purchase outcome, branch failure/isolation, revised recovery acceptance, preserved email progress, WhatsApp preview, zero-effect assistance policy |
| Static/API serving | `/`, JS, CSS, integration status and OpenAPI returned HTTP 200 |

The extended tests cover scoped quantities and shared subscriptions, catalogue dependencies, missing cost/inventory, GST eligibility, contract deferral, stale quote acceptance, cross-order tokens, task ordering/version/replay handling, branch-specific forecasts, explicit recovery consent, payment-related repricing blocks, bound KYC identity, pilot assignment, raw-body webhook signatures, wrong payment amounts, late payment events, provider timeouts, WhatsApp duplicates and its 24-hour response window.

An intermediate run encountered an inaccessible Windows shared pytest temporary directory. `tests/conftest.py` now allocates pytest storage inside its own disposable directory; the affected test and all 35 new tests subsequently passed. SQLite connections now close at the end of each transaction scope, fixing an earlier Windows cleanup lock.

## Customer-experience fixes from the final review

- The customer journey is the default. Operations, operator evidence and activation controls require staff workspace entry; sample presets additionally require Test tools.
- Customer tracking uses readable statuses, location names, grouped services and a clear next step. Internal task IDs and unconfigured checkout controls are absent from the customer renderer.
- Provider registry controls are staff-only and disabled when the account is missing. Frontend API errors no longer dump validation objects or configuration-variable names into customer messages.
- Required contact fields validate before submission; the server also rejects whitespace-only names/addresses, invalid phones and malformed emails. Formatted phone numbers normalize consistently.
- Shared subscriptions select their correct scope automatically. Quantity inputs are separate from their checkbox labels and are disabled for unselected services. Extra service choices are collapsed.
- Order restoration updates the KYC reference/lane and stores a successfully supplied private access code for that order. Stale responses cannot overwrite a more recent order load.
- Successful checkout blocks repeated submission of the same accepted plan. A recovery acceptance refreshes the displayed quote and profile as well as the tracker.
- Staff order retrieval has its own protected endpoint, and partner summary requests forward the staff token correctly.
- Responsive styles, keyboard focus, field labels and header/nav positioning were verified in real Chrome at desktop and 390×844 mobile dimensions.
- The refreshed public home, customer/employee sign-in split, OTP customer dashboard, role-filtered staff panels, signed partner referral and prerequisite-gated journey were exercised against the live local API. A final Lighthouse pass reported 1.00 accessibility, best-practices and SEO scores with no listed failures.
- The advisor now extracts several deterministic answers from one typed or spoken sentence. In Chrome, “I run a small kirana shop with 2 people in Hyderabad” produced Retail/kirana, 1–6 employees, one site and Tier 1, displayed **4 of 6 questions answered**, then asked only GSTIN followed by needs. Equivalent Hindi, Tamil and Telugu sentences passed the parser contract.
- English, Hindi, Tamil and Telugu were switched in the live interface; public, advisor, interpreted-answer, needs-review, plan, access, account and staff-workspace chrome updated without reloading or producing console errors.
- Customer sign-out exists only under My account. Authenticated customer/staff account views hide the role chooser and show the active identity plus sign-out. Staff order opening was rehearsed from Work queue with customer navigation hidden and a working Back to work queue action.
- Consent-gated Udyam prefill was exercised with K03 in Chrome: selecting the certificate called the bounded OCR endpoint and filled `UDYAM-MH-00-9516696`; the temporary image and raw OCR text were not retained.
- The reported two-needs/two-locations order was verified as three unique catalogue products and five service-location instances: AirFiber and CCTV at each location plus one business-wide WhatsApp subscription. The tracker now states this conversion and identifies required catalogue dependencies instead of calling all five “selected services.”
- Hindi, Tamil and Telugu checkout text explains that existing provider bills remain separate. Customer-facing advisor, verification, plan controls and footer text no longer expose older implementation descriptions or promise a live timeline. Official catalogue product names and technical identifiers remain canonical data values.

## Verification limits

- Browser QA used installed headless Chrome through the DevTools protocol because the desktop-browser bridge was unavailable. It exercised rendered DOM interactions, file uploads, accessibility data, desktop/mobile screenshots and live HTTP calls rather than relying only on static syntax checks.
- No external provider credentials were supplied. Cashfree, Razorpay and Meta adapters were tested with controlled responses; no live call, payment or WhatsApp send occurred.
- Live Airtel provisioning is not connected. Demo/operator task evidence is not a telecom provider confirmation.
- The locked, non-root Docker image built successfully (about 348 MB), started read-only against PostgreSQL 17, passed `/api/ready`, rejected anonymous staff access and kept mock KYC disabled in production mode.
- Online neural speech returned a valid MP3 in the current QA environment. Optional LLM calls were not used.
- No scale benchmark, security certification, measured business uplift or competition winning probability is claimed.

FastAPI's TestClient emitted one dependency deprecation warning concerning its current HTTPX integration. Pytest also could not write its optional cache in the synced workspace. Neither warning affected the passing tests; no application failure was hidden or skipped.
