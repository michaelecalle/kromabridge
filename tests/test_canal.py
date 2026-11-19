"""Canal MIDI de sortie reglable par morceau."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kromabridge.bank import Bank  # noqa: E402
from kromabridge.preset import Beam, Preset  # noqa: E402
from kromabridge.runtime import Runtime  # noqa: E402


def test_canal_de_sortie(tmp_path):
    envoyes = []
    p = Preset(name="Un", beams=[Beam(index=1, incoming_note=60, note=67)], beam_count=5)
    rt = Runtime(Bank([p]), send=envoyes.extend, presets_dir=tmp_path)
    rt.handle([0x90, 60, 100])
    rt.set_out_channel(3)
    # la note tenue est coupee sur l'ancien canal 1
    assert [0x80, 67, 0] in envoyes
    envoyes.clear()
    assert rt.handle([0x90, 60, 100]) == [[0x92, 67, 100]]
    assert rt.state()["canal_sortie"] == 3
    fichier = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))
    assert fichier["canal_sortie"] == 3
    with pytest.raises(ValueError):
        rt.set_out_channel(17)



def test_son_du_synthe_par_morceau(tmp_path):
    import json
    from kromabridge.bank import Bank
    from kromabridge.preset import Beam, Preset
    from kromabridge.runtime import Runtime
    presets = tmp_path / "presets"
    presets.mkdir()
    a = Preset(name="A", beams=[Beam(index=1, incoming_note=60, note=60)], out_channel=2,
               synth={"programme": 5, "msb": 1})
    b = Preset(name="B", beams=[Beam(index=1, incoming_note=60, note=60)])
    envoyes = []
    rt = Runtime(Bank([a, b]), send=envoyes.extend, presets_dir=presets)
    rt.select(0)
    assert envoyes[-2:] == [[0xB1, 0, 1], [0xC1, 4]]       # banque puis programme, canal 2
    envoyes.clear()
    rt.select(1)
    assert not [m for m in envoyes if m[0] & 0xF0 == 0xC0]  # morceau sans son : rien
    rt.set_synth_sound(128, None, 3)
    assert envoyes[-2:] == [[0xB0, 32, 3], [0xC0, 127]]
    assert json.loads((presets / "B.json").read_text(encoding="utf-8"))["son_synthe"] == {"programme": 128, "lsb": 3}
    envoyes.clear()
    rt.set_out_channel(3)                                   # le son suit le nouveau canal
    assert envoyes[-2:] == [[0xB2, 32, 3], [0xC2, 127]]
    rt.set_synth_sound(None)
    assert rt.state()["son_synthe"] == {}
    import pytest
    with pytest.raises(ValueError):
        rt.set_synth_sound(0)
    with pytest.raises(ValueError):
        rt.set_synth_sound(1, 128)



def test_son_du_synthe_desactive(tmp_path):
    from kromabridge.bank import Bank
    from kromabridge.preset import Beam, Preset
    from kromabridge.runtime import Runtime
    presets = tmp_path / "presets"
    presets.mkdir()
    a = Preset(name="A", beams=[Beam(index=1, incoming_note=60, note=60)])
    envoyes = []
    rt = Runtime(Bank([a]), send=envoyes.extend, presets_dir=presets)
    rt.set_synth_sound(7, actif=False)                  # garde le numero, n'envoie rien
    rt.select(0)
    assert not [m for m in envoyes if m[0] & 0xF0 == 0xC0]
    assert rt.state()["son_synthe"] == {"programme": 7, "actif": False}
    rt.set_synth_sound(7)                               # recoche : envoye
    assert envoyes[-1] == [0xC0, 6]
