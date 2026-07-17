"""LIVE end-to-end for ``btk.import_maya_scene`` — requires a local Maya install.

NOT part of the automated suite (Run-Tests.ps1 picks ``test_*.py`` / ``*_slot_check.py``
only): the run checks out a Maya license and takes minutes. Run manually when touching
the pull bridge:

    blender --background --factory-startup --python blendertk/test/scene_import_live_e2e.py

Exercises the full production path: AppSpec maya.exe discovery -> mayapy derivation ->
template render -> ``pythontk.run_script_to_artifact`` (fresh mayapy: open scene,
FBXExport) -> ``FbxUtils.import_fbx`` -> temp-payload cleanup. Source-scene generation
itself dogfoods the runner (mayapy is known to fatal-error in teardown after a
successful save; judged-by-artifact absorbs it).

The generated scene is deliberately parser/FBX-hostile — each object pins one
fidelity trap the conversion must survive:

* ``e2e_cube``   — LIVE construction history (un-deleted polyExtrudeFacet) + a phong
  with an ABSOLUTE-path file texture. History must arrive *evaluated* (16 verts);
  the classic-model texture must ride the FBX.
* ``e2e_sphere`` — ``standardSurface`` (Maya 2020+ default shader family) with a file
  texture on baseColor. FBX only carries the classic Lambert/Phong material model,
  so the template must translate modern surface shaders before export or the
  texture silently drops.
* ``e2e_cone``   — a phong whose file texture uses a PROJECT-RELATIVE path
  (``sourceimages/...``). Pins project-path resolution: the template opens the
  scene's Maya project (nearest workspace.mel above it) before converting, so
  relative texture paths resolve headless (a live production scene showed
  Maya's bare fallbacks are NOT sufficient in general — pink materials).
* ``e2e_shared`` — a second object fed by the SAME standardSurface through a
  second shading group. Pins one-phong-per-material memoization (per-SG
  translation exploded into "_fbxsafe1..N" duplicates in production).
* ``e2e_stingray`` — StingrayPBS (ShaderFX game shader, the GameShader tool's
  output) with a color map. FBX writes these as a ``Maya|TEX_*`` custom-property
  set Blender's importer ignores ("material link ... ignored" — the live user
  report), so the template must translate them like the standardSurface family.
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

TEMP = os.path.join(HERE, "temp_tests")

lines = []


def check(name, cond, detail=""):
    lines.append(f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}")


# Generation script run under mayapy. {proj} / {abs_tex} substituted via .format.
_GEN_SCENE = '''
import os, struct, sys, zlib
import maya.standalone
maya.standalone.initialize(name="python")
import maya.cmds as cmds

PROJ = r"{proj}"
ABS_TEX = r"{abs_tex}"


def png(path, rgb, size=8):
    """Minimal valid RGB PNG, stdlib only (mayapy has no PIL)."""
    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))
    raw = (b"\\x00" + bytes(rgb) * size) * size
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    with open(path, "wb") as f:
        f.write(b"\\x89PNG\\r\\n\\x1a\\n" + chunk(b"IHDR", ihdr)
                + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def assign(objs, mat):
    sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=mat + "SG")
    cmds.connectAttr(mat + ".outColor", sg + ".surfaceShader", force=True)
    cmds.sets(objs, edit=True, forceElement=sg)


def file_tex(path):
    node = cmds.shadingNode("file", asTexture=True, isColorManaged=True)
    cmds.setAttr(node + ".fileTextureName", path, type="string")
    return node


# Project layout: workspace.mel + scenes/ + sourceimages/ (relative-path trap).
for sub in ("scenes", "sourceimages"):
    os.makedirs(os.path.join(PROJ, sub), exist_ok=True)
with open(os.path.join(PROJ, "workspace.mel"), "w") as f:
    f.write('workspace -fr "sourceimages" "sourceimages";\\n')
png(ABS_TEX, (255, 0, 0))
png(os.path.join(PROJ, "sourceimages", "e2e_rel.png"), (0, 0, 255))
cmds.workspace(PROJ, openWorkspace=True)

# cube: LIVE history + phong with ABSOLUTE-path texture
cube, _ = cmds.polyCube(width=2, height=1, depth=1, subdivisionsX=2, name="e2e_cube")
cmds.polyExtrudeFacet(cube + ".f[0]", localTranslateZ=0.5)  # keep history LIVE
phong_abs = cmds.shadingNode("phong", asShader=True, name="e2e_phong_abs")
cmds.connectAttr(file_tex(ABS_TEX) + ".outColor", phong_abs + ".color", force=True)
assign([cube], phong_abs)

# sphere: standardSurface with a texture on baseColor (modern-shader trap)
sphere = cmds.polySphere(radius=0.5, name="e2e_sphere")[0]
cmds.move(3, 0, 0, sphere)
ss = cmds.shadingNode("standardSurface", asShader=True, name="e2e_ss")
cmds.connectAttr(file_tex(ABS_TEX) + ".outColor", ss + ".baseColor", force=True)
assign([sphere], ss)

# Shared-material trap: production scenes routinely feed ONE material into MANY
# shading groups (per-object SG splits, merged imports). The translation must
# emit ONE phong per source material, not one per SG ("_fbxsafe1..13" explosion).
shared = cmds.polySphere(radius=0.5, name="e2e_shared")[0]
cmds.move(3, 0, 3, shared)
sg2 = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name="e2e_ssSG2")
cmds.connectAttr(ss + ".outColor", sg2 + ".surfaceShader", force=True)
cmds.sets(shared, edit=True, forceElement=sg2)

# cone: phong with a PROJECT-RELATIVE texture path (workspace trap)
cone = cmds.polyCone(radius=0.5, name="e2e_cone")[0]
cmds.move(-3, 0, 0, cone)
phong_rel = cmds.shadingNode("phong", asShader=True, name="e2e_phong_rel")
cmds.connectAttr(
    file_tex("sourceimages/e2e_rel.png") + ".outColor", phong_rel + ".color", force=True
)
assign([cone], phong_rel)

# cylinder: StingrayPBS (ShaderFX game shader) with a color map -- FBX writes it
# as a Maya|TEX_* custom-property set Blender ignores ("material link ... ignored").
cmds.loadPlugin("shaderFXPlugin", quiet=True)
cyl = cmds.polyCylinder(radius=0.5, name="e2e_stingray")[0]
cmds.move(0, 0, 3, cyl)
sr = cmds.shadingNode("StingrayPBS", asShader=True, name="e2e_sr")
if not cmds.attributeQuery("TEX_color_map", node=sr, exists=True):
    # Batch creation may skip the default-graph init the UI performs.
    try:
        cmds.shaderfx(sfxnode=sr, initShaderAttributes=True)
    except Exception:
        graph = os.path.join(os.environ["MAYA_LOCATION"], "presets", "ShaderFX",
                             "Scene", "StingrayPBS.sfx")
        cmds.shaderfx(sfxnode=sr, loadGraph=graph)
# Conventionally named set (the shared ptk.MapFactory taxonomy -- what GameShader
# itself consumes), so the Blender-side manifest rebuild can classify by filename.
def sr_tex(stem, rgb):
    path = os.path.join(PROJ, "sourceimages", "e2e_stingray_" + stem + ".png")
    png(path, rgb)
    return path

cmds.connectAttr(file_tex(sr_tex("BaseColor", (255, 255, 0))) + ".outColor",
                 sr + ".TEX_color_map", force=True)
cmds.setAttr(sr + ".use_color_map", 1)
cmds.connectAttr(file_tex(sr_tex("Normal_OpenGL", (128, 128, 255))) + ".outColor",
                 sr + ".TEX_normal_map", force=True)
cmds.setAttr(sr + ".use_normal_map", 1)
# Packed Unity metallic-smoothness -- NO slot in FBX's classic model; can only
# arrive via the manifest sidecar + Blender-side create_pbr_material rebuild.
cmds.connectAttr(file_tex(sr_tex("Metallic_Smoothness", (128, 128, 128))) + ".outColor",
                 sr + ".TEX_metallic_map", force=True)
cmds.setAttr(sr + ".use_metallic_map", 1)
assign([cyl], sr)
# Multi-material trap: half the faces keep the plain phong -- the Blender-side
# manifest rebuild must swap ONLY the Stingray slot, never clobber the mesh.
cmds.sets(cyl + ".f[0:9]", edit=True, forceElement="e2e_phong_absSG")

cmds.file(rename=os.path.join(PROJ, "scenes", "e2e_scene.ma").replace("\\\\", "/"))
cmds.file(save=True, type="mayaAscii", force=True)
sys.stdout.flush()
os._exit(0)  # skip standalone teardown (known crasher); artifact is the ground truth
'''


def _img_ok(img):
    """A usable image: packed (FBX-embedded), loaded, or pointing at a real file.
    (``has_data`` stays False for disk images Blender hasn't sampled yet — the
    manifest-rebuilt nodes — so file existence is part of the check.)"""
    import bpy

    if img.packed_file or img.has_data:
        return True
    try:
        return bool(img.filepath) and os.path.exists(bpy.path.abspath(img.filepath))
    except Exception:
        return False


def usable_images(obj):
    """Count usable image textures across *obj*'s materials."""
    count = 0
    for slot in obj.material_slots:
        mat = slot.material
        if not mat or not mat.node_tree:
            continue
        for node in mat.node_tree.nodes:
            img = getattr(node, "image", None)
            if node.type == "TEX_IMAGE" and img and _img_ok(img):
                count += 1
    return count


def textured(obj):
    """True if any material on *obj* carries a usable image texture."""
    return usable_images(obj) > 0


def mat_metallic_linked(mat):
    """True if *mat* drives a Principled BSDF's Metallic input."""
    if not mat or not mat.node_tree:
        return False
    return any(
        node.type == "BSDF_PRINCIPLED" and node.inputs["Metallic"].is_linked
        for node in mat.node_tree.nodes
    )


def metallic_linked(obj):
    """True if any material on *obj* drives a Principled BSDF's Metallic input."""
    return any(mat_metallic_linked(slot.material) for slot in obj.material_slots)


try:
    import bpy
    import pythontk as ptk
    import blendertk as btk

    os.makedirs(TEMP, exist_ok=True)
    proj = os.path.join(TEMP, "e2e_proj")
    abs_tex = os.path.join(TEMP, "e2e_abs.png")
    src = os.path.join(proj, "scenes", "e2e_scene.ma")

    mayapy = btk.MayaSceneImport().require_mayapy()  # raises if no Maya install
    ptk.run_script_to_artifact(
        mayapy, _GEN_SCENE.format(proj=proj, abs_tex=abs_tex),
        artifact=src, timeout=600,
    )
    check("textured project scene generated", os.path.getsize(src) > 1000)

    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()

    import time
    t0 = time.time()
    imported = btk.import_maya_scene(src, timeout=600)
    first_duration = time.time() - t0

    objs = {o.name: o for o in imported}
    names = sorted(objs)
    meshes = {o.name: len(o.data.vertices) for o in imported if o.type == "MESH"}
    check("all five meshes imported", len(meshes) == 5, f"{names}")

    # One source material through two shading groups must arrive as ONE
    # material, not per-SG "_fbxsafe" duplicates (live production report).
    ss_mats = [m.name for m in bpy.data.materials if "e2e_ss" in m.name]
    check("shared material translated once (no per-SG duplicates)",
          len(ss_mats) == 1, f"{sorted(ss_mats)}")
    cube_verts = next((v for n, v in meshes.items() if "cube" in n), None)
    # 2x1x1-subdiv cube = 12 verts; the LIVE extrude adds 4 -> Maya must have
    # evaluated the history chain during conversion (a .ma parser never could).
    check("live construction history evaluated (12+4 verts)", cube_verts == 16,
          f"{cube_verts}")

    for tag, why in (
        ("e2e_cube", "phong + absolute path (classic-model baseline)"),
        ("e2e_sphere", "standardSurface (needs FBX-safe translation)"),
        ("e2e_cone", "project-relative path (needs workspace resolution)"),
        ("e2e_stingray", "StingrayPBS (Maya|TEX_* property set Blender ignores)"),
    ):
        obj = next((o for n, o in objs.items() if tag in n), None)
        check(f"{tag} texture arrived — {why}",
              obj is not None and textured(obj))

    stingray = next((o for n, o in objs.items() if "e2e_stingray" in n), None)
    check("e2e_stingray normal map arrived (manifest rebuild)",
          stingray is not None and usable_images(stingray) >= 2,
          f"{usable_images(stingray) if stingray else 0} image(s)")
    # The packed map can ONLY arrive via the manifest + create_pbr_material path:
    # a linked Principled Metallic input proves the packed chain was wired.
    check("e2e_stingray packed Metallic_Smoothness wired — Principled Metallic linked",
          stingray is not None and metallic_linked(stingray)
          and usable_images(stingray) >= 3,
          f"{usable_images(stingray) if stingray else 0} image(s)")
    # Multi-material integrity: the rebuild must swap only the Stingray SLOT --
    # the phong half of the mesh keeps its material and texture.
    slot_mats = ([s.material for s in stingray.material_slots if s.material]
                 if stingray else [])
    phong_side = [m for m in slot_mats if not mat_metallic_linked(m)]
    check("e2e_stingray multi-material: phong slot survived the slot-swap",
          len(slot_mats) == 2 and len(phong_side) == 1
          and any(getattr(n, "image", None) and _img_ok(n.image)
                  for n in phong_side[0].node_tree.nodes),
          f"{len(slot_mats)} slot(s)")

    # Conversion cache: an identical second import must skip the Maya launch
    # (its cost is Blender's FBX import only).
    t0 = time.time()
    imported2 = btk.import_maya_scene(src, timeout=600)
    second_duration = time.time() - t0
    check("conversion cache: second import skips the Maya launch",
          len(imported2) == len(imported) and second_duration < first_duration * 0.5,
          f"{first_duration:.1f}s -> {second_duration:.1f}s")

    # Cached payloads persist BY DESIGN (detached policy, stale-swept);
    # anything else with the scratch prefix is a leak.
    leftovers = [
        f for f in os.listdir(os.environ.get("TEMP", "/tmp"))
        if f.startswith("maya_to_btk_") and not f.startswith("maya_to_btk_cache_")
    ]
    check("no temp payload leftovers", not leftovers, f"{leftovers}")

except Exception as e:
    lines.append(f"FAIL setup: {e!r}")
    lines.append(traceback.format_exc())
finally:
    shutil.rmtree(TEMP, ignore_errors=True)
    import glob as _glob
    for cached in _glob.glob(
        os.path.join(os.environ.get("TEMP", "/tmp"), "maya_to_btk_cache_*")
    ):
        try:
            os.remove(cached)
        except OSError:
            pass

ok = all(line.startswith("OK") for line in lines)
for line in lines:
    print(line)
print(f"===RESULT: {'PASS' if ok else 'FAIL'}===")
