# Installer la version Raspberry Pi de Kromabridge

*Version 1.1 — 21/11/2026*

La version Raspberry Pi rend la harpe autonome : la harpe, le Pi et un synthétiseur, sans ordinateur. On prépare la carte microSD une seule fois, depuis n'importe quel ordinateur relié à Internet. Comptez un quart d'heure, sans aucune ligne de commande.

## Ce qu'il vous faut

- un **Raspberry Pi** (Kromabridge a été testé sur un Raspberry Pi 4 ; le Pi 3 et le Pi 5 devraient convenir, mais n'ont pas été testés) et son **alimentation officielle** ;
- une **carte microSD** de 16 Go ou plus, et un lecteur de carte pour l'ordinateur ;
- une **interface MIDI USB** (un câble USB-MIDI, par exemple) pour relier le Pi au synthétiseur.

> Le Raspberry Pi n'a pas de prise MIDI : l'interface MIDI USB se branche sur une de ses prises USB. Une carte MIDI branchée sur les broches GPIO du Pi n'a pas été testée et peut demander une configuration supplémentaire.

## 1. Télécharger l'image Kromabridge

Sur la page des versions, https://github.com/michaelecalle/kromabridge/releases/latest, téléchargez le fichier **kromabridge-pi-….img.xz** (dans « Assets », en bas de la page). Ne le décompressez pas.

## 2. Écrire la carte microSD

1. Téléchargez et installez **Raspberry Pi Imager** depuis le site officiel : https://www.raspberrypi.com/software/
2. Mettez la carte microSD dans le lecteur de l'ordinateur, puis lancez Raspberry Pi Imager.
3. Choisissez :
   - **Appareil** : le modèle de Raspberry Pi que vous utilisez ;
   - **Système d'exploitation** : tout en bas de la liste, **Utiliser une image personnalisée**, puis le fichier **kromabridge-pi-….img.xz** téléchargé ;
   - **Stockage** : votre carte microSD.
4. Si Imager propose de **personnaliser** le système, répondez **Non** : tout est déjà réglé dans l'image.
5. Lancez l'écriture, confirmez l'effacement de la carte, et attendez la fin (quelques minutes). Retirez la carte.

## 3. Démarrer le Pi

1. Mettez la carte microSD dans le Pi.
2. Branchez la harpe sur le Pi en USB, et l'interface MIDI USB entre le Pi et votre synthétiseur.
3. Branchez l'alimentation. Le premier démarrage prend **deux à trois minutes** (le Pi se prépare et redémarre parfois une fois tout seul).

## 4. Ouvrir Kromabridge

1. Sur une tablette, un téléphone ou un ordinateur, connectez-vous au réseau Wi-Fi **Kromalaser** (mot de passe : `kromalaser`).

   > Sur un PC Windows, la première fois, Windows demande parfois un **code PIN** : cliquez alors sur « **Se connecter à l'aide d'une clé de sécurité** » (ou « … d'un mot de passe », selon la version), puis tapez `kromalaser`.
2. Ouvrez **http://10.42.0.1:8080** dans le navigateur.

## 5. Premier réglage : la sortie MIDI (indispensable)

Au premier démarrage, Kromabridge ne sait pas encore à quoi votre synthétiseur est relié : la page s'ouvre directement sur les **Paramètres de l'application**, avec un bandeau de bienvenue.

1. Dans **Sortie MIDI des notes de la harpe**, choisissez votre interface MIDI USB (elle apparaît sous son nom, par exemple « USB MIDI Interface » ou le nom de votre câble).
2. Allumez la harpe, ouvrez-la et jouez : le synthétiseur doit sonner.

Ce réglage n'est demandé qu'une fois. Ensuite, la page s'ouvre normalement. La suite est dans le manuel de Kromabridge.

## 6. Éteindre le Pi

Ne coupez jamais l'alimentation directement : dans la page de Kromabridge, touchez le bouton **⏻** en haut à droite, confirmez, et attendez le message « Vous pouvez couper l'alimentation ». Au rallumage, le Pi recrée tout seul son réseau Kromalaser.

## Les mises à jour

Quand une nouvelle version paraît, un bandeau vert le signale en haut de la page (si l'appareil qui l'affiche a Internet).

1. Téléchargez le fichier **kromabridge-pi.zip** de la nouvelle version, depuis la page des versions : https://github.com/michaelecalle/kromabridge/releases
2. Dans la page du Pi : ⚙ > **Mettre à jour le Pi…**, puis **Choisir le fichier…** (ou **Depuis la clé USB du Pi**, si vous avez copié le fichier sur une clé branchée sur le Pi).

Vos morceaux, listes et réglages ne sont jamais touchés. En cas de souci, la même fenêtre permet de **revenir à la version précédente**.

## Pour les utilisateurs avancés

- **Accès SSH** : désactivé dans l'image. Pour l'activer, créez un fichier vide nommé `ssh` sur la partition « bootfs » de la carte microSD (lisible depuis l'ordinateur), puis redémarrez le Pi : `ssh kromalaser@10.42.0.1` (mot de passe `kromalaser`), depuis le réseau Kromalaser.
- **Installer Kromabridge sur un Raspberry Pi OS existant** (plutôt que l'image) : le Pi relié à Internet, connecté en SSH avec votre propre compte, tapez :

  ```
  curl -sSL https://github.com/michaelecalle/kromabridge/releases/latest/download/installer-pi.sh | sudo bash
  ```

---

*Kromabridge — créé par Michael avec Claude.*
