"""Petit programme separe qui parle a la harpe (version PC).

Sous Windows, un envoi vers la harpe peut attendre sans fin si elle cesse de
repondre (lampe rouge), et bloque alors tout le processus qui l'a fait. On isole
donc ces envois ici : le programme principal nous parle par une ligne JSON par
commande et attend notre reponse ; si nous ne repondons plus, il nous arrete,
signale "harpe injoignable", et nous relance a la commande suivante.

Protocole (une ligne par message) :
  -> {"m": [[0xB0, 45, 13], [0xF0, ...]], "hint": "kromalaser usb"}
  <- "ok <nom du port>"  ou  "err <raison>"
"""

from __future__ import annotations

import json
import sys
import time

ECART_S = 0.002          # intervalle minimum entre deux messages vers la harpe


def main() -> int:
    import rtmidi
    sortie = rtmidi.MidiOut()
    nom = None
    dernier = 0.0
    for ligne in sys.stdin:
        try:
            demande = json.loads(ligne)
            hint = str(demande.get("hint", "kromalaser usb")).lower()
            ports = sortie.get_ports()
            index = next((i for i, p in enumerate(ports) if hint in p.lower()), None)
            if index is None:
                raise RuntimeError("harpe absente")
            if nom != ports[index] or not sortie.is_port_open():
                if sortie.is_port_open():
                    sortie.close_port()
                sortie.open_port(index)
                nom = ports[index]
            for message in demande.get("m", []):
                attente = ECART_S - (time.perf_counter() - dernier)
                if attente > 0:
                    time.sleep(attente)
                sortie.send_message(list(message))
                dernier = time.perf_counter()
            sys.stdout.write("ok " + nom + "\n")
        except Exception as exc:          # on repond toujours, sauf blocage
            nom = None
            sys.stdout.write("err " + str(exc).replace("\n", " ") + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
