# !/usr/bin/python
# coding=utf-8
"""Where a lightmap bake's reflection probe stands -- Blender's scene for the placement.

Mirror of mayatk's ``_probe_placement``. The placement itself -- open air near
the bake's middle, the room around it face by face, over what matters, at eye
level, clear of every surface -- is host-free and lives once in pythontk
(:class:`pythontk.ProbePlacement`, whose module docstring holds the rules and
the scenes that forced each). This module hands it Blender's scene, as its
host adapter (:class:`BlenderProbeHost`, a :class:`pythontk.ProbeHost` and the
:class:`pythontk.ProbeMesh` its rays intersect through):

* **The rays**: a ``mathutils.bvhtree.BVHTree`` per object, built from its
  EVALUATED mesh (modifiers applied, as the render sees it) in world space.
  Its hits are two-sided, as Maya's ``closestIntersection`` is -- what the
  placement reads a shell's back by.
* **The bounds**: the same world-space vertices -- exact, as Maya's
  ``exactWorldBoundingBox`` is, never the object's local box turned.
* **The lights**: the light objects that reach the render at a non-zero power
  -- a sun as the sky, the rest by their world position -- and an emitting
  world as the sky (mayatk's sky dome).
* **The units and axes**: Blender's own. Everything inside is in scene units
  (Blender units), :attr:`BlenderProbeHost.internal_m` the metres in one
  (``Units.metres_per``); Z is up. A probe's deliverables read it in glTF's Y-up
  axes -- ``LightmapBaker.bake_probe`` turns the site into them -- so Blender
  has no Z-up refusal: its axes are always Z-up, and always converted.

:class:`ProbePlacement` is the shared class bound to it: configuration only.
"""

from typing import Any, List, Optional, Sequence, Tuple

import pythontk as ptk

from blendertk.core_utils.mesh_rays import MeshRays

Vec = Tuple[float, float, float]
Box = List[List[float]]


class BlenderProbeHost(MeshRays):
    """What the probe placement asks of the Blender scene
    (:class:`pythontk.ProbeHost`), and how its rays intersect Blender objects
    (:class:`pythontk.ProbeMesh`): one ``BVHTree`` each, in world space.

    One adapter serves one placement: it reads each object's evaluated world
    geometry once, against the window's depsgraph. Nodes are object NAMES
    inside (``ProbeSite.hide`` names what moves).
    """

    #: The up axis, as an index: Blender's Z.
    up: int = 2

    def __init__(self):
        from blendertk.core_utils.units import Units

        super().__init__()  # the evaluated world geometry, read once (MeshRays)
        #: Metres per Blender unit, as the exporters and the probe's record
        #: read it (a unit system of None is metres, whatever the scale).
        self.internal_m: float = Units.metres_per("internal")

    @staticmethod
    def nodes(objects: Sequence[Any]) -> List[str]:
        """Objects (or names) as the object names the placement keys by."""
        return [o if isinstance(o, str) else o.name for o in objects or ()]

    def bounds(self, nodes: Sequence[str]) -> Box:
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

    @staticmethod
    def lights() -> Tuple[List[Tuple[Vec, Optional[str]]], bool]:
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

    @staticmethod
    def to_scene(value: float) -> float:
        """Blender lengths are scene units inside and out."""
        return value

    @staticmethod
    def to_internal(point: Sequence[float]) -> List[float]:
        return [float(c) for c in point]


class ProbePlacement(ptk.ProbePlacement):
    """Place a lightmap bake's reflection probe in the Blender scene:
    :class:`pythontk.ProbePlacement` bound to :class:`BlenderProbeHost`
    (Blender's rays, bounds, lights, units and axes -- see the module
    docstring). Mirror of mayatk's.

    Parameters:
        baked: The baked mesh objects (or names) the render shows -- the room.
        unbaked: The mesh objects (or names) the render shows that no bake
            marks. Those whose footprint covers the bake's (a sky dome, a
            ground or terrain nobody baked) are the room's surroundings: seen,
            and walls. The rest move: the capture sees through them.
    """

    HOST_CLASS = BlenderProbeHost
