"""Piste de controle : un sequenceur (Cubase...) pilote la harpe par MIDI.

Convention, sur le canal 16 uniquement :
- changement de programme n (0 = premier) : charger le n-ieme morceau ;
- note 0 (do-1) : ouvrir la harpe ; note 1 (do#-1) : la fermer ;
- note 12 + k (do0 = A, do#0 = B...) : passer a la partie k du morceau.
"""

from __future__ import annotations

import struct

from .keyboard import KeyboardListener

CANAL = 16
NOTE_OUVRIR = 0
NOTE_FERMER = 1
NOTE_PARTIE_A = 12
# Sur PC : le second port loopMIDI, distinct de celui ou part la harpe.
PORT_PC = "controle harpe"


class ControlListener(KeyboardListener):
    """Ecoute le port de controle en permanence et transmet tout message."""

    def __init__(self, hint_getter, on_message, retry: float = 1.0, factory=None) -> None:
        super().__init__(hint_getter, lambda note: None, retry=retry, factory=factory)
        self.on_raw = on_message
        # Le port de controle est justement un de ceux qu'on cache aux claviers.
        self.exclus = ("kromalaser", "kromabridge", "rtmidi", "midi through")

    def _callback(self, event, _data=None) -> None:
        message, _delta = event
        if message:
            self.on_raw(list(message))


def _varlen(v: int) -> bytes:
    out = [v & 0x7F]
    v >>= 7
    while v:
        out.insert(0, (v & 0x7F) | 0x80)
        v >>= 7
    return bytes(out)


def piste_exemple(nom: str, index_morceau: int, parties: int) -> bytes:
    """Un petit fichier .mid (format 0) pret a glisser dans Cubase : charger le
    morceau, l'ouvrir, une note par partie (une mesure chacune), le fermer."""
    division = 480
    mesure = division * 4
    ev: list[tuple[int, bytes]] = []
    titre = ("Controle Harpe - " + nom).encode("latin-1", "replace")
    ev.append((0, b"\xff\x03" + _varlen(len(titre)) + titre))
    ev.append((0, b"\xff\x51\x03\x07\xa1\x20"))                      # 120 a la noire
    ev.append((0, bytes([0xC0 | (CANAL - 1), index_morceau & 0x7F])))
    def note(t, n):
        ev.append((t, bytes([0x90 | (CANAL - 1), n, 100])))
        ev.append((t + division // 4, bytes([0x80 | (CANAL - 1), n, 0])))
    # Assez d'ecart pour laisser une animation d'ouverture se terminer :
    # ouverture au 2e temps, une partie toutes les 4 mesures, fermeture 4 mesures apres.
    note(division, NOTE_OUVRIR)
    for k in range(parties):
        note(mesure * (4 * k + 2), NOTE_PARTIE_A + k)
    note(mesure * (4 * max(parties, 1) + 2), NOTE_FERMER)
    ev.sort(key=lambda e: e[0])
    data, prec = b"", 0
    for t, m in ev:
        data += _varlen(t - prec) + m
        prec = t
    data += b"\x00\xff\x2f\x00"
    return (b"MThd" + struct.pack(">IHHH", 6, 0, 1, division) +
            b"MTrk" + struct.pack(">I", len(data)) + data)


def piste_commandes(fichier, commandes: list[dict], index_morceau: int, nom: str,
                    decalage: int = 0) -> bytes:
    """Une piste de controle calee sur `fichier` (meme division, tempos et
    mesures). `commandes` : [{"type": "morceau" | "ouvrir" | "fermer" | "partie",
    "partie": k, "mesure": m, "temps": t}, ...], en mesures du projet Cubase.
    `decalage` : nombre de mesures ajoutees avant le morceau dans le projet
    (le morceau commence a la mesure 1 + decalage) ; les tempos et mesures du
    fichier sont decales d'autant."""
    division = fichier.division
    decalage = max(0, int(decalage))
    # mesures ajoutees au debut : dans la signature de depart du fichier
    sig0 = sorted(fichier.signatures)[0] if fichier.signatures else (0, 4, 4)
    avance = decalage * max(1, sig0[1] * division * 4 // sig0[2])

    def position(mesure: int, temps: float) -> int:
        if mesure > decalage:
            return avance + fichier.position(mesure - decalage, temps)
        return (mesure - 1) * max(1, sig0[1] * division * 4 // sig0[2]) + \
            round((max(1.0, temps) - 1) * division * 4 / sig0[2])

    ev: list[tuple[int, int, bytes]] = []      # (temps, ordre, octets)
    titre = ("Controle Harpe - " + nom).encode("latin-1", "replace")
    ev.append((0, 0, b"\xff\x03" + _varlen(len(titre)) + titre))
    tempos = fichier.tempos or [(0, 500000)]
    if avance and tempos[0][0] > 0:
        tempos = [(0, 500000)] + list(tempos)
    for i, (t, us) in enumerate(tempos):
        ev.append((0 if (i == 0 and avance) else t + avance, 1, b"\xff\x51\x03" + us.to_bytes(3, "big")))
    sigs = fichier.signatures or ([(0, 4, 4)] if avance else [])
    for i, (t, num, den) in enumerate(sigs):
        ev.append((0 if (i == 0 and avance) else t + avance, 1,
                   bytes([0xFF, 0x58, 0x04, num, max(0, den.bit_length() - 1), 24, 8])))
    c = CANAL - 1
    for cmd in commandes:
        t = position(int(cmd.get("mesure", 1)), float(cmd.get("temps", 1)))
        genre = cmd.get("type")
        if genre == "morceau":
            ev.append((t, 2, bytes([0xC0 | c, index_morceau & 0x7F])))
            continue
        if genre == "ouvrir":
            n = NOTE_OUVRIR
        elif genre == "fermer":
            n = NOTE_FERMER
        elif genre == "partie":
            n = NOTE_PARTIE_A + int(cmd.get("partie", 0))
        else:
            continue
        ev.append((t, 3, bytes([0x90 | c, n, 100])))
        ev.append((t + max(1, division // 4), 2, bytes([0x80 | c, n, 0])))
    ev.sort(key=lambda e: (e[0], e[1]))
    data, prec = b"", 0
    for t, _o, m in ev:
        data += _varlen(t - prec) + m
        prec = t
    data += b"\x00\xff\x2f\x00"
    return (b"MThd" + struct.pack(">IHHH", 6, 0, 1, division) +
            b"MTrk" + struct.pack(">I", len(data)) + data)
