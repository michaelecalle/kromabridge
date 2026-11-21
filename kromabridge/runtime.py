"""L'etat vivant du boitier.

Tout ce que la page web affiche ou modifie passe par ici, et rien dans ce module
ne depend d'un port MIDI ni d'un serveur : il fabrique des messages et les confie
a une fonction d'emission. C'est ce qui permet de l'essayer sans materiel.
"""

from __future__ import annotations

import copy
import json
import threading
from collections import deque
from collections.abc import Callable
from pathlib import Path

from . import harp
from .bank import Bank, harp_messages
from .engine import Engine, Message
from .learn import Learner
from .preset import Beam, Preset

LANGUES = ("fr", "en")
SONS_CLAVIER = ("aucun", "sortie", "windows", "harpe_pc", "page")
SONS_JEU = ("aucun", "harpe_pc", "page")

Sender = Callable[[list[Message]], None]


def _noop(_messages: list[Message]) -> None:
    """Emission par defaut : on jette. Utile pour les essais et le mode moniteur."""


# Numerotation des sons du synthe affichee sur la page : 1 a 128, 0 a 127, ou
# groupes de 8 numerotes 11 a 88 (Roland D-50...). En interne : toujours 1 a 128.
NUMEROTATIONS = ("1-128", "0-127", "groupes8")


class Runtime:
    """Le boitier en marche : une banque, un moteur, et un mode apprentissage.

    Un verrou protege l'ensemble, parce que les messages MIDI arrivent sur le fil
    de la bibliotheque audio pendant que la page web peut modifier une memoire.
    """

    def __init__(
        self,
        bank: Bank,
        send: Sender | None = None,
        presets_dir: str | Path | None = None,
        history: int = 24,
        harp_send: Sender | None = None,
        harp_recall: Callable[[int], object] | None = None,
        harp_capture: Callable[[], dict] | None = None,
        gamme: bool = False,
    ) -> None:
        self.bank = bank
        self.send = send or _noop
        # Ce qui s'adresse a la harpe (nombre de rayons, reglages, preset) part
        # sur son port USB quand on sait l'y envoyer ; sinon, comme avant, par
        # la sortie generale.
        self.harp_send = harp_send
        self.harp_recall = harp_recall
        self.harp_capture = harp_capture
        self.presets_dir = Path(presets_dir) if presets_dir else None
        self.lock = threading.RLock()
        self.learner: Learner | None = None
        self.incoming = deque(maxlen=history)
        self.last_note: int | None = None
        # Parametres de l'application (nommage des notes...), a cote des morceaux.
        self.parametres = {"langue": "fr", "notes": "", "clavier": "", "son_clavier": "", "sortie_audio": "",
                           "sortie_midi": "", "son_jeu": "aucun", "controle_actif": False, "controle_entree": "", "clavier_joue": False,
                           "numerotation": "1-128"}
        # Sorties MIDI disponibles (branche par main) et celle d'origine.
        self.midi_outputs: Callable[[], list[str]] = lambda: []
        self.midi_output_default = ""
        # Son de la harpe : joue sur l'ordinateur (version PC) ou envoye aux pages
        # ouvertes (flux d'evenements), qui le jouent elles-memes.
        self.audio_pc = None
        self._abonnes: list = []
        # Ou faire entendre le clavier : "aucun", "sortie" (synthe ou logiciel),
        # "windows" (synthe integre de Windows, envoye par `send_windows`).
        self.send_windows: Callable[[list], None] | None = None
        # Piste de controle : ouvrir / fermer la harpe (branches par main).
        self.harp_open: Callable[[], None] | None = None
        self.harp_close: Callable[[], None] | None = None
        self.control_connected: Callable[[], bool] = lambda: False
        self.part_switcher: Callable[[int], None] | None = None
        self.control_log = deque(maxlen=20)
        # Mode "Attribuer au clavier" : le dernier rayon coupe recoit la note
        # jouee sur le clavier d'attribution.
        self.attribution = False
        self._avant_attribution: tuple | None = None
        # Apprentissage au clavier : les notes jouees (None = inactif).
        self.kb_notes: list[int] | None = None
        self.keyboard_ports: Callable[[], list[str]] = lambda: []
        self.keyboard_connected: Callable[[], bool] = lambda: False
        # Reglages actuels de la harpe (tenus a jour par le service de reglages),
        # pour signaler ceux qui different de ce qui est memorise dans le morceau.
        self.harp_values: Callable[[], dict] = lambda: {}
        # Fichier MIDI en cours d'import, et son ecoute.
        from .player import Lecteur
        self.midi = None
        self.midi_brut: bytes | None = None
        # La pre-ecoute part comme le jeu : sortie MIDI, et son de la harpe si choisi.
        self.lecteur = Lecteur(lambda messages: (self.send(messages), self._son_jeu(messages)))
        self.harp_applied: Callable[[dict], None] = lambda valeurs: None
        self._charge_parametres()
        # Listes : ordre de la liste par defaut (= numeros des morceaux pour la
        # piste de controle) et listes de morceaux.
        self.listes: dict = {"listes": [], "active": None}
        self._charge_listes()
        if gamme:
            self._assure_gamme()

    # ----------------------------------------------------------------- lecture

    @property
    def engine(self) -> Engine:
        return self.bank.engine

    def state(self) -> dict:
        """Ce que la page web affiche."""
        with self.lock:
            preset = self.bank.current
            return {
                "memoire": preset.name,
                "index": self.bank.index,
                "memoires": self.bank.names,
                "rayons_allumes": preset.beam_count if preset.beam_count is not None else (
                    preset.harp_snapshot["rayons"] + 5 if "rayons" in preset.harp_snapshot else None),
                "preset_harpe": preset.harp_preset,
                "harpe_memorisee": bool(preset.harp_snapshot),
                "harpe_modifiee": self._harpe_modifiee(preset),
                "harpe_communs": sorted(self._communs()),
                "animation": dict(preset.animation),
                "canal_sortie": preset.out_channel,
                "son_synthe": dict(preset.synth),
                "parties": [str(x.get("nom", "")) for x in preset.parts],
                "import": bool(preset.import_info),
                "partie": preset.part_index,
                "attribution": self.attribution,
                "son_clavier": self.parametres.get("son_clavier", ""),
                "son_jeu": self.parametres.get("son_jeu", "aucun"),
                "clavier_joue": bool(self.parametres.get("clavier_joue")),
                "apprentissage_clavier": self.kb_notes is not None,
                "notes_apprises": sorted(self.kb_notes or []),
                "apprentissage": self.learner is not None,
                "rayons_appris": self.learner.count if self.learner else 0,
                "derniere_note": self.last_note,
                "notes_tenues": len(self.engine.held_notes),
                "listes": [x["nom"] for x in self.listes["listes"]],
                "liste_active": self.listes.get("active"),
                "gamme": preset.gamme,
                "index_gamme": next((i for i, p in enumerate(self.bank.presets) if p.gamme), None),
                "listes_detail": {x["nom"]: [n for n in x["morceaux"] if n in self.bank.names]
                                  for x in self.listes["listes"]},
                "liste_morceaux": self._indices_liste(self.listes.get("active")),
                "rayons": [beam.to_dict() for beam in preset.beams],
            }

    # ------------------------------------------------------------------- notes

    def handle(self, message) -> list[Message]:
        """Traite un message venu de la harpe et emet ce qu'il faut."""
        with self.lock:
            data = list(message)
            self.incoming.append(data)
            if len(data) >= 3 and data[0] & 0xF0 == 0x90 and data[2] > 0:
                self.last_note = data[1]
            if self.learner is not None:
                # En apprentissage, on ecoute sans rien renvoyer au synthetiseur :
                # inutile d'entendre les notes d'origine pendant le reperage.
                self.learner.feed(data)
                return []
            self.engine.sustain_mono = self._pedale_un_rayon()
            out = self.engine.process(data)
        if out:
            self.send(out)
            self._son_jeu(out)
        return out

    def _son_jeu(self, messages) -> None:
        """Le son de la harpe en plus du MIDI, quand on joue (mode jeu)."""
        son = self.parametres.get("son_jeu") or "aucun"
        if son == "aucun":
            return
        for m in messages:
            if len(m) < 3 or (m[0] & 0xF0) not in (0x80, 0x90):
                continue
            if son == "harpe_pc" and self.audio_pc is not None:
                self.audio_pc.message(list(m[:3]))
            elif son == "page":
                on = (m[0] & 0xF0) == 0x90 and m[2] > 0
                self.diffuser({"t": "on" if on else "off", "note": m[1], "vel": m[2]})

    # ---------------------------------------------------------------- memoires

    def select(self, index: int) -> list[Message]:
        with self.lock:
            cible = self.bank.presets[index] if 0 <= index < len(self.bank.presets) else None
            if cible is not None and cible.parts and cible.part_index != 0:
                # Un morceau charge repart toujours de sa premiere partie (A).
                cible.sync_part()
                cible.part_index = 0
                cible.beams = [Beam.from_dict(b) for b in cible.parts[0]["rayons"]]
                cible.synth = cible.part_synth(0)
                if harp.MIN_BEAMS <= len(cible.beams) <= harp.MAX_BEAMS:
                    cible.beam_count = len(cible.beams)
                    if "rayons" in cible.harp_snapshot:
                        cible.harp_snapshot["rayons"] = len(cible.beams) - harp.MIN_BEAMS
            if self.harp_send is None:
                out = self.bank.select(index)
                harp_out: list[Message] = []
            else:
                out = self.bank.select(index, include_harp=False)
                harp_out = self._messages_harpe(self.bank.current)
            preset_harpe = self.bank.current.harp_preset
        self.send(out)
        self._configure_harp(preset_harpe, harp_out)
        if self.attribution:
            with self.lock:
                self._copie_rayons()
        return out + harp_out

    def _configure_harp(self, preset_harpe: int | None, harp_out: list[Message]) -> None:
        """Rappelle le preset de la harpe, puis applique les reglages du morceau."""
        if preset_harpe is not None and self.harp_recall is not None:
            try:
                self.harp_recall(preset_harpe)
            except Exception:   # harpe absente : le morceau reste charge
                pass
        if harp_out and self.harp_send is not None:
            try:
                self.harp_send(harp_out)
            except Exception:
                pass
            else:
                with self.lock:
                    instantane = self._harpe_attendue(self.bank.current)
                self.harp_applied(instantane)

    def set_animation(self, active: bool, pause: float, duree: float,
                      transition: bool | None = None, trans_duree: float | None = None) -> None:
        """Regle l'animation d'ouverture (et la transition entre parties) du
        morceau courant, et l'enregistre."""
        if not (0 <= pause <= 30 and 0 <= duree <= 30):
            raise ValueError("pause et durée doivent être entre 0 et 30 secondes")
        if trans_duree is not None and not 0 <= trans_duree <= 10:
            raise ValueError("la durée de transition doit être entre 0 et 10 secondes")
        with self.lock:
            preset = self.bank.current
            avant = dict(preset.animation)
            preset.animation = {"active": bool(active), "pause": float(pause), "duree": float(duree),
                                "transition": bool(avant.get("transition", False) if transition is None else transition),
                                "trans_duree": float(avant.get("trans_duree", 1.0) if trans_duree is None else trans_duree)}
            self._save(preset)

    def transition_plan(self, largeur_actuelle: int | None) -> dict | None:
        """Ce qu'il faut pour une transition animee entre parties, ou None."""
        with self.lock:
            preset = self.bank.current
            anim = preset.animation
            if not anim.get("transition"):
                return None
            largeur = self._harpe_attendue(preset).get("largeur", largeur_actuelle)
            return {"largeur": 40 if largeur is None else largeur,
                    "duree": float(anim.get("trans_duree", 1.0))}

    def change_part(self, index: int, attendre: bool = False) -> None:
        """Changer de partie : animee si le morceau le demande (branche par main).
        `attendre` : ne rendre la main qu'une fois la transition finie."""
        if self.part_switcher is not None:
            self.part_switcher(index, attendre)
        else:
            self.select_part(index)

    def opening_plan(self, largeur_actuelle: int | None) -> dict | None:
        """Ce qu'il faut pour ouvrir en animation, ou None si le morceau n'en veut pas."""
        with self.lock:
            preset = self.bank.current
            anim = preset.animation
            if not anim.get("active"):
                return None
            largeur = self._harpe_attendue(preset).get("largeur", largeur_actuelle)
            return {"largeur": 40 if largeur is None else largeur,
                    "pause": float(anim.get("pause", 2)), "duree": float(anim.get("duree", 3))}

    def capture_harp(self) -> dict:
        """Lit la harpe et range tous ses reglages actuels dans le morceau courant."""
        if self.harp_capture is None:
            raise RuntimeError("la harpe n'est pas reliée au Pi")
        valeurs = self.harp_capture()
        with self.lock:
            preset = self.bank.current
            preset.harp_snapshot = dict(valeurs)
            # Le nombre de rayons fait desormais partie de l'instantane.
            preset.beam_count = None
            self._save(preset)
            return dict(preset.harp_snapshot)

    def reload_current(self) -> list[Message]:
        """Relit le morceau courant depuis son fichier et le renvoie a la harpe,
        pour revenir a ce qui a ete enregistre apres une fausse manoeuvre."""
        with self.lock:
            preset = self.bank.current
            if preset.source is not None and preset.source.exists():
                self.bank.presets[self.bank.index] = Preset.load(preset.source)
            index = self.bank.index
        return self.select(index)

    # --------------------------------------------------- presets de la harpe

    def cible_preset_harpe(self, actuels: dict) -> tuple[dict, bool]:
        """Les reglages a enregistrer dans un preset de la harpe pour le morceau
        courant : ses reglages memorises (et communs), completes par les reglages
        actuels, avec son nombre de rayons. Si ses rayons forment une gamme
        chromatique continue, Transpose et Octave reproduisent ses notes.
        Rend (reglages, notes_reproduites)."""
        from . import presets_harpe as ph
        with self.lock:
            preset = self.bank.current
            cible = dict(actuels)
            cible.update(self._harpe_attendue(preset))
            if "rayons" not in self._harpe_attendue(preset):
                cible["rayons"] = max(0, min(10, len(preset.beams) - 5))
            d = ph.decalage_chromatique(preset.beams)
            reglages = ph.vers_transpo_octave(d) if d is not None else None
            cible.update(reglages or ph.vers_transpo_octave(0))
            return cible, reglages is not None

    def song_from_harp_preset(self, numero: int, valeurs: dict, nouveau: bool, nom: str | None = None) -> Preset:
        """Un preset de la harpe dans un nouveau morceau (ses reglages, et les notes
        qu'il joue : la gamme de la harpe, decalee par Transpose et Octave), ou ses
        reglages seulement dans le morceau courant."""
        from . import presets_harpe as ph
        from .harp_service import LOCKED_AT_ZERO
        reglages = {k: int(v) for k, v in valeurs.items()}
        d = ph.decalage(reglages)
        reglages.update(LOCKED_AT_ZERO)       # la transposition se fait dans Kromabridge
        rayons = 5 + int(reglages.get("rayons", 4))
        if nouveau:
            nom = (nom or f"Preset {numero}").strip()
            with self.lock:
                base_nom, k = nom, 2
                while nom in self.bank.names:
                    nom, k = f"{base_nom} ({k})", k + 1
            self.new_song(nom, rayons=rayons)
        with self.lock:
            preset = self.bank.current
            if preset.gamme:
                raise ValueError("le morceau Gamme ne peut pas recevoir les réglages d’un preset")
            preset.harp_snapshot = {k: v for k, v in reglages.items() if k not in self._communs()}
            preset.beam_count = None
            if nouveau:
                base = min((b.incoming_note for b in preset.beams), default=60)
                preset.beams = [Beam(index=i, incoming_note=base + i - 1, note=max(0, min(127, base + i - 1 + d)))
                                for i in range(1, rayons + 1)]
                preset.sync_part()
            self._save(preset)
            out = self._messages_harpe(preset)
        if self.harp_send is None:
            self.send(out)
        else:
            self._configure_harp(None, out)
        return preset

    def set_harp_preset(self, number: int | None) -> None:
        """Associe au morceau courant un preset de la harpe (ou aucun), et l'enregistre."""
        with self.lock:
            preset = self.bank.current
            if number is not None and not 1 <= number <= 40:
                raise ValueError(f"preset de la harpe hors de 1..40 : {number}")
            preset.harp_preset = number
            self._save(preset)
            preset_harpe = preset.harp_preset
        self._configure_harp(preset_harpe, [])

    def reload_harp(self) -> list[Message]:
        """Renvoie a la harpe les reglages de la memoire courante.

        Sert quand la harpe a ete rallumee apres le boitier, ou apres son
        animation d'ouverture, tant qu'on ne sait pas quel est le bon moment.
        """
        with self.lock:
            out = self._messages_harpe(self.bank.current)
        if self.harp_send is None:
            self.send(out)
        else:
            self._configure_harp(None, out)
        return out

    # ------------------------------------------------------ attribution clavier

    def set_attribution(self, active: bool, annuler: bool = False) -> None:
        """Mode "Changer un rayon au clavier". En entrant, on garde une copie des
        rayons du morceau ; en sortant avec `annuler`, on la remet."""
        restaurer = False
        with self.lock:
            if active and not self.attribution:
                self._copie_rayons()
            if not active and annuler and self._avant_attribution is not None:
                nom, rayons = self._avant_attribution
                preset = self.bank.current
                if preset.name == nom:
                    preset.beams = [Beam.from_dict(b) for b in rayons]
                    self._save(preset)
                    restaurer = True
            self.attribution = bool(active)
            if active:
                self.kb_notes = None
            else:
                self._avant_attribution = None
        if restaurer:
            self.panic()

    def _copie_rayons(self) -> None:
        preset = self.bank.current
        self._avant_attribution = (preset.name, [b.to_dict() for b in preset.beams])

    def choose_beam(self, index: int) -> None:
        """Choisit un rayon depuis l'ecran, comme si on l'avait coupe (sans harpe)."""
        with self.lock:
            beam = next((b for b in self.bank.current.beams if b.index == index), None)
            if beam is None:
                raise ValueError(f"pas de rayon {index}")
            self.last_note = beam.incoming_note

    def selected_beam(self) -> int | None:
        """Le rayon selectionne : le dernier coupe sur la harpe."""
        with self.lock:
            if self.last_note is None:
                return None
            for beam in self.bank.current.beams:
                if beam.incoming_note == self.last_note:
                    return beam.index
        return None

    def control_message(self, message: list[int]) -> str | None:
        """Un message du port de controle (canal 16) : charger un morceau, ouvrir
        ou fermer la harpe, changer de partie. Les commandes sont executees une
        par une, dans l'ordre, chacune attendant la fin de la precedente (une
        animation d'ouverture n'est plus coupee par un changement de partie)."""
        from . import controle as ct
        if not self.parametres.get("controle_actif"):
            return None
        if len(message) < 2 or message[0] & 0x0F != ct.CANAL - 1:
            return None
        genre = message[0] & 0xF0
        if genre == 0xC0:
            action, quoi = ("morceau", message[1]), f"morceau {message[1] + 1}"
        elif genre == 0x90 and len(message) >= 3 and message[2] > 0:
            note = message[1]
            if note == ct.NOTE_OUVRIR:
                action, quoi = ("ouvrir", None), "ouverture de la harpe"
            elif note == ct.NOTE_FERMER:
                action, quoi = ("fermer", None), "fermeture de la harpe"
            elif note >= ct.NOTE_PARTIE_A:
                action, quoi = ("partie", note - ct.NOTE_PARTIE_A), f"partie {chr(65 + note - ct.NOTE_PARTIE_A)}"
            else:
                return None
        else:
            return None
        self._file_commandes().put(action)
        return quoi

    def _file_commandes(self):
        import queue
        with self.lock:
            if getattr(self, "_commandes", None) is None:
                self._commandes = queue.Queue()
                threading.Thread(target=self._executer_commandes, daemon=True).start()
            return self._commandes

    def control_wait(self) -> None:
        """Attend que toutes les commandes recues soient executees (essais)."""
        if getattr(self, "_commandes", None) is not None:
            self._commandes.join()

    def _executer_commandes(self) -> None:
        import time as _t
        while True:
            genre, valeur = self._commandes.get()
            fait = None
            try:
                if genre == "morceau":
                    if valeur == self.bank.index:
                        # Le sequenceur renvoie le changement de programme a chaque
                        # depart de lecture : morceau deja charge, on ne renvoie pas
                        # tous les reglages a la harpe (elle n'obeissait plus ensuite
                        # a l'ouverture). On revient seulement a la partie A.
                        if self.bank.current.parts and self.bank.current.part_index != 0:
                            self.change_part(0, attendre=True)
                        fait = f"morceau {valeur + 1} : {self.bank.current.name} (déjà chargé)"
                    elif valeur < len(self.bank.presets):
                        self.select(valeur)
                        fait = f"morceau {valeur + 1} : {self.bank.current.name}"
                elif genre == "ouvrir" and self.harp_open is not None:
                    self.harp_open()   # renvoyee une seconde apres par le controle de la harpe
                    fait = "ouverture de la harpe"
                elif genre == "fermer" and self.harp_close is not None:
                    self.harp_close()
                    fait = "fermeture de la harpe"
                elif genre == "partie" and valeur < len(self.bank.current.parts):
                    self.change_part(valeur, attendre=True)
                    fait = "partie " + str(self.bank.current.parts[valeur].get("nom", valeur + 1))
            except Exception as exc:
                fait = f"erreur : {exc}"
            finally:
                self._commandes.task_done()
            if fait:
                self.control_log.appendleft(_t.strftime("%H:%M:%S ") + fait)
                self.journal("commande : " + fait)

    # ------------------------------------------------- journal sur fichier

    def journal(self, texte: str) -> None:
        """Ajoute une ligne horodatee au fichier journal.txt (a cote des
        morceaux). Il survit a la fermeture du programme."""
        if self.presets_dir is None:
            return
        from datetime import datetime
        f = self.presets_dir.parent / "journal.txt"
        try:
            if f.exists() and f.stat().st_size > 2_000_000:
                f.replace(f.with_name("journal.1.txt"))
            with open(f, "a", encoding="utf-8") as sortie:
                sortie.write(datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + " " + texte + "\n")
        except OSError:
            pass

    # ------------------------------------------------- flux vers les pages

    def abonner(self):
        import queue
        q = queue.Queue(maxsize=500)
        with self.lock:
            self._abonnes.append(q)
        return q

    def desabonner(self, q) -> None:
        with self.lock:
            if q in self._abonnes:
                self._abonnes.remove(q)

    def diffuser(self, evenement: dict) -> None:
        with self.lock:
            abonnes = list(self._abonnes)
        for q in abonnes:
            try:
                q.put_nowait(evenement)
            except Exception:
                pass

    def keyboard_echo(self, message: list[int]) -> None:
        """Fait entendre une note jouee au clavier pendant l'apprentissage ou
        l'attribution (un clavier maitre n'a pas de son a lui). Hors
        apprentissage, avec « Le clavier joue comme la harpe », la note part vers
        la sortie du morceau (secours si la harpe lache en concert)."""
        if not (self.attribution or self.kb_notes is not None):
            if self.parametres.get("clavier_joue"):
                self._clavier_joue(message)
            return
        son = self.parametres.get("son_clavier") or "aucun"
        with self.lock:
            canal = self.bank.current.out_channel - 1
        msg = [(message[0] & 0xF0) | canal, message[1], message[2]]
        if son == "sortie":
            self.send([msg])
        elif son == "harpe_pc" and self.audio_pc is not None:
            self.audio_pc.message(list(message[:3]))
        elif son == "page":
            on = (message[0] & 0xF0) == 0x90 and message[2] > 0
            self.diffuser({"t": "on" if on else "off", "note": message[1], "vel": message[2]})
        elif son == "windows" and self.send_windows is not None:
            try:
                self.send_windows([[(message[0] & 0xF0), message[1], message[2]]])
            except Exception:
                pass

    def keyboard_active(self) -> bool:
        """Le clavier doit-il etre ecoute (attribution, apprentissage, ou
        « Le clavier joue comme la harpe ») ?"""
        return self.attribution or self.kb_notes is not None or bool(self.parametres.get("clavier_joue"))

    def _clavier_joue(self, message: list[int]) -> None:
        """Une note du clavier, telle quelle, sur le canal du morceau ; si c'est
        la note d'un rayon, sa case s'allume sur la page comme s'il etait coupe."""
        with self.lock:
            preset = self.bank.current
            canal = preset.out_channel - 1
            msg = [(message[0] & 0xF0) | canal, message[1], message[2]]
            if (message[0] & 0xF0) == 0x90 and message[2] > 0:
                for beam in preset.beams:
                    cible = preset.target(beam)
                    if cible is not None and cible[1] == message[1]:
                        self.last_note = beam.incoming_note
                        break
        self.send([msg])
        self._son_jeu([msg])

    def start_kb_learning(self) -> None:
        with self.lock:
            self.kb_notes = []
            self.attribution = False

    def clear_kb_learning(self) -> None:
        """Repart de zero pour la partie suivante, sans quitter l'apprentissage."""
        with self.lock:
            if self.kb_notes is not None:
                self.kb_notes = []

    def cancel_kb_learning(self) -> None:
        with self.lock:
            self.kb_notes = None

    def finish_kb_learning(self, name: str, notes: list[int], remplacer: bool = False,
                           parties: list[dict] | None = None) -> Preset | None:
        """Cree un morceau avec ces notes (ou remplace celles du morceau affiche).
        Avec `parties`, cree un morceau en plusieurs parties."""
        if remplacer:
            self._proteger_gamme()
        if parties:
            preset = self.new_song_from_parts(name, parties)
            with self.lock:
                self.kb_notes = None
            return preset
        if remplacer:
            notes = self._notes_rayons(notes)
            with self.lock:
                self.kb_notes = None
            self.set_beam_count(len(notes))
            with self.lock:
                preset = self.bank.current
                for beam, n in zip(preset.beams, notes):
                    beam.note = n
                self._save(preset)
            return None
        preset = self.new_song_from_notes(name, notes)
        with self.lock:
            self.kb_notes = None
        return preset

    def keyboard_note(self, note: int) -> int | None:
        """Une note jouee au clavier : en apprentissage, on la retient ; en mode
        attribution, elle devient celle du rayon selectionne. Renvoie le rayon
        modifie (ou None)."""
        with self.lock:
            if self.kb_notes is not None:
                if note not in self.kb_notes:
                    self.kb_notes.append(note)
                return None
            if not self.attribution:
                return None
        rayon = self.selected_beam()
        if rayon is None:
            return None
        self.set_beam_note(rayon, note)
        return rayon

    def preview(self, note: int, duree: float = 0.7) -> None:
        """Fait entendre une note (choix d'une note dans la liste) sur le canal du morceau."""
        if not 0 <= note <= 127:
            raise ValueError("note hors de l'étendue MIDI")
        with self.lock:
            canal = self.bank.current.out_channel - 1
        self.send([[0x90 | canal, note, 100]])
        threading.Timer(duree, self.send, args=([[0x80 | canal, note, 0]],)).start()

    def panic(self) -> list[Message]:
        with self.lock:
            out = self.engine.all_notes_off()
        self.send(out)
        self._son_jeu(out)
        return out

    def panic_total(self) -> list[Message]:
        """Le bouton Panique : coupe tout, meme les notes tenues par la pedale,
        sur les 16 canaux (pedale relachee, puis "couper tout le son" et
        "relacher toutes les notes"), comme le bouton Panic des logiciels de musique."""
        out = self.panic()
        tout: list[Message] = []
        for canal in range(16):
            tout += [[0xB0 | canal, 64, 0], [0xB0 | canal, 120, 0], [0xB0 | canal, 123, 0]]
        self.send(tout)
        return out + tout

    def set_beam_chord(self, beam_index: int, notes: list[int]) -> None:
        """Les notes jouees en plus de la note du rayon (accord), et l'enregistre."""
        self._proteger_gamme()
        if any(not 0 <= int(n) <= 127 for n in notes):
            raise ValueError("note hors de l’étendue MIDI")
        if len(notes) > 7:
            raise ValueError("8 notes au plus par rayon")
        self.panic()
        with self.lock:
            preset = self.bank.current
            beam = next((b for b in preset.beams if b.index == beam_index), None)
            if beam is None:
                raise ValueError(f"pas de rayon {beam_index}")
            beam.extra = [int(n) for n in notes]
            self._save(preset)

    def set_beam_note(self, beam_index: int, note: int) -> None:
        """Change la note d'un rayon dans la memoire courante, et l'enregistre."""
        self._proteger_gamme()
        with self.lock:
            preset = self.bank.current
            for beam in preset.beams:
                if beam.index == beam_index:
                    if not 0 <= note <= 127:
                        raise ValueError(f"note hors de l’étendue MIDI : {note}")
                    beam.note = note
                    break
            else:
                raise KeyError(f"la memoire {preset.name!r} n'a pas de rayon {beam_index}")
            self._save(preset)

    def sort_beams(self) -> None:
        """Range les notes des rayons de la plus grave (rayon 1, a gauche) a la
        plus aigue, et l'enregistre."""
        self._proteger_gamme()
        self.panic()
        with self.lock:
            preset = self.bank.current
            notes = sorted(((b.note, list(b.extra)) for b in preset.beams), key=lambda x: x[0])
            for beam, (n, extra) in zip(sorted(preset.beams, key=lambda b: b.index), notes):
                beam.note = n
                beam.extra = extra          # l'accord suit sa note
            self._save(preset)

    def shift_all(self, step: int) -> None:
        """Decale d'un meme intervalle la note de tous les rayons du morceau, et l'enregistre."""
        self._proteger_gamme()
        with self.lock:
            preset = self.bank.current
            hors = [b.index for b in preset.beams
                    if not all(0 <= n + step <= 127 for n in [b.note] + list(b.extra))]
            if hors:
                raise ValueError(f"décalage impossible : rayon(s) {hors} hors de l'étendue MIDI")
            for beam in preset.beams:
                beam.note += step
                beam.extra = [n + step for n in beam.extra]
            self._save(preset)

    # Reglages de la harpe communs a tous les morceaux (reglages du lieu :
    # largeur, sensibilite...) : memorises une fois pour l'appareil, et
    # appliques par-dessus ceux de chaque morceau. Jamais le nombre de rayons.
    NON_COMMUNS = ("rayons",)

    def _pedale_un_rayon(self) -> bool:
        """Pedale analogique reglee sur "Sustain (un rayon a la fois)" ?"""
        try:
            v = self.harp_values().get("pedale_analogique")
        except Exception:
            v = None
        if v is None:
            v = self._harpe_attendue(self.bank.current).get("pedale_analogique")
        return v == 5

    def _communs(self) -> dict:
        return self.parametres.setdefault("harpe_communs", {})

    def _harpe_attendue(self, preset: Preset) -> dict:
        """Les reglages que la harpe doit avoir pour ce morceau."""
        attendue = dict(preset.harp_snapshot)
        attendue.update(self._communs())
        return attendue

    def _messages_harpe(self, preset: Preset) -> list[Message]:
        vue = copy.copy(preset)
        vue.harp_snapshot = self._harpe_attendue(preset)
        return harp_messages(vue)

    def _harpe_modifiee(self, preset: Preset) -> list[str]:
        """Les reglages de la harpe qui different de ceux memorises (dans le
        morceau, ou communs a tous les morceaux)."""
        attendue = self._harpe_attendue(preset)
        if not attendue:
            return []
        try:
            actuels = self.harp_values()
        except Exception:
            return []
        return sorted(k for k, v in attendue.items() if k in actuels and actuels[k] != v)

    def _ecrit_parametres(self) -> None:
        f = self._fichier_parametres
        if f is not None:
            f.write_text(json.dumps(self.parametres, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def set_harp_common(self, cle: str, commun: bool) -> None:
        """Rend un reglage commun a tous les morceaux (avec sa valeur actuelle), ou non."""
        from .harp_settings import BY_KEY
        if cle not in BY_KEY or cle in self.NON_COMMUNS:
            raise ValueError("ce réglage ne peut pas être commun à tous les morceaux")
        try:
            actuels = self.harp_values()
        except Exception:
            actuels = {}
        with self.lock:
            preset = self.bank.current
            valeur = actuels.get(cle, self._harpe_attendue(preset).get(cle))
            communs = self._communs()
            if commun:
                if valeur is None:
                    raise ValueError("valeur inconnue : lisez d'abord la harpe")
                communs[cle] = int(valeur)
            else:
                communs.pop(cle, None)
                if valeur is not None:          # le morceau garde la valeur du moment
                    preset.harp_snapshot[cle] = int(valeur)
                    self._save(preset)
            self._ecrit_parametres()

    def memorize_harp(self, tous: bool = False) -> list[str]:
        """Memorise les reglages de la harpe qui ont change : les reglages communs
        pour l'appareil, les autres dans ce morceau (ou dans tous les morceaux,
        sauf le nombre de rayons, propre a chaque morceau). Rend les reglages
        memorises."""
        with self.lock:
            preset = self.bank.current
            neuf = not preset.harp_snapshot
        if neuf and not tous:
            # premier enregistrement de ce morceau : on lit tout
            self.capture_harp()
            with self.lock:
                communs = self._communs()
                for k in list(communs):
                    if k in self.bank.current.harp_snapshot:
                        communs[k] = self.bank.current.harp_snapshot[k]
                self._ecrit_parametres()
                return sorted(self.bank.current.harp_snapshot)
        actuels = self.harp_values()
        with self.lock:
            preset = self.bank.current
            modifs = self._harpe_modifiee(preset) if not neuf else sorted(actuels)
            communs = self._communs()
            # reglages pas encore memorises dans ce morceau (instantane incomplet) :
            # on les complete, pour ce morceau seulement
            for k in sorted(actuels):
                if k not in communs and k not in preset.harp_snapshot and k not in modifs:
                    preset.harp_snapshot[k] = actuels[k]
            for k in modifs:
                v = actuels[k]
                if k in communs:
                    communs[k] = v
                elif tous and k not in self.NON_COMMUNS:
                    for p in self.bank.presets:
                        p.harp_snapshot[k] = v
                        self._save(p)
                else:
                    preset.harp_snapshot[k] = v
            if "rayons" in preset.harp_snapshot:
                preset.beam_count = None
            self._save(preset)
            self._ecrit_parametres()
            return modifs

    # ------------------------------------------------------------ parametres

    @property
    def _fichier_parametres(self) -> Path | None:
        return None if self.presets_dir is None else self.presets_dir.parent / "parametres.json"

    def _charge_parametres(self) -> None:
        f = self._fichier_parametres
        if f is None or not f.exists():
            return
        try:
            lu = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        langue = lu.get("langue", lu.get("nommage"))
        if langue in LANGUES:
            self.parametres["langue"] = langue
        if lu.get("notes") in LANGUES:
            self.parametres["notes"] = lu["notes"]
        if isinstance(lu.get("clavier"), str):
            self.parametres["clavier"] = lu["clavier"]
        if lu.get("son_clavier") in SONS_CLAVIER:
            self.parametres["son_clavier"] = lu["son_clavier"]
        if isinstance(lu.get("sortie_audio"), str):
            self.parametres["sortie_audio"] = lu["sortie_audio"]
        if isinstance(lu.get("sortie_midi"), str):
            self.parametres["sortie_midi"] = lu["sortie_midi"]
        if lu.get("son_jeu") in SONS_JEU:
            self.parametres["son_jeu"] = lu["son_jeu"]
        if isinstance(lu.get("controle_actif"), bool):
            self.parametres["controle_actif"] = lu["controle_actif"]
        if isinstance(lu.get("controle_entree"), str):
            self.parametres["controle_entree"] = lu["controle_entree"]
        if isinstance(lu.get("clavier_joue"), bool):
            self.parametres["clavier_joue"] = lu["clavier_joue"]
        if lu.get("numerotation") in NUMEROTATIONS:
            self.parametres["numerotation"] = lu["numerotation"]
        if isinstance(lu.get("harpe_communs"), dict):
            from .harp_settings import BY_KEY
            self.parametres["harpe_communs"] = {k: int(v) for k, v in lu["harpe_communs"].items()
                                                if k in BY_KEY and k not in self.NON_COMMUNS}

    def nommage_notes(self) -> str:
        """do-re-mi ("fr") ou C-D-E ("en") ; sans choix, comme la langue."""
        return self.parametres.get("notes") or self.parametres.get("langue", "fr")

    def set_parametres(self, langue: str | None = None, clavier: str | None = None,
                       son_clavier: str | None = None, sortie_audio: str | None = None,
                       sortie_midi: str | None = None, son_jeu: str | None = None,
                       controle_actif: bool | None = None, numerotation: str | None = None,
                       clavier_joue: bool | None = None, controle_entree: str | None = None,
                       notes: str | None = None) -> None:
        """Langue de l'application (textes et nom des notes) et clavier d'attribution."""
        if langue is not None and langue not in LANGUES:
            raise ValueError("langue inconnue : fr ou en")
        if notes is not None and notes not in LANGUES:
            raise ValueError("nom des notes inconnu : fr ou en")
        with self.lock:
            if langue is not None:
                self.parametres["langue"] = langue
            if notes is not None:
                self.parametres["notes"] = notes
            if clavier is not None:
                self.parametres["clavier"] = clavier.strip()
            if son_clavier is not None:
                if son_clavier not in SONS_CLAVIER:
                    raise ValueError("son du clavier inconnu")
                self.parametres["son_clavier"] = son_clavier
            if sortie_audio is not None:
                self.parametres["sortie_audio"] = sortie_audio
            if sortie_midi is not None:
                self.parametres["sortie_midi"] = sortie_midi.strip()
            if son_jeu is not None:
                if son_jeu not in SONS_JEU:
                    raise ValueError("son du mode jeu inconnu")
                self.parametres["son_jeu"] = son_jeu
            if controle_actif is not None:
                self.parametres["controle_actif"] = bool(controle_actif)
            if clavier_joue is not None:
                self.parametres["clavier_joue"] = bool(clavier_joue)
            if controle_entree is not None:
                self.parametres["controle_entree"] = controle_entree.strip()
            if numerotation is not None:
                if numerotation not in NUMEROTATIONS:
                    raise ValueError("numérotation inconnue")
                self.parametres["numerotation"] = numerotation
            f = self._fichier_parametres
            if f is not None:
                f.write_text(json.dumps(self.parametres, indent=2, ensure_ascii=False) + "\n",
                             encoding="utf-8")

    # ------------------------------------------------------------ morceaux

    def new_song(self, name: str, source: int | None = None, rayons: int = 9) -> Preset:
        """Cree un morceau et le rend actif.

        `source` = rang d'un morceau existant : copie complete (rayons, notes,
        canal, reglages de la harpe, animation), l'original reste intact.
        `source` = None : morceau vierge de `rayons` rayons, notes par demi-tons
        a partir de do4, canal 1, sans reglages de harpe memorises.
        """
        name = name.strip()
        if not name:
            raise ValueError("donnez un titre au morceau")
        with self.lock:
            if name in self.bank.names:
                raise ValueError(f"un morceau s'appelle déjà {name!r}")
            if source is None:
                if not harp.MIN_BEAMS <= rayons <= harp.MAX_BEAMS:
                    raise ValueError(f"de {harp.MIN_BEAMS} à {harp.MAX_BEAMS} rayons")
                preset = Preset(name=name, beam_count=rayons, beams=[
                    Beam(index=i, incoming_note=59 + i, note=59 + i) for i in range(1, rayons + 1)])
            else:
                if not 0 <= source < len(self.bank.presets):
                    raise ValueError("morceau d'origine introuvable")
                preset = copy.deepcopy(self.bank.presets[source])
                preset.name = name
                preset.source = None
            self.bank.presets.append(preset)
            self._save(preset)
            index = len(self.bank.presets) - 1
        self.select(index)
        return preset

    def midi_ecouter(self, pistes: list[int], debut: float, fin: float | None = None) -> None:
        if self.midi is None:
            raise ValueError("aucun fichier MIDI chargé")
        with self.lock:
            canal = self.bank.current.out_channel
        self.lecteur.jouer(self.midi, pistes, max(0.0, debut), canal, fin)

    @staticmethod
    def _notes_rayons(notes: list[int]) -> list[int]:
        notes = sorted({int(n) for n in notes})
        if not notes:
            raise ValueError("aucune note choisie")
        if len(notes) > harp.MAX_BEAMS:
            raise ValueError(f"{len(notes)} notes : la harpe a {harp.MAX_BEAMS} rayons au plus")
        if not all(0 <= n <= 127 for n in notes):
            raise ValueError("note hors de l'étendue MIDI")
        while len(notes) < harp.MIN_BEAMS:
            notes.append(notes[-1])
        return notes

    def new_song_from_parts(self, name: str, parties: list[dict]) -> Preset:
        """Cree un morceau en plusieurs parties : [{"nom": "A", "notes": [...]}, ...]."""
        if not parties:
            raise ValueError("aucune partie")
        rayons = [self._notes_rayons(x.get("notes", [])) for x in parties]
        preset = self.new_song_from_notes(name, rayons[0])
        with self.lock:
            base = preset.beams[0].incoming_note
            preset.parts = [
                {"nom": str(x.get("nom") or chr(65 + i)),
                 "rayons": [Beam(index=k, incoming_note=base + k - 1, note=n).to_dict()
                            for k, n in enumerate(r, start=1)],
                 "source": sorted({int(n) for n in x.get("notes", [])})}
                for i, (x, r) in enumerate(zip(parties, rayons))]
            preset.part_index = 0
            self._save(preset)
        return preset

    # ------------------------------------------------------ import memorise

    def _preset_nomme(self, nom: str) -> Preset:
        for preset in self.bank.presets:
            if preset.name == nom:
                return preset
        raise ValueError(f"aucun morceau nommé {nom!r}")

    def save_import(self, nom: str, info: dict) -> None:
        """Memorise avec le morceau tout ce qu'il faut pour rouvrir son import.
        Le fichier MIDI est garde dans presets/imports."""
        with self.lock:
            preset = self._preset_nomme(nom)
            info = dict(info)
            ancien = preset.import_info.get("fichier")
            if self.midi_brut is not None and self.presets_dir is not None:
                dossier = self.presets_dir / "imports"
                dossier.mkdir(parents=True, exist_ok=True)
                sur = "".join(c if c.isalnum() or c in " -_" else "_" for c in nom).strip() or "import"
                cible = dossier / (sur + ".mid")
                if not (ancien and (dossier / ancien).exists() and (dossier / ancien).read_bytes() == self.midi_brut):
                    cible.write_bytes(self.midi_brut)
                    ancien = cible.name
            info["fichier"] = ancien
            preset.import_info = info
            self._save(preset)

    def set_song_animation(self, nom: str, active: bool | None = None,
                           transition: bool | None = None, pause: float | None = None,
                           duree: float | None = None, trans_duree: float | None = None) -> None:
        """Coche ou decoche l'animation d'ouverture et la transition d'un morceau
        donne (depuis la fenetre d'import), sans toucher a ses durees."""
        with self.lock:
            preset = self._preset_nomme(nom)
            anim = {"active": False, "pause": 2.0, "duree": 3.0, "transition": False, "trans_duree": 1.0}
            anim.update(preset.animation)
            if active is not None:
                anim["active"] = bool(active)
            if transition is not None:
                anim["transition"] = bool(transition)
            for cle, v, maxi in (("pause", pause, 30), ("duree", duree, 30), ("trans_duree", trans_duree, 10)):
                if v is not None:
                    if not 0 <= float(v) <= maxi:
                        raise ValueError(f"durée hors de 0..{maxi} secondes")
                    anim[cle] = float(v)
            preset.animation = anim
            self._save(preset)

    def load_import(self, nom: str):
        """Recharge le fichier MIDI memorise d'un morceau : (fichier analyse, infos)."""
        from .midifile import analyser
        with self.lock:
            preset = self._preset_nomme(nom)
            info = dict(preset.import_info)
        if not info.get("fichier") or self.presets_dir is None:
            raise ValueError("ce morceau n'a pas d'import mémorisé")
        brut = (self.presets_dir / "imports" / info["fichier"]).read_bytes()
        self.lecteur.arreter()
        self.midi = analyser(brut)
        self.midi_brut = brut
        return self.midi, info

    @staticmethod
    def _partie_intacte(ancienne: dict, source: list[int]) -> bool:
        """Vrai si l'import n'a rien change a cette partie : on garde alors ses
        rayons tels quels, avec les retouches faites a la main (note ajoutee,
        ordre, note changee)."""
        if "source" in ancienne:
            return sorted(ancienne["source"]) == source
        # morceau cree avant cette memorisation : l'import n'apporte aucune
        # note qui ne soit deja dans la partie
        return set(source) <= {int(b.get("note", -1)) for b in ancienne.get("rayons", [])}

    def update_song(self, nom: str, parties: list[dict] | None = None,
                    notes: list[int] | None = None) -> list[str]:
        """Remplace les notes (ou les parties) d'un morceau existant, sans
        toucher a ses autres reglages. Une partie que l'import ne change pas
        garde ses rayons (et les retouches faites a la main). Rend la liste
        des parties gardees."""
        gardees: list[str] = []
        with self.lock:
            preset = self._preset_nomme(nom)
            index = self.bank.presets.index(preset)
            preset.sync_part()
            manuelles = [dict(x) for x in preset.parts if x.get("manuelle")]
            if parties and len(parties) > 1:
                preset.sync_part()
                anciennes = {str(x.get("nom")): x for x in preset.parts}
                base = min((b.incoming_note for b in preset.beams), default=60)
                nouvelles = []
                for i, x in enumerate(parties):
                    nom_p = str(x.get("nom") or chr(65 + i))
                    source = sorted({int(n) for n in x.get("notes", [])})
                    vieille = anciennes.get(nom_p)
                    if vieille is not None and vieille.get("rayons") and self._partie_intacte(vieille, source):
                        partie = dict(vieille)
                        gardees.append(nom_p)
                    else:
                        r = self._notes_rayons(source)
                        partie = {"nom": nom_p,
                                  "rayons": [Beam(index=k, incoming_note=base + k - 1, note=n).to_dict()
                                             for k, n in enumerate(r, start=1)]}
                    partie["source"] = source
                    nouvelles.append(partie)
                preset.parts = nouvelles
                preset.part_index = 0
                preset.beams = [Beam.from_dict(b) for b in nouvelles[0]["rayons"]]
                preset.beam_count = len(preset.beams)
                if "rayons" in preset.harp_snapshot:
                    preset.harp_snapshot["rayons"] = len(preset.beams) - harp.MIN_BEAMS
                self._save(preset)
                courant = index == self.bank.index
                choisies = None
            else:
                choisies = self._notes_rayons(parties[0]["notes"] if parties else (notes or []))
                preset.parts = []
                preset.part_index = 0
            if choisies is not None:
                base = min((b.incoming_note for b in preset.beams), default=60)
                preset.beams = [Beam(index=i, incoming_note=base + i - 1, note=n) for i, n in enumerate(choisies, start=1)]
                preset.beam_count = len(choisies)
                if "rayons" in preset.harp_snapshot:
                    preset.harp_snapshot["rayons"] = len(choisies) - harp.MIN_BEAMS
                self._save(preset)
                courant = index == self.bank.index
            if manuelles:                     # les parties ajoutees a la main restent, a la fin
                self._assure_parties(preset)
                preset.parts = [x for x in preset.parts if not x.get("manuelle")] + manuelles
                self._lettres_parties(preset)
                self._save(preset)
        if courant:
            self.select(index)
        return gardees

    # ------------------------------------------------- parties (a la main)

    @staticmethod
    def _lettres_parties(preset: Preset) -> None:
        """Les parties s'appellent A, B, C... dans l'ordre ou elles sont."""
        for i, x in enumerate(preset.parts):
            x["nom"] = chr(65 + i) if i < 26 else str(i + 1)

    @staticmethod
    def _assure_parties(preset: Preset) -> None:
        """Un morceau sans parties devient un morceau a une seule partie A."""
        if not preset.parts:
            preset.parts = [{"nom": "A", "rayons": [b.to_dict() for b in preset.beams],
                             "son_synthe": dict(preset.synth)}]
            preset.part_index = 0

    @staticmethod
    def _remap_import(preset: Preset, nouveau_de: dict[int, int]) -> None:
        """Apres un deplacement ou une suppression de parties : les commandes de
        partie de la piste de controle et les parties de l'import suivent
        (`nouveau_de` : ancien indice -> nouvel indice ; absent = supprimee)."""
        info = dict(preset.import_info)
        if not info:
            return
        if isinstance(info.get("commandes"), list):
            cmds = []
            for c in info["commandes"]:
                c = dict(c)
                if c.get("type") == "partie":
                    k = int(c.get("partie", 0))
                    if k not in nouveau_de:
                        continue
                    c["partie"] = nouveau_de[k]
                cmds.append(c)
            info["commandes"] = cmds
        if isinstance(info.get("parties"), list):
            gardees = sorted((nouveau_de[i], dict(x)) for i, x in enumerate(info["parties"]) if i in nouveau_de)
            info["parties"] = [x for _, x in gardees]
            for i, x in enumerate(info["parties"]):
                if "nom" in x:
                    x["nom"] = chr(65 + i) if i < 26 else str(i + 1)
        preset.import_info = info

    def part_add(self, copie: int | None = None) -> None:
        """Ajoute une partie a la fin : vierge (5 rayons qui jouent les notes de
        la harpe, sans son de synthe) ou copie d'une partie existante."""
        self._proteger_gamme()
        with self.lock:
            preset = self.bank.current
            preset.sync_part()
            self._assure_parties(preset)
            if copie is not None:
                if not 0 <= copie < len(preset.parts):
                    raise ValueError("partie introuvable")
                nouvelle = json.loads(json.dumps(preset.parts[copie]))
                nouvelle.pop("source", None)
            else:
                base = min((b.incoming_note for b in preset.beams), default=60)
                nouvelle = {"rayons": [Beam(index=i, incoming_note=base + i - 1, note=base + i - 1).to_dict()
                                       for i in range(1, harp.MIN_BEAMS + 1)],
                            "son_synthe": {}}
            nouvelle["manuelle"] = True       # gardee par « Mettre a jour le morceau »
            preset.parts.append(nouvelle)
            self._lettres_parties(preset)
            self._save(preset)
            index = len(preset.parts) - 1
        self.select_part(index)

    def part_move(self, de: int, vers: int) -> bool:
        """Deplace une partie ; les lettres suivent la position. Rend True si le
        morceau a une piste de controle (a reexporter)."""
        with self.lock:
            preset = self.bank.current
            n = len(preset.parts)
            if not (0 <= de < n and 0 <= vers < n):
                raise ValueError("partie introuvable")
            if de == vers:
                return False
            preset.sync_part()
            ordre = list(range(n))
            ordre.insert(vers, ordre.pop(de))            # ordre[nouveau] = ancien
            nouveau_de = {ancien: nouveau for nouveau, ancien in enumerate(ordre)}
            preset.parts = [preset.parts[o] for o in ordre]
            preset.part_index = nouveau_de[preset.part_index]
            self._remap_import(preset, nouveau_de)
            self._lettres_parties(preset)
            self._save(preset)
            return bool(preset.import_info.get("commandes"))

    def part_delete(self, indices: list[int]) -> bool:
        """Supprime des parties (il en reste toujours au moins une). Rend True si
        le morceau a une piste de controle (a reexporter)."""
        with self.lock:
            preset = self.bank.current
            n = len(preset.parts)
            a_suppr = {int(i) for i in indices if 0 <= int(i) < n}
            if not a_suppr:
                return False
            if len(a_suppr) >= n:
                raise ValueError("un morceau garde au moins une partie")
            preset.sync_part()
            gardees = [i for i in range(n) if i not in a_suppr]
            nouveau_de = {ancien: nouveau for nouveau, ancien in enumerate(gardees)}
            courante = preset.part_index
            preset.parts = [preset.parts[i] for i in gardees]
            self._remap_import(preset, nouveau_de)
            self._lettres_parties(preset)
            if courante in nouveau_de:
                preset.part_index = nouveau_de[courante]
            else:                                         # la partie affichee a disparu : on passe a A
                preset.part_index = 0
                preset.beams = [Beam.from_dict(b) for b in preset.parts[0]["rayons"]]
                preset.synth = preset.part_synth(0)
            self._save(preset)
            index = preset.part_index
            piste = bool(preset.import_info.get("commandes"))
        self.select_part(index)
        return piste

    def select_part(self, index: int) -> None:
        """Passe a une autre partie du morceau : ses notes, et son nombre de rayons."""
        # Sustain "un rayon a la fois", pedale enfoncee : la derniere note continue
        # pendant le changement de partie ; le premier rayon de la nouvelle partie
        # la coupera. Sinon, on coupe tout avant de changer les notes.
        # Pedale enfoncee (sustain "toutes les notes" ou "un rayon a la fois") :
        # les notes tenues continuent pendant le changement de partie.
        garder = self.engine._sustain_down
        if not garder:
            self.panic()
        with self.lock:
            preset = self.bank.current
            if not 0 <= index < len(preset.parts):
                raise ValueError("partie introuvable")
            preset.sync_part()
            preset.part_index = index
            preset.beams = [Beam.from_dict(b) for b in preset.parts[index]["rayons"]]
            # Le son du synthe de la partie (rien si sa case est decochee ou vide).
            preset.synth = preset.part_synth(index)
            from .bank import synth_messages
            son = synth_messages(preset)
            count = len(preset.beams)
            if harp.MIN_BEAMS <= count <= harp.MAX_BEAMS:
                preset.beam_count = count
                if "rayons" in preset.harp_snapshot:
                    preset.harp_snapshot["rayons"] = count - harp.MIN_BEAMS
            self._save(preset)
            if garder:
                self.engine.preset = preset       # sans couper la note tenue
            else:
                self.engine.set_preset(preset)
            message = [harp.set_beam_count(count)] if harp.MIN_BEAMS <= count <= harp.MAX_BEAMS else []
        if son:
            self.send(son)
        if message and self.harp_send is not None:
            try:
                self.harp_send(message)
            except Exception:
                pass
            else:
                self.harp_applied({"rayons": count - harp.MIN_BEAMS})
        elif message:
            self.send(message)

    def new_song_from_notes(self, name: str, notes: list[int]) -> Preset:
        """Cree un morceau dont les rayons jouent `notes` (de gauche a droite,
        de la plus grave a la plus aigue). Les autres reglages (canal, harpe,
        animation) viennent du morceau affiche. Moins de 5 notes : les rayons
        en trop rejouent la plus aigue (la harpe a au moins 5 rayons)."""
        notes = self._notes_rayons(notes)
        name = name.strip()
        if not name:
            raise ValueError("donnez un titre au morceau")
        with self.lock:
            if name in self.bank.names:
                raise ValueError(f"un morceau s'appelle déjà {name!r}")
            preset = copy.deepcopy(self.bank.current)
            preset.name = name
            preset.source = None
            preset.parts = []
            preset.part_index = 0
            base = min((b.incoming_note for b in preset.beams), default=60)
            preset.beams = [Beam(index=i, incoming_note=base + i - 1, note=n)
                            for i, n in enumerate(notes, start=1)]
            preset.beam_count = len(notes)
            if "rayons" in preset.harp_snapshot:
                preset.harp_snapshot["rayons"] = len(notes) - harp.MIN_BEAMS
            self.bank.presets.append(preset)
            self._save(preset)
            index = len(self.bank.presets) - 1
        self.select(index)
        return preset

    def delete_song(self, index: int) -> None:
        """Supprime un morceau. Son fichier n'est pas efface : il part dans le
        dossier "corbeille", a cote des morceaux, d'ou on peut le recuperer."""
        with self.lock:
            if not 0 <= index < len(self.bank.presets):
                raise ValueError("morceau introuvable")
            if len(self.bank.presets) == 1:
                raise ValueError("impossible de supprimer le dernier morceau")
            if self.bank.presets[index].gamme:
                raise ValueError("le morceau Gamme ne peut pas être supprimé")
            preset = self.bank.presets.pop(index)
            for liste in self.listes["listes"]:
                liste["morceaux"] = [n for n in liste["morceaux"] if n != preset.name]
            self._sauve_listes()
            if preset.source is not None and preset.source.exists():
                corbeille = preset.source.parent.parent / "corbeille"
                corbeille.mkdir(exist_ok=True)
                cible = corbeille / preset.source.name
                n = 1
                while cible.exists():
                    n += 1
                    cible = corbeille / f"{preset.source.stem} ({n}){preset.source.suffix}"
                preset.source.replace(cible)
                cible.touch()             # date de suppression, montree dans la corbeille
            courant = self.bank.index
            if courant > index or courant >= len(self.bank.presets):
                courant -= 1
            self.bank.index = max(0, courant)
            nouveau = self.bank.index
        self.select(nouveau)

    def delete_songs(self, noms: list[str]) -> None:
        """Supprime plusieurs morceaux d'un coup (ils partent dans la corbeille)."""
        for nom in noms:
            with self.lock:
                if nom not in self.bank.names:
                    continue
                index = self.bank.names.index(nom)
            self.delete_song(index)

    def set_beam_count(self, count: int) -> None:
        """Change le nombre de rayons du morceau courant : la harpe le recoit tout
        de suite, et la table des rayons suit (les rayons gardes conservent leur
        note ; un rayon ajoute joue d'abord sa note d'entree). La harpe envoie des
        notes qui se suivent par demi-tons a partir de celle du rayon 1."""
        if not harp.MIN_BEAMS <= count <= harp.MAX_BEAMS:
            raise ValueError(f"de {harp.MIN_BEAMS} à {harp.MAX_BEAMS} rayons")
        self.panic()
        with self.lock:
            preset = self.bank.current
            gardes = sorted(preset.beams, key=lambda b: b.index)[:count]
            base = min((b.incoming_note for b in preset.beams), default=60)
            par_index = {b.index: b for b in gardes}
            preset.beams = [
                par_index.get(i) or Beam(index=i, incoming_note=base + i - 1, note=base + i - 1)
                for i in range(1, count + 1)
            ]
            for i, b in enumerate(preset.beams, start=1):
                b.incoming_note = base + i - 1
            preset.beam_count = count
            if "rayons" in preset.harp_snapshot:
                preset.harp_snapshot["rayons"] = count - harp.MIN_BEAMS
            self._save(preset)
            self.engine.set_preset(preset)
            message = [harp.set_beam_count(count)]
        if self.harp_send is not None:
            try:
                self.harp_send(message)
            except Exception:
                pass
            else:
                self.harp_applied({"rayons": count - harp.MIN_BEAMS})
        else:
            self.send(message)

    def delete_beam(self, beam_index: int) -> None:
        """Supprime un rayon choisi du morceau (partie courante) : les rayons
        suivants se decalent d'un cran vers la gauche en gardant leur note (et
        leur accord). La harpe passe a un rayon de moins."""
        self._proteger_gamme()
        with self.lock:
            preset = self.bank.current
            rayons = sorted(preset.beams, key=lambda b: b.index)
            if len(rayons) - 1 < harp.MIN_BEAMS:
                raise ValueError(f"la harpe a au moins {harp.MIN_BEAMS} rayons")
            if not any(b.index == beam_index for b in rayons):
                raise ValueError("rayon introuvable")
        self.panic()
        with self.lock:
            preset = self.bank.current
            rayons = [b for b in sorted(preset.beams, key=lambda b: b.index) if b.index != beam_index]
            base = min((b.incoming_note for b in preset.beams), default=60)
            for i, b in enumerate(rayons, start=1):
                b.index = i
                b.incoming_note = base + i - 1
            preset.beams = rayons
            count = len(rayons)
            preset.beam_count = count
            if "rayons" in preset.harp_snapshot:
                preset.harp_snapshot["rayons"] = count - harp.MIN_BEAMS
            self._save(preset)
            self.engine.set_preset(preset)
            message = [harp.set_beam_count(count)]
        if self.harp_send is not None:
            try:
                self.harp_send(message)
            except Exception:
                pass
            else:
                self.harp_applied({"rayons": count - harp.MIN_BEAMS})
        else:
            self.send(message)

    def set_out_channel(self, canal: int) -> None:
        """Change le canal MIDI de sortie du morceau courant, et l'enregistre.
        Les notes en cours sont coupees d'abord, sur l'ancien canal."""
        if not 1 <= canal <= 16:
            raise ValueError("le canal doit être entre 1 et 16")
        self.panic()
        with self.lock:
            preset = self.bank.current
            preset.out_channel = canal
            for beam in preset.beams:
                beam.channel = None
            self._save(preset)
            from .bank import synth_messages
            son = synth_messages(preset)
        if son:
            self.send(son)

    def set_synth_sound(self, programme: int | None, msb: int | None = None,
                        lsb: int | None = None, actif: bool = True,
                        numerotation: str | None = None) -> None:
        """Le son du synthe du morceau courant (programme 1..128, banque 0..127,
        None = rien a envoyer). Il est enregistre et envoye tout de suite."""
        from .bank import synth_messages
        if programme is not None and not 1 <= programme <= 128:
            raise ValueError("le programme doit être entre 1 et 128")
        for v in (msb, lsb):
            if v is not None and not 0 <= v <= 127:
                raise ValueError("la banque doit être entre 0 et 127")
        if numerotation is not None and numerotation not in NUMEROTATIONS:
            raise ValueError("numérotation inconnue")
        with self.lock:
            preset = self.bank.current
            preset.synth = {k: v for k, v in (("programme", programme), ("msb", msb), ("lsb", lsb))
                            if v is not None}
            if not actif:
                preset.synth["actif"] = False
            # numerotation affichee pour ce morceau (celle de son synthe)
            if numerotation and numerotation != "1-128":
                preset.synth["numerotation"] = numerotation
            self._save(preset)
            out = synth_messages(preset)
        self.send(out)

    # ------------------------------------------- echange PC <-> boitier

    FORMAT_ECHANGE = "kromalaser-morceaux"

    def export_songs(self, indices: list[int] | None = None) -> dict:
        """Les morceaux choisis (tous si `indices` est None), avec le fichier
        MIDI de leur import, dans un seul paquet a ouvrir sur l'autre appareil."""
        import base64
        with self.lock:
            choisis = (list(self.bank.presets) if indices is None
                       else [self.bank.presets[i] for i in indices if 0 <= i < len(self.bank.presets)])
            morceaux = []
            for preset in choisis:
                item = {"morceau": preset.to_dict()}
                f = preset.import_info.get("fichier")
                if f and self.presets_dir is not None and (self.presets_dir / "imports" / f).exists():
                    item["import_mid"] = base64.b64encode((self.presets_dir / "imports" / f).read_bytes()).decode()
                morceaux.append(item)
        return {"format": self.FORMAT_ECHANGE, "version": 1, "morceaux": morceaux}

    def import_songs(self, paquet: dict, remplacer: bool = False) -> dict:
        """Ajoute les morceaux d'un paquet exporte. Un morceau qui porte deja ce
        nom est remplace si `remplacer` (l'ancien part dans la corbeille),
        sinon le nouveau est renomme "Titre (2)"."""
        import base64
        if not isinstance(paquet, dict) or paquet.get("format") != self.FORMAT_ECHANGE:
            raise ValueError("ce fichier n'est pas un export de morceaux Kromalaser")
        resultat = {"ajoutes": [], "remplaces": [], "renommes": []}
        with self.lock:
            for item in paquet.get("morceaux", []):
                preset = Preset.from_dict(dict(item.get("morceau", {})))
                nom = preset.name.strip() or "Sans titre"
                existant = next((i for i, x in enumerate(self.bank.presets) if x.name == nom), None)
                if existant is not None and remplacer:
                    ancien = self.bank.presets[existant]
                    if ancien.source is not None and ancien.source.exists():
                        corbeille = ancien.source.parent.parent / "corbeille"
                        corbeille.mkdir(exist_ok=True)
                        cible = corbeille / ancien.source.name
                        n = 1
                        while cible.exists():
                            n += 1
                            cible = corbeille / f"{ancien.source.stem} ({n}){ancien.source.suffix}"
                        import shutil
                        shutil.copy2(ancien.source, cible)
                    preset.source = ancien.source
                    self.bank.presets[existant] = preset
                    resultat["remplaces"].append(nom)
                else:
                    if existant is not None:
                        n = 2
                        while f"{nom} ({n})" in self.bank.names:
                            n += 1
                        resultat["renommes"].append([nom, f"{nom} ({n})"])
                        nom = f"{nom} ({n})"
                    else:
                        resultat["ajoutes"].append(nom)
                    preset.name = nom
                    preset.source = None
                    self.bank.presets.append(preset)
                if item.get("import_mid") and self.presets_dir is not None:
                    dossier = self.presets_dir / "imports"
                    dossier.mkdir(parents=True, exist_ok=True)
                    sur = "".join(c if c.isalnum() or c in " -_" else "_" for c in nom).strip() or "import"
                    (dossier / (sur + ".mid")).write_bytes(base64.b64decode(item["import_mid"]))
                    preset.import_info = dict(preset.import_info)
                    preset.import_info["fichier"] = sur + ".mid"
                self._save(preset)
            courant = self.bank.index
        self.select(courant)
        return resultat

    GAMME_ECHANGE = "__gamme__"     # le morceau Gamme dans un export de listes

    def export_lists(self, noms: list[str] | None = None) -> dict:
        """Les listes choisies (toutes si `noms` est None) et les morceaux qu'elles
        contiennent, dans un seul paquet. Le morceau Gamme n'est pas emporte :
        chaque appareil a le sien."""
        with self.lock:
            gammes = {x.name for x in self.bank.presets if x.gamme}
            listes = [{"nom": x["nom"], "morceaux": [self.GAMME_ECHANGE if n in gammes else n for n in x["morceaux"]]}
                      for x in self.listes["listes"] if noms is None or x["nom"] in noms]
            utiles = []
            for liste in listes:
                for n in liste["morceaux"]:
                    if n != self.GAMME_ECHANGE and n not in utiles:
                        utiles.append(n)
            indices = [self.bank.names.index(n) for n in utiles if n in self.bank.names]
        if not listes:
            raise ValueError("aucune liste à exporter")
        paquet = self.export_songs(indices)
        paquet["listes"] = listes
        return paquet

    def import_lists(self, paquet: dict, noms: list[str] | None = None, remplacer: bool = False) -> dict:
        """Importe les listes choisies d'un paquet (toutes si `noms` est None) avec
        leurs morceaux (memes regles que l'import de morceaux). Une liste qui
        porte deja ce nom est remplacee, les autres sont ajoutees."""
        if not isinstance(paquet, dict) or paquet.get("format") != self.FORMAT_ECHANGE:
            raise ValueError("ce fichier n'est pas un export de morceaux Kromalaser")
        if not paquet.get("listes"):
            raise ValueError("ce fichier ne contient pas de listes")
        choisies = [x for x in paquet["listes"] if noms is None or x.get("nom") in noms]
        besoin = {n for x in choisies for n in x.get("morceaux", [])}
        sous = dict(paquet, morceaux=[m for m in paquet.get("morceaux", [])
                                      if (m.get("morceau") or {}).get("nom") in besoin
                                      and not (m.get("morceau") or {}).get("gamme")])
        resultat = self.import_songs(sous, remplacer)
        renomme = {a: b for a, b in resultat.get("renommes", [])}
        resultat["listes_ajoutees"], resultat["listes_remplacees"] = [], []
        with self.lock:
            gamme = next((x.name for x in self.bank.presets if x.gamme), None)
            for liste in choisies:
                nom = str(liste.get("nom", "")).strip()
                if not nom:
                    continue
                morceaux = []
                for n in liste.get("morceaux", []):
                    n = gamme if n == self.GAMME_ECHANGE else renomme.get(n, n)
                    if n and n in self.bank.names:
                        morceaux.append(n)
                existante = next((x for x in self.listes["listes"] if x["nom"] == nom), None)
                if existante is not None:
                    existante["morceaux"] = morceaux
                    resultat["listes_remplacees"].append(nom)
                else:
                    self.listes["listes"].append({"nom": nom, "morceaux": morceaux})
                    resultat["listes_ajoutees"].append(nom)
            self._sauve_listes()
        return resultat

    def rename(self, name: str, index: int | None = None) -> None:
        """Renomme le morceau affiche, ou celui d'indice `index` (sans le charger)."""
        name = name.strip()
        if not name:
            raise ValueError("donnez un titre au morceau")
        with self.lock:
            preset = self.bank.current if index is None else self.bank.presets[index]
            if preset.gamme:
                raise ValueError("le morceau Gamme ne peut pas être renommé")
            if name in self.bank.names and name != preset.name:
                raise ValueError(f"un morceau s'appelle déjà {name!r}")
            ancien = preset.name
            preset.name = name
            self._save(preset)
            for liste in self.listes["listes"]:          # les listes suivent le nouveau nom
                liste["morceaux"] = [name if n == ancien else n for n in liste["morceaux"]]
            self._sauve_listes()

    # ------------------------------------------------------- morceau Gamme

    def _assure_gamme(self) -> None:
        """Cree le morceau "Gamme" s'il manque : 9 rayons, chacun jouant la note
        envoyee par la harpe (gamme chromatique a partir de do4). A la fin de la
        liste, pour ne decaler aucun numero."""
        if any(p.gamme for p in self.bank.presets):
            return
        nom = "Gamme"
        n = 2
        while nom in self.bank.names:
            nom = f"Gamme ({n})"
            n += 1
        preset = Preset(name=nom, beam_count=9, gamme=True,
                        beams=[Beam(index=i, incoming_note=59 + i, note=59 + i) for i in range(1, 10)])
        self.bank.presets.append(preset)
        self._save(preset)

    def _proteger_gamme(self, preset: Preset | None = None) -> None:
        if (preset or self.bank.current).gamme:
            raise ValueError("le morceau Gamme joue les notes de la harpe telles quelles : ses notes ne se modifient pas")

    # ---------------------------------------------------------- corbeille

    @property
    def _corbeille(self) -> Path | None:
        return None if self.presets_dir is None else self.presets_dir.parent / "corbeille"

    def _fichier_corbeille(self, fichier: str) -> Path:
        c = self._corbeille
        chemin = (c / Path(fichier).name) if c is not None else None
        if chemin is None or not chemin.exists() or chemin.suffix != ".json":
            raise ValueError("morceau introuvable dans la corbeille")
        return chemin

    def trash_list(self) -> list[dict]:
        """Les morceaux supprimes, du plus recent au plus ancien."""
        import datetime
        c = self._corbeille
        if c is None or not c.exists():
            return []
        out = []
        for f in sorted(c.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
            try:
                nom = str(json.loads(f.read_text(encoding="utf-8")).get("nom", f.stem))
            except (OSError, ValueError):
                nom = f.stem
            out.append({"fichier": f.name, "nom": nom,
                        "date": datetime.datetime.fromtimestamp(f.stat().st_mtime).strftime("%d/%m/%Y %H:%M")})
        return out

    def trash_restore(self, fichier: str) -> str:
        """Remet un morceau supprime a la fin de la liste par defaut (aucun numero
        ne bouge). Renomme "Titre (2)" si le nom est deja pris. Rend son nom."""
        with self.lock:
            chemin = self._fichier_corbeille(fichier)
            preset = Preset.load(chemin)
            nom = preset.name
            if nom in self.bank.names:
                n = 2
                while f"{nom} ({n})" in self.bank.names:
                    n += 1
                nom = f"{nom} ({n})"
            preset.name = nom
            preset.source = None
            self.bank.presets.append(preset)
            self._save(preset)
            chemin.unlink()
            return nom

    def _supprime_definitivement(self, chemin: Path) -> None:
        try:
            info = json.loads(chemin.read_text(encoding="utf-8")).get("import", {})
        except (OSError, ValueError):
            info = {}
        chemin.unlink()
        # son fichier MIDI d'import, s'il ne sert plus a aucun morceau
        mid = info.get("fichier")
        if mid and self.presets_dir is not None:
            utilise = any(p.import_info.get("fichier") == mid for p in self.bank.presets)
            c = self._corbeille
            if c is not None and c.exists():
                for f in c.glob("*.json"):
                    try:
                        utilise = utilise or json.loads(f.read_text(encoding="utf-8")).get("import", {}).get("fichier") == mid
                    except (OSError, ValueError):
                        pass
            cible = self.presets_dir / "imports" / mid
            if not utilise and cible.exists():
                cible.unlink()

    def trash_delete(self, fichier: str) -> None:
        with self.lock:
            self._supprime_definitivement(self._fichier_corbeille(fichier))

    def trash_empty(self) -> int:
        with self.lock:
            c = self._corbeille
            fichiers = list(c.glob("*.json")) if c is not None and c.exists() else []
            for f in fichiers:
                self._supprime_definitivement(f)
            return len(fichiers)

    # ------------------------------------------------------------ listes

    @property
    def _fichier_listes(self) -> Path | None:
        return None if self.presets_dir is None else self.presets_dir.parent / "listes.json"

    def _charge_listes(self) -> None:
        f = self._fichier_listes
        if f is None or not f.exists():
            return
        try:
            lu = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        self.listes["listes"] = [{"nom": str(x.get("nom", "")), "morceaux": [str(n) for n in x.get("morceaux", [])]}
                                 for x in lu.get("listes", []) if x.get("nom")]
        noms = [x["nom"] for x in self.listes["listes"]]
        self.listes["active"] = lu.get("active") if lu.get("active") in noms else None
        ordre = [str(n) for n in lu.get("ordre", [])]
        if ordre:
            self._range_banque(ordre)

    def _sauve_listes(self) -> None:
        f = self._fichier_listes
        if f is None:
            return
        donnees = {"ordre": self.bank.names, "listes": self.listes["listes"], "active": self.listes.get("active")}
        f.write_text(json.dumps(donnees, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def _range_banque(self, ordre: list[str]) -> None:
        """Remet les morceaux dans l'ordre donne (ceux qui n'y sont pas : a la fin)."""
        courant = self.bank.current
        rang = {n: i for i, n in enumerate(ordre)}
        self.bank.presets.sort(key=lambda p: rang.get(p.name, len(ordre)))
        self.bank.index = self.bank.presets.index(courant)

    def _indices_liste(self, nom: str | None) -> list[int]:
        if not nom:
            return list(range(len(self.bank.presets)))
        liste = next((x for x in self.listes["listes"] if x["nom"] == nom), None)
        noms = self.bank.names
        return [noms.index(n) for n in (liste["morceaux"] if liste else []) if n in noms]

    def _numeros_changes(self, avant: list[str]) -> list[dict]:
        """Les morceaux qui ont change de numero, et s'ils ont une piste de controle."""
        apres = self.bank.names
        out = []
        for i, n in enumerate(avant):
            if n in apres and apres.index(n) != i:
                p = self.bank.presets[apres.index(n)]
                out.append({"nom": n, "avant": i + 1, "apres": apres.index(n) + 1,
                            "piste": bool(p.import_info.get("commandes"))})
        return out

    def set_default_order(self, noms: list[str]) -> list[dict]:
        """Nouvel ordre de la liste par defaut. Rend les morceaux qui changent de numero."""
        with self.lock:
            if sorted(noms) != sorted(self.bank.names):
                raise ValueError("la liste doit contenir tous les morceaux, une seule fois")
            avant = self.bank.names
            self._range_banque(list(noms))
            self._sauve_listes()
            return self._numeros_changes(avant)

    def list_create(self, nom: str) -> None:
        nom = nom.strip()
        with self.lock:
            if not nom:
                raise ValueError("donnez un nom à la liste")
            if any(x["nom"] == nom for x in self.listes["listes"]):
                raise ValueError(f"une liste s'appelle déjà {nom!r}")
            self.listes["listes"].append({"nom": nom, "morceaux": []})
            self._sauve_listes()

    def _liste(self, nom: str) -> dict:
        liste = next((x for x in self.listes["listes"] if x["nom"] == nom), None)
        if liste is None:
            raise ValueError(f"aucune liste nommée {nom!r}")
        return liste

    def list_rename(self, ancien: str, nouveau: str) -> None:
        nouveau = nouveau.strip()
        with self.lock:
            liste = self._liste(ancien)
            if not nouveau:
                raise ValueError("donnez un nom à la liste")
            if nouveau != ancien and any(x["nom"] == nouveau for x in self.listes["listes"]):
                raise ValueError(f"une liste s'appelle déjà {nouveau!r}")
            liste["nom"] = nouveau
            if self.listes.get("active") == ancien:
                self.listes["active"] = nouveau
            self._sauve_listes()

    def list_delete(self, nom: str) -> None:
        with self.lock:
            self.listes["listes"].remove(self._liste(nom))
            if self.listes.get("active") == nom:
                self.listes["active"] = None
            self._sauve_listes()

    def list_set_songs(self, nom: str, morceaux: list[str]) -> None:
        with self.lock:
            liste = self._liste(nom)
            inconnus = [n for n in morceaux if n not in self.bank.names]
            if inconnus:
                raise ValueError("morceau introuvable : " + ", ".join(inconnus))
            liste["morceaux"] = list(morceaux)
            self._sauve_listes()

    def list_activate(self, nom: str | None) -> None:
        with self.lock:
            if nom:
                self._liste(nom)
            self.listes["active"] = nom or None
            self._sauve_listes()

    def control_tracks(self) -> list[tuple[str, bytes]]:
        """Toutes les pistes de controle des morceaux qui ont un import et des
        commandes memorises, avec leur numero actuel."""
        from .controle import piste_commandes
        from .midifile import analyser
        out = []
        with self.lock:
            morceaux = [(i, p.name, dict(p.import_info)) for i, p in enumerate(self.bank.presets)]
        for i, nom, info in morceaux:
            if not info.get("commandes") or not info.get("fichier") or self.presets_dir is None:
                continue
            chemin = self.presets_dir / "imports" / info["fichier"]
            if not chemin.exists():
                continue
            fichier = analyser(chemin.read_bytes())
            out.append((nom, piste_commandes(fichier, list(info["commandes"]), i, nom,
                                             int(info.get("debut", 1)) - 1)))
        return out

    def _save(self, preset: Preset) -> None:
        if self.presets_dir is None:
            return
        if preset.source is None:
            self.presets_dir.mkdir(parents=True, exist_ok=True)
            safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in preset.name).strip()
            preset.source = self.presets_dir / f"{safe or 'memoire'}.json"
        preset.save(preset.source)
        if hasattr(self, "listes"):
            self._sauve_listes()      # l'ordre des morceaux (nouveau morceau : a la fin)

    # ----------------------------------------------------------- apprentissage

    def start_learning(self) -> None:
        with self.lock:
            self.learner = Learner()

    def cancel_learning(self) -> None:
        with self.lock:
            self.learner = None

    def undo_learning(self) -> int | None:
        with self.lock:
            return self.learner.undo() if self.learner else None

    def finish_learning(self, name: str | None = None) -> Preset:
        """Transforme ce qui a ete appris en memoire, et la rend active."""
        with self.lock:
            if self.learner is None:
                raise RuntimeError("aucun apprentissage en cours")
            if self.learner.count == 0:
                raise RuntimeError("aucun rayon appris : coupez les rayons un par un")
            current = self.bank.current
            # On ne commande a la harpe un nombre de rayons que s'il est dans ce
            # qu'elle accepte : un apprentissage partiel ne doit pas la reduire.
            appris = self.learner.count
            commandable = harp.MIN_BEAMS <= appris <= harp.MAX_BEAMS
            preset = self.learner.to_preset(
                name=name or f"{current.name} (appris)",
                out_channel=current.out_channel,
                beam_count=appris if commandable else None,
            )
            self.learner = None
            self.bank.presets.append(preset)
            self._save(preset)
            out = self.bank.select(len(self.bank.presets) - 1)
        self.send(out)
        return preset
