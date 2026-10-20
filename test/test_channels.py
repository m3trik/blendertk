"""blendertk Channels engine headless test (no viewport, no Qt).

Mirror of mayatk's ``test_channels`` for the engine half: exercises the Blender attribute
query / mutation logic (transform channels + custom properties) that backs the Channels panel.
Run: blender --background --factory-startup --python blendertk/test/test_channels.py
"""

import sys
import os
import math
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)  # blendertk/
MONO = os.path.dirname(REPO)  # _scripts/
for p in (REPO, os.path.join(MONO, "pythontk")):
    if p not in sys.path:
        sys.path.insert(0, p)

lines = []


def check(name, cond, detail=""):
    lines.append(
        f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + str(detail)) if detail else ''}"
    )


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
    check(
        "get_selected_nodes -> current selection", set(sel) == {A, B}, f"n={len(sel)}"
    )

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
    check(
        "keyable filter -> 9 transform + 2 custom", len(names) == 11, f"n={len(names)}"
    )
    check("channel-box order -> location_x first", names[0] == "location_x", names[0])

    custom = ch.collect_channels([A], "custom")
    cnames = {d["name"] for d in custom}
    check(
        "custom filter -> only custom props",
        cnames == {"myFloat", "myInt"},
        f"{cnames}",
    )

    # type inference
    by_name = {d["name"]: d for d in custom}
    check(
        "custom type -> float/int inferred",
        by_name["myFloat"]["type"] == "float" and by_name["myInt"]["type"] == "int",
    )

    # intersection across multi-selection
    B = cube("B")
    B["myFloat"] = 9.0  # only myFloat is common
    common = ch.collect_channels([A, B], "custom")
    check(
        "multi-select custom intersection -> myFloat only",
        {d["name"] for d in common} == {"myFloat"},
        f"{[d['name'] for d in common]}",
    )

    # --- Values (incl. angle conversion) -----------------------------------
    loc_x = next(d for d in transform if d["name"] == "location_x")
    check("get location_x value", ch.get_channel_value(A, loc_x) == 1.0)

    A.rotation_euler[2] = math.radians(90)
    rot_z = next(
        d for d in ch.collect_channels([A], "keyable") if d["name"] == "rotation_z"
    )
    check(
        "rotation shown in degrees",
        abs(ch.get_channel_value(A, rot_z) - 90.0) < 1e-4,
        f"{ch.get_channel_value(A, rot_z)}",
    )

    # set via parse (degrees -> radians for angle channels)
    ch.set_channel_value([A], rot_z, "45")
    check(
        "set angle channel parses degrees",
        abs(A.rotation_euler[2] - math.radians(45)) < 1e-4,
        f"{math.degrees(A.rotation_euler[2])}",
    )
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
    check(
        "locked filter finds the locked channel",
        [d["name"] for d in ch.collect_channels([A], "locked")] == ["location_x"],
    )
    ch.set_lock([A], [loc_x], False)

    # --- Keyframes + classification ----------------------------------------
    bpy.context.scene.frame_set(1)
    check(
        "classify none initially",
        ch.classify_connection(A, loc_x) == "none",
        ch.classify_connection(A, loc_x),
    )
    res = ch.toggle_key_at_current_time([A], loc_x)
    check("toggle key -> set", res == "set", str(res))
    check(
        "classify keyframe_active on keyed frame",
        ch.classify_connection(A, loc_x) == "keyframe_active",
        ch.classify_connection(A, loc_x),
    )
    bpy.context.scene.frame_set(10)
    check(
        "classify keyframe off the keyed frame",
        ch.classify_connection(A, loc_x) == "keyframe",
        ch.classify_connection(A, loc_x),
    )
    # animated filter picks it up
    animated = {d["name"] for d in ch.collect_channels([A], "animated")}
    check("animated filter -> location_x", "location_x" in animated, f"{animated}")
    bpy.context.scene.frame_set(1)
    res = ch.toggle_key_at_current_time([A], loc_x)
    check("toggle key again -> removed", res == "removed", str(res))
    check(
        "classify none after key removed",
        ch.classify_connection(A, loc_x) == "none",
        ch.classify_connection(A, loc_x),
    )

    # --- Create / delete / rename custom attribute -------------------------
    ch.create_attribute(
        [A], "newAttr", "float", min_val=0.0, max_val=10.0, default_val=2.0
    )
    check(
        "create_attribute -> exists with default",
        A.get("newAttr") == 2.0,
        f"{A.get('newAttr')}",
    )
    ui = A.id_properties_ui("newAttr").as_dict()
    check(
        "create_attribute -> UI range applied",
        ui.get("min") == 0.0 and ui.get("max") == 10.0,
        f"{ui}",
    )

    # vector (Maya double3) create-type: 3-float XYZ array, default on each component, editable
    ch.create_attribute(
        [A], "vecAttr", "vector", min_val=-1.0, max_val=1.0, default_val=0.5
    )
    check(
        "create_attribute vector -> 3-float array, default on each component",
        "vecAttr" in A.keys() and list(A["vecAttr"]) == [0.5, 0.5, 0.5],
        f"{list(A.get('vecAttr', []))}",
    )
    vui = A.id_properties_ui("vecAttr").as_dict()
    check(
        "create_attribute vector -> XYZ subtype + float range",
        vui.get("subtype") == "XYZ"
        and vui.get("min") == -1.0
        and vui.get("max") == 1.0,
        f"{vui}",
    )
    vdesc = next(
        d for d in ch.collect_channels([A], "custom") if d["name"] == "vecAttr"
    )
    check("vector custom prop typed 'vector'", vdesc["type"] == "vector", vdesc["type"])
    check(
        "format_value renders a vector as (x, y, z)",
        ch.format_value(A["vecAttr"]) == "(0.5, 0.5, 0.5)",
        ch.format_value(A["vecAttr"]),
    )
    ch.set_channel_value([A], vdesc, "(1, 0, -1)")  # edit round-trips via parse_value
    check(
        "set_channel_value edits a vector prop element-wise",
        list(A["vecAttr"]) == [1.0, 0.0, -1.0],
        f"{list(A['vecAttr'])}",
    )
    ch.delete_attributes(
        [A], [vdesc]
    )  # clean up so downstream table-count checks are unaffected

    ch.rename_attribute([A], "newAttr", "renamedAttr")
    check(
        "rename_attribute -> value preserved",
        "newAttr" not in A.keys() and A.get("renamedAttr") == 2.0,
    )
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
    check(
        "reset transform -> loc 0, scale 1",
        abs(A.location[0]) < 1e-6 and abs(A.scale[0] - 1.0) < 1e-6,
        f"loc={A.location[0]} scale={A.scale[0]}",
    )

    # --- build_table_data --------------------------------------------------
    rows, states = ch.build_table_data([A], "keyable")
    check(
        "build_table_data -> rows + states aligned",
        len(rows) == len(states) and len(rows) >= 9,
    )
    check("build_table_data row shape", all(len(r) == 5 for r in rows))
    rows_empty, states_empty = ch.build_table_data([], "keyable")
    check(
        "build_table_data [] -> No selection placeholder",
        rows_empty == [["", "", "", "", "No selection"]],
        f"{rows_empty}",
    )
    A2 = cube("Empty2")
    rows2, states2 = ch.build_table_data([A2], "custom")  # no custom props
    check(
        "build_table_data no channels -> placeholder",
        rows2 == [["", "", "", "", "No channels"]],
        f"{rows2}",
    )

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
    check(
        "rename_node -> object renamed",
        A.name == "Renamed_A" and newname == "Renamed_A",
        A.name,
    )

    # --- copy / paste ------------------------------------------------------
    reset()
    src = cube("src", (3.0, 0, 0))
    dst = cube("dst", (0, 0, 0))
    loc_x = next(
        d for d in ch.collect_channels([src], "keyable") if d["name"] == "location_x"
    )
    ch.copy_values([src], [loc_x])
    ch.paste_values([dst])
    check(
        "copy/paste location_x", abs(dst.location[0] - 3.0) < 1e-6, f"{dst.location[0]}"
    )

    # --- Mute / unmute + classification ------------------------------------
    reset()
    A = cube("A", (1, 0, 0))
    lx = next(
        d for d in ch.collect_channels([A], "keyable") if d["name"] == "location_x"
    )
    bpy.context.scene.frame_set(1)
    ch.toggle_key_at_current_time([A], lx)
    changed = ch.set_mute([A], [lx], True)
    check(
        "set_mute -> classify muted",
        changed and ch.classify_connection(A, lx) == "muted",
        ch.classify_connection(A, lx),
    )
    ch.set_mute([A], [lx], False)
    check(
        "unmute -> classify keyframe_active again",
        ch.classify_connection(A, lx) == "keyframe_active",
        ch.classify_connection(A, lx),
    )

    # --- Breakdown key -----------------------------------------------------
    reset()
    A = cube("A")
    lx = next(
        d for d in ch.collect_channels([A], "keyable") if d["name"] == "location_x"
    )
    bpy.context.scene.frame_set(5)
    n = ch.set_breakdown_key([A], [lx])
    fc = ch._find_fcurve(A, lx)
    kp = (
        next((k for k in fc.keyframe_points if round(k.co[0]) == 5), None)
        if fc
        else None
    )
    check(
        "set_breakdown_key -> BREAKDOWN keyframe",
        kp is not None and kp.type == "BREAKDOWN",
        f"n={n} type={getattr(kp, 'type', None)}",
    )

    # --- Freeze stores bakes (hidden) -> Unfreeze restores -----------------
    reset()
    A = cube("A", (3, 0, 0))
    ch.freeze_transforms([A])  # full freeze (all T/R/S groups)
    check(
        "freeze stamps bake props",
        any(k.endswith("_bake") for k in A.keys()),
        f"{list(A.keys())}",
    )
    check(
        "bake props hidden from custom channels",
        {d["name"] for d in ch.collect_channels([A], "custom")} == set(),
        f"{[d['name'] for d in ch.collect_channels([A], 'custom')]}",
    )
    check("has_unfreeze_info True after freeze", ch.has_unfreeze_info([A]))
    restored = ch.unfreeze_transforms([A])
    check(
        "unfreeze -> object restored + location back",
        A in restored and abs(A.location[0] - 3.0) < 1e-4,
        f"loc={A.location[0]}",
    )
    check("has_unfreeze_info False after unfreeze", not ch.has_unfreeze_info([A]))

    # --- Select connection (driver target) ---------------------------------
    reset()
    tgt = cube("Driver_Target", (5, 0, 0))
    driven = cube("Driven", (0, 0, 0))
    fcd = driven.driver_add("location", 0)
    var = fcd.driver.variables.new()
    var.targets[0].id = tgt
    lx = next(
        d for d in ch.collect_channels([driven], "keyable") if d["name"] == "location_x"
    )
    check(
        "classify driven -> driven_key",
        ch.classify_connection(driven, lx) == "driven_key",
        ch.classify_connection(driven, lx),
    )
    bpy.ops.object.select_all(action="DESELECT")
    ok = ch.select_connections([driven], lx)
    check(
        "select_connections selects driver target", ok and tgt.select_get(), f"ok={ok}"
    )

    # --- Explicit key set / remove (the panel's press / Alt+press) ---------
    reset()
    A = cube("A")
    lx = next(
        d for d in ch.collect_channels([A], "keyable") if d["name"] == "location_x"
    )
    bpy.context.scene.frame_set(3)
    ch.set_key_at_current_time([A], lx)
    res = ch.set_key_at_current_time([A], lx)
    fc = ch._find_fcurve(A, lx)
    check(
        "set_key twice -> still keyed (never toggles off)",
        res == "set" and fc is not None and ch._has_key_at_current_frame(fc),
        str(res),
    )
    res = ch.set_key_at_current_time([A], lx, keyed=False)
    check(
        "set_key keyed=False -> key removed",
        res == "removed" and ch.classify_connection(A, lx) == "none",
        ch.classify_connection(A, lx),
    )
    res = ch.set_key_at_current_time([A], lx, keyed=False)
    check("remove where no key sits -> harmless", res == "removed", str(res))

    # --- break_connections on a SLOTTED action (Blender 4.4+/5.x) ----------
    # Regression: ``_remove_fcurve`` reached for the flat ``action.fcurves`` first
    # and caught (RuntimeError, TypeError, ReferenceError).  5.x REMOVED that
    # attribute, so the access raises AttributeError -- not in that tuple -- and the
    # slotted branch beneath it was unreachable: the call propagated and removed
    # nothing.  Measured on 5.1.2 before the fix: raised, 3 curves before and after.
    reset()
    K = cube("K")
    K.keyframe_insert("location", frame=1)
    K.location.x = 5.0
    K.keyframe_insert("location", frame=10)
    slotted = not hasattr(K.animation_data.action, "fcurves")
    before = len(list(Channels._iter_fcurves(K)))
    try:
        broke = Channels.break_connections([K], {"data_path": "location", "index": 0})
        raised = ""
    except Exception as e:
        broke, raised = False, f"{type(e).__name__}: {e}"
    after = len(list(Channels._iter_fcurves(K)))
    check("break_connections does not raise on a slotted action", not raised, raised)
    check("break_connections reports the removal", broke is True)
    check(
        "break_connections removed exactly one curve",
        after == before - 1,
        f"before={before} after={after} slotted={slotted}",
    )
    check(
        "break_connections removed the RIGHT curve",
        not any(
            f.data_path == "location" and f.array_index == 0
            for f in Channels._iter_fcurves(K)
        ),
    )

    # --- A keyed value moved off its curve reads keyframe_changed ----------
    # Mirror of mayatk's TestOffCurveState. Repro (Channels, Blender 5.1): a keyed
    # channel moved without a key -- lost on the next frame change, orange in
    # Blender's own fields -- kept the plain keyed colour (measured before the fix:
    # "keyframe_active" on its key, "keyframe" between keys).
    reset()
    A = cube("A")
    A["myInt"] = 0
    scn = bpy.context.scene
    for frame, v in ((1, 0.0), (10, 10.0)):
        scn.frame_set(frame)
        A.location[0] = v
        A.rotation_euler[2] = math.radians(v * 9)
        A["myInt"] = int(v // 3)
        A.keyframe_insert("location", index=0, frame=frame)
        A.keyframe_insert("rotation_euler", index=2, frame=frame)
        A.keyframe_insert('["myInt"]', frame=frame)
    by = {d["name"]: d for d in ch.collect_channels([A], "all")}
    lx, ly, rz, mi = (
        by[n] for n in ("location_x", "location_y", "rotation_z", "myInt")
    )

    def states(*descriptors):
        return [ch.classify_connection(A, d) for d in descriptors]

    scn.frame_set(1)
    A.location[0] = 4.0
    check(
        "a value moved off its key -> keyframe_changed",
        states(lx) == ["keyframe_changed"] and ch.is_off_curve(A, lx),
        states(lx),
    )
    scn.frame_set(5)
    check(
        "a time change puts it back on its curve -> keyframe",
        states(lx) == ["keyframe"] and not ch.is_off_curve(A, lx),
        states(lx),
    )
    A.location[0] = 99.0
    check(
        "a value moved between keys -> keyframe_changed",
        states(lx) == ["keyframe_changed"],
        states(lx),
    )
    on_curve = []
    for frame in range(1, 11):
        scn.frame_set(frame)
        on_curve.append(tuple(states(lx, rz, mi)))
    check(
        "values on their curve never read changed (radians both sides; an int curve rounds)",
        all("keyframe_changed" not in s for s in on_curve)
        and on_curve[0] == ("keyframe_active",) * 3
        and on_curve[4] == ("keyframe",) * 3,
        on_curve,
    )
    A.location[1] = 4.0
    check(
        "an unkeyed channel never reads changed",
        states(ly) == ["none"] and not ch.is_off_curve(A, ly),
        states(ly),
    )
    scn.frame_set(5)
    A.location[0] = 4.0
    ch.set_key_at_current_time([A], lx, keyed=True)  # the panel's click on the cell
    scn.frame_set(6)
    scn.frame_set(5)
    check(
        "keying a changed value keeps it across a frame change",
        states(lx) == ["keyframe_active"] and abs(A.location[0] - 4.0) < 1e-5,
        f"{states(lx)} x={A.location[0]}",
    )

    # --- Every panel edit is ONE undo step, a drag-fill included ------------
    # Mirror of mayatk's TestEditBatchUndo and its engine's undo chunks. Repro
    # (Channels, Blender 5.1): the panel writes through RNA and pushed NO undo
    # step, so one Ctrl+Z reverted the user's previous operation along with the
    # edit, and Redo brought back only that operation (measured: a three-cell
    # fill of scale 2.5 after a move -> one undo gave scale 1 AND the move undone;
    # redo restored the move, scale stayed 1).
    from types import SimpleNamespace

    from blendertk.node_utils.attributes.channels.channels_slots import (
        ChannelsSlots,
    )

    T = {d["name"]: d for d in Channels._transform_descriptors()}

    def custom(key):
        return Channels._describe(key, f'["{key}"]', None, "custom", "float")

    def undo_round(names, setup, edit, read):
        """(edited, undone, previous op kept, redone) for *edit* after a previous step.

        Objects are re-read by name order each time: undo swaps ``bpy.data``.
        """
        reset()
        for n in names:
            cube(n)
        bpy.ops.object.select_all(action="SELECT")

        def objs():
            return sorted(bpy.data.objects, key=lambda o: o.name)

        setup(objs())
        bpy.ops.ed.undo_push(message="baseline")
        objs()[0]["prev"] = 1
        bpy.ops.ed.undo_push(message="previous op")
        edit(objs())
        edited = read(objs())
        bpy.ops.ed.undo()
        undone = read(objs())
        kept = "prev" in objs()[0].keys()
        bpy.ops.ed.redo()
        return edited, undone, kept, read(objs())

    def stand_in(descriptors, state="unlocked"):
        """A ChannelsSlots over the real engine, minus the Qt widgets it never needs here."""
        s = ChannelsSlots.__new__(ChannelsSlots)
        s.controller = ch
        s._row_descriptors = list(descriptors)
        s._scrub_state = None
        s._edit_chunk = None
        s.ui = SimpleNamespace(
            tbl000=SimpleNamespace(actions=SimpleNamespace(get=lambda r, c: state))
        )
        s.sb = SimpleNamespace(
            QtCore=SimpleNamespace(
                Qt=SimpleNamespace(AltModifier=1, ControlModifier=2, ShiftModifier=4)
            ),
            QtWidgets=SimpleNamespace(
                QApplication=SimpleNamespace(keyboardModifiers=lambda: 0),
                QLineEdit=type("QLineEdit", (), {}),
            ),
        )
        s._refresh_table = lambda *a: None
        s._set_value_cell_text = lambda *a: None
        return s

    def fill(o):
        s = SimpleNamespace(_edit_chunk=None)
        ChannelsSlots._on_edit_batch_started(s)
        for axis in "xyz":  # one cellChanged per cell, as the fill delivers them
            ch.set_channel_value(o, T[f"scale_{axis}"], "2.5")
        ChannelsSlots._on_edit_batch_finished(s)

    def scrub(o):
        s = stand_in([T["scale_x"]])
        s._on_scrub_started(0, ChannelsSlots.COL_VALUE)
        for dx in (40, 80, 100):
            s._on_scrub_moved(0, ChannelsSlots.COL_VALUE, dx, 0)
        s._on_scrub_finished(0, ChannelsSlots.COL_VALUE)

    def key_x(o):
        o[0].keyframe_insert("location", index=0, frame=1)

    def scale_2(o):
        o[0].scale[0] = 2.0

    def copied_scale_2(o):
        o[0].scale[0] = 2.0
        ch.copy_values(o, [T["scale_x"]])
        o[0].scale[0] = 1.0

    def frozen_scale_2(o):
        o[0].scale[0] = 2.0
        ch.freeze_transforms(o, [T["scale_x"]])

    def has_curve(o):
        return ch._find_fcurve(o[0], T["location_x"]) is not None

    def scale_x(o):
        return round(o[0].scale[0], 4)

    def none(o):
        pass

    lock_rows = [T["location_x"], T["location_y"], T["location_z"]]
    # fmt: off
    cases = [
        # (name, objects, setup, edit, read, before, after)
        ("set_channel_value", ["U"], none,
         lambda o: ch.set_channel_value(o, T["scale_x"], "2.5"), scale_x, 1.0, 2.5),
        ("set_lock", ["U"], none,
         lambda o: ch.set_lock(o, [T["location_x"]], True),
         lambda o: o[0].lock_location[0], False, True),
        ("set_key_at_current_time", ["U"], none,
         lambda o: ch.set_key_at_current_time(o, T["location_x"]), has_curve, False, True),
        ("break_connections", ["U"], key_x,
         lambda o: ch.break_connections(o, T["location_x"]), has_curve, True, False),
        ("set_mute", ["U"], key_x,
         lambda o: ch.set_mute(o, [T["location_x"]], True),
         lambda o: ch.classify_connection(o[0], T["location_x"]) == "muted", False, True),
        ("set_breakdown_key", ["U"], none,
         lambda o: ch.set_breakdown_key(o, [T["location_x"]]), has_curve, False, True),
        ("reset_to_default", ["U"], scale_2,
         lambda o: ch.reset_to_default(o, [T["scale_x"]]), scale_x, 2.0, 1.0),
        ("create_attribute", ["U"], none,
         lambda o: ch.create_attribute(o, "made", "float"),
         lambda o: "made" in o[0].keys(), False, True),
        ("delete_attributes", ["U"], lambda o: o[0].__setitem__("gone", 1.0),
         lambda o: ch.delete_attributes(o, [custom("gone")]),
         lambda o: "gone" in o[0].keys(), True, False),
        ("rename_attribute", ["U"], lambda o: o[0].__setitem__("old", 1.0),
         lambda o: ch.rename_attribute(o, "old", "new"),
         lambda o: [k for k in ("old", "new") if k in o[0].keys()], ["old"], ["new"]),
        ("rename_node", ["U"], none,
         lambda o: ch.rename_node(o[0], "Renamed"), lambda o: o[0].name, "U", "Renamed"),
        ("paste_values", ["U"], copied_scale_2,
         lambda o: ch.paste_values(o), scale_x, 1.0, 2.0),
        ("freeze_transforms", ["U"], scale_2,
         lambda o: ch.freeze_transforms(o, [T["scale_x"]]), scale_x, 2.0, 1.0),
        ("unfreeze_transforms", ["U"], frozen_scale_2,
         lambda o: ch.unfreeze_transforms(o), scale_x, 1.0, 2.0),
        ("a drag-fill (editBatchStarted / editBatchFinished)", ["U"], none, fill,
         lambda o: [round(v, 4) for v in o[0].scale], [1.0] * 3, [2.5] * 3),
        ("an icon drag down the Lock column", ["U"], none,
         lambda o: stand_in(lock_rows)._on_icon_cells_dragged(
             [0, 1, 2], ChannelsSlots.COL_LOCK
         ),
         lambda o: list(o[0].lock_location), [False] * 3, [True] * 3),
        ("an MMB scrub", ["U"], none, scrub, scale_x, 1.0, 2.0),
        ("a wheel notch over two objects", ["U", "V"], none,
         lambda o: stand_in([T["scale_x"]])._on_wheel_scrolled(
             0, ChannelsSlots.COL_VALUE, 1, 0
         ),
         lambda o: [round(x.scale[0], 4) for x in o], [1.0, 1.0], [1.1, 1.1]),
    ]
    # fmt: on
    for name, names, setup, edit, read, before, after in cases:
        try:
            got = undo_round(names, setup, edit, read)
        except Exception as e:
            got = f"raised {type(e).__name__}: {e}"
        check(
            f"undo: {name} is one step (one Ctrl+Z reverts it alone, redo restores it)",
            got == (after, before, True, after),
            f"(edited, undone, previous op kept, redone)={got}",
        )
    check(
        "undo: no bracket left global undo off",
        bpy.context.preferences.edit.use_global_undo is True,
    )
    try:
        ChannelsSlots._on_edit_batch_finished(SimpleNamespace(_edit_chunk=None))
        unmatched = ""
    except Exception as e:
        unmatched = f"{type(e).__name__}: {e}"
    check("undo: an unmatched batch finish closes nothing", not unmatched, unmatched)

    # --- The panel's Slots module imports without Qt -----------------------
    # Headless Blender ships no Qt binding, so a Qt-only uitk import belongs in
    # the method that uses it (blendertk/CLAUDE.md). uitk is put on the path and
    # qtpy blocked, so the check fails only on a module-top Qt import.
    import importlib

    _slots_mod = "blendertk.node_utils.attributes.channels.channels_slots"
    _saved_path = list(sys.path)
    _saved_qtpy = sys.modules.get("qtpy", "absent")
    sys.path.insert(0, os.path.join(MONO, "uitk"))
    sys.modules["qtpy"] = None  # ``import qtpy`` now raises ImportError
    sys.modules.pop(_slots_mod, None)
    try:
        importlib.import_module(_slots_mod)
        slots_err = ""
    except ImportError as e:
        slots_err = repr(e)
    finally:
        sys.path[:] = _saved_path
        if _saved_qtpy == "absent":
            sys.modules.pop("qtpy", None)
        else:
            sys.modules["qtpy"] = _saved_qtpy
        sys.modules.pop(_slots_mod, None)
    check("channels_slots imports without Qt", not slots_err, slots_err)

except Exception as e:
    traceback.print_exc()
    check("test raised", False, repr(e))

passed = sum(1 for line in lines if line.startswith("OK"))
for line in lines:
    print(line)
result = "PASS" if all(line.startswith("OK") for line in lines) else "FAIL"
print(f"===RESULT: {result}=== ({passed}/{len(lines)})")
