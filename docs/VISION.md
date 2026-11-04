# One intelligence, many possible bodies

[Project](../README.md) · [Current evidence](JUDGES.md#today-code-and-vision) · [Acceptance sheet](ACCEPTANCE.md)

**Personal superintelligence needs a way to act where people already live.** Our ambition is to let the phones and tablets we already own become useful bodies for personal AI, starting with a spare iPad on a desk. We want that idea to reach billions of people. This is a vision, not a claim about current users, market size or idle-device inventory.

## The first person we are building for

A solo builder or creator already has a Mac and an unused iPad. Useful public information is inside native apps; turning it into work still means switching screens, reading, copying and checking. Our first workflow to validate is **public-app research → source-linked findings → a local draft for review**, while the owner can keep talking to the companion.

The film shows ingredients of this workflow: a real Meta screen decision and physical input, rednote research, a source-bearing discovery and the companion. It does not show the entire research-to-draft chain in one new run or establish saved time. The next proof should be one complete attended task with a useful final artifact, followed by repeated runs. [Blank record](ACCEPTANCE.md).

**Example task to validate:** “Find one public speaking-practice idea in rednote, keep a checked source excerpt, and prepare three lines for me to review.” The intended artifact is a local note with the app, source title/URL when available, checked excerpt and draft. This is a proposed acceptance task, not a recorded success.

## Why this fits Meta's direction

Meta already presents Muse as an agent that can plan and do work, and has announced access through AI glasses and Muse Charm. These official directions motivate our idea; they do not announce an Ode partnership:

- [Muse announcement](https://about.fb.com/news/2026/09/introducing-muse-personal-ai-agent/): personal agents that act toward goals.
- [Connect 2026](https://about.fb.com/news/2026/09/the-biggest-news-from-connect-2026/): Muse across AI glasses and the pocket-sized Charm concept.
- [Meta's Muse safety architecture](https://research.meta.ai/blog/security-and-safety-for-ai-agents-our-approach-with-muse/): explicit boundaries between agent proposals and permitted actions. This informs our direction; Ode does not implement Muse Secure VM or Sentinel.

**Our proposed complement is an action endpoint for existing devices.** A future agent could receive intent through glasses or a pocket companion, carry it to a tablet's native apps, and return evidence for review. Today Ode is independent software calling the Muse Spark API, with no connection to the hosted Muse app's identity, accounts, memory, tasks, glasses or Charm.

## A route from prototype to platform

| Stage | Product hypothesis | Gate before making a stronger claim |
| --- | --- | --- |
| **Current prototype** | Pixels, Muse Spark and ordinary Bluetooth input can close a useful physical loop | Existing selected iPad footage, model/HID excerpt and source code; [evidence tiers](JUDGES.md#today-code-and-vision) |
| **Daily usefulness** | An attended companion can reduce repetitive app work and attention switching | Complete source-to-draft runs; honest elapsed time, interventions and owner feedback |
| **Reusable competence** | Checked workflows can become skills and help later builders | Capture trajectory → candidate with preconditions → fresh-state acceptance → owner review → versioned reuse; not automatic learning today |
| **Easier adoption** | Builders can reproduce it with hardware they own | Fresh setup by another builder; document dependency, pairing and calibration friction before promising setup time |
| **More bodies** | The same interface principle can extend beyond one iPad | Separate geometry, calibration, input capabilities and acceptance per device; evaluate iPhone and Android; no compatibility promise |
| **Ambient agency** | One personal AI could coordinate approved work across devices and surfaces | Stronger action authorization, privacy boundaries, cancellation and long-task reliability; external integrations separately implemented and evaluated |

### The proposed builder loop

**Reproduce one read-only task → report a visible result or failure → contribute a reviewed skill → test it in a fresh state → make the next setup easier.** This is an adoption hypothesis, not existing contributor traction or a measured network effect. Begin at [Setup's acceptance ladder](SETUP.md#first-run-acceptance-ladder) and [Contributing](../CONTRIBUTING.md).

## Where the interface matters

| Approach | Best fit | Ode's design choice |
| --- | --- | --- |
| An authorized app API or connector | Structured supported operations and data | Prefer supported integrations when available; Ode explores tasks through native screens without a per-app control API |
| A calibrated skill | Repeatable operations on known layouts | Reuse bounded steps and inspect outcomes; 24 registered cards are an inventory, not full-app acceptance |
| Visual model reasoning | Choosing the next step on a changing or unfamiliar screen | Muse Spark proposes an action; input geometry and result checking still matter |
| Physical Bluetooth input | A device that accepts paired keyboard/mouse reports | The ESP32 carries out input on the owner's iPad; a Mac coordinates screen capture and cloud calls |

The contribution is the combination: real native screens, Meta reasoning, calibrated skills and inspectable action state. We claim neither universal app competence nor a replacement for approved APIs.

## Applications we want to explore

- **Creators:** collect public app references and prepare source-linked briefs before a human publishes.
- **Independent builders:** investigate unfamiliar app flows and document reproducible observations.
- **Everyday desk tasks:** voice-directed, reviewed information gathering on an existing tablet.
- **Longer-term research:** hands-free assistance for people who find touch interaction difficult. No accessibility efficacy, medical suitability or unattended-care claim is made.

Device reuse could broaden access without a new dedicated gadget; no environmental impact, cost saving, user count or productivity gain has been measured. The current cost is the board plus an iPad, Mac, network, cloud access and setup effort.

## What must get better

1. Measure the whole task: source-to-result time, model waits, input waits, verification and human attention, including failures.
2. Demonstrate interruption and recovery on real hardware, including stop/new-action timing and late model results. Existing heuristics and their limits remain in [Safety](SAFETY.md).
3. Bind higher-risk actions to the exact account, draft, attachment and one-use confirmation in a stronger enforcement layer; today's photo flow still requires operator checks.
4. Simplify receiver installation, device isolation and calibration; keep firmware size visible as features grow.
5. Evaluate approved model/voice alternatives against the same tasks; the current demo uses Gemini Live for voice/camera, Muse Spark for device reasoning and Jev for primary visual checks.

Based on Henry Yuan's [ESP32 iPhone Agent](https://github.com/onehumanbeing/esp32-iphone-agent) (MIT). Selected Hermes memory components: Nous Research (MIT). Muse character: Meta; not affiliated with or endorsed by Meta.
