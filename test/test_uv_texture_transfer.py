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
        f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}"
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
        shared, source_uv_set="UVMap", target_uv_set="map2", size=32, supersample=1,
        padding=0, output_dir=out_dir, assign=True,
    )
    check("two materials on one set -> one output named after the set", list(res) == ["map2"], str(list(res)))
    new = bpy.data.materials.get("map2_TRANSFER")
    slot_of_new = next((i for i, sl in enumerate(shared.material_slots) if sl.material == new), None)
    check(
        "one map2_TRANSFER material on every face",
        new is not None and slot_of_new is not None
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
    check("a re-run replaces rather than accumulates", named == ["hero_atlas"], str(named))
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
        os.path.normpath(TT.resolve_output_dir("")) == os.path.normpath(TT.default_output_dir())
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
