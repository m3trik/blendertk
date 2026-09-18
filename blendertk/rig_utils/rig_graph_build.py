# !/usr/bin/python
# coding=utf-8
"""Build a RigGraph in Blender -- phase 3 of the rig-transfer stack.

The planner (``pythontk.RigPlanner``) decides what this target can build from
a graph and what must be baked; this module is the target. It composes the
rig primitives ``RigUtils`` already has -- a Maya ``parentConstraint`` is a
``CHILD_OF``, an ``aimConstraint`` a ``TRACK_TO``, a set-driven key a driver
whose own F-curve holds the keys, an ``ikSplineSolver`` a ``SPLINE_IK`` -- and
declares what it built as data the planner reads (:meth:`capability`).

Fidelity is EARNED (schema section 9.1). No conformance fixture vouches for
any op yet, so every entry grades ``approximate`` and the planner attaches
``verify`` to every record it builds: a built constraint is measured against
the source's sampled points (``pythontk.RigVerify``) before it is trusted,
never assumed equivalent because its name matches.

Identity is the payload's prim path. On the USD route an object's path is
``UsdUtils.prim_path`` (the importer's ``.NNN`` collision suffixes stripped);
on the FBX route only the leaf survives, so ids resolve by leaf with the same
``.NNN`` tolerance the manifest appliers use. Joints arrive as bones of an
imported armature and resolve by bone name.

``import bpy`` is deferred into the call bodies (no import side effects), so
the surface resolves under headless ``--background`` and in plain-venv tests.
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pythontk as ptk


class _RigGraphBuilderInternal:
    """Resolution and the per-op builders."""

    #: Maya axis vector -> Blender TRACK_TO axis enums.
    _TRACK_AXIS: Dict[Tuple[int, int, int], str] = {
        (1, 0, 0): "TRACK_X",
        (0, 1, 0): "TRACK_Y",
        (0, 0, 1): "TRACK_Z",
        (-1, 0, 0): "TRACK_NEGATIVE_X",
        (0, -1, 0): "TRACK_NEGATIVE_Y",
        (0, 0, -1): "TRACK_NEGATIVE_Z",
    }
    _UP_AXIS: Dict[Tuple[int, int, int], str] = {
        (1, 0, 0): "UP_X",
        (0, 1, 0): "UP_Y",
        (0, 0, 1): "UP_Z",
    }
    _CHANNEL_PATH: Dict[str, Tuple[str, Optional[int]]] = {
        "translate.x": ("location", 0),
        "translate.y": ("location", 1),
        "translate.z": ("location", 2),
        "rotate.x": ("rotation_euler", 0),
        "rotate.y": ("rotation_euler", 1),
        "rotate.z": ("rotation_euler", 2),
        "scale.x": ("scale", 0),
        "scale.y": ("scale", 1),
        "scale.z": ("scale", 2),
        "visibility": ("hide_viewport", None),
    }
    _TRANSFORM_VAR: Dict[str, str] = {
        "translate.x": "LOC_X",
        "translate.y": "LOC_Y",
        "translate.z": "LOC_Z",
        "rotate.x": "ROT_X",
        "rotate.y": "ROT_Y",
        "rotate.z": "ROT_Z",
        "scale.x": "SCALE_X",
        "scale.y": "SCALE_Y",
        "scale.z": "SCALE_Z",
    }

    def __init__(self) -> None:
        self._objects: Dict[str, Any] = {}
        #: FBX route: EVERY object per leaf -- the two production configs carry
        #: identical leaves, and a first-wins lookup gave one of them both rigs.
        self._by_leaf: Dict[str, List[Any]] = {}
        self._bones: Dict[str, Tuple[Any, str]] = {}
        self._drivers: List[Any] = []
        #: What each record's build created, so a verify miss can take it back
        #: (schema 9.4): ``{record id: [("constraint", owner, con) | ("driver", owner, fcurve)]}``.
        self._created: Dict[str, List[Tuple[str, Any, Any]]] = {}
        #: The payload's own baked keys on channels a built record now drives,
        #: MUTED for the verify step: the carrier bakes constrained motion too,
        #: and a fresh constraint composed on top of those keys is a double
        #: transform (217/217 production blends diverged before this). A pass
        #: deletes them (:meth:`commit`); a miss unmutes them (:meth:`remove`).
        self._muted: Dict[str, List[Any]] = {}
        #: Bones a built record targets that arrived CONNECTED (head glued to the
        #: parent's tail -- no constraint can place such a head), unglued by
        #: :meth:`_free_bones` for the build: ``(armature, bone) -> record ids``.
        #: :meth:`remove` re-glues one once no built record needs it.
        self._freed: Dict[Tuple[Any, str], set] = {}
        #: Every object a built record touches (targets, spaces, their
        #: armatures), for :meth:`evaluable`.
        self._evaluable: List[Any] = []
        self._current: Optional[str] = None

    # ------------------------------------------------------------ resolution
    def _index(self, imported: Sequence[Any], is_usd: bool) -> None:
        """One lookup for the run: prim path (USD) or leaf name (FBX) -> object,
        and bone name -> (armature, bone) for every imported armature."""
        from blendertk.env_utils.usd import UsdUtils

        self._objects.clear()
        self._by_leaf.clear()
        self._bones.clear()
        for obj in imported:
            try:
                name = obj.name
            except ReferenceError:
                continue
            self._by_leaf.setdefault(re.sub(r"\.\d+$", "", name), []).append(obj)
            if is_usd:
                self._objects.setdefault(UsdUtils.prim_path(obj), obj)
            if getattr(getattr(obj, "data", None), "bones", None) is not None:
                for bone in obj.data.bones:
                    self._bones.setdefault(
                        re.sub(r"\.\d+$", "", bone.name), (obj, bone.name)
                    )

    def _object(self, node_id: str) -> Any:
        """The object a node id names, or None: the full prim path (USD), else
        the leaf -- and among objects sharing a leaf, the one whose parent chain
        matches the id's ancestors best."""
        found = self._objects.get(node_id)
        if found is not None:
            return found
        segments = [seg for seg in node_id.split("/") if seg]
        candidates = self._by_leaf.get(segments[-1] if segments else "", [])
        if len(candidates) <= 1:
            return candidates[0] if candidates else None

        def score(obj: Any) -> int:
            matched, cur = 0, obj.parent
            for want in reversed(segments[:-1]):
                if cur is None or re.sub(r"\.\d+$", "", cur.name) != want:
                    break
                matched, cur = matched + 1, cur.parent
            return matched

        return max(candidates, key=score)

    def _is_armature(self, obj: Any) -> bool:
        return getattr(getattr(obj, "data", None), "bones", None) is not None

    def _anchor(self, node_id: str) -> Tuple[Any, Optional[str]]:
        """``(target, subtarget)`` for a constraint source: an object, or the
        armature + bone name when the id names a joint (joints arrive as bones)."""
        obj = self._object(node_id)
        if obj is not None and not self._is_armature(obj):
            return obj, None
        bone = self._bone(node_id)
        if bone is not None:
            return bone[0], bone[1]
        if obj is not None:
            return obj, None
        raise LookupError(f"no imported object or bone for {node_id!r}")

    def _constrain(self, node_id: str, ctype: str, source_id: str, **props: Any) -> Any:
        """One constraint on the object OR pose bone *node_id* names, bound to
        what *source_id* names; tracked for :meth:`remove`."""
        target, subtarget = self._anchor(source_id)
        return self._constrain_to(node_id, ctype, target, subtarget, **props)

    def _constrain_to(
        self,
        node_id: str,
        ctype: str,
        target: Any,
        subtarget: Optional[str] = None,
        **props: Any,
    ) -> Any:
        """One constraint on the object OR pose bone *node_id* names, bound to
        *target* (+ *subtarget* bone); tracked for :meth:`remove`."""
        from blendertk.rig_utils._rig_utils import RigUtils

        if subtarget is not None:
            props["subtarget"] = subtarget
        owner = self._object(node_id)
        if owner is not None and not self._is_armature(owner):
            return self._track(
                "constraint", owner, RigUtils._constraint(owner, ctype, target, **props)
            )
        bone = self._bone(node_id)
        if bone is None:
            raise LookupError(f"no imported object or bone for {node_id!r}")
        armature, name = bone
        con = RigUtils.add_bone_constraint(
            armature, name, ctype, target=target, **props
        )
        return self._track("bone_constraint", armature.pose.bones[name], con)

    def _bone(self, node_id: str) -> Optional[Tuple[Any, str]]:
        return self._bones.get(node_id.rsplit("/", 1)[-1])

    def _free_bones(self, rig: Any, record_ids: Sequence[str]) -> int:
        """Unglue every connected bone a transform record in *record_ids*
        targets (chains included), one edit-mode session per armature; returns
        the count. A Maya joint translates on its own, so a freed bone is the
        faithful shape; a connected one has no head of its own to place, and
        every bone-owner blend on the production module diverged by exactly its
        parent's motion until this pre-pass existed."""
        from blendertk.rig_utils._rig_utils import RigUtils

        wanted: Dict[Any, Dict[str, set]] = {}
        for record_id in record_ids:
            record = rig.record(record_id)
            if record is None or record.shape != "transform":
                continue
            target = record.target
            for node_id in target.get("chain") or [target.get("id")]:
                bone = self._bone(node_id)
                if bone is not None:
                    wanted.setdefault(bone[0], {}).setdefault(bone[1], set()).add(
                        record_id
                    )
        freed = 0
        for armature, names in wanted.items():
            connected = [n for n in names if armature.data.bones[n].use_connect]
            if not connected:
                continue
            with RigUtils._active_mode(armature, "EDIT"):
                ebones = armature.data.edit_bones
                for name in connected:
                    ebone = ebones.get(name)
                    if ebone is None or not ebone.use_connect:
                        continue
                    ebone.use_connect = False
                    self._freed[(armature, name)] = set(names[name])
                    freed += 1
        return freed

    def _reglue_bones(self, record_id: str) -> int:
        """Re-connect the bones only *record_id* had freed (its demotion)."""
        from blendertk.rig_utils._rig_utils import RigUtils

        by_armature: Dict[Any, List[str]] = {}
        for (armature, name), owners in list(self._freed.items()):
            owners.discard(record_id)
            if not owners:
                by_armature.setdefault(armature, []).append(name)
                del self._freed[(armature, name)]
        reglued = 0
        for armature, names in by_armature.items():
            try:
                with RigUtils._active_mode(armature, "EDIT"):
                    ebones = armature.data.edit_bones
                    for name in names:
                        ebone = ebones.get(name)
                        if ebone is not None and ebone.parent is not None:
                            ebone.use_connect = True
                            reglued += 1
            except (ReferenceError, RuntimeError):
                continue
        return reglued

    def _collect_evaluable(self, rig: Any, record_ids: Sequence[str]) -> None:
        """Every object the records in *record_ids* touch, for :meth:`evaluable`."""
        seen: Dict[int, Any] = {}
        for record_id in record_ids:
            record = rig.record(record_id)
            if record is None:
                continue
            ids = list(record.target_ids()) + [
                (s.get("id") or self._split(s["plug"])[0])
                for s in record.sources
                if s.get("id") or s.get("plug")
            ]
            for node_id in ids:
                obj = self._object(node_id)
                bone = self._bone(node_id)
                for candidate in (obj, bone[0] if bone else None):
                    if candidate is not None:
                        seen.setdefault(id(candidate), candidate)
        self._evaluable = list(seen.values())

    @contextmanager
    def evaluable(self):
        """Yield with every object a built record touches evaluable at any
        frame (``AnimUtils.evaluable_override``): a hidden object is not
        evaluated, and rig internals are routinely hidden with keyed visibility
        -- measured: 210 production records "diverged" by exactly their own
        travel, on three runs, whatever the constraint was. Wraps the build
        (offsets are read at the build frame) and the engine's verify."""
        from blendertk.anim_utils._anim_utils import AnimUtils

        with AnimUtils.evaluable_override(self._evaluable):
            yield

    def _world_matrix(self, node_id: str) -> Any:
        """The world matrix of what *node_id* names: an object's, or a joint's
        pose-bone head frame."""
        obj = self._object(node_id)
        if obj is not None and not self._is_armature(obj):
            return obj.matrix_world.copy()
        bone = self._bone(node_id)
        if bone is not None:
            armature, name = bone
            return (armature.matrix_world @ armature.pose.bones[name].matrix).copy()
        if obj is not None:
            return obj.matrix_world.copy()
        raise LookupError(f"no imported object or bone for {node_id!r}")

    def _space_helpers(
        self, node_id: str, spaces: Sequence[Tuple[str, Any]]
    ) -> List[Any]:
        """One SPACE HELPER per ``(source_id, frame)`` in *spaces*: an empty
        parented to the space (object, or armature + bone) that holds *frame*
        as a constant offset, so its world matrix is the space's world matrix
        carried through that offset -- Maya's constraint-target semantics.
        Measured, not modelled: Blender's own parent frame is read back after
        one depsgraph update, so a bone parent's tail origin needs no formula."""
        import bpy

        owner = self._object(node_id) or (self._bone(node_id) or (None,))[0]
        collections = getattr(owner, "users_collection", None) or ()
        collection = collections[0] if collections else bpy.context.scene.collection
        helpers = []
        for source_id, frame in spaces:
            target, subtarget = self._anchor(source_id)
            helper = bpy.data.objects.new(f"{node_id.rsplit('/', 1)[-1]}.space", None)
            helper.empty_display_type, helper.empty_display_size = "PLAIN_AXES", 0.05
            helper["rig_graph"] = "space"  # the extractor folds it back into its space
            collection.objects.link(helper)
            helper.parent = target
            if subtarget is not None:
                helper.parent_type, helper.parent_bone = "BONE", subtarget
            self._track("object", None, helper)
            helpers.append((helper, frame))
        bpy.context.view_layer.update()
        for helper, frame in helpers:
            helper.matrix_parent_inverse = helper.matrix_world.inverted_safe()
            helper.matrix_basis = frame
        return [h for h, _frame in helpers]

    # ------------------------------------------------------------ drivers
    def _driver_for(self, plug: str, expression: str) -> Tuple[Any, Any]:
        """A SCRIPTED driver on the target *plug*; returns ``(fcurve, target)``."""
        from blendertk.rig_utils._rig_utils import RigUtils

        node_id, channel = self._split(plug)
        target = self._object(node_id)
        if target is None:
            raise LookupError(f"no imported object for {node_id!r}")
        if channel in self._CHANNEL_PATH:
            data_path, index = self._CHANNEL_PATH[channel]
        else:
            data_path, index = f'["{channel}"]', None
            RigUtils.ensure_custom_prop(target, channel, 0.0)
        fc = RigUtils._driver_add(target, data_path, index)
        fc.driver.type = "SCRIPTED"
        fc.driver.expression = expression
        self._drivers.append(target)
        self._track("driver", target, fc)
        return fc, target

    def _track(self, kind: str, owner: Any, created: Any) -> Any:
        if self._current is not None:
            self._created.setdefault(self._current, []).append((kind, owner, created))
        return created

    def _add_var(self, fcurve: Any, name: str, plug: str) -> None:
        """Bind *name* in a driver to the source *plug* (rule 9: a plug Value)."""
        from blendertk.rig_utils._rig_utils import RigUtils

        node_id, channel = self._split(plug)
        source = self._object(node_id)
        if source is None:
            raise LookupError(f"no imported object for {node_id!r}")
        if channel in self._TRANSFORM_VAR:
            RigUtils.add_transform_var(
                fcurve, name, source, self._TRANSFORM_VAR[channel], "LOCAL_SPACE"
            )
            return
        RigUtils.ensure_custom_prop(source, channel, float(source.get(channel, 0.0)))
        RigUtils.add_prop_var(fcurve, name, source, f'["{channel}"]')

    @staticmethod
    def _split(plug: str) -> Tuple[str, str]:
        from pythontk.core_utils.engines.rig_graph.rig_model import RigGraph

        return RigGraph.split_plug(plug)

    # ------------------------------------------------------------ builders
    def _build_blend(self, record: Any, params: Dict[str, Any]) -> None:
        """parent/point/orient/scale constraint -> the driven REPLACES its
        transform with each space's world matrix carried through a constant
        offset (Maya's semantics), on an object or a pose bone alike.

        Not a CHILD_OF: that composes the space onto the owner's WORLD matrix,
        animated ancestors included, so a driven node under a moving group rode
        the motion twice (measured on the production module: every blend
        diverged by exactly its ancestor's travel). Each space gets a helper
        holding the offset (:meth:`_space_helpers`) and the driven copies the
        helper's world transform; N spaces stack by influence (1.0, then
        w_k / sum(w_1..w_k)) -- a sequential lerp is the weighted mean."""
        target_id = record.target["id"]
        channels = frozenset(record.target.get("channels") or ("translate", "rotate"))
        ctype = {
            frozenset({"translate"}): "COPY_LOCATION",
            frozenset({"rotate"}): "COPY_ROTATION",
            frozenset({"scale"}): "COPY_SCALE",
        }.get(channels, "COPY_TRANSFORMS")
        maintain = params.get("maintain_offset", True)
        driven = self._world_matrix(target_id)
        spaces = [
            (s["id"], driven if maintain else self._world_matrix(s["id"]))
            for s in record.sources
        ]
        helpers = self._space_helpers(target_id, spaces)
        total = 0.0
        for source, helper in zip(record.sources, helpers):
            weight = source.get("weight", 1.0)
            weight = float(weight) if not isinstance(weight, dict) else 1.0
            total += weight
            self._constrain_to(
                target_id, ctype, helper, influence=(weight / total) if total else 1.0
            )

    def _build_aim(self, record: Any, params: Dict[str, Any]) -> None:
        aim_at = next((s for s in record.sources if s.get("role") == "target"), None)
        if aim_at is None:
            raise LookupError("aim needs a 'target' source")
        track = self._TRACK_AXIS.get(self._sign(params.get("aim_axis")), "TRACK_Y")
        up = self._UP_AXIS.get(self._sign(params.get("up_axis"), positive=True), "UP_Z")
        if track[-1] == up[-1]:  # Blender refuses a track axis equal to the up axis
            up = "UP_Z" if track[-1] != "Z" else "UP_Y"
        weight = aim_at.get("weight", 1.0)
        self._constrain(
            record.target["id"],
            "TRACK_TO",
            aim_at["id"],
            track_axis=track,
            up_axis=up,
            influence=float(weight) if not isinstance(weight, dict) else 1.0,
        )

    @staticmethod
    def _sign(axis: Any, positive: bool = False) -> Tuple[int, int, int]:
        if not axis:
            return (0, 1, 0)
        signed = tuple(int(round(v)) if abs(v) >= 0.5 else 0 for v in axis)
        return tuple(abs(v) for v in signed) if positive else signed  # type: ignore[return-value]

    def _build_skin(self, record: Any, params: Dict[str, Any]) -> None:
        """``points/skin``: nothing to build -- the carrier ships a mesh skin as
        an ARMATURE modifier -- but the binding must be THERE: the object needs
        a modifier on the armature the record's influences belong to, else the
        deformer edge did not travel and the record fails (its whole component
        goes with it)."""
        target_id = record.target["id"]
        obj = self._object(target_id)
        if obj is None:
            raise LookupError(f"no imported object for {target_id!r}")
        armatures = [
            bone[0]
            for bone in (self._bone(s["id"]) for s in record.sources if s.get("id"))
            if bone is not None
        ]
        bound = [
            m.object
            for m in getattr(obj, "modifiers", ())
            if m.type == "ARMATURE" and m.object is not None
        ]
        if armatures and any(a == b for a in armatures for b in bound):
            return
        raise LookupError(
            f"skin binding did not travel: {obj.name} has no armature modifier "
            f"for the record's {len(armatures)} resolvable influence(s)"
        )

    def _build_linear(self, record: Any, params: Dict[str, Any]) -> None:
        scale, offset = (
            float(params.get("scale", 1.0)),
            float(params.get("offset", 0.0)),
        )
        fc, _ = self._driver_for(record.target, f"a * {scale!r} + {offset!r}")
        self._add_var(fc, "a", record.sources[0]["plug"])

    def _build_curve(self, record: Any, params: Dict[str, Any]) -> None:
        """A set-driven key -> a driver whose OWN F-curve remaps the driver value."""
        fc, _ = self._driver_for(record.target, "a")
        self._add_var(fc, "a", record.sources[0]["plug"])
        points = params.get("points") or []
        interp = "LINEAR" if params.get("interp") == "linear" else "BEZIER"
        fc.keyframe_points.add(len(points))
        for kp, point in zip(fc.keyframe_points, points):
            kp.co = (float(point[0]), float(point[1]))
            kp.interpolation = interp
            kp.handle_left_type = kp.handle_right_type = "AUTO_CLAMPED"
        fc.update()

    def _build_spline_ik(self, record: Any, params: Dict[str, Any]) -> None:
        from blendertk.rig_utils._rig_utils import RigUtils

        chain = record.target.get("chain") or []
        curve = next((s for s in record.sources if s.get("role") == "curve"), None)
        tip = self._bone(chain[-1]) if chain else None
        curve_obj = self._object(curve["id"]) if curve else None
        if tip is None or curve_obj is None:
            raise LookupError(
                "spline_ik needs an imported chain tip bone and its curve"
            )
        armature, bone = tip
        con = RigUtils.add_spline_ik(armature, bone, curve_obj, chain_count=len(chain))
        self._track("bone_constraint", armature.pose.bones[bone], con)

    def _build_ik(self, record: Any, params: Dict[str, Any]) -> None:
        from blendertk.rig_utils._rig_utils import RigUtils

        chain = record.target.get("chain") or []
        goal = next((s for s in record.sources if s.get("role") == "goal"), None)
        pole = next((s for s in record.sources if s.get("role") == "pole"), None)
        tip = self._bone(chain[-1]) if chain else None
        goal_obj = self._object(goal["id"]) if goal else None
        if tip is None or goal_obj is None:
            raise LookupError("ik needs an imported chain tip bone and its goal")
        armature, bone = tip
        props: Dict[str, Any] = {"chain_count": len(chain)}
        pole_obj = self._object(pole["id"]) if pole else None
        if pole_obj is not None:
            props["pole_target"] = pole_obj
        con = RigUtils.add_bone_constraint(
            armature, bone, "IK", target=goal_obj, **props
        )
        self._track("bone_constraint", armature.pose.bones[bone], con)

    def _build_path(self, record: Any, params: Dict[str, Any]) -> None:
        from blendertk.rig_utils._rig_utils import RigUtils

        target = self._object(record.target["id"])
        curve = next((s for s in record.sources if s.get("role") == "curve"), None)
        curve_obj = self._object(curve["id"]) if curve else None
        if target is None or curve_obj is None:
            raise LookupError("path needs an imported target and its curve")
        u = params.get("u", 0.0)
        con = RigUtils._constraint(
            target,
            "FOLLOW_PATH",
            curve_obj,
            use_curve_follow=bool(params.get("follow", True)),
            use_fixed_location=True,
            offset_factor=float(u) if not isinstance(u, dict) else 0.0,
        )
        con.forward_axis = {"x": "FORWARD_X", "y": "FORWARD_Y", "z": "FORWARD_Z"}.get(
            str(params.get("front_axis", "x")), "FORWARD_X"
        )
        self._track("constraint", target, con)


class RigGraphBuilder(_RigGraphBuilderInternal, ptk.HelpMixin):
    """Plan a RigGraph against Blender and build what the plan allows.

    Example:
        >>> result = RigGraphBuilder().build(graph_dict, imported, is_usd=True)
        >>> result["built"], result["baked"]
        (['rec_001', ...], ['/rig/ghost', ...])
    """

    #: ``shape/op`` -> builder. Adding an op is one method and one entry here,
    #: plus its capability row in :meth:`capability` -- nothing else changes.
    REGISTRY: Dict[str, str] = {
        "transform/blend": "_build_blend",
        "transform/aim": "_build_aim",
        "transform/ik": "_build_ik",
        "transform/spline_ik": "_build_spline_ik",
        "transform/path": "_build_path",
        "channel/linear": "_build_linear",
        "channel/curve": "_build_curve",
        "points/skin": "_build_skin",
    }

    @classmethod
    def capability(cls) -> Dict[str, Any]:
        """What this target can build, as data the planner reads (section 9.2).

        The SHAPE of each entry (channels, roles, params) is declared here with
        the builder; its ``fidelity`` is a conformance result. No fixture has
        run against these builders yet, so every op is ``approximate`` -- an
        op with no fixture can be built but nothing vouches for it (9.1) --
        and the planner attaches ``verify`` to every record. ``plugs`` is empty
        for the same reason: a driven weight is honestly baked rather than
        built by a driver nobody has measured.

        Returns:
            dict: A capability manifest ``RigCapability.from_dict`` accepts.
        """
        import bpy

        approx = "approximate"
        return {
            "target": "blender",
            "target_version": bpy.app.version_string,
            "schema_version": 1,
            "shapes": ["transform", "channel", "points"],
            "ops": {
                "transform/blend": {
                    "fidelity": approx,
                    "channels": ["translate", "rotate", "scale"],
                    "roles": ["space"],
                    "params": {
                        "compose": ["matrix", "independent"],
                        "maintain_offset": True,
                        "skip": True,
                    },
                    "plugs": [],
                },
                "transform/aim": {
                    "fidelity": approx,
                    "channels": ["rotate"],
                    "roles": ["target", "up"],
                    "params": {
                        "aim_axis": True,
                        "up_axis": True,
                        "up_ref.kind": ["axis", "object", "object_axis"],
                        "up_ref.role": True,
                        "up_ref.vector": True,
                        "up_ref.axis": True,
                    },
                    "plugs": [],
                },
                "transform/ik": {
                    "fidelity": approx,
                    "channels": ["rotate"],
                    "roles": ["goal", "pole"],
                    "params": {
                        "solver": ["rotate_plane", "single_chain", "two_bone"],
                        "twist": True,
                    },
                    "plugs": [],
                },
                "transform/spline_ik": {
                    "fidelity": approx,
                    "channels": ["rotate"],
                    "roles": ["curve", "up_start", "up_end"],
                    "params": {
                        "twist.distribution": ["linear"],
                        "twist.start": True,
                        "twist.end": True,
                    },
                    "plugs": [],
                },
                "transform/path": {
                    "fidelity": approx,
                    "channels": ["translate", "rotate"],
                    "roles": ["curve", "up"],
                    "params": {
                        "u": True,
                        "follow": True,
                        "front_axis": True,
                        "up_axis": True,
                        "bank": True,
                        "up_ref.kind": ["object"],
                        "up_ref.role": True,
                    },
                    "plugs": [],
                },
                "channel/linear": {
                    "fidelity": approx,
                    "params": {"scale": True, "offset": True},
                    "plugs": [],
                },
                "points/skin": {
                    "fidelity": approx,
                    "channels": [],
                    "roles": ["influence"],
                    # A MESH skin travels natively (USD skel / FBX skin -> an
                    # ARMATURE modifier) and is only CHECKED here; a curve,
                    # surface or lattice skin does not travel, so it is refused
                    # and its component bakes rather than driving a bake
                    # (schema 15.6).
                    "params": {"geometry": ["mesh"]},
                    "plugs": [],
                },
                "channel/curve": {
                    "fidelity": approx,
                    "params": {"points": True, "interp": ["linear", "bezier"]},
                    "plugs": [],
                },
            },
        }

    @contextmanager
    def scope(self):
        """Yield with every touched object evaluable AND the scene's frame
        restored afterwards -- the two things a measurement needs and must not
        leave behind (:meth:`evaluable`; schema 15.5)."""
        import bpy

        scene = bpy.context.scene
        current = scene.frame_current
        try:
            with self.evaluable():
                yield
        finally:
            scene.frame_set(current)

    @staticmethod
    def linear_unit() -> str:
        """Blender's scene unit is always metres for our purposes."""
        return "m"

    @staticmethod
    def up_axis() -> str:
        """Blender is Z-up."""
        return "z"

    def sample_world(
        self, node_id: str, frame: int
    ) -> Optional[Tuple[float, float, float]]:
        """The world position of what *node_id* names at *frame* -- an object's
        origin, or a joint's pose-bone head -- or ``None`` when the id resolves to
        nothing measurable. The sampler ``pythontk.RigVerify.verify_plan`` reads;
        bones are half the production module's targets, and a sampler that could
        not see them passed every one of them vacuously."""
        import bpy

        scene = bpy.context.scene
        if scene.frame_current != frame:
            scene.frame_set(frame)
        else:  # what build() just added has not been evaluated at this frame yet
            bpy.context.view_layer.update()
        bone = self._bone(node_id)
        obj = self._object(node_id)
        if obj is not None and not self._is_armature(obj):
            return tuple(obj.matrix_world.translation)
        if bone is not None:
            armature, name = bone
            return tuple(
                (armature.matrix_world @ armature.pose.bones[name].matrix).translation
            )
        if obj is not None:
            return tuple(obj.matrix_world.translation)
        return None

    #: Schema channel group -> the RNA paths the payload's keys live on.
    _KEY_PATHS: Dict[str, Tuple[str, ...]] = {
        "translate": ("location",),
        "rotate": ("rotation_euler", "rotation_quaternion"),
        "scale": ("scale",),
    }

    def _mute_target_keys(self, record: Any) -> None:
        """Mute the baked keys on every channel *record* now drives."""
        from blendertk.anim_utils._anim_utils import AnimUtils

        wanted: List[Tuple[Any, str, Optional[int]]] = []
        if record.shape == "transform":
            target = record.target
            ids = target.get("chain") or [target.get("id")]
            for node_id in ids:
                owner = self._object(node_id)
                bone = self._bone(node_id)
                prefix = ""
                if bone is not None and (owner is None or self._is_armature(owner)):
                    owner, prefix = bone[0], f'pose.bones["{bone[1]}"].'
                if owner is None:
                    continue
                for channel in target.get("channels") or ():
                    for path in self._KEY_PATHS.get(channel, ()):
                        wanted.append((owner, prefix + path, None))
        elif record.shape == "channel":
            node_id, channel = self._split(record.target)
            owner = self._object(node_id)
            if owner is not None and channel in self._CHANNEL_PATH:
                path, index = self._CHANNEL_PATH[channel]
                wanted.append((owner, path, index))
        for owner, path, index in wanted:
            for fc in AnimUtils.get_fcurves(owner):
                if fc.data_path == path and (index is None or fc.array_index == index):
                    if not fc.mute:
                        fc.mute = True
                        self._muted.setdefault(self._current or "", []).append(
                            (owner, fc)
                        )

    def commit(self, record_id: str) -> int:
        """A verified record owns its channels: delete the payload's muted keys
        there. Returns the number of curves removed."""
        from blendertk.anim_utils._anim_utils import _AnimUtilsInternal

        removed = 0
        for owner, fc in self._muted.pop(record_id, []):
            anim = getattr(owner, "animation_data", None)
            if anim is None or anim.action is None:
                continue
            try:
                removed += bool(
                    _AnimUtilsInternal._remove_fcurve(
                        anim.action, getattr(anim, "action_slot", None), fc
                    )
                )
            except (ReferenceError, RuntimeError):
                continue
        return removed

    def remove(self, record_id: str) -> int:
        """Take back everything :meth:`build` created for *record_id* -- the
        demotion a failed verification applies (schema 9.4) -- and unmute the
        keys it had silenced, so no motion is lost. Returns the count removed."""
        for owner, fc in self._muted.pop(record_id, []):
            try:
                fc.mute = False
            except ReferenceError:
                continue
        self._reglue_bones(record_id)
        removed = 0
        for kind, owner, created in reversed(self._created.pop(record_id, [])):
            try:
                if kind == "driver":
                    owner.driver_remove(created.data_path, created.array_index)
                elif kind == "object":  # a space helper
                    import bpy

                    bpy.data.objects.remove(created, do_unlink=True)
                else:  # "constraint" on an object, or "bone_constraint" on a pose bone
                    owner.constraints.remove(created)
                removed += 1
            except (ReferenceError, RuntimeError, ValueError):
                continue
        return removed

    def build(
        self, graph: Dict[str, Any], imported: Sequence[Any], is_usd: bool = False
    ) -> Dict[str, Any]:
        """Plan *graph* against :meth:`capability` and build what it allows.

        Parameters:
            graph: A RigGraph document (plain dict, the extractor's output).
            imported: The objects the payload import created -- what ids
                resolve against.
            is_usd: Resolve ids by prim path (USD) or by leaf name (FBX).

        Returns:
            dict: ``built`` (record ids built), ``baked`` (node ids the plan
                or a failed build left to the bake), ``verify``
                (``{record id: policy}`` for every built record the plan wants
                measured), ``report`` (plan entries plus a ``failed`` entry per
                builder that raised) and ``plan`` (the plan as data).
        """
        from pythontk import RigCapability, RigGraph, RigPlanner

        rig = RigGraph.from_dict(graph)
        plan = RigPlanner.plan(rig, RigCapability.from_dict(self.capability()))
        self._index(imported, is_usd)
        self._drivers = []
        self._created = {}
        self._muted = {}
        self._freed = {}
        self._collect_evaluable(rig, plan.build)
        built: List[str] = []
        baked: List[str] = list(plan.bake)
        report: List[Dict[str, Any]] = [e.to_dict() for e in plan.report]
        edits: List[str] = []
        with self.evaluable():
            freed = self._free_bones(rig, plan.build)
            if freed:
                # Outlives the build for every record that survives, so it is
                # stated rather than left for the user to discover.
                edits.append(
                    f"{freed} connected bone(s) unglued from their parent's tail "
                    "so a constraint can place their heads"
                )
            for record_id in plan.build:
                record = rig.record(record_id)
                if record is None:
                    continue
                method = self.REGISTRY.get(f"{record.shape}/{record.op}")
                self._current = record_id
                try:
                    if method is None:
                        raise LookupError(
                            f"no builder registered for {record.shape}/{record.op}"
                        )
                    getattr(self, method)(record, dict(record.params))
                    self._mute_target_keys(record)
                except Exception as error:  # noqa: BLE001 -- a build that fails is DATA
                    self.remove(record_id)  # a half-built record leaves nothing behind
                    nodes = list(record.target_ids())
                    baked.extend(n for n in nodes if n not in baked)
                    report.append(
                        {
                            "kind": "failed",
                            "severity": "error",
                            "record": record_id,
                            "nodes": nodes,
                            "reason": "builder_raised",
                            "detail": {"error": f"{type(error).__name__}: {error}"},
                            "recoverable": False,
                        }
                    )
                    continue
                built.append(record_id)
        if self._drivers:
            from blendertk.rig_utils._rig_utils import RigUtils

            RigUtils.refresh_drivers(list(dict.fromkeys(self._drivers)))
        verify = {rid: plan.verify[rid] for rid in built if rid in plan.verify}
        return {
            "built": built,
            "baked": baked,
            "verify": verify,
            "report": report,
            "plan": plan.to_dict(),
            "edits": edits,
        }
