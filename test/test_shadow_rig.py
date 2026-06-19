"""blendertk.rig_utils.shadow_rig headless test — projected-shadow rig (driver-based).
Run: blender --background --factory-startup --python blendertk/test/test_shadow_rig.py

Verifies the rig BUILDS (source/contact/plane/material/silhouette + keyable props), the transform
+ opacity DRIVERS are wired on the right channels, and they EVALUATE (Z-up: plane stretches toward
the ground away from the light; orbit rotates about Z). Visual fidelity of the texture/material is
not asserted (headless) — only its structure, per the parity plan.
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
    for prop, val in (("shadowIntensity", 1.0), ("falloffPower", 1.2), ("scaleInfluence", 0.0)):
        check(f"plane has {prop}={val}", approx(float(p.get(prop, -99)), val), f"{p.get(prop)}")
    check("plane basePlaneSize stamped > 0", float(p.get("basePlaneSize", 0)) > 0,
          f"{p.get('basePlaneSize')}")

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
    # All driver expressions are branchless (no ternary/comparison -> Blender fast parser).
    exprs = [d.driver.expression for o in (p, nt) for d in (o.animation_data.drivers if o.animation_data else [])]
    check("driver expressions are branchless (no ' if '/comparison)",
          all(" if " not in e and "<" not in e and ">" not in e for e in exprs), f"{len(exprs)} exprs")

    # ---- evaluate (light at (5,5,10), contact (0,0,-1), G=0): sx=sy=1.5, loc≈(-0.55,-0.55,0.005) ----
    pe = ev(p)
    check("stretch scale.x ≈ 1.5", approx(pe.scale[0], 1.5), f"{pe.scale[0]:.4f}")
    check("stretch scale.y ≈ 1.5", approx(pe.scale[1], 1.5), f"{pe.scale[1]:.4f}")
    check("stretch keeps scale.z = 1", approx(pe.scale[2], 1.0), f"{pe.scale[2]:.4f}")
    check("stretch location.x ≈ -0.55", approx(pe.matrix_world.translation[0], -0.55, 3e-2),
          f"{pe.matrix_world.translation[0]:.4f}")
    check("stretch sits on the ground (z ≈ 0.005)", approx(pe.matrix_world.translation[2], 0.005),
          f"{pe.matrix_world.translation[2]:.4f}")

    # ---- moving the source farther/lower grows the stretch (monotonic) ----
    s = rig.light
    s.location = (5, 5, 3)  # lower -> larger ratio -> bigger scale
    bpy.context.view_layer.update()
    check("lowering the source increases stretch", ev(p).scale[0] > 1.5 + 1e-3,
          f"{ev(p).scale[0]:.4f}")
    s.location = (5, 5, 10)
    bpy.context.view_layer.update()

    # ---- opacity driver evaluated into (0,1] (intensity/maxStretch^power * heightFade) ----
    op = nt.nodes["opacity"].outputs[0].default_value
    check("opacity driven into (0, 1]", 0.0 < op <= 1.0, f"{op:.4f}")

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
    rz = ev(p).rotation_euler[2]
    check("orbit rotates about Z for an off-axis light (nonzero)", abs(rz) > 1e-3, f"rz={rz:.4f}")

    # ============================ RE-ENTRANCY + GUARD ============================
    n_before = len([d for d in p.animation_data.drivers if d.data_path == "location" and d.array_index == 0])
    ShadowRig.create([c], light_pos=(5, 5, 10), texture_res=64, mode="orbit")
    p2 = bpy.data.objects.get("Box_shadow") or p
    n_after = len([d for d in p2.animation_data.drivers if d.data_path == "location" and d.array_index == 0])
    check("rebuild does not stack drivers", n_before == 1 and n_after == 1, f"{n_before}->{n_after}")

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
