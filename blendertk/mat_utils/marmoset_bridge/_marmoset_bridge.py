# !/usr/bin/python
# coding=utf-8
"""Blender-side glue for the Marmoset Toolbag engine -- mirror of mayatk's
``mat_utils.marmoset_bridge._marmoset_bridge``.

:class:`MarmosetBridge` is the Blender half of the split: a :class:`pythontk.HandoffBridge`
whose ``_produce`` exports the current selection to FBX, builds a :class:`blendertk.mat_utils.
mat_manifest.MatManifest` sidecar and a Blender-hierarchy-classified high/low bake-pairs sidecar,
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
import tempfile
from typing import Any, Dict, List, Optional, Sequence

import pythontk as ptk

from blendertk.mat_utils.marmoset_bridge._marmoset_engine import (  # noqa: F401
    MarmosetEngine,
    SEND_TO,
    ROUNDTRIP,
    _TEMPLATE_DIR,
    list_templates,
    template_modes,
    list_template_modes,
)

from blendertk.env_utils.fbx_utils import export_selection_fbx
from blendertk.mat_utils.mat_manifest import MatManifest

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


def _classify_blender_chain(
    obj, high_suffix: str, low_suffix: str
) -> Optional[str]:
    """Walk *obj*'s parent chain in Blender, return ``'high'``/``'low'``/None.

    Mirrors the Toolbag-side ``_classify_by_chain`` in :mod:`._toolbag_helpers`, but operates on
    the live Blender object hierarchy via ``obj.parent`` -- so we can run it BEFORE the FBX
    export flattens it. Mirror of mayatk's ``_classify_maya_chain``.
    """
    cur = obj
    visited = 0
    while cur is not None and visited < 64:
        stem = cur.name
        if high_suffix and stem.endswith(high_suffix):
            return "high"
        if low_suffix and stem.endswith(low_suffix):
            return "low"
        cur = cur.parent
        visited += 1
    return None


def build_bake_pairs_manifest(
    objects: Sequence, high_suffix: str, low_suffix: str
) -> Dict[str, str]:
    """Build the ``{mesh_name: 'high'|'low'}`` sidecar for the bake -- mirror of mayatk's
    ``build_bake_pairs_manifest`` (Blender parent-chain walk instead of a Maya DAG walk).

    For each selected object, finds every mesh-type descendant (recursively, plus the object
    itself if it's a mesh), walks its parent chain, and records a classification if any ancestor
    (or the mesh itself) carries *high_suffix* or *low_suffix*.
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
        cls = _classify_blender_chain(mesh_obj, high_suffix, low_suffix)
        if cls:
            out[mesh_obj.name] = cls
    return out


class MarmosetBridge(ptk.HandoffBridge):
    """Export the Blender selection to Marmoset Toolbag with templated automation.

    A :class:`pythontk.HandoffBridge` whose ``_produce`` exports the selection to FBX with a
    :class:`MatManifest` sidecar and a bake-pairs sidecar, and whose deliverer is the
    DCC-agnostic :class:`MarmosetEngine` (renders the Toolbag template + launches /
    round-trips). Mirror of mayatk's ``MarmosetBridge``.

    Usage::

        MarmosetBridge().send(template="bake", mode="roundtrip")
        MarmosetBridge().send(template="lookdev")  # mode defaults to send_to
    """

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

        return _params.defaults()

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

    def _produce(self, objects, request) -> Optional[ptk.Payload]:
        """Export the FBX + material manifest (+ bake-pairs sidecar) into ``output_dir``."""
        output_dir = request.get("output_dir") or os.path.join(
            tempfile.gettempdir(), "blender_marmoset_bridge"
        )
        os.makedirs(output_dir, exist_ok=True)
        base = request.get("output_name") or self._scene_base_name()
        request.extras["output_dir"] = output_dir
        request.extras["output_name"] = base

        fbx_path = os.path.join(output_dir, f"{base}.fbx")
        manifest_path = os.path.join(output_dir, f"{base}.materials.json")
        pairs_path = os.path.join(output_dir, f"{base}.bake_pairs.json")

        merged_options = dict(_DEFAULT_FBX_OPTIONS)
        if request.get("fbx_options"):
            merged_options.update(request.get("fbx_options"))

        self.logger.info("Exporting FBX ...")
        try:
            export_selection_fbx(filepath=fbx_path, objects=objects, **merged_options)
        except Exception as e:
            self.logger.error(f"FBX export failed: {e}")
            return None
        self.logger.info(
            f'FBX written: <a href="action://open?path={fbx_path}">{fbx_path}</a>'
        )

        self.logger.info("Building material manifest ...")
        manifest = MatManifest.build(objects)
        with open(manifest_path, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2)
        self.logger.info(
            f'Manifest written: '
            f'<a href="action://open?path={manifest_path}">{manifest_path}</a>'
        )

        _high_suffix = request.params.get("HIGH_SUFFIX", "_high") or ""
        _low_suffix = request.params.get("LOW_SUFFIX", "_low") or ""
        bake_pairs = build_bake_pairs_manifest(objects, _high_suffix, _low_suffix)
        actual_pairs_path: Optional[str] = None
        if bake_pairs:
            with open(pairs_path, "w", encoding="utf-8") as fh:
                json.dump(bake_pairs, fh, indent=2)
            self.logger.info(
                f"Bake-pairs sidecar written ({len(bake_pairs)} mesh(es) "
                f'pre-classified): '
                f'<a href="action://open?path={pairs_path}">{pairs_path}</a>'
            )
            actual_pairs_path = pairs_path

        return ptk.Payload(
            primary=fbx_path,
            extras={"manifest": manifest_path, "pairs": actual_pairs_path},
        )

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
    bridge = MarmosetBridge()
    bridge.send(template="bake", mode=ROUNDTRIP)
