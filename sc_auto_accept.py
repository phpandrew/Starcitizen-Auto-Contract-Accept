"""
Star Citizen - Auto-Accept
==========================

Watches a strip across the top-center of your chosen monitor. When a popup
appears that you've chosen to auto-accept (Contract Shared, party invite, or
friend request), it presses the [ key to ACCEPT it.

Detection is done with OCR (optical character recognition) via Tesseract.

Settings (away/pause hotkeys + which popups to auto-accept) live in
settings.json next to this script. A settings window opens on the first run;
afterwards you can change settings from the system-tray icon.

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

# Folder this script lives in - used for settings.json, the debug folder,
# and the optional tray icon file.
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

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
REGION_HEIGHT_FRACTION = 0.30   # 0.30 = top 30% of the monitor height

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
AWAY_INTERVAL_SECONDS = 240      # how often to tap it (240 = every 4 minutes)
AWAY_MODE_AT_STARTUP = False     # start with Away Mode already on?

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

# Print the raw OCR text on every scan (handy while tuning). Set False for quiet.
VERBOSE = True

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
        return ctypes.windll.kernel32.GetConsoleWindow()
    except Exception:
        return 0


def set_console_visible(visible):
    """Show or hide the console (log) window. The script keeps running either way."""
    hwnd = get_console_window()
    if not hwnd:
        return
    try:
        # SW_HIDE = 0, SW_SHOW = 5
        ctypes.windll.user32.ShowWindow(hwnd, 5 if visible else 0)
        if visible:
            ctypes.windll.user32.SetForegroundWindow(hwnd)
    except Exception:
        pass


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
        log("Settings saved to settings.json")
    except Exception as e:
        log("Could not save settings.json: " + str(e))


def show_settings_window(settings):
    """Show the settings dialog. Returns the (possibly updated) settings dict."""
    try:
        import tkinter as tk
    except Exception:
        log("tkinter is not available - skipping the settings window.")
        return dict(settings)

    result = dict(settings)
    root = tk.Tk()
    root.title("Star Citizen Auto-Accept - Settings")
    root.resizable(False, False)
    try:
        root.attributes("-topmost", True)
        root.lift()
    except Exception:
        pass

    bold = ("Segoe UI", 10, "bold")

    tk.Label(root, text="Hotkeys", font=bold).grid(
        row=0, column=0, columnspan=2, sticky="w", padx=12, pady=(12, 2))

    tk.Label(root, text="Away Mode hotkey:").grid(
        row=1, column=0, sticky="w", padx=12, pady=3)
    away_var = tk.StringVar(value=settings["away_hotkey"])
    tk.Entry(root, textvariable=away_var, width=22).grid(
        row=1, column=1, padx=12, pady=3)

    tk.Label(root, text="Pause hotkey:").grid(
        row=2, column=0, sticky="w", padx=12, pady=3)
    pause_var = tk.StringVar(value=settings["pause_hotkey"])
    tk.Entry(root, textvariable=pause_var, width=22).grid(
        row=2, column=1, padx=12, pady=3)

    tk.Label(root, text="examples:   f8    ctrl+f12    alt+p    shift+f7",
             fg="gray").grid(row=3, column=0, columnspan=2, sticky="w",
                             padx=12, pady=(0, 4))

    tk.Label(root, text="Auto-accept", font=bold).grid(
        row=4, column=0, columnspan=2, sticky="w", padx=12, pady=(10, 2))

    contracts_var = tk.BooleanVar(value=settings["accept_contracts"])
    party_var = tk.BooleanVar(value=settings["accept_party_invites"])
    friend_var = tk.BooleanVar(value=settings["accept_friend_requests"])
    tk.Checkbutton(root, text="Contracts  (Contract Shared popups)",
                   variable=contracts_var).grid(
        row=5, column=0, columnspan=2, sticky="w", padx=12)
    tk.Checkbutton(root, text="Party invites",
                   variable=party_var).grid(
        row=6, column=0, columnspan=2, sticky="w", padx=12)
    tk.Checkbutton(root, text="Friend requests",
                   variable=friend_var).grid(
        row=7, column=0, columnspan=2, sticky="w", padx=12)

    def on_save():
        away = away_var.get().strip().lower()
        pause = pause_var.get().strip().lower()
        result["away_hotkey"] = away if away else settings["away_hotkey"]
        result["pause_hotkey"] = pause if pause else settings["pause_hotkey"]
        result["accept_contracts"] = bool(contracts_var.get())
        result["accept_party_invites"] = bool(party_var.get())
        result["accept_friend_requests"] = bool(friend_var.get())
        root.destroy()

    tk.Button(root, text="Save", width=16, command=on_save).grid(
        row=8, column=0, columnspan=2, pady=12)

    # Closing the window with the X behaves the same as Save.
    root.protocol("WM_DELETE_WINDOW", on_save)
    root.mainloop()
    return result


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


def choose_monitor(sct):
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


def make_tray_icon(state, debug_dir):
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
            log("Console window hidden (tray -> Show console window to bring "
                "it back).")
        else:
            log("Console window shown.")

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
        pystray.MenuItem("Settings...", on_settings),
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
    """Show the settings window, save, and re-apply hotkeys. Runs on main thread."""
    log("Opening settings window...")
    settings.update(show_settings_window(settings))
    save_settings(settings)
    keyboard.unhook_all()
    register_hotkeys(state, settings)
    log("Settings applied.  Hotkeys: " + settings["away_hotkey"].upper()
        + " = away, " + settings["pause_hotkey"].upper() + " = pause.")
    log("Auto-accepting: " + describe_enabled(settings))


def main():
    setup_console()
    banner([
        "STAR CITIZEN  -  AUTO-ACCEPT",
        "",
        "Right-click the tray icon for Settings, Pause, and Exit.",
    ])
    print("", flush=True)

    configure_tesseract()
    log("Tesseract OCR found and working.")

    # Load settings; on the very first run, pop up the settings window.
    first_run = not os.path.isfile(SETTINGS_FILE)
    settings = load_settings()
    if first_run:
        log("First run - opening the settings window...")
        settings.update(show_settings_window(settings))
        save_settings(settings)

    state = State()
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

    tray = make_tray_icon(state, debug_dir)
    if tray is not None:
        tray.run_detached()
        log("System tray icon active - right-click it for menu options.")
    elif ENABLE_TRAY_ICON and not HAVE_PYSTRAY:
        log("Tray icon off - run install_requirements.bat to enable it.")

    with mss.mss() as sct:
        monitor = choose_monitor(sct)
        region = make_region(monitor)
        log("Watching strip: " + str(region["width"]) + "x"
            + str(region["height"]) + " px, top-center of the monitor.")
        log("Scanning every " + str(SCAN_INTERVAL) + "s. Switch to Star Citizen now.")
        print("", flush=True)

        while not state.quit:
            # --- Away Mode runs regardless of pause ----------------------
            if state.away_mode:
                now = time.time()
                if now - state.last_away_press >= AWAY_INTERVAL_SECONDS:
                    tap_key(AWAY_KEY)
                    state.last_away_press = now
                    log("Away Mode: tapped '" + AWAY_KEY + "' (anti-idle).")

            # --- settings window requested from the tray -----------------
            if state.want_settings:
                state.want_settings = False
                open_settings(state, settings)

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
                interruptible_sleep(SCAN_INTERVAL, state)
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
                    if VERBOSE:
                        log(label + " popup still on screen - cooldown, skipping.")
                else:
                    log(label + ' detected. OCR read: "' + cleaned + '"')
                    if SAVE_DEBUG_IMAGES:
                        fname = datetime.datetime.now().strftime(
                            "hit_%Y%m%d_%H%M%S.png")
                        image.save(os.path.join(debug_dir, fname))
                    do_accept(state, label)
            else:
                if VERBOSE:
                    shown = cleaned if cleaned else "(no text)"
                    if len(shown) > 60:
                        shown = shown[:60] + "..."
                    status = "[away:" + ("ON" if state.away_mode else "OFF") + "]"
                    log("scanning  " + status + "  OCR: " + shown)

            interruptible_sleep(SCAN_INTERVAL, state)

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
