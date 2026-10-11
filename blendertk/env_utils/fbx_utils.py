# !/usr/bin/python
# coding=utf-8
"""FBX import / export helpers — the Blender counterpart of mayatk's ``env_utils.fbx_utils``
(``btk.FbxUtils`` ↔ ``mtk.FbxUtils``).

Mirrors the module + class name and the **portable export/import** surface over
``bpy.ops.export_scene.fbx`` / ``import_scene.fbx``, including the animation-takes trio
``apply_takes`` / ``apply_takes_from_node`` / ``reset_takes`` (one AnimStack = one Unity
AnimationClip per declared take). Two intentional divergences from mayatk:

* **Takes are realized by post-processing the written file, not by exporter options.**
  Maya arms sticky global exporter state (MEL ``FBXExportSplitAnimationIntoTakes`` +
  bake-complex) that the next write consumes. Blender's exporter has no take-splitting
  concept at all — its only multi-stack modes (``bake_anim_use_nla_strips`` /
  ``bake_anim_use_all_actions``) null every object's active action and emit one stack *per
  strip/action*, so they cannot express a multi-object scene-time window (see
  ``export_fbx_bin.fbx_animations``). ``apply_takes`` therefore arms a pending-takes list
  (the Blender analogue of Maya's armed exporter state), and :meth:`FbxUtils.export`
  consumes it right after the ``bpy.ops`` write by cloning the file's single scene-range
  AnimStack into one windowed AnimStack per take, kept beside the whole timeline as Maya's
  ``Take 001`` is (``_split_animation_takes``, built on the FBX addon's own ``parse_fbx`` /
  ``encode_bin``; the same pass writes the keyed visibility the exporter cannot -- see
  ``_add_visibility_curves``). Like Maya's, the armed state applies to
  every export until ``reset_takes`` — the Scene Exporter's ``apply_declared_takes`` task
  stages that reset. What has **no** Blender analogue is the kBeforeExport auto-export hook:
  ``bpy.app.handlers`` has no before-FBX-export event (the same reason ``ScriptJobManager``
  has no ``add_om_callback``), so producers publish at authoring time and the Scene
  Exporter's tasks are the pre-write refresh/arming point.
* **No MEL plugin/preset/option layer.** Maya needs ``load_plugin`` / ``set_fbx_options`` /
  ``load_preset`` because its FBX options are set out-of-band via MEL; Blender's exporter takes its
  options as direct ``bpy.ops`` keyword args, so callers just pass ``**fbx_opts``.

``import bpy`` (and ``tempfile``) are deferred into the call bodies so resolving the package
surface never requires a running Blender. ``export_selection_fbx`` stays exported (module-level)
as the selection-only convenience used by the Substance / Marmoset / RizomUV bridges.
"""

import os
import math
import logging
import contextlib
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

import pythontk as ptk

logger = logging.getLogger(__name__)

# Window-independent selection reader + window-supplying override for the Qt event-pump timer
# context (``bpy.context.window`` is ``None`` there — see ``_core_utils.selected_objects``). Both
# import Qt-free / bpy-deferred, so importing this module never needs a running Blender.
from blendertk.core_utils._core_utils import CoreUtils

# Blender's own defects we correct, declared once beside the probe that retires
# each. Import-safe: nothing is applied until a ``with`` block asks for it.
from blendertk.env_utils.upstream_patches import SIBLING_ARMATURES

# Bridge/export defaults: geometry + hierarchy, modifiers applied, selection-only — the safe
# hand-off set (the same defaults the bridges relied on when this lived in ``core_utils``).
# ``EMPTY`` is load-bearing, NOT decoration: Blender's FBX exporter drops every object whose
# type is excluded and RE-ROOTS its children, so a mesh-only set silently flattens the whole
# scene graph (Blender Empties are Maya's groups) — verified live, a grp>sub>mesh chain arrives
# in Maya/Unity as two parentless meshes. Bridges that want less (Substance / Marmoset) narrow
# this explicitly; DCC hand-offs widen it (see ``BlenderExportMixin._fbx_options``).
_EXPORT_DEFAULTS = {
    "use_selection": True,
    "object_types": {"MESH", "EMPTY"},
    "use_mesh_modifiers": True,
    "mesh_smooth_type": "FACE",
    "bake_anim": False,
    # Exact baked keys, as Maya's bake: the exporter's default 1.0 thins them,
    # and the thinned curve played back drifts (a 0-5 m ease-out was 1.47e-3 m
    # off at frame 98). Not 0.0: that KEYS every channel of every shipped object,
    # static ones included (``fbx_animations_do``: ``force_key = simplify_fac ==
    # 0.0``), where this tolerance (1e-9 relative, under float32's own) keeps
    # every sample that moves and drops the channels that never do. A caller's
    # (or a preset's) own value wins.
    "bake_anim_simplify_factor": 1e-6,
    "path_mode": "AUTO",
}


class _FbxUtilsInternal(object):
    """Internal helpers for FbxUtils."""

    @staticmethod
    def _as_object_types(value):
        """Coerce an ``object_types`` value to the set ``bpy.ops`` requires.

        The enum-flag is a set to Blender, but JSON-backed option presets
        (scene_exporter's PresetStore tier) can only store a list, and a hand-edited
        preset may hold a bare string. ``set("MESH")`` would explode that into
        characters and produce a baffling enum error, so a string wraps as one item.

        Shared with :meth:`BlenderExportMixin._export_fbx`, which unions ``EMPTY`` in
        when it appends the ``data_export`` carrier — same coercion, so the two cannot
        disagree about what a caller's ``object_types`` meant.
        """
        if isinstance(value, set):
            return value
        return {value} if isinstance(value, str) else set(value or ())

    @staticmethod
    def _translate_fbx_options(options):
        """Translate Maya MEL FBX option names (``FBXExport*``) in *options* to ``export_scene.fbx``
        kwargs, returning a new dict.

        The Substance/Marmoset bridge templates are vendored verbatim from mayatk, where ``FBX_OPTIONS``
        drives ``mel.eval`` ``FBXExport*`` commands (``FbxUtils.set_fbx_options``). Those names are
        meaningless to Blender's ``bpy.ops.export_scene.fbx`` — passing one raises
        ``keyword "FBXExport…" unrecognized``. This is the Blender side of the "engine does the
        idiomatic-per-DCC translation" contract the bridges' ``_DEFAULT_FBX_OPTIONS`` documents.

        Known Maya names map to their Blender equivalent; an unmapped ``FBXExport*`` name is a Maya-only
        concept and is dropped. Every non-Maya key passes through unchanged, so Blender still validates
        real ``export_scene.fbx`` kwargs (a typo'd Blender kwarg still errors loudly). Maya translations
        are applied last so their intent wins over the Blender-native defaults regardless of dict order.
        """
        passthrough, maya = {}, {}
        for key, value in options.items():
            (maya if key.startswith("FBXExport") else passthrough)[key] = value
        for key, value in maya.items():
            if key == "FBXExportEmbeddedTextures":
                passthrough["embed_textures"] = bool(value)
                if value:  # Blender only embeds textures when the paths are copied in
                    passthrough["path_mode"] = "COPY"
            elif key == "FBXExportTangents":
                # Without a TANGENT attribute a normal-mapped glTF leaves its
                # tangent basis for the consumer to invent, and consumers
                # disagree — three.js swaps in a screen-space derivative basis
                # and flips green to compensate. Blender needs the UV map that
                # feeds it, which is what ``use_tspace`` asserts.
                passthrough["use_tspace"] = bool(value)
            # else: Maya MEL option with no Blender analogue — intentionally dropped.
        return passthrough

    # ------------------------------------------------------------------
    # The deliverable's own nodes: shipped, and shipped readable
    # ------------------------------------------------------------------

    @staticmethod
    def _resolved_objects(objects) -> List[Any]:
        """*objects* as Blender objects: names resolved, the unresolvable
        dropped (:meth:`FbxUtils.export`'s tolerance), order kept, each once."""
        found, seen = [], set()
        for o in ptk.make_iterable(objects) if objects is not None else ():
            if isinstance(o, str):
                import bpy

                o = bpy.data.objects.get(o)
            if o is None:
                continue
            try:
                key = o.as_pointer()
            except (AttributeError, ReferenceError):
                continue
            if key not in seen:
                seen.add(key)
                found.append(o)
        return found

    @staticmethod
    def _is_transport_node(obj) -> bool:
        """Is *obj* a node whose payload is its CUSTOM PROPERTIES -- the
        ``data_export`` carrier, or a staged curve proxy
        (``ptk.MeshConvert.CURVE_PROXY_MARKER``)?"""
        from blendertk.node_utils.data_nodes import DataNodes

        try:
            return obj.name == DataNodes.EXPORT or bool(
                obj.get(ptk.MeshConvert.CURVE_PROXY_MARKER)
            )
        except (AttributeError, ReferenceError):
            return False

    @staticmethod
    def _force_carrier_readability(objects, options: dict) -> List[str]:
        """Force the options a shipped transport node needs to be READ.

        The ``data_export`` carrier and a staged curve proxy carry their payload
        as custom properties, which Blender's exporter drops by default, on an
        Empty, which an ``object_types`` without ``EMPTY`` drops outright --
        either way the node arrives holding nothing, or not at all, and nothing
        says so: the failure that looks most like success. Shipping the node
        and shipping what makes it readable are one decision, so no option set
        separates them. The one shared step the Scene Exporter (before its
        settings report), the hand-off bridges and :meth:`FbxUtils.export`
        itself take, whatever its caller ran.

        Parameters:
            objects: What the write ships (objects or names).
            options: ``export_scene.fbx`` kwargs, repaired in place. An absent
                ``object_types`` is the operator's default, which admits Empties.

        Returns:
            The repairs made (``"use_custom_props=True"``,
            ``"object_types+=EMPTY"``); empty when none was needed.
        """
        nodes = _FbxUtilsInternal._resolved_objects(objects)
        if not any(_FbxUtilsInternal._is_transport_node(o) for o in nodes):
            return []
        repaired = []
        if not options.get("use_custom_props"):
            options["use_custom_props"] = True
            repaired.append("use_custom_props=True")
        if "object_types" in options:
            types = _FbxUtilsInternal._as_object_types(options["object_types"])
            if "EMPTY" not in types:
                options["object_types"] = types | {"EMPTY"}
                repaired.append("object_types+=EMPTY")
        return repaired

    @staticmethod
    @contextlib.contextmanager
    def _carriers_shippable(objects):
        """Make every local ``data_export`` carrier among *objects* selectable
        for the block -- a write -- and put its hide state back after it.

        A selection-based write ships only what it can select, and the carrier
        is a metadata node whose hiding is incidental: hidden
        (``hide_viewport``, the view layer's eye, ``hide_select``) it would be
        dropped, and in a hidden or excluded collection it cannot be selected
        at all (``select_set`` raises). So its flags are cleared and, where
        that is not enough, it is linked to the scene's root collection for the
        block. Lifted from the Scene Exporter's carrier task (2026-10-05), so
        every writer ships the carrier the same way. A curve proxy staged under
        the carrier (the keyed emissive weights) is linked into the carrier's
        collections, so it is hidden or excluded with it and revealed with it;
        any other hidden object stays the caller's to decide
        (:meth:`FbxUtils.export` drops it, and says so).
        """
        from blendertk.node_utils.data_nodes import DataNodes

        view_layer = CoreUtils._active_view_layer()
        undo = []
        try:
            for obj in _FbxUtilsInternal._resolved_objects(objects):
                parent = obj.parent
                is_carrier = obj.name == DataNodes.EXPORT
                rides_carrier = (
                    parent is not None
                    and parent.name == DataNodes.EXPORT
                    and bool(obj.get(ptk.MeshConvert.CURVE_PROXY_MARKER))
                )
                # A linked carrier is not this file's to edit.
                if not (is_carrier or rides_carrier) or getattr(obj, "library", None):
                    continue
                undo.append(_FbxUtilsInternal._reveal_carrier(obj, view_layer))
            yield
        finally:
            for restore in reversed(undo):
                restore()

    @staticmethod
    def _reveal_carrier(carrier, view_layer) -> Callable[[], None]:
        """Clear whatever hides *carrier* (or a curve proxy riding it) in
        *view_layer*; return the undo.

        The eye is per view layer -- the window's, the one the write selects in
        (windowless, the bare calls read the scene's default layer).
        """
        import bpy

        try:
            layer_hidden = carrier.hide_get(view_layer=view_layer)
        except RuntimeError:  # not in the layer
            layer_hidden = False
        state = (carrier.hide_select, carrier.hide_viewport, layer_hidden)
        carrier.hide_select = False
        carrier.hide_viewport = False
        try:
            if not carrier.visible_get(view_layer=view_layer):
                carrier.hide_set(False, view_layer=view_layer)
        except RuntimeError:  # not in the layer
            pass
        linked = None
        try:
            hidden = not carrier.visible_get(view_layer=view_layer)
        except RuntimeError:  # not in the layer at all
            hidden = True
        scene = view_layer.id_data if view_layer is not None else bpy.context.scene
        if hidden and carrier.name not in scene.collection.objects:
            # Its collection is hidden or excluded, which no object flag
            # overrides: link it to the root collection for the write.
            linked = scene.collection
            linked.objects.link(carrier)
            if view_layer is not None:
                view_layer.update()  # visible_get / select_set read the evaluated layer
            try:
                carrier.hide_set(False, view_layer=view_layer)
            except RuntimeError:
                pass
        if any(state) or linked is not None:
            logger.info(
                "%s was hidden: shown for the write, hidden again after it.",
                carrier.name,
            )

        def undo():
            try:
                if linked is not None:
                    linked.objects.unlink(carrier)
            except (RuntimeError, ReferenceError):
                pass
            try:
                carrier.hide_select, carrier.hide_viewport = state[0], state[1]
                if state[2]:
                    carrier.hide_set(True, view_layer=view_layer)
            except (RuntimeError, ReferenceError):
                pass  # unlinked from the layer since, or freed

        return undo

    @staticmethod
    def _shadow_source_names() -> Set[str]:
        """The sources the stored shadow record (``ptk.SceneRecords.SHADOWS``)
        has its planes follow."""
        from blendertk.node_utils.data_nodes import DataNodes

        try:
            record = ptk.SceneRecords.SHADOWS.load(DataNodes) or {}
        except Exception:  # noqa: BLE001 -- a probe must not fail a write
            return set()
        planes = record.get("planes") if isinstance(record, dict) else None
        return {
            str(plane["source"])
            for plane in planes or ()
            if isinstance(plane, dict) and plane.get("source")
        }

    @staticmethod
    def _admit_shadow_sources(objects, options: dict) -> List[Any]:
        """Let the shadow sources among *objects* through a type filter that
        excludes ``LIGHT``; return the other lights, which stay out.

        The shadow record names the source each plane follows, and the engine
        finds it by name (unitytk's ``ShadowPlaneController``; the GLB binds
        it by node) -- but the exporter drops every object whose type the
        filter excludes, so a SUN source never shipped and follow-source fell
        back to the baked keys. ``LIGHT`` joins the filter for the sources the
        record names; any other light in the set is left unselected, out as
        the filter left it before.

        Returns:
            The lights to leave out of the write (empty when nothing changed).
        """
        types = options.get("object_types")
        if types is None:
            return []  # the operator's default admits every light
        types = _FbxUtilsInternal._as_object_types(types)
        if "LIGHT" in types:
            return []
        lights = [o for o in objects if getattr(o, "type", None) == "LIGHT"]
        if not lights:
            return []
        sources = _FbxUtilsInternal._shadow_source_names()
        admitted = [o for o in lights if o.name in sources]
        if not admitted:
            return []
        options["object_types"] = types | {"LIGHT"}
        logger.info(
            "FBX export: the shadow source light(s) %s ship for the engine to "
            "follow (LIGHT admitted for them alone).",
            ", ".join(o.name for o in admitted),
        )
        return [o for o in lights if o.name not in sources]

    @staticmethod
    @contextlib.contextmanager
    def _range_covering(scene, takes):
        """Widen *scene*'s frame range to cover every one of *takes* for the
        block -- a write -- and give the scene its own range back after.

        Blender bakes over the scene range, and the split clamps a take to the
        span it baked, so a shot past the range shipped cut short. The Scene
        Exporter's takes task widens the range up front; a hand-off arms the
        same takes and did not, so the write guarantees it itself -- as Maya's
        ``apply_takes`` sets its bake range to cover the takes. Widened, never
        narrowed (:meth:`FbxUtils.bake_range`, the range the records describe).
        """
        original = (scene.frame_start, scene.frame_end)
        start, end = original
        for _name, take_start, take_end in FbxUtils._declared_take_bounds(takes):
            start = min(start, int(math.floor(take_start)))
            end = max(end, int(math.ceil(take_end)))
        if (start, end) == original:
            yield
            return
        scene.frame_start, scene.frame_end = start, end
        try:
            yield
        finally:
            scene.frame_start, scene.frame_end = original

    # ------------------------------------------------------------------
    # Animation-takes splitting (post-write AnimStack surgery)
    # ------------------------------------------------------------------
    #
    # Everything below operates on the parsed element tree of a just-written
    # binary FBX, using the FBX addon's own reader/writer (``parse_fbx`` /
    # ``encode_bin`` — the modules the importer and exporter themselves are
    # built on), so the machinery only exists inside a Blender runtime.

    # parse_fbx property-type byte → encode_bin.FBXElem add-method name.
    _FBX_PROP_ENCODERS = {
        ord("Z"): "add_int8",
        ord("Y"): "add_int16",
        ord("B"): "add_bool",
        ord("C"): "add_char",
        ord("I"): "add_int32",
        ord("F"): "add_float32",
        ord("D"): "add_float64",
        ord("L"): "add_int64",
        ord("R"): "add_bytes",
        ord("S"): "add_string",
        ord("b"): "add_bool_array",
        ord("c"): "add_byte_array",
        ord("i"): "add_int32_array",
        ord("l"): "add_int64_array",
        ord("f"): "add_float32_array",
        ord("d"): "add_float64_array",
    }

    @staticmethod
    def _parsed_to_encode(elem, encode_mod):
        """Rebuild a ``parse_fbx.FBXElem`` (namedtuple) as an ``encode_bin.FBXElem``.

        The two modules share the FBX property-type grammar but not an element
        class, so a parse → transform → encode round-trip needs this one walk.
        Property values come back from the parser in exactly the Python types
        the encoder's ``add_*`` methods assert on (bools, ints, floats, raw
        bytes, ``array.array`` with the matching typecode), so the dispatch is
        a straight type-code table.
        """
        out = encode_mod.FBXElem(elem.id)
        for ptype, val in zip(elem.props_type, elem.props):
            getattr(out, _FbxUtilsInternal._FBX_PROP_ENCODERS[ptype])(val)
        for child in elem.elems:
            out.elems.append(_FbxUtilsInternal._parsed_to_encode(child, encode_mod))
        return out

    @staticmethod
    def _find_elems(parent, elem_id):
        """All direct children of *parent* with id *elem_id* (parsed tree)."""
        return [e for e in parent.elems if e.id == elem_id]

    @staticmethod
    def _find_elem(parent, elem_id):
        """First direct child of *parent* with id *elem_id*, or ``None``."""
        for e in parent.elems:
            if e.id == elem_id:
                return e
        return None

    @staticmethod
    def _make_parsed(parse_mod, elem_id, props=(), props_type=b"", elems=()):
        """Construct a ``parse_fbx.FBXElem`` namedtuple from plain values."""
        return parse_mod.FBXElem(
            elem_id, list(props), bytearray(props_type), list(elems)
        )

    @staticmethod
    def _clone_parsed(parse_mod, elem):
        """Deep-copy a parsed element (prop values are immutable or replaced
        wholesale by the callers, so the prop list is copied shallow)."""
        return parse_mod.FBXElem(
            elem.id,
            list(elem.props),
            bytearray(elem.props_type),
            [_FbxUtilsInternal._clone_parsed(parse_mod, c) for c in elem.elems],
        )

    @staticmethod
    def _timestamp_props(parse_mod, entries):
        """A ``Properties70`` element holding one ``P`` timestamp per (name, ktime).

        Written explicitly (never template-relative) so a consumer needs no
        ``Definitions`` template lookup to see the take window.
        """
        ps = [
            _FbxUtilsInternal._make_parsed(
                parse_mod,
                b"P",
                [name, b"KTime", b"Time", b"", int(value)],
                b"SSSSL",
            )
            for name, value in entries
        ]
        return _FbxUtilsInternal._make_parsed(parse_mod, b"Properties70", elems=ps)

    @staticmethod
    def _sliced_curve(parse_mod, curve_elem, t0, t1, ktime_per_frame):
        """Clone *curve_elem* with its keys windowed to ktime span [t0, t1].

        The source curve is the baked scene-range take's (dense per-frame keys,
        linear interpolation, then simplified — so a long-constant span may hold
        keys only at its ends). Keys inside the window are kept; when no key
        lands on a window edge, the edge value is linearly interpolated from
        the surrounding keys and a boundary key is synthesized — exact, because
        the exporter writes these curves with linear tangents. A stepped curve
        (the Visibility curves :meth:`_add_visibility_curves` writes) holds its
        last key across the edge instead: interpolating it would invent a
        half-visible frame.
        """
        import numpy as np

        from io_scene_fbx import data_types

        clone = _FbxUtilsInternal._clone_parsed(parse_mod, curve_elem)
        kt_el = _FbxUtilsInternal._find_elem(clone, b"KeyTime")
        kv_el = _FbxUtilsInternal._find_elem(clone, b"KeyValueFloat")
        refcount_el = _FbxUtilsInternal._find_elem(clone, b"KeyAttrRefCount")
        if kt_el is None or kv_el is None:
            return clone  # degenerate curve — nothing to window

        kt = np.asarray(kt_el.props[0], dtype=np.int64)
        kv = np.asarray(kv_el.props[0], dtype=np.float64)
        flags_el = _FbxUtilsInternal._find_elem(clone, b"KeyAttrFlags")
        stepped = (
            flags_el is not None
            and len(flags_el.props[0])
            and all(
                (int(f) & _FbxUtilsInternal._KEY_INTERPOLATION_BITS)
                == _FbxUtilsInternal._KEY_CONSTANT
                for f in flags_el.props[0]
            )
        )

        def value_at(t):
            if stepped:
                return float(kv[max(int(np.searchsorted(kt, t, side="right")) - 1, 0)])
            return float(np.interp(t, kt, kv))

        eps = int(ktime_per_frame * 1e-3)  # sub-millframe tolerance
        mask = (kt >= t0 - eps) & (kt <= t1 + eps)
        new_t = list(kt[mask])
        new_v = list(kv[mask])
        # np.interp clamps outside the key range — correct here: the take
        # window is clamped to the baked span by the caller, and a constant
        # tail extends at its held value.
        if not new_t or new_t[0] > t0 + eps:
            new_t.insert(0, t0)
            new_v.insert(0, value_at(t0))
        if new_t[-1] < t1 - eps:
            new_t.append(t1)
            new_v.append(value_at(t1))

        import array as _array

        # Typecodes MUST come from the addon's data_types: encode_bin asserts
        # on them, and they are platform-dependent (Windows resolves
        # ARRAY_INT32 to 'l', Linux to 'i').
        kt_el.props[0] = _array.array(data_types.ARRAY_INT64, [int(t) for t in new_t])
        kv_el.props[0] = _array.array(
            data_types.ARRAY_FLOAT32, [float(v) for v in new_v]
        )
        if refcount_el is not None:
            refcount_el.props[0] = _array.array(data_types.ARRAY_INT32, [len(new_t)])
        return clone

    @staticmethod
    def _file_ktime(root, version):
        """ktime units per second as the file defines them — by file version,
        exactly as the FBX importer resolves it."""
        try:
            from io_scene_fbx.fbx_utils import (
                FBX_KTIME_V7,
                FBX_KTIME_V8,
                FBX_TIMECODE_DEFINITION_TO_KTIME_PER_SECOND,
            )

            ktime = FBX_KTIME_V8 if version >= 8000 else FBX_KTIME_V7
            # A header of version 1004+ may pin the rate explicitly
            # (FBX 7700's TCDefinition opt-in) — mirror the importer's
            # resolution exactly, or a pinned file gets mis-timed takes.
            header = _FbxUtilsInternal._find_elem(root, b"FBXHeaderExtension")
            hv = header and _FbxUtilsInternal._find_elem(header, b"FBXHeaderVersion")
            if hv is not None and hv.props and hv.props[0] >= 1004:
                flags = _FbxUtilsInternal._find_elem(header, b"OtherFlags")
                tc = flags and _FbxUtilsInternal._find_elem(flags, b"TCDefinition")
                if tc is not None and tc.props:
                    ktime = FBX_TIMECODE_DEFINITION_TO_KTIME_PER_SECOND.get(
                        tc.props[0], FBX_KTIME_V8
                    )
        except ImportError:  # pre-4.2 addon: a single constant
            from io_scene_fbx.fbx_utils import FBX_KTIME as ktime
        return ktime

    @staticmethod
    def _file_frame_scale(root, version):
        """(fps, ktime_per_second) as the file itself defines them.

        fps comes from the ``GlobalSettings`` ``CustomFrameRate`` property the
        exporter always writes; the ktime rate follows the file version
        (:meth:`_file_ktime`).
        """
        ktime = _FbxUtilsInternal._file_ktime(root, version)
        fps = None
        gs = _FbxUtilsInternal._find_elem(root, b"GlobalSettings")
        props = gs and _FbxUtilsInternal._find_elem(gs, b"Properties70")
        for p in props.elems if props else ():
            if p.id == b"P" and p.props and p.props[0] == b"CustomFrameRate":
                fps = float(p.props[-1])
                break
        if not fps or fps <= 0:
            import bpy

            r = bpy.context.scene.render
            fps = r.fps / r.fps_base
        return fps, ktime

    # ---- shared tree edits ---------------------------------------------------

    @staticmethod
    def _uid_allocator(objects_el):
        """A ``new_uid()`` handing out uids no object of *objects_el* holds."""
        existing = {
            e.props[0]
            for e in objects_el.elems
            if e.props and isinstance(e.props[0], int)
        }
        counter = [max(existing) if existing else 1]

        def new_uid():
            while True:
                counter[0] += 1
                if counter[0] >= 2**63 - 1:
                    counter[0] = 1
                if counter[0] not in existing:
                    break
            existing.add(counter[0])
            return counter[0]

        return new_uid

    @staticmethod
    def _connection(parse_mod, kind, child_uid, parent_uid, prop=None):
        """A ``Connections`` ``C`` element: ``OO``, or ``OP`` onto *prop*."""
        props = [kind, child_uid, parent_uid]
        if prop is not None:
            props.append(prop)
        return _FbxUtilsInternal._make_parsed(
            parse_mod, b"C", props, b"SLLS" if prop is not None else b"SLL"
        )

    @staticmethod
    def _take_element(parse_mod, name_b, t0, t1):
        """A ``Takes`` entry for the stack *name_b* over ktime ``[t0, t1]``."""
        make = _FbxUtilsInternal._make_parsed
        return make(
            parse_mod,
            b"Take",
            [name_b],
            b"S",
            [
                make(parse_mod, b"FileName", [name_b + b".tak"], b"S"),
                make(parse_mod, b"LocalTime", [t0, t1], b"LL"),
                make(parse_mod, b"ReferenceTime", [t0, t1], b"LL"),
            ],
        )

    #: What the exporter declares in ``Definitions`` for each animation object
    #: type -- its property template as ``(type codes, values)`` rows, copied
    #: from a Blender 5.1 write -- for a file it wrote with nothing animated,
    #: where a stack added after the write has to be declared as the exporter
    #: would have declared it.
    _ANIM_TEMPLATES = {
        b"AnimationStack": (
            b"FbxAnimStack",
            (
                (b"SSSSS", (b"Description", b"KString", b"", b"", b"")),
                (b"SSSSL", (b"LocalStart", b"KTime", b"Time", b"", 0)),
                (b"SSSSL", (b"LocalStop", b"KTime", b"Time", b"", 0)),
                (b"SSSSL", (b"ReferenceStart", b"KTime", b"Time", b"", 0)),
                (b"SSSSL", (b"ReferenceStop", b"KTime", b"Time", b"", 0)),
            ),
        ),
        b"AnimationLayer": (
            b"FbxAnimLayer",
            (
                (b"SSSSD", (b"Weight", b"Number", b"", b"A", 100.0)),
                (b"SSSSI", (b"Mute", b"bool", b"", b"", 0)),
                (b"SSSSI", (b"Solo", b"bool", b"", b"", 0)),
                (b"SSSSI", (b"Lock", b"bool", b"", b"", 0)),
                (b"SSSSDDD", (b"Color", b"ColorRGB", b"Color", b"", 0.8, 0.8, 0.8)),
                (b"SSSSI", (b"BlendMode", b"enum", b"", b"", 0)),
                (b"SSSSI", (b"RotationAccumulationMode", b"enum", b"", b"", 0)),
                (b"SSSSI", (b"ScaleAccumulationMode", b"enum", b"", b"", 0)),
                (b"SSSSL", (b"BlendModeBypass", b"ULongLong", b"", b"", 0)),
            ),
        ),
        b"AnimationCurveNode": (
            b"FbxAnimCurveNode",
            ((b"SSSS", (b"d", b"Compound", b"", b"")),),
        ),
        b"AnimationCurve": (None, ()),
    }

    @staticmethod
    def _count_definitions(parse_mod, root, added) -> None:
        """Keep ``Definitions`` honest after objects were added: each type's
        ``Count`` and the total grow by *added* (``{object type: n}``), and a
        type the file did not declare is declared with the exporter's
        template (:attr:`_ANIM_TEMPLATES`)."""
        find, make = _FbxUtilsInternal._find_elem, _FbxUtilsInternal._make_parsed
        defs = find(root, b"Definitions")
        if defs is None:
            return
        declared = {
            ot.props[0]: ot
            for ot in _FbxUtilsInternal._find_elems(defs, b"ObjectType")
            if ot.props
        }
        total = 0
        for kind, n in added.items():
            if not n:
                continue
            total += n
            ot = declared.get(kind)
            if ot is not None:
                count = find(ot, b"Count")
                if count is not None:
                    count.props[0] += n
                continue
            template, rows = _FbxUtilsInternal._ANIM_TEMPLATES.get(kind, (None, ()))
            elems = [make(parse_mod, b"Count", [n], b"I")]
            if template is not None:
                props = [
                    make(parse_mod, b"P", list(values), codes) for codes, values in rows
                ]
                elems.append(
                    make(
                        parse_mod,
                        b"PropertyTemplate",
                        [template],
                        b"S",
                        [make(parse_mod, b"Properties70", elems=props)],
                    )
                )
            defs.elems.append(make(parse_mod, b"ObjectType", [kind], b"S", elems))
        total_el = find(defs, b"Count")
        if total_el is not None:
            total_el.props[0] += total

    @staticmethod
    def _rewrite_animation(
        filepath, takes=None, visibility=None, frame_range=None, scene_name=None
    ) -> int:
        """Rewrite *filepath* in place: the keyed visibility added
        (:meth:`_add_visibility_curves`), then the armed takes split out
        (:meth:`_split_animation_takes`) -- in that order, so every take
        carries its window of the visibility -- through ONE parse and ONE
        encode of the FBX addon's own ``parse_fbx`` / ``encode_bin``.

        Returns:
            The number of takes written (0: none armed, or nothing to split).
        """
        from io_scene_fbx import parse_fbx, encode_bin

        root, version = parse_fbx.parse(filepath)
        changed = False
        if visibility:
            changed = bool(
                _FbxUtilsInternal._add_visibility_curves(
                    parse_fbx, root, version, visibility, frame_range, scene_name
                )
            )
        split = 0
        if takes:
            split = _FbxUtilsInternal._split_animation_takes(
                parse_fbx, root, version, takes, os.path.basename(filepath)
            )
        if changed or split:
            enc_root = encode_bin.FBXElem(b"")
            for child in root.elems:
                enc_root.elems.append(
                    _FbxUtilsInternal._parsed_to_encode(child, encode_bin)
                )
            encode_bin.write(filepath, enc_root, version)
        return split

    # ---- keyed visibility: the FBX's own Visibility channel ------------------
    #
    # Blender's exporter bakes transforms, shape keys and camera lenses and
    # writes NO visibility animation, so a keyed ``hide_render`` never left
    # the file: the GLB rebuilds it from the ``visibility_tracks`` record, but
    # Unity -- which imports a node's animated FBX ``Visibility`` as its
    # Renderer's ``m_Enabled``, the curve Maya's exporter writes -- got
    # nothing for an object with no opacity channel to carry it.

    #: ``KeyAttrFlags`` of a stepped key: interpolation-mode bit 1
    #: (``eInterpolationConstant``; linear is bit 2, cubic bit 3).
    _KEY_CONSTANT = 1 << 1
    #: The interpolation-mode bits of ``KeyAttrFlags``.
    _KEY_INTERPOLATION_BITS = 0x0E
    #: The per-key data words the exporter writes beside its flags
    #: (``export_fbx_bin``): FBX expects four, whatever the mode reads.
    _KEY_ATTR_DATA = (0.0, 0.0, 9.419963346924634e-30, 0.0)

    @staticmethod
    def _visibility_curves(objects, start, end) -> Dict[str, list]:
        """``{FBX model name: [(frame, visible), ...]}`` for each of *objects*
        whose render visibility is keyed.

        ``hide_render`` is the channel -- the one the ``visibility_tracks``
        record publishes and :class:`RenderEffects` mirrors an opacity onto --
        read off the object's own action, sampled on every frame of
        ``[start, end]`` (the frames the write baked) and reduced to the first
        frame, each step and the last frame. 1.0 is visible, as FBX's
        ``Visibility`` reads. Model names are the exporter's own
        (``io_scene_fbx.fbx_utils.get_bid_name``).
        """
        from io_scene_fbx.fbx_utils import get_bid_name

        from blendertk.anim_utils._anim_utils import AnimUtils

        start, end = int(start), int(end)
        curves: Dict[str, list] = {}
        for obj in objects:
            try:
                fc = next(
                    (
                        f
                        for f in AnimUtils.get_fcurves([obj])
                        if f.data_path == "hide_render"
                        and not f.mute
                        and len(f.keyframe_points)
                    ),
                    None,
                )
                name = get_bid_name(obj)
            except (AttributeError, ReferenceError):
                continue  # not an object, or removed since
            if fc is None:
                continue
            keys = []
            for frame in range(start, end + 1):
                visible = 0.0 if fc.evaluate(frame) >= 0.5 else 1.0
                if not keys or keys[-1][1] != visible:
                    keys.append((frame, visible))
            if keys and keys[-1][0] != end:
                keys.append((end, keys[-1][1]))
            if keys:
                curves[name] = keys
        return curves

    @staticmethod
    def _scene_range_layer(parse_mod, root, frame_range, scene_name, per_frame):
        """The layer of the file's one scene-range stack, or ``None``.

        A write with nothing else animated holds no stack at all (the
        exporter writes none), so one is added over *frame_range* -- named
        *scene_name*, as the exporter names its own -- with its layer, its
        ``Takes`` entry and its ``Definitions``. Several stacks (the
        multi-stack modes :meth:`FbxUtils.scene_range_take` turns off) is not a
        shape this extends.
        """
        find, finds = _FbxUtilsInternal._find_elem, _FbxUtilsInternal._find_elems
        make = _FbxUtilsInternal._make_parsed
        objects_el = find(root, b"Objects")
        conns_el = find(root, b"Connections")
        if objects_el is None or conns_el is None:
            return None
        stacks = finds(objects_el, b"AnimationStack")
        if len(stacks) > 1:
            return None
        if stacks:
            layers = {
                e.props[0] for e in finds(objects_el, b"AnimationLayer") if e.props
            }
            stack_uid = stacks[0].props[0]
            return next(
                (
                    c.props[1]
                    for c in conns_el.elems
                    if c.id == b"C"
                    and len(c.props) > 2
                    and c.props[0] == b"OO"
                    and c.props[2] == stack_uid
                    and c.props[1] in layers
                ),
                None,
            )
        if frame_range is None or not scene_name:
            return None
        new_uid = _FbxUtilsInternal._uid_allocator(objects_el)
        t0 = int(round(frame_range[0] * per_frame))
        t1 = int(round(frame_range[1] * per_frame))
        name_b = str(scene_name).encode("utf-8")
        stack_uid, layer_uid = new_uid(), new_uid()
        objects_el.elems.append(
            make(
                parse_mod,
                b"AnimationStack",
                [stack_uid, name_b + b"\x00\x01AnimStack", b""],
                b"LSS",
                [
                    _FbxUtilsInternal._timestamp_props(
                        parse_mod,
                        [
                            (b"LocalStart", t0),
                            (b"LocalStop", t1),
                            (b"ReferenceStart", t0),
                            (b"ReferenceStop", t1),
                        ],
                    )
                ],
            )
        )
        objects_el.elems.append(
            make(
                parse_mod,
                b"AnimationLayer",
                [layer_uid, name_b + b"\x00\x01AnimLayer", b""],
                b"LSS",
            )
        )
        conns_el.elems.append(
            _FbxUtilsInternal._connection(parse_mod, b"OO", layer_uid, stack_uid)
        )
        takes_el = find(root, b"Takes")
        if takes_el is not None:
            takes_el.elems.append(
                _FbxUtilsInternal._take_element(parse_mod, name_b, t0, t1)
            )
        _FbxUtilsInternal._count_definitions(
            parse_mod, root, {b"AnimationStack": 1, b"AnimationLayer": 1}
        )
        return layer_uid

    @staticmethod
    def _add_visibility_curves(
        parse_mod, root, version, curves, frame_range=None, scene_name=None
    ) -> int:
        """Animate each named Model's ``Visibility`` in the scene-range stack.

        *curves* is :meth:`_visibility_curves`' ``{model name: [(frame,
        visible), ...]}``. Each becomes one ``AnimationCurveNode`` and one
        stepped ``AnimationCurve`` on the stack's layer -- the shape Maya's
        exporter writes, so a take split clones and windows it like any other
        curve (:meth:`_sliced_curve` holds a stepped one across a window edge)
        -- and the Model's ``Visibility`` property is flagged animated.
        Names the file carries no Model for (a type the filter dropped) are
        skipped.

        Returns:
            The number of curves written.
        """
        import array

        from io_scene_fbx import data_types
        from io_scene_fbx.fbx_utils import FBX_ANIM_KEY_VERSION

        find, finds = _FbxUtilsInternal._find_elem, _FbxUtilsInternal._find_elems
        make = _FbxUtilsInternal._make_parsed
        objects_el = find(root, b"Objects")
        conns_el = find(root, b"Connections")
        if objects_el is None or conns_el is None:
            return 0
        models = {}
        for e in finds(objects_el, b"Model"):
            if len(e.props) > 1 and isinstance(e.props[1], bytes):
                label = e.props[1].split(b"\x00\x01")[0].decode("utf-8", "replace")
                models.setdefault(label, e)
        wanted = [
            (models[name], keys) for name, keys in curves.items() if name in models
        ]
        if not wanted:
            return 0

        fps, ktime = _FbxUtilsInternal._file_frame_scale(root, version)
        per_frame = ktime / fps
        layer_uid = _FbxUtilsInternal._scene_range_layer(
            parse_mod, root, frame_range, scene_name, per_frame
        )
        if layer_uid is None:
            logger.warning(
                "Keyed visibility not written: the FBX holds no single "
                "scene-range AnimStack to carry it."
            )
            return 0

        new_uid = _FbxUtilsInternal._uid_allocator(objects_el)
        conn = _FbxUtilsInternal._connection
        for model, keys in wanted:
            first = float(keys[0][1])
            node_uid, curve_uid = new_uid(), new_uid()
            objects_el.elems.append(
                make(
                    parse_mod,
                    b"AnimationCurveNode",
                    [node_uid, b"Visibility\x00\x01AnimCurveNode", b""],
                    b"LSS",
                    [
                        make(
                            parse_mod,
                            b"Properties70",
                            elems=[
                                make(
                                    parse_mod,
                                    b"P",
                                    [b"d|Visibility", b"Number", b"", b"A", first],
                                    b"SSSSD",
                                )
                            ],
                        )
                    ],
                )
            )
            # Typecodes from the addon's data_types: encode_bin asserts on
            # them, and they are platform-dependent (see _sliced_curve).
            objects_el.elems.append(
                make(
                    parse_mod,
                    b"AnimationCurve",
                    [curve_uid, b"\x00\x01AnimCurve", b""],
                    b"LSS",
                    [
                        make(parse_mod, b"Default", [first], b"D"),
                        make(parse_mod, b"KeyVer", [FBX_ANIM_KEY_VERSION], b"I"),
                        make(
                            parse_mod,
                            b"KeyTime",
                            [
                                array.array(
                                    data_types.ARRAY_INT64,
                                    [int(round(f * per_frame)) for f, _v in keys],
                                )
                            ],
                            b"l",
                        ),
                        make(
                            parse_mod,
                            b"KeyValueFloat",
                            [
                                array.array(
                                    data_types.ARRAY_FLOAT32,
                                    [float(v) for _f, v in keys],
                                )
                            ],
                            b"f",
                        ),
                        make(
                            parse_mod,
                            b"KeyAttrFlags",
                            [
                                array.array(
                                    data_types.ARRAY_INT32,
                                    [_FbxUtilsInternal._KEY_CONSTANT],
                                )
                            ],
                            b"i",
                        ),
                        make(
                            parse_mod,
                            b"KeyAttrDataFloat",
                            [
                                array.array(
                                    data_types.ARRAY_FLOAT32,
                                    _FbxUtilsInternal._KEY_ATTR_DATA,
                                )
                            ],
                            b"f",
                        ),
                        make(
                            parse_mod,
                            b"KeyAttrRefCount",
                            [array.array(data_types.ARRAY_INT32, [len(keys)])],
                            b"i",
                        ),
                    ],
                )
            )
            model_uid = model.props[0]
            # extend, never +=: the element is a namedtuple, and += rebinds it.
            conns_el.elems.extend(
                [
                    conn(parse_mod, b"OO", node_uid, layer_uid),
                    conn(parse_mod, b"OP", node_uid, model_uid, b"Visibility"),
                    conn(parse_mod, b"OP", curve_uid, node_uid, b"d|Visibility"),
                ]
            )
            # Flag the property animated ("A+", what the exporter writes for
            # the transforms it animates), at the value the curve opens on.
            props70 = find(model, b"Properties70")
            if props70 is None:
                props70 = make(parse_mod, b"Properties70")
                model.elems.append(props70)
            vis = next(
                (
                    p
                    for p in props70.elems
                    if p.id == b"P" and p.props and p.props[0] == b"Visibility"
                ),
                None,
            )
            if vis is None:
                props70.elems.append(
                    make(
                        parse_mod,
                        b"P",
                        [b"Visibility", b"Visibility", b"", b"A+", first],
                        b"SSSSD",
                    )
                )
            else:
                vis.props[3] = b"A+"
                if len(vis.props) > 4 and vis.props_type[4] == ord("D"):
                    vis.props[4] = first
        _FbxUtilsInternal._count_definitions(
            parse_mod,
            root,
            {b"AnimationCurveNode": len(wanted), b"AnimationCurve": len(wanted)},
        )
        logger.info(
            "Keyed visibility written as the FBX Visibility channel: "
            + ", ".join(
                model.props[1].split(b"\x00\x01")[0].decode("utf-8", "replace")
                for model, _keys in wanted
            )
        )
        return len(wanted)

    # ---- the take split ------------------------------------------------------

    #: What the scene-range stack is renamed to when a declared take already
    #: uses its name -- the name the GLB gives the same stack (pythontk's
    #: ``GlbClips.SEQUENCE_CLIP``).
    _SEQUENCE_STACK = b"FULL_SEQUENCE"

    @staticmethod
    def _split_animation_takes(parse_mod, root, version, takes, label="") -> int:
        """Clone the scene-range AnimStack into one windowed stack per take.

        The parsed file carries the exporter's single baked scene-range
        AnimStack (the ``_force_scene_range_take`` invariant). For each
        ``(name, start, end)`` take the stack graph — stack, layer(s), curve
        nodes, curves — is cloned with fresh uids, the curves' keys windowed to
        the take's span (kept in absolute scene time, as Maya's split takes
        are), and a ``Takes`` entry added.

        The scene-range stack STAYS, beside the takes cut from it, as Maya's
        whole-timeline ``Take 001`` stays beside the takes its splitter writes
        (2026-10-05; until then it was removed, so a Shots + Full Sequence
        export shipped no whole timeline in either file): the GLB conversion
        cuts every clip from it and keeps it as ``FULL_SEQUENCE``, Unity plays
        the shots as windows of it, and Shots Only drops it from the FBX once
        the GLB was cut (the Scene Exporter's ``ship_declared_takes``). A take
        named like the stack would leave no take that no shot names, so the
        stack is renamed :attr:`_SEQUENCE_STACK` then.

        Take windows are clamped to the baked span — content beyond
        it cannot exist in the source curves; the Scene Exporter's
        ``apply_declared_takes`` task widens the scene range up front so
        clamping never bites on that path.

        Returns the number of takes written; 0 (tree untouched) when the file
        holds no single baked AnimStack to split.
        """
        find, finds = _FbxUtilsInternal._find_elem, _FbxUtilsInternal._find_elems
        objects_el = find(root, b"Objects")
        conns_el = find(root, b"Connections")
        takes_el = find(root, b"Takes")
        stacks = finds(objects_el, b"AnimationStack") if objects_el else []
        if conns_el is None or len(stacks) != 1:
            logger.warning(
                f"Cannot split animation takes: expected one baked AnimStack in "
                f"{label or 'the FBX'}, found {len(stacks)} "
                "(was the write made with bake_anim enabled?). File left as written."
            )
            return 0

        fps, ktime = _FbxUtilsInternal._file_frame_scale(root, version)
        ktime_per_frame = ktime / fps

        # ---- index the existing stack graph off the Connections table ----
        stack = stacks[0]
        stack_uid = stack.props[0]
        oo, op = {}, {}  # child uid → [conn elem] by connection kind
        for c in conns_el.elems:
            if c.id != b"C" or not c.props:
                continue
            (oo if c.props[0] == b"OO" else op).setdefault(c.props[1], []).append(c)

        def children_of(parent_uid, pool):
            return [
                uid
                for uid, conns in pool.items()
                if any(c.props[2] == parent_uid for c in conns)
            ]

        def conn(kind, child_uid, parent_uid, prop=None):
            return _FbxUtilsInternal._connection(
                parse_mod, kind, child_uid, parent_uid, prop
            )

        layer_uids = [
            e.props[0]
            for e in finds(objects_el, b"AnimationLayer")
            if e.props[0] in children_of(stack_uid, oo)
        ]
        by_uid = {e.props[0]: e for e in objects_el.elems if e.props}
        cn_uids = [
            uid
            for luid in layer_uids
            for uid in children_of(luid, oo)
            if by_uid.get(uid) is not None and by_uid[uid].id == b"AnimationCurveNode"
        ]
        curve_uids = [
            uid
            for cnuid in cn_uids
            for uid in children_of(cnuid, op)
            if by_uid.get(uid) is not None and by_uid[uid].id == b"AnimationCurve"
        ]

        # Baked span (for window clamping) off the stack's own key data: the
        # union of every curve's first/last key time.
        span_lo = span_hi = None
        for u in curve_uids:
            kt_el = find(by_uid[u], b"KeyTime")
            if kt_el is None or not len(kt_el.props[0]):
                continue
            lo, hi = int(kt_el.props[0][0]), int(kt_el.props[0][-1])
            span_lo = lo if span_lo is None else min(span_lo, lo)
            span_hi = hi if span_hi is None else max(span_hi, hi)

        new_uid = _FbxUtilsInternal._uid_allocator(objects_el)

        # ---- build the per-take clones ----
        new_objects, new_conns, new_takes = [], [], []
        clamped = []
        for name, start, end in takes:
            c_start, c_end = float(start), float(end)
            if span_lo is not None:
                c_start = max(c_start, span_lo / ktime_per_frame)
                c_end = max(min(c_end, span_hi / ktime_per_frame), c_start)
            if (c_start, c_end) != (float(start), float(end)):
                clamped.append(name)
            t0 = int(round(c_start * ktime_per_frame))
            t1 = int(round(c_end * ktime_per_frame))
            name_b = name.encode("utf-8")

            s_uid = new_uid()
            new_objects.append(
                _FbxUtilsInternal._make_parsed(
                    parse_mod,
                    b"AnimationStack",
                    [s_uid, name_b + b"\x00\x01AnimStack", b""],
                    b"LSS",
                    [
                        _FbxUtilsInternal._timestamp_props(
                            parse_mod,
                            [
                                (b"LocalStart", t0),
                                (b"LocalStop", t1),
                                (b"ReferenceStart", t0),
                                (b"ReferenceStop", t1),
                            ],
                        )
                    ],
                )
            )
            for luid in layer_uids:
                l_uid = new_uid()
                layer_clone = _FbxUtilsInternal._clone_parsed(parse_mod, by_uid[luid])
                layer_clone.props[0] = l_uid
                layer_clone.props[1] = name_b + b"\x00\x01AnimLayer"
                new_objects.append(layer_clone)
                new_conns.append(conn(b"OO", l_uid, s_uid))
                for cnuid in children_of(luid, oo):
                    if cnuid not in cn_uids:
                        continue
                    cn_uid = new_uid()
                    cn_clone = _FbxUtilsInternal._clone_parsed(parse_mod, by_uid[cnuid])
                    cn_clone.props[0] = cn_uid
                    new_objects.append(cn_clone)
                    new_conns.append(conn(b"OO", cn_uid, l_uid))
                    # Replicate the node → animated-property links.
                    for c in op.get(cnuid, []):
                        new_conns.append(conn(b"OP", cn_uid, c.props[2], c.props[3]))
                    for cuid in children_of(cnuid, op):
                        if cuid not in curve_uids:
                            continue
                        curve_clone = _FbxUtilsInternal._sliced_curve(
                            parse_mod, by_uid[cuid], t0, t1, ktime_per_frame
                        )
                        c_uid = new_uid()
                        curve_clone.props[0] = c_uid
                        new_objects.append(curve_clone)
                        for c in op[cuid]:
                            if c.props[2] != cnuid:
                                continue
                            new_conns.append(conn(b"OP", c_uid, cn_uid, c.props[3]))

            new_takes.append(_FbxUtilsInternal._take_element(parse_mod, name_b, t0, t1))

        if clamped:
            logger.warning(
                f"{len(clamped)} take window(s) extended past the baked "
                f"animation span and were clamped to it: {', '.join(clamped)}"
            )

        # ---- the whole timeline keeps a name no take uses ----
        stack_name = stack.props[1].split(b"\x00\x01")[0]
        names = {name.encode("utf-8") for name, _s, _e in takes}
        if stack_name in names:
            renamed, suffix = _FbxUtilsInternal._SEQUENCE_STACK, 0
            while renamed in names:
                suffix += 1
                renamed = b"%s_%d" % (_FbxUtilsInternal._SEQUENCE_STACK, suffix)
            stack.props[1] = renamed + b"\x00\x01AnimStack"
            for luid in layer_uids:
                by_uid[luid].props[1] = renamed + b"\x00\x01AnimLayer"
            for take in finds(takes_el, b"Take") if takes_el is not None else ():
                if take.props and take.props[0] == stack_name:
                    take.props[0] = renamed
                    file_el = find(take, b"FileName")
                    if file_el is not None:
                        file_el.props[0] = renamed + b".tak"
            logger.warning(
                f"A declared take is named {stack_name.decode('utf-8', 'replace')!r}, "
                "like the scene-range stack: the whole timeline ships as "
                f"{renamed.decode()!r}."
            )

        # ---- the take clones join the scene-range stack graph ----
        objects_el.elems.extend(new_objects)
        conns_el.elems.extend(new_conns)
        if takes_el is not None:
            takes_el.elems.extend(new_takes)
        n = len(takes)
        _FbxUtilsInternal._count_definitions(
            parse_mod,
            root,
            {
                b"AnimationStack": n,
                b"AnimationLayer": n * len(layer_uids),
                b"AnimationCurveNode": n * len(cn_uids),
                b"AnimationCurve": n * len(curve_uids),
            },
        )
        logger.info(
            f"Split animation into {n} take(s) beside the whole timeline: "
            + ", ".join(name for name, _s, _e in takes)
        )
        return n


class FbxUtils(_FbxUtilsInternal):
    """FBX import / export over ``bpy.ops`` (mirror of mayatk's ``FbxUtils`` export surface)."""

    # ------------------------------------------------------------------
    # Export metadata: producers, stagers and the bracket (mirror of mayatk)
    # ------------------------------------------------------------------
    #
    # A PRODUCER computes one scene record from live scene state and RETURNS
    # it; it never writes.  ``ptk.ExportSnapshot`` orders the producers by
    # the records' declared dependencies, hands each the ``ptk.ExportContext``
    # (the exporter's decisions as input, plus every record produced before
    # it) and commits the carrier ONCE, handoff block included.  A STAGER
    # mutates the scene for the write and undoes it after; it produces no
    # record.  Mirror of ``mtk.FbxUtils`` minus the session hook: bpy has no
    # before-export event, so the Scene Exporter's ``export_data_node`` task
    # is the one publish of an export, producers also publish at authoring
    # time (what a non-exporter write ships), and ``enable_export_producer``
    # records the opt-in for parity.

    #: The records this DCC produces: ``ptk.SceneRecords`` spec -> (module,
    #: class, classmethod taking the ExportContext).  Add a producer HERE and
    #: declare its record THERE; an unregistered key fails
    #: ``SceneRecords.check_producers`` (pinned by test_fbx_utils).  Resolved
    #: lazily, so an uninstalled subsystem is skipped rather than blocking an
    #: export.  Order is irrelevant: the snapshot orders by the records'
    #: ``after``.  Audio joins when its port lands.
    PRODUCERS: Dict[Any, Tuple[str, str, str]] = {
        ptk.SceneRecords.SHOTS: (
            "blendertk.anim_utils.shots._shots",
            "BlenderShotStore",
            "produce_export_records",
        ),
        ptk.SceneRecords.VISIBILITY: (
            "blendertk.mat_utils.render_opacity.render_effects",
            "RenderEffects",
            "export_record",
        ),
        ptk.SceneRecords.SHADOWS: (
            "blendertk.rig_utils.shadow_rig._shadow_rig",
            "ShadowRig",
            "export_record",
        ),
        ptk.SceneRecords.EMISSIVE_GROUPS: (
            "blendertk.mat_utils.emissive_groups",
            "EmissiveGroups",
            "export_record",
        ),
        ptk.SceneRecords.LIGHTMAPS: (
            "blendertk.light_utils.lightmap_baker.lightmap_records",
            "LightmapRecords",
            "export_record",
        ),
    }

    #: Export stagers: name -> (module, class, prepare, finish); a ``None``
    #: finish is a one-way stage; a stager registered for the session runs in
    #: every bracket.  A ``prepare`` that RETURNS objects staged transport
    #: nodes: they are recorded until its finish (:attr:`_staged`), and
    #: :meth:`export` ships each one whose parent ships -- Blender writes only
    #: what is selected, so a curve proxy hanging under an exported object
    #: would otherwise stay behind (mayatk's exporter carries a child with its
    #: parent).
    STAGERS: Dict[str, Tuple[str, str, str, Optional[str]]] = {
        # The curve-proxy transport (mirror of mayatk's row): one child Empty
        # per keyed render-effect channel, ``<object>__<channel>``, whose
        # scale.x carries the curve Unity's RenderEffectsImporter rebinds.
        "render_effects": (
            "blendertk.mat_utils.render_opacity.render_effects",
            "RenderEffects",
            "stage_export_proxies",
            "finish_export",
        ),
        # The same transport for keyed emissive weights (Blender only: Maya's
        # FBX carries the carrier's custom-attribute curves itself), one Empty
        # per keyed group under the data_export carrier.
        "emissive_groups": (
            "blendertk.mat_utils.emissive_groups",
            "EmissiveGroups",
            "create_export_curve_proxies",
            "remove_export_curve_proxies",
        ),
        # One-way: a marker baked before 2026-09-23 still carries its map's
        # folder, and markers ride every FBX -- lifted into the private record
        # before the write, so an old file ships clean without a re-bake.
        "lightmap_folder_hints": (
            "blendertk.light_utils.lightmap_baker.lightmap_records",
            "LightmapRecords",
            "migrate_folder_hints",
            None,
        ),
    }

    #: Record keys opted into a before-export publish.  Kept for parity with
    #: mayatk's session hook, whose registry this is: bpy offers no such
    #: event, so the opt-in is recorded and nothing here reads it -- the
    #: Scene Exporter's publish runs every producer anyway.
    _session_producers: Set[str] = set()
    #: Session stagers: name -> (prepare, finish), either may be None.
    _session_stagers: Dict[str, Tuple[Optional[Callable], Optional[Callable]]] = {}
    #: Depth of open :meth:`export_prepared` brackets (a class attribute: no
    #: OpenMaya callback survives a reload here, so no process-wide copy).
    _export_depth: int = 0
    #: The stager table the open bracket staged, finished when it closes.
    _bracket_stagers: Optional[
        Dict[str, Tuple[Optional[Callable], Optional[Callable]]]
    ] = None
    #: Stager name -> the transport nodes its ``prepare`` returned, until its
    #: ``finish`` runs.  While they stand, preparing that stager again is a
    #: no-op: a Blender object is a reference, so re-staging (which pre-cleans
    #: and rebuilds) would free the very nodes an export set already holds --
    #: where mayatk's re-stage by name is harmless.
    _staged: Dict[str, List[Any]] = {}

    @classmethod
    def producers(cls, only: Optional[Iterable[Any]] = None) -> Dict[Any, Callable]:
        """:attr:`PRODUCERS` resolved to callables, unimportable ones skipped;
        *only* (specs or keys) narrows the table."""
        import importlib

        wanted = (
            None if only is None else {ptk.SceneRecords.resolve(k).key for k in only}
        )
        table: Dict[Any, Callable] = {}
        for spec, (module_path, cls_name, method) in cls.PRODUCERS.items():
            if wanted is not None and spec.key not in wanted:
                continue
            try:
                owner = getattr(importlib.import_module(module_path), cls_name)
                table[spec] = getattr(owner, method)
            except Exception:  # an uninstalled subsystem is fine
                logger.debug(
                    "Producer for %r unavailable; skipped.", spec.key, exc_info=True
                )
        return table

    @classmethod
    def export_context(
        cls, mode: str = ptk.ExportContext.PIPELINE, **decisions
    ) -> ptk.ExportContext:
        """A context for this file: provenance filled in, *decisions*
        (``clip_mode``, ``clip_span``, ``rendering``, ``scope``) as given."""
        try:
            import bpy

            source = {
                "application": "blender",
                "version": bpy.app.version_string,
                "scene": os.path.basename(bpy.data.filepath or "") or None,
            }
        except ImportError:
            source = {"application": "blender"}
        return ptk.ExportContext(mode=mode, source=source, **decisions)

    @classmethod
    def publish(
        cls,
        ctx: Optional[ptk.ExportContext] = None,
        only: Optional[Iterable[Any]] = None,
    ) -> ptk.ExportSnapshot:
        """Assemble every producer's record and commit the carrier ONCE
        (mirror of ``mtk.FbxUtils.publish``; the WHY lives there).

        *ctx* carries the exporter's decisions (a pipeline context for this
        file by default); *only* narrows the run.  A hand-off context
        refreshes only the DERIVED records.  Each producer is isolated, and a
        failing one's record is left as stored.  Producers always see the
        staged scene: outside a bracket the session stagers' (idempotent)
        ``prepare`` runs first.

        Returns:
            ptk.ExportSnapshot: What was produced and written.
        """
        from blendertk.node_utils.data_nodes import DataNodes

        ctx = ctx or cls.export_context()
        if not cls._export_depth:
            cls._run_stagers("prepare", dict(cls._session_stagers))
        snapshot = ptk.ExportSnapshot.assemble(cls.producers(only), ctx)
        snapshot.commit(DataNodes)
        return snapshot

    @classmethod
    def publish_authored(cls, records) -> ptk.ExportSnapshot:
        """Commit records a tool already holds -- its AUTHORING-time publish
        (mirror of ``mtk.FbxUtils.publish_authored``).

        ``ptk.ExportSnapshot.publish`` under this file's authoring context, so
        the handoff block the commit restamps keeps its provenance.  Runs no
        stager: nothing is being written, and a tool republishing its own
        record must not stand a preview down.

        Parameters:
            records: Record spec (or key) -> a ``ptk.Record``, a payload, or a
                falsy value (the record is cleared).

        Returns:
            ptk.ExportSnapshot: The committed snapshot.
        """
        from blendertk.node_utils.data_nodes import DataNodes

        return ptk.ExportSnapshot.publish(
            DataNodes, records, cls.export_context(mode=ptk.ExportContext.AUTHORING)
        )

    # -- stagers ---------------------------------------------------------

    @classmethod
    def stagers(
        cls, names: Optional[Iterable[str]] = None
    ) -> Dict[str, Tuple[Optional[Callable], Optional[Callable]]]:
        """The stager table for a bracket: the known stagers (*names* narrows
        them; ``None`` = all) resolved to callables, then every session
        stager -- those always run."""
        import importlib

        table: Dict[str, Tuple[Optional[Callable], Optional[Callable]]] = {}
        for name, (module_path, cls_name, prepare, finish) in cls.STAGERS.items():
            if names is not None and name not in names:
                continue
            try:
                owner = getattr(importlib.import_module(module_path), cls_name)
            except Exception:
                logger.debug("Stager %r unavailable; skipped.", name, exc_info=True)
                continue
            table[name] = tuple(
                getattr(owner, method, None) if method else None
                for method in (prepare, finish)
            )
        table.update(cls._session_stagers)
        return table

    @classmethod
    def stage(cls, names: Optional[Iterable[str]] = None):
        """Run every stager's ``prepare`` now and return the table that ran
        (mirror of ``mtk.FbxUtils.stage``; ``prepare`` is idempotent)."""
        table = cls.stagers(names)
        cls._run_stagers("prepare", table)
        return table

    @staticmethod
    def _run_stagers(phase: str, table) -> None:
        """Run one *phase* (``"prepare"`` / ``"finish"``) of every stager in
        *table*, each isolated; ``finish`` runs in reverse order (LIFO).

        The objects a ``prepare`` returns are recorded as staged
        (:attr:`FbxUtils._staged`) and forgotten at its ``finish`` -- raising
        or not; a stager whose staged nodes all still stand is not prepared
        again (see :attr:`FbxUtils._staged`)."""
        items = list(table.items())
        if phase == "finish":
            items.reverse()
        for name, (prepare, finish) in items:
            if phase == "prepare" and FbxUtils._staged_nodes((name,)):
                continue  # staged and standing: the export set holds them
            fn = prepare if phase == "prepare" else finish
            if fn is None:
                if phase == "finish":
                    FbxUtils._staged.pop(name, None)
                continue
            try:
                result = fn()
            except Exception:  # one subsystem's failure must not block others
                result = None
                logger.warning(
                    "Export stager %r failed to %s.", name, phase, exc_info=True
                )
            if phase == "finish":
                FbxUtils._staged.pop(name, None)
                continue
            nodes = FbxUtils._as_staged(result)
            if nodes:
                FbxUtils._staged[name] = nodes

    @staticmethod
    def _as_staged(result) -> List[Any]:
        """The Blender objects among what a stager's ``prepare`` returned --
        a count, a list of names or ``None`` stage no node."""
        if not isinstance(result, (list, tuple)):
            return []
        try:
            import bpy
        except ImportError:
            return []
        return [n for n in result if isinstance(n, bpy.types.Object)]

    @staticmethod
    def _staged_nodes(names: Optional[Iterable[str]] = None) -> List[Any]:
        """The standing transport nodes the named stagers (``None``: all)
        staged; a stager one of whose nodes was removed since is forgotten,
        so its next ``prepare`` stages afresh."""
        found: List[Any] = []
        for name in list(FbxUtils._staged) if names is None else names:
            nodes = FbxUtils._staged.get(name)
            if not nodes:
                continue
            try:
                for node in nodes:
                    node.name  # a removed object raises here
            except ReferenceError:
                FbxUtils._staged.pop(name, None)
                continue
            found.extend(nodes)
        return found

    # -- the bracket -------------------------------------------------------

    @classmethod
    def begin_export(
        cls,
        ctx: Optional[ptk.ExportContext] = None,
        only: Optional[Iterable[Any]] = None,
        stagers: Optional[Iterable[str]] = None,
    ) -> Optional[ptk.ExportSnapshot]:
        """Open an export bracket (outermost only): stage the scene, then --
        when a *ctx* is given -- publish.  Mirror of mayatk's; pair with
        :meth:`end_export` in a ``finally``, or use :meth:`export_prepared`."""
        cls._export_depth += 1
        if cls._export_depth != 1:
            return None
        try:
            cls._bracket_stagers = cls.stage(stagers)
            return cls.publish(ctx, only) if ctx is not None else None
        except BaseException:
            # No ``end_export`` follows a bracket that failed to OPEN: finish
            # what was staged and leave the depth as it was found.
            cls._export_depth -= 1
            table, cls._bracket_stagers = cls._bracket_stagers, None
            if table is not None:
                cls._run_stagers("finish", table)
            raise

    @classmethod
    def end_export(cls) -> None:
        """Close an export bracket: run the stagers' finish (outermost only)."""
        if cls._export_depth <= 0:
            return
        cls._export_depth -= 1
        if cls._export_depth == 0:
            table, cls._bracket_stagers = cls._bracket_stagers, None
            cls._run_stagers("finish", table if table is not None else cls.stagers())

    @classmethod
    @contextlib.contextmanager
    def export_prepared(
        cls,
        ctx: Optional[ptk.ExportContext] = None,
        only: Optional[Iterable[Any]] = None,
        stagers: Optional[Iterable[str]] = None,
    ):
        """Stage the scene (and publish, given a *ctx*) for an export; finish
        on exit -- AFTER everything inside the block.  Yields the snapshot
        :meth:`begin_export` published, or ``None``."""
        snapshot = cls.begin_export(ctx, only, stagers)
        try:
            yield snapshot
        finally:
            cls.end_export()

    @classmethod
    @contextlib.contextmanager
    def scratch_export(cls):
        """Bracket for a THROWAWAY FBX write: nothing inside stages or
        publishes (mirror of mayatk's)."""
        cls._export_depth += 1
        try:
            yield
        finally:
            cls._export_depth = max(cls._export_depth - 1, 0)

    # -- opt-ins (parity with mayatk's session hook) ---------------------------

    @classmethod
    def enable_export_producer(cls, spec) -> None:
        """Record that *spec*'s producer wants to run before every export.
        Kept for API parity: Blender has no before-export event to run it
        from, and every producer already runs in the Scene Exporter's publish."""
        cls._session_producers.add(ptk.SceneRecords.resolve(spec).key)

    @classmethod
    def disable_export_producer(cls, spec) -> None:
        cls._session_producers.discard(ptk.SceneRecords.resolve(spec).key)

    @classmethod
    def register_export_stager(
        cls,
        name: str,
        prepare: Optional[Callable[[], Any]] = None,
        finish: Optional[Callable[[], Any]] = None,
    ) -> None:
        """Run *prepare* before and *finish* after every bracketed export.

        *prepare* must be idempotent (a publish outside a bracket runs it so
        the producers see the staged scene, and the bracket that follows runs
        it again), and *finish* must tolerate a second call (the Scene
        Exporter also finishes what its publish prepared, for a run that
        stops before its write).  Registering *name* again replaces the half
        given and keeps the other."""
        old_prepare, old_finish = cls._session_stagers.get(name, (None, None))
        cls._session_stagers[name] = (prepare or old_prepare, finish or old_finish)

    @classmethod
    def unregister_export_stager(cls, name: str) -> None:
        cls._session_stagers.pop(name, None)

    # ------------------------------------------------------------------
    # Animation takes (generic — any tool can declare takes on a node)
    # ------------------------------------------------------------------

    #: Armed take definitions consumed by the next :meth:`export` write(s) —
    #: the Blender analogue of Maya's sticky global exporter state (MEL
    #: ``FBXExportSplitAnimationIntoTakes``): applies to EVERY export until
    #: :meth:`reset_takes`, which is why the Scene Exporter's takes task
    #: stages that reset (see the module docstring's takes divergence note).
    _pending_takes = None

    #: Blender's multi-stack animation modes. Both are ON in the operator's
    #: defaults, and either one makes the exporter write a start-zeroed take
    #: per NLA strip / action INSTEAD of the scene-range take
    #: (``export_fbx_bin.fbx_animations``) -- a shape no toolkit write uses.
    MULTI_STACK_OPTIONS: Tuple[str, str] = (
        "bake_anim_use_nla_strips",
        "bake_anim_use_all_actions",
    )

    @classmethod
    def scene_range_take(cls, options: dict) -> list:
        """Pin *options* to ONE scene-range animation take: every
        :attr:`MULTI_STACK_OPTIONS` off. No-op unless *options* bakes
        animation.

        The toolkit's FBX contract: armed takes are split out of that one take
        after the write, :meth:`bake_range` is the span it carries (so the
        scene records' clip span is true by construction), and it is what
        mayatk's FBX writes.

        Returns:
            The keys *options* turned ON -- an explicit request a caller
            reports. An absent key is set too (the operator's default is ON),
            silently.
        """
        if not options.get("bake_anim"):
            return []
        asked = [key for key in cls.MULTI_STACK_OPTIONS if options.get(key)]
        for key in cls.MULTI_STACK_OPTIONS:
            options[key] = False
        return asked

    @staticmethod
    def bake_range(takes=None):
        """The ``(start, end)`` frames the next write will actually BAKE.

        Same question, and the same name, as mayatk's ``FbxUtils.bake_range``;
        the source differs because the exporters do. Maya keeps a sticky
        bake-complex range on the FBX plugin, so its twin reads that back.
        Blender has no such global -- the range is the SCENE's, which the write
        bakes over -- so this composes it from the scene range and the one rule
        the export applies to it whatever the range source: every declared take
        WIDENS it (``min``/``max``, never a narrowing), so each take's window
        lies inside the baked span.  ``apply_declared_takes`` widens the scene
        and ``set_bake_animation_range`` -- LAST in ``TASK_ORDER`` -- widens its
        own measurement in turn.

        The producers publish BEFORE both (``export_data_node`` precedes them
        in ``TASK_ORDER``), which is exactly why the widening has to be
        reproduced here rather than read off the scene: at publish time the
        scene does not yet carry it.  What the range source will then set is
        not known here either; the scene range stands in for it -- the seed
        for a publish outside the Scene Exporter, whose own publish predicts
        the range its Bake Range row will set (``_clip_origin_span``).

        Anyone describing the exported stack's ORIGIN needs this: a glTF
        converter rebases every stack onto its first key, so publishing the
        scene's earliest key instead slides every clip cut from that stack.

        Parameters:
            takes: The take list to widen by (``{"name","start","end"}``
                entries or ``(name, start, end)`` tuples); ``None`` reads the
                one the carrier declares.  A producer passes the takes its OWN
                assembly resolved (``ptk.SceneRecords.declared_takes`` over
                ``ctx.record``): the snapshot commits only after every producer
                has run, so the carrier still holds the PREVIOUS export's takes.

        Returns:
            The range, or None outside Blender / with no scene to read.
        """
        try:
            import bpy

            scene = bpy.context.scene
            start, end = float(scene.frame_start), float(scene.frame_end)
        except Exception as error:  # noqa: BLE001 -- a probe must not fail a write
            logger.debug(f"Could not read the scene frame range: {error}")
            return None

        for _name, take_start, take_end in FbxUtils._declared_take_bounds(takes):
            start, end = min(start, take_start), max(end, take_end)
        return (start, end)

    @staticmethod
    def _stored_takes():
        """The take list the carrier declares (``ptk.SceneRecords.declared_takes``
        over the stored records), empty when none is."""
        from blendertk.node_utils.data_nodes import DataNodes

        return ptk.SceneRecords.declared_takes(
            lambda key: ptk.SceneRecords.resolve(key).load(DataNodes)
        )

    @staticmethod
    def _declared_take_bounds(takes=None):
        """``(name, start, end)`` for each of *takes* -- by default the ones on
        the carrier -- malformed ones skipped.

        The carrier rather than ``_pending_takes``: the producers run before
        ``apply_declared_takes`` has armed anything, so the pending list is
        still empty when :meth:`bake_range` needs the answer.
        """
        if takes is None:
            takes = FbxUtils._stored_takes()
        for take in takes or ():
            try:
                if isinstance(take, dict):
                    yield str(take["name"]), float(take["start"]), float(take["end"])
                else:
                    yield str(take[0]), float(take[1]), float(take[2])
            except (KeyError, IndexError, TypeError, ValueError):
                continue  # one bad entry must not decide the whole range

    @staticmethod
    def reset_takes() -> None:
        """Clear the armed take definitions (mirror of ``mtk.FbxUtils.reset_takes``).

        The Maya twin also restores bake-complex MEL state; Blender's exporter
        options are plain per-write kwargs, so the pending list is the only
        sticky state to clear.
        """
        FbxUtils._pending_takes = None

    @staticmethod
    def apply_takes(takes) -> int:
        """Arm one FBX take (Unity AnimationClip) per entry for the coming export.

        EVERY subsequent :meth:`export` write consumes the armed state — it is
        sticky until :meth:`reset_takes`, mirroring Maya's global exporter
        state — by cloning the file's single baked scene-range AnimStack into
        windowed per-take stacks, kept beside it as Maya's ``Take 001`` is
        (see ``_split_animation_takes``).  The write
        must go out with
        ``bake_anim`` enabled and a scene frame range covering every take —
        the Scene Exporter's ``apply_declared_takes`` task guarantees both.

        Parameters:
            takes: Sequence of ``{"name","start","end"}`` mappings (what
                ``ptk.SceneRecords.declared_takes`` returns) or
                ``(name, start, end)`` tuples.

        Returns:
            int: Number of takes armed.  Empty input only clears state.
        """
        FbxUtils.reset_takes()

        norm = []
        for t in takes or []:
            if isinstance(t, dict):
                name, start, end = t["name"], t["start"], t["end"]
            else:
                name, start, end = t
            norm.append((str(name), int(round(start)), int(round(end))))

        if not norm:
            return 0
        FbxUtils._pending_takes = norm
        logger.info(
            f"Armed {len(norm)} FBX take(s) for the next export: "
            + ", ".join(n for n, _s, _e in norm)
        )
        return len(norm)

    @staticmethod
    def stage_curve_proxy(name: str, parent, fcurve, *markers: str):
        """Stage one transient Empty whose ``scale.x`` carries *fcurve*, for an FBX write.

        The curve-proxy transport shared by every producer that has to ship a
        per-object (or per-group) float curve: Blender's FBX exporter cannot
        ship custom-property animation, and Unity flattens what does arrive
        onto the root Animator with empty paths. Object transform animation,
        by contrast, keeps its hierarchy path through every consumer -- and
        scale is the one channel unit conversion never touches. The Empty is
        parented under *parent*, linked into its collections, keyed with each
        keyframe of *fcurve* (interpolation copied per key) and stamped with
        every *markers* custom property -- always including
        ``ptk.MeshConvert.CURVE_PROXY_MARKER``, which the GLB conversion strips
        on and the Unity importer recognises.

        Returns:
            The created proxy object, or ``None`` when *name* is already taken
            (logged by the caller, who knows what the curve was for).
        """
        import bpy

        from blendertk.anim_utils._anim_utils import AnimUtils

        if bpy.data.objects.get(name) is not None:
            return None
        proxy = bpy.data.objects.new(name, None)  # None data -> Empty
        for marker in set(markers) | {ptk.MeshConvert.CURVE_PROXY_MARKER}:
            proxy[marker] = True
        proxy.parent = parent
        collections = list(getattr(parent, "users_collection", ()) or ())
        if not collections:
            scene = getattr(bpy.context, "scene", None)
            collections = [scene.collection] if scene is not None else []
        for coll in collections:
            coll.objects.link(proxy)
        # keyframe_insert rather than action.fcurves.new -- slot-aware across
        # Blender 4.4+/5.x -- then copy each key's interpolation. Keyed on the
        # frame rounded to 1e-4, not to a whole number: sub-frame keys
        # (0.4 / 0.6) would otherwise collide onto one entry.
        for kp in sorted(fcurve.keyframe_points, key=lambda k: k.co[0]):
            proxy.scale[0] = kp.co[1]
            proxy.keyframe_insert(data_path="scale", index=0, frame=kp.co[0])
        dst = next(
            (
                f
                for f in AnimUtils.get_fcurves([proxy])
                if f.data_path == "scale" and f.array_index == 0
            ),
            None,
        )
        if dst is not None:
            interp = {
                round(k.co[0], 4): k.interpolation for k in fcurve.keyframe_points
            }
            for k in dst.keyframe_points:
                k.interpolation = interp.get(round(k.co[0], 4), k.interpolation)
        return proxy

    @staticmethod
    def apply_takes_from_node(node=None, attr=None) -> int:
        """Arm the takes the scene declares for the next write.

        Defaults to the shot record on the shared carrier -- each
        ``shot_metadata`` clip's range, or the legacy ``fbx_takes`` channel of
        a file published before 0.8.0 -- so this is shot-agnostic (mirror of
        ``mtk.FbxUtils.apply_takes_from_node``; *node* is an object name here).
        An explicit *node* / *attr* reads a JSON take list off any object.

        Returns:
            int: Number of takes armed (0 if nothing is declared).
        """
        import json

        from blendertk.node_utils.data_nodes import DataNodes

        if node is None and attr is None:
            defs = FbxUtils._stored_takes()
        else:
            attr = attr or ptk.SceneRecords.FBX_TAKES.key
            if node is None:
                obj = DataNodes.get_export_node(create=False)
            else:
                import bpy

                obj = bpy.data.objects.get(node) if isinstance(node, str) else node
            raw = obj.get(attr) if obj is not None else None
            if not raw:
                return 0
            try:
                defs = json.loads(raw)
            except (ValueError, TypeError):
                logger.warning(f"Could not parse take defs from {obj.name}.{attr}")
                return 0
        if not defs:
            return 0
        return FbxUtils.apply_takes(defs)

    @staticmethod
    def export(
        filepath=None, objects=None, selection_only=True, strict=False, **fbx_opts
    ):
        """Export to an FBX file — the consolidated counterpart of mayatk's ``FbxUtils.export``.

        Parameters:
            filepath: output ``.fbx`` path (``.fbx`` appended if missing; parent dirs created).
                Defaults to ``<temp>/<blend-stem>_bridge.fbx``.
            objects: objects (datablocks or names) to export; ``None`` exports the current
                selection. When given, they are selected first and the prior selection is
                restored afterward.
            selection_only: ``True`` exports the selection (``use_selection``); ``False`` exports
                the whole scene.
            strict: the selection funnel can only ship selectable, visible objects — a
                hidden member of *objects* silently fails ``select_set`` and one in a
                view-layer-excluded collection makes it RAISE, so unselectable members
                are collected instead and logged as a WARNING (count + first names):
                content loss must never be silent. ``strict=True`` raises
                ``RuntimeError`` with that list instead of exporting without them.
                The ``data_export`` carrier (with any curve proxy staged under
                it) is the exception: a metadata node, it is shown for the write
                and hidden again after it.
            **fbx_opts: overrides merged over the defaults, forwarded to
                ``bpy.ops.export_scene.fbx``.

        What the deliverable's own nodes need is the write's to guarantee, not
        the caller's: the carrier and every staged curve proxy ship readable
        (``use_custom_props``, ``EMPTY`` -- forced, and said, over options
        that would drop them); a proxy the open staging made ships with the
        object it hangs under, and stays out with it when that object cannot
        be selected (:attr:`STAGERS`); a light the shadow record names as a
        plane's source passes a type filter without ``LIGHT``; and an animated
        write carries each shipped object's keyed ``hide_render`` as its FBX
        ``Visibility`` curve, which the exporter does not write.

        Returns:
            str: the written FBX path.

        Raises:
            RuntimeError: ``selection_only`` and nothing is selected to export,
                or ``strict`` and a requested object cannot be selected.
        """
        import bpy
        import tempfile

        if not filepath:
            stem = (
                os.path.splitext(os.path.basename(bpy.data.filepath))[0] or "untitled"
            )
            filepath = os.path.join(tempfile.gettempdir(), f"{stem}_bridge.fbx")
        if not filepath.lower().endswith(".fbx"):
            filepath += ".fbx"
        os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)

        opts = dict(_EXPORT_DEFAULTS)
        opts["use_selection"] = selection_only
        opts.update(fbx_opts)
        if "object_types" in opts:
            opts["object_types"] = _FbxUtilsInternal._as_object_types(
                opts["object_types"]
            )
        # Templates vendored from mayatk carry Maya MEL FBX names (e.g. FBXExportEmbeddedTextures);
        # translate them to export_scene.fbx kwargs so they don't fault the Blender exporter.
        opts = _FbxUtilsInternal._translate_fbx_options(opts)

        # ONE scene-range take for every animated write (scene_range_take). The
        # two multi-stack modes are the operator's DEFAULTS, so a caller passing
        # only bake_anim=True got per-action start-zeroed takes and no
        # scene-range take at all: armed takes had nothing to split, and the
        # data_export carrier's clip span -- seeded from the scene range the
        # write bakes -- described a take the file did not hold.
        asked = FbxUtils.scene_range_take(opts)
        if asked:
            logger.warning(
                "FBX export: %s overridden to False -- every animated write is ONE "
                "scene-range take (the takes, the scene records' clip span and "
                "mayatk's FBX all assume it).",
                ", ".join(asked),
            )

        # Selection is read via the window-independent ``selected_objects`` (view layer), never
        # ``bpy.context.selected_objects`` — the latter raises AttributeError from tentacle's Qt
        # event-pump timer (``bpy.context.window is None``). The operators run under
        # ``window_context_override`` because ``export_scene.fbx``'s io_scene_fbx handler *itself*
        # reads ``context.selected_objects`` internally, so a window must be in context for it.
        # ``preserved_selection`` puts the caller's selection (and active object) back
        # after a write that selected *objects* -- even when the write raises -- the
        # scope mayatk's export takes.
        with CoreUtils.window_context_override(), CoreUtils.preserved_selection():
            # What ships: the objects asked for, else the selection -- plus
            # each transport node the open staging made under one of them
            # (STAGERS). A whole-scene write ships the scene, which the type
            # filter alone narrows.
            if objects is not None:
                shipping = _FbxUtilsInternal._resolved_objects(objects)
            elif selection_only:
                shipping = list(CoreUtils.selected_objects())
            else:
                shipping = None
            riders = set()  # the staged nodes' pointers (see the selection below)
            if selection_only and shipping:
                staged = FbxUtils._staged_nodes()
                riders = {node.as_pointer() for node in staged}
                shipping += [
                    node
                    for node in staged
                    if node.parent in shipping and node not in shipping
                ]
            written = (
                shipping if selection_only else list(bpy.context.scene.objects)
            ) or []
            # The deliverable's own nodes ship readable whatever the caller
            # passed (the shared step its callers also take for their reports).
            repaired = FbxUtils._force_carrier_readability(written, opts)
            if repaired:
                overruled = [
                    r for r in repaired if r.partition("=")[0].rstrip("+") in fbx_opts
                ]
                (logger.warning if overruled else logger.info)(
                    "FBX export ships the data_export carrier or a curve proxy: "
                    "forced %s%s.",
                    ", ".join(repaired),
                    " over the options given" if overruled else "",
                )
            left_out = (
                FbxUtils._admit_shadow_sources(written, opts) if selection_only else []
            )

            with FbxUtils._carriers_shippable(shipping or ()):
                dropped = []
                if shipping is not None:
                    bpy.ops.object.select_all(action="DESELECT")
                    # Staged nodes last: each ships only beside the object it
                    # hangs under, so one whose object was dropped (hidden,
                    # say) stays out too -- re-rooted, its curve would reach
                    # every Renderer in Unity. The drop is reported for the
                    # object itself.
                    for obj in sorted(shipping, key=lambda o: o.as_pointer() in riders):
                        if obj in left_out:
                            continue
                        parent = obj.parent if obj.as_pointer() in riders else None
                        if parent is not None:
                            try:
                                if not parent.select_get():
                                    continue
                            except RuntimeError:  # outside the layer
                                continue
                        # An unselectable object must not kill the whole export
                        # (an excluded-collection member makes select_set RAISE),
                        # but it will be silently absent from the FBX — a hidden
                        # object "succeeds" without selecting. Compare requested
                        # vs actually-selected and surface the difference below.
                        try:
                            obj.select_set(True)
                            selected = obj.select_get()
                        except RuntimeError:
                            selected = False
                        if not selected:
                            dropped.append(obj.name)

                if dropped:
                    shown = ", ".join(dropped[:10]) + (
                        " …" if len(dropped) > 10 else ""
                    )
                    msg = (
                        f"{len(dropped)} requested object(s) cannot be selected and "
                        f"will be DROPPED from the FBX (hidden, selection-locked, or "
                        f"outside the active view layer): {shown}"
                    )
                    if strict:
                        raise RuntimeError(msg)
                    logger.warning(msg)
                if selection_only and not CoreUtils.selected_objects():
                    raise RuntimeError("Nothing selected to export.")
                scene = bpy.context.scene
                with _FbxUtilsInternal._range_covering(
                    scene,
                    (FbxUtils._pending_takes or ()) if opts.get("bake_anim") else (),
                ):
                    bpy.ops.export_scene.fbx(filepath=filepath, **opts)
                    # One rewrite of the file for what the exporter cannot
                    # write: the keyed visibility (_add_visibility_curves), and
                    # the armed takes, consumed by every write until
                    # reset_takes -- the Maya-parity sticky-state semantics (see
                    # apply_takes). A failing rewrite raises: the promised
                    # per-shot clips are the write's contract, and the
                    # single-take file on disk saying otherwise must not pass
                    # as success.
                    visibility = (
                        _FbxUtilsInternal._visibility_curves(
                            written, scene.frame_start, scene.frame_end
                        )
                        if opts.get("bake_anim")
                        else {}
                    )
                    if visibility or FbxUtils._pending_takes:
                        _FbxUtilsInternal._rewrite_animation(
                            filepath,
                            FbxUtils._pending_takes,
                            visibility,
                            (scene.frame_start, scene.frame_end),
                            scene.name,
                        )
        return filepath

    @staticmethod
    def import_fbx(filepath, **fbx_opts):
        """Import an FBX file (wrapper over ``bpy.ops.import_scene.fbx``).

        Args:
            filepath: the ``.fbx`` to import (``$VARS`` expanded). Raises ``FileNotFoundError`` if
                absent.
            **fbx_opts: forwarded to ``bpy.ops.import_scene.fbx``.

        Returns:
            list: the objects created by the import (those newly added to ``bpy.data.objects``).

        ``anim_offset`` defaults to 0.0 here, not Blender's 1.0: an imported curve keeps
        the frames it was authored on, which is what Maya's own importer does.

        Every sibling armature binds its meshes here, which the stock importer does not do
        (``env_utils.upstream_patches.SIBLING_ARMATURES``) -- so this wrapper is the only
        supported way to import an FBX that carries more than one skeleton.
        """
        import bpy

        filepath = os.path.abspath(os.path.expandvars(filepath))
        if not os.path.isfile(filepath):
            raise FileNotFoundError(f"FBX not found: {filepath}")
        # Blender's importer defaults this to 1.0, so a curve authored at frames
        # 1-10 arrives at 2-11 and every Maya -> Blender -> Maya hop drifts a frame
        # further (measured on a production module: 3845/4738 -> 3846/4739 ->
        # 3847/4740). Maya's importer shifts nothing, so 0.0 is what makes the two
        # agree. A caller that wants the shift still passes it.
        fbx_opts.setdefault("anim_offset", 0.0)
        before = set(bpy.data.objects)
        # Same contract as export above: io_scene_fbx reads context internally
        # (it selects the imported objects), so a window must be in context —
        # driven bare from tentacle's Qt event-pump timer, context.window is
        # None and the op raises.
        with CoreUtils.window_context_override(), SIBLING_ARMATURES.applied():
            bpy.ops.import_scene.fbx(filepath=filepath, **fbx_opts)
        return [o for o in bpy.data.objects if o not in before]

    @staticmethod
    def scene_settings(filepath):
        """The time setup an FBX file itself carries, as a (partial) ``scene`` record
        (see ``EnvUtils.SCENE_SETTINGS_KEYS``): ``fps`` from ``GlobalSettings``
        (``TimeMode`` / ``CustomFrameRate``) and the animation range as the union of
        the AnimationStacks' ``LocalStart`` / ``LocalStop`` (what Maya's
        *Fill Timeline* reads), else ``GlobalSettings`` ``TimeSpanStart`` /
        ``TimeSpanStop`` — Maya writes both; Blender's exporter writes a dummy 0-1 s
        global span and the real range on the stack. ktime → frames at that fps. The
        fallback for a source with no conversion manifest — Blender's importer applies
        the fps but drops the span. Keys the file doesn't pin are omitted; ``{}`` for
        an unparsable file.
        """
        from io_scene_fbx import parse_fbx
        from io_scene_fbx.fbx_utils import FBX_FRAMERATES

        filepath = os.path.abspath(os.path.expandvars(filepath))
        try:
            root, version = parse_fbx.parse(filepath)
        except Exception:  # noqa: BLE001 — a record, never a failed import
            return {}
        gs = _FbxUtilsInternal._find_elem(root, b"GlobalSettings")
        props = gs and _FbxUtilsInternal._find_elem(gs, b"Properties70")
        raw = {}
        for p in props.elems if props else ():
            if p.id == b"P" and p.props:
                raw[p.props[0]] = p.props[-1]
        # Same resolution as the importer: a named TimeMode wins, else CustomFrameRate.
        by_mode = {eid: val for val, eid in FBX_FRAMERATES[1:]}
        fps = by_mode.get(raw.get(b"TimeMode"), raw.get(b"CustomFrameRate"))
        out = {}
        if fps and float(fps) > 0:
            out["fps"] = float(fps)
        spans = []
        objects = _FbxUtilsInternal._find_elem(root, b"Objects")
        for stack in (
            _FbxUtilsInternal._find_elems(objects, b"AnimationStack") if objects else ()
        ):
            sprops = _FbxUtilsInternal._find_elem(stack, b"Properties70")
            local = {}
            for p in sprops.elems if sprops else ():
                if p.id == b"P" and p.props:
                    local[p.props[0]] = p.props[-1]
            if b"LocalStop" in local:
                spans.append((local.get(b"LocalStart", 0), local[b"LocalStop"]))
        if not spans:
            start, stop = raw.get(b"TimeSpanStart"), raw.get(b"TimeSpanStop")
            if start is not None and stop is not None:
                spans.append((start, stop))
        if "fps" in out and spans:
            start = min(s for s, _ in spans)
            stop = max(e for _, e in spans)
            if stop > start:
                per_frame = _FbxUtilsInternal._file_ktime(root, version) / out["fps"]
                out["anim_start"] = int(round(start / per_frame))
                out["anim_end"] = int(round(stop / per_frame))
        return out

    @staticmethod
    def export_selection_fbx(filepath=None, objects=None, strict=False, **fbx_opts):
        """Export the selection (or ``objects``) to an FBX file for an external-app hand-off.

        The non-interactive counterpart of the scene slot's "Export Selection" — used by the
        Substance / Marmoset / RizomUV bridges to stage the current selection. Thin selection-only
        alias for :meth:`FbxUtils.export` (``strict`` passes through — see there).
        """
        return FbxUtils.export(
            filepath=filepath,
            objects=objects,
            selection_only=True,
            strict=strict,
            **fbx_opts,
        )

    @staticmethod
    def embed_dependencies(
        file_path: str,
        search_dirs: Optional[Iterable[str]] = None,
        logger: Optional[logging.Logger] = None,
    ) -> Optional[Dict[str, Any]]:
        """Embed every file a written FBX's scene records name, in place.

        The records name their files by file name -- ``lightmap_metadata`` its
        maps and reflection probe, ``shadow_metadata`` its silhouette, atlas
        and horizon maps -- and a lighting-only bake binds its maps to no
        material, so the FBX carried none of them and every consumer had them
        copied in beside it. ``ptk.FbxMedia.embed_dependencies`` puts each
        inside the file under its name, the form Unity's importer extracts,
        resolved where this scene keeps it: *search_dirs*, else
        :meth:`LightmapRecords.search_dirs` -- the folders the GLB build joins
        the same names against. The Scene Exporter runs it on its FBX after
        the GLB conversion, which binds its own copies. Mirror of mayatk's.

        Never raises -- a failure is a warning and the file stays as written.
        A file that is not a binary FBX is left alone.

        Parameters:
            file_path: The FBX just written from this scene.
            search_dirs: Folders to resolve the names against, in priority
                order; ``None`` asks the scene.
            logger: Where the outcome is said; this module's otherwise.

        Returns:
            ``ptk.FbxMedia.embed_dependencies``'s report (``"named"``,
            ``"embedded"``, ``"present"``, ``"missing"``, ``"bytes"``), or
            ``None`` when the file is not a binary FBX or the pass failed.
        """
        log = logger or logging.getLogger(__name__)
        if not ptk.FbxFile.is_fbx(file_path):
            return None
        try:
            if search_dirs is None:
                from blendertk.light_utils.lightmap_baker.lightmap_records import (
                    LightmapRecords,
                )

                search_dirs = LightmapRecords.search_dirs()
            return ptk.FbxMedia.embed_dependencies(
                file_path, search_dirs=list(search_dirs), logger=log
            )
        except Exception as error:  # noqa: BLE001 -- the deliverable already shipped
            log.warning(f"FBX dependencies: not embedded -- the pass failed: {error}")
            log.debug("Dependency pass failed.", exc_info=True)
            return None
