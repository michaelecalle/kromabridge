"""Le son de la harpe : quelques echantillons enregistres, transposes pour
couvrir toutes les notes.

- `banque()` : la liste des echantillons et leur hauteur exacte (mesuree,
  corrigee : les enregistrements sont un peu trop hauts).
- `choix(note)` : quel echantillon jouer, et a quelle vitesse.
- `LecteurAudio` : joue sur une sortie audio de l'ordinateur (version PC, avec
  numpy et sounddevice). Sur le boitier, le son est joue par le navigateur.
"""

from __future__ import annotations

import json
import threading
import wave
from pathlib import Path

DOSSIER = Path(__file__).resolve().parent / "sons" / "harpe"
RELACHE_S = 0.35            # fondu a la fin d'une note


def banque(dossier: Path = DOSSIER) -> list[tuple[str, float]]:
    """[(fichier, hauteur MIDI exacte), ...], du plus grave au plus aigu."""
    try:
        hauteurs = json.loads((dossier / "hauteurs.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return sorted(((f, float(h)) for f, h in hauteurs.items() if (dossier / f).exists()),
                  key=lambda x: x[1])


def choix(note: int, echantillons: list[tuple[str, float]]) -> tuple[str, float] | None:
    """L'echantillon le plus proche de `note`, et le rapport de vitesse a appliquer."""
    if not echantillons:
        return None
    fichier, h = min(echantillons, key=lambda e: (abs(e[1] - note), e[1]))
    return fichier, 2 ** ((note - h) / 12)


class LecteurAudio:
    """Melangeur simple : chaque note est un echantillon lu a la bonne vitesse."""

    def __init__(self, sortie_getter=lambda: None, dossier: Path = DOSSIER) -> None:
        self.sortie_getter = sortie_getter
        self.dossier = dossier
        self._donnees: dict[str, object] = {}
        self._voix: list[dict] = []
        self._lock = threading.Lock()
        self._flux = None
        self._sortie = None
        self.taux = 48000
        self.erreur: str | None = None

    # -------------------------------------------------------------- materiel

    @staticmethod
    def sorties() -> list[str]:
        """Les sorties audio proposees (de preference en WASAPI : faible latence)."""
        import sounddevice as sd
        apis = sd.query_hostapis()
        noms = []
        for d in sd.query_devices():
            if d["max_output_channels"] < 1:
                continue
            api = apis[d["hostapi"]]["name"]
            if "WASAPI" in api:
                noms.append(d["name"])
        if not noms:
            noms = [d["name"] for d in sd.query_devices() if d["max_output_channels"] > 0]
        return sorted(set(noms))

    def _ouvrir(self) -> None:
        import numpy as np
        import sounddevice as sd
        voulue = self.sortie_getter() or None
        if self._flux is not None and voulue == self._sortie:
            return
        self.fermer()
        apis = sd.query_hostapis()
        index = None
        for i, d in enumerate(sd.query_devices()):
            if d["max_output_channels"] < 2:
                continue
            if voulue is None and "WASAPI" in apis[d["hostapi"]]["name"]:
                if i == apis[d["hostapi"]].get("default_output_device"):
                    index = i
                    break
            if voulue and d["name"] == voulue and "WASAPI" in apis[d["hostapi"]]["name"]:
                index = i
                break
        if index is None and voulue:
            index = next((i for i, d in enumerate(sd.query_devices())
                          if d["name"] == voulue and d["max_output_channels"] >= 2), None)
        info = sd.query_devices(index, "output") if index is not None else sd.query_devices(kind="output")
        self.taux = int(info["default_samplerate"])

        def remplir(sortie, frames, _temps, _etat):
            sortie[:] = 0
            with self._lock:
                vivantes = []
                for v in self._voix:
                    d = v["donnees"]
                    pos = v["pos"] + v["pas"] * np.arange(frames)
                    if pos[0] >= len(d) - 1:
                        continue
                    pos = np.minimum(pos, len(d) - 1.001)
                    i = pos.astype(np.int64)
                    fr = (pos - i)[:, None]
                    bloc = d[i] * (1 - fr) + d[i + 1] * fr
                    if v["relache"] is not None:
                        g = v["relache"] - np.arange(frames) / (RELACHE_S * self.taux)
                        g = np.clip(g, 0, 1)[:, None]
                        v["relache"] = float(g[-1, 0])
                        bloc = bloc * g
                    sortie += bloc * v["gain"]
                    v["pos"] = pos[-1] + v["pas"]
                    if v["pos"] < len(d) - 1 and (v["relache"] is None or v["relache"] > 0):
                        vivantes.append(v)
                self._voix = vivantes
            np.clip(sortie, -1, 1, out=sortie)

        self._flux = sd.OutputStream(device=index, channels=2, samplerate=self.taux,
                                     dtype="float32", latency="low", callback=remplir)
        self._flux.start()
        self._sortie = voulue

    def fermer(self) -> None:
        if self._flux is not None:
            try:
                self._flux.stop()
                self._flux.close()
            except Exception:
                pass
        self._flux = None

    # -------------------------------------------------------------- notes

    def _echantillon(self, fichier: str):
        import numpy as np
        if fichier not in self._donnees:
            with wave.open(str(self.dossier / fichier)) as w:
                brut = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
                canaux, taux = w.getnchannels(), w.getframerate()
            d = brut.reshape(-1, canaux).astype(np.float32) / 32768.0
            if canaux == 1:
                d = np.repeat(d, 2, axis=1)
            self._donnees[fichier] = (d[:, :2], taux)
        return self._donnees[fichier]

    def message(self, message: list[int]) -> None:
        """Un message MIDI de note (debut ou fin)."""
        if len(message) < 3:
            return
        genre, note, vel = message[0] & 0xF0, message[1], message[2]
        try:
            self._ouvrir()
            if genre == 0x90 and vel > 0:
                c = choix(note, banque(self.dossier))
                if c is None:
                    return
                donnees, taux = self._echantillon(c[0])
                with self._lock:
                    self._voix.append({"note": note, "donnees": donnees, "pos": 0.0,
                                       "pas": c[1] * taux / self.taux, "gain": 0.45 * vel / 127,
                                       "relache": None})
                    self._voix = self._voix[-24:]
            elif genre in (0x80, 0x90):
                with self._lock:
                    for v in self._voix:
                        if v["note"] == note and v["relache"] is None:
                            v["relache"] = 1.0
            self.erreur = None
        except Exception as exc:          # pas de carte son, pilote absent...
            self.erreur = str(exc)
