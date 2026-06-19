"""blendertk Channels engine headless test (no viewport, no Qt).

Mirror of mayatk's ``test_channels`` for the engine half: exercises the Blender attribute
query / mutation logic (transform channels + custom properties) that backs the Channels panel.
Run: blender --background --factory-startup --python blendertk/test/test_channels.py
"""
import sys, os, math, traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)            # blendertk/
MONO = os.path.dirname(REPO)           # _scripts/
for p in (REPO, os.path.join(MONO, "pythontk")):
    if p not in sys.path:
        sys.path.insert(0, p)

lines = []
def check(name, cond, detail=""):
    lines.append(f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}")

try:
    import bpy
    from blendertk.node_utils.attributes.channels._channels import Channels

    def reset():
        bpy.ops.object.select_all(action="DESELECT")
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)

    def cube(name, loc=(0, 0, 0)):
        bpy.ops.mesh.primitive_cube_add(location=loc)
        o = bpy.context.active_object
        o.name = name
        return o

    # --- Target resolution -------------------------------------------------
    reset()
    A = cube("A", (1, 2, 3))
    B = cube("B", (4, 5, 6))
    ch = Channels()
    bpy.ops.object.select_all(action="SELECT")
    sel = ch.get_selected_nodes()
    check("get_selected_nodes -> current selection", set(sel) == {A, B}, f"n={len(sel)}")

    # single-object mode -> active only
    ch.single_object_mode = True
    bpy.context.view_layer.objects.active = B
    sel1 = ch.get_selected_nodes()
    check("single_object_mode -> active only", sel1 == [B], f"{[o.name for o in sel1]}")
    ch.single_object_mode = False

    # pin
    ch.pin_targets([A])
    check("pin_targets -> is_pinned", ch.is_pinned and ch.get_selected_nodes() == [A])
    ch.pin_targets(None)
    check("pin clear -> not pinned", not ch.is_pinned)

    # --- Channel collection / filters --------------------------------------
    reset()
    A = cube("A", (1.0, 2.0, 3.0))
    A["myFloat"] = 0.5
    A["myInt"] = 3
    transform = ch.collect_channels([A], "keyable")
    names = [d["name"] for d in transform]
    check("keyable filter -> 9 transform + 2 custom", len(names) == 11, f"n={len(names)}")
    check("channel-box order -> location_x first", names[0] == "location_x", names[0])

    custom = ch.collect_channels([A], "custom")
    cnames = {d["name"] for d in custom}
    check("custom filter -> only custom props", cnames == {"myFloat", "myInt"}, f"{cnames}")

    # type inference
    by_name = {d["name"]: d for d in custom}
    check("custom type -> float/int inferred",
          by_name["myFloat"]["type"] == "float" and by_name["myInt"]["type"] == "int")

    # intersection across multi-selection
    B = cube("B")
    B["myFloat"] = 9.0   # only myFloat is common
    common = ch.collect_channels([A, B], "custom")
    check("multi-select custom intersection -> myFloat only",
          {d["name"] for d in common} == {"myFloat"}, f"{[d['name'] for d in common]}")

    # --- Values (incl. angle conversion) -----------------------------------
    loc_x = next(d for d in transform if d["name"] == "location_x")
    check("get location_x value", ch.get_channel_value(A, loc_x) == 1.0)

    A.rotation_euler[2] = math.radians(90)
    rot_z = next(d for d in ch.collect_channels([A], "keyable") if d["name"] == "rotation_z")
    check("rotation shown in degrees", abs(ch.get_channel_value(A, rot_z) - 90.0) < 1e-4,
          f"{ch.get_channel_value(A, rot_z)}")

    # set via parse (degrees -> radians for angle channels)
    ch.set_channel_value([A], rot_z, "45")
    check("set angle channel parses degrees", abs(A.rotation_euler[2] - math.radians(45)) < 1e-4,
          f"{math.degrees(A.rotation_euler[2])}")
    ch.set_channel_value([A], loc_x, "7.5")
    check("set float channel", abs(A.location[0] - 7.5) < 1e-6, f"{A.location[0]}")

    # custom prop set
    mf = by_name["myFloat"]
    ch.set_channel_value([A], mf, "2.25")
    check("set custom float prop", abs(A["myFloat"] - 2.25) < 1e-6, f"{A['myFloat']}")

    # --- Lock --------------------------------------------------------------
    check("location_x not locked initially", not ch.is_locked(A, loc_x))
    ch.toggle_lock([A], loc_x)
    check("toggle_lock -> locked", A.lock_location[0] is True)
    ch.toggle_lock([A], loc_x)
    check("toggle_lock again -> unlocked", A.lock_location[0] is False)
    # custom props are not lockable -> no-op, reports False
    check("custom prop not lockable", not ch.is_locked(A, mf))
    locked_only = ch.collect_channels([A], "locked")
    check("locked filter empty when nothing locked", locked_only == [])
    ch.set_lock([A], [loc_x], True)
    check("locked filter finds the locked channel",
          [d["name"] for d in ch.collect_channels([A], "locked")] == ["location_x"])
    ch.set_lock([A], [loc_x], False)

    # --- Keyframes + classification ----------------------------------------
    bpy.context.scene.frame_set(1)
    check("classify none initially", ch.classify_connection(A, loc_x) == "none",
          ch.classify_connection(A, loc_x))
    res = ch.toggle_key_at_current_time([A], loc_x)
    check("toggle key -> set", res == "set", str(res))
    check("classify keyframe_active on keyed frame",
          ch.classify_connection(A, loc_x) == "keyframe_active",
          ch.classify_connection(A, loc_x))
    bpy.context.scene.frame_set(10)
    check("classify keyframe off the keyed frame",
          ch.classify_connection(A, loc_x) == "keyframe",
          ch.classify_connection(A, loc_x))
    # animated filter picks it up
    animated = {d["name"] for d in ch.collect_channels([A], "animated")}
    check("animated filter -> location_x", "location_x" in animated, f"{animated}")
    bpy.context.scene.frame_set(1)
    res = ch.toggle_key_at_current_time([A], loc_x)
    check("toggle key again -> removed", res == "removed", str(res))
    check("classify none after key removed",
          ch.classify_connection(A, loc_x) == "none", ch.classify_connection(A, loc_x))

    # --- Create / delete / rename custom attribute -------------------------
    ch.create_attribute([A], "newAttr", "float", min_val=0.0, max_val=10.0, default_val=2.0)
    check("create_attribute -> exists with default", A.get("newAttr") == 2.0, f"{A.get('newAttr')}")
    ui = A.id_properties_ui("newAttr").as_dict()
    check("create_attribute -> UI range applied", ui.get("min") == 0.0 and ui.get("max") == 10.0,
          f"{ui}")
    ch.rename_attribute([A], "newAttr", "renamedAttr")
    check("rename_attribute -> value preserved",
          "newAttr" not in A.keys() and A.get("renamedAttr") == 2.0)
    ren = {d["name"]: d for d in ch.collect_channels([A], "custom")}
    ch.delete_attributes([A], [ren["renamedAttr"]])
    check("delete_attributes -> gone", "renamedAttr" not in A.keys())
    # delete must NOT touch transform channels
    ch.delete_attributes([A], [loc_x])
    check("delete_attributes ignores transform channels", hasattr(A, "location"))

    # --- Reset to default --------------------------------------------------
    A.location = (5, 5, 5)
    A.scale = (2, 2, 2)
    sx = next(d for d in ch.collect_channels([A], "keyable") if d["name"] == "scale_x")
    ch.reset_to_default([A], [loc_x, sx])
    check("reset transform -> loc 0, scale 1",
          abs(A.location[0]) < 1e-6 and abs(A.scale[0] - 1.0) < 1e-6,
          f"loc={A.location[0]} scale={A.scale[0]}")

    # --- build_table_data --------------------------------------------------
    rows, states = ch.build_table_data([A], "keyable")
    check("build_table_data -> rows + states aligned", len(rows) == len(states) and len(rows) >= 9)
    check("build_table_data row shape", all(len(r) == 5 for r in rows))
    rows_empty, states_empty = ch.build_table_data([], "keyable")
    check("build_table_data [] -> No selection placeholder",
          rows_empty == [["", "", "", "", "No selection"]], f"{rows_empty}")
    A2 = cube("Empty2")
    rows2, states2 = ch.build_table_data([A2], "custom")  # no custom props
    check("build_table_data no channels -> placeholder",
          rows2 == [["", "", "", "", "No channels"]], f"{rows2}")

    # multi-object differing value -> "*", matching value -> shown (guards _same_value)
    reset()
    m1 = cube("m1", (1, 0, 0))
    m2 = cube("m2", (2, 0, 0))
    rows_m, _ = ch.build_table_data([m1, m2], "keyable")
    lx_row = next(r for r in rows_m if r[0] == "location_x")
    ly_row = next(r for r in rows_m if r[0] == "location_y")
    check("multi-object differing value -> '*'", lx_row[3] == "*", lx_row[3])
    check("multi-object matching value shown", ly_row[3] == "0", ly_row[3])
    A = m1  # keep a valid object for the rename check below

    # --- rename node -------------------------------------------------------
    newname = ch.rename_node(A, "Renamed_A")
    check("rename_node -> object renamed", A.name == "Renamed_A" and newname == "Renamed_A", A.name)

    # --- copy / paste ------------------------------------------------------
    reset()
    src = cube("src", (3.0, 0, 0))
    dst = cube("dst", (0, 0, 0))
    loc_x = next(d for d in ch.collect_channels([src], "keyable") if d["name"] == "location_x")
    ch.copy_values([src], [loc_x])
    ch.paste_values([dst])
    check("copy/paste location_x", abs(dst.location[0] - 3.0) < 1e-6, f"{dst.location[0]}")

    # --- Mute / unmute + classification ------------------------------------
    reset()
    A = cube("A", (1, 0, 0))
    lx = next(d for d in ch.collect_channels([A], "keyable") if d["name"] == "location_x")
    bpy.context.scene.frame_set(1)
    ch.toggle_key_at_current_time([A], lx)
    changed = ch.set_mute([A], [lx], True)
    check("set_mute -> classify muted",
          changed and ch.classify_connection(A, lx) == "muted", ch.classify_connection(A, lx))
    ch.set_mute([A], [lx], False)
    check("unmute -> classify keyframe_active again",
          ch.classify_connection(A, lx) == "keyframe_active", ch.classify_connection(A, lx))

    # --- Breakdown key -----------------------------------------------------
    reset()
    A = cube("A")
    lx = next(d for d in ch.collect_channels([A], "keyable") if d["name"] == "location_x")
    bpy.context.scene.frame_set(5)
    n = ch.set_breakdown_key([A], [lx])
    fc = ch._find_fcurve(A, lx)
    kp = next((k for k in fc.keyframe_points if round(k.co[0]) == 5), None) if fc else None
    check("set_breakdown_key -> BREAKDOWN keyframe",
          kp is not None and kp.type == "BREAKDOWN", f"n={n} type={getattr(kp, 'type', None)}")

    # --- Freeze stores bakes (hidden) -> Unfreeze restores -----------------
    reset()
    A = cube("A", (3, 0, 0))
    ch.freeze_transforms([A])  # full freeze (all T/R/S groups)
    check("freeze stamps bake props", any(k.endswith("_bake") for k in A.keys()), f"{list(A.keys())}")
    check("bake props hidden from custom channels",
          {d["name"] for d in ch.collect_channels([A], "custom")} == set(),
          f"{[d['name'] for d in ch.collect_channels([A], 'custom')]}")
    check("has_unfreeze_info True after freeze", ch.has_unfreeze_info([A]))
    restored = ch.unfreeze_transforms([A])
    check("unfreeze -> object restored + location back",
          A in restored and abs(A.location[0] - 3.0) < 1e-4, f"loc={A.location[0]}")
    check("has_unfreeze_info False after unfreeze", not ch.has_unfreeze_info([A]))

    # --- Select connection (driver target) ---------------------------------
    reset()
    tgt = cube("Driver_Target", (5, 0, 0))
    driven = cube("Driven", (0, 0, 0))
    fcd = driven.driver_add("location", 0)
    var = fcd.driver.variables.new()
    var.targets[0].id = tgt
    lx = next(d for d in ch.collect_channels([driven], "keyable") if d["name"] == "location_x")
    check("classify driven -> driven_key",
          ch.classify_connection(driven, lx) == "driven_key", ch.classify_connection(driven, lx))
    bpy.ops.object.select_all(action="DESELECT")
    ok = ch.select_connections([driven], lx)
    check("select_connections selects driver target", ok and tgt.select_get(), f"ok={ok}")

except Exception as e:
    traceback.print_exc()
    check("test raised", False, repr(e))

passed = sum(1 for line in lines if line.startswith("OK"))
for line in lines:
    print(line)
result = "PASS" if all(line.startswith("OK") for line in lines) else "FAIL"
print(f"===RESULT: {result}=== ({passed}/{len(lines)})")
