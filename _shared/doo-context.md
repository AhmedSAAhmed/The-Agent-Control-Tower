# DOO context - who the tower is for

DOO (doo.ooo) is a Bahrain-founded, Arabic-first AI customer-service platform. Its agents answer
customers in their own language and dialect on WhatsApp, Instagram, voice, email, SMS and web chat,
take actions in the client's systems, and hand over to a human team in a shared inbox. Customers
include telecom, banking, food delivery, government and education teams across the GCC. DOO's own
product for building, training and correcting agents is called CTRL.

What that means for the tower:

- **Multi-tenant is the default.** One operator team oversees agents for many client brands, each
  with its own currency, thresholds and data boundary.
- **Arabic first.** Tickets arrive in Gulf Arabic and English; replies must match the customer.
- **Agents act, not just answer.** Refunds, payouts and onboarding decisions are side effects that
  need policy, approval and audit.
- **Inbound text is untrusted.** A customer message is the obvious prompt-injection channel.

## Demo tenants (fictional, mirroring DOO's sectors)

| Tenant | Sector | Region | Currency | Agents |
|---|---|---|---|---|
| Pearl Bank | Retail banking | Bahrain | BHD | Noor (KYC), Faisal (withdrawals) |
| Dilmun Telecom | Telecom | Bahrain | BHD | Layla (support) |
| Rimal Eats | Food delivery | Saudi Arabia | SAR | Saad (support) |

Thresholds per tenant live in `app/policy.yaml` (see [risk-policy.md](risk-policy.md)).
