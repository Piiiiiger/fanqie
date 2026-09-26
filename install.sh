#!/usr/bin/env bash
# Installs Fanqie for the current user (no root needed).
set -euo pipefail
cd "$(dirname "$0")"
install -Dm755 fanqie.py "$HOME/.local/bin/fanqie"
install -Dm755 tray.py "$HOME/.local/bin/fanqie-tray"
install -Dm644 dev.pigger.Fanqie.desktop "$HOME/.local/share/applications/dev.pigger.Fanqie.desktop"
if [[ -d "$HOME/.config/DankMaterialShell/plugins" ]]; then
    for name in plugin.json FanqieWidget.qml; do
        install -Dm644 "dms-plugin/fanqie/$name" "$HOME/.config/DankMaterialShell/plugins/fanqie/$name"
    done
    install -Dm755 dms-plugin/fanqie/status.py "$HOME/.config/DankMaterialShell/plugins/fanqie/status.py"
fi
echo "Installed to ~/.local/bin/fanqie"
echo "Now add the snippet from niri.kdl to ~/.config/niri/config.kdl"
