"""Le programme du boitier.

Trois usages :

    python3 -m kromabridge ports
        Liste les ports MIDI vus par la machine. C'est la premiere chose a
        lancer pour savoir sous quel nom apparaissent la harpe et l'interface.

    python3 -m kromabridge moniteur
        Affiche ce qui arrive, sans rien traduire. Sert a decouvrir ce que la
        harpe envoie, et a verifier qu'elle est bien entendue.

    python3 -m kromabridge jouer
        Le mode normal : traduit les notes et sert la page d'edition.
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
import time
from pathlib import Path

from . import network
from .bank import Bank
from .harp_service import HarpService
from .midi_io import HARP_PORT_HINT, HarpControl, HarpListener, MidiError, MidiLink, list_ports
from .preset import PresetError, chromatic_preset
from .runtime import Runtime
from .web import note_name, serve

def _dossier_documents() -> Path:
    """Le dossier Documents de l'utilisateur Windows (meme s'il a ete deplace)."""
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(260)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0 and buf.value:
            return Path(buf.value)
    except Exception:
        pass
    return Path.home() / "Documents"


def _presets_par_defaut() -> Path:
    """Version installee sur PC (fichier "installe" a cote du programme) : les
    donnees vont dans Documents\\Kromabridge, hors du dossier du programme, pour
    qu'une mise a jour ne les touche jamais. Sinon, a cote du programme."""
    racine = Path(__file__).resolve().parent.parent
    if (racine / "installe").exists() and sys.platform == "win32":
        return _dossier_documents() / "Kromabridge" / "presets"
    return racine / "presets"


DEFAULT_PRESETS = _presets_par_defaut()


def _describe(message: list[int]) -> str:
    """Une ligne lisible pour le mode moniteur."""
    if not message:
        return ""
    status = message[0]
    kind = status & 0xF0
    channel = (status & 0x0F) + 1
    if kind == 0x90 and len(message) > 2 and message[2] > 0:
        return f"canal {channel}  note ON   {message[1]:3d} ({note_name(message[1])})  force {message[2]}"
    if kind == 0x80 or (kind == 0x90 and len(message) > 2):
        return f"canal {channel}  note OFF  {message[1]:3d} ({note_name(message[1])})"
    if kind == 0xB0:
        return f"canal {channel}  reglage   {message[1]:3d} = {message[2]}"
    if status == 0xF0:
        return "sysex     " + " ".join(f"{b:02X}" for b in message)
    return " ".join(f"{b:02X}" for b in message)


def _load_bank(directory: Path, beams: int) -> Bank:
    try:
        return Bank.from_directory(directory)
    except PresetError as exc:
        print(f"{exc}. Une memoire neutre de {beams} rayons est utilisee.", file=sys.stderr)
        return Bank([chromatic_preset(beams)])


def commande_ports(_args) -> int:
    ports = list_ports()
    for sens in ("entrees", "sorties"):
        print(f"{sens.capitalize()} :")
        for nom in ports[sens] or ["(aucun)"]:
            print(f"  - {nom}")
    return 0


def commande_moniteur(args) -> int:
    arret = threading.Event()
    with MidiLink(args.entree, args.sortie or args.entree) as lien:
        lien.on_message(lambda message: print(_describe(message), flush=True))
        print("Ecoute en cours. Coupe les rayons, Ctrl-C pour arreter.", flush=True)
        signal.signal(signal.SIGINT, lambda *_: arret.set())
        arret.wait()
    return 0


MIDI_RETRY_SECONDS = 3.0

# Ports choisis sans toucher au service systeme : fichier modifiable sans sudo.
#   {"entree": "kromalaser", "sortie": "ch345"}
CONFIG_FILE = Path(__file__).resolve().parent.parent / "boitier.json"


def _ports(args) -> tuple[str, str | None]:
    """Entree et sortie : boitier.json d'abord, sinon la ligne de commande, sinon la harpe.

    Le fichier passe avant la ligne de commande : le service systeme (protege par
    mot de passe) garde ainsi ses anciennes options sans qu'il faille le modifier.
    """
    config = {}
    if CONFIG_FILE.exists():
        try:
            config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"boitier.json illisible, ignore : {exc}", flush=True)
    entree = config.get("entree") or args.entree or HARP_PORT_HINT
    sortie = config.get("sortie") or args.sortie
    return entree, sortie


def commande_jouer(args) -> int:
    entree, sortie = _ports(args)
    joue_la_harpe = HARP_PORT_HINT in entree.lower()
    print(f"Entree : {entree}. Sortie : {sortie or 'premier port'}.", flush=True)
    Path(args.memoires).mkdir(parents=True, exist_ok=True)
    bank = _load_bank(Path(args.memoires), args.rayons)
    arret = threading.Event()
    lien: MidiLink | None = None

    # Tant que la carte MIDI ou la harpe manquent, les messages sont perdus sans
    # erreur : la page d'edition doit rester accessible, avec ou sans MIDI.
    ecoute = None
    # Vrai seulement pendant le traitement d'une lecture de la harpe : le delai
    # n'est mesure que pour les notes jouees sur la harpe (pas pour la pre-ecoute,
    # le clavier ou les commandes de Cubase, qui n'ont rien a voir avec une lecture).
    lecture = threading.local()

    def depuis_harpe(data) -> None:
        lecture.active = True
        try:
            reglages.feed(data)
        finally:
            lecture.active = False

    def envoyer(messages) -> None:
        if lien is not None:
            try:
                lien.send_all(messages)
            except Exception:  # port disparu entre deux verifications
                pass
            else:
                if ecoute is not None and ecoute.last_read_ns and getattr(lecture, "active", False):
                    reglages.latences.append((time.perf_counter_ns() - ecoute.last_read_ns) // 1000)
                reglages.monitor.sent(messages)

    # Version PC (Windows) : la harpe passe par les ports MIDI du systeme.
    version_pc = sys.platform == "win32"
    if version_pc:
        from .harp_rtmidi import ProcHarpControl, RtHarpListener
        # Envois vers la harpe dans un petit programme separe et surveille :
        # une harpe qui ne repond plus ne peut plus figer tout le programme.
        harpe = ProcHarpControl()
    else:
        harpe = HarpControl()
    reglages = HarpService(harpe)
    def vers_moniteur(message) -> None:
        reglages.monitor.to_harp(message)
        if runtime_ref:
            runtime_ref[0].journal("harpe <- " + " ".join(f"{b:02X}" for b in message))

    runtime_ref: list = []
    harpe.on_sent = vers_moniteur
    if hasattr(harpe, "on_erreur"):
        harpe.on_erreur = lambda texte: runtime_ref and runtime_ref[0].journal("ERREUR " + texte)

    def vers_harpe(messages) -> None:
        for message in messages:
            harpe.send(message)

    def rappel_harpe(numero: int) -> None:
        # La lecture des reglages qui suit prend un instant : pas sur le fil de la page.
        threading.Thread(target=reglages.recall, args=(numero,), daemon=True).start()

    runtime = Runtime(bank, send=envoyer, presets_dir=Path(args.memoires),
                      harp_send=vers_harpe, harp_recall=rappel_harpe,
                      harp_capture=lambda: reglages.read()["valeurs"], gamme=True)
    # Sur PC, la page n'est servie qu'a cet ordinateur (pas d'alerte du pare-feu).
    serveur = serve(runtime, host="127.0.0.1" if version_pc else "0.0.0.0", port=args.port,
                    harpe=harpe, service=reglages, pc=version_pc)
    # La harpe est toujours lue en continu par HarpListener : sans cette lecture
    # elle ne detecte pas la main. Quand c'est elle qu'on joue, ses notes passent
    # de la au moteur, puis vers la sortie (le synthe).
    if joue_la_harpe:
        reglages.monitor.on_message = runtime.handle
    if version_pc:
        ecoute = RtHarpListener(harpe, on_data=depuis_harpe)
    else:
        ecoute = HarpListener(harpe, on_data=depuis_harpe)
    ecoute.start()
    # Clavier d'attribution : sur le boitier, par defaut l'entree du cable MIDI
    # (le D-50) ; sur PC, celui choisi dans Parametres.
    from .keyboard import KeyboardListener
    if not runtime.parametres.get("clavier") and not version_pc:
        runtime.parametres["clavier"] = sortie or ""
    # Le clavier n'est ouvert que pendant le mode "Attribuer au clavier" : sous
    # Windows, un port ouvert ici serait refuse au logiciel de musique.
    def entree_controle() -> str:
        """L'entree ecoutee par la piste de controle ("" = rien)."""
        if not runtime.parametres.get("controle_actif"):
            return ""
        from .controle import PORT_PC
        return runtime.parametres.get("controle_entree") or (PORT_PC if version_pc else (sortie or ""))

    def clavier_partage() -> bool:
        """Sur PC, un port MIDI ne s'ouvre qu'une fois : si le clavier est aussi
        l'entree de la piste de controle, c'est l'ecoute du controle qui l'ouvre
        et qui fait suivre les notes au clavier."""
        nom = (runtime.parametres.get("clavier") or "").strip().lower()
        return version_pc and bool(nom) and entree_controle().strip().lower() == nom

    clavier = KeyboardListener(
        lambda: runtime.parametres.get("clavier", "") if runtime.keyboard_active() and not clavier_partage() else "",
        runtime.keyboard_note, retry=0.5, on_message=runtime.keyboard_echo)
    # Son du clavier par defaut : le synthe integre de Windows sur PC (un clavier
    # maitre n'a pas de son) ; rien sur le boitier (le D-50 joue deja ses notes).
    if not runtime.parametres.get("son_clavier"):
        runtime.parametres["son_clavier"] = "windows" if version_pc else "aucun"
    if version_pc:
        from .keyboard import WindowsSynth
        runtime.send_windows = WindowsSynth().send
        try:
            import numpy  # noqa: F401
            import sounddevice  # noqa: F401
            from .sampler import LecteurAudio
            runtime.audio_pc = LecteurAudio(lambda: runtime.parametres.get("sortie_audio", ""))
        except Exception as exc:     # pas de carte son utilisable : le reste marche
            print(f"Son de la harpe sur le PC indisponible : {exc}", flush=True)
    runtime.keyboard_ports = clavier.ports
    runtime.harp_values = lambda: dict(reglages.valeurs)
    runtime.harp_applied = lambda valeurs: reglages.valeurs.update(valeurs)
    runtime.keyboard_connected = lambda: controle.connected if clavier_partage() else clavier.connected
    clavier.start()
    # Piste de controle : sur PC le port loopMIDI "Controle Harpe" ; sur le
    # boitier, l'entree du cable MIDI (canal 16 seulement).
    from .controle import CANAL, ControlListener
    from .web import commander_harpe
    runtime_ref.append(runtime)
    runtime.journal("demarrage du programme")
    # La piste de controle attend la fin de chaque animation avant la commande suivante.
    runtime.harp_open = lambda: commander_harpe(runtime, harpe, reglages, True, attendre=True)
    runtime.harp_close = lambda: commander_harpe(runtime, harpe, reglages, False, attendre=True)
    from .web import changer_partie
    runtime.part_switcher = lambda i, attendre=False: changer_partie(runtime, harpe, reglages, i, attendre=attendre)
    def message_controle(message: list[int]) -> None:
        if message and message[0] & 0x0F == CANAL - 1:
            runtime.control_message(message)
        elif clavier_partage() and runtime.keyboard_active():
            clavier._callback((message, 0.0))

    controle = ControlListener(entree_controle, message_controle)
    runtime.control_connected = lambda: controle.connected
    controle.start()
    threading.Thread(target=serveur.serve_forever, daemon=True).start()
    print(("Programme en marche" if version_pc else "Boitier en marche") + f". Page d'edition sur le port {args.port}.", flush=True)
    print(f"Memoire active : {bank.current.name}", flush=True)
    if version_pc:
        print(f"Version PC : ouvrez http://localhost:{args.port} dans votre navigateur.", flush=True)
    else:
        network.direct_at_startup()

    signal.signal(signal.SIGINT, lambda *_: arret.set())
    signal.signal(signal.SIGTERM, lambda *_: arret.set())

    # Sortie MIDI choisie dans Parametres (sinon celle d'origine), et la liste
    # des sorties proposees (un seul objet rtmidi pour lister, garde ouvert).
    import re as _re
    runtime.midi_output_default = sortie or ""
    _lister = {"out": None}

    def sorties_midi() -> list[str]:
        if _lister["out"] is None:
            from .midi_io import _require_rtmidi
            _lister["out"] = _require_rtmidi().MidiOut()
        exclus = ("kromalaser", "rtmidi", "midi through", "controle harpe")
        noms = [_re.sub(r" \d+(:\d+)?$", "", n) for n in _lister["out"].get_ports()
                if not any(x in n.lower() for x in exclus)]
        return list(dict.fromkeys(noms))

    runtime.midi_outputs = sorties_midi
    lien_sortie = None
    attente_signalee = False
    sans_sortie_annonce = False
    while not arret.is_set():
        voulue = runtime.parametres.get("sortie_midi") or sortie
        if voulue == SANS_SORTIE_MIDI:
            # Sortie MIDI "Aucune" : les notes de la harpe ne partent vers aucun
            # synthe (seul le son de la harpe du mode jeu les joue).
            if lien is not None:
                runtime.panic()
                ancien, lien = lien, None
                ancien.close()
            lien_sortie = voulue
            if not sans_sortie_annonce:
                print("Sortie MIDI : aucune.", flush=True)
                runtime.reload_harp()
                sans_sortie_annonce = True
            arret.wait(1.0)
            continue
        sans_sortie_annonce = False
        if lien is not None and voulue != lien_sortie:
            print(f"Sortie MIDI changee : {voulue}", flush=True)
            runtime.panic()
            ancien, lien = lien, None
            ancien.close()
        if lien is None:
            candidat = MidiLink(entree, voulue, use_input=not joue_la_harpe)
            lien_sortie = voulue
            try:
                candidat.__enter__()
            except MidiError as exc:
                if not attente_signalee:
                    print(f"En attente du MIDI : {exc}", flush=True)
                    attente_signalee = True
            else:
                lien = candidat
                if not joue_la_harpe:
                    lien.on_message(runtime.handle)
                runtime.reload_harp()
                attente_signalee = False
                print("MIDI connecte.", flush=True)
        elif not lien.still_present():
            print("MIDI debranche, en attente.", flush=True)
            ancien, lien = lien, None
            ancien.close()
        arret.wait(1.0 if lien is not None else MIDI_RETRY_SECONDS)

    # On ne laisse jamais une note tenue derriere soi en s'arretant.
    runtime.panic()
    serveur.shutdown()
    if ecoute is not None:
        ecoute.stop()
    harpe.close()
    if lien is not None:
        lien.close()
    return 0


SANS_SORTIE_MIDI = "__aucune__"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kromabridge", description=__doc__)
    sous = parser.add_subparsers(dest="commande", required=True)

    sous.add_parser("ports", help="liste les ports MIDI").set_defaults(func=commande_ports)

    moniteur = sous.add_parser("moniteur", help="affiche ce qui arrive")
    moniteur.add_argument("--entree", default=HARP_PORT_HINT, help="morceau du nom du port d'entree")
    moniteur.add_argument("--sortie", default=None, help="morceau du nom du port de sortie")
    moniteur.set_defaults(func=commande_moniteur)

    jouer = sous.add_parser("jouer", help="traduit les notes et sert la page")
    jouer.add_argument("--entree", default=None,
                       help="morceau du nom du port d'entree (defaut : boitier.json, sinon la harpe)")
    jouer.add_argument("--sortie", default=None,
                       help="morceau du nom du port de sortie (defaut : boitier.json)")
    jouer.add_argument("--memoires", default=str(DEFAULT_PRESETS), help="dossier des memoires")
    jouer.add_argument("--port", type=int, default=8080, help="port de la page d'edition")
    jouer.add_argument("--rayons", type=int, default=12, help="rayons de la memoire de secours")
    jouer.set_defaults(func=commande_jouer)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except MidiError as exc:
        print(f"Erreur MIDI : {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
