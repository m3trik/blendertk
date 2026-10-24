"""blendertk.env_utils.scene_state — the sidecar readers, headless test.
Run: blender --background --factory-startup --python blendertk/test/test_scene_state.py

Retired 2026-10-20 (removed in 0.21.0): the materials record states every material
(``MaterialRecords``), so no export reads a section. The graph readers the sections shared --
the alpha mode, the unlit model, the opacity's scalar part -- moved into MaterialRecords, and
their checks with them (``test_material_records``). What stays pinned here is the retired
surface itself: reading a section warns, naming what replaced it, and every section still
answers through MaterialRecords' readers until the removal. The notice is asserted once and
quieted for the rest.
"""

import sys
import os
import traceback
import warnings

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

    # --- retired: reading a section warns, naming what replaced it ----------
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        SceneState.read([])
        SceneState.source()
        table = SceneState.READERS
    notices = [str(w.message) for w in caught if w.category is DeprecationWarning]
    check(
        "reading the sections warns, naming the materials record",
        any("MaterialRecords" in n for n in notices),
        notices,
    )
    check(
        "...the host's identity names the export context",
        any("export_context" in n for n in notices),
        notices,
    )
    check(
        "...and the section table still serves itself while it warns",
        table is SceneState._READERS,
    )
    warnings.filterwarnings(
        "ignore", message=r".*SceneState\.", category=DeprecationWarning
    )

    def fresh(name):
        mat = bpy.data.materials.new(name)
        MatUtils.get_node_tree(mat, create=True)
        return mat

    def principled(mat):
        return next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")

    check(
        "alpha_mode and unlit are still declared sections",
        SceneState.READERS.get("alpha_mode") == "_read_alpha_mode"
        and SceneState.READERS.get("unlit") == "_read_unlit",
    )

    # --- every section still answers, through MaterialRecords' readers -------
    # The shadow rigs' shape: an Emission mixed against a Transparent BSDF.
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
    check(
        "the shadow-plane material shape reports BLEND to the sidecar",
        built.get("ShadowPlaneLike", {}).get("mode") == "BLEND",
        str(built),
    )
    unlit = SceneState._read_unlit([rig_mat], {})
    check(
        "...and the unlit section names it, one empty entry per material",
        unlit == {"ShadowPlaneLike": {}},
        str(unlit),
    )
    sections = SceneState.read([plane])
    check(
        "read() still reads every section through the table",
        {"alpha_mode", "unlit"} <= set(sections),
        sorted(sections),
    )

    tint = fresh("Tint")
    principled(tint).inputs["Alpha"].default_value = 0.5
    entry = SceneState._read_base_color([tint], {}).get("Tint") or {}
    check(
        "a constant opacity rides as the base colour's alpha",
        abs((entry.get("alpha") or 0.0) - 0.5) < 1e-5,
        str(entry),
    )
    check("...beside the constant colour", "color" in entry, str(entry))

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
