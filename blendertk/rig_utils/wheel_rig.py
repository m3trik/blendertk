# !/usr/bin/python
# coding=utf-8
"""Wheel Rig — engine + Switchboard slot wiring for the co-located ``wheel_rig.ui``.

Blender port of mayatk's ``rig_utils.wheel_rig`` (``btk.WheelRig`` ↔ ``mtk.WheelRig``): make wheels
auto-rotate from a control's travel (rolling). Maya wires this with an ``expression`` node reading
``distance / circumference`` (plus keyable ``wheelHeight`` / ``enableRotation`` / ``spinDirection``
control attrs, a left/right auto-flip, and an optional ``decomposeMatrix`` node for world-space
travel); the Blender analogue is a ``rotation_euler`` **driver** (``rotation = 2·travel / height``
radians — equal to Maya's ``distance/(π·height)·360°``) with the same three control attrs as keyable
custom properties, the same dot-product auto-flip, and the same local/world toggle — Blender's
``TRANSFORMS`` driver variable already supports both spaces natively (``TRANSFORM_SPACE`` = the raw
local channel value, ``WORLD_SPACE`` = full parent-aware position), so no ``decomposeMatrix``-style
node is needed for the world-space mode.

Built on the shared ``RigUtils`` base (constraints / drivers / custom-prop vars). ``import bpy`` is
deferred into the call bodies and the Qt-only ``uitk`` helper into its method, so importing the
module / resolving the package surface never needs a running Blender or Qt.
"""
import pythontk as ptk

from blendertk.core_utils._core_utils import undo_checkpoint
from blendertk.core_utils.script_job_manager import ScriptJobManager
from blendertk.edit_utils.naming._naming import Naming
from blendertk.rig_utils._rig_utils import RigUtils
from blendertk.xform_utils._xform_utils import freeze_transforms as _apply_freeze_transforms


class WheelRig(ptk.LoggingMixin):
    """
    Handles basic wheel rigging by linking rotation to linear control movement.

    This class supports re-entrancy by stamping a ``wheel_rig_id`` custom property onto the control
    object. When initialized with a control that already carries this property, it reuses the
    existing rig name so a rebuild updates the rig's drivers in place instead of duplicating them.

    Parameters:
        control (str/object): The control driving movement.
        wheels (list): Wheel objects.
        rig_name (str): Optional name for the rig.
        freeze_transforms (bool): Freeze (bake) LOCATION only on the control + wheels before
            rigging — rotation must be preserved so the auto-flip pass can read mirrored-wheel
            orientation from the world matrix (mirrors mayatk's translate-only freeze).

    Attributes:
        control (object): The control object.
        wheels (list): Wheel objects.
        rig_name (str): Name of the rig.
    """

    # combo index -> (control travel channel, wheel rotation_euler index). Mirrors Maya's combo
    # (Move X→Rotate Z, Y→Y, Z→X); the user picks the pair matching their wheel's axle orientation.
    _AXIS_MAP = {0: ("LOC_X", 2), 1: ("LOC_Y", 1), 2: ("LOC_Z", 0)}

    def __init__(self, control, wheels, rig_name=None, freeze_transforms=True):
        super().__init__()
        self.control = RigUtils.resolve_object(control)
        resolved_wheels = [RigUtils.resolve_object(o) for o in ptk.make_iterable(wheels)]

        if self.control is None or not resolved_wheels or any(w is None for w in resolved_wheels):
            raise ValueError("Invalid control or wheel inputs.")
        self.wheels = resolved_wheels

        # Check for persistent rig ID
        stored_name = self.control.get("wheel_rig_id")
        self._rig_name = rig_name or stored_name or Naming.generate_unique_name("wheel_rig")

        # Persist ID
        self.control["wheel_rig_id"] = self._rig_name

        if freeze_transforms:
            # Freeze location only — rotation must be preserved so the auto-flip pass can read
            # mirrored-wheel orientation from the world matrix.
            _apply_freeze_transforms(self.control, location=True, rotation=False, scale=False)
            _apply_freeze_transforms(self.wheels, location=True, rotation=False, scale=False)

    @property
    def rig_name(self) -> str:
        return self._rig_name

    @rig_name.setter
    def rig_name(self, name: str):
        self._rig_name = name
        self.logger.debug(f"Rig name set to: {self._rig_name}")

    def get_drivers(self):
        """Return every rotation driver fcurve currently attached to this rig's wheels.

        Blender analogue of mayatk's ``get_expressions``: Maya's expression node lives on the
        control and is discovered via ``listConnections``; Blender attaches the driver directly to
        the wheel object being driven, so this is naturally already scoped to ``self.wheels`` —
        there is no ``filter_by_rig`` equivalent to offer.

        Returns:
            List of driver FCurves.
        """
        drivers = []
        for wheel in self.wheels:
            anim_data = getattr(wheel, "animation_data", None)
            for fcurve in anim_data.drivers if anim_data else ():
                if fcurve.data_path == "rotation_euler":
                    drivers.append(fcurve)
        return drivers

    def delete_drivers(self) -> None:
        """Remove this rig's rotation drivers from its wheels.

        Blender analogue of mayatk's ``delete_expressions``; the control's keyable custom-property
        attrs (``wheelHeight`` / ``enableRotation`` / ``spinDirection``) are left intact.
        """
        drivers = self.get_drivers()
        if drivers:
            for wheel in self.wheels:
                for index in range(3):
                    RigUtils.remove_driver(wheel, "rotation_euler", index)
            self.logger.info(f"Deleted {len(drivers)} driver(s) for rig: {self.rig_name}")
        else:
            self.logger.info(f"No drivers found to delete for rig: {self.rig_name}")

    @undo_checkpoint
    def rig_rotation(
        self,
        movement_axis: str = "LOC_Z",
        rotation_index: int = None,
        wheel_height: float = 1.0,
        wheels: list = None,
        use_world_space: bool = False,
    ) -> list:
        """
        Rig wheels to rotate based on control movement.

        Parameters:
            movement_axis: Which control travel channel drives rotation
                (e.g. ``"LOC_Z"``).
            rotation_index: Which ``rotation_euler`` component (0=X, 1=Y, 2=Z) to drive on the
                wheels. Auto-inferred from ``movement_axis`` when ``None``.
            wheel_height: Diameter used to compute rotation amount.
            wheels: Wheel objects to rig.  Falls back to ``self.wheels``.
            use_world_space: When ``True``, the driver reads the control's WORLD-space position
                (``TRANSFORMS`` variable, ``transform_space="WORLD_SPACE"``) so parent movement is
                captured — the analogue of Maya's optional ``decomposeMatrix`` node.  When
                ``False`` (default), the driver reads the control's own translate channel directly
                (``transform_space="TRANSFORM_SPACE"``) — simpler and sufficient when the control
                itself is being animated.

        Returns:
            The wheel objects that were rigged.
        """
        import bpy

        wheels = self.wheels if wheels is None else [
            w for w in (RigUtils.resolve_object(o) for o in ptk.make_iterable(wheels)) if w
        ]
        if not wheels:
            raise ValueError("No wheels specified. You must pass wheels to rig.")
        if wheel_height <= 0:
            raise ValueError(f"Invalid wheel height: {wheel_height}")

        # Auto-infer rotation index if not specified
        if rotation_index is None:
            # Standard vehicle dynamics assumption:
            # - Forward Z -> Pitch (Rotate X)
            # - Side X -> Roll (Rotate Z)
            rotation_index = {"LOC_Z": 0, "LOC_X": 2, "LOC_Y": 1}.get(movement_axis, 0)
            self.logger.info(
                f"Auto-inferred rotation index: {rotation_index} from movement: {movement_axis}"
            )

        # Smart attribute management for wheel height: find or create a "wheelHeight" custom
        # property matching the requested height, or a new suffixed one if existing ones differ
        # significantly — lets ONE control drive multiple wheel groups with different diameters.
        height_attr_name = "wheelHeight"
        suffix_idx = 0
        epsilon = 0.01
        while True:
            candidate = "wheelHeight" if suffix_idx == 0 else f"wheelHeight_{suffix_idx}"

            if candidate not in self.control:
                RigUtils.ensure_custom_prop(
                    self.control, candidate, float(wheel_height), min_value=0.001
                )
                height_attr_name = candidate
                break

            existing_val = self.control[candidate]
            if abs(existing_val - wheel_height) < epsilon:
                self.control[candidate] = float(wheel_height)
                height_attr_name = candidate
                break

            suffix_idx += 1

        # Global control attributes (shared across all wheel groups)
        RigUtils.ensure_custom_prop(
            self.control, "enableRotation", 1.0, min_value=0.0, max_value=1.0
        )
        RigUtils.ensure_custom_prop(self.control, "spinDirection", 1.0)

        space = "WORLD_SPACE" if use_world_space else "TRANSFORM_SPACE"

        bpy.context.view_layer.update()
        ctrl_axis = self.control.matrix_world.col[rotation_index].to_3d()
        for wheel in wheels:
            # Auto-flip mirrored wheels: opposite rotation-axis world direction -> negate.
            wheel_axis = wheel.matrix_world.col[rotation_index].to_3d()
            auto_flip = -1.0 if ctrl_axis.dot(wheel_axis) < 0 else 1.0

            RigUtils.remove_driver(wheel, "rotation_euler", rotation_index)  # re-entrant

            # Build the driver + ALL variables BEFORE the expression — an expression that
            # references a not-yet-added variable evaluates it as 0 (div-by-zero) and sticks.
            fcurve = RigUtils.add_transform_driver(
                wheel, "rotation_euler", rotation_index, self.control, movement_axis,
                space=space, var_name="travel",
            )
            RigUtils.add_prop_var(fcurve, "height", self.control, f'["{height_attr_name}"]')
            RigUtils.add_prop_var(fcurve, "enable", self.control, '["enableRotation"]')
            RigUtils.add_prop_var(fcurve, "direction", self.control, '["spinDirection"]')
            # radians = 2*travel/height (== Maya's distance/(pi*height)*360deg). Pure arithmetic
            # (no ternary -> stays on Blender's fast driver parser, no full-Python security gate);
            # height can't hit 0 (custom-prop min=0.001).
            fcurve.driver.expression = f"enable * direction * {auto_flip} * 2.0 * travel / height"

            self.logger.debug(
                f"Rigged wheel: {wheel.name} with driver: rotation_euler[{rotation_index}]"
            )

        RigUtils.refresh_drivers(wheels)  # post-build recompile (script-built driver gotcha)
        self.logger.info(f"Wheel rig rotation updated for wheels: {[w.name for w in wheels]}")
        return wheels


class WheelRigSlots(ptk.LoggingMixin):
    """Switchboard slot wiring for the Wheel Rig panel.

    Self-contained (``ptk.LoggingMixin`` only, no back-dependency on tentacle); the Qt-only
    ``uitk`` helper is deferred into ``header_init``.
    """

    def __init__(self, switchboard, log_level: str = "WARNING"):
        super().__init__()
        self.set_log_level(log_level)
        self.sb = switchboard
        # Use the actual UI name loaded from the wheel_rig.ui file
        # Avoid creating a placeholder UI ("wheel_rig_slots") which has no widgets
        self.ui = self.sb.loaded_ui.wheel_rig

        # Populate axis combo box showing both movement and rotation axes
        self.ui.cmb000.clear()
        self.ui.cmb000.addItems(
            [
                "Move X → Rotate Z",
                "Move Y → Rotate Y",
                "Move Z → Rotate X",
            ]
        )
        self.ui.cmb000.setCurrentIndex(2)  # Default to Move Z → Rotate X

        # Remove redundant UI elements: cmb001 (Rot Axis) is folded into cmb000's combined
        # move+rotate combo, and chk000 (Reverse) is superseded by the persistent, always-editable
        # `spinDirection` custom property. Hide rather than deleteLater — the runtime .ui loader
        # can invalidate deleted-widget wrappers on re-show.
        for widget_name in ("chk000", "cmb001"):
            widget = getattr(self.ui, widget_name, None)
            if widget is not None:
                widget.setVisible(False)

        # 1) update placeholder right away
        mgr = ScriptJobManager.instance()
        mgr.subscribe(
            "SelectionChanged",
            self._on_selection_changed,
            owner=self,
        )
        mgr.connect_cleanup(self.ui, owner=self)
        self.update_rig_name_placeholder()

        # 2) cleanup on actual window close
        self.ui.on_close.connect(self.cleanup)

        # 3) Setup Tooltips
        self.ui.b000.setToolTip(
            "<b>Rig Rotation</b><br>"
            "Select wheel objects, then the driver LAST (active), and click to create or update "
            "the rig."
        )

        # 4) World-space mode flag (toggled via header menu)
        self._use_world_space = False

    def header_init(self, widget):
        """Configure header menu with mode toggle and instructions."""
        from uitk.widgets.mixins.tooltip_mixin import fmt, kbd

        widget.menu.add("Separator", setTitle="Mode")
        # Local vs World Space is a two-valued mode, not a modifier — a combobox
        # names both states; extend with a third space here without a relayout.
        cmb_space = widget.menu.add(
            "QComboBox",
            setObjectName="cmb_space",
            setToolTip=(
                "Local (default) — the driver reads its own translate channel\n"
                "directly (Transform Space). Simpler and sufficient when the\n"
                "driver itself is being animated.\n\n"
                "World Space — wheel rotation reads the driver's WORLD-space\n"
                "position (the driver variable's Transform Space is set to World\n"
                "Space), capturing movement from parent transforms."
            ),
        )
        cmb_space.addItems(["Local", "World Space"])
        cmb_space.setCurrentText("Local")  # preserve prior default (checkbox off = local)
        cmb_space.currentTextChanged.connect(self._on_space_changed)

        widget.set_help_text(
            fmt(
                title="Wheel Rig",
                body="Drive wheel rotation from a control's linear movement. "
                "Wheel diameter (Wheel Height) and travel axis determine the "
                "rotation speed.",
                sections=[
                    ("Selection order", [
                        "Select one or more <b>wheel</b> objects.",
                        f"{kbd('Shift')}-select the <b>driver / control</b> "
                        "object LAST so it becomes the <b>active</b> object.",
                        "Click <b>Rig Rotation</b>.",
                    ]),
                    ("Settings", [
                        "<b>Axis</b> — which travel axis drives which "
                        "rotation axis. e.g. <i>Move Z → Rotate X</i> means "
                        "forward Z movement produces pitch on X.",
                        "<b>Wheel Height</b> — diameter used to compute "
                        "rotation speed. Use <b>Get Wheel Size</b> (slider "
                        "option box) to auto-detect from the bounding box.",
                    ]),
                    ("Modes", [
                        "<b>Local</b> (default) — reads the driver's own "
                        "translate channel. Best when the driver itself is animated.",
                        "<b>World Space</b> — reads world-space position so "
                        "parent transform movement is captured. Toggle via "
                        "the header menu.",
                    ]),
                    ("Driver attributes added", [
                        "<b>wheelHeight</b> — animation-friendly diameter control.",
                        "<b>enableRotation</b> — on/off toggle (0..1).",
                        "<b>spinDirection</b> — flip direction (+1 / -1).",
                    ]),
                ],
                notes=[
                    "Re-running on the same driver updates the existing "
                    "rig in place (a <i>wheel_rig_id</i> custom property "
                    "on the driver acts as the idempotency key — no duplicate drivers).",
                ],
            )
        )

    def _on_space_changed(self, text: str):
        self._use_world_space = text == "World Space"

    @property
    def rig_name(self) -> str:
        """Get the rig name from the text box."""
        return self.ui.txt000.text()

    @rig_name.setter
    def rig_name(self, name: str):
        self.ui.txt000.setText(name)

    @property
    def movement_axis(self) -> str:
        """Get the control travel channel from the axis combo box."""
        axis, _ = WheelRig._AXIS_MAP.get(self.ui.cmb000.currentIndex(), ("LOC_Z", 0))
        return axis

    @property
    def rotation_axis(self) -> int:
        """Get the wheel ``rotation_euler`` index that corresponds to the selected movement axis.

        Blender's driver targets a ``rotation_euler`` component INDEX (0/1/2), unlike Maya's
        named ``rotateX/Y/Z`` attribute string — same property name/role, adapted return type.
        """
        _, rotation_index = WheelRig._AXIS_MAP.get(self.ui.cmb000.currentIndex(), ("LOC_Z", 0))
        return rotation_index

    def resolve_selection(self):
        """Resolve the current selection into control (driver) and wheels.

        Blender doesn't reliably preserve click-order, so unlike Maya's "last selected", the
        **active** object is the driver. All other selected objects are treated as wheels.

        Returns:
            Tuple of (control, list of wheels).
        Raises:
            ValueError: if selection is invalid.
        """
        import bpy

        from blendertk.core_utils._core_utils import selected_objects

        sel = selected_objects()
        if len(sel) < 2:
            raise ValueError("Select one or more wheel objects, then the driver (active last).")

        control = bpy.context.view_layer.objects.active
        if control is None or control not in sel:
            raise ValueError(
                "Invalid selection. Make sure the driver/control object is the active object."
            )
        wheels = [o for o in sel if o is not control]

        return control, wheels

    def set_wheel_height(self):
        """Get the wheel height from the selected object's bounding box."""
        import bpy

        from blendertk.core_utils._core_utils import selected_objects

        sel = selected_objects()
        if not sel:
            self.sb.message_box("Select a single object to determine wheel height.")
            return
        active = bpy.context.view_layer.objects.active
        obj = active if active in sel else sel[0]

        # Determine dimension based on the inferred rotation axis
        # Move Z -> Rotate X -> Diameter is Y or Z (Height/Depth)
        # Move X -> Rotate Z -> Diameter is X or Y (Width/Height)
        # Move Y -> Rotate Y -> Diameter is X or Z

        # We use the max dimension perpendicular to the rotation axis to ensure we get diameter
        dims = obj.dimensions
        move_axis = self.movement_axis

        if "X" in move_axis:  # Moving X, Rotating Z
            # Perpendiculars are X and Y.
            wheel_size = max(dims[0], dims[1])

        elif "Y" in move_axis:  # Moving Y, Rotating Y
            # Perpendiculars X and Z
            wheel_size = max(dims[0], dims[2])

        else:  # Moving Z, Rotating X (Default)
            # Perpendiculars Y and Z
            wheel_size = max(dims[1], dims[2])

        self.ui.s000.setText(str(round(wheel_size, 3)))

    def s000_init(self, widget):
        """Initialize the wheel height field's option-box menu."""
        widget.option_box.menu.add(
            "QPushButton",
            setText="Get Wheel Size",
            setObjectName="b010",
            setToolTip="Determine wheel diameter from the selected object's bounding box,\nbased on the current movement axis.",
        )
        widget.option_box.menu.b010.clicked.connect(self.set_wheel_height)

    def _on_selection_changed(self):
        """Handle selection change: invalidate cached rig and update UI."""
        # Discard the cached WheelRig so the property re-resolves from
        # the current selection next time it is accessed.
        self._wheel_rig = None
        self.update_rig_name_placeholder()

    def update_rig_name_placeholder(self):
        """Update the rig name placeholder based on the driver (active object)."""
        try:
            import bpy
        except ImportError:  # Qt-only harness — no selection to reflect (bpy-free load contract)
            return

        control = bpy.context.view_layer.objects.active
        if control is None:
            return

        default_name = f"{control.name}_wheel_rig"
        self.ui.txt000.setPlaceholderText(default_name)

    def cleanup(self):
        """Unsubscribe from the centralized ScriptJobManager."""
        ScriptJobManager.instance().unsubscribe_all(self)

    @property
    def wheel_rig(self):
        """Get or create the wheel rig attached to the selected control.

        Returns None if the current selection is invalid, so the property is safe for
        introspection (e.g. ``inspect.getmembers``).
        """
        try:
            rig = self._wheel_rig
            if rig is None:
                raise AttributeError
            rig.control.name  # a deleted bpy object raises ReferenceError on attribute access
            return rig
        except (AttributeError, ReferenceError):
            self._wheel_rig = None
            try:
                control, wheels = self.resolve_selection()
            except ValueError:
                return None

            # Check persistent ID on control to recover correct name
            stored = control.get("wheel_rig_id")
            if stored:
                rig_name = stored
                self.rig_name = rig_name  # Sync UI
            else:
                rig_name = self.rig_name or f"{control.name}_wheel_rig"

            self._wheel_rig = WheelRig(
                control,
                wheels,
                rig_name=rig_name,
                freeze_transforms=self.ui.chk010.isChecked(),
            )
            self.logger.info(f"Created new wheel rig: {self._wheel_rig.rig_name}")

            return self._wheel_rig

    # NOTE: not decorated with `undo_checkpoint` (unlike mayatk's `@CoreUtils.undoable` here) —
    # `WheelRig.rig_rotation` already pushes one; Blender's `undo_push` is a flat checkpoint
    # marker (not a Maya-style openChunk/closeChunk pair), so stacking a second decorator here
    # would create TWO undo steps for one click instead of nesting into one.
    def b000(self):
        """Create or update Wheel Rig."""
        wheel_rig = self.wheel_rig
        if not wheel_rig:
            self.sb.message_box(
                "Select one or more wheel objects, then the driver (active last)."
            )
            return

        _, wheels = self.resolve_selection()

        try:
            wheel_rig.rig_rotation(
                movement_axis=self.movement_axis,
                rotation_index=self.rotation_axis,
                wheel_height=float(self.ui.s000.text() or 1.0),
                wheels=wheels,
                use_world_space=self._use_world_space,
            )
        except Exception as e:
            self.sb.message_box(f"Error setting up wheel rig: {e}")


# -----------------------------------------------------------------------------


if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("wheel_rig", reload=True)
    ui.show(pos="screen", app_exec=True)

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
