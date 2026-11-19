"""Lecture d'un fichier MIDI standard (.mid), juste ce qu'il faut pour l'import :
le nom de chaque piste et les notes qu'elle joue.

Sans bibliotheque externe : le boitier n'a que Python et rtmidi.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field


class MidiFileError(ValueError):
    pass


@dataclass
class Piste:
    index: int
    nom: str = ""
    # (debut en temps MIDI, note, canal 1..16)
    notes: list[tuple[int, int, int]] = field(default_factory=list)
    # (debut, fin) en temps MIDI, note, canal : pour l'apercu et l'ecoute
    segments: list[tuple[int, int, int, int]] = field(default_factory=list)
    # changements de tempo trouves dans la piste : (temps, microsecondes par noire)
    tempos: list[tuple[int, int]] = field(default_factory=list)
    # changements de signature : (temps, numerateur, denominateur)
    signatures: list[tuple[int, int, int]] = field(default_factory=list)

    @property
    def notes_distinctes(self) -> list[int]:
        return sorted({n for _, n, _ in self.notes})

    @property
    def canaux(self) -> list[int]:
        return sorted({c for _, _, c in self.notes})

    def resume(self) -> dict:
        d = self.notes_distinctes
        return {"index": self.index, "nom": self.nom, "nombre": len(self.notes),
                "distinctes": d, "canaux": self.canaux,
                "grave": d[0] if d else None, "aigue": d[-1] if d else None}


def _varlen(data: bytes, pos: int) -> tuple[int, int]:
    valeur = 0
    for _ in range(4):
        if pos >= len(data):
            raise MidiFileError("fichier MIDI tronqué")
        b = data[pos]
        pos += 1
        valeur = (valeur << 7) | (b & 0x7F)
        if not b & 0x80:
            return valeur, pos
    raise MidiFileError("longueur variable invalide")


@dataclass
class Fichier:
    pistes: list[Piste]
    division: int
    tempos: list[tuple[int, int]]
    signatures: list[tuple[int, int, int]] = field(default_factory=list)

    def secondes(self, temps: int) -> float:
        """Le moment, en secondes, d'un temps MIDI (carte des tempos comprise)."""
        t_prec, us_prec, total = 0, 500000, 0.0
        for t, us in self.tempos:
            if t >= temps:
                break
            total += (t - t_prec) * us_prec / self.division / 1e6
            t_prec, us_prec = t, us
        return total + (temps - t_prec) * us_prec / self.division / 1e6

    def duree(self) -> float:
        fin = max((f for p in self.pistes for _, f, _, _ in p.segments), default=0)
        return self.secondes(fin)

    def mesures(self) -> list[float]:
        """Le debut de chaque mesure, en secondes (mesure 1 = premier element),
        jusqu'a la fin du morceau, plus la fin de la derniere mesure."""
        fin = max((f for p in self.pistes for _, f, _, _ in p.segments), default=0)
        sigs = sorted(self.signatures) or [(0, 4, 4)]
        if sigs[0][0] > 0:
            sigs.insert(0, (0, 4, 4))
        debuts, t, i = [], 0, 0
        while True:
            while i + 1 < len(sigs) and sigs[i + 1][0] <= t:
                i += 1
            debuts.append(round(self.secondes(t), 3))
            if t > fin or len(debuts) > 5000:
                break
            _, num, den = sigs[i]
            t += max(1, num * self.division * 4 // den)
        return debuts

    def temps_mesures(self) -> list[int]:
        """Le nombre de temps de chaque mesure (4 en 4/4, 2 en 2/4...), dans
        le meme ordre que mesures() (une valeur par mesure)."""
        fin = max((f for p in self.pistes for _, f, _, _ in p.segments), default=0)
        sigs = sorted(self.signatures) or [(0, 4, 4)]
        if sigs[0][0] > 0:
            sigs.insert(0, (0, 4, 4))
        out, t, i = [], 0, 0
        while True:
            while i + 1 < len(sigs) and sigs[i + 1][0] <= t:
                i += 1
            _, num, den = sigs[i]
            if t > fin or len(out) > 5000:
                break
            out.append(max(1, num))
            t += max(1, num * self.division * 4 // den)
        return out

    def position(self, mesure: int, temps: float = 1.0) -> int:
        """Le temps MIDI du debut d'un temps (battement) dans une mesure (a partir de 1)."""
        sigs = sorted(self.signatures) or [(0, 4, 4)]
        if sigs[0][0] > 0:
            sigs.insert(0, (0, 4, 4))
        t, i = 0, 0
        for _ in range(max(0, mesure - 1)):
            while i + 1 < len(sigs) and sigs[i + 1][0] <= t:
                i += 1
            _, num, den = sigs[i]
            t += max(1, num * self.division * 4 // den)
        while i + 1 < len(sigs) and sigs[i + 1][0] <= t:
            i += 1
        _, num, den = sigs[i]
        return t + round((max(1.0, temps) - 1) * self.division * 4 / den)

    def apercu(self, piste: Piste, maximum: int = 4000) -> list[list[float]]:
        """[debut s, fin s, note] pour dessiner la piste."""
        return [[round(self.secondes(d), 3), round(self.secondes(f), 3), n]
                for d, f, n, _ in piste.segments[:maximum]]


def analyser(data: bytes) -> Fichier:
    pistes = lire(data)
    division = struct.unpack(">H", data[12:14])[0]
    if division & 0x8000 or division == 0:
        division = 480          # format SMPTE, rare : on suppose 480 par noire a 120
    tempos = sorted(t for p in pistes for t in p.tempos)
    signatures = sorted(t for p in pistes for t in p.signatures)
    return Fichier(pistes, division, tempos, signatures)


def lire(data: bytes) -> list[Piste]:
    """Les pistes du fichier, dans l'ordre (index a partir de 1)."""
    if len(data) < 14 or data[:4] != b"MThd":
        raise MidiFileError("ce n'est pas un fichier MIDI (.mid)")
    longueur, _format, nb_pistes, _division = struct.unpack(">IHHH", data[4:14])
    pos = 8 + longueur
    pistes: list[Piste] = []
    while pos + 8 <= len(data) and len(pistes) < nb_pistes:
        genre, taille = data[pos:pos + 4], struct.unpack(">I", data[pos + 4:pos + 8])[0]
        pos += 8
        bloc = data[pos:pos + taille]
        pos += taille
        if genre != b"MTrk":
            continue
        pistes.append(_lire_piste(bloc, len(pistes) + 1))
    if not pistes:
        raise MidiFileError("aucune piste dans ce fichier MIDI")
    return pistes


def _lire_piste(bloc: bytes, index: int) -> Piste:
    piste = Piste(index=index)
    pos, temps, statut = 0, 0, None
    ouvertes: dict[tuple[int, int], list[int]] = {}
    while pos < len(bloc):
        delta, pos = _varlen(bloc, pos)
        temps += delta
        if pos >= len(bloc):
            break
        octet = bloc[pos]
        if octet == 0xFF:                       # meta
            genre = bloc[pos + 1]
            longueur, pos = _varlen(bloc, pos + 2)
            contenu = bloc[pos:pos + longueur]
            pos += longueur
            if genre == 0x03 and not piste.nom:
                piste.nom = contenu.decode("latin-1", "replace").strip()
            if genre == 0x58 and len(contenu) >= 2:
                piste.signatures.append((temps, contenu[0] or 4, 2 ** contenu[1]))
            if genre == 0x51 and len(contenu) == 3:
                piste.tempos.append((temps, int.from_bytes(contenu, "big")))
            if genre == 0x2F:
                break
            continue
        if octet in (0xF0, 0xF7):               # sysex
            longueur, pos = _varlen(bloc, pos + 1)
            pos += longueur
            continue
        if octet & 0x80:
            statut = octet
            pos += 1
        elif statut is None:
            raise MidiFileError("événement sans statut")
        genre = statut & 0xF0
        taille = 1 if genre in (0xC0, 0xD0) else 2
        donnees = bloc[pos:pos + taille]
        pos += taille
        canal = (statut & 0x0F) + 1
        if genre == 0x90 and len(donnees) == 2 and donnees[1] > 0:
            piste.notes.append((temps, donnees[0], canal))
            ouvertes.setdefault((canal, donnees[0]), []).append(temps)
        elif genre in (0x80, 0x90) and len(donnees) == 2:
            debuts = ouvertes.get((canal, donnees[0]))
            if debuts:
                piste.segments.append((debuts.pop(0), temps, donnees[0], canal))
    for (canal, note), debuts in ouvertes.items():      # notes jamais relachees
        for d in debuts:
            piste.segments.append((d, max(temps, d + 1), note, canal))
    piste.segments.sort()
    return piste
