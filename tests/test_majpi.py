"""Mise a jour du Pi depuis un fichier .zip (sans materiel, dans un dossier temporaire)."""

import io
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kromabridge import majpi  # noqa: E402


def paquet(version, extra=None):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("kromabridge/__init__.py", f'__version__ = "{version}"\n')
        z.writestr("kromabridge/web.py", f"# web {version}\n")
        z.writestr("outils/kromabridge-usb", "#!/bin/sh\n")
        for nom, contenu in (extra or {}).items():
            z.writestr(nom, contenu)
    return b.getvalue()


def installation(tmp_path):
    racine = tmp_path / "kromabridge"
    (racine / "kromabridge").mkdir(parents=True)
    (racine / "kromabridge" / "__init__.py").write_text('__version__ = "1.0"\n')
    (racine / "kromabridge" / "ancien.py").write_text("# ancien\n")
    (racine / "presets").mkdir()
    (racine / "presets" / "Morceau.json").write_text("{}")
    (racine / "listes.json").write_text("[]")
    return racine


def test_installer_puis_revenir(tmp_path):
    racine = installation(tmp_path)
    assert majpi.installer(paquet("1.1"), racine) == "1.1"
    assert 'version__ = "1.1"' in (racine / "kromabridge" / "__init__.py").read_text()
    assert not (racine / "kromabridge" / "ancien.py").exists()       # le programme est remplace en entier
    assert (racine / "presets" / "Morceau.json").exists()              # les morceaux restent
    assert (racine / "listes.json").exists()
    assert majpi.version_precedente(racine) == "1.0"
    assert majpi.revenir(racine) == "1.0"
    assert (racine / "kromabridge" / "ancien.py").exists()
    assert majpi.version_precedente(racine) == "1.1"                  # on peut refaire la mise a jour


def test_fichiers_refuses(tmp_path):
    racine = installation(tmp_path)
    with pytest.raises(majpi.ErreurMaj):
        majpi.installer(b"pas un zip", racine)
    with pytest.raises(majpi.ErreurMaj):
        majpi.installer(paquet("1.1", {"presets/Morceau.json": "{}"}), racine)   # ne touche pas aux morceaux
    with pytest.raises(majpi.ErreurMaj):
        majpi.installer(paquet("1.1", {"kromabridge/../../x": ""}), racine)
    assert 'version__ = "1.0"' in (racine / "kromabridge" / "__init__.py").read_text()
    with pytest.raises(majpi.ErreurMaj):
        majpi.revenir(racine)                                             # pas encore de version precedente
