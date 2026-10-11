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

from blendertk.core_utils._core_utils import CoreUtils
from blendertk.edit_utils._edit_utils import EditUtils
from blendertk.env_utils.fbx_utils import FbxUtils
from blendertk.env_utils.handoff_export import BlenderExportMixin
from blendertk.env_utils.usd import UsdUtils
from blendertk.mat_utils.bake_sets import BakeSourceSet
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

    #: Packed map type -> the :class:`pythontk.MapFactory` unpacker for it,
    #: with the kwargs that turn its components into what Toolbag's material
    #: slots expect (smoothness inverts into roughness on the way out).
    _UNPACKERS: Dict[str, Tuple[str, Dict[str, Any]]] = {
        "ORM": ("unpack_orm_texture", {}),
        "MRAO": ("unpack_mrao_texture", {}),
        "MSAO": (
            "unpack_msao_texture",
            {"invert_smoothness": True, "smoothness_suffix": "_Roughness"},
        ),
        "Metallic_Smoothness": (
            "unpack_metallic_smoothness",
            {"invert_smoothness": True, "smoothness_suffix": "_Roughness"},
        ),
        "Albedo_Transparency": ("unpack_albedo_transparency", {}),
    }

    #: Manifest slot -> map-type names (lowercased) an unpacked component may
    #: resolve to and satisfy that slot.
    _SLOT_ACCEPTS: Dict[str, Tuple[str, ...]] = {
        "baseColor": ("base_color", "basecolor", "albedo", "diffuse"),
        "metallic": ("metallic", "metalness"),
        "roughness": ("roughness",),
        "ambientOcclusion": ("ambient_occlusion", "ao", "mixed_ao"),
        "opacity": ("opacity", "alpha", "transparency"),
    }

    @classmethod
    def _stage_manifest_textures(
        cls, manifest: Dict[str, Any], staging_dir: str, log
    ) -> Dict[str, str]:
        """Retarget packed-map manifest slots at unpacked component files.

        The Maya materials read specific channels out of packed maps (MSAO /
        MetallicSmoothness / ORM); Toolbag's material fields sample whole
        images, so wiring the packed file verbatim feeds the wrong data to
        the surface-transfer bake. Each packed source is split once into
        *staging_dir* (smoothness channels invert into roughness) and every
        slot that referenced it is re-pointed at the matching component.

        Returns ``{material: packed_map_type}`` for every material whose
        slots read from a packed source -- the texture-map template the
        post-bake rewire restores (the baked components repack into the
        same layout the source material shipped with). Failures leave the
        original path in place -- a packed file in the slot still beats an
        empty one.
        """
        unpack_cache: Dict[str, List[str]] = {}
        packing: Dict[str, str] = {}
        retargeted = 0
        for mat_name, slots in (manifest.get("materials") or {}).items():
            for slot, path in list(slots.items()):
                accepts = cls._SLOT_ACCEPTS.get(slot)
                if not accepts or not path:
                    continue
                try:
                    map_type = ptk.MapFactory.resolve_map_type(path)
                except Exception:  # noqa: BLE001
                    continue
                spec = cls._UNPACKERS.get(map_type)
                if spec is None:
                    continue
                packing.setdefault(mat_name, map_type)
                if path not in unpack_cache:
                    unpacker, kwargs = spec
                    try:
                        produced = getattr(ptk.MapFactory, unpacker)(
                            path, output_dir=staging_dir, save=True, **kwargs
                        )
                    except Exception as e:  # noqa: BLE001
                        log.warning(
                            f"Could not unpack {map_type} map "
                            f"{os.path.basename(path)}: {e}"
                        )
                        produced = ()
                    unpack_cache[path] = [
                        str(p) for p in produced or () if p and os.path.isfile(str(p))
                    ]
                # One unpack's components are one set, read together: a set
                # named for a lobe (`Hero_Coat`) keeps its roughness a roughness.
                try:
                    ctypes = ptk.MapFactory.resolve_map_types(unpack_cache[path])
                except Exception:  # noqa: BLE001
                    ctypes = {}
                for component in unpack_cache[path]:
                    ctype = (ctypes.get(component) or "").lower()
                    if not ctype:
                        continue
                    if ctype in accepts:
                        slots[slot] = component.replace("\\", "/")
                        retargeted += 1
                        break
        if retargeted:
            log.info(
                f"Unpacked packed maps: {retargeted} material slot(s) now "
                f"point at single-channel components."
            )
        return packing

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

    def _model_writers(
        self,
    ) -> Dict[str, Callable[[str, Any, ptk.HandoffRequest], None]]:
        """``{carrier: writer(path, objects, request)}`` (mirror of mayatk's)."""
        return {"fbx": self._export_model_fbx, "usd": self._export_model_usd}

    def _export_model(self, path: str, objects, request: ptk.HandoffRequest) -> None:
        """Write *objects* to *path* in the carrier its extension names."""
        self._model_writers()[self.carrier_of(path)](path, objects, request)

    def _export_model_fbx(
        self, path: str, objects, request: ptk.HandoffRequest
    ) -> None:
        """The Toolbag-tuned kwargs plus the caller's ``fbx_options`` extra."""
        options = dict(_DEFAULT_FBX_OPTIONS)
        options.update(request.get("fbx_options") or {})
        FbxUtils.export_selection_fbx(filepath=path, objects=objects, **options)

    def _export_model_usd(
        self, path: str, objects, request: ptk.HandoffRequest
    ) -> None:
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

    @classmethod
    def source_model_path_for(cls, fbx_path: str) -> str:
        """``.../asset.fbx`` -> ``.../asset_source.fbx`` (shared convention)."""
        return BakeSourceSet.companion_path(fbx_path)

    def _split_bake_objects(self, objects) -> Tuple[List[Any], List[Any]]:
        """Split the export scope into (targets, sources) via the file's Bake Source set.

        The scoped selection is the bake *target*; the file's
        :class:`BakeSourceSet` meshes are the bake *source* and ride along
        whether or not they were selected. Any scoped object that is a set
        member (or sits under one) moves to the source side rather than
        exporting twice. Mirror of mayatk's ``_split_bake_objects``.

        Unlike Maya's, a scoped ANCESTOR of a member stays a target: Blender's
        FBX writes exactly the objects it is handed, never their subtree, so a
        parent Empty smuggles no source geometry into the target export -- and
        dropping it would re-root the target meshes it carries.
        """
        members = BakeSourceSet.members()
        if not members:
            return list(objects), []
        claimed = {
            node.name
            for member in members
            for node in [member] + list(member.children_recursive)
        }
        targets = [o for o in objects if getattr(o, "name", o) not in claimed]
        sources = BakeSourceSet.meshes()
        if not sources:
            # A set of Empties alone: the artist defined a source that holds no
            # geometry, and the send is about to pair by name instead -- say so.
            self.logger.warning(
                "The Bake Source set holds no mesh; pairing falls back to the "
                "Source/Target Suffix convention."
            )
        return targets, sources

    def _produce(self, objects, request) -> Optional[ptk.Payload]:
        """Export the model (FBX or USD) + material manifest (+ sidecars) into
        ``output_dir``.

        For the bake template, the file's :class:`BakeSourceSet` splits the
        export in two: the scoped selection becomes ``<base>.fbx`` (bake
        target) and the set's meshes a companion ``<base>_source.fbx`` (bake
        source), which the template parents into the baker's High container --
        explicit classification, no name suffixes needed. Without the set, the
        single-file suffix-pairing flow applies. Mirror of mayatk's.
        """
        output_dir = request.get("output_dir") or self._scratch_dir(request, "handoff")
        os.makedirs(output_dir, exist_ok=True)
        base = request.get("output_name") or self._scene_base_name()
        request.extras["output_dir"] = output_dir
        request.extras["output_name"] = base

        carrier = self.carrier(request).upper()
        fbx_path = os.path.join(output_dir, f"{base}{self.payload_extension(request)}")
        manifest_path = os.path.join(output_dir, f"{base}.materials.json")
        pairs_path = os.path.join(output_dir, f"{base}.bake_pairs.json")

        # What ships is the scope's hierarchy closure -- a scoped group sends
        # the subtree it names, as Maya's export-selection does, where Blender's
        # FBX writes exactly the objects it is handed (a selected Empty shipped
        # alone). Closed BEFORE the split, so a scoped group holding the Bake
        # Source set sends its other meshes as the target and the set's as the
        # companion; every sidecar below reads this same list.
        objects = BlenderExportMixin.scope_closure(objects, request.params)

        # Bake sends split the export scope via the file's Bake Source set.
        is_bake = request.template == "bake"
        # Fall back to the registry defaults for any key a programmatic caller
        # left out -- one source of truth for what "_source" is.
        pairing = {**template_params.DEFAULTS, **request.params}
        source_objects: List[Any] = []
        target_objects = list(objects)
        if is_bake:
            target_objects, source_objects = self._split_bake_objects(objects)
            # A target mesh, not merely a target: the closure's ancestors stay on
            # the target side (see _split_bake_objects), so a member under a group
            # Empty left that Empty alone -- shipped as a target with nothing on it.
            if source_objects and not any(
                getattr(o, "type", None) == "MESH" for o in target_objects
            ):
                self.logger.error(
                    "The export scope resolved to Bake Source set members only "
                    "-- there is no bake target. Select the target "
                    "geometry (the source set rides along automatically)."
                )
                return None

        self.logger.info(f"Exporting {carrier} ...")
        try:
            self._export_model(fbx_path, target_objects, request)
        except Exception as e:
            self.logger.error(f"{carrier} export failed: {e}")
            return None
        self.logger.info(
            f'{carrier} written: <a href="action://open?path={fbx_path}">{fbx_path}</a>'
        )

        source_model_path: Optional[str] = None
        if source_objects:
            source_model_path = self.source_model_path_for(fbx_path)
            self.logger.info(
                f"Exporting bake source ({len(source_objects)} Bake Source set "
                f"mesh(es)) ..."
            )
            # A bake source is usually hidden while the target is worked on,
            # and Blender's exporter drops what it cannot select (Maya's writes
            # hidden geometry verbatim): reveal the set for the write alone --
            # ``visible_override`` puts every flag and link back, and the
            # export restores the selection.
            try:
                with CoreUtils.visible_override(source_objects):
                    self._export_model(source_model_path, source_objects, request)
            except Exception as e:
                self.logger.error(f"Bake-source {carrier} export failed: {e}")
                return None
            self.logger.info(
                f"Bake source written: "
                f'<a href="action://open?path={source_model_path}">{source_model_path}</a>'
            )

        self.logger.info("Building material manifest ...")
        # The source's materials are what the surface-transfer maps sample.
        manifest = MatManifest.build(target_objects + source_objects)
        if is_bake:
            # Toolbag samples whole images per material field; packed maps
            # must be split before the surface-transfer bake reads them.
            # Staged into the run's swept scratch, never beside the .blend:
            # these are bake INPUTS, not project textures. Mirror of mayatk.
            staging_dir = self._scratch_dir(
                request, f"{ptk.StrUtils.sanitize(base, preserve_case=True)}_staging"
            )
            # The recorded packing templates let the post-bake rewire pack
            # the baked components back into the layout each source shipped.
            request.extras["source_packing"] = self._stage_manifest_textures(
                manifest, staging_dir, self.logger
            )
        with open(manifest_path, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2)
        self.logger.info(
            f"Manifest written: "
            f'<a href="action://open?path={manifest_path}">{manifest_path}</a>'
        )

        # Record which target meshes carry which material so the roundtrip
        # can put each baked texture set back where its source was.
        if is_bake:
            assignments = self._material_assignments(target_objects)
            request.extras["bake_assignments"] = assignments
            # File the maps under the SOURCE material, so a re-bake overwrites
            # its own rather than laying down a ``_BAKED``-suffixed generation
            # beside it. Computed ONCE here and carried on the request: the
            # relocation renames the files by it and the rewire buckets the
            # results by it (mirror of mayatk).
            request.extras["texture_set_aliases"] = self.texture_set_aliases(
                assignments, log=self.logger.warning
            )
            # Maps go beside the .blend rather than in with the hand-off
            # artifacts (``baked_texture_dir``) -- unless the caller named a
            # destination of its own, which always wins.
            if not request.get("texture_dir"):
                request.extras["texture_dir"] = self.baked_texture_dir()

        # Bake-pairs sidecar (single-file fallback only): Blender-side
        # parent-chain classification, written while the full hierarchy is
        # still here (Toolbag's FBX importer flattens empty parents).
        actual_pairs_path: Optional[str] = None
        cage_sources = source_objects
        cage_targets = [o for o in target_objects if getattr(o, "type", None) == "MESH"]
        if is_bake and not source_objects:
            bake_pairs = MarmosetBridge.build_bake_pairs_manifest(
                target_objects,
                pairing.get("HIGH_SUFFIX") or "",
                pairing.get("LOW_SUFFIX") or "",
                include_children=bool(pairing.get("SUFFIX_INCLUDE_CHILDREN", True)),
            )
            # The manifest walks each object's mesh descendants (mayatk's
            # contract, where the export writes the subtree); keep only what
            # this export shipped -- under Visible Only a hidden child is walked
            # but never written, and Toolbag must not be told to pair it.
            shipped = {getattr(o, "name", o) for o in target_objects}
            bake_pairs = {n: s for n, s in bake_pairs.items() if n in shipped}
            if bake_pairs:
                with open(pairs_path, "w", encoding="utf-8") as fh:
                    json.dump(bake_pairs, fh, indent=2)
                self.logger.info(
                    f"Bake-pairs sidecar written ({len(bake_pairs)} mesh(es) "
                    f"pre-classified): "
                    f'<a href="action://open?path={pairs_path}">{pairs_path}</a>'
                )
                actual_pairs_path = pairs_path
            # Same classification the Toolbag side will group by, so the cage
            # is measured for the pairs it actually gets applied to.
            cage_sources, cage_targets = self._split_by_pairs(
                target_objects, bake_pairs
            )

        # Measured cage input, passed as template params. Only for AUTO_CAGE:
        # with a hand-typed offset these would go unread, and the measurement
        # walks every source mesh's points.
        if is_bake and pairing.get("AUTO_CAGE"):
            request.params.update(self._cage_measurements(cage_sources, cage_targets))

        return ptk.Payload(
            primary=fbx_path,
            extras={
                "manifest": manifest_path,
                "pairs": actual_pairs_path,
                "source_model": source_model_path,
            },
        )

    def _deliver(self, payload, request) -> Optional[Dict[str, Any]]:
        """Run the engine hand-off, then wire bake results back into Blender.

        A bake roundtrip's outputs are production maps; with ``ASSIGN_MATERIAL``
        on (the default), each baked texture set becomes a material assigned in
        place of the source material on the bake-target meshes that wore it: the
        full loop the panel promises. Mirror of mayatk's ``_deliver``.
        """
        result = super()._deliver(payload, request)
        if (
            result
            and request.template == "bake"
            and request.mode == ROUND_TRIP
            and result.get("outputs")
            and request.params.get("ASSIGN_MATERIAL", True)
        ):
            try:
                created = self._assign_baked_materials(
                    result["outputs"],
                    request.extras.get("bake_assignments") or {},
                    strip_prefix=request.extras.get("output_name") or "",
                    source_packing=request.extras.get("source_packing") or {},
                    output_dir=result.get("texture_dir") or "",
                    aliases=request.extras.get("texture_set_aliases") or {},
                )
                if created:
                    result["materials"] = created
            except Exception:  # noqa: BLE001 -- the bake itself succeeded
                import traceback

                self.logger.error(
                    "Baked-material assignment failed:\n" + traceback.format_exc()
                )
        return result

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
        """``(sources, targets)`` mesh objects among *objects*, per *bake_pairs*.

        Reuses the sidecar's classification -- the same one the Toolbag side
        groups by -- rather than forming a second opinion that could disagree
        with the bake groups the cage is measured for. *objects* is what the
        export shipped (the scope's closure), so it is read as given: walking
        descendants again would count a mesh twice, or size the cage for one
        the export left out.
        """
        sources, targets = [], []
        for node in ptk.make_iterable(objects):
            if getattr(node, "type", None) != "MESH":
                continue
            side = bake_pairs.get(node.name)
            if side == "source":
                sources.append(node)
            elif side == "target":
                targets.append(node)
        return sources, targets

    def _cage_measurements(
        self, sources: Sequence, targets: Sequence
    ) -> Dict[str, Any]:
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
        from mathutils import Vector

        # the window layer's depsgraph: windowless, the context's is the scene
        # default layer's, which never evaluates a collection excluded there
        depsgraph = CoreUtils._evaluated_depsgraph()
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

    #: Packed map type (as recorded from the source materials) -> the
    #: GameShader.create_network flags that rebuild that layout from the
    #: baked single-channel components. Types without an entry (e.g. MRAO)
    #: fall back to wiring the separates.
    _PACKING_FLAGS: Dict[str, Dict[str, bool]] = {
        "MSAO": {"mask_map": True},
        "ORM": {"orm_map": True},
        "Metallic_Smoothness": {"metallic_smoothness": True},
        "Albedo_Transparency": {"albedo_transparency": True},
    }

    #: Suffix marking a material this bridge built from a bake.
    BAKED_SUFFIX = "_BAKED"

    @classmethod
    def source_material_name(cls, mat_name: str) -> str:
        """*mat_name* with every trailing ``_BAKED`` removed.

        The identity that survives re-baking, and the one the MAP FILES carry:
        a bake's output for a material is the same material's maps at the
        target's UV layout, whether it is the first bake or the fifth. See
        :meth:`texture_set_aliases` for why the files must not follow the
        material's ``_BAKED`` name instead.
        """
        base = ptk.StrUtils.sanitize(str(mat_name), preserve_case=True)
        while base.endswith(cls.BAKED_SUFFIX):
            base = base[: -len(cls.BAKED_SUFFIX)]
        return base

    @classmethod
    def baked_material_name(cls, mat_name: str) -> str:
        """``<source material>_BAKED``, idempotent across re-bakes.

        A re-bake reads its texture-set names off the meshes' CURRENT
        materials, which after the first roundtrip are already ``<mat>_BAKED``
        -- so appending unconditionally grew a new material per bake:
        ``mat_BAKED_BAKED_BAKED``. Stripping first
        (:meth:`source_material_name`) means the second bake reclaims the
        first material's name instead of stacking beside it.
        """
        return f"{cls.source_material_name(mat_name)}{cls.BAKED_SUFFIX}"

    @classmethod
    def texture_set_aliases(cls, materials, log=None) -> Dict[str, str]:
        """``{material: name its map files carry}`` for every renamed material.

        Toolbag names each texture set -- and so each output file -- after the
        material it baked. On a re-bake the target meshes wear ``<mat>_BAKED``,
        so without this the maps would come back as ``<mat>_BAKED_<map>.png``
        beside the first bake's ``<mat>_<map>.png``: a second generation of
        the same textures, differing only by a suffix nobody asked for, which
        is exactly the kind of thing that goes unnoticed until someone is
        cleaning up by hand. Filing them under the SOURCE material instead
        makes a re-bake overwrite its own maps in place.

        Materials that already carry their own name are omitted -- the table
        is the exceptions, not a full mapping.

        *log* is the ``warning(msg, *args)`` sink to report a dropped alias
        through. It has to be passed in: :class:`pythontk.LoggingMixin` builds
        each class a DETACHED logger (``propagate=False``, ``parent=None``), so
        a module-level warning here would never reach the panel the user is
        watching -- it would land in a logger nothing is listening to.

        An alias is DROPPED when its target name is already spoken for by
        another material in the same bake: a partial re-bake can put ``M`` and
        ``M_BAKED`` on the targets at once (bake one mesh, then bake it
        alongside a fresh one), and filing both under ``M`` would land two
        texture sets on one filename, where the second silently overwrites the
        first. Keeping the suffix on the colliding set is the "unless
        unavoidable" case, and it says so rather than losing a map.
        """
        taken = {str(m) for m in materials}
        aliases: Dict[str, str] = {}
        collided: List[str] = []
        for m in materials:
            name = str(m)
            source = cls.source_material_name(name)
            if source == name:
                continue
            if source in taken:
                collided.append(name)
                continue
            aliases[name] = source
            taken.add(source)
        if collided:
            (log or logger.warning)(
                "Map filenames would collide in this bake (%s), so %s keeps "
                "its suffixed map names -- rename the material if you want the "
                "clean name back.",
                "; ".join(
                    f"'{c}' would file under '{cls.source_material_name(c)}', "
                    "already taken"
                    for c in collided
                ),
                "each" if len(collided) > 1 else "it",
            )
        return aliases

    @staticmethod
    def _material_assignments(objects: Sequence) -> Dict[str, List[str]]:
        """``{material: [mesh object names]}`` over the mesh objects in *objects*.

        *objects* is the target export as shipped (the scope's closure), so it
        is read as given -- mayatk walks descendants because Maya's export does.
        Names, not object refs: the roundtrip blocks while Toolbag bakes, and a
        name survives the wait where a wrapper may not.
        """
        out: Dict[str, List[str]] = {}
        for obj in ptk.make_iterable(objects):
            if getattr(obj, "type", None) != "MESH":
                continue
            for slot in obj.material_slots:
                mat = slot.material
                if mat is None:
                    continue
                names = out.setdefault(mat.name, [])
                if obj.name not in names:
                    names.append(obj.name)
        return out

    def _assign_baked_materials(
        self,
        outputs: List[str],
        assignments: Dict[str, List[str]],
        strip_prefix: str = "",
        source_packing: Optional[Dict[str, str]] = None,
        output_dir: str = "",
        aliases: Optional[Dict[str, str]] = None,
    ) -> Dict[str, str]:
        """Create one material per baked texture set and put it where its source was.

        Mirror of mayatk's ``_assign_baked_materials``: the maps bucket to
        source materials by texture-set name (:meth:`_group_baked_outputs`,
        through the run's filing *aliases*), each set becomes
        :meth:`blendertk.GameShader.create_network`'s Principled network under a
        name that survives re-baking (:meth:`baked_material_name`), the
        source's packed-map layout is restored (*source_packing* ->
        :attr:`_PACKING_FLAGS`), and a map wired from the bake scratch rather
        than *output_dir* is flagged. Two Blender differences:

        - **The swap is per slot.** Each target's slots that held the source
          material take the baked one, so a mesh wearing several materials
          (several texture sets) gets each set back on the faces it came from;
          mayatk assigns the whole mesh. A target with no such slot left (its
          material was changed after the send) is assigned whole, as mayatk's.
        - **The previous bake's material is retired only once nothing wears
          it.** Blender has one PBR material type, so there is no shader type
          to preserve; but a partial re-bake leaves the earlier ``<mat>_BAKED``
          on meshes this bake did not touch, and removing it would strip them.
          It is kept (and said so) until it is unused; then it goes and the
          rebuild takes its name.

        Returns ``{source_material: created_material_name}``.
        """
        import bpy

        from blendertk.mat_utils._mat_utils import MatUtils
        from blendertk.mat_utils.game_shader import GameShader

        outputs = [str(p).replace("\\", "/") for p in outputs]
        buckets = self._group_baked_outputs(
            outputs,
            list(assignments),
            strip_prefix=strip_prefix,
            aliases=aliases or {},
            log=self.logger.warning,
        )
        if not buckets:
            self.logger.warning(
                "No baked maps could be matched to source materials; "
                "skipping material assignment."
            )
            return {}

        # Dominant packing = the fallback template for buckets whose material
        # has no recorded source of its own.
        source_packing = source_packing or {}
        default_packing: Optional[str] = None
        if source_packing:
            counts: Dict[str, int] = {}
            for ptype in source_packing.values():
                counts[ptype] = counts.get(ptype, 0) + 1
            default_packing = max(counts, key=counts.get)

        production_root = (
            os.path.normcase(os.path.abspath(output_dir)) if output_dir else ""
        )

        shader_builder = GameShader()
        shader_builder.logger.setLevel("WARNING")  # keep the panel log tight
        created: Dict[str, str] = {}
        for mat_name, textures in buckets.items():
            targets = [
                obj
                for obj in (
                    bpy.data.objects.get(n) for n in assignments.get(mat_name) or []
                )
                if obj is not None
            ]
            if not targets:
                self.logger.warning(
                    f"Baked set '{mat_name}': no surviving target meshes; "
                    f"maps left on disk unassigned."
                )
                continue
            if production_root:
                stranded = [
                    t
                    for t in textures
                    if not os.path.normcase(os.path.abspath(t)).startswith(
                        production_root + os.sep
                    )
                ]
                if stranded:
                    self.logger.warning(
                        f"Baked set '{mat_name}': {len(stranded)} map(s) are "
                        f"being wired from the bake scratch folder, not "
                        f"'{output_dir}' -- their copy could not be verified. "
                        f"That folder is swept on a later bake; copy them "
                        f"beside the .blend and re-point the image nodes:\n  "
                        + "\n  ".join(stranded)
                    )
            shader_name = self.baked_material_name(mat_name)
            # A re-bake's material is named off the PREVIOUS bake's output
            # (``mat_BAKED``), so the packing recorded against the original
            # source (``mat``) is found under the canonical name too.
            pack_type = source_packing.get(
                mat_name,
                source_packing.get(
                    shader_name[: -len(self.BAKED_SUFFIX)], default_packing
                ),
            )
            pack_flags = self._PACKING_FLAGS.get(pack_type or "", {})
            if pack_flags:
                self.logger.info(
                    f"Restoring source texture template '{pack_type}' for "
                    f"'{shader_name}' (baked components repack on wire)."
                )
            # The earlier bake's material, retired only once its replacement is
            # built AND assigned (and then only if nothing else wears it).
            previous = bpy.data.materials.get(shader_name)
            if previous is not None and previous.library is not None:
                previous = None  # a linked material is the library's, not ours
            self.logger.info(
                f"Building '{shader_name}' from {len(textures)} baked map(s) ..."
            )
            node = shader_builder.create_network(
                textures,
                name=shader_name,
                normal_type="OpenGL",
                ambient_occlusion=True,
                **pack_flags,
            )
            # Batch-shaped returns (a list) collapse to their first entry -- a
            # named create_network builds exactly one network.
            if isinstance(node, (list, tuple)):
                node = node[0] if node else None
            if node is None:
                self.logger.error(f"Material build failed for '{mat_name}'.")
                continue

            swapped = 0
            for obj in targets:
                slots = [
                    s
                    for s in obj.material_slots
                    if s.material is not None and s.material.name == mat_name
                ]
                for slot in slots:
                    slot.material = node
                    swapped += 1
                if not slots:
                    MatUtils.assign_mat([obj], node)
                    swapped += 1

            if previous is not None and previous != node:
                if previous.users:
                    self.logger.warning(
                        f"The previous bake's '{previous.name}' is still worn by "
                        f"{previous.users} other user(s), so it stays; the "
                        f"rebuild is '{node.name}'."
                    )
                else:
                    bpy.data.materials.remove(previous)
                    node.name = shader_name  # the freed name, reclaimed
                    self.logger.info(
                        f"Replaced the previous bake's '{shader_name}' "
                        f"(re-bakes overwrite rather than accumulate)."
                    )
            created[mat_name] = node.name
            self.logger.info(
                f"Assigned '{node.name}' to {len(targets)} mesh(es), "
                f"{swapped} slot(s) (was '{mat_name}')."
            )
        return created

    @staticmethod
    def _group_baked_outputs(
        outputs: List[str],
        materials: List[str],
        strip_prefix: str = "",
        aliases: Optional[Dict[str, str]] = None,
        log=None,
    ) -> Dict[str, List[str]]:
        """Bucket baked map files to source materials by texture-set naming.

        Longest match wins so ``mat`` never swallows ``mat_metal``'s files.
        With a single recorded material every output goes to it (the
        single-texture-set bake carries no set token in its filenames).
        *strip_prefix* (the output stem) is removed from each filename before
        matching so a scene name containing a material name stays inert.

        *aliases* (:meth:`texture_set_aliases`) is what each material's files
        are actually NAMED, which on a re-bake is not what the material is
        called -- match on the filed name, bucket under the material.

        *log*: as :meth:`texture_set_aliases` -- the caller's sink, because a
        module-level one cannot reach a ``LoggingMixin`` panel. A map that
        matched no material is a map that will not be wired, so that warning
        has to be somewhere the user actually sees it.
        """
        if not outputs:
            return {}
        if len(materials) == 1:
            return {materials[0]: list(outputs)}
        buckets: Dict[str, List[str]] = {}
        unmatched: List[str] = []
        aliases = aliases or {}
        # (material, token it is filed under), longest token first.
        by_len = sorted(
            ((m, aliases.get(m, m).lower()) for m in materials),
            key=lambda pair: len(pair[1]),
            reverse=True,
        )
        prefix = strip_prefix.lower()
        for path in outputs:
            stem = os.path.splitext(os.path.basename(path))[0].lower()
            if prefix and stem.startswith(prefix):
                stem = stem[len(prefix) :]
            for mat, token in by_len:
                if token in stem:
                    buckets.setdefault(mat, []).append(path)
                    break
            else:
                unmatched.append(path)
        if unmatched:
            (log or logger.warning)(
                "Baked outputs with no matching material name: %s",
                ", ".join(os.path.basename(p) for p in unmatched),
            )
        return buckets

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
