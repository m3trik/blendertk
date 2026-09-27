# !/usr/bin/python
# coding=utf-8
"""Tube Rig panel (Blender) — the Switchboard slots for ``tube_rig.ui``.

Mirror of mayatk's ``tube_rig_slots``: the HYBRID docked panel whose mode combo rebuilds the
options body from the strategies' ``AttributeSpec`` dicts. Qt-only ``uitk`` imports stay
deferred into the methods that use them.
"""

import pythontk as ptk

from blendertk.core_utils._core_utils import CoreUtils
from blendertk.rig_utils._rig_utils import RigUtils
from blendertk.rig_utils.tube_rig._tube_rig import TubeRig
from blendertk.rig_utils.tube_rig.strategies import TUBE_STRATEGIES


# ----------------------------------------------------------------------------
# UI slots — the HYBRID docked panel (mode combo rebuilds the options dynamically)
# ----------------------------------------------------------------------------


class TubeRigSlots(ptk.LoggingMixin):
    """Switchboard slot wiring for the co-located ``tube_rig.ui`` — the **HYBRID** panel.

    The mode combo (``cmb_preset``) lists the registered strategies; selecting one **rebuilds the
    options body** from that strategy's option dicts (turning each into a ``uitk.AttributeSpec`` →
    ``make_widget``), so adding a rig type needs no ``.ui`` edit. Build reads the option widgets,
    resolves the selected mesh, and calls :meth:`TubeRig.build`. Self-contained
    (``ptk.LoggingMixin``); the Qt-only ``uitk`` factory is imported lazily (headless-safe).
    """

    def __init__(self, switchboard, log_level="WARNING"):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.tube_rig
        self.logger.setLevel(log_level)
        self.logger.set_log_prefix("[tube_rig] ")
        self._option_widgets = {}  # key -> built widget (rebuilt per mode)

        self.ui.cmb_preset.clear()
        for name, cls in TUBE_STRATEGIES.items():
            self.ui.cmb_preset.addItem(
                cls.label or name, name
            )  # userData = strategy key
        self.ui.cmb_preset.currentIndexChanged.connect(self._on_mode_changed)
        self._rebuild_options()

    def txt000_init(self, widget):
        """Rig-name field — optional, so clearing back to auto-naming is a state."""
        widget.option_box.clear_option = True

    def header_init(self, widget):
        """Configure header help text."""
        widget.set_help_text(
            self.sb.tooltip.fmt(
                title="Tube Rig",
                body="Rig a tube-shaped mesh several ways. The centerline is auto-detected; pick a "
                "<b>Mode</b> and the options below reconfigure to that rig type.",
                steps=[
                    "Select a tube mesh.",
                    "Pick a <b>Mode</b> — Spline (hose/cable), Anchor (piston), or FK (tail).",
                    "Set the mode's options, then press <b>Build Rig</b>.",
                ],
                sections=[
                    (
                        "Modes",
                        [
                            "<b>Spline</b> — a bone chain follows a curve; a few control handles hook "
                            "the curve (great for hoses/cables, with stretch).",
                            "<b>Anchor</b> — two end controls drive a piston/hydraulic that stretches "
                            "between them.",
                            "<b>FK</b> — the bones are the controls (rotate one, the rest follow) — a "
                            "tail/tentacle.",
                        ],
                    ),
                ],
                notes=[
                    "Each mode <b>declares its own options</b>; the panel rebuilds them on mode "
                    "change. Custom strategies registered via <b>TubeStrategy.register</b> appear "
                    "here automatically.",
                ],
            )
        )

    # ------------------------------------------------------------------ dynamic options
    def _current_strategy(self):
        name = self.ui.cmb_preset.currentData()
        return name, TUBE_STRATEGIES.get(name)

    def _rebuild_options(self):
        """Clear + repopulate the options container from the selected strategy's option dicts."""
        from qtpy import QtWidgets, QtCore
        from uitk.bridge.spec import AttributeSpec, KindFactory

        layout = self.ui.wgt_options.layout()
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self._option_widgets = {}

        _, cls = self._current_strategy()
        if cls is None:
            return
        for opt in cls.options:
            spec = AttributeSpec(**opt)
            row = QtWidgets.QWidget(self.ui.wgt_options)
            hbox = QtWidgets.QHBoxLayout(row)
            hbox.setContentsMargins(0, 0, 0, 0)
            hbox.setSpacing(2)
            label = QtWidgets.QLabel(spec.display_label + ":", row)
            label.setMinimumWidth(90)
            label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            widget = KindFactory.make_widget(spec, row)
            widget.setObjectName(f"opt_{spec.key}")
            if spec.tooltip:
                label.setToolTip(spec.tooltip)
                widget.setToolTip(spec.tooltip)
            hbox.addWidget(label)
            hbox.addWidget(widget, 1)
            layout.addWidget(row)
            self._option_widgets[spec.key] = widget

    def _on_mode_changed(self, *_):
        self._rebuild_options()

    def _collect_opts(self):
        from uitk.bridge.spec import KindFactory

        return {k: KindFactory.read_value(w) for k, w in self._option_widgets.items()}

    # ------------------------------------------------------------------ build
    # NOTE: not decorated -- `TubeRig.build` already pushes one. Blender's
    # `undo_push` is a flat checkpoint marker, not a Maya-style
    # openChunk/closeChunk pair, so a second decorator here would make TWO undo
    # steps for one click rather than nesting into one (same reasoning as
    # `WheelRigSlots.b000`). The granular steps below DO carry it, because the
    # methods they call are also reached from inside `build` and decorating
    # those would split every full build in three.
    def b000(self):
        """Build Rig — run the selected strategy on the selected tube mesh."""
        meshes = [o for o in CoreUtils.selected_objects() if o.type == "MESH"]
        if not meshes:
            self.sb.message_box("Select a tube mesh to rig.")
            return
        name, cls = self._current_strategy()
        if cls is None:
            self.sb.message_box("Pick a rig mode first.")
            return
        rig_name = (self.ui.txt000.text() or "").strip() or None
        try:
            rig = TubeRig(meshes[-1], rig_name=rig_name)
            # The build suspends global undo and redraws nothing until it ends,
            # so the footer is the only thing saying the tool is working rather
            # than hung. A marquee, not a bar: the phase count varies with the
            # strategy and its options, so a percentage would be a fiction.
            # ``sb.progress`` is a no-op on a UI without a footer, so the build
            # never depends on one. Mirror of mayatk's b000.
            with self.sb.progress(
                ui=self.ui, text="Tube Rig: preparing…", busy=True
            ) as update:
                bundle = rig.build(
                    name,
                    progress=self.sb.progress_adapter(update),
                    **self._collect_opts(),
                )
        except Exception as e:  # surface the engine's reason (e.g. non-tube mesh)
            self.sb.message_box(f"Tube rig failed: {e}")
            return
        self.sb.message_box(
            f"<hl>Built {self.ui.cmb_preset.currentText()} rig "
            f"({len(bundle.bones)} bones) on {meshes[-1].name}.</hl>"
        )

    # ------------------------------------------------------------------ granular steps (Spline)
    @staticmethod
    def _ordered_chain(armature):
        """Bone names root->tip for the deform chain — Spline IK needs the chain order (``bones[-1]``
        is the tip it constrains). Walks parent→first-child from a root. When the armature ALSO holds
        isolated control bones (an ``enable_twist`` roll control, ``b004`` anchor bones — all parentless
        AND childless), picks the parentless root whose chain is LONGEST, so those single-bone helpers
        can't be mistaken for the deform chain (order-independent, unlike picking the first root)."""

        def walk(root):
            chain, b = [], root
            while b is not None:
                chain.append(b.name)
                b = b.children[0] if b.children else None
            return chain

        chains = [walk(b) for b in armature.data.bones if b.parent is None]
        return max(chains, key=len) if chains else []

    @CoreUtils.undoable
    def b001(self):
        """Step 1 — create the joint/bone chain from the selected tube mesh's centerline (no controls
        or bind yet). Mirror of Maya's ``b001`` create_joints_from_tube; Reverse Direction = chk000."""
        meshes = [o for o in CoreUtils.selected_objects() if o.type == "MESH"]
        if not meshes:
            self.sb.message_box("Select a tube mesh to create joints from.")
            return
        opts = self._collect_opts()
        rig_name = (self.ui.txt000.text() or "").strip() or None
        try:
            rig = TubeRig(meshes[-1], rig_name=rig_name)
            centerline = rig.resolve_centerline(int(opts.get("num_joints", 12)))
            _, bones = rig.create_joint_chain(
                centerline,
                radius=float(opts.get("radius", 1.0)),
                reverse=self.ui.chk000.isChecked(),
            )
        except Exception as e:  # surface the engine's reason (e.g. non-tube mesh)
            self.sb.message_box(f"Create Joints failed: {e}")
            return
        self.sb.message_box(
            f"<hl>Step 1: created {len(bones)} joints on {meshes[-1].name}.</hl>"
        )

    @CoreUtils.undo_checkpoint
    def b002(self):
        """Step 2 — add the curve + Spline IK + hooked controls onto the selected armature's EXISTING
        bone chain (Maya's ``b002`` for Spline mode). Reads the deform toggles from the mode options."""
        import bpy

        arm = bpy.context.view_layer.objects.active
        if arm is None or arm.type != "ARMATURE":
            arm = next(
                (o for o in bpy.context.selected_objects if o.type == "ARMATURE"), None
            )
        if arm is None:
            self.sb.message_box("Select the joint chain (armature) created in Step 1.")
            return
        bones = self._ordered_chain(arm)
        if len(bones) < 2:
            self.sb.message_box(
                "The selected armature needs a chain of at least 2 bones for Spline IK."
            )
            return
        opts = self._collect_opts()
        rig_name = (self.ui.txt000.text() or "").strip() or None
        try:
            rig = TubeRig(None, rig_name=rig_name)
            rig._root = (
                arm.parent
            )  # reuse Step 1's rig root if present (else attach_spline_rig makes one)
            _, controls = rig.attach_spline_rig(
                arm,
                bones,
                num_controls=int(opts.get("num_controls", 3)),
                radius=float(opts.get("radius", 1.0)),
                enable_stretch=bool(opts.get("enable_stretch", True)),
                enable_squash=bool(opts.get("enable_squash", False)),
                enable_volume=bool(opts.get("enable_volume", False)),
                enable_auto_bend=bool(opts.get("enable_auto_bend", False)),
                enable_twist=bool(opts.get("enable_twist", False)),
            )
        except Exception as e:
            self.sb.message_box(f"Create IK / Controls failed: {e}")
            return
        self.sb.message_box(
            f"<hl>Step 2: added Spline IK + {len(controls)} controls to {arm.name}.</hl>"
        )

    @CoreUtils.undoable
    def b003(self):
        """Step 3 — bind the selected tube mesh to the selected armature (Armature modifier + automatic
        weights). Mirror of Maya's ``b003`` bind_joint_chain."""
        import bpy

        sel = bpy.context.selected_objects
        mesh = next((o for o in sel if o.type == "MESH"), None)
        arm = next((o for o in sel if o.type == "ARMATURE"), None)
        if mesh is None or arm is None:
            self.sb.message_box(
                "Select BOTH the tube mesh and its joint chain (armature), then Bind."
            )
            return
        try:
            RigUtils.bind_armature(mesh, arm, auto_weights=True)
        except Exception as e:
            self.sb.message_box(f"Bind failed: {e}")
            return
        self.sb.message_box(f"<hl>Step 3: bound {mesh.name} to {arm.name}.</hl>")

    @staticmethod
    def _estimate_tube_radius(mesh):
        """Approximate a tube's cross-sectional radius from its world bounding box — for a tube
        aligned to its longest axis the two SMALLER dimensions are the cross-section diameter, so the
        radius ≈ the average of their half-extents. Sizes the end-anchor falloff the way Maya's b004
        uses ~2× the joint radius."""
        dims = sorted(mesh.dimensions)  # ascending world bbox dims (min, mid, max)
        return max((dims[0] + dims[1]) / 4.0, 1e-3)

    @CoreUtils.undoable  # constraints only, no driver: a trailing push is safe
    def b004(self):
        """Utility — Constrain Ends to Anchors, one anchor or two: select the rig's armature and
        the anchor object for each tube end to constrain; each anchor constrains its NEAREST end
        with a distance falloff (mirror of Maya's b004; Blender selection order is unreliable).
        A single anchor leaves the other end free; two anchors off the same end are refused with
        the reason. Requires the mesh already bound (Step 3)."""
        import bpy

        sel = list(bpy.context.selected_objects)
        arm = next((o for o in sel if o.type == "ARMATURE"), None)
        # anchors are transforms/Empties, never a mesh — so the bound tube being selected too
        # (the common case) isn't mistaken for an anchor.
        anchors = [o for o in sel if o is not arm and o.type != "MESH"]
        if arm is None or not anchors:
            self.sb.message_box(
                "Select the rig's armature and the anchor object for each tube end to "
                "constrain (one or two)."
            )
            return
        if len(anchors) > 2:
            self.sb.message_box(
                f"A tube has two ends — got {len(anchors)} anchors. Select one anchor per end."
            )
            return
        bones = self._ordered_chain(arm)
        if len(bones) < 2:
            self.sb.message_box(
                "The selected armature needs a chain of at least 2 bones."
            )
            return
        # the bound mesh: a mesh whose Armature modifier targets this armature (Maya's skinCluster check)
        mesh = next(
            (
                o
                for o in bpy.data.objects
                if o.type == "MESH"
                and any(m.type == "ARMATURE" and m.object is arm for m in o.modifiers)
            ),
            None,
        )
        if mesh is None:
            self.sb.message_box(
                "The joints aren't bound to a mesh yet — run Step 3 (Bind Joints to Mesh) first."
            )
            return

        bpy.context.view_layer.update()
        mw, db = arm.matrix_world, arm.data.bones
        p_start = mw @ db[bones[0]].head_local
        p_end = mw @ db[bones[-1]].tail_local
        # each anchor to its nearest tube end (order-independent); two anchors off
        # one end are refused rather than silently collapsed onto it
        nearest = [
            0
            if (a.matrix_world.translation - p_start).length
            <= (a.matrix_world.translation - p_end).length
            else -1
            for a in anchors
        ]
        if len(anchors) == 2 and nearest[0] == nearest[1]:
            which = "start" if nearest[0] == 0 else "end"
            self.sb.message_box(
                f"Both anchors sit nearest the tube's {which} end ({anchors[0].name}, "
                f"{anchors[1].name}). Select one anchor per end — or a single anchor to "
                "constrain just that end."
            )
            return

        falloff = self._estimate_tube_radius(mesh) * 2.0
        rig_name = (self.ui.txt000.text() or "").strip() or None
        done = []
        try:
            rig = TubeRig(mesh, rig_name=rig_name)
            for anchor, idx in zip(anchors, nearest):
                result = rig.constrain_end_with_falloff(
                    arm, bones, anchor, mesh, falloff=falloff, bone_index=idx
                )
                done.append(f"{'start' if idx == 0 else 'end'}: {result}")
        except Exception as ex:
            self.sb.message_box(f"Add End Constraints failed: {ex}")
            return
        self.sb.message_box(f"<hl>End constraints added — {', '.join(done)}.</hl>")


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("tube_rig", reload=True)
    ui.show(pos="screen", app_exec=True)
