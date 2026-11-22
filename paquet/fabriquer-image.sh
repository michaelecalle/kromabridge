#!/bin/bash
# Fabrique l'image de carte SD « Kromabridge » : Raspberry Pi OS Lite (64 bits)
# avec Kromabridge deja installe et regle. A lancer SUR UN RASPBERRY PI (64 bits),
# relie a Internet, avec sudo :
#   sudo bash fabriquer-image.sh kromabridge-pi.zip
# Resultat : kromabridge-pi-<version>.img.xz dans le dossier de travail.
#
# Dans l'image : utilisateur « kromalaser » (mot de passe « kromalaser »), nom
# reseau « kromabridge », SSH desactive, pays Wi-Fi FR (fixe au premier demarrage),
# fuseau Europe/Paris. Kromabridge demarre tout seul et cree le reseau Kromalaser.
set -e
[ "$(id -u)" = 0 ] || { echo "Lancez ce script avec sudo." >&2; exit 1; }
ZIP=$(readlink -f "${1:?donnez le fichier kromabridge-pi.zip}")
[ "$(uname -m)" = aarch64 ] || { echo "A lancer sur un Raspberry Pi en 64 bits." >&2; exit 1; }
ICI=$(dirname "$(readlink -f "$0")")
TRAVAIL="${TRAVAIL:-$HOME/image-kromabridge}"
[ -n "$SUDO_USER" ] && TRAVAIL="$(getent passwd "$SUDO_USER" | cut -d: -f6)/image-kromabridge"
UTILISATEUR=kromalaser
MOTDEPASSE=kromalaser
URL="https://downloads.raspberrypi.com/raspios_lite_arm64_latest"
MONTAGE=/mnt/kromabridge-image
mkdir -p "$TRAVAIL"
cd "$TRAVAIL"
VERSION=$(unzip -p "$ZIP" kromabridge/__init__.py | sed -n 's/^__version__ = "\(.*\)"/\1/p')
[ -n "$VERSION" ] || { echo "kromabridge-pi.zip invalide." >&2; exit 1; }
echo "== Image Kromabridge $VERSION, dans $TRAVAIL"

echo "== 1/7 Outils"
apt-get install -y -qq xz-utils parted e2fsprogs dosfstools curl >/dev/null

echo "== 2/7 Raspberry Pi OS Lite (64 bits), image officielle"
if [ ! -f raspios.img.xz ]; then
    curl -sSL -o raspios.img.xz.tmp "$URL" && mv raspios.img.xz.tmp raspios.img.xz
fi
rm -f kromabridge.img
xz -dc raspios.img.xz > kromabridge.img
# un peu de place en plus pour Kromabridge et sa bibliotheque MIDI
truncate -s +768M kromabridge.img
parted -s kromabridge.img resizepart 2 100%

echo "== 3/7 Montage"
BOUCLE=$(losetup -fP --show kromabridge.img)
nettoyer() {
    set +e
    for m in dev/pts dev proc sys boot/firmware ""; do umount "$MONTAGE/$m" 2>/dev/null; done
    losetup -d "$BOUCLE" 2>/dev/null
}
trap nettoyer EXIT
e2fsck -fy "${BOUCLE}p2" >/dev/null || true
resize2fs "${BOUCLE}p2" >/dev/null
mkdir -p "$MONTAGE"
mount "${BOUCLE}p2" "$MONTAGE"
mount "${BOUCLE}p1" "$MONTAGE/boot/firmware"
for m in dev dev/pts proc sys; do mount --bind "/$m" "$MONTAGE/$m"; done
# acces a Internet dans l'image le temps de l'installation (on remet ensuite l'original)
mv "$MONTAGE/etc/resolv.conf" "$MONTAGE/etc/resolv.conf.kromabridge" 2>/dev/null || true
cp /etc/resolv.conf "$MONTAGE/etc/resolv.conf"

echo "== 4/7 Utilisateur, nom, fuseau"
HASH=$(openssl passwd -6 "$MOTDEPASSE")
# l'outil officiel de Raspberry Pi OS renomme le premier utilisateur et fixe son mot de passe
chroot "$MONTAGE" /usr/lib/userconf-pi/userconf "$UTILISATEUR" "$HASH"
echo kromabridge > "$MONTAGE/etc/hostname"
sed -i 's/raspberrypi/kromabridge/g' "$MONTAGE/etc/hosts"
ln -sf /usr/share/zoneinfo/Europe/Paris "$MONTAGE/etc/localtime"
echo "Europe/Paris" > "$MONTAGE/etc/timezone"

echo "== 5/7 Kromabridge"
cp "$ZIP" "$MONTAGE/tmp/kromabridge-pi.zip"
cp "$ICI/installer-pi.sh" "$MONTAGE/tmp/installer-pi.sh"
chroot "$MONTAGE" env KB_IMAGE=1 KB_UTILISATEUR="$UTILISATEUR" bash /tmp/installer-pi.sh /tmp/kromabridge-pi.zip
rm -f "$MONTAGE/tmp/kromabridge-pi.zip" "$MONTAGE/tmp/installer-pi.sh"

echo "== 6/7 Premier demarrage : pays du Wi-Fi (sinon le Wi-Fi reste coupe)"
cat > "$MONTAGE/usr/local/sbin/kromabridge-premier-demarrage" <<'FIN'
#!/bin/sh
# Une seule fois, au premier demarrage : pays du Wi-Fi (France), pour que le Wi-Fi
# soit autorise et que Kromabridge puisse creer son reseau Kromalaser.
raspi-config nonint do_wifi_country FR || true
rfkill unblock wifi || true
systemctl disable kromabridge-premier-demarrage.service
FIN
chmod 755 "$MONTAGE/usr/local/sbin/kromabridge-premier-demarrage"
cat > "$MONTAGE/etc/systemd/system/kromabridge-premier-demarrage.service" <<'FIN'
[Unit]
Description=Kromabridge : reglages du premier demarrage
Before=kromabridge.service NetworkManager.service

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/kromabridge-premier-demarrage

[Install]
WantedBy=multi-user.target
FIN
ln -sf /etc/systemd/system/kromabridge-premier-demarrage.service \
    "$MONTAGE/etc/systemd/system/multi-user.target.wants/kromabridge-premier-demarrage.service"
chroot "$MONTAGE" apt-get clean
rm -f "$MONTAGE/etc/resolv.conf"
[ -e "$MONTAGE/etc/resolv.conf.kromabridge" ] || [ -L "$MONTAGE/etc/resolv.conf.kromabridge" ] && mv "$MONTAGE/etc/resolv.conf.kromabridge" "$MONTAGE/etc/resolv.conf"

echo "== 7/7 Fermeture et compression (un bon quart d'heure)"
nettoyer
trap - EXIT
SORTIE="kromabridge-pi-$VERSION.img.xz"
xz -T0 -6 -c kromabridge.img > "$SORTIE.tmp" && mv "$SORTIE.tmp" "$SORTIE"
rm -f kromabridge.img
chown -R "${SUDO_USER:-root}:" "$TRAVAIL" 2>/dev/null || true
echo
echo "Image prete : $TRAVAIL/$SORTIE ($(du -h "$SORTIE" | cut -f1))"
