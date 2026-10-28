"""blendertk Components (mirror of mayatk's) headless test: the normals operations a panel
shared by both hosts calls, each with mayatk's name and signature; ``None`` takes the selection.
Run: blender --background --factory-startup --python blendertk/test/test_components.py
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


try:
    import bpy
    import blendertk as btk

    Components = btk.Components

    def reset():
        bpy.ops.object.select_all(action="DESELECT")
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)

    def cube(x=0.0):
        bpy.ops.mesh.primitive_cube_add(size=2.0, location=(x, 0.0, 0.0))
        return bpy.context.active_object

    def smooth(o):
        return all(p.use_smooth for p in o.data.polygons)

    # ---- soften / harden: smooth vs flat shading
    reset()
    o = cube()
    Components.set_edge_softness([o], soft=True)
    check("set_edge_softness soft smooths every face", smooth(o))
    Components.set_edge_softness([o], soft=False)
    check("set_edge_softness hard flattens every face", not any(p.use_smooth for p in o.data.polygons))

    # ---- None takes the selection; nothing selected does nothing
    reset()
    o = cube()
    o.select_set(True)
    Components.set_edge_softness(None, soft=True)
    check("None takes the selection", smooth(o))
    o.select_set(False)
    check("nothing selected returns []", Components.set_edge_softness(None) == [])

    # ---- a keyword target is honored: the given objects, never the selection
    reset()
    o, other = cube(), cube(x=4.0)
    other.select_set(True)
    Components.set_edge_softness(objects=[o], soft=True)
    check(
        "objects= by keyword acts on the given objects",
        smooth(o) and not any(p.use_smooth for p in other.data.polygons),
    )
    Components.set_edge_softness(objects=None, soft=True)
    check("objects=None by keyword takes the selection", smooth(other))

    # ---- set_to_face_normals: flat
    reset()
    o = cube()
    Components.set_edge_softness([o], soft=True)
    Components.set_to_face_normals([o])
    check("set_to_face_normals flattens", not any(p.use_smooth for p in o.data.polygons))

    # ---- set_edge_hardness: a cube's 90 degree edges are hard over a 45 threshold
    reset()
    o = cube()
    out = Components.set_edge_hardness([o], 45, upper_hardness=0, lower_hardness=180)
    check(
        "set_edge_hardness marks the upper bucket hard",
        out == [] and all(e.use_edge_sharp for e in o.data.edges),
        f"out={out}",
    )

    # ---- locked (custom split) normals: skipped and named unless unlocked
    reset()
    o = cube()
    btk.EditUtils.add_custom_split_normals([o])
    out = Components.set_edge_hardness([o], 45, upper_hardness=0)
    check("locked normals are skipped and named", out == [o.name], f"out={out}")
    out = Components.set_edge_hardness([o], 45, upper_hardness=0, unlock_normals=True)
    check(
        "unlock_normals clears them and runs",
        out == [] and not o.data.has_custom_normals,
        f"out={out}",
    )

    # ---- toggle_normal_lock: True, then False, then None for nothing
    reset()
    o = cube()
    check("toggle locks", Components.toggle_normal_lock([o]) is True and o.data.has_custom_normals)
    check("toggle unlocks", Components.toggle_normal_lock([o]) is False and not o.data.has_custom_normals)
    check("toggle on no mesh is None", Components.toggle_normal_lock([]) is None)

    # ---- reverse_normals mode 0 flips every face
    reset()
    o = cube()
    before = [p.normal.copy() for p in o.data.polygons]
    Components.reverse_normals([o], mode=0)
    after = [p.normal for p in o.data.polygons]
    check("reverse_normals flips", all(a.dot(b) < -0.99 for a, b in zip(after, before)))

    # ---- average_normals smooths
    reset()
    o = cube()
    Components.average_normals([o])
    check("average_normals smooths", smooth(o))

    # ---- transfer_normals: first mesh is the source; fewer than two raises
    reset()
    src, tgt = cube(), cube(x=4.0)
    btk.EditUtils.add_custom_split_normals([src])
    Components.transfer_normals([src, tgt], space="world")
    check("transfer_normals gives the target custom normals", tgt.data.has_custom_normals)
    # None: the selected active mesh is the source; nothing selected is []
    bpy.ops.object.select_all(action="DESELECT")
    check("transfer_normals(None) with nothing selected is []", Components.transfer_normals(None) == [])
    tgt.select_set(True)
    bpy.context.view_layer.objects.active = src  # active, NOT selected
    try:
        Components.transfer_normals(None)
        check("an unselected active mesh is never the source", False)
    except ValueError:
        check("an unselected active mesh is never the source", True)
    try:
        Components.transfer_normals([src])
        check("transfer_normals of one mesh raises", False)
    except ValueError as error:
        # The panel shows it as it is: a missing target, not a non-polygonal mesh.
        check(
            "transfer_normals of one mesh asks for a source and a target",
            str(error) == "Transfer needs a source mesh and one or more target meshes.",
        )
    try:
        Components.transfer_normals([src, tgt], space="uv")
        check("transfer_normals of an unknown space raises", False)
    except ValueError:
        check("transfer_normals of an unknown space raises", True)

    # ---- transfer_mesh_data: Data Transfer from the first mesh (None: the active one)
    reset()
    src, tgt = cube(), cube(x=4.0)
    probe = src.vertex_groups.new(name="TransferProbe")
    probe.add([v.index for v in src.data.vertices], 1.0, "REPLACE")
    btk.CoreUtils.select([src, tgt])  # replace: the first one (src) is active
    targets = Components.transfer_mesh_data(
        None,
        "VGROUP_WEIGHTS",
        vert_mapping="NEAREST",
        layers_select_src="ALL",
        layers_select_dst="NAME",
    )
    check(
        "transfer_mesh_data(None): the active mesh is the source, the rest the targets",
        targets == [tgt] and "TransferProbe" in tgt.vertex_groups,
        f"{targets}",
    )
    btk.CoreUtils.select([])
    check(
        "transfer_mesh_data(None) with nothing selected is []",
        Components.transfer_mesh_data(None, "VGROUP_WEIGHTS") == [],
    )
    try:
        Components.transfer_mesh_data([src], "UV")
        check("transfer_mesh_data of one mesh raises", False)
    except ValueError as error:
        check(
            "transfer_mesh_data of one mesh raises",
            str(error) == "Transfer needs a source mesh and one or more target meshes.",
        )
    tgt.select_set(True)
    bpy.context.view_layer.objects.active = src  # active, NOT selected
    try:
        Components.transfer_mesh_data(None, "UV")
        check("transfer_mesh_data: an unselected active mesh is never the source", False)
    except ValueError as error:
        check(
            "transfer_mesh_data: an unselected active mesh is never the source",
            str(error) == "Select target mesh(es) with the source mesh active.",
        )

except Exception:
    traceback.print_exc()
    lines.append("FAIL unhandled exception")

print("\n".join(lines))
ok = all(l.startswith("OK") for l in lines) and lines
print(
    f"===RESULT: {'PASS' if ok else 'FAIL'}=== ({sum(1 for l in lines if l.startswith('OK'))}/{len(lines)})"
)
