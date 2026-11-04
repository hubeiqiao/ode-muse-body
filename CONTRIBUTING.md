# Contributing to Ode

Help give Muse a reliable body. Useful contributions include reproducible bug reports, clearer setup instructions, privacy-safe fixtures and focused improvements to calibration, recovery or source checks.

## Before you start

Read [AGENTS.md](AGENTS.md), [Architecture](docs/ARCHITECTURE.md), [Safety](docs/SAFETY.md) and the [Code of conduct](CODE_OF_CONDUCT.md). Open a feature issue before a substantial change so scope and device acceptance can be agreed. Small documentation corrections can go straight to a pull request.

## Local development

Follow [Setup](docs/SETUP.md) and [Testing](docs/TESTING.md) for dependency versions and exact commands. Run the Python and Node suites offline. Changes to companion source also require rebuilding its distributable and retaining third-party notices. Use synthetic fixtures; keep live device/model calls out of automated checks.

## A useful pull request

- Describe the concrete problem, resulting behavior and scope.
- Include reproducible verification, environment and explicit skip reasons.
- Report physical acceptance separately from offline checks; state when it was not performed.
- Update relevant documentation and preserve original MIT attribution and dependency notices.

Keep changes focused. Do not commit credentials, filled environment files, private account details, device identities, calibration, personal captures, task receipts, memory or conversation history. Screenshots for public documentation must be explicitly selected and checked for private content.

## Working with hardware

Select the board serial port explicitly. Back up and verify a new board before writing it. Keep iPad/iPhone identities, ports, runtime and calibration separate. Device access and outgoing actions require the operator’s task authorization; contributing a patch grants neither. Stop on uncertain delivery rather than repeating input.

## Reporting problems

Use the [bug](https://github.com/hubeiqiao/ode-muse-body/issues/new?template=bug_report.yml) or [feature](https://github.com/hubeiqiao/ode-muse-body/issues/new?template=feature_request.yml) template. Include sanitized text and synthetic reproducers. Report vulnerabilities privately through [SECURITY.md](SECURITY.md).

Based on [ESP32 iPhone Agent](https://github.com/onehumanbeing/esp32-iphone-agent) by Henry Yuan. Keep the [LICENSE](LICENSE) and [NOTICE](NOTICE); public demos, videos and posts of derived work should credit the upstream project too.
