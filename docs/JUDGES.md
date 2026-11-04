# Two minutes to judge Ode

**Give Muse a body on the iPad you already own.** [Full demo]([YOUTUBE_URL]) · [README](../README.md) · [Editing disclosure](DEMO.md)

## A two-minute review path

- **0:00–0:40 of your review:** watch film **00:30–01:10**. Follow owner request → Camera/photo → draft → fresh confirmation → one physical send. Open the [existing X result](https://x.com/hubeiqiao/status/2117654510482960846).
- **0:40–1:05:** watch **01:22–01:35** and compare the [real sanitized model/HID excerpt](DEMO.md#sanitized-real-run-excerpt). Muse Spark's located search target becomes board input and a changed screen.
- **1:05–1:30:** watch **02:03–02:20** and inspect the [phone UI captures](../README.md#companion-on-your-phone). Goals return local review drafts; Feed retains a checked quote/source; Library is owner-controlled. These captures include saved state.
- **1:30–2:00:** inspect the rubric map and linked code below. The [test inventory/results](TESTING.md) distinguish definitions, executed cases and excluded private-frame regressions. Optionally watch **02:20 onward** for the live Ottawa demonstration.

## Rubric → inspectable evidence

| Criterion | Evidence | Why it matters / boundary |
| --- | --- | --- |
| **Impact — 30%** | Film 00:30, 01:12 and README features; real [X output](https://x.com/hubeiqiao/status/2117654510482960846); source-bearing Feed screenshot | Reuses an idle tablet for everyday app tasks without a separate integration per app. The board is ~$5; Mac/tablet/APIs are additional. No measured time-saving or accessibility-product claim. |
| **Use of Meta AI Tooling — 25%** | Film 01:22; `muse-spark-1.3` decision excerpt; [Meta client](../server/hidagent/meta_model.py), [hybrid actor](../server/hidagent/hybrid_agent.py), [Goal planner](../server/hidagent/app_mission.py), [draft writer](../server/hidagent/app_tools.py) | Spark makes device decisions and source-bound writing decisions. Jev is the primary judge; Gemini handles voice/camera. Optional Meta profiles are not extra demo models. |
| **Demo — 25%** | Film 00:30–01:12 causal sequence, existing result, 02:20 stage excerpt; [timestamp/edit disclosure](DEMO.md) | Real physical iPad footage and owner interaction. Hook is composited; time is compressed; rednote answer uses the search AI summary. Publication uncertainty was not retried. |
| **Technical Depth & Execution — 20%** | [Architecture](ARCHITECTURE.md), geometry/input, no-replay and source tests below, source + rebuilt UI, iPad firmware | Calibration, evidence checks and cancellation connect model proposals to physical input. Offline checks do not establish live app coverage. |

A shareable hook is already available: Muse entering a spare iPad, followed by the board visibly operating it. The existing X result provides a public result link; no views, likes, repost counts or viral bonus are claimed.

## Three engineering decisions to inspect

| Problem | Implementation | Regression examples |
| --- | --- | --- |
| Landscape geometry, accelerated or hidden pointer | [Screen negotiation](../server/hidagent/screen.py), [calibrated input](../server/hidagent/phone.py), [icon location](../server/hidagent/icon_locator.py) | [Native mirror](../server/tests/test_native_mirror.py), [Home icons](../server/tests/test_home_icons.py), [landscape frames](../server/tests/test_landscape_real_frames.py); private-image cases explicitly skip |
| Stop, late model completion, lost board ACK | [Revocable input leases](../server/hidagent/hid_lease.py), [durable receipts](../server/hidagent/receipt_journal.py), [one-use photo send](../server/hidagent/x_photo_flow.py) | [Goal stop](../server/tests/test_app_mission.py), [restart/no replay](../server/tests/test_receipt_journal.py), [lost ACK](../server/tests/test_device_handshake.py), [photo send](../server/tests/test_x_photo_landscape.py) |
| A proposed discovery is not an observed source | [Exploration](../server/hidagent/exploration_agent.py), [result reader](../server/hidagent/app_result_reader.py), [source-bound Goals](../server/hidagent/app_mission.py) | [Independent rejection](../server/tests/test_exploration_landscape.py), [completed-only persistence](../server/tests/test_companion.py), [Goal source boundaries](../server/tests/test_app_mission.py) |

## Reproduce safely

Follow [Hardware](HARDWARE.md), [Setup](SETUP.md) and [Testing](TESTING.md). The public export omits personal device images, templates, runtime state and private history. Do not replay the recorded public send. Offline checks need no live models or device. A fresh physical acceptance run needs an attended, explicitly authorized setup.

Based on [ESP32 iPhone Agent](https://github.com/onehumanbeing/esp32-iphone-agent) by Henry Yuan (MIT). Memory store: Hermes Agent, Nous Research (MIT). Muse character: Meta · not affiliated with or endorsed by Meta.
