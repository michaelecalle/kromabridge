"""Les commandes vers la harpe et l'enchainement des memoires."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kromabridge import harp  # noqa: E402
from kromabridge.bank import Bank  # noqa: E402
from kromabridge.engine import Engine  # noqa: E402
from kromabridge.learn import Learner  # noqa: E402
from kromabridge.preset import Beam, Preset, PresetError  # noqa: E402


def test_le_nombre_de_rayons_suit_la_table_du_logiciel_d_origine():
    attendu = [0, 13, 26, 39, 51, 64, 77, 89, 102, 115, 127]   # comme le Manager V24
    assert [harp.beam_count_value(n) for n in range(5, 16)] == attendu


def test_la_harpe_refuse_un_nombre_de_rayons_impossible():
    with pytest.raises(harp.HarpError, match="de 5 a 15"):
        harp.set_beam_count(16)


def test_les_sysex_ont_la_forme_attendue_par_la_harpe():
    assert harp.recall_preset(3) == [0xF0, 0x7B, 0x01, 0x74, 3, 0xF7]
    assert harp.store_preset(3) == [0xF0, 0x7B, 0x01, 0x72, 3, 0xF7]
    assert harp.request_settings() == [0xF0, 0x7B, 0x01, 0x78, 0xF7]
    assert harp.request_firmware_version() == [0xF0, 0x7B, 0x01, 0x79, 0xF7]


def test_un_reglage_se_designe_par_son_nom():
    assert harp.setting("puissance_rouge", 100) == [0xB0, 12, 100]
    with pytest.raises(harp.HarpError, match="réglage inconnu"):
        harp.setting("puissance_violette", 100)


def memoire(nom, note, rayons=None, reglages=None):
    return Preset(
        name=nom,
        beams=[Beam(index=1, incoming_note=60, note=note)],
        beam_count=rayons,
        harp_settings=reglages or {},
    )


def test_charger_une_memoire_coupe_les_notes_puis_configure_la_harpe():
    banque = Bank([memoire("Un", 67, rayons=8), memoire("Deux", 72, rayons=12)])
    banque.engine.process([0x90, 60, 100])

    sortie = banque.select(1)

    assert sortie[0] == [0x80, 67, 0], "la coupure doit passer avant le reste"
    assert harp.set_beam_count(12) in sortie
    assert banque.current.name == "Deux"
    assert banque.engine.process([0x90, 60, 100]) == [[0x90, 72, 100]]


def test_les_reglages_de_la_harpe_partent_avec_la_memoire():
    banque = Bank([memoire("Rouge", 67, rayons=10, reglages={"puissance_rouge": 120})])
    sortie = banque.select(0)
    assert [0xB0, 12, 120] in sortie


def test_les_memoires_defilent_en_boucle():
    banque = Bank([memoire("Un", 67), memoire("Deux", 69), memoire("Trois", 71)])
    banque.next()
    banque.next()
    assert banque.current.name == "Trois"
    banque.next()
    assert banque.current.name == "Un"
    banque.previous()
    assert banque.current.name == "Trois"


def test_une_memoire_inexistante_est_refusee_clairement():
    banque = Bank([memoire("Un", 67)])
    with pytest.raises(PresetError, match="la banque en compte 1"):
        banque.select(4)


def test_l_apprentissage_numerote_les_rayons_dans_l_ordre_des_coupures():
    apprenti = Learner()
    for note in (64, 66, 68, 71):
        apprenti.feed([0x90, note, 90])
        apprenti.feed([0x80, note, 0])

    preset = apprenti.to_preset(name="Ma harpe")

    assert [b.index for b in preset.beams] == [1, 2, 3, 4]
    assert [b.incoming_note for b in preset.beams] == [64, 66, 68, 71]
    assert [b.note for b in preset.beams] == [64, 66, 68, 71], "identique au depart"


def test_l_apprentissage_ignore_une_note_deja_vue_et_sait_revenir_en_arriere():
    apprenti = Learner()
    apprenti.feed([0x90, 64, 90])
    apprenti.feed([0x90, 64, 90])
    assert apprenti.count == 1
    apprenti.feed([0x90, 66, 90])
    assert apprenti.undo() == 66
    assert apprenti.count == 1


def test_une_memoire_survit_a_l_aller_retour_sur_le_disque(tmp_path):
    preset = Preset(
        name="Morceau 1",
        beams=[Beam(index=1, incoming_note=60, note=67, channel=3, velocity=90)],
        beam_count=9,
        harp_settings={"puissance_bleue": 64},
        transpose=-12,
    )
    chemin = tmp_path / "morceau.json"
    preset.save(chemin)

    relu = Preset.load(chemin)

    assert relu.to_dict() == preset.to_dict()
    assert Bank.from_directory(tmp_path).current.name == "Morceau 1"
