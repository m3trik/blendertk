"""Manual GUI harness for ``btk.toggle_fullscreen_area`` + ``btk.toggle_window_bars``
(``ui_utils/_ui_utils.py``) and their consumer ``Macros.m_toggle_panels``.

**GUI-only** — both helpers drive the topbar/statusbar *global areas*, which don't exist under
``--background``. The non-``test_``/non-``*_slot_check`` name keeps it out of ``Run-Tests.ps1``
(headless-only runner). Run it against a *fresh* Blender (never an existing session)::

    blender --factory-startup --python blendertk/test/fullscreen_area_gui_check.py

Pins the version-specific behavior the helpers are built on (probed on 5.1.2 — re-run on a
Blender upgrade): the TOPBAR area is unreachable via ``screen.areas``; a state-matched
``screen_full_area`` exit restores the bars; a ``back_to_previous`` exit keeps them hidden
(what ``toggle_window_bars`` hiding relies on). Also covers ``m_toggle_panels`` driving the
bars and the viewport regions in sync (mayatk parity) — GUI-only for the same reason, so the
headless ``test_macros.py`` can only cover its bars-sit-out fallback. Auto-quits when done.
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

    win = btk.main_window()
    scr = win.screen

    def layout():
        return sorted((a.type, a.x, a.y, a.width, a.height) for a in win.screen.areas)

    def top_gap():
        return win.height - max(a.y + a.height for a in win.screen.areas)

    def redraw():
        # Area geometry settles on the next paint; interactive use repaints after every
        # toggle, so the harness must too before reading coordinates back.
        with bpy.context.temp_override(window=win, screen=win.screen):
            bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=1)

    # -- the constraint the helpers exist for: the topbar is NOT a reachable area
    check("TOPBAR absent from screen.areas (the global-area constraint)",
          not any(a.type == "TOPBAR" for a in scr.areas))
    check("no show_topbar flag on Screen", not hasattr(scr, "show_topbar"))

    baseline = layout()
    check("baseline: tiled multi-area screen with the bars visible",
          not scr.show_fullscreen and len(baseline) > 1 and top_gap() > 4,
          f"{len(baseline)} areas, top gap {top_gap()}px")

    # ---- toggle_fullscreen_area -------------------------------------------------------
    # toggle on: largest area (the viewport) fills the ENTIRE window — bars included
    res = btk.toggle_fullscreen_area()
    redraw()
    scr = win.screen  # fullscreen swaps in a temp screen
    check("fullscreen: toggle on -> True", res is True)
    check("fullscreen: screen is fullscreen", scr.show_fullscreen)
    full = max(scr.areas, key=lambda a: a.width * a.height)
    check("fullscreen: area is the 3D viewport (largest-area default)", full.type == "VIEW_3D", full.type)
    check("fullscreen: area spans the full window (topbar+statusbar hidden)",
          (full.x, full.y, full.width, full.height) == (0, 0, win.width, win.height),
          f"area={full.x},{full.y} {full.width}x{full.height} win={win.width}x{win.height}")

    # toggle off: the previous tiled layout comes back exactly, bars and all
    res = btk.toggle_fullscreen_area()
    redraw()
    check("fullscreen: toggle off -> False", res is False)
    check("fullscreen: tiled layout restored exactly (bars back)",
          not win.screen.show_fullscreen and layout() == baseline,
          f"now={layout()} base={baseline}")

    # editor param targets a named area
    res = btk.toggle_fullscreen_area(editor="Outliner")
    redraw()
    scr = win.screen
    full = max(scr.areas, key=lambda a: a.width * a.height)
    check("fullscreen: editor='Outliner' fullscreens the Outliner",
          res is True and scr.show_fullscreen and full.ui_type == "OUTLINER", full.ui_type)
    btk.toggle_fullscreen_area()
    redraw()
    check("fullscreen: restored after editor toggle", layout() == baseline, f"now={layout()}")

    # unknown editor: no-op, stays tiled
    check("fullscreen: unknown editor -> False, layout untouched",
          btk.toggle_fullscreen_area(editor="No Such Editor") is False and layout() == baseline)

    # hide_panels=False = plain maximize: area fills the tiled space, bars stay
    res = btk.toggle_fullscreen_area(hide_panels=False)
    redraw()
    scr = win.screen
    full = max(scr.areas, key=lambda a: a.width * a.height)
    check("fullscreen: maximize variant keeps the bars (area shorter than the window)",
          res is True and scr.show_fullscreen and full.height < win.height,
          f"area h={full.height} win h={win.height}")
    res = btk.toggle_fullscreen_area()
    redraw()
    check("fullscreen: restored after maximize (state-matched exit)",
          res is False and layout() == baseline, f"now={layout()}")

    # ---- toggle_window_bars -----------------------------------------------------------
    # The bars-only contract: use_hide_panels collapses the fullscreened area's own header +
    # toolbar and back_to_previous does NOT restore them, so the round-trip must snapshot them.
    # Geometry alone can't see this (region flags don't move area rects) — assert the flags.
    def v3d_flags():
        sp = next(a.spaces.active for a in win.screen.areas if a.type == "VIEW_3D")
        return (sp.show_region_header, sp.show_region_toolbar, sp.show_region_ui)

    flags_before = v3d_flags()

    # hide: tiled layout kept (same area types), areas expand to the window top
    res = btk.toggle_window_bars()
    redraw()
    check("bars: toggle -> hidden (returns False = not visible)", res is False)
    check("bars: topbar row gone, tiled layout kept",
          top_gap() <= 4
          and not win.screen.show_fullscreen
          and sorted(t for t, *_ in layout()) == sorted(t for t, *_ in baseline),
          f"top gap {top_gap()}px, layout={layout()}")
    check("bars: hiding leaves the viewport's own regions untouched (bars-only contract)",
          v3d_flags() == flags_before, f"{flags_before} -> {v3d_flags()}")

    # idempotent targeted hide
    check("bars: visible=False when already hidden -> False, no churn",
          btk.toggle_window_bars(visible=False) is False and top_gap() <= 4)

    # show: bars come back and the layout returns to the exact baseline
    res = btk.toggle_window_bars()
    redraw()
    check("bars: toggle -> visible again (returns True)", res is True)
    check("bars: baseline layout restored exactly", layout() == baseline, f"now={layout()}")
    check("bars: showing leaves the viewport's own regions untouched too",
          v3d_flags() == flags_before, f"{flags_before} -> {v3d_flags()}")

    # targeted show when already visible: no churn
    check("bars: visible=True when already visible -> True, layout untouched",
          btk.toggle_window_bars(visible=True) is True and layout() == baseline)

    # All-or-nothing with no CONTEXT window (the Qt event-pump timer state the macros can run
    # in): the round-trip's panel side effect can only be undone by a bare assignment, which
    # needs one — so the whole toggle must sit out rather than hide the bars and strand the
    # viewport's regions collapsed (measured: it did exactly that, and reported success).
    with bpy.context.temp_override(window=None):
        res_nw = btk.toggle_window_bars(visible=False)
    redraw()
    check("bars: no context window -> None, nothing applied at all",
          res_nw is None and top_gap() > 4 and v3d_flags() == flags_before,
          f"ret={res_nw}, top gap {top_gap()}px, regions={v3d_flags()}")

    # composes with fullscreen mode: unsupported there (fullscreen manages the bars itself)
    btk.toggle_fullscreen_area()
    redraw()
    check("bars: None while in fullscreen-area mode", btk.toggle_window_bars() is None)
    btk.toggle_fullscreen_area()
    redraw()
    check("bars: baseline intact after the fullscreen detour", layout() == baseline)

    # combo: a maximize entered while the bars are hidden covers the FULL window, so the exit
    # can't tell the state apart geometrically — it must still get out (prop-retry exit)
    btk.toggle_window_bars(visible=False)
    redraw()
    res = btk.toggle_fullscreen_area(hide_panels=False)
    redraw()
    check("combo: maximize entered with the bars hidden", res is True and win.screen.show_fullscreen)
    res = btk.toggle_fullscreen_area()
    redraw()
    check("combo: exit does not strand the window in fullscreen",
          res is False and not win.screen.show_fullscreen,
          f"show_fullscreen={win.screen.show_fullscreen}")
    btk.toggle_window_bars(visible=True)
    redraw()
    check("combo: bars visible and baseline restored at the end", layout() == baseline,
          f"now={layout()}")

    # ---- Macros.m_toggle_panels: bars + viewport regions in sync (mayatk parity) ----------
    from blendertk.edit_utils.macros import Macros

    space = next(a.spaces.active for a in win.screen.areas if a.type == "VIEW_3D")

    def regions():
        return (space.show_region_header, space.show_region_toolbar, space.show_region_ui)

    space.show_region_header = space.show_region_toolbar = space.show_region_ui = True
    redraw()
    check("macro: starts from bars visible + regions visible",
          top_gap() > 4 and regions() == (True, True, True))

    Macros.m_toggle_panels()
    redraw()
    check("macro: one press hides the topbar AND the viewport regions",
          top_gap() <= 4 and regions() == (False, False, False),
          f"top gap {top_gap()}px, regions={regions()}")
    check("macro: tiled layout kept (never fullscreen)", not win.screen.show_fullscreen)

    Macros.m_toggle_panels()
    redraw()
    check("macro: press again restores both, layout byte-exact",
          top_gap() > 4 and regions() == (True, True, True) and layout() == baseline,
          f"top gap {top_gap()}px, regions={regions()}, now={layout()}")

    # the fix's payoff: bars hidden out-of-band, regions still shown = drift. The bars lead, so
    # one press absorbs it into a consistent state instead of flipping them out of phase forever.
    btk.toggle_window_bars(visible=False)
    redraw()
    check("macro: drift set up (bars hidden, regions shown)",
          top_gap() <= 4 and regions() == (True, True, True))
    Macros.m_toggle_panels()
    redraw()
    check("macro: drift absorbed — bars lead, regions follow to match",
          top_gap() > 4 and regions() == (True, True, True),
          f"top gap {top_gap()}px, regions={regions()}")

    # opt-out halves (mayatk's toggle_menu / toggle_panels)
    Macros.m_toggle_panels(toggle_panels=False)
    redraw()
    check("macro: toggle_panels=False hides the bars only",
          top_gap() <= 4 and regions() == (True, True, True),
          f"top gap {top_gap()}px, regions={regions()}")
    Macros.m_toggle_panels(toggle_menu=False)
    redraw()
    check("macro: toggle_menu=False toggles the regions only (bars stay hidden)",
          top_gap() <= 4 and regions() == (False, False, False),
          f"top gap {top_gap()}px, regions={regions()}")

    # leave the session as we found it
    btk.toggle_window_bars(visible=True)
    space.show_region_header = space.show_region_toolbar = space.show_region_ui = True
    redraw()
    check("macro: baseline restored at the end", layout() == baseline and top_gap() > 4)


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
    # Quit under an explicit window override — a bare-timer quit_blender crashes on a NULL
    # context window (see CHANGELOG 2026-07-15, console_shadow_check harness fix).
    win = bpy.context.window_manager.windows[0]
    with bpy.context.temp_override(window=win):
        bpy.ops.wm.quit_blender()
    return None


import bpy  # noqa: E402

bpy.app.timers.register(_main, first_interval=1.0)
