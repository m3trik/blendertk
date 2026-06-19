"""blendertk.mat_utils.render_opacity headless test — per-object opacity (driver + dual-key).
Run: blender --background --factory-startup --python blendertk/test/test_render_opacity.py

Verifies: create adds a keyable 'opacity' prop + drives Principled Alpha (single-user material);
key_fade dual-keys opacity (linear) AND render visibility (stepped) — the Unity-parity invariant;
sync/prepare_for_export mirror opacity→visibility; remove strips every artifact.
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

def approx(a, b, tol=1e-3):
    return abs(a - b) <= tol

try:
    import bpy
    from blendertk.mat_utils.render_opacity._render_opacity import RenderOpacity

    def reset():
        bpy.ops.object.select_all(action="DESELECT")
        for blk in (bpy.data.objects, bpy.data.meshes, bpy.data.materials):
            for d in list(blk):
                blk.remove(d)

    def cube(name="Box"):
        import bmesh
        me = bpy.data.meshes.new(f"{name}_mesh")
        bm = bmesh.new(); bmesh.ops.create_cube(bm, size=2.0); bm.to_mesh(me); bm.free()
        o = bpy.data.objects.new(name, me)
        bpy.context.collection.objects.link(o)
        return o

    def mat(name="M"):
        m = bpy.data.materials.new(name); m.use_nodes = True
        return m

    def fcurve(obj, data_path):
        return RenderOpacity._fcurve(obj, data_path)  # slot-aware (Blender 5.x has no act.fcurves)

    def alpha_driver(m):
        # The engine adds exactly one driver to the material node tree (Principled Alpha).
        ad = getattr(m.node_tree, "animation_data", None)
        return ad.drivers[0] if (ad and ad.drivers) else None

    # ============================ CREATE ============================
    reset()
    c = cube("Box")
    m = mat("Shared")
    c.data.materials.append(m)
    bpy.context.view_layer.update()
    results = RenderOpacity.create([c], mode="attribute")
    check("create returns the object", "Box" in results)
    check("opacity prop seeded (1.0)", RenderOpacity.ATTR_NAME in c and approx(c["opacity"], 1.0),
          f"{c.get('opacity')}")
    drv = alpha_driver(c.data.materials[0])
    check("Principled Alpha driven by a driver", drv is not None)
    check("Alpha driver reads ['opacity'] SINGLE_PROP",
          drv is not None and any(v.type == "SINGLE_PROP" and v.targets[0].data_path == '["opacity"]'
                                  and v.targets[0].id is c for v in drv.driver.variables))

    # ---- opacity drives Alpha (verified via the animated path — the real use case: a keyframed
    # opacity scrubbed by the playhead, which re-evaluates the node-tree driver). ----
    pn = next(n for n in c.data.materials[0].node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    RenderOpacity.key_fade([c], start=1, end=11, direction="out")  # opacity 1 -> 0
    bpy.context.scene.frame_set(6)  # midpoint -> opacity 0.5
    check("Alpha tracks animated opacity (0.5 @ frame 6)",
          approx(pn.inputs["Alpha"].default_value, 0.5, 1e-2),
          f"alpha={pn.inputs['Alpha'].default_value:.4f} opacity={c['opacity']:.4f}")
    bpy.context.scene.frame_set(1)
    RenderOpacity.remove([c])  # clean slate for the next sub-test
    RenderOpacity.create([c])

    # ============================ SHARED MATERIAL -> SINGLE-USER ============================
    reset()
    a, b = cube("A"), cube("B")
    shared = mat("Shared")
    a.data.materials.append(shared)
    b.data.materials.append(shared)
    check("material shared by 2 objects pre-create", shared.users == 2, f"users={shared.users}")
    RenderOpacity.create([a, b])
    check("create made materials single-user (per-object opacity)",
          a.data.materials[0] is not b.data.materials[0], "distinct datablocks")

    # ============================ KEY FADE (dual-key) ============================
    reset()
    c = cube("Fade")
    c.data.materials.append(mat("Fm"))
    RenderOpacity.create([c])
    keyed = RenderOpacity.key_fade([c], start=1, end=20, direction="out")
    check("key_fade returns (name, 'out')", keyed == [("Fade", "out")], f"{keyed}")
    of = fcurve(c, '["opacity"]')
    vf = fcurve(c, "hide_render")
    check("opacity fcurve has 2 keys", of is not None and len(of.keyframe_points) == 2,
          f"{len(of.keyframe_points) if of else 0}")
    check("opacity keys are linear", of is not None and all(k.interpolation == "LINEAR" for k in of.keyframe_points))
    check("visibility (hide_render) fcurve dual-keyed", vf is not None and len(vf.keyframe_points) == 2,
          f"{len(vf.keyframe_points) if vf else 0}")
    check("visibility keys are stepped (CONSTANT)",
          vf is not None and all(k.interpolation == "CONSTANT" for k in vf.keyframe_points))
    # fade-out: opacity 1->0; at end opacity 0 -> hide_render 1 (hidden)
    end_op = next(k.co[1] for k in of.keyframe_points if round(k.co[0]) == 20)
    end_vis = next(k.co[1] for k in vf.keyframe_points if round(k.co[0]) == 20)
    check("fade-out ends opacity 0", approx(end_op, 0.0), f"{end_op}")
    check("visibility hidden (1) where opacity 0", approx(end_vis, 1.0), f"{end_vis}")

    # objects_with_visibility_keys detects it
    check("objects_with_visibility_keys finds the keyed object",
          RenderOpacity.objects_with_visibility_keys([c]) == [c])

    # ---- auto_create on a FRESH object (no opacity yet) sets up the prop + keys in one call ----
    reset()
    fresh = cube("Fresh")
    fresh.data.materials.append(mat("Frm"))
    keyed = RenderOpacity.key_fade([fresh], start=1, end=10, direction="in", auto_create=True)
    check("key_fade auto_create seeds the prop + keys", keyed == [("Fresh", "in")]
          and RenderOpacity.ATTR_NAME in fresh and fcurve(fresh, '["opacity"]') is not None)

    # ---- auto_create must NOT raise on an object with pre-existing visibility keys ----
    reset()
    vis = cube("Vis")
    vis.data.materials.append(mat("Vm"))
    RenderOpacity._set_key(vis, "hide_render", 1, 0.0, "CONSTANT")  # manual vis key, no opacity
    try:
        RenderOpacity.key_fade([vis], start=1, end=10, direction="out", auto_create=True)
        check("key_fade auto_create does not hit the create() visibility guard", True)
    except RuntimeError:
        check("key_fade auto_create does not hit the create() visibility guard", False)

    # ============================ PREPARE FOR EXPORT (sync) ============================
    reset()
    c = cube("Hand")
    c.data.materials.append(mat("Hm"))
    RenderOpacity.create([c])
    # Hand-key ONLY opacity (no visibility) — the safety-net case.
    RenderOpacity._set_key(c, '["opacity"]', 1, 1.0, "LINEAR")
    RenderOpacity._set_key(c, '["opacity"]', 10, 0.0, "LINEAR")
    check("no visibility keys after hand-keying opacity", fcurve(c, "hide_render") is None)
    synced = RenderOpacity.prepare_for_export([c])
    vf = fcurve(c, "hide_render")
    check("prepare_for_export reports the synced object", synced == ["Hand"], f"{synced}")
    check("prepare_for_export mirrored opacity->visibility (2 keys)",
          vf is not None and len(vf.keyframe_points) == 2, f"{len(vf.keyframe_points) if vf else 0}")
    # idempotent: re-running syncs nothing
    check("prepare_for_export is idempotent", RenderOpacity.prepare_for_export([c]) == [])

    # ============================ CREATE GUARD on existing vis keys ============================
    reset()
    c = cube("Guard")
    c.data.materials.append(mat("Gm"))
    RenderOpacity._set_key(c, "hide_render", 1, 0.0, "CONSTANT")  # pre-existing vis key
    raised = False
    try:
        RenderOpacity.create([c], delete_visibility_keys=False)
    except RuntimeError:
        raised = True
    check("create raises on pre-existing visibility keys (delete=False)", raised)
    RenderOpacity.create([c], delete_visibility_keys=True)  # now allowed
    check("create with delete_visibility_keys=True clears them + applies",
          RenderOpacity.ATTR_NAME in c and fcurve(c, "hide_render") is None)

    # ============================ REMOVE ============================
    reset()
    c = cube("Rem")
    c.data.materials.append(mat("Rm"))
    RenderOpacity.create([c])
    RenderOpacity.key_fade([c], start=1, end=10, direction="in")
    RenderOpacity.remove([c])
    check("remove deletes the opacity prop", RenderOpacity.ATTR_NAME not in c)
    check("remove deletes opacity + visibility curves",
          fcurve(c, '["opacity"]') is None and fcurve(c, "hide_render") is None)
    check("remove deletes the Alpha driver", alpha_driver(c.data.materials[0]) is None)

except Exception as e:
    traceback.print_exc()
    check("test harness raised", False, repr(e))

passed = sum(1 for ln in lines if ln.startswith("OK"))
for ln in lines:
    print(ln)
result = "PASS" if all(ln.startswith("OK") for ln in lines) else "FAIL"
print(f"===RESULT: {result}=== ({passed}/{len(lines)})")
