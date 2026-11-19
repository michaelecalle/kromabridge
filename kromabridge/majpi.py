"""Mise a jour de la version Raspberry Pi a partir du fichier kromabridge-pi.zip
publie avec chaque version (choisi dans la page, ou lu sur la cle USB du Pi).

Seuls le programme (kromabridge/), les outils (outils/) et la documentation
(doc/) sont remplaces : les morceaux, les listes, les reglages et le journal ne
sont jamais touches. L'ancienne version est gardee a cote, pour pouvoir y revenir.
Apres l'installation, le programme s'arrete ; le service systeme le relance
aussitot avec la nouvelle version."""

from __future__ import annotations

import io
import os
import re
import shutil
import threading
import zipfile
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
PARTIES = ("kromabridge", "outils", "doc")


class ErreurMaj(ValueError):
    pass


def _version(texte: str) -> str | None:
    m = re.search(r'__version__\s*=\s*"([^"]+)"', texte)
    return m.group(1) if m else None


def _precedent(racine: Path) -> Path:
    return racine.parent / (racine.name + "-precedent")


def version_precedente(racine: Path = RACINE) -> str | None:
    f = _precedent(racine) / "kromabridge" / "__init__.py"
    return _version(f.read_text(encoding="utf-8")) if f.is_file() else None


def _copier(depuis: Path, vers: Path) -> None:
    if vers.exists():
        shutil.rmtree(vers)
    vers.mkdir(parents=True)
    for p in PARTIES:
        if (depuis / p).exists():
            shutil.copytree(depuis / p, vers / p, ignore=shutil.ignore_patterns("__pycache__"))


def _remplacer(depuis: Path, racine: Path) -> None:
    for p in PARTIES:
        if (depuis / p).exists():
            if (racine / p).exists():
                shutil.rmtree(racine / p)
            shutil.copytree(depuis / p, racine / p)


def installer(donnees: bytes, racine: Path = RACINE) -> str:
    """Installe la mise a jour ; renvoie son numero de version."""
    try:
        z = zipfile.ZipFile(io.BytesIO(donnees))
    except zipfile.BadZipFile:
        raise ErreurMaj("ce fichier n’est pas une mise à jour de Kromabridge") from None
    noms = z.namelist()
    if "kromabridge/__init__.py" not in noms:
        raise ErreurMaj("ce fichier n’est pas une mise à jour de Kromabridge")
    for n in noms:
        if n.startswith("/") or ".." in n.split("/") or n.split("/")[0] not in PARTIES:
            raise ErreurMaj("fichier de mise à jour refusé (contenu inattendu)")
    version = _version(z.read("kromabridge/__init__.py").decode("utf-8"))
    if not version:
        raise ErreurMaj("ce fichier n’est pas une mise à jour de Kromabridge")
    temp = racine / ".maj"
    if temp.exists():
        shutil.rmtree(temp)
    z.extractall(temp)
    try:
        _copier(racine, _precedent(racine))        # l'ancienne version, gardee a cote
        _remplacer(temp, racine)
    finally:
        shutil.rmtree(temp, ignore_errors=True)
    return version


def revenir(racine: Path = RACINE) -> str:
    """Remet la version precedente (et garde l'actuelle a sa place, pour pouvoir
    refaire la mise a jour). Renvoie le numero de la version remise."""
    precedent = _precedent(racine)
    version = version_precedente(racine)
    if version is None:
        raise ErreurMaj("aucune version précédente n’est disponible")
    temp = racine / ".maj"
    _copier(precedent, temp)
    try:
        _copier(racine, precedent)
        _remplacer(temp, racine)
    finally:
        shutil.rmtree(temp, ignore_errors=True)
    return version


def redemarrer(delai: float = 1.5) -> None:
    """Arrete le programme apres avoir repondu a la page : le service systeme
    (Restart=always) le relance avec la nouvelle version."""
    threading.Timer(delai, os._exit, args=(0,)).start()
