"""blendertk key_stash headless test — parking keys outside the working animation and
bringing them back (mirror of mayatk's ``test_key_stash.py``).

Each check is a claim the design rests on, probed live before the code was written
(Blender 5.1): a stashed clip has zero evaluation effect and does not export, survives
save/reopen, retrieves the exact keys, previews through transient NLA tracks that leave
no trace, and its persistence channel never touches the shot store's.

Run: blender --background --factory-startup --python blendertk/test/test_key_stash.py
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


KEYS = [(1, 0.0), (10, 5.0), (20, 10.0), (30, 15.0), (40, 20.0)]

try:
    import bpy
    import blendertk as btk  # noqa: F401
    from blendertk.anim_utils.key_stash._key_stash import KeyStash
    from blendertk.anim_utils.shots._shots import (
        BlenderScenePersistence,
        BlenderShotStore,
    )

    TEMP = os.path.join(HERE, "temp_tests")
    os.makedirs(TEMP, exist_ok=True)

    def reset_store():
        backend = KeyStash._persistence
        if backend is not None and hasattr(backend, "remove_callbacks"):
            backend.remove_callbacks()
        KeyStash._active = None
        KeyStash.set_persistence(None)

    def reset():
        bpy.ops.wm.read_factory_settings(use_empty=True)
        reset_store()

    def keyed_cube(name="Cube"):
        bpy.ops.mesh.primitive_cube_add()
        o = bpy.context.object
        o.name = name
        for f, v in KEYS:
            o.location.x = v
            o.keyframe_insert("location", index=0, frame=f)
        return o

    def fc_x(o):
        return next(
            (
                fc
                for fc in BlenderShotStore.iter_action_fcurves(o)
                if fc.data_path == "location" and fc.array_index == 0
            ),
            None,
        )

    def frames(fc):
        return [k.co.x for k in fc.keyframe_points] if fc is not None else []

    def ev(o, f):
        bpy.context.scene.frame_set(f)
        return round(o.matrix_world.translation.x, 4)

    # ---- stash parks keys ----
    reset()
    cube = keyed_cube()
    fc = fc_x(cube)
    clip = KeyStash.active().stash(objects=[cube], time_range=(10, 30))
    check(
        "stash parks keys off the live fcurve",
        frames(fc) == [1, 40] and clip.key_count == 3 and clip.label == "Cube 10-30",
        f"{frames(fc)} {clip}",
    )
    action = bpy.data.actions.get(clip.curves[0]["action"])
    check(
        "stash action is an orphan kept by a fake user",
        action is not None and action.use_fake_user and action.users == 1,
        f"{action} users={getattr(action, 'users', None)}",
    )
    rec = clip.curves[0]
    check(
        "record carries object / data_path / index / slot",
        (rec["object"], rec["data_path"], rec["array_index"]) == ("Cube", "location", 0)
        and rec["slot"],
        f"{rec}",
    )

    # ---- zero evaluation effect ----
    with_stash = {f: ev(cube, f) for f in (5, 20, 35)}
    KeyStash.active().drop(clip.clip_id)
    plain = {f: ev(cube, f) for f in (5, 20, 35)}
    check(
        "stashed clip has zero evaluation effect (identical to a plain delete)",
        with_stash == plain and bpy.data.actions.get(rec["action"]) is None,
        f"{with_stash} vs {plain}",
    )

    # ---- retrieve exact ----
    reset()
    cube = keyed_cube()
    fc = fc_x(cube)
    fc.keyframe_points[2].interpolation = "CONSTANT"  # frame 20
    clip = KeyStash.active().stash(objects=[cube], time_range=(10, 30))
    n = KeyStash.active().retrieve(clip.clip_id)
    fc = fc_x(cube)
    check(
        "retrieve restores the exact keys, values and interpolation, and forgets the clip",
        n == 3
        and frames(fc) == [1, 10, 20, 30, 40]
        and [round(k.co.y, 4) for k in fc.keyframe_points] == [v for _, v in KEYS]
        and fc.keyframe_points[2].interpolation == "CONSTANT"
        and KeyStash.active().is_empty()
        and bpy.data.actions.get(clip.curves[0]["action"]) is None,
        f"n={n} {frames(fc)}",
    )

    # ---- retrieve at offset ----
    reset()
    cube = keyed_cube()
    clip = KeyStash.active().stash(objects=[cube], time_range=(10, 30))
    KeyStash.active().retrieve(clip.clip_id, at=100)
    check(
        "retrieve at an offset",
        frames(fc_x(cube)) == [1, 40, 100, 110, 120],
        f"{frames(fc_x(cube))}",
    )

    # ---- selected keys, per curve ----
    reset()
    cube = keyed_cube()
    for f, v in ((5, 0.1), (15, 0.2), (25, 0.3)):
        cube.rotation_euler.z = v
        cube.keyframe_insert("rotation_euler", index=2, frame=f)
    fx = fc_x(cube)
    frz = next(
        fc
        for fc in BlenderShotStore.iter_action_fcurves(cube)
        if fc.data_path == "rotation_euler"
    )
    for fc in BlenderShotStore.iter_action_fcurves(cube):
        for k in fc.keyframe_points:
            k.select_control_point = False
    for k in fx.keyframe_points:
        k.select_control_point = k.co.x in (10, 20)
    for k in frz.keyframe_points:
        k.select_control_point = k.co.x == 25
    clip = KeyStash.active().stash(selected_keys=True)
    by_path = {(r["data_path"], r["array_index"]): r["times"] for r in clip.curves}
    check(
        "stash of the key selection is per curve",
        frames(fx) == [1, 30, 40]
        and frames(frz) == [5, 15]
        and by_path == {("location", 0): [10.0, 20.0], ("rotation_euler", 2): [25.0]},
        f"{frames(fx)} {frames(frz)} {by_path}",
    )

    # ---- missing object keeps the record; target another ----
    reset()
    cube = keyed_cube()
    clip = KeyStash.active().stash(objects=[cube], time_range=(10, 30))
    bpy.data.objects.remove(cube, do_unlink=True)
    store = KeyStash.active()
    n0 = store.retrieve(clip.clip_id)
    kept = len(store.clips)
    other = keyed_cube("Other")
    other_fc = fc_x(other)
    for k in reversed(list(other_fc.keyframe_points)):
        other_fc.keyframe_points.remove(k)
    n1 = store.retrieve(clip.clip_id, target="Other")
    check(
        "retrieve keeps an orphaned record, then lands it on a target",
        n0 == 0
        and kept == 1
        and n1 == 3
        and frames(fc_x(other)) == [10, 20, 30]
        and store.is_empty(),
        f"n0={n0} kept={kept} n1={n1} {frames(fc_x(other))}",
    )

    # ---- persistence: channel + save/reopen ----
    reset()
    cube = keyed_cube()
    clip = KeyStash.active().stash(objects=[cube], time_range=(10, 30))
    scene = bpy.context.scene
    check(
        "channel is isolated from the shot store",
        scene.get("key_stash") is not None
        and scene.get("shot_store") is None
        and BlenderScenePersistence().store_cls is BlenderShotStore,
    )
    path = os.path.join(TEMP, "key_stash_roundtrip.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    reset_store()
    bpy.ops.wm.open_mainfile(filepath=path)
    store = KeyStash.active()
    ids = [c.clip_id for c in store.clips]
    n = store.retrieve(clip.clip_id) if ids else 0
    check(
        "clip survives save/reopen and retrieves",
        ids == [clip.clip_id]
        and n == 3
        and frames(fc_x(bpy.data.objects["Cube"])) == [1, 10, 20, 30, 40],
        f"ids={ids} n={n}",
    )
    for p in (path, path + "1"):
        if os.path.exists(p):
            os.remove(p)

    # ---- FBX export carries neither the stash nor its keys ----
    reset()
    cube = keyed_cube()
    KeyStash.active().stash(objects=[cube], time_range=(10, 30))
    fbx = os.path.join(TEMP, "key_stash.fbx")
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.export_scene.fbx(
        filepath=fbx,
        use_selection=True,
        bake_anim=True,
        bake_anim_use_all_actions=False,
        bake_anim_use_nla_strips=False,
    )
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.fbx(filepath=fbx)
    names = [a.name for a in bpy.data.actions]
    check(
        "FBX round-trip holds one action and none named after the stash",
        len(names) == 1 and not any("keyStash" in n for n in names),
        f"{names}",
    )
    if os.path.exists(fbx):
        os.remove(fbx)

    # ---- preview: in context ----
    reset()
    cube = keyed_cube()
    scene = bpy.context.scene
    scene.use_preview_range = False
    clip = KeyStash.active().stash(objects=[cube], time_range=(10, 30))
    after_cut = {f: ev(cube, f) for f in (5, 20, 35)}
    store = KeyStash.active()
    store.preview(clip.clip_id, in_context=True)
    ad = cube.animation_data
    check(
        "in-context preview plays the clip inside its range and the base outside",
        ev(cube, 20) == 10.0
        and ev(cube, 5) == after_cut[5]
        and ev(cube, 35) == after_cut[35]
        and store.is_previewing(clip.clip_id)
        and ad.action is None
        and len(ad.nla_tracks) == 2
        and scene.use_preview_range
        and (scene.frame_preview_start, scene.frame_preview_end) == (10, 30),
        f"{ev(cube, 5)} {ev(cube, 20)} {ev(cube, 35)} tracks={len(ad.nla_tracks)}",
    )
    ended = store.end_preview()
    check(
        "ending the preview leaves no trace",
        ended
        and len(ad.nla_tracks) == 0
        and ad.action is not None
        and {f: ev(cube, f) for f in (5, 20, 35)} == after_cut
        and not scene.use_preview_range
        and store.active_preview is None
        and not store.end_preview(),
        f"tracks={len(ad.nla_tracks)} action={ad.action}",
    )

    # ---- preview: isolated ----
    store.preview(clip.clip_id, in_context=False)
    check(
        "isolated preview holds the clip's end poses outside its range",
        ev(cube, 5) == 5.0 and ev(cube, 20) == 10.0 and ev(cube, 35) == 15.0,
        f"{ev(cube, 5)} {ev(cube, 20)} {ev(cube, 35)}",
    )
    store.end_preview()

    # ---- retrieve ends an active preview ----
    store.preview(clip.clip_id)
    store.retrieve(clip.clip_id)
    check(
        "retrieve ends an active preview and restores the keys",
        len(cube.animation_data.nla_tracks) == 0
        and store.active_preview is None
        and frames(fc_x(cube)) == [1, 10, 20, 30, 40],
        f"{frames(fc_x(cube))}",
    )

    # ---- reconcile ends a preview left in the record ----
    reset()
    cube = keyed_cube()
    clip = KeyStash.active().stash(objects=[cube], time_range=(10, 30))
    store = KeyStash.active()
    store.preview(clip.clip_id)
    data = store.to_dict()
    reset_store()
    backend = BlenderScenePersistence(attr_name=KeyStash.ATTR_NAME, store_cls=KeyStash)
    KeyStash.set_persistence(backend)
    backend.save(data)
    reopened = KeyStash.active()
    check(
        "reconcile ends a preview the saved record says is active",
        reopened.active_preview is None
        and len(cube.animation_data.nla_tracks) == 0
        and len(reopened.clips) == 1,
        f"{reopened.active_preview} tracks={len(cube.animation_data.nla_tracks)}",
    )

    # ---- frame-rate change leaves Blender frames alone ----
    reopened.rescale_to_fps(reopened.scene_fps * 2)
    check(
        "rescale_to_fps records the rate but does not move Blender keys",
        reopened.clips[0].times == [10.0, 20.0, 30.0],
        f"{reopened.clips[0].times}",
    )
    reset_store()

except Exception:
    traceback.print_exc()
    lines.append("FAIL unhandled exception")

print("\n".join(lines))
ok = all(line.startswith("OK") for line in lines) and lines
print(
    f"===RESULT: {'PASS' if ok else 'FAIL'}=== ({sum(1 for line in lines if line.startswith('OK'))}/{len(lines)})"
)
