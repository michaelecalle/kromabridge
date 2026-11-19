"""Le Wi-Fi du boitier : celui de la maison, ou son propre reseau pour la scene.

A la maison, le Pi rejoint la box, ce qui permet de le mettre a jour depuis le
PC. Sur scene, il cree son propre reseau, auquel le telephone ou l'iPad se
connecte directement, sans routeur.

Tout passe par NetworkManager (`nmcli`), autorise sans mot de passe par la regle
sudoers du boitier.
"""

from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path

DIRECT_PROFILE = "Kromalaser"
DIRECT_SSID = "Kromalaser"
DIRECT_PASSWORD = "kromalaser"
DIRECT_ADDRESS = "10.42.0.1"
WIFI_DEVICE = "wlan0"

# Le temps que NetworkManager soit pret apres l'allumage.
STARTUP_DELAY_SECONDS = 5
# /tmp est en memoire : le repere disparait a chaque extinction du Pi.
# (Pas /dev/shm : systemd y efface les fichiers de michael a chaque fin de
# session SSH, ce qui faisait rebasculer le reseau au redemarrage suivant.)
BOOT_MARKER = Path("/tmp/kromabridge-reseau-direct-fait")


def _nmcli(*args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["sudo", "-n", "/usr/bin/nmcli", *args],
        capture_output=True, text=True, timeout=60,
    )
    if check and result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "nmcli a echoue")
    return result.stdout


def _wifi_profiles() -> list[str]:
    lignes = _nmcli("-t", "-f", "NAME,TYPE", "connection", "show").splitlines()
    return [ligne.rsplit(":", 1)[0] for ligne in lignes if ligne.endswith(":802-11-wireless")]


def home_profile() -> str | None:
    """Le profil Wi-Fi de la maison : le premier qui n'est pas le reseau direct."""
    for nom in _wifi_profiles():
        if nom != DIRECT_PROFILE:
            return nom
    return None


def active_profile() -> str | None:
    lignes = _nmcli("-t", "-f", "NAME,DEVICE", "connection", "show", "--active").splitlines()
    for ligne in lignes:
        nom, _, device = ligne.rpartition(":")
        if device == WIFI_DEVICE:
            return nom
    return None


def ensure_direct_profile() -> None:
    """Cree le profil du reseau direct s'il n'existe pas encore.

    Il n'est jamais active automatiquement par NetworkManager : c'est le
    boitier qui decide, pour ne pas prendre la place de la box au demarrage.
    """
    if DIRECT_PROFILE in _wifi_profiles():
        return
    _nmcli(
        "connection", "add", "type", "wifi", "ifname", WIFI_DEVICE,
        "con-name", DIRECT_PROFILE, "ssid", DIRECT_SSID,
        "autoconnect", "no",
        "802-11-wireless.mode", "ap", "802-11-wireless.band", "bg",
        "ipv4.method", "shared", "ipv4.addresses", f"{DIRECT_ADDRESS}/24",
        "ipv6.method", "disabled",
        "wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", DIRECT_PASSWORD,
    )


def state() -> dict:
    actif = active_profile()
    return {
        "mode": "direct" if actif == DIRECT_PROFILE else ("maison" if actif else "aucun"),
        "reseau": actif,
        "ssid_direct": DIRECT_SSID,
        "adresse_directe": DIRECT_ADDRESS,
    }


def switch_to_direct() -> None:
    ensure_direct_profile()
    _nmcli("connection", "up", DIRECT_PROFILE)


def switch_to_home() -> None:
    maison = home_profile()
    if maison is None:
        raise RuntimeError("aucun Wi-Fi de maison enregistre")
    _nmcli("connection", "up", maison, check=False)
    # Si la box n'est pas a portee, on ne laisse pas le boitier injoignable.
    if active_profile() is None:
        switch_to_direct()


def later(action, delay: float = 1.0) -> None:
    """Lance `action` un peu apres, le temps que la page recoive sa reponse."""
    threading.Timer(delay, action).start()


def direct_at_startup(delay: float = STARTUP_DELAY_SECONDS) -> None:
    """A l'allumage, le boitier cree son propre reseau : c'est son usage normal.

    Une seule fois par demarrage du Pi (le repere vit en memoire et disparait a
    l'extinction) : si le programme redemarre, ou si l'on a choisi le Wi-Fi de
    la maison depuis la page, on ne rebascule pas.
    """
    if BOOT_MARKER.exists():
        return

    def basculer() -> None:
        time.sleep(delay)
        try:
            switch_to_direct()
            BOOT_MARKER.touch()
            print("Reseau direct actif.", flush=True)
        except Exception as exc:  # le boitier doit jouer meme sans Wi-Fi
            print(f"Reseau direct impossible : {exc}", flush=True)

    threading.Thread(target=basculer, daemon=True).start()
