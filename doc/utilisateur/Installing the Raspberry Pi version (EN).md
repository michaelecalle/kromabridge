# Installing the Raspberry Pi version of Kromabridge

*Version 1.0 — 19/11/2026*

The Raspberry Pi version makes the harp standalone: the harp, the Pi and a synthesizer, with no computer. You install it only once, from a PC connected to the Internet. Allow half an hour.

## What you need

- a **Raspberry Pi 4** (Kromabridge was tested on this model; the Pi 3 and Pi 5 should work but have not been tested) and its **official power supply**;
- a **microSD card** of 16 GB or more, and a card reader for the PC;
- a **USB MIDI interface** (a USB-MIDI cable, for example) to connect the Pi to the synthesizer;
- a Windows PC connected to the Internet, and the name and password of your Wi-Fi.

## 1. Preparing the microSD card

1. On the PC, download and install **Raspberry Pi Imager** from the official website: https://www.raspberrypi.com/software/
2. Put the microSD card into the PC's reader, then start Raspberry Pi Imager.
3. Choose:
   - **Device**: the Raspberry Pi model you use (Kromabridge was tested on the Pi 4);
   - **Operating system**: *Raspberry Pi OS (other)*, then **Raspberry Pi OS Lite (64-bit)**;
   - **Storage**: your microSD card.
4. Imager offers to **customise** the system ("Edit settings" button, or successive steps depending on the Imager version). Set:
   - **localisation**: your country, your **time zone** and your keyboard;
   - **hostname**: `kromabridge`;
   - **username** and **password**: choose them and **write them down**, you will need them right after;
   - **Wi-Fi**: the name and password of your Wi-Fi;
   - **SSH** (**Services** tab, depending on the version): **enable it**, with password authentication;
   - **Raspberry Pi Connect**, if offered: **leave it disabled**, Kromabridge does not need it.
5. Start writing and wait until it finishes (a few minutes). Remove the card.

## 2. Starting the Pi

1. Put the microSD card into the Pi.
2. Plug in the power supply. Wait **two minutes**: on its first start, the Pi sets itself up and joins your Wi-Fi.

## 3. Connecting to the Pi from the PC

1. On the PC, **right-click the Start button**, then choose **Terminal** (on Windows 10: **Windows PowerShell**).
2. Type the following command, replacing **YOUR-NAME** with the **username you chose in step 1**, then Enter:

   ```
   ssh YOUR-NAME@kromabridge.local
   ```

   For example, if your username is "harpist": `ssh harpist@kromabridge.local`

3. The first time, a question asks whether you trust this device: type `yes`, then Enter.
4. Type the **password you chose in step 1**, then Enter. **Nothing is shown while you type**: that is normal.

> If `kromabridge.local` is not found, wait another minute and try again. Otherwise, look up the Pi's address in your router's page (connected devices) and use it instead, for example `ssh YOUR-NAME@192.168.1.42`.

## 4. Installing Kromabridge

Copy this command, paste it into the Terminal (right-click), then Enter:

```
curl -sSL https://github.com/michaelecalle/kromabridge/releases/latest/download/installer-pi.sh | sudo bash
```

The Pi asks for the same password again, then installs everything it needs by itself (a few minutes). At the end, "Kromabridge est installé" is shown.

One minute later, the Pi leaves your home Wi-Fi and creates its own network, **Kromalaser**: the SSH connection drops, that is normal. You can close the Terminal.

## 5. Shutting the Pi down to move it

If the Pi has to join the harp somewhere else, never cut the power directly:

1. Connect your device (laptop, tablet, phone) to the **Kromalaser** Wi-Fi (password: `kromalaser`).
2. Open **http://10.42.0.1:8080** in the browser.
3. Tap the **⏻** button at the top right, then confirm.
4. Wait for the message telling you that you can cut the power, then unplug.

Switched on again near the harp, the Pi recreates its Kromalaser network by itself. Remember to reconnect your device to your usual Wi-Fi.

## 6. First try

1. Connect the harp to the Pi by USB, and the USB MIDI interface between the Pi and your synthesizer.
2. On a tablet, phone or PC, connect to the **Kromalaser** Wi-Fi network (password: `kromalaser`).
3. Open **http://10.42.0.1:8080** in the browser.
4. In ⚙ > **App settings** > **MIDI output of the harp notes**, choose your MIDI interface.

You are ready. The rest is in the Kromabridge manual.

## Updates

When a new version is released, a green banner says so at the top of the page (if the device showing it has Internet).

1. Download the **kromabridge-pi.zip** file of the new version from the releases page: https://github.com/michaelecalle/kromabridge/releases
2. In the Pi page: ⚙ > **Update the Pi…**, then **Choose the file…** (or **From the Pi USB key**, if you copied the file onto a key plugged into the Pi).

Your songs, lists and settings are never touched. If something goes wrong, the same window lets you **go back to the previous version**.

---

*Kromabridge — created by Michael with Claude.*
