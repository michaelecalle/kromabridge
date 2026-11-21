#!/bin/bash
# Installation de Kromabridge sur un Raspberry Pi (Raspberry Pi OS, 64 bits).
#
# A lancer une fois, connecte au Pi en SSH, le Pi etant relie a Internet :
#   curl -sSL https://github.com/michaelecalle/kromabridge/releases/latest/download/installer-pi.sh | sudo bash
# ou, avec un fichier kromabridge-pi.zip deja copie sur le Pi :
#   sudo bash installer-pi.sh kromabridge-pi.zip
#
# Ce que fait ce script : installe la bibliotheque MIDI (python3-rtmidi), copie
# Kromabridge dans le dossier personnel de l'utilisateur, le fait demarrer avec
# le Pi, l'autorise a eteindre le Pi, a changer de reseau Wi-Fi, a lire une cle
# USB et a se mettre a l'heure. Les morceaux deja presents ne sont jamais effaces.
set -e
DEPOT="michaelecalle/kromabridge"

[ "$(id -u)" = 0 ] || { echo "Lancez ce script avec sudo." >&2; exit 1; }
# KB_IMAGE=1 : installation dans une image de carte SD (fabriquer-image.sh), sans
# demarrer quoi que ce soit ; KB_UTILISATEUR donne alors le nom de l'utilisateur.
UTILISATEUR="${KB_UTILISATEUR:-${SUDO_USER:-}}"
[ -n "$UTILISATEUR" ] && [ "$UTILISATEUR" != root ] || { echo "Lancez ce script avec sudo, depuis votre compte (pas root)." >&2; exit 1; }
MAISON=$(getent passwd "$UTILISATEUR" | cut -d: -f6)
DOSSIER="$MAISON/kromabridge"

echo "== Kromabridge : installation pour l'utilisateur $UTILISATEUR, dans $DOSSIER"

echo "== 1/6 Bibliotheques"
apt-get update -qq
apt-get install -y -qq python3-rtmidi unzip curl >/dev/null

echo "== 2/6 Programme"
TEMP=$(mktemp -d)
if [ -n "$1" ]; then
    cp "$1" "$TEMP/kromabridge-pi.zip"
else
    curl -sSL -o "$TEMP/kromabridge-pi.zip" "https://github.com/$DEPOT/releases/latest/download/kromabridge-pi.zip"
fi
unzip -q -o "$TEMP/kromabridge-pi.zip" -d "$TEMP/paquet"
[ -f "$TEMP/paquet/kromabridge/__init__.py" ] || { echo "Fichier kromabridge-pi.zip invalide." >&2; exit 1; }
mkdir -p "$DOSSIER"
for partie in kromabridge outils doc; do
    rm -rf "${DOSSIER:?}/$partie"
    [ -d "$TEMP/paquet/$partie" ] && cp -r "$TEMP/paquet/$partie" "$DOSSIER/$partie"
done
mkdir -p "$DOSSIER/presets"
# Ports MIDI : la harpe en entree ; la sortie se choisit ensuite dans la page
# (Parametres de l'application > Sortie MIDI des notes de la harpe).
[ -f "$DOSSIER/boitier.json" ] || echo '{"entree": "kromalaser", "sortie": ""}' > "$DOSSIER/boitier.json"
chown -R "$UTILISATEUR:$UTILISATEUR" "$DOSSIER"
rm -rf "$TEMP"

echo "== 3/6 Droits (eteindre le Pi, changer de reseau Wi-Fi)"
REGLE=/etc/sudoers.d/kromabridge
TMP=$(mktemp)
echo "$UTILISATEUR ALL=(root) NOPASSWD: /usr/bin/nmcli, /usr/bin/systemctl poweroff" > "$TMP"
visudo -c -f "$TMP" >/dev/null
install -o root -g root -m 0440 "$TMP" "$REGLE"
rm -f "$TMP"

echo "== 4/6 Cle USB et mise a l'heure"
SUDO_USER="$UTILISATEUR" sh "$DOSSIER/outils/installer-usb.sh"
SUDO_USER="$UTILISATEUR" sh "$DOSSIER/outils/installer-heure.sh"

echo "== 5/6 Acces au MIDI"
usermod -aG audio "$UTILISATEUR"

echo "== 6/6 Demarrage automatique"
cat > /etc/systemd/system/kromabridge.service <<FIN
[Unit]
Description=Kromabridge (harpe laser Kromalaser)
After=sound.target network-online.target
Wants=network-online.target

[Service]
ExecStart=/usr/bin/python3 -m kromabridge jouer
WorkingDirectory=$DOSSIER
Restart=always
RestartSec=5
User=$UTILISATEUR

[Install]
WantedBy=multi-user.target
FIN
if [ -n "${KB_IMAGE:-}" ]; then
    systemctl enable kromabridge >/dev/null 2>&1 || ln -sf /etc/systemd/system/kromabridge.service /etc/systemd/system/multi-user.target.wants/kromabridge.service
    echo "Kromabridge est installe dans l'image."
    exit 0
fi
systemctl daemon-reload
systemctl enable kromabridge >/dev/null 2>&1
systemctl restart kromabridge

echo
echo "Kromabridge est installe."
echo "Dans une minute environ, le Pi quitte le Wi-Fi de la maison et cree son propre"
echo "reseau Wi-Fi « Kromalaser » (mot de passe : kromalaser). Votre connexion SSH"
echo "va donc se couper : c'est normal."
echo "Connectez ensuite votre tablette, telephone ou PC au reseau Kromalaser et"
echo "ouvrez http://10.42.0.1:8080"
