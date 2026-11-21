"""Les 40 presets de la harpe, vus depuis Kromabridge : copie de leur contenu
(lue sur la harpe), passage d'un morceau a un preset et d'un preset a un morceau.

Un preset de la harpe ne contient que les reglages du Manager V24 (nombre de
rayons, largeur, couleurs, pedales...). La note de chaque rayon, les accords, les
parties, l'animation, le son du synthe et le canal MIDI n'existent que dans
Kromabridge : la harpe ne sait jouer qu'une gamme chromatique, eventuellement
transposee (reglages Transpose et Octave)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

TRANSPO, OCTAVE = "transposition", "octave"
OCTAVE_ZERO = 3                       # valeur du reglage Octave pour "pas de decalage"
SAUVEGARDES_MAX = 40


def decalage(valeurs: dict) -> int:
    """Le decalage en demi-tons produit par Transpose et Octave."""
    return int(valeurs.get(TRANSPO, 0)) + 12 * (int(valeurs.get(OCTAVE, OCTAVE_ZERO)) - OCTAVE_ZERO)


def vers_transpo_octave(d: int) -> dict | None:
    """Le decalage d en reglages Transpose (0..11) et Octave (0..5), ou None."""
    octave = OCTAVE_ZERO + d // 12
    if not 0 <= octave <= 5:
        return None
    return {TRANSPO: d % 12, OCTAVE: octave}


def decalage_chromatique(beams) -> int | None:
    """Si chaque rayon joue sa note d'origine decalee d'un meme nombre de demi-tons
    (une gamme chromatique continue, sans accord), ce decalage ; sinon None."""
    if not beams:
        return None
    ecarts = {b.note - b.incoming_note for b in beams}
    if len(ecarts) != 1 or any(getattr(b, "extra", None) for b in beams):
        return None
    return ecarts.pop()


class Copie:
    """La copie du contenu des presets, gardee dans presets_harpe.json (a cote des
    morceaux), avec les anciens contenus des presets ecrases par Kromabridge."""

    def __init__(self, fichier: Path | None) -> None:
        self.fichier = fichier
        self.donnees = {"presets": {}, "sauvegardes": []}
        if fichier is not None and fichier.exists():
            try:
                self.donnees.update(json.loads(fichier.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                pass

    def _ecrire(self) -> None:
        if self.fichier is not None:
            self.fichier.write_text(json.dumps(self.donnees, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")

    def get(self, n: int) -> dict | None:
        return self.donnees["presets"].get(str(n))

    def mettre(self, n: int, valeurs: dict) -> dict:
        entree = {"valeurs": dict(valeurs), "lu": datetime.now().strftime("%d/%m/%Y %H:%M")}
        self.donnees["presets"][str(n)] = entree
        self._ecrire()
        return entree

    def garder_ancien(self, n: int, valeurs: dict) -> None:
        s = self.donnees["sauvegardes"]
        s.insert(0, {"preset": n, "date": datetime.now().strftime("%d/%m/%Y %H:%M"), "valeurs": dict(valeurs)})
        del s[SAUVEGARDES_MAX:]
        self._ecrire()

    def semble_programme(self, n: int) -> bool:
        """Vrai si le contenu (connu) du preset n differe de celui que partagent la
        plupart des presets lus : ceux restes aux reglages d'usine sont identiques."""
        presets = self.donnees["presets"]
        if str(n) not in presets or len(presets) < 5:
            return False
        cles = [json.dumps(e["valeurs"], sort_keys=True) for e in presets.values()]
        commun = max(set(cles), key=cles.count)
        if cles.count(commun) < 3:
            return False
        return json.dumps(presets[str(n)]["valeurs"], sort_keys=True) != commun

    def resume(self) -> dict:
        return {n: e["lu"] for n, e in self.donnees["presets"].items()}

    def programmes(self) -> list[int]:
        return [int(n) for n in self.donnees["presets"] if self.semble_programme(int(n))]
