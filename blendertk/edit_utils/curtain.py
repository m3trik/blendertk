# !/usr/bin/python
# coding=utf-8
"""Curtain (draped-cloth) generation — the Blender build over the shared
``ptk.CurtainDrape`` engine (mirror of mayatk's ``edit_utils.curtain``: same
parameters, same drape math — only the mesh build and post-ops differ).

``create_curtain`` builds a grid mesh from :meth:`ptk.CurtainDrape.grid_points`
and owns the Blender post-ops (``thickness`` → applied Solidify, ``reduce`` →
:func:`blendertk.decimate`, ``invert`` → reversed faces, ``soften`` → smooth
shading). ``curtain_rail_from_selection`` is the Blender counterpart of
mayatk's ``Rail.from_selection`` (edit-mode mesh edges / a curve object / 2+
object positions).

:class:`CurtainSlots` is the Switchboard slot wiring for the co-located
``curtain.ui`` panel — it lives here next to the engine it drives (mirror of
mayatk's ``CurtainSlots``), discovered and served by ``BlenderUiHandler`` so the
panel shows via ``marking_menu.show("curtain")`` from a tentacle nav button.

``import bpy`` / ``bmesh`` (and the Qt-only ``uitk`` helpers) are deferred into the call
bodies so importing this module to resolve the engine surface never needs a running
Blender *or* a Qt binding — Blender's headless interpreter (``--background``) ships
neither (unlike mayapy, which bundles PySide6, so mayatk can import uitk at module top).
"""
from pathlib import Path

import pythontk as ptk

from blendertk.core_utils._core_utils import _object_mode, selected_objects
from blendertk.core_utils.preview import Preview
from blendertk.edit_utils._edit_utils import _apply_modifier, hook_bind_inverse
from blendertk.xform_utils._xform_utils import get_world_bbox

# Shipped, read-only curtain presets (UI-state snapshots). Identical to mayatk's — the panel
# shares the Maya widget names AND the ptk.CurtainDrape engine, so a preset drapes the same.
_PRESETS_DIR = Path(__file__).resolve().parent / "presets" / "curtain"


def curtain_rail_from_selection(objects):
    """Resolve a rail polyline from a Blender selection.

    Accepts (in priority order, mirroring the Maya resolver) edit-mode selected
    mesh edges (ordered into a path), a curve object (sampled via its evaluated
    tessellation; ``closed`` from the spline's cyclic flag), or two-plus
    objects' world positions. Returns ``(points, closed)`` or None when nothing
    usable is selected.
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
        if o.type != "CURVE":
            continue
        closed = any(s.use_cyclic_u for s in o.data.splines)
        depsgraph = bpy.context.evaluated_depsgraph_get()
        evaluated = o.evaluated_get(depsgraph)
        me = evaluated.to_mesh()
        pts = [tuple(o.matrix_world @ v.co) for v in me.vertices]
        evaluated.to_mesh_clear()
        if len(pts) < 2:  # un-tessellatable curve — fall back to control points
            pts = []
            for s in o.data.splines:
                for p in s.points if len(s.points) else s.bezier_points:
                    co = p.co.to_3d() if len(p.co) == 4 else p.co  # spline pts are 4D
                    pts.append(tuple(o.matrix_world @ co))
        if len(pts) >= 2:
            return ([tuple(float(c) for c in p) for p in pts], closed)

    if len(objects) >= 2:
        bpy.context.view_layer.update()  # fresh objects may have stale matrices
        return ([tuple(o.matrix_world.translation) for o in objects], False)
    return None


@_object_mode
def create_curtain(rail, name="curtain", **options):
    """Create a pleated, gravity-draped curtain mesh from a rail polyline.

    The drape math is :class:`ptk.CurtainDrape` (shared with mayatk's
    ``CurtainMesh`` — see it for the parameter reference); this builds the grid
    mesh from :meth:`grid_points` with grid UVs, then applies the post-ops:
    ``thickness`` (applied Solidify shell), ``reduce`` (percent decimated),
    ``invert`` (reversed normals), ``soften`` (smooth shading).

    Returns:
        (bpy.types.Object) the created curtain object.
    """
    import bpy
    import bmesh

    drape = ptk.CurtainDrape(rail, name=name, **options)
    u_segs, v_segs, pts = drape.grid_points()
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
        f.smooth = drape.soften
        for loop in f.loops:
            loop[uv_layer].uv = uv_of[loop.vert]
    if drape.invert:
        bmesh.ops.reverse_faces(bm, faces=bm.faces)

    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)

    if drape.thickness > 0:
        mod = obj.modifiers.new(name="Solidify", type="SOLIDIFY")
        mod.thickness = drape.thickness
        mod.offset = 1.0  # shell outward, matching Maya's face extrude
        _apply_modifier(obj, mod.name)
    if drape.reduce > 0:
        import blendertk as btk

        btk.decimate(obj, percentage=drape.reduce)
    return obj


class CurtainUtils:
    """Namespace mirror of mayatk's curtain module (helpers also exposed module-level)."""

    create_curtain = staticmethod(create_curtain)
    curtain_rail_from_selection = staticmethod(curtain_rail_from_selection)


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

    Decoupled from the drape (:func:`create_curtain`) so it attaches to *any* curtain mesh, like
    Maya's ``CurtainRig`` taking any mesh + any curve.
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
            mw = controls.matrix_world
            pts = []
            for s in controls.data.splines:
                src = s.points if len(s.points) else s.bezier_points
                for p in src:
                    co = p.co.to_3d() if len(p.co) == 4 else p.co  # spline pts are 4D
                    pts.append(tuple(mw @ co))
            return pts
        if isinstance(controls, int):
            n = max(1, controls)
            rail = CurtainRig._rail_edge(curtain)
            if not rail:
                return []
            if n == 1:
                return [rail[len(rail) // 2]]
            return ptk.Polyline.resample(rail, n) if len(rail) >= 2 else rail
        return [tuple(float(c) for c in p) for p in controls]

    @staticmethod
    def _rail_edge(curtain):
        """Ordered world positions of the rail (top) edge the controls sit on.

        The shared ``ptk.CurtainDrape`` hangs the cloth in **-Y** (gravity axis; see
        :func:`create_curtain`), so the rail is the band of verts near the maximum Y. Ordered
        along the rail's length with the shared path sorter (handles a bowed/curved rail).
        """
        mw = curtain.matrix_world
        world = [mw @ v.co for v in curtain.data.vertices]
        ys = [p.y for p in world]
        top, bot = max(ys), min(ys)
        band = top - 0.15 * ((top - bot) or 1.0)
        rail = [tuple(p) for p in world if p.y >= band]
        return ptk.Polyline.order_points(rail) if len(rail) >= 2 else rail

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
        dists = [(i, (mw @ v.co - cpos).length) for i, v in enumerate(curtain.data.vertices)]
        # verts within the dropoff reach; if none (tiny dropoff) grab the single nearest so the
        # handle still bites the cloth.
        idx = [i for i, d in dists if d <= dropoff] or [min(dists, key=lambda t: t[1])[0]]
        mod.vertex_indices_set(idx)
        # center is in the curtain's LOCAL space; matrix_inverse cancels the bind (identity deform).
        mod.center = mw.inverted() @ cpos
        mod.matrix_inverse = hook_bind_inverse(control, curtain)
        return mod

    @staticmethod
    def _parent_keep_world(child, parent):
        """Parent *child* under *parent* preserving its world transform (parent-inverse bind)."""
        child.parent = parent
        child.matrix_parent_inverse = parent.matrix_world.inverted()


# ----------------------------------------------------------------------------
# UI slots
# ----------------------------------------------------------------------------


class CurtainSlots(ptk.LoggingMixin):
    """Switchboard slot wiring for the curtain UI (live preview + rail resolution).

    Blender port of mayatk's ``CurtainSlots``: the drape math is the shared
    :class:`ptk.CurtainDrape` engine, so every parameter behaves identically across
    DCCs — only the mesh build differs (``create_curtain``, bmesh). The rail resolves
    from the selection (edit-mode edges / a curve object / 2+ object positions) or is
    generated from the Rail fields. Live preview via :class:`blendertk.Preview`, and the same
    in-panel **preset combo** as Maya (shared ``uitk.PresetManager`` + the identical built-in
    presets). The wire-deformer rig is the :class:`CurtainRig` engine class (Maya's
    ``CurtainRig``); like Maya it is **not** wired into this panel — it is an engine-level
    capability with no tentacle nav button (Maya exposes it the same way).

    Self-contained (``ptk.LoggingMixin`` only) so blendertk carries no back-dependency
    on tentacle — the selection comes from ``btk.selected_objects``, not a tentacle base.
    """

    def __init__(self, switchboard, log_level="WARNING"):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.curtain
        self.logger.setLevel(log_level)
        self.logger.set_log_prefix("[curtain] ")
        self._created = None  # last curtain object name (Select Result)
        self.presets = None  # in-panel PresetManager (wired in cmb000_init)

        # Per-field reset buttons must precede connect_multi/Preview.
        self.sb.add_reset_buttons(self.ui)

        self.preview = Preview(
            self,
            self.ui.chk000,
            self.ui.b000,
            finalize_func=self._finalize,
            message_func=self.sb.message_box,
            undo_message="Create Curtain",
        )

        self.sb.connect_multi(self.ui, "s000-27", "valueChanged", self.preview.refresh)
        self.sb.connect_multi(self.ui, "chk001,chk004", "clicked", self.preview.refresh)

    def header_init(self, widget):
        """Configure header help text."""
        from uitk.widgets.mixins.tooltip_mixin import fmt

        widget.set_help_text(
            fmt(
                title="Curtain Generator",
                body="Drape a procedural pleated, gravity-draped (catenary) curtain "
                "from a rail — a selected curve / edit-mode edges / 2+ objects, or "
                "a generated straight (optionally bowed) rail.",
                steps=[
                    "Optionally select a rail source (curve, edges, or objects).",
                    "Toggle <b>Preview</b> — the curtain re-drapes live as you dial.",
                    "Set pleats via <b>Hanging Points</b>; sag via <b>Gravity</b>.",
                    "Press <b>Create</b> to commit.",
                ],
                sections=[
                    ("Notes", [
                        "With nothing selected, the Rail fields generate the rail.",
                        "Same engine as the Maya panel — identical settings drape "
                        "identically.",
                    ]),
                ],
            )
        )

    def cmb000_init(self, widget):
        """Wire the in-panel preset selector (built-in + user tiers) — mirror of the Maya panel.

        A curtain preset is a UI-state snapshot of the drape fields; because the panel shares the
        Maya widget names *and* the shared ``ptk.CurtainDrape`` engine, the built-in JSONs are
        identical across DCCs (shipped in ``edit_utils/presets/curtain``). Loading a preset re-drapes
        the live preview."""
        # Wrap construction + wiring (not just the import) so any failure degrades to "no presets,
        # panel still works" with a clean warning rather than a raw slot-init error.
        try:
            from uitk.widgets.mixins.preset_manager import PresetManager

            self.presets = PresetManager(
                parent=self.ui,
                state=self.ui.state,
                preset_dir="blendertk/curtain",
                builtin_dir=str(_PRESETS_DIR),
            )
            self.presets.wire_combo(widget, on_loaded=lambda *_: self.preview.refresh())
        except Exception as e:  # uitk missing / older / wiring failure — non-fatal
            self.logger.warning(f"Preset combo unavailable: {e}")

    # ------------------------------------------------------------------ helpers
    def _resolve_rail(self, objects):
        """The rail from the captured selection, else generated from the Rail fields."""
        resolved = curtain_rail_from_selection(objects)
        if resolved is not None:
            points, closed = resolved
            return points, closed or self.ui.chk001.isChecked()
        return ptk.Polyline.make(
            width=self.ui.s001.value(),
            curvature=self.ui.s002.value(),
            closed=self.ui.chk001.isChecked(),
            center=(self.ui.s025.value(), self.ui.s026.value(), self.ui.s027.value()),
        )

    def _finalize(self):
        """Post-commit: optionally select the created curtain (Select Result)."""
        import bpy

        obj = self._created and bpy.data.objects.get(self._created)
        if obj is None or not self.ui.chk005.isChecked():
            return
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj

    # ------------------------------------------------------------------ slots
    def perform_operation(self, objects):
        rail, closed = self._resolve_rail(objects)
        obj = create_curtain(
            rail,
            height=self.ui.s000.value(),
            hanging_points=int(self.ui.s003.value()),
            hang_jitter=self.ui.s023.value(),
            hang_seed=int(self.ui.s024.value()),
            gravity=self.ui.s004.value(),
            tension=self.ui.s005.value(),
            round_points=self.ui.s013.value(),
            round_gather=self.ui.s022.value(),
            fullness=self.ui.s006.value(),
            taper=self.ui.s007.value(),
            mid_folds=self.ui.s019.value(),
            mid_fold_seed=int(self.ui.s010.value()),
            creases=self.ui.s014.value(),
            crease_seed=int(self.ui.s015.value()),
            sway=self.ui.s020.value(),
            sway_seed=int(self.ui.s021.value()),
            end_bend_left=self.ui.s016.value(),
            end_bend_right=self.ui.s017.value(),
            end_bend_falloff=self.ui.s018.value(),
            irregularity=self.ui.s008.value(),
            density=self.ui.s009.value(),
            reduce=self.ui.s012.value(),
            thickness=self.ui.s011.value(),
            invert=self.ui.chk004.isChecked(),
            closed=closed,
        )
        self._created = obj.name

    def b001(self):
        """Reset all fields to their default values."""
        self.ui.state.reset_all()

    def b002(self):
        """Set Position (s025-27) to the selection's combined bounding-box center."""
        meshes = [o for o in selected_objects() if o.type == "MESH"]
        if not meshes:
            self.sb.message_box("Select mesh object(s) to grab a center from.")
            return
        boxes = [get_world_bbox(o) for o in meshes]
        mn = [min(b[0][i] for b in boxes) for i in range(3)]
        mx = [max(b[1][i] for b in boxes) for i in range(3)]
        for widget, value in zip(
            (self.ui.s025, self.ui.s026, self.ui.s027),
            ((mn[i] + mx[i]) / 2.0 for i in range(3)),
        ):
            widget.setValue(value)
        self.preview.refresh()


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("curtain", reload=True)
    ui.show(pos="screen", app_exec=True)
