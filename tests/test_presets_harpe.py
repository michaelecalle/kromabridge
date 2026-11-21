"""Presets de la harpe <-> morceaux de Kromabridge (avec une fausse harpe)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kromabridge import presets_harpe as ph  # noqa: E402
from kromabridge.bank import Bank  # noqa: E402
from kromabridge.preset import Beam, Preset  # noqa: E402
from kromabridge.runtime import Runtime  # noqa: E402
from tests.test_harp_service import make  # noqa: E402


def test_decalages():
    assert ph.decalage({"transposition": 2, "octave": 4}) == 14
    assert ph.vers_transpo_octave(14) == {"transposition": 2, "octave": 4}
    assert ph.vers_transpo_octave(-5) == {"transposition": 7, "octave": 2}
    assert ph.vers_transpo_octave(-48) is None
    chromatique = [Beam(index=i, incoming_note=59 + i, note=61 + i) for i in range(1, 6)]
    assert ph.decalage_chromatique(chromatique) == 2
    libre = [Beam(index=1, incoming_note=60, note=60), Beam(index=2, incoming_note=61, note=64)]
    assert ph.decalage_chromatique(libre) is None


def test_copie(tmp_path):
    c = ph.Copie(tmp_path / "presets_harpe.json")
    c.mettre(3, {"largeur": 50})
    c.garder_ancien(3, {"largeur": 20})
    d = ph.Copie(tmp_path / "presets_harpe.json")          # relue depuis le fichier
    assert d.get(3)["valeurs"] == {"largeur": 50}
    assert d.donnees["sauvegardes"][0]["valeurs"] == {"largeur": 20}
    assert d.get(4) is None and "3" in d.resume()


def test_lire_et_enregistrer_un_preset():
    harpe, service = make()
    contenu = service.lire_preset(5)
    assert [0xF0, 0x7B, 0x01, 0x74, 5, 0xF7] in harpe.envois                 # rappel du preset 5
    assert contenu["gain"] == 75
    harpe.envois.clear()
    service.enregistrer_preset(5, {"largeur": 40})
    assert [0xF0, 0x7B, 0x01, 0x72, 5, 0xF7] in harpe.envois                 # enregistrement
    i = harpe.envois.index([0xF0, 0x7B, 0x01, 0x72, 5, 0xF7])
    assert any(m[:2] == [0xB0, 42] for m in harpe.envois[:i])                # la largeur avant l'enregistrement


def morceaux(tmp_path):
    presets = tmp_path / "presets"
    presets.mkdir()
    a = Preset(name="Un", beams=[Beam(index=i, incoming_note=59 + i, note=61 + i) for i in range(1, 8)], beam_count=7)
    rt = Runtime(Bank([a]), presets_dir=presets)
    return rt


def test_morceau_vers_preset_et_preset_vers_morceau(tmp_path):
    rt = morceaux(tmp_path)
    cible, notes = rt.cible_preset_harpe({"largeur": 30, "transposition": 0, "octave": 3})
    assert notes and cible["transposition"] == 2 and cible["octave"] == 3   # gamme decalee de 2 demi-tons
    assert cible["rayons"] == 2 and cible["largeur"] == 30                  # 7 rayons
    valeurs = {"rayons": 5, "largeur": 60, "transposition": 3, "octave": 4}  # 10 rayons, +15 demi-tons
    p = rt.song_from_harp_preset(12, valeurs, nouveau=True)
    assert p.name == "Preset 12" and len(p.beams) == 10
    assert all(b.note == b.incoming_note + 15 for b in p.beams)
    assert p.harp_snapshot["largeur"] == 60 and p.harp_snapshot["transposition"] == 0   # transposition dans Kromabridge
    rt.song_from_harp_preset(12, valeurs, nouveau=True)
    assert "Preset 12 (2)" in rt.state()["memoires"]
    rt.select(0)
    avant = [b.note for b in rt.bank.current.beams]
    rt.song_from_harp_preset(7, {"largeur": 10}, nouveau=False)              # morceau en cours : reglages seulement
    assert [b.note for b in rt.bank.current.beams] == avant
    assert rt.bank.current.harp_snapshot["largeur"] == 10
