"""blendertk.uv_utils.texture_combine headless test -- TextureCombine (in-place combine).
Run: blender --background --factory-startup --python blendertk/test/test_texture_combine.py

Mirror of mayatk's ``test_texture_combine``: the plan and pack arithmetic is pinned in
pythontk's ``test_uv_combine``; this covers what the Blender adapter adds -- gathering
islands per material (corners welded into UV points), the in-place write-back (no
duplicated meshes, no lasting UV map), the transfer into one material, linked duplicates,
the record, restore / re-combine / update, and the lightmap consolidation. Plus what only
Blender needs: the emptied material slots go (an FBX ships every slot) while the source
materials keep a fake user until the combine is restored or forgotten.
"""

import os
import sys
import traceback

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
    import numpy as np
    import blendertk as btk

    btk.CoreUtils.ensure_image_deps()  # Blender ships no Pillow
    from PIL import Image
    import pythontk as ptk
    from blendertk.light_utils.lightmap_baker.lightmap_records import LightmapRecords
    from blendertk.uv_utils.texture_combine.texture_combine import TextureCombine
    from blendertk.uv_utils.texture_transfer import TextureTransfer

    artifacts = ptk.TempArtifacts("texture_combine_btk_test", policy="scoped")
    tmp = artifacts.dir_path()
    out_dir = os.path.join(tmp, "out").replace("\\", "/")

    def image(name, rgb, size=32):
        path = os.path.join(tmp, name).replace("\\", "/")
        Image.fromarray(np.full((size, size, 3), rgb, np.uint8)).save(path)
        return path

    red = image("red.png", (255, 0, 0))
    blue = image("blue.png", (0, 0, 255))

    def reset():
        if bpy.context.view_layer.objects.active is not None and (
            bpy.context.view_layer.objects.active.mode != "OBJECT"
        ):
            bpy.ops.object.mode_set(mode="OBJECT")
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)
        for coll in (bpy.data.meshes, bpy.data.materials, bpy.data.images):
            for item in list(coll):
                coll.remove(item)
        TextureCombine._save({})
        LightmapRecords.revert()

    def loop_uvs(o, name="UVMap"):
        return TextureTransfer._uv_layer_vectors(o.data, name).astype(np.float64)

    def set_loop_uvs(o, values, name="UVMap"):
        TextureCombine._set_loop_uvs(o.data.uv_layers[name], values)
        o.data.update()

    def plane(name, x=0.0, uv_scale=1.0):
        bpy.ops.mesh.primitive_plane_add(size=1, location=(x, 0, 0))
        o = bpy.context.active_object
        o.name = name
        o.data.name = name
        if uv_scale != 1.0:
            set_loop_uvs(o, loop_uvs(o) * uv_scale)
        return o

    def material(name, texture):
        mat = bpy.data.materials.new(name)
        btk.MatUtils.get_node_tree(mat, create=True)
        bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
        tex = mat.node_tree.nodes.new("ShaderNodeTexImage")
        tex.image = bpy.data.images.load(texture)
        mat.node_tree.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
        return mat

    def wear(objects, mat):
        for o in objects:
            o.data.materials.clear()
            o.data.materials.append(mat)

    def scene():
        """Two materials; plane A2 is a stacked copy of A1 (same UVs)."""
        a1, a2, b = plane("A1", 0), plane("A2", 2), plane("B", 4)
        wear([a1, a2], material("matA", red))
        wear([b], material("matB", blue))
        return a1, a2, b

    def worn(o):
        return {
            o.material_slots[p.material_index].material.name
            for p in o.data.polygons
            if o.material_slots[p.material_index].material is not None
        }

    def combine(objects, **kwargs):
        kwargs.setdefault("map_size", 64)
        kwargs.setdefault("padding", 2)
        kwargs.setdefault("supersample", 1)
        kwargs.setdefault("output_dir", out_dir)
        kwargs.setdefault("name", "kit")
        kwargs.setdefault("sizing", "density")
        return TextureCombine(log_level="WARNING").combine(objects, **kwargs)

    def sample(path, uv):
        img = np.asarray(Image.open(path).convert("RGB"), dtype=np.float32)
        h, w = img.shape[:2]
        x = min(w - 1, int(uv[0] * w))
        y = min(h - 1, int((1.0 - uv[1]) * h))
        return img[y, x]

    def span(o):
        return float(np.ptp(loop_uvs(o), axis=0).max())

    def raises(fn):
        try:
            fn()
        except ValueError:
            return True
        return False

    # ---- the UV points and their islands ----------------------------------
    reset()
    me = bpy.data.meshes.new("grid")  # two quads sharing an edge
    me.from_pydata(
        [(0, 0, 0), (1, 0, 0), (2, 0, 0), (0, 1, 0), (1, 1, 0), (2, 1, 0)],
        [],
        [(0, 1, 4, 3), (1, 2, 5, 4)],
    )
    me.uv_layers.new(name="UVMap")
    grid = bpy.data.objects.new("grid", me)
    bpy.context.collection.objects.link(grid)
    corner_verts = np.array([lp.vertex_index for lp in me.loops])
    co = np.array([me.vertices[i].co[:2] for i in corner_verts])
    set_loop_uvs(grid, co / (2.0, 1.0))
    read = TextureCombine._read_shape(grid)
    check(
        "corners at one vertex with one UV weld into one point",
        len(read["uvs"]) == 6 and len(np.unique(read["shell_ids"])) == 1,
        f"points={len(read['uvs'])} shells={np.unique(read['shell_ids'])}",
    )
    uv = loop_uvs(grid)
    first_face = np.arange(4)  # the first quad's corners
    uv[first_face] += (2.0, 0.0)  # cut along the shared edge: two islands
    set_loop_uvs(grid, uv)
    read = TextureCombine._read_shape(grid)
    check(
        "a UV seam splits the mesh into two islands",
        len(read["uvs"]) == 8 and len(np.unique(read["shell_ids"])) == 2,
        f"points={len(read['uvs'])} shells={np.unique(read['shell_ids'])}",
    )
    # Island by island, the way a pack moves them: the cut-off island crosses
    # to the other side of its neighbour, so ordering points by coordinate
    # would number the seam's points the other way round.
    moved = read["uvs"].copy()
    cut = read["shell_ids"] == read["shell_ids"][read["uv_ids"][0]]
    moved[cut] += (-3.0, 0.5)
    set_loop_uvs(grid, moved[read["uv_ids"]])
    again = TextureCombine._read_shape(grid)
    check(
        "a moved layout welds into the same points (the record's key)",
        TextureCombine._fingerprint(again) == TextureCombine._fingerprint(read),
        f"{TextureCombine._fingerprint(again)} vs {TextureCombine._fingerprint(read)}",
    )

    # ---- combine in place: one material, stacks kept ----------------------
    reset()
    a1, a2, b = scene()
    maps_before = [uv.name for uv in a1.data.uv_layers]
    result = combine([a1, a2, b])
    check(
        "every combined mesh wears one material",
        len({frozenset(worn(o)) for o in (a1, a2, b)}) == 1 and len(worn(a1)) == 1,
        [worn(o) for o in (a1, a2, b)],
    )
    check(
        "the emptied source slots are gone (one slot each)",
        all(len(o.material_slots) == 1 for o in (a1, a2, b)),
        [len(o.material_slots) for o in (a1, a2, b)],
    )
    check(
        "no UV map outlives the call",
        [uv.name for uv in a1.data.uv_layers] == maps_before,
        [uv.name for uv in a1.data.uv_layers],
    )
    check(
        "nothing duplicated",
        len([o for o in bpy.data.objects if o.type == "MESH"]) == 3,
        len(bpy.data.objects),
    )
    ua1, ua2, ub = loop_uvs(a1), loop_uvs(a2), loop_uvs(b)
    check(
        "the stack stays stacked",
        np.allclose(ua1, ua2, atol=1e-6),
        f"{ua1.tolist()} vs {ua2.tolist()}",
    )
    check(
        "the packed layout is inside 0-1",
        all((u >= -1e-6).all() and (u <= 1 + 1e-6).all() for u in (ua1, ub)),
    )
    apart = (ua1.max(0) <= ub.min(0) + 1e-6).any() or (
        ub.max(0) <= ua1.min(0) + 1e-6
    ).any()
    check("the two materials' islands do not overlap", bool(apart))
    color = result["maps"].get("baseColor", "")
    check(
        "the atlas reads each source through its island",
        os.path.isfile(color)
        and sample(color, ua1.mean(0))[0] > 200
        and sample(color, ub.mean(0))[2] > 200,
        color,
    )
    check(
        "the result names the combine's material",
        result["material"] == "kit" and worn(a1) == {"kit"},
        result["material"],
    )
    check(
        "the source materials keep a fake user while recorded",
        all(bpy.data.materials[m].use_fake_user for m in ("matA", "matB"))
        and sorted(TextureCombine.entry("kit")["kept"]) == ["matA", "matB"],
        TextureCombine.entry("kit").get("kept"),
    )
    check("names lists the combine", TextureCombine.names() == ["kit"])
    check("owner finds it from a member", TextureCombine.owner([b]) == "kit")

    # ---- restore is exact --------------------------------------------------
    reset()
    a1, a2, b = scene()
    before = {o.name: loop_uvs(o).astype(np.float32) for o in (a1, a2, b)}
    combine([a1, a2, b])
    restored = TextureCombine(log_level="WARNING").restore("kit")
    check("restore returns every member", set(restored) == {"A1", "A2", "B"}, restored)
    check(
        "restore puts the original UVs back exactly",
        all(np.allclose(loop_uvs(o), before[o.name], atol=1e-7) for o in (a1, a2, b)),
    )
    check(
        "restore puts the original materials back, slots compact",
        worn(a1) == {"matA"}
        and worn(b) == {"matB"}
        and all(len(o.material_slots) == 1 for o in (a1, a2, b)),
        [(worn(o), len(o.material_slots)) for o in (a1, a2, b)],
    )
    check(
        "restore releases the fake users and forgets the combine",
        not bpy.data.materials["matA"].use_fake_user and TextureCombine.names() == [],
    )

    # ---- inputs_for rebuilds the pre-combine state ------------------------
    reset()
    a1, a2, b = scene()
    gathered = TextureCombine().gather([a1, a2, b])
    combine([a1, a2, b])
    after = TextureCombine().inputs_for("kit")
    check(
        "inputs_for reads the ORIGINAL materials",
        set(after.sources) == {"matA", "matB"},
        set(after.sources),
    )
    check(
        "inputs_for reads the original islands and UVs",
        len(after.shells) == len(gathered.shells)
        and all(
            np.allclose(after.shapes[k]["uvs"], r["uvs"].astype(np.float32), atol=1e-7)
            for k, r in gathered.shapes.items()
        ),
    )

    # ---- re-combine: a revised source into the same layout ----------------
    reset()
    a1, a2, b = scene()
    result = combine([a1, a2, b])
    layout = loop_uvs(a1)
    check("a fresh combine is not stale", TextureCombine.stale("kit") == [])
    image("red.png", (0, 255, 0))  # the source revised: now green
    os.utime(red, None)
    check(
        "a revised source map is stale",
        red in TextureCombine.stale("kit"),
        TextureCombine.stale("kit"),
    )
    try:
        again = TextureCombine(log_level="WARNING").recombine("kit")
    except Exception as error:  # noqa: BLE001 -- reported as the check's detail
        again = {"maps": {}, "error": repr(error)}
    check(
        "recombine keeps the layout and the map's path",
        np.allclose(loop_uvs(a1), layout, atol=1e-7)
        and again["maps"].get("baseColor") == result["maps"]["baseColor"],
        again.get("error", again["maps"]),
    )
    check(
        "recombine reads the revised source through the recorded material",
        bool(again["maps"])
        and sample(again["maps"]["baseColor"], layout.mean(0))[1] > 200,
        again.get("error", ""),
    )
    check(
        "recombine refreshes the stamps",
        bool(again["maps"]) and TextureCombine.stale("kit") == [],
        TextureCombine.stale("kit"),
    )
    image("red.png", (255, 0, 0))

    # ---- override rows -----------------------------------------------------
    reset()
    a1, _, b = scene()
    combine([a1, b], rows=[{"kind": "material", "target": "matB", "scale": 0.5}])
    check(
        "a material row scales that material",
        abs(span(b) / span(a1) - 0.5) < 0.02,
        f"{span(b) / span(a1):.3f}",
    )

    reset()
    a1, _, b = scene()
    rows = [{"kind": "faces", "target": TextureCombine._face_ranges("B", [0])}]
    rows[0]["scale"] = 0.5
    combine([a1, b], rows=rows)
    check(
        "a faces row (<object>.f[a:b]) scales the islands under it",
        rows[0]["target"] == ["B.f[0:0]"] and abs(span(b) / span(a1) - 0.5) < 0.02,
        f"{rows[0]['target']} {span(b) / span(a1):.3f}",
    )

    reset()
    a1 = plane("A1", 0)
    a2 = plane("A2", 2, uv_scale=0.5)  # half the texels, same surface
    b = plane("B", 4)
    wear([a1, a2], material("matA", red))
    wear([b], material("matB", blue))
    combine([a1, a2, b], sizing="proportional")
    check(
        "proportional keeps a material's own ratios",
        abs(span(a2) / span(a1) - 0.5) < 0.02,
        f"{span(a2) / span(a1):.3f}",
    )

    # ---- linked duplicates -------------------------------------------------
    reset()
    a1, _, b = scene()
    inst = a1.copy()  # shares a1's mesh
    inst.name = "A1_inst"
    bpy.context.collection.objects.link(inst)
    combine([a1, b])  # the duplicate is not picked
    check(
        "every linked duplicate receives the material",
        worn(inst) == worn(a1) == worn(b) == {"kit"},
        [worn(o) for o in (a1, inst, b)],
    )
    check(
        "members lists the linked duplicate",
        "A1_inst" in TextureCombine.members("kit"),
        TextureCombine.members("kit"),
    )

    # ---- collisions refuse -------------------------------------------------
    reset()
    a1, a2, b = scene()
    combine([a1, b])
    check("a taken name refuses", raises(lambda: combine([a2], name="kit")))
    check(
        "a mesh in another combine refuses",
        raises(lambda: combine([a1, a2], name="other")),
    )

    # ---- update re-packs with new settings --------------------------------
    reset()
    a1, _, b = scene()
    combine([a1, b])
    combined = loop_uvs(b)
    bpy.ops.ed.undo_push(message="combined")
    TextureCombine(log_level="WARNING").update(
        "kit", rows=[{"kind": "material", "target": "matB", "scale": 0.5}]
    )
    check(
        "update keeps the name and applies the new rows",
        TextureCombine.names() == ["kit"] and abs(span(b) / span(a1) - 0.5) < 0.02,
        f"{TextureCombine.names()} {span(b) / span(a1):.3f}",
    )
    # Restore + re-combine are ONE step: one undo returns the combine as it
    # was, not the restored originals (references go stale across an undo).
    bpy.ops.ed.undo()
    b = bpy.data.objects["B"]
    check(
        "one undo takes an update back to the combine it replaced",
        TextureCombine.names() == ["kit"]
        and np.allclose(loop_uvs(b), combined, atol=1e-6),
        TextureCombine.names(),
    )

    # ---- forget ------------------------------------------------------------
    check(
        "forget drops the record and the fake users",
        TextureCombine.forget("kit")
        and TextureCombine.names() == []
        and not any(m.use_fake_user for m in bpy.data.materials),
        [m.name for m in bpy.data.materials if m.use_fake_user],
    )

    # ---- lightmaps ---------------------------------------------------------
    def lightmap_maps(*objects):
        for o in objects:
            layer = o.data.uv_layers.new(name="Lightmap", do_init=False)
            TextureCombine._set_loop_uvs(layer, loop_uvs(o))
            o.data.uv_layers.active = o.data.uv_layers["UVMap"]

    def atlas(name, value=0.5, size=16):
        path = os.path.join(tmp, name).replace("\\", "/")
        LightmapRecords._write_lightmap(
            path, np.full((size, size, 3), value, np.float32)
        )
        return path

    reset()
    a1, _, b = scene()
    lightmap_maps(a1, b)
    shared = atlas("matA_Lightmap.exr")
    LightmapRecords.commit({"A1": shared, "B": shared}, written=False)
    result = combine([a1, b])
    check(
        "an atlas the objects own is renamed after the combine",
        result["lightmaps"].get("how") == "renamed"
        and LightmapRecords.lightmap_info("A1").get("map") == "kit_Lightmap.exr"
        and LightmapRecords.lightmap_info("B").get("map") == "kit_Lightmap.exr",
        result["lightmaps"],
    )
    # A copy: in the session until the file is saved, its home beside the
    # old one (LightmapRecords._stage).
    _kit = {p for n, _m, p in LightmapRecords._reads() if n in ("A1", "B")}
    check(
        "the renamed atlas is a copy beside the old one, which stays",
        len(_kit) == 1
        and all(os.path.isfile(p) for p in _kit)
        and {
            os.path.normcase(LightmapRecords._home(p)) for p in _kit
        } == {os.path.normcase(os.path.join(tmp, "kit_Lightmap.exr"))}
        and os.path.isfile(shared),
        _kit,
    )

    reset()
    a1, a2, b = scene()
    lightmap_maps(a1, a2, b)
    room = atlas("room_Lightmap.exr", value=0.25)
    LightmapRecords.commit(
        {"A1": room, "A2": room},
        scale_offsets={"A1": [0.5, 0.5, 0.0, 0.0], "A2": [0.5, 0.5, 0.5, 0.5]},
        written=False,
    )
    result = combine([a1, b])  # A2 keeps reading the room atlas
    moved = LightmapRecords.lightmap_info("A1")
    check(
        "a shared atlas is not renamed: the cells are re-packed",
        result["lightmaps"].get("how") == "repacked" and os.path.isfile(room),
        result["lightmaps"],
    )
    check(
        "the moved object reads the combine's own atlas",
        str(moved.get("map", "")).startswith("kit_Lightmap")
        and bool(moved.get("path"))
        and os.path.isfile(moved["path"]),
        moved,
    )
    # A1's cell is the room atlas's lower-left quarter (8 px of 16) plus a
    # 2 px gutter: the crop covers 0..1.25 of A1's own layout, so its solo
    # atlas binds A1 through that region -- uv * 0.8.
    so = [float(v) for v in moved.get("scaleOffset") or ()]
    check(
        "its rect is composed back onto its own layout (the crop's region)",
        np.allclose(so, [0.8, 0.8, 0.0, 0.0], atol=1e-6),
        so,
    )
    from blendertk.light_utils.lightmap_baker.lightmap_baker import LightmapBaker

    texels = LightmapBaker._read_exr_top_down(moved["path"])[..., :3]
    check(
        "the re-packed atlas carries the object's own lighting",
        np.allclose(texels, 0.25, atol=2e-3),
        f"{float(texels.min()):.4f}..{float(texels.max()):.4f}",
    )
    check(
        "the object with no lightmap is listed",
        result["lightmaps"].get("unbaked") == ["B"],
        result["lightmaps"].get("unbaked"),
    )
    check(
        "the object left out keeps the old atlas",
        LightmapRecords.lightmap_info("A2").get("map") == "room_Lightmap.exr",
        LightmapRecords.lightmap_info("A2"),
    )

    reset()
    a1, a2, b = scene()
    lightmap_maps(a1, a2, b)
    own = atlas("matA_Lightmap.exr")
    held = atlas("kit_Lightmap.exr", value=0.25)
    LightmapRecords.commit({"A1": own, "B": own, "A2": held}, written=False)
    result = combine([a1, b])  # A2 reads kit_Lightmap.exr
    check(
        "a renamed atlas steps aside from a name another object reads",
        result["lightmaps"].get("map") == "kit_Lightmap_1.exr"
        and LightmapRecords.lightmap_info("A1").get("map") == "kit_Lightmap_1.exr"
        and LightmapRecords.lightmap_info("A2").get("map") == "kit_Lightmap.exr",
        result["lightmaps"],
    )

    # Review 2026-10-24: a file another .blend wrote under the combine's name
    # -- read by nothing here, so no claim -- was taken over at the save.
    reset()
    a1, a2, b = scene()
    lightmap_maps(a1, b)
    own = atlas("matA_Lightmap.exr")
    atlas("kit_Lightmap.exr", value=0.25)  # theirs
    # Another file's: no writer record here (the helper's write stamps one).
    LightmapRecords._save_writers(
        {k: v for k, v in LightmapRecords._writers().items() if k != "kit_lightmap.exr"}
    )
    LightmapRecords.commit({"A1": own, "B": own}, written=False)
    result = combine([a1, b])
    check(
        "a renamed atlas steps aside from another file's map of its name",
        result["lightmaps"].get("how") == "renamed"
        and result["lightmaps"].get("map") == "kit_Lightmap_1.exr"
        and LightmapRecords.lightmap_info("A1").get("map") == "kit_Lightmap_1.exr",
        result["lightmaps"],
    )

    # ---- what the shared Texture Combine panel asks beside the combine -------
    # (tentacle's ``slots/tools/texture_combine.py``; its gates, rows and report
    # are tentacle's ``test_texture_combine_panel``). Mirror of mayatk's
    # TestPanelSelection / TestTransferSelectionSides.
    from blendertk.uv_utils._uv_utils import UvUtils
    from blendertk.uv_utils.texture_combine.texture_combine import CombineInputs

    def pick(objs, active=None):
        with btk.CoreUtils.window_context_override():
            for o in list(bpy.context.view_layer.objects):
                o.select_set(o in objs)
            bpy.context.view_layer.objects.active = active

    def cube(name, x=0.0):
        bpy.ops.mesh.primitive_cube_add(location=(x, 0, 0))
        o = bpy.context.active_object
        o.name = name
        return o

    reset()
    a, b = plane("selA"), plane("selB", 2)
    pick([])
    check(
        "select takes objects (by name) and counts what exists",
        TextureCombine.select(["selA", "goneForever"]) == 1
        and [o.name for o in btk.CoreUtils.selected_objects()] == ["selA"],
    )
    check(
        "select with nothing that exists leaves the selection alone",
        TextureCombine.select(["goneForever"]) == 0
        and [o.name for o in btk.CoreUtils.selected_objects()] == ["selA"],
    )
    count = TextureCombine.select(["selB.f[0:0]"])
    check(
        "select puts a face component's object in Edit Mode with those faces",
        count == 1
        and b.mode == "EDIT"
        and TextureCombine.selected_faces() == ["selB.f[0:0]"],
        f"{count} {b.mode} {TextureCombine.selected_faces()}",
    )
    bpy.ops.object.mode_set(mode="OBJECT")
    check(
        "selected_faces holds none outside Edit Mode",
        TextureCombine.selected_faces() == [],
    )
    inputs = CombineInputs(transforms=["selA"])
    inputs.shapes["selA"] = {
        "materials": ["matX", "matY"],
        "face_material": np.array([0, 1, 1]),
        "transforms": ["selA"],
    }
    check(
        "material_faces names a material's faces as components",
        TextureCombine.material_faces(inputs, "matY") == ["selA.f[1:2]"]
        and TextureCombine.material_faces(inputs, "other") == [],
    )
    check(
        "leaf_name is the object's name, a component's object",
        TextureCombine.leaf_name("Cube.001.f[0:3]") == "Cube.001"
        and TextureCombine.leaf_name(a) == "selA",
    )
    engine = TextureCombine(log_level="WARNING")
    check(
        "default_name falls back to the caller's word",
        engine.default_name([]) == "combined"
        and engine.default_name([], fallback="transfer") == "transfer",
    )

    reset()
    a1, _a2, b = scene()
    check(
        "combine refuses a shader type (none to rebuild on), before any change",
        raises(lambda: combine([a1, b], assign_shader_type="standard_surface"))
        and TextureCombine.names() == []
        and TextureTransfer.shader_types() == {},
    )
    check(
        "TERMS speak of the active object and UV maps",
        TextureTransfer.TERMS["uv"] == "UV Map"
        and TextureTransfer.TERMS["first"] == "Active Mesh",
    )

    reset()
    s, t = cube("uvSrc"), cube("uvTgt", 3)
    check(
        "transfer_uvs reports each pair and how (mayatk's shape)",
        UvUtils.transfer_uvs([s], [t], match_by_similarity=False)
        == [("uvSrc", "uvTgt", "topology")],
    )

    def joined():
        """srcA (cube), srcB (sphere) and 'comb' = B joined with A."""
        reset()
        a = cube("srcA")
        bpy.ops.mesh.primitive_uv_sphere_add(segments=6, ring_count=4, location=(3, 0, 0))
        b = bpy.context.active_object
        b.name = "srcB"
        copies = []
        for o in (b, a):
            c = o.copy()
            c.data = o.data.copy()
            bpy.context.collection.objects.link(c)
            copies.append(c)
        with bpy.context.temp_override(
            active_object=copies[0], selected_editable_objects=copies
        ):
            bpy.ops.object.join()
        copies[0].name = "comb"
        return a, b, copies[0]

    def sides(mode="auto", stored=()):
        mode, source, targets = TextureTransfer.selection_sides(mode, stored)
        names = lambda objs: None if objs is None else [o.name for o in objs]  # noqa: E731
        return mode, names(source), names(targets)

    a, b, c = joined()
    pick([a, b, c], c)
    check(
        "All But Active: the active mesh is the target of every other",
        sides("last") == ("last", ["srcA", "srcB"], ["comb"])
        or sides("last") == ("last", ["srcB", "srcA"], ["comb"]),
        sides("last"),
    )
    pick([a, b, c], a)
    got = sides()
    check(
        "Auto finds the joined target whatever is active",
        got[0] == "last" and sorted(got[1]) == ["srcA", "srcB"] and got[2] == ["comb"],
        got,
    )
    pick([a], a)
    check("Auto reads one mesh as its own UV maps", sides() == ("uvset", None, ["srcA"]))
    pick([b, a], b)
    check(
        "Auto falls back to the active mesh as the source",
        sides() == ("first", ["srcB"], ["srcA"]),
        sides(),
    )
    pick([c], c)
    got = sides("auto", ["srcA", "srcB"])
    check(
        "Auto reads stored sources that match the selection",
        got[0] == "stored" and got[1] == ["srcA", "srcB"] and got[2] == ["comb"],
        got,
    )
    pick([a], a)
    check(
        "Auto ignores a stale capture and a deleted one",
        sides("auto", ["comb"]) == ("uvset", None, ["srcA"])
        and sides("auto", ["goneForever"]) == ("uvset", None, ["srcA"]),
    )
    d = cube("deselD", 9)
    pick([a, b], d)  # D active, not selected
    check(
        "a deselected active object is no side of the transfer",
        sides("first")[1] == [] and sides("last")[2] == [],
        f"{sides('first')} {sides('last')}",
    )
    pick([a, b], a)
    names = TextureTransfer.capture_sources()
    check(
        "capture stores names; live_sources skips the deleted",
        sorted(names) == ["srcA", "srcB"]
        and [o.name for o in TextureTransfer.live_sources(["srcA", "goneForever"])]
        == ["srcA"],
        names,
    )

    reset()
    artifacts.cleanup()

except Exception as e:
    lines.append(f"FAIL setup: {e!r}")
    lines.append(traceback.format_exc())

ok = all(line.startswith("OK") for line in lines)
print("\n===TEXTURE-COMBINE===")
print("\n".join(lines))
print(
    f"===RESULT: {'PASS' if ok else 'FAIL'}=== "
    f"({sum(1 for line in lines if line.startswith('OK'))}/{len(lines)})"
)
