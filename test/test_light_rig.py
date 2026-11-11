"""blendertk LightRig headless test: Blender's binding of pythontk's light rig engine.

The engine (normalize, compare, the record, the swap) is pythontk's and tested
there; this holds what Blender adds: collections as groups, the payload's
centimetres / degrees / Y-up against Blender units / radians / Z-up, the
overrides, and the honest report of what Blender cannot build.

Run: blender --background --factory-startup --python blendertk/test/test_light_rig.py
"""

import math
import os
import shutil
import sys
import tempfile
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


def close(a, b, tol=1e-4):
    return all(abs(x - y) <= tol for x, y in zip(a, b)) and len(a) == len(b)


scratch = tempfile.mkdtemp(prefix="btk_light_rig_")
try:
    import bpy
    import pythontk as ptk
    import blendertk as btk

    def fresh():
        bpy.ops.wm.read_factory_settings(use_empty=True)
        store = ptk.PresetStore("light_rigs", user_dir=os.path.join(scratch, str(len(lines))))
        return btk.LightRig(store=store)

    def rig_group(name="LIGHT_RIG", parent=None):
        col = bpy.data.collections.new(name)
        (parent or bpy.context.scene.collection).children.link(col)
        return col

    def light(col, name, kind="SPOT", loc=(0, 0, 0)):
        obj = bpy.data.objects.new(name, bpy.data.lights.new(name, kind))
        col.objects.link(obj)
        obj.location = loc
        return obj

    # ------------------------------------------------------------- the binding
    check("binds pythontk's engine", issubclass(btk.LightRig, ptk.LightRig))
    check("the host is a LightRigHost", isinstance(btk.LightRig.HOST, ptk.LightRigHost))
    check(
        "every neutral type maps onto a Blender light",
        set(btk.LightRig.TYPES) == set(ptk.LightRigLight.TYPES)
        and set(btk.LightRig.TYPES.values()) <= {"POINT", "SPOT", "AREA", "SUN"},
        btk.LightRig.TYPES,
    )
    check(
        "every neutral setting maps",
        set(btk.LightRig.FRIENDLY) == set(ptk.LightRigLight.SETTINGS),
    )

    # ------------------------------------------------------------- capture
    rig = fresh()
    col = rig_group()
    key = light(col, "Key", "SPOT", (1, 2, 3))
    key.data.energy = 500
    key.data.spot_size = math.radians(30)
    cap = rig.capture()
    entry = cap["lights"][0] if cap["lights"] else {}
    check("capture writes the neutral type", entry.get("type") == "spot", entry)
    check(
        "capture is Y-up centimetres",
        close(entry.get("pos", []), [100.0, 300.0, -200.0]),
        entry.get("pos"),
    )
    check(
        "a Blender light's -Z down reads as a Y-up pitch of -90",
        close(entry.get("rot", []), [-90.0, 0.0, 0.0]),
        entry.get("rot"),
    )
    check(
        "an angle attribute reads in degrees",
        abs(entry.get("attrs", {}).get("spot_size", 0) - 30.0) < 1e-4,
        entry.get("attrs"),
    )
    check(
        "only what differs from a fresh light is captured",
        set(entry.get("attrs", {})) == {"energy", "spot_size"},
        entry.get("attrs"),
    )

    # ------------------------------------------------------------- save / applied
    rig.save("one", description="first")
    check("save records the scene's rig", rig.applied() == ("one", False), rig.applied())
    check("save fills the library entry", rig.store.info("one").get("lights") == 1)
    key.location.x = 5
    check("an edit reads as edited", rig.applied() == ("one", True), rig.applied())

    # ------------------------------------------------------------- apply
    outside = bpy.data.objects.new("Lamp", bpy.data.lights.new("Lamp", "POINT"))
    bpy.context.scene.collection.objects.link(outside)
    report = rig.apply(
        {
            "version": 1,
            "lights": [
                {"name": "Fill", "type": "point", "pos": [0, 100, 0], "intensity": 50},
                {"name": "Warm", "type": "spot", "kelvin": 3200, "cone": 40, "penumbra": 3},
            ],
            "existing": {"Lamp": {"intensity": 7.0}},
        },
        name="two",
    )
    check("apply builds the rig's lights", sorted(report["lights"]) == ["Fill", "Warm"], report)
    check("the old lights went", "Key" not in bpy.data.objects)
    check("a light outside the group stays", "Lamp" in bpy.data.objects)
    fill = bpy.data.objects["Fill"]
    check(
        "a Y-up position lands Z-up in Blender units",
        close(tuple(fill.matrix_world.translation), (0.0, 0.0, 1.0)),
        tuple(fill.matrix_world.translation),
    )
    check("a neutral setting lands on its Blender attribute", fill.data.energy == 50)
    warm = bpy.data.objects["Warm"].data
    check(
        "kelvin sets Blender's temperature",
        warm.use_temperature and abs(warm.temperature - 3200) < 1e-3,
    )
    check("cone lands in radians", abs(warm.spot_size - math.radians(40)) < 1e-5)
    check(
        "a setting Blender lacks is reported, not guessed",
        any("Warm.penumbra" in s for s in report["skipped"]),
        report["skipped"],
    )
    check("an override lands", outside.data.energy == 7.0)
    check("the lights sit in the group", set(bpy.data.collections["LIGHT_RIG"].objects.keys()) == {"Fill", "Warm"})
    check("swap back is armed", rig.can_swap_back())

    # ------------------------------------------------------------- compare / swap back
    check("compare names what changes", "+ Key (SPOT)" in rig.compare("one"), rig.compare("one"))
    back = rig.swap_back()
    check("swap back rebuilds the outgoing rig", back["lights"] == ["Key"], back)
    check(
        "swap back keeps the outgoing rig's edit",
        close(tuple(bpy.data.objects["Key"].location), (5.0, 2.0, 3.0)),
        tuple(bpy.data.objects["Key"].location),
    )
    check("swap back restores an override", abs(outside.data.energy - 10.0) < 1e-6, outside.data.energy)
    check("swap back names the rig it returned to", rig.applied() == ("one", False), rig.applied())
    check("the scene's rigs", rig.scene_rigs() == [("LIGHT_RIG", "one")], rig.scene_rigs())

    # ------------------------------------------------------------- round trip
    rig = fresh()
    parent = rig_group("RIGS")
    col = rig_group("LOOK", parent)
    area = light(col, "Soft", "AREA", (0.5, -1.0, 2.0))
    area.rotation_mode = "YXZ"
    area.rotation_euler = (0.3, -0.7, 1.1)
    area.scale = (2.0, 1.0, 1.0)
    area.data.size = 0.25
    area.visible_camera = True
    area.hide_render = True
    before = rig.capture("LOOK")
    world = area.matrix_basis.copy()  # matrix_world follows only a depsgraph update
    rig.save("look", group="LOOK")
    rig.apply("look", group="LOOK")
    after = rig.capture("LOOK")
    check("a rig round-trips unchanged", rig.diff(before, after) == [], rig.diff(before, after))
    rebuilt = bpy.data.objects["Soft"]
    check(
        "the rebuilt light sits where it was",
        all(close(world[i], rebuilt.matrix_basis[i]) for i in range(4)),
    )
    check("the rotate order carries", rebuilt.rotation_mode == "YXZ")
    check("a length attribute carries", abs(rebuilt.data.size - 0.25) < 1e-6, rebuilt.data.size)
    check("an object attribute carries", rebuilt.visible_camera is True)
    check("hidden carries", rebuilt.hide_render is True)
    check(
        "the group keeps its place under its parent",
        "LOOK" in bpy.data.collections["RIGS"].children,
    )

    # ------------------------------------------------------------- units
    rig = fresh()
    bpy.context.scene.unit_settings.scale_length = 0.01  # one Blender unit is a centimetre
    rig.apply({"version": 1, "lights": [{"name": "C", "type": "point", "pos": [100, 0, 0], "radius": 5}]})
    c = bpy.data.objects["C"]
    check(
        "a scene of centimetre units takes centimetres",
        close(tuple(c.matrix_world.translation), (100.0, 0.0, 0.0)) and abs(c.data.shadow_soft_size - 5.0) < 1e-6,
        (tuple(c.matrix_world.translation), c.data.shadow_soft_size),
    )
    check("and captures them back", close(rig.capture()["lights"][0]["pos"], [100.0, 0.0, 0.0]))

    # ------------------------------------------------------------- what does not carry
    rig = fresh()
    report = rig.apply(
        {
            "version": 1,
            "lights": [
                {"name": "M", "type": "spotLight", "attrs": {"coneAngle": 40}},
                {"name": "G", "type": "spot", "gobo": "slide.png", "aim": [0, 0, -100]},
            ],
        }
    )
    check("a type Blender lacks is left out and said", any(s.startswith("M: a spotLight") for s in report["skipped"]), report)
    check("a gobo is said", any("gobo filter" in s for s in report["skipped"]), report)
    g = bpy.data.objects["G"]
    check(
        "aim points the light's -Z at the target",
        close(tuple(g.matrix_world.to_3x3() @ __import__("mathutils").Vector((0, 0, -1))), (0.0, 1.0, 0.0)),
    )

    # ------------------------------------------------------------- overrides
    rig = fresh()
    lamp = bpy.data.objects.new("Bulb", bpy.data.lights.new("Bulb", "POINT"))
    bpy.context.scene.collection.objects.link(lamp)
    mesh = bpy.data.objects.new("Shade", bpy.data.meshes.new("Shade"))
    bpy.context.scene.collection.objects.link(mesh)
    driven = lamp.data.driver_add("color", 0)
    driven.driver.expression = "0.5"
    report = rig.apply(
        {
            "version": 1,
            "lights": [],
            "fixtures": [{"name": "shade", "mode": "lamp", "meshes": ["Shade"], "lamps": {"Bulb": 40}}],
            "existing": {"Bulb": {"color": [1, 0, 0]}, "Ghost": {"energy": 1}},
        }
    )
    check("a fixture's lamp turns on", lamp.data.energy == 40)
    check("a fixture's mesh stops lighting the room", mesh.visible_diffuse is False)
    check("a driven plug is left alone and said", any("Bulb.color: driven" in s for s in report["skipped"]), report["skipped"])
    check("a missing node is said", any(s.startswith("Ghost: not in the scene") for s in report["skipped"]), report["skipped"])
    rig.swap_back()
    check("swap back puts the fixture back", lamp.data.energy == 10.0 and mesh.visible_diffuse is True)

    # ------------------------------------------------------------- remove
    rig = fresh()
    col = rig_group()
    shared = light(col, "Shared", "POINT")
    bpy.context.scene.collection.objects.link(shared)
    light(col, "Own", "POINT")
    removed = rig.remove()
    check("remove deletes the group", removed == ["LIGHT_RIG"] and "LIGHT_RIG" not in bpy.data.collections)
    check("remove deletes a light only the group held", "Own" not in bpy.data.objects and "Own" not in bpy.data.lights)
    check("remove keeps a light linked elsewhere", "Shared" in bpy.data.objects)
    check("remove of no group removes nothing", rig.remove() == [])

except Exception as e:
    traceback.print_exc()
    check("light rig test raised", False, repr(e))
finally:
    shutil.rmtree(scratch, ignore_errors=True)

passed = sum(1 for line in lines if line.startswith("OK"))
for line in lines:
    print(line)
result = "PASS" if all(line.startswith("OK") for line in lines) else "FAIL"
print(f"===RESULT: {result}=== ({passed}/{len(lines)})")
