"""Ecoute d'une ou plusieurs pistes d'un fichier MIDI importe, sur le synthe
(ou le logiciel de musique), sur le canal du morceau affiche."""

from __future__ import annotations

import threading
import time

from .midifile import Fichier


class Lecteur:
    def __init__(self, envoyer, horloge=time.monotonic) -> None:
        self.envoyer = envoyer
        self.horloge = horloge
        self._arret = threading.Event()
        self._fil: threading.Thread | None = None
        self._depart = 0.0
        self._debut = 0.0
        self.pistes: list[int] = []
        self.en_cours = False

    def position(self) -> float | None:
        if not self.en_cours:
            return None
        return self._debut + (self.horloge() - self._depart)

    def jouer(self, fichier: Fichier, pistes: list[int], debut: float, canal: int,
              fin: float | None = None) -> None:
        """Joue les pistes (index a partir de 1) de `debut` a `fin` secondes."""
        self.arreter()
        evenements = []
        for p in fichier.pistes:
            if p.index not in pistes:
                continue
            for d, f, note, _ in p.segments:
                sd, sf = fichier.secondes(d), fichier.secondes(f)
                if sf <= debut or (fin is not None and sd >= fin):
                    continue
                evenements.append((max(sd, debut), 1, note))
                evenements.append((sf if fin is None else min(sf, fin), 0, note))
        # a temps egal, les fins avant les debuts (note rejouee aussitot)
        evenements.sort(key=lambda e: (e[0], e[1]))
        self._arret = threading.Event()
        self.pistes = list(pistes)
        self._debut = debut
        self._depart = self.horloge()
        self.en_cours = True
        arret = self._arret
        self._fil = threading.Thread(target=self._boucle, args=(evenements, canal - 1, arret),
                                     daemon=True)
        self._fil.start()

    def _boucle(self, evenements, canal: int, arret: threading.Event) -> None:
        sonnent: dict[int, int] = {}
        try:
            for t, allume, note in evenements:
                attente = (t - self._debut) - (self.horloge() - self._depart)
                if attente > 0 and arret.wait(attente):
                    break
                if arret.is_set():
                    break
                if allume:
                    sonnent[note] = sonnent.get(note, 0) + 1
                    self.envoyer([[0x90 | canal, note, 100]])
                elif sonnent.get(note):
                    sonnent[note] -= 1
                    self.envoyer([[0x80 | canal, note, 0]])
        finally:
            fins = [[0x80 | canal, n, 0] for n, k in sonnent.items() for _ in range(k)]
            if fins:
                self.envoyer(fins)
            if self._arret is arret:
                self.en_cours = False

    def arreter(self) -> None:
        self._arret.set()
        if self._fil is not None and self._fil is not threading.current_thread():
            self._fil.join(timeout=1)
        self._fil = None
        self.en_cours = False
