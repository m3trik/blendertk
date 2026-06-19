# !/usr/bin/python
# coding=utf-8
"""Shared curve helpers — Blender mirror of mayatk's ``nurbs_utils.NurbsUtils`` namespace.

Maya's ``NurbsUtils`` is a thick layer over ``cmds`` NURBS commands (loft / planarSrf / nurbsToPoly
/ extrude / MASH). Blender ships those capabilities as object properties (curve ``bevel_depth`` /
``fill_mode``) + one evaluated-mesh bake, so this holds only the **two primitives the curve tools
share**: build a curve object from a point list, and bake a curve (its evaluated bevel / 2D-fill)
to a mesh. Tool-specific behaviour (image-contour tracing, tube bevel/RDP) lives in each tool module.

``import bpy`` is deferred into the call bodies (no import side effects).
"""
import pythontk as ptk


class NurbsUtils(ptk.LoggingMixin):
    """Shared Blender curve primitives (mirror of mayatk's ``NurbsUtils``)."""

    @staticmethod
    def add_spline(curve, points, cyclic=False, kind="POLY"):
        """Append a spline of ``points`` (each an ``(x, y, z)``) to an existing curve.

        Parameters:
            curve (bpy.types.Object | bpy.types.Curve): The curve object or its data.
            points (list): ``(x, y, z)`` control points.
            cyclic (bool): Close the spline (``use_cyclic_u``).
            kind (str): ``"POLY"`` (linear — Blender's analogue of Maya ``degree=1``) or ``"NURBS"``.

        Returns:
            bpy.types.Spline: The new spline.
        """
        import bpy

        cu = curve.data if isinstance(curve, bpy.types.Object) else curve
        pts = [tuple(p) for p in points]
        if not pts:
            raise ValueError("add_spline requires at least one point.")
        spline = cu.splines.new(kind)
        spline.points.add(len(pts) - 1)  # a fresh spline starts with one point
        for sp_pt, p in zip(spline.points, pts):
            sp_pt.co = (p[0], p[1], p[2], 1.0)
        if kind == "NURBS":
            spline.order_u = min(4, len(pts))  # ≤ point count
            spline.use_endpoint_u = True  # pass through the endpoints
        spline.use_cyclic_u = bool(cyclic)
        return spline

    @classmethod
    def create_curve(cls, points, name="curve", cyclic=False, kind="POLY",
                     dimensions="3D", link=True, collection=None):
        """Build a curve object from a point list — mirror of mayatk's ``cmds.curve`` usage.

        Parameters:
            points (list): ``(x, y, z)`` control points for the (single) spline.
            name (str): Object/data name.
            cyclic (bool): Close the spline.
            kind (str): ``"POLY"`` or ``"NURBS"``.
            dimensions (str): ``"3D"`` or ``"2D"`` (2D enables planar fill).
            link (bool): Link the object into a collection.
            collection (bpy.types.Collection): Target collection (else the active one).

        Returns:
            bpy.types.Object: The curve object.
        """
        import bpy

        cu = bpy.data.curves.new(name, "CURVE")
        cu.dimensions = dimensions
        cls.add_spline(cu, points, cyclic=cyclic, kind=kind)
        obj = bpy.data.objects.new(name, cu)
        if link:
            (collection or bpy.context.collection).objects.link(obj)
        return obj

    @staticmethod
    def curve_to_mesh(curve_obj, name=None, link=True, keep_curve=False, collection=None):
        """Bake a curve object's **evaluated** geometry (its bevel sweep / 2D fill) to a new mesh
        object — Blender's analogue of Maya's ``nurbsToPoly``.

        Parameters:
            curve_obj (bpy.types.Object): The (beveled or 2D-fill) curve object.
            name (str): Mesh object name (defaults to the curve's name).
            link (bool): Link the mesh object into a collection.
            keep_curve (bool): Leave the source curve in the scene (else remove it + purge its
                orphaned curve datablock, mirroring the ImageToPlane orphan purge).
            collection (bpy.types.Collection): Target collection (else the curve's own, else active).

        Returns:
            bpy.types.Object: The new mesh object.
        """
        import bpy

        name = name or curve_obj.name
        target = collection or (
            curve_obj.users_collection[0]
            if curve_obj.users_collection
            else bpy.context.collection
        )
        deps = bpy.context.evaluated_depsgraph_get()
        me = bpy.data.meshes.new_from_object(curve_obj.evaluated_get(deps))
        me.name = name
        mesh_obj = bpy.data.objects.new(name, me)
        if link:
            target.objects.link(mesh_obj)
        if not keep_curve:
            cu_data = curve_obj.data
            bpy.data.objects.remove(curve_obj, do_unlink=True)
            if cu_data.users == 0:  # purge the now-orphaned curve datablock
                bpy.data.curves.remove(cu_data)
        return mesh_obj


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    pass
