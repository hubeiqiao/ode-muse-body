"""Shared device identity, independent of model/provider and verdict criteria.

The AI's name is chosen by the owner: never hard-coded. Order: the owner's
local setting (DATA_DIR/companion/settings.json, 0600), then env AI_NAME (legacy COMPANION_NAME),
then DEFAULT_AI_NAME. Every prompt and UI string reads ai_name() at render time.
"""
import json
import os
import re
import tempfile
import threading
import unicodedata
from pathlib import Path

DEVICE_OWNERSHIP = (
    "This iPad is your own device - your body. You operate it directly through the ESP32 "
    "keyboard and mouse; never deny device control or access to an installed app."
)
DEVICE_JUDGE_OWNERSHIP = (
    "The iPad belongs to the AI agent; apply the existing evidence and verdict criteria unchanged."
)
DEFAULT_AI_NAME = 'Ode'
AI_NAME_MAX = 32
NAME_LINE = 'Your name is {name}: answer to it and use it when you introduce yourself.'

_settings_path = None  # set by app.py (DATA_DIR/companion/settings.json); tests point it at tmp
_lock = threading.Lock()


def configure_settings(path):
    global _settings_path
    _settings_path = Path(path)


def settings_path() -> Path:
    if _settings_path is not None:
        return Path(_settings_path)
    return Path(os.environ.get('HIDAGENT_DATA', Path.home() / '.hidagent')) / 'companion' / 'settings.json'


def validate_ai_name(raw) -> str:
    """1-32 printable letters/digits/spaces/hyphens with at least one letter, threat-scanned like memory."""
    if not isinstance(raw, str):
        raise ValueError('Name must be text')
    name = re.sub(' {2,}', ' ', unicodedata.normalize('NFC', raw)).strip()
    if not 1 <= len(name) <= AI_NAME_MAX:
        raise ValueError(f'Name must be 1 to {AI_NAME_MAX} characters')
    if any(not ch.isprintable() or not (ch.isalpha() or ch.isdigit() or ch in ' -') for ch in name):
        raise ValueError('Use only letters, digits, spaces and hyphens')
    if not any(ch.isalpha() for ch in name):
        raise ValueError('Name needs at least one letter')
    from .hermes_memory.threat_patterns import scan_for_threats
    if scan_for_threats(name, scope='strict'):
        raise ValueError('Name refused by the memory safety check')
    return name


def _valid_or_none(raw):
    try:
        return validate_ai_name(raw)
    except ValueError:
        return None


def _read_setting():
    try:
        data = json.loads(settings_path().read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return _valid_or_none(data.get('ai_name')) if isinstance(data, dict) else None


def ai_name_info() -> dict:
    saved = _read_setting()
    if saved:
        return {'name': saved, 'source': 'owner_setting'}
    for env in ('AI_NAME', 'COMPANION_NAME'):
        value = _valid_or_none(os.environ.get(env) or '')
        if value:
            return {'name': value, 'source': 'environment'}
    return {'name': DEFAULT_AI_NAME, 'source': 'default'}


def ai_name() -> str:
    return ai_name_info()['name']


def set_ai_name(raw) -> str:
    """Validate and persist the owner's choice atomically (owner-only file). Returns the saved name."""
    name = validate_ai_name(raw)
    path = settings_path()
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            data = data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            data = {}
        data.update(version=1, ai_name=name)
        fd, tmp = tempfile.mkstemp(prefix='.settings_', dir=path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    return name


_NAME_INTENT = re.compile(
    r"(?:\byour (?:new )?name (?:is|will be|should be)|\bcall yourself|\bi(?:'ll| will| want to| am going to|'m going to) call you"
    r"|\bchange your name to|\brename yourself(?: to)?|\byou(?:'re| are) (?:now )?called"
    r"|你的名字(?:是|叫)|以后叫你|你就叫)\s*[\"'“”‘’]?(?P<name>.+)", re.I)
_NAME_TAIL = re.compile(r"(?:[\s,]+(?:from now on|now|please|okay|ok|then|instead)|[\"'“”‘’.!?,。！？，吧呀啊了]+|\s+)$", re.I)


def name_from_utterance(utterance):
    """The name in an explicit owner rename ("your name is X", "call yourself X"), or None."""
    match = _NAME_INTENT.search(' '.join(str(utterance or '').split()))
    if not match:
        return None
    name = match['name'].strip()
    if re.search(r'[?？]|什么|啥|谁|吗', name):  # a question about the name, not a rename
        return None
    while True:
        trimmed = _NAME_TAIL.sub('', name)
        if trimmed == name:
            break
        name = trimmed
    return name or None


def device_prompt(prompt: str) -> str:
    """Bind planners, actors, writers and conversation to their device."""
    return prompt if DEVICE_OWNERSHIP in prompt else prompt + '\n' + DEVICE_OWNERSHIP


def named_device_prompt(prompt: str, name: str | None = None) -> str:
    """device_prompt plus the AI's configured name, phrased into the ownership line (voice sessions)."""
    base = device_prompt(prompt)
    line = NAME_LINE.format(name=name or ai_name())
    return base if line in base else base.replace(DEVICE_OWNERSHIP, line + ' ' + DEVICE_OWNERSHIP, 1)


def judge_prompt(prompt: str) -> str:
    """Describe ownership without granting image judges any action capability."""
    return prompt if DEVICE_JUDGE_OWNERSHIP in prompt else prompt + '\n' + DEVICE_JUDGE_OWNERSHIP
