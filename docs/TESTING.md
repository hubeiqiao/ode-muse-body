# Ode — Give Muse a body: verification

Run from the repository root with Python 3.12 and Node 22+:

```sh
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -e './server[dev,mcp]'
npm ci --prefix tools/twitter-text-validator
(cd server && PYTHONPATH=. ../.venv/bin/python -m pytest -q -rs -p no:cacheprovider)
node --test server/tests/*.test.mjs server/tests/*.test.cjs
npm ci --prefix companion-ui
npm run build --prefix companion-ui
cp -R companion-ui/dist/. server/hidagent/static/companion/
```

Run Python tests from `server/`; a few inherited subprocess tests use that working directory. The suite uses temporary runtime state, in-process ASGI/MockTransport clients, synthetic images and isolated ephemeral-port protocol doubles. Standard device-service ports are blocked during tests. No iPad, board, model API, flashing or publishing is needed. Offline Swift protocol checks use doubles and never start a native device runner.

Private captured frames/crops are excluded. Tests that actually depend on them skip with an explicit reason; synthetic geometry, routing, refusal and safety tests still execute. Some tests require macOS Vision/Swift and skip if unavailable. A skipped pixel regression is not a verified app/layout result.

Remote authorization tests verify the ASGI boundary for HTTP and WebSockets, JWT signature and claim checks, owner tokens, wrong origins, spoofed forwarding headers and default-denied non-local traffic. They do not provision a live Cloudflare application or tunnel.

Firmware can be compiled with ESP32 core **3.3.8** and NimBLE-Arduino **2.3.9**, using an empty local copy of `secrets.example.h`; see [HARDWARE.md](HARDWARE.md). A successful build is distinct from flashing, pairing, calibration or a real-device task. Build artifacts are excluded from this repository.

Two inherited warnings may appear: Starlette's httpx/TestClient deprecation and a Pydantic field-name warning in the optional Grok preview model. The companion build may emit dependency directive and chunk-size warnings. None of these establish physical acceptance.

Final submission acceptance requires the owner to record and inspect the three segments in [DEMO.md](DEMO.md), check current model availability, and test their own tunnel if enabled. The external local release report records exact results for this snapshot; this guide deliberately distinguishes reproducible checks from live claims.
