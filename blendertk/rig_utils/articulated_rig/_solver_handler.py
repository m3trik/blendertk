# !/usr/bin/python
# coding=utf-8
"""The end control's live solve (Blender) -- the twin of mayatk's ``_solver_expression.py``.

Maya solves an articulated rig's end control in a MEL expression: a port of
``ptk.ArticulationModel.solve`` written into the scene, so any Maya that opens it moves.
Blender carries no evaluator a file can hold that iterates without Python (a driver is
one expression), so here the solve is the model itself, run by blendertk: a pair of
``@persistent`` handlers -- ``depsgraph_update_post`` (an edit) and ``frame_change_post``
(playback, a scrub, an exporter stepping the frames) -- reads each rig's end control and
FK controls as the handler's depsgraph evaluated them, calls
``ArticulatedRig._ik_offsets`` (the one function the engine's ``state`` also calls) and
writes each channel's offset onto its MCH (``_IK``) bone, which the deform channel's
driver adds to the control's FK value. Measured on 5.1: a handler that writes
pose channels gets a second evaluation pass, so ``frame_set``, ``view_layer.update`` and
the FBX exporter's per-frame bake all see the offsets each frame was solved for (an FBX
read back off the file holds every frame's own -- no export staging is needed). A rig
whose inputs did not change since its last solve is skipped
(:attr:`SolverHandler._solved`), so the handler's own write never solves again.

A file must still PLAY its IK where blendertk is not installed. Before every save the
offsets are baked as keys on the MCH channels -- when anything the solve reads (the end
control, its switches, the FK controls) is animated -- and the file is written with
those curves playing. While installed, the handlers own the MCH channels and mute the
baked curves (a playing curve would put its stale bake back at the next frame change,
and the input check would leave it there -- measured); they are unmuted for each save
and when the handlers go (:meth:`SolverHandler.uninstall`).

Divergence from Maya (``docs/STRUCTURE.md``): a Maya scene moves on its end control in
any Maya; a Blender file opened without blendertk plays its baked IK and its FK, but
dragging its end control there moves nothing.
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any, Dict, Iterator, List

logger = logging.getLogger(__name__)


class SolverHandler:
    """The handlers that keep every articulated rig's end control solved, and the bake
    that lets a file play its IK without them. Class-level state: one per session."""

    #: ``bpy.app.handlers`` list -> the handler run from it.
    HANDLERS: Dict[str, str] = {
        "depsgraph_update_post": "_on_depsgraph_update",
        "frame_change_post": "_on_frame_change",
        "save_pre": "_on_save_pre",
        "save_post": "_on_save_post",
        "load_post": "_on_load_post",
    }
    #: ``group_uuid`` -> the inputs the rig's MCH channels were last solved for.
    _solved: Dict[str, Any] = {}
    #: Depth of suspensions (an engine rebuild): the handlers skip.
    _suspended: int = 0

    # ============================================================ install
    @classmethod
    def install(cls) -> bool:
        """Install the handlers (persistent: they survive a file load) and mute every
        rig's baked curves -- the handlers own the MCH channels now. Idempotent and
        reload-proof: any copy already installed is removed by name first."""
        import bpy

        cls._remove_handlers()
        handlers = bpy.app.handlers
        for list_name, name in cls.HANDLERS.items():
            getattr(handlers, list_name).append(handlers.persistent(getattr(cls, name)))
        cls._solved.clear()
        cls.mute_all(True)
        return True

    @classmethod
    def uninstall(cls) -> None:
        """Remove the handlers and unmute every rig's baked curves: without the
        handlers, the file plays its bake."""
        cls._remove_handlers()
        cls._solved.clear()
        cls.mute_all(False)

    @classmethod
    def installed(cls) -> bool:
        """Whether the handlers are installed (any copy of them)."""
        try:
            import bpy
        except ImportError:
            return False
        return any(cls._is_ours(fn) for fn in bpy.app.handlers.depsgraph_update_post)

    @classmethod
    def ensure_installed(cls) -> bool:
        """:meth:`install` unless a copy of the handlers already is (the engine's lazy
        arming: a headless session that touches a rig with an end control)."""
        try:
            import bpy  # noqa: F401
        except ImportError:
            return False
        return True if cls.installed() else cls.install()

    @classmethod
    def handler(cls):
        """The installed ``depsgraph_update_post`` handler, or None."""
        import bpy

        return next(
            (fn for fn in bpy.app.handlers.depsgraph_update_post if cls._is_ours(fn)),
            None,
        )

    @classmethod
    def _is_ours(cls, fn) -> bool:
        return getattr(fn, "__name__", "") in cls.HANDLERS.values() and (
            cls.__name__ in getattr(fn, "__qualname__", "")
        )

    @classmethod
    def _remove_handlers(cls) -> None:
        import bpy

        for list_name in cls.HANDLERS:
            handler_list = getattr(bpy.app.handlers, list_name)
            for fn in list(handler_list):
                if cls._is_ours(fn):
                    handler_list.remove(fn)

    @classmethod
    @contextlib.contextmanager
    def suspended(cls) -> Iterator[None]:
        """Hold the handlers off for the block (a rebuild's half-built bones)."""
        cls._suspended += 1
        try:
            yield
        finally:
            cls._suspended -= 1

    # ============================================================ rigs
    @staticmethod
    def _rig(obj):
        """*obj*'s rig when it is one with an end control, else None (a duplicate's
        copied record included)."""
        from blendertk.rig_utils.articulated_rig._articulated_rig import (
            ArticulatedRig,
        )

        if obj is None or obj.type != "ARMATURE":
            return None
        if ArticulatedRig.DATA_ATTR not in obj.keys() or obj.mode == "EDIT":
            return None
        try:
            rig = ArticulatedRig(obj)
        except ValueError:
            return None
        return rig if rig.end_control is not None else None

    @classmethod
    def rigs(cls, objects=None) -> List[Any]:
        """The rigs with an end control among *objects* (default: every object in the
        file)."""
        import bpy

        out = []
        for obj in bpy.data.objects if objects is None else objects:
            rig = cls._rig(obj)
            if rig is not None:
                out.append(rig)
        return out

    @classmethod
    def mute_all(cls, mute: bool) -> None:
        """Mute (or unmute) every rig's baked MCH curves -- read off the stamped
        plans, so no rig is constructed (nor the handlers armed) on the way."""
        import bpy

        from blendertk.rig_utils.articulated_rig._articulated_rig import (
            ArticulatedRig,
        )

        for obj in bpy.data.objects:
            if obj.type != "ARMATURE" or ArticulatedRig.DATA_ATTR not in obj.keys():
                continue
            for curve in ArticulatedRig._bake_curves(obj):
                try:
                    curve.mute = mute
                except (AttributeError, RuntimeError):  # a linked, read-only rig
                    pass

    # ============================================================ solve
    @classmethod
    def solve(cls, rig, depsgraph=None) -> bool:
        """Write *rig*'s MCH offsets for its inputs as *depsgraph* evaluated them (the
        original values without one), unless it was solved for exactly these.

        Returns:
            Whether anything was written.
        """
        arm = rig.group
        text = arm.get(rig.DATA_ATTR) or ""
        spec = rig.scene_data(arm) or {}
        source = arm.evaluated_get(depsgraph) if depsgraph is not None else arm
        inputs = rig._solve_inputs(source, spec)
        if inputs is None:
            return False
        key = cls._key(text, inputs)
        uid = spec.get("group_uuid")
        if cls._solved.get(uid) == key:
            return False
        rig._write_offsets(rig._ik_offsets(*inputs, spec=spec), spec)
        cls._solved[uid] = key
        return True

    @staticmethod
    def _key(text: str, inputs) -> tuple:
        """What a solve depends on: the stamped plan (limits and give included) and
        the inputs -- exact values, so the handler's own write never matches a stale
        solve."""
        fk, end_matrix, blend, follow = inputs
        flat = tuple(v for row in end_matrix for v in row)
        return (text, tuple(fk), flat, blend, follow)

    @staticmethod
    def _on_depsgraph_update(scene, depsgraph=None) -> None:
        """An edit: solve the rigs whose armature the update touched. Never raises."""
        if SolverHandler._suspended or depsgraph is None:
            return
        try:
            import bpy

            touched = []
            for update in depsgraph.updates:
                obj = update.id
                if not isinstance(obj, bpy.types.Object) or obj.type != "ARMATURE":
                    continue
                original = obj.original
                if original not in touched:
                    touched.append(original)
            for rig in SolverHandler.rigs(touched):
                SolverHandler.solve(rig, depsgraph)
        except Exception:  # noqa: BLE001 - a handler must never break an edit
            logger.debug("Articulated rig solve skipped.", exc_info=True)

    @staticmethod
    def _on_frame_change(scene, depsgraph=None) -> None:
        """A new frame: solve every rig of *scene* for it. Never raises."""
        if SolverHandler._suspended:
            return
        try:
            for rig in SolverHandler.rigs(list(scene.objects)):
                SolverHandler.solve(rig, depsgraph)
        except Exception:  # noqa: BLE001 - a handler must never break playback
            logger.debug("Articulated rig solve skipped.", exc_info=True)

    # ============================================================ bake
    @staticmethod
    def _on_save_pre(*_args) -> None:
        """Bake every rig's offsets into the file, playing: it plays its IK wherever it
        is opened. Never raises: a bake must not cost the save."""
        for rig in SolverHandler.rigs():
            try:
                rig._bake()
                for curve in rig._bake_curves(rig.group):
                    curve.mute = False
            except Exception:  # noqa: BLE001
                logger.warning(
                    "Articulated rig %r: the IK was not baked for the save.",
                    getattr(rig, "name", "?"),
                    exc_info=True,
                )

    @staticmethod
    def _on_save_post(*_args) -> None:
        """The handlers own the MCH channels again: the bake is muted."""
        try:
            SolverHandler.mute_all(True)
        except Exception:  # noqa: BLE001
            logger.debug("Articulated rig bake not muted.", exc_info=True)

    @staticmethod
    def _on_load_post(*_args) -> None:
        """A file opened in a session with the handlers: they own its rigs."""
        try:
            SolverHandler._solved.clear()
            SolverHandler.mute_all(True)
        except Exception:  # noqa: BLE001
            logger.debug("Articulated rig bake not muted.", exc_info=True)
