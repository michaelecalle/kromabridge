"""Mise a l'heure du Pi depuis l'appareil qui affiche la page (sans materiel)."""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kromabridge import heure  # noqa: E402


def test_regler(monkeypatch, tmp_path):
    outil = tmp_path / "kromabridge-heure"
    outil.write_text("")
    monkeypatch.setattr(heure, "OUTIL", str(outil))
    appels = []

    def lancer(cmd, **_):
        appels.append(cmd)
        return SimpleNamespace(returncode=0, stdout="ok\n")

    # moins d'une minute d'ecart : rien
    assert heure.regler(1_000_030_000, lancer=lancer, maintenant=lambda: 1_000_000) is None
    assert appels == []
    # cinq jours de retard : on corrige
    ecart = heure.regler(1_432_000_000, lancer=lancer, maintenant=lambda: 1_000_000)
    assert ecart == 1_432_000 - 1_000_000
    assert appels == [["sudo", "-n", str(outil), "1432000"]]
    # outil absent : rien
    monkeypatch.setattr(heure, "OUTIL", str(tmp_path / "absent"))
    assert heure.regler(1_432_000_000, lancer=lancer, maintenant=lambda: 1_000_000) is None
