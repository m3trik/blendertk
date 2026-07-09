# !/usr/bin/python
# coding=utf-8
"""Logic for separating and reassembling mesh assemblies (bpy adapter).

The clustering itself (same-material touch graph, GCD count splits,
distance-consistency assignment, orphan recovery, cross-copy support gate)
is the shared DCC-neutral ``pythontk.AssemblySorter`` — this module extracts
the per-part features from bpy and turns the returned index groups into
scene structure. Maya→Blender mappings:

- ``polySeparate``           → ``EditUtils.separate_objects`` (LOOSE). The
  original object keeps one shell (no shapeless husk is left behind, so
  ``cleanup_empty_sources`` is a no-op kept for API parity).
- Maya group nodes           → Empties carrying the ``autoInstancerAssembly``
  custom property.
- ``polyUnite``              → ``bpy.ops.object.join`` (single-user guard —
  join chokes on multi-user data).
- UUID tracking              → object names + ``session_uid``.
- instanced-shape guard      → shared mesh datablock (``_object_users > 1``).
- locked-normal preservation → custom split normals captured in world space
  and re-set after the transform edit.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple
from collections import defaultdict

import numpy as np

import pythontk as ptk

from blendertk.core_utils._core_utils import _object_mode
from blendertk.core_utils.auto_instancer.geometry_matcher import GeometryMatcher, _mesh
from blendertk.node_utils._node_utils import _object_users, reparent

logger = logging.getLogger(__name__)

# Custom property stamped on assembly groups this tool creates, so later
# passes never mistake a user's own ``Assembly_*``-named object for ours.
ASSEMBLY_TAG_ATTR = "autoInstancerAssembly"


def _alive(obj) -> bool:
    """True when *obj* is a live object still registered in ``bpy.data``."""
    import bpy

    try:
        return obj is not None and bpy.data.objects.get(obj.name) is not None
    except ReferenceError:
        return False


class AssemblyReconstructor:
    """Handles the separation and intelligent reassembly of combined meshes."""

    def __init__(
        self,
        matcher: GeometryMatcher,
        combine_assemblies: bool = True,
        search_radius_mult: float = 1.5,
        verbose: bool = False,
    ):
        self.matcher = matcher
        self.combine_assemblies = combine_assemblies
        self.search_radius_mult = search_radius_mult
        self.verbose = verbose
        # Assembly group Empties created by this run; ones emptied by later
        # combining are deleted (see cleanup_empty_assembly_groups).
        self._created_assembly_uids: List[int] = []
        # session_uids of the combined per-copy assembly meshes this run
        # produced. A combined copy that fails to instance is still a
        # semantic unit — the remainder-combine must not dissolve it into a
        # material blob.
        self._combined_assembly_uids: List[int] = []

    # ------------------------------------------------------------------
    # Separation
    # ------------------------------------------------------------------
    def separate_combined_meshes(self, objects: List[object]) -> List[object]:
        """Separate any combined (multi-shell) meshes into their shells.

        Unlike Maya's ``polySeparate``, Blender's separate keeps the source
        object holding one shell — the returned list contains the source
        plus the new shell objects; no empty husk is left behind.
        """
        import bmesh

        from blendertk.edit_utils._edit_utils import _count_shells, separate_objects

        new_nodes: List[object] = []
        for obj in objects:
            if not _alive(obj):
                continue
            me = _mesh(obj)
            if me is None:
                new_nodes.append(obj)
                continue

            # Never split an already-instanced mesh — separation would
            # collapse the sharing the user (or a prior run) set up.
            if _object_users(me) > 1:
                new_nodes.append(obj)
                continue

            bm = bmesh.new()
            bm.from_mesh(me)
            num_shells = _count_shells(bm)
            bm.free()

            if num_shells > 1:
                if self.verbose:
                    logger.info(
                        "Separating combined mesh: %s (%s shells)",
                        obj.name,
                        num_shells,
                    )
                try:
                    # NOTE: Do NOT canonicalize here — it expands bounding
                    # boxes and breaks BFS grouping. Canonicalization is done
                    # after reassemble_assemblies for instancing purposes.
                    parts = separate_objects([obj], center_pivots=False)
                    # separate mutates the source datablock in place (it
                    # keeps one shell) — drop any cached geometry for it.
                    self.matcher.invalidate(me)
                    new_nodes.append(obj)  # source keeps one shell
                    new_nodes.extend(parts)
                except RuntimeError as e:
                    logger.warning("Failed to separate %s: %s", obj.name, e)
                    new_nodes.append(obj)
            else:
                new_nodes.append(obj)

        return new_nodes

    def cleanup_empty_sources(self) -> None:
        """No-op (API parity with mayatk).

        Blender's separate keeps the source object as a real shell — there
        is no shapeless leftover transform to delete.
        """

    def cleanup_empty_assembly_groups(self) -> None:
        """Delete assembly group Empties this run created that have emptied.

        Combining the non-instanced remainder joins a kept group's children
        into top-level meshes, leaving the Empty behind. Scoped to this
        run's own groups via session_uid — never touches groups from earlier
        runs the user may have kept.
        """
        import bpy

        uids = set(self._created_assembly_uids)
        self._created_assembly_uids = []
        if not uids:
            return
        for obj in [o for o in bpy.data.objects if o.session_uid in uids]:
            if not obj.children:
                try:
                    bpy.data.objects.remove(obj, do_unlink=True)
                except Exception as e:
                    logger.debug("Could not delete empty group %s: %s", obj, e)

    # ------------------------------------------------------------------
    # Canonicalization
    # ------------------------------------------------------------------
    def _capture_custom_normals_world(self, obj) -> Optional[np.ndarray]:
        """World-space per-loop custom normals, or ``None`` when absent.

        Custom split normals live in object space and do NOT follow the
        transform edits below — without an explicit restore, the custom
        shading of CAD/FBX imports rotates with the transform. Meshes
        without custom normals recompute from geometry and need no
        compensation (the analogue of Maya's locked-normal capture).
        """
        me = obj.data
        if not me.has_custom_normals:
            return None
        n = len(me.loops)
        buf = np.empty(n * 3, dtype=np.float32)
        me.corner_normals.foreach_get("vector", buf)
        local = buf.astype(np.float64).reshape(-1, 3)
        # Normal matrix = inverse-transpose of the linear block.
        nm = np.array(obj.matrix_world, dtype=float)[:3, :3]
        nm = np.linalg.inv(nm).T
        world = local @ nm.T
        lengths = np.linalg.norm(world, axis=1)
        nz = lengths > 1e-12
        world[nz] /= lengths[nz, None]
        return world

    @staticmethod
    def _restore_custom_normals_world(obj, world_normals: np.ndarray) -> None:
        """Re-set custom split normals from world-space vectors."""
        nm = np.array(obj.matrix_world, dtype=float)[:3, :3]
        nm = np.linalg.inv(nm).T  # world = nm @ local  ->  local = nm^-1 @ world
        local = world_normals @ np.linalg.inv(nm).T
        lengths = np.linalg.norm(local, axis=1)
        nz = lengths > 1e-12
        local[nz] /= lengths[nz, None]
        obj.data.normals_split_custom_set(local.tolist())

    def center_transform_on_geometry(self, obj) -> None:
        """Move the transform to the center of its geometry without moving it."""
        import bpy
        from mathutils import Vector

        me = _mesh(obj)
        if me is None:
            return
        pts = self.matcher._object_points(me)
        if not len(pts):
            return
        w = np.array(obj.matrix_world, dtype=float)
        world_pts = pts @ w[:3, :3].T + w[:3, 3]
        center = Vector(world_pts.mean(axis=0).tolist())

        w_old = obj.matrix_world.copy()
        w_new = w_old.copy()
        w_new.translation = center
        inv = w_new.inverted(None)
        if inv is None:  # degenerate transform (zero-scale axis) — skip
            return
        # Pure translation in mesh space — custom normals unaffected.
        me.transform(inv @ w_old)
        obj.matrix_world = w_new
        self.matcher.invalidate(me)
        bpy.context.view_layer.update()

    def canonicalize_transform(self, obj) -> None:
        """Align the transform's rotation to the geometry's PCA axes."""
        import bpy
        from mathutils import Matrix

        me = _mesh(obj)
        if me is None:
            return
        # Editing points through one instance would counter-rotate the
        # shared datablock for every OTHER user — never canonicalize
        # instanced geometry (the robust matcher handles it uncanonicalized).
        if _object_users(me) > 1:
            return

        try:
            self.center_transform_on_geometry(obj)

            basis = self.matcher.get_pca_basis(obj)
            if basis is None:
                return

            w_old = obj.matrix_world.copy()
            loc = w_old.to_translation()
            scale = w_old.to_scale()
            w_new = Matrix.LocRotScale(loc, basis.to_quaternion(), scale)

            custom = self._capture_custom_normals_world(obj)
            me.transform(w_new.inverted() @ w_old)
            obj.matrix_world = w_new
            self.matcher.invalidate(me)
            bpy.context.view_layer.update()
            if custom is not None:
                self._restore_custom_normals_world(obj, custom)
        except Exception as e:
            if self.verbose:
                logger.warning("Canonicalization failed for %s: %s", obj.name, e)

    def canonicalize_leaf_meshes(self, objects: List[object]) -> List[object]:
        """Canonicalize all leaf mesh objects for instancing.

        Called AFTER reassemble_assemblies (canonicalization expands
        bounding boxes and would break touch detection). Centers each
        mesh's transform on its geometry and aligns rotation to PCA axes;
        for group Empties, canonicalizes mesh children one level deep.
        """
        for obj in objects:
            if not _alive(obj):
                logger.debug("canonicalize_leaf_meshes: skipping stale object")
                continue
            if _mesh(obj) is not None:
                self.canonicalize_transform(obj)
            else:
                for child in obj.children:
                    if _mesh(child) is not None:
                        self.canonicalize_transform(child)
        return objects

    # ------------------------------------------------------------------
    # Reassembly
    # ------------------------------------------------------------------
    def reassemble_assemblies(self, objects: List[object]) -> List[object]:
        """Reassemble separated shells into logical assemblies.

        Feature extraction here; the sorting itself is the shared
        ``ptk.AssemblySorter`` (see its docstring for the algorithm). A
        multi-part group becomes an Empty carrying ``ASSEMBLY_TAG_ATTR``.
        """
        import bmesh

        if not objects:
            return []
        self._created_assembly_uids = []

        # Filter to valid mesh objects. Already-instanced meshes are passed
        # through untouched: they are deduplicated already, and joining them
        # into per-copy combined assemblies would re-duplicate their data.
        valid_nodes = []
        passthrough: List[object] = []
        for o in objects:
            if not _alive(o):
                continue
            if _mesh(o) is None:
                continue
            if _object_users(o.data) > 1:
                passthrough.append(o)
            else:
                valid_nodes.append(o)

        if not valid_nodes:
            return [o for o in objects if _alive(o)]

        # Build part info (exact world-vert bbox — the corner-transformed
        # local bbox overestimates for rotated parts and would create false
        # touches; world surface area is rotation invariant).
        parts: List[Dict[str, Any]] = []
        for obj in valid_nodes:
            try:
                me = obj.data
                pts = self.matcher._object_points(me)
                if not len(pts):
                    continue
                w = np.array(obj.matrix_world, dtype=float)
                world_pts = pts @ w[:3, :3].T + w[:3, 3]
                mins = world_pts.min(axis=0)
                maxs = world_pts.max(axis=0)

                bm = bmesh.new()
                bm.from_mesh(me)
                bm.transform(obj.matrix_world)
                area = float(sum(f.calc_area() for f in bm.faces))
                bm.free()

                parts.append(
                    {
                        "idx": len(parts),
                        "node": obj,
                        "bbox": [*mins.tolist(), *maxs.tolist()],
                        "topo": (len(me.vertices), len(me.polygons)),
                        "area": area,
                        "center": (mins + maxs) / 2.0,
                        "volume": float(np.prod(maxs - mins)),
                        "material": self._get_material(obj),
                    }
                )
            except Exception:
                pass

        if not parts:
            return list(objects)

        # Sort parts into assembly copies via the shared DCC-neutral
        # clustering (mayatk consumes the same implementation).
        sorter = ptk.AssemblySorter(
            search_radius_mult=self.search_radius_mult, verbose=self.verbose
        )
        final_groups = sorter.sort(parts)

        if self.verbose:
            logger.info("Final assembly count: %s", len(final_groups))

        return self._create_assembly_groups(parts, final_groups) + passthrough

    def _get_material(self, obj) -> Optional[str]:
        """Material identity key for an object, or ``None``.

        Always material-aware regardless of the matcher's
        ``require_same_material``: a material boundary is physical evidence
        that parts belong to different objects (see mayatk's measured
        precision collapse without it). Multi-material objects produce a
        sorted composite key for run-to-run determinism.
        """
        try:
            mats = sorted(
                {s.material.name for s in obj.material_slots if s.material}
            )
            if mats:
                return ",".join(mats)
        except Exception:
            pass
        return None

    def _create_assembly_groups(
        self, parts: List[Dict], groups: List[List[int]]
    ) -> List[object]:
        """Create an Empty per multi-part assembly; parent parts under it."""
        import bpy

        result: List[object] = []
        used: set = set()

        for group in groups:
            if len(group) <= 1:
                for idx in group:
                    obj = parts[idx]["node"]
                    if obj.session_uid not in used:
                        result.append(obj)
                        used.add(obj.session_uid)
                continue

            root_idx = max(group, key=lambda i: parts[i]["volume"])
            root = parts[root_idx]["node"]
            children = [parts[idx]["node"] for idx in group if idx != root_idx]

            if root.session_uid in used or any(
                c.session_uid in used for c in children
            ):
                for idx in group:
                    obj = parts[idx]["node"]
                    if obj.session_uid not in used:
                        result.append(obj)
                        used.add(obj.session_uid)
                continue

            try:
                grp = bpy.data.objects.new("Assembly", None)
                collections = list(root.users_collection) or [
                    bpy.context.scene.collection
                ]
                collections[0].objects.link(grp)
                grp[ASSEMBLY_TAG_ATTR] = True
                self._created_assembly_uids.append(grp.session_uid)

                # Position at the centroid of member bbox centers, with the
                # root's world rotation.
                centroid = np.mean([parts[idx]["center"] for idx in group], axis=0)
                grp.location = centroid.tolist()
                grp.rotation_euler = root.matrix_world.to_euler()
                bpy.context.view_layer.update()

                reparent([root] + children, grp, keep_transform=True)
                used.add(root.session_uid)
                used.update(c.session_uid for c in children)
                result.append(grp)
            except Exception as e:
                logger.error(f"Error creating assembly for {root.name}: {e}")
                for idx in group:
                    obj = parts[idx]["node"]
                    if obj.session_uid not in used:
                        result.append(obj)
                        used.add(obj.session_uid)

        return result

    @staticmethod
    def _is_assembly_group(obj) -> bool:
        """True if *obj* is an assembly group Empty created by this tool."""
        try:
            return obj.type == "EMPTY" and bool(obj.get(ASSEMBLY_TAG_ATTR))
        except Exception:
            return False

    @staticmethod
    def _is_mesh_object(obj) -> bool:
        return _alive(obj) and _mesh(obj) is not None

    # ------------------------------------------------------------------
    # Assembly combining
    # ------------------------------------------------------------------
    @_object_mode
    def combine_reassembled_assemblies(self, objects: List[object]) -> List[object]:
        """Combine each copy of a repeated assembly type into a single mesh.

        Assembly groups are clustered by their part-signature multiset (the
        assembly "type"); every copy of a type with >= 2 copies is joined
        into one mesh so the copies instance at assembly level. Unique
        (single-copy) assembly types are left as reconstructed groups:
        combining them gains no instancing, and their parts stay eligible
        for leaf-level matching.
        """
        import bpy

        if not objects:
            return []
        self._combined_assembly_uids = []

        combined_meshes: List[object] = []
        assembly_groups: List[object] = []
        for obj in objects:
            if not _alive(obj):
                continue
            if self._is_assembly_group(obj):
                assembly_groups.append(obj)
            else:
                combined_meshes.append(obj)

        if not assembly_groups:
            return combined_meshes

        # Cluster groups by assembly type: the multiset of relaxed
        # (topology-only) part signatures — the combined results are still
        # verified by full geometric matching before any instancing happens.
        grp_children: Dict[int, List[object]] = {}
        by_type: Dict[frozenset, List[object]] = defaultdict(list)
        for grp in assembly_groups:
            mesh_children = [c for c in grp.children if self._is_mesh_object(c)]
            grp_children[grp.session_uid] = mesh_children
            sig_counts: Dict[Tuple, int] = defaultdict(int)
            for c in mesh_children:
                s = self.matcher.get_mesh_signature(c)
                if s:
                    sig_counts[s[:3]] += 1
            by_type[frozenset(sig_counts.items())].append(grp)

        for type_key, grps in by_type.items():
            if len(grps) < 2 or not type_key:
                # Unique type (or no signable parts) — keep the group intact;
                # its parts stay individually eligible for leaf matching.
                for grp in grps:
                    combined_meshes.extend(grp_children[grp.session_uid])
                continue

            for grp in grps:
                grp_parts = [
                    p for p in grp_children[grp.session_uid] if _alive(p)
                ]
                if not grp_parts:
                    combined_meshes.append(grp)
                    continue
                grp_name = grp.name
                if len(grp_parts) == 1:
                    core_mesh = grp_parts[0]
                else:
                    try:
                        core_mesh = self._join(grp_parts)
                        # join mutates parts[0]'s datablock in place.
                        self.matcher.invalidate(core_mesh.data)
                    except Exception as e:
                        logger.warning("Join failed for %s: %s", grp_name, e)
                        core_mesh = None

                if core_mesh is not None:
                    try:
                        core_mesh.name = f"{grp_name}_combined"
                        self.canonicalize_transform(core_mesh)
                        if core_mesh.parent is not None:
                            reparent([core_mesh], None, keep_transform=True)
                    except Exception:
                        pass
                    self._combined_assembly_uids.append(core_mesh.session_uid)
                    combined_meshes.append(core_mesh)
                else:
                    # Join failed — keep the group so nothing is lost.
                    combined_meshes.append(grp)
                    continue

                try:
                    if not grp.children:
                        bpy.data.objects.remove(grp, do_unlink=True)
                except Exception:
                    pass

        return combined_meshes

    @staticmethod
    def _join(parts: List[object]):
        """Join *parts* into ``parts[0]`` (Maya's polyUnite analogue)."""
        import bpy

        # join chokes on multi-user data — make each part single-user first.
        for p in parts:
            if p.data.users > 1:
                p.data = p.data.copy()
        with bpy.context.temp_override(
            active_object=parts[0],
            selected_objects=list(parts),
            selected_editable_objects=list(parts),
        ):
            bpy.ops.object.join()
        return parts[0]
