"""blendertk Color ID engine headless test (material / object-color / vertex channels).
Run: blender --background --factory-startup --python blendertk/test/test_color_id.py
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
    from blendertk.display_utils.color_id import ColorId as CM

    def reset_scene():
        if (bpy.context.view_layer.objects.active
                and bpy.context.view_layer.objects.active.mode != "OBJECT"):
            bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.select_all(action="DESELECT")
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)

    reset_scene()
    bpy.ops.mesh.primitive_cube_add(); a = bpy.context.active_object; a.name = "A"
    bpy.ops.mesh.primitive_cube_add(location=(3, 0, 0)); b = bpy.context.active_object; b.name = "B"
    RED = (0.8, 0.1, 0.1)

    # apply RED to A across all three channels
    CM.apply_color([a], RED, apply_to_object=True, apply_to_material=True, apply_to_vertex=True)
    check("object color set", abs(a.color[0] - 0.8) < 1e-3, str(tuple(a.color)[:3]))
    check("ID material assigned", bool(a.active_material) and a.active_material.name.startswith("ID_"),
          a.active_material.name if a.active_material else None)
    check("vertex color attribute created", len(a.data.color_attributes) > 0)

    # read-back per channel
    check("get_object_color", CM.get_object_color(a) is not None and abs(CM.get_object_color(a)[0] - 0.8) < 1e-3)
    check("get_material_color (Principled base)", CM.get_material_color(a) is not None and abs(CM.get_material_color(a)[0] - 0.8) < 1e-3)
    avg = CM.get_average_vertex_color(a)
    check("get_average_vertex_color", avg is not None and abs(avg[0] - 0.8) < 0.02, str(avg))

    # select-by-color (object channel) finds only A
    found = CM.get_objects_by_color(RED, check_object=True)
    check("select-by-color finds A only", [o.name for o in found] == ["A"], str([o.name for o in found]))

    # other color doesn't match
    none_found = CM.get_objects_by_color((0.0, 0.0, 1.0), check_object=True)
    check("select-by-color blue finds nothing", none_found == [], str([o.name for o in none_found]))

    # reset clears all three channels (and leaves non-ID materials alone)
    b.data.materials.clear()
    keep = bpy.data.materials.new("KeepMe"); b.data.materials.append(keep)
    CM.reset_colors([a, b])
    check("reset clears object color", abs(a.color[0] - 1.0) < 1e-3, str(tuple(a.color)[:3]))
    check("reset removes the ID material", not (a.active_material and a.active_material.name.startswith("ID_")))
    check("reset removes vertex colors", len(a.data.color_attributes) == 0)
    check("reset keeps non-ID materials", any(m and m.name == "KeepMe" for m in b.data.materials))

    # random color path (color=None) doesn't raise and sets something
    CM.apply_color([b], None, apply_to_object=True)
    check("random color applies", CM.get_object_color(b) is not None)

except Exception as e:
    traceback.print_exc()
    check("color manager raised", False, repr(e))

passed = sum(1 for line in lines if line.startswith("OK"))
for line in lines:
    print(line)
result = "PASS" if all(line.startswith("OK") for line in lines) else "FAIL"
print(f"===RESULT: {result}=== ({passed}/{len(lines)})")
