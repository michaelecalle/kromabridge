"""La page d'edition, servie par le boitier lui-meme.

Aucune bibliotheque exterieure : le Raspberry sert la page avec ce que Python
apporte deja, ce qui evite d'avoir a installer quoi que ce soit sur scene.

La page s'adapte a l'ecran : sur un telephone les rayons se suivent, sur une
tablette ils s'affichent cote a cote, comme la harpe elle-meme.
"""

from __future__ import annotations

import json
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import network
from .runtime import Runtime

# Autorise par une regle sudoers dediee (voir doc/installation.md), sans mot de passe.
POWEROFF_COMMAND = ["sudo", "-n", "/usr/bin/systemctl", "poweroff"]


def power_off(delay: float = 1.0) -> None:
    """Eteint le Raspberry un instant apres, le temps que la page recoive sa reponse."""
    threading.Timer(delay, subprocess.run, args=(POWEROFF_COMMAND,)).start()


NOTE_NAMES = ["do", "do#", "ré", "ré#", "mi", "fa", "fa#", "sol", "sol#", "la", "la#", "si"]
NOTE_NAMES_EN = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def note_name(note: int, nommage: str = "fr") -> str:
    """Le nom d'une note MIDI, avec son octave (60 = do3 = C3)."""
    noms = NOTE_NAMES_EN if nommage == "en" else NOTE_NAMES
    return f"{noms[note % 12]}{note // 12 - 1}"


class _Interrompue(Exception):
    pass


# L'animation en cours : une nouvelle commande l'interrompt (par exemple une
# fermeture envoyee par la piste de controle avant la fin de l'ouverture).
_ANIM = {"gen": 0, "en_cours": None, "lock": threading.Lock()}


def commander_harpe(runtime: Runtime, harpe, service, ouvrir: bool, attendre: bool = False) -> None:
    """Ouvre ou ferme la harpe, avec l'animation du morceau s'il en a une."""
    if harpe is None:
        raise RuntimeError("commande de la harpe indisponible")
    import time as _time
    with _ANIM["lock"]:
        _ANIM["gen"] += 1
        gen = _ANIM["gen"]
        interrompue = _ANIM["en_cours"]
    plan = runtime.opening_plan(service.valeurs.get("largeur") if service else None)
    if plan is None or (not ouvrir and interrompue == "ouverture"):
        # Fermeture pendant une ouverture animee : on ferme tout de suite, puis
        # on remet la largeur du morceau (l'ouverture l'avait reduite).
        (harpe.open_harp if ouvrir else harpe.close_harp)()
        if plan is not None:
            from .harp_settings import BY_KEY
            harpe.send(BY_KEY["largeur"].message(plan["largeur"]))
        return
    from .harp_service import close_animated, open_animated

    def dormir(d: float) -> None:
        fin = _time.monotonic() + d
        while True:
            if _ANIM["gen"] != gen:
                raise _Interrompue()
            reste = fin - _time.monotonic()
            if reste <= 0:
                return
            _time.sleep(min(reste, 0.05))

    def lancer() -> None:
        with _ANIM["lock"]:
            _ANIM["en_cours"] = "ouverture" if ouvrir else "fermeture"
        try:
            if ouvrir:
                # Depart a 0 % (HarpService.largeur_zero) essaye le 18/11 puis abandonne :
                # chaque envoi du message cache est enregistre dans la memoire flash
                # de la harpe (usure). On part du minimum MIDI (10 %).
                open_animated(harpe, plan["largeur"], plan["pause"], plan["duree"], sleep=dormir)
            else:
                close_animated(harpe, plan["largeur"], plan["pause"], plan["duree"], sleep=dormir)
        except _Interrompue:
            pass
        finally:
            with _ANIM["lock"]:
                if _ANIM["gen"] == gen:
                    _ANIM["en_cours"] = None

    if attendre:
        lancer()
    else:
        threading.Thread(target=lancer, daemon=True).start()


def changer_partie(runtime: Runtime, harpe, service, index: int, pas_s: float = 0.06,
                   attendre: bool = False) -> None:
    """Change de partie ; si le morceau le demande, en resserrant les rayons
    (largeur au minimum), puis en les reecartant, sans fermer la harpe."""
    plan = runtime.transition_plan(service.valeurs.get("largeur") if service else None)
    if plan is None or harpe is None or plan["duree"] <= 0:
        runtime.select_part(index)
        return
    import time as _time
    from .harp_settings import BY_KEY
    reglage = BY_KEY["largeur"]
    with _ANIM["lock"]:
        _ANIM["gen"] += 1
        gen = _ANIM["gen"]

    def dormir(d: float) -> None:
        fin = _time.monotonic() + d
        while True:
            if _ANIM["gen"] != gen:
                raise _Interrompue()
            reste = fin - _time.monotonic()
            if reste <= 0:
                return
            _time.sleep(min(reste, 0.05))

    def rampe(de: int, a: int) -> None:
        pas = max(1, int(plan["duree"] / pas_s))
        prec = None
        for i in range(1, pas + 1):
            v = round(de + (a - de) * i / pas)
            if v != prec and i < pas:
                harpe.send_quick(reglage.message(v))
            prec = v
            dormir(pas_s)
        harpe.send(reglage.message(a))

    def lancer() -> None:
        with _ANIM["lock"]:
            _ANIM["en_cours"] = "transition"
        try:
            rampe(plan["largeur"], 0)
            runtime.select_part(index)
            rampe(0, plan["largeur"])
        except _Interrompue:
            pass
        finally:
            with _ANIM["lock"]:
                if _ANIM["gen"] == gen:
                    _ANIM["en_cours"] = None

    if attendre:
        lancer()
    else:
        threading.Thread(target=lancer, daemon=True).start()


def _analyse(fichier) -> dict:
    """Ce que la fenetre d'import affiche d'un fichier MIDI."""
    pistes = []
    for p in fichier.pistes:
        if not p.notes:
            continue
        r = p.resume()
        r["apercu"] = fichier.apercu(p)
        r["premiere"] = r["apercu"][0][0] if r["apercu"] else 0
        pistes.append(r)
    return {"pistes": pistes, "duree": fichier.duree(), "mesures": fichier.mesures(),
            "temps_mesures": fichier.temps_mesures()}


def make_handler(runtime: Runtime, harpe=None, service=None, pc: bool = False):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        # On garde les journaux silencieux : le boitier tourne sans ecran.
        def log_message(self, *args) -> None:  # noqa: D102
            pass

        # ------------------------------------------------------------ reponses

        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data, code: int = 200) -> None:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self._send(code, body, "application/json; charset=utf-8")

        def _flux(self) -> None:
            """Evenements en direct (notes a faire entendre) pour la page."""
            import queue
            q = runtime.abonner()
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(b": ok\n\n")
                self.wfile.flush()
                while True:
                    try:
                        ev = q.get(timeout=15)
                    except queue.Empty:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                        continue
                    self.wfile.write(("data: " + json.dumps(ev) + "\n\n").encode("utf-8"))
                    self.wfile.flush()
            except Exception:
                pass
            finally:
                runtime.desabonner(q)
                self.close_connection = True

        def _read_json(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if not length:
                return {}
            return json.loads(self.rfile.read(length).decode("utf-8"))

        # ------------------------------------------------------------- lecture

        def do_GET(self) -> None:  # noqa: N802
            route = urlparse(self.path).path
            if route == "/api/flux":
                self._flux()
                return
            if route.startswith("/sons/harpe/"):
                from .sampler import DOSSIER
                nom = route.rsplit("/", 1)[-1]
                fichier = DOSSIER / nom
                if "/" in nom or "\\" in nom or not nom.endswith(".wav") or not fichier.exists():
                    self._json({"erreur": "son inconnu"}, 404)
                    return
                donnees = fichier.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "audio/wav")
                self.send_header("Content-Length", str(len(donnees)))
                self.send_header("Cache-Control", "max-age=86400")
                self.end_headers()
                self.wfile.write(donnees)
                return
            if route in ("/", "/index.html"):
                self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
            elif route == "/parametres":
                self._send(200, PAGE_PARAMETRES.encode("utf-8"), "text/html; charset=utf-8")
            elif route == "/harpe":
                self._send(200, PAGE_HARPE.encode("utf-8"), "text/html; charset=utf-8")
            elif route == "/api/harpe/moniteur":
                if service is None:
                    self._json({"erreur": "moniteur indisponible"}, 409)
                else:
                    requete = parse_qs(urlparse(self.path).query)
                    self._json(service.monitor.since(int(requete.get("depuis", ["0"])[0])))
            elif route == "/api/latence":
                self._json(service.latency_report() if service else {"mesures": 0})
            elif route == "/api/harpe/reglages":
                if service is None:
                    self._json({"erreur": "réglages de la harpe indisponibles"}, 409)
                else:
                    self._json(service.state())
            elif route == "/api/midi/position":
                self._json({"position": runtime.lecteur.position(), "pistes": runtime.lecteur.pistes})
            elif route == "/api/jeu":
                try:
                    sorties = runtime.midi_outputs()
                except Exception as exc:
                    sorties = []
                    print(f"liste des sorties MIDI : {exc}", flush=True)
                self._json({"sorties": sorties, "sortie": runtime.parametres.get("sortie_midi", ""),
                            "origine": runtime.midi_output_default,
                            "son_jeu": runtime.parametres.get("son_jeu", "aucun"), "pc": pc})
            elif route == "/api/sons":
                from .sampler import banque
                self._json({"sons": [{"fichier": f, "hauteur": h} for f, h in banque()]})
            elif route == "/api/audio/sorties":
                try:
                    from .sampler import LecteurAudio
                    self._json({"sorties": LecteurAudio.sorties(),
                                "sortie": runtime.parametres.get("sortie_audio", ""),
                                "erreur_son": runtime.audio_pc.erreur if runtime.audio_pc else None})
                except Exception as exc:
                    self._json({"sorties": [], "erreur": str(exc)})
            elif route == "/api/controle":
                self._json({"connecte": runtime.control_connected(), "pc": pc,
                            "actif": bool(runtime.parametres.get("controle_actif")),
                            "entree": runtime.parametres.get("controle_entree", ""),
                            "entrees": runtime.keyboard_ports(),
                            "journal": list(runtime.control_log),
                            "morceaux": runtime.bank.names, "index": runtime.bank.index})
            elif route == "/api/controle/exporter":
                from .controle import piste_exemple
                with runtime.lock:
                    preset = runtime.bank.current
                    donnees = piste_exemple(preset.name, runtime.bank.index, len(preset.parts))
                    nom = "".join(c if c.isalnum() or c in " -_" else "_" for c in preset.name)
                self.send_response(200)
                self.send_header("Content-Type", "audio/midi")
                self.send_header("Content-Disposition",
                                 f'attachment; filename="Controle - {nom}.mid"')
                self.send_header("Content-Length", str(len(donnees)))
                self.end_headers()
                self.wfile.write(donnees)
            elif route == "/api/claviers":
                try:
                    self._json({"ports": runtime.keyboard_ports(),
                                "clavier": runtime.parametres.get("clavier", ""),
                                "son_clavier": runtime.parametres.get("son_clavier", ""),
                                "clavier_joue": bool(runtime.parametres.get("clavier_joue")),
                                "pc": pc})
                except Exception as exc:
                    self._json({"erreur": str(exc)})
            elif route == "/api/corbeille":
                self._json({"morceaux": runtime.trash_list()})
            elif route == "/api/apropos":
                from . import DATE_VERSION, NOM, __version__
                self._json({"nom": NOM, "version": __version__, "date": DATE_VERSION, "pc": pc,
                            "firmware": service.firmware() if service else None})
            elif route == "/api/etat":
                etat = runtime.state()
                etat["pc"] = pc
                etat["langue"] = runtime.parametres["langue"]
                etat["notes"] = runtime.nommage_notes()
                etat["harpe_injoignable"] = bool(getattr(harpe, "injoignable", False))
                from .harp_settings import FIRMWARE_TESTE
                etat["firmware"] = service.firmware() if service else None
                etat["firmware_teste"] = FIRMWARE_TESTE
                from . import DEPOT, __version__
                etat["version"] = __version__
                etat["depot"] = DEPOT
                from .harp_settings import BY_KEY
                etat["harpe_modifiee_noms"] = [
                    (BY_KEY[k].label if etat["langue"] == "en" else (BY_KEY[k].label_fr or BY_KEY[k].label))
                    if k in BY_KEY else k for k in etat.get("harpe_modifiee", [])]
                etat["clavier"] = runtime.parametres.get("clavier", "")
                etat["clavier_connecte"] = runtime.keyboard_connected()
                etat["noms"] = {
                    str(n): note_name(n, runtime.nommage_notes()) for n in range(128)
                }
                self._json(etat)
            elif route == "/api/reseau":
                try:
                    self._json(network.state())
                except Exception as exc:
                    self._json({"erreur": str(exc)}, 500)
            else:
                self._json({"erreur": "page inconnue"}, 404)

        # ----------------------------------------------------------- ecritures

        def do_POST(self) -> None:  # noqa: N802
            route = urlparse(self.path).path
            try:
                corps = self._read_json()
                if route == "/api/memoire":
                    runtime.select(int(corps["index"]))
                elif route == "/api/memoire/recharger":
                    runtime.reload_current()
                elif route == "/api/memoire/harpe":
                    faits = runtime.memorize_harp(bool(corps.get("tous")))
                    self._json(runtime.state() | {"memorises": faits})
                    return
                elif route == "/api/harpe/commun":
                    runtime.set_harp_common(str(corps.get("cle", "")), bool(corps.get("commun")))
                elif route == "/api/preset_harpe":
                    numero = corps.get("numero")
                    runtime.set_harp_preset(None if numero in (None, "", 0) else int(numero))
                elif route == "/api/rayon":
                    runtime.set_beam_note(int(corps["rayon"]), int(corps["note"]))
                elif route == "/api/rayon/choisir":
                    runtime.choose_beam(int(corps["rayon"]))
                elif route == "/api/rayon/supprimer":
                    runtime.delete_beam(int(corps["rayon"]))
                elif route == "/api/rayon/accord":
                    runtime.set_beam_chord(int(corps["rayon"]), [int(n) for n in corps.get("notes", [])])
                elif route == "/api/animation":
                    runtime.set_animation(bool(corps.get("active")), float(corps.get("pause", 2)),
                                          float(corps.get("duree", 3)),
                                          None if corps.get("transition") is None else bool(corps.get("transition")),
                                          None if corps.get("trans_duree") is None else float(corps.get("trans_duree")))
                elif route == "/api/rayons/ranger":
                    runtime.sort_beams()
                elif route == "/api/rayons/decaler":
                    runtime.shift_all(int(corps["pas"]))
                elif route == "/api/parametres":
                    runtime.set_parametres(
                        langue=corps.get("langue"),
                        notes=corps.get("notes"),
                        clavier=None if corps.get("clavier") is None else str(corps.get("clavier")),
                        son_clavier=corps.get("son_clavier"),
                        sortie_audio=corps.get("sortie_audio"),
                        sortie_midi=corps.get("sortie_midi"),
                        son_jeu=corps.get("son_jeu"),
                        controle_actif=corps.get("controle_actif"),
                        clavier_joue=corps.get("clavier_joue"),
                        controle_entree=None if corps.get("controle_entree") is None else str(corps.get("controle_entree")),
                        numerotation=corps.get("numerotation"))
                elif route == "/api/son/essai":
                    note = int(corps.get("note", 60))
                    son = runtime.parametres.get("son_clavier")
                    if son == "harpe_pc" and runtime.audio_pc is not None:
                        runtime.audio_pc.message([0x90, note, 100])
                        threading.Timer(1.2, runtime.audio_pc.message, args=([0x80, note, 0],)).start()
                    elif son == "page":
                        runtime.diffuser({"t": "on", "note": note, "vel": 100})
                        threading.Timer(1.2, runtime.diffuser, args=({"t": "off", "note": note, "vel": 0},)).start()
                elif route == "/api/attribution":
                    runtime.set_attribution(bool(corps.get("active")), bool(corps.get("annuler")))
                elif route == "/api/midi/analyser":
                    import base64
                    from .midifile import analyser
                    runtime.lecteur.arreter()
                    brut = base64.b64decode(str(corps.get("donnees", "")))
                    fichier = analyser(brut)
                    runtime.midi = fichier
                    runtime.midi_brut = brut
                    self._json(_analyse(fichier))
                    return
                elif route == "/api/midi/import/rouvrir":
                    fichier, info = runtime.load_import(str(corps.get("morceau", "")))
                    reponse = _analyse(fichier)
                    reponse["import"] = info
                    self._json(reponse)
                    return
                elif route == "/api/midi/animation":
                    num = lambda k: None if corps.get(k) is None else float(corps[k])
                    runtime.set_song_animation(str(corps.get("morceau", "")),
                                               None if corps.get("active") is None else bool(corps["active"]),
                                               None if corps.get("transition") is None else bool(corps["transition"]),
                                               num("pause"), num("duree"), num("trans_duree"))
                    self._json({"ok": True})
                    return
                elif route == "/api/midi/import/sauver":
                    runtime.save_import(str(corps.get("morceau", "")), dict(corps.get("import", {})))
                    self._json({"ok": True})
                    return
                elif route == "/api/midi/maj":
                    nom = str(corps.get("nom", ""))
                    if corps.get("parties"):
                        gardees = runtime.update_song(nom, parties=[
                            {"nom": str(x.get("nom", "")), "notes": [int(n) for n in x.get("notes", [])]}
                            for x in corps["parties"]])
                    else:
                        gardees = runtime.update_song(nom, notes=[int(n) for n in corps.get("notes", [])])
                    if corps.get("import"):
                        runtime.save_import(nom, dict(corps["import"]))
                    if corps.get("animation"):
                        a = corps["animation"]
                        runtime.set_song_animation(nom, bool(a.get("active")), bool(a.get("transition")),
                                                   a.get("pause"), a.get("duree"), a.get("trans_duree"))
                    self._json(runtime.state() | {"gardees": gardees})
                    return
                elif route == "/api/midi/commandes":
                    from .controle import piste_commandes
                    if runtime.midi is None:
                        raise ValueError("aucun fichier MIDI chargé")
                    nom = str(corps.get("morceau", ""))
                    if nom not in runtime.bank.names:
                        raise ValueError("crée d’abord le morceau")
                    donnees = piste_commandes(runtime.midi, list(corps.get("commandes", [])),
                                              runtime.bank.names.index(nom), nom,
                                              int(corps.get("debut", 1)) - 1)
                    sur = "".join(ch if ch.isalnum() or ch in " -_" else "_" for ch in nom)
                    self.send_response(200)
                    self.send_header("Content-Type", "audio/midi")
                    self.send_header("Content-Disposition", f'attachment; filename="Controle - {sur}.mid"')
                    self.send_header("Content-Length", str(len(donnees)))
                    self.end_headers()
                    self.wfile.write(donnees)
                    return
                elif route == "/api/morceaux/exporter":
                    tous = bool(corps.get("tous"))
                    paquet = runtime.export_songs(None if tous else [runtime.bank.index])
                    donnees = json.dumps(paquet, ensure_ascii=False, indent=1).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Length", str(len(donnees)))
                    self.end_headers()
                    self.wfile.write(donnees)
                    return
                elif route == "/api/usb/exporter":
                    from . import usb
                    from datetime import date
                    jour = date.today().isoformat()
                    if corps.get("quoi") == "listes":
                        tous = bool(corps.get("tous"))
                        paquet = runtime.export_lists(None if tous else [str(corps.get("nom", ""))])
                        nom = ("Toutes les listes " + jour) if tous else ("Liste " + str(corps.get("nom", "")))
                    else:
                        tous = bool(corps.get("tous"))
                        paquet = runtime.export_songs(None if tous else [runtime.bank.index])
                        nom = ("Tous les morceaux " + jour) if tous else runtime.bank.current.name
                    donnees = json.dumps(paquet, ensure_ascii=False, indent=1).encode("utf-8")
                    self._json({"fichier": usb.ecrire(nom, donnees)})
                    return
                elif route == "/api/usb/lister":
                    from . import usb
                    self._json({"fichiers": usb.lister()})
                    return
                elif route == "/api/usb/lire":
                    from . import usb
                    self._json({"paquet": json.loads(usb.lire(str(corps.get("fichier", ""))).decode("utf-8"))})
                    return
                elif route.startswith("/api/maj/pi"):
                    if pc:
                        raise ValueError("la mise à jour du Pi se fait depuis la page du Pi")
                    from . import majpi
                    if route == "/api/maj/pi/etat":
                        from . import __version__
                        self._json({"version": __version__, "precedente": majpi.version_precedente()})
                        return
                    if route == "/api/maj/pi/usb/lister":
                        from . import usb
                        self._json({"fichiers": usb.lister(motif="kromabridge-pi*.zip")})
                        return
                    if route == "/api/maj/pi/revenir":
                        version = majpi.revenir()
                    else:
                        if route == "/api/maj/pi/usb":
                            from . import usb
                            donnees = usb.lire(str(corps.get("fichier", "")), suffixe=".zip")
                        else:
                            import base64
                            donnees = base64.b64decode(str(corps.get("donnees", "")))
                        version = majpi.installer(donnees)
                    runtime.journal(f"mise a jour du Pi : version {version} installee, redemarrage du programme")
                    self._json({"version": version})
                    majpi.redemarrer()
                    return
                elif route == "/api/heure":
                    ecart = None
                    if not pc:
                        from . import heure
                        ecart = heure.regler(float(corps.get("ms", 0)))
                        if ecart is not None:
                            runtime.journal(f"horloge du Pi mise a l'heure de l'appareil (ecart corrige : {ecart:+.0f} s)")
                    self._json({"ecart": ecart})
                    return
                elif route == "/api/usb/ejecter":
                    from . import usb
                    usb.ejecter()
                    self._json({"ok": True})
                    return
                elif route == "/api/listes/exporter":
                    paquet = runtime.export_lists(None if corps.get("tous") else [str(corps.get("nom", ""))])
                    donnees = json.dumps(paquet, ensure_ascii=False, indent=1).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Length", str(len(donnees)))
                    self.end_headers()
                    self.wfile.write(donnees)
                    return
                elif route == "/api/listes/importer":
                    noms = corps.get("noms")
                    resultat = runtime.import_lists(corps.get("paquet"), None if noms is None else [str(n) for n in noms],
                                                    bool(corps.get("remplacer")))
                    self._json(runtime.state() | {"echange": resultat})
                    return
                elif route == "/api/morceaux/importer":
                    resultat = runtime.import_songs(corps.get("paquet"), bool(corps.get("remplacer")))
                    self._json(runtime.state() | {"echange": resultat})
                    return
                elif route == "/api/midi/ecouter":
                    fin = corps.get("fin")
                    runtime.midi_ecouter([int(x) for x in corps.get("pistes", [])],
                                         float(corps.get("debut", 0)),
                                         None if fin is None else float(fin))
                    self._json({"ok": True})
                    return
                elif route == "/api/midi/arreter":
                    runtime.lecteur.arreter()
                    self._json({"ok": True})
                    return
                elif route == "/api/midi/creer":
                    if corps.get("parties"):
                        runtime.new_song_from_parts(str(corps.get("nom", "")), [
                            {"nom": str(x.get("nom", "")), "notes": [int(n) for n in x.get("notes", [])]}
                            for x in corps["parties"]])
                    else:
                        runtime.new_song_from_notes(str(corps.get("nom", "")),
                                                    [int(n) for n in corps.get("notes", [])])
                    if corps.get("import"):
                        runtime.save_import(str(corps.get("nom", "")).strip(), dict(corps["import"]))
                    if corps.get("animation"):
                        a = corps["animation"]
                        runtime.set_song_animation(str(corps.get("nom", "")).strip(),
                                                   bool(a.get("active")), bool(a.get("transition")),
                                                   a.get("pause"), a.get("duree"), a.get("trans_duree"))
                elif route == "/api/partie":
                    runtime.change_part(int(corps["index"]))
                elif route == "/api/partie/ajouter":
                    copie = corps.get("copie")
                    runtime.part_add(None if copie is None else int(copie))
                elif route == "/api/partie/deplacer":
                    piste = runtime.part_move(int(corps["de"]), int(corps["vers"]))
                    self._json(runtime.state() | {"piste_a_refaire": piste})
                    return
                elif route == "/api/partie/supprimer":
                    piste = runtime.part_delete([int(i) for i in corps.get("indices", [])])
                    self._json(runtime.state() | {"piste_a_refaire": piste})
                    return
                elif route == "/api/morceau/supprimer":
                    avant = runtime.bank.names
                    runtime.delete_song(int(corps["index"]))
                    self._json(runtime.state() | {"numeros": runtime._numeros_changes(avant)})
                    return
                elif route == "/api/morceaux/supprimer":
                    avant = runtime.bank.names
                    runtime.delete_songs([str(n) for n in corps.get("noms", [])])
                    self._json(runtime.state() | {"numeros": runtime._numeros_changes(avant)})
                    return
                elif route == "/api/morceaux/renommer":
                    runtime.rename(str(corps.get("nom", "")), int(corps["index"]))
                elif route == "/api/listes/ordre":
                    changes = runtime.set_default_order([str(n) for n in corps.get("noms", [])])
                    self._json(runtime.state() | {"numeros": changes})
                    return
                elif route == "/api/corbeille/restaurer":
                    nom = runtime.trash_restore(str(corps.get("fichier", "")))
                    self._json(runtime.state() | {"restaure": nom})
                    return
                elif route == "/api/corbeille/supprimer":
                    runtime.trash_delete(str(corps.get("fichier", "")))
                elif route == "/api/corbeille/vider":
                    runtime.trash_empty()
                elif route == "/api/listes/creer":
                    runtime.list_create(str(corps.get("nom", "")))
                elif route == "/api/listes/renommer":
                    runtime.list_rename(str(corps.get("ancien", "")), str(corps.get("nouveau", "")))
                elif route == "/api/listes/supprimer":
                    runtime.list_delete(str(corps.get("nom", "")))
                elif route == "/api/listes/morceaux":
                    runtime.list_set_songs(str(corps.get("nom", "")), [str(n) for n in corps.get("morceaux", [])])
                elif route == "/api/listes/activer":
                    runtime.list_activate(corps.get("nom") or None)
                elif route == "/api/controle/tout":
                    import io
                    import zipfile
                    pistes = runtime.control_tracks()
                    if not pistes:
                        raise ValueError("aucun morceau n'a de piste de contrôle mémorisée")
                    tampon = io.BytesIO()
                    with zipfile.ZipFile(tampon, "w", zipfile.ZIP_DEFLATED) as z:
                        for nom, donnees in pistes:
                            sur = "".join(c if c.isalnum() or c in " -_" else "_" for c in nom)
                            z.writestr(f"Controle - {sur}.mid", donnees)
                    donnees = tampon.getvalue()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/zip")
                    self.send_header("Content-Length", str(len(donnees)))
                    self.end_headers()
                    self.wfile.write(donnees)
                    return
                elif route == "/api/morceau/nouveau":
                    src = corps.get("source")
                    runtime.new_song(str(corps.get("nom", "")),
                                     None if src in (None, "", "vierge") else int(src),
                                     int(corps.get("rayons", 9)))
                elif route == "/api/rayons/nombre":
                    runtime.set_beam_count(int(corps["nombre"]))
                elif route == "/api/ecouter":
                    runtime.preview(int(corps["note"]))
                elif route == "/api/canal":
                    runtime.set_out_channel(int(corps["canal"]))
                elif route == "/api/son_synthe":
                    val = lambda k: None if corps.get(k) in (None, "") else int(corps[k])
                    runtime.set_synth_sound(val("programme"), val("msb"), val("lsb"),
                                            corps.get("actif") is not False, corps.get("numerotation"))
                elif route == "/api/nom":
                    runtime.rename(str(corps["nom"]))
                elif route == "/api/harpe":
                    runtime.reload_harp()
                elif route.startswith("/api/harpe/") and route not in (
                        "/api/harpe/ouvrir", "/api/harpe/fermer"):
                    if service is None:
                        raise RuntimeError("réglages de la harpe indisponibles")
                    if route == "/api/harpe/lire":
                        etat = service.read()
                    elif route == "/api/harpe/reglage":
                        etat = service.set(str(corps["cle"]), int(corps["valeur"]))
                    elif route == "/api/harpe/preset/rappeler":
                        etat = service.recall(int(corps["numero"]))
                    elif route == "/api/harpe/preset/sauver":
                        etat = service.save(int(corps["numero"]))
                    elif route in ("/api/harpe/preset/apercu", "/api/harpe/preset/enregistrer", "/api/harpe/preset/charger"):
                        from .presets_harpe import Copie
                        copie = Copie(None if runtime.presets_dir is None else runtime.presets_dir.parent / "presets_harpe.json")
                        n = int(corps.get("numero", 0))
                        fermee = not getattr(harpe, "_ouverte", False)
                        entree = copie.get(n)
                        if route == "/api/harpe/preset/enregistrer" or corps.get("relire") or entree is None:
                            if not fermee:
                                raise ValueError("Fermez la harpe pour lire ou enregistrer un preset : pendant un instant, elle applique vraiment le preset.")
                            contenu = service.lire_preset(n)
                            if route == "/api/harpe/preset/enregistrer":
                                copie.garder_ancien(n, contenu)
                                cible, _ = runtime.cible_preset_harpe(service.valeurs)
                                service.enregistrer_preset(n, cible)
                                runtime.journal(f"preset {n} de la harpe : reglages du morceau {runtime.bank.current.name!r} enregistres")
                                entree = copie.mettre(n, cible)
                            else:
                                entree = copie.mettre(n, contenu)
                        if route == "/api/harpe/preset/charger":
                            runtime.song_from_harp_preset(n, entree["valeurs"], bool(corps.get("nouveau")), corps.get("nom"))
                            runtime.journal(f"preset {n} de la harpe charge dans le morceau {runtime.bank.current.name!r}")
                            service.valeurs.update({k: v for k, v in runtime._harpe_attendue(runtime.bank.current).items()})
                        cible, notes = runtime.cible_preset_harpe(service.valeurs)
                        etat = {"numero": n, "valeurs": entree["valeurs"], "lu": entree["lu"], "morceau": cible,
                                "notes_reproduites": notes, "nom_morceau": runtime.bank.current.name,
                                "gamme": runtime.bank.current.gamme, "copie": copie.resume()}
                    elif route == "/api/harpe/preset/copie":
                        from .presets_harpe import Copie
                        copie = Copie(None if runtime.presets_dir is None else runtime.presets_dir.parent / "presets_harpe.json")
                        etat = {"copie": copie.resume(), "ouverte": bool(getattr(harpe, "_ouverte", False))}
                    elif route == "/api/harpe/moniteur/effacer":
                        service.monitor.clear()
                        etat = {"efface": True}
                    elif route == "/api/harpe/usine":
                        etat = service.factory_reset()
                    else:
                        self._json({"erreur": "action inconnue"}, 404)
                        return
                    self._json(etat)
                    return
                elif route in ("/api/harpe/ouvrir", "/api/harpe/fermer"):
                    commander_harpe(runtime, harpe, service, route.endswith("ouvrir"))
                elif route == "/api/silence":
                    runtime.panic_total()
                elif route == "/api/apprentissage/clavier/debut":
                    runtime.start_kb_learning()
                elif route == "/api/apprentissage/clavier/annuler":
                    runtime.cancel_kb_learning()
                elif route == "/api/apprentissage/clavier/fin":
                    runtime.finish_kb_learning(str(corps.get("nom", "")),
                                               [int(n) for n in corps.get("notes", [])],
                                               bool(corps.get("remplacer")),
                                               [{"nom": str(x.get("nom", "")),
                                                 "notes": [int(n) for n in x.get("notes", [])]}
                                                for x in corps.get("parties", [])])
                elif route == "/api/apprentissage/clavier/vider":
                    runtime.clear_kb_learning()
                elif route == "/api/apprentissage/debut":
                    runtime.start_learning()
                elif route == "/api/apprentissage/annuler":
                    runtime.cancel_learning()
                elif route == "/api/apprentissage/retour":
                    runtime.undo_learning()
                elif route == "/api/apprentissage/fin":
                    runtime.finish_learning(corps.get("nom"))
                elif route == "/api/eteindre" and pc:
                    raise RuntimeError("version PC : ferme simplement la fenêtre du programme")
                elif route == "/api/eteindre":
                    runtime.panic()
                    power_off()
                    self._json({"eteint": True})
                    return
                elif route == "/api/reseau/direct":
                    network.ensure_direct_profile()
                    network.later(network.switch_to_direct)
                    self._json(network.state() | {"bascule": "direct"})
                    return
                elif route == "/api/reseau/maison":
                    if network.home_profile() is None:
                        raise RuntimeError("aucun Wi-Fi de maison enregistré")
                    network.later(network.switch_to_home)
                    self._json(network.state() | {"bascule": "maison"})
                    return
                else:
                    self._json({"erreur": "action inconnue"}, 404)
                    return
            except (KeyError, ValueError) as exc:
                self._json({"erreur": str(exc)}, 400)
                return
            except RuntimeError as exc:
                self._json({"erreur": str(exc)}, 409)
                return
            self._json(runtime.state())

    return Handler


def serve(runtime: Runtime, host: str = "0.0.0.0", port: int = 80, harpe=None,
          service=None, pc: bool = False) -> ThreadingHTTPServer:
    """Demarre le serveur. A l'appelant de decider s'il tourne en fil separe.

    `harpe` (un HarpControl) sert aux boutons d'ouverture et de fermeture des rayons.
    """
    server = ThreadingHTTPServer((host, port), make_handler(runtime, harpe, service, pc))
    return server


PAGE = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Kromabridge</title>
<style>
  :root {
    --fond: #11131a; --carte: #1b1f2a; --trait: #2c3242;
    --texte: #eef1f7; --doux: #9aa3b8; --vif: #ffb454; --actif: #4ea1ff;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 16px; background: var(--fond); color: var(--texte);
    font: 16px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif;
    -webkit-text-size-adjust: 100%;
  }
  header { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 16px; }
  h1 { font-size: 20px; margin: 0; flex: 1 1 auto; }
  select, button {
    font: inherit; color: var(--texte); background: var(--carte);
    border: 1px solid var(--trait); border-radius: 10px; padding: 10px 14px;
    min-height: 44px;
  }
  button:active { background: var(--trait); }
  button:disabled { opacity: .35; cursor: not-allowed; }
  button.vif { border-color: var(--vif); color: var(--vif); }
  button.modifie { background: #b85c00; border-color: var(--vif); color: #fff; font-weight: 600; }
  button.actif { background: #1d4f8f; border-color: var(--actif); color: #fff; font-weight: 600; }
  button.danger { background: #c62828; border-color: #c62828; color: #fff; font-weight: 600; }
  .rayons { display: grid; gap: 10px; grid-template-columns: 1fr; }
  @media (min-width: 700px) { .rayons { grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); } }
  .rayon {
    background: var(--carte); border: 1px solid var(--trait); border-radius: 14px;
    padding: 12px; display: flex; flex-direction: column; gap: 8px;
  }
  .rayon.touche { border: 3px solid var(--actif); background: #1d4f8f; padding: 10px; }
  .rayon.touche .num { color: #dbe9ff; }
  .num { color: var(--doux); font-size: 13px; letter-spacing: .04em; }
  .note { font-size: 24px; font-weight: 600; cursor: pointer; }
  dialog { background: var(--carte); color: var(--texte); border: 1px solid var(--trait);
           border-radius: 14px; padding: 16px 20px; width: min(92vw, 460px); }
  dialog::backdrop { background: rgba(0,0,0,.6); }
  dialog h2 { margin: 0 0 8px; font-size: 20px; }
  dialog input[type=text], dialog select { width: 100%; font: inherit; color: var(--texte);
           background: var(--fond); border: 1px solid var(--trait); border-radius: 8px; padding: 8px; }
  dialog .doux { color: var(--doux); font-size: 14px; }
  #dlgmidi { width: min(94vw, 640px); }
  #mpistes label, #mnotes label { display: block; padding: 6px 4px; border-bottom: 1px solid var(--trait); cursor: pointer; }
  #mpistes input, #mnotes input { width: 20px; height: 20px; vertical-align: middle; margin-right: 8px; }
  #mnotes label { display: inline-block; border: 1px solid var(--trait); border-radius: 8px; margin: 3px; padding: 6px 10px; }
  #mpistes { max-height: 50vh; overflow-y: auto; }
  #dlgmidi { width: 98vw; max-width: none; height: 96vh; max-height: none; }
  .mpiste { border-bottom: 1px solid var(--trait); padding: 6px 0; }
  .mpiste .mtete { display: flex; align-items: center; gap: 8px; }
  .mpiste .mtete label { flex: 1; border: 0; padding: 4px; }
  .mpiste .mjouer { min-height: 36px; padding: 4px 12px; }
  .mpiste .mvue { position: relative; height: 48px; background: var(--fond); border-radius: 6px; cursor: pointer; }
  .mpiste canvas { width: 100%; height: 48px; display: block; }
  .mpiste .msel { position: absolute; top: 0; bottom: 0; background: rgba(255,180,84,.22);
    border-left: 2px solid var(--vif); border-right: 2px solid var(--vif); display: none; pointer-events: none; }
  .mpiste .mvue { touch-action: pan-x; }
  .mpiste .mdefile { overflow-x: auto; overflow-y: hidden; }
  .mnum { width: 4.5em; font: inherit; color: var(--texte); background: var(--fond);
    border: 1px solid var(--trait); border-radius: 8px; padding: 6px; }
  .mparties label { display: inline-flex; align-items: center; gap: 4px; }
  #mzoom { width: auto; }
  .mparties { align-items: center; flex-wrap: wrap; margin: 8px 0; color: var(--doux); }
  .mparties button { flex: 0 0 auto; min-height: 36px; padding: 4px 12px; }
  #mcmdliste .lignecmd { display: flex; gap: 6px; align-items: center; margin: 4px 0; flex-wrap: wrap; }
  #mcmdliste select, #mcmdliste input { font: inherit; color: var(--texte); background: var(--fond);
    border: 1px solid var(--trait); border-radius: 8px; padding: 6px; }
  #mcmdliste input { width: 4.5em; }
  dialog #mcmdliste select { width: auto; flex: 1 1 220px; }
  #mcmdliste .lignecmd { flex-wrap: nowrap; }
  #mvuecmd { height: 110px; touch-action: pan-x; }
  .anim button.vider { min-height: 32px; padding: 2px 8px; margin-left: -4px; flex: 0 0 auto; }
  #mcmdliste .lignecmd.nouvelle { background: rgba(255,180,84,.16); outline: 2px solid var(--vif); border-radius: 8px; }
  #mcmdcanvas { height: 110px; }
  .mpiste .mcurseur { position: absolute; top: 0; bottom: 0; width: 2px; background: var(--vif); display: none; }
  .note .crayon { font-size: 14px; color: var(--doux); }
  .choixnote { font-size: 20px; margin: 4px 0; width: 100%; }
  .rayon .plusaccord { min-height: 30px; padding: 0 10px; font-size: 18px; line-height: 1; }
  .rayon .supprray { min-height: 30px; padding: 0 6px; font-size: 14px; line-height: 1; }
  .rayon .num .plusaccord { padding: 0 8px; font-size: 16px; }
  .rayon .accord { display: flex; gap: 6px; align-items: center; }
  .rayon .accord .choixnote { font-size: 17px; margin: 0; flex: 1; }
  .rayon .accord button { min-height: 34px; padding: 2px 10px; flex: 0 0 auto; }
  .ligne { display: flex; gap: 8px; }
  .ligne button { flex: 1; padding: 8px; }
  footer { margin-top: 20px; color: var(--doux); font-size: 14px; }
  .etat { margin-bottom: 12px; color: var(--doux); }
  .tous { align-items: center; margin-bottom: 12px; color: var(--doux); }
  .anim { align-items: center; margin-bottom: 12px; color: var(--doux); flex-wrap: wrap; }
  .anim input[type=number] { width: 4.5em; font: inherit; color: var(--texte); background: var(--carte);
    border: 1px solid var(--trait); border-radius: 8px; padding: 6px; }
  .anim select { font: inherit; color: var(--texte); background: var(--carte);
    border: 1px solid var(--trait); border-radius: 8px; padding: 6px; }
  .anim input[type=checkbox] { width: 22px; height: 22px; vertical-align: middle; }
  .tous button { flex: 0 0 auto; min-width: 56px; }
  .commande { display: flex; gap: 8px; margin-bottom: 12px; }
  .partiesmorceau { align-items: center; margin-bottom: 12px; color: var(--doux); flex-wrap: wrap; }
  .partiesmorceau button { flex: 0 0 auto; min-width: 64px; font-weight: 600; }
  .partiesmorceau button.ptuile { touch-action: none; user-select: none; -webkit-user-select: none; }
  .partiesmorceau button.ptuile.glisse { outline: 2px solid var(--actif); opacity: .85; }
  .partiesmorceau button.ptuile.asuppr { border-color: #e5484d; color: #e5484d; text-decoration: line-through; }
  .partiesmorceau button.pplus { min-width: 44px; }
  .commande button { flex: 1; font-weight: 600; }
  /* barre du haut : menus deroulants */
  .barre { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-bottom: 14px; }
  .barre h1 { flex: 0 1 auto; margin-right: 4px; }
  .barre .nav { min-width: 44px; padding: 8px 10px; font-size: 18px; }
  .barre .harpe { font-weight: 600; }
  .barre .espace { flex: 1 1 auto; }
  .menu { position: relative; }
  .menu > button::after { content: " ▾"; color: var(--doux); }
  .menuliste { display: none; position: absolute; top: 100%; left: 0; z-index: 20; min-width: 250px;
    background: var(--carte); border: 1px solid var(--trait); border-radius: 12px; padding: 6px;
    box-shadow: 0 8px 24px rgba(0,0,0,.5); }
  .menu.droite .menuliste { left: auto; right: 0; }
  .menu.ouvert .menuliste { display: block; }
  .menuliste button, .menuliste a > button { display: block; width: 100%; text-align: left; border: 0;
    background: transparent; border-radius: 8px; }
  .menuliste button:hover { background: var(--trait); }
  .menuliste button:disabled { opacity: .35; background: transparent; }
  .menuliste hr { border: 0; border-top: 1px solid var(--trait); margin: 4px 0; }
  .menuliste .info { color: var(--doux); font-size: 13px; padding: 4px 12px; }
  .icone { min-width: 44px; padding: 8px 10px; font-size: 18px; position: relative; }
  .icone.eteint { opacity: .45; }
  #silence { background: #8b1a1a; border-color: #c62828; color: #fff; font-weight: 600; }
  #reglages.point::after { content: ""; position: absolute; top: 4px; right: 4px; width: 11px; height: 11px;
    border-radius: 50%; background: var(--vif); }
  #reglages::after { content: none; }
  #lmorceaux .lm { display: flex; align-items: center; gap: 6px; padding: 4px 0; border-bottom: 1px solid var(--trait); }
  #dlgmorceaux { width: min(94vw, 640px); }
  #gmliste .lm { display: flex; align-items: center; gap: 6px; padding: 4px 0; border-bottom: 1px solid var(--trait); }
  #gmliste .lm span { flex: 1; }
  #gmliste .lm button { min-height: 36px; padding: 4px 10px; flex: 0 0 auto; }
  #gmliste .lm input { width: 22px; height: 22px; flex: 0 0 auto; }
  #lmorceaux .lm span { flex: 1; }
  #lmorceaux .lm button { min-height: 36px; padding: 4px 10px; flex: 0 0 auto; }
  #lmorceaux .poignee { flex: 0 0 auto; padding: 6px 10px; font-size: 20px; color: var(--doux); cursor: grab; touch-action: none; user-select: none; -webkit-user-select: none; }
  #lmorceaux .lm.glisse { background: var(--fond); outline: 2px solid var(--actif); border-radius: 8px; }
  #lmorceaux .lm.glisse .poignee { cursor: grabbing; color: var(--texte); }
  #dlglistes, #dlgcorbeille { width: min(94vw, 600px); }
  #anotesaccord .lm { display: flex; align-items: center; gap: 6px; padding: 4px 0; border-bottom: 1px solid var(--trait); }
  #anotesaccord .lm span { flex: 0 0 6em; }
  #anotesaccord .lm select { flex: 1; }
  #anotesaccord .lm button { min-height: 36px; padding: 4px 10px; flex: 0 0 auto; }
  #cliste .lm { display: flex; align-items: center; gap: 6px; padding: 4px 0; border-bottom: 1px solid var(--trait); }
  #cliste .lm span { flex: 1; }
  #cliste .lm button { min-height: 36px; padding: 4px 10px; flex: 0 0 auto; }
  .barre .groupe { display: flex; align-items: center; gap: 6px; flex: 0 0 auto; margin-right: 6px; }
  #nomliste { font-size: 20px; font-weight: bold; color: var(--texte); }
  #posmorceau { font-size: 14px; }
  /* reglages ouverts sous la barre : le reste de la page est cache */
  body.enreglages > *:not(header):not(dialog):not(#vuereglages):not(#barreglages):not(script):not(input) { display: none !important; }
  #vuereglages { width: 100%; height: calc(100vh - 150px); border: 0; border-radius: 12px; background: var(--fond); }
  #listeouvrir button { display: block; width: 100%; text-align: left; margin: 4px 0; }
  #listeouvrir button.courant { border-color: var(--vif); color: var(--vif); }
</style>
</head>
<body>
<header class="barre">
  <span class="groupe"><button id="listeprec" class="nav" title="Liste précédente">▲</button>
    <button id="listesuiv" class="nav" title="Liste suivante">▼</button>
    <span id="nomliste" title="Liste de morceaux active"></span></span>
  <span class="groupe"><button id="precedent" class="nav" title="Morceau précédent">◀</button>
    <button id="suivant" class="nav" title="Morceau suivant">▶</button>
    <h1 id="titre">Harpe</h1><span class="doux" id="posmorceau"></span></span>
  <select id="memoires" aria-label="Memoire" style="display:none"></select>
  <button id="ouvrir" class="vif harpe">Ouvrir la harpe</button>
  <button id="fermer" class="harpe">Fermer la harpe</button>
  <div class="menu" id="menumorceau"><button class="menubtn">Morceau</button>
    <div class="menuliste">
      <button id="mouvrir">Ouvrir…</button>
      <button id="gerermorceaux">Gérer les morceaux…</button>
      <button id="nouveau">Nouveau morceau</button>
      <button id="enregsous">Enregistrer sous…</button>
      <button id="renommer">Renommer</button>
      <button id="supprimer">Supprimer</button>
      <button id="mcorbeille">Corbeille…</button>
      <hr>
      <button id="importer">Importer un fichier MIDI</button>
      <button id="modifimport" disabled>Modifier l’import</button>
      <hr>
      <button id="exporter">Exporter des morceaux</button>
      <button id="importermorceaux">Importer des morceaux</button>
      <button id="musbimport" class="usbseul">Importer depuis la clé USB…</button>
    </div></div>
  <input type="file" id="fmorceaux" accept=".kromalaser,application/json,application/octet-stream" style="display:none">
  <div class="menu" id="menulistes"><button class="menubtn">Listes</button>
    <div class="menuliste"><div id="choixlistes"></div><hr>
      <button id="gererlistes">Gérer les listes…</button><hr>
      <button id="exporterlistes">Exporter des listes…</button>
      <button id="importerlistes">Importer des listes…</button>
      <button id="lusbimport" class="usbseul">Importer depuis la clé USB…</button></div></div>
  <input type="file" id="flistes" accept=".kromalaser,application/json,application/octet-stream" style="display:none">
  <div class="menu" id="menuapprentissage"><button class="menubtn" id="btnapprentissage">Apprentissage</button>
    <div class="menuliste">
      <button id="apprendre">Créer les rayons au clavier</button>
      <button id="attribuer">Changer un rayon au clavier</button>
      <hr>
      <button id="importer2">Importer un fichier MIDI</button>
    </div></div>
  <button id="terminerappr" class="actif" style="display:none"></button>
  <button id="finattrib" class="actif" style="display:none">✓ Garder les changements</button>
  <button id="annulattrib" class="danger" style="display:none">✕ Annuler les changements</button>
  <span class="espace"></span>
  <button id="btnactiverson" class="icone eteint" style="display:none" title="Activer le son de la harpe sur cet appareil">🔇</button>
  <button id="silence" class="icone" title="Couper toutes les notes qui sonnent encore">🛑 <span>Panique</span></button>
  <button id="eteindre" class="icone" title="Éteindre le Pi">⏻</button>
  <div class="menu droite" id="menureglages"><button class="menubtn icone" id="reglages" title="Paramètres">⚙</button>
    <div class="menuliste">
      <button id="vueparam">Paramètres de l’application</button>
      <button id="vueharpe">Paramètres de la harpe</button>
      <button id="apropos">À propos</button>
      <button id="majpi" class="piseul">Mettre à jour le Pi…</button>
      <button id="memoharpe" style="display:none">Mémoriser la harpe dans ce morceau</button>
      <button id="recharger" style="display:none">Rétablir les réglages mémorisés</button>
      <div id="menureseau"><hr>
        <div class="info" id="reseau"></div>
        <button id="basculer">Connexion directe</button></div>
    </div></div>
</header>
<dialog id="dlgmajpi">
  <h2>Mettre à jour le Pi</h2>
  <p id="mpversion"></p>
  <p class="doux"><span>Téléchargez le fichier kromabridge-pi.zip de la nouvelle version (sur l’appareil qui affiche cette page, ou sur une clé USB à brancher sur le Pi), puis choisissez-le ci-dessous. Vos morceaux, listes et réglages ne sont pas touchés.</span></p>
  <input type="file" id="mpfichier" accept=".zip" style="display:none">
  <div class="ligne"><button id="mpchoisir" class="vif">Choisir le fichier…</button><button id="mpusb">Depuis la clé USB du Pi</button></div>
  <div id="mpusbliste"></div>
  <p><button id="mprevenir" style="display:none"></button></p>
  <p class="doux" id="mpetat"></p>
  <div class="ligne"><button id="mpfermer">Fermer</button></div>
</dialog>
<dialog id="dlgapropos">
  <h2 id="aptitre">Kromabridge</h2>
  <p id="apversion"></p>
  <p id="apappareil"></p>
  <p id="apfirmware" class="doux"></p>
  <p><span>Créé par Michael avec Claude.</span></p>
  <p class="doux"><span>Le pont entre la harpe laser et les synthétiseurs : notes par rayon, morceaux, listes, piste de contrôle, réglages de la harpe.</span></p>
  <div class="ligne"><button id="apfermer">Fermer</button></div>
</dialog>
<dialog id="dlgmorceaux">
  <h2>Gérer les morceaux</h2>
  <p class="doux"><span>Le numéro de chaque morceau est celui utilisé par les pistes de contrôle.</span></p>
  <div id="gmliste"></div>
  <div class="ligne mparties"><button id="gmsupprimer" class="danger">Supprimer les morceaux cochés</button>
    <button id="gmliste2">Créer une liste avec les morceaux cochés</button></div>
  <div class="ligne"><button id="gmnouveau">Nouveau morceau</button><button id="gmcorbeille">Corbeille…</button><button id="gmfermer">Fermer</button></div>
</dialog>
<dialog id="dlgpartie">
  <h2>Nouvelle partie</h2>
  <p><label><input type="radio" name="ptype" value="vierge" checked style="width:20px;height:20px;vertical-align:middle"> <span>Partie vierge (5 rayons)</span></label></p>
  <p><label><input type="radio" name="ptype" value="copie" style="width:20px;height:20px;vertical-align:middle"> <span>Copie de la partie</span></label> <select id="pcopie"></select></p>
  <div class="ligne"><button id="pcreer" class="vif">Créer</button><button id="pannuler">Annuler</button></div>
</dialog>
<dialog id="dlglistes">
  <h2>Listes</h2>
  <p><select id="lchoix"></select></p>
  <p class="doux" id="lexplication"></p>
  <div id="lmorceaux"></div>
  <div class="ligne mparties" id="lligneajout"><select id="lajout"></select><button id="lajouter">+ <span>Ajouter</span></button></div>
  <div class="ligne mparties"><button id="lnouvelle">Nouvelle liste</button><button id="lrenommer">Renommer la liste</button>
    <button id="lsupprimer">Supprimer la liste</button></div>
  <div class="ligne"><button id="lvalider" class="vif">Valider</button><button id="lfermer">Fermer</button></div>
</dialog>
<div id="barreglages" class="ligne" style="display:none;align-items:center;margin-bottom:8px">
  <b id="titrereglages" style="flex:1"></b>
  <button id="fermerreglages" style="flex:0 0 auto">✕ <span>Fermer les réglages</span></button></div>
<iframe id="vuereglages" title="Réglages" style="display:none"></iframe>
<dialog id="dlgcorbeille">
  <h2>Corbeille</h2>
  <p class="doux"><span>Les morceaux supprimés. « Restaurer » remet un morceau à la fin de la liste : aucun numéro ne change.</span></p>
  <div id="cliste"></div>
  <div class="ligne"><button id="cvider" class="danger">Vider la corbeille</button><button id="cfermer">Fermer</button></div>
</dialog>
<dialog id="dlgaccord">
  <h2 id="atitreaccord">Accord</h2>
  <p class="doux"><span>Accord tout fait : la note du rayon en est la fondamentale.</span></p>
  <div class="ligne mparties"><select id="atype"></select><button id="aappliquer" class="vif">Appliquer</button></div>
  <p class="doux"><span>Ou note par note :</span></p>
  <div id="anotesaccord"></div>
  <div class="ligne mparties"><button id="aajouternote">+ <span>Ajouter une note</span></button>
    <button id="asupprimeraccord">Supprimer l'accord</button></div>
  <div class="ligne"><button id="afermeraccord">Fermer</button></div>
</dialog>
<dialog id="dlgouvrir">
  <h2>Ouvrir un morceau</h2>
  <div id="listeouvrir"></div>
  <div class="ligne"><button id="oannuler">Annuler</button></div>
</dialog>
<dialog id="dlgexport">
  <h2>Exporter des morceaux</h2>
  <p class="doux"><span>Le fichier se range dans vos téléchargements. Sur l’autre appareil (PC ou Pi), ouvrez-le avec « Importer des morceaux ».</span></p>
  <p class="usbseul"><label><input type="checkbox" id="xusb" style="width:22px;height:22px;vertical-align:middle"> <span>Enregistrer sur la clé USB du Pi</span></label></p>
  <div class="ligne"><button id="xcelui" class="vif"><span>Ce morceau</span></button>
    <button id="xtous"><span>Tous les morceaux</span></button><button id="xannuler">Annuler</button></div>
</dialog>
<dialog id="dlgexportlistes">
  <h2>Exporter des listes</h2>
  <p class="doux"><span>Chaque liste emporte ses morceaux. Le fichier se range dans vos téléchargements ; sur l’autre appareil, ouvrez-le avec « Importer des listes ».</span></p>
  <p><select id="xlchoix" style="width:100%"></select></p>
  <p class="usbseul"><label><input type="checkbox" id="xlusb" style="width:22px;height:22px;vertical-align:middle"> <span>Enregistrer sur la clé USB du Pi</span></label></p>
  <div class="ligne"><button id="xlcelle" class="vif"><span>Cette liste</span></button>
    <button id="xltoutes"><span>Toutes les listes</span></button><button id="xlannuler">Annuler</button></div>
</dialog>
<dialog id="dlgdoublons">
  <h2>Morceaux déjà présents</h2>
  <p id="ddliste"></p>
  <p class="doux"><span>Remplacer : les anciens vont dans la corbeille. Garder les deux : les nouveaux sont renommés.</span></p>
  <div class="ligne"><button id="ddremplacer" class="vif">Remplacer</button><button id="ddgarder">Garder les deux</button>
    <button id="ddannuler">Annuler l’import</button></div>
</dialog>
<dialog id="dlgusb">
  <h2>Clé USB du Pi</h2>
  <p class="doux" id="usbavis"></p>
  <div id="usbfichiers"></div>
  <div class="ligne"><button id="usbejecter">Éjecter la clé</button><button id="usbfermer">Fermer</button></div>
</dialog>
<dialog id="dlgimportlistes">
  <h2>Importer des listes</h2>
  <p class="doux"><span>Cochez les listes à importer. Elles arrivent avec leurs morceaux ; une liste qui porte le même nom est remplacée.</span></p>
  <div id="ilchoix"></div>
  <div class="ligne"><button id="ilimporter" class="vif">Importer</button><button id="ilannuler">Annuler</button></div>
</dialog>
<dialog id="dlgnouveau">
  <h2>Nouveau morceau</h2>
  <p><label><span>Titre</span><br><input type="text" id="ntitre" autocomplete="off"></label></p>
  <p><label><span>Partir de</span><br><select id="nsource"></select></label></p>
  <p id="nlignerayons"><label><span>Nombre de rayons</span> <select id="nrayons"></select></label></p>
  <p class="doux">Une copie reprend tout du morceau choisi (notes, canal, réglages de la harpe) ; l'original reste intact.</p>
  <div class="ligne"><button id="ncreer" class="vif">Créer</button><button id="nannuler">Annuler</button></div>
</dialog>
<dialog id="dlgmidi">
  <h2>Importer un fichier MIDI</h2>
  <p><input type="file" id="mfichier" accept=".mid,.midi,audio/midi"></p>
  <div id="mpistes"></div>
  <p id="mligneselection" class="ligne mparties" style="display:none">
    <label><span>Zoom</span> <select id="mzoom"><option value="1">x1</option><option value="2">x2</option>
      <option value="4">x4</option><option value="8">x8</option><option value="16">x16</option></select></label>
    <label><input type="checkbox" id="mcaler" checked> <span>Caler sur les mesures</span></label>
    <label><span>De la mesure</span> <input type="number" id="mdeb" min="1" class="mnum"></label>
    <label><span>à la mesure</span> <input type="number" id="mfin" min="1" class="mnum"></label>
    <span id="mselinfo" class="doux"></span>
    <button id="mjouersel">&#9654; <span>Écouter la sélection</span></button>
    <button id="msanssel"><span>Effacer la sélection</span></button></p>
  <div id="mnotes"></div>
  <p id="mlignetransp" style="display:none"><label><span>Transposer de</span> <select id="mtransp">
    <option value="-36">-3 oct.</option><option value="-24">-2 oct.</option><option value="-12">-1 oct.</option>
    <option value="0" selected>0</option><option value="12">+1 oct.</option><option value="24">+2 oct.</option>
    <option value="36">+3 oct.</option></select></label></p>
  <p id="mlignetitre" style="display:none"><label><span>Titre</span><br><input type="text" id="mtitre" autocomplete="off"></label></p>
  <p class="doux" id="mavis"></p>
  <div class="ligne mparties" id="mligneanim" style="display:none">
      <label><input type="checkbox" id="manimouv"> <span>Animation d'ouverture</span></label>
      <label><span>pause</span> <input type="number" id="manimpause" min="0" max="30" step="0.5" class="mnum"> s</label>
      <label><span>ouverture</span> <input type="number" id="manimduree" min="0" max="30" step="0.5" class="mnum"> s</label>
      <label style="margin-left:12px"><input type="checkbox" id="manimtrans"> <span>Transition animée entre les parties</span></label>
      <label><input type="number" id="manimtransduree" min="0" max="10" step="0.25" class="mnum"> s</label></div>
  <div class="ligne mparties" id="mlignerapide" style="display:none">
    <label><span>Parties en mesures</span> <input type="text" id="mrapide" placeholder="1-5, 6-30, 31-38" style="width:18em"></label>
    <button id="mrapidebtn"><span>Créer les parties</span></button></div>
  <div id="mlisteparties" class="mparties"></div>
  <div id="mcommandes" style="display:none;border-top:1px solid var(--trait);margin-top:8px;padding-top:8px">
    <div><b><span>Commandes de la harpe</span></b> <span class="doux"><span>(pour la piste de contrôle)</span></span></div>
    <div class="mpiste"><div class="mdefile" id="mdefilecmd"><div class="mvue" id="mvuecmd"><canvas id="mcmdcanvas"></canvas></div></div></div>
    <div class="doux"><span>Cliquez dans l’aperçu pour placer le curseur : « Ajouter une commande » l’ajoute à cet endroit. Faites glisser un repère pour le déplacer.</span></div>
    <div class="ligne mparties"><label><span>Dans le DAW, le morceau commence à la mesure</span>
      <input type="number" id="mdebut" min="1" value="3" class="mnum"></label>
      <span class="doux"><span>(toutes les commandes suivent)</span></span></div>
    <div id="mcmdliste"></div>
    <div class="ligne mparties"><button id="mcmdajout">+ <span>Ajouter une commande</span></button>
      <button id="mcmdauto"><span>Replacer automatiquement</span></button>
      <button id="mcmdexport" class="vif" disabled><span>Exporter la piste de contrôle</span></button></div>
    <div class="doux" id="mcmdavis"></div>
  </div>
  <div class="ligne"><button id="majouter" disabled>+ <span>Ajouter comme partie</span></button>
    <button id="mcreer" class="vif" disabled>Créer le morceau</button><button id="marreter">&#9632; <span>Arrêter l'écoute</span></button><button id="mannuler">Annuler</button></div>
</dialog>
<dialog id="dlgappris">
  <h2>Apprentissage au clavier</h2>
  <p class="doux"><span>Notes jouées, de la plus grave à la plus aiguë (un appui retire une note) :</span></p>
  <div id="anotes" class="mparties"></div>
  <p class="doux" id="aavis"></p>
  <div id="aparties" class="mparties"></div>
  <div class="ligne"><button id="apartie">+ <span>Garder comme partie et continuer</span></button></div>
  <p><label><span>Titre</span><br><input type="text" id="atitre" autocomplete="off"></label></p>
  <div class="ligne"><button id="acreer" class="vif">Créer le morceau</button>
    <button id="aremplacer">Remplacer les notes du morceau affiché</button></div>
  <div class="ligne" style="margin-top:8px"><button id="acontinuer">Continuer à jouer</button>
    <button id="aannuler">Annuler l'apprentissage</button></div>
</dialog>
<div class="ligne" id="activerson" style="display:none;margin-bottom:12px">
  <span class="doux"><span>Touchez 🔇 en haut pour entendre la harpe sur cet appareil. Si vous n’entendez rien ensuite : vérifiez que l’appareil n’est pas en mode silencieux.</span></span>
</div>
<div id="alerteharpe" style="display:none;background:#8b1a1a;color:#fff;padding:10px 14px;border-radius:10px;margin-bottom:12px">
  <b><span>Harpe injoignable</span></b> : <span>elle ne répond plus (lampe rouge ?). Éteins-la puis rallume-la ; le programme la retrouvera tout seul.</span></div>
<div id="avismaj" style="display:none;background:#123d2a;border:1px solid #3fa66b;color:#fff;padding:12px 16px;border-radius:12px;margin-bottom:12px">
  <div class="ligne" style="justify-content:space-between;align-items:flex-start;flex-wrap:nowrap">
    <div><span id="avismajtexte"></span> <a id="avismajlien" href="#" target="_blank" rel="noopener" style="color:#8fe3b0">Télécharger</a></div>
    <button id="avismajfermer" style="flex:0 0 auto" title="Fermer">✕</button></div>
</div>
<div id="avisfirmware" style="display:none;background:#5a3a00;border:1px solid var(--vif);color:#fff;padding:12px 16px;border-radius:12px;margin-bottom:12px">
  <div class="ligne" style="justify-content:space-between;align-items:flex-start;flex-wrap:nowrap">
    <div id="avisfirmwaretexte"></div>
    <button id="avisfirmwarefermer" style="flex:0 0 auto" title="Fermer">✕</button></div>
</div>
<div class="ligne partiesmorceau" id="partiesmorceau" style="display:none"></div>
<div class="ligne anim">
  <label><span>Nombre de rayons</span> <select id="nbrayons"></select></label>
  <label><span>Canal MIDI de sortie</span> <select id="canal"></select></label>
</div>
<div class="ligne anim">
  <label><input type="checkbox" id="sactif"> <span id="slibelle">Changer le son du synthé à l’ouverture du morceau :</span></label>
  <select id="snum" title="Numérotation des sons du synthé">
    <option value="1-128">1 à 128</option><option value="0-127">0 à 127</option>
    <option value="groupes8">11 à 88</option></select>
  <label><span>programme</span> <input type="text" inputmode="numeric" id="sprog" placeholder="—" style="width:4.5em"></label><button class="vider" data-vide="sprog" title="Vider">✕</button>
  <label><span>banque MSB</span> <input type="number" id="smsb" min="0" max="127" placeholder="—"></label><button class="vider" data-vide="smsb" title="Vider">✕</button>
  <label><span>LSB</span> <input type="number" id="slsb" min="0" max="127" placeholder="—"></label><button class="vider" data-vide="slsb" title="Vider">✕</button>
  <span class="doux">(<span>décoché ou vide = le son du synthé n’est pas touché</span> ; <span id="splage"></span>)</span>
</div>
<div class="ligne anim">
  <label><input type="checkbox" id="animactive"> <span>Animation d'ouverture</span></label>
  <span>pause</span> <input type="number" id="animpause" min="0" max="30" step="0.5" value="2"> s,
  <span>ouverture</span> <input type="number" id="animduree" min="0" max="30" step="0.5" value="3"> s
  <label style="margin-left:12px"><input type="checkbox" id="animtrans"> <span>Transition animée entre les parties</span></label>
  <input type="number" id="animtransduree" min="0" max="10" step="0.25" value="1"> s
</div>
<div class="ligne tous"><span>Tous les rayons :</span>
  <button data-pas="-12">-12</button><button data-pas="-1">-1</button>
  <button data-pas="1">+1</button><button data-pas="12">+12</button>
  <button id="ranger" style="flex:0 0 auto">Ranger du grave à l’aigu</button>
</div>
<div id="bandeauappr" style="display:none;background:#1d4f8f;border:1px solid var(--actif);color:#fff;padding:12px 16px;border-radius:12px;margin-bottom:12px;font-weight:600">
  <div id="bandeautexte"></div>
  <div class="ligne" id="bandeauboutons" style="margin-top:8px">
    <button id="bgarder" style="flex:0 0 auto">✓ <span>Garder les changements</span></button>
    <button id="bannuler" class="danger" style="flex:0 0 auto">✕ <span>Annuler les changements</span></button></div>
</div>
<div id="avisgamme" class="doux" style="display:none;margin-bottom:10px"><span>Morceau Gamme : chaque rayon joue exactement la note envoyée par la harpe (gamme chromatique). Seul le nombre de rayons se règle.</span></div>
<div class="etat" id="etat"></div>
<div class="rayons" id="rayons"></div>
<footer><span>Coupez un rayon : il s'allume ici.</span></footer>
<script>
let noms = {}, etat = null;

async function lire() {
  const reponse = await fetch('/api/etat');
  appliquer(await reponse.json());
}

async function agir(chemin, corps) {
  const reponse = await fetch(chemin, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(corps || {})
  });
  const data = await reponse.json();
  if (data.erreur) { alert(T(data.erreur)); return; }
  appliquer(data);
}

function envoieAnimation() {
  agir('/api/animation', {
    active: document.getElementById('animactive').checked,
    pause: +document.getElementById('animpause').value,
    duree: +document.getElementById('animduree').value,
    transition: document.getElementById('animtrans').checked,
    trans_duree: +document.getElementById('animtransduree').value
  });
}
['animactive', 'animpause', 'animduree', 'animtrans', 'animtransduree'].forEach(id =>
  document.getElementById(id).onchange = envoieAnimation);
document.getElementById('ranger').onclick = e => { e.stopPropagation(); agir('/api/rayons/ranger'); };
document.querySelectorAll('.tous button[data-pas]').forEach(b =>
  b.onclick = () => agir('/api/rayons/decaler', {pas: +b.dataset.pas}));
document.getElementById('recharger').onclick = () => {
  if (!confirm(T('Remettre la harpe telle qu’elle est mémorisée dans ce morceau ?'))) return;
  agir('/api/memoire/recharger');
};
// Menus deroulants : un toucher ouvre, un autre (ou un choix, ou ailleurs) ferme.
document.querySelectorAll('.menu > .menubtn').forEach(b => b.onclick = e => {
  e.stopPropagation();
  const m = b.parentElement, ouvert = m.classList.contains('ouvert');
  document.querySelectorAll('.menu.ouvert').forEach(x => x.classList.remove('ouvert'));
  if (!ouvert) m.classList.add('ouvert');
});
document.addEventListener('click', () => document.querySelectorAll('.menu.ouvert').forEach(x => x.classList.remove('ouvert')));
// Morceau precedent / suivant, dans l'ordre de la liste choisie
function rangDansListe() {
  const l = (etat && etat.liste_morceaux) || [];
  return {l: l, i: l.indexOf(etat ? etat.index : -1)};
}
document.getElementById('precedent').onclick = () => {
  const {l, i} = rangDansListe();
  if (!l.length) return;
  const cible = i < 0 ? l[l.length - 1] : l[i - 1];
  if (cible !== undefined) agir('/api/memoire', {index: cible});
};
document.getElementById('suivant').onclick = () => {
  const {l, i} = rangDansListe();
  if (!l.length) return;
  const cible = i < 0 ? l[0] : l[i + 1];
  if (cible !== undefined) agir('/api/memoire', {index: cible});
};
// Liste precedente / suivante (la liste par defaut, puis les listes de morceaux)
function changerListe(pas) {
  if (!etat) return;
  const toutes = [null].concat(etat.listes || []);
  const i = Math.max(0, toutes.indexOf(etat.liste_active || null));
  const j = i + pas;
  if (j < 0 || j >= toutes.length) return;
  agir('/api/listes/activer', {nom: toutes[j]});
}
document.getElementById('listeprec').onclick = () => changerListe(-1);
document.getElementById('listesuiv').onclick = () => changerListe(1);
// Reexport de toutes les pistes de controle (un fichier .zip)
async function reexporterPistes() {
  const rep = await fetch('/api/controle/tout', {method: 'POST'});
  if ((rep.headers.get('Content-Type') || '').includes('json')) { const d = await rep.json(); alert(T(d.erreur || 'erreur')); return; }
  const a = document.createElement('a');
  a.href = URL.createObjectURL(await rep.blob());
  a.download = T('Pistes de contrôle') + '.zip';
  document.body.appendChild(a); a.click(); a.remove();
}
// Apres un changement de numeros : avertir, et proposer de reexporter
// Ligne des parties : toujours affichee (un morceau sans parties = partie A).
// ＋ ajoute une partie, － passe en mode suppression, on fait glisser une tuile
// pour la deplacer (les lettres suivent la position).
let pSuppr = null;        // null, ou ensemble des parties marquees pour suppression
function avertirPiste(r) {
  if (r && r.piste_a_refaire) alert(T('Les parties ont changé : la piste de contrôle déjà exportée pour ce morceau ne désigne plus les mêmes parties. Les commandes mémorisées ont suivi : réexportez la piste (Morceau > Modifier l’import > Exporter la piste de contrôle).'));
}
async function pPoster(chemin, corps) {
  const r = await (await fetch(chemin, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(corps || {})})).json();
  if (r.erreur) { alert(T(r.erreur)); return null; }
  appliquer(r); return r;
}
function dessinerParties(data, force) {
  const zp = document.getElementById('partiesmorceau');
  const noms = (data.parties && data.parties.length) ? data.parties : ['A'];
  const cle = JSON.stringify([noms, data.partie, data.gamme, pSuppr ? [...pSuppr] : null]);
  if (!force && zp.dataset.cle === cle) return;
  zp.dataset.cle = cle;
  zp.innerHTML = '';
  zp.style.display = data.gamme ? 'none' : '';
  zp.appendChild(document.createTextNode(T('Parties :') + ' '));
  noms.forEach((nom, i) => {
    const b = document.createElement('button');
    b.textContent = nom; b.className = 'ptuile' + (i === (data.partie || 0) ? ' actif' : '') + (pSuppr && pSuppr.has(i) ? ' asuppr' : '');
    if (pSuppr) {
      b.onclick = () => { pSuppr.has(i) ? pSuppr.delete(i) : pSuppr.add(i); dessinerParties(etat, true); };
    } else {
      b.onpointerdown = e => pGlisser(e, b, i, noms.length);
    }
    zp.appendChild(b);
  });
  if (pSuppr) {
    const ok = document.createElement('button'); ok.className = 'danger';
    ok.textContent = T('Supprimer') + ' (' + pSuppr.size + ')'; ok.disabled = !pSuppr.size;
    ok.onclick = async () => {
      const ind = [...pSuppr].sort((a, b) => a - b);
      if (ind.length >= noms.length) { alert(T('un morceau garde au moins une partie')); return; }
      if (!confirm(T('Supprimer les parties') + ' ' + ind.map(k => noms[k]).join(', ') + ' ? ' + T('Les lettres des parties suivantes changent.'))) return;
      pSuppr = null;
      avertirPiste(await pPoster('/api/partie/supprimer', {indices: ind}));
    };
    const non = document.createElement('button'); non.textContent = T('Annuler');
    non.onclick = () => { pSuppr = null; dessinerParties(etat, true); };
    zp.appendChild(ok); zp.appendChild(non);
    const aide = document.createElement('span'); aide.className = 'doux'; aide.textContent = ' ' + T('Touchez les parties à supprimer.');
    zp.appendChild(aide);
  } else {
    const plus = document.createElement('button'); plus.className = 'pplus'; plus.textContent = '＋'; plus.title = T('Ajouter une partie');
    plus.onclick = () => {
      const sel = document.getElementById('pcopie'); sel.innerHTML = '';
      noms.forEach((n, i) => sel.add(new Option(n, i)));
      sel.value = data.partie || 0;
      document.getElementById('dlgpartie').showModal();
    };
    zp.appendChild(plus);
    if (noms.length > 1) {
      const moins = document.createElement('button'); moins.className = 'pplus'; moins.textContent = '－'; moins.title = T('Supprimer des parties');
      moins.onclick = () => { pSuppr = new Set(); dessinerParties(etat, true); };
      zp.appendChild(moins);
    }
  }
}
// Toucher une tuile = changer de partie ; la faire glisser = la deplacer.
function pGlisser(e, tuile, de, n) {
  const x0 = e.clientX; let deplace = false, vers = de;
  const tuiles = () => [...document.querySelectorAll('#partiesmorceau .ptuile')];
  const bouger = ev => {
    if (!deplace && Math.abs(ev.clientX - x0) < 12) return;
    if (!deplace) { deplace = true; tuile.classList.add('glisse'); }
    const t = tuiles();
    // nouvelle place = nombre d'autres tuiles dont le milieu est a gauche du doigt
    let v = 0;
    t.forEach((b, k) => { const r = b.getBoundingClientRect(); if (k !== de && ev.clientX > r.left + r.width / 2) v++; });
    vers = Math.min(v, n - 1);
    tuile.style.transform = 'translateX(' + (ev.clientX - x0) + 'px)';
  };
  const fin = async () => {
    window.removeEventListener('pointermove', bouger);
    window.removeEventListener('pointerup', fin);
    window.removeEventListener('pointercancel', fin);
    tuile.classList.remove('glisse'); tuile.style.transform = '';
    if (!deplace) { agir('/api/partie', {index: de}); return; }
    if (vers !== de && etat && etat.parties && etat.parties.length) avertirPiste(await pPoster('/api/partie/deplacer', {de: de, vers: vers}));
    else dessinerParties(etat, true);
  };
  window.addEventListener('pointermove', bouger);
  window.addEventListener('pointerup', fin);
  window.addEventListener('pointercancel', fin);
}
document.getElementById('pannuler').onclick = () => document.getElementById('dlgpartie').close();
document.getElementById('pcreer').onclick = async () => {
  const copie = document.querySelector('input[name=ptype]:checked').value === 'copie';
  document.getElementById('dlgpartie').close();
  await pPoster('/api/partie/ajouter', copie ? {copie: +document.getElementById('pcopie').value} : {});
};
function avertirNumeros(numeros) {
  const touches = (numeros || []).filter(x => x.piste);
  if (!touches.length) return;
  const lignes = touches.map(x => '• ' + x.nom + ' : ' + x.avant + ' → ' + x.apres);
  if (confirm(T('Ces morceaux ont changé de numéro : leurs pistes de contrôle déjà exportées ne chargent plus le bon morceau.') +
      String.fromCharCode(10) + lignes.join(String.fromCharCode(10)) + String.fromCharCode(10) + String.fromCharCode(10) +
      T('Réexporter maintenant toutes les pistes de contrôle ?'))) reexporterPistes();
}
// Fenetre "Gerer les listes"
let lTravail = [], lNom = null;
function lCharger(nom) {
  lNom = nom;
  const noms = etat.memoires;
  lTravail = nom ? etat.liste_morceaux_de(nom) : noms.slice();
  lDessiner();
}
function lDessiner() {
  const sel = document.getElementById('lchoix');
  sel.innerHTML = '';
  sel.add(new Option(T('Tous les morceaux (liste par défaut)'), ''));
  (etat.listes || []).forEach(n => sel.add(new Option(n, n)));
  sel.value = lNom || '';
  const parDefaut = !lNom;
  document.getElementById('lexplication').textContent = parDefaut
    ? T('Leur ordre donne leur numéro, utilisé par les pistes de contrôle. Un nouveau morceau va toujours à la fin.')
    : T('L’ordre dans lequel ◀ ▶ passent les morceaux. Les numéros ne changent pas.');
  ['lligneajout', 'lrenommer', 'lsupprimer'].forEach(id => { document.getElementById(id).style.display = parDefaut ? 'none' : ''; });
  const z = document.getElementById('lmorceaux');
  z.innerHTML = '';
  lTravail.forEach((n, i) => {
    const l = document.createElement('div'); l.className = 'lm';
    const poignee = document.createElement('span'); poignee.className = 'poignee'; poignee.textContent = '☰';
    poignee.title = T('Faire glisser pour déplacer');
    poignee.onpointerdown = e => lGlisser(e, l);
    l.appendChild(poignee);
    const t = document.createElement('span');
    t.textContent = (parDefaut ? (i + 1) + '. ' : '') + n + (!parDefaut ? '  (n° ' + (etat.memoires.indexOf(n) + 1) + ')' : '');
    l.appendChild(t);
    const bouton = (texte, f) => { const b = document.createElement('button'); b.textContent = texte; b.onclick = f; l.appendChild(b); return b; };
    bouton('↑', () => { if (i > 0) { [lTravail[i - 1], lTravail[i]] = [lTravail[i], lTravail[i - 1]]; lDessiner(); } }).disabled = i === 0;
    bouton('↓', () => { if (i < lTravail.length - 1) { [lTravail[i + 1], lTravail[i]] = [lTravail[i], lTravail[i + 1]]; lDessiner(); } }).disabled = i === lTravail.length - 1;
    if (!parDefaut) bouton('✕', () => { lTravail.splice(i, 1); lDessiner(); });
    z.appendChild(l);
  });
  const aj = document.getElementById('lajout');
  aj.innerHTML = '';
  etat.memoires.forEach(n => aj.add(new Option(n, n)));
}
// Glisser-deposer d'un morceau dans "Gerer les listes" (doigt ou souris) : on tient la poignee,
// la ligne suit et prend sa place ; au lacher, la liste est renumerotee. Les fleches restent.
// (Ecoute sur window : deplacer la ligne dans la page ferait perdre la capture du pointeur.)
function lGlisser(e, ligne) {
  e.preventDefault();
  const z = document.getElementById('lmorceaux'), dlg = document.getElementById('dlglistes');
  ligne.classList.add('glisse');
  let pos = [...z.children].indexOf(ligne), y = e.clientY, defile = null;
  const placer = () => {
    const lignes = [...z.children];
    for (let k = 0; k < lignes.length; k++) {
      if (k === pos) continue;
      const r = lignes[k].getBoundingClientRect(), milieu = r.top + r.height / 2;
      if ((k < pos && y < milieu) || (k > pos && y > milieu)) {
        const [m] = lTravail.splice(pos, 1);
        lTravail.splice(k, 0, m);
        z.insertBefore(ligne, k < pos ? lignes[k] : lignes[k].nextSibling);
        pos = k;
        return;
      }
    }
  };
  // pres du bord haut ou bas de la fenetre : elle defile toute seule
  const bord = () => {
    const r = dlg.getBoundingClientRect();
    const v = y < r.top + 50 ? -12 : (y > r.bottom - 50 ? 12 : 0);
    if (v) { dlg.scrollTop += v; placer(); }
  };
  const bouger = ev => { y = ev.clientY; placer(); };
  const fin = () => {
    window.removeEventListener('pointermove', bouger);
    window.removeEventListener('pointerup', fin);
    window.removeEventListener('pointercancel', fin);
    clearInterval(defile);
    lDessiner();
  };
  window.addEventListener('pointermove', bouger);
  window.addEventListener('pointerup', fin);
  window.addEventListener('pointercancel', fin);
  defile = setInterval(bord, 30);
}
async function corbeilleDessiner() {
  const d = await (await fetch('/api/corbeille')).json();
  const z = document.getElementById('cliste');
  z.innerHTML = '';
  if (!(d.morceaux || []).length) z.innerHTML = '<p class="doux">' + T('La corbeille est vide.') + '</p>';
  (d.morceaux || []).forEach(m => {
    const l = document.createElement('div'); l.className = 'lm';
    const t = document.createElement('span'); t.textContent = m.nom + '  (' + T('supprimé le') + ' ' + m.date + ')';
    l.appendChild(t);
    const r = document.createElement('button'); r.textContent = T('Restaurer');
    r.onclick = async () => {
      const rep = await (await fetch('/api/corbeille/restaurer', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({fichier: m.fichier})})).json();
      if (rep.erreur) { alert(T(rep.erreur)); return; }
      appliquer(rep); corbeilleDessiner();
    };
    const x = document.createElement('button'); x.textContent = T('Supprimer définitivement');
    x.onclick = async () => {
      if (!confirm(T('Supprimer définitivement') + ' « ' + m.nom + ' » ? ' + T('Il ne pourra plus être récupéré.'))) return;
      await agir('/api/corbeille/supprimer', {fichier: m.fichier}); corbeilleDessiner();
    };
    l.appendChild(r); l.appendChild(x);
    z.appendChild(l);
  });
  document.getElementById('cvider').disabled = !(d.morceaux || []).length;
}
// Fenetre "Gerer les morceaux"
async function gmPoster(chemin, corps) {
  const r = await (await fetch(chemin, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(corps || {})})).json();
  if (r.erreur) { alert(T(r.erreur)); return null; }
  appliquer(r); return r;
}
function gmDessiner() {
  const z = document.getElementById('gmliste');
  z.innerHTML = '';
  (etat ? etat.memoires : []).forEach((nom, i) => {
    const l = document.createElement('div'); l.className = 'lm';
    const gamme = etat.index_gamme === i;
    const c = document.createElement('input'); c.type = 'checkbox'; c.value = nom;   // Gamme : cochable pour une liste
    const t = document.createElement('span'); t.textContent = (i + 1) + '. ' + nom + (i === etat.index ? '  ◀' : '');
    const bouton = (texte, f, titre) => { const b = document.createElement('button'); b.textContent = texte; if (titre) b.title = titre; b.onclick = f; return b; };
    l.appendChild(c); l.appendChild(t);
    l.appendChild(bouton(T('Ouvrir'), async () => { if (await gmPoster('/api/memoire', {index: i})) document.getElementById('dlgmorceaux').close(); }));
    if (!gamme) {
      l.appendChild(bouton(T('Renommer'), async () => {
        const n = prompt(T('Titre du morceau :'), nom);
        if (n && n.trim() && n.trim() !== nom && await gmPoster('/api/morceaux/renommer', {index: i, nom: n.trim()})) gmDessiner();
      }));
      l.appendChild(bouton('✕', () => gmSupprimer([nom]), T('Supprimer ce morceau')));
    }
    z.appendChild(l);
  });
}
async function gmSupprimer(noms) {
  const gamme = etat && etat.index_gamme !== null && etat.index_gamme !== undefined ? etat.memoires[etat.index_gamme] : null;
  if (gamme && noms.includes(gamme)) {
    noms = noms.filter(n => n !== gamme);
    if (!noms.length) { alert(T('le morceau Gamme ne peut pas être supprimé')); return; }
  }
  if (!noms.length) { alert(T('Cochez au moins un morceau.')); return; }
  if (noms.length >= (etat ? etat.memoires.length : 0)) { alert(T('Impossible de supprimer le dernier morceau.')); return; }
  if (!confirm(T('Supprimer') + ' : ' + noms.join(', ') + ' ? ' + T('(Ils vont dans la corbeille : menu Morceau, Corbeille…)'))) return;
  const r = await gmPoster('/api/morceaux/supprimer', {noms: noms});
  if (!r) return;
  gmDessiner();
  avertirNumeros(r.numeros);
}
const gmCoches = () => [...document.querySelectorAll('#gmliste input:checked')].map(c => c.value);
document.getElementById('gerermorceaux').onclick = () => { gmDessiner(); document.getElementById('dlgmorceaux').showModal(); };
document.getElementById('gmfermer').onclick = () => document.getElementById('dlgmorceaux').close();
document.getElementById('gmsupprimer').onclick = () => gmSupprimer(gmCoches());
document.getElementById('gmliste2').onclick = async () => {
  const noms = gmCoches();
  if (!noms.length) { alert(T('Cochez au moins un morceau.')); return; }
  const nom = prompt(T('Nom de la nouvelle liste :'));
  if (!nom || !nom.trim()) return;
  if (!await gmPoster('/api/listes/creer', {nom: nom.trim()})) return;
  if (!await gmPoster('/api/listes/morceaux', {nom: nom.trim(), morceaux: noms})) return;
  alert(T('Liste créée :') + ' ' + nom.trim() + ' (' + noms.length + ' ' + T('morceaux') + ')');
};
document.getElementById('gmnouveau').onclick = () => { document.getElementById('dlgmorceaux').close(); document.getElementById('nouveau').onclick(); };
document.getElementById('gmcorbeille').onclick = () => { document.getElementById('dlgmorceaux').close(); document.getElementById('mcorbeille').onclick(); };
document.getElementById('mcorbeille').onclick = () => { corbeilleDessiner(); document.getElementById('dlgcorbeille').showModal(); };
document.getElementById('cfermer').onclick = () => document.getElementById('dlgcorbeille').close();
document.getElementById('cvider').onclick = async () => {
  if (!confirm(T('Vider la corbeille ? Tous les morceaux qu’elle contient seront supprimés définitivement.'))) return;
  await agir('/api/corbeille/vider'); corbeilleDessiner();
};
// Fenetre "Accord" d'un rayon : accord tout fait (la note du rayon est la fondamentale) ou note par note.
const ACCORDS = [['majeur', 'majeur', [4, 7]], ['mineur', 'mineur', [3, 7]], ['7', '7e', [4, 7, 10]],
  ['maj7', '7e majeure', [4, 7, 11]], ['m7', 'mineur 7e', [3, 7, 10]], ['sus2', 'sus2', [2, 7]],
  ['sus4', 'sus4', [5, 7]], ['5', 'quinte seule', [7]], ['dim', 'diminué', [3, 6]], ['aug', 'augmenté', [4, 8]]];
let accordRayon = null;
function accordBeam() { return etat && etat.rayons.find(r => r.rayon === accordRayon); }
async function accordEnvoyer(notes) {
  await agir('/api/rayon/accord', {rayon: accordRayon, notes: notes});
  accordDessiner();
}
function accordDessiner() {
  const r = accordBeam();
  if (!r) return;
  const sup = r.notes_sup || [];
  document.getElementById('atitreaccord').textContent = T('Accord du rayon') + ' ' + r.rayon + ' : ' +
    [r.note].concat(sup).map(n => noms[n] || n).join(' + ');
  const z = document.getElementById('anotesaccord');
  z.innerHTML = '';
  const l0 = document.createElement('div'); l0.className = 'lm';
  l0.innerHTML = '<span>' + T('Note du rayon') + '</span><b>' + (noms[r.note] || r.note) + '</b>';
  z.appendChild(l0);
  sup.forEach((n, i) => {
    const l = document.createElement('div'); l.className = 'lm';
    const t = document.createElement('span'); t.textContent = T('Note') + ' ' + (i + 2);
    const sel = document.createElement('select');
    for (let k = 127; k >= 0; k--) sel.add(new Option(noms[k] || k, k));
    sel.value = n;
    sel.onchange = () => {
      fetch('/api/ecouter', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({note: +sel.value})});
      const nv = sup.slice(); nv[i] = +sel.value; accordEnvoyer(nv);
    };
    const x = document.createElement('button'); x.textContent = '✕';
    x.onclick = () => { const nv = sup.slice(); nv.splice(i, 1); accordEnvoyer(nv); };
    l.appendChild(t); l.appendChild(sel); l.appendChild(x); z.appendChild(l);
  });
  document.getElementById('asupprimeraccord').disabled = !sup.length;
}
function accordOuvrir(numero) {
  accordRayon = numero;
  const t = document.getElementById('atype');
  if (!t.options.length) ACCORDS.forEach(([cle, nom]) => t.add(new Option(T(nom), cle)));
  accordDessiner();
  document.getElementById('dlgaccord').showModal();
}
document.getElementById('aappliquer').onclick = () => {
  const r = accordBeam(), a = ACCORDS.find(x => x[0] === document.getElementById('atype').value);
  if (!r || !a) return;
  accordEnvoyer(a[2].map(i => r.note + i).filter(n => n <= 127));
};
document.getElementById('aajouternote').onclick = () => {
  const r = accordBeam(); if (!r) return;
  const sup = r.notes_sup || [], base = sup.length ? sup[sup.length - 1] : r.note;
  accordEnvoyer(sup.concat([Math.min(127, base + 4)]));
};
document.getElementById('asupprimeraccord').onclick = () => accordEnvoyer([]);
document.getElementById('afermeraccord').onclick = () => document.getElementById('dlgaccord').close();
document.getElementById('gererlistes').onclick = () => {
  lCharger(etat.liste_active || null);
  document.getElementById('dlglistes').showModal();
};
document.getElementById('lchoix').onchange = e => lCharger(e.target.value || null);
document.getElementById('lajouter').onclick = () => { lTravail.push(document.getElementById('lajout').value); lDessiner(); };
document.getElementById('lfermer').onclick = () => document.getElementById('dlglistes').close();
document.getElementById('lvalider').onclick = async () => {
  if (!lNom) {
    if (JSON.stringify(lTravail) === JSON.stringify(etat.memoires)) return;
    const r = await (await fetch('/api/listes/ordre', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({noms: lTravail})})).json();
    if (r.erreur) { alert(T(r.erreur)); return; }
    appliquer(r); lCharger(null);
    avertirNumeros(r.numeros);
  } else {
    await agir('/api/listes/morceaux', {nom: lNom, morceaux: lTravail});
    lCharger(lNom);
  }
  document.getElementById('lexplication').textContent = T('Enregistré.');
};
document.getElementById('lnouvelle').onclick = async () => {
  const nom = prompt(T('Nom de la nouvelle liste :'));
  if (!nom || !nom.trim()) return;
  await agir('/api/listes/creer', {nom: nom.trim()});
  lCharger(nom.trim());
};
document.getElementById('lrenommer').onclick = async () => {
  const nom = prompt(T('Nouveau nom de la liste :'), lNom);
  if (!nom || !nom.trim() || nom.trim() === lNom) return;
  await agir('/api/listes/renommer', {ancien: lNom, nouveau: nom.trim()});
  lCharger(nom.trim());
};
document.getElementById('lsupprimer').onclick = async () => {
  if (!confirm(T('Supprimer la liste') + ' « ' + lNom + ' » ? ' + T('Les morceaux, eux, restent.'))) return;
  await agir('/api/listes/supprimer', {nom: lNom});
  lCharger(null);
};
// Ouvrir... : la liste des morceaux
document.getElementById('mouvrir').onclick = () => {
  const z = document.getElementById('listeouvrir');
  z.innerHTML = '';
  (etat ? etat.memoires : []).forEach((nom, i) => {
    const b = document.createElement('button');
    b.textContent = (i + 1) + '. ' + nom;
    if (etat && i === etat.index) b.className = 'courant';
    b.onclick = () => { document.getElementById('dlgouvrir').close(); agir('/api/memoire', {index: i}); };
    z.appendChild(b);
  });
  document.getElementById('dlgouvrir').showModal();
};
document.getElementById('oannuler').onclick = () => document.getElementById('dlgouvrir').close();
// Reglages (application / harpe) affiches sous la barre, qui reste utilisable.
function ouvrirReglages(chemin, titre) {
  const v = document.getElementById('vuereglages');
  if (!v.src.endsWith(chemin)) v.src = chemin;
  document.getElementById('titrereglages').textContent = titre;
  v.style.display = ''; document.getElementById('barreglages').style.display = '';
  document.body.classList.add('enreglages');
}
function fermerReglages() {
  document.body.classList.remove('enreglages');
  document.getElementById('vuereglages').style.display = 'none';
  document.getElementById('barreglages').style.display = 'none';
}
document.getElementById('apropos').onclick = async () => {
  const a = await (await fetch('/api/apropos')).json();
  document.getElementById('aptitre').textContent = a.nom;
  document.getElementById('apversion').textContent = T('Version') + ' ' + a.version + ' (' + a.date + ')';
  document.getElementById('apappareil').textContent = a.pc ? T('Version PC') : T('Version Raspberry Pi');
  document.getElementById('apfirmware').textContent = a.firmware
    ? T('Firmware de la harpe :') + ' ' + a.firmware : T('Firmware de la harpe : pas encore lu (Paramètres de la harpe > Lire la harpe).');
  document.getElementById('dlgapropos').showModal();
};
document.getElementById('apfermer').onclick = () => document.getElementById('dlgapropos').close();
// Mettre a jour le Pi
async function mpPoster(chemin, corps) {
  const d = await (await fetch(chemin, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(corps || {})})).json();
  if (d.erreur) { document.getElementById('mpetat').textContent = T(d.erreur); return null; }
  return d;
}
async function mpOuvrir() {
  document.getElementById('mpusbliste').innerHTML = '';
  document.getElementById('mpetat').textContent = '';
  const e = await mpPoster('/api/maj/pi/etat');
  document.getElementById('mpversion').textContent = T('Version installée :') + ' ' + (e ? e.version : '?');
  const rv = document.getElementById('mprevenir');
  rv.style.display = e && e.precedente ? '' : 'none';
  if (e && e.precedente) rv.textContent = T('Revenir à la version précédente') + ' (' + e.precedente + ')';
  document.getElementById('dlgmajpi').showModal();
}
function mpInstallee(d) {
  if (!d) return;
  document.getElementById('mpetat').textContent = T('Version') + ' ' + d.version + ' ' +
    T('installée. Kromabridge redémarre : la page se recharge dans quelques secondes.');
  const essai = async () => {
    try { const r = await fetch('/api/etat', {cache: 'no-store'}); if (r.ok) { location.reload(); return; } } catch (x) {}
    setTimeout(essai, 2000);
  };
  setTimeout(essai, 8000);
}
document.getElementById('majpi').onclick = mpOuvrir;
document.getElementById('mpfermer').onclick = () => document.getElementById('dlgmajpi').close();
document.getElementById('mpchoisir').onclick = () => document.getElementById('mpfichier').click();
document.getElementById('mpfichier').onchange = async ev => {
  const f = ev.target.files[0]; ev.target.value = '';
  if (!f) return;
  if (!confirm(T('Installer la mise à jour') + ' « ' + f.name + ' » ?')) return;
  document.getElementById('mpetat').textContent = T('Installation en cours…');
  const b = new Uint8Array(await f.arrayBuffer());
  let bin = ''; for (let i = 0; i < b.length; i += 0x8000) bin += String.fromCharCode.apply(null, b.subarray(i, i + 0x8000));
  mpInstallee(await mpPoster('/api/maj/pi', {donnees: btoa(bin)}));
};
document.getElementById('mpusb').onclick = async () => {
  const z = document.getElementById('mpusbliste'); z.innerHTML = '';
  document.getElementById('mpetat').textContent = T('Lecture de la clé…');
  const d = await mpPoster('/api/maj/pi/usb/lister');
  if (!d) return;
  document.getElementById('mpetat').textContent = (d.fichiers || []).length ? '' : T('Aucun fichier kromabridge-pi….zip sur la clé.');
  (d.fichiers || []).forEach(f => {
    const b = document.createElement('button'); b.style.display = 'block'; b.style.width = '100%'; b.style.textAlign = 'left'; b.style.margin = '4px 0';
    b.textContent = f.fichier + '  (' + f.date + ')';
    b.onclick = async () => {
      if (!confirm(T('Installer la mise à jour') + ' « ' + f.fichier + ' » ?')) return;
      document.getElementById('mpetat').textContent = T('Installation en cours…');
      mpInstallee(await mpPoster('/api/maj/pi/usb', {fichier: f.fichier}));
    };
    z.appendChild(b);
  });
};
document.getElementById('mprevenir').onclick = async () => {
  if (!confirm(T('Revenir à la version précédente ?'))) return;
  mpInstallee(await mpPoster('/api/maj/pi/revenir'));
};
document.getElementById('vueparam').onclick = () => ouvrirReglages('/parametres?inclus=1', T('Paramètres de l’application'));
document.getElementById('vueharpe').onclick = () => ouvrirReglages('/harpe?inclus=1', T('Paramètres de la harpe'));
// Quitter les reglages : avertir s'il reste des reglages de harpe non memorises.
function quitterReglages() {
  if (!document.body.classList.contains('enreglages')) return true;
  const modifs = (etat && etat.harpe_modifiee) || [];
  if (modifs.length && !confirm(T('Attention : réglages de la harpe non mémorisés') + ' (' +
      ((etat.harpe_modifiee_noms || modifs).join(', ')) + '). ' + T('Quitter les réglages sans les mémoriser ?'))) return false;
  fermerReglages();
  return true;
}
document.getElementById('fermerreglages').onclick = () => quitterReglages();
// Pendant l'apprentissage ou l'attribution, le bouton pour terminer reste dans la barre.
document.getElementById('terminerappr').onclick = () => document.getElementById('apprendre').onclick();
document.getElementById('finattrib').onclick = () => agir('/api/attribution', {active: false});
(() => {
  const c = document.getElementById('canal');
  for (let i = 1; i <= 16; i++) c.add(new Option(i, i));
  c.onchange = () => agir('/api/canal', {canal: +c.value});
  document.querySelectorAll('button.vider').forEach(b => b.onclick = () => {
    const champ = document.getElementById(b.dataset.vide);
    if (champ.value === '') return;
    champ.value = '';
    champ.onchange();
  });
  document.getElementById('snum').onchange = () => {
    // le numero tape garde son sens : on le relit avec la nouvelle numerotation
    NUMEROTATION = document.getElementById('snum').value;
    document.getElementById('splage').textContent = progPlage();
    const champ = document.getElementById('sprog');
    if (progInterne(champ.value.trim()) === undefined) champ.value = '';
    champ.onchange();
  };
  ['sprog', 'smsb', 'slsb', 'sactif'].forEach(id => document.getElementById(id).onchange = () => {
    // taper un numero de programme coche la case : c'est qu'on veut ce son
    if (id === 'sprog' && document.getElementById('sprog').value !== '') document.getElementById('sactif').checked = true;
    // cocher la case sans numero de programme : elle reste cochee et on attend le numero
    if (id === 'sactif' && document.getElementById('sactif').checked && document.getElementById('sprog').value.trim() === '') {
      sAttente = cleSon();
      ['sprog', 'smsb', 'slsb'].forEach(i => { document.getElementById(i).style.opacity = ''; });
      document.getElementById('sprog').focus(); return;
    }
    sAttente = null;
    const v = id2 => document.getElementById(id2).value === '' ? null : +document.getElementById(id2).value;
    const prog = progInterne(document.getElementById('sprog').value.trim());
    if (prog === undefined) {
      alert(T('Ce numéro n’existe pas avec la numérotation choisie :') + ' ' + progPlage() + '.');
      document.getElementById('sprog').value = progAffiche((etat && etat.son_synthe || {}).programme);
      return;
    }
    agir('/api/son_synthe', {programme: prog, msb: v('smsb'), lsb: v('slsb'),
                             actif: document.getElementById('sactif').checked,
                             numerotation: document.getElementById('snum').value});
  });
  const r = document.getElementById('nbrayons');
  for (let i = 5; i <= 15; i++) r.add(new Option(i, i));
  r.onchange = () => agir('/api/rayons/nombre', {nombre: +r.value});
})();
let nouveauCopie = false;
// Case « Changer le son » cochee en attendant qu'on tape le numero (morceau + partie)
let sAttente = null;
const cleSon = () => etat ? etat.index + '/' + etat.partie : '';
document.getElementById('enregsous').onclick = () => { nouveauCopie = true; document.getElementById('nouveau').onclick(); };
document.getElementById('nouveau').onclick = () => {
  const copie = nouveauCopie;
  nouveauCopie = false;
  document.querySelector('#dlgnouveau h2').textContent = copie ? T('Enregistrer sous') : T('Nouveau morceau');
  const d = document.getElementById('dlgnouveau');
  const src = document.getElementById('nsource');
  src.innerHTML = '';
  src.add(new Option(T('Un morceau vierge'), 'vierge'));
  (etat ? etat.memoires : []).forEach((nom, i) => src.add(new Option(T('Une copie de : ') + nom, i)));
  src.value = (copie && etat) ? etat.index : 'vierge';
  const nb = document.getElementById('nrayons');
  if (!nb.options.length) for (let i = 5; i <= 15; i++) nb.add(new Option(i, i));
  nb.value = 9;
  const maj = () => { document.getElementById('nlignerayons').style.display = src.value === 'vierge' ? '' : 'none'; };
  src.onchange = maj; maj();
  document.getElementById('ntitre').value = '';
  d.showModal();
  document.getElementById('ntitre').focus();
};
document.getElementById('nannuler').onclick = () => document.getElementById('dlgnouveau').close();
document.getElementById('ncreer').onclick = () => {
  const nom = document.getElementById('ntitre').value.trim();
  if (!nom) { alert(T('Donnez un titre au morceau.')); return; }
  document.getElementById('dlgnouveau').close();
  agir('/api/morceau/nouveau', {nom: nom, source: document.getElementById('nsource').value,
                                rayons: +document.getElementById('nrayons').value});
};
// Son de la harpe joue par le navigateur (iPad...) : echantillons transposes.
const harpeSon = window.harpeSon = {pret: false, ctx: null, sons: [], voix: {}};
async function harpeActiver() {
  const AC = window.AudioContext || window.webkitAudioContext;
  const ctx = new AC();
  await ctx.resume();
  harpeSon.ctx = ctx;
  const liste = (await (await fetch('/api/sons')).json()).sons;
  harpeSon.sons = [];
  for (const s of liste) {
    const brut = await (await fetch('/sons/harpe/' + encodeURIComponent(s.fichier))).arrayBuffer();
    const tampon = await new Promise((ok, ko) => ctx.decodeAudioData(brut, ok, ko));
    harpeSon.sons.push({hauteur: s.hauteur, tampon: tampon});
  }
  harpeSon.pret = true;
  document.getElementById('activerson').style.display = 'none';
  const hp = document.getElementById('btnactiverson');
  hp.textContent = '🔊'; hp.classList.remove('eteint');
  const flux = new EventSource('/api/flux');
  flux.onmessage = e => { const ev = JSON.parse(e.data); if (ev.t === 'on') harpeJoue(ev.note, ev.vel); else harpeLache(ev.note); };
}
function harpeJoue(note, vel) {
  if (!harpeSon.pret || !harpeSon.sons.length) return;
  const ctx = harpeSon.ctx;
  let best = harpeSon.sons[0];
  harpeSon.sons.forEach(s => { if (Math.abs(s.hauteur - note) < Math.abs(best.hauteur - note)) best = s; });
  const src = ctx.createBufferSource();
  src.buffer = best.tampon;
  src.playbackRate.value = Math.pow(2, (note - best.hauteur) / 12);
  const g = ctx.createGain();
  g.gain.value = 0.45 * (vel || 100) / 127;
  src.connect(g); g.connect(ctx.destination);
  src.start();
  (harpeSon.voix[note] = harpeSon.voix[note] || []).push({src: src, g: g});
}
function harpeLache(note) {
  const ctx = harpeSon.ctx;
  (harpeSon.voix[note] || []).forEach(v => {
    v.g.gain.setTargetAtTime(0, ctx.currentTime, 0.1);
    v.src.stop(ctx.currentTime + 0.6);
  });
  harpeSon.voix[note] = [];
}
document.getElementById('btnactiverson').onclick = () => { if (!harpeSon.pret) harpeActiver().catch(e => alert(T('Son indisponible :') + ' ' + e)); };
let midiPistes = [], midiSel = null, midiDuree = 1, midiMinuterie = null, midiMesures = [], midiZoom = 1, midiTempsMes = [];
document.getElementById('importer').onclick = () => {
  document.getElementById('mfichier').value = '';
  document.getElementById('mtransp').value = '0';
  document.getElementById('mlignetransp').style.display = 'none';
  document.getElementById('mpistes').innerHTML = '';
  document.getElementById('mnotes').innerHTML = '';
  document.getElementById('mzoom').value = '1'; midiZoom = 1;
  document.getElementById('mligneselection').style.display = 'none';
  midiEdition = null;
  midiPistes = []; midiSel = null; midiParts = []; midiListeParties(); midiCree = null; midiCmds = []; midiCmdCurseur = null; midiCmdNouvelle = null; midiCmdDessine();
  midiDebut = 3; document.getElementById('mdebut').value = 3;
  midiEdition = null; midiNomFichier = '';
  const anim0 = (etat && etat.animation) || {};
  document.getElementById('manimouv').checked = !!anim0.active;
  document.getElementById('manimtrans').checked = !!anim0.transition;
  document.getElementById('manimpause').value = anim0.pause ?? 2;
  document.getElementById('manimduree').value = anim0.duree ?? 3;
  document.getElementById('manimtransduree').value = anim0.trans_duree ?? 1;
  document.getElementById('mlignerapide').style.display = 'none';
  document.getElementById('mligneanim').style.display = 'none';
  document.getElementById('mrapide').value = '';
  document.getElementById('mcreer').textContent = T('Créer le morceau');
  document.getElementById('mavis').textContent = T('Choisissez un fichier .mid.');
  document.getElementById('mlignetitre').style.display = 'none';
  document.getElementById('mcreer').disabled = true;
  document.getElementById('dlgmidi').showModal();
};
document.getElementById('mannuler').onclick = () => document.getElementById('dlgmidi').close();
document.getElementById('mtransp').onchange = () => {
  const coches = new Set([...document.querySelectorAll('#mnotes input:checked')].map(i => i.value));
  midiNotes();
  document.querySelectorAll('#mnotes input').forEach(i => { i.checked = coches.has(i.value); });
  midiCompte();
};
function midiTemps(s) {
  const m = Math.floor(s / 60), r = Math.floor(s % 60);
  return m + ':' + (r < 10 ? '0' : '') + r;
}
let midiNomFichier = '', midiEdition = null, midiMinuterieSauve = null;
document.getElementById('mfichier').onchange = async e => {
  const f = e.target.files[0];
  if (!f) return;
  const octets = new Uint8Array(await f.arrayBuffer());
  let bin = '';
  for (let i = 0; i < octets.length; i += 8192) bin += String.fromCharCode.apply(null, octets.subarray(i, i + 8192));
  const rep = await (await fetch('/api/midi/analyser', {method: 'POST',
    headers: {'Content-Type': 'application/json'}, body: JSON.stringify({donnees: btoa(bin)})})).json();
  if (rep.erreur) { document.getElementById('mavis').textContent = T(rep.erreur); return; }
  midiNomFichier = f.name;
  midiEdition = null;
  midiConstruire(rep);
  document.getElementById('mtitre').value = f.name.replace(/[.]midi?$/i, '');
  midiNotes();
};
function midiConstruire(rep) {
  midiPistes = rep.pistes;
  midiDuree = rep.duree || 1;
  midiMesures = rep.mesures || [];
  midiTempsMes = rep.temps_mesures || [];
  midiSel = null;
  document.getElementById('mlignerapide').style.display = '';
  document.getElementById('mligneanim').style.display = '';
  const zone = document.getElementById('mpistes');
  zone.innerHTML = '<p>' + T('Cochez la ou les pistes de la harpe :') + '</p>';
  midiPistes.forEach(p => {
    const bloc = document.createElement('div');
    bloc.className = 'mpiste';
    bloc.innerHTML = '<div class="mtete"><label><input type="checkbox" value="' + p.index + '"> ' + T('Piste') + ' ' + p.index +
      (p.nom ? ' - ' + p.nom.replace(/</g, '&lt;') : '') + ' <span class="doux">(' + p.distinctes.length + ' ' +
      T('notes différentes') + ', ' + (noms[p.grave] || p.grave) + ' ' + T('a') + ' ' + (noms[p.aigue] || p.aigue) +
      ', ' + T('canal') + ' ' + p.canaux.join('/') + ')</span></label>' +
      '<button class="mjouer" title="' + T('Écouter cette piste') + '">&#9654;</button></div>' +
      '<div class="mdefile"><div class="mvue" title="' + T('Touchez pour écouter à partir d’ici, glissez pour sélectionner une partie') + '">' +
      '<canvas></canvas><div class="msel"></div><div class="mcurseur"></div></div></div>';
    bloc.querySelector('input').onchange = () => { midiAfficheSel(); midiNotes(); midiCouleurs(); if (midiCree) midiCmdDessine(); };
    bloc.querySelector('.mjouer').onclick = () => midiEcouter([p.index], midiSel ? midiSel.d : p.premiere, midiSel ? midiSel.f : null);
    const vue = bloc.querySelector('.mvue');
    let depart = null;
    const temps = e => {
      const r = vue.getBoundingClientRect();
      return Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)) * midiDuree;
    };
    const defile = bloc.querySelector('.mdefile');
    defile.addEventListener('scroll', () => {
      document.querySelectorAll('#mpistes .mdefile').forEach(d => { if (d !== defile && d.scrollLeft !== defile.scrollLeft) d.scrollLeft = defile.scrollLeft; });
    });
    vue.addEventListener('pointerdown', e => { depart = {x: e.clientX, t: temps(e)}; vue.setPointerCapture(e.pointerId); });
    vue.addEventListener('pointermove', e => {
      if (!depart || Math.abs(e.clientX - depart.x) < 6) return;
      if (!bloc.querySelector('input').checked) {
        document.getElementById('mavis').textContent = T('Cochez d’abord cette piste pour y sélectionner une partie.');
        return;
      }
      const t = temps(e);
      midiSel = midiCale(Math.min(depart.t, t), Math.max(depart.t, t));
      midiAfficheSel();
    });
    vue.addEventListener('pointerup', e => {
      if (!depart) return;
      const glisse = Math.abs(e.clientX - depart.x) >= 6;
      const t0 = depart.t;
      depart = null;
      if (glisse && !bloc.querySelector('input').checked) return;
      if (glisse) { midiAfficheSel(); midiNotes(); }
      else midiEcouter([p.index], t0, null);
    });
    zone.appendChild(bloc);
    midiDessine(bloc.querySelector('canvas'), p);
  });
  midiAfficheSel();
}
// Ce qu'on memorise avec le morceau pour rouvrir l'import plus tard.
function midiEtatImport() {
  return {fichier_nom: midiNomFichier,
          pistes: midiCochees().map(p => p.index),
          transp: +document.getElementById('mtransp').value,
          parties: midiParts.map(p => ({nom: p.nom, deb: p.deb, fin: p.fin, notes: p.notes, info: p.info})),
          debut: midiDebut, commandes: midiCmds};
}
function midiSauver() {
  if (!midiEdition) return;
  clearTimeout(midiMinuterieSauve);
  midiMinuterieSauve = setTimeout(() => fetch('/api/midi/import/sauver', {method: 'POST',
    headers: {'Content-Type': 'application/json'}, body: JSON.stringify({morceau: midiEdition, import: midiEtatImport()})}), 600);
}
function midiBoutonCreer() {
  const b = document.getElementById('mcreer');
  const titre = document.getElementById('mtitre').value.trim();
  b.textContent = (midiEdition && titre === midiEdition) ? T('Mettre à jour le morceau') : T('Créer le morceau');
}
document.getElementById('mtitre').addEventListener('input', midiBoutonCreer);
document.getElementById('modifimport').onclick = async () => {
  document.getElementById('importer').onclick();
  const rep = await (await fetch('/api/midi/import/rouvrir', {method: 'POST',
    headers: {'Content-Type': 'application/json'}, body: JSON.stringify({morceau: etat.memoire})})).json();
  if (rep.erreur) { document.getElementById('mavis').textContent = T(rep.erreur); return; }
  const info = rep.import || {};
  midiNomFichier = info.fichier_nom || '';
  midiConstruire(rep);
  (info.pistes || []).forEach(i => { const c = document.querySelector('#mpistes input[value="' + i + '"]'); if (c) c.checked = true; });
  document.getElementById('mtransp').value = String(info.transp || 0);
  midiParts = (info.parties || []).map(p => Object.assign({}, p));
  midiListeParties();
  midiEdition = etat.memoire;
  document.getElementById('mtitre').value = etat.memoire;
  midiDebut = info.debut || 3;
  document.getElementById('mdebut').value = midiDebut;
  midiCree = {nom: etat.memoire, parts: midiParts.length ? midiParts.map(p => ({nom: p.nom, deb: p.deb, fin: p.fin}))
    : [{nom: '', deb: 1, fin: Math.max(1, midiMesures.length - 1)}]};
  midiCmds = (info.commandes || []).map(c => Object.assign({}, c));
  if (!midiCmds.length) midiCmdAuto();
  midiAfficheSel();
  midiNotes();
  midiCmdDessine();
  midiBoutonCreer();
  document.getElementById('mavis').textContent = T('Import rouvert : modifiez ce que vous voulez, puis « Mettre à jour le morceau » ou exportez la piste de contrôle.');
};
document.getElementById('mrapidebtn').onclick = () => {
  const texte = document.getElementById('mrapide').value;
  const plages = [...texte.matchAll(/([0-9]+)[ ]*-[ ]*([0-9]+)/g)].map(m => [+m[1], +m[2]]);
  if (!plages.length) { alert(T('Écrivez les parties sous la forme 1-5, 6-30, 31-38')); return; }
  if (!midiCochees().length) { alert(T('Cochez au moins une piste.')); return; }
  const t = +document.getElementById('mtransp').value;
  const refusees = [];
  plages.forEach(([a, b]) => {
    const i = Math.max(1, Math.min(a, b)), j = Math.min(Math.max(a, b), midiMesures.length - 1);
    const d = midiMesures[i - 1], f = midiMesures[j];
    const set = new Set();
    midiCochees().forEach(p => p.apercu.forEach(([s0, s1, n]) => { if (s0 >= d && s0 < f) set.add(n + t); }));
    const notes = [...set].sort((x, y) => x - y);
    if (!notes.length || notes.length > 15) { refusees.push(i + '-' + j + ' (' + notes.length + ' ' + T('notes') + ')'); return; }
    midiParts.push({nom: String.fromCharCode(65 + midiParts.length), notes: notes,
                    info: T('mes.') + ' ' + i + '-' + j + ', ' + notes.length + ' ' + T('rayons'), deb: i, fin: j});
  });
  midiListeParties();
  midiCompte();
  document.getElementById('mavis').textContent = refusees.length
    ? T('Parties non créées (aucune note ou plus de 15) :') + ' ' + refusees.join(', ') + '. ' +
      T('Sélectionnez-les à la main pour décocher des notes.')
    : T('Parties créées. Vérifiez-les, puis « Créer le morceau ».');
};
function midiDessine(c, p) {
  const w = c.clientWidth || 600, h = 48, r = Math.min(window.devicePixelRatio || 1, 8000 / w);
  c.width = w * r; c.height = h * r;
  const g = c.getContext('2d');
  g.scale(r, r);
  // barres de mesure, et leur numero toutes les `pas` mesures
  const largeurMesure = midiMesures.length > 1 ? (midiMesures[1] - midiMesures[0]) / midiDuree * w : w;
  const pas = [1, 2, 4, 5, 10, 20, 50].find(k => k * largeurMesure >= 28) || 100;
  g.font = '10px system-ui, sans-serif';
  midiMesures.forEach((t, i) => {
    const x = t / midiDuree * w;
    g.fillStyle = (i % pas === 0) ? 'rgba(255,255,255,.22)' : 'rgba(255,255,255,.07)';
    g.fillRect(x, 0, 1, h);
    if (i % pas === 0) { g.fillStyle = 'rgba(255,255,255,.45)'; g.fillText(String(i + 1), x + 2, 10); }
  });
  g.fillStyle = '#4ea1ff';
  const bas = p.grave, haut = Math.max(p.aigue, p.grave + 1);
  p.apercu.forEach(([d, f, n]) => {
    const x = d / midiDuree * w, l = Math.max(1, (f - d) / midiDuree * w);
    const y = h - 4 - (n - bas) / (haut - bas) * (h - 8);
    g.fillRect(x, y - 1.5, l, 3);
  });
}
function midiCochees() {
  const choisies = [...document.querySelectorAll('#mpistes input[type=checkbox]:checked')].map(i => +i.value);
  return midiPistes.filter(p => choisies.includes(p.index));
}
// Calage sur les mesures : debut au debut de sa mesure, fin a la fin de la sienne.
function midiCale(d, f) {
  if (!document.getElementById('mcaler').checked || midiMesures.length < 2) return {d: d, f: f};
  let i = 0, j = 0;
  midiMesures.forEach((t, k) => { if (t <= d + 0.001) i = k; });
  for (j = 0; j < midiMesures.length && midiMesures[j] < f - 0.001; j++) ;
  j = Math.min(Math.max(j, i + 1), midiMesures.length - 1);
  return {d: midiMesures[i], f: midiMesures[j]};
}
function midiMesureDe(t) {
  let i = 0;
  midiMesures.forEach((x, k) => { if (x <= t + 0.001) i = k; });
  return i + 1;
}
function midiDepuisChamps() {
  const a = +document.getElementById('mdeb').value, b = +document.getElementById('mfin').value;
  if (!a || !b || midiMesures.length < 2) return;
  const i = Math.max(1, Math.min(a, b)), j = Math.min(Math.max(a, b), midiMesures.length - 1);
  midiSel = {d: midiMesures[i - 1], f: midiMesures[j]};
  midiAfficheSel(); midiNotes();
}
document.getElementById('mdeb').onchange = midiDepuisChamps;
document.getElementById('mfin').onchange = midiDepuisChamps;
document.getElementById('mzoom').onchange = () => {
  midiZoom = +document.getElementById('mzoom').value;
  document.querySelectorAll('.mpiste .mvue').forEach(v => { v.style.width = (midiZoom * 100) + '%'; });
  document.querySelectorAll('#mpistes .mpiste canvas').forEach((c, i) => midiDessine(c, midiPistes[i]));
  if (midiCree) midiCmdDessine();
  if (midiSel) {
    const d = document.querySelector('.mdefile');
    if (d) d.scrollLeft = midiSel.d / midiDuree * d.scrollWidth - 20;
  }
};
function midiAfficheSel() {
  document.querySelectorAll('.mpiste .msel').forEach((s, i) => {
    const cochee = document.querySelectorAll('.mpiste input[type=checkbox]')[i].checked;
    s.style.display = (midiSel && cochee) ? 'block' : 'none';
    if (midiSel) {
      s.style.left = (midiSel.d / midiDuree * 100) + '%';
      s.style.width = ((midiSel.f - midiSel.d) / midiDuree * 100) + '%';
    }
  });
  document.getElementById('mligneselection').style.display = midiPistes.length ? '' : 'none';
  document.getElementById('mjouersel').disabled = !midiSel;
  document.getElementById('msanssel').disabled = !midiSel;
  document.getElementById('mselinfo').textContent = midiSel
    ? '(' + midiTemps(midiSel.d) + ' - ' + midiTemps(midiSel.f) + ')' : T('(tout le fichier)');
  if (midiSel && midiMesures.length > 1) {
    document.getElementById('mdeb').value = midiMesureDe(midiSel.d);
    document.getElementById('mfin').value = Math.max(midiMesureDe(midiSel.d), midiMesureDe(midiSel.f - 0.01));
  } else if (!midiSel) {
    document.getElementById('mdeb').value = ''; document.getElementById('mfin').value = '';
  }
}
document.getElementById('msanssel').onclick = () => { midiSel = null; midiAfficheSel(); midiNotes(); };
document.getElementById('mjouersel').onclick = () => {
  const p = midiCochees().map(x => x.index);
  if (!p.length) { alert(T('Cochez au moins une piste.')); return; }
  midiEcouter(p, midiSel.d, midiSel.f);
};
async function midiEcouter(pistes, debut, fin) {
  const rep = await (await fetch('/api/midi/ecouter', {method: 'POST',
    headers: {'Content-Type': 'application/json'}, body: JSON.stringify({pistes: pistes, debut: debut, fin: fin})})).json();
  if (rep.erreur) { alert(T(rep.erreur)); return; }
  if (!midiMinuterie) midiMinuterie = setInterval(midiCurseur, 150);
}
async function midiCurseur() {
  let d;
  try { d = await (await fetch('/api/midi/position')).json(); } catch (e) { return; }
  document.querySelectorAll('#mpistes .mpiste').forEach((b, i) => {
    const cur = b.querySelector('.mcurseur');
    const joue = d.position !== null && d.pistes.includes(midiPistes[i].index);
    cur.style.display = joue ? 'block' : 'none';
    if (joue) cur.style.left = Math.min(100, d.position / midiDuree * 100) + '%';
  });
  if (d.position === null) { clearInterval(midiMinuterie); midiMinuterie = null; }
}
function midiArreter() {
  fetch('/api/midi/arreter', {method: 'POST'});
}
document.getElementById('marreter').onclick = midiArreter;
document.getElementById('dlgmidi').addEventListener('close', midiArreter);
function midiNotes() {
  const set = new Set();
  midiCochees().forEach(p => {
    if (midiSel) p.apercu.forEach(([d, f, n]) => { if (d >= midiSel.d && d < midiSel.f) set.add(n); });
    else p.distinctes.forEach(n => set.add(n));
  });
  const notes = [...set].sort((a, b) => a - b);
  const zone = document.getElementById('mnotes');
  zone.innerHTML = notes.length ? '<p>' + T('Notes des rayons (de gauche à droite) :') + '</p>' : '';
  notes.forEach(n => {
    const l = document.createElement('label');
    const t = +document.getElementById('mtransp').value;
    l.innerHTML = '<input type="checkbox" value="' + n + '"' + (notes.length <= 15 ? ' checked' : '') + '> ' + (noms[n + t] || (n + t));
    l.querySelector('input').onchange = midiCompte;
    zone.appendChild(l);
  });
  document.getElementById('mlignetitre').style.display = notes.length ? '' : 'none';
  document.getElementById('mlignetransp').style.display = notes.length ? '' : 'none';
  midiCompte();
}
function midiCompte() {
  const n = document.querySelectorAll('#mnotes input:checked').length;
  const total = document.querySelectorAll('#mnotes input').length;
  const avis = document.getElementById('mavis');
  const ok = n >= 1 && n <= 15;
  document.getElementById('mcreer').disabled = !ok && !midiParts.length;
  document.getElementById('majouter').disabled = !ok;
  if (!total) {
    avis.textContent = !midiPistes.length ? T('Aucune note dans ce fichier.')
      : (midiCochees().length ? T('Aucune note dans la sélection.') : T('Cochez au moins une piste.'));
    return;
  }
  avis.textContent = n + ' ' + T('rayons') + (n > 15 ? ' : ' + T('15 au plus, décochez des notes.') : n < 5 ? ' : ' +
    T('la harpe en a au moins 5, les rayons en trop rejoueront la note la plus aiguë.') : '');
}
function midiTitreSuivant(nom) {
  const m = nom.match(/^(.*) ([A-Y])$/);
  if (m) return m[1] + ' ' + String.fromCharCode(m[2].charCodeAt(0) + 1);
  return nom + ' B';
}
let midiParts = [], midiCree = null, midiCmds = [], midiDebut = 3;
const CMD_NOMS = {morceau: 'Chargement du morceau', ouvrir: 'Ouverture', fermer: 'Fermeture', partie: 'Partie'};
function midiCmdAuto() {
  if (!midiCree) return;
  const p = midiCree.parts;
  const avecParties = p.length > 1 || (p[0] && p[0].nom);
  // Chargement mesure 1 (il remet le morceau sur sa partie A), ouverture
  // mesure 2 ; chaque changement de partie suivant une demi-mesure avant le
  // debut de la partie si la transition animee est cochee.
  const transition = document.getElementById('manimtrans').checked;
  const dec = midiDebut - 1;      // mesures ajoutees avant le morceau dans Cubase
  midiCmds = [{type: 'morceau', mesure: 1, temps: 1},
              {type: 'ouvrir', mesure: Math.max(dec > 0 ? 2 : 1, midiDebut - 1), temps: 1}];
  if (avecParties) p.forEach((x, k) => {
    if (k === 0) return;
    const m = x.deb + dec;
    midiCmds.push(transition && m > 1 ? {type: 'partie', partie: k, mesure: m - 1, temps: 1 + (midiCmdBarres().n[m - 2] || 4) / 2}
                                      : {type: 'partie', partie: k, mesure: m, temps: 1});
  });
  midiCmds.push({type: 'fermer', mesure: p[p.length - 1].fin + 1 + dec, temps: 1});
  midiCmdDessine();
}
// L'apercu des commandes est en mesures du projet Cubase : les mesures
// ajoutees avant le morceau, le morceau, puis 3 mesures apres sa fin.
let midiCmdCurseur = null, midiCmdX = [], midiCmdGlisse = null, midiCmdNouvelle = null;
function midiCmdBarres() {
  const m = midiMesures.length > 1 ? midiMesures : [0, 2];
  const l0 = m[1] - m[0], ln = m[m.length - 1] - m[m.length - 2];
  const dec = Math.max(0, midiDebut - 1), avant = dec * l0;
  const b = [], n = [];
  const n0 = midiTempsMes[0] || 4, nf = midiTempsMes[midiTempsMes.length - 1] || 4;
  for (let i = 0; i < dec; i++) { b.push(i * l0); n.push(n0); }
  m.forEach((t, i) => { b.push(avant + t); if (i < m.length - 1) n.push(midiTempsMes[i] || 4); });
  for (let i = 0; i < 3; i++) { b.push(b[b.length - 1] + ln); n.push(nf); }
  return {b: b, n: n, avant: avant, total: b[b.length - 1]};
}
// Une couleur par piste cochee quand il y en a plusieurs (aussi sur leur nom en haut).
const MIDI_TEINTES = ['#4ea1ff', '#ff7ab6', '#7ee081', '#c39bff', '#ffd166', '#5ce1e6', '#ff9f68', '#b8c0d0'];
function midiCouleurs() {
  const c = midiCochees(), t = {};
  if (c.length > 1) c.forEach((p, i) => { t[p.index] = MIDI_TEINTES[i % MIDI_TEINTES.length]; });
  document.querySelectorAll('#mpistes .mpiste').forEach((b, i) => {
    const nom = b.querySelector('.mtete label');
    const p = midiPistes[i];
    if (nom && p) nom.style.color = t[p.index] || '';
  });
  return t;
}
function midiCmdTemps(c, B) {
  B = B || midiCmdBarres();
  const b = B.b, i = Math.max(0, Math.min(b.length - 2, c.mesure - 1));
  return b[i] + (b[i + 1] - b[i]) * Math.min(1, ((c.temps || 1) - 1) / (B.n[i] || 4));
}
// secondes sur l'apercu -> mesure et temps, au demi-temps pres
function midiCmdPosition(t, B) {
  const b = B.b;
  let i = 0;
  for (let k = 0; k < b.length - 1; k++) if (b[k] <= t + 0.0001) i = k;
  const nb = B.n[i] || 4;
  let temps = 1 + Math.round((t - b[i]) / (b[i + 1] - b[i]) * nb * 2) / 2, mesure = i + 1;
  if (temps >= nb + 1) { temps = 1; mesure += 1; }
  return {mesure: mesure, temps: Math.max(1, temps)};
}
function midiCmdDessine() {
  midiSauver();
  const bloc = document.getElementById('mcommandes');
  bloc.style.display = midiCree ? '' : 'none';
  if (!midiCree) return;
  document.getElementById('mcmdexport').disabled = !midiCmds.length;
  const liste = document.getElementById('mcmdliste');
  liste.innerHTML = '';
  const parts = midiCree.parts.filter(x => x.nom);
  // un temps qui depasse la mesure (temps 3 d'une mesure a 2/4) passe a la mesure suivante,
  // comme dans la piste exportee
  const Bn = midiCmdBarres().n;
  midiCmds.forEach(c => {
    let n = Bn[c.mesure - 1] || 4;
    while (c.temps >= n + 1 && c.mesure < Bn.length) { c.temps -= n; c.mesure += 1; n = Bn[c.mesure - 1] || 4; }
  });
  midiCmds.sort((a, b) => a.mesure - b.mesure || a.temps - b.temps);
  midiCmds.forEach((c, i) => {
    const l = document.createElement('div');
    l.className = 'lignecmd' + (c === midiCmdNouvelle ? ' nouvelle' : '');
    const sel = document.createElement('select');
    sel.add(new Option(T('Chargement du morceau'), 'morceau'));
    sel.add(new Option(T('Ouverture'), 'ouvrir'));
    sel.add(new Option(T('Fermeture'), 'fermer'));
    parts.forEach((x, k) => sel.add(new Option(T('Partie') + ' ' + x.nom, 'partie' + k)));
    sel.value = c.type === 'partie' ? 'partie' + c.partie : c.type;
    sel.onchange = () => {
      if (sel.value.startsWith('partie')) { c.type = 'partie'; c.partie = +sel.value.slice(6); }
      else { c.type = sel.value; delete c.partie; }
      midiCmdDessine();
    };
    const mes = document.createElement('input'); mes.type = 'number'; mes.min = 1; mes.value = c.mesure;
    mes.onchange = () => { c.mesure = Math.max(1, +mes.value || 1); midiCmdDessine(); };
    const tps = document.createElement('input'); tps.type = 'number'; tps.min = 1; tps.max = 16; tps.step = 0.5; tps.value = c.temps;
    tps.onchange = () => { c.temps = Math.max(1, +tps.value || 1); midiCmdDessine(); };
    const sup = document.createElement('button'); sup.textContent = '✕';
    sup.onclick = () => { midiCmds.splice(i, 1); midiCmdDessine(); };
    const e1 = document.createElement('span'); e1.textContent = T('mesure');
    const e2 = document.createElement('span'); e2.textContent = T('temps');
    [sel, e1, mes, e2, tps, sup].forEach(x => l.appendChild(x));
    liste.appendChild(l);
  });
  // l'apercu : les notes du morceau et les commandes, en mesures du projet
  const vue = document.getElementById('mvuecmd');
  vue.style.width = (midiZoom * 100) + '%';
  const B = midiCmdBarres();
  const c = document.getElementById('mcmdcanvas');
  const w = c.clientWidth || 600, h = 110, r = Math.min(window.devicePixelRatio || 1, 8000 / w);
  c.width = w * r; c.height = h * r;
  const g = c.getContext('2d'); g.scale(r, r);
  g.font = '10px system-ui, sans-serif';
  const X = t => t / B.total * w;
  g.fillStyle = 'rgba(0,0,0,.35)';
  g.fillRect(0, 0, X(B.avant), h);
  g.fillRect(X(B.avant + midiDuree), 0, w - X(B.avant + midiDuree), h);
  const lm = (B.b[1] - B.b[0]) / B.total * w;
  const pas = [1, 2, 4, 5, 10, 20, 50].find(k => k * lm >= 28) || 100;
  B.b.slice(0, -1).forEach((t, i) => {
    g.fillStyle = (i % pas === 0) ? 'rgba(255,255,255,.22)' : 'rgba(255,255,255,.07)';
    g.fillRect(X(t), 0, 1, h);
    if (i % pas === 0) { g.fillStyle = 'rgba(255,255,255,.45)'; g.fillText(String(i + 1), X(t) + 2, 10); }
  });
  const pistes = midiCochees(), teintes = midiCouleurs();
  let bas = 127, haut = 0;
  pistes.forEach(p => { bas = Math.min(bas, p.grave); haut = Math.max(haut, p.aigue); });
  haut = Math.max(haut, bas + 1);
  pistes.forEach(p => {
    g.fillStyle = teintes[p.index] || 'rgba(78,161,255,.75)';
    p.apercu.forEach(([d, f, n]) => {
      const y = h - 6 - (n - bas) / (haut - bas) * (h - 52);
      g.fillRect(X(B.avant + d), y - 1.5, Math.max(1, (f - d) / B.total * w), 3);
    });
  });
  if (midiCmdCurseur) {
    const x = X(midiCmdTemps(midiCmdCurseur, B));
    g.fillStyle = '#ffffff';
    g.fillRect(x - 0.5, 0, 1.5, h);
    g.beginPath(); g.moveTo(x - 5, 0); g.lineTo(x + 5, 0); g.lineTo(x, 7); g.fill();
  }
  const couleurs = {morceau: '#9aa3b8', ouvrir: '#2ecc71', fermer: '#ff5c5c', partie: '#ffb454'};
  midiCmdX = [];
  midiCmds.forEach((cmd, k) => {
    const x = X(midiCmdTemps(cmd, B));
    midiCmdX.push(x);
    if (cmd === midiCmdNouvelle) { g.fillStyle = 'rgba(255,255,255,.18)'; g.fillRect(x - 6, 0, 13, h); }
    g.fillStyle = couleurs[cmd.type] || '#fff';
    g.fillRect(x - 1, 0, 3, h);
    const lib = cmd.type === 'partie' ? (parts[cmd.partie] ? parts[cmd.partie].nom : '?')
      : cmd.type === 'morceau' ? 'PC' : (cmd.type === 'ouvrir' ? T('Ouv.') : T('Ferm.'));
    g.fillText(lib, x + 4, 22 + (k % 3) * 11);
  });
}
// clic : place le curseur (sans lancer l'ecoute) ; glisser un repere : le deplace
(function () {
  const vue = document.getElementById('mvuecmd');
  const lit = e => {
    const r = vue.getBoundingClientRect(), B = midiCmdBarres();
    const x = e.clientX - r.left;
    return {x: x, pos: midiCmdPosition(Math.max(0, Math.min(1, x / r.width)) * B.total, B)};
  };
  const proche = x => {
    let k = -1, mieux = 8;
    midiCmdX.forEach((cx, i) => { const d = Math.abs(cx - x); if (d <= mieux) { mieux = d; k = i; } });
    return k;
  };
  vue.addEventListener('pointerdown', e => {
    if (!midiCree) return;
    const l = lit(e), k = proche(l.x);
    if (k >= 0) { midiCmdGlisse = midiCmds[k]; vue.setPointerCapture(e.pointerId); e.preventDefault(); }
    else { midiCmdCurseur = l.pos; midiCmdDessine(); }
  });
  vue.addEventListener('pointermove', e => {
    const l = lit(e);
    if (!midiCmdGlisse) { vue.style.cursor = proche(l.x) >= 0 ? 'ew-resize' : 'crosshair'; return; }
    if (midiCmdGlisse.mesure === l.pos.mesure && midiCmdGlisse.temps === l.pos.temps) return;
    midiCmdGlisse.mesure = l.pos.mesure; midiCmdGlisse.temps = l.pos.temps;
    midiCmdDessine();
  });
  const fin = () => { if (midiCmdGlisse) { midiCmdGlisse = null; midiCmdDessine(); } };
  vue.addEventListener('pointerup', fin);
  vue.addEventListener('pointercancel', fin);
})();
document.getElementById('mcmdajout').onclick = () => {
  const pos = midiCmdCurseur || {mesure: midiCmds.length ? midiCmds[midiCmds.length - 1].mesure : 1, temps: 1};
  midiCmdNouvelle = {type: 'ouvrir', mesure: pos.mesure, temps: pos.temps};
  midiCmds.push(midiCmdNouvelle);
  midiCmdDessine();
  const ligne = document.querySelector('#mcmdliste .nouvelle');
  if (ligne) ligne.scrollIntoView({block: 'nearest'});
};
document.getElementById('mcmdauto').onclick = () => midiCmdAuto();
function midiCasesAnimation() {
  return {active: document.getElementById('manimouv').checked,
          transition: document.getElementById('manimtrans').checked,
          pause: +document.getElementById('manimpause').value,
          duree: +document.getElementById('manimduree').value,
          trans_duree: +document.getElementById('manimtransduree').value};
}
['manimpause', 'manimduree', 'manimtransduree'].forEach(id => document.getElementById(id).onchange = () => {
  if (midiEdition) fetch('/api/midi/animation', {method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(Object.assign({morceau: midiEdition}, midiCasesAnimation()))});
});
document.getElementById('manimouv').onchange = () => {
  if (midiEdition) fetch('/api/midi/animation', {method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({morceau: midiEdition, active: document.getElementById('manimouv').checked})});
};
document.getElementById('manimtrans').onchange = () => {
  const on = document.getElementById('manimtrans').checked;
  // Les changements de partie avancent (ou reculent) d'une demi-mesure.
  midiCmds.forEach(c => {
    if (c.type !== 'partie') return;
    let temps = c.temps + (on ? -2 : 2), mesure = c.mesure;
    if (temps < 1) { temps += 4; mesure -= 1; }
    if (temps > 4) { temps -= 4; mesure += 1; }
    if (mesure >= 1) { c.mesure = mesure; c.temps = temps; }
  });
  midiCmdDessine();
  if (midiEdition) fetch('/api/midi/animation', {method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({morceau: midiEdition, transition: on})});
};
document.getElementById('mdebut').onchange = () => {
  const nouveau = Math.max(1, +document.getElementById('mdebut').value || 1);
  const delta = nouveau - midiDebut;
  midiDebut = nouveau;
  // Tout suit, sauf le chargement qui reste en mesure 1 ; tes retouches sont gardees.
  midiCmds.forEach(c => { if (c.type !== 'morceau') c.mesure = Math.max(1, c.mesure + delta); });
  midiCmdDessine();
};
document.getElementById('mcmdexport').onclick = async () => {
  const rep = await fetch('/api/midi/commandes', {method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({morceau: midiCree.nom, commandes: midiCmds, debut: midiDebut})});
  if (!rep.ok || (rep.headers.get('Content-Type') || '').includes('json')) {
    const d = await rep.json(); alert(T(d.erreur || 'erreur')); return;
  }
  const blob = await rep.blob();
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'Controle - ' + midiCree.nom + '.mid';
  document.body.appendChild(a); a.click(); a.remove();
  document.getElementById('mcmdavis').textContent = T('Piste de contrôle exportée : glissez-la dans votre DAW, au début du projet, à côté du morceau.');
};
function midiNotesChoisies() {
  const t = +document.getElementById('mtransp').value;
  const notes = [...document.querySelectorAll('#mnotes input:checked')].map(i => +i.value + t);
  if (notes.some(n => n < 0 || n > 127)) { alert(T('Transposition hors de l’étendue MIDI.')); return null; }
  return notes;
}
function midiListeParties() {
  const z = document.getElementById('mlisteparties');
  z.innerHTML = '';
  if (!midiParts.length) return;
  z.appendChild(document.createTextNode(T('Parties du morceau :') + ' '));
  midiParts.forEach((p, i) => {
    const b = document.createElement('button');
    b.textContent = p.nom + ' (' + p.info + ') ✕';
    b.title = T('Retirer cette partie');
    b.onclick = () => {
      midiParts.splice(i, 1);
      midiParts.forEach((x, k) => { x.nom = String.fromCharCode(65 + k); });
      midiListeParties(); midiCompte(); midiSauver();
    };
    z.appendChild(b);
  });
}
document.getElementById('majouter').onclick = () => {
  const notes = midiNotesChoisies();
  if (!notes) return;
  const info = (midiSel ? T('mes.') + ' ' + document.getElementById('mdeb').value + '-' + document.getElementById('mfin').value
    : T('tout')) + ', ' + notes.length + ' ' + T('rayons');
  const derniere = Math.max(1, midiMesures.length - 1);
  midiParts.push({nom: String.fromCharCode(65 + midiParts.length), notes: notes, info: info,
                  deb: midiSel ? +document.getElementById('mdeb').value : 1,
                  fin: midiSel ? +document.getElementById('mfin').value : derniere});
  midiListeParties();
  midiCompte();
  document.getElementById('mavis').textContent = T('Partie ajoutée. Sélectionnez la suivante, ou cliquez sur Créer le morceau.');
};
document.getElementById('mcreer').onclick = async () => {
  const nom = document.getElementById('mtitre').value.trim();
  if (!nom) { alert(T('Donnez un titre au morceau.')); return; }
  const miseAJour = midiEdition && nom === midiEdition;
  let corps;
  if (midiParts.length) corps = {nom: nom, parties: midiParts.map(p => ({nom: p.nom, notes: p.notes}))};
  else {
    const notes = midiNotesChoisies();
    if (!notes) return;
    corps = {nom: nom, notes: notes};
  }
  const avant = midiCree;
  midiCree = {nom: nom, parts: midiParts.length ? midiParts.map(p => ({nom: p.nom, deb: p.deb, fin: p.fin}))
    : [{nom: '', deb: midiSel ? +document.getElementById('mdeb').value : 1,
        fin: midiSel ? +document.getElementById('mfin').value : Math.max(1, midiMesures.length - 1)}]};
  if (!miseAJour || !midiCmds.length) midiCmdAuto();
  corps.import = midiEtatImport();
  corps.animation = midiCasesAnimation();
  const reponse = await fetch(miseAJour ? '/api/midi/maj' : '/api/midi/creer', {method: 'POST',
    headers: {'Content-Type': 'application/json'}, body: JSON.stringify(corps)});
  const data = await reponse.json();
  if (data.erreur) { midiCree = avant; alert(T(data.erreur)); return; }
  appliquer(data);
  midiEdition = nom;
  midiBoutonCreer();
  midiCmdDessine();
  document.getElementById('mavis').textContent = (miseAJour ? T('Morceau mis à jour :') : T('Morceau créé :')) + ' ' + nom + '. ' +
    (data.gardees && data.gardees.length ? T('Parties inchangées, gardées avec vos retouches :') + ' ' + data.gardees.join(', ') + '. ' : '') +
    T('Tout est mémorisé : vous pourrez rouvrir cet import avec « Modifier l’import ».');
};
document.getElementById('supprimer').onclick = () => {
  if (!etat) return;
  if (etat.memoires.length < 2) { alert(T('Impossible de supprimer le dernier morceau.')); return; }
  if (!confirm(T('Supprimer le morceau') + ' "' + etat.memoire + '" ? ' + T('(Il va dans la corbeille : menu Morceau, Corbeille…)'))) return;
  (async () => {
    const r = await (await fetch('/api/morceau/supprimer', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({index: etat.index})})).json();
    if (r.erreur) { alert(T(r.erreur)); return; }
    appliquer(r);
    avertirNumeros(r.numeros);
  })();
};
document.getElementById('exporter').onclick = () => document.getElementById('dlgexport').showModal();
document.getElementById('xannuler').onclick = () => document.getElementById('dlgexport').close();
async function exporterSurCle(corps) {
  const rep = await fetch('/api/usb/exporter', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(corps)});
  const d = await rep.json();
  if (d.erreur) { alert(T(d.erreur)); return; }
  alert(T('Enregistré sur la clé USB :') + ' ' + d.fichier + String.fromCharCode(10) + T('Pensez à éjecter la clé (menu Morceau ou Listes > Importer depuis la clé USB… > Éjecter la clé) avant de la retirer.'));
}
async function exporterMorceaux(tous) {
  document.getElementById('dlgexport').close();
  if (document.getElementById('xusb').checked) { exporterSurCle({quoi: 'morceaux', tous: tous}); return; }
  const rep = await fetch('/api/morceaux/exporter', {method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({tous: tous})});
  if ((rep.headers.get('Content-Type') || '').includes('json')) { const d = await rep.json(); alert(T(d.erreur || 'erreur')); return; }
  const blob = await rep.blob();
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  const jour = new Date().toISOString().slice(0, 10);
  a.download = (tous ? T('Tous les morceaux') + ' ' + jour : (etat ? etat.memoire : 'morceau')) + '.kromalaser';
  document.body.appendChild(a); a.click(); a.remove();
}
document.getElementById('xcelui').onclick = () => exporterMorceaux(false);
document.getElementById('xtous').onclick = () => exporterMorceaux(true);
document.getElementById('importermorceaux').onclick = () => document.getElementById('fmorceaux').click();
document.getElementById('fmorceaux').onchange = async e => {
  const f = e.target.files[0];
  e.target.value = '';
  if (!f) return;
  let paquet;
  try { paquet = JSON.parse(await f.text()); } catch (x) { alert(T('ce fichier n’est pas un export de morceaux Kromalaser')); return; }
  importerPaquetMorceaux(paquet);
};
// Morceaux deja presents a l'import : Remplacer / Garder les deux / Annuler.
function demanderDoublons(deja) {
  return new Promise(resolve => {
    const d = document.getElementById('dlgdoublons');
    document.getElementById('ddliste').textContent = T('Ces morceaux existent déjà ici :') + ' ' + deja.join(', ') + '.';
    const fin = v => { d.onclose = null; d.close(); resolve(v); };
    document.getElementById('ddremplacer').onclick = () => fin('remplacer');
    document.getElementById('ddgarder').onclick = () => fin('garder');
    document.getElementById('ddannuler').onclick = () => fin(null);
    d.onclose = () => resolve(null);
    d.showModal();
  });
}
async function importerPaquetMorceaux(paquet) {
  const noms = (paquet.morceaux || []).map(m => (m.morceau || {}).nom);
  const deja = noms.filter(n => etat && etat.memoires.includes(n));
  let remplacer = false;
  if (deja.length) {
    const choix = await demanderDoublons(deja);
    if (choix === null) return;
    remplacer = choix === 'remplacer';
  }
  const rep = await fetch('/api/morceaux/importer', {method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({paquet: paquet, remplacer: remplacer})});
  const data = await rep.json();
  if (data.erreur) { alert(T(data.erreur)); return; }
  appliquer(data);
  const r = data.echange || {}, l = [];
  if ((r.ajoutes || []).length) l.push(T('Ajoutés :') + ' ' + r.ajoutes.join(', '));
  if ((r.remplaces || []).length) l.push(T('Remplacés :') + ' ' + r.remplaces.join(', '));
  if ((r.renommes || []).length) l.push(T('Renommés :') + ' ' + r.renommes.map(x => x[0] + ' → ' + x[1]).join(', '));
  alert(l.join(String.fromCharCode(10)) || T('Aucun morceau dans ce fichier.'));
}
// Export / import de listes de morceaux (avec leurs morceaux)
document.getElementById('exporterlistes').onclick = () => {
  const sel = document.getElementById('xlchoix'); sel.innerHTML = '';
  (etat ? etat.listes : []).forEach(n => sel.add(new Option(n, n)));
  if (!sel.options.length) { alert(T('Aucune liste de morceaux à exporter.')); return; }
  if (etat && etat.liste_active) sel.value = etat.liste_active;
  document.getElementById('dlgexportlistes').showModal();
};
document.getElementById('xlannuler').onclick = () => document.getElementById('dlgexportlistes').close();
async function exporterListes(toutes) {
  const nom = document.getElementById('xlchoix').value;
  document.getElementById('dlgexportlistes').close();
  if (document.getElementById('xlusb').checked) { exporterSurCle({quoi: 'listes', tous: toutes, nom: nom}); return; }
  const rep = await fetch('/api/listes/exporter', {method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({tous: toutes, nom: nom})});
  if ((rep.headers.get('Content-Type') || '').includes('json')) { const d = await rep.json(); alert(T(d.erreur || 'erreur')); return; }
  const blob = await rep.blob();
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  const jour = new Date().toISOString().slice(0, 10);
  a.download = (toutes ? T('Toutes les listes') + ' ' + jour : T('Liste') + ' ' + nom) + '.kromalaser';
  document.body.appendChild(a); a.click(); a.remove();
}
document.getElementById('xlcelle').onclick = () => exporterListes(false);
document.getElementById('xltoutes').onclick = () => exporterListes(true);
let paquetListes = null;
document.getElementById('importerlistes').onclick = () => document.getElementById('flistes').click();
document.getElementById('flistes').onchange = async e => {
  const f = e.target.files[0];
  e.target.value = '';
  if (!f) return;
  let p;
  try { p = JSON.parse(await f.text()); } catch (x) { alert(T('ce fichier n’est pas un export de morceaux Kromalaser')); return; }
  preparerImportListes(p);
};
function preparerImportListes(p) {
  paquetListes = p;
  if (!(paquetListes.listes || []).length) { alert(T('Ce fichier ne contient pas de listes. Pour des morceaux seuls, utilisez Morceau > Importer des morceaux.')); return; }
  const z = document.getElementById('ilchoix'); z.innerHTML = '';
  paquetListes.listes.forEach(l => {
    const lab = document.createElement('label'); lab.style.display = 'block'; lab.style.margin = '6px 0';
    const c = document.createElement('input'); c.type = 'checkbox'; c.checked = true; c.value = l.nom;
    c.style.width = '22px'; c.style.height = '22px'; c.style.verticalAlign = 'middle';
    lab.appendChild(c);
    lab.appendChild(document.createTextNode(' ' + l.nom + ' (' + (l.morceaux || []).length + ' ' + T('morceaux') + ')' +
      (etat && etat.listes.includes(l.nom) ? ' — ' + T('remplacera la liste existante') : '')));
    z.appendChild(lab);
  });
  document.getElementById('dlgimportlistes').showModal();
}
document.getElementById('ilannuler').onclick = () => document.getElementById('dlgimportlistes').close();
document.getElementById('ilimporter').onclick = async () => {
  const noms = [...document.querySelectorAll('#ilchoix input:checked')].map(c => c.value);
  if (!noms.length) { alert(T('Cochez au moins une liste.')); return; }
  document.getElementById('dlgimportlistes').close();
  const besoin = new Set(paquetListes.listes.filter(l => noms.includes(l.nom)).flatMap(l => l.morceaux || []));
  const deja = [...besoin].filter(n => etat && etat.memoires.includes(n));
  let remplacer = false;
  if (deja.length) {
    const choix = await demanderDoublons(deja);
    if (choix === null) return;
    remplacer = choix === 'remplacer';
  }
  const rep = await fetch('/api/listes/importer', {method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({paquet: paquetListes, noms: noms, remplacer: remplacer})});
  const data = await rep.json();
  if (data.erreur) { alert(T(data.erreur)); return; }
  appliquer(data);
  const r = data.echange || {}, l = [];
  if ((r.listes_ajoutees || []).length) l.push(T('Listes ajoutées :') + ' ' + r.listes_ajoutees.join(', '));
  if ((r.listes_remplacees || []).length) l.push(T('Listes remplacées :') + ' ' + r.listes_remplacees.join(', '));
  if ((r.ajoutes || []).length) l.push(T('Morceaux ajoutés :') + ' ' + r.ajoutes.join(', '));
  if ((r.remplaces || []).length) l.push(T('Morceaux remplacés :') + ' ' + r.remplaces.join(', '));
  if ((r.renommes || []).length) l.push(T('Renommés :') + ' ' + r.renommes.map(x => x[0] + ' → ' + x[1]).join(', '));
  alert(l.join(String.fromCharCode(10)));
};
// Cle USB du boitier : choisir un fichier .kromalaser a importer
let usbMode = 'morceaux';
async function ouvrirCle(mode) {
  usbMode = mode;
  const z = document.getElementById('usbfichiers'), avis = document.getElementById('usbavis');
  z.innerHTML = ''; avis.textContent = T('Lecture de la clé…');
  document.getElementById('dlgusb').showModal();
  const d = await (await fetch('/api/usb/lister', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'})).json();
  if (d.erreur) { avis.textContent = T(d.erreur); return; }
  avis.textContent = (d.fichiers || []).length
    ? (mode === 'listes' ? T('Choisissez le fichier de listes à importer :') : T('Choisissez le fichier de morceaux à importer :'))
    : T('Aucun fichier .kromalaser sur la clé.');
  (d.fichiers || []).forEach(f => {
    const b = document.createElement('button'); b.style.display = 'block'; b.style.width = '100%'; b.style.textAlign = 'left'; b.style.margin = '4px 0';
    b.textContent = f.fichier + '  (' + f.date + ')';
    b.onclick = async () => {
      const r = await (await fetch('/api/usb/lire', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({fichier: f.fichier})})).json();
      if (r.erreur) { alert(T(r.erreur)); return; }
      document.getElementById('dlgusb').close();
      if (usbMode === 'listes') preparerImportListes(r.paquet); else importerPaquetMorceaux(r.paquet);
    };
    z.appendChild(b);
  });
}
document.getElementById('musbimport').onclick = () => ouvrirCle('morceaux');
document.getElementById('lusbimport').onclick = () => ouvrirCle('listes');
document.getElementById('usbfermer').onclick = () => document.getElementById('dlgusb').close();
document.getElementById('usbejecter').onclick = async () => {
  const d = await (await fetch('/api/usb/ejecter', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'})).json();
  document.getElementById('usbavis').textContent = d.erreur ? T(d.erreur) : T('Vous pouvez retirer la clé.');
  document.getElementById('usbfichiers').innerHTML = '';
};
document.getElementById('renommer').onclick = () => {
  const nom = prompt(T('Titre du morceau :'), etat ? etat.memoire : '');
  if (nom && nom.trim()) agir('/api/nom', {nom: nom.trim()});
};
document.getElementById('memoharpe').onclick = () => {
  if (!confirm(T('Mémoriser dans ce morceau tous les réglages actuels de la harpe ? Ils remplaceront ceux déjà mémorisés.'))) return;
  agir('/api/memoire/harpe');
};

// Numero du son tel que l'affiche le synthe <-> numero interne (1 a 128).
let NUMEROTATION = '1-128';
function progPlage() {
  return NUMEROTATION === '0-127' ? T('sons 0 à 127')
       : NUMEROTATION === 'groupes8' ? T('sons 11 à 88, chiffres de 1 à 8') : T('sons 1 à 128');
}
function progAffiche(n) {
  if (n === undefined || n === null) return '';
  if (NUMEROTATION === '0-127') return String(n - 1);
  if (NUMEROTATION === 'groupes8' && n <= 64) return String(Math.floor((n - 1) / 8) + 1) + String((n - 1) % 8 + 1);
  return String(n);
}
function progInterne(v) {          // undefined = numero impossible ; null = vide
  if (v === '') return null;
  if (!/^[0-9]+$/.test(v)) return undefined;
  const n = +v;
  if (NUMEROTATION === '0-127') return n <= 127 ? n + 1 : undefined;
  if (NUMEROTATION === 'groupes8') {
    const a = Math.floor(n / 10), b = n % 10;
    return (v.length === 2 && a >= 1 && a <= 8 && b >= 1 && b <= 8) ? (a - 1) * 8 + b : undefined;
  }
  return (n >= 1 && n <= 128) ? n : undefined;
}
let heureEnvoyee = false;
// Nouvelle version publiee sur GitHub ? (seulement si l'appareil qui affiche la
// page a Internet ; sinon, rien ne s'affiche)
let majVerifiee = false;
function versionPlusRecente(a, b) {
  const pa = String(a).split('.').map(Number), pb = String(b).split('.').map(Number);
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
    const x = pa[i] || 0, y = pb[i] || 0;
    if (x !== y) return x > y;
  }
  return false;
}
async function verifierMiseAJour(data) {
  if (majVerifiee || !data.depot || !data.version) return;
  majVerifiee = true;
  try {
    const r = await fetch('https://api.github.com/repos/' + data.depot + '/releases/latest', {cache: 'no-store'});
    if (!r.ok) return;
    const d = await r.json();
    const derniere = String(d.tag_name || '').replace(/^v/i, '');
    if (!derniere || !versionPlusRecente(derniere, data.version)) return;
    document.getElementById('avismajtexte').textContent =
      T('Une nouvelle version de Kromabridge est disponible :') + ' ' + derniere + ' (' + T('vous avez la') + ' ' + data.version + ').' +
      (data.pc ? '' : ' ' + T('Téléchargez le fichier kromabridge-pi.zip, puis ⚙ > Mettre à jour le Pi….'));
    document.getElementById('avismajlien').href = d.html_url || ('https://github.com/' + data.depot + '/releases/latest');
    document.getElementById('avismaj').style.display = '';
  } catch (x) { /* pas d'Internet : rien */ }
}
document.getElementById('avismajfermer').onclick = () => { document.getElementById('avismaj').style.display = 'none'; };
let avisFirmwareFerme = false;
function avertirFirmware(data) {
  const z = document.getElementById('avisfirmware');
  const autre = data.firmware && data.firmware_teste && data.firmware !== data.firmware_teste;
  z.style.display = autre && !avisFirmwareFerme ? '' : 'none';
  if (autre) document.getElementById('avisfirmwaretexte').textContent =
    T('Votre harpe a le firmware') + ' ' + data.firmware + '. ' + T('Kromabridge est prévu pour le firmware') + ' ' + data.firmware_teste +
    ', ' + T('le seul testé : certaines fonctions (nombre de rayons, animation, réglages de la harpe) peuvent ne pas fonctionner. Vous pouvez mettre la harpe à jour avec le logiciel du fabricant (KromaUpdater). Utilisation à vos risques et périls.');
}
document.getElementById('avisfirmwarefermer').onclick = () => { avisFirmwareFerme = true; document.getElementById('avisfirmware').style.display = 'none'; };
function appliquer(data) {
  data.liste_morceaux_de = nom => (data.listes_detail || {})[nom] || [];
  avertirFirmware(data);
  verifierMiseAJour(data);
  // Version Raspberry Pi : le Pi prend l'heure de l'appareil qui affiche la page.
  if (!data.pc && !heureEnvoyee) {
    heureEnvoyee = true;
    fetch('/api/heure', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ms: Date.now()})}).catch(() => {});
  }
  if (data.pc) {
    // Version PC : ni Wi-Fi du boitier ni extinction.
    ['menureseau', 'eteindre'].forEach(id => {
      const e = document.getElementById(id); if (e) e.style.display = 'none'; });
  }
  if (data.noms) noms = data.noms;
  if (data.langue && data.langue !== LANGUE) { LANGUE = data.langue; traduirePage(); }
  etat = data;
  document.getElementById('titre').textContent = data.memoire;
  document.querySelectorAll('.usbseul, .piseul').forEach(e => { e.style.display = data.pc ? 'none' : ''; });

  const choix = document.getElementById('memoires');
  if (JSON.stringify([...choix.options].map(o => o.text)) !== JSON.stringify(data.memoires)) {
    choix.innerHTML = '';
    data.memoires.forEach((nom, i) => choix.add(new Option(nom, i)));
  }
  choix.value = data.index;
  if (document.activeElement && document.activeElement.id !== 'nbrayons')
    document.getElementById('nbrayons').value = data.rayons.length;
  if (document.activeElement && document.activeElement.id !== 'canal' && data.canal_sortie)
    document.getElementById('canal').value = data.canal_sortie;
  const son = data.son_synthe || {};
  const sonActif = son.actif !== false && son.programme !== undefined;
  // Morceau en parties : la ligne regle le son de la partie choisie.
  document.getElementById('slibelle').textContent = (data.parties || []).length > 1
    ? T('Changer le son du synthé pour la partie') + ' ' + (data.parties[data.partie] || '') + ' :'
    : T('Changer le son du synthé à l’ouverture du morceau :');
  if (sAttente !== null && sAttente !== cleSon()) sAttente = null;   // autre morceau ou autre partie
  if (document.activeElement && document.activeElement.id !== 'sactif')
    document.getElementById('sactif').checked = sonActif || sAttente !== null;
  ['sprog', 'smsb', 'slsb'].forEach(id => { document.getElementById(id).style.opacity = (sonActif || sAttente !== null) ? '' : '.45'; });
  if (document.activeElement && document.activeElement.id !== 'snum') {
    NUMEROTATION = son.numerotation || '1-128';
    document.getElementById('snum').value = NUMEROTATION;
  }
  document.getElementById('splage').textContent = progPlage();
  [['sprog', 'programme'], ['smsb', 'msb'], ['slsb', 'lsb']].forEach(([id, k]) => {
    if (document.activeElement && document.activeElement.id !== id)
      document.getElementById(id).value = id === 'sprog' ? progAffiche(son[k]) : (son[k] ?? '');
  });
  const a = data.animation || {};
  if (document.activeElement && !['animpause', 'animduree', 'animtransduree'].includes(document.activeElement.id)) {
    document.getElementById('animactive').checked = !!a.active;
    document.getElementById('animpause').value = a.pause ?? 2;
    document.getElementById('animduree').value = a.duree ?? 3;
    document.getElementById('animtrans').checked = !!a.transition;
    document.getElementById('animtransduree').value = a.trans_duree ?? 1;
  }
  const sonPage = (data.son_clavier === 'page' || data.son_jeu === 'page');
  document.getElementById('activerson').style.display = (sonPage && !harpeSon.pret) ? '' : 'none';
  const hp = document.getElementById('btnactiverson');
  hp.style.display = sonPage ? '' : 'none';
  hp.textContent = harpeSon.pret ? '🔊' : '🔇';
  hp.classList.toggle('eteint', !harpeSon.pret);
  hp.title = harpeSon.pret ? T('Son de la harpe activé sur cet appareil') : T('Activer le son de la harpe sur cet appareil');
  document.getElementById('alerteharpe').style.display = data.harpe_injoignable ? '' : 'none';
  // toujours dans le menu, grise pour un morceau qui n'a pas ete importe
  document.getElementById('modifimport').disabled = !data.import;
  document.getElementById('modifimport').title = data.import ? '' : T('Ce morceau n’a pas été créé par un import MIDI.');
  dessinerParties(data);
  const lm = data.liste_morceaux || [], pos = lm.indexOf(data.index);
  document.getElementById('precedent').disabled = !lm.length || pos === 0;
  document.getElementById('suivant').disabled = !lm.length || (pos >= 0 && pos === lm.length - 1);
  const toutes = [null].concat(data.listes || []), il = Math.max(0, toutes.indexOf(data.liste_active || null));
  document.getElementById('listeprec').disabled = il <= 0;
  document.getElementById('listesuiv').disabled = il >= toutes.length - 1;
  // ▲ ▼ + nom de la liste ; ◀ ▶ + titre du morceau et sa place dans la liste
  document.getElementById('nomliste').textContent = data.liste_active || T('Tous');
  document.getElementById('posmorceau').textContent = pos >= 0 ? '(' + (pos + 1) + '/' + lm.length + ')' : '(' + T('hors liste') + ')';
  const cl = document.getElementById('choixlistes');
  const cleListes = JSON.stringify([data.listes, data.liste_active]);
  if (cl.dataset.cle !== cleListes) {
    cl.dataset.cle = cleListes;
    cl.innerHTML = '';
    toutes.forEach(n => {
      const b = document.createElement('button');
      b.textContent = ((n || null) === (data.liste_active || null) ? '✓ ' : ' ') + (n || T('Tous les morceaux (liste par défaut)'));
      b.onclick = () => agir('/api/listes/activer', {nom: n});
      cl.appendChild(b);
    });
  }
  const bm = document.getElementById('memoharpe');
  const modifs = data.harpe_modifiee || [];
  document.getElementById('reglages').classList.toggle('point', modifs.length > 0);
  document.getElementById('reglages').title = modifs.length
    ? T('Réglages de harpe non mémorisés : ') + (data.harpe_modifiee_noms || modifs).join(', ') : T('Paramètres');
  bm.className = modifs.length ? 'modifie' : '';
  bm.textContent = modifs.length
    ? T('Réglages de harpe modifiés : mémoriser')
    : (data.harpe_memorisee ? T('Harpe mémorisée (remplacer)') : T('Mémoriser la harpe dans ce morceau'));
  bm.title = modifs.length ? T('Non mémorisés : ') + (data.harpe_modifiee_noms || modifs).join(', ') : '';

  const na = (data.notes_apprises || []).length;
  document.getElementById('apprendre').textContent =
    data.apprentissage_clavier ? T('Terminer') + ' (' + na + ' ' + T('notes') + ')' : T('Créer les rayons au clavier');
  document.getElementById('apprendre').className = data.apprentissage_clavier ? 'actif' : '';
  const ta = document.getElementById('terminerappr');
  ta.style.display = data.apprentissage_clavier ? '' : 'none';
  ta.textContent = T('Terminer') + ' (' + na + ' ' + T('notes') + ')';
  document.getElementById('finattrib').style.display = data.attribution ? '' : 'none';
  document.getElementById('finattrib').textContent = '✓ ' + T('Garder les changements');
  const bandeau = document.getElementById('bandeauappr');
  bandeau.style.display = (data.apprentissage_clavier || data.attribution) ? '' : 'none';
  document.getElementById('bandeauboutons').style.display = data.attribution ? '' : 'none';
  document.getElementById('annulattrib').style.display = data.attribution ? '' : 'none';
  document.getElementById('bandeautexte').textContent = data.apprentissage_clavier
    ? T('Créer les rayons au clavier : jouez au clavier les notes du morceau, dans n’importe quel ordre, puis touchez Terminer : elles deviendront les rayons, du grave à l’aigu.')
    : T('Changer un rayon au clavier : coupez un rayon (ou touchez sa tuile), puis jouez sur le clavier la note qu’il doit jouer. Chaque note change le rayon tout de suite ; « Annuler les changements » remet le morceau comme avant.');
  if (document.getElementById('dlgappris').open) apprisDessine(data);
  const ba = document.getElementById('attribuer');
  ba.className = data.attribution ? 'actif' : '';
  ba.textContent = data.attribution ? T('Terminer le changement des rayons') : T('Changer un rayon au clavier');
  document.getElementById('etat').textContent = data.apprentissage_clavier
    ? ((data.clavier_connecte || na)
        ? T('Apprentissage : jouez vos notes au clavier.') + ' ' +
          (apprisParts.length ? T('Partie') + ' ' + String.fromCharCode(65 + apprisParts.length) + ' : ' : '') +
          (na ? (data.notes_apprises.map(n => noms[n] || n).join(' ')) : '') +
          (na > 15 ? '  ' + T('(plus de 15 notes : il faudra faire plusieurs parties)') : '')
        : T('Aucun clavier relié : choisissez-le dans Paramètres.'))
    : data.attribution
      ? (data.clavier_connecte
          ? T('Coupez un rayon, puis jouez sa note sur le clavier.')
          : T('Aucun clavier relié : choisissez-le dans Paramètres.'))
      : (data.rayons_allumes ? data.rayons_allumes + T(' rayons allumés') : '');

  const zone = document.getElementById('rayons');
  // Pas de redessin pendant qu'une liste de notes est ouverte.
  if (document.activeElement && document.activeElement.className === 'choixnote') return;
  zone.innerHTML = '';
  document.querySelector('.ligne.tous').style.display = data.gamme ? 'none' : '';
  document.getElementById('avisgamme').style.display = data.gamme ? '' : 'none';
  data.rayons.forEach(rayon => {
    const carte = document.createElement('div');
    carte.className = 'rayon' + (rayon.note_entrante === data.derniere_note ? ' touche' : '');
    carte.innerHTML =
      '<div class="num" style="display:flex;justify-content:space-between;align-items:center;gap:4px">' +
      '<span style="white-space:nowrap">' + T('Rayon') + ' ' + rayon.rayon + '</span>' +
      '<span style="display:flex;gap:4px;flex:0 0 auto"><button class="plusaccord" title="' + T('Faire jouer un accord à ce rayon') + '">' + T('Accord') + '</button>' +
      '<button class="supprray" title="' + T('Supprimer ce rayon') + '">✕</button></span></div>' +
      '<div class="note" title="' + T('Touchez pour choisir la note') + '">' + (noms[rayon.note] || rayon.note) + ' <span class="crayon">&#9662;</span></div>' +
      '<div class="ligne"></div>';
    const nom = carte.querySelector('.note');
    nom.onclick = () => {
      const liste = document.createElement('select');
      liste.className = 'choixnote';
      for (let n = 127; n >= 0; n--) liste.add(new Option(noms[n] || n, n));
      liste.value = rayon.note;
      nom.replaceWith(liste);
      liste.focus();
      liste.onchange = () => {
        fetch('/api/ecouter', {method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({note: +liste.value})});
        liste.blur(); agir('/api/rayon', {rayon: rayon.rayon, note: +liste.value});
      };
      liste.onblur = () => setTimeout(lire, 50);
    };
    // Accord : les notes jouees en plus, une ligne chacune.
    const accord = rayon.notes_sup || [];
    const envoyerAccord = notes => agir('/api/rayon/accord', {rayon: rayon.rayon, notes: notes});
    accord.forEach((n, i) => {
      const l = document.createElement('div'); l.className = 'accord';
      const liste = document.createElement('select'); liste.className = 'choixnote';
      for (let k = 127; k >= 0; k--) liste.add(new Option(noms[k] || k, k));
      liste.value = n;
      liste.onchange = () => {
        fetch('/api/ecouter', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({note: +liste.value})});
        const nv = accord.slice(); nv[i] = +liste.value; liste.blur(); envoyerAccord(nv);
      };
      const x = document.createElement('button'); x.textContent = '✕'; x.title = T('Retirer cette note');
      x.onclick = () => { const nv = accord.slice(); nv.splice(i, 1); envoyerAccord(nv); };
      l.appendChild(liste); l.appendChild(x);
      carte.insertBefore(l, carte.querySelector('.ligne'));
    });
    carte.querySelector('.plusaccord').onclick = () => accordOuvrir(rayon.rayon);
    const sup = carte.querySelector('.supprray');
    if (data.rayons.length <= 5) { sup.disabled = true; sup.title = T('La harpe a au moins 5 rayons'); }
    sup.onclick = () => {
      if (!confirm(T('Supprimer le rayon') + ' ' + rayon.rayon + ' (' + (noms[rayon.note] || rayon.note) + ') ? ' +
                   T('Les rayons suivants se décalent d’un cran vers la gauche et gardent leur note.'))) return;
      agir('/api/rayon/supprimer', {rayon: rayon.rayon});
    };
    if (data.gamme) {
      sup.style.display = 'none';
      // morceau Gamme : chaque rayon joue la note de la harpe, rien ne se modifie
      carte.querySelector('.plusaccord').style.display = 'none';
      carte.querySelector('.ligne').style.display = 'none';
      const nm = carte.querySelector('.note'); nm.onclick = null; nm.style.cursor = 'default';
      const cr = nm.querySelector('.crayon'); if (cr) cr.remove();
      zone.appendChild(carte);
      return;
    }
    // "Changer un rayon au clavier" : toucher la tuile choisit le rayon (sans harpe)
    if (data.attribution) {
      carte.style.cursor = 'pointer';
      carte.onclick = e => {
        if (e.target.closest('button, select, .note')) return;
        agir('/api/rayon/choisir', {rayon: rayon.rayon});
      };
    }
    const ligne = carte.querySelector('.ligne');
    [['-12', -12], ['-1', -1], ['+1', 1], ['+12', 12]].forEach(([texte, pas]) => {
      const bouton = document.createElement('button');
      bouton.textContent = texte;
      bouton.onclick = () => {
        const note = Math.min(127, Math.max(0, rayon.note + pas));
        agir('/api/rayon', {rayon: rayon.rayon, note: note});
      };
      ligne.appendChild(bouton);
    });
    zone.appendChild(carte);
  });
}

document.getElementById('memoires').onchange = e => agir('/api/memoire', {index: +e.target.value});
document.getElementById('silence').onclick = () => agir('/api/silence');
// Raccourcis clavier (version PC) : fleches gauche / droite = morceaux, haut / bas = listes, Echap = Panique.
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') { agir('/api/silence'); return; }
  const cible = e.target, tag = (cible && cible.tagName) || '';
  if (['INPUT', 'SELECT', 'TEXTAREA'].includes(tag) || (cible && cible.isContentEditable)) return;
  if (document.querySelector('dialog[open]') || e.ctrlKey || e.altKey || e.metaKey) return;
  const id = {ArrowLeft: 'precedent', ArrowRight: 'suivant', ArrowUp: 'listeprec', ArrowDown: 'listesuiv'}[e.key];
  if (!id) return;
  e.preventDefault();
  const b = document.getElementById(id);
  if (b && !b.disabled) b.click();
});
document.getElementById('ouvrir').onclick = () => agir('/api/harpe/ouvrir');
document.getElementById('fermer').onclick = () => agir('/api/harpe/fermer');
document.getElementById('attribuer').onclick = () =>
  agir('/api/attribution', {active: !(etat && etat.attribution)});
let apprisRetires = new Set(), apprisParts = [];
document.getElementById('apprendre').onclick = () => {
  if (etat && etat.apprentissage_clavier) {
    document.getElementById('atitre').value = '';
    document.getElementById('aremplacer').textContent = T('Remplacer les notes de') + ' « ' + etat.memoire + ' »';
    document.getElementById('dlgappris').showModal();
    apprisDessine(etat);
  } else {
    apprisRetires = new Set();
    apprisParts = [];
    agir('/api/apprentissage/clavier/debut');
  }
};
function apprisNotes(data) {
  return (data.notes_apprises || []).filter(n => !apprisRetires.has(n));
}
function apprisDessine(data) {
  const z = document.getElementById('anotes');
  const notes = apprisNotes(data);
  const cle = JSON.stringify(notes);
  if (z.dataset.cle === cle) return;
  z.dataset.cle = cle;
  z.innerHTML = '';
  notes.forEach(n => {
    const b = document.createElement('button');
    b.textContent = (noms[n] || n) + ' ✕';
    b.onclick = () => { apprisRetires.add(n); apprisDessine(etat); };
    z.appendChild(b);
  });
  const k = notes.length, ok = k >= 1 && k <= 15;
  const avis = document.getElementById('aavis');
  avis.textContent = k > 15
    ? T('Plus de 15 notes : la harpe a 15 rayons au plus. Retirez des notes, ou faites l’apprentissage en plusieurs parties : gardez les notes de la première partie avec le bouton ci-dessous, puis jouez la suivante.')
    : (k ? k + ' ' + T('rayons') : '') + (k && k < 5 ? ' : ' +
       T('la harpe en a au moins 5, les rayons en trop rejoueront la note la plus aiguë.') : '');
  avis.style.color = k > 15 ? 'var(--vif)' : '';
  const zp = document.getElementById('aparties');
  zp.innerHTML = '';
  if (apprisParts.length) {
    zp.appendChild(document.createTextNode(T('Parties gardées :') + ' '));
    apprisParts.forEach((p, i) => {
      const b = document.createElement('button');
      b.textContent = p.nom + ' (' + p.notes.length + ' ' + T('rayons') + ') ✕';
      b.onclick = () => {
        apprisParts.splice(i, 1);
        apprisParts.forEach((x, j) => { x.nom = String.fromCharCode(65 + j); });
        z.dataset.cle = ''; apprisDessine(etat);
      };
      zp.appendChild(b);
    });
  }
  document.getElementById('apartie').disabled = !ok;
  document.getElementById('acreer').disabled = !(ok || (apprisParts.length && k === 0));
  document.getElementById('aremplacer').disabled = !ok || apprisParts.length > 0;
}
async function apprisFin(remplacer) {
  const notes = apprisNotes(etat);
  const nom = document.getElementById('atitre').value.trim();
  if (!remplacer && !nom) { alert(T('Donnez un titre au morceau.')); return; }
  let parties = [];
  if (apprisParts.length) {
    parties = apprisParts.map(p => ({nom: p.nom, notes: p.notes}));
    if (notes.length) parties.push({nom: String.fromCharCode(65 + parties.length), notes: notes});
  }
  document.getElementById('dlgappris').close();
  apprisParts = [];
  await agir('/api/apprentissage/clavier/fin', {nom: nom, notes: notes, remplacer: remplacer, parties: parties});
}
document.getElementById('apartie').onclick = async () => {
  const notes = apprisNotes(etat);
  apprisParts.push({nom: String.fromCharCode(65 + apprisParts.length), notes: notes});
  apprisRetires = new Set();
  await fetch('/api/apprentissage/clavier/vider', {method: 'POST'});
  document.getElementById('dlgappris').close();
};
document.getElementById('acreer').onclick = () => apprisFin(false);
document.getElementById('aremplacer').onclick = () => apprisFin(true);
document.getElementById('acontinuer').onclick = () => document.getElementById('dlgappris').close();
document.getElementById('aannuler').onclick = () => {
  document.getElementById('dlgappris').close();
  agir('/api/apprentissage/clavier/annuler');
};

let reseau = null;
async function lireReseau() {
  try {
    reseau = await (await fetch('/api/reseau')).json();
  } catch (e) { return; }
  if (reseau.erreur) return;
  const direct = reseau.mode === 'direct';
  document.getElementById('reseau').textContent = direct
    ? T('Connexion directe : réseau ') + reseau.ssid_direct + T(', page sur ') + 'http://' + reseau.adresse_directe + ':8080'
    : T('Wi-Fi de la maison : ') + (reseau.reseau || T('aucun'));
  document.getElementById('basculer').textContent = direct ? T('Revenir au Wi-Fi de la maison') : T('Connexion directe');
}
document.getElementById('basculer').onclick = async () => {
  const direct = reseau && reseau.mode === 'direct';
  const message = direct
    ? 'Le Pi va rejoindre le Wi-Fi de la maison. Reconnectez votre appareil au Wi-Fi de la box. Continuer ?'
    : 'Le Pi va quitter la box et créer le réseau Kromalaser (mot de passe : kromalaser). Connectez votre appareil à ce réseau, puis ouvrez http://10.42.0.1:8080. Continuer ?';
  if (!confirm(T(message))) return;
  const reponse = await (await fetch(direct ? '/api/reseau/maison' : '/api/reseau/direct', {method: 'POST'})).json();
  if (reponse.erreur) { alert(T(reponse.erreur)); return; }
  clearInterval(minuterie);
  document.body.innerHTML = direct
    ? '<h1>Retour au Wi-Fi de la maison</h1><p>Reconnectez votre appareil au Wi-Fi de votre box, puis rouvrez la page du Pi.</p>'
    : '<h1>Connexion directe en cours</h1><p>Dans les réglages Wi-Fi de votre appareil, choisissez le réseau <b>Kromalaser</b> (mot de passe : <b>kromalaser</b>), puis ouvrez <b>http://10.42.0.1:8080</b>.</p>';
};
lireReseau();
setInterval(lireReseau, 5000);

let minuterie = null;
document.getElementById('eteindre').onclick = async () => {
  if (!confirm(T('Éteindre le Pi ?'))) return;
  clearInterval(minuterie);
  await fetch('/api/eteindre', {method: 'POST'});
  document.body.innerHTML =
    '<h1>Pi en cours d\\'extinction</h1>' +
    '<p id="decompte" style="font-size:28px">Patientez encore <b>30</b> secondes…</p>';
  let reste = 30;
  const t = setInterval(() => {
    reste -= 1;
    const p = document.getElementById('decompte');
    if (reste > 0) { p.innerHTML = 'Patientez encore <b>' + reste + '</b> secondes…'; return; }
    clearInterval(t);
    p.innerHTML = '<b>Vous pouvez couper l\\'alimentation.</b>';
  }, 1000);
};

document.getElementById('importer2').onclick = () => document.getElementById('importer').onclick();
// Toucher le bandeau arrete le changement des rayons au clavier.
// Fin du mode "Changer un rayon au clavier" : garder, ou annuler les changements.
const garderAttribution = () => agir('/api/attribution', {active: false});
const annulerAttribution = () => {
  if (confirm(T('Remettre tous les rayons du morceau comme ils étaient avant ?'))) agir('/api/attribution', {active: false, annuler: true});
};
document.getElementById('bgarder').onclick = garderAttribution;
document.getElementById('bannuler').onclick = annulerAttribution;
document.getElementById('annulattrib').onclick = annulerAttribution;
// Apprendre, attribuer ou un choix du menu Morceau : on revient aux rayons (avec avertissement).
['apprendre', 'attribuer', 'terminerappr', 'finattrib', 'mouvrir', 'nouveau', 'enregsous', 'renommer', 'supprimer',
 'mcorbeille', 'importer', 'modifimport', 'exporter', 'importermorceaux', 'gererlistes', 'exporterlistes', 'importerlistes', 'musbimport', 'lusbimport', 'gerermorceaux', 'majpi'].forEach(id => {
  const b = document.getElementById(id);
  if (!b || !b.onclick) return;
  const action = b.onclick;
  b.onclick = e => { if (quitterReglages()) action.call(b, e); };
});
lire();
minuterie = setInterval(lire, 250);
</script>
</body>
</html>
"""


PAGE_HARPE = '<!DOCTYPE html>\n<html lang="fr">\n<head>\n<meta charset="utf-8">\n<meta name="viewport" content="width=device-width, initial-scale=1">\n<title>Kromabridge - Réglages de la harpe</title>\n<style>\n  :root { --fond: #11131a; --carte: #1b1f2a; --trait: #2c3242; --texte: #eef1f7;\n          --doux: #9aa3b8; --vif: #ffb454; }\n  * { box-sizing: border-box; }\n  body { margin: 0; padding: 16px; background: var(--fond); color: var(--texte);\n         font: 16px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }\n  header { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 12px; }\n  h1 { font-size: 20px; margin: 0; flex: 1 1 auto; }\n  button, select { font: inherit; color: var(--texte); background: var(--carte);\n         border: 1px solid var(--trait); border-radius: 10px; padding: 10px 14px; min-height: 44px; }\n  button.vif { border-color: var(--vif); color: var(--vif); }\n  .onglets { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 12px; }\n  .onglets button.actif { border-color: var(--vif); color: var(--vif); }\n  .reglage { background: var(--carte); border: 1px solid var(--trait); border-radius: 12px;\n             padding: 12px; margin-bottom: 10px; }\n  .reglage .haut { display: flex; justify-content: space-between; color: var(--doux); }\n  .reglage .val { color: var(--texte); font-weight: 600; }\n  .oct { color: var(--doux); }\n  input[type=range] { width: 100%; height: 36px; }\n  input[type=checkbox] { width: 28px; height: 28px; }\n  .etat { color: var(--doux); margin-bottom: 12px; min-height: 1.5em; }\n  .ligne { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }\n  #journal { font: 14px/1.4 ui-monospace, Menlo, Consolas, monospace; max-height: 60vh; overflow-y: auto; }\n  #journal div { padding: 4px 0; border-bottom: 1px solid var(--trait); }\n  #journal .oct { color: var(--doux); }\n</style>\n</head>\n<body>\n<header>\n  <h1>Réglages de la harpe</h1>\n  <a href="/"><button>Retour</button></a>\n  <button id="lire" class="vif">Lire la harpe</button>\n</header>\n<div class="etat" id="etat">Appuyez sur Lire la harpe pour afficher ses réglages actuels.</div>\n<div class="onglets" id="onglets"></div>\n<div id="zone"></div>\n<script>\nlet etat = null, onglet = \'System\';\nconst NOMS_ONGLETS = {System: \'Système\', Footsw: \'Pédales\', Beams: \'Rayons\', Colors: \'Couleurs\', Transpose: \'Transposition\', Preset: \'Presets\', Moniteur: \'Moniteur\'};\nconst NOMS_ONGLETS_EN = {System: \'System\', Footsw: \'Footswitches\', Beams: \'Beams\', Colors: \'Colors\', Transpose: \'Transpose\', Preset: \'Presets\', Moniteur: \'Monitor\'};\nconst ONGLETS = [\'System\', \'Footsw\', \'Beams\', \'Colors\', \'Transpose\', \'Preset\', \'Moniteur\'];\n\nfunction message(t) { document.getElementById(\'etat\').textContent = t; }\n\nasync function appel(chemin, corps, methode) {\n  const options = {method: methode || \'POST\', headers: {\'Content-Type\': \'application/json\'}};\n  if (options.method !== \'GET\') options.body = JSON.stringify(corps || {});\n  const d = await (await fetch(chemin, options)).json();\n  if (d.erreur) { message(T(\'Erreur : \') + T(d.erreur)); return null; }\n  etat = d; return d;\n}\n\nfunction affiche(r, v) { return v + r.decalage; }\n\nfunction dessine() {\n  const o = document.getElementById(\'onglets\'); o.innerHTML = \'\';\n  ONGLETS.forEach(n => {\n    const b = document.createElement(\'button\');\n    b.innerHTML = LANGUE === \'en\' ? NOMS_ONGLETS_EN[n]\n      : NOMS_ONGLETS[n] + (n !== \'Moniteur\' ? \'<br><small class="oct">\' + n + \'</small>\' : \'\');\n    if (n === onglet) b.className = \'actif\';\n    b.onclick = () => { onglet = n; dessine(); };\n    o.appendChild(b);\n  });\n  const z = document.getElementById(\'zone\'); z.innerHTML = \'\';\n  if (!etat) return;\n  if (onglet === \'Preset\') { dessinePresets(z); return; }\n  if (onglet === \'Moniteur\') { dessineMoniteur(z); return; }\n  etat.reglages.filter(r => r.onglet === onglet).forEach(r => {\n    const v = etat.valeurs[r.cle];\n    const inconnu = (v === undefined);\n    const c = document.createElement(\'div\'); c.className = \'reglage\';\n    const nomEn = (r.nom_manager && !r.nom_manager.startsWith(\'(\')) ? r.nom_manager : T(r.nom);\n    c.innerHTML = \'<div class="haut"><span>\' + (LANGUE === \'en\' ? nomEn :\n      r.nom + (r.nom_manager && r.nom_manager !== r.nom ? \' <small class="oct">(\' + r.nom_manager + \')</small>\' : \'\')) +\n      \'</span><span class="val"></span></div>\';\n    const val = c.querySelector(\'.val\');\n    let champ;\n    if (r.case) {\n      champ = document.createElement(\'input\'); champ.type = \'checkbox\'; champ.checked = v === 1;\n      val.textContent = inconnu ? \'?\' : (v ? T(\'oui\') : T(\'non\'));\n      champ.onchange = () => envoie(r, champ.checked ? 1 : 0);\n    } else if (r.choix) {\n      champ = document.createElement(\'select\');\n      r.choix.forEach((t, i) => champ.add(new Option(\n        LANGUE === \'en\' && r.choix_manager ? r.choix_manager[i] :\n        t + (r.choix_manager && r.choix_manager[i] !== t ? \'  (\' + r.choix_manager[i] + \')\' : \'\'), i)));\n      if (inconnu) { champ.selectedIndex = -1; val.textContent = \'?\'; } else { champ.value = v; }\n      champ.onchange = () => envoie(r, +champ.value);\n    } else {\n      champ = document.createElement(\'input\'); champ.type = \'range\';\n      champ.min = 0; champ.max = r.max; champ.step = 1; champ.value = inconnu ? 0 : v;\n      val.textContent = inconnu ? \'?\' : affiche(r, v);\n      champ.oninput = () => { val.textContent = affiche(r, +champ.value); };\n      champ.onchange = () => envoie(r, +champ.value);\n    }\n    if ((etat.verrouilles || []).includes(r.cle)) {\n      champ.disabled = true;\n      const note = document.createElement(\'div\'); note.className = \'oct\';\n      note.textContent = T(\'Verrouillé à 0 : la transposition se fait dans Kromabridge.\');\n      c.appendChild(champ); c.appendChild(note); z.appendChild(c); return;\n    }\n    c.appendChild(champ); z.appendChild(c);\n  });\n}\n\nfunction dessinePresets(z) {\n  const c = document.createElement(\'div\'); c.className = \'reglage\';\n  const choix = document.createElement(\'select\');\n  for (let n = etat.presets[0]; n <= etat.presets[1]; n++) choix.add(new Option(\'Preset \' + n, n));\n  const rappel = document.createElement(\'button\'); rappel.textContent = LANGUE === \'en\' ? \'Recall\' : \'Rappeler (Recall)\'; rappel.className = \'vif\';\n  const sauve = document.createElement(\'button\'); sauve.textContent = LANGUE === \'en\' ? \'Save\' : \'Enregistrer (Save)\';\n  const usine = document.createElement(\'button\'); usine.textContent = LANGUE === \'en\' ? \'Load Defaults\' : "Réglages d\'usine (Load Defaults)";\n  rappel.onclick = async () => {\n    message(T(\'Rappel du preset \') + choix.value + \'...\');\n    if (await appel(\'/api/harpe/preset/rappeler\', {numero: +choix.value})) {\n      message(\'Preset \' + choix.value + T(\' rappelé.\')); dessine();\n    }\n  };\n  sauve.onclick = async () => {\n    if (!confirm(T(\'Enregistrer les réglages actuels dans le preset \') + choix.value + T(\' ? Son contenu sera remplacé.\'))) return;\n    if (await appel(\'/api/harpe/preset/sauver\', {numero: +choix.value})) {\n      message(T(\'Réglages enregistrés dans le preset \') + choix.value + \'.\');\n    }\n  };\n  usine.onclick = async () => {\n    if (!confirm(T("Remettre la harpe à ses réglages d\'usine ?"))) return;\n    if (await appel(\'/api/harpe/usine\')) { message(T("Réglages d\'usine rappelés.")); dessine(); }\n  };\n  const l = document.createElement(\'div\'); l.className = \'ligne\';\n  [choix, rappel, sauve].forEach(e => l.appendChild(e));\n  c.appendChild(l);\n  const p = document.createElement(\'p\'); p.appendChild(usine); c.appendChild(p);\n  z.appendChild(c);\n}\n\nlet dernier = 0, pause = false, minuterieMoniteur = null;\n\nfunction dessineMoniteur(z) {\n  const c = document.createElement(\'div\'); c.className = \'reglage\';\n  c.innerHTML = \'<div class="ligne"><button id="mpause"></button><button id="meffacer">\' + T(\'Effacer\') + \'</button>\' +\n    \'<label><input type="checkbox" id="mbend"> \' + T(\'Masquer les pitch bends\') + \'</label>\' +\n    \'<span id="mcompte" class="oct"></span></div><div id="journal"></div>\';\n  z.appendChild(c);\n  const bouton = document.getElementById(\'mpause\');\n  bouton.textContent = pause ? T(\'Reprendre\') : \'Pause\';\n  bouton.onclick = () => { pause = !pause; bouton.textContent = pause ? T(\'Reprendre\') : \'Pause\'; };\n  document.getElementById(\'meffacer\').onclick = async () => {\n    await fetch(\'/api/harpe/moniteur/effacer\', {method: \'POST\'});\n    document.getElementById(\'journal\').innerHTML = \'\';\n  };\n  dernier = 0;\n  litMoniteur();\n}\n\nasync function litMoniteur() {\n  if (onglet !== \'Moniteur\' || pause) return;\n  let d;\n  try { d = await (await fetch(\'/api/harpe/moniteur?depuis=\' + dernier)).json(); } catch (e) { return; }\n  const j = document.getElementById(\'journal\');\n  if (!j || d.erreur) return;\n  const masque = document.getElementById(\'mbend\').checked;\n  d.messages.forEach(m => {\n    if (masque && m.texte.startsWith(\'Pitch bend\')) return;\n    const l = document.createElement(\'div\');\n    l.innerHTML = \'<span class="oct">\' + m.t.toFixed(2) + \' s  \' + m.octets + \'</span><br>\' + m.texte;\n    j.prepend(l);\n  });\n  while (j.childNodes.length > 200) j.removeChild(j.lastChild);\n  if (d.messages.length) j.scrollTop = 0;\n  dernier = d.dernier;\n  document.getElementById(\'mcompte\').textContent = dernier + T(\' messages reçus depuis le démarrage\');\n}\nsetInterval(litMoniteur, 400);\n\nasync function envoie(r, v) {\n  if (await appel(\'/api/harpe/reglage\', {cle: r.cle, valeur: v})) { message((LANGUE === \'en\' && r.nom_manager && !r.nom_manager.startsWith(\'(\') ? r.nom_manager : r.nom) + T(\' : envoyé.\')); dessine(); }\n}\n\ndocument.getElementById(\'lire\').onclick = async () => {\n  message(T(\'Lecture de la harpe…\'));\n  if (await appel(\'/api/harpe/lire\')) {\n    message(T(\'Réglages lus\') + (etat.version ? \' (firmware \' + etat.version + \').\' : \'.\'));\n    dessine();\n  }\n};\n\n(async () => {\n  try { const e = await (await fetch(\'/api/etat\')).json(); if (e.langue) { LANGUE = e.langue; traduirePage(); } } catch (x) {}\n  await appel(\'/api/harpe/reglages\', null, \'GET\'); dessine();\n})();\n</script>\n</body>\n</html>\n'


PAGE_PARAMETRES = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Kromabridge - Paramètres</title>
<style>
  :root { --fond: #11131a; --carte: #1b1f2a; --trait: #2c3242; --texte: #eef1f7;
          --doux: #9aa3b8; --vif: #ffb454; }
  * { box-sizing: border-box; }
  body { margin: 0; padding: 16px; background: var(--fond); color: var(--texte);
         font: 16px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
  header { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 12px; }
  h1 { font-size: 20px; margin: 0; flex: 1 1 auto; }
  button { font: inherit; color: var(--texte); background: var(--carte);
         border: 1px solid var(--trait); border-radius: 10px; padding: 10px 14px; min-height: 44px; }
  .reglage { background: var(--carte); border: 1px solid var(--trait); border-radius: 12px;
             padding: 12px; margin-bottom: 10px; }
  label { display: block; padding: 8px 0; }
  input[type=radio] { width: 22px; height: 22px; vertical-align: middle; margin-right: 8px; }
  .doux { color: var(--doux); }
</style>
</head>
<body>
<header>
  <h1>Paramètres</h1>
  <a href="/"><button>Retour</button></a>
</header>
<div class="reglage">
  <div>Langue / Language</div>
  <label><input type="radio" name="langue" value="fr"> Français</label>
  <label><input type="radio" name="langue" value="en"> English</label>
  <div class="doux" id="etat"></div>
</div>
<div class="reglage">
  <div><span>Nom des notes</span></div>
  <label><input type="radio" name="notes" value="fr"> do, ré, mi, fa, sol, la, si</label>
  <label><input type="radio" name="notes" value="en"> C, D, E, F, G, A, B</label>
  <div class="doux"><span>Le do central (note MIDI 60) s'appelle do4 / C4.</span></div>
  <div class="doux" id="etatnotes"></div>
</div>
<div class="reglage">
  <div><span>Clavier pour attribuer les notes</span></div>
  <select id="clavier" style="width:100%;margin-top:8px;font:inherit;padding:8px;border-radius:8px"></select>
  <div class="doux"><span>Le clavier dont on joue les notes en mode Attribuer au clavier.</span></div>
  <div class="doux" id="etatclavier"></div>
</div>
<div class="reglage">
  <label style="display:block"><input type="checkbox" id="clavierjoue" style="width:22px;height:22px;vertical-align:middle">
    <span>Le clavier joue comme la harpe</span></label>
  <div class="doux"><span>Les notes de ce clavier partent vers la sortie du morceau, telles quelles, sur son canal : pour continuer à jouer au clavier si la harpe a un problème. La case du rayon qui joue la même note s’allume sur la page.</span></div>
  <div class="doux"><span>Si le clavier est aussi le synthé, coupez son mode « Local », sinon chaque note sonnera deux fois.</span></div>
  <div class="doux" id="etatclavierjoue"></div>
</div>
<div class="reglage" id="lignesortie" style="display:none">
  <div><span>Sortie audio du son de la harpe (sur ce PC)</span></div>
  <select id="sortieaudio" style="width:100%;margin-top:8px;font:inherit;padding:8px;border-radius:8px"></select>
  <div class="doux"><span>Utilisée par le mode jeu et par le son du clavier quand ils jouent le son de la harpe sur ce PC.</span></div>
  <div class="doux" id="etatsortie"></div>
</div>
<div class="reglage">
  <div><span>Mode jeu (quand vous jouez de la harpe)</span></div>
  <div style="margin-top:8px"><span>Sortie MIDI des notes de la harpe</span>
    <select id="sortiemidi" style="width:100%;font:inherit;padding:8px;border-radius:8px"></select></div>
  <div style="margin-top:8px"><span>Son de la harpe en plus</span>
    <select id="sonjeu" style="width:100%;font:inherit;padding:8px;border-radius:8px">
      <option value="aucun">Aucun (seulement la sortie MIDI)</option>
      <option value="harpe_pc" id="optjeupc">Sur une sortie audio de ce PC</option>
      <option value="page">Sur l'appareil qui affiche la page (iPad...)</option>
    </select></div>
  <div class="doux" id="etatjeu"></div>
  <div class="doux" id="sanssons" style="display:none"><span>Les sons de la harpe ne sont pas encore fournis dans cette version : le « son de la harpe en plus » reste muet. Ils arriveront avec une prochaine mise à jour.</span></div>
</div>
<div class="reglage">
  <div><span>Piste de contrôle (DAW ou séquenceur)</span></div>
  <label style="display:block;margin:8px 0"><input type="checkbox" id="ctlactif" style="width:22px;height:22px;vertical-align:middle">
    <span>Contrôle par le DAW</span></label>
  <div style="margin:8px 0"><span>Entrée de la piste de contrôle</span>
    <select id="ctlentree" style="width:100%;font:inherit;padding:8px;border-radius:8px"></select></div>
  <div class="doux" id="ctletat"></div>
  <div class="doux" id="ctlport"></div>
  <ul class="doux" style="margin:6px 0;padding-left:20px">
    <li><span>Tout se passe sur le canal MIDI 16.</span></li>
    <li><span>Changement de programme n : charge le morceau n de la liste (1 = le premier).</span></li>
    <li id="ctlouvrir"></li>
    <li id="ctlfermer"></li>
    <li id="ctlparties"></li>
  </ul>
  <div class="doux" id="ctlliste"></div>
  <p><a id="ctlexport" href="/api/controle/exporter"><button>Exporter une piste de contrôle pour le morceau affiché</button></a></p>
  <div class="doux" id="ctljournal"></div>
</div>
<div class="reglage">
  <div><span>Son du clavier (pendant « Créer les rayons au clavier » et « Changer un rayon au clavier »)</span></div>
  <select id="sonclavier" style="width:100%;margin-top:8px;font:inherit;padding:8px;border-radius:8px">
    <option value="aucun">Aucun (le clavier a son propre son)</option>
    <option value="sortie">Sortie du programme (synthé ou logiciel de musique)</option>
    <option value="windows" id="optwindows">Synthé intégré à Windows</option>
    <option value="harpe_pc" id="optharpepc">Son de la harpe, sur une sortie audio de ce PC</option>
    <option value="page">Son de la harpe, sur l'appareil qui affiche la page (iPad...)</option>
  </select>
  <p><button id="essaison">&#9654; <span>Essayer le son</span></button></p>
  <div class="doux" id="etatson"></div>
</div>
<script>
(async () => {
  const d = await (await fetch('/api/etat')).json();
  if (d.langue) { LANGUE = d.langue; traduirePage(); }
  document.querySelectorAll('input[name=langue]').forEach(r => {
    r.checked = (r.value === d.langue);
    r.onchange = async () => {
      const rep = await (await fetch('/api/parametres', {method: 'POST',
        headers: {'Content-Type': 'application/json'}, body: JSON.stringify({langue: r.value})})).json();
      if (rep.erreur) { document.getElementById('etat').textContent = 'Erreur : ' + rep.erreur; return; }
      location.reload();
    };
  });
  document.querySelectorAll('input[name=notes]').forEach(r => {
    r.checked = (r.value === d.notes);
    r.onchange = async () => {
      const rep = await (await fetch('/api/parametres', {method: 'POST',
        headers: {'Content-Type': 'application/json'}, body: JSON.stringify({notes: r.value})})).json();
      document.getElementById('etatnotes').textContent = rep.erreur ? 'Erreur : ' + rep.erreur : T('Enregistré.');
    };
  });
  const c = await (await fetch('/api/claviers')).json();
  const liste = document.getElementById('clavier');
  liste.add(new Option(T('(aucun)'), ''));
  (c.ports || []).forEach(p => liste.add(new Option(p, p)));
  if (c.clavier && ![...liste.options].some(o => o.value === c.clavier)) {
    const o = [...liste.options].find(o => o.value.toLowerCase().includes(c.clavier.toLowerCase()));
    if (o) liste.value = o.value; else liste.add(new Option(c.clavier + T(' (débranché)'), c.clavier));
  }
  if ([...liste.options].some(o => o.value === c.clavier)) liste.value = c.clavier;
  liste.onchange = async () => {
    const rep = await (await fetch('/api/parametres', {method: 'POST',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify({clavier: liste.value})})).json();
    document.getElementById('etatclavier').textContent = rep.erreur ? 'Erreur : ' + rep.erreur : T('Enregistré.');
  };
  const jeu = await (await fetch('/api/jeu')).json();
  const sm = document.getElementById('sortiemidi');
  sm.add(new Option(T('Réglage d’origine') + (jeu.origine ? ' (' + jeu.origine + ')' : ''), ''));
  sm.add(new Option(T('Aucune (les notes ne partent vers aucun synthé)'), '__aucune__'));
  (jeu.sorties || []).forEach(n => sm.add(new Option(n, n)));
  if (jeu.sortie && ![...sm.options].some(o => o.value === jeu.sortie)) sm.add(new Option(jeu.sortie + T(' (débranché)'), jeu.sortie));
  sm.value = jeu.sortie || '';
  const sj = document.getElementById('sonjeu');
  if (!jeu.pc) document.getElementById('optjeupc').remove();
  sj.value = jeu.son_jeu || 'aucun';
  const enreg = async corps => {
    const rep = await (await fetch('/api/parametres', {method: 'POST',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify(corps)})).json();
    document.getElementById('etatjeu').textContent = rep.erreur ? 'Erreur : ' + rep.erreur : T('Enregistré.');
  };
  sm.onchange = () => enreg({sortie_midi: sm.value});
  fetch('/api/sons').then(r => r.json()).then(x => {
    document.getElementById('sanssons').style.display = (x.sons || []).length ? 'none' : '';
  }).catch(() => {});
  sj.onchange = () => { enreg({son_jeu: sj.value}); window.majSortie && window.majSortie(); };
  const ctl = await (await fetch('/api/controle')).json();
  const nm = d.noms || {};
  // Cubase nomme les notes en anglais avec le do central (60) = C3.
  const cubase = n => ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'][n % 12] + (Math.floor(n / 12) - 2);
  const nomNote = n => (nm[n] || n) + ' (' + T('dans le DAW, do central = C3 :') + ' ' + cubase(n) + ')';
  const ce = document.getElementById('ctlentree');
  ce.add(new Option(ctl.pc ? T('Port loopMIDI « Controle Harpe » (par défaut)') : T('Entrée de l’interface MIDI USB du Pi (par défaut)'), ''));
  (ctl.entrees || []).forEach(n => ce.add(new Option(n, n)));
  if (ctl.entree && ![...ce.options].some(o => o.value === ctl.entree)) ce.add(new Option(ctl.entree + T(' (débranché)'), ctl.entree));
  ce.value = ctl.entree || '';
  const majPort = () => {
    document.getElementById('ctlport').textContent = ce.value
      ? T('Les commandes du canal 16 arrivant par cette entrée pilotent la harpe (un clavier ou un séquenceur, par exemple).')
      : (ctl.pc ? T('Dans le DAW, sortie de la piste : le port loopMIDI « Controle Harpe ».')
                : T('Sur le Pi : par l’entrée du câble MIDI.'));
  };
  majPort();
  ce.onchange = async () => {
    await fetch('/api/parametres', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({controle_entree: ce.value})});
    majPort(); majCtl();
  };
  document.getElementById('ctlouvrir').textContent = T('Note') + ' ' + nomNote(0) + ' : ' + T('ouvrir la harpe.');
  document.getElementById('ctlfermer').textContent = T('Note') + ' ' + nomNote(1) + ' : ' + T('fermer la harpe.');
  document.getElementById('ctlparties').textContent = T('Notes') + ' ' + nomNote(12) + ', ' + nomNote(13) + ', ' +
    nomNote(14) + '... : ' + T('parties A, B, C... du morceau.');
  document.getElementById('ctlliste').textContent = T('Morceaux :') + ' ' +
    ctl.morceaux.map((m, i) => (i + 1) + ' = ' + m).join(' ; ');
  const ca = document.getElementById('ctlactif');
  ca.checked = !!ctl.actif;
  ca.onchange = async () => {
    await fetch('/api/parametres', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({controle_actif: ca.checked})});
    majCtl();
  };
  const majCtl = async () => {
    const c2 = await (await fetch('/api/controle')).json();
    document.getElementById('ctletat').textContent = !c2.actif
      ? T('Contrôle désactivé : les commandes reçues sont ignorées.')
      : (c2.connecte ? T('Port de contrôle : connecté.') : T('Port de contrôle : non trouvé.'));
    document.getElementById('ctljournal').innerHTML = (c2.journal || []).map(x => x.replace(/</g, '&lt;')).join('<br>');
  };
  majCtl(); setInterval(majCtl, 1000);
  const son = document.getElementById('sonclavier');
  if (!c.pc) { document.getElementById('optwindows').remove(); document.getElementById('optharpepc').remove(); }
  son.value = c.son_clavier || (c.pc ? 'windows' : 'aucun');
  const cj = document.getElementById('clavierjoue');
  cj.checked = !!c.clavier_joue;
  cj.onchange = async () => {
    const rep = await (await fetch('/api/parametres', {method: 'POST',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify({clavier_joue: cj.checked})})).json();
    document.getElementById('etatclavierjoue').textContent = rep.erreur ? 'Erreur : ' + rep.erreur : T('Enregistré.');
  };
  const sa = document.getElementById('sortieaudio');
  const majSortie = window.majSortie = async () => {
    const visible = son.value === 'harpe_pc' || document.getElementById('sonjeu').value === 'harpe_pc';
    document.getElementById('lignesortie').style.display = visible ? '' : 'none';
    if (!visible || sa.options.length) return;
    const r = await (await fetch('/api/audio/sorties')).json();
    sa.add(new Option(T('Sortie par défaut de Windows'), ''));
    (r.sorties || []).forEach(n => sa.add(new Option(n, n)));
    sa.value = r.sortie || '';
  };
  majSortie();
  son.onchange = async () => {
    const rep = await (await fetch('/api/parametres', {method: 'POST',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify({son_clavier: son.value})})).json();
    document.getElementById('etatson').textContent = rep.erreur ? 'Erreur : ' + rep.erreur
      : (son.value === 'page' ? T('Enregistré. Sur la page principale, touchez « Activer le son » une fois.') : T('Enregistré.'));
    majSortie();
  };
  sa.onchange = async () => {
    const rep = await (await fetch('/api/parametres', {method: 'POST',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify({sortie_audio: sa.value})})).json();
    document.getElementById('etatsortie').textContent = rep.erreur ? 'Erreur : ' + rep.erreur : T('Enregistré.');
  };
  document.getElementById('essaison').onclick = async () => {
    if (son.value === 'page' && !(window.harpeSon && window.harpeSon.pret)) {
      document.getElementById('etatson').textContent = T('Pour ce choix, activez le son sur la page principale, puis essayez depuis elle.');
      return;
    }
    await fetch('/api/son/essai', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({note: 60})});
    setTimeout(async () => {
      const r = await (await fetch('/api/audio/sorties')).json();
      if (r.erreur_son) document.getElementById('etatson').textContent = 'Erreur : ' + r.erreur_son;
    }, 600);
  };
})();
</script>
</body>
</html>
"""


_I18N = '<script>\nlet LANGUE = "fr";\nconst EN = {"Kromabridge": "Kromabridge", "Kromabridge - Réglages de la harpe": "Kromabridge - Harp settings", "Kromabridge - Paramètres": "Kromabridge - Settings", "Recharger": "Reload", "Nouveau morceau": "New song", "Renommer": "Rename", "Supprimer": "Delete", "Mémoriser la harpe dans ce morceau": "Store harp settings in this song", "Harpe mémorisée (remplacer)": "Harp settings stored (replace)", "Silence": "Silence", "Apprendre": "Learn", "Terminer": "Finish", "Titre": "Title", "Partir de": "Start from", "Nombre de rayons": "Number of beams", "Une copie reprend tout du morceau choisi (notes, canal, réglages de la harpe) ; l\'original reste intact.": "A copy takes everything from the chosen song (notes, channel, harp settings); the original is left untouched.", "Créer": "Create", "Annuler": "Cancel", "Ouvrir la harpe": "Open the harp", "Fermer la harpe": "Close the harp", "Réglages de la harpe": "Harp settings", "Paramètres": "Settings", "Paramètres": "Settings", "Canal MIDI de sortie": "MIDI output channel", "Animation d\'ouverture": "Opening animation", "pause": "pause", "ouverture": "opening", "Tous les rayons :": "All beams:", "Coupez un rayon : il s\'allume ici. En apprentissage, coupez-les un par un de gauche a droite.": "Cut a beam: it lights up here. While learning, cut them one by one from left to right.", "Connexion directe": "Direct connection", "Éteindre le Pi": "Shut down the Pi", "Revenir au Wi-Fi de la maison": "Back to home Wi-Fi", "Connexion directe : réseau ": "Direct connection: network ", ", page sur ": ", page at ", "Wi-Fi de la maison : ": "Home Wi-Fi: ", "aucun": "none", "Revenir au morceau tel qu\'il est enregistré (notes et réglages de la harpe) ?": "Go back to the song as saved (notes and harp settings)?", "Un morceau vierge": "A blank song", "Une copie de : ": "A copy of: ", "Donnez un titre au morceau.": "Give the song a title.", "Impossible de supprimer le dernier morceau.": "The last song cannot be deleted.", "Supprimer le morceau": "Delete the song", "(Son fichier est rangé dans le dossier corbeille.)": "(Its file is moved to the corbeille (trash) folder.)", "Titre du morceau :": "Song title:", "Mémoriser dans ce morceau tous les réglages actuels de la harpe ? Ils remplaceront ceux déjà mémorisés.": "Store all the current harp settings in this song? They will replace the ones already stored.", "Apprentissage en cours : ": "Learning: ", " rayons repérés.": " beams found.", " rayons allumés": " beams on", "Rayon": "Beam", "Touchez pour choisir la note": "Tap to choose the note", "Éteindre le Pi ?": "Shut down the Pi?", "Le Pi va rejoindre le Wi-Fi de la maison. Reconnectez votre appareil au Wi-Fi de la box. Continuer ?": "The Pi will join the home Wi-Fi. Reconnect your device to your router\'s Wi-Fi. Continue?", "Le Pi va quitter la box et créer le réseau Kromalaser (mot de passe : kromalaser). Connectez votre appareil à ce réseau, puis ouvrez http://10.42.0.1:8080. Continuer ?": "The Pi will leave your router and create the Kromalaser network (password: kromalaser). Connect your device to it, then open http://10.42.0.1:8080. Continue?", "Retour": "Back", "Lire la harpe": "Read the harp", "Appuyez sur Lire la harpe pour afficher ses réglages actuels.": "Press Read the harp to show its current settings.", "Erreur : ": "Error: ", "oui": "yes", "non": "no", "Verrouillé à 0 : la transposition se fait dans Kromabridge.": "Locked at 0: transposition is done in Kromabridge.", "Rappel du preset ": "Recalling preset ", " rappelé.": " recalled.", "Enregistrer les réglages actuels dans le preset ": "Save the current settings to preset ", " ? Son contenu sera remplacé.": "? Its content will be replaced.", "Réglages enregistrés dans le preset ": "Settings saved to preset ", "Remettre la harpe à ses réglages d\'usine ?": "Reset the harp to its factory settings?", "Réglages d\'usine rappelés.": "Factory settings loaded.", "Effacer": "Clear", "Masquer les pitch bends": "Hide pitch bends", "Reprendre": "Resume", " messages reçus depuis le démarrage": " messages received since startup", " : envoyé.": ": sent.", "Lecture de la harpe…": "Reading the harp...", "Réglages lus": "Settings read", "Modulation de couleur sur": "Color modulation on", "Le do central (note MIDI 60) s\'appelle do4 / C4.": "Middle C (MIDI note 60) is called C4.", "donnez un titre au morceau": "give the song a title", "impossible de supprimer le dernier morceau": "the last song cannot be deleted", "la harpe n\'a pas répondu à la lecture des réglages": "the harp did not answer the settings read", "la harpe n\'est pas reliée au Pi": "the harp is not connected to the Pi", "réglages de la harpe indisponibles": "harp settings unavailable", "commande de la harpe indisponible": "harp control unavailable", "version PC : ferme simplement la fenêtre du programme": "PC version: just close the program window", "aucun Wi-Fi de maison enregistré": "no home Wi-Fi saved", "aucun rayon appris : coupez les rayons un par un": "no beam learned: cut the beams one by one", "le canal doit être entre 1 et 16": "the channel must be between 1 and 16"};\nfunction T(s) { return (LANGUE === "en" && EN[s] !== undefined) ? EN[s] : s; }\nfunction traduirePage() {\n  if (LANGUE !== "en") return;\n  document.documentElement.lang = "en";\n  if (EN[document.title] !== undefined) document.title = EN[document.title];\n  const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);\n  const n = []; while (w.nextNode()) n.push(w.currentNode);\n  n.forEach(x => { const t = x.nodeValue.trim(); if (t && EN[t] !== undefined) x.nodeValue = x.nodeValue.replace(t, EN[t]); });\n}\n</script>\n'
_I18N = _I18N.replace("function T(s)", 'Object.assign(EN, ' + '{"Cliquez dans l’aperçu pour placer le curseur : « Ajouter une commande » l’ajoute à cet endroit. Faites glisser un repère pour le déplacer.": "Click in the preview to place the cursor: « Add a command » adds it there. Drag a marker to move it."}' + ');' + "function T(s)", 1)
_I18N = _I18N.replace("function T(s)", 'Object.assign(EN, ' + '{"Parties inchangées, gardées avec vos retouches :": "Unchanged parts, kept with your edits:"}' + ');' + "function T(s)", 1)
_I18N = _I18N.replace("function T(s)", 'Object.assign(EN, ' + '{"Exporter des morceaux": "Export songs", "Importer des morceaux": "Import songs", "Le fichier se range dans vos téléchargements. Sur l’autre appareil (PC ou Pi), ouvrez-le avec « Importer des morceaux ».": "The file goes to your downloads. On the other device (PC or Pi), open it with « Import songs ».", "Ce morceau": "This song", "Tous les morceaux": "All songs", "ce fichier n’est pas un export de morceaux Kromalaser": "this file is not a Kromalaser song export", "ce fichier n\'est pas un export de morceaux Kromalaser": "this file is not a Kromalaser song export", "Ces morceaux existent déjà ici :": "These songs already exist here:", "OK : les remplacer (les anciens vont dans la corbeille). Annuler : garder les deux (les nouveaux seront renommés).": "OK: replace them (the old ones go to the corbeille (trash) folder). Cancel: keep both (the new ones will be renamed).", "Ajoutés :": "Added:", "Remplacés :": "Replaced:", "Renommés :": "Renamed:", "Aucun morceau dans ce fichier.": "No song in this file."}' + ');' + "function T(s)", 1)
_I18N = _I18N.replace("function T(s)", 'Object.assign(EN, ' + '{"Son du synthé à l’ouverture du morceau :": "Synth sound when the song is loaded:", "programme": "program", "banque MSB": "bank MSB", "LSB": "LSB", "(vide = rien envoyé ; programme de 1 à 128)": "(empty = nothing sent; program 1 to 128)", "le programme doit être entre 1 et 128": "the program must be between 1 and 128", "la banque doit être entre 0 et 127": "the bank must be between 0 and 127"}' + ');' + "function T(s)", 1)
_I18N = _I18N.replace("function T(s)", 'Object.assign(EN, ' + '{"Changer le son du synthé à l’ouverture du morceau :": "Change the synth sound when the song is loaded:", "(décoché ou vide = le son du synthé n’est pas touché ; programme de 1 à 128)": "(unticked or empty = the synth sound is left alone; program 1 to 128)"}' + ');' + "function T(s)", 1)
_I18N = _I18N.replace("function T(s)", 'Object.assign(EN, ' + '{"Numérotation des sons du synthé": "Synth sound numbering", "1 à 128 (la plupart des synthés et des VST)": "1 to 128 (most synths and VSTs)", "0 à 127 (synthés qui comptent à partir de 0)": "0 to 127 (synths counting from 0)", "11 à 88 (8 banques de 8 sons, numérotées banque puis son)": "11 to 88 (8 banks of 8 sounds, numbered bank then sound)", "Pour taper dans « Changer le son du synthé » le numéro affiché sur votre synthé.": "So you can type in « Change the synth sound » the number shown on your synth.", "décoché ou vide = le son du synthé n’est pas touché": "unticked or empty = the synth sound is left alone", "sons 0 à 127": "sounds 0 to 127", "sons 11 à 88, chiffres de 1 à 8": "sounds 11 to 88, digits 1 to 8", "sons 1 à 128": "sounds 1 to 128", "Ce numéro n’existe pas avec la numérotation choisie :": "This number does not exist with the chosen numbering:", "numérotation inconnue": "unknown numbering"}' + ');' + "function T(s)", 1)
_I18N = _I18N.replace("function T(s)", 'Object.assign(EN, ' + '{"Aucune (les notes ne partent vers aucun synthé)": "None (notes go to no synth)"}' + ');' + "function T(s)", 1)
_I18N = _I18N.replace("function T(s)", 'Object.assign(EN, ' + '{"Si vous n’entendez rien ensuite : vérifiez que l’appareil n’est pas en mode silencieux.": "If you then hear nothing: check that the device is not in silent mode."}' + ');' + "function T(s)", 1)
_I18N = _I18N.replace("function T(s)", 'Object.assign(EN, ' + '{"Morceau": "Song", "Ouvrir…": "Open…", "Enregistrer sous…": "Save as…", "Enregistrer sous": "Save as", "Apprentissage": "Learning", "Panique": "Panic", "Paramètres de l’application": "App settings", "Paramètres de la harpe": "Harp settings", "Rétablir les réglages mémorisés": "Restore stored settings", "Ouvrir un morceau": "Open a song", "Morceau précédent": "Previous song", "Morceau suivant": "Next song", "Couper toutes les notes qui sonnent encore": "Cut all notes still sounding", "Éteindre le Pi": "Shut down the Pi", "Paramètres": "Settings", "Réglages de harpe non mémorisés : ": "Harp settings not stored: ", "Remettre la harpe telle qu’elle est mémorisée dans ce morceau ?": "Put the harp back as stored in this song?", "Son de la harpe activé sur cet appareil": "Harp sound enabled on this device", "Touchez 🔇 en haut pour entendre la harpe sur cet appareil. Si vous n’entendez rien ensuite : vérifiez que l’appareil n’est pas en mode silencieux.": "Tap 🔇 at the top to hear the harp on this device. If you then hear nothing: check that the device is not in silent mode.", "Attribution au clavier : ON": "Assign from keyboard: ON"}' + ');' + "function T(s)", 1)
_I18N = _I18N.replace("function T(s)", 'Object.assign(EN, ' + '{"Listes": "Lists", "Gérer les listes…": "Manage lists…", "Liste précédente": "Previous list", "Liste suivante": "Next list", "Tous les morceaux (liste par défaut)": "All songs (default list)", "Nouvelle liste": "New list", "Renommer la liste": "Rename list", "Supprimer la liste": "Delete list", "Valider": "Apply", "Fermer": "Close", "Ajouter": "Add", "hors liste": "not in list", "Pistes de contrôle": "Control tracks", "Leur ordre donne leur numéro, utilisé par les pistes de contrôle. Un nouveau morceau va toujours à la fin.": "Their order gives their number, used by control tracks. A new song always goes at the end.", "L’ordre dans lequel ◀ ▶ passent les morceaux. Les numéros ne changent pas.": "The order in which ◀ ▶ go through the songs. Numbers do not change.", "Ces morceaux ont changé de numéro : leurs pistes de contrôle déjà exportées ne chargent plus le bon morceau.": "These songs changed number: their control tracks already exported no longer load the right song.", "Réexporter maintenant toutes les pistes de contrôle ?": "Re-export all control tracks now?", "Nom de la nouvelle liste :": "Name of the new list:", "Nouveau nom de la liste :": "New name of the list:", "Les morceaux, eux, restent.": "The songs themselves stay.", "aucun morceau n\'a de piste de contrôle mémorisée": "no song has a stored control track", "donnez un nom à la liste": "give the list a name", "Enregistré.": "Saved."}' + ');' + "function T(s)", 1)
_I18N = _I18N.replace("function T(s)", 'Object.assign(EN, ' + '{"Corbeille…": "Trash…", "Corbeille": "Trash", "Restaurer": "Restore", "Supprimer définitivement": "Delete permanently", "Vider la corbeille": "Empty the trash", "La corbeille est vide.": "The trash is empty.", "supprimé le": "deleted on", "Il ne pourra plus être récupéré.": "It can no longer be recovered.", "Vider la corbeille ? Tous les morceaux qu’elle contient seront supprimés définitivement.": "Empty the trash? All the songs in it will be deleted permanently.", "Les morceaux supprimés. « Restaurer » remet un morceau à la fin de la liste : aucun numéro ne change.": "Deleted songs. « Restore » puts a song back at the end of the list: no number changes.", "morceau introuvable dans la corbeille": "song not found in the trash", "(Il va dans la corbeille : menu Morceau, Corbeille…)": "(It goes to the trash: Song menu, Trash…)"}' + ');' + "function T(s)", 1)
_I18N = _I18N.replace("function T(s)", 'Object.assign(EN, ' + '{"Ce morceau n’a pas été créé par un import MIDI.": "This song was not created by a MIDI import."}' + ');' + "function T(s)", 1)
_I18N = _I18N.replace("function T(s)", 'Object.assign(EN, ' + '{"Mémoriser dans ce morceau": "Store in this song", "Mémoriser dans tous les morceaux": "Store in all songs", "Seuls les réglages modifiés (en orange) sont mémorisés. Les réglages cochés « commun à tous les morceaux » (largeur, sensibilité…) sont mémorisés une seule fois pour tous les morceaux.": "Only changed settings (in orange) are stored. Settings ticked « common to all songs » (width, sensitivity…) are stored once for all songs.", "réglage(s) non mémorisé(s) :": "setting(s) not stored:", "Tout est mémorisé.": "Everything is stored.", "Mémorisé dans ce morceau.": "Stored in this song.", "Mémorisé dans tous les morceaux.": "Stored in all songs.", "Mémoriser les réglages modifiés dans tous les morceaux ? (Le nombre de rayons reste propre à chaque morceau.)": "Store the changed settings in all songs? (The number of beams stays specific to each song.)", "Remettre la harpe telle qu’elle est mémorisée ?": "Put the harp back as stored?", "Réglages mémorisés rétablis.": "Stored settings restored.", "commun à tous les morceaux": "common to all songs", "Réglage commun à tous les morceaux.": "Setting common to all songs.", "Réglage propre à chaque morceau.": "Setting specific to each song.", "ce réglage ne peut pas être commun à tous les morceaux": "this setting cannot be common to all songs", "valeur inconnue : lisez d\'abord la harpe": "unknown value: read the harp first"}' + ');' + "function T(s)", 1)
_I18N = _I18N.replace("function T(s)", 'Object.assign(EN, ' + '{"Fermer les réglages": "Close settings", "Paramètres de l’application": "App settings"}' + ');' + "function T(s)", 1)
_I18N = _I18N.replace("function T(s)", 'Object.assign(EN, ' + '{"Attention : réglages de la harpe non mémorisés": "Warning: harp settings not stored", "Quitter les réglages sans les mémoriser ?": "Leave the settings without storing them?"}' + ');' + "function T(s)", 1)
_I18N = _I18N.replace("function T(s)", 'Object.assign(EN, ' + '{"Ajouter une note (accord)": "Add a note (chord)", "Retirer cette note": "Remove this note", "8 notes au plus par rayon": "8 notes at most per beam"}' + ');' + "function T(s)", 1)
_I18N = _I18N.replace("function T(s)", 'Object.assign(EN, ' + '{"Accord": "Chord", "Faire jouer un accord à ce rayon": "Make this beam play a chord", "Accord tout fait : la note du rayon en est la fondamentale.": "Ready-made chord: the beam\'s note is its root.", "Appliquer": "Apply", "Ou note par note :": "Or note by note:", "Ajouter une note": "Add a note", "Supprimer l\'accord": "Delete the chord", "Accord du rayon": "Chord of beam", "Note du rayon": "Beam note", "majeur": "major", "mineur": "minor", "7e": "7th", "7e majeure": "major 7th", "mineur 7e": "minor 7th", "quinte seule": "fifth only", "diminué": "diminished", "augmenté": "augmented"}' + ');' + "function T(s)", 1)
_I18N = _I18N.replace("function T(s)", 'Object.assign(EN, ' + '{"Créer les rayons au clavier": "Create beams from keyboard", "Changer un rayon au clavier": "Change a beam from keyboard", "Terminer le changement des rayons": "Stop changing beams", "Créer les rayons au clavier : jouez au clavier les notes du morceau, dans n’importe quel ordre, puis touchez Terminer : elles deviendront les rayons, du grave à l’aigu.": "Create beams from keyboard: play the song\'s notes on the keyboard, in any order, then tap Finish: they will become the beams, from low to high.", "Changer un rayon au clavier : coupez un rayon, puis jouez sur le clavier la note qu’il doit jouer. Touchez ce bandeau pour arrêter.": "Change a beam from keyboard: cut a beam, then play on the keyboard the note it should play. Tap this banner to stop."}' + ');' + "function T(s)", 1)
_I18N = _I18N.replace("function T(s)", 'Object.assign(EN, ' + '{"Changer un rayon au clavier : coupez un rayon (ou touchez sa tuile), puis jouez sur le clavier la note qu’il doit jouer. Touchez ce bandeau pour arrêter.": "Change a beam from keyboard: cut a beam (or tap its tile), then play on the keyboard the note it should play. Tap this banner to stop."}' + ');' + "function T(s)", 1)
_I18N = _I18N.replace("function T(s)", 'Object.assign(EN, ' + '{"Garder les changements": "Keep the changes", "Annuler les changements": "Undo the changes", "Changer un rayon au clavier : coupez un rayon (ou touchez sa tuile), puis jouez sur le clavier la note qu’il doit jouer. Chaque note change le rayon tout de suite ; « Annuler les changements » remet le morceau comme avant.": "Change a beam from keyboard: cut a beam (or tap its tile), then play on the keyboard the note it should play. Each note changes the beam at once; « Undo the changes » puts the song back as before.", "Remettre tous les rayons du morceau comme ils étaient avant ?": "Put all the song\'s beams back as they were before?"}' + ');' + "function T(s)", 1)
_I18N = _I18N.replace("function T(s)", 'Object.assign(EN, ' + '{"Couleurs des rayons": "Beam colours", "Les couleurs suivent :": "Colours follow:", "le toucher": "touch", "les tons et demi-tons": "tones and semitones", "Rayons au repos": "Beams at rest", "Rayons touchés": "Touched beams", "Tons (do, ré, mi…)": "Tones (C, D, E…)", "Demi-tons (do#, ré#…)": "Semitones (C#, D#…)", "Appuyez sur Lire la harpe pour afficher ses couleurs actuelles.": "Press Read the harp to show its current colours.", "Les tons et demi-tons sont ceux de la harpe elle-même : ce choix ne fonctionne qu’avec le morceau Gamme.": "Tones and semitones are the harp\'s own: this choice only works with the Scale song.", "Charger le morceau Gamme": "Load the Scale song", "ton touché | demi-ton touché": "touched tone | touched semitone", "pas de changement": "no change", "Intensité": "Intensity", "Tous les lasers sont pris par les tons et demi-tons : le toucher ne peut rien changer.": "All lasers are used by tones and semitones: touch cannot change anything.", "Réglages détaillés des lasers": "Detailed laser settings", "réglés automatiquement par la carte ci-dessus": "set automatically by the card above", "Couleur ajoutée au toucher": "Colour added on touch", "(déjà pris par les tons ou demi-tons, ignoré pour le toucher)": "(already used by tones or semitones, ignored for touch)", "Couleurs envoyées à la harpe.": "Colours sent to the harp.", "Impossible exactement : un laser ne peut pas avoir deux puissances différentes": "Not exactly possible: a laser cannot have two different powers", "La harpe utilisera la plus forte (voir l’aperçu).": "The harp will use the stronger one (see the preview).", "Rouge": "Red", "Vert": "Green", "Bleu": "Blue", "Orange": "Orange", "Jaune": "Yellow", "Cyan": "Cyan", "Violet": "Purple", "Rose": "Pink", "Blanc": "White", "Éteint": "Off", "Aucun": "None", "Tous": "All", "Tons": "Tones", "Demi-tons": "Semitones", "Touchés": "Touched", "Non touchés": "Untouched", "Morceau Gamme : chaque rayon joue exactement la note envoyée par la harpe (gamme chromatique). Seul le nombre de rayons se règle.": "Scale song: each beam plays exactly the note sent by the harp (chromatic scale). Only the number of beams can be set.", "le morceau Gamme joue les notes de la harpe telles quelles : ses notes ne se modifient pas": "the Scale song plays the harp\'s notes as they are: its notes cannot be changed", "le morceau Gamme ne peut pas être renommé": "the Scale song cannot be renamed", "le morceau Gamme ne peut pas être supprimé": "the Scale song cannot be deleted"}' + ');' + "function T(s)", 1)
_I18N = _I18N.replace("function T(s)", 'Object.assign(EN, ' + '{"Ranger du grave à l’aigu": "Sort from low to high"}' + ');' + "function T(s)", 1)
_I18N = _I18N.replace("function T(s)", 'Object.assign(EN, ' + '{"Modifier l’import": "Edit the import", "Parties en mesures": "Parts in bars", "Créer les parties": "Create the parts", "Mettre à jour le morceau": "Update the song", "Import rouvert : modifiez ce que vous voulez, puis « Mettre à jour le morceau » ou exportez la piste de contrôle.": "Import reopened: change what you want, then « Update the song » or export the control track.", "Écrivez les parties sous la forme 1-5, 6-30, 31-38": "Write the parts as 1-5, 6-30, 31-38", "Parties non créées (aucune note ou plus de 15) :": "Parts not created (no note or more than 15):", "Sélectionnez-les à la main pour décocher des notes.": "Select them by hand to untick some notes.", "Parties créées. Vérifiez-les, puis « Créer le morceau ».": "Parts created. Check them, then « Create the song ».", "Morceau mis à jour :": "Song updated:", "Tout est mémorisé : vous pourrez rouvrir cet import avec « Modifier l’import ».": "Everything is stored: you can reopen this import with « Edit the import ».", "ce morceau n\'a pas d\'import mémorisé": "this song has no stored import"}' + ');' + "function T(s)", 1)
_I18N = _I18N.replace("function T(s)", 'Object.assign(EN, ' + '{"Dans le DAW, le morceau commence à la mesure": "In the DAW, the song starts at bar", "(toutes les commandes suivent)": "(all commands follow)"}' + ');' + "function T(s)", 1)
_I18N = _I18N.replace("function T(s)", 'Object.assign(EN, ' + '{"Harpe injoignable": "Harp unreachable", "elle ne répond plus (lampe rouge ?). Éteins-la puis rallume-la ; le programme la retrouvera tout seul.": "it no longer answers (red light?). Switch it off and on again; the program will find it by itself."}' + ');' + "function T(s)", 1)
_I18N = _I18N.replace("function T(s)", 'Object.assign(EN, ' + '{"Commandes de la harpe": "Harp commands", "(pour la piste de contrôle)": "(for the control track)", "Ajouter une commande": "Add a command", "Replacer automatiquement": "Place automatically", "Exporter la piste de contrôle": "Export the control track", "Chargement du morceau": "Song load", "Ouverture": "Opening", "Fermeture": "Closing", "mesure": "bar", "temps": "beat", "Ouv.": "Open", "Ferm.": "Close", "Piste de contrôle exportée : glissez-la dans votre DAW, au début du projet, à côté du morceau.": "Control track exported: drag it into your DAW, at the start of the project, next to the song.", "crée d’abord le morceau": "create the song first"}' + ');' + "function T(s)", 1)
_I18N = _I18N.replace("function T(s)", 'Object.assign(EN, ' + '{"Transition animée entre les parties": "Animated transition between parts", "la durée de transition doit être entre 0 et 10 secondes": "the transition time must be between 0 and 10 seconds"}' + ');' + "function T(s)", 1)
_I18N = _I18N.replace("function T(s)", 'Object.assign(EN, ' + '{"Contrôle par le DAW": "Control from the DAW", "Contrôle désactivé : les commandes reçues sont ignorées.": "Control disabled: received commands are ignored.", "dans le DAW, do central = C3 :": "in the DAW, middle C = C3:", "Sur une sortie audio de ce PC": "On an audio output of this PC", "Sortie audio du son de la harpe (sur ce PC)": "Audio output for the harp sound (on this PC)", "Utilisée par le mode jeu et par le son du clavier quand ils jouent le son de la harpe sur ce PC.": "Used by play mode and keyboard sound when they play the harp sound on this PC."}' + ');' + "function T(s)", 1)
_I18N = _I18N.replace("function T(s)", 'Object.assign(EN, ' + '{"Mode jeu (quand vous jouez de la harpe)": "Play mode (when you play the harp)", "Sortie MIDI des notes de la harpe": "MIDI output of the harp notes", "Son de la harpe en plus": "Harp sound as well", "Aucun (seulement la sortie MIDI)": "None (MIDI output only)", "Sur une sortie audio de ce PC (celle choisie plus bas)": "On an audio output of this PC (the one chosen below)", "Sur l\'appareil qui affiche la page (iPad...)": "On the device showing the page (iPad...)", "Réglage d’origine": "Original setting"}' + ');' + "function T(s)", 1)
_I18N = _I18N.replace("function T(s)", 'Object.assign(EN, ' + '{"Son de la harpe, sur une sortie audio de ce PC": "Harp sound, on an audio output of this PC", "Son de la harpe, sur l\'appareil qui affiche la page (iPad...)": "Harp sound, on the device showing the page (iPad...)", "Sortie audio": "Audio output", "Essayer le son": "Try the sound", "Sortie par défaut de Windows": "Windows default output", "Enregistré. Sur la page principale, touchez « Activer le son » une fois.": "Saved. On the main page, tap \\\\u00ab Enable sound \\\\u00bb once.", "Pour ce choix, activez le son sur la page principale, puis essayez depuis elle.": "For this choice, enable the sound on the main page and try from there.", "Activer le son de la harpe sur cet appareil": "Enable the harp sound on this device", "Son indisponible :": "Sound unavailable:"}' + ');' + "function T(s)", 1)
_I18N = _I18N.replace("function T(s)", 'Object.assign(EN, ' + '{"Piste de contrôle (DAW ou séquenceur)": "Control track (DAW or sequencer)", "Tout se passe sur le canal MIDI 16.": "Everything happens on MIDI channel 16.", "Changement de programme n : charge le morceau n de la liste (1 = le premier).": "Program change n: loads song n of the list (1 = the first one).", "Dans le DAW, sortie de la piste : le port loopMIDI « Controle Harpe ».": "In the DAW, track output: the loopMIDI port « Controle Harpe ».", "Sur le Pi : par l’entrée du câble MIDI.": "On the Pi: through the MIDI cable input.", "Note": "Note", "Notes": "Notes", "ouvrir la harpe.": "open the harp.", "fermer la harpe.": "close the harp.", "parties A, B, C... du morceau.": "parts A, B, C... of the song.", "Morceaux :": "Songs:", "Exporter une piste de contrôle pour le morceau affiché": "Export a control track for the current song", "Port de contrôle : connecté.": "Control port: connected.", "Port de contrôle : non trouvé.": "Control port: not found."}' + ');' + "function T(s)", 1)
_I18N = _I18N.replace("function T(s)", 'Object.assign(EN, ' + '{"Son du clavier (pendant « Créer les rayons au clavier » et « Changer un rayon au clavier »)": "Keyboard sound (during « Create beams from keyboard » and « Change a beam from keyboard »)", "Aucun (le clavier a son propre son)": "None (the keyboard has its own sound)", "Sortie du programme (synthé ou logiciel de musique)": "Program output (synth or music software)", "Synthé intégré à Windows": "Windows built-in synth"}' + ');' + "function T(s)", 1)
_I18N = _I18N.replace("function T(s)", 'Object.assign(EN, ' + '{"Garder comme partie et continuer": "Keep as a part and continue", "Plus de 15 notes : la harpe a 15 rayons au plus. Retirez des notes, ou faites l’apprentissage en plusieurs parties : gardez les notes de la première partie avec le bouton ci-dessous, puis jouez la suivante.": "More than 15 notes: the harp has 15 beams at most. Remove some notes, or learn in several parts: keep the first part\'s notes with the button below, then play the next one.", "Parties gardées :": "Parts kept:", "(plus de 15 notes : il faudra faire plusieurs parties)": "(more than 15 notes: you will need several parts)"}' + ');' + "function T(s)", 1)
_I18N = _I18N.replace("function T(s)", 'Object.assign(EN, ' + '{"Apprentissage au clavier": "Learning from the keyboard", "Notes jouées, de la plus grave à la plus aiguë (un appui retire une note) :": "Notes played, lowest to highest (tap to remove a note):", "Remplacer les notes du morceau affiché": "Replace the notes of the current song", "Remplacer les notes de": "Replace the notes of", "Continuer à jouer": "Keep playing", "Annuler l\'apprentissage": "Cancel learning", "notes": "notes", "Apprentissage : jouez vos notes au clavier.": "Learning: play your notes on the keyboard.", "15 au plus, retirez des notes.": "15 at most, remove some notes.", "Coupez un rayon : il s\'allume ici.": "Cut a beam: it lights up here."}' + ');' + "function T(s)", 1)
_I18N = _I18N.replace("function T(s)", 'Object.assign(EN, ' + '{"Cochez d’abord cette piste pour y sélectionner une partie.": "Tick this track first to select a part on it."}' + ');' + "function T(s)", 1)
_I18N = _I18N.replace("function T(s)", 'Object.assign(EN, ' + '{"Parties :": "Parts:", "Ajouter comme partie": "Add as a part", "Parties du morceau :": "Song parts:", "Retirer cette partie": "Remove this part", "mes.": "bars", "tout": "all", "Partie ajoutée. Sélectionnez la suivante, ou cliquez sur Créer le morceau.": "Part added. Select the next one, or click Create the song.", "partie introuvable": "part not found", "aucune partie": "no part"}' + ');' + "function T(s)", 1)
_I18N = _I18N.replace("function T(s)", 'Object.assign(EN, ' + '{"Zoom": "Zoom", "Caler sur les mesures": "Snap to bars", "De la mesure": "From bar", "à la mesure": "to bar", "(tout le fichier)": "(whole file)"}' + ');' + "function T(s)", 1)
_I18N = _I18N.replace("function T(s)", 'Object.assign(EN, ' + '{"Touchez pour écouter à partir d’ici, glissez pour sélectionner une partie": "Tap to play from here, drag to select a part", "Parties détectées :": "Detected parts:", "Partie": "Part", "Sélection :": "Selection:", "Écouter la sélection": "Play the selection", "Effacer la sélection": "Clear the selection", "Aucune note dans la sélection.": "No notes in the selection.", "Morceau créé :": "Song created:", "Sélectionne la partie suivante, ou ferme la fenêtre.": "Select the next part, or close the window."}' + ');' + "function T(s)", 1)
_I18N = _I18N.replace("function T(s)", 'Object.assign(EN, ' + '{"Arrêter l\'écoute": "Stop playback", "Écouter cette piste": "Play this track", "Touchez pour ecouter a partir d ici": "Tap to play from here", "aucun fichier MIDI chargé": "no MIDI file loaded"}' + ');' + "function T(s)", 1)
_I18N = _I18N.replace("function T(s)", 'Object.assign(EN, ' + '{"Transposer de": "Transpose by", "Transposition hors de l’étendue MIDI.": "Transposition outside the MIDI range."}' + ');' + "function T(s)", 1)
_I18N = _I18N.replace("function T(s)", 'Object.assign(EN, ' + '{"Importer un fichier MIDI": "Import a MIDI file", "Choisissez un fichier .mid.": "Choose a .mid file.", "Cochez la ou les pistes de la harpe :": "Tick the laser harp track(s):", "Piste": "Track", "notes différentes": "different notes", "a": "to", "canal": "channel", "Notes des rayons (de gauche à droite) :": "Beam notes (left to right):", "Cochez au moins une piste.": "Tick at least one track.", "Aucune note dans ce fichier.": "No notes in this file.", "rayons": "beams", "15 au plus, décochez des notes.": "15 at most, untick some notes.", "la harpe en a au moins 5, les rayons en trop rejoueront la note la plus aiguë.": "the harp has at least 5, the extra beams will repeat the highest note.", "Créer le morceau": "Create the song", "ce n\'est pas un fichier MIDI (.mid)": "this is not a MIDI file (.mid)", "aucune note choisie": "no note chosen"}' + ');' + "function T(s)", 1)
_I18N = _I18N.replace("function T(s)", 'Object.assign(EN, ' + '{"Réglages de harpe modifiés : mémoriser": "Harp settings changed: store", "Non mémorisés : ": "Not stored: "}' + ');' + "function T(s)", 1)
_I18N = _I18N.replace("function T(s)", 'Object.assign(EN, {"Attribuer au clavier": "Assign from keyboard", "Attribution au clavier : ON": "Keyboard assign: ON", "Coupez un rayon, puis jouez sa note sur le clavier.": "Cut a beam, then play its note on the keyboard.", "Aucun clavier relié : choisissez-le dans Paramètres.": "No keyboard connected: choose it in Settings.", "Clavier pour attribuer les notes": "Keyboard used to assign notes", "Le clavier dont on joue les notes en mode Attribuer au clavier.": "The keyboard you play notes on in Assign from keyboard mode.", "(aucun)": "(none)", " (débranché)": " (unplugged)", "Enregistré.": "Saved."});\n' + "function T(s)", 1)
PAGE = PAGE.replace("<script>", _I18N + "<script>", 1)
PAGE_HARPE = PAGE_HARPE.replace("<script>", _I18N + "<script>", 1)
PAGE_PARAMETRES = PAGE_PARAMETRES.replace("<script>", _I18N + "<script>", 1)
PAGE_PARAMETRES = PAGE_PARAMETRES.replace("</head>", '<script>\nif (location.search.indexOf(\'inclus\') >= 0) {\n  document.addEventListener(\'DOMContentLoaded\', () => {\n    document.querySelectorAll(\'a[href="/"]\').forEach(a => a.style.display = \'none\');\n    [\'memoprec\', \'memosuiv\'].forEach(id => { const b = document.getElementById(id); if (b) b.style.display = \'none\'; });\n  });\n}\n</script>\n' + "</head>", 1)
# Page de la harpe : memorisation (ce morceau / tous / communs) et case "commun".
PAGE_HARPE = PAGE_HARPE.replace("</style>", "  button.modifie { background: #b85c00; border-color: var(--vif); color: #fff; font-weight: 600; }\n</style>", 1)
PAGE_HARPE = PAGE_HARPE.replace('<div class="onglets" id="onglets"></div>', '<div class="reglage" id="memo">\n  <div class="haut"><span><span>Morceau</span> : <b id="memomorceau"></b></span><span id="memoetat"></span></div>\n  <div class="ligne" style="margin-top:8px">\n    <button id="memoce">Mémoriser dans ce morceau</button>\n    <button id="memotous">Mémoriser dans tous les morceaux</button>\n    <button id="memoretablir">Rétablir les réglages mémorisés</button></div>\n  <div class="oct"><span>Seuls les réglages modifiés (en orange) sont mémorisés. Les réglages cochés « commun à tous les morceaux » (largeur, sensibilité…) sont mémorisés une seule fois pour tous les morceaux.</span></div>\n</div>\n<div class="onglets" id="onglets"></div>', 1)
PAGE_HARPE = PAGE_HARPE.replace("    c.appendChild(champ); z.appendChild(c);\n  });", "    c.appendChild(champ); ajouteCommun(c, r); z.appendChild(c);\n  });", 1)
PAGE_HARPE = PAGE_HARPE.replace("\n</script>\n</body>", "\nlet MODIFS = [], COMMUNS = [];\nasync function lireMemo() {\n  let e;\n  try { e = await (await fetch('/api/etat')).json(); } catch (x) { return; }\n  const avant = JSON.stringify([MODIFS, COMMUNS]);\n  MODIFS = e.harpe_modifiee || []; COMMUNS = e.harpe_communs || [];\n  document.getElementById('memomorceau').textContent = e.memoire;\n  document.getElementById('memoetat').textContent = MODIFS.length\n    ? MODIFS.length + ' ' + T('réglage(s) non mémorisé(s) :') + ' ' + (e.harpe_modifiee_noms || MODIFS).join(', ')\n    : T('Tout est mémorisé.');\n  document.getElementById('memoetat').style.color = MODIFS.length ? 'var(--vif)' : '';\n  ['memoce', 'memotous', 'memoretablir'].forEach(id => { document.getElementById(id).disabled = !MODIFS.length && !!e.harpe_memorisee; });\n  // le bouton a utiliser s'allume : ce morceau (reglages propres) / tous (reglages communs)\n  const propres = MODIFS.filter(k => !COMMUNS.includes(k)), communsModifies = MODIFS.filter(k => COMMUNS.includes(k));\n  document.getElementById('memoce').className = propres.length ? 'modifie' : '';\n  document.getElementById('memotous').className = communsModifies.length ? 'modifie' : '';\n  if (avant !== JSON.stringify([MODIFS, COMMUNS]) && etat && onglet !== 'Moniteur' && onglet !== 'Preset'\n      && !(document.activeElement && document.activeElement.type === 'range')) dessine();\n}\nsetInterval(lireMemo, 1000);\nasync function memoAppel(chemin, corps, texte) {\n  const d = await (await fetch(chemin, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(corps || {})})).json();\n  if (d.erreur) { message(T('Erreur : ') + T(d.erreur)); return; }\n  message(texte);\n  await appel('/api/harpe/reglages', null, 'GET');\n  await lireMemo(); dessine();\n}\ndocument.getElementById('memoce').onclick = () => memoAppel('/api/memoire/harpe', {}, T('Mémorisé dans ce morceau.'));\ndocument.getElementById('memotous').onclick = () => {\n  if (!confirm(T('Mémoriser les réglages modifiés dans tous les morceaux ? (Le nombre de rayons reste propre à chaque morceau.)'))) return;\n  memoAppel('/api/memoire/harpe', {tous: true}, T('Mémorisé dans tous les morceaux.'));\n};\ndocument.getElementById('memoretablir').onclick = () => {\n  if (!confirm(T('Remettre la harpe telle qu’elle est mémorisée ?'))) return;\n  memoAppel('/api/memoire/recharger', {}, T('Réglages mémorisés rétablis.'));\n};\nfunction ajouteCommun(c, r) {\n  if (MODIFS.includes(r.cle)) c.style.borderColor = 'var(--vif)';\n  if (r.cle === 'rayons' || (etat.verrouilles || []).includes(r.cle)) return;\n  const l = document.createElement('label'); l.className = 'oct';\n  l.style.display = 'flex'; l.style.alignItems = 'center'; l.style.gap = '8px'; l.style.marginTop = '6px';\n  const b = document.createElement('input'); b.type = 'checkbox'; b.checked = COMMUNS.includes(r.cle);\n  b.onchange = () => memoAppel('/api/harpe/commun', {cle: r.cle, commun: b.checked},\n    b.checked ? T('Réglage commun à tous les morceaux.') : T('Réglage propre à chaque morceau.'));\n  const t = document.createElement('span'); t.textContent = T('commun à tous les morceaux');\n  l.appendChild(b); l.appendChild(t); c.appendChild(l);\n}\nlireMemo();\n" + "\n</script>\n</body>", 1)


# Page de la harpe : changer de morceau sans la quitter, raccourcis clavier.
HARPE_NAV_JS = """
async function changerMorceau(pas) {
  let e;
  try { e = await (await fetch('/api/etat')).json(); } catch (x) { return; }
  const l = e.liste_morceaux || [], i = l.indexOf(e.index);
  const cible = i < 0 ? (pas > 0 ? l[0] : l[l.length - 1]) : l[i + pas];
  if (cible === undefined) return;
  await fetch('/api/memoire', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({index: cible})});
  await appel('/api/harpe/reglages', null, 'GET');
  await lireMemo(); dessine();
}
document.getElementById('memoprec').onclick = () => changerMorceau(-1);
document.getElementById('memosuiv').onclick = () => changerMorceau(1);
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') { fetch('/api/silence', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'}); return; }
  const tag = (e.target && e.target.tagName) || '';
  if (['INPUT', 'SELECT', 'TEXTAREA'].includes(tag) || e.ctrlKey || e.altKey || e.metaKey) return;
  if (e.key === 'ArrowLeft') { e.preventDefault(); changerMorceau(-1); }
  if (e.key === 'ArrowRight') { e.preventDefault(); changerMorceau(1); }
});
"""
PAGE_HARPE = PAGE_HARPE.replace('<b id="memomorceau"></b>',
                                '<button id="memoprec" title="Morceau précédent">◀</button> '
                                '<b id="memomorceau"></b> <button id="memosuiv" title="Morceau suivant">▶</button>', 1)
PAGE_HARPE = PAGE_HARPE.replace(chr(10) + "</script>" + chr(10) + "</body>",
                                HARPE_NAV_JS + chr(10) + "</script>" + chr(10) + "</body>", 1)
PAGE_HARPE = PAGE_HARPE.replace("</head>", '<script>\nif (location.search.indexOf(\'inclus\') >= 0) {\n  document.addEventListener(\'DOMContentLoaded\', () => {\n    document.querySelectorAll(\'a[href="/"]\').forEach(a => a.style.display = \'none\');\n    [\'memoprec\', \'memosuiv\'].forEach(id => { const b = document.getElementById(id); if (b) b.style.display = \'none\'; });\n  });\n}\n</script>\n' + "</head>", 1)
# la page de la harpe relit ses reglages quand le morceau change (depuis la barre)
PAGE_HARPE = PAGE_HARPE.replace("  const avant = JSON.stringify([MODIFS, COMMUNS]);",
    "  if (window.MORCEAU_VU !== undefined && window.MORCEAU_VU !== e.index) { window.MORCEAU_VU = e.index;"
    " await appel('/api/harpe/reglages', null, 'GET'); dessine(); }\n  window.MORCEAU_VU = e.index;\n"
    "  const avant = JSON.stringify([MODIFS, COMMUNS]);", 1)

# Page de la harpe : carte "Couleurs des rayons" en tete de l'onglet Couleurs.
PAGE_HARPE = PAGE_HARPE.replace("  if (onglet === 'Moniteur') { dessineMoniteur(z); return; }",
    "  if (onglet === 'Moniteur') { dessineMoniteur(z); return; }\n  if (onglet === 'Colors') dessineCouleurs(z);", 1)
PAGE_HARPE = PAGE_HARPE.replace("  MODIFS = e.harpe_modifiee || []; COMMUNS = e.harpe_communs || [];",
    "  MODIFS = e.harpe_modifiee || []; COMMUNS = e.harpe_communs || [];\n"
    "  if (window.ETAT_MORCEAU && window.ETAT_MORCEAU.index !== e.index) coul = null;\n  window.ETAT_MORCEAU = e;", 1)
PAGE_HARPE = PAGE_HARPE.replace("\n</script>\n</body>", '\n// ---- Couleurs des rayons : deux couleurs, l\'application regle modes et puissances des lasers\nconst LASERS_C = [[\'r\', \'Rouge\', \'#ff3b3b\'], [\'v\', \'Vert\', \'#3bff5c\'], [\'b\', \'Bleu\', \'#3b6bff\']];\nconst NUANCES_C = [[\'Rouge\', {r: 100}], [\'Orange\', {r: 100, v: 40}], [\'Jaune\', {r: 100, v: 100}], [\'Vert\', {v: 100}],\n  [\'Cyan\', {v: 100, b: 100}], [\'Bleu\', {b: 100}], [\'Violet\', {r: 70, b: 100}], [\'Rose\', {r: 100, b: 50}],\n  [\'Blanc\', {r: 100, v: 100, b: 100}], [\'Éteint\', {}]];\n// modes de la harpe : 0 aucun, 1 tous, 2 tons, 3 demi-tons, 4 touches, 5 non touches\nconst MODE_A = {toucher: 5, tons: 2}, MODE_B = {toucher: 4, tons: 3};\nlet coul = null;    // {schema, a: {r,v,b}, b: {r,v,b}}\nconst cval = (c, k) => (c && c[k]) || 0;\nconst crgb = c => \'rgb(\' + [\'r\', \'v\', \'b\'].map(k => Math.round(255 * cval(c, k) / 100)).join(\',\') + \')\';\nfunction coulDepuisHarpe() {\n  const v = etat.valeurs, modes = LASERS_C.map(([k]) => v[\'mode_\' + k]);\n  if (modes.some(m => m === undefined)) return null;\n  const schema = modes.some(m => m === 2 || m === 3) ? \'tons\' : \'toucher\';\n  const c = {schema: schema, a: {}, b: {}, c: {}};\n  LASERS_C.forEach(([k], i) => {\n    const m = modes[i], p = v[\'puissance_\' + k] || 0;\n    if (!p) return;\n    if (m === 1 || m === MODE_A[schema]) c.a[k] = p;\n    if (m === 1 || m === MODE_B[schema]) c.b[k] = p;\n    if (schema === \'tons\' && m === 4) c.c[k] = p;\n  });\n  return c;\n}\nfunction coulCalcul() {\n  const res = {}, conflits = [];\n  LASERS_C.forEach(([k, nom]) => {\n    const pa = cval(coul.a, k), pb = cval(coul.b, k), pc = coul.schema === \'tons\' ? cval(coul.c, k) : 0;\n    if (pc && (pa || pb)) conflits.push(T(nom).toLowerCase() + \' \' + T(\'(déjà pris par les tons ou demi-tons, ignoré pour le toucher)\'));\n    if (pc && !pa && !pb) res[k] = {mode: 4, p: pc};\n    else if (!pa && !pb) res[k] = {mode: 0, p: 0};\n    else if (pa && !pb) res[k] = {mode: MODE_A[coul.schema], p: pa};\n    else if (!pa && pb) res[k] = {mode: MODE_B[coul.schema], p: pb};\n    else { res[k] = {mode: 1, p: Math.max(pa, pb)}; if (pa !== pb) conflits.push(T(nom).toLowerCase() + \' (\' + pa + \' % / \' + pb + \' %)\'); }\n  });\n  return {res, conflits};\n}\nfunction coulObtenue(qui, res, touche) {\n  const c = {};\n  LASERS_C.forEach(([k]) => {\n    const m = res[k].mode;\n    if (m === 1 || m === (qui === \'a\' ? MODE_A : MODE_B)[coul.schema] || (touche && m === 4 && coul.schema === \'tons\')) c[k] = res[k].p;\n  });\n  return c;\n}\nlet coulMinuterie = null;\nfunction coulEnvoyer() {\n  clearTimeout(coulMinuterie);\n  coulMinuterie = setTimeout(async () => {\n    const {res} = coulCalcul();\n    for (const [k] of LASERS_C) {\n      if (etat.valeurs[\'puissance_\' + k] !== res[k].p && res[k].mode !== 0)\n        await appel(\'/api/harpe/reglage\', {cle: \'puissance_\' + k, valeur: res[k].p});\n      if (etat.valeurs[\'mode_\' + k] !== res[k].mode)\n        await appel(\'/api/harpe/reglage\', {cle: \'mode_\' + k, valeur: res[k].mode});\n    }\n    message(T(\'Couleurs envoyées à la harpe.\'));\n    if (typeof lireMemo === \'function\') lireMemo();\n  }, 250);\n}\nfunction dessineCouleurs(z) {\n  const carte = document.createElement(\'div\'); carte.className = \'reglage\'; carte.id = \'cartecouleurs\';\n  z.appendChild(carte);\n  if (!coul) coul = coulDepuisHarpe();\n  if (!coul) { carte.innerHTML = \'<b>\' + T(\'Couleurs des rayons\') + \'</b><div class="oct">\' + T(\'Appuyez sur Lire la harpe pour afficher ses couleurs actuelles.\') + \'</div>\'; return; }\n  const enGamme = window.ETAT_MORCEAU && window.ETAT_MORCEAU.gamme;\n  const {res, conflits} = coulCalcul();\n  const titres = coul.schema === \'toucher\' ? [T(\'Rayons au repos\'), T(\'Rayons touchés\')]\n    : [T(\'Tons (do, ré, mi…)\'), T(\'Demi-tons (do#, ré#…)\'), T(\'Couleur ajoutée au toucher\')];\n  carte.innerHTML = \'<b>\' + T(\'Couleurs des rayons\') + \'</b>\' +\n    \'<div class="ligne" style="margin:6px 0"><span>\' + T(\'Les couleurs suivent :\') + \'</span>\' +\n    \'<label><input type="radio" name="cschema" value="toucher"\' + (coul.schema === \'toucher\' ? \' checked\' : \'\') + \' style="width:20px;height:20px"> \' + T(\'le toucher\') + \'</label>\' +\n    \'<label><input type="radio" name="cschema" value="tons"\' + (coul.schema === \'tons\' ? \' checked\' : \'\') + \' style="width:20px;height:20px"> \' + T(\'les tons et demi-tons\') + \'</label></div>\' +\n    \'<div id="cavertons"></div><div class="ligne" id="cetats" style="align-items:stretch"></div>\' +\n    \'<div id="capercu" style="display:flex;justify-content:center;align-items:flex-end;gap:12px;height:150px;background:#05060a;border-radius:10px;margin:10px 0;padding:10px"></div>\' +\n    \'<div class="ligne" id="clasers"></div><div class="oct" id="cconflit" style="color:var(--vif)"></div>\';\n  carte.querySelectorAll(\'input[name=cschema]\').forEach(r => r.onchange = () => {\n    coul = r.value === \'tons\' ? {schema: \'tons\', a: {v: 100}, b: {r: 100}, c: {b: 100}} : {schema: \'toucher\', a: {v: 100}, b: {b: 100}, c: {}};\n    dessine(); if (r.value === \'toucher\' || enGamme) coulEnvoyer();\n  });\n  const bloque = coul.schema === \'tons\' && !enGamme;\n  if (coul.schema === \'tons\') {\n    const av = carte.querySelector(\'#cavertons\');\n    av.className = \'oct\'; av.style.color = \'var(--vif)\';\n    av.innerHTML = T(\'Les tons et demi-tons sont ceux de la harpe elle-même : ce choix ne fonctionne qu’avec le morceau Gamme.\') +\n      (enGamme ? \'\' : \' <button id="cchargergamme">\' + T(\'Charger le morceau Gamme\') + \'</button>\');\n    const b = av.querySelector(\'#cchargergamme\');\n    if (b) b.onclick = async () => {\n      const e = await (await fetch(\'/api/etat\')).json();\n      if (e.index_gamme === null || e.index_gamme === undefined) return;\n      await fetch(\'/api/memoire\', {method: \'POST\', headers: {\'Content-Type\': \'application/json\'}, body: JSON.stringify({index: e.index_gamme})});\n      window.ETAT_MORCEAU = Object.assign({}, e, {gamme: true});\n      await appel(\'/api/harpe/reglages\', null, \'GET\'); if (typeof lireMemo === \'function\') await lireMemo();\n      coulEnvoyer(); dessine();\n    };\n  }\n  if (!coul.c) coul.c = {};\n  (coul.schema === \'tons\' ? [\'a\', \'b\', \'c\'] : [\'a\', \'b\']).forEach((id, i) => {\n    const c = coul[id], d = document.createElement(\'div\');\n    if (id === \'c\') {\n      // Rayons touches (tons / demi-tons) : on choisit directement la paire obtenue\n      // ton touche | demi-ton touche ; seuls les lasers libres peuvent s\'ajouter.\n      const libres = LASERS_C.filter(([k]) => !cval(coul.a, k) && !cval(coul.b, k)).map(([k]) => k);\n      const choisis = Object.keys(coul.c).filter(k => libres.includes(k) && coul.c[k] > 0);\n      const intensite = choisis.length ? coul.c[choisis[0]] : 100;\n      const options = [[]];\n      for (let m = 1; m < (1 << libres.length); m++) options.push(libres.filter((k, j) => m & (1 << j)));\n      d.style.cssText = \'flex:1 1 280px;border:1px solid var(--trait);border-radius:10px;padding:10px\' + (bloque ? \';opacity:.4;pointer-events:none\' : \'\');\n      d.innerHTML = \'<div style="font-weight:600">\' + T(\'Rayons touchés\') + \'</div><div class="oct">\' + T(\'ton touché | demi-ton touché\') + \'</div>\' +\n        \'<div class="ligne paires" style="margin:8px 0"></div>\';\n      const ajout = (base, ks, p) => { const r = Object.assign({}, base); ks.forEach(k => r[k] = p); return r; };\n      options.forEach(ks => {\n        const b = document.createElement(\'button\');\n        const sel = JSON.stringify([...ks].sort()) === JSON.stringify([...choisis].sort());\n        b.title = ks.length ? \'\' : T(\'pas de changement\');\n        b.style.cssText = \'display:flex;gap:0;padding:0;min-height:40px;border-radius:8px;overflow:hidden\' + (sel ? \';border:2px solid #fff\' : \'\');\n        const moitie = c => \'<span style="display:inline-block;width:30px;height:36px;background:\' + crgb(c) + \'"></span>\';\n        b.innerHTML = moitie(ajout(coul.a, ks, intensite)) + moitie(ajout(coul.b, ks, intensite));\n        b.onclick = () => { coul.c = {}; ks.forEach(k => coul.c[k] = intensite); dessine(); coulEnvoyer(); };\n        d.querySelector(\'.paires\').appendChild(b);\n      });\n      if (!libres.length) d.innerHTML += \'<div class="oct">\' + T(\'Tous les lasers sont pris par les tons et demi-tons : le toucher ne peut rien changer.\') + \'</div>\';\n      else {\n        const l = document.createElement(\'div\'); l.className = \'ligne\';\n        l.innerHTML = \'<span style="width:56px">\' + T(\'Intensité\') + \'</span><input type="range" min="10" max="100" value="\' + intensite +\n          \'" style="flex:1;height:28px"\' + (choisis.length ? \'\' : \' disabled\') + \'><b style="width:52px;text-align:right">\' + intensite + \' %</b>\';\n        const cur = l.querySelector(\'input\');\n        cur.oninput = () => { l.querySelector(\'b\').textContent = cur.value + \' %\'; };\n        cur.onchange = () => { choisis.forEach(k => coul.c[k] = +cur.value); dessine(); coulEnvoyer(); };\n        d.appendChild(l);\n      }\n      carte.querySelector(\'#cetats\').appendChild(d);\n      return;\n    }\n    d.style.cssText = \'flex:1 1 280px;border:1px solid var(--trait);border-radius:10px;padding:10px\' + (bloque ? \';opacity:.4;pointer-events:none\' : \'\');\n    d.innerHTML = \'<div style="display:flex;align-items:center;gap:10px;font-weight:600"><span style="width:28px;height:28px;border-radius:50%;display:inline-block;border:2px solid #fff3;background:\' +\n      crgb(id === \'c\' ? Object.fromEntries(LASERS_C.filter(([k]) => res[k].mode === 4).map(([k]) => [k, res[k].p])) : coulObtenue(id, res)) + \'"></span>\' + titres[i] + \'</div><div class="ligne nuancier" style="margin:8px 0"></div>\';\n    NUANCES_C.forEach(([nom, n]) => {\n      const b = document.createElement(\'button\'); b.title = T(nom);\n      b.style.cssText = \'width:36px;height:36px;min-height:36px;padding:0;border-radius:8px;background:\' + (nom === \'Éteint\' ? \'#000\' : crgb(n)) +\n        (JSON.stringify(n) === JSON.stringify(c) ? \';border:2px solid #fff\' : \'\');\n      b.onclick = () => { coul[id] = Object.assign({}, n); dessine(); coulEnvoyer(); };\n      d.querySelector(\'.nuancier\').appendChild(b);\n    });\n    LASERS_C.forEach(([k, nom]) => {\n      const autre = coul[id === \'a\' ? \'b\' : \'a\'];\n      const conflit = id === \'c\' ? (cval(c, k) && (cval(coul.a, k) || cval(coul.b, k))) : (cval(c, k) && cval(autre, k) && cval(c, k) !== cval(autre, k));\n      const l = document.createElement(\'div\'); l.className = \'ligne\';\n      l.innerHTML = \'<span style="width:56px\' + (conflit ? \';color:var(--vif)\' : \'\') + \'">\' + T(nom) + \'</span>\' +\n        \'<input type="range" min="0" max="100" value="\' + cval(c, k) + \'" style="flex:1;height:28px"><b style="width:52px;text-align:right">\' + cval(c, k) + \' %</b>\';\n      const curseur = l.querySelector(\'input\');\n      curseur.oninput = () => { l.querySelector(\'b\').textContent = curseur.value + \' %\'; };\n      curseur.onchange = () => { coul[id][k] = +curseur.value; if (!coul[id][k]) delete coul[id][k]; dessine(); coulEnvoyer(); };\n      d.appendChild(l);\n    });\n    carte.querySelector(\'#cetats\').appendChild(d);\n  });\n  const ca = coulObtenue(\'a\', res), cb = coulObtenue(\'b\', res), ap = carte.querySelector(\'#capercu\');\n  const cat = coulObtenue(\'a\', res, true), cbt = coulObtenue(\'b\', res, true);\n  for (let i = 0; i < 9; i++) {\n    const demi = [1, 3, 6].includes(i), touche = coul.schema === \'toucher\' ? i === 3 : (i === 3 || i === 5);\n    const c = coul.schema === \'toucher\' ? (touche ? cb : ca) : (demi ? (touche ? cbt : cb) : (touche ? cat : ca));\n    const r = document.createElement(\'div\'); const col = crgb(c);\n    r.style.cssText = \'width:5px;border-radius:3px;height:\' + (touche ? 80 : 125) + \'px;background:\' + col + \';box-shadow:0 0 10px \' + col;\n    ap.appendChild(r);\n  }\n  const noms = {0: T(\'Aucun\'), 1: T(\'Tous\'), 2: T(\'Tons\'), 3: T(\'Demi-tons\'), 4: T(\'Touchés\'), 5: T(\'Non touchés\')};\n  LASERS_C.forEach(([k, nom, cl]) => {\n    const e = document.createElement(\'span\'); e.className = \'oct\';\n    e.style.cssText = \'border:1px solid \' + cl + \';border-radius:8px;padding:4px 8px\';\n    e.innerHTML = \'<b style="color:\' + cl + \'">\' + T(nom) + \'</b> : \' + noms[res[k].mode] + (res[k].p ? \', \' + res[k].p + \' %\' : \'\');\n    carte.querySelector(\'#clasers\').appendChild(e);\n  });\n  const titreDetail = document.createElement(\'div\'); titreDetail.className = \'oct\'; titreDetail.style.margin = \'14px 0 6px\';\n  titreDetail.innerHTML = \'<b>\' + T(\'Réglages détaillés des lasers\') + \'</b> (\' + T(\'réglés automatiquement par la carte ci-dessus\') + \')\';\n  z.appendChild(titreDetail);\n  carte.querySelector(\'#cconflit\').textContent = conflits.length\n    ? \'⚠ \' + T(\'Impossible exactement : un laser ne peut pas avoir deux puissances différentes\') + \' — \' + conflits.join(\', \') + \'. \' + T(\'La harpe utilisera la plus forte (voir l’aperçu).\') : \'\';\n}\n' + "\n</script>\n</body>", 1)
# Les reglages detailles (modes et puissances) et la carte des couleurs restent lies :
# changer un reglage detaille fait relire la carte.
PAGE_HARPE = PAGE_HARPE.replace("  if (await appel('/api/harpe/reglage', {cle: r.cle, valeur: v})) {",
    "  if (/^(mode|puissance)_[rvb]$/.test(r.cle) && typeof coul !== 'undefined') coul = null;\n"
    "  if (await appel('/api/harpe/reglage', {cle: r.cle, valeur: v})) {", 1)

# Onglet "Couleurs (historique)" : les six reglages du Manager (modes et puissances),
# verrouilles ; on ne les modifie qu'en cochant "Modifier a la main".
PAGE_HARPE = PAGE_HARPE.replace("'Colors', 'Transpose', 'Preset', 'Moniteur'];",
                                "'Colors', 'ColorsOld', 'Transpose', 'Preset', 'Moniteur'];", 1)
PAGE_HARPE = PAGE_HARPE.replace("Colors: 'Couleurs', Transpose:", "Colors: 'Couleurs', ColorsOld: 'Couleurs (historique)', Transpose:", 1)
PAGE_HARPE = PAGE_HARPE.replace("Colors: 'Colors', Transpose:", "Colors: 'Colors', ColorsOld: 'Colors (legacy)', Transpose:", 1)
# petit nom sous l'onglet : celui du Manager (ces reglages etaient dans "System")
PAGE_HARPE = PAGE_HARPE.replace("'<br><small class=\"oct\">' + n + '</small>'",
                                "'<br><small class=\"oct\">' + (n === 'ColorsOld' ? 'System' : n) + '</small>'", 1)
PAGE_HARPE = PAGE_HARPE.replace("b.onclick = () => { onglet = n; dessine(); };",
                                "b.onclick = () => { onglet = n; histDeverrouille = false; dessine(); };", 1)
PAGE_HARPE = PAGE_HARPE.replace("if (onglet === 'Colors') dessineCouleurs(z);",
    "if (onglet === 'Colors') dessineCouleurs(z);\n"
    "  if (onglet === 'ColorsOld') {\n"
    "    const v = document.createElement('div'); v.className = 'reglage';\n"
    "    v.innerHTML = '<div class=\"oct\">' + T('Les réglages d’origine du Manager. Ils sont réglés automatiquement par la carte de l’onglet Couleurs.') + '</div>' +\n"
    "      '<label class=\"ligne\" style=\"margin-top:6px\"><input type=\"checkbox\" id=\"histdev\"' + (histDeverrouille ? ' checked' : '') + '> ' + T('Modifier à la main') + '</label>';\n"
    "    v.querySelector('#histdev').onchange = e => { histDeverrouille = e.target.checked; dessine(); };\n"
    "    z.appendChild(v);\n"
    "  }", 1)
PAGE_HARPE = PAGE_HARPE.replace("c.appendChild(champ); ajouteCommun(c, r); z.appendChild(c);",
    "if (onglet === 'ColorsOld' && !histDeverrouille) { champ.disabled = true; c.style.opacity = '.55'; }\n"
    "    c.appendChild(champ); ajouteCommun(c, r); z.appendChild(c);", 1)
PAGE_HARPE = PAGE_HARPE.replace("let etat = null, onglet = 'System';", "let etat = null, onglet = 'System', histDeverrouille = false;", 1)
# l'ancien titre "Reglages detailles des lasers" sous la carte n'a plus lieu d'etre
_i = PAGE_HARPE.find("  const titreDetail = ")
_j = PAGE_HARPE.find("z.appendChild(titreDetail);", _i)
if _i >= 0 and _j >= 0:
    PAGE_HARPE = PAGE_HARPE[:_i] + PAGE_HARPE[_j + len("z.appendChild(titreDetail);") + 1:]
_I18N_HIST = {"Couleurs (historique)": "Colors (legacy)", "Modifier à la main": "Edit by hand",
              "Les réglages d’origine du Manager. Ils sont réglés automatiquement par la carte de l’onglet Couleurs.":
              "The Manager's original settings. They are set automatically by the card in the Colors tab."}
PAGE_HARPE = PAGE_HARPE.replace("function T(s)", "Object.assign(EN, " + json.dumps(_I18N_HIST, ensure_ascii=False) + ");function T(s)", 1)
# Apres Memoriser / Retablir / reglage commun : la carte des couleurs relit la harpe.
PAGE_HARPE = PAGE_HARPE.replace("  message(texte);\n  await appel('/api/harpe/reglages', null, 'GET');",
                                "  message(texte);\n  if (typeof coul !== 'undefined') coul = null;\n  await appel('/api/harpe/reglages', null, 'GET');", 1)
PAGE = PAGE.replace("function T(s)", 'Object.assign(EN, ' + '{"Faire glisser pour déplacer": "Drag to move"}' + ');' + "function T(s)", 1)

# ------------------------------------------------------------------ aides des reglages de la harpe
# Un bouton « ? » a cote du nom de chaque reglage affiche son explication (tiree du
# manuel du Manager). Un bouton plutot qu'une bulle au survol : l'iPad n'a pas de souris.
_AIDES_HARPE = {
    "gain": ("Sensibilité des capteurs qui voient la main. Baissez-la si des reflets (plafond, lumières) déclenchent des notes tout seuls ; sinon, gardez la valeur habituelle.",
             "Sensitivity of the sensors that see the hand. Lower it if reflections (ceiling, lights) trigger notes by themselves; otherwise keep the usual value."),
    "mode_r": ("Quand ce laser s'allume : Aucun (jamais), Tous (tous les rayons), Tons (do, ré, mi…), Demi-tons (do#, ré#…), Touchés (seulement le rayon coupé par la main), Non touchés (il s'éteint quand la main coupe le rayon). Plus simple : la carte « Couleurs des rayons » de l'onglet Couleurs.",
               "When this laser lights up: None (never), All (every beam), Tone (C, D, E…), Half tone (C#, D#…), Trigged (only the beam cut by the hand), Untrigged (it goes off when the hand cuts the beam). Easier: the « Beam colours » card in the Colors tab."),
    "puissance_r": ("Luminosité maximale de ce laser, de 0 à 100. Plus simple : la carte « Couleurs des rayons » de l'onglet Couleurs.",
                    "Maximum brightness of this laser, 0 to 100. Easier: the « Beam colours » card in the Colors tab."),
    "ttl": ("À cocher seulement si le projecteur laser est piloté en TTL et non en analogique. En cas de doute, demandez au fournisseur du projecteur.",
            "Tick only if the laser projector is driven in TTL rather than analog. If in doubt, ask the projector's supplier."),
    "tout_module": ("Coché : la modulation de couleur agit sur les trois lasers. Décoché : seulement sur le rouge (réglage d'origine).",
                    "Ticked: colour modulation acts on all three lasers. Unticked: on red only (factory setting)."),
    "main": ("Ce que commande la hauteur de la main dans le rayon : rien, le volume, le pitch bend, la molette, le sustain, l'octave ou la vélocité. Octave et vélocité agissent note par note.",
             "What the hand height in the beam controls: nothing, volume, pitch bend, wheel, sustain, octave or velocity. Octave and velocity act note by note."),
    "pedale_analogique": ("Ce que commande la pédale d'expression branchée sur la harpe : rien, le volume, le pitch bend, la molette ou le sustain. « Sustain (un rayon à la fois) » est une variante ajoutée par Kromabridge.",
                          "What the expression pedal plugged into the harp controls: nothing, volume, pitch bend, wheel or sustain. « Sustain (one beam at a time) » is a variant added by Kromabridge."),
    "pedale_1": ("Rôle de cette pédale du pédalier : octave -1 ou +1, demi-ton -1 ou +1, ouvrir/fermer la harpe, preset précédent ou suivant.",
                 "Role of this footswitch: octave -1 or +1, half tone -1 or +1, open/close the harp, previous or next preset."),
    "rayons": ("Nombre de rayons de l'éventail, de 5 à 15.", "Number of beams in the fan, 5 to 15."),
    "balayage": ("Vitesse à laquelle le faisceau balaie l'éventail. Plus elle est haute, moins les rayons semblent bouger. Si les rayons perdent en luminosité ou se dessinent mal (projecteur lent), baissez-la.",
                 "How fast the beam sweeps the fan. The higher, the less the beams seem to move. If the beams get dimmer or are badly drawn (slow projector), lower it."),
    "largeur": ("Écartement des rayons : la largeur de l'éventail.", "Spacing of the beams: the width of the fan."),
    "z_decalage": ("Distance de la harpe par rapport à vous.", "Distance of the harp from you."),
    "z_profondeur": ("Profondeur de l'arc de cercle en mode 3D (harpe circulaire).", "Depth of the arc in 3D mode (circular harp)."),
    "z_mode": ("Décalage : la harpe est seulement avancée ou reculée. 3D : les rayons forment un arc de cercle.",
               "Offset: the harp is just moved nearer or further. 3D: the beams form an arc."),
    "miroir": ("Retourne la harpe : gauche-droite, en Z, ou les deux.", "Flips the harp: left-right, in Z, or both."),
    "delai": ("Retarde l'allumage du laser sur chaque rayon. À augmenter si les points des rayons au plafond font de petits cercles (projecteurs bon marché). Trop haut, les rayons perdent en luminosité.",
              "Delays the laser turn-on on each beam. Raise it if the beam dots on the ceiling draw small circles (cheap projectors). Too high, the beams get dimmer."),
    "auto_couleur": ("Quels rayons changent de couleur tout seuls, en douceur : aucun, tous, les tons, les demi-tons, les rayons touchés ou les non touchés.",
                     "Which beams change colour by themselves, smoothly: none, all, tones, half tones, touched or untouched beams."),
    "mod_vitesse": ("Vitesse du changement de couleur.", "Speed of the colour change."),
    "mod_profondeur": ("Ampleur du changement de couleur : faible, la couleur bouge à peine ; forte, elle change franchement.",
                       "Amount of colour change: low, the colour barely moves; high, it changes clearly."),
    "mod_phase": ("Décalage entre le rouge, le vert et le bleu pendant le changement de couleur : donne des teintes et des motifs différents.",
                  "Offset between red, green and blue while the colour changes: gives different hues and patterns."),
    "rideau": ("Les rayons ne s'éteignent jamais entre deux notes : effet « rideau ».", "Beams never go off between notes: « curtain » effect."),
    "transposition": ("Transposition interne de la harpe. Elle reste à 0 : c'est Kromabridge qui choisit la note de chaque rayon.",
                      "The harp's own transposition. It stays at 0: Kromabridge chooses each beam's note."),
}
for _c, _s in (("mode_v", "mode_r"), ("mode_b", "mode_r"), ("puissance_v", "puissance_r"), ("puissance_b", "puissance_r"),
               ("pedale_2", "pedale_1"), ("pedale_3", "pedale_1"), ("pedale_4", "pedale_1"), ("octave", "transposition")):
    _AIDES_HARPE[_c] = _AIDES_HARPE[_s]
PAGE_HARPE = PAGE_HARPE.replace(
    "</style>",
    "  .baide { min-height: 0; padding: 0 9px; margin-left: 8px; border-radius: 50%; font-size: 14px; line-height: 24px; }\n"
    "  .aide { color: var(--doux); font-size: 14px; margin: 4px 0 8px; }\n</style>", 1)
PAGE_HARPE = PAGE_HARPE.replace(
    "function dessine()",
    "const AIDES = " + json.dumps(_AIDES_HARPE, ensure_ascii=False) + ";\n"
    "const aidesOuvertes = new Set();\n"
    "function ajouterAide(c, r) {\n"
    "  const a = AIDES[r.cle]; if (!a) return;\n"
    "  const b = document.createElement('button'); b.className = 'baide'; b.textContent = '?';\n"
    "  b.title = LANGUE === 'en' ? 'Help' : 'Aide';\n"
    "  const t = document.createElement('div'); t.className = 'aide'; t.textContent = LANGUE === 'en' ? a[1] : a[0];\n"
    "  t.style.display = aidesOuvertes.has(r.cle) ? '' : 'none';\n"
    "  b.onclick = () => { if (aidesOuvertes.has(r.cle)) aidesOuvertes.delete(r.cle); else aidesOuvertes.add(r.cle);\n"
    "    t.style.display = aidesOuvertes.has(r.cle) ? '' : 'none'; };\n"
    "  c.querySelector('.haut span').appendChild(b);\n"
    "  c.querySelector('.haut').after(t);\n"
    "}\n"
    "function dessine()", 1)
PAGE_HARPE = PAGE_HARPE.replace(
    "    const val = c.querySelector('.val');",
    "    ajouterAide(c, r);\n    const val = c.querySelector('.val');", 1)

PAGE = PAGE.replace("function T(s)", 'Object.assign(EN, ' + '{"Changer le son du synthé pour la partie": "Change the synth sound for part"}' + ');' + "function T(s)", 1)

PAGE_PARAMETRES = PAGE_PARAMETRES.replace("function T(s)", 'Object.assign(EN, ' + '{"Le clavier joue comme la harpe": "The keyboard plays like the harp", "Les notes de ce clavier partent vers la sortie du morceau, telles quelles, sur son canal : pour continuer à jouer au clavier si la harpe a un problème. La case du rayon qui joue la même note s’allume sur la page.": "This keyboard\'s notes go to the song\'s output, unchanged, on its channel: to keep playing on the keyboard if the harp has a problem. The tile of the beam playing the same note lights up on the page.", "Si le clavier est aussi le synthé, coupez son mode « Local », sinon chaque note sonnera deux fois.": "If the keyboard is also the synth, turn its « Local » mode off, otherwise each note will sound twice."}' + ');' + "function T(s)", 1)

PAGE = PAGE.replace("function T(s)", 'Object.assign(EN, ' + '{"À propos": "About", "Version": "Version", "Version PC": "PC version", "Version Raspberry Pi": "Raspberry Pi version", "Firmware de la harpe :": "Harp firmware:", "Firmware de la harpe : pas encore lu (Paramètres de la harpe > Lire la harpe).": "Harp firmware: not read yet (Harp settings > Read the harp).", "Le pont entre la harpe laser et les synthétiseurs : notes par rayon, morceaux, listes, piste de contrôle, réglages de la harpe.": "The bridge between the laser harp and synthesizers: notes per beam, songs, lists, control track, harp settings."}' + ');' + "function T(s)", 1)
PAGE = PAGE.replace("function T(s)", 'Object.assign(EN, ' + '{"Créé par Michael avec Claude.": "Created by Michael with Claude."}' + ');' + "function T(s)", 1)

PAGE = PAGE.replace("function T(s)", 'Object.assign(EN, ' + '{"Supprimer ce rayon": "Delete this beam", "La harpe a au moins 5 rayons": "The harp has at least 5 beams", "Supprimer le rayon": "Delete beam", "Les rayons suivants se décalent d’un cran vers la gauche et gardent leur note.": "The following beams move one step to the left and keep their note.", "la harpe a au moins 5 rayons": "the harp has at least 5 beams"}' + ');' + "function T(s)", 1)

PAGE = PAGE.replace("function T(s)", 'Object.assign(EN, ' + '{"Exporter des listes…": "Export lists…", "Importer des listes…": "Import lists…", "Exporter des listes": "Export lists", "Importer des listes": "Import lists", "Chaque liste emporte ses morceaux. Le fichier se range dans vos téléchargements ; sur l’autre appareil, ouvrez-le avec « Importer des listes ».": "Each list takes its songs along. The file goes to your downloads; on the other device, open it with « Import lists ».", "Cette liste": "This list", "Toutes les listes": "All lists", "Importer": "Import", "Liste": "List", "Cochez les listes à importer. Elles arrivent avec leurs morceaux ; une liste qui porte le même nom est remplacée.": "Tick the lists to import. They come with their songs; a list with the same name is replaced.", "Aucune liste de morceaux à exporter.": "No song list to export.", "Ce fichier ne contient pas de listes. Pour des morceaux seuls, utilisez Morceau > Importer des morceaux.": "This file contains no lists. For songs only, use Song > Import songs.", "morceaux": "songs", "remplacera la liste existante": "will replace the existing list", "Cochez au moins une liste.": "Tick at least one list.", "Listes ajoutées :": "Lists added:", "Listes remplacées :": "Lists replaced:", "Morceaux ajoutés :": "Songs added:", "Morceaux remplacés :": "Songs replaced:", "aucune liste à exporter": "no list to export", "ce fichier ne contient pas de listes": "this file contains no lists"}' + ');' + "function T(s)", 1)

PAGE = PAGE.replace("function T(s)", 'Object.assign(EN, ' + '{"Enregistrer sur la clé USB du Pi": "Save to the Pi USB key", "Importer depuis la clé USB…": "Import from the USB key…", "Clé USB du Pi": "Pi USB key", "Éjecter la clé": "Eject the key", "Lecture de la clé…": "Reading the key…", "Choisissez le fichier de listes à importer :": "Choose the lists file to import:", "Choisissez le fichier de morceaux à importer :": "Choose the songs file to import:", "Aucun fichier .kromalaser sur la clé.": "No .kromalaser file on the key.", "Vous pouvez retirer la clé.": "You can remove the key.", "Enregistré sur la clé USB :": "Saved to the USB key:", "Pensez à éjecter la clé (menu Morceau ou Listes > Importer depuis la clé USB… > Éjecter la clé) avant de la retirer.": "Remember to eject the key (Song or Lists menu > Import from the USB key… > Eject the key) before removing it.", "aucune clé USB branchée sur le Pi": "no USB key plugged into the Pi", "clé USB illisible (pas de système de fichiers reconnu)": "unreadable USB key (no known file system)", "la clé USB n’est pas encore prise en charge sur ce Pi (outil USB non installé)": "USB keys are not supported on this Pi yet (USB tool not installed)", "fichier introuvable sur la clé USB": "file not found on the USB key"}' + ');' + "function T(s)", 1)

PAGE = PAGE.replace("function T(s)", 'Object.assign(EN, ' + '{"Gérer les morceaux…": "Manage songs…", "Gérer les morceaux": "Manage songs", "Le numéro de chaque morceau est celui utilisé par les pistes de contrôle.": "Each song\'s number is the one used by control tracks.", "Supprimer les morceaux cochés": "Delete ticked songs", "Créer une liste avec les morceaux cochés": "Create a list with the ticked songs", "Supprimer ce morceau": "Delete this song", "Cochez au moins un morceau.": "Tick at least one song.", "(Ils vont dans la corbeille : menu Morceau, Corbeille…)": "(They go to the trash: Song menu, Trash…)", "Liste créée :": "List created:", "Ouvrir": "Open"}' + ');' + "function T(s)", 1)

PAGE = PAGE.replace("function T(s)", 'Object.assign(EN, ' + '{"Nouvelle partie": "New part", "Partie vierge (5 rayons)": "Blank part (5 beams)", "Copie de la partie": "Copy of part", "Ajouter une partie": "Add a part", "Supprimer des parties": "Delete parts", "Supprimer les parties": "Delete parts", "Les lettres des parties suivantes changent.": "The letters of the following parts change.", "Touchez les parties à supprimer.": "Tap the parts to delete.", "un morceau garde au moins une partie": "a song keeps at least one part", "Les parties ont changé : la piste de contrôle déjà exportée pour ce morceau ne désigne plus les mêmes parties. Les commandes mémorisées ont suivi : réexportez la piste (Morceau > Modifier l’import > Exporter la piste de contrôle).": "The parts changed: the control track already exported for this song no longer points to the same parts. The stored commands have followed: re-export the track (Song > Edit the import > Export the control track)."}' + ');' + "function T(s)", 1)
PAGE = PAGE.replace("function T(s)", 'Object.assign(EN, ' + '{"Tous": "All", "Liste de morceaux active": "Active song list"}' + ');' + "function T(s)", 1)

PAGE_PARAMETRES = PAGE_PARAMETRES.replace("function T(s)", 'Object.assign(EN, ' + '{"Entrée de la piste de contrôle": "Control track input", "Port loopMIDI « Controle Harpe » (par défaut)": "loopMIDI port « Controle Harpe » (default)", "Entrée de l’interface MIDI USB du Pi (par défaut)": "Input of the Pi USB MIDI interface (default)", "Les commandes du canal 16 arrivant par cette entrée pilotent la harpe (un clavier ou un séquenceur, par exemple).": "Channel 16 commands arriving on this input control the harp (a keyboard or a sequencer, for example)."}' + ');' + "function T(s)", 1)

PAGE_PARAMETRES = PAGE_PARAMETRES.replace("function T(s)", 'Object.assign(EN, ' + '{"Nom des notes": "Note names"}' + ');' + "function T(s)", 1)

PAGE = PAGE.replace("function T(s)", 'Object.assign(EN, ' + '{"Morceaux déjà présents": "Songs already here", "Remplacer": "Replace", "Garder les deux": "Keep both", "Annuler l’import": "Cancel the import", "Remplacer : les anciens vont dans la corbeille. Garder les deux : les nouveaux sont renommés.": "Replace: the old ones go to the trash. Keep both: the new ones are renamed."}' + ');' + "function T(s)", 1)

PAGE = PAGE.replace("function T(s)", 'Object.assign(EN, ' + '{"Votre harpe a le firmware": "Your harp has firmware", "Kromabridge est prévu pour le firmware": "Kromabridge is designed for firmware", "le seul testé : certaines fonctions (nombre de rayons, animation, réglages de la harpe) peuvent ne pas fonctionner. Vous pouvez mettre la harpe à jour avec le logiciel du fabricant (KromaUpdater). Utilisation à vos risques et périls.": "the only one tested: some functions (number of beams, animation, harp settings) may not work. You can update the harp with the manufacturer\'s software (KromaUpdater). Use at your own risk."}' + ');' + "function T(s)", 1)

PAGE = PAGE.replace("function T(s)", 'Object.assign(EN, ' + '{"Mettre à jour le Pi…": "Update the Pi…", "Mettre à jour le Pi": "Update the Pi", "Téléchargez le fichier kromabridge-pi.zip de la nouvelle version (sur l’appareil qui affiche cette page, ou sur une clé USB à brancher sur le Pi), puis choisissez-le ci-dessous. Vos morceaux, listes et réglages ne sont pas touchés.": "Download the kromabridge-pi.zip file of the new version (on the device showing this page, or on a USB key to plug into the Pi), then choose it below. Your songs, lists and settings are not touched.", "Choisir le fichier…": "Choose the file…", "Depuis la clé USB du Pi": "From the Pi USB key", "Version installée :": "Installed version:", "Revenir à la version précédente": "Go back to the previous version", "Revenir à la version précédente ?": "Go back to the previous version?", "installée. Kromabridge redémarre : la page se recharge dans quelques secondes.": "installed. Kromabridge is restarting: the page will reload in a few seconds.", "Installer la mise à jour": "Install the update", "Installation en cours…": "Installing…", "Aucun fichier kromabridge-pi….zip sur la clé.": "No kromabridge-pi….zip file on the key.", "Une nouvelle version de Kromabridge est disponible :": "A new version of Kromabridge is available:", "vous avez la": "you have", "Télécharger": "Download", "Téléchargez le fichier kromabridge-pi.zip, puis ⚙ > Mettre à jour le Pi….": "Download the kromabridge-pi.zip file, then ⚙ > Update the Pi….", "ce fichier n’est pas une mise à jour de Kromabridge": "this file is not a Kromabridge update", "fichier de mise à jour refusé (contenu inattendu)": "update file refused (unexpected content)", "aucune version précédente n’est disponible": "no previous version is available", "la mise à jour du Pi se fait depuis la page du Pi": "the Pi is updated from the Pi page"}' + ');' + "function T(s)", 1)

PAGE_PARAMETRES = PAGE_PARAMETRES.replace("function T(s)", 'Object.assign(EN, ' + '{"Les sons de la harpe ne sont pas encore fournis dans cette version : le « son de la harpe en plus » reste muet. Ils arriveront avec une prochaine mise à jour.": "The harp sounds are not supplied in this version yet: the « extra harp sound » stays silent. They will come with a future update."}' + ');' + "function T(s)", 1)

_i = PAGE_HARPE.index("function dessinePresets(z) {")
_j = PAGE_HARPE.index("let dernier = 0", _i)
PAGE_HARPE = PAGE_HARPE[:_i] + '\nconst HORS_PRESET = [\'La note de chaque rayon\', \'Les accords\', \'Les parties\', \'L’animation d’ouverture et la transition\', \'Le son du synthé\', \'Le canal MIDI\', \'La piste de contrôle\'];\nlet PRESET = \'\', COPIE = {};\nasync function posterP(chemin, corps) {\n  let d;\n  try { d = await (await fetch(chemin, {method: \'POST\', headers: {\'Content-Type\': \'application/json\'}, body: JSON.stringify(corps || {})})).json(); }\n  catch (x) { message(T(\'Erreur : \') + x); return null; }\n  if (d.erreur) { message(T(d.erreur)); return null; }\n  if (d.copie) COPIE = d.copie;\n  return d;\n}\nfunction valeurLisible(r, v) {\n  if (v === undefined || v === null) return \'?\';\n  if (r.case) return v ? T(\'oui\') : T(\'non\');\n  if (r.choix) return (LANGUE === \'en\' && r.choix_manager ? r.choix_manager[v] : r.choix[v]) || String(v);\n  return String(v + r.decalage);\n}\nfunction nomReglage(r) { return LANGUE === \'en\' && r.nom_manager && !r.nom_manager.startsWith(\'(\') ? r.nom_manager : r.nom; }\nfunction dessinePresets(z) {\n  const c = document.createElement(\'div\'); c.className = \'reglage\';\n  const dans = etat.reglages.map(nomReglage).join(\', \');\n  c.innerHTML = \'<table class="tpresets"><tr><th>\' + T(\'Enregistré dans le preset de la harpe\') + \'</th><th>\' + T(\'Seulement dans Kromabridge\') + \'</th></tr>\' +\n    \'<tr><td>\' + dans + \'</td><td>\' + HORS_PRESET.map(T).join(\', \') + \'</td></tr></table>\';\n  const l = document.createElement(\'div\'); l.className = \'ligne\'; l.style.marginTop = \'12px\';\n  const choix = document.createElement(\'select\'); choix.id = \'pchoix\';\n  const remplir = () => {\n    const v = choix.value; choix.innerHTML = \'\';\n    choix.add(new Option(T(\'— choisir un preset —\'), \'\'));\n    for (let n = etat.presets[0]; n <= etat.presets[1]; n++)\n      choix.add(new Option(\'Preset \' + n + (COPIE[n] ? \'  ·  \' + T(\'lu le\') + \' \' + COPIE[n] : \'\'), n));\n    choix.value = v || PRESET;\n  };\n  const relire = document.createElement(\'button\'); relire.textContent = T(\'Relire ce preset\');\n  const tous = document.createElement(\'button\'); tous.textContent = T(\'Lire tous les presets\');\n  [choix, relire, tous].forEach(e => l.appendChild(e));\n  c.appendChild(l);\n  const ap = document.createElement(\'div\'); ap.id = \'papercu\'; ap.style.margin = \'12px 0\';\n  ap.textContent = T(\'Choisissez un preset pour voir son contenu et ses différences avec le morceau en cours.\');\n  c.appendChild(ap);\n  const l2 = document.createElement(\'div\'); l2.className = \'ligne\';\n  const sauve = document.createElement(\'button\'); sauve.textContent = LANGUE === \'en\' ? \'Save\' : \'Enregistrer (Save)\';\n  const rappel = document.createElement(\'button\'); rappel.textContent = LANGUE === \'en\' ? \'Recall\' : \'Rappeler (Recall)\'; rappel.className = \'vif\';\n  [sauve, rappel].forEach(e => { e.disabled = true; l2.appendChild(e); });\n  c.appendChild(l2);\n  const usine = document.createElement(\'button\'); usine.textContent = LANGUE === \'en\' ? \'Load Defaults\' : "Réglages d\'usine (Load Defaults)";\n  const p = document.createElement(\'p\'); p.style.marginTop = \'18px\'; p.appendChild(usine); c.appendChild(p);\n  z.appendChild(c);\n  let dernier = null;\n  const montrer = d => {\n    dernier = d;\n    if (!d) { sauve.disabled = rappel.disabled = true; return; }\n    sauve.disabled = rappel.disabled = !!d.gamme;\n    const diffs = [], lignes = [];\n    etat.reglages.forEach(r => {\n      const pv = d.valeurs[r.cle], mv = d.morceau[r.cle];\n      lignes.push(\'<tr><td>\' + nomReglage(r) + \'</td><td>\' + valeurLisible(r, pv) + \'</td></tr>\');\n      if (pv !== undefined && mv !== undefined && pv !== mv)\n        diffs.push(\'<li>\' + nomReglage(r) + \' : \' + valeurLisible(r, mv) + \' → <b>\' + valeurLisible(r, pv) + \'</b></li>\');\n    });\n    ap.innerHTML = \'<div><b>Preset \' + d.numero + \'</b> <span class="oct">(\' + T(\'lu le\') + \' \' + d.lu + \')</span></div>\' +\n      \'<div style="margin-top:6px">\' + (diffs.length\n        ? T(\'Différences avec le morceau en cours\') + \' « \' + d.nom_morceau + \' » (\' + T(\'morceau → preset\') + \') :<ul style="margin:4px 0">\' + diffs.join(\'\') + \'</ul>\'\n        : T(\'Identique au morceau en cours\') + \' « \' + d.nom_morceau + \' ».\') + \'</div>\' +\n      \'<details><summary>\' + T(\'Tout le contenu du preset\') + \'</summary><table class="tprecap">\' + lignes.join(\'\') + \'</table></details>\' +\n      (d.gamme ? \'<p class="oct">\' + T(\'Le morceau Gamme ne peut pas être enregistré dans un preset ni en recevoir un : choisissez un autre morceau.\') + \'</p>\' : \'\');\n  };\n  const apercu = async rel => {\n    if (!choix.value) { montrer(null); return; }\n    PRESET = choix.value;\n    message(T(\'Lecture du preset\') + \' \' + choix.value + \'…\');\n    const d = await posterP(\'/api/harpe/preset/apercu\', {numero: +choix.value, relire: !!rel});\n    if (d) { message(\'\'); montrer(d); remplir(); } else montrer(null);\n  };\n  choix.onchange = () => apercu(false);\n  relire.onclick = () => apercu(true);\n  tous.onclick = async () => {\n    if (!confirm(T(\'Lire les 40 presets de la harpe ? Cela prend environ une minute, harpe fermée. Rien n’est écrit dans la harpe.\'))) return;\n    for (let n = etat.presets[0]; n <= etat.presets[1]; n++) {\n      message(T(\'Lecture du preset\') + \' \' + n + \' / \' + etat.presets[1] + \'…\');\n      if (!await posterP(\'/api/harpe/preset/apercu\', {numero: n, relire: true})) return;\n    }\n    message(T(\'Les 40 presets sont lus.\')); remplir(); apercu(false);\n  };\n  sauve.onclick = async () => {\n    const d = dernier; if (!d) return;\n    const fin = String.fromCharCode(10);\n    if (!confirm(T(\'Enregistrer les réglages du morceau\') + \' « \' + d.nom_morceau + \' » \' + T(\'dans le preset\') + \' \' + d.numero + \' ?\' + fin + fin +\n        T(\'Enregistré :\') + \' \' + T(\'les réglages de la harpe (nombre de rayons, largeur, couleurs, pédales…).\') + fin +\n        T(\'Non enregistré :\') + \' \' + HORS_PRESET.map(T).join(\', \') + \'.\' + fin +\n        (d.notes_reproduites ? T(\'Les rayons de ce morceau forment une gamme continue : la transposition est enregistrée aussi, le preset jouera les mêmes notes.\')\n                             : T(\'La harpe seule ne peut pas jouer les notes de ce morceau : le preset jouera sa gamme d’origine.\')) + fin + fin +\n        T(\'Le contenu actuel du preset sera remplacé (une copie est gardée dans Kromabridge).\'))) return;\n    message(T(\'Enregistrement dans le preset\') + \' \' + d.numero + \'…\');\n    const r = await posterP(\'/api/harpe/preset/enregistrer\', {numero: d.numero});\n    if (r) { message(T(\'Réglages enregistrés dans le preset \') + d.numero + \'.\'); montrer(r); remplir(); }\n  };\n  rappel.onclick = () => {\n    const d = dernier; if (!d) return;\n    let dlg = document.getElementById(\'dlgrappel\');\n    if (!dlg) { dlg = document.createElement(\'dialog\'); dlg.id = \'dlgrappel\'; document.body.appendChild(dlg); }\n    dlg.innerHTML = \'<h3>\' + T(\'Rappeler le preset\') + \' \' + d.numero + \'</h3><p>\' + T(\'Où charger ses réglages ?\') + \'</p>\' +\n      \'<div class="ligne"><button id="rnouveau" class="vif">\' + T(\'Dans un nouveau morceau\') + \'</button>\' +\n      \'<button id="rcourant">\' + T(\'Dans le morceau en cours\') + \' « \' + d.nom_morceau + \' »</button>\' +\n      \'<button id="rannuler">\' + T(\'Annuler\') + \'</button></div>\' +\n      \'<p class="oct">\' + T(\'Nouveau morceau : ses réglages, et les notes que joue le preset. Morceau en cours : ses réglages seulement, les notes des rayons ne changent pas.\') + \'</p>\';\n    dlg.showModal();\n    const charger = async nouveau => {\n      dlg.close();\n      message(T(\'Chargement du preset\') + \' \' + d.numero + \'…\');\n      const r = await posterP(\'/api/harpe/preset/charger\', {numero: d.numero, nouveau: nouveau});\n      if (r) {\n        await appel(\'/api/harpe/reglages\', null, \'GET\');\n        message(T(\'Preset\') + \' \' + d.numero + \' \' + T(\'chargé dans le morceau\') + \' « \' + r.nom_morceau + \' ».\');\n        montrer(r);\n      }\n    };\n    dlg.querySelector(\'#rnouveau\').onclick = () => charger(true);\n    dlg.querySelector(\'#rcourant\').onclick = () => charger(false);\n    dlg.querySelector(\'#rannuler\').onclick = () => dlg.close();\n  };\n  usine.onclick = async () => {\n    if (!confirm(T("Remettre la harpe à ses réglages d\'usine ?"))) return;\n    if (await appel(\'/api/harpe/usine\')) { message(T("Réglages d\'usine rappelés.")); dessine(); }\n  };\n  remplir();\n  posterP(\'/api/harpe/preset/copie\').then(() => { remplir(); if (PRESET) apercu(false); });\n}\n\n' + PAGE_HARPE[_j:]
PAGE_HARPE = PAGE_HARPE.replace("</style>", '  .tpresets { width: 100%; border-collapse: collapse; }\n  .tpresets th, .tpresets td { border: 1px solid var(--trait); padding: 8px; vertical-align: top; text-align: left; width: 50%; }\n  .tprecap td { padding: 2px 10px 2px 0; }\n</style>', 1)

PAGE_HARPE = PAGE_HARPE.replace("function T(s)", 'Object.assign(EN, ' + '{"La note de chaque rayon": "Each beam\'s note", "Les accords": "Chords", "Les parties": "Parts", "L’animation d’ouverture et la transition": "Opening animation and transition", "Le son du synthé": "Synth sound", "Le canal MIDI": "MIDI channel", "La piste de contrôle": "Control track", "Enregistré dans le preset de la harpe": "Stored in the harp preset", "Seulement dans Kromabridge": "Only in Kromabridge", "— choisir un preset —": "— choose a preset —", "lu le": "read on", "Relire ce preset": "Read this preset again", "Lire tous les presets": "Read all presets", "Choisissez un preset pour voir son contenu et ses différences avec le morceau en cours.": "Choose a preset to see its content and its differences with the current song.", "Différences avec le morceau en cours": "Differences with the current song", "morceau → preset": "song → preset", "Identique au morceau en cours": "Same as the current song", "Tout le contenu du preset": "Full preset content", "Le morceau Gamme ne peut pas être enregistré dans un preset ni en recevoir un : choisissez un autre morceau.": "The Gamme song cannot be saved to a preset or receive one: choose another song.", "Lecture du preset": "Reading preset", "Lire les 40 presets de la harpe ? Cela prend environ une minute, harpe fermée. Rien n’est écrit dans la harpe.": "Read the 40 harp presets? It takes about a minute, with the harp closed. Nothing is written to the harp.", "Les 40 presets sont lus.": "The 40 presets have been read.", "Enregistrer les réglages du morceau": "Save the settings of song", "dans le preset": "into preset", "Enregistré :": "Saved:", "les réglages de la harpe (nombre de rayons, largeur, couleurs, pédales…).": "the harp settings (number of beams, width, colours, footswitches…).", "Non enregistré :": "Not saved:", "Les rayons de ce morceau forment une gamme continue : la transposition est enregistrée aussi, le preset jouera les mêmes notes.": "This song\'s beams form a continuous scale: the transposition is saved too, the preset will play the same notes.", "La harpe seule ne peut pas jouer les notes de ce morceau : le preset jouera sa gamme d’origine.": "The harp alone cannot play this song\'s notes: the preset will play its original scale.", "Le contenu actuel du preset sera remplacé (une copie est gardée dans Kromabridge).": "The current preset content will be replaced (a copy is kept in Kromabridge).", "Enregistrement dans le preset": "Saving into preset", "Rappeler le preset": "Recall preset", "Où charger ses réglages ?": "Where should its settings go?", "Dans un nouveau morceau": "Into a new song", "Dans le morceau en cours": "Into the current song", "Annuler": "Cancel", "Nouveau morceau : ses réglages, et les notes que joue le preset. Morceau en cours : ses réglages seulement, les notes des rayons ne changent pas.": "New song: its settings, and the notes the preset plays. Current song: its settings only, the beam notes do not change.", "Chargement du preset": "Loading preset", "Preset": "Preset", "chargé dans le morceau": "loaded into song", "Fermez la harpe pour lire ou enregistrer un preset : pendant un instant, elle applique vraiment le preset.": "Close the harp to read or save a preset: for a moment, it really applies the preset.", "le morceau Gamme ne peut pas recevoir les réglages d’un preset": "the Gamme song cannot receive a preset\'s settings", "Erreur : ": "Error: "}' + ');' + "function T(s)", 1)
