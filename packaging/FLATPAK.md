# Flatpak / AppStream packaging (skeleton)

This is a **skeleton only**. It has not been submitted to Flathub and no Flathub
account was created. The app id `io.github.bluemesh_app.BlueMesh` is used
consistently everywhere and matches the GitHub account `bluemesh-app`
(Flathub turns the hyphen into an underscore).

## Files

| File | Purpose |
| --- | --- |
| `io.github.bluemesh_app.BlueMesh.yml` | Flatpak manifest: builds `bluemeshd` with cargo, installs the GUI, daemon, desktop file, metainfo and icon under `/app`. |
| `cargo-sources.json` | Vendored Rust crate sources generated from `backend/Cargo.lock`. |
| `io.github.bluemesh_app.BlueMesh.metainfo.xml` | AppStream metadata. Homepage/bugtracker point at https://github.com/bluemesh-app/BlueMesh; the donation URL is https://buymeacoffee.com/bluemesh.app. |
| `io.github.bluemesh_app.BlueMesh.desktop` | Desktop entry whose filename matches the app id. |
| `io.github.bluemesh_app.BlueMesh.sh` | Installed to `/app/bin/io.github.bluemesh_app.BlueMesh`; launches the GTK GUI and points it at `/app/bin/bluemeshd`. |

The app icon is the BlueMesh logo, stored as PNGs in `data/icons/`
(`bluemesh-32.png` … `bluemesh-512.png`, made from
`data/icons/bluemesh-logo-source.png`). The manifest installs each size as
`/app/share/icons/hicolor/<size>x<size>/apps/io.github.bluemesh_app.BlueMesh.png`
plus a `bluemesh.png` alias. If the app id changes, update those install lines
and `Icon=` in the desktop file. The per-user `install.sh` installs the same
PNGs as `bluemesh.png`.

## Remaining TODOs before a real build / Flathub submission

1. App id `io.github.bluemesh_app.BlueMesh` matches GitHub account
   `bluemesh-app` (done).
2. Homepage/bugtracker URLs in the metainfo file point at
   https://github.com/bluemesh-app/BlueMesh (done).
3. Donation URL in the metainfo file points at
   https://buymeacoffee.com/bluemesh.app (done).
4. Replace the local `dir` source with a pinned git source (URL + tag + commit).
5. Add screenshots to the metainfo before submitting.

## Before you start

Install the GNOME runtime/SDK (GNOME 51 is current on Flathub) and
`flatpak-builder`:

```sh
flatpak remote-add --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo
flatpak install flathub org.gnome.Platform//51 org.gnome.Sdk//51
flatpak install flathub org.freedesktop.Sdk.Extension.rust-stable
```

`flatpak-builder` has no `--dry-run` flag. These are the safe checks:

```sh
# Parse/normalise the YAML manifest (no build, no source download).
flatpak-builder --show-manifest packaging/io.github.bluemesh_app.BlueMesh.yml

# Resolve/download sources only; does not run cargo or any build command.
flatpak-builder --download-only packaging/io.github.bluemesh_app.BlueMesh.yml
```

## Regenerate cargo-sources.json

Run from the repository root. `cargo-sources.json` must be regenerated whenever
`backend/Cargo.lock` changes.

```sh
git clone https://github.com/flatpak/flatpak-builder-tools
python3 -m venv /tmp/fcg-venv
/tmp/fcg-venv/bin/pip install aiohttp tomlkit
/tmp/fcg-venv/bin/python flatpak-builder-tools/cargo/flatpak-cargo-generator.py \
    backend/Cargo.lock -o packaging/cargo-sources.json
```

The manifest includes `cargo-sources.json` in the module `sources` and keeps
`cargo build --offline`.

## Build and run

```sh
flatpak-builder --user --install-deps-from=flathub --force-clean build-dir packaging/io.github.bluemesh_app.BlueMesh.yml
flatpak run io.github.bluemesh_app.BlueMesh
```

## Metadata and non-Flatpak tests

```sh
appstreamcli validate --no-net packaging/io.github.bluemesh_app.BlueMesh.metainfo.xml
desktop-file-validate packaging/io.github.bluemesh_app.BlueMesh.desktop
desktop-file-validate data/bluemesh.desktop

python3 -m unittest discover -s tests -v
cargo test --manifest-path backend/Cargo.toml --release
```

The Flatpak permissions are deliberately narrow: `--share=network` for Nostr
and Bluetooth, `--allow=bluetooth` for `AF_BLUETOOTH` sockets,
`--system-talk-name=org.bluez` for BlueZ/GATT, and
`--talk-name=org.freedesktop.Notifications` for notifications. File picking and
opening use portals, so no home/filesystem access is granted.
