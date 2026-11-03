# Ode — Give Muse a body: local setup

Start with the root README and [hardware guide](HARDWARE.md). This guide covers the pieces that depend on the operator's Mac, iPad and installed app layouts.

## AirPlay receiver

UxPlay is an external GPLv3 process, not redistributed with Ode. Follow its [official macOS build instructions](https://github.com/FDH2/UxPlay#building-uxplay-on-macos-intel-x86_64-and-apple-silicon-macs). The Mac needs command-line developer tools; no Xcode project, signing or runner is installed on the iPad.

One Homebrew-based source build is:

```sh
brew install cmake pkg-config libplist openssl@3 gstreamer
mkdir -p "$HOME/Developer"
git clone https://github.com/FDH2/UxPlay.git "$HOME/Developer/UxPlay"
cmake -S "$HOME/Developer/UxPlay" -B "$HOME/Developer/UxPlay/build"
cmake --build "$HOME/Developer/UxPlay/build" --parallel
export UXPLAY_BIN="$HOME/Developer/UxPlay/build/uxplay"
"$UXPLAY_BIN" -h
```

Ode's `scripts/uxplay-ipad.sh` invokes `UXPLAY_BIN` or `uxplay` on PATH with software decoding. Check that `gst-inspect-1.0 avdec_h264` succeeds. The wrapper preserves explicit caller options and accepts an optional private `UXPLAY_MAC`; no real receiver MAC ships in source. Do not run a second receiver alongside the server-owned receiver.

Set `UXPLAY_DISPLAY_SIZE` to your iPad's actual landscape pixel dimensions. With `MIRROR_FRAME_HEIGHT=1080`, a 2266×1488 display produces a processing frame around 1646×1080. Check the actual frame and calibration key in `/api/status`; never assume that another iPad has this geometry. Native display negotiation and lower-resolution processing are separate steps. Changing either requires recalibration.

For a separately managed receiver, `python -m hidagent.receiver` can serve the mirror and the main process can follow `MIRROR_STREAM`. This is optional; use the integrated receiver first. Keep receiver and board ports local to the trusted network.

## Local OCR

The Swift worker uses Apple's Vision framework and can be compiled with the Mac's command-line developer tools. It does not install an iPad app:

```sh
mkdir -p work/native-probe
xcrun swiftc tools/native-probe/vision_ocr.swift -o work/native-probe/vision-ocr
export LOCAL_OCR_BIN="$PWD/work/native-probe/vision-ocr"
```

The configured worker is launched with `--serve`. Keep its absolute executable path in the local environment; the public repository has no dependency on another private checkout. Native OCR tests can skip when the compiler/platform or private captures are absent.

## Camera and photo skills

The general visual agent is available independently of the private fast-path templates. The calibrated Camera/photo workflow additionally needs your own masked UI-control templates. Enabling a flag alone does not create them.

- `CAMERA_TEMPLATE_DIR`: Camera control-strip crops matching `TEMPLATE_FILES` and geometry in `server/hidagent/camera_conversation.py`. Never include preview, thumbnail, face or personal content pixels.
- `X_PHOTO_TEMPLATE_DIR`: control-only crops named by `PATCHES` in `server/hidagent/x_photo_local.py`. Missing crops return no match; they are not synthetic runtime evidence.
- `APP_CONTROLS_DIR`: local app control registry. Its public default points to ignored `runtime/ipad/app-controls`.
- `APP_ICON_LAYOUT`: optional local home-icon layout, with correct reference geometry and a home guard. No personal home-screen image is distributed.

Capture only the necessary app chrome locally, mask unrelated regions, and verify each detector positively and negatively before use. Keep all source captures and crops ignored. Tests generate synthetic shapes to test match/reject logic; do not use synthetic test images as runtime app templates.

After actual screen/calibration/full-screen checks, use `IPAD_VOICE_DEMOS=1` for bounded demo tools and `CAMERA_CONVERSATION=1` for camera conversation. Set `LOCAL_OCR_BIN` for native text/label checking. Do not set `IPAD_FULL_SCREEN_VERIFIED` as a substitute for inspecting the device. Begin with opening Camera and reporting the preview; perform the publish rehearsal only with a separately authorized test task.

## Configuration map

| Variable | Purpose |
| --- | --- |
| `AGENT_ENGINE=meta_hybrid`, `META_MODEL=muse-spark-1.3` | Submission model route |
| `META_API_KEY`, `TYPESAFE_API_KEY`, `GEMINI_LIVE_API_KEY` | Private provider credentials, Mac only |
| `DEVICE_MODE=ipad`, `AIRPLAY_NAME`, `AI_NAME` | Device profile, receiver name, conversational name |
| `HOST=127.0.0.1`, `PORT=8890`, `DEVICE_PORT=7300` | Web binding and separate board listener; firmware must match |
| `HIDAGENT_DATA` | Dedicated ignored runtime directory; separate for every device |
| `UXPLAY`, `UXPLAY_BIN`, `UXPLAY_DISPLAY_SIZE`, `MIRROR_FRAME_HEIGHT` | Receiver wrapper, executable and geometry |
| `IDLE_NUDGE=0` | Disable idle pointer nudging in the documented attended setup |
| `HIDAGENT_DEBUG`, `HIDAGENT_TRACE` | Optional sensitive traces; off unless explicitly enabled |
| `ODE_PUBLIC_ORIGIN`, `ODE_ACCESS_ISSUER`, `ODE_ACCESS_AUD`, `ODE_OWNER_TOKEN` | Optional remote origin authorization; see tunnel guide |

`server/.env.example` intentionally has no values. Fill only private credentials or explicitly selected overrides. `scripts/run-ode.sh` loads the operator-owned file and runs the already installed server. Do not download or source an untrusted environment file.
