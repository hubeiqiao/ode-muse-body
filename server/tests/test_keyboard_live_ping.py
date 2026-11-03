import asyncio, pytest
from hidagent.phone import Phone, PhoneUnavailable


class Dev:
    connected = True
    def __init__(self, acks_needed):
        self.sent, self.acks = [], 0
        self.acks_needed = acks_needed
    def readiness(self):
        stable = self.acks >= self.acks_needed
        return {'connected': True, 'stable': stable,
                'reason': 'ready' if stable else 'waiting_for_stable_acknowledgements'}
    async def send(self, command):
        self.sent.append(command); self.acks += 1


class Scr:
    name = 'iPad Agent'
    def mirror_health(self): return {'state': 'session_connected_no_recent_frame'}


def phone(dev):
    p = Phone.__new__(Phone); p.device, p.screen = dev, Scr(); return p


def test_quiet_link_is_refreshed_by_ping_not_pointer():
    dev = Dev(acks_needed=2)
    asyncio.run(phone(dev).ensure_keyboard_live())
    assert dev.sent == ['ping', 'ping']


def test_link_that_never_acks_is_still_refused():
    dev = Dev(acks_needed=99)
    with pytest.raises(PhoneUnavailable):
        asyncio.run(phone(dev).ensure_keyboard_live())
    assert dev.sent == ['ping', 'ping'] and not any(c.startswith(('move', 'press')) for c in dev.sent)
