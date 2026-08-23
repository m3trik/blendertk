"""blendertk ui_utils.ui_state headless test — ``btk.UiState`` (session-persistent UI state).
Run: blender --background --factory-startup --python blendertk/test/test_ui_state.py

Covers what is decidable without a window: the ``hidden`` merge rule, the data-driven
``show_*`` flag snapshot over the factory screen (present even under ``--background``), the
snapshot -> mutate -> apply round-trip on plain overlay/shading flags, diff-first application,
tolerance for unknown keys, sidecar load/save/clear under a scratch dir, the headless guards
(``install`` / ``close_hidden`` no-op), and the ``btk`` surface. The area-closing and file-load
re-apply halves need a real screen -> ``ui_state_gui_check.py``.
"""
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


def check(name, cond, detail=""):
    lines.append(f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}")


SCRATCH = os.path.join(HERE, "temp_tests", f"ui_state_{os.getpid()}")

try:
    import bpy
    import blendertk as btk
    from blendertk.ui_utils.ui_state import UiState

    os.makedirs(SCRATCH, exist_ok=True)
    UiState._state_dir_override = SCRATCH

    # ---- surface
    check("btk.UiState resolves", getattr(btk, "UiState", None) is UiState)
    for fn in ("install", "uninstall", "load", "save", "clear", "snapshot_workspace",
               "apply_workspace", "apply_spaces", "close_hidden", "state_path"):
        check(f"UiState.{fn} is callable", callable(getattr(UiState, fn, None)))

    # ---- hidden merge rule: (saved | (loaded - live)) - live
    m = UiState._merge_hidden
    check("closed out of the loaded layout -> hidden",
          m([], ["VIEW_3D", "TIMELINE"], ["VIEW_3D"]) == ["TIMELINE"])
    check("stays hidden while off screen (Load UI off / already applied)",
          m(["TIMELINE"], ["VIEW_3D"], ["VIEW_3D"]) == ["TIMELINE"])
    check("released when live again",
          m(["TIMELINE"], ["VIEW_3D"], ["VIEW_3D", "TIMELINE"]) == [])
    check("an added editor never reads as hidden",
          m([], ["VIEW_3D"], ["VIEW_3D", "INFO"]) == [])
    check("sorted, deduplicated",
          m(["B", "A"], ["A", "C"], []) == ["A", "B", "C"])

    # ---- sidecar path / load skeleton / save / clear
    path = UiState.state_path()
    check("state_path under the override dir",
          path is not None and os.path.dirname(path) == SCRATCH and path.endswith(".json"), str(path))
    state = UiState.load()
    check("load() with no file -> skeleton", state == {"version": UiState.VERSION, "workspaces": {}}, str(state))
    with open(path, "w", encoding="utf-8") as f:
        f.write("{not json")
    check("load() with a corrupt file -> skeleton", UiState.load()["workspaces"] == {})
    check("save(explicit) writes", UiState.save({"version": 1, "workspaces": {"X": {"hidden": ["TIMELINE"], "spaces": {}}}}))
    check("load() round-trips", UiState.load()["workspaces"]["X"]["hidden"] == ["TIMELINE"])
    check("clear() removes the sidecar", UiState.clear() and not os.path.exists(path))

    # ---- snapshot over the factory screen
    window = btk.main_window()
    check("factory screen has a main window + VIEW_3D (headless)",
          window is not None and "VIEW_3D" in UiState._ui_types(window.screen),
          str(window and UiState._ui_types(window.screen)))
    spaces = UiState.snapshot_spaces(window.screen)
    v3d = spaces.get("VIEW_3D", {})
    check("VIEW_3D snapshot carries overlay.show_floor (the grid)", "overlay.show_floor" in v3d)
    check("VIEW_3D snapshot carries overlay.show_axis_x", "overlay.show_axis_x" in v3d)
    check("VIEW_3D snapshot carries shading.show_xray", "shading.show_xray" in v3d)
    check("VIEW_3D snapshot carries show_region_toolbar", "show_region_toolbar" in v3d)
    check("VIEW_3D snapshot carries show_gizmo", "show_gizmo" in v3d)
    check("every flag is a show_* bool", all(
        isinstance(v, bool) and k.rsplit(".", 1)[-1].startswith("show_")
        for flags in spaces.values() for k, v in flags.items()))
    check("no enum smuggled in (shading.type)", "shading.type" not in v3d)
    check("other editors snapshot too (OUTLINER show_* flags)", bool(spaces.get("OUTLINER")), str(sorted(spaces)))

    # ---- workspace entry: hidden merged against the loaded layout + saved flags carried
    UiState._state = {"version": 1, "workspaces": {}}
    UiState._loaded = {window.workspace.name: UiState._ui_types(window.screen) + ["TIMELINE_GHOST"]}
    entry = UiState.snapshot_workspace(window, saved={"hidden": ["KEPT"], "spaces": {"GONE": {"show_x": True}}})
    check("snapshot_workspace: hidden = saved | (loaded - live)", entry["hidden"] == ["KEPT", "TIMELINE_GHOST"], str(entry["hidden"]))
    check("snapshot_workspace: off-screen editor flags ride along", entry["spaces"].get("GONE") == {"show_x": True})
    check("snapshot_workspace: live editors read fresh", "VIEW_3D" in entry["spaces"])
    UiState._loaded = {}

    # ---- apply round-trip on plain (non-region) flags; diff-first; unknown keys tolerated
    v3d_area = next(a for a in window.screen.areas if a.ui_type == "VIEW_3D")
    space = v3d_area.spaces.active
    floor0, ax0, xray0 = space.overlay.show_floor, space.overlay.show_axis_x, space.shading.show_xray
    saved_flags = {"VIEW_3D": {"overlay.show_floor": not floor0, "overlay.show_axis_x": not ax0,
                               "shading.show_xray": not xray0, "overlay.show_no_such_flag": True,
                               "nosuch.show_thing": True}}
    n = UiState.apply_spaces(window.screen, saved_flags)
    check("apply_spaces flips the three differing flags", n == 3, f"changed={n}")
    check("overlay.show_floor applied", space.overlay.show_floor == (not floor0))
    check("overlay.show_axis_x applied", space.overlay.show_axis_x == (not ax0))
    check("shading.show_xray applied", space.shading.show_xray == (not xray0))
    n2 = UiState.apply_spaces(window.screen, saved_flags)
    check("apply_spaces is diff-first (second pass changes nothing)", n2 == 0, f"changed={n2}")
    snap2 = UiState.snapshot_spaces(window.screen)["VIEW_3D"]
    check("snapshot reflects the applied flags", snap2["overlay.show_floor"] == (not floor0)
          and snap2["shading.show_xray"] == (not xray0))
    # restore
    UiState.apply_spaces(window.screen, {"VIEW_3D": {"overlay.show_floor": floor0, "overlay.show_axis_x": ax0,
                                                     "shading.show_xray": xray0}})
    check("restored", space.overlay.show_floor == floor0 and space.shading.show_xray == xray0)

    # ---- apply_workspace headless: no context window -> refuses (region setters would crash)
    check("apply_workspace without a context window -> False (headless)",
          bpy.context.window is not None or UiState.apply_workspace(window) is False)

    # ---- headless guards
    check("install() is a no-op under --background", UiState.install() is False and not UiState._installed)
    check("close_hidden() is a no-op under --background", UiState.close_hidden(window, ["OUTLINER"]) == 0
          and "OUTLINER" in UiState._ui_types(window.screen))
    UiState.uninstall()
    check("uninstall() is safe when never installed", not UiState._installed)

except Exception:
    traceback.print_exc()
    lines.append("FAIL unhandled exception")
finally:
    shutil.rmtree(SCRATCH, ignore_errors=True)

print("\n".join(lines))
ok = all(ln.startswith("OK") for ln in lines) and lines
print(f"===RESULT: {'PASS' if ok else 'FAIL'}=== ({sum(1 for ln in lines if ln.startswith('OK'))}/{len(lines)})")
