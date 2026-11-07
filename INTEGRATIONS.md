# Provider setup and boundaries

No accounts or keys were available during implementation. Adapters were tested with controlled provider responses; no live registry lookup, payment or WhatsApp send was performed.

## Local configuration

Copy `.env.example` to `.env`. Launch with the provided run script, or add `--env-file .env` to Uvicorn. `/api/integrations` returns statuses without exposing keys. `configured_not_verified` means keys are present, not that they work.

Keep the default demo on localhost. Before exposing a webhook host, set `UDYAM_DEMO=false`, set a strong `ADMIN_TOKEN`, and use HTTPS. The service still uses a fictional catalogue and cannot provision real Airtel products.

## Cashfree Secure ID: GSTIN and Udyam

1. Obtain an approved Secure ID account and sandbox credentials.
2. Set `CASHFREE_CLIENT_ID`, `CASHFREE_CLIENT_SECRET`, and `CASHFREE_MODE=sandbox`.
3. Create an order and retain its returned `access_token`.
4. In the KYC provider panel, select the identifier type, enter the registration number, and consent to sending that number and the order business name to Cashfree.
5. Test provider-approved fixtures first. The local synthetic GSTIN/Udyam pack is not a source of valid sandbox/live identities.

`POST /api/orders/{id}/registry` accepts `kind`, `number`, `consent`; it requires `x-order-token`. GSTIN must return an active valid registration and exact requested number. Udyam must return SUCCESS and the requested number. Normalized legal/trade/enterprise name must match the order business name; aliases need review. The response retains only status, match, provider reference, mode, consent version and scope. Social-category and other unnecessary provider fields are discarded.

The endpoint records **registry evidence**, not complete telecom KYC. Signatory authority and installation address still need their own authorised verification. The synthetic OCR workflow never silently switches to Cashfree or treats provider failure as a mock success. `CASHFREE_MODE=production` selects the documented production host when credentials and account entitlement exist; that mode was not tested against Cashfree.

Sources: [GSTIN verification](https://www.cashfree.com/docs/api-reference/vrs/v2/gstin/verify-gstin), [Udyam verification](https://www.cashfree.com/docs/api-reference/vrs/v2/udyam/verify-udyam).

## Razorpay: hosted test checkout

1. Obtain test keys and configure `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET` and `PAYMENT_MODE=test`.
2. Point a Razorpay test webhook to `POST /api/webhooks/razorpay`; subscribe to `payment_link.paid`, `payment_link.cancelled`, `payment_link.expired`.
3. Create a test link from the order tracker. The server calculates **first month plus setup**, in paise, from the accepted order. Tax is unspecified in the case catalogue; this is not a tax invoice or recurring mandate.
4. Use only Razorpay test payment methods. The webhook validates HMAC-SHA256 over the raw body, checks link/reference/amount/currency/captured payment association, and records payment status independently of service activation.

Creating a link does not email, SMS or WhatsApp anyone; provider notification flags are false. Card details remain on Razorpay's hosted checkout.

Repeated link creation returns the existing link. A failed/ambiguous provider call leaves `needs_reconciliation`; it never creates another link blindly. An operator finds the saved `reference` in Razorpay, then calls `POST /api/orders/{id}/payment/reconcile` with `provider_id` and `x-admin-token`. The adapter retrieves the link and verifies the order reference before updating local state. If no remote link exists, manual investigation is required; no automatic charge retry is attempted.

Duplicate webhook events are idempotent. Late expiry/cancellation events do not downgrade paid status. A payment link blocks automatic repricing during recovery; billing review is required. Refunds, recurring mandates, settlements and live invoicing are outside this prototype.

**Live keys are rejected by code** because the product catalogue is fictional. A real commercial release requires an authorised tariff/order source and payment acceptance testing, not just a configuration toggle.

Sources: [Create Payment Link](https://razorpay.com/docs/api/payments/payment-links/create-standard/), [Webhook signatures and ordering](https://razorpay.com/docs/webhooks/validate-test/), [Payment Link payloads](https://razorpay.com/docs/webhooks/payment-links/).

## Meta WhatsApp Cloud API

1. Configure an approved Meta app, WhatsApp Business number, access token and app secret.
2. Set `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_APP_SECRET`, `WHATSAPP_VERIFY_TOKEN`, `WHATSAPP_GRAPH_VERSION` to your app's supported API version. The code does not guess the current version.
3. Register `/api/webhooks/whatsapp` as the HTTPS callback. GET verifies `hub.verify_token`; POST verifies `X-Hub-Signature-256` over the raw body and checks the target phone-number ID.
4. Send START from a permitted test number. The bot asks for purpose-specific consent, then business details, needs, existing service and budget. It calls the same planner as the website.

Commands: START, HELP, STOP, TRACK followed by order ID. Tracking requires consent and an exact normalized match between the inbound WhatsApp number and the order phone. Orders/access tokens are not accepted as authorization from an unrelated number. No order or payment is created in chat.

The English pilot supports one location and one existing subscription; it refers more complex cases to the website. `/api/whatsapp/preview` runs the conversation locally without sending messages, and is disabled when demo mode is off.

Only signed inbound messages from within 24 hours are answered; messages more than five minutes into the future are ignored. There are no proactive campaigns or templates. Inbound IDs are durably deduplicated. Ambiguous send failures are recorded as `send_unknown_no_auto_retry`; webhook retries do not send duplicates. Operators must investigate delivery; automated outbox/retry workers and delivery-receipt monitoring remain production work.

Sessions expire after 24 hours and are removed lazily on the next inbound message. STOP deletes the session immediately. Minimal message-ID/status receipts remain to prevent replay; schedule receipt retention before production. Raw webhook bodies and chat transcripts are not stored. The conversational profile is stored until STOP/expiry.

Sources: [Meta-hosted receiving guide](https://whatsapp.github.io/WhatsApp-Nodejs-SDK/receivingMessages/), [signature verification behaviour](https://whatsapp.github.io/WhatsApp-Nodejs-SDK/api-reference/webhooks/start/), [text message request](https://whatsapp.github.io/WhatsApp-Nodejs-SDK/api-reference/messages/text/). Meta's current developer portal could not be retrieved during this session; confirm app-version requirements during account setup.

## Airtel provisioning

No public provisioning endpoint was invented. `activation.py` defines task/event boundaries for site feasibility, installation/configuration and useful-service checks. Production work requires Airtel's authorised API specification and mappings for catalogue IDs, customer/order identifiers, feasibility, scheduling, activation checks, retries and cancellation. Operator-recorded evidence is not a live Airtel API response.

The demo route `/api/orders/{id}/demo-verification` explicitly records `demo_only`, not legal KYC. It is disabled outside demo mode. Do not use demo events as proof of real service activation.
