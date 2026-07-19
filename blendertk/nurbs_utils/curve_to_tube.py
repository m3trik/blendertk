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

The co-located ``.ui`` is now a byte-identical mirror of mayatk's (same objectNames, same widget
tree, same tooltips) per the mayatk↔blendertk tool-panel parity sweep — cross-host QSettings
collisions between identical objectNames are host-namespaced upstream in uitk, so there is no
longer a reason to diverge the two panels' object trees. Some tooltip text is retained verbatim in
Maya's own vocabulary (e.g. "NURBS curves", "construction history") for textual parity with
mayatk's panel; the engine below is DCC-accurate regardless — it accepts any ``bpy`` ``CURVE``
object and "Keep History" bevels the source curve in place rather than touching Maya-only
construction-history nodes.

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
                target = NurbsUtils.duplicate_curve(source, name=name, link=True)
            target.data.bevel_mode = "ROUND"
            target.data.bevel_depth = float(radius)
            # Degree maps to the round-bevel resolution (1 ≈ faceted, 3 ≈ smooth); Sections scales it.
            target.data.bevel_resolution = 0 if degree <= 1 else max(1, round(sections / 4))
            return target

        # Polygon → a MESH. A bevel_object circle of `sections` points gives exactly `sections`
        # sides; resolution_u the rings; use_fill_caps the caps. The source curve is always
        # preserved (work on a copy); `live` keeps the source as the editable driver vs consumes it.
        work = NurbsUtils.duplicate_curve(source, link=True)
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
    """Switchboard slot wiring for the Curve to Tube panel — structural mirror of mayatk's
    ``CurveToTubeSlots`` (same objectNames, same widget count, same method names) driving
    blendertk's engine through the snapshot/restore :class:`~blendertk.core_utils.preview.Preview`.
    Discovered + served by ``BlenderUiHandler`` (``marking_menu.show("curve_to_tube")``).
    """

    # Mirrors mayatk's PRESERVE_GEOMETRY marker: mayatk's node-diff Preview reads this attr to opt
    # a slot into the duplicate-and-restore path for in-place ops (Curve to Tube resamples/bevels
    # its source curve in place). blendertk's snapshot/restore Preview (core_utils/preview.py)
    # always snapshots every captured object's data regardless of this flag, so it has no
    # behavioral effect here — kept only for documentation parity with mayatk's slot.
    PRESERVE_GEOMETRY = True

    def __init__(self, switchboard, log_level="WARNING"):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.curve_to_tube
        self.last_tubes = []
        self.logger.setLevel(log_level)
        self.logger.set_log_prefix("[curve_to_tube] ")

        # Output-type combo (NURBS / Polygon) drives which options apply.
        self.ui.cmb000.add(list(CurveToTube.OUTPUT_TYPES))
        # Polygon topology: quads vs triangles (was the "Quads" checkbox). Quads is
        # the default, matching the prior checked state.
        self.ui.cmb_topology.add([("Quads", "quads"), ("Triangles", "triangles")])
        self.ui.cmb_topology.setAsCurrent("quads")

        # Per-field reset buttons (uitk option-box): click resets a field to its default;
        # Alt/Ctrl+click bypasses it to default (greyed, restorable). Must precede
        # connect_multi/Preview — wrapping reparents the widgets and invalidates any
        # already-deferred wrapper (see add_reset_buttons docstring).
        self.sb.add_reset_buttons(self.ui)

        # NOTE (Preview architecture divergence): mayatk's Preview is a node-diff CleanupContract
        # that offers validation_func / select_result_checkbox / result_provider hooks so a panel
        # gets "at least one curve selected" gating and Select Result for free. blendertk's Preview
        # (core_utils/preview.py) is snapshot/restore and has no equivalent hooks — the "at least
        # one curve" gate is the engine's own RuntimeError (CurveToTube._curve_objects), surfaced
        # through message_func exactly like an OperationError is in mayatk (see Preview._run /
        # _report), and Select Result is hand-wired at the end of perform_operation below.
        self.preview = Preview(
            self,
            self.ui.chk000,
            self.ui.b000,
            message_func=self.sb.message_box,
            undo_message="Curve to Tube",
        )
        # Re-sweep live as any numeric field changes.
        self.sb.connect_multi(self.ui, "s000-3", "valueChanged", self.preview.refresh)
        # Output type and the poly-only toggles also re-sweep; the combo also enables/disables
        # the options that don't apply to the current type.
        self.ui.cmb000.currentIndexChanged.connect(self.preview.refresh)
        self.ui.cmb000.currentIndexChanged.connect(self._toggle_output_options)
        self.ui.chk001.toggled.connect(self.preview.refresh)  # Caps
        self.ui.cmb_topology.currentIndexChanged.connect(self.preview.refresh)  # Quads/Triangles
        self.ui.chk003.toggled.connect(self.preview.refresh)  # Keep History (live)

        self._toggle_output_options()

        # Footer doubles as a stats readout (triangle count, curve points) once a tube is built;
        # show a hint until then.
        try:
            self.ui.footer.setDefaultStatusText("Select curve(s), then Preview.")
        except Exception:
            pass

    def header_init(self, widget):
        """Configure header help text."""
        from uitk.widgets.mixins.tooltip_mixin import fmt

        widget.set_help_text(
            fmt(
                title="Curve to Tube",
                body="Sweep a circular profile along selected curves to build a tube, output as "
                "a smooth NURBS-style beveled curve or a polygon mesh.",
                steps=[
                    "Select one or more curves.",
                    "Pick an <b>Output</b> type (NURBS or Polygon).",
                    "Set <b>Radius</b> and <b>Sections</b> (sides around).",
                    "For polygon, set <b>Path Res</b> (ring density along the curve) and toggle "
                    "<b>Caps</b> / <b>Quads</b>.",
                    "Toggle <b>Preview</b> to iterate, then <b>Create</b> to commit.",
                ],
                notes=[
                    "<b>Sections</b> sets the profile smoothness for NURBS and the literal number "
                    "of sides for polygon.",
                    "<b>Path Res</b> sets ring density along the path (Blender's "
                    "<i>resolution_u</i>). <b>Path Res</b>, <b>Caps</b>, and <b>Quads</b> apply to "
                    "polygon output only.",
                    "<b>Keep History</b> bevels the source curve in place so it stays the live "
                    "tube (edit the curve and it updates). Blender has no live curve→mesh, so a "
                    "polygon tube is always baked to a mesh — there <b>Keep History</b> only keeps "
                    "the source curve as the editable driver instead of consuming it.",
                    "<b>Select Result</b> selects the finished tube(s) on <b>Create</b> so you can "
                    "see the resulting tessellation.",
                ],
            )
        )

    def _toggle_output_options(self, *_):
        """Enable only the options that apply to the current output type."""
        is_poly = self.ui.cmb000.currentData() == "polygon"
        self.ui.s002.setEnabled(is_poly)  # Path divisions
        self.ui.chk001.setEnabled(is_poly)  # Caps
        self.ui.cmb_topology.setEnabled(is_poly)  # Quads/Triangles
        self.ui.s003.setEnabled(not is_poly)  # Degree (NURBS bevel resolution)

    def b001(self):
        """Reset to Defaults."""
        self.ui.state.reset_all()

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
            quads=self.ui.cmb_topology.currentData() == "quads",
            live=self.ui.chk003.isChecked(),
        )
        # Select Result: not first-class in blendertk's Preview (unlike mayatk's, which owns
        # chk004 via select_result_checkbox/result_provider) — hand-wired here so it (de)selects
        # on every preview build and on commit, matching mayatk's user-visible behavior.
        if self.ui.chk004.isChecked() and self.last_tubes:
            bpy.ops.object.select_all(action="DESELECT")
            for t in self.last_tubes:
                t.select_set(True)
            bpy.context.view_layer.objects.active = self.last_tubes[0]
        self._update_footer()

    def _update_footer(self):
        """Show stats for the last build in the footer (triangle count for a polygon tube,
        point / bevel-resolution readout for a NURBS one — Blender's analogue of mayatk's
        triangle-count / spans readout). Updates live as the preview re-sweeps; clears to the
        default hint when there is no result."""
        import bpy

        try:
            footer = self.ui.footer
        except Exception:
            return
        tubes = []
        for t in self.last_tubes:
            try:
                if t and t.name in bpy.data.objects:
                    tubes.append(t)
            except ReferenceError:
                continue  # a rolled-back/removed bpy object reference
        if not tubes:
            footer.setStatusText("")  # falls back to the default hint
            return
        prefix = f"{len(tubes)} tubes — " if len(tubes) > 1 else ""
        if self.ui.cmb000.currentData() == "polygon":
            tris = 0
            for t in tubes:
                t.data.calc_loop_triangles()
                tris += len(t.data.loop_triangles)
            footer.setStatusText(f"{prefix}{tris:,} tris")
        else:
            info = []
            for t in tubes:
                n_pts = sum((len(s.points) or len(s.bezier_points)) for s in t.data.splines)
                info.append(f"{n_pts} pts, bevel res {t.data.bevel_resolution}")
            footer.setStatusText(f"{prefix}beveled curve · {', '.join(info)}")


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("curve_to_tube", reload=True)
    ui.show(pos="screen", app_exec=True)
