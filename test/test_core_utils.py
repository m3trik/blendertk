"""blendertk.core_utils._core_utils headless test — the window-independent context readers
(``selected_objects`` / ``active_object`` / ``get_areas``).

Regression guard for the "tentacle Blender operations report *nothing selected* while an object
IS selected" bug: the slots run from tentacle's Qt event-pump timer, a context where
``bpy.context.window`` is None, and the screen-context members ``bpy.context.selected_objects`` /
``active_object`` are empty there. ``btk.selected_objects()`` / ``btk.active_object()`` must read
the window-independent ``view_layer.objects`` instead. ``bpy.context.temp_override(window=None)``
reproduces the exact failing condition headlessly.

Run: blender --background --factory-startup --python blendertk/test/test_core_utils.py
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

try:
    import bpy
    import blendertk as btk

    def reset():
        bpy.ops.object.select_all(action="DESELECT")
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)

    # --- one cube, selected + active -------------------------------------------------------
    reset()
    bpy.ops.mesh.primitive_cube_add()
    cube = bpy.context.view_layer.objects.active
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    cube.select_set(True)
    bpy.context.view_layer.objects.active = cube

    # 1. normal reads (window present)
    check("selected_objects() -> [cube]", btk.selected_objects() == [cube])
    check("active_object() -> cube", btk.active_object() is cube)
    check("selected_objects() matches view_layer.objects.selected",
          btk.selected_objects() == [o for o in bpy.context.view_layer.objects.selected])

    # 2. THE REGRESSION: from a window-less context (the Qt event-pump timer condition), the
    #    screen-context members go empty while the view-layer readers stay correct.
    with bpy.context.temp_override(window=None):
        raw_sel = list(getattr(bpy.context, "selected_objects", None) or [])
        raw_active = getattr(bpy.context, "active_object", None)
        check("precondition: temp_override(window=None) empties bpy.context.selected_objects",
              raw_sel == [], f"raw={ [o.name for o in raw_sel] }")
        check("precondition: temp_override(window=None) nulls bpy.context.active_object",
              raw_active is None, f"raw={raw_active!r}")
        check("selected_objects() survives window=None -> [cube]",
              btk.selected_objects() == [cube], f"got={ [o.name for o in btk.selected_objects()] }")
        check("active_object() survives window=None -> cube",
              btk.active_object() is cube, f"got={btk.active_object()!r}")

    # 3. empty selection -> empty list / None active (no crash)
    bpy.ops.object.select_all(action="DESELECT")
    bpy.context.view_layer.objects.active = None
    check("selected_objects() empty -> []", btk.selected_objects() == [])
    check("active_object() no active -> None", btk.active_object() is None)

    # 4. get_areas — the same window-independence contract for area iteration: a
    #    ``context.screen.areas`` loop crashes with AttributeError when window is None (the
    #    display/selection viewport toggles' bug); get_areas resolves through the window
    #    manager instead, so its result is IDENTICAL with and without a context window.
    #    (Even --background keeps one window with the default screen, so the list is
    #    usually non-empty here — the contract is type-filtered + window-independent,
    #    not empty.)
    baseline = btk.get_areas("VIEW_3D")
    check("get_areas returns only VIEW_3D areas",
          all(a.type == "VIEW_3D" for a in baseline), f"got={ [a.type for a in baseline] }")
    with bpy.context.temp_override(window=None):
        check("precondition: window=None nulls bpy.context.screen",
              getattr(bpy.context, "screen", None) is None)
        check("get_areas survives window=None (identical result)",
              btk.get_areas("VIEW_3D") == baseline)

    # 5. multi-select is order-independent set membership
    reset()
    bpy.ops.mesh.primitive_cube_add(); a = bpy.context.view_layer.objects.active
    bpy.ops.mesh.primitive_cube_add(location=(3, 0, 0)); b = bpy.context.view_layer.objects.active
    a.select_set(True); b.select_set(True)
    with bpy.context.temp_override(window=None):
        check("selected_objects() window=None sees both of a multi-selection",
              set(btk.selected_objects()) == {a, b},
              f"got={ sorted(o.name for o in btk.selected_objects()) }")

except Exception as e:
    lines.append(f"FAIL setup: {e!r}")
    lines.append(traceback.format_exc())

ok = all(l.startswith("OK") for l in lines)
print("\n===CORE-UTILS-SELECTION===")
print("\n".join(lines))
print(f"===RESULT: {'PASS' if ok else 'FAIL'}===")
