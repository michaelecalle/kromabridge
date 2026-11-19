"""Le mode apprentissage.

Le boitier ne sait pas quel rayon correspond a quelle note tant qu'on ne le lui a
pas montre. Plutot que de deviner, on coupe les rayons un par un, de gauche a
droite, et le boitier note ce qu'il recoit : la premiere note entendue devient le
rayon 1, la suivante le rayon 2, et ainsi de suite.

La harpe doit rester sur la meme configuration de gamme ensuite, sinon les notes
qu'elle envoie changent et la correspondance ne veut plus rien dire.
"""

from __future__ import annotations

from .engine import NOTE_ON, SYSTEM
from .preset import Beam, Preset


class Learner:
    """Enregistre l'ordre d'apparition des notes entrantes."""

    def __init__(self) -> None:
        self.notes: list[int] = []

    def feed(self, message) -> bool:
        """Donne un message entrant. Renvoie True si un rayon vient d'etre appris."""
        data = list(message)
        if len(data) < 3 or data[0] >= SYSTEM:
            return False
        if data[0] & 0xF0 != NOTE_ON or data[2] == 0:
            return False
        note = data[1]
        if note in self.notes:
            return False
        self.notes.append(note)
        return True

    def undo(self) -> int | None:
        """Annule le dernier rayon appris, si on s'est trompe."""
        return self.notes.pop() if self.notes else None

    def reset(self) -> None:
        self.notes.clear()

    @property
    def count(self) -> int:
        return len(self.notes)

    def to_preset(self, name: str = "Apprentissage", **kwargs) -> Preset:
        """Une memoire ou chaque rayon rejoue d'abord la note qu'il envoie.

        C'est le point de depart : la harpe sonne comme avant, et il n'y a plus
        qu'a changer les notes une par une.
        """
        beams = [
            Beam(index=i + 1, incoming_note=note, note=note)
            for i, note in enumerate(self.notes)
        ]
        return Preset(name=name, beams=beams, **kwargs)
