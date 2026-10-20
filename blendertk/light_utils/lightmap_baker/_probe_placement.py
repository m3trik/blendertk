# !/usr/bin/python
# coding=utf-8
"""Where a lightmap bake's reflection probe stands -- Blender's scene for the placement.

Mirror of mayatk's ``_probe_placement``. The placement itself -- open air near
the bake's middle, the room around it face by face, over what matters, at eye
level, clear of every surface -- is host-free and lives once in pythontk
(:class:`pythontk.ProbePlacement`, whose module docstring holds the rules and
the scenes that forced each). This module hands it Blender's scene:

* **The rays** (:class:`_Rays`): a ``mathutils.bvhtree.BVHTree`` per object,
  built from its EVALUATED mesh (modifiers applied, as the render sees it) in
  world space.
* **The bounds**: the same world-space vertices -- exact, as Maya's
  ``exactWorldBoundingBox`` is, never the object's local box turned.
* **The lights**: the light objects that reach the render at a non-zero power
  -- a sun as the sky, the rest by their world position -- and an emitting
  world as the sky (mayatk's sky dome).
* **The units and axes**: Blender's own. Everything inside is in scene units
  (Blender units), :attr:`ProbePlacement.INTERNAL_M` the metres in one
  (``Units.metres_per``); Z is up. A probe's deliverables read it in glTF's Y-up
  axes -- ``LightmapBaker.bake_probe`` turns the site into them -- so Blender
  has no Z-up refusal: its axes are always Z-up, and always converted.
"""

from typing import Any, Dict, List, Optional, Sequence, Tuple

import pythontk as ptk

Vec = Tuple[float, float, float]
Box = List[List[float]]


class _Rays(ptk.ProbeRays):
    """:class:`pythontk.ProbeRays` over Blender objects: one ``BVHTree`` each,
    in world space. Its hits are two-sided, as Maya's ``closestIntersection``
    is -- what the placement reads a shell's back by."""

    def __init__(self, names: Sequence[str], geometry):
        super().__init__()
        from mathutils.bvhtree import BVHTree

        for name in names:
            verts, tris = geometry(name)
            if not len(tris):
                continue
            tree = BVHTree.FromPolygons(
                [tuple(v) for v in verts.tolist()],
                [tuple(t) for t in tris.tolist()],
                all_triangles=True,
            )
            self.add(name, verts.min(axis=0), verts.max(axis=0), tree)

    def _intersect(self, tree, origin, direction, far):
        from mathutils import Vector

        location, normal, _index, distance = tree.ray_cast(
            Vector(tuple(origin)), Vector(tuple(direction)), float(far)
        )
        if location is None:
            return None
        return distance, (location.x, location.y, location.z), normal

    def _normal(self, tree, face) -> Vec:
        return (face.x, face.y, face.z)


class ProbePlacement(ptk.ProbePlacement):
    """Place a lightmap bake's reflection probe in the Blender scene
    (:class:`pythontk.ProbePlacement`, with Blender's rays, bounds, lights,
    units and axes -- see the module docstring). Mirror of mayatk's.

    Parameters:
        baked: The baked mesh objects (or names) the render shows -- the room.
        unbaked: The mesh objects (or names) the render shows that no bake
            marks. Those whose footprint covers the bake's (a sky dome, a
            ground or terrain nobody baked) are the room's surroundings: seen,
            and walls. The rest move: the capture sees through them.

    Nodes are object NAMES inside (``ProbeSite.hide`` names what moves).
    """

    #: The up axis, as an index: Blender's Z.
    UP: int = 2

    def __init__(self, baked: Sequence[Any], unbaked: Sequence[Any] = ()):
        from blendertk.core_utils._core_utils import CoreUtils
        from blendertk.core_utils.units import Units

        # Metres per Blender unit, as the exporters and the probe's record read
        # it (a unit system of None is metres, whatever the scale).
        self.INTERNAL_M = Units.metres_per("internal")
        self._geometry: Dict[str, Tuple[Any, Any]] = {}
        self._depsgraph = CoreUtils._evaluated_depsgraph()  # the window's layer
        super().__init__(self._names(baked), self._names(unbaked))

    @staticmethod
    def _names(objects: Sequence[Any]) -> List[str]:
        return [o if isinstance(o, str) else o.name for o in objects or ()]

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

    def _make_rays(self, nodes: Sequence[str]) -> _Rays:
        return _Rays(nodes, self._world)

    def _bounds(self, nodes: Sequence[str]) -> Box:
        """``[[min], [max]]`` of *nodes*' world-space vertices."""
        import numpy as np

        points = [self._world(n)[0] for n in nodes]
        points = [p for p in points if len(p)]
        if not points:
            return [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0]]
        stacked = np.concatenate(points)
        return [
            [float(c) for c in stacked.min(axis=0)],
            [float(c) for c in stacked.max(axis=0)],
        ]

    def _read_lights(self) -> Tuple[List[Tuple[Vec, Optional[str]]], bool]:
        """The placed lights the render shows at a non-zero power, ``[(world
        point, None)]``, and whether a sky lights the scene: a sun, or a
        world that emits."""
        import bpy

        from blendertk.light_utils._light_utils import LightUtils
        from blendertk.light_utils.lightmap_baker.lightmap_baker import LightmapBaker

        scene = bpy.context.scene
        rendered = LightmapBaker._rendered_collections()
        points: List[Tuple[Vec, Optional[str]]] = []
        sky = bool(LightUtils.world_emits(scene.world))
        for obj in scene.objects:
            if obj.type != "LIGHT" or LightmapBaker._power(obj) <= 0.0:
                continue
            if not LightmapBaker._renders(obj, rendered):
                continue
            if obj.data.type == "SUN":
                sky = True
                continue
            t = obj.matrix_world.translation
            points.append(((t.x, t.y, t.z), None))
        return points, sky
