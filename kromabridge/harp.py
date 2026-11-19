"""Commandes a destination de la harpe.

La harpe se pilote en MIDI standard sur le canal 1 : des Control Change pour ses
reglages, et des messages systeme exclusifs de la forme F0 7B 01 ... F7 pour ses
presets internes. Le detail est dans doc/protocole-kromalaser.md.

Ce module se contente de fabriquer les messages. Il n'ouvre aucun port, ce qui le
rend testable sans materiel.
"""

from __future__ import annotations

import math

SYSEX_START = 0xF0
SYSEX_END = 0xF7
MANUFACTURER = [0x7B, 0x01]

# Reglages de la harpe, par numero de Control Change.
CC_RED_POWER = 12
CC_GREEN_POWER = 13
CC_BLUE_POWER = 14
CC_RED_MODE = 15
CC_GREEN_MODE = 16
CC_BLUE_MODE = 17
CC_SENSOR_GAIN = 18
CC_TTL_MODULATION = 19
CC_HAND_MODULATION = 21
CC_ANALOG_PEDAL = 22
CC_PEDAL_1 = 31
CC_PEDAL_2 = 33
CC_PEDAL_3 = 34
CC_PEDAL_4 = 35
CC_MODULATE_ALL = 39
CC_SCAN_SPEED = 41
CC_X_WIDTH = 42
CC_Z_DEPTH = 43
CC_Z_OFFSET = 44
CC_BEAM_COUNT = 45
CC_DELAY = 46
CC_MIRROR = 47
CC_Z_MODE = 48
CC_SMOOTHING = 49
CC_COLOR_MOD_SPEED = 51
CC_COLOR_MOD_DEPTH = 52
CC_COLOR_MOD_PHASE = 53
CC_AUTO_COLOR = 54
CC_CURTAIN = 55
CC_FACTORY_RESET = 60

# Messages temps reel associes dans le Manager aux boutons d'ouverture et de
# fermeture de l'eventail. A confirmer sur la harpe.
REALTIME_START = 0xFB
REALTIME_CLOSE = 0xFC

# Commandes sysex.
SYSEX_STORE_PRESET = 0x72
SYSEX_RECALL_PRESET = 0x74
SYSEX_DUMP_SETTINGS = 0x78
SYSEX_FIRMWARE_VERSION = 0x79

MIN_BEAMS = 5
MAX_BEAMS = 15

Message = list[int]


class HarpError(ValueError):
    """Commande impossible a fabriquer."""


def control_change(cc: int, value: int, channel: int = 1) -> Message:
    """Un Control Change vers la harpe."""
    if not 0 <= cc <= 127:
        raise HarpError(f"numero de controleur hors bornes : {cc}")
    if not 0 <= value <= 127:
        raise HarpError(f"valeur de controleur hors bornes : {value}")
    if not 1 <= channel <= 16:
        raise HarpError(f"canal hors bornes : {channel}")
    return [0xB0 | (channel - 1), cc, value]


def beam_count_value(count: int) -> int:
    """La valeur brute du CC 45 pour un nombre de rayons donne.

    Arrondi au-dessus, comme le Manager V24 : ceil((N - 5) * 127 / 10). Verifie
    sur la harpe le 16/11/2026 : la troncature de l'ancienne version 1.30
    (50 pour 9 rayons) donnait 8 rayons au lieu de 9.
    """
    if not MIN_BEAMS <= count <= MAX_BEAMS:
        raise HarpError(
            f"la harpe accepte de {MIN_BEAMS} a {MAX_BEAMS} rayons, demande : {count}"
        )
    return math.ceil((count - MIN_BEAMS) * 127 / (MAX_BEAMS - MIN_BEAMS))


def set_beam_count(count: int, channel: int = 1) -> Message:
    """Regle le nombre de rayons allumes, de 5 a 15."""
    return control_change(CC_BEAM_COUNT, beam_count_value(count), channel)


def _sysex(*payload: int) -> Message:
    for byte in payload:
        if not 0 <= byte <= 127:
            raise HarpError(f"octet sysex hors bornes : {byte}")
    return [SYSEX_START, *MANUFACTURER, *payload, SYSEX_END]


def store_preset(number: int) -> Message:
    """Enregistre les reglages courants dans un preset interne de la harpe."""
    return _sysex(SYSEX_STORE_PRESET, number)


def recall_preset(number: int) -> Message:
    """Rappelle un preset interne de la harpe."""
    return _sysex(SYSEX_RECALL_PRESET, number)


def request_settings() -> Message:
    """Demande a la harpe l'etat de tous ses reglages."""
    return _sysex(SYSEX_DUMP_SETTINGS)


def request_firmware_version() -> Message:
    """Demande a la harpe sa version de firmware."""
    return _sysex(SYSEX_FIRMWARE_VERSION)


def checksum(payload) -> int:
    """La somme de controle des reponses de la harpe, modulo 128."""
    return sum(payload) % 128


# Noms utilises dans les fichiers de memoire, pour ne pas y ecrire des numeros.
SETTINGS_BY_NAME: dict[str, int] = {
    "puissance_rouge": CC_RED_POWER,
    "puissance_verte": CC_GREEN_POWER,
    "puissance_bleue": CC_BLUE_POWER,
    "mode_rouge": CC_RED_MODE,
    "mode_vert": CC_GREEN_MODE,
    "mode_bleu": CC_BLUE_MODE,
    "gain_capteur": CC_SENSOR_GAIN,
    "modulation_ttl": CC_TTL_MODULATION,
    "modulation_main": CC_HAND_MODULATION,
    "pedale_analogique": CC_ANALOG_PEDAL,
    "pedale_1": CC_PEDAL_1,
    "pedale_2": CC_PEDAL_2,
    "pedale_3": CC_PEDAL_3,
    "pedale_4": CC_PEDAL_4,
    "modulation_tous_rayons": CC_MODULATE_ALL,
    "vitesse_balayage": CC_SCAN_SPEED,
    "largeur_x": CC_X_WIDTH,
    "profondeur_z": CC_Z_DEPTH,
    "decalage_z": CC_Z_OFFSET,
    "delai": CC_DELAY,
    "miroir": CC_MIRROR,
    "mode_z": CC_Z_MODE,
    "lissage": CC_SMOOTHING,
    "vitesse_modulation_couleur": CC_COLOR_MOD_SPEED,
    "profondeur_modulation_couleur": CC_COLOR_MOD_DEPTH,
    "phase_modulation_couleur": CC_COLOR_MOD_PHASE,
    "auto_couleur": CC_AUTO_COLOR,
    "rideau": CC_CURTAIN,
}


def setting(name: str, value: int, channel: int = 1) -> Message:
    """Un reglage de la harpe designe par son nom plutot que par son numero."""
    try:
        cc = SETTINGS_BY_NAME[name]
    except KeyError:
        raise HarpError(f"réglage inconnu : {name}") from None
    return control_change(cc, value, channel)
