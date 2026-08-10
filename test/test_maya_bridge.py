"""blendertk MayaBridge feature test (headless Blender — bpy present, NO Qt).

Run: blender --background --factory-startup --python blendertk/test/test_maya_bridge.py

Covers the Qt-free engine surface (exe discovery, template discovery, MEL builder, raw template
text) and the bpy-dependent FBX export (full + strip-materials), with ``btk.FbxUtils.export_selection_fbx``
stubbed. ``render_template`` / ``send`` are NOT exercised here: they import ``parameters`` ->
``uitk.bridge`` (Qt), which headless ``--factory-startup`` Blender lacks. Those (and the live panel
that builds the param widgets) are covered under the workspace ``.venv`` by
``test_blender_ui_handler.py``.
"""

import sys
import os
import shutil
import tempfile
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
    import blendertk as btk
    from blendertk.env_utils.maya_bridge._maya_bridge import MayaBridge, _TEMPLATE_DIR

    def reset():
        bpy.ops.object.select_all(action="DESELECT")
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)
        for m in list(bpy.data.materials):
            bpy.data.materials.remove(m)

    # ---- discovery (Qt-free) -------------------------------------------------
    resolved = MayaBridge().maya_path
    check(
        "maya_path returns None or a str (no raise)",
        resolved is None or isinstance(resolved, str),
        f"{resolved}",
    )
    check(
        "explicit maya_path wins", MayaBridge("X:/maya.exe").maya_path == "X:/maya.exe"
    )

    # ---- template discovery (Qt-free) ---------------------------------------
    pairs = MayaBridge.list_template_modes()
    stems = {t for t, _ in pairs}
    # The three near-identical recipes collapsed into one options-driven template.
    check("templates discovered", stems == {"import"}, f"{sorted(stems)}")
    check("all modes send_to", all(m == "send_to" for _, m in pairs))
    check(
        "template_modes parses BRIDGE_MODES",
        MayaBridge.template_modes(_TEMPLATE_DIR / "import.py") == ("send_to",),
    )
    # The allowed-list must cover every mode a spec actually serves: the helpers filter
    # declarations against it and silently fall back to entry [0], so a `save_as`
    # template left out of it reads as an interactive send -- which then routes through
    # send(), never populates __OUT_FILE__, and fails minutes into a launched Maya.
    # Derived from the specs so it cannot fall out of step; [0] stays the fallback.
    check(
        "allowed modes cover every registered spec mode",
        set(MayaBridge.template_modes_allowed)
        == set(MayaBridge.spec.modes) | set(MayaBridge.run_spec.modes),
        f"{MayaBridge.template_modes_allowed}",
    )
    check(
        "send_to is still the lenient fallback",
        MayaBridge.template_modes_allowed[0] == "send_to",
    )
    check(
        "the blocking template's own mode survives the strict read",
        MayaBridge.template_modes(_TEMPLATE_DIR / "_save_scene.py") == ("save_as",),
    )

    # ---- raw template text (Qt-free; render_template itself needs Qt) -------
    import_txt = (_TEMPLATE_DIR / "import.py").read_text()
    check(
        "import template: FBXImport + FBX_PATH placeholder",
        "FBXImport" in import_txt and "__FBX_PATH__" in import_txt,
    )
    check(
        "import template: export-options placeholders present (panel visibility)",
        "__INCLUDE_MATERIALS__" in import_txt and "__EMBED_TEXTURES__" in import_txt,
    )
    check(
        "unified template exposes both scene-behavior options",
        "__FRAME_VIEW__" in import_txt and "__CLEAR_SCENE__" in import_txt,
    )
    # Post-import repairs (regressions: sent hierarchies arrived as locators; real
    # production materials arrived gray). The Maya-side execution is pinned by
    # mayatk's suite + live probe; here we pin the template's wiring.
    check(
        "import template: parent Empties -> plain groups (locator strip)",
        "restore_empty_groups" in import_txt and "exactType=\"locator\"" in import_txt,
    )
    check(
        "import template: manifest rebuild through mayatk's paired applier",
        "_apply_texture_manifest" in import_txt
        and ".manifest.json" in import_txt
        and "keeping the FBX-carried materials" in import_txt,
    )
    check(
        "import template: deterministic import (reset + add mode, new-node diff)",
        "FBXResetImport" in import_txt and "returnNewNodes=True" in import_txt,
    )

    # ---- MEL command builder (Qt-free) --------------------------------------
    mel = MayaBridge._build_mel_command(r"C:\tmp\btk_to_maya.py")
    check(
        "mel command wraps python(exec(open(...)))",
        mel == "python(\"exec(open(r'C:/tmp/btk_to_maya.py').read())\")",
        mel,
    )

    # ---- save_as: the blocking route to a native .ma (Qt-FREE by design) -----
    # This is the whole point of the mode: a headless Blender must be able to write a
    # Maya scene, and headless Blender has no Qt -- so params/rendering may not touch
    # uitk here. The mayapy run itself is stubbed (it costs ~6s and needs Maya).
    from pythontk.core_utils.script_template import ScriptTemplate
    from pythontk.core_utils import app_handoff as _handoff
    from blendertk.env_utils.maya_bridge._maya_bridge import DEFAULTS

    save_txt = (_TEMPLATE_DIR / "_save_scene.py").read_text()
    check(
        "save template hidden from the panel list (not a user-pickable recipe)",
        "_save_scene" not in {p.stem for p in MayaBridge.list_templates()},
    )
    check(
        "save template declares only save_as",
        ScriptTemplate.declared_modes(_TEMPLATE_DIR / "_save_scene.py") == ("save_as",),
    )
    check(
        "save template: renames + saves, and exits hard (artifact is the verdict)",
        "cmds.file(rename=OUT_FILE)" in save_txt
        and "save=True" in save_txt
        and "os._exit(0)" in save_txt,
    )
    check(
        "save template: same paired-engine repairs as the interactive import",
        "_apply_texture_manifest" in save_txt
        and "restore_empty_groups" in save_txt,
    )
    check(
        "save template: .mb only when asked for, else mayaAscii",
        'mayaBinary" if OUT_FILE.lower().endswith(".mb")' in save_txt,
    )

    sa_hp = MayaBridge(maya_path="C:/fake/maya.exe").headless_app_path
    check(
        "headless route resolves a mayapy (or None where Maya is absent)",
        sa_hp is None or os.path.basename(sa_hp).lower().startswith("mayapy"),
        f"{sa_hp}",
    )

    # Qt-free params: the engine still knows its own defaults with no uitk import.
    sa_bridge = MayaBridge(maya_path="C:/fake/bin/maya.exe")
    check(
        "params_defaults answers without Qt",
        sa_bridge.params_defaults() == DEFAULTS,
        f"{sa_bridge.params_defaults()}",
    )
    # These hand-offs feed a game engine: the rebuild targets Maya's game shader
    # unless the user says otherwise. Stingray is also the only family that
    # DECLARES its texture slots, so its maps survive the trip back out rather
    # than being re-guessed from filenames. Paired with the Maya-side pull
    # default (mayatk: BlenderSceneImport.import_scene) -- same rebuild, so the
    # two must not disagree; each side pins its own.
    check(
        "default rebuild shader is the game shader",
        DEFAULTS["SHADER_TYPE"] == "stingray",
        DEFAULTS["SHADER_TYPE"],
    )

    sa_runs = []

    def sa_fake_run(app_exe, script_text, *, artifact, launch_args, timeout, env=None):
        import pythontk as ptk

        sa_runs.append(
            {
                "app": app_exe,
                "script": script_text,
                "artifact": artifact,
                "args": list(launch_args("S.py")),
                "env": env,
            }
        )
        with open(artifact, "w", encoding="utf-8") as fh:
            fh.write("//Maya ASCII\n")
        return ptk.ScriptRunResult(
            artifact=artifact, returncode=0, output="", duration=0.1, script_path="S.py"
        )

    sa_dir = tempfile.mkdtemp(prefix="btk_save_as_")
    sa_orig_run = _handoff.ScriptRunDeliverer.run
    sa_orig_export = btk.FbxUtils.export_selection_fbx
    _handoff.ScriptRunDeliverer.run = staticmethod(sa_fake_run)
    btk.FbxUtils.export_selection_fbx = lambda filepath=None, objects=None, **o: filepath
    try:
        reset()
        bpy.ops.mesh.primitive_cube_add()
        bpy.context.active_object.name = "SaveAsCube"
        bpy.ops.object.select_all(action="DESELECT")  # nothing selected on purpose

        sa_out = os.path.join(sa_dir, "asset.ma")
        sa_result = sa_bridge.save_as(sa_out)
        check("save_as returns the written artifact", bool(sa_result), f"{sa_result}")
        # NOTE: never echo the run record itself -- it carries the child env.
        check(
            "save_as ran ONE headless mayapy, interpreter-style argv",
            len(sa_runs) == 1 and sa_runs[0]["args"] == ["S.py"],
            f"{len(sa_runs)} run(s), argv={sa_runs[0]['args'] if sa_runs else None}",
        )
        check(
            "save_as env carries the fast-start vars",
            (sa_runs[0]["env"] or {}).get("MAYA_SKIP_USERSETUP_PY") == "1",
        )
        import re

        sa_script = sa_runs[0]["script"]
        # mayapy writes a staging sibling; the caller's path is the promotion target,
        # so a failed run can never destroy an existing scene file.
        sa_staged = _handoff.ScriptRunDeliverer._staging_path(sa_out)
        check(
            "rendered save script points at the staging sibling + the payload",
            f'OUT_FILE = r"{sa_staged.replace(os.sep, "/")}"' in sa_script
            and not re.findall(r"__[A-Z][A-Z0-9_]*__", sa_script),
        )
        check(
            "the staged file is promoted to the caller's path",
            os.path.isfile(sa_out) and not os.path.exists(sa_staged),
        )
        import ast

        ast.parse(sa_script)  # raises -> caught by the outer handler
        check("rendered save script is valid Python", True)

        # "Save the scene as ..." means the SCENE -- nothing was selected above.
        sa_runs.clear()
        check(
            "save_as defaults to the whole scene, not the selection",
            [o.name for o in sa_bridge._scene_objects()] == ["SaveAsCube"],
            f"{[o.name for o in sa_bridge._scene_objects()]}",
        )

        sa_bare = sa_bridge.save_as(os.path.join(sa_dir, "bare"))
        check(
            "a bare path gets .ma; .mb is honoured when asked for",
            sa_bare and sa_bare["output"].endswith(".ma")
            and MayaBridge.resolve_save_path("x/y.mb").endswith(".mb"),
        )

        sa_runs.clear()
        sa_bad = sa_bridge.save_as(sa_out, template="import")
        check(
            "the interactive import template is rejected for save_as (preflight)",
            sa_bad is None and not sa_runs,
        )
    finally:
        _handoff.ScriptRunDeliverer.run = staticmethod(sa_orig_run)
        btk.FbxUtils.export_selection_fbx = sa_orig_export
        shutil.rmtree(sa_dir, ignore_errors=True)

    # ---- FBX export via _export_objects (bpy; plain params dict, no Qt) ------
    captured = {}

    def fake_export(filepath=None, objects=None, **opts):
        captured["names"] = [o.name for o in (objects or [])]
        captured["mat_counts"] = [
            len(o.data.materials)
            if getattr(o, "data", None) is not None and hasattr(o.data, "materials")
            else -1
            for o in (objects or [])
        ]
        captured["opts"] = dict(opts)
        return filepath

    orig_export = btk.FbxUtils.export_selection_fbx
    btk.FbxUtils.export_selection_fbx = fake_export
    try:
        bridge = MayaBridge(maya_path="C:/fake/maya.exe")
        tmp_fbx = os.path.join(tempfile.gettempdir(), "btk_maya_bridge_test.fbx")

        # full materials
        reset()
        bpy.ops.mesh.primitive_cube_add()
        cube = bpy.context.active_object
        btk.assign_mat([cube], btk.create_mat("standard", name="MB"))
        captured.clear()
        bridge._export_fbx(
            [cube],
            tmp_fbx,
            {
                "INCLUDE_MATERIALS": True,
                "EMBED_TEXTURES": True,
                "TRIANGULATE": True,
                "APPLY_UNIT_SCALE": True,
            },
        )
        check(
            "export(full): the original object is exported (materials kept)",
            captured["names"] == [cube.name],
        )
        check(
            "export(full): opts map params",
            captured["opts"].get("use_triangles") is True
            and captured["opts"].get("embed_textures") is True
            and captured["opts"].get("path_mode") == "COPY"
            and captured["opts"].get("apply_unit_scale") is True,
        )

        # strip materials
        reset()
        bpy.ops.mesh.primitive_cube_add()
        cube = bpy.context.active_object
        btk.assign_mat([cube], btk.create_mat("standard", name="MB2"))
        orig_count = len(cube.data.materials)
        captured.clear()
        bridge._export_fbx([cube], tmp_fbx, {"INCLUDE_MATERIALS": False})
        check(
            "strip: exported copies, not the original",
            cube.name not in captured["names"] and len(captured["names"]) == 1,
        )
        check("strip: exported copies have no materials", captured["mat_counts"] == [0])
        check(
            "strip: original keeps its materials",
            len(cube.data.materials) == orig_count and orig_count > 0,
        )
        check(
            "strip: temp copies removed from the scene",
            all(n not in bpy.data.objects for n in captured["names"]),
        )
    finally:
        btk.FbxUtils.export_selection_fbx = orig_export

    # ---- texture-manifest sidecar (bpy; the send half of the materials fix) --
    reset()
    tex_path = os.path.join(tempfile.gettempdir(), "btk_mb_BaseColor.png")
    img = bpy.data.images.new("btk_mb_BaseColor", 8, 8)
    img.filepath_raw = tex_path
    img.file_format = "PNG"
    img.save()

    # Textured mat: image routed through a Mix node — the case Blender's FBX
    # exporter carries NOTHING for (only near-direct socket wiring rides).
    mat = bpy.data.materials.new("MB_textured")
    mat.use_nodes = True
    bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    tex_node = mat.node_tree.nodes.new("ShaderNodeTexImage")
    tex_node.image = bpy.data.images.load(tex_path)
    mix = mat.node_tree.nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    mat.node_tree.links.new(tex_node.outputs["Color"], mix.inputs["A"])
    mat.node_tree.links.new(mix.outputs["Result"], bsdf.inputs["Base Color"])
    # Packed-only image (no file on disk) -> file-less entry, named warning Maya-side.
    packed = bpy.data.materials.new("MB_packed")
    packed.use_nodes = True
    packed_tex = packed.node_tree.nodes.new("ShaderNodeTexImage")
    packed_tex.image = bpy.data.images.new("MB_generated", 4, 4)
    # Untextured mat: flat colors ride the FBX fine -> no entry, but listed in
    # scene_materials (the rename-on-clash guard).
    flat = bpy.data.materials.new("MB_flat")

    bpy.ops.mesh.primitive_cube_add()
    cube_a = bpy.context.active_object
    cube_a.data.materials.append(mat)
    bpy.ops.mesh.primitive_cube_add()
    cube_b = bpy.context.active_object
    cube_b.data.materials.append(mat)  # shared datablock -> ONE entry, two objects
    cube_b.data.materials.append(packed)
    cube_b.data.materials.append(flat)

    manifest_fbx = os.path.join(tempfile.gettempdir(), "btk_mb_manifest.fbx")
    manifest_path = manifest_fbx + ".manifest.json"
    if os.path.isfile(manifest_path):
        os.remove(manifest_path)
    MayaBridge(maya_path="C:/fake/maya.exe")._write_manifest(
        [cube_a, cube_b], manifest_fbx
    )
    import json

    with open(manifest_path, "r", encoding="utf-8") as fh:
        manifest = json.load(fh)
    entries = {e["name"]: e for e in manifest["materials"]}
    check(
        "manifest: one entry per textured material (flat skipped)",
        set(entries) == {"MB_textured", "MB_packed"},
        f"{sorted(entries)}",
    )
    check(
        "manifest: shared material merges both user objects",
        sorted(entries["MB_textured"]["objects"]) == sorted([cube_a.name, cube_b.name])
        and entries["MB_textured"]["files"] == [os.path.abspath(tex_path)],
        f"{entries['MB_textured']}",
    )
    check(
        "manifest: packed-only image -> file-less entry (named warning, not silence)",
        entries["MB_packed"]["files"] == [],
    )
    check(
        "manifest: scene_materials lists the untextured sibling (clash guard)",
        "MB_flat" in manifest["scene_materials"],
        f"{manifest['scene_materials']}",
    )
    os.remove(manifest_path)
    os.remove(tex_path)

    # ---- launch env: Blender-private OCIO stripped, foreign inherited --------
    blender_root = os.path.dirname(bpy.app.binary_path)
    bundled = os.path.join(blender_root, "dummy", "config.ocio")
    prior = os.environ.pop("OCIO", None)
    try:
        os.environ["OCIO"] = bundled
        env = MayaBridge._launch_env()
        check(
            "launch env: OCIO inside Blender's install is stripped",
            env is not None and "OCIO" not in env,
        )
        os.environ["OCIO"] = os.path.join(tempfile.gettempdir(), "studio.ocio")
        check(
            "launch env: foreign OCIO -> inherit unchanged (None)",
            MayaBridge._launch_env() is None,
        )
        del os.environ["OCIO"]
        check("launch env: no OCIO -> inherit unchanged (None)", MayaBridge._launch_env() is None)
    finally:
        os.environ.pop("OCIO", None)
        if prior is not None:
            os.environ["OCIO"] = prior

    # ------------------------------------------------- slot derivation (link tracing)
    # The Maya side rebuilds manifest materials by classifying FILENAMES, which
    # cannot place a texture named after a product. Maya's own shaders declare
    # their inputs; a Blender material has to be traced forward from each image
    # node to a Principled socket. Only an UNAMBIGUOUS destination is recorded --
    # a packed map reaches several channels and its identity lives in its
    # filename, which the Maya side already reads correctly.
    import bpy as _bpy
    import struct as _struct
    import zlib as _zlib

    _slot_dir = tempfile.mkdtemp(prefix="btk_slots_")

    def _png(path):
        """A real 1x1 PNG -- _resolved_image_file skips generated/packed images.

        Built from integer byte values rather than escapes: a \\x literal here is
        one bad edit away from becoming real bytes and corrupting the file.
        """
        def chunk(tag, payload):
            body = tag + payload
            return (_struct.pack(">I", len(payload)) + body
                    + _struct.pack(">I", _zlib.crc32(body)))

        signature = bytes([137, 80, 78, 71, 13, 10, 26, 10])
        header = _struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
        with open(path, "wb") as fh:
            fh.write(
                signature
                + chunk(b"IHDR", header)
                + chunk(b"IDAT", _zlib.compress(bytes(4)))
                + chunk(b"IEND", b"")
            )
        return path

    def _img(nt, name):
        node = nt.nodes.new("ShaderNodeTexImage")
        node.image = _bpy.data.images.load(
            _png(os.path.join(_slot_dir, name + ".png")), check_existing=True
        )
        return node

    def _mat(name):
        m = _bpy.data.materials.new(name)
        m.use_nodes = True
        nt = m.node_tree
        return m, nt, next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")

    # 1. straight into Base Color -> baseColor
    m, nt, bsdf = _mat("slotDirect")
    nt.links.new(_img(nt, "prod_number").outputs["Color"], bsdf.inputs["Base Color"])
    slots = MayaBridge._material_slots(m)
    check("slot trace: direct link resolves baseColor",
          list(slots) == ["baseColor"], str(slots))

    # 2. through a Normal Map node -> normal (NOT bump)
    m, nt, bsdf = _mat("slotNormal")
    nmap = nt.nodes.new("ShaderNodeNormalMap")
    nt.links.new(_img(nt, "nrm").outputs["Color"], nmap.inputs["Color"])
    nt.links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
    slots = MayaBridge._material_slots(m)
    check("slot trace: Normal Map chain resolves normal",
          list(slots) == ["normal"], str(slots))

    # 3. through a Bump node -> bump, distinguishable from a normal map
    m, nt, bsdf = _mat("slotBump")
    bump = nt.nodes.new("ShaderNodeBump")
    nt.links.new(_img(nt, "hgt").outputs["Color"], bump.inputs["Height"])
    nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    slots = MayaBridge._material_slots(m)
    check("slot trace: Bump chain resolves bump, not normal",
          list(slots) == ["bump"], str(slots))

    # 4. one image reaching SEVERAL channels (packed) records nothing
    m, nt, bsdf = _mat("slotPacked")
    packed = _img(nt, "orm")
    sep = nt.nodes.new("ShaderNodeSeparateColor")
    nt.links.new(packed.outputs["Color"], sep.inputs["Color"])
    nt.links.new(sep.outputs[0], bsdf.inputs["Metallic"])
    nt.links.new(sep.outputs[1], bsdf.inputs["Roughness"])
    slots = MayaBridge._material_slots(m)
    check("slot trace: a packed map records NO channel (filename owns it)",
          slots == {}, str(slots))

    # 5. a raw image straight into the Principled Normal input is ambiguous
    m, nt, bsdf = _mat("slotRawNormal")
    nt.links.new(_img(nt, "raw").outputs["Color"], bsdf.inputs["Normal"])
    slots = MayaBridge._material_slots(m)
    check("slot trace: raw image into Normal is ambiguous -> no channel",
          slots == {}, str(slots))

    # 6. an unconnected image records nothing
    m, nt, bsdf = _mat("slotOrphan")
    _img(nt, "orphan")
    slots = MayaBridge._material_slots(m)
    check("slot trace: an unconnected image records no channel",
          slots == {}, str(slots))

    # 7. SEVERAL images reaching ONE channel is just as unresolvable as one image
    #    reaching several. An AO multiply feeds the AO map and the color map into
    #    the same Base Color input; picking by node order would hand Maya the AO
    #    map as the base color.
    m, nt, bsdf = _mat("slotAoMultiply")
    mix = nt.nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    mix.blend_type = "MULTIPLY"
    nt.links.new(_img(nt, "colr").outputs["Color"], mix.inputs[6])
    nt.links.new(_img(nt, "occl").outputs["Color"], mix.inputs[7])
    nt.links.new(mix.outputs[2], bsdf.inputs["Base Color"])
    slots = MayaBridge._material_slots(m)
    check("slot trace: two images into one channel record NOTHING",
          slots == {}, str(slots))

    # 7b. The canonical cutout material: ONE image whose Color feeds Base Color
    #     and whose Alpha feeds Alpha. Read node-wide that is two channels --
    #     indistinguishable from a packed map -- so both were dropped and the
    #     material arrived with neither its color nor its cutout. The two
    #     outputs are different DATA, so they must trace separately.
    m, nt, bsdf = _mat("slotCutout")
    cutout = _img(nt, "cutout_leaf")
    nt.links.new(cutout.outputs["Color"], bsdf.inputs["Base Color"])
    nt.links.new(cutout.outputs["Alpha"], bsdf.inputs["Alpha"])
    slots = MayaBridge._material_slots(m)
    check(
        "slot trace: Color+Alpha off ONE image resolves BOTH channels",
        sorted(slots) == ["baseColor", "opacity"]
        and slots.get("baseColor") == slots.get("opacity"),
        str(slots),
    )

    # 7c. A packed map is still rejected: one SOCKET reaching several channels
    #     (an ORM's Color through a Separate Color) is the real ambiguity, and
    #     socket-awareness must not weaken that.
    m, nt, bsdf = _mat("slotPackedSocket")
    sep = nt.nodes.new("ShaderNodeSeparateColor")
    nt.links.new(_img(nt, "orm").outputs["Color"], sep.inputs["Color"])
    nt.links.new(sep.outputs[1], bsdf.inputs["Roughness"])
    nt.links.new(sep.outputs[2], bsdf.inputs["Metallic"])
    slots = MayaBridge._material_slots(m)
    check(
        "slot trace: a packed map's Color socket still records NOTHING",
        slots == {}, str(slots),
    )

    # 8. every derived channel must be resolvable by the SHARED registry
    import pythontk as ptk

    m, nt, bsdf = _mat("slotVocab")
    nt.links.new(_img(nt, "c").outputs["Color"], bsdf.inputs["Base Color"])
    nt.links.new(_img(nt, "r").outputs["Color"], bsdf.inputs["Roughness"])
    nt.links.new(_img(nt, "a").outputs["Color"], bsdf.inputs["Alpha"])
    derived = MayaBridge._material_slots(m)
    check("slot trace: derived channels are all in the shared vocabulary",
          derived and all(
              ptk.MapRegistry.resolve_type_from_channel(c) is not None
              for c in derived),
          str({c: ptk.MapRegistry.resolve_type_from_channel(c) for c in derived}))

    shutil.rmtree(_slot_dir, ignore_errors=True)  # artifacts are teardown's job

except Exception as e:
    lines.append(f"FAIL setup: {e!r}")
    lines.append(traceback.format_exc())

ok = all(line.startswith("OK") for line in lines)
for line in lines:
    print(line)
print(f"===RESULT: {'PASS' if ok else 'FAIL'}===")
