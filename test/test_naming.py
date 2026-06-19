"""blendertk Naming (btk.Naming engine) headless test.
Run: blender --background --factory-startup --python blendertk/test/test_naming.py

Covers the engine — pattern rename (replace-all / append-suffix / filter / retain-suffix), set_case,
strip_chars, suffix_by_type (Blender type map), append_location_based_suffix (distance ordering,
alphabetical + integer), generate_unique_name, strip_illegal_chars. The NamingSlots Qt wiring is
covered by the handler test (under .venv).
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
    from blendertk.edit_utils.naming._naming import Naming

    def reset():
        bpy.ops.object.select_all(action="DESELECT")
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)

    def empty(name, x=0.0, parent=None):
        o = bpy.data.objects.new(name, None)
        o.location = (x, 0, 0)
        bpy.context.collection.objects.link(o)
        if parent:
            o.parent = parent
        return o

    def typed(name, kind):
        if kind == "MESH":
            data = bpy.data.meshes.new(name)
        elif kind == "CURVE":
            data = bpy.data.curves.new(name, "CURVE")
        elif kind == "CAMERA":
            data = bpy.data.cameras.new(name)
        elif kind == "LIGHT":
            data = bpy.data.lights.new(name, "POINT")
        elif kind == "ARMATURE":
            data = bpy.data.armatures.new(name)
        o = bpy.data.objects.new(name, data)
        bpy.context.collection.objects.link(o)
        return o

    # ---- rename: replace-all ------------------------------------------------------------------
    reset()
    o = empty("Cube")
    Naming.rename([o], "Box")
    check("rename replace-all", o.name == "Box", o.name)

    # ---- rename: append-suffix (**chars) ------------------------------------------------------
    reset()
    o = empty("Box")
    Naming.rename([o], "**_GEO")
    check("rename append-suffix", o.name == "Box_GEO", o.name)

    # ---- rename: filter excludes non-matches --------------------------------------------------
    reset()
    a, b = empty("Cube"), empty("Sphere")
    Naming.rename([a, b], "**_X", fltr="*Cube*")
    check("rename filter matches only Cube", a.name == "Cube_X" and b.name == "Sphere",
          f"{a.name},{b.name}")

    # ---- rename: retain_suffix re-appends the type suffix -------------------------------------
    reset()
    o = empty("Cube_GEO")
    Naming.rename([o], "Box", retain_suffix=True, valid_suffixes=["_GEO"])
    check("rename retain_suffix", o.name == "Box_GEO", o.name)

    # ---- set_case -----------------------------------------------------------------------------
    reset()
    o = empty("cube")
    Naming.set_case([o], "upper")
    check("set_case upper", o.name == "CUBE", o.name)
    Naming.set_case([o], "capitalize")
    check("set_case capitalize", o.name == "Cube", o.name)

    # ---- strip_chars (leading / trailing) -----------------------------------------------------
    reset()
    o = empty("XXcube")
    Naming.strip_chars([o], num_chars=2, trailing=False)
    check("strip leading chars", o.name == "cube", o.name)
    reset()
    o = empty("cubeYY")
    Naming.strip_chars([o], num_chars=2, trailing=True)
    check("strip trailing chars", o.name == "cube", o.name)

    # ---- suffix_by_type: Blender type map -----------------------------------------------------
    reset()
    mesh = typed("Cube", "MESH")
    grp = empty("Root")
    child = empty("Child", parent=grp)
    loc = empty("Helper")
    crv = typed("Path", "CURVE")
    cam = typed("Cam", "CAMERA")
    lgt = typed("Lamp", "LIGHT")
    arm = typed("Rig", "ARMATURE")
    Naming.suffix_by_type([mesh, grp, loc, crv, cam, lgt, arm])
    check("mesh → _GEO", mesh.name == "Cube_GEO", mesh.name)
    check("empty w/ children → _GRP", grp.name == "Root_GRP", grp.name)
    check("childless empty → _LOC", loc.name == "Helper_LOC", loc.name)
    check("curve → _CRV", crv.name == "Path_CRV", crv.name)
    check("camera → _CAM", cam.name == "Cam_CAM", cam.name)
    check("light → _LGT", lgt.name == "Lamp_LGT", lgt.name)
    check("armature → _JNT", arm.name == "Rig_JNT", arm.name)

    # ---- suffix_by_type: idempotent (already-suffixed stays) ----------------------------------
    Naming.suffix_by_type([mesh])
    check("suffix_by_type idempotent", mesh.name == "Cube_GEO", mesh.name)

    # ---- append_location_based_suffix: integer suffixes by distance ---------------------------
    reset()
    far = empty("right", x=3.0)
    near = empty("left", x=1.0)
    mid = empty("mid", x=2.0)
    Naming.append_location_based_suffix([far, near, mid], alphabetical=False)
    check("nearest → _01", near.name == "left_01", near.name)
    check("middle → _02", mid.name == "mid_02", mid.name)
    check("farthest → _03", far.name == "right_03", far.name)

    # ---- append_location_based_suffix: alphabetical -------------------------------------------
    reset()
    near = empty("a", x=1.0)
    far = empty("b", x=5.0)
    Naming.append_location_based_suffix([far, near], alphabetical=True)
    check("alphabetical nearest → _A", near.name == "a_A", near.name)
    check("alphabetical farthest → _B", far.name == "b_B", far.name)

    # ---- generate_unique_name -----------------------------------------------------------------
    reset()
    empty("Cube")
    check("unique name for existing → _001", Naming.generate_unique_name("Cube") == "Cube_001")
    check("unique name for free → unchanged", Naming.generate_unique_name("Free") == "Free")

    # ---- strip_illegal_chars ------------------------------------------------------------------
    check("strip illegal chars", Naming.strip_illegal_chars("a b-c") == "a_b_c")
    check("strip illegal chars (list)",
          Naming.strip_illegal_chars(["x.y", "z 1"]) == ["x_y", "z_1"])

except Exception:
    traceback.print_exc()
    lines.append("FAIL unhandled exception")

print("\n".join(lines))
ok = all(l.startswith("OK") for l in lines) and lines
print(f"===RESULT: {'PASS' if ok else 'FAIL'}=== ({sum(1 for l in lines if l.startswith('OK'))}/{len(lines)})")
