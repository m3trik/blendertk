"""blendertk ui_utils headless test — the native-menu/editor surface that doesn't need a window.
Run: blender --background --factory-startup --python blendertk/test/test_ui_utils.py

``open_editor`` / ``call_native_menu`` / ``toggle_fullscreen_area`` open / pop / restack real
Blender UI (GUI-only; the last is proven live in ``fullscreen_area_gui_check.py``); here we cover
the parts that ARE headless-decidable — ``menu_exists``, the early-return guards (unknown menu,
no 3D viewport, ``--background``), the editor-name map, and surface resolution.
"""
import os
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
    lines.append(f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + str(detail)) if detail else ''}")

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

    # ---- close_area is headless-safe: ``screen.area_close`` blows the C stack under
    # --background on 5.1 (any area; the process dies with rc 127, no Python frame to catch),
    # so the primitive returns False there instead of running it. Reached for real by
    # tentacle's register -> ScriptConsole.restore -> QtDock.dock in a headless run.
    _win = btk.main_window()
    _area = next(a for a in _win.screen.areas if a.type != "VIEW_3D")
    check("close_area headless-safe -> False (area kept)",
          btk.close_area(_win, _area) is False and _area in _win.screen.areas[:])

    # ---- editor-name map intact (open_editor's contract)
    editors = btk.get_editor_types()
    check("get_editor_types is a dict", isinstance(editors, dict) and len(editors) > 0)
    for name in ("Outliner", "Properties", "UV Editor", "Shader Editor"):
        check(f"editor map has {name!r}", name in editors)

    # ---- surface resolves both module-level and on UiUtils
    for fn in ("menu_exists", "call_native_menu", "open_editor", "get_editor_types", "toggle_fullscreen_area", "toggle_window_bars"):
        check(f"btk.{fn} is callable", callable(getattr(btk, fn, None)))
        check(f"UiUtils.{fn} is callable", callable(getattr(UiUtils, fn, None)))

    # ---- reveal_in_outliner: selection is primary; scroll is best-effort -----
    import bpy

    bpy.ops.mesh.primitive_cube_add()
    rv_a = bpy.context.active_object
    rv_a.name = "RevealA"
    bpy.ops.mesh.primitive_cube_add()
    rv_b = bpy.context.active_object
    rv_b.name = "RevealB"
    rv_a.select_set(True)
    UiUtils.reveal_in_outliner(["RevealB"])
    check("reveal_in_outliner: target selected + active", rv_b.select_get() and bpy.context.view_layer.objects.active is rv_b)
    check("reveal_in_outliner: prior selection replaced", not rv_a.select_get())
    UiUtils.reveal_in_outliner(["NoSuchObject"])
    check("reveal_in_outliner: unknown name is a clean no-op", bpy.context.view_layer.objects.active is rv_b)
    # From tentacle's Qt event pump bpy.context.window is None, and the screen-context
    # selected_objects reads EMPTY while objects are selected -- the reveal must still
    # replace the selection (the shot sequencer's clip menu calls it from there).
    rv_a.select_set(True)
    with bpy.context.temp_override(selected_objects=[]):
        UiUtils.reveal_in_outliner(["RevealB"])
    check("reveal_in_outliner: replaces the selection with no context window", rv_b.select_get() and not rv_a.select_get())
    check("btk.reveal_in_outliner mirrors mtk (callable)", callable(getattr(btk, "reveal_in_outliner", None)))

    # ---- NodeIcons: Object.type -> uitk icon name (mirror of mtk.NodeIcons) --
    from blendertk.ui_utils.node_icons import NodeIcons

    check("NodeIcons.icon_name_for_type MESH -> cube", NodeIcons.icon_name_for_type("MESH") == "cube")
    check("NodeIcons.icon_name_for_type unknown -> None", NodeIcons.icon_name_for_type("WHATEVER") is None)
    check("NodeIcons.icon_name_for_node resolves a scene object", NodeIcons.icon_name_for_node("RevealA") == "cube")
    bpy.ops.object.camera_add()
    bpy.context.active_object.name = "RevealCam"
    check("NodeIcons.icon_name_for_node CAMERA -> camera", NodeIcons.icon_name_for_node("RevealCam") == "camera")
    check("NodeIcons.icon_name_for_node missing -> None", NodeIcons.icon_name_for_node("nope") is None)
    check("btk.NodeIcons registered", getattr(btk, "NodeIcons", None) is NodeIcons)

    # ---- get_operator / invoke_operator_dialog: registration proven, not assumed ----
    import bpy

    check("get_operator resolves a registered operator", callable(UiUtils.get_operator("object.select_all")))
    check(
        "get_operator: an unregistered id is None (bpy.ops resolves any name)",
        UiUtils.get_operator("wm.not_a_real_operator") is None,
    )
    check("get_operator: an id without a group is None", UiUtils.get_operator("select_all") is None)
    check(
        "invoke_operator_dialog: an unregistered id is None",
        UiUtils.invoke_operator_dialog("wm.not_a_real_operator") is None,
    )
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    bpy.ops.mesh.primitive_cube_add()
    tool_mesh = bpy.context.view_layer.objects.active
    bpy.ops.curve.primitive_bezier_curve_add(location=(4, 0, 0))
    tool_curve = bpy.context.view_layer.objects.active
    btk.CoreUtils.select([tool_mesh, tool_curve])
    with bpy.context.temp_override(window=None):
        invoked = UiUtils.invoke_operator_dialog("object.select_all", action="DESELECT")
    check(
        "invoke_operator_dialog runs windowless (the Qt-pump state)",
        invoked == {"FINISHED"} and not btk.CoreUtils.selected_objects(),
        f"{invoked}",
    )

    # ---- viewport tools: get_tool_types / set_tool / current_tool (mayatk's names) -------
    tools = UiUtils.get_tool_types()
    check(
        "get_tool_types: Multi-Cut is the knife, gated on mesh Edit Mode",
        tools.get("Multi-Cut") == ("builtin.knife", "MESH"),
    )
    tools["Multi-Cut"] = None
    check("get_tool_types returns a copy", UiUtils.get_tool_types()["Multi-Cut"] == ("builtin.knife", "MESH"))
    btk.CoreUtils.select([tool_curve])
    try:
        UiUtils.set_tool("Multi-Cut")
        check("set_tool: a mesh-only tool with no mesh raises ValueError", False)
    except ValueError as error:
        check("set_tool: a mesh-only tool with no mesh raises ValueError", "mesh" in str(error), str(error))
    check("set_tool: the refusal leaves the mode alone", tool_curve.mode == "OBJECT", tool_curve.mode)
    btk.CoreUtils.select([tool_mesh])
    try:
        UiUtils.set_tool("builtin.knife")  # a raw idname keeps the registry's mode gate
    except RuntimeError:
        pass  # --background: keymap-bearing tools fail their setup (viewport_tool_check.py)
    check("set_tool: a mesh-only tool enters Edit Mode first", tool_mesh.mode == "EDIT", tool_mesh.mode)
    btk.CoreUtils.ensure_object_mode()
    try:
        UiUtils.set_tool("Measure")
        measured = True
    except RuntimeError:
        measured = bpy.app.background  # the same headless setup caveat
    check("set_tool: a mode-agnostic tool forces no mode", measured and tool_mesh.mode == "OBJECT")
    if not bpy.app.background:
        check("current_tool names the tool just set", UiUtils.current_tool() == "Measure", UiUtils.current_tool())
    current = UiUtils.current_tool()
    check("current_tool: a name, an idname or None", current is None or isinstance(current, str), repr(current))

    # ---- annotate / open_pencil: each host's own (mayatk's names; tentacle's utilities panel)
    import bpy

    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    try:
        armed = UiUtils.annotate()
        check("annotate arms the Annotate tool and names it", armed == "Annotation", repr(armed))
        if not bpy.app.background:
            check("annotate: the Annotate tool is current", UiUtils.current_tool() == "Annotation")
    except RuntimeError as error:
        # --background: keymap-bearing tools fail their setup (viewport_tool_check.py)
        check("annotate refuses only headless", bpy.app.background, str(error))
    names_before = {o.name for o in bpy.data.objects}
    pencil = UiUtils.open_pencil()
    added = {o.name for o in bpy.data.objects} - names_before
    check(
        "open_pencil adds one empty Grease Pencil object and returns it",
        pencil is not None
        and pencil.type in ("GREASEPENCIL", "GPENCIL")
        and added == {pencil.name},
        f"{getattr(pencil, 'type', pencil)} added={added}",
    )

    # ---- get_editor_categories + mayatk's editor names (the shared Editors panel's calls)
    _cats = UiUtils.get_editor_categories()
    _listed = [n for names in _cats.values() for n in names]
    check(
        "get_editor_categories: Blender's four groups",
        list(_cats)
        == ["General Editors", "Modeling Editors", "Animation Editors", "Rendering Editors"],
        f"{list(_cats)}",
    )
    check(
        "get_editor_categories: every listed editor is a registered type",
        _listed and set(_listed) <= set(UiUtils.get_editor_types()),
        f"{_listed}",
    )
    _aliases = {
        "Attribute Editor": "PROPERTIES",
        "Tool Settings": "PROPERTIES",
        "Channel Box": "PROPERTIES",
        "Layer Editor": "OUTLINER",
        "Node Editor": "ShaderNodeTree",
        "Command Line": "CONSOLE",
    }
    check(
        "get_editor_categories lists none of mayatk's alias names",
        not set(_listed) & set(_aliases),
        f"{set(_listed) & set(_aliases)}",
    )
    for _alias, _ui_type in _aliases.items():
        check(
            f"mayatk's {_alias!r} opens Blender's {_ui_type}",
            UiUtils.get_editor_types().get(_alias) == _ui_type,
        )

    # ---- browse_directory: a modal dialog (GUI-only), so its presence is what is pinned here
    check("UiUtils.browse_directory mirrors mtk (callable)", callable(getattr(UiUtils, "browse_directory", None)))

    # ---- open_preferences: the tab is set before the editor opens (GUI-only, so
    # headless the operator's refusal is what reaches the caller, as RuntimeError)
    import bpy

    try:
        UiUtils.open_preferences("nowhere")
        check("open_preferences: an unknown section raises ValueError", False)
    except ValueError:
        check("open_preferences: an unknown section raises ValueError", True)
    _prior_section = bpy.context.preferences.active_section
    try:
        UiUtils.open_preferences("hotkeys")
        _opened = True
    except RuntimeError:
        _opened = bpy.app.background  # no window to open in
    check(
        "open_preferences('hotkeys') shows the Keymap tab",
        _opened and bpy.context.preferences.active_section == "KEYMAP",
        bpy.context.preferences.active_section,
    )
    bpy.context.preferences.active_section = _prior_section

    # mayatk's rendering editors open Properties tabs (the shared Rendering panel)
    from blendertk.ui_utils._ui_utils import EDITOR_TYPES

    check(
        "mayatk's rendering editors open Properties tabs",
        all(EDITOR_TYPES[name] == "PROPERTIES" for name in UiUtils._PROPERTIES_TABS)
        and UiUtils._PROPERTIES_TABS
        == {"Render Settings": "RENDER", "Render Setup": "VIEW_LAYER", "Rendering Flags": "OBJECT"},
    )

except Exception:
    traceback.print_exc()
    lines.append("FAIL unhandled exception")

print("\n".join(lines))
ok = all(line.startswith("OK") for line in lines) and lines
print(f"===RESULT: {'PASS' if ok else 'FAIL'}=== ({sum(1 for line in lines if line.startswith('OK'))}/{len(lines)})")
