"""blendertk.env_utils.material_records -- the ``materials`` record's Blender
producer, headless test.
Run: blender --background --factory-startup --python blendertk/test/test_material_records.py

Mirror of mayatk's ``test_material_records.py``. Blender's own FBX writer is
why the record is read here, from the file, rather than from the FBX: it
writes a Principled BSDF as a Phong (metallic in ``ReflectionFactor``,
roughness folded into a shininess), which no reader of the file can tell
from an authored Phong. The producer reads the sockets; the model
(``ptk.MaterialFamilies``, pinned by pythontk's ``test_material_record``)
says what they mean. Added: 2026-10-20

It is the one reader of the file's materials: the graph readers the retired
``SceneState`` sections shared -- the alpha mode, Blender's unlit model, the
opacity's scalar part -- are its own, and their checks moved here with them
(2026-10-20).

Record v2 (2026-11-05): the record is literal glTF -- metal and roughness
packed into one image of the pack store (never beside the maps),
emission above 1 as the strength, Blender's unlit model as
KHR_materials_unlit, a Mapping node's location and scale as
KHR_texture_transform -- and a real export's FBX record and the GLB built
from that FBX state the same materials (the "record v2" checks).
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


def rounded(values, places=4):
    return [round(v, places) for v in values]


store = None
try:
    import bpy
    import pythontk as ptk
    from blendertk.mat_utils._mat_utils import MatUtils
    from blendertk.env_utils.material_records import MaterialRecords

    store = ptk.TempArtifacts("btk_material_records", policy="scoped")
    folder = store.dir_path()

    def fresh(name):
        mat = bpy.data.materials.new(name)
        MatUtils.get_node_tree(mat, create=True)
        return mat

    def principled(mat):
        return next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")

    def worn(mat, name):
        bpy.ops.mesh.primitive_cube_add()
        obj = bpy.context.active_object
        obj.name = name
        obj.data.materials.append(mat)
        return obj

    def entries(objects):
        return {e["name"]: e for e in MaterialRecords.entries(objects)}

    def image(name, rgba=(0.5, 0.5, 0.5, 1.0)):
        img = bpy.data.images.new(name, 4, 4)
        img.pixels = list(rgba) * 16
        img.filepath_raw = os.path.join(folder, name + ".png")
        img.file_format = "PNG"
        img.save()
        return img

    # --- a Principled BSDF reads as the pbr family ----------------------------
    body = fresh("Body")
    node = principled(body)
    node.inputs["Base Color"].default_value = (0.2, 0.4, 0.6, 1.0)
    node.inputs["Metallic"].default_value = 0.3
    node.inputs["Roughness"].default_value = 0.7
    node.inputs["Emission Color"].default_value = (1.0, 0.5, 0.0, 1.0)
    node.inputs["Emission Strength"].default_value = 2.0
    cube = worn(body, "BodyCube")
    entry = entries([cube])["Body"]
    check(
        "its sockets are the factors",
        (
            rounded(entry["baseColorFactor"]),
            round(entry["metallicFactor"], 4),
            round(entry["roughnessFactor"], 4),
        )
        == ([0.2, 0.4, 0.6, 1.0], 0.3, 0.7),
        entry,
    )
    check(
        "its emission is the colour times the strength",
        rounded(entry["emissiveFactor"]) == [2.0, 1.0, 0.0],
        entry["emissiveFactor"],
    )
    check("it is opaque", entry["alphaMode"] == "OPAQUE", entry["alphaMode"])

    # --- a glass says the record states no transmission: the model's note
    # (ptk.MaterialFamilies, mirror of mayatk's). Added: 2026-10-31 -----------
    lens = fresh("TransmissiveLens")
    principled(lens).inputs["Transmission Weight"].default_value = 0.5
    lens_notes = []
    MaterialRecords.entries([worn(lens, "TransmissiveLensCube")], notes=lens_notes)
    check(
        "a transmission is said, in the model's words",
        lens_notes
        == [f"Material TransmissiveLens: {ptk.MaterialFamilies.TRANSMISSION_NOTE}"],
        lens_notes,
    )

    # --- a map stands in for its value, named by its file ---------------------
    painted = fresh("Painted")
    tex = painted.node_tree.nodes.new("ShaderNodeTexImage")
    tex.image = image("Painted_BaseColor")
    painted.node_tree.links.new(tex.outputs["Color"], principled(painted).inputs["Base Color"])
    entry = entries([worn(painted, "PaintedCube")])["Painted"]
    check(
        "a colour map is the albedo, by its file name",
        (entry.get("baseColorTexture"), entry["baseColorFactor"][:3])
        == ("Painted_BaseColor.png", [1.0, 1.0, 1.0]),
        entry,
    )

    # --- opacity and its mode -------------------------------------------------
    glass = fresh("Glass")
    principled(glass).inputs["Alpha"].default_value = 0.25
    entry = entries([worn(glass, "GlassCube")])["Glass"]
    check(
        "an alpha below 1 blends at that alpha",
        (entry["alphaMode"], round(entry["baseColorFactor"][3], 4)) == ("BLEND", 0.25),
        entry,
    )
    cutout = fresh("Cutout")
    tree = cutout.node_tree
    tex = tree.nodes.new("ShaderNodeTexImage")
    tex.image = image("Cutout_Alpha", (1.0, 1.0, 1.0, 0.5))
    threshold = tree.nodes.new("ShaderNodeMath")
    threshold.operation = "GREATER_THAN"
    threshold.label = "Mask Threshold"
    threshold.inputs[1].default_value = 0.35
    tree.links.new(tex.outputs["Alpha"], threshold.inputs[0])
    tree.links.new(threshold.outputs[0], principled(cutout).inputs["Alpha"])
    entry = entries([worn(cutout, "CutoutCube")])["Cutout"]
    check(
        "a thresholded alpha is MASK at its threshold",
        (entry["alphaMode"], round(entry.get("alphaCutoff", -1), 4)) == ("MASK", 0.35),
        entry,
    )

    # --- an Emission surface is Blender's unlit model -------------------------
    flat = fresh("Flat")
    tree = flat.node_tree
    for n in list(tree.nodes):
        if n.type != "OUTPUT_MATERIAL":
            tree.nodes.remove(n)
    emission = tree.nodes.new("ShaderNodeEmission")
    emission.inputs["Color"].default_value = (0.1, 0.2, 0.3, 1.0)
    out = next(n for n in tree.nodes if n.type == "OUTPUT_MATERIAL")
    tree.links.new(emission.outputs["Emission"], out.inputs["Surface"])
    entry = entries([worn(flat, "FlatCube")]).get("Flat") or {}
    check(
        "an Emission surface is unlit, its colour the base colour",
        entry.get("unlit") is True
        and rounded(entry.get("baseColorFactor", [0, 0, 0, 0])) == [0.1, 0.2, 0.3, 1.0],
        entry,
    )

    # --- the alpha mode the file authors (_alpha_mode_of) ---------------------
    # The FBX cannot say which glTF ``alphaMode`` a material wants and FBX2glTF
    # decides from the base colour's alpha alone, so a transparent material
    # reached WebXR OPAQUE -- its silhouette embedded, a solid square. The
    # boundary is the point: Blender's defaults are not neutral -- a brand-new
    # material already reads ``blend_method='HASHED'`` /
    # ``surface_render_method='DITHERED'`` -- so a reader keying off "not
    # OPAQUE" would mark every material in the file transparent.
    def mode_of(mat):
        return MaterialRecords._alpha_mode_of(mat)

    plain = fresh("Plain")
    check(
        "a default material is omitted (HASHED/DITHERED are defaults, not intent)",
        mode_of(plain) is None,
        f"blend={plain.blend_method} render={getattr(plain, 'surface_render_method', '-')}",
    )

    # `blend_method = "CLIP"` does NOT stick on 4.2+: the property is a shim
    # over `surface_render_method`, and EEVEE Next kept only DITHERED/BLENDED.
    shim = fresh("ClipShim")
    shim.blend_method = "CLIP"
    check(
        "blend_method CLIP does not survive on this Blender (so it cannot be the signal)",
        shim.blend_method != "CLIP",
        f"came back {shim.blend_method!r}",
    )

    # MatUtils marks a cutout with a labelled GREATER_THAN node on the alpha,
    # which is what actually cuts in EEVEE, Cycles and the exporters.
    gated = fresh("ThresholdGate")
    tree = gated.node_tree
    noise = tree.nodes.new("ShaderNodeTexNoise")
    gate = tree.nodes.new("ShaderNodeMath")
    gate.operation = "GREATER_THAN"
    gate.inputs[1].default_value = 0.25
    gate.label = MaterialRecords.MASK_NODE_LABEL
    tree.links.new(gate.inputs[0], noise.outputs["Fac"])
    tree.links.new(principled(gated).inputs["Alpha"], gate.outputs["Value"])
    found = mode_of(gated)
    check("a threshold node reads as MASK", (found or {}).get("mode") == "MASK", found)
    check(
        "MASK carries the node's threshold as the cutoff",
        (found or {}).get("cutoff") == 0.25,
        found,
    )

    # An UNLABELLED math node is somebody's own graph, not a cutout marker.
    plain_math = fresh("PlainMath")
    tree = plain_math.node_tree
    noise = tree.nodes.new("ShaderNodeTexNoise")
    gate = tree.nodes.new("ShaderNodeMath")
    gate.operation = "GREATER_THAN"
    tree.links.new(gate.inputs[0], noise.outputs["Fac"])
    tree.links.new(principled(plain_math).inputs["Alpha"], gate.outputs["Value"])
    check(
        "an unlabelled threshold is BLEND, not MASK (a linked alpha, nothing more)",
        (mode_of(plain_math) or {}).get("mode") == "BLEND",
        mode_of(plain_math),
    )

    legacy = fresh("LegacyBlend")
    legacy.blend_method = "BLEND"
    check(
        "blend_method BLEND reads as BLEND",
        (mode_of(legacy) or {}).get("mode") == "BLEND",
    )
    blended = fresh("EeveeNextBlend")
    blended.surface_render_method = "BLENDED"
    check(
        "surface_render_method BLENDED reads as BLEND",
        (mode_of(blended) or {}).get("mode") == "BLEND",
    )
    dialled = fresh("AlphaDialled")
    principled(dialled).inputs["Alpha"].default_value = 0.4
    check(
        "a Principled Alpha below 1 reads as BLEND",
        (mode_of(dialled) or {}).get("mode") == "BLEND",
    )
    linked = fresh("AlphaLinked")
    tree = linked.node_tree
    noise = tree.nodes.new("ShaderNodeTexNoise")
    tree.links.new(principled(linked).inputs["Alpha"], noise.outputs["Fac"])
    check(
        "a LINKED Principled Alpha reads as BLEND (the value still says 1.0)",
        (mode_of(linked) or {}).get("mode") == "BLEND",
    )

    def mixed_over(mat, shader_output):
        """*mat*'s active output: a Transparent BSDF mixed against *shader_output*."""
        tree = mat.node_tree
        mix = tree.nodes.new("ShaderNodeMixShader")
        transp = tree.nodes.new("ShaderNodeBsdfTransparent")
        out = next(n for n in tree.nodes if n.type == "OUTPUT_MATERIAL")
        tree.links.new(mix.inputs[1], transp.outputs[0])
        tree.links.new(mix.inputs[2], shader_output)
        tree.links.new(out.inputs["Surface"], mix.outputs[0])
        return mix

    # The shadow rigs' shape: no alpha socket at all, a mixed Transparent.
    mixed = fresh("MixedTransparent")
    mixed_over(mixed, principled(mixed).outputs[0])
    check(
        "a Transparent BSDF mixed into the surface reads as BLEND",
        (mode_of(mixed) or {}).get("mode") == "BLEND",
    )
    orphan = fresh("OrphanTransparent")
    orphan.node_tree.nodes.new("ShaderNodeBsdfTransparent")  # present but unlinked
    check(
        "a Transparent BSDF that reaches nothing is NOT transparency",
        mode_of(orphan) is None,
    )
    plane_like = fresh("ShadowPlaneLike")
    mixed_over(plane_like, plane_like.node_tree.nodes.new("ShaderNodeEmission").outputs[0])
    plane_like.blend_method = "BLEND"
    check(
        "the shadow-plane material shape reads as BLEND",
        (mode_of(plane_like) or {}).get("mode") == "BLEND",
        mode_of(plane_like),
    )

    # --- Blender's unlit model (_unlit_materials: material NAMES) -------------
    # FBX2glTF makes every material lit PBR; a shadow plane reached WebXR lit
    # and read BRIGHTER than its floor (Maya side, 2026-10-15).
    def unlit_of(mat):
        return mat.name in MaterialRecords._unlit_materials([mat])

    emits = fresh("EmissionOnly")
    tree = emits.node_tree
    out = next(n for n in tree.nodes if n.type == "OUTPUT_MATERIAL")
    tree.links.new(out.inputs["Surface"], tree.nodes.new("ShaderNodeEmission").outputs[0])
    check("an Emission shader alone reads unlit", unlit_of(emits))
    check(
        "the shadow-plane shape (Emission mixed with Transparent) reads unlit",
        unlit_of(plane_like),
    )
    check("a Principled BSDF is lit", not unlit_of(fresh("LitPlain")))
    check("a Transparent BSDF mixed with a Principled is lit", not unlit_of(mixed))
    glow = fresh("EmissionOverLit")
    tree = glow.node_tree
    add = tree.nodes.new("ShaderNodeAddShader")
    out = next(n for n in tree.nodes if n.type == "OUTPUT_MATERIAL")
    tree.links.new(add.inputs[0], tree.nodes.new("ShaderNodeEmission").outputs[0])
    tree.links.new(add.inputs[1], principled(glow).outputs[0])
    tree.links.new(out.inputs["Surface"], add.outputs[0])
    check("Emission added over a BSDF is lit", not unlit_of(glow))

    # What else shades a material unlit is a row resolved on use, as the
    # producers are -- the shadow rig's planes (test_shadow_rig reads a real
    # one, by name: the source returns material objects).
    # One resolver for every row table (``FbxUtils._resolve_row``, as mayatk's
    # reader does), not a third copy of it. 2026-10-24
    from blendertk.env_utils.fbx_utils import FbxUtils
    from blendertk.rig_utils.shadow_rig._shadow_rig import ShadowRig

    row = MaterialRecords.UNLIT_SOURCES[0]
    check(
        "the unlit source row resolves to ShadowRig.plane_materials",
        FbxUtils._resolve_row("Unlit source", *row) == (ShadowRig.plane_materials,),
        row,
    )
    check(
        "...a misspelt method resolves to None, an unimportable module to nothing",
        FbxUtils._resolve_row("Unlit source", row[0], row[1], "no_such") == (None,)
        and FbxUtils._resolve_row("Unlit source", "no.such.module", "X", "y") is None,
    )
    check(
        "...and the reader keeps no resolver of its own",
        not hasattr(MaterialRecords, "_resolve_row"),
    )

    # --- the opacity's scalar part (_opacity_factor) --------------------------
    # glTF states it as ``baseColorFactor[3]`` and the FBX drops it. A shadow
    # plane held at a static 0.836 fade reached WebXR at alpha 1.0 -- solid black
    # where the viewport drew it half-transparent (Maya side, 2026-10-15).
    def alpha_of(mat):
        return MaterialRecords._opacity_factor(mat)

    def faded(tree, into, scalar=0.836):
        """Texture alpha x a Value node through a Math MULTIPLY, into *into*."""
        tex = tree.nodes.new("ShaderNodeTexImage")
        fade = tree.nodes.new("ShaderNodeValue")
        fade.outputs[0].default_value = scalar
        mult = tree.nodes.new("ShaderNodeMath")
        mult.operation = "MULTIPLY"
        tree.links.new(mult.inputs[0], tex.outputs["Alpha"])
        tree.links.new(mult.inputs[1], fade.outputs[0])
        tree.links.new(into, mult.outputs[0])
        return tex

    fade_mat = fresh("FadeMat")
    faded(fade_mat.node_tree, principled(fade_mat).inputs["Alpha"])
    check(
        "texture alpha x a fade carries the fade",
        abs((alpha_of(fade_mat) or 0.0) - 0.836) < 1e-5,
        alpha_of(fade_mat),
    )
    swapped = fresh("FadeSwapped")
    tree = swapped.node_tree
    tex = tree.nodes.new("ShaderNodeTexImage")
    mult = tree.nodes.new("ShaderNodeMath")
    mult.operation = "MULTIPLY"
    mult.inputs[0].default_value = 0.25
    tree.links.new(mult.inputs[1], tex.outputs["Alpha"])
    tree.links.new(principled(swapped).inputs["Alpha"], mult.outputs[0])
    check(
        "...the scalar on either input, a bare constant too",
        abs((alpha_of(swapped) or 0.0) - 0.25) < 1e-5,
        alpha_of(swapped),
    )
    tint = fresh("Tint")
    principled(tint).inputs["Alpha"].default_value = 0.5
    check(
        "a constant opacity carries itself",
        abs((alpha_of(tint) or 0.0) - 0.5) < 1e-5,
        alpha_of(tint),
    )
    bare = fresh("Bare")
    tree = bare.node_tree
    tex = tree.nodes.new("ShaderNodeTexImage")
    tree.links.new(principled(bare).inputs["Alpha"], tex.outputs["Alpha"])
    check("a texture alone states nothing", alpha_of(bare) is None)
    check("an opaque material states nothing", alpha_of(fresh("Solid")) is None)

    # The shadow rig's own shape: no Principled on the surface, the fade
    # reaching the Mix Shader's factor against its Transparent BSDF.
    rig_like = fresh("RigFade")
    mix = mixed_over(rig_like, rig_like.node_tree.nodes.new("ShaderNodeEmission").outputs[0])
    faded(rig_like.node_tree, mix.inputs["Fac"], scalar=0.6)
    principled(rig_like).inputs["Alpha"].default_value = 0.2  # unwired: not read
    check(
        "the shadow rig's fade reads through its Mix Shader factor",
        abs((alpha_of(rig_like) or 0.0) - 0.6) < 1e-5,
        alpha_of(rig_like),
    )

    # A detached plane's shape: the active output mixes on the PNG's own
    # alpha (its fade already baked in), and an inactive second output holds
    # a Principled BSDF for the exporters. Neither states a factor.
    detached = fresh("Detached")
    tree = detached.node_tree
    tree.nodes.clear()
    tex = tree.nodes.new("ShaderNodeTexImage")
    emis = tree.nodes.new("ShaderNodeEmission")
    transp = tree.nodes.new("ShaderNodeBsdfTransparent")
    mix = tree.nodes.new("ShaderNodeMixShader")
    out = tree.nodes.new("ShaderNodeOutputMaterial")
    tree.links.new(tex.outputs["Color"], emis.inputs["Color"])
    tree.links.new(tex.outputs["Alpha"], mix.inputs["Fac"])
    tree.links.new(transp.outputs[0], mix.inputs[1])
    tree.links.new(emis.outputs[0], mix.inputs[2])
    tree.links.new(mix.outputs[0], out.inputs["Surface"])
    bsdf = tree.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Alpha"].default_value = 0.3
    export = tree.nodes.new("ShaderNodeOutputMaterial")
    tree.links.new(bsdf.outputs[0], export.inputs["Surface"])
    out.is_active_output = True
    export.is_active_output = False
    try:
        got = alpha_of(detached)
    except Exception as exc:  # noqa: BLE001 -- the check IS that nothing raises
        got = f"error: {exc}"
    check(
        "a detached plane states no factor (its inactive Principled unread)",
        got is None,
        got,
    )

    # --- transient: the bracket's write carries it, the file does not keep it --
    from blendertk.env_utils.fbx_utils import FbxUtils
    from blendertk.node_utils.data_nodes import DataNodes

    path = os.path.join(folder, "carried.fbx")
    ctx = FbxUtils.export_context(mode=ptk.ExportContext.HANDOFF)
    with FbxUtils.export_prepared(ctx, only=[ptk.SceneRecords.MATERIALS]):
        held = ptk.SceneRecords.MATERIALS.load(DataNodes)
        carrier = DataNodes.get_export_node(create=False)
        FbxUtils.export(path, objects=[cube, carrier])
    check("the bracket's publish holds it for the write", bool(held), held)
    check(
        "the file does not keep it once the bracket closes",
        ptk.SceneRecords.MATERIALS.load(DataNodes) is None,
    )
    check(
        "nor the carrier the write alone needed",
        DataNodes.get_export_node(create=False) is None,
    )
    carried = ptk.FbxMedia.record(path, "materials") or {}
    names = [e["name"] for e in carried.get("materials") or []]
    check("the written FBX carries it", "Body" in names, names)
    stated = next((e for e in carried.get("materials") or [] if e["name"] == "Body"), {})
    pbr = stated.get("pbrMetallicRoughness") or {}
    check(
        "as the sockets state it, not as Blender's FBX writer folds it",
        (
            round(pbr.get("metallicFactor", -1), 4),
            round(pbr.get("roughnessFactor", -1), 4),
        )
        == (0.3, 0.7),
        stated,
    )

    # --- record v2: literal glTF (2026-11-05) --------------------------------
    import copy

    def png(name, value, mode="L"):
        path = os.path.join(folder, name)
        image = bpy.data.images.new(name, 4, 4, alpha=False)
        grey = value / 255.0
        image.pixels[:] = [grey, grey, grey, 1.0] * 16
        image.filepath_raw = path
        image.file_format = "PNG"
        image.save()
        return image

    def textured(name, image, socket, non_color=True):
        mat = bpy.data.materials.get(name) or fresh(name)
        node = mat.node_tree.nodes.new("ShaderNodeTexImage")
        node.image = image
        if non_color:
            image.colorspace_settings.name = "Non-Color"
        mat.node_tree.links.new(principled(mat).inputs[socket], node.outputs["Color"])
        return mat, node

    metal_rough, _ = textured("Packed", png("Packed_Metallic.png", 200), "Metallic")
    textured("Packed", png("Packed_Roughness.png", 50), "Roughness")
    record = MaterialRecords.export_record(
        ptk.ExportContext(scope=(worn(metal_rough, "PackedCube").name,))
    ).payload
    check(
        "record v2: the record is one a reader reads",
        ptk.SceneRecords.problems(ptk.SceneRecords.MATERIALS, record) == [],
        ptk.SceneRecords.problems(ptk.SceneRecords.MATERIALS, record),
    )
    (packed_mat,) = record["materials"]
    files = ptk.MaterialFamilies.material_files(record, packed_mat)
    check(
        "record v2: metal and roughness pack into one image, a delivery file "
        "of the pack store, never beside the maps",
        files.get("metallicRoughnessTexture") == "Packed_ORM.png"
        and ptk.MaterialFamilies.packed_file("Packed_ORM.png") is not None
        and not os.path.exists(os.path.join(folder, "Packed_ORM.png")),
        (files, sorted(os.listdir(folder))),
    )

    hot = fresh("Hot")
    bsdf = principled(hot)
    bsdf.inputs["Emission Color"].default_value = (1.0, 0.5, 0.0, 1.0)
    bsdf.inputs["Emission Strength"].default_value = 4.0
    emits = fresh("Glows")
    tree = emits.node_tree
    tree.nodes.clear()
    emission = tree.nodes.new("ShaderNodeEmission")
    out = tree.nodes.new("ShaderNodeOutputMaterial")
    tree.links.new(emission.outputs[0], out.inputs["Surface"])
    by_name = ptk.MaterialFamilies.materials_by_name(
        MaterialRecords.export_record(
            ptk.ExportContext(scope=(worn(hot, "HotCube").name, worn(emits, "GlowCube").name))
        ).payload
    )
    check(
        "record v2: emission above one is the strength, never the factor",
        (by_name.get("Hot") or {}).get("emissiveFactor") == [1.0, 0.5, 0.0]
        and (by_name.get("Hot") or {}).get("extensions")
        == {"KHR_materials_emissive_strength": {"emissiveStrength": 4.0}},
        by_name.get("Hot"),
    )
    check(
        "record v2: an Emission surface is KHR_materials_unlit",
        ((by_name.get("Glows") or {}).get("extensions") or {}).get("KHR_materials_unlit")
        == {},
        by_name.get("Glows"),
    )

    tiled, tile_node = textured(
        "Tiled", png("Tiled_BaseColor.png", 120), "Base Color", non_color=False
    )
    mapping = tiled.node_tree.nodes.new("ShaderNodeMapping")
    mapping.inputs["Scale"].default_value = (2.0, 3.0, 1.0)
    mapping.inputs["Location"].default_value = (0.25, 0.5, 0.0)
    tiled.node_tree.links.new(tile_node.inputs["Vector"], mapping.outputs["Vector"])
    (tiled_mat,) = MaterialRecords.export_record(
        ptk.ExportContext(scope=(worn(tiled, "TiledCube").name,))
    ).payload["materials"]
    transform = (
        (tiled_mat["pbrMetallicRoughness"].get("baseColorTexture") or {}).get("extensions")
        or {}
    ).get("KHR_texture_transform")
    check(
        "record v2: a Mapping node's location and scale are the texture transform",
        transform == {"offset": [0.25, 1.0 - 3.0 - 0.5], "scale": [2.0, 3.0]},
        transform,
    )

    # A real export: the FBX's record read back, and the GLB built from that
    # FBX alone, whose materials are the record's.
    real = os.path.join(folder, "real.fbx")
    shipped = ("PackedCube", "HotCube", "GlowCube", "TiledCube")
    objects = [bpy.data.objects[n] for n in shipped]
    scoped = FbxUtils.export_context(mode=ptk.ExportContext.HANDOFF, scope=shipped)
    with FbxUtils.export_prepared(scoped, only=[ptk.SceneRecords.MATERIALS]):
        FbxUtils.export(real, objects=objects + [DataNodes.get_export_node(create=False)])
    ptk.FbxMedia.embed_dependencies(real, search_dirs=[folder])
    record = ptk.FbxMedia.record(real, "materials") or {}
    check(
        "record v2: the written FBX carries the record a reader reads",
        ptk.SceneRecords.problems(ptk.SceneRecords.MATERIALS, record) == []
        and {"Packed", "Hot", "Glows", "Tiled"}
        == {m["name"] for m in record.get("materials") or []},
        [m.get("name") for m in record.get("materials") or []],
    )
    built = ptk.GlbPipeline.build(real, os.path.join(folder, "real.glb"), downsize=False)

    def resolved(material, image_of):
        found = copy.deepcopy(material)
        found.pop("extras", None)
        for container, slot in ptk.MaterialFamilies.TEXTURE_SLOTS:
            holder = found.get(container) if container else found
            if isinstance(holder, dict) and isinstance(holder.get(slot), dict):
                holder[slot]["index"] = image_of(holder[slot]["index"])
        return found

    with ptk.MeshConvert.open_glb(built["glb"]) as edit:

        def glb_image(index):
            label = edit.image_label(edit.image_for_texture(index))
            return os.path.splitext(os.path.basename(label))[0].casefold()

        def record_image(index):
            name = ptk.MaterialFamilies.texture_file(record, index)
            return os.path.splitext(name)[0].casefold()

        glb_by_name = {m.get("name"): m for m in edit.materials}
        for material in record.get("materials") or []:
            mine = resolved(material, record_image)
            theirs = resolved(glb_by_name.get(material["name"]) or {}, glb_image)
            check(
                f"record v2: the GLB's {material['name']} is the record's",
                mine == theirs,
                (mine, theirs),
            )

    # --- lightmaps v3 (2026-11-07): the bake holds an object's occlusion, so a
    # material only lightmapped objects wear states none; one lightmapped and
    # unbaked objects share keeps it, said once with the fix -- decided against
    # the lightmaps record the write ships, by mesh, never the baker's markers
    # (mirror of mayatk's; ptk.MaterialFamilies.export_record). A Principled
    # BSDF has no occlusion socket, so the manifest is handed one. ----------
    from unittest import mock as _mock
    from blendertk.light_utils.lightmap_baker.lightmap_records import LightmapRecords
    from blendertk.mat_utils.mat_manifest import MatManifest

    for leftover in [o for o in bpy.data.objects if LightmapRecords.LIGHTMAP_INFO_PROP in o]:
        del leftover[LightmapRecords.LIGHTMAP_INFO_PROP]
    wall_mat, shared_mat = fresh("BakedWall"), fresh("BakedShared")
    wall = worn(wall_mat, "BakedWallCube")
    shared_baked = worn(shared_mat, "BakedSharedCube")
    shared_unbaked = worn(shared_mat, "UnbakedSharedCube")
    # A linked duplicate of the wall, unbaked: its mesh is lightmapped
    # through the wall, so the wall's material still ships no occlusion.
    twin = wall.copy()
    twin.name = "BakedWallTwin"
    bpy.context.scene.collection.objects.link(twin)

    def baked(obj, slot_name="Lightmap"):
        if obj.data.uv_layers.get(slot_name) is None:
            obj.data.uv_layers.new(name=slot_name)
        LightmapRecords._write_marker(
            obj, {"map": f"{obj.name}_Lightmap.exr", "uv_set": slot_name}
        )

    for baked_obj in (wall, shared_baked):
        baked(baked_obj)
    check(
        "each mesh names every object it is worn under, and what it wears",
        sorted(
            (sorted(map(tuple, paths)), sorted(mats))
            for paths, mats in MaterialRecords._worn([wall, twin])
        )
        == [([("BakedWallCube",), ("BakedWallTwin",)], ["BakedWall"])],
        MaterialRecords._worn([wall, twin]),
    )
    check(
        "the baker is named by no row (the record the write ships is read)",
        not hasattr(MaterialRecords, "LIGHTMAPPED")
        and ptk.SceneRecords.MATERIALS.after == ("lightmaps",),
    )
    real_maps = MatManifest._process_material

    def with_occlusion(material):
        found = dict(real_maps(material))
        found["ambientOcclusion"] = os.path.join(folder, f"{material.name}_AO.png")
        return found

    def shipped_materials(ctx):
        with _mock.patch.object(MatManifest, "_process_material", side_effect=with_occlusion):
            snapshot = ptk.ExportSnapshot.assemble(
                {
                    ptk.SceneRecords.LIGHTMAPS: LightmapRecords.export_record,
                    ptk.SceneRecords.MATERIALS: MaterialRecords.export_record,
                },
                ctx,
            )
        record = snapshot.record(ptk.SceneRecords.MATERIALS) or {}
        return {m["name"]: m for m in record.get("materials") or []}

    names = (wall.name, twin.name, shared_baked.name, shared_unbaked.name)
    ao_ctx = ptk.ExportContext(scope=names)
    ao_materials = shipped_materials(ao_ctx)
    check(
        "a material only lightmapped objects wear ships no occlusion",
        "occlusionTexture" not in ao_materials["BakedWall"],
        ao_materials["BakedWall"],
    )
    check(
        "a material unbaked objects share keeps it, said once with the fix",
        "occlusionTexture" in ao_materials["BakedShared"]
        and len([n for n in ao_ctx.notes if "BakedShared" in n]) == 1
        and any("their own material" in n for n in ao_ctx.notes),
        (ao_materials["BakedShared"], ao_ctx.notes),
    )
    # A refused lightmaps record ships no lightmap: every occlusion stays.
    first = wall.data.uv_layers[0].name
    baked(wall, first)
    refused_ctx = ptk.ExportContext(scope=names)
    refused = shipped_materials(refused_ctx)
    check(
        "a refused lightmaps record leaves every occlusion",
        "occlusionTexture" in refused["BakedWall"]
        and "occlusionTexture" in refused["BakedShared"]
        and any("Lightmaps not published" in n for n in refused_ctx.notes)
        and not any("their own material" in n for n in refused_ctx.notes),
        refused_ctx.notes,
    )
    for baked_obj in (wall, shared_baked):
        del baked_obj[LightmapRecords.LIGHTMAP_INFO_PROP]
    bpy.data.objects.remove(twin)

    # The one reader stands alone: every family read and a bracketed write
    # published, and the retired SceneState -- which delegates to this reader
    # until its removal -- was never loaded on the way.
    check(
        "reading and publishing the record never loads the retired SceneState",
        "blendertk.env_utils.scene_state" not in sys.modules,
    )

except Exception:
    traceback.print_exc()
    lines.append("FAIL unhandled exception")
finally:
    if store is not None:
        store.cleanup()

print("\n".join(lines))
ok = all(line.startswith("OK") for line in lines) and lines
print(
    f"===RESULT: {'PASS' if ok else 'FAIL'}=== "
    f"({sum(1 for line in lines if line.startswith('OK'))}/{len(lines)})"
)
