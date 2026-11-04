# Release snapshot and export scope

Source: `legacy` at `f3e3317c04a9dc0c4cc4e9beb04dd6ecc2e00cd2`. Base release commit: `9eed068c9c23ce371263aa86d0b57b5e7b680d03`. This update preserves the release repository's existing main history with normal commits; source history is not imported. Repository visibility is an owner decision and is not changed by this update.

The previous export's 544-file manifest was checked byte-for-byte against the base release commit. All 21 changed source paths since that export's source revision were reviewed; already exported authentication changes were retained, and the remaining source changes were merged with the existing sanitization. A newly introduced literal account default was changed to the same synthetic example account used elsewhere in the export. No product feature was added to repair an export.

## Included

- Server implementation and offline tests, with synthetic fixtures and explicit private-capture skip handling.
- Companion UI source, lockfile, rebuilt static bundle and third-party notices.
- iPad ESP32-C3 firmware source and an empty local-secrets template.
- 24 registered app skill cards, with private operation/evidence records excluded.
- Setup, hardware, safety, architecture, demo, judge and remote-access documentation.
- Explicitly selected cover and four public-safe companion captures; a short sanitized model/HID documentation excerpt.
- Required third-party licenses/provenance and isolated legacy compatibility source outside the iPad BLE submission path.

## Excluded

Keys/tokens and filled dotenv files; Wi-Fi credentials; actual private-network addresses/MACs/UDIDs; board inventories, calibrations and personal control templates; raw screenshots and original video; runtime data, traces, receipts, conversations, memory stores and evidence folders; dependency/build caches, firmware binaries, local upload logs and private source history. `.env.example` files contain variable names and comments only. Public author credit and the explicitly requested published-result link are retained.

Third-party legal contact addresses, loopback/bind/documentation IPs, and explicitly synthetic test identifiers remain as reviewed exceptions. They are not operator device/network details. The requested Git author identity is commit metadata, not runtime configuration.

## Reproduce verification

Follow [TESTING.md](TESTING.md) for the full offline suites and build. The Markdown relative paths are checked, all six README image references survive GitHub's GFM renderer, and no tracked file exceeds 10 MB. The selected cover is the largest file (1,005,101 bytes); no videos are bundled.

Before pushing, run Gitleaks on the final file tree and the new normal commits, and review regex/metadata checks on every manifest file and each new commit snapshot/diff:

```sh
gitleaks dir . --redact --no-banner
gitleaks git . --log-opts='9eed068..HEAD' --redact --no-banner
```

The separate privacy checks cover credential formats, owner/device identifiers, addresses, account syntax, forbidden runtime paths, dotenv values, file size and image metadata. Both automated scans and the reviewed exception inventory must be clear before push. Clean scans establish the defined checks, not every possible encoded disclosure.

## Before changing visibility

Replace `[YOUTUBE_URL]` in README, DEMO and JUDGES with the accepted upload. Review the rendered GitHub page, safe captures, character rights/credits and final demo description. Preserve Henry Yuan and Hermes attribution, including in demo/video/post descriptions. The owner changes visibility and submits the hackathon entry separately; pushing this snapshot performs neither action.
