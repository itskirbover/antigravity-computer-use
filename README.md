# Antigravity Computer Use for Linux (`antigravity-computer-use`)

Universal Linux desktop automation and computer-use skill for **Google Antigravity** across **Wayland** (KDE Plasma/KWin, GNOME/Mutter, Hyprland, Sway/wlroots) and **X11**.

---

## Highlights

- **Zero-Dependency Kernel `/dev/uinput` Virtual Pointer & Keyboard (`cu.py`)**
  Works natively across all Wayland compositors and X11 without requiring `xdotool` or a running `ydotoold` daemon. Automatically falls back to `xdotool`, `ydotool`, or `wtype` when `/dev/uinput` is unavailable.
- **Auto-Detecting Screenshot Engine**
  Selects the best native screen capture backend for your session: `spectacle` (KDE Plasma Wayland), `grim` (Hyprland / Sway / wlroots), `gnome-screenshot` (GNOME), or `maim` / `scrot` / `import` (X11).
- **HiDPI & Fractional Scaling Auto-Normalization**
  Automatically maps screenshot pixel coordinates `(x, y)` into the Linux kernel's `0..32767` absolute uinput coordinate space so clicks land accurately at any display scaling (100%, 125%, 150%, 200%).
- **Visual Grounding & Multi-Pass OCR (`vision_helper.py`)**
  - **Multi-Pass Tesseract OCR (`find-text`, `ocr`)**: Scans standard contrast, bright-text-on-colored-buttons thresholding, and dark-mode contrast inversion to return exact click centers `(center_x, center_y)`.
  - **Coordinate Grid Overlays (`grid`, `crop`, `mark`)**: Overlays labeled `(x, y)` grid lines, zoomed sub-grids, and bullseye verification crosshairs on screenshots for visual inspection.
- **Self-Contained CLI Installer (`install.sh`)**
  Supports global installation, per-workspace installation, plugin packaging, `/dev/uinput` ACL + persistent `udev` rule configuration, and automatic distro dependency installation (`pacman`, `apt`, `dnf`, `zypper`).

---

## Quick Start

### One-Line Install (Global Skill)

```bash
curl -fsSL https://raw.githubusercontent.com/itskirbover/antigravity-computer-use/main/install.sh | bash -s -- --global --doctor
```

### Clone & Install

```bash
git clone https://github.com/itskirbover/antigravity-computer-use.git
cd antigravity-computer-use

# Standard global install (~/.gemini/config/skills/computer-use) + run diagnostics
./install.sh --global --doctor

# Full first-time setup (grants /dev/uinput ACL + udev rule, installs distro packages, installs skill)
./install.sh --global --setup-uinput --install-deps --doctor
```

Once installed, invoke the skill in Antigravity chat with:

```text
/computer-use
```

---

## Command-Line Installer Options

```bash
./install.sh [OPTIONS]
```

| Flag | Description |
| :--- | :--- |
| `-g, --global` | Install globally to `~/.gemini/config/skills/computer-use` (default) |
| `-w, --workspace <DIR>` | Install to a specific project workspace (`<DIR>/.agents/skills/computer-use`) |
| `-p, --plugin` | Install as a toggleable plugin bundle to `~/.gemini/config/plugins/computer-use` |
| `-u, --setup-uinput` | Configure `/dev/uinput` write permissions (`setfacl` + persistent `/etc/udev/rules.d/99-antigravity-uinput.rules`) |
| `-d, --install-deps` | Auto-detect Linux distro (`pacman`, `apt`, `dnf`, `zypper`) and install recommended packages |
| `--doctor` | Run `cu.py doctor` environment diagnostics after installation |
| `--uninstall` | Remove the skill/plugin from the selected target location |
| `-h, --help` | Display full CLI help |

---

## CLI Tools (`agy-cu` & `agy-vision`)

When `~/.local/bin` is writable, `install.sh` creates `agy-cu` and `agy-vision` symlinks for direct terminal usage:

### 1. Desktop Control (`agy-cu` / `scripts/cu.py`)

```bash
# Check display server, compositor, /dev/uinput permissions, and screen geometry
agy-cu doctor
agy-cu screen-info

# Capture a full-desktop or active-window screenshot
agy-cu screenshot -o /tmp/screen.png
agy-cu screenshot -w -o /tmp/window.png

# Click (left, right, middle, double-click) with HiDPI coordinate auto-scaling
agy-cu click 640 420 --image-width 1920 --image-height 1080
agy-cu click 640 420 -b right --image-width 1920 --image-height 1080
agy-cu click 640 420 -c 2 --image-width 1920 --image-height 1080

# Move, drag, and scroll
agy-cu move 800 500 --image-width 1920 --image-height 1080
agy-cu drag 300 400 900 400 --duration 0.4 --image-width 1920 --image-height 1080
agy-cu scroll -5 -x 640 -y 500 --image-width 1920 --image-height 1080

# Keyboard shortcuts and text entry
agy-cu key "super"
agy-cu key "ctrl+shift+t"
agy-cu type "Hello world!" --enter
agy-cu type "Unicode & fast paste 🚀" --paste --enter

# Window list, clipboard, and app launching
agy-cu windows
agy-cu clipboard-get
agy-cu clipboard-set "copied text"
agy-cu launch "konsole"
agy-cu open "https://github.com"
```

### 2. Visual Grounding & OCR (`agy-vision` / `scripts/vision_helper.py`)

```bash
# Find exact click coordinates for an on-screen text button or label
agy-vision find-text /tmp/screen.png "Submit"

# Dump all detected screen text and bounding-box centers
agy-vision ocr /tmp/screen.png

# Overlay a labeled (x, y) coordinate grid onto a screenshot
agy-vision grid /tmp/screen.png -o /tmp/grid.png --step 200 --sub-step 100

# Crop a zoomed region with a 50px coordinate sub-grid
agy-vision crop /tmp/screen.png 1200 40 400 400 -o /tmp/crop.png --step 50

# Mark (x, y) with a bullseye crosshair to verify click accuracy
agy-vision mark /tmp/screen.png 640 420 -o /tmp/mark.png
```

---

## Repository Structure

```text
antigravity-computer-use/
├── install.sh                                      # Self-contained CLI installer (with embedded archive)
├── README.md                                       # Documentation & quickstart guide
└── skills/
    └── computer-use/
        ├── SKILL.md                                # Antigravity skill instructions & workflow loop
        ├── references/
        │   └── linux_backends.md                   # Wayland/X11 backends, uinput & HiDPI reference
        └── scripts/
            ├── cu.py                               # Universal Linux desktop control CLI
            └── vision_helper.py                    # Visual grounding (coordinate grid, crop, mark) & OCR
```
