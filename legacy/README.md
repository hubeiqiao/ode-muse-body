# Archived compatibility code

Ode's submission uses **iPad + AirPlay + ESP32 BLE HID**, with Muse Spark reasoning. Start at the root README.

This directory isolates upstream USB/iPhone firmware and optional native-runner/WDA experiments retained for provenance and offline regression tests. They are not required, started, or validated by the Ode quick start. Do not use their historical setup notes as submission instructions. There are no signing credentials, device inventories, board backups or runtime captures here.

Reusable Python compatibility modules remain in `server/hidagent` to preserve imports and tests. They are selected only by explicit driver configuration; the default driver is ESP32. No native phone runner is used in the submission.
