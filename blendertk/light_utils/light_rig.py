# !/usr/bin/python
# coding=utf-8
"""Light Rig manager (Blender): save, manage, swap and restore named light rigs.

Mirror of mayatk's ``light_utils.light_rig`` (name + behavior; the mechanism is
Blender's). The engine is pythontk's, written once (``ptk.LightRig``):
:class:`BlenderLightRigHost` is what it asks of Blender, and :class:`LightRig`
binds it with Blender's tables and overrides nothing. One shared Light Rigs
panel drives it, as it drives Maya's.

**Groups.** A rig's group is a collection: its lights are the light objects
in it (and in its children). Apply keeps the collection's place under its
parent; Remove deletes the objects only the group holds and the light data
only they used.

**Units and axes.** A payload is centimetres, degrees and Y-up whatever the
scene's units (``ptk.LightRigPayload``), so a rig saved in Maya lands in
Blender where it was: positions and orientations go through the Y-up <-> Z-up
change of basis, lengths through the scene's ``scale_length`` (``Units``), and
every length or angle a light's ``attrs`` hold through its property's unit
(``LENGTH`` in centimetres, ``ROTATION`` in degrees). A light's local axes are
the same in both hosts: it shines down its own -Z.

**What carries.** A light's type and settings by their neutral names
(``ptk.LightRigLight``: ``spot``, ``cone``, ``radius`` ...) and its own
attributes by Blender's. A Blender capture writes the neutral type, so another
host builds it. What Blender has no equivalent for is reported in Apply's
``skipped``, never guessed: a Maya node type, a Maya attribute, a texture on a
light, a light filter (a gobo), a setting with no Blender twin (``penumbra``,
``dropoff``, ``samples``, ``decay``, ``roundness``, ``spread`` as a 0..1
fraction). Intensities carry as numbers: Blender's ``energy`` is watts.

**Overrides.** An override sets a property of an object, its data, a material
or a collection by name, or by a data path under it
(``node_tree.nodes["Principled BSDF"].inputs["Emission Strength"].default_value``).
A property a driver or an F-curve drives is left alone and reported: Blender
cannot hand a driver back the way Maya reconnects a plug.
"""

import contextlib
import math
from typing import Any, ContextManager, Dict, List, Optional, Tuple

import pythontk as ptk

from blendertk.core_utils._core_utils import CoreUtils
from blendertk.core_utils.units import Units


class _BlenderLightRigHostInternal(object):
    """Blender reads and writes behind :class:`BlenderLightRigHost`."""

    #: The light data types Blender builds.
    LIGHT_TYPES = ("POINT", "SPOT", "AREA", "SUN")
    #: Blender's light type -> the neutral one a capture writes.
    NEUTRAL_TYPES = {"POINT": "point", "SPOT": "spot", "AREA": "area", "SUN": "directional"}
    #: Object properties a rig carries beside its light data's (their defaults
    #: read from a fresh light object: a light is unseen by camera rays).
    OBJECT_ATTRS = (
        "visible_camera",
        "visible_diffuse",
        "visible_glossy",
        "visible_transmission",
        "visible_volume_scatter",
        "visible_shadow",
    )
    #: Light data properties that are bookkeeping, never part of a rig.
    _SKIP = frozenset(
        {
            "rna_type",
            "name",
            "type",
            "tag",
            "is_runtime_data",
            "use_extra_user",
            "use_fake_user",
            "use_nodes",
            "is_evaluated",
            "is_missing",
            "is_embedded_data",
            "is_library_indirect",
        }
    )
    #: Blender's rotation modes by the payload's rotate-order index.
    _ROTATION_MODES = ("XYZ", "YZX", "ZXY", "XZY", "YXZ", "ZYX")
    #: Y-up (the payload's) from Z-up (Blender's): ``(x, y, z) -> (x, z, -y)``.
    _TO_Y_UP = ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, -1.0, 0.0))
    #: ``{light type: ({attr: fresh value, payload units}, {})}``, once per session.
    _structure: Dict[str, Tuple[Dict[str, Any], Dict[str, str]]] = {}

    # ------------------------------------------------------------- the scene graph
    @staticmethod
    def _id(node: str):
        """The datablock *node* names: an object, a material, a light or a collection."""
        import bpy

        for pool in (bpy.data.objects, bpy.data.materials, bpy.data.lights, bpy.data.collections):
            found = pool.get(node)
            if found is not None:
                return found
        return None

    @staticmethod
    def _tree(collection) -> set:
        return {collection} | set(collection.children_recursive)

    @classmethod
    def _lights(cls, group: str) -> list:
        import bpy

        collection = bpy.data.collections.get(group)
        if collection is None:
            return []
        return [o for o in collection.all_objects if o.type == "LIGHT"]

    # ------------------------------------------------------------- values and units
    @staticmethod
    def _prop(container, name: str):
        props = getattr(getattr(container, "bl_rna", None), "properties", None)
        return props.get(name) if props is not None else None

    @classmethod
    def _resolve(cls, owner, attr: str) -> Optional[Tuple[Any, Any, str]]:
        """``(container, its property, name)`` for *attr* on an object's data or
        else on *owner* itself (a light's ``color`` is its data's, never the
        object's viewport colour): a property name or a data path under it."""
        base, _, name = attr.rpartition(".")
        for candidate in (getattr(owner, "data", None), owner):
            if candidate is None:
                continue
            try:
                container = candidate.path_resolve(base) if base else candidate
            except (ValueError, AttributeError):
                continue
            prop = cls._prop(container, name)
            if prop is not None and not prop.is_readonly:
                return container, prop, name
        return None

    @staticmethod
    def _factor(prop) -> Optional[float]:
        """Payload units per scene unit for a length or an angle property, else None."""
        unit = getattr(prop, "unit", "NONE")
        if unit == "LENGTH":
            return Units.convert(1.0, "internal", "cm")
        if unit == "ROTATION":
            return math.degrees(1.0)
        return None

    @classmethod
    def _read(cls, container, prop, name: str) -> Any:
        """*name*'s value on *container*, in the payload's units (a list for an array)."""
        value = getattr(container, name)
        if getattr(prop, "is_array", False):
            value = list(value)
        factor = cls._factor(prop)
        if factor is not None:
            value = [v * factor for v in value] if isinstance(value, list) else value * factor
        if isinstance(value, list) and getattr(prop, "type", "") == "BOOLEAN":
            value = [bool(v) for v in value]
        return value

    @classmethod
    def _write(cls, container, prop, name: str, value: Any) -> None:
        """Set *name* on *container* from a payload-units *value*."""
        factor = cls._factor(prop)
        if factor is not None:
            value = (
                [v / factor for v in value] if isinstance(value, (list, tuple)) else value / factor
            )
        setattr(container, name, value)

    @staticmethod
    def _animated(container, name: str) -> Optional[str]:
        """``"driven"`` / ``"animated"`` when a driver or an F-curve sets the property
        (on the datablock that holds it: an object's light data is its own)."""
        anim = getattr(container.id_data, "animation_data", None)
        if anim is None:
            return None
        try:
            path = container.path_from_id(name)
        except (ValueError, TypeError, AttributeError):
            return None
        if any(d.data_path == path for d in anim.drivers):
            return "driven"
        action = anim.action
        curves = getattr(action, "fcurves", None) or []
        if any(c.data_path == path for c in curves):
            return "animated"
        return None

    @classmethod
    def _captured(cls, container, skip=()) -> Dict[str, Any]:
        """Every settable scalar or array property of *container*, in payload units."""
        out = {}
        for prop in container.bl_rna.properties:
            name = prop.identifier
            if prop.is_readonly or name in cls._SKIP or name in skip:
                continue
            if prop.type not in ("BOOLEAN", "INT", "FLOAT", "STRING", "ENUM"):
                continue
            if prop.type == "ENUM" and getattr(prop, "is_enum_flag", False):
                continue
            out[name] = cls._read(container, prop, name)
        return out

    # ------------------------------------------------------------- axes
    @classmethod
    def _y_up(cls):
        from mathutils import Matrix

        return Matrix(cls._TO_Y_UP)

    @classmethod
    def _world(cls, obj):
        """*obj*'s world matrix from its own and its parents' transforms: what
        ``matrix_world`` holds once the depsgraph has run, read now."""
        basis = obj.matrix_parent_inverse @ obj.matrix_basis
        return cls._world(obj.parent) @ basis if obj.parent else obj.matrix_basis.copy()

    @classmethod
    def _placement(cls, obj) -> Dict[str, Any]:
        """``pos`` / ``rot`` / ``scale`` / ``rotate_order`` of *obj*, Y-up cm and degrees."""
        loc, rot, _scale = cls._world(obj).decompose()
        to_y = cls._y_up()
        mode = obj.rotation_mode if obj.rotation_mode in cls._ROTATION_MODES else "XYZ"
        euler = (to_y @ rot.to_matrix()).to_euler(mode)
        out = {
            "pos": Units.convert(list(to_y @ loc), "internal", "cm"),
            "rot": [math.degrees(a) for a in euler],
        }
        scale = list(obj.scale)
        if any(abs(s - 1.0) > 1e-9 for s in scale):
            out["scale"] = scale
        if mode != "XYZ":
            out["rotate_order"] = cls._ROTATION_MODES.index(mode)
        return out

    @classmethod
    def _place(cls, obj, light: Dict[str, Any]) -> None:
        """Put *obj* where canonical *light* says (Y-up cm and degrees)."""
        from mathutils import Euler, Matrix, Vector

        mode = cls._ROTATION_MODES[light.get("rotate_order") or 0]
        to_z = cls._y_up().transposed()
        pos = Units.convert(list(light.get("pos") or (0, 0, 0)), "cm", "internal")
        rot = Euler([math.radians(a) for a in light.get("rot") or (0, 0, 0)], mode)
        obj.rotation_mode = mode
        obj.matrix_world = Matrix.LocRotScale(
            to_z @ Vector(pos), (to_z @ rot.to_matrix()).to_quaternion(), None
        )
        if light.get("scale"):
            obj.scale = light["scale"]


class BlenderLightRigHost(_BlenderLightRigHostInternal):
    """The ``ptk.LightRigHost`` Blender is: collections, light objects, and the
    ``light_rigs`` record on the scene store (:class:`DataNodes`)."""

    package = "blendertk"

    def payload_units(self) -> ContextManager:
        """A no-op: every read and write here converts itself (module docstring)."""
        return contextlib.nullcontext()

    def scene_units(self) -> Any:
        """Nothing to hand back: no Blender override reconnects a driver."""
        return None

    def undo_step(self, name: str) -> ContextManager:
        return CoreUtils.undo_chunk(name)

    def record_store(self) -> Any:
        from blendertk.node_utils.data_nodes import DataNodes

        return DataNodes

    def exists(self, node: str) -> bool:
        return self._id(node) is not None

    def defaults(self, kind: str) -> Tuple[Dict[str, Any], Dict[str, str]]:
        """A fresh light of *kind*'s properties (payload units) and its object's."""
        if kind not in self.LIGHT_TYPES:
            return {}, {}
        if kind not in self._structure:
            import bpy

            probe = bpy.data.lights.new("__light_rig_probe", kind)
            holder = bpy.data.objects.new("__light_rig_probe", probe)
            try:
                values = self._captured(probe)
                values.update({a: getattr(holder, a) for a in self.OBJECT_ATTRS})
            finally:
                bpy.data.objects.remove(holder)
                bpy.data.lights.remove(probe)
            self._structure[kind] = (values, {})
        return self._structure[kind]

    def prepare(self, kinds: set, group: str) -> None:
        for kind in kinds | {o.data.type for o in self._lights(group)}:
            self.defaults(kind)

    def capture_lights(self, group: str) -> List[Dict[str, Any]]:
        lights = []
        for obj in self._lights(group):
            kind = obj.data.type
            entry = {"name": obj.name, "type": self.NEUTRAL_TYPES.get(kind, kind)}
            entry.update(self._placement(obj))
            if obj.hide_render:
                entry["visible"] = False
            defaults, _ = self.defaults(kind)
            values = self._captured(obj.data)
            values.update({a: getattr(obj, a) for a in self.OBJECT_ATTRS})
            attrs = {
                k: v
                for k, v in values.items()
                if k not in defaults or not ptk.LightRig.same(v, defaults[k], 1e-6)
            }
            if attrs:
                entry["attrs"] = attrs
            user = {
                k: {"type": type(v).__name__, "value": v}
                for k, v in obj.data.items()
                if isinstance(v, (bool, int, float, str))
            }
            if user:
                entry["user_attrs"] = user
            lights.append(entry)
        return lights

    def remove_group(self, group: str) -> List[str]:
        import bpy

        collection = bpy.data.collections.get(group)
        if collection is None:
            return []
        tree = self._tree(collection)
        for obj in list(collection.all_objects):
            if set(obj.users_collection) <= tree:
                data = obj.data if obj.type == "LIGHT" else None
                bpy.data.objects.remove(obj)
                if data is not None and data.users == 0:
                    bpy.data.lights.remove(data)
            else:  # linked elsewhere too: it leaves the group, nothing more
                for owner in tree & set(obj.users_collection):
                    owner.objects.unlink(obj)
        for child in sorted(tree - {collection}, key=lambda c: -len(c.children_recursive)):
            bpy.data.collections.remove(child)
        bpy.data.collections.remove(collection)
        return [group]

    def group_parent(self, group: str) -> Optional[str]:
        import bpy

        collection = bpy.data.collections.get(group)
        if collection is None:
            return None
        for candidate in bpy.data.collections:
            if collection.name in candidate.children:
                return candidate.name
        return None

    def group_lights(
        self, made: List[str], group: str, parent: Optional[str]
    ) -> Tuple[str, List[str]]:
        import bpy

        collection = bpy.data.collections.new(group)
        host = bpy.data.collections.get(parent) if parent else None
        (host or bpy.context.scene.collection).children.link(collection)
        for name in made:
            obj = bpy.data.objects[name]
            for owner in list(obj.users_collection):
                owner.objects.unlink(obj)
            collection.objects.link(obj)
        return collection.name, list(made)

    def build_light(self, light: Dict[str, Any]) -> Tuple[Optional[str], List[str]]:
        import bpy

        name, kind = light["name"], light["type"]
        if kind not in self.LIGHT_TYPES:
            return None, [f"{name}: a {kind} is not a Blender light, left out"]
        data = bpy.data.lights.new(name, kind)
        obj = bpy.data.objects.new(name, data)
        bpy.context.scene.collection.objects.link(obj)
        skipped = (
            [] if obj.name == name else [f"{name}: the name was taken, built as {obj.name}"]
        )
        self._place(obj, light)
        if light.get("visible") is False:
            obj.hide_render = obj.hide_viewport = True
        for attr, value in (light.get("attrs") or {}).items():
            found = self._resolve(obj, attr)
            if found is None:
                skipped.append(f"{name}.{attr}: a {kind} light has no such attribute")
                continue
            try:
                self._write(*found, value)
            except (TypeError, ValueError) as e:
                skipped.append(f"{name}.{attr}: {e}")
        for attr, spec in (light.get("user_attrs") or {}).items():
            data[attr] = spec["value"]
        for attr in light.get("textures") or {}:
            skipped.append(f"{name}.{attr}: a Blender light takes no texture, left out")
        for filt in light.get("filters") or []:
            skipped.append(f"{name}: Blender lights carry no {filt['type']} filter, left out")
        return obj.name, skipped

    def gobo_filter(self, path: str, rotate: Optional[float]) -> Dict[str, Any]:
        out = {"type": "gobo", "textures": {"image": {"file": path}}}
        if rotate is not None:
            out["attrs"] = {"rotate": rotate}
        return out

    def plug(self, node: str, attr: str) -> Optional[Tuple[str, str]]:
        owner = self._id(node)
        if owner is None or self._resolve(owner, attr) is None:
            return None
        plug = f"{node}.{attr}"
        return plug, plug

    def plug_key(self, plug: str) -> str:
        return plug

    def set_overrides(
        self, node: str, attrs: Dict[str, Any], scene_units: Any = None
    ) -> Tuple[List[str], List[str]]:
        owner = self._id(node)
        if owner is None:
            return [f"{node}: not in the scene"], []
        skipped = []
        for attr, value in attrs.items():
            found = self._resolve(owner, attr)
            if found is None:
                skipped.append(f"{node}.{attr}: no such attribute")
                continue
            if isinstance(value, dict):  # another host's driver: nothing here drives it
                skipped.append(f"{node}.{attr}: driven by {value.get('from')} elsewhere, left alone")
                continue
            held = self._animated(found[0], found[2])
            if held:
                skipped.append(f"{node}.{attr}: {held}, left alone")
                continue
            try:
                self._write(*found, value)
            except (TypeError, ValueError) as e:
                skipped.append(f"{node}.{attr}: {e}")
        return skipped, []

    def current_values(
        self, section: Optional[Dict[str, Dict[str, Any]]]
    ) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        for node, attrs in (section or {}).items():
            owner = self._id(node)
            if owner is None:
                continue
            for attr in attrs:
                found = self._resolve(owner, attr)
                if found is not None:
                    out.setdefault(node, {})[attr] = self._read(*found)
        return out


class LightRig(ptk.LightRig):
    """Save, manage, swap and restore named light rigs (Blender; mirror of
    ``mtk.LightRig``). See the module docstring and ``ptk.LightRig``.

    Example:
        >>> rig = LightRig(group="MOODY_LIGHTS")
        >>> rig.save("moody_v1", description="first pass")
        >>> rig.apply("moody_v2")   # the scene's rig swapped; the outgoing one kept
        >>> rig.swap_back()         # and back again
    """

    HOST = BlenderLightRigHost()
    HOST_TYPES = {
        "spot": "SPOT",
        "point": "POINT",
        "area": "AREA",
        "aiArea": "AREA",
        "directional": "SUN",
        # Blender's IES light is a point light with a profile node: the profile
        # does not carry, the lamp does.
        "photometric": "POINT",
    }
    #: A setting Blender has no twin for keeps a name no Blender light has, so
    #: Apply reports it (module docstring).
    HOST_SETTINGS = {
        "intensity": "energy",
        "exposure": "exposure",
        "cone": "spot_size",
        "penumbra": "penumbra",
        "dropoff": "dropoff",
        "radius": "shadow_soft_size",
        # Blender's spread is an angle; the neutral one a 0..1 fraction.
        "spread": "spread_fraction",
        "normalize": "normalize",
        "samples": "samples",
        "decay": "decay",
        "specular": "specular_factor",
        "diffuse": "diffuse_factor",
        "volume": "volume_factor",
        "roundness": "roundness",
        "camera": "visible_camera",
        "color": "color",
    }
    HOST_FIXTURE_MODES = {
        "glow": (False, False),
        "emissive": (True, False),
        "lamp": (False, True),
    }
    DIFFUSE_VISIBILITY = ("visible_diffuse",)
    KELVIN_ATTRS = ("use_temperature", "temperature")
