"""QtDock geometry tests — the native resize strip above an embedded child.

A child glued flush over the docked area's WINDOW region covers the lower half of
Blender's area-edge grab band (edge ± BORDERPADDING): the cursor flips to a resize
arrow over the remaining sliver of Blender-owned border, but the press lands on the
embedded text widget and starts a text selection instead of the drag the cursor
promised. ``QtDock`` therefore insets the child's TOP by the native grab tolerance
whenever the region is flush with the area's top edge, keeping every pixel that shows
the resize cursor Blender-owned.

The inset math is pure (bpy objects are only read for ints), so this suite runs under
the workspace ``.venv``::

    python blendertk/test/test_qt_dock.py

and under the Blender harness — where it additionally pins the two live assumptions
the fix stands on: ``_native_edge_pad`` resolves from real preferences, and a
header-less docked strip's WINDOW region really is flush with its area top::

    blender --background --factory-startup --python blendertk/test/test_qt_dock.py

The embed itself (GHOST hwnd, WS_CHILD) needs a GUI window + QApplication and is out
of scope here.
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

lines = []


def check(label, ok, detail=""):
    lines.append(f"{'OK' if ok else 'FAIL'} {label}" + (f" — {detail}" if not ok and detail else ""))


class _Rect:
    """Stand-in for a bpy Area/Region — only the ints the inset math reads."""

    def __init__(self, y, height):
        self.y = y
        self.height = height


class _DeadWrapper:
    """A bpy wrapper whose data block is gone: every attribute read raises."""

    def __getattr__(self, name):
        raise ReferenceError("StructRNA of type Region has been removed")


try:
    from blendertk.ui_utils.qt_dock import QtDock

    dock = QtDock()
    dock._edge_pad = 3
    dock._area = _Rect(y=0, height=200)

    # -- flush region (header hidden): top inset by the native grab tolerance ----
    flush_region = _Rect(y=0, height=200)
    out = dock._expose_resize_edge((10, 800, 500, 200), flush_region)
    check("flush top edge: child top inset by the grab tolerance",
          out == (10, 803, 500, 197), repr(out))

    # -- region below the area top (a header row above): child stays flush -------
    header_region = _Rect(y=0, height=174)
    out = dock._expose_resize_edge((10, 826, 500, 174), header_region)
    check("non-flush region (header above): rect unchanged",
          out == (10, 826, 500, 174), repr(out))

    # -- pad 0 (not docked yet / disabled): no-op ---------------------------------
    dock._edge_pad = 0
    out = dock._expose_resize_edge((10, 800, 500, 200), flush_region)
    check("zero pad: rect unchanged", out == (10, 800, 500, 200), repr(out))
    dock._edge_pad = 3

    # -- a strip too short to give pixels away is left alone ---------------------
    out = dock._expose_resize_edge((10, 997, 500, 3), _Rect(y=0, height=3))
    check("tiny strip never collapses", out == (10, 997, 500, 3), repr(out))

    # -- dead bpy wrapper mid-read: fail safe to the un-inset rect ----------------
    out = dock._expose_resize_edge((10, 800, 500, 200), _DeadWrapper())
    check("dead region wrapper: rect unchanged", out == (10, 800, 500, 200), repr(out))
    dock._area = _DeadWrapper()
    out = dock._expose_resize_edge((10, 800, 500, 200), flush_region)
    check("dead area wrapper: rect unchanged", out == (10, 800, 500, 200), repr(out))

    # -- the pad itself -----------------------------------------------------------
    try:
        import bpy  # noqa: F401 — which harness are we under?
    except ImportError:
        bpy = None
    pad = QtDock._native_edge_pad()
    if bpy is None:
        check("venv: pad falls back to the 1x-scale native value (3)", pad == 3, repr(pad))
    else:
        check("blender: pad resolves from live preferences (int >= 2)",
              isinstance(pad, int) and pad >= 2, repr(pad))

        # -- the live assumption the flush test stands on: a docked strip's WINDOW
        # region tops out AT the area top (within a px). Headless lays the region
        # flush regardless of the header (no drawing = no header row reserved), so
        # this pins exactly what the fix reads — region-vs-area tops on real bpy
        # data. No teardown: ``screen.area_close`` blows the stack under
        # ``--background`` on 5.1, and the harness gives every suite a fresh
        # instance anyway.
        import blendertk as btk

        window = btk.main_window()
        area = btk.dock_editor("Info Log", edge_size=150, window=window)
        check("harness: Info Log strip docked", area is not None)
        if area is not None:
            region = next(r for r in area.regions if r.type == "WINDOW")
            gap = (area.y + area.height) - (region.y + region.height)
            check("docked strip: WINDOW region flush with the area top (gap <= 1px)",
                  0 <= gap <= 1, f"gap={gap}px area={area.y}+{area.height} "
                                 f"region={region.y}+{region.height}")

            # and the inset applies against the REAL region/area pair
            live = QtDock()
            live._edge_pad = pad
            live._area = area
            rect = (0, 0, int(region.width), int(region.height))
            out = live._expose_resize_edge(rect, region)
            check("live region: child top inset by the pad",
                  out == (0, pad, int(region.width), int(region.height) - pad),
                  f"{rect} -> {out} (pad={pad})")

except Exception as e:
    traceback.print_exc()
    check("suite raised", False, repr(e))

passed = sum(1 for line in lines if line.startswith("OK"))
for line in lines:
    print(line)
result = "PASS" if lines and all(line.startswith("OK") for line in lines) else "FAIL"
print(f"===RESULT: {result}=== ({passed}/{len(lines)})")
