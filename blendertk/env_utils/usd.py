# !/usr/bin/python
# coding=utf-8
"""USD import / export helpers — the Blender counterpart of mayatk's ``env_utils.usd``
(``btk.UsdUtils`` ↔ ``mtk.UsdUtils``).

Mirrors the module + class name and the export/import surface over Blender's native
USD runtime (``bpy.ops.wm.usd_export`` / ``wm.usd_import``), which already converts
Principled BSDF ↔ ``UsdPreviewSurface``, keeps instancing (``use_instancing``), and
round-trips custom properties. Two intentional divergences from mayatk:

* **No plugin/namespace layer.** Maya needs ``load_plugin`` (mayaUsdPlugin) and
  active-namespace isolation; Blender's USD ops are built in and Blender has no
  namespaces — imported objects are simply returned (datablock diff), matching
  ``FbxUtils.import_fbx``'s contract.
* **Native ``.usdz``.** Blender packages ``.usdz`` itself (a ``.usdz`` filepath is
  enough); Maya-side ``.usdz`` composes ``pythontk.UsdzPackager`` instead. The shared
  zero-dep floor (sniffing/packaging) still lives in ``pythontk.file_utils.usd``.

Option names drift across Blender majors (4.x ↔ 5.x renamed several ``usd_export``
kwargs), so kwargs are filtered against the operator's live RNA properties — an
option this Blender doesn't know is dropped with a log line instead of faulting the
export (the same resilience contract as ``fbx_utils._translate_fbx_options``).

``import bpy`` is deferred into the call bodies so resolving the package surface
never requires a running Blender.
"""

from typing import Any, Dict, List, Optional, Tuple
import os
import re
import math

import pythontk as ptk

# Window-independent selection reader + window-supplying override for the Qt
# event-pump timer context (see ``fbx_utils`` — same contract: the USD io
# handlers read ``context`` internally, so a window must be in context).
from blendertk.core_utils._core_utils import CoreUtils

#: Extensions the USD runtime reads/writes (shared SSoT with pythontk).
USD_EXTENSIONS = ptk.USD_EXTENSIONS

# Interchange-quality export defaults (mirror of mayatk's intent: materials as
# preview surface, textures alongside, instancing preserved, no animation).
_EXPORT_DEFAULTS = {
    "selected_objects_only": True,
    "export_materials": True,
    "generate_preview_surface": True,
    "export_textures": True,
    "relative_paths": True,
    "use_instancing": True,
    "export_animation": False,
}

# Blender is Z-up; every hand-off target is Y-up, and mayaUsd 0.30 reads a layer
# WITHOUT converting (probed: `upAxis` is inert on import) -- a Z-up stage lands
# in Maya rotated +90 about X (production report 2026-08-22). The exporter bakes
# the conversion onto the root prims (rotateXYZ -90 X beside the unit scale),
# exactly where the FBX exporter's axis conversion lands; a Y-up stage is also
# what a consumer that DOES honor `upAxis` reads without any work.
_Y_UP_EXPORT_OPTIONS = {
    "convert_orientation": True,
    "export_global_forward_selection": "NEGATIVE_Z",
    "export_global_up_selection": "Y",
}
_EXPORT_DEFAULTS.update(_Y_UP_EXPORT_OPTIONS)


class _UsdUtilsInternal(object):
    """Internal helpers for UsdUtils."""

    @staticmethod
    def _reveal(objects):
        """Clear every hide flag on *objects* for an export; return the state
        to restore. All three: the exporter skips an object by ``hide_render``
        in its default RENDER evaluation and by ``hide_viewport`` / the eye in
        VIEWPORT evaluation (probed on 5.1), so only a fully revealed object
        is certain to reach the layer."""
        state = []
        for o in objects:
            try:
                eye = o.hide_get()
            except RuntimeError:  # not in the view layer
                eye = None
            if not (o.hide_viewport or o.hide_render or eye):
                continue
            state.append((o, o.hide_viewport, o.hide_render, eye))
            o.hide_viewport = False
            o.hide_render = False
            if eye:
                o.hide_set(False)
        return state

    @staticmethod
    def _restore_hidden(state):
        for o, monitor, render, eye in state:
            try:
                o.hide_viewport = monitor
                o.hide_render = render
                if eye:
                    o.hide_set(True)
            except (ReferenceError, RuntimeError):
                continue

    @staticmethod
    def _mute_visibility_curves(objects):
        """Mute every ``hide_*`` fcurve on *objects*; return the curves muted, for
        the caller to unmute.

        A hidden object is not evaluated AT ALL -- its constraints included
        (probed on 5.1: a constrained object with ``hide_viewport`` on reports the
        same matrix at every frame), so anything that samples a transform per
        frame reads a stale one and writes a FLAT curve. Clearing the flags is not
        enough on its own: a keyed object re-hides itself at the next frame."""
        from blendertk.anim_utils._anim_utils import AnimUtils

        muted = []
        for fc in AnimUtils.get_fcurves(objects):
            if AnimUtils._is_visibility_fcurve(fc) and not fc.mute:
                fc.mute = True
                muted.append(fc)
        return muted

    @staticmethod
    def _filter_op_options(op, options):
        """*options* restricted to kwargs *op* actually declares, a new dict.

        Blender renames USD operator options between majors; a stale name must
        degrade to a logged drop, not fault the whole export (mirror of the FBX
        option-translation contract). Never filters out ``filepath``.
        """
        known = {p.identifier for p in op.get_rna_type().properties}
        kept, dropped = {}, []
        for key, value in options.items():
            if key in known:
                kept[key] = value
            else:
                dropped.append(key)
        if dropped:
            import logging

            logging.getLogger(__name__).warning(
                "USD option(s) unknown to this Blender dropped: %s", ", ".join(dropped)
            )
        return kept


class UsdUtils(_UsdUtilsInternal):
    """USD import / export over ``bpy.ops`` (mirror of mayatk's ``UsdUtils``)."""

    EXTENSIONS = USD_EXTENSIONS

    #: The hand-off set every USD carrier composes from -- the Blender->Maya pull
    #: route's live-verified ``wm.usd_export`` kwargs, measured reason by reason in
    #: its conversion template: materials as ``UsdPreviewSurface``, the ORIGINAL
    #: texture files referenced rather than copied (a scratch payload's copied
    #: ``textures/`` dir would be swept under the consumer; both spellings so the
    #: RNA filter keeps the one this Blender knows), absolute paths (a deliverable
    #: beside its scene overrides to relative), one prim per object and no
    #: ``/root`` wrapper (flat parity with the FBX route's structure on the Maya
    #: side), instancing flattened, hidden objects included. Mirror of mayatk's
    #: ``UsdUtils.INTERCHANGE_EXPORT_OPTIONS`` in Blender's own names. Compose,
    #: never mutate: ``dict(UsdUtils.INTERCHANGE_EXPORT_OPTIONS, relative_paths=True)``.
    INTERCHANGE_EXPORT_OPTIONS = {
        "export_materials": True,
        "generate_preview_surface": True,
        "export_textures_mode": "KEEP",  # 4.2+ / 5.x name
        "export_textures": False,  # pre-4.2 name
        "relative_paths": False,
        "use_instancing": False,
        "export_animation": False,
        "merge_parent_xform": True,
        "root_prim_path": "",
        "visible_objects_only": False,  # pre-5.x only; dropped elsewhere
        **_Y_UP_EXPORT_OPTIONS,
    }

    #: ``wm.usd_import`` kwargs every hand-off importer starts from: EVERY prim
    #: (the importer's default skips invisible ones outright -- a Maya-hidden
    #: bake-source set vanished from a production pull, 2026-08-22; they arrive
    #: hidden through :meth:`apply_visibility` instead), the UsdPreviewSurface
    #: networks as Principled BSDFs, and the hierarchy as authored: the
    #: importer's ``merge_parent_xform`` default folds a single-child Xform
    #: into its child, so a Maya group holding one mesh lost its group (and
    #: the mesh its prim path -- the instance replay keys on it). Compose,
    #: never mutate.
    INTERCHANGE_IMPORT_OPTIONS = {
        "import_visible_only": False,
        "import_usd_preview": True,
        "merge_parent_xform": False,
    }

    @staticmethod
    def is_usd_file(filepath) -> bool:
        """True when *filepath* is a USD layer/package (delegates to pythontk)."""
        return ptk.UsdFile.is_usd_file(filepath)

    @staticmethod
    def export(
        filepath=None,
        objects=None,
        selection_only=True,
        frame_range=None,
        include_hidden=True,
        **usd_opts,
    ):
        """Export to a USD file — the counterpart of mayatk's ``UsdUtils.export``.

        Args:
            filepath: output path (``.usd`` appended when no USD extension is
                given; parent dirs created). A ``.usdz`` path produces a
                packaged archive (Blender packages natively). Defaults to
                ``<temp>/<blend-stem>_bridge.usd``.
            objects: objects (datablocks or names) to export; ``None`` exports the
                current selection. When given, they are selected first and the prior
                selection is restored afterward.
            selection_only: ``True`` exports the selection
                (``selected_objects_only``); ``False`` exports the whole scene.
            frame_range: ``(start, end)`` to sample when ``export_animation`` is
                on. Blender's exporter reads ``scene.frame_start/end`` rather
                than taking a range, so the scene range is set for the call and
                RESTORED afterwards -- the artist's playback range is theirs
                (see :meth:`sampling_frame_range` for why narrowing matters).
            include_hidden: ``True`` (default) carries viewport-hidden objects
                (``hide_viewport`` / the view layer's eye) as INVISIBLE prims,
                the way FBX carries them. Blender's exporter has no such mode
                -- it skips a hidden object outright in every evaluation mode
                (probed on 5.1) -- so the hidden set is revealed for the call,
                restored afterwards, and their prims stamped
                ``visibility = invisible`` (:meth:`mark_invisible`). ``False``
                is the exporter's own behavior: hidden objects are left out.
                Object flags only: an object hidden through its COLLECTION
                (excluded or hidden in the view layer) is not revealed.
            **usd_opts: overrides merged over the defaults, forwarded to
                ``bpy.ops.wm.usd_export`` (unknown-to-this-Blender names are
                dropped with a log line).

        Returns:
            str: the written USD path. Raises ``RuntimeError`` when
            ``selection_only`` and nothing is selected to export.
        """
        import bpy
        import tempfile

        if not filepath:
            stem = (
                os.path.splitext(os.path.basename(bpy.data.filepath))[0] or "untitled"
            )
            filepath = os.path.join(tempfile.gettempdir(), f"{stem}_bridge.usd")
        if os.path.splitext(filepath)[1].lower() not in USD_EXTENSIONS:
            filepath += ".usd"
        os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)

        opts = dict(_EXPORT_DEFAULTS)
        opts["selected_objects_only"] = selection_only
        opts.update(usd_opts)
        # Blender 5.1 drops an animated object's Mesh when merge_parent_xform and
        # export_animation are both on (see fold_single_mesh_xforms): an animated
        # export runs unmerged and is folded back to the merged shape afterwards.
        fold = bool(opts.get("export_animation")) and bool(
            opts.get("merge_parent_xform")
        )
        if fold and os.path.splitext(filepath)[1].lower() == ".usdz":
            # A package can't be folded in place; unmerged is the lossless shape
            # (Xform + child Mesh), the merged one drops the animated meshes.
            import logging

            logging.getLogger(__name__).warning(
                "Animated .usdz export: written UNMERGED (Xform + child Mesh per "
                "object) -- Blender's merged export drops animated meshes and a "
                "package cannot be folded after the fact."
            )
        if fold:
            opts["merge_parent_xform"] = False
        # Where the exporter puts the prims: its own default (``/root``) when
        # the caller did not say -- the invisible stamp matches by full path.
        root_prim_path = opts.get(
            "root_prim_path",
            bpy.ops.wm.usd_export.get_rna_type().properties["root_prim_path"].default,
        )
        opts = _UsdUtilsInternal._filter_op_options(bpy.ops.wm.usd_export, opts)

        wanted = None
        if objects is not None:
            wanted = [
                bpy.data.objects.get(o) if isinstance(o, str) else o
                for o in ptk.make_iterable(objects)
            ]
            wanted = [o for o in wanted if o is not None]
        # The hidden set travels: every hide flag on the export pool cleared
        # for the exporter (which would otherwise skip the object), the
        # viewport-hidden ones stamped invisible in the layer afterwards,
        # the flags restored regardless.
        hidden, revealed = [], []
        if include_hidden:
            pool = wanted
            if pool is None:  # the selection (hidden objects cannot be in it)
                pool = CoreUtils.selected_objects() if selection_only else None
            hidden = UsdUtils.hidden_objects(pool)
            revealed = _UsdUtilsInternal._reveal(
                bpy.data.objects if pool is None else pool
            )

        prior = list(CoreUtils.selected_objects()) if objects is not None else None
        scene = bpy.context.scene
        prior_range = (scene.frame_start, scene.frame_end)
        with CoreUtils.window_context_override():
            if wanted is not None:
                bpy.ops.object.select_all(action="DESELECT")
                for obj in wanted:
                    obj.select_set(True)
            try:
                if selection_only and not CoreUtils.selected_objects():
                    raise RuntimeError("Nothing selected to export.")
                if frame_range:
                    scene.frame_start, scene.frame_end = (
                        int(frame_range[0]),
                        int(frame_range[1]),
                    )
                bpy.ops.wm.usd_export(filepath=filepath, **opts)
                if fold:
                    UsdUtils.fold_single_mesh_xforms(filepath)
                # After the fold: it RENAMES prims, and these passes address
                # them by path. Unconditional -- each is a no-op on a layer
                # without the defect it repairs.
                UsdUtils.pin_primvar_indices(filepath)
                UsdUtils.mark_skinning_methods(filepath, wanted, root_prim_path)
                UsdUtils.mark_container_skeletons(filepath)
                if hidden:
                    stamped = UsdUtils.mark_invisible(filepath, hidden, root_prim_path)
                    if stamped < len(hidden):
                        import logging

                        logging.getLogger(__name__).warning(
                            f"{len(hidden) - stamped} hidden object(s) have no prim "
                            f"at their expected path under {root_prim_path!r}; "
                            "they arrive VISIBLE."
                        )
            finally:
                _UsdUtilsInternal._restore_hidden(revealed)
                if frame_range:
                    scene.frame_start, scene.frame_end = prior_range
                if prior is not None:  # restore the user's selection
                    bpy.ops.object.select_all(action="DESELECT")
                    for o in prior:
                        try:
                            o.select_set(True)
                        except ReferenceError:
                            pass
        return filepath

    @staticmethod
    def sampling_frame_range(
        objects: Optional[List[Any]] = None,
    ) -> Optional[Tuple[float, float]]:
        """The frames a USD export is worth sampling, or ``None`` for a static one.

        USD has no animation *curves* -- ``export_animation`` writes a time sample
        per frame for every prim, so the range is a direct multiplier on export
        cost (the Maya-side mirror measured 234s -> 1.8s on a static 755-object
        module). So sample only what moves:

        * nothing animated -> ``None`` (static export)
        * constraints / drivers / NLA present -> the scene's full range: motion
          with no keys of its own, so no narrower range can be read off
        * actions only -> their ``frame_range`` union, clamped to the scene range
          (a stray far-out key must not multiply the sample count)

        Args:
            objects: scope the question to these objects (a selection send);
                ``None`` asks the whole current scene.
        """
        import bpy

        scene = bpy.context.scene
        objects = list(objects) if objects is not None else list(scene.objects)
        lo = hi = None
        for ob in objects:
            ob = bpy.data.objects.get(ob) if isinstance(ob, str) else ob
            if ob is None:
                continue
            if ob.constraints:
                return (scene.frame_start, scene.frame_end)
            data = getattr(ob, "data", None)
            owners = (ob, data, getattr(data, "shape_keys", None))
            for ad in [getattr(o, "animation_data", None) for o in owners if o]:
                if ad is None:
                    continue
                if ad.nla_tracks or ad.drivers:
                    return (scene.frame_start, scene.frame_end)
                if ad.action:
                    a, b = ad.action.frame_range
                    lo = a if lo is None else min(lo, a)
                    hi = b if hi is None else max(hi, b)
        if lo is None:
            return None
        # floor/ceil, not int(): int() truncates toward zero, which would clip a
        # key at 20.5 down to 20 (losing motion) and mis-round negative frames.
        start = max(scene.frame_start, math.floor(lo))
        end = min(scene.frame_end, math.ceil(hi))
        if end < start:  # keys live entirely outside the scene's own time
            return None
        return (start, end)

    @staticmethod
    def fold_single_mesh_xforms(filepath: str) -> int:
        """Fold every ``Xform`` whose only child is a ``Mesh`` into one Mesh prim
        that carries the Xform's ops -- the shape ``merge_parent_xform=True``
        would have written. Rewrites *filepath* in place; returns the fold count.

        Why this exists: Blender 5.1's exporter DROPS an animated object's Mesh
        when ``merge_parent_xform`` and ``export_animation`` are both on (probe:
        a keyed cube exports as a bare ``Xform``; static objects and unmerged
        exports are fine), so an animated export runs unmerged and is folded
        back here. A Mesh prim is Xformable, so the ops are valid on it; the
        mesh's own GeomSubsets and bindings move with it. A Mesh child carrying
        ops of its own is left alone. A ``.usdz`` package is not editable in
        place and is returned untouched (0).
        """
        import os

        if os.path.splitext(str(filepath))[1].lower() == ".usdz":
            return 0
        from pxr import Sdf, Usd, UsdGeom

        layer = Sdf.Layer.FindOrOpen(str(filepath))
        if layer is None:
            return 0
        stage = Usd.Stage.Open(layer)
        targets = []
        for prim in stage.Traverse():
            if prim.GetTypeName() != "Xform":
                continue
            kids = prim.GetChildren()
            if (
                len(kids) == 1
                and kids[0].GetTypeName() == "Mesh"
                and not UsdGeom.Xformable(kids[0]).GetOrderedXformOps()
            ):
                targets.append((prim.GetPath(), kids[0].GetPath()))
        for xf_path, mesh_path in targets:
            xf_spec = layer.GetPrimAtPath(xf_path)
            for attr in list(xf_spec.attributes):
                if attr.name.startswith("xformOp:") or attr.name == "xformOpOrder":
                    Sdf.CopySpec(
                        layer, attr.path, layer, mesh_path.AppendProperty(attr.name)
                    )
            parent = xf_path.GetParentPath()
            tmp_name = xf_path.name + "__fold"
            # Rename BEFORE reparenting: a mesh datablock named like its object
            # (``/mover/mover``) would otherwise collide with the Xform it is
            # about to replace ("cannot be an ancestor of itself").
            edit = Sdf.BatchNamespaceEdit()
            edit.Add(Sdf.NamespaceEdit.Rename(mesh_path, tmp_name))
            edit.Add(
                Sdf.NamespaceEdit.Reparent(xf_path.AppendChild(tmp_name), parent, -1)
            )
            edit.Add(Sdf.NamespaceEdit.Remove(xf_path))
            edit.Add(
                Sdf.NamespaceEdit.Rename(parent.AppendChild(tmp_name), xf_path.name)
            )
            if not layer.Apply(edit):
                raise RuntimeError(f"USD fold failed for {xf_path}")
        if targets:
            layer.Save()
        return len(targets)

    @staticmethod
    def sanitize_prim_name(name: str) -> str:
        """*name* as Blender's USD exporter spells the prim (probe-verified on 5.1:
        ``Chair.001`` -> ``Chair_001``; a leading digit is PREFIXED, ``1digit`` ->
        ``_1digit``): every char outside ``[A-Za-z0-9_]`` becomes ``_``. The
        mirror of mayatk's ``UsdUtils.sanitize_prim_name``; the pull templates
        carry dependency-free copies kept in step by hand."""
        if not name:
            return "_"
        name = re.sub(r"[^A-Za-z0-9_]", "_", name)
        if name[0].isdigit():
            name = "_" + name
        return name

    @staticmethod
    def hidden_objects(objects: Optional[List[Any]] = None) -> List[Any]:
        """The viewport-hidden objects among *objects* (default: every object in
        the file): ``hide_viewport`` (the monitor toggle) or the view layer's eye
        (``hide_get``). What Maya's ``visibility`` off means, and what an FBX
        carries as hidden; ``hide_render`` alone is a render setting, not a
        hidden object."""
        import bpy

        out = []
        for o in bpy.data.objects if objects is None else objects:
            try:
                hidden = o.hide_viewport or o.hide_get()
            except RuntimeError:  # not in the view layer: the eye is undefined
                hidden = o.hide_viewport
            if hidden:
                out.append(o)
        return out

    @staticmethod
    def export_prim_path(obj, root_prim_path: str = "") -> str:
        """The prim path Blender's exporter writes for *obj* (one prim per
        object, ``merge_parent_xform``): its parent chain, each name spelled
        through :meth:`sanitize_prim_name`, under *root_prim_path*."""
        parts, cur = [], obj
        while cur is not None:
            parts.append(UsdUtils.sanitize_prim_name(cur.name))
            cur = cur.parent
        return (root_prim_path or "").rstrip("/") + "/" + "/".join(reversed(parts))

    @staticmethod
    def prim_path(obj) -> str:
        """The prim path an IMPORTED *obj* came from: its parent chain with the
        importer's ``.NNN`` collision suffixes stripped (a ``.NNN`` suffix can
        ONLY be a collision rename; prim names cannot contain dots). Prim paths
        are unique, so duplicate LEAF names (``/g1/wheel`` vs ``/g2/wheel``)
        stay unambiguous even after the importer renames one."""
        parts, cur = [], obj
        while cur is not None:
            parts.append(re.sub(r"\.\d+$", "", cur.name))
            cur = cur.parent
        return "/" + "/".join(reversed(parts))

    @staticmethod
    def pin_primvar_indices(filepath: str) -> int:
        """Give every indexed primvar a DEFAULT index array, copied from its lone
        time sample when that is the only place one was authored. Returns the count.

        Blender 5.1 writes a SKINNED mesh's UVs split across time: the values at
        the default, the indices as a single sample at the export's first frame.
        Measured on a production module -- 7 of 7 skinned meshes affected, 0 of
        1498 unskinned. A consumer reading the primvar at the default time then
        finds values with no mapping, and mayaUsd refuses the set outright
        ("Unable to retrieve and assign data for UV set <st>"), landing the mesh
        with the right number of UV COORDINATES and not one assigned face --
        silently, since the import still reports success and only warns.

        The mapping is present, just not where a default-time read looks, so this
        copies it down rather than rebuilding it; the samples are left in place.
        A primvar that already authors a default is untouched, as is a ``.usdz``
        package (not editable in place, returns 0).
        """
        import os

        if os.path.splitext(str(filepath))[1].lower() == ".usdz":
            return 0
        from pxr import Usd, UsdGeom

        stage = Usd.Stage.Open(str(filepath))
        if stage is None:
            return 0
        count = varying = 0
        for prim in stage.Traverse():
            for primvar in UsdGeom.PrimvarsAPI(prim).GetPrimvars():
                attr = primvar.GetIndicesAttr()
                if not attr or not attr.HasAuthoredValue() or attr.Get() is not None:
                    continue  # absent, or already readable at the default
                samples = attr.GetTimeSamples()
                if len(samples) != 1:
                    # Exactly one sample is the measured defect: a constant
                    # mapping written to the wrong place. Several samples mean
                    # the indices genuinely vary over time, and pinning the
                    # first would publish one frame's mapping as the answer for
                    # all of them -- a quieter wrong than the bug being fixed.
                    varying += bool(samples)
                    continue
                indices = attr.Get(samples[0])
                if indices is None:
                    continue
                attr.Set(indices)
                count += 1
        if varying:
            import logging

            logging.getLogger(__name__).warning(
                f"{varying} primvar(s) index over TIME with no default; left as "
                "authored (pinning one sample would misstate the others). A "
                "consumer reading at the default time will not see them."
            )
        if count:
            stage.GetRootLayer().Save()
        return count

    @staticmethod
    def mark_skinning_methods(
        filepath: str, objects: Optional[List[Any]] = None, root_prim_path: str = ""
    ) -> int:
        """Stamp each skinned mesh's SKINNING METHOD into the layer, read from its
        Armature modifier's Preserve Volume. Returns the count; saved in place.

        UsdSkel carries the method as ``skinningMethod`` on ``UsdSkelBindingAPI``
        and :meth:`skinning_methods` already reads it on the way IN -- but
        Blender's exporter authors it for nothing, so a skin that reached Blender
        as dual-quaternion (Preserve Volume being Blender's own DQS) went back out
        as the default ``classicLinear``. Measured on a production module: 7 of 7
        skins, costing 4.03 mm of SHAPE against 0.22 mm of placement -- worst
        where the rig bends and vanishing where it is straight, which is what
        distinguishes a skinning-algorithm loss from a transform error.

        Writing the STANDARD attribute rather than a sidecar means one spelling
        serves both directions and every other UsdSkel consumer; it is also what
        mayaUsd itself writes for a dual-quaternion skinCluster, so a Maya scene
        that makes the round trip comes home spelled the way it left.
        """
        import os

        if os.path.splitext(str(filepath))[1].lower() == ".usdz":
            return 0
        import bpy
        from pxr import Sdf, Usd, UsdSkel

        layer = Sdf.Layer.FindOrOpen(str(filepath))
        if layer is None:
            raise FileNotFoundError(f"USD layer not found: {filepath}")
        stage = Usd.Stage.Open(layer)
        count = 0
        for obj in bpy.data.objects if objects is None else objects:
            if getattr(obj, "type", "") != "MESH":
                continue
            armatures = [
                m
                for m in (getattr(obj, "modifiers", None) or [])
                if getattr(m, "type", "") == "ARMATURE"
            ]
            if not armatures:
                continue
            prim = stage.GetPrimAtPath(UsdUtils.export_prim_path(obj, root_prim_path))
            if not prim or not prim.IsValid():
                continue
            # Stated either way: an explicit `classicLinear` is the author saying
            # so, which a reader can trust; an absent attribute is only a default.
            method = (
                "dualQuaternion"
                if any(m.use_deform_preserve_volume for m in armatures)
                else "classicLinear"
            )
            UsdSkel.BindingAPI.Apply(prim)
            UsdSkel.BindingAPI(prim).CreateSkinningMethodAttr().Set(method)
            count += 1
        if count:
            layer.Save()
        return count

    @staticmethod
    def mark_container_skeletons(filepath: str) -> int:
        """Mark each Skeleton prim that is only a CONTAINER for its bones with
        mayaUsd's ``customData Maya:generated``, so Maya makes no joint of it.
        Returns the count; the layer is saved in place (a ``.usdz`` returns 0).

        Blender writes an armature's DATA as a Skeleton prim nested under the
        object's Xform, and mayaUsd imports every Skeleton prim as a joint of its
        own unless that prim says it was generated -- which its own exporter
        stamps on every skeleton it writes, so a Maya scene's round trip comes
        home clean while a Blender one gains a joint per armature. That joint
        sits between the armature's transform and its root joint, at the
        armature's origin, and wherever the data is named like its root bone
        (always, for a Maya joint pulled to Blender and back) a bone's path
        names IT: the rig transfer built and verified against it, 2.4-2.6 m from
        the real joint on a production module. The joint itself was exact.

        Only a pure container is marked. mayaUsd hangs a marked skeleton's joints
        under its PARENT's node and reads nothing off the prim itself, so one
        carrying a transform of its own -- a static export merges a leaf
        armature's object into its Skeleton -- or one at the root arrives with
        no joints at all (both measured, mayaUsd 0.30). A prim that already says
        either way is the author's word and is left alone.
        """
        import os

        if os.path.splitext(str(filepath))[1].lower() == ".usdz":
            return 0
        from pxr import Sdf, Usd, UsdGeom, UsdSkel

        layer = Sdf.Layer.FindOrOpen(str(filepath))
        if layer is None:
            raise FileNotFoundError("USD layer not found: " + str(filepath))
        stage = Usd.Stage.Open(layer)
        count = 0
        for prim in stage.Traverse():
            if not prim.IsA(UsdSkel.Skeleton) or prim.HasCustomDataKey(
                "Maya:generated"
            ):
                continue
            if prim.GetParent().IsPseudoRoot():
                continue  # no parent node to hold the joints
            xformable = UsdGeom.Xformable(prim)
            if xformable.GetOrderedXformOps() or xformable.GetResetXformStack():
                continue  # the object's own transform lives here
            prim.SetCustomDataByKey("Maya:generated", True)
            count += 1
        if count:
            layer.Save()
        return count

    @staticmethod
    def mark_invisible(
        filepath: str, objects: List[Any], root_prim_path: str = ""
    ) -> int:
        """Stamp ``visibility = invisible`` on the prims *objects* exported to
        (matched through :meth:`export_prim_path`); return the count. The layer
        is saved in place. An object whose prim is not in the layer is skipped."""
        from pxr import Sdf, Usd, UsdGeom

        layer = Sdf.Layer.FindOrOpen(filepath)
        if layer is None:
            raise FileNotFoundError(f"USD layer not found: {filepath}")
        stage = Usd.Stage.Open(layer)
        count = 0
        for obj in objects:
            prim = stage.GetPrimAtPath(UsdUtils.export_prim_path(obj, root_prim_path))
            if not prim or not prim.IsValid():
                continue
            UsdGeom.Imageable(prim).CreateVisibilityAttr().Set(UsdGeom.Tokens.invisible)
            count += 1
        if count:
            layer.Save()
        return count

    @staticmethod
    def apply_visibility(filepath: str, objects: List[Any]) -> int:
        """Reproduce the layer's visibility on *objects* -- ``hide_viewport`` +
        ``hide_render``, KEYED where the layer animates it. Returns the number of
        objects touched.

        Blender's USD importer reads visibility for nothing: probed on 5.1, a
        statically invisible prim and an animated one both arrive fully visible
        with no fcurves, whatever ``import_visible_only`` says. Visibility is
        COMPUTED per prim rather than read off it, because USD inherits it down
        the subtree while Blender does not hide a child with its parent -- so
        every descendant of an invisible group needs its own answer, as in Maya.

        An animated prim is keyed at the authored sample times of its own
        visibility and of every ancestor's (an ancestor's switch is the child's
        too), CONSTANT, one key per actual CHANGE. Measured on a production pull:
        8 animated prims whose subtrees otherwise stayed on screen for all 4741
        frames -- among them a RenderOpacity fade, which is authored as the GAP
        between two opposite visibility keys and so travels as visibility or not
        at all."""
        from pxr import Usd, UsdGeom

        stage = Usd.Stage.Open(filepath)
        if stage is None:
            raise FileNotFoundError(f"USD layer not found: {filepath}")
        invisible = set()
        tracks: Dict[str, List[Tuple[float, bool]]] = {}
        for prim in stage.Traverse():
            img = UsdGeom.Imageable(prim)
            if not img:
                continue
            path = str(prim.GetPath())
            times = UsdUtils._visibility_sample_times(prim)
            track = [
                (t, img.ComputeVisibility(t) == UsdGeom.Tokens.invisible) for t in times
            ]
            states = {hidden for _, hidden in track}
            if len(states) > 1:
                tracks[path] = track
                continue
            # Static: either nothing on the chain animates, or something does and
            # this prim's computed value never switches anyway (an ancestor hidden
            # throughout). A flag, not an action on every object of the subtree.
            hidden = (
                states.pop()
                if states
                else img.ComputeVisibility() == UsdGeom.Tokens.invisible
            )
            if hidden:
                invisible.add(path)
        count = 0
        keyed = []
        for obj in objects:
            path = UsdUtils.prim_path(obj)
            if path in invisible:
                obj.hide_viewport = True
                obj.hide_render = True
                count += 1
            elif UsdUtils._key_visibility(obj, tracks.get(path) or ()):
                keyed.append(obj)
                count += 1
        if keyed:
            from blendertk.anim_utils._anim_utils import AnimUtils

            AnimUtils.step_visibility_keys(keyed)
        return count

    @staticmethod
    def _visibility_sample_times(prim) -> List[float]:
        """Sorted union of the authored ``visibility`` sample times on *prim* and on
        every ancestor; empty when nothing on that chain is time-varying. The CHAIN
        decides, not the prim: USD inherits visibility, so an ancestor's switch is a
        change in this prim's computed value even when its own attribute is static."""
        from pxr import UsdGeom

        times = set()
        node = prim
        while node and node.IsValid():
            img = UsdGeom.Imageable(node)
            attr = img.GetVisibilityAttr() if img else None
            if attr and attr.ValueMightBeTimeVarying():
                times.update(attr.GetTimeSamples())
            node = node.GetParent()
        return sorted(times)

    @staticmethod
    def _key_visibility(obj, track) -> bool:
        """Key *obj*'s ``hide_viewport`` / ``hide_render`` from a ``[(time, hidden)]``
        track, one key per CHANGE -- a sample repeating the current state is not a
        key. False when nothing was keyed (an empty track). The caller decides which
        prims get here: a track whose value never switches is static, not animation."""
        previous = None
        for time, hidden in track:
            if hidden == previous:
                continue
            obj.hide_viewport = hidden
            obj.hide_render = hidden
            obj.keyframe_insert("hide_viewport", frame=time)
            obj.keyframe_insert("hide_render", frame=time)
            previous = hidden
        return previous is not None

    @staticmethod
    def activate_uv_map(objects: List[Any], name: str = "map1") -> int:
        """Make the UV map *name* the active and render UV map on every mesh in
        *objects* that has one; return the count. USD stores primvars
        alphabetically, so the importer's "first" UV map is whichever sorts
        first (``lightmap`` before ``map1``); Maya's primary set travels by
        NAME (``map1``, kept by ``preserveUVSetNames``) and every texture node
        without an explicit UV Map input samples the render-active map."""
        count = 0
        for obj in objects:
            layers = getattr(getattr(obj, "data", None), "uv_layers", None)
            if not layers:
                continue
            uv = layers.get(name)
            if uv is None:
                continue
            layers.active = uv
            uv.active_render = True
            count += 1
        return count

    @staticmethod
    def import_scene(filepath: str, **usd_opts) -> List[Any]:
        """Import a hand-off USD layer the way the bridges do: EVERY prim
        (:attr:`INTERCHANGE_IMPORT_OPTIONS`, *usd_opts* on top), the invisible
        ones landing HIDDEN (:meth:`apply_visibility`) and Maya's primary UV set
        render-active (:meth:`activate_uv_map`). The mirror of mayatk's
        ``UsdUtils.import_scene``; returns the objects created."""
        opts = dict(UsdUtils.INTERCHANGE_IMPORT_OPTIONS)
        opts.update(usd_opts)
        imported = UsdUtils.import_usd(filepath, **opts)
        if not opts.get("import_visible_only"):
            UsdUtils.apply_visibility(filepath, imported)
        UsdUtils.activate_uv_map(imported)
        return imported

    @staticmethod
    def import_usd(filepath, **usd_opts):
        """Import a USD file (wrapper over ``bpy.ops.wm.usd_import``).

        Args:
            filepath: the USD layer/package to import (``$VARS`` expanded).
                Raises ``FileNotFoundError`` if absent.
            **usd_opts: forwarded to ``bpy.ops.wm.usd_import`` (unknown names
                dropped with a log line).

        Returns:
            list: the objects created by the import (those newly added to
            ``bpy.data.objects``).
        """
        import bpy

        filepath = os.path.abspath(os.path.expandvars(filepath))
        if not os.path.isfile(filepath):
            raise FileNotFoundError(f"USD file not found: {filepath}")
        opts = _UsdUtilsInternal._filter_op_options(bpy.ops.wm.usd_import, usd_opts)
        before = set(bpy.data.objects)
        with CoreUtils.window_context_override():
            bpy.ops.wm.usd_import(filepath=filepath, **opts)
        return [o for o in bpy.data.objects if o not in before]

    @staticmethod
    def bake_transform_caches(
        objects: Optional[List[Any]] = None,
        frame_range: Optional[Tuple[float, float]] = None,
        clean: bool = True,
    ) -> int:
        """Bake every ``TRANSFORM_CACHE`` constraint on *objects* (default: all) into
        real keyframes and drop the constraint + its orphaned cache file. Returns
        the number of objects baked. Meant for freshly USD-imported objects: the
        bake clears EVERY constraint on an object it bakes (the Bake Action
        engine's clear), and the importer only ever adds the cache one.

        Blender's USD importer does not import animated transforms as keys: each
        animated prim gets a Transform Cache constraint that streams from the USD
        file **on disk, by path** — so a scene imported from a temp intermediate
        (the bridges' cached conversions, swept by age) loses its animation the
        moment that file goes, and a linked/opened bake carries a dependency on a
        path nobody else has. Keys are owned data; a cache path is not.

        *frame_range* defaults to the scene range (the importer's ``set_frame_range``
        has just set it to the stage's authored range). The bake is the Bake Action
        operator's own engine (``bpy_extras.anim_utils.bake_action_objects``) with
        visual keying, called directly so it needs no window/selection context
        (headless bakes run it too).

        *clean* removes what the per-frame bake writes redundantly: a curve that never
        changes goes (its value moves onto the property), and so do the interior keys
        of a hold, through :meth:`blendertk.AnimUtils.optimize_keys` at ``1e-4`` (the
        operator's own threshold). It is NOT the operator's ``do_clean``, which deletes
        keys one at a time from the front of each curve -- quadratic in the curve's
        length. Measured on a production pull (205 animated prims over 4742 frames,
        8.75 M keys): the bake took 70 s and that clean was still running minutes after
        the conversion's 600 s budget expired, which is what failed the pull. Pass
        ``clean=False`` when a key reduction follows anyway.
        """
        import bpy
        from bpy_extras import anim_utils

        objects = list(objects) if objects is not None else list(bpy.data.objects)
        cached = [
            o
            for o in objects
            if any(c.type == "TRANSFORM_CACHE" for c in o.constraints)
        ]
        if not cached:
            return 0
        scene = bpy.context.scene
        start, end = frame_range or (scene.frame_start, scene.frame_end)
        previous_frame = scene.frame_current
        options = anim_utils.BakeOptions(
            only_selected=False,
            do_pose=False,
            do_object=True,
            do_visual_keying=True,
            do_constraint_clear=True,
            do_parents_clear=False,
            do_clean=False,
            do_location=True,
            do_rotation=True,
            do_scale=True,
            do_bbone=False,
            do_custom_props=False,
        )
        # Visible for the duration, or the bake reads a stale matrix: a hidden
        # object is not evaluated, constraints included (see
        # :meth:`_mute_visibility_curves`). The payload's invisible prims arrive
        # hidden and its animated ones keyed (:meth:`apply_visibility`), so on a
        # pull this is the common case, not the corner.
        revealed = _UsdUtilsInternal._reveal(cached)
        muted = _UsdUtilsInternal._mute_visibility_curves(cached)
        try:
            # Bake INTO the action each object already has: with ``None`` the engine
            # creates a new one and ASSIGNS it, discarding whatever was there
            # (measured) -- and that visibility is exactly what would be lost.
            anim_utils.bake_action_objects(
                [
                    (o, getattr(getattr(o, "animation_data", None), "action", None))
                    for o in cached
                ],
                frames=range(int(start), int(end) + 1),
                bake_options=options,
            )
        finally:
            for fc in muted:
                fc.mute = False
            _UsdUtilsInternal._restore_hidden(revealed)
        scene.frame_set(previous_frame)
        if clean:
            from blendertk.anim_utils._anim_utils import AnimUtils

            AnimUtils.optimize_keys(
                cached,
                value_tolerance=1e-4,
                remove_static_curves=True,
                remove_flat_keys=True,
            )
        # ``bpy.data.cache_files`` has no ``remove`` — the generic batch remover is
        # the API for this collection.
        orphans = [c for c in bpy.data.cache_files if c.users == 0]
        if orphans:
            bpy.data.batch_remove(ids=orphans)
        return len(cached)

    @staticmethod
    def honor_reset_xform_stack(
        usd_path: str, objects: Optional[List[Any]] = None
    ) -> int:
        """Stop imported objects whose prim declares ``!resetXformStack!`` from
        inheriting their ancestors' transforms. Returns the number re-rooted.

        ``!resetXformStack!`` is the USD opinion "my local transform IS my world
        transform -- ignore every ancestor". Maya's exporter writes it for a node
        driven by ``offsetParentMatrix`` (a matrix-constraint rig), whose world
        placement is authored independently of whatever DAG parent it sits under.
        **Blender's USD importer ignores the token** (measured, 5.1): it parents the
        object normally, so an ANIMATED ancestor drags it away from where the prim
        says it is -- silently, since nothing errors and the scene looks plausible.

        Measured on a production pull (14 declaring prims: 7
        dual-quaternion skinned wire looms, which the exporter could not write as
        UsdSkel and so baked to time-sampled points, plus their 7 rig IK curves).
        The looms arrived 1.0-1.4 m from where Maya has them and collapsed onto
        roughly one spot, because they share the ancestor chain that displaced them.
        With this repair each matches a fresh-mayapy reading of the source scene to
        within 0.001 m.

        The object keeps its own local transform, its parent is CLEARED, and its
        world is set to ``conversion x local`` -- the stage's own
        ``ComputeLocalToWorldTransform`` for a reset-stack prim, unit scale and
        up-axis included. The conversion is taken from a top-level imported object
        rather than reimplementing the importer's convention (metres-per-unit and
        up-axis), which is Blender's to change: the importer applies it to the ROOT
        object's transform, so that root's world is the conversion times its own
        prim ops, and dividing those ops back out leaves the conversion alone. The
        ops come from the stage, because a top-level object's ``matrix_basis`` IS
        its world (no parent, identity parent-inverse) and so cannot supply them --
        measured the hard way: dividing by ``matrix_basis`` yields the identity
        every time, which left a production pull sitting at its raw centimetre,
        Y-up coordinates, 190 m out. It is frame-independent because both factors
        move together when the root itself is animated.

        Parenting is cleared rather than re-pointed at the root because no STATIC
        parent-inverse can cancel an ANIMATED ancestor -- exactly the case this
        repair exists for. (Re-pointing looks right whenever the top of the chain
        happens to be static, and silently does nothing when it is not.) The object
        keeps its collection, so it stays where the outliner filters find it.

        Call it BEFORE any visual-keying bake (:meth:`bake_transform_caches`), which
        samples world space and would otherwise bake the displacement into keys.

        A mesh the importer bound to a skeleton (an Armature modifier) is left alone
        even when its prim declares the token: UsdSkel places skinned points through
        the skeleton, and Blender's Armature modifier deforms RELATIVE to its
        armature object, so such a mesh must keep riding the parent its armature
        rides. Freed, it lands the parent's motion away from its bones -- measured
        0.55-1.27 m on a fixture whose module moves 1 m. (The production looms above
        were point caches at the time; the Maya-side conversion now hands the
        exporter one root joint per skin, so they arrive skeleton-bound.)
        """
        import bpy
        from mathutils import Matrix
        from pxr import Usd, UsdGeom

        stage = Usd.Stage.Open(str(usd_path))
        if stage is None:
            return 0

        def _root_ops(root_obj):
            """*root_obj*'s OWN prim transform, as a Blender matrix (identity when it
            has none -- the common case, a transform-less asset root)."""
            prim = stage.GetPrimAtPath(UsdUtils.prim_path(root_obj))
            xform = UsdGeom.Xformable(prim) if prim else None
            if not xform:
                return Matrix.Identity(4)
            gf = xform.GetLocalTransformation(
                Usd.TimeCode(float(bpy.context.scene.frame_current))
            )
            # Gf is row-vector/row-major, mathutils column-vector: transpose.
            return Matrix([[gf[col][row] for col in range(4)] for row in range(4)])

        reset = set()
        for prim in stage.Traverse():
            attr = prim.GetAttribute("xformOpOrder")
            order = attr.Get() if attr else None
            if order and any("!resetXformStack!" in str(token) for token in order):
                reset.add(str(prim.GetPath()))
        if not reset:
            return 0

        objects = list(objects) if objects is not None else list(bpy.data.objects)
        # A top-level object inherits nothing, so the token is already honored
        # there; a skeleton-bound mesh is placed by its armature (docstring).
        targets = [
            obj
            for obj in objects
            if obj.parent is not None
            and UsdUtils.prim_path(obj) in reset
            and not any(
                getattr(mod, "type", "") == "ARMATURE"
                for mod in getattr(obj, "modifiers", ())
            )
        ]
        if not targets:
            return 0
        # The step before this one removes objects (the materials scope), which leaves
        # the depsgraph stale; matrix_world below must read the CURRENT evaluation.
        bpy.context.view_layer.update()
        # Resolve every target's final world BEFORE detaching any of them. Both halves
        # of this read the hierarchy -- ``prim_path`` walks the parent chain, and the
        # conversion is read off the top of it -- so freeing an ancestor that ALSO
        # declares the token would truncate its descendant's prim path (silently
        # leaving it displaced) and resolve the conversion against a detached root.
        plans = []
        for obj in targets:
            top = obj.parent
            while top.parent is not None:
                top = top.parent
            conversion = top.matrix_world @ _root_ops(top).inverted_safe()
            plans.append((obj, conversion @ obj.matrix_basis))
        for obj, world in plans:
            obj.parent = None
            obj.matrix_world = world
        return len(plans)

    @staticmethod
    def skinning_methods(usd_path: str) -> Dict[str, str]:
        """``{prim path: skinning method}`` for every prim of the stage that authors
        one -- ``"dualQuaternion"`` or ``"classicLinear"`` (``UsdSkelBindingAPI``'s
        ``skinningMethod``, USD 23.11+; mayaUsd 0.30 writes it for a dual-quaternion
        skinCluster). Blender's importer binds the skin but never reads the method,
        so a dual-quaternion skin would deform linearly; the Maya hand-off maps it
        onto the Armature modifier's Preserve Volume -- Blender's own dual-quaternion
        skinning (``MayaSceneImport._apply_skinning_methods``). ``{}`` when the
        stage cannot be opened.
        """
        from pxr import Usd, UsdSkel

        stage = Usd.Stage.Open(str(usd_path))
        if stage is None:
            return {}
        out: Dict[str, str] = {}
        for prim in stage.Traverse():
            attr = UsdSkel.BindingAPI(prim).GetSkinningMethodAttr()
            if attr and attr.HasAuthoredValue():
                out[str(prim.GetPath())] = str(attr.Get())
        return out

    @staticmethod
    def scene_settings(filepath: str) -> Dict[str, Any]:
        """The time setup a USD stage itself carries, as a (partial) ``scene`` record
        (see ``EnvUtils.SCENE_SETTINGS_KEYS``): ``fps`` from ``timeCodesPerSecond``
        and the animation range from ``startTimeCode`` / ``endTimeCode``. The
        fallback for a source with no conversion manifest — Blender's importer
        applies the range (``set_frame_range``) but never the fps. Reads through the
        ``pxr`` bindings Blender bundles; keys the stage doesn't author are omitted
        and ``{}`` is returned when the stage can't be opened.
        """
        try:
            from pxr import Usd

            stage = Usd.Stage.Open(os.path.abspath(os.path.expandvars(filepath)))
        except Exception:  # noqa: BLE001 — a record, never a failed import
            return {}
        if stage is None:
            return {}
        out = {}
        if stage.HasAuthoredMetadata("timeCodesPerSecond"):
            fps = float(stage.GetTimeCodesPerSecond())
            if fps > 0:
                out["fps"] = fps
        if stage.HasAuthoredTimeCodeRange():
            start, end = stage.GetStartTimeCode(), stage.GetEndTimeCode()
            out["anim_start"] = int(round(start))
            out["anim_end"] = int(round(end))
        return out

    @staticmethod
    def export_selection_usd(filepath=None, objects=None, **usd_opts):
        """Export the selection (or *objects*) to a USD file for an external-app hand-off.

        Thin selection-only alias for :meth:`UsdUtils.export` (the USD counterpart of
        :meth:`FbxUtils.export_selection_fbx`). Reached as ``btk.UsdUtils.export_selection_usd``
        — the module is scoped class-only (a generic ``export`` would collide flat with
        ``FbxUtils.export`` under a wildcard scan).
        """
        return UsdUtils.export(
            filepath=filepath, objects=objects, selection_only=True, **usd_opts
        )
