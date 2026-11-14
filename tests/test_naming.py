"""blendertk Naming (btk.Naming engine) headless test.
Run: blender --background --factory-startup --python blendertk/tests/test_naming.py

Covers the engine — pattern rename (replace-all / append-suffix / filter / retain-suffix), set_case,
strip_chars, suffix_by_type (Blender type map), append_location_based_suffix (distance ordering,
alphabetical + integer), generate_unique_name, strip_illegal_chars, and the API tentacle's Naming
panel calls (scope_objects, select_by_name, select_link, UNSUPPORTED_TYPES).
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
    lines.append(
        f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + str(detail)) if detail else ''}"
    )


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
    check(
        "rename filter matches only Cube",
        a.name == "Cube_X" and b.name == "Sphere",
        f"{a.name},{b.name}",
    )

    # ---- rename: retain_suffix re-appends the type suffix -------------------------------------
    reset()
    o = empty("Cube_GEO")
    Naming.rename([o], "Box", retain_suffix=True, valid_suffixes=["_GEO"])
    check("rename retain_suffix", o.name == "Box_GEO", o.name)

    # ---- rename: retain only carries a DEFINED suffix, once (mayatk parity) -------------------
    reset()
    o = empty("wall_low")
    Naming.rename([o], "Prop", retain_suffix=True)  # default: the shared convention
    check("rename retain skips undefined suffix", o.name == "Prop", o.name)
    reset()
    o = empty("Sphere_GEO")
    Naming.rename([o], "**_A", retain_suffix=True, valid_suffixes=["_GEO"])
    check("rename retain not doubled by append", o.name == "Sphere_GEO_A", o.name)
    reset()
    o = empty("pCube_GEO1")
    Naming.rename([o], "**_A", retain_suffix=True, valid_suffixes=["_GEO"])
    check("rename retain keeps numbering", o.name == "pCube_GEO1_A", o.name)

    # ---- rename: strip collapses separator residue (mayatk parity) ----------------------------
    reset()
    o = empty("prop__uninst_tmp__uninst_tmpShape702")
    Naming.rename([o], "", fltr="*uninst_tmp*")
    check("rename strip collapses residue", o.name == "prop_Shape702", o.name)
    reset()
    o = empty("PlainCube")
    Naming.rename([o], "foo__bar")
    check("rename explicit __ preserved", o.name == "foo__bar", o.name)

    # ---- rename: paired '|' terms (mayatk parity) ---------------------------------------------
    reset()
    a, b = empty("arm_L"), empty("arm_R")
    Naming.rename([a, b], "*_lt|*_rt", fltr="*_L|*_R")
    check(
        "rename paired terms",
        a.name == "arm_lt" and b.name == "arm_rt",
        f"{a.name},{b.name}",
    )

    # ---- rename: each filter term supplies its own 'from' text --------------------------------
    reset()
    a, b = empty("pCube1"), empty("nurbsSphere1")
    Naming.rename([a, b], "*box*", fltr="pCube*|nurbs*")
    check(
        "rename multi-term filter",
        a.name == "box1" and b.name == "boxSphere1",
        f"{a.name},{b.name}",
    )

    # ---- rename: regex capture-group backref --------------------------------------------------
    reset()
    o = empty("pCube1")
    Naming.rename([o], r"*\1_GEO", fltr=r"Cube(\d+)", regex=True)
    check("rename regex backref", o.name == "p1_GEO", o.name)

    # ---- rename: regex replace-suffix cuts at the match ---------------------------------------
    reset()
    o = empty("pCube1")
    Naming.rename([o], "*_GEO", fltr=r"Cube.*", regex=True)
    check("rename regex replace-suffix", o.name == "p_GEO", o.name)

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

    # A locator rig's locator holds its object and is still a locator: its Maya node-type
    # stamp decides, as Maya's shape does -- never "_GRP" for parenting something.
    reset()
    rig_loc = empty("Rig")
    empty("Held", parent=rig_loc)
    btk.NodeUtils.set_maya_node_type(rig_loc, "locator")
    Naming.suffix_by_type([rig_loc])
    check("typed locator w/ children → _LOC", rig_loc.name == "Rig_LOC", rig_loc.name)

    # ---- suffix_by_type: expanded types (surface / lattice / material / image) ----------------
    reset()
    srf_data = bpy.data.curves.new("Patch", "SURFACE")
    srf = bpy.data.objects.new("Patch", srf_data)
    bpy.context.collection.objects.link(srf)
    lat = bpy.data.objects.new("Cage", bpy.data.lattices.new("Cage"))
    bpy.context.collection.objects.link(lat)
    mat = bpy.data.materials.new("Brick")
    img = bpy.data.images.new("Brick_D", 4, 4)
    Naming.suffix_by_type([srf, lat, mat, img])
    check("surface → _SRF", srf.name == "Patch_SRF", srf.name)
    check("lattice → _LAT", lat.name == "Cage_LAT", lat.name)
    check("material → _MAT", mat.name == "Brick_MAT", mat.name)
    check("image → _TEX", img.name == "Brick_D_TEX", img.name)
    check("type_key empty/material", Naming.type_key(mat) == "material")

    # ---- suffix_by_type: a wrong suffix from the expanded set is stripped first ----------------
    reset()
    mesh = typed("Wall_SRF", "MESH")
    Naming.suffix_by_type([mesh])
    check("wrong suffix stripped", mesh.name == "Wall_GEO", mesh.name)

    # ---- dry_run: every operation plans + reports without renaming ---------------------------
    reset()
    a, b = empty("Cube"), empty("Sphere")
    planned = Naming.rename([a, b], "**_X", dry_run=True)
    check("rename dry_run plans", planned == ["Cube_X", "Sphere_X"], str(planned))
    check("rename dry_run untouched", a.name == "Cube" and b.name == "Sphere")
    planned = Naming.set_case([a], "upper", dry_run=True)
    check("set_case dry_run", planned == ["CUBE"] and a.name == "Cube", str(planned))
    planned = Naming.strip_chars([a], num_chars=1, dry_run=True)
    check("strip_chars dry_run", planned == ["ube"] and a.name == "Cube", str(planned))
    mesh = typed("Box", "MESH")
    planned = Naming.suffix_by_type([mesh], dry_run=True)
    check(
        "suffix_by_type dry_run",
        planned == ["Box_GEO"] and mesh.name == "Box",
        str(planned),
    )
    far, near = empty("r", x=3.0), empty("l", x=1.0)
    planned = Naming.append_location_based_suffix([far, near], dry_run=True)
    check(
        "location suffix dry_run",
        sorted(planned) == ["l_01", "r_02"] and far.name == "r" and near.name == "l",
        str(planned),
    )

    # ---- location suffix: an already-correct name is never left on the placeholder -----------
    reset()
    near, far = empty("l_01", x=1.0), empty("r", x=5.0)
    Naming.append_location_based_suffix([far, near])
    check(
        "already-correct name kept",
        near.name == "l_01" and far.name == "r_02",
        f"{near.name},{far.name}",
    )

    # ---- scene_objects: the "Scene" scope is the current scene's objects ---------------------
    reset()
    empty("A"), empty("B")
    check("scene_objects", sorted(o.name for o in Naming.scene_objects()) == ["A", "B"])

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
    check(
        "unique name for existing → _001",
        Naming.generate_unique_name("Cube") == "Cube_001",
    )
    check(
        "unique name for free → unchanged",
        Naming.generate_unique_name("Free") == "Free",
    )

    # ---- strip_illegal_chars ------------------------------------------------------------------
    check("strip illegal chars", Naming.strip_illegal_chars("a b-c") == "a_b_c")
    check(
        "strip illegal chars (list)",
        Naming.strip_illegal_chars(["x.y", "z 1"]) == ["x_y", "z_1"],
    )

    # ---- the tool API (what tentacle's Naming panel calls) ------------------------------------
    reset()
    a, b = empty("ScopeA"), empty("ScopeB")
    btk.CoreUtils.select([a], mode="replace")
    check("scope_objects: the selection", Naming.scope_objects() == [a])
    check(
        "scope_objects: the scene",
        sorted(o.name for o in Naming.scope_objects(scene=True)) == ["ScopeA", "ScopeB"],
    )

    reset()
    loc, mesh = empty("FindLoc"), typed("FindMe", "MESH")
    found, searched = Naming.select_by_name("Find*")
    check(
        "select_by_name selects the matches",
        sorted(o.name for o in found) == ["FindLoc", "FindMe"]
        and searched == 2
        and all(o.select_get() for o in found),
        f"{[o.name for o in found]} of {searched}",
    )
    found, searched = Naming.select_by_name("Find*", locators_only=True)
    check(
        "select_by_name locators_only searches Empties",
        [o.name for o in found] == ["FindLoc"] and searched == 1 and not mesh.select_get(),
    )
    found, _searched = Naming.select_by_name("NoSuch")
    check("no match clears the selection", found == [] and not loc.select_get())
    check(
        "select_link labels and selects by name",
        Naming.select_link(loc) == Naming.log_link("FindLoc", "select", node="FindLoc"),
    )
    check(
        "UNSUPPORTED_TYPES name bound convention keys",
        bool(Naming.UNSUPPORTED_TYPES)
        and set(Naming.UNSUPPORTED_TYPES) <= {ck for _kw, ck, _tk in Naming.SUFFIX_BINDINGS},
    )

    # ==== the Naming probe's findings (2026-10-31), mirrored from mayatk ======================
    import logging
    import tempfile

    class Captured:
        def __init__(self):
            self.texts = []
            self.handler = logging.Handler()
            self.handler.emit = lambda record: self.texts.append(record.getMessage())

        def __enter__(self):
            Naming.logger.addHandler(self.handler)
            return self

        def __exit__(self, *_exc):
            Naming.logger.removeHandler(self.handler)

        def lines(self, word):
            return [t for t in self.texts if word in t]

    def linked_object(name):
        """A library-linked object: Blender refuses to rename it (is_editable False)."""
        reset()
        src = empty(name)
        lib = os.path.join(tempfile.mkdtemp(), "naming_lib.blend")
        bpy.data.libraries.write(lib, {src}, fake_user=True)
        reset()
        with bpy.data.libraries.load(lib, link=True) as (_from, to):
            to.objects = [name]
        obj = to.objects[0]
        bpy.context.collection.objects.link(obj)
        return obj

    # ---- S2-C: a swap renames both (two phases in every operation) -----------------------------
    reset()
    a, b = empty("obj1"), empty("obj2")
    Naming.rename([a, b], "obj2|obj1", fltr="obj1|obj2")
    check("swap renames both", (a.name, b.name) == ("obj2", "obj1"), f"{a.name},{b.name}")

    # ---- S1-D / E: a linked object is skipped in one line, nothing stranded -------------------
    lk = linked_object("lk")
    a, b = empty("a", x=0.0), empty("b", x=2.0)
    lk_x = lk.matrix_world.translation.x
    with Captured() as log:
        Naming.append_location_based_suffix([a, lk, b])
    names = sorted(o.name for o in bpy.data.objects)
    check(
        "location: linked object skipped, nothing parked",
        lk.name == "lk" and not [n for n in names if "naming" in n] and a.name == "a_01",
        f"{names} lk_x={lk_x}",
    )
    check("location: one skip line", len(log.lines("Skipped")) == 1, log.texts)
    with Captured() as log:
        result = Naming.rename([lk, a], "**_x")
    check(
        "rename: linked skipped, no failure",
        not log.lines("Could not rename") and len(log.lines("Skipped")) == 1
        and result == ["lk", "a_01_x"],
        f"{result} {log.texts}",
    )

    # ---- G: a numbered duplicate is re-affixed, not decorated ----------------------------------
    reset()
    dup, idx = typed("crate_GEO.001", "MESH"), typed("wall_GEO_01", "MESH")
    Naming.suffix_by_type([dup, idx])
    check(
        "suffix_by_type re-affixes a numbered duplicate",
        (dup.name, idx.name) == ("crate_001_GEO", "wall_01_GEO"),
        f"{dup.name},{idx.name}",
    )

    # ---- H: alphabetical numbering is re-runnable, a side marker kept --------------------------
    reset()
    tiles = [empty("tile", x=float(i)) for i in range(4)]
    Naming.append_location_based_suffix(tiles, alphabetical=True)
    first = [o.name for o in tiles]
    Naming.append_location_based_suffix(tiles, alphabetical=True)
    check(
        "alphabetical re-run is stable",
        first == ["tile_A", "tile_B", "tile_C", "tile_D"] and [o.name for o in tiles] == first,
        f"{first} -> {[o.name for o in tiles]}",
    )
    reset()
    c1, c2 = empty("Container_GRP", x=0.0), empty("Container_GRP", x=10.0)
    for alpha, want in ((False, ("Container_01_GRP", "Container_02_GRP")), (True, ("Container_A_GRP", "Container_B_GRP"))):
        for _run in range(2):
            Naming.append_location_based_suffix(
                [c1, c2], independent_groups=True, strip_defined_suffixes=False,
                alphabetical=alpha, valid_suffixes=["_GRP"],
            )
            check(
                f"independent groups re-run is stable (alphabetical={alpha})",
                (c1.name, c2.name) == want,
                f"{c1.name},{c2.name}",
            )
    reset()
    left, right = empty("arm_L", x=0.0), empty("arm_R", x=1.0)
    Naming.append_location_based_suffix([left, right], alphabetical=True)
    check("alphabetical keeps a side marker", (left.name, right.name) == ("arm_L_A", "arm_R_B"))

    # ---- K / L: every planned name is legalized ------------------------------------------------
    reset()
    o = empty("a1box")
    Naming.strip_chars([o], num_chars=1)
    check("strip leading keeps the digit", o.name == "_1box", o.name)
    reset()
    o = empty("c4")
    Naming.rename([o], "1box")
    check("leading digit gets '_'", o.name == "_1box", o.name)
    o = empty("c7")
    with Captured() as log:
        Naming.rename([o], "---")
    check("nothing legal left: skipped", o.name == "c7" and log.lines("no letters or digits"), f"{o.name} {log.texts}")
    o = empty("c6")
    with Captured() as log:
        Naming.rename([o], "crate_#")
    check("'#' is not a counter", bool(log.lines("not a counter")), log.texts)

    # ---- M: the dry run predicts Blender's numbering -------------------------------------------
    reset()
    empty("box")
    cs = [empty(f"c{i}") for i in range(3)]
    dry = Naming.rename(cs, "box", dry_run=True)
    with Captured() as log:
        live = Naming.rename(cs, "box")
    check(
        "dry run predicts the live numbering",
        dry == live == ["box.001", "box.002", "box.003"] and not log.lines("instead of"),
        f"dry={dry} live={live} {log.texts}",
    )

    # ---- Suffix By Location's order ------------------------------------------------------------
    def row():
        reset()
        spots = {
            "east": (3, 0, 0), "west": (-3, 0, 0), "north": (0, -3, 0),
            "up": (0, 0, 3), "center": (0, 0, 0), "far_west": (-6, 0, 0),
            "near_east": (1, 0, 0),
        }
        objs = {}
        for name, pos in spots.items():
            objs[name] = empty(name)
            objs[name].location = pos
        return objs

    objs = row()
    Naming.append_location_based_suffix(list(objs.values()), order="x")
    got = {k: o.name for k, o in objs.items()}
    check(
        "order x: signed position, ties by the other axes",
        [got[k] for k in ("far_west", "west", "north", "center", "up", "near_east", "east")]
        == ["far_west_01", "west_02", "north_03", "center_04", "up_05", "near_east_06", "east_07"],
        got,
    )
    for pair in (("east", "west"), ("west", "east")):
        objs = row()
        Naming.append_location_based_suffix([objs[k] for k in pair])
        check(
            f"distance tie breaks by position ({pair[0]} first)",
            (objs["west"].name, objs["east"].name) == ("west_01", "east_02"),
            f"{objs['west'].name},{objs['east'].name}",
        )
    reset()
    posts = [empty("post", x=float(x)) for x in (4, -2, 9, 0)]
    for o, x in zip(posts, (4, -2, 9, 0)):
        o.location.y = 0.001 * x
    Naming.append_location_based_suffix(posts, order="auto")
    check(
        "order auto numbers along the row",
        [o.name for o in posts] == ["post_03", "post_01", "post_04", "post_02"],
        [o.name for o in posts],
    )
    Naming.append_location_based_suffix(posts, order="auto", reverse=True)
    check(
        "order auto reversed",
        [o.name for o in posts] == ["post_02", "post_04", "post_01", "post_03"],
        [o.name for o in posts],
    )

except Exception:
    traceback.print_exc()
    lines.append("FAIL unhandled exception")

print("\n".join(lines))
ok = all(ln.startswith("OK") for ln in lines) and lines
print(
    f"===RESULT: {'PASS' if ok else 'FAIL'}=== ({sum(1 for ln in lines if ln.startswith('OK'))}/{len(lines)})"
)
