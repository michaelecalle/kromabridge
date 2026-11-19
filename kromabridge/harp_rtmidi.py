"""La harpe par rtmidi : pour la version PC (Windows), ou /dev/snd n'existe pas.

Meme interface que HarpControl / HarpListener (midi_io), mais en passant par
les ports MIDI du systeme. Les memes precautions s'appliquent : la harpe doit
etre lue en continu, et chaque commande est suivie de deux relances.
"""

from __future__ import annotations

import threading
import time

from .midi_io import HARP_CLOSE, HARP_NUDGE, HARP_OPEN, MidiError, _release, _require_rtmidi, find_port, preparer_ouverture, renvoyer_ouverture, suivre_envoi

# Sous Windows, la harpe s'appelle "Kromalaser USB" (plus un numero). On cherche
# ce nom precis pour ne pas confondre avec un port virtuel cree par l'utilisateur.
PC_HARP_HINT = "kromalaser usb"


class RtHarpControl:
    def __init__(self, hint: str = PC_HARP_HINT, factory=None) -> None:
        self.hint = hint
        self._factory = factory
        self._out = None
        self._name: str | None = None
        self._lock = threading.Lock()
        self.on_sent = None

    def device(self) -> str:
        """Le nom du port de la harpe (ouvre la sortie si besoin)."""
        if self._out is None:
            self._out = (self._factory or _require_rtmidi().MidiOut)()
        ports = self._out.get_ports()
        index = find_port(ports, self.hint)
        if self._name != ports[index] or not self._out.is_port_open():
            if self._out.is_port_open():
                self._out.close_port()
            self._out.open_port(index)
            self._name = ports[index]
        return self._name

    def _write(self, message) -> None:
        try:
            self._out.send_message(list(message))
        except Exception as exc:
            self._name = None
            raise MidiError(f"harpe injoignable : {exc}") from exc

    def send(self, message, renvoi: bool = True) -> None:
        if renvoi and list(message) == HARP_OPEN:
            preparer_ouverture(self)
        with self._lock:
            self.device()
            self._write(message)
            self._write(HARP_NUDGE)
            self._write(HARP_NUDGE)
        if self.on_sent is not None:
            self.on_sent(list(message))
        suivre_envoi(self, message)
        if renvoi:
            renvoyer_ouverture(self, message)

    def send_quick(self, message) -> None:
        with self._lock:
            self.device()
            self._write(message)
        if self.on_sent is not None:
            self.on_sent(list(message))
        suivre_envoi(self, message)

    def open_harp(self) -> None:
        self.send(HARP_OPEN)

    def close_harp(self) -> None:
        self.send(HARP_CLOSE)

    def close(self) -> None:
        with self._lock:
            _release(self._out)
            self._out = None
            self._name = None


class ProcHarpControl:
    """Comme RtHarpControl, mais les envois passent par un petit programme
    separe (harp_worker), surveille : si la harpe ne repond plus et que l'envoi
    bloque, on l'arrete au bout de `delai` secondes au lieu de figer tout le
    programme, et on signale "harpe injoignable"."""

    def __init__(self, hint: str = PC_HARP_HINT, delai: float = 3.0, lancer=None) -> None:
        self.hint = hint
        self.delai = delai
        self._lancer = lancer          # pour les essais : fabrique du sous-programme
        self._proc = None
        self._reponses = None
        self._lock = threading.Lock()
        self._name: str | None = None
        self.on_sent = None
        self.injoignable = False
        self.on_erreur = None

    def _demarrer(self) -> None:
        import queue
        import subprocess
        import sys
        from pathlib import Path
        if self._lancer is not None:
            self._proc = self._lancer()
        else:
            options = {}
            if sys.platform == "win32":
                options["creationflags"] = 0x08000000      # CREATE_NO_WINDOW
            self._proc = subprocess.Popen(
                [sys.executable, "-m", "kromabridge.harp_worker"],
                cwd=str(Path(__file__).resolve().parents[1]),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1, **options)
        self._reponses = queue.Queue()
        proc, reponses = self._proc, self._reponses

        def lire() -> None:
            for ligne in proc.stdout:
                reponses.put(ligne.rstrip(chr(10)))
            reponses.put(None)

        threading.Thread(target=lire, daemon=True).start()

    def _arreter(self) -> None:
        if self._proc is not None:
            try:
                self._proc.kill()
            except Exception:
                pass
        self._proc = None
        self._name = None

    def _envoyer(self, messages) -> str:
        import json
        import queue
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                self._demarrer()
            try:
                self._proc.stdin.write(json.dumps({"m": [list(m) for m in messages], "hint": self.hint}) + chr(10))
                self._proc.stdin.flush()
                reponse = self._reponses.get(timeout=self.delai)
            except (queue.Empty, OSError, ValueError):
                reponse = None
            if reponse is None:
                self._arreter()
                self.injoignable = True
                if self.on_erreur is not None:
                    self.on_erreur("harpe injoignable : elle ne répond plus")
                raise MidiError("harpe injoignable : elle ne répond plus")
            if reponse.startswith("err"):
                self._name = None
                raise MidiError("harpe injoignable : " + reponse[4:])
            self.injoignable = False
            self._name = reponse[3:]
            return self._name

    def device(self) -> str:
        return self._envoyer([])

    def send(self, message, renvoi: bool = True) -> None:
        if renvoi and list(message) == HARP_OPEN:
            preparer_ouverture(self)
        self._envoyer([message, HARP_NUDGE, HARP_NUDGE])
        if self.on_sent is not None:
            self.on_sent(list(message))
        suivre_envoi(self, message)
        if renvoi:
            renvoyer_ouverture(self, message)

    def send_quick(self, message) -> None:
        self._envoyer([message])
        if self.on_sent is not None:
            self.on_sent(list(message))
        suivre_envoi(self, message)

    def open_harp(self) -> None:
        self.send(HARP_OPEN)

    def close_harp(self) -> None:
        self.send(HARP_CLOSE)

    def close(self) -> None:
        with self._lock:
            self._arreter()


class RtHarpListener:
    """Lit la harpe en continu par un rappel rtmidi, et la rouvre si elle revient."""

    def __init__(self, control: RtHarpControl, on_data=None, retry: float = 3.0,
                 factory=None) -> None:
        self.control = control
        self.on_data = on_data
        self.retry = retry
        self._factory = factory
        self._in = None
        self._name: str | None = None
        self._stop = threading.Event()
        self.connected = False
        self.last_read_ns = 0

    def _callback(self, event, _data=None) -> None:
        message, _delta = event
        self.last_read_ns = time.perf_counter_ns()
        if self.on_data is not None:
            self.on_data(bytes(message))

    def check(self) -> None:
        """Ouvre l'entree de la harpe si elle est la et pas encore ouverte."""
        if self._in is None:
            self._in = (self._factory or _require_rtmidi().MidiIn)()
            self._in.ignore_types(sysex=False, timing=False, active_sense=True)
            self._in.set_callback(self._callback)
        ports = self._in.get_ports()
        try:
            index = find_port(ports, self.control.hint)
        except MidiError:
            if self._in.is_port_open():
                self._in.close_port()
            self.connected = False
            self._name = None
            return
        if self._name != ports[index] or not self._in.is_port_open():
            if self._in.is_port_open():
                self._in.close_port()
            self._in.open_port(index)
            self._name = ports[index]
        self.connected = True

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.check()
            except Exception:
                self.connected = False
            self._stop.wait(self.retry)

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        _release(self._in)
        self._in = None
