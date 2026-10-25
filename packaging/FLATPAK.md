# Flatpak / AppStream packaging

Not yet submitted to Flathub. The app id `io.github.bluemesh_app.BlueMesh` is
used consistently everywhere and matches the GitHub account `bluemesh-app`
(Flathub turns the hyphen into an underscore).

The manifest builds from the pinned `v0.1.0` git tag
(https://github.com/bluemesh-app/BlueMesh/releases/tag/v0.1.0), and the
metainfo screenshot URLs point at that tag rather than `master`.

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

## Release checklist

- [x] App id `io.github.bluemesh_app.BlueMesh` matches GitHub account `bluemesh-app`.
- [x] Homepage/bugtracker URLs point at https://github.com/bluemesh-app/BlueMesh.
- [x] Donation URL points at https://buymeacoffee.com/bluemesh.app.
- [x] Screenshots in the metainfo, pinned to the `v0.1.0` tag.
- [x] `v0.1.0` tag and GitHub release.
- [x] Manifest uses a pinned git source (URL + tag + commit) instead of a local `dir`.
- [ ] Full local `flatpak-builder` build and run from the git source.
- [ ] `flatpak-builder-lint` passes on the manifest and the built repo.
- [ ] Open the Flathub submission PR (by hand, see `FLATHUB-SUBMISSION.md`).

For each new release: tag it, add a `<release>` entry to the metainfo, point
the screenshot URLs at the new tag, update `tag:` and `commit:` in the
manifest, and regenerate `cargo-sources.json` if `backend/Cargo.lock` changed.

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

# Resolve/download sources only (clones the v0.1.0 tag); no build.
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

### Ubuntu: using org.flatpak.Builder

If Ubuntu's `flatpak-builder` is too old or fails, use the Flathub one
(`org.flatpak.Builder`). It runs sandboxed, so point it at your user install:

```sh
flatpak install --user flathub org.flatpak.Builder
FLATPAK_USER_DIR=$HOME/.local/share/flatpak flatpak run --filesystem=home --env=FLATPAK_USER_DIR=$HOME/.local/share/flatpak org.flatpak.Builder --user --install --install-deps-from=flathub --force-clean build-dir packaging/io.github.bluemesh_app.BlueMesh.yml
```

Lint the way Flathub does:

```sh
flatpak install flathub org.flatpak.Builder
flatpak run --command=flatpak-builder-lint org.flatpak.Builder manifest packaging/io.github.bluemesh_app.BlueMesh.yml
flatpak-builder --repo=repo --force-clean build-dir packaging/io.github.bluemesh_app.BlueMesh.yml
flatpak run --command=flatpak-builder-lint org.flatpak.Builder repo repo
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
