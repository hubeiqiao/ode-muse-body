# Offline verification

[Documentation](README.md) · [Project](../README.md)


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

The recorded v6 film and its editing boundaries are described in [DEMO.md](DEMO.md). A new setup still requires device acceptance, current model access and live tunnel verification if enabled. No live device/model task was run during this export.

## November 4 judge-panel verification

Re-executed against release base `b0a5a95` with this documentation-only update, using Python **3.12.8** on macOS and Node **22.23.4**. The final commit is identified by this document's Git history; its pushed [Offline verification workflow](../.github/workflows/ci.yml) supplies the separate CI result.

| Check | Current local result |
| --- | --- |
| Python suite | **3,620 successful cases, 352 skipped, 0 failures**, 2 warnings; 138.26 s |
| Node suite | **83 successful cases, 0 failures/skips**, 14 files |
| Companion source build | Completed with Node 22; dependency directive/chunk-size warnings remain |
| Physical/device acceptance | No new device task, model API call, publication, pairing or board write performed |

These are executed cases, separate from the static definition inventory. The skip/fixture/platform limits below still apply. Local macOS checks and Ubuntu CI are different environments; neither establishes current real-app completion rates.

### Failure handling → inspectable regressions

| Failure or boundary | Regression evidence |
| --- | --- |
| Lost ACK after possible write | [`test_device_handshake.py`](../server/tests/test_device_handshake.py): `test_lost_ack_after_payload_is_ambiguous_and_never_retried` |
| Restart with saved request | [`test_receipt_journal.py`](../server/tests/test_receipt_journal.py): `test_same_saved_request_after_restart_returns_history_without_calling_models_or_hid` |
| Late model completion after Stop | [`test_app_mission.py`](../server/tests/test_app_mission.py): `test_stop_revokes_swallowed_model_cancellation_and_holds_ownership` |
| Native landscape geometry/rounding | [`test_native_mirror.py`](../server/tests/test_native_mirror.py): `test_mini_native_rounding_keeps_exact_template_sizes_without_forcing_aspect` |
| Lost photo-send ACK | [`test_x_photo_landscape.py`](../server/tests/test_x_photo_landscape.py): `test_lost_post_ack_consumes_draft_before_send` |

These tests exercise offline contracts with synthetic state/doubles. They do not establish BLE, app-layout or source-truth acceptance.

## Verification of the November 3 release export

Executed in this exported repository with Python 3.12.9 and Node 22.23.4, using isolated test data. No live device/model task or board write was performed.

| Check | Measured result |
| --- | --- |
| Full Python suite | 3,620 successful cases, 352 skipped, 0 failures; 2 warnings; 135.06 s |
| Full Node suite | 83 successful cases, 0 failures/skips; 14 test files |
| Static Python inventory | 2,022 test function definitions in 169 files; this is not the executed case count |
| Companion source build | Rebuilt with Node 22; generated HTML/CSS/JS/avatar bytes match the shipped bundle; required notices retained separately |
| ESP32-C3 firmware compile | 1,208,301 / 1,310,720 bytes program storage (92%); 43,988 / 327,680 bytes RAM (13%); no board flashed |

The 352 skips cover intentionally excluded device frames/crops and optional local fixtures. They do not establish app/layout acceptance. Python warnings are the inherited Starlette httpx/TestClient deprecation and optional Grok-preview Pydantic field-name warning. Vite emits dependency directive/chunk-size warnings. The firmware has limited remaining program storage. These are offline source/build results, separate from the filmed device evidence.

[Export scope and reproduction](RELEASE.md) · [Judge guide](JUDGES.md)
