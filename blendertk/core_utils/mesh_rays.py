# !/usr/bin/python
# coding=utf-8
"""Rays through the scene's meshes: a :class:`pythontk.ProbeRays` over Blender's -- mirror of mayatk's.

What any tool asks of the scene's geometry by sight -- a lightmap bake's
reflection probe (``ProbePlacement``), a helper's first pose
(``ptk.SpotPlacement``, the Shot Manifest) -- through a ``mathutils.bvhtree.BVHTree``
per object, built from its EVALUATED mesh (modifiers applied, as the render
sees it) in world space, both-sided as Maya's ``closestIntersection`` is. One
instance reads each object's world geometry once, against the window's
depsgraph. In Blender units.
"""

from typing import Any, Dict, Sequence, Tuple

import pythontk as ptk

Vec = Tuple[float, float, float]


class MeshRays:
    """The objects named as a :class:`pythontk.ProbeRays`, and how its rays meet
    them (:class:`pythontk.ProbeMesh`): one ``BVHTree`` each, in world space."""

    def __init__(self):
        from blendertk.core_utils._core_utils import CoreUtils

        self._geometry: Dict[str, Tuple[Any, Any]] = {}
        self._depsgraph = CoreUtils._evaluated_depsgraph()  # the window's layer

    def _world(self, name: str) -> Tuple[Any, Any]:
        """``(vertices (n, 3), triangles (m, 3))`` of *name*'s evaluated mesh
        in world space, read once. Empty for an object with no mesh."""
        if name in self._geometry:
            return self._geometry[name]
        import bpy
        import numpy as np

        verts = np.zeros((0, 3))
        tris = np.zeros((0, 3), dtype=np.int64)
        obj = bpy.data.objects.get(name)
        evaluated = obj.evaluated_get(self._depsgraph) if obj is not None else None
        mesh = None
        try:
            mesh = evaluated.to_mesh() if evaluated is not None else None
        except RuntimeError:  # an object with no geometry to evaluate
            mesh = None
        if mesh is not None:
            try:
                count = len(mesh.vertices)
                local = np.empty(count * 3, dtype=np.float64)
                mesh.vertices.foreach_get("co", local)
                local = local.reshape(-1, 3)
                matrix = np.array(evaluated.matrix_world, dtype=np.float64)
                verts = local @ matrix[:3, :3].T + matrix[:3, 3]
                mesh.calc_loop_triangles()
                flat = np.empty(len(mesh.loop_triangles) * 3, dtype=np.int64)
                mesh.loop_triangles.foreach_get("vertices", flat)
                tris = flat.reshape(-1, 3)
            finally:
                evaluated.to_mesh_clear()
        self._geometry[name] = (verts, tris)
        return verts, tris

    def rays(self, names: Sequence[str]) -> "ptk.ProbeRays":
        """A :class:`pythontk.ProbeRays` over the objects *names* name: one
        ``BVHTree`` each, from the evaluated world geometry."""
        from mathutils.bvhtree import BVHTree

        rays = ptk.ProbeRays(self)
        for name in names:
            verts, tris = self._world(name)
            if not len(tris):
                continue
            tree = BVHTree.FromPolygons(
                [tuple(v) for v in verts.tolist()],
                [tuple(t) for t in tris.tolist()],
                all_triangles=True,
            )
            rays.add(name, verts.min(axis=0), verts.max(axis=0), tree)
        return rays

    @staticmethod
    def intersect(tree, origin, direction, far):
        from mathutils import Vector

        location, normal, _index, distance = tree.ray_cast(
            Vector(tuple(origin)), Vector(tuple(direction)), float(far)
        )
        if location is None:
            return None
        return distance, (location.x, location.y, location.z), normal

    @staticmethod
    def normal(tree, face) -> Vec:
        return (face.x, face.y, face.z)
