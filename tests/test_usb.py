"""Cle USB du boitier (sans materiel : dossier temporaire a la place de la cle)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kromabridge import usb  # noqa: E402


def test_ecrire_lister_lire(tmp_path):
    rel = usb.ecrire("Liste Concert/été?", b'{"format": 1}', racine=tmp_path)
    assert rel == "Kromabridge/Liste Concert_été_.kromalaser"
    (tmp_path / "vieux.kromalaser").write_bytes(b"x")
    noms = [f["fichier"] for f in usb.lister(racine=tmp_path)]
    assert noms == ["Kromabridge/Liste Concert_été_.kromalaser", "vieux.kromalaser"]
    assert usb.lire(rel, racine=tmp_path) == b'{"format": 1}'
    with pytest.raises(usb.ErreurUSB):
        usb.lire("../secret.kromalaser", racine=tmp_path)


def test_outil_absent(monkeypatch):
    monkeypatch.setattr(usb, "OUTIL", "/nexiste/pas")
    with pytest.raises(usb.ErreurUSB):
        usb.monter()
