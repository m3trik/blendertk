# !/usr/bin/python
# coding=utf-8
"""Rig control-shape factory — Blender port of mayatk's ``rig_utils.controls.Controls``.

A control is a **curve object** drawn in a recognizable wireframe shape (circle, diamond, square,
cube, sphere, arrow), scaled / oriented / colored, optionally wrapped in an offset **group** (an
Empty parent — the standard rig "buffer"). Controls drive a rig's animatable handles; a curve
object works both standalone *and* as a pose-bone ``custom_shape``.

Mirror of mayatk's ``Controls`` at the **name + behavior** level. Maya builds NURBS curves merged
into one transform and exposes presets through a metaclass; here each shape is a **pure geometry
builder** (a list of ``(points, cyclic)`` polylines, Qt/bpy-free → unit-testable) registered in a
``_PRESETS`` dict, and ``create`` bakes them into one multi-spline curve object. The metaclass is
relaxed to a plain ``register_preset`` classmethod (per the "relax the mirror where the *mechanism*
diverges" rule) — adding a shape stays a one-liner, the extensibility Maya's registry gives.

``import bpy`` / ``mathutils`` are deferred into ``create`` so importing this module never needs a
running Blender (the no-import-side-effects rule); the geometry builders are pure and import-safe.
"""
from dataclasses import dataclass
from typing import List, Optional, Tuple

from blendertk.rig_utils._rig_utils import RigUtils

# A shape is a list of polylines; each polyline is (points, cyclic).
Polyline = Tuple[List[Tuple[float, float, float]], bool]


@dataclass(frozen=True)
class ControlNodes:
    """Return bundle of :meth:`Controls.create` — mirror of mayatk's ``ControlNodes``."""

    control: object
    group: Optional[object] = None


# ----------------------------------------------------------------------------
# Pure geometry builders (canonical: built in the XY plane, normal +Z, unit size)
# ----------------------------------------------------------------------------


def _circle(segments: int = 24) -> List[Polyline]:
    import math

    pts = [
        (math.cos(2 * math.pi * i / segments), math.sin(2 * math.pi * i / segments), 0.0)
        for i in range(segments)
    ]
    return [(pts, True)]


def _square() -> List[Polyline]:
    return [([(-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0)], True)]


def _diamond() -> List[Polyline]:
    return [([(1, 0, 0), (0, 1, 0), (-1, 0, 0), (0, -1, 0)], True)]


def _cube() -> List[Polyline]:
    b = [(-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1)]  # bottom (z=-1)
    t = [(-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1)]  # top (z=+1)
    verticals = [([b[i], t[i]], False) for i in range(4)]
    return [(b, True), (t, True), *verticals]


def _sphere(segments: int = 24) -> List[Polyline]:
    import math

    rings = []
    for plane in ("xy", "xz", "yz"):
        ring = []
        for i in range(segments):
            a = 2 * math.pi * i / segments
            c, s = math.cos(a), math.sin(a)
            ring.append({"xy": (c, s, 0.0), "xz": (c, 0.0, s), "yz": (0.0, c, s)}[plane])
        rings.append((ring, True))
    return rings


def _arrow() -> List[Polyline]:
    # An arrow outline pointing +Y (shaft + head), cyclic.
    return [
        (
            [
                (-0.3, -1, 0), (0.3, -1, 0), (0.3, 0.3, 0), (0.7, 0.3, 0),
                (0.0, 1.0, 0), (-0.7, 0.3, 0), (-0.3, 0.3, 0),
            ],
            True,
        )
    ]


class Controls:
    """Rig control-shape factory (curve-object widgets) — Blender mirror of mayatk's ``Controls``.

    ``Controls.create("circle", name="hand_ctrl", size=2, axis="y", color=(1, 1, 0))`` builds a
    yellow circle curve. Shapes come from the :data:`_PRESETS` registry; register more with
    :meth:`register_preset`.
    """

    _PRESETS = {
        "circle": _circle,
        "square": _square,
        "diamond": _diamond,
        "cube": _cube,
        "box": _cube,  # alias (mayatk's "box")
        "sphere": _sphere,
        "ball": _sphere,  # alias (mayatk's "ball")
        "arrow": _arrow,
    }

    @classmethod
    def register_preset(cls, name, builder):
        """Register a custom shape *builder* (``() -> List[(points, cyclic)]``, pure geometry in the
        canonical XY/+Z unit frame) under *name* — the extension point mirroring Maya's registry."""
        cls._PRESETS[name] = builder

    @classmethod
    def shapes(cls) -> List[str]:
        """Sorted names of the registered shapes (for a UI combo / validation)."""
        return sorted(cls._PRESETS)

    @staticmethod
    def _axis_matrix(axis):
        """Rotate the canonical (XY-plane, normal +Z) shape so its normal points along *axis*."""
        import math
        from mathutils import Matrix

        a = (axis or "y").lower()
        if a == "z":
            return Matrix.Identity(3)
        if a == "x":
            return Matrix.Rotation(math.radians(90), 3, "Y")  # +Z -> +X
        return Matrix.Rotation(math.radians(90), 3, "X")  # "y": +Z -> Y

    @classmethod
    def create(
        cls,
        shape="circle",
        name="ctrl",
        size=1.0,
        axis="y",
        color=None,
        location=(0, 0, 0),
        group=False,
        collection=None,
        return_nodes=False,
    ):
        """Build a control curve object in *shape*, scaled by *size*, oriented by *axis*, optionally
        colored and wrapped in an offset *group* Empty.

        Args:
            shape (str): A registered shape name (see :meth:`shapes`).
            name (str): Object name.
            size (float): Uniform scale of the unit shape.
            axis (str): ``"x"``/``"y"``/``"z"`` — the world axis the shape's plane-normal points along.
            color: Optional ``(r, g, b[, a])`` viewport object color.
            location: World position of the control (and its group).
            group (bool): If True, create an offset-group Empty at *location* and parent the control
                under it (zeroed relative to the group — the standard rig buffer).
            collection: Target collection (defaults to the active one).
            return_nodes (bool): Return a :class:`ControlNodes`; otherwise just the control object.

        Returns:
            bpy.types.Object | ControlNodes
        """
        import bpy
        from mathutils import Vector

        builder = cls._PRESETS.get(shape)
        if builder is None:
            raise ValueError(
                f"Unknown control shape '{shape}'. Registered: {cls.shapes()}"
            )
        rot = cls._axis_matrix(axis)

        cu = bpy.data.curves.new(name, "CURVE")
        cu.dimensions = "3D"
        for pts, cyclic in builder():
            sp = cu.splines.new("POLY")
            sp.points.add(len(pts) - 1)  # one point exists by default
            for p4, src in zip(sp.points, pts):
                v = rot @ (Vector(src) * size)
                p4.co = (v.x, v.y, v.z, 1.0)
            sp.use_cyclic_u = cyclic

        obj = bpy.data.objects.new(name, cu)
        obj.location = location
        if color is not None:
            obj.color = (color[0], color[1], color[2], color[3] if len(color) > 3 else 1.0)
        (collection or bpy.context.collection).objects.link(obj)

        grp = None
        if group:
            grp = RigUtils.create_locator(
                f"{name}_grp", location=location, display_type="ARROWS", collection=collection
            )
            # matrix_world is lazy: settle it before parent_keep_transform reads grp.matrix_world,
            # else the offset-group binds an identity parent-inverse and the control doubles its
            # offset (the documented matrix_world-is-lazy gotcha).
            bpy.context.view_layer.update()
            RigUtils.parent_keep_transform(obj, grp)

        return ControlNodes(control=obj, group=grp) if return_nodes else obj


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    pass
