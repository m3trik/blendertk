"""blendertk.node_utils headless test — instancing via shared object data (no viewport).
Run: blender --background --factory-startup --python blendertk/test/test_node_utils.py
"""
import sys, os, traceback

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

    # regression: fake-user mesh with a single object is NOT reported as an instance
    reset()
    A = cube("A", (0, 0, 0))
    A.data.use_fake_user = True   # data.users == 2, but only ONE object references it
    check("get_instances ignores fake-user single object",
          A not in btk.get_instances(objects=None), f"data.users={A.data.users}")
    check("uninstance fake-user single object -> no copy",
          btk.uninstance([A]) == [] and A.data.use_fake_user, f"data.users={A.data.users}")

except Exception as e:
    lines.append(f"FAIL setup: {e!r}")
    lines.append(traceback.format_exc())

ok = all(l.startswith("OK") for l in lines)
print("\n===NODE-UTILS===")
print("\n".join(lines))
print(f"===RESULT: {'PASS' if ok else 'FAIL'}===")
