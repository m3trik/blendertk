"""blendertk MayaBridge feature test (headless Blender — bpy present, NO Qt).

Run: blender --background --factory-startup --python blendertk/test/test_maya_bridge.py

Covers the Qt-free engine surface (exe discovery, template discovery, MEL builder, raw template
text) and the bpy-dependent FBX export (full + strip-materials), with ``btk.export_selection_fbx``
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
    lines.append(f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}")


try:
    import bpy
    import pythontk as ptk
    import blendertk as btk
    from blendertk.env_utils.maya_bridge._maya_bridge import (
        MayaBridge, list_template_modes, template_modes, _TEMPLATE_DIR,
    )

    def reset():
        bpy.ops.object.select_all(action="DESELECT")
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)
        for m in list(bpy.data.materials):
            bpy.data.materials.remove(m)

    # ---- discovery (Qt-free) -------------------------------------------------
    resolved = MayaBridge().maya_path
    check("maya_path returns None or a str (no raise)",
          resolved is None or isinstance(resolved, str), f"{resolved}")
    check("explicit maya_path wins", MayaBridge("X:/maya.exe").maya_path == "X:/maya.exe")

    # ---- template discovery (Qt-free) ---------------------------------------
    pairs = list_template_modes()
    stems = {t for t, _ in pairs}
    check("templates discovered", stems == {"import", "import_and_frame", "new_scene"}, f"{sorted(stems)}")
    check("all modes send_to", all(m == "send_to" for _, m in pairs))
    check("template_modes parses BRIDGE_MODES",
          template_modes(_TEMPLATE_DIR / "import.py") == ("send_to",))

    # ---- raw template text (Qt-free; render_template itself needs Qt) -------
    import_txt = (_TEMPLATE_DIR / "import.py").read_text()
    frame_txt = (_TEMPLATE_DIR / "import_and_frame.py").read_text()
    check("import template: FBXImport + FBX_PATH placeholder",
          "FBXImport" in import_txt and '__FBX_PATH__' in import_txt)
    check("import template: export-options placeholders present (panel visibility)",
          "__INCLUDE_MATERIALS__" in import_txt and "__EMBED_TEXTURES__" in import_txt)
    check("FRAME_VIEW exposed only by the frame template",
          "__FRAME_VIEW__" in frame_txt and "__FRAME_VIEW__" not in import_txt)

    # ---- MEL command builder (Qt-free) --------------------------------------
    mel = MayaBridge._build_mel_command(r"C:\tmp\btk_to_maya.py")
    check("mel command wraps python(exec(open(...)))",
          mel == "python(\"exec(open(r'C:/tmp/btk_to_maya.py').read())\")", mel)

    # ---- FBX export via _export_objects (bpy; plain params dict, no Qt) ------
    captured = {}

    def fake_export(filepath=None, objects=None, **opts):
        captured["names"] = [o.name for o in (objects or [])]
        captured["mat_counts"] = [
            len(o.data.materials) if getattr(o, "data", None) is not None
            and hasattr(o.data, "materials") else -1
            for o in (objects or [])
        ]
        captured["opts"] = dict(opts)
        return filepath

    orig_export = btk.export_selection_fbx
    btk.export_selection_fbx = fake_export
    try:
        bridge = MayaBridge(maya_path="C:/fake/maya.exe")
        tmp_fbx = os.path.join(tempfile.gettempdir(), "btk_maya_bridge_test.fbx")

        # full materials
        reset()
        bpy.ops.mesh.primitive_cube_add()
        cube = bpy.context.active_object
        btk.assign_mat([cube], btk.create_mat("standard", name="MB"))
        captured.clear()
        bridge._export_fbx([cube], tmp_fbx, {"INCLUDE_MATERIALS": True, "EMBED_TEXTURES": True,
                                             "TRIANGULATE": True, "APPLY_UNIT_SCALE": True})
        check("export(full): the original object is exported (materials kept)",
              captured["names"] == [cube.name])
        check("export(full): opts map params",
              captured["opts"].get("use_triangles") is True
              and captured["opts"].get("embed_textures") is True
              and captured["opts"].get("path_mode") == "COPY"
              and captured["opts"].get("apply_unit_scale") is True)

        # strip materials
        reset()
        bpy.ops.mesh.primitive_cube_add()
        cube = bpy.context.active_object
        btk.assign_mat([cube], btk.create_mat("standard", name="MB2"))
        orig_count = len(cube.data.materials)
        captured.clear()
        bridge._export_fbx([cube], tmp_fbx, {"INCLUDE_MATERIALS": False})
        check("strip: exported copies, not the original",
              cube.name not in captured["names"] and len(captured["names"]) == 1)
        check("strip: exported copies have no materials", captured["mat_counts"] == [0])
        check("strip: original keeps its materials",
              len(cube.data.materials) == orig_count and orig_count > 0)
        check("strip: temp copies removed from the scene",
              all(n not in bpy.data.objects for n in captured["names"]))
    finally:
        btk.export_selection_fbx = orig_export

except Exception as e:
    lines.append(f"FAIL setup: {e!r}")
    lines.append(traceback.format_exc())

ok = all(line.startswith("OK") for line in lines)
for line in lines:
    print(line)
print(f"===RESULT: {'PASS' if ok else 'FAIL'}===")
