import ctypes
import os
import random
import threading
import time
import tkinter as tk
from pathlib import Path

import keyboard
from PIL import Image, ImageTk


# ============================================================
# THE WRONG KEYBOARD
# Windows desktop application
#
# Features:
# - System-wide fixed keyboard remapping
# - Unpredictable Shift fake shutdown
# - Caps Lock ALWAYS triggers the fake shutdown
# - Exactly 60-second fake shutdown screen
# - Random visual meme after shutdown
# - Normal Keyboard Mode
# - F12 emergency restore/exit
#
# IMPORTANT:
# This program NEVER actually shuts down Windows.
# ============================================================


# ------------------------------------------------------------
# CONFIGURATION
# ------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
MEME_FOLDER = BASE_DIR / "memes"

SHUTDOWN_DURATION = 60       # Exactly 60 seconds
MEME_DURATION = 4             # Meme stays for 4 seconds

# Shift trigger probability.
# Every time Shift is pressed, this probability decides whether
# the fake shutdown happens.
SHIFT_TRIGGER_PROBABILITY = 0.12

# Minimum number of Shift presses before another shutdown can
# happen, so it doesn't trigger immediately again.
MIN_SHIFT_PRESSES = 3

# ------------------------------------------------------------
# FIXED KEY MAPPINGS
#
# NOTE: "caps lock" has been removed from this table on purpose.
# Caps Lock is no longer remapped to Esc — it is now a dedicated
# trigger key for the fake shutdown screen (see capslock_handler
# below). If you want Caps Lock to also do a key-remap in Normal
# Cursed Mode, don't add it back here; handle it in
# capslock_handler instead, since a key can only be hooked once.
# ------------------------------------------------------------

KEY_MAPPING = {
    "tab": "backspace",
    "backspace": "enter",
    "enter": "space",
    "space": "tab",

    "esc": "caps lock",

    "home": "end",
    "end": "home",

    "up": "down",
    "down": "up",
    "left": "right",
    "right": "left",

    "delete": "shift",
}


# ------------------------------------------------------------
# GLOBAL STATE
# ------------------------------------------------------------

cursed_mode = True
prank_running = False

shift_press_count = 0
shift_presses_since_prank = 0

keyboard_hooks = []
capslock_hook = None
remap_hotkeys = []

state_lock = threading.Lock()


# ============================================================
# FULL VIRTUAL DESKTOP GEOMETRY (COVERS ALL MONITORS)
# ============================================================

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79


def get_full_screen_geometry():
    """
    Returns (x, y, width, height) spanning the entire Windows
    virtual desktop, i.e. every connected monitor combined.

    Using this instead of the '-fullscreen' attribute avoids the
    "partially black" bug where -fullscreen only covers the
    primary monitor and leaves other monitors untouched.
    """
    try:
        user32 = ctypes.windll.user32
        x = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
        y = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
        width = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
        height = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)

        if width > 0 and height > 0:
            return x, y, width, height
    except Exception as exc:
        print("Could not read virtual screen metrics:", exc)

    # Fallback: primary monitor only.
    return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()


def make_fully_black_window():
    """
    Creates a Toplevel that is guaranteed to be 100% black and
    covers the entire virtual desktop (all monitors), with no
    window borders, title bar, or gaps of any kind.
    """
    x, y, width, height = get_full_screen_geometry()

    window = tk.Toplevel(root)

    window.configure(bg="black")
    window.overrideredirect(True)
    window.geometry(f"{width}x{height}+{x}+{y}")
    window.attributes("-topmost", True)
    window.configure(cursor="none")

    # Belt-and-suspenders: also request native fullscreen on the
    # monitor the window lands on. If this fails on some setups,
    # the explicit geometry above still guarantees full coverage.
    try:
        window.attributes("-fullscreen", True)
    except Exception:
        pass

    window.focus_force()

    return window


# ============================================================
# TKINTER ROOT
# ============================================================

root = tk.Tk()
root.title("The Wrong Keyboard")
root.geometry("450x280")
root.resizable(False, False)


# ============================================================
# UI VARIABLES
# ============================================================

mode_var = tk.StringVar(value="CURSED MODE — ON")
status_var = tk.StringVar(value="Keyboard is currently cursed.")


# ============================================================
# LOAD MEMES
# ============================================================

def get_meme_files():
    """
    Returns supported meme image files from the memes folder.
    """
    MEME_FOLDER.mkdir(exist_ok=True)

    supported = {
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".gif",
        ".bmp",
    }

    return [
        path
        for path in MEME_FOLDER.iterdir()
        if path.is_file() and path.suffix.lower() in supported
    ]


# ============================================================
# KEYBOARD REMAPPING
# ============================================================

def send_replacement_key(replacement):
    """
    Sends the replacement key.
    """
    try:
        keyboard.send(replacement)
    except Exception as exc:
        print("Could not send replacement key:", exc)


def make_remap_handler(source_key, target_key):
    """
    Creates a handler for a physical source key.
    """
    def handler(_event):
        if not cursed_mode:
            return

        with state_lock:
            # Do not interfere while fake shutdown is running.
            if prank_running:
                return

        # Suppress the original key and send replacement.
        try:
            keyboard.press(target_key)
            keyboard.release(target_key)
        except Exception as exc:
            print(
                f"Remapping error: {source_key} -> "
                f"{target_key}: {exc}"
            )

    return handler


def install_remapping():
    """
    Installs fixed global key mappings plus the Caps Lock
    fake-shutdown trigger.
    """
    global keyboard_hooks
    global capslock_hook

    remove_remapping()

    if not cursed_mode:
        return

    for source, target in KEY_MAPPING.items():
        try:
            hook = keyboard.on_press_key(
                source,
                make_remap_handler(source, target),
                suppress=True
            )
            keyboard_hooks.append(hook)
        except Exception as exc:
            print(f"Could not hook {source}: {exc}")

    # Caps Lock is handled separately: it always triggers the
    # fake shutdown instead of behaving like a normal key.
    try:
        capslock_hook = keyboard.on_press_key(
            "caps lock",
            capslock_handler,
            suppress=True
        )
    except Exception as exc:
        print("Could not hook caps lock:", exc)


def remove_remapping():
    """
    Removes all fixed key-remapping hooks and the Caps Lock hook.
    """
    global keyboard_hooks
    global capslock_hook

    for hook in keyboard_hooks:
        try:
            keyboard.unhook(hook)
        except Exception:
            pass

    keyboard_hooks.clear()

    if capslock_hook is not None:
        try:
            keyboard.unhook(capslock_hook)
        except Exception:
            pass
        capslock_hook = None


# ============================================================
# CAPS LOCK LOGIC (ALWAYS TRIGGERS FAKE SHUTDOWN)
# ============================================================

def capslock_handler(_event):
    """
    Handles the physical Caps Lock key.

    Every single press of Caps Lock immediately triggers the
    fake shutdown screen — no probability roll, no minimum
    press count. The key press itself is suppressed, so the
    Caps Lock light/state is never actually toggled.
    """
    if not cursed_mode:
        return

    with state_lock:
        if prank_running:
            return

    # Start prank on UI thread.
    root.after(0, start_fake_shutdown)


# ============================================================
# SHIFT LOGIC
# ============================================================

def shift_handler(event):
    """
    Handles the real physical Shift key.

    Shift normally behaves as Shift.

    At an unpredictable time, a Shift press triggers
    the fake shutdown.
    """
    global shift_press_count
    global shift_presses_since_prank

    if not cursed_mode:
        return

    with state_lock:
        if prank_running:
            return

        shift_press_count += 1
        shift_presses_since_prank += 1

        # Prevent immediate repeated triggering.
        if shift_presses_since_prank < MIN_SHIFT_PRESSES:
            return

        should_trigger = (
            random.random() < SHIFT_TRIGGER_PROBABILITY
        )

        if should_trigger:
            shift_presses_since_prank = 0

            # Start prank on UI thread.
            root.after(50, start_fake_shutdown)


# ============================================================
# FAKE SHUTDOWN
# ============================================================

def start_fake_shutdown():
    """
    Starts the fullscreen fake shutdown.
    """
    global prank_running

    with state_lock:
        if prank_running or not cursed_mode:
            return

        prank_running = True

    # Temporarily stop remapping (this also removes the Caps
    # Lock hook so repeated presses during the prank do nothing
    # extra).
    remove_remapping()

    shutdown_window = make_fully_black_window()

    container = tk.Frame(
        shutdown_window,
        bg="black"
    )
    container.place(
        relx=0.5,
        rely=0.5,
        anchor="center"
    )

    # --------------------------------------------------------
    # WHITE DOT SPINNER
    # --------------------------------------------------------

    canvas = tk.Canvas(
        container,
        width=100,
        height=100,
        bg="black",
        highlightthickness=0,
    )
    canvas.pack(pady=(0, 20))

    center_x = 50
    center_y = 50
    radius = 30

    dot_items = []

    import math

    for i in range(8):
        angle = (2 * math.pi * i) / 8

        x = center_x + radius * math.cos(angle)
        y = center_y + radius * math.sin(angle)

        dot = canvas.create_oval(
            x - 4,
            y - 4,
            x + 4,
            y + 4,
            fill="white",
            outline=""
        )

        dot_items.append(dot)

    # --------------------------------------------------------
    # "SHUTTING DOWN" TEXT
    # --------------------------------------------------------

    shutdown_label = tk.Label(
        container,
        text="Shutting down",
        bg="black",
        fg="white",
        font=("Segoe UI", 18),
    )
    shutdown_label.pack()

    # --------------------------------------------------------
    # SPINNER ANIMATION
    # --------------------------------------------------------

    def animate_spinner(step=0):
        if not shutdown_window.winfo_exists():
            return

        for index, dot in enumerate(dot_items):
            if index == step % len(dot_items):
                canvas.itemconfig(
                    dot,
                    fill="white"
                )
            else:
                canvas.itemconfig(
                    dot,
                    fill="#555555"
                )

        shutdown_window.after(
            100,
            animate_spinner,
            step + 1
        )

    animate_spinner()

    # --------------------------------------------------------
    # EXACT 60-SECOND TIMER
    # --------------------------------------------------------

    shutdown_window.after(
        SHUTDOWN_DURATION * 1000,
        lambda: finish_fake_shutdown(shutdown_window)
    )


# ============================================================
# MEME DISPLAY
# ============================================================

def finish_fake_shutdown(shutdown_window):
    """
    Closes the fake shutdown and displays a random visual meme.
    """
    global prank_running

    try:
        shutdown_window.destroy()
    except Exception:
        pass

    show_random_meme()


def show_random_meme():
    """
    Displays a random meme image.
    """
    global prank_running

    meme_files = get_meme_files()

    if not meme_files:
        # If there are no memes, simply restore the keyboard.
        finish_prank()
        return

    meme_path = random.choice(meme_files)

    meme_window = make_fully_black_window()

    try:
        image = Image.open(meme_path)

        # Convert animated images to first frame if necessary.
        try:
            image.seek(0)
        except Exception:
            pass

        image = image.convert("RGB")

        screen_width = meme_window.winfo_screenwidth()
        screen_height = meme_window.winfo_screenheight()

        # Fit the meme inside the screen while preserving aspect ratio.
        max_width = int(screen_width * 0.85)
        max_height = int(screen_height * 0.85)

        image.thumbnail(
            (max_width, max_height),
            Image.Resampling.LANCZOS
        )

        photo = ImageTk.PhotoImage(image)

        label = tk.Label(
            meme_window,
            image=photo,
            bg="black"
        )

        # Keep image reference alive.
        label.image = photo

        label.pack(
            expand=True
        )

    except Exception as exc:
        print(
            f"Could not display meme {meme_path}: {exc}"
        )

        fallback = tk.Label(
            meme_window,
            text="JUST KIDDING.",
            fg="white",
            bg="black",
            font=("Segoe UI", 40, "bold")
        )

        fallback.pack(expand=True)

    # Remove meme after a few seconds.
    meme_window.after(
        MEME_DURATION * 1000,
        lambda: end_meme(meme_window)
    )


def end_meme(meme_window):
    try:
        meme_window.destroy()
    except Exception:
        pass

    finish_prank()


# ============================================================
# FINISH PRANK
# ============================================================

def finish_prank():
    """
    Restores cursed keyboard behaviour after prank.
    """
    global prank_running
    global shift_press_count

    with state_lock:
        prank_running = False
        shift_press_count = 0

    if cursed_mode:
        install_remapping()


# ============================================================
# NORMAL MODE / CURSED MODE
# ============================================================

def enable_cursed_mode():
    global cursed_mode
    global shift_press_count
    global shift_presses_since_prank

    with state_lock:
        cursed_mode = True
        shift_press_count = 0
        shift_presses_since_prank = 0

    install_remapping()

    mode_var.set("CURSED MODE — ON")
    status_var.set(
        "Keyboard remapping is active. Caps Lock triggers "
        "the fake shutdown."
    )


def restore_normal_keyboard():
    global cursed_mode
    global shift_press_count
    global shift_presses_since_prank

    with state_lock:
        cursed_mode = False
        shift_press_count = 0
        shift_presses_since_prank = 0

    remove_remapping()

    mode_var.set("NORMAL MODE — ON")
    status_var.set(
        "Keyboard is completely normal."
    )


# ============================================================
# COMPLETE EXIT
# ============================================================

def emergency_exit():
    """
    Safely releases all keyboard hooks and exits.
    """
    global cursed_mode

    with state_lock:
        cursed_mode = False

    remove_remapping()

    try:
        keyboard.unhook_all()
    except Exception:
        pass

    try:
        root.destroy()
    except Exception:
        pass


# ============================================================
# CONTROL WINDOW
# ============================================================

title = tk.Label(
    root,
    text="THE WRONG KEYBOARD",
    font=("Segoe UI", 21, "bold")
)
title.pack(pady=(25, 8))

mode_label = tk.Label(
    root,
    textvariable=mode_var,
    font=("Segoe UI", 13, "bold")
)
mode_label.pack(pady=5)

status_label = tk.Label(
    root,
    textvariable=status_var,
    font=("Segoe UI", 10),
    wraplength=380,
    justify="center"
)
status_label.pack(pady=8)

restore_button = tk.Button(
    root,
    text="Restore Normal Keyboard",
    command=restore_normal_keyboard,
    width=25,
    height=2
)
restore_button.pack(pady=5)

curse_button = tk.Button(
    root,
    text="Enable Cursed Mode",
    command=enable_cursed_mode,
    width=25,
    height=2
)
curse_button.pack(pady=5)

exit_label = tk.Label(
    root,
    text="F12 = Emergency Restore + Exit",
    fg="#666666",
    font=("Segoe UI", 9)
)
exit_label.pack(pady=(12, 0))


# ============================================================
# GLOBAL F12 EMERGENCY EXIT
# ============================================================

try:
    keyboard.add_hotkey(
        "f12",
        emergency_exit,
        suppress=False
    )
except Exception as exc:
    print("Could not register F12:", exc)


# ============================================================
# STARTUP
# ============================================================

def startup():
    """
    Starts the keyboard hooks safely.
    """
    enable_cursed_mode()


root.protocol(
    "WM_DELETE_WINDOW",
    emergency_exit
)

root.after(
    300,
    startup
)

root.mainloop()