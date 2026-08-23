# !/usr/bin/python
# coding=utf-8
"""Blender-side glue for the Marmoset Toolbag engine -- mirror of mayatk's
``mat_utils.marmoset_bridge._marmoset_bridge``.

:class:`MarmosetBridge` is the Blender half of the split: a :class:`pythontk.HandoffBridge`
whose ``_produce`` exports the current selection to FBX, builds a :class:`blendertk.mat_utils.
mat_manifest.MatManifest` sidecar and a Blender-hierarchy-classified source/target bake-pairs sidecar,
and whose **deliverer** is the DCC-agnostic :class:`._marmoset_engine.MarmosetEngine` (a
:class:`pythontk.Deliverer`) that renders the Toolbag template and launches / round-trips
Toolbag.

Everything Marmoset-specific but DCC-agnostic (Toolbag discovery/launch, log handling, template
rendering, the in-Toolbag helpers, the RPC client) is vendored alongside this module in the
``marmoset_bridge`` subpackage -- an identical copy to mayatk's, per the established pattern
(the standalone extapps ``marmoset_workflow`` panel keeps its own copy too, since none of the
three can import each other). This module owns only what genuinely needs Blender.

``import bpy`` is deferred so the engine surface resolves headlessly.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import pythontk as ptk

from blendertk.mat_utils.marmoset_bridge._marmoset_engine import (
    MarmosetEngine,
    ROUND_TRIP,
)

# Re-exported so the slots/tests can ``from ._marmoset_bridge import SEND_TO, _TEMPLATE_DIR``
# (mirror of mayatk's _marmoset_bridge). Without these, marmoset_bridge_slots.py fails to import
# → MarmosetBridgeSlots never registers → discovery falls back to the engine class.
from blendertk.mat_utils.marmoset_bridge._marmoset_engine import (  # noqa: F401
    SEND_TO,
    _TEMPLATE_DIR,
)

# Sibling module, imported relatively (as the engine does) so this module
# never re-enters its own subpackage during import.
from . import template_params

from blendertk.edit_utils._edit_utils import EditUtils
from blendertk.env_utils.fbx_utils import FbxUtils
from blendertk.env_utils.usd import UsdUtils
from blendertk.mat_utils.mat_manifest import MatManifest
from ._marmoset_engine import APP

logger = logging.getLogger(__name__)

# FBX options tuned for Marmoset Toolbag (Blender-native ``export_scene.fbx`` kwargs -- the
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
}

# USD options tuned for Marmoset Toolbag (the USD carrier): the shared interchange
# set (UsdPreviewSurface is all Toolbag reads), geometry only like the FBX set.
# Mirror of mayatk's ``_DEFAULT_USD_OPTIONS`` intent in Blender's own kwargs.
_DEFAULT_USD_OPTIONS: Dict[str, Any] = dict(
    UsdUtils.INTERCHANGE_EXPORT_OPTIONS,
    export_armatures=False,
    export_shapekeys=False,
)


class _MarmosetBridgeInternal(object):
    """Internal helpers for MarmosetBridge."""

    @staticmethod
    def _classify_blender_chain(
        obj, high_suffix: str, low_suffix: str, include_children: bool = True
    ) -> Optional[str]:
        """Walk *obj*'s parent chain in Blender, return ``'source'``/``'target'``/None.

        Mirrors the Toolbag-side ``_classify_by_chain`` in :mod:`._toolbag_helpers`, but operates on
        the live Blender object hierarchy via ``obj.parent`` -- so we can run it BEFORE the FBX
        export flattens it. Mirror of mayatk's ``_classify_maya_chain``. *include_children* off
        stops the walk at the object itself, so a suffixed parent no longer tags its children.
        """
        cur = obj
        visited = 0
        while cur is not None and visited < 64:
            stem = cur.name
            if high_suffix and stem.endswith(high_suffix):
                return "source"
            if low_suffix and stem.endswith(low_suffix):
                return "target"
            if not include_children:
                break
            cur = cur.parent
            visited += 1
        return None


class MarmosetBridge(ptk.HandoffBridge, _MarmosetBridgeInternal):
    """Export the Blender selection to Marmoset Toolbag with templated automation.

    A :class:`pythontk.HandoffBridge` whose ``_produce`` exports the selection to FBX with a
    :class:`MatManifest` sidecar and a bake-pairs sidecar, and whose deliverer is the
    DCC-agnostic :class:`MarmosetEngine` (renders the Toolbag template + launches /
    round-trips). Mirror of mayatk's ``MarmosetBridge``.

    Usage::

        MarmosetBridge().send(template="bake", mode="round_trip")
        MarmosetBridge().send(template="lookdev")  # mode defaults to send_to
    """

    #: Executable discovery for this bridge's target app (:class:`pythontk.AppSpec`),
    #: re-exposed from the engine module so callers reach it through the class
    #: namespace: a panel's ``*_init`` gates its launch button on
    #: ``<Bridge>.APP.available`` and shows ``APP.not_found_message`` when unmet.
    APP = APP

    #: Namespace for this bridge's temp payload + run scratch
    #: (``<temp>/blender_marmoset_bridge_*``), and the scope its stale-leftover
    #: sweep runs over.
    payload_prefix = "blender_marmoset_bridge"

    #: Furthest source standoff, as a fraction of the target diagonal, at or
    #: below which the source IS the target surface (see _cage_measurements).
    #: Mirror of mayatk.
    COINCIDENT_FRACTION = 1e-4

    #: A ROUNDTRIP consumes what it stages: Toolbag runs BLOCKING and its only
    #: durable output -- the maps -- is relocated beside the .blend, so the FBX
    #: hand-off, the manifest, the bake-pairs sidecar, the rendered script and
    #: the saved ``.tbscene`` are all intermediates of the run itself and go to
    #: a scratch dir it then removes. A ``send_to`` launches a DETACHED Toolbag
    #: that reads those files after we return, so no delete is safe there.
    #: Mirror of mayatk. Panel-side twin: ``TRANSIENT_OUTPUT_MODES``.
    scoped_scratch_modes = (ROUND_TRIP,)

    def __init__(self, toolbag_path: Optional[str] = None):
        super().__init__()
        self.deliverer = MarmosetEngine(toolbag_path)
        # The panel redirects only the bridge's logger (`BridgeSlotsBase`); route the engine's
        # delivery-phase output through the SAME logger so it reaches the log panel.
        self.deliverer.logger = self.logger

    @property
    def toolbag_path(self) -> Optional[str]:
        return self.deliverer.toolbag_path

    @toolbag_path.setter
    def toolbag_path(self, value: Optional[str]) -> None:
        self.deliverer.toolbag_path = value

    def params_defaults(self) -> Dict[str, Any]:
        from blendertk.mat_utils.marmoset_bridge import parameters as _params

        return _params.Parameters.defaults()

    def render_template(self, *args, **kwargs) -> Optional[str]:
        """Render a Toolbag script body (delegates to the engine deliverer)."""
        return self.deliverer.render_template(*args, **kwargs)

    # ------------------------------------------------------------------ hooks
    def _resolve_objects(self, objects):
        """Return the objects to export; ``None`` -> current selection."""
        import blendertk as btk

        if not objects:
            objects = btk.selected_objects()
        return objects or []

    #: Subfolder next to the saved ``.blend`` that bake roundtrips write their
    #: maps into -- blendertk's own convention (``TextureBaker`` uses the same
    #: name), and the mirror of mayatk's ``sourceimages/baked``. A subfolder,
    #: not the .blend dir itself: a bake's output for material ``M`` carries
    #: the SAME ``<material>_<map>`` name as the source maps that fed it.
    BAKED_TEXTURE_SUBDIR = "baked_textures"

    @classmethod
    def baked_texture_dir(cls) -> str:
        """``<blend dir>/baked_textures`` -- where a roundtrip's maps land.

        Baked maps are production textures the .blend's materials reference,
        so they belong beside it rather than in with the transient hand-off
        artifacts. Returns ``""`` when the .blend is unsaved (no directory to
        be beside); the engine then falls back to the run's ``output_dir``.
        Mirror of mayatk's ``MarmosetBridge.baked_texture_dir``.
        """
        import bpy

        from blendertk.mat_utils.texture_baker import TextureBaker

        if not bpy.data.filepath:
            return ""
        return TextureBaker.default_output_dir(cls.BAKED_TEXTURE_SUBDIR).replace(
            "\\", "/"
        )

    # Both carriers: Toolbag imports USD (UsdPreviewSurface) beside FBX. Flat is
    # fine here -- a bake wants every duplicate's own textures anyway, and
    # nothing comes back as geometry. Mirror of mayatk's.
    carriers = ("fbx", "usd")
    usd_flattens_instances = True

    def _model_writers(self) -> Dict[str, Callable[[str, Any, ptk.HandoffRequest], None]]:
        """``{carrier: writer(path, objects, request)}`` (mirror of mayatk's)."""
        return {"fbx": self._export_model_fbx, "usd": self._export_model_usd}

    def _export_model(self, path: str, objects, request: ptk.HandoffRequest) -> None:
        """Write *objects* to *path* in the carrier its extension names."""
        self._model_writers()[self.carrier_of(path)](path, objects, request)

    def _export_model_fbx(self, path: str, objects, request: ptk.HandoffRequest) -> None:
        """The Toolbag-tuned kwargs plus the caller's ``fbx_options`` extra."""
        options = dict(_DEFAULT_FBX_OPTIONS)
        options.update(request.get("fbx_options") or {})
        FbxUtils.export_selection_fbx(filepath=path, objects=objects, **options)

    def _export_model_usd(self, path: str, objects, request: ptk.HandoffRequest) -> None:
        """The Toolbag USD set plus the ``usd_options`` extra -- flat, with a
        warning when the set holds linked duplicates (each bakes as its own mesh)."""
        import bpy

        shared = {}
        for o in objects:
            obj = bpy.data.objects.get(o) if isinstance(o, str) else o
            data = getattr(obj, "data", None)
            if obj is not None and data is not None and obj.type == "MESH":
                shared.setdefault(data.name, []).append(obj.name)
        linked = {m: n for m, n in shared.items() if len(n) > 1}
        if linked:
            self.logger.warning(
                f"USD carrier: {len(linked)} shared mesh(es) are flattened for "
                "this hand-off (each duplicate bakes as its own mesh)."
            )
        options = dict(_DEFAULT_USD_OPTIONS)
        options.update(request.get("usd_options") or {})
        UsdUtils.export_selection_usd(filepath=path, objects=objects, **options)

    def _produce(self, objects, request) -> Optional[ptk.Payload]:
        """Export the model (FBX or USD) + material manifest (+ bake-pairs sidecar)
        into ``output_dir``."""
        output_dir = request.get("output_dir") or self._scratch_dir(request, "handoff")
        os.makedirs(output_dir, exist_ok=True)
        base = request.get("output_name") or self._scene_base_name()
        request.extras["output_dir"] = output_dir
        request.extras["output_name"] = base

        carrier = self.carrier(request).upper()
        fbx_path = os.path.join(output_dir, f"{base}{self.payload_extension(request)}")
        manifest_path = os.path.join(output_dir, f"{base}.materials.json")
        pairs_path = os.path.join(output_dir, f"{base}.bake_pairs.json")

        self.logger.info(f"Exporting {carrier} ...")
        try:
            self._export_model(fbx_path, objects, request)
        except Exception as e:
            self.logger.error(f"{carrier} export failed: {e}")
            return None
        self.logger.info(
            f'{carrier} written: <a href="action://open?path={fbx_path}">{fbx_path}</a>'
        )

        self.logger.info("Building material manifest ...")
        manifest = MatManifest.build(objects)
        with open(manifest_path, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2)
        self.logger.info(
            f"Manifest written: "
            f'<a href="action://open?path={manifest_path}">{manifest_path}</a>'
        )

        # Maps go beside the .blend rather than in with the hand-off
        # artifacts (``baked_texture_dir``) -- unless the caller named a
        # destination of its own, which always wins.
        if request.template == "bake" and not request.get("texture_dir"):
            request.extras["texture_dir"] = self.baked_texture_dir()

        # Fall back to the registry defaults for any key a programmatic caller
        # left out -- one source of truth for what "_source" is.
        pairing = {**template_params.DEFAULTS, **request.params}
        bake_pairs = MarmosetBridge.build_bake_pairs_manifest(
            objects,
            pairing.get("HIGH_SUFFIX") or "",
            pairing.get("LOW_SUFFIX") or "",
            include_children=bool(pairing.get("SUFFIX_INCLUDE_CHILDREN", True)),
        )
        actual_pairs_path: Optional[str] = None
        if bake_pairs:
            with open(pairs_path, "w", encoding="utf-8") as fh:
                json.dump(bake_pairs, fh, indent=2)
            self.logger.info(
                f"Bake-pairs sidecar written ({len(bake_pairs)} mesh(es) "
                f"pre-classified): "
                f'<a href="action://open?path={pairs_path}">{pairs_path}</a>'
            )
            actual_pairs_path = pairs_path

        # Measured cage input, passed as template params. Only for AUTO_CAGE:
        # with a hand-typed offset these would go unread, and the measurement
        # walks every source mesh's points.
        if pairing.get("AUTO_CAGE"):
            request.params.update(
                self._cage_measurements(*self._split_by_pairs(objects, bake_pairs))
            )

        return ptk.Payload(
            primary=fbx_path,
            extras={"manifest": manifest_path, "pairs": actual_pairs_path},
        )

    def _delivered_paths(self, result):
        """The maps, and the folder they were destined for.

        With the .blend unsaved ``baked_texture_dir`` is empty and the engine
        writes the maps into the run's own output dir -- which for a scratch
        run IS the scratch, so this is what stops the cleanup from taking the
        bake with it.
        """
        delivered = list((result or {}).get("outputs") or [])
        delivered.append((result or {}).get("texture_dir"))
        return delivered

    @staticmethod
    def _split_by_pairs(objects: Sequence, bake_pairs: Dict[str, str]) -> Tuple:
        """``(sources, targets)`` mesh objects under *objects*, per *bake_pairs*.

        Reuses the sidecar's classification -- the same one the Toolbag side
        groups by -- rather than forming a second opinion that could disagree
        with the bake groups the cage is measured for.
        """
        sources, targets = [], []
        for obj in ptk.make_iterable(objects):
            for node in [obj] + list(getattr(obj, "children_recursive", []) or []):
                if getattr(node, "type", None) != "MESH":
                    continue
                side = bake_pairs.get(node.name)
                if side == "source":
                    sources.append(node)
                elif side == "target":
                    targets.append(node)
        return sources, targets

    def _cage_measurements(self, sources: Sequence, targets: Sequence) -> Dict[str, Any]:
        """Measure what the auto cage needs -- mirror of mayatk's ``_cage_measurements``.

        The cage has to travel from the bake target out past the source's
        FURTHEST point, and only a closest-point query can say how far that is
        -- a source standing off an INTERIOR target surface (a light fixture
        under a ceiling, a door inset in its opening) sits wholly inside the
        target's bounding box, so every box-derived estimate reads zero for it.
        Blender has the acceleration structure for the real query; Toolbag
        exposes no such call, which is why it happens here.

        The diagonal rides along so the Toolbag side can convert these
        host-unit distances into its own (see ``_unit_scale`` in
        ``templates/bake.py``). Returns an empty mapping when there is nothing
        to measure; the template falls back to its bounds estimate.
        """
        if not (sources and targets):
            return {}
        try:
            distances = EditUtils.get_standoff_distances(sources, targets)
            lo, hi = self._world_bounds(targets)
        except Exception as e:  # noqa: BLE001
            self.logger.warning(
                f"Could not measure the bake cage ({e}); Toolbag will estimate "
                f"it from the imported bounds instead."
            )
            return {}
        if not distances or lo is None:
            return {}

        diagonal = sum((hi[i] - lo[i]) ** 2 for i in range(3)) ** 0.5
        furthest = max(distances.items(), key=lambda kv: kv[1])
        self.logger.info(
            f"Cage measured over {len(distances)} source mesh(es): the furthest "
            f"stands {furthest[1]:.4g} off the bake target ({furthest[0]})."
        )
        if furthest[1] <= diagonal * self.COINCIDENT_FRACTION:
            # Every source point lies ON the target: the same surface twice (a
            # UV re-layout / material consolidation), not a high->low bake. A
            # ray-cast bake bleeds wherever the mesh touches itself and no cage
            # value can fix a contact region; that job is the UV Transfer tool.
            self.logger.warning(
                "Bake source and target are COINCIDENT (furthest source point "
                f"{furthest[1]:.4g} off the target, {len(distances)} mesh(es)). "
                "A ray-cast bake bleeds wherever the mesh touches itself and "
                "no cage offset can prevent it; for a UV re-layout use "
                "UV > Transfer Textures (blendertk TextureTransfer) instead."
            )
        return {"CAGE_STANDOFFS": dict(distances), "CAGE_HOST_DIAGONAL": diagonal}

    @staticmethod
    def _world_bounds(objects: Sequence) -> Tuple:
        """World-space AABB over *objects* as ``(min_xyz, max_xyz)``, or ``(None, None)``.

        Depsgraph-evaluated, matching ``get_standoff_distances``: this diagonal
        is compared against Toolbag's measurement of the SAME target to convert
        units, and Toolbag sees the post-modifier mesh the FBX carried. Reading
        the base ``bound_box`` off a modifier-carrying target would invent a
        scale factor and rescale every standoff by it.
        """
        import bpy
        from mathutils import Vector

        depsgraph = bpy.context.evaluated_depsgraph_get()
        lo = [None, None, None]
        hi = [None, None, None]
        for obj in objects:
            obj = obj.evaluated_get(depsgraph)
            mw = obj.matrix_world
            for corner in getattr(obj, "bound_box", ()) or ():
                world = mw @ Vector(corner)
                for i in range(3):
                    lo[i] = world[i] if lo[i] is None else min(lo[i], world[i])
                    hi[i] = world[i] if hi[i] is None else max(hi[i], world[i])
        return (None, None) if lo[0] is None else (lo, hi)

    @staticmethod
    def _scene_base_name() -> str:
        """Return the current .blend's base name (no extension), or ``'untitled'``."""
        import bpy

        path = bpy.data.filepath
        if path:
            return os.path.splitext(os.path.basename(path))[0]
        return "untitled"

    @staticmethod
    def build_bake_pairs_manifest(
        objects: Sequence,
        high_suffix: str,
        low_suffix: str,
        include_children: bool = True,
    ) -> Dict[str, str]:
        """Build the ``{mesh_name: 'source'|'target'}`` sidecar for the bake -- mirror of mayatk's
        ``build_bake_pairs_manifest`` (Blender parent-chain walk instead of a Maya DAG walk).

        For each selected object, finds every mesh-type descendant (recursively, plus the object
        itself if it's a mesh), walks its parent chain, and records a classification if any ancestor
        (or the mesh itself) carries *high_suffix* or *low_suffix*. With *include_children* off only
        the mesh's own name is consulted.
        """
        if not (high_suffix or low_suffix):
            return {}

        def _mesh_descendants(o):
            found = [o] if o.type == "MESH" else []
            for child in o.children:
                found.extend(_mesh_descendants(child))
            return found

        visited = set()
        mesh_objs: List[Any] = []
        for obj in objects:
            for x in _mesh_descendants(obj):
                if x.name not in visited:
                    visited.add(x.name)
                    mesh_objs.append(x)

        out: Dict[str, str] = {}
        for mesh_obj in mesh_objs:
            cls = _MarmosetBridgeInternal._classify_blender_chain(
                mesh_obj, high_suffix, low_suffix, include_children
            )
            if cls:
                out[mesh_obj.name] = cls
        return out


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    bridge = MarmosetBridge()
    bridge.send(template="bake", mode=ROUND_TRIP)
