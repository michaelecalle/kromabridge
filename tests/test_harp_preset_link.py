"""Un morceau du boitier peut rappeler un preset de la harpe."""

import pytest

from kromabridge.bank import Bank
from kromabridge.preset import Preset, PresetError, chromatic_preset
from kromabridge.runtime import Runtime


def make(tmp_path):
    a = chromatic_preset(12, name="A")
    b = chromatic_preset(12, name="B")
    b.harp_preset = 7
    b.beam_count = 10
    synthe, harpe, rappels = [], [], []
    rt = Runtime(Bank([a, b]), send=synthe.extend, presets_dir=tmp_path,
                 harp_send=harpe.extend, harp_recall=rappels.append)
    return rt, synthe, harpe, rappels


def test_selecting_a_song_recalls_its_harp_preset_then_its_settings(tmp_path):
    rt, synthe, harpe, rappels = make(tmp_path)
    rt.select(1)
    assert rappels == [7]
    assert harpe == [[0xB0, 45, 63]] or harpe[0][:2] == [0xB0, 45]   # nombre de rayons vers la harpe
    assert all(m[:2] != [0xB0, 45] for m in synthe)                    # rien de tel vers le synthe
    assert rt.state()["preset_harpe"] == 7


def test_song_without_harp_preset_recalls_nothing(tmp_path):
    rt, _, _, rappels = make(tmp_path)
    rt.select(0)
    assert rappels == []


def test_set_harp_preset_is_saved_and_applied(tmp_path):
    rt, _, _, rappels = make(tmp_path)
    rt.set_harp_preset(12)
    assert rappels == [12]
    assert Preset.load(tmp_path / "A.json").harp_preset == 12
    rt.set_harp_preset(None)
    assert Preset.load(tmp_path / "A.json").harp_preset is None


def test_harp_preset_range_is_checked():
    with pytest.raises(PresetError):
        Preset.from_dict({"nom": "x", "preset_harpe": 41})


def test_song_keeps_a_full_snapshot_of_the_harp_and_sends_it_back(tmp_path):
    instantane = {"rayons": 8, "puissance_r": 30, "transposition": 0, "octave": 3}
    a = chromatic_preset(12, name="A")
    b = chromatic_preset(12, name="B")
    harpe = []
    rt = Runtime(Bank([a, b]), presets_dir=tmp_path, harp_send=harpe.extend,
                 harp_capture=lambda: dict(instantane))
    rt.capture_harp()
    assert Preset.load(tmp_path / "A.json").harp_snapshot == instantane
    assert rt.state()["harpe_memorisee"]
    rt.select(1)
    harpe.clear()
    rt.select(0)                     # retour au morceau A : la harpe recoit tout
    assert [0xB0, 45, 102] in harpe  # 8 rayons
    assert [0xB0, 12, 39] in harpe   # puissance rouge 30


def test_snapshot_values_are_checked():
    with pytest.raises(PresetError):
        Preset.from_dict({"nom": "x", "harpe": {"rayons": 11}})
    with pytest.raises(PresetError):
        Preset.from_dict({"nom": "x", "harpe": {"inconnu": 1}})


def test_saving_rewrites_the_file_it_came_from(tmp_path):
    chromatic_preset(5, name="Morceau 1").save(tmp_path / "01-morceau-1.json")
    rt = Runtime(Bank.from_directory(tmp_path), presets_dir=tmp_path)
    rt.set_beam_note(1, 70)
    rt.rename("Intro")
    assert sorted(p.name for p in tmp_path.glob("*.json")) == ["01-morceau-1.json"]
    assert Preset.load(tmp_path / "01-morceau-1.json").name == "Intro"


def test_reload_current_restores_the_saved_song(tmp_path):
    p = chromatic_preset(3, name="A")
    p.harp_snapshot = {"rayons": 4}
    p.save(tmp_path / "a.json")
    harpe = []
    rt = Runtime(Bank.from_directory(tmp_path), presets_dir=tmp_path, harp_send=harpe.extend)
    rt.bank.current.beams[0].note = 99          # fausse manoeuvre non enregistree
    rt.reload_current()
    assert rt.bank.current.beams[0].note == 60
    assert [0xB0, 45, 51] in harpe              # reglages de la harpe renvoyes (9 rayons)
