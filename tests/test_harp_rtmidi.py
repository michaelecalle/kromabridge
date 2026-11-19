"""Version PC : la harpe par les ports MIDI du systeme (rtmidi), sans materiel."""

from kromabridge.harp_rtmidi import RtHarpControl, RtHarpListener
from kromabridge.midi_io import HARP_NUDGE


class FakePort:
    def __init__(self, ports):
        self.ports = ports
        self.opened = None
        self.sent = []
        self.callback = None

    def get_ports(self):
        return list(self.ports)

    def is_port_open(self):
        return self.opened is not None

    def open_port(self, i):
        self.opened = i

    def close_port(self):
        self.opened = None

    def send_message(self, m):
        self.sent.append(m)

    def ignore_types(self, **kw):
        self.ignored = kw

    def set_callback(self, cb):
        self.callback = cb

    def delete(self):
        pass


PORTS = ["loopMIDI Port Kromabridge 1", "Kromalaser USB 2"]


def test_control_sends_command_then_two_nudges_to_the_harp_not_the_virtual_port():
    out = FakePort(PORTS)
    harpe = RtHarpControl(factory=lambda: out)
    harpe.open_harp()
    assert out.opened == 1
    assert out.sent == [[0xFA], HARP_NUDGE, HARP_NUDGE]


def test_listener_reads_continuously_keeps_sysex_and_follows_the_harp():
    inp = FakePort(PORTS)
    recu = []
    ecoute = RtHarpListener(RtHarpControl(factory=lambda: FakePort(PORTS)),
                            on_data=recu.append, factory=lambda: inp)
    ecoute.check()
    assert ecoute.connected and inp.opened == 1 and inp.ignored["sysex"] is False
    inp.callback(([0x90, 60, 112], 0.0))
    assert recu == [bytes([0x90, 60, 112])] and ecoute.last_read_ns > 0
    inp.ports = ["loopMIDI Port Kromabridge 1"]          # harpe debranchee
    ecoute.check()
    assert not ecoute.connected and inp.opened is None
