"""blendertk.rig_utils.shadow_rig headless test — projected-shadow rig (driver-based).
Run: blender --background --factory-startup --python blendertk/test/test_shadow_rig.py

Verifies the rig BUILDS (source/contact/plane/material/silhouette + keyable props), the transform
+ opacity DRIVERS are wired on the right channels, and they EVALUATE (Z-up: plane stretches toward
the ground away from the light; orbit rotates about Z with the head pointing away from the light;
the anchor slides away from the light and the opacity fades as the target rises off the ground),
and BAKE strips the drivers and lays keyframes. Visual fidelity of the texture/material is not
asserted (headless) — only its structure, per the parity plan.

Reference values (cube 2x2x2 at origin, light (5,5,10), G=0):
  plane_size = 2.2, objectHeight = 2, contact = (0,0,-1)
  sx = 1 + (objH * |Cx-Lx|/relH)/size = 1 + (2*0.5)/2.2          = 1.4545
  k  = (Lz-G)/(Lz-Cz) = 10/11                                     = 0.9091
  Sx = Lx + (Cx-Lx)*k = 5 - 5*0.9091                              = 0.4545
  loc.x = Sx + 1.1*(1-sx) = 0.4545 - 0.5                          = -0.0455
  orbit rz = atan2(Lx-Cx, Cy-Ly) = atan2(5, -5)                   = 2.3562
"""
import sys, os, traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for p in (REPO, os.path.join(MONO, "pythontk")):
    if p not in sys.path:
        sys.path.insert(0, p)

lines = []
def check(name, cond, detail=""):
    lines.append(f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}")

def approx(a, b, tol=2e-2):
    return abs(a - b) <= tol

try:
    import bpy
    from blendertk.rig_utils.shadow_rig import ShadowRig, ShadowRigSlots

    def reset():
        bpy.ops.object.select_all(action="DESELECT")
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)
        for blk in (bpy.data.materials, bpy.data.images):
            for d in list(blk):
                blk.remove(d)

    def cube(name="Cube", loc=(0, 0, 0)):
        # 2x2x2 cube (verts -1..1) via a mesh primitive (no bpy.ops to stay context-clean).
        import bmesh
        me = bpy.data.meshes.new(f"{name}_mesh")
        bm = bmesh.new(); bmesh.ops.create_cube(bm, size=2.0); bm.to_mesh(me); bm.free()
        o = bpy.data.objects.new(name, me); o.location = loc
        bpy.context.collection.objects.link(o)
        return o

    def drv(obj, data_path, index):
        ad = getattr(obj, "animation_data", None)
        if not ad:
            return None
        return next((d for d in ad.drivers
                     if d.data_path == data_path and (index is None or d.array_index == index)), None)

    def ev(o):
        return o.evaluated_get(bpy.context.evaluated_depsgraph_get())

    # ============================ STRETCH MODE ============================
    reset()
    c = cube("Box")
    bpy.context.view_layer.update()
    rig = ShadowRig.create([c], light_pos=(5, 5, 10), texture_res=64, mode="stretch", axis="auto")

    # ---- build ----
    check("source empty created", rig.light is not None and rig.light.name == "shadow_source")
    check("contact empty parented to target", rig.contact is not None and rig.contact.parent is c)
    check("contact sits at footprint min-Z", approx(rig.contact.matrix_world.translation[2], -1.0),
          f"z={rig.contact.matrix_world.translation[2]:.3f}")
    check("shadow plane created", rig.shadow_plane is not None)
    check("plane grouped under *_shadow_grp",
          rig.shadow_plane.parent is not None and rig.shadow_plane.parent.name.endswith("_shadow_grp"))

    # ---- keyable props (Maya parity) ----
    p = rig.shadow_plane
    for prop, val in (("shadowIntensity", 1.0), ("falloffPower", 1.2), ("scaleInfluence", 0.0),
                      ("maxStretch", 4.0)):
        check(f"plane has {prop}={val}", approx(float(p.get(prop, -99)), val), f"{p.get(prop)}")
    check("plane basePlaneSize stamped > 0", float(p.get("basePlaneSize", 0)) > 0,
          f"{p.get('basePlaneSize')}")
    check("plane objectHeight stamped (cube = 2)", approx(float(p.get("objectHeight", 0)), 2.0),
          f"{p.get('objectHeight')}")
    check("plane fadeHeight defaults to 2 x objectHeight", approx(float(p.get("fadeHeight", 0)), 4.0),
          f"{p.get('fadeHeight')}")

    # ---- silhouette + material structure ----
    check("silhouette PNG written", bool(rig.texture_path) and os.path.exists(rig.texture_path),
          f"{rig.texture_path}")
    check("image datablock loaded", rig.image is not None and tuple(rig.image.size) == (64, 64))
    nt = rig.material.node_tree
    nodes = {n.bl_idname for n in nt.nodes}
    check("material is unlit emission + transparent + mix",
          {"ShaderNodeEmission", "ShaderNodeBsdfTransparent", "ShaderNodeMixShader",
           "ShaderNodeTexImage"} <= nodes, f"{sorted(nodes)}")
    check("material has driven 'opacity' value node", "opacity" in nt.nodes)
    check("plane uses the shadow material",
          len(p.data.materials) == 1 and p.data.materials[0] is rig.material)

    # ---- drivers on the right channels (stretch: loc X/Y + scale X/Y; rot Z static) ----
    check("stretch drives location[0]", drv(p, "location", 0) is not None)
    check("stretch drives location[1]", drv(p, "location", 1) is not None)
    check("stretch drives scale[0]", drv(p, "scale", 0) is not None)
    check("stretch drives scale[1]", drv(p, "scale", 1) is not None)
    check("stretch leaves rotation undriven (static 0)", drv(p, "rotation_euler", 2) is None
          and approx(p.rotation_euler[2], 0.0))
    check("opacity driver on material node tree",
          drv(nt, 'nodes["opacity"].outputs[0].default_value', None) is not None)
    # All driver expressions are branchless (no ternary/comparison -> Blender fast parser),
    # and under Blender's 255-char driver-expression cap.
    exprs = [d.driver.expression for o in (p, nt) for d in (o.animation_data.drivers if o.animation_data else [])]
    check("driver expressions are branchless (no ' if '/comparison)",
          all(" if " not in e and "<" not in e and ">" not in e for e in exprs), f"{len(exprs)} exprs")
    check("driver expressions fit the 255-char cap",
          all(len(e) <= 255 for e in exprs), f"max={max(len(e) for e in exprs)}")

    # ---- evaluate (light (5,5,10), contact (0,0,-1), G=0): sx=sy=1.4545, loc.x=-0.0455 ----
    pe = ev(p)
    check("stretch scale.x ≈ 1.4545 (objectHeight-proportional)", approx(pe.scale[0], 1.4545),
          f"{pe.scale[0]:.4f}")
    check("stretch scale.y ≈ 1.4545", approx(pe.scale[1], 1.4545), f"{pe.scale[1]:.4f}")
    check("stretch keeps scale.z = 1", approx(pe.scale[2], 1.0), f"{pe.scale[2]:.4f}")
    check("stretch location.x ≈ -0.0455 (heel at projected anchor)",
          approx(pe.matrix_world.translation[0], -0.0455, 3e-2),
          f"{pe.matrix_world.translation[0]:.4f}")
    check("stretch sits on the ground (z ≈ 0.01)", approx(pe.matrix_world.translation[2], 0.01),
          f"{pe.matrix_world.translation[2]:.4f}")

    # ---- moving the source farther/lower grows the stretch (monotonic) ----
    s = rig.light
    s.location = (5, 5, 3)  # lower -> larger ratio -> bigger scale
    bpy.context.view_layer.update()
    check("lowering the source increases stretch", ev(p).scale[0] > 1.4545 + 1e-3,
          f"{ev(p).scale[0]:.4f}")
    s.location = (5, 5, 10)
    bpy.context.view_layer.update()

    # ---- opacity driver evaluated into (0,1] (intensity/maxStretch^power * fades) ----
    op_grounded = nt.nodes["opacity"].outputs[0].default_value
    check("opacity driven into (0, 1]", 0.0 < op_grounded <= 1.0, f"{op_grounded:.4f}")

    # ---- raising the target: anchor slides away from the light; opacity fades ----
    # cube at z=+3 -> contact z=2: k = 10/8 = 1.25 -> Sx = -1.25 -> loc.x = -1.75;
    # riseFade = 1 - 2/fadeHeight(4) = 0.5 -> opacity halves.
    loc_x_grounded = ev(p).matrix_world.translation[0]
    c.location = (0, 0, 3)
    bpy.context.view_layer.update()
    loc_x_risen = ev(p).matrix_world.translation[0]
    op_risen = nt.nodes["opacity"].outputs[0].default_value
    check("rising target slides the shadow away from the light",
          approx(loc_x_risen, -1.75, 5e-2) and loc_x_risen < loc_x_grounded - 0.5,
          f"{loc_x_grounded:.4f} -> {loc_x_risen:.4f}")
    check("rising target fades the shadow (riseFade = 0.5)",
          approx(op_risen, op_grounded * 0.5, 3e-2) and op_risen < op_grounded,
          f"{op_grounded:.4f} -> {op_risen:.4f}")
    c.location = (0, 0, 0)
    bpy.context.view_layer.update()

    # ============================ ORBIT MODE ============================
    reset()
    c = cube("Box")
    bpy.context.view_layer.update()
    rig = ShadowRig.create([c], light_pos=(5, 5, 10), texture_res=64, mode="orbit", axis="auto")
    p, nt = rig.shadow_plane, rig.material.node_tree
    check("orbit drives rotation_euler[2]", drv(p, "rotation_euler", 2) is not None)
    check("orbit drives location[0]/[1] + scale[1]",
          drv(p, "location", 0) and drv(p, "location", 1) and drv(p, "scale", 1))
    check("orbit leaves scale.x static (= 1)", drv(p, "scale", 0) is None and approx(p.scale[0], 1.0))
    # Head (local +Y) must point AWAY from the light: rz = atan2(Lx-Cx, Cy-Ly) = atan2(5,-5).
    rz = ev(p).rotation_euler[2]
    check("orbit heads away from the light (rz ≈ 2.356, not the mirrored -0.785)",
          approx(rz, 2.3562, 1e-2), f"rz={rz:.4f}")
    # Heel-origin mesh: local min-Y of the orbit plane sits at the origin (anchor pivot).
    check("orbit plane origin on the heel edge",
          approx(min(v.co.y for v in p.data.vertices), 0.0),
          f"min_y={min(v.co.y for v in p.data.vertices):.3f}")

    # ============================ BAKE ============================
    scene = bpy.context.scene
    baked = rig.bake(1, 3)
    check("bake returns the plane", bool(baked) and baked[0] is p)
    check("bake strips the transform drivers",
          not [d for d in (p.animation_data.drivers if p.animation_data else [])])
    check("bake strips the opacity driver",
          drv(nt, 'nodes["opacity"].outputs[0].default_value', None) is None)
    def action_fcurves(act):
        """Action fcurves across Blender's API break: legacy flat ``act.fcurves`` (< 4.4) vs
        layered/slotted actions (5.x: layer -> strip -> channelbag(slot).fcurves)."""
        if act is None:
            return []
        if hasattr(act, "fcurves"):
            return list(act.fcurves)
        fcs = []
        for layer in act.layers:
            for strip in layer.strips:
                for slot in act.slots:
                    cb = strip.channelbag(slot)
                    if cb:
                        fcs.extend(cb.fcurves)
        return fcs

    act = p.animation_data.action if p.animation_data else None
    fcs = action_fcurves(act)
    loc_fc = next((f for f in fcs if f.data_path == "location" and f.array_index == 0), None)
    check("bake lays location keys over the range", loc_fc is not None
          and len(loc_fc.keyframe_points) == 3,
          f"{len(loc_fc.keyframe_points) if loc_fc else 0} keys")
    rot_fc = next((f for f in fcs if f.data_path == "rotation_euler" and f.array_index == 2), None)
    check("baked rotation preserves the driven pose (rz ≈ 2.356)",
          rot_fc is not None and approx(rot_fc.evaluate(2), 2.3562, 1e-2),
          f"{rot_fc.evaluate(2):.4f}" if rot_fc else "no fcurve")

    # ============================ EXPORT METADATA ============================
    # create()/bake() publish the shadow_metadata channel on the data_export
    # carrier (the Scene Exporter hand-off contract, mirror of mayatk's).
    import json as _json
    from blendertk.node_utils.data_nodes import DataNodes
    raw = DataNodes.get_export_string(ShadowRig.SHADOW_METADATA)
    payload = _json.loads(raw) if raw else {}
    recs = {r["name"]: r for r in payload.get("planes", [])}
    check("shadow_metadata published on the data_export carrier",
          payload.get("version") == 1 and p.name in recs, f"{raw}")
    check("record carries the silhouette filename",
          recs.get(p.name, {}).get("texture") == "Box_shadow.png",
          f"{recs.get(p.name)}")
    check("record carries the authored intensity",
          approx(recs.get(p.name, {}).get("intensity", -1), 1.0))

    # ============================ RE-ENTRANCY + GUARD ============================
    reset()
    c = cube("Box")
    bpy.context.view_layer.update()
    rig = ShadowRig.create([c], light_pos=(5, 5, 10), texture_res=64, mode="orbit")
    p = rig.shadow_plane
    n_before = len([d for d in p.animation_data.drivers if d.data_path == "location" and d.array_index == 0])
    ShadowRig.create([c], light_pos=(5, 5, 10), texture_res=64, mode="orbit")
    p2 = bpy.data.objects.get("Box_shadow") or p
    n_after = len([d for d in p2.animation_data.drivers if d.data_path == "location" and d.array_index == 0])
    check("rebuild does not stack drivers", n_before == 1 and n_after == 1, f"{n_before}->{n_after}")

    # ---- explicit world-axis silhouette still works (Y/Z-swap path) ----
    reset()
    c = cube("Box")
    bpy.context.view_layer.update()
    rig = ShadowRig.create([c], texture_res=32, mode="stretch", axis="y")
    check("explicit axis='y' (top-down) silhouette builds",
          rig.image is not None and tuple(rig.image.size) == (32, 32))

    # ============================ DELETE ============================
    # delete_rigs tears down the WHOLE rig (mirror of mayatk's): plane +
    # group + contact empty, material/image datablocks, the PNG on disk
    # (delete_textures), and republishes (clears) the metadata channel —
    # while the target and the shared source empty survive.
    reset()
    c = cube("Box")
    bpy.context.view_layer.update()
    rig = ShadowRig.create([c], light_pos=(5, 5, 10), texture_res=32, mode="stretch")
    tex = rig.texture_path
    deleted = ShadowRig.delete_rigs([rig.shadow_plane], delete_textures=True)
    check("delete_rigs returns the plane name", deleted == ["Box_shadow"], f"{deleted}")
    check("delete removes plane/group/contact",
          not any(bpy.data.objects.get(n)
                  for n in ("Box_shadow", "Box_shadow_grp", "Box_contact")),
          f"{[o.name for o in bpy.data.objects]}")
    check("delete keeps target + shared source",
          bpy.data.objects.get("Box") is not None
          and bpy.data.objects.get("shadow_source") is not None)
    check("delete frees material + image datablocks",
          bpy.data.materials.get("Box_shadow_mat") is None
          and bpy.data.images.get("Box_shadow") is None)
    check("delete_textures removes the PNG", not (tex and os.path.exists(tex)))
    check("delete clears the metadata channel",
          DataNodes.get_export_string(ShadowRig.SHADOW_METADATA) is None)

    # A BAKED rig (drivers already stripped) still tears down fully.
    rig = ShadowRig.create([c], light_pos=(5, 5, 10), texture_res=32, mode="stretch")
    rig.bake(1, 2)
    rig.delete()
    check("baked rig still tears down fully",
          bpy.data.objects.get("Box_shadow") is None
          and bpy.data.objects.get("Box_contact") is None)

    # An overlapping selection (group + its plane child) must not double-list
    # the plane — delete_rigs would hit the second, already-removed entry.
    rig = ShadowRig.create([c], light_pos=(5, 5, 10), texture_res=32, mode="stretch")
    grp = rig.shadow_plane.parent
    found = ShadowRig.find_shadow_planes([grp, rig.shadow_plane])
    check("find_shadow_planes dedups an overlapping selection",
          len(found) == 1, f"{[o.name for o in found]}")
    deleted = ShadowRig.delete_rigs([grp, rig.shadow_plane])
    check("delete_rigs survives an overlapping selection",
          deleted == ["Box_shadow"] and bpy.data.objects.get("Box_shadow") is None,
          f"{deleted}")
    check("second delete on the stale ref no-ops (mirror of Maya)",
          ShadowRig.delete_rigs([rig.shadow_plane]) == [])

    # ============================ FOOTPRINT INCLUDES DESCENDANTS ============================
    # A target empty with a large mesh CHILD: the plane footprint + contact must come from the
    # child geometry, not the empty's meaningless unit bound_box (Maya's exactWorldBoundingBox
    # includes descendants). The child cube spans -2..2, so plane_size ≈ 4*1.1 = 4.4 (> the unit
    # cube's 2.2) and the contact sits at the child's min-Z (-2).
    reset()
    parent = bpy.data.objects.new("Grp", None)  # empty
    bpy.context.collection.objects.link(parent)
    import bmesh
    cme = bpy.data.meshes.new("Big_mesh")
    bm = bmesh.new(); bmesh.ops.create_cube(bm, size=4.0); bm.to_mesh(cme); bm.free()
    child = bpy.data.objects.new("Big", cme)
    bpy.context.collection.objects.link(child)
    child.parent = parent
    bpy.context.view_layer.update()
    rig = ShadowRig.create([parent], texture_res=32, mode="stretch")
    check("footprint reflects descendant geometry (not the empty's unit cube)",
          rig.plane_size > 3.0, f"plane_size={rig.plane_size:.3f}")
    check("contact uses descendant min-Z (-2)",
          approx(rig.contact.matrix_world.translation[2], -2.0),
          f"z={rig.contact.matrix_world.translation[2]:.3f}")

    reset()
    # A plane-less refresh clears the channel (no empty carrier left behind).
    check("refresh clears the channel with no shadow planes",
          ShadowRig.refresh_export_metadata() is None
          and DataNodes.get_export_string(ShadowRig.SHADOW_METADATA) is None)
    try:
        ShadowRig.create([], mode="stretch")
        check("rejects empty target list", False)
    except ValueError:
        check("rejects empty target list", True)

except Exception as e:
    traceback.print_exc()
    check("test harness raised", False, repr(e))

passed = sum(1 for ln in lines if ln.startswith("OK"))
for ln in lines:
    print(ln)
result = "PASS" if all(ln.startswith("OK") for ln in lines) else "FAIL"
print(f"===RESULT: {result}=== ({passed}/{len(lines)})")
