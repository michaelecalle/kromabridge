"""Modele de donnees des memoires (presets).

Un preset decrit, pour un morceau donne, ce que chaque rayon de la harpe doit
jouer. Les fichiers sont du JSON a cles francaises, pour rester lisibles et
modifiables a la main :

    {
      "nom": "Morceau 1",
      "transposition": 0,
      "canal_sortie": 1,
      "notes_inconnues": "passer",
      "rayons_allumes": 12,
      "preset_harpe": 3,
      "reglages_harpe": {"puissance_rouge": 100},
      "rayons": [
        {"rayon": 1, "note_entrante": 60, "note": 62},
        {"rayon": 2, "note_entrante": 62, "note": 65, "canal": 2, "velocite": 100}
      ]
    }

"note_entrante" est ce que la harpe envoie pour ce rayon (etabli une fois pour
toutes par le mode apprentissage). "note" est ce que le boitier envoie a sa
place. "canal" et "velocite" sont facultatifs et, absents, suivent le preset.

"rayons_allumes" et "reglages_harpe" sont envoyes a la harpe au chargement de la
memoire : c'est ce qui permet de changer le nombre de rayons d'un morceau a
l'autre. Les noms de reglages disponibles sont ceux de harp.SETTINGS_BY_NAME.

"harpe" (facultatif) est un instantane complet des reglages de la harpe, avec
les noms et valeurs de harp_settings (ceux du Manager V24) : il est renvoye a la
harpe au chargement du morceau. Chaque morceau contient ainsi ses notes et tous
les reglages de la harpe.

"preset_harpe" (1 a 40, facultatif) est le preset enregistre dans la harpe elle-
meme qui va avec ce morceau : couleurs, puissances, balayage... Il est rappele
dans la harpe au chargement du morceau, avant "rayons_allumes" et
"reglages_harpe", qui restent prioritaires.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

PASS_UNKNOWN = "passer"
DROP_UNKNOWN = "ignorer"


class PresetError(ValueError):
    """Un fichier de preset est invalide."""


def _check_range(value: int, low: int, high: int, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise PresetError(f"{label} doit être un entier, reçu {value!r}")
    if not low <= value <= high:
        raise PresetError(f"{label} doit être entre {low} et {high}, reçu {value}")
    return value


@dataclass
class Beam:
    """Un rayon de la harpe et la note qu'il doit declencher."""

    index: int
    incoming_note: int
    note: int
    channel: int | None = None
    velocity: int | None = None
    # Accord : les notes jouees en plus de `note` quand on coupe le rayon.
    extra: list = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: dict) -> "Beam":
        try:
            index = raw["rayon"]
            incoming = raw["note_entrante"]
            note = raw["note"]
        except KeyError as exc:
            raise PresetError(f"rayon incomplet, cle manquante : {exc.args[0]}") from exc
        channel = raw.get("canal")
        velocity = raw.get("velocite")
        beam = cls(
            index=_check_range(index, 1, 512, "rayon"),
            incoming_note=_check_range(incoming, 0, 127, "note_entrante"),
            note=_check_range(note, 0, 127, "note"),
            channel=None if channel is None else _check_range(channel, 1, 16, "canal"),
            velocity=None if velocity is None else _check_range(velocity, 1, 127, "velocite"),
            extra=[_check_range(n, 0, 127, "notes_sup") for n in raw.get("notes_sup", [])],
        )
        return beam

    def to_dict(self) -> dict:
        out: dict = {"rayon": self.index, "note_entrante": self.incoming_note, "note": self.note}
        if self.channel is not None:
            out["canal"] = self.channel
        if self.velocity is not None:
            out["velocite"] = self.velocity
        if self.extra:
            out["notes_sup"] = list(self.extra)
        return out


@dataclass
class Preset:
    """Une memoire complete, c'est a dire la configuration d'un morceau."""

    name: str = "Sans titre"
    beams: list[Beam] = field(default_factory=list)
    out_channel: int = 1
    transpose: int = 0
    unknown_notes: str = PASS_UNKNOWN
    beam_count: int | None = None
    harp_settings: dict = field(default_factory=dict)
    harp_preset: int | None = None
    harp_snapshot: dict = field(default_factory=dict)
    # Animation d'ouverture : {"active": bool, "pause": s, "duree": s}
    animation: dict = field(default_factory=dict)
    # Parties du morceau : [{"nom": "A", "rayons": [rayons au format JSON]}, ...].
    # `beams` contient toujours les rayons de la partie active.
    parts: list = field(default_factory=list)
    part_index: int = 0
    # Ce qu'il faut pour rouvrir l'import tel qu'on l'a laisse (fichier MIDI
    # garde dans presets/imports, pistes, parties, commandes...).
    import_info: dict = field(default_factory=dict)
    # Son du synthe choisi a l'ouverture du morceau : {"programme": 1..128,
    # "msb": 0..127, "lsb": 0..127} (chaque cle facultative).
    synth: dict = field(default_factory=dict)
    # Morceau special "Gamme" : chaque rayon joue la note envoyee par la harpe,
    # sans modification (couleurs tons / demi-tons, essais).
    gamme: bool = False
    # Le fichier d'ou vient la memoire : on y reecrit, au lieu d'en creer un
    # nouveau d'apres le nom (ce qui dupliquait les morceaux renommes ou dont le
    # fichier ne portait pas exactement leur nom).
    source: Path | None = field(default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        from .harp_settings import BY_KEY
        for key, v in self.harp_snapshot.items():
            if key not in BY_KEY:
                raise PresetError(f"harpe.{key} : réglage inconnu")
            _check_range(v, 0, BY_KEY[key].span, f"harpe.{key}")
        if self.harp_preset is not None:
            _check_range(self.harp_preset, 1, 40, "preset_harpe")
        _check_range(self.out_channel, 1, 16, "canal_sortie")
        _check_range(self.transpose, -48, 48, "transposition")
        if self.beam_count is not None:
            _check_range(self.beam_count, 5, 15, "rayons_allumes")
        for name, value in self.harp_settings.items():
            _check_range(value, 0, 127, f"reglages_harpe.{name}")
        if self.unknown_notes not in (PASS_UNKNOWN, DROP_UNKNOWN):
            raise PresetError(
                f"notes_inconnues doit valoir '{PASS_UNKNOWN}' ou '{DROP_UNKNOWN}', "
                f"recu {self.unknown_notes!r}"
            )
        seen: dict[int, int] = {}
        for beam in self.beams:
            if beam.incoming_note in seen:
                raise PresetError(
                    f"les rayons {seen[beam.incoming_note]} et {beam.index} declarent la meme "
                    f"note entrante {beam.incoming_note} : Kromabridge ne pourrait pas les "
                    "distinguer"
                )
            seen[beam.incoming_note] = beam.index

    def beam_for(self, incoming_note: int) -> Beam | None:
        """Le rayon qui correspond a cette note entrante, s'il existe."""
        for beam in self.beams:
            if beam.incoming_note == incoming_note:
                return beam
        return None

    def extra_targets(self, beam: Beam) -> list[tuple[int, int]]:
        """Les notes de l'accord du rayon (en plus de sa note), transposition comprise."""
        canal = beam.channel or self.out_channel
        return [(canal, n + self.transpose) for n in beam.extra if 0 <= n + self.transpose <= 127]

    def target(self, beam: Beam) -> tuple[int, int] | None:
        """Le couple (canal, note) a emettre pour ce rayon, transposition comprise.

        Renvoie None si la transposition fait sortir la note de l'etendue MIDI.
        """
        note = beam.note + self.transpose
        if not 0 <= note <= 127:
            return None
        return (beam.channel or self.out_channel, note)

    @classmethod
    def from_dict(cls, raw: dict) -> "Preset":
        if not isinstance(raw, dict):
            raise PresetError("un preset doit être un objet JSON")
        rays = raw.get("rayons", [])
        if not isinstance(rays, list):
            raise PresetError("rayons doit être une liste")
        preset = cls(
            name=str(raw.get("nom", "Sans titre")),
            beams=[Beam.from_dict(item) for item in rays],
            out_channel=raw.get("canal_sortie", 1),
            transpose=raw.get("transposition", 0),
            unknown_notes=raw.get("notes_inconnues", PASS_UNKNOWN),
            beam_count=raw.get("rayons_allumes"),
            harp_settings=dict(raw.get("reglages_harpe", {})),
            harp_preset=raw.get("preset_harpe"),
            harp_snapshot=dict(raw.get("harpe", {})),
            animation=dict(raw.get("animation", {})),
            parts=[dict(x) for x in raw.get("parties", [])],
            part_index=int(raw.get("partie_active", 0)),
            import_info=dict(raw.get("import", {})),
            synth=dict(raw.get("son_synthe", {})),
            gamme=bool(raw.get("gamme", False)),
        )
        # Son du synthe par partie. Ancien format (un son pour tout le morceau) :
        # il devient celui de la partie A ; les autres ne changent pas le son.
        if preset.parts:
            if not any("son_synthe" in x for x in preset.parts):
                preset.parts[0]["son_synthe"] = dict(preset.synth)
            if 0 <= preset.part_index < len(preset.parts):
                preset.synth = preset.part_synth(preset.part_index)
        return preset

    def to_dict(self) -> dict:
        out = {
            "nom": self.name,
            "transposition": self.transpose,
            "canal_sortie": self.out_channel,
            "notes_inconnues": self.unknown_notes,
        }
        if self.beam_count is not None:
            out["rayons_allumes"] = self.beam_count
        if self.harp_preset is not None:
            out["preset_harpe"] = self.harp_preset
        if self.harp_snapshot:
            out["harpe"] = dict(self.harp_snapshot)
        if self.animation:
            out["animation"] = dict(self.animation)
        if self.harp_settings:
            out["reglages_harpe"] = dict(self.harp_settings)
        out["rayons"] = [beam.to_dict() for beam in self.beams]
        if self.parts:
            self.sync_part()
            out["parties"] = [dict(x) for x in self.parts]
            out["partie_active"] = self.part_index
        if self.import_info:
            out["import"] = dict(self.import_info)
        if self.synth:
            out["son_synthe"] = dict(self.synth)
        if self.gamme:
            out["gamme"] = True
        return out

    def sync_part(self) -> None:
        """Recopie les rayons et le son du synthe actuels dans la partie active."""
        if self.parts and 0 <= self.part_index < len(self.parts):
            self.parts[self.part_index]["rayons"] = [beam.to_dict() for beam in self.beams]
            self.parts[self.part_index]["son_synthe"] = dict(self.synth)

    def part_synth(self, index: int) -> dict:
        """Le son du synthe d'une partie ({} : le son du synthe n'est pas touche)."""
        return dict(self.parts[index].get("son_synthe", {}))

    @classmethod
    def load(cls, path: str | Path) -> "Preset":
        path = Path(path)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise PresetError(f"{path.name} n'est pas du JSON valide : {exc}") from exc
        preset = cls.from_dict(raw)
        preset.source = path
        return preset

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )


def chromatic_preset(beam_count: int, first_note: int = 60, name: str = "Chromatique") -> Preset:
    """Un preset ou chaque rayon rejoue simplement ce qu'il recoit.

    Utile comme point de depart : la harpe sonne comme avant, puis on modifie
    rayon par rayon.
    """
    beams = [
        Beam(index=i + 1, incoming_note=first_note + i, note=first_note + i)
        for i in range(beam_count)
    ]
    return Preset(name=name, beams=beams)
