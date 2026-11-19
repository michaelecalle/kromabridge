# Kromabridge

**Le pont entre une harpe laser Kromalaser et vos synthétiseurs.**
*The bridge between a Kromalaser laser harp and your synthesizers.* (English below)

Kromabridge choisit la note jouée par chaque rayon, organise vos morceaux (avec leurs parties) et vos listes de morceaux, pilote la harpe (ouverture, fermeture, réglages du Manager V24) et peut être commandé depuis votre logiciel de musique (DAW) par une piste de contrôle.

Il existe en deux versions qui font exactement la même chose :

- **version PC** (Windows) : la harpe en USB sur le PC, les notes vers votre DAW ou vos instruments virtuels par loopMIDI ;
- **version Raspberry Pi** : la harpe, un Raspberry Pi et un synthétiseur, sans ordinateur ; tout se règle depuis une tablette, un téléphone ou un PC, par le Wi-Fi du Pi.

## Installer

Téléchargez la dernière version sur la page **[Releases](https://github.com/michaelecalle/kromabridge/releases/latest)** :

- **Kromabridge-Setup.exe** : l'installateur Windows (il vous guide aussi pour installer loopMIDI) ;
- **kromabridge-pi.zip** et le guide « Installer la version Raspberry Pi » : la version Raspberry Pi. Une seule commande, sur le Pi :

  ```
  curl -sSL https://github.com/michaelecalle/kromabridge/releases/latest/download/installer-pi.sh | sudo bash
  ```

Les manuels et les fiches d'aide sont dans [doc/utilisateur](doc/utilisateur).

## Harpes compatibles

Développé et testé avec une **KL 3d-3000** équipée du **firmware 1.440** (Manager V24). Voir le paragraphe « Harpes compatibles » du manuel pour les autres modèles. Toute utilisation avec un autre firmware ou un autre modèle se fait à vos risques et périls.

Kromabridge est un projet indépendant, non affilié au fabricant de la harpe Kromalaser.

---

## English

Kromabridge chooses the note played by each beam, organises your songs (with their parts) and song lists, controls the harp (opening, closing, Manager V24 settings) and can be driven from your music software (DAW) through a control track.

Two versions that do exactly the same thing:

- **PC version** (Windows): the harp on the PC by USB, notes to your DAW or virtual instruments through loopMIDI;
- **Raspberry Pi version**: the harp, a Raspberry Pi and a synthesizer, with no computer; everything is set from a tablet, phone or PC over the Pi's Wi-Fi.

Download the latest version from the **[Releases](https://github.com/michaelecalle/kromabridge/releases/latest)** page: **Kromabridge-Setup.exe** for Windows, **kromabridge-pi.zip** and the "Installing the Raspberry Pi version" guide for the Pi. Manuals and quick references are in [doc/utilisateur](doc/utilisateur).

Developed and tested with a **KL 3d-3000** running **firmware 1.440**. Use with any other firmware or model is at your own risk. Kromabridge is an independent project, not affiliated with the maker of the Kromalaser harp.

---

## Pour les développeurs / For developers

- `kromabridge/` : le programme (Python 3, sans dépendance pour la page web ; `python-rtmidi` pour le MIDI ; `numpy` et `sounddevice` pour le son de la harpe sur PC).
- `tests/` : `python -m pytest`
- `outils/` : outils système du Pi (clé USB, mise à l'heure).
- `paquet/` : fabrication d'une version (`construire.py`, installateur Inno Setup, script d'installation du Pi).

Lancer depuis les sources : `python -m kromabridge jouer`, puis ouvrir http://localhost:8080

*Kromabridge — créé par Michael avec Claude. Licence MIT.*
