"""Attribution des notes depuis un clavier MIDI."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kromabridge.bank import Bank  # noqa: E402
from kromabridge.keyboard import KeyboardListener, ports_clavier  # noqa: E402
from kromabridge.preset import Beam, Preset  # noqa: E402
from kromabridge.runtime import Runtime  # noqa: E402


def boitier(tmp_path):
    presets = tmp_path / "presets"
    presets.mkdir()
    p = Preset(name="Un", beams=[Beam(index=1, incoming_note=60, note=48),
                                 Beam(index=2, incoming_note=61, note=53)], beam_count=5)
    return Runtime(Bank([p]), presets_dir=presets)


def test_attribution(tmp_path):
    rt = boitier(tmp_path)
    assert rt.keyboard_note(70) is None          # mode inactif
    rt.set_attribution(True)
    assert rt.keyboard_note(70) is None          # aucun rayon coupe
    rt.handle([0x90, 61, 100])                   # on coupe le rayon 2
    rt.handle([0x80, 61, 0])
    assert rt.selected_beam() == 2
    assert rt.keyboard_note(70) == 2
    assert rt.bank.current.beams[1].note == 70
    assert rt.state()["attribution"] is True


class FauxIn:
    def __init__(self):
        self.ports = ["Kromalaser USB", "CH345:CH345 MIDI 1 20:0", "Kromabridge 3"]
        self.ouvert = None
        self.cb = None
    def ignore_types(self, **k): pass
    def set_callback(self, cb): self.cb = cb
    def get_ports(self): return list(self.ports)
    def is_port_open(self): return self.ouvert is not None
    def open_port(self, i): self.ouvert = i
    def close_port(self): self.ouvert = None


def test_ecoute_clavier():
    faux = FauxIn()
    notes = []
    hint = {"v": "ch345"}
    crees = []
    def fabrique():
        crees.append(1)
        return faux
    k = KeyboardListener(lambda: hint["v"], notes.append, factory=fabrique)
    k.check()
    assert k.connected and faux.ouvert == 1
    faux.cb(([0x90, 64, 90], 0))
    faux.cb(([0x90, 65, 0], 0))                  # note off deguisee : ignoree
    faux.cb(([0xB0, 64, 127], 0))
    assert notes == [64]
    hint["v"] = "kromabridge"                    # jamais le port vers le logiciel
    k.check()
    assert not k.connected
    n = len(crees)
    hint["v"] = "ch345"                          # reouverture : objet neuf
    k.check()
    assert k.connected and len(crees) == n + 1
    assert ports_clavier(faux.ports) == ["CH345:CH345 MIDI 1 20:0"]


def test_noms_sans_numero():
    faux = FauxIn()
    faux.ports = ["Arturia KeyLab Essential 61 1", "CH345:CH345 MIDI 1 20:0"]
    k = KeyboardListener(lambda: "", lambda n: None, factory=lambda: faux)
    assert k.ports() == ["Arturia KeyLab Essential 61", "CH345:CH345 MIDI 1"]



def test_apprentissage_clavier(tmp_path):
    rt = boitier(tmp_path)
    assert not rt.keyboard_active()
    rt.start_kb_learning()
    assert rt.keyboard_active()
    for n in [65, 60, 65, 72, 67, 62, 70]:
        rt.keyboard_note(n)
    e = rt.state()
    assert e["apprentissage_clavier"] and e["notes_apprises"] == [60, 62, 65, 67, 70, 72]
    rt.finish_kb_learning("Appris", [60, 62, 65, 67, 70, 72])
    e = rt.state()
    assert e["memoire"] == "Appris" and not e["apprentissage_clavier"]
    assert [r["note"] for r in e["rayons"]] == [60, 62, 65, 67, 70, 72]
    assert not rt.keyboard_active()
    rt.start_kb_learning()
    rt.finish_kb_learning("", [50, 52, 53, 55, 57, 59, 60], remplacer=True)
    e = rt.state()
    assert e["memoire"] == "Appris" and [r["note"] for r in e["rayons"]] == [50, 52, 53, 55, 57, 59, 60]



def test_apprentissage_en_parties(tmp_path):
    rt = boitier(tmp_path)
    rt.start_kb_learning()
    for n in range(48, 66):                      # 18 notes
        rt.keyboard_note(n)
    assert len(rt.state()["notes_apprises"]) == 18
    rt.clear_kb_learning()
    assert rt.state()["apprentissage_clavier"] and rt.state()["notes_apprises"] == []
    rt.finish_kb_learning("Deux parties", [], parties=[
        {"nom": "A", "notes": list(range(48, 57))}, {"nom": "B", "notes": list(range(57, 66))}])
    e = rt.state()
    assert e["memoire"] == "Deux parties" and e["parties"] == ["A", "B"]
    assert not e["apprentissage_clavier"]



def test_son_du_clavier(tmp_path):
    rt = boitier(tmp_path)
    sortie, windows = [], []
    rt.send = sortie.extend
    rt.send_windows = windows.extend
    rt.keyboard_echo([0x90, 60, 90])
    assert sortie == [] and windows == []          # pas en apprentissage : silence
    rt.start_kb_learning()
    rt.set_parametres(son_clavier="windows")
    rt.keyboard_echo([0x90, 60, 90])
    rt.keyboard_echo([0x80, 60, 0])
    assert windows == [[0x90, 60, 90], [0x80, 60, 0]] and sortie == []
    rt.set_parametres(son_clavier="sortie")
    rt.bank.current.out_channel = 3
    rt.keyboard_echo([0x91, 62, 90])
    assert sortie == [[0x92, 62, 90]]
    rt.set_parametres(son_clavier="aucun")
    rt.keyboard_echo([0x90, 64, 90])
    assert len(sortie) == 1 and len(windows) == 2
