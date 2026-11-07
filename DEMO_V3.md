# Judge demo runbook and 3-minute script

The official demo must be **under three minutes**. Record to **2:50–2:55** so export or portal rounding cannot push it over the limit.

## The story judges should remember

Do not present a feature tour. Prove three unusual decisions:

1. **UdyamOne knows when not to sell.** It reviews existing services first and can recommend Keep, Add, Replace Later, clarification, or Nothing to buy.
2. **UdyamOne recovers without erasing progress.** A failed connectivity branch produces a priced alternative; completed independent work remains complete.
3. **UdyamOne knows when not to spend.** Propensity is separated from intervention value; when assumed benefit is zero, the value policy contacts nobody.

The one-line proposition is:

> **Sell only the gap. Preserve completed work. Spend assistance only where it can help.**

## Use this exact synthetic data

The K03 documents belong to Ganesh Woodcraft and Joseph Singh. Do not use the older Demo Clinic / Asha Rao values with K03; that mismatch is correctly treated as a verification problem.

| Field | Value |
|---|---|
| Business profile | Manufacturer |
| Employees | 7–25 (resolved to 12) |
| Locations | 1 |
| City | Tier 1 |
| GSTIN | Yes |
| Location name | Main workshop |
| Business name | Ganesh Woodcraft |
| Contact | Joseph Singh |
| Phone | 9876543210 |
| Email | joseph@example.com |
| Address | Shop/Unit 80, Civil Lines, Nagpur, Maharashtra - 411231 |

Use these four files for the successful document check:

- `kyc_samples/K03_gst_certificate.png`
- `kyc_samples/K03_udyam_certificate.png`
- `kyc_samples/K03_signatory_id.png`
- `kyc_samples/K03_address_proof.png`

## Rehearse in Chrome: exact preparation

### 1. Start clean

1. Copy `.env.example` to `.env` and keep the server bound to localhost.
2. For a disposable rehearsal, set `UDYAM_DB=demo-rehearsal.db` and `UDYAM_CONSENT_LOG=demo-rehearsal-consent.jsonl` in `.env`.
3. Run `./run.ps1 -Port 8000` from PowerShell.
4. Open `http://127.0.0.1:8000` in current Chrome at 1920×1080 and 100% zoom.
5. Turn on Do Not Disturb, close private tabs and disable password-manager overlays.
6. Confirm `/api/health` reports `status: ok`, version `3.0.0`, 25 products and 5,000 leads.
7. Warm the K03 document check once before recording; cut OCR waiting time from the final video.

Never expose this explicitly enabled demo server to a public network. Production intentionally disables local test access, mock registries and synthetic modeling.

### 2. Prepare the “do not oversell” shot

1. On the home page choose **Plan my services**.
2. Choose **Manufacturer → 7–25 → 1 → Tier 1 → Yes**.
3. Select **Email** and **Cloud backup**, then choose **Done**.
4. In Final needs review, set the location name to **Main workshop**.
5. Expand **Existing services**, choose **Add an existing service**, then enter:
   - Existing service: **Email**
   - Provider: **Existing provider**
   - Units: **5**
   - Meets your needs: **Yes, keep it**
   - Monthly charge: **700**
   - Contract ends: **31 March 2027**
   - Early switching cost: **1200**
6. Expand **Budget and timing** and enter **1500** as the total monthly budget.
7. Tick the inventory confirmation and choose **Build my service plan**.
8. Verify the result before recording:
   - Keep: existing Email
   - Add now: Cloud Backup only
   - New charge: **₹599/month**
   - Existing charge: **₹700/month**
   - Total: **₹1,299/month**

Record this result, but do not create an order from it. This keeps the later work queue clean.

### 3. Create the full order used for checkout, KYC and recovery

1. Start over and choose **Manufacturer → 7–25 → 1 → Tier 1 → Yes**.
2. Select **Fibre, CCTV and Email**, then choose **Done**.
3. Set the location name to **Main workshop**, leave quantities on Auto, confirm the inventory and build the plan.
4. Verify the quote:
   - Biz Fibre 100 × 1: **₹1,099/month**
   - Cloud Email & Office Suite × 3: **₹414/month**
   - Cloud CCTV × 2: **₹498/month**
   - Total: **₹2,011/month**
   - Setup: **₹3,600**
   - Longest contract: **12 months**
5. Choose **Sign in → Business customer**.
6. Enter `9876543210`, choose **Send sign-in code**, enter the six-digit local test code shown, and choose **Verify and continue**. The code changes every time; do not put one in the script.
7. Return to **Your plan**, choose **Accept this plan & continue**, and enter the exact synthetic checkout values from the table above.
8. Tick the contact/privacy consent and choose **Create order**. Save the generated `UO-...` reference.
9. Choose **Continue to business verification**.
10. Keep **Standard (GST + Udyam)** selected, upload the four K03 files, tick **I agree for this purpose**, and choose **Verify documents**.
11. After OCR finishes, verify the genuine result: **Documents checked — No document issues found**.

### 4. Prepare the failure and recovery state

Use a second Chrome tab for staff work. Do not show staff sign-in in the final three-minute edit.

1. Choose **Sign in → Employee or partner**.
2. In an explicitly enabled local demo, choose **Open my workspace**. If `ADMIN_TOKEN` is configured, enter it. Production requires a valid OIDC token.
3. In **Work queue**, refresh and open the Ganesh Woodcraft order.
4. Expand **Cloud Email & Office Suite**.
5. Enter evidence `Email tenant configured for three users` and choose **Complete step** under **Installation and setup**.
6. Reopen Email, enter `Owner confirmed send and receive test`, and choose **Complete step** under **Final service check**.
7. Expand **Biz Fibre 100**.
8. Enter `Site survey: wired fibre unavailable at this address` and choose **Record failure** under **Connection availability**.
9. Return to the signed-in customer tab, open **Track services**, and choose **Refresh**.
10. Verify the customer view contains no staff controls or internal task IDs and shows:
    - Email: **Checks complete**
    - Fibre: **Needs attention**
    - CCTV: waiting for the connection path
    - **1/3 service checklists complete**
11. Choose **Review connection options**.
12. Tick the wireless-consideration box and choose **Calculate alternative**.
13. Verify the result:
    - Biz 5G AirFiber 100 replaces Fibre
    - Monthly total changes from ₹2,011 to **₹1,911**
    - Monthly change is **-₹100**
    - Setup remains **₹3,600**
    - Nothing has changed yet
14. Tick the revised-plan acceptance and choose **Accept revised plan**.
15. Verify Email remains complete while AirFiber receives a new feasibility/install/check path and CCTV now waits for AirFiber.

For the recording, capture one clip before revised acceptance and one immediately after it.

### 5. Prepare the “do not waste assistance” shot

1. In the staff tab choose **Work queue → Lead assistance → Load leads**.
2. Confirm the disclosure says propensity is not the causal effect of assistance and the model AUC is **0.84**.
3. In **Assistance policy experiment**, set **Assumed lift** to `0` and choose **Compare policies**.
4. Verify:
   - Propensity first: 20 contacts, ₹5,000 spend, 0 assumed extra activations, -₹5,000 contribution
   - Intervention value: **0 contacts, ₹0 spend, ₹0 contribution**

This is the third memorable decision: a model score does not justify outreach when modeled treatment value is zero.

---

# Final synchronized script

Speak naturally at about 125–130 words per minute. Pause on the three bold ideas instead of racing through every label.

## 0:00–0:14 — Human hook

### Show

Home page and the three differentiator cards.

### Say

> “Joseph runs a twelve-person workshop. He does not need another chatbot or a bigger bundle. He needs three honest decisions: what not to sell, what not to restart, and when not to call.”

On-screen caption: **Three decisions most sales funnels avoid**

## 0:14–0:44 — It knows when not to sell

### Show

Existing Email entry, choose **Build my service plan**, then hold on Keep / Add now and ₹1,299 total.

### Say

> “Ganesh Woodcraft already has five adequate email seats. UdyamOne begins with that inventory, keeps the ₹700 service, and adds only the missing Cloud Backup for ₹599. A normal recommender can sell Email twice. Ours can also return nothing to buy, or block acceptance rather than hide an unmet need to fit a budget.”

On-screen caption: **Sell only the gap**

## 0:44–1:06 — Transparent quote and order

### Show

The full Fibre + Email + CCTV itemised quote, pre-filled checkout, consent, **Create order**, then the generated reference.

### Say

> “On a fuller order, every quantity, setup charge and contract is visible. The server rebuilds and fingerprints the accepted catalogue plan before creating the order, so a browser cannot quietly submit a cheaper fake price.”

## 1:06–1:31 — Real document result

### Show

Purpose-specific consent, four K03 files, **Verify documents**, cut the wait, then **Documents checked**.

### Say

> “With explicit consent, local OCR reads GST, Udyam, signatory and address evidence, checks identity, status, checksum and recency, then deletes the images. This matching case passes. Across all twelve supplied synthetic applicants, the same rules produce six verified, three action-required and three rejected—not twelve convenient green ticks.”

On-screen caption: **6 verified · 3 action required · 3 rejected**

## 1:31–2:10 — Recover without starting again

### Show

Customer tracker with Email complete, Fibre failed and CCTV waiting. Calculate AirFiber alternative; show `-₹100`, accept it, then show Email still complete.

### Say

> “Now Fibre fails its site check. This tracker is a dependency graph, not a painted progress bar. Email is independent, so completed work survives; CCTV waits for compatible connectivity. UdyamOne proposes AirFiber at one hundred rupees less per month, repeats the setup and contract terms, and changes nothing until Joseph accepts. After acceptance, only the failed branch is rebuilt.”

On-screen caption: **Failure isolated · Progress preserved · Re-acceptance required**

## 2:10–2:36 — Know when not to contact

### Show

Lead assistance disclosure, AUC 0.84, then the zero-lift comparison table.

### Say

> “A high propensity score says who may activate; it does not say who benefits from a call. At zero assumed lift, propensity-first still spends five thousand rupees. The intervention-value policy contacts nobody. We claim no learned uplift—we prepare randomized pilot assignments so Airtel can measure it honestly.”

On-screen caption: **No fake uplift claim**

## 2:36–2:53 — Close

### Show

Architecture frame and proof badges: `91 Python`, `17 frontend`, `8/8 scenarios`, then the UdyamOne end card.

### Say

> “Rules decide; AI explains. Provider failures never become mock success. UdyamOne can decide not to sell, recover without erasing progress, and decide not to spend—turning Airtel from another vendor into the operating system for a small business.”

End card: **UdyamOne — Built around your business.**

## Recording rules

- Record short genuine clips and edit out typing, OCR waiting and tab changes; never edit an outcome into existence.
- Keep the mouse slow and deliberate. Pause for roughly two seconds on each result the narration names.
- Use only four short captions: `Sell only the gap`, `Failure isolated · Progress preserved`, `No fake uplift claim`, and `Rules decide · AI explains`.
- Do not show terminals, file browsing, access tokens, private documents outside the synthetic K03 pack or unrelated browser tabs.
- Do not claim live Airtel provisioning, production/legal KYC, measured uplift, guaranteed savings or production readiness.
- Export H.264 MP4 at 1920×1080, 30 fps; watch the complete export and confirm it remains under 3:00.

## Final pass/fail checklist

- [ ] The narration sounds like a founder explaining one business story, not reading a feature list.
- [ ] Advisor visibly considers the existing Email service before recommending Backup.
- [ ] Quote shows quantities, monthly charge, setup and contract.
- [ ] Checkout creates a real `UO-...` order reference.
- [ ] K03 consent, upload and genuine result are visible.
- [ ] Tracker proves Email is preserved through Fibre recovery.
- [ ] Recovery shows the revised price before explicit acceptance.
- [ ] Zero-lift assistance produces zero value-based contacts and ₹0 spend.
- [ ] Limitations remain visible and truthful.
- [ ] Final duration is 2:50–2:55.
