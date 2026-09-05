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
  consumes it right after the ``bpy.ops`` write by splitting the file's single scene-range
  AnimStack into one windowed AnimStack per take (``_split_animation_takes``, built on the
  FBX addon's own ``parse_fbx`` / ``encode_bin``). Like Maya's, the armed state applies to
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
import logging
from typing import Iterable, Optional

import pythontk as ptk

logger = logging.getLogger(__name__)

# Window-independent selection reader + window-supplying override for the Qt event-pump timer
# context (``bpy.context.window`` is ``None`` there — see ``_core_utils.selected_objects``). Both
# import Qt-free / bpy-deferred, so importing this module never needs a running Blender.
from blendertk.core_utils._core_utils import CoreUtils

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
        the exporter writes these curves with linear tangents.
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
        eps = int(ktime_per_frame * 1e-3)  # sub-millframe tolerance
        mask = (kt >= t0 - eps) & (kt <= t1 + eps)
        new_t = list(kt[mask])
        new_v = list(kv[mask])
        # np.interp clamps outside the key range — correct here: the take
        # window is clamped to the baked span by the caller, and a constant
        # tail extends at its held value.
        if not new_t or new_t[0] > t0 + eps:
            new_t.insert(0, t0)
            new_v.insert(0, float(np.interp(t0, kt, kv)))
        if new_t[-1] < t1 - eps:
            new_t.append(t1)
            new_v.append(float(np.interp(t1, kt, kv)))

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

    @staticmethod
    def _split_animation_takes(filepath, takes) -> int:
        """Rewrite *filepath* in place: one windowed AnimStack per declared take.

        The just-written file carries the exporter's single baked scene-range
        AnimStack (the ``_force_scene_range_take`` invariant). For each
        ``(name, start, end)`` take the stack graph — stack, layer(s), curve
        nodes, curves — is cloned with fresh uids, the curves' keys windowed to
        the take's span (kept in absolute scene time, as Maya's split takes
        are), and the ``Takes`` index rebuilt; the original scene-range stack
        is then removed, so the file ships exactly the declared takes.

        That last part is where the two DCCs' ARTIFACTS differ, measured
        2026-08-28 on Maya 2025 + FBX2glTF 0.13.1: Maya's own splitter keeps
        its whole-timeline ``Take 001`` alongside the takes it was asked for,
        so a Maya FBX (and the GLB converted from it) carries N+1 stacks where
        this carries N. The public surface is still the mirror it claims to be
        — same call, same declared takes, same names — and a consumer that
        selects clips by name cannot tell the difference; one that plays the
        FIRST clip can. ``MeshConvert.apply_glb_animations`` is what makes that
        answerable from either file (it marks which clips a shot declared).

        Take windows are clamped to the baked span — content beyond
        it cannot exist in the source curves; the Scene Exporter's
        ``apply_declared_takes`` task widens the scene range up front so
        clamping never bites on that path.

        Returns the number of takes written; 0 (file untouched) when the file
        holds no single baked AnimStack to split.
        """
        from io_scene_fbx import parse_fbx, encode_bin

        root, version = parse_fbx.parse(filepath)
        objects_el = _FbxUtilsInternal._find_elem(root, b"Objects")
        conns_el = _FbxUtilsInternal._find_elem(root, b"Connections")
        takes_el = _FbxUtilsInternal._find_elem(root, b"Takes")
        stacks = (
            _FbxUtilsInternal._find_elems(objects_el, b"AnimationStack")
            if objects_el
            else []
        )
        if conns_el is None or len(stacks) != 1:
            logger.warning(
                f"Cannot split animation takes: expected one baked AnimStack in "
                f"{os.path.basename(filepath)}, found {len(stacks)} "
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
            props = [kind, child_uid, parent_uid]
            if prop is not None:
                props.append(prop)
            return _FbxUtilsInternal._make_parsed(
                parse_fbx, b"C", props, b"SLLS" if prop is not None else b"SLL"
            )

        layer_uids = [
            e.props[0]
            for e in _FbxUtilsInternal._find_elems(objects_el, b"AnimationLayer")
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
            kt_el = _FbxUtilsInternal._find_elem(by_uid[u], b"KeyTime")
            if kt_el is None or not len(kt_el.props[0]):
                continue
            lo, hi = int(kt_el.props[0][0]), int(kt_el.props[0][-1])
            span_lo = lo if span_lo is None else min(span_lo, lo)
            span_hi = hi if span_hi is None else max(span_hi, hi)

        # ---- fresh uids ----
        existing_uids = {
            e.props[0]
            for e in objects_el.elems
            if e.props and isinstance(e.props[0], int)
        }
        uid_counter = max(existing_uids) if existing_uids else 1

        def new_uid():
            nonlocal uid_counter
            while True:
                uid_counter += 1
                if uid_counter >= 2**63 - 1:
                    uid_counter = 1
                if uid_counter not in existing_uids:
                    break
            existing_uids.add(uid_counter)
            return uid_counter

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
                    parse_fbx,
                    b"AnimationStack",
                    [s_uid, name_b + b"\x00\x01AnimStack", b""],
                    b"LSS",
                    [
                        _FbxUtilsInternal._timestamp_props(
                            parse_fbx,
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
                layer_clone = _FbxUtilsInternal._clone_parsed(parse_fbx, by_uid[luid])
                layer_clone.props[0] = l_uid
                layer_clone.props[1] = name_b + b"\x00\x01AnimLayer"
                new_objects.append(layer_clone)
                new_conns.append(conn(b"OO", l_uid, s_uid))
                for cnuid in children_of(luid, oo):
                    if cnuid not in cn_uids:
                        continue
                    cn_uid = new_uid()
                    cn_clone = _FbxUtilsInternal._clone_parsed(parse_fbx, by_uid[cnuid])
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
                            parse_fbx, by_uid[cuid], t0, t1, ktime_per_frame
                        )
                        c_uid = new_uid()
                        curve_clone.props[0] = c_uid
                        new_objects.append(curve_clone)
                        for c in op[cuid]:
                            if c.props[2] != cnuid:
                                continue
                            new_conns.append(conn(b"OP", c_uid, cn_uid, c.props[3]))

            take_el = _FbxUtilsInternal._make_parsed(
                parse_fbx,
                b"Take",
                [name_b],
                b"S",
                [
                    _FbxUtilsInternal._make_parsed(
                        parse_fbx, b"FileName", [name_b + b".tak"], b"S"
                    ),
                    _FbxUtilsInternal._make_parsed(
                        parse_fbx, b"LocalTime", [t0, t1], b"LL"
                    ),
                    _FbxUtilsInternal._make_parsed(
                        parse_fbx, b"ReferenceTime", [t0, t1], b"LL"
                    ),
                ],
            )
            new_takes.append(take_el)

        if clamped:
            logger.warning(
                f"{len(clamped)} take window(s) extended past the baked "
                f"animation span and were clamped to it: {', '.join(clamped)}"
            )

        # ---- swap the scene-range stack graph for the take clones ----
        removed_uids = {stack_uid, *layer_uids, *cn_uids, *curve_uids}
        objects_el.elems[:] = [
            e for e in objects_el.elems if not (e.props and e.props[0] in removed_uids)
        ] + new_objects
        conns_el.elems[:] = [
            c
            for c in conns_el.elems
            if not (
                c.id == b"C"
                and c.props
                and (c.props[1] in removed_uids or c.props[2] in removed_uids)
            )
        ] + new_conns
        if takes_el is not None:
            takes_el.elems[:] = [
                e for e in takes_el.elems if e.id != b"Take"
            ] + new_takes

        # ---- keep the Definitions reference counts honest ----
        n = len(takes)
        counts = {
            b"AnimationStack": n,
            b"AnimationLayer": n * len(layer_uids),
            b"AnimationCurveNode": n * len(cn_uids),
            b"AnimationCurve": n * len(curve_uids),
        }
        defs_el = _FbxUtilsInternal._find_elem(root, b"Definitions")
        delta = 0
        for ot in (
            _FbxUtilsInternal._find_elems(defs_el, b"ObjectType") if defs_el else []
        ):
            if ot.props and ot.props[0] in counts:
                count_el = _FbxUtilsInternal._find_elem(ot, b"Count")
                if count_el is not None:
                    delta += counts[ot.props[0]] - count_el.props[0]
                    count_el.props[0] = counts[ot.props[0]]
        total_el = defs_el and _FbxUtilsInternal._find_elem(defs_el, b"Count")
        if total_el is not None:
            total_el.props[0] += delta

        # ---- re-encode in place ----
        enc_root = encode_bin.FBXElem(b"")
        for child in root.elems:
            enc_root.elems.append(
                _FbxUtilsInternal._parsed_to_encode(child, encode_bin)
            )
        encode_bin.write(filepath, enc_root, version)
        logger.info(
            f"Split animation into {n} take(s): "
            + ", ".join(name for name, _s, _e in takes)
        )
        return n


class FbxUtils(_FbxUtilsInternal):
    """FBX import / export over ``bpy.ops`` (mirror of mayatk's ``FbxUtils`` export surface)."""

    # The declarative list of known metadata producers that stamp the shared
    # ``data_export`` carrier: name → (module, class, no-arg refresh method).
    # Mirror of mayatk's ``FbxUtils._KNOWN_PRODUCERS``, minus the session-hook
    # half: bpy has no before-FBX-export event, so the Scene Exporter's
    # ``export_data_node`` task is the only refresh dispatch point (producers
    # additionally publish at authoring time, which is what non-exporter paths
    # ship). Audio joins here when its port lands. Add new producers HERE —
    # nothing else needs to change. Resolved lazily; an unimportable producer
    # is skipped (never blocks an export).
    #
    # ORDER IS A CONTRACT (dict insertion order = run order, same as mayatk's
    # rank sort): a producer that reads another's channel must come after it.
    # The mayatk twin runs shots before audio because audio scopes its events
    # against the freshly published ``fbx_takes`` — the audio port must land
    # after shots here too.
    _KNOWN_PRODUCERS = {
        "shots": (
            "blendertk.anim_utils.shots._shots",
            "BlenderShotStore",
            "refresh_export_view",
        ),
        # After "shots": it reads back the fbx_takes and fps that shots has
        # just republished, to place each gate against its own clip's zero.
        "visibility": (
            "blendertk.mat_utils.render_opacity.render_effects",
            "RenderEffects",
            "refresh_export_metadata",
        ),
        "shadow": (
            "blendertk.rig_utils.shadow_rig",
            "ShadowRig",
            "refresh_export_metadata",
        ),
        "emissive_groups": (
            "blendertk.mat_utils.emissive_groups",
            "EmissiveGroups",
            "refresh_export_metadata",
        ),
        "lightmap": (
            "blendertk.light_utils.lightmap_baker.lightmap_baker",
            "LightmapBaker",
            "refresh_export_metadata",
        ),
    }

    #: Session preparers, name -> no-arg callable (mirror of mayatk's
    #: ``_export_preparers``, minus the auto-export hook bpy cannot offer). A
    #: preparer registered under a KNOWN producer's name replaces it for the
    #: run and must therefore do that producer's refresh itself -- the horizon
    #: preview's ``"shadow"`` preparer hands the planes back their visibility
    #: and then republishes the metadata.
    _export_preparers: dict = {}

    @staticmethod
    def register_export_preparer(name: str, prepare) -> None:
        """Run *prepare* whenever the export preparers run this session
        (mirror of mayatk's; there is no before-export event in bpy, so the
        Scene Exporter's refresh is the dispatch point). Re-registering a
        name replaces it; :meth:`unregister_export_preparer` removes it."""
        FbxUtils._export_preparers[name] = prepare

    @staticmethod
    def unregister_export_preparer(name: str) -> None:
        FbxUtils._export_preparers.pop(name, None)

    @staticmethod
    def run_export_preparers(only: Optional[Iterable[str]] = None) -> None:
        """Refresh every known producer's ``data_export`` channel once, right now.

        Each producer is isolated — one failing or unimportable subsystem never
        blocks the others — and each no-ops (or clears its channel) when it has
        nothing to write, so scene edits since the last authoring-time publish
        (a deleted lightmapped mesh, a removed shadow plane) can't ship a stale
        manifest.  This is the one call an export pipeline needs to make the
        carrier current — name + behavior mirror of
        ``mtk.FbxUtils.run_export_preparers``.

        *only* narrows the run to the named producers.  Because a producer with
        nothing to publish CLEARS its channel, refreshing the whole set is safe
        only where the producers are the authority on every channel — an export
        pipeline.  A hand-off that merely SHIPS the carrier must not clear a
        manifest it cannot regenerate, so it names the channels that are derived
        from live scene state and leaves the rest as authored.
        """
        import importlib

        wanted = None if only is None else set(only)
        # Known producers in their contract order; a session preparer of the
        # same name stands in for the producer, others run after (mirror of
        # mayatk's rank sort).
        ordered = list(FbxUtils._KNOWN_PRODUCERS) + [
            n for n in FbxUtils._export_preparers if n not in FbxUtils._KNOWN_PRODUCERS
        ]
        for name in ordered:
            if wanted is not None and name not in wanted:
                continue
            session = FbxUtils._export_preparers.get(name)
            if session is not None:
                try:
                    session()
                except Exception:
                    logger.warning("Export preparer %r failed.", name, exc_info=True)
                continue
            module_path, cls_name, method = FbxUtils._KNOWN_PRODUCERS[name]
            try:
                producer = getattr(importlib.import_module(module_path), cls_name)
                refresh = getattr(producer, method)
            except Exception:
                # Producers are speculative — an uninstalled subsystem is fine.
                logger.debug("Producer %r unavailable; skipped.", name, exc_info=True)
                continue
            try:
                refresh()
            except Exception:
                # But a resolvable producer that fails would silently ship
                # stale channels — surface it.
                logger.warning("Producer %r refresh failed.", name, exc_info=True)
        FbxUtils._stamp_export_handoff()

    @staticmethod
    def _stamp_export_handoff() -> None:
        """Publish the standalone-reader contract describing the carrier's channels.

        Mirror of mayatk's method of the same name; the WHY lives there. A
        FINALIZER rather than a ``_KNOWN_PRODUCERS`` entry because it describes
        what the producers wrote and must therefore run after all of them. Text
        and schema come from ``ptk.MeshConvert.build_fbx_handoff``, so the two
        packages -- which cannot import each other -- cannot drift on what an
        FBX deliverable claims about itself.

        Never creates the carrier and never stamps an empty one; fully
        best-effort, so a missing description can never fail an export.
        """
        try:
            import bpy

            from blendertk.node_utils.data_nodes import DataNodes

            if DataNodes.get_export_node(create=False) is None:
                return
            channels = (DataNodes.dump(decode=False) or {}).get("data_export") or {}
            block = ptk.MeshConvert.build_fbx_handoff(
                channels,
                source={
                    "application": "blender",
                    "version": bpy.app.version_string,
                    # Provenance, not identity — see the builder's docstring.
                    "scene": os.path.basename(bpy.data.filepath or "") or None,
                },
            )
            DataNodes.set_export_json(ptk.MeshConvert.FBX_HANDOFF_CHANNEL, block)
        except Exception:  # noqa: BLE001 — a missing description never costs the export
            logger.debug("Export handoff block not stamped.", exc_info=True)

    # ------------------------------------------------------------------
    # Animation takes (generic — any tool can declare takes on a node)
    # ------------------------------------------------------------------

    #: Armed take definitions consumed by the next :meth:`export` write(s) —
    #: the Blender analogue of Maya's sticky global exporter state (MEL
    #: ``FBXExportSplitAnimationIntoTakes``): applies to EVERY export until
    #: :meth:`reset_takes`, which is why the Scene Exporter's takes task
    #: stages that reset (see the module docstring's takes divergence note).
    _pending_takes = None

    @staticmethod
    def bake_range():
        """The ``(start, end)`` frames the next write will actually BAKE.

        Same question, and the same name, as mayatk's ``FbxUtils.bake_range``;
        the source differs because the exporters do. Maya keeps a sticky
        bake-complex range on the FBX plugin, so its twin reads that back.
        Blender has no such global -- the range is the SCENE's, which the write
        bakes over -- so this composes it from the two things that set it, in
        the order the export applies them:

        * ``set_bake_animation_range`` puts the exported objects' evaluated
          keyframe extent on the scene, and
        * ``apply_declared_takes`` then WIDENS that to cover every declared
          take (``min``/``max`` against the scene range, never a narrowing), so
          each take's window lies inside the baked span.

        The producers publish BETWEEN those two (``TASK_ORDER``: after
        ``set_bake_animation_range``, inside ``export_data_node``, before
        ``apply_declared_takes``), which is exactly why the widening has to be
        reproduced here rather than read off the scene: at publish time the
        scene does not yet carry it.

        Anyone describing the exported stack's ORIGIN needs this: a glTF
        converter rebases every stack onto its first key, so publishing the
        scene's earliest key instead slides every clip cut from that stack.

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

        for _name, take_start, take_end in FbxUtils._declared_take_bounds():
            start, end = min(start, take_start), max(end, take_end)
        return (start, end)

    @staticmethod
    def _declared_take_bounds():
        """``(name, start, end)`` for each take on the carrier, malformed ones skipped.

        Read from the carrier rather than ``_pending_takes``: the producers run
        before ``apply_declared_takes`` has armed anything, so the pending list
        is still empty when :meth:`bake_range` needs the answer.
        """
        import json

        from blendertk.node_utils.data_nodes import DataNodes

        try:
            raw = DataNodes.get_export_string("fbx_takes")
            takes = json.loads(raw) if raw else []
        except Exception:  # noqa: BLE001 -- absent or unparseable channel
            return
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
        state — by splitting the file's single baked scene-range AnimStack into
        windowed per-take stacks (see ``_split_animation_takes``).  The write
        must go out with
        ``bake_anim`` enabled and a scene frame range covering every take —
        the Scene Exporter's ``apply_declared_takes`` task guarantees both.

        Parameters:
            takes: Sequence of ``{"name","start","end"}`` mappings (the
                ``fbx_takes`` channel shape) or ``(name, start, end)`` tuples.

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
        """Read take defs from a JSON channel on *node* and arm them.

        Defaults to the shared ``data_export`` carrier's ``fbx_takes`` channel,
        so this is shot-agnostic — it realizes whatever takes the scene
        declares.  Mirror of ``mtk.FbxUtils.apply_takes_from_node`` (*node* is
        an object name here; Maya passes a node path).

        Returns:
            int: Number of takes armed (0 if the channel is absent/empty).
        """
        import json

        from blendertk.node_utils.data_nodes import DataNodes

        attr = attr or DataNodes.FBX_TAKES
        if node is None:
            obj = DataNodes.get_export_node(create=False)
        else:
            import bpy

            obj = bpy.data.objects.get(node) if isinstance(node, str) else node
        if obj is None:
            return 0
        raw = obj.get(attr) or None  # a cleared channel is stored as ""
        if not raw:
            return 0
        try:
            defs = json.loads(raw)
        except (ValueError, TypeError):
            logger.warning(f"Could not parse take defs from {obj.name}.{attr}")
            return 0
        return FbxUtils.apply_takes(defs)

    @staticmethod
    def export(
        filepath=None, objects=None, selection_only=True, strict=False, **fbx_opts
    ):
        """Export to an FBX file — the consolidated counterpart of mayatk's ``FbxUtils.export``.

        Args:
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
            **fbx_opts: overrides merged over the defaults, forwarded to
                ``bpy.ops.export_scene.fbx``.

        Returns:
            str: the written FBX path. Raises ``RuntimeError`` when ``selection_only`` and nothing
            is selected to export.
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

        # Armed takes are cut from the write's single scene-range AnimStack, so
        # the two multi-stack modes must be off for this write — with either
        # left on (they are Blender's operator DEFAULTS, so a caller passing
        # only bake_anim=True gets them), the exporter writes per-action
        # start-zeroed stacks and no scene-range stack exists to split.
        if FbxUtils._pending_takes and opts.get("bake_anim"):
            for key in ("bake_anim_use_nla_strips", "bake_anim_use_all_actions"):
                if opts.get(key, True):
                    opts[key] = False
                    logger.debug(f"Animation takes armed — forced {key}=False.")

        # Selection is read via the window-independent ``selected_objects`` (view layer), never
        # ``bpy.context.selected_objects`` — the latter raises AttributeError from tentacle's Qt
        # event-pump timer (``bpy.context.window is None``). The operators run under
        # ``window_context_override`` because ``export_scene.fbx``'s io_scene_fbx handler *itself*
        # reads ``context.selected_objects`` internally, so a window must be in context for it.
        prior = list(CoreUtils.selected_objects()) if objects is not None else None
        with CoreUtils.window_context_override():
            dropped = []
            if objects is not None:
                bpy.ops.object.select_all(action="DESELECT")
                for o in ptk.make_iterable(objects):
                    obj = bpy.data.objects.get(o) if isinstance(o, str) else o
                    if obj is None:
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

            # Guard is inside the try so the finally restores the caller's selection even when it
            # raises (e.g. ``objects`` given but all names resolved to nothing — the DESELECT above
            # already cleared the real selection).
            try:
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
                bpy.ops.export_scene.fbx(filepath=filepath, **opts)
                # Armed takes are consumed by every write until reset_takes —
                # the Maya-parity sticky-state semantics (see apply_takes). A
                # failing split raises: the promised per-shot clips are the
                # write's contract, and the single-take file on disk saying
                # otherwise must not pass as success.
                if FbxUtils._pending_takes:
                    _FbxUtilsInternal._split_animation_takes(
                        filepath, FbxUtils._pending_takes
                    )
            finally:
                if prior is not None:  # restore the user's selection
                    bpy.ops.object.select_all(action="DESELECT")
                    for o in prior:
                        try:
                            o.select_set(True)
                        except (ReferenceError, RuntimeError):
                            # deleted since capture, or no longer selectable
                            # (e.g. its collection was view-layer-excluded) —
                            # a best-effort restore must not fail the export
                            # that already succeeded.
                            pass
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
        """
        import bpy

        filepath = os.path.abspath(os.path.expandvars(filepath))
        if not os.path.isfile(filepath):
            raise FileNotFoundError(f"FBX not found: {filepath}")
        before = set(bpy.data.objects)
        # Same contract as export above: io_scene_fbx reads context internally
        # (it selects the imported objects), so a window must be in context —
        # driven bare from tentacle's Qt event-pump timer, context.window is
        # None and the op raises.
        with CoreUtils.window_context_override():
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
