#!/bin/sh
# Installs BitChat Desktop into ~/.local (no sudo). Re-run after rebuilding.
set -e
here=$(cd "$(dirname "$0")" && pwd)
bin="$HOME/.local/bin"; share="$HOME/.local/share"
mkdir -p "$bin" "$share/bitchat-desktop/app" "$share/applications" "$share/icons/hicolor/scalable/apps"
install -m 755 "$here/backend/target/release/bitchat-desktopd" "$bin/bitchat-desktopd"
install -m 644 "$here/gui/bitchat_desktop.py" "$share/bitchat-desktop/app/bitchat_desktop.py"
cat > "$bin/bitchat-desktop" <<'EOS'
#!/bin/sh
exec /usr/bin/python3 "$HOME/.local/share/bitchat-desktop/app/bitchat_desktop.py" "$@"
EOS
chmod 755 "$bin/bitchat-desktop"
install -m 644 "$here/data/bitchat-desktop.svg" "$share/icons/hicolor/scalable/apps/bitchat-desktop.svg"
sed "s|^Exec=.*|Exec=$bin/bitchat-desktop|" "$here/data/bitchat-desktop.desktop" > "$share/applications/bitchat-desktop.desktop"
chmod 644 "$share/applications/bitchat-desktop.desktop"
gtk-update-icon-cache -q -t "$share/icons/hicolor" 2>/dev/null || true
update-desktop-database -q "$share/applications" 2>/dev/null || true
echo "Installed. Launch 'BitChat' from the app grid or run bitchat-desktop."
