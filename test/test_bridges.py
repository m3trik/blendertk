"""blendertk bridge feature test: export_selection_fbx + RizomUVBridge send-script / discovery.

Run: blender --background --factory-startup --python blendertk/test/test_bridges.py

Covers the export-and-hand-off foundation shared by the Substance / Marmoset / RizomUV bridges
(the actual app launch is not exercised — it would open RizomUV / Painter / Toolbag).
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
    import blendertk as btk
    from blendertk.uv_utils.rizom_bridge._rizom_bridge import RizomUVBridge

    def reset():
        bpy.ops.object.select_all(action="DESELECT")
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)
        for m in list(bpy.data.materials):
            bpy.data.materials.remove(m)

    tmp = tempfile.mkdtemp(prefix="btk_bridge_")

    # ---- export_selection_fbx -----------------------------------------------
    reset()
    bpy.ops.mesh.primitive_cube_add()
    cube = bpy.context.active_object
    bpy.ops.mesh.primitive_cube_add(location=(3, 0, 0))
    other = bpy.context.active_object
    bpy.ops.object.select_all(action="DESELECT")
    cube.select_set(True)  # prior selection = {cube}

    out = os.path.join(tmp, "sel.fbx")
    written = btk.export_selection_fbx(filepath=out, objects=[cube])
    check("export_selection_fbx writes the file",
          written == out and os.path.isfile(out) and os.path.getsize(out) > 0)
    check("export_selection_fbx restores the prior selection",
          cube.select_get() and not other.select_get())

    bpy.ops.object.select_all(action="DESELECT")
    try:
        btk.export_selection_fbx(filepath=os.path.join(tmp, "empty.fbx"))
        check("export_selection_fbx with nothing selected -> RuntimeError", False)
    except RuntimeError:
        check("export_selection_fbx with nothing selected -> RuntimeError", True)

    default_path = btk.export_selection_fbx(objects=[cube])
    check("export_selection_fbx default temp path",
          default_path.endswith("_bridge.fbx") and os.path.isfile(default_path))
    os.remove(default_path)

    # ---- RizomUVBridge.build_send_script --------------------------------------
    rb = RizomUVBridge()
    script = rb.build_send_script(
        "C:/tmp/mesh.fbx", load_uvs=True, import_groups=False,
        load_uvw_props=True, load_textures=False,
    )
    check("build_send_script: ZomLoad with forward-slashed path",
          'ZomLoad({File={Path="C:/tmp/mesh.fbx"' in script)
    check("build_send_script: Lua booleans map the toggles",
          "XYZUVW=true" in script and "ImportGroups=false" in script and "UVWProps=true" in script)
    check("build_send_script: no texture block when disabled", "ZomLoadTexture" not in script)

    # textured object -> a pcall-wrapped ZomLoadTexture per on-disk texture
    img_path = os.path.join(tmp, "TexA_Diffuse.png")
    gen = bpy.data.images.new("_g", 4, 4)
    gen.filepath_raw = img_path
    gen.file_format = "PNG"
    gen.save()
    bpy.data.images.remove(gen)
    mat = btk.create_mat("standard", name="RZ")
    nt = mat.node_tree
    img = bpy.data.images.load(img_path)
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = img
    bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is not None:
        nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    btk.assign_mat([cube], mat)
    script_t = rb.build_send_script("C:/tmp/mesh.fbx", objects=[cube], load_textures=True)
    check("build_send_script: pcall ZomLoadTexture for existing texture",
          "ZomLoadTexture" in script_t and "pcall(function()" in script_t
          and "TexA_Diffuse.png" in script_t.replace("\\", "/"))

    # ---- exe discovery: graceful, never raises ------------------------------
    resolved = RizomUVBridge().rizom_path
    check("rizom_path returns None or a str (no raise)",
          resolved is None or isinstance(resolved, str), f"{resolved}")

    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

except Exception as e:
    lines.append(f"FAIL setup: {e!r}")
    lines.append(traceback.format_exc())

ok = all(line.startswith("OK") for line in lines)
for line in lines:
    print(line)
print(f"===RESULT: {'PASS' if ok else 'FAIL'}===")
