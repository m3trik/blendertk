"""Manual GUI harness for ``btk.UiState`` (``ui_utils/ui_state.py``) — the halves that need a
real screen: closing the hidden editors, the ``show_region_*`` flags, the tick snapshot, and
the re-apply after a file load. The non-``test_`` name keeps this out of the headless runner.
Run it against a *fresh* Blender (never an existing session)::

    blender --factory-startup --python blendertk/test/ui_state_gui_check.py

Drives the whole lifecycle against a scratch sidecar: seed "Timeline hidden, grid off, toolbar
off" -> install from a windowless timer state (the fallback path) -> first tick applies ->
user-style changes land in the sidecar -> a maximized viewport's one-area screen is skipped
(not recorded as "everything closed") -> ``read_homefile`` brings the factory layout back ->
the tick re-applies the saved state over it. Auto-quits when done.
"""
import json
import os
import shutil
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for p in (REPO, os.path.join(MONO, "pythontk")):
    if p not in sys.path:
        sys.path.insert(0, p)

lines = []
SCRATCH = os.path.join(HERE, "temp_tests", f"ui_state_gui_{os.getpid()}")


def check(name, cond, detail=""):
    lines.append(f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}")


def _ui_types():
    import blendertk as btk

    return sorted(a.ui_type for a in btk.main_window().screen.areas)


def _v3d():
    import blendertk as btk

    area = next(a for a in btk.main_window().screen.areas if a.ui_type == "VIEW_3D")
    return area.spaces.active


def _sidecar():
    from blendertk.ui_utils.ui_state import UiState

    with open(UiState.state_path(), "r", encoding="utf-8") as f:
        return json.load(f)


def phase_seed_and_install():
    import bpy
    import blendertk as btk
    from blendertk.ui_utils.ui_state import UiState

    os.makedirs(SCRATCH, exist_ok=True)
    UiState._state_dir_override = SCRATCH
    UiState.INTERVAL = 0.4
    ws = btk.main_window().workspace.name
    UiState.save({"version": 1, "workspaces": {ws: {
        "hidden": ["TIMELINE"],
        "spaces": {"VIEW_3D": {"overlay.show_floor": False, "show_region_toolbar": False}},
    }}})
    types = _ui_types()
    v3d = _v3d()
    check("baseline: factory layout has a TIMELINE, grid on, toolbar on",
          "TIMELINE" in types and v3d.overlay.show_floor and v3d.show_region_toolbar, str(types))
    # The fallback path: no context window at install -> nothing applied yet, first tick does it.
    with bpy.context.temp_override(window=None):
        ok = UiState.install()
    check("install() from a windowless state -> True, apply deferred to the tick",
          ok and UiState._installed and UiState._pending_apply and "TIMELINE" in _ui_types())
    check("install() is idempotent", UiState.install() is True)
    # A timer registered from inside another timer's callback gets its first slot only after
    # the registering timer's next fire (measured) -> give the tick real slack, not one INTERVAL.
    return 2.0


def phase_applied():
    from blendertk.ui_utils.ui_state import UiState

    types = _ui_types()
    v3d = _v3d()
    check("tick applied: TIMELINE closed", "TIMELINE" not in types, str(types))
    check("tick applied: grid (overlay.show_floor) off", not v3d.overlay.show_floor)
    check("tick applied: toolbar (show_region_toolbar) off", not v3d.show_region_toolbar)
    check("pending flag cleared", not UiState._pending_apply)
    ws = UiState._state["workspaces"]
    entry = next(iter(ws.values()))
    check("snapshot keeps TIMELINE hidden (loaded - live)", entry["hidden"] == ["TIMELINE"], str(entry["hidden"]))
    # user-style changes: grid back on, close the Outliner
    import blendertk as btk

    v3d.overlay.show_floor = True
    btk.close_editor("OUTLINER")
    return 2.0


def phase_user_changes_persisted():
    data = _sidecar()
    entry = next(iter(data["workspaces"].values()))
    check("sidecar: grid back on recorded", entry["spaces"]["VIEW_3D"]["overlay.show_floor"] is True)
    check("sidecar: OUTLINER now hidden too", entry["hidden"] == ["OUTLINER", "TIMELINE"], str(entry["hidden"]))
    check("sidecar: toolbar still off", entry["spaces"]["VIEW_3D"]["show_region_toolbar"] is False)
    # Maximize the viewport: the temporary one-area screen must NOT read as "everything closed".
    import blendertk as btk

    check("maximize viewport (toggle_fullscreen_area)", btk.toggle_fullscreen_area("VIEW_3D") is True
          and btk.main_window().screen.show_fullscreen)
    return 2.0


def phase_fullscreen_skipped():
    import blendertk as btk

    entry = next(iter(_sidecar()["workspaces"].values()))
    check("maximized: sidecar untouched (no PROPERTIES/VIEW_3D marked hidden)",
          entry["hidden"] == ["OUTLINER", "TIMELINE"], str(entry["hidden"]))
    check("restore viewport", btk.toggle_fullscreen_area("VIEW_3D") is False
          and not btk.main_window().screen.show_fullscreen)
    return 2.0


def phase_reload():
    entry = next(iter(_sidecar()["workspaces"].values()))
    check("after restore: sidecar still the user's state", entry["hidden"] == ["OUTLINER", "TIMELINE"], str(entry["hidden"]))
    # A file load brings the file's own layout back (Load UI) -> must be re-applied.
    import bpy
    import blendertk as btk

    with bpy.context.temp_override(window=btk.main_window()):
        bpy.ops.wm.read_homefile()
    types = _ui_types()
    v3d = _v3d()
    check("after read_homefile: factory layout is back (TIMELINE/OUTLINER, toolbar on)",
          "TIMELINE" in types and "OUTLINER" in types and v3d.show_region_toolbar, str(types))
    return 2.0


def phase_reapplied_after_load():
    from blendertk.ui_utils.ui_state import UiState

    types = _ui_types()
    v3d = _v3d()
    check("re-applied after load: TIMELINE + OUTLINER closed again",
          "TIMELINE" not in types and "OUTLINER" not in types, str(types))
    check("re-applied after load: toolbar off again", not v3d.show_region_toolbar)
    check("re-applied after load: grid on (the user's last state)", v3d.overlay.show_floor)
    entry = next(iter(_sidecar()["workspaces"].values()))
    check("sidecar stable after the re-apply", entry["hidden"] == ["OUTLINER", "TIMELINE"], str(entry["hidden"]))
    UiState.uninstall()
    check("uninstall() stops tracking", not UiState._installed and UiState._timer_fn is None)
    return None


PHASES = [phase_seed_and_install, phase_applied, phase_user_changes_persisted,
          phase_fullscreen_skipped, phase_reload, phase_reapplied_after_load]


def _main():
    import bpy

    try:
        phase = PHASES.pop(0)
        print(f"[ui_state_gui_check] {phase.__name__}", flush=True)
        delay = phase()
        if delay is not None and PHASES:
            return delay
    except Exception:
        traceback.print_exc()
        lines.append("FAIL unhandled exception")
    shutil.rmtree(SCRATCH, ignore_errors=True)
    print("\n".join(lines))
    ok = all(ln.startswith("OK") for ln in lines) and lines
    print(f"===RESULT: {'PASS' if ok else 'FAIL'}=== ({sum(1 for ln in lines if ln.startswith('OK'))}/{len(lines)})")
    sys.stdout.flush()
    # Quit under an explicit window override — a bare-timer quit crashes on a NULL context window.
    win = bpy.context.window_manager.windows[0]
    with bpy.context.temp_override(window=win):
        bpy.ops.wm.quit_blender()
    return None


import bpy  # noqa: E402

# persistent: ``read_homefile`` in phase 3 drops non-persistent timers (the driver included).
bpy.app.timers.register(_main, first_interval=1.0, persistent=True)
