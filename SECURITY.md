# Security policy

## Supported scope

Ode is a research prototype. Security fixes target the current `main` branch; older snapshots have no separate maintenance commitment. Read [Safety and data flow](docs/SAFETY.md) before connecting an owner-controlled device or enabling remote access.

## Report a vulnerability privately

Email **[Joe Hu](mailto:hubeiqiao77@gmail.com)** with the subject **Ode security report**. Include the affected commit, component, impact and a minimal synthetic reproducer. Do not open a public issue with an exploit, credential, private device frame or personal record. Send no API keys or filled configuration; redact identities and use synthetic data.

The maintainer will assess the report and coordinate disclosure and remediation with the reporter. This volunteer project does not promise a response deadline or a bug bounty.

## Relevant boundaries

HTTP defaults to loopback. Optional remote control requires an exact HTTPS origin plus verified Cloudflare Access JWT or a strong owner token. Board TCP has no application-layer authentication/encryption; keep it and AirPlay on a trusted LAN. Cloud model providers receive explicitly enabled task data. See [remote access](docs/DEPLOY-CLOUDFLARE.md) and [action/data limits](docs/SAFETY.md) for the complete boundaries.
