"""blendertk hotkey-macros test (engine + key translation + registration).

Viewport-only macros (shading/isolate/frame/...) need a 3D view that ``--background`` lacks, so
they are only checked to no-op safely; the data-mutating macros (mode switch, group, merge, keys,
subsurf) are verified on real geometry. Key-spec parsing is pure logic.

Run: blender --background --factory-startup --python blendertk/test/test_macros.py
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
        "m_grid_and_image_planes", "m_cycle_display_state", "m_smooth_preview", "m_multi_component",
        "m_frame", "m_object_selection", "m_vertex_selection", "m_edge_selection", "m_face_selection",
        "m_invert_selection", "m_paste_and_rename", "m_toggle_panels", "m_toggle_UV_select_type",
        "m_merge_vertices", "m_group", "m_set_selected_keys", "m_unset_selected_keys",
    ]
    missing = [m for m in expected if not callable(getattr(M, m, None))]
    check("all userSetup macros present", not missing, str(missing))

    # 2. Key-spec translation (pure logic).
    check("digit key -> ONE", M._blender_key("1") == "ONE")
    check("letter key -> F", M._blender_key("f") == "F")
    check("function key F12", M._blender_key("F12") == "F12")
    check("special return -> RET", M._blender_key("return") == "RET")

    # 3. Dispatcher operator registers (idempotent).
    M._ensure_operator()
    M._ensure_operator()
    check("dispatcher operator registered", hasattr(bpy.types, "BTK_OT_macro"))

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
                   M.m_grid_and_image_planes, M.m_isolate_selected, M.m_frame,
                   M.m_toggle_panels, M.m_toggle_UV_select_type, M.m_paste_and_rename):
            fn()
        check("viewport macros no-op safely headless", True)
    except Exception as e:
        check("viewport macros no-op safely headless", False, repr(e))

except Exception as e:
    traceback.print_exc()
    check("macros test raised", False, repr(e))

passed = sum(1 for line in lines if line.startswith("OK"))
for line in lines:
    print(line)
result = "PASS" if all(line.startswith("OK") for line in lines) else "FAIL"
print(f"===RESULT: {result}=== ({passed}/{len(lines)})")
