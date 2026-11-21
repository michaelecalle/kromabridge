"""Le pilotage de la harpe depuis la page : lire et changer ses reglages.

Relie l'envoi brut (HarpControl), la lecture continue (HarpListener) et la
table des reglages du Manager V24 (harp_settings).
"""

from __future__ import annotations

import threading
import time
from collections import deque

from . import harp_settings as hs
from .midi_io import HARP_CLOSE as HARP_CLOSE_MSG, HARP_OPEN as HARP_OPEN_MSG, MidiError
from .monitor import MidiMonitor


# Le boitier reconnait chaque rayon au numero de la note que la harpe envoie.
# Si la harpe transposait elle-meme, il confondrait les rayons (verifie le
# 13/11/2026 : Transpose = 2 fait passer do4 (60) a re4 (62)). Ces deux
# reglages sont donc tenus a 0 sur la harpe, comme decide avec Michael.
LOCKED_AT_ZERO = {"transposition": 0, "octave": 3}   # v = 3 -> octave 0


class HarpService:
    def __init__(self, control, collector: hs.SysexCollector | None = None,
                 timeout: float = 4.0) -> None:
        # Harpe ouverte, ses reponses peuvent mettre plus d'une seconde et demie
        # (mesure le 16/11/2026) : on laisse de la marge.
        self.control = control
        self.collector = collector or hs.SysexCollector()
        self.timeout = timeout
        self.monitor = MidiMonitor()
        # Delais lecture harpe -> envoi au synthe, en microsecondes (les 500 derniers).
        self.latences: deque = deque(maxlen=500)
        self.valeurs: dict[str, int] = {}
        self.version: str | None = None
        self._lock = threading.Lock()

    # Branche sur HarpListener(on_data=...)
    def feed(self, data: bytes) -> None:
        self.collector.feed(data)
        self.monitor.feed(data)

    def firmware(self) -> str | None:
        """La version du firmware : lue avec les reglages, ou tiree de la derniere
        reponse a la demande de version (envoyee apres chaque message a la harpe)."""
        if self.version is None:
            reponse = self.collector.last.get(0x06)
            if reponse is not None:
                self.version = hs.parse_version(reponse)
        return self.version

    def latency_report(self) -> dict:
        v = sorted(self.latences)
        if not v:
            return {"mesures": 0}
        return {"mesures": len(v), "min_us": v[0], "mediane_us": v[len(v) // 2],
                "p95_us": v[int(len(v) * 0.95) - 1 if len(v) > 1 else 0], "max_us": v[-1]}

    def state(self) -> dict:
        return {
            "reglages": [s.describe() for s in hs.SETTINGS],
            "valeurs": dict(self.valeurs),
            "version": self.version,
            "presets": [hs.PRESET_MIN, hs.PRESET_MAX],
            "verrouilles": sorted(LOCKED_AT_ZERO),
        }

    def read(self, verrous: bool = True) -> dict:
        """Demande la version et tous les reglages, et attend les reponses.

        Constate sur la harpe le 16/11/2026 : elle ne libere sa reponse qu'a la
        reception du message suivant. On la "pousse" donc en renvoyant une
        demande de version, comme le fait le Manager V24 apres chaque lecture.
        """
        with self._lock:
            avant = self.collector.snapshot()
            self.control.send(hs.VERSION)
            self.control.send(hs.READ_ALL)
            dump = None
            fin = time.monotonic() + self.timeout
            while dump is None and time.monotonic() < fin:
                self.control.send(hs.VERSION)          # pousse la reponse en attente
                dump = self.collector.wait(0x70, after=avant, timeout=0.4)
            if dump is None:
                raise MidiError("la harpe n'a pas répondu à la lecture des réglages")
            reponse = self.collector.last.get(0x06)
            if reponse is not None:
                self.version = hs.parse_version(reponse)
            anciennes = dict(self.valeurs)
            self.valeurs = hs.parse_dump(dump)
            # la harpe ne connait pas les choix ajoutes par l'application : on les garde
            for key, v in anciennes.items():
                r = hs.BY_KEY.get(key)
                if r is not None and v in r.alias and r.alias[v] == self.valeurs.get(key):
                    self.valeurs[key] = v
            if verrous:
                self.enforce_locks()
        return self.state()

    def appliquer(self, valeurs: dict) -> None:
        """Envoie a la harpe tous ces reglages (sans rien enregistrer en memoire)."""
        for key, v in valeurs.items():
            r = hs.BY_KEY.get(key)
            if r is not None:
                self.control.send(r.message(int(v)))
        self.valeurs.update({k: int(v) for k, v in valeurs.items() if k in hs.BY_KEY})

    def lire_preset(self, n: int) -> dict:
        """Le contenu d'un preset de la harpe : on le rappelle, on lit ses reglages
        (transposition et octave comprises), puis on remet la harpe exactement
        comme avant. Rien n'est ecrit dans la memoire de la harpe."""
        hs.check_preset(int(n))
        avant = dict(self.read()["valeurs"])
        self.control.send(hs.recall_preset(int(n)))
        self.read(verrous=False)
        contenu = dict(self.valeurs)
        self.appliquer(avant)
        self.valeurs = avant
        return contenu

    def enregistrer_preset(self, n: int, cible: dict) -> None:
        """Met la harpe dans l'etat `cible`, l'enregistre dans le preset n, puis
        remet la harpe comme avant. Ecrit dans la memoire de la harpe."""
        hs.check_preset(int(n))
        avant = dict(self.read()["valeurs"])
        self.appliquer(cible)
        time.sleep(0.2)
        self.control.send(hs.save_preset(int(n)))
        time.sleep(0.3)
        self.appliquer(avant)
        self.valeurs = avant

    def largeur_zero(self) -> list[int] | None:
        """Prepare le message cache qui met la largeur X a 0 % (tous les rayons
        fusionnes) : relit les reglages et ne change que la largeur. A envoyer
        HARPE OUVERTE : harpe fermee, elle remonte la largeur a 10 % (essai du
        18/11/2026). None si la harpe n'a pas repondu."""
        with self._lock:
            avant = self.collector.snapshot()
            self.control.send(hs.READ_ALL)
            dump = None
            fin = time.monotonic() + self.timeout
            while dump is None and time.monotonic() < fin:
                self.control.send(hs.VERSION)
                dump = self.collector.wait(0x70, after=avant, timeout=0.4)
            if dump is None or len(dump) < 44:
                return None
            donnees = list(dump[4:44])
            donnees[hs.OCTET_LARGEUR] = 0
            return hs.write_all(donnees)

    def enforce_locks(self) -> None:
        """Remet a 0 la transposition et l'octave de la harpe si besoin."""
        for key, v in LOCKED_AT_ZERO.items():
            if self.valeurs.get(key) != v:
                self.control.send(hs.BY_KEY[key].message(v))
                self.valeurs[key] = v

    def set(self, key: str, v: int) -> dict:
        reglage = hs.BY_KEY.get(key)
        if reglage is None:
            raise ValueError(f"réglage inconnu : {key}")
        if key in LOCKED_AT_ZERO and int(v) != LOCKED_AT_ZERO[key]:
            raise ValueError(f"{reglage.label} reste à 0 : la transposition se fait dans Kromabridge")
        self.control.send(reglage.message(int(v)))
        self.valeurs[key] = int(v)
        return self.state()

    def recall(self, n: int) -> dict:
        self.control.send(hs.recall_preset(int(n)))
        return self.read()

    def save(self, n: int) -> dict:
        self.control.send(hs.save_preset(int(n)))
        return self.state()

    def factory_reset(self) -> dict:
        self.control.send(hs.FACTORY_RESET)
        return self.read()


def open_animated(control, largeur: int, pause: float, duree: float,
                  sleep=time.sleep, step_s: float = 0.03, depart_zero=None) -> list[int]:
    """Ouvre la harpe en eventail : largeur au minimum, ouverture (animation
    d'origine du firmware, serree au centre), pause, puis elargissement fluide
    jusqu'a `largeur` (valeur 0..90 du reglage "X Wide").

    Renvoie la suite des largeurs envoyees (pour les essais).
    """
    reglage = hs.BY_KEY["largeur"]
    largeur = max(0, min(reglage.span, int(largeur)))
    # Depart a 0 % (rayons fusionnes) par le message cache, envoye juste apres
    # l'ouverture (harpe fermee, elle le ramene a 10 %) : les couleurs montent
    # alors depuis zero, on ne voit pas l'eventail a 10 %. Sinon : minimum des
    # commandes MIDI (10 %), avant l'ouverture.
    zero = depart_zero() if depart_zero is not None else None
    control.send(reglage.message(0))
    control.send(HARP_OPEN_MSG)
    if zero:
        control.send(zero)
    sleep(max(0.0, pause))
    pas = max(1, int(max(0.05, duree) / step_s))
    envoyees = []
    for i in range(1, pas + 1):
        v = round(largeur * i / pas)
        if not envoyees or v != envoyees[-1]:
            envoyees.append(v)
            if i < pas:
                control.send_quick(reglage.message(v))
        sleep(step_s)          # a chaque pas, meme sans changement : la duree est respectee
    control.send(reglage.message(largeur))
    if not envoyees or envoyees[-1] != largeur:
        envoyees.append(largeur)
    return envoyees


def close_animated(control, largeur: int, pause: float, duree: float,
                   sleep=time.sleep, step_s: float = 0.03) -> list[int]:
    """L'inverse de open_animated : l'eventail se referme progressivement
    jusqu'a la largeur minimale, pause, fermeture de la harpe, puis la largeur
    du morceau est remise (harpe fermee) pour la prochaine ouverture."""
    reglage = hs.BY_KEY["largeur"]
    largeur = max(0, min(reglage.span, int(largeur)))
    pas = max(1, int(max(0.05, duree) / step_s))
    envoyees = []
    for i in range(1, pas + 1):
        v = round(largeur * (pas - i) / pas)
        if not envoyees or v != envoyees[-1]:
            envoyees.append(v)
            if i < pas:
                control.send_quick(reglage.message(v))
        sleep(step_s)          # a chaque pas, meme sans changement : la duree est respectee
    control.send(reglage.message(0))
    sleep(max(0.0, pause))
    control.send(HARP_CLOSE_MSG)
    control.send(reglage.message(largeur))
    return envoyees
