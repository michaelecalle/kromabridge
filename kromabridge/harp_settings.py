"""Les reglages de la harpe, tels que les envoie le Kromalaser Manager V24.

Releve par analyse du programme V24 (13/11/2026) : chaque reglage est un Control
Change sur le canal 1, `B0 <cc> <valeur>`, ou la valeur est
`ceil(v * 127 / plage)` avec `v` compris entre 0 et `plage`.
Les commandes de presets et de lecture sont des sysex `F0 7B 01 ...`.
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Setting:
    key: str            # nom technique, stable
    label: str          # nom affiche
    tab: str            # onglet de la page
    cc: int
    span: int           # v va de 0 a span
    offset: int = 0     # valeur affichee = v + offset (ex. nombre de rayons = v + 5)
    choices: tuple[str, ...] = field(default=())  # liste a choix (v = rang)
    toggle: bool = False                          # case a cocher (v = 0/1)
    label_fr: str = ""                            # nom en francais (affiche en grand)
    choices_fr: tuple[str, ...] = field(default=())
    # Choix ajoutes par l'application (au-dela de ceux de la harpe) : valeur
    # affichee -> valeur envoyee a la harpe. Ex. "Sustain (un rayon a la fois)"
    # = Sustain pour la harpe, la difference etant faite par l'application.
    alias: dict = field(default_factory=dict)

    @property
    def harp_span(self) -> int:
        return self.span - len(self.alias)

    def harp_value(self, v: int) -> int:
        return self.alias.get(v, v)

    def cc_value(self, v: int) -> int:
        if not 0 <= v <= self.span:
            raise ValueError(f"{self.label} : valeur {v} hors de 0..{self.span}")
        return math.ceil(self.harp_value(v) * 127 / self.harp_span) & 0x7F

    def message(self, v: int) -> list[int]:
        return [0xB0, self.cc, self.cc_value(v)]

    def describe(self) -> dict:
        d = {"cle": self.key, "nom": self.label_fr or self.label, "nom_manager": self.label,
             "onglet": self.tab, "max": self.span, "decalage": self.offset}
        if self.choices:
            d["choix"] = list(self.choices_fr or self.choices)
            d["choix_manager"] = list(self.choices)
        if self.toggle:
            d["case"] = True
        return d


# Noms, onglets et choix exactement tels qu'ils s'affichent dans le Kromalaser
# Manager V24 (extraits de son formulaire le 13/11/2026), pour que Michael
# retrouve les memes reperes. Seule exception : la liste de l'onglet Colors n'a
# pas de titre dans le Manager.
MODES = ("NONE", "ALL", "TONE", "HALF TONE", "TRIGGED", "UNTRIGGED")
DESTINATIONS = ("NONE", "VOLUME", "BENDER", "WHEEL", "SUSTAIN")
PEDALE = ("OCTAVE-1", "OCTAVE+1", "HALFTONE-1", "HALFTONE+1", "START/STOP", "PRESET-", "PRESET+")

# Traductions (meme sens ; l'original du Manager reste affiche en petit a cote).
MODES_FR = ("Aucun", "Tous", "Tons", "Demi-tons", "Touchés", "Non touchés")
DESTINATIONS_FR = ("Aucune", "Volume", "Pitch bend", "Molette", "Sustain")
PEDALE_FR = ("Octave -1", "Octave +1", "Demi-ton -1", "Demi-ton +1", "Ouvrir/fermer", "Preset -", "Preset +")

SETTINGS: tuple[Setting, ...] = (
    Setting("gain", "Sensor Gain", "System", 18, 75, offset=25, label_fr="Gain du capteur"),
    Setting("mode_r", "Beam Mode : Red", "ColorsOld", 15, 5, choices=MODES, choices_fr=MODES_FR, label_fr="Mode des rayons : rouge"),
    Setting("mode_v", "Beam Mode : Green", "ColorsOld", 16, 5, choices=MODES, choices_fr=MODES_FR, label_fr="Mode des rayons : vert"),
    Setting("mode_b", "Beam Mode : Blue", "ColorsOld", 17, 5, choices=MODES, choices_fr=MODES_FR, label_fr="Mode des rayons : bleu"),
    Setting("puissance_r", "Red Power", "ColorsOld", 12, 100, label_fr="Puissance rouge"),
    Setting("puissance_v", "Green Power", "ColorsOld", 13, 100, label_fr="Puissance verte"),
    Setting("puissance_b", "Blue Power", "ColorsOld", 14, 100, label_fr="Puissance bleue"),
    Setting("ttl", "TTL OUT", "System", 19, 1, toggle=True, label_fr="Sortie TTL"),
    Setting("tout_module", "All Modulated", "System", 39, 1, toggle=True, label_fr="Tous les rayons modulés"),
    Setting("main", "Hand Modulation to", "Footsw", 21, 6, choices=DESTINATIONS + ("OCTAVE", "VELOCITY"), choices_fr=DESTINATIONS_FR + ("Octave", "Vélocité"), label_fr="Modulation par la main vers"),
    Setting("pedale_analogique", "Analog Pedal to", "Footsw", 22, 5, choices=DESTINATIONS + ("SUSTAIN - 1 BEAM",),
            choices_fr=DESTINATIONS_FR[:4] + ("Sustain (toutes les notes)", "Sustain (un rayon à la fois)"),
            label_fr="Pédale analogique vers", alias={5: 4}),
    Setting("pedale_1", "FootSwitches : 1", "Footsw", 31, 6, choices=PEDALE, choices_fr=PEDALE_FR, label_fr="Pédalier : pédale 1"),
    Setting("pedale_2", "FootSwitches : 2", "Footsw", 33, 6, choices=PEDALE, choices_fr=PEDALE_FR, label_fr="Pédalier : pédale 2"),
    Setting("pedale_3", "FootSwitches : 3", "Footsw", 34, 6, choices=PEDALE, choices_fr=PEDALE_FR, label_fr="Pédalier : pédale 3"),
    Setting("pedale_4", "FootSwitches : 4", "Footsw", 35, 6, choices=PEDALE, choices_fr=PEDALE_FR, label_fr="Pédalier : pédale 4"),
    Setting("rayons", "Strings", "Beams", 45, 10, offset=5, label_fr="Nombre de rayons"),
    Setting("balayage", "Scanrate", "Beams", 41, 95, offset=5, label_fr="Vitesse de balayage"),
    Setting("largeur", "X Wide", "Beams", 42, 90, offset=10, label_fr="Largeur X"),
    Setting("z_decalage", "Z Offset", "Beams", 44, 100, label_fr="Décalage Z"),
    Setting("z_profondeur", "Z Depth", "Beams", 43, 100, label_fr="Profondeur Z"),
    Setting("z_mode", "Z Mode", "Beams", 48, 1, choices=("Offset", "** 3D **"), choices_fr=("Décalage", "3D"), label_fr="Mode Z"),
    Setting("miroir", "Mirror Mode", "Beams", 47, 3, choices=("None", "L <-> R", "Z Mirror", "BOTH"), choices_fr=("Aucun", "Gauche <-> droite", "Miroir Z", "Les deux"), label_fr="Mode miroir"),
    Setting("delai", "Turn On Delay", "Beams", 46, 35, offset=5, label_fr="Délai d'allumage"),
    Setting("auto_couleur", "(liste sans titre dans le Manager)", "Colors", 54, 5, choices=MODES, choices_fr=MODES_FR, label_fr="Modulation de couleur sur"),
    Setting("mod_vitesse", "Modulation Speed", "Colors", 51, 49, offset=1, label_fr="Modulation : vitesse"),
    Setting("mod_profondeur", "Modulation Depth", "Colors", 52, 85, offset=5, label_fr="Modulation : profondeur"),
    Setting("mod_phase", "Modulation Phase", "Colors", 53, 50, label_fr="Modulation : phase"),
    Setting("rideau", "Curtain", "Colors", 55, 1, toggle=True, label_fr="Rideau"),
    Setting("transposition", "Transpose", "Transpose", 61, 11, label_fr="Transposition"),
    Setting("octave", "Octave", "Transpose", 62, 5, offset=-3, label_fr="Octave"),
)

BY_KEY = {s.key: s for s in SETTINGS}

# Ou lire chaque reglage dans la reponse `F0 7B 01 70 ...` a la lecture
# (octet compte depuis F0 = 0), et ce qu'on retranche a l'octet brut pour
# obtenir `v`. Releve dans le Manager V24 (fonction 0x406154), verifie sur le
# releve de la harpe de Michael le 13/11/2026. Les cases valent 1 si l'octet
# n'est pas nul.
DUMP_LAYOUT: dict[str, tuple[int, int]] = {
    "gain": (4, 25),
    "mode_r": (5, 0), "mode_v": (6, 0), "mode_b": (7, 0),
    "puissance_r": (8, 0), "puissance_v": (9, 0), "puissance_b": (10, 0),
    "ttl": (11, 0),
    "main": (13, 0), "pedale_analogique": (14, 0),
    "tout_module": (19, 0),
    "pedale_1": (20, 0), "pedale_2": (21, 0), "pedale_3": (22, 0), "pedale_4": (23, 0),
    "transposition": (24, 0),
    "octave": (25, 2),
    "rayons": (28, 5), "balayage": (29, 5), "largeur": (30, 10),
    "delai": (33, 5),
    "z_profondeur": (34, 0), "z_mode": (35, 0), "z_decalage": (36, 0),
    "miroir": (37, 0), "auto_couleur": (38, 0),
    "mod_vitesse": (39, 1), "mod_profondeur": (40, 5), "mod_phase": (41, 0),
    "rideau": (42, 0),
}


def parse_dump(msg: bytes) -> dict[str, int]:
    """Les reglages courants de la harpe, a partir de sa reponse a READ_ALL."""
    if len(msg) < 44 or msg[0] != 0xF0 or msg[3] != 0x70:
        raise ValueError("reponse de lecture des reglages invalide")
    valeurs = {}
    for key, (pos, moins) in DUMP_LAYOUT.items():
        s = BY_KEY[key]
        brut = msg[pos]
        if s.toggle:
            v = 1 if brut - moins else 0
        else:
            v = min(max(brut - moins, 0), s.span)
        valeurs[key] = v
    return valeurs

READ_ALL = [0xF0, 0x7B, 0x01, 0x78, 0xF7]
VERSION = [0xF0, 0x7B, 0x01, 0x79, 0xF7]
# Seul firmware avec lequel Kromabridge a ete teste (le plus recent du fabricant, 2017).
FIRMWARE_TESTE = "1.440"
FACTORY_RESET = [0xB0, 60, 127]
PRESET_MIN, PRESET_MAX = 1, 40


def save_preset(n: int) -> list[int]:
    _check_preset(n)
    return [0xF0, 0x7B, 0x01, 0x72, n, 0xF7]


def recall_preset(n: int) -> list[int]:
    _check_preset(n)
    return [0xF0, 0x7B, 0x01, 0x74, n, 0xF7]


def _check_preset(n: int) -> None:
    if not PRESET_MIN <= n <= PRESET_MAX:
        raise ValueError(f"preset {n} hors de {PRESET_MIN}..{PRESET_MAX}")


def parse_version(msg: bytes) -> str | None:
    """`F0 7B 01 06 7A a b c d F7` -> "a.bcd" (comme le Manager V24)."""
    if len(msg) >= 9 and msg[0] == 0xF0 and msg[3] == 0x06 and msg[4] == 0x7A:
        return f"{msg[5]}.{msg[6]}{msg[7]}{msg[8]}"
    return None


class SysexCollector:
    """Reassemble les sysex dans le flux brut lu sur la harpe."""

    def __init__(self) -> None:
        self._buf: bytearray | None = None
        self._lock = threading.Condition()
        self.last: dict[int, bytes] = {}   # dernier sysex recu, par octet de commande
        # Compteur par commande : une reponse identique a la precedente (memes
        # reglages) doit quand meme compter comme une nouvelle reponse.
        self._count: dict[int, int] = {}

    def feed(self, data: bytes) -> None:
        for b in data:
            if b == 0xF0:
                self._buf = bytearray([b])
            elif self._buf is not None:
                if b >= 0xF8:          # temps reel : peut s'intercaler, on l'ignore
                    continue
                if b & 0x80 and b != 0xF7:
                    self._buf = None   # message interrompu
                    continue
                self._buf.append(b)
                if b == 0xF7:
                    msg = bytes(self._buf)
                    self._buf = None
                    if len(msg) >= 5:
                        with self._lock:
                            self.last[msg[3]] = msg
                            self._count[msg[3]] = self._count.get(msg[3], 0) + 1
                            self._lock.notify_all()

    def wait(self, command: int, after: dict | None = None, timeout: float = 1.5) -> bytes | None:
        """Attend une reponse dont l'octet 3 vaut `command`, arrivee apres `after`."""
        avant = (after or {}).get(command, 0)
        with self._lock:
            ok = self._lock.wait_for(lambda: self._count.get(command, 0) > avant, timeout)
            return self.last.get(command) if ok else None

    def snapshot(self) -> dict:
        """Les compteurs actuels, a passer ensuite a wait(after=...)."""
        with self._lock:
            return dict(self._count)


# Message non documente trouve dans le firmware V1.440 (18/11/2026) :
# F0 7B 01 71 + 40 octets ecrit TOUS les reglages en valeurs internes, sans la mise
# a l'echelle des CC (meme format que la reponse 70 a READ_ALL ; dernier octet =
# XOR des 39 premiers, sur 7 bits). Il permet une largeur X sous 10 % (0 = tous
# les rayons fusionnes). Le nombre de rayons reste au moins 5 (la harpe corrige).
# La harpe l'enregistre aussi dans sa memoire : a n'envoyer que ponctuellement.
OCTET_LARGEUR = 26


def write_all(donnees: list[int]) -> list[int]:
    """Le message qui ecrit les 40 octets de reglages (controle recalcule)."""
    d = [int(v) & 0x7F for v in donnees[:40]]
    if len(d) != 40:
        raise ValueError("il faut 40 octets de réglages")
    x = 0
    for v in d[:39]:
        x = (x ^ v) & 0x7F
    d[39] = x
    return [0xF0, 0x7B, 0x01, 0x71] + d + [0xF7]
