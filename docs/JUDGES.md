# Two minutes to judge Ode

**Give Muse a body on the iPad you already own.** [Full demo](https://youtu.be/0wcoqi5CVuQ) · [README](../README.md) · [Editing disclosure](DEMO.md)

**Indie Developer Track · Push the Frontier:** connect Muse screen reasoning to physical iPad input.

## The case in 30 seconds

**New capability:** Muse Spark screen reasoning becomes physical Bluetooth input on an unmodified iPad. **First intended user:** a solo builder/creator researching public app content and preparing work for review. **Long-term ambition:** useful bodies for personal AI on devices people already own. [Vision and adoption path](VISION.md).

Start at [01:30](https://youtu.be/0wcoqi5CVuQ?t=90): real app pixels → Spark target → ESP32 input → changed screen. Then inspect the result and source map below. Ode is built with the Muse Spark API; it is not a client for the hosted Muse app's accounts, tasks or memory.

| 1 · Observe | 2 · Decide | 3 · Act | 4 · Inspect |
| --- | --- | --- | --- |
| Real rednote screen mirrored to Mac | Muse Spark returns the search-entry box | Calibrated motion → ESP32 Bluetooth click | Search opens on the real iPad; retain the receipt |

This is the selected filmed locate call, not a new model call at every tap. The [exact record](DEMO.md#sanitized-real-run-excerpt) separates model time from input acknowledgement.

## A two-minute review path

- **First 25 seconds of your review:** watch [film 01:30–01:46](https://youtu.be/0wcoqi5CVuQ?t=90) and compare the [sanitized model/HID excerpt](DEMO.md#sanitized-real-run-excerpt). Muse Spark locates rednote's search target; ESP32 input changes the real screen. This is one selected step, not proof of model planning at every tap.
- **Next 50 seconds:** watch [00:29–01:17](https://youtu.be/0wcoqi5CVuQ?t=29). Follow room description → Psyduck → photo → draft → fresh confirmation → one physical send and result. Gemini Live supplies conversation; a measured-tap skill supplies this input sequence. [Existing X result](https://x.com/hubeiqiao/status/2117654510482960846).
- **Next 20 seconds:** watch [02:07–02:24](https://youtu.be/0wcoqi5CVuQ?t=127) and inspect the [companion UI captures](DEMO.md#companion-and-capability-boundaries). Goals return local review drafts; Feed retains a checked quote/source; Library is owner-controlled. These captures include saved state.
- **Final 25 seconds:** inspect the rubric/source map below. [Testing](TESTING.md) separates static definitions, current execution and excluded private-frame cases. Optional: [02:24 onward](https://youtu.be/0wcoqi5CVuQ?t=144), live Ottawa demonstration; Amazon at **02:33.47**.

The opening is Muse's composite leap → “Muse had no body” → Meet Ode's redrawn session chat → ESP32 explanation timed to real HID logs. The [complete chapter list](DEMO.md#chapters-for-the-final-v6-cut) includes the 01:50 architecture beat.

## What Ode adds to its foundations

Henry Yuan's ESP32 iPhone Agent supplies the original device-control foundation. Ode builds on it with a Muse-backed landscape iPad companion, calibrated input/recovery, bounded source-checked Goals and Feed, owner-controlled memory UI, and durable no-replay receipts. Hermes supplies selected memory-store components. This is integration and execution work, with no new model-training or historical-priority claim.

**Credit the right layer:** Gemini Live describes the room and Psyduck; the filmed Camera/X path uses measured taps. Muse Spark's clearest filmed screen-to-action proof is rednote at **01:30.17**. The general visual path and Goal/writing routes are implemented in code; saved UI state alone does not demonstrate a new run. [Model routing](ARCHITECTURE.md#model-routing).

## Rubric → inspectable evidence

| Criterion | Evidence | Why it matters / boundary |
| --- | --- | --- |
| **Impact — 30%** | Film 00:29, 01:17 and README features; real [X output](https://x.com/hubeiqiao/status/2117654510482960846); source-bearing Feed screenshot | First intended use: a solo builder/creator moves from public-app research to checked findings and a local review draft, using existing hardware. [Vision/adoption hypothesis](VISION.md). This complete chain and time-saving still need measured acceptance; ~$5 is the board, with Mac/tablet/APIs additional. |
| **Use of Meta AI Tooling — 25%** | Film 01:30.17; `muse-spark-1.3` decision excerpt; [Meta client](../server/hidagent/meta_model.py), [hybrid actor](../server/hidagent/hybrid_agent.py), [Goal planner](../server/hidagent/app_mission.py), [draft writer](../server/hidagent/app_tools.py) | Spark makes device decisions and source-bound writing decisions. Jev is the primary judge; Gemini handles voice/camera. Optional Meta profiles are not extra demo models. |
| **Demo — 25%** | Film 00:29–01:17 causal sequence, existing result, 02:24 stage excerpt; [timestamp/edit disclosure](DEMO.md) | Real physical iPad footage and owner interaction. Hook is composited; time is compressed; rednote answer uses the search AI summary. Publication uncertainty was not retried. |
| **Technical Depth & Execution — 20%** | [Architecture](ARCHITECTURE.md), geometry/input, no-replay and source tests below, source + rebuilt UI, iPad firmware | Calibration, evidence checks and cancellation connect model proposals to physical input. Offline checks do not establish live app coverage. |

A shareable hook is already available: Muse entering a spare iPad, followed by the board visibly operating it. The existing X result provides a public result link; no views, likes, repost counts or viral bonus are claimed.

## Today, code and vision

| Capability | Evidence tier | Inspect |
| --- | --- | --- |
| Muse Spark target → physical input → screen change | **Filmed selected step** | [rednote 01:30](https://youtu.be/0wcoqi5CVuQ?t=90), [sanitized record](DEMO.md#sanitized-real-run-excerpt) |
| Camera conversation → photo → owner-confirmed send | **Filmed physical workflow + existing public result** | [00:29](https://youtu.be/0wcoqi5CVuQ?t=29); Gemini Live + calibrated Camera/X skill; [result](https://x.com/hubeiqiao/status/2117654510482960846) |
| Bounded Goals and source-bound review drafts | **Implemented code; saved-state UI in film** | [Goal planner](../server/hidagent/app_mission.py), [writer](../server/hidagent/app_tools.py); not a new full-chain run |
| Feed / editable memory | **Selected discovery and saved-state UI + code** | [Companion evidence and data boundaries](DEMO.md#companion-and-capability-boundaries) |
| 24 app skill cards | **Registered inventory** | [Card index](../app-skills/index.json), [acceptance loader](../server/hidagent/app_cards.py); not 24 full-app validations |
| General visual fallback | **Implemented general path; selected filmed visual targeting** | [Hybrid actor](../server/hidagent/hybrid_agent.py); the filmed call locates a target, not a complete unfamiliar-app task; broader reliability remains to be measured |
| Multi-device profiles, checked skill growth, glasses/Charm handoff | **Roadmap** | [Vision](VISION.md); no shipped external integration |

## Failure state is part of the product

| Situation | Implemented behavior / evidence | Operator interpretation |
| --- | --- | --- |
| Same request after restart | Durable request identity returns saved history without new model/HID execution | Inspect the saved receipt; previously in-flight requests become interrupted. A restart does not authorize replay |
| ACK lost after a possible write | Transport records ambiguous delivery instead of retrying payload | Inspect actual device; uncertainty is not failure permission to resend |
| Photo send begins | Pending draft is consumed before awaited send | One-use send; owner must still check exact account/text/photo and visible outcome |
| Stop / late model result | Revocable input ownership and cancellation checks; voice timing heuristics have limits | Inspect release receipt and actual input; [known limits](SAFETY.md) |

The [offline failure map](TESTING.md#failure-handling--inspectable-regressions) links concrete regressions. The [physical acceptance sheet](ACCEPTANCE.md) is blank: it specifies the next evidence rather than inventing a reliability result.

## Three engineering decisions to inspect

| Problem | Implementation | Regression examples |
| --- | --- | --- |
| Landscape geometry, accelerated or hidden pointer | [Screen negotiation](../server/hidagent/screen.py), [calibrated input](../server/hidagent/phone.py), [icon location](../server/hidagent/icon_locator.py) | [Native mirror](../server/tests/test_native_mirror.py), [Home icons](../server/tests/test_home_icons.py), [landscape frames](../server/tests/test_landscape_real_frames.py); private-image cases explicitly skip |
| Stop, late model completion, lost board ACK | [Revocable input leases](../server/hidagent/hid_lease.py), [durable receipts](../server/hidagent/receipt_journal.py), [one-use photo send](../server/hidagent/x_photo_flow.py) | [Goal stop](../server/tests/test_app_mission.py), [restart/no replay](../server/tests/test_receipt_journal.py), [lost ACK](../server/tests/test_device_handshake.py), [photo send](../server/tests/test_x_photo_landscape.py) |
| A proposed discovery is not an observed source | [Exploration](../server/hidagent/exploration_agent.py), [result reader](../server/hidagent/app_result_reader.py), [source-bound Goals](../server/hidagent/app_mission.py) | [Independent rejection](../server/tests/test_exploration_landscape.py), [completed-only persistence](../server/tests/test_companion.py), [Goal source boundaries](../server/tests/test_app_mission.py) |

## Evidence boundaries worth checking

- **Board ACK:** command acceptance, not proof of app success. **Closed composer:** not independent proof of publication. The film's result card and existing post are the publication evidence; the November 4 audit independently retrieved public X post metadata confirming `@hubeiqiao`, the caption and an attached photo.
- **Photo confirmation:** the guarded flow consumes a fresh draft authorization before its send. Preparation checks an attachment and nonempty caption; the operator still checks the actual account, exact caption and photo. Photo drafts skip the text-only readback-complete gate; spoken readback in this film is not a universal enforced check. [Flow](../server/hidagent/x_photo_flow.py) · [voice branch](../server/hidagent/ipad_demo_flows.py) · [limits](SAFETY.md).
- **Offline evidence:** synthetic regressions and protocol doubles exercise failure handling; excluded private images and macOS-only cases can skip. CI does not pair BLE, inspect apps or measure consecutive-run reliability. [Execution record and failure map](TESTING.md).

## Reproduce safely

Follow [Hardware](HARDWARE.md), [Setup](SETUP.md) and [Testing](TESTING.md). The public export omits personal device images, templates, runtime state and private history. Do not replay the recorded public send. Offline checks need no live models or device. A fresh physical acceptance run needs an attended, explicitly authorized setup.

Based on [ESP32 iPhone Agent](https://github.com/onehumanbeing/esp32-iphone-agent) by Henry Yuan (MIT). Memory store: Hermes Agent, Nous Research (MIT). Muse character: Meta · not affiliated with or endorsed by Meta.
