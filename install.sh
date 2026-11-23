#!/usr/bin/env bash
# =============================================================================
# Antigravity Linux Computer-Use Skill — Command-Line Installer
# =============================================================================
# Installs and configures the `computer-use` skill (or plugin) for Antigravity
# on Linux (Wayland: KDE Plasma, GNOME, Hyprland, Sway/wlroots; and X11).
#
# Usage:
#   ./install.sh [OPTIONS]
#
# Options:
#   -g, --global            Install globally to ~/.gemini/config/skills/computer-use (default)
#   -w, --workspace <DIR>   Install into a workspace at <DIR>/.agents/skills/computer-use
#   -p, --plugin            Install as an Antigravity plugin in ~/.gemini/config/plugins/computer-use
#   -u, --setup-uinput      Configure /dev/uinput write permissions (ACL + persistent udev rule)
#   -d, --install-deps      Install recommended system packages (Pillow, Tesseract, screenshot/clipboard tools)
#   --doctor                Run `cu.py doctor` diagnostics after installation
#   --uninstall             Remove the skill/plugin from the selected target location
#   -f, --force             Overwrite existing installation without prompting
#   -h, --help              Show this help message
# =============================================================================

set -euo pipefail

SKILL_NAME="computer-use"
REPO_TARBALL_URL="https://codeload.github.com/itskirbover/antigravity-computer-use/tar.gz/refs/heads/main"
SELF_PATH="${BASH_SOURCE[0]:-}"
if [[ -n "$SELF_PATH" && -f "$SELF_PATH" ]]; then
  SCRIPT_DIR="$(cd "$(dirname "$SELF_PATH")" && pwd)"
else
  SCRIPT_DIR="$(pwd)"
fi
SOURCE_SKILL_DIR="${SCRIPT_DIR}/skills/${SKILL_NAME}"

# Colors
if [[ -t 1 ]]; then
  C_RESET="\033[0m"
  C_BOLD="\033[1m"
  C_GREEN="\033[1;32m"
  C_CYAN="\033[1;36m"
  C_YELLOW="\033[1;33m"
  C_RED="\033[1;31m"
else
  C_RESET="" C_BOLD="" C_GREEN="" C_CYAN="" C_YELLOW="" C_RED=""
fi

info()  { echo -e "${C_CYAN}[INFO]${C_RESET} $*"; }
ok()    { echo -e "${C_GREEN}[OK]${C_RESET}   $*"; }
warn()  { echo -e "${C_YELLOW}[WARN]${C_RESET} $*"; }
err()   { echo -e "${C_RED}[ERR]${C_RESET}  $*" >&2; }

usage() {
  echo -e "$(cat <<EOF
${C_BOLD}Antigravity Linux Computer-Use Skill Installer${C_RESET}

${C_BOLD}USAGE:${C_RESET}
  $(basename "$0") [OPTIONS]

${C_BOLD}INSTALLATION TARGETS:${C_RESET}
  -g, --global              Install globally to ~/.gemini/config/skills/computer-use (default)
  -w, --workspace <PATH>    Install to a specific project workspace (<PATH>/.agents/skills/computer-use)
  -p, --plugin              Install as a plugin bundle to ~/.gemini/config/plugins/computer-use

${C_BOLD}SYSTEM SETUP & DIAGNOSTICS:${C_RESET}
  -u, --setup-uinput        Grant current user write access to /dev/uinput (ACL + persistent udev rule)
  -d, --install-deps        Auto-detect Linux distro and install recommended dependencies
      --doctor              Run environment diagnostics (cu.py doctor)
      --uninstall           Uninstall from the specified target (--global, --workspace, or --plugin)
  -f, --force               Overwrite existing files without confirmation
  -h, --help                Display this help message

${C_BOLD}EXAMPLES:${C_RESET}
  # Standard global install + diagnostics
  ./install.sh --global --doctor

  # Full setup including /dev/uinput permissions and system dependencies
  ./install.sh --global --setup-uinput --install-deps --doctor

  # Install into a specific project workspace
  ./install.sh --workspace /path/to/my-project

  # Uninstall global skill
  ./install.sh --global --uninstall
EOF
)"
}

# Defaults
MODE="global"
WORKSPACE_DIR=""
SETUP_UINPUT=0
INSTALL_DEPS=0
RUN_DOCTOR=0
UNINSTALL=0
FORCE=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    -g|--global)
      MODE="global"
      shift
      ;;
    -w|--workspace)
      MODE="workspace"
      if [[ $# -lt 2 || "$2" == -* ]]; then
        err "--workspace requires a directory path argument."
        exit 1
      fi
      WORKSPACE_DIR="$(cd "$2" 2>/dev/null && pwd || echo "$2")"
      shift 2
      ;;
    -p|--plugin)
      MODE="plugin"
      shift
      ;;
    -u|--setup-uinput)
      SETUP_UINPUT=1
      shift
      ;;
    -d|--install-deps)
      INSTALL_DEPS=1
      shift
      ;;
    --doctor)
      RUN_DOCTOR=1
      shift
      ;;
    --uninstall)
      UNINSTALL=1
      shift
      ;;
    -f|--force)
      FORCE=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      err "Unknown option: $1"
      usage
      exit 1
      ;;
  esac
done

# Determine target path
case "$MODE" in
  global)
    TARGET_BASE="${HOME}/.gemini/config/skills/${SKILL_NAME}"
    SKILL_DEST="${TARGET_BASE}"
    ;;
  workspace)
    TARGET_BASE="${WORKSPACE_DIR}/.agents/skills/${SKILL_NAME}"
    SKILL_DEST="${TARGET_BASE}"
    ;;
  plugin)
    TARGET_BASE="${HOME}/.gemini/config/plugins/${SKILL_NAME}"
    SKILL_DEST="${TARGET_BASE}/skills/${SKILL_NAME}"
    ;;
esac

# -----------------------------------------------------------------------------
# Uninstall Handler
# -----------------------------------------------------------------------------
if [[ "$UNINSTALL" -eq 1 ]]; then
  if [[ -d "$TARGET_BASE" ]]; then
    info "Removing ${MODE} installation at: ${TARGET_BASE}"
    rm -rf "$TARGET_BASE"
    ok "Uninstalled ${SKILL_NAME} from ${TARGET_BASE}"
  else
    warn "Nothing to uninstall at ${TARGET_BASE} (directory does not exist)."
  fi
  exit 0
fi

# -----------------------------------------------------------------------------
# Optional: Install System Dependencies
# -----------------------------------------------------------------------------
install_system_deps() {
  info "Detecting package manager and desktop session for dependencies..."
  local session_type="${XDG_SESSION_TYPE:-wayland}"
  local desktop="${XDG_CURRENT_DESKTOP:-}"

  if command -v pacman >/dev/null 2>&1; then
    local pkgs=(python python-pillow tesseract tesseract-data-eng wl-clipboard xdg-utils libnotify)
    if [[ "$desktop" == *KDE* || "$desktop" == *plasma* ]]; then
      pkgs+=(spectacle)
    elif [[ "$session_type" == "wayland" ]]; then
      pkgs+=(grim slurp)
    else
      pkgs+=(maim xdotool xclip)
    fi
    info "Arch Linux detected. Installing: ${pkgs[*]}"
    sudo pacman -S --needed "${pkgs[@]}"
  elif command -v apt-get >/dev/null 2>&1; then
    local pkgs=(python3 python3-pil tesseract-ocr tesseract-ocr-eng wl-clipboard xdg-utils libnotify-bin grim slurp maim xdotool xclip)
    info "Debian/Ubuntu detected. Installing: ${pkgs[*]}"
    sudo apt-get update && sudo apt-get install -y "${pkgs[@]}"
  elif command -v dnf >/dev/null 2>&1; then
    local pkgs=(python3 python3-pillow tesseract tesseract-langpack-eng wl-clipboard xdg-utils libnotify grim slurp maim xdotool xclip)
    info "Fedora/RHEL detected. Installing: ${pkgs[*]}"
    sudo dnf install -y "${pkgs[@]}"
  elif command -v zypper >/dev/null 2>&1; then
    local pkgs=(python3 python3-Pillow tesseract-ocr tesseract-ocr-traineddata-english wl-clipboard xdg-utils libnotify-tools grim slurp maim xdotool xclip)
    info "openSUSE detected. Installing: ${pkgs[*]}"
    sudo zypper install -y "${pkgs[@]}"
  else
    warn "Could not auto-detect package manager (pacman/apt/dnf/zypper). Please ensure python3, Pillow, tesseract, and a screenshot tool (spectacle/grim/maim) are installed."
  fi
}

# -----------------------------------------------------------------------------
# Optional: Configure /dev/uinput Permissions
# -----------------------------------------------------------------------------
configure_uinput() {
  info "Checking /dev/uinput access for user '${USER}'..."
  if [[ ! -e /dev/uinput ]]; then
    info "Loading uinput kernel module..."
    sudo modprobe uinput || warn "Could not modprobe uinput."
  fi

  if python3 -c "import os; fd = os.open('/dev/uinput', os.O_WRONLY | os.O_NONBLOCK); os.close(fd)" 2>/dev/null; then
    ok "/dev/uinput is already writable by '${USER}'."
  else
    info "Granting immediate ACL write access on /dev/uinput to '${USER}'..."
    sudo setfacl -m "u:${USER}:rw" /dev/uinput
    ok "Granted immediate ACL access: u:${USER}:rw on /dev/uinput"
  fi

  local udev_rule="/etc/udev/rules.d/99-antigravity-uinput.rules"
  if [[ ! -f "$udev_rule" ]]; then
    info "Installing persistent udev rule at ${udev_rule}..."
    echo "KERNEL==\"uinput\", SUBSYSTEM==\"misc\", OPTIONS+=\"static_node=uinput\", TAG+=\"uaccess\", RUN+=\"/usr/bin/setfacl -m u:${USER}:rw /dev/uinput\"" \
      | sudo tee "$udev_rule" >/dev/null
    sudo udevadm control --reload-rules && sudo udevadm trigger /dev/uinput || true
    ok "Installed persistent udev rule (${udev_rule})."
  else
    ok "Persistent udev rule already present at ${udev_rule}."
  fi
}

# -----------------------------------------------------------------------------
# Extract / Copy Skill Payload
# -----------------------------------------------------------------------------
install_payload() {
  mkdir -p "${SKILL_DEST}/scripts" "${SKILL_DEST}/references"

  if [[ -d "$SOURCE_SKILL_DIR" && -f "${SOURCE_SKILL_DIR}/SKILL.md" ]]; then
    info "Installing skill files from ${SOURCE_SKILL_DIR} -> ${SKILL_DEST}"
    cp -rf "${SOURCE_SKILL_DIR}/"* "${SKILL_DEST}/"
  elif [[ -n "$SELF_PATH" && -f "$SELF_PATH" ]] && grep -q "^__ARCHIVE_BELOW__$" "$SELF_PATH"; then
    info "Extracting embedded skill payload -> ${SKILL_DEST}"
    awk '/^__ARCHIVE_BELOW__$/{found=1; next} found' "$SELF_PATH" \
      | base64 -d | tar -xzf - -C "${SKILL_DEST}"
  elif command -v curl >/dev/null 2>&1; then
    info "Downloading skill payload from GitHub (${REPO_TARBALL_URL}) -> ${SKILL_DEST}"
    curl -fsSL "$REPO_TARBALL_URL" | tar -xzf - --strip-components=3 -C "${SKILL_DEST}" "antigravity-computer-use-main/skills/computer-use"
  else
    err "Could not find skill source directory at ${SOURCE_SKILL_DIR} and no embedded archive found."
    exit 1
  fi

  chmod +x "${SKILL_DEST}/scripts/cu.py" "${SKILL_DEST}/scripts/vision_helper.py" 2>/dev/null || true

  if [[ "$MODE" == "plugin" ]]; then
    cat > "${TARGET_BASE}/plugin.json" <<EOF
{
  "name": "computer-use",
  "version": "1.0.0",
  "description": "Universal Linux desktop automation and computer-use skill for Antigravity across Wayland (KDE Plasma, GNOME, Hyprland, Sway/wlroots) and X11."
}
EOF
)"
    ok "Created plugin manifest at ${TARGET_BASE}/plugin.json"
  fi

  ok "Installed '${SKILL_NAME}' (${MODE}) to: ${TARGET_BASE}"
}

# -----------------------------------------------------------------------------
# Main Execution Flow
# -----------------------------------------------------------------------------
if [[ "$INSTALL_DEPS" -eq 1 ]]; then
  install_system_deps
fi

if [[ "$SETUP_UINPUT" -eq 1 ]]; then
  configure_uinput
fi

install_payload

# Create convenient CLI symlink in ~/.local/bin if writable
if [[ -d "${HOME}/.local/bin" && -w "${HOME}/.local/bin" ]]; then
  ln -sf "${SKILL_DEST}/scripts/cu.py" "${HOME}/.local/bin/agy-cu"
  ln -sf "${SKILL_DEST}/scripts/vision_helper.py" "${HOME}/.local/bin/agy-vision"
  ok "Created CLI shortcuts: 'agy-cu' and 'agy-vision' in ~/.local/bin/"
fi

if [[ "$RUN_DOCTOR" -eq 1 ]]; then
  info "Running environment diagnostics (cu.py doctor)..."
  python3 "${SKILL_DEST}/scripts/cu.py" doctor
fi

echo ""
ok "Installation complete! Trigger the skill in Antigravity with: /computer-use"
exit 0
__ARCHIVE_BELOW__
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
9s//BbssNHUAaAEA
