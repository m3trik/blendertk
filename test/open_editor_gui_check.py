"""Manual GUI harness for ``btk.open_editor`` (``ui_utils/_ui_utils.py``).

**GUI-only** — ``wm.window_new`` polls false under ``--background`` (``G.background``), so the
headless ``test_ui_utils.py`` can only cover the editor-name map. The non-``test_``/non-
``*_slot_check`` name keeps this out of ``Run-Tests.ps1`` (headless-only runner). Run it against
a *fresh* Blender (never an existing session)::

    blender --factory-startup --python blendertk/test/open_editor_gui_check.py

Pins the regression this exists for: tentacle drives the slots from its Qt event-pump timer,
where ``context.window`` is ``None``, and ``wm.window_new``'s poll reads it — a live
``uv#b031`` click raised ``window_new.poll() failed, context is incorrect``. The no-context-window
case below is the actual bug; ``temp_override(window=None)`` reproduces that state exactly (same
trick as ``fullscreen_area_gui_check.py``). Also covers the pointer-diff guard: a refused open
must never retype the MAIN window's viewport. Auto-quits when done.
"""
import sys
import os
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for p in (REPO, os.path.join(MONO, "pythontk")):
    if p not in sys.path:
        sys.path.insert(0, p)

lines = []


def check(name, cond, detail=""):
    lines.append(f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}")


def run_checks():
    import bpy
    import blendertk as btk

    wm = bpy.context.window_manager
    main = btk.main_window()

    def close(window):
        # Deliberately NOT ui_utils._close_window: the unknown-editor check below asserts that
        # open_editor cleans up after itself, which can't be measured with open_editor's own
        # cleanup helper. Keep this independent.
        if window is not None and window != main:
            with bpy.context.temp_override(window=window):
                bpy.ops.wm.window_close()

    def main_ui_types():
        return sorted(a.ui_type for a in main.screen.areas)

    baseline_types = main_ui_types()
    check("baseline: one window, main viewport intact",
          len(wm.windows) == 1 and "VIEW_3D" in baseline_types, f"{baseline_types}")

    # ---- normal context: opens a NEW window switched to the editor
    win = btk.open_editor("UV Editor")
    check("open_editor -> a new window", win is not None and win != main,
          f"windows={len(wm.windows)}")
    if win is not None:
        check("open_editor: the new window's area is the UV editor",
              win.screen.areas[0].ui_type == "UV", win.screen.areas[0].ui_type)
    check("open_editor: main window untouched", main_ui_types() == baseline_types,
          f"{main_ui_types()}")
    close(win)

    # ---- THE REGRESSION: no context window (tentacle's Qt event-pump timer state, where a live
    # uv#b031 click raised "window_new.poll() failed, context is incorrect")
    err = None
    try:
        with bpy.context.temp_override(window=None):
            win = btk.open_editor("UV Editor")
    except RuntimeError as e:
        win, err = None, e
    check("THE FIX — open_editor with NO context window still opens the editor "
          "(the tentacle Qt-timer state)",
          err is None and win is not None and win != main,
          f"err={err} win={win}")
    if win is not None:
        check("no-context-window: the opened window is the UV editor",
              win.screen.areas[0].ui_type == "UV", win.screen.areas[0].ui_type)
    check("no-context-window: main window still untouched (pointer-diff guard — a refused "
          "open must never retype the user's viewport)",
          main_ui_types() == baseline_types, f"{main_ui_types()}")
    close(win)

    # ---- Preferences: its own op (screen.userpref_show), same no-context-window requirement.
    # Also pins WHY that branch can't trust windows[-1]: a second call re-focuses the window it
    # already opened instead of appending one, so it must match on the editor.
    with bpy.context.temp_override(window=None):
        prefs = btk.open_editor("Preferences")
    check("Preferences opens with NO context window",
          prefs is not None and any(a.ui_type == "PREFERENCES" for a in prefs.screen.areas),
          f"{prefs}")
    again = btk.open_editor("Preferences")
    check("Preferences: a second call returns the SAME window (userpref_show re-focuses, "
          "never appends — the reason windows[-1] is untrustworthy here)",
          again is not None and prefs is not None and again.as_pointer() == prefs.as_pointer(),
          f"windows={len(wm.windows)}")
    close(prefs)

    # ---- raw ui_type accepted as well as the friendly name
    win = btk.open_editor("OUTLINER")
    check("open_editor accepts a raw ui_type",
          win is not None and win.screen.areas[0].ui_type == "OUTLINER",
          win.screen.areas[0].ui_type if win else None)
    close(win)

    # ---- unknown editor: None AND no stray window. open_editor can only tell a bogus ui_type
    # from a real one by assigning it, so the window is already open by then — it must unwind
    # the window it opened rather than strand a duplicate viewport behind the caller's
    # "could not open" message (channels_slots / rizom_bridge_slots branch on the None).
    n_before = len(wm.windows)
    win = btk.open_editor("No Such Editor")
    check("unknown editor -> None", win is None, f"{win}")
    check("unknown editor: the window it opened to test the ui_type is closed again "
          "(no stray duplicate viewport)",
          len(wm.windows) == n_before, f"windows={len(wm.windows)} before={n_before}")
    check("unknown editor: main window untouched",
          main_ui_types() == baseline_types, f"{main_ui_types()}")

    check("end state: back to the main window alone", len(wm.windows) == 1,
          f"windows={len(wm.windows)}")


def _main():
    import bpy

    try:
        run_checks()
    except Exception:
        traceback.print_exc()
        lines.append("FAIL unhandled exception")
    print("\n".join(lines))
    ok = all(l.startswith("OK") for l in lines) and lines
    print(f"===RESULT: {'PASS' if ok else 'FAIL'}=== ({sum(1 for l in lines if l.startswith('OK'))}/{len(lines)})")
    sys.stdout.flush()
    # Quit under an explicit window override — a bare-timer quit_blender crashes on a NULL
    # context window (see CHANGELOG 2026-07-15, console_shadow_check harness fix).
    win = bpy.context.window_manager.windows[0]
    with bpy.context.temp_override(window=win):
        bpy.ops.wm.quit_blender()
    return None


import bpy  # noqa: E402

bpy.app.timers.register(_main, first_interval=1.0)
