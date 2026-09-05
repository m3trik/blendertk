"""blendertk.mat_utils.render_opacity headless test — per-object opacity (driver + dual-key).
Run: blender --background --factory-startup --python blendertk/test/test_render_opacity.py

Verifies: create adds a keyable 'opacity' prop + drives Principled Alpha (single-user material);
key_fade dual-keys opacity (linear) AND render visibility (stepped) — the Unity-parity invariant;
sync/prepare_for_export mirror opacity→visibility; remove strips every artifact.
"""

import sys
import os
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
        bm = bmesh.new()
        bmesh.ops.create_cube(bm, size=2.0)
        bm.to_mesh(me)
        bm.free()
        o = bpy.data.objects.new(name, me)
        bpy.context.collection.objects.link(o)
        return o

    def mat(name="M"):
        m = bpy.data.materials.new(name)
        m.use_nodes = True
        return m

    def fcurve(obj, data_path):
        return RenderOpacity._fcurve(
            obj, data_path
        )  # slot-aware (Blender 5.x has no act.fcurves)

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
    check(
        "opacity prop seeded (1.0)",
        RenderOpacity.ATTR_NAME in c and approx(c["opacity"], 1.0),
        f"{c.get('opacity')}",
    )
    drv = alpha_driver(c.data.materials[0])
    check("Principled Alpha driven by a driver", drv is not None)
    check(
        "Alpha driver reads ['opacity'] SINGLE_PROP",
        drv is not None
        and any(
            v.type == "SINGLE_PROP"
            and v.targets[0].data_path == '["opacity"]'
            and v.targets[0].id is c
            for v in drv.driver.variables
        ),
    )

    # ---- opacity drives Alpha (verified via the animated path — the real use case: a keyframed
    # opacity scrubbed by the playhead, which re-evaluates the node-tree driver). ----
    pn = next(
        n for n in c.data.materials[0].node_tree.nodes if n.type == "BSDF_PRINCIPLED"
    )
    RenderOpacity.key_fade([c], start=1, end=11, direction="out")  # opacity 1 -> 0
    bpy.context.scene.frame_set(6)  # midpoint -> opacity 0.5
    check(
        "Alpha tracks animated opacity (0.5 @ frame 6)",
        approx(pn.inputs["Alpha"].default_value, 0.5, 1e-2),
        f"alpha={pn.inputs['Alpha'].default_value:.4f} opacity={c['opacity']:.4f}",
    )
    bpy.context.scene.frame_set(1)
    RenderOpacity.remove([c])  # clean slate for the next sub-test
    RenderOpacity.create([c])

    # ============================ SHARED MATERIAL -> SINGLE-USER ============================
    reset()
    a, b = cube("A"), cube("B")
    shared = mat("Shared")
    a.data.materials.append(shared)
    b.data.materials.append(shared)
    check(
        "material shared by 2 objects pre-create",
        shared.users == 2,
        f"users={shared.users}",
    )
    RenderOpacity.create([a, b])
    check(
        "create made materials single-user (per-object opacity)",
        a.data.materials[0] is not b.data.materials[0],
        "distinct datablocks",
    )

    # ============================ KEY FADE (dual-key) ============================
    reset()
    c = cube("Fade")
    c.data.materials.append(mat("Fm"))
    RenderOpacity.create([c])
    keyed = RenderOpacity.key_fade([c], start=1, end=20, direction="out")
    check("key_fade returns (name, 'out')", keyed == [("Fade", "out")], f"{keyed}")
    of = fcurve(c, '["opacity"]')
    vf = fcurve(c, "hide_render")
    check(
        "opacity fcurve has 2 keys",
        of is not None and len(of.keyframe_points) == 2,
        f"{len(of.keyframe_points) if of else 0}",
    )
    check(
        "opacity keys are linear",
        of is not None and all(k.interpolation == "LINEAR" for k in of.keyframe_points),
    )
    check(
        "visibility (hide_render) fcurve dual-keyed",
        vf is not None and len(vf.keyframe_points) == 2,
        f"{len(vf.keyframe_points) if vf else 0}",
    )
    check(
        "visibility keys are stepped (CONSTANT)",
        vf is not None
        and all(k.interpolation == "CONSTANT" for k in vf.keyframe_points),
    )
    # fade-out: opacity 1->0; at end opacity 0 -> hide_render 1 (hidden)
    end_op = next(k.co[1] for k in of.keyframe_points if round(k.co[0]) == 20)
    end_vis = next(k.co[1] for k in vf.keyframe_points if round(k.co[0]) == 20)
    check("fade-out ends opacity 0", approx(end_op, 0.0), f"{end_op}")
    check("visibility hidden (1) where opacity 0", approx(end_vis, 1.0), f"{end_vis}")

    # objects_with_visibility_keys detects it
    check(
        "objects_with_visibility_keys finds the keyed object",
        RenderOpacity.objects_with_visibility_keys([c]) == [c],
    )

    # ---- auto_create on a FRESH object (no opacity yet) sets up the prop + keys in one call ----
    reset()
    fresh = cube("Fresh")
    fresh.data.materials.append(mat("Frm"))
    keyed = RenderOpacity.key_fade(
        [fresh], start=1, end=10, direction="in", auto_create=True
    )
    check(
        "key_fade auto_create seeds the prop + keys",
        keyed == [("Fresh", "in")]
        and RenderOpacity.ATTR_NAME in fresh
        and fcurve(fresh, '["opacity"]') is not None,
    )

    # ---- auto_create must NOT raise on an object with pre-existing visibility keys ----
    reset()
    vis = cube("Vis")
    vis.data.materials.append(mat("Vm"))
    RenderOpacity._set_key(
        vis, "hide_render", 1, 0.0, "CONSTANT"
    )  # manual vis key, no opacity
    try:
        RenderOpacity.key_fade(
            [vis], start=1, end=10, direction="out", auto_create=True
        )
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
    check(
        "no visibility keys after hand-keying opacity", fcurve(c, "hide_render") is None
    )
    synced = RenderOpacity.prepare_for_export([c])
    vf = fcurve(c, "hide_render")
    check(
        "prepare_for_export reports the synced object", synced == ["Hand"], f"{synced}"
    )
    check(
        "prepare_for_export mirrored opacity->visibility (2 keys)",
        vf is not None and len(vf.keyframe_points) == 2,
        f"{len(vf.keyframe_points) if vf else 0}",
    )
    # idempotent: re-running syncs nothing
    check(
        "prepare_for_export is idempotent", RenderOpacity.prepare_for_export([c]) == []
    )

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
    check(
        "create with delete_visibility_keys=True clears them + applies",
        RenderOpacity.ATTR_NAME in c and fcurve(c, "hide_render") is None,
    )

    # ============================ REMOVE ============================
    reset()
    c = cube("Rem")
    c.data.materials.append(mat("Rm"))
    RenderOpacity.create([c])
    RenderOpacity.key_fade([c], start=1, end=10, direction="in")
    RenderOpacity.remove([c])
    check("remove deletes the opacity prop", RenderOpacity.ATTR_NAME not in c)
    check(
        "remove deletes opacity + visibility curves",
        fcurve(c, '["opacity"]') is None and fcurve(c, "hide_render") is None,
    )
    check("remove deletes the Alpha driver", alpha_driver(c.data.materials[0]) is None)

    # ==================== VISIBILITY TRACKS (glTF route) ====================
    # Mirror of mayatk's TestVisibilityTracksProducer. glTF animates only
    # translation/rotation/scale/weights, so keyed visibility does not survive
    # the conversion from either DCC; this channel is what
    # ptk.MeshConvert.apply_glb_visibility rebuilds it from.
    reset()
    import json as _json
    from blendertk.node_utils.data_nodes import DataNodes

    c = cube("Gate")
    c.data.materials.append(mat("GateM"))
    RenderOpacity.create([c])
    RenderOpacity.key_fade([c], start=8, end=23, direction="in")

    tracks = RenderOpacity.visibility_tracks()
    track = next((t for t in tracks if t["node"] == c.name), None)
    check("visibility_tracks finds the keyed object", track is not None)
    # hide_render is INVERTED on the way out: the published contract is glTF's
    # (1 == visible), not Blender's (1 == hidden).
    check(
        "the published track is visible-true, not hide-true",
        track is not None and track["visibility"] == [[8.0, 0.0], [23.0, 1.0]],
        detail=repr(track and track["visibility"]),
    )
    check(
        "the authored opacity ramp rides along",
        track is not None and track.get("opacity") == [[8.0, 0.0], [23.0, 1.0]],
        detail=repr(track and track.get("opacity")),
    )

    # A CONSTANT key HOLDS to the next one and then jumps, so publishing the
    # keys alone makes every consumer -- all of which read the ramp linearly --
    # invent a ramp across a segment the artist authored as a cut. Mirror of
    # mayatk's `_linear_ramp`, where the equivalent tangent turned a hold into a
    # fifteen-frame fade-out that then played in the deliverable.
    curve = fcurve(c, '["opacity"]')
    for point in curve.keyframe_points:
        point.interpolation = "CONSTANT"
    ramp = RenderOpacity._linear_ramp(curve)
    check(
        "a CONSTANT segment is published as a hold, not a ramp",
        ramp == [[8.0, 0.0], [22.99, 0.0], [23.0, 1.0]],
        detail=repr(ramp),
    )
    for point in curve.keyframe_points:
        point.interpolation = "LINEAR"
    check(
        "a LINEAR segment is published unchanged",
        RenderOpacity._linear_ramp(curve) == [[8.0, 0.0], [23.0, 1.0]],
        detail=repr(RenderOpacity._linear_ramp(curve)),
    )

    DataNodes.set_export_string(
        "fbx_takes", _json.dumps([{"name": "Shot_1", "start": 7, "end": 100}])
    )
    DataNodes.set_export_string(
        "shot_metadata", _json.dumps({"version": 1, "fps": 30.0, "shots": []})
    )
    RenderOpacity.refresh_export_metadata()
    published = _json.loads(
        DataNodes.get_export_string(RenderOpacity.DATA_CHANNEL) or "{}"
    )
    check(
        "refresh_export_metadata publishes the channel",
        published.get("version") == RenderOpacity.SCHEMA_VERSION,
    )
    check(
        "the rate is carried from the shots producer",
        published.get("fps") == 30.0,
        detail=repr(published.get("fps")),
    )
    # The take's window opens at 7; its first authored key is at 8, and that is
    # the frame the converter places at the clip's zero.
    check(
        "clip_span reports the take's first authored frame, not its start",
        published.get("clip_span", {}).get("Shot_1") == [8.0, 23.0],
        detail=repr(published.get("clip_span")),
    )

    # The exported stack carries the range the write BAKES, and the converter
    # rebases every stack onto its FIRST key -- so publishing the scene's
    # earliest key slides every clip cut from it (measured on the Maya side of
    # this pipeline as a 33-frame slide, up to 90 cm of apparent distortion).
    # That range is the scene's extent WIDENED by the declared takes, which is
    # what apply_declared_takes computes -- and it runs AFTER the producers, so
    # bake_range has to reproduce the widening rather than read it off scene.
    from blendertk.env_utils.fbx_utils import FbxUtils as _Fbx

    _scene = bpy.context.scene
    _scene.frame_start, _scene.frame_end = 0, 100
    DataNodes.set_export_string("fbx_takes", "")
    check(
        "with no takes the bake range is the scene range",
        _Fbx.bake_range() == (0.0, 100.0),
        detail=repr(_Fbx.bake_range()),
    )

    DataNodes.set_export_string(
        "fbx_takes",
        _json.dumps(
            [
                {"name": "a", "start": 33, "end": 60},
                {"name": "b", "start": 61, "end": 140},
            ]
        ),
    )
    # 0 stays (the scene starts before the first take -- the exact case that
    # made a takes-union answer wrong); 140 widens past the scene's end.
    check(
        "takes widen the bake range, never narrow it",
        _Fbx.bake_range() == (0.0, 140.0),
        detail=repr(_Fbx.bake_range()),
    )

    DataNodes.set_export_string(
        "fbx_takes", _json.dumps([{"name": "x"}, {"name": "y", "start": 2, "end": 4}])
    )
    check(
        "a malformed take entry cannot decide the range",
        _Fbx.bake_range() == (0.0, 100.0),
        detail=repr(_Fbx.bake_range()),
    )
    DataNodes.set_export_string("fbx_takes", "")

    reset()
    check(
        "a file with no keyed visibility leaves no channel",
        RenderOpacity.refresh_export_metadata() is None,
    )

    # ---- highlight channel (mirror of mayatk's RenderEffects) ----------------
    from blendertk.mat_utils.render_opacity.render_effects import RenderEffects

    check("RenderOpacity is the RenderEffects alias", RenderOpacity is RenderEffects)
    reset()
    box = cube("Glow")
    other = cube("Bystander")
    mat = bpy.data.materials.new("Shared")
    mat.use_nodes = True
    box.data.materials.append(mat)
    other.data.materials.append(mat)
    RenderEffects.create([box], channel="highlight")
    check(
        "create(highlight) seeds highlight + highlightColor",
        "highlight" in box
        and "highlightColor" in box
        and approx(box["highlight"], 0.0),
        detail=f"{dict(box.items())}",
    )
    check(
        "a shared material is single-usered for the highlighted object",
        box.data.materials[0] is not other.data.materials[0],
    )
    from blendertk.mat_utils._mat_utils import _MatUtilsInternal

    node = _MatUtilsInternal._principled_node(box.data.materials[0])
    nt = box.data.materials[0].node_tree
    strength_path = node.inputs["Emission Strength"].path_from_id("default_value")
    drivers = [
        fc
        for fc in (nt.animation_data.drivers if nt.animation_data else [])
        if fc.data_path == strength_path
    ]
    check("Emission Strength is driven by highlight", len(drivers) == 1)
    keyed = RenderEffects.key_pulse(
        [box],
        start=0,
        end=200,
        period=100,
        bright_fraction=0.6,
        ramp_fraction=0.2,
        color=(1.0, 0.0, 0.0),
    )
    fc = RenderEffects._fcurve(box, '["highlight"]')
    pts = sorted((k.co[0], k.co[1]) for k in fc.keyframe_points)
    check(
        "key_pulse writes hold/ramp keys and the colour",
        keyed == ["Glow"]
        and pts[:5] == [(0.0, 1.0), (40.0, 1.0), (60.0, 0.0), (80.0, 0.0), (100.0, 1.0)]
        and list(box["highlightColor"])[:3] == [1.0, 0.0, 0.0],
        detail=f"{pts[:6]}",
    )
    check(
        "key_pulse does not key visibility",
        RenderEffects._fcurve(box, "hide_render") is None,
    )
    tracks = RenderEffects.visibility_tracks()
    t = next((x for x in tracks if x["node"] == "Glow"), None)
    check(
        "a highlight-only object publishes its ramp and colour",
        t is not None
        and "visibility" not in t
        and t["highlight"][0] == [0.0, 1.0]
        and t["highlight_color"] == [1.0, 0.0, 0.0],
        detail=repr(t),
    )
    proxies = RenderEffects.stage_export_proxies()
    check(
        "stage_export_proxies makes one marked Empty per keyed channel",
        [p.name for p in proxies] == ["Glow__highlight"]
        and proxies[0].parent is box
        and proxies[0].get(RenderEffects.PROXY_MARKER),
        detail=repr([p.name for p in proxies]),
    )
    from blendertk.anim_utils._anim_utils import (
        AnimUtils,
    )  # slot-aware (4.4+/5.x drop Action.fcurves)

    pfc = (
        next(
            (
                f
                for f in AnimUtils.get_fcurves([proxies[0]])
                if f.data_path == "scale" and f.array_index == 0
            ),
            None,
        )
        if proxies
        else None
    )
    check(
        "the proxy's scale.x carries the curve",
        pfc is not None and len(pfc.keyframe_points) == len(fc.keyframe_points),
    )
    RenderEffects.remove_export_proxies()
    check(
        "remove_export_proxies deletes them",
        bpy.data.objects.get("Glow__highlight") is None,
    )
    RenderEffects.remove([box], channel="highlight")
    check(
        "remove(highlight) strips props, drivers and keys",
        "highlight" not in box
        and "highlightColor" not in box
        and not [
            f
            for f in (nt.animation_data.drivers if nt.animation_data else [])
            if f.data_path == strength_path
        ],
    )

except Exception as e:
    traceback.print_exc()
    check("test harness raised", False, repr(e))

passed = sum(1 for ln in lines if ln.startswith("OK"))
for ln in lines:
    print(ln)
result = "PASS" if all(ln.startswith("OK") for ln in lines) else "FAIL"
print(f"===RESULT: {result}=== ({passed}/{len(lines)})")
