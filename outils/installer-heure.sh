#!/bin/sh
# A lancer une fois avec sudo : installe l'outil de mise a l'heure de Kromabridge
# et la regle qui permet au programme (utilisateur $SUDO_USER) de s'en servir
# sans mot de passe.
set -e
[ "$(id -u)" = 0 ] || { echo "Lancez ce script avec sudo." >&2; exit 1; }
UTILISATEUR="${SUDO_USER:-}"
[ -n "$UTILISATEUR" ] || { echo "Lancez ce script avec sudo, depuis votre compte." >&2; exit 1; }
ICI=$(dirname "$0")
install -o root -g root -m 0755 "$ICI/kromabridge-heure" /usr/local/sbin/kromabridge-heure
REGLE=/etc/sudoers.d/kromabridge-heure
TMP=$(mktemp)
echo "$UTILISATEUR ALL=(root) NOPASSWD: /usr/local/sbin/kromabridge-heure" > "$TMP"
visudo -c -f "$TMP" >/dev/null
install -o root -g root -m 0440 "$TMP" "$REGLE"
rm -f "$TMP"
echo "Outil de mise à l'heure de Kromabridge installé."
