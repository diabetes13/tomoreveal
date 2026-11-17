#!/usr/bin/env bash
# TomoReveal installer for Linux and macOS, for computers without Conda.
# Run from the downloaded TomoReveal folder:   bash installers/install_tomoreveal.sh
# Everything goes into one folder (default ~/.local/share/tomoreveal). To uninstall, delete that folder,
# ~/.local/bin/tomoreveal and the menu entry. Administrator rights are not needed.
set -euo pipefail
APP="${TOMOREVEAL_HOME:-$HOME/.local/share/tomoreveal}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[ -f "$ROOT/environment.yml" ] && [ -d "$ROOT/tomoreveal" ] || { echo "Run this from the complete TomoReveal folder (environment.yml and tomoreveal/ are missing)."; exit 1; }
case "$(uname -s)-$(uname -m)" in
  Linux-x86_64)  PLAT=linux-64 ;;
  Linux-aarch64) PLAT=linux-aarch64 ;;
  Darwin-arm64)  PLAT=osx-arm64 ;;
  Darwin-x86_64) PLAT=osx-64 ;;
  *) echo "Unsupported system: $(uname -s) $(uname -m)"; exit 1 ;;
esac
mkdir -p "$APP" "$HOME/.local/bin"
MM="$APP/micromamba"
if [ ! -x "$MM" ]; then
  echo "[1/3] Downloading the package manager (micromamba, about 15 MB) ..."
  curl -fsSL "https://github.com/mamba-org/micromamba-releases/releases/latest/download/micromamba-$PLAT" -o "$MM.part"
  mv "$MM.part" "$MM"; chmod +x "$MM"
fi
if [ ! -x "$APP/env/bin/python" ]; then
  echo "[2/3] Installing Python, Qt and the 3D/video libraries into $APP/env (about 4 GB, several minutes) ..."
  "$MM" create -y -q -r "$APP/mamba-root" -p "$APP/env" -f "$ROOT/environment.yml"
  if [ "$(uname -s)" = "Linux" ]; then   # software renderer, used when the graphics driver cannot be used (Linux only)
    "$MM" install -y -q -r "$APP/mamba-root" -p "$APP/env" -c conda-forge mesalib
  fi
else
  echo "[2/3] Libraries already installed (delete $APP/env to install them again)."
fi
echo "[3/3] Installing the program and its launcher ..."
"$APP/env/bin/python" -m pip install -q --no-deps --upgrade --force-reinstall "$ROOT"
cat > "$HOME/.local/bin/tomoreveal" <<LAUNCH
#!/usr/bin/env bash
exec "$APP/env/bin/python" -m tomoreveal "\$@"
LAUNCH
chmod +x "$HOME/.local/bin/tomoreveal"
"$APP/env/bin/python" -m tomoreveal icon "$APP/icon.png" >/dev/null
if [ "$(uname -s)" = "Linux" ]; then
  mkdir -p "$HOME/.local/share/applications"
  cat > "$HOME/.local/share/applications/tomoreveal.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=TomoReveal
Comment=Tomogram slice-sweep and segmentation-reveal movies
Exec=$HOME/.local/bin/tomoreveal %F
Icon=$APP/icon.png
Terminal=false
Categories=Science;Graphics;
DESK
  command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$HOME/.local/share/applications" >/dev/null 2>&1 || true
  echo "Done. Open TomoReveal from your applications menu, or run:  $HOME/.local/bin/tomoreveal"
else
  if [ -d "$HOME/Desktop" ]; then
    printf '#!/usr/bin/env bash\nexec "%s" -m tomoreveal\n' "$APP/env/bin/python" > "$HOME/Desktop/TomoReveal.command"
    chmod +x "$HOME/Desktop/TomoReveal.command"
    echo "Done. Double-click TomoReveal.command on your Desktop, or run:  $HOME/.local/bin/tomoreveal"
  else
    echo "Done. Start it with:  $HOME/.local/bin/tomoreveal"
  fi
fi
