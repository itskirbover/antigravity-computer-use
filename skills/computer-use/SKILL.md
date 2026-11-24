---
name: computer-use
description: >-
  Universal Linux desktop automation and computer-use skill for Antigravity across Wayland
  (KDE Plasma, GNOME, Hyprland, Sway/wlroots) and X11. Use this skill when the user asks to
  see or control the Linux desktop, interact with native GUI applications, take desktop
  screenshots, click/move/drag/scroll the mouse, type or press keyboard shortcuts, locate
  on-screen buttons via OCR or visual coordinate grids, manage windows, or read/write the
  system clipboard.
---

# Linux Computer Use (`computer-use`)

This skill equips Antigravity with full graphical desktop control on Linux across both **Wayland** (KDE Plasma/KWin, GNOME/Mutter, Hyprland, Sway/wlroots) and **X11**.

> [!IMPORTANT]
> **Non-Focus-Stealing Policy (Default Behavior):**
> - **All interaction actions (`click`, `drag`, `scroll`, `launch`, `open`, and `--window`-targeted `key`/`type`) preserve the user's currently focused window and cursor position by default.**
> - **Focusing an application is a separate, explicit action (`cu.py focus <query>`).** Do NOT call `cu.py focus` or pass `--steal-focus` unless the user explicitly asks you to switch to or bring an application into focus.
> - When an app exposes accessibility widgets (GTK/Qt/accessible apps), prefer `cu.py a11y-click` / `cu.py a11y-type` for 100% zero-focus, zero-cursor-movement interaction.
> - For standard web browsing or web application tasks, prefer the built-in `browser` subagent. Use **`computer-use`** for native Linux desktop applications and GUI workflows.

---

## Bundled Scripts

All operations are driven by two self-contained Python CLI tools in this skill folder:

1. **[cu.py](./scripts/cu.py)** (`~/.gemini/config/skills/computer-use/scripts/cu.py`):
   - **Zero-dependency kernel `/dev/uinput` virtual tablet + keyboard controller** (works across all Wayland compositors and X11 without requiring `xdotool` or `ydotool` daemons) with automatic fallbacks to `xdotool`, `ydotool`, `wtype`, `spectacle`, `grim`, `maim`, `scrot`, `wl-clipboard`, `xclip`, `kscreen-doctor`, `hyprctl`, `swaymsg`, and `qdbus6`.
2. **[vision_helper.py](./scripts/vision_helper.py)** (`~/.gemini/config/skills/computer-use/scripts/vision_helper.py`):
   - Visual grounding & OCR engine using **Pillow (PIL)** and **Tesseract OCR** (`tesseract`) to overlay labeled `(x, y)` coordinate grids, draw target verification crosshairs, crop zoomed regions, and locate exact click coordinates for on-screen text.

For compositor-specific architecture, permissions, and troubleshooting, read [references/linux_backends.md](./references/linux_backends.md).

---

## Sandbox & Execution Rules

1. **Try Standard Sandbox first (`BypassSandbox: false`)**:
   - `/dev/uinput` mouse and keyboard injection (`click`, `move`, `drag`, `scroll`, `key`, `type`) and `vision_helper.py` image/OCR processing work **directly inside the standard sandbox** whenever `/dev/uinput` has user write ACLs.
2. **When `BypassSandbox: true` is needed**:
   - Taking desktop screenshots (`spectacle`, `grim`, `gnome-screenshot`), querying Wayland/DBus window lists (`qdbus6`, `hyprctl`, `swaymsg`), or accessing the Wayland/X11 clipboard (`wl-copy`, `wl-paste`) requires access to `/run/user/$UID` sockets (`WAYLAND_DISPLAY` and `DBUS_SESSION_BUS_ADDRESS`).
   - If `cu.py screenshot`, `cu.py windows`, or `cu.py clipboard-get` reports `sandbox_blocks_display_sockets: true` or fails to connect to D-Bus/Wayland inside the sandbox, re-run **only that command** with `BypassSandbox: true`.

---

## Core Workflow: The Observe → Ground → Act → Verify Loop

Always follow this 4-step closed loop when performing GUI automation:

### Step 1: Observe (Capture & Inspect the Screen)
1. Run `doctor` once at the start of a session to check display resolution, scaling, and available backends:
   ```bash
   python3 ~/.gemini/config/skills/computer-use/scripts/cu.py doctor
   ```
2. Capture a full-screen screenshot to `/tmp/agy_screen.png`:
   ```bash
   python3 ~/.gemini/config/skills/computer-use/scripts/cu.py screenshot -o /tmp/agy_screen.png
   ```
   *(Note the returned `image_width`, `image_height`, `logical_width`, `logical_height`, and `scale`.)*

### Step 2: Ground (Find Exact Target Coordinates)
Use **either or both** of these grounding strategies before clicking:

- **Strategy A — Fast OCR Text Lookup (Best for buttons, menus, tabs, labels)**:
  Find exact bounding-box center `(center_x, center_y)` for any visible text on screen:
  ```bash
  python3 ~/.gemini/config/skills/computer-use/scripts/vision_helper.py find-text /tmp/agy_screen.png "Submit"
  ```
  Or dump all visible screen text and coordinates:
  ```bash
  python3 ~/.gemini/config/skills/computer-use/scripts/vision_helper.py ocr /tmp/agy_screen.png
  ```

- **Strategy B — Visual Coordinate Grid Overlay (Best for icons, canvas controls, non-text UI)**:
  Overlay a labeled `(x, y)` grid onto `/tmp/agy_screen.png` and inspect it visually with `read_file`:
  ```bash
  python3 ~/.gemini/config/skills/computer-use/scripts/vision_helper.py grid /tmp/agy_screen.png -o /tmp/agy_grid.png --step 200 --sub-step 100
  ```
  Then call `read_file` on `/tmp/agy_grid.png` to visually inspect the screenshot and read the `(x, y)` coordinates directly off the grid.
  - For tiny icons or dense toolbars, crop a 400x400 region with a 50px sub-grid first:
    ```bash
    python3 ~/.gemini/config/skills/computer-use/scripts/vision_helper.py crop /tmp/agy_screen.png 1200 40 400 400 -o /tmp/agy_crop.png --step 50
    ```
  - Optional: Preview where your click will land by marking `(x, y)` before clicking:
    ```bash
    python3 ~/.gemini/config/skills/computer-use/scripts/vision_helper.py mark /tmp/agy_screen.png 640 420 -o /tmp/agy_mark.png
    ```

### Step 3: Act (Mouse, Keyboard, Clipboard, App Launch)
> [!TIP]
> **HiDPI / Fractional Scaling Auto-Normalization**:
> Always pass `--image-width <W> --image-height <H>` (using the `image_width` and `image_height` returned by `cu.py screenshot`) when clicking or moving to coordinates measured from a screenshot or OCR! `cu.py` automatically normalizes screenshot pixel coordinates into the kernel's `0..32767` uinput space, preventing HiDPI scaling mismatches.

- **Left / Right / Double Click** *(preserves user's active window and cursor position by default; pass `--steal-focus` or `--keep-cursor` only if explicitly requested)*:
  ```bash
  python3 ~/.gemini/config/skills/computer-use/scripts/cu.py click 640 420 --image-width 1920 --image-height 1080
  python3 ~/.gemini/config/skills/computer-use/scripts/cu.py click 640 420 -b right --image-width 1920 --image-height 1080
  python3 ~/.gemini/config/skills/computer-use/scripts/cu.py click 640 420 -c 2 --image-width 1920 --image-height 1080
  ```
- **Move Pointer (Absolute or Relative)**:
  ```bash
  python3 ~/.gemini/config/skills/computer-use/scripts/cu.py move 800 500 --image-width 1920 --image-height 1080
  python3 ~/.gemini/config/skills/computer-use/scripts/cu.py move 50 -30 --relative
  ```
- **Drag and Drop** *(preserves focus & cursor by default)*:
  ```bash
  python3 ~/.gemini/config/skills/computer-use/scripts/cu.py drag 300 400 900 400 --duration 0.4 --image-width 1920 --image-height 1080
  ```
- **Scroll Wheel** (`dy > 0` scrolls UP, `dy < 0` scrolls DOWN; preserves focus & cursor by default):
  ```bash
  python3 ~/.gemini/config/skills/computer-use/scripts/cu.py scroll -5 -x 640 -y 500 --image-width 1920 --image-height 1080
  ```
- **Type Text & Keyboard Shortcuts** *(use `--window "<title/class>"` to target a background app and automatically restore user focus immediately afterward)*:
  ```bash
  python3 ~/.gemini/config/skills/computer-use/scripts/cu.py type "Hello world!" --window "Discord" --enter
  python3 ~/.gemini/config/skills/computer-use/scripts/cu.py type "Fast or Unicode text: 🚀" --paste --enter
  python3 ~/.gemini/config/skills/computer-use/scripts/cu.py key "ctrl+l" --window "LibreWolf"
  ```
- **Zero-Focus Background Interaction via AT-SPI2** *(zero focus or cursor change for accessible GTK/Qt apps)*:
  ```bash
  python3 ~/.gemini/config/skills/computer-use/scripts/cu.py a11y-list --app "easyeffects"
  python3 ~/.gemini/config/skills/computer-use/scripts/cu.py a11y-click "easyeffects" "Bypass"
  python3 ~/.gemini/config/skills/computer-use/scripts/cu.py a11y-type "gedit" "Search" "query"
  ```
- **Explicit App Focusing (Separate Action — Only When Requested)**:
  ```bash
  python3 ~/.gemini/config/skills/computer-use/scripts/cu.py focus "Dolphin"
  python3 ~/.gemini/config/skills/computer-use/scripts/cu.py focus --class "discord"
  python3 ~/.gemini/config/skills/computer-use/scripts/cu.py focus --id "{eb5999c2-b773-4695-99d0-6330ee463e22}"
  ```
- **Launch Applications or Open Files/URLs** *(guards against focus-stealing for 2.5s by default so the new app does not interrupt the user's active window; pass `--steal-focus` only if asked to switch to it)*:
  ```bash
  python3 ~/.gemini/config/skills/computer-use/scripts/cu.py launch "konsole"
  python3 ~/.gemini/config/skills/computer-use/scripts/cu.py open "/home/kirbo/Desktop"
  ```
- **Inspect Windows, Active Focus, Cursor, & Clipboard**:
  ```bash
  python3 ~/.gemini/config/skills/computer-use/scripts/cu.py windows
  python3 ~/.gemini/config/skills/computer-use/scripts/cu.py active-window
  python3 ~/.gemini/config/skills/computer-use/scripts/cu.py cursor-pos
  python3 ~/.gemini/config/skills/computer-use/scripts/cu.py clipboard-get
  python3 ~/.gemini/config/skills/computer-use/scripts/cu.py clipboard-set "copied text"
  ```

### Step 4: Verify (Confirm UI State Transition)
After performing an action (click, key press, app launch), wait briefly if an animation or window launch is occurring (`sleep 0.5`), take a fresh screenshot (`cu.py screenshot -o /tmp/agy_screen.png`), and inspect it via `read_file` or `vision_helper.py ocr` to verify that the intended UI state change succeeded before moving on.

---

## Safety & Guardrails
- **Never click blindly**: Always verify coordinates using OCR (`find-text` / `ocr`) or a coordinate grid (`grid` + `read_file`) before clicking destructive controls.
- **Confirm destructive OS actions**: Pause and ask the user before confirming irreversible actions in GUI dialogs (e.g., deleting user files, formatting disks, sending emails/messages, or submitting financial transactions).
- **Never bypass CAPTCHAs or authentication gates**: If a desktop application or browser window displays a CAPTCHA or sensitive password prompt (e.g., Polkit/sudo authentication dialog), stop and ask the user to complete it directly.
