# !/usr/bin/python
# coding=utf-8
"""Geometry analysis and matching logic for AutoInstancer (bpy adapter).

The verification pipeline itself (3-stage compare, PCA basis stabilization,
eigenvalue signatures) is the shared DCC-neutral implementation in
``pythontk.PointCloud`` — this module only extracts mesh data from bpy and
converts matrices. scipy is an optional accelerator: without it pythontk
falls back to a brute-force scorer (slower; off-grid spins around a
symmetric axis may fail at tight tolerance — install scipy into Blender's
Python via the blenderpy package manager for full fidelity).

Matrix conventions: pythontk returns flat 16-element ROW-MAJOR matrices
(row-vector, translation in row 3); Blender's ``mathutils.Matrix`` is
column-vector. The relative transform is stored as a ``mathutils.Matrix``
``rel`` such that placing an instance is ``matrix_world @ rel`` (the
column-convention equivalent of Maya's ``rel * target_matrix``).
"""
from __future__ import annotations

import logging
from typing import Optional, Tuple, Union

import numpy as np

import pythontk as ptk

logger = logging.getLogger(__name__)


def _flat_rm_to_matrix(flat):
    """Row-major 16-float list -> column-convention ``mathutils.Matrix``."""
    from mathutils import Matrix

    return Matrix(np.array(flat, dtype=float).reshape(4, 4).T.tolist())


def _matrix_to_np_rm(matrix) -> np.ndarray:
    """``mathutils.Matrix`` -> row-major numpy 4x4 (row-vector convention)."""
    return np.array(matrix, dtype=float).T


def _mesh(obj):
    """The object's mesh datablock, or ``None`` for non-mesh objects."""
    if obj is None or getattr(obj, "type", None) != "MESH":
        return None
    return obj.data


class GeometryMatcher:
    """Handles geometric analysis and comparison."""

    # Minimum mean normal dot product for a match to count as shading-
    # compatible. Identical copies score ~1.0; a flipped symmetric twin
    # scores ~-1.0.
    NORMAL_AGREEMENT_THRESHOLD = 0.8

    def __init__(
        self,
        tolerance: float = 0.001,
        scale_tolerance: float = 0.0,
        uv_tolerance: float = 0.001,
        require_same_material: Union[bool, int] = True,
        check_uvs: bool = False,
        verbose: bool = False,
    ):
        self.tolerance = tolerance
        self.scale_tolerance = scale_tolerance
        self.uv_tolerance = uv_tolerance
        self.require_same_material = require_same_material
        self.check_uvs = check_uvs
        self.verbose = verbose
        # Discovery-phase caches — valid while the scene is static. Callers
        # that mutate geometry between comparison batches must clear_cache().
        # Keyed by mesh datablock name (unique within bpy.data.meshes; bpy
        # recreates RNA wrappers per access, so wrappers can't key a dict).
        self._points_cache: dict = {}
        self._normals_cache: dict = {}
        self._pair_cache: dict = {}

    def clear_cache(self) -> None:
        """Drop cached point arrays and pair results (call after scene edits)."""
        self._points_cache.clear()
        self._normals_cache.clear()
        self._pair_cache.clear()

    def invalidate(self, me) -> None:
        """Drop cached data for ONE mesh datablock (call after mutating it).

        Unlike Maya — where polyUnite/polySeparate create NEW shape nodes,
        so a stale cache entry can never be re-hit — Blender's join/separate/
        ``mesh.transform`` mutate a datablock in place under its same name.
        Every reconstructor stage that edits a mesh must invalidate it.
        """
        key = me.name_full
        self._points_cache.pop(key, None)
        self._normals_cache.pop(key, None)
        for pair in [k for k in self._pair_cache if key in k]:
            del self._pair_cache[pair]

    # ------------------------------------------------------------------
    # Data extraction
    # ------------------------------------------------------------------
    def _object_points(self, me) -> np.ndarray:
        """Cached object-space points for mesh datablock *me* as (N, 3)."""
        key = me.name_full
        pts = self._points_cache.get(key)
        if pts is None:
            n = len(me.vertices)
            buf = np.empty(n * 3, dtype=np.float32)
            me.vertices.foreach_get("co", buf)
            pts = buf.astype(np.float64).reshape(-1, 3)
            self._points_cache[key] = pts
        return pts

    def _object_normals(self, me) -> Optional[np.ndarray]:
        """Cached object-space per-vertex averaged shading normals (N, 3).

        ``corner_normals`` is the SHADING truth (honors custom split
        normals and sharp edges — the analogue of Maya's ``getNormals``);
        the per-corner normals are scatter-accumulated per vertex and unit-
        normalized. Fully-cancelling vertices stay zero — their dot
        contributes rejection, which is correct (contradictory shading).
        """
        key = me.name_full
        if key in self._normals_cache:
            return self._normals_cache[key]
        normals = None
        try:
            n_loops = len(me.loops)
            if n_loops:
                corner = np.empty(n_loops * 3, dtype=np.float32)
                me.corner_normals.foreach_get("vector", corner)
                corner = corner.reshape(-1, 3).astype(np.float64)
                loop_verts = np.empty(n_loops, dtype=np.int64)
                me.loops.foreach_get("vertex_index", loop_verts)
                acc = np.zeros((len(me.vertices), 3))
                np.add.at(acc, loop_verts, corner)
                lengths = np.linalg.norm(acc, axis=1)
                nz = lengths > 1e-9
                acc[nz] /= lengths[nz, None]
                normals = acc
        except Exception:
            pass
        self._normals_cache[key] = normals
        return normals

    def quantize(self, value: float, precision: int = 4) -> float:
        """Round a value to a specific precision to ignore float noise."""
        if value == 0.0:
            return 0.0
        return round(value, precision)

    def get_pca_basis(self, obj):
        """Stabilized PCA rotation basis for the object's mesh.

        Returns a column-convention ``mathutils.Matrix`` (rotation only)
        whose local axes align with the geometry's principal axes, or
        ``None``. The frame is stabilized so identical geometry always
        yields the same basis (see ``PointCloud.pca_basis``).
        """
        me = _mesh(obj)
        if me is None:
            return None
        try:
            pts = self._object_points(me)
            w = np.array(obj.matrix_world, dtype=float)
            world_pts = pts @ w[:3, :3].T + w[:3, 3]
            flat = ptk.PointCloud.pca_basis(world_pts)
            if flat is None:
                return None
            return _flat_rm_to_matrix(flat)
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Signatures
    # ------------------------------------------------------------------
    def get_mesh_signature(self, obj) -> Optional[Tuple]:
        """Lightweight signature for quick rejection.

        Returns ``(verts, edges, faces, pca_sig, materials, uv_signature)``,
        or ``None`` when *obj* has no mesh. Surface area is deliberately
        absent — it is not scale invariant.
        """
        me = _mesh(obj)
        if me is None:
            return None

        num_verts = len(me.vertices)
        num_edges = len(me.edges)
        num_faces = len(me.polygons)

        # PCA Signature (Eigenvalues). With scale tolerance enabled the
        # eigenvalue descriptors are unreliable without full alignment —
        # rely on topological counts and the detailed check instead.
        pca_sig = ()
        if self.scale_tolerance <= 0:
            try:
                pca_sig = ptk.PointCloud.pca_eigenvalue_signature(
                    self._object_points(me), 3
                )
            except Exception as e:
                if self.verbose:
                    logger.debug(f"PCA failed for {obj.name}: {e}")

        materials = ()
        if self.require_same_material:
            materials = tuple(
                sorted(
                    {s.material.name for s in obj.material_slots if s.material}
                )
            )

        uv_signature = ()
        if self.check_uvs:
            names = tuple(layer.name for layer in me.uv_layers)
            # Blender UVs are per-loop, so every layer holds len(loops)
            # coordinates; the count still catches layer-count mismatches
            # and the detailed compare does the real work.
            uv_signature = (names, tuple(len(me.loops) for _ in names))

        return (num_verts, num_edges, num_faces, pca_sig, materials, uv_signature)

    def get_hierarchy_signature(self, obj) -> Tuple:
        """Recursive signature generation for hierarchy comparison."""
        geo_sig = self.get_mesh_signature(obj)

        child_sigs = []
        for child in obj.children:
            c_sig = self.get_hierarchy_signature(child)
            # Use distance for rotation invariance
            dist = child.matrix_local.to_translation().length
            dist_sig = self.quantize(dist, 2)
            if self.scale_tolerance > 0:
                dist_sig = 0.0
            child_sigs.append((c_sig, dist_sig))

        child_sigs.sort(key=lambda x: str(x))
        return (geo_sig, tuple(child_sigs))

    # ------------------------------------------------------------------
    # Detailed comparison
    # ------------------------------------------------------------------
    def are_meshes_identical(self, o1, o2) -> Tuple[bool, Optional[object]]:
        """Detailed geometric comparison via ``PointCloud.match_clouds``.

        Results are memoized per mesh-datablock pair (see ``clear_cache``).

        Returns:
            (is_identical, relative_transform) — the relative transform is a
            column-convention ``mathutils.Matrix`` (place an instance via
            ``matrix_world @ rel``), or ``None`` for an identity match.
        """
        m1 = _mesh(o1)
        m2 = _mesh(o2)
        if m1 is None or m2 is None:
            return False, None

        key = (m1.name_full, m2.name_full)
        cached = self._pair_cache.get(key)
        if cached is not None:
            return cached

        result = self._are_meshes_identical_uncached(m1, m2, o1.name, o2.name)
        self._pair_cache[key] = result
        return result

    def _are_meshes_identical_uncached(
        self, m1, m2, n1: str, n2: str
    ) -> Tuple[bool, Optional[object]]:
        uvs_identical = None
        if self.check_uvs:
            uvs_identical = lambda: self._are_uvs_identical(m1, m2)  # noqa: E731

        matched, matrix_list = ptk.PointCloud.match_clouds(
            self._object_points(m1),
            self._object_points(m2),
            tolerance=self.tolerance,
            scale_tolerance=self.scale_tolerance,
            normals_a=self._object_normals(m1),
            normals_b=self._object_normals(m2),
            normal_threshold=self.NORMAL_AGREEMENT_THRESHOLD,
            uvs_identical=uvs_identical,
        )
        if not matched:
            if self.verbose:
                logger.debug(f"No geometric match for {n1} vs {n2}")
            return False, None
        if matrix_list is None:
            return True, None
        return True, _flat_rm_to_matrix(matrix_list)

    def _uv_coords(self, me, layer) -> np.ndarray:
        arr = np.empty(len(me.loops) * 2, dtype=np.float32)
        try:
            layer.uv.foreach_get("vector", arr)
        except (AttributeError, TypeError):
            layer.data.foreach_get("uv", arr)  # legacy accessor
        return arr.reshape(-1, 2)

    def _are_uvs_identical(self, m1, m2) -> bool:
        """Compare UVs of two meshes (assumes identical loop order)."""
        names1 = {layer.name for layer in m1.uv_layers}
        names2 = {layer.name for layer in m2.uv_layers}
        if names1 != names2:
            return False

        for layer1 in m1.uv_layers:
            layer2 = m2.uv_layers[layer1.name]
            uv1 = self._uv_coords(m1, layer1)
            uv2 = self._uv_coords(m2, layer2)
            if len(uv1) != len(uv2):
                return False
            if not np.allclose(uv1, uv2, atol=self.uv_tolerance):
                return False
        return True

    # ------------------------------------------------------------------
    # Hierarchy comparison
    # ------------------------------------------------------------------
    def _is_matrix_close(self, m1, m2) -> bool:
        """True when two matrices are equivalent within tolerance."""
        a = np.eye(4) if m1 is None else np.array(m1, dtype=float)
        b = np.eye(4) if m2 is None else np.array(m2, dtype=float)
        return bool(np.max(np.abs(a - b)) <= self.tolerance)

    def are_meshes_identical_with_transform(self, o1, o2, matrix) -> bool:
        """True when *o1* transformed by *matrix* matches *o2* in parent space."""
        m1 = _mesh(o1)
        m2 = _mesh(o2)
        if m1 is None or m2 is None:
            return False

        pts1 = self._object_points(m1)
        pts2 = self._object_points(m2)
        if len(pts1) != len(pts2):
            return False
        if len(pts1) == 0:
            # match_clouds declares two empty clouds identical (stage-1
            # short-circuit) — honor the same convention instead of crashing
            # the NN query on a zero-size array.
            return True

        rel_rm = np.eye(4) if matrix is None else _matrix_to_np_rm(matrix)
        mat1 = _matrix_to_np_rm(o1.matrix_local)
        mat2 = _matrix_to_np_rm(o2.matrix_local)

        ones = np.ones((len(pts1), 1))
        pts1_parent = np.hstack([pts1, ones]) @ mat1  # (N, 4)
        pts1_target = (pts1_parent @ rel_rm)[:, :3]
        pts2_parent = (np.hstack([pts2, ones]) @ mat2)[:, :3]

        dists, _ = ptk.PointCloud.nn_query(pts2_parent, pts1_target, k=1)
        return bool(float(dists.max()) <= self.tolerance)

    def are_hierarchies_identical(
        self,
        o1,
        o2,
        expected_transform=None,
        is_root: bool = False,
    ) -> Tuple[bool, Optional[object]]:
        """Detailed hierarchy comparison. Returns (is_identical, relative_transform)."""
        has_mesh1 = _mesh(o1) is not None
        has_mesh2 = _mesh(o2) is not None
        if has_mesh1 != has_mesh2:
            return False, None

        relative_transform = expected_transform
        transform_determined = expected_transform is not None

        if has_mesh1:
            if transform_determined:
                if not self.are_meshes_identical_with_transform(
                    o1, o2, relative_transform
                ):
                    if self.verbose:
                        logger.debug(
                            f"Mesh mismatch with expected transform for "
                            f"{o1.name} vs {o2.name}"
                        )
                    return False, None
            else:
                is_identical, rel_mtx = self.are_meshes_identical(o1, o2)
                if not is_identical:
                    return False, None

                # Verify the found transform actually aligns geometry in
                # parent space. Catches cases where shapes match but local
                # transforms differ (scale/rotation). For roots we expect
                # different locations, so skip — trust the shape-level check.
                if not is_root and not self.are_meshes_identical_with_transform(
                    o1, o2, rel_mtx
                ):
                    if self.verbose:
                        logger.debug(
                            f"Transform found by shape match does not align "
                            f"parent-space geometry for {o1.name} vs {o2.name}"
                        )
                    return False, None

                relative_transform = rel_mtx
                transform_determined = True

        # 2. Children
        children1 = list(o1.children)
        children2 = list(o2.children)
        if len(children1) != len(children2):
            return False, None

        # Sort children by distance from parent and mesh complexity.
        def get_sort_key(obj):
            dist = obj.matrix_local.to_translation().length
            me = _mesh(obj)
            mesh_sig = (
                (len(me.vertices), len(me.edges), len(me.polygons))
                if me is not None
                else (0, 0, 0)
            )
            if self.scale_tolerance > 0:
                return (0.0, mesh_sig)
            return (round(dist, 3), mesh_sig)

        children1.sort(key=get_sort_key)
        children2.sort(key=get_sort_key)

        processed_pairs = []

        for c1, c2 in zip(children1, children2):
            # Distance to parent
            if self.scale_tolerance <= 0:
                d1 = c1.matrix_local.to_translation().length
                d2 = c2.matrix_local.to_translation().length
                if abs(d1 - d2) > self.tolerance:
                    return False, None

            is_child_identical, child_rel_mtx = self.are_hierarchies_identical(
                c1, c2, relative_transform
            )

            if is_child_identical:
                if transform_determined:
                    # Thought it was identity (None), but child says otherwise
                    if relative_transform is None and child_rel_mtx is not None:
                        all_compatible = True
                        for p1, p2 in processed_pairs:
                            ok, _ = self.are_hierarchies_identical(
                                p1, p2, child_rel_mtx
                            )
                            if not ok:
                                all_compatible = False
                                break
                        if not all_compatible:
                            return False, None
                        relative_transform = child_rel_mtx

                if not transform_determined:
                    relative_transform = child_rel_mtx
                    transform_determined = True

                processed_pairs.append((c1, c2))
                continue

            # If failed and we have a transform, maybe it was the wrong one
            if transform_determined:
                if self.verbose:
                    logger.debug(
                        f"Transform mismatch. Retrying {c1.name} vs {c2.name} "
                        f"independently..."
                    )
                is_indep_match, indep_mtx = self.are_hierarchies_identical(
                    c1, c2, None
                )
                if is_indep_match and indep_mtx is not None:
                    all_compatible = True
                    for p1, p2 in processed_pairs:
                        ok, _ = self.are_hierarchies_identical(p1, p2, indep_mtx)
                        if not ok:
                            all_compatible = False
                            break
                    if all_compatible:
                        relative_transform = indep_mtx
                        processed_pairs.append((c1, c2))
                        continue

            return False, None

        # 3. Internal structure (pairwise distances)
        if self.scale_tolerance <= 0 and len(children1) > 1:
            for i in range(len(children1)):
                for j in range(i + 1, len(children1)):
                    p1_i = children1[i].matrix_local.to_translation()
                    p1_j = children1[j].matrix_local.to_translation()
                    dist1 = (p1_i - p1_j).length

                    p2_i = children2[i].matrix_local.to_translation()
                    p2_j = children2[j].matrix_local.to_translation()
                    dist2 = (p2_i - p2_j).length

                    if abs(dist1 - dist2) > self.tolerance:
                        return False, None

        return True, relative_transform
