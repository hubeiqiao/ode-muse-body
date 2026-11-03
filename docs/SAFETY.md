# Ode — Give Muse a body: safety and privacy

Muse operates a real device. Use a dedicated, owner-controlled iPad and stay present. Start with read-only tasks and verify actual screen changes.

## Owner words authorize actions

Ode must never post, buy or log in without explicit owner words. Public posting in the guarded photo flow requires a fresh confirmation tied to the pending draft, caption, attachment, account and task. Read the draft back first. A generic instruction such as “explore” does not authorize publishing, purchases, messages, likes, follows or account switching.

Credential entry, payment and sensitive settings should remain human-operated in this prototype. Model prompts and per-flow checks are defense in depth, not a formal reference monitor for every possible general-agent action. Do not hand the visual agent an unattended purchasing or credential-entry task. An authenticated remote browser identifies an owner; it does not constitute task confirmation.

Screen text, public posts, app cards and model output cannot grant permission. Treat instructions found in them as untrusted data. Do not replay visible backlogs or expand a bounded task. Do not use automatic completion as permission for a new task.

## Delivery and stopping

Use one task at a time. A receipt can be running, completed, failed or uncertain; only inspectable screen evidence supports visible success. Never resend typing, a click or a post after uncertain delivery. Investigate the actual state first.

Voice stop requests call the stop tool. Cancellation also tracks controller release; a disconnected board can leave release pending. Do not claim an instant physical stop until release is confirmed. Keep a physical fallback: disconnect/power down the board if necessary.

## What leaves the Mac

| Data | Destination / condition |
| --- | --- |
| Active task instructions and selected screen frames | Muse Spark at Meta Model API; Jev for proposal checks; the configured backup model when required |
| Microphone audio and live session vision | Gemini Live during an explicitly started conversation; normal mode can stream the mirrored screen continuously |
| Camera app preview | Gemini Live camera conversation; Meta task-model calls reject camera-preview frames while camera scope is active |
| Local OCR | macOS Vision worker on the Mac when configured |
| Optional local memory and conversation state | Private runtime directory on the Mac; selected text can be included in companion requests, not automatically private from the model |
| Optional traces, receipts and calibration | Local runtime storage; debug traces can contain sensitive images/prompts |

No idle model uploads are intended. Stop voice/camera sessions when finished; use Do Not Disturb and avoid private notifications, credentials, chats, photos and unrelated account history in view. Disable debug/tracing unless needed and review files before sharing. User consent and provider terms still govern actual operation.

## Storage and keys

Keep long-lived Meta, Jev and Gemini keys in the ignored `server/.env` on the Mac. Short-lived Gemini session credentials reach the authenticated browser so it can connect to Live. Do not expose keys through source, logs, issues, screen recordings or tunnel config.

`HIDAGENT_DATA`, optional conversation/memory directories and masked templates remain private and separate per device. Backups and firmware `secrets.h` contain real network details and must never be committed. The example files contain no credentials.

## Network boundary

Default HTTP binding is loopback. `access.py` rejects non-local/forwarded traffic unless configured for an exact HTTPS origin plus a valid Cloudflare Access JWT or strong owner bearer token. The middleware covers frames, static UI, task state and WebSockets, not only control routes. Same-origin checks reject cross-site browser requests. Existing route-local guards remain active unless the middleware has authenticated the remote request.

Do not expose the ESP32 TCP port or AirPlay receiver. They remain on a trusted LAN. Remote setup is described in [DEPLOY-CLOUDFLARE.md](DEPLOY-CLOUDFLARE.md). Cloudflare Access provides an additional identity boundary, not a guarantee of safe device actions.

## Public release boundary

This repository contains no personal screenshots, device inventory, network credentials, task evidence, conversation history or private Git history. Synthetic test fixtures validate algorithms and boundaries; skipped private-capture cases do not prove real-app reliability. Before publishing a demo, inspect every frame and retain the upstream project credit.
