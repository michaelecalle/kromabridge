"""La commande de la harpe (ouverture/fermeture), sans materiel."""

import pytest

from kromabridge.midi_io import HarpControl, MidiError

CARDS = """ 0 [Headphones     ]: bcm2835_headpho - bcm2835 Headphones
                      bcm2835 Headphones
 3 [USB            ]: USB-Audio - Kromalaser USB
                      Kromalaser LasHarp Kromalaser USB at usb-0000:01:00.0-1.2, full speed
"""


def make(tmp_path, cards=CARDS, devices=("midiC3D0",)):
    asound = tmp_path / "asound"
    dev = tmp_path / "snd"
    asound.mkdir()
    dev.mkdir()
    (asound / "cards").write_text(cards)
    for nom in devices:
        (dev / nom).write_bytes(b"")
    envois = []
    harpe = HarpControl(asound=str(asound), dev=str(dev),
                        writer=lambda chemin, data: envois.append((chemin.name, data)))
    return harpe, envois


def test_open_and_close_write_realtime_bytes_to_the_harp_raw_device(tmp_path):
    harpe, envois = make(tmp_path)
    harpe.open_harp()
    harpe.close_harp()
    pousse = bytes([0xF0, 0x7B, 0x01, 0x79, 0xF7])   # relance : la harpe a un message de retard
    assert envois == [("midiC3D0", b"\xfa"), ("midiC3D0", pousse), ("midiC3D0", pousse),
                      ("midiC3D0", b"\xfc"), ("midiC3D0", pousse), ("midiC3D0", pousse)]


def test_missing_harp_raises_a_clear_error(tmp_path):
    harpe, envois = make(tmp_path, cards=" 0 [Headphones ]: bcm2835 Headphones\n", devices=())
    with pytest.raises(MidiError):
        harpe.open_harp()
    assert envois == []


def test_follows_the_card_number_after_replugging(tmp_path):
    cartes = CARDS.replace(" 3 [USB", " 4 [USB")
    harpe, envois = make(tmp_path, cards=cartes, devices=("midiC4D0",))
    harpe.open_harp()
    assert envois[0][0] == "midiC4D0" and envois[0][1].startswith(b"\xfa")


def test_write_error_becomes_midi_error(tmp_path):
    harpe, _ = make(tmp_path)

    def casse(chemin, data):
        raise OSError("No such device")

    harpe._writer = casse
    with pytest.raises(MidiError):
        harpe.open_harp()


def test_open_is_midi_start_like_manager_v24(tmp_path):
    harpe, envois = make(tmp_path)
    harpe.open_harp()
    assert envois[0][1].startswith(b"\xfa")


def test_listener_reads_until_the_harp_disappears(tmp_path, monkeypatch):
    import kromabridge.midi_io as mio
    from kromabridge.midi_io import HarpListener

    harpe, _ = make(tmp_path)
    morceaux = [b"\x90\x3c\x70", b"\x80\x3c\x00", b""]
    monkeypatch.setattr(mio.os, "read", lambda fd, n: morceaux.pop(0))
    monkeypatch.setattr(mio.os, "close", lambda fd: None)
    recu = []
    ecoute = HarpListener(harpe, on_data=recu.append, opener=lambda chemin: 42)
    ecoute.run_once()
    assert recu == [b"\x90\x3c\x70", b"\x80\x3c\x00"]
    assert not ecoute.connected


def test_listener_without_harp_raises_midi_error(tmp_path):
    from kromabridge.midi_io import HarpListener

    harpe, _ = make(tmp_path, cards=" 0 [Headphones ]: bcm2835 Headphones\n", devices=())
    with pytest.raises(MidiError):
        HarpListener(harpe, opener=lambda chemin: 42).run_once()



def test_fermer_avant_ouverture(tmp_path, monkeypatch):
    """Reglages envoyes, harpe fermee : un « fermer » part juste avant l'unique
    ordre d'ouverture. Sans reglages depuis le dernier ordre : rien de plus."""
    from kromabridge import midi_io
    monkeypatch.setattr(midi_io, "DELAI_AVANT_OUVERTURE", 0)
    harpe, envois = make(tmp_path)
    ordres = lambda: [d for _, d in envois if d in (bytes([0xFA]), bytes([0xFC]))]
    harpe.open_harp()                                   # rien avant : un seul FA
    assert ordres() == [bytes([0xFA])]
    harpe.close_harp()
    harpe.send([0xB0, 45, 26])                          # reglages d'un morceau
    harpe.open_harp()
    assert ordres() == [bytes([0xFA]), bytes([0xFC]), bytes([0xFC]), bytes([0xFA])]
    harpe.send([0xB0, 42, 50])                          # harpe ouverte : pas de « fermer »
    harpe.open_harp()
    assert ordres()[-1] == bytes([0xFA]) and ordres()[-2] == bytes([0xFA])
    harpe.send([0xF0, 0x7B, 0x01, 0x78, 0xF7])           # une lecture n'est pas un reglage
    harpe.close_harp()
    harpe.send([0xF0, 0x7B, 0x01, 0x78, 0xF7])
    harpe.open_harp()
    assert ordres()[-2:] == [bytes([0xFC]), bytes([0xFA])]


def test_message_largeur_zero():
    from kromabridge import harp_settings as hs
    d = [int(x, 16) for x in "40 00 01 04 56 64 64 00 01 00 04 00 01 00 09 00 04 05 06 04 00 05 00 00 0A 62 32 00 0A 14 14 00 32 00 00 2D 5A 00 00 0D".split()]
    assert hs.write_all(d)[4:44] == d                  # controle d'un vrai releve retrouve
    d[hs.OCTET_LARGEUR] = 0
    m = hs.write_all(d)
    assert m[:4] == [0xF0, 0x7B, 0x01, 0x71] and m[-1] == 0xF7 and m[4 + 26] == 0 and m[4 + 39] == 0x3F


def test_ouverture_depart_zero():
    from kromabridge.harp_service import open_animated
    envois = []

    class C:
        def send(self, m, renvoi=True): envois.append(list(m))
        def send_quick(self, m): envois.append(list(m))
    open_animated(C(), 40, 0, 0.1, sleep=lambda s: None, depart_zero=lambda: [0xF0, 0x7B, 0x01, 0x71, 0xF7])
    assert envois[0][:2] == [0xB0, 42] and envois[1] == [0xFA] and envois[2][3] == 0x71   # largeur 0 apres l'ouverture
    envois.clear()
    open_animated(C(), 40, 0, 0.1, sleep=lambda s: None, depart_zero=lambda: None)
    assert envois[0][:2] == [0xB0, 42] and envois[1] == [0xFA] and envois[2][0] == 0xB0
