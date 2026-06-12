# !/usr/bin/python
# coding=utf-8
"""Material utilities — get/assign/create/select-by-material (mirror of mayatk's ``MatUtils``
public names where the concepts align: Blender materials live on ``obj.material_slots`` /
``bpy.data.materials``; there are no shading engines).

Datablock-level (no viewport) → **headless-testable**. ``import bpy`` is deferred into the
call bodies (no import side effects).
"""
import random

import pythontk as ptk


def get_mats(objects):
    """Unique materials assigned to the given object(s), in slot order."""
    seen = []
    for o in ptk.make_iterable(objects):
        for slot in getattr(o, "material_slots", []):
            if slot.material is not None and slot.material not in seen:
                seen.append(slot.material)
    return seen


def create_mat(mat_type="standard", name=""):
    """Create a new material (mirror of ``mtk.MatUtils.create_mat``).

    ``mat_type='random'`` seeds a random base/viewport color (name defaults to the hex value).
    """
    import bpy

    if mat_type == "random":
        rgb = [random.uniform(0.1, 1.0) for _ in range(3)]
        name = name or "mat_" + "".join(f"{int(c * 255):02x}" for c in rgb)
        mat = bpy.data.materials.new(name)
        mat.use_nodes = True
        mat.diffuse_color = (*rgb, 1.0)
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        if bsdf:
            bsdf.inputs["Base Color"].default_value = (*rgb, 1.0)
        return mat
    mat = bpy.data.materials.new(name or "material")
    mat.use_nodes = True
    return mat


def assign_mat(objects, material):
    """Assign ``material`` to the given object(s) — whole-object assignment (all slots).

    Mirrors Maya's object-level assign; per-face assignment is an edit-mode workflow and is
    intentionally out of scope here.
    """
    for o in ptk.make_iterable(objects):
        data = getattr(o, "data", None)
        if data is None or not hasattr(data, "materials"):
            continue
        data.materials.clear()
        data.materials.append(material)


def find_by_mat_id(material, objects=None):
    """Objects using ``material`` (mirror of ``mtk.find_by_mat_id`` at the object level).

    ``objects=None`` scans the whole scene; otherwise restricts to the given objects.
    """
    import bpy

    pool = ptk.make_iterable(objects) if objects is not None else bpy.data.objects
    return [
        o
        for o in pool
        if any(s.material is material for s in getattr(o, "material_slots", []))
    ]


def select_by_material(material, add=False):
    """Select every scene object using ``material`` (optionally adding to the selection)."""
    import bpy

    if not add:
        for o in bpy.data.objects:
            o.select_set(False)
    users = find_by_mat_id(material)
    for o in users:
        o.select_set(True)
    if users:
        bpy.context.view_layer.objects.active = users[0]
    return users


def reload_textures():
    """Reload every image datablock from disk (mirror of ``mtk.MatUtils.reload_textures``)."""
    import bpy

    for img in bpy.data.images:
        if img.source == "FILE":
            try:
                img.reload()
            except RuntimeError:
                pass


class MatUtils:
    """Namespace mirror of mayatk's ``MatUtils`` (helpers also exposed module-level)."""

    get_mats = staticmethod(get_mats)
    create_mat = staticmethod(create_mat)
    assign_mat = staticmethod(assign_mat)
    find_by_mat_id = staticmethod(find_by_mat_id)
    select_by_material = staticmethod(select_by_material)
    reload_textures = staticmethod(reload_textures)
