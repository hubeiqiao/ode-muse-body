import asyncio
import io

from PIL import Image

from hidagent.agent import Agent, scale_coordinates
from hidagent.debug import Trace


def test_original_image_pixels_map_to_normalized_tap():
    result = scale_coordinates({'action': 'tap', 'x': 541, 'y': 432, 'precise': True}, 1000 / 710, 1000 / 1080)
    assert result == {'action': 'tap', 'x': 762, 'y': 400, 'precise': True}


def test_paths_radius_and_text_preserve_their_meaning():
    assert scale_coordinates({'action': 'path', 'points': [[71, 108], [355, 540]]}, 1000 / 710, 1000 / 1080)['points'] == [[100, 100], [500, 500]]
    assert scale_coordinates({'action': 'circle', 'x': 355, 'y': 540, 'r': 71}, 1000 / 710, 1000 / 1080)['r'] == 100
    assert scale_coordinates({'action': 'type', 'text': 'Settings 123'}, 2, 3)['text'] == 'Settings 123'


def test_actor_receives_native_image_dimensions_and_pixel_history(monkeypatch):
    monkeypatch.setenv('ACTOR_COORDINATES', 'pixels')
    frame = io.BytesIO()
    Image.new('RGB', (710, 1080)).save(frame, 'JPEG')

    class Screen:
        live = True

    screen = Screen()
    screen.frame = frame.getvalue()

    class Model:
        model = 'test'

        async def chat(self, system, text, image, temperature=0):
            assert '710 by 1080' in system
            assert 'Do not normalize' in system
            assert '"x": 355' in text and '"y": 540' in text
            return 'THINK: Select the visible close button.\nDO: tap 541 432 precise'

    model = Model()
    agent = Agent(None, screen, model, model, Trace(None))
    agent.task = 'Select close'
    agent.steps = [{'action': 'tap', 'x': 500, 'y': 500, 'changed': True}]
    result = asyncio.run(agent._next_actions())
    assert result[0]['x'] == 762 and result[0]['y'] == 400
