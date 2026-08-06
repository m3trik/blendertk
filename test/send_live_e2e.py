"""LIVE end-to-end for the SEND direction (``btk.MayaBridge``) — requires a local Maya.

NOT part of the automated suite (Run-Tests.ps1 picks ``test_*.py`` / ``*_slot_check.py``
only): the run checks out Maya licenses and takes minutes. Run manually when touching
the push bridge:

    blender --background --factory-startup --python blendertk/test/send_live_e2e.py

Add ``-- --gui`` after the script path to also run the interactive-send leg (launches a
REAL GUI ``maya.exe`` with ``-log``, waits for the import template to finish, scans the
log for color-management errors, then terminates ONLY that Maya PID).

Pins the 2026-08-03 live production report (send arrived flat / wrong node types /
gray or mis-wired textures / OCIO error at Maya launch):

* **Hierarchy closure** — seeds of one nested mesh + one marked parent Empty must
  pull the ancestor chain and the subtree, and nothing else (no sibling widening).
* **Node types** — PLAIN_AXES parent Empties -> plain group transforms; childless
  Empties -> locators; a non-default display type (ARROWS) is an author marker and
  survives as a locator EVEN WITH children (was demoted to a group before the
  manifest's ``empties`` section).
* **Texture rebuild** — a map named after a product (``Agilent_E4419B.png``, no
  map-type token) must be rescued through the manifest's traced ``slots`` into
  baseColor with an sRGB color space; a classifiable ``*_Roughness`` map must wire
  to roughness as Raw.
* **OCIO hand-off** — Blender 5.x sets ``OCIO`` to its bundled v2.5 config at the
  C level AFTER Python init (invisible to ``os.environ``); the launched Maya (OCIO
  2.3) inherited it and failed color-management init on every send. The GUI leg
  asserts the launched Maya's log carries no color-management errors
  (``AppLauncher.process_environ`` + ``handoff_env`` strip).
"""

import json
import os
import shutil
import struct
import sys
import time
import traceback
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for p in (REPO, os.path.join(MONO, "pythontk")):
    if p not in sys.path:
        sys.path.insert(0, p)

TEMP = os.path.join(HERE, "temp_tests", "send_e2e")
GUI_LEG = "--gui" in sys.argv

lines = []


# What "the base-color slot" is CALLED per shader family. Hand-written rather
# than read from ShaderAttributeMap: a map that is wrong would otherwise agree
# with itself and the assertion would prove nothing.
_COLOR_SLOTS = ("baseColor", "TEX_color_map")


def _is_color_slot(dest_plug):
    """True when *dest_plug* is some shader's base-color input."""
    return any(slot in dest_plug for slot in _COLOR_SLOTS)


def check(name, cond, detail=""):
    lines.append(
        f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}"
    )


def png(path, rgb, size=8, alpha=False):
    """Minimal valid PNG, stdlib only; *alpha* writes RGBA (a real alpha channel)."""
    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))
    px = bytes(rgb) + (b"\x80" if alpha else b"")
    raw = (b"\x00" + px * size) * size
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6 if alpha else 2, 0, 0, 0)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
                + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


# Inspection script run under mayapy against the two saved .ma files. {out_dir}
# substituted via .format; writes JSON so the Blender side owns the asserts.
_INSPECT = '''
import json, os, sys
import maya.standalone
maya.standalone.initialize(name="python")
import maya.cmds as cmds

OUT_DIR = r"{out_dir}"


def node_kind(name):
    if not cmds.objExists(name):
        return None
    shapes = cmds.listRelatives(name, shapes=True, fullPath=True) or []
    kinds = sorted({{cmds.nodeType(s) for s in shapes}})
    return "+".join(kinds) if kinds else "group"


def parent_of(name):
    parents = cmds.listRelatives(name, parent=True) or []
    return parents[0] if parents else None


def shaders_of(mesh):
    shapes = cmds.listRelatives(mesh, shapes=True, fullPath=True) or [mesh]
    out = []
    for shape in shapes:
        for sg in cmds.listConnections(shape, type="shadingEngine") or []:
            for shader in cmds.listConnections(sg + ".surfaceShader", source=True) or []:
                if shader not in out:
                    out.append(shader)
    return out


def file_textures(mesh):
    out = {{}}
    for shader in shaders_of(mesh):
        for node in cmds.listHistory(shader) or []:
            if cmds.nodeType(node) != "file":
                continue
            dests = cmds.listConnections(
                node, destination=True, source=False, plugs=True) or []
            out[os.path.basename(cmds.getAttr(node + ".fileTextureName"))] = [
                cmds.getAttr(node + ".colorSpace"), dests]
    return out


def opacity_wiring(mesh):
    """Source plugs driving the shader's opacity -- parent, else its children.

    listConnections on a compound PARENT reports nothing when only the children
    are connected, so a float3 opacity must be read per child or the probe
    measures the query rather than the wiring.
    """
    out = []
    for shader in shaders_of(mesh):
        # StingrayPBS's slot is the scalar "opacity" (there is no
        # TEX_opacity_map on either ShaderFX graph -- probed live).
        for attr in ("opacity", "geometryOpacity", "transparency"):
            if not cmds.objExists(shader + "." + attr):
                continue
            direct = cmds.listConnections(
                shader + "." + attr, source=True, destination=False, plugs=True) or []
            if direct:
                out.extend(direct)
                continue
            for child in cmds.attributeQuery(attr, node=shader, listChildren=True) or []:
                out.extend(cmds.listConnections(
                    shader + "." + child, source=True, destination=False, plugs=True) or [])
    return out


def shader_types(mesh):
    """Node types of the surface shaders assigned to *mesh*."""
    return sorted({{cmds.nodeType(s) for s in shaders_of(mesh)}})


state = {{}}
for leg in ("closure", "scene", "openpbr", "standard"):
    if not os.path.isfile(os.path.join(OUT_DIR, leg + ".ma")):
        continue
    cmds.file(os.path.join(OUT_DIR, leg + ".ma").replace(chr(92), "/"),
              open=True, force=True)
    names = ("grp_root", "grp_sub", "loc_parent", "loc_marker", "stray_empty",
             "mesh_a", "mesh_b", "mesh_c", "mesh_cutout")
    state[leg] = {{
        "kinds": {{n: node_kind(n) for n in names}},
        "parents": {{n: parent_of(n) for n in names if cmds.objExists(n)}},
        "grp_sub_t": cmds.getAttr("grp_sub.translate")[0] if cmds.objExists("grp_sub") else None,
        "textures": file_textures("mesh_a") if cmds.objExists("mesh_a") else {{}},
        "opacity": opacity_wiring("mesh_cutout") if cmds.objExists("mesh_cutout") else [],
        "cutout_textures": (
            file_textures("mesh_cutout") if cmds.objExists("mesh_cutout") else {{}}),
        "shaders": shader_types("mesh_a") if cmds.objExists("mesh_a") else [],
        "materials": sorted(
            {{s for m in ("mesh_a", "mesh_cutout") if cmds.objExists(m)
              for s in shaders_of(m)}}),
    }}
with open(os.path.join(OUT_DIR, "inspect.json"), "w") as fh:
    json.dump(state, fh)
sys.stdout.flush()
os._exit(0)  # skip standalone teardown (known crasher); artifact is the verdict
'''


def build_scene(bpy):
    """The trap hierarchy + the two-map material (see module docstring)."""
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()

    def empty(name, display="PLAIN_AXES", parent=None):
        obj = bpy.data.objects.new(name, None)
        obj.empty_display_type = display
        bpy.context.scene.collection.objects.link(obj)
        obj.parent = parent
        return obj

    def cube(name, parent=None, at=(0, 0, 0)):
        bpy.ops.mesh.primitive_cube_add(size=1, location=at)
        obj = bpy.context.active_object
        obj.name = name
        obj.parent = parent
        return obj

    tex_product = os.path.join(TEMP, "Agilent_E4419B.png")
    tex_rough = os.path.join(TEMP, "e2e_brick_Roughness.png")
    png(tex_product, (200, 40, 40))
    png(tex_rough, (128, 128, 128))

    grp_root = empty("grp_root")
    grp_sub = empty("grp_sub", parent=grp_root)
    grp_sub.location = (1.0, 2.0, 3.0)
    mesh_a = cube("mesh_a", parent=grp_sub)
    loc_parent = empty("loc_parent", display="ARROWS", parent=grp_root)
    cube("mesh_b", parent=loc_parent, at=(3, 0, 0))
    empty("loc_marker", display="ARROWS", parent=grp_root)
    cube("mesh_c", at=(-3, 0, 0))
    empty("stray_empty")

    mat = bpy.data.materials.new("E2E_product_mat")
    mat.use_nodes = True
    bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    for path, socket in ((tex_product, "Base Color"), (tex_rough, "Roughness")):
        node = mat.node_tree.nodes.new("ShaderNodeTexImage")
        node.image = bpy.data.images.load(path)
        mat.node_tree.links.new(node.outputs["Color"], bsdf.inputs[socket])
    mesh_a.data.materials.append(mat)

    # Cutout trap: ONE image, Color -> Base Color AND Alpha -> Alpha (the
    # canonical Blender alpha setup). Product-named so the filename taxonomy
    # cannot classify it -- the manifest's traced slots are the only route, which
    # is exactly the path that used to wire the COLOR into Maya's opacity.
    tex_cutout = os.path.join(TEMP, "Agilent_8757D.png")
    png(tex_cutout, (40, 200, 40), alpha=True)
    cutout_mat = bpy.data.materials.new("E2E_cutout_mat")
    cutout_mat.use_nodes = True
    cutout_bsdf = next(
        n for n in cutout_mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"
    )
    cutout_tex = cutout_mat.node_tree.nodes.new("ShaderNodeTexImage")
    cutout_tex.image = bpy.data.images.load(tex_cutout)
    cutout_mat.node_tree.links.new(
        cutout_tex.outputs["Color"], cutout_bsdf.inputs["Base Color"]
    )
    cutout_mat.node_tree.links.new(
        cutout_tex.outputs["Alpha"], cutout_bsdf.inputs["Alpha"]
    )
    mesh_cutout = cube("mesh_cutout", at=(0, 3, 0))
    mesh_cutout.data.materials.append(cutout_mat)
    return mesh_a, loc_parent


def run_gui_leg(bridge, ptk, seeds):
    """Interactive-send leg: real ``maya.exe`` with ``-log``; see module docstring."""
    from pythontk.core_utils.app_handoff import HandoffRequest
    from blendertk.env_utils.maya_bridge._maya_bridge import MayaBridge

    sentinel = os.path.join(TEMP, "gui_state.json")
    maya_log = os.path.join(TEMP, "gui_maya.log")
    request = HandoffRequest(
        template="import", mode="send_to", params=bridge.merge_params(None)
    )
    payload = bridge._produce(bridge._resolve_objects(list(seeds)), request)
    script = bridge.deliverers["send_to"].render(bridge, payload, request)
    script += (
        "\nwith open(r'%s', 'w') as _fh:\n    _fh.write('done')\n" % sentinel
    )
    script_path = str(payload.primary) + ".e2e.py"
    with open(script_path, "w", encoding="utf-8") as fh:
        fh.write(script)

    proc = ptk.AppLauncher.launch(
        bridge.maya_path,
        args=["-log", maya_log, "-command", MayaBridge._build_mel_command(script_path)],
        detached=True,
        env=MayaBridge._launch_env(),
    )
    deadline = time.time() + 420
    while time.time() < deadline and not os.path.isfile(sentinel):
        time.sleep(3)
    done = os.path.isfile(sentinel)
    time.sleep(2)

    log_text = ""
    if os.path.isfile(maya_log):
        with open(maya_log, "r", errors="replace") as fh:
            log_text = fh.read()
    color_lines = [
        line.strip() for line in log_text.splitlines()
        if any(k in line.lower() for k in ("ocio", "color space", "color management"))
    ]
    pid = getattr(proc, "pid", None)
    if pid:  # terminate ONLY the Maya this run launched
        import subprocess

        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)

    check("gui: import template ran to completion in the launched Maya", done)
    check(
        "gui: no OCIO / color-management errors in the Maya log",
        bool(log_text) and not any("error" in l.lower() or "failed" in l.lower()
                                   for l in color_lines),
        json.dumps(color_lines[:5]),
    )
    # The OCIO strip edits the child's environment, so it is exactly the kind of
    # change that could take the renderer down with it. Maya announces VP2 in
    # its log ("Initialized VP2.0 renderer"), so assert it rather than assume.
    vp2_lines = [
        line.strip() for line in log_text.splitlines()
        if "vp2" in line.lower() or "viewport 2" in line.lower()
    ]
    check(
        "gui: Viewport 2.0 initialized (the env strip did not take it down)",
        any("initialized vp2" in l.lower() for l in vp2_lines),
        json.dumps(vp2_lines[:5]),
    )


try:
    import bpy
    import pythontk as ptk
    import blendertk  # noqa: F401 -- resolves the package surface

    from blendertk.env_utils.maya_bridge._maya_bridge import MayaBridge

    os.makedirs(TEMP, exist_ok=True)
    mesh_a, loc_parent = build_scene(bpy)
    bridge = MayaBridge()

    # Leg 1 -- closure: one nested mesh + one marked parent Empty as seeds.
    r1 = bridge.save_as(
        os.path.join(TEMP, "closure.ma"), objects=[mesh_a, "loc_parent"], timeout=900
    )
    check("closure save_as produced a .ma", bool(r1))
    # Leg 2 -- whole scene.
    r2 = bridge.save_as(os.path.join(TEMP, "scene.ma"), timeout=900)
    check("whole-scene save_as produced a .ma", bool(r2))

    # Legs 3/4 -- the SHADER_TYPE choice reaches GameShader. Same payload, same
    # manifest; only the rebuild target differs. A Maya that cannot build the
    # requested type degrades to standardSurface with a warning, so these assert
    # "requested OR the documented fallback" rather than failing on old installs.
    shader_legs = {}
    for leg, value, want in (
        ("openpbr", "open_pbr", "openPBRSurface"),
        ("standard", "standard_surface", "standardSurface"),
    ):
        result = bridge.save_as(
            os.path.join(TEMP, leg + ".ma"),
            params={"SHADER_TYPE": value},
            timeout=900,
        )
        shader_legs[leg] = want
        check(f"{leg} save_as produced a .ma", bool(result))

    if r1 and r2:
        mayapy = bridge.headless_app_path
        got = ptk.ScriptRunner.run_script_to_artifact(
            mayapy,
            _INSPECT.format(out_dir=TEMP),
            artifact=os.path.join(TEMP, "inspect.json"),
            timeout=600,
        )
        with open(os.path.join(TEMP, "inspect.json"), "r") as fh:
            state = json.load(fh)

        closure, scene = state["closure"], state["scene"]
        check(
            "closure: ancestor chain arrived as groups",
            closure["kinds"]["grp_root"] == "group"
            and closure["kinds"]["grp_sub"] == "group",
            json.dumps(closure["kinds"]),
        )
        check(
            "closure: nesting intact (mesh_a under grp_sub under grp_root)",
            closure["parents"].get("mesh_a") == "grp_sub"
            and closure["parents"].get("grp_sub") == "grp_root",
        )
        check(
            "closure: descendant mesh_b rode with its marked parent",
            closure["parents"].get("mesh_b") == "loc_parent",
        )
        check(
            "closure: marked parent stays a LOCATOR despite children",
            closure["kinds"]["loc_parent"] == "locator",
        )
        check(
            "closure: unrequested siblings did NOT ride",
            all(closure["kinds"][n] is None
                for n in ("mesh_c", "stray_empty", "loc_marker")),
            json.dumps(closure["kinds"]),
        )
        check(
            "closure: transform values survived",
            closure["grp_sub_t"]
            and all(abs(a - b) < 1e-4
                    for a, b in zip(closure["grp_sub_t"], (1.0, 2.0, 3.0))),
            str(closure["grp_sub_t"]),
        )

        want = {
            "grp_root": "group", "grp_sub": "group", "loc_parent": "locator",
            "loc_marker": "locator", "stray_empty": "locator",
            "mesh_a": "mesh", "mesh_b": "mesh", "mesh_c": "mesh",
            "mesh_cutout": "mesh",
        }
        check(
            "scene: every node arrived as the CORRECT Maya node type",
            all(scene["kinds"].get(n) == k for n, k in want.items()),
            json.dumps(scene["kinds"]),
        )
        texs = scene["textures"]
        product = next((v for k, v in texs.items() if "Agilent" in k), None)
        rough = next((v for k, v in texs.items() if "Roughness" in k), None)
        check(
            "scene: product-named map rescued into the color slot as sRGB",
            product and any(_is_color_slot(d) for d in product[1])
            and "srgb" in (product[0] or "").lower(),
            json.dumps(product),
        )
        check(
            "scene: Roughness map classified -> roughness plug as Raw",
            rough and any("oughness" in d for d in rough[1])
            and (rough[0] or "").lower() == "raw",
            json.dumps(rough),
        )

        # The reported bug: a cutout material arrived with its COLOR wired into
        # opacity, so the texture's RGB drove transparency.
        opacity = scene["opacity"]
        check(
            "scene: cutout opacity driven by the image ALPHA, never its color",
            bool(opacity) and all(p.endswith(".outAlpha") for p in opacity),
            json.dumps(opacity),
        )
        check(
            # A float3 opacity (standardSurface / openPBR) must be driven on
            # every child, or the untouched channels leave it partly opaque; a
            # scalar slot (StingrayPBS `opacity`) takes exactly one.
            "scene: opacity drives the WHOLE attribute, not one channel of it",
            len(opacity) in (1, 3),
            json.dumps(opacity),
        )
        cutout_tex = scene["cutout_textures"]
        check(
            "scene: the cutout's color still reaches the color slot off the same file",
            any(
                any(_is_color_slot(d) for d in meta[1])
                for meta in cutout_tex.values()
            ),
            json.dumps(cutout_tex),
        )

        # Names are the binding for a game-engine-bound asset: the rebuild is
        # created while the FBX-carried material still owns the name, so every
        # material used to land suffixed ("E2E_cutout_mat1") and the digit
        # compounded on each re-send (live production report).
        check(
            "scene: rebuilt materials keep their EXACT source names",
            set(scene["materials"]) == {"E2E_product_mat", "E2E_cutout_mat"},
            json.dumps(scene["materials"]),
        )

        # Default is the game shader; the two opt-in legs must differ from it.
        check(
            "scene: default rebuild targets the game shader (StingrayPBS)",
            scene["shaders"] in (["StingrayPBS"], ["standardSurface"]),
            json.dumps(scene["shaders"]),
        )
        for leg, want in shader_legs.items():
            got = state.get(leg, {}).get("shaders", [])
            check(
                f"{leg}: rebuild targets {want} (or the documented fallback)",
                got in ([want], ["standardSurface"]),
                json.dumps(got),
            )
            # standardSurface is a legitimate REQUEST as well as the fallback
            # target, so only call it a fallback when it isn't what was asked for.
            if got == ["standardSurface"] and want != "standardSurface":
                lines.append(
                    f"     note: {want} unavailable in this Maya — fallback taken"
                )

    if GUI_LEG:
        run_gui_leg(bridge, ptk, ("mesh_a", "loc_parent"))

except Exception:
    lines.append("FAIL setup:")
    lines.append(traceback.format_exc())
finally:
    shutil.rmtree(TEMP, ignore_errors=True)

# Indented lines are informational notes (a documented fallback was taken, and
# the check itself already passed) -- they must not decide the verdict.
ok = all(line.startswith("OK") for line in lines if not line.startswith(" "))
for line in lines:
    print(line)
print(f"===RESULT: {'PASS' if ok else 'FAIL'}===")
