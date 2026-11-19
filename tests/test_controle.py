"""Piste de controle depuis un sequenceur."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kromabridge.bank import Bank  # noqa: E402
from kromabridge.controle import piste_exemple  # noqa: E402
from kromabridge.midifile import analyser  # noqa: E402
from kromabridge.preset import Beam, Preset  # noqa: E402
from kromabridge.runtime import Runtime  # noqa: E402


def boitier(tmp_path):
    presets = tmp_path / "presets"
    presets.mkdir()
    a = Preset(name="Un", beams=[Beam(index=i, incoming_note=59 + i, note=60) for i in range(1, 6)], beam_count=5)
    b = Preset(name="Deux", beams=[Beam(index=i, incoming_note=59 + i, note=70) for i in range(1, 6)], beam_count=5)
    rt = Runtime(Bank([a, b]), presets_dir=presets)
    actions = []
    rt.harp_open = lambda: actions.append("ouvrir")
    rt.harp_close = lambda: actions.append("fermer")
    rt.set_parametres(controle_actif=True)
    return rt, actions


def test_commandes(tmp_path):
    rt, actions = boitier(tmp_path)
    assert rt.control_message([0xC0, 1]) is None          # canal 1 : ignore
    assert rt.control_message([0xCF, 1]).startswith("morceau 2")
    rt.control_wait()
    assert rt.state()["memoire"] == "Deux"
    rt.control_message([0x9F, 0, 100])
    rt.control_message([0x9F, 1, 100])
    rt.control_message([0x9F, 0, 0])                       # note off deguisee : rien
    rt.control_wait()
    assert actions == ["ouvrir", "fermer"]
    rt.control_message([0xCF, 9])                          # morceau inexistant : rien
    rt.new_song_from_parts("P", [{"nom": "A", "notes": [60, 62, 64, 65, 67]},
                                 {"nom": "B", "notes": [70, 72, 74, 75, 77]}])
    assert rt.control_message([0x9F, 13, 90]) == "partie B"
    rt.control_message([0x9F, 20, 90])                     # partie inexistante : rien
    rt.control_wait()
    assert rt.state()["partie"] == 1
    assert len(rt.control_log) == 4
    assert (tmp_path / "journal.txt").read_text(encoding="utf-8").count("commande :") == 4


def test_export():
    f = analyser(piste_exemple("Rendez-vous 3", 2, 3))
    p = f.pistes[0]
    assert p.nom == "Controle Harpe - Rendez-vous 3"
    assert [n for _, n, c in p.notes] == [0, 12, 13, 14, 1] and {c for _, _, c in p.notes} == {16}



def test_interrupteur(tmp_path):
    rt, actions = boitier(tmp_path)
    rt.set_parametres(controle_actif=False)
    assert rt.control_message([0x9F, 0, 100]) is None and actions == []
    rt.set_parametres(controle_actif=True)
    rt.control_message([0x9F, 0, 100])
    rt.control_wait()
    assert actions == ["ouvrir"]


def test_fermeture_pendant_ouverture_animee(tmp_path):
    import time
    from kromabridge.web import commander_harpe

    class Harpe:
        def __init__(self):
            self.envoyes = []
        def send(self, m): self.envoyes.append(list(m))
        def send_quick(self, m): self.envoyes.append(list(m))
        def open_harp(self): self.envoyes.append([0xFA])
        def close_harp(self): self.envoyes.append([0xFC])

    rt, _ = boitier(tmp_path)
    rt.bank.current.animation = {"active": True, "pause": 0.5, "duree": 1.0}
    rt.bank.current.harp_snapshot = {"largeur": 40}
    h = Harpe()
    commander_harpe(rt, h, None, True)
    time.sleep(0.1)
    commander_harpe(rt, h, None, False)            # fermeture pendant la pause
    time.sleep(0.8)
    assert [0xFC] in h.envoyes
    fin = h.envoyes[h.envoyes.index([0xFC]):]
    assert fin[-1][1] == 42                          # largeur remise apres la fermeture
    assert [0xFA] not in fin                         # l'ouverture ne reprend pas



def test_transition_entre_parties(tmp_path):
    import time
    from kromabridge.web import changer_partie

    class Harpe:
        def __init__(self):
            self.envoyes = []
        def send(self, m): self.envoyes.append(("send", list(m)))
        def send_quick(self, m): self.envoyes.append(("quick", list(m)))

    rt, _ = boitier(tmp_path)
    rt.new_song_from_parts("P", [{"nom": "A", "notes": [60, 62, 64, 65, 67]},
                                 {"nom": "B", "notes": [70, 72, 74, 75, 77, 79]}])
    rt.bank.current.harp_snapshot["largeur"] = 30
    rt.set_animation(False, 2, 3, transition=True, trans_duree=0.15)
    h = Harpe()
    rt.harp_send = lambda msgs: [h.send(m) for m in msgs]
    changer_partie(rt, h, None, 1, pas_s=0.01)
    time.sleep(0.6)
    assert rt.state()["partie"] == 1
    largeurs = [m[2] for genre, m in h.envoyes if m[:2] == [0xB0, 42]]
    assert largeurs[0] < largeurs[-1] and min(largeurs) == 0   # resserre puis reecarte
    cc45 = [i for i, (g, m) in enumerate(h.envoyes) if m[:2] == [0xB0, 45]]
    zero = [i for i, (g, m) in enumerate(h.envoyes) if m == [0xB0, 42, 0]]
    assert cc45 and zero and zero[0] < cc45[0]                # nombre de rayons change une fois serre
    rt.set_animation(False, 2, 3, transition=False)
    h.envoyes.clear()
    changer_partie(rt, h, None, 0)
    assert rt.state()["partie"] == 0 and not [m for g, m in h.envoyes if m[:2] == [0xB0, 42]]



def test_piste_calee_sur_le_fichier():
    import struct
    from kromabridge.controle import piste_commandes
    # fichier a 480 par noire, en 3/4, tempo 100
    evs = (b"\x00\xff\x58\x04\x03\x02\x18\x08" b"\x00\xff\x51\x03\x09\x27\xc0"
           b"\x00\x90\x3c\x64" b"\x8b\x20\x80\x3c\x00" b"\x00\xff\x2f\x00")
    src = analyser(b"MThd" + struct.pack(">IHHH", 6, 1, 1, 480) + b"MTrk" + struct.pack(">I", len(evs)) + evs)
    assert src.position(1) == 0 and src.position(2) == 1440 and src.position(3, 2) == 2880 + 480
    data = piste_commandes(src, [{"type": "morceau", "mesure": 1, "temps": 1},
                                 {"type": "ouvrir", "mesure": 1, "temps": 2},
                                 {"type": "partie", "partie": 1, "mesure": 3, "temps": 1},
                                 {"type": "fermer", "mesure": 5, "temps": 1}], 4, "RDV3")
    f = analyser(data)
    p = f.pistes[0]
    assert f.division == 480 and f.tempos == [(0, 600000)] and f.signatures == [(0, 3, 4)]
    assert [(t, n) for t, n, c in p.notes] == [(480, 0), (2880, 13), (5760, 1)]



def test_commandes_dans_l_ordre_sans_chevauchement(tmp_path):
    import time
    rt, _ = boitier(tmp_path)
    journal = []
    def ouvrir():
        journal.append("debut ouverture"); time.sleep(0.2); journal.append("fin ouverture")
    rt.harp_open = ouvrir
    rt.new_song_from_parts("P", [{"nom": "A", "notes": [60, 62, 64, 65, 67]},
                                 {"nom": "B", "notes": [70, 72, 74, 75, 77]}])
    rt.part_switcher = lambda i, attendre=False: (journal.append(f"partie {i}"), rt.select_part(i))
    rt.control_message([0x9F, 0, 100])
    rt.control_message([0x9F, 13, 100])
    rt.control_wait()
    assert journal == ["debut ouverture", "fin ouverture", "partie 1"]


def test_chargement_remet_la_partie_a(tmp_path):
    rt, _ = boitier(tmp_path)
    rt.new_song_from_parts("P", [{"nom": "A", "notes": [60, 62, 64, 65, 67]},
                                 {"nom": "B", "notes": [70, 72, 74, 75, 77, 79]}])
    i = rt.state()["index"]
    rt.select_part(1)
    rt.select(0)
    rt.select(i)
    e = rt.state()
    assert e["partie"] == 0 and [r["note"] for r in e["rayons"]] == [60, 62, 64, 65, 67]


def test_harpe_injoignable_ne_fige_pas():
    import io
    import time
    from kromabridge.harp_rtmidi import ProcHarpControl
    from kromabridge.midi_io import MidiError

    class Muet:                       # un sous-programme qui ne repond jamais
        def __init__(self):
            self.stdin = io.StringIO()
            self.stdout = iter(())     # rien a lire : aucune reponse
            self.tue = False
        def poll(self): return None
        def kill(self): self.tue = True

    muets = []
    h = ProcHarpControl(delai=0.2, lancer=lambda: muets.append(Muet()) or muets[-1])
    erreurs = []
    h.on_erreur = erreurs.append
    t0 = time.monotonic()
    try:
        h.send([0xFA])
        assert False
    except MidiError:
        pass
    assert time.monotonic() - t0 < 1.0 and h.injoignable and muets[0].tue and erreurs



def test_piste_decalee_de_deux_mesures():
    import struct
    from kromabridge.controle import piste_commandes
    evs = (b"\x00\xff\x58\x04\x04\x02\x18\x08" b"\x00\xff\x51\x03\x07\xa1\x20"
           b"\x00\x90\x3c\x64" b"\x8f\x00\x80\x3c\x00" b"\x00\xff\x2f\x00")
    src = analyser(b"MThd" + struct.pack(">IHHH", 6, 1, 1, 480) + b"MTrk" + struct.pack(">I", len(evs)) + evs)
    data = piste_commandes(src, [{"type": "morceau", "mesure": 1, "temps": 1},
                                 {"type": "ouvrir", "mesure": 2, "temps": 1},
                                 {"type": "partie", "partie": 1, "mesure": 10, "temps": 1}], 0, "X", decalage=2)
    f = analyser(data)
    notes = [(t, n) for t, n, c in f.pistes[0].notes]
    # mesure 2 = 1920 ; mesure 10 du projet = mesure 8 du fichier = 2 x 1920 + 7 x 1920
    assert notes == [(1920, 0), (9 * 1920, 13)]



def test_temps_des_mesures_et_mesure_a_2_4():
    import struct
    from kromabridge.controle import piste_commandes
    # 4/4, puis 2/4 a la mesure 2, puis 4/4 a la mesure 3
    evs = (b"\x00\xff\x58\x04\x04\x02\x18\x08"
           b"\x00\x90\x3c\x64"
           b"\x8f\x00\xff\x58\x04\x02\x02\x18\x08"      # 1920 : 2/4
           b"\x87\x40\xff\x58\x04\x04\x02\x18\x08"      # 2880 : 4/4
           b"\x8f\x00\x80\x3c\x00" b"\x00\xff\x2f\x00")
    src = analyser(b"MThd" + struct.pack(">IHHH", 6, 1, 1, 480) + b"MTrk" + struct.pack(">I", len(evs)) + evs)
    assert src.temps_mesures()[:3] == [4, 2, 4]
    assert len(src.temps_mesures()) == len(src.mesures()) - 1
    # mesure 2 (a 2/4), temps 2 : 1920 + 480 ; mesure 3 temps 1 : 2880
    data = piste_commandes(src, [{"type": "ouvrir", "mesure": 2, "temps": 2},
                                 {"type": "fermer", "mesure": 3, "temps": 1}], 0, "X")
    notes = [(t, n) for t, n, c in analyser(data).pistes[0].notes]
    assert notes == [(2400, 0), (2880, 1)]


def test_morceau_deja_charge(tmp_path):
    """Cubase renvoie le changement de programme a chaque depart de lecture :
    pas de rechargement (ni de reglages renvoyes a la harpe), juste retour a A."""
    rt, actions = boitier(tmp_path)
    envois = []
    rt.harp_send = lambda m: envois.append(m)
    rt.harp_applied = lambda v: None
    rt.control_message([0xCF, 1]); rt.control_wait()
    assert len(envois) == 1
    rt.control_message([0xCF, 1]); rt.control_wait()
    assert rt.control_log[0].endswith("(déjà chargé)")
    assert len(envois) == 1
    rt.new_song_from_parts("P", [{"nom": "A", "notes": [60, 62, 64, 65, 67]},
                                 {"nom": "B", "notes": [70, 72, 74, 75, 77]}])
    rt.control_message([0x9F, 13, 90]); rt.control_wait()
    assert rt.state()["partie"] == 1
    rt.control_message([0xCF, rt.bank.index]); rt.control_wait()
    assert rt.state()["partie"] == 0



def test_entree_du_controle(tmp_path):
    rt, _ = boitier(tmp_path)
    assert rt.parametres["controle_entree"] == ""
    rt.set_parametres(controle_entree="  KeyLab mkII 61  ")
    assert rt.parametres["controle_entree"] == "KeyLab mkII 61"
    rt.set_parametres(controle_entree="")
    assert rt.parametres["controle_entree"] == ""
