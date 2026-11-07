# Security policy

UdyamOne is moving from a synthetic hackathon prototype toward a controlled pilot. It is not currently authorised for real customer KYC, charging or Airtel provisioning.

Report suspected vulnerabilities privately to the repository owner. Do not include customer data, credentials or organiser-provided documents in an issue. Rotate any exposed secret immediately and preserve relevant audit records.

Production deployments must use OIDC staff identity, customer OTP, PostgreSQL migrations, HTTPS, a secrets manager, encrypted storage, gateway rate limits and `UDYAM_DEMO=false`. The application intentionally refuses production startup when required identity or database settings are missing.
