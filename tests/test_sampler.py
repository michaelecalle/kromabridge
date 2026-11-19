"""Le son de la harpe."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kromabridge.bank import Bank  # noqa: E402
from kromabridge.preset import Beam, Preset  # noqa: E402
from kromabridge.runtime import Runtime  # noqa: E402
from kromabridge.sampler import banque, choix  # noqa: E402


def test_banque_et_choix(tmp_path):
    import json
    notes = {"Son_C3.wav": 48, "Son_E3.wav": 52, "Son_A3.wav": 57, "Son_C4.wav": 60}
    for f in notes:
        (tmp_path / f).write_bytes(b"")
    (tmp_path / "hauteurs.json").write_text(json.dumps(notes | {"absent.wav": 50}), encoding="utf-8")
    b = banque(tmp_path)
    assert [f for f, _ in b] == ["Son_C3.wav", "Son_E3.wav", "Son_A3.wav", "Son_C4.wav"]   # sans le fichier absent
    f, r = choix(57, b)                      # la3 : son propre echantillon, sans correction
    assert f == "Son_A3.wav" and r == 1.0
    f, r = choix(61, b)                      # do#4 : depuis do4, un demi-ton plus haut
    assert f == "Son_C4.wav" and 1.05 < r < 1.06
    f, r = choix(36, b)                      # do2 : une octave sous do3
    assert f == "Son_C3.wav" and r == 0.5
    assert banque(tmp_path / "rien") == []   # pas de sons : le son de la harpe reste muet


def test_page_recoit_les_notes(tmp_path):
    presets = tmp_path / "presets"
    presets.mkdir()
    p = Preset(name="Un", beams=[Beam(index=1, incoming_note=60, note=48)], beam_count=5)
    rt = Runtime(Bank([p]), presets_dir=presets)
    q = rt.abonner()
    rt.start_kb_learning()
    rt.set_parametres(son_clavier="page")
    rt.keyboard_echo([0x90, 64, 90])
    rt.keyboard_echo([0x80, 64, 0])
    assert q.get_nowait() == {"t": "on", "note": 64, "vel": 90}
    assert q.get_nowait()["t"] == "off"
    rt.desabonner(q)


def test_mode_jeu(tmp_path):
    presets = tmp_path / "presets"
    presets.mkdir()
    p = Preset(name="Un", beams=[Beam(index=1, incoming_note=60, note=48)], beam_count=5)
    rt = Runtime(Bank([p]), presets_dir=presets)
    q = rt.abonner()
    rt.handle([0x90, 60, 100])
    assert q.empty()                                  # par defaut : seulement le MIDI
    rt.set_parametres(son_jeu="page", sortie_midi="MiniFuse 2 MIDI Out")
    rt.handle([0x80, 60, 0])
    rt.handle([0x90, 60, 100])
    evs = [q.get_nowait() for _ in range(q.qsize())]
    assert {"t": "on", "note": 48, "vel": 100} in evs
    assert rt.parametres["sortie_midi"] == "MiniFuse 2 MIDI Out"
