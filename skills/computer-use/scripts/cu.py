#!/usr/bin/env python3
"""
Universal Linux Computer Use CLI (`cu.py`) for Antigravity.

Provides cross-desktop (Wayland: KDE Plasma, GNOME, Hyprland, Sway/wlroots; and X11)
capabilities for:
- Environment detection & diagnostics (`doctor`)
- Screen geometry & cursor information (`screen-info`, `cursor-pos`)
- Screenshot capture (`screenshot`)
- Mouse & keyboard control via kernel `/dev/uinput` (zero dependencies) or
  fallback CLI tools (`xdotool`, `ydotool`, `wtype`, `dotool`, `hyprctl`)
- Clipboard read/write (`clipboard-get`, `clipboard-set`)
- Window listing, focusing, moving, closing (`windows`, `window-focus`, `window-close`)
- Application launching (`launch`, `open`)
"""

import argparse
import ctypes
import fcntl
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Environment & Session Restoration
# ---------------------------------------------------------------------------

def ensure_desktop_env() -> Dict[str, str]:
    """Ensure XDG_RUNTIME_DIR, DBUS_SESSION_BUS_ADDRESS, WAYLAND_DISPLAY, and DISPLAY are populated."""
    uid = os.getuid()
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{uid}"
    if os.path.isdir(runtime_dir):
        os.environ["XDG_RUNTIME_DIR"] = runtime_dir

    if not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        bus_path = f"{runtime_dir}/bus"
        if os.path.exists(bus_path):
            os.environ["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={bus_path}"

    if not os.environ.get("WAYLAND_DISPLAY") and os.path.isdir(runtime_dir):
        for entry in sorted(os.listdir(runtime_dir)):
            if entry.startswith("wayland-") and not entry.endswith(".lock"):
                os.environ["WAYLAND_DISPLAY"] = entry
                break

    if not os.environ.get("DISPLAY") and os.path.isdir("/tmp/.X11-unix"):
        for entry in sorted(os.listdir("/tmp/.X11-unix")):
            if entry.startswith("X"):
                os.environ["DISPLAY"] = f":{entry[1:]}"
                break

    return dict(os.environ)


def detect_environment() -> Dict[str, Any]:
    """Detect display server, compositor, sandbox state, and available backends."""
    ensure_desktop_env()
    uid = os.getuid()
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{uid}")
    runtime_dir_accessible = os.path.isdir(runtime_dir) and os.path.exists(f"{runtime_dir}/bus")

    session_type = os.environ.get("XDG_SESSION_TYPE", "").lower()
    desktop = (os.environ.get("XDG_CURRENT_DESKTOP") or os.environ.get("DESKTOP_SESSION") or "").lower()

    if not session_type:
        if os.environ.get("WAYLAND_DISPLAY"):
            session_type = "wayland"
        elif os.environ.get("DISPLAY"):
            session_type = "x11"
        else:
            session_type = "unknown"

    compositor = "unknown"
    if "kde" in desktop or "plasma" in desktop:
        compositor = "kde-kwin"
    elif "gnome" in desktop:
        compositor = "gnome-mutter"
    elif "hyprland" in desktop or os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        compositor = "hyprland"
    elif "sway" in desktop or os.environ.get("SWAYSOCK"):
        compositor = "sway"
    elif "wlroots" in desktop or "river" in desktop or "wayfire" in desktop:
        compositor = "wlroots"
    elif session_type == "x11":
        compositor = "x11-wm"

    # Check uinput access
    uinput_writable = False
    try:
        fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
        os.close(fd)
        uinput_writable = True
    except OSError:
        uinput_writable = False

    tools = {}
    for binary in [
        "spectacle", "grim", "slurp", "gnome-screenshot", "scrot", "maim", "import", "ffmpeg",
        "xdotool", "ydotool", "wtype", "dotool", "wlrctl", "kdotool", "hyprctl", "swaymsg", "wmctrl",
        "wl-copy", "wl-paste", "xclip", "xsel",
        "kscreen-doctor", "xrandr", "qdbus6", "qdbus", "gdbus", "busctl",
        "tesseract", "xdg-open", "notify-send"
    ]:
        path = shutil.which(binary)
        if path:
            tools[binary] = path

    sandbox_active = not runtime_dir_accessible
    return {
        "session_type": session_type,
        "desktop": desktop,
        "compositor": compositor,
        "wayland_display": os.environ.get("WAYLAND_DISPLAY", ""),
        "x11_display": os.environ.get("DISPLAY", ""),
        "xdg_runtime_dir": runtime_dir,
        "runtime_dir_accessible": runtime_dir_accessible,
        "sandbox_blocks_display_sockets": sandbox_active,
        "uinput_writable": uinput_writable,
        "available_tools": tools,
    }


# ---------------------------------------------------------------------------
# Screen Geometry Discovery
# ---------------------------------------------------------------------------

def get_screen_info() -> Dict[str, Any]:
    """Detect screen logical resolution, physical resolution, and scale factor."""
    env = detect_environment()
    width, height, scale = 1920, 1080, 1.0
    source = "default-fallback"
    monitors: List[Dict[str, Any]] = []

    # 1. Try kscreen-doctor on KDE
    if "kscreen-doctor" in env["available_tools"] and env["runtime_dir_accessible"]:
        try:
            res = subprocess.run(
                ["kscreen-doctor", "-o"],
                capture_output=True, text=True, timeout=3
            )
            # Strip ANSI escape codes
            clean = re.sub(r"\x1b\[[0-9;]*m", "", res.stdout)
            cur_mon: Dict[str, Any] = {}
            for line in clean.splitlines():
                line_s = line.strip()
                m_out = re.match(r"^Output:\s+(\d+)\s+(\S+)", line_s)
                if m_out:
                    if cur_mon:
                        monitors.append(cur_mon)
                    cur_mon = {"id": int(m_out.group(1)), "name": m_out.group(2)}
                elif "Geometry:" in line_s:
                    m_geo = re.search(r"Geometry:\s*(\d+),(\d+)\s+(\d+)x(\d+)", line_s)
                    if m_geo:
                        cur_mon["x"] = int(m_geo.group(1))
                        cur_mon["y"] = int(m_geo.group(2))
                        cur_mon["logical_width"] = int(m_geo.group(3))
                        cur_mon["logical_height"] = int(m_geo.group(4))
                elif "Scale:" in line_s:
                    m_scale = re.search(r"Scale:\s*([0-9.]+)", line_s)
                    if m_scale:
                        cur_mon["scale"] = float(m_scale.group(1))
            if cur_mon:
                monitors.append(cur_mon)
            valid_mons = [m for m in monitors if "logical_width" in m]
            if valid_mons:
                max_x = max(m.get("x", 0) + m["logical_width"] for m in valid_mons)
                max_y = max(m.get("y", 0) + m["logical_height"] for m in valid_mons)
                width, height = max_x, max_y
                scale = valid_mons[0].get("scale", 1.0)
                source = "kscreen-doctor"
        except Exception:
            pass

    # 2. Try hyprctl on Hyprland
    if source == "default-fallback" and env["compositor"] == "hyprland" and "hyprctl" in env["available_tools"]:
        try:
            res = subprocess.run(["hyprctl", "monitors", "-j"], capture_output=True, text=True, timeout=3)
            if res.returncode == 0:
                data = json.loads(res.stdout)
                if data:
                    for m in data:
                        sc = float(m.get("scale", 1.0))
                        lw = int(round(m["width"] / sc))
                        lh = int(round(m["height"] / sc))
                        monitors.append({
                            "name": m.get("name"),
                            "x": m.get("x", 0),
                            "y": m.get("y", 0),
                            "logical_width": lw,
                            "logical_height": lh,
                            "physical_width": m["width"],
                            "physical_height": m["height"],
                            "scale": sc,
                        })
                    width = max(m["x"] + m["logical_width"] for m in monitors)
                    height = max(m["y"] + m["logical_height"] for m in monitors)
                    scale = monitors[0]["scale"]
                    source = "hyprctl"
        except Exception:
            pass

    # 3. Try swaymsg on Sway
    if source == "default-fallback" and env["compositor"] == "sway" and "swaymsg" in env["available_tools"]:
        try:
            res = subprocess.run(["swaymsg", "-t", "get_outputs", "-r"], capture_output=True, text=True, timeout=3)
            if res.returncode == 0:
                data = json.loads(res.stdout)
                for m in data:
                    if m.get("active"):
                        rect = m.get("rect", {})
                        monitors.append({
                            "name": m.get("name"),
                            "x": rect.get("x", 0),
                            "y": rect.get("y", 0),
                            "logical_width": rect.get("width", 1920),
                            "logical_height": rect.get("height", 1080),
                            "scale": float(m.get("scale", 1.0)),
                        })
                if monitors:
                    width = max(m["x"] + m["logical_width"] for m in monitors)
                    height = max(m["y"] + m["logical_height"] for m in monitors)
                    scale = monitors[0]["scale"]
                    source = "swaymsg"
        except Exception:
            pass

    # 4. Try xrandr (works on X11 and XWayland)
    if source == "default-fallback" and "xrandr" in env["available_tools"]:
        try:
            res = subprocess.run(["xrandr", "--current"], capture_output=True, text=True, timeout=3)
            if res.returncode == 0:
                m_cur = re.search(r"current\s+(\d+)\s+x\s+(\d+)", res.stdout)
                if m_cur:
                    width = int(m_cur.group(1))
                    height = int(m_cur.group(2))
                    source = "xrandr"
        except Exception:
            pass

    # 5. Fallback to sysfs DRM modes if running inside sandbox without display socket access
    if source == "default-fallback":
        try:
            drm_root = Path("/sys/class/drm")
            if drm_root.exists():
                for status_file in sorted(drm_root.glob("card*-*/status")):
                    if status_file.read_text().strip() == "connected":
                        modes_file = status_file.parent / "modes"
                        if modes_file.exists():
                            first_mode = modes_file.read_text().splitlines()[0].strip()
                            m_res = re.match(r"^(\d+)x(\d+)", first_mode)
                            if m_res:
                                width = int(m_res.group(1))
                                height = int(m_res.group(2))
                                source = f"sysfs-drm ({status_file.parent.name})"
                                break
        except Exception:
            pass

    return {
        "logical_width": width,
        "logical_height": height,
        "scale": scale,
        "source": source,
        "monitors": monitors,
    }


# ---------------------------------------------------------------------------
# Linux Kernel `/dev/uinput` Virtual Tablet & Keyboard Driver (Zero External Deps)
# ---------------------------------------------------------------------------

# ioctl constants for Linux uinput (<linux/uinput.h> and <linux/input-event-codes.h>)
UI_SET_EVBIT = 0x40045564
UI_SET_KEYBIT = 0x40045565
UI_SET_RELBIT = 0x40045566
UI_SET_ABSBIT = 0x40045567
UI_SET_PROPBIT = 0x4004556E
UI_DEV_CREATE = 0x5501
UI_DEV_DESTROY = 0x5502

EV_SYN = 0x00
EV_KEY = 0x01
EV_REL = 0x02
EV_ABS = 0x03

SYN_REPORT = 0x00

REL_X = 0x00
REL_Y = 0x01
REL_HWHEEL = 0x06
REL_WHEEL = 0x08

ABS_X = 0x00
ABS_Y = 0x01
INPUT_PROP_DIRECT = 0x01
INPUT_PROP_POINTER = 0x00

BTN_LEFT = 0x110
BTN_RIGHT = 0x111
BTN_MIDDLE = 0x112

# Standard Linux keycodes (<linux/input-event-codes.h>)
KEY_MAP: Dict[str, int] = {
    "esc": 1, "escape": 1,
    "1": 2, "2": 3, "3": 4, "4": 5, "5": 6, "6": 7, "7": 8, "8": 9, "9": 10, "0": 11,
    "minus": 12, "-": 12, "equal": 13, "=": 13,
    "backspace": 14, "bksp": 14,
    "tab": 15,
    "q": 16, "w": 17, "e": 18, "r": 19, "t": 20, "y": 21, "u": 22, "i": 23, "o": 24, "p": 25,
    "leftbrace": 26, "[": 26, "rightbrace": 27, "]": 27,
    "enter": 28, "return": 28, "\n": 28,
    "ctrl": 29, "leftctrl": 29, "control": 29,
    "a": 30, "s": 31, "d": 32, "f": 33, "g": 34, "h": 35, "j": 36, "k": 37, "l": 38,
    "semicolon": 39, ";": 39, "apostrophe": 40, "'": 40, "grave": 41, "`": 41,
    "shift": 42, "leftshift": 42,
    "backslash": 43, "\\": 43,
    "z": 44, "x": 45, "c": 46, "v": 47, "b": 48, "n": 49, "m": 50,
    "comma": 51, ",": 51, "dot": 52, ".": 52, "slash": 53, "/": 53,
    "rightshift": 54,
    "kpasterisk": 55,
    "alt": 56, "leftalt": 56,
    "space": 57, " ": 57,
    "capslock": 58,
    "f1": 59, "f2": 60, "f3": 61, "f4": 62, "f5": 63, "f6": 64,
    "f7": 65, "f8": 66, "f9": 67, "f10": 68, "f11": 87, "f12": 88,
    "rightctrl": 97,
    "rightalt": 100,
    "home": 102, "up": 103, "pageup": 104, "pgup": 104,
    "left": 105, "right": 106,
    "end": 107, "down": 108, "pagedown": 109, "pgdn": 109,
    "insert": 110, "delete": 111, "del": 111,
    "mute": 113, "volumedown": 114, "volumeup": 115,
    "super": 125, "meta": 125, "win": 125, "leftmeta": 125, "cmd": 125,
    "rightmeta": 126, "menu": 127,
}

# Characters that require holding Shift on a US QWERTY layout
SHIFT_CHARS: Dict[str, str] = {
    "!": "1", "@": "2", "#": "3", "$": "4", "%": "5", "^": "6", "&": "7", "*": "8", "(": "9", ")": "0",
    "_": "-", "+": "=", "{": "[", "}": "]", "|": "\\", ":": ";", '"': "'", "<": ",", ">": ".", "?": "/",
    "~": "`",
}


class UInputController:
    """
    Creates an ephemeral Linux uinput virtual absolute pointer + keyboard device.
    Works across X11 and all Wayland compositors (KDE, GNOME, Hyprland, Sway)
    Whenever `/dev/uinput` is writable by the user.
    """

    ABS_MAX = 32767

    def __init__(self, screen_width: int = 1920, screen_height: int = 1080):
        self.fd: Optional[int] = None
        self.screen_width = max(1, screen_width)
        self.screen_height = max(1, screen_height)

    def __enter__(self) -> "UInputController":
        self.fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)

        # Enable event types
        fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_SYN)
        fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_KEY)
        fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_REL)
        fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_ABS)

        # Enable mouse buttons
        for btn in (BTN_LEFT, BTN_RIGHT, BTN_MIDDLE):
            fcntl.ioctl(self.fd, UI_SET_KEYBIT, btn)

        # Enable all keyboard keys in KEY_MAP
        for code in set(KEY_MAP.values()):
            fcntl.ioctl(self.fd, UI_SET_KEYBIT, code)

        # Enable relative wheel and motion
        for rel in (REL_X, REL_Y, REL_WHEEL, REL_HWHEEL):
            fcntl.ioctl(self.fd, UI_SET_RELBIT, rel)

        # Enable absolute X/Y axes for exact screen-coordinate positioning
        fcntl.ioctl(self.fd, UI_SET_ABSBIT, ABS_X)
        fcntl.ioctl(self.fd, UI_SET_ABSBIT, ABS_Y)

        # Build legacy `uinput_user_dev` struct:
        # char name[80]; struct input_id (4 * uint16); uint32 ff_effects_max;
        # int32 absmax[64]; int32 absmin[64]; int32 absfuzz[64]; int32 absflat[64];
        name = b"Antigravity Virtual Input"
        name_padded = name.ljust(80, b"\x00")
        input_id = struct.pack("HHHH", 0x03, 0x1234, 0x5678, 1)  # BUS_USB
        ff_effects_max = struct.pack("I", 0)

        absmax = [0] * 64
        absmin = [0] * 64
        absfuzz = [0] * 64
        absflat = [0] * 64
        absmax[ABS_X] = self.ABS_MAX
        absmax[ABS_Y] = self.ABS_MAX

        abs_arrays = (
            struct.pack("64i", *absmax)
            + struct.pack("64i", *absmin)
            + struct.pack("64i", *absfuzz)
            + struct.pack("64i", *absflat)
        )

        udev_bytes = name_padded + input_id + ff_effects_max + abs_arrays
        os.write(self.fd, udev_bytes)
        fcntl.ioctl(self.fd, UI_DEV_CREATE)
        # Give libinput / compositor ~120ms to register the hotplugged virtual device
        time.sleep(0.14)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self.fd is not None:
            try:
                time.sleep(0.04)
                fcntl.ioctl(self.fd, UI_DEV_DESTROY)
            except OSError:
                pass
            finally:
                os.close(self.fd)
                self.fd = None

    def _emit(self, ev_type: int, ev_code: int, ev_value: int, syn: bool = True) -> None:
        if self.fd is None:
            raise RuntimeError("UInputController is not open")
        now = time.time()
        sec = int(now)
        usec = int((now - sec) * 1_000_000)
        # struct input_event on 64-bit Linux: long tv_sec, long tv_usec, uint16 type, uint16 code, int32 value
        pkt = struct.pack("llHHi", sec, usec, ev_type, ev_code, ev_value)
        os.write(self.fd, pkt)
        if syn:
            syn_pkt = struct.pack("llHHi", sec, usec, EV_SYN, SYN_REPORT, 0)
            os.write(self.fd, syn_pkt)

    def move_to(self, x: int, y: int, image_width: Optional[int] = None, image_height: Optional[int] = None) -> Tuple[int, int]:
        """Move pointer to absolute screen/image coordinates (x, y)."""
        w = image_width or self.screen_width
        h = image_height or self.screen_height
        clamped_x = max(0, min(x, w - 1))
        clamped_y = max(0, min(y, h - 1))
        ax = int(round((clamped_x / max(1, w - 1)) * self.ABS_MAX))
        ay = int(round((clamped_y / max(1, h - 1)) * self.ABS_MAX))
        self._emit(EV_ABS, ABS_X, ax, syn=False)
        self._emit(EV_ABS, ABS_Y, ay, syn=True)
        time.sleep(0.03)
        return clamped_x, clamped_y

    def move_rel(self, dx: int, dy: int) -> None:
        """Move pointer by relative delta (dx, dy)."""
        self._emit(EV_REL, REL_X, int(dx), syn=False)
        self._emit(EV_REL, REL_Y, int(dy), syn=True)
        time.sleep(0.03)

    def click(self, button: str = "left", count: int = 1) -> None:
        btn_code = {
            "left": BTN_LEFT, "1": BTN_LEFT,
            "right": BTN_RIGHT, "3": BTN_RIGHT,
            "middle": BTN_MIDDLE, "2": BTN_MIDDLE,
        }.get(button.lower(), BTN_LEFT)

        for i in range(count):
            self._emit(EV_KEY, btn_code, 1)
            time.sleep(0.035)
            self._emit(EV_KEY, btn_code, 0)
            if i + 1 < count:
                time.sleep(0.08)

    def drag(
        self,
        start_x: int, start_y: int,
        end_x: int, end_y: int,
        button: str = "left",
        steps: int = 20,
        duration: float = 0.35,
        image_width: Optional[int] = None,
        image_height: Optional[int] = None,
    ) -> None:
        btn_code = {
            "left": BTN_LEFT, "right": BTN_RIGHT, "middle": BTN_MIDDLE
        }.get(button.lower(), BTN_LEFT)

        self.move_to(start_x, start_y, image_width=image_width, image_height=image_height)
        time.sleep(0.05)
        self._emit(EV_KEY, btn_code, 1)
        time.sleep(0.05)

        step_sleep = max(0.005, duration / max(1, steps))
        for i in range(1, steps + 1):
            ix = int(round(start_x + (end_x - start_x) * (i / steps)))
            iy = int(round(start_y + (end_y - start_y) * (i / steps)))
            self.move_to(ix, iy, image_width=image_width, image_height=image_height)
            time.sleep(step_sleep)

        time.sleep(0.05)
        self._emit(EV_KEY, btn_code, 0)

    def scroll(self, dy: int = 0, dx: int = 0) -> None:
        """Scroll vertically (dy > 0 up, dy < 0 down) and horizontally (dx)."""
        if dy != 0:
            step = 1 if dy > 0 else -1
            for _ in range(abs(dy)):
                self._emit(EV_REL, REL_WHEEL, step)
                time.sleep(0.02)
        if dx != 0:
            step = 1 if dx > 0 else -1
            for _ in range(abs(dx)):
                self._emit(EV_REL, REL_HWHEEL, step)
                time.sleep(0.02)

    def key_combo(self, combo: str) -> None:
        """Press a key or key combination such as 'ctrl+c', 'alt+tab', 'super', 'enter'."""
        parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
        codes: List[int] = []
        for p in parts:
            if p in SHIFT_CHARS:
                codes.append(KEY_MAP["shift"])
                p = SHIFT_CHARS[p]
            code = KEY_MAP.get(p)
            if code is None:
                raise ValueError(f"Unknown key '{p}' in combo '{combo}'. Supported keys: {', '.join(sorted(KEY_MAP.keys()))}")
            codes.append(code)

        # Press in order
        for c in codes:
            self._emit(EV_KEY, c, 1)
            time.sleep(0.02)
        time.sleep(0.03)
        # Release in reverse order
        for c in reversed(codes):
            self._emit(EV_KEY, c, 0)
            time.sleep(0.015)

    def type_text(self, text: str, delay: float = 0.018) -> None:
        """Type an ASCII text string via virtual keyboard events."""
        for ch in text:
            need_shift = False
            lookup = ch
            if ch.isupper():
                need_shift = True
                lookup = ch.lower()
            elif ch in SHIFT_CHARS:
                need_shift = True
                lookup = SHIFT_CHARS[ch]

            code = KEY_MAP.get(lookup)
            if code is None:
                # Skip unsupported non-ASCII characters (use clipboard paste for Unicode)
                continue

            if need_shift:
                self._emit(EV_KEY, KEY_MAP["shift"], 1)
                time.sleep(0.01)

            self._emit(EV_KEY, code, 1)
            time.sleep(delay)
            self._emit(EV_KEY, code, 0)

            if need_shift:
                time.sleep(0.01)
                self._emit(EV_KEY, KEY_MAP["shift"], 0)
            time.sleep(delay)


# ---------------------------------------------------------------------------
# High-Level Actions (Screenshots, Mouse, Keyboard, Windows, Clipboard)
# ---------------------------------------------------------------------------

def take_screenshot(output_path: str, window_only: bool = False, region: Optional[str] = None) -> Dict[str, Any]:
    """
    Take a screenshot using the best available tool for the current compositor.
    Returns metadata including path, image width/height, and backend used.
    """
    env = detect_environment()
    out = Path(output_path).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    tools = env["available_tools"]
    compositor = env["compositor"]
    errors: List[str] = []

    candidates: List[Tuple[str, List[str]]] = []

    if env["session_type"] == "wayland":
        if compositor == "kde-kwin" and "spectacle" in tools:
            cmd = ["spectacle", "-b", "-n"]
            if window_only:
                cmd.append("-a")
            else:
                cmd.append("-f")
            cmd.extend(["-o", str(out)])
            candidates.append(("spectacle", cmd))

        if "grim" in tools:
            cmd = ["grim"]
            if region:
                cmd.extend(["-g", region])
            cmd.append(str(out))
            candidates.append(("grim", cmd))

        if "gnome-screenshot" in tools:
            cmd = ["gnome-screenshot"]
            if window_only:
                cmd.append("-w")
            cmd.extend(["-f", str(out)])
            candidates.append(("gnome-screenshot", cmd))

    # X11 or fallback tools
    if "maim" in tools:
        cmd = ["maim"]
        if region:
            cmd.extend(["-g", region])
        cmd.append(str(out))
        candidates.append(("maim", cmd))

    if "scrot" in tools:
        cmd = ["scrot", "--overwrite"]
        if window_only:
            cmd.append("-u")
        cmd.append(str(out))
        candidates.append(("scrot", cmd))

    if "import" in tools:
        candidates.append(("imagemagick-import", ["import", "-window", "root", str(out)]))

    # Ensure spectable/grim fallback if session_type wasn't explicitly set
    if not candidates:
        if "spectacle" in tools:
            candidates.append(("spectacle", ["spectacle", "-b", "-n", "-f", "-o", str(out)]))
        if "grim" in tools:
            candidates.append(("grim", ["grim", str(out)]))

    if out.exists():
        try:
            out.unlink()
        except OSError:
            pass

    used_backend = None
    for backend_name, cmd in candidates:
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
            if res.returncode == 0 and out.exists() and out.stat().st_size > 0:
                used_backend = backend_name
                break
            else:
                errors.append(f"{backend_name}: exit={res.returncode} stderr={res.stderr.strip()}")
        except Exception as e:
            errors.append(f"{backend_name}: {e}")

    if not used_backend or not out.exists():
        hint = ""
        if env["sandbox_blocks_display_sockets"]:
            hint = (
                "Display/DBus sockets in /run/user/$UID are not accessible inside the standard sandbox. "
                "Re-run this command with BypassSandbox: true in run_command."
            )
        return {
            "ok": False,
            "error": "Failed to capture screenshot with any available backend.",
            "details": errors,
            "hint": hint,
        }

    # Read image dimensions via PIL if available
    img_w, img_h = None, None
    try:
        from PIL import Image
        with Image.open(out) as im:
            img_w, img_h = im.size
    except Exception:
        pass

    screen = get_screen_info()
    return {
        "ok": True,
        "path": str(out),
        "backend": used_backend,
        "image_width": img_w,
        "image_height": img_h,
        "logical_width": screen["logical_width"],
        "logical_height": screen["logical_height"],
        "scale": screen["scale"],
    }


def perform_click(
    x: Optional[int],
    y: Optional[int],
    button: str = "left",
    count: int = 1,
    image_width: Optional[int] = None,
    image_height: Optional[int] = None,
) -> Dict[str, Any]:
    """Move to (x, y) if provided and click mouse button."""
    env = detect_environment()
    screen = get_screen_info()
    tools = env["available_tools"]

    # Prefer uinput (works universally on Wayland + X11 and even inside the sandbox!)
    if env["uinput_writable"]:
        with UInputController(screen["logical_width"], screen["logical_height"]) as ui:
            target = None
            if x is not None and y is not None:
                target = ui.move_to(x, y, image_width=image_width, image_height=image_height)
            ui.click(button=button, count=count)
        return {
            "ok": True,
            "backend": "uinput",
            "x": target[0] if target else None,
            "y": target[1] if target else None,
            "button": button,
            "count": count,
        }

    # Fallback to xdotool on X11
    if env["session_type"] == "x11" and "xdotool" in tools:
        btn_num = {"left": "1", "middle": "2", "right": "3"}.get(button.lower(), "1")
        cmd = ["xdotool"]
        if x is not None and y is not None:
            cmd.extend(["mousemove", "--sync", str(x), str(y)])
        cmd.extend(["click", "--repeat", str(count), btn_num])
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        return {"ok": res.returncode == 0, "backend": "xdotool", "stderr": res.stderr.strip()}

    # Fallback to ydotool
    if "ydotool" in tools:
        btn_hex = {"left": "0xC0", "right": "0xC1", "middle": "0xC2"}.get(button.lower(), "0xC0")
        if x is not None and y is not None:
            subprocess.run(["ydotool", "mousemove", "--absolute", "-x", str(x), "-y", str(y)], check=False)
        res = subprocess.run(["ydotool", "click", "--repeat", str(count), btn_hex], capture_output=True, text=True)
        return {"ok": res.returncode == 0, "backend": "ydotool", "stderr": res.stderr.strip()}

    return {
        "ok": False,
        "error": "No input injection backend available (/dev/uinput not writable and xdotool/ydotool not found).",
    }


def perform_move(
    x: int,
    y: int,
    relative: bool = False,
    image_width: Optional[int] = None,
    image_height: Optional[int] = None,
) -> Dict[str, Any]:
    env = detect_environment()
    screen = get_screen_info()
    tools = env["available_tools"]

    if env["uinput_writable"]:
        with UInputController(screen["logical_width"], screen["logical_height"]) as ui:
            if relative:
                ui.move_rel(x, y)
                return {"ok": True, "backend": "uinput", "relative": True, "dx": x, "dy": y}
            else:
                cx, cy = ui.move_to(x, y, image_width=image_width, image_height=image_height)
                return {"ok": True, "backend": "uinput", "relative": False, "x": cx, "y": cy}

    if env["session_type"] == "x11" and "xdotool" in tools:
        cmd = ["xdotool", "mousemove_relative" if relative else "mousemove", "--", str(x), str(y)]
        res = subprocess.run(cmd, capture_output=True, text=True)
        return {"ok": res.returncode == 0, "backend": "xdotool", "stderr": res.stderr.strip()}

    return {"ok": False, "error": "No pointer move backend available."}


def perform_drag(
    start_x: int, start_y: int,
    end_x: int, end_y: int,
    button: str = "left",
    duration: float = 0.35,
    image_width: Optional[int] = None,
    image_height: Optional[int] = None,
) -> Dict[str, Any]:
    env = detect_environment()
    screen = get_screen_info()
    if env["uinput_writable"]:
        with UInputController(screen["logical_width"], screen["logical_height"]) as ui:
            ui.drag(
                start_x, start_y, end_x, end_y,
                button=button, duration=duration,
                image_width=image_width, image_height=image_height,
            )
        return {
            "ok": True,
            "backend": "uinput",
            "from": [start_x, start_y],
            "to": [end_x, end_y],
            "button": button,
        }
    return {"ok": False, "error": "/dev/uinput is required for smooth drag operations."}


def perform_scroll(
    dy: int = 0,
    dx: int = 0,
    x: Optional[int] = None,
    y: Optional[int] = None,
    image_width: Optional[int] = None,
    image_height: Optional[int] = None,
) -> Dict[str, Any]:
    env = detect_environment()
    screen = get_screen_info()
    if env["uinput_writable"]:
        with UInputController(screen["logical_width"], screen["logical_height"]) as ui:
            if x is not None and y is not None:
                ui.move_to(x, y, image_width=image_width, image_height=image_height)
            ui.scroll(dy=dy, dx=dx)
        return {"ok": True, "backend": "uinput", "dy": dy, "dx": dx}
    return {"ok": False, "error": "No scroll backend available."}


def perform_key(combo: str) -> Dict[str, Any]:
    env = detect_environment()
    screen = get_screen_info()
    tools = env["available_tools"]

    if env["uinput_writable"]:
        with UInputController(screen["logical_width"], screen["logical_height"]) as ui:
            ui.key_combo(combo)
        return {"ok": True, "backend": "uinput", "combo": combo}

    if env["session_type"] == "x11" and "xdotool" in tools:
        res = subprocess.run(["xdotool", "key", combo], capture_output=True, text=True)
        return {"ok": res.returncode == 0, "backend": "xdotool", "stderr": res.stderr.strip()}

    return {"ok": False, "error": "No key injection backend available."}


def perform_type(text: str, paste: bool = False, enter: bool = False) -> Dict[str, Any]:
    env = detect_environment()
    screen = get_screen_info()
    tools = env["available_tools"]

    # If text contains non-ASCII or user requested clipboard-paste mode, copy to clipboard and press Ctrl+V
    is_ascii = all(32 <= ord(c) < 127 or c in "\n\t" for c in text)
    if paste or not is_ascii:
        cb_res = clipboard_set(text)
        if cb_res.get("ok"):
            time.sleep(0.05)
            key_res = perform_key("ctrl+v")
            if enter:
                time.sleep(0.05)
                perform_key("enter")
            return {"ok": key_res.get("ok", False), "backend": "clipboard+ctrl+v", "length": len(text)}

    if env["uinput_writable"]:
        with UInputController(screen["logical_width"], screen["logical_height"]) as ui:
            ui.type_text(text)
            if enter:
                time.sleep(0.04)
                ui.key_combo("enter")
        return {"ok": True, "backend": "uinput", "length": len(text), "enter": enter}

    if "wtype" in tools and env["session_type"] == "wayland":
        res = subprocess.run(["wtype", "--", text], capture_output=True, text=True)
        if enter:
            subprocess.run(["wtype", "-k", "Return"], check=False)
        return {"ok": res.returncode == 0, "backend": "wtype"}

    if "xdotool" in tools and env["session_type"] == "x11":
        res = subprocess.run(["xdotool", "type", "--clearmodifiers", "--", text], capture_output=True, text=True)
        if enter:
            subprocess.run(["xdotool", "key", "Return"], check=False)
        return {"ok": res.returncode == 0, "backend": "xdotool"}

    return {"ok": False, "error": "No typing backend available."}


# ---------------------------------------------------------------------------
# Clipboard Management
# ---------------------------------------------------------------------------

def clipboard_get() -> Dict[str, Any]:
    env = detect_environment()
    tools = env["available_tools"]

    if env["session_type"] == "wayland" and "wl-paste" in tools:
        res = subprocess.run(["wl-paste", "--no-newline"], capture_output=True, text=True, timeout=3)
        if res.returncode == 0:
            return {"ok": True, "backend": "wl-paste", "text": res.stdout}

    if "xclip" in tools:
        res = subprocess.run(["xclip", "-selection", "clipboard", "-o"], capture_output=True, text=True, timeout=3)
        if res.returncode == 0:
            return {"ok": True, "backend": "xclip", "text": res.stdout}

    if "xsel" in tools:
        res = subprocess.run(["xsel", "--clipboard", "--output"], capture_output=True, text=True, timeout=3)
        if res.returncode == 0:
            return {"ok": True, "backend": "xsel", "text": res.stdout}

    if "qdbus6" in tools:
        res = subprocess.run(
            ["qdbus6", "org.kde.klipper", "/klipper", "getClipboardContents"],
            capture_output=True, text=True, timeout=3
        )
        if res.returncode == 0:
            return {"ok": True, "backend": "klipper-dbus", "text": res.stdout.rstrip("\n")}

    return {"ok": False, "error": "Could not read clipboard (requires display/DBus access or wl-paste/xclip)."}


def clipboard_set(text: str) -> Dict[str, Any]:
    env = detect_environment()
    tools = env["available_tools"]

    if env["session_type"] == "wayland" and "wl-copy" in tools:
        try:
            proc = subprocess.Popen(["wl-copy", "--", text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(0.08)
            return {"ok": True, "backend": "wl-copy", "length": len(text)}
        except Exception:
            pass

    if "qdbus6" in tools:
        res = subprocess.run(
            ["qdbus6", "org.kde.klipper", "/klipper", "setClipboardContents", text],
            capture_output=True, text=True, timeout=3
        )
        if res.returncode == 0:
            return {"ok": True, "backend": "klipper-dbus", "length": len(text)}

    if "xclip" in tools:
        proc = subprocess.Popen(["xclip", "-selection", "clipboard"], stdin=subprocess.PIPE)
        proc.communicate(text.encode("utf-8"), timeout=3)
        return {"ok": proc.returncode == 0, "backend": "xclip", "length": len(text)}

    if "xsel" in tools:
        proc = subprocess.Popen(["xsel", "--clipboard", "--input"], stdin=subprocess.PIPE)
        proc.communicate(text.encode("utf-8"), timeout=3)
        return {"ok": proc.returncode == 0, "backend": "xsel", "length": len(text)}

    return {"ok": False, "error": "Could not write clipboard."}


# ---------------------------------------------------------------------------
# Window Management (KDE KWin, Hyprland, Sway, X11)
# ---------------------------------------------------------------------------

def list_windows() -> Dict[str, Any]:
    env = detect_environment()
    tools = env["available_tools"]
    compositor = env["compositor"]
    windows: List[Dict[str, Any]] = []

    # 1. Hyprland
    if compositor == "hyprland" and "hyprctl" in tools:
        res = subprocess.run(["hyprctl", "clients", "-j"], capture_output=True, text=True, timeout=3)
        if res.returncode == 0:
            for w in json.loads(res.stdout):
                windows.append({
                    "id": w.get("address"),
                    "title": w.get("title"),
                    "class": w.get("class"),
                    "x": w.get("at", [0, 0])[0],
                    "y": w.get("at", [0, 0])[1],
                    "width": w.get("size", [0, 0])[0],
                    "height": w.get("size", [0, 0])[1],
                    "pid": w.get("pid"),
                    "workspace": w.get("workspace", {}).get("name"),
                })
            return {"ok": True, "backend": "hyprctl", "windows": windows}

    # 2. Sway
    if compositor == "sway" and "swaymsg" in tools:
        res = subprocess.run(["swaymsg", "-t", "get_tree", "-r"], capture_output=True, text=True, timeout=3)
        if res.returncode == 0:
            def walk(node: Dict[str, Any]) -> None:
                if node.get("type") in ("con", "floating_con") and node.get("name") and node.get("pid"):
                    rect = node.get("rect", {})
                    windows.append({
                        "id": str(node.get("id")),
                        "title": node.get("name"),
                        "class": node.get("app_id") or node.get("window_properties", {}).get("class"),
                        "x": rect.get("x"),
                        "y": rect.get("y"),
                        "width": rect.get("width"),
                        "height": rect.get("height"),
                        "focused": node.get("focused", False),
                        "pid": node.get("pid"),
                    })
                for child in node.get("nodes", []) + node.get("floating_nodes", []):
                    walk(child)

            walk(json.loads(res.stdout))
            return {"ok": True, "backend": "swaymsg", "windows": windows}

    # 3. KDE KWin (queryWindowInfo or KWin supportInformation client list)
    if compositor == "kde-kwin" and "qdbus6" in tools and env["runtime_dir_accessible"]:
        res = subprocess.run(
            ["qdbus6", "org.kde.KWin", "/KWin", "supportInformation"],
            capture_output=True, text=True, timeout=4
        )
        if res.returncode == 0 and "=== Clients ===" in res.stdout:
            clients_section = res.stdout.split("=== Clients ===", 1)[1]
            cur: Dict[str, Any] = {}
            for line in clients_section.splitlines():
                line_s = line.strip()
                if not line_s:
                    continue
                if line_s.startswith("Window ") or line_s.startswith("Client "):
                    if cur:
                        windows.append(cur)
                    cur = {"raw": line_s}
                elif ":" in line_s:
                    k, v = [p.strip() for p in line_s.split(":", 1)]
                    kl = k.lower()
                    if kl in ("caption", "title"):
                        cur["title"] = v
                    elif kl in ("resourceclass", "resourceName", "desktopfilename"):
                        cur.setdefault("class", v)
                    elif kl == "geometry":
                        cur["geometry"] = v
                    elif kl == "pid":
                        cur["pid"] = v
            if cur:
                windows.append(cur)
            return {"ok": True, "backend": "kwin-dbus", "windows": windows}

    # 4. wmctrl on X11
    if "wmctrl" in tools:
        res = subprocess.run(["wmctrl", "-l", "-G", "-p"], capture_output=True, text=True, timeout=3)
        if res.returncode == 0:
            for line in res.stdout.splitlines():
                parts = line.split(None, 8)
                if len(parts) >= 8:
                    windows.append({
                        "id": parts[0],
                        "desktop": parts[1],
                        "pid": parts[2],
                        "x": int(parts[3]),
                        "y": int(parts[4]),
                        "width": int(parts[5]),
                        "height": int(parts[6]),
                        "title": parts[8] if len(parts) > 8 else "",
                    })
            return {"ok": True, "backend": "wmctrl", "windows": windows}

    return {
        "ok": False,
        "error": "Unable to query window list (if running sandboxed on Wayland, retry with BypassSandbox: true).",
    }


def launch_app(command_or_target: str, is_url_or_file: bool = False) -> Dict[str, Any]:
    """Launch a desktop application or open a URL/file detached from the terminal."""
    ensure_desktop_env()
    if is_url_or_file:
        cmd = ["xdg-open", command_or_target]
    else:
        cmd = ["bash", "-c", f"nohup {command_or_target} >/dev/null 2>&1 &"]

    try:
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return {"ok": True, "launched": command_or_target}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------------------
# CLI Entrypoint
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cu.py",
        description="Universal Linux Computer Use CLI for Antigravity",
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    # doctor
    sub.add_parser("doctor", help="Inspect display server, compositor, uinput permissions, and tools")

    # screen-info
    sub.add_parser("screen-info", help="Get logical and physical screen geometry and scaling")

    # screenshot
    p_ss = sub.add_parser("screenshot", help="Capture a screenshot of the desktop or active window")
    p_ss.add_argument("-o", "--output", default="/tmp/agy_screenshot.png", help="Output PNG path")
    p_ss.add_argument("-w", "--window", action="store_true", help="Capture active window only")
    p_ss.add_argument("-g", "--region", help="Capture region 'X,Y WxH' (for grim/maim)")

    # click
    p_click = sub.add_parser("click", help="Move to (x, y) and click a mouse button")
    p_click.add_argument("x", type=int, nargs="?", help="X coordinate")
    p_click.add_argument("y", type=int, nargs="?", help="Y coordinate")
    p_click.add_argument("-b", "--button", default="left", choices=["left", "right", "middle"], help="Mouse button")
    p_click.add_argument("-c", "--count", type=int, default=1, help="Click count (2 for double-click)")
    p_click.add_argument("--image-width", type=int, help="Source screenshot width (auto-scales HiDPI coordinates)")
    p_click.add_argument("--image-height", type=int, help="Source screenshot height (auto-scales HiDPI coordinates)")

    # move
    p_move = sub.add_parser("move", help="Move mouse pointer to (x, y) or by relative offset")
    p_move.add_argument("x", type=int, help="X coordinate or dX")
    p_move.add_argument("y", type=int, help="Y coordinate or dY")
    p_move.add_argument("-r", "--relative", action="store_true", help="Relative movement")
    p_move.add_argument("--image-width", type=int, help="Source screenshot width")
    p_move.add_argument("--image-height", type=int, help="Source screenshot height")

    # drag
    p_drag = sub.add_parser("drag", help="Drag mouse from (start_x, start_y) to (end_x, end_y)")
    p_drag.add_argument("start_x", type=int)
    p_drag.add_argument("start_y", type=int)
    p_drag.add_argument("end_x", type=int)
    p_drag.add_argument("end_y", type=int)
    p_drag.add_argument("-b", "--button", default="left", choices=["left", "right", "middle"])
    p_drag.add_argument("-d", "--duration", type=float, default=0.35, help="Drag duration in seconds")
    p_drag.add_argument("--image-width", type=int, help="Source screenshot width")
    p_drag.add_argument("--image-height", type=int, help="Source screenshot height")

    # scroll
    p_scroll = sub.add_parser("scroll", help="Scroll mouse wheel (dy > 0 up, dy < 0 down)")
    p_scroll.add_argument("dy", type=int, help="Vertical scroll clicks (positive=up, negative=down)")
    p_scroll.add_argument("--dx", type=int, default=0, help="Horizontal scroll clicks")
    p_scroll.add_argument("-x", type=int, help="Optional X coordinate to move pointer to before scrolling")
    p_scroll.add_argument("-y", type=int, help="Optional Y coordinate to move pointer to before scrolling")
    p_scroll.add_argument("--image-width", type=int, help="Source screenshot width")
    p_scroll.add_argument("--image-height", type=int, help="Source screenshot height")

    # key
    p_key = sub.add_parser("key", help="Press a key or shortcut combo (e.g. 'ctrl+c', 'alt+tab', 'super', 'enter')")
    p_key.add_argument("combo", help="Key combination joined by '+'")

    # type
    p_type = sub.add_parser("type", help="Type text string via keyboard or clipboard paste")
    p_type.add_argument("text", help="Text string to type")
    p_type.add_argument("--paste", action="store_true", help="Copy to clipboard and press Ctrl+V (fast & Unicode-safe)")
    p_type.add_argument("--enter", action="store_true", help="Press Enter after typing")

    # clipboard-get
    sub.add_parser("clipboard-get", help="Read current system clipboard contents")

    # clipboard-set
    p_cbset = sub.add_parser("clipboard-set", help="Set system clipboard contents")
    p_cbset.add_argument("text", help="Text to place in clipboard")

    # windows
    sub.add_parser("windows", help="List open desktop windows and their titles/geometry")

    # launch
    p_launch = sub.add_parser("launch", help="Launch a desktop application in the background")
    p_launch.add_argument("command", help="Application binary or command string")

    # open
    p_open = sub.add_parser("open", help="Open a file or URL with xdg-open")
    p_open.add_argument("target", help="File path or URL")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.subcommand == "doctor":
        env = detect_environment()
        screen = get_screen_info()
        result = {"ok": True, "environment": env, "screen": screen}
    elif args.subcommand == "screen-info":
        result = {"ok": True, **get_screen_info()}
    elif args.subcommand == "screenshot":
        result = take_screenshot(args.output, window_only=args.window, region=args.region)
    elif args.subcommand == "click":
        result = perform_click(
            args.x, args.y,
            button=args.button, count=args.count,
            image_width=args.image_width, image_height=args.image_height,
        )
    elif args.subcommand == "move":
        result = perform_move(
            args.x, args.y,
            relative=args.relative,
            image_width=args.image_width, image_height=args.image_height,
        )
    elif args.subcommand == "drag":
        result = perform_drag(
            args.start_x, args.start_y, args.end_x, args.end_y,
            button=args.button, duration=args.duration,
            image_width=args.image_width, image_height=args.image_height,
        )
    elif args.subcommand == "scroll":
        result = perform_scroll(
            dy=args.dy, dx=args.dx, x=args.x, y=args.y,
            image_width=args.image_width, image_height=args.image_height,
        )
    elif args.subcommand == "key":
        result = perform_key(args.combo)
    elif args.subcommand == "type":
        result = perform_type(args.text, paste=args.paste, enter=args.enter)
    elif args.subcommand == "clipboard-get":
        result = clipboard_get()
    elif args.subcommand == "clipboard-set":
        result = clipboard_set(args.text)
    elif args.subcommand == "windows":
        result = list_windows()
    elif args.subcommand == "launch":
        result = launch_app(args.command, is_url_or_file=False)
    elif args.subcommand == "open":
        result = launch_app(args.target, is_url_or_file=True)
    else:
        result = {"ok": False, "error": f"Unknown subcommand: {args.subcommand}"}

    print(json.dumps(result, indent=2))
    return 0 if result.get("ok", False) else 1


if __name__ == "__main__":
    sys.exit(main())
