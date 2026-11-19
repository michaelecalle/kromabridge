"""Import d'un fichier MIDI."""

import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kromabridge.bank import Bank  # noqa: E402
from kromabridge.midifile import MidiFileError, lire  # noqa: E402
from kromabridge.preset import Beam, Preset  # noqa: E402
from kromabridge.runtime import Runtime  # noqa: E402


def piste(evenements: bytes) -> bytes:
    return b"MTrk" + struct.pack(">I", len(evenements)) + evenements


def fichier(*pistes: bytes) -> bytes:
    return b"MThd" + struct.pack(">IHHH", 6, 1, len(pistes), 480) + b"".join(pistes)


NOM = b"\x00\xff\x03\x05Harpe"
FIN = b"\x00\xff\x2f\x00"
# notes 48, 53 (statut courant), un note-on a velocite 0 (= note off), 48 rejouee
NOTES = (b"\x00\x90\x30\x64" b"\x83\x60\x35\x64" b"\x00\x30\x00" b"\x10\x90\x30\x50"
         b"\x00\xc0\x05" b"\x00\xf0\x02\x01\xf7")


def test_lecture():
    p = lire(fichier(piste(b"\x00\xff\x03\x04Info" + FIN), piste(NOM + NOTES + FIN)))
    assert [x.nom for x in p] == ["Info", "Harpe"]
    r = p[1].resume()
    assert r["nombre"] == 3 and r["distinctes"] == [48, 53] and r["canaux"] == [1]
    assert p[1].notes[1][0] == 480
    with pytest.raises(MidiFileError):
        lire(b"RIFF....")


def test_creation_depuis_notes(tmp_path):
    presets = tmp_path / "presets"
    presets.mkdir()
    base = Preset(name="Un", beams=[Beam(index=i, incoming_note=59 + i, note=60) for i in range(1, 10)],
                  beam_count=9, out_channel=2, harp_snapshot={"rayons": 4, "gain": 37})
    rt = Runtime(Bank([base]), presets_dir=presets)
    rt.new_song_from_notes("Import", [65, 60, 62, 64, 60, 67, 69])
    p = rt.bank.current
    assert p.name == "Import" and p.out_channel == 2
    assert [b.note for b in p.beams] == [60, 62, 64, 65, 67, 69]
    assert [b.incoming_note for b in p.beams] == list(range(60, 66))
    assert p.beam_count == 6 and p.harp_snapshot == {"rayons": 1, "gain": 37}
    assert rt.bank.presets[0].harp_snapshot["rayons"] == 4      # original intact
    rt.new_song_from_notes("Court", [50, 52])
    assert [b.note for b in rt.bank.current.beams] == [50, 52, 52, 52, 52]
    with pytest.raises(ValueError):
        rt.new_song_from_notes("Trop", list(range(40, 56)))


def test_vrai_fichier_rendez_vous():
    f = Path(r"C:\Users\micha\Downloads\RendezVous2\RendezVous2.mid")
    if not f.exists():
        pytest.skip("fichier d'exemple absent")
    pistes = lire(f.read_bytes())
    assert pistes[9].resume()["distinctes"] == [24, 29, 31, 32, 34, 35, 36, 38, 39]


def test_ecoute():
    import time
    from kromabridge.midifile import analyser
    from kromabridge.player import Lecteur
    # 2 notes de 0,05 s a 120 bpm / 480 par noire (0,05 s = 48 temps)
    ev = (b"\x00\x90\x3c\x64" b"\x30\x80\x3c\x00" b"\x00\x90\x3e\x64" b"\x30\x80\x3e\x00")
    f = analyser(fichier(piste(NOM + ev + FIN)))
    assert f.pistes[0].segments == [(0, 48, 60, 1), (48, 96, 62, 1)]
    assert abs(f.duree() - 0.1) < 1e-6
    envoyes = []
    lec = Lecteur(envoyes.extend)
    lec.jouer(f, [1], 0.0, canal=3)
    time.sleep(0.3)
    assert envoyes == [[0x92, 60, 100], [0x82, 60, 0], [0x92, 62, 100], [0x82, 62, 0]]
    assert lec.position() is None
    envoyes.clear()
    lec.jouer(f, [1], 0.06, canal=1)            # reprise au milieu de la 2e note
    time.sleep(0.2)
    assert envoyes == [[0x90, 62, 100], [0x80, 62, 0]]
    envoyes.clear()
    long = analyser(fichier(piste(NOM + b"\x00\x90\x3c\x64" b"\x8f\x00\x80\x3c\x00" + FIN)))
    lec.jouer(long, [1], 0.0, canal=1)
    time.sleep(0.05)
    lec.arreter()                               # arret : la note tenue est coupee
    assert envoyes == [[0x90, 60, 100], [0x80, 60, 0]]


def test_ecoute_partie():
    import time
    from kromabridge.midifile import analyser
    from kromabridge.player import Lecteur
    ev = (b"\x00\x90\x3c\x64" b"\x30\x80\x3c\x00" b"\x00\x90\x3e\x64" b"\x30\x80\x3e\x00")
    f = analyser(fichier(piste(NOM + ev + FIN)))
    envoyes = []
    lec = Lecteur(envoyes.extend)
    lec.jouer(f, [1], 0.0, canal=1, fin=0.04)   # seulement la 1re note, coupee a 0,04 s
    time.sleep(0.2)
    assert envoyes == [[0x90, 60, 100], [0x80, 60, 0]]


def test_morceau_en_parties(tmp_path):
    import json
    presets = tmp_path / "presets"
    presets.mkdir()
    base = Preset(name="Un", beams=[Beam(index=i, incoming_note=59 + i, note=60) for i in range(1, 10)],
                  beam_count=9, harp_snapshot={"rayons": 4})
    rt = Runtime(Bank([base]), presets_dir=presets)
    envoyes = []
    rt.harp_send = envoyes.extend
    rt.new_song_from_parts("RDV3", [{"nom": "A", "notes": [41, 48, 53, 60, 65, 72, 77, 84]},
                                   {"nom": "B", "notes": [48, 53, 55, 56, 58, 60]}])
    e = rt.state()
    assert e["memoire"] == "RDV3" and e["parties"] == ["A", "B"] and e["partie"] == 0
    assert [r["note"] for r in e["rayons"]] == [41, 48, 53, 60, 65, 72, 77, 84]
    envoyes.clear()
    rt.select_part(1)
    e = rt.state()
    assert e["partie"] == 1 and [r["note"] for r in e["rayons"]] == [48, 53, 55, 56, 58, 60]
    assert envoyes == [[0xB0, 45, 13]]                    # 6 rayons
    rt.set_beam_note(1, 47)                               # modifier la partie B
    rt.select_part(0)
    rt.select_part(1)
    assert rt.bank.current.beams[0].note == 47
    data = json.loads((presets / "RDV3.json").read_text(encoding="utf-8"))
    assert [p["nom"] for p in data["parties"]] == ["A", "B"]
    assert data["parties"][1]["rayons"][0]["note"] == 47
    recharge = Preset.load(presets / "RDV3.json")
    assert recharge.part_index == 1 and recharge.beams[0].note == 47
    assert rt.handle([0x90, 60, 100]) == [[0x90, 47, 100]]



def test_import_memorise_et_mis_a_jour(tmp_path):
    from kromabridge.midifile import analyser
    presets = tmp_path / "presets"
    presets.mkdir()
    base = Preset(name="Un", beams=[Beam(index=i, incoming_note=59 + i, note=60) for i in range(1, 10)],
                  beam_count=9)
    rt = Runtime(Bank([base]), presets_dir=presets)
    brut = fichier(piste(NOM + NOTES + FIN))
    rt.midi, rt.midi_brut = analyser(brut), brut
    rt.new_song_from_parts("Morceau", [{"nom": "A", "notes": [60, 62, 64, 65, 67]},
                                       {"nom": "B", "notes": [70, 72, 74, 75, 77]}])
    info = {"pistes": [1], "parties": [{"nom": "A", "deb": 1, "fin": 4}], "debut": 3,
            "commandes": [{"type": "ouvrir", "mesure": 2, "temps": 1}]}
    rt.save_import("Morceau", info)
    assert (presets / "imports" / "Morceau.mid").read_bytes() == brut
    rt2 = Runtime(Bank.from_directory(presets), presets_dir=presets)
    f, lu = rt2.load_import("Morceau")
    assert lu["debut"] == 3 and lu["fichier"] == "Morceau.mid" and f.pistes[0].nom == "Harpe"
    rt2.update_song("Morceau", parties=[{"nom": "A", "notes": [50, 52, 53, 55, 57]},
                                       {"nom": "B", "notes": [60, 62, 64, 65, 67, 69]},
                                       {"nom": "C", "notes": [70, 72, 74, 75, 77]}])
    p = rt2._preset_nomme("Morceau")
    assert [x["nom"] for x in p.parts] == ["A", "B", "C"] and [b.note for b in p.beams] == [50, 52, 53, 55, 57]
    assert p.import_info["debut"] == 3                     # les infos d'import sont gardees


def test_animation_choisie_dans_l_import(tmp_path):
    presets = tmp_path / "presets"
    presets.mkdir()
    base = Preset(name="Un", beams=[Beam(index=i, incoming_note=59 + i, note=60) for i in range(1, 6)],
                  beam_count=5, animation={"active": False, "pause": 4.0, "duree": 2.0})
    rt = Runtime(Bank([base]), presets_dir=presets)
    rt.set_song_animation("Un", active=True, transition=True)
    a = Preset.load(presets / "Un.json").animation
    assert a["active"] and a["transition"] and a["pause"] == 4.0 and a["trans_duree"] == 1.0


def test_durees_choisies_dans_l_import(tmp_path):
    presets = tmp_path / "presets"
    presets.mkdir()
    base = Preset(name="Un", beams=[Beam(index=i, incoming_note=59 + i, note=60) for i in range(1, 6)], beam_count=5)
    rt = Runtime(Bank([base]), presets_dir=presets)
    rt.set_song_animation("Un", True, True, pause=1.5, duree=4, trans_duree=0.5)
    a = Preset.load(presets / "Un.json").animation
    assert (a["pause"], a["duree"], a["trans_duree"]) == (1.5, 4.0, 0.5)



def test_mise_a_jour_garde_les_retouches(tmp_path):
    presets = tmp_path / "presets"
    presets.mkdir()
    base = Preset(name="Un", beams=[Beam(index=i, incoming_note=59 + i, note=60) for i in range(1, 10)],
                  beam_count=9)
    rt = Runtime(Bank([base]), presets_dir=presets)
    rt.new_song_from_parts("M", [{"nom": "A", "notes": [41, 60, 65, 72, 77, 84]},
                                 {"nom": "B", "notes": [48, 53, 55, 56, 58]}])
    rt.set_beam_count(7)                       # partie A : un rayon de plus...
    rt.set_beam_note(7, 29)                    # ... avec un fa1 ajoute a la main
    rt.sort_beams()
    avant = [b.note for b in rt.bank.current.beams]
    assert avant == [29, 41, 60, 65, 72, 77, 84]
    gardees = rt.update_song("M", parties=[{"nom": "A", "notes": [41, 60, 65, 72, 77, 84]},
                                           {"nom": "B", "notes": [48, 53, 55, 56, 58, 60]}])
    p = rt._preset_nomme("M")
    assert gardees == ["A"]
    assert [b.note for b in p.beams] == avant                       # A garde le fa1
    assert [b["note"] for b in p.parts[1]["rayons"]] == [48, 53, 55, 56, 58, 60]   # B refaite
    # si l'import change la partie A, elle est refaite
    assert rt.update_song("M", parties=[{"nom": "A", "notes": [41, 60, 65]},
                                        {"nom": "B", "notes": [48, 53, 55, 56, 58, 60]}]) == ["B"]
    assert [b.note for b in rt._preset_nomme("M").beams] == [41, 60, 65, 65, 65]


def test_mise_a_jour_morceau_ancien(tmp_path):
    """Morceau cree avant la memorisation des notes d'import : une partie dont
    l'import n'apporte aucune nouvelle note est gardee."""
    presets = tmp_path / "presets"
    presets.mkdir()
    rayons = lambda notes: [Beam(index=k, incoming_note=59 + k, note=n).to_dict() for k, n in enumerate(notes, 1)]
    p = Preset(name="Vieux", beams=[Beam.from_dict(b) for b in rayons([29, 41, 60, 65, 72, 77, 84])], beam_count=7,
               parts=[{"nom": "A", "rayons": rayons([29, 41, 60, 65, 72, 77, 84])},
                      {"nom": "B", "rayons": rayons([48, 53, 55, 56, 58])}])
    rt = Runtime(Bank([p]), presets_dir=presets)
    assert rt.update_song("Vieux", parties=[{"nom": "A", "notes": [41, 60, 65, 72, 77, 84]},
                                            {"nom": "B", "notes": [48, 53, 55, 56, 58, 60]}]) == ["A"]
    assert [b.note for b in rt.bank.current.beams][0] == 29
