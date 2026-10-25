# BlueMesh

BlueMesh is an off-grid chat app for Linux. It talks to nearby devices over a
Bluetooth Low Energy mesh: no internet, accounts or phone numbers are needed for
mesh chat. It speaks the open BitChat mesh wire protocol, so it can chat with
BitChat-compatible devices nearby.

Features: public mesh chat, end-to-end encrypted direct messages (Noise XX),
favourites, private file transfer to accepted contacts, block list, emergency
wipe, image preview, and optional geohash "location channels" carried over Nostr
relays (these use the internet and are public).

## File transfer (accepted contacts only)

Files only go to an **accepted contact**: a *mutual favourite* (you starred them
and they starred you back). Open their chat and use the paperclip or the ⋮ menu.

* Sent **only to that person**, sealed inside your Noise session, using the same
  wire format as BitChat Android's private media (Noise payload `0x20`, file TLV,
  fragments addressed to the recipient). Relays only see ciphertext.
* BlueMesh advertises the private-media capability (bit 8) and proves it with an
  authenticated peer-state payload (Noise `0x21`) right after each handshake, so
  Android offers encrypted file transfer instead of asking to send without E2E
  encryption.
* An encrypted session must be up first (send them a message).
* Limit **52.9 KiB (54,188 bytes)** per private file: Android refuses to reassemble
  more than 256 fragments and BlueMesh sends 213-byte fragments. That budget also
  carries the file name, type and encryption overhead. Bigger files are refused
  with a message rather than lost on the phone.
* **No broadcast files.** Sending a file to everyone in range is switched off;
  `send_file` without a `peer_id` is refused by the backend.
* **Receiving:** files are kept only from mutual favourites (private or broadcast).
  Anything else is dropped unsaved with a notice "Ignored file … from non-contact".
  Kept files go to `~/.local/share/bluemesh/received/` (folder mode 700, files 600),
  with cleaned names that never overwrite. Nothing opens automatically: use
  **Preview** (images, shown inside BlueMesh), **Open** or **Show in folder**.

## Blocking

Right-click (or long-press) a person, or use ⋮ → *Block…* in their chat. Their
traffic is dropped by the mesh layer and their session closed. *Menu → Blocked
people…* lists and unblocks. The list is saved in `state.json` (`blocked_peers`,
SHA-256 fingerprints, same as the TUI). Names of blocked people are remembered
for the current session only; after a restart they show as an ID.

## Emergency wipe

*Menu → Emergency wipe…*, type `CONFIRM`. The backend overwrites and deletes
`~/.local/share/bluemesh/state.json` (identity and noise keys, Nostr seed,
nickname, favourites, verified contacts, block list, recent channels), forgets all
peers and sessions in memory, optionally deletes received files, and exits.
The app then closes; the next launch creates a brand-new identity. The backend
never wipes without `{"cmd":"wipe","confirm":true}`. A failed wipe reports that
the keys are still on disk and does not quit.

## Location channels

*Menu → Browse location channels…* explains geohash precision, lets you type a
channel and join it, and lists your last 10 channels (stored in `state.json`, so
they're removed by a wipe) with their rough coordinates and activity if joined.
The TUI's ASCII world map with live hotspots is **not** in the GTK app yet; it
needs the map sampler that subscribes to many cells at once.

## Slap

Right-click a person → *Slap with a trout* sends the same public action line the
phone app's `/slap` sends (`* you slaps them around a bit with a large trout 🐟 *`).
It's an ordinary public message, not a protocol feature.

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
Messages are not saved to disk. Received files from contacts are (see File transfer).

## Backend protocol

Commands (stdin): send{text} · dm{peer_id,text} · read{peer_id,ids} · nick{name} ·
join{channel} · leave{channel} · channel_send{channel,text} · favorite{peer_id,on} ·
send_file{path,peer_id} · block{peer} · unblock{peer} · blocks · wipe{confirm,files} ·
channels · peers · quit.
Events (stdout): ready, status, peripheral, peers, peer_joined/left, public, dm, sent
(kind public/dm/channel/file), file_sending{name,size,parts},
file{name,path,size,sender,peer_id,ts,mime,kind,private}, file_ignored{sender,name,private},
blocked, unblocked, blocks{blocked[]}, wiped{ok,existed,files_removed}, channels{joined,recent}, delivery,
session_up, nick, favorite, channel_joined/left/msg/people/live, notice, error, log, bye.

## License

MIT – see `LICENSE`. The radio stack in `backend/` is a fork of
[bitmancer](https://github.com/Mhsbrian/bitmancer) (MIT, a descendant of
bitchat-tui); its copyright notices are preserved in `LICENSE` and `backend/LICENSE`.
"BitChat" refers only to the mesh protocol BlueMesh is compatible with; BlueMesh is
not affiliated with or endorsed by the BitChat project.
