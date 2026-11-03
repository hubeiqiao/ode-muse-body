"""Optional Jev image decisions; callers decide whether and how to act on them."""

import base64
import io
from PIL import Image
import math
import os
import shlex
import time
import uuid
from pathlib import Path

import httpx
from .identity import judge_prompt


class DecisionUnavailable(RuntimeError):
    def __init__(self, message, result=None):
        super().__init__(message)
        self.result = result


def read_key_file(path: str) -> str:
    """Read the one requested credential; never source or execute a dotenv file."""
    for line in Path(path).read_text().splitlines():
        name, sep, value = line.removeprefix("export ").partition("=")
        if sep and name.strip() == "TYPESAFE_API_KEY":
            parts = shlex.split(value, comments=True)
            if parts:
                return parts[0]
    raise DecisionUnavailable("TYPESAFE_API_KEY is not configured")


def probability(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 0 <= value <= 1


def compatible_image(image: bytes):
    """Keep the whole frame; normalized boxes survive an aspect-preserving resize."""
    try:
        picture = Image.open(io.BytesIO(image))
    except OSError:
        raise DecisionUnavailable('Invalid screen image; no image request sent') from None
    source = picture.size
    # TypeSafe official image bounds require BOTH sides >=280. A long URL
    # strip cannot be scaled to that minimum without exceeding the max side;
    # pad instead, retaining every source pixel and its aspect ratio.
    if max(source)<=1080 and min(source)>=280:
        return image, {'source':source, 'sent':source}
    scale=min(1,1080/max(source))
    size=tuple(max(1,round(value*scale)) for value in source)
    picture=picture.convert('RGB')
    if size!=source:picture=picture.resize(size,Image.Resampling.LANCZOS)
    canvas_size=tuple(max(280,value) for value in size)
    geometry={'source':source,'sent':canvas_size}
    if canvas_size!=size:
        left,top=((canvas_size[i]-size[i])//2 for i in range(2))
        canvas=Image.new('RGB',canvas_size,(127,127,127));canvas.paste(picture,(left,top));picture=canvas
        geometry['content_box']={'left':left/canvas_size[0],'right':(left+size[0])/canvas_size[0],
                                 'top':top/canvas_size[1],'bottom':(top+size[1])/canvas_size[1]}
    output=io.BytesIO();picture.save(output,format='JPEG',quality=95 if 'content_box' in geometry else 90)
    return output.getvalue(),geometry


class Jev:
    def __init__(self, key: str, model: str = "jev-latest", timeout: float = 3.0, transport=None):
        if not key:
            raise DecisionUnavailable("TypeSafe key is missing")
        self.model = model
        # Credential stays bound to the official origin; no redirect or automatic paid retry.
        self.http = httpx.AsyncClient(base_url="https://api.typesafe.ai/v1/", timeout=timeout,
                                      headers={"Authorization": f"Bearer {key}"}, transport=transport)

    @classmethod
    def from_env(cls):
        key = os.environ.get("TYPESAFE_API_KEY")
        if not key:
            path = os.environ.get("JEV_KEY_FILE")
            if not path:
                raise DecisionUnavailable("Set JEV_KEY_FILE or TYPESAFE_API_KEY")
            key = read_key_file(path)
        return cls(key, os.environ.get("JEV_MODEL", "jev-latest"), float(os.environ.get("JEV_TIMEOUT", "3")))

    async def close(self):
        await self.http.aclose()

    async def evaluate(self, state, questions: dict, image: bytes | None = None) -> dict:
        from .frame_privacy import check_cloud_frame
        check_cloud_frame(image)
        body = {"model": self.model, "state": state, "questions": {
            name: {**question, 'instructions': judge_prompt(question['instructions'])}
            for name, question in questions.items()}}
        headers = {}
        geometry = None
        if image:
            image, geometry = compatible_image(image)
            ref = str(uuid.uuid4())
            body["state"] = {"context": state, "screen": ref}
            if 'content_box' in geometry:body['state']['screen_content_box']=geometry['content_box']
            body["assets"] = {ref: {"type": "image", "format": "jpg",
                                     "data_base64": base64.b64encode(image).decode()}}
            headers["TypeSafe-Danger-Zone-Images"] = "true"
        started = time.perf_counter()
        try:
            response = await self.http.post("systemone", json=body, headers=headers)
        except httpx.HTTPError:
            raise DecisionUnavailable("Jev connection failed or timed out") from None
        elapsed = time.perf_counter() - started
        if not response.is_success:
            raise DecisionUnavailable(f"Jev HTTP {response.status_code}")
        try:
            data = response.json()
            answers = data["answers"]
            if not isinstance(answers, dict):
                raise ValueError("answers")
            for name, question in questions.items():
                answer = answers[name]
                kind = question["type"]
                if answer["type"] != kind:
                    raise ValueError("type")
                if kind == "choice" and answer["choice"] not in question["criteria"]:
                    raise ValueError("choice")
                if kind == "noul" and not probability(answer["noul"]):
                    raise ValueError("noul")
                if kind in ("choice", "box") and not probability(answer["confidence"]):
                    raise ValueError("confidence")
                if kind == "box":
                    box = answer["box"]
                    if not all(probability(box[k]) for k in ("left", "right", "top", "bottom")):
                        raise ValueError("coordinates")
                    if not (box["left"] < box["right"] and box["top"] < box["bottom"]):
                        raise ValueError("box")
                    if geometry and 'content_box' in geometry:
                        content=geometry['content_box']
                        # Never clip a model-selected padding area into a real
                        # control. Reject it, preserving normal backup behavior.
                        if any(box[k]<content[k]-1e-7 for k in ('left','top')) or any(box[k]>content[k]+1e-7 for k in ('right','bottom')):
                            raise ValueError('box outside source image')
                        answer['box']={k:max(0,min(1,(box[k]-content['left'])/(content['right']-content['left']))) if k in ('left','right')
                            else max(0,min(1,(box[k]-content['top'])/(content['bottom']-content['top']))) for k in box}
        except (ValueError, KeyError, TypeError):
            raise DecisionUnavailable("Jev returned an invalid decision") from None
        return {"model": data.get("model"), "answers": answers, "usage": data.get("usage", {}),
                "seconds": elapsed, **({"image_geometry":geometry} if geometry else {})}

    async def refine(self, image: bytes, target: str, coarse: dict) -> dict:
        """One better-resolution grounding pass, preserving the acceptance threshold."""
        picture = Image.open(io.BytesIO(image))
        w, h = picture.size
        cx, cy = (coarse['left'] + coarse['right']) / 2, (coarse['top'] + coarse['bottom']) / 2
        px, py = max(.12, (coarse['right'] - coarse['left']) * 1.5), max(.12, (coarse['bottom'] - coarse['top']) * 1.5)
        bounds = (max(0, int((cx-px)*w)), max(0, int((cy-py)*h)),
                  min(w, int((cx+px)*w)), min(h, int((cy+py)*h)))
        crop = picture.crop(bounds)
        scale = min(3, 640 / max(crop.size))
        crop = crop.resize((round(crop.width * scale), round(crop.height * scale)))
        out = io.BytesIO(); crop.convert('RGB').save(out, format='JPEG', quality=90)
        crop_target = target.split(' near ')[0]
        result = await self.evaluate({'target': crop_target, 'view': 'An enlarged crop of the original screen.'},
                                     {'target': {'type': 'box', 'instructions': f'Locate {crop_target} in screen.'},
                                      'visible': {'type': 'noul', 'instructions': f'Is {crop_target} visibly present in this enlarged screen crop?'}}, out.getvalue())
        box = result['answers']['target']['box']
        x0, y0, x1, y1 = bounds
        mapped = {'left': (x0 + box['left']*(x1-x0))/w, 'right': (x0 + box['right']*(x1-x0))/w,
                  'top': (y0 + box['top']*(y1-y0))/h, 'bottom': (y0 + box['bottom']*(y1-y0))/h}
        return {**result, 'mapped_box': mapped, 'crop_bounds': bounds}

    async def locate(self, image: bytes, target: str, threshold: float = .9) -> dict:
        result = await self.evaluate(
            {"target": target},
            {"visible": {"type": "noul", "instructions": f"Is {target!r} clearly visible in screen?"},
             "target": {"type": "box", "instructions": f"Locate {target!r} in screen."}}, image)
        answer = result["answers"]["target"]
        if result["answers"]["visible"]["noul"] < threshold or answer["confidence"] < threshold:
            raise DecisionUnavailable("Target was not located confidently; use the existing actor", result)
        box = answer["box"]
        return {**result, "point": {"x": (box["left"] + box["right"]) / 2,
                                    "y": (box["top"] + box["bottom"]) / 2}}
