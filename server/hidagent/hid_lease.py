"""Task-local HID lease. Rescue release remains possible after cancellation."""
from contextlib import contextmanager
from contextvars import ContextVar

_guard=ContextVar('hid_input_lease',default=None)
# Camera conversation (CAMERA_CONVERSATION=1) only: an input-attempt counter for
# gate G9 and an optional block hook. With no camera session both are inert.
# Board diagnostics are not input to the device and are neither counted nor blocked.
_NOT_INPUT=('ping','stats','tune')
_hid_count=0
_camera_block=None

@contextmanager
def leased_input(guard):
    parent=_guard.get()
    def combined():
        if parent is not None:parent()
        guard()
    token=_guard.set(combined)
    try:yield
    finally:_guard.reset(token)

def set_camera_block(block):
    global _camera_block
    _camera_block=block

def hid_count():
    return _hid_count

def check_hid(command):
    global _hid_count
    text=command.strip()
    if text!='release' and text.partition(' ')[0] not in _NOT_INPUT:
        _hid_count+=1  # attempts count, refused or not: G9 treats any as concurrent input
        block=_camera_block
        if block is not None and block(text):
            raise RuntimeError('camera_conversation_active: HID refused while the camera conversation runs')
    guard=_guard.get()
    if guard is not None and command.strip()!='release':guard()

def has_input_lease():
    return _guard.get() is not None
