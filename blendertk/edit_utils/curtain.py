# !/usr/bin/python
# coding=utf-8
"""Curtain (draped-cloth) generation — the Blender build over the vendored
``CurtainDrape`` engine (``_curtain_drape``, code-identical with mayatk's copy;
mirror of mayatk's ``edit_utils.curtain``: same parameters, same drape math —
only the mesh build and post-ops differ).

``create_curtain`` builds a grid mesh from :meth:`CurtainDrape.grid_points`
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
from typing import Optional

import pythontk as ptk

from blendertk.core_utils._core_utils import _object_mode, selected_objects
from blendertk.core_utils.preview import Preview
from blendertk.edit_utils._curtain_drape import CurtainDrape
from blendertk.edit_utils._edit_utils import _apply_modifier, hook_bind_inverse
from blendertk.xform_utils._xform_utils import get_world_bbox

# Shipped, read-only curtain presets (UI-state snapshots). Identical to mayatk's — the panel
# shares the Maya widget names AND the vendored CurtainDrape engine, so a preset drapes the same.
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

    The drape math is :class:`CurtainDrape` (the vendored twin of mayatk's
    ``CurtainMesh`` engine — see it for the parameter reference); this builds the grid
    mesh from :meth:`grid_points` with grid UVs, then applies the post-ops:
    ``thickness`` (applied Solidify shell), ``reduce`` (percent decimated),
    ``invert`` (reversed normals), ``soften`` (smooth shading).

    Returns:
        (bpy.types.Object) the created curtain object.
    """
    import bpy
    import bmesh

    drape = CurtainDrape(rail, name=name, **options)
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

        The ``CurtainDrape`` engine hangs the cloth in **-Y** (gravity axis; see
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
    """Switchboard slot wiring for the curtain UI (live preview + rail resolution + presets).

    Blender port of mayatk's ``CurtainSlots``: the drape math is the vendored
    :class:`CurtainDrape` engine (code-identical with mayatk's copy — drift fails
    extapps' ``test_vendor_sync.py``), so every parameter behaves identically across
    DCCs — only the mesh build differs (``create_curtain``, bmesh). The rail resolves
    from the selection (edit-mode edges / a curve object / 2+ object positions) or,
    when nothing usable is selected, from a generated driver curve built from the
    Width/Curvature/Position/Closed fields — :meth:`_ensure_rail` builds and selects
    it the moment Preview is toggled on, mirroring mayatk's auto-rail so ``Preview``
    never requires an unrelated selection first. The same in-panel **preset combo** as
    Maya (shared ``uitk.PresetManager`` + the identical built-in presets). The
    wire-deformer rig is the :class:`CurtainRig` engine class (Maya's ``CurtainRig``);
    like Maya it is **not** wired into this panel — it is an engine-level capability
    with no tentacle nav button (Maya exposes it the same way).

    One real divergence from mayatk, driven by :class:`~blendertk.core_utils.preview.Preview`'s
    snapshot/restore design (vs. mayatk's node-diff ``CleanupContract``): a node mayatk's Preview
    didn't create survives a rollback untouched, so mayatk resyncs the driver curve's shape on
    every rail-field change and discards it on a plain cancel. Here, any object captured at
    ``enable()`` time is restored (even recreated) by every rollback, so a mid-preview resync would
    be immediately undone and a cancel-time delete would just be recreated by Preview's own
    restore — see :meth:`_ensure_rail` / :meth:`_sync_driver`. The curtain **mesh** itself is
    unaffected (it re-reads the live field values on every refresh); only the driver curve's own
    on-screen shape can lag behind the dialed Rail fields until the next Preview toggle, and a
    cancelled (not committed) session's generated driver is reclaimed on the next use rather than
    deleted immediately. Commit still drops it (see :meth:`_finalize`).

    Self-contained (``ptk.LoggingMixin`` only) so blendertk carries no back-dependency
    on tentacle — the selection comes from ``btk.selected_objects``, not a tentacle base.
    """

    def __init__(self, switchboard, log_level="WARNING"):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.curtain
        self.logger.setLevel(log_level)
        self.logger.set_log_prefix("[curtain] ")
        self.last_curtain: Optional[str] = None
        self.presets = None  # in-panel PresetManager (wired in cmb000_init)
        # Auto-rail state: when nothing usable is selected we own a generated driver
        # curve (``_driver``, its object name) built from the Width/Curvature/Hanging-
        # Points/Closed fields. ``_generated`` flags that mode.
        self._driver: Optional[str] = None
        self._generated: bool = False

        # Ensure a rail exists the moment Preview is toggled on (and, on commit,
        # discard our generated one). Connected BEFORE Preview so it runs first and
        # Preview.enable() finds a selection — you never have to select an unrelated
        # object.
        self.ui.chk000.toggled.connect(self._ensure_rail)

        # Per-parameter reset buttons must precede connect_multi/Preview — wrapping
        # reparents the widgets and invalidates any already-deferred wrapper. The X/Y/Z
        # Position triplet is skipped — it already shares a tight row with the Get button.
        self.sb.add_reset_buttons(self.ui, skip=("s025", "s026", "s027"))

        self.preview = Preview(
            self,
            self.ui.chk000,
            self.ui.b000,
            finalize_func=self._finalize,
            message_func=self.sb.message_box,
            undo_message="Create Curtain",
        )
        # Re-drape live as any numeric field changes; Closed/Invert are pure re-drapes
        # (see _on_param_changed for why the driver curve itself isn't resynced here).
        self.sb.connect_multi(self.ui, "s000-27", "valueChanged", self._on_param_changed)
        self.sb.connect_multi(self.ui, "chk001,chk004", "clicked", self.preview.refresh)

        # The Position fields dropped their "X "/"Y "/"Z " prefixes; color-code the
        # values red/green/blue instead (axis convention) so the row stays compact
        # while still reading per-axis at a glance.
        self._color_code_position_fields()

        # Footer doubles as a stats readout (the result's tri count) once a curtain is
        # built; show a hint until then.
        try:
            self.ui.footer.setDefaultStatusText("Toggle Preview to drape a curtain.")
        except Exception:
            pass

    # --------------------------------------------------------------- header

    def header_init(self, widget):
        """Configure header help text (the preset combo lives in the panel)."""
        from uitk.widgets.mixins.tooltip_mixin import fmt

        widget.set_help_text(
            fmt(
                title="Curtain",
                body="Drape a pleated cloth curtain from a <b>rail</b> — a "
                "selected curve object, edit-mode mesh edge loop, or chain of "
                "objects, or a generated straight rail when nothing usable is "
                "selected.",
                steps=[
                    "Toggle <b>Preview</b> (a rail is auto-created from "
                    "Width/Curvature if you haven't selected your own).",
                    "Set <b>Hanging Points</b> (the pleats/pins) and "
                    "<b>Fullness</b>.",
                    "Dial <b>Gravity</b> — how far the fabric falls between "
                    "hanging points.",
                    "Press <b>Create</b> to commit.",
                ],
                sections=[
                    ("Model", [
                        "Each <b>Hanging Point</b> is a pleat where the fabric "
                        "pins to the rail — one clean gather at the rail — and "
                        "bellies into a full fold between consecutive points, so "
                        "the count maps roughly 1:1 to the folds you see. The "
                        "spans sag down a real <b>catenary</b> (cosh).",
                        "<b>Gravity</b> sets the sag depth (wider gaps fall "
                        "further); <b>Catenary Tension</b> shapes that curve.",
                        "<b>Taper</b> gathers the pleats at the top and flares "
                        "them toward the hem.",
                        "<b>Mid Folds</b> fork V-folds down from some hang "
                        "points (seed varies which), breaking the plain in/out "
                        "belly; <b>Creases</b> add diagonal V break-lines; "
                        "<b>Sway</b> randomly leans a subset of the folds left "
                        "or right along the rail (not just in/out); the "
                        "<b>Ends</b> group bends each end; <b>Round</b> softens "
                        "the hooks.",
                    ]),
                ],
                notes=[
                    "The <b>preset</b> combo loads built-in looks "
                    "(Stage Swag, Shower Curtain) and saves your own.",
                    "<b>Select Result</b> selects the finished curtain on "
                    "<b>Create</b> so you can see the result.",
                    "Same engine as the Maya panel — identical settings drape "
                    "identically.",
                ],
            )
        )
        # Align every spinbox's value column once the panel's fonts/styles are settled
        # (deferred a tick so QFontMetrics sees the themed font).
        try:
            from qtpy import QtCore

            QtCore.QTimer.singleShot(0, self._align_spinbox_prefixes)
        except Exception as e:
            self.logger.debug(f"Prefix alignment deferral failed: {e}")

    def cmb000_init(self, widget):
        """Wire the in-panel preset selector (built-in + user tiers) — mirror of the Maya panel.

        A curtain preset is a UI-state snapshot of the drape fields; because the panel shares the
        Maya widget names *and* the vendored ``CurtainDrape`` engine, the built-in JSONs are
        identical across DCCs (shipped in ``edit_utils/presets/curtain``). Loading a preset resyncs
        the generated driver to the loaded fields, then refreshes the preview in one shot."""
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
            self.presets.wire_combo(widget, on_loaded=self._on_param_changed)
        except Exception as e:  # uitk missing / older / wiring failure — non-fatal
            self.logger.warning(f"Preset combo unavailable: {e}")

    # ------------------------------------------------- spinbox value alignment

    def _color_code_position_fields(self) -> None:
        """Tint the rail Position values red/green/blue for X/Y/Z.

        Mirrors mayatk: the fields dropped their "X "/"Y "/"Z " prefixes (see curtain.ui);
        the axis-coded value text now carries that meaning at a glance, with the tooltips
        naming the axis as a textual fallback. Colors come from the shared
        ``pythontk.Palette.axes()`` (RGB axis convention), applied via the uitk
        ``DoubleSpinBox`` ``set_text_color`` helper.
        """
        try:
            axes = ptk.Palette.axes()
        except Exception as e:
            self.logger.debug(f"Position color-coding unavailable: {e}")
            return
        for name, key in (("s025", "x"), ("s026", "y"), ("s027", "z")):
            setter = getattr(getattr(self.ui, name, None), "set_text_color", None)
            if callable(setter):
                setter(axes[key].hex)

    def _align_spinbox_prefixes(self) -> None:
        """Pad each spinbox prefix so the values line up within each group.

        The custom spin widgets add a single ``\\t`` after the prefix, which only lands
        on one tab stop — long prefixes ("Catenary Tension:") then overflow past short
        ones ("Seed:"), so the value columns don't align. Here we measure the widest
        prefix per section (with the widget's own font metrics) and right-pad the rest
        with spaces to match (font-correct to within a space width), bypassing the
        ``\\t``. Aligning per group — keyed on each spinbox's container — keeps
        short-labelled sections tight instead of indenting them to clear a long label
        elsewhere.
        """
        try:
            from qtpy import QtWidgets, QtGui
        except Exception:
            return

        # Bucket the spinboxes by their titled group. Walk up to the nearest
        # CollapsableGroup rather than using the immediate parent, since the option-box
        # "disable" wrapping reparents each spinbox into its own container — grouping on
        # that would defeat the per-section alignment.
        try:
            from uitk.widgets.collapsableGroup import CollapsableGroup
        except Exception:
            CollapsableGroup = ()

        def _group_of(w):
            p = w.parentWidget()
            while p is not None:
                if CollapsableGroup and isinstance(p, CollapsableGroup):
                    return p
                p = p.parentWidget()
            return w.parentWidget()

        groups = {}
        for sb in self.ui.findChildren(QtWidgets.QAbstractSpinBox):
            base = sb.prefix().rstrip()  # drop the trailing tab/space
            if not base:
                continue
            groups.setdefault(_group_of(sb), []).append(
                (sb, base, QtGui.QFontMetrics(sb.font()))
            )

        for entries in groups.values():
            max_w = max(fm.horizontalAdvance(base) for _, base, fm in entries)
            for sb, base, fm in entries:
                space_w = fm.horizontalAdvance(" ") or 1
                gap = max_w + 2 * space_w - fm.horizontalAdvance(base)
                text = base + " " * max(1, round(gap / space_w))
                # Bypass the custom setPrefix (which would re-append a tab).
                if isinstance(sb, QtWidgets.QDoubleSpinBox):
                    QtWidgets.QDoubleSpinBox.setPrefix(sb, text)
                else:
                    QtWidgets.QSpinBox.setPrefix(sb, text)

    # ----------------------------------------------------------- rail / driver

    def _on_param_changed(self, *_):
        """A field changed: re-drape.

        Mirrors mayatk's hook name/point, but — unlike mayatk — doesn't resync the
        driver curve's shape here; see the class docstring and :meth:`_sync_driver` for
        why (blendertk's Preview would immediately undo it on the next refresh). The
        curtain mesh itself always tracks the live field values (``perform_operation``
        re-reads them every refresh), so the drape is correct regardless.
        """
        self.preview.refresh()

    def _field_rail(self):
        """The generated rail from the Width / Curvature / Position / Closed fields."""
        return ptk.Polyline.make(
            width=self.ui.s001.value(),
            curvature=self.ui.s002.value(),
            closed=self.ui.chk001.isChecked(),
            center=(self.ui.s025.value(), self.ui.s026.value(), self.ui.s027.value()),
        )

    def _build_driver(self, points, closed) -> str:
        """Build a low-CV rail curve whose CVs sit at the hanging points.

        Blender mirror of mayatk's ``cmds.curve`` driver build — used as the preview's
        visible rail, resampled to ``hanging_points`` control points so it reads as the
        line of pins the cloth gathers on. Returns the new object's name.
        """
        import bpy

        n = max(2, int(self.ui.s003.value()))
        ctrl = ptk.Polyline.resample(points, n)
        curve_data = bpy.data.curves.new("curtain_rail", type="CURVE")
        curve_data.dimensions = "3D"
        spline = curve_data.splines.new("POLY")
        spline.points.add(len(ctrl) - 1)
        for i, p in enumerate(ctrl):
            spline.points[i].co = (*p, 1.0)
        spline.use_cyclic_u = bool(closed)
        obj = bpy.data.objects.new("curtain_rail", curve_data)
        bpy.context.collection.objects.link(obj)
        return obj.name

    def _sync_driver(self) -> None:
        """(Re)build the owned driver curve from the current Rail fields and select it.

        Only called on preview-enable (see :meth:`_ensure_rail`) — not on every
        rail-field change like mayatk's version. blendertk's Preview snapshots the
        captured selection at ``enable()`` time and restores it on *every* rollback,
        so a mid-preview rebuild here would be overwritten by the very next refresh;
        see the class docstring.
        """
        import bpy

        if self._driver and self._driver in bpy.data.objects:
            self._discard_driver()
        points, closed = self._field_rail()
        self._driver = self._build_driver(points, closed)
        bpy.ops.object.select_all(action="DESELECT")
        obj = bpy.data.objects[self._driver]
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj

    def _discard_driver(self) -> None:
        """Delete the generated driver curve we own (orphan-rail cleanup), if it exists."""
        import bpy

        if self._driver and self._driver in bpy.data.objects:
            obj = bpy.data.objects[self._driver]
            data = obj.data
            try:
                bpy.data.objects.remove(obj, do_unlink=True)
                if data is not None and data.users == 0:
                    bpy.data.curves.remove(data)
            except Exception:
                pass
        self._driver = None

    def _user_selection(self):
        """Current selection minus our own driver curve."""
        return [o for o in selected_objects() if o.name != self._driver]

    def _ensure_rail(self, state: bool) -> None:
        """On preview-enable, guarantee a usable rail; a no-op on disable.

        If the user has their own rail selected we hang on that (Width/Curvature are
        ignored), discarding any generated driver we still own. Otherwise we enter
        *generated* mode and build/select a driver curve — both to satisfy Preview's
        selection gate and to show the rail the cloth hangs on.

        Nothing is discarded here on ``state=False``: unlike mayatk's node-diff
        Preview, blendertk's Preview restores (even recreates) whatever it captured at
        ``enable()`` time on every rollback, so deleting our driver on a plain cancel
        would just have Preview's own rollback bring it straight back. It is instead
        reclaimed the next time this fires (discarded-and-rebuilt, or discarded outright
        if a real rail is now selected) or dropped for good on commit — see
        :meth:`_finalize`.
        """
        if not state:
            return
        if curtain_rail_from_selection(self._user_selection()) is not None:
            self._discard_driver()
            self._generated = False
            return
        self._generated = True
        self._sync_driver()

    def _resolve_rail(self, objects):
        """Rail points for the current drape.

        Generated mode reads the Width/Curvature/Closed fields live (so they take
        effect on every refresh); selected mode resolves the user's rail.
        """
        if not self._generated:
            rail = curtain_rail_from_selection(
                [o for o in objects if getattr(o, "name", o) != self._driver]
            )
            if rail is not None:
                points, closed = rail
                return points, closed or self.ui.chk001.isChecked()
        return self._field_rail()

    # --------------------------------------------------------------- buttons

    def b001(self):
        """Reset to Defaults."""
        self.ui.state.reset_all()

    def b002(self):
        """Set Position to the bounding-box center of the selected object(s).

        Centers the generated rail on whatever is selected (its combined world
        bounding box). Ignores the panel's own auto-rail driver and the curtain it's
        building, so Get centers on the *external* target. The three Position fields
        are set in one shot (signals blocked) and a single re-drape is fired, so the
        curtain re-centers immediately.
        """
        ours = {self._driver, self.last_curtain}
        sel = [o for o in selected_objects() if o.name not in ours]
        if not sel:
            self.sb.message_box("Select object(s) to center the rail on.")
            return
        boxes = [get_world_bbox(o) for o in sel]
        mn = [min(b[0][i] for b in boxes) for i in range(3)]
        mx = [max(b[1][i] for b in boxes) for i in range(3)]
        for widget, value in zip(
            (self.ui.s025, self.ui.s026, self.ui.s027),
            ((mn[i] + mx[i]) / 2.0 for i in range(3)),
        ):
            widget.blockSignals(True)
            widget.setValue(value)
            widget.blockSignals(False)
        self._on_param_changed()

    # ------------------------------------------------------------- operation

    def perform_operation(self, objects):
        """Build the curtain from the resolved rail (Preview entry point)."""
        points, closed = self._resolve_rail(objects)

        obj = create_curtain(
            points,
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
        self.last_curtain = obj.name
        self._update_footer()
        # Select Result is applied manually in _finalize (blendertk's Preview has no
        # built-in select_result_checkbox/result_provider like mayatk's) -- see there.

    def _update_footer(self):
        """Show the result's triangle count in the footer; clears to the default hint
        when there is no result. Updates live as the preview re-drapes."""
        import bpy

        try:
            footer = self.ui.footer
        except Exception:
            return
        obj = self.last_curtain and bpy.data.objects.get(self.last_curtain)
        if obj is None or obj.type != "MESH":
            footer.setStatusText("")  # falls back to the default hint
            return
        obj.data.calc_loop_triangles()
        tris = len(obj.data.loop_triangles)
        footer.setStatusText(f"{tris:,} tris")

    def _finalize(self):
        """On commit, drop the preview's auto-rail, then apply Select Result.

        The auto-rail is only a preview aid (it shows where the cloth hangs and
        satisfies Preview's selection gate); it isn't wanted in the committed scene.
        Safe to delete outright here (unlike a plain cancel — see the class
        docstring): ``Preview.commit()`` drops its own captured-object bookkeeping
        before calling this, with no rollback in between, so there's nothing left to
        resurrect it. The next preview recomputes the rail mode from the live
        selection, so the mode flag is cleared here too. Select Result is applied
        *after* the discard (which can change the active selection), so the result
        wins.
        """
        import bpy

        self._generated = False
        self._discard_driver()
        obj = self.last_curtain and bpy.data.objects.get(self.last_curtain)
        if obj is None or not self.ui.chk005.isChecked():
            return
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("curtain", reload=True)
    ui.show(pos="screen", app_exec=True)
