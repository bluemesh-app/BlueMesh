# BlueMesh

BlueMesh is an off-grid chat app for Linux. It talks to nearby devices over a
Bluetooth Low Energy mesh: no internet, accounts or phone numbers are needed for
mesh chat. It speaks the open BitChat mesh wire protocol, so it can chat with
BitChat-compatible devices nearby.

Features: public mesh chat, end-to-end encrypted direct messages (Noise XX),
favourites, mesh file transfer, and optional geohash "location channels" carried
over Nostr relays (these use the internet and are public).

## File transfer

Use the paperclip in the Public room (or *Menu → Send file to everyone nearby…*)
to send a file over the Bluetooth mesh, the same way bitchat-tui / bitmancer
`/send` does:

* Limit **1 MiB** per file. Larger, empty or unreadable files are refused with a message.
* Files go to **everyone in range** and are **not encrypted**. There is no private
  (DM) file path in the mesh yet. A 1 MiB file is several thousand Bluetooth
  fragments, so expect it to take a while.
* Received files are saved to `~/.local/share/bluemesh/received/` (folder mode 700,
  files mode 600). The name is cleaned and never overwrites an existing file.
  Nothing is opened automatically. Use **Open** or **Show in folder** in the chat.
* Files over 64 KiB go out as v2 frames (4-byte length field), because a v1 frame can't hold them.

## Password-protected channels

Upstream BitChat removed password-protected mesh channels from the protocol in
July 2025. BlueMesh doesn't offer them. Use encrypted direct messages instead.

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
Messages are not saved to disk. Received files are (see File transfer).

## Backend protocol

Commands (stdin): send{text} · dm{peer_id,text} · read{peer_id,ids} · nick{name} ·
join{channel} · leave{channel} · channel_send{channel,text} · favorite{peer_id,on} ·
send_file{path} · peers · quit.
Events (stdout): ready, status, peripheral, peers, peer_joined/left, public, dm, sent
(kind public/dm/channel/file), file_sending{name,size,parts},
file{name,path,size,sender,peer_id,ts,mime,kind}, delivery,
session_up, nick, favorite, channel_joined/left/msg/people/live, notice, error, log, bye.

## License

MIT – see `LICENSE`. The radio stack in `backend/` is a fork of
[bitmancer](https://github.com/Mhsbrian/bitmancer) (MIT, a descendant of
bitchat-tui); its copyright notices are preserved in `LICENSE` and `backend/LICENSE`.
"BitChat" refers only to the mesh protocol BlueMesh is compatible with; BlueMesh is
not affiliated with or endorsed by the BitChat project.
