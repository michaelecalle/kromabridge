# Installer la version Raspberry Pi de Kromabridge

*Version 1.0 — 19/11/2026*

La version Raspberry Pi rend la harpe autonome : la harpe, le Pi et un synthétiseur, sans ordinateur. On l'installe une seule fois, depuis un PC relié à Internet. Comptez une demi-heure.

## Ce qu'il vous faut

- un **Raspberry Pi 4** (Kromabridge a été testé sur ce modèle ; le Pi 3 et le Pi 5 devraient convenir, mais n'ont pas été testés) et son **alimentation officielle** ;
- une **carte microSD** de 16 Go ou plus, et un lecteur de carte pour le PC ;
- une **interface MIDI USB** (un câble USB-MIDI, par exemple) pour relier le Pi au synthétiseur ;
- un PC Windows relié à Internet, et le nom et le mot de passe de votre Wi-Fi.

## 1. Préparer la carte microSD

1. Sur le PC, téléchargez et installez **Raspberry Pi Imager** depuis le site officiel : https://www.raspberrypi.com/software/
2. Mettez la carte microSD dans le lecteur du PC, puis lancez Raspberry Pi Imager.
3. Choisissez :
   - **Appareil** : le modèle de Raspberry Pi que vous utilisez (Kromabridge a été testé sur le Pi 4) ;
   - **Système d'exploitation** : *Raspberry Pi OS (other)*, puis **Raspberry Pi OS Lite (64-bit)** ;
   - **Stockage** : votre carte microSD.
4. Imager propose de **personnaliser** le système (bouton « Modifier les réglages », ou étapes successives selon la version d'Imager). Réglez :
   - **localisation** : votre pays, votre **fuseau horaire** (Europe/Paris) et votre clavier ;
   - **nom d'hôte** : `kromabridge` ;
   - **nom d'utilisateur** et **mot de passe** : choisissez-les et **notez-les**, ils vous serviront juste après ;
   - **Wi-Fi** : le nom et le mot de passe de votre Wi-Fi ;
   - **SSH** (onglet **Services** selon la version) : **activez-le**, avec authentification par mot de passe ;
   - **Raspberry Pi Connect**, s'il est proposé : **laissez-le désactivé**, Kromabridge n'en a pas besoin.
5. Lancez l'écriture et attendez la fin (quelques minutes). Retirez la carte.

## 2. Démarrer le Pi

1. Mettez la carte microSD dans le Pi.
2. Branchez l'alimentation. Attendez **deux minutes** : au premier démarrage, le Pi se prépare et rejoint votre Wi-Fi.

## 3. Se connecter au Pi depuis le PC

1. Sur le PC, faites un **clic droit sur le bouton Démarrer**, puis choisissez **Terminal** (sous Windows 10 : **Windows PowerShell**).
2. Tapez la commande suivante, en remplaçant **VOTRE-NOM** par le **nom d'utilisateur choisi dans Raspberry Pi Imager**, puis Entrée :

   ```
   ssh VOTRE-NOM@kromabridge.local
   ```

   Par exemple, si votre nom d'utilisateur est « harpiste » : `ssh harpiste@kromabridge.local`

3. La première fois, une question demande si vous faites confiance à cet appareil : tapez `yes`, puis Entrée.
4. Tapez le **mot de passe choisi dans Raspberry Pi Imager**, puis Entrée. **Rien ne s'affiche pendant que vous tapez** : c'est normal.

> Si `kromabridge.local` n'est pas trouvé, attendez encore une minute et réessayez. Sinon, cherchez l'adresse du Pi dans la page de votre box (appareils connectés) et utilisez-la à la place, par exemple `ssh VOTRE-NOM@192.168.1.42`.

## 4. Installer Kromabridge

Copiez cette commande, collez-la dans le Terminal (clic droit), puis Entrée :

```
curl -sSL https://github.com/michaelecalle/kromabridge/releases/latest/download/installer-pi.sh | sudo bash
```

Le Pi redemande le même mot de passe, puis installe tout seul ce qu'il faut (quelques minutes). À la fin s'affiche « Kromabridge est installé ».

Une minute plus tard, le Pi quitte le Wi-Fi de la maison et crée son propre réseau, **Kromalaser** : la connexion SSH se coupe, c'est normal. Vous pouvez fermer le Terminal.

## 5. Premier essai

1. Branchez la harpe sur le Pi en USB, et l'interface MIDI USB entre le Pi et votre synthétiseur.
2. Sur une tablette, un téléphone ou un PC, connectez-vous au réseau Wi-Fi **Kromalaser** (mot de passe : `kromalaser`).
3. Ouvrez **http://10.42.0.1:8080** dans le navigateur.
4. Dans ⚙ > **Paramètres de l'application** > **Sortie MIDI des notes de la harpe**, choisissez votre interface MIDI.

C'est prêt. La suite est dans le manuel de Kromabridge.

## Les mises à jour

Quand une nouvelle version paraît, un bandeau vert le signale en haut de la page (si l'appareil qui l'affiche a Internet).

1. Téléchargez le fichier **kromabridge-pi.zip** de la nouvelle version, depuis la page des versions : https://github.com/michaelecalle/kromabridge/releases
2. Dans la page du Pi : ⚙ > **Mettre à jour le Pi…**, puis **Choisir le fichier…** (ou **Depuis la clé USB du Pi**, si vous avez copié le fichier sur une clé branchée sur le Pi).

Vos morceaux, listes et réglages ne sont jamais touchés. En cas de souci, la même fenêtre permet de **revenir à la version précédente**.

---

*Kromabridge — créé par Michael avec Claude.*
