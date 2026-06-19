"""blendertk bridge (btk.Bridge + BridgeSlots/Preview) headless test.
Run: blender --background --factory-startup --python blendertk/test/test_bridge.py

Covers the engine (bridge two open loops via bmesh.ops.bridge_loops in the Object-Mode/Preview
entry path, the divisions→subdivide rows, empty/insufficient-selection guards) and the live-Preview
snapshot/rollback contract (refresh re-bridges the SAME captured loops from a pristine mesh instead
of stacking; disable rolls back), mirroring test_bevel.py.
"""
import sys, os, traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for p in (REPO, os.path.join(MONO, "pythontk")):
    if p not in sys.path:
        sys.path.insert(0, p)

lines = []
def check(name, cond, detail=""):
    lines.append(f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}")


# --- minimal duck-typed Qt stand-ins for the Preview's checkbox / commit button -------------
class _Signal:
    def __init__(self):
        self._slots = []
    def connect(self, fn):
        self._slots.append(fn)
    def emit(self, *a):
        for fn in list(self._slots):
            fn(*a)


class _CheckBox:
    def __init__(self):
        self.toggled = _Signal()
        self._checked = False
    def setChecked(self, state):
        self._checked = bool(state)
    def isChecked(self):
        return self._checked
    def toggle(self, state):
        self._checked = bool(state)
        self.toggled.emit(self._checked)


class _Button:
    def __init__(self):
        self.clicked = _Signal()
        self._enabled = False
    def setEnabled(self, v):
        self._enabled = bool(v)
    def isEnabled(self):
        return self._enabled


try:
    import bpy
    import bmesh
    import blendertk as btk
    from blendertk.edit_utils.bridge import Bridge
    from blendertk.core_utils.preview import Preview

    NSIDES = 8

    def reset():
        if (
            bpy.context.view_layer.objects.active
            and bpy.context.view_layer.objects.active.mode != "OBJECT"
        ):
            bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.select_all(action="DESELECT")
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)

    def two_open_loops(select=True):
        """One mesh with two separate open edge loops (two N-gon circles, no faces) at z=0 and
        z=2 — bridging them makes a tube wall of N quads. With ``select`` the edges are flushed
        to mesh data and the object left in OBJECT mode (the state the Preview captures)."""
        bpy.ops.mesh.primitive_circle_add(vertices=NSIDES, radius=1, fill_type="NOTHING",
                                          location=(0, 0, 0))
        c1 = bpy.context.active_object
        bpy.ops.mesh.primitive_circle_add(vertices=NSIDES, radius=1, fill_type="NOTHING",
                                          location=(0, 0, 2))
        c2 = bpy.context.active_object
        bpy.ops.object.select_all(action="DESELECT")
        c1.select_set(True)
        c2.select_set(True)
        bpy.context.view_layer.objects.active = c2
        bpy.ops.object.join()
        obj = c2
        bpy.ops.object.mode_set(mode="EDIT")
        bm = bmesh.from_edit_mesh(obj.data)
        for e in bm.edges:
            e.select = select
        bm.select_flush(select)
        bmesh.update_edit_mesh(obj.data)
        bpy.ops.object.mode_set(mode="OBJECT")
        return obj

    # ---- engine: bridge two open loops -> N quad faces (Object-Mode/Preview entry) ----------
    reset()
    obj = two_open_loops()
    base_faces_before = len(obj.data.polygons)
    n = Bridge.bridge([obj])
    check("two open loops start with no faces", base_faces_before == 0, f"faces={base_faces_before}")
    check("bridge returns N new faces", n == NSIDES, f"faces={n}")
    check("bridge builds the tube wall (N polys)", len(obj.data.polygons) == NSIDES,
          f"polys={len(obj.data.polygons)}")
    check("object-mode path restores OBJECT mode", obj.mode == "OBJECT", obj.mode)

    # ---- engine: divisions add rows (divisions=1 -> 2 rows = 2N faces) ----------------------
    reset()
    obj = two_open_loops()
    Bridge.bridge([obj], divisions=1)
    check("divisions=1 doubles the rows (2N faces)", len(obj.data.polygons) == 2 * NSIDES,
          f"polys={len(obj.data.polygons)}")

    # ---- engine: offset still bridges (vertex-pairing shift; topology count unchanged) ------
    reset()
    obj = two_open_loops()
    Bridge.bridge([obj], offset=2)
    check("offset still bridges N faces", len(obj.data.polygons) == NSIDES,
          f"polys={len(obj.data.polygons)}")

    # ---- engine: nothing selected raises a clear error --------------------------------------
    reset()
    obj = two_open_loops(select=False)
    raised = False
    try:
        Bridge.bridge([obj])
    except RuntimeError:
        raised = True
    check("empty selection raises RuntimeError", raised)

    # ---- engine: non-mesh selection raises --------------------------------------------------
    reset()
    empty = bpy.data.objects.new("Empty", None)
    bpy.context.collection.objects.link(empty)
    raised = False
    try:
        Bridge.bridge([empty])
    except RuntimeError:
        raised = True
    check("non-mesh selection raises RuntimeError", raised)

    # ---- Preview: enable bridges; refresh re-bridges from pristine (NO stacking) ------------
    class _Op:
        """Stand-in BridgeSlots: Preview calls perform_operation(objects)."""
        def __init__(self):
            self.divisions = 0
        def perform_operation(self, objects):
            Bridge.bridge(objects, divisions=self.divisions)

    reset()
    obj = two_open_loops()
    op = _Op()
    chk, btn = _CheckBox(), _Button()
    preview = Preview(op, chk, btn, message_func=lambda *a: None, undo_message="Bridge")

    chk.toggle(True)  # enable -> snapshot + run
    check("preview enable bridges the captured loops", len(obj.data.polygons) == NSIDES,
          f"polys={len(obj.data.polygons)}")

    preview.refresh()  # same params -> rollback + re-run -> still a single bridge
    check("preview refresh does NOT stack (still N faces)", len(obj.data.polygons) == NSIDES,
          f"polys={len(obj.data.polygons)}")

    op.divisions = 1  # change a value -> refresh re-bridges from pristine
    preview.refresh()
    check("preview refresh after value change re-bridges (2N faces)",
          len(obj.data.polygons) == 2 * NSIDES, f"polys={len(obj.data.polygons)}")

    btn.clicked.emit()  # Create
    check("commit keeps the bridged result", len(obj.data.polygons) == 2 * NSIDES,
          f"polys={len(obj.data.polygons)}")
    check("commit clears the preview-enabled state", preview.is_enabled is False)

    # ---- Preview: un-check rolls back to the pristine (faceless) mesh -----------------------
    reset()
    obj = two_open_loops()
    op = _Op()
    chk, btn = _CheckBox(), _Button()
    preview = Preview(op, chk, btn, message_func=lambda *a: None, undo_message="Bridge")
    chk.toggle(True)
    check("re-enabled preview bridges", len(obj.data.polygons) == NSIDES)
    chk.toggle(False)  # disable -> rollback
    obj = bpy.data.objects.get(obj.name) or obj
    check("disable rolls back to 0 faces", len(obj.data.polygons) == 0,
          f"polys={len(obj.data.polygons)}")

except Exception:
    traceback.print_exc()
    lines.append("FAIL unhandled exception")

print("\n".join(lines))
ok = all(l.startswith("OK") for l in lines) and lines
print(f"===RESULT: {'PASS' if ok else 'FAIL'}=== ({sum(1 for l in lines if l.startswith('OK'))}/{len(lines)})")
