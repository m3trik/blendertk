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
import logging

import pythontk as ptk

logger = logging.getLogger(__name__)

# Window-independent selection reader + window-supplying override for the Qt event-pump timer
# context (``bpy.context.window`` is ``None`` there — see ``_core_utils.selected_objects``). Both
# import Qt-free / bpy-deferred, so importing this module never needs a running Blender.
from blendertk.core_utils._core_utils import CoreUtils

# Bridge/export defaults: geometry + hierarchy, modifiers applied, selection-only — the safe
# hand-off set (the same defaults the bridges relied on when this lived in ``core_utils``).
# ``EMPTY`` is load-bearing, NOT decoration: Blender's FBX exporter drops every object whose
# type is excluded and RE-ROOTS its children, so a mesh-only set silently flattens the whole
# scene graph (Blender Empties are Maya's groups) — verified live, a grp>sub>mesh chain arrives
# in Maya/Unity as two parentless meshes. Bridges that want less (Substance / Marmoset) narrow
# this explicitly; DCC hand-offs widen it (see ``BlenderExportMixin._fbx_options``).
_EXPORT_DEFAULTS = {
    "use_selection": True,
    "object_types": {"MESH", "EMPTY"},
    "use_mesh_modifiers": True,
    "mesh_smooth_type": "FACE",
    "bake_anim": False,
    "path_mode": "AUTO",
}


class _FbxUtilsInternal(object):
    """Internal helpers for FbxUtils."""

    @staticmethod
    def _as_object_types(value):
        """Coerce an ``object_types`` value to the set ``bpy.ops`` requires.

        The enum-flag is a set to Blender, but JSON-backed option presets
        (scene_exporter's PresetStore tier) can only store a list, and a hand-edited
        preset may hold a bare string. ``set("MESH")`` would explode that into
        characters and produce a baffling enum error, so a string wraps as one item.

        Shared with :meth:`BlenderExportMixin._export_fbx`, which unions ``EMPTY`` in
        when it appends the ``data_export`` carrier — same coercion, so the two cannot
        disagree about what a caller's ``object_types`` meant.
        """
        if isinstance(value, set):
            return value
        return {value} if isinstance(value, str) else set(value or ())

    @staticmethod
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


class FbxUtils(_FbxUtilsInternal):
    """FBX import / export over ``bpy.ops`` (mirror of mayatk's ``FbxUtils`` export surface)."""

    # The declarative list of known metadata producers that stamp the shared
    # ``data_export`` carrier: name → (module, class, no-arg refresh method).
    # Mirror of mayatk's ``FbxUtils._KNOWN_PRODUCERS``, minus the session-hook
    # half: bpy has no before-FBX-export event, so the Scene Exporter's
    # ``export_data_node`` task is the only refresh dispatch point (producers
    # additionally publish at authoring time, which is what non-exporter paths
    # ship). Shots / Audio join here when their ports land. Add new producers
    # HERE — nothing else needs to change. Resolved lazily; an unimportable
    # producer is skipped (never blocks an export).
    #
    # ORDER IS A CONTRACT (dict insertion order = run order, same as mayatk's
    # rank sort): a producer that reads another's channel must come after it.
    # The mayatk twin runs shots before audio because audio scopes its events
    # against the freshly published ``fbx_takes`` — the port must land them
    # in that order here too.
    _KNOWN_PRODUCERS = {
        "shadow": (
            "blendertk.rig_utils.shadow_rig",
            "ShadowRig",
            "refresh_export_metadata",
        ),
        "emissive_groups": (
            "blendertk.mat_utils.emissive_groups",
            "EmissiveGroups",
            "refresh_export_metadata",
        ),
        "lightmap": (
            "blendertk.light_utils.lightmap_baker.lightmap_baker",
            "LightmapBaker",
            "refresh_export_metadata",
        ),
    }

    @staticmethod
    def run_export_preparers() -> None:
        """Refresh every known producer's ``data_export`` channel once, right now.

        Each producer is isolated — one failing or unimportable subsystem never
        blocks the others — and each no-ops (or clears its channel) when it has
        nothing to write, so scene edits since the last authoring-time publish
        (a deleted lightmapped mesh, a removed shadow plane) can't ship a stale
        manifest.  This is the one call an export pipeline needs to make the
        carrier current — name + behavior mirror of
        ``mtk.FbxUtils.run_export_preparers``.
        """
        import importlib

        for name, (module_path, cls_name, method) in FbxUtils._KNOWN_PRODUCERS.items():
            try:
                producer = getattr(importlib.import_module(module_path), cls_name)
                refresh = getattr(producer, method)
            except Exception:
                # Producers are speculative — an uninstalled subsystem is fine.
                logger.debug("Producer %r unavailable; skipped.", name, exc_info=True)
                continue
            try:
                refresh()
            except Exception:
                # But a resolvable producer that fails would silently ship
                # stale channels — surface it.
                logger.warning("Producer %r refresh failed.", name, exc_info=True)

    @staticmethod
    def export(filepath=None, objects=None, selection_only=True, strict=False, **fbx_opts):
        """Export to an FBX file — the consolidated counterpart of mayatk's ``FbxUtils.export``.

        Args:
            filepath: output ``.fbx`` path (``.fbx`` appended if missing; parent dirs created).
                Defaults to ``<temp>/<blend-stem>_bridge.fbx``.
            objects: objects (datablocks or names) to export; ``None`` exports the current
                selection. When given, they are selected first and the prior selection is
                restored afterward.
            selection_only: ``True`` exports the selection (``use_selection``); ``False`` exports
                the whole scene.
            strict: the selection funnel can only ship selectable, visible objects — a
                hidden member of *objects* silently fails ``select_set`` and one in a
                view-layer-excluded collection makes it RAISE, so unselectable members
                are collected instead and logged as a WARNING (count + first names):
                content loss must never be silent. ``strict=True`` raises
                ``RuntimeError`` with that list instead of exporting without them.
            **fbx_opts: overrides merged over the defaults, forwarded to
                ``bpy.ops.export_scene.fbx``.

        Returns:
            str: the written FBX path. Raises ``RuntimeError`` when ``selection_only`` and nothing
            is selected to export.
        """
        import bpy
        import tempfile

        if not filepath:
            stem = (
                os.path.splitext(os.path.basename(bpy.data.filepath))[0] or "untitled"
            )
            filepath = os.path.join(tempfile.gettempdir(), f"{stem}_bridge.fbx")
        if not filepath.lower().endswith(".fbx"):
            filepath += ".fbx"
        os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)

        opts = dict(_EXPORT_DEFAULTS)
        opts["use_selection"] = selection_only
        opts.update(fbx_opts)
        if "object_types" in opts:
            opts["object_types"] = _FbxUtilsInternal._as_object_types(
                opts["object_types"]
            )
        # Templates vendored from mayatk carry Maya MEL FBX names (e.g. FBXExportEmbeddedTextures);
        # translate them to export_scene.fbx kwargs so they don't fault the Blender exporter.
        opts = _FbxUtilsInternal._translate_fbx_options(opts)

        # Selection is read via the window-independent ``selected_objects`` (view layer), never
        # ``bpy.context.selected_objects`` — the latter raises AttributeError from tentacle's Qt
        # event-pump timer (``bpy.context.window is None``). The operators run under
        # ``window_context_override`` because ``export_scene.fbx``'s io_scene_fbx handler *itself*
        # reads ``context.selected_objects`` internally, so a window must be in context for it.
        prior = list(CoreUtils.selected_objects()) if objects is not None else None
        with CoreUtils.window_context_override():
            dropped = []
            if objects is not None:
                bpy.ops.object.select_all(action="DESELECT")
                for o in ptk.make_iterable(objects):
                    obj = bpy.data.objects.get(o) if isinstance(o, str) else o
                    if obj is None:
                        continue
                    # An unselectable object must not kill the whole export
                    # (an excluded-collection member makes select_set RAISE),
                    # but it will be silently absent from the FBX — a hidden
                    # object "succeeds" without selecting. Compare requested
                    # vs actually-selected and surface the difference below.
                    try:
                        obj.select_set(True)
                        selected = obj.select_get()
                    except RuntimeError:
                        selected = False
                    if not selected:
                        dropped.append(obj.name)

            # Guard is inside the try so the finally restores the caller's selection even when it
            # raises (e.g. ``objects`` given but all names resolved to nothing — the DESELECT above
            # already cleared the real selection).
            try:
                if dropped:
                    shown = ", ".join(dropped[:10]) + (
                        " …" if len(dropped) > 10 else ""
                    )
                    msg = (
                        f"{len(dropped)} requested object(s) cannot be selected and "
                        f"will be DROPPED from the FBX (hidden, selection-locked, or "
                        f"outside the active view layer): {shown}"
                    )
                    if strict:
                        raise RuntimeError(msg)
                    logger.warning(msg)
                if selection_only and not CoreUtils.selected_objects():
                    raise RuntimeError("Nothing selected to export.")
                bpy.ops.export_scene.fbx(filepath=filepath, **opts)
            finally:
                if prior is not None:  # restore the user's selection
                    bpy.ops.object.select_all(action="DESELECT")
                    for o in prior:
                        try:
                            o.select_set(True)
                        except (ReferenceError, RuntimeError):
                            # deleted since capture, or no longer selectable
                            # (e.g. its collection was view-layer-excluded) —
                            # a best-effort restore must not fail the export
                            # that already succeeded.
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
        with CoreUtils.window_context_override():
            bpy.ops.import_scene.fbx(filepath=filepath, **fbx_opts)
        return [o for o in bpy.data.objects if o not in before]

    @staticmethod
    def export_selection_fbx(filepath=None, objects=None, strict=False, **fbx_opts):
        """Export the selection (or ``objects``) to an FBX file for an external-app hand-off.

        The non-interactive counterpart of the scene slot's "Export Selection" — used by the
        Substance / Marmoset / RizomUV bridges to stage the current selection. Thin selection-only
        alias for :meth:`FbxUtils.export` (``strict`` passes through — see there).
        """
        return FbxUtils.export(
            filepath=filepath,
            objects=objects,
            selection_only=True,
            strict=strict,
            **fbx_opts,
        )
