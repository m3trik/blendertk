# !/usr/bin/python
# coding=utf-8
"""Per-camera visibility sets — rolled infrastructure for Maya's camera-sets isolate
commands (``SetExclusiveToCamera`` / ``SetHiddenFromCamera`` / ``CameraRemoveFrom*`` /
``CameraRemoveAll*``), which have no native Blender primitive: object visibility is either
global (``hide_viewport``/``hide_render``) or Scene/View-Layer/Collection-scoped, never
per-camera.

The rolled model:

* Each camera object carries two id-prop name lists — ``camvis_exclusive`` (only these
  objects are visible while the camera is in force) and ``camvis_hidden`` (these objects are
  hidden while it is). Id-props persist in the .blend.
* :func:`CameraVisibility.apply` derives ``hide_viewport``/``hide_render`` from the **scene's
  active camera**'s sets, stashing whatever it hides on the scene so the effect is exactly
  reversible (objects the user hid independently are left alone). Divergence from Maya
  (documented): Maya applies while *looking through* the camera per panel; Blender's
  look-through state is per-viewport draw state with no headless surface, so the sets follow
  ``scene.camera`` — switching the active camera (which look-through does) switches whose
  isolation is in force.
* :func:`enable_auto` subscribes ``scene.camera`` via ``bpy.msgbus`` so a camera switch
  re-applies automatically; without it, callers apply manually (the cameras menu does).

``import bpy`` is deferred into the call bodies (no import side effects); everything is
headless-testable.
"""

import pythontk as ptk

from blendertk.core_utils._core_utils import CoreUtils


class CameraVisibility:
    """Per-camera exclusive/hidden visibility sets (see module docstring)."""

    EXCLUSIVE_PROP = "camvis_exclusive"
    HIDDEN_PROP = "camvis_hidden"
    _STASH_PROP = "_camvis_stash"  # scene id-prop: {obj_name: (hide_viewport, hide_render)}
    _MSGBUS_OWNER = object()

    # ------------------------------------------------------------------ set management
    @classmethod
    def _resolve_camera(cls, camera=None):
        """``camera`` or the scene's active camera; raises when neither resolves."""
        import bpy

        cam = camera if camera is not None else bpy.context.scene.camera
        if cam is None or cam.type != "CAMERA":
            raise ValueError("A camera object is required (none active in the scene).")
        return cam

    @classmethod
    def _edit_set(cls, camera, prop, objects, add):
        cam = cls._resolve_camera(camera)
        names = list(cam.get(prop, []))
        edit = [
            o.name
            for o in (
                ptk.make_iterable(objects)
                if objects is not None
                else CoreUtils.selected_objects()
            )
            if o is not None and o is not cam
        ]
        if add:
            names += [n for n in edit if n not in names]
        else:
            names = [n for n in names if n not in edit]
        cam[prop] = names
        return names

    @classmethod
    def set_exclusive(cls, camera=None, objects=None):
        """Add ``objects`` (default: the selection) to the camera's exclusive set — while the
        camera is in force, ONLY its exclusive objects stay visible (Maya
        ``SetExclusiveToCamera``). Returns the set's names."""
        names = cls._edit_set(camera, cls.EXCLUSIVE_PROP, objects, add=True)
        cls.apply()
        return names

    @classmethod
    def set_hidden(cls, camera=None, objects=None):
        """Add ``objects`` (default: the selection) to the camera's hidden set (Maya
        ``SetHiddenFromCamera``). Returns the set's names."""
        names = cls._edit_set(camera, cls.HIDDEN_PROP, objects, add=True)
        cls.apply()
        return names

    @classmethod
    def remove_from_exclusive(cls, camera=None, objects=None):
        """Remove ``objects`` (default: the selection) from the camera's exclusive set (Maya
        ``CameraRemoveFromExclusive``)."""
        names = cls._edit_set(camera, cls.EXCLUSIVE_PROP, objects, add=False)
        cls.apply()
        return names

    @classmethod
    def remove_from_hidden(cls, camera=None, objects=None):
        """Remove ``objects`` (default: the selection) from the camera's hidden set (Maya
        ``CameraRemoveFromHidden``)."""
        names = cls._edit_set(camera, cls.HIDDEN_PROP, objects, add=False)
        cls.apply()
        return names

    @classmethod
    def remove_all(cls, camera=None):
        """Clear both sets on one camera (Maya ``CameraRemoveAll``)."""
        cam = cls._resolve_camera(camera)
        for prop in (cls.EXCLUSIVE_PROP, cls.HIDDEN_PROP):
            if prop in cam:
                del cam[prop]
        cls.apply()

    @classmethod
    def remove_all_for_all(cls):
        """Clear both sets on EVERY camera (Maya ``CameraRemoveAllForAll``)."""
        import bpy

        for cam in bpy.data.objects:
            if cam.type != "CAMERA":
                continue
            for prop in (cls.EXCLUSIVE_PROP, cls.HIDDEN_PROP):
                if prop in cam:
                    del cam[prop]
        cls.apply()

    @classmethod
    def get_sets(cls, camera=None):
        """(exclusive_names, hidden_names) for the camera."""
        cam = cls._resolve_camera(camera)
        return list(cam.get(cls.EXCLUSIVE_PROP, [])), list(cam.get(cls.HIDDEN_PROP, []))

    # ------------------------------------------------------------------ application
    @classmethod
    def apply(cls):
        """Re-derive object visibility from the ACTIVE camera's sets.

        Restores the previous stash first (so edits/camera switches never accumulate), then —
        when the active camera carries sets — hides the derived objects and records exactly
        what was hidden (with prior flag values) in the scene stash. Geometry-type objects
        only for the exclusive rule: helper objects (lights, empties, other cameras) are not
        implicitly hidden by an exclusive set, matching Maya's geometry-isolate behavior.
        """
        import bpy

        scene = bpy.context.scene
        cls.restore()

        cam = scene.camera
        if cam is None:
            return
        exclusive = set(cam.get(cls.EXCLUSIVE_PROP, []))
        hidden = set(cam.get(cls.HIDDEN_PROP, []))
        if not exclusive and not hidden:
            return

        stash = {}
        for o in scene.objects:
            if o is cam:
                continue
            hide = o.name in hidden or (
                bool(exclusive)
                and o.name not in exclusive
                and o.type not in ("CAMERA", "LIGHT", "EMPTY", "SPEAKER", "LIGHT_PROBE")
            )
            if hide and not (o.hide_viewport and o.hide_render):
                stash[o.name] = [int(o.hide_viewport), int(o.hide_render)]
                o.hide_viewport = True
                o.hide_render = True
        scene[cls._STASH_PROP] = stash

    @classmethod
    def restore(cls):
        """Undo whatever :func:`apply` hid (reads the scene stash; missing objects skipped)."""
        import bpy

        scene = bpy.context.scene
        stash = scene.get(cls._STASH_PROP, {})
        for name in list(stash.keys()):
            o = bpy.data.objects.get(name)
            if o is not None:
                prior = stash[name]
                o.hide_viewport = bool(prior[0])
                o.hide_render = bool(prior[1])
        if cls._STASH_PROP in scene:
            del scene[cls._STASH_PROP]

    # ------------------------------------------------------------------ auto re-apply
    @classmethod
    def enable_auto(cls):
        """Re-apply automatically whenever ``scene.camera`` changes (msgbus). Idempotent.
        Session-scoped: msgbus subscriptions are cleared on file load — re-call after opening
        a .blend (the sets themselves persist as id-props; every set-editing method also
        applies directly, so manual workflows never need this at all)."""
        import bpy

        bpy.msgbus.clear_by_owner(cls._MSGBUS_OWNER)
        bpy.msgbus.subscribe_rna(
            key=(bpy.types.Scene, "camera"),
            owner=cls._MSGBUS_OWNER,
            args=(),
            notify=cls.apply,
        )

    @classmethod
    def disable_auto(cls):
        """Stop the automatic re-apply (sets stay stored; the current derivation is restored)."""
        import bpy

        bpy.msgbus.clear_by_owner(cls._MSGBUS_OWNER)
        cls.restore()
