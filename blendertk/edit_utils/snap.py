# !/usr/bin/python
# coding=utf-8
"""Snap: vertices to a target surface, to the closest target vertex, or to the
world grid -- mirror of mayatk's ``edit_utils.snap.Snap`` (name + behavior).

The math lives module-level in :mod:`~blendertk.edit_utils._edit_utils`
(``snap_to_surface`` / ``snap_closest_verts`` / ``snap_to_grid``); this class
is the API tentacle's Snap panel calls, as it calls mayatk's. Maya's "source
first, target last" is the **active** object here: Blender keeps no selection
order.

``import bpy`` is deferred into the call bodies.
"""

import pythontk as ptk

from blendertk.core_utils._core_utils import CoreUtils
from blendertk.edit_utils._edit_utils import EditUtils


class Snap(ptk.HelpMixin):
    """Vertex and mesh snapping utilities."""

    @staticmethod
    def source_and_target():
        """The selected meshes as ``(sources, target)``: the active mesh is the
        target, the other selected meshes the sources; ``([], None)`` unless
        two or more meshes are selected with a mesh active."""
        meshes = [o for o in CoreUtils.selected_objects() if o.type == "MESH"]
        target = CoreUtils.active_object()
        if len(meshes) < 2 or target is None or target not in meshes:
            return [], None
        return [o for o in meshes if o is not target], target

    @staticmethod
    @CoreUtils.undoable
    def snap_to_surface(source_meshes, target_mesh, offset=None, threshold=None, invert=False):
        """Snap the source meshes' vertices onto the target's surface, at
        *offset* (see ``EditUtils.snap_to_surface``); returns the count moved."""
        return EditUtils.snap_to_surface(
            source_meshes, target_mesh, offset=offset or 0.0, threshold=threshold, invert=invert
        )

    @staticmethod
    @CoreUtils.undoable
    def snap_to_closest_vertex(obj1, obj2, tolerance=10.0, freeze_transforms=False):
        """Snap *obj1*'s vertices to *obj2*'s closest within *tolerance*;
        returns the count moved. *freeze_transforms* is mayatk's signature and
        has no effect: the world math here is exact under any transform."""
        return EditUtils.snap_closest_verts(obj1, obj2, tolerance=tolerance)

    @staticmethod
    @CoreUtils.undoable
    def snap_to_grid(objects=None, grid_size=1.0, axes="xyz"):
        """Snap the selected vertices (Edit Mode) or each object's origin to
        the grid on *axes*; returns the count snapped."""
        return EditUtils.snap_to_grid(objects, grid_size=grid_size, axes=axes)
