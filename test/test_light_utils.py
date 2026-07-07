"""blendertk light_utils (world HDRI) headless test.
Run: blender --background --factory-startup --python blendertk/test/test_light_utils.py
"""
import sys, os, math, tempfile, traceback

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

    # generated .hdr fixtures (cleaned in teardown)
    tmp_dir = tempfile.mkdtemp(prefix="btk_hdr_")
    paths = []
    for n in ("env_a", "env_b"):
        img = bpy.data.images.new(n, 8, 4, float_buffer=True)
        img.file_format = "HDR"
        p = os.path.join(tmp_dir, f"{n}.hdr")
        img.filepath_raw = p
        img.save()
        paths.append(p)

    check("no btk environment in a fresh scene", btk.get_world_hdri() is None)

    # ---- set: builds the node rig and applies levels
    world = btk.set_world_hdri(paths[0], strength=2.0, rotation=90.0, visible=True)
    check("set returns the scene world", world is bpy.context.scene.world)
    state = btk.get_world_hdri()
    check("get reports the map", state is not None
          and os.path.basename(state["filepath"]) == "env_a.hdr",
          f"state={state}")
    check("strength applied", abs(state["strength"] - 2.0) < 1e-6)
    check("rotation roundtrips (degrees)", abs(state["rotation"] - 90.0) < 1e-4,
          f"rot={state['rotation']:.3f}")
    check("visible -> opaque background", state["visible"]
          and not bpy.context.scene.render.film_transparent)

    nt = world.node_tree
    env = nt.nodes.get("btk_hdri_env")
    bg = next(n for n in nt.nodes if n.bl_idname == "ShaderNodeBackground")
    out = next(n for n in nt.nodes if n.bl_idname == "ShaderNodeOutputWorld")
    # NB: bpy recreates RNA wrappers per access — compare with ==, never `is`.
    check("env -> background -> output linked",
          any(l.from_node == env and l.to_node == bg for l in nt.links)
          and any(l.from_node == bg and l.to_node == out for l in nt.links))
    mapping = nt.nodes.get("btk_hdri_mapping")
    check("mapping drives the env vector",
          any(l.from_node == mapping and l.to_node == env for l in nt.links))
    check("mapping rotation in radians",
          abs(mapping.inputs["Rotation"].default_value[2] - math.radians(90)) < 1e-6)

    # ---- update in place: same nodes, new map + levels
    node_count = len(nt.nodes)
    btk.set_world_hdri(paths[1], strength=0.5, rotation=180.0, visible=False)
    check("update reuses the node rig", len(nt.nodes) == node_count,
          f"{node_count} -> {len(nt.nodes)}")
    state = btk.get_world_hdri()
    check("update swaps the map", os.path.basename(state["filepath"]) == "env_b.hdr")
    check("invisible -> transparent film", not state["visible"]
          and bpy.context.scene.render.film_transparent)

    # ---- levels-only update (filepath=None keeps the map)
    btk.set_world_hdri(None, strength=3.0, rotation=45.0, visible=True)
    state = btk.get_world_hdri()
    check("levels-only update keeps the map",
          os.path.basename(state["filepath"]) == "env_b.hdr")
    check("levels-only update applies", abs(state["strength"] - 3.0) < 1e-6
          and abs(state["rotation"] - 45.0) < 1e-4 and state["visible"])

    # ---- clear removes the managed env / mapping / coord nodes (Clear Network)
    check("clear removes a managed environment", btk.clear_world_hdri() is True)
    check("get is None after clear", btk.get_world_hdri() is None)
    check("cleared nodes are gone",
          nt.nodes.get("btk_hdri_env") is None
          and nt.nodes.get("btk_hdri_mapping") is None)
    check("clear on an already-clear world returns False",
          btk.clear_world_hdri() is False)

    # ---- levels-only with nothing assigned -> ValueError (env node already cleared above)
    for img in list(bpy.data.images):
        bpy.data.images.remove(img)
    try:
        btk.set_world_hdri(None)
        check("levels-only without a map raises", False)
    except ValueError:
        check("levels-only without a map raises", True)

    # ---- world ray visibility (Cycles diffuse/glossy — the aiDiffuse/aiSpecular analogue) ----
    if bpy.context.scene.world is None:
        bpy.context.scene.world = bpy.data.worlds.new("W")
    rv = btk.set_world_ray_visibility(diffuse=False, glossy=True)
    check("ray visibility reports the applied state",
          rv is not None and rv["diffuse"] is False and rv["glossy"] is True, str(rv))
    check("get_world_ray_visibility round-trips",
          btk.get_world_ray_visibility() == {"diffuse": False, "glossy": True})
    # partial update leaves the unspecified component untouched
    btk.set_world_ray_visibility(diffuse=True)
    check("partial ray-visibility update keeps glossy",
          btk.get_world_ray_visibility() == {"diffuse": True, "glossy": True})

    for p in paths:
        os.remove(p)
    os.rmdir(tmp_dir)

except Exception:
    traceback.print_exc()
    lines.append("FAIL unhandled exception")

print("\n".join(lines))
ok = all(l.startswith("OK") for l in lines) and lines
print(f"===RESULT: {'PASS' if ok else 'FAIL'}=== ({sum(1 for l in lines if l.startswith('OK'))}/{len(lines)})")
