"""Le service des reglages, avec une fausse harpe qui repond comme la vraie."""

import pytest

from kromabridge.harp_service import HarpService
from kromabridge.midi_io import MidiError

REAL_DUMP = bytes.fromhex(
    "F07B017064010204646464000100000001000900000102030005000"
    "00D2D32000A1414003200002D5A000056F7")
VERSION = bytes.fromhex("F07B01067A01040400F7")


class FakeHarp:
    def __init__(self, service=None, repond=True):
        self.service = service
        self.repond = repond
        self.envois = []

    def send(self, message):
        self.envois.append(list(message))
        if not self.repond:
            return
        if message[:4] == [0xF0, 0x7B, 0x01, 0x79]:
            self.service.feed(VERSION)
        elif message[:4] == [0xF0, 0x7B, 0x01, 0x78]:
            self.service.feed(b"\xb0\x78\x00" + REAL_DUMP)


def make(repond=True):
    harpe = FakeHarp(repond=repond)
    service = HarpService(harpe, timeout=0.2)
    harpe.service = service
    return harpe, service


def test_read_fills_values_and_version():
    _, service = make()
    etat = service.read()
    assert etat["version"] == "1.440"
    assert etat["valeurs"]["gain"] == 75
    assert len(etat["reglages"]) == 30


def test_set_sends_the_cc():
    harpe, service = make()
    service.set("rayons", 8)
    assert harpe.envois[-1] == [0xB0, 45, 102]
    assert service.valeurs["rayons"] == 8


def test_recall_sends_preset_then_reads():
    harpe, service = make()
    service.recall(3)
    assert harpe.envois[0] == [0xF0, 0x7B, 0x01, 0x74, 3, 0xF7]
    assert [0xF0, 0x7B, 0x01, 0x78, 0xF7] in harpe.envois


def test_silent_harp_gives_a_clear_error():
    _, service = make(repond=False)
    with pytest.raises(MidiError):
        service.read()


def test_unknown_setting_is_refused():
    _, service = make()
    with pytest.raises(ValueError):
        service.set("inconnu", 1)


def test_read_forces_transpose_and_octave_back_to_zero():
    harpe, service = make()
    # le releve de la harpe a Transpose = 0, Octave = 0 : rien a envoyer
    service.read()
    assert not any(e[:2] in ([0xB0, 61], [0xB0, 62]) for e in harpe.envois)
    # si la harpe etait transposee, la lecture la remet a 0
    service.valeurs["transposition"] = 2
    service.enforce_locks()
    assert harpe.envois[-1] == [0xB0, 61, 0]


def test_locked_settings_refuse_other_values():
    _, service = make()
    with pytest.raises(ValueError):
        service.set("transposition", 2)
    with pytest.raises(ValueError):
        service.set("octave", 4)
    assert "transposition" in service.state()["verrouilles"]


class LateHarp(FakeHarp):
    """Comme la vraie harpe : chaque reponse ne sort qu'au message suivant."""

    def __init__(self):
        super().__init__()
        self.en_attente = None

    def send(self, message):
        self.envois.append(list(message))
        if self.en_attente is not None:
            self.service.feed(self.en_attente)
        self.en_attente = VERSION if message[3] == 0x79 else REAL_DUMP if message[3] == 0x78 else None


def test_read_works_with_a_harp_that_releases_replies_late():
    harpe = LateHarp()
    service = HarpService(harpe, timeout=1.0)
    harpe.service = service
    etat = service.read()
    assert etat["valeurs"]["gain"] == 75
    assert etat["version"] == "1.440"


def test_open_animated_starts_narrow_opens_pauses_then_widens_smoothly():
    from kromabridge.harp_service import open_animated

    class Ctl:
        def __init__(self):
            self.log = []
        def send(self, m):
            self.log.append(("send", list(m)))
        def send_quick(self, m):
            self.log.append(("quick", list(m)))

    ctl, pauses = Ctl(), []
    largeurs = open_animated(ctl, 40, pause=2.0, duree=1.5, sleep=pauses.append, step_s=0.03)
    assert ctl.log[0] == ("send", [0xB0, 42, 0])          # largeur au minimum
    assert ctl.log[1] == ("send", [0xFA])                 # puis ouverture
    assert pauses[0] == 2.0                               # pause
    assert largeurs == sorted(largeurs) and largeurs[-1] == 40 and len(largeurs) > 20
    assert ctl.log[-1][0] == "send"                       # le dernier pas avec relance


def test_runtime_animation_setting_is_saved(tmp_path):
    from kromabridge.bank import Bank
    from kromabridge.preset import Preset, chromatic_preset
    from kromabridge.runtime import Runtime

    rt = Runtime(Bank([chromatic_preset(3, name="A")]), presets_dir=tmp_path)
    assert rt.opening_plan(50) is None
    rt.set_animation(True, 1.5, 4)
    assert Preset.load(tmp_path / "A.json").animation == {"active": True, "pause": 1.5, "duree": 4.0, "transition": False, "trans_duree": 1.0}
    assert rt.opening_plan(50) == {"largeur": 50, "pause": 1.5, "duree": 4.0}


def test_close_animated_narrows_pauses_closes_then_restores_width():
    from kromabridge.harp_service import close_animated

    class Ctl:
        def __init__(self):
            self.log = []
        def send(self, m):
            self.log.append(("send", list(m)))
        def send_quick(self, m):
            self.log.append(("quick", list(m)))

    ctl, attentes = Ctl(), []
    largeurs = close_animated(ctl, 40, pause=5.0, duree=1.5, sleep=attentes.append, step_s=0.03)
    assert largeurs == sorted(largeurs, reverse=True) and largeurs[-1] == 0
    assert 5.0 in attentes
    assert ctl.log[-3:] == [("send", [0xB0, 42, 0]), ("send", [0xFC]), ("send", [0xB0, 42, 57])]


def test_animations_really_last_the_requested_duration():
    from kromabridge.harp_service import close_animated, open_animated

    class Ctl:
        def send(self, m): pass
        def send_quick(self, m): pass

    for fn in (open_animated, close_animated):
        attentes = []
        fn(Ctl(), 46, pause=0.0, duree=20.0, sleep=attentes.append, step_s=0.03)
        assert abs(sum(attentes) - 20.0) < 0.1, fn.__name__   # bug du 16/11 : 1,4 s au lieu de 20



def test_firmware_sans_lecture_complete():
    harpe, service = make()
    assert service.firmware() is None
    harpe.send([0xF0, 0x7B, 0x01, 0x79, 0xF7])       # la relance envoyee apres chaque message
    assert service.firmware() == "1.440"
