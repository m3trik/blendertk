"""blendertk cleanup_uv_sets headless test — empty-removal, keep-only-primary (largest area),
rename-to-map1 collisions (force on/off), dry-run, and the no-UV case.
Run: blender --background --factory-startup --python blendertk/test/test_uv_set_cleanup.py
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
    import bmesh
    import blendertk as btk

    def reset():
        if (
            bpy.context.view_layer.objects.active
            and bpy.context.view_layer.objects.active.mode != "OBJECT"
        ):
            bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.select_all(action="DESELECT")
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)

    def mesh_with_uvs(layer_specs, name):
        """A one-quad mesh carrying the given UV layers. ``layer_specs`` = list of
        ``(layer_name, (u0,v0,u1,v1) | None)`` — ``None`` fills the layer at the origin (empty).
        No specs at all → a mesh with zero UV layers."""
        bm = bmesh.new()
        verts = [bm.verts.new(p) for p in ((0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0))]
        face = bm.faces.new(verts)
        for lname, box in layer_specs:
            uvl = bm.loops.layers.uv.new(lname)
            for loop, (cu, cv) in zip(face.loops, ((0, 0), (1, 0), (1, 1), (0, 1))):
                if box is None:
                    loop[uvl].uv = (0.0, 0.0)
                else:
                    u0, v0, u1, v1 = box
                    loop[uvl].uv = (u0 + cu * (u1 - u0), v0 + cv * (v1 - v0))
        me = bpy.data.meshes.new(name)
        bm.to_mesh(me)
        bm.free()
        o = bpy.data.objects.new(name, me)
        bpy.context.collection.objects.link(o)
        o.select_set(True)
        bpy.context.view_layer.objects.active = o
        return o

    names_of = lambda o: [layer.name for layer in o.data.uv_layers]

    # S1 — remove empty + rename primary to map1
    reset()
    o = mesh_with_uvs([("UVMap", (0, 0, 1, 1)), ("extra", None)], "S1")
    r = btk.cleanup_uv_sets([o], remove_empty=True, rename_to_map1=True)[0]
    check("S1 empty set removed + primary renamed", names_of(o) == ["map1"], f"{names_of(o)}")
    check("S1 result reports the deletion", r.deleted == ["extra"], f"{r.deleted}")
    check("S1 result final_name=map1", r.final_name == "map1", r.final_name)

    # S2 — keep only primary, chosen by largest UV area
    reset()
    o = mesh_with_uvs(
        [("small", (0, 0, 0.2, 0.2)), ("big", (0, 0, 1, 1)), ("mid", (0, 0, 0.5, 0.5))], "S2"
    )
    btk.cleanup_uv_sets([o], keep_only_primary=True, rename_to_map1=False, prefer_largest_area=True)
    check("S2 keeps only the largest-area set", names_of(o) == ["big"], f"{names_of(o)}")

    # S3 — rename collision, force OFF → both sets kept, rename skipped
    reset()
    o = mesh_with_uvs([("UVMap", (0, 0, 1, 1)), ("map1", (0, 0, 0.3, 0.3))], "S3")
    r = btk.cleanup_uv_sets([o], remove_empty=False, rename_to_map1=True, force_rename=False)[0]
    check("S3 no-force keeps both sets", set(names_of(o)) == {"UVMap", "map1"}, f"{names_of(o)}")
    check("S3 no-force skips the rename", r.final_name == "UVMap", r.final_name)

    # S4 — rename collision, force ON → clash overwritten, primary becomes map1
    reset()
    o = mesh_with_uvs([("UVMap", (0, 0, 1, 1)), ("map1", (0, 0, 0.3, 0.3))], "S4")
    r = btk.cleanup_uv_sets([o], remove_empty=False, rename_to_map1=True, force_rename=True)[0]
    check("S4 force collapses to a single map1", names_of(o) == ["map1"], f"{names_of(o)}")
    check("S4 force reports the clash deleted", "map1" in r.deleted, f"{r.deleted}")

    # S5 — dry run changes nothing but still reports the plan
    reset()
    o = mesh_with_uvs([("UVMap", (0, 0, 1, 1)), ("extra", None)], "S5")
    r = btk.cleanup_uv_sets([o], remove_empty=True, rename_to_map1=True, dry_run=True)[0]
    check("S5 dry_run changes nothing", set(names_of(o)) == {"UVMap", "extra"}, f"{names_of(o)}")
    check(
        "S5 dry_run still reports plan",
        r.deleted == ["extra"] and r.final_name == "map1",
        f"deleted={r.deleted} final={r.final_name}",
    )

    # S6 — a mesh with no UV layers is reported, not crashed
    reset()
    o = mesh_with_uvs([], "S6")
    r = btk.cleanup_uv_sets([o])[0]
    check("S6 no-UV mesh reports an error", r.error == "no UV sets", str(r.error))

    # S7 — keep-only-primary deletes a 'map1' clash, freeing the name → rename proceeds (no force)
    reset()
    o = mesh_with_uvs([("UVMap", (0, 0, 1, 1)), ("map1", (0, 0, 0.3, 0.3))], "S7")
    btk.cleanup_uv_sets([o], keep_only_primary=True, rename_to_map1=True, force_rename=False)
    check("S7 deleted clash frees the name so rename proceeds", names_of(o) == ["map1"], f"{names_of(o)}")

except Exception:
    traceback.print_exc()
    lines.append("FAIL unhandled exception")

print("\n".join(lines))
ok = all(l.startswith("OK") for l in lines) and lines
print(f"===RESULT: {'PASS' if ok else 'FAIL'}=== ({sum(1 for l in lines if l.startswith('OK'))}/{len(lines)})")
