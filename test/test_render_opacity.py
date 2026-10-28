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
        f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + str(detail)) if detail else ''}"
    )


def approx(a, b, tol=1e-3):
    return abs(a - b) <= tol


try:
    import bpy
    from blendertk.mat_utils._mat_utils import MatUtils
    from blendertk.mat_utils.render_opacity.render_effects import RenderEffects

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
        MatUtils.get_node_tree(m, create=True)
        return m

    def fcurve(obj, data_path):
        return RenderEffects._fcurve(
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
    results = RenderEffects.create([c], mode="attribute")
    check("create returns the object", "Box" in results)
    check(
        "opacity prop seeded (1.0)",
        RenderEffects.ATTR_NAME in c and approx(c["opacity"], 1.0),
        f"{c.get('opacity')}",
    )
    # The driver preview that wired Principled Alpha to the prop is retired
    # (2026-09-05): the material is the artist's, the prop is the channel.
    check(
        "create leaves the Principled Alpha undriven",
        alpha_driver(c.data.materials[0]) is None,
    )
    pn = next(
        n for n in c.data.materials[0].node_tree.nodes if n.type == "BSDF_PRINCIPLED"
    )
    RenderEffects.key_fade([c], start=1, end=11, direction="out")  # opacity 1 -> 0
    bpy.context.scene.frame_set(6)  # midpoint -> opacity 0.5
    check(
        "the prop animates (0.5 @ frame 6) while the material Alpha stays authored",
        approx(c["opacity"], 0.5, 1e-2)
        and approx(pn.inputs["Alpha"].default_value, 1.0),
        f"alpha={pn.inputs['Alpha'].default_value:.4f} opacity={c['opacity']:.4f}",
    )
    bpy.context.scene.frame_set(1)
    RenderEffects.remove([c])  # clean slate for the next sub-test
    RenderEffects.create([c])

    # ============================ SHARED MATERIAL STAYS SHARED ============================
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
    RenderEffects.create([a, b])
    check(
        "create leaves a shared material shared (no per-object copy)",
        a.data.materials[0] is b.data.materials[0] and shared.users == 2,
        f"users={shared.users}",
    )

    # ============================ KEY FADE (dual-key) ============================
    reset()
    c = cube("Fade")
    c.data.materials.append(mat("Fm"))
    RenderEffects.create([c])
    keyed = RenderEffects.key_fade([c], start=1, end=20, direction="out")
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
        RenderEffects.objects_with_visibility_keys([c]) == [c],
    )

    # ---- auto_create on a FRESH object (no opacity yet) sets up the prop + keys in one call ----
    reset()
    fresh = cube("Fresh")
    fresh.data.materials.append(mat("Frm"))
    keyed = RenderEffects.key_fade(
        [fresh], start=1, end=10, direction="in", auto_create=True
    )
    check(
        "key_fade auto_create seeds the prop + keys",
        keyed == [("Fresh", "in")]
        and RenderEffects.ATTR_NAME in fresh
        and fcurve(fresh, '["opacity"]') is not None,
    )

    # ---- auto_create must NOT raise on an object with pre-existing visibility keys ----
    reset()
    vis = cube("Vis")
    vis.data.materials.append(mat("Vm"))
    RenderEffects.set_key(
        vis, "hide_render", 1, 0.0, "CONSTANT"
    )  # manual vis key, no opacity
    try:
        RenderEffects.key_fade(
            [vis], start=1, end=10, direction="out", auto_create=True
        )
        check("key_fade auto_create does not hit the create() visibility guard", True)
    except RuntimeError:
        check("key_fade auto_create does not hit the create() visibility guard", False)

    # ============================ PREPARE FOR EXPORT (no mirror) ============================
    reset()
    c = cube("Hand")
    c.data.materials.append(mat("Hm"))
    RenderEffects.create([c])
    # Hand-key ONLY opacity (no visibility). Presence is derived from the ramp
    # downstream (ptk.MeshConvert._presence_keys), so the export writes no
    # mirror -- mirror of mayatk (2026-09-14).
    RenderEffects.set_key(c, '["opacity"]', 1, 1.0, "LINEAR")
    RenderEffects.set_key(c, '["opacity"]', 10, 0.0, "LINEAR")
    check(
        "no visibility keys after hand-keying opacity", fcurve(c, "hide_render") is None
    )
    synced = RenderEffects.prepare_for_export()
    RenderEffects.finish_export()
    check("prepare_for_export syncs nothing", synced == [], f"{synced}")
    check(
        "prepare_for_export writes no visibility mirror",
        fcurve(c, "hide_render") is None,
    )
    # Authored visibility is left exactly as authored, however few its keys.
    reset()
    c = cube("Sparse")
    c.data.materials.append(mat("Sm"))
    RenderEffects.create([c])
    for frame, value in ((1, 1.0), (5, 0.5), (10, 0.0), (15, 0.0)):
        RenderEffects.set_key(c, '["opacity"]', frame, value, "LINEAR")
    RenderEffects.set_key(c, "hide_render", 1, 0.0, "CONSTANT")
    RenderEffects.set_key(c, "hide_render", 10, 1.0, "CONSTANT")
    authored = [tuple(kp.co) for kp in fcurve(c, "hide_render").keyframe_points]
    synced = RenderEffects.prepare_for_export()
    kept = [tuple(kp.co) for kp in fcurve(c, "hide_render").keyframe_points]
    check(
        "prepare_for_export leaves fewer visibility keys than opacity keys as authored",
        synced == [] and kept == authored,
        f"{synced} {kept}",
    )

    # ============================ CREATE GUARD on existing vis keys ============================
    reset()
    c = cube("Guard")
    c.data.materials.append(mat("Gm"))
    RenderEffects.set_key(c, "hide_render", 1, 0.0, "CONSTANT")  # pre-existing vis key
    raised = False
    try:
        RenderEffects.create([c], delete_visibility_keys=False)
    except RuntimeError:
        raised = True
    check("create raises on pre-existing visibility keys (delete=False)", raised)
    RenderEffects.create([c], delete_visibility_keys=True)  # now allowed
    check(
        "create with delete_visibility_keys=True clears them + applies",
        RenderEffects.ATTR_NAME in c and fcurve(c, "hide_render") is None,
    )

    # ============================ CREATE is per channel ============================
    # Mirror of mayatk: create(channel="highlight") resets the highlight channel
    # only, and the visibility-key guard belongs to the presence channel -- a
    # fading object keeps its opacity keys and their render-visibility mirror.
    reset()
    c = cube("Both")
    RenderEffects.create([c])
    RenderEffects.key_fade([c], start=1, end=10, direction="in")
    raised = None
    try:
        RenderEffects.create([c], channel=RenderEffects.HIGHLIGHT_ATTR)
    except RuntimeError as exc:
        raised = exc
    check(
        "create(highlight) leaves a fading object's opacity channel alone",
        raised is None
        and RenderEffects.ATTR_NAME in c
        and fcurve(c, f'["{RenderEffects.ATTR_NAME}"]') is not None
        and fcurve(c, "hide_render") is not None
        and RenderEffects.HIGHLIGHT_ATTR in c,
        detail=repr(raised),
    )

    # ============================ REMOVE ============================
    reset()
    c = cube("Rem")
    c.data.materials.append(mat("Rm"))
    RenderEffects.create([c])
    RenderEffects.key_fade([c], start=1, end=10, direction="in")
    bpy.context.scene.frame_set(1)  # before the fade-in: the mirror hides it
    RenderEffects.remove([c])
    check("remove leaves the object rendered", c.hide_render is False)
    check("remove deletes the opacity prop", RenderEffects.ATTR_NAME not in c)
    check(
        "remove deletes opacity + visibility curves",
        fcurve(c, '["opacity"]') is None and fcurve(c, "hide_render") is None,
    )
    check("remove deletes the Alpha driver", alpha_driver(c.data.materials[0]) is None)
    # An object in the scope that never carried the channel is the user's: remove
    # un-hid it from render and dropped its own visibility keys (mayatk's remove
    # touches only objects that have the channel). Added: 2026-10-10
    plain = cube("RemPlain")
    RenderEffects.set_key(plain, "hide_render", 1, 1.0, "CONSTANT")  # its own key
    plain.hide_render = True
    RenderEffects.remove([plain])
    check(
        "remove leaves an object without the channel hidden, its keys kept",
        plain.hide_render is True and fcurve(plain, "hide_render") is not None,
        f"hide_render={plain.hide_render}",
    )

    # ==================== VISIBILITY TRACKS (glTF route) ====================
    # Mirror of mayatk's TestVisibilityTracksProducer. glTF animates only
    # translation/rotation/scale/weights, so keyed visibility does not survive
    # the conversion from either DCC; this channel is what
    # ptk.MeshConvert.apply_glb_visibility rebuilds it from.
    reset()
    import pythontk as ptk
    from blendertk.node_utils.data_nodes import DataNodes

    c = cube("Gate")
    c.data.materials.append(mat("GateM"))
    RenderEffects.create([c])
    RenderEffects.key_fade([c], start=8, end=23, direction="in")

    tracks = RenderEffects.visibility_tracks()
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
    ramp = RenderEffects._linear_ramp(curve)
    check(
        "a CONSTANT segment is published as a hold, not a ramp",
        ramp == [[8.0, 0.0], [22.99, 0.0], [23.0, 1.0]],
        detail=repr(ramp),
    )
    for point in curve.keyframe_points:
        point.interpolation = "LINEAR"
    check(
        "a LINEAR segment is published unchanged",
        RenderEffects._linear_ramp(curve) == [[8.0, 0.0], [23.0, 1.0]],
        detail=repr(RenderEffects._linear_ramp(curve)),
    )

    # The take as a publish declares it: a shot-record clip carrying its range.
    ptk.SceneRecords.SHOTS.save(
        DataNodes,
        {
            "fps": 30.0,
            "shots": [{"clip": "Shot_1", "start": 7, "end": 100, "objects": []}],
        },
    )
    RenderEffects.refresh_export_metadata()
    published = ptk.SceneRecords.VISIBILITY.load(DataNodes) or {}
    check(
        "refresh_export_metadata publishes the channel",
        published.get("version") == RenderEffects.SCHEMA_VERSION,
    )
    ptk.SceneRecords.SHOTS.clear(DataNodes)
    RenderEffects.refresh_export_metadata()
    republished = ptk.SceneRecords.VISIBILITY.load(DataNodes) or {}
    scene_fps = bpy.context.scene.render.fps / bpy.context.scene.render.fps_base
    check(
        "with no shots producer the channel carries the scene's own rate",
        republished.get("fps") == scene_fps,
        detail=repr(republished.get("fps")),
    )
    ptk.SceneRecords.SHOTS.save(DataNodes, {"fps": 30.0, "shots": []})
    check(
        "the rate is carried from the shots producer",
        published.get("fps") == 30.0,
        detail=repr(published.get("fps")),
    )
    # The take's window opens at 7 and its first authored key is at 8, but
    # Blender's split bakes every frame of the window, so the converter places
    # frame 7 at the clip's zero (measured 2026-10-04: ShotB 21-40 opened at
    # 21, not at its first key 30, and a gate placed against 30 lost the
    # shot's visible run). mayatk's split keeps a take to its authored keys.
    check(
        "clip_span reports the take's window: Blender bakes every frame of it",
        published.get("clip_span", {}).get("Shot_1") == [7.0, 100.0],
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
    check(
        "with no takes the bake range is the scene range",
        _Fbx.bake_range() == (0.0, 100.0),
        detail=repr(_Fbx.bake_range()),
    )

    _takes = [("a", 33, 60), ("b", 61, 140)]
    ptk.SceneRecords.SHOTS.save(
        DataNodes,
        {
            "fps": 30.0,
            "shots": [
                {"clip": n, "start": s, "end": e, "objects": []} for n, s, e in _takes
            ],
        },
    )
    # 0 stays (the scene starts before the first take -- the exact case that
    # made a takes-union answer wrong); 140 widens past the scene's end.
    check(
        "takes widen the bake range, never narrow it",
        _Fbx.bake_range() == (0.0, 140.0),
        detail=repr(_Fbx.bake_range()),
    )
    # Clips without their ranges declare no take, and a retired fbx_takes list
    # beside them is never read (2026-10-20: no legacy branch).
    import json as _takes_json

    ptk.SceneRecords.SHOTS.save(
        DataNodes,
        {"fps": 30.0, "shots": [{"clip": n, "objects": []} for n, _s, _e in _takes]},
    )
    DataNodes.write(
        ptk.Scope.DELIVERABLE,
        "fbx_takes",
        _takes_json.dumps([{"name": n, "start": s, "end": e} for n, s, e in _takes]),
    )
    check(
        "clips without ranges widen nothing; a retired fbx_takes is not read",
        _Fbx.bake_range() == (0.0, 100.0),
        detail=repr(_Fbx.bake_range()),
    )
    DataNodes.remove(ptk.Scope.DELIVERABLE, "fbx_takes")
    ptk.SceneRecords.SHOTS.save(DataNodes, {"fps": 30.0, "shots": []})

    # What the bake-range seed for clip_span["*"] actually describes, through
    # FbxUtils.export rather than raw bpy.ops. Blender's global take CLIPS to
    # the range: a curve keyed 0-100 is written as 60 frames under a 20-80
    # range, so the seed is TRUE -- in BOTH modes since 2026-09-19. Until then
    # a write with no takes armed took Blender's operator defaults, emitted
    # per-action start-zeroed takes over each action's OWN range, and wrote the
    # curve whole (100 frames against a seed of 60). FbxUtils now makes every
    # animated write one scene-range take (``FbxUtils.scene_range_take``), which
    # is what the seed describes.
    #
    # Maya writes the curve whole under ANY range, which is why its fix
    # measures the key extent instead; publishing a measured extent here would
    # OVERSTATE the write, so these checks pin the Blender answer itself.
    _cube = bpy.data.objects.new("ClipSpanProbe", bpy.data.meshes.new("ClipSpanProbe"))
    bpy.context.collection.objects.link(_cube)
    for _f, _x in ((0, 0.0), (100, 10.0)):
        _cube.location.x = _x
        _cube.keyframe_insert(data_path="location", index=0, frame=_f)

    _dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "temp_tests")
    os.makedirs(_dir, exist_ok=True)
    _fbx = os.path.join(_dir, "btk_clip_span_probe.fbx")
    _prior_takes = _Fbx._pending_takes

    def _written_span(takes):
        """Frames the real export path writes for the probe, under *takes*."""
        _scene.frame_start, _scene.frame_end = 20, 80
        _Fbx._pending_takes = takes
        seen = {a.name for a in bpy.data.actions}
        try:
            _Fbx.export(filepath=_fbx, objects=[_cube], bake_anim=True)
            bpy.ops.import_scene.fbx(filepath=_fbx)
        except Exception as exc:  # noqa: BLE001
            return f"export/import failed: {exc}"
        spans = [
            round(a.frame_range[1] - a.frame_range[0])
            for a in bpy.data.actions
            if a.name not in seen
        ]
        for a in [a for a in bpy.data.actions if a.name not in seen]:
            bpy.data.actions.remove(a, do_unlink=True)
        for o in [
            o
            for o in bpy.data.objects
            if o.name.startswith("ClipSpanProbe") and o is not _cube
        ]:
            bpy.data.objects.remove(o, do_unlink=True)
        return max(spans) if spans else None

    _armed = _written_span([("probe", 20, 80)])
    check(
        "takes armed: the write is one scene-range stack, so the bake-range seed is TRUE",
        _armed == 60,
        detail=f"authored 100 frames, wrote {_armed} under a 20-80 range",
    )

    _bare = _written_span(None)
    check(
        "no takes: still one scene-range take, so the bake-range seed is TRUE",
        _bare == 60,
        detail=f"authored 100 frames, wrote {_bare} against a 60-frame seed",
    )

    _Fbx._pending_takes = _prior_takes
    try:
        os.remove(_fbx)
    except OSError:
        pass
    for _o in [o for o in bpy.data.objects if o.name.startswith("ClipSpanProbe")]:
        bpy.data.objects.remove(_o, do_unlink=True)
    _scene.frame_start, _scene.frame_end = 0, 100

    # A hand-edited or truncated clip -- one without its range -- declares
    # no take, so it cannot decide the range.
    ptk.SceneRecords.SHOTS.save(
        DataNodes,
        {
            "fps": 30.0,
            "shots": [
                {"clip": "x", "objects": []},
                {"clip": "y", "start": 2, "end": 4, "objects": []},
            ],
        },
    )
    check(
        "a malformed take entry cannot decide the range",
        _Fbx.bake_range() == (0.0, 100.0),
        detail=repr(_Fbx.bake_range()),
    )
    ptk.SceneRecords.SHOTS.save(DataNodes, {"fps": 30.0, "shots": []})

    # REGRESSION (2026-09-18): the FIRST export after a shot is added cut the
    # clip origin against the PREVIOUS export's takes -- bake_range read the
    # STORED shot record, and an assembly commits only after every producer
    # has run, so the takes the shots producer had just built were invisible
    # to the visibility producer that runs after it.
    from blendertk import BlenderShotStore

    ptk.SceneRecords.SHOTS.clear(DataNodes)
    BlenderShotStore._prefs_dir_override = ptk.TempArtifacts(
        "btk_render_opacity_prefs"
    ).dir_path()
    BlenderShotStore.clear_active()
    BlenderShotStore.active().define_shot("Late", 90, 140, objects=[c.name])
    _fresh = _Fbx.publish(
        _Fbx.export_context(),
        only=[ptk.SceneRecords.SHOTS, ptk.SceneRecords.VISIBILITY],
    ).record(ptk.SceneRecords.VISIBILITY, {})
    check(
        "the first export after adding a shot cuts the origin against ITS takes",
        (_fresh.get("clip_span") or {}).get("*") == [0.0, 140.0],
        detail=repr(_fresh.get("clip_span")),
    )
    BlenderShotStore.clear_active()
    BlenderShotStore._prefs_dir_override = None
    for _spec in (
        ptk.SceneRecords.SHOTS,
        ptk.SceneRecords.SHOT_STORE,
    ):
        _spec.clear(DataNodes)

    reset()
    check(
        "a file with no keyed visibility leaves no channel",
        RenderEffects.refresh_export_metadata() is None,
    )

    # ---- highlight channel (mirror of mayatk's RenderEffects) ----------------
    reset()
    box = cube("Glow")
    other = cube("Bystander")
    mat = bpy.data.materials.new("Shared")
    MatUtils.get_node_tree(mat, create=True)
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
    # The driver preview that copied a shared material per object is retired
    # (2026-09-05): creating the channel leaves the authored material alone.
    check(
        "create(highlight) leaves the shared material shared and undriven",
        box.data.materials[0] is other.data.materials[0]
        and not (box.data.materials[0].node_tree.animation_data or None),
    )
    RenderEffects.key_pulse(
        [box],
        start=0,
        end=200,
        period=100,
        bright_fraction=0.6,
        ramp_fraction=0.2,
        lead_in=40,
        lead_out=0,
    )
    fc = RenderEffects._fcurve(box, '["highlight"]')
    pts = sorted((k.co[0], k.co[1]) for k in fc.keyframe_points)
    check(
        "the two pulse gaps can be set apart",
        pts[:2] == [(0.0, 0.0), (40.0, 1.0)]
        and pts[-1] == (200.0, 0.0)
        and pts[-2][0] == 199.0,
        detail=f"{pts[:3]} .. {pts[-2:]}",
    )
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
        and pts[:5] == [(0.0, 0.0), (20.0, 1.0), (60.0, 1.0), (80.0, 0.0), (100.0, 0.0)]
        and list(box["highlightColor"])[:3] == [1.0, 0.0, 0.0],
        detail=f"{pts[:6]}",
    )
    # An F-Curve holds its first key backwards and its last forwards, so a
    # train that merely BEGAN bright glowed for the whole timeline before it
    # (measured on a production board keyed 725-845 of a 3468 frame scene).
    check(
        "the pulse is bracketed by dim keys at both ends",
        pts[0] == (0.0, 0.0) and pts[-1] == (200.0, 0.0),
        detail=f"{pts[0]} .. {pts[-1]}",
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
        and t["highlight"][0] == [0.0, 0.0]  # bracketed dim, not bright
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
        and not (box.data.materials[0].node_tree.animation_data or None),
    )

    # ---- colour revision: restate the colour without touching the keys -------
    reset()
    a = cube("ReviseA")
    b = cube("ReviseB")
    plain = cube("NoChannel")
    RenderEffects.create([a, b], channel="highlight")
    RenderEffects.key_pulse(
        [a], start=0, end=100, period=50, bright_fraction=0.6, ramp_fraction=0.2
    )
    path = '["highlight"]'
    before = sorted(
        (k.co[0], k.co[1]) for k in RenderEffects._fcurve(a, path).keyframe_points
    )
    written = RenderEffects.set_channel_color([a], color=(0.045, 0.39, 1.0))
    after = sorted(
        (k.co[0], k.co[1]) for k in RenderEffects._fcurve(a, path).keyframe_points
    )
    check(
        "set_channel_color writes the named objects",
        written == ["ReviseA"]
        and all(
            approx(g, e)
            for g, e in zip(list(a["highlightColor"])[:3], (0.045, 0.39, 1.0))
        ),
        detail=f"{list(a['highlightColor'])[:3]}",
    )
    check("a recolour leaves the pulse keys alone", before == after)
    check(
        "an object without the channel is skipped",
        RenderEffects.set_channel_color([a, plain], color=(1.0, 0.0, 0.0))
        == ["ReviseA"]
        and "highlightColor" not in plain,
    )
    check(
        "objects_with_channel finds only the carriers",
        sorted(o.name for o in RenderEffects.objects_with_channel("highlight"))
        == ["ReviseA", "ReviseB"],
    )
    for o in bpy.data.objects:
        try:
            o.select_set(False)
        except RuntimeError:  # not linked to the view layer
            pass
    written_all = RenderEffects.set_channel_color(color=(0.045, 0.39, 1.0))
    colors = RenderEffects.channel_colors()
    check(
        "no selection falls back to every object with the channel",
        sorted(written_all) == ["ReviseA", "ReviseB"],
        detail=repr(written_all),
    )
    check(
        "channel_colors reads back what was written",
        sorted(colors) == ["ReviseA", "ReviseB"]
        and all(
            approx(g, e)
            for rgb in colors.values()
            for g, e in zip(rgb, (0.045, 0.39, 1.0))
        ),
        detail=repr(colors),
    )
    try:
        RenderEffects.set_channel_color([a])
        refused = False
    except ValueError:
        refused = True
    check("a missing colour is refused", refused)
    try:
        RenderEffects.set_channel_color([a], color=(1.0, 0.0, 0.0), channel="opacity")
        refused = False
    except ValueError:
        refused = True
    check("a channel without a colour is refused", refused)

    # ---- the key tools own channel creation (panel has no Create action) ------
    def _emission_drivers(obj):
        from blendertk.mat_utils._mat_utils import _MatUtilsInternal

        m = obj.data.materials[0]
        n = _MatUtilsInternal._principled_node(m)
        path = n.inputs["Emission Strength"].path_from_id("default_value")
        ad = m.node_tree.animation_data
        return [f for f in (ad.drivers if ad else []) if f.data_path == path]

    reset()
    box = cube("Quiet")
    m = bpy.data.materials.new("QuietMat")
    MatUtils.get_node_tree(m, create=True)
    box.data.materials.append(m)
    RenderEffects.key_pulse([box], start=0, end=100, period=50)
    check(
        "key_pulse creates the prop and keys, wires no driver",
        "highlight" in box
        and RenderEffects._fcurve(box, '["highlight"]') is not None
        and not _emission_drivers(box),
    )
    try:
        RenderEffects.key_pulse([box], start=0, end=100, period=50, preview=True)
        _refused = False
    except TypeError:
        _refused = True
    check(
        "the retired preview keyword is gone (2026-09-21) -- and wired no driver",
        _refused and not _emission_drivers(box),
    )
    _before = dict(box.items())
    check(
        "the retired 'material' mode is refused before anything is touched",
        RenderEffects.create([box], mode="material") == {}
        and dict(box.items()).keys() == _before.keys()
        and not hasattr(RenderEffects, "preview"),
    )
    # A scene saved with the old preview on still heals through remove().
    from blendertk.mat_utils._mat_utils import _MatUtilsInternal as _MI

    n = _MI._principled_node(m)
    fc = m.node_tree.driver_add(
        n.inputs["Emission Strength"].path_from_id("default_value")
    )
    fc.driver.expression = "1.0"
    check("legacy driver present before remove", len(_emission_drivers(box)) == 1)
    RenderEffects.remove([box], channel="highlight")
    check("remove() heals the retired preview's driver", not _emission_drivers(box))

    # key_pulse keys the channel it is given (mirror of mayatk): a pulse on the
    # opacity channel is a blink keyed on ["opacity"]; it leaves no highlight,
    # and a colour for a channel with no colour ramp is skipped, not raised.
    reset()
    blink = cube("Blink")
    RenderEffects.key_pulse(
        [blink],
        start=0,
        end=100,
        period=50,
        channel=RenderEffects.ATTR_NAME,
        color=(1.0, 0.0, 0.0),
    )
    check(
        "key_pulse(channel='opacity') keys the opacity channel, not the highlight",
        RenderEffects._fcurve(blink, f'["{RenderEffects.ATTR_NAME}"]') is not None
        and RenderEffects.HIGHLIGHT_ATTR not in blink,
    )
    reset()
    box = cube("Vis")
    box.hide_render = True
    box.keyframe_insert(data_path="hide_render", frame=5)
    box.hide_render = False
    box.keyframe_insert(data_path="hide_render", frame=50)
    RenderEffects.key_fade(
        [box], start=10, end=20, direction="in", delete_visibility_keys=True
    )
    vis = RenderEffects._fcurve(box, "hide_render")
    check(
        "key_fade(delete_visibility_keys=True) clears old vis keys, writes the mirror",
        vis is not None
        and sorted(k.co[0] for k in vis.keyframe_points) == [10.0, 20.0],
        detail=str(sorted(k.co[0] for k in vis.keyframe_points) if vis else None),
    )

    reset()
    box = cube("WholeFrames")
    # The pulse is authored in SECONDS, so its cadence is fractional in frames
    # (2.86 s is 85.8 of them at 30 fps): every key snaps to a whole frame, and
    # the cycle still advances by the exact period, so a long train does not
    # drift off the asked-for cadence. Mirror of mayatk's TestWholeFrameKeys.
    RenderEffects.key_pulse(
        [box], start=10.4, end=110.6, period=85.8, bright_fraction=0.5
    )
    pts = sorted(
        k.co[0] for k in RenderEffects._fcurve(box, '["highlight"]').keyframe_points
    )
    check(
        "a fractional pulse keys whole frames only, window included",
        pts
        and not [t for t in pts if t != int(t)]
        and (pts[0], pts[-1]) == (10.0, 111.0),
        detail=f"{pts[:4]} .. {pts[-2:]}",
    )

    reset()
    box = cube("SubFrames")
    RenderEffects.key_pulse([box], start=0, end=400, period=85.8, whole_frames=False)
    pts = sorted(
        k.co[0] for k in RenderEffects._fcurve(box, '["highlight"]').keyframe_points
    )
    check(
        "whole_frames=False still authors the sub-frame cadence",
        any(t != int(t) for t in pts),
        detail=f"{pts[:4]}",
    )

    reset()
    box = cube("FadeWindow")
    RenderEffects.key_fade([box], start=10.4, end=25.6, direction="in")
    opa = sorted(
        k.co[0] for k in RenderEffects._fcurve(box, '["opacity"]').keyframe_points
    )
    vis = sorted(
        k.co[0] for k in RenderEffects._fcurve(box, "hide_render").keyframe_points
    )
    check(
        "a fade snaps its window and its visibility mirror",
        opa == [10.0, 26.0] and vis == [10.0, 26.0],
        detail=f"{opa} / {vis}",
    )

    reset()
    box = cube("FadeHl")
    RenderEffects.key_fade([box], start=0, end=10, direction="in", channel="highlight")
    check(
        "key_fade(channel='highlight') keys the highlight prop and leaves visibility alone",
        RenderEffects._fcurve(box, '["highlight"]') is not None
        and RenderEffects._fcurve(box, "hide_render") is None,
    )

    # ---- highlight colour ramp: two authored ends ---------------------------
    reset()
    box = cube("Stops")
    RenderEffects.create([box], channel="highlight")
    check(
        "create(highlight) seeds BOTH ends of the colour ramp",
        "highlightColor" in box and "highlightColorDim" in box,
    )
    # Two files state this colour: what create() authors, and the effect
    # recipe's defaults -- what the Render Effects panel's row shows before
    # anything is picked (tentacle's panel test pins its seeds to the recipe,
    # and their 8-bit round trip). Drift shows one colour and writes another.
    import pythontk as _cptk

    _seed = _cptk.EffectRecipe()
    check(
        "create writes the recipe's default colours (the panel's seeds)",
        all(
            approx(g, e, tol=1e-6)
            for prop, seed in (
                ("highlightColor", _seed.pulse_bright),
                ("highlightColorDim", _seed.pulse_dim),
            )
            for g, e in zip(list(box[prop])[:3], seed)
        ),
        detail=f"{list(box['highlightColor'])[:3]} vs {_seed.pulse_bright}",
    )
    check(
        "the dim end seeds black, so the ramp collapses to the legacy shape",
        list(box["highlightColorDim"])[:3] == [0.0, 0.0, 0.0],
        detail=f"{list(box['highlightColorDim'])[:3]}",
    )

    RenderEffects.set_channel_color([box], color=(1.0, 0.0, 0.0), stop="lo")
    check(
        "writing one end leaves the other alone",
        list(box["highlightColorDim"])[:3] == [1.0, 0.0, 0.0]
        and all(
            approx(g, e)
            for g, e in zip(list(box["highlightColor"])[:3], (0.0, 0.088656, 0.723055))
        ),
        detail=f"{list(box['highlightColor'])[:3]} / {list(box['highlightColorDim'])[:3]}",
    )

    RenderEffects.set_channel_color([box], color=(0.9, 0.1, 0.1))
    check(
        "the default end is still the bright one (every legacy call site)",
        all(
            approx(g, e)
            for g, e in zip(list(box["highlightColor"])[:3], (0.9, 0.1, 0.1))
        )
        and list(box["highlightColorDim"])[:3] == [1.0, 0.0, 0.0],
    )

    stops = RenderEffects.channel_color_stops([box])
    pair = stops.get(box.name)
    check(
        "channel_color_stops reads both ends in one pass, high first",
        pair is not None
        and len(pair) == 2
        and all(approx(g, e) for g, e in zip(pair[0], (0.9, 0.1, 0.1)))
        and all(approx(g, e) for g, e in zip(pair[1], (1.0, 0.0, 0.0))),
        detail=f"{pair}",
    )

    reset()
    box = cube("StopsPulse")
    RenderEffects.key_pulse(
        [box], start=0, end=100, color=(1.0, 0.0, 0.0), dim_color=(0.0, 0.0, 0.5)
    )
    check(
        "key_pulse writes both ends",
        list(box["highlightColor"])[:3] == [1.0, 0.0, 0.0]
        and list(box["highlightColorDim"])[:3] == [0.0, 0.0, 0.5],
    )

    RenderEffects.key_pulse([box], start=0, end=100, color=(0.0, 1.0, 0.0))
    check(
        "a pulse that states no dim colour does not erase one",
        list(box["highlightColorDim"])[:3] == [0.0, 0.0, 0.5],
        detail=f"{list(box['highlightColorDim'])[:3]}",
    )

    tracks = RenderEffects.visibility_tracks()
    t = next((x for x in tracks if x.get("node") == box.name), None)
    check(
        "both ends publish under their own track keys",
        t is not None
        and t.get("highlight_color") == [0.0, 1.0, 0.0]
        and t.get("highlight_color_dim") == [0.0, 0.0, 0.5],
        detail=f"{t}",
    )

    RenderEffects.remove([box], channel="highlight")
    check(
        "remove strips both ends",
        "highlightColor" not in box and "highlightColorDim" not in box,
    )

    # The two spellings of one concept, in one place rather than two files.
    from pythontk import GlbFades

    _GLTF = GlbFades.CHANNELS

    check(
        "blendertk's published stop keys match pythontk's channel table",
        RenderEffects.HIGHLIGHT_TRACK_STOPS.keys == _GLTF["highlight"].color_stops.keys,
        detail=f"{RenderEffects.HIGHLIGHT_TRACK_STOPS.keys}",
    )
    check(
        "PROP_PATHS covers both ends (what the shot system reads as content)",
        all(
            f'["{p}"]' in RenderEffects.PROP_PATHS
            for p in RenderEffects.HIGHLIGHT_COLOR_STOPS.keys
        ),
        detail=f"{RenderEffects.PROP_PATHS}",
    )

    # ---- WebXR preview overlay: the effect as planned, nothing written --------
    # What the Render Effects panel's WebXR action pushes (the panel is
    # tentacle's; its own tests moved with it): the page's plan on the
    # selection, by name, the object's own keys left out and nothing written.
    import pythontk as _ptk

    reset()
    p = cube("PreviewPulsed")
    RenderEffects.key_pulse([p], start=0, end=100, period=50)
    pulse_keys = len(RenderEffects._fcurve(p, '["highlight"]').keyframe_points)
    _fps = RenderEffects._scene_fps()
    expected = _ptk.RampKeys.fade_loop(15, hold=0.6 * _fps)
    overlay = RenderEffects.preview_channels(
        ["PreviewPulsed"], channel="opacity", keys=expected, fps=_fps
    )
    tracks = (overlay.get(_ptk.SceneRecords.VISIBILITY.key) or {}).get("tracks") or [{}]
    # At the channel's published precision: build_visibility_tracks rounds every
    # float (0.6 s * 24 fps is 14.399999999999999 raw and ships as 14.4).
    _digits = _ptk.MeshConvert.VISIBILITY_TRACK_DIGITS
    check(
        "preview_channels overlays the planned fade on the named object",
        tracks[0].get("node") == "PreviewPulsed"
        and tracks[0].get("opacity")
        == [[round(f, _digits), round(v, _digits)] for f, v in expected],
        detail=f"{tracks}",
    )
    check(
        "the overlay writes nothing and leaves the object's own pulse out",
        RenderEffects.ATTR_NAME not in p
        and len(RenderEffects._fcurve(p, '["highlight"]').keyframe_points) == pulse_keys
        and "highlight" not in tracks[0],
        detail=f"{list(p.keys())} {tracks[0]}",
    )

    # The fade's preview runs the EXPORTER's own function, so it cannot drift
    # from the deliverable: alpha on the fourth lane of baseColorFactor.
    _fade_spec = _GLTF["opacity"]
    _faded = _fade_spec.values([0.78, 0.80, 0.84], 0.4, ())
    check(
        "the fade's published value is the albedo with alpha on lane four",
        len(_faded) == 4 and approx(_faded[3], 0.4) and approx(_faded[0], 0.78),
        detail=f"{_faded}",
    )

    # ---- the Render Effects panel's reads (mirror of mayatk's) ---------------
    # The selection its actions act on, which objects carry a channel, and the
    # selection a Shot Manifest focus makes.
    reset()
    lit, plain = cube("Lit"), cube("Plain")
    RenderEffects.create([lit], channel="highlight")
    check(
        "has_channel: by object or name, per channel",
        RenderEffects.has_channel(lit, "highlight")
        and RenderEffects.has_channel("Lit", "highlight")
        and not RenderEffects.has_channel(plain, "highlight")
        and not RenderEffects.has_channel(lit, "opacity")
        and not RenderEffects.has_channel("Gone", "highlight"),
    )
    _unknown = ""
    try:
        RenderEffects.has_channel(lit, "glow")
    except KeyError as exc:
        _unknown = str(exc)
    check("has_channel refuses an unknown channel", "glow" in _unknown, _unknown)
    _names = RenderEffects.select_objects(["Plain", "Lit", "Gone"])
    check(
        "select_objects selects what resolves, the first active, by name",
        _names == ["Plain", "Lit"]
        and plain.select_get()
        and lit.select_get()
        and bpy.context.view_layer.objects.active == plain,
        detail=f"{_names}",
    )
    check(
        "selected_objects: the selection by name; last_only, the active object",
        sorted(RenderEffects.selected_objects()) == ["Lit", "Plain"]
        and RenderEffects.selected_objects(last_only=True) == ["Plain"],
        detail=f"{RenderEffects.selected_objects()} "
        f"{RenderEffects.selected_objects(last_only=True)}",
    )
    check(
        "select_objects of nothing that resolves leaves the selection alone",
        RenderEffects.select_objects([]) == []
        and RenderEffects.select_objects(["Gone"]) == []
        and sorted(RenderEffects.selected_objects()) == ["Lit", "Plain"],
    )

    # ---- apply_effect: the Shot Manifest's writer (mirror of mayatk's) -------
    # One plan, one writer, nothing deleted; the recipe's colours only on a
    # channel it creates.
    bpy.context.scene.render.fps = 24
    bpy.context.scene.render.fps_base = 1.0
    reset()
    fx = cube("FxBox")
    _op_path = f'["{RenderEffects.ATTR_NAME}"]'
    _hl_path = f'["{RenderEffects.HIGHLIGHT_ATTR}"]'
    _hi, _lo = RenderEffects.HIGHLIGHT_COLOR_STOPS.keys

    def _pts(obj, path):
        fc = RenderEffects._fcurve(obj, path)
        return sorted((k.co[0], k.co[1]) for k in fc.keyframe_points) if fc else []

    def _rgb(obj, prop):
        return [round(c, 6) for c in list(obj[prop])[:3]]

    _fade = _ptk.EffectRecipe(fade_frames=12)
    written = RenderEffects.apply_effect(fx, "fade_in", 10, 100, _fade, fps=24)
    RenderEffects.apply_effect(fx, "fade_out", 10, 100, _fade, fps=24)
    check(
        "apply_effect: a fade is the recipe's length where it is placed",
        _pts(fx, _op_path) == [(10.0, 0.0), (22.0, 1.0), (88.0, 1.0), (100.0, 0.0)],
        detail=f"{_pts(fx, _op_path)}",
    )
    check(
        "apply_effect: returns every key it wrote, the hide_render mirror included",
        sorted(t for _c, t in written) == [10.0, 10.0, 22.0, 22.0]
        and len({c for c, _t in written}) == 2,
        detail=f"{written}",
    )
    _pulse = _ptk.EffectRecipe(
        pulse_period=2.0, pulse_duty=0.5, pulse_bright=(1, 0, 0), pulse_dim=(0, 0, 0.5)
    )
    RenderEffects.apply_effect(fx, "pulse", 0, 240, _pulse, fps=24)
    check(
        "apply_effect: a pulse keys the recipe's plan over the range",
        _pts(fx, _hl_path) == _pulse.plan("pulse", 0, 240, 24),
        detail=f"{_pts(fx, _hl_path)[:4]}",
    )
    check(
        "apply_effect: a channel it creates takes the recipe's colours",
        _rgb(fx, _hi) == [1.0, 0.0, 0.0] and _rgb(fx, _lo) == [0.0, 0.0, 0.5],
        detail=f"{_rgb(fx, _hi)} {_rgb(fx, _lo)}",
    )
    RenderEffects.set_channel_color([fx], (0.0, 1.0, 0.0))
    fx[RenderEffects.HIGHLIGHT_ATTR] = 0.5
    fx.keyframe_insert(data_path=_hl_path, frame=500)  # the animator's
    RenderEffects.apply_effect(fx, "pulse", 0, 240, _pulse, fps=24)
    check(
        "apply_effect: an existing channel keeps its colours",
        _rgb(fx, _hi) == [0.0, 1.0, 0.0],
        detail=f"{_rgb(fx, _hi)}",
    )
    check(
        "apply_effect: deletes nothing (the animator's key stays)",
        (500.0, 0.5) in _pts(fx, _hl_path),
        detail=f"{_pts(fx, _hl_path)[-3:]}",
    )
    try:
        RenderEffects.apply_effect(fx, "clip", 0, 10)
        _raised = False
    except ValueError:
        _raised = True
    check("apply_effect: an effect without a channel says so", _raised)

    # The panel's Key and the manifest's build key one recipe: the scene's.
    _store = RenderEffects.scene_store().active()
    _recipe_before = _store.effect_recipe
    _store.update_effect_recipe(fade_frames=8, pulse_period=2.0, pulse_duty=0.5)
    hand = cube("HandBox")
    RenderEffects.key_fade([hand], start=20)
    RenderEffects.key_pulse([hand], start=0, end=240)
    check(
        "the hand tools key with the scene recipe",
        _pts(hand, _op_path) == [(20.0, 0.0), (28.0, 1.0)]
        and _pts(hand, _hl_path) == _store.effect_recipe.plan("pulse", 0, 240, 24),
        detail=f"{_pts(hand, _op_path)} {_pts(hand, _hl_path)[:4]}",
    )
    _store.effect_recipe = _recipe_before

    # ============================ CHANNEL RECORDS LAND IN BULK ============================
    # ``apply_channel_records`` (an imported FBX's user-property curves, the
    # Maya bridge's shot-section replay) writes each label's keys in one bulk
    # pass, leaving exactly what ``set_key`` per key leaves -- every point
    # property, replaced keys included -- without scanning the curves per key
    # (2026-10-19: 20 curves x 2000 keys took 59 s).
    import math

    from blendertk.anim_utils._anim_utils import AnimUtils as _AU

    _KP_FLOAT = (
        ("co", 2),
        ("handle_left", 2),
        ("handle_right", 2),
        ("back", 1),
        ("amplitude", 1),
        ("period", 1),
    )
    _KP_INT = (
        "interpolation",
        "handle_left_type",
        "handle_right_type",
        "easing",
        "type",
        "select_control_point",
        "select_left_handle",
        "select_right_handle",
    )

    def _key_print(o):
        """Every custom property and every point property of *o*'s curves."""
        out = {}
        for k in o.keys():
            v = o[k]
            try:
                out[k] = [round(float(x), 6) for x in v]
            except TypeError:
                out[k] = round(float(v), 6) if isinstance(v, (int, float)) else v
        for fc in _AU.get_fcurves([o]):
            n = len(fc.keyframe_points)
            row = {}
            for name, width in _KP_FLOAT:
                buf = [0.0] * (n * width)
                fc.keyframe_points.foreach_get(name, buf)
                row[name] = buf
            for name in _KP_INT:
                buf = [0] * n
                fc.keyframe_points.foreach_get(name, buf)
                row[name] = buf
            out[(fc.data_path, fc.array_index)] = row
        return out

    def _first_diff(a, b):
        for key in sorted(set(a) | set(b), key=str):
            if a.get(key) != b.get(key):
                if isinstance(a.get(key), dict) and isinstance(b.get(key), dict):
                    for name in a[key]:
                        if a[key][name] != b[key].get(name):
                            pairs = list(zip(a[key][name], b[key].get(name) or []))
                            at = next(
                                (i for i, (x, y) in enumerate(pairs) if x != y), None
                            )
                            where = pairs[at] if at is not None else "length"
                            return f"{key} {name} @{at}: {where}"
                return f"{key}: {a.get(key)} != {b.get(key)}"
        return ""

    _landed_interps = {}

    def _land_per_key(o, records):
        """The oracle: Blender's own insert, ``set_key`` per key, each key's
        interpolation then on ITS point (``set_key`` restyles every point on
        the same rounded frame -- a subframe key's whole-frame neighbour too)."""
        for label, rec in records.items():
            prop, index = RenderEffects._channel_slot(o, label)
            if (
                prop in RenderEffects.CHANNELS
                or prop in RenderEffects.HIGHLIGHT_COLOR_STOPS.keys
            ):
                RenderEffects._ensure_channel(
                    [o],
                    RenderEffects.ATTR_NAME
                    if prop == RenderEffects.ATTR_NAME
                    else RenderEffects.HIGHLIGHT_ATTR,
                    auto_create=True,
                    delete_visibility_keys=False,
                )
            elif prop not in o.keys():
                o[prop] = 0.0 if index < 0 else [0.0, 0.0, 0.0]
            if rec.get("value") is not None:
                RenderEffects._set_prop(o, prop, index, float(rec["value"]))
            for f, v, interp in rec["keys"]:
                RenderEffects.set_key(
                    o,
                    f'["{prop}"]',
                    f,
                    v,
                    RenderEffects._BLENDER_FROM_INTERP[interp],
                    index=index,
                )
            # Every key landed on this curve so far, the last landing winning.
            intended = _landed_interps.setdefault((o.name, prop, index), {})
            for f, _v, interp in rec["keys"]:
                intended[float(f)] = RenderEffects._BLENDER_FROM_INTERP[interp]
            for k in RenderEffects._fcurve(o, f'["{prop}"]', index).keyframe_points:
                if k.co[0] in intended:  # whole and half frames: float32-exact
                    k.interpolation = intended[k.co[0]]

    def _records(n, labels=6, start=1.0, phase=0.0, sub=False):
        names = ["opacity", "highlight", "highlightColorR", "highlightColorG"]
        names += [f"user_{i}" for i in range(labels - len(names))]
        out = {}
        for j, label in enumerate(names[:labels]):
            keys = []
            for i in range(n):
                f = start + i + (0.5 if sub and i % 9 == 4 else 0.0)
                v = math.sin(0.05 * i + j + phase)
                interp = ("linear", "step", "smooth")[(i + j) % 3]
                keys.append([f, 0.5 + 0.5 * v if j < 2 else v, interp])
            out[label] = {"value": 0.25, "keys": keys}
        return out

    def _animator_keys(o):
        """An artist's curve the landing interleaves with: a deselected key it
        replaces, a deselected ELASTIC one it does not."""
        o["user_0"] = 0.0
        for f in range(-5, 90, 7):
            o["user_0"] = f * 0.1
            o.keyframe_insert('["user_0"]', frame=f)
        pts = RenderEffects._fcurve(o, '["user_0"]').keyframe_points
        pts[3].select_control_point = False
        pts[-1].interpolation = "ELASTIC"
        pts[-1].easing = "EASE_IN"
        pts[-1].select_control_point = False

    scenarios = {
        "fresh": (None, [_records(60)]),
        "re-applied, then an overlapping section with subframes": (
            None,
            [_records(40), _records(40), _records(40, start=20.0, phase=1.3, sub=True)],
        ),
        "between an animator's keys": (
            _animator_keys,
            [_records(30, start=0.0, phase=0.4)],
        ),
    }
    for _label, (_prep, _batches) in scenarios.items():
        _landed_interps.clear()
        _bulk = bpy.data.objects.new("Bulk", None)
        _ref = bpy.data.objects.new("Ref", None)
        for _o in (_bulk, _ref):
            bpy.context.collection.objects.link(_o)
            if _prep:
                _prep(_o)
        for _batch in _batches:
            RenderEffects.apply_channel_records(_bulk, _batch)
            _land_per_key(_ref, _batch)
        _a, _b = _key_print(_bulk), _key_print(_ref)
        check(
            f"apply_channel_records lands as set_key per key: {_label}",
            _a == _b,
            _first_diff(_a, _b),
        )
        bpy.data.objects.remove(_bulk)
        bpy.data.objects.remove(_ref)

    # A parked take's action: two objects animated through one action, a
    # slot each -- every key lands in its own object's slot.
    _landed_interps.clear()
    _pair = {}
    for _kind in ("bulk", "ref"):
        _act = bpy.data.actions.new(f"Shared|{_kind}")
        for _name, _phase in (("D", 0.0), ("E", 2.0)):
            _o = bpy.data.objects.new(f"{_name}_{_kind}", None)
            bpy.context.collection.objects.link(_o)
            _ad = _o.animation_data_create()
            _ad.action = _act
            _ad.action_slot = _act.slots.new("OBJECT", _name)
            _recs = _records(25, labels=3, phase=_phase)
            if _kind == "bulk":
                RenderEffects.apply_channel_records(_o, _recs)
            else:
                _land_per_key(_o, _recs)
            _pair.setdefault(_name, []).append(_o)
    check(
        "apply_channel_records lands in each object's own slot of a shared action",
        all(_key_print(a) == _key_print(b) for a, b in _pair.values())
        and len(_AU.get_fcurves([_pair["D"][0]])) == 3,
        [_first_diff(_key_print(a), _key_print(b)) for a, b in _pair.values()],
    )

    # Two deliberate departures from a per-key insert: each key's interpolation
    # lands on its own point only (``set_key`` restyled every point on the same
    # rounded frame), and an artist's FREE handles keep their authored shape
    # (an insert between two keys subdivides the segment, cutting them).
    _sub = bpy.data.objects.new("Subframe", None)
    bpy.context.collection.objects.link(_sub)
    _sub_keys = [[24, 0.0, "linear"], [24.5, 1.0, "step"], [25, 0.5, "smooth"]]
    RenderEffects.apply_channel_records(_sub, {"user_0": {"keys": _sub_keys}})
    _sub_interps = [
        k.interpolation
        for k in RenderEffects._fcurve(_sub, '["user_0"]').keyframe_points
    ]
    check(
        "apply_channel_records: a subframe key leaves its whole-frame "
        "neighbour's interpolation",
        _sub_interps == ["LINEAR", "CONSTANT", "BEZIER"],
        _sub_interps,
    )
    _free = bpy.data.objects.new("FreeHandles", None)
    bpy.context.collection.objects.link(_free)
    for _f, _v in ((0, 0.0), (10, 1.0), (20, 0.0)):
        _free["user_0"] = _v
        _free.keyframe_insert('["user_0"]', frame=_f)
    _pts_free = RenderEffects._fcurve(_free, '["user_0"]').keyframe_points
    _pts_free[1].handle_left_type = _pts_free[1].handle_right_type = "FREE"
    _pts_free[1].handle_left = (8.0, 5.0)
    _pts_free[1].handle_right = (12.0, -1.0)
    RenderEffects.apply_channel_records(
        _free, {"user_0": {"keys": [[5, 0.25, "linear"], [10, 3.0, "smooth"]]}}
    )
    _by_frame = {k.co[0]: k for k in _pts_free}
    check(
        "apply_channel_records: a replaced FREE key keeps its handles, "
        "shifted with its value",
        sorted(_by_frame) == [0.0, 5.0, 10.0, 20.0]
        and tuple(_by_frame[10.0].co) == (10.0, 3.0)
        and (_by_frame[10.0].handle_left_type, _by_frame[10.0].handle_right_type)
        == ("FREE", "FREE")
        and tuple(_by_frame[10.0].handle_left) == (8.0, 7.0)
        and tuple(_by_frame[10.0].handle_right) == (12.0, 1.0)
        and _by_frame[5.0].handle_left_type == "AUTO_CLAMPED",
        [(tuple(k.co), tuple(k.handle_left), tuple(k.handle_right)) for k in _pts_free],
    )

    # The complexity guard: one curve lookup per label (two when it makes the
    # curve), never a per-key insert with its curve scan.
    _calls = {"_fcurve": 0, "set_key": 0}
    _orig = {name: RenderEffects.__dict__[name] for name in _calls}

    def _counting(name):
        inner = _orig[name].__func__

        def spy(*args, **kwargs):
            _calls[name] += 1
            return inner(*args, **kwargs)

        return staticmethod(spy)

    try:
        for _name in _calls:
            setattr(RenderEffects, _name, _counting(_name))
        _guard = bpy.data.objects.new("Guard", None)
        bpy.context.collection.objects.link(_guard)
        _recs = _records(200, labels=5)
        RenderEffects.apply_channel_records(_guard, _recs)
    finally:
        for _name, _fn in _orig.items():
            setattr(RenderEffects, _name, _fn)
    check(
        "apply_channel_records: no per-key insert, a curve lookup or two per label",
        _calls["set_key"] == 0 and _calls["_fcurve"] <= 2 * len(_recs),
        _calls,
    )

except Exception as e:
    traceback.print_exc()
    check("test harness raised", False, repr(e))

passed = sum(1 for ln in lines if ln.startswith("OK"))
for ln in lines:
    print(ln)
result = "PASS" if all(ln.startswith("OK") for ln in lines) else "FAIL"
print(f"===RESULT: {result}=== ({passed}/{len(lines)})")
