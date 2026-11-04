# Presentation benchmark and release notes

Reviewed November 4, 2026, before the presentation rewrite. These are layout references, not capability comparisons. No star counts, performance claims or third-party branding are borrowed.

## Patterns adopted

| Reference | Observed presentation pattern | Applied to Ode |
| --- | --- | --- |
| [Ollama](https://github.com/ollama/ollama) | Recognizable logo, one clear promise, direct installation path | Orange Ode avatar, one-line tagline, numbered quick start |
| [Browser Use](https://github.com/browser-use/browser-use) | Centered identity, visible navigation and product demonstrations | Centered hero, section links and moving evidence near the top |
| [Open Interpreter](https://github.com/openinterpreter/openinterpreter) | Compact identity/badges, large product image, concise installation | Restrained badges, linked cover and collapsed setup steps |
| [OmniParser](https://github.com/microsoft/OmniParser) | Screen-grounding explanation linked to inspectable technical material | Architecture graphic and a real model decision → HID → screen-result example |
| [LeRobot](https://github.com/huggingface/lerobot) | Animated capability sections beside concrete APIs and hardware guides | Animated feature grid, explicit hardware requirements and deeper source links |
| [Reachy Mini](https://github.com/pollen-robotics/reachy_mini) | Expressive hero GIF and clear routes into hardware/SDK setup | Short hero loop and separate offline-review / physical-setup routes |
| [ESP32 iPhone Agent](https://github.com/onehumanbeing/esp32-iphone-agent) | Logo, CI/license/platform badges, real-device pictures, architecture and visible attribution | Real operation footage, truthful cost table, existing architecture and prominent upstream credit |

The Open Interpreter reference URL now redirects from `OpenInterpreter/open-interpreter` to `openinterpreter/openinterpreter`; this review uses the live destination. Upstream ESP32 hardware has evolved: Ode documents its own Mac + ESP32-C3 + iPad setup rather than copying current upstream requirements.

## Before / after

**Before:** a 14 KB README repeated demo chapters, routing explanations, evidence boundaries and feature descriptions. The cover and static captures were the only visual entry points; community intake and citation files were missing.

**After:** one visual entry point leads from the avatar and demo to a four-feature grid, architecture, a short proof excerpt and expandable setup. Detailed editing and evaluation disclosures remain in [Demo](DEMO.md) and [Judges](JUDGES.md). A [documentation index](README.md), issue/PR templates, security policy, conduct policy and citation record provide clear next steps for visitors and contributors. Product behavior is unchanged.

## Media and owner handoff

See [media recipes, sizes and upload instructions](assets/media/README.md). The README keeps a `GITHUB_VIDEO_EMBED` marker directly below the hero so the owner can insert the MP4 through GitHub’s attachment UI. The full film remains on [YouTube](https://youtu.be/0wcoqi5CVuQ).

The existing 2:1 cover can be uploaded in **Settings → General → Social preview**. GitHub generates its own card crop; inspect the preview before saving. This repository update does not change repository visibility, submit the hackathon entry or publish a social post.

## Verification of this refresh

Against clean base `7ff96d7`, Python **3.12.8** on macOS executed **3,620 successful cases, 352 skips, 0 failures** in 146.50 s (two existing warnings). Node **22.23.4** executed **83 successful cases, 0 failures or skips**. The companion source build completed; every generated file byte-matched the shipped bundle. Existing dependency/chunk-size warnings remain. No product source, firmware, generated bundle or test file changed.

GitHub’s Markdown API rendered the README with all 15 images and four setup details retained. All 174 relative references/anchors resolve; the 16 external README URL requests returned HTTP 200 (HTTP reachability is separate from independently validating X post content). Citation schema, issue-template YAML, media size/decode and MP4 faststart were checked. No tracked file exceeds 10 MB. Gitleaks checks cover the final tree and new commit range. GitHub’s push-triggered workflow supplies the separate remote CI result.

The interactive browser host was unavailable. Returned GFM HTML and local media samples were inspected; no live desktop/mobile GitHub viewport acceptance is claimed. Offline verification does not establish current real-device reliability. No device/model task, board write or social post was performed.
