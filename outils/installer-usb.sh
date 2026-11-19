#!/bin/sh
# A lancer une fois avec sudo : installe l'outil USB de Kromabridge et la regle
# qui permet au programme (utilisateur $SUDO_USER) de s'en servir sans mot de passe.
set -e
[ "$(id -u)" = 0 ] || { echo "Lancez ce script avec sudo." >&2; exit 1; }
UTILISATEUR="${SUDO_USER:-}"
[ -n "$UTILISATEUR" ] || { echo "Lancez ce script avec sudo, depuis votre compte." >&2; exit 1; }
ICI=$(dirname "$0")
install -o root -g root -m 0755 "$ICI/kromabridge-usb" /usr/local/sbin/kromabridge-usb
REGLE=/etc/sudoers.d/kromabridge-usb
TMP=$(mktemp)
echo "$UTILISATEUR ALL=(root) NOPASSWD: /usr/local/sbin/kromabridge-usb" > "$TMP"
visudo -c -f "$TMP" >/dev/null
install -o root -g root -m 0440 "$TMP" "$REGLE"
rm -f "$TMP"
echo "Outil USB de Kromabridge installé."
