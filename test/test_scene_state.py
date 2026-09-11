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
        f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}"
    )


try:
    import bpy
    from blendertk.env_utils.scene_state import SceneState

    def fresh(name):
        mat = bpy.data.materials.new(name)
        mat.use_nodes = True
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

except Exception:
    traceback.print_exc()
    lines.append("FAIL unhandled exception")

print("\n".join(lines))
ok = all(line.startswith("OK") for line in lines) and lines
print(
    f"===RESULT: {'PASS' if ok else 'FAIL'}=== "
    f"({sum(1 for line in lines if line.startswith('OK'))}/{len(lines)})"
)
