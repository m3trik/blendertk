"""blendertk.env_utils.scene_state — the sidecar readers, headless test.
Run: blender --background --factory-startup --python blendertk/test/test_scene_state.py

Mirror of mayatk's ``test_scene_state.py``, scoped to the section the Blender side was
missing: ``alpha_mode``. The FBX cannot say which glTF ``alphaMode`` a material wants and
FBX2glTF decides from the base colour's alpha alone, so a transparent material reached WebXR
OPAQUE — its silhouette embedded, rendering as a solid square. Both of this package's own
producers ship down that route: ``MatUtils`` thresholds the Principled ``Alpha`` in the graph
for a cutout, the shadow rigs mix a Transparent BSDF into the surface output.

The boundary is the point. Blender's defaults are not neutral — a brand-new material already
reads ``blend_method='HASHED'`` / ``surface_render_method='DITHERED'`` — so a reader keying off
"not OPAQUE" would mark every material in the scene transparent.
"""

import sys
import os
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
    from blendertk.env_utils.scene_state import SceneState
    from blendertk.mat_utils._mat_utils import MatUtils

    def fresh(name):
        mat = bpy.data.materials.new(name)
        MatUtils.get_node_tree(mat, create=True)
        return mat

    def principled(mat):
        return next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")

    def mode_of(mat):
        return SceneState._read_alpha_mode([mat], {}).get(mat.name)

    check(
        "alpha_mode is a declared section",
        SceneState.READERS.get("alpha_mode") == "_read_alpha_mode",
    )

    # --- the boundary: an untouched material must stay OPAQUE ---------------
    plain = fresh("Plain")
    check(
        "a default material is omitted (HASHED/DITHERED are defaults, not intent)",
        mode_of(plain) is None,
        f"blend={plain.blend_method} render={getattr(plain, 'surface_render_method', '-')}",
    )

    # --- cutout -------------------------------------------------------------
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
    cut = fresh("Cutout")
    nt = cut.node_tree
    tex = nt.nodes.new("ShaderNodeTexNoise")
    gate = nt.nodes.new("ShaderNodeMath")
    gate.operation = "GREATER_THAN"
    gate.inputs[1].default_value = 0.25
    gate.label = "Mask Threshold"
    nt.links.new(gate.inputs[0], tex.outputs["Fac"])
    nt.links.new(principled(cut).inputs["Alpha"], gate.outputs["Value"])
    entry = mode_of(cut)
    check(
        "a threshold node reads as MASK",
        (entry or {}).get("mode") == "MASK",
        str(entry),
    )
    check(
        "MASK carries the node's threshold as the cutoff",
        (entry or {}).get("cutoff") == 0.25,
        str(entry),
    )

    # An UNLABELLED math node is somebody's own graph, not a cutout marker.
    plain_math = fresh("PlainMath")
    nt = plain_math.node_tree
    tex = nt.nodes.new("ShaderNodeTexNoise")
    gate = nt.nodes.new("ShaderNodeMath")
    gate.operation = "GREATER_THAN"
    nt.links.new(gate.inputs[0], tex.outputs["Fac"])
    nt.links.new(principled(plain_math).inputs["Alpha"], gate.outputs["Value"])
    check(
        "an unlabelled threshold is BLEND, not MASK (a linked alpha, nothing more)",
        (mode_of(plain_math) or {}).get("mode") == "BLEND",
        str(mode_of(plain_math)),
    )

    # --- blend, by each independent signal -----------------------------------
    legacy = fresh("LegacyBlend")
    legacy.blend_method = "BLEND"
    check(
        "blend_method BLEND reads as BLEND",
        (mode_of(legacy) or {}).get("mode") == "BLEND",
    )

    nxt = fresh("EeveeNextBlend")
    nxt.surface_render_method = "BLENDED"
    check(
        "surface_render_method BLENDED reads as BLEND",
        (mode_of(nxt) or {}).get("mode") == "BLEND",
    )

    dialled = fresh("AlphaDialled")
    principled(dialled).inputs["Alpha"].default_value = 0.4
    check(
        "a Principled Alpha below 1 reads as BLEND",
        (mode_of(dialled) or {}).get("mode") == "BLEND",
    )

    linked = fresh("AlphaLinked")
    nt = linked.node_tree
    tex = nt.nodes.new("ShaderNodeTexNoise")
    nt.links.new(principled(linked).inputs["Alpha"], tex.outputs["Fac"])
    check(
        "a LINKED Principled Alpha reads as BLEND (the value still says 1.0)",
        (mode_of(linked) or {}).get("mode") == "BLEND",
    )

    # --- the shadow-rig shape: no alpha socket at all, a mixed Transparent ---
    mixed = fresh("MixedTransparent")
    nt = mixed.node_tree
    mix = nt.nodes.new("ShaderNodeMixShader")
    transp = nt.nodes.new("ShaderNodeBsdfTransparent")
    out = next(n for n in nt.nodes if n.type == "OUTPUT_MATERIAL")
    nt.links.new(mix.inputs[1], transp.outputs[0])
    nt.links.new(mix.inputs[2], principled(mixed).outputs[0])
    nt.links.new(out.inputs["Surface"], mix.outputs[0])
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

    # --- the real producer's shape, end to end -------------------------------
    try:
        bpy.ops.mesh.primitive_plane_add()
        plane = bpy.context.active_object
        rig_mat = fresh("ShadowPlaneLike")
        nt = rig_mat.node_tree
        mix = nt.nodes.new("ShaderNodeMixShader")
        transp = nt.nodes.new("ShaderNodeBsdfTransparent")
        emis = nt.nodes.new("ShaderNodeEmission")
        out = next(n for n in nt.nodes if n.type == "OUTPUT_MATERIAL")
        nt.links.new(mix.inputs[1], transp.outputs[0])
        nt.links.new(mix.inputs[2], emis.outputs[0])
        nt.links.new(out.inputs["Surface"], mix.outputs[0])
        rig_mat.blend_method = "BLEND"
        plane.data.materials.append(rig_mat)
        built = SceneState._read_alpha_mode([rig_mat], {})
    except Exception as exc:  # noqa: BLE001
        built = f"error: {exc}"
    check(
        "the shadow-plane material shape reports BLEND to the sidecar",
        isinstance(built, dict)
        and built.get("ShadowPlaneLike", {}).get("mode") == "BLEND",
        str(built),
    )

    # --- unlit: Blender's own unlit model (mirror of mayatk's surfaceShader) --
    # FBX2glTF makes every material lit PBR; a shadow plane reached WebXR lit
    # and read BRIGHTER than its floor (Maya side, 2026-10-15).
    check(
        "unlit is a declared section",
        SceneState.READERS.get("unlit") == "_read_unlit",
    )

    def unlit_of(mat):
        return mat.name in SceneState._read_unlit([mat], {})

    emits = fresh("EmissionOnly")
    nt = emits.node_tree
    emis = nt.nodes.new("ShaderNodeEmission")
    out = next(n for n in nt.nodes if n.type == "OUTPUT_MATERIAL")
    nt.links.new(out.inputs["Surface"], emis.outputs[0])
    check("an Emission shader alone reads unlit", unlit_of(emits))
    check(
        "the shadow-plane shape (Emission mixed with Transparent) reads unlit",
        unlit_of(bpy.data.materials["ShadowPlaneLike"]),
    )
    check("a Principled BSDF is lit", not unlit_of(fresh("LitPlain")))
    check(
        "a Transparent BSDF mixed with a Principled is lit",
        not unlit_of(mixed),
    )
    glow = fresh("EmissionOverLit")
    nt = glow.node_tree
    add = nt.nodes.new("ShaderNodeAddShader")
    emis = nt.nodes.new("ShaderNodeEmission")
    out = next(n for n in nt.nodes if n.type == "OUTPUT_MATERIAL")
    nt.links.new(add.inputs[0], emis.outputs[0])
    nt.links.new(add.inputs[1], principled(glow).outputs[0])
    nt.links.new(out.inputs["Surface"], add.outputs[0])
    check("Emission added over a BSDF is lit", not unlit_of(glow))

    # --- base colour alpha: the scalar part of a material's opacity ---------
    # Mirror of mayatk's ``TestOpacityFactor``: glTF states it as
    # ``baseColorFactor[3]`` and the FBX drops it. A shadow plane held at a
    # static 0.836 fade reached WebXR at alpha 1.0 -- solid black where the
    # viewport drew it half-transparent (Maya side, 2026-10-15).
    def alpha_of(mat):
        return (SceneState._read_base_color([mat], {}).get(mat.name) or {}).get("alpha")

    def faded(nt, into, scalar=0.836):
        """Texture alpha x a Value node through a Math MULTIPLY, into *into*."""
        tex = nt.nodes.new("ShaderNodeTexImage")
        fade = nt.nodes.new("ShaderNodeValue")
        fade.outputs[0].default_value = scalar
        mult = nt.nodes.new("ShaderNodeMath")
        mult.operation = "MULTIPLY"
        nt.links.new(mult.inputs[0], tex.outputs["Alpha"])
        nt.links.new(mult.inputs[1], fade.outputs[0])
        nt.links.new(into, mult.outputs[0])
        return tex

    fade_mat = fresh("FadeMat")
    faded(fade_mat.node_tree, principled(fade_mat).inputs["Alpha"])
    check(
        "texture alpha x a fade carries the fade",
        abs((alpha_of(fade_mat) or 0.0) - 0.836) < 1e-5,
        str(alpha_of(fade_mat)),
    )
    swapped = fresh("FadeSwapped")
    nt = swapped.node_tree
    tex = nt.nodes.new("ShaderNodeTexImage")
    mult = nt.nodes.new("ShaderNodeMath")
    mult.operation = "MULTIPLY"
    mult.inputs[0].default_value = 0.25
    nt.links.new(mult.inputs[1], tex.outputs["Alpha"])
    nt.links.new(principled(swapped).inputs["Alpha"], mult.outputs[0])
    check(
        "...the scalar on either input, a bare constant too",
        abs((alpha_of(swapped) or 0.0) - 0.25) < 1e-5,
        str(alpha_of(swapped)),
    )
    tint = fresh("Tint")
    principled(tint).inputs["Alpha"].default_value = 0.5
    check(
        "a constant opacity carries itself",
        abs((alpha_of(tint) or 0.0) - 0.5) < 1e-5,
        str(alpha_of(tint)),
    )
    check(
        "...beside the constant colour",
        "color" in (SceneState._read_base_color([tint], {}).get("Tint") or {}),
    )
    bare = fresh("Bare")
    nt = bare.node_tree
    tex = nt.nodes.new("ShaderNodeTexImage")
    nt.links.new(principled(bare).inputs["Alpha"], tex.outputs["Alpha"])
    check("a texture alone states nothing", alpha_of(bare) is None)
    check("an opaque material states nothing", alpha_of(fresh("Solid")) is None)

    # The shadow rig's own shape: no Principled on the surface, the fade
    # reaching the Mix Shader's factor against its Transparent BSDF.
    rig_like = fresh("RigFade")
    nt = rig_like.node_tree
    mix = nt.nodes.new("ShaderNodeMixShader")
    transp = nt.nodes.new("ShaderNodeBsdfTransparent")
    emis = nt.nodes.new("ShaderNodeEmission")
    out = next(n for n in nt.nodes if n.type == "OUTPUT_MATERIAL")
    nt.links.new(mix.inputs[1], transp.outputs[0])
    nt.links.new(mix.inputs[2], emis.outputs[0])
    nt.links.new(out.inputs["Surface"], mix.outputs[0])
    faded(nt, mix.inputs["Fac"], scalar=0.6)
    principled(rig_like).inputs["Alpha"].default_value = 0.2  # unwired: not read
    check(
        "the shadow rig's fade reads through its Mix Shader factor",
        abs((alpha_of(rig_like) or 0.0) - 0.6) < 1e-5,
        str(alpha_of(rig_like)),
    )

    # A detached plane's shape: the active output mixes on the PNG's own
    # alpha (its fade already baked in), and an inactive second output holds
    # a Principled BSDF for the exporters. Neither states a factor.
    detached = fresh("Detached")
    nt = detached.node_tree
    nt.nodes.clear()
    tex = nt.nodes.new("ShaderNodeTexImage")
    emis = nt.nodes.new("ShaderNodeEmission")
    transp = nt.nodes.new("ShaderNodeBsdfTransparent")
    mix = nt.nodes.new("ShaderNodeMixShader")
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    nt.links.new(tex.outputs["Color"], emis.inputs["Color"])
    nt.links.new(tex.outputs["Alpha"], mix.inputs["Fac"])
    nt.links.new(transp.outputs[0], mix.inputs[1])
    nt.links.new(emis.outputs[0], mix.inputs[2])
    nt.links.new(mix.outputs[0], out.inputs["Surface"])
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Alpha"].default_value = 0.3
    export = nt.nodes.new("ShaderNodeOutputMaterial")
    nt.links.new(bsdf.outputs[0], export.inputs["Surface"])
    out.is_active_output = True
    export.is_active_output = False
    try:
        got = alpha_of(detached)
    except Exception as exc:  # noqa: BLE001 -- the check IS that nothing raises
        got = f"error: {exc}"
    check(
        "a detached plane states no factor (its inactive Principled unread)",
        got is None,
        str(got),
    )

    # --- emissive: a mapped emission switched off ships off ------------------
    # The FBX hands the map on and FBX2glTF glows it at 1.0, so a Strength-0
    # material omitted from the section reached WebXR lit (Maya's twin, measured
    # on a production room's ceiling panels, 2026-10-17).
    dark = fresh("emissive_off")
    principled(dark).inputs["Emission Strength"].default_value = 0.0
    entry = SceneState._read_emissive(
        [dark], {dark.name: {"emission": "C:/maps/panel_Emissive.png"}}
    ).get(dark.name)
    check(
        "a mapped emission at strength 0 ships off (factor 0, its map named)",
        entry == {"texture": "C:/maps/panel_Emissive.png", "color": [0.0, 0.0, 0.0]},
        str(entry),
    )

except Exception:
    traceback.print_exc()
    lines.append("FAIL unhandled exception")

print("\n".join(lines))
ok = all(line.startswith("OK") for line in lines) and lines
print(
    f"===RESULT: {'PASS' if ok else 'FAIL'}=== "
    f"({sum(1 for line in lines if line.startswith('OK'))}/{len(lines)})"
)
