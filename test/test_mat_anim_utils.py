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
