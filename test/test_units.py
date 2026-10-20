"""blendertk.core_utils.units headless test: conversions by the kind of value.

Mirror of mayatk's ``test_units.py``. Blender answers every length in its one
unit, the Blender unit (BU), and every angle in radians, however it is asked;
the scene's ``scale_length`` says how many metres a BU is (the factor the FBX
writer scales the geometry by), and ``length_unit`` / ``system_rotation`` only
choose what the UI displays. Pinned here: ``"internal"`` and ``"working"`` are
both the BU, read when the value is; a unit system of None ignores the scale,
as the FBX writer does; angles are radians whatever the display; sequences nest
and ``None`` passes; an unknown unit is an error; ``working`` names the display
units, and ``working_as`` sets them for a block or a call and puts them back
-- the values bpy answers never change.

Run: blender --background --factory-startup --python blendertk/test/test_units.py
"""

import math
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for p in (REPO, os.path.join(MONO, "pythontk")):
    if p not in sys.path:
        sys.path.insert(0, p)

lines = []


def check(name, cond, detail=""):
    lines.append(
        f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + str(detail)) if detail else ''}"
    )


def near(a, b, tol=1e-6):
    """Equal to within *tol* of the larger of 1 and *b*: ``scale_length`` is an
    RNA float, single precision (0.01 reads back 0.0099999998)."""
    if isinstance(a, (list, tuple)):
        return len(a) == len(b) and all(near(x, y, tol) for x, y in zip(a, b))
    return abs(float(a) - float(b)) <= tol * max(1.0, abs(float(b)))


def raises(error, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except error:
        return True
    except Exception:  # noqa: BLE001 -- the wrong error is a failure, not a crash
        return False
    return False


try:
    import bpy

    import blendertk as btk
    from blendertk.core_utils.units import Units

    settings = bpy.context.scene.unit_settings

    def units_reset():
        settings.system, settings.scale_length = "METRIC", 1.0
        settings.length_unit, settings.system_rotation = "METERS", "DEGREES"

    check("btk.Units resolves from core_utils.units", btk.Units is Units)

    # ---- the Blender unit: internal == working == scale_length metres --------
    units_reset()
    settings.scale_length = 0.01  # a unit is a centimetre
    check(
        "internal is scale_length metres, read when the value is",
        near(Units.metres_per("internal"), 0.01)
        and near(Units.convert(250.0), 2.5)
        and near(Units.convert(250.0, "working"), 2.5),
        (Units.metres_per("internal"), Units.convert(250.0)),
    )
    settings.scale_length = 1.0
    check(
        "...and follows the scale it is read under",
        near(Units.convert(250.0), 250.0),
        Units.convert(250.0),
    )
    settings.scale_length = 0.001
    check(
        "a metre as bpy takes it (Blender units)",
        near(Units.convert(1.0, "m", "internal"), 1000.0)
        and near(Units.convert(1.0, "m", "working"), 1000.0),
        Units.convert(1.0, "m", "internal"),
    )
    check(
        "unit names convert between themselves",
        near(Units.convert(1.0, "in", "cm"), 2.54) and near(Units.metres_per("ft"), 0.3048),
    )
    # The FBX writer reads a unit system of None as one metre a unit, whatever
    # the scale (io_scene_fbx ``units_blender_to_fbx_factor``); so does the UI,
    # which disables the scale there.
    settings.scale_length = 0.01
    settings.system = "NONE"
    check(
        "a unit system of None ignores the scale, as the FBX writer does",
        near(Units.metres_per("internal"), 1.0),
        Units.metres_per("internal"),
    )
    units_reset()

    # ---- the display never changes a value ------------------------------------
    settings.length_unit = "MILLIMETERS"
    check(
        "the display unit converts nothing (a BU is still scale_length metres)",
        near(Units.convert(2.5, "working"), 2.5),
        Units.convert(2.5, "working"),
    )
    units_reset()

    # ---- angles: radians inside, whatever the display -------------------------
    settings.system_rotation = "DEGREES"
    check(
        "an angle bpy answered is radians, whatever the display",
        near(Units.angle(math.pi), 180.0) and near(Units.angle(1.0, "internal"), math.degrees(1.0)),
        Units.angle(math.pi),
    )
    settings.system_rotation = "RADIANS"
    check(
        "...under a radian display too",
        near(Units.angle(math.pi), 180.0)
        and near(Units.angle(90.0, "deg", "rad"), math.pi / 2)
        and near(Units.angle(90.0, "deg", "working"), math.pi / 2),
    )
    units_reset()

    # ---- sequences, None, unknown units ----------------------------------------
    settings.scale_length = 0.01
    check(
        "sequences nest and None passes",
        near(Units.convert([[100.0, 200.0], [300.0]]), [[1.0, 2.0], [3.0]])
        and Units.convert(None) is None,
        Units.convert([[100.0, 200.0], [300.0]]),
    )
    units_reset()
    check(
        "an unknown unit is an error, not a guess",
        raises(ValueError, Units.convert, 1.0, "furlong")
        and raises(ValueError, Units.angle, 1.0, "grad")
        and raises(ValueError, Units.working_as, "furlong")
        and raises(ValueError, Units.working_as, None, "min"),
    )

    # ---- working: the display units, by short name -----------------------------
    check(
        "working names the display units",
        (Units.working(), Units.working("linear"), Units.working("angle"))
        == ("m", "m", "deg"),
        (Units.working(), Units.working("angle")),
    )
    settings.system, settings.length_unit = "IMPERIAL", "INCHES"
    settings.system_rotation = "RADIANS"
    check(
        "...an imperial and a radian display",
        (Units.working(), Units.working("angle")) == ("in", "rad"),
        (Units.working(), Units.working("angle")),
    )
    settings.system = "NONE"
    check("...and an adaptive one", Units.working() == "adaptive", Units.working())
    units_reset()

    # ---- working_as: a block or a call, then put back ---------------------------
    settings.scale_length = 0.01
    with Units.working_as("in", "rad"):
        inside = (settings.system, settings.length_unit, settings.system_rotation)
        inside_scale = settings.scale_length
        with Units.working_as("in"):  # re-entrant: already so, a no-op
            nested = Units.working()
        with Units.working_as(angle="deg"):  # None keeps the other one
            mixed = (Units.working(), Units.working("angle"))
        after_nested = (Units.working(), Units.working("angle"))
    check(
        "working_as switches the display system and unit for a block",
        inside == ("IMPERIAL", "INCHES", "RADIANS") and near(inside_scale, 0.01),
        (inside, inside_scale),
    )
    check(
        "...re-entrant, and None leaves that one as it is",
        nested == "in" and mixed == ("in", "deg") and after_nested == ("in", "rad"),
        (nested, mixed, after_nested),
    )
    check(
        "...and puts the display back after, the scale never touched",
        (settings.system, settings.length_unit, settings.system_rotation)
        == ("METRIC", "METERS", "DEGREES")
        and near(settings.scale_length, 0.01),
    )

    settings.system = "NONE"

    @Units.working_as("cm")
    def decorated():
        return (settings.system, Units.working())

    check(
        "as a decorator, from a unit system of None and back",
        decorated() == ("METRIC", "cm")
        and (settings.system, settings.length_unit) == ("NONE", "ADAPTIVE"),
        (decorated(), settings.system, settings.length_unit),
    )
    units_reset()
    try:
        with Units.working_as("mm"):
            raise RuntimeError("inside")
    except RuntimeError:
        pass
    check(
        "...and puts it back when the block raises",
        settings.length_unit == "METERS",
        settings.length_unit,
    )
    units_reset()
    bpy.ops.mesh.primitive_cube_add(location=(1.5, 0.0, 0.0))
    cube = bpy.context.active_object
    with Units.working_as("mm", "rad"):
        read = cube.location.x
    check("bpy answers the same value whatever the display", read == 1.5, read)

except Exception as e:
    lines.append(f"FAIL setup: {e!r}")
    lines.append(traceback.format_exc())

ok = bool(lines) and all(line.startswith("OK") for line in lines)
for line in lines:
    print(line)
print(
    f"===RESULT: {'PASS' if ok else 'FAIL'}=== "
    f"({sum(1 for x in lines if x.startswith('OK'))}/{len(lines)})"
)
