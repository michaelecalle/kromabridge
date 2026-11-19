"""Horloge du Pi : il n'a pas de pile pour garder l'heure, et sur son propre
reseau Wi-Fi il n'a pas Internet. On le met donc a l'heure de l'appareil qui
affiche la page (tablette, telephone, PC), par l'outil /usr/local/sbin/kromabridge-heure
(installe une fois avec les droits d'administrateur, voir outils/installer-heure.sh).
Sur le PC, rien de tout ca : Windows est deja a l'heure."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

OUTIL = "/usr/local/sbin/kromabridge-heure"
ECART_MIN = 60          # en dessous d'une minute d'ecart, on ne touche a rien


def regler(ms_appareil: float, lancer=subprocess.run, maintenant=time.time) -> float | None:
    """Met l'horloge a l'heure de l'appareil (en millisecondes depuis 1970).
    Renvoie l'ecart corrige en secondes, ou None si rien n'a ete fait."""
    ecart = ms_appareil / 1000 - maintenant()
    if abs(ecart) < ECART_MIN or not Path(OUTIL).exists():
        return None
    r = lancer(["sudo", "-n", OUTIL, str(int(ms_appareil // 1000))], capture_output=True, text=True, timeout=10)
    if r.returncode != 0 or (r.stdout or "").strip() != "ok":
        return None
    return ecart
