# Two minutes to judge Ode

**Give Muse a body on the iPad you already own.** [Full demo](https://youtu.be/0wcoqi5CVuQ) · [README](../README.md) · [Editing disclosure](DEMO.md)

## A two-minute review path

- **First 50 seconds of your review:** watch film **00:29–01:17**. Follow room description → Psyduck → photo → draft → fresh confirmation → one physical send and result. [Existing X result](https://x.com/hubeiqiao/status/2117654510482960846).
- **Next 20 seconds:** watch **01:30–01:46** and compare the [sanitized model/HID excerpt](DEMO.md#sanitized-real-run-excerpt). Muse Spark locates a search target; board input changes the screen. This is one selected real step, not proof of model planning at every tap.
- **Next 20 seconds:** watch **02:07–02:24** and inspect the [phone UI captures](DEMO.md#companion-and-capability-boundaries). Goals return local review drafts; Feed retains a checked quote/source; Library is owner-controlled. These captures include saved state.
- **Final 30 seconds:** inspect the rubric/source map below. [Testing](TESTING.md) separates static definitions, current execution and excluded private-frame cases. Optional: **02:24 onward**, live Ottawa demonstration; Amazon at **02:33.47**.

The opening is Muse's composite leap → “Muse had no body” → Meet Ode's redrawn session chat → ESP32 explanation timed to real HID logs. The [complete chapter list](DEMO.md#chapters-for-the-final-v6-cut) includes the 01:50 architecture beat.

## What Ode adds to its foundations

Henry Yuan's ESP32 iPhone Agent supplies the original device-control foundation. Ode builds on it with a Muse-backed landscape iPad companion, calibrated input/recovery, bounded source-checked Goals and Feed, owner-controlled memory UI, and durable no-replay receipts. Hermes supplies selected memory-store components. This is integration and execution work, with no new model-training or historical-priority claim.

**Credit the right layer:** Gemini Live describes the room and Psyduck; the filmed Camera/X path uses measured taps. Muse Spark's clearest filmed screen-to-action proof is rednote at **01:30.17**. The general visual path and Goal/writing routes are implemented in code; saved UI state alone does not demonstrate a new run. [Model routing](ARCHITECTURE.md#model-routing).

## Rubric → inspectable evidence

| Criterion | Evidence | Why it matters / boundary |
| --- | --- | --- |
| **Impact — 30%** | Film 00:29, 01:17 and README features; real [X output](https://x.com/hubeiqiao/status/2117654510482960846); source-bearing Feed screenshot | Intended for builders/creators with a Mac and spare iPad: attended app research, checked findings and review drafts without a separate integration per app. The board is ~$5; Mac/tablet/APIs are additional. No measured time-saving or accessibility-product claim. |
| **Use of Meta AI Tooling — 25%** | Film 01:30.17; `muse-spark-1.3` decision excerpt; [Meta client](../server/hidagent/meta_model.py), [hybrid actor](../server/hidagent/hybrid_agent.py), [Goal planner](../server/hidagent/app_mission.py), [draft writer](../server/hidagent/app_tools.py) | Spark makes device decisions and source-bound writing decisions. Jev is the primary judge; Gemini handles voice/camera. Optional Meta profiles are not extra demo models. |
| **Demo — 25%** | Film 00:29–01:17 causal sequence, existing result, 02:24 stage excerpt; [timestamp/edit disclosure](DEMO.md) | Real physical iPad footage and owner interaction. Hook is composited; time is compressed; rednote answer uses the search AI summary. Publication uncertainty was not retried. |
| **Technical Depth & Execution — 20%** | [Architecture](ARCHITECTURE.md), geometry/input, no-replay and source tests below, source + rebuilt UI, iPad firmware | Calibration, evidence checks and cancellation connect model proposals to physical input. Offline checks do not establish live app coverage. |

A shareable hook is already available: Muse entering a spare iPad, followed by the board visibly operating it. The existing X result provides a public result link; no views, likes, repost counts or viral bonus are claimed.

## Three engineering decisions to inspect

| Problem | Implementation | Regression examples |
| --- | --- | --- |
| Landscape geometry, accelerated or hidden pointer | [Screen negotiation](../server/hidagent/screen.py), [calibrated input](../server/hidagent/phone.py), [icon location](../server/hidagent/icon_locator.py) | [Native mirror](../server/tests/test_native_mirror.py), [Home icons](../server/tests/test_home_icons.py), [landscape frames](../server/tests/test_landscape_real_frames.py); private-image cases explicitly skip |
| Stop, late model completion, lost board ACK | [Revocable input leases](../server/hidagent/hid_lease.py), [durable receipts](../server/hidagent/receipt_journal.py), [one-use photo send](../server/hidagent/x_photo_flow.py) | [Goal stop](../server/tests/test_app_mission.py), [restart/no replay](../server/tests/test_receipt_journal.py), [lost ACK](../server/tests/test_device_handshake.py), [photo send](../server/tests/test_x_photo_landscape.py) |
| A proposed discovery is not an observed source | [Exploration](../server/hidagent/exploration_agent.py), [result reader](../server/hidagent/app_result_reader.py), [source-bound Goals](../server/hidagent/app_mission.py) | [Independent rejection](../server/tests/test_exploration_landscape.py), [completed-only persistence](../server/tests/test_companion.py), [Goal source boundaries](../server/tests/test_app_mission.py) |

## Evidence boundaries worth checking

- **Board ACK:** command acceptance, not proof of app success. **Closed composer:** not independent proof of publication. The film's result card and existing post are the publication evidence; web retrieval of X was unavailable during review.
- **Photo confirmation:** the guarded flow consumes a fresh draft authorization before its send. Preparation checks an attachment and nonempty caption; the operator still checks the actual account, exact caption and photo. Photo drafts skip the text-only readback-complete gate; spoken readback in this film is not a universal enforced check. [Flow](../server/hidagent/x_photo_flow.py) · [voice branch](../server/hidagent/ipad_demo_flows.py) · [limits](SAFETY.md).
- **Offline evidence:** synthetic regressions and protocol doubles exercise failure handling; excluded private images and macOS-only cases can skip. CI does not pair BLE, inspect apps or measure consecutive-run reliability. [Execution record and failure map](TESTING.md).

## Reproduce safely

Follow [Hardware](HARDWARE.md), [Setup](SETUP.md) and [Testing](TESTING.md). The public export omits personal device images, templates, runtime state and private history. Do not replay the recorded public send. Offline checks need no live models or device. A fresh physical acceptance run needs an attended, explicitly authorized setup.

Based on [ESP32 iPhone Agent](https://github.com/onehumanbeing/esp32-iphone-agent) by Henry Yuan (MIT). Memory store: Hermes Agent, Nous Research (MIT). Muse character: Meta · not affiliated with or endorsed by Meta.
