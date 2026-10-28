# !/usr/bin/python
# coding=utf-8
"""Channels — Blender attribute query / mutation logic.

Blender mirror of mayatk's ``node_utils.attributes.channels._channels.Channels``. Where Maya
exposes arbitrary node attributes via ``cmds.listAttr`` / ``getAttr`` / ``setAttr``, Blender's
"channels" are an object's **transform channels** (location / rotation / scale, per-axis) plus
its **custom properties** (ID properties). This class encapsulates the query, filtering,
connection classification, table-data building, and mutation so that ``ChannelsSlots`` only
handles UI wiring — exactly the engine/slots split mayatk uses.

A *channel* is described by a plain dict (see :meth:`_describe`) carrying the friendly ``name``,
the Blender ``data_path`` + array ``index`` (so values, locks and keyframes resolve uniformly),
the ``kind`` (``"transform"`` / ``"custom"``), the display ``type`` and an ``is_angle`` flag.
Every public method taking a channel also takes its NAME (``location_x``, a custom property's
key), resolved per object by :meth:`_channel`: tentacle's shared Channels panel keys its rows
by name and calls this class by mayatk's names (the "Panel surface" section).

``import bpy`` is deferred into the call bodies (no import side effects) so the module — and the
package surface — resolves under headless ``--background`` without a running Blender.
"""

import math

import pythontk as ptk

from blendertk.core_utils._core_utils import CoreUtils


class Channels:
    """Blender attribute query / mutation logic.

    Encapsulates channel querying, filtering, connection classification, and mutation so that
    ``ChannelsSlots`` only handles UI wiring. Operates on ``bpy.types.Object`` references (the
    Blender analogue of Maya node-name strings).

    Every mutation is one undo step (:meth:`CoreUtils.undo_chunk`), as each is one undo chunk
    in mayatk: the panel writes through RNA from the Qt pump, which pushes no step of its own.
    Nested inside a slots gesture bracket (a drag, a fill, a scrub) they fold into its step.
    """

    # Maps filter ComboBox items → an opaque filter key consumed by :meth:`collect_channels`.
    # Mirrors mayatk's ``FILTER_MAP`` (Maya-only keys — Channel Box / Settable / Keyed — collapse
    # onto Blender's smaller, native channel model).
    FILTER_MAP = {
        "Custom": "custom",
        "Keyable": "keyable",
        "Locked": "locked",
        "Animated": "animated",
        "All": "all",
    }

    # Canonical channel-box ordering: Location → Rotation → Scale, then custom props alphabetically.
    _CHANNEL_BOX_ORDER = [
        "location_x",
        "location_y",
        "location_z",
        "rotation_x",
        "rotation_y",
        "rotation_z",
        "scale_x",
        "scale_y",
        "scale_z",
    ]

    # (friendly name, data_path, array index, is_angle) for the nine transform channels.
    _TRANSFORM_CHANNELS = [
        ("location_x", "location", 0, False),
        ("location_y", "location", 1, False),
        ("location_z", "location", 2, False),
        ("rotation_x", "rotation_euler", 0, True),
        ("rotation_y", "rotation_euler", 1, True),
        ("rotation_z", "rotation_euler", 2, True),
        ("scale_x", "scale", 0, False),
        ("scale_y", "scale", 1, False),
        ("scale_z", "scale", 2, False),
    ]

    # data_path → the matching ``lock_*`` array on the object.
    _LOCK_ATTR = {
        "location": "lock_location",
        "rotation_euler": "lock_rotation",
        "scale": "lock_scale",
    }

    #: The display types (:meth:`get_attr_type`) a value cell scrubs and wheel-steps;
    #: :attr:`INTEGER_TYPES` step in whole units. Mirror of mayatk's (there Maya's
    #: storage types).
    SCRUBBABLE_TYPES = frozenset({"float", "int"})
    INTEGER_TYPES = frozenset({"int"})
    #: The connection states (:meth:`classify_connection`) a plain click on the panel's
    #: key icon keys. A constrained object's channels key like any other (the constraint
    #: applies over them), and so does a muted F-curve; a driver overrides its channel,
    #: so it is only removed (Ctrl). Mirror of mayatk's (there only the keyframe states).
    KEYABLE_STATES = frozenset(
        {
            "none",
            "keyframe",
            "keyframe_active",
            "keyframe_changed",
            "constraint",
            "muted",
        }
    )
    #: The custom-property types :meth:`create_attribute` makes, in the panel's order.
    #: Maya's ``double3`` is ``vector``; an arbitrary object has no enum property.
    CREATE_TYPES = ("float", "int", "bool", "string", "vector")
    #: Blender's own editors the panel's header opens (:meth:`open_editor`):
    #: ``{key: (label, tooltip)}``.
    EDITORS = {
        "properties": (
            "Properties Editor",
            "Open Blender's Properties editor (Object tab) in a new window.",
        ),
        "drivers": ("Drivers Editor", "Open Blender's Drivers editor in a new window."),
        "graph": (
            "Graph Editor",
            "Open Blender's Graph editor (F-curves) in a new window.",
        ),
    }
    _EDITOR_ARGS = {
        "properties": ("Properties", "OBJECT"),
        "drivers": ("Drivers", None),
        "graph": ("Graph Editor", None),
    }

    def __init__(self):
        self._pinned_targets = None
        self._single_object_mode = False
        self._clipboard = {}

    # ------------------------------------------------------------------
    # Target resolution
    # ------------------------------------------------------------------

    @property
    def is_pinned(self):
        return self._pinned_targets is not None

    @property
    def single_object_mode(self):
        return self._single_object_mode

    @single_object_mode.setter
    def single_object_mode(self, value):
        self._single_object_mode = bool(value)

    def pin_targets(self, objects):
        """Pin the manager to a fixed object list; ``None`` (or empty) clears the pin."""
        self._pinned_targets = list(objects) if objects else None

    def get_selected_nodes(self):
        """Return the target object list.

        When pinned, returns the cached list filtered to objects still present in the blend file;
        otherwise the current selection. In ``single_object_mode`` only the active object is
        returned (mirror of Maya's "most recently selected"). Without Blender (no ``bpy``: the
        panel loading in plain Qt) there are none.
        """
        try:
            import bpy
        except ImportError:
            return []

        if self._pinned_targets is not None:
            names = {o.name for o in bpy.data.objects}
            # Tolerate a deleted pinned object per element: reading ``.name`` on a dead
            # StructRNA raises ReferenceError (getattr's default only catches AttributeError),
            # so skip the removed reference instead of aborting the whole resolution.
            objs = []
            for o in self._pinned_targets:
                try:
                    if o.name in names:
                        objs.append(o)
                except (ReferenceError, AttributeError):
                    continue
        else:
            # not bpy.context's screen members: absent windowless (the Qt pump)
            objs = list(CoreUtils.selected_objects())
        if self._single_object_mode and len(objs) > 1:
            active = CoreUtils.active_object()
            return [active] if active in objs else [objs[-1]]
        return objs

    # ------------------------------------------------------------------
    # Channel description / querying
    # ------------------------------------------------------------------

    @staticmethod
    def _describe(name, data_path, index, kind, type_str, is_angle=False):
        """Build a channel-descriptor dict (the unit every other method consumes)."""
        return {
            "name": name,
            "data_path": data_path,
            "index": index,
            "kind": kind,
            "type": type_str,
            "is_angle": is_angle,
        }

    @classmethod
    def _transform_descriptors(cls):
        """The nine transform-channel descriptors (object-independent)."""
        return [
            cls._describe(n, dp, i, "transform", "float", is_angle)
            for (n, dp, i, is_angle) in cls._TRANSFORM_CHANNELS
        ]

    @staticmethod
    def _custom_prop_keys(obj):
        """User-defined (ID) property keys on *obj*.

        Excludes Blender's internal ``_RNA_UI`` / underscore keys, and the freeze bake
        props (``btk_*_bake``) that :func:`btk.freeze_transforms` stamps so Unfreeze can
        reverse the apply — those are bookkeeping, not user channels, and must never
        surface as table rows.
        """
        from blendertk.xform_utils._xform_utils import _BAKE_T, _BAKE_R, _BAKE_S

        internal = {"_RNA_UI", _BAKE_T, _BAKE_R, _BAKE_S}
        keys = []
        for key in obj.keys():
            if key in internal or key.startswith("_"):
                continue
            keys.append(key)
        return keys

    @classmethod
    def _custom_descriptors(cls, obj):
        """Descriptors for *obj*'s custom properties (data_path is the ``["key"]`` RNA path)."""
        out = []
        for key in cls._custom_prop_keys(obj):
            out.append(
                cls._describe(
                    key, f'["{key}"]', None, "custom", cls._value_type(obj[key])
                )
            )
        return out

    @classmethod
    def _channel(cls, obj, channel):
        """The descriptor for *channel* on *obj*, or ``None`` when *obj* has no such channel.

        *channel* is a descriptor (returned as is) or a channel NAME -- the panel's row key,
        mayatk's attribute name -- resolved to the transform channel of that name or *obj*'s
        custom property of that key (internal ``_`` and freeze-bake keys excluded).
        """
        if isinstance(channel, dict):
            return channel
        for d in cls._transform_descriptors():
            if d["name"] == channel:
                return d
        try:
            if channel in cls._custom_prop_keys(obj):
                return cls._describe(
                    channel,
                    f'["{channel}"]',
                    None,
                    "custom",
                    cls._value_type(obj[channel]),
                )
        except (AttributeError, KeyError, ReferenceError, TypeError):
            pass
        return None

    @classmethod
    def _first_channel(cls, objects, channel):
        """*channel*'s descriptor on the first of *objects* that has it, else ``None``."""
        for obj in objects or ():
            d = cls._channel(obj, channel)
            if d is not None:
                return d
        return None

    @staticmethod
    def _channel_name(channel):
        return channel["name"] if isinstance(channel, dict) else channel

    @staticmethod
    def _value_type(val):
        """Friendly display type for a Python value pulled from an ID property."""
        if isinstance(val, bool):
            return "bool"
        if isinstance(val, int):
            return "int"
        if isinstance(val, float):
            return "float"
        if isinstance(val, str):
            return "string"
        if hasattr(val, "__len__"):
            return "vector"
        return "?"

    @classmethod
    def collect_channels(cls, objects, filter_key="custom", invert=False):
        """Return the channel descriptors shared across all *objects* for the given filter.

        Intersection semantics mirror Maya's ``collect_attr_names``: a channel is included only
        when present on every object (transform channels are common to all objects; custom props
        intersect by key). Sorted into canonical channel-box order.
        """
        if not objects:
            return []

        per_object = [cls._channels_for_object(o, filter_key) for o in objects]
        # Intersect by channel name, keeping the first object's descriptor for display.
        common_names = set(d["name"] for d in per_object[0])
        for descs in per_object[1:]:
            common_names &= set(d["name"] for d in descs)

        if invert:
            all_names = set()
            for o in objects:
                all_names |= set(d["name"] for d in cls._channels_for_object(o, "all"))
            common_names = all_names - common_names

        # Resolve names → descriptors against the primary object (falls back to a transform
        # descriptor or a typeless custom descriptor when only the inverted set knows the name).
        primary = objects[0]
        by_name = {d["name"]: d for d in cls._channels_for_object(primary, "all")}
        result = [by_name[n] for n in common_names if n in by_name]
        return cls._sort_channel_box(result)

    @classmethod
    def _channels_for_object(cls, obj, filter_key):
        """All descriptors on *obj* matching *filter_key* (no intersection / sort)."""
        transform = cls._transform_descriptors()
        custom = cls._custom_descriptors(obj)

        if filter_key == "custom":
            return custom
        if filter_key == "all" or filter_key == "keyable":
            return transform + custom
        if filter_key == "locked":
            return [d for d in transform if cls.is_locked(obj, d)]
        if filter_key == "animated":
            return [
                d
                for d in (transform + custom)
                if cls.classify_connection(obj, d) != "none"
            ]
        return transform + custom

    @classmethod
    def _sort_channel_box(cls, descriptors):
        """Sort descriptors: transform channels in fixed order, then custom props alphabetically."""
        priority = {name: i for i, name in enumerate(cls._CHANNEL_BOX_ORDER)}
        ordered, remaining = [], []
        for d in descriptors:
            (ordered if d["name"] in priority else remaining).append(d)
        ordered.sort(key=lambda d: priority[d["name"]])
        remaining.sort(key=lambda d: d["name"])
        return ordered + remaining

    # ------------------------------------------------------------------
    # Values
    # ------------------------------------------------------------------

    @classmethod
    def get_channel_value(cls, obj, descriptor):
        """Return the raw Python value for *descriptor* on *obj* (``None`` on failure).

        Angle channels are converted radians → degrees so the table shows degrees (Blender's UI
        convention); :meth:`parse_value` reverses it on edit.
        """
        try:
            descriptor = cls._channel(obj, descriptor)
            if descriptor is None:
                return None
            if descriptor["kind"] == "transform":
                val = getattr(obj, descriptor["data_path"])[descriptor["index"]]
                return math.degrees(val) if descriptor["is_angle"] else val
            return obj[descriptor["name"]]
        except Exception:
            return None

    @staticmethod
    def _fmt_float(val, decimals=4):
        """Format a float, stripping trailing zeros and a bare trailing decimal point."""
        s = f"{val:.{decimals}f}"
        if "." in s:
            s = s.rstrip("0").rstrip(".")
        return s

    @classmethod
    def format_value(cls, val):
        """Convert a channel value to a display string (``"*"`` marks a mixed multi-selection)."""
        if isinstance(val, str) and val == "*":
            return "*"
        if val is None:
            return ""
        if isinstance(val, bool):
            return str(val)
        if isinstance(val, float):
            return cls._fmt_float(val)
        if (
            isinstance(val, (list, tuple))
            or hasattr(val, "__len__")
            and not isinstance(val, str)
        ):
            inner = ", ".join(
                cls._fmt_float(v) if isinstance(v, float) else str(v) for v in val
            )
            return f"({inner})"
        return str(val)

    @staticmethod
    def _same_value(a, b):
        """Value equality that is safe for Blender array properties (which don't return a scalar
        bool from ``!=``). Array-likes compare element-wise as lists; scalars compare directly."""
        try:
            a_arr = hasattr(a, "__len__") and not isinstance(a, str)
            b_arr = hasattr(b, "__len__") and not isinstance(b, str)
            if a_arr or b_arr:
                return a_arr and b_arr and list(a) == list(b)
            return a == b
        except Exception:
            return False

    @classmethod
    def parse_value(cls, text, descriptor):
        """Convert user-entered *text* to a Python value for *descriptor* (``None`` = skip).

        Angle channels parse degrees → radians (the inverse of :meth:`get_channel_value`).
        """
        t = descriptor["type"]
        try:
            if t == "bool":
                return text.strip().lower() in ("1", "true", "yes", "on")
            if t == "int":
                return int(float(text))
            if t == "string":
                return text
            if t == "vector":
                # Round-trips format_value's "(x, y, z)" (also accepts "x, y, z" / "[x, y, z]").
                stripped = text.strip().lstrip("([").rstrip(")]")
                nums = [float(x) for x in stripped.split(",") if x.strip()]
                return tuple(nums) if nums else None
            if t in ("float",):
                val = float(text)
                return math.radians(val) if descriptor["is_angle"] else val
        except (ValueError, TypeError):
            return None
        return None

    # ------------------------------------------------------------------
    # Lock state
    # ------------------------------------------------------------------

    @classmethod
    def is_locked(cls, obj, descriptor):
        """Lock state for *descriptor* (or a channel name). Only transform channels lock."""
        descriptor = cls._channel(obj, descriptor)
        if descriptor is None or descriptor["kind"] != "transform":
            return False
        lock_attr = cls._LOCK_ATTR.get(descriptor["data_path"])
        if not lock_attr:
            return False
        try:
            return bool(getattr(obj, lock_attr)[descriptor["index"]])
        except Exception:
            return False

    @classmethod
    def toggle_lock(cls, objects, descriptor):
        """Toggle the lock state of *descriptor* across *objects* (transform channels only)."""
        descriptor = cls._first_channel(objects, descriptor)
        if descriptor is None or descriptor["kind"] != "transform":
            return
        new_state = not cls.is_locked(objects[0], descriptor)
        cls.set_lock(objects, [descriptor], new_state)

    @classmethod
    def set_lock(cls, objects, descriptors, lock):
        """Lock or unlock *descriptors* across all *objects* (transform channels only)."""
        with CoreUtils.undo_chunk(f"{'Lock' if lock else 'Unlock'} Attrs"):
            for obj in objects:
                for channel in descriptors:
                    d = cls._channel(obj, channel)
                    if d is None or d["kind"] != "transform":
                        continue
                    lock_attr = cls._LOCK_ATTR.get(d["data_path"])
                    if not lock_attr:
                        continue
                    try:
                        getattr(obj, lock_attr)[d["index"]] = bool(lock)
                    except Exception:
                        pass

    # ------------------------------------------------------------------
    # Connection / animation classification
    # ------------------------------------------------------------------

    @staticmethod
    def _iter_fcurves(obj):
        """Yield every animation F-curve owned by *obj*'s action.

        Handles both legacy (``action.fcurves``) and 4.4+ slotted actions (layers → strips →
        channelbag). Drivers are queried separately (``animation_data.drivers``).
        """
        ad = getattr(obj, "animation_data", None)
        if not ad:
            return
        act = getattr(ad, "action", None)
        if not act:
            return
        layers = getattr(act, "layers", None)
        if not layers:
            # Legacy (pre-4.4) action. On 4.4-5.0 ``action.fcurves`` survives only
            # as a proxy for the FIRST slot, so a layered action never reads it.
            yield from getattr(act, "fcurves", []) or []
            return
        # Slotted-action API (Blender 4.4+): resolve the object's bound slot's channelbag.
        slot = getattr(ad, "action_slot", None)
        for layer in layers:
            for strip in getattr(layer, "strips", []) or []:
                cb = None
                try:
                    cb = strip.channelbag(slot) if slot is not None else None
                except Exception:
                    cb = None
                if cb is not None:
                    yield from getattr(cb, "fcurves", []) or []

    @classmethod
    def _find_fcurve(cls, obj, descriptor):
        """Return the F-curve animating *descriptor*, or ``None``."""
        dp, idx = descriptor["data_path"], descriptor["index"]
        for fc in cls._iter_fcurves(obj):
            if fc.data_path == dp and (idx is None or fc.array_index == idx):
                return fc
        return None

    @classmethod
    def _find_driver(cls, obj, descriptor):
        """Return the driver F-curve on *descriptor*, or ``None``."""
        ad = getattr(obj, "animation_data", None)
        if not ad:
            return None
        dp, idx = descriptor["data_path"], descriptor["index"]
        for drv in getattr(ad, "drivers", []) or []:
            if drv.data_path == dp and (idx is None or drv.array_index == idx):
                return drv
        return None

    @classmethod
    def classify_connection(cls, obj, descriptor):
        """Classify what drives *descriptor* on *obj*.

        Returns one of ``"none"``, ``"keyframe"`` (animated, no key on the current frame),
        ``"keyframe_active"`` (key on the current frame), ``"keyframe_changed"`` (animated,
        but the value was moved off its F-curve without a key -- see :meth:`is_off_curve`),
        ``"muted"`` (F-curve or driver present but muted), ``"driven_key"`` (driver), or
        ``"constraint"`` (transform channel on a constrained object). Mirrors the Maya tool's
        connection states with Blender's drivers / F-curves. *descriptor* may be a channel name.
        """
        descriptor = cls._channel(obj, descriptor)
        if descriptor is None:
            return "none"
        drv = cls._find_driver(obj, descriptor)
        if drv is not None:
            return "muted" if getattr(drv, "mute", False) else "driven_key"
        fc = cls._find_fcurve(obj, descriptor)
        if fc is not None:
            if getattr(fc, "mute", False):
                return "muted"
            # A value moved off its curve outranks a key on this frame, as in mayatk:
            # either way it is lost on the next frame change.
            if cls._off_curve(obj, fc):
                return "keyframe_changed"
            return (
                "keyframe_active" if cls._has_key_at_current_frame(fc) else "keyframe"
            )
        if descriptor["kind"] == "transform" and getattr(obj, "constraints", None):
            return "constraint"
        return "none"

    @classmethod
    def is_off_curve(cls, obj, descriptor, tolerance=1e-6):
        """Return ``True`` if keyed *descriptor* on *obj* holds a value its F-curve does not.

        Mirror of mayatk's ``Channels.is_off_curve``. A keyed property set without a key
        keeps the new value until the next frame change re-evaluates the curve; Blender's
        own fields show it orange, the panel lighter red (``"keyframe_changed"``). Only a
        channel its F-curve drives is judged: a driver overrides the curve and a muted
        curve writes nothing.

        Parameters:
            obj (bpy.types.Object): The object.
            descriptor (dict): A channel descriptor (see :meth:`_describe`).
            tolerance (float): Relative tolerance of the comparison.

        Returns:
            bool
        """
        descriptor = cls._channel(obj, descriptor)
        if descriptor is None or cls._find_driver(obj, descriptor) is not None:
            return False
        fc = cls._find_fcurve(obj, descriptor)
        if fc is None or getattr(fc, "mute", False):
            return False
        return cls._off_curve(obj, fc, tolerance)

    @staticmethod
    def _off_curve(obj, fcurve, tolerance=1e-6):
        """True if *obj*'s value at *fcurve*'s path differs from the curve at the current frame.

        The raw RNA value against ``fcurve.evaluate``: radians on both sides for a
        rotation, and an integer curve already rounds as the animation write does
        (measured, Blender 5.1).
        """
        import bpy

        try:
            value = obj.path_resolve(fcurve.data_path)
            if hasattr(value, "__len__") and not isinstance(value, str):
                value = value[fcurve.array_index]
            curve = fcurve.evaluate(bpy.context.scene.frame_current_final)
        except (AttributeError, IndexError, ReferenceError, TypeError, ValueError):
            return False
        if not isinstance(value, (int, float)):
            return False
        scale = max(1.0, abs(value), abs(curve))
        return abs(value - curve) > tolerance * scale

    @staticmethod
    def _has_key_at_current_frame(fcurve):
        """True if *fcurve* has a keyframe exactly on the current frame."""
        import bpy

        frame = bpy.context.scene.frame_current
        for kp in fcurve.keyframe_points:
            if round(kp.co[0]) == frame:
                return True
        return False

    # ------------------------------------------------------------------
    # Table data
    # ------------------------------------------------------------------

    @classmethod
    def build_table_data(cls, objects, filter_key="custom", invert=False):
        """Build ``(rows, states)`` for the table.

        Each row is ``[name, "", "", value_str, type_str]``; each state is
        ``(is_locked, conn_type)``. A value differing across a multi-selection shows ``"*"``.
        *filter_key* may also be a ``FILTER_MAP`` label, or :meth:`get_filter_kwargs`' dict
        (mayatk's call shape: ``build_table_data(objects, get_filter_kwargs(label, invert))``).
        """
        if isinstance(filter_key, dict):
            return cls.build_table_data(objects, **filter_key)
        filter_key = cls.FILTER_MAP.get(filter_key, filter_key)
        if not objects:
            return [["", "", "", "", "No selection"]], [(False, "none")]

        descriptors = cls.collect_channels(objects, filter_key, invert)
        primary = objects[0]
        multi = len(objects) > 1

        rows, states = [], []
        for d in descriptors:
            val = cls.get_channel_value(primary, d)
            if multi:
                for other in objects[1:]:
                    if not cls._same_value(cls.get_channel_value(other, d), val):
                        val = "*"
                        break
            rows.append([d["name"], "", "", cls.format_value(val), d["type"]])
            states.append(
                (cls.is_locked(primary, d), cls.classify_connection(primary, d))
            )

        if not rows:
            rows = [["", "", "", "", "No channels"]]
            states = [(False, "none")]
        return rows, states

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    @classmethod
    def set_channel_value(cls, objects, descriptor, text):
        """Parse *text* and set *descriptor* (or a channel name) on all *objects*.

        One undo step; none when nothing took the value (unparseable, or no object has
        the channel).
        """
        with CoreUtils.undo_chunk(f"Edit Attr: {cls._channel_name(descriptor)}") as step:
            changed = False
            for obj in objects:
                d = cls._channel(obj, descriptor)
                value = cls.parse_value(text, d) if d is not None else None
                if value is None:
                    continue
                try:
                    if d["kind"] == "transform":
                        getattr(obj, d["data_path"])[d["index"]] = value
                    elif d["name"] in obj.keys():
                        obj[d["name"]] = value
                    else:
                        continue
                    changed = True
                except Exception:
                    pass
            if not changed:
                step.cancel()

    @classmethod
    def reset_to_default(cls, objects, descriptors):
        """Reset *descriptors* to their default values across all *objects*.

        Transform defaults: location/rotation → 0, scale → 1. Custom-prop defaults come from the
        property's ``id_properties_ui`` metadata (falls back to 0).
        """
        with CoreUtils.undo_chunk("Reset to Default"):
            for obj in objects:
                for channel in descriptors:
                    d = cls._channel(obj, channel)
                    if d is None:
                        continue
                    try:
                        if d["kind"] == "transform":
                            default = 1.0 if d["data_path"] == "scale" else 0.0
                            getattr(obj, d["data_path"])[d["index"]] = default
                        else:
                            cls._reset_custom_prop(obj, d["name"])
                    except Exception:
                        pass

    @staticmethod
    def _reset_custom_prop(obj, name):
        """Reset a custom property to its UI default (no-op if it has none)."""
        if name not in obj.keys():
            return
        try:
            ui = obj.id_properties_ui(name)
            default = ui.as_dict().get("default")
            if default is not None:
                obj[name] = default
        except (TypeError, KeyError, AttributeError):
            pass

    @classmethod
    def toggle_key_at_current_time(cls, objects, descriptor):
        """Set or remove a keyframe on *descriptor* at the current frame across *objects*.

        Decides set-vs-remove from the primary object so a batch stays consistent. Returns
        ``"set"`` / ``"removed"`` / ``None``.
        """
        descriptor = cls._first_channel(objects, descriptor)
        if descriptor is None:
            return None
        primary_fc = cls._find_fcurve(objects[0], descriptor)
        removing = bool(primary_fc and cls._has_key_at_current_frame(primary_fc))
        return cls.set_key_at_current_time(objects, descriptor, keyed=not removing)

    @classmethod
    def set_key_at_current_time(cls, objects, descriptor, keyed=True):
        """Set (``keyed=True``) or remove (``keyed=False``) the key on *descriptor* at the current
        frame across *objects*.

        Explicit rather than a toggle, so repeating it is harmless: inserting over an existing key
        re-keys the current value, and removing where no key sits does nothing. Returns ``"set"`` /
        ``"removed"`` / ``None``.
        """
        import bpy

        descriptor = cls._first_channel(objects, descriptor)
        if descriptor is None:
            return None
        frame = bpy.context.scene.frame_current
        dp, idx = descriptor["data_path"], descriptor["index"]
        index = -1 if idx is None else idx
        action = "Set" if keyed else "Remove"
        with CoreUtils.undo_chunk(f"{action} Key: {descriptor.get('name', dp)}"):
            for obj in objects:
                try:
                    if keyed:
                        obj.keyframe_insert(data_path=dp, index=index, frame=frame)
                    else:
                        obj.keyframe_delete(data_path=dp, index=index, frame=frame)
                except (RuntimeError, TypeError):
                    pass
        return "set" if keyed else "removed"

    @classmethod
    def break_connections(cls, objects, descriptor):
        """Remove the animation / driver on *descriptor* across *objects* (Maya's break-connection).

        Returns ``True`` if anything was removed.
        """
        descriptor = cls._first_channel(objects, descriptor)
        if descriptor is None:
            return False
        broke = False
        dp, idx = descriptor["data_path"], descriptor["index"]
        with CoreUtils.undo_chunk("Break Connection") as step:
            for obj in objects:
                ad = getattr(obj, "animation_data", None)
                if not ad:
                    continue
                drv = cls._find_driver(obj, descriptor)
                if drv is not None:
                    try:
                        obj.driver_remove(dp, -1 if idx is None else idx)
                        broke = True
                    except (RuntimeError, TypeError):
                        pass
                fc = cls._find_fcurve(obj, descriptor)
                if fc is not None and cls._remove_fcurve(obj, fc):
                    broke = True
            if not broke:
                step.cancel()
        return broke

    @staticmethod
    def _remove_fcurve(obj, fcurve):
        """Remove *fcurve* from whichever container owns it. ``True`` on success.

        Delegates to the package's one slot-aware remover rather than repeating that
        traversal. This copy used to try the flat ``action.fcurves`` first and catch
        (RuntimeError, TypeError, ReferenceError); Blender 5.x REMOVED that attribute,
        so the access raises AttributeError -- not in that tuple -- and the slotted
        branch beneath it was unreachable. :meth:`break_connections` propagated
        instead of removing anything (measured on 5.1.2: 3 curves before and after).
        """
        from blendertk.anim_utils._anim_utils import _AnimUtilsInternal

        anim = getattr(obj, "animation_data", None)
        action = getattr(anim, "action", None)
        if action is None:
            return False
        return _AnimUtilsInternal._remove_fcurve(
            action, getattr(anim, "action_slot", None), fcurve
        )

    @classmethod
    def set_mute(cls, objects, descriptors, mute=True):
        """Mute / unmute the F-curve (or driver) on each descriptor across *objects*.

        Blender mirror of Maya's channel mute: toggles ``fcurve.mute`` (and a driver
        F-curve's ``mute``). Channels with neither an F-curve nor a driver are skipped
        silently. Returns ``True`` when anything was toggled.
        """
        changed = False
        with CoreUtils.undo_chunk(f"{'Mute' if mute else 'Unmute'} Attrs") as step:
            for obj in objects:
                for channel in descriptors:
                    d = cls._channel(obj, channel)
                    if d is None:
                        continue
                    drv = cls._find_driver(obj, d)
                    if drv is not None:
                        try:
                            drv.mute = bool(mute)
                            changed = True
                            continue
                        except (AttributeError, TypeError):
                            pass
                    fc = cls._find_fcurve(obj, d)
                    if fc is not None:
                        try:
                            fc.mute = bool(mute)
                            changed = True
                        except (AttributeError, TypeError):
                            pass
            if not changed:
                step.cancel()
        return changed

    @classmethod
    def set_breakdown_key(cls, objects, descriptors):
        """Set a breakdown key on *descriptors* at the current frame across *objects*.

        Blender mirror of Maya's ``setKeyframe(breakdown=True)``: inserts a keyframe and
        flags it as a *breakdown* keyframe type (``kp.type = "BREAKDOWN"``). Returns the
        number of keyframes flagged.
        """
        import bpy

        if not objects:
            return 0
        frame = bpy.context.scene.frame_current
        count = 0
        with CoreUtils.undo_chunk("Set Breakdown Key"):
            for obj in objects:
                for channel in descriptors:
                    d = cls._channel(obj, channel)
                    if d is None:
                        continue
                    dp, idx = d["data_path"], d["index"]
                    try:
                        obj.keyframe_insert(
                            data_path=dp, index=-1 if idx is None else idx, frame=frame
                        )
                    except (RuntimeError, TypeError):
                        continue
                    fc = cls._find_fcurve(obj, d)
                    if fc is None:
                        continue
                    for kp in fc.keyframe_points:
                        if round(kp.co[0]) == frame:
                            kp.type = "BREAKDOWN"
                            count += 1
                    try:
                        fc.update()
                    except (AttributeError, RuntimeError):
                        pass
        return count

    @classmethod
    def select_connections(cls, objects, descriptor):
        """Select the object(s) driving *descriptor* on the primary object.

        Blender analogue of Maya's "Select Connection" (which selects the upstream source
        node): selects the driver's variable target object(s); failing that, a constrained
        transform channel's constraint target(s). Returns ``True`` if anything was selected.
        *descriptor* may be a channel name.
        """
        if not objects:
            return False
        obj = objects[0]
        descriptor = cls._channel(obj, descriptor)
        if descriptor is None:
            return False
        targets = []
        drv = cls._find_driver(obj, descriptor)
        if drv is not None:
            for var in getattr(drv.driver, "variables", []) or []:
                for tgt in getattr(var, "targets", []) or []:
                    tid = getattr(tgt, "id", None)
                    if tid is not None and tid != obj and tid not in targets:
                        targets.append(tid)
        if not targets and descriptor["kind"] == "transform":
            for con in getattr(obj, "constraints", []) or []:
                tid = getattr(con, "target", None)
                if tid is not None and tid not in targets:
                    targets.append(tid)
        if not targets:
            return False
        # The window's view layer, in any mode; the first target becomes active.
        CoreUtils.select(targets, mode="replace")
        return True

    # Map UI-friendly type names → (python default, is_float).
    _CREATE_DEFAULTS = {
        "float": (0.0, True),
        "int": (0, False),
        "bool": (False, False),
        "string": ("", False),
    }

    @classmethod
    def create_attribute(
        cls,
        objects,
        name,
        attr_type,
        min_val=None,
        max_val=None,
        default_val=0.0,
        keyable=True,
        enum_names="",
    ):
        """Create a custom (ID) property on *objects*.

        *keyable* and *enum_names* are mayatk's keywords, taken for one call shape and
        unused: every Blender custom property is keyable, and an arbitrary object has no
        enum property (:attr:`CREATE_TYPES` offers none).

        Parameters mirror the Maya tool's create-attribute form (numeric range + default). Maya's
        ``double3`` compound maps to a 3-element float ARRAY property (``vector``) — the direct
        Blender analogue (subtype ``'XYZ'``), which the table already displays/keys/edits per-index.
        Maya's ``enum`` has no arbitrary-object Blender analogue and is not offered. Skips objects
        that already carry the property.
        """
        with CoreUtils.undo_chunk(f"Create Attr: {name}"):
            for obj in objects:
                if name not in obj.keys():
                    cls._create_property(
                        obj, name, attr_type, min_val, max_val, default_val
                    )

    @staticmethod
    def _create_property(obj, name, attr_type, min_val, max_val, default_val):
        """Create one custom property on *obj* (see :meth:`create_attribute`)."""
        try:
            if attr_type == "string":
                obj[name] = str(default_val) if default_val else ""
            elif attr_type == "bool":
                obj[name] = bool(default_val)
            elif attr_type == "int":
                obj[name] = int(default_val)
            elif attr_type == "vector":
                d = float(default_val)
                obj[name] = (d, d, d)
            else:  # float (and any unmapped numeric type)
                obj[name] = float(default_val)
        except Exception:
            return
        # Apply UI metadata (range + default) for numeric/vector properties. The spinboxes hand
        # back floats, so coerce to int for int props (id_properties_ui rejects a float range
        # there); vectors take a float range plus subtype='XYZ' for Blender's own X/Y/Z panel
        # labels. Guarded — metadata failure must not undo the creation.
        if attr_type in ("float", "int", "vector"):
            cast = int if attr_type == "int" else float
            try:
                ui = obj.id_properties_ui(name)
                kw = {
                    "default": list(obj[name]) if attr_type == "vector" else obj[name]
                }
                if attr_type == "vector":
                    kw["subtype"] = "XYZ"
                if min_val is not None:
                    kw["min"] = cast(min_val)
                if max_val is not None:
                    kw["max"] = cast(max_val)
                ui.update(**kw)
            except (TypeError, KeyError, AttributeError):
                pass

    @classmethod
    def delete_attributes(cls, objects, descriptors):
        """Delete custom *descriptors* across all *objects* (transform channels are skipped)."""
        with CoreUtils.undo_chunk("Delete Attrs"):
            for obj in objects:
                for channel in descriptors:
                    d = cls._channel(obj, channel)
                    if d is None or d["kind"] != "custom":
                        continue
                    try:
                        if d["name"] in obj.keys():
                            del obj[d["name"]]
                    except Exception:
                        pass

    @classmethod
    def rename_attribute(cls, objects, old_name, new_name):
        """Rename a custom property on *objects* (preserves value + UI metadata). Returns success."""
        if not new_name or new_name == old_name:
            return False
        renamed = False
        with CoreUtils.undo_chunk("Rename Attribute") as step:
            for obj in objects:
                if old_name not in obj.keys() or new_name in obj.keys():
                    continue
                try:
                    obj[new_name] = obj[old_name]
                    try:  # carry the UI metadata across
                        obj.id_properties_ui(new_name).update(
                            **obj.id_properties_ui(old_name).as_dict()
                        )
                    except (TypeError, KeyError, AttributeError):
                        pass
                    del obj[old_name]
                    renamed = True
                except Exception:
                    pass
            if not renamed:
                step.cancel()
        return renamed

    @staticmethod
    def rename_node(obj, new_name):
        """Rename the object datablock (Blender may suffix the name: ``.001``); return the object.

        The object is its own handle -- as mayatk's returns the node's new long name -- and
        keeps its identity through the rename, so a pin on it holds.
        """
        if not new_name or obj is None:
            return obj
        with CoreUtils.undo_chunk("Rename Node") as step:
            try:
                obj.name = new_name
            except Exception:
                step.cancel()
        return obj

    # ------------------------------------------------------------------
    # Copy / paste
    # ------------------------------------------------------------------

    def copy_attr_values(self, objects, attr_names):
        """Copy the channels' values (names or descriptors) from the primary object into
        the instance clipboard. Returns it (``{name: (descriptor, value)}``)."""
        self._clipboard = {}
        if not objects or not attr_names:
            return {}
        primary = objects[0]
        for channel in attr_names:
            d = self._channel(primary, channel)
            if d is not None:
                self._clipboard[d["name"]] = (d, self.get_channel_value(primary, d))
        return self._clipboard

    def paste_attr_values(self, objects):
        """Paste previously copied values onto *objects* (matched by channel name)."""
        if not objects or not self._clipboard:
            return
        with CoreUtils.undo_chunk("Paste Attribute Values"):
            for obj in objects:
                for name, (descriptor, value) in self._clipboard.items():
                    if value is None:
                        continue
                    # Re-serialize the (already display-space) value for parse_value. str()
                    # renders scalars faithfully, but yields an unparseable repr for an array
                    # (vector) prop; format_value emits the "(x, y, z)" form parse_value's
                    # vector branch round-trips.
                    is_array = hasattr(value, "__len__") and not isinstance(value, str)
                    text = self.format_value(value) if is_array else str(value)
                    self.set_channel_value([obj], descriptor, text)

    # ------------------------------------------------------------------
    # Transform freeze (Maya's Freeze Transforms → Blender Apply Transform)
    # ------------------------------------------------------------------

    #: Each transform group's whole-group names and per-axis channels
    #: (:meth:`can_freeze_selection`).
    _FREEZE_GROUPS = {
        "location": ({"location"}, ("location_x", "location_y", "location_z")),
        "rotation": (
            {"rotation", "rotation_euler"},
            ("rotation_x", "rotation_y", "rotation_z"),
        ),
        "scale": ({"scale"}, ("scale_x", "scale_y", "scale_z")),
    }
    #: The groups in ``btk.restore_transforms``' words.
    _RESTORE_CHANNELS = {"location": "translate", "rotation": "rotate", "scale": "scale"}

    @classmethod
    def can_freeze_selection(cls, attr_names):
        """Whether *attr_names* (channel names or descriptors) make a whole-group freeze.

        Mirror of mayatk's: ``object.transform_apply`` applies location, rotation and scale
        as whole groups, so a selection qualifies when it is empty (freeze everything) or
        names only transform channels covering whole groups -- the group's name, or all
        three of its axes. The Channels panel enables its Freeze / Unfreeze items by it.
        """
        if not attr_names:
            return True
        names = {cls._channel_name(c) for c in attr_names}
        known = set()
        for whole, axes in cls._FREEZE_GROUPS.values():
            known |= whole | set(axes)
        if not names <= known:
            return False
        for whole, axes in cls._FREEZE_GROUPS.values():
            picked = names & set(axes)
            if picked and not names & whole and picked != set(axes):
                return False
        return True

    @classmethod
    def freeze_transforms(cls, objects, attrs=None, store=True):
        """Apply (freeze) transforms on *objects*: every group, or the whole groups *attrs* names.

        Delegates to ``btk.freeze_transforms`` (``object.transform_apply``), which with
        *store* records the pre-freeze channels for :meth:`unfreeze_transforms`. *attrs*
        (channel names or descriptors) must make whole groups (:meth:`can_freeze_selection`);
        ``None`` freezes everything.

        Returns:
            bool: True when a freeze ran, False when *attrs* failed the check.
        """
        if not objects or not cls.can_freeze_selection(attrs or []):
            return False
        import blendertk as btk

        groups = cls._groups_from_descriptors(attrs)
        kwargs = {g: (g in groups) if groups else True for g in cls._FREEZE_GROUPS}
        with CoreUtils.undo_chunk("Freeze Transforms"):
            btk.freeze_transforms(objects, store=store, **kwargs)
        return True

    @classmethod
    def _groups_from_descriptors(cls, descriptors):
        """The {location, rotation, scale} groups *descriptors* (or channel / group names) touch."""
        groups = set()
        for channel in descriptors or ():
            name = cls._channel_name(channel)
            for group, (whole, axes) in cls._FREEZE_GROUPS.items():
                if name in whole or name in axes:
                    groups.add(group)
        return groups

    @classmethod
    def unfreeze_transforms(cls, objects, attrs=None):
        """Restore previously frozen transforms on *objects* (Maya's Unfreeze Transforms).

        Delegates to ``btk.restore_transforms``, which composes the stored pre-freeze
        channels back into the local transform and counter-shifts the geometry so the
        world position is preserved. *attrs* (channel names or descriptors) restricts the
        restore to the whole groups it names, by :meth:`freeze_transforms`' rule; ``None``
        restores every stored channel.

        Returns:
            list: The restored objects; empty when none had stored data or *attrs* failed
            the check.
        """
        if not objects:
            return []
        channels = None
        if attrs is not None:
            if not cls.can_freeze_selection(attrs):
                return []
            groups = cls._groups_from_descriptors(attrs)
            if groups:
                channels = {cls._RESTORE_CHANNELS[g] for g in groups}
        import blendertk as btk

        with CoreUtils.undo_chunk("Unfreeze Transforms"):
            return btk.restore_transforms(objects, channels=channels) or []

    @staticmethod
    def has_unfreeze_info(objects):
        """Return ``True`` when at least one of *objects* carries stored freeze data."""
        if not objects:
            return False
        import blendertk as btk

        statuses = btk.has_stored_transforms(objects) or {}
        return any(statuses.values())

    # ------------------------------------------------------------------
    # Panel surface: what tentacle's shared Channels panel asks of a host, by mayatk's
    # names (mayatk's ``Channels`` is the reference)
    # ------------------------------------------------------------------

    @classmethod
    def get_filter_kwargs(cls, filter_key="Custom", invert=False):
        """The table filter for combo label *filter_key* (its complement with *invert*),
        for :meth:`build_table_data`: mayatk's two-step call shape (there the kwargs are
        ``cmds.listAttr``'s)."""
        return {
            "filter_key": cls.FILTER_MAP.get(filter_key, "custom"),
            "invert": bool(invert),
        }

    @classmethod
    def get_attr_type(cls, obj, attr_name):
        """*attr_name*'s display type on *obj* (``"float"``, ``"int"``, ``"bool"``,
        ``"string"``, ``"vector"``); ``"?"`` when *obj* has no such channel."""
        d = cls._channel(obj, attr_name)
        return d["type"] if d is not None else "?"

    @classmethod
    def get_attr_value(cls, obj, attr_name):
        """*attr_name*'s value on *obj* in display units (degrees for a rotation);
        ``None`` on failure."""
        return cls.get_channel_value(obj, attr_name)

    @classmethod
    def collect_value_strings(cls, objects, attr_names):
        """``{name: (value string, connection state)}`` for the rows already shown: the
        live-update path, which skips the lock and type reads. A value that differs across
        *objects* shows ``"*"``; a name the primary object lacks is left out."""
        if not objects:
            return {}
        primary, others = objects[0], objects[1:]
        result = {}
        for name in attr_names:
            d = cls._channel(primary, name)
            if d is None:
                continue
            val = cls.get_channel_value(primary, d)
            for other in others:
                if not cls._same_value(cls.get_channel_value(other, name), val):
                    val = "*"
                    break
            result[name] = (cls.format_value(val), cls.classify_connection(primary, d))
        return result

    @classmethod
    def set_attribute_value(cls, objects, attr_name, text):
        """Parse *text* and set *attr_name* on all *objects* (one undo step)."""
        cls.set_channel_value(objects, attr_name, text)

    @classmethod
    def mute_attrs(cls, objects, attr_names):
        """Mute the F-curves / drivers on *attr_names* across *objects* (one undo step)."""
        return cls.set_mute(objects, attr_names, True)

    @classmethod
    def unmute_attrs(cls, objects, attr_names):
        """Unmute the F-curves / drivers on *attr_names* across *objects* (one undo step)."""
        return cls.set_mute(objects, attr_names, False)

    @classmethod
    def get_custom_attrs(cls, obj):
        """*obj*'s custom property names: the channels a rename may change."""
        try:
            return cls._custom_prop_keys(obj)
        except (AttributeError, ReferenceError, TypeError):
            return []

    @staticmethod
    def node_name(obj, full=False):
        """*obj*'s name (*full* changes nothing: an object's name is unique in the file);
        ``None`` for no object or one Blender has freed (deleted, or swapped by an undo)."""
        if obj is None:
            return None
        try:
            return obj.name
        except (AttributeError, ReferenceError):
            return None

    @staticmethod
    def select_nodes(objects):
        """Replace the selection with *objects*; return those selected."""
        return CoreUtils.select(list(objects or []), mode="replace")

    @classmethod
    def get_numeric_values(cls, objects, attr_name):
        """``{object: value}`` (display units) for the *objects* whose *attr_name* is
        numeric and unlocked: what a scrub or a wheel step moves."""
        values = {}
        for obj in objects:
            d = cls._channel(obj, attr_name)
            if d is None or cls.is_locked(obj, d):
                continue
            val = cls.get_channel_value(obj, d)
            if isinstance(val, bool) or not isinstance(val, (int, float)):
                continue
            values[obj] = float(val)
        return values

    @classmethod
    def set_numeric_values(cls, values, attr_name):
        """Set *attr_name* on each object of *values* (``{object: value}``, display units:
        degrees for a rotation) as one undo step; an int property stays int."""
        with CoreUtils.undo_chunk(f"Edit Attr: {attr_name}") as step:
            changed = False
            for obj, value in values.items():
                d = cls._channel(obj, attr_name)
                if d is None:
                    continue
                try:
                    if d["kind"] == "transform":
                        if d["is_angle"]:
                            value = math.radians(value)
                        getattr(obj, d["data_path"])[d["index"]] = value
                    elif d["type"] == "int":
                        obj[d["name"]] = int(round(value))
                    else:
                        obj[d["name"]] = float(value)
                    changed = True
                except Exception:
                    pass
            if not changed:
                step.cancel()

    @staticmethod
    def defer(fn):
        """Run *fn* once the current handler or event returns (a zero-delay
        ``bpy.app.timers`` timer); at once without Blender. Mirror of mayatk's
        (``evalDeferred``): a burst of depsgraph updates is one rebuild after them,
        outside the handler."""
        try:
            import bpy
        except ImportError:
            fn()
            return

        def tick():
            fn()
            return None  # once

        bpy.app.timers.register(tick, first_interval=0.0)

    @classmethod
    def open_editor(cls, key):
        """Open the :attr:`EDITORS` editor *key* in a new window; ``False`` when it could
        not be opened (an unknown key, no window to open from)."""
        args = cls._EDITOR_ARGS.get(key)
        if args is None:
            return False
        from blendertk.ui_utils._ui_utils import UiUtils

        try:
            return UiUtils.open_editor(*args) is not None
        except Exception:
            return False

    @staticmethod
    def watch_scene(owner, on_rebuild, on_values, widget=None):
        """Call a panel back on the scene events that change what it shows (mirror of
        mayatk's).

        *on_rebuild*: the selection changed, a file loaded, an undo or redo (which swaps
        ``bpy.data``, so held objects are read again). *on_values*: the frame changed.
        Every subscription is *owner*'s (:meth:`unwatch`); with *widget* they end as it
        is destroyed. Per-object property callbacks (mayatk's ``watch_nodes``) are not
        built: a value changed outside the panel shows at the next of these events.

        Returns:
            bool: False without Blender (nothing to watch).
        """
        try:
            import bpy  # noqa: F401 -- the handlers need a running Blender
        except ImportError:
            return False
        from blendertk.core_utils.script_job_manager import ScriptJobManager

        mgr = ScriptJobManager.instance()
        for event in ("SelectionChanged", "SceneOpened", "Undo", "Redo"):
            mgr.subscribe(event, on_rebuild, owner=owner)
        mgr.subscribe("timeChanged", on_values, owner=owner)
        if widget is not None:
            mgr.connect_cleanup(widget, owner=owner)
        return True

    @staticmethod
    def unwatch(owner=None, tokens=()):
        """End subscriptions: the *tokens* given, and every one *owner* holds."""
        from blendertk.core_utils.script_job_manager import ScriptJobManager

        mgr = ScriptJobManager.instance()
        for token in tokens or ():
            mgr.unsubscribe(token)
        if owner is not None:
            mgr.unsubscribe_all(owner)
