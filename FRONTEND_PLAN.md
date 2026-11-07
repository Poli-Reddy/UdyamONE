# Role-based frontend plan

## Design rule

Show one role only the information and actions needed for its next decision. Internal implementation details, test controls and unrelated analytics remain hidden by default.

## Customer journey

1. **Your needs** — short guided questions, then a review of locations, existing services and budget.
2. **Your plan** — Keep / Add now / Replace later, separated existing and new charges, open questions and one primary acceptance action.
3. **Verification** — available only after an order exists; show the exact documents for the accepted GST or GST-free lane, purpose-specific consent and fix instructions.
4. **Track services** — plain-language next action, progress grouped by service/location, private order reopening and customer-controlled recovery acceptance.

Future steps are visibly locked until prerequisites are complete. Customers can always return to an earlier completed step. Staff/test controls never appear in customer mode.

## Staff workspace

After authentication, customer navigation is replaced with a work workspace. Panels are role-filtered:

- **Sales / manager:** lead insights, actual order blockers and assisted-planning queue.
- **KYC reviewer:** orders waiting for documents or review; no activation mutation.
- **Activation operator:** verified orders, delayed/failed service branches and evidence controls.
- **Billing:** payment status/reconciliation work only.
- **Analyst / manager:** synthetic-case model and assistance experiment; hidden in production until an approved model exists.
- **Partner:** own signed referral, QR and attributed aggregate only.
- **Administrator:** connection status and configuration diagnostics; no automatic right to accept customer terms.

Selecting an order opens the same service tracker with staff-only evidence controls appropriate to the authenticated role.

## Interaction standards

- One emphasized primary action per card.
- Explain why a field is requested and what happens next.
- Use customer language, not task IDs or provider-variable names.
- Keep advanced quantities, contracts and test tools collapsed.
- Preserve keyboard focus, labels, status announcements and mobile layout.
- Disable unavailable navigation rather than opening an empty screen.
- Display estimates as estimates and simulations as simulations.
