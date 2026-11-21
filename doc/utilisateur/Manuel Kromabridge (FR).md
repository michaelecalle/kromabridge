# Kromabridge — Manuel d'utilisation

*Version 1.0 — 19/11/2026*

Kromabridge est le pont entre une harpe laser Kromalaser et vos synthétiseurs. Il choisit la note jouée par chaque rayon, organise vos morceaux et vos listes de morceaux, pilote la harpe (ouverture, fermeture, réglages) et peut être commandé depuis votre logiciel de musique (DAW) ou un séquenceur.

Il existe en deux versions qui font exactement la même chose :

- **la version PC** (Windows) : la harpe est branchée en USB sur le PC, et les notes partent vers votre logiciel de musique (DAW, instrument virtuel VST…) par un port MIDI virtuel ;
- **la version Raspberry Pi** : la harpe est branchée sur un Raspberry Pi (appelé simplement « le Pi » dans ce manuel), et les notes partent vers un synthétiseur par un câble MIDI. On règle tout depuis une tablette, un téléphone ou un ordinateur, sans ordinateur relié à la harpe : la harpe devient autonome.

> La version Raspberry Pi a été développée et testée sur un **Raspberry Pi 4**. Les autres modèles, en particulier le Pi 3 et le Pi 5, devraient convenir, mais n'ont pas été testés. Utilisez obligatoirement l'**alimentation officielle** du modèle de Raspberry Pi choisi.

### Harpes compatibles

Kromabridge a été développé et testé avec une **KL 3d-3000** équipée du **firmware 1.440** (Manager V24). La version du firmware compte plus que le modèle.

> **Kromabridge est prévu pour le firmware 1.440**, le seul qui ait été testé (c'est la version la plus récente publiée par le fabricant, datée de 2017). Toute utilisation avec un autre firmware ou un autre modèle se fait aux risques et périls de l'utilisateur.


- **KL 3d-1000, 3d-3000, 3d-5000** : elles ne diffèrent que par la puissance du laser et utilisent le même firmware. Avec le firmware 1.440, elles devraient fonctionner exactement de la même façon.
- **KL 3d-EVO** (contrôleur pour projecteur laser externe) : son firmware 1.440 traite les commandes MIDI exactement comme celui de la KL 3d-1000 (comparaison des firmwares du fabricant). Elle devrait donc être entièrement compatible, mais elle n'a pas été testée.
- **KL 3d-PRO** : son firmware (version 1.3) est plus ancien. Les fonctions de notes (morceaux, parties, listes, piste de contrôle) devraient fonctionner, mais les réglages envoyés par Kromabridge (nombre de rayons, animation d'ouverture, page des réglages de la harpe) risquent de ne pas être pris en compte. Non testée.
- **Firmware plus ancien que 1.440** (1.320, par exemple) : mettez d'abord la harpe à jour avec le logiciel de mise à jour du fabricant (KromaUpdater).

Les fonctions de notes ne demandent que les notes MIDI de la harpe : elles devraient fonctionner avec tous les modèles. La version du firmware de votre harpe s'affiche dans ⚙ > À propos.

---

## 1. Premiers pas

### Version PC

**Installation** (une seule fois) : lancez **Kromabridge-Setup.exe**, téléchargé depuis https://github.com/michaelecalle/kromabridge/releases, et laissez-vous guider. L'installateur vous aide aussi à installer **loopMIDI**, le logiciel gratuit qui crée les ports MIDI virtuels, et crée lui-même les ports **Kromabridge** et **Controle Harpe**. Vos morceaux, listes et réglages sont rangés dans **Documents\Kromabridge**.

1. Allumez la harpe et branchez-la au PC en USB.
2. Double-cliquez sur le raccourci **Kromabridge** du bureau. La page de l'application s'ouvre dans votre navigateur.
3. Dans votre logiciel de musique, choisissez **Kromabridge** comme entrée MIDI de la piste qui doit jouer la harpe, et armez-la (écoute).

### Version Raspberry Pi

**Installation** (une seule fois) : suivez le guide « Installer la version Raspberry Pi », fourni avec l'installateur PC et sur la page des versions.

1. Branchez la harpe sur le Pi en USB, et la sortie MIDI du Pi sur votre synthétiseur.

   > Le Raspberry Pi n'a pas de prise MIDI : branchez sur une de ses prises USB **une interface MIDI USB** (un câble USB-MIDI, par exemple). Deux interfaces différentes ont été testées : elles ont fonctionné dès le branchement, sans réglage. Une carte MIDI branchée sur les broches GPIO du Pi n'a pas été testée et peut demander une configuration supplémentaire.
2. Allumez le Pi. Au bout d'une minute environ, il crée le réseau Wi-Fi **Kromalaser** (mot de passe : `kromalaser`).
3. Connectez à ce réseau l'appareil de votre choix (tablette, téléphone, ordinateur : tout appareil qui a le Wi-Fi et un navigateur), puis ouvrez **http://10.42.0.1:8080** dans le navigateur.

   > Sur un PC Windows, la première fois, Windows demande parfois un **code PIN** : cliquez alors sur « **Se connecter à l'aide d'une clé de sécurité** » (ou « … d'un mot de passe », selon la version), puis tapez `kromalaser`.

> Astuce : ajoutez la page à l'écran d'accueil de la tablette pour l'ouvrir comme une application.

### La page en un coup d'œil

En haut, la barre de commandes, de gauche à droite :

| Élément | Rôle |
|---|---|
| ▲ ▼ | Liste de morceaux précédente / suivante |
| Nom de la liste | La liste de morceaux active (« Tous » quand aucune liste n'est choisie) |
| ◀ ▶ | Morceau précédent / suivant |
| Titre du morceau | Le morceau chargé, et sa position dans la liste, par exemple (2/12) |
| **Ouvrir la harpe** / **Fermer la harpe** | Allume ou éteint l'éventail de rayons |
| Menu **Morceau** | Ouvrir, créer, renommer, supprimer, importer, exporter… |
| Menu **Listes** | Choisir et gérer les listes de morceaux |
| Menu **Apprentissage** | Créer ou changer des rayons au clavier |
| 🔇 | Activer le son de la harpe sur cet appareil (si ce réglage est utilisé) |
| 🛑 **Panique** | Coupe immédiatement toutes les notes qui sonnent |
| ⏻ | Éteindre le Pi proprement (version Raspberry Pi seulement) |
| ⚙ | Paramètres de l'application, de la harpe, À propos |

Un petit point orange sur ⚙ signale des réglages de harpe modifiés mais pas encore mémorisés.

Sous la barre : les réglages du morceau, puis une **case par rayon**. Quand vous coupez un rayon, sa case s'allume.

---

## 2. Les morceaux

Un morceau (une « mémoire ») contient tout ce qu'il faut pour jouer une pièce : la note de chaque rayon, le nombre de rayons, le canal MIDI, le son du synthétiseur, les réglages de la harpe, l'animation d'ouverture…

**Tout changement est enregistré immédiatement** : il n'y a pas de bouton « Enregistrer ».

### Menu Morceau

- **Ouvrir…** : choisir un morceau dans la liste.
- **Gérer les morceaux…** : tous les morceaux d'un coup d'œil, avec leur numéro. Pour chacun : **Ouvrir**, **Renommer** (sans avoir à le charger) ou **✕** (supprimer). Cochez plusieurs morceaux pour **Supprimer les morceaux cochés** ou **Créer une liste avec les morceaux cochés**.
- **Nouveau morceau** : un morceau vierge, ou une copie d'un morceau existant.
- **Enregistrer sous…** : faire une copie du morceau affiché sous un autre nom.
- **Renommer**, **Supprimer** : un morceau supprimé va dans la corbeille.
- **Corbeille…** : restaurer un morceau supprimé, ou vider la corbeille.
- **Importer un fichier MIDI** / **Modifier l'import** : voir le chapitre 6.
- **Exporter des morceaux** / **Importer des morceaux** : transférer des morceaux entre le PC et le Pi (un fichier .kromalaser, qui contient aussi le fichier MIDI importé). Si un morceau existe déjà, vous choisissez de le remplacer (l'ancien va dans la corbeille) ou de garder les deux.
- **Importer depuis la clé USB…** (version Raspberry Pi seulement) : voir « La clé USB du Pi » plus bas.

### La clé USB du Pi

Une clé USB branchée sur le Pi permet d'échanger des fichiers avec le PC, sans ordinateur près de la harpe : très pratique quand on règle le Pi depuis une tablette.

- **Enregistrer sur la clé** : dans la fenêtre **Exporter des morceaux** (ou **Exporter des listes**), cochez **Enregistrer sur la clé USB du Pi**. Le fichier est rangé dans le dossier **Kromabridge** de la clé.
- **Lire la clé** : **Morceau > Importer depuis la clé USB…** (ou **Listes > Importer depuis la clé USB…**) affiche les fichiers .kromalaser de la clé ; touchez celui à importer.
- **Avant de retirer la clé**, touchez **Éjecter la clé** dans cette même fenêtre.

Sur le PC, ces options n'apparaissent pas : la fenêtre d'ouverture de fichiers donne déjà accès aux clés USB.

### Réglages du morceau

- **Nombre de rayons** : de 5 à 15 (limite de la harpe).
- **Canal MIDI de sortie** : le canal sur lequel partent les notes (1 à 16).
- **Changer le son du synthé à l'ouverture du morceau** : quand la case est cochée, Kromabridge envoie au synthétiseur le son choisi (numéro de programme, et si besoin la banque MSB / LSB) au chargement du morceau. Décochée ou vide : le son du synthé n'est pas touché. La liste à gauche du programme choisit la numérotation utilisée par votre synthé (1 à 128, 0 à 127, ou 11 à 88 pour les synthés à 8 banques de 8 sons). Taper un numéro coche la case automatiquement.
- **Animation d'ouverture** : à l'ouverture, l'éventail part serré puis s'écarte progressivement (pause, puis durée d'ouverture en secondes).
- **Transition animée entre les parties** : au changement de partie, l'éventail se resserre puis se rouvre.

### Le morceau « Gamme »

Ce morceau spécial est toujours présent, en fin de liste. Chaque rayon y joue la note que la harpe envoie elle-même, sans modification. Il sert aux essais et aux couleurs « tons / demi-tons ». On ne peut ni le renommer, ni le supprimer, ni changer ses notes.

---

## 3. Les rayons

Chaque case représente un rayon, de gauche (grave) à droite (aigu).

- **Changer la note** : touchez le nom de la note et choisissez dans la liste. La note est jouée pour que vous l'entendiez.
- **-12 / -1 / +1 / +12** : descendre ou monter la note d'une octave ou d'un demi-ton.
- **Tous les rayons** : décaler toutes les notes d'un coup (-12, -1, +1, +12), ou **Ranger du grave à l'aigu**.
- **Accord** : faire jouer plusieurs notes à un seul rayon. Choisissez un accord tout fait (majeur, mineur, 7e…) bâti sur la note du rayon, ou ajoutez les notes une par une.
- **✕** : supprimer ce rayon. Les rayons suivants se décalent d'un cran vers la gauche en gardant leur note. Impossible en dessous de 5 rayons.

> Réduire le nombre de rayons avec la liste « Nombre de rayons » retire toujours le dernier rayon à droite. Pour choisir lequel retirer, utilisez ✕.

---

## 4. Les parties

Un morceau peut avoir plusieurs **parties** (A, B, C…), chacune avec ses propres notes et son propre nombre de rayons. Les boutons de parties apparaissent sous les réglages du morceau.

- **Changer de partie** : touchez sa tuile (A, B, C…).
- **Ajouter une partie** : touchez **＋**, puis choisissez une **partie vierge** (5 rayons) ou la **copie** d'une partie existante.
- **Changer l'ordre** : faites glisser une tuile vers la gauche ou la droite. Les lettres suivent la position : la partie placée en deuxième devient B.
- **Supprimer des parties** : touchez **－**, touchez les parties à supprimer, puis **Supprimer**. Un morceau garde toujours au moins une partie.
- Déplacer ou supprimer une partie change les lettres des suivantes : si le morceau a une piste de contrôle (chapitre 8), l'application vous prévient et propose de la réexporter.
- Au chargement d'un morceau, c'est toujours la partie A qui est active.
- Chaque partie peut avoir **son propre son de synthé** : sélectionnez la partie, puis réglez la ligne « Changer le son du synthé pour la partie… ». Une partie dont la case est décochée ne change pas le son.
- Les parties se créent aussi à l'import MIDI (chapitre 6) ou à l'apprentissage au clavier (chapitre 5).
- Pédale de sustain enfoncée, les notes tenues continuent de sonner pendant un changement de partie.

---

## 5. Apprentissage au clavier

Un clavier MIDI permet de créer ou modifier les rayons très vite. Choisissez votre clavier dans ⚙ > Paramètres de l'application.

- **Créer les rayons au clavier** : jouez au clavier les notes du morceau, dans n'importe quel ordre, puis touchez **Terminer**. Elles deviennent les rayons, du grave à l'aigu. Vous pouvez créer un nouveau morceau, remplacer les notes du morceau affiché, ou enchaîner plusieurs parties.
- **Changer un rayon au clavier** : coupez un rayon (ou touchez sa case), puis jouez sur le clavier la note qu'il doit jouer. Chaque note change le rayon tout de suite. **Garder les changements** valide, **Annuler les changements** remet le morceau comme avant.

---

## 6. Importer un fichier MIDI

**Morceau > Importer un fichier MIDI** transforme une piste d'un fichier MIDI en morceau.

1. Choisissez le fichier .mid, puis cochez la ou les pistes de la harpe. Un aperçu (comme dans un séquenceur) montre les notes ; ▶ permet d'écouter.
2. Si la piste contient plus de 15 notes différentes, décochez celles à ignorer (la harpe a 15 rayons au plus).
3. Réglez si besoin la transposition par octaves, puis le titre.
4. **Parties** : sélectionnez une zone de l'aperçu (en mesures) et ajoutez-la comme partie, ou laissez l'application créer les parties.
5. **Commandes de la harpe** : placez dans l'aperçu les moments où la harpe doit s'ouvrir, se fermer ou changer de partie, puis **Exporter la piste de contrôle** (voir chapitre 8).
6. **Créer le morceau**.

**Modifier l'import** rouvre l'import tel que vous l'avez laissé. « Mettre à jour le morceau » garde les parties que vous avez retouchées à la main.

---

## 7. Les listes de morceaux

Le menu **Listes** permet d'avoir, en plus de la liste de tous les morceaux, des listes de morceaux dans l'ordre de votre choix.

- **Gérer les listes…** : créer, renommer, supprimer une liste ; ajouter des morceaux ; les remettre dans l'ordre en les faisant glisser par la poignée ☰ (au doigt ou à la souris) ou avec les flèches ↑ ↓. Validez avec **Valider**.
- **Morceau > Gérer les morceaux…** permet aussi de créer une liste d'un coup, avec les morceaux cochés.
- Pendant le jeu, ◀ ▶ passent au morceau précédent / suivant **de la liste active**, et ▲ ▼ changent de liste. Le nom de la liste active s'affiche en haut, à côté de ▲ ▼.
- **Exporter des listes…** : enregistre une liste, ou toutes, dans un fichier .kromalaser. Chaque liste emporte ses morceaux.
- **Importer des listes…** : cochez les listes à importer ; elles arrivent avec leurs morceaux. Une liste qui porte le même nom est remplacée.
- Sur le Pi, l'export et l'import peuvent passer par la clé USB (chapitre 2).
- **Attention** : l'ordre de la liste « Tous les morceaux » donne le **numéro** de chaque morceau, utilisé par la piste de contrôle (chapitre 8). Si vous le changez, l'application vous prévient et propose de réexporter les pistes de contrôle concernées. Un nouveau morceau va toujours à la fin, pour ne décaler aucun numéro. En revanche, supprimer un morceau décale d'un cran les numéros des morceaux suivants (si vous supprimez le n° 2, le n° 3 devient le n° 2) : vous recevez le même avertissement.

---

## 8. Piloter la harpe depuis votre DAW (piste de contrôle)

Une piste MIDI de votre DAW (ou d'un séquenceur) peut charger un morceau, ouvrir et fermer la harpe, et changer de partie, au bon moment de la chanson. Tout se passe sur le **canal MIDI 16** :

| Message (canal 16) | Effet |
|---|---|
| Changement de programme n | Charge le morceau n° n (1 = le premier de « Tous les morceaux ») |
| Note do-1 (C-2 dans un DAW où le do central est C3) | Ouvre la harpe |
| Note do#-1 (C#-2) | Ferme la harpe |
| Notes do0, do#0, ré0… (C-1, C#-1, D-1…) | Parties A, B, C… |

- **Version PC** : la piste de contrôle du DAW sort vers le port **Controle Harpe**.
- **Version Raspberry Pi** : la piste sort par une sortie MIDI de votre interface, branchée sur l'entrée MIDI du Pi.
- Activez la case **Contrôle par le DAW** dans ⚙ > Paramètres de l'application. La page affiche les dernières commandes reçues.
- Le plus simple : préparez les commandes à l'import du fichier MIDI (chapitre 6) puis **Exporter la piste de contrôle**, et glissez le fichier obtenu dans votre DAW.

> Si le DAW renvoie le même morceau au début de chaque lecture, l'application ne recharge pas tout : elle revient seulement à la partie A.

---

## 9. Les réglages de la harpe

**⚙ > Paramètres de la harpe** regroupe tous les réglages du Manager d'origine, avec les mêmes noms (en français, avec le nom d'origine entre parenthèses). Chaque réglage a un bouton **?** qui en explique le rôle.

Onglets : Système, Pédales, Rayons, Couleurs, Couleurs (historique), Transposition, Presets, Moniteur.

- **Lire la harpe** : affiche les réglages actuels de la harpe.
- Un réglage modifié est envoyé tout de suite à la harpe, et apparaît en orange tant qu'il n'est pas mémorisé.
- **Mémoriser dans ce morceau** : le morceau retiendra ces réglages et les renverra à la harpe à chaque chargement.
- **Mémoriser dans tous les morceaux** : la même chose pour tous les morceaux (le nombre de rayons reste propre à chaque morceau).
- **Rétablir les réglages mémorisés** : annule les changements non mémorisés.
- **Commun à tous les morceaux** : cochez cette case pour un réglage qui dépend du lieu plutôt que du morceau (largeur de l'éventail, sensibilité…). Il est retenu une seule fois et s'applique à tous les morceaux.

### La carte « Couleurs des rayons »

Dans l'onglet **Couleurs**, choisissez simplement la couleur des rayons, et l'application règle elle-même les modes et puissances des trois lasers :

- **le toucher** : une couleur pour les rayons au repos, une pour les rayons touchés ;
- **les tons et demi-tons** : une couleur pour les tons (do, ré, mi…), une pour les demi-tons (do#, ré#…), et la couleur ajoutée quand on touche un rayon. Ce choix ne fonctionne qu'avec le morceau Gamme, car ce sont les notes de la harpe elle-même.

L'aperçu montre le résultat. Un laser ne pouvant avoir qu'une seule puissance, certaines combinaisons sont impossibles exactement : l'application le signale.

L'onglet **Couleurs (historique)** donne accès aux réglages d'origine des lasers, verrouillés par défaut (case « Modifier à la main »).

### Les presets de la harpe

La harpe garde elle-même 40 presets, que l'on peut rappeler au pédalier sans ordinateur. Un preset ne contient que les réglages du Manager : nombre de rayons, largeur, couleurs, pédales, transposition… La note de chaque rayon, les accords, les parties, l'animation, le son du synthé, le canal MIDI et la piste de contrôle n'existent que dans Kromabridge : la harpe seule ne sait jouer qu'une gamme chromatique, éventuellement transposée. Le tableau repliable en haut de l'onglet **Presets** récapitule ce que garde chacun.

- **Voir un preset** : choisissez-le dans la liste. Kromabridge le lit (la harpe l'applique un instant, puis revient à son état d'avant ; rien n'est écrit dans la harpe) et affiche ses **différences avec le morceau en cours**, puis tout son contenu. Un preset déjà lu s'affiche ensuite instantanément, avec la date de lecture. **Relire ce preset** et **Lire tous les presets** (une vingtaine de secondes) mettent la copie à jour.
- **Enregistrer (Save)** : enregistre les réglages du morceau en cours dans un preset. Une fenêtre demande toujours dans quel preset, sans en choisir un d'avance ; les presets qui semblent déjà programmés sont marqués « ⚠ (programmé) ». Si les rayons du morceau forment une gamme continue, la transposition est enregistrée aussi, et le preset jouera les mêmes notes.
- **Rappeler (Recall)** : charge le preset affiché **dans un nouveau morceau** (ses réglages, et les notes qu'il joue : une base de travail) ou **dans le morceau en cours** (ses réglages seulement ; les notes des rayons ne changent pas).
- **Remettre l'ancien contenu** : avant d'écrire dans un preset, Kromabridge garde son ancien contenu. Ce bouton le remet, en cas d'erreur.

La lecture et l'enregistrement se font **harpe fermée** : pendant un instant, la harpe applique vraiment le preset. Enregistrer un preset écrit dans la mémoire de la harpe, comme le Manager d'origine : Kromabridge ne le fait que sur votre demande.

### Moniteur

L'onglet **Moniteur** affiche tous les messages échangés avec la harpe, pour comprendre ce qui se passe.

---

## 10. Les paramètres de l'application

**⚙ > Paramètres de l'application** :

- **Langue** : français ou anglais, pour tous les textes de l'application.
- **Nom des notes** : do, ré, mi… ou C, D, E…, indépendamment de la langue (par exemple, l'application en français avec les notes C, D, E). Le do central (note MIDI 60) s'appelle do4 / C4.
- **Clavier pour attribuer les notes** : le clavier MIDI utilisé pour l'apprentissage.
- **Le clavier joue comme la harpe** : les notes de ce clavier partent vers la sortie du morceau, telles quelles : une solution de secours pour continuer à jouer si la harpe a un problème. La case du rayon qui joue la même note s'allume. Si le clavier est aussi le synthé, coupez son mode « Local », sinon chaque note sonne deux fois.
- **Mode jeu** : la sortie MIDI des notes de la harpe, et un **son de harpe en plus**, joué par l'application elle-même (sur le PC, ou sur la tablette qui affiche la page — touchez alors 🔇 une fois pour activer le son ; vérifiez que la tablette n'est pas en mode silencieux).
- **Piste de contrôle** : voir chapitre 8.
- **Son du clavier** : pendant l'apprentissage, faire entendre ce que vous jouez au clavier.
- **Version Raspberry Pi seulement** : passer du réseau **Kromalaser** au Wi-Fi de la maison, et inversement. Ce n'est utile que pour un usage avancé : accéder au Pi depuis le réseau de la maison, en SSH par exemple, pour une mise à jour ou un dépannage. Pour jouer, restez sur le réseau **Kromalaser**.

**⚙ > À propos** affiche la version de l'application et celle du firmware de la harpe.

---

## 11. Les mises à jour

Quand une nouvelle version de Kromabridge paraît, un **bandeau vert** le signale en haut de la page, avec un lien **Télécharger**. Pour cela, l'appareil qui affiche la page doit avoir Internet (la page interroge alors la page des versions de Kromabridge sur GitHub) ; sans Internet, rien ne s'affiche.

- **Version PC** : téléchargez le nouveau **Kromabridge-Setup.exe** et lancez-le. Il remplace le programme et ne touche pas à vos morceaux, listes et réglages (dans Documents\Kromabridge).
- **Version Raspberry Pi** : téléchargez le fichier **kromabridge-pi.zip** de la nouvelle version, puis, dans la page du Pi, ⚙ > **Mettre à jour le Pi…** et **Choisir le fichier…** (ou **Depuis la clé USB du Pi**). Kromabridge redémarre tout seul avec la nouvelle version. En cas de souci, la même fenêtre permet de **revenir à la version précédente**. Vos morceaux, listes et réglages ne sont jamais touchés.

---

## 12. Raccourcis clavier (PC)

| Touche | Effet |
|---|---|
| ← → | Morceau précédent / suivant |
| ↑ ↓ | Liste précédente / suivante |
| Échap | Panique : coupe toutes les notes |

---

## 13. En cas de souci

- **Une note reste coincée** : 🛑 **Panique** (ou Échap). Elle coupe aussi les notes tenues par la pédale.
- **La harpe ne répond pas** : vérifiez qu'elle est allumée et branchée en USB ; la page l'indique quand elle est injoignable.
- **Aucun son sur la tablette** : touchez 🔇 en haut de la page, et vérifiez le mode silencieux.
- **Les notes sont une octave trop haut ou trop bas** sur un synthé : réglez la transposition dans le synthé ou dans la piste du séquenceur, plutôt que dans le morceau, pour qu'il reste juste sur vos autres synthés.
- **Éteindre le Pi** : toujours avec ⏻, puis attendre le message « Vous pouvez couper l'alimentation. » qui apparaît après un compte à rebours.

---

*Kromabridge — créé par Michael avec Claude.*
