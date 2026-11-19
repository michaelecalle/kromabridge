"""Le moniteur MIDI decode le flux brut de la harpe."""

from kromabridge.monitor import MidiMonitor


def textes(m, depuis=0):
    return [e["texte"] for e in m.since(depuis)["messages"]]


def test_notes_with_running_status_and_realtime_inside():
    m = MidiMonitor()
    m.feed(bytes([0x90, 0x3C, 0x70, 0x3E]))       # running status : 2e note coupee en deux
    m.feed(bytes([0xFA, 0x70, 0x80, 0x3C, 0x00]))  # FA intercale
    assert textes(m) == [
        "Note jouée do4 (60), vélocité 112, canal 1",
        "Temps réel : Start",
        "Note jouée ré4 (62), vélocité 112, canal 1",
        "Note relâchée do4 (60), canal 1",
    ]


def test_cc_pitch_bend_and_sysex():
    m = MidiMonitor()
    m.feed(bytes([0xB0, 0x78, 0x00, 0xE0, 0x00, 0x40, 0xE0, 0x7F, 0x7F]))
    m.feed(bytes.fromhex("F07B01067A01040400F7"))
    t = textes(m)
    assert t[0] == "Contrôleur 120 (tout son coupé) = 0, canal 1"
    assert t[1] == "Pitch bend +0, canal 1"
    assert t[2] == "Pitch bend +8191, canal 1"
    assert t[3].startswith("Sysex (F0 7B 01 06 7A 01")


def test_since_returns_only_new_messages_and_limit():
    m = MidiMonitor(size=3)
    for n in range(5):
        m.feed(bytes([0x90, 60 + n, 100]))
    etat = m.since(0)
    assert etat["dernier"] == 5 and [e["n"] for e in etat["messages"]] == [3, 4, 5]
    assert [e["n"] for e in m.since(4)["messages"]] == [5]


def test_channel_messages_reach_the_engine_callback_before_display():
    recus = []
    m = MidiMonitor()
    m.on_message = recus.append
    m.feed(bytes([0x90, 0x3C, 0x70, 0xF0, 0x7B, 0x01, 0x70, 0x01, 0xF7, 0xFA, 0x80, 0x3C, 0x00]))
    assert recus == [[0x90, 0x3C, 0x70], [0x80, 0x3C, 0x00]]   # ni sysex ni temps reel
