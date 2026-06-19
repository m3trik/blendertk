# !/usr/bin/python
# coding=utf-8
"""Live-preview driver for the tentacle Blender tool panels — the Blender analogue of
mayatk's hermetic ``Preview`` (same slot-facing API: an enable checkbox + a commit button
+ ``refresh()``), built on **snapshot/restore** instead of Maya's node-diff CleanupContract.

On enable it captures the current selection, snapshots each captured object (a copy of its
datablock, its world matrix, its collection links) plus the scene's object/mesh/collection
name sets, then runs ``operation.perform_operation(objects)``. ``refresh()`` rolls the scene
back to the snapshot and re-runs (wire it to every parameter widget). Commit keeps the
current result and pushes a **single undo step**; un-checking rolls everything back.

Constraints on ``perform_operation(objects)`` authors:
  - It may create objects/collections/datablocks freely — rollback removes anything whose
    name wasn't present at enable time.
  - It may mutate or even delete the captured objects (their data, matrix and collection
    links are restored — a deleted source is recreated from its snapshot).
  - It must NOT mutate objects outside the captured selection (their state isn't
    snapshotted and won't be restored).
  - Raise with a clear message for user-facing failures — the message lands in
    ``message_func`` and the preview switches itself off.

No Qt import — the checkbox/button are duck-typed (``.toggled.connect`` /
``.clicked.connect`` / ``.setChecked`` / ``.setEnabled``), so the class is headless-testable
with stubs.
"""


class Preview:
    """Snapshot-based live preview: ``Preview(slot, ui.chk000, ui.b000, …)``."""

    def __init__(
        self,
        operation,
        enable_checkbox,
        commit_button,
        finalize_func=None,
        message_func=print,
        undo_message=None,
    ):
        self.operation = operation
        self.enable_checkbox = enable_checkbox
        self.commit_button = commit_button
        self.finalize_func = finalize_func
        self.message_func = message_func
        self.undo_message = undo_message or type(operation).__name__

        self._enabled = False
        self._guard = False  # suppress toggled-handler on programmatic setChecked
        self._captured = []  # captured selection (object names)
        self._active = None
        self._snapshots = []
        self._prior_objects = set()
        self._prior_collections = set()
        self._prior_data = set()

        enable_checkbox.toggled.connect(self._on_toggled)
        commit_button.clicked.connect(self.commit)
        # The co-located ``.ui`` files ship the commit button ``enabled=false`` (copied from
        # mayatk, whose Preview re-enables it on preview-on). This Preview instead supports
        # commit WITHOUT a preview (``commit`` runs the op once on the live selection), so the
        # button must always be clickable — enable it here so every panel's Create works
        # regardless of what its ``.ui`` ships (otherwise the button is dead: preview can never
        # be committed and a no-preview commit is impossible).
        commit_button.setEnabled(True)

    # ------------------------------------------------------------------ public surface
    @property
    def is_enabled(self):
        return self._enabled

    def refresh(self, *args):
        """Roll back to the snapshot and re-run the operation (parameter-change hook)."""
        if not self._enabled:
            return
        self._rollback()
        self._run()

    @staticmethod
    def _ensure_object_mode():
        """The preview workflow is object-level: rollback replaces datablocks and runs
        object-selection ops, both unsafe under a live edit session. Unlike the
        ``_object_mode`` guard this does NOT restore the prior mode — restoring EDIT
        after ``_run`` would re-create the hazard on the next rollback."""
        import bpy

        active = bpy.context.view_layer.objects.active
        if active and active.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")

    def enable(self):
        import bpy

        self._ensure_object_mode()
        objects = [o for o in (bpy.context.selected_objects or []) if o]
        if not objects:
            self.message_func(
                "<strong>Nothing selected.</strong><br>Select object(s) before "
                "enabling the preview."
            )
            self._set_checked(False)
            return
        self._captured = [o.name for o in objects]
        active = bpy.context.view_layer.objects.active
        self._active = active.name if active else None
        self._prior_objects = {o.name for o in bpy.data.objects}
        self._prior_collections = {c.name for c in bpy.data.collections}
        self._prior_data = {m.name for m in bpy.data.meshes} | {
            c.name for c in bpy.data.curves
        }
        self._snapshots = []
        for o in objects:
            data = None
            if o.data is not None:
                data = o.data.copy()
                data.use_fake_user = True  # keep the snapshot alive while unassigned
            self._snapshots.append(
                {
                    "name": o.name,
                    "data": data,
                    "matrix": o.matrix_world.copy(),
                    "collections": [c.name for c in o.users_collection],
                }
            )
        self._enabled = True
        self._set_checked(True)
        self._run()

    def disable(self):
        """Roll back and drop the snapshot (the un-check path)."""
        if self._enabled:
            self._rollback()
        self._drop_snapshots()
        self._enabled = False
        self._set_checked(False)

    def commit(self):
        """Keep the current result and push one undo step. With the preview off, runs the
        operation once directly on the current selection (the Maya commit-without-preview
        behavior)."""
        import bpy

        if not self._enabled:
            objects = [o for o in (bpy.context.selected_objects or []) if o]
            if not objects:
                self.message_func("<strong>Nothing selected.</strong>")
                return
            try:
                self.operation.perform_operation(objects)
            except Exception as e:
                self._report(e)
                return
        else:
            self._drop_snapshots()
            self._enabled = False
            self._set_checked(False)
        if self.finalize_func:
            self.finalize_func()
        bpy.ops.ed.undo_push(message=self.undo_message)

    # ------------------------------------------------------------------ internals
    def _on_toggled(self, state):
        if self._guard:
            return
        if state:
            self.enable()
        else:
            self.disable()

    def _set_checked(self, state):
        self._guard = True
        try:
            self.enable_checkbox.setChecked(state)
        finally:
            self._guard = False

    def _objects(self):
        """Re-resolve the captured selection by name — rollback may have recreated them."""
        import bpy

        return [bpy.data.objects[n] for n in self._captured if n in bpy.data.objects]

    def _run(self):
        import bpy

        try:
            self.operation.perform_operation(self._objects())
        except Exception as e:
            self._report(e)
            self.disable()
            return
        bpy.context.view_layer.update()

    def _report(self, err):
        import traceback

        traceback.print_exc()
        first_line = next((ln for ln in str(err).splitlines() if ln.strip()), "")
        self.message_func(first_line or type(err).__name__)

    def _rollback(self):
        import bpy

        self._ensure_object_mode()
        # 1. anything created since enable goes (objects, then collections, then orphans)
        for o in [o for o in bpy.data.objects if o.name not in self._prior_objects]:
            bpy.data.objects.remove(o, do_unlink=True)
        for c in [
            c for c in bpy.data.collections if c.name not in self._prior_collections
        ]:
            bpy.data.collections.remove(c)
        orphans = [  # datablocks the op created, now unowned — would leak per refresh
            d
            for d in list(bpy.data.meshes) + list(bpy.data.curves)
            if d.users == 0 and d.name not in self._prior_data
        ]
        if orphans:
            bpy.data.batch_remove(orphans)
        # 2. restore the captured sources (recreate any the operation deleted)
        for snap in self._snapshots:
            obj = bpy.data.objects.get(snap["name"])
            if obj is None:
                fresh = self._fresh_copy(snap["data"])
                obj = bpy.data.objects.new(snap["name"], fresh)
                for cname in snap["collections"]:
                    coll = bpy.data.collections.get(cname)
                    (coll or bpy.context.scene.collection).objects.link(obj)
                obj.name = snap["name"]  # reclaim the exact name (old holder is gone)
            elif snap["data"] is not None:
                old = obj.data
                obj.data = self._fresh_copy(snap["data"])
                if old is not None and old.users == 0:
                    bpy.data.batch_remove([old])
            obj.matrix_world = snap["matrix"]
        bpy.context.view_layer.update()
        # 3. restore selection + active
        bpy.ops.object.select_all(action="DESELECT")
        for n in self._captured:
            o = bpy.data.objects.get(n)
            if o:
                o.select_set(True)
        if self._active and self._active in bpy.data.objects:
            bpy.context.view_layer.objects.active = bpy.data.objects[self._active]

    @staticmethod
    def _fresh_copy(data):
        """A working copy of a snapshot datablock (the snapshot itself stays pristine)."""
        if data is None:
            return None
        fresh = data.copy()
        fresh.use_fake_user = False
        return fresh

    def _drop_snapshots(self):
        import bpy

        orphans = []
        for snap in self._snapshots:
            d = snap["data"]
            if d is not None:
                d.use_fake_user = False
                if d.users == 0:
                    orphans.append(d)
        if orphans:
            bpy.data.batch_remove(orphans)
        self._snapshots = []
        self._captured = []
        self._active = None
        self._prior_objects = set()
        self._prior_collections = set()
        self._prior_data = set()
