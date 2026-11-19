"""Reglages de la harpe : valeurs envoyees identiques a celles du Manager V24."""

import pytest

from kromabridge.harp_settings import (BY_KEY, SETTINGS, SysexCollector, parse_version,
                                       recall_preset, save_preset)


def values(key):
    s = BY_KEY[key]
    return [s.cc_value(v) for v in range(s.span + 1)]


def test_scaling_matches_manager_v24_tables():
    assert values("mode_r") == [0, 26, 51, 77, 102, 127]
    assert values("main") == [0, 22, 43, 64, 85, 106, 127]
    assert values("pedale_analogique") == [0, 32, 64, 96, 127, 127]   # + "un rayon a la fois" = Sustain
    assert values("rayons") == [0, 13, 26, 39, 51, 64, 77, 89, 102, 115, 127]
    assert values("miroir") == [0, 43, 85, 127]
    assert values("transposition") == [0, 12, 24, 35, 47, 58, 70, 81, 93, 104, 116, 127]
    assert values("ttl") == [0, 127]


def test_message_and_cc_numbers():
    assert BY_KEY["gain"].message(75) == [0xB0, 18, 127]
    assert BY_KEY["octave"].message(3) == [0xB0, 62, 77]
    assert {s.cc for s in SETTINGS} >= {12, 13, 14, 15, 16, 17, 18, 45, 61, 62}
    assert len({s.key for s in SETTINGS}) == len(SETTINGS) == 30


def test_out_of_range_is_refused():
    with pytest.raises(ValueError):
        BY_KEY["rayons"].message(11)
    with pytest.raises(ValueError):
        save_preset(41)


def test_presets():
    assert save_preset(1) == [0xF0, 0x7B, 0x01, 0x72, 1, 0xF7]
    assert recall_preset(40) == [0xF0, 0x7B, 0x01, 0x74, 40, 0xF7]


def test_version_and_sysex_reassembly():
    c = SysexCollector()
    c.feed(b"\xb0\x78\x00\xf0\x7b\x01\x06")
    c.feed(b"\xfa\x7a\x01\x04\x04\x00\xf7\x90\x3c\x70")  # FA temps reel intercale
    msg = c.last[0x06]
    assert parse_version(msg) == "1.440"


def test_wait_returns_only_a_new_reply():
    c = SysexCollector()
    c.feed(b"\xf0\x7b\x01\x70\x01\xf7")
    avant = c.snapshot()
    assert c.wait(0x70, after=avant, timeout=0.05) is None
    c.feed(b"\xf0\x7b\x01\x70\x02\xf7")
    assert c.wait(0x70, after=avant, timeout=0.05)[4] == 2


REAL_DUMP = bytes.fromhex(
    "F07B017064010204646464000100000001000900000102030005000"
    "00D2D32000A1414003200002D5A000056F7")


def test_parse_real_dump_from_michaels_harp():
    from kromabridge.harp_settings import DUMP_LAYOUT, parse_dump

    v = parse_dump(REAL_DUMP)
    assert set(v) == set(DUMP_LAYOUT) == {s.key for s in SETTINGS}
    assert v["gain"] == 75                       # curseur au maximum
    assert (v["mode_r"], v["mode_v"], v["mode_b"]) == (1, 2, 4)   # Tous, Ton, Declenche
    assert v["puissance_r"] == v["puissance_v"] == v["puissance_b"] == 100
    assert v["ttl"] == 0 and v["tout_module"] == 0
    assert [v[f"pedale_{i}"] for i in (1, 2, 3, 4)] == [0, 1, 2, 3]
    assert v["rayons"] + BY_KEY["rayons"].offset == 13
    assert v["octave"] + BY_KEY["octave"].offset == 0
    assert v["mod_profondeur"] == 85


def test_parse_rejects_other_replies():
    from kromabridge.harp_settings import parse_dump

    with pytest.raises(ValueError):
        parse_dump(bytes.fromhex("F07B0106 7A01040400F7".replace(" ", "")))


def test_every_list_has_a_french_translation_of_the_same_length():
    for s in SETTINGS:
        assert s.label_fr
        if s.choices:
            assert len(s.choices_fr) == len(s.choices), s.key
            d = s.describe()
            assert d["choix_manager"] == list(s.choices)


def test_an_identical_reply_still_counts_as_new():
    c = SysexCollector()
    c.feed(b"\xf0\x7b\x01\x70\x01\xf7")
    avant = c.snapshot()
    c.feed(b"\xf0\x7b\x01\x70\x01\xf7")          # memes reglages qu'avant
    assert c.wait(0x70, after=avant, timeout=0.05) is not None
