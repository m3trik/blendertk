# !/usr/bin/python
# coding=utf-8
"""Procedural draped-cloth (curtain) generator for Blender -- mirror of mayatk's
``edit_utils.curtain``: same classes, same parameters, same drape math (the
vendored ``_curtain_drape``, code-identical with mayatk's copy); only the
selection readers, the mesh build and the rig differ.

- :class:`Rail` -- *rail geometry*: the pure parts (``make`` / ``length`` /
  ``resample`` / ``frames``) from :class:`ptk.Polyline`, plus the Blender
  resolvers (edit-mode mesh edges / a curve object / 2+ object positions).
- :class:`CurtainMesh` -- *deformation*: the :class:`CurtainDrape` engine plus
  the Blender mesh build (a bmesh grid with grid UVs) and post-ops
  (``thickness`` -> applied Solidify, ``reduce`` -> :func:`blendertk.decimate`,
  ``invert`` -> reversed faces, ``soften`` -> smooth shading).
- :class:`CurtainRig` -- *rig*: control handles drive a finished curtain.

:class:`CurtainSlots` (``curtain_slots.py``) is the panel over this engine.

``import bpy`` / ``bmesh`` are deferred into the call bodies so importing this
module to resolve the engine surface never needs a running Blender.
"""

from typing import List, Optional, Sequence, Tuple

import pythontk as ptk

from blendertk.core_utils._core_utils import CoreUtils
from blendertk.edit_utils._edit_utils import EditUtils
from blendertk.edit_utils.curtain._curtain_drape import CurtainDrape

Vec = Tuple[float, float, float]


# ----------------------------------------------------------------------------
# Rail geometry -- ptk.Polyline + the Blender selection readers
# ----------------------------------------------------------------------------


class Rail(ptk.Polyline):
    """Rail-polyline geometry -- the line a curtain hangs from.

    The pure parts come from :class:`ptk.Polyline`; this subclass adds the
    Blender-only resolvers. The cloth engine (:class:`CurtainMesh`) and the rig
    (:class:`CurtainRig`) both consume its output but neither lives here.
    """

    @staticmethod
    def from_selection(objects) -> Optional[Tuple[List[Vec], bool]]:
        """Resolve a rail polyline from a Blender selection.

        Accepts (in priority order, mirroring the Maya resolver) edit-mode
        selected mesh edges (ordered into a path), a curve object, or two-plus
        objects' world positions. Returns ``(points, closed)`` or ``None`` when
        nothing usable is selected.
        """
        import bpy
        import bmesh

        objects = [o for o in ptk.make_iterable(objects) if o]

        active = bpy.context.view_layer.objects.active
        if active and active.type == "MESH" and active.mode == "EDIT":
            bm = bmesh.from_edit_mesh(active.data)
            verts = {v for e in bm.edges if e.select for v in e.verts}
            if len(verts) >= 2:
                pts = [tuple(active.matrix_world @ v.co) for v in verts]
                ordered = ptk.Polyline.order_points(pts)
                return ([tuple(float(c) for c in p) for p in ordered], False)

        for o in objects:
            if o.type == "CURVE":
                pts, closed = Rail.sample_curve(o)
                if len(pts) >= 2:
                    return pts, closed

        if len(objects) >= 2:
            bpy.context.view_layer.update()  # fresh objects may have stale matrices
            return ([tuple(o.matrix_world.translation) for o in objects], False)
        return None

    @staticmethod
    def sample_curve(curve) -> Tuple[List[Vec], bool]:
        """Sample a curve object into a dense world-space polyline.

        Reads the evaluated tessellation (``closed`` from any spline's cyclic
        flag); an un-tessellatable curve falls back to its control points.
        """
        import bpy

        closed = any(s.use_cyclic_u for s in curve.data.splines)
        evaluated = curve.evaluated_get(bpy.context.evaluated_depsgraph_get())
        me = evaluated.to_mesh()
        pts = [tuple(curve.matrix_world @ v.co) for v in me.vertices]
        evaluated.to_mesh_clear()
        if len(pts) < 2:
            pts = Rail._control_points(curve)
        return [tuple(float(c) for c in p) for p in pts], closed

    @staticmethod
    def _control_points(curve) -> List[Vec]:
        """World positions of *curve*'s control points, spline by spline."""
        mw = curve.matrix_world
        return [
            tuple(mw @ (p.co.to_3d() if len(p.co) == 4 else p.co))  # spline pts are 4D
            for s in curve.data.splines
            for p in (s.points if len(s.points) else s.bezier_points)
        ]


# ----------------------------------------------------------------------------
# Curtain generator -- the vendored CurtainDrape engine + the Blender mesh build
# ----------------------------------------------------------------------------


class CurtainMesh(CurtainDrape):
    """Generate a pleated, gravity-draped curtain mesh from a rail polyline.

    The drape math and its parameters are :class:`CurtainDrape`'s (the
    vendored twin of mayatk's -- see it, or mayatk's ``CurtainMesh``, for the
    parameter reference); this subclass adds the Blender mesh build. It
    consumes plain rail points (see :class:`Rail`) and emits a mesh object.
    """

    # Alias so callers can `CurtainMesh.create(rail, **opts)` in one line.
    @classmethod
    def create(cls, rail: Sequence[Vec], **opts):
        return cls(rail, **opts).build()

    @CoreUtils._object_mode
    def build(self):
        """Create the curtain mesh object and return it.

        Returns:
            (bpy.types.Object) the created curtain object.
        """
        import bpy
        import bmesh

        u_segs, v_segs, pts = self.grid_points()
        cols = u_segs + 1

        bm = bmesh.new()
        verts = [bm.verts.new(p) for p in pts]
        uv_of = {
            v: ((i % cols) / u_segs, (i // cols) / v_segs) for i, v in enumerate(verts)
        }
        for r in range(v_segs):
            base = r * cols
            for c in range(u_segs):
                bm.faces.new(
                    (
                        verts[base + c],
                        verts[base + c + 1],
                        verts[base + c + 1 + cols],
                        verts[base + c + cols],
                    )
                )
        uv_layer = bm.loops.layers.uv.new("UVMap")
        for f in bm.faces:
            f.smooth = self.soften
            for loop in f.loops:
                loop[uv_layer].uv = uv_of[loop.vert]
        if self.invert:
            bmesh.ops.reverse_faces(bm, faces=bm.faces)

        mesh = bpy.data.meshes.new(self.name)
        bm.to_mesh(mesh)
        bm.free()
        obj = bpy.data.objects.new(self.name, mesh)
        bpy.context.collection.objects.link(obj)
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)

        if self.thickness > 0:
            mod = obj.modifiers.new(name="Solidify", type="SOLIDIFY")
            mod.thickness = self.thickness
            mod.offset = 1.0  # shell outward, matching Maya's face extrude
            EditUtils._apply_modifier(obj, mod.name)
        if self.reduce > 0:
            import blendertk as btk

            btk.decimate(obj, percentage=self.reduce)
        return obj


class CurtainUtils:
    """Retired entry point: :class:`Rail` and :class:`CurtainMesh`, mayatk's names."""

    @staticmethod
    @ptk.Deprecation.symbol(
        "Rail.from_selection", remove_in="0.14.0", since="2026-09-26"
    )
    def curtain_rail_from_selection(objects):
        """Moved to :meth:`Rail.from_selection`."""
        return Rail.from_selection(objects)

    @staticmethod
    @ptk.Deprecation.symbol(
        "CurtainMesh.create", remove_in="0.14.0", since="2026-09-26"
    )
    def create_curtain(rail, name="curtain", **options):
        """Moved to :meth:`CurtainMesh.create`."""
        return CurtainMesh.create(rail, name=name, **options)


# ----------------------------------------------------------------------------
# Rig (control handles drive a finished curtain) — Blender mirror of CurtainRig
# ----------------------------------------------------------------------------


class CurtainRig:
    """Make grabbable control handles drive a finished curtain — Blender mirror of mayatk's
    :class:`CurtainRig`.

    Maya rigs the cloth with three pieces: a driver **NURBS curve**, a **wire deformer** binding
    the curve to the curtain (its ``dropoffDistance`` sets how far the pull reaches into the drop),
    and per-CV **cluster** handles to grab. **Blender has no wire deformer and no curve→mesh
    proximity deform**, so the faithful analogue collapses all three into the native **Hook
    modifier with smooth falloff**:

    - the hook ``falloff_radius`` **is** Maya's wire ``dropoffDistance`` (how far the pull bleeds
      into the cloth), with ``falloff_type='SMOOTH'`` standing in for the wire's smooth dropoff;
    - the **control Empties** collapse Maya's curve-CVs *and* its clusters into one grabbable
      handle per pin — animate by moving an empty and the cloth follows with a smooth localized
      pull, exactly like dragging a cluster on a wire-driven rail;
    - a root **Empty** parents the controls (and the curtain) — the analogue of Maya's rig group.

    So Maya's two build steps (``_add_wire`` + ``_add_clusters``) **fuse** into one
    control-empty-plus-hook step (:meth:`_add_hook`); there is no separate driver curve or hidden
    base wire to hide/group. The hook-bind math reuses the proven form from ``DynamicPipe``
    (``matrix_inverse = control.matrix_world.inverted() @ curtain.matrix_world`` → identity deform
    at bind, so the cloth doesn't jump).

    Decoupled from the drape (:class:`CurtainMesh`) so it attaches to *any* curtain
    mesh, like Maya's ``CurtainRig`` taking any mesh + any curve.
    """

    @staticmethod
    def attach(curtain, controls=5, dropoff=2.0, name=None):
        """Rig *curtain* with control-empty handles that pull the cloth via hooks.

        Args:
            curtain (bpy.types.Object): The finished curtain mesh to rig.
            controls: Control sources — an ``int`` (auto-place that many handles evenly along the
                rail/top edge; pass the curtain's ``hanging_points`` for one handle per pleat), a
                Blender **curve object** (use its control-point world positions, mirroring Maya's
                per-CV clusters), or an explicit sequence of ``(x, y, z)`` world positions.
            dropoff (float): How far each control's pull reaches into the cloth (the hook
                ``falloff_radius`` — Maya's wire ``dropoffDistance``).
            name (str): Base name for the rig root empty (default ``<curtain>_rig``).

        Returns:
            bpy.types.Object: The rig root empty (parents the controls + the curtain — the group).
        """
        import bpy

        if not (getattr(curtain, "data", None) and curtain.data.vertices):
            raise ValueError("CurtainRig.attach requires a curtain mesh with vertices.")
        dropoff = float(dropoff)
        name = name or f"{curtain.name}_rig"
        collection = (
            curtain.users_collection[0]
            if curtain.users_collection
            else bpy.context.collection
        )

        root = bpy.data.objects.new(name, None)
        root.empty_display_type = "ARROWS"
        collection.objects.link(root)

        positions = CurtainRig._resolve_controls(curtain, controls)
        empties = []
        for i, pos in enumerate(positions):
            e = bpy.data.objects.new(f"{curtain.name}_ctrl_{i}", None)
            e.empty_display_type = "SPHERE"
            e.empty_display_size = max(dropoff * 0.15, 0.1)
            e.location = pos
            collection.objects.link(e)
            CurtainRig._parent_keep_world(e, root)
            empties.append(e)
        # The curtain joins the group too; parent BEFORE binding so the hook matrix_inverse is
        # computed against the curtain's final (parented) matrix_world — rigid root motion then
        # cancels out (controls + curtain move together → identity hook deform → clean translate).
        CurtainRig._parent_keep_world(curtain, root)
        bpy.context.view_layer.update()  # settle empty/curtain matrices before binding

        for e, pos in zip(empties, positions):
            CurtainRig._add_hook(curtain, e, pos, dropoff)
        bpy.context.view_layer.update()  # settle the hooked vertices
        return root

    @staticmethod
    def _resolve_controls(curtain, controls):
        """Resolve *controls* (int | curve object | positions) to a list of world positions."""
        if getattr(controls, "type", None) == "CURVE":
            return Rail._control_points(controls)
        if isinstance(controls, int):
            n = max(1, controls)
            rail = CurtainRig._rail_edge(curtain)
            if not rail:
                return []
            if n == 1:
                return [rail[len(rail) // 2]]
            return Rail.resample(rail, n) if len(rail) >= 2 else rail
        return [tuple(float(c) for c in p) for p in controls]

    @staticmethod
    def _rail_edge(curtain):
        """Ordered world positions of the rail (top) edge the controls sit on.

        The ``CurtainDrape`` engine hangs the cloth in **-Y** (gravity axis; see
        :meth:`CurtainMesh.build`), so the rail is the band of verts near the maximum Y. Ordered
        along the rail's length with the shared path sorter (handles a bowed/curved rail).
        """
        mw = curtain.matrix_world
        world = [mw @ v.co for v in curtain.data.vertices]
        ys = [p.y for p in world]
        top, bot = max(ys), min(ys)
        band = top - 0.15 * ((top - bot) or 1.0)
        rail = [tuple(p) for p in world if p.y >= band]
        return Rail.order_points(rail) if len(rail) >= 2 else rail

    @staticmethod
    def _add_hook(curtain, control, pos, dropoff):
        """Fused wire+cluster step: a Hook modifier binds *control* to the cloth verts within
        *dropoff* of *pos*, with a smooth falloff = the wire dropoff. Returns the modifier."""
        from mathutils import Vector

        mod = curtain.modifiers.new(name=f"Hook_{control.name}", type="HOOK")
        mod.object = control
        mod.falloff_type = "SMOOTH"
        mod.falloff_radius = dropoff

        mw = curtain.matrix_world
        cpos = Vector(pos)
        dists = [
            (i, (mw @ v.co - cpos).length) for i, v in enumerate(curtain.data.vertices)
        ]
        # verts within the dropoff reach; if none (tiny dropoff) grab the single nearest so the
        # handle still bites the cloth.
        idx = [i for i, d in dists if d <= dropoff] or [
            min(dists, key=lambda t: t[1])[0]
        ]
        mod.vertex_indices_set(idx)
        # center is in the curtain's LOCAL space; matrix_inverse cancels the bind (identity deform).
        mod.center = mw.inverted() @ cpos
        mod.matrix_inverse = EditUtils.hook_bind_inverse(control, curtain)
        return mod

    @staticmethod
    def _parent_keep_world(child, parent):
        """Parent *child* under *parent* preserving its world transform (parent-inverse bind)."""
        child.parent = parent
        child.matrix_parent_inverse = parent.matrix_world.inverted()
