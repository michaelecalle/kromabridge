"""Cle USB branchee sur le boitier : y ecrire et y lire les fichiers .kromalaser
(exports de morceaux et de listes). Sur le PC, rien de tout ca : la fenetre
d'ouverture de fichier du navigateur donne deja acces aux cles USB.

La cle est montee par l'outil /usr/local/sbin/kromabridge-usb (installe une
fois avec les droits d'administrateur, voir outils/installer-usb.sh)."""

from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path

OUTIL = "/usr/local/sbin/kromabridge-usb"
DOSSIER = "Kromabridge"          # dossier cree sur la cle


class ErreurUSB(RuntimeError):
    pass


def _outil(action: str, lancer=subprocess.run) -> str:
    if not Path(OUTIL).exists():
        raise ErreurUSB("la clé USB n’est pas encore prise en charge sur ce Pi (outil USB non installé)")
    r = lancer(["sudo", "-n", OUTIL, action], capture_output=True, text=True, timeout=20)
    if r.returncode != 0:
        raise ErreurUSB((r.stderr or r.stdout or "clé USB inaccessible").strip().replace("boîtier", "Pi"))
    return r.stdout.strip()


def monter(lancer=subprocess.run) -> Path:
    return Path(_outil("monter", lancer))


def ejecter(lancer=subprocess.run) -> None:
    _outil("ejecter", lancer)


def nom_fichier(nom: str) -> str:
    """Un nom de fichier sans caracteres interdits (FAT32, Windows)."""
    sur = "".join(c if c.isalnum() or c in " -_()'" else "_" for c in nom).strip() or "export"
    return sur[:120] + ".kromalaser"


def ecrire(nom: str, donnees: bytes, racine: Path | None = None) -> str:
    """Ecrit un fichier dans le dossier Kromabridge de la cle ; renvoie son chemin
    relatif (pour l'afficher)."""
    racine = racine or monter()
    dossier = racine / DOSSIER
    dossier.mkdir(exist_ok=True)
    f = dossier / nom_fichier(nom)
    f.write_bytes(donnees)
    return f"{DOSSIER}/{f.name}"


def lister(racine: Path | None = None, motif: str = "*.kromalaser") -> list[dict]:
    """Les fichiers .kromalaser de la cle (dossier Kromabridge et racine)."""
    racine = racine or monter()
    fichiers = []
    for d in (racine / DOSSIER, racine):
        if d.is_dir():
            for f in sorted(d.glob(motif)):
                st = f.stat()
                fichiers.append({"fichier": str(f.relative_to(racine)).replace("\\", "/"),
                                 "taille": st.st_size,
                                 "date": datetime.fromtimestamp(st.st_mtime).strftime("%d/%m/%Y %H:%M")})
    return fichiers


def lire(fichier: str, racine: Path | None = None, suffixe: str = ".kromalaser") -> bytes:
    racine = racine or monter()
    f = (racine / fichier).resolve()
    if racine.resolve() not in f.parents or f.suffix != suffixe or not f.is_file():
        raise ErreurUSB("fichier introuvable sur la clé USB")
    return f.read_bytes()
