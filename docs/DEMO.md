# Demo: intent, physical action, visible result

[Watch the 2:58 v6 film]([YOUTUBE_URL]) · [Existing home-demo X post](https://x.com/hubeiqiao/status/2117654510482960846) · [Judge guide](JUDGES.md)

The home footage was recorded November 3, 2026. Stage footage is from Shopify Builder Sundays, Ottawa, November 1. The timestamps below follow the final v6 video metadata (5,342 frames at 30 fps, 2:58.07). They are film positions, not task latency measurements.

| Film time | Scene / evidence |
| --- | --- |
| 00:00 | Muse leaps into an idle iPad. This character/desk composite is the hook, not device execution. The real board taps Camera at 00:02.7. |
| 00:10.57 | Meet Ode: real self-introduction with enlarged Mac chat redrawn from the session's words in their original order. |
| 00:24.60 | ESP32 explanation; board-command animation is aligned to recorded HID commands. |
| 00:30.17 | Camera → scene conversation → photo → X compose → fresh “post it” confirmation → one send. The source interaction is one continuous take from self-introduction through the post; the delivered film compresses time. |
| 01:10.57 | Linked result card for the actual X post. The automated check returned uncertainty; no resend was made. |
| 01:12.27 | rednote search and reading. The ingredients reported aloud came from its search-result AI summary, not the opened post body. |
| 01:22.23 | Real Muse Spark search-entry decision. Bounding box/confidence and subsequent input are excerpted below. |
| 01:46.57 | Architecture: voice, Meta device reasoning, board input and mirrored-screen feedback. |
| 02:03.70 | 24 skill cards, bounded Goals, Feed and Library. These include companion captures of saved local state; a UI overview is not a fresh Goal execution. The Baobab Feed card is from the same November 3 run at 11:32. |
| 02:20.83 | Live stage excerpt; Amazon AirPods search appears at 02:30.37. Gemini Live supplies the room description. |
| 02:50.00 | End card and credits. |

## What is edited for time and readability

The film cuts waits, speeds footage up in places and marks speeds on screen. It combines the home camera/post run, a later rednote run, companion saved-state views and the November 1 stage footage. It is not a 2:58 end-to-end latency claim. The hook leap is composited; the enlarged Mac chat is a faithful redraw. Narration is AI voice, and overlays/architecture explain the recorded workflow. App screens and physical device footage after the hook are real. The camera/photo/X path is a measured-tap skill, not proof that the general visual fallback completed that entire sequence.

Muse character: Meta · not affiliated with or endorsed by Meta. Based on [ESP32 iPhone Agent](https://github.com/onehumanbeing/esp32-iphone-agent) by Henry Yuan (MIT). Memory store: [Hermes Agent](https://github.com/NousResearch/hermes-agent), Nous Research (MIT).

## Sanitized real run excerpt

The film production's source-aligned extraction of the November 3 input timeline and model-step records contains the following fields. Only this short whitelisted excerpt is distributed. Original traces, screen files, absolute paths, connection/device identities and conversations are excluded. `relative_s` is seconds within the rednote source clip, not the edited film. `write→ACK_s` measures command write to board acknowledgement, including motion completion; it is not total task time.

```text
muse-spark-1.3 · purpose: locate public search entry
model_seconds=4.287 · confidence=0.95
box={left:0.808, top:0.035, right:0.985, bottom:0.078}
routing: opt-in landscape locate directly to backup visual model

relative_s  command_id  command  write→ACK_s  accepted
44.765      362         home     2.058        true
46.824      363         move     0.301        true
47.126      364         move     1.651        true
49.380      365         move     0.215        true
49.744      366         move     0.219        true
50.976      367         click    0.132        true
```

A board ACK establishes acceptance by the board. The changed screen in the film establishes the visual effect. A model's confidence is its own score, not a calibrated correctness rate. This single step cannot establish average latency, arbitrary-app compatibility or system-wide reliability.

## How to evaluate without replaying publication

Inspect the existing video/result and read the implementation/tests in [JUDGES.md](JUDGES.md). No device actions were run to prepare this release. Reproduction requires the owner's board pairing, API access, calibration and local masked templates. Start with read-only navigation. Do not replay the historical X send or infer new authorization from the recorded confirmation.

The photo flow's terminal label can mean the composer closed. It alone does not independently verify server-side publication, actual account or exact caption equality. The film and supplied existing post are the result evidence. No engagement totals, measured productivity benefit or broad app benchmark is claimed. The release check could not independently fetch X through its web fetcher; the link is the owner's supplied published result.
