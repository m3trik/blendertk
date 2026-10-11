"""blendertk.uv_utils.texture_transfer headless test -- UV-to-UV texel remap (TextureTransfer).
Run: blender --background --factory-startup --python blendertk/test/test_uv_texture_transfer.py

Mirror of mayatk's ``test_uv_texture_transfer``: the engine arithmetic is pinned in
pythontk's ``test_uv_transfer``; this covers what the Blender adapter adds -- the
triangle correspondence between two UV maps / two objects, material discovery (maps vs
Principled constants, per face), output naming, normal re-encode, assign-on-finish.
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
    from blendertk.uv_utils.texture_transfer import TextureTransfer

    tmp = ptk.TempArtifacts("uv_transfer_btk_test", policy="detached").dir_path()
    out_dir = os.path.join(tmp, "out").replace("\\", "/")

    def checker(size=64, cell=8):
        img = np.zeros((size, size, 3), np.uint8)
        cells = (
            np.arange(size)[:, None] // cell + np.arange(size)[None, :] // cell
        ) % 2
        img[cells == 0] = 220
        img[cells == 1] = 40
        img[:cell, :cell] = (255, 0, 0)  # top-left (u=0, v=1) red
        return img

    checker_path = os.path.join(tmp, "src_checker.png").replace("\\", "/")
    Image.fromarray(checker()).save(checker_path)

    def reset():
        bpy.ops.object.select_all(action="DESELECT")
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)
        for m in list(bpy.data.materials):
            bpy.data.materials.remove(m)
        for i in list(bpy.data.images):
            bpy.data.images.remove(i)

    def plane(name):
        bpy.ops.mesh.primitive_plane_add()
        o = bpy.context.active_object
        o.name = name
        return o

    def material(name, texture=None, color=None):
        mat = bpy.data.materials.new(name)
        mat.use_nodes = True
        bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
        if texture:
            tex = mat.node_tree.nodes.new("ShaderNodeTexImage")
            tex.image = bpy.data.images.load(texture)
            mat.node_tree.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
        if color:
            bsdf.inputs["Base Color"].default_value = (*color, 1.0)
        return mat

    def rotate_uv_copy(o, new_name="map2", angle=90):
        mesh = o.data
        src = mesh.uv_layers.active
        new = mesh.uv_layers.new(name=new_name, do_init=True)
        n = len(mesh.loops)
        buf = np.empty(n * 2, np.float32)
        try:
            src.uv.foreach_get("vector", buf)
        except (AttributeError, TypeError):
            src.data.foreach_get("uv", buf)
        uv = buf.reshape(-1, 2) - 0.5
        rad = np.deg2rad(angle)
        rot = (
            np.stack(
                [
                    uv[:, 0] * np.cos(rad) - uv[:, 1] * np.sin(rad),
                    uv[:, 0] * np.sin(rad) + uv[:, 1] * np.cos(rad),
                ],
                axis=1,
            )
            + 0.5
        )
        flat = rot.astype(np.float32).ravel()
        try:
            new.uv.foreach_set("vector", flat)
        except (AttributeError, TypeError):
            new.data.foreach_set("uv", flat)
        mesh.uv_layers.active = src

    def load(path):
        return np.asarray(Image.open(path).convert("RGB"), dtype=np.float32)

    # --- UV map -> UV map, 90 deg rotation -------------------------------
    reset()
    o = plane("xferPlane")
    mat = material("xferMat", texture=checker_path)
    o.data.materials.append(mat)
    rotate_uv_copy(o, "map2", 90)
    res = TextureTransfer().transfer(
        o,
        source_uv_set="UVMap",
        target_uv_set="map2",
        size=64,
        supersample=1,
        padding=0,
        output_dir=out_dir,
    )
    path = res.get("xferMat", {}).get("baseColor", "")
    check(
        "uvmap->uvmap writes <material>_BaseColor.png",
        path.endswith("xferMat_BaseColor.png"),
        path,
    )
    got = load(path)
    err = float(np.abs(got - np.rot90(checker().astype(np.float32), 1)).max())
    check("rotated map matches np.rot90 of source", err < 2.0, f"max err {err:.2f}")

    # --- Auto: read the bound (render) map, write the other --------------
    reset()
    o = plane("autoPlane")
    mat = material("autoMat", texture=checker_path)
    o.data.materials.append(mat)
    rotate_uv_copy(o, "map2", 90)
    o.data.uv_layers["UVMap"].active_render = True
    o.data.uv_layers.active = o.data.uv_layers["map2"]  # editing the new layout
    res = TextureTransfer().transfer(
        o, size=64, supersample=1, padding=0, output_dir=out_dir
    )
    got = load(res["autoMat"]["baseColor"])
    err = float(np.abs(got - np.rot90(checker().astype(np.float32), 1)).max())
    check(
        "Auto reads the render-bound map and writes the other",
        err < 2.0,
        f"max err {err:.2f}",
    )

    # --- mesh -> mesh (mirror in U), pair by name -------------------------
    reset()
    src = plane("partA")
    smat = material("srcMat", texture=checker_path)
    src.data.materials.append(smat)
    tgt = src.copy()
    tgt.data = src.data.copy()
    bpy.context.collection.objects.link(tgt)
    tgt.name = "partA.tgt"
    tgt.data.materials.clear()
    tgt.data.materials.append(material("tgtMat"))
    layer = tgt.data.uv_layers.active
    n = len(tgt.data.loops)
    buf = np.empty(n * 2, np.float32)
    try:
        layer.uv.foreach_get("vector", buf)
    except (AttributeError, TypeError):
        layer.data.foreach_get("uv", buf)
    uv = buf.reshape(-1, 2)
    uv[:, 0] = 1.0 - uv[:, 0]
    try:
        layer.uv.foreach_set("vector", uv.ravel())
    except (AttributeError, TypeError):
        layer.data.foreach_set("uv", uv.ravel())
    res = TextureTransfer().transfer(
        tgt, src, size=64, supersample=1, padding=0, output_dir=out_dir
    )
    got = load(res["tgtMat"]["baseColor"])
    err = float(np.abs(got - checker().astype(np.float32)[:, ::-1]).max())
    check(
        "mesh->mesh mirrored U matches flipped source", err < 2.0, f"max err {err:.2f}"
    )

    # --- topology mismatch raises ----------------------------------------
    reset()
    a = plane("topoA")
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=3, y_subdivisions=3)
    b = bpy.context.active_object
    b.name = "topoB"
    m = material("topoMat", texture=checker_path)
    a.data.materials.append(m)
    b.data.materials.append(m)
    try:
        TextureTransfer().transfer(b, a, size=16, output_dir=out_dir)
        check("topology mismatch raises ValueError", False)
    except ValueError:
        check("topology mismatch raises ValueError", True)

    # --- consolidation: textured + constant -> one atlas -------------------
    reset()
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=2, y_subdivisions=1)
    consol = bpy.context.active_object
    consol.name = "consol"
    m0 = material("texMat", texture=checker_path)
    m1 = material("flatMat", color=(0.0, 0.0, 1.0))
    consol.data.materials.append(m0)
    consol.data.materials.append(m1)
    # faces with centre u < 0.5 -> m0, else m1 (grid is 2 faces wide)
    for poly in consol.data.polygons:
        poly.material_index = 0 if poly.center.x < 0 else 1
    tgt = consol.copy()
    tgt.data = consol.data.copy()
    bpy.context.collection.objects.link(tgt)
    tgt.name = "consol.tgt"
    tgt.data.materials.clear()
    tgt.data.materials.append(material("atlasMat"))
    for poly in tgt.data.polygons:
        poly.material_index = 0
    res = TextureTransfer().transfer(
        tgt, consol, size=32, supersample=1, padding=0, output_dir=out_dir
    )
    got = load(res["atlasMat"]["baseColor"])
    right_blue = bool(np.allclose(got[:, 20:], (0, 0, 255), atol=2.0))
    left_checker = bool((got[:, :12, 0] > 200).any() and (got[:, :12, 0] < 60).any())
    check(
        "consolidation fills unmapped source with its constant",
        right_blue and left_checker,
    )

    # --- a target JOINED from several sources reads them all ---------------
    # Joined with B active (B's faces first, the reverse of the source list),
    # then laid out: B's face on the right half, A's on the left.
    reset()
    ja = plane("joinA")
    ja.data.materials.append(material("joinTexMat", texture=checker_path))
    jb = plane("joinB")
    jb.location.x = 3.0
    jb.data.materials.append(material("joinFlatMat", color=(0.0, 0.0, 1.0)))
    bpy.context.view_layer.update()
    copies = []
    for o in (jb, ja):
        c = o.copy()
        c.data = o.data.copy()
        bpy.context.collection.objects.link(c)
        copies.append(c)
    with bpy.context.temp_override(
        active_object=copies[0], selected_editable_objects=copies
    ):
        bpy.ops.object.join()
    joined = copies[0]
    joined.name = "joined"
    joined.data.materials.clear()
    joined.data.materials.append(material("joinAtlasMat"))
    uv = joined.data.uv_layers.active
    for poly in joined.data.polygons:  # polygon 0 = B -> right, 1 = A -> left
        poly.material_index = 0
        for li in poly.loop_indices:
            u = uv.uv[li].vector[0] if hasattr(uv, "uv") else uv.data[li].uv[0]
            new_u = 0.5 + u * 0.5 if poly.index == 0 else u * 0.5
            if hasattr(uv, "uv"):
                uv.uv[li].vector[0] = new_u
            else:
                uv.data[li].uv[0] = new_u
    order = TextureTransfer.pair_sources([joined], [ja, jb]).get(joined)
    check(
        "pair_sources reads the join order back",
        isinstance(order, tuple) and [o.name for o in order] == ["joinB", "joinA"],
        repr(order),
    )
    fa = ja.copy()
    fa.data = ja.data.copy()
    bpy.context.collection.objects.link(fa)
    fb = ja.copy()
    fb.data = ja.data.copy()
    bpy.context.collection.objects.link(fb)
    feed = TextureTransfer.pair_sources([fa, fb], [ja])
    check(
        "one source feeds every target",
        feed == {fa: ja, fb: ja},
        repr(feed),
    )
    found = TextureTransfer.find_combined([ja, joined, jb])
    check(
        "find_combined picks the joined mesh out of the selection",
        found is not None
        and found[0] == joined
        and [o.name for o in found[1]] == ["joinB", "joinA"],
        repr(found),
    )
    res = TextureTransfer().transfer(
        joined, [ja, jb], size=32, supersample=1, padding=0, output_dir=out_dir
    )
    got = load(res["joinAtlasMat"]["baseColor"])
    check(
        "a joined target reads every source in join order",
        bool(np.allclose(got[:, 20:], (0, 0, 255), atol=2.0))
        and bool((got[:, :12, 0] > 200).any() and (got[:, :12, 0] < 60).any()),
    )
    from blendertk.light_utils.lightmap_baker.lightmap_records import (
        LightmapRecords,
    )

    try:
        LightmapRecords.transfer_lightmaps(joined, [ja, jb])
        check("lightmaps refuse a joined target by name", False)
    except ValueError as e:
        check(
            "lightmaps refuse a joined target by name", "one mesh to one mesh" in str(e)
        )

    # --- assign creates copy material, original untouched ----------------
    reset()
    o = plane("assignPlane")
    mat = material("assignMat", texture=checker_path)
    o.data.materials.append(mat)
    rotate_uv_copy(o, "map2", 90)
    TextureTransfer().transfer(
        o,
        source_uv_set="UVMap",
        target_uv_set="map2",
        size=16,
        supersample=1,
        output_dir=out_dir,
        assign=True,
    )
    new = bpy.data.materials.get("assignMat_TRANSFER")
    check("assign creates <mat>_TRANSFER", new is not None)
    if new is not None:
        from blendertk.mat_utils.mat_manifest import MatManifest

        maps = MatManifest._process_material(new)
        check(
            "copy wired to the transferred map",
            "assignMat_BaseColor" in maps.get("baseColor", ""),
            str(maps),
        )
        orig = MatManifest._process_material(mat)
        check(
            "original still wired to the source texture",
            orig.get("baseColor", "").endswith("src_checker.png"),
        )
        check(
            "plane wears the copy",
            any(s.material == new for s in o.material_slots)
            and all(
                p.material_index == [s.material for s in o.material_slots].index(new)
                for p in o.data.polygons
            ),
        )

    # --- OpenPBR lobes ride the transfer ----------------------------------
    # The copy drops every Principled link and the manifest restores the
    # transferred maps -- a lobe the manifest did not know was lost, and a
    # restored data map loaded as sRGB (Blender's PNG default).
    reset()
    o = plane("lobePlane")
    mat = material("lobeMat", texture=checker_path)
    nt = mat.node_tree
    bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
    for socket in ("Coat Roughness", "Specular IOR Level"):
        tex = nt.nodes.new("ShaderNodeTexImage")
        tex.image = bpy.data.images.load(checker_path, check_existing=True)
        nt.links.new(tex.outputs["Color"], bsdf.inputs[socket])
    o.data.materials.append(mat)
    rotate_uv_copy(o, "map2", 90)
    TextureTransfer().transfer(
        o,
        source_uv_set="UVMap",
        target_uv_set="map2",
        size=16,
        supersample=1,
        output_dir=out_dir,
        assign=True,
    )
    lobe_copy = bpy.data.materials.get("lobeMat_TRANSFER")
    check("lobe: the transfer made its copy", lobe_copy is not None)
    if lobe_copy is not None:
        from blendertk.mat_utils.mat_manifest import MatManifest

        maps = MatManifest._process_material(lobe_copy)
        check(
            "lobe: the coat roughness is re-baked onto the copy",
            ptk.MapFactory.resolve_map_type(maps.get("coatRoughness", ""))
            == "Clearcoat_Roughness",
            str(maps),
        )
        check(
            "lobe: the specular LEVEL is specularWeight, not the tint",
            "specularWeight" in maps and "specular" not in maps,
            str(maps),
        )
        copy_bsdf = next(
            n for n in lobe_copy.node_tree.nodes if n.type == "BSDF_PRINCIPLED"
        )
        link = copy_bsdf.inputs["Coat Roughness"].links
        check(
            "lobe: a restored data map is Non-Color",
            bool(link)
            and link[0].from_node.image.colorspace_settings.name == "Non-Color",
            link[0].from_node.image.colorspace_settings.name if link else "unlinked",
        )

    # --- one transfer material per shared UV map --------------------------
    reset()
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=2, y_subdivisions=1)
    shared = bpy.context.active_object
    shared.name = "sharedPlane"
    shared.data.materials.append(material("leftMat", texture=checker_path))
    shared.data.materials.append(material("rightMat", texture=checker_path))
    for poly in shared.data.polygons:
        poly.material_index = 0 if poly.center.x < 0 else 1
    rotate_uv_copy(shared, "map2", 90)
    res = TextureTransfer().transfer(
        shared,
        source_uv_set="UVMap",
        target_uv_set="map2",
        size=32,
        supersample=1,
        padding=0,
        output_dir=out_dir,
        assign=True,
    )
    check(
        "two materials on one set -> one output named after the set",
        list(res) == ["map2"],
        str(list(res)),
    )
    new = bpy.data.materials.get("map2_TRANSFER")
    slot_of_new = next(
        (i for i, sl in enumerate(shared.material_slots) if sl.material == new), None
    )
    check(
        "one map2_TRANSFER material on every face",
        new is not None
        and slot_of_new is not None
        and all(p.material_index == slot_of_new for p in shared.data.polygons),
    )

    # --- normal map re-encode on a rotated map ----------------------------
    reset()
    nrm = np.empty((16, 16, 3), np.uint8)
    nrm[:] = (int(round((0.6 + 1) * 127.5)), 128, int(round((0.8 + 1) * 127.5)))
    npath = os.path.join(tmp, "src_Normal_OpenGL.png").replace("\\", "/")
    Image.fromarray(nrm).save(npath)
    o = plane("nrmPlane")
    mat = bpy.data.materials.new("nrmMat")
    mat.use_nodes = True
    bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    tex = mat.node_tree.nodes.new("ShaderNodeTexImage")
    tex.image = bpy.data.images.load(npath)
    nm = mat.node_tree.nodes.new("ShaderNodeNormalMap")
    mat.node_tree.links.new(tex.outputs["Color"], nm.inputs["Color"])
    mat.node_tree.links.new(nm.outputs["Normal"], bsdf.inputs["Normal"])
    o.data.materials.append(mat)
    rotate_uv_copy(o, "map2", 90)
    res = TextureTransfer().transfer(
        o,
        source_uv_set="UVMap",
        target_uv_set="map2",
        size=16,
        supersample=1,
        padding=0,
        output_dir=out_dir,
    )
    got = load(res["nrmMat"]["normal"]) / 255.0 * 2.0 - 1.0
    check(
        "rotated island re-encodes +X tilt as +Y",
        bool(
            np.allclose(got[..., 0], 0.0, atol=0.02)
            and np.allclose(got[..., 1], 0.6, atol=0.02)
        ),
        f"mean xyz {got.reshape(-1, 3).mean(axis=0)}",
    )

    # --- explicit output name: the maps, the material, and a re-run --------
    reset()
    o = plane("namedPlane")
    mat = material("namedMat", texture=checker_path)
    o.data.materials.append(mat)
    rotate_uv_copy(o, "map2", 90)
    kwargs = dict(
        source_uv_set="UVMap",
        target_uv_set="map2",
        size=16,
        supersample=1,
        padding=0,
        output_dir=out_dir,
        output_name="hero_atlas",
        assign=True,
    )
    res = btk.TextureTransfer().transfer(o, **kwargs)
    written = os.path.basename(next(iter(res.values()))["baseColor"])
    check(
        "output_name names the maps",
        written.startswith("hero_atlas_") and "namedMat" not in written,
        written,
    )
    check(
        "output_name names the material, with no _TRANSFER suffix",
        bpy.data.materials.get("hero_atlas") is not None
        and bpy.data.materials.get("hero_atlas_TRANSFER") is None,
        str(sorted(m.name for m in bpy.data.materials)),
    )
    # A second run's target material IS the one the first run assigned:
    # removing it before copying frees the datablock being copied, and
    # re-reading the members afterwards is a dangling StructRNA.
    btk.TextureTransfer().transfer(o, **kwargs)
    named = [m.name for m in bpy.data.materials if m.name.startswith("hero_atlas")]
    check(
        "a re-run replaces rather than accumulates", named == ["hero_atlas"], str(named)
    )
    slots = [sl.material.name if sl.material else None for sl in o.material_slots]
    check("the re-run is still assigned", "hero_atlas" in slots, str(slots))
    check("the re-run leaves no empty material slot", None not in slots, str(slots))

    # --- material affix: the material's naming convention, not the maps' ---
    reset()
    o = plane("affixPlane")
    o.data.materials.append(material("affixMat", texture=checker_path))
    rotate_uv_copy(o, "map2", 90)
    res = btk.TextureTransfer().transfer(
        o,
        source_uv_set="UVMap",
        target_uv_set="map2",
        size=16,
        supersample=1,
        padding=0,
        output_dir=out_dir,
        output_name="hero_atlas",
        assign=True,
        assign_suffix="_MAT",
    )
    written = os.path.basename(next(iter(res.values()))["baseColor"])
    check(
        "assign_suffix names the material only, never the maps",
        bpy.data.materials.get("hero_atlas_MAT") is not None
        and bpy.data.materials.get("hero_atlas") is None
        and written.startswith("hero_atlas_BaseColor"),
        f"{written} / {sorted(m.name for m in bpy.data.materials)}",
    )
    reset()
    o = plane("prefixPlane")
    o.data.materials.append(material("prefixMat", texture=checker_path))
    rotate_uv_copy(o, "map2", 90)
    btk.TextureTransfer().transfer(
        o,
        source_uv_set="UVMap",
        target_uv_set="map2",
        size=16,
        supersample=1,
        padding=0,
        output_dir=out_dir,
        output_name="hero_atlas",
        assign=True,
        assign_prefix="MAT_",
        assign_suffix="",
    )
    check(
        "assign_prefix prepends instead",
        bpy.data.materials.get("MAT_hero_atlas") is not None,
        str(sorted(m.name for m in bpy.data.materials)),
    )
    # The layout-derived default: a re-run derives its name from the material
    # the FIRST run assigned, so the affix must not stack.
    reset()
    o = plane("stackPlane")
    o.data.materials.append(material("stackMat", texture=checker_path))
    rotate_uv_copy(o, "map2", 90)
    kwargs = dict(
        source_uv_set="UVMap",
        target_uv_set="map2",
        size=16,
        supersample=1,
        padding=0,
        output_dir=out_dir,
        assign=True,
    )
    btk.TextureTransfer().transfer(o, **kwargs)
    btk.TextureTransfer().transfer(o, **kwargs)
    check(
        "the layout-derived affix does not stack on a re-run",
        bpy.data.materials.get("stackMat_TRANSFER") is not None
        and bpy.data.materials.get("stackMat_TRANSFER_TRANSFER") is None,
        str(sorted(m.name for m in bpy.data.materials)),
    )
    # A NAMED re-run over several layouts: each is `<name>_<layout>`, and a
    # layout is named after its target material -- on a re-run, the previous
    # result -- so the name stacked (`Table_Table_Table_deskMat_MAT`).
    reset()
    targets, sources = [], []
    for name, mat_name in (("stackA", "deskMat"), ("stackB", "legsMat")):
        o = plane(name)
        o.data.materials.append(material(mat_name, texture=checker_path))
        s = o.copy()
        s.data = o.data.copy()
        bpy.context.collection.objects.link(s)
        s.name = f"{name}_src"
        targets.append(o)
        sources.append(s)
    kwargs = dict(
        size=16,
        supersample=1,
        padding=0,
        output_dir=out_dir,
        output_name="Table",
        assign=True,
        assign_suffix="_MAT",
    )
    runs = [
        btk.TextureTransfer().transfer(targets, sources, **kwargs) for _ in range(3)
    ]
    named = sorted(m.name for m in bpy.data.materials if m.name.startswith("Table_"))
    check(
        "a named re-run over several layouts does not stack the name",
        named == ["Table_deskMat_MAT", "Table_legsMat_MAT"],
        str(named),
    )
    files = [
        sorted(os.path.basename(p) for ch in run.values() for p in ch.values())
        for run in runs
    ]
    check(
        "every named re-run writes the same maps",
        files[0] == files[1] == files[2] and "Table_deskMat_BaseColor.png" in files[0],
        str(files),
    )

    # ------------------------------------------- layouts, not names (2026-10-03)
    # Mirror of mayatk: what each target contributes is grouped into LAYOUTS
    # by overlap -- not by what its UV map is called or what it wears -- and
    # where a target stands never enters a material transfer.
    import logging

    def twin(src, name):
        """*src*'s object + mesh data copied, its materials cleared."""
        o = src.copy()
        o.data = src.data.copy()
        bpy.context.collection.objects.link(o)
        o.name = name
        o.data.materials.clear()
        return o

    def used_materials(o):
        """Names of the materials *o*'s faces actually wear."""
        mats, per_face = TextureTransfer.face_materials(o)
        return {mats[i].name for i in set(per_face.tolist()) if i >= 0}

    class _Warnings(logging.Handler):
        def __init__(self):
            super().__init__(logging.WARNING)
            self.messages = []

        def emit(self, record):
            self.messages.append(record.getMessage())

    def half_layout(name, side, uv_name=None):
        """A target plane laid out in one HALF of 0-1, beside a full-square
        source wearing the checker; *uv_name* renames the target's map."""
        s = plane(f"{name}_src")
        s.data.materials.append(material(f"{name}_srcMat", texture=checker_path))
        t = twin(s, name)
        layer = t.data.uv_layers.active
        buf = np.empty(len(t.data.loops) * 2, np.float32)
        layer.uv.foreach_get("vector", buf)
        uv = buf.reshape(-1, 2)
        uv[:, 0] = uv[:, 0] * 0.5 + 0.5 * side
        layer.uv.foreach_set("vector", uv.ravel())
        if uv_name:
            layer.name = uv_name
        return s, t

    reset()
    sa, ta = half_layout("tableTop", 0)
    sb, tb = half_layout("tableLegs", 1, "UVChannel_1")
    old = material("tableOld")
    for t in (ta, tb):
        t.data.materials.append(old)
    res = TextureTransfer().transfer(
        [ta, tb],
        [sa, sb],
        size=32,
        supersample=1,
        padding=0,
        output_dir=out_dir,
        output_name="Table",
        assign=True,
    )
    check("one layout under two UV map names is ONE output", len(res) == 1, str(res))
    check(
        "... and ONE material on both targets",
        used_materials(ta) == {"Table"} and used_materials(tb) == {"Table"},
        f"{used_materials(ta)} {used_materials(tb)}",
    )

    reset()
    s = plane("bareSrc")
    s.data.materials.append(material("bareSrcMat", texture=checker_path))
    t = twin(s, "bareTgt")
    t.data.materials.append(None)  # an empty slot: the material is gone
    try:
        res = TextureTransfer().transfer(
            t,
            s,
            size=16,
            supersample=1,
            padding=0,
            output_dir=out_dir,
            output_name="bare",
            assign=True,
        )
    except ValueError as e:
        res = {"error": str(e)}
    check(
        "a target whose material is gone is still transferred",
        len(res) == 1 and "error" not in res and used_materials(t) == {"bare"},
        f"{res} {used_materials(t)}",
    )

    reset()
    s = plane("movedSrc")
    s.data.materials.append(material("movedSrcMat", texture=checker_path))
    t = twin(s, "movedTgt")
    t.data.materials.append(material("movedTgtMat"))
    t.location.x += 5.0
    t.data.vertices[0].co.z += 0.3  # and reshaped
    bpy.context.view_layer.update()
    tt = TextureTransfer()
    caught = _Warnings()
    tt.logger.addHandler(caught)
    try:
        tt.transfer(t, s, size=16, supersample=1, padding=0, output_dir=out_dir)
    finally:
        tt.logger.removeHandler(caught)
    check(
        "a moved, reshaped target transfers without a warning",
        not caught.messages,
        str(caught.messages),
    )

    reset()
    srcs = [plane("stripA_src"), plane("stripB_src")]
    strip_src = material("stripSrcMat", texture=checker_path)
    for s in srcs:
        s.data.materials.append(strip_src)
    a = twin(srcs[0], "stripA")
    a.data.materials.append(material("deskMat"))
    b = twin(srcs[1], "stripB")
    b.data.materials.append(material("Table_deskMat_MAT"))
    try:
        TextureTransfer().transfer(
            [a, b],
            srcs,
            size=16,
            supersample=1,
            padding=0,
            output_dir=out_dir,
            output_name="Table",
            assign=True,
            assign_suffix="_MAT",
        )
        stripped = [
            o.name
            for o in (a, b)
            if len(used_materials(o)) != 1
            or not next(iter(used_materials(o))).startswith("Table_")
        ]
    except Exception as e:  # a dangling Material is part of the failure
        stripped = [repr(e)]
    check("assigning one layout never strips another", not stripped, str(stripped))

    # ------------------------------------------------ one name, one owner
    # Mirror of mayatk: a material holding the output's name is replaced only
    # when it is this output's own previous result -- stamped, and worn by
    # nothing outside the run's targets. Anything else keeps the name, and the
    # output, material AND maps, is named beside it.
    def seat(chair, rgb):
        """A plane wearing a flat *rgb* map, with a rotated ``map2``: two
        chairs' seats, which tentacle's blank Output Name derives ``seat``
        for."""
        tex = os.path.join(tmp, f"{chair}_src.png").replace("\\", "/")
        Image.new("RGB", (16, 16), rgb).save(tex)
        o = plane(f"{chair}_seat")
        o.data.materials.append(material(f"{chair}Mat", texture=tex))
        rotate_uv_copy(o, "map2", 90)
        return o

    def mirror_u(o):
        layer = o.data.uv_layers.active
        buf = np.empty(len(o.data.loops) * 2, np.float32)
        layer.uv.foreach_get("vector", buf)
        uv = buf.reshape(-1, 2)
        uv[:, 0] = 1.0 - uv[:, 0]
        layer.uv.foreach_set("vector", uv.ravel())

    reset()
    seat_kwargs = dict(
        source_uv_set="UVMap",
        target_uv_set="map2",
        size=16,
        supersample=1,
        padding=0,
        output_dir=out_dir,
        output_name="seat",
        assign=True,
    )
    chair_a, chair_b = seat("chairA", (255, 0, 0)), seat("chairB", (0, 0, 255))
    worn = []
    try:
        for _run in range(2):  # the second round: each re-run replaces its own
            TextureTransfer().transfer(chair_a, **seat_kwargs)
            TextureTransfer().transfer(chair_b, **seat_kwargs)
            worn.append((used_materials(chair_a), used_materials(chair_b)))
    except ValueError as e:  # chairA left wearing nothing has nothing to read
        worn.append(repr(e))
    check(
        "a name another object wears is never taken from it",
        worn == [({"seat"}, {"seat_1"})] * 2,
        str(worn),
    )
    red_p = os.path.join(out_dir, "seat_BaseColor.png")
    blue_p = os.path.join(out_dir, "seat_1_BaseColor.png")
    both = os.path.isfile(red_p) and os.path.isfile(blue_p)
    red = load(red_p) if both else None
    blue = load(blue_p) if both else None
    check(
        "... nor are its maps written over",
        both
        and red[..., 0].mean() > 200
        and red[..., 2].mean() < 50
        and blue[..., 2].mean() > 200
        and blue[..., 0].mean() < 50,
        f"{red_p} / {blue_p}",
    )

    reset()
    o = plane("ownPlane")
    o.data.materials.append(material("seat", texture=checker_path))
    rotate_uv_copy(o, "map2", 90)
    TextureTransfer().transfer(
        o,
        source_uv_set="UVMap",
        target_uv_set="map2",
        size=16,
        supersample=1,
        padding=0,
        output_dir=out_dir,
        output_name="seat",
        assign=True,
    )
    from blendertk.mat_utils.mat_manifest import MatManifest

    own = bpy.data.materials.get("seat")
    own_map = MatManifest._process_material(own).get("baseColor", "") if own else ""
    check(
        "a same-mesh run never replaces the material it reads",
        own_map.endswith("src_checker.png") and used_materials(o) == {"seat_1"},
        f"{own_map} / {used_materials(o)}",
    )

    reset()
    o = plane("collidePlane")
    o.data.materials.append(material("collideMat", texture=checker_path))
    rotate_uv_copy(o, "map2", 90)
    bystander = plane("bystander")
    bystander.data.materials.append(material("hero_atlas"))
    res = TextureTransfer().transfer(
        o,
        source_uv_set="UVMap",
        target_uv_set="map2",
        size=16,
        supersample=1,
        padding=0,
        output_dir=out_dir,
        output_name="hero_atlas",
        assign=True,
    )
    written = os.path.basename(next(iter(res.values()))["baseColor"])
    check(
        "an output name a bystander wears is named beside it",
        used_materials(bystander) == {"hero_atlas"}
        and used_materials(o) == {"hero_atlas_1"}
        and written == "hero_atlas_1_BaseColor.png",
        f"{used_materials(bystander)} {used_materials(o)} {written}",
    )

    reset()
    kept_dir = os.path.join(tmp, "kept_out").replace("\\", "/")
    os.makedirs(kept_dir, exist_ok=True)
    held = os.path.join(kept_dir, "hero_BaseColor.png").replace("\\", "/")
    Image.fromarray(checker()).save(held)
    before = load(held)
    s = plane("keptSrc")
    s.data.materials.append(material("keptSrcMat", texture=held))
    t = twin(s, "keptTgt")
    t.data.materials.append(material("keptTgtMat"))
    mirror_u(t)  # another layout, so a write over the source would show
    res = TextureTransfer().transfer(
        t,
        s,
        size=64,
        supersample=1,
        padding=0,
        output_dir=kept_dir,
        output_name="hero",
        assign=True,
    )
    written = os.path.basename(next(iter(res.values()))["baseColor"])
    check(
        "a map a kept material reads is never written over",
        bool(np.array_equal(load(held), before))
        and written == "hero_1_BaseColor.png"
        and used_materials(t) == {"hero_1"},
        f"{written} {used_materials(t)}",
    )

    reset()
    emit = material("emitMat")
    unweighted = TextureTransfer.material_constant(emit, "emission")
    emit_bsdf = next(n for n in emit.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    emit_bsdf.inputs["Emission Color"].default_value = (1.0, 0.5, 0.0, 1.0)
    emit_bsdf.inputs["Emission Strength"].default_value = 2.0
    weighted = TextureTransfer.material_constant(emit, "emission")
    check(
        "an emission is its colour times its strength (0 by default)",
        unweighted == (0.0, 0.0, 0.0) and weighted == (2.0, 1.0, 0.0),
        f"{unweighted} / {weighted}",
    )

    # ------------------------------------------------------- assign_from
    # Mirror of mayatk: "source" copies the SOURCE's material (the look being
    # transferred, with every node no channel owns), and a material a source
    # of the run wears is never replaced by a colliding output name.
    reset()
    src = plane("fromSrc")
    src_mat = material("held_MAT", texture=checker_path)
    src_bsdf = next(n for n in src_mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    src_bsdf.inputs["Coat Weight"].default_value = 0.5
    src_mat.node_tree.nodes.new("ShaderNodeTexNoise").name = "extra_noise"
    src.data.materials.append(src_mat)
    tgt = src.copy()
    tgt.data = src.data.copy()
    bpy.context.collection.objects.link(tgt)
    tgt.name = "fromTgt"
    tgt.data.materials.clear()
    tgt.data.materials.append(material("placeholderMat", color=(0.5, 0.5, 0.5)))
    btk.TextureTransfer().transfer(
        tgt,
        src,
        size=16,
        supersample=1,
        padding=0,
        output_dir=out_dir,
        output_name="held",
        assign=True,
        assign_suffix="_MAT",
        assign_from="source",
    )
    got = tgt.material_slots[tgt.data.polygons[0].material_index].material
    got_bsdf = next(n for n in got.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    check(
        "assign_from='source' copies the source's material",
        abs(got_bsdf.inputs["Coat Weight"].default_value - 0.5) < 1e-6
        and "extra_noise" in got.node_tree.nodes,
        got.name,
    )
    check(
        "a material a source wears survives a colliding output name",
        bpy.data.materials.get("held_MAT") is src_mat
        and src.material_slots[0].material is src_mat
        and got is not src_mat,
        f"{got.name} / {[sl.material and sl.material.name for sl in src.material_slots]}",
    )

    # ------------------------------------------------------------ lightmaps
    # transfer_lightmaps (mirror of mayatk's TestLightmapTransfer): rebound
    # when the lightmap layouts match, resampled into the target's layout
    # when they do not, committed on the target either way.
    from blendertk.light_utils.lightmap_baker.lightmap_records import (
        LightmapRecords,
    )

    LM = LightmapRecords
    lm_size = 32
    ramp_u = (np.arange(lm_size) + 0.5) / lm_size
    ramp_v = 1.0 - (np.arange(lm_size) + 0.5) / lm_size
    hdr = np.zeros((lm_size, lm_size, 3), np.float32)
    hdr[..., 0] = ramp_u[None, :] * 6.0
    hdr[..., 1] = ramp_v[:, None] * 6.0
    hdr[..., 2] = 0.25

    def rotate_layer(o, layer, angle):
        """Rotate UV map *layer* of *o* in place about the tile center."""
        uvs = o.data.uv_layers[layer]
        buf = np.empty(len(o.data.loops) * 2, np.float32)
        uvs.uv.foreach_get("vector", buf)
        uv = buf.reshape(-1, 2) - 0.5
        rad = np.deg2rad(angle)
        rot = np.stack(
            [
                uv[:, 0] * np.cos(rad) - uv[:, 1] * np.sin(rad),
                uv[:, 0] * np.sin(rad) + uv[:, 1] * np.cos(rad),
            ],
            axis=1,
        )
        uvs.uv.foreach_set("vector", (rot + 0.5).astype(np.float32).ravel())

    def lightmapped(name, image=None, rect=None, written=True):
        """A plane with a ``Lightmap`` map (UVMap's layout) and a committed map."""
        o = plane(name)
        active = o.data.uv_layers.active
        o.data.uv_layers.new(name="Lightmap", do_init=True)  # copies the active
        o.data.uv_layers.active = active
        path = os.path.join(tmp, f"{name}_Lightmap.exr").replace("\\", "/")
        LM._write_lightmap(path, hdr if image is None else image)
        LM.commit(
            {o.name: path},
            {o.name: rect} if rect else None,
            intensity=1.5,
            written=written,
        )
        return o, path

    def lm_copy(src, name, rotate=0):
        """*src* copied without its marker; its Lightmap map optionally rotated."""
        o = src.copy()
        o.data = src.data.copy()
        bpy.context.collection.objects.link(o)
        o.name = name
        if LM.LIGHTMAP_INFO_PROP in o:
            del o[LM.LIGHTMAP_INFO_PROP]
        if rotate:
            rotate_layer(o, "Lightmap", rotate)
        return o

    lm_out = os.path.join(tmp, "lm_out").replace("\\", "/")

    reset()
    rect = [0.5, 0.5, 0.25, 0.25]
    src, path = lightmapped("lmSrcA", rect=rect)
    tgt = lm_copy(src, "lmTgtA")
    out = LM.transfer_lightmaps(tgt, src, output_dir=lm_out)
    info = LM.lightmap_info(tgt)
    check(
        "a matching lightmap layout is rebound, not resampled",
        list(out) == [tgt.name]
        and out[tgt.name]["how"] == "rebound"
        and os.path.normcase(out[tgt.name]["path"]) == os.path.normcase(path)
        and not os.path.isdir(lm_out),
        str(out),
    )
    check(
        "the rebound target carries the source's rect, intensity and map",
        info.get("map") == os.path.basename(path)
        and info.get("scaleOffset") == rect
        and info.get("intensity") == 1.5
        and info.get("uv_set") == "Lightmap",
        str(info),
    )

    reset()
    src, _path = lightmapped("lmSrcB")
    tgt = lm_copy(src, "lmTgtB", rotate=90)
    out = LM.transfer_lightmaps(
        tgt, src, output_dir=lm_out, output_name="hero", supersample=1
    )
    got_path = (out.get(tgt.name) or {}).get("path", "")
    got = LM._read_lightmap(got_path) if got_path else None
    check(
        "a different lightmap layout is resampled into <name>_Lightmap.exr",
        (out.get(tgt.name) or {}).get("how") == "resampled"
        and os.path.basename(got_path) == "hero_Lightmap.exr",
        str(out),
    )
    check(
        "the resampled map follows the layout and keeps HDR values",
        got is not None
        and got.max() > 1.0
        and float(np.abs(got - np.rot90(hdr, 1)).max()) < 0.05,
        "" if got is None else f"max err {float(np.abs(got - np.rot90(hdr, 1)).max())}",
    )
    info = LM.lightmap_info(tgt)
    check(
        "the resampled target is committed on its own map at the identity rect",
        info.get("map") == "hero_Lightmap.exr"
        and info.get("scaleOffset") == [1.0, 1.0, 0.0, 0.0]
        and info.get("intensity") == 1.5,
        str(info),
    )

    reset()
    atlas = np.full((32, 32, 3), 2.0, np.float32)
    atlas[:, 16:] = 50.0  # another object's lighting in the shared map
    src, _path = lightmapped("lmSrcC", image=atlas, rect=[0.5, 1.0, 0.0, 0.0])
    tgt = lm_copy(src, "lmTgtC", rotate=90)
    out = LM.transfer_lightmaps(tgt, src, output_dir=lm_out)
    got = LM._read_lightmap(out[tgt.name]["path"])
    check(
        "a resample reads only the source's atlas cell",
        bool(np.allclose(got, 2.0, atol=0.01)),
        f"max {float(got.max())}",
    )

    reset()
    src, path = lightmapped("lmSrcD", written=False)
    tgt = lm_copy(src, "lmTgtD")
    key = os.path.basename(path).lower()
    LM.transfer_lightmaps(tgt, src, output_dir=lm_out)
    check(
        "a rebind does not claim the map was written here",
        key not in LM._writers(),
        str(LM._writers()),
    )

    reset()
    src = plane("lmBare")
    src.data.uv_layers.new(name="Lightmap", do_init=True)
    tgt = lm_copy(src, "lmBareTgt")
    out = LM.transfer_lightmaps(tgt, src, output_dir=lm_out)
    check(
        "a source without a lightmap carries nothing",
        out == {} and LM.lightmap_info(tgt) == {},
        str(out),
    )

    # Mirror of mayatk: the pair shares topology, so the source's own
    # lightmap layout fits the target loop for loop -- it is given that, and
    # the lightmap is REBOUND. It used to be skipped outright.
    reset()
    lm_rect = [0.5, 0.5, 0.25, 0.25]
    src, path = lightmapped("lmSrcE", rect=lm_rect)
    rotate_layer(src, "Lightmap", 90)  # not UVMap's layout: the copy is provable
    tgt = lm_copy(src, "lmTgtE")
    tgt.data.uv_layers.remove(tgt.data.uv_layers["Lightmap"])
    lm_out_e = os.path.join(tmp, "lm_out_e").replace("\\", "/")
    out = LM.transfer_lightmaps(tgt, src, output_dir=lm_out_e)
    info = LM.lightmap_info(tgt)
    given = info.get("uv_set") or ""

    def lm_vectors(o, name):
        buf = np.empty(len(o.data.loops) * 2, np.float32)
        o.data.uv_layers[name].uv.foreach_get("vector", buf)
        return buf

    check(
        "a target without a lightmap UV map takes the source's and is rebound",
        (out.get(tgt.name) or {}).get("how") == "rebound"
        and info.get("scaleOffset") == lm_rect
        and given in tgt.data.uv_layers
        and bool(
            np.allclose(lm_vectors(tgt, given), lm_vectors(src, "Lightmap"), atol=1e-6)
        )
        and not os.path.isdir(lm_out_e),
        f"{out} {info}",
    )

    reset()
    src, _path = lightmapped("lmSrcF")
    tgt = lm_copy(src, "lmTgtF")
    tgt.location.x += 10.0
    bpy.context.view_layer.update()
    import logging

    class _Recorder(logging.Handler):
        def emit(self, record):
            warned.append(record.getMessage())

    warned = []
    recorder = _Recorder(logging.WARNING)
    LM.logger.addHandler(recorder)
    try:
        out = LM.transfer_lightmaps(tgt, src, output_dir=lm_out)
    finally:
        LM.logger.removeHandler(recorder)
    check(
        "a target elsewhere is carried, with a warning",
        (out.get(tgt.name) or {}).get("how") == "rebound"
        and any("different places" in m for m in warned),
        f"{out} {warned}",
    )

    try:
        LM.transfer_lightmaps(tgt, None)
        raised = False
    except ValueError:
        raised = True
    check("transfer_lightmaps without a source raises", raised)

    # ---------------------------------------------------- output dir rules
    # The panel's Output Folder field: blank = the default subfolder, a
    # relative entry lands under the .blend's textures folder (the portable
    # spelling a browse writes back), "//" and full paths win outright.
    reset()
    blend = os.path.join(tmp, "outdir_probe.blend")
    bpy.ops.wm.save_as_mainfile(filepath=blend)
    TT = btk.TextureTransfer
    base = os.path.normpath(TT.output_base_dir())
    check(
        "output_base_dir is the .blend's textures folder",
        base == os.path.normpath(os.path.join(os.path.dirname(blend), "textures")),
        base,
    )
    check(
        "blank resolves to the default subfolder, not the base",
        os.path.normpath(TT.resolve_output_dir(""))
        == os.path.normpath(TT.default_output_dir())
        and os.path.normpath(TT.default_output_dir()) != base,
        TT.resolve_output_dir(""),
    )
    check(
        "a relative entry lands under the base",
        os.path.normpath(TT.resolve_output_dir("bakes/v2"))
        == os.path.join(base, "bakes", "v2"),
        TT.resolve_output_dir("bakes/v2"),
    )
    check(
        "Blender's // prefix is expanded, not treated as relative",
        os.path.normpath(TT.resolve_output_dir("//out"))
        == os.path.normpath(os.path.join(os.path.dirname(blend), "out")),
        TT.resolve_output_dir("//out"),
    )
    picked = os.path.join(base, "bakes", "v2")
    entry = ptk.FileUtils.relativize_output_dir(picked, base)
    check(
        "the browse spelling round-trips through resolve_output_dir",
        not os.path.isabs(entry)
        and os.path.normpath(TT.resolve_output_dir(entry)) == picked,
        f"entry={entry!r}",
    )

except Exception as e:
    lines.append(f"FAIL setup: {e!r}")
    lines.append(traceback.format_exc())

ok = all(line.startswith("OK") for line in lines)
print("\n===UV-TEXTURE-TRANSFER===")
print("\n".join(lines))
print(f"===RESULT: {'PASS' if ok else 'FAIL'}===")
