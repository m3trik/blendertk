"""Manual GUI harness for ``OutlinerTint`` (``display_utils/outliner_tint.py``).

**GUI-only** — the overlay is a ``SpaceOutliner`` draw handler, and draw handlers never fire
under ``--background``, so ``test_color_id.py`` can only cover the stored colours and the
enable/disable contract. This proves the part that needs a real draw loop: offset calibration
against the live process, the tree walk resolving objects, and the repaint landing on the right
rows. The non-``test_``/non-``*_slot_check`` name keeps it out of the headless runner.

Run against a *fresh* Blender (never an existing session)::

    blender --factory-startup --python blendertk/test/outliner_tint_gui_check.py

Writes ``temp_tests/outliner_tint_shot.png`` for eyeball confirmation and auto-quits.
"""
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for p in (REPO, os.path.join(MONO, "pythontk")):
    if p not in sys.path:
        sys.path.insert(0, p)

SHOT = os.path.join(HERE, "temp_tests", "outliner_tint_shot.png")
lines = []


def check(name, cond, detail=""):
    lines.append(f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}")


def report():
    import bpy

    passed = sum(1 for line in lines if line.startswith("OK"))
    for line in lines:
        print(line)
    result = "PASS" if all(line.startswith("OK") for line in lines) else "FAIL"
    print(f"===RESULT: {result}=== ({passed}/{len(lines)})")
    with bpy.context.temp_override(window=bpy.context.window_manager.windows[0]):
        bpy.ops.wm.quit_blender()


def run():
    import bpy
    from blendertk.display_utils.outliner_tint import OutlinerTint

    try:
        swatches = {
            "TINT_RED": (0.90, 0.15, 0.15),
            "TINT_GREEN": (0.15, 0.90, 0.25),
            "TINT_BLUE": (0.30, 0.45, 1.00),
        }
        for name, col in swatches.items():
            ob = bpy.data.objects.new(name, None)
            bpy.context.scene.collection.objects.link(ob)
            OutlinerTint.set_color([ob], col)

        # select one and make another active, so the mask must use the row highlight colours
        red = bpy.data.objects["TINT_RED"]
        blue = bpy.data.objects["TINT_BLUE"]
        red.select_set(True)  # selected, not active -> selected_highlight mask
        green = bpy.data.objects["TINT_GREEN"]
        green.select_set(True)
        bpy.context.view_layer.objects.active = green  # selected + active -> active mask
        blue.select_set(False)  # ACTIVE-looking but unselected: must keep the plain back mask

        check("platform supported", OutlinerTint.is_supported(), sys.platform)
        check("applying a colour auto-enabled the overlay", OutlinerTint.is_enabled())
        check("stored colours read back",
              all(OutlinerTint.get_color(bpy.data.objects[n]) is not None for n in swatches))

        outliner = next(
            (a for w in bpy.context.window_manager.windows
             for a in w.screen.areas if a.type == "OUTLINER"), None)
        check("an outliner area exists", outliner is not None)
        if outliner:
            outliner.tag_redraw()

        def verify():
            # status is only decided once the handler has actually drawn
            status = OutlinerTint.status()
            check("overlay calibrated against the live process (status 'ok')",
                  status == "ok", status)
            check("overlay still enabled after drawing (did not stand down)",
                  OutlinerTint.is_enabled(), status)

            # the walk must resolve the planted objects at real row positions
            space = outliner.spaces.active
            head_ok = False
            rows = []
            try:
                from blendertk.display_utils import outliner_tint as ot

                head = ot._qword(space.as_pointer() + OutlinerTint._LAYOUT["tree_head"])
                head_ok = bool(head)
                rows = OutlinerTint._walk(head) if head else []
            except Exception:
                lines.append("FAIL walk raised | " + traceback.format_exc())
            check("tree head resolves", head_ok)
            names = {r.object_name for r in rows if r.object_name}
            check("walk resolves every planted object", set(swatches) <= names,
                  str(sorted(n for n in names if n.startswith("TINT"))))
            ladder = [r.ys for r in rows if r.object_name in swatches and r.ys is not None]
            check("planted rows sit on distinct positions",
                  len(set(ladder)) == len(swatches), str(ladder))

            try:
                bpy.ops.screen.screenshot(filepath=SHOT)
                check("screenshot written", os.path.exists(SHOT), SHOT)
            except Exception as e:
                check("screenshot written", False, repr(e))

            # clearing removes the stamp; the overlay tolerates having nothing to paint
            OutlinerTint.clear([bpy.data.objects[n] for n in swatches])
            check("clear removes every stamp", not OutlinerTint.tinted_objects())
            outliner.tag_redraw()

            def teardown():
                check("overlay survives an empty scene of tints",
                      OutlinerTint.is_enabled(), OutlinerTint.status())

                # A failure raised inside the draw callback must disable the overlay WITHOUT
                # removing the handler mid-iteration (deferred to a timer) and without spamming.
                for name, col in swatches.items():
                    OutlinerTint.set_color([bpy.data.objects[name]], col)
                broken = dict(OutlinerTint._LAYOUT)
                OutlinerTint._LAYOUT["tree_head"] = 0x7FFFFF0  # nonsense offset
                OutlinerTint._state = "unknown"  # force a recalibration
                outliner.tag_redraw()

                def after_failure():
                    check("bad layout stands the overlay down instead of crashing",
                          not OutlinerTint.is_enabled(), OutlinerTint.status())
                    check("stand-down records why",
                          OutlinerTint.status() not in ("ok", "unknown"),
                          OutlinerTint.status())
                    check("stored colours survive the stand-down",
                          all(OutlinerTint.get_color(bpy.data.objects[n]) is not None
                              for n in swatches))
                    OutlinerTint._LAYOUT.update(broken)
                    OutlinerTint._state = "unknown"
                    OutlinerTint.disable()
                    check("disable removes the handler", not OutlinerTint.is_enabled())
                    report()
                    return None

                bpy.app.timers.register(after_failure, first_interval=0.8)
                return None

            bpy.app.timers.register(teardown, first_interval=0.8)
            return None

        bpy.app.timers.register(verify, first_interval=1.5)
    except Exception:
        lines.append("FAIL harness raised | " + traceback.format_exc())
        report()
    return None


import bpy  # noqa: E402

bpy.app.timers.register(run, first_interval=2.0)
