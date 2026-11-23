# Linux Computer Use Backends & Architecture Reference

This document details how [`cu.py`](../scripts/cu.py) and [`vision_helper.py`](../scripts/vision_helper.py) interface with Linux display servers, compositors, and input subsystems.

---

## 1. Input Injection Architecture (`/dev/uinput` vs. Compositor Tools)

### Primary Backend: Linux Kernel `/dev/uinput` (`UInputController`)
Wayland compositors isolate client windows from one another and block synthetic X11 events (`XTest`) from reaching native Wayland windows. To provide **universal, zero-dependency input automation** across all Wayland compositors (KDE KWin, GNOME Mutter, Hyprland, Sway, wlroots) and X11:
- `cu.py` opens `/dev/uinput` directly and registers an ephemeral virtual input device (`Antigravity Virtual Input`) with:
  - `EV_ABS` (`ABS_X`, `ABS_Y` in range `0..32767`) for exact absolute screen coordinate positioning.
  - `EV_REL` (`REL_X`, `REL_Y`, `REL_WHEEL`, `REL_HWHEEL`) for relative pointer movement and mouse wheel scrolling.
  - `EV_KEY` (`BTN_LEFT`, `BTN_RIGHT`, `BTN_MIDDLE`, plus standard keyboard scan codes) for clicking, dragging, typing, and multi-key shortcuts.
- When `UInputController` opens `/dev/uinput`, it waits ~140ms for `udev` and `libinput` in the compositor to hotplug the device before emitting events, and waits ~40ms before destroying the device on exit.

#### Verifying `/dev/uinput` Permissions
Check permissions with:
```bash
getfacl /dev/uinput
```
If `/dev/uinput` is not writable by the current user, grant access persistently via a udev rule or ACL:
```bash
sudo setfacl -m u:$USER:rw /dev/uinput
```

### Fallback Input Backends
When `/dev/uinput` is unavailable, `cu.py` automatically falls back to:
1. **`xdotool`** (on X11 sessions)
2. **`ydotool`** (via `ydotoold` socket)
3. **`wtype`** (for Wayland text entry on wlroots compositors supporting `zwp_virtual_keyboard_v1`)

---

## 2. Screenshot Backends by Desktop Environment

Wayland restricts screen capture to privileged protocols or compositor-specific tools. `cu.py screenshot` selects the highest-fidelity backend automatically:

| Environment / Compositor | Primary Tool | Command Used by `cu.py` | Notes |
| :--- | :--- | :--- | :--- |
| **KDE Plasma (Wayland)** | `spectacle` | `spectacle -b -n -f -o <out.png>` | Uses `org.kde.KWin.ScreenShot2` D-Bus interface seamlessly without popup prompts. (`grim` does not work on KDE KWin because KWin does not implement `wlr-screencopy-unstable-v1`.) |
| **Hyprland / Sway / wlroots** | `grim` | `grim [-g "X,Y WxH"] <out.png>` | Uses `wlr-screencopy-unstable-v1`. Supports sub-region capture with `-g`. |
| **GNOME (Wayland)** | `gnome-screenshot` | `gnome-screenshot [-w] -f <out.png>` | Interfaces with GNOME Shell's `org.gnome.Shell.Screenshot` D-Bus API. |
| **X11 (Any WM)** | `maim` / `scrot` / `import` | `maim <out.png>` or `scrot --overwrite <out.png>` | Captures X11 root window directly via XShm / XGetImage. |

---

## 3. HiDPI & Fractional Scaling Coordinate Mapping

On Wayland with fractional scaling (e.g., 125%, 150%, 200% on a 4K display):
- **Screenshot pixel dimensions (`image_width x image_height`)** reflect the physical framebuffer resolution (e.g., `3840x2160`).
- **Logical desktop dimensions (`logical_width x logical_height`)** reflect scaled logical units (e.g., `2560x1440` at `1.5` scale).
- **Kernel `/dev/uinput` absolute coordinates (`0..32767`)** map proportionally across the active desktop bounding box (`0..100%` of width and height).

Because `UInputController.move_to(x, y, image_width=W, image_height=H)` normalizes `(x / (W - 1)) * 32767`, passing `--image-width` and `--image-height` from the screenshot metadata guarantees that **any `(x, y)` coordinate read from a screenshot or OCR bounding box lands on the exact same pixel on screen**, regardless of whether display scaling is 100%, 125%, 150%, or 200%!

> [!WARNING]
> **Multi-Monitor Layouts**:
> On multi-monitor desktops, `EV_ABS` (`0..32767`) spans the combined bounding box of all active monitors. Always use full-desktop screenshots (`cu.py screenshot` without `-w`) when measuring coordinates for `click`, `move`, or `drag`.
