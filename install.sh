#!/bin/sh
# Installs BlueMesh into ~/.local (no sudo). Re-run after rebuilding.
set -e
here=$(cd "$(dirname "$0")" && pwd)
bin="$HOME/.local/bin"; share="$HOME/.local/share"
mkdir -p "$bin" "$share/bluemesh/app" "$share/applications"
chmod 700 "$share/bluemesh"
install -m 755 "$here/backend/target/release/bluemeshd" "$bin/bluemeshd"
install -m 644 "$here/gui/bluemesh.py" "$share/bluemesh/app/bluemesh.py"
install -m 644 "$here/LICENSE" "$share/bluemesh/app/LICENSE"
cat > "$bin/bluemesh" <<'EOS'
#!/bin/sh
exec /usr/bin/python3 "$HOME/.local/share/bluemesh/app/bluemesh.py" "$@"
EOS
chmod 755 "$bin/bluemesh"
rm -f "$share/icons/hicolor/scalable/apps/bluemesh.svg"   # old icon
for s in 32 48 64 128 256 512; do
  install -Dm644 "$here/data/icons/bluemesh-$s.png" "$share/icons/hicolor/${s}x${s}/apps/bluemesh.png"
done
sed "s|^Exec=.*|Exec=$bin/bluemesh|" "$here/data/bluemesh.desktop" > "$share/applications/bluemesh.desktop"
chmod 644 "$share/applications/bluemesh.desktop"
gtk-update-icon-cache -q -t "$share/icons/hicolor" 2>/dev/null || true
update-desktop-database -q "$share/applications" 2>/dev/null || true
echo "Installed. Launch 'BlueMesh' from the app grid or run bluemesh."
