"""Le moteur de remappage.

Le boitier ne voit pas des rayons, il voit des messages MIDI. Ce module traduit
ce qui arrive de la harpe en ce qui doit sortir vers le synthetiseur, en suivant
le preset actif.

Deux precautions valent d'etre signalees, parce qu'elles evitent les notes
bloquees en concert :

* la coupure d'une note (Note Off) utilise la correspondance qui etait active au
  moment de son declenchement, et non celle du preset courant ; changer de
  memoire alors qu'un rayon est encore tenu ne laisse donc rien de suspendu ;
* si deux rayons sont regles sur la meme note et tenus ensemble, relacher l'un
  ne coupe pas l'autre : le moteur compte les tenues et n'envoie la coupure
  qu'au dernier relachement.
"""

from __future__ import annotations

from collections import Counter

from .preset import DROP_UNKNOWN, Preset

NOTE_OFF = 0x80
NOTE_ON = 0x90
POLY_AFTERTOUCH = 0xA0
CONTROL_CHANGE = 0xB0
SUSTAIN = 64
SUSTAIN_THRESHOLD = 32
PROGRAM_CHANGE = 0xC0
CHANNEL_AFTERTOUCH = 0xD0
PITCH_BEND = 0xE0
BEND_CENTER = 8192
SYSTEM = 0xF0

ALL_NOTES_OFF = 123

Message = list[int]


class Engine:
    """Traduit un flux MIDI entrant selon un preset.

    `in_channel` vaut None pour accepter ce qui arrive sur n'importe quel canal,
    ce qui est le reglage normal avec une seule harpe branchee. Un message
    arrivant sur un autre canal que celui demande est transmis tel quel.

    `rechannel_others` renvoie les messages continus (molette, pression,
    controleurs) sur le canal de sortie du preset, pour qu'ils atteignent le
    meme son que les notes.
    """

    def __init__(
        self,
        preset: Preset,
        in_channel: int | None = None,
        rechannel_others: bool = True,
    ) -> None:
        self.preset = preset
        self.in_channel = in_channel
        self.rechannel_others = rechannel_others
        # (canal entrant, note entrante) -> (canal sortant, note sortante)
        self._held: dict[tuple[int, int], tuple[int, int]] = {}
        # (canal sortant, note sortante) -> nombre de rayons qui la tiennent
        self._sounding: Counter[tuple[int, int]] = Counter()
        # Notes d'accord declenchees avec chaque rayon tenu.
        self._held_extra: dict[tuple[int, int], list[tuple[int, int]]] = {}
        self._sustain_down = False
        # Sustain "un rayon a la fois" : l'application tient elle-meme les notes
        # relachees pendant que la pedale est enfoncee, et les coupe des qu'un
        # autre rayon est coupe (ou que la pedale remonte).
        self.sustain_mono = False
        self._tenues_pedale: list[tuple[int, int]] = []
        self._bent: set[int] = set()   # canaux sortants dont le pitch bend n'est pas au centre

    # ------------------------------------------------------------------ etat

    @property
    def held_notes(self) -> dict[tuple[int, int], tuple[int, int]]:
        """Les notes actuellement tenues, pour l'affichage et les tests."""
        return dict(self._held)

    def set_preset(self, preset: Preset) -> list[Message]:
        """Change de memoire et renvoie les coupures a emettre d'abord."""
        flush = self.panic()
        self.preset = preset
        return flush

    def panic(self) -> list[Message]:
        """Coupe tout ce qui sonne encore, note par note."""
        out: list[Message] = []
        for channel, note in sorted(self._sounding):
            out.append([NOTE_OFF | (channel - 1), note, 0])
        self._held.clear()
        self._held_extra.clear()
        self._tenues_pedale.clear()
        self._sounding.clear()
        return out

    def all_notes_off(self) -> list[Message]:
        """Coupures explicites, suivies du controleur 123 par securite."""
        out = self.panic()
        out.append([CONTROL_CHANGE | (self.preset.out_channel - 1), ALL_NOTES_OFF, 0])
        return out

    # --------------------------------------------------------------- moteur

    def process(self, message) -> list[Message]:
        """Traduit un message entrant en zero, un ou plusieurs messages sortants."""
        data = list(message)
        if not data:
            return []
        status = data[0]
        if status >= SYSTEM:
            # Horloge, sysex, demarrage, arret : on ne touche a rien.
            return [data]

        kind = status & 0xF0
        channel = (status & 0x0F) + 1
        if self.in_channel is not None and channel != self.in_channel:
            return [data]

        if kind == NOTE_ON and len(data) >= 3:
            if data[2] == 0:  # un Note On de velocite nulle est une coupure
                return self._note_off(channel, data[1])
            return self._note_on(channel, data[1], data[2])
        if kind == NOTE_OFF and len(data) >= 3:
            return self._note_off(channel, data[1])
        if kind == POLY_AFTERTOUCH and len(data) >= 3:
            return self._poly_aftertouch(channel, data[1], data[2])
        if kind == CONTROL_CHANGE and len(data) >= 3 and data[1] == SUSTAIN:
            return self._sustain(data)
        if kind == PITCH_BEND and len(data) >= 3:
            out = self._rechannel(data)
            if (out[2] << 7 | out[1]) == BEND_CENTER:
                self._bent.discard(out[0] & 0x0F)
            else:
                self._bent.add(out[0] & 0x0F)
            return [out]
        if kind in (CONTROL_CHANGE, CHANNEL_AFTERTOUCH, PITCH_BEND, PROGRAM_CHANGE):
            return [self._rechannel(data)]
        return [data]

    def _sustain(self, data: list[int]) -> list[Message]:
        """Pedale de sustain : tout ou rien, et seulement quand elle change d'etat.

        La pedale de Michael, branchee sur la harpe, n'envoie que 52 ou 63 au
        lieu de 127 (releve le 13/11/2026) : sous 64, un synthe la croit
        relachee. On la ramene donc a 0 ou 127, et on ignore ses rebonds.
        """
        enfoncee = data[2] >= SUSTAIN_THRESHOLD
        if enfoncee == self._sustain_down:
            return []
        self._sustain_down = enfoncee
        if self.sustain_mono or self._tenues_pedale:
            # l'application tient les notes elle-meme : rien a envoyer au synthe,
            # sauf les coupures des notes tenues quand la pedale remonte
            return [] if enfoncee else self._lacher_tenues()
        return [self._rechannel([data[0], SUSTAIN, 127 if enfoncee else 0])]

    def _lacher_tenues(self) -> list[Message]:
        out: list[Message] = []
        tenues, self._tenues_pedale = self._tenues_pedale, []
        for t in tenues:
            self._sounding[t] -= 1
            if self._sounding[t] > 0:
                continue
            del self._sounding[t]
            out.append([NOTE_OFF | (t[0] - 1), t[1], 0])
        return out

    # ---------------------------------------------------------------- notes

    def _note_on(self, channel: int, note: int, velocity: int) -> list[Message]:
        out: list[Message] = []
        if self._tenues_pedale:
            # un autre rayon est coupe : la note tenue par la pedale s'arrete
            out.extend(self._lacher_tenues())
        # Rayon redeclenche sans coupure intermediaire : on ferme l'ancienne.
        if (channel, note) in self._held:
            out.extend(self._note_off(channel, note))

        beam = self.preset.beam_for(note)
        if beam is None:
            if self.preset.unknown_notes == DROP_UNKNOWN:
                return out
            out.append([NOTE_ON | (channel - 1), note, velocity])
            return out

        target = self.preset.target(beam)
        if target is None:  # la transposition sort de l'etendue MIDI
            return out

        out_channel, out_note = target
        out.append(
            [NOTE_ON | (out_channel - 1), out_note, beam.velocity or velocity]
        )
        self._held[(channel, note)] = target
        self._sounding[target] += 1
        extras = self.preset.extra_targets(beam)
        for t in extras:
            out.append([NOTE_ON | (t[0] - 1), t[1], beam.velocity or velocity])
            self._sounding[t] += 1
        if extras:
            self._held_extra[(channel, note)] = extras
        return out

    def _note_off(self, channel: int, note: int) -> list[Message]:
        target = self._held.pop((channel, note), None)
        if target is None:
            # Coupure sans declenchement connu : on applique la table statique,
            # ce qui evite de laisser sonner une note si un Note On s'est perdu.
            beam = self.preset.beam_for(note)
            if beam is None:
                if self.preset.unknown_notes == DROP_UNKNOWN:
                    return []
                return [[NOTE_OFF | (channel - 1), note, 0]]
            target = self.preset.target(beam)
            if target is None:
                return []
            return [[NOTE_OFF | (c - 1), n, 0] for c, n in [target] + self.preset.extra_targets(beam)]

        cibles = [target] + self._held_extra.pop((channel, note), [])
        if self.sustain_mono and self._sustain_down:
            # pedale enfoncee : la note continue jusqu'au prochain rayon
            self._tenues_pedale.extend(cibles)
            return []
        out: list[Message] = []
        for t in cibles:
            self._sounding[t] -= 1
            if self._sounding[t] > 0:
                # Un autre rayon tient encore cette note : on ne la coupe pas.
                continue
            del self._sounding[t]
            out.append([NOTE_OFF | (t[0] - 1), t[1], 0])
        if not out:
            return []
        return out + self._spring_back(target[0])

    def _spring_back(self, out_channel: int) -> list[Message]:
        """Comme le ressort d'une molette : quand plus aucune note ne sonne sur
        ce canal, le pitch bend revient au centre (demande de Michael, 16/11/2026)."""
        canal = out_channel - 1
        if canal not in self._bent:
            return []
        if any(ch == out_channel for ch, _ in self._sounding):
            return []
        self._bent.discard(canal)
        return [[PITCH_BEND | canal, BEND_CENTER & 0x7F, BEND_CENTER >> 7]]

    def _poly_aftertouch(self, channel: int, note: int, value: int) -> list[Message]:
        target = self._held.get((channel, note))
        if target is None:
            beam = self.preset.beam_for(note)
            target = self.preset.target(beam) if beam else None
        if target is None:
            if self.preset.unknown_notes == DROP_UNKNOWN:
                return []
            return [[POLY_AFTERTOUCH | (channel - 1), note, value]]
        out_channel, out_note = target
        return [[POLY_AFTERTOUCH | (out_channel - 1), out_note, value]]

    def _rechannel(self, data: Message) -> Message:
        if not self.rechannel_others:
            return data
        return [(data[0] & 0xF0) | (self.preset.out_channel - 1), *data[1:]]
