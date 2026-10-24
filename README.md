# BlueMesh

BlueMesh is an off-grid chat app for Linux. It talks to nearby devices over a
Bluetooth Low Energy mesh: no internet, accounts or phone numbers are needed for
mesh chat. It speaks the open BitChat mesh wire protocol, so it can chat with
BitChat-compatible devices nearby.

Features: public mesh chat, end-to-end encrypted direct messages (Noise XX),
favourites, and optional geohash "location channels" carried over Nostr relays
(these use the internet and are public).

## Layout

* `gui/bluemesh.py` – the GTK4 + libadwaita window (Python, PyGObject; system packages only).
* `backend/` – the radio stack, a fork of bitmancer (MIT). Adds `src/bin/bluemeshd/`,
  a headless node that speaks line-delimited JSON on stdin/stdout, plus a BlueZ
  GATT-server/advertising role (`peripheral.rs`) so other devices can connect to it.
* `data/` – desktop entry and icon.
* `install.sh` – per-user install into `~/.local` (no root needed).
* `tests/fake_backend.py` – stand-in backend for GUI tests (no Bluetooth).

## Requirements

Linux with BlueZ, Python 3, PyGObject, GTK 4 and libadwaita (1.2 or newer),
and a Rust toolchain to build the backend.

## Build and install

    cd backend && cargo build --release --bin bluemeshd && cd ..
    ./install.sh

Then launch **BlueMesh** from the app grid, or run `bluemesh`.

Identity and keys are stored in `~/.local/share/bluemesh/state.json` (mode 600).
Messages are not saved to disk.

## Backend protocol

Commands (stdin): send{text} · dm{peer_id,text} · read{peer_id,ids} · nick{name} ·
join{channel} · leave{channel} · channel_send{channel,text} · favorite{peer_id,on} · peers · quit.
Events (stdout): ready, status, peripheral, peers, peer_joined/left, public, dm, sent, delivery,
session_up, nick, favorite, channel_joined/left/msg/people/live, notice, error, log, bye.

## License

MIT – see `LICENSE`. The radio stack in `backend/` is a fork of
[bitmancer](https://github.com/Mhsbrian/bitmancer) (MIT, a descendant of
bitchat-tui); its copyright notices are preserved in `LICENSE` and `backend/LICENSE`.
"BitChat" refers only to the mesh protocol BlueMesh is compatible with; BlueMesh is
not affiliated with or endorsed by the BitChat project.
