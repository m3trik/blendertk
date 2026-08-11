"""blendertk.rig_utils.telescope_rig headless test — constraint + driver telescope rig.
Run: blender --background --factory-startup --python blendertk/test/test_telescope_rig.py

Verifies the rig BUILDS (constraints + scale driver wired), EVALUATES (build pose preserved, the
segments track the base->end lerp, the middle segment's driven scale collapses AND clamps), that a
two-segment strut and auto-created handles work, and that teardown restores what it changed.
"""
import sys, os, traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)            # blendertk/
MONO = os.path.dirname(REPO)           # _scripts/
for p in (REPO, os.path.join(MONO, "pythontk")):
    if p not in sys.path:
        sys.path.insert(0, p)

lines = []
def check(name, cond, detail=""):
    lines.append(f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}")

def approx(a, b, tol=1e-3):
    return abs(a - b) <= tol

try:
    import bpy
    from blendertk.rig_utils.telescope_rig import TelescopeRig, TelescopeRigBundle

    def reset():
        bpy.ops.object.select_all(action="DESELECT")
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)

    def empty(name, loc):
        e = bpy.data.objects.new(name, None)
        e.location = loc
        bpy.context.collection.objects.link(e)
        return e

    def cube(name, loc, height):
        """A 2x2 cube stretched to *height* along Y, centred on *loc*."""
        bpy.ops.mesh.primitive_cube_add(size=2, location=loc)
        o = bpy.context.view_layer.objects.active
        o.name = name
        o.scale.y = height / 2.0
        return o

    def eval_obj(o):
        dg = bpy.context.evaluated_depsgraph_get()
        return o.evaluated_get(dg)

    def constraints_of(o, ctype):
        return [c for c in o.constraints if c.type == ctype]

    def has_scale_driver(o, index=1):
        ad = o.animation_data
        return bool(ad and any(d.data_path == "scale" and d.array_index == index for d in ad.drivers))

    # ---- build: base@origin, end@(0,10,0), 3 segments already on the line ----
    reset()
    base = empty("base", (0, 0, 0))
    end = empty("end", (0, 10, 0))
    segs = [empty(f"seg{i}", (0, i * 5, 0)) for i in range(3)]
    bundle = TelescopeRig().setup_telescope_rig(base, end, segs)
    check("setup returns a bundle for the 3 segments",
          isinstance(bundle, TelescopeRigBundle) and len(bundle.segments) == 3,
          f"n={len(bundle.segments)}")

    # ---- constraints wired: handles track, ends Child Of, interior lerps ----
    check("base has DAMPED_TRACK", bool(constraints_of(base, "DAMPED_TRACK")))
    check("end has DAMPED_TRACK", bool(constraints_of(end, "DAMPED_TRACK")))
    check("seg0 (endpoint) rides base via CHILD_OF", len(constraints_of(segs[0], "CHILD_OF")) == 1)
    check("seg2 (endpoint) rides end via CHILD_OF", len(constraints_of(segs[2], "CHILD_OF")) == 1)
    check("endpoint CHILD_OF ignores handle scale (Maya parentConstraint mirror)",
          all(not (c.use_scale_x or c.use_scale_y or c.use_scale_z)
              for c in constraints_of(segs[0], "CHILD_OF")))
    check("seg1 (interior) has 2 copy-location (lerp)",
          len(constraints_of(segs[1], "COPY_LOCATION")) == 2,
          f"n={len(constraints_of(segs[1], 'COPY_LOCATION'))}")
    check("seg1 (interior) has a damped-track", bool(constraints_of(segs[1], "DAMPED_TRACK")))

    # ---- middle segment has a scale.y driver; endpoints do not ----
    check("seg1 (middle) has scale.y driver", has_scale_driver(segs[1]))
    check("seg0 (endpoint) has NO driver", not has_scale_driver(segs[0]))
    check("seg2 (endpoint) has NO driver", not has_scale_driver(segs[2]))

    # ---- evaluated positions: base->end lerp (seg0@base, seg2@end, seg1@midpoint) ----
    bpy.context.view_layer.update()
    p0 = eval_obj(segs[0]).matrix_world.translation
    p1 = eval_obj(segs[1]).matrix_world.translation
    p2 = eval_obj(segs[2]).matrix_world.translation
    check("seg0 at base (0,0,0)", approx(p0.x, 0) and approx(p0.y, 0) and approx(p0.z, 0), f"{tuple(round(v,2) for v in p0)}")
    check("seg1 at midpoint (0,5,0)", approx(p1.x, 0) and approx(p1.y, 5) and approx(p1.z, 0), f"{tuple(round(v,2) for v in p1)}")
    check("seg2 at end (0,10,0)", approx(p2.x, 0) and approx(p2.y, 10) and approx(p2.z, 0), f"{tuple(round(v,2) for v in p2)}")

    # ---- driven scale: at rest distance(=10) the middle scale.y == 1 ----
    s_rest = eval_obj(segs[1]).scale.y
    check("middle scale.y == 1.0 at rest", approx(s_rest, 1.0), f"scale.y={s_rest:.4f}")

    # ---- extend: move end to (0,20,0) -> distance doubles -> middle scale.y == 2 ----
    end.location = (0, 20, 0)
    bpy.context.view_layer.update()
    check("middle scale.y == 2.0 when extended 2x", approx(eval_obj(segs[1]).scale.y, 2.0, 2e-3),
          f"scale.y={eval_obj(segs[1]).scale.y:.4f}")
    check("middle stays at the midpoint when extended",
          approx(eval_obj(segs[1]).matrix_world.translation.y, 10.0, 2e-3),
          f"y={eval_obj(segs[1]).matrix_world.translation.y:.4f}")

    # ---- collapse: distance halves -> middle scale.y == 0.5 ----
    end.location = (0, 5, 0)
    bpy.context.view_layer.update()
    s_collapsed = eval_obj(segs[1]).scale.y
    check("middle scale.y == 0.5 when collapsed to half", approx(s_collapsed, 0.5, 2e-3), f"scale.y={s_collapsed:.4f}")

    # ---- clamp: pushed past full collapse the scale holds at collapsed/initial ----
    # (Empties carry no geometry, so the auto collapse falls back to initial/n = 10/3.)
    expected_clamp = bundle.collapsed_distance / bundle.initial_distance
    check("auto collapsed_distance falls back to initial/n for shapeless segments",
          approx(bundle.collapsed_distance, 10.0 / 3.0, 1e-3), f"c={bundle.collapsed_distance:.4f}")
    end.location = (0, 0.5, 0)
    bpy.context.view_layer.update()
    check("middle scale CLAMPS past full collapse (Maya constant pre-infinity mirror)",
          approx(eval_obj(segs[1]).scale.y, expected_clamp, 2e-3),
          f"scale.y={eval_obj(segs[1]).scale.y:.4f} expected={expected_clamp:.4f}")

    # ---- teardown: constraints/drivers gone, locks + transforms restored ----
    end.location = (0, 10, 0)
    rig = TelescopeRig()
    check("teardown reports success", rig.teardown(bundle) is True)
    check("teardown: constraints removed", not any(o.constraints for o in (base, end, *segs)))
    check("teardown: drivers removed", not has_scale_driver(segs[1]))
    check("teardown: locks restored", tuple(segs[1].lock_location) == (False, False, False))
    check("teardown: scale restored", approx(segs[1].scale.y, 1.0), f"scale.y={segs[1].scale.y:.4f}")
    check("teardown: objects survive", all(o.name in bpy.data.objects for o in (base, end, *segs)))
    check("teardown: stamp removed", "telescope_rig_data" not in base)
    check("teardown: nothing left to do on a second call", rig.teardown(bundle) is not None)

    # ---- build pose is PRESERVED (Maya's maintainOffset mirror) ----
    reset()
    base = empty("base", (0, 0, 0))
    end = empty("end", (0, 10, 0))
    # deliberately off the ideal lerp line and off-axis
    offs = [(1, 0, 0), (2, 6, 1), (0, 10, 3)]
    segs = [empty(f"oseg{i}", offs[i]) for i in range(3)]
    TelescopeRig().setup_telescope_rig(base, end, segs)
    bpy.context.view_layer.update()
    for i, o in enumerate(segs):
        got = eval_obj(o).matrix_world.translation
        check(f"build pose preserved for oseg{i}",
              all(approx(got[k], offs[i][k], 2e-3) for k in range(3)),
              f"{tuple(round(v,2) for v in got)} vs {offs[i]}")
    # ...and the offset rides the handles: half the base delta reaches the middle segment.
    base.location = (0, -4, 0)
    bpy.context.view_layer.update()
    check("interior offset tracks the lerp (half of a base move)",
          approx(eval_obj(segs[1]).matrix_world.translation.y, 6 - 2, 2e-3),
          f"y={eval_obj(segs[1]).matrix_world.translation.y:.4f}")

    # ---- two segments: a sliding strut, no driver, no interior ----
    reset()
    base = empty("base", (0, 0, 0))
    end = empty("end", (0, 10, 0))
    outer = empty("outer", (0, 0, 0))
    inner = empty("inner", (0, 4, 0))
    strut = TelescopeRig().setup_telescope_rig(base, end, [outer, inner])
    check("2 segments: no drivers recorded", strut.drivers == [], f"{strut.drivers}")
    check("2 segments: neither half is driven",
          not has_scale_driver(outer) and not has_scale_driver(inner))
    check("2 segments: both halves ride a handle",
          len(constraints_of(outer, "CHILD_OF")) == 1 and len(constraints_of(inner, "CHILD_OF")) == 1)
    end.location = (0, 18, 0)
    bpy.context.view_layer.update()
    check("2 segments: inner slides with the end handle",
          approx(eval_obj(inner).matrix_world.translation.y, 12.0, 2e-3),
          f"y={eval_obj(inner).matrix_world.translation.y:.4f}")
    check("2 segments: outer stays on the base handle",
          approx(eval_obj(outer).matrix_world.translation.y, 0.0, 2e-3),
          f"y={eval_obj(outer).matrix_world.translation.y:.4f}")
    # An out-of-range collapsed distance is IGNORED, not refused.
    reset()
    TelescopeRig().setup_telescope_rig(
        empty("b", (0, 0, 0)), empty("e", (0, 10, 0)),
        [empty("o", (0, 0, 0)), empty("i", (0, 4, 0))], collapsed_distance=999.0,
    )
    check("2 segments: out-of-range collapsed_distance is ignored", True)

    # ---- auto handles: segments alone are a valid build ----
    reset()
    segs = [cube(f"tube{i}", (0, 2 + i * 3, 0), 4.0) for i in range(3)]
    bundle = TelescopeRig().setup_telescope_rig(segments=segs)
    check("auto handles: two created", len(bundle.created_locators) == 2, f"{bundle.created_locators}")
    auto_base = bpy.data.objects.get(bundle.base_locator)
    auto_end = bpy.data.objects.get(bundle.end_locator)
    check("auto handles: exist in the scene", auto_base is not None and auto_end is not None)
    check("auto base at the bottom of tube0 (y=0)",
          approx(auto_base.matrix_world.translation.y, 0.0, 2e-3),
          f"y={auto_base.matrix_world.translation.y:.4f}")
    check("auto end at the top of tube2 (y=10)",
          approx(auto_end.matrix_world.translation.y, 10.0, 2e-3),
          f"y={auto_end.matrix_world.translation.y:.4f}")
    check("auto collapsed_distance = longest tube (4.0)",
          approx(bundle.collapsed_distance, 4.0, 2e-3), f"c={bundle.collapsed_distance:.4f}")
    bpy.context.view_layer.update()
    for i, o in enumerate(segs):
        check(f"auto handles: tube{i} build pose preserved",
              approx(eval_obj(o).matrix_world.translation.y, 2 + i * 3, 2e-3),
              f"y={eval_obj(o).matrix_world.translation.y:.4f}")

    # ---- scene stamp: recover the bundle without the building instance ----
    found = TelescopeRig.find_bundles([segs[1]])
    check("find_bundles recovers the rig from a segment", len(found) == 1, f"n={len(found)}")
    check("recovered bundle round-trips", found and found[0] == bundle)
    check("find_bundles ignores unrelated objects", TelescopeRig.find_bundles([]) == [])
    TelescopeRig().teardown(found[0])
    check("teardown from a recovered bundle deletes the auto handles",
          bundle.base_locator not in bpy.data.objects and bundle.end_locator not in bpy.data.objects)
    check("teardown from a recovered bundle keeps the segments",
          all(o.name in bpy.data.objects for o in segs))
    check("scene_bundles empty after teardown", TelescopeRig.scene_bundles() == [])

    # ---- re-rigging the same objects is refused, not stacked ----
    # Blender has no "channel already connected" pre-flight like Maya's: constraints and
    # drivers just accumulate, so without the stamp guard a second Build silently double-rigs.
    reset()
    base = empty("base", (0, 0, 0))
    end = empty("end", (0, 10, 0))
    segs = [empty(f"rseg{i}", (0, i * 5, 0)) for i in range(3)]
    first = TelescopeRig().setup_telescope_rig(base, end, segs)
    constraint_counts = [len(o.constraints) for o in (base, end, *segs)]
    try:
        TelescopeRig().setup_telescope_rig(base, end, segs)
        check("refuses to re-rig already-rigged objects", False)
    except ValueError as e:
        check("refuses to re-rig already-rigged objects", "already carry" in str(e), str(e))
    check("refused rebuild stacked no constraints",
          [len(o.constraints) for o in (base, end, *segs)] == constraint_counts,
          f"{[len(o.constraints) for o in (base, end, *segs)]} vs {constraint_counts}")
    try:  # one overlapping segment is enough
        TelescopeRig().setup_telescope_rig(
            empty("b2", (0, 0, 0)), empty("e2", (0, 10, 0)), [segs[0], empty("fresh", (0, 5, 0))]
        )
        check("refuses a build overlapping one rigged segment", False)
    except ValueError:
        check("refuses a build overlapping one rigged segment", True)
    TelescopeRig().teardown(first)
    try:
        TelescopeRig().setup_telescope_rig(base, end, segs)
        check("the same objects rig again once removed", True)
    except ValueError as e:
        check("the same objects rig again once removed", False, str(e))

    # ---- aim_axis="x": chain along X drives scale.x and locks y/z ----
    reset()
    base = empty("base", (0, 0, 0))
    end = empty("end", (10, 0, 0))
    segs = [empty(f"xseg{i}", (i * 5, 0, 0)) for i in range(3)]
    TelescopeRig().setup_telescope_rig(base, end, segs, aim_axis="x")
    check("aim_axis=x -> middle drives scale.x", has_scale_driver(segs[1], index=0))
    check("aim_axis=x -> base tracks TRACK_X",
          any(c.type == "DAMPED_TRACK" and c.track_axis == "TRACK_X" for c in base.constraints))
    check("aim_axis=x -> off-axis scale locked, x free",
          tuple(segs[1].lock_scale) == (False, True, True), str(tuple(segs[1].lock_scale)))
    end.location = (5, 0, 0)
    bpy.context.view_layer.update()
    check("aim_axis=x -> scale.x collapses to 0.5",
          approx(eval_obj(segs[1]).scale.x, 0.5, 2e-3), f"scale.x={eval_obj(segs[1]).scale.x:.4f}")
    try:
        TelescopeRig().setup_telescope_rig(base, end, segs, aim_axis="w")
        check("rejects bad aim_axis", False)
    except ValueError:
        check("rejects bad aim_axis", True)

    # ---- guards ----
    reset()
    try:
        TelescopeRig().setup_telescope_rig(empty("b", (0, 0, 0)), empty("e", (0, 1, 0)), [empty("only", (0, 0, 0))])
        check("rejects <2 segments", False)
    except ValueError:
        check("rejects <2 segments", True)
    reset()
    try:
        s = empty("s", (0, 0, 0))
        TelescopeRig().setup_telescope_rig(s, empty("e", (0, 1, 0)), [s, empty("s2", (0, 1, 0))])
        check("rejects a handle that is also a segment", False)
    except ValueError:
        check("rejects a handle that is also a segment", True)
    reset()
    try:
        s = empty("s", (0, 0, 0))
        TelescopeRig().setup_telescope_rig(empty("b", (0, 0, 0)), empty("e", (0, 1, 0)), [s, s])
        check("rejects duplicate segments", False)
    except ValueError:
        check("rejects duplicate segments", True)

    # ---- Slots.build_rig / remove_rig via lightweight stubs ----
    from blendertk.rig_utils.telescope_rig import TelescopeRigSlots

    class _Sig:
        def connect(self, *a, **k):
            pass

    class _UI:
        def __init__(self, collapsed=0.0, axis=1):
            self.btn_build = type("B", (), {"clicked": _Sig()})()
            self.btn_remove = type("B", (), {"clicked": _Sig()})()
            self.spin_collapsed = type("S", (), {"value": staticmethod(lambda: collapsed)})()
            self.cmb_axis = type("C", (), {"currentIndex": staticmethod(lambda: axis)})()
            self.txt003 = type("T", (), {"append": staticmethod(lambda *a, **k: None)})()

    class _SB:
        def __init__(self, ui):
            self.loaded_ui = type("L", (), {"telescope_rig": ui})()
            self.registered_widgets = type("R", (), {})()   # no TextEditLogHandler -> guarded skip
            self.messages = []

        def message_box(self, msg, *a, **k):
            self.messages.append(msg)

    # Empties are handles, everything else is a segment; segments order by distance from active.
    reset()
    handles = [empty("base", (0, 0, 0)), empty("end", (0, 10, 0))]
    tubes = [cube(f"t{i}", (0, 2 + i * 3, 0), 4.0) for i in range(3)]
    for o in handles + tubes:
        o.select_set(True)
    bpy.context.view_layer.objects.active = handles[0]
    sb = _SB(_UI())
    slot = TelescopeRigSlots(sb)
    slot.build_rig()
    check("build_rig: no error message_box", not sb.messages, f"msgs={sb.messages}")
    check("build_rig: handles taken from the Empties, not created",
          slot.bundle is not None and slot.bundle.created_locators == [],
          f"{slot.bundle and slot.bundle.created_locators}")
    check("build_rig: base/end assigned from the Empties",
          slot.bundle and {slot.bundle.base_locator, slot.bundle.end_locator} == {"base", "end"},
          f"{slot.bundle and (slot.bundle.base_locator, slot.bundle.end_locator)}")
    check("build_rig: 3 segments rigged", slot.bundle and len(slot.bundle.segments) == 3)

    # remove_rig from a selected segment tears the rig down
    bpy.ops.object.select_all(action="DESELECT")
    tubes[1].select_set(True)
    slot.remove_rig()
    check("remove_rig: no error message_box", not sb.messages, f"msgs={sb.messages}")
    check("remove_rig: constraints gone", not any(o.constraints for o in handles + tubes))
    check("remove_rig: user handles kept", all(o.name in bpy.data.objects for o in handles))

    # geometry only (2 tubes) -> both handles auto-created
    reset()
    tubes = [cube(f"s{i}", (0, 2 + i * 3, 0), 4.0) for i in range(2)]
    for o in tubes:
        o.select_set(True)
    bpy.context.view_layer.objects.active = tubes[0]
    sb2 = _SB(_UI())
    slot2 = TelescopeRigSlots(sb2)
    slot2.build_rig()
    check("build_rig: 2 tubes alone build a strut", not sb2.messages, f"msgs={sb2.messages}")
    check("build_rig: both handles auto-created",
          slot2.bundle is not None and len(slot2.bundle.created_locators) == 2,
          f"{slot2.bundle and slot2.bundle.created_locators}")

    # remove_rig with nothing selected falls back to this session's last build
    bpy.ops.object.select_all(action="DESELECT")
    slot2.remove_rig()
    check("remove_rig: falls back to the last build", not sb2.messages and slot2.bundle is None,
          f"msgs={sb2.messages}")

    # build_rig rejects an under-sized selection
    reset()
    sb3 = _SB(_UI())
    TelescopeRigSlots(sb3).build_rig()   # nothing selected
    check("build_rig: guards empty selection", len(sb3.messages) == 1, f"msgs={sb3.messages}")

    reset()
    lone = cube("lone", (0, 0, 0), 4.0)
    lone.select_set(True)
    bpy.context.view_layer.objects.active = lone
    sb4 = _SB(_UI())
    TelescopeRigSlots(sb4).build_rig()
    check("build_rig: guards a single segment", len(sb4.messages) == 1, f"msgs={sb4.messages}")

    # remove_rig with a selection that carries no rig warns
    reset()
    stray = cube("stray", (0, 0, 0), 4.0)
    stray.select_set(True)
    sb5 = _SB(_UI())
    TelescopeRigSlots(sb5).remove_rig()
    check("remove_rig: guards a rig-less selection", len(sb5.messages) == 1, f"msgs={sb5.messages}")

    # ---- Verbose (INFO) report path ----
    # Every other build here runs at the default WARNING level, where the run
    # banner / build group / summary box are gated OFF — so a typo in one of
    # them (e.g. reading a bundle field this DCC's bundle does not have) would
    # never execute under test while breaking every real panel run, which logs
    # at INFO. Build and tear down once at INFO so the report actually renders.
    reset()
    base = empty("vbase", (0, 0, 0))
    end = empty("vend", (0, 12, 0))
    segs = [cube(f"v{i}", (0, 2 + i * 4, 0), 4.0) for i in range(3)]
    try:
        rig = TelescopeRig(log_level="INFO")
        vbundle = rig.setup_telescope_rig(base, end, segs)
        check("verbose build: report path renders without raising", vbundle is not None)
        check("verbose build: teardown report renders without raising", rig.teardown(vbundle))
    except Exception as e:
        check("verbose build: report path renders without raising", False, repr(e))

    # Auto-created handles at INFO exercise the _create_handle log_link line.
    reset()
    segs = [cube(f"a{i}", (0, 2 + i * 4, 0), 4.0) for i in range(3)]
    try:
        auto = TelescopeRig(log_level="INFO").setup_telescope_rig(segments=segs)
        check("verbose build: auto-handle report renders", len(auto.created_locators) == 2)
    except Exception as e:
        check("verbose build: auto-handle report renders", False, repr(e))

except Exception:
    lines.append("FAIL harness | " + traceback.format_exc().replace("\n", " | "))

failed = sum(1 for ln in lines if ln.startswith("FAIL"))
print("\n".join(lines))
result = "PASS" if not failed and lines else "FAIL"
print(f"===RESULT: {result}=== ({len(lines) - failed}/{len(lines)})")
sys.exit(1 if failed else 0)
