# !/usr/bin/python
# coding=utf-8
"""UV utilities — UV-coordinate translation and UV-set cleanup (mirror of mayatk's ``UvUtils``
names where they apply). Operate on the mesh UV layer via bmesh / ``mesh.uv_layers`` → headless.

The unwrap / pack / project / seam operators are ``bpy.ops.uv.*`` and live in the slot (they run
in edit mode); this module holds the data-level UV helpers that need no UV editor.

``import bpy`` / ``bmesh`` are deferred into the call bodies (no import side effects). Shares the
mesh primitives with ``edit_utils`` (the canonical home for mesh-bmesh infra).
"""
from blendertk.core_utils._core_utils import _object_mode
from blendertk.edit_utils._edit_utils import _meshes, _bmesh_edit


def move_uvs(objects, du=0.0, dv=0.0):
    """Translate the UVs of the given mesh object(s) by ``(du, dv)`` — "move to UV space"
    (whole UV map). **Mode-aware** (NOT ``@_object_mode``): edit mode updates the live bmesh.
    """
    import bmesh

    def _shift(bm):
        uvl = bm.loops.layers.uv.verify()
        for face in bm.faces:
            for loop in face.loops:
                loop[uvl].uv.x += du
                loop[uvl].uv.y += dv

    for o in _meshes(objects):
        if o.mode == "EDIT":
            bm = bmesh.from_edit_mesh(o.data)
            _shift(bm)
            bmesh.update_edit_mesh(o.data)
        else:
            _bmesh_edit(o, _shift)


@_object_mode
def delete_extra_uv_sets(objects):
    """Remove all but the first UV map on the given mesh object(s) — "Cleanup UV Sets"."""
    for o in _meshes(objects):
        while len(o.data.uv_layers) > 1:
            o.data.uv_layers.remove(o.data.uv_layers[-1])


class UvUtils:
    """Namespace mirror of mayatk's ``UvUtils`` (helpers also exposed module-level)."""

    move_uvs = staticmethod(move_uvs)
    delete_extra_uv_sets = staticmethod(delete_extra_uv_sets)
