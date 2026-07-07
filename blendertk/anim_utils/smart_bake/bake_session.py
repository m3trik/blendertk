# !/usr/bin/python
# coding=utf-8
"""Persistence and restore engine for SmartBake's nondestructive manifest — mirror of mayatk's
``anim_utils.smart_bake.bake_session`` at the class/function-name level (``BakeSessionStore``,
``RestoreResult``, ``restore_session``, ``node_ref``/``resolve_ref``). The restore *mechanism*
itself does not port mayatk's internals — it is reimplemented on Blender-native primitives, since
the Maya-only problems that machinery solves don't exist here.

A *bake session* is a JSON record of everything a ``SmartBake.bake()`` call changed, persisted as
a string custom property on the ``data_internal`` Empty (``node_utils.data_nodes.DataNodes``) so
it survives scene save/reopen without riding into an FBX export — that carrier is ``data_export``,
which this module never touches.

Divergence from mayatk (by design, not a gap to fill in later):
    * No curve stashing / driver-network reconnect machinery. ``bpy.ops.nla.bake`` never
      disconnects or deletes a constraint or driver (confirmed live) — there is nothing to rebuild
      on restore except *mute state*. A bake here is: swap in a fresh baked Action, mute the
      sources that would otherwise fight it every evaluation. Restore is the exact reverse: drop
      the baked Action, reassign the original one, unmute the sources back to their prior state.
    * No UUID-based node references. Blender objects/actions carry no persistent id across a
      save/reopen (``session_uid`` resets each process) but names ARE force-unique within
      ``bpy.data.objects`` / ``bpy.data.actions`` — so a reference is just ``{"name", "kind"}``.
    * No separate IK-handle restore bucket. IK in Blender is an IK-type constraint on a pose
      bone; it restores through the same muted-constraint path as any other constraint.
    * Blend-shape (shape-key) drivers ARE destructively removed by the existing
      ``bake_blend_shapes()`` helper — a property can't carry both a live driver and independently
      keyed values the way a constraint and a baked action can coexist. This module lets the
      caller snapshot a driver (:func:`snapshot_blend_shape_driver`) before it's removed and
      best-effort rebuilds it on restore, scoped to the common case (a ``SCRIPTED`` driver whose
      variables are all ``SINGLE_PROP`` targeting an ``OBJECT``). Anything more exotic
      (``TRANSFORMS``/``ROTATION_DIFF`` variables, ``AVERAGE``/``SUM`` driver types) is recorded
      but marked non-rebuildable — restore warns instead of guessing.
    * A shape key already carrying its OWN action (no driver) has no driver to remove, but
      ``bake_blend_shapes()`` still densely resamples that SAME fcurve in place — there is no
      fresh Action datablock for this path the way the transform bake gets one. This module
      snapshots the pre-bake keyframes (:func:`snapshot_blend_shape_action`) and best-effort
      rebuilds them on restore by clearing the resampled fcurve and re-inserting the originals.
"""
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Object / action references (plain-name based — Blender has no cross-session UUID; see module
# docstring)
# ---------------------------------------------------------------------------


def node_ref(obj_or_action) -> Optional[Dict[str, str]]:
    """Return a plain-name reference ``{"name", "kind"}`` for a live object or action.

    ``kind`` is ``"action"`` for a ``bpy.types.Action``, else ``"object"``. Every reference this
    system records points at a local datablock (never a linked-library one), so a name lookup
    against ``bpy.data.objects``/``bpy.data.actions`` is unambiguous. Returns ``None`` for ``None``.
    """
    if obj_or_action is None:
        return None
    import bpy

    kind = "action" if isinstance(obj_or_action, bpy.types.Action) else "object"
    return {"name": obj_or_action.name, "kind": kind}


def resolve_ref(ref: Optional[Dict[str, str]]):
    """Resolve a :func:`node_ref` back to a live ``bpy.types.Object``/``Action``, or ``None``."""
    if not ref:
        return None
    import bpy

    name = ref.get("name")
    if not name:
        return None
    collection = bpy.data.actions if ref.get("kind") == "action" else bpy.data.objects
    return collection.get(name)


def constraint_ref(obj, constraint, bone: Optional[str] = None) -> Dict[str, Any]:
    """Reference a constraint on ``obj`` (or, for an armature, on pose bone ``bone``)."""
    ref: Dict[str, Any] = {"object": node_ref(obj), "name": constraint.name}
    if bone is not None:
        ref["bone"] = bone
    return ref


def resolve_constraint(ref: Optional[Dict[str, Any]]):
    """Resolve a :func:`constraint_ref` back to a live ``bpy.types.Constraint``, or ``None``."""
    if not ref:
        return None
    obj = resolve_ref(ref.get("object"))
    if obj is None:
        return None
    bone = ref.get("bone")
    try:
        constraints = obj.pose.bones[bone].constraints if bone else obj.constraints
    except (KeyError, AttributeError):
        return None
    return constraints.get(ref.get("name"))


def driver_ref(obj, fcurve) -> Dict[str, Any]:
    """Reference a driver ``fcurve`` on ``obj.animation_data.drivers``."""
    return {
        "object": node_ref(obj),
        "data_path": fcurve.data_path,
        "array_index": fcurve.array_index,
    }


def resolve_driver(ref: Optional[Dict[str, Any]]):
    """Resolve a :func:`driver_ref` back to a live driver ``FCurve``, or ``None``."""
    if not ref:
        return None
    obj = resolve_ref(ref.get("object"))
    ad = getattr(obj, "animation_data", None) if obj is not None else None
    if ad is None:
        return None
    data_path = ref.get("data_path")
    array_index = ref.get("array_index", 0)
    for fc in ad.drivers:
        if fc.data_path == data_path and fc.array_index == array_index:
            return fc
    return None


# ---------------------------------------------------------------------------
# Blend-shape driver snapshotting (best-effort rebuild after bake_blend_shapes removes them)
# ---------------------------------------------------------------------------

_REBUILDABLE_DRIVER_TYPES = {"SCRIPTED"}
_REBUILDABLE_VAR_TYPES = {"SINGLE_PROP"}


def snapshot_blend_shape_driver(obj, key_block, fcurve) -> Dict[str, Any]:
    """Serialize a shape-key driver before ``bake_blend_shapes`` removes it.

    Call this once per driven ``key_block`` on ``obj``'s shape keys, before
    ``AnimUtils.bake_blend_shapes()`` runs — it deletes the driver, so this is the only chance to
    capture it. Restricted to the common case: a ``SCRIPTED`` driver whose variables are all
    ``SINGLE_PROP`` targeting an ``OBJECT``. Anything else is still recorded (for traceability) but
    marked ``rebuildable=False``; :func:`restore_session` warns instead of guessing at it.
    """
    drv = fcurve.driver
    rebuildable = drv.type in _REBUILDABLE_DRIVER_TYPES
    variables = []
    for var in drv.variables:
        entry: Dict[str, Any] = {"name": var.name, "type": var.type}
        if var.type in _REBUILDABLE_VAR_TYPES:
            target = var.targets[0]
            if target.id_type == "OBJECT" and target.id is not None:
                entry["target"] = node_ref(target.id)
                entry["data_path"] = target.data_path
            else:
                rebuildable = False
        else:
            rebuildable = False
        variables.append(entry)
    return {
        "object": node_ref(obj),
        "key_block": key_block.name,
        "driver_type": drv.type,
        "expression": drv.expression,
        "variables": variables,
        "rebuildable": rebuildable,
    }


def _restore_blend_shape_driver(entry: dict, warnings: List[str]) -> Optional[str]:
    """Best-effort rebuild of one :func:`snapshot_blend_shape_driver` record.

    Returns ``"object.key_block"`` on success, or ``None`` (with a warning appended) when the
    object/key is missing or the snapshot was marked non-rebuildable.
    """
    object_ref = entry.get("object") or {}
    obj = resolve_ref(object_ref)
    if obj is None:
        warnings.append(f"Blend-shape object '{object_ref.get('name')}' not found.")
        return None

    shape_keys = getattr(obj.data, "shape_keys", None)
    key_block = shape_keys.key_blocks.get(entry.get("key_block")) if shape_keys else None
    if key_block is None:
        warnings.append(
            f"Shape key '{entry.get('key_block')}' on '{obj.name}' not found — "
            "cannot rebuild its driver."
        )
        return None

    if not entry.get("rebuildable", False):
        warnings.append(
            f"Driver on '{obj.name}' shape key '{key_block.name}' was too complex to rebuild "
            "automatically (non-SCRIPTED type or a non-SINGLE_PROP/non-object variable) — "
            "recreate it manually if needed."
        )
        return None

    from blendertk.rig_utils._rig_utils import RigUtils

    fcurve = key_block.driver_add("value")
    drv = fcurve.driver
    drv.type = entry.get("driver_type", "SCRIPTED")
    drv.expression = entry.get("expression", "")
    for var_entry in entry.get("variables", []):
        # Every variable reaching this loop is guaranteed SINGLE_PROP — snapshot_blend_shape_driver
        # only marks an entry rebuildable when ALL its variables are SINGLE_PROP/OBJECT-targeted.
        name = var_entry.get("name") or "var"
        target_obj = resolve_ref(var_entry.get("target"))
        if target_obj is None:
            warnings.append(
                f"Driver variable '{name}' target not found while rebuilding "
                f"'{obj.name}' shape key '{key_block.name}'."
            )
            continue
        RigUtils.add_prop_var(fcurve, name, target_obj, var_entry.get("data_path", ""))
    return f"{obj.name}.{key_block.name}"


# ---------------------------------------------------------------------------
# Blend-shape ACTION snapshotting — the no-driver case. ``bake_blend_shapes`` resamples this
# fcurve densely IN PLACE (same Action, no fresh datablock the way the transform bake gets one),
# so the only way back is recording every existing key and rebuilding them on restore.
# ---------------------------------------------------------------------------


def snapshot_blend_shape_action(obj, key_block, fcurve) -> Dict[str, Any]:
    """Serialize a shape-key's own (non-driver) keyframes before ``bake_blend_shapes`` resamples
    them in place. Call once per key_block whose weight is animated by its OWN action (no
    driver) — see :func:`SmartBake._shape_key_block_for_fcurve` / the ``elif ad.action`` branch
    of ``_smart_bake._analyze_blend_shapes``."""
    keys = []
    for k in fcurve.keyframe_points:
        keys.append(
            {
                "co": (k.co.x, k.co.y),
                "interpolation": k.interpolation,
                "handle_left_type": k.handle_left_type,
                "handle_right_type": k.handle_right_type,
                "handle_left": (k.handle_left.x, k.handle_left.y),
                "handle_right": (k.handle_right.x, k.handle_right.y),
            }
        )
    return {
        "object": node_ref(obj),
        "key_block": key_block.name,
        "data_path": fcurve.data_path,
        "array_index": fcurve.array_index,
        "keys": keys,
    }


def _restore_blend_shape_action(entry: dict, warnings: List[str]) -> Optional[str]:
    """Best-effort rebuild of one :func:`snapshot_blend_shape_action` record — clears the
    densely-resampled fcurve ``bake_blend_shapes`` produced and re-inserts the original keys.

    Returns ``"object.key_block"`` on success, or ``None`` (with a warning appended) when the
    object/key/fcurve is missing.
    """
    object_ref = entry.get("object") or {}
    obj = resolve_ref(object_ref)
    if obj is None:
        warnings.append(f"Blend-shape object '{object_ref.get('name')}' not found.")
        return None

    shape_keys = getattr(obj.data, "shape_keys", None)
    key_block = shape_keys.key_blocks.get(entry.get("key_block")) if shape_keys else None
    if key_block is None:
        warnings.append(
            f"Shape key '{entry.get('key_block')}' on '{obj.name}' not found — "
            "cannot restore its original keys."
        )
        return None

    ad = getattr(shape_keys, "animation_data", None)
    action = getattr(ad, "action", None) if ad is not None else None
    if action is None:
        warnings.append(
            f"'{obj.name}' shape key '{key_block.name}' has no action to restore keys onto."
        )
        return None

    from blendertk.anim_utils._anim_utils import _slot_fcurves

    data_path = entry.get("data_path")
    array_index = entry.get("array_index", 0)
    fcurve = next(
        (
            fc
            for fc in _slot_fcurves(action)
            if fc.data_path == data_path and fc.array_index == array_index
        ),
        None,
    )
    if fcurve is None:
        warnings.append(
            f"Fcurve '{data_path}' on '{obj.name}' shape key '{key_block.name}' not found — "
            "cannot restore its original keys."
        )
        return None

    fcurve.keyframe_points.clear()
    for key_entry in entry.get("keys", []):
        x, y = key_entry.get("co", (0.0, 0.0))
        k = fcurve.keyframe_points.insert(x, y)
        k.interpolation = key_entry.get("interpolation", k.interpolation)
        k.handle_left_type = key_entry.get("handle_left_type", k.handle_left_type)
        k.handle_right_type = key_entry.get("handle_right_type", k.handle_right_type)
        hl = key_entry.get("handle_left")
        if hl:
            k.handle_left.x, k.handle_left.y = hl
        hr = key_entry.get("handle_right")
        if hr:
            k.handle_right.x, k.handle_right.y = hr
    fcurve.update()
    return f"{obj.name}.{key_block.name}"


# ---------------------------------------------------------------------------
# Manifest store (JSON on data_internal)
# ---------------------------------------------------------------------------


class BakeSessionStore:
    """LIFO stack of bake-session manifests on the ``data_internal`` Empty."""

    ATTR = "smart_bake_sessions"
    SCHEMA_VERSION = 1

    @classmethod
    def load(cls) -> List[dict]:
        """Return all persisted sessions (oldest first)."""
        from blendertk.node_utils.data_nodes import DataNodes

        raw = DataNodes.get_internal_string(cls.ATTR)
        if not raw:
            return []
        try:
            sessions = json.loads(raw)
        except (ValueError, TypeError):
            logger.warning("SmartBake: session manifest is corrupt; ignoring.")
            return []
        return sessions if isinstance(sessions, list) else []

    @classmethod
    def save(cls, sessions: List[dict]) -> None:
        from blendertk.node_utils.data_nodes import DataNodes

        DataNodes.set_internal_string(cls.ATTR, json.dumps(sessions))

    @classmethod
    def push(cls, session: dict) -> None:
        sessions = cls.load()
        sessions.append(session)
        cls.save(sessions)

    @classmethod
    def peek(cls, session_id: Optional[str] = None) -> Optional[dict]:
        """Return the latest session (or the one matching *session_id*)."""
        sessions = cls.load()
        if not sessions:
            return None
        if session_id is None:
            return sessions[-1]
        for session in reversed(sessions):
            if session.get("id") == session_id:
                return session
        return None

    @classmethod
    def pop(cls, session_id: Optional[str] = None) -> Optional[dict]:
        """Remove and return the latest session (or the matching one)."""
        sessions = cls.load()
        if not sessions:
            return None
        if session_id is None:
            session = sessions.pop()
        else:
            session = None
            for i in range(len(sessions) - 1, -1, -1):
                if sessions[i].get("id") == session_id:
                    session = sessions.pop(i)
                    break
            if session is None:
                return None
        cls.save(sessions)
        return session

    @classmethod
    def list_ids(cls) -> List[str]:
        return [s.get("id") for s in cls.load() if s.get("id")]

    @classmethod
    def new_session_id(cls) -> str:
        """A fresh, collision-safe session id.

        Derived from the PERSISTED session count (``len(load())``), not a process-local
        ``itertools.count()`` — a module counter resets to 0 on every fresh Blender launch and
        could collide with an id already sitting in a reopened scene's manifest. Millisecond
        timestamp precision covers the remaining edge case (pop-then-push within the same second
        landing on the same count).
        """
        return f"sb_{int(time.time() * 1000)}_{len(cls.load())}"


# ---------------------------------------------------------------------------
# Restore engine
# ---------------------------------------------------------------------------


@dataclass
class RestoreResult:
    """Result container for ``SmartBake.restore()``."""

    success: bool = False
    """True if the session was found, restorable, and processed."""

    session_id: Optional[str] = None
    """The session that was restored (or attempted)."""

    warnings: List[str] = field(default_factory=list)
    """Per-item issues (missing objects, un-rebuildable drivers). Restore continues past these —
    they flag partial fidelity, not failure."""

    restored_actions: List[str] = field(default_factory=list)
    """Object names whose original (pre-bake) action was reassigned."""

    unmuted_constraints: List[str] = field(default_factory=list)
    """Constraint names restored to their pre-bake ``.mute`` state."""

    unmuted_drivers: List[str] = field(default_factory=list)
    """Driver data paths restored to their pre-bake ``.mute`` state."""

    blend_shapes_restored: List[str] = field(default_factory=list)
    """``"object.key_block"`` entries whose driver was rebuilt from a snapshot."""


def restore_session(session: dict) -> RestoreResult:
    """Reverse everything recorded in *session*. See module docstring. Never raises — per-item
    issues are collected in ``RestoreResult.warnings``, matching mayatk's restore philosophy."""
    result = RestoreResult(session_id=session.get("id"))

    if not session.get("restorable", True):
        msg = (
            f"Bake session '{result.session_id}' was destructive (constraints were deleted, not "
            "muted) and cannot be rebuilt from the manifest."
        )
        result.warnings.append(msg)
        logger.warning(msg)
        return result

    import bpy

    # 1. Swap each baked object's action back, then delete the action nla.bake created.
    for entry in session.get("baked_objects", []):
        object_ref = entry.get("object") or {}
        obj = resolve_ref(object_ref)
        if obj is None:
            result.warnings.append(f"Baked object '{object_ref.get('name')}' not found.")
            continue
        ad = getattr(obj, "animation_data", None)
        baked_action = resolve_ref(entry.get("baked_action"))
        original_action = resolve_ref(entry.get("original_action"))
        if ad is not None:
            ad.action = original_action
        if original_action is not None and "original_action_prior_fake_user" in entry:
            # bake() force-set use_fake_user=True to protect the original action from GC while
            # it wasn't the active action — revert to the value it had before the bake so a
            # restored action doesn't stay permanently pinned against orphans_purge.
            original_action.use_fake_user = entry["original_action_prior_fake_user"]
        if baked_action is not None:
            try:
                bpy.data.actions.remove(baked_action)
            except Exception as e:
                result.warnings.append(
                    f"Could not remove baked action '{baked_action.name}': {e}"
                )
        result.restored_actions.append(obj.name)

    # 2. Unmute constraints to their recorded prior state (may already have been muted before the
    # bake for unrelated reasons — restoring the recorded value, not forcing False, honors that).
    for entry in session.get("muted_constraints", []):
        ref = entry.get("ref") or {}
        constraint = resolve_constraint(ref)
        if constraint is None:
            result.warnings.append(
                f"Constraint '{ref.get('name')}' on "
                f"'{(ref.get('object') or {}).get('name')}' not found."
            )
            continue
        constraint.mute = entry.get("prior_mute", False)
        result.unmuted_constraints.append(constraint.name)

    # 3. Unmute drivers to their recorded prior state.
    for entry in session.get("muted_drivers", []):
        ref = entry.get("ref") or {}
        fcurve = resolve_driver(ref)
        if fcurve is None:
            result.warnings.append(
                f"Driver '{ref.get('data_path')}' on "
                f"'{(ref.get('object') or {}).get('name')}' not found."
            )
            continue
        fcurve.mute = entry.get("prior_mute", False)
        result.unmuted_drivers.append(fcurve.data_path)

    # 4. Best-effort rebuild of blend-shape drivers bake_blend_shapes removed.
    for entry in session.get("blend_shape_drivers", []):
        restored = _restore_blend_shape_driver(entry, result.warnings)
        if restored:
            result.blend_shapes_restored.append(restored)

    # 5. Best-effort restore of blend-shape keyframes bake_blend_shapes densely resampled in
    # place (the "own action, no driver" case — nothing to swap, so we rebuild the raw keys).
    for entry in session.get("blend_shape_actions", []):
        restored = _restore_blend_shape_action(entry, result.warnings)
        if restored:
            result.blend_shapes_restored.append(restored)

    result.success = True
    return result


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    pass
