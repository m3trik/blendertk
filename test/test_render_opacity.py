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
    # Hand-key ONLY opacity (no visibility). An opacity at 0 is presence by the
    # render_effects contract, so the export writes no mirror -- mirror of
    # mayatk (2026-09-14).
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
    # Version 2: the track names its channels; the fade's hide_render keys are
    # the opacity's mirror, so the record names the opacity alone.
    check(
        "the track names the opacity and not its mirror",
        track == {"node": c.name, "channels": ["opacity"]},
        detail=repr(track),
    )
    # A keyed hide_render with no opacity is its own channel, and its proxy
    # carries it visible-at-1 (the contract's, not Blender's hidden-at-1).
    bl = cube("Blink")
    RenderEffects.set_key(bl, "hide_render", 1, 0.0, "CONSTANT")
    RenderEffects.set_key(bl, "hide_render", 10, 1.0, "CONSTANT")
    blink = next((t for t in RenderEffects.visibility_tracks() if t["node"] == bl.name), None)
    check(
        "a keyed hide_render alone publishes visibility",
        blink == {"node": bl.name, "channels": ["visibility"]},
        detail=repr(blink),
    )
    staged = {p.name: p for p in RenderEffects.stage_export_proxies()}
    vis_proxy = staged.get("Blink__visibility")
    vis_keys = (
        [
            (k.co[0], k.co[1], k.interpolation)
            for k in next(
                f for f in vis_proxy.animation_data.action.fcurves if f.data_path == "scale"
            ).keyframe_points
        ]
        if vis_proxy is not None
        and vis_proxy.animation_data
        and getattr(vis_proxy.animation_data.action, "fcurves", None)
        else None
    )
    RenderEffects.remove_export_proxies()
    check(
        "the visibility proxy is stepped and visible at 1",
        vis_proxy is not None
        and "Gate__visibility" not in staged
        and (vis_keys is None or vis_keys == [(1.0, 1.0, "CONSTANT"), (10.0, 0.0, "CONSTANT")]),
        detail=repr((sorted(staged), vis_keys)),
    )
    bpy.data.objects.remove(bl, do_unlink=True)

    # The record states no frame and no rate: the curves ride the takes.
    ptk.SceneRecords.SHOTS.save(DataNodes, {"shots": [{"clip": "Shot_1"}]})
    RenderEffects.refresh_export_metadata()
    published = ptk.SceneRecords.VISIBILITY.load(DataNodes) or {}
    check(
        "refresh_export_metadata publishes the channel",
        published.get("version") == RenderEffects.SCHEMA_VERSION == 2,
    )
    check(
        "the record holds no frame and no rate",
        set(published) == {"version", "tracks"},
        detail=repr(sorted(published)),
    )
    ptk.SceneRecords.SHOTS.clear(DataNodes)

    # A scoped write states the tracks of what it ships (mirror of mayatk's):
    # another object's track warned "not in this GLB". Added: 2026-10-31
    sc_other = cube("Other")
    sc_other.data.materials.append(mat("OtherM"))
    RenderEffects.create([sc_other])
    RenderEffects.key_fade([sc_other], start=3, end=40, direction="in")
    sc_ctx = ptk.ExportContext(scope=(c.name,), nodes=frozenset({c.name}))
    sc_record = RenderEffects.export_record(sc_ctx)
    check(
        "a scoped write's record names the tracks of what it ships",
        sc_record is not None
        and [t["node"] for t in sc_record.payload["tracks"]] == [c.name],
        detail=repr(sc_record and sc_record.payload.get("tracks")),
    )
    sc_whole = RenderEffects.export_record(ptk.ExportContext())
    check(
        "an unscoped write names every keyed object",
        sc_whole is not None
        and sorted(t["node"] for t in sc_whole.payload["tracks"])
        == sorted([c.name, sc_other.name]),
    )
    RenderEffects.remove([sc_other])
    bpy.data.objects.remove(sc_other)

    # The range the write BAKES: the scene's extent WIDENED by the takes the
    # shot store declares (ShotStore.declared_takes), which apply_declared_takes
    # arms AFTER the producers, so bake_range reproduces the widening rather
    # than read it off the scene. The record's clips state no range and widen
    # nothing.
    from blendertk import ShotStore
    from blendertk.env_utils.fbx_utils import FbxUtils as _Fbx

    _scene = bpy.context.scene
    _scene.frame_start, _scene.frame_end = 0, 100
    ShotStore._prefs_dir_override = ptk.TempArtifacts(
        "btk_render_opacity_prefs"
    ).dir_path()
    ShotStore.clear_active()
    check(
        "with no shots the bake range is the scene range",
        _Fbx.bake_range() == (0.0, 100.0),
        detail=repr(_Fbx.bake_range()),
    )
    ptk.SceneRecords.SHOTS.save(
        DataNodes, {"shots": [{"clip": "a"}, {"clip": "b"}]}
    )
    check(
        "the record's clips widen nothing: they state no range",
        _Fbx.bake_range() == (0.0, 100.0),
        detail=repr(_Fbx.bake_range()),
    )
    ptk.SceneRecords.SHOTS.clear(DataNodes)
    _arm_store = ShotStore.active()
    _arm_store.define_shot("a", 33, 60)
    _arm_store.define_shot("b", 61, 140)
    # 0 stays (the scene starts before the first take -- the exact case that
    # made a takes-union answer wrong); 140 widens past the scene's end.
    check(
        "the store's shots widen the bake range, never narrow it",
        _Fbx.bake_range() == (0.0, 140.0),
        detail=repr(_Fbx.bake_range()),
    )
    for _sid in [s.shot_id for s in list(_arm_store.shots)]:
        _arm_store.remove_shot(_sid)
    ShotStore.clear_active()

    # What a take carries, through FbxUtils.export rather than raw bpy.ops:
    # Blender's take CLIPS to the range it is baked over, so a curve keyed
    # 0-100 is written as 60 frames under a 20-80 range -- and an armed take
    # REPLACES the scene-range stack, so the file's one take is that window.
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
        """Frames the real export path writes for the probe, under *takes*,
        and the file's take names."""
        _scene.frame_start, _scene.frame_end = 20, 80
        _Fbx._pending_takes = takes
        seen = {a.name for a in bpy.data.actions}
        try:
            _Fbx.export(filepath=_fbx, objects=[_cube], bake_anim=True)
            names = ptk.FbxFile.load(_fbx, raw_payloads=False).take_names()
            bpy.ops.import_scene.fbx(filepath=_fbx)
        except Exception as exc:  # noqa: BLE001
            return f"export/import failed: {exc}", None
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
        return (max(spans) if spans else None), names

    _armed, _armed_names = _written_span([("probe", 20, 80)])
    check(
        "takes armed: the file's one take is the armed window, no scene-range take",
        _armed == 60 and _armed_names == ["probe"],
        detail=f"authored 100 frames, wrote {_armed} under a 20-80 range; takes {_armed_names}",
    )

    _bare, _bare_names = _written_span(None)
    check(
        "no takes: one scene-range take over the range it is baked over",
        _bare == 60 and len(_bare_names or ()) == 1,
        detail=f"authored 100 frames, wrote {_bare}; takes {_bare_names}",
    )

    _Fbx._pending_takes = _prior_takes
    try:
        os.remove(_fbx)
    except OSError:
        pass
    for _o in [o for o in bpy.data.objects if o.name.startswith("ClipSpanProbe")]:
        bpy.data.objects.remove(_o, do_unlink=True)
    _scene.frame_start, _scene.frame_end = 0, 100
    ShotStore._prefs_dir_override = None
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
        "a highlight-only object publishes its channel and colour",
        t is not None
        and t["channels"] == ["highlight"]
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
    curves = (overlay.get(_ptk.MeshConvert.EFFECT_PREVIEW_KEY) or {}).get("curves") or [{}]
    check(
        "preview_channels overlays the planned fade on the named object",
        tracks[0] == {"node": "PreviewPulsed", "channels": ["opacity"]}
        and curves[0].get("target") == "PreviewPulsed"
        and [[round(t * _fps, 4), v] for t, v in curves[0].get("keys")]
        == [[round(f, 4), v] for f, v in expected],
        detail=f"{tracks} {curves}",
    )
    check(
        "the overlay writes nothing and leaves the object's own pulse out",
        RenderEffects.ATTR_NAME not in p
        and len(RenderEffects._fcurve(p, '["highlight"]').keyframe_points) == pulse_keys
        and tracks[0]["channels"] == ["opacity"],
        detail=f"{list(p.keys())} {tracks[0]}",
    )

    # The fade's preview runs the EXPORTER's own function, so it cannot drift
    # from the deliverable: alpha on the fourth lane of baseColorFactor.
    _fade_spec = _GLTF["opacity"]
    _faded = _fade_spec.values([0.78, 0.80, 0.84, 1.0], 0.4, (), {})
    check(
        "the fade's published value is the albedo with alpha on lane four",
        len(_faded) == 4 and approx(_faded[3], 0.4) and approx(_faded[0], 0.78),
        detail=f"{_faded}",
    )

    # ---- render_effects v2 on a real export (mirror of mayatk's) -------------
    # Two shots, one take each; a box fading out in the first and in again in
    # the second, a sphere pulsing, a cube switched off and on by hide_render.
    # Read back with ptk.FbxFile and in the GLB built from it. Added: 2026-11-05
    import json as _json
    import tempfile as _tempfile

    from blendertk.anim_utils.shots._shots import ShotStore as _ShotStore
    from blendertk.env_utils.fbx_utils import FbxUtils as _FbxUtils

    reset()
    _scene = bpy.context.scene
    _scene.render.fps, _scene.render.fps_base = 24, 1.0
    _scene.frame_start, _scene.frame_end = 1, 48
    fader = cube("fader")
    fader.data.materials.append(bpy.data.materials.new("faderMat"))
    RenderEffects.key_fade([fader], start=5, end=15, direction="out")
    RenderEffects.key_fade([fader], start=30, end=40, direction="in")
    glower = cube("glower")
    glower.data.materials.append(bpy.data.materials.new("glowerMat"))
    RenderEffects.key_pulse([glower], start=1, end=48, period=24, color=(1.0, 0.5, 0.0))
    blinker = cube("blinker")
    blinker.data.materials.append(bpy.data.materials.new("blinkerMat"))
    for _frame, _hidden in ((1, 0.0), (10, 1.0), (30, 0.0)):
        RenderEffects.set_key(blinker, "hide_render", _frame, _hidden, "CONSTANT")
    _store = _ShotStore()
    _ShotStore.set_active(_store)
    _store.define_shot("ShotA", 1, 24, objects=[o.name for o in (fader, glower, blinker)])
    _store.define_shot("ShotB", 25, 48, objects=[o.name for o in (fader, glower, blinker)])
    _out = _tempfile.mkdtemp(prefix="btk_render_effects_v2_")
    _fbx = os.path.join(_out, "effects.fbx")
    try:
        # The Scene Exporter's shape: one bracket publishes the records and
        # stages the curve proxies, and the write ships them with the carrier.
        with _FbxUtils.export_prepared(_FbxUtils.export_context(), stagers=("render_effects",)):
            _FbxUtils.apply_takes_from_node()
            _FbxUtils.export(
                filepath=_fbx,
                objects=[fader, glower, blinker, DataNodes.EXPORT],
                selection_only=True,
                bake_anim=True,
                object_types={"MESH", "EMPTY"},
            )
    finally:
        _ShotStore.clear_active()
    _file = ptk.FbxFile.load(_fbx, decode_arrays=ptk.FbxFile.CURVE_ARRAYS)
    _raw = [
        v for _o, k, v in _file.user_property_items() if k == ptk.SceneRecords.VISIBILITY.key
    ]
    _record = _json.loads(bytes(_raw[0]).decode("utf-8")) if _raw else {}
    check(
        "the FBX takes are the shots",
        sorted(_file.take_names()) == ["ShotA", "ShotB"],
        detail=repr(_file.take_names()),
    )
    check(
        "the FBX record is v2 and names each node's channels",
        _record.get("version") == 2
        and set(_record) == {"version", "tracks"}
        and {t["node"]: t["channels"] for t in _record.get("tracks") or ()}
        == {"blinker": ["visibility"], "fader": ["opacity"], "glower": ["highlight"]},
        detail=repr(_record),
    )
    _curves = ptk.MeshConvert.render_effect_curves(_file)
    check(
        "every channel's proxy curve is read off the FBX in each take",
        all(
            {("fader", "opacity"), ("glower", "highlight"), ("blinker", "visibility")}
            <= set(_curves.get(_take, {}))
            for _take in ("ShotA", "ShotB")
        ),
        detail=repr({k: sorted(v) for k, v in _curves.items()}),
    )
    _zero = 1 / 30.0  # ShotA opens on frame 1, floored to the 1/30 s grid
    _at = {
        round((t + _zero) * 24, 2): v
        for t, v, _i in _curves.get("ShotA", {}).get(("fader", "opacity"), ())
    }
    check(
        "the fade-out reaches 0 at frame 15 of the timeline",
        approx(_at.get(5.0, -1), 1.0) and approx(_at.get(15.0, -1), 0.0),
        detail=repr(sorted(_at.items())[:20]),
    )
    _steps = {
        round((t + _zero) * 24): round(v)
        for t, v, _i in _curves.get("ShotA", {}).get(("blinker", "visibility"), ())
    }
    check(
        "the visibility proxy is visible at 1 and off from frame 10",
        _steps.get(1) == 1 and _steps.get(10) == 0,
        detail=repr(sorted(_steps.items())),
    )
    _verdict = ptk.ExportVerifier(fbx=_fbx, sidecar=None).run(["check_fbx_render_effects"])
    check("the record names exactly the proxies", _verdict.ok, _verdict.summary())
    _glb = os.path.join(_out, "effects.glb")
    ptk.MeshConvert.fbx_to_glb(_fbx, dst=_glb, overwrite=True, prompt=False, lightmaps=False)
    with ptk.MeshConvert.open_glb(_glb) as _edit:
        _gltf = _edit.gltf
    _names = {i: n.get("name") for i, n in enumerate(_gltf["nodes"])}
    _played = {}
    for _anim in _gltf.get("animations") or []:
        for _ch in _anim["channels"]:
            _target = _ch["target"]
            _note = (_ch.get("extras") or {}).get("render_effects")
            if _target.get("path") == "pointer" and _note:
                _played.setdefault(_anim["name"], set()).add((_note["node"], _note["channel"]))
            elif _target.get("path") == "scale" and _names.get(_target.get("node")) in ("fader", "blinker"):
                _played.setdefault(_anim["name"], set()).add((_names[_target["node"]], "gate"))
    check(
        "the GLB plays each channel in each clip from the file's curves",
        all(
            {("fader", "opacity"), ("glower", "highlight"), ("fader", "gate"), ("blinker", "gate")}
            <= _played.get(_clip, set())
            for _clip in ("ShotA", "ShotB")
        ),
        detail=repr({k: sorted(v) for k, v in _played.items()}),
    )
    check(
        "no GLB material is written BLEND (opaque at rest)",
        {m.get("alphaMode", "OPAQUE") for m in _gltf["materials"]} == {"OPAQUE"},
        detail=repr([m.get("alphaMode") for m in _gltf["materials"]]),
    )
    check(
        "the GLB carries the record verbatim and no proxy",
        (_gltf.get("extras") or {}).get(ptk.SceneRecords.VISIBILITY.key) == _record
        and not [n for n in _names.values() if n and "__" in n],
        detail=repr(sorted(n for n in _names.values() if n)),
    )

    # ---- the same scene pushed with Include Animation off (the default) -----
    # Bug: such a push played the scene's effects until the curves moved into
    # the takes (render_effects v2); its FBX has no take, so nothing played.
    # Fixed: 2026-11-05 -- the push states the scene's curves in its build's
    # preview overlay, in seconds from the bake range's start (where an
    # animated write opens its clip), and the GLB plays them on its one clip.
    # A real push; its build fed exactly what the push handed its deliverer.
    import struct as _struct

    from blendertk.env_utils.webxr_preview import WebXrPreview as _WebXrPreview

    class _Build(ptk.Deliverer):
        seen = {}

        def preflight(self, bridge, request):
            return True

        def deliver(self, bridge, payload, request):
            glb = os.path.join(_out, "pushed.glb")
            overlay = request.get("data_export")
            type(self).seen.update(
                takes=ptk.FbxFile.load(payload.primary).take_names(), overlay=overlay
            )
            ptk.MeshConvert.fbx_to_glb(
                payload.primary, dst=glb, overwrite=True, prompt=False,
                lightmaps=False, data_export=overlay,
            )
            return {"glb": glb}

    # With shots declared, one clip per take an animated push splits (fixed
    # 2026-11-07: the first fix gave them one Take 001, where HEAD gave one
    # clip per declared take -- the page's shots and their sequence), each
    # from its shot's start and spanning it, the record's descriptions beside.
    _FbxUtils.reset_takes()  # the takes the export above armed
    _ShotStore.set_active(_store)
    for _shot, _text in zip(_store.sorted_shots(), ("the fade out", "the fade in")):
        _shot.description = _text
    _store.publish_export_view()
    _bridge = _WebXrPreview()
    _bridge.deliverer = _Build()
    try:
        _pushed = _bridge.push(objects=[fader, glower, blinker], open_browser=False)
    finally:
        _ShotStore.clear_active()
    _seen = _Build.seen
    _overlay = _seen.get("overlay") or {}
    _stated = (_overlay.get(ptk.MeshConvert.EFFECT_PREVIEW_KEY) or {}).get("clips") or []
    check(
        "an animation-off push writes no take and states one clip per shot",
        _pushed is not None
        and not _seen.get("takes")
        and list(_overlay) == [ptk.MeshConvert.EFFECT_PREVIEW_KEY]
        and [c["name"] for c in _stated] == ["ShotA", "ShotB"]
        and all(
            {(c["target"], c["channel"]) for c in _clip["curves"]}
            == {("fader", "opacity"), ("glower", "highlight"), ("blinker", "visibility")}
            for _clip in _stated
        ),
        detail=f"{_pushed} {_seen.get('takes')} {list(_overlay)} {[c.get('name') for c in _stated]}",
    )
    _clips = {}
    if _pushed is not None:
        with ptk.MeshConvert.open_glb(_pushed["glb"]) as _edit:
            _pg, _blob = _edit.gltf, _edit.bin_data

        def _read(index):
            acc = _pg["accessors"][index]
            per = {"SCALAR": 1, "VEC3": 3, "VEC4": 4}[acc["type"]]
            view = _pg["bufferViews"][acc["bufferView"]]
            off = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
            flat = _struct.unpack_from(f"<{acc['count'] * per}f", _blob, off)
            return [flat[i : i + per] for i in range(0, len(flat), per)]

        _pnames = {i: n.get("name") for i, n in enumerate(_pg["nodes"])}
        for _anim in _pg.get("animations") or []:
            _clip = _clips.setdefault(_anim["name"], {})
            for _ch in _anim["channels"]:
                _target, _sampler = _ch["target"], _anim["samplers"][_ch["sampler"]]
                if _target.get("path") == "pointer":
                    _note = (_ch.get("extras") or {}).get("render_effects") or {}
                    _key = (_note.get("node"), _note.get("channel"))
                elif _target.get("path") == "scale":
                    _key = (_pnames.get(_target.get("node")), "gate")
                else:
                    continue
                _clip.setdefault(
                    _key, ([t[0] for t in _read(_sampler["input"])], _read(_sampler["output"]))
                )
    _record = {}
    if _pushed is not None:
        _record = (_pg.get("extras") or {}).get(ptk.SceneRecords.SHOTS.key) or {}
    check(
        "the GLB's record names, describes and orders the shots, at the scene's rate",
        [(x.get("clip"), x.get("description")) for x in _record.get("shots") or ()]
        == [("ShotA", "the fade out"), ("ShotB", "the fade in")]
        and _record.get("fps") == 24.0,
        detail=repr(_record),
    )
    _a, _b = _clips.get("ShotA", {}), _clips.get("ShotB", {})
    check(
        "the GLB plays every effect on each shot's clip, in order",
        list(_clips) == ["ShotA", "ShotB"]
        and all(
            {("fader", "opacity"), ("glower", "highlight"), ("fader", "gate"), ("blinker", "gate")}
            <= set(_c)
            for _c in (_a, _b)
        ),
        detail=repr({k: sorted(v) for k, v in _clips.items()}),
    )
    check(
        "each clip spans its shot (23 frames, first to last)",
        all(
            _c and approx(max(t[-1] for t, _v in _c.values()), 23 / 24.0, 1e-3)
            for _c in (_a, _b)
        ),
        detail=repr([max((t[-1] for t, _v in _c.values()), default=None) for _c in (_a, _b)]),
    )
    _zero_a = [t for t, v in zip(*_a.get(("fader", "opacity"), ([], []))) if v[3] <= 1e-6]
    _zero_b = [t for t, v in zip(*_b.get(("fader", "opacity"), ([], []))) if v[3] <= 1e-6]
    check(
        "from each shot's start: 0 at frame 15 in ShotA; ShotB opens at 0 until frame 30",
        bool(_zero_a) and approx(_zero_a[0], 14 / 24.0, 1e-3)
        and bool(_zero_b) and approx(_zero_b[0], 0.0, 1e-3) and approx(_zero_b[-1], 5 / 24.0, 1e-3),
        detail=repr((_zero_a, _zero_b)),
    )
    _gate_a = [
        (round(t * 24, 2), int(v[0] > 0.0)) for t, v in zip(*_a.get(("blinker", "gate"), ([], [])))
    ]
    _gate_b = [
        (round(t * 24, 2), int(v[0] > 0.0)) for t, v in zip(*_b.get(("blinker", "gate"), ([], [])))
    ]
    check(
        "blinker steps off at frame 10 (ShotA) and on at frame 30 (ShotB)",
        (9.0, 0) in _gate_a and _gate_b[:1] == [(0.0, 0)] and (5.0, 1) in _gate_b,
        detail=repr((_gate_a, _gate_b)),
    )
    _glow = _a.get(("glower", "highlight"), ([], []))[1]
    _peak = tuple(round(c, 3) for c in max(_glow, key=sum)) if _glow else None
    check(
        "the pulse peaks in the colour it was keyed with",
        _peak == (1.0, 0.5, 0.0),
        detail=repr(_peak),
    )

    # ---- a still shot is a clip, on an animated push and an animation-off one
    # Bug: a shot in which nothing moves had no glTF animation (glTF forbids
    # an empty one; FBX2glTF drops the take), so the page lost it from its
    # picker and sequence; at HEAD it was a window in the whole-timeline take.
    # Fixed: 2026-11-07 -- every declared take, and every clip an overlay
    # states, is a clip spanning its shot, a still one by a hold. ShotC
    # (frames 49-72) holds still. Mirror of mayatk's.
    import math as _math

    class _BuildTo(ptk.Deliverer):
        path = None

        def preflight(self, bridge, request):
            return True

        def deliver(self, bridge, payload, request):
            ptk.MeshConvert.fbx_to_glb(
                payload.primary, dst=type(self).path, overwrite=True, prompt=False,
                lightmaps=False, data_export=request.get("data_export"),
            )
            return {"glb": type(self).path}

    _ShotStore.set_active(_store)
    _store.define_shot("ShotC", 49, 72, objects=[o.name for o in (fader, glower, blinker)], description="the title")
    _store.publish_export_view()
    try:
        for _animated in (True, False):
            _FbxUtils.reset_takes()
            _BuildTo.path = os.path.join(_out, f"still_{_animated}.glb")
            _bridge = _WebXrPreview()
            _bridge.deliverer = _BuildTo()
            _done = _bridge.push(
                objects=[fader, glower, blinker], open_browser=False, INCLUDE_ANIMATION=_animated
            )
            _spans, _shots, _effects = {}, [], {}
            if _done is not None:
                with ptk.MeshConvert.open_glb(_done["glb"]) as _edit:
                    _spans = {
                        a["name"]: ptk.MeshConvert._animation_time_span(_edit, a)[1]
                        for a in _edit.gltf.get("animations") or []
                    }
                    _effects = {
                        a["name"]: {
                            ((c.get("extras") or {}).get("render_effects") or {}).get("channel")
                            for c in a["channels"]
                            if c["target"].get("path") == "pointer"
                        }
                        for a in _edit.gltf.get("animations") or []
                    }
                    _shots = ((_edit.gltf.get("extras") or {}).get(ptk.SceneRecords.SHOTS.key) or {}).get("shots") or []
            # From the clip's t=0 to the shot's end: an animated take's t=0 is
            # its first key floored to the converter's 1/30 s grid (frame 49 at
            # 24 fps -> 61/30 s); an overlay's clip opens on the shot's first.
            _zero = _math.floor(49 / 24.0 * 30.0) / 30.0 if _animated else 49 / 24.0
            check(
                f"a still shot is a clip spanning its shot (animation {'on' if _animated else 'off'})",
                list(_spans) == ["ShotA", "ShotB", "ShotC"]
                and approx(_spans.get("ShotC") or 0.0, 72 / 24.0 - _zero, 1e-4)
                and len(_shots) == 3
                and _shots[2] == {"clip": "ShotC", "description": "the title"},
                detail=f"{_spans} {_shots[2:] if _shots else _shots}",
            )
            # The effects play in the shots that key them, whichever way the
            # push carries them (Bug: an animated push staged no curve proxy,
            # so its effects played nowhere. Fixed: 2026-11-07, the render
            # effects stager, as mayatk's WebXrPreview names it).
            check(
                f"the shots' effects play (animation {'on' if _animated else 'off'})",
                all(
                    {"opacity", "highlight"} <= _effects.get(_shot, set())
                    for _shot in ("ShotA", "ShotB")
                ),
                detail=repr(_effects),
            )
    finally:
        _FbxUtils.reset_takes()
        _ShotStore.clear_active()

    # ---- an emissive group's weight on an animation-off push ---------------
    # Bug: a group's weight rides its copy's <material>__emission proxy, in the
    # takes, so an animation-off push played no group's glow. Fixed: 2026-11-07
    # -- effect_curves states each animated copy's weight
    # (EmissiveGroups.weight_curves) and the overlay plays it on each shot's
    # clip, starting, ending and peaking where the animated push's does.
    # Mirror of mayatk's.
    import struct as _eg_struct

    from blendertk.mat_utils._mat_utils import MatUtils as _MatUtils
    from blendertk.mat_utils.emissive_groups import EmissiveGroups as _EmissiveGroups

    _glow_obj = cube("glowPanel")
    _glow_mat = bpy.data.materials.new("glowPanelMat")
    _bsdf = next(
        n for n in _MatUtils.get_node_tree(_glow_mat, create=True).nodes if n.type == "BSDF_PRINCIPLED"
    )
    _bsdf.inputs["Emission Strength"].default_value = 2.0
    _bsdf.inputs["Emission Color"].default_value = (0.2, 0.6, 1.0, 1.0)
    _glow_obj.data.materials.append(_glow_mat)
    _EmissiveGroups.add_group("signal", {_glow_obj.name: [0, 1]})
    for _frame, _value in ((1, 0.0), (20, 1.0), (21, 1.0), (40, 0.0)):
        _EmissiveGroups.key_weight("signal", _value, frame=_frame)
    _glow_store = _ShotStore()
    _ShotStore.set_active(_glow_store)
    _glow_store.define_shot("ShotA", 1, 20, objects=[_glow_obj.name])
    _glow_store.define_shot("ShotB", 21, 40, objects=[_glow_obj.name])
    _glow_store.publish_export_view()

    def _emission_by_clip(path):
        with ptk.MeshConvert.open_glb(path) as edit:
            gltf, blob = edit.gltf, edit.bin_data
        out = {}
        for animation in gltf.get("animations") or []:
            for channel in animation["channels"]:
                target = channel["target"]
                pointer = (target.get("extensions") or {}).get("KHR_animation_pointer", {}).get("pointer", "")
                if target.get("path") != "pointer" or not pointer.endswith("/emissiveFactor"):
                    continue
                acc = gltf["accessors"][animation["samplers"][channel["sampler"]]["output"]]
                view = gltf["bufferViews"][acc["bufferView"]]
                offset = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
                flat = _eg_struct.unpack_from(f"<{acc['count'] * 3}f", blob, offset)
                out[animation["name"]] = [
                    tuple(round(c, 3) for c in flat[i : i + 3]) for i in range(0, len(flat), 3)
                ]
        return out

    _glow = {}
    try:
        for _animated in (True, False):
            _FbxUtils.reset_takes()
            _BuildTo.path = os.path.join(_out, f"glow_{_animated}.glb")
            _bridge = _WebXrPreview()
            _bridge.deliverer = _BuildTo()
            _done = _bridge.push(objects=[_glow_obj], open_browser=False, INCLUDE_ANIMATION=_animated)
            _glow[_animated] = _emission_by_clip(_done["glb"]) if _done else {}
    finally:
        _FbxUtils.reset_takes()
        _ShotStore.clear_active()
    check(
        "an emissive group's weight plays on an animation-off push as on an animated one",
        sorted(_glow.get(True, {})) == ["ShotA", "ShotB"]
        and sorted(_glow.get(False, {})) == ["ShotA", "ShotB"]
        and all(
            _glow[False][_c][0] == _glow[True][_c][0]
            and _glow[False][_c][-1] == _glow[True][_c][-1]
            and max(_glow[False][_c], key=sum) == max(_glow[True][_c], key=sum)
            for _c in ("ShotA", "ShotB")
        ),
        detail=repr(
            {k: {c: (v[0], v[-1], max(v, key=sum)) for c, v in d.items()} for k, d in _glow.items()}
        ),
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
    from blendertk.anim_utils.shots._shots import BlenderShotsHost

    written = RenderEffects.apply_effect(
        fx, "fade_in", 10, 100, _fade, fps=24, curve_key=BlenderShotsHost.curve_key
    )
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
