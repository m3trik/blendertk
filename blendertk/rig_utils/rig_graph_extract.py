# !/usr/bin/python
# coding=utf-8
"""Read a Blender rig into a RigGraph -- the Blender side of phase 2.

Mirror of mayatk's ``RigGraphExtractor`` (name + behavior). Object constraints,
pose-bone constraints and drivers become records in the schema's five shapes
(``.claude/RIG_GRAPH_SCHEMA.md``); anything that drives the scene but has no
op is emitted as ``opaque`` rather than skipped, so ``RigGraph.coverage()`` --
not the plan's severity -- is what says the graph is complete.

Identity is the payload's prim path, ``UsdUtils.export_prim_path`` (the parent
chain, each name spelled the way Blender's USD exporter writes it); a bone's id
is its armature's path plus the bone name, which is what the Maya builder
resolves to a joint.

``import bpy`` is deferred into the call bodies (no import side effects), so
the surface resolves under headless ``--background`` and in plain-venv tests.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import pythontk as ptk


class _RigGraphExtractorInternal:
    """Readers: one per driver shape, plus identity and census helpers."""

    #: Blender constraint type -> the channels a ``blend`` record writes.
    _BLEND_TYPES: Dict[str, Tuple[str, ...]] = {
        "CHILD_OF": ("translate", "rotate"),
        "COPY_TRANSFORMS": ("translate", "rotate"),
        "COPY_LOCATION": ("translate",),
        "COPY_ROTATION": ("rotate",),
        "COPY_SCALE": ("scale",),
    }
    _AIM_TYPES: Tuple[str, ...] = ("TRACK_TO", "DAMPED_TRACK", "LOCKED_TRACK")
    #: Blender track / up enums -> the record's local axis vectors.
    _AXIS: Dict[str, List[float]] = {
        "X": [1.0, 0.0, 0.0],
        "Y": [0.0, 1.0, 0.0],
        "Z": [0.0, 0.0, 1.0],
        "NEGATIVE_X": [-1.0, 0.0, 0.0],
        "NEGATIVE_Y": [0.0, -1.0, 0.0],
        "NEGATIVE_Z": [0.0, 0.0, -1.0],
    }
    _CHANNEL_OF: Dict[Tuple[str, Optional[int]], str] = {
        ("location", 0): "translate.x",
        ("location", 1): "translate.y",
        ("location", 2): "translate.z",
        ("rotation_euler", 0): "rotate.x",
        ("rotation_euler", 1): "rotate.y",
        ("rotation_euler", 2): "rotate.z",
        ("scale", 0): "scale.x",
        ("scale", 1): "scale.y",
        ("scale", 2): "scale.z",
        ("hide_viewport", None): "visibility",
    }
    _VAR_CHANNEL: Dict[str, str] = {
        "LOC_X": "translate.x",
        "LOC_Y": "translate.y",
        "LOC_Z": "translate.z",
        "ROT_X": "rotate.x",
        "ROT_Y": "rotate.y",
        "ROT_Z": "rotate.z",
        "SCALE_X": "scale.x",
        "SCALE_Y": "scale.y",
        "SCALE_Z": "scale.z",
    }
    _LINEAR_RE = re.compile(
        r"^\s*(?P<v>[A-Za-z_]\w*)\s*\*\s*(?P<k>-?\d+(?:\.\d+)?)\s*(?:\+\s*(?P<b>-?\d+(?:\.\d+)?))?\s*$"
    )

    #: ``params.geometry`` of a ``points/skin`` record, by object type.
    _GEOMETRY_KIND: Dict[str, str] = {
        "MESH": "mesh",
        "CURVE": "curve",
        "CURVES": "curve",
        "SURFACE": "surface",
        "LATTICE": "lattice",
    }

    def __init__(self) -> None:
        self._nodes: Dict[str, Dict[str, Any]] = {}
        self._records: List[Dict[str, Any]] = []
        self._census: Dict[str, int] = {}

    # ------------------------------------------------------------ identity
    def _id(self, obj: Any) -> str:
        from blendertk.env_utils.usd import UsdUtils

        return UsdUtils.export_prim_path(obj)

    def _bone_id(self, armature: Any, bone_name: str) -> str:
        from blendertk.env_utils.usd import UsdUtils

        return f"{self._id(armature)}/{UsdUtils.sanitize_prim_name(bone_name)}"

    def _anchor_id(self, target: Any, subtarget: str = "") -> Optional[str]:
        """The id a constraint's target names: an object, or one of an
        armature's bones when ``subtarget`` is set."""
        if target is None:
            return None
        if (
            subtarget
            and getattr(getattr(target, "data", None), "bones", None) is not None
        ):
            self._ensure_node(target)
            bone = target.data.bones.get(subtarget)
            if bone is not None:
                return self._ensure_bone(target, bone)
        return self._ensure_node(target)

    # ---------------------------------------------------------------- nodes
    def _ensure_node(self, obj: Any) -> str:
        node_id = self._id(obj)
        if node_id in self._nodes:
            return node_id
        if obj.parent is not None:
            self._ensure_node(obj.parent)
        kind = {
            "ARMATURE": "transform",
            "EMPTY": "locator",
            "CURVE": "curve",
            "MESH": "mesh",
            "LATTICE": "lattice",
        }.get(getattr(obj, "type", ""), "transform")
        rest = {
            "translate": [float(v) for v in obj.location],
            "rotate": [float(v) for v in obj.rotation_euler],
            "scale": [float(v) for v in obj.scale],
            "rotate_order": str(obj.rotation_mode).lower()
            if obj.rotation_mode in ("XYZ", "XZY", "YXZ", "YZX", "ZXY", "ZYX")
            else "xyz",
        }
        self._nodes[node_id] = {
            "id": node_id,
            "path": obj.name,
            "kind": kind,
            "rest": rest,
        }
        return node_id

    def _ensure_bone(self, armature: Any, bone: Any) -> str:
        node_id = self._bone_id(armature, bone.name)
        if node_id in self._nodes:
            return node_id
        self._ensure_node(armature)
        if bone.parent is not None:
            self._ensure_bone(armature, bone.parent)
        head = bone.head_local
        self._nodes[node_id] = {
            "id": node_id,
            "path": f"{armature.name}:{bone.name}",
            "kind": "joint",
            "rest": {
                "translate": [float(v) for v in head],
                "rotate": [0.0, 0.0, 0.0],
                "scale": [1.0, 1.0, 1.0],
                "rotate_order": "xyz",
            },
        }
        return node_id

    def _ensure_attr(self, obj: Any, name: str) -> None:
        node_id = self._ensure_node(obj)
        if name in ("location", "rotation_euler", "scale", "hide_viewport"):
            return
        value = obj.get(name)
        entry: Dict[str, Any] = {"type": "float", "keyable": True}
        if isinstance(value, (int, float)):
            entry["default"] = float(value)
        try:
            ui = obj.id_properties_ui(name).as_dict()
            for key in ("min", "max"):
                if key in ui:
                    entry[key] = ui[key]
        except (AttributeError, TypeError, KeyError):
            pass
        self._nodes[node_id].setdefault("attrs", {})[name] = entry

    # -------------------------------------------------------------- records
    def _emit(
        self,
        shape: str,
        op: str,
        target: Any,
        sources: List[Dict[str, Any]],
        params: Dict[str, Any],
        provenance: List[str],
    ) -> Dict[str, Any]:
        record = {
            "id": f"rec_{len(self._records) + 1:03d}",
            "shape": shape,
            "op": op,
            "target": target,
            "sources": sources,
            "params": params,
            "provenance": provenance,
        }
        self._records.append(record)
        return record

    def _opaque(self, node_type: str, node: str, target_ids: Iterable[str]) -> None:
        ids = [i for i in dict.fromkeys(target_ids) if i]
        if not ids:
            return
        self._emit(
            "opaque",
            "opaque",
            {"ids": ids},
            [],
            {"origin": {"app": "blender", "node_type": node_type, "node": node}},
            [f"{node_type}:{node}"],
        )

    def _count(self, node_type: str) -> None:
        self._census[node_type] = self._census.get(node_type, 0) + 1

    # ---- constraints ------------------------------------------------------
    def _read_constraints(
        self, owner: Any, constraints: Any, owner_id: str, owner_label: str
    ) -> None:
        """Every unmuted constraint on an object or pose bone."""
        blends: Dict[Tuple[str, ...], Dict[str, Any]] = {}
        for con in constraints:
            if con.mute:
                continue
            self._count(con.type)
            provenance = [f"{con.type}:{owner_label}.{con.name}"]
            target, subtarget = (
                getattr(con, "target", None),
                getattr(con, "subtarget", ""),
            )
            offset = con.type == "CHILD_OF" or bool(getattr(con, "use_offset", False))
            if (
                target is not None
                and target.get("rig_graph") == "space"
                and target.parent is not None
            ):
                # A builder SPACE HELPER (the Maya-semantics blend): the space
                # is its parent and the offset it holds is the record's
                # maintain_offset -- fold it back so the graph names the rig,
                # not the plumbing.
                subtarget = target.parent_bone if target.parent_type == "BONE" else ""
                target, offset = target.parent, True
            target_id = self._anchor_id(target, subtarget)
            if con.type in self._BLEND_TYPES and target_id:
                # Maya says "one parentConstraint, N targets"; Blender says it as N
                # stacked constraints on the owner. Same intent: ONE blend record.
                channels = self._BLEND_TYPES[con.type]
                record = blends.get(channels)
                if record is None:
                    record = blends[channels] = self._emit(
                        "transform",
                        "blend",
                        {"id": owner_id, "channels": list(channels)},
                        [],
                        {
                            "compose": "matrix"
                            if con.type in ("CHILD_OF", "COPY_TRANSFORMS")
                            else "independent",
                            "maintain_offset": offset,
                        },
                        [],
                    )
                record["sources"].append(
                    {"id": target_id, "role": "space", "weight": float(con.influence)}
                )
                record["provenance"].extend(provenance)
            elif con.type in self._AIM_TYPES and target_id:
                params: Dict[str, Any] = {
                    "aim_axis": self._AXIS[str(con.track_axis).replace("TRACK_", "")],
                }
                if con.type == "TRACK_TO":
                    params["up_axis"] = self._AXIS[str(con.up_axis).replace("UP_", "")]
                    params["up_ref"] = {"kind": "axis", "vector": [0.0, 0.0, 1.0]}
                self._emit(
                    "transform",
                    "aim",
                    {"id": owner_id, "channels": ["rotate"]},
                    [
                        {
                            "id": target_id,
                            "role": "target",
                            "weight": float(con.influence),
                        }
                    ],
                    params,
                    provenance,
                )
            elif con.type == "FOLLOW_PATH" and target_id:
                self._emit(
                    "transform",
                    "path",
                    {"id": owner_id, "channels": ["translate", "rotate"]},
                    [{"id": target_id, "role": "curve"}],
                    {
                        "u": float(getattr(con, "offset_factor", 0.0)),
                        "follow": bool(con.use_curve_follow),
                        "front_axis": str(con.forward_axis)
                        .replace("FORWARD_", "")
                        .lower()[-1],
                        "up_axis": str(con.up_axis).replace("UP_", "").lower()[-1],
                        "bank": False,
                    },
                    provenance,
                )
            elif con.type == "SPLINE_IK" and target_id:
                self._emit(
                    "transform",
                    "spline_ik",
                    {
                        "chain": self._chain(owner, int(con.chain_count)),
                        "channels": ["rotate"],
                    },
                    [{"id": target_id, "role": "curve"}],
                    {"twist": {"distribution": "linear", "start": 0.0, "end": 0.0}},
                    provenance,
                )
            elif con.type == "IK" and target_id:
                sources = [{"id": target_id, "role": "goal"}]
                pole = self._anchor_id(
                    getattr(con, "pole_target", None),
                    getattr(con, "pole_subtarget", ""),
                )
                if pole:
                    sources.append({"id": pole, "role": "pole"})
                self._emit(
                    "transform",
                    "ik",
                    {
                        "chain": self._chain(owner, int(con.chain_count)),
                        "channels": ["rotate"],
                    },
                    sources,
                    {"solver": "rotate_plane", "twist": 0.0},
                    provenance,
                )
            else:
                self._opaque(con.type, f"{owner_label}.{con.name}", [owner_id])

    def _chain(self, pose_bone: Any, count: int) -> List[str]:
        """The bone chain ``count`` long ending at *pose_bone* (0 = to the root)."""
        armature = pose_bone.id_data
        chain, cur = [], pose_bone.bone
        while cur is not None and (count <= 0 or len(chain) < count):
            chain.append(self._ensure_bone(armature, cur))
            cur = cur.parent
        return list(reversed(chain))

    # ---- drivers ----------------------------------------------------------
    def _read_drivers(self, obj: Any) -> None:
        anim = getattr(obj, "animation_data", None)
        if anim is None:
            return
        for fc in anim.drivers:
            if fc.mute:
                continue
            self._count("driver")
            label = f"{obj.name}.{fc.data_path}[{fc.array_index}]"
            channel = self._CHANNEL_OF.get((fc.data_path, fc.array_index))
            if channel is None and fc.data_path.startswith('["'):
                channel = fc.data_path[2:-2]
            target_plug = f"{self._ensure_node(obj)}.{channel}" if channel else None
            variables = list(fc.driver.variables)
            source_plug = self._var_plug(variables[0]) if len(variables) == 1 else None
            if target_plug is None or source_plug is None:
                self._opaque("driver", label, [self._ensure_node(obj)])
                continue
            role = variables[0].name
            points = [tuple(k.co) for k in fc.keyframe_points]
            # Blender 5.1 seeds every new driver F-curve with two IDENTITY
            # keyframes (measured); only a curve off the y = x line is a remap.
            if len(points) == 2 and all(abs(x - y) < 1e-9 for x, y in points):
                points = []
            expr = (
                fc.driver.expression.strip() if fc.driver.type == "SCRIPTED" else role
            )
            if len(points) >= 2 and expr == role:
                interp = (
                    "linear"
                    if all(k.interpolation == "LINEAR" for k in fc.keyframe_points)
                    else "bezier"
                )
                self._emit(
                    "channel",
                    "curve",
                    target_plug,
                    [{"plug": source_plug, "role": "a"}],
                    {
                        "points": [[float(x), float(y), 0.0, 0.0] for x, y in points],
                        "interp": interp,
                    },
                    [f"driver:{label}"],
                )
                continue
            match = self._LINEAR_RE.match(expr)
            if match and match.group("v") == role and not points:
                self._emit(
                    "channel",
                    "linear",
                    target_plug,
                    [{"plug": source_plug, "role": "a"}],
                    {
                        "scale": float(match.group("k")),
                        "offset": float(match.group("b") or 0.0),
                    },
                    [f"driver:{label}"],
                )
                continue
            if expr == role and not points:
                self._emit(
                    "channel",
                    "linear",
                    target_plug,
                    [{"plug": source_plug, "role": "a"}],
                    {"scale": 1.0, "offset": 0.0},
                    [f"driver:{label}"],
                )
                continue
            self._opaque("driver", label, [self._ensure_node(obj)])

    # ---- deformer bindings --------------------------------------------------
    def _read_skin(self, obj: Any) -> None:
        """An ARMATURE modifier -> one ``points/skin`` record: the BINDING
        (which bones deform this object -- the deform bones its vertex groups
        name, or every deform bone when it has none), never the weights, which
        travel with the carrier. Mirror of the Maya extractor's skinCluster
        reader: the deformer edge is a graph edge, so a rig whose skin did not
        travel is incomplete rather than "complete" at its controls (schema
        15.6)."""
        for mod in getattr(obj, "modifiers", ()):
            if mod.type != "ARMATURE" or mod.object is None:
                continue
            self._count("armature_modifier")
            armature = mod.object
            groups = {g.name for g in getattr(obj, "vertex_groups", ())}
            bones = [
                b
                for b in armature.data.bones
                if b.use_deform and (not groups or b.name in groups)
            ]
            label = f"{obj.name}.{mod.name}"
            if not bones:
                self._opaque("armature_modifier", label, [self._ensure_node(obj)])
                continue
            self._emit(
                "points",
                "skin",
                {"id": self._ensure_node(obj), "points": "all"},
                [
                    {"id": self._ensure_bone(armature, b), "role": "influence"}
                    for b in bones
                ],
                {"geometry": self._GEOMETRY_KIND.get(obj.type, "mesh")},
                [f"armature_modifier:{label}"],
            )

    def _var_plug(self, var: Any) -> Optional[str]:
        """``<id>.<channel>`` for a one-target driver variable, or None."""
        target = var.targets[0]
        source = getattr(target, "id", None)
        if source is None:
            return None
        if var.type == "TRANSFORMS":
            channel = self._VAR_CHANNEL.get(str(target.transform_type))
            return f"{self._ensure_node(source)}.{channel}" if channel else None
        if var.type == "SINGLE_PROP":
            path = str(target.data_path)
            if path.startswith('["') and path.endswith('"]'):
                name = path[2:-2]
                self._ensure_attr(source, name)
                return f"{self._ensure_node(source)}.{name}"
            parsed = re.match(r"^(location|rotation_euler|scale)\[(\d)\]$", path)
            if parsed:
                channel = self._CHANNEL_OF.get((parsed.group(1), int(parsed.group(2))))
                return f"{self._ensure_node(source)}.{channel}" if channel else None
        return None


class RigGraphExtractor(_RigGraphExtractorInternal, ptk.HelpMixin):
    """Extract a Blender scene's rig logic into a RigGraph document (plain dict).

    Example:
        >>> data = RigGraphExtractor().extract()
        >>> graph = ptk.RigGraph.from_dict(data)
        >>> graph.validate(), graph.coverage()["unaccounted"]
        ([], 0)
    """

    def extract(self, objects: Optional[Sequence[Any]] = None) -> Dict[str, Any]:
        """Read the scene's constraints and drivers into a RigGraph envelope.

        Parameters:
            objects: The objects to read; every object in ``bpy.data`` when
                omitted (a driver outside a selection still moves what is
                inside it, and a partial census cannot promise coverage).

        Returns:
            dict: A ``RigGraph`` document whose ``source.census`` counts every
                constraint type and driver read, each of which appears in some
                record's ``provenance``.
        """
        import bpy

        self._nodes.clear()
        self._records.clear()
        self._census = {}
        for obj in list(objects) if objects is not None else list(bpy.data.objects):
            owner_id = None
            if obj.constraints:
                owner_id = self._ensure_node(obj)
                self._read_constraints(obj, obj.constraints, owner_id, obj.name)
            if obj.type == "ARMATURE":
                for pb in obj.pose.bones:
                    if pb.constraints:
                        self._read_constraints(
                            pb,
                            pb.constraints,
                            self._ensure_bone(obj, pb.bone),
                            f"{obj.name}:{pb.name}",
                        )
            self._read_drivers(obj)
            self._read_skin(obj)
        scene = bpy.context.scene
        return {
            "version": 1,
            "source": {
                "app": "blender",
                "app_version": bpy.app.version_string,
                "up_axis": "z",
                "linear_unit": "m",
                "angular_unit": "rad",
                "fps": float(scene.render.fps / (scene.render.fps_base or 1.0)),
                "frame_range": [float(scene.frame_start), float(scene.frame_end)],
                "rest_frame": float(scene.frame_current),
                "census": dict(self._census),
            },
            "nodes": list(self._nodes.values()),
            "records": list(self._records),
        }
