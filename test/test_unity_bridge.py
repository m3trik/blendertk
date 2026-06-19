"""blendertk UnityBridge feature test (headless Blender -- bpy present, NO Qt).

Run: blender --background --factory-startup --python blendertk/test/test_unity_bridge.py

Covers the Blender->Unity hand-off engine: selection resolve, FBX export (shared BlenderExportMixin),
and the copy-to-Assets delivery (shared unitytk.CopyToAssetsDeliverer Strategy). ``send()`` itself is
NOT exercised -- it calls ``params_defaults`` -> ``parameters`` -> ``uitk.bridge`` (Qt), which
headless ``--factory-startup`` Blender lacks -- so the export + deliver hooks are driven directly
with plain dicts (mirrors test_maya_bridge's ``_export_fbx`` approach). No real Unity is needed (the
project is just a folder with an ``Assets/`` dir; ``LAUNCH_EDITOR`` stays off).
"""
import sys
import os
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for p in (REPO, os.path.join(MONO, "pythontk"), os.path.join(MONO, "unitytk")):
    if p not in sys.path:
        sys.path.insert(0, p)

lines = []


def check(name, cond, detail=""):
    lines.append(f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}")


try:
    import bpy
    import blendertk as btk
    from pythontk.core_utils.app_handoff import HandoffRequest, Payload
    from blendertk.env_utils.unity_bridge._unity_bridge import UnityBridge, list_delivery_modes

    def reset():
        bpy.ops.object.select_all(action="DESELECT")
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)

    def req(params=None):
        return HandoffRequest(template="copy_to_assets", mode="send_to", params=params or {})

    # ---- delivery modes (Qt-free, single-sourced from unitytk) --------------
    check("delivery modes", list_delivery_modes() == [("copy_to_assets", "")])
    check("delivery modes single-sourced from CopyToAssetsDeliverer",
          UnityBridge().list_template_modes() == list_delivery_modes())

    # ---- preflight gating (via the deliverer Strategy) ----------------------
    tmp = tempfile.mkdtemp(prefix="btk_unity_")
    project = os.path.join(tmp, "UnityProj")
    os.makedirs(os.path.join(project, "Assets"))

    def preflight(br):
        return br.deliverer.preflight(br, req())

    check("preflight rejects unset project", preflight(UnityBridge()) is False)
    check("preflight rejects non-project dir", preflight(UnityBridge(project_path=tmp)) is False)
    check("preflight accepts real project", preflight(UnityBridge(project_path=project)) is True)

    # ---- export + deliver end-to-end (bpy; plain params, no Qt) -------------
    reset()
    bpy.ops.mesh.primitive_cube_add()
    cube = bpy.context.active_object
    cube.name = "UnityHero"

    bridge = UnityBridge(project_path=project)
    check("_default_asset_name uses the object name", bridge._default_asset_name([cube]) == "UnityHero")

    objs = bridge._resolve_objects([cube])
    check("_resolve_objects returns the selection", objs == [cube])

    fbx = os.path.join(tmp, "export.fbx")
    bridge._export_fbx(objs, fbx, {"INCLUDE_MATERIALS": True, "EMBED_TEXTURES": True,
                                   "TRIANGULATE": False})
    check("export wrote a non-empty FBX", os.path.isfile(fbx) and os.path.getsize(fbx) > 0)

    def deliver(params):
        payload = Payload(primary=fbx, extras={"default_asset_name": bridge._default_asset_name(objs)})
        return bridge.deliverer.deliver(bridge, payload, req(params))

    result = deliver({"ASSETS_SUBDIR": "Models", "LAUNCH_EDITOR": False})
    expected = os.path.join(project, "Assets", "Models", "UnityHero.fbx")
    check("deliver copied FBX into Assets/Models/<name>.fbx",
          result is not None and os.path.normpath(result["asset"]) == os.path.normpath(expected)
          and os.path.isfile(expected) and os.path.getsize(expected) > 0,
          str(result))
    check("deliver did not launch the editor", result is not None and result["launched"] is False)

    # explicit ASSET_NAME is sanitized
    result2 = deliver({"ASSET_NAME": "Hero/Mesh:1"})
    check("explicit ASSET_NAME sanitized to Hero_Mesh_1.fbx",
          result2 is not None and os.path.basename(result2["asset"]) == "Hero_Mesh_1.fbx")

    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

except Exception as e:
    lines.append(f"FAIL setup: {e!r}")
    lines.append(traceback.format_exc())

ok = all(line.startswith("OK") for line in lines)
for line in lines:
    print(line)
print(f"===RESULT: {'PASS' if ok else 'FAIL'}===")
