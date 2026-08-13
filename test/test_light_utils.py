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

    # ---- world importance-sampling resolution (Cycles sample_map_resolution = Arnold Resolution) ----
    bpy.context.scene.render.engine = "CYCLES"  # world.cycles is a Cycles-addon namespace
    applied = btk.set_world_importance_resolution(2048)
    w = bpy.context.scene.world
    check("importance resolution switches to MANUAL and applies the map size",
          applied == 2048 and w.cycles.sampling_method == "MANUAL"
          and w.cycles.sample_map_resolution == 2048, f"applied={applied}")
    check("get_world_importance_resolution round-trips in MANUAL",
          btk.get_world_importance_resolution() == 2048)
    # 0/None restores AUTOMATIC sampling and reports None (Cycles sizes the map itself)
    check("importance resolution 0 restores AUTOMATIC + reports None",
          btk.set_world_importance_resolution(0) is None
          and w.cycles.sampling_method == "AUTOMATIC"
          and btk.get_world_importance_resolution() is None)

    # ---- scale_light_energy: the Maya->Cycles unit dial ----------------------
    # Relative, so it corrects the crossing without flattening the artist's own
    # relative brightnesses -- and must not compound on a SHARED light datablock.
    # The factory-startup scene ships its own "Light" -- clear it so "every light in
    # the file" is a set this test actually controls.
    for _stray in [o for o in bpy.data.objects if o.type == "LIGHT"]:
        bpy.data.objects.remove(_stray, do_unlink=True)
    key = bpy.data.objects.new("KeyLight", bpy.data.lights.new("KeyData", "POINT"))
    bpy.context.scene.collection.objects.link(key)
    key.data.energy = 100.0
    rim = bpy.data.objects.new("RimLight", bpy.data.lights.new("RimData", "POINT"))
    bpy.context.scene.collection.objects.link(rim)
    rim.data.energy = 25.0

    scaled = btk.LightUtils.scale_light_energy(2.0)
    check("scale_light_energy reports every light's NEW power",
          scaled == {"KeyLight": 200.0, "RimLight": 50.0}, f"{scaled}")
    check("relative brightnesses are preserved (4:1 before and after)",
          scaled["KeyLight"] / scaled["RimLight"] == 4.0)
    check("1.0 is a no-op",
          btk.LightUtils.scale_light_energy(1.0) == scaled)

    # A linked duplicate shares the datablock; scaling is relative, so touching it
    # once per OBJECT would square the multiplier instead of applying it.
    shared = bpy.data.objects.new("KeyLightCopy", key.data)
    bpy.context.scene.collection.objects.link(shared)
    both = btk.LightUtils.scale_light_energy(2.0, [key, shared])
    check("a shared light datablock scales once, not once per user",
          both == {"KeyLight": 400.0, "KeyLightCopy": 400.0}, f"{both}")
    check("an explicit subset leaves the others alone", rim.data.energy == 50.0)
    check("no lights matched -> empty, no raise",
          btk.LightUtils.scale_light_energy(2.0, []) == {})

    # ---- lights_from_records: rebuild a sender's lights onto placed empties -----
    import mathutils

    for _stray in [o for o in bpy.data.objects if o.type in {"LIGHT", "EMPTY"}]:
        bpy.data.objects.remove(_stray, do_unlink=True)

    # An empty standing in for what an FBX import placed: right position, and the
    # WRONG orientation an interchange format's light-axis convention leaves behind.
    placed = bpy.data.objects.new("keyLight", None)
    bpy.context.scene.collection.objects.link(placed)
    placed.location = (0.0, 0.0, 2.5)
    placed.rotation_euler = (1.5707963, 0.0, 0.0)  # local -Z points +Y, not down

    built = btk.LightUtils.lights_from_records(
        [
            {
                "name": "keyLight",
                "type": "SPOT",
                "color": [1.0, 0.5, 0.25],
                "energy": 1000.0,
                "aim": [0.0, -1.0, 0.0],  # straight down in a Y-UP sender's axes
                "axis_up": "Y",
                "spot_size": 1.0471976,
                "spot_blend": 0.25,
            }
        ]
    )
    check("lights_from_records reports {record: object}",
          built == {"keyLight": "keyLight"}, f"{built}")
    # The name is a join key downstream, so a ".001" from the empty still existing
    # when the lamp was created would be a real defect, not cosmetic.
    check("the lamp TOOK the empty's name (no .001 suffix)",
          "keyLight" in bpy.data.objects
          and bpy.data.objects["keyLight"].type == "LIGHT"
          and "keyLight.001" not in bpy.data.objects)

    lamp = bpy.data.objects["keyLight"]
    check("position comes from the placed empty",
          tuple(round(v, 4) for v in lamp.location) == (0.0, 0.0, 2.5),
          f"{tuple(lamp.location)}")
    aimed = (lamp.matrix_world.to_3x3() @ mathutils.Vector((0, 0, -1))).normalized()
    check("aim OVERRIDES the empty's orientation, Y-up converted to Z-up",
          all(abs(a - b) < 1e-4 for a, b in zip(aimed, (0.0, 0.0, -1.0))),
          f"{tuple(round(v, 4) for v in aimed)}")
    check("spot parameters applied",
          lamp.data.type == "SPOT"
          and abs(lamp.data.spot_size - 1.0471976) < 1e-5
          and abs(lamp.data.spot_blend - 0.25) < 1e-6)
    check("colour and energy applied",
          tuple(round(c, 3) for c in lamp.data.color) == (1.0, 0.5, 0.25)
          and lamp.data.energy == 1000.0)

    # An AREA light sizes from the record's LOCAL extent times the empty's world
    # scale, so it cannot end up in different units from the scene it lights.
    scaled = bpy.data.objects.new("areaLight", None)
    bpy.context.scene.collection.objects.link(scaled)
    scaled.scale = (3.0, 0.5, 1.0)
    btk.LightUtils.lights_from_records(
        [{"name": "areaLight", "type": "AREA", "energy": 10.0,
          "shape": "RECTANGLE", "local_size": [2.0, 2.0]}]
    )
    area = bpy.data.objects["areaLight"].data
    check("area size = local extent x the empty's world scale",
          abs(area.size - 6.0) < 1e-4 and abs(area.size_y - 1.0) < 1e-4,
          f"size={area.size} size_y={area.size_y}")

    check("a record naming no object is skipped, not raised",
          btk.LightUtils.lights_from_records([{"name": "nope", "type": "POINT"}]) == {})

    # Anything parented under the light must survive the swap in place: removing the
    # empty would re-root the child, and Blender keeps its LOCAL matrix when that
    # happens, so it would silently jump to the origin.
    host = bpy.data.objects.new("hostLight", None)
    bpy.context.scene.collection.objects.link(host)
    host.location = (1.0, 2.0, 3.0)
    kid = bpy.data.objects.new("lightChild", None)
    bpy.context.scene.collection.objects.link(kid)
    kid.parent = host
    bpy.context.view_layer.update()
    kid_world = kid.matrix_world.copy()

    btk.LightUtils.lights_from_records(
        [{"name": "hostLight", "type": "POINT", "energy": 5.0,
          "aim": [0.0, -1.0, 0.0], "axis_up": "Y"}]
    )
    bpy.context.view_layer.update()
    check("a child of the light is re-parented onto it, not re-rooted",
          bpy.data.objects["lightChild"].parent is bpy.data.objects["hostLight"],
          f"{bpy.data.objects['lightChild'].parent}")
    check("the child keeps its world transform through the swap",
          all(abs(a - b) < 1e-5 for a, b in zip(
              bpy.data.objects["lightChild"].matrix_world.translation,
              kid_world.translation)),
          f"{tuple(bpy.data.objects['lightChild'].matrix_world.translation)}")

    # --- world_emits: "is the world a light source?" ------------------------
    # Cycles' analogue of an aiSkyDomeLight, so an HDRI-only scene has no LIGHT
    # object yet is genuinely lit. Backs the Lightmap Baker's pre-bake guard.
    w = bpy.data.worlds.new("emit_probe")
    w.use_nodes = True
    bg = next(n for n in w.node_tree.nodes if n.type == "BACKGROUND")

    bg.inputs["Strength"].default_value = 1.0
    bg.inputs["Color"].default_value = (0.5, 0.5, 0.5, 1.0)
    check("world_emits: a lit background is a light source",
          btk.LightUtils.world_emits(w) is True)

    bg.inputs["Strength"].default_value = 0.0
    check("world_emits: zero strength is not",
          btk.LightUtils.world_emits(w) is False)

    bg.inputs["Strength"].default_value = 1.0
    bg.inputs["Color"].default_value = (0.0, 0.0, 0.0, 1.0)
    check("world_emits: a black background is not",
          btk.LightUtils.world_emits(w) is False)

    check("world_emits: no world at all is not",
          btk.LightUtils.world_emits(None) is False)

    # A linked Strength/Color (HDRI texture, driver, node group) can't be read
    # statically -- assume lit rather than cry "unlit" at a correct setup.
    bg.inputs["Color"].default_value = (0.5, 0.5, 0.5, 1.0)
    tex = w.node_tree.nodes.new("ShaderNodeTexEnvironment")
    w.node_tree.links.new(tex.outputs["Color"], bg.inputs["Color"])
    check("world_emits: a linked background input is assumed lit",
          btk.LightUtils.world_emits(w) is True)

    # ...but a READABLE zero strength is definitive, whatever drives the
    # colour -- the linked-input escape hatch must not outrank it.
    bg.inputs["Strength"].default_value = 0.0
    check("world_emits: zero strength beats a linked colour (HDRI at 0 is dark)",
          btk.LightUtils.world_emits(w) is False)

    # The flat-colour fallback is for a TREELESS world. It can't be built on 5.x
    # (every world ships a tree, and `use_nodes = False` is deprecated and does
    # not stick -- probed), so the tree's presence, never `use_nodes`, is what
    # selects the branch. Pinned here so the deprecated attribute can't creep back.
    bg.inputs["Strength"].default_value = 1.0
    w.use_nodes = False
    check("world_emits: ignores the deprecated use_nodes flag (reads the tree)",
          w.node_tree is not None and btk.LightUtils.world_emits(w) is True,
          f"use_nodes read back as {w.use_nodes}")

    # Omitted arg = "ask the scene"; an explicit None = "no world" (they must
    # stay distinguishable -- callers forward a possibly-None scene.world).
    bg.inputs["Strength"].default_value = 1.0
    scene_world_backup = bpy.context.scene.world
    bpy.context.scene.world = w
    check("world_emits: defaults to the scene world",
          btk.LightUtils.world_emits() is True)
    bpy.context.scene.world = None
    check("world_emits: a scene with no world is not lit by one",
          btk.LightUtils.world_emits() is False)
    bpy.context.scene.world = scene_world_backup
    bpy.data.worlds.remove(w)

    # --- set_world_environment: flat-ambient path --------------------------
    # Untested until now, and it hand-rolled the find-or-create-world-and-tree
    # dance (incl. an UNGUARDED deprecated `use_nodes = True`) instead of
    # calling _world_node_tree. These pin the rerouted behaviour, especially
    # the no-world-at-all case the hand-rolled version existed to cover.
    bpy.context.scene.world = None
    desc = btk.LightUtils.set_world_environment(color=(0.1, 0.2, 0.3), strength=2.0)
    world = bpy.context.scene.world
    check("set_world_environment creates a world when the scene has none",
          world is not None and world.node_tree is not None, f"{world}")
    bg = next((n for n in world.node_tree.nodes if n.type == "BACKGROUND"), None)
    check("flat ambient wires a Background node with the requested colour/strength",
          bg is not None
          and all(abs(a - b) < 1e-5 for a, b in zip(bg.inputs["Color"].default_value[:3],
                                                    (0.1, 0.2, 0.3)))
          and abs(bg.inputs["Strength"].default_value - 2.0) < 1e-5,
          f"{tuple(bg.inputs['Color'].default_value) if bg else None}")
    check("the Background reaches the World Output",
          bool(bg.outputs["Background"].links))
    check("it reports what it applied", "flat ambient" in desc and "2.0" in desc, desc)
    check("a flat-ambient world is a light source",
          btk.LightUtils.world_emits(world) is True)
    # Re-running on an EXISTING world updates in place rather than stacking nodes.
    btk.LightUtils.set_world_environment(color=(0.5, 0.5, 0.5), strength=1.0)
    bgs = [n for n in bpy.context.scene.world.node_tree.nodes if n.type == "BACKGROUND"]
    check("a second call updates in place (one Background node)", len(bgs) == 1,
          f"{len(bgs)} background node(s)")

    for p in paths:
        os.remove(p)
    os.rmdir(tmp_dir)

except Exception:
    traceback.print_exc()
    lines.append("FAIL unhandled exception")

print("\n".join(lines))
ok = all(l.startswith("OK") for l in lines) and lines
print(f"===RESULT: {'PASS' if ok else 'FAIL'}=== ({sum(1 for l in lines if l.startswith('OK'))}/{len(lines)})")
