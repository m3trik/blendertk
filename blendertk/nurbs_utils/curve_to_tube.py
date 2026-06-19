# !/usr/bin/python
# coding=utf-8
"""Curve to Tube tool — Blender port of mayatk's ``nurbs_utils.curve_to_tube``.

Sweep a circular profile along curve(s) to build a tube. Maya needs a whole command chain for this
(``extrude`` → ``nurbsToPoly`` → ``curveWarp`` deformer → RDP ring placement → normal-conform);
**Blender ships it natively** as the curve's own **bevel**, so this port is a thin wrapper over
curve properties + the shared :meth:`~blendertk.nurbs_utils._nurbs_utils.NurbsUtils.curve_to_mesh`
bake:

  * **NURBS Tube** → a **beveled curve** (``bevel_mode='ROUND'``) — smooth and inherently *live*
    (edit the curve and the tube updates), Blender's analogue of Maya's NURBS surface.
  * **Polygon Tube** → a **mesh**: a ``bevel_object`` profile **circle of exactly ``sections``
    points** gives exactly ``sections`` sides, ``resolution_u`` the ring density, ``use_fill_caps``
    the caps; baked to a mesh via ``curve_to_mesh``.

Divergences (documented for parity — see ``tentacle/docs/PARITY_PORTING_PLAN.md``):
  * **``output_type`` decides the result type** (Blender unifies curve+surface): ``nurbs`` → a live
    beveled curve, ``polygon`` → a baked mesh. The **source curve is always preserved** (Maya does
    too). **``live`` (Keep History)** is honored for ``nurbs`` (in-place on the source = the curve
    *is* the live tube, vs a beveled duplicate); for ``polygon`` Blender has **no live curve→mesh**,
    so the mesh is always baked and ``live`` only keeps the source curve as the editable driver.
  * **No RDP curvature ring placement / curveWarp / normal-conform** — Blender's bevel produces
    uniform rings (``resolution_u`` = Path Res) with consistent outward normals natively. ``Degree``
    maps to the round-bevel resolution (1 ≈ faceted, 3 ≈ smooth); ``Sections`` scales it for NURBS.
  * ``import bpy`` / ``bmesh`` deferred into the call bodies; the Qt-only ``uitk`` ``fmt`` into its method.
"""
import math

import pythontk as ptk

from blendertk.core_utils.preview import Preview
from blendertk.nurbs_utils._nurbs_utils import NurbsUtils


class CurveToTube(ptk.LoggingMixin):
    """Sweep a circular profile along curve(s) to build a tube — Blender mirror of mayatk's
    ``CurveToTube``. Stateless: every entry point is a ``classmethod`` returning plain objects."""

    # (label, value) pairs for the UI combo and for input validation (mirror of mayatk).
    OUTPUT_TYPES = (("NURBS Tube", "nurbs"), ("Polygon Tube", "polygon"))

    @classmethod
    def create(cls, curves, output_type="nurbs", radius=1.0, sections=8, path_divisions=1,
               degree=3, caps=True, quads=True, live=False, name="tube"):
        """Build a tube along each given curve.

        Parameters:
            curves (list): Curve object(s) to sweep along (non-curve selections are ignored).
            output_type (str): ``"nurbs"`` → a beveled curve (smooth, live), ``"polygon"`` → a mesh.
            radius (float): Radius of the swept profile.
            sections (int): Sides around the profile — exact for polygon, round-bevel smoothness
                for NURBS.
            path_divisions (int): Polygon only — ring density along the path (``resolution_u``).
            degree (int): NURBS only — round-bevel resolution (1 ≈ faceted, 3 ≈ smooth).
            caps (bool): Polygon only — fill the open ends (``use_fill_caps``).
            quads (bool): Polygon only — keep quads (True) or triangulate (False).
            live (bool): NURBS — bevel the source curve in place (it *is* the live tube) vs a
                beveled duplicate. Polygon — keep the source curve as the editable driver vs consume it.
            name (str): Base name for the created tube.

        Returns:
            list: The created tube object(s) (curves for ``nurbs``, meshes for ``polygon``).
        """
        if output_type not in ("nurbs", "polygon"):
            raise ValueError(f"Unknown output_type: {output_type!r}")
        sources = cls._curve_objects(curves)
        if not sources:
            raise RuntimeError(
                "No curve selected — select one or more curve objects to sweep along."
            )
        return [
            cls._build_one(
                src, output_type, radius, max(3, int(sections)), max(1, int(path_divisions)),
                int(degree), bool(caps), bool(quads), bool(live), name,
            )
            for src in sources
        ]

    # ----------------------------------------------------------- internals
    @staticmethod
    def _curve_objects(curves):
        """Resolve the input to a de-duplicated list of curve objects."""
        seen, result = set(), []
        for o in ptk.make_iterable(curves):
            if o and getattr(o, "type", None) == "CURVE" and o.name not in seen:
                seen.add(o.name)
                result.append(o)
        return result

    @staticmethod
    def _duplicate(source):
        """A linked-collection duplicate of ``source`` with its own copied curve data."""
        dup = source.copy()
        dup.data = source.data.copy()
        for c in (source.users_collection or []):
            c.objects.link(dup)
        return dup

    @staticmethod
    def _profile_circle(radius, sections, name):
        """A faceted (POLY) circle of exactly ``sections`` points → the polygon tube's exact sides."""
        n = max(3, int(sections))
        r = float(radius)
        pts = [
            (r * math.cos(2 * math.pi * i / n), r * math.sin(2 * math.pi * i / n), 0.0)
            for i in range(n)
        ]
        return NurbsUtils.create_curve(pts, name=f"{name}_profile", cyclic=True, kind="POLY")

    @classmethod
    def _build_one(cls, source, output_type, radius, sections, path_divisions, degree, caps,
                   quads, live, name):
        """Build one tube along one curve; return the finished object."""
        import bpy

        if output_type == "nurbs":
            # NURBS → a beveled CURVE (round, smooth, live). live = bevel the source in place (the
            # curve IS the tube); baked = bevel a duplicate (source preserved). Only the duplicate is
            # renamed — renaming the in-place source would make the Preview's name-based rollback
            # treat it as a new object (remove + recreate) instead of cleanly restoring its data.
            if live:
                target = source
            else:
                target = cls._duplicate(source)
                target.name = name
            target.data.bevel_mode = "ROUND"
            target.data.bevel_depth = float(radius)
            # Degree maps to the round-bevel resolution (1 ≈ faceted, 3 ≈ smooth); Sections scales it.
            target.data.bevel_resolution = 0 if degree <= 1 else max(1, round(sections / 4))
            return target

        # Polygon → a MESH. A bevel_object circle of `sections` points gives exactly `sections`
        # sides; resolution_u the rings; use_fill_caps the caps. The source curve is always
        # preserved (work on a copy); `live` keeps the source as the editable driver vs consumes it.
        work = cls._duplicate(source)
        profile = cls._profile_circle(radius, sections, name)
        work.data.bevel_mode = "OBJECT"
        work.data.bevel_object = profile
        work.data.resolution_u = path_divisions
        work.data.use_fill_caps = bool(caps)
        mesh = NurbsUtils.curve_to_mesh(work, name=name, keep_curve=False)  # consumes the copy
        if profile.users_collection:
            bpy.data.objects.remove(profile, do_unlink=True)
        if not quads:
            cls._triangulate(mesh)
        if not live:
            bpy.data.objects.remove(source, do_unlink=True)  # baked: consume the source curve
        return mesh

    @staticmethod
    def _triangulate(mesh_obj):
        """Triangulate a mesh in place (the Quads-off path) — bmesh, no view context needed."""
        import bmesh

        bm = bmesh.new()
        bm.from_mesh(mesh_obj.data)
        bmesh.ops.triangulate(bm, faces=bm.faces[:])
        bm.to_mesh(mesh_obj.data)
        bm.free()


class CurveToTubeSlots(ptk.LoggingMixin):
    """Switchboard slot wiring for the Curve to Tube panel (hermetic Preview), mirror of mayatk's
    ``CurveToTubeSlots``. Discovered + served by ``BlenderUiHandler``
    (``marking_menu.show("curve_to_tube")``).
    """

    # A live NURBS tube bevels the source curve in place; tell the Preview to snapshot the selected
    # curve(s) so rollback restores them (the blendertk Preview snapshots object data by default).
    PRESERVE_GEOMETRY = True

    def __init__(self, switchboard, log_level="WARNING"):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.curve_to_tube
        self.last_tubes = []
        self.logger.setLevel(log_level)
        self.logger.set_log_prefix("[curve_to_tube] ")

        self.ui.cmb000.add(list(CurveToTube.OUTPUT_TYPES))

        # Per-field reset buttons must precede connect_multi/Preview (wrap-first — reparenting a
        # spinbox after a deferred-widget access invalidates its wrapper).
        self.sb.add_reset_buttons(self.ui)

        self.preview = Preview(
            self,
            self.ui.chk000,
            self.ui.b000,
            message_func=self.sb.message_box,
            undo_message="Curve to Tube",
        )

        self.sb.connect_multi(self.ui, "s000-3", "valueChanged", self.preview.refresh)
        self.ui.cmb000.currentIndexChanged.connect(self.preview.refresh)
        self.ui.cmb000.currentIndexChanged.connect(self._toggle_output_options)
        self.ui.chk001.toggled.connect(self.preview.refresh)  # Caps
        self.ui.chk002.toggled.connect(self.preview.refresh)  # Quads
        self.ui.chk003.toggled.connect(self.preview.refresh)  # Keep History (live)

        self._toggle_output_options()

    def header_init(self, widget):
        """Configure header help text."""
        from uitk.widgets.mixins.tooltip_mixin import fmt

        widget.set_help_text(
            fmt(
                title="Curve to Tube",
                body="Sweep a circular profile along selected curves to build a tube, as a smooth "
                "NURBS-style beveled curve or a polygon mesh.",
                steps=[
                    "Select one or more curves.",
                    "Pick an <b>Output</b> type (NURBS or Polygon).",
                    "Set <b>Radius</b> and <b>Sections</b> (sides around).",
                    "For polygon, set <b>Path Res</b> (ring density) and toggle <b>Caps</b> / "
                    "<b>Quads</b>.",
                    "Toggle <b>Preview</b> to iterate, then <b>Create</b> to commit.",
                ],
                notes=[
                    "<b>NURBS</b> output stays an editable, smooth <i>beveled curve</i> (Blender's "
                    "live surface); <b>Polygon</b> bakes a mesh with exactly <b>Sections</b> sides.",
                    "<b>Keep History</b> (NURBS): bevel the source curve in place so it stays the "
                    "live tube. Blender has no live curve→mesh, so a polygon tube is always baked.",
                    "<b>Path Res</b>, <b>Caps</b>, <b>Quads</b> apply to polygon output only.",
                ],
            )
        )

    def _toggle_output_options(self, *_):
        """Enable only the options that apply to the current output type."""
        is_poly = self.ui.cmb000.currentData() == "polygon"
        self.ui.s002.setEnabled(is_poly)   # Path divisions
        self.ui.chk001.setEnabled(is_poly)  # Caps
        self.ui.chk002.setEnabled(is_poly)  # Quads
        self.ui.s003.setEnabled(not is_poly)  # Degree (NURBS)

    def b001(self):
        """Reset to Defaults."""
        try:
            self.ui.state.reset_all()
        except Exception:
            pass

    def perform_operation(self, objects):
        """Build the tube(s) from the selected curves (Preview entry point)."""
        import bpy

        self.last_tubes = CurveToTube.create(
            objects,
            output_type=self.ui.cmb000.currentData(),
            radius=self.ui.s000.value(),
            sections=self.ui.s001.value(),
            path_divisions=self.ui.s002.value(),
            degree=self.ui.s003.value(),
            caps=self.ui.chk001.isChecked(),
            quads=self.ui.chk002.isChecked(),
            live=self.ui.chk003.isChecked(),
        )
        if self.ui.chk004.isChecked() and self.last_tubes:
            bpy.ops.object.select_all(action="DESELECT")
            for t in self.last_tubes:
                t.select_set(True)
            bpy.context.view_layer.objects.active = self.last_tubes[0]


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("curve_to_tube", reload=True)
    ui.show(pos="screen", app_exec=True)
