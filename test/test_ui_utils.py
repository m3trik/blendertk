"""blendertk ui_utils headless test — the native-menu/editor surface that doesn't need a window.
Run: blender --background --factory-startup --python blendertk/test/test_ui_utils.py

``open_editor`` / ``call_native_menu`` / ``toggle_fullscreen_area`` open / pop / restack real
Blender UI (GUI-only; the last is proven live in ``fullscreen_area_gui_check.py``); here we cover
the parts that ARE headless-decidable — ``menu_exists``, the early-return guards (unknown menu,
no 3D viewport, ``--background``), the editor-name map, and surface resolution.
"""
import sys, os, traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for p in (REPO, os.path.join(MONO, "pythontk")):
    if p not in sys.path:
        sys.path.insert(0, p)

lines = []
def check(name, cond, detail=""):
    lines.append(f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}")

try:
    import blendertk as btk
    from blendertk.ui_utils._ui_utils import UiUtils

    # ---- menu_exists: real VIEW3D menus resolve; a bogus id does not
    check("menu_exists(VIEW3D_MT_add)", btk.menu_exists("VIEW3D_MT_add"))
    check("menu_exists(VIEW3D_MT_edit_mesh)", btk.menu_exists("VIEW3D_MT_edit_mesh"))
    check("menu_exists(VIEW3D_MT_uv_map)", btk.menu_exists("VIEW3D_MT_uv_map"))
    check("menu_exists rejects a bogus id", not btk.menu_exists("VIEW3D_MT_not_a_real_menu"))

    # ---- shared VIEW3D-context helper (call_native_menu's dependency; also used by the cameras
    # slot — consolidated from the per-slot duplicates). factory-startup HAS a VIEW_3D area even
    # under --background, so this returns a context dict (None only if no 3D viewport exists).
    check("get_view3d_context is callable", callable(getattr(btk, "get_view3d_context", None)))
    ctx = btk.get_view3d_context()
    check(
        "get_view3d_context() -> None or a window/area/region context dict",
        ctx is None or (isinstance(ctx, dict) and {"window", "area", "region"} <= set(ctx)),
        f"{type(ctx).__name__}: {sorted(ctx) if isinstance(ctx, dict) else ctx}",
    )

    # ---- call_native_menu is headless-safe: returns None (never faults) under --background,
    # for both a real and a bogus menu id. The actual popup is GUI-only (proven live).
    check("call_native_menu(real) headless-safe -> None", btk.call_native_menu("VIEW3D_MT_add") is None)
    check("call_native_menu(bogus) -> None", btk.call_native_menu("VIEW3D_MT_not_a_real_menu") is None)

    # ---- toggle_fullscreen_area / toggle_window_bars are headless-safe: no window chrome
    # exists under --background, so the guards return without touching any screen op (the
    # real toggles are GUI-proven in fullscreen_area_gui_check.py).
    check("toggle_fullscreen_area headless-safe -> False", btk.toggle_fullscreen_area() is False)
    check("toggle_window_bars headless-safe -> None", btk.toggle_window_bars() is None)

    # ---- open_editor is headless-safe: ``wm.window_new`` polls false under --background, and
    # the contract is "returns the window, or None when it could not be opened" — never a raised
    # RuntimeError. Same guard that carries the GUI fix for a NULL context.window (tentacle's Qt
    # event-pump timer state, where a live click raised "poll() failed, context is incorrect");
    # the opens-for-real half is GUI-only -> open_editor_gui_check.py.
    check("open_editor headless-safe -> None", btk.open_editor("UV Editor") is None)
    check("open_editor(unknown) -> None", btk.open_editor("No Such Editor") is None)

    # ---- editor-name map intact (open_editor's contract)
    editors = btk.get_editor_types()
    check("get_editor_types is a dict", isinstance(editors, dict) and len(editors) > 0)
    for name in ("Outliner", "Properties", "UV Editor", "Shader Editor"):
        check(f"editor map has {name!r}", name in editors)

    # ---- surface resolves both module-level and on UiUtils
    for fn in ("menu_exists", "call_native_menu", "open_editor", "get_editor_types", "toggle_fullscreen_area", "toggle_window_bars"):
        check(f"btk.{fn} is callable", callable(getattr(btk, fn, None)))
        check(f"UiUtils.{fn} is callable", callable(getattr(UiUtils, fn, None)))

except Exception:
    traceback.print_exc()
    lines.append("FAIL unhandled exception")

print("\n".join(lines))
ok = all(l.startswith("OK") for l in lines) and lines
print(f"===RESULT: {'PASS' if ok else 'FAIL'}=== ({sum(1 for l in lines if l.startswith('OK'))}/{len(lines)})")
