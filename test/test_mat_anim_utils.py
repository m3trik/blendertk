"""blendertk.mat_utils + anim_utils + core recent-files headless test.
Run: blender --background --factory-startup --python blendertk/test/test_mat_anim_utils.py
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

try:
    import bpy
    import blendertk as btk

    def reset():
        bpy.ops.object.select_all(action="DESELECT")
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)
        for m in list(bpy.data.materials):
            bpy.data.materials.remove(m)

    # ---- mat_utils ----------------------------------------------------------
    reset()
    bpy.ops.mesh.primitive_cube_add(); a = bpy.context.active_object
    bpy.ops.mesh.primitive_cube_add(location=(3, 0, 0)); b = bpy.context.active_object

    m1 = btk.create_mat("standard", name="M1")
    check("create_mat standard", m1.name == "M1" and m1.use_nodes)
    rnd = btk.create_mat("random")
    check("create_mat random has color name", rnd.name.startswith("mat_"))

    btk.assign_mat([a, b], m1)
    check("assign_mat -> both objects use M1", btk.get_mats([a, b]) == [m1])

    btk.assign_mat(b, rnd)
    users = btk.find_by_mat_id(m1)
    check("find_by_mat_id scoped to users", users == [a], f"{[o.name for o in users]}")

    sel = btk.select_by_material(rnd)
    check("select_by_material selects + activates", sel == [b] and b.select_set is not None
          and bpy.context.view_layer.objects.active is b)

    # ---- anim_utils ---------------------------------------------------------
    def key_obj(o, frames=(1, 11)):
        for f in frames:
            o.location.x = float(f)
            o.keyframe_insert(data_path="location", index=0, frame=f)

    def key_times(o):
        return sorted(k.co.x for fc in btk.get_fcurves(o) for k in fc.keyframe_points)

    reset()
    bpy.ops.mesh.primitive_cube_add(); a = bpy.context.active_object
    bpy.ops.mesh.primitive_cube_add(location=(3, 0, 0)); b = bpy.context.active_object
    key_obj(a, (1, 11)); key_obj(b, (1, 21))

    btk.shift_keys(a, 5)
    check("shift_keys +5", key_times(a) == [6.0, 16.0], f"{key_times(a)}")

    btk.invert_keys(a)  # mirror about center (11) -> same frames swapped = same set
    check("invert_keys keeps range", key_times(a) == [6.0, 16.0])

    btk.snap_keys(a)
    check("snap_keys whole frames", all(t == int(t) for t in key_times(a)))

    btk.scale_keys(a, 2.0)  # about first key (6): 6,16 -> 6,26
    check("scale_keys x2 about first", key_times(a) == [6.0, 26.0], f"{key_times(a)}")

    btk.stagger_keys([a, b], spacing=5)  # a: 6-26; b starts at 31
    check("stagger_keys sequential", key_times(b)[0] == 31.0, f"{key_times(b)}")

    btk.set_stepped(a)
    interps = {k.interpolation for fc in btk.get_fcurves(a) for k in fc.keyframe_points}
    check("set_stepped CONSTANT", interps == {"CONSTANT"})

    rng = btk.fit_playback_range([a, b])
    sc = bpy.context.scene
    check("fit_playback_range", rng == (sc.frame_start, sc.frame_end) and sc.frame_start == 6,
          f"{rng}")

    # move_keys_to_frame: a=6-26, b=31-51 after stagger. Global (retain_spacing): earliest
    # key (6) lands on the target, b keeps its +25 offset; per-action: both start at target.
    moved = btk.move_keys_to_frame([a, b], frame=100, retain_spacing=True)
    check("move_keys_to_frame retain_spacing keeps offsets",
          moved == 2 and key_times(a)[0] == 100.0 and key_times(b)[0] == 125.0,
          f"a={key_times(a)} b={key_times(b)}")
    btk.move_keys_to_frame([a, b], frame=50, retain_spacing=False)
    check("move_keys_to_frame per-action aligns first keys",
          key_times(a)[0] == 50.0 and key_times(b)[0] == 50.0,
          f"a={key_times(a)} b={key_times(b)}")
    check("move_keys_to_frame keyless -> 0", btk.move_keys_to_frame([], frame=1) == 0)
    sc0 = sc.frame_current
    sc.frame_set(60)
    btk.move_keys_to_frame(a)  # frame defaults to the current frame
    check("move_keys_to_frame defaults to current frame", key_times(a)[0] == 60.0,
          f"{key_times(a)}")
    sc.frame_set(sc0)

    # adjust_key_spacing: a = 60,80 -> shift keys >= 70 by +5 (only the 80 moves)
    moved = btk.adjust_key_spacing(a, spacing=5, frame=70)
    check("adjust_key_spacing shifts only keys at/after the frame",
          moved == 1 and key_times(a) == [60.0, 85.0], f"{key_times(a)}")
    check("adjust_key_spacing no keys after frame -> 0",
          btk.adjust_key_spacing(a, spacing=5, frame=1000) == 0)

    # align_selected_keyframes: only SELECTED keys move
    for k in [k for fc in btk.get_fcurves(a) for k in fc.keyframe_points]:
        k.select_control_point = False
    last = max(
        (k for fc in btk.get_fcurves(a) for k in fc.keyframe_points),
        key=lambda k: k.co.x,
    )
    last.select_control_point = True
    moved = btk.align_selected_keyframes(a, target_frame=70)
    check("align_selected_keyframes moves only the selected key",
          moved == 1 and key_times(a) == [60.0, 70.0], f"{key_times(a)}")
    for k in [k for fc in btk.get_fcurves(a) for k in fc.keyframe_points]:
        k.select_control_point = False
    check("align_selected_keyframes none selected -> 0",
          btk.align_selected_keyframes(a) == 0)

    # intermediate keys: a = 60,70 -> sampled key on every frame between (61..69)
    added = btk.add_intermediate_keys(a)
    check("add_intermediate_keys fills the span", added == 9 and len(key_times(a)) == 11,
          f"added={added} n={len(key_times(a))}")
    removed = btk.remove_intermediate_keys(a)
    check("remove_intermediate_keys keeps endpoints",
          removed == 9 and key_times(a) == [60.0, 70.0], f"{key_times(a)}")

    # select_keys: range selects in-range, deselects the rest; None = all
    n = btk.select_keys(a, time=(65, 75))
    sel = [k.co.x for fc in btk.get_fcurves(a) for k in fc.keyframe_points
           if k.select_control_point]
    check("select_keys range", n == 1 and sel == [70.0], f"n={n} sel={sel}")
    check("select_keys all", btk.select_keys(a) == 2)

    # set_visibility_keys: keys hide_viewport/hide_render at the frame
    keyed = btk.set_visibility_keys(b, visible=False, frame=42)
    vis_curves = [
        fc for fc in btk.get_fcurves(b)
        if fc.data_path in ("hide_viewport", "hide_render")
    ]
    check("set_visibility_keys keys both hide props",
          keyed == [b] and len(vis_curves) == 2
          and all(any(k.co.x == 42.0 for k in fc.keyframe_points) for fc in vis_curves)
          and b.hide_render,
          f"curves={[fc.data_path for fc in vis_curves]}")

    action = btk.copy_keys(a)
    btk.paste_keys(b, action)
    check("copy/paste keys independent copy",
          key_times(b) == key_times(a)
          and b.animation_data.action is not a.animation_data.action)

    cleared = btk.delete_keys([a, b])
    check("delete_keys clears both", len(cleared) == 2 and a.animation_data is None)

    # ---- core recent files --------------------------------------------------
    files = btk.get_recent_files()
    check("get_recent_files returns list", isinstance(files, list))
    autosaves = btk.get_recent_autosave(filter_time=24)
    check("get_recent_autosave (path, stamp) pairs",
          isinstance(autosaves, list)
          and all(len(t) == 2 for t in autosaves))

except Exception as e:
    lines.append(f"FAIL setup: {e!r}")
    lines.append(traceback.format_exc())

ok = all(l.startswith("OK") for l in lines)
print("\n===MAT-ANIM-UTILS===")
print("\n".join(lines))
print(f"===RESULT: {'PASS' if ok else 'FAIL'}===")
