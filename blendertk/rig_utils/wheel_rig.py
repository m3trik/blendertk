# !/usr/bin/python
# coding=utf-8
"""Wheel Rig — wheels that roll as their control moves (tentacle's Wheel Rig panel drives it).

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
deferred into the call bodies, so importing the module / resolving the package surface never needs
a running Blender.
"""

import pythontk as ptk

from blendertk.core_utils._core_utils import CoreUtils
from blendertk.edit_utils.naming._naming import Naming
from blendertk.rig_utils._rig_utils import RigUtils
from blendertk.xform_utils._xform_utils import XformUtils


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

    #: mayatk's channel names (what tentacle's shared panel passes) -> the control's travel
    #: channel (a ``TRANSFORMS`` driver variable's type) and the wheel's ``rotation_euler``
    #: index. Blender's own spellings (``"LOC_Z"``, ``0``) are taken as they are.
    _TRAVEL_CHANNELS = {"translateX": "LOC_X", "translateY": "LOC_Y", "translateZ": "LOC_Z"}
    _ROTATION_INDICES = {"rotateX": 0, "rotateY": 1, "rotateZ": 2}

    def __init__(self, control, wheels, rig_name=None, freeze_transforms=True):
        super().__init__()
        self.control = RigUtils.resolve_object(control)
        resolved_wheels = [
            RigUtils.resolve_object(o) for o in ptk.make_iterable(wheels)
        ]

        if (
            self.control is None
            or not resolved_wheels
            or any(w is None for w in resolved_wheels)
        ):
            raise ValueError("Invalid control or wheel inputs.")
        self.wheels = resolved_wheels

        # Check for persistent rig ID
        stored_name = self._stored_name(self.control)
        self._rig_name = (
            rig_name or stored_name or Naming.generate_unique_name("wheel_rig")
        )

        # Persist ID
        self.control["wheel_rig_id"] = self._rig_name

        if freeze_transforms:
            # Freeze location only — rotation must be preserved so the auto-flip pass can read
            # mirrored-wheel orientation from the world matrix.
            XformUtils.freeze_transforms(
                self.control, location=True, rotation=False, scale=False
            )
            XformUtils.freeze_transforms(
                self.wheels, location=True, rotation=False, scale=False
            )

    @property
    def rig_name(self) -> str:
        return self._rig_name

    @rig_name.setter
    def rig_name(self, name: str):
        self._rig_name = name
        self.logger.debug(f"Rig name set to: {self._rig_name}")

    # ------------------------------------------------------------ the selection (the panel's)
    @staticmethod
    def _stored_name(control):
        """The rig ID an earlier rig stamped on *control* (``wheel_rig_id``), or ``None``."""
        control = RigUtils.resolve_object(control)
        return (control.get("wheel_rig_id") or None) if control is not None else None

    @staticmethod
    def _control_and_wheels():
        """The selection as ``(control, wheels)``: the ACTIVE object drives -- Blender keeps no
        click order, so it stands for mayatk's "last selected" -- every other selected object
        is a wheel.

        Raises:
            ValueError: Fewer than two objects are selected, or the active one is not among them.
        """
        sel = CoreUtils.selected_objects()
        if len(sel) < 2:
            raise ValueError("Select one or more wheel objects, then the driver (active last).")
        control = CoreUtils.active_object()  # the window's layer, not the default
        if control is None or control not in sel:
            raise ValueError(
                "Invalid selection. Make sure the driver/control object is the active object."
            )
        return control, [o for o in sel if o != control]

    @classmethod
    def default_rig_name(cls, control=None):
        """The name a rig on *control* takes when none is given: the rig ID an earlier rig
        stamped on it (a re-run updates that rig in place), else ``<control>_wheel_rig`` --
        mirror of mayatk's ``default_rig_name``.

        Parameters:
            control (str/object/None): The driver. ``None`` (the default) takes the active
                object.

        Returns:
            str/None: The name, or ``None`` with no active object (or no scene: outside
            Blender, as a Qt-only load of the panel runs).
        """
        if control is None:
            try:
                control = CoreUtils.active_object()
            except ImportError:  # no bpy: nothing to read
                return None
        control = RigUtils.resolve_object(control)
        if control is None:
            return None
        return cls._stored_name(control) or f"{control.name}_wheel_rig"

    @classmethod
    def from_selection(cls, rig_name=None, freeze_transforms=True, **kwargs):
        """Rig the selected wheels to the driver as ONE undo step: what tentacle's Wheel Rig
        panel runs (mirror of mayatk's ``from_selection``).

        The ACTIVE object drives (Blender keeps no click order); every other selected object
        is a wheel. The rig takes the driver's stored rig ID when an earlier rig left one -- a
        re-run updates that rig in place -- else *rig_name*, else ``<driver>_wheel_rig``.

        Parameters:
            rig_name (str): The name for a driver that carries no rig ID yet.
            freeze_transforms (bool): Freeze location on the driver and the wheels first (see
                the constructor).
            **kwargs: :meth:`rig_rotation`'s options (``movement_axis``, ``rotation_axis``,
                ``wheel_height``, ``use_world_space``).

        Returns:
            WheelRig: The rig.

        Raises:
            ValueError: An invalid selection or wheel height -- checked before the undo step
                opens, so a refused click leaves none.
        """
        control, wheels = cls._control_and_wheels()
        if kwargs.get("wheel_height", 1.0) <= 0:
            raise ValueError(f"Invalid wheel height: {kwargs['wheel_height']}")
        name = cls._stored_name(control) or rig_name or cls.default_rig_name(control)
        return cls._rig_undoable(control, wheels, name, freeze_transforms, kwargs)

    @classmethod
    @CoreUtils.undo_checkpoint
    def _rig_undoable(cls, control, wheels, rig_name, freeze_transforms, options):
        """The rig built and its rotation wired as ONE undo step (:meth:`from_selection`).

        ``undo_checkpoint``, not ``undoable``: the rotation is drivers, which must recompile
        after the step is pushed. It runs :meth:`_rig_rotation`, the body of
        :meth:`rig_rotation` without that method's own step, so none nests in this one.
        """
        rig = cls(control, wheels, rig_name=rig_name, freeze_transforms=freeze_transforms)
        rig._rig_rotation(wheels=rig.wheels, **options)
        return rig

    @staticmethod
    def wheel_size(obj=None, movement_axis="translateZ"):
        """A wheel's diameter from *obj*'s dimensions: its larger extent across the two axes
        the wheel turns in for *movement_axis* (Move X rolls on Z: the X/Y extents; Move Y
        spins on Y: X/Z; Move Z pitches on X: Y/Z) -- mirror of mayatk's ``wheel_size``.

        Parameters:
            obj (str/object/None): The wheel. ``None`` (the default) takes the active object
                when it is selected, else the first selected one.
            movement_axis (str): The driver's travel channel (``translateX`` / ``translateY`` /
                ``translateZ``, or Blender's ``LOC_*``).

        Returns:
            float/None: The diameter, or ``None`` with nothing to measure.
        """
        if obj is None:
            sel = CoreUtils.selected_objects()
            if not sel:
                return None
            active = CoreUtils.active_object()
            obj = active if active in sel else sel[0]
        else:
            obj = RigUtils.resolve_object(obj)
            if obj is None:
                return None
        dims = obj.dimensions
        if "X" in movement_axis:
            return max(dims[0], dims[1])
        if "Y" in movement_axis:
            return max(dims[0], dims[2])
        return max(dims[1], dims[2])

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
            self.logger.info(
                f"Deleted {len(drivers)} driver(s) for rig: {self.rig_name}"
            )
        else:
            self.logger.info(f"No drivers found to delete for rig: {self.rig_name}")

    @CoreUtils.undo_checkpoint
    def rig_rotation(
        self,
        movement_axis: str = "translateZ",
        rotation_axis=None,
        wheel_height: float = 1.0,
        wheels: list = None,
        use_world_space: bool = False,
    ) -> list:
        """
        Rig wheels to rotate based on control movement (mirror of mayatk's ``rig_rotation``:
        name, signature and channel names).

        Parameters:
            movement_axis: The control's travel channel that drives rotation: mayatk's
                ``"translateX"`` / ``"translateY"`` / ``"translateZ"`` (what tentacle's panel
                passes), or Blender's ``"LOC_X"`` / ``"LOC_Y"`` / ``"LOC_Z"``.
            rotation_axis: The wheels' rotation channel: ``"rotateX"`` / ``"rotateY"`` /
                ``"rotateZ"``, or its ``rotation_euler`` index (0/1/2). Auto-inferred from
                ``movement_axis`` when ``None``.
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

        Raises:
            ValueError: No wheels, a wheel height of 0 or less, or an unknown axis.
        """
        return self._rig_rotation(
            movement_axis, rotation_axis, wheel_height, wheels, use_world_space
        )

    def _rig_rotation(
        self,
        movement_axis="translateZ",
        rotation_axis=None,
        wheel_height=1.0,
        wheels=None,
        use_world_space=False,
    ) -> list:
        """:meth:`rig_rotation`'s body, without an undo step of its own (:meth:`_rig_undoable`
        runs it inside the one it pushes)."""
        import bpy

        wheels = (
            self.wheels
            if wheels is None
            else [
                w
                for w in (RigUtils.resolve_object(o) for o in ptk.make_iterable(wheels))
                if w
            ]
        )
        if not wheels:
            raise ValueError("No wheels specified. You must pass wheels to rig.")
        if wheel_height <= 0:
            raise ValueError(f"Invalid wheel height: {wheel_height}")

        travel = self._TRAVEL_CHANNELS.get(movement_axis, movement_axis)
        if travel not in self._TRAVEL_CHANNELS.values():
            raise ValueError(
                f"Invalid movement axis: {movement_axis!r} (translateX/Y/Z or LOC_X/Y/Z)."
            )
        movement_axis = travel

        if rotation_axis is None:
            # Auto-infer: the standard vehicle dynamics assumption --
            # forward Z -> pitch (rotate X), side X -> roll (rotate Z).
            rotation_index = {"LOC_Z": 0, "LOC_X": 2, "LOC_Y": 1}[movement_axis]
            self.logger.info(
                f"Auto-inferred rotation index: {rotation_index} from movement: {movement_axis}"
            )
        else:
            rotation_index = self._ROTATION_INDICES.get(rotation_axis, rotation_axis)
            if rotation_index not in (0, 1, 2):
                raise ValueError(
                    f"Invalid rotation axis: {rotation_axis!r} (rotateX/Y/Z or 0/1/2)."
                )

        # Smart attribute management for wheel height: find or create a "wheelHeight" custom
        # property matching the requested height, or a new suffixed one if existing ones differ
        # significantly — lets ONE control drive multiple wheel groups with different diameters.
        height_attr_name = "wheelHeight"
        suffix_idx = 0
        epsilon = 0.01
        while True:
            candidate = (
                "wheelHeight" if suffix_idx == 0 else f"wheelHeight_{suffix_idx}"
            )

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

            RigUtils.remove_driver(
                wheel, "rotation_euler", rotation_index
            )  # re-entrant

            # Build the driver + ALL variables BEFORE the expression — an expression that
            # references a not-yet-added variable evaluates it as 0 (div-by-zero) and sticks.
            fcurve = RigUtils.add_transform_driver(
                wheel,
                "rotation_euler",
                rotation_index,
                self.control,
                movement_axis,
                space=space,
                var_name="travel",
            )
            RigUtils.add_prop_var(
                fcurve, "height", self.control, f'["{height_attr_name}"]'
            )
            RigUtils.add_prop_var(fcurve, "enable", self.control, '["enableRotation"]')
            RigUtils.add_prop_var(
                fcurve, "direction", self.control, '["spinDirection"]'
            )
            # radians = 2*travel/height (== Maya's distance/(pi*height)*360deg). Pure arithmetic
            # (no ternary -> stays on Blender's fast driver parser, no full-Python security gate);
            # height can't hit 0 (custom-prop min=0.001).
            fcurve.driver.expression = (
                f"enable * direction * {auto_flip} * 2.0 * travel / height"
            )

            self.logger.debug(
                f"Rigged wheel: {wheel.name} with driver: rotation_euler[{rotation_index}]"
            )

        RigUtils.refresh_drivers(
            wheels
        )  # post-build recompile (script-built driver gotcha)
        self.logger.info(
            f"Wheel rig rotation updated for wheels: {[w.name for w in wheels]}"
        )
        return wheels
