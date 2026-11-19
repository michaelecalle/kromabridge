"""Les ports MIDI reels.

Tout ce qui precede fabrique des messages sans rien brancher, ce qui rend le
moteur testable sur n'importe quelle machine. Ce module est la seule partie qui
touche au materiel, et il reste volontairement mince.

Sur le Raspberry, la harpe branchee en USB apparait sous le nom `Kromalaser USB`,
et une carte MIDI posee sur le connecteur GPIO expose les prises DIN. On designe
donc les ports par un morceau de leur nom plutot que par un numero, qui change
d'un demarrage a l'autre.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from pathlib import Path

try:
    import rtmidi
except ImportError:  # pragma: no cover - depend de la machine
    rtmidi = None

HARP_PORT_HINT = "kromalaser"


class MidiError(RuntimeError):
    """Un port MIDI manque ou ne s'ouvre pas."""


def _require_rtmidi():
    if rtmidi is None:
        raise MidiError(
            "la bibliotheque python-rtmidi est absente : installez-la avec "
            "'pip install python-rtmidi' (paquet systeme librtmidi-dev sur Raspberry Pi)"
        )
    return rtmidi


def list_ports() -> dict[str, list[str]]:
    """Les ports disponibles, pour afficher a l'utilisateur ce que voit le boitier."""
    module = _require_rtmidi()
    entree, sortie = module.MidiIn(), module.MidiOut()
    try:
        return {"entrees": entree.get_ports(), "sorties": sortie.get_ports()}
    finally:
        _release(entree)
        _release(sortie)


def _release(port) -> None:
    """Ferme un port rtmidi et libere son client ALSA.

    Sans `delete()`, chaque objet rtmidi garde son client du sequenceur ALSA
    ouvert : en reessayant toutes les 3 secondes, le boitier epuisait les 64
    clients autorises en 90 secondes et plantait sur "Cannot allocate memory".
    """
    if port is None:
        return
    try:
        port.close_port()
    except Exception:  # pragma: no cover - fermeture au mieux
        pass
    try:
        port.delete()
    except Exception:  # pragma: no cover - objet deja libere
        pass


def find_port(ports: list[str], hint: str | None) -> int:
    """Le rang du premier port dont le nom contient `hint`, sans tenir compte de la casse.

    Sans indication, on prend le premier port disponible, ce qui est le cas
    courant sur un Raspberry dedie ou il n'y en a qu'un de chaque cote.
    """
    if not ports:
        raise MidiError("aucun port MIDI disponible")
    if not hint:
        # le premier port qui n'est ni la harpe ni un port interne du systeme
        for index, nom in enumerate(ports):
            if not any(x in nom.lower() for x in ("midi through", "kromalaser", "rtmidi")):
                return index
        return 0
    besoin = hint.lower()
    for index, nom in enumerate(ports):
        if besoin in nom.lower():
            return index
    connus = ", ".join(ports)
    raise MidiError(f"aucun port ne correspond a {hint!r}. Ports vus : {connus}")


class MidiLink:
    """Une entree et une sortie MIDI ouvertes ensemble.

    S'utilise comme un gestionnaire de contexte pour que les ports soient
    toujours refermes, y compris si le programme s'arrete sur une erreur.
    """

    def __init__(self, in_hint: str | None = None, out_hint: str | None = None,
                 use_input: bool = True) -> None:
        self.in_hint = in_hint
        self.out_hint = out_hint
        # Sans entree : la harpe est lue par HarpListener, seul lecteur de son
        # port (une lecture concurrente par le sequenceur serait refusee).
        self.use_input = use_input
        self._in = None
        self._out = None

    def __enter__(self) -> "MidiLink":
        module = _require_rtmidi()
        self._in = module.MidiIn() if self.use_input else None
        self._out = module.MidiOut()
        try:
            if self._in is not None:
                self._in.open_port(find_port(self._in.get_ports(), self.in_hint))
            self._out.open_port(find_port(self._out.get_ports(), self.out_hint))
        except Exception:
            self.close()
            raise
        if self._in is not None:
            # Les sysex de la harpe nous interessent : ils portent ses reglages.
            self._in.ignore_types(sysex=False, timing=False, active_sense=True)
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def still_present(self) -> bool:
        """Vrai tant que les ports designes existent encore (un appareil USB peut etre debranche)."""
        if self._out is None or (self.use_input and self._in is None):
            return False
        try:
            if self._in is not None:
                find_port(self._in.get_ports(), self.in_hint)
            find_port(self._out.get_ports(), self.out_hint)
        except MidiError:
            return False
        return True

    def close(self) -> None:
        _release(self._in)
        _release(self._out)
        self._in = None
        self._out = None

    def on_message(self, callback: Callable[[list[int]], None]) -> None:
        """Appelle `callback` a chaque message recu, depuis le fil de rtmidi."""
        if self._in is None:
            raise MidiError("lien MIDI non ouvert")
        self._in.set_callback(lambda event, _data: callback(list(event[0])))

    def send(self, message) -> None:
        if self._out is None:
            raise MidiError("lien MIDI non ouvert")
        self._out.send_message(list(message))

    def send_all(self, messages) -> None:
        for message in messages:
            self.send(message)


# Messages temps reel de la harpe. Le Kromalaser Manager V24 ouvre avec FA
# ("Start") et ferme avec FC ("Stop"). Verifie sur la harpe le 13/11/2026 :
# FA, avec la harpe ecoutee en permanence, ouvre la harpe complete et la
# detection de la main fonctionne ; FB (ancienne version 1.30) n'ouvrait que
# trois rayons, sans detection ni reouverture possible apres FC.
HARP_OPEN = [0xFA]
HARP_CLOSE = [0xFC]
# La harpe n'execute une commande (et ne libere une reponse) qu'a la reception
# du message suivant (constate le 16/11/2026). Chaque envoi est donc suivi de
# cette demande de version, sans effet, qui "pousse" la commande.
HARP_NUDGE = [0xF0, 0x7B, 0x01, 0x79, 0xF7]
# Apres avoir recu les reglages d'un morceau, la harpe ignore le premier ordre
# d'ouverture qui suit, meme longtemps apres (essais automatiques du 18/11/2026 :
# 1 ouverture sur 5). Deux parades :
# - "fermer_avant" : si des reglages sont partis depuis le dernier ordre et que la
#   harpe est fermee, un ordre "fermer" (sans effet sur une harpe fermee) part juste
#   avant l'ouverture. Un seul ordre d'ouverture : le logo a 3 rayons de la premiere
#   ouverture apres l'allumage est preserve.
# - "renvoi" : l'ordre d'ouverture est renvoye une seconde apres (marche, mais saute
#   le logo a 3 rayons, que le firmware quitte a tout nouvel ordre d'ouverture).
OUVERTURE = "fermer_avant"
DELAI_REOUVERTURE = 1.0
DELAI_AVANT_OUVERTURE = 0.5


def suivre_envoi(control, message) -> None:
    """Retient l'etat suppose de la harpe et s'il y a eu des reglages depuis le
    dernier ordre ouvrir / fermer (lectures et relances exceptees)."""
    m = list(message)
    if m == HARP_OPEN:
        control._ouverte, control._reglages_depuis = True, False
    elif m == HARP_CLOSE:
        control._ouverte, control._reglages_depuis = False, False
    elif m and ((m[0] & 0xF0) == 0xB0 or (m[0] == 0xF0 and len(m) > 3 and m[3] not in (0x78, 0x79))):
        control._reglages_depuis = True


def preparer_ouverture(control) -> None:
    """Avant un ordre d'ouverture (mode "fermer_avant")."""
    if OUVERTURE != "fermer_avant":
        return
    if getattr(control, "_reglages_depuis", False) and not getattr(control, "_ouverte", False):
        control.send(HARP_CLOSE, renvoi=False)
        time.sleep(DELAI_AVANT_OUVERTURE)


def renvoyer_ouverture(control, message) -> None:
    """Si `message` est l'ordre d'ouverture, le renvoie une seconde apres
    (sans bloquer l'appelant)."""
    if OUVERTURE != "renvoi" or list(message) != HARP_OPEN or DELAI_REOUVERTURE is None:
        return
    def plus_tard() -> None:
        try:
            control.send(HARP_OPEN, renvoi=False)
        except Exception:
            pass
    t = threading.Timer(DELAI_REOUVERTURE, plus_tard)
    t.daemon = True
    t.start()


class HarpControl:
    """Envoie des commandes a la harpe elle-meme, sur son port USB.

    On ecrit directement sur le peripherique MIDI brut de la harpe
    (/dev/snd/midiC<carte>D0), exactement comme `amidi`, la methode verifiee sur
    la harpe le 13/11/2026. Un premier essai par le sequenceur ALSA (rtmidi)
    faisait alterner le voyant vert/orange sans ouvrir les rayons.
    Independant du lien de jeu : la harpe peut etre pilotee meme quand le jeu
    passe par une autre carte MIDI.
    """

    def __init__(self, hint: str = HARP_PORT_HINT, asound: str = "/proc/asound",
                 dev: str = "/dev/snd", writer=None) -> None:
        self.on_sent = None   # appele avec chaque commande envoyee (moniteur)
        self.hint = hint
        self.asound = Path(asound)
        self.dev = Path(dev)
        self._writer = writer or _write_raw
        self._lock = threading.Lock()

    def device(self) -> Path:
        """Le peripherique MIDI brut de la carte son dont le nom contient `hint`."""
        besoin = self.hint.lower()
        try:
            lignes = (self.asound / "cards").read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise MidiError(f"cartes son illisibles : {exc}") from exc
        for ligne in lignes.splitlines():
            debut = ligne.strip().split(" ", 1)[0]
            if debut.isdigit() and besoin in ligne.lower():
                chemin = self.dev / f"midiC{debut}D0"
                if chemin.exists():
                    return chemin
        raise MidiError("harpe absente : branchez-la en USB et allumez-la")

    def send(self, message, renvoi: bool = True) -> None:
        if renvoi and list(message) == HARP_OPEN:
            preparer_ouverture(self)
        with self._lock:
            chemin = self.device()
            try:
                # Deux ecritures separees : collee dans la meme ecriture que la
                # commande, la relance faisait ignorer les Control Change
                # (verifie sur la harpe le 16/11/2026).
                # Une seule relance ne suffit pas pour les reglages : le nombre de
                # rayons ne changeait qu'a la relance suivante (16/11/2026). Deux.
                self._writer(chemin, bytes(message))
                self._writer(chemin, bytes(HARP_NUDGE))
                self._writer(chemin, bytes(HARP_NUDGE))
                if self.on_sent is not None:
                    self.on_sent(list(message))
            except OSError as exc:
                raise MidiError(f"harpe injoignable : {exc}") from exc
        suivre_envoi(self, message)
        if renvoi:
            renvoyer_ouverture(self, message)

    def send_quick(self, message) -> None:
        """Envoi sans relance : pour une suite rapide de messages, ou chaque
        message pousse le precedent. Le dernier doit partir par send()."""
        with self._lock:
            chemin = self.device()
            try:
                self._writer(chemin, bytes(message))
            except OSError as exc:
                raise MidiError(f"harpe injoignable : {exc}") from exc
            if self.on_sent is not None:
                self.on_sent(list(message))
        suivre_envoi(self, message)

    def open_harp(self) -> None:
        self.send(HARP_OPEN)

    def close_harp(self) -> None:
        self.send(HARP_CLOSE)

    def close(self) -> None:
        """Rien a liberer : chaque envoi ouvre et referme le peripherique."""


def _write_raw(chemin: Path, data: bytes) -> None:
    fd = os.open(chemin, os.O_WRONLY)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)


class HarpListener:
    """Lit en permanence ce que la harpe envoie sur son port USB.

    Le Manager V24 garde l'entree de la harpe ouverte en continu ; sans cette
    lecture, la harpe ouvrait bien ses rayons mais ne detectait jamais la main
    (verifie le 13/11/2026). Ce fil vide donc le port tant que la harpe est la,
    et attend son retour si elle est debranchee. `on_data` recoit les octets lus.
    """

    def __init__(self, control: HarpControl | None = None, on_data=None,
                 retry: float = 3.0, opener=None) -> None:
        self.control = control or HarpControl()
        self.on_data = on_data
        self.retry = retry
        self._opener = opener or (lambda chemin: os.open(chemin, os.O_RDONLY))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.connected = False
        self.last_read_ns = 0

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def run_once(self) -> None:
        """Ouvre la harpe et lit jusqu'a ce qu'elle disparaisse (une passe)."""
        chemin = self.control.device()
        fd = self._opener(chemin)
        self.connected = True
        try:
            while not self._stop.is_set():
                data = os.read(fd, 256)
                if not data:
                    break
                # Instant de lecture, pour mesurer le delai jusqu'a l'envoi au synthe.
                self.last_read_ns = time.perf_counter_ns()
                if self.on_data is not None:
                    self.on_data(data)
        finally:
            self.connected = False
            try:
                os.close(fd)
            except OSError:
                pass

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_once()
            except (MidiError, OSError):
                pass
            self._stop.wait(self.retry)
