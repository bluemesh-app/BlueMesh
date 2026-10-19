# BitChat Desktop

A GNOME (GTK4 + libadwaita) desktop app for the BitChat Bluetooth mesh.

* `gui/bitchat_desktop.py` – the window (Python, PyGObject; system packages only).
* `backend/` – fork of [bitmancer](https://github.com/Mhsbrian/bitmancer) (MIT, a maintained
  descendant of bitchat-tui that speaks the current BitChat wire protocol). Added
  `src/bin/bitchat-desktopd/` – a headless node that speaks line-delimited JSON on stdin/stdout,
  plus a BlueZ GATT-server/advertising role (`peripheral.rs`) so other computers can connect to it.
* `install.sh` – installs into `~/.local` (no sudo).

Build: `cd backend && cargo build --release --bin bitchat-desktopd && cd .. && ./install.sh`

Identity/keys: `~/.local/share/bitchat-desktop/state.json` (mode 600). Messages are not saved to disk.

Protocol (stdin commands): send{text} · dm{peer_id,text} · read{peer_id,ids} · nick{name} ·
join{channel} · leave{channel} · channel_send{channel,text} · peers · quit.
Events (stdout): ready, status, peripheral, peers, peer_joined/left, public, dm, sent, delivery,
session_up, nick, channel_joined/left/msg/people/live, notice, error, log, bye.
