# !/usr/bin/python
# coding=utf-8
"""External auto-unwrap round-trip: OBJ out, engine, OBJ back, UVs transferred.

Drives :class:`pythontk.UvUnwrap` (Ministry of Flat / Boundary First
Flattening) from Blender. Reached through :meth:`blendertk.UvUtils.auto_unwrap`;
nothing here is called directly. Mirror of mayatk's module of the same name.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import pythontk as ptk

from blendertk.core_utils._core_utils import CoreUtils


@dataclass
class AutoUnwrapResult:
    """Per-object outcome of an :meth:`auto_unwrap` run."""

    engine: str
    succeeded: List[str] = field(default_factory=list)
    failed: List[Tuple[str, str]] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.succeeded)


class _AutoUnwrapInternal:
    """Round-trip mechanics for :meth:`blendertk.UvUtils.auto_unwrap`."""

    # Every engine call funnels through here so tests can substitute a stub
    # without needing the real executables.
    @staticmethod
    def _engine_unwrap(obj_in: str, engine: str, **params) -> str:
        return ptk.UvUnwrap.unwrap(obj_in, engine=engine, **params)

    @staticmethod
    def _check_engine(engine: str) -> str:
        """Resolve the executable up front, before the scene is touched."""
        return ptk.UvUnwrap.resolve_engine(engine, required=True)

    @staticmethod
    def _resolve_meshes(objects) -> List[Any]:
        """Argument-or-selection to unique mesh objects, caller order kept.

        Objects sharing a mesh datablock collapse to one representative --
        unwrapping it updates every linked duplicate.
        """
        from blendertk.edit_utils._edit_utils import EditUtils

        if objects is None:
            objects = list(CoreUtils.selected_objects())
        meshes, seen = [], set()
        for obj in EditUtils._meshes(objects):
            if obj.data.name in seen:
                continue
            seen.add(obj.data.name)
            meshes.append(obj)
        return meshes

    @staticmethod
    def _export_obj(obj, path: str) -> None:
        """Write *obj* alone to a Wavefront OBJ.

        Modifiers are deliberately not applied: the export must carry the base
        topology so the engine's result comes back loop-for-loop identical.
        """
        import bpy

        with CoreUtils.window_context_override():
            for other in bpy.context.view_layer.objects:
                other.select_set(False)
            obj.select_set(True)
            bpy.context.view_layer.objects.active = obj
            bpy.ops.wm.obj_export(
                filepath=path,
                export_selected_objects=True,
                export_materials=False,
                export_normals=True,
                export_uv=True,
                apply_modifiers=False,
                export_triangulated_mesh=False,
            )

    @staticmethod
    def _import_obj(path: str) -> List[Any]:
        """Import *path*; return the objects it created.

        Identified by diffing ``bpy.data.objects`` rather than trusting the
        importer's selection, which the Qt-pump context can leave unset.
        """
        import bpy

        before = set(bpy.data.objects)
        with CoreUtils.window_context_override():
            bpy.ops.wm.obj_import(filepath=path)
        return [o for o in bpy.data.objects if o not in before]

    @staticmethod
    def _remove(objects) -> None:
        """Delete imported objects and any mesh data they leave orphaned."""
        import bpy

        for obj in objects:
            data = obj.data if getattr(obj, "type", None) == "MESH" else None
            try:
                bpy.data.objects.remove(obj, do_unlink=True)
            except (ReferenceError, RuntimeError):
                continue
            if data is not None and data.users == 0:
                try:
                    bpy.data.meshes.remove(data)
                except (ReferenceError, RuntimeError):
                    pass

    @classmethod
    def run(
        cls,
        uv_utils,
        objects=None,
        method: str = "hard",
        map_size: int = 4096,
        pack: Optional[bool] = None,
        orient: bool = True,
        engine_params: Optional[Dict[str, Any]] = None,
    ) -> AutoUnwrapResult:
        """Unwrap each mesh through an external engine. See ``UvUtils.auto_unwrap``."""
        import bpy

        engine = ptk.UvUnwrap.resolve_method(method)
        meshes = cls._resolve_meshes(objects)
        if not meshes:
            raise ValueError("auto_unwrap: no mesh objects given or selected.")

        # Resolve the executable before anything mutates the scene, so a
        # missing engine surfaces as a clean error rather than a half-run.
        cls._check_engine(engine)

        params = dict(engine_params or {})
        if engine == "mof":
            # Ministry of Flat derives its island gutter from this.
            params.setdefault("resolution", map_size)
        layout = cls._layout_mode(engine, pack)

        result = AutoUnwrapResult(engine=engine)
        prior_active = bpy.context.view_layer.objects.active
        prior_mode = getattr(prior_active, "mode", "OBJECT") if prior_active else "OBJECT"
        prior_selection = [o for o in bpy.context.view_layer.objects if o.select_get()]
        try:
            with CoreUtils.undo_chunk(f"Auto Unwrap ({engine})"):
                cls._ensure_object_mode()
                with ptk.TempArtifacts("uv_unwrap", policy="scoped") as tmp:
                    for mesh in meshes:
                        cls._unwrap_one(
                            uv_utils, mesh, engine, params, map_size, layout,
                            orient, tmp, result,
                        )
        finally:
            cls._restore_context(prior_active, prior_mode, prior_selection)
        return result

    @staticmethod
    def _layout_mode(engine: str, pack: Optional[bool]) -> str:
        """What to do with the engine's UVs: ``"pack"`` / ``"fit"`` / ``"none"``.

        The default follows the engine: one that arranges its own islands keeps
        that arrangement and is only scaled into the tile (Ministry of Flat packs
        into a rectangle that overruns 0-1); one that only flattens gets the full
        pack.
        """
        if pack is not None:
            return "pack" if pack else "none"
        return "fit" if ptk.UvUnwrap.ENGINES[engine].packs_own_layout else "pack"

    @staticmethod
    def _ensure_object_mode() -> None:
        import bpy

        try:
            if bpy.context.object and bpy.context.object.mode != "OBJECT":
                with CoreUtils.window_context_override():
                    bpy.ops.object.mode_set(mode="OBJECT")
        except RuntimeError:
            pass

    @staticmethod
    def _restore_context(prior_active, prior_mode, prior_selection) -> None:
        import bpy

        try:
            for obj in bpy.context.view_layer.objects:
                obj.select_set(False)
            for obj in prior_selection:
                try:
                    obj.select_set(True)
                except ReferenceError:
                    pass
            if prior_active is not None:
                bpy.context.view_layer.objects.active = prior_active
                if prior_mode != "OBJECT":
                    with CoreUtils.window_context_override():
                        bpy.ops.object.mode_set(mode=prior_mode)
        except (ReferenceError, RuntimeError):
            pass

    @classmethod
    def _unwrap_one(
        cls, uv_utils, mesh, engine, params, map_size, layout, orient, tmp, result
    ) -> None:
        """Round-trip one mesh, recording success or an isolated failure."""
        snapshot = uv_utils.get_uv_coords([mesh])
        imported: List[Any] = []
        try:
            payload = tmp.path(extension=".obj")
            cls._export_obj(mesh, payload)
            unwrapped = cls._engine_unwrap(payload, engine, **params)
            tmp.register(unwrapped)

            imported = cls._import_obj(unwrapped)
            source = next((o for o in imported if o.type == "MESH"), None)
            if source is None:
                raise RuntimeError("engine output contained no mesh")
            if not source.data.uv_layers:
                raise RuntimeError("engine output contained no UVs")

            # Both engines return the input topology untouched, so the loop
            # counts line up and UVs copy across exactly.
            uv_utils.transfer_uvs(source, mesh, match_by_similarity=False)
            if layout == "pack":
                uv_utils._pack_shells(mesh, map_size=map_size, orient=orient)
            elif layout == "fit":
                # Keep the engine's own island arrangement, just scale it into
                # the tile -- Ministry of Flat packs into a rectangle that
                # routinely overruns 0-1.
                uv_utils._fit_uvs_to_tile(mesh)
        except Exception as error:  # noqa: BLE001 - one bad mesh must not stop the rest
            uv_utils.set_uv_coords([mesh], snapshot)
            result.failed.append((mesh.name, str(error)))
        else:
            result.succeeded.append(mesh.name)
        finally:
            cls._remove(imported)
