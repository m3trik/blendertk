# !/usr/bin/python
# coding=utf-8
"""Primitive creation -- mirror of mayatk's ``edit_utils.primitives.Primitives``.

So far the Create menu a panel shared by both hosts builds (tentacle's
``slots/edit.py``): :meth:`Primitives.get_primitive_types` lists what Blender
creates, by Maya's categories, and :meth:`Primitives.create` makes one -- a new
object, in Object Mode, selected and active. Maya's ``create_default_primitive``
(sized, matched to the selection) has no twin yet.

``import bpy`` is deferred into the call bodies (no import side effects).
"""

from blendertk.core_utils._core_utils import CoreUtils
from blendertk.node_utils._node_utils import NodeUtils


class _PrimitivesInternal(object):
    """Internal helpers for Primitives."""

    #: Create > Polygon: label -> ``bpy.ops.mesh`` primitive.
    _MESHES = {
        "Cube": "primitive_cube_add",
        "Sphere": "primitive_uv_sphere_add",
        "Ico Sphere": "primitive_ico_sphere_add",
        "Cylinder": "primitive_cylinder_add",
        "Plane": "primitive_plane_add",
        "Circle": "primitive_circle_add",
        "Cone": "primitive_cone_add",
        "Torus": "primitive_torus_add",
        "Grid": "primitive_grid_add",
        "Monkey": "primitive_monkey_add",
    }

    #: Create > NURBS: Maya's NURBS *surfaces* -> ``bpy.ops.surface`` (NURBS curves
    #: are ``bpy.ops.curve``, under Curve).
    _SURFACES = {
        "Sphere": "primitive_nurbs_surface_sphere_add",
        "Cylinder": "primitive_nurbs_surface_cylinder_add",
        "Torus": "primitive_nurbs_surface_torus_add",
        "Circle": "primitive_nurbs_surface_circle_add",
        "Curve": "primitive_nurbs_surface_curve_add",
        "Patch": "primitive_nurbs_surface_surface_add",
    }

    #: Create > Curve: ``bpy.ops.curve`` primitives. Maya's freehand curve *tools*
    #: (EP, CV, Pencil) are modal click-to-place tools, which a menu click that
    #: makes an object does not fit (``bpy.ops.curve.draw`` is that tool).
    _CURVES = {
        "Bezier Curve": "primitive_bezier_curve_add",
        "Bezier Circle": "primitive_bezier_circle_add",
        "Nurbs Curve": "primitive_nurbs_curve_add",
        "Nurbs Circle": "primitive_nurbs_circle_add",
        "Path": "primitive_nurbs_path_add",
    }

    #: Create > Light: ``object.light_add`` types. Blender's lights are exactly
    #: these four: Maya's Ambient and Volume lights have no counterpart, and
    #: Directional is the Sun.
    _LIGHTS = {"Point": "POINT", "Directional": "SUN", "Spot": "SPOT", "Area": "AREA"}

    #: Create > Helper: label -> (the Empty's display, the Maya node it stands for),
    #: or None for Set: a Collection, the closest logical-grouping container to
    #: Maya's ``sets``. Each Empty is typed (``NodeUtils.set_maya_node_type``), so a
    #: locator stays one with children under it and a childless null group is no
    #: locator -- to selection, Suffix By Type and the Maya send.
    _HELPERS = {
        "Locator": ("PLAIN_AXES", "locator"),
        "Null Group": ("ARROWS", "group"),
        "Set": None,
    }

    @staticmethod
    def _add(group, operator, **props):
        """Run a ``bpy.ops.<group>.<operator>`` add operator; the object it made."""
        import bpy

        getattr(getattr(bpy.ops, group), operator)(**props)
        return CoreUtils.active_object()

    @classmethod
    def _add_helper(cls, name):
        """An Empty typed the Maya node it stands for (its own display kept: a null
        group keeps the arrows it is grabbed by), or a linked Collection for Set --
        under ``scene.collection``: ``context.collection`` is None in the Qt pump."""
        import bpy

        spec = cls._HELPERS[name]
        if spec is None:
            collection = bpy.data.collections.new("set1")
            bpy.context.scene.collection.children.link(collection)
            return collection
        display, node_type = spec
        empty = cls._add("object", "empty_add", type=display)
        NodeUtils.set_maya_node_type(empty, node_type, look=False)
        return empty


class Primitives(_PrimitivesInternal):
    """Primitive creation (mirror of mayatk's ``Primitives``)."""

    @classmethod
    def get_primitive_types(cls):
        """The Create menu: ``{category: [label, ...]}`` in menu order, each label an
        item :meth:`create` makes. Mirror of mayatk's ``Primitives.get_primitive_types``
        (Maya's own primitives there)."""
        return {
            "Polygon": list(cls._MESHES),
            "NURBS": list(cls._SURFACES),
            "Curve": list(cls._CURVES),
            "Helper": list(cls._HELPERS),
            "Light": list(cls._LIGHTS),
        }

    @classmethod
    @CoreUtils.undoable
    def create(cls, category, name):
        """Make one Create-menu item (:meth:`get_primitive_types`): a new object,
        selected and active. Mirror of mayatk's ``Primitives.create``.

        Object Mode first: from Edit Mode, Blender's mesh / curve / surface add
        operators merge the new geometry into the mesh being edited instead of
        making an object (and the Empty / light ones poll-fail).

        Returns:
            The object made (a Collection for Set).

        Raises:
            ValueError: *name* is not in *category*.
            RuntimeError: Blender refused (leaving the mode, the operator's poll).
        """
        if name not in cls.get_primitive_types().get(category, ()):
            raise ValueError(f"Create > {category} has no '{name}'.")
        CoreUtils.ensure_object_mode()
        # windowless, the operators and the active read would address the scene's
        # default view layer, not the window's
        with CoreUtils.window_context_override():
            if category == "Helper":
                return cls._add_helper(name)
            if category == "Light":
                return cls._add("object", "light_add", type=cls._LIGHTS[name])
            group, table = {
                "Polygon": ("mesh", cls._MESHES),
                "NURBS": ("surface", cls._SURFACES),
                "Curve": ("curve", cls._CURVES),
            }[category]
            return cls._add(group, table[name])
