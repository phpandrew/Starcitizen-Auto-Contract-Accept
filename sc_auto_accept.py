"""
Star Citizen - Auto-Accept
==========================

Watches a strip across the top-center of your chosen monitor. When a popup
appears that you've chosen to auto-accept (Contract Shared, party invite, or
friend request), it presses the [ key to ACCEPT it.

Detection is done with OCR (optical character recognition) via Tesseract.

Settings (away/pause hotkeys + which popups to auto-accept) live in
settings.json next to this script. The settings window opens each time the
script starts; you can also change settings from the system-tray icon.

Fixed hotkeys (work even while the game has focus):
    Ctrl+F10  - save a debug snapshot of what the script is currently capturing
    F9        - quit
The away-mode and pause hotkeys are configurable in the settings window.

This script only READS the screen and presses keys. It does not touch the
game's files or memory.

See run.bat / install_requirements.bat for setup help.
"""

import os
import sys
import time
import json
import ctypes
import difflib
import datetime
import threading

# Folder this script lives in - used for settings.json, the debug folder,
# and the optional tray icon file.
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Version of this tool. Bump this whenever the script changes.
VERSION = "1.14"

# ----------------------------------------------------------------------
# CONFIG  -- tweak these values, then save and re-run.
# ----------------------------------------------------------------------

# Which monitor to watch.
#   "middle" = auto-pick the physically middle monitor by position (recommended)
#   a number = force a specific monitor (the capture library numbers them from 1)
#   None     = ask at startup
MONITOR_INDEX = "middle"

# Capture region as a fraction of the monitor, measured from the top-center.
REGION_WIDTH_FRACTION  = 0.55   # 0.55 = center 55% of the monitor width
REGION_HEIGHT_FRACTION = 0.20   # 0.20 = top 20% of the monitor height

# How often to grab and scan a screenshot, in seconds.
SCAN_INTERVAL = 1.5

# --- Accepting -------------------------------------------------------------
ACCEPT_KEY = "["                 # key that ACCEPTS a popup in Star Citizen
KEY_HOLD_SECONDS = 0.06          # how long to hold a key for one tap
ACCEPT_PRESS_COUNT = 2           # presses per accept sequence
ACCEPT_PRESS_GAP_SECONDS = 3.0   # gap between those presses
ACCEPT_COOLDOWN_SECONDS = 6.0    # wait after accepting before accepting again

# --- Away Mode (anti-idle) -------------------------------------------------
AWAY_KEY = "up"                  # key tapped so the game sees you as active
AWAY_INTERVAL_SECONDS = 120      # how often to tap it (120 = every 2 minutes)
AWAY_PRESS_COUNT = 2             # how many taps per interval
AWAY_PRESS_GAP_SECONDS = 0.05    # gap between those taps
AWAY_MODE_AT_STARTUP = False     # default; actual value is a saved setting

# --- Detection -------------------------------------------------------------
# Each popup type is recognised by two words appearing NEXT TO each other in
# the OCR text. Adjacency keeps it strict (e.g. the "Accepted Contracts"
# tracker panel does not trigger the contract matcher).
CONTRACT_WORDS = ["contract", "shared"]
PARTY_WORDS    = ["party", "invite"]      # best-guess wording for party invites
FRIEND_WORDS   = ["friend", "request"]    # best-guess wording for friend requests

# Brightness cutoff (0-255) for isolating the popup's white text from the game
# scene behind it. Lower it if the script misses popups; raise it if it
# false-triggers.
OCR_BRIGHT_THRESHOLD = 190

# How close an OCR word must be to a target word to count (0.0-1.0). OCR makes
# mistakes on stylised game fonts; this tolerates them. 0.82 ~= 1 wrong letter.
FUZZY_CUTOFF = 0.82

# Save the captured strip to the /debug folder whenever a popup is accepted.
SAVE_DEBUG_IMAGES = False

# --- Fixed hotkeys ---------------------------------------------------------
# The away and pause hotkeys are editable in the Settings window; these two
# are fixed.
DEBUG_HOTKEY = "ctrl+f10"
QUIT_HOTKEY  = "f9"

# --- Saved settings --------------------------------------------------------
# These live in settings.json next to the script and are editable in the
# Settings window (shown on first run, and from the tray menu afterwards).
SETTINGS_FILE = os.path.join(SCRIPT_DIR, "settings.json")
DEFAULT_SETTINGS = {
    "away_hotkey": "ctrl+f12",
    "pause_hotkey": "f8",
    "accept_contracts": True,
    "accept_party_invites": False,
    "accept_friend_requests": False,
    "verbose": True,            # show the detailed per-scan debug log
    "hide_console": False,      # fully hide the console after Start (tray only)
    "scan_interval": SCAN_INTERVAL,   # seconds between screen scans
    "monitor_index": "middle",  # "middle" auto-picks, or an int (mss id)
    "away_mode": False,         # start with Away Mode on? (anti-idle)
    "dark_mode": True,          # dark theme for the Settings window
}

# --- System tray -----------------------------------------------------------
# Show a tray icon with a right-click menu. Needs the 'pystray' library.
ENABLE_TRAY_ICON = True
# If a file named tray_icon.png (or .ico / .jpg) is in this folder it is used
# as the tray icon; otherwise a simple icon is drawn.
TRAY_ICON_NAMES = ["tray_icon.png", "tray_icon.ico", "tray_icon.jpg"]

# If Tesseract is not on your PATH, put the full path to tesseract.exe here.
TESSERACT_PATH = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# ----------------------------------------------------------------------
# End of config.
# ----------------------------------------------------------------------


def setup_console():
    """Black background, white text. Stops the window burning your eyes."""
    if os.name == "nt":
        os.system("")            # enables ANSI escape handling on modern Windows
        os.system("color 0F")    # 0 = black background, F = white text
        os.system("title Star Citizen Auto-Accept")


def get_console_window():
    """Return the handle of this script's console window (0 if there is none)."""
    try:
        ctypes.windll.kernel32.GetConsoleWindow.restype = ctypes.c_void_p
        return ctypes.windll.kernel32.GetConsoleWindow()
    except Exception:
        return 0


def set_console_visible(visible):
    """Show or fully hide the console (log) window.

    Hiding removes its taskbar entry too - the tray icon is the only access
    point in that case. The tray menu's "Show console window" brings it back.
    """
    hwnd = get_console_window()
    if not hwnd:
        return
    try:
        user32 = ctypes.windll.user32
        user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
        user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
        # SW_SHOW = 5, SW_HIDE = 0
        user32.ShowWindow(hwnd, 5 if visible else 0)
        if visible:
            user32.SetForegroundWindow(hwnd)
    except Exception:
        pass


def set_window_icon():
    """Give the console window - and its taskbar entry - the custom app icon."""
    hwnd = get_console_window()
    if not hwnd:
        return
    for name in ("app_icon.ico", "tray_icon.ico"):
        path = os.path.join(SCRIPT_DIR, name)
        if not os.path.isfile(path):
            continue
        try:
            user32 = ctypes.windll.user32
            user32.LoadImageW.restype = ctypes.c_void_p
            user32.LoadImageW.argtypes = [
                ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint,
                ctypes.c_int, ctypes.c_int, ctypes.c_uint]
            user32.SendMessageW.argtypes = [
                ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
            # IMAGE_ICON = 1, LR_LOADFROMFILE = 0x10, LR_DEFAULTSIZE = 0x40
            hicon = user32.LoadImageW(None, path, 1, 0, 0, 0x10 | 0x40)
            if hicon:
                # WM_SETICON = 0x80, ICON_SMALL = 0, ICON_BIG = 1
                user32.SendMessageW(hwnd, 0x80, 0, hicon)
                user32.SendMessageW(hwnd, 0x80, 1, hicon)
                log("Window/taskbar icon set from " + name + ".")
            return
        except Exception:
            pass


def ensure_single_instance():
    """Exit immediately if another copy of this script is already running."""
    try:
        kernel32 = ctypes.windll.kernel32
        # Create a named mutex. If it already exists, another copy owns it.
        kernel32.CreateMutexW(None, False, "SC_AutoAccept_SingleInstance")
        if kernel32.GetLastError() == 183:        # ERROR_ALREADY_EXISTS
            try:
                ctypes.windll.user32.MessageBoxW(
                    None,
                    "Star Citizen Auto-Accept is already running.\n\n"
                    "Look for its icon in the system tray.",
                    "Already Running",
                    0x30)                          # MB_ICONWARNING
            except Exception:
                pass
            sys.exit(0)
    except SystemExit:
        raise
    except Exception:
        pass   # never let the single-instance check block a real launch


def stamp():
    return datetime.datetime.now().strftime("%H:%M:%S")


def log(msg):
    print("[" + stamp() + "] " + msg, flush=True)


def banner(lines):
    width = max(len(l) for l in lines) + 4
    bar = "=" * width
    print(bar, flush=True)
    for l in lines:
        print("  " + l, flush=True)
    print(bar, flush=True)


def fail(msg, hint=""):
    print("", flush=True)
    log("ERROR: " + msg)
    if hint:
        print(hint, flush=True)
    print("", flush=True)
    input("Press Enter to close this window...")
    sys.exit(1)


# --- Import the third-party libraries, with friendly errors if missing -------
try:
    import mss
except ImportError:
    fail("The 'mss' library is not installed.",
         "Install it with:  pip install mss")

try:
    from PIL import Image, ImageOps, ImageDraw
except ImportError:
    fail("The 'Pillow' library is not installed.",
         "Install it with:  pip install pillow")

try:
    import pytesseract
except ImportError:
    fail("The 'pytesseract' library is not installed.",
         "Install it with:  pip install pytesseract")

try:
    import pydirectinput
except ImportError:
    fail("The 'pydirectinput' library is not installed.",
         "Install it with:  pip install pydirectinput")

try:
    import keyboard
except ImportError:
    fail("The 'keyboard' library is not installed.",
         "Install it with:  pip install keyboard")

# pystray is OPTIONAL - the script runs fine without it, just no tray icon.
try:
    import pystray
    HAVE_PYSTRAY = True
except ImportError:
    HAVE_PYSTRAY = False


# pydirectinput safety: don't abort if the mouse hits a screen corner.
pydirectinput.FAILSAFE = False
pydirectinput.PAUSE = 0.0


def load_settings():
    """Load settings.json, falling back to defaults for anything missing."""
    data = dict(DEFAULT_SETTINGS)
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as fh:
            saved = json.load(fh)
        if isinstance(saved, dict):
            for key in DEFAULT_SETTINGS:
                if key in saved:
                    data[key] = saved[key]
    except FileNotFoundError:
        pass
    except Exception as e:
        log("Could not read settings.json (" + str(e) + ") - using defaults.")
    return data


def save_settings(settings):
    """Write the current settings to settings.json."""
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as fh:
            json.dump({k: settings[k] for k in DEFAULT_SETTINGS}, fh, indent=2)
    except Exception as e:
        log("Could not save settings.json: " + str(e))


def show_settings_window(settings, state=None):
    """Show the settings window (live auto-save).

    Every change writes settings.json immediately. If `state` is given (window
    opened from the tray while the watcher is running) the footer shows live
    Running / Paused status and a Pause/Resume button. If `state` is None
    (startup) the button reads "Start" and closes the window so the watcher
    can begin.

    Built with plain tkinter + Pillow (both already required). Rounded cards,
    toggle switches and buttons are drawn with Pillow at 4x and scaled down
    so the edges stay smooth.

    Modifies `settings` in place; also returned for convenience.
    """
    try:
        import tkinter as tk
        import tkinter.font as tkfont
        from PIL import ImageTk
    except Exception:
        log("tkinter / Pillow ImageTk not available - skipping the settings window.")
        return settings

    # Crisp rendering on high-DPI screens (mss does the same when it starts).
    if sys.platform == "win32":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass

    root = tk.Tk()
    root.withdraw()
    root.title("Star Citizen Auto-Accept")
    root.resizable(False, False)
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass
    try:
        _icon_path = os.path.join(SCRIPT_DIR, "app_icon.ico")
        if os.path.isfile(_icon_path):
            root.iconbitmap(_icon_path)
    except Exception:
        pass

    SCALE = max(1.0, root.winfo_fpixels("1i") / 96.0)

    def px(v):
        return int(round(v * SCALE))

    _fams = set(tkfont.families(root))

    def _pick_font(*names):
        for n in names:
            if n in _fams:
                return n
        return "Segoe UI"

    FONT = _pick_font("Segoe UI Variable Text", "Segoe UI")
    FONT_DISPLAY = _pick_font("Segoe UI Variable Display", "Segoe UI")
    F_TITLE = (FONT_DISPLAY, 20, "bold")
    F_SUB = (FONT, 11)
    F_HEAD = (FONT, 12, "bold")
    F_ROW = (FONT, 11)
    F_DESC = (FONT, 10)
    F_BTN = (FONT, 11)
    F_STATUS = (FONT, 11, "bold")

    # --- Theming --------------------------------------------------------
    THEMES = {
        "dark": {
            "win": "#0f131a", "card": "#161b23", "card_border": "#262d38",
            "sep": "#232a35", "fg": "#e9ecf1", "hint": "#9aa5b4",
            "field_bg": "#12161d", "field_border": "#3a414d",
            "field_hover": "#59616f", "accent": "#2f7bf5",
            "accent_hover": "#4a8dff", "track_off": "#3b414c",
            "knob": "#ffffff", "chevron": "#c5ccd6", "btn_fg": "#ffffff",
            "run": "#3ad35f", "paused": "#f0a43a", "idle": "#9aa5b4",
            "menu_bg": "#1c222c",
        },
        "light": {
            "win": "#eef1f5", "card": "#ffffff", "card_border": "#dde2e9",
            "sep": "#e8ebf0", "fg": "#14181f", "hint": "#5d6878",
            "field_bg": "#ffffff", "field_border": "#c9d0da",
            "field_hover": "#9aa5b4", "accent": "#2563eb",
            "accent_hover": "#3b76f0", "track_off": "#c4cbd5",
            "knob": "#ffffff", "chevron": "#4b5563", "btn_fg": "#ffffff",
            "run": "#16a34a", "paused": "#c2700a", "idle": "#5d6878",
            "menu_bg": "#ffffff",
        },
    }

    def T():
        return THEMES["dark" if settings.get("dark_mode", True) else "light"]

    # --- Pillow-drawn shapes (supersampled for smooth edges) -------------
    SS = 4
    _img_cache = {}

    def _canvas(w, h, bg):
        img = Image.new("RGB", (w * SS, h * SS), bg)
        return img, ImageDraw.Draw(img)

    def _finish(img, w, h, key):
        photo = ImageTk.PhotoImage(img.resize((w, h), Image.LANCZOS),
                                   master=root)
        _img_cache[key] = photo
        return photo

    def rrect(w, h, r, fill, bg, outline=None, ow=1.0):
        """Rounded rectangle. w/h in real pixels, r and ow in logical px."""
        key = ("rr", w, h, r, fill, bg, outline, ow)
        if key in _img_cache:
            return _img_cache[key]
        img, d = _canvas(w, h, bg)
        kw = {"fill": fill}
        if outline:
            kw["outline"] = outline
            kw["width"] = max(SS, int(round(ow * SCALE * SS)))
        d.rounded_rectangle((0, 0, w * SS - 1, h * SS - 1),
                            radius=int(px(r) * SS), **kw)
        return _finish(img, w, h, key)

    def toggle_img(on):
        t = T()
        w, h = px(46), px(26)
        key = ("tg", on, w, h, t["card"], t["accent"], t["track_off"])
        if key in _img_cache:
            return _img_cache[key]
        img, d = _canvas(w, h, t["card"])
        d.rounded_rectangle((0, 0, w * SS - 1, h * SS - 1),
                            radius=h * SS // 2,
                            fill=t["accent"] if on else t["track_off"])
        m = px(3) * SS
        dia = h * SS - 2 * m
        x0 = (w * SS - m - dia) if on else m
        d.ellipse((x0, m, x0 + dia, m + dia), fill=t["knob"])
        return _finish(img, w, h, key)

    def dot_img(color, bg):
        n = px(18)
        key = ("dot", n, color, bg)
        if key in _img_cache:
            return _img_cache[key]
        img, d = _canvas(n, n, bg)
        d.ellipse((0, 0, n * SS - 1, n * SS - 1), fill=color)
        return _finish(img, n, n, key)

    def chevron_img(direction, color, bg, box=16, thick=1.7):
        n = px(box)
        key = ("chev", direction, n, color, bg, thick)
        if key in _img_cache:
            return _img_cache[key]
        img, d = _canvas(n, n, bg)
        S = n * SS
        a, b, mid = S * 0.22, S * 0.78, S * 0.5
        q, r = S * 0.36, S * 0.64
        pts = {"down": [(a, q), (mid, r), (b, q)],
               "up": [(a, r), (mid, q), (b, r)],
               "right": [(q, a), (r, mid), (q, b)]}[direction]
        lw = max(SS, int(thick * SCALE * SS))
        d.line(pts, fill=color, width=lw, joint="curve")
        for (x, y) in (pts[0], pts[-1]):          # round line caps
            d.ellipse((x - lw / 2, y - lw / 2, x + lw / 2, y + lw / 2),
                      fill=color)
        return _finish(img, n, n, key)

    def autosave():
        save_settings(settings)

    # --- Setting variables (created once; survive theme rebuilds) -------
    _sync = {"on": False}       # True while WE mirror state into a variable

    def bool_var(key, default=False, on_change=None):
        v = tk.BooleanVar(master=root, value=bool(settings.get(key, default)))

        def _cb(*_):
            if _sync["on"]:
                return
            new_val = bool(v.get())
            settings[key] = new_val
            autosave()
            if on_change:
                on_change(new_val)

        v.trace_add("write", _cb)
        return v

    def _away_changed(new_val):
        if state is not None:
            state.away_mode = new_val
            if new_val:
                state.last_away_press = time.time()
            log("Away Mode " + ("ON" if new_val else "OFF") + " (via settings).")

    def _hide_changed(new_val):
        if state is not None:
            state.console_visible = not new_val
            set_console_visible(state.console_visible)
            log("Console " + ("hidden" if new_val else "shown")
                + " (via settings).")

    contracts_var = bool_var("accept_contracts", True)
    party_var = bool_var("accept_party_invites", True)
    friend_var = bool_var("accept_friend_requests", False)
    away_var = bool_var("away_mode", False, _away_changed)
    hide_var = bool_var("hide_console", False, _hide_changed)
    verbose_var = bool_var("verbose", False)
    dark_var = bool_var("dark_mode", True,
                        lambda _v: root.after_idle(build))

    # Monitor list
    try:
        with mss.mss() as _sct_q:
            _real_mons = _sct_q.monitors[1:]
    except Exception:
        _real_mons = []
    _mon_labels = ["Auto (middle)"] + [
        "Monitor " + str(i + 1) + "  ("
        + str(m["width"]) + "x" + str(m["height"]) + ")"
        for i, m in enumerate(_real_mons)
    ]
    _current_mon = settings.get("monitor_index", "middle")
    _current_label = _mon_labels[0]
    if _current_mon != "middle":
        try:
            _idx = int(_current_mon)
            if 0 < _idx < len(_mon_labels):
                _current_label = _mon_labels[_idx]
        except (ValueError, TypeError):
            pass
    monitor_var = tk.StringVar(master=root, value=_current_label)

    def _on_monitor_change(*_):
        lbl = monitor_var.get()
        if lbl.startswith("Auto"):
            settings["monitor_index"] = "middle"
        else:
            try:
                settings["monitor_index"] = int(lbl.split()[1])
            except (ValueError, IndexError):
                settings["monitor_index"] = "middle"
        autosave()

    monitor_var.trace_add("write", _on_monitor_change)

    SCAN_MIN, SCAN_MAX, SCAN_STEP = 0.5, 5.0, 0.5
    ui = {}
    _ui_state = {"advanced_open": True}

    def set_scan(v):
        v = min(SCAN_MAX, max(SCAN_MIN, v))
        v = round(round(v / SCAN_STEP) * SCAN_STEP, 2)
        settings["scan_interval"] = v
        autosave()
        if "scan_redraw" in ui:
            ui["scan_redraw"]()

    def cur_scan():
        try:
            return float(settings.get("scan_interval", 1.5))
        except (ValueError, TypeError):
            return 1.5

    MOD_KEYS = {"Control_L": "ctrl", "Control_R": "ctrl",
                "Shift_L": "shift", "Shift_R": "shift",
                "Alt_L": "alt", "Alt_R": "alt"}

    def pretty_combo(combo):
        out = []
        for p in [p for p in str(combo).split("+") if p]:
            if len(p) == 1 or (p[0] in "fF" and p[1:].isdigit()):
                out.append(p.upper())
            else:
                out.append(p[:1].upper() + p[1:])
        return " + ".join(out)

    # --- Widgets ----------------------------------------------------------
    _hotkey_canvases = []

    class Card:
        """Rounded card: a canvas with a Pillow background and a Frame on top."""
        PADX, PADT, PADB = 18, 11, 4

        def __init__(self, parent, title, collapsible=False):
            t = T()
            self.t = t
            self.canvas = tk.Canvas(parent, width=ui["card_w"], height=px(40),
                                    bg=t["win"], highlightthickness=0, bd=0)
            self.canvas.pack(fill="x", pady=(0, px(8)))
            self.bg_item = self.canvas.create_image(0, 0, anchor="nw")
            self.inner = tk.Frame(self.canvas, bg=t["card"])
            self.win = self.canvas.create_window(px(self.PADX), px(self.PADT),
                                                 anchor="nw", window=self.inner)
            self._last = None
            self.rows = 0
            ui.setdefault("cards", []).append(self)

            head = tk.Frame(self.inner, bg=t["card"])
            head.pack(fill="x", pady=(0, px(2)))
            title_lbl = tk.Label(head, text=title, font=F_HEAD, bg=t["card"],
                                 fg=t["fg"], anchor="w")
            title_lbl.pack(side="left")
            self.body = tk.Frame(self.inner, bg=t["card"])
            self.body.pack(fill="x")

            if collapsible:
                chev = tk.Label(head, bg=t["card"], cursor="hand2")
                chev.pack(side="right")

                def paint():
                    chev.configure(image=chevron_img(
                        "down" if _ui_state["advanced_open"] else "right",
                        t["chevron"], t["card"]))
                    if _ui_state["advanced_open"]:
                        self.body.pack(fill="x")
                    else:
                        self.body.pack_forget()

                def flip(_e=None):
                    _ui_state["advanced_open"] = not _ui_state["advanced_open"]
                    paint()

                for w in (head, title_lbl, chev):
                    w.bind("<Button-1>", flip)
                title_lbl.configure(cursor="hand2")
                paint()

            self.canvas.bind("<Configure>", self._layout)
            self.inner.bind("<Configure>", self._layout)

        def _layout(self, _e=None):
            cw = self.canvas.winfo_width()
            if cw <= 1:
                cw = ui["card_w"]
            self.canvas.itemconfigure(self.win, width=cw - 2 * px(self.PADX))
            ch = self.inner.winfo_reqheight() + px(self.PADT) + px(self.PADB)
            if int(float(self.canvas.cget("height"))) != ch:
                self.canvas.configure(height=ch)
            if self._last != (cw, ch):
                self._last = (cw, ch)
                self.canvas.itemconfigure(self.bg_item, image=rrect(
                    cw, ch, 12, self.t["card"], self.t["win"],
                    self.t["card_border"], 1))

        def row(self, label, desc=None):
            t = self.t
            if self.rows:
                tk.Frame(self.body, bg=t["sep"], height=max(1, px(1))).pack(fill="x")
            self.rows += 1
            r = tk.Frame(self.body, bg=t["card"])
            r.pack(fill="x", pady=px(6))
            lbl = tk.Label(r, text=label, font=F_ROW, bg=t["card"], fg=t["fg"])
            lbl.pack(side="left")
            if desc:
                tk.Frame(r, bg=t["field_border"], width=max(1, px(1)),
                         height=px(24)).pack(side="left", padx=px(14))
                tk.Label(r, text=desc, font=F_DESC, bg=t["card"],
                         fg=t["hint"]).pack(side="left")
            r.label = lbl
            return r

        def toggle_row(self, label, var, desc=None):
            r = self.row(label, desc)
            make_toggle(r, var).pack(side="right")
            r.label.bind("<Button-1>", lambda e: var.set(not bool(var.get())))
            r.label.configure(cursor="hand2")
            return r

        def note(self, text):
            tk.Label(self.body, text=text, font=F_DESC, bg=self.t["card"],
                     fg=self.t["hint"], anchor="w").pack(fill="x", pady=(0, px(8)))

    def make_toggle(parent, var):
        t = T()
        lbl = tk.Label(parent, bg=t["card"], bd=0, cursor="hand2")

        def draw(*_):
            try:
                lbl.configure(image=toggle_img(bool(var.get())))
            except Exception:
                pass

        lbl.bind("<Button-1>", lambda e: var.set(not bool(var.get())))
        tid = var.trace_add("write", draw)

        def _cleanup(_e):
            try:
                var.trace_remove("write", tid)
            except Exception:
                pass

        lbl.bind("<Destroy>", _cleanup)
        draw()
        return lbl

    def field(parent, w, h, cursor="hand2", takefocus=0):
        t = T()
        c = tk.Canvas(parent, width=w, height=h, bg=t["card"], bd=0,
                      highlightthickness=0, cursor=cursor, takefocus=takefocus)
        bg = c.create_image(0, 0, anchor="nw")

        def border(color, ow=1.0):
            c.itemconfigure(bg, image=rrect(w, h, 6, t["field_bg"], t["card"],
                                            color, ow))

        border(t["field_border"])
        return c, border

    def make_dropdown(parent):
        t = T()
        w, h = px(210), px(36)
        c, border = field(parent, w, h)
        txt = c.create_text(px(14), h // 2, anchor="w", font=F_ROW,
                            fill=t["fg"], text=monitor_var.get())
        c.create_image(w - px(22), h // 2,
                       image=chevron_img("down", t["chevron"], t["field_bg"]))
        menu = tk.Menu(c, tearoff=0, bg=t["menu_bg"], fg=t["fg"],
                       activebackground=t["accent"], activeforeground="#ffffff",
                       selectcolor=t["fg"], bd=0, relief="flat", font=F_ROW)
        for lbl in _mon_labels:
            menu.add_radiobutton(
                label=lbl, variable=monitor_var, value=lbl,
                command=lambda: c.itemconfigure(txt, text=monitor_var.get()))
        c.bind("<Enter>", lambda e: border(t["field_hover"]))
        c.bind("<Leave>", lambda e: border(t["field_border"]))
        c.bind("<Button-1>", lambda e: menu.tk_popup(
            c.winfo_rootx(), c.winfo_rooty() + h + px(2)))
        return c

    def make_spinner(parent):
        t = T()
        w, h = px(100), px(36)
        split = w - px(30)
        c, border = field(parent, w, h, cursor="arrow")
        txt = c.create_text(px(14), h // 2, anchor="w", font=F_ROW, fill=t["fg"])
        c.create_line(split, px(7), split, h - px(7), fill=t["field_border"])
        cx = split + (w - split) // 2
        c.create_image(cx, h // 2 - px(7),
                       image=chevron_img("up", t["chevron"], t["field_bg"], 13))
        c.create_image(cx, h // 2 + px(7),
                       image=chevron_img("down", t["chevron"], t["field_bg"], 13))

        def redraw():
            c.itemconfigure(txt, text="%.1f" % cur_scan())

        def click(e):
            if e.x >= split:
                set_scan(cur_scan() + (SCAN_STEP if e.y < h / 2 else -SCAN_STEP))

        def motion(e):
            c.configure(cursor="hand2" if e.x >= split else "arrow")

        c.bind("<Button-1>", click)
        c.bind("<Motion>", motion)
        c.bind("<MouseWheel>", lambda e: set_scan(
            cur_scan() + (SCAN_STEP if e.delta > 0 else -SCAN_STEP)))
        c.bind("<Button-4>", lambda e: set_scan(cur_scan() + SCAN_STEP))
        c.bind("<Button-5>", lambda e: set_scan(cur_scan() - SCAN_STEP))
        c.bind("<Enter>", lambda e: border(t["field_hover"]))
        c.bind("<Leave>", lambda e: border(t["field_border"]))
        ui["scan_redraw"] = redraw
        redraw()
        return c

    def make_hotkey(parent, key_name):
        """Click-to-record hotkey box. Saves as soon as a full combo is pressed."""
        t = T()
        w, h = px(240), px(38)
        c, border = field(parent, w, h, takefocus=1)
        txt = c.create_text(px(16), h // 2, anchor="w", font=F_ROW)
        hk = {"value": settings[key_name], "mods": []}

        def paint(text, active):
            border(t["accent"] if active else t["field_border"],
                   2 if active else 1)
            c.itemconfigure(txt, text=text,
                            fill=t["hint"] if active else t["fg"])

        def order():
            return [m for m in ("ctrl", "alt", "shift") if m in hk["mods"]]

        def on_click(_e):
            c.focus_set()
            return "break"

        def on_focus_in(_e):
            hk["mods"] = []
            paint("Press keys…", True)

        def on_key_press(e):
            ks = e.keysym
            if ks == "Escape":
                root.focus_set()
            elif ks in MOD_KEYS:
                if MOD_KEYS[ks] not in hk["mods"]:
                    hk["mods"].append(MOD_KEYS[ks])
                paint(pretty_combo("+".join(order())) + " + …", True)
            else:
                combo = "+".join(order() + [ks.lower()])
                hk["value"] = combo
                settings[key_name] = combo
                autosave()
                root.focus_set()
            return "break"

        def on_key_release(e):
            ks = e.keysym
            if ks in MOD_KEYS and MOD_KEYS[ks] in hk["mods"]:
                hk["mods"].remove(MOD_KEYS[ks])

        def on_focus_out(_e):
            paint(pretty_combo(hk["value"]), False)

        c.bind("<Button-1>", on_click)
        c.bind("<FocusIn>", on_focus_in)
        c.bind("<FocusOut>", on_focus_out)
        c.bind("<KeyPress>", on_key_press)
        c.bind("<KeyRelease>", on_key_release)
        _hotkey_canvases.append(c)
        paint(pretty_combo(hk["value"]), False)
        return c

    def make_button(parent, command):
        t = T()
        w, h = px(190), px(44)
        c = tk.Canvas(parent, width=w, height=h, bg=t["win"], bd=0,
                      highlightthickness=0, cursor="hand2")
        bg = c.create_image(0, 0, anchor="nw")
        txt = c.create_text(w // 2, h // 2, font=F_BTN, fill=t["btn_fg"])

        def paint(hover):
            c.itemconfigure(bg, image=rrect(
                w, h, 7, t["accent_hover"] if hover else t["accent"], t["win"]))

        c.bind("<Enter>", lambda e: paint(True))
        c.bind("<Leave>", lambda e: paint(False))
        c.bind("<Button-1>", lambda e: command())
        paint(False)
        return c, (lambda text: c.itemconfigure(txt, text=text))

    # Clicking anywhere outside a hotkey box ends recording.
    def _blur(e):
        if e.widget not in _hotkey_canvases:
            try:
                root.focus_set()
            except Exception:
                pass

    root.bind_all("<Button-1>", _blur, add="+")

    # --- Windows title bar colour ----------------------------------------
    def style_titlebar():
        if sys.platform != "win32":
            return
        try:
            root.update_idletasks()
            try:
                hwnd = int(root.wm_frame(), 16)
            except Exception:
                hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
            t = T()
            dwm = ctypes.windll.dwmapi
            dark = ctypes.c_int(1 if settings.get("dark_mode", True) else 0)
            if dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(dark), 4) != 0:
                dwm.DwmSetWindowAttribute(hwnd, 19, ctypes.byref(dark), 4)

            def colorref(hexstr):
                h = hexstr.lstrip("#")
                r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
                return ctypes.c_int(r | (g << 8) | (b << 16))

            # Windows 11 only; silently ignored on Windows 10.
            dwm.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(colorref(t["win"])), 4)
            dwm.DwmSetWindowAttribute(hwnd, 36, ctypes.byref(colorref(t["fg"])), 4)
            # SWP_NOSIZE|NOMOVE|NOZORDER|NOACTIVATE|FRAMECHANGED -> repaint frame
            ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0,
                                              0x1 | 0x2 | 0x4 | 0x10 | 0x20)
        except Exception:
            pass

    # --- Status + action ---------------------------------------------------
    def refresh_status():
        if "status" not in ui:
            return
        t = T()
        if state is None:
            color, text, btn = t["idle"], "Not started", "Start"
        elif getattr(state, "paused", False):
            color, text, btn = t["paused"], "Paused", "Resume scanning"
        else:
            color, text, btn = t["run"], "Running", "Pause scanning"
        ui["dot"].configure(image=dot_img(color, t["win"]))
        ui["status"].configure(text=text, fg=color)
        ui["btn_text"](btn)
        # Mirror changes made elsewhere (hotkey / tray) into the toggles.
        if state is not None:
            _sync["on"] = True
            try:
                if bool(state.away_mode) != bool(away_var.get()):
                    away_var.set(bool(state.away_mode))
                want_hidden = not state.console_visible
                if want_hidden != bool(hide_var.get()):
                    hide_var.set(want_hidden)
            finally:
                _sync["on"] = False

    def on_action():
        if state is None:
            root.destroy()                 # startup: closing -> start watcher
        else:
            state.paused = not state.paused
            log("PAUSED (via settings window)." if state.paused
                else "RESUMED (via settings window).")
            refresh_status()

    # --- Build (re-run on theme change) -----------------------------------
    away_desc = ("Taps " + AWAY_KEY.capitalize() + " "
                 + ("twice" if AWAY_PRESS_COUNT == 2
                    else str(AWAY_PRESS_COUNT) + " times")
                 + " every " + str(AWAY_INTERVAL_SECONDS // 60) + " minutes")

    def build():
        t = T()
        old = ui.get("container")
        if old is not None:
            old.destroy()
        ui.clear()
        _img_cache.clear()
        del _hotkey_canvases[:]
        ui["card_w"] = px(520)

        root.configure(bg=t["win"])
        cont = tk.Frame(root, bg=t["win"])
        cont.pack(fill="both", expand=True)
        ui["container"] = cont

        body = tk.Frame(cont, bg=t["win"])
        body.pack(fill="x", padx=px(14), pady=(px(14), px(4)))
        tk.Label(body, text="Settings", font=F_TITLE, bg=t["win"], fg=t["fg"],
                 anchor="w").pack(fill="x", padx=px(4))
        tk.Label(body, text="Manage invites, scanning and shortcuts.",
                 font=F_SUB, bg=t["win"], fg=t["hint"], anchor="w").pack(
                     fill="x", padx=px(4), pady=(0, px(10)))

        c = Card(body, "Auto-accept")
        c.toggle_row("Contracts", contracts_var, "Contract Shared popups")
        c.toggle_row("Party invites", party_var)
        c.toggle_row("Friend requests", friend_var)

        c = Card(body, "Scanning")
        r = c.row("Monitor")
        make_dropdown(r).pack(side="right")
        r = c.row("Scan interval")
        holder = tk.Frame(r, bg=t["card"], width=px(210), height=px(36))
        holder.pack_propagate(False)
        holder.pack(side="right")
        make_spinner(holder).pack(side="left")
        tk.Label(holder, text="s", font=F_ROW, bg=t["card"],
                 fg=t["fg"]).pack(side="left", padx=px(12))
        c.toggle_row("Away Mode", away_var, away_desc)

        c = Card(body, "Hotkeys")
        r = c.row("Away Mode")
        make_hotkey(r, "away_hotkey").pack(side="right")
        r = c.row("Pause scanning")
        make_hotkey(r, "pause_hotkey").pack(side="right")
        c.note("Click a shortcut to record a new combination.")

        c = Card(body, "Appearance")
        c.toggle_row("Dark mode", dark_var)

        c = Card(body, "Advanced", collapsible=True)
        c.toggle_row("Hide console", hide_var,
                     "Run in the system tray after starting")
        c.toggle_row("Detailed logging", verbose_var)

        # Footer
        tk.Frame(cont, bg=t["card_border"], height=max(1, px(1))).pack(fill="x")
        foot = tk.Frame(cont, bg=t["win"])
        foot.pack(fill="x", padx=px(18), pady=px(10))
        dot = tk.Label(foot, bg=t["win"])
        dot.pack(side="left")
        st = tk.Label(foot, font=F_STATUS, bg=t["win"])
        st.pack(side="left", padx=(px(10), 0))
        tk.Label(foot, text="v" + VERSION, font=F_DESC, bg=t["win"],
                 fg=t["hint"]).pack(side="right")
        btn, set_text = make_button(foot, on_action)
        btn.pack(side="right", padx=(0, px(22)))
        ui.update(dot=dot, status=st, btn_text=set_text)

        # Size every card now (not on the first <Configure>) so the window
        # has its final height before it is centred and shown.
        root.update_idletasks()
        for card in ui.get("cards", []):
            card._layout()
        root.update_idletasks()

        style_titlebar()
        refresh_status()

    build()

    # Centre on screen, then show.
    root.update_idletasks()
    w, h = root.winfo_reqwidth(), root.winfo_reqheight()
    x = max(0, (root.winfo_screenwidth() - w) // 2)
    y = max(0, (root.winfo_screenheight() - h) // 2 - px(20))
    root.geometry("+%d+%d" % (x, y))
    root.deiconify()
    root.lift()
    style_titlebar()

    # While running, poll state so changes from the tray / hotkeys show here.
    if state is not None:
        def poll():
            try:
                refresh_status()
                root.after(500, poll)
            except Exception:
                pass
        root.after(500, poll)

    root.protocol("WM_DELETE_WINDOW", root.destroy)
    try:
        root.focus_set()
    except Exception:
        pass
    root.mainloop()
    return settings


class State:
    """Shared flags. Hotkey/tray callbacks write them; the main loop reads them."""
    def __init__(self):
        self.paused = False
        self.away_mode = AWAY_MODE_AT_STARTUP
        self.quit = False
        self.last_accept = 0.0       # time of last accept (used for cooldown)
        self.last_away_press = time.time()
        self.want_snapshot = False   # set by the debug hotkey / tray
        self.want_settings = False   # set by the tray "Settings" item
        self.settings_open = False   # True while the settings window is up
        self.console_visible = True  # whether the log window is shown


def configure_tesseract():
    """Find the Tesseract executable, point pytesseract at it, confirm it runs."""
    local = os.environ.get("LOCALAPPDATA", "")
    candidates = [
        TESSERACT_PATH,
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        os.path.join(local, "Tesseract-OCR", "tesseract.exe"),
        os.path.join(local, "Programs", "Tesseract-OCR", "tesseract.exe"),
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            pytesseract.pytesseract.tesseract_cmd = path
            try:
                pytesseract.get_tesseract_version()
                log("Using Tesseract at: " + path)
                return
            except Exception:
                continue
    try:
        pytesseract.get_tesseract_version()
        log("Using Tesseract from the system PATH.")
        return
    except Exception:
        pass
    fail(
        "Tesseract OCR is not installed (or could not be found).",
        "1. Download the installer from:\n"
        "   https://github.com/UB-Mannheim/tesseract/wiki\n"
        "   (pick the file named like 'tesseract-ocr-w64-setup-5.x.x.exe')\n"
        "2. Run it and click through with the default settings.\n"
        "3. Re-run this script - it will find Tesseract automatically.\n"
        "   If you installed it to an unusual folder, set TESSERACT_PATH\n"
        "   near the top of this script to the full path of tesseract.exe.",
    )


def choose_monitor(sct, setting=None):
    """Return the monitor dict to capture from."""
    monitors = sct.monitors
    real = monitors[1:]

    print("", flush=True)
    log("Detected " + str(len(real)) + " monitor(s):")
    for i, m in enumerate(real, start=1):
        print("    [" + str(i) + "]  " + str(m["width"]) + "x" + str(m["height"])
              + "  at position (left=" + str(m["left"]) + ", top="
              + str(m["top"]) + ")", flush=True)
    print("", flush=True)

    if setting is None:
        setting = MONITOR_INDEX

    if isinstance(setting, str) and setting.lower() == "middle":
        order = sorted(range(len(real)), key=lambda k: real[k]["left"])
        middle = order[len(order) // 2]
        index = middle + 1
        log("Auto-selected the middle monitor (capture id #" + str(index) + ", "
            + str(real[middle]["width"]) + "x" + str(real[middle]["height"]) + ").")
        return monitors[index]

    index = setting
    if index is None:
        try:
            raw = input("Which monitor number do you play on?  ").strip()
            index = int(raw)
        except (ValueError, EOFError):
            fail("That was not a valid monitor number.")

    if not isinstance(index, int) or index < 1 or index > len(real):
        fail("Monitor " + str(index) + " does not exist. Pick 1 to "
             + str(len(real)) + ".")

    log("Using monitor " + str(index) + ".")
    return monitors[index]


def make_region(monitor):
    """Compute the top-center capture strip for the given monitor."""
    width  = int(monitor["width"]  * REGION_WIDTH_FRACTION)
    height = int(monitor["height"] * REGION_HEIGHT_FRACTION)
    left   = monitor["left"] + (monitor["width"] - width) // 2
    top    = monitor["top"]
    return {"left": left, "top": top, "width": width, "height": height}


def preprocess_variants(image):
    """Return cleaned-up versions of the strip for OCR.

    Variant A keeps only bright pixels (a hard threshold), so a busy
    background like an asteroid field drops to black and the text stands
    alone. Variant B is a softer contrast stretch for dark, calm backgrounds.
    """
    gray = ImageOps.grayscale(image)
    gray = gray.resize((gray.width * 2, gray.height * 2), Image.LANCZOS)
    threshold = gray.point(lambda p: 0 if p >= OCR_BRIGHT_THRESHOLD else 255)
    stretched = ImageOps.autocontrast(gray)
    return [threshold, stretched]


def _phrase_fuzzy_match(words, targets, cutoff):
    """True if `targets` appear as CONSECUTIVE words in `words`, in order."""
    n = len(targets)
    for i in range(len(words) - n + 1):
        if all(difflib.SequenceMatcher(None, words[i + j], targets[j]).ratio()
               >= cutoff for j in range(n)):
            return True
    return False


def detect_popup(ocr_text, settings):
    """Return a label for an accept-worthy popup, or None.

    Each popup type is only checked if it is enabled in settings.
    """
    lowered = ocr_text.lower()
    collapsed = "".join(ch if (ch.isalnum() or ch.isspace()) else " "
                        for ch in lowered)
    words = collapsed.split()

    if settings.get("accept_contracts", True):
        if _phrase_fuzzy_match(words, CONTRACT_WORDS, FUZZY_CUTOFF):
            return "Contract share"
    if settings.get("accept_party_invites", False):
        if _phrase_fuzzy_match(words, PARTY_WORDS, FUZZY_CUTOFF):
            return "Party invite"
    if settings.get("accept_friend_requests", False):
        if _phrase_fuzzy_match(words, FRIEND_WORDS, FUZZY_CUTOFF):
            return "Friend request"
    return None


def tap_key(key, hold=KEY_HOLD_SECONDS):
    """Send a single keypress using DirectInput scancodes (works inside games)."""
    pydirectinput.keyDown(key)
    time.sleep(hold)
    pydirectinput.keyUp(key)


def interruptible_sleep(duration, state):
    """Sleep, but bail out early if the user asked to quit."""
    end = time.time() + duration
    while time.time() < end:
        if state.quit:
            return
        time.sleep(min(0.1, end - time.time()))


def register_hotkeys(state, settings):
    """Wire up the hotkeys to callbacks (event-driven, never missed)."""

    def toggle_pause():
        state.paused = not state.paused
        log("PAUSED - not scanning." if state.paused
            else "RESUMED - scanning again.")

    def toggle_away():
        state.away_mode = not state.away_mode
        if state.away_mode:
            state.last_away_press = time.time()
            log("Away Mode: ON  (will tap '" + AWAY_KEY + "' every "
                + str(AWAY_INTERVAL_SECONDS // 60) + " min)")
        else:
            log("Away Mode: OFF")
        settings["away_mode"] = state.away_mode
        save_settings(settings)

    def request_quit():
        state.quit = True
        log("Quit hotkey pressed. Shutting down...")

    def request_snapshot():
        state.want_snapshot = True
        log("Debug snapshot requested - saving on the next scan...")

    def safe_add(combo, callback, label, default):
        try:
            keyboard.add_hotkey(combo, callback)
        except Exception:
            log("Hotkey '" + str(combo) + "' for " + label
                + " is not valid - falling back to '" + default + "'.")
            try:
                keyboard.add_hotkey(default, callback)
            except Exception:
                pass

    safe_add(settings["pause_hotkey"], toggle_pause, "pause",
             DEFAULT_SETTINGS["pause_hotkey"])
    safe_add(settings["away_hotkey"], toggle_away, "away mode",
             DEFAULT_SETTINGS["away_hotkey"])
    keyboard.add_hotkey(QUIT_HOTKEY, request_quit)
    keyboard.add_hotkey(DEBUG_HOTKEY, request_snapshot)


def load_tray_image():
    """Load the tray icon from a file in the script folder, or draw a fallback."""
    for name in TRAY_ICON_NAMES:
        path = os.path.join(SCRIPT_DIR, name)
        if os.path.isfile(path):
            try:
                img = Image.open(path).convert("RGBA")
                log("Using tray icon: " + name)
                return img.resize((64, 64), Image.LANCZOS)
            except Exception as e:
                log("Could not load tray icon '" + name + "': " + str(e))
    # Fallback: draw a simple icon.
    img = Image.new("RGB", (64, 64), (16, 92, 56))
    draw = ImageDraw.Draw(img)
    draw.ellipse((18, 18, 46, 46), fill=(235, 235, 235))
    return img


def make_tray_icon(state, debug_dir, settings):
    """Build a system-tray icon with a right-click menu, or None if unavailable."""
    if not (ENABLE_TRAY_ICON and HAVE_PYSTRAY):
        return None

    img = load_tray_image()

    def on_settings(icon, item):
        state.want_settings = True
        log("Settings requested (via tray) - opening shortly...")

    def on_toggle_pause(icon, item):
        state.paused = not state.paused
        log("PAUSED (via tray)." if state.paused else "RESUMED (via tray).")

    def on_toggle_console(icon, item):
        state.console_visible = not state.console_visible
        set_console_visible(state.console_visible)
        if not state.console_visible:
            log("Console hidden (tray -> Show console window to bring it back).")
        else:
            log("Console window shown.")
        # Mirror the change into the saved setting so the next launch matches.
        settings["hide_console"] = not state.console_visible
        save_settings(settings)

    def on_snapshot(icon, item):
        state.want_snapshot = True
        log("Debug snapshot requested (via tray) - saving on the next scan...")

    def on_open_debug(icon, item):
        try:
            os.startfile(debug_dir)
        except Exception as e:
            log("Could not open the debug folder: " + str(e))

    def on_exit(icon, item):
        log("Exit selected from the tray. Shutting down...")
        state.quit = True
        icon.stop()

    menu = pystray.Menu(
        # default=True -> double-click on the tray icon opens Settings.
        pystray.MenuItem("Settings...", on_settings, default=True),
        pystray.MenuItem(
            lambda item: "Resume scanning" if state.paused else "Pause scanning",
            on_toggle_pause),
        pystray.MenuItem(
            lambda item: "Hide console window" if state.console_visible
                         else "Show console window",
            on_toggle_console),
        pystray.MenuItem("Save debug snapshot", on_snapshot),
        pystray.MenuItem("Open debug folder", on_open_debug),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Exit", on_exit),
    )
    return pystray.Icon("sc_auto_accept", img, "SC Auto-Accept", menu)


def do_accept(state, label):
    """Press the accept key ACCEPT_PRESS_COUNT times, spaced apart."""
    banner([label.upper() + " DETECTED -> pressing '" + ACCEPT_KEY + "'"])
    for i in range(ACCEPT_PRESS_COUNT):
        tap_key(ACCEPT_KEY)
        log("Sent ACCEPT key  (" + str(i + 1) + "/" + str(ACCEPT_PRESS_COUNT) + ")")
        if i < ACCEPT_PRESS_COUNT - 1:
            interruptible_sleep(ACCEPT_PRESS_GAP_SECONDS, state)
            if state.quit:
                return
    state.last_accept = time.time()
    log(label + " accepted. Watching for the next one.")


def describe_enabled(settings):
    """Human-readable summary of which popup types are being auto-accepted."""
    pairs = (("contracts", "accept_contracts"),
             ("party invites", "accept_party_invites"),
             ("friend requests", "accept_friend_requests"))
    on = [name for name, key in pairs if settings.get(key)]
    return ", ".join(on) if on else "nothing (all toggles are off)"


def open_settings(state, settings):
    """Show the settings window, then re-apply hotkeys. Runs on main thread."""
    log("Opening settings window...")
    show_settings_window(settings, state)
    keyboard.unhook_all()
    register_hotkeys(state, settings)
    log("Settings applied.  Hotkeys: " + settings["away_hotkey"].upper()
        + " = away, " + settings["pause_hotkey"].upper() + " = pause.")
    log("Auto-accepting: " + describe_enabled(settings))


def warn_if_windows_terminal():
    """Windows Terminal hosts the console differently from the legacy host.
    That breaks "Hide console" and replaces the script's taskbar icon with
    Windows Terminal's icon. We can't fix it from inside the script, so we
    detect it and explain how to switch to the legacy console host."""
    if os.environ.get("WT_SESSION") or os.environ.get("WT_PROFILE_ID"):
        print("", flush=True)
        banner(["WINDOWS TERMINAL DETECTED"])
        log("Windows Terminal is hosting this console. Two things to know:")
        log("  * The taskbar shows Windows Terminal's icon, NOT this app's.")
        log("  * The 'Hide console' option in Settings cannot hide the WT tab.")
        log("To get a proper taskbar icon and a working Hide option:")
        log("  Windows Settings -> Privacy & security -> For developers")
        log("  -> 'Terminal' -> change to 'Windows Console Host'.")
        log("Then close this window and re-run from run.bat.")
        print("", flush=True)


def main():
    ensure_single_instance()
    setup_console()
    set_window_icon()
    banner([
        "STAR CITIZEN  -  AUTO-ACCEPT   v" + VERSION,
        "",
        "Right-click the tray icon for Settings, Pause, and Exit.",
    ])
    print("", flush=True)

    warn_if_windows_terminal()

    configure_tesseract()
    log("Tesseract OCR found and working.")

    # Always open the settings window when the script starts.
    settings = load_settings()
    log("Opening the settings window...")
    show_settings_window(settings)

    state = State()
    state.away_mode = bool(settings.get("away_mode", False))
    if state.away_mode:
        state.last_away_press = time.time()
    register_hotkeys(state, settings)
    log("Hotkeys:  " + settings["away_hotkey"].upper() + " = away mode    "
        + settings["pause_hotkey"].upper() + " = pause    "
        + DEBUG_HOTKEY.upper() + " = debug snapshot    "
        + QUIT_HOTKEY.upper() + " = quit")
    log("Auto-accepting: " + describe_enabled(settings))
    log("Away Mode: " + ("ON" if state.away_mode else "OFF"))

    debug_dir = os.path.join(SCRIPT_DIR, "debug")
    if not os.path.isdir(debug_dir):
        os.makedirs(debug_dir, exist_ok=True)

    tray = make_tray_icon(state, debug_dir, settings)
    if tray is not None:
        tray.run_detached()
        log("System tray icon active - right-click it for menu options.")
    elif ENABLE_TRAY_ICON and not HAVE_PYSTRAY:
        log("Tray icon off - run install_requirements.bat to enable it.")

    # After Start: apply the hide_console preference. When hidden, the
    # console disappears completely (no taskbar entry) and the tray icon
    # becomes the only access point. When not hidden, the console stays
    # visible so its taskbar entry is your "running" indicator.
    if settings.get("hide_console", False):
        log("Hiding the console - right-click the tray icon for Settings.")
        state.console_visible = False
        set_console_visible(False)
    else:
        log("Console stays visible. Right-click the tray icon for Settings "
            "and other options.")

    with mss.mss() as sct:
        monitor = choose_monitor(sct, settings.get("monitor_index", "middle"))
        region = make_region(monitor)
        log("Watching strip: " + str(region["width"]) + "x"
            + str(region["height"]) + " px, top-center of the monitor.")
        log("Scanning every " + str(settings["scan_interval"])
            + "s. Switch to Star Citizen now.")
        print("", flush=True)

        while not state.quit:
            # --- Away Mode runs regardless of pause ----------------------
            if state.away_mode:
                now = time.time()
                if now - state.last_away_press >= AWAY_INTERVAL_SECONDS:
                    for _ in range(AWAY_PRESS_COUNT):
                        tap_key(AWAY_KEY)
                        time.sleep(AWAY_PRESS_GAP_SECONDS)
                    state.last_away_press = now
                    log("Away Mode: tapped '" + AWAY_KEY + "' x"
                        + str(AWAY_PRESS_COUNT) + " (anti-idle).")

            # --- settings window requested from the tray -----------------
            # Run it on a background thread so the watcher keeps scanning
            # while the window is open. tkinter is happy as long as each
            # root only ever talks to its own (creating) thread, which it
            # does here.
            if state.want_settings:
                state.want_settings = False
                if not state.settings_open:
                    state.settings_open = True

                    def _settings_thread():
                        try:
                            open_settings(state, settings)
                        finally:
                            state.settings_open = False

                    threading.Thread(target=_settings_thread,
                                     daemon=True).start()

            if state.paused:
                time.sleep(0.2)
                continue

            # --- grab + OCR the strip ------------------------------------
            shot = sct.grab(region)
            image = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
            variants = preprocess_variants(image)

            try:
                ocr_text = ""
                for v in variants:
                    ocr_text += pytesseract.image_to_string(v, config="--psm 6")
                    ocr_text += "\n"
            except Exception as e:
                log("OCR error: " + str(e))
                interruptible_sleep(settings["scan_interval"], state)
                continue

            cleaned = " ".join(ocr_text.split())
            label = detect_popup(ocr_text, settings)

            # --- debug snapshot: save exactly what the script captured ---
            if state.want_snapshot:
                state.want_snapshot = False
                ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                raw_path = os.path.join(debug_dir, "snap_" + ts + "_raw.png")
                proc_path = os.path.join(debug_dir, "snap_" + ts + "_processed.png")
                txt_path = os.path.join(debug_dir, "snap_" + ts + "_info.txt")
                image.save(raw_path)
                variants[0].save(proc_path)
                with open(txt_path, "w", encoding="utf-8") as fh:
                    fh.write("Capture region: " + str(region) + "\n")
                    fh.write("Detected as: " + str(label) + "\n\n")
                    fh.write("OCR text the script read:\n")
                    fh.write(ocr_text + "\n")
                banner(["DEBUG SNAPSHOT SAVED",
                        "Folder: " + debug_dir,
                        "The *_raw.png shows what the script captured."])

            if label:
                if time.time() - state.last_accept < ACCEPT_COOLDOWN_SECONDS:
                    if settings["verbose"]:
                        log(label + " popup still on screen - cooldown, skipping.")
                else:
                    log(label + ' detected. OCR read: "' + cleaned + '"')
                    if SAVE_DEBUG_IMAGES:
                        fname = datetime.datetime.now().strftime(
                            "hit_%Y%m%d_%H%M%S.png")
                        image.save(os.path.join(debug_dir, fname))
                    do_accept(state, label)
            else:
                if settings["verbose"]:
                    shown = cleaned if cleaned else "(no text)"
                    if len(shown) > 60:
                        shown = shown[:60] + "..."
                    status = "[away:" + ("ON" if state.away_mode else "OFF") + "]"
                    log("scanning  " + status + "  OCR: " + shown)

            interruptible_sleep(settings["scan_interval"], state)

    keyboard.unhook_all()
    if tray is not None:
        try:
            tray.stop()
        except Exception:
            pass
    # Make sure the console is visible so the exit prompt below can be seen.
    state.console_visible = True
    set_console_visible(True)
    print("", flush=True)
    log("Stopped.")
    input("Press Enter to close this window...")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("", flush=True)
        log("Interrupted by user (Ctrl+C). Stopped.")
    except Exception as e:
        fail("Unexpected problem: " + str(e))
