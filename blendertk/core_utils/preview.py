# !/usr/bin/python
# coding=utf-8
"""Live preview for the tentacle Blender tool panels: Blender's preview session.

The widgets and the lifecycle -- the enable checkbox, the commit button, the
window's show / hide hooks, Select Result, the error popup -- are uitk's
``PreviewController``, shared with mayatk; :class:`Preview` is it bound to
:class:`BlenderPreviewSession`, the scene half written here, built on
**snapshot/restore** where mayatk's is a node-diff ``CleanupContract``.

On enable the session captures the current selection, snapshots each captured object (a
copy of its datablock, its world matrix, its collection links) plus the scene's
object/mesh/collection name sets, then runs ``operation.perform_operation(objects)``.
``refresh()`` rolls the scene back to the snapshot and re-runs (wire it to every parameter
widget). Commit keeps the current result as a **single undo step**; un-checking rolls
everything back.

The preview is **optional**: the commit button is live from the start and a click with the
preview off runs the operation once on the current selection. Panels that must not commit
blind pass ``require_preview=True`` (button dead until the preview is on).

Constraints on ``perform_operation(objects)`` authors:
  - It may create objects/collections/datablocks freely — rollback removes anything whose
    name wasn't present at enable time.
  - It may mutate or even delete the captured objects (their data, matrix and collection
    links are restored — a deleted source is recreated from its snapshot).
  - It must NOT mutate objects outside the captured selection (their state isn't
    snapshotted and won't be restored). If the operation unavoidably does (a custom prop
    on a shared carrier object, a file written to disk), pass ``restore_func`` — called
    once after the rollback that DISCARDS the preview (un-check or failure; never on
    commit) so the operation can repair that out-of-snapshot state itself.
  - Raise with a clear message for user-facing failures — the message lands in
    ``message_func`` and the preview switches itself off.

No Qt import, and ``bpy`` is imported in the call bodies: the class imports and binds its
duck-typed widgets headless.
"""

import contextlib

import uitk


class BlenderPreviewSession:
    """Blender's scene half of a preview (:class:`uitk.PreviewSession`).

    A pass runs on the captured objects; a rollback removes what it created and puts each
    captured object's data, matrix and collection links back from the enable-time
    snapshot (a deleted one is recreated). A commit keeps the result: the controller's
    undo step pushes it.

    Parameters:
        operation: The operation, ``perform_operation(objects)``.
    """

    def __init__(self, operation):
        self.operation = operation
        self._captured = []  # captured selection (object names)
        self._active = None
        self._snapshots = []
        self._prior_objects = set()
        self._prior_collections = set()
        self._prior_data = set()

    @property
    def captured_objects(self):
        """The names of the objects the preview captured."""
        return list(self._captured)

    # ------------------------------------------------------ PreviewSession
    def selection(self):
        """The selected objects, in OBJECT mode: the whole workflow is object-level
        (rollback replaces datablocks and runs object-selection ops, unsafe under a
        live edit session). Read from the view layer: robust from the Qt-pump timer
        context."""
        from blendertk.core_utils._core_utils import CoreUtils

        self._ensure_object_mode()
        return CoreUtils.selected_objects()

    def begin(self, objects):
        """Snapshot *objects* and the scene's name sets.

        ``_prior_data`` is the set of datablocks that already existed, and anything
        outside it is purged as "new" on rollback, which is why the controller runs
        the operation's ``prepare_operation`` (a fork of linked data) BEFORE this.
        """
        import bpy

        from blendertk.core_utils._core_utils import CoreUtils

        self._captured = [o.name for o in objects]
        active = CoreUtils.active_object()  # the window's layer, not the default
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

    def run(self):
        """One pass over the captured objects, the captured selection and active
        object in place first (the operation may read them)."""
        import bpy

        self._restore_selection()
        self.operation.perform_operation(self._objects())
        bpy.context.view_layer.update()

    def rollback(self):
        """Remove what the passes created and restore each captured object."""
        if not self._snapshots:
            return
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

    def discard(self, reselect):
        """Drop the snapshot, the captured selection back first with *reselect*."""
        if reselect:
            self._restore_selection()
        self._drop_snapshots()

    def commit(self):
        """Keep the result: drop the snapshot."""
        self._drop_snapshots()

    def apply(self, objects):
        """Run the operation once over *objects*."""
        self.operation.perform_operation(list(objects))

    @staticmethod
    def undo_step(name):
        """ONE undo step named *name*, nested operators' own steps folded in
        (``CoreUtils.undo_chunk``)."""
        from blendertk.core_utils._core_utils import CoreUtils

        return CoreUtils.undo_chunk(name)

    @staticmethod
    def unrecorded():
        """Blender records a step only when one is pushed, which a discard never does:
        nothing to suspend."""
        return contextlib.nullcontext()

    def select_result(self, checkbox, results, committed):
        """:meth:`Preview.apply_result_selection`, in OBJECT mode."""
        self._ensure_object_mode()
        Preview.apply_result_selection(checkbox, results)

    # ------------------------------------------------------------ internals
    @staticmethod
    def _ensure_object_mode():
        """The preview workflow is object-level: rollback replaces datablocks and runs
        object-selection ops, both unsafe under a live edit session. Unlike the
        ``_object_mode`` guard this does NOT restore the prior mode — restoring EDIT
        after a pass would re-create the hazard on the next rollback."""
        import bpy

        from blendertk.core_utils._core_utils import CoreUtils

        # the window's view layer + context (windowless: the scene's default layer,
        # and mode_set's poll fails)
        with CoreUtils.window_context_override():
            active = bpy.context.view_layer.objects.active
            if active and active.mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")

    def _objects(self):
        """Re-resolve the captured selection by name — rollback may have recreated them."""
        import bpy

        return [bpy.data.objects[n] for n in self._captured if n in bpy.data.objects]

    def _restore_selection(self):
        """Select the captured objects, the captured active one active (none
        captured: the selection is left alone)."""
        if not self._captured:
            return
        import bpy

        from blendertk.core_utils._core_utils import CoreUtils

        CoreUtils.select(bpy.data.objects.get(n) for n in self._captured)
        if self._active and self._active in bpy.data.objects:
            view_layer = CoreUtils.active_view_layer()
            try:
                view_layer.objects.active = bpy.data.objects[self._active]
            except RuntimeError:  # not in the window's layer
                pass

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


class Preview(uitk.PreviewController):
    """Snapshot-based live preview: ``Preview(slot, ui.chk000, ui.b000, ...)``.

    :class:`uitk.PreviewController` bound to :class:`BlenderPreviewSession`
    (its session, plus the host's Select Result primitive; the signature and
    every lifecycle method are the controller's). Mirror of mayatk's ``Preview``.
    """

    SESSION_CLASS = BlenderPreviewSession

    @staticmethod
    def apply_result_selection(widget, results):
        """Select the operation's result(s), or deselect them, per a Select Result checkbox
        -- mirror of mayatk's ``Preview.apply_result_selection``.

        Checked, the results become the selection (the first one active); unchecked, they are
        deselected, as an operation can leave its result selected. A result a rollback removed
        is skipped, and a dead widget reads as checked.

        Parameters:
            widget: The Select Result checkbox (``isChecked``).
            results: The result object(s) or object name(s).
        """
        import bpy

        from blendertk.core_utils._core_utils import CoreUtils

        if results is None:
            results = []
        elif isinstance(results, (str, bpy.types.ID)):
            results = [results]
        objects = []
        for result in results:
            try:
                name = result if isinstance(result, str) else result.name
            except ReferenceError:  # removed by a rollback
                continue
            obj = bpy.data.objects.get(name)
            if obj is not None:
                objects.append(obj)
        if not objects:
            return
        try:
            select = widget.isChecked()
        except Exception:
            select = True
        CoreUtils.select(objects, mode="replace" if select else "remove")
