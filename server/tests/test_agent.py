import math

import pytest

from hidagent.agent import circle, parse_actions, parse_command, parse_plan


def test_parse_single_action():
    acts = parse_actions("THINK: The Search pill is visible, so I tap it.\nDO: tap 500 814")
    assert acts == [{"thought": "The Search pill is visible, so I tap it.", "thought_zh": "",
                     "action": "tap", "x": 500, "y": 814}]


def test_parse_several_actions_keeps_thought_on_first():
    acts = parse_actions("THINK: Type and search.\nDO: type Whiteboard\nDO: key enter")
    assert [a["action"] for a in acts] == ["type", "key"]
    assert acts[0]["text"] == "Whiteboard" and acts[1]["combo"] == "enter"
    assert "thought" not in acts[1]


def test_parse_caps_at_six_actions():
    reply = "THINK: draw\n" + "\n".join(f"DO: tap {i} {i}" for i in range(10))
    assert len(parse_actions(reply)) == 6


def test_parse_path_and_circle():
    assert parse_command("path 100 200 300 400 500 600")["points"] == [[100, 200], [300, 400], [500, 600]]
    assert parse_command("circle 500 450 200") == {"action": "circle", "x": 500, "y": 450, "r": 200}


@pytest.mark.parametrize("line", ["fly 1 2", "tap 500", "path 1 2 3"])
def test_parse_rejects_bad_commands(line):
    with pytest.raises(ValueError):
        parse_command(line)


def test_parse_requires_a_do_line():
    with pytest.raises(ValueError):
        parse_actions("THINK: I am not sure what to do.")


@pytest.mark.parametrize("answer", ["", "ANSWER", "answer", '"ANSWER"'])
def test_done_missing_or_placeholder_answer_is_empty(answer):
    assert parse_command(f"done {answer}") == {"action": "done", "answer": ""}
    assert parse_actions(f"THINK: Content is visible.\nDO: done {answer}")[0]["answer"] == ""


@pytest.mark.parametrize("ending", ["done", "done ANSWER", "done The chat is visible."])
def test_parse_think_and_do_on_one_line(ending):
    thought = "Joe Speaking chat content is already visible."
    assert parse_actions(f"THINK: {thought} DO: {ending}") == [
        {"thought": thought, "thought_zh": "", "action": "done",
         "answer": "The chat is visible." if ending.endswith("visible.") else ""}
    ]


def test_parse_inline_action_preserves_following_actions_and_language():
    assert parse_actions("think： Tap the field. do： tap 152 362 precise\nZH: Tap it.\nDO: type ANSWER DO: done") == [
        {"thought": "Tap the field.", "thought_zh": "Tap it.",
         "action": "tap", "x": 152, "y": 362, "precise": True},
        {"action": "type", "text": "ANSWER DO: done"},
    ]


@pytest.mark.parametrize("command", ["tap 500", "tap", "swipe 1 2 3", "path 1 2 3", "fly 1 2"])
def test_parse_inline_action_keeps_command_validation(command):
    with pytest.raises(ValueError):
        parse_actions(f"THINK: Act on the visible target. DO: {command}")


@pytest.mark.parametrize("command,key,value", [
    ("done ANSWER saved.", "answer", "ANSWER saved."),
    ("type ANSWER", "text", "ANSWER"),
    ("type THINK: visible DO: done", "text", "THINK: visible DO: done"),
    ("key enter", "combo", "enter"),
    ("fail ANSWER", "reason", "ANSWER"),
])
def test_parse_preserves_real_text_arguments(command, key, value):
    assert parse_actions(f"THINK: Use the visible field.\nDO: {command}")[0][key] == value


def test_parse_plan_with_and_without_languages():
    assert parse_plan("EN:\n1. Tap Search\nZH:\n1. 点搜索") == ("1. Tap Search", "1. 点搜索")
    assert parse_plan("1. Tap Search") == ("1. Tap Search", "1. Tap Search")


def test_circle_is_closed_and_round_on_a_tall_screen():
    aspect = 0.46  # width / height of a portrait iPhone frame
    pts = circle(0.5, 0.5, 0.2, aspect)
    assert pts[0] == pytest.approx(pts[-1])
    for x, y in pts:  # equal radius in pixels: y offsets shrink by the aspect ratio
        assert math.hypot(x - 0.5, (y - 0.5) / aspect) == pytest.approx(0.2)


def test_actor_may_not_repeat_a_move_that_changed_nothing():
    import asyncio

    from hidagent.agent import Agent
    from hidagent.debug import Trace

    class Screen:
        live, frame, aspect = True, b"jpeg", 0.46

    class Actor:
        model = "fake"
        def __init__(self):
            self.prompts = []
        async def chat(self, system, text, image=None, max_tokens=600, temperature=0):
            self.prompts.append(text)
            return "THINK: again\nDO: swipe 382 253 382 150" if len(self.prompts) == 1 else "THINK: other\nDO: tap 1 2"

    actor = Actor()
    agent = Agent(phone=None, screen=Screen(), actor=actor, planner=actor, trace=Trace(None))
    agent.steps = [{"action": "swipe", "x1": 382, "y1": 253, "x2": 382, "y2": 150, "changed": False}]
    acts = asyncio.run(agent._next_actions())
    assert acts[0]["action"] == "tap"
    assert "changed nothing" in actor.prompts[1]


def test_repeating_a_move_that_keeps_changing_the_screen_is_not_stuck():
    from hidagent.agent import Agent
    from hidagent.debug import Trace

    agent = Agent(phone=None, screen=None, actor=None, planner=None, trace=Trace(None))
    swipe = {"action": "swipe", "x1": 500, "y1": 800, "x2": 500, "y2": 400}
    agent.steps = [{**swipe, "changed": True}, {**swipe, "changed": True}]
    assert not agent._repeating(dict(swipe))
    agent.steps[-1]["changed"] = False  # reached the end of the list
    assert agent._repeating(dict(swipe))


def test_precise_tap_flag():
    assert parse_command("tap 120 640 precise") == {"action": "tap", "x": 120, "y": 640, "precise": True}
    assert "precise" not in parse_command("tap 120 640")


@pytest.mark.parametrize('action,changed,missed', [('key', False, False), ('wait', False, False),
                                                 ('type', False, False), ('tap', False, True), ('tap', True, False)])
def test_only_unchanged_taps_trigger_precision_retry(action, changed, missed):
    from types import SimpleNamespace
    from hidagent.agent import Agent
    assert Agent._tap_missed(SimpleNamespace(steps=[{'action': action, 'changed': changed}])) is missed


@pytest.mark.parametrize('device,count', [('ipad', 1), ('iphone', 3)])
def test_ipad_observes_between_actions_while_iphone_keeps_batches(device, count):
    import asyncio
    from types import SimpleNamespace
    from hidagent.agent import Agent
    from hidagent.debug import Trace
    class Model:
        model = 'fake'
        async def chat(self, *args, **kwargs):
            return 'THINK: clear and calculate\nDO: key esc\nDO: type 2+2\nDO: key enter'
    model = Model()
    agent = Agent(SimpleNamespace(target_info=lambda: {'device': device}),
                  SimpleNamespace(live=True, frame=b'jpeg', aspect=.65), model, model, Trace(None))
    actions = asyncio.run(agent._next_actions())
    assert len(actions) == count


def test_frozen_mirror_is_detected_after_three_unseen_inputs():
    from types import SimpleNamespace
    from hidagent.agent import Agent
    a = Agent.__new__(Agent)
    a.started_at = 100.0
    a.screen = SimpleNamespace(updated=50.0)
    a.steps = [{'action': 'plan'}, {'action': 'key'}, {'action': 'home'}]
    assert not a._frozen()
    a.steps.append({'action': 'key'})
    assert a._frozen()
    a.screen.updated = 101.0
    assert not a._frozen()
