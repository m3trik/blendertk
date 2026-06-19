# !/usr/bin/python
# coding=utf-8
"""Blender-side selection + FBX-export hooks shared by the hand-off bridge engines.

:class:`BlenderExportMixin` supplies the two DCC-specific :class:`pythontk.HandoffBridge`
hooks every Blender-originating bridge shares -- read the selection and export it to
FBX (including the strip-materials path) -- so the Maya bridge, the Unity bridge, and
any future Blender->X bridge don't each re-implement them. Mirror of mayatk's
:class:`mayatk.env_utils.handoff_export.MayaExportMixin`.

``import bpy`` is deferred into the strip path so the engine surface resolves under
headless ``blender --background`` and outside Blender entirely; ``blendertk`` itself
imports Qt-free.
"""
from __future__ import annotations

from typing import Any, Dict, List

import blendertk as btk
from pythontk import Payload


class BlenderExportMixin:
    """The Blender producer hooks for hand-off bridges (``_resolve_objects`` + ``_produce``).

    Supplies the two DCC-specific :class:`pythontk.HandoffBridge` steps every
    Blender-originating bridge shares -- read the selection and produce the FBX
    :class:`pythontk.Payload` (incl. the strip-materials path). Bridges needing side
    artifacts override :meth:`_produce` and call :meth:`_export_fbx` themselves.
    Mirror of mayatk's :class:`mayatk.env_utils.handoff_export.MayaExportMixin`.
    """

    def _resolve_objects(self, objects):
        """Return the objects to export; ``None`` -> current selection."""
        if objects is None:
            objects = btk.selected_objects()
        return objects or []

    def _produce(self, objects, request) -> Payload:
        """Export the selection to a temp FBX and wrap it as a :class:`pythontk.Payload`."""
        fbx_path = self._make_payload_path()
        self._export_fbx(objects, fbx_path, request.params)
        return Payload(primary=fbx_path)

    def _fbx_options(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Blender ``export_scene.fbx`` options derived from the bridge params.

        Bridges that need a different surface override this.
        """
        return dict(
            embed_textures=bool(params.get("EMBED_TEXTURES", True)),
            path_mode=("COPY" if params.get("EMBED_TEXTURES", True) else "AUTO"),
            use_triangles=bool(params.get("TRIANGULATE", False)),
            bake_anim=bool(params.get("INCLUDE_ANIMATION", False)),
            apply_unit_scale=bool(params.get("APPLY_UNIT_SCALE", True)),
        )

    def _export_fbx(self, objects: List[Any], fbx_path: str, params: Dict[str, Any]) -> None:
        """Export *objects* to *fbx_path* with FBX options derived from *params*.

        When ``INCLUDE_MATERIALS`` is False the objects are copied (full data copy),
        their material slots cleared on the copies, the copies exported, then removed
        -- the originals and the user's selection are untouched (Blender's FBX
        exporter has no "exclude materials" flag).
        """
        fbx_opts = self._fbx_options(params)

        if bool(params.get("INCLUDE_MATERIALS", True)):
            btk.export_selection_fbx(filepath=fbx_path, objects=objects, **fbx_opts)
            return

        # Strip-materials path: export shader-less copies, leave originals alone.
        import bpy

        src = [bpy.data.objects.get(o) if isinstance(o, str) else o for o in objects]
        src = [o for o in src if o is not None]
        dups = []  # (object, copied_data)
        for o in src:
            nd = o.copy()
            copied_data = None
            if getattr(o, "data", None) is not None:
                copied_data = o.data.copy()
                nd.data = copied_data
            bpy.context.scene.collection.objects.link(nd)
            dups.append((nd, copied_data))
        try:
            for obj, _ in dups:
                data = getattr(obj, "data", None)
                if data is not None and hasattr(data, "materials"):
                    data.materials.clear()
            btk.export_selection_fbx(
                filepath=fbx_path, objects=[d[0] for d in dups], **fbx_opts
            )
        finally:
            for obj, copied_data in dups:
                try:
                    bpy.data.objects.remove(obj, do_unlink=True)
                except Exception:
                    pass
                # Drop the orphaned copied datablock so the strip leaves no residue.
                if copied_data is not None and getattr(copied_data, "users", 0) == 0:
                    for coll in (
                        getattr(bpy.data, "meshes", None),
                        getattr(bpy.data, "curves", None),
                    ):
                        try:
                            if coll is not None and copied_data.name in coll:
                                coll.remove(copied_data)
                                break
                        except Exception:
                            pass
