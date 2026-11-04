# Physical task acceptance sheet

[Judge guide](JUDGES.md) · [Setup](SETUP.md#first-run-acceptance-ladder) · [Offline testing](TESTING.md)

**Blank template, not a result.** Copy locally and fill only after an attended, explicitly authorized task. Keep raw screenshots, account identifiers, network details and private traces untracked. Publish only a reviewed, sanitized summary. Do not replay the film's publication.

## Task and setup

| Field | Actual observation |
| --- | --- |
| Date, source commit and operator | |
| Device / OS / app version, orientation | |
| Receiver, board/firmware version, geometry and calibration profile (sanitized) | |
| Models and routing; cold or warm start | |
| Exact read-only goal and allowed scope | |
| Start state, preconditions, selected skill or visual fallback | |
| Visible success condition and expected local artifact | |

## Consecutive runs

Record every attempted run, including failures. Do not populate a row from saved UI state or a model's completion claim.

| Run | Completed / failed / uncertain | Visible result / source / local draft | Total elapsed | Model wait | Input / ACK wait | Verification time | Human interventions / attention time | Failure and recovery |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| | | | | | | | | |

Use one monotonic task timeline when available; state missing clocks explicitly. A final click ACK is board acceptance, not app success. Model confidence is not a measured correctness rate. Separate first-run setup from task time. Report the full denominator and cold/warm differences; do not infer broad app acceptance from one workflow.

## Optional controlled checks

- On a public read-only screen, interrupt a task; record Stop, lease/release receipt and any later input. Do not infer physical release from a UI label.
- Compare skill and visual paths on the same allowed task and start state; retain both outcomes.
- Try a changed layout or harmless modal; document recovery or refusal, including manual help.
- Compare manual and Ode versions of the same task if measuring benefit; include corrections and time spent supervising.
- Ask a fresh builder to complete setup and one read-only task; capture actual installation/calibration friction without guessing a setup duration.

## Result review

Check actual screen effect, exact source, artifact usefulness and intervention count. Link the sanitized evidence only after review. State what this sample establishes and what remains untested. Broader devices, unattended tasks, payments and publication need their own implementation, authorization and acceptance.
