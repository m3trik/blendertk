"""blendertk mirror / cut_along_axis headless test — plane frames, merge modes, delete/mirror
sides. Run: blender --background --factory-startup --python blendertk/test/test_mirror_cut.py
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
    import blendertk as btk

    def reset():
        bpy.ops.object.select_all(action="DESELECT")
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)

    def cube_at(x=0.0, y=0.0, z=0.0, size=2.0):
        bpy.ops.mesh.primitive_cube_add(size=size, location=(x, y, z))
        return bpy.context.active_object

    def world_xs(o):
        return [(o.matrix_world @ v.co).x for v in o.data.vertices]

    def world_bbox_center(o):
        from mathutils import Vector
        cs = [o.matrix_world @ Vector(c) for c in o.bound_box]
        mn = Vector((min(c.x for c in cs), min(c.y for c in cs), min(c.z for c in cs)))
        mx = Vector((max(c.x for c in cs), max(c.y for c in cs), max(c.z for c in cs)))
        return (mn + mx) / 2.0

    # ---- mirror merge_mode=-1 (separate object) about world plane
    reset()
    o = cube_at(x=2.0)  # spans x 1..3
    n_objs = len(bpy.data.objects)
    created = btk.mirror(o, axis="x", pivot="world", merge_mode=-1)
    check("mirror -1 creates one new object", len(bpy.data.objects) == n_objs + 1)
    m = created[0]
    check("mirror -1 names it _mirror", m.name.endswith("_mirror"), m.name)
    xs = world_xs(m)
    check("mirror -1 reflected across world X (x in -3..-1)",
          max(xs) <= -0.99 and min(xs) >= -3.01, f"x range {min(xs):.2f}..{max(xs):.2f}")
    check("mirror -1 source untouched", abs(min(world_xs(o)) - 1.0) < 1e-5)
    # winding: reflected cube face normals point away from the mesh center (outward)
    m.data.update()
    center = sum((v.co for v in m.data.vertices), m.data.vertices[0].co * 0) / len(m.data.vertices)
    outward = all(p.normal.dot(p.center - center) > 0 for p in m.data.polygons)
    check("mirror -1 winding fixed (normals outward)", outward)

    # ---- mirror -1 delete_original removes the source
    reset()
    o = cube_at(x=2.0)
    name = o.name
    btk.mirror(o, axis="x", pivot="world", merge_mode=-1, delete_original=True)
    check("mirror -1 delete_original removes source", name not in bpy.data.objects)

    # ---- mirror merge_mode=0 (same mesh, unwelded): face/vert counts double
    reset()
    o = cube_at(x=2.0)
    btk.mirror(o, axis="x", pivot="world", merge_mode=0)
    check("mirror 0 doubles geometry in-mesh",
          len(o.data.polygons) == 12 and len(o.data.vertices) == 16,
          f"f={len(o.data.polygons)} v={len(o.data.vertices)}")

    # ---- mirror merge_mode=1 about the object's own min face (border pivot): welds seam
    reset()
    o = cube_at(x=2.0)  # spans 1..3; xmin face at x=1
    btk.mirror(o, axis="x", pivot="xmin", merge_mode=1)
    xs = world_xs(o)
    check("mirror 1 xmin spans -1..3", abs(min(xs) + 1.0) < 1e-4 and abs(max(xs) - 3.0) < 1e-4,
          f"{min(xs):.2f}..{max(xs):.2f}")
    check("mirror 1 welds seam verts (12 not 16)", len(o.data.vertices) == 12,
          f"v={len(o.data.vertices)}")

    # ---- mirror about object pivot follows the object's LOCAL axis when rotated
    reset()
    o = cube_at(x=2.0)
    o.rotation_euler = (0.0, 0.0, 1.5708)  # local X now points along world Y
    bpy.context.view_layer.update()
    btk.mirror(o, axis="x", pivot="object", merge_mode=0)
    ys = [(o.matrix_world @ v.co).y for v in o.data.vertices]
    # local-X mirror through the origin of an object at (2,0,0) rotated 90°: spans both sides in Y
    check("mirror object-pivot uses local axis", min(ys) < -0.9 and max(ys) > 0.9,
          f"y {min(ys):.2f}..{max(ys):.2f}")

    # ---- "manip" is an OBJECT-frame pivot, exactly like mayatk's OBJECT_FRAME_PIVOTS.
    # It used to return the manip POINT with a WORLD axis, so mirror(pivot="manip")
    # on a rotated object tilted the plane in Maya but stayed world-aligned here --
    # the twins produced different geometry from the same call. Decision 2026-08-21:
    # "manip" means point + the OBJECT's axes; blendertk adapts to mayatk.
    # Asserted on _plane_frame itself: the geometry-level assertions that first
    # suggested themselves were vacuous, since Blender's default cube already
    # spans +/-1 in every axis, so a span check passes without any mirror at all.
    from blendertk.edit_utils._edit_utils import _EditUtilsInternal as _EI
    from mathutils import Vector

    reset()
    o = cube_at(x=2.0)
    o.rotation_euler = (0.0, 0.0, 1.5708)  # local X now points along world Y
    bpy.context.view_layer.update()
    mp, mn_ = _EI._plane_frame(o, "x", "manip")
    op, on_ = _EI._plane_frame(o, "x", "object")
    check("manip plane normal follows the object's axes when rotated",
          abs(mn_.y) > 0.99 and abs(mn_.x) < 1e-3,
          f"normal=({mn_.x:.3f},{mn_.y:.3f},{mn_.z:.3f})")
    check("manip and object normals agree (both object-frame)",
          (mn_ - on_).length < 1e-6,
          f"manip={tuple(round(v,3) for v in mn_)} object={tuple(round(v,3) for v in on_)}")

    # The POINT must still come from the manip setting, not become the object
    # origin -- only the AXES changed. CURSOR resolves somewhere else entirely.
    _prev_pivot = bpy.context.scene.tool_settings.transform_pivot_point
    _prev_cursor = bpy.context.scene.cursor.location.copy()
    bpy.context.scene.cursor.location = (7.0, -3.0, 1.0)
    bpy.context.scene.tool_settings.transform_pivot_point = "CURSOR"
    cp, cn = _EI._plane_frame(o, "x", "manip")
    check("manip plane point still honors the pivot setting (cursor)",
          (cp - Vector((7.0, -3.0, 1.0))).length < 1e-6, f"point={tuple(round(v,2) for v in cp)}")
    check("manip cursor-pivot keeps the object's axes",
          abs(cn.y) > 0.99, f"normal=({cn.x:.3f},{cn.y:.3f},{cn.z:.3f})")
    # Restore what was there, not a hardcoded default: reset() clears objects
    # only, so tool settings and the 3D cursor leak into every later check.
    bpy.context.scene.tool_settings.transform_pivot_point = _prev_pivot
    bpy.context.scene.cursor.location = _prev_cursor

    # Counterweight: an UNROTATED object is unaffected (object axes == world axes),
    # which is every existing caller.
    reset()
    o = cube_at(x=2.0)
    bpy.context.view_layer.update()
    up, un = _EI._plane_frame(o, "x", "manip")
    check("manip plane normal unchanged for an unrotated object",
          abs(un.x) > 0.99 and abs(un.y) < 1e-3,
          f"normal=({un.x:.3f},{un.y:.3f},{un.z:.3f})")
    # ---- axis_frame names the frame outright, mirroring mtk.EditUtils. Until
    # 2026-08-21 there was no way to keep an object-frame pivot's POINT while
    # forcing WORLD axes: pivot="world" also moves the point to the origin, so
    # it was never an equivalent escape. The frame picks the AXES and the pivot
    # picks the POSITION -- independently, so no combination leaves either
    # parameter with nothing to say.
    reset()
    o = cube_at(x=2.0)
    o.rotation_euler = (0.0, 0.0, 1.5708)  # local X now points along world Y
    bpy.context.view_layer.update()

    wp, wn = _EI._plane_frame(o, "x", "object", axis_frame="world")
    check("axis_frame='world' forces world axes on an object-frame pivot",
          abs(wn.x) > 0.99 and abs(wn.y) < 1e-3,
          f"normal=({wn.x:.3f},{wn.y:.3f},{wn.z:.3f})")
    check("axis_frame='world' keeps the pivot's own point (not the origin)",
          (wp - o.matrix_world.translation).length < 1e-6,
          f"point={tuple(round(v,2) for v in wp)}")

    tp, tn = _EI._plane_frame(o, "x", (5.0, 1.0, 0.0), axis_frame="object")
    check("axis_frame='object' tilts the plane for a world TUPLE pivot",
          abs(tn.y) > 0.99 and abs(tn.x) < 1e-3,
          f"normal=({tn.x:.3f},{tn.y:.3f},{tn.z:.3f})")
    check("axis_frame='object' leaves the tuple point exactly where it was",
          (tp - Vector((5.0, 1.0, 0.0))).length < 1e-6,
          f"point={tuple(round(v,2) for v in tp)}")

    _ap, an = _EI._plane_frame(o, "x", (5.0, 1.0, 0.0))
    check("a tuple pivot still means world axes when the frame is left to auto",
          abs(an.x) > 0.99, f"normal=({an.x:.3f},{an.y:.3f},{an.z:.3f})")

    try:
        _EI._plane_frame(o, "x", "object", axis_frame="local")
        _rejected = False
    except ValueError:
        _rejected = True
    check("an unknown axis_frame is rejected, not silently world-aligned", _rejected)

    # It reaches the public entry point, not just the internal resolver: the
    # same call cuts a different half depending on the frame.
    reset()
    o = cube_at(x=2.0)
    o.rotation_euler = (0.0, 0.0, 1.5708)
    bpy.context.view_layer.update()
    btk.cut_along_axis(
        o, axis="x", pivot="object", amount=1, delete=True, axis_frame="world"
    )
    xs = world_xs(o)
    check("cut_along_axis axis_frame='world' clears the world +X half",
          max(xs) <= 2.001 and min(xs) >= 0.999,
          f"x range {min(xs):.2f}..{max(xs):.2f}")

    reset()
    o = cube_at(x=2.0)
    o.rotation_euler = (0.0, 0.0, 1.5708)
    bpy.context.view_layer.update()
    btk.cut_along_axis(o, axis="x", pivot="object", amount=1, delete=True)
    ys = [(o.matrix_world @ v.co).y for v in o.data.vertices]
    check("cut_along_axis left to auto still clears the OBJECT +X half (world +Y)",
          max(ys) <= 0.001, f"y max {max(ys):.2f}")

    # ---- mirroring a LINKED object: separate mode copies the data explicitly, so the
    # sibling survives untouched (Maya's polySeparate consumed both — see its changelog);
    # merge modes edit the shared data in place, so every linked duplicate updates together.
    reset()
    o = cube_at(x=2.0)
    sib = o.copy()  # linked duplicate: shares o.data
    for coll in o.users_collection:
        coll.objects.link(sib)
    sib.location.x += 10.0
    bpy.context.view_layer.update()
    sib_verts_before = len(sib.data.vertices)

    created = btk.mirror(o, axis="x", pivot="object", merge_mode=-1)
    check("mirror -1 on a linked object keeps the sibling", sib.name in bpy.data.objects)
    check("mirror -1 on a linked object leaves the sibling's geometry alone",
          len(sib.data.vertices) == sib_verts_before,
          f"verts {len(sib.data.vertices)} (was {sib_verts_before})")
    check("mirror -1 gives the new half its own data",
          created and created[0].data is not sib.data)

    # merge mode: the link is broken first, so the sibling is NOT rewritten in place
    reset()
    o = cube_at(x=2.0)
    sib = o.copy()
    for coll in o.users_collection:
        coll.objects.link(sib)
    sib.location.x += 10.0
    bpy.context.view_layer.update()
    sib_verts_before = len(sib.data.vertices)
    btk.mirror(o, axis="x", pivot="object", merge_mode=0)
    check("mirror merge breaks the link instead of rewriting the sibling",
          sib.data is not o.data and len(sib.data.vertices) == sib_verts_before,
          f"verts={len(sib.data.vertices)} (was {sib_verts_before}) shared={sib.data is o.data}")
    check("mirror merge still doubled the mirrored object", len(o.data.vertices) > sib_verts_before,
          f"verts={len(o.data.vertices)}")

    # ---- mirror_instance: LINKED duplicate (shared mesh data) reflected across the plane
    reset()
    o = cube_at(x=2.0)  # spans x 1..3
    n_objs = len(bpy.data.objects)
    created = btk.mirror_instance(o, axis="x", pivot="world")
    check("mirror_instance creates one new object", len(bpy.data.objects) == n_objs + 1)
    m = created[0]
    check("mirror_instance shares the source mesh data", m.data is o.data)
    check("mirror_instance names it _mirror", m.name.endswith("_mirror"), m.name)
    xs = world_xs(m)
    check("mirror_instance reflected across world X (x in -3..-1)",
          max(xs) <= -0.99 and min(xs) >= -3.01, f"x range {min(xs):.2f}..{max(xs):.2f}")
    check("mirror_instance is mirrored (negative determinant)",
          m.matrix_world.determinant() < 0, f"det={m.matrix_world.determinant():.3f}")
    check("mirror_instance leaves the source unmirrored",
          o.matrix_world.determinant() > 0)
    check("mirror_instance source geometry untouched", abs(min(world_xs(o)) - 1.0) < 1e-5)
    # editing the shared data must move BOTH halves — that's the point of instancing
    o.data.vertices[0].co.x += 5.0
    bpy.context.view_layer.update()
    check("mirror_instance halves stay linked (edit propagates)",
          abs(max(world_xs(m)) - (-1.0)) > 1e-3 or abs(min(world_xs(m)) + 3.0) > 1e-3,
          f"x range {min(world_xs(m)):.2f}..{max(world_xs(m)):.2f}")

    # ---- mirror_instance about the object pivot follows the object's LOCAL axis
    reset()
    o = cube_at(x=2.0)
    o.rotation_euler = (0.0, 0.0, 1.5708)  # local X now points along world Y
    bpy.context.view_layer.update()
    m = btk.mirror_instance(o, axis="x", pivot="object")[0]
    check("mirror_instance object-pivot keeps the copy on the object",
          (m.matrix_world.translation - o.matrix_world.translation).length < 1e-4,
          f"delta={(m.matrix_world.translation - o.matrix_world.translation).length:.4f}")
    check("mirror_instance object-pivot still mirrors", m.matrix_world.determinant() < 0)

    # ---- cut_along_axis: amount=2 adds two slices (cube 6 faces -> 6 + 2*4 = 14)
    reset()
    o = cube_at()
    btk.cut_along_axis(o, axis="x", pivot="center", amount=2)
    check("cut amount=2 slices the cube", len(o.data.polygons) == 14, f"f={len(o.data.polygons)}")

    def cut_xs(o, **kwargs):
        """Distinct x positions the cuts landed at (new verts vs a plain cube's ±1)."""
        btk.cut_along_axis(o, axis="x", pivot="center", **kwargs)
        return sorted({round(x, 4) for x in world_xs(o)} - {-1.0, 1.0})

    # legacy default preserved: 3 linear cuts even-fill the axis (span L*(n-1)/(n+1) = 1
    # for a 2-unit cube -> cuts at -0.5, 0, +0.5) — identical to the old spacing math
    reset()
    xs = cut_xs(cube_at(), amount=3)
    check("cut linear default = legacy even-fill", xs == [-0.5, 0.0, 0.5], str(xs))

    # spacing>0 fixes the per-cut gap: 3 cuts, spacing 0.25 -> -0.25, 0, +0.25
    reset()
    xs = cut_xs(cube_at(), amount=3, spacing=0.25)
    check("cut spacing fixes the gap", xs == [-0.25, 0.0, 0.25], str(xs))

    # non-linear distribution: ease_in keeps endpoints, biases the middle cut off-center
    reset()
    xs = cut_xs(cube_at(), amount=3, spacing=0.5, distribution="ease_in")
    mid = [x for x in xs if abs(x) < 0.49]
    check(
        "cut ease_in biases interior cuts (endpoints fixed)",
        len(xs) == 3 and abs(min(xs) + 0.5) < 1e-3 and abs(max(xs) - 0.5) < 1e-3
        and mid and mid[0] != 0.0,
        str(xs),
    )

    # ---- cut delete=True: 'x' deletes the +X half (Maya convention)
    reset()
    o = cube_at()
    btk.cut_along_axis(o, axis="x", pivot="center", amount=1, delete=True)
    xs = world_xs(o)
    check("cut delete 'x' removes +X half", max(xs) < 1e-4 and min(xs) < -0.9,
          f"x {min(xs):.2f}..{max(xs):.2f}")

    # ---- cut delete '-x' removes the -X half
    reset()
    o = cube_at()
    btk.cut_along_axis(o, axis="-x", pivot="center", amount=1, delete=True)
    xs = world_xs(o)
    check("cut delete '-x' removes -X half", min(xs) > -1e-4 and max(xs) > 0.9,
          f"x {min(xs):.2f}..{max(xs):.2f}")

    # ---- invert flips the convention (the Mirror panel's center-symmetrize path)
    reset()
    o = cube_at()
    btk.cut_along_axis(o, axis="x", pivot="center", amount=1, invert=True, delete=True)
    xs = world_xs(o)
    check("cut invert 'x' keeps +X half", min(xs) > -1e-4, f"x {min(xs):.2f}..{max(xs):.2f}")

    # ---- delete+mirror = symmetrize: keep one half, reflect it back, weld the seam
    reset()
    o = cube_at()
    v0, f0 = len(o.data.vertices), len(o.data.polygons)
    btk.cut_along_axis(o, axis="x", pivot="center", amount=1, invert=True, delete=True, mirror=True)
    xs = world_xs(o)
    check("symmetrize restores the full span", abs(min(xs) + 1.0) < 1e-4 and abs(max(xs) - 1.0) < 1e-4,
          f"x {min(xs):.2f}..{max(xs):.2f}")
    check("symmetrize welds the seam (no doubled plane verts)",
          len(o.data.vertices) == 12, f"v={len(o.data.vertices)}")  # cube cut at 0: 12 verts after weld

    # ---- offset shifts the cut plane
    reset()
    o = cube_at()
    btk.cut_along_axis(o, axis="x", pivot="center", amount=1, offset=0.5, delete=True)
    xs = world_xs(o)
    check("cut offset moves the plane (+0.5)", abs(max(xs) - 0.5) < 1e-4,
          f"max x {max(xs):.2f}")

    # ---- PIVOT CENTERING ---------------------------------------------------------
    # merge combines into the source object; its old origin is now off the doubled
    # result, so it re-centers on the combined bbox (cube 1..3 mirror world -> -3..3).
    reset()
    o = cube_at(x=2.0)
    btk.mirror(o, axis="x", pivot="world", merge_mode=1)
    ox = o.matrix_world.translation.x
    check("merge re-centers origin on combined bbox (x~0)", abs(ox) < 1e-4, f"origin x {ox:.3f}")

    # center_pivot=False keeps the pre-mirror origin (opt-out escape hatch)
    reset()
    o = cube_at(x=2.0)
    btk.mirror(o, axis="x", pivot="world", merge_mode=1, center_pivot=False)
    ox = o.matrix_world.translation.x
    check("merge center_pivot=False keeps origin (x~2)", abs(ox - 2.0) < 1e-4, f"origin x {ox:.3f}")

    # separate: the NEW half centers on ITSELF; the SOURCE origin is left untouched
    reset()
    o = cube_at(x=2.0)  # origin at x=2
    src_x = o.matrix_world.translation.x
    created = btk.mirror(o, axis="x", pivot="world", merge_mode=-1)  # new spans -3..-1
    m = created[0]
    mx = m.matrix_world.translation.x
    check("separate centers new half on itself (x~-2)", abs(mx + 2.0) < 1e-4, f"new origin x {mx:.3f}")
    check("separate leaves source origin untouched (x~2)",
          abs(o.matrix_world.translation.x - src_x) < 1e-4, f"src origin x {o.matrix_world.translation.x:.3f}")

    # symmetrize (cut mirror+delete) combines into the object -> re-center. Offset the mesh
    # off its origin first so a no-op would be visible (origin 0, geometry world 2..4).
    reset()
    o = cube_at()
    for v in o.data.vertices:
        v.co.x += 3.0
    o.data.update()
    bpy.context.view_layer.update()
    btk.cut_along_axis(o, axis="x", pivot="center", amount=1, invert=True, delete=True, mirror=True)
    c = world_bbox_center(o)
    otr = o.matrix_world.translation
    check("symmetrize re-centers origin on result bbox", (otr - c).length < 1e-4,
          f"origin {tuple(round(v,2) for v in otr)} vs center {tuple(round(v,2) for v in c)}")
    check("symmetrize actually moved the origin off 0", abs(otr.x) > 0.5, f"origin x {otr.x:.2f}")

    # a plain cut (no mirror) is not a combine -> origin left alone
    reset()
    o = cube_at()
    for v in o.data.vertices:
        v.co.x += 3.0
    o.data.update()
    bpy.context.view_layer.update()
    btk.cut_along_axis(o, axis="x", pivot="center", amount=1, delete=True)  # trims a half
    check("plain cut leaves origin untouched (x~0)", abs(o.matrix_world.translation.x) < 1e-4,
          f"origin x {o.matrix_world.translation.x:.3f}")

except Exception:
    traceback.print_exc()
    lines.append("FAIL unhandled exception")

print("\n".join(lines))
ok = all(l.startswith("OK") for l in lines) and lines
print(f"===RESULT: {'PASS' if ok else 'FAIL'}=== ({sum(1 for l in lines if l.startswith('OK'))}/{len(lines)})")
