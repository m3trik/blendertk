# !/usr/bin/python
# coding=utf-8
"""FBX import / export helpers — the Blender counterpart of mayatk's ``env_utils.fbx_utils``
(``btk.FbxUtils`` ↔ ``mtk.FbxUtils``).

Mirrors the module + class name and the **portable export/import** surface over
``bpy.ops.export_scene.fbx`` / ``import_scene.fbx``. Two intentional divergences from mayatk:

* **No animation-takes / auto-export machinery.** mayatk's ``FbxUtils`` also ships
  ``reset_takes`` / ``apply_takes`` and a kBeforeExport/kAfterExport auto-export hook (one
  AnimStack per Unity clip, driven through MEL ``FBXExportSplitAnimationIntoTakes`` and OpenMaya
  ``MSceneMessage`` callbacks). Blender's FBX exporter emits AnimStacks straight from NLA strips /
  actions (``bake_anim`` + ``bake_anim_use_all_actions``), and ``bpy.app.handlers`` has **no**
  before-FBX-export event (the same reason ``ScriptJobManager`` has no ``add_om_callback``), so
  that machinery has no Blender analogue.
* **No MEL plugin/preset/option layer.** Maya needs ``load_plugin`` / ``set_fbx_options`` /
  ``load_preset`` because its FBX options are set out-of-band via MEL; Blender's exporter takes its
  options as direct ``bpy.ops`` keyword args, so callers just pass ``**fbx_opts``.

``import bpy`` (and ``tempfile``) are deferred into the call bodies so resolving the package
surface never requires a running Blender. ``export_selection_fbx`` stays exported (module-level)
as the selection-only convenience used by the Substance / Marmoset / RizomUV bridges.
"""
import os

import pythontk as ptk

# Bridge/export defaults: mesh-only, modifiers applied, selection-only — the safe hand-off set
# (the same defaults the bridges relied on when this lived in ``core_utils``).
_EXPORT_DEFAULTS = {
    "use_selection": True,
    "object_types": {"MESH"},
    "use_mesh_modifiers": True,
    "mesh_smooth_type": "FACE",
    "bake_anim": False,
    "path_mode": "AUTO",
}


class FbxUtils:
    """FBX import / export over ``bpy.ops`` (mirror of mayatk's ``FbxUtils`` export surface)."""

    @staticmethod
    def export(filepath=None, objects=None, selection_only=True, **fbx_opts):
        """Export to an FBX file — the consolidated counterpart of mayatk's ``FbxUtils.export``.

        Args:
            filepath: output ``.fbx`` path (``.fbx`` appended if missing; parent dirs created).
                Defaults to ``<temp>/<blend-stem>_bridge.fbx``.
            objects: objects (datablocks or names) to export; ``None`` exports the current
                selection. When given, they are selected first and the prior selection is
                restored afterward.
            selection_only: ``True`` exports the selection (``use_selection``); ``False`` exports
                the whole scene.
            **fbx_opts: overrides merged over the defaults, forwarded to
                ``bpy.ops.export_scene.fbx``.

        Returns:
            str: the written FBX path. Raises ``RuntimeError`` when ``selection_only`` and nothing
            is selected to export.
        """
        import bpy
        import tempfile

        if not filepath:
            stem = os.path.splitext(os.path.basename(bpy.data.filepath))[0] or "untitled"
            filepath = os.path.join(tempfile.gettempdir(), f"{stem}_bridge.fbx")
        if not filepath.lower().endswith(".fbx"):
            filepath += ".fbx"
        os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)

        prior = None
        if objects is not None:
            prior = list(getattr(bpy.context, "selected_objects", []) or [])
            bpy.ops.object.select_all(action="DESELECT")
            for o in ptk.make_iterable(objects):
                obj = bpy.data.objects.get(o) if isinstance(o, str) else o
                if obj is not None:
                    obj.select_set(True)

        if selection_only and not (getattr(bpy.context, "selected_objects", None) or []):
            raise RuntimeError("Nothing selected to export.")

        opts = dict(_EXPORT_DEFAULTS)
        opts["use_selection"] = selection_only
        opts.update(fbx_opts)
        try:
            bpy.ops.export_scene.fbx(filepath=filepath, **opts)
        finally:
            if prior is not None:  # restore the user's selection
                bpy.ops.object.select_all(action="DESELECT")
                for o in prior:
                    try:
                        o.select_set(True)
                    except ReferenceError:
                        pass
        return filepath

    @staticmethod
    def import_fbx(filepath, **fbx_opts):
        """Import an FBX file (wrapper over ``bpy.ops.import_scene.fbx``).

        Args:
            filepath: the ``.fbx`` to import (``$VARS`` expanded). Raises ``FileNotFoundError`` if
                absent.
            **fbx_opts: forwarded to ``bpy.ops.import_scene.fbx``.

        Returns:
            list: the objects created by the import (those newly added to ``bpy.data.objects``).
        """
        import bpy

        filepath = os.path.abspath(os.path.expandvars(filepath))
        if not os.path.isfile(filepath):
            raise FileNotFoundError(f"FBX not found: {filepath}")
        before = set(bpy.data.objects)
        bpy.ops.import_scene.fbx(filepath=filepath, **fbx_opts)
        return [o for o in bpy.data.objects if o not in before]


def export_selection_fbx(filepath=None, objects=None, **fbx_opts):
    """Export the selection (or ``objects``) to an FBX file for an external-app hand-off.

    The non-interactive counterpart of the scene slot's "Export Selection" — used by the Substance
    / Marmoset / RizomUV bridges to stage the current selection. Thin selection-only alias for
    :meth:`FbxUtils.export` (kept module-level for the established ``btk.export_selection_fbx`` API).
    """
    return FbxUtils.export(filepath=filepath, objects=objects, selection_only=True, **fbx_opts)


def import_fbx(filepath, **fbx_opts):
    """Import an FBX file; returns the objects created (alias for :meth:`FbxUtils.import_fbx`)."""
    return FbxUtils.import_fbx(filepath, **fbx_opts)
