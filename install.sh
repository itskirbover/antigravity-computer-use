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
4AEA
