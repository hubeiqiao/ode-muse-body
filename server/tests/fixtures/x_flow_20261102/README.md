Owner-supplied real iPad frames, copied byte-for-byte from the 2026-11-02
`x-flow-20261102` evidence directory. `manifest.json` records SHA-256 and geometry.

| Frame | Independently inspected state |
| --- | --- |
| spotlight-x-tophit-row.jpg | Query X; Top Hit icons X, XChat, Quantumult X; a separate Suggestions X |
| x1.jpg | Empty standalone X composer |
| x2.jpg | Photos picker; newest/top-left photo already selected; blue 1, enabled check, 1 Photo count |
| x3.jpg | Standalone composer with one attached image, no caption |
| x4.jpg | Same image with caption: Selfie from my AI on an old iPad. Orange hoodie, warm light. |

Run `python -m pytest -q -s tests/test_x_flow_real_frames.py` from `server`.
On macOS the tests compile the checked-in native Vision OCR worker, or use
`LOCAL_OCR_BIN` when explicitly configured. The verified deployed fast-only
worker also passes. Apple Vision needs normal macOS image-buffer access, which
may require execution outside a restrictive sandbox. Other platforms skip these
native tests. The caption comparison unit tests run on every platform.

No detector or OCR mocks, model credentials, network calls or device input are
needed. Positive checks call the production DemoVision / PhotoPostVision paths;
the Spotlight test also calls DemoFlows.require('top_hit'). Negative tests alter
pixels and rerun actual OCR/template detectors. Phone input alone is stubbed in
the require() integration test and is asserted unused.

Both native OCR and Tesseract read the caption's AI glyphs as Al. The measured
local composer readback permits only positional I-to-l OCR ambiguity inside
expected uppercase initialisms. Other case, words, punctuation, missing/extra
text and arbitrary substitutions still refuse; the remote observer's exact
comparison is unchanged.
