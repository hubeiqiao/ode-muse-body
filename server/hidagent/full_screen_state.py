"""Runtime-scoped iPad setting proof, independent of HID links and orientation."""
import json
import os
from pathlib import Path
import re
import tempfile


def identity(info):
    if info.get('device') != 'ipad' or not info.get('calibration_key'):
        return None
    # A runtime/calibration store belongs to one physical device. The profile's
    # portrait/landscape spelling is geometry, not a different iPad.
    key = re.sub(r'-(?:landscape|portrait)(?=-|$)', '', info['calibration_key'])
    return 'ipad:' + key


class FullScreenState:
    def __init__(self, phone, memory):
        root = os.environ.get('HIDAGENT_DATA')
        calibration = getattr(phone, '_base_file', None)
        memory_path = getattr(getattr(memory, 'memory', None), 'path', None)
        self.path = (Path(root) if root else Path(calibration).parent if calibration else
                     Path(memory_path) if memory_path else Path.home() / '.hidagent') / 'full-screen-apps.json'
        self.records = self._load()

    def _load(self):
        try:
            data = json.loads(self.path.read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def proof(self, info):
        # The owner's explicit override is independent of transient calibration
        # identity and wins over any older negative observation.
        if os.environ.get('IPAD_FULL_SCREEN_VERIFIED') == '1':
            return True, 'env'
        self.records = self._load()
        record = self.records.get(identity(info))
        if isinstance(record, dict):
            return record.get('verified') is True, 'persisted'
        return False, 'unverified'

    def verified(self, info):
        return self.proof(info)[0]

    def mark(self, info, verified, source):
        # Flows may record positive proof, but only owner configuration can
        # revoke it. App geometry is never evidence of a system setting change.
        if verified is not True:
            raise ValueError('Only owner configuration may revoke Full Screen Apps')
        key = identity(info)
        if not key: return
        record = {'verified': verified, 'source': source}
        if self.records.get(key) == record: return
        self.records[key] = record
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix='.full-screen-', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'w') as f:
                json.dump(self.records, f); f.flush(); os.fsync(f.fileno())
            os.replace(name, self.path)
        finally:
            if os.path.exists(name): os.unlink(name)
