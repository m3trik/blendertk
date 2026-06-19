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

    # ---- cut_along_axis: amount=2 adds two slices (cube 6 faces -> 6 + 2*4 = 14)
    reset()
    o = cube_at()
    btk.cut_along_axis(o, axis="x", pivot="center", amount=2)
    check("cut amount=2 slices the cube", len(o.data.polygons) == 14, f"f={len(o.data.polygons)}")

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

except Exception:
    traceback.print_exc()
    lines.append("FAIL unhandled exception")

print("\n".join(lines))
ok = all(l.startswith("OK") for l in lines) and lines
print(f"===RESULT: {'PASS' if ok else 'FAIL'}=== ({sum(1 for l in lines if l.startswith('OK'))}/{len(lines)})")
