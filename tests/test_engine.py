"""Ce que le moteur doit garantir, en particulier ne jamais laisser une note tenue."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kromabridge.engine import Engine  # noqa: E402
from kromabridge.preset import Beam, Preset, PresetError  # noqa: E402

NOTE_ON_CH1 = 0x90
NOTE_OFF_CH1 = 0x80


def preset(**kwargs) -> Preset:
    """Trois rayons : do, re, mi entrants, renvoyes vers sol, la, si."""
    beams = [
        Beam(index=1, incoming_note=60, note=67),
        Beam(index=2, incoming_note=62, note=69),
        Beam(index=3, incoming_note=64, note=71),
    ]
    return Preset(name="Essai", beams=beams, **kwargs)


def test_une_note_est_traduite_a_l_aller_et_au_retour():
    engine = Engine(preset())
    assert engine.process([NOTE_ON_CH1, 60, 100]) == [[NOTE_ON_CH1, 67, 100]]
    assert engine.process([NOTE_OFF_CH1, 60, 0]) == [[NOTE_OFF_CH1, 67, 0]]
    assert engine.held_notes == {}


def test_un_note_on_de_velocite_nulle_coupe_la_note():
    engine = Engine(preset())
    engine.process([NOTE_ON_CH1, 60, 100])
    assert engine.process([NOTE_ON_CH1, 60, 0]) == [[NOTE_OFF_CH1, 67, 0]]


def test_changer_de_memoire_ne_laisse_aucune_note_suspendue():
    engine = Engine(preset())
    engine.process([NOTE_ON_CH1, 60, 100])
    autre = Preset(name="Autre", beams=[Beam(index=1, incoming_note=60, note=36)])

    flush = engine.set_preset(autre)

    assert flush == [[NOTE_OFF_CH1, 67, 0]], "la coupure doit viser l'ancienne note"
    assert engine.process([NOTE_ON_CH1, 60, 100]) == [[NOTE_ON_CH1, 36, 100]]


def test_deux_rayons_sur_la_meme_note_ne_se_coupent_pas_l_un_l_autre():
    memoire = Preset(
        name="Doublon",
        beams=[
            Beam(index=1, incoming_note=60, note=72),
            Beam(index=2, incoming_note=62, note=72),
        ],
    )
    engine = Engine(memoire)
    engine.process([NOTE_ON_CH1, 60, 100])
    engine.process([NOTE_ON_CH1, 62, 100])

    assert engine.process([NOTE_OFF_CH1, 60, 0]) == [], "l'autre rayon la tient encore"
    assert engine.process([NOTE_OFF_CH1, 62, 0]) == [[NOTE_OFF_CH1, 72, 0]]


def test_un_rayon_redeclenche_sans_coupure_ne_fuit_pas():
    engine = Engine(preset())
    engine.process([NOTE_ON_CH1, 60, 100])
    sortie = engine.process([NOTE_ON_CH1, 60, 110])

    assert sortie == [[NOTE_OFF_CH1, 67, 0], [NOTE_ON_CH1, 67, 110]]
    assert engine.process([NOTE_OFF_CH1, 60, 0]) == [[NOTE_OFF_CH1, 67, 0]]
    assert engine.held_notes == {}


def test_une_note_inconnue_passe_ou_disparait_selon_le_reglage():
    passante = Engine(preset())
    assert passante.process([NOTE_ON_CH1, 90, 100]) == [[NOTE_ON_CH1, 90, 100]]

    muette = Engine(preset(unknown_notes="ignorer"))
    assert muette.process([NOTE_ON_CH1, 90, 100]) == []


def test_la_transposition_s_applique_et_les_notes_hors_etendue_sont_ecartees():
    engine = Engine(preset(transpose=12))
    assert engine.process([NOTE_ON_CH1, 60, 100]) == [[NOTE_ON_CH1, 79, 100]]

    aigue = Preset(
        name="Trop haut",
        transpose=48,
        beams=[Beam(index=1, incoming_note=60, note=100)],
    )
    assert Engine(aigue).process([NOTE_ON_CH1, 60, 100]) == [], "100 + 48 depasse 127"


def test_le_canal_et_la_velocite_d_un_rayon_priment():
    memoire = Preset(
        name="Sur mesure",
        out_channel=1,
        beams=[Beam(index=1, incoming_note=60, note=67, channel=10, velocity=64)],
    )
    engine = Engine(memoire)
    assert engine.process([NOTE_ON_CH1, 60, 127]) == [[NOTE_ON_CH1 | 9, 67, 64]]


def test_les_messages_continus_suivent_le_canal_de_sortie():
    engine = Engine(preset(out_channel=5))
    assert engine.process([0xB0, 1, 64]) == [[0xB0 | 4, 1, 64]]
    assert engine.process([0xE0, 0, 96]) == [[0xE0 | 4, 0, 96]]


def test_l_horloge_et_les_sysex_traversent_sans_etre_touches():
    engine = Engine(preset())
    assert engine.process([0xF8]) == [[0xF8]]
    assert engine.process([0xF0, 0x7B, 0x01, 0x78, 0xF7]) == [[0xF0, 0x7B, 0x01, 0x78, 0xF7]]


def test_la_pression_polyphonique_suit_la_note_remappee():
    engine = Engine(preset())
    engine.process([NOTE_ON_CH1, 60, 100])
    assert engine.process([0xA0, 60, 80]) == [[0xA0, 67, 80]]


def test_panique_coupe_tout_ce_qui_sonne():
    engine = Engine(preset())
    engine.process([NOTE_ON_CH1, 60, 100])
    engine.process([NOTE_ON_CH1, 64, 100])

    assert engine.panic() == [[NOTE_OFF_CH1, 67, 0], [NOTE_OFF_CH1, 71, 0]]
    assert engine.held_notes == {}


def test_deux_rayons_ne_peuvent_pas_declarer_la_meme_note_entrante():
    with pytest.raises(PresetError, match="meme note entrante"):
        Preset(
            name="Impossible",
            beams=[
                Beam(index=1, incoming_note=60, note=67),
                Beam(index=2, incoming_note=60, note=69),
            ],
        )


def test_sustain_pedal_is_normalised_and_debounced():
    from kromabridge.engine import Engine
    from kromabridge.preset import chromatic_preset

    e = Engine(chromatic_preset(3))
    sorties = [e.process([0xB0, 64, v]) for v in (52, 63, 0, 0, 63, 10)]
    assert sorties == [[[0xB0, 64, 127]], [], [[0xB0, 64, 0]], [], [[0xB0, 64, 127]], [[0xB0, 64, 0]]]


def test_pitch_bend_springs_back_when_the_hand_leaves():
    from kromabridge.engine import Engine
    from kromabridge.preset import chromatic_preset

    e = Engine(chromatic_preset(3, first_note=60))
    e.process([0x90, 60, 100])
    assert e.process([0xE0, 0x00, 0x30]) == [[0xE0, 0x00, 0x30]]          # main qui descend
    assert e.process([0x80, 60, 0]) == [[0x80, 60, 0], [0xE0, 0x00, 0x40]]  # retour au centre
    e.process([0x90, 61, 100])
    assert e.process([0x80, 61, 0]) == [[0x80, 61, 0]]                      # rien a recentrer


def test_no_spring_back_while_another_beam_still_sounds():
    from kromabridge.engine import Engine
    from kromabridge.preset import chromatic_preset

    e = Engine(chromatic_preset(3, first_note=60))
    e.process([0x90, 60, 100]); e.process([0x90, 61, 100])
    e.process([0xE0, 0x00, 0x50])
    assert e.process([0x80, 60, 0]) == [[0x80, 60, 0]]
    assert e.process([0x80, 61, 0]) == [[0x80, 61, 0], [0xE0, 0x00, 0x40]]



def test_accord_part_et_s_arrete_ensemble():
    from kromabridge.engine import Engine
    from kromabridge.preset import Beam, Preset
    p = Preset(name="A", beams=[Beam(index=1, incoming_note=60, note=48, extra=[52, 55]),
                                Beam(index=2, incoming_note=61, note=55)])
    e = Engine(p)
    assert e.process([0x90, 60, 100]) == [[0x90, 48, 100], [0x90, 52, 100], [0x90, 55, 100]]
    # un autre rayon joue aussi 55 : le relachement du premier ne la coupe pas
    assert e.process([0x90, 61, 90]) == [[0x90, 55, 90]]
    assert e.process([0x80, 60, 0]) == [[0x80, 48, 0], [0x80, 52, 0]]
    assert e.process([0x80, 61, 0]) == [[0x80, 55, 0]]
    assert Beam.from_dict(p.beams[0].to_dict()).extra == [52, 55]



def test_sustain_un_rayon_a_la_fois():
    from kromabridge.engine import Engine
    from kromabridge.preset import Beam, Preset
    p = Preset(name="A", beams=[Beam(index=1, incoming_note=60, note=48),
                                Beam(index=2, incoming_note=61, note=50, extra=[53])])
    e = Engine(p)
    e.sustain_mono = True
    assert e.process([0xB0, 64, 127]) == []                      # pedale : rien vers le synthe
    assert e.process([0x90, 60, 100]) == [[0x90, 48, 100]]
    assert e.process([0x80, 60, 0]) == []                        # tenue par la pedale
    # rayon 2 : la note tenue s'arrete, l'accord du rayon 2 demarre
    assert e.process([0x90, 61, 90]) == [[0x80, 48, 0], [0x90, 50, 90], [0x90, 53, 90]]
    assert e.process([0x80, 61, 0]) == []
    assert e.process([0xB0, 64, 0]) == [[0x80, 50, 0], [0x80, 53, 0]]   # pedale relachee
    # deux rayons coupes ensemble sonnent ensemble
    e.process([0xB0, 64, 127])
    e.process([0x90, 60, 100])
    assert e.process([0x90, 61, 100]) == [[0x90, 50, 100], [0x90, 53, 100]]


def test_choix_ajoute_de_la_pedale():
    from kromabridge.harp_settings import BY_KEY
    r = BY_KEY["pedale_analogique"]
    assert r.message(5) == r.message(4) == [0xB0, 22, 127]      # la harpe recoit "Sustain"
    assert r.message(1) == [0xB0, 22, 32]                        # inchange (Volume)
    assert r.describe()["choix"][5] == "Sustain (un rayon à la fois)"



def test_sustain_un_rayon_traverse_le_changement_de_partie(tmp_path):
    from kromabridge.bank import Bank
    from kromabridge.preset import Beam, Preset
    from kromabridge.runtime import Runtime
    rayons = lambda notes: [Beam(index=i, incoming_note=59 + i, note=n).to_dict() for i, n in enumerate(notes, 1)]
    p = Preset(name="M", beams=[Beam.from_dict(b) for b in rayons([48, 50, 52, 53, 55])], beam_count=5,
               parts=[{"nom": "A", "rayons": rayons([48, 50, 52, 53, 55])},
                      {"nom": "B", "rayons": rayons([60, 62, 64, 65, 67])}])
    envoyes = []
    rt = Runtime(Bank([p]), send=envoyes.extend)
    rt.harp_values = lambda: {"pedale_analogique": 5}
    rt.handle([0xB0, 64, 127]); rt.handle([0x90, 60, 100]); rt.handle([0x80, 60, 0])
    envoyes.clear()
    rt.select_part(1)                                    # la note 48 continue
    assert [0x80, 48, 0] not in envoyes
    assert rt.handle([0x90, 61, 100]) == [[0x80, 48, 0], [0x90, 62, 100]]



def test_sustain_toutes_les_notes_traverse_le_changement_de_partie():
    from kromabridge.bank import Bank
    from kromabridge.preset import Beam, Preset
    from kromabridge.runtime import Runtime
    rayons = lambda notes: [Beam(index=i, incoming_note=59 + i, note=n).to_dict() for i, n in enumerate(notes, 1)]
    p = Preset(name="M", beams=[Beam.from_dict(b) for b in rayons([48, 50, 52, 53, 55])], beam_count=5,
               parts=[{"nom": "A", "rayons": rayons([48, 50, 52, 53, 55])},
                      {"nom": "B", "rayons": rayons([60, 62, 64, 65, 67])}])
    envoyes = []
    rt = Runtime(Bank([p]), send=envoyes.extend)
    rt.harp_values = lambda: {"pedale_analogique": 4}
    rt.handle([0xB0, 64, 127]); rt.handle([0x90, 60, 100]); rt.handle([0x80, 60, 0])
    envoyes.clear()
    rt.select_part(1)
    assert not [m for m in envoyes if m[0] & 0xF0 == 0x80]      # rien n'est coupe
