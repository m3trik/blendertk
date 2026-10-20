# !/usr/bin/python
# coding=utf-8
"""Articulated Rig engine (Blender) -- rigid parts on hinge, swivel, ball and slide joints.

Mirror of mayatk's ``rig_utils/articulated_rig/_articulated_rig.py`` (name + behaviour):
for props that move like a desk lamp or a magnifier arm, every part rigid, and WHERE the
parts meet, and how, is what the rig is about. One plan builds one ARMATURE,
``<rig>_RIG`` -- the rig space, parented where the parts' common parent is:

- **deform bones that ship** (``<rig>_<id>_jnt``, in a hidden ``DEF`` bone collection):
  one per moving part, the bone's rest frame the joint's (X along the link, Z a hinge's
  axis), the part parented to it (``parent_type='BONE'``) where it stands. They are the
  deliverable -- the FBX writes them as LimbNodes under the armature's node, each part
  under its joint, and Unity and the WebXR runtime pose them -- so nothing on them is
  apparatus. A deform bone never hangs under a control bone: the exporter's deform-only
  mode keeps a non-deform ancestor of a deform bone.
- **FK control bones** (``<rig>_<id><control affix>``, the ``CTRL`` collection), nested,
  one per joint at the joint's rest frame, drawn as ``Controls`` shapes and carrying only
  the joint's channels (the others locked) within its limits (Limit Rotation / Location
  constraints in local space that the transform tools respect). Each deform channel is a
  driver over the control's channel clamped to its limits (a SUM, or a ``max`` / ``min``
  simple expression -- Blender evaluates both without Python, so a file opened with Auto
  Run off plays), and what an animator keys is what the joint shows, what an engine
  plays back and what a grab writes (``ptk.ArticulationModel`` works in the same
  numbers).
- **the end control** (``<rig>_end<control affix>``, mayatk's IK target): a box around
  the end link, its ``ikBlend`` and ``followRotation`` custom properties keyable. With
  it each FK control hangs under an MCH bone (``<control>_IK``, the hidden ``MCH``
  collection, Maya's IK group) and each deform driver adds that bone's channel (an
  ``ik`` variable) to the control's: ``joint = FK + offset``, the offset what
  ``ptk.ArticulationModel.solve`` moves the chain by to put the end link on the end
  control, faded by ``ikBlend`` -- written by blendertk's handlers
  (``_solver_handler.py``) and baked into the file at every save, so a file opened
  without blendertk plays its keyed IK.
- **the ``articulation`` record** (``ptk.SceneRecords.ARTICULATION``, produced at export
  by :meth:`ArticulatedRig.export_record`).

The plan comes from the parts' geometry (:meth:`ArticulatedRig.analyze` over
``ptk.ArticulationAnalysis``) or from the caller, and is stamped on the armature
(:attr:`ArticulatedRig.DATA_ATTR`) in the armature's own space, in mayatk's JSON shape.
Every edit after the build is an edit of that plan (``ptk.ArticulationPlan``) and a
rebuild that keeps the armature object and the control bones' names, so the F-curves on
the controls stay bound; a channel the new plan drops loses its curve, with a warning.
The pivots themselves move by hand: :meth:`ArticulatedRig.begin_adjust` puts out a
handle (an Empty riding the deform bone of the part a joint hangs off) on every joint
and on the end control, and :meth:`ArticulatedRig.end_adjust` rebuilds on where they
stand. And a part is posed by its own geometry: the grab (``grab_tool.py``, over
:meth:`ArticulatedRig.grab_begin` / :meth:`ArticulatedRig.grab_to`) drags it with the
model the runtimes' hand grab runs.

Blender has no node UUIDs, so identity is by ID pointer: the armature holds a pointer to
every part (and each part's original parent) under the uuid the plan names it by
(:attr:`ArticulatedRig.PARTS_ATTR`), which survives any rename. A Shift+D copy of the
armature copies its record verbatim and Blender remaps a pointer back to the copied
object itself, so a self-pointer cannot tell the copy from the rig; the scene's
registry (:attr:`ArticulatedRig.REGISTRY_ATTR`, the rig's ``group_uuid`` -> its
armature DATA, which Shift+D duplicates) can, and a copy is refused as mayatk refuses a
duplicated record.
"""

from __future__ import annotations

import contextlib
import copy
import json
import math
import re
import uuid as _uuid
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pythontk as ptk

from blendertk.core_utils._core_utils import CoreUtils
from blendertk.rig_utils._rig_utils import RigUtils
from blendertk.rig_utils.articulated_rig._solver_handler import SolverHandler
from blendertk.rig_utils.controls import Controls


class _ArticulatedRigInternal:
    """Scene plumbing behind :class:`ArticulatedRig`: object lookup, shells, matrices
    and the part records."""

    #: A part's own transform state, captured before the build parents it to its joint
    #: and put back exactly by the teardown (with its parent, parent type, parent bone
    #: and parent inverse).
    PART_ATTRS: Tuple[str, ...] = (
        "parent_type",
        "parent_bone",
        "location",
        "rotation_mode",
        "rotation_euler",
        "rotation_quaternion",
        "rotation_axis_angle",
        "scale",
        "lock_location",
        "lock_rotation",
        "lock_rotation_w",
        "lock_rotations_4d",
        "lock_scale",
    )
    #: Blender is Z-up: the analysis's default root is the part lowest along it.
    UP: Tuple[float, float, float] = (0.0, 0.0, 1.0)
    #: A channel's pose-bone property and index.
    CHANNEL_PATHS: Dict[str, Tuple[str, int]] = {
        "rx": ("rotation_euler", 0),
        "ry": ("rotation_euler", 1),
        "rz": ("rotation_euler", 2),
        "tx": ("location", 0),
        "ty": ("location", 1),
        "tz": ("location", 2),
    }
    #: An object's channels something may drive (keys, a driver): such a part cannot
    #: be rigged -- its own channels move it, and the rig parents across them.
    DRIVEN_PATHS: Tuple[str, ...] = (
        "location",
        "rotation_euler",
        "rotation_quaternion",
        "rotation_axis_angle",
        "scale",
        "delta_location",
        "delta_rotation_euler",
        "delta_rotation_quaternion",
        "delta_scale",
    )
    #: The open side of a one-sided rotation limit (Limit Rotation has one toggle per
    #: axis): a full turn, past anything the constraint's decomposition reads.
    FREE_TURN = math.tau
    #: A bone's length over its joint's control size (cosmetic: the deform bones are
    #: hidden and the controls draw their shapes at their own size).
    BONE_SCALE = 0.2
    #: The hidden bone collection of the MCH bones -- each joint's share of the end
    #: control's solve; nothing but the solve's bake keys them.
    MCH_COLLECTION = "MCH"
    #: On the armature: the fingerprint of what its bake was made from and what it
    #: wrote (:meth:`ArticulatedRig._bake_fingerprint`) -- a save with neither changed
    #: keeps the bake instead of solving every frame again.
    BAKE_ATTR = "articulatedRigBake"

    # --------------------------------------------------------------- objects
    @staticmethod
    def _object(node):
        """*node* (an object or its name) as the object, or None."""
        import bpy

        if node is None:
            return None
        if isinstance(node, str):
            return bpy.data.objects.get(node)
        return node if isinstance(node, bpy.types.Object) else None

    @staticmethod
    def _is_bone(node) -> bool:
        """Whether *node* is a bone of some kind (pose, armature or edit bone)."""
        import bpy

        return isinstance(
            node, (bpy.types.PoseBone, bpy.types.Bone, bpy.types.EditBone)
        )

    @staticmethod
    def _items(nodes) -> list:
        """*nodes* as a list: a list, tuple or set as it is, anything else one item."""
        if nodes is None:
            return []
        return list(nodes) if isinstance(nodes, (list, tuple, set)) else [nodes]

    @classmethod
    def _owner(cls, node):
        """The object *node* is or belongs to: a bone's armature object."""
        import bpy

        if not cls._is_bone(node):
            return cls._object(node)
        owner = node.id_data
        if isinstance(owner, bpy.types.Object):
            return owner
        return next((o for o in bpy.data.objects if o.data == owner), None)

    @classmethod
    def _label(cls, node) -> str:
        if isinstance(node, str):
            return node
        return getattr(node, "name", None) or str(node)

    @staticmethod
    def _alive(obj) -> bool:
        """Whether *obj* is still in a scene: deleted in the UI, an object our pointers
        hold stays in the file with no collection."""
        try:
            return obj is not None and bool(obj.users_collection)
        except ReferenceError:
            return False

    @staticmethod
    def _scene():
        """The scene the window shows (window-independent)."""
        import bpy

        layer = CoreUtils._active_view_layer()
        return layer.id_data if layer is not None else bpy.context.scene

    @staticmethod
    def _update() -> None:
        """Evaluate the scene: drivers, bone poses, ``matrix_world``."""
        layer = CoreUtils._active_view_layer()
        if layer is not None:
            layer.update()

    @staticmethod
    def _lineage(obj) -> list:
        """*obj*'s ancestors, root first, ending with *obj*."""
        out = []
        while obj is not None:
            out.append(obj)
            obj = obj.parent
        return out[::-1]

    @classmethod
    def _common_parent(cls, objects: Sequence[Any]):
        """The deepest object every one of *objects* sits under; None: the world."""
        paths = [cls._lineage(o) for o in objects]
        shared: list = []
        for level in zip(*paths):
            if any(o != level[0] for o in level[1:]):
                break
            shared.append(level[0])
        # An object is not its own parent: stop above the shallowest one.
        shared = shared[: min(len(p) for p in paths) - 1]
        return shared[-1] if shared else None

    @staticmethod
    def _has_mesh(obj) -> bool:
        return obj.type == "MESH" or any(
            d.type == "MESH" for d in obj.children_recursive
        )

    @classmethod
    def _expand(cls, nodes) -> List[str]:
        """The parts *nodes* names: as given or -- one group picked -- the objects under
        it that carry geometry, descending through single-child wrappers (``GRP > LOC >
        asset``) to the level holding several. Names."""
        parts = [o for o in (cls._object(n) for n in cls._items(nodes)) if o]
        while len(parts) == 1:
            children = sorted(
                (c for c in parts[0].children if cls._has_mesh(c)), key=lambda o: o.name
            )
            if not children:
                break
            parts = children
        return [p.name for p in parts]

    # -------------------------------------------------------------- matrices
    @staticmethod
    def _plan(matrix) -> List[float]:
        """A ``mathutils.Matrix`` (column vectors) as the plan's 16 floats (row vectors:
        rows 0-2 the axes, row 3 the position -- ``ptk.ArticulationPlan``'s layout)."""
        return [float(matrix[c][r]) for r in range(4) for c in range(4)]

    @staticmethod
    def _mat(values: Sequence[float]):
        """The plan's 16 floats as a ``mathutils.Matrix``."""
        from mathutils import Matrix

        v = [float(x) for x in values]
        return Matrix([[v[c * 4 + r] for c in range(4)] for r in range(4)])

    @staticmethod
    def _np(matrix):
        """A matrix as a 4 x 4 float64 array (column vectors): Blender's are float32,
        and the plan's round trips (world to rig space and back) are exact in
        double."""
        import numpy as np

        return np.array([list(row) for row in matrix], dtype=np.float64)

    @classmethod
    def _inverse(cls, matrix) -> List[float]:
        """The inverse of *matrix*, in double, as the plan's 16 floats."""
        import numpy as np

        return cls._plan(np.linalg.inv(cls._np(matrix)))

    @classmethod
    def _home(cls, arm):
        """Where the armature stood when built: its world matrix without its own
        transform (a rig moved as a whole keeps the plan where it was) -- float64."""
        import numpy as np

        return cls._np(arm.matrix_world) @ np.linalg.inv(cls._np(arm.matrix_basis))

    # ---------------------------------------------------------------- shells
    @classmethod
    def _shells(cls, node) -> List[List[Tuple[float, float, float]]]:
        """World-space points of every connected piece of geometry under *node* (its own
        evaluated mesh and its descendants'), one list per shell -- a face's vertices
        are one shell, read without touching the selection or Edit Mode."""
        import numpy as np

        obj = cls._object(node)
        if obj is None:
            return []
        depsgraph = CoreUtils._evaluated_depsgraph()
        out: List[List[Tuple[float, float, float]]] = []
        for o in [obj] + list(obj.children_recursive):
            if o.type != "MESH":
                continue
            ev = o.evaluated_get(depsgraph)
            mesh = ev.to_mesh()
            try:
                count = len(mesh.vertices)
                if not count:
                    continue
                co = np.empty(count * 3, dtype=np.float32)
                mesh.vertices.foreach_get("co", co)
                m = cls._np(ev.matrix_world)
                world = co.reshape(-1, 3).astype(np.float64) @ m[:3, :3].T + m[:3, 3]
                corners = np.empty(len(mesh.loops), dtype=np.int32)
                mesh.loops.foreach_get("vertex_index", corners)
                starts = np.empty(len(mesh.polygons), dtype=np.int32)
                totals = np.empty(len(mesh.polygons), dtype=np.int32)
                mesh.polygons.foreach_get("loop_start", starts)
                mesh.polygons.foreach_get("loop_total", totals)
            finally:
                ev.to_mesh_clear()
            # Each corner joined to its face's first: a face's vertices are one shell.
            first = np.repeat(corners[starts], totals) if len(starts) else corners
            labels = np.asarray(
                ptk.MathUtils.connected_components(
                    count, np.column_stack((first, corners))
                )
            )
            for label in np.unique(labels):
                out.append([tuple(p) for p in world[labels == label].tolist()])
        return out

    @classmethod
    def _points(cls, nodes: Sequence[Any]) -> List[Tuple[float, float, float]]:
        return [p for n in nodes for s in cls._shells(n) for p in s]

    # ---------------------------------------------------------------- parts
    @classmethod
    def _capture(cls, obj) -> Dict[str, Any]:
        values: Dict[str, Any] = {}
        for attr in cls.PART_ATTRS:
            value = getattr(obj, attr)
            values[attr] = value if isinstance(value, (str, bool)) else list(value)
        values["matrix_parent_inverse"] = cls._plan(obj.matrix_parent_inverse)
        return values

    @classmethod
    def _restore(cls, obj, values: Dict[str, Any]) -> None:
        """Put *obj*'s captured state back (its parent is the caller's)."""
        obj.rotation_mode = values["rotation_mode"]  # first: it re-labels the Euler
        for attr in cls.PART_ATTRS:
            if attr not in ("parent_type", "parent_bone", "rotation_mode"):
                setattr(obj, attr, values[attr])
        obj.matrix_parent_inverse = cls._mat(values["matrix_parent_inverse"])

    @classmethod
    def _driven(cls, obj) -> List[str]:
        """What drives *obj*'s transform -- keys, a driver, a constraint."""
        import blendertk as btk  # the root: anim_utils ranks above rig_utils

        driven = [
            f"{fc.data_path}[{fc.array_index}]"
            for fc in btk.AnimUtils.get_fcurves([obj])
            if fc.data_path in cls.DRIVEN_PATHS
        ]
        anim = obj.animation_data
        driven += [
            f"{fc.data_path}[{fc.array_index}] driver"
            for fc in (anim.drivers if anim else ())
            if fc.data_path in cls.DRIVEN_PATHS
        ]
        driven += [f"constraint {c.name}" for c in obj.constraints if c.enabled]
        return driven

    # ---------------------------------------------------------------- bones
    @classmethod
    def _channel_path(cls, bone: str, channel: str) -> Tuple[str, int]:
        """The data path and index of *bone*'s *channel* (``rz`` -> rotation_euler[2])."""
        import bpy

        prop, index = cls.CHANNEL_PATHS[channel]
        return f'pose.bones["{bpy.utils.escape_identifier(bone)}"].{prop}', index

    @staticmethod
    def _bone_of_path(data_path: str) -> Optional[Tuple[str, str]]:
        """``(bone, property)`` of a ``pose.bones["..."].prop`` path -- or of a custom
        property's ``pose.bones["..."]["prop"]`` -- or None."""
        match = re.match(
            r'^pose\.bones\["((?:[^"\\]|\\.)*)"\](?:\.(\w+)|\["((?:[^"\\]|\\.)*)"\])$',
            data_path,
        )
        if not match:
            return None
        unescape = lambda text: re.sub(r"\\(.)", r"\1", text)  # noqa: E731
        prop = match.group(2) or unescape(match.group(3))
        return unescape(match.group(1)), prop

    @classmethod
    def _mch_bones(cls, arm) -> List[str]:
        """The names of *arm*'s MCH bones (its :attr:`MCH_COLLECTION`)."""
        data = getattr(arm, "data", None)
        collection = data.collections.get(cls.MCH_COLLECTION) if data else None
        return [b.name for b in collection.bones] if collection else []

    @classmethod
    def _bake_curves(cls, arm) -> list:
        """The F-curves on *arm*'s MCH bones: the end control's baked solve (nothing
        else keys them)."""
        import blendertk as btk  # the root: anim_utils ranks above rig_utils

        names = set(cls._mch_bones(arm))
        if not names:
            return []
        return [
            fc
            for fc in btk.AnimUtils.get_fcurves([arm])
            if (cls._bone_of_path(fc.data_path) or ("",))[0] in names
        ]

    @staticmethod
    def _collection(arm, name: str, visible: bool):
        """The armature's bone collection *name*, made on demand."""
        collection = arm.data.collections.get(name) or arm.data.collections.new(name)
        collection.is_visible = visible
        return collection

    @staticmethod
    def _limit(bone, kind: str):
        """*bone*'s rig limit constraint of *kind*, made on demand."""
        name = {
            "LIMIT_ROTATION": "Articulated Rig Rotation Limit",
            "LIMIT_LOCATION": "Articulated Rig Location Limit",
        }[kind]
        con = bone.constraints.get(name)
        if con is None:
            con = bone.constraints.new(kind)
            con.name = name
            con.owner_space = "LOCAL"
            con.use_transform_limit = True
        return con

    @staticmethod
    @contextlib.contextmanager
    def _step(name: str):
        """One undo step named *name* -- none when the body refuses with a
        ValueError, which every operation raises from its checks, before anything
        changed (the twin of Maya discarding an empty chunk)."""
        with CoreUtils.undo_chunk(name) as step:
            try:
                yield
            except ValueError:
                step.cancel()
                raise

    @staticmethod
    def _delete_object(obj) -> None:
        """Remove *obj* and its data when nothing else uses the data."""
        import bpy

        if obj is None:
            return
        data = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if isinstance(data, bpy.types.Curve) and not data.users:
            bpy.data.curves.remove(data)


class ArticulatedRig(ptk.LoggingMixin, _ArticulatedRigInternal):
    """One articulated rig in the scene, bound to its armature.

    Build one with :meth:`create` (from the parts, joints proposed by :meth:`analyze`
    unless given); find one again with :meth:`for_node` or :meth:`scene_rigs`.

    Parameters:
        armature: The rig's armature (``<name>_RIG``, an object or its name) carrying
            :attr:`DATA_ATTR`.

    Raises:
        ValueError: *armature* is not an articulated rig's, or carries a record copied
            from another rig (a Shift+D duplicate).
    """

    #: The plan the rig was built from, JSON on the armature object, in its space.
    DATA_ATTR = "articulatedRigData"
    #: ID pointers from the armature to every object the build made (the control
    #: shapes, by joint id): ownership by pointer, so a teardown deletes exactly those,
    #: however renamed.
    NODES_ATTR = "articulatedRigNodes"
    #: ID pointers from the armature to every part and each part's original parent, by
    #: the uuid the plan names it by -- Blender's stand-in for Maya's node UUIDs.
    PARTS_ATTR = "articulatedRigParts"
    #: On each scene holding a rig: ``group_uuid`` -> the rig's armature DATA. Shift+D
    #: copies the record and duplicates the data, so a copy's uuid maps to another
    #: armature's data and the copy is refused.
    REGISTRY_ATTR = "articulatedRigs"
    #: The parked-animation key of the end control (a joint id is a bone name, which
    #: never holds ``<``).
    END_KEY = ptk.ArticulationPlan.END_KEY
    #: What solves the end control: blendertk's handlers (``_solver_handler.py``), not
    #: a node in the file -- Maya's is an ``expression`` node. mayatk's
    #: ``LEGACY_SOLVER_PLUGIN`` (the plug-in an earlier Maya build solved with) has no
    #: Blender counterpart.
    SOLVER_TYPE = "handler"
    #: The end control's switches, custom properties on its bone: how much the solve
    #: adds over FK (0 = FK only), and whether its rotation turns a ball-mounted end
    #: link.
    BLEND_ATTR = "ikBlend"
    FOLLOW_ATTR = "followRotation"
    #: Each FK control of a rig with an end control hangs under an MCH bone (its name
    #: this suffix on the control's) at the joint's rest, carrying that joint's share
    #: of the solve: the controls ride their joints when the end control moves the
    #: arm, instead of staying where FK left them (Maya's IK group).
    IK_GROUP_SUFFIX = "_IK"
    #: The end control's colour (Maya's override colour 13, red), the margin its box
    #: keeps around the end link (a fraction of the link's largest extent) and its
    #: heavier line.
    END_COLOR: Tuple[float, float, float] = (1.0, 0.0, 0.0)
    END_MARGIN = 0.08
    END_LINE_WIDTH = 2.0
    #: The plan's schema (``ptk.ArticulationPlan``, the host-free half of this engine:
    #: the plan's checks, frames, record and edits).
    VERSION = ptk.ArticulationPlan.VERSION
    JOINT_TYPES = ptk.ArticulationAnalysis.JOINT_TYPES
    #: Control preset and its normal axis per joint type: a hinge's ring lies in the
    #: plane it turns in, a swivel's around the link, a slide's arrow in the arm's
    #: plane, along the travel.
    CONTROL_SHAPES: Dict[str, Tuple[str, str]] = {
        "hinge": ("circle", "z"),
        "swivel": ("circle", "x"),
        "universal": ("target", "y"),
        "ball": ("ball", "y"),
        "slide": ("two_way_arrow", "z"),
    }
    #: Control colours per joint type -- Maya's override colours (yellow 17, light blue
    #: 18, blue 6, green 14) as the bone colours they read as.
    CONTROL_COLORS: Dict[str, Tuple[float, float, float]] = {
        "hinge": (1.0, 1.0, 0.0),
        "swivel": (0.39, 0.86, 1.0),
        "universal": (0.39, 0.86, 1.0),
        "ball": (0.0, 0.0, 1.0),
        "slide": (0.0, 1.0, 0.0),
    }
    #: A control's size over its link's body radius.
    CONTROL_SCALE = 2.5
    #: How far a control riding a link's axis keeps clear of the geometry there, over
    #: that geometry's reach from the axis: a slide's arrow floats above its tubes, a
    #: swivel's ring around its housing.
    CLEARANCE = 1.25
    #: Passes a drag of the end control may take to land the held point
    #: (:meth:`_settle_end`): a ball-mounted end lands in none, a hinged end settles in
    #: two or three.
    END_DRAG_PASSES = 4
    #: Adjusting the pivots (:meth:`begin_adjust`): the armature's ID pointers to the
    #: handles out, the string each handle carries naming what it places (a joint id,
    #: or :attr:`END_KEY`), and a handle's name ending.
    ADJUST_ATTR = "articulatedRigAdjust"
    PIVOT_ATTR = "articulatedRigPivot"
    HANDLE_SUFFIX = "_pivot"
    #: A handle's axes' length over its joint's control size.
    HANDLE_SCALE = 2.0
    #: A handle closer than this to where it started moved nothing.
    ADJUST_EPS = ptk.ArticulationPlan.ADJUST_EPS

    def __init__(self, armature):
        arm = self._object(armature)
        if arm is None or arm.type != "ARMATURE" or self.DATA_ATTR not in arm.keys():
            raise ValueError(f"{armature!r} is not an articulated rig armature.")
        data = self.scene_data(arm) or {}
        self._group_uuid = data.get("group_uuid")
        if not self._claims(arm, self._group_uuid):
            # A Shift+D copy carries the record verbatim, parts and all: acting on it
            # would tear the ORIGINAL's parts out of their rig.
            raise ValueError(
                f"{arm.name!r} carries another rig's record (a duplicate?); rebuild "
                "it from its parts instead."
            )
        self._arm_name = arm.name
        if data.get("end"):
            # The first touch of a rig with an end control arms its solve (a headless
            # session has no UI handler to do it): Maya's expression is live in any
            # scene that holds it.
            SolverHandler.ensure_installed()

    # ================================================================ lookup
    @classmethod
    def _claims(cls, arm, group_uuid: Optional[str]) -> bool:
        """Whether *arm* is the rig *group_uuid* names in the scenes holding it: no
        registry entry (a rig appended from another file) claims nothing against it."""
        if not group_uuid:
            return True
        for scene in arm.users_scene:
            registry = scene.get(cls.REGISTRY_ATTR)
            if registry is None or group_uuid not in registry:
                continue
            if registry[group_uuid] != arm.data:
                return False
        return True

    @classmethod
    def _register(cls, arm, group_uuid: str) -> None:
        """Enter *arm* in the registry of every scene holding it, dropping entries
        whose armature data nothing else uses (a rig deleted in the UI)."""
        for scene in arm.users_scene:
            stored = scene.get(cls.REGISTRY_ATTR)
            registry = {
                k: v
                for k, v in (stored.items() if stored else ())
                if v is not None and v.users > 1
            }
            registry[group_uuid] = arm.data
            scene[cls.REGISTRY_ATTR] = registry

    @classmethod
    def _unregister(cls, arm, group_uuid: Optional[str]) -> None:
        for scene in arm.users_scene:
            registry = scene.get(cls.REGISTRY_ATTR)
            if registry is not None and group_uuid in registry:
                del registry[group_uuid]

    @property
    def group(self):
        """The rig's armature object. Found again by its record when renamed.

        Raises:
            RuntimeError: The rig is gone (torn down, or deleted).
        """
        import bpy

        arm = bpy.data.objects.get(self._arm_name) if self._arm_name else None
        if (
            arm is None
            or (self.scene_data(arm) or {}).get("group_uuid") != self._group_uuid
        ):
            arm = next(
                (
                    o
                    for o in bpy.data.objects
                    if o.type == "ARMATURE"
                    and self._alive(o)
                    and (self.scene_data(o) or {}).get("group_uuid") == self._group_uuid
                    and self._claims(o, self._group_uuid)
                ),
                None,
            )
            if arm is None:
                raise RuntimeError("The rig group no longer exists.")
            self._arm_name = arm.name
        return arm

    @property
    def spec(self) -> Dict[str, Any]:
        """The stamped plan (a fresh copy): name, links, joints, parts."""
        return self.scene_data(self.group) or {}

    @property
    def name(self) -> str:
        return self.spec.get("name") or self.group.name

    @classmethod
    def scene_data(cls, node) -> Optional[Dict[str, Any]]:
        """The :attr:`DATA_ATTR` plan on *node*, or None."""
        obj = cls._object(node)
        if obj is None or cls.DATA_ATTR not in obj.keys():
            return None
        try:
            data = json.loads(obj[cls.DATA_ATTR] or "")
        except (TypeError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def _stamp(self, spec: Dict[str, Any]) -> None:
        self.group[self.DATA_ATTR] = json.dumps(spec, sort_keys=True)

    def _nodes(self) -> Dict[str, Any]:
        """The objects the plan names, by uuid (parts and their original parents)."""
        stored = self.group.get(self.PARTS_ATTR)
        return {k: v for k, v in (stored.items() if stored else ()) if v is not None}

    def _uuid(self, obj) -> Optional[str]:
        """The uuid this rig names *obj* by, or None."""
        return next((k for k, v in self._nodes().items() if v == obj), None)

    @classmethod
    def scene_rigs(cls) -> List["ArticulatedRig"]:
        """Every articulated rig in the scene (duplicated records skipped)."""
        rigs = []
        for obj in cls._scene().objects:
            if obj.type != "ARMATURE" or cls.DATA_ATTR not in obj.keys():
                continue
            try:
                rigs.append(cls(obj))
            except ValueError:
                continue
        return rigs

    @classmethod
    def for_node(cls, node) -> Optional["ArticulatedRig"]:
        """The rig *node* belongs to -- its armature, a joint or control bone, a part
        riding a joint (or anything under one), or a part of the fixed root link -- or
        None."""
        obj = cls._owner(node)
        walk = obj
        while walk is not None:
            if walk.type == "ARMATURE" and cls.DATA_ATTR in walk.keys():
                try:
                    return cls(walk)
                except ValueError:
                    return None
            walk = walk.parent
        if obj is None:
            return None
        for rig in cls.scene_rigs():
            members = {m["uuid"] for link in rig.spec.get("links", []) for m in link}
            if any(v == obj for k, v in rig._nodes().items() if k in members):
                return rig
        return None

    @property
    def end_control(self):
        """The end control's bone (a ``PoseBone``), or None (built without one)."""
        spec = self.spec
        if not spec.get("end"):
            return None
        return self.group.pose.bones.get(self._end_name(spec))

    @property
    def solver(self):
        """What solves the end control -- blendertk's installed handler -- or None: no
        end control, or a session whose handlers are not installed, until
        :meth:`ensure_solver` installs them."""
        if self.end_control is None:
            return None
        return SolverHandler.handler()

    def ik_blend(self) -> float:
        """How much the end control's solve adds over FK: 1 places the end link on the
        end control, 0 leaves the FK pose (no end control, or no solve: 0)."""
        end = self.end_control
        if end is None or not self.solver:
            return 0.0
        return float(end.get(self.BLEND_ATTR, 1.0))

    # ============================================================== analysis
    @classmethod
    def analyze(cls, nodes, root=None, ordered: bool = False) -> Dict[str, Any]:
        """Propose a rig for *nodes*: one link per part, and how and where each joins
        the next.

        Parameters:
            nodes: The parts (objects carrying geometry, or groups of them), or one
                group holding them.
            root: The part that stays put; default the lowest (along Z).
            ordered: Take *nodes* as the chain, root first (a selection order),
                instead of walking the parts' contacts.

        Returns:
            A plan :meth:`create` builds: ``{"links": [[part name], ...], "joints":
            [{"link", "parent", "type", "position", "aim", "normal", "limits", "size",
            "reason"}, ...], "unreached": [part name, ...]}`` -- world space, link 0
            the root.
        """
        parts = cls._expand(nodes)
        if len(parts) < 2:
            raise ValueError("An articulated rig needs at least two parts.")
        return cls._propose([[p] for p in parts], root=root, ordered=ordered)

    @classmethod
    def _propose(
        cls, links: Sequence[Sequence[Any]], root=None, ordered: bool = False
    ) -> Dict[str, Any]:
        names = [[cls._label(n) for n in link] for link in links]
        facts = [
            {"name": link[0], "shells": [s for n in link for s in cls._shells(n)]}
            for link in names
        ]
        root_index = None
        if root is not None:
            root_name = cls._label(root)
            root_index = next(
                (i for i, link in enumerate(names) if root_name in link), None
            )
        proposal = ptk.ArticulationAnalysis.propose(
            facts,
            root=root_index,
            order=list(range(len(names))) if ordered else None,
            up=cls.UP,
        )
        return ptk.ArticulationPlan.from_proposal(proposal, names, cls.CONTROL_SCALE)

    # ================================================================= build
    @classmethod
    def create(
        cls,
        links: Sequence[Any],
        joints: Optional[Sequence[Dict[str, Any]]] = None,
        name: Optional[str] = None,
        parent=None,
        end_control: bool = True,
    ) -> "ArticulatedRig":
        """Build a rig -- one undo step.

        Parameters:
            links: The rigid links, root first: each an object (or name) or a list of
                them that move together. Link 0 stays put.
            joints: One per non-root link, in WORLD space, the shape :meth:`analyze`
                returns: ``{"link": i, "parent": j, "type": "hinge" | "swivel" |
                "universal" | "ball" | "slide", "position": [x, y, z], "aim": [...],
                "normal": [...], "limits": {"rz": [min, max], ...}, "weights": {...},
                "size": float}`` -- degrees for a rotation's limits, world units for a
                slide's; ``None`` for an unbounded side. None proposes them from the
                links' geometry.
            name: The rig's name (default: the parts' parent's, lowercase).
            parent: Where the armature goes (default: the parts' common parent, so the
                rig rides whatever carries the prop).
            end_control: Give the rig an end control on the link at the end of its
                longest chain (see :meth:`set_end_control`).

        Returns:
            The new rig.

        Raises:
            ValueError: A missing, repeated or driven part; fewer than two links;
                joints that do not join every link to the root.
        """
        with cls._step("Articulated Rig: Build"):
            return cls._create(links, joints, name, parent, end_control)

    @classmethod
    def _create(cls, links, joints, name, parent, end_control) -> "ArticulatedRig":
        resolved, missing = [], []
        for link in links:
            row = []
            for node in cls._items(link):
                obj = cls._object(node)
                if obj is None:
                    missing.append(cls._label(node))
                else:
                    row.append(obj)
            resolved.append(row)
        if missing:
            raise ValueError(f"Parts not found: {missing}.")
        if len(resolved) < 2 or not all(resolved):
            raise ValueError("An articulated rig needs at least two non-empty links.")
        cls._update()
        if joints is None:
            joints = cls._propose(resolved, ordered=True)["joints"]
        members = [o for link in resolved for o in link]
        home = (
            cls._object(parent) if parent is not None else cls._common_parent(members)
        )
        name = cls._unique_name(name or (home or members[0]).name.lower())
        inverse = (
            cls._inverse(home.matrix_world)
            if home is not None
            else [float(r == c) for r in range(4) for c in range(4)]
        )
        nodes: Dict[str, Any] = {}
        spec_links = []
        for link in resolved:
            row = []
            for obj in link:
                # One uuid per object: a part named twice is refused by the check.
                uid = next((k for k, v in nodes.items() if v == obj), None)
                if uid is None:
                    uid = _uuid.uuid4().hex
                    nodes[uid] = obj
                row.append({"uuid": uid, "name": obj.name})
            spec_links.append(row)
        spec = {
            "version": cls.VERSION,
            "name": name,
            "links": spec_links,
            "joints": [
                ptk.ArticulationPlan.plan_joint(j, resolved[j["link"]][0].name, inverse)
                for j in joints
            ],
        }
        cls._check(spec, nodes)
        if end_control:
            end = cls._default_end(spec, resolved, inverse)
            if end is not None:
                spec["end"] = end
        return cls._build(spec, home, nodes)

    @classmethod
    def _default_end(
        cls,
        spec: Dict[str, Any],
        links: Sequence[Sequence[Any]],
        inverse: Sequence[float],
        link: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """Where the end control goes on a plan AT REST: on *link* -- default the link
        at the end of the longest chain -- aligned with that link's joint and centred
        on its geometry (the magnifier's lens). ``{"part": uuid, "frame": [16
        floats]}``, the frame in the armature's space; None for the fixed root link.
        See ``ptk.ArticulationPlan.default_end``.

        Parameters:
            links: The plan's objects, per link.
            inverse: World to the armature's space (the plan's 16 floats).
        """
        return ptk.ArticulationPlan.default_end(
            spec, inverse, lambda i: cls._points(links[i]), link=link
        )

    @classmethod
    def _unique_name(cls, name: str) -> str:
        """*name*, numbered past any rig already using it: the joints a rig ships are
        named after it, and two rigs of one name would bind ambiguously."""
        import bpy

        taken = {r.spec.get("name") for r in cls.scene_rigs()}
        base, n = name, 1
        while name in taken or bpy.data.objects.get(f"{name}_RIG") is not None:
            n += 1
            name = f"{base}{n}"
        return name

    @classmethod
    def _check(cls, spec: Dict[str, Any], nodes: Dict[str, Any]) -> None:
        """Refuse a plan the build cannot keep: a part named twice or gone, a part
        something already drives, a link without exactly one joint, a joint the root
        does not reach. Orders the joints parent first (``ptk.ArticulationPlan.check``,
        the scene's parts checked here)."""
        ptk.ArticulationPlan.check(
            spec, parts=lambda plan: cls._check_parts(plan, nodes)
        )

    @classmethod
    def _check_parts(cls, spec: Dict[str, Any], nodes: Dict[str, Any]) -> None:
        """Refuse a plan whose parts are gone from the scene or driven."""
        lost = [
            m["name"]
            for link in spec["links"]
            for m in link
            if not cls._alive(nodes.get(m["uuid"]))
        ]
        if lost:
            raise ValueError(f"Parts no longer in the scene: {lost}.")
        for link in spec["links"]:
            for member in link:
                driven = cls._driven(nodes[member["uuid"]])
                if driven:
                    raise ValueError(
                        f"{member['name']} is driven ({', '.join(driven)}): a rigged "
                        "part is moved by its joint alone. Bake or remove its "
                        "animation first."
                    )

    @classmethod
    def _build(
        cls,
        spec: Dict[str, Any],
        home,
        nodes: Dict[str, Any],
        arm=None,
        group_uuid: Optional[str] = None,
    ) -> "ArticulatedRig":
        """Build *spec*: a new armature under *home*, or the bones of *arm* (a
        rebuild, which keeps the armature, its transform, its animation and the
        bones' names). The parts must stand at rest, where the plan measured them."""
        spec = copy.deepcopy(spec)
        if spec.get("end") and ptk.ArticulationPlan.end_joint(spec) is None:
            cls.logger.warning(
                "The end control's part rides no joint (the fixed root link?): built "
                "without it."
            )
        solved = ptk.ArticulationPlan.end_joint(spec) is not None
        with SolverHandler.suspended():
            if arm is None:
                arm = cls._new_armature(spec["name"], home, nodes, spec)
            spec["group_uuid"] = group_uuid or _uuid.uuid4().hex
            cls._update()
            names = cls._make_bones(arm, spec, solved)
            widgets = cls._make_controls(arm, spec, nodes, names, solved)
            cls._update()
            spec["parts"] = cls._bind(arm, spec, nodes, names)
            arm[cls.PARTS_ATTR] = {k: v for k, v in nodes.items() if v is not None}
            arm[cls.NODES_ATTR] = widgets
            arm[cls.DATA_ATTR] = json.dumps(spec, sort_keys=True)
            cls._register(arm, spec["group_uuid"])
            cls._update()
        rig = cls(arm)  # with an end control, arms the solve
        cls.refresh_export_metadata()
        return rig

    @classmethod
    def _new_armature(cls, name: str, home, nodes: Dict[str, Any], spec):
        """``<name>_RIG`` in the collection of *home* (else of the first part), under
        *home* at its origin: the rig space is *home*'s."""
        import bpy
        from mathutils import Matrix

        data = bpy.data.armatures.new(f"{name}_RIG")
        arm = bpy.data.objects.new(f"{name}_RIG", data)
        anchor = home if home is not None else nodes[spec["links"][0][0]["uuid"]]
        collections = list(getattr(anchor, "users_collection", ()) or ())
        (collections[0] if collections else cls._scene().collection).objects.link(arm)
        if home is not None:
            arm.parent = home
            arm.matrix_parent_inverse = Matrix.Identity(4)
        arm.show_in_front = True
        return arm

    @classmethod
    def _bone_names(
        cls, spec: Dict[str, Any], joint: Dict[str, Any]
    ) -> Tuple[str, str]:
        """``(deform bone, control bone)`` of *joint*."""
        base = f"{spec['name']}_{joint['id']}"
        return f"{base}_jnt", f"{base}{ptk.NamingConvention.affix('control')}"

    @classmethod
    def _end_name(cls, spec: Dict[str, Any]) -> str:
        """The end control's bone: ``<rig>_end<control affix>``."""
        return f"{spec['name']}_end{ptk.NamingConvention.affix('control')}"

    @classmethod
    def _make_bones(
        cls, arm, spec: Dict[str, Any], solved: bool = False
    ) -> Dict[int, Tuple[str, str]]:
        """The deform and control bones, at each joint's rest frame; the deform bones
        nest under each other, the controls under each other -- never across. With an
        end control (*solved*) each control hangs under its MCH bone (at the same
        rest, under the parent's control), and the end control's bone stands at the
        plan's ``end`` frame.

        Returns:
            ``{link: (deform bone, control bone)}``.
        """
        frames = ptk.ArticulationPlan.frames(spec)
        deform = cls._collection(arm, "DEF", visible=False)
        control = cls._collection(arm, "CTRL", visible=True)
        mechanism = None
        if solved:
            mechanism = cls._collection(arm, cls.MCH_COLLECTION, visible=False)
        elif arm.data.collections.get(cls.MCH_COLLECTION) is not None:
            arm.data.collections.remove(arm.data.collections[cls.MCH_COLLECTION])
        names: Dict[int, Tuple[str, str]] = {}

        def new(bones, name, frame, length, parent, collection, deforms=False):
            bone = bones.new(name)
            bone.head = (0.0, 0.0, 0.0)
            bone.tail = (0.0, length, 0.0)
            bone.matrix = frame  # keeps the length: X, Y, Z are the frame's
            bone.use_connect = False
            bone.use_deform = deforms
            if parent is not None:
                bone.parent = bones[parent]
            collection.assign(bone)
            return bone.name

        with RigUtils._active_mode(arm, "EDIT"):
            bones = arm.data.edit_bones
            for joint in spec["joints"]:
                frame = cls._mat(frames[joint["link"]])
                length = max((joint.get("size") or 1.0) * cls.BONE_SCALE, 1.0e-3)
                deform_name, control_name = cls._bone_names(spec, joint)
                above = names.get(joint["parent"], (None, None))
                deform_name = new(
                    bones, deform_name, frame, length, above[0], deform, True
                )
                parent = above[1]
                if solved:
                    parent = new(
                        bones,
                        control_name + cls.IK_GROUP_SUFFIX,
                        frame,
                        length,
                        parent,
                        mechanism,
                    )
                control_name = new(bones, control_name, frame, length, parent, control)
                names[joint["link"]] = (deform_name, control_name)
            if solved:
                joint = spec["joints"][ptk.ArticulationPlan.end_joint(spec)]
                length = max((joint.get("size") or 1.0) * cls.BONE_SCALE, 1.0e-3)
                frame = cls._mat(spec["end"]["frame"])
                new(bones, cls._end_name(spec), frame, length, None, control)
        return names

    @classmethod
    def _make_controls(
        cls,
        arm,
        spec: Dict[str, Any],
        nodes: Dict[str, Any],
        names: Dict[int, Tuple[str, str]],
        solved: bool = False,
    ) -> Dict[str, Any]:
        """Pose the bones: rotate orders, locks, the controls' shapes, colours and
        limits, each deform channel's driver -- and with an end control (*solved*) the
        MCH bones and the end control.

        Returns:
            The control shapes made, by joint id (the end control's by
            :attr:`END_KEY`).
        """
        import numpy as np

        frames = ptk.ArticulationPlan.frames(spec)
        home = cls._home(arm)
        widgets: Dict[str, Any] = {}
        for joint in spec["joints"]:
            channels, order = cls.JOINT_TYPES[joint["type"]]
            deform_name, control_name = names[joint["link"]]
            deform = arm.pose.bones[deform_name]
            control = arm.pose.bones[control_name]
            for bone in (deform, control):
                bone.rotation_mode = order.upper()  # Blender's XYZ applies X first
            deform.lock_location = deform.lock_rotation = deform.lock_scale = (
                True,
                True,
                True,
            )
            control.lock_location = tuple(c not in channels for c in ("tx", "ty", "tz"))
            control.lock_rotation = tuple(c not in channels for c in ("rx", "ry", "rz"))
            control.lock_scale = (True, True, True)
            widget = cls._widget(spec, joint)
            joint_world = home @ cls._np(cls._mat(frames[joint["link"]]))
            parts = [nodes[m["uuid"]] for m in spec["links"][joint["link"]]] + [
                nodes[m["uuid"]] for m in spec["links"][joint["parent"]]
            ]
            cls._clear(widget, joint["type"], np.linalg.inv(joint_world), parts)
            control.custom_shape = widget
            control.use_custom_shape_bone_size = False
            cls._paint(control, cls.CONTROL_COLORS[joint["type"]])
            cls._apply_limits(control, joint.get("limits") or {}, channels)
            mechanism = None
            if solved:
                mechanism = arm.pose.bones[control_name + cls.IK_GROUP_SUFFIX]
                mechanism.rotation_mode = order.upper()
                mechanism.lock_location = mechanism.lock_rotation = (True, True, True)
                mechanism.lock_scale = (True, True, True)
            cls._wire(arm, deform, control, channels, joint.get("limits"), mechanism)
            widgets[joint["id"]] = widget
        if solved:
            widgets[cls.END_KEY] = cls._make_end(arm, spec, nodes)
        return widgets

    @staticmethod
    def _paint(bone, color: Sequence[float]) -> None:
        """*bone*'s colour, lighter selected and active (Maya's highlight)."""
        bone.color.palette = "CUSTOM"
        bone.color.custom.normal = tuple(color)
        bone.color.custom.select = tuple(min(1.0, c + 0.35) for c in color)
        bone.color.custom.active = tuple(min(1.0, c + 0.6) for c in color)

    @classmethod
    def _make_end(cls, arm, spec: Dict[str, Any], nodes: Dict[str, Any]):
        """The end control: a box around the end link at the plan's ``end`` frame,
        keyable location (and rotation when the link hangs off a ball, whose turn it
        then drives), with the IK blend and -- a ball -- the follow switch.

        Returns:
            Its shape.
        """
        import numpy as np

        joint = spec["joints"][ptk.ArticulationPlan.end_joint(spec)]
        ball = joint["type"] == "ball"
        end = arm.pose.bones[cls._end_name(spec)]
        end.rotation_mode = "XYZ"
        widget = cls._unlinked(
            Controls.create("box", name=f"WGT_{spec['name']}_end", size=1.0)
        )
        # The box hugs the end link as it stands at rest, in the control's frame;
        # the pivot is its centre (the default frame's origin).
        frame = cls._home(arm) @ cls._np(cls._mat(spec["end"]["frame"]))
        to_frame = np.linalg.inv(frame)
        parts = [nodes[m["uuid"]] for m in spec["links"][joint["link"]]]
        world = np.asarray(cls._points(parts), dtype=np.float64).reshape(-1, 3)
        local = world @ to_frame[:3, :3].T + to_frame[:3, 3]
        lo, hi = local.min(axis=0), local.max(axis=0)
        margin = cls.END_MARGIN * float((hi - lo).max())
        lo, hi = lo - margin, hi + margin
        cls._reshape(
            widget,
            lambda x, y, z: tuple(
                float(a if v < 0.0 else b) for v, a, b in zip((x, y, z), lo, hi)
            ),
        )
        end.custom_shape = widget
        end.use_custom_shape_bone_size = False
        if hasattr(end, "custom_shape_wire_width"):  # Blender 4.2+
            end.custom_shape_wire_width = cls.END_LINE_WIDTH
        cls._paint(end, cls.END_COLOR)
        end[cls.BLEND_ATTR] = 1.0
        end.id_properties_ui(cls.BLEND_ATTR).update(
            min=0.0,
            max=1.0,
            soft_min=0.0,
            soft_max=1.0,
            default=1.0,
            description="How much the end control's solve adds over FK (0: FK only)",
        )
        if ball:
            end[cls.FOLLOW_ATTR] = True
            end.id_properties_ui(cls.FOLLOW_ATTR).update(
                default=True,
                description="The end link turns with the end control (else it keeps "
                "its own turn)",
            )
        end.lock_location = (False, False, False)
        end.lock_rotation = (not ball,) * 3
        end.lock_scale = (True, True, True)
        return widget

    @classmethod
    def _widget(cls, spec: Dict[str, Any], joint: Dict[str, Any]):
        """The control's shape: a ``Controls`` curve in the joint's frame, linked to no
        collection -- the bone's ``custom_shape`` keeps it, and nothing exports it."""
        shape, axis = cls.CONTROL_SHAPES[joint["type"]]
        return cls._unlinked(
            Controls.create(
                shape,
                name=f"WGT_{spec['name']}_{joint['id']}",
                size=max(joint.get("size") or 1.0, 1.0e-3),
                axis=axis,
            )
        )

    @staticmethod
    def _unlinked(widget):
        """*widget* out of every collection: a bone's ``custom_shape`` keeps it."""
        for collection in list(widget.users_collection):
            collection.objects.unlink(widget)
        return widget

    @staticmethod
    def _widget_points(widget) -> List[Tuple[float, float, float]]:
        return [tuple(p.co[:3]) for s in widget.data.splines for p in s.points]

    @staticmethod
    def _reshape(widget, fn) -> None:
        """Move every point of *widget*'s splines to ``fn(x, y, z)`` (object space)."""
        for spline in widget.data.splines:
            for point in spline.points:
                x, y, z = fn(*point.co[:3])
                point.co = (x, y, z, point.co[3])

    @classmethod
    def _clear(cls, widget, kind: str, to_joint, nodes: Sequence[Any]) -> None:
        """Keep a control that rides its link's axis out of the geometry there
        (*nodes*: the two links it joins, at rest; *to_joint*: world to the joint's
        frame): a slide's arrow moves up off the axis until it floats above the tubes,
        a swivel's ring widens around its housing. A hinge's ring, a universal's gimbal
        and a ball's sphere already stand around their joint."""
        import numpy as np

        if kind not in ("slide", "swivel"):
            return
        points = cls._widget_points(widget)
        span = max(abs(p[0]) for p in points)
        world = np.asarray(cls._points(nodes), dtype=np.float64).reshape(-1, 3)
        local = world @ to_joint[:3, :3].T + to_joint[:3, 3]
        near = local[np.abs(local[:, 0]) <= span]
        reach = float(np.hypot(near[:, 1], near[:, 2]).max()) if len(near) else 0.0
        clear = cls.CLEARANCE * reach
        if kind == "slide":
            low = min(p[1] for p in points)
            if clear > low:
                cls._reshape(widget, lambda x, y, z: (x, y + clear - low, z))
            return
        radius = max(math.hypot(p[1], p[2]) for p in points)
        if clear > radius > 0.0:
            k = clear / radius
            cls._reshape(widget, lambda x, y, z: (x, y * k, z * k))

    @classmethod
    def _apply_limits(
        cls, control, limits: Dict[str, Any], channels: Sequence[str]
    ) -> None:
        """The control's limits: *limits*' bounds on, every other side off."""
        for channel in channels:
            lo, hi = (limits or {}).get(channel) or (None, None)
            axis = channel[1]
            if channel.startswith("r"):
                con = cls._limit(control, "LIMIT_ROTATION")
                setattr(con, f"use_limit_{axis}", lo is not None or hi is not None)
                setattr(
                    con,
                    f"min_{axis}",
                    -cls.FREE_TURN if lo is None else math.radians(lo),
                )
                setattr(
                    con,
                    f"max_{axis}",
                    cls.FREE_TURN if hi is None else math.radians(hi),
                )
            else:
                con = cls._limit(control, "LIMIT_LOCATION")
                setattr(con, f"use_min_{axis}", lo is not None)
                setattr(con, f"min_{axis}", 0.0 if lo is None else float(lo))
                setattr(con, f"use_max_{axis}", hi is not None)
                setattr(con, f"max_{axis}", 0.0 if hi is None else float(hi))

    @classmethod
    def _wire(
        cls,
        arm,
        deform,
        control,
        channels: Sequence[str],
        limits: Optional[Dict[str, Any]] = None,
        mechanism=None,
    ) -> None:
        """Each deform channel a driver over the control's channel (``fk``) and -- with
        an end control -- the MCH bone's (``ik``): ``clamp(fk) + ik``, the control
        clamped to its *limits* as Maya's limited channel holds it (a channel typed
        past its limit stops there). Each variable reads the raw property: a
        transform-channel read decomposes the bone's rotation afresh -- the Euler
        triple nearest zero -- and a ball wound past it read as another triple, whose
        channel-wise sum with the solve's offset is another pose (measured: 180
        degrees off on a conformance ball). A SUM driver where no limit applies, else
        a simple expression (``max`` / ``min``) Blender evaluates without Python: the
        file poses with Auto Run off either way."""
        import bpy

        for channel in channels:
            path, index = cls._channel_path(deform.name, channel)
            arm.driver_remove(path, index)
            driver = arm.driver_add(path, index).driver
            prop, i = cls.CHANNEL_PATHS[channel]
            for name, bone in (("fk", control), ("ik", mechanism)):
                if bone is None:
                    continue
                var = driver.variables.new()
                var.name = name
                var.type = "SINGLE_PROP"
                target = var.targets[0]
                target.id_type = "OBJECT"
                target.id = arm
                escaped = bpy.utils.escape_identifier(bone.name)
                target.data_path = f'pose.bones["{escaped}"].{prop}[{i}]'
            # Last, once every variable stands (a script-built expression compiles
            # against the variables it finds).
            cls._limit_driver(driver, channel, (limits or {}).get(channel))

    @staticmethod
    def _limit_driver(driver, channel: str, bounds) -> None:
        """*driver* (a deform channel's) clamping its ``fk`` to *bounds* -- ``[min,
        max]`` in degrees (a rotation) or the rig's units, None an open side."""
        lo, hi = bounds or (None, None)
        convert = math.radians if channel.startswith("r") else float
        value = "fk"
        if hi is not None:
            value = f"min({convert(hi)!r}, {value})"
        if lo is not None:
            value = f"max({convert(lo)!r}, {value})"
        if value == "fk":
            driver.type = "SUM"
            return
        driver.type = "SCRIPTED"
        solved = any(v.name == "ik" for v in driver.variables)
        driver.expression = value + (" + ik" if solved else "")

    @classmethod
    def _bind(
        cls,
        arm,
        spec: Dict[str, Any],
        nodes: Dict[str, Any],
        names: Dict[int, Tuple[str, str]],
    ) -> List[Dict[str, Any]]:
        """Parent every moving part to its deform bone where it stands, its own
        transform untouched (the parent inverse takes the difference); capture what
        the teardown puts back. Each part's original parent joins *nodes*.

        Returns:
            The plan's ``parts``: ``{uuid, name, parent (uuid or None), attrs}``.
        """
        import numpy as np
        from mathutils import Matrix

        home = cls._home(arm)
        parts: List[Dict[str, Any]] = []
        for joint in spec["joints"]:
            bone = arm.data.bones[names[joint["link"]][0]]
            tail = (
                home
                @ cls._np(bone.matrix_local)
                @ cls._np(Matrix.Translation((0.0, bone.length, 0.0)))
            )
            to_tail = np.linalg.inv(tail)
            for member in spec["links"][joint["link"]]:
                obj = nodes[member["uuid"]]
                above = obj.parent
                parent = None
                if above is not None:
                    parent = next((k for k, v in nodes.items() if v == above), None)
                    if parent is None:
                        parent = _uuid.uuid4().hex
                        nodes[parent] = above
                parts.append(
                    {
                        "uuid": member["uuid"],
                        "name": obj.name,
                        "parent": parent,
                        "attrs": cls._capture(obj),
                    }
                )
                world = cls._np(obj.matrix_world)
                basis = cls._np(obj.matrix_basis)
                obj.parent_type = "OBJECT"
                obj.parent = arm
                obj.parent_type = "BONE"
                obj.parent_bone = bone.name
                obj.matrix_parent_inverse = Matrix(
                    (to_tail @ world @ np.linalg.pinv(basis)).tolist()
                )
        return parts

    # ============================================================= lifecycle
    def teardown(self) -> None:
        """Remove the rig -- one undo step -- and hand every part back exactly as it
        was: its original parent, parent type and bone, parent inverse and its own
        channels and locks (captured at the build). The animation on the controls goes
        with them."""
        with self._step("Articulated Rig: Remove"):
            self._teardown_scene()
            self.refresh_export_metadata()

    def _release(self, spec: Dict[str, Any], nodes: Dict[str, Any]) -> None:
        """Every part back on its original parent, as captured."""
        for part in reversed(spec.get("parts", [])):
            obj = nodes.get(part["uuid"])
            if not self._alive(obj):
                self.logger.warning(f"Part {part['name']} is gone; nothing to restore.")
                continue
            attrs = part["attrs"]
            obj.parent_type = "OBJECT"
            obj.parent = nodes.get(part.get("parent")) if part.get("parent") else None
            obj.parent_type = attrs["parent_type"]
            obj.parent_bone = attrs["parent_bone"]
            # After the parent: setting one resets the parent inverse.
            self._restore(obj, attrs)

    def _delete_widgets(self, arm) -> None:
        """Remove the control shapes the build made (:attr:`NODES_ATTR`)."""
        for widget in (arm.get(self.NODES_ATTR) or {}).values():
            self._delete_object(widget)

    def _unbuild(self, arm) -> None:
        """Remove what a build made on *arm*: the deform drivers, the control shapes,
        the end control's baked solve and every bone -- the armature, its animation
        and its transform stay."""
        import blendertk as btk  # the root: anim_utils ranks above rig_utils

        anim = arm.animation_data
        for curve in list(anim.drivers) if anim else []:
            if self._bone_of_path(curve.data_path) is not None:
                anim.drivers.remove(curve)
        baked = self._bake_curves(arm)
        if baked:
            # Derived: the next save bakes the rebuilt rig afresh.
            action, slot = btk.AnimUtils._animating(arm.animation_data)
            for curve in reversed(baked):
                btk.AnimUtils._remove_fcurve(action, slot, curve)
        self._delete_widgets(arm)
        with RigUtils._active_mode(arm, "EDIT"):
            for bone in list(arm.data.edit_bones):
                arm.data.edit_bones.remove(bone)

    def _teardown_scene(self) -> None:
        import bpy

        arm = self.group
        spec = self.spec
        # Parts FIRST: they ride the bones the armature's removal takes.
        self._release(spec, self._nodes())
        self._drop_handles(arm)
        self._delete_widgets(arm)
        self._unregister(arm, spec.get("group_uuid"))
        anim = arm.animation_data
        action = anim.action if anim else None
        data = arm.data
        bpy.data.objects.remove(arm, do_unlink=True)
        if action is not None and not action.users and not action.use_fake_user:
            bpy.data.actions.remove(action)
        if data is not None and not data.users:
            bpy.data.armatures.remove(data)
        self._update()

    def rebuild(self, spec: Optional[Dict[str, Any]] = None) -> "ArticulatedRig":
        """Build the rig again from *spec* (default: its own plan) -- one undo step --
        carrying the animation across: the armature and the control bones' names stay,
        so each control channel's F-curve lands on the rebuilt control of the same
        joint id and channel, and an unkeyed channel keeps its value. A channel the new
        plan no longer has loses its curve, with a warning.

        This instance follows the rig; it is also returned.
        """
        with self._step("Articulated Rig: Rebuild"):
            return self._rebuild(spec)

    def _rebuild(self, spec: Optional[Dict[str, Any]] = None) -> "ArticulatedRig":
        return self._replace(spec or self.spec, self._park())

    def _replace(
        self, spec: Dict[str, Any], parked: Dict[Tuple[str, str], Any]
    ) -> "ArticulatedRig":
        """Rebuild *spec* on this rig's armature and land the *parked* values on it.
        The plan is checked FIRST, while the rig still stands: a plan the build would
        refuse leaves it standing, its pose put back."""
        arm = self.group
        old = self.spec
        nodes = self._nodes()
        plan = {
            k: copy.deepcopy(v)
            for k, v in spec.items()
            if k not in ("parts", "group_uuid")
        }
        try:
            self._check(plan, nodes)
        except ValueError:
            self._unpark(parked)
            raise
        with SolverHandler.suspended():  # the bones are half-built in between
            self._release(old, nodes)
            self._unbuild(arm)
            self._update()
            self._build(plan, None, nodes, arm=arm, group_uuid=old.get("group_uuid"))
            self._unpark(parked, old)
        self._solve_now()
        return self

    def _park(self) -> Dict[Tuple[str, str], Any]:
        """Each control channel's value as it stands, by ``(joint id, channel)`` --
        what a rebuild puts back on a channel no curve drives -- and the end control's
        channels and switches, by ``(END_KEY, channel or switch)``."""
        spec = self.spec
        parked: Dict[Tuple[str, str], Any] = dict(
            zip(ptk.ArticulationPlan.slots(spec), self.fk_state())
        )
        end = self.end_control
        if end is not None:
            for channel, (prop, index) in self.CHANNEL_PATHS.items():
                parked[(self.END_KEY, channel)] = float(getattr(end, prop)[index])
            for switch in (self.BLEND_ATTR, self.FOLLOW_ATTR):
                if switch in end.keys():
                    parked[(self.END_KEY, switch)] = end[switch]
        return parked

    def _unpark(
        self,
        parked: Dict[Tuple[str, str], Any],
        old: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Land *parked* values on the rig's controls and -- rebuilt from the plan
        *old* -- drop the curves of the control channels this plan no longer has, and
        the end control's when it has none now (a warning names the ones *old*
        animated)."""
        import blendertk as btk  # the root: anim_utils ranks above rig_utils

        spec = self.spec
        arm = self.group
        live = set(ptk.ArticulationPlan.slots(spec))
        self._write(
            [(k, v) for k, v in parked.items() if k in live], key=False, spec=spec
        )
        self._unpark_end(parked)
        if old is None:
            self._solve_now()
            return
        owners = {self._bone_names(old, j)[1]: j["id"] for j in old.get("joints", [])}
        if old.get("end") and self.end_control is None:
            owners[self._end_name(old)] = self.END_KEY
        kept = {
            self._channel_path(self._bone_names(spec, j)[1], channel)
            for j in spec["joints"]
            for channel in self.JOINT_TYPES[j["type"]][0]
        }
        action, slot = btk.AnimUtils._animating(arm.animation_data)
        dropped = []
        for curve in reversed(btk.AnimUtils.get_fcurves([arm])):
            found = self._bone_of_path(curve.data_path)
            if found is None or found[0] not in owners:
                continue
            if (curve.data_path, curve.array_index) in kept:
                continue
            channel = next(
                (
                    ch
                    for ch, (prop, i) in self.CHANNEL_PATHS.items()
                    if prop == found[1] and i == curve.array_index
                ),
                found[1],  # a switch of the end control
            )
            if (owners[found[0]], channel) in parked:
                dropped.append(f"{owners[found[0]]}.{channel}")
            btk.AnimUtils._remove_fcurve(action, slot, curve)
        if dropped:
            self.logger.warning(
                f"Animation dropped with its channel: {', '.join(reversed(dropped))}."
            )
        self._update()

    def _unpark_end(self, parked: Dict[Tuple[str, str], Any]) -> None:
        """The end control's parked channels (those it leaves free) and switches back
        on it, when the rig still has one."""
        end = self.end_control
        if end is None:
            return
        for (owner, attr), value in parked.items():
            if owner != self.END_KEY:
                continue
            if attr in self.CHANNEL_PATHS:
                prop, index = self.CHANNEL_PATHS[attr]
                locks = end.lock_location if prop == "location" else end.lock_rotation
                if not locks[index]:
                    getattr(end, prop)[index] = value
            elif attr in end.keys():
                end[attr] = value

    def _to_rest(self) -> None:
        """Every control at rest, unkeyed: FK at 0 and the end control on its rest
        frame -- the pose a plan is measured in. The MCH bones are zeroed too: the
        solve at rest adds nothing, handlers installed or not."""
        self.set_state([0.0] * len(self._slots()), key=False)
        end = self.end_control
        if end is None:
            return
        end.location = (0.0, 0.0, 0.0)
        end.rotation_euler = (0.0, 0.0, 0.0)
        end.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
        end.rotation_axis_angle = (0.0, 0.0, 1.0, 0.0)
        self._write_offsets([0.0] * len(self._slots()), self.spec)
        self._update()

    # ============================================================ post-rig
    def insert_joint(
        self, members, joint_type: Optional[str] = None, **fields
    ) -> "ArticulatedRig":
        """Split a link: *members* leave the link they ride and get a joint of their
        own, hung off that link -- one undo step. What the new joint is -- a slide for a
        tube inside a tube -- comes from the geometry unless *joint_type* (and any of
        :meth:`create`'s joint fields, world space) says otherwise.

        The joints that hung off the split link and sit nearer *members* move onto the
        new joint, so the chain beyond rides it. The new joint is at 0, which changes no
        pose: every key already made still holds. With an end control it joins the
        solve at weight 0 (unless *weights* says otherwise), so a keyed IK pose holds
        too.

        Returns:
            This rig, rebuilt.

        Raises:
            ValueError: *members* are not all in one link, or are the whole of it.
        """
        with self._step("Articulated Rig: Insert Joint"):
            return self._insert_joint(members, joint_type, **fields)

    def _insert_joint(self, members, joint_type, **fields) -> "ArticulatedRig":
        spec = self.spec
        objs, missing = [], []
        for node in self._items(members):
            obj = self._object(node)
            (objs if obj is not None else missing).append(
                obj if obj is not None else self._label(node)
            )
        if missing:
            raise ValueError(f"Not in the scene: {missing}.")
        nodes = self._nodes()
        moved = {self._uuid(o) for o in objs}
        host, keep, split = ptk.ArticulationPlan.split_link(spec, moved)

        parked = self._park()
        try:
            self._to_rest()  # planned at rest
            keep_nodes = [nodes[m["uuid"]] for m in keep]
            split_nodes = [nodes[m["uuid"]] for m in split]
            proposal = self._propose([keep_nodes, split_nodes], ordered=True)["joints"][
                0
            ]
            proposal.update({k: v for k, v in fields.items() if v is not None})
            if joint_type:
                proposal["type"] = joint_type
            proposal.update(link=len(spec["links"]), parent=host)
            world = self.group.matrix_world
            joint = ptk.ArticulationPlan.plan_joint(
                proposal, split_nodes[0].name, self._inverse(world)
            )
            if spec.get("end") and not fields.get("weights"):
                # Free to move, the end control's solve would use the new joint and
                # reshape every keyed IK pose: it joins the solve still, until it is
                # given some give.
                joint["weights"] = {c: 0.0 for c in self.JOINT_TYPES[joint["type"]][0]}
                self.logger.info(
                    f"{joint['id']} joins the end control's solve at give 0, so the IK "
                    "animation holds; raise its give to let the end control move it."
                )

            # The split, and the joints beyond it that the split-off parts now carry.
            ptk.ArticulationPlan.insert_joint(
                spec,
                moved,
                joint,
                self._plan(world),
                self._points(keep_nodes),
                self._points(split_nodes),
            )
        except Exception:
            # Nothing torn down yet: the rig takes its pose back.
            self._unpark(parked)
            raise
        return self._replace(spec, parked)

    def remove_joint(self, joint_id: str) -> "ArticulatedRig":
        """Fold *joint_id*'s link back into the link it hangs off -- one undo step: its
        parts ride that joint again, the joints beyond it move up with them, and its own
        animation is dropped (with a warning).

        Returns:
            This rig, rebuilt.
        """
        with self._step("Articulated Rig: Remove Joint"):
            return self._rebuild(ptk.ArticulationPlan.remove_joint(self.spec, joint_id))

    def edit_joint(self, joint_id: str, **fields) -> "ArticulatedRig":
        """Change a joint's ``type``, ``position``, ``aim`` or ``normal`` (world space),
        or its ``limits`` / ``weights``, and rebuild with the animation carried across
        -- one undo step. A retyped joint keeps the curves of the channels both types
        share."""
        with self._step("Articulated Rig: Edit Joint"):
            spec = self.spec
            world = self._plan(self.group.matrix_world)
            return self._rebuild(
                ptk.ArticulationPlan.edit_joint(spec, joint_id, world, **fields)
            )

    def set_end_control(self, enabled: bool = True, part=None) -> "ArticulatedRig":
        """Give the rig an end control, move it to another link, or drop it -- one undo
        step.

        The end control is an IK target: move it and the chain from the root moves the
        end link there; rotate it and a ball-mounted end link turns with it. Its
        ``ikBlend`` fades the solve over FK (0 = the FK pose) and ``followRotation``
        lets the end link keep its own turn instead. Placed at rest on *part*'s link
        (default: the link at the end of the longest chain), aligned with that link's
        joint and centred on its geometry. Dropping it drops its animation (with a
        warning).

        Returns:
            This rig, rebuilt with the animation carried across.

        Raises:
            ValueError: *part* is not a part of this rig, or rides the fixed root link.
        """
        with self._step("Articulated Rig: End Control"):
            spec = self.spec
            if not enabled:
                spec.pop("end", None)
                return self._rebuild(spec)
            link = None
            if part is not None:
                link = ptk.ArticulationPlan.link_of(
                    spec, self._uuid(self._object(part))
                )
                if link is None:
                    raise ValueError(
                        f"{self._label(part)} is not a part of rig {self.name!r}."
                    )
            parked = self._park()
            nodes = self._nodes()
            try:
                self._to_rest()
                links = [[nodes[m["uuid"]] for m in row] for row in spec["links"]]
                end = self._default_end(
                    spec, links, self._inverse(self.group.matrix_world), link=link
                )
                if end is None:
                    raise ValueError(
                        f"{self._label(part)} rides the fixed root link: nothing to "
                        "move."
                    )
            except Exception:
                self._unpark(parked)
                raise
            spec["end"] = end
            return self._replace(spec, parked)

    def set_limits(
        self,
        joint_id: str,
        channel: str,
        minimum: Optional[float],
        maximum: Optional[float],
    ) -> None:
        """Bound one channel (degrees, or a slide's units in the rig's space); None
        frees that side -- one undo step. No rebuild: the control, the solve and the
        record change in place."""
        with self._step("Articulated Rig: Set Limits"):
            spec = self.spec
            joint = ptk.ArticulationPlan.set_limits(
                spec, joint_id, channel, minimum, maximum
            )
            self._stamp(spec)
            self._apply_limits(self.control(joint_id), joint["limits"], [channel])
            path, index = self._channel_path(self.joint(joint_id).name, channel)
            curve = self.group.animation_data.drivers.find(path, index=index)
            if curve is not None:
                self._limit_driver(curve.driver, channel, joint["limits"].get(channel))
            self._update()
            self._solve_now()
            self.refresh_export_metadata()

    def set_limit_from_pose(self, joint_id: str, channel: str, side: str) -> float:
        """Take the channel's current value -- the pose as it stands, the end control's
        solve included -- as its *side* (``"min"`` or ``"max"``) limit: pose the part
        to where it physically stops, then call this. Returns the value taken."""
        value = self.channel_value(joint_id, channel)
        lo, hi = ptk.ArticulationPlan.side_limits(
            self.spec, joint_id, channel, side, value
        )
        self.set_limits(joint_id, channel, lo, hi)
        return value

    def set_weight(
        self, joint_id: str, weight: float, channel: Optional[str] = None
    ) -> None:
        """How readily *joint_id* moves when the end control or a grab pulls the rig,
        against the other joints: 2 twice as readily as 1, 0 not at all -- every
        channel of the joint, or just *channel*. The runtimes' hand grab reads the same
        weights, and the end control's solve takes them at once. No rebuild; one undo
        step."""
        with self._step("Articulated Rig: Set Weight"):
            spec = self.spec
            ptk.ArticulationPlan.set_weight(spec, joint_id, weight, channel)
            self._stamp(spec)
            self._solve_now()
            self.refresh_export_metadata()

    # ================================================================ adjust
    #: On each handle: its matrix as placed. Blender's float32 channels round a
    #: pivot by more than :attr:`ADJUST_EPS`, so a handle still on what it was placed
    #: at is no move, whatever that rounding.
    _PLACED_ATTR = "articulatedRigPivotPlaced"

    @property
    def adjusting(self) -> bool:
        """Whether the rig's pivots are out for adjusting (:meth:`begin_adjust`)."""
        return bool(self.adjust_handles())

    def adjust_handles(self) -> Dict[str, Any]:
        """The pivot handles while adjusting (Empties), by what each places: a joint
        id, or :attr:`END_KEY` for the end control's."""
        stored = self.group.get(self.ADJUST_ATTR)
        return {
            key: handle
            for key, handle in (stored.items() if stored else ())
            if self._alive(handle)
        }

    def begin_adjust(self) -> Dict[str, Any]:
        """Put the rig's pivots out for adjusting -- one undo step: a handle where every
        joint turns -- its rest frame, X along the part, Z a hinge's axis -- riding
        the deform bone of the part the joint hangs off, and one where the end
        control sits, riding the end part. Move and turn them, then
        :meth:`end_adjust`; until then nothing changes, and the animation plays on.

        Returns:
            The handles (:meth:`adjust_handles`).
        """
        if self.adjusting:
            return self.adjust_handles()
        with self._step("Articulated Rig: Adjust Pivots"):
            arm = self.group
            spec = self.spec
            pivots = ptk.ArticulationPlan.pivots(spec)
            joints = {j["link"]: j for j in spec["joints"]}
            handles: Dict[str, Any] = {}
            for joint in spec["joints"]:
                above = joints.get(joint["parent"])
                handles[joint["id"]] = self._make_handle(
                    arm,
                    joint["id"],
                    pivots[joint["id"]],
                    self.joint(above["id"]).name if above else None,
                    self.HANDLE_SCALE * max(joint.get("size") or 1.0, 1.0e-3),
                )
            index = ptk.ArticulationPlan.end_joint(spec)
            if index is not None and self.end_control is not None:
                handles[self.END_KEY] = self._make_handle(
                    arm,
                    self.END_KEY,
                    pivots[self.END_KEY],
                    self.joint(spec["joints"][index]["id"]).name,
                    0.35 * self._end_extent(world=False),
                )
            arm[self.ADJUST_ATTR] = handles
            self._update()
        return self.adjust_handles()

    def _make_handle(self, arm, key: str, local, carrier: Optional[str], size: float):
        """One pivot handle: an arrows Empty drawn in front, at *local* (the plan's 16
        floats) in the frame of *carrier* -- the deform bone it rides (bone-parented,
        its parent inverse taking out the tail the bone parents from), or the rig
        space itself for the fixed root link's joints -- its scale locked."""
        import bpy
        from mathutils import Matrix

        label = "end" if key == self.END_KEY else key
        handle = bpy.data.objects.new(f"{self.name}_{label}{self.HANDLE_SUFFIX}", None)
        collections = list(arm.users_collection) or [self._scene().collection]
        collections[0].objects.link(handle)
        handle.empty_display_type = "ARROWS"
        handle.empty_display_size = max(size, 1.0e-3)
        handle.show_in_front = True
        handle[self.PIVOT_ATTR] = key
        handle.parent = arm
        if carrier is not None:
            handle.parent_type = "BONE"
            handle.parent_bone = carrier
            length = arm.data.bones[carrier].length
            # After the parent: setting one resets the parent inverse.
            handle.matrix_parent_inverse = Matrix.Translation((0.0, -length, 0.0))
        else:
            handle.matrix_parent_inverse = Matrix.Identity(4)
        handle.rotation_mode = "XYZ"
        handle.matrix_basis = self._mat(local)
        handle.lock_scale = (True, True, True)
        handle[self._PLACED_ATTR] = self._plan(handle.matrix_basis)
        return handle

    def end_adjust(self, apply: bool = True) -> bool:
        """Lock the pivots again -- one undo step: the rig rebuilt with every joint
        turning where its handle stands and the end control where its handle put it,
        the animation carried across (each channel's keys now turn the part about
        its new pivot) -- or, *apply* False, the handles dropped and nothing
        changed.

        Returns:
            Whether the rig was rebuilt (False: no handle moved, or not applied).
        """
        arm = self.group
        if self.ADJUST_ATTR not in arm.keys():
            return False
        with self._step("Articulated Rig: Apply Pivots"):
            spec = self.spec
            handles = self.adjust_handles()
            adjusted = self._adjusted(spec, handles) if apply else None
            self._drop_handles(arm)
            if adjusted is None:
                self._update()
                return False
            before = (spec.get("end") or {}).get("frame")
            after = (adjusted.get("end") or {}).get("frame")
            moved_end = bool(before and after) and any(
                abs(a - b) > self.ADJUST_EPS for a, b in zip(before, after)
            )
            end = self.end_control
            if moved_end and end is not None and self._keyed(end):
                self.logger.warning(
                    f"{self.name}: the end control's keys are relative to its frame; "
                    "they now play about the adjusted one."
                )
            self._rebuild(adjusted)
            return True

    def _adjusted(
        self, spec: Dict[str, Any], handles: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """*spec* with each joint's rest frame and the end control's where the handles
        put them, or None when none moved (``ptk.ArticulationPlan.adjusted``): a
        handle rides what its joint hangs off, so its local matrix is that joint's
        rest frame there. A handle still on what it was placed at is left out, so
        its float32 rounding moves nothing."""
        moved = {}
        for key, handle in handles.items():
            now = self._plan(handle.matrix_basis)
            placed = list(handle.get(self._PLACED_ATTR) or ())
            if len(placed) == 16 and all(
                abs(a - b) <= 1e-9 for a, b in zip(now, placed)
            ):
                continue
            moved[key] = now
        return ptk.ArticulationPlan.adjusted(spec, moved, eps=self.ADJUST_EPS)

    def _drop_handles(self, arm) -> None:
        """Remove the pivot handles (and the keys an auto key gave one)."""
        import bpy

        for handle in self.adjust_handles().values():
            anim = handle.animation_data
            action = anim.action if anim else None
            bpy.data.objects.remove(handle, do_unlink=True)
            if action is not None and not action.users and not action.use_fake_user:
                bpy.data.actions.remove(action)
        if self.ADJUST_ATTR in arm.keys():
            del arm[self.ADJUST_ATTR]

    def _keyed(self, bone) -> bool:
        """Whether any curve keys *bone*."""
        import blendertk as btk  # the root: anim_utils ranks above rig_utils

        return any(
            (self._bone_of_path(fc.data_path) or ("",))[0] == bone.name
            for fc in btk.AnimUtils.get_fcurves([bone.id_data])
        )

    def _end_extent(self, world: bool = True) -> float:
        """The diagonal of the end control's box, in world units (or the rig's)."""
        end = self.end_control
        shape = end.custom_shape if end is not None else None
        if shape is None:
            return 0.0
        from mathutils import Vector

        to = end.bone.matrix_local @ end.matrix_basis
        if world:
            to = self.group.matrix_world @ to
        points = [to @ Vector(p.co[:3]) for s in shape.data.splines for p in s.points]
        lo = [min(p[i] for p in points) for i in range(3)]
        hi = [max(p[i] for p in points) for i in range(3)]
        return math.dist(lo, hi)

    # ============================================================== controls
    def joint_ids(self) -> List[str]:
        """The joints' ids -- each its link's first part -- parent first."""
        return [j["id"] for j in self.spec.get("joints", [])]

    def joint_id_of(self, node) -> Optional[str]:
        """The id of the joint *node* is -- its deform or control bone -- or rides (a
        part, or anything under one); None for the fixed root link's."""
        spec = self.spec
        leaf = self._label(node)
        for joint in spec.get("joints", []):
            if leaf in self._bone_names(spec, joint):
                return joint["id"]
        if self._is_bone(node):
            return None
        try:
            return spec["joints"][self._joint_of(node)]["id"]
        except ValueError:
            return None

    def _bone(self, name: str):
        bone = self.group.pose.bones.get(name)
        if bone is None:
            raise KeyError(f"No bone {name!r} in rig {self.name!r}.")
        return bone

    def joint(self, joint_id: str):
        """The deform bone (a ``PoseBone``) of *joint_id* -- the joint that ships."""
        spec = self.spec
        return self._bone(self._bone_names(spec, self._entry(spec, joint_id))[0])

    def control(self, joint_id: str):
        """The FK control bone (a ``PoseBone``) of *joint_id*."""
        spec = self.spec
        return self._bone(self._bone_names(spec, self._entry(spec, joint_id))[1])

    @staticmethod
    def _entry(spec: Dict[str, Any], joint_id: str) -> Dict[str, Any]:
        """The plan's joint *joint_id*, or a stand-in carrying the id (the bone lookup
        then raises KeyError naming it)."""
        return next(
            (j for j in spec.get("joints", []) if j["id"] == joint_id),
            {"id": joint_id},
        )

    def controls(self) -> list:
        """Every control of the rig: the FK controls parent first, then the end
        control."""
        out = [self.control(joint_id) for joint_id in self.joint_ids()]
        end = self.end_control
        return out + [end] if end is not None else out

    def _slots(self) -> List[Tuple[Any, str]]:
        """``(control bone, channel)`` per state slot, in the model's order."""
        return [
            (self.control(joint_id), channel)
            for joint_id, channel in ptk.ArticulationPlan.slots(self.spec)
        ]

    def state(self, slots: Optional[Sequence[Tuple[Any, str]]] = None) -> List[float]:
        """The rig's pose, in :meth:`model` state order: each channel's FK value plus
        what the end control's solve adds -- what the joints show. Computed from the
        model over the inputs as they stand (:meth:`_ik_offsets`), not read back off
        the bones."""
        spec = self.spec
        slots = slots or self._slots()
        values = self.fk_state(slots)
        if not self.solver:
            return values
        inputs = self._solve_inputs(self.group, spec)
        if inputs is None:
            return values
        offsets = self._ik_offsets(*inputs, spec=spec)
        index = {k: i for i, k in enumerate(ptk.ArticulationPlan.slots(spec))}
        ids = {self._bone_names(spec, j)[1]: j["id"] for j in spec["joints"]}
        return [
            value + offsets[index[(ids[bone.name], channel)]]
            for (bone, channel), value in zip(slots, values)
        ]

    def fk_state(
        self, slots: Optional[Sequence[Tuple[Any, str]]] = None
    ) -> List[float]:
        """The FK controls' channel values, in :meth:`model` state order: degrees for a
        rotation, the rig's units for a slide -- each as its limit leaves it (the value
        its joint shows: a channel typed past its limit stops there, as in Maya)."""
        spec = self.spec
        limits = {
            self._bone_names(spec, j)[1]: j.get("limits") or {}
            for j in spec.get("joints", [])
        }
        out = []
        for bone, channel in slots or self._slots():
            prop, index = self.CHANNEL_PATHS[channel]
            value = float(getattr(bone, prop)[index])
            value = math.degrees(value) if channel.startswith("r") else value
            bounds = limits.get(bone.name, {}).get(channel) or (None, None)
            out.append(self._clamped(value, *bounds))
        return out

    @staticmethod
    def _clamped(value: float, lo: Optional[float], hi: Optional[float]) -> float:
        """*value* inside ``[lo, hi]`` (None: that side open)."""
        value = float(value)
        if lo is not None and value < lo:
            return float(lo)
        if hi is not None and value > hi:
            return float(hi)
        return value

    def channel_value(self, joint_id: str, channel: str) -> float:
        """One channel as the pose stands (FK plus the end control's solve)."""
        slots = ptk.ArticulationPlan.slots(self.spec)
        return self.state()[slots.index((joint_id, channel))]

    def set_state(
        self,
        values: Sequence[float],
        key: Optional[bool] = None,
        slots: Optional[Sequence[Tuple[Any, str]]] = None,
    ) -> None:
        """Set the FK controls to *values* (model state order: degrees, the rig's
        units), each inside its limits as Maya's limited channels hold it, keyed when
        *key* (default: Blender's auto keying). With the end control on, FK is the
        solve's seed: the end link stays on the end control and the chain reshapes
        under it."""
        spec = self.spec
        if slots is None:
            pairs = list(zip(ptk.ArticulationPlan.slots(spec), values))
        else:
            ids = {self._bone_names(spec, j)[1]: j["id"] for j in spec["joints"]}
            pairs = [((ids[b.name], ch), v) for (b, ch), v in zip(slots, values)]
        self._write(pairs, key=key, spec=spec)

    def _write(self, pairs, key: Optional[bool], spec: Dict[str, Any]) -> None:
        """``((joint id, channel), value)`` onto the controls, clamped to the limits."""
        scene = self._scene()
        if key is None:
            key = bool(scene.tool_settings.use_keyframe_insert_auto)
        limits = {j["id"]: j.get("limits") or {} for j in spec.get("joints", [])}
        for (joint_id, channel), value in pairs:
            bounds = limits.get(joint_id, {}).get(channel) or (None, None)
            value = self._clamped(value, *bounds)
            bone = self.control(joint_id)
            prop, index = self.CHANNEL_PATHS[channel]
            getattr(bone, prop)[index] = (
                math.radians(value) if channel.startswith("r") else value
            )
            if key:
                bone.keyframe_insert(
                    prop, index=index, frame=scene.frame_current, group=bone.name
                )
        self._update()

    # ============================================================== solve
    #: Plan -> what its end control's solve needs (the model, the end joint, the
    #: pivot and turn): built once per plan, the handlers asking every frame.
    _contexts: Dict[str, Dict[str, Any]] = {}

    @classmethod
    def install(cls) -> bool:
        """Install the end controls' solve for this session: blendertk's persistent
        handlers (``_solver_handler.py``), which also bake each rig's IK into the file
        at every save. Idempotent. The UI handler installs it at startup; in a headless
        session the first touch of a rig with an end control does (an export's
        producer among them). Returns True."""
        return SolverHandler.install()

    @classmethod
    def uninstall(cls) -> None:
        """Remove the solve's handlers; every rig's baked IK plays again (unmuted)."""
        SolverHandler.uninstall()

    @classmethod
    def repair_scene(cls) -> int:
        """Give every rig in the scene whose end control has no solve its solve back.
        In Blender the solve is blendertk's handlers, not a node in the file, so a
        session that has not installed them (a file opened headless, before any rig
        was touched) holds rigs whose end control moves nothing: this installs them
        and solves each rig now -- mayatk's repair of a scene an earlier build solved
        with a plug-in, by the same name. Not an undo step.

        Returns:
            How many rigs were repaired (0 when the solve already runs).
        """
        if SolverHandler.installed():
            return 0
        ended = []
        for obj in cls._scene().objects:
            data = cls.scene_data(obj) if obj.type == "ARMATURE" else None
            if data and data.get("end") and cls._claims(obj, data.get("group_uuid")):
                ended.append(obj)
        if not ended:
            return 0
        SolverHandler.install()
        rigs = SolverHandler.rigs(ended)
        for rig in rigs:
            rig._solve_now()
        return len(rigs)

    def ensure_solver(self) -> bool:
        """Make the end control live: install the solve's handlers when this session
        has not, and solve the rig now (mayatk's rebuilds a missing expression node;
        the name is kept for the shared callers).

        Returns:
            Whether the rig has a working end control.
        """
        if self.end_control is None:
            return False
        spec = self.spec
        bones = self.group.pose.bones
        if any(
            bones.get(self._bone_names(spec, j)[1] + self.IK_GROUP_SUFFIX) is None
            for j in spec["joints"]
        ):
            self.logger.warning(
                f"{self.name}: the end control's wiring is incomplete; rebuild the rig."
            )
            return False
        SolverHandler.ensure_installed()
        self._solve_now()
        return True

    @classmethod
    def _solve_context(cls, spec: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """What *spec*'s end control solves with, or None (no end control): the model,
        the end joint, the control's pivot on the end link and the link's turn in the
        control (``ptk.ArticulationPlan.end_pivot_turn``), whether the link hangs off
        a ball, its parent joint, and the slots of the chain from the root."""
        index = ptk.ArticulationPlan.end_joint(spec)
        if index is None:
            return None
        key = json.dumps(
            [spec.get(k) for k in ("name", "links", "joints", "end")], sort_keys=True
        )
        context = cls._contexts.get(key)
        if context is None:
            model = ptk.ArticulationModel(
                {
                    "name": spec["name"],
                    "joints": ptk.ArticulationPlan.joint_records(spec),
                }
            )
            pivot, turn = ptk.ArticulationPlan.end_pivot_turn(spec, index)
            chain = set(model.chain(index))
            turns = {c for j, c in model.channels if j == index}
            context = {
                "model": model,
                "end": index,
                "pivot": tuple(pivot),
                "turn": tuple(turn),
                "ball": {"rx", "ry", "rz"} <= turns,
                "parent": model.joints[index]["parent"],
                "chain": [s for s, (j, _c) in enumerate(model.channels) if j in chain],
            }
            if len(cls._contexts) > 32:
                cls._contexts.clear()
            cls._contexts[key] = context
        return context

    def _ik_offsets(
        self,
        fk: Sequence[float],
        end_matrix,
        blend: float,
        follow: bool,
        spec: Optional[Dict[str, Any]] = None,
    ) -> List[float]:
        """What the end control's solve adds to each channel, in :meth:`model` state
        order (degrees, the rig's units): ``blend * (solve(seed=FK) - FK)`` -- mayatk's
        expression, over ``ptk.ArticulationModel.solve`` itself. The one function the
        handlers, the save's bake and :meth:`state` all call.

        By the end link's joint: a ball following the control takes the wrist split
        (the control's rotation, its turn on the link composed in); a ball keeping its
        own turn is carried by its parent's link (the held point solved there, where
        it is rigid); any other end is placed by position alone.

        Parameters:
            fk: The FK controls' values, state order, as their limits leave them.
            end_matrix: The end control's matrix (``mathutils.Matrix``) in the rig's
                (armature) space.
            blend: ``ikBlend`` (clamped to 0..1).
            follow: ``followRotation``.
            spec: The plan (default: the stamped one).
        """
        from mathutils import Quaternion

        spec = spec if spec is not None else self.spec
        out = [0.0] * len(fk)
        context = self._solve_context(spec)
        blend = min(1.0, max(0.0, float(blend)))
        if context is None or blend <= 0.0:
            return out
        model, end, pivot = context["model"], context["end"], context["pivot"]
        seed = [float(v) for v in fk]
        target = tuple(end_matrix.to_translation())
        if not context["ball"] or (not follow and context["parent"] is None):
            solved = model.solve(seed, end, pivot, target)
        elif follow:
            x, y, z, w = context["turn"]
            held = end_matrix.to_quaternion() @ Quaternion((w, x, y, z))
            held.normalize()
            solved = model.solve(
                seed, end, pivot, target, (held.x, held.y, held.z, held.w)
            )
        else:
            parent = context["parent"]
            held = model.to_local(seed, parent, model.point(seed, end, pivot))
            solved = model.solve(seed, parent, held, target)
        for slot in context["chain"]:
            out[slot] = blend * (solved[slot] - seed[slot])
        return out

    def _fk_of(self, source, spec: Dict[str, Any]) -> List[float]:
        """The FK controls' values on *source* (the armature, or its evaluated copy),
        state order, as their limits leave them."""
        joints = {j["id"]: j for j in spec["joints"]}
        out = []
        for joint_id, channel in ptk.ArticulationPlan.slots(spec):
            joint = joints[joint_id]
            bone = source.pose.bones[self._bone_names(spec, joint)[1]]
            prop, index = self.CHANNEL_PATHS[channel]
            value = float(getattr(bone, prop)[index])
            value = math.degrees(value) if channel.startswith("r") else value
            bounds = (joint.get("limits") or {}).get(channel) or (None, None)
            out.append(self._clamped(value, *bounds))
        return out

    def _solve_inputs(self, source, spec: Dict[str, Any]):
        """``(fk, end matrix, blend, follow)`` -- what the end control's solve reads off
        *source* (the armature, or its evaluated copy) -- or None (no end control).
        The end control is placed by its own channels (a root bone the build leaves
        unconstrained), so a bake reading its curves sees what the handlers see."""
        if not spec.get("end"):
            return None
        end = source.pose.bones.get(self._end_name(spec))
        if end is None:
            return None
        return (
            self._fk_of(source, spec),
            end.bone.matrix_local @ end.matrix_basis,
            float(end.get(self.BLEND_ATTR, 1.0)),
            bool(end.get(self.FOLLOW_ATTR, True)),
        )

    def _write_offsets(self, offsets: Sequence[float], spec: Dict[str, Any]) -> None:
        """*offsets* (state order) onto the MCH bones' channels."""
        bones = self.group.pose.bones
        joints = {j["id"]: j for j in spec["joints"]}
        for (joint_id, channel), value in zip(
            ptk.ArticulationPlan.slots(spec), offsets
        ):
            name = self._bone_names(spec, joints[joint_id])[1] + self.IK_GROUP_SUFFIX
            bone = bones.get(name)
            if bone is None:
                continue
            prop, index = self.CHANNEL_PATHS[channel]
            getattr(bone, prop)[index] = (
                math.radians(value) if channel.startswith("r") else value
            )

    def _solve_now(self) -> None:
        """Solve the end control from its inputs as they stand and write the offsets
        now -- after an edit the handlers do not see (a switch, a give, a rebuild):
        Blender re-evaluates on a custom property only when the UI sets it."""
        if self.end_control is None or not SolverHandler.installed():
            return
        SolverHandler._solved.pop(self._group_uuid, None)
        SolverHandler.solve(self)
        self._update()

    # ================================================================ bake
    def _bake(self) -> bool:
        """Key the end control's solve on the MCH channels over every frame its inputs
        span (the scene's range and their keys) when anything it reads is animated --
        the end control, its switches, the FK controls -- so the file plays its IK
        without blendertk; a bake gone stale (nothing animated now) is removed. The
        inputs are read off their curves frame by frame, the scene never stepped.
        Unmuting the bake is the caller's. A bake whose inputs and keys are as the last
        one left them is kept (:meth:`_bake_fingerprint`): solving every frame costs
        milliseconds a frame, at every save.

        Returns:
            Whether the rig holds a bake.
        """
        import blendertk as btk  # the root: anim_utils ranks above rig_utils

        spec = self.spec
        arm = self.group
        end = self.end_control
        if end is None:
            return False
        reads = {self._bone_names(spec, j)[1] for j in spec["joints"]} | {end.name}
        curves: Dict[Tuple[str, str, int], Any] = {}
        for curve in btk.AnimUtils.get_fcurves([arm]):
            found = self._bone_of_path(curve.data_path)
            if found is not None and found[0] in reads and not curve.mute:
                curves[(found[0], found[1], curve.array_index)] = curve
        if not curves:
            action, slot = btk.AnimUtils._animating(arm.animation_data)
            for curve in reversed(self._bake_curves(arm)):
                btk.AnimUtils._remove_fcurve(action, slot, curve)
            if self.BAKE_ATTR in arm.keys():
                del arm[self.BAKE_ATTR]
            return False
        scene = (list(arm.users_scene) or [self._scene()])[0]
        lo, hi = float(scene.frame_start), float(scene.frame_end)
        for curve in curves.values():
            first, last = curve.range()
            lo, hi = min(lo, first), max(hi, last)
        frames = list(range(math.floor(lo), math.ceil(hi) + 1))
        if arm.get(self.BAKE_ATTR) == self._bake_fingerprint(curves, frames, spec):
            return True  # made from these inputs, and untouched since
        rows = [
            self._ik_offsets(*self._inputs_at(frame, curves, spec), spec=spec)
            for frame in frames
        ]
        joints = {j["id"]: j for j in spec["joints"]}
        bones = arm.pose.bones
        for column, (joint_id, channel) in enumerate(ptk.ArticulationPlan.slots(spec)):
            name = self._bone_names(spec, joints[joint_id])[1] + self.IK_GROUP_SUFFIX
            prop, index = self.CHANNEL_PATHS[channel]
            to_value = math.radians if channel.startswith("r") else float
            values = [to_value(row[column]) for row in rows]
            self._key_curve(bones[name], prop, index, frames, values)
        arm[self.BAKE_ATTR] = self._bake_fingerprint(curves, frames, spec)
        return True

    def _bake_fingerprint(self, curves, frames, spec: Dict[str, Any]) -> str:
        """What a bake depends on, hashed: the plan, the frames, every input curve
        (its keys, handles, interpolation and easing, extrapolation, and every
        property of every modifier -- a Cycles modifier plays past the last key), the
        input channels no curve drives as they stand, and the bake curves' own keys (a
        bake edited or cut by hand is made again). A stale bake shipped is worse than the
        seconds a needless one costs."""
        import hashlib

        parts: List[Any] = [json.dumps(spec, sort_keys=True), frames[0], frames[-1]]
        for key in sorted(curves):
            parts.append((key, self._curve_signature(curves[key])))
        bones = self.group.pose.bones
        statics = [self._bone_names(spec, j)[1] for j in spec["joints"]]
        statics.append(self._end_name(spec))
        channels = (
            "location",
            "rotation_euler",
            "rotation_quaternion",
            "rotation_axis_angle",
            "scale",
        )
        for name in statics:
            # A channel a curve drives is its curve's (above): its value now is only
            # the current frame's, and would bake again at a save on another frame.
            bone = bones[name]
            held = [bone.rotation_mode]
            for prop in channels:
                held += [
                    (prop, i, v)
                    for i, v in enumerate(getattr(bone, prop))
                    if (name, prop, i) not in curves
                ]
            held += [
                (k, bone[k])
                for k in (self.BLEND_ATTR, self.FOLLOW_ATTR)
                if k in bone.keys() and (name, k, 0) not in curves
            ]
            parts.append((name, tuple(held)))
        baked = sorted(
            self._bake_curves(self.group), key=lambda c: (c.data_path, c.array_index)
        )
        parts.append([self._curve_signature(c) for c in baked])
        return hashlib.sha1(repr(parts).encode("utf-8")).hexdigest()

    @classmethod
    def _curve_signature(cls, curve) -> tuple:
        """Everything *curve*'s value at a frame depends on, as plain values."""
        points = curve.keyframe_points
        count = len(points)
        arrays = []
        for attr in ("co", "handle_left", "handle_right"):
            flat = [0.0] * (2 * count)
            points.foreach_get(attr, flat)
            arrays.append(tuple(flat))
        shapes = tuple(
            (p.interpolation, p.easing, p.back, p.amplitude, p.period) for p in points
        )
        modifiers = tuple(cls._rna_signature(m) for m in curve.modifiers)
        # Not its mute: the handlers mute and unmute the bake around every save, and
        # a muted input curve is no input at all (left out of the bake's inputs).
        return (
            curve.data_path,
            curve.array_index,
            curve.extrapolation,
            tuple(arrays),
            shapes,
            modifiers,
        )

    @classmethod
    def _rna_signature(cls, struct, depth: int = 0) -> tuple:
        """Every plain property of an RNA *struct* (and of its nested structs and
        collections, two levels down) as ``(name, value)`` pairs -- a modifier's
        settings whatever its type."""
        import bpy

        out = []
        for prop in struct.bl_rna.properties:
            name = prop.identifier
            if name == "rna_type":
                continue
            value = getattr(struct, name, None)
            if prop.type == "POINTER":
                if (
                    depth < 2
                    and value is not None
                    and not isinstance(value, bpy.types.ID)
                ):
                    out.append((name, cls._rna_signature(value, depth + 1)))
                continue
            if prop.type == "COLLECTION":
                if depth < 2:
                    out.append(
                        (name, tuple(cls._rna_signature(v, depth + 1) for v in value))
                    )
                continue
            if hasattr(value, "__len__") and not isinstance(value, str):
                value = tuple(value)
            out.append((name, value))
        return tuple(out)

    def _inputs_at(self, frame: float, curves, spec: Dict[str, Any]):
        """:meth:`_solve_inputs` at *frame*, each channel off its curve (*curves*:
        ``(bone, property, index)`` -> F-curve), else as it stands."""
        from mathutils import Euler, Matrix, Quaternion

        bones = self.group.pose.bones

        def value(bone, prop, index, current):
            curve = curves.get((bone.name, prop, index))
            return float(curve.evaluate(frame)) if curve is not None else float(current)

        def channel(bone, prop, size):
            stored = getattr(bone, prop)
            return [value(bone, prop, i, stored[i]) for i in range(size)]

        joints = {j["id"]: j for j in spec["joints"]}
        fk = []
        for joint_id, ch in ptk.ArticulationPlan.slots(spec):
            bone = bones[self._bone_names(spec, joints[joint_id])[1]]
            prop, index = self.CHANNEL_PATHS[ch]
            v = value(bone, prop, index, getattr(bone, prop)[index])
            v = math.degrees(v) if ch.startswith("r") else v
            bounds = (joints[joint_id].get("limits") or {}).get(ch) or (None, None)
            fk.append(self._clamped(v, *bounds))
        end = bones[self._end_name(spec)]
        mode = end.rotation_mode
        if mode == "QUATERNION":
            turn = Quaternion(channel(end, "rotation_quaternion", 4))
        elif mode == "AXIS_ANGLE":
            angle, *axis = channel(end, "rotation_axis_angle", 4)
            turn = Quaternion(axis, angle)
        else:
            turn = Euler(channel(end, "rotation_euler", 3), mode).to_quaternion()
        basis = Matrix.LocRotScale(
            channel(end, "location", 3), turn, channel(end, "scale", 3)
        )
        blend = value(end, self.BLEND_ATTR, 0, end.get(self.BLEND_ATTR, 1.0))
        follow = value(end, self.FOLLOW_ATTR, 0, end.get(self.FOLLOW_ATTR, True))
        return fk, end.bone.matrix_local @ basis, blend, bool(round(follow))

    @staticmethod
    def _key_curve(bone, prop: str, index: int, frames, values) -> None:
        """*bone*'s ``prop[index]`` keyed at *frames* to *values* -- one linear key a
        frame, replacing whatever the curve held."""
        import blendertk as btk  # the root: anim_utils ranks above rig_utils

        arm = bone.id_data
        path = f"{bone.path_from_id()}.{prop}"

        def find():
            return next(
                (
                    c
                    for c in btk.AnimUtils.get_fcurves([arm])
                    if c.data_path == path and c.array_index == index
                ),
                None,
            )

        curve = find()
        if curve is None:
            bone.keyframe_insert(prop, index=index, frame=frames[0], group=bone.name)
            curve = find()
        points = curve.keyframe_points
        points.clear()
        points.add(len(frames))
        points.foreach_set(
            "co", [v for f, value in zip(frames, values) for v in (float(f), value)]
        )
        for point in points:
            point.interpolation = "LINEAR"
        curve.update()

    # ================================================================== IK
    def _key_end(self, key: Optional[bool]) -> None:
        """Key the end control's free channels when *key* (default: Blender's auto
        keying)."""
        scene = self._scene()
        if key is None:
            key = bool(scene.tool_settings.use_keyframe_insert_auto)
        end = self.end_control
        if not key or end is None:
            return
        frame = scene.frame_current
        for index, locked in enumerate(end.lock_location):
            if not locked:
                end.keyframe_insert(
                    "location", index=index, frame=frame, group=end.name
                )
        rotation = self._rotation_path(end)
        if rotation == "rotation_euler":
            for index, locked in enumerate(end.lock_rotation):
                if not locked:
                    end.keyframe_insert(
                        rotation, index=index, frame=frame, group=end.name
                    )
        elif not all(end.lock_rotation):
            end.keyframe_insert(rotation, frame=frame, group=end.name)

    @staticmethod
    def _rotation_path(bone) -> str:
        """The rotation property *bone*'s rotation mode reads."""
        return {
            "QUATERNION": "rotation_quaternion",
            "AXIS_ANGLE": "rotation_axis_angle",
        }.get(bone.rotation_mode, "rotation_euler")

    def _place_end(self, matrix) -> None:
        """Put the end control at *matrix* (the rig's space) through its location and
        the rotation channels it leaves free -- a hinged end's turn stays as it is."""
        end = self.end_control
        basis = end.bone.matrix_local.inverted() @ matrix
        location, rotation, _scale = basis.decompose()
        end.location = location
        path = self._rotation_path(end)
        if path == "rotation_euler":
            euler = rotation.to_euler(end.rotation_mode, end.rotation_euler)
            for index, locked in enumerate(end.lock_rotation):
                if not locked:
                    end.rotation_euler[index] = euler[index]
        elif all(end.lock_rotation):
            return
        elif path == "rotation_quaternion":
            end.rotation_quaternion = rotation
        else:
            axis, angle = rotation.to_axis_angle()
            end.rotation_axis_angle = (angle, *axis)

    def match_end_control(self, key: Optional[bool] = None) -> None:
        """Put the end control where the end link is now -- after FK posing, or when it
        was left out of reach -- so nothing moves when the IK blend comes up; one undo
        step. Keyed when *key* (default: Blender's auto keying).

        Raises:
            RuntimeError: The rig has no end control.
        """
        if self.end_control is None:
            raise RuntimeError(f"Rig {self.name!r} has no end control.")
        with self._step("Articulated Rig: Match End Control"):
            self._match_end(key)
            self._solve_now()

    def _match_end(self, key: Optional[bool]) -> None:
        """:meth:`match_end_control`'s body: the end link's pose read off the model --
        FK plus the solve -- and the control put back on it through the plan's end
        offset."""
        from mathutils import Matrix, Quaternion, Vector

        spec = self.spec
        index = ptk.ArticulationPlan.end_joint(spec)
        position, (x, y, z, w) = self.model().world(self.state())[index]
        link = Matrix.LocRotScale(Vector(position), Quaternion((w, x, y, z)), None)
        offset = self._mat(ptk.ArticulationPlan.end_offset(spec, index))
        self._place_end(link @ offset.inverted())
        self._key_end(key)

    def switch_ik(self, on: Optional[bool] = None, key: Optional[bool] = None) -> bool:
        """Switch the end control's solve on or off (default: flip it) without the pose
        jumping -- one undo step: on, the end control first moves to the end link;
        off, the FK controls first take the pose the solve made. Keyed when *key*
        (default: Blender's auto keying).

        Returns:
            Whether the solve is now on.

        Raises:
            RuntimeError: The rig has no end control.
        """
        end = self.end_control
        if end is None:
            raise RuntimeError(f"Rig {self.name!r} has no end control.")
        scene = self._scene()
        if key is None:
            key = bool(scene.tool_settings.use_keyframe_insert_auto)
        if on is None:
            # The switch's own setting, not :meth:`ik_blend`: an end control whose
            # solve is not live reads 0 there, and would never flip off.
            on = float(end.get(self.BLEND_ATTR, 1.0)) < 0.5
        on = bool(on)
        with self._step("Articulated Rig: Switch IK/FK"):
            if on:
                self._match_end(key)
            else:
                slots = self._slots()
                self.set_state(self.state(slots), key=key, slots=slots)
            end = self.end_control
            end[self.BLEND_ATTR] = 1.0 if on else 0.0
            if key:
                end.keyframe_insert(
                    f'["{self.BLEND_ATTR}"]', frame=scene.frame_current, group=end.name
                )
            self._solve_now()
        return on

    def reset_pose(self, key: Optional[bool] = None) -> None:
        """Every control back to rest -- FK at 0, the end control on its rest frame --
        one undo step, keyed when *key* (default: Blender's auto keying)."""
        with self._step("Articulated Rig: Rest Pose"):
            self._to_rest()
            if key is None:
                key = bool(self._scene().tool_settings.use_keyframe_insert_auto)
            if key:
                self.set_state([0.0] * len(self._slots()), key=True)
                self._key_end(True)
            self._solve_now()

    # ================================================================ record
    def record(self) -> Dict[str, Any]:
        """This rig's entry in the ``articulation`` record: its joints
        (``ptk.ArticulationPlan.joint_records``) and the parts a hand grabs."""
        nodes = self._nodes()
        # The part's name NOW (a runtime binds by it): a rename after the build must
        # not leave the record naming an object that is gone.
        return ptk.ArticulationPlan.record(
            self.spec,
            lambda m: nodes[m["uuid"]].name if m["uuid"] in nodes else m["name"],
        )

    def model(self) -> "ptk.ArticulationModel":
        """The runtime model of this rig -- what the engines pose."""
        return ptk.ArticulationModel(self.record())

    @classmethod
    def export_record(cls, ctx) -> Optional["ptk.Record"]:
        """The ``articulation`` record for this scene, or None when it has no rig --
        the ``ptk.SceneRecords.ARTICULATION`` producer (``FbxUtils.PRODUCERS``). Pure:
        reads the rigs' plans, never writes.

        ``{"version": 1, "rigs": [...]}``, one :meth:`record` per rig. No
        ``unit_scale``: a runtime measures its own units against the rest pose
        (``ptk.ArticulationModel.scale_of``), which covers a scaled prop and an FBX
        unit conversion alike.

        Parameters:
            ctx (ptk.ExportContext): The export's decisions (unused: the record is a
                function of the rigs alone).
        """
        rigs = [rig.record() for rig in cls.scene_rigs()]
        if not rigs:
            return None
        return ptk.SceneRecords.ARTICULATION.make({"rigs": rigs})

    @classmethod
    def refresh_export_metadata(cls) -> Optional[str]:
        """Publish the ``articulation`` record now -- the authoring-time half of
        :meth:`export_record` (an export pipeline runs the producer itself) -- and opt
        it into every FBX export this session. Clears the channel when the scene has no
        rig."""
        import blendertk as btk  # the root: env_utils ranks above rig_utils

        record = cls.export_record(ptk.ExportContext(mode=ptk.ExportContext.AUTHORING))
        btk.FbxUtils.publish_authored({ptk.SceneRecords.ARTICULATION: record})
        if record is not None:
            btk.FbxUtils.enable_export_producer(ptk.SceneRecords.ARTICULATION)
        return record.text if record is not None else None

    # ================================================================ joints
    def _joint_of(self, node) -> int:
        """The model index of the joint *node* rides -- a part, or anything under
        one."""
        riders = ptk.ArticulationPlan.riders(self.spec)
        nodes = self._nodes()
        obj = self._object(node)
        while obj is not None:
            uid = next((k for k, v in nodes.items() if v == obj), None)
            index = riders.get(uid) if uid is not None else None
            if index is not None:
                return index
            obj = obj.parent
        raise ValueError(
            f"{self._label(node)} rides no joint of rig {self.name!r} (the root link "
            "stays put)."
        )

    # ================================================================== grab
    def _space(self):
        """``(world -> rig matrix, the rig space's rotation in the world)``."""
        world = self.group.matrix_world
        return world.inverted(), world.to_3x3().normalized().to_quaternion()

    def grab_begin(self, node, point: Sequence[float]) -> Dict[str, Any]:
        """Take hold of *node* (a part, or anything under one) at the world *point*.

        With the end control on and *node* on the end link, the hold drags the end
        control (the solve does the rest, as when the end control is moved by hand);
        otherwise it poses the FK controls.

        Returns:
            The hold :meth:`grab_to` moves: the joint, the point in its frame, the
            link's rig-space rotation as taken, and the model and control slots (read
            once, not per drag) -- plus, dragging the end control, its bone's name,
            its world matrix as taken and the point.
        """
        from mathutils import Vector

        model = self.model()
        slots = self._slots()
        state = self.state(slots)
        joint = self._joint_of(node)
        inverse, _space = self._space()
        local = inverse @ Vector(point)
        hold: Dict[str, Any] = {
            "joint": joint,
            "local": model.to_local(state, joint, tuple(local)),
            "rotation": model.world(state)[joint][1],
            "model": model,
            "slots": slots,
        }
        end = self.end_control
        if (
            end is not None
            and self.ik_blend() >= 0.5
            and joint == ptk.ArticulationPlan.end_joint(self.spec)
        ):
            hold["end"] = {
                "control": end.name,
                "matrix": self.group.matrix_world
                @ end.bone.matrix_local
                @ end.matrix_basis,
                "point": tuple(point)[:3],
            }
        return hold

    def held_point(self, hold: Dict[str, Any]) -> Tuple[float, float, float]:
        """Where a hold's point is now, in world space -- read off the model of the
        pose (FK plus the solve), not the evaluated bones."""
        from mathutils import Vector

        state = self.state(hold["slots"])
        p = self.group.matrix_world @ Vector(
            hold["model"].point(state, hold["joint"], hold["local"])
        )
        return (p.x, p.y, p.z)

    def pose_to(
        self,
        node,
        target: Sequence[float],
        point: Optional[Sequence[float]] = None,
        key: Optional[bool] = None,
        attempts: int = 8,
    ) -> float:
        """Bring a part's *point* (default its origin) to the world *target* -- the
        lens over an Empty, a hook onto a peg -- solving a grab's step again until it
        lands or stops closing in; one undo step.

        Returns:
            The distance left (0 within the solver's tolerance; more when the target
            is out of reach or behind a limit).
        """
        with self._step("Articulated Rig: Pose To"):
            obj = self._object(node)
            if obj is None:
                raise ValueError(f"{self._label(node)} is not in the scene.")
            if point is None:
                point = tuple(obj.matrix_world.translation)
            hold = self.grab_begin(obj, point)
            left = float("inf")
            for _ in range(max(1, attempts)):
                self.grab_to(hold, target, key=False)
                now = math.dist(self.held_point(hold), target)
                if now >= left - 1.0e-9:
                    left = now
                    break
                left = now
            self.key_hold(hold, key)
            return left

    def grab_to(
        self,
        hold: Dict[str, Any],
        target: Sequence[float],
        turn: Optional[Sequence[float]] = None,
        key: Optional[bool] = None,
    ) -> List[float]:
        """Move a hold's point to the world *target* and pose the rig there.

        Parameters:
            hold: From :meth:`grab_begin`.
            target: Where the held point goes, world space.
            turn: The hand's rotation since the hold began, a world-space ``(x, y,
                z, w)``, applied to the held link when it hangs off a ball. None
                keeps the link as it was taken (a mouse).
            key: Key what moved (default: Blender's auto keying).

        Returns:
            The new state (the pose, as :meth:`state` reads it).
        """
        from mathutils import Quaternion, Vector

        if "end" in hold:
            self._drag_end(hold, target, turn)
            self._settle_end(hold, target)
            self._solve_now()
            self.key_hold(hold, key)
            return self.state(hold["slots"])
        model = hold["model"]
        inverse, space = self._space()
        goal = inverse @ Vector(target)
        x, y, z, w = hold["rotation"]
        held = Quaternion((w, x, y, z))
        if turn is not None:
            # A world turn seen from the rig space: out to the world, turn, back.
            tx, ty, tz, tw = turn
            held = space.inverted() @ Quaternion((tw, tx, ty, tz)) @ space @ held
        state = model.solve(
            self.state(hold["slots"]),
            hold["joint"],
            hold["local"],
            tuple(goal),
            (held.x, held.y, held.z, held.w),
        )
        self.set_state(state, key=key, slots=hold["slots"])
        return state

    def _drag_end(
        self,
        hold: Dict[str, Any],
        target: Sequence[float],
        turn: Optional[Sequence[float]],
    ) -> None:
        """Carry the end control rigidly with the held point: turned by the hand's
        *turn* about the point (a ball-mounted end link only -- a hinged one has no
        turn of its own to take, and its control's rotation is locked), then moved
        so the point is on *target*."""
        from mathutils import Matrix, Quaternion, Vector

        end = hold["end"]
        moved = end["matrix"]
        point = Vector(end["point"])
        control = self.group.pose.bones[end["control"]]
        if turn is not None and self.FOLLOW_ATTR in control.keys():
            x, y, z, w = turn
            spin = Quaternion((w, x, y, z)).to_matrix().to_4x4()
            moved = (
                Matrix.Translation(point) @ spin @ Matrix.Translation(-point) @ moved
            )
        moved = Matrix.Translation(Vector(target) - point) @ moved
        self._place_end(self.group.matrix_world.inverted() @ moved)

    def _settle_end(self, hold: Dict[str, Any], target: Sequence[float]) -> None:
        """Nudge a dragged end control until the held point is on *target*. Carried
        rigidly, the control lands the point only when the end link keeps its turn to
        the control (a ball following it); a hinged end turns with the solve, so a
        point off the pivot drifts -- the next pass takes up what is left. A pass that
        gains nothing (the target out of reach) is undone, so the control never runs
        off ahead of the arm. The held point is read off the model (:meth:`held_point`)
        -- no evaluation between passes."""
        from mathutils import Vector

        tolerance = 1.0e-4 * self._end_extent()
        goal = Vector(target)
        miss = goal - Vector(self.held_point(hold))
        for _ in range(self.END_DRAG_PASSES):
            if miss.length <= tolerance:
                return
            self._move_end(miss)
            after = goal - Vector(self.held_point(hold))
            if after.length >= miss.length:
                self._move_end(-miss)
                return
            miss = after

    def _move_end(self, delta) -> None:
        """Move the end control by the world vector *delta* (its location channels)."""
        end = self.end_control
        to_basis = (self.group.matrix_world @ end.bone.matrix_local).to_3x3()
        end.location = end.location + to_basis.inverted() @ delta

    def key_hold(self, hold: Dict[str, Any], key: Optional[bool] = None) -> None:
        """Key what a hold moved -- the end control it dragged, else the FK controls --
        when *key* (default: Blender's auto keying)."""
        scene = self._scene()
        if key is None:
            key = bool(scene.tool_settings.use_keyframe_insert_auto)
        if not key:
            return
        if "end" in hold:
            self._key_end(True)
            return
        for bone, channel in hold["slots"]:
            prop, index = self.CHANNEL_PATHS[channel]
            bone.keyframe_insert(
                prop, index=index, frame=scene.frame_current, group=bone.name
            )
