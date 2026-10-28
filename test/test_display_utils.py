"""blendertk.display_utils headless test -- the Display menu's toggles (the
twins of mayatk's ``DisplayUtils`` toggles that tentacle's composed
``slots/display.py`` calls through ``self.tk``).
Run: blender --background --factory-startup --python blendertk/test/test_display_utils.py
"""

import sys
import os
import traceback
from types import SimpleNamespace as NS

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)  # blendertk/
MONO = os.path.dirname(REPO)  # _scripts/
for p in (REPO, os.path.join(MONO, "pythontk")):
    if p not in sys.path:
        sys.path.insert(0, p)

lines = []


def check(name, cond, detail=""):
    lines.append(
        f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + str(detail)) if detail else ''}"
    )


try:
    import bpy
    import blendertk as btk

    D = btk.DisplayUtils

    def reset():
        bpy.ops.object.select_all(action="DESELECT")
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)

    def cube(name, loc=(0, 0, 0)):
        bpy.ops.mesh.primitive_cube_add(location=loc)
        o = bpy.context.active_object
        o.name = name
        return o

    reset()
    a, b, c = cube("A"), cube("B", (3, 0, 0)), cube("C", (6, 0, 0))
    bpy.ops.object.empty_add(location=(0, 4, 0))
    empty = bpy.context.active_object
    btk.CoreUtils.select([])

    # nothing selected: the selection ops answer []
    check(
        "nothing selected -> []",
        all(op() == [] for op in (D.hide_objects, D.show_objects, D.toggle_xray, D.toggle_wireframe)),
    )

    # hide / show
    btk.CoreUtils.select([a])
    hidden = D.hide_objects()
    check("hide_objects hides the selection", hidden == [a] and a.hide_get(), str(hidden))
    check("show_objects shows what it is given", D.show_objects([a]) == [a] and not a.hide_get())

    # set_visibility('mesh'): every mesh, its parents and the collections
    b.hide_set(True)
    a.parent = empty
    empty.hide_set(True)
    D.set_visibility("mesh", True)
    check(
        "set_visibility('mesh') shows meshes and their ancestors",
        not b.hide_get() and not a.hide_get() and not empty.hide_get(),
    )
    D.set_visibility([c], False, include_ancestors=False, affect_layers=False)
    check("set_visibility(objects, False) hides just them", c.hide_get() and not b.hide_get())
    c.hide_set(False)
    a.parent = None

    # x-ray: uniform toggles
    btk.CoreUtils.select([a, b])
    a.show_in_front = True  # mixed: one press turns both on
    check("toggle_xray unifies a mixed selection", D.toggle_xray() == (True, 2)
          and a.show_in_front and b.show_in_front and not c.show_in_front)
    btk.CoreUtils.select([a])
    a.show_in_front = b.show_in_front = False
    check("toggle_xray_others leaves the selection out",
          D.toggle_xray_others() == (True, 2) and not a.show_in_front
          and b.show_in_front and c.show_in_front)
    btk.CoreUtils.select([])
    check("toggle_xray_others with nothing selected: every mesh",
          D.toggle_xray_others()[1] == 3)
    D.clear_xray()
    check("clear_xray clears every object",
          not any(o.show_in_front for o in (a, b, c, empty)))

    # wireframe
    btk.CoreUtils.select([a, b])  # a active: it decides
    b.display_type = "WIRE"
    check("toggle_wireframe: the active object decides", D.toggle_wireframe() is True
          and a.display_type == b.display_type == "WIRE")
    check("toggle_wireframe back to textured", D.toggle_wireframe() is False
          and a.display_type == "TEXTURED")
    D.set_wireframe([c])
    check("set_wireframe(False) textures again",
          D.set_wireframe([c], False) == [c] and c.display_type == "TEXTURED")
    btk.CoreUtils.select([a])
    check("toggle_wireframe_inactive wires the unselected meshes",
          D.toggle_wireframe_inactive() is True and b.display_type == "WIRE"
          and c.display_type == "WIRE" and a.display_type == "TEXTURED")
    check("...and textures them again", D.toggle_wireframe_inactive() is False
          and b.display_type == "TEXTURED")

    # template: templates the selection, releases with nothing selected
    btk.CoreUtils.select([a, b])
    state, objs = D.toggle_template()
    check("toggle_template templates the selection",
          state is True and set(objs) == {a, b} and a.hide_select and a.hide_render
          and a.display_type == "WIRE" and not a.select_get())
    state, objs = D.toggle_template()
    check("toggle_template, nothing selected, releases the templated",
          state is False and set(objs) == {a, b} and not a.hide_select
          and a.display_type == "TEXTURED")
    check("...and with none templated, []", D.toggle_template() == [])

    # viewport overlays, through stand-in spaces (headless has no 3D viewport)
    overlay = NS(show_face_normals=False, show_vertex_normals=False,
                 show_extra_indices=False, show_edge_sharp=False)
    shading = NS(color_type="MATERIAL")
    uv = NS(show_stretch=False, display_stretch_type="ANGLE")
    view = NS(spaces=NS(active=NS(overlay=overlay, shading=shading)))
    editor = NS(spaces=NS(active=NS(uv_editor=uv, image=None)))
    real_areas = vars(btk.CoreUtils)["get_areas"]  # the descriptors, put back as they were
    real_redraw = vars(btk.CoreUtils)["tag_redraw"]
    btk.CoreUtils.get_areas = staticmethod(
        lambda area_type=None: [view] if area_type == "VIEW_3D" else [editor]
    )
    btk.CoreUtils.tag_redraw = classmethod(lambda cls, area_type=None: 0)
    try:
        cycle = [D.cycle_normals_display() for _ in range(3)]
        check("cycle_normals_display: face, vertex, off", cycle == ["face", "vertex", "off"],
              str(cycle))
        check("cycle_component_ids toggles the index overlay",
              D.cycle_component_ids() == "on" and overlay.show_extra_indices
              and D.cycle_component_ids() == "off")
        check("toggle_soft_edge_display flips show_edge_sharp",
              D.toggle_soft_edge_display() is True and overlay.show_edge_sharp)
        check("toggle_material_override: Single, then Material",
              D.toggle_material_override() is True and shading.color_type == "SINGLE"
              and D.toggle_material_override() is False
              and shading.color_type == "MATERIAL")
        steps = [D.toggle_uv_distortion() for _ in range(3)]
        check("toggle_uv_distortion: angle, area, off", steps == ["angle", "area", "off"],
              str(steps))
        check("toggle_uv_checker shows the grid", D.toggle_uv_checker() is True
              and editor.spaces.active.image.name == D.UV_CHECKER_IMAGE)
        check("...and puts the editor's own image back",
              D.toggle_uv_checker() is False and editor.spaces.active.image is None)
    finally:
        btk.CoreUtils.get_areas, btk.CoreUtils.tag_redraw = real_areas, real_redraw
    btk.CoreUtils.get_areas = staticmethod(lambda area_type=None: [])
    try:
        check("no viewport -> None",
              D.cycle_normals_display() is None and D.toggle_material_override() is None
              and D.toggle_uv_checker() is None)
    finally:
        btk.CoreUtils.get_areas = real_areas

    # ---- smooth level (tentacle's composed SubdivisionSlots s000/s001) -----------------
    reset()
    sl = cube("SmoothLevel")
    btk.CoreUtils.select([sl])
    check("get_smooth_level without a Subsurf -> None", D.get_smooth_level() is None)
    check("set_smooth_level(None) adds the modifier on the selection",
          D.set_smooth_level(None, level=2, adaptive_level=3) == [sl])
    check("get_smooth_level reads viewport / render levels",
          D.get_smooth_level() == {"level": 2, "adaptive_level": 3},
          str(D.get_smooth_level()))
    btk.CoreUtils.select([])
    check("set_smooth_level with nothing selected -> []",
          D.set_smooth_level(None, level=1) == [])

    # ---- toggle_local_axes (mirror of mayatk's displayLocalAxis toggle) ----
    _arm_data = bpy.data.armatures.new("AxesArm")
    _arm = bpy.data.objects.new("AxesArm", _arm_data)
    bpy.context.scene.collection.objects.link(_arm)
    _mesh = bpy.data.objects.new("AxesMesh", bpy.data.meshes.new("AxesMesh"))
    bpy.context.scene.collection.objects.link(_mesh)
    btk.CoreUtils.select([])
    _state = btk.DisplayUtils.toggle_local_axes(None, joints=True)
    check("toggle_local_axes: the bone axes of every armature, nothing selected",
          _state in (True, False) and _arm_data.show_axes is _state, str(_state))
    btk.CoreUtils.select([_arm])
    _again = btk.DisplayUtils.toggle_local_axes(None, joints=True)
    check("toggle_local_axes: a selected armature's bones toggle back",
          _again is (not _state) and _arm_data.show_axes is _again)
    btk.CoreUtils.select([_mesh])
    check("toggle_local_axes: the selected object's own axis",
          btk.DisplayUtils.toggle_local_axes(None) is True and _mesh.show_axis is True)
    btk.CoreUtils.select([])
    check("toggle_local_axes(None), nothing selected -> None",
          btk.DisplayUtils.toggle_local_axes(None) is None)
    for _o in (_arm, _mesh):
        bpy.data.objects.remove(_o, do_unlink=True)

except Exception as e:
    lines.append(f"FAIL setup: {e!r}")
    lines.append(traceback.format_exc())

ok = all(ln.startswith("OK") for ln in lines)
print("\n===DISPLAY-UTILS===")
print("\n".join(lines))
print(f"===RESULT: {'PASS' if ok else 'FAIL'}===")
