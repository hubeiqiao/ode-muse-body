# README demo media

[Full 2:58 film](https://youtu.be/0wcoqi5CVuQ) · [Chapters and complete editing disclosure](../../DEMO.md) · [Asset credits](../README.md)

## Selected excerpts

All moving images come from the approved v6 film, except the explicitly selected public-safe Library insert. These are edited highlights, not new device runs or task-latency measurements. Animated WebP preserves full color with smaller files than the palette GIF trials; every frame was decoded and start/middle/end samples inspected. Each animation loops silently at 12 fps, 16:9. Encoding uses Lanczos scaling and libwebp quality 82 / method 5. Repeated identical frames may be coalesced while retaining their duration.

| File | Size (decimal MB) | Display / duration | Source in main film / additional speed |
| --- | --- | --- | --- |
| [ode-hero.webp](ode-hero.webp) | 2.06 | 960×540 · 9.42 s | 00:00–00:02.70 + 00:25.20–00:28.70 + 01:14.20–01:17.40; 1× film speed, hard-cut montage |
| [camera-to-x.webp](camera-to-x.webp) | 2.86 | 720×405 · 12.00 s | 00:29.30–01:17.40; further accelerated 4× |
| [muse-spark-hid.webp](muse-spark-hid.webp) | 1.77 | 720×405 · 11.00 s | 01:29–01:40; 1× film speed |
| [goals-feed-library.webp](goals-feed-library.webp) | 0.51 | 720×405 · 8.42 s | 02:09.13–02:22 at 2×, followed by a 2 s public-safe Library still |
| [live-on-stage.webp](live-on-stage.webp) | 2.05 | 720×405 · 8.00 s | 02:25.30–02:41.30; further accelerated 2× |

**Library source:** only [library-safe.png](../companion-mobile/library-safe.png), a selected saved-state companion capture. The original film’s later Library screen is excluded from this animation. Goals and Feed are also saved-state views, not fresh task executions. The README feature captions identify additional acceleration; the original film’s speed labels still apply.

The hero’s Muse leap is composited; the ESP32 animation follows logged commands. The Camera/X sequence uses a measured-tap skill and Gemini Live conversation, with fresh owner confirmation before sending. Muse Spark’s filmed screen-decision evidence is the separate rednote locate step. In rednote, spoken ingredients come from the search-result AI summary, not the opened post body. The stage film is from Shopify Builder Sundays, Ottawa, November 1, 2026. [Full disclosure](../../DEMO.md).

## Native GitHub video attachment

Export: **`ode-github-embed.mp4`**, kept outside Git at the production-relative path `social/ode-github-embed.mp4`. No owner-specific filesystem path is required to inspect the public attachment.

- **53.67 s**, **1280×720**, 30 fps, **8,761,903 bytes (8.76 MB)**.
- H.264 video (two-pass, 1,200 kbit/s target), AAC audio (96 kbit/s); `moov` precedes `mdat` for faststart.
- Continuous excerpt of the approved film, **00:23.733–01:17.400**: ESP32 hands → Camera → photo → X draft → fresh confirmation → recorded send/result. Original mixed audio retained. The film’s existing time compression remains; no extra speed change is applied to the MP4.
- Full source: `ode-give-muse-a-body-3min-v6.mp4`, 178.633333 s, 1920×1080, 30 fps; SHA-256 `ecaeef2f1267d84e7ca51e18af541fd4552cb56995b91443d1eaf78ee4551317`.

### Published attachment and verification

The README embeds the uploaded clip using GitHub's native video player. The original attachment reference is `https://github.com/user-attachments/assets/883dbbaa-a3f9-4b56-9db3-3a5ea700166f`; GitHub's rendered page resolves it to a temporary signed video URL. Directly requesting the original reference can return 404 even while the rendered player works. Do not replace the README reference with an expiring signed URL.

On November 4 the public page's resolved video downloaded successfully as `video/mp4`: **8,761,903 bytes**, **53.666667 s**, SHA-256 `2d8e5427c93eb707be56d8d182c9c302b16f6652ec92a93a542fe9a07e3e9737`, identical to the local attachment export. The former `GITHUB_VIDEO_EMBED` insertion marker is no longer needed.

The public repository HTML advertises a custom social-preview image. Inspect its actual crop before sharing; do not infer it from the README's cover dimensions alone.

## Credits and reuse

Footage and Ode project presentation: Joe Hu. Based on [ESP32 iPhone Agent](https://github.com/onehumanbeing/esp32-iphone-agent) by Henry Yuan (MIT). Selected memory store: [Hermes Agent](https://github.com/NousResearch/hermes-agent), Nous Research (MIT).

**Muse character: Meta · not affiliated with or endorsed by Meta.** App icons/content remain with their owners. The orange Ode mascot is separate project artwork. The MP4 retains “Happy Home” by Michael Ramir C. (Mixkit Stock Music Free License) and Mixkit sound effects (Sound Effects Free License), as credited in the original film. Source attribution is not additional media-rights clearance; the repository’s code MIT license does not grant rights to Meta character imagery, third-party app content or music. Public demos, videos and posts of derived work should credit Henry’s project too.
