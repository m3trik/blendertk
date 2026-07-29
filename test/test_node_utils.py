"""blendertk.node_utils headless test — instancing via shared object data (no viewport).
Run: blender --background --factory-startup --python blendertk/test/test_node_utils.py
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
    import blendertk as btk

    def reset():
        bpy.ops.object.select_all(action="DESELECT")
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)
    def cube(name, loc):
        bpy.ops.mesh.primitive_cube_add(location=loc)
        o = bpy.context.active_object; o.name = name
        return o

    # replace_with_instances: A(source) + B,C targets -> B,C share A's data
    reset()
    A, B, C = cube("A", (0, 0, 0)), cube("B", (3, 0, 0)), cube("C", (6, 0, 0))
    out = btk.replace_with_instances([A, B, C])
    check("replace_with_instances -> 2 targets instanced", len(out) == 2, f"n={len(out)}")
    check("replace_with_instances -> B shares A data", B.data is A.data)
    check("replace_with_instances -> C shares A data", C.data is A.data)
    check("replace_with_instances -> data.users == 3", A.data.users == 3, f"users={A.data.users}")

    # guard: <2 objects -> no-op, returns []
    check("replace_with_instances <2 -> []", btk.replace_with_instances([A]) == [])

    # get_instances(None): scene-wide shared datablocks (A,B,C all share -> all 3)
    inst = btk.get_instances(objects=None)
    check("get_instances(None) -> 3 instanced", len(inst) == 3, f"n={len(inst)}")
    # add a lone cube D -> not instanced, not returned
    D = cube("D", (9, 0, 0))
    inst = btk.get_instances(objects=None)
    check("get_instances ignores single-user D", D not in inst and len(inst) == 3, f"n={len(inst)}")
    # get_instances(subset) -> instances sharing data with B (= A,B,C)
    inst_b = btk.get_instances([B])
    check("get_instances([B]) -> the A/B/C group", len(inst_b) == 3 and D not in inst_b, f"n={len(inst_b)}")

    # uninstance B -> B gets its own copy, users drop to 2
    changed = btk.uninstance([B])
    check("uninstance -> 1 changed", len(changed) == 1, f"n={len(changed)}")
    check("uninstance -> B distinct from A", B.data is not A.data)
    check("uninstance -> A data.users == 2", A.data.users == 2, f"users={A.data.users}")
    # uninstancing a single-user object is a no-op
    check("uninstance single-user D -> []", btk.uninstance([D]) == [])

    # center_pivot flag honored (source origin moves to bbox center) — smoke that it doesn't raise
    reset()
    A = cube("A", (5, 0, 0))
    for v in A.data.vertices:
        v.co.x += 2.0
    bpy.context.view_layer.update()
    B = cube("B", (0, 0, 0))
    btk.replace_with_instances([A, B], center_pivot=True)
    check("replace_with_instances center_pivot -> A origin re-centered to 7",
          abs(A.location.x - 7.0) < 1e-3, f"x={A.location.x:.3f}")
    check("replace_with_instances center_pivot -> B shares A data", B.data is A.data)

    # regression: freeze_transforms pre-cleans only the SOURCE -> the target keeps its world
    # position (a naive whole-list freeze would zero the target's location, relocating it).
    reset()
    A = cube("A", (0, 0, 0)); B = cube("B", (4, 0, 0))
    btk.replace_with_instances([A, B], freeze_transforms=True)
    check("freeze flag leaves target B in place (world x=4)",
          abs(B.matrix_world.translation.x - 4.0) < 1e-3, f"x={B.matrix_world.translation.x:.3f}")
    check("freeze flag -> B still shares A data", B.data is A.data)

    # retain_bbox_scale: target's size lives in its GEOMETRY (scale channels stay 1), so
    # adopting the source's smaller data would shrink it -> rescale back to its own bbox size.
    def world_size_x(o):
        bpy.context.view_layer.update()  # bound_box/matrix_world are lazily evaluated
        mn, mx = btk.get_world_bbox(o)
        return (mx - mn).x

    reset()
    A = cube("A", (0, 0, 0))                       # 2 units (default cube)
    B = cube("B", (10, 0, 0))
    for v in B.data.vertices:                      # 6 units, scale channels still 1
        v.co *= 3.0
    bpy.context.view_layer.update()
    btk.replace_with_instances([A, B])             # off (default): B takes A's size
    check("retain_bbox_scale off -> B shrinks to source size",
          abs(world_size_x(B) - world_size_x(A)) < 1e-3, f"x={world_size_x(B):.3f}")

    reset()
    A = cube("A", (0, 0, 0))
    B = cube("B", (10, 0, 0))
    for v in B.data.vertices:
        v.co *= 3.0
    bpy.context.view_layer.update()
    want = world_size_x(B)
    btk.replace_with_instances([A, B], retain_bbox_scale=True)
    check("retain_bbox_scale -> B keeps its own world bbox size",
          abs(world_size_x(B) - want) < 1e-3, f"x={world_size_x(B):.3f} want={want:.3f}")
    check("retain_bbox_scale -> B still shares A data", B.data is A.data)
    check("retain_bbox_scale -> uniform scale factor",
          abs(B.scale.x - B.scale.y) < 1e-6 and abs(B.scale.x - B.scale.z) < 1e-6,
          f"scale={tuple(round(v, 4) for v in B.scale)}")

    # retain_bbox_per_axis: fits each axis independently, measured in the LOCAL frame -> a
    # ROTATED target still lands on its own proportions (a world-axis ratio would not).
    reset()
    A = cube("A", (0, 0, 0))                       # 2 x 2 x 2
    B = cube("B", (10, 0, 0))
    for v in B.data.vertices:                      # 2 x 4 x 8, baked into the mesh
        v.co.y *= 2.0
        v.co.z *= 4.0
    B.rotation_euler = (0.0, math.radians(45.0), 0.0)
    bpy.context.view_layer.update()
    want_world = [round(v, 4) for v in (btk.get_world_bbox(B)[1] - btk.get_world_bbox(B)[0])]
    btk.replace_with_instances([A, B], retain_bbox_scale=True, retain_bbox_per_axis=True)
    bpy.context.view_layer.update()
    got_world = [round(v, 4) for v in (btk.get_world_bbox(B)[1] - btk.get_world_bbox(B)[0])]
    check("retain_bbox_per_axis -> rotated target keeps its world bbox",
          all(abs(g - w) < 1e-3 for g, w in zip(got_world, want_world)),
          f"got={got_world} want={want_world}")
    check("retain_bbox_per_axis -> non-uniform local scale 1:2:4",
          abs(B.scale.y / B.scale.x - 2.0) < 1e-3 and abs(B.scale.z / B.scale.x - 4.0) < 1e-3,
          f"scale={tuple(round(v, 4) for v in B.scale)}")

    # a mirrored target (negative scale) stays mirrored: bbox extents are unsigned, so the
    # ratio is always positive and the sign of each channel survives the fit untouched.
    for per_axis in (False, True):
        reset()
        A = cube("A", (0, 0, 0))                   # 2 units
        B = cube("B", (10, 0, 0))
        for v in B.data.vertices:
            v.co *= 3.0                            # 6 units
        B.scale.x = -1.0                           # mirrored
        bpy.context.view_layer.update()
        btk.replace_with_instances([A, B], retain_bbox_scale=True, retain_bbox_per_axis=per_axis)
        check(f"retain_bbox_scale(per_axis={per_axis}) -> mirror preserved",
              B.scale.x < 0 and B.scale.y > 0 and B.scale.z > 0,
              f"scale={tuple(round(v, 4) for v in B.scale)}")
        check(f"retain_bbox_scale(per_axis={per_axis}) -> mirrored target keeps its size",
              abs(world_size_x(B) - 6.0) < 1e-3, f"x={world_size_x(B):.3f}")
        check(f"retain_bbox_scale(per_axis={per_axis}) -> source not mirrored", A.scale.x > 0)

    # a degenerate axis (flat target vs. solid source) has no reproducible ratio -> left alone
    reset()
    A = cube("A", (0, 0, 0))
    bpy.ops.mesh.primitive_plane_add(location=(10, 0, 0))   # 2 x 2 x 0
    B = bpy.context.active_object; B.name = "B"
    for v in B.data.vertices:
        v.co *= 3.0                                          # 6 x 6 x 0
    bpy.context.view_layer.update()
    btk.replace_with_instances([A, B], retain_bbox_scale=True, retain_bbox_per_axis=True)
    check("retain_bbox_per_axis -> flat axis keeps scale 1 (no collapse)",
          abs(B.scale.x - 3.0) < 1e-3 and abs(B.scale.y - 3.0) < 1e-3 and abs(B.scale.z - 1.0) < 1e-3,
          f"scale={tuple(round(v, 4) for v in B.scale)}")

    # regression: fake-user mesh with a single object is NOT reported as an instance
    reset()
    A = cube("A", (0, 0, 0))
    A.data.use_fake_user = True   # data.users == 2, but only ONE object references it
    check("get_instances ignores fake-user single object",
          A not in btk.get_instances(objects=None), f"data.users={A.data.users}")
    check("uninstance fake-user single object -> no copy",
          btk.uninstance([A]) == [] and A.data.use_fake_user, f"data.users={A.data.users}")

    # --- hierarchy helpers: get_parent / get_children / get_shape / reparent ---
    reset()
    p = cube("Parent", (0, 0, 0))
    c1 = cube("Child1", (5, 0, 0))
    c2 = cube("Child2", (0, 5, 0))
    w1 = tuple(round(v, 3) for v in c1.matrix_world.translation)
    out = btk.reparent([c1, c2], p)
    check("reparent sets parent + keeps world transform",
          out == [c1, c2] and c1.parent is p
          and tuple(round(v, 3) for v in c1.matrix_world.translation) == w1, f"{w1}")
    check("get_parent immediate", btk.get_parent(c1) is p)
    check("get_children", set(btk.get_children(p)) == {c1, c2})
    g = cube("Grand", (5, 5, 0))
    btk.reparent(g, c1)
    check("get_children recursive", set(btk.get_children(p, recursive=True)) == {c1, c2, g})
    check("get_parent all -> ancestor chain", btk.get_parent(g, all=True) == [c1, p])
    check("get_shape returns object data", btk.get_shape(c1) is c1.data)
    btk.reparent(c1, None)   # unparent, keep transform
    check("reparent to None unparents",
          c1.parent is None and tuple(round(v, 3) for v in c1.matrix_world.translation) == w1)
    check("reparent skips self-parent", btk.reparent([p], p) == [])

    # --- DataNodes.dump / format_dump: read every channel a scene carries ---
    import json as _json
    from blendertk.node_utils.data_nodes import DataNodes
    reset()
    empty = DataNodes.dump()
    check("dump empty -> empty groups",
          empty == {DataNodes.INTERNAL: {}, DataNodes.EXPORT: {}}, f"{empty}")
    check("format_dump empty -> ''", DataNodes.format_dump() == "")
    DataNodes.set_internal_string("app_state", '{"open": true}')
    DataNodes.set_export_string("wire", "abc")
    data = DataNodes.dump()   # decode=True default
    check("dump groups internal channel + decodes JSON",
          data[DataNodes.INTERNAL] == {"app_state": {"open": True}}, f"{data[DataNodes.INTERNAL]}")
    check("dump groups export channel (plain string)",
          data[DataNodes.EXPORT] == {"wire": "abc"}, f"{data[DataNodes.EXPORT]}")
    check("dump decode=False keeps raw JSON string",
          DataNodes.dump(decode=False)[DataNodes.INTERNAL]["app_state"] == '{"open": true}')
    DataNodes.set_internal_string("dead", "y")
    DataNodes.set_internal_string("dead", "")   # clear (key stays, value empty)
    check("dump skips cleared channel", "dead" not in DataNodes.dump()[DataNodes.INTERNAL])
    # non-string custom props (the audio tool's per-track flags) are real stored data — kept.
    DataNodes.get_internal_node()["audio_clip_voice"] = 1
    check("dump includes non-string channel",
          DataNodes.dump()[DataNodes.INTERNAL].get("audio_clip_voice") == 1)
    parsed = _json.loads(DataNodes.format_dump())
    check("format_dump -> valid JSON round-trip (mixed types)",
          parsed[DataNodes.EXPORT]["wire"] == "abc"
          and parsed[DataNodes.INTERNAL]["app_state"] == {"open": True}
          and parsed[DataNodes.INTERNAL]["audio_clip_voice"] == 1)

except Exception as e:
    lines.append(f"FAIL setup: {e!r}")
    lines.append(traceback.format_exc())

ok = all(l.startswith("OK") for l in lines)
print("\n===NODE-UTILS===")
print("\n".join(lines))
print(f"===RESULT: {'PASS' if ok else 'FAIL'}===")
