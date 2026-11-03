# Offline scenario evaluation

This package adds receipt measurements and an explicit scenario runner. Importing
it never starts a server or connects to a device. All validation in this branch
uses mocks or the checked-in catalog fixture.

Offline validation from the worktree root:

```sh
python tools/eval/run.py --all --dry-run --catalog-fixture tools/eval/fixtures/catalog.json
```

The fixture contains the registered tool schemas at the base commit, with a fake
server instance. Tests compare its names to the actual registry. It never serves
as a live dispatch catalog. Without `--catalog-fixture`, `--dry-run` makes only a
GET to `/api/app-tools/catalog`; it does not dispatch anything.

For a separately authorized future device session, the interface is:

```sh
python tools/eval/run.py --scenario rednote_search --repeat 1 --base http://127.0.0.1:8890
python tools/eval/run.py --all --repeat 2 --base http://127.0.0.1:8890 --output /tmp/eval-results.jsonl
```

These live commands were **not executed** in this worktree. A fresh live catalog
and an idle, stable, calibrated device with known portrait/landscape orientation
are mandatory. Voice/manual/exploration/AppTools ownership prevents dispatch.
Every operation uses a new instance-prefixed request ID. An operation is sent
once, then only its status is polled. Any uncertain dispatch, polling failure,
deadline, historical/mismatched receipt, failed status or publishing flag halts
the entire batch, including subsequent repeats. No stop, repair, resend, Home
reset, or engagement endpoint is called. Redirects and HTTP proxies are disabled.

Scenario start conditions and optional visible checks are human preconditions,
not facts inferred from `/api/status`. Repeats do not reset the device surface.
Use separately established Home starts when comparing cold-launch measurements.
Table success means the first dispatch sequence returned permitted receipts;
it does not certify visible content or goal quality. JSONL records explicitly
mark visible checks as `manual_required`. P50/max use wall time across all
dispatched attempts, including failures, and omit manual scenarios.

| Scenario | Default execution | Required observation |
| --- | --- | --- |
| `photo` | Manual | Camera conversation is voice-only |
| `grok_read_action` | `grok.read_action` | Dedicated Example Action Workspace card metadata |
| `grok_preview_reply` | Manual | Fresh reviewed card/target, preview, independent check, discard |
| `muse_threads_read` | Manual, proposed `muse.open` | Threads bot target list only |
| `rednote_search` | `xiaohongshu.search`, `public.read_results` | Public search result cards |
| `reddit_chrome_search` | `reddit.search`, `public.read_results` | Public Reddit results in Chrome |
| `instagram_profile_status` | Manual, proposed `instagram.open` | Own visible follower/post counts |
| `discord_status` | Manual, proposed `discord.open` | Server/channel list header; no DMs |
| `bilibili_notifications` | Manual, proposed `bilibili.open` | Notifications/activity header |
| `youtube_notifications` | Manual, proposed `youtube.open` | Notifications/activity header |
| `tiktok_notifications` | Manual, proposed `tiktok.open` | Notifications/activity header |
| `douyin_notifications` | Manual, proposed `douyin.open` | Notifications/activity header |
| `yolo_goal` | Existing `/api/mission/start`, then GET `/api/mission/status` | Checked public research, three-step budget |

Nine scenarios remain manual. Their proposed steps are validated but **never
dispatched**. Opening an app alone does not prove a profile, bot list or activity
tab was read, and Discord might reopen a DM. No matching bounded tool exists in
the base registry. Preview has no supplied fresh target: `00000000-00` and
`@ESP32QA` are inert placeholders, not a task or account authorization. No example authorizes an outgoing action.

The mission API is the existing goal endpoint, not a catalog AppTool. Its frozen
goal requests public research only. It deliberately contains none of the mission
executor's draft-permission trigger words. Its planner is restricted by the
existing mission skill allowlist; no new goal or tool capability is introduced.

Receipts contain a compact `metrics` object:

```json
{
  "total_s": 12.4,
  "model_calls": {
    "count": 3, "total_s": 2.1,
    "jev": {"count": 2, "total_s": 1.4},
    "muse": {"count": 1, "total_s": 0.7},
    "other": {"count": 0, "total_s": 0.0}
  },
  "first_feedback_s": 1.8,
  "hid_actions": 7,
  "waits": 1,
  "launch_method": "spotlight_first",
  "summary": "12.4 s, 3 model calls (2.1 s), first action 1.8 s, 7 inputs"
}
```

This is an illustrative deterministic example, not a device benchmark.

Measurements observe leaf `chat` and Jev `evaluate` calls, including failures,
cancellations, vision JSON repairs and fallback/hedge requests, without double
counting a parent judge or its trace. Provider `meta` maps to Muse; Jev model
names map to Jev; other configured models map to `other`. Each provider attempt
has wall time; overlapping calls can sum beyond task duration. A call is one leaf
model invocation, matching the existing trace boundary. An LLM client's internal
HTTP retries remain inside that invocation's duration and are not a separate
transport-request counter. In-progress calls
count immediately and contribute elapsed time. Final metrics freeze when the
task ends, so late returns cannot rewrite a stopped receipt. Goal metrics add
planner calls and child receipts once, including dispatch offsets for the first
action. Tokens are not fabricated from text-only model responses.

`hid_actions` counts acknowledged input packets (`move`, `click`, `press`,
`scroll`, `key`, `type`, `home`), excluding release/configuration packets. It is
not a count of semantic clicks; typing chunks and pointer moves count separately.
Unacknowledged delivery is not counted as a confirmed input. The first feedback
time is the dispatch offset of the first acknowledged input packet. AppTools
and AppMission have no spoken-status event source in their owned interfaces, so
unobserved speech is never inferred. A tool without device feedback uses `null`.
Waits count explicit agent `wait` steps, excluding HTTP polls and settling sleeps.
Launch methods come only from fixed known trace values; otherwise they are null.

Temporary observation wrappers preserve arguments, results and exceptions, use
task-local context to exclude unrelated calls, and restore the original methods
in `finally`. They do not change timing controls or HID behavior. SQLite stores
metrics separately from existing result JSON and migrates old schemas locally.
Legacy/interrupted rows without measurements use `instrumentation=unavailable`;
their numeric zero defaults are not evidence of zero historical model usage.
Metrics appear in current status, saved status and paginated history, including
mission goal receipts. No typed text, screenshots, prompts, private excerpts,
model names or arbitrary policy strings are copied into metrics.

JSONL stores metrics and a content-free receipt projection, with generated request
IDs, tool names, status, persistence/uncertainty flags and publication booleans.
It omits raw result text, screenshots, exception bodies and provider payloads.
The aggregate row reports runner wall time and model count/time; each step keeps
its own receipt metrics. Missing receipt measurements remain absent in the step
projection and must not be interpreted as verified zero usage.
