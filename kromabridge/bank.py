"""Les memoires : une configuration complete par morceau.

Une memoire reunit deux choses : la table des notes, qui dit ce que joue chaque
rayon, et les reglages de la harpe elle-meme, dont le nombre de rayons allumes.
Charger une memoire, c'est donc a la fois changer la traduction des notes et
envoyer a la harpe ce qu'elle doit devenir.
"""

from __future__ import annotations

from pathlib import Path

from . import harp
from .engine import Engine, Message
from .preset import Preset, PresetError


class Bank:
    """La liste des memoires disponibles et celle qui est active."""

    def __init__(self, presets: list[Preset], engine: Engine | None = None) -> None:
        if not presets:
            raise PresetError("une banque doit contenir au moins une memoire")
        self.presets = presets
        self.index = 0
        self.engine = engine or Engine(presets[0])
        self.engine.preset = presets[0]

    # ------------------------------------------------------------- chargement

    @classmethod
    def from_directory(cls, directory: str | Path, engine: Engine | None = None) -> "Bank":
        """Charge tous les fichiers .json d'un dossier, dans l'ordre des noms."""
        directory = Path(directory)
        paths = sorted(directory.glob("*.json"))
        if not paths:
            raise PresetError(f"aucune memoire trouvee dans {directory}")
        return cls([Preset.load(path) for path in paths], engine)

    # ---------------------------------------------------------------- lecture

    @property
    def current(self) -> Preset:
        return self.presets[self.index]

    @property
    def names(self) -> list[str]:
        return [preset.name for preset in self.presets]

    # ------------------------------------------------------------- changement

    def select(self, index: int, include_harp: bool = True) -> list[Message]:
        """Charge une memoire par son rang et renvoie tout ce qu'il faut emettre.

        L'ordre compte : on coupe d'abord ce qui sonne encore, sinon une note
        tenue resterait suspendue, puis on reconfigure la harpe.
        """
        if not 0 <= index < len(self.presets):
            raise PresetError(
                f"memoire {index + 1} demandee, la banque en compte {len(self.presets)}"
            )
        self.index = index
        preset = self.presets[index]
        out = self.engine.set_preset(preset)
        out.extend(synth_messages(preset))
        if include_harp:
            out.extend(harp_messages(preset))
        return out

    def select_by_name(self, name: str) -> list[Message]:
        for index, preset in enumerate(self.presets):
            if preset.name == name:
                return self.select(index)
        raise PresetError(f"aucune memoire nommee {name!r}")

    def next(self) -> list[Message]:
        """Memoire suivante, en bouclant a la premiere apres la derniere."""
        return self.select((self.index + 1) % len(self.presets))

    def previous(self) -> list[Message]:
        return self.select((self.index - 1) % len(self.presets))


def synth_messages(preset: Preset) -> list[Message]:
    """Le son du synthe du morceau : selection de banque (CC 0 / CC 32) puis
    changement de programme, sur le canal de sortie du morceau."""
    son = preset.synth or {}
    if son.get("actif") is False:       # case decochee : on ne touche pas au son choisi sur le synthe
        return []
    canal = (preset.out_channel or 1) - 1
    out: list[Message] = []
    if son.get("msb") is not None:
        out.append([0xB0 | canal, 0, int(son["msb"])])
    if son.get("lsb") is not None:
        out.append([0xB0 | canal, 32, int(son["lsb"])])
    if son.get("programme") is not None:
        out.append([0xC0 | canal, int(son["programme"]) - 1])
    return out


def harp_messages(preset: Preset) -> list[Message]:
    """Ce qu'il faut envoyer a la harpe pour qu'elle corresponde a la memoire."""
    out: list[Message] = []
    if preset.harp_snapshot:
        from .harp_settings import BY_KEY
        for key, v in preset.harp_snapshot.items():
            out.append(BY_KEY[key].message(v))
    if preset.beam_count is not None:
        out.append(harp.set_beam_count(preset.beam_count))
    for name, value in preset.harp_settings.items():
        out.append(harp.setting(name, value))
    return out
