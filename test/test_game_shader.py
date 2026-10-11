"""blendertk Game Shader headless test: the masked (alpha cutout) build, twin of
mayatk's StingrayPBS masked graph.
Run: blender --background --factory-startup --python blendertk/test/test_game_shader.py
"""

import os
import shutil
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


TMP = os.path.join(HERE, "temp_tests", "game_shader_masked")
try:
    import bpy
    from blendertk.mat_utils._mat_utils import MatUtils

    shutil.rmtree(TMP, ignore_errors=True)
    os.makedirs(TMP)

    def write_png(name, rgba_at):
        """8x8 PNG via bpy (no Pillow in Blender's Python)."""
        path = os.path.join(TMP, name)
        img = bpy.data.images.new(name, 8, 8, alpha=True)
        px = []
        for y in range(8):
            for x in range(8):
                px.extend(rgba_at(x, y))
        img.pixels = px
        img.filepath_raw = path
        img.file_format = "PNG"
        img.save()
        bpy.data.images.remove(img)
        return path

    base = write_png("model_Base_Color.png", lambda x, y: (0.8, 0.2, 0.2, 1.0))
    # Grayscale opacity: one dark texel, the rest opaque.
    opacity = write_png(
        "model_Opacity.png",
        lambda x, y: (0.1, 0.1, 0.1, 1.0) if (x, y) == (0, 0) else (1.0, 1.0, 1.0, 1.0),
    )

    def principled(mat):
        return next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")

    def alpha_link(mat):
        links = principled(mat).inputs["Alpha"].links
        return links[0].from_node if links else None

    # --- masked: alpha thresholded to 0/1 in the graph -------------------------
    masked = MatUtils.create_pbr_material(
        [base, opacity], name="gs_masked", config={"opacity_mode": "masked"}
    )
    src = alpha_link(masked)
    check("masked: Alpha is driven", src is not None)
    check(
        "masked: Alpha comes through a GREATER_THAN math node",
        src is not None and src.type == "MATH" and src.operation == "GREATER_THAN",
        f"type={getattr(src, 'type', None)} op={getattr(src, 'operation', None)}",
    )
    check(
        "masked: the threshold node is labelled and defaults to 0.5",
        src is not None
        and src.label == "Mask Threshold"
        and abs(src.inputs[1].default_value - 0.5) < 1e-6,
    )
    upstream = src.inputs[0].links[0].from_node if src and src.inputs[0].links else None
    check(
        "masked: the opacity image feeds the threshold",
        upstream is not None
        and upstream.type == "TEX_IMAGE"
        and os.path.basename(upstream.image.filepath) == "model_Opacity.png",
        f"upstream={getattr(upstream, 'type', None)}",
    )
    # Blender < 4.2 keeps CLIP as its own mode; 4.2+ folds CLIP and HASHED into
    # the dithered surface method (both render in the opaque pass -- the cutout
    # semantics), and reads the legacy enum back as HASHED.
    if hasattr(masked, "surface_render_method"):
        check(
            "masked: dithered surface method (opaque pass), never blended",
            masked.surface_render_method == "DITHERED",
            masked.surface_render_method,
        )
    elif hasattr(masked, "blend_method"):
        check(
            "masked: blend_method is CLIP (or its 4.2+ alias HASHED)",
            masked.blend_method in ("CLIP", "HASHED"),
            masked.blend_method,
        )

    # --- auto (default): alpha straight from the image, blended ----------------
    auto = MatUtils.create_pbr_material([base, opacity], name="gs_auto")
    src = alpha_link(auto)
    check(
        "auto: Alpha comes straight from the opacity image",
        src is not None and src.type == "TEX_IMAGE",
        f"type={getattr(src, 'type', None)}",
    )

    # --- none: opacity ruled out; the alpha stays unwired ----------------------
    # An assertion, not an absence: the set carries a usable alpha and the
    # caller is asking for the solid material anyway (a cutout the target
    # engine masks with its own material, a decal sheet reused as a body).
    ignored = MatUtils.create_pbr_material(
        [base, opacity], name="gs_none", config={"opacity_mode": "none"}
    )
    check(
        "none: Alpha is left unwired even though the set carries one",
        alpha_link(ignored) is None,
        f"driver={getattr(alpha_link(ignored), 'type', None)}",
    )
    check(
        "none: the base colour is still wired",
        bool(principled(ignored).inputs["Base Color"].links),
    )
    check(
        "none: no image node was loaded for the opacity map",
        not any(
            n.type == "TEX_IMAGE"
            and os.path.basename(n.image.filepath) == "model_Opacity.png"
            for n in ignored.node_tree.nodes
        ),
    )

    from blendertk.mat_utils.game_shader import GameShader as _GS

    check(
        "none: the report names the reason instead of blaming another map",
        _GS._shadowed_by("Opacity", {"Base_Color": base}, {"opacity_mode": "none"})
        == "opacity ruled out (Opacity: None)",
        _GS._shadowed_by("Opacity", {"Base_Color": base}, {"opacity_mode": "none"}),
    )
    check(
        "auto: an unconnected map still reports the map that took its input",
        "already drives" in _GS._shadowed_by("Height", {"Normal": base}, None),
        _GS._shadowed_by("Height", {"Normal": base}, None),
    )

    # --- the panel path: opacity_mode must reach the build through create_network
    import blendertk.mat_utils._mat_utils as mat_utils
    from blendertk.mat_utils.game_shader import GameShader

    seen = []
    orig_create = mat_utils.MatUtils.create_pbr_material

    def spy_create(files, name=None, **kw):
        seen.append(kw.get("config") or {})
        return orig_create(files, name=name, **kw)

    mat_utils.MatUtils.create_pbr_material = spy_create
    try:
        built = GameShader().create_network(
            [base, opacity], name="gs_seam", opacity_mode="masked"
        )
    finally:
        mat_utils.MatUtils.create_pbr_material = orig_create
    check(
        "create_network hands opacity_mode to create_pbr_material",
        bool(seen) and seen[-1].get("opacity_mode") == "masked",
        f"config keys={sorted(seen[-1]) if seen else None}",
    )
    seam_mat = (
        built if hasattr(built, "node_tree") else bpy.data.materials.get("gs_seam")
    )
    src = alpha_link(seam_mat) if seam_mat else None
    check(
        "panel-path masked build thresholds the alpha",
        src is not None and src.type == "MATH" and src.operation == "GREATER_THAN",
        f"type={getattr(src, 'type', None)}",
    )

    # --- a UDIM set is ONE material whose images tile, not a material per tile ---
    # Blender tiles from a real tile file with its source set to TILED (measured,
    # 5.1: the path becomes <UDIM> and every sibling tile is found); a literal
    # <UDIM> path loads as a one-tile image.
    tile_files = [
        write_png(f"rock_Base_Color.{number}.png", lambda x, y: (0.6, 0.3, 0.3, 1.0))
        for number in (1001, 1002)
    ] + [
        write_png(f"rock_Roughness.{number}.png", lambda x, y: (0.5, 0.5, 0.5, 1.0))
        for number in (1001, 1002)
    ]
    built = GameShader().create_network(tile_files)
    made = [m for m in (built if isinstance(built, list) else [built]) if m]
    check(
        "udim: one material for the whole tile set",
        len(made) == 1 and made[0].name.startswith("rock"),
        f"{[m.name for m in made]}",
    )
    tiled_images = [
        n.image
        for m in made
        for n in m.node_tree.nodes
        if n.type == "TEX_IMAGE" and n.image
    ]
    check(
        "udim: every image node tiles from its tile set",
        bool(tiled_images)
        and all(
            i.source == "TILED" and sorted(t.number for t in i.tiles) == [1001, 1002]
            for i in tiled_images
        ),
        f"{[(i.name, i.source, [t.number for t in i.tiles]) for i in tiled_images]}",
    )

    # --- a NAMED build of a tile set: one asset, yet every tile converted -------
    # `BaseColor` sources, so the factory writes renamed `Base_Color` tiles: the
    # tiles an image can find are the ones it converted. It converted 1001 alone
    # for a named (one-asset) call before pythontk's prepare_maps split by tile.
    named_files = [
        write_png(f"slab_BaseColor.{number}.png", lambda x, y: (0.4, 0.4, 0.6, 1.0))
        for number in (1001, 1002)
    ]
    named = GameShader().create_network(named_files, name="slab_named")
    named_mat = (
        named if hasattr(named, "node_tree") else bpy.data.materials.get("slab_named")
    )
    named_images = [
        n.image
        for n in (named_mat.node_tree.nodes if named_mat else [])
        if n.type == "TEX_IMAGE" and n.image
    ]
    check(
        "udim named: its image tiles from every converted tile",
        bool(named_images)
        and all(
            "Base_Color" in i.filepath
            and i.source == "TILED"
            and sorted(t.number for t in i.tiles) == [1001, 1002]
            for i in named_images
        ),
        f"{[(i.filepath, i.source, [t.number for t in i.tiles]) for i in named_images]}",
    )

    # A LONE tile-numbered file is one image, not a set: tiling it would move it
    # off 0-1 UV space onto the tile its number names.
    lone = MatUtils.create_pbr_material(
        [write_png("lone_Base_Color.1024.png", lambda x, y: (0.3, 0.6, 0.3, 1.0))],
        name="gs_lone_tile",
    )
    lone_images = [
        n.image
        for n in (lone.node_tree.nodes if lone else [])
        if n.type == "TEX_IMAGE" and n.image
    ]
    check(
        "a lone tile-numbered map stays one image",
        bool(lone_images) and all(i.source == "FILE" for i in lone_images),
        f"{[(i.name, i.source) for i in lone_images]}",
    )

    # The panel's Assign to Selection step: one material -> assigned; several ->
    # skipped (the user merges them by setting a Material Name).
    from blendertk.mat_utils.game_shader import GameShaderSlots

    slots = GameShaderSlots.__new__(GameShaderSlots)  # no switchboard needed
    target = bpy.data.objects.new("gs_assign_target", bpy.data.meshes.new("gs_assign"))
    bpy.context.scene.collection.objects.link(target)
    mat_a = bpy.data.materials.new("gs_assign_a")
    mat_b = bpy.data.materials.new("gs_assign_b")
    slots._assign_to_selection([mat_a, mat_b], [target])
    check(
        "assign to selection skips several materials",
        len(target.data.materials) == 0,
        f"{[m.name for m in target.data.materials if m]}",
    )
    slots._assign_to_selection([mat_a], [target])
    check(
        "assign to selection assigns the one material",
        list(target.data.materials) == [mat_a],
        f"{[m.name for m in target.data.materials if m]}",
    )

    # --- OpenPBR lobes: Principled IS the OpenPBR model -------------------------
    # Twin of mayatk's openPBRSurface lobe wiring, from the same pythontk table.
    # Before it, coat/sheen/transmission maps classified as base maps (a coat
    # roughness WAS the roughness) or landed in "no matching Principled input".
    import pythontk as ptk

    grey = lambda x, y: (0.4, 0.4, 0.4, 1.0)  # noqa: E731
    lobe_files = [
        write_png(f"lobe_{t}.png", grey)
        for t in (
            "Base_Color",
            "Roughness",
            "Coat_Roughness",
            "Coat_Normal",
            "Sheen_Color",
            "Transmission",
            "Specular",
            "Specular_Level",
            "Thin_Film_Thickness",
            "Anisotropy_Angle",
        )
    ]
    lobe_plan = MatUtils.resolve_pbr_plan(lobe_files)
    lobe_mat = MatUtils.create_pbr_material(lobe_files, name="gs_lobes", plan=lobe_plan)
    lobe_bsdf = principled(lobe_mat)

    def source_type(input_name):
        """Map type of the image feeding *input_name*, through converter nodes."""
        sock = lobe_bsdf.inputs.get(input_name)
        frontier = [link.from_node for link in sock.links] if sock else []
        seen = set()
        while frontier:
            node = frontier.pop()
            if node.type == "TEX_IMAGE":
                return ptk.MapFactory.resolve_map_type(
                    os.path.basename(node.image.filepath)
                )
            if node.name in seen:
                continue
            seen.add(node.name)
            for inp in node.inputs:
                frontier.extend(link.from_node for link in inp.links)
        return None

    for input_name, expected in (
        ("Roughness", "Roughness"),
        ("Coat Roughness", "Clearcoat_Roughness"),
        ("Coat Normal", "Clearcoat_Normal"),
        ("Sheen Tint", "Sheen_Color"),
        ("Transmission Weight", "Transmission"),
        ("Specular IOR Level", "Specular_Level"),
        ("Thin Film Thickness", "Thin_Film_Thickness"),
        ("Anisotropic Rotation", "Anisotropy_Angle"),
    ):
        got = source_type(input_name)
        check(f"lobe: {input_name} <- {expected}", got == expected, got)
    check(
        "lobe: a coat / sheen parameter opens its weight",
        lobe_bsdf.inputs["Coat Weight"].default_value == 1.0
        and lobe_bsdf.inputs["Sheen Weight"].default_value == 1.0,
        (
            lobe_bsdf.inputs["Coat Weight"].default_value,
            lobe_bsdf.inputs["Sheen Weight"].default_value,
        ),
    )
    check(
        "lobe: a specular level outranks the loose Specular, which loads nothing",
        "Specular" not in lobe_plan["wired"] and "Specular_Level" in lobe_plan["wired"],
        sorted(lobe_plan["wired"]),
    )
    check(
        "lobe: the loose Specular names what displaced it",
        GameShaderSlots._shadowed_by("Specular", lobe_plan["by_type"])
        == "Specular_Level already drives that input",
        GameShaderSlots._shadowed_by("Specular", lobe_plan["by_type"]),
    )
    check(
        "lobe: no lobe map is reported as having no Principled input",
        not lobe_plan["unhandled"],
        lobe_plan["unhandled"],
    )
    film = lobe_bsdf.inputs["Thin Film Thickness"].links[0].from_node
    check(
        "lobe: thin film micrometres reach Principled as nanometres",
        film.type == "MATH" and film.inputs[1].default_value == 1000.0,
        (film.type, film.inputs[1].default_value),
    )

    # --- A set named for a lobe word (``Hero_Coat``) wires BASE maps ------------
    # Read file by file `Hero_Coat_Roughness` is a coat roughness of a set `Hero`;
    # read with its set (`ptk.MapFactory.resolve_map_types`) it is the garment's
    # own roughness, and the material keeps the set's whole name.
    coat_files = [
        write_png(f"Hero_Coat_{t}.png", grey)
        for t in ("BaseColor", "Roughness", "Normal", "Color")
    ]
    coat_plan = MatUtils.resolve_pbr_plan(coat_files)
    check(
        "lobe-named set: its maps are base maps",
        sorted(coat_plan["by_type"]) == ["Base_Color", "Normal", "Roughness"],
        sorted(coat_plan["by_type"]),
    )
    coat_mat = MatUtils.create_pbr_material(coat_files, plan=coat_plan)
    check(
        "lobe-named set: the material keeps the set's whole name",
        coat_mat is not None and coat_mat.name == "Hero_Coat",
        getattr(coat_mat, "name", None),
    )
    coat_bsdf = principled(coat_mat)
    check(
        "lobe-named set: no coat input is driven",
        not coat_bsdf.inputs["Coat Roughness"].is_linked
        and not coat_bsdf.inputs["Coat Normal"].is_linked,
    )

except Exception as e:
    traceback.print_exc()
    check("game shader masked build raised", False, repr(e))
finally:
    shutil.rmtree(TMP, ignore_errors=True)

passed = sum(1 for line in lines if line.startswith("OK"))
for line in lines:
    print(line)
result = "PASS" if all(line.startswith("OK") for line in lines) else "FAIL"
print(f"===RESULT: {result}=== ({passed}/{len(lines)})")
