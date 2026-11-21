# Kromabridge — User manual

*Version 1.0 — 19/11/2026*

Kromabridge is the bridge between a Kromalaser laser harp and your synthesizers. It chooses the note played by each beam, organises your songs and song lists, controls the harp (opening, closing, settings) and can be driven from your music software (DAW) or a sequencer.

It comes in two versions that do exactly the same thing:

- **the PC version** (Windows): the harp is connected to the PC by USB, and the notes go to your music software (DAW, VST instrument…) through a virtual MIDI port;
- **the Raspberry Pi version**: the harp is connected to a Raspberry Pi (simply called "the Pi" in this manual), and the notes go to a synthesizer through a MIDI cable. Everything is set from a tablet, a phone or a computer, with no computer connected to the harp: the harp becomes standalone.

> The Raspberry Pi version was developed and tested on a **Raspberry Pi 4**. Other models, in particular the Pi 3 and Pi 5, should work but have not been tested. Always use the **official power supply** for your Raspberry Pi model.

### Compatible harps

Kromabridge was developed and tested with a **KL 3d-3000** running **firmware 1.440** (Manager V24). The firmware version matters more than the model.

> **Kromabridge is designed for firmware 1.440**, the only one that has been tested (it is the latest version published by the manufacturer, dated 2017). Any use with another firmware or another model is at the user's own risk.


- **KL 3d-1000, 3d-3000, 3d-5000**: they only differ in laser power and use the same firmware. With firmware 1.440, they should work exactly the same way.
- **KL 3d-EVO** (controller for an external laser projector): its firmware 1.440 handles MIDI commands exactly like the KL 3d-1000's (comparison of the manufacturer's firmware files). It should therefore be fully compatible, but it has not been tested.
- **KL 3d-PRO**: its firmware (version 1.3) is older. Note functions (songs, parts, lists, control track) should work, but the settings Kromabridge sends (number of beams, opening animation, harp settings page) may not be applied. Not tested.
- **Firmware older than 1.440** (1.320, for example): first update the harp with the manufacturer's update software (KromaUpdater).

Note functions only need the harp's MIDI notes: they should work with every model. Your harp's firmware version is shown in ⚙ > About.

---

## 1. Getting started

### PC version

**Installation** (only once): run **Kromabridge-Setup.exe**, downloaded from https://github.com/michaelecalle/kromabridge/releases, and follow the steps. The installer also helps you install **loopMIDI**, the free software that creates the virtual MIDI ports, and creates the **Kromabridge** and **Controle Harpe** ports itself. Your songs, lists and settings are stored in **Documents\Kromabridge**.

1. Switch the harp on and connect it to the PC by USB.
2. Double-click the **Kromabridge** shortcut on the desktop. The application page opens in your browser.
3. In your music software, choose **Kromabridge** as the MIDI input of the track that should play the harp, and arm it (monitoring).

### Raspberry Pi version

**Installation** (only once): follow the "Installing the Raspberry Pi version" guide, supplied with the PC installer and on the releases page.

1. Connect the harp to the Pi by USB, and the Pi MIDI output to your synthesizer.

   > The Raspberry Pi has no MIDI socket: plug **a USB MIDI interface** (a USB-MIDI cable, for example) into one of its USB ports. Two different interfaces were tested: both worked as soon as they were plugged in, with no setup. A MIDI board connected to the Pi's GPIO pins has not been tested and may need extra configuration.
2. Switch the Pi on. After about one minute, it creates the **Kromalaser** Wi-Fi network (password: `kromalaser`).
3. Connect the device of your choice to this network (tablet, phone, computer: any device with Wi-Fi and a web browser), then open **http://10.42.0.1:8080** in the browser.

   > On a Windows PC, the first time, Windows sometimes asks for a **PIN**: then click « **Connect using a security key instead** » (or « … a password », depending on the version), and type `kromalaser`.

> Tip: add the page to the tablet home screen to open it like an app.

### The page at a glance

At the top, the command bar, from left to right:

| Item | Role |
|---|---|
| ▲ ▼ | Previous / next song list |
| List name | The active song list ("All" when no list is chosen) |
| ◀ ▶ | Previous / next song |
| Song title | The loaded song, and its position in the list, for example (2/12) |
| **Open the harp** / **Close the harp** | Switches the beam fan on or off |
| **Song** menu | Open, create, rename, delete, import, export… |
| **Lists** menu | Choose and manage song lists |
| **Learning** menu | Create or change beams from a keyboard |
| 🔇 | Enable the harp sound on this device (if that setting is used) |
| 🛑 **Panic** | Immediately cuts every sounding note |
| ⏻ | Shut the Pi down properly (Raspberry Pi version only) |
| ⚙ | App settings, harp settings, About |

A small orange dot on ⚙ means harp settings were changed but not stored yet.

Below the bar: the song settings, then **one tile per beam**. When you cut a beam, its tile lights up.

---

## 2. Songs

A song (a "memory") holds everything needed to play a piece: the note of each beam, the number of beams, the MIDI channel, the synth sound, the harp settings, the opening animation…

**Every change is saved immediately**: there is no "Save" button.

### Song menu

- **Open…**: choose a song from the list.
- **Manage songs…**: every song at a glance, with its number. For each one: **Open**, **Rename** (without loading it) or **✕** (delete). Tick several songs to **Delete ticked songs** or **Create a list with the ticked songs**.
- **New song**: a blank song, or a copy of an existing one.
- **Save as…**: copy the displayed song under another name.
- **Rename**, **Delete**: a deleted song goes to the trash.
- **Trash…**: restore a deleted song, or empty the trash.
- **Import a MIDI file** / **Edit the import**: see chapter 6.
- **Export songs** / **Import songs**: move songs between the PC and the Pi (a .kromalaser file, which also holds the imported MIDI file). If a song already exists, you choose to replace it (the old one goes to the trash) or keep both.
- **Import from the USB key…** (Raspberry Pi version only): see "The Pi USB key" below.

### The Pi USB key

A USB key plugged into the Pi lets you exchange files with the PC without a computer near the harp: very handy when you set up the Pi from a tablet.

- **Save to the key**: in the **Export songs** (or **Export lists**) window, tick **Save to the Pi USB key**. The file goes into the **Kromabridge** folder of the key.
- **Read the key**: **Song > Import from the USB key…** (or **Lists > Import from the USB key…**) shows the .kromalaser files on the key; tap the one to import.
- **Before removing the key**, tap **Eject the key** in that same window.

On the PC these options do not appear: the file-open window already gives access to USB keys.

### Song settings

- **Number of beams**: 5 to 15 (harp limit).
- **MIDI output channel**: the channel the notes are sent on (1 to 16).
- **Change the synth sound when the song is loaded**: when ticked, the synth sound you chose (program number, plus bank MSB / LSB if needed) is sent when the song loads. Unticked or empty: the synth sound is left alone. The list on the left chooses your synth's numbering (1 to 128, 0 to 127, or 11 to 88 for synths with 8 banks of 8 sounds). Typing a number ticks the Pi automatically.
- **Opening animation**: on opening, the fan starts narrow then widens progressively (pause, then opening time in seconds).
- **Animated transition between parts**: when changing part, the fan closes in then opens again.

### The "Gamme" (scale) song

This special song is always there, at the end of the list. Each beam plays the note sent by the harp itself, unchanged. It is used for tests and for the "tones / half tones" colours. It cannot be renamed, deleted, or have its notes changed.

---

## 3. Beams

Each tile is a beam, from left (low) to right (high).

- **Change the note**: tap the note name and pick from the list. The note is played so you can hear it.
- **-12 / -1 / +1 / +12**: move the note down or up by an octave or a semitone.
- **All beams**: shift every note at once (-12, -1, +1, +12), or **Sort from low to high**.
- **Chord**: make one beam play several notes. Choose a ready-made chord (major, minor, 7th…) built on the beam note, or add notes one by one.
- **✕**: delete this beam. The following beams move one step to the left and keep their note. Not possible below 5 beams.

> Reducing the number of beams with the "Number of beams" list always removes the last beam on the right. To choose which one goes, use ✕.

---

## 4. Parts

A song can have several **parts** (A, B, C…), each with its own notes and number of beams. The part buttons appear below the song settings.

- **Change part**: tap its tile (A, B, C…).
- **Add a part**: tap **＋**, then choose a **blank part** (5 beams) or a **copy** of an existing part.
- **Reorder**: drag a tile left or right. Letters follow the position: the part moved to second place becomes B.
- **Delete parts**: tap **－**, tap the parts to delete, then **Delete**. A song always keeps at least one part.
- Moving or deleting a part changes the letters of the following ones: if the song has a control track (chapter 8), the application warns you and offers to re-export it.
- When a song is loaded, part A is always active.
- Each part can have **its own synth sound**: select the part, then set the line "Change the synth sound for part…". A part whose box is unticked does not change the sound.
- Parts are also created by the MIDI import (chapter 6) or by keyboard learning (chapter 5).
- With the sustain pedal down, held notes keep sounding during a part change.

---

## 5. Keyboard learning

A MIDI keyboard creates or edits beams very quickly. Choose your keyboard in ⚙ > App settings.

- **Create beams from keyboard**: play the song notes on the keyboard, in any order, then tap **Finish**. They become the beams, from low to high. You can create a new song, replace the notes of the displayed song, or chain several parts.
- **Change a beam from keyboard**: cut a beam (or tap its tile), then play on the keyboard the note it should play. Each note changes the beam at once. **Keep the changes** confirms, **Undo the changes** puts the song back as before.

---

## 6. Importing a MIDI file

**Song > Import a MIDI file** turns a track of a MIDI file into a song.

1. Choose the .mid file, then tick the harp track(s). A preview (like in a sequencer) shows the notes; ▶ lets you listen.
2. If the track holds more than 15 different notes, untick the ones to ignore (the harp has at most 15 beams).
3. Set the octave transposition if needed, then the title.
4. **Parts**: select an area of the preview (in bars) and add it as a part, or let the application create the parts.
5. **Harp commands**: place in the preview the moments when the harp should open, close or change part, then **Export the control track** (see chapter 8).
6. **Create the song**.

**Edit the import** reopens the import as you left it. "Update the song" keeps the parts you edited by hand.

---

## 7. Song lists

The **Lists** menu gives you, besides the list of all songs, song lists in the order you choose.

- **Manage lists…**: create, rename, delete a list; add songs; reorder them by dragging the ☰ handle (finger or mouse) or with the ↑ ↓ arrows. Confirm with **Apply**.
- **Song > Manage songs…** can also create a list in one go from the ticked songs.
- While playing, ◀ ▶ go to the previous / next song **of the active list**, and ▲ ▼ change list. The name of the active list is shown at the top, next to ▲ ▼.
- **Export lists…**: saves one list, or all of them, into a .kromalaser file. Each list takes its songs along.
- **Import lists…**: tick the lists to import; they arrive with their songs. A list with the same name is replaced.
- On the Pi, export and import can go through the USB key (chapter 2).
- **Warning**: the order of the "All songs" list gives each song its **number**, used by the control track (chapter 8). If you change it, the application warns you and offers to re-export the affected control tracks. A new song always goes at the end, so no number moves. Deleting a song, however, moves the following songs' numbers down by one (delete number 2 and number 3 becomes number 2): you get the same warning.

---

## 8. Driving the harp from your DAW (control track)

A MIDI track in your sequencer can load a song, open and close the harp, and change part at the right moment of the song. Everything happens on **MIDI channel 16**:

| Message (channel 16) | Effect |
|---|---|
| Program change n | Loads song number n (1 = the first one in "All songs") |
| Note C-1 (C-2 in a DAW where middle C is C3) | Opens the harp |
| Note C#-1 (C#-2) | Closes the harp |
| Notes C0, C#0, D0… (C-1, C#-1, D-1…) | Parts A, B, C… |

- **PC version**: the DAW control track goes out to the **Controle Harpe** port.
- **Raspberry Pi version**: the track goes out through a MIDI output of your interface, plugged into the Pi MIDI input.
- Tick **Control from the DAW** in ⚙ > App settings. The page shows the last commands received.
- The easiest way: prepare the commands during the MIDI import (chapter 6), then **Export the control track**, and drop the file into your DAW.

> If the DAW sends the same song again at every playback start, the application does not reload everything: it just goes back to part A.

---

## 9. Harp settings

**⚙ > Harp settings** gathers all the settings of the original Manager, with the same names. Each setting has a **?** button explaining what it does.

Tabs: System, Footswitches, Beams, Colors, Colors (legacy), Transpose, Presets, Monitor.

- **Read the harp**: shows the current harp settings.
- A changed setting is sent to the harp at once, and shown in orange until it is stored.
- **Store in this song**: the song will remember these settings and send them back to the harp each time it loads.
- **Store in all songs**: the same for every song (the number of beams stays specific to each song).
- **Restore stored settings**: cancels the changes that were not stored.
- **Common to all songs**: tick this Pi for a setting that depends on the venue rather than the song (fan width, sensitivity…). It is stored once and applies to all songs.

### The "Beam colours" card

In the **Colors** tab, simply choose the beam colours and the application sets the modes and powers of the three lasers itself:

- **touch**: one colour for beams at rest, one for touched beams;
- **tones and half tones**: one colour for tones (C, D, E…), one for half tones (C#, D#…), and the colour added when a beam is touched. This choice only works with the Gamme song, because these are the harp's own notes.

The preview shows the result. Since a laser can only have one power, some combinations cannot be made exactly: the application tells you.

The **Colors (legacy)** tab gives access to the original laser settings, locked by default ("Edit by hand" box).

### Harp presets

The harp itself keeps 40 presets, which can be recalled from the footswitch without a computer. A preset only holds the Manager settings: number of beams, width, colours, footswitches, transpose… Each beam's note, chords, parts, the animation, the synth sound, the MIDI channel and the control track only exist in Kromabridge: on its own, the harp can only play a chromatic scale, possibly transposed. The collapsible table at the top of the **Presets** tab sums up what each one keeps.

- **Viewing a preset**: choose it in the list. Kromabridge reads it (the harp applies it for a moment, then goes back to its previous state; nothing is written to the harp) and shows its **differences with the current song**, then its full content. A preset already read then shows instantly, with the date it was read. **Read this preset again** and **Read all presets** (about twenty seconds) refresh the copy.
- **Save**: saves the current song's settings into a preset. A window always asks which preset, without choosing one in advance; presets that seem already programmed are marked "⚠ (programmed)". If the song's beams form a continuous scale, the transposition is saved too, and the preset will play the same notes.
- **Recall**: loads the displayed preset **into a new song** (its settings, and the notes it plays: a starting point) or **into the current song** (its settings only; the beam notes do not change).
- **Put back the previous content**: before writing into a preset, Kromabridge keeps its previous content. This button puts it back, in case of a mistake.

Reading and saving are done **with the harp closed**: for a moment, the harp really applies the preset. Saving a preset writes into the harp's memory, as the original Manager does: Kromabridge only does it when you ask.

### Monitor

The **Monitor** tab shows every message exchanged with the harp, to understand what is going on.

---

## 10. App settings

**⚙ > App settings**:

- **Language**: French or English, for all the application texts.
- **Note names**: C, D, E… or do, ré, mi…, independently of the language (for example, the application in English with do, ré, mi notes). Middle C (MIDI note 60) is called C4 / do4.
- **Keyboard for assigning notes**: the MIDI keyboard used for learning.
- **The keyboard plays like the harp**: the notes of this keyboard go to the song output, unchanged: a backup to keep playing if the harp has a problem. The tile of the beam playing the same note lights up. If the keyboard is also the synth, turn its "Local" mode off, otherwise each note sounds twice.
- **Play mode**: the MIDI output for the harp notes, and an **extra harp sound** played by the application itself (on the PC, or on the tablet showing the page — then tap 🔇 once to enable sound; check the tablet is not in silent mode).
- **Control track**: see chapter 8.
- **Keyboard sound**: during learning, hear what you play on the keyboard.
- **Raspberry Pi version only**: switch between the **Kromalaser** network and your home Wi-Fi. This is only useful for advanced use: reaching the Pi from your home network, over SSH for example, for an update or troubleshooting. To play, stay on the **Kromalaser** network.

**⚙ > About** shows the application version and the harp firmware version.

---

## 11. Updates

When a new version of Kromabridge is released, a **green banner** says so at the top of the page, with a **Download** link. For this, the device showing the page needs the Internet (the page then checks the Kromabridge releases page on GitHub); without the Internet, nothing is shown.

- **PC version**: download the new **Kromabridge-Setup.exe** and run it. It replaces the program and does not touch your songs, lists and settings (in Documents\Kromabridge).
- **Raspberry Pi version**: download the **kromabridge-pi.zip** file of the new version, then, in the Pi page, ⚙ > **Update the Pi…** and **Choose the file…** (or **From the Pi USB key**). Kromabridge restarts by itself with the new version. If something goes wrong, the same window lets you **go back to the previous version**. Your songs, lists and settings are never touched.

---

## 12. Keyboard shortcuts (PC)

| Key | Effect |
|---|---|
| ← → | Previous / next song |
| ↑ ↓ | Previous / next list |
| Esc | Panic: cuts all notes |

---

## 13. Troubleshooting

- **A note is stuck**: 🛑 **Panic** (or Esc). It also cuts notes held by the pedal.
- **The harp does not respond**: check it is on and connected by USB; the page shows when it cannot be reached.
- **No sound on the tablet**: tap 🔇 at the top of the page, and check silent mode.
- **Notes are an octave too high or too low** on a synth: set the transposition in the synth or in the sequencer track rather than in the song, so it stays right on your other synths.
- **Shutting the Pi down**: always with ⏻, then wait for the message telling you that you can cut the power, shown after a countdown.

---

*Kromabridge — created by Michael with Claude.*
