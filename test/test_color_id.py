"""blendertk Color ID engine headless test (material / object-color / vertex channels).
Run: blender --background --factory-startup --python blendertk/test/test_color_id.py
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
    lines.append(f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + str(detail)) if detail else ''}")

try:
    import bpy
    from blendertk.display_utils.color_id import ColorId as CM

    def reset_scene():
        if (bpy.context.view_layer.objects.active
                and bpy.context.view_layer.objects.active.mode != "OBJECT"):
            bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.select_all(action="DESELECT")
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)

    reset_scene()
    bpy.ops.mesh.primitive_cube_add(); a = bpy.context.active_object; a.name = "A"
    bpy.ops.mesh.primitive_cube_add(location=(3, 0, 0)); b = bpy.context.active_object; b.name = "B"
    RED = (0.8, 0.1, 0.1)

    # apply RED to A across all three channels
    CM.apply_color([a], RED, apply_to_object=True, apply_to_material=True, apply_to_vertex=True)
    check("object color set", abs(a.color[0] - 0.8) < 1e-3, str(tuple(a.color)[:3]))
    check("ID material assigned", bool(a.active_material) and a.active_material.name.startswith("ID_"),
          a.active_material.name if a.active_material else None)
    check("vertex color attribute created", len(a.data.color_attributes) > 0)

    # read-back per channel
    check("get_object_color", CM.get_object_color(a) is not None and abs(CM.get_object_color(a)[0] - 0.8) < 1e-3)
    check("get_material_color (Principled base)", CM.get_material_color(a) is not None and abs(CM.get_material_color(a)[0] - 0.8) < 1e-3)
    avg = CM.get_average_vertex_color(a)
    check("get_average_vertex_color", avg is not None and abs(avg[0] - 0.8) < 0.02, str(avg))

    # select-by-color (object channel) finds only A
    found = CM.get_objects_by_color(RED, check_wireframe_color=True)
    check("select-by-color finds A only", [o.name for o in found] == ["A"], str([o.name for o in found]))

    # other color doesn't match
    none_found = CM.get_objects_by_color((0.0, 0.0, 1.0), check_wireframe_color=True)
    check("select-by-color blue finds nothing", none_found == [], str([o.name for o in none_found]))

    # reset clears all three channels (and leaves non-ID materials alone)
    b.data.materials.clear()
    keep = bpy.data.materials.new("KeepMe"); b.data.materials.append(keep)
    CM.reset_colors([a, b])
    check("reset clears object color", abs(a.color[0] - 1.0) < 1e-3, str(tuple(a.color)[:3]))
    check("reset removes the ID material", not (a.active_material and a.active_material.name.startswith("ID_")))
    check("reset removes vertex colors", len(a.data.color_attributes) == 0)
    check("reset keeps non-ID materials", any(m and m.name == "KeepMe" for m in b.data.materials))

    # random color path (color=None) doesn't raise and sets something
    CM.apply_color([b], None, apply_to_object=True)
    check("random color applies", CM.get_object_color(b) is not None)

    # ── assigned-vs-unset object color ────────────────────────────────────────
    # Blender has no "use object color" flag (Maya's useOutlinerColor); the untouched
    # default (white) is the only available "unset" signal.
    reset_scene()
    bpy.ops.mesh.primitive_cube_add(); a = bpy.context.active_object; a.name = "A"
    bpy.ops.mesh.primitive_cube_add(location=(3, 0, 0)); b = bpy.context.active_object; b.name = "B"
    CM.apply_color([a], RED, apply_to_object=True)
    check("has_object_color true for an assigned object", CM.has_object_color(a) is True)
    check("has_object_color false for an untouched object", CM.has_object_color(b) is False)
    CM.reset_colors([a])
    check("has_object_color false after reset", CM.has_object_color(a) is False)
    CM.apply_color([a], RED, apply_to_object=True)  # restore for the select-by check below

    # select-by-color on a white swatch must not sweep up every untouched object
    white_hits = CM.get_objects_by_color((1.0, 1.0, 1.0), check_wireframe_color=True)
    check("select-by-color white skips unassigned objects", white_hits == [],
          str([o.name for o in white_hits]))

    # ── viewport display sync (the applied color must be VISIBLE) ─────────────
    from blendertk.core_utils._core_utils import CoreUtils

    areas = CoreUtils.get_areas("VIEW_3D")
    check("a VIEW_3D area is reachable headless", len(areas) > 0, f"{len(areas)} area(s)")
    space = areas[0].spaces.active if areas else None

    n = CM.show_channels({"object": True, "material": False, "vertex": False, "wireframe": False})
    check("show_channels(object) switches color_type to OBJECT",
          bool(space) and space.shading.color_type == "OBJECT",
          f"n={n} color_type={space.shading.color_type if space else None}")

    CM.show_channels({"object": False, "material": False, "vertex": True, "wireframe": False})
    check("show_channels(vertex) switches color_type to VERTEX",
          bool(space) and space.shading.color_type == "VERTEX",
          space.shading.color_type if space else None)

    CM.show_channels({"object": False, "material": True, "vertex": False, "wireframe": False})
    check("show_channels(material) switches color_type to MATERIAL",
          bool(space) and space.shading.color_type == "MATERIAL",
          space.shading.color_type if space else None)

    # object wins over material when both are on (material is already Blender's default)
    CM.show_channels({"object": True, "material": True, "vertex": False, "wireframe": False})
    check("show_channels prefers the channel that needs a switch",
          bool(space) and space.shading.color_type == "OBJECT",
          space.shading.color_type if space else None)

    # wireframe channel drives wireframe_color_type, not color_type
    space.shading.wireframe_color_type = "THEME"
    CM.show_channels({"object": False, "material": False, "vertex": False, "wireframe": True})
    check("show_channels(wireframe) switches wireframe_color_type to OBJECT",
          space.shading.wireframe_color_type == "OBJECT",
          space.shading.wireframe_color_type)

    # a viewport parked in WIREFRAME/RENDERED can't show a Solid color source
    space.shading.type = "WIREFRAME"
    CM.show_channels({"object": True, "material": False, "vertex": False, "wireframe": False})
    check("show_channels restores SOLID shading", space.shading.type == "SOLID",
          space.shading.type)

    # ...but a Material pass must NOT yank a look-dev viewport out of Material-preview/Rendered:
    # an ID material is exactly what those modes already show.
    space.shading.type = "MATERIAL"
    CM.show_channels({"object": False, "material": True, "vertex": False, "wireframe": False})
    check("show_channels leaves Material-preview alone for a material pass",
          space.shading.type == "MATERIAL", space.shading.type)
    space.shading.type = "RENDERED"
    CM.show_channels({"object": False, "material": True, "vertex": False, "wireframe": False})
    check("show_channels leaves Rendered alone for a material pass",
          space.shading.type == "RENDERED", space.shading.type)

    # a wireframe-only pass is drawable in Wireframe mode too, so that mode is kept
    space.shading.type = "WIREFRAME"
    CM.show_channels({"object": False, "material": False, "vertex": False, "wireframe": True})
    check("show_channels keeps WIREFRAME mode for a wireframe-only pass",
          space.shading.type == "WIREFRAME", space.shading.type)
    # but from Rendered (no wires drawn at all) it must fall back to Solid
    space.shading.type = "RENDERED"
    CM.show_channels({"object": False, "material": False, "vertex": False, "wireframe": True})
    check("show_channels drops Rendered to SOLID for a wireframe-only pass",
          space.shading.type == "SOLID", space.shading.type)
    space.shading.type = "SOLID"

    # ── outliner TEXT color (Maya outlinerColor analogue) ────────────────────
    # Stored as a plain custom property so it saves with the .blend; the overlay that paints
    # it is GUI-only (draw handlers never fire under --background), so these checks cover the
    # data + the fail-closed contract, and outliner_tint_gui_check.py covers the painting.
    from blendertk.display_utils.outliner_tint import OutlinerTint, COLOR_PROP

    reset_scene()
    bpy.ops.mesh.primitive_cube_add(); a = bpy.context.active_object; a.name = "A"
    bpy.ops.mesh.primitive_cube_add(location=(3, 0, 0)); b = bpy.context.active_object; b.name = "B"

    check("text: set_outliner_color stamps the object", CM.set_outliner_color([a], RED) == 1
          and COLOR_PROP in a)
    got = CM.get_outliner_color(a)
    check("text: color round-trips exactly",
          got is not None and CM.color_difference(got, RED) < 1e-6, str(got))
    check("text: untouched object reads None", CM.get_outliner_color(b) is None)
    check("text: select-by finds A only",
          [o.name for o in CM.get_objects_by_color(RED, check_outliner_color=True)] == ["A"])
    check("text: apply_color routes the channel",
          (CM.apply_color([b], RED, apply_to_outliner=True), CM.get_outliner_color(b))[1] is not None)
    check("text: tinted_objects lists both", len(OutlinerTint.tinted_objects()) == 2)
    check("text: reset clears the stamp",
          (CM.reset_colors([a, b]), CM.get_outliner_color(a), COLOR_PROP in a)[1] is None)
    check("text: clear is idempotent on an unstamped object", OutlinerTint.clear([a]) == 0)
    # None-safety: engine must tolerate a None in the batch
    check("text: None entries tolerated", CM.set_outliner_color([None, a], RED) == 1)
    CM.reset_colors([a])

    # fail-closed contract: overlay unavailability never costs stored data
    check("text: overlay reports a status", OutlinerTint.status() in
          ("ok", "unknown", "unsupported") or "refused" in OutlinerTint.status(),
          OutlinerTint.status())
    # Registration itself works headless (the handler simply never fires with no GUI); what
    # must hold is that enable/disable are idempotent and stored colors never depend on them.
    first = OutlinerTint.enable()
    if OutlinerTint.is_supported():
        check("text: enable() registers and is idempotent",
              first == OutlinerTint.enable() and OutlinerTint.is_enabled() is True,
              f"enable={first} status={OutlinerTint.status()}")
    else:  # no memory guard on this OS (Win32-only): enable declines, the same way every time
        check("text: enable() declines cleanly where the overlay is unsupported",
              first is False and OutlinerTint.enable() is False
              and OutlinerTint.is_enabled() is False and OutlinerTint.status() == "unsupported",
              f"enable={first} status={OutlinerTint.status()}")
    check("text: colors are readable regardless of the overlay",
          (CM.set_outliner_color([a], RED), CM.get_outliner_color(a))[1] is not None)
    OutlinerTint.disable()
    OutlinerTint.disable()  # idempotent
    check("text: disable() is idempotent and leaves the color intact",
          OutlinerTint.is_enabled() is False and CM.get_outliner_color(a) is not None)
    CM.reset_colors([a])

    # ── Set Per Color (grouping aid: color-tagged ID collections) ────────────
    # Groups a color's objects into a color-tagged ID collection (home membership kept).
    # The tag is the nearest of the 8 theme swatches; the exact color is stamped on the
    # collection so select-by / get round-trip exactly despite the display quantization.
    reset_scene()
    bpy.ops.mesh.primitive_cube_add(); a = bpy.context.active_object; a.name = "A"
    bpy.ops.mesh.primitive_cube_add(location=(3, 0, 0)); b = bpy.context.active_object; b.name = "B"
    home = a.users_collection[0]

    tags = CM.collection_tag_colors()
    check("collection_tag_colors returns the 8 theme swatches",
          len(tags) == 8 and all(len(c) == 3 for c in tags), str(tags[:2]))
    expected_tag = "COLOR_%02d" % (
        min(range(8), key=lambda i: CM.color_difference(tags[i], RED)) + 1)

    CM.add_to_color_set([a], RED)
    id_cols = [c for c in bpy.data.collections if CM._ID_COLLECTION_PROP in c]
    check("set: one stamped ID collection created", len(id_cols) == 1,
          str([c.name for c in id_cols]))
    col = id_cols[0]
    check("set: object linked into the ID collection", a.name in col.objects)
    check("set: home collection membership kept", a.name in home.objects)
    check("set: collection in the scene tree",
          col.name in {c.name for c in bpy.context.scene.collection.children_recursive})
    check("set: tag is the nearest theme swatch", col.color_tag == expected_tag,
          f"{col.color_tag} vs {expected_tag}")
    got = CM.get_color_set_color(a)
    check("set: exact color round-trips through the stamp",
          got is not None and CM.color_difference(got, RED) < 1e-3, str(got))
    check("set: untouched object reads None", CM.get_color_set_color(b) is None)

    found = CM.get_objects_by_color(RED, check_set=True)
    check("select-by set finds A only", [o.name for o in found] == ["A"],
          str([o.name for o in found]))

    # recolor moves the object between ID collections; the emptied one is removed
    BLUE = (0.1, 0.2, 0.9)
    CM.add_to_color_set([a], BLUE)
    id_cols = [c for c in bpy.data.collections if CM._ID_COLLECTION_PROP in c]
    check("set: recolor leaves exactly one stamped collection", len(id_cols) == 1,
          str([c.name for c in id_cols]))
    got = CM.get_color_set_color(a)
    check("set: recolor round-trips the new color",
          got is not None and CM.color_difference(got, BLUE) < 1e-3, str(got))

    # apply_color routes the channel
    CM.apply_color([b], RED, set_per_color=True)
    check("apply_color(set_per_color) links the object",
          CM.get_color_set_color(b) is not None)

    # reset unlinks + removes stamped collections; an unstamped user ID_* survives
    user_col = bpy.data.collections.new("ID_FF0000")  # user's own, NO stamp
    bpy.context.scene.collection.children.link(user_col)
    user_col.objects.link(b)
    CM.reset_colors([a, b])
    check("set: reset removes every stamped ID collection",
          not [c for c in bpy.data.collections if CM._ID_COLLECTION_PROP in c])
    check("set: reset leaves the user's unstamped ID_* collection",
          user_col.name in bpy.data.collections and b.name in user_col.objects)
    check("set: reset reads back None", CM.get_color_set_color(a) is None)

    # an unstamped user collection that happens to hold the exact ID_<HEX> name must NOT be
    # adopted (stamped/retagged/swept) — the tool creates its own (Blender .001-suffixes it)
    clash = bpy.data.collections.new("ID_CC1919")  # RED's ID name, user-owned, NO stamp
    bpy.context.scene.collection.children.link(clash)
    CM.add_to_color_set([a], RED)
    check("set: user's name-colliding collection is not adopted",
          CM._ID_COLLECTION_PROP not in clash and clash.color_tag == "NONE")
    stamped = [c for c in bpy.data.collections if CM._ID_COLLECTION_PROP in c]
    check("set: tool created its own collection despite the clash",
          len(stamped) == 1 and stamped[0] is not clash,
          str([c.name for c in stamped]))
    # applying the same color again reuses the tool's collection (stamp-keyed, not name-keyed)
    CM.add_to_color_set([b], RED)
    check("set: same color reuses the stamped collection",
          len([c for c in bpy.data.collections if CM._ID_COLLECTION_PROP in c]) == 1)

    # reset must not orphan an object whose ID collection is its ONLY membership
    for c in list(a.users_collection):
        if CM._ID_COLLECTION_PROP not in c:
            c.objects.unlink(a)
    check("set: object now lives only in the ID collection",
          all(CM._ID_COLLECTION_PROP in c for c in a.users_collection),
          str([c.name for c in a.users_collection]))
    CM.reset_colors([a, b])
    check("set: reset re-homes an otherwise-orphaned object",
          len(a.users_collection) > 0
          and a.name in bpy.context.view_layer.objects,
          str([c.name for c in a.users_collection]))

    # empty batch is a true no-op: nothing created, None returned
    n_cols = len(bpy.data.collections)
    check("set: empty batch creates nothing and returns None",
          CM.add_to_color_set([None], RED) is None
          and len(bpy.data.collections) == n_cols)

    # ── channel scope: each channel resets on its own ────────────────────────
    # Regression: the panel's Reset called reset_colors(objects) with no flags, so all five
    # reset_* defaults (True) fired — clearing a wireframe tint also stripped the ID material,
    # the vertex colors, the outliner stamp and the ID collection. (The panel's flag wiring
    # and its no-channel guard: tentacle's test_color_id_panel.py.)
    reset_scene()
    bpy.ops.mesh.primitive_cube_add()
    a = bpy.context.active_object
    a.name = "A"
    if space is not None:
        space.shading.wireframe_color_type = "THEME"
    CM.apply_color([a], RED, apply_to_wireframe=True, apply_to_material=True,
                   apply_to_vertex=True, apply_to_outliner=True, set_per_color=True)
    check("apply_to_wireframe (mayatk's keyword) writes obj.color", CM.has_object_color(a),
          str(tuple(a.color)[:3]))
    check("apply_color points the wires at the object color",
          bool(space) and space.shading.wireframe_color_type == "OBJECT",
          space.shading.wireframe_color_type if space else None)

    CM.reset_colors([a], reset_wireframe=True, reset_outliner=False, reset_material=False,
                    reset_vertex=False, reset_sets=False)
    check("scope: reset clears the enabled wireframe channel", not CM.has_object_color(a),
          str(tuple(a.color)[:3]))
    check("scope: reset keeps the unchecked material channel",
          bool(a.active_material) and a.active_material.name.startswith("ID_"),
          a.active_material.name if a.active_material else None)
    check("scope: reset keeps the unchecked vertex channel", len(a.data.color_attributes) > 0)
    check("scope: reset keeps the unchecked outliner channel",
          CM.get_outliner_color(a) is not None)
    check("scope: reset keeps the unchecked ID collection",
          CM.get_color_set_color(a) is not None)

    CM.reset_colors([a])  # every channel defaults on
    check("scope: reset clears every enabled channel",
          not CM.has_object_color(a)
          and not (a.active_material and a.active_material.name.startswith("ID_"))
          and len(a.data.color_attributes) == 0
          and CM.get_outliner_color(a) is None
          and CM.get_color_set_color(a) is None)

    # ── the shared panel's reads: get_color, lead_object, select_by_color ───
    reset_scene()
    bpy.ops.mesh.primitive_cube_add()
    a = bpy.context.active_object
    a.name = "A"
    bpy.ops.mesh.primitive_cube_add(location=(3, 0, 0))
    b = bpy.context.active_object
    b.name = "B"
    BLUE = (0.1, 0.2, 0.9)
    CM.apply_color([a], RED, apply_to_wireframe=True)
    CM.apply_color([a], BLUE, apply_to_outliner=True)
    got = CM.get_color(a, check_wireframe_color=True, check_outliner_color=True)
    check("get_color reads the outliner stamp first",
          got is not None and CM.color_difference(got, BLUE) < 1e-3, str(got))
    got = CM.get_color(a, check_wireframe_color=True)
    check("get_color reads the wireframe channel",
          got is not None and CM.color_difference(got, RED) < 1e-3, str(got))
    check("get_color: an untouched object's default white is no color",
          CM.get_color(b, check_wireframe_color=True) is None)
    check("get_color with no channel enabled reads nothing", CM.get_color(a) is None)

    bpy.context.view_layer.objects.active = b
    check("lead_object is the active object", CM.lead_object() == b)

    a.select_set(False)
    b.select_set(True)
    found = CM.select_by_color(RED, check_wireframe_color=True)
    check("select_by_color selects the matches only",
          [o.name for o in found] == ["A"] and a.select_get() and not b.select_get(),
          str([o.name for o in found]))
    check("select_by_color makes the first match active", CM.lead_object() == a)
    none_found = CM.select_by_color((0.0, 1.0, 0.0), check_wireframe_color=True)
    check("select_by_color with no match clears the selection",
          none_found == [] and not a.select_get() and not b.select_get())

    check("scene_objects lists the scene's objects",
          sorted(o.name for o in CM.scene_objects()) == ["A", "B"])
    check("swatch presets keep blendertk's store", CM.SWATCH_PRESET_DIR == "blendertk/color_id")

    # ── redraw helper ────────────────────────────────────────────────────────
    check("CoreUtils.tag_redraw tags the viewports",
          CoreUtils.tag_redraw("VIEW_3D") == len(areas),
          str(CoreUtils.tag_redraw("VIEW_3D")))
    # area_type=None sweeps every editor (what the style setter's theme repaint needs)
    all_areas = CoreUtils.get_areas()
    check("get_areas()/tag_redraw() default to every area type",
          len(all_areas) >= len(areas) and CoreUtils.tag_redraw() == len(all_areas),
          f"all={len(all_areas)} view3d={len(areas)}")

except Exception as e:
    traceback.print_exc()
    check("color manager raised", False, repr(e))

passed = sum(1 for line in lines if line.startswith("OK"))
for line in lines:
    print(line)
result = "PASS" if all(line.startswith("OK") for line in lines) else "FAIL"
print(f"===RESULT: {result}=== ({passed}/{len(lines)})")
