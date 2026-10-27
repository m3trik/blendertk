"""Export baselines -- Blender port of mayatk's ``test_export_baselines.py``.

Each block builds one small scene, exports it as FBX + GLB through
``SceneExporter.perform_export`` -- the path the panel's Export button runs --
and compares both files with the fingerprints saved under
``test/baselines/export/`` (:class:`pythontk.ExportFingerprint`). A code move
must change no output, so any difference fails with its path named.

A change that alters the output on purpose rewrites the baselines with
``UPDATE_EXPORT_BASELINES=1``; the fingerprint diff in the commit is the review.

Run: blender --background --factory-startup --python blendertk/test/test_export_baselines.py
"""

import math
import os
import shutil
import sys
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for p in (REPO, os.path.join(MONO, "pythontk"), os.path.join(MONO, "uitk")):
    if p not in sys.path:
        sys.path.insert(0, p)

import pythontk as ptk  # noqa: E402 -- after the sibling paths above

BASELINES = os.path.join(HERE, "baselines", "export")
UPDATE = os.environ.get("UPDATE_EXPORT_BASELINES") == "1"

# Isolate the preset store and the per-user cache, as test_scene_exporter does.
_PRESETS_ROOT = tempfile.mkdtemp(prefix="btk_baselines_presets_")
os.environ["UITK_PRESETS_ROOT"] = _PRESETS_ROOT
_CACHE_ROOT = None
if not os.environ.get("PYTHONTK_CACHE_ROOT"):
    _CACHE_ROOT = tempfile.mkdtemp(prefix="btk_baselines_cache_")
    os.environ["PYTHONTK_CACHE_ROOT"] = _CACHE_ROOT

lines = []


def check(name, cond, detail=""):
    lines.append(
        f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + str(detail)) if detail else ''}"
    )


try:
    import bpy
    from blendertk.anim_utils.shots._shots import BlenderShotStore
    from blendertk.env_utils.scene_exporter._scene_exporter import SceneExporter

    tmp = tempfile.mkdtemp(prefix="btk_export_baselines_")
    BlenderShotStore._prefs_dir_override = tempfile.mkdtemp(prefix="btk_baselines_prefs_")

    def reset_scene():
        bpy.ops.object.select_all(action="DESELECT")
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)
        for collection in (bpy.data.actions, bpy.data.materials, bpy.data.armatures):
            for block in list(collection):
                collection.remove(block)
        BlenderShotStore.clear_active()
        scene = bpy.context.scene
        scene.frame_start, scene.frame_end = 1, 40

    def export(name, objects, **tasks):
        written = SceneExporter().perform_export(
            export_dir=tmp,
            objects=objects,
            output_name=name,
            export_visible=True,
            tasks={"output_format": "fbx_glb", **tasks},
        )
        check(f"the {name} export writes its deliverable", written is True, written)
        return name

    def matches_baseline(name):
        for extension in ("fbx", "glb"):
            path = os.path.join(tmp, f"{name}.{extension}")
            if not os.path.isfile(path):
                check(f"{name}.{extension} is written", False, path)
                continue
            baseline = os.path.join(BASELINES, f"{name}.{extension}.json")
            changes = ptk.ExportFingerprint.against(path, baseline, update=UPDATE)
            check(f"{name}.{extension} matches its baseline", not changes, changes[:8])

    def material(name, color, metallic=0.0):
        mat = bpy.data.materials.new(name)
        mat.use_nodes = True
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        bsdf.inputs["Base Color"].default_value = (*color, 1.0)
        bsdf.inputs["Metallic"].default_value = metallic
        return mat

    # ---- a prop hierarchy with two materials ----------------------------------------------
    reset_scene()
    group = bpy.data.objects.new("props_grp", None)
    bpy.context.scene.collection.objects.link(group)
    bpy.ops.mesh.primitive_cube_add(size=1)
    crate = bpy.context.active_object
    crate.name = "crate"
    crate.location = (1.0, 2.0, 0.5)
    crate.rotation_euler = (0.0, 0.0, math.radians(45))
    crate.scale = (2.0, 1.0, 1.0)
    crate.data.materials.append(material("crate_mat", (0.8, 0.2, 0.1)))
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.5, segments=8, ring_count=6)
    ball = bpy.context.active_object
    ball.name = "ball"
    ball.location = (-1.0, 0.0, 1.0)
    ball.data.materials.append(material("ball_mat", (0.1, 0.3, 0.9), metallic=0.5))
    for child in (crate, ball):
        child.parent = group
    matches_baseline(export("props", [group, crate, ball]))

    # ---- keyed transforms split into shots ------------------------------------------------
    reset_scene()
    bpy.ops.mesh.primitive_cube_add()
    mover = bpy.context.active_object
    mover.name = "mover"
    for frame, x in ((1, 0.0), (40, 10.0)):
        mover.location.x = x
        mover.keyframe_insert("location", index=0, frame=frame)
    for frame, z in ((21, 0.0), (40, math.radians(90))):
        mover.rotation_euler.z = z
        mover.keyframe_insert("rotation_euler", index=2, frame=frame)
    store = BlenderShotStore.active()
    store.define_shot("ShotA", 1, 20, objects=["mover"])
    store.define_shot("ShotB", 21, 40, objects=["mover"])
    store.publish_export_view()
    matches_baseline(
        export("shots", [mover], export_data_node=True, apply_declared_takes="both")
    )

    # ---- a skinned joint chain ------------------------------------------------------------
    reset_scene()
    armature = bpy.data.armatures.new("rig")
    rig = bpy.data.objects.new("rig_grp", armature)
    bpy.context.scene.collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    parent = None
    for i in range(3):
        bone = armature.edit_bones.new(f"bone_{i}")
        bone.head, bone.tail = (0.0, 0.0, i * 2.0), (0.0, 0.0, i * 2.0 + 2.0)
        bone.parent = parent
        bone.use_connect = parent is not None
        parent = bone
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.mesh.primitive_cylinder_add(radius=0.4, depth=4, vertices=8, location=(0, 0, 2))
    limb = bpy.context.active_object
    limb.name = "limb"
    for i in range(3):
        group_ = limb.vertex_groups.new(name=f"bone_{i}")
        group_.add(
            [v.index for v in limb.data.vertices if i * 2.0 <= v.co.z + 2.0 <= i * 2.0 + 2.0],
            1.0,
            "REPLACE",
        )
    limb.parent = rig
    limb.modifiers.new("Armature", "ARMATURE").object = rig
    pose_bone = rig.pose.bones["bone_1"]
    pose_bone.rotation_mode = "XYZ"
    for frame, angle in ((1, 0.0), (20, math.radians(60))):
        pose_bone.rotation_euler.y = angle
        pose_bone.keyframe_insert("rotation_euler", frame=frame)
    matches_baseline(export("skinned", [rig, limb]))

    shutil.rmtree(tmp, ignore_errors=True)
    shutil.rmtree(BlenderShotStore._prefs_dir_override, ignore_errors=True)

except Exception as e:
    lines.append(f"FAIL setup: {e!r}")
    lines.append(traceback.format_exc())
finally:
    shutil.rmtree(_PRESETS_ROOT, ignore_errors=True)
    if _CACHE_ROOT:
        shutil.rmtree(_CACHE_ROOT, ignore_errors=True)

ok = all(line.startswith("OK") for line in lines)
for line in lines:
    print(line)
print(f"===RESULT: {'PASS' if ok else 'FAIL'}===")
