# Installing the Raspberry Pi version of Kromabridge

*Version 1.1 — 21/11/2026*

The Raspberry Pi version makes the harp standalone: the harp, the Pi and a synthesizer, with no computer. You prepare the microSD card only once, from any computer connected to the Internet. Allow a quarter of an hour, with no command line at all.

## What you need

- a **Raspberry Pi** (Kromabridge was tested on a Raspberry Pi 4; the Pi 3 and Pi 5 should work but have not been tested) and its **official power supply**;
- a **microSD card** of 16 GB or more, and a card reader for the computer;
- a **USB MIDI interface** (a USB-MIDI cable, for example) to connect the Pi to the synthesizer.

> The Raspberry Pi has no MIDI socket: the USB MIDI interface plugs into one of its USB ports. A MIDI board connected to the Pi's GPIO pins has not been tested and may need extra configuration.

## 1. Downloading the Kromabridge image

On the releases page, https://github.com/michaelecalle/kromabridge/releases/latest, download the **kromabridge-pi-….img.xz** file (under "Assets", at the bottom of the page). Do not unzip it.

## 2. Writing the microSD card

1. Download and install **Raspberry Pi Imager** from the official website: https://www.raspberrypi.com/software/
2. Put the microSD card into the computer's reader, then start Raspberry Pi Imager.
3. Choose:
   - **Device**: the Raspberry Pi model you use;
   - **Operating system**: at the very bottom of the list, **Use custom**, then the downloaded **kromabridge-pi-….img.xz** file;
   - **Storage**: your microSD card.
4. If Imager offers to **customise** the system, answer **No**: everything is already set in the image.
5. Start writing, confirm that the card will be erased, and wait until it finishes (a few minutes). Remove the card.

## 3. Starting the Pi

1. Put the microSD card into the Pi.
2. Connect the harp to the Pi by USB, and the USB MIDI interface between the Pi and your synthesizer.
3. Plug in the power supply. The first start takes **two to three minutes** (the Pi sets itself up and sometimes restarts once by itself).

## 4. Opening Kromabridge

1. On a tablet, phone or computer, connect to the **Kromalaser** Wi-Fi network (password: `kromalaser`).

   > On a Windows PC, the first time, Windows sometimes asks for a **PIN**: then click « **Connect using a security key instead** » (or « … a password », depending on the version), and type `kromalaser`.
2. Open **http://10.42.0.1:8080** in the browser.
3. In ⚙ > **App settings** > **MIDI output of the harp notes**, choose your MIDI interface.

You are ready. The rest is in the Kromabridge manual.

## 5. Shutting the Pi down

Never cut the power directly: in the Kromabridge page, tap the **⏻** button at the top right, confirm, and wait for the message telling you that you can cut the power. When switched on again, the Pi recreates its Kromalaser network by itself.

## Updates

When a new version is released, a green banner says so at the top of the page (if the device showing it has Internet).

1. Download the **kromabridge-pi.zip** file of the new version from the releases page: https://github.com/michaelecalle/kromabridge/releases
2. In the Pi page: ⚙ > **Update the Pi…**, then **Choose the file…** (or **From the Pi USB key**, if you copied the file onto a key plugged into the Pi).

Your songs, lists and settings are never touched. If something goes wrong, the same window lets you **go back to the previous version**.

## For advanced users

- **SSH access**: connected to the Kromalaser network, `ssh kromalaser@10.42.0.1` (password `kromalaser`). Change this password with the `passwd` command if you wish.
- **Installing Kromabridge on an existing Raspberry Pi OS** (instead of the image): with the Pi connected to the Internet, logged in over SSH with your own account, type:

  ```
  curl -sSL https://github.com/michaelecalle/kromabridge/releases/latest/download/installer-pi.sh | sudo bash
  ```

---

*Kromabridge — created by Michael with Claude.*
