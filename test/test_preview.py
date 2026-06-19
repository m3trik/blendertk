"""blendertk core_utils.preview headless test — snapshot/rollback/commit semantics with a
stub operation + duck-typed widgets (no Qt needed).
Run: blender --background --factory-startup --python blendertk/test/test_preview.py
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


class _Sig:
    def __init__(self):
        self._subs = []
    def connect(self, fn):
        self._subs.append(fn)
    def emit(self, *a):
        for fn in self._subs:
            fn(*a)


class FakeCheck:
    """Mimics QCheckBox: setChecked fires toggled (like real Qt) so the guard is exercised."""
    def __init__(self):
        self.toggled = _Sig()
        self._c = False
    def setChecked(self, v):
        if v != self._c:
            self._c = v
            self.toggled.emit(v)
    def isChecked(self):
        return self._c


class FakeButton:
    def __init__(self):
        self.clicked = _Sig()
        self._enabled = False  # mimics the .ui's enabled=false default
    def setEnabled(self, v):
        self._enabled = bool(v)
    def isEnabled(self):
        return self._enabled
    def click(self):
        self.clicked.emit()


try:
    import bpy
    import blendertk as btk

    def reset():
        bpy.ops.object.select_all(action="DESELECT")
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)
        for m in [m for m in bpy.data.meshes if m.users == 0]:
            bpy.data.meshes.remove(m)

    def cube(name=None, x=0.0):
        bpy.ops.mesh.primitive_cube_add(size=2.0, location=(x, 0, 0))
        o = bpy.context.active_object
        if name:
            o.name = name
        o.select_set(True)
        return o

    msgs = []

    class CreatorOp:
        """Creates 3 linked copies (the duplicate-panel shape)."""
        def perform_operation(self, objects):
            btk.duplicate_linear(objects[0], 3, translate=(6, 0, 0), calculation_mode="linear")

    class MutatorOp:
        """Mutates the source mesh in place (the mirror merge-mode 0/1 shape)."""
        def perform_operation(self, objects):
            btk.mirror(objects, axis="x", pivot="world", merge_mode=0)

    class DeleterOp:
        """Deletes the source (the radial keep_original=False shape)."""
        def perform_operation(self, objects):
            btk.duplicate_radial(objects[0], 2, end_angle=90, rotate_axis="z", pivot="world")

    class FailerOp:
        def perform_operation(self, objects):
            raise ValueError("intentional failure")

    # ---- the commit button is enabled at construction (the .ui ships it disabled; a panel
    #      whose Create stays dead can never commit — the bug this guards against)
    reset()
    o = cube("Src")
    chk, btn = FakeCheck(), FakeButton()
    check("commit button starts disabled in the .ui", not btn.isEnabled())
    pv = btk.Preview(CreatorOp(), chk, btn, message_func=msgs.append)
    check("Preview enables the commit button so Create works", btn.isEnabled())

    # ---- enable creates, refresh doesn't accumulate, disable rolls all back
    chk.setChecked(True)
    check("enable runs the op (3 copies)", len(bpy.data.objects) == 4, f"n={len(bpy.data.objects)}")
    check("is_enabled", pv.is_enabled)
    pv.refresh()
    pv.refresh()
    check("refresh doesn't accumulate", len(bpy.data.objects) == 4, f"n={len(bpy.data.objects)}")
    n_meshes = len(bpy.data.meshes)
    pv.refresh()
    check("refresh doesn't leak datablocks", len(bpy.data.meshes) == n_meshes,
          f"{n_meshes}->{len(bpy.data.meshes)}")
    chk.setChecked(False)
    check("disable rolls back to just the source", len(bpy.data.objects) == 1,
          f"n={len(bpy.data.objects)}")
    check("disable drops preview state", not pv.is_enabled)

    # ---- commit keeps the result
    reset()
    o = cube("Src")
    chk, btn = FakeCheck(), FakeButton()
    pv = btk.Preview(CreatorOp(), chk, btn, message_func=msgs.append)
    chk.setChecked(True)
    btn.click()
    check("commit keeps the copies", len(bpy.data.objects) == 4, f"n={len(bpy.data.objects)}")
    check("commit unchecks + disables", not chk.isChecked() and not pv.is_enabled)

    # ---- commit with preview OFF runs once directly
    reset()
    o = cube("Src")
    chk, btn = FakeCheck(), FakeButton()
    pv = btk.Preview(CreatorOp(), chk, btn, message_func=msgs.append)
    btn.click()
    check("commit-without-preview runs once", len(bpy.data.objects) == 4, f"n={len(bpy.data.objects)}")

    # ---- mesh mutation is restored on rollback
    reset()
    o = cube("Src")
    v0 = len(o.data.vertices)
    chk, btn = FakeCheck(), FakeButton()
    pv = btk.Preview(MutatorOp(), chk, btn, message_func=msgs.append)
    chk.setChecked(True)
    check("mutator doubles verts while enabled", len(bpy.data.objects[ "Src"].data.vertices) == v0 * 2)
    pv.refresh()
    check("mutator refresh doesn't stack", len(bpy.data.objects["Src"].data.vertices) == v0 * 2,
          f"v={len(bpy.data.objects['Src'].data.vertices)}")
    chk.setChecked(False)
    check("mutator rollback restores the mesh", len(bpy.data.objects["Src"].data.vertices) == v0,
          f"v={len(bpy.data.objects['Src'].data.vertices)}")

    # ---- a deleted source is recreated on rollback (matrix + name + collection)
    reset()
    o = cube("Src", x=3.0)
    chk, btn = FakeCheck(), FakeButton()
    pv = btk.Preview(DeleterOp(), chk, btn, message_func=msgs.append)
    chk.setChecked(True)
    check("deleter removed the source while enabled", "Src" not in bpy.data.objects
          or bpy.data.objects["Src"].type == "EMPTY")
    pv.refresh()  # exercises recreate-then-delete-again
    chk.setChecked(False)
    restored = bpy.data.objects.get("Src")
    check("deleted source recreated on rollback", restored is not None and restored.type == "MESH")
    check("recreated source keeps its transform",
          restored is not None and abs(restored.matrix_world.translation.x - 3.0) < 1e-5)
    check("recreated source is back in the view layer",
          restored is not None and restored.name in bpy.context.view_layer.objects)

    # ---- failure: message lands + preview switches itself off + scene is clean
    reset()
    o = cube("Src")
    chk, btn = FakeCheck(), FakeButton()
    pv = btk.Preview(FailerOp(), chk, btn, message_func=msgs.append)
    n_msgs = len(msgs)
    chk.setChecked(True)
    check("failure reports a message", len(msgs) > n_msgs and "intentional" in msgs[-1], f"{msgs[-n_msgs:]}")
    check("failure disables the preview", not pv.is_enabled and not chk.isChecked())
    check("failure leaves the scene intact", len(bpy.data.objects) == 1)

    # ---- empty selection: message + stays unchecked
    reset()
    chk, btn = FakeCheck(), FakeButton()
    pv = btk.Preview(CreatorOp(), chk, btn, message_func=msgs.append)
    n_msgs = len(msgs)
    chk.setChecked(True)
    check("empty selection refuses to enable", not pv.is_enabled and not chk.isChecked())
    check("empty selection reports", len(msgs) > n_msgs)

    # ---- enabling from EDIT mode is safe: rollback replaces datablocks and runs
    #      object-selection ops, so the preview must establish OBJECT mode first
    reset()
    o = cube("Src")
    v0 = len(o.data.vertices)
    bpy.context.view_layer.objects.active = o
    bpy.ops.object.mode_set(mode="EDIT")
    chk, btn = FakeCheck(), FakeButton()
    pv = btk.Preview(MutatorOp(), chk, btn, message_func=msgs.append)
    chk.setChecked(True)
    check("edit-mode enable forces OBJECT mode", o.mode == "OBJECT", f"mode={o.mode}")
    pv.refresh()
    chk.setChecked(False)
    check("edit-mode enable still rolls back cleanly",
          len(bpy.data.objects["Src"].data.vertices) == v0,
          f"v={len(bpy.data.objects['Src'].data.vertices)}")

except Exception:
    traceback.print_exc()
    lines.append("FAIL unhandled exception")

print("\n".join(lines))
ok = all(l.startswith("OK") for l in lines) and lines
print(f"===RESULT: {'PASS' if ok else 'FAIL'}=== ({sum(1 for l in lines if l.startswith('OK'))}/{len(lines)})")
