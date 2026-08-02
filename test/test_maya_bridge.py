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
    MayaBridge(maya_path="C:/fake/maya.exe")._write_texture_manifest(
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

except Exception as e:
    lines.append(f"FAIL setup: {e!r}")
    lines.append(traceback.format_exc())

ok = all(line.startswith("OK") for line in lines)
for line in lines:
    print(line)
print(f"===RESULT: {'PASS' if ok else 'FAIL'}===")
