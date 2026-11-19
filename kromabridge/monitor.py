"""Le moniteur MIDI : ce que la harpe envoie, decode et lisible.

Lit le flux brut du port USB de la harpe (avec le "running status" du MIDI),
garde les derniers messages et les decrit en francais. Sert a verifier les
reglages qui ne se voient pas sur les rayons : modulation par la main, pedale
analogique, transposition, notes parasites.
"""

from __future__ import annotations

import threading
import time
from collections import deque

NOTE_NAMES = ["do", "do#", "ré", "ré#", "mi", "fa", "fa#", "sol", "sol#", "la", "la#", "si"]

# Nombre d'octets de donnees par type de message de canal.
_DATA_LEN = {0x80: 2, 0x90: 2, 0xA0: 2, 0xB0: 2, 0xC0: 1, 0xD0: 1, 0xE0: 2}

_CC_NAMES = {1: "molette", 7: "volume", 64: "sustain", 120: "tout son coupé",
             121: "remise à zéro des contrôleurs", 123: "toutes notes coupées"}

_REALTIME = {0xF8: "horloge", 0xFA: "Start", 0xFB: "Continue", 0xFC: "Stop",
             0xFE: "active sensing", 0xFF: "reset"}


def note_name(n: int) -> str:
    return f"{NOTE_NAMES[n % 12]}{n // 12 - 1}"


def describe(msg: bytes) -> str:
    st = msg[0]
    if st in _REALTIME:
        return f"Temps réel : {_REALTIME[st]}"
    if st == 0xF0:
        return "Sysex (" + " ".join(f"{b:02X}" for b in msg[:6]) + (" ...)" if len(msg) > 6 else ")")
    kind, canal = st & 0xF0, (st & 0x0F) + 1
    if kind == 0x90 and msg[2] > 0:
        return f"Note jouée {note_name(msg[1])} ({msg[1]}), vélocité {msg[2]}, canal {canal}"
    if kind in (0x80, 0x90):
        return f"Note relâchée {note_name(msg[1])} ({msg[1]}), canal {canal}"
    if kind == 0xB0:
        nom = _CC_NAMES.get(msg[1])
        return f"Contrôleur {msg[1]}" + (f" ({nom})" if nom else "") + f" = {msg[2]}, canal {canal}"
    if kind == 0xE0:
        valeur = (msg[2] << 7 | msg[1]) - 8192
        return f"Pitch bend {valeur:+d}, canal {canal}"
    if kind == 0xD0:
        return f"Pression {msg[1]}, canal {canal}"
    if kind == 0xA0:
        return f"Pression {note_name(msg[1])} = {msg[2]}, canal {canal}"
    if kind == 0xC0:
        return f"Changement de programme {msg[1] + 1}, canal {canal}"
    return "Message " + " ".join(f"{b:02X}" for b in msg)


class MidiMonitor:
    def __init__(self, size: int = 2000, clock=time.monotonic) -> None:
        self._entries: deque = deque(maxlen=size)
        self._seq = 0
        self._lock = threading.Lock()
        self._clock = clock
        self._start = clock()
        self._status: int | None = None
        self._data: list[int] = []
        self._sysex: bytearray | None = None
        # Appele (hors verrou) pour chaque message de canal complet : c'est par
        # la que les notes de la harpe rejoignent le moteur du boitier.
        self.on_message = None

    def feed(self, data: bytes) -> None:
        for b in data:
            if b >= 0xF8:                       # temps reel : peut arriver a tout moment
                self._add(bytes([b]))
                continue
            if b == 0xF0:
                self._sysex = bytearray([b])
                continue
            if b == 0xF7:
                if self._sysex is not None:
                    self._sysex.append(b)
                    self._add(bytes(self._sysex))
                self._sysex = None
                continue
            if self._sysex is not None:
                if not b & 0x80:
                    self._sysex.append(b)
                    continue
                self._sysex = None              # sysex interrompu par un nouveau statut
            if b & 0x80:
                # statut de canal, ou message systeme commun (ignore)
                self._status = b if b < 0xF0 else None
                self._data = []
                continue
            if self._status is not None:
                self._data.append(b)
                if len(self._data) == _DATA_LEN[self._status & 0xF0]:
                    msg = bytes([self._status, *self._data])
                    self._data = []             # running status : meme statut ensuite
                    if self.on_message is not None:
                        self.on_message(list(msg))   # d'abord le son, ensuite l'affichage
                    self._add(msg)

    def _add(self, msg: bytes, prefix: str = "") -> None:
        with self._lock:
            # La pedale envoie sans cesse la meme valeur : on ne garde qu'une
            # ligne (sinon elle chassait tout le reste du journal).
            if (not prefix and self._entries and self._entries[-1]["octets"]
                    == " ".join(f"{b:02X}" for b in msg) and msg[0] & 0xF0 == 0xB0):
                self._entries[-1]["t"] = round(self._clock() - self._start, 3)
                return
            self._seq += 1
            self._entries.append({
                "n": self._seq,
                "t": round(self._clock() - self._start, 3),
                "octets": " ".join(f"{b:02X}" for b in msg),
                "texte": prefix + describe(msg),
            })

    def to_harp(self, message) -> None:
        """Note une commande envoyee a la harpe (hors relances)."""
        self._add(bytes(message), prefix="-> harpe : ")

    def sent(self, messages) -> None:
        """Note ce que le boitier envoie au synthe, pour le voir dans le moniteur."""
        for msg in messages:
            self._add(bytes(msg), prefix="-> synthé : ")

    def since(self, n: int = 0) -> dict:
        with self._lock:
            return {"dernier": self._seq, "messages": [e for e in self._entries if e["n"] > n]}

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
