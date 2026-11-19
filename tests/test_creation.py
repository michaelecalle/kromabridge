"""Creation de morceaux : nouveau morceau, nommage des notes."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kromabridge.bank import Bank  # noqa: E402
from kromabridge.preset import Beam, Preset  # noqa: E402
from kromabridge.runtime import Runtime  # noqa: E402
from kromabridge.web import note_name  # noqa: E402


def boitier(tmp_path):
    presets = tmp_path / "presets"
    presets.mkdir()
    p = Preset(name="Un", beams=[Beam(index=1, incoming_note=60, note=48),
                                 Beam(index=2, incoming_note=61, note=53)],
               beam_count=5, out_channel=3, harp_snapshot={"rayons": 0})
    return Runtime(Bank([p]), presets_dir=presets), presets


def test_nouveau_morceau(tmp_path):
    rt, presets = boitier(tmp_path)
    rt.new_song("  Mon titre ", 0)
    etat = rt.state()
    assert etat["memoire"] == "Mon titre"
    assert etat["memoires"] == ["Un", "Mon titre"]
    assert [r["note_entrante"] for r in etat["rayons"]] == [60, 61]
    assert [r["note"] for r in etat["rayons"]] == [48, 53]
    # la copie est independante de l'original
    rt.set_beam_note(1, 50)
    assert rt.bank.presets[0].beams[0].note == 48
    assert etat["canal_sortie"] == 3
    fichier = json.loads((presets / "Mon titre.json").read_text(encoding="utf-8"))
    assert fichier["harpe"] == {"rayons": 0}
    with pytest.raises(ValueError):
        rt.new_song("Un", 0)
    with pytest.raises(ValueError):
        rt.new_song("   ", 0)


def test_nommage(tmp_path):
    rt, presets = boitier(tmp_path)
    assert rt.parametres["langue"] == "fr"
    rt.set_parametres("en")
    assert json.loads((tmp_path / "parametres.json").read_text())["langue"] == "en"
    rt2, _ = Runtime(rt.bank, presets_dir=presets), None
    assert rt2.parametres["langue"] == "en"
    with pytest.raises(ValueError):
        rt.set_parametres("de")
    assert note_name(60) == "do4"
    assert note_name(61, "en") == "C#4"
    assert note_name(48, "fr") == "do3"


def test_nombre_de_rayons(tmp_path):
    rt, presets = boitier(tmp_path)
    envoyes = []
    rt.harp_send = envoyes.extend
    rt.set_beam_count(7)
    b = rt.bank.current.beams
    assert [x.incoming_note for x in b] == list(range(60, 67))
    assert [x.note for x in b][:2] == [48, 53]
    assert [x.note for x in b][2:] == list(range(62, 67))
    assert rt.bank.current.harp_snapshot["rayons"] == 2
    assert envoyes == [[0xB0, 45, 26]]
    rt.set_beam_count(5)
    assert len(rt.bank.current.beams) == 5
    assert rt.handle([0x90, 61, 100]) == [[0x92, 53, 100]]
    with pytest.raises(ValueError):
        rt.set_beam_count(4)


def test_morceau_vierge(tmp_path):
    rt, presets = boitier(tmp_path)
    rt.new_song("Vierge", None, 7)
    p = rt.bank.current
    assert p.name == "Vierge" and p.beam_count == 7 and p.out_channel == 1
    assert [b.note for b in p.beams] == list(range(60, 67))
    assert p.harp_snapshot == {}
    with pytest.raises(ValueError):
        rt.new_song("Autre", None, 20)


def test_ecoute_note(tmp_path):
    import time
    rt, presets = boitier(tmp_path)
    envoyes = []
    rt.send = envoyes.extend
    rt.preview(67, duree=0.05)
    assert envoyes == [[0x92, 67, 100]]
    time.sleep(0.2)
    assert envoyes[-1] == [0x82, 67, 0]


def test_supprimer(tmp_path):
    rt, presets = boitier(tmp_path)
    rt.new_song("Deux", 0)
    rt.new_song("Trois", 0)
    assert rt.state()["memoire"] == "Trois"
    rt.delete_song(1)
    assert rt.state()["memoires"] == ["Un", "Trois"]
    assert rt.state()["memoire"] == "Trois"
    assert not (presets / "Deux.json").exists()
    assert (tmp_path / "corbeille" / "Deux.json").exists()
    rt.delete_song(1)
    assert rt.state()["memoire"] == "Un"
    with pytest.raises(ValueError):
        rt.delete_song(0)


def test_harpe_modifiee(tmp_path):
    rt, presets = boitier(tmp_path)          # instantane : {"rayons": 0}
    actuels = {"rayons": 0, "gain": 30}
    rt.harp_values = lambda: actuels
    rt.harp_send = lambda m: None
    rt.harp_applied = actuels.update
    assert rt.state()["harpe_modifiee"] == []
    actuels["rayons"] = 4                    # change depuis la page Reglages
    assert rt.state()["harpe_modifiee"] == ["rayons"]
    rt.select(0)                             # recharger le morceau remet la harpe
    assert rt.state()["harpe_modifiee"] == []


def test_ranger_les_rayons(tmp_path):
    rt, presets = boitier(tmp_path)
    rt.set_beam_note(1, 70)                 # rayons : 70, 53
    rt.sort_beams()
    assert [b.note for b in rt.bank.current.beams] == [53, 70]



def test_echange_de_morceaux(tmp_path):
    # appareil A : un morceau avec son import MIDI
    (tmp_path / "a").mkdir(); (tmp_path / "b").mkdir()
    rt, presets = boitier(tmp_path / "a")
    rt.new_song("Avec import", 0)
    (presets / "imports").mkdir()
    (presets / "imports" / "Avec import.mid").write_bytes(b"MThd-test")
    rt.bank.current.import_info = {"fichier": "Avec import.mid", "debut": 3}
    paquet = json.loads(json.dumps(rt.export_songs()))          # comme un fichier
    assert [m["morceau"]["nom"] for m in paquet["morceaux"]] == ["Un", "Avec import"]
    assert "import_mid" in paquet["morceaux"][1]
    # appareil B : "Un" existe deja
    rt2, presets2 = boitier(tmp_path / "b")
    r = rt2.import_songs(paquet)
    assert r == {"ajoutes": ["Avec import"], "remplaces": [], "renommes": [["Un", "Un (2)"]]}
    assert rt2.state()["memoires"] == ["Un", "Un (2)", "Avec import"]
    assert (presets2 / "imports" / "Avec import.mid").read_bytes() == b"MThd-test"
    assert rt2._preset_nomme("Avec import").import_info == {"fichier": "Avec import.mid", "debut": 3}
    # remplacer : l'ancien part dans la corbeille
    r = rt2.import_songs(paquet, remplacer=True)
    assert r["remplaces"] == ["Un", "Avec import"]
    assert (tmp_path / "b" / "corbeille" / "Avec import.json").exists()
    assert len(rt2.state()["memoires"]) == 3
    with pytest.raises(ValueError):
        rt2.import_songs({"format": "autre"})



def test_numerotation_du_synthe(tmp_path):
    rt, presets = boitier(tmp_path)
    envoyes = []
    rt.send = envoyes.extend
    rt.set_synth_sound(38, numerotation="groupes8")      # "56" sur un synthe a groupes de 8
    assert rt.state()["son_synthe"] == {"programme": 38, "numerotation": "groupes8"}
    assert envoyes[-1] == [0xC2, 37]
    assert json.loads((presets / "Un.json").read_text(encoding="utf-8"))["son_synthe"]["numerotation"] == "groupes8"
    rt.set_synth_sound(5, numerotation="1-128")          # valeur par defaut : pas enregistree
    assert rt.state()["son_synthe"] == {"programme": 5}
    with pytest.raises(ValueError):
        rt.set_synth_sound(5, numerotation="autre")


def test_listes_et_ordre(tmp_path):
    rt, presets = boitier(tmp_path)
    rt.new_song("Deux", 0)
    rt.new_song("Trois", 0)
    assert rt.state()["memoires"] == ["Un", "Deux", "Trois"]
    rt.bank.presets[2].import_info = {"commandes": [{"type": "ouvrir", "mesure": 1, "temps": 1}]}
    # ordre de la liste par defaut : numeros changes, avec ou sans piste de controle
    changes = rt.set_default_order(["Trois", "Un", "Deux"])
    assert {c["nom"]: (c["avant"], c["apres"], c["piste"]) for c in changes} == {
        "Trois": (3, 1, True), "Un": (1, 2, False), "Deux": (2, 3, False)}
    assert rt.state()["memoire"] == "Trois"             # le morceau affiche suit
    with pytest.raises(ValueError):
        rt.set_default_order(["Un", "Deux"])
    # listes de concert
    rt.list_create("Lyon")
    rt.list_set_songs("Lyon", ["Deux", "Un"])
    rt.list_activate("Lyon")
    e = rt.state()
    assert e["listes"] == ["Lyon"] and e["liste_active"] == "Lyon"
    assert e["liste_morceaux"] == [2, 1]                 # Deux = n°3, Un = n°2
    rt.select(1)
    rt.rename("Un bis")                                  # la liste suit le nouveau nom
    assert rt.state()["listes_detail"]["Lyon"] == ["Deux", "Un bis"]
    # tout est garde au redemarrage
    rt2 = Runtime(Bank.from_directory(presets), presets_dir=presets)
    assert rt2.state()["memoires"] == ["Trois", "Un bis", "Deux"]
    assert rt2.state()["liste_active"] == "Lyon"
    # supprimer un morceau le retire des listes
    rt2.delete_song(2)
    assert rt2.state()["listes_detail"]["Lyon"] == ["Un bis"]
    rt2.list_rename("Lyon", "Paris")
    assert rt2.state()["liste_active"] == "Paris"
    rt2.list_delete("Paris")
    assert rt2.state()["liste_active"] is None and rt2.state()["liste_morceaux"] == [0, 1]


def test_corbeille(tmp_path):
    rt, presets = boitier(tmp_path)
    rt.new_song("Deux", 0)
    rt.new_song("Trois", 0)
    (presets / "imports").mkdir()
    (presets / "imports" / "Deux.mid").write_bytes(b"MThd")
    rt.bank.presets[1].import_info = {"fichier": "Deux.mid"}
    rt._save(rt.bank.presets[1])
    rt.delete_song(1)                                   # Deux -> corbeille
    assert [m["nom"] for m in rt.trash_list()] == ["Deux"]
    assert rt.trash_restore("Deux.json") == "Deux"
    assert rt.state()["memoires"] == ["Un", "Trois", "Deux"]   # a la fin : aucun numero ne bouge
    assert rt.trash_list() == []
    rt.new_song("Quatre", 0)
    rt.delete_song(3)
    rt.new_song("Quatre", 0)                            # meme nom repris entre-temps
    assert rt.trash_restore("Quatre.json") == "Quatre (2)"
    rt.delete_song(2)                                   # Deux a nouveau
    assert rt.trash_empty() == 1
    assert not (presets / "imports" / "Deux.mid").exists()      # son MIDI ne sert plus
    with pytest.raises(ValueError):
        rt.trash_restore("Deux.json")


def test_reglages_communs_et_memorisation(tmp_path):
    rt, presets = boitier(tmp_path)             # "Un" : instantane {"rayons": 0}
    rt.new_song("Deux", 0)
    actuels = {"rayons": 0, "largeur": 40, "gain": 60}
    rt.harp_values = lambda: dict(actuels)
    envoyes = []
    rt.harp_send = envoyes.extend
    rt.harp_applied = lambda v: None
    rt.select(0)
    rt.memorize_harp()                         # premier enregistrement : tout est lu
    rt.harp_capture = lambda: dict(actuels)
    # largeur commune a tous les morceaux
    rt.set_harp_common("largeur", True)
    assert rt.state()["harpe_communs"] == ["largeur"]
    actuels["largeur"] = 55                    # on arrive dans une autre salle
    assert rt.state()["harpe_modifiee"] == ["largeur"]
    assert rt.memorize_harp() == ["largeur"]
    assert rt.parametres["harpe_communs"] == {"largeur": 55}
    # le morceau "Deux" recoit aussi la largeur commune
    envoyes.clear()
    rt.select(1)
    from kromabridge.harp_settings import BY_KEY
    assert BY_KEY["largeur"].message(55) in envoyes
    rt.memorize_harp()                         # complete l'instantane de "Deux"
    assert rt.bank.presets[1].harp_snapshot["gain"] == 60
    # gain modifie : memorise dans tous les morceaux ; jamais le nombre de rayons
    actuels.update(gain=70, rayons=3)
    faits = rt.memorize_harp(tous=True)
    assert set(faits) == {"gain", "rayons"}
    assert all(p.harp_snapshot.get("gain") == 70 for p in rt.bank.presets)
    assert rt.bank.presets[0].harp_snapshot["rayons"] == 0      # l'autre morceau garde ses rayons
    with pytest.raises(ValueError):
        rt.set_harp_common("rayons", True)
    # garde au redemarrage
    assert Runtime(rt.bank, presets_dir=presets).parametres["harpe_communs"] == {"largeur": 55}


def test_panique_coupe_aussi_la_pedale(tmp_path):
    rt, presets = boitier(tmp_path)
    envoyes = []
    rt.send = envoyes.extend
    rt.panic_total()
    for canal in range(16):
        assert [0xB0 | canal, 64, 0] in envoyes and [0xB0 | canal, 120, 0] in envoyes



def test_choisir_un_rayon_a_l_ecran(tmp_path):
    rt, presets = boitier(tmp_path)
    rt.set_attribution(True)
    rt.choose_beam(2)                      # sans harpe : on touche la tuile
    assert rt.keyboard_note(67) == 2
    assert rt.bank.current.beams[1].note == 67



def test_annuler_le_changement_des_rayons(tmp_path):
    rt, presets = boitier(tmp_path)
    avant = [b.note for b in rt.bank.current.beams]
    rt.set_attribution(True)
    rt.choose_beam(1)
    rt.keyboard_note(40)
    rt.keyboard_note(41)
    assert rt.bank.current.beams[0].note == 41
    rt.set_attribution(False, annuler=True)
    assert [b.note for b in rt.bank.current.beams] == avant
    assert json.loads((presets / "Un.json").read_text(encoding="utf-8"))["rayons"][0]["note"] == avant[0]
    # garder : les changements restent
    rt.set_attribution(True); rt.choose_beam(1); rt.keyboard_note(50); rt.set_attribution(False)
    assert rt.bank.current.beams[0].note == 50



def test_morceau_gamme(tmp_path):
    presets = tmp_path / "presets"
    presets.mkdir()
    p = Preset(name="Un", beams=[Beam(index=1, incoming_note=60, note=48)], beam_count=5)
    rt = Runtime(Bank([p]), presets_dir=presets, gamme=True)
    e = rt.state()
    assert e["memoires"] == ["Un", "Gamme"] and e["index_gamme"] == 1
    rt.select(1)
    g = rt.bank.current
    assert g.gamme and [b.note for b in g.beams] == list(range(60, 69))
    assert rt.handle([0x90, 64, 100])[0][1] == 64            # la note de la harpe, telle quelle
    for action in (lambda: rt.set_beam_note(1, 50), lambda: rt.shift_all(1), lambda: rt.rename("X"),
                   lambda: rt.delete_song(1), lambda: rt.set_beam_chord(1, [64])):
        with pytest.raises(ValueError):
            action()
    rt.set_beam_count(12)                                   # le nombre de rayons se regle
    assert [b.note for b in rt.bank.current.beams] == list(range(60, 72))
    # au redemarrage : pas de deuxieme Gamme
    rt2 = Runtime(Bank.from_directory(presets), presets_dir=presets, gamme=True)
    assert rt2.state()["memoires"].count("Gamme") == 1



def test_son_par_partie(tmp_path):
    presets = tmp_path / "presets"
    presets.mkdir()
    p = Preset(name="Un", beams=[Beam(index=1, incoming_note=60, note=48)], beam_count=5)
    rt = Runtime(Bank([p, Preset(name="Deux", beams=[Beam(index=1, incoming_note=60, note=50)], beam_count=5)]),
                 presets_dir=presets)
    envois = []
    rt.send = lambda m: envois.extend(m)
    rt.new_song_from_parts("P", [{"nom": "A", "notes": [60, 62, 64, 65, 67]},
                                 {"nom": "B", "notes": [70, 72, 74, 75, 77]},
                                 {"nom": "C", "notes": [71, 72, 74, 75, 77]}])
    rt.set_synth_sound(11)                       # partie A
    rt.select_part(1); rt.set_synth_sound(12)    # partie B
    # partie C : rien (le son de B reste)
    envois.clear()
    rt.select_part(2)
    assert not [m for m in envois if m[0] & 0xF0 == 0xC0]
    rt.select_part(0)
    assert [m for m in envois if m[0] & 0xF0 == 0xC0][-1][1] == 10
    rt.select_part(1)
    assert [m for m in envois if m[0] & 0xF0 == 0xC0][-1][1] == 11
    # rechargement depuis le disque : chaque partie garde son son
    rt2 = Runtime(Bank.from_directory(presets), presets_dir=presets)
    q = next(x for x in rt2.bank.presets if x.name == "P")
    assert [x.get("son_synthe", {}).get("programme") for x in q.parts] == [11, 12, None]
    # au chargement du morceau : le son de la partie A
    rt2.select(rt2.bank.presets.index(q))
    assert rt2.bank.current.synth.get("programme") == 11


def test_son_ancien_format_devient_partie_a(tmp_path):
    from kromabridge.preset import Preset as P
    raw = {"nom": "X", "rayons": [], "son_synthe": {"programme": 5},
           "parties": [{"nom": "A", "rayons": []}, {"nom": "B", "rayons": []}], "partie_active": 0}
    x = P.from_dict(raw)
    assert x.parts[0]["son_synthe"] == {"programme": 5} and "son_synthe" not in x.parts[1]
    assert x.synth == {"programme": 5}



def test_clavier_joue_comme_la_harpe(tmp_path):
    presets = tmp_path / "presets"
    presets.mkdir()
    p = Preset(name="Un", beams=[Beam(index=1, incoming_note=60, note=48), Beam(index=2, incoming_note=61, note=50)],
               beam_count=5, out_channel=2)
    rt = Runtime(Bank([p]), presets_dir=presets)
    envois = []
    rt.send = lambda m: envois.extend(m)
    rt.keyboard_echo([0x90, 50, 90])
    assert envois == []                                  # case decochee : rien
    assert not rt.keyboard_active()
    rt.set_parametres(clavier_joue=True)
    assert rt.keyboard_active()
    rt.keyboard_echo([0x90, 50, 90])
    rt.keyboard_echo([0x80, 50, 0])
    assert envois == [[0x91, 50, 90], [0x81, 50, 0]]     # telle quelle, canal du morceau
    assert rt.state()["derniere_note"] == 61               # la case du rayon 2 s'allume
    rt.keyboard_echo([0x90, 72, 90])                       # note d'aucun rayon : elle joue quand meme
    assert envois[-1] == [0x91, 72, 90]
    # en apprentissage, le clavier garde son role
    envois.clear()
    rt.start_kb_learning()
    rt.keyboard_echo([0x90, 64, 90])
    assert envois == []



def test_supprimer_un_rayon(tmp_path):
    presets = tmp_path / "presets"
    presets.mkdir()
    p = Preset(name="Un", beams=[Beam(index=i, incoming_note=59 + i, note=40 + i) for i in range(1, 8)], beam_count=7)
    rt = Runtime(Bank([p]), presets_dir=presets)
    rt.set_beam_chord(5, [60])
    rt.delete_beam(3)
    b = rt.bank.current.beams
    assert [x.note for x in b] == [41, 42, 44, 45, 46, 47]          # le 3e (43) a disparu
    assert [x.index for x in b] == [1, 2, 3, 4, 5, 6] and [x.incoming_note for x in b] == list(range(60, 66))
    assert b[3].extra == [60]                                     # l'accord suit son rayon
    assert rt.bank.current.beam_count == 6
    rt.delete_beam(6)
    with pytest.raises(ValueError):
        rt.delete_beam(1)                                         # 5 rayons au minimum
    rt2 = Runtime(Bank.from_directory(presets), presets_dir=presets)
    assert [x.note for x in rt2.bank.current.beams] == [41, 42, 44, 45, 46]



def test_export_import_de_listes(tmp_path):
    def appareil(nom):
        presets = tmp_path / nom / "presets"
        presets.mkdir(parents=True)
        return presets
    pa = appareil("pc")
    a = Runtime(Bank([Preset(name=n, beams=[Beam(index=1, incoming_note=60, note=40 + i)], beam_count=5)
                      for i, n in enumerate(["Un", "Deux", "Trois", "Quatre"])]), presets_dir=pa, gamme=True)
    a.list_create("Concert"); a.list_set_songs("Concert", ["Trois", "Un", "Gamme"])
    a.list_create("Autre"); a.list_set_songs("Autre", ["Quatre"])
    paquet = a.export_lists(["Concert"])
    assert [m["morceau"]["nom"] for m in paquet["morceaux"]] == ["Trois", "Un"]       # ordre de la liste, sans Gamme
    assert paquet["listes"] == [{"nom": "Concert", "morceaux": ["Trois", "Un", "__gamme__"]}]
    # sur l'autre appareil : "Un" existe deja, garde les deux
    pb = appareil("boitier")
    b = Runtime(Bank([Preset(name="Un", beams=[Beam(index=1, incoming_note=60, note=70)], beam_count=5)]),
                presets_dir=pb, gamme=True)
    r = b.import_lists(paquet)
    assert r["listes_ajoutees"] == ["Concert"] and r["renommes"] == [["Un", "Un (2)"]]
    assert b.listes["listes"][0]["morceaux"] == ["Trois", "Un (2)", "Gamme"]
    assert b.state()["memoires"].count("Gamme") == 1
    # tout exporter, n'importer qu'une liste ; une liste du meme nom est remplacee
    tout = a.export_lists()
    assert {x["nom"] for x in tout["listes"]} == {"Concert", "Autre"}
    r = b.import_lists(tout, ["Autre"])
    assert r["listes_ajoutees"] == ["Autre"] and "Quatre" in b.state()["memoires"]
    r = b.import_lists(tout, ["Concert"], remplacer=True)
    assert r["listes_remplacees"] == ["Concert"]



def test_gerer_les_morceaux(tmp_path):
    presets = tmp_path / "presets"
    presets.mkdir()
    rt = Runtime(Bank([Preset(name=n, beams=[Beam(index=1, incoming_note=60, note=48)], beam_count=5)
                       for n in ["Un", "Deux", "Trois", "Quatre"]]), presets_dir=presets, gamme=True)
    rt.select(0)
    rt.rename("Deuxième", 1)                       # sans charger le morceau
    assert rt.state()["memoire"] == "Un" and "Deuxième" in rt.state()["memoires"]
    rt.delete_songs(["Deuxième", "Quatre", "Gamme inconnue"])
    assert rt.state()["memoires"] == ["Un", "Trois", "Gamme"]
    with pytest.raises(ValueError):
        rt.rename("X", 2)                          # Gamme protege



def test_parties_a_la_main(tmp_path):
    presets = tmp_path / "presets"
    presets.mkdir()
    p = Preset(name="Un", beams=[Beam(index=i, incoming_note=59 + i, note=40 + i) for i in range(1, 8)], beam_count=7)
    rt = Runtime(Bank([p]), presets_dir=presets)
    rt.set_synth_sound(11)
    rt.part_add()                                        # vierge -> B, et on y passe
    e = rt.state()
    assert e["parties"] == ["A", "B"] and e["partie"] == 1 and len(e["rayons"]) == 5
    assert e["son_synthe"] == {}
    rt.part_add(copie=0)                                 # copie de A -> C
    e = rt.state()
    assert e["parties"] == ["A", "B", "C"] and [r["note"] for r in e["rayons"]] == list(range(41, 48))
    assert e["son_synthe"].get("programme") == 11
    # commandes de piste de controle : elles suivent les parties
    rt.bank.current.import_info = {"commandes": [{"type": "partie", "partie": 2}, {"type": "partie", "partie": 1},
                                                  {"type": "ouvrir"}]}
    assert rt.part_move(2, 0) is True                    # C devant : C->A, A->B, B->C
    q = rt.bank.current
    assert [len(x["rayons"]) for x in q.parts] == [7, 7, 5] and q.part_index == 0
    assert [c.get("partie") for c in q.import_info["commandes"]] == [0, 2, None]
    rt.part_delete([2])                                  # la partie vierge disparait, sa commande aussi
    assert rt.state()["parties"] == ["A", "B"]
    assert [c.get("partie") for c in rt.bank.current.import_info["commandes"]] == [0, None]
    with pytest.raises(ValueError):
        rt.part_delete([0, 1])
    # rechargement : tout est garde
    rt2 = Runtime(Bank.from_directory(presets), presets_dir=presets)
    assert [x["nom"] for x in rt2.bank.current.parts] == ["A", "B"]



def test_nom_des_notes_separe_de_la_langue(tmp_path):
    presets = tmp_path / "presets"
    presets.mkdir()
    rt = Runtime(Bank([Preset(name="Un", beams=[Beam(index=1, incoming_note=60, note=48)], beam_count=5)]),
                 presets_dir=presets)
    assert rt.nommage_notes() == "fr"
    rt.set_parametres("en")
    assert rt.nommage_notes() == "en"             # sans choix : comme la langue
    rt.set_parametres(notes="fr")
    assert rt.nommage_notes() == "fr" and rt.parametres["langue"] == "en"
    with pytest.raises(ValueError):
        rt.set_parametres(notes="de")
