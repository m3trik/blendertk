# !/usr/bin/python
# coding=utf-8
"""Exploded View: spread meshes apart from their shared center, and back.

``ExplodedView`` mirrors mayatk's (name + behavior): the buttons tentacle's
Exploded View panel drives, over the explode / unexplode engine module-level in
:mod:`~blendertk.display_utils._display_utils` (``explode_view`` /
``unexplode_view`` / ``unexplode_all`` / ``is_exploded``). Maya uses an
iterative repulsive-force solve; blendertk a deterministic bbox-center
separation (headless-testable) that stamps each pre-explode location as a
custom prop, so the toggle survives save/reload. Target resolution (which
objects a button acts on) is this class's, as mayatk keeps its ``objects`` /
``_get_target_objects`` on its ``ExplodedView``.

``import bpy`` is deferred into the call bodies (via ``selected_objects()``).
"""

from typing import Optional

import pythontk as ptk

from blendertk.core_utils._core_utils import CoreUtils
from blendertk.display_utils._display_utils import DisplayUtils


class ExplodedView(ptk.LoggingMixin):
    """Explode, un-explode and toggle a set of meshes: the given objects, or the
    selection when none were given.

    Parameters:
        objects: The objects to act on; ``None`` (the default) acts on the
            selection at each call.
    """

    def __init__(self, objects: Optional[list] = None, **kwargs):
        self._objects = objects
        self._kwargs = kwargs

    @property
    def objects(self) -> list:
        """The assigned objects, or the current selection."""
        if self._objects is not None:
            return list(self._objects)
        return CoreUtils.selected_objects()

    @objects.setter
    def objects(self, value: list):
        self._objects = value

    def _get_target_objects(self) -> list:
        """The meshes the objects stand for: a mesh itself, a group (an Empty
        with no mesh of its own) its child meshes, recursively -- mirror of
        mayatk's ``NodeUtils.get_unique_children``. Other types (curves,
        armatures, lights, ...) are dropped; the explode moves meshes only."""

        def collect(obj, found):
            if obj.type == "MESH":
                found.append(obj)
            elif obj.type == "EMPTY":
                for child in obj.children:
                    collect(child, found)

        found = []
        for obj in self.objects:
            collect(obj, found)
        meshes = list(dict.fromkeys(found))
        if not meshes:
            self.logger.warning("No objects provided or selected.")
        return meshes

    @CoreUtils.undoable
    def explode(self, objects: Optional[list] = None) -> list:
        """Push the meshes apart (those not exploded already); returns them."""
        if objects is not None:
            self.objects = objects
        meshes = self._get_target_objects()
        if len(meshes) < 2:
            if meshes:
                self.logger.warning("Select two or more mesh objects to explode apart.")
            return []
        DisplayUtils.explode_view(meshes)  # leaves the exploded ones alone
        return meshes

    @CoreUtils.undoable
    def un_explode(self, objects: Optional[list] = None) -> list:
        """Return the meshes to their stored positions; returns those restored."""
        if objects is not None:
            self.objects = objects
        return DisplayUtils.unexplode_view(self._get_target_objects())

    def toggle_explode(self, objects: Optional[list] = None) -> list:
        """Un-explode once the whole set is exploded; else explode what is not."""
        if objects is not None:
            self.objects = objects
        meshes = self._get_target_objects()
        if meshes and all(DisplayUtils.is_exploded([m]) for m in meshes):
            return self.un_explode()
        return self.explode()

    @CoreUtils.undoable
    def un_explode_all(self) -> list:
        """Restore every exploded object in the scene, regardless of selection."""
        return DisplayUtils.unexplode_all()
