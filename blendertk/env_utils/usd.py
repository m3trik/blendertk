# !/usr/bin/python
# coding=utf-8
"""USD import / export helpers — the Blender counterpart of mayatk's ``env_utils.usd``
(``btk.UsdUtils`` ↔ ``mtk.UsdUtils``).

Mirrors the module + class name and the export/import surface over Blender's native
USD runtime (``bpy.ops.wm.usd_export`` / ``wm.usd_import``), which already converts
Principled BSDF ↔ ``UsdPreviewSurface``, keeps instancing (``use_instancing``), and
round-trips custom properties. Two intentional divergences from mayatk:

* **No plugin/namespace layer.** Maya needs ``load_plugin`` (mayaUsdPlugin) and
  active-namespace isolation; Blender's USD ops are built in and Blender has no
  namespaces — imported objects are simply returned (datablock diff), matching
  ``FbxUtils.import_fbx``'s contract.
* **Native ``.usdz``.** Blender packages ``.usdz`` itself (a ``.usdz`` filepath is
  enough); Maya-side ``.usdz`` composes ``pythontk.UsdzPackager`` instead. The shared
  zero-dep floor (sniffing/packaging) still lives in ``pythontk.file_utils.usd``.

Option names drift across Blender majors (4.x ↔ 5.x renamed several ``usd_export``
kwargs), so kwargs are filtered against the operator's live RNA properties — an
option this Blender doesn't know is dropped with a log line instead of faulting the
export (the same resilience contract as ``fbx_utils._translate_fbx_options``).

``import bpy`` is deferred into the call bodies so resolving the package surface
never requires a running Blender.
"""
import os

import pythontk as ptk

# Window-independent selection reader + window-supplying override for the Qt
# event-pump timer context (see ``fbx_utils`` — same contract: the USD io
# handlers read ``context`` internally, so a window must be in context).
from blendertk.core_utils._core_utils import selected_objects, window_context_override

#: Extensions the USD runtime reads/writes (shared SSoT with pythontk).
USD_EXTENSIONS = ptk.USD_EXTENSIONS

# Interchange-quality export defaults (mirror of mayatk's intent: materials as
# preview surface, textures alongside, instancing preserved, no animation).
_EXPORT_DEFAULTS = {
    "selected_objects_only": True,
    "export_materials": True,
    "generate_preview_surface": True,
    "export_textures": True,
    "relative_paths": True,
    "use_instancing": True,
    "export_animation": False,
}


def _filter_op_options(op, options):
    """*options* restricted to kwargs *op* actually declares, a new dict.

    Blender renames USD operator options between majors; a stale name must
    degrade to a logged drop, not fault the whole export (mirror of the FBX
    option-translation contract). Never filters out ``filepath``.
    """
    known = {p.identifier for p in op.get_rna_type().properties}
    kept, dropped = {}, []
    for key, value in options.items():
        if key in known:
            kept[key] = value
        else:
            dropped.append(key)
    if dropped:
        import logging

        logging.getLogger(__name__).warning(
            "USD option(s) unknown to this Blender dropped: %s", ", ".join(dropped)
        )
    return kept


class UsdUtils:
    """USD import / export over ``bpy.ops`` (mirror of mayatk's ``UsdUtils``)."""

    EXTENSIONS = USD_EXTENSIONS

    @staticmethod
    def is_usd_file(filepath) -> bool:
        """True when *filepath* is a USD layer/package (delegates to pythontk)."""
        return ptk.is_usd_file(filepath)

    @staticmethod
    def export(filepath=None, objects=None, selection_only=True, **usd_opts):
        """Export to a USD file — the counterpart of mayatk's ``UsdUtils.export``.

        Args:
            filepath: output path (``.usd`` appended when no USD extension is
                given; parent dirs created). A ``.usdz`` path produces a
                packaged archive (Blender packages natively). Defaults to
                ``<temp>/<blend-stem>_bridge.usd``.
            objects: objects (datablocks or names) to export; ``None`` exports the
                current selection. When given, they are selected first and the prior
                selection is restored afterward.
            selection_only: ``True`` exports the selection
                (``selected_objects_only``); ``False`` exports the whole scene.
            **usd_opts: overrides merged over the defaults, forwarded to
                ``bpy.ops.wm.usd_export`` (unknown-to-this-Blender names are
                dropped with a log line).

        Returns:
            str: the written USD path. Raises ``RuntimeError`` when
            ``selection_only`` and nothing is selected to export.
        """
        import bpy
        import tempfile

        if not filepath:
            stem = os.path.splitext(os.path.basename(bpy.data.filepath))[0] or "untitled"
            filepath = os.path.join(tempfile.gettempdir(), f"{stem}_bridge.usd")
        if os.path.splitext(filepath)[1].lower() not in USD_EXTENSIONS:
            filepath += ".usd"
        os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)

        opts = dict(_EXPORT_DEFAULTS)
        opts["selected_objects_only"] = selection_only
        opts.update(usd_opts)
        opts = _filter_op_options(bpy.ops.wm.usd_export, opts)

        prior = list(selected_objects()) if objects is not None else None
        with window_context_override():
            if objects is not None:
                bpy.ops.object.select_all(action="DESELECT")
                for o in ptk.make_iterable(objects):
                    obj = bpy.data.objects.get(o) if isinstance(o, str) else o
                    if obj is not None:
                        obj.select_set(True)
            try:
                if selection_only and not selected_objects():
                    raise RuntimeError("Nothing selected to export.")
                bpy.ops.wm.usd_export(filepath=filepath, **opts)
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
    def import_usd(filepath, **usd_opts):
        """Import a USD file (wrapper over ``bpy.ops.wm.usd_import``).

        Args:
            filepath: the USD layer/package to import (``$VARS`` expanded).
                Raises ``FileNotFoundError`` if absent.
            **usd_opts: forwarded to ``bpy.ops.wm.usd_import`` (unknown names
                dropped with a log line).

        Returns:
            list: the objects created by the import (those newly added to
            ``bpy.data.objects``).
        """
        import bpy

        filepath = os.path.abspath(os.path.expandvars(filepath))
        if not os.path.isfile(filepath):
            raise FileNotFoundError(f"USD file not found: {filepath}")
        opts = _filter_op_options(bpy.ops.wm.usd_import, usd_opts)
        before = set(bpy.data.objects)
        with window_context_override():
            bpy.ops.wm.usd_import(filepath=filepath, **opts)
        return [o for o in bpy.data.objects if o not in before]


def export_selection_usd(filepath=None, objects=None, **usd_opts):
    """Export the selection (or ``objects``) to a USD file for an external-app hand-off.

    Thin selection-only alias for :meth:`UsdUtils.export` (the USD counterpart of
    ``export_selection_fbx``).
    """
    return UsdUtils.export(filepath=filepath, objects=objects, selection_only=True, **usd_opts)


def import_usd(filepath, **usd_opts):
    """Import a USD file; returns the objects created (alias for :meth:`UsdUtils.import_usd`)."""
    return UsdUtils.import_usd(filepath, **usd_opts)
