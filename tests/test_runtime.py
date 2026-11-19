"""Le boitier en marche : memoires, apprentissage, et l'API de la page web."""

import json
import sys
import threading
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kromabridge import harp  # noqa: E402
from kromabridge.bank import Bank  # noqa: E402
from kromabridge.preset import Beam, Preset  # noqa: E402
from kromabridge.runtime import Runtime  # noqa: E402
from kromabridge.web import note_name, serve  # noqa: E402


def memoire(nom="M1", note=67, rayons=8):
    return Preset(
        name=nom,
        beams=[
            Beam(index=1, incoming_note=60, note=note),
            Beam(index=2, incoming_note=62, note=note + 2),
        ],
        beam_count=rayons,
    )


@pytest.fixture
def boitier(tmp_path):
    envoyes: list[list[int]] = []
    runtime = Runtime(
        Bank([memoire("Un"), memoire("Deux", note=72)]),
        send=envoyes.extend,
        presets_dir=tmp_path,
    )
    return runtime, envoyes


def test_une_note_traverse_et_part_vers_le_synthetiseur(boitier):
    runtime, envoyes = boitier
    runtime.handle([0x90, 60, 100])
    assert envoyes == [[0x90, 67, 100]]


def test_changer_de_memoire_configure_aussi_la_harpe(boitier):
    runtime, envoyes = boitier
    runtime.handle([0x90, 60, 100])
    envoyes.clear()

    runtime.select(1)

    assert envoyes[0] == [0x80, 67, 0], "la note tenue est coupee d'abord"
    assert harp.set_beam_count(8) in envoyes
    assert runtime.state()["memoire"] == "Deux"


def test_pendant_l_apprentissage_rien_ne_sort_vers_le_synthetiseur(boitier):
    runtime, envoyes = boitier
    runtime.start_learning()
    envoyes.clear()

    runtime.handle([0x90, 64, 100])
    runtime.handle([0x80, 64, 0])

    assert envoyes == []
    assert runtime.state()["rayons_appris"] == 1


def test_l_apprentissage_cree_une_memoire_et_la_rend_active(boitier, tmp_path):
    runtime, _ = boitier
    runtime.start_learning()
    for note in (64, 66, 68, 69, 71, 72):
        runtime.handle([0x90, note, 100])

    preset = runtime.finish_learning(name="Ma harpe")

    assert [beam.incoming_note for beam in preset.beams] == [64, 66, 68, 69, 71, 72]
    assert preset.beam_count == 6, "le nombre de rayons suit ce qu'on a appris"
    assert runtime.state()["memoire"] == "Ma harpe"
    assert (tmp_path / "Ma harpe.json").exists(), "la memoire est ecrite sur la carte"


def test_un_apprentissage_trop_court_ne_commande_pas_la_harpe(boitier):
    """La harpe n'accepte pas moins de 5 rayons : mieux vaut ne rien lui imposer."""
    runtime, _ = boitier
    runtime.start_learning()
    for note in (64, 66, 68):
        runtime.handle([0x90, note, 100])

    preset = runtime.finish_learning(name="Partiel")

    assert len(preset.beams) == 3
    assert preset.beam_count is None


def test_terminer_un_apprentissage_vide_est_refuse_avec_une_raison(boitier):
    runtime, _ = boitier
    runtime.start_learning()
    with pytest.raises(RuntimeError, match="coupez les rayons un par un"):
        runtime.finish_learning()


def test_changer_la_note_d_un_rayon_est_enregistre(boitier, tmp_path):
    runtime, _ = boitier
    runtime.set_beam_note(1, 60)

    assert runtime.state()["rayons"][0]["note"] == 60
    ecrit = json.loads((tmp_path / "Un.json").read_text(encoding="utf-8"))
    assert ecrit["rayons"][0]["note"] == 60


def test_un_rayon_inconnu_ou_une_note_impossible_sont_refuses(boitier):
    runtime, _ = boitier
    with pytest.raises(KeyError):
        runtime.set_beam_note(99, 60)
    with pytest.raises(ValueError, match="étendue MIDI"):
        runtime.set_beam_note(1, 200)


def test_le_nom_des_notes_est_lisible():
    assert note_name(60) == "do4"
    assert note_name(69) == "la4"


def test_la_page_web_repond_et_accepte_un_changement(boitier):
    runtime, _ = boitier
    serveur = serve(runtime, host="127.0.0.1", port=0)
    threading.Thread(target=serveur.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{serveur.server_address[1]}"
    try:
        page = urllib.request.urlopen(base + "/").read().decode("utf-8")
        assert "<title>Kromabridge</title>" in page

        etat = json.loads(urllib.request.urlopen(base + "/api/etat").read())
        assert etat["memoire"] == "Un"

        requete = urllib.request.Request(
            base + "/api/rayon",
            data=json.dumps({"rayon": 2, "note": 80}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        apres = json.loads(urllib.request.urlopen(requete).read())
        assert apres["rayons"][1]["note"] == 80
    finally:
        serveur.shutdown()
        serveur.server_close()


def test_la_page_web_explique_une_demande_invalide(boitier):
    runtime, _ = boitier
    serveur = serve(runtime, host="127.0.0.1", port=0)
    threading.Thread(target=serveur.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{serveur.server_address[1]}"
    try:
        requete = urllib.request.Request(
            base + "/api/rayon",
            data=json.dumps({"rayon": 1, "note": 999}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as erreur:
            urllib.request.urlopen(requete)
        assert erreur.value.code == 400
    finally:
        serveur.shutdown()
        serveur.server_close()


def test_shift_all_moves_every_beam_and_refuses_overflow(tmp_path):
    from kromabridge.bank import Bank
    from kromabridge.preset import Preset, chromatic_preset
    from kromabridge.runtime import Runtime

    rt = Runtime(Bank([chromatic_preset(3, first_note=60, name="A")]), presets_dir=tmp_path)
    rt.shift_all(12)
    assert [b.note for b in rt.bank.current.beams] == [72, 73, 74]
    assert [b.note for b in Preset.load(tmp_path / "A.json").beams] == [72, 73, 74]
    import pytest
    with pytest.raises(ValueError):
        rt.shift_all(60)
    assert [b.note for b in rt.bank.current.beams] == [72, 73, 74]
