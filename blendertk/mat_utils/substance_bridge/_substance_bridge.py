# !/usr/bin/python
# coding=utf-8
"""Substance 3D Painter bridge -- export Blender selection and hand off to Painter.

Mirror of mayatk's ``mat_utils.substance_bridge._substance_bridge``: the Blender half
of the split, mirroring :mod:`blendertk.mat_utils.marmoset_bridge`:

* :class:`SubstanceBridge` (this module) -- a
  :class:`._substance_engine.SubstanceEngine` that supplies only the scene I/O the
  engine's produce step is written against: the FBX / USD writers, the selection, the
  material manifest, the textures assigned to the selection, the
  :class:`~blendertk.mat_utils.bake_sets.BakeSourceSet` export and the scene
  custom-property record of the last export.
* :mod:`_substance_engine` -- ``SubstanceEngine``, the DCC-free Painter half (template
  parsing, the launch line and RPC ops, Painter launch / attach and the managed-instance
  registry, the RPC plugin install, texture staging). Vendored byte-identical from mayatk.
* :mod:`templates/*.py` -- declarative metadata describing each handoff (vendored verbatim
  from mayatk -- DCC-agnostic, describes only the Painter-side handoff).
* :mod:`parameters` -- UI-tunable knob registry referenced by templates (vendored verbatim).
* :mod:`connection` -- live process I/O (stdout / log tail / RPC) (vendored verbatim).
"""

import logging
import os
from typing import Any, Dict, List, Optional, Union

import pythontk as ptk

from blendertk.core_utils._core_utils import CoreUtils
from blendertk.env_utils.fbx_utils import FbxUtils
from blendertk.env_utils.handoff_export import BlenderExportMixin
from blendertk.env_utils.usd import UsdUtils
from blendertk.mat_utils.bake_sets import BakeSourceSet
from blendertk.mat_utils.mat_manifest import MatManifest

# The DCC-free engine, plus the names the slots and tests import from this module.
import blendertk.mat_utils.substance_bridge._substance_engine as _engine
from blendertk.mat_utils.substance_bridge._substance_engine import (  # noqa: F401
    SubstanceEngine,
    _TEMPLATE_DEFAULTS,
    _TEMPLATE_DIR,
)
from blendertk.mat_utils.substance_bridge.connection import (  # noqa: F401
    APP,
    SubstanceConnection,
)
from blendertk.mat_utils.substance_bridge.substance_rpc import (  # noqa: F401
    DEFAULT_RPC_PORT,
)

logger = logging.getLogger(__name__)

# The mode / target vocabulary is the engine's. Bound here as assignments (not a
# bare import) because it is this module's public surface too: the slots and the
# tests import it from here.
SEND_TO = _engine.SEND_TO
ROUND_TRIP = _engine.ROUND_TRIP
TARGET_AUTO = _engine.TARGET_AUTO
TARGET_NEW = _engine.TARGET_NEW
TARGET_CURRENT = _engine.TARGET_CURRENT


# FBX options tuned for Substance Painter (Blender-native ``export_scene.fbx`` kwargs -- the
# idiomatic-per-DCC translation of mayatk's ``_DEFAULT_FBX_OPTIONS`` intent, not a literal
# flag-for-flag mirror; see CLAUDE.md "relax the mirror where concepts diverge").
_DEFAULT_FBX_OPTIONS: Dict[str, Any] = {
    "mesh_smooth_type": "FACE",
    "use_tspace": True,
    "use_triangles": False,
    "embed_textures": False,
    "path_mode": "AUTO",
    "object_types": {"MESH", "EMPTY"},
    "bake_anim": False,
    "global_scale": 1.0,
    "axis_up": "Y",
}

# USD options tuned for Substance Painter (the USD carrier): the shared interchange
# set (Painter's texture sets come from the UsdPreviewSurface bindings), geometry
# only like the FBX set. Mirror of mayatk's ``_DEFAULT_USD_OPTIONS`` intent.
_DEFAULT_USD_OPTIONS: Dict[str, Any] = dict(
    UsdUtils.INTERCHANGE_EXPORT_OPTIONS,
    export_armatures=False,
    export_shapekeys=False,
)


# The bake source was this module's ``HighPolySet`` until it became the
# cross-bridge ``bake_sets.BakeSourceSet`` (mayatk's name). A .blend saved
# under the old class still resolves -- ``BakeSourceSet.LEGACY_STAMPS`` adopts
# its collection -- and the Python name is served as a warned alias until
# ``remove_in`` -- a release after the one this notice first ships in.
ptk.Deprecation.attributes(
    globals(),
    {"HighPolySet": "blendertk.mat_utils.bake_sets.BakeSourceSet"},
    remove_in="0.15.0",
    since="2026-09-27",
)


# -- Bridge ----------------------------------------------------------------


class SubstanceBridge(SubstanceEngine):
    """Export Blender selection to Substance Painter via a chosen template.

    The Blender half of the bridge. :class:`SubstanceEngine` (vendored, DCC-free)
    owns the hand-off itself -- the ``resolve -> preflight -> produce -> deliver``
    skeleton, template parsing, the launch line and RPC ops, Painter launch / attach
    and the managed-instance registry, texture staging. This class supplies the scene
    I/O the engine calls: the FBX / USD writers, the selection, the material manifest,
    the textures assigned to the selection, the :class:`BakeSourceSet` export and the
    scene custom-property record of the last export.

    Two operating modes per template (declared via ``BRIDGE_MODES``):

    * ``send_to`` -- launch Painter interactively, fire-and-forget.
    * ``roundtrip`` -- launch Painter with remote scripting, send the
      template's ``RPC_SCRIPT`` body, and wait for the call to complete.

    Usage::

        SubstanceBridge().send()                       # default: import template
        SubstanceBridge().send(template="import", mode="send_to")

    Backward-compatible with the pre-restructure API: legacy kwargs
    (``headless``, ``enable_remote``) are accepted and ignored if not
    meaningful to the template-driven model.
    """

    # Scratch namespace of a send with no Output Dir
    # (``<temp>/blender_substance_bridge_handoff``; see ``HandoffBridge._scratch_dir``).
    payload_prefix = "blender_substance_bridge"

    DEFAULT_FBX_OPTIONS = _DEFAULT_FBX_OPTIONS

    #: Suffix appended to the export stem for the companion high-poly file.
    #: Sourced from the shared set class so every bridge derives the same
    #: companion filename (see :meth:`BakeSourceSet.companion_path`).
    HIGH_POLY_SUFFIX = BakeSourceSet.FILE_SUFFIX

    # -- Public API -------------------------------------------------------

    def send(
        self,
        objects: Optional[List[str]] = None,
        output_dir: Optional[str] = None,
        output_name: Optional[str] = None,
        painter_exe: Optional[str] = None,
        fbx_options: Optional[Dict[str, Any]] = None,
        template: str = "import",
        mode: str = SEND_TO,
        target: Union[str, int] = TARGET_AUTO,
        params: Optional[Dict[str, Any]] = None,
        **legacy_kwargs: Any,
    ) -> Optional[Dict[str, Any]]:
        """Export *objects*, render *template* in *mode*, hand off to Painter.

        Parameters:
            objects: Objects to export. Defaults to current selection.
            output_dir: Where the FBX (and optional manifest) lands.
                Defaults to ``<temp>/blender_substance_bridge_handoff``.
            output_name: Base filename without extension. Defaults to the
                saved ``.blend`` name or ``"untitled"``.
            painter_exe: Explicit ``Adobe Substance 3D Painter.exe`` override.
            fbx_options: ``export_scene.fbx`` overrides merged on top of defaults.
            template: Template stem under ``templates/`` (``"import"`` etc.).
            mode: ``"send_to"`` (fire-and-forget) or ``"round_trip"``.
                Must match one of the template's declared
                :data:`BRIDGE_MODES`.
            target: Which Painter to send to. One of:
                - ``"auto"`` (default) -- reuse a managed live instance if
                  one exists; otherwise launch new.
                - ``"new"`` -- always launch a fresh Painter.
                - ``"current"`` -- require an existing managed instance;
                  error if none is reachable.
                - ``int`` -- attach to that explicit RPC port.
                The template's ``TARGET_INSTANCE`` constant constrains
                which values are valid; conflicts surface as errors.
            params: Placeholder overrides, e.g. ``{"PAINTER_RESOLUTION": 4096}``.
            **legacy_kwargs: Swallowed (``headless``, ``enable_remote``) for
                backward compatibility with the pre-restructure API.

        Returns:
            A result dict with ``fbx``, ``mode``, ``connection`` (the
            :class:`SubstanceConnection`, or *None* on a hint-declaring
            template's graceful fallback), ``output_dir``, ``high_poly``
            (only when a companion high-poly file was written), ``delivered``
            (False when the RPC leg was skipped or failed on a
            ``send_to`` template), and -- for RPC templates --
            ``rpc_results`` (one value per op that succeeded), ``rpc_failed``
            (op names that did not, present only when some did fail; a
            ``send_to`` run continues past them) and/or ``rpc_result`` (the
            ``RPC_SCRIPT`` return). *None* on failure.
        """
        return self._handoff(
            objects,
            template=template,
            mode=mode,
            target=target,
            params=params,
            legacy_kwargs=legacy_kwargs,
            output_dir=output_dir,
            output_name=output_name,
            painter_exe=painter_exe,
            fbx_options=fbx_options,
        )

    # -- Scene I/O (the engine's DCC hooks) -------------------------------

    def _produce(self, objects, request) -> Optional[ptk.Payload]:
        """The engine's produce step over the scope's hierarchy closure.

        Blender's FBX writes exactly the objects it is handed, so a send of a
        selected group shipped the Empty alone -- no mesh for Painter, no
        material in the manifest (Maya's export-selection writes the subtree,
        which the vendored engine assumes). Closed here, before the export,
        the texture staging and the manifest all read the same list, by the
        rule every Blender bridge exports by
        (:meth:`BlenderExportMixin.scope_closure`). The engine reads the
        selection lazily; the closure needs it now.
        """
        if objects is None:
            objects = self._selected_objects()
        objects = BlenderExportMixin.scope_closure(objects, request.params)
        return super()._produce(objects, request)

    def _export_model_usd(self, path, objects, request, fbx_options) -> None:
        options = dict(_DEFAULT_USD_OPTIONS)
        options.update(request.get("usd_options") or {})
        UsdUtils.export_selection_usd(filepath=path, objects=objects, **options)

    def _export_model_fbx(self, path, objects, request, fbx_options) -> None:
        FbxUtils.export_selection_fbx(filepath=path, objects=objects, **fbx_options)

    def _selected_objects(self) -> List[Any]:
        """The Blender selection, read only when the scope needs it."""
        import blendertk as btk

        return btk.selected_objects()

    def _material_manifest(self, objects: List[Any]) -> Dict[str, Any]:
        return MatManifest.build(objects)

    def _assigned_texture_paths(self, objects: List[Any]) -> List[str]:
        """Every texture file the material node trees of *objects* reference."""
        from blendertk.mat_utils._mat_utils import MatUtils

        return MatUtils.get_texture_paths(objects=objects, absolute=True)

    @classmethod
    def _recorded_export_path(cls) -> Optional[str]:
        """Return the FBX path recorded by the last export, or ``None``."""
        try:
            import bpy

            value = bpy.context.scene.get(cls.EXPORT_RECORD_KEY)
        except Exception:  # noqa: BLE001 -- no bpy / no scene
            return None
        if not value:
            return None
        return str(value).replace("\\", "/")

    @classmethod
    def _record_export_path(cls, fbx_path: str) -> None:
        """Persist *fbx_path* on the scene (custom property, forward slashes)."""
        try:
            import bpy

            bpy.context.scene[cls.EXPORT_RECORD_KEY] = fbx_path.replace("\\", "/")
        except Exception as e:  # noqa: BLE001 -- recording is best-effort
            logger.debug("Could not record export path on the scene: %s", e)

    @classmethod
    def source_model_path_for(cls, fbx_path: str) -> str:
        """``.../asset.fbx`` -> ``.../asset_source.fbx``.

        Derived from the main export rather than re-resolved, so a
        ``REUSE_RECORDED_EXPORT`` template's high-poly file lands beside
        the exact mesh the open Painter project was built from. Delegates
        to the shared convention on :class:`BakeSourceSet` (mirror of
        mayatk's).
        """
        return BakeSourceSet.companion_path(fbx_path)

    @classmethod
    @ptk.Deprecation.symbol(
        "SubstanceBridge.source_model_path_for", remove_in="0.15.0", since="2026-09-27"
    )
    def high_poly_path_for(cls, fbx_path: str) -> str:
        """Renamed :meth:`source_model_path_for` (mayatk's name)."""
        return cls.source_model_path_for(fbx_path)

    def _export_bake_source(
        self,
        fbx_path: str,
        fbx_options: Dict[str, Any],
        referenced: set,
        request: ptk.HandoffRequest,
    ) -> Optional[str]:
        """Export :class:`BakeSourceSet`'s meshes to ``<stem>_source.fbx``.

        Returns the written path, or ``None`` when the template doesn't claim
        the Bake Source row, the file has no set, or the export failed. A
        failure here is logged and swallowed: the main mesh is already on disk
        and the handoff is still worth making -- Painter simply opens without
        a bake source.

        **The set's contents are the switch.** There is no companion checkbox:
        a file that has defined a bake source has, by defining it, said to
        ship it, and one that hasn't ships nothing. The pairing this replaces
        (a set plus an "Export Bake Source" tick) had two ways to spell "off"
        and one silent failure mode -- a set defined, the box left clear --
        which is the state a user reads as a bug. Same contract as the
        Marmoset bridge, which never had the second control.

        Hidden members export exactly like visible ones, and the scene is
        left as it was found. Blender's FBX export is selection-based and
        drops whatever it cannot select, so each member is revealed for the
        write alone (:meth:`CoreUtils.visible_override`, which puts every
        flag and link back); a high-poly mesh hidden by its own flags or by
        its collection otherwise left the bake source without it. Reading the
        set rather than the selection is also why this can't disturb a
        "Visible Only" scope. What ships is the set's MESHES
        (:meth:`BakeSourceSet.meshes`): the FBX writes exactly the objects it
        is handed, so a member that groups the source would otherwise send an
        Empty and none of the geometry under it.
        """
        if "BAKE_SOURCE_SET" not in referenced:
            return None

        if not BakeSourceSet.exists():
            # Not a warning: no bake source is the ordinary case for a plain
            # texturing hand-off, and a file-state note per send would be noise.
            self.logger.debug(
                "No bake source defined in this file; nothing to export. "
                "Define one with the panel's 'Set From Selection'."
            )
            return None
        members = BakeSourceSet.meshes()
        if not members:
            # A defined set that resolves to no geometry is the artist's
            # intent going unmet -- say so, rather than ship nothing quietly.
            self.logger.warning(
                "The Bake Source set holds no mesh; no bake source was exported."
            )
            return None

        # Texture embedding is for the paintable mesh; the bake source is
        # geometry only, and embedding would bloat a dense mesh for nothing.
        options = dict(fbx_options)
        options["embed_textures"] = False

        high_path = self.source_model_path_for(fbx_path)
        self.logger.info(f"Exporting bake source ({len(members)} mesh(es)) ...")
        try:
            with CoreUtils.visible_override(members):
                self._export_model(high_path, members, request, options)
        except Exception as e:  # noqa: BLE001 -- optional leg, never fatal
            self.logger.error(f"Bake-source export failed: {e}")
            return None
        self.logger.info(
            f"Bake source written: "
            f'<a href="action://open?path={high_path}">{high_path}</a>'
        )
        return high_path

    @staticmethod
    def _scene_base_name() -> str:
        """Return the current .blend's base name (no extension), or ``'untitled'``."""
        import bpy

        path = bpy.data.filepath
        if path:
            return os.path.splitext(os.path.basename(path))[0]
        return "untitled"


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    SubstanceBridge().send()
