"""blendertk bevel (btk.Bevel + BevelSlots/Preview) headless test.
Run: blender --background --factory-startup --python blendertk/test/test_bevel.py

Covers the engine (edge-component bevel in both the Edit-Mode and Object-Mode/Preview entry
paths, empty-selection guard) and the live-Preview contract that matters most for an in-place
mesh op: snapshot/rollback so repeated refreshes re-bevel the SAME captured edges from a pristine
mesh instead of stacking (the historyless-mesh hazard Maya's PRESERVE_GEOMETRY guards against).
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
        """User click: set state and fire toggled (Preview wires enable/disable here)."""
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
    from blendertk.edit_utils.bevel import Bevel
    from blendertk.core_utils.preview import Preview

    def reset():
        if (
            bpy.context.view_layer.objects.active
            and bpy.context.view_layer.objects.active.mode != "OBJECT"
        ):
            bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.select_all(action="DESELECT")
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)

    def cube_with_one_edge_selected():
        """A fresh cube with exactly one edge (idx 3) selected, back in OBJECT mode — the state
        the Preview captures (selection flushed to mesh data by the Edit→Object transition)."""
        bpy.ops.mesh.primitive_cube_add(size=2)
        obj = bpy.context.active_object
        bpy.ops.object.mode_set(mode="EDIT")
        bm = bmesh.from_edit_mesh(obj.data)
        for seq in (bm.faces, bm.edges, bm.verts):  # clear EVERYTHING first (faces too —
            for el in seq:                          # a still-selected face re-selects its edges)
                el.select = False
        bm.edges.ensure_lookup_table()
        bm.edges[3].select = True
        bm.select_flush(True)
        bmesh.update_edit_mesh(obj.data)
        bpy.ops.object.mode_set(mode="OBJECT")
        return obj

    # ---- engine: Edit-Mode path bevels exactly the selected edge --------------------------
    reset()
    obj = cube_with_one_edge_selected()
    bpy.ops.object.mode_set(mode="EDIT")
    n = Bevel.bevel([obj], width=0.3, segments=3, profile=0.5)
    bpy.ops.object.mode_set(mode="OBJECT")  # exit first — vertex array isn't synced in Edit Mode
    edit_verts = len(obj.data.vertices)
    check("edit-mode bevel beveled one edge", n == 1, f"geom={n}")
    check("edit-mode bevel adds verts (8 -> 14 for 1 edge / 3 segs)",
          edit_verts == 14, f"verts={edit_verts}")

    # ---- engine: Object-Mode path (Preview entry) — same result, mode restored ------------
    reset()
    obj = cube_with_one_edge_selected()
    n = Bevel.bevel([obj], width=0.3, segments=3, profile=0.5)
    check("object-mode bevel beveled one edge", n == 1, f"geom={n}")
    check("object-mode path matches edit-mode result",
          len(obj.data.vertices) == 14, f"verts={len(obj.data.vertices)}")
    check("object-mode path restores OBJECT mode", obj.mode == "OBJECT", obj.mode)

    # ---- engine: empty selection raises a clear error -------------------------------------
    reset()
    bpy.ops.mesh.primitive_cube_add(size=2)
    obj = bpy.context.active_object
    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(obj.data)
    for seq in (bm.faces, bm.edges, bm.verts):
        for el in seq:
            el.select = False
    bmesh.update_edit_mesh(obj.data)
    bpy.ops.object.mode_set(mode="OBJECT")
    raised = False
    try:
        Bevel.bevel([obj], width=0.3, segments=3)
    except RuntimeError:
        raised = True
    check("empty selection raises RuntimeError", raised)

    # ---- engine: no mesh raises -----------------------------------------------------------
    reset()
    empty = bpy.data.objects.new("Empty", None)
    bpy.context.collection.objects.link(empty)
    raised = False
    try:
        Bevel.bevel([empty], width=0.3)
    except RuntimeError:
        raised = True
    check("non-mesh selection raises RuntimeError", raised)

    # ---- Preview: enable bevels; refresh re-bevels from pristine (NO stacking) -------------
    class _Op:
        """Stand-in BevelSlots: Preview calls perform_operation(objects)."""
        def __init__(self):
            self.width = 0.3
        def perform_operation(self, objects):
            Bevel.bevel(objects, width=self.width, segments=3, profile=0.5)

    reset()
    obj = cube_with_one_edge_selected()
    op = _Op()
    chk, btn = _CheckBox(), _Button()
    preview = Preview(op, chk, btn, message_func=lambda *a: None, undo_message="Bevel")

    chk.toggle(True)  # user enables Preview -> snapshot + run
    after_enable = len(obj.data.vertices)
    check("preview enable bevels the captured edge", after_enable == 14, f"verts={after_enable}")

    preview.refresh()  # same params -> rollback + re-run -> still a single bevel
    after_refresh = len(obj.data.vertices)
    check("preview refresh does NOT stack (still 14)", after_refresh == 14, f"verts={after_refresh}")

    op.width = 0.5  # change a value -> refresh re-bevels from pristine
    preview.refresh()
    after_change = len(obj.data.vertices)
    check("preview refresh after value change still single bevel",
          after_change == 14, f"verts={after_change}")

    # ---- Preview: commit keeps the result -------------------------------------------------
    btn.clicked.emit()  # Create
    check("commit keeps the beveled result", len(obj.data.vertices) == 14,
          f"verts={len(obj.data.vertices)}")
    check("commit clears the preview-enabled state", preview.is_enabled is False)

    # ---- Preview: un-check rolls back to the pristine mesh --------------------------------
    reset()
    obj = cube_with_one_edge_selected()
    op = _Op()
    chk, btn = _CheckBox(), _Button()
    preview = Preview(op, chk, btn, message_func=lambda *a: None, undo_message="Bevel")
    chk.toggle(True)
    check("re-enabled preview bevels", len(obj.data.vertices) == 14)
    chk.toggle(False)  # disable -> rollback
    obj = bpy.data.objects.get(obj.name) or obj
    check("disable rolls back to the original 8 verts",
          len(obj.data.vertices) == 8, f"verts={len(obj.data.vertices)}")

except Exception:
    traceback.print_exc()
    lines.append("FAIL unhandled exception")

print("\n".join(lines))
ok = all(l.startswith("OK") for l in lines) and lines
print(f"===RESULT: {'PASS' if ok else 'FAIL'}=== ({sum(1 for l in lines if l.startswith('OK'))}/{len(lines)})")
