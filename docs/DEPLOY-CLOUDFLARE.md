# Protected remote access

[Documentation](README.md) · [Project](../README.md)


The Mac remains Ode's host. Cloudflare Tunnel gives its local web server a custom HTTPS hostname; Cloudflare Access identifies the owner. Long-lived model keys, ESP32 connections and the AirPlay receiver stay on the Mac/LAN. This guide prepares an optional deployment; no tunnel is provisioned by the repository.

## Why a Worker alone cannot run Ode

The server owns a local ESP32 TCP connection, spawns an AirPlay/GStreamer receiver, reads local frames and can run a macOS OCR worker. A Worker cannot replace those local OS processes and hardware connections. A Worker may provide a separate landing page, but the device host must remain local.

## Create an Access application first

In Cloudflare Zero Trust, configure an identity provider or **One-time PIN** (email OTP). Under **Access → Applications**, add a **Self-hosted** application for your exact hostname, for example `ode.example.com`, covering **all paths**. Add an **Allow** policy for only the owner's selected email/identity or group. Do not use “Everyone”, an allow-all policy, or a path exception. Choose a short session duration appropriate to device control.

Record the application **AUD tag** and your team's issuer URL (`https://YOUR_TEAM.cloudflareaccess.com`). These are identifiers, not API keys. Keep the real owner's email out of this repository. The browser authenticates at Access and the origin verifies the signed `Cf-Access-Jwt-Assertion` independently. [Cloudflare JWT validation reference](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/validating-json/).

## Create the locally managed tunnel

Install `cloudflared` using Cloudflare's supported package for your Mac. Authenticate and create a named tunnel from a terminal on that Mac:

```sh
cloudflared tunnel login
cloudflared tunnel create ode
cloudflared tunnel list
mkdir -p "$HOME/.cloudflared"
cp deploy/cloudflared/config.example.yml "$HOME/.cloudflared/ode.yml"
```

Edit `~/.cloudflared/ode.yml` locally: replace the tunnel UUID, the absolute credentials-file path produced by `tunnel create`, and the hostname. Keep `service: http://127.0.0.1:8890` and the final 404 catch-all. Do not commit tunnel credentials, account certificates or a filled configuration.

```sh
cloudflared tunnel route dns ode ode.example.com
cloudflared tunnel --config "$HOME/.cloudflared/ode.yml" ingress validate
cloudflared tunnel --config "$HOME/.cloudflared/ode.yml" ingress rule https://ode.example.com
```

[Cloudflare locally managed tunnel commands](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/local-management/create-local-tunnel/).

## Enable the origin's authentication gate

Add these values to the Mac's ignored `server/.env`, replacing the illustrative team/audience values:

```sh
ODE_PUBLIC_ORIGIN=https://ode.example.com
ODE_ACCESS_ISSUER=https://YOUR_TEAM.cloudflareaccess.com
ODE_ACCESS_AUD=REPLACE_WITH_APPLICATION_AUD
```

Restart Ode using the root README's Muse profile and `./scripts/run-ode.sh`. Keep `HOST=127.0.0.1` and `PORT=8890`. Run the tunnel in another terminal:

```sh
cloudflared tunnel --config "$HOME/.cloudflared/ode.yml" run ode
```

The server validates RS256 signatures using your configured team's public signing keys, checks issuer/audience/expiry/issued-at/subject, and fails closed if configuration or validation is missing. Merely presenting the header is insufficient. Forwarded requests from the local tunnel still require authentication. The supplied Python entrypoint disables proxy-header rewriting; do not run a custom entrypoint that blindly trusts forwarded client identity.

Open the custom HTTPS hostname, complete email OTP or identity login, then visit `/companion`. The hostname must exactly match `ODE_PUBLIC_ORIGIN`; origins are not wildcarded. Authenticated same-origin requests pass route-local control guards. API tokens and camera/task authorization are separate concerns.

## Optional owner-token clients

For a trusted API client instead of JWT authentication, set `ODE_OWNER_TOKEN` to a high-entropy value of at least 32 characters in the Mac's ignored environment file. Generate one locally with `python3 -c 'import secrets; print(secrets.token_urlsafe(32))'`. Retain `ODE_PUBLIC_ORIGIN` and keep Cloudflare Access enabled. Send `Authorization: Bearer <owner-token>` in an HTTP header, never a URL/query parameter. Browser UI users should use Access JWT mode; the UI does not store or inject a bearer token.

Either valid authentication method authorizes the origin request when configured. Leaving `ODE_OWNER_TOKEN` unset gives JWT-only remote access. A short/empty token, missing origin, wrong Host, wrong Origin or unverified Access header is rejected. Localhost works without remote configuration; external traffic remains denied by default.

## Verify before device use

1. With a private/unauthenticated browser, confirm Access challenges both `/companion` and `/api/status`.
2. Sign in as the allowed owner. Confirm the companion loads and read-only status/frame requests succeed. A non-allowed identity must be refused.
3. From the Mac, confirm the origin itself refuses a spoofed forwarded request without credentials:

   ```sh
   curl -i http://127.0.0.1:8890/api/status -H 'Host: ode.example.com' -H 'X-Forwarded-For: 203.0.113.10'
   # Expected: 403, Owner access required.
   ```

4. With valid Access identity, test a harmless read-only task while watching the real iPad. Confirm stop and session ending. Never use a publish action as an auth smoke test.
5. Stop `cloudflared` when remote access is no longer needed. Review Access sessions/policies and revoke access as appropriate.

Offline auth tests cover signature/claims, wrong origins, forged forwarding headers, default-denied remote traffic, owner tokens and WebSockets. They do not provision or certify your live Cloudflare account. No ESP32/AirPlay port belongs in `ingress`.
