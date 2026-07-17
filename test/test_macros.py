"""blendertk hotkey-macros test (engine + key translation + registration).

Viewport-only macros (shading/isolate/frame/...) need a 3D view that ``--background`` lacks, so
they are only checked to no-op safely; the data-mutating macros (mode switch, group, merge, keys,
subsurf) are verified on real geometry. Key-spec parsing is pure logic.

Run: blender --background --factory-startup --python blendertk/test/test_macros.py
"""
import sys, os, shutil, tempfile, traceback, inspect

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for p in (REPO, os.path.join(MONO, "pythontk")):
    if p not in sys.path:
        sys.path.insert(0, p)

# Isolate PresetStore's user tier in a scratch dir for this run — never touch the real
# %LOCALAPPDATA%/uitk store (pythontk.core_utils.user_config.user_config_root honors this).
# The 12b round-trip below calls save_preset/delete_preset, which MOVE the `.active`
# pointer as a side effect (save sets it; delete clears the now-dangling name). Against
# the live tier that silently wipes the artist's active macro preset, and the next launch
# resolves `active or DEFAULT_PRESET` to the shipped all-unbound 'default' — every macro
# hotkey dead, with nothing logged. Mirrors mayatk's `_TempPresetRoot` mixin and
# test_scene_exporter.py's sandbox.
_PRESETS_ROOT = tempfile.mkdtemp(prefix="btk_macro_presets_")
os.environ["UITK_PRESETS_ROOT"] = _PRESETS_ROOT

lines = []
def check(name, cond, detail=""):
    lines.append(f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}")

try:
    import bpy
    from blendertk.edit_utils import macros
    M = macros.Macros

    def reset_scene():
        if bpy.context.view_layer.objects.active and bpy.context.view_layer.objects.active.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.select_all(action="DESELECT")
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)

    # 1. Every macro named in the Maya userSetup spec exists on the Blender Macros class.
    expected = [
        "m_back_face_culling", "m_isolate_selected", "m_wireframe", "m_shading", "m_lighting",
        "m_grid", "m_grid_and_image_planes", "m_cycle_display_state", "m_smooth_preview", "m_multi_component",
        "m_frame", "m_object_selection", "m_vertex_selection", "m_edge_selection", "m_face_selection",
        "m_invert_selection", "m_paste_and_rename", "m_toggle_panels", "m_toggle_UV_select_type",
        "m_merge_vertices", "m_group", "m_set_selected_keys", "m_unset_selected_keys",
    ]
    missing = [m for m in expected if not callable(getattr(M, m, None))]
    check("all userSetup macros present", not missing, str(missing))

    # 1b. The preset sandbox is actually in effect. Guards the whole file: the 12b
    #     round-trip moves the `.active` pointer, so if this store ever resolves to the
    #     live user tier again, running the suite silently unbinds the artist's macros.
    _user_dir = str(M._preset_store().user_dir)
    check("preset store user tier is sandboxed (not the live %LOCALAPPDATA%/uitk store)",
          _user_dir.startswith(_PRESETS_ROOT), _user_dir)

    # 2. Key-spec translation (pure logic).
    check("digit key -> ONE", M._blender_key("1") == "ONE")
    check("letter key -> F", M._blender_key("f") == "F")
    check("function key F12", M._blender_key("F12") == "F12")
    check("special return -> RET", M._blender_key("return") == "RET")

    # 3. Dispatcher operator registers (idempotent).
    M._ensure_operator()
    M._ensure_operator()
    check("dispatcher operator registered", hasattr(bpy.types, "BTK_OT_macro"))

    # 3b. The dispatcher operator is the actual hotkey-invocation path (a keymap item's
    #     properties.macro is what a real keypress drives) — every other step below calls
    #     the macro function directly, so exercise bpy.ops.btk.macro() itself once to prove
    #     the "play" side of the record/play round trip, not just the underlying macros.
    reset_scene()
    bpy.ops.mesh.primitive_cube_add()
    dispatch_cube = bpy.context.active_object
    bpy.ops.object.mode_set(mode="EDIT")
    result = bpy.ops.btk.macro(macro="m_object_selection")
    check("dispatcher operator runs a macro by name",
          result == {"FINISHED"} and dispatch_cube.mode == "OBJECT", str(result))
    bad_result = bpy.ops.btk.macro(macro="m_does_not_exist")
    check("dispatcher operator reports + cancels on an unknown macro", bad_result == {"CANCELLED"})

    # 4. set_macros registers keymap items when an addon keyconfig exists (else skips cleanly).
    kc = bpy.context.window_manager.keyconfigs.addon
    ntargets = len(M._KEYMAP_TARGETS)
    M.set_macros("m_frame, key=f, cat=Display", "m_invert_selection, key=ctl+sht+i, cat=Edit")
    if kc is not None:
        types = {kmi.type for _km, kmi in M._KEYMAPS}
        check("keymap item for 'f'", "F" in types, str(types))
        # each macro is bound into every target keymap so it overrides mode defaults
        check("macro bound into all target keymaps",
              len([1 for _k, kmi in M._KEYMAPS if kmi.type == "F"]) == ntargets,
              f"{ntargets} targets")
        km_names = {km.name for km, _kmi in M._KEYMAPS}
        check("includes Object Mode + Mesh overrides", {"Object Mode", "Mesh", "3D View"} <= km_names, str(km_names))
        inv = next((kmi for _km, kmi in M._KEYMAPS if kmi.properties.macro == "m_invert_selection"), None)
        check("ctl+sht modifiers parsed", inv is not None and inv.ctrl and inv.shift and not inv.alt)
        M.set_macros("m_frame, key=f, cat=Display")  # idempotent re-run
        check("re-run does not duplicate", len([1 for _k, kmi in M._KEYMAPS if kmi.type == "F"]) == ntargets)
        M.remove_macros()
        check("remove_macros clears", M._KEYMAPS == [])
    else:
        check("addon keyconfig unavailable (headless) -> skipped", True, "no kc")

    # 5. Selection-mode macros switch object/edit mode + mesh select mask.
    reset_scene()
    bpy.ops.mesh.primitive_cube_add()
    cube = bpy.context.active_object
    M.m_vertex_selection()
    check("m_vertex_selection -> EDIT + vert mask",
          cube.mode == "EDIT" and tuple(bpy.context.tool_settings.mesh_select_mode) == (True, False, False))
    M.m_face_selection()
    check("m_face_selection -> face mask", tuple(bpy.context.tool_settings.mesh_select_mode) == (False, False, True))
    M.m_multi_component()
    check("m_multi_component -> all masks", tuple(bpy.context.tool_settings.mesh_select_mode) == (True, True, True))
    M.m_object_selection()
    check("m_object_selection -> OBJECT", cube.mode == "OBJECT")

    # 6. m_smooth_preview toggles a Subsurf modifier.
    reset_scene()
    bpy.ops.mesh.primitive_cube_add(); c = bpy.context.active_object
    M.m_smooth_preview()
    check("smooth preview ON (subsurf added)", any(m.type == "SUBSURF" for m in c.modifiers))
    M.m_smooth_preview()
    check("smooth preview OFF (subsurf removed)", not any(m.type == "SUBSURF" for m in c.modifiers))

    # 7. m_group parents the selection under an Empty AT THE SELECTION CENTER, world tfm kept.
    reset_scene()
    bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0)); a = bpy.context.active_object
    bpy.ops.mesh.primitive_cube_add(location=(4, 0, 0)); b = bpy.context.active_object
    a.select_set(True); b.select_set(True)
    M.m_group()
    grp = next((o for o in bpy.data.objects if o.type == "EMPTY" and o.name.startswith("group")), None)
    check("m_group made an Empty", grp is not None)
    check("selection parented to group", grp is not None and a.parent == grp and b.parent == grp)
    check("group Empty at selection center", grp is not None and abs(grp.location.x - 2.0) < 1e-4,
          str(grp.location) if grp else None)
    check("children kept world position",
          abs(a.matrix_world.translation.x) < 1e-4 and abs(b.matrix_world.translation.x - 4.0) < 1e-4)

    # 7b. m_merge_vertices works in OBJECT mode (merges doubles across selected meshes).
    reset_scene()
    bpy.ops.mesh.primitive_plane_add(); pl = bpy.context.active_object  # 4 verts
    import bmesh
    bm = bmesh.new(); bm.from_mesh(pl.data); bm.verts.ensure_lookup_table()
    bm.verts.new(bm.verts[0].co)  # +1 coincident vert
    bm.to_mesh(pl.data); bm.free()
    check("doubled vert added", len(pl.data.vertices) == 5)
    M.m_merge_vertices()  # object mode path
    check("object-mode merge removed the double", len(pl.data.vertices) == 4, str(len(pl.data.vertices)))

    # 8. m_cycle_display_state cycles object display.
    reset_scene()
    bpy.ops.mesh.primitive_cube_add(); o = bpy.context.active_object
    o.select_set(True); bpy.context.view_layer.objects.active = o
    M.m_cycle_display_state()
    check("cycle -> WIRE", o.display_type == "WIRE")
    M.m_cycle_display_state()
    check("cycle -> BOUNDS", o.display_type == "BOUNDS")
    M.m_cycle_display_state()
    check("cycle -> TEXTURED (reversible)", o.display_type == "TEXTURED")

    # 9. animation keys set/unset on transform channels (Action-API-agnostic: a deleted key
    #    can't be deleted again).
    reset_scene()
    bpy.ops.mesh.primitive_cube_add(); k = bpy.context.active_object
    k.select_set(True)
    bpy.context.scene.frame_set(1)
    M.m_set_selected_keys()
    check("m_set_selected_keys keyed", k.animation_data is not None and k.animation_data.action is not None)
    M.m_unset_selected_keys()

    def _has_loc_key():
        try:
            return k.keyframe_delete(data_path="location")  # True if one existed (and just removed it)
        except RuntimeError:
            return False
    check("m_unset_selected_keys removed frame-1 keys", _has_loc_key() is False)

    # 10. Viewport-only macros no-op safely (no 3D view in --background).
    reset_scene()
    bpy.ops.mesh.primitive_cube_add(); bpy.context.active_object.select_set(True)
    try:
        for fn in (M.m_back_face_culling, M.m_wireframe, M.m_shading, M.m_lighting,
                   M.m_grid, M.m_grid_and_image_planes, M.m_isolate_selected, M.m_frame,
                   M.m_toggle_panels, M.m_toggle_UV_select_type, M.m_paste_and_rename):
            fn()
        check("viewport macros no-op safely headless", True)
    except Exception as e:
        check("viewport macros no-op safely headless", False, repr(e))

    # 10d. m_grid toggles the floor grid (+ its axis lines) and RETURNS the applied state;
    # m_grid_and_image_planes drives that SAME toggle — the grid leads and the image-empties
    # follow it. mayatk's counterpart used to let the image planes lead, with cmds.grid called
    # from inside the image-plane loop, so a scene with none left the grid untouched entirely.
    reset_scene()
    _area, space = M._view3d()
    if space:
        space.overlay.show_floor = True
        state = M.m_grid()
        check("m_grid toggles the floor grid + axes off, returning the applied state",
              state is False and space.overlay.show_floor is False
              and space.overlay.show_axis_x is False and space.overlay.show_axis_y is False,
              f"returned {state!r}")
        state = M.m_grid()
        check("m_grid toggles the grid back on",
              state is True and space.overlay.show_floor is True)

        img = bpy.data.objects.new("ref_img", None)
        img.empty_display_type = "IMAGE"
        bpy.context.scene.collection.objects.link(img)

        space.overlay.show_floor = True
        M.m_grid_and_image_planes()
        check("grid leads: image-empties follow it OFF",
              space.overlay.show_floor is False and img.hide_viewport is True)
        M.m_grid_and_image_planes()
        check("grid leads: image-empties follow it ON",
              space.overlay.show_floor is True and img.hide_viewport is False)

        # The no-image-plane case is the regression itself: the grid must still toggle.
        reset_scene()
        space.overlay.show_floor = True
        M.m_grid_and_image_planes()
        check("grid toggles with NO image planes in the scene",
              space.overlay.show_floor is False)
        space.overlay.show_floor = True
    else:
        check("m_grid toggles the floor grid + axes off, returning the applied state", False, "no VIEW_3D")

    # 10b. m_toggle_panels' menu-bar half (btk.toggle_window_bars) is GUI-only — the topbar is a
    # global area that --background has no window for. Headless the bars sit out and the viewport
    # regions must still lead themselves (their pre-bars behavior), so the macro stays useful in
    # every context. The bars<->regions sync itself is GUI-proven in fullscreen_area_gui_check.py.
    _area, space = M._view3d()
    if space:
        space.show_region_header = True
        M.m_toggle_panels()
        check("m_toggle_panels: regions lead when the bars sit out (headless)",
              space.show_region_header is False
              and space.show_region_toolbar is False
              and space.show_region_ui is False)
        M.m_toggle_panels()
        check("m_toggle_panels: regions toggle back", space.show_region_header is True)
        # toggle_panels=False -> regions untouched (the bars-only half; no-op headless)
        M.m_toggle_panels(toggle_panels=False)
        check("m_toggle_panels(toggle_panels=False) leaves the regions alone",
              space.show_region_header is True)
        # toggle_menu=False -> the pre-bars behavior, regions only
        M.m_toggle_panels(toggle_menu=False)
        check("m_toggle_panels(toggle_menu=False) still toggles the regions",
              space.show_region_header is False)
        space.show_region_header = True
    else:
        check("m_toggle_panels: regions lead when the bars sit out (headless)", False, "no VIEW_3D")

    # 10c. m_toggle_panels sits out with no context window rather than crashing: assigning
    # show_region_* bare fires ED_area_init, which dereferences it (a hard crash = the user's
    # unsaved scene). Forced via temp_override(window=None), the same way test_bridges.py
    # reproduces the windowless Qt-timer context.
    if space:
        space.show_region_header = True
        try:
            with bpy.context.temp_override(window=None):
                M.m_toggle_panels()
            check("m_toggle_panels no-ops (no crash) with no context window",
                  space.show_region_header is True)
        except Exception as e:
            check("m_toggle_panels no-ops (no crash) with no context window", False, repr(e))

    # Signature parity with mayatk's m_toggle_panels(toggle_menu=True, toggle_panels=True).
    _sig = inspect.signature(M.m_toggle_panels).parameters
    check("m_toggle_panels mirrors mayatk's toggle_menu/toggle_panels defaults",
          _sig["toggle_menu"].default is True and _sig["toggle_panels"].default is True,
          str(dict(_sig)))

    # 11. Management API (the single source of truth behind the Macro Manager
    # editor, ``Macros.show_editor``) — pure introspection, no bpy required for
    # these, but exercised here alongside the rest of the engine.
    available = M.list_available_macros()
    check("list_available_macros finds every m_* macro", set(expected) <= set(available), str(sorted(available)))
    check("macro_label humanizes + preserves acronyms",
          M.macro_label("m_back_face_culling") == "Back Face Culling"
          and M.macro_label("m_toggle_UV_select_type") == "Toggle UV Select Type")
    check("macro_category derives from the defining *Macros mixin",
          M.macro_category("m_back_face_culling") == "Display"
          and M.macro_category("m_group") == "Edit"
          and M.macro_category("m_object_selection") == "Selection"
          and M.macro_category("m_set_selected_keys") == "Animation"
          and M.macro_category("m_toggle_panels") == "UI")
    cats = M.list_categories()
    check("list_categories lists every mixin-derived category",
          {"Display", "Edit", "Selection", "Animation", "UI"} <= set(cats), str(cats))
    check("macro_help returns the macro's docstring",
          "Toggle Back-Face Culling" in M.macro_help("m_back_face_culling"))

    # Key-format round trip (pure string logic, no bpy).
    check("qt_sequence_to_maya_key / maya_key_to_qt_sequence round trip",
          M.qt_sequence_to_maya_key("Ctrl+Shift+I") == "ctl+sht+i"
          and M.maya_key_to_qt_sequence("ctl+sht+i") == "Ctrl+Shift+I")
    check("qt_sequence_to_maya_key with no non-modifier key -> empty", M.qt_sequence_to_maya_key("Ctrl") == "")

    # 12. get_current_bindings / apply_bindings / clear_hotkey / find_conflicts round trip —
    # only meaningful with an addon keyconfig (headless --background may have none; skip cleanly
    # like section 4 does).
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc is not None:
        M.remove_macros()
        M.set_macros("m_frame, key=f, cat=Display", "m_invert_selection, key=ctl+sht+i, cat=Edit")
        bindings = M.get_current_bindings()
        check("get_current_bindings reflects set_macros (key + stashed category)",
              bindings["m_frame"] == {"key": "f", "cat": "Display"}
              and bindings["m_invert_selection"] == {"key": "ctl+sht+i", "cat": "Edit"},
              str({k: bindings[k] for k in ("m_frame", "m_invert_selection")}))
        check("get_current_bindings falls back to the mixin category when unbound",
              bindings["m_group"] == {"key": "", "cat": "Edit"}, str(bindings["m_group"]))

        conflicts = M.find_conflicts({"m_frame": {"key": "f"}, "m_shading": {"key": "f"}})
        check("find_conflicts flags two macros sharing a key",
              conflicts.get("f") == ["m_frame", "m_shading"] or set(conflicts.get("f", [])) == {"m_frame", "m_shading"})

        M.clear_hotkey("m_frame")
        cleared = M.get_current_bindings()
        check("clear_hotkey unbinds just that macro", cleared["m_frame"]["key"] == ""
              and cleared["m_invert_selection"]["key"] == "ctl+sht+i")

        M.apply_bindings({"m_frame": {"key": "ctl+f", "cat": "UI"}, "m_invert_selection": {"key": ""}})
        applied = M.get_current_bindings()
        check("apply_bindings sets a new key + unbinds a falsy-key entry",
              applied["m_frame"] == {"key": "ctl+f", "cat": "UI"}
              and applied["m_invert_selection"]["key"] == "",
              str({k: applied[k] for k in ("m_frame", "m_invert_selection")}))

        M.remove_macros()

        # 12b. Preset persistence + apply_saved_macros (tentacle's TclBlender launch entry point) —
        # writes/reads real files under the user PresetStore tier, cleaned up afterward.
        try:
            saved_path = M.save_preset("_test_roundtrip", {"m_frame": {"key": "ctl+alt+f", "cat": "Display"}})
            check("save_preset writes a file", os.path.isfile(saved_path), saved_path)
            loaded = M.load_preset("_test_roundtrip")
            check("load_preset round-trips the saved bindings",
                  loaded.get("m_frame") == {"key": "ctl+alt+f", "cat": "Display"}, str(loaded))

            M.remove_macros()
            M.apply_saved_macros("_test_roundtrip")
            after_apply = M.get_current_bindings()
            check("apply_saved_macros applies a named preset's bindings",
                  after_apply["m_frame"] == {"key": "ctl+alt+f", "cat": "Display"},
                  str(after_apply["m_frame"]))
        finally:
            M.remove_macros()
            M.delete_preset("_test_roundtrip")
            check("delete_preset cleans up the test preset", not M._preset_store().exists("_test_roundtrip"))

        default_bindings = M.load_preset(M.DEFAULT_PRESET)
        check("shipped 'default' preset is all-unbound (no bindings)",
              default_bindings == {}, str(sorted(default_bindings)))

        # 12c. set_macro must self-register the dispatcher operator. The preset
        # path (apply_bindings -> set_macro) doesn't go through set_macros —
        # the only prior _ensure_operator caller — so a fresh session applying
        # a preset produced keymap items that did NOTHING on keypress.
        op = getattr(bpy.types, "BTK_OT_macro", None)
        if op is not None:
            bpy.utils.unregister_class(op)
        M.set_macro("m_frame", key="ctl+alt+p", cat="Display")
        check("set_macro re-registers the btk.macro operator (preset path)",
              hasattr(bpy.types, "BTK_OT_macro"))
        M.remove_macros()
    else:
        check("addon keyconfig unavailable (headless) -> management-API round trip skipped", True, "no kc")

except Exception as e:
    traceback.print_exc()
    check("macros test raised", False, repr(e))

shutil.rmtree(_PRESETS_ROOT, ignore_errors=True)

passed = sum(1 for line in lines if line.startswith("OK"))
for line in lines:
    print(line)
result = "PASS" if all(line.startswith("OK") for line in lines) else "FAIL"
print(f"===RESULT: {result}=== ({passed}/{len(lines)})")
