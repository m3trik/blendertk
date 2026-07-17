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

# Window-independent selection reader + window-supplying override for the Qt event-pump timer
# context (``bpy.context.window`` is ``None`` there — see ``_core_utils.selected_objects``). Both
# import Qt-free / bpy-deferred, so importing this module never needs a running Blender.
from blendertk.core_utils._core_utils import selected_objects, window_context_override

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


def _translate_fbx_options(options):
    """Translate Maya MEL FBX option names (``FBXExport*``) in *options* to ``export_scene.fbx``
    kwargs, returning a new dict.

    The Substance/Marmoset bridge templates are vendored verbatim from mayatk, where ``FBX_OPTIONS``
    drives ``mel.eval`` ``FBXExport*`` commands (``FbxUtils.set_fbx_options``). Those names are
    meaningless to Blender's ``bpy.ops.export_scene.fbx`` — passing one raises
    ``keyword "FBXExport…" unrecognized``. This is the Blender side of the "engine does the
    idiomatic-per-DCC translation" contract the bridges' ``_DEFAULT_FBX_OPTIONS`` documents.

    Known Maya names map to their Blender equivalent; an unmapped ``FBXExport*`` name is a Maya-only
    concept and is dropped. Every non-Maya key passes through unchanged, so Blender still validates
    real ``export_scene.fbx`` kwargs (a typo'd Blender kwarg still errors loudly). Maya translations
    are applied last so their intent wins over the Blender-native defaults regardless of dict order.
    """
    passthrough, maya = {}, {}
    for key, value in options.items():
        (maya if key.startswith("FBXExport") else passthrough)[key] = value
    for key, value in maya.items():
        if key == "FBXExportEmbeddedTextures":
            passthrough["embed_textures"] = bool(value)
            if value:  # Blender only embeds textures when the paths are copied in
                passthrough["path_mode"] = "COPY"
        # else: Maya MEL option with no Blender analogue — intentionally dropped.
    return passthrough


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

        opts = dict(_EXPORT_DEFAULTS)
        opts["use_selection"] = selection_only
        opts.update(fbx_opts)
        # ``object_types`` is an enum-flag: bpy.ops requires a set, but JSON-backed
        # option presets (scene_exporter's PresetStore tier) can only store a list.
        # A bare string (hand-edited preset) wraps as one item — set("MESH") would
        # explode into characters and produce a baffling enum error.
        if "object_types" in opts and not isinstance(opts["object_types"], set):
            value = opts["object_types"]
            opts["object_types"] = {value} if isinstance(value, str) else set(value)
        # Templates vendored from mayatk carry Maya MEL FBX names (e.g. FBXExportEmbeddedTextures);
        # translate them to export_scene.fbx kwargs so they don't fault the Blender exporter.
        opts = _translate_fbx_options(opts)

        # Selection is read via the window-independent ``selected_objects`` (view layer), never
        # ``bpy.context.selected_objects`` — the latter raises AttributeError from tentacle's Qt
        # event-pump timer (``bpy.context.window is None``). The operators run under
        # ``window_context_override`` because ``export_scene.fbx``'s io_scene_fbx handler *itself*
        # reads ``context.selected_objects`` internally, so a window must be in context for it.
        prior = list(selected_objects()) if objects is not None else None
        with window_context_override():
            if objects is not None:
                bpy.ops.object.select_all(action="DESELECT")
                for o in ptk.make_iterable(objects):
                    obj = bpy.data.objects.get(o) if isinstance(o, str) else o
                    if obj is not None:
                        obj.select_set(True)

            # Guard is inside the try so the finally restores the caller's selection even when it
            # raises (e.g. ``objects`` given but all names resolved to nothing — the DESELECT above
            # already cleared the real selection).
            try:
                if selection_only and not selected_objects():
                    raise RuntimeError("Nothing selected to export.")
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
        # Same contract as export above: io_scene_fbx reads context internally
        # (it selects the imported objects), so a window must be in context —
        # driven bare from tentacle's Qt event-pump timer, context.window is
        # None and the op raises.
        with window_context_override():
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
