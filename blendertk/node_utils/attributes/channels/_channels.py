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

``import bpy`` is deferred into the call bodies (no import side effects) so the module — and the
package surface — resolves under headless ``--background`` without a running Blender.
"""
import math


class Channels:
    """Blender attribute query / mutation logic.

    Encapsulates channel querying, filtering, connection classification, and mutation so that
    ``ChannelsSlots`` only handles UI wiring. Operates on ``bpy.types.Object`` references (the
    Blender analogue of Maya node-name strings).
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
        "location_x", "location_y", "location_z",
        "rotation_x", "rotation_y", "rotation_z",
        "scale_x", "scale_y", "scale_z",
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
        returned (mirror of Maya's "most recently selected").
        """
        import bpy

        if self._pinned_targets is not None:
            names = {o.name for o in bpy.data.objects}
            objs = [o for o in self._pinned_targets if getattr(o, "name", None) in names]
        else:
            objs = [o for o in (getattr(bpy.context, "selected_objects", None) or []) if o]
        if self._single_object_mode and len(objs) > 1:
            active = getattr(bpy.context, "active_object", None)
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
                all_names |= set(
                    d["name"] for d in cls._channels_for_object(o, "all")
                )
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
            return [d for d in (transform + custom) if cls.classify_connection(obj, d) != "none"]
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
        if isinstance(val, (list, tuple)) or hasattr(val, "__len__") and not isinstance(val, str):
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
        """Lock state for *descriptor*. Only transform channels are lockable in Blender."""
        if descriptor["kind"] != "transform":
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
        if not objects or descriptor["kind"] != "transform":
            return
        new_state = not cls.is_locked(objects[0], descriptor)
        cls.set_lock(objects, [descriptor], new_state)

    @classmethod
    def set_lock(cls, objects, descriptors, lock):
        """Lock or unlock *descriptors* across all *objects* (transform channels only)."""
        for obj in objects:
            for d in descriptors:
                if d["kind"] != "transform":
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
        legacy = list(getattr(act, "fcurves", []) or [])
        if legacy:
            yield from legacy
            return
        # Slotted-action API (Blender 4.4+): resolve the object's bound slot's channelbag.
        slot = getattr(ad, "action_slot", None)
        for layer in getattr(act, "layers", []) or []:
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
        ``"keyframe_active"`` (key on the current frame), ``"muted"`` (F-curve or driver
        present but muted), ``"driven_key"`` (driver), or ``"constraint"`` (transform channel
        on a constrained object). Mirrors the Maya tool's connection states with Blender's
        drivers / F-curves.
        """
        drv = cls._find_driver(obj, descriptor)
        if drv is not None:
            return "muted" if getattr(drv, "mute", False) else "driven_key"
        fc = cls._find_fcurve(obj, descriptor)
        if fc is not None:
            if getattr(fc, "mute", False):
                return "muted"
            return "keyframe_active" if cls._has_key_at_current_frame(fc) else "keyframe"
        if descriptor["kind"] == "transform" and getattr(obj, "constraints", None):
            return "constraint"
        return "none"

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
        """
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
            states.append((cls.is_locked(primary, d), cls.classify_connection(primary, d)))

        if not rows:
            rows = [["", "", "", "", "No channels"]]
            states = [(False, "none")]
        return rows, states

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    @classmethod
    def set_channel_value(cls, objects, descriptor, text):
        """Parse *text* and set *descriptor* on all *objects*."""
        value = cls.parse_value(text, descriptor)
        if value is None:
            return
        for obj in objects:
            try:
                if descriptor["kind"] == "transform":
                    getattr(obj, descriptor["data_path"])[descriptor["index"]] = value
                else:
                    if descriptor["name"] in obj.keys():
                        obj[descriptor["name"]] = value
            except Exception:
                pass

    @classmethod
    def reset_to_default(cls, objects, descriptors):
        """Reset *descriptors* to their default values across all *objects*.

        Transform defaults: location/rotation → 0, scale → 1. Custom-prop defaults come from the
        property's ``id_properties_ui`` metadata (falls back to 0).
        """
        for obj in objects:
            for d in descriptors:
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
        import bpy

        if not objects:
            return None
        frame = bpy.context.scene.frame_current
        primary_fc = cls._find_fcurve(objects[0], descriptor)
        removing = bool(primary_fc and cls._has_key_at_current_frame(primary_fc))

        dp, idx = descriptor["data_path"], descriptor["index"]
        for obj in objects:
            try:
                if removing:
                    obj.keyframe_delete(data_path=dp, index=-1 if idx is None else idx, frame=frame)
                else:
                    obj.keyframe_insert(data_path=dp, index=-1 if idx is None else idx, frame=frame)
            except (RuntimeError, TypeError):
                pass
        return "removed" if removing else "set"

    @classmethod
    def break_connections(cls, objects, descriptor):
        """Remove the animation / driver on *descriptor* across *objects* (Maya's break-connection).

        Returns ``True`` if anything was removed.
        """
        broke = False
        dp, idx = descriptor["data_path"], descriptor["index"]
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
        return broke

    @staticmethod
    def _remove_fcurve(obj, fcurve):
        """Remove *fcurve* from whichever container owns it (legacy action or 4.4+ channelbag).

        Returns ``True`` on success.
        """
        act = getattr(getattr(obj, "animation_data", None), "action", None)
        if act is None:
            return False
        # Legacy action.
        try:
            act.fcurves.remove(fcurve)
            return True
        except (RuntimeError, TypeError, ReferenceError):
            pass
        # Slotted action (4.4+): the curve lives in a layer/strip channelbag.
        for layer in getattr(act, "layers", []) or []:
            for strip in getattr(layer, "strips", []) or []:
                slot = getattr(obj.animation_data, "action_slot", None)
                try:
                    cb = strip.channelbag(slot) if slot is not None else None
                    if cb is not None:
                        cb.fcurves.remove(fcurve)
                        return True
                except (RuntimeError, TypeError, ReferenceError):
                    continue
        return False

    @classmethod
    def set_mute(cls, objects, descriptors, mute=True):
        """Mute / unmute the F-curve (or driver) on each descriptor across *objects*.

        Blender mirror of Maya's channel mute: toggles ``fcurve.mute`` (and a driver
        F-curve's ``mute``). Channels with neither an F-curve nor a driver are skipped
        silently. Returns ``True`` when anything was toggled.
        """
        changed = False
        for obj in objects:
            for d in descriptors:
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
        for obj in objects:
            for d in descriptors:
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
        """
        import bpy

        if not objects:
            return False
        obj = objects[0]
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
        try:
            bpy.ops.object.select_all(action="DESELECT")
        except RuntimeError:
            pass
        for tid in targets:
            try:
                tid.select_set(True)
            except (AttributeError, RuntimeError):
                pass
        try:
            bpy.context.view_layer.objects.active = targets[0]
        except (AttributeError, TypeError):
            pass
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
    ):
        """Create a custom (ID) property on *objects*.

        Parameters mirror the Maya tool's create-attribute form (numeric range + default). Enum /
        compound types from Maya have no direct single-property Blender analogue and are reduced to
        ``int`` / ``float``. Skips objects that already carry the property.
        """
        for obj in objects:
            if name in obj.keys():
                continue
            try:
                if attr_type == "string":
                    obj[name] = str(default_val) if default_val else ""
                elif attr_type == "bool":
                    obj[name] = bool(default_val)
                elif attr_type == "int":
                    obj[name] = int(default_val)
                else:  # float (and any unmapped numeric type)
                    obj[name] = float(default_val)
            except Exception:
                continue
            # Apply UI metadata (range + default) for numeric properties. The spinboxes hand back
            # floats, so coerce to int for int props (id_properties_ui rejects a float range there).
            if attr_type in ("float", "int"):
                cast = int if attr_type == "int" else float
                try:
                    ui = obj.id_properties_ui(name)
                    kw = {"default": obj[name]}
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
        for obj in objects:
            for d in descriptors:
                if d["kind"] != "custom":
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
        return renamed

    @staticmethod
    def rename_node(obj, new_name):
        """Rename the object datablock and return its (possibly suffixed) new name."""
        if not new_name or obj is None:
            return getattr(obj, "name", "")
        try:
            obj.name = new_name
            return obj.name
        except Exception:
            return getattr(obj, "name", "")

    # ------------------------------------------------------------------
    # Copy / paste
    # ------------------------------------------------------------------

    def copy_values(self, objects, descriptors):
        """Copy *descriptors*' values from the primary object into the instance clipboard."""
        self._clipboard = {}
        if not objects or not descriptors:
            return {}
        primary = objects[0]
        for d in descriptors:
            self._clipboard[d["name"]] = (d, self.get_channel_value(primary, d))
        return self._clipboard

    def paste_values(self, objects):
        """Paste previously copied values onto *objects* (matched by channel name)."""
        if not objects or not self._clipboard:
            return
        for obj in objects:
            for name, (descriptor, value) in self._clipboard.items():
                if value is None:
                    continue
                # Re-format the (already display-space) value back through parse for angles.
                self.set_channel_value([obj], descriptor, str(value))

    # ------------------------------------------------------------------
    # Transform freeze (Maya's Freeze Transforms → Blender Apply Transform)
    # ------------------------------------------------------------------

    @classmethod
    def freeze_transforms(cls, objects, descriptors=None):
        """Apply (freeze) transforms on *objects*, restricted to the touched channel groups.

        Delegates to ``btk.freeze_transforms`` (which wraps ``object.transform_apply``). When
        *descriptors* name only some of location/rotation/scale, only those groups are applied.
        Returns ``True`` when a freeze ran.
        """
        if not objects:
            return False
        import blendertk as btk

        groups = cls._groups_from_descriptors(descriptors)
        # A non-empty selection that names only custom props (no transform group) is not freezable
        # — don't silently fall through to a full freeze. ``descriptors=None`` means "freeze all".
        if descriptors and not groups:
            return False
        kwargs = {
            "location": "location" in groups if groups else True,
            "rotation": "rotation" in groups if groups else True,
            "scale": "scale" in groups if groups else True,
        }
        btk.freeze_transforms(objects, **kwargs)
        return True

    @classmethod
    def _groups_from_descriptors(cls, descriptors):
        """Return the set of {location, rotation, scale} groups *descriptors* touch."""
        if not descriptors:
            return set()
        mapping = {"location": "location", "rotation_euler": "rotation", "scale": "scale"}
        return {
            mapping[d["data_path"]]
            for d in descriptors
            if d["kind"] == "transform" and d["data_path"] in mapping
        }

    @classmethod
    def unfreeze_transforms(cls, objects):
        """Restore previously frozen transforms on *objects* (Maya's Unfreeze Transforms).

        Delegates to ``btk.restore_transforms``, which composes the stored pre-freeze
        channels back into the local transform and counter-shifts the geometry so the
        world position is preserved. Unlike :meth:`freeze_transforms` (which can target a
        channel-group subset), restore is whole-history — it replays every stored channel —
        so there is no per-channel argument. Returns the restored objects.
        """
        if not objects:
            return []
        import blendertk as btk

        return btk.restore_transforms(objects) or []

    @staticmethod
    def has_unfreeze_info(objects):
        """Return ``True`` when at least one of *objects* carries stored freeze data."""
        if not objects:
            return False
        import blendertk as btk

        statuses = btk.has_stored_transforms(objects) or {}
        return any(statuses.values())
