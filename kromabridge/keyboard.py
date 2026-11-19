"""Le clavier d'attribution : un instrument MIDI (D-50, KeyLab...) dont on lit
les notes pour les attribuer aux rayons, en mode "Attribuer au clavier".

Un seul objet rtmidi, garde ouvert d'un bout a l'autre (voir midi_io._release :
recreer des objets a chaque essai epuisait les clients ALSA du boitier).
"""

from __future__ import annotations

import re
import threading

from .midi_io import _release, _require_rtmidi

# Ports qu'on ne propose jamais comme clavier : la harpe elle-meme, et le port
# virtuel par lequel on envoie au logiciel de musique (on s'ecouterait soi-meme).
EXCLUS = ("kromalaser", "kromabridge", "rtmidi", "midi through", "controle harpe")


def ports_clavier(ports: list[str], exclus=EXCLUS) -> list[str]:
    return [p for p in ports if not any(x in p.lower() for x in exclus)]


class KeyboardListener:
    def __init__(self, hint_getter, on_note, retry: float = 3.0, factory=None,
                 on_message=None) -> None:
        # hint_getter() rend le nom (ou un bout du nom) du port choisi, ou "".
        self.hint_getter = hint_getter
        self.on_note = on_note
        # Tous les messages de notes (debuts et fins), pour les faire entendre.
        self.on_message = on_message
        self.retry = retry
        self._factory = factory
        self._in = None
        self._lister = None
        self._name: str | None = None
        self._stop = threading.Event()
        self.connected = False
        self.exclus = EXCLUS

    def _callback(self, event, _data=None) -> None:
        message, _delta = event
        if self.on_message is not None and len(message) >= 3 and message[0] & 0xF0 in (0x80, 0x90):
            self.on_message(list(message))
        if len(message) >= 3 and message[0] & 0xF0 == 0x90 and message[2] > 0:
            self.on_note(message[1])

    def ports(self) -> list[str]:
        """Les claviers proposes, sans le numero que le systeme ajoute en fin de
        nom (il change selon l'ordre de branchement)."""
        return [re.sub(r" \d+(:\d+)?$", "", p) for p in ports_clavier(self._liste())]

    def _nouveau(self):
        return (self._factory or _require_rtmidi().MidiIn)()

    def _liste(self) -> list[str]:
        # Un objet a part, jamais ouvert, sert seulement a lister les ports.
        if self._lister is None:
            self._lister = self._nouveau()
        return self._lister.get_ports()

    def _fermer(self) -> None:
        # Apres close_port puis open_port, rtmidi (ALSA) n'appelle plus le
        # rappel (constate sur le boitier le 17/11/2026) : on libere donc
        # l'objet entier et on en recree un a la prochaine ouverture.
        _release(self._in)
        self._in = None
        self._name = None

    def check(self) -> None:
        hint = (self.hint_getter() or "").lower()
        ports = self._liste() if hint else []
        index = None
        for i, nom in enumerate(ports):
            if hint in nom.lower() and nom in ports_clavier(ports, self.exclus):
                index = i
                break
        if index is None:
            if self._in is not None:
                self._fermer()
            self.connected = False
            return
        if self._in is not None and self._name != ports[index]:
            self._fermer()
        if self._in is None:
            self._in = self._nouveau()
            self._in.ignore_types(sysex=True, timing=True, active_sense=True)
            self._in.set_callback(self._callback)
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
        self._fermer()
        _release(self._lister)
        self._lister = None


class WindowsSynth:
    """Le synthe integre de Windows (Microsoft GS Wavetable Synth), ouvert a la
    premiere note seulement."""

    def __init__(self, factory=None) -> None:
        self._factory = factory
        self._out = None
        self._lock = threading.Lock()

    def send(self, messages) -> None:
        with self._lock:
            if self._out is None:
                out = (self._factory or _require_rtmidi().MidiOut)()
                ports = out.get_ports()
                index = next((i for i, p in enumerate(ports) if "wavetable" in p.lower()), None)
                if index is None:
                    _release(out)
                    return
                out.open_port(index)
                self._out = out
            for m in messages:
                self._out.send_message(list(m))
