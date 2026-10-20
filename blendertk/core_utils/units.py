# !/usr/bin/python
# coding=utf-8
"""Blender's lengths and angles, converted by the kind of value that holds them.

Mirror of mayatk's ``core_utils.units``. Where Maya answers a length in one of
two units by how it is asked, Blender answers every length in ONE unit, the
Blender unit (BU) -- ``bpy`` properties, ``mathutils`` matrices, ``bmesh``
coordinates alike -- and every angle in radians: nothing converts on read. The
scene's unit settings say what a BU is: ``scale_length`` metres (1 under a
unit system of None, which disables the scale), the factor Blender's FBX
writer scales the geometry by (``apply_unit_scale``); ``length_unit`` and
``system_rotation`` only choose how the UI DISPLAYS a value. So mayatk's two
kinds of value collapse to one here: ``"internal"`` and ``"working"`` are both
the BU, and both radians for an angle.

The Scene Exporter switches the units (``set_linear_unit``) before it produces
the records, and the FBX write then scales the geometry by them -- so a record
reads the metres per BU (:meth:`Units.metres_per`) when it is produced, never
when its tool ran, and a deliverable record's lengths follow the geometry
(``ptk.SceneRecords``).
"""
import contextlib
import math
from typing import Any, Optional, Tuple

import pythontk as ptk


class Units(ptk.HelpMixin):
    """Lengths and angles between Blender's units and the ones a deliverable
    ships (mirror of ``mtk.Units``).

    A *source* or *target* is ``"internal"`` or ``"working"`` (both the Blender
    unit, read now; radians for an angle) or a unit name -- ``"cm"``, ``"m"``,
    ``"in"`` ... (:attr:`ptk.MathUtils.LENGTH_UNIT_FACTORS`), or ``"deg"``,
    ``"rad"``, ``"min"``, ``"sec"`` for an angle. Values are a number or a
    (nested) sequence of numbers; ``None`` passes through.
    """

    #: Radians in one of each angle unit (mayatk's table).
    RADIANS_PER: dict = {
        "rad": 1.0,
        "deg": math.pi / 180.0,
        "min": math.pi / 10800.0,
        "sec": math.pi / 648000.0,
    }
    #: A display length unit's short name -> ``(unit system, length_unit)``;
    #: ``"adaptive"`` keeps the system.
    LENGTH_UNITS: dict = {
        "km": ("METRIC", "KILOMETERS"),
        "m": ("METRIC", "METERS"),
        "cm": ("METRIC", "CENTIMETERS"),
        "mm": ("METRIC", "MILLIMETERS"),
        "um": ("METRIC", "MICROMETERS"),
        "mi": ("IMPERIAL", "MILES"),
        "ft": ("IMPERIAL", "FEET"),
        "in": ("IMPERIAL", "INCHES"),
        "thou": ("IMPERIAL", "THOU"),
        "adaptive": (None, "ADAPTIVE"),
    }
    #: A display angle unit's short name -> ``system_rotation``.
    ANGLE_UNITS: dict = {"deg": "DEGREES", "rad": "RADIANS"}

    @staticmethod
    def working_as(
        linear: Optional[str] = None, angle: Optional[str] = None
    ) -> "_WorkingAs":
        """The scene's display units set to *linear* and *angle* for a block or
        a call (a context manager and a decorator), and put back after;
        ``None`` leaves that one as it is.

        For mirror parity only: Blender's API never converts a value, so
        inside, ``bpy`` takes and answers exactly the Blender units and
        radians it does outside -- only the UI shows them otherwise. A length
        unit of the other system switches the unit system with it; the scale
        (``scale_length``, what a Blender unit IS) is never touched.

        Raises:
            ValueError: A unit Blender cannot display (:attr:`LENGTH_UNITS`,
                :attr:`ANGLE_UNITS`).
        """
        return _WorkingAs(linear, angle)

    @classmethod
    def working(cls, kind: str = "linear") -> str:
        """The display unit now: ``"m"``, ``"cm"``, ``"in"`` ... or
        ``"adaptive"`` (*kind* ``"linear"``), or ``"deg"`` / ``"rad"``
        (``"angle"``). What the UI shows, not what a value is in:
        :meth:`metres_per` reads the scale, never this."""
        settings = cls._settings()
        if kind == "angle":
            names = {v: k for k, v in cls.ANGLE_UNITS.items()}
            return names[settings.system_rotation]
        names = {unit: name for name, (_system, unit) in cls.LENGTH_UNITS.items()}
        return names.get(settings.length_unit, str(settings.length_unit).lower())

    @classmethod
    def metres_per(cls, unit: str = "internal") -> float:
        """Metres in one *unit* (``"internal"``, ``"working"`` or a unit name).

        ``"internal"`` and ``"working"`` are the Blender unit: the scene's
        ``scale_length`` metres, read now, or 1 under a unit system of None --
        the factor the FBX writer scales the geometry by
        (io_scene_fbx ``units_blender_to_fbx_factor``).

        Raises:
            ValueError: An unknown unit name.
        """
        if unit in ("internal", "working"):
            settings = cls._settings()
            if settings.system == "NONE":
                return 1.0
            return float(settings.scale_length or 1.0)
        factor = ptk.MathUtils.metres_per_unit(unit)
        if factor is None:
            raise ValueError(f"Unknown length unit {unit!r}.")
        return factor

    @classmethod
    def convert(cls, value: Any, source: str = "internal", target: str = "m") -> Any:
        """A length (or lengths) from *source* units to *target* units.

        Example:
            Units.convert(obj.matrix_world.translation[:])  # in metres
            Units.convert(1.0, "m", "internal")  # a metre, as bpy takes it
        """
        return cls._scaled(value, cls.metres_per(source) / cls.metres_per(target))

    @classmethod
    def angle(cls, value: Any, source: str = "working", target: str = "deg") -> Any:
        """An angle (or angles) from *source* units to *target* units.

        Example:
            Units.angle(light.data.spot_size)  # degrees: bpy answers radians
        """
        return cls._scaled(value, cls._radians_per(source) / cls._radians_per(target))

    @staticmethod
    def _settings():
        """The current scene's ``unit_settings``."""
        import bpy

        return bpy.context.scene.unit_settings

    @classmethod
    def _radians_per(cls, unit: str) -> float:
        name = "rad" if unit in ("internal", "working") else unit
        if name not in cls.RADIANS_PER:
            raise ValueError(f"Unknown angle unit {name!r}.")
        return cls.RADIANS_PER[name]

    @classmethod
    def _scaled(cls, value: Any, factor: float) -> Any:
        if value is None:
            return None
        if isinstance(value, (list, tuple)):
            return [cls._scaled(v, factor) for v in value]
        return float(value) * factor


class _WorkingAs(contextlib.ContextDecorator):
    """:meth:`Units.working_as`: re-entrant, a no-op when the units already are."""

    def __init__(self, linear: Optional[str], angle: Optional[str]):
        if linear is not None and linear not in Units.LENGTH_UNITS:
            raise ValueError(f"Blender cannot display the length unit {linear!r}.")
        if angle is not None and angle not in Units.ANGLE_UNITS:
            raise ValueError(f"Blender cannot display the angle unit {angle!r}.")
        self.units = (linear, angle)
        self._saved = []

    @staticmethod
    def _read(settings) -> Tuple[str, str, str]:
        return (settings.system, settings.length_unit, settings.system_rotation)

    @staticmethod
    def _write(settings, units: Tuple[str, str, str]) -> None:
        # The system first: switching it resets the length unit to its own.
        settings.system = units[0]
        settings.length_unit = units[1]
        settings.system_rotation = units[2]

    def __enter__(self) -> "_WorkingAs":
        settings = Units._settings()
        current = self._read(settings)
        system, length_unit, rotation = current
        linear, angle = self.units
        if linear is not None:
            wanted_system, length_unit = Units.LENGTH_UNITS[linear]
            if wanted_system is not None:  # "adaptive" keeps the system
                system = wanted_system
        if angle is not None:
            rotation = Units.ANGLE_UNITS[angle]
        wanted = (system, length_unit, rotation)
        self._saved.append(current if current != wanted else None)
        if current != wanted:
            self._write(settings, wanted)
        return self

    def __exit__(self, *exc) -> bool:
        saved = self._saved.pop()
        if saved is not None:
            self._write(Units._settings(), saved)
        return False
