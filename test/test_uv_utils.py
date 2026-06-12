"""blendertk.uv_utils headless test — UV translate + UV-set cleanup (mesh UV data, no editor).
Run: blender --background --factory-startup --python blendertk/test/test_uv_utils.py
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
    import bpy, bmesh
    import blendertk as btk

    def reset():
        bpy.ops.object.select_all(action="DESELECT")
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)
    def uv_bounds(o):
        bm = bmesh.new(); bm.from_mesh(o.data); uvl = bm.loops.layers.uv.active
        us = [loop[uvl].uv.x for f in bm.faces for loop in f.loops]
        vs = [loop[uvl].uv.y for f in bm.faces for loop in f.loops]
        bm.free()
        return min(us), max(us), min(vs), max(vs)

    # move_uvs object mode: +1 in u
    reset()
    bpy.ops.mesh.primitive_plane_add(); o = bpy.context.active_object  # default UV 0..1
    u0 = uv_bounds(o)[0]
    btk.move_uvs(o, du=1.0)
    check("move_uvs du=+1 -> u shifted +1", abs(uv_bounds(o)[0] - (u0 + 1.0)) < 1e-4,
          f"minu {u0:.2f}->{uv_bounds(o)[0]:.2f}")
    btk.move_uvs(o, dv=-2.0)
    check("move_uvs dv=-2 -> v shifted -2", abs(uv_bounds(o)[2] - (-2.0)) < 1e-4,
          f"minv={uv_bounds(o)[2]:.2f}")

    # move_uvs edit mode: live bmesh update
    reset()
    bpy.ops.mesh.primitive_plane_add(); o = bpy.context.active_object
    o.select_set(True); bpy.context.view_layer.objects.active = o
    bpy.ops.object.mode_set(mode="EDIT")
    btk.move_uvs(o, du=1.0)
    bpy.ops.object.mode_set(mode="OBJECT")
    check("move_uvs in EDIT mode -> u shifted +1", abs(uv_bounds(o)[0] - 1.0) < 1e-4,
          f"minu={uv_bounds(o)[0]:.2f}")

    # delete_extra_uv_sets: 3 -> 1 (keeps first)
    reset()
    bpy.ops.mesh.primitive_cube_add(); o = bpy.context.active_object
    first = o.data.uv_layers[0].name
    o.data.uv_layers.new(name="extra1"); o.data.uv_layers.new(name="extra2")
    btk.delete_extra_uv_sets(o)
    check("delete_extra_uv_sets -> 1 layer left", len(o.data.uv_layers) == 1, f"n={len(o.data.uv_layers)}")
    check("delete_extra_uv_sets -> kept the first", o.data.uv_layers[0].name == first,
          f"kept={o.data.uv_layers[0].name}")

    # no UV layer -> move_uvs verify() creates one, no crash
    reset()
    bpy.ops.mesh.primitive_cube_add(); o = bpy.context.active_object
    while o.data.uv_layers:
        o.data.uv_layers.remove(o.data.uv_layers[0])
    btk.move_uvs(o, du=1.0)
    check("move_uvs with no UV layer -> creates one (no crash)", len(o.data.uv_layers) >= 1)

    # non-mesh ignored
    reset()
    bpy.ops.object.empty_add(); e = bpy.context.active_object
    btk.move_uvs(e, du=1.0); btk.delete_extra_uv_sets(e)
    check("non-mesh ignored (no crash)", True)

except Exception as e:
    lines.append(f"FAIL setup: {e!r}")
    lines.append(traceback.format_exc())

ok = all(l.startswith("OK") for l in lines)
print("\n===UV-UTILS===")
print("\n".join(lines))
print(f"===RESULT: {'PASS' if ok else 'FAIL'}===")
