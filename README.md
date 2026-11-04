<div align="center">
  <img src="docs/assets/ode-avatar.png" width="112" height="112" alt="Ode, the orange companion mascot">
  <h1>Ode</h1>
  <p><strong>Personal superintelligence needs a body. Ode gives Meta's AI one, on the devices you already own.</strong></p>
  <p>
    <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-6366f1" alt="License: MIT"></a>
    <a href="https://github.com/hubeiqiao/ode-muse-body/actions/workflows/ci.yml"><img src="https://github.com/hubeiqiao/ode-muse-body/actions/workflows/ci.yml/badge.svg" alt="Offline verification CI"></a>
    <a href="#meta-ai-at-the-core"><img src="https://img.shields.io/badge/Meta-Muse_Spark-0866ff" alt="Meta Muse Spark"></a>
    <a href="docs/JUDGES.md"><img src="https://img.shields.io/badge/Meta_AI-Hackathon-ff824d" alt="Meta Global AI Developer Hackathon entry"></a>
    <a href="docs/SETUP.md"><img src="https://img.shields.io/badge/Python-3.12-3776ab" alt="Python 3.12"></a>
    <a href="docs/HARDWARE.md"><img src="https://img.shields.io/badge/Platform-iPadOS_%2B_macOS-444444" alt="Platform: iPadOS and macOS"></a>
    <a href="https://youtu.be/0wcoqi5CVuQ"><img src="https://img.shields.io/badge/YouTube-Watch_demo-ff0033?logo=youtube&amp;logoColor=white" alt="Watch the 2:58 demo on YouTube"></a>
    <a href="https://x.com/hubeiqiao"><img src="https://img.shields.io/badge/Follow-%40hubeiqiao-000000?logo=x&amp;logoColor=white" alt="Follow Joe Hu on X"></a>
  </p>
  <p>
    <a href="#the-vision">Vision</a> · <a href="#demo">Demo</a> · <a href="#how-it-works">How it works</a> · <a href="#quick-start">Quick start</a> · <a href="docs/JUDGES.md">Judges</a> · <a href="#safety-and-limits">Safety</a> · <a href="#credits-and-license">Credits</a>
  </p>
</div>

> **I gave Meta's AI a body.** It lives in my spare iPad mini now. It sees, talks, and taps real apps through a $5 ESP32.
> **No jailbreak. No developer mode. No Xcode. No app APIs.**

Built by **Joe Hu** ([@hubeiqiao](https://x.com/hubeiqiao)) for the **Meta Global AI Developer Hackathon** · Indie Developer Track · Push the Frontier.

## Demo

<p align="center">
  <a href="https://youtu.be/0wcoqi5CVuQ"><img src="docs/assets/ode-cover-readme-2x1.png" width="960" alt="MY iPAD IS ALIVE? — watch Ode's full demo"></a>
</p>

https://github.com/user-attachments/assets/883dbbaa-a3f9-4b56-9db3-3a5ea700166f

<p align="center"><a href="https://youtu.be/0wcoqi5CVuQ"><strong>Watch the full 2:58 film ↗</strong></a> · <a href="docs/JUDGES.md">Two-minute judge guide</a> · <a href="docs/DEMO.md">Chapters</a></p>

## The vision

Meta's mission is **personal superintelligence for everyone**. Today that intelligence lives in a chat box. It can answer, but it can't *do*: most of the apps our lives run on will never ship an API, and an AI that can only talk can't use them.

At the same time, **billions of phones and tablets sit idle in drawers.** Each one already has a screen, a camera, a microphone, a battery and every app you use.

**Ode puts the two together. Any spare device becomes a body for your AI.**

- **It sees.** The screen through mirroring, and you through the device's camera.
- **It talks.** A live voice conversation while it works.
- **It acts.** A $5 ESP32 is its hands: a Bluetooth keyboard and mouse, the same input a person uses. If you can tap it, Muse can tap it.
- **It remembers and pursues goals.** A memory you control, goals it works toward, and discoveries it checks against sources.

No jailbreak, no developer mode, no Xcode, no app APIs, nothing installed on the device. One general approach that works through the same interface humans use, on hardware people already own.

<p align="center"><img src="docs/assets/media/ode-hero.webp" width="860" alt="Muse leaps into the iPad, the ESP32 opens Camera, and the post goes out"></p>

## What Muse can do with a body

<table>
  <tr>
    <td width="50%" align="center">
      <a href="https://youtu.be/0wcoqi5CVuQ?t=29"><img src="docs/assets/media/camera-to-x.webp" width="460" alt="Muse sees Joe through the iPad camera, takes a photo and posts it on X"></a><br>
      <strong>See you, then act</strong><br>Describes my room through the camera, names the figure I hold up, takes the photo and posts it on X after I say "post it".
    </td>
    <td width="50%" align="center">
      <a href="https://youtu.be/0wcoqi5CVuQ?t=89"><img src="docs/assets/media/muse-spark-hid.webp" width="460" alt="Muse Spark locates rednote search and the ESP32 taps it"></a><br>
      <strong>Use apps with no API</strong><br>In rednote, Muse Spark finds the search box and the ESP32 taps it. A model decision becomes a physical tap.
    </td>
  </tr>
  <tr>
    <td align="center">
      <a href="https://youtu.be/0wcoqi5CVuQ?t=127"><img src="docs/assets/media/goals-feed-library.webp" width="460" alt="Skills, Goals, Feed and Library"></a><br>
      <strong>Pursue goals and remember</strong><br>24 app skills learned from real runs, a general visual agent for everything else, goals, a source-checked discovery feed and a memory you control.
    </td>
    <td align="center">
      <a href="https://youtu.be/0wcoqi5CVuQ?t=144"><img src="docs/assets/media/live-on-stage.webp" width="460" alt="Ode demonstrated on stage at Shopify Builder Sundays in Ottawa"></a><br>
      <strong>Live, in front of a room</strong><br>On stage at Shopify Builder Sundays in Ottawa: it counted the room and searched Amazon for AirPods.
    </td>
  </tr>
</table>

## How it works

![Architecture: AirPlay screen to Mac, Muse Spark reasoning, ESP32 Bluetooth input, iPad screen feedback](docs/assets/architecture.png)

1. **See.** The iPad mirrors its screen to a Mac over AirPlay; Gemini Live carries the voice and camera conversation.
2. **Think.** **Muse Spark** (`muse-spark-1.3`, Meta Model API) reads the screen and plans the next step. Known apps take fast skill paths.
3. **Act.** The Mac sends the step over Wi-Fi to the ESP32, which taps, types and swipes as a Bluetooth keyboard and mouse.
4. **Check.** Muse reads the changed screen, keeps a receipt, and never replays an action it isn't sure landed.

**Proof from a real run** (01:30 in the film): Muse Spark finds rednote's search box, the board moves and clicks.

```text
muse-spark-1.3 → search-entry box [0.808, 0.035, 0.985, 0.078]   conf 0.95 · 4.3 s
ESP32 #362 home → #363–366 move → #367 click                       ACK 0.13 s ✓
```

The post Ode published in the demo is real: [x.com/hubeiqiao/status/2117654510482960846](https://x.com/hubeiqiao/status/2117654510482960846). [Architecture](docs/ARCHITECTURE.md) · [Judge guide](docs/JUDGES.md) · [Testing](docs/TESTING.md)

## Meta AI at the core

| Role | What Muse Spark does |
| --- | --- |
| **Eyes + brain** | Reads screenshots of real apps and decides the next action |
| **Planner** | Breaks a goal into app skills, checks each result, writes the draft |
| **Researcher** | Reads sources and saves only checked discoveries |
| **Judge** | Backs up the visual check before risky input |

The product is built around Meta's Muse: the whole story is giving Muse a body. Gemini Live handles real-time voice and camera, local OCR and calibrated skills handle speed, and Muse Spark makes the decisions. [Model routing](docs/ARCHITECTURE.md#model-routing).

## Where this goes

- **Every device you own.** iPad today. The same Bluetooth-HID approach already drives iPhone in the upstream project; Android tablets, old laptops and any screen that accepts a keyboard and mouse are next.
- **Always-on companion.** A device on your desk that watches what you point it at, works through apps while you talk to it, and keeps going toward your goals.
- **Learns every app it touches.** Each successful run becomes a skill, so it gets faster in the apps you use most.
- **Safe by design.** It asks before anything public, stops on uncertainty, and keeps memory under your control. [Safety](docs/SAFETY.md)

## Quick start

**Two routes:** inspect [offline verification](docs/TESTING.md) with no device or API credentials, or follow the attended hardware setup below. Python **3.12** and Node **22+** are the verification targets. No jailbreak, developer mode or Xcode IDE on the iPad; the Mac needs its command-line tools for the receiver and OCR.

<details>
<summary><strong>1. Install the coordinator</strong></summary>

```sh
git clone https://github.com/hubeiqiao/ode-muse-body.git
cd ode-muse-body
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -e './server[dev,mcp]'
npm ci --prefix tools/twitter-text-validator
cp server/.env.example server/.env
```

Fill `META_API_KEY`, `TYPESAFE_API_KEY` and `GEMINI_LIVE_API_KEY` locally. Keep the filled file untracked. The rebuilt companion is included. [Full setup](docs/SETUP.md).

</details>

<details>
<summary><strong>2. Prepare the board and AirPlay</strong></summary>

Follow [Hardware](docs/HARDWARE.md): identify the ESP32-C3, explicitly select its serial port, back up and verify the backup before flashing. Pair **Ode iPad HID** with the iPad and [install UxPlay separately](docs/SETUP.md#airplay-receiver). USB powers/flashes the board; the iPad receives input over Bluetooth. No GPIO wiring or iPad app installation is needed.

</details>

<details>
<summary><strong>3. Start Ode on the Mac</strong></summary>

The geometry below is an iPad mini example; choose your own display size.

```sh
export AGENT_ENGINE=meta_hybrid META_MODEL=muse-spark-1.3
export DEVICE_MODE=ipad AI_NAME=Ode AIRPLAY_NAME='Ode iPad'
export HOST=127.0.0.1 PORT=8890 DEVICE_PORT=7300 IDLE_NUDGE=0
export HIDAGENT_DATA="$PWD/runtime/ipad"
export UXPLAY="$PWD/scripts/uxplay-ipad.sh"
export UXPLAY_DISPLAY_SIZE=2266x1488 MIRROR_FRAME_HEIGHT=1080
./scripts/run-ode.sh
```

</details>

<details>
<summary><strong>4. Mirror, calibrate, try “Open Settings”</strong></summary>

Mirror to **Ode iPad** in landscape full-screen mode. Open the [companion](http://127.0.0.1:8890/companion) and [calibration UI](http://127.0.0.1:8890/). Confirm live frames, stable board connection and calibration before an attended “Open Settings.” Follow the [acceptance ladder](docs/SETUP.md#first-run-acceptance-ladder).

Goals need `APP_MISSIONS=1`. Set `META_MISSION_WRITER=1` and `META_DRAFT_WRITER=1` to select dedicated hosted Meta role clients; otherwise Goals inherit the configured actor (Meta in this `meta_hybrid` setup). Exploration needs `EXPLORATION_PUBLIC_BROWSER=1`. Enable attended sessions only after setup. Camera/photo fast paths need your own masked templates and local OCR; personal captures and calibration are excluded from this checkout. Saved-state companion views start empty on a fresh clone.

</details>

### Hardware and costs

| Item | Requirement / cost |
| --- | --- |
| ESP32-C3 development board | **Approximately $5, board only**; supplier-dependent |
| Mac | Required coordinator, AirPlay receiver and local OCR; additional cost |
| iPad | Owner-controlled device with AirPlay and Bluetooth HID; additional cost |
| USB data cable + trusted network | Board power/flashing and local connectivity; additional cost |
| Cloud APIs | Meta Muse Spark, Jev and Gemini Live access; usage costs vary |

[Board preparation, pairing & calibration](docs/HARDWARE.md)

## Safety and limits

Ode asks before anything public, stops instead of repeating an action it isn't sure landed, keeps memory local and owner-controlled, and keeps purchases, passwords and sensitive settings with the human. It needs a Mac, a one-time calibration and cloud APIs; the $5 is the board. The film is edited for time with speeds marked. [Safety & data flow](docs/SAFETY.md) · [Editing disclosure](docs/DEMO.md) · [Security](SECURITY.md)

## Credits and license

Based on [ESP32 iPhone Agent](https://github.com/onehumanbeing/esp32-iphone-agent) by **Henry Yuan**. Original **Copyright (c) 2026 Henry Yuan**, [MIT LICENSE](LICENSE) and [NOTICE](NOTICE) are retained alongside Ode contributors’ copyright. Public demos, videos and posts of derived work should credit Henry’s project too.

**Memory store:** selected [Hermes Agent](https://github.com/NousResearch/hermes-agent) components by **Nous Research**, MIT; [pinned provenance](server/hidagent/hermes_memory/UPSTREAM.md). The whole Hermes agent is not bundled.

**Muse character: Meta · not affiliated with or endorsed by Meta.** The orange Ode mascot is separate project artwork. The cover uses a generated background, Meta character cutout and Joe’s real face. Code licensing does not grant character/media reuse rights. [Asset credits](docs/assets/README.md).

UxPlay is an external GPLv3 receiver, installed separately. [Dependency notices](docs/DEPENDENCIES.md) · [Contributing](CONTRIBUTING.md) · [Code of conduct](CODE_OF_CONDUCT.md) · [Documentation](docs/README.md) · [Presentation notes](docs/PRESENTATION.md).

<p align="center"><strong>Built by Joe Hu (<a href="https://x.com/hubeiqiao">@hubeiqiao</a>) for the Meta Global AI Developer Hackathon.</strong></p>
