# !/usr/bin/python
# coding=utf-8
"""Hierarchy Sync core engine — mirror of mayatk's ``env_utils.hierarchy_sync._hierarchy_sync``.

Diffs the current scene's object hierarchy against a reference object set (in practice, a
``.blend`` linked in as a library — Blender's closest analogue of Maya's namespace-sandboxed
reference import; an ``.fbx`` reference is first converted to a throwaway ``.blend`` by
:func:`stage_reference_blend` so the pipeline stays format-agnostic; see
``hierarchy_sync_slots.HierarchySyncController`` for how the library is staged/torn down),
then repairs drift: create stub Empties for missing nodes, quarantine extras, fix reparented /
fuzzy-renamed nodes.

Divergence from mayatk (by design, not a gap to fill in later — see the port's scoping notes in
``hierarchy_sync_slots.py``):
    * No namespace stripping. Blender has no namespaces; a linked library's objects keep their
      source-file names untouched (that IS the "clean" name), so paths need no cleaning pass —
      unlike Maya's DAG, where two same-named nodes under different parents are common and only
      a namespace / full-path disambiguates them.
    * No default-camera filtering. Blender ships no fixed set of unremovable default cameras.
    * No locator-group atomic-move promotion. Maya's "GRP > LOC > children" rigging idiom (an
      object parented under a locator that must move as one unit) has no Blender counterpart.
    * No ``lockNode`` stub protection. Blender has no lock-from-delete primitive to mirror; stubs
      are tagged with a custom property + viewport Object Color only (informational, not
      enforced).
    * No anim-layer-aware curve transfer. Pull (``ObjectSwapper``, below) APPENDS matched objects
      fresh from the reference ``.blend``, and native Append copies each object's Action wholesale
      — there is no per-curve transfer step for anim layers to complicate.
"""
import os
import tempfile
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pythontk as ptk

from blendertk.core_utils._core_utils import strip_dup_suffix
from blendertk.display_utils.color_id import ColorId
from blendertk.node_utils._node_utils import reparent as _reparent

#: Worker run in a fresh headless Blender to convert an FBX reference into a standalone ``.blend``
#: (see :func:`stage_reference_blend`). Committed beside this module and run **by path** as a
#: subprocess — not imported here (but it is import-safe; slot discovery walks the package).
_FBX_STAGE_WORKER = os.path.join(os.path.dirname(__file__), "_fbx_stage_worker.py")


def stage_reference_blend(reference_path: str, logger=None):
    """Return a ``.blend`` path to link as the reference, converting other scene formats.

    A ``.blend`` reference is staged directly. Any other supported scene format (currently
    ``.fbx``) is converted to a throwaway temp ``.blend`` **in a fresh headless Blender**
    (:data:`_FBX_STAGE_WORKER` via :class:`~blendertk.env_utils.blender_connection.BlenderConnection`)
    so the whole linked-library Diff/Pull pipeline stays format-agnostic (an FBX reference becomes
    indistinguishable from a ``.blend`` one). mayatk imports FBX straight into an isolating Maya
    namespace; Blender object names are global to a ``.blend``, so importing the FBX into the
    user's live scene would suffix every colliding object ``.001`` (the normal case for a Hierarchy
    Sync reference, which shares names with the current scene) and bake that suffix into the staged
    file — breaking the diff. Converting in a brand-new empty Blender guarantees clean names and
    never mutates the user's scene.

    Args:
        reference_path: the reference ``.blend`` or ``.fbx``.
        logger: optional ``ptk`` logger for progress/error reporting.

    Returns:
        ``(blend_path, temp_blend_or_None)`` — the temp file (if any) is the caller's to delete on
        teardown. ``(None, None)`` on failure or an unsupported format.
    """
    def _log(level, msg):
        if logger is not None:
            getattr(logger, level)(msg)

    ext = os.path.splitext(reference_path)[1].lower()
    if ext == ".blend":
        return reference_path, None
    if ext != ".fbx":
        _log("error", f"Unsupported reference format '{ext}'. Use a .blend or .fbx file.")
        return None, None

    import bpy
    from blendertk.env_utils.blender_connection import BlenderConnection

    _log("progress", f"Converting reference {ext} for staging: {os.path.basename(reference_path)}")

    fd, temp_blend = tempfile.mkstemp(prefix="hier_ref_", suffix=".blend")
    os.close(fd)

    def _fail(msg):
        _log("error", msg)
        try:
            os.remove(temp_blend)
        except OSError:
            pass
        return None, None

    try:
        result = BlenderConnection(blender_exe=bpy.app.binary_path).run_script(
            _FBX_STAGE_WORKER,
            script_args=[os.path.abspath(reference_path), temp_blend],
            timeout=300,
        )
    except Exception as e:  # noqa: BLE001 — launch/timeout failures reported cleanly, never raised
        return _fail(f"Failed to stage reference FBX (could not launch Blender): {e}")

    if result.returncode != 0 or not (os.path.isfile(temp_blend) and os.path.getsize(temp_blend)):
        tail = (result.stdout or "").strip().splitlines()[-3:]
        return _fail(
            f"Reference FBX staging failed (rc={result.returncode}). " + " | ".join(tail)
        )

    return temp_blend, temp_blend


# ---------------------------------------------------------------------------
# Path building — the Blender analogue of mayatk's DAG full-path / clean-path split.
# ---------------------------------------------------------------------------


def build_path(obj) -> str:
    """Pipe-joined hierarchy path from the root down to ``obj`` (e.g. ``"GRP|Child|Leaf"``).

    Built from live ``.name`` values — Blender enforces globally-unique local names and a linked
    library's objects keep their untouched source-file names, so (unlike Maya, where a namespace
    prefix must be stripped for comparison) the path IS already the clean comparison key.
    """
    parts = [obj.name]
    parent = obj.parent
    while parent is not None:
        parts.append(parent.name)
        parent = parent.parent
    return "|".join(reversed(parts))


def delete_objects(objects) -> List[str]:
    """Delete *objects* AND all their descendants from the blend data; return the deleted names.

    Maya parity: ``cmds.delete`` removes the whole subtree, but a bare
    ``bpy.data.objects.remove()`` re-roots the children instead — they stay in the scene and
    their world transform shifts once the parent's transform vanishes. Descendants are expanded
    up front (dedup by pointer, so an overlapping selection never double-deletes).
    """
    import bpy

    doomed = {}
    for obj in objects:
        for cand in (obj, *obj.children_recursive):
            doomed[cand.as_pointer()] = cand
    names = [o.name for o in doomed.values()]
    for o in doomed.values():
        bpy.data.objects.remove(o, do_unlink=True)
    return names


def should_keep_node_by_type(obj, node_types: List[str], exclude: bool = True) -> bool:
    """Filter by Blender object type — mirror of mayatk's shape-type filter."""
    matched = obj.type in node_types
    return not matched if exclude else matched


class HierarchyMapBuilder:
    """Builds hierarchy path maps for Blender objects (mirror of mayatk's ``HierarchyMapBuilder``).

    Unlike Maya (where a root-based ``listRelatives`` traversal and an arbitrary-node-list build
    are two different code paths), every Blender object already carries its own ``.parent``
    reference, so one method covers both: the path is derived directly from each object, with no
    traversal needed.
    """

    @staticmethod
    def build_path_map(objects) -> Dict[str, Any]:
        """Map every object in ``objects`` to its hierarchy path (see :func:`build_path`)."""
        return {build_path(obj): obj for obj in objects}


class HierarchySync(ptk.LoggingMixin):
    """Core hierarchy analysis and repair manager (mirror of mayatk's ``HierarchySync``)."""

    #: Custom property flagging a stub Empty (informational discovery only — no enforcement).
    STUB_ATTR = "hierarchy_sync_stub"
    STUB_NOTE = (
        "Placeholder created by Hierarchy Sync. This empty object preserves the "
        "reference hierarchy structure."
    )
    #: Viewport Object Color for stub Empties (muted teal — matches mayatk's outliner color).
    STUB_COLOR = (0.42, 0.55, 0.62)

    def __init__(self, fuzzy_matching: bool = True, dry_run: bool = True):
        super().__init__()
        self.dry_run = dry_run
        self.fuzzy_matching = fuzzy_matching

        self.current_scene_path_map: Dict[str, Any] = {}
        self.reference_scene_path_map: Dict[str, Any] = {}
        self.differences: Dict[str, Any] = {}
        self.missing_objects: List[str] = []
        self.extra_objects: List[str] = []

    # ------------------------------------------------------------------ #
    # Diff
    # ------------------------------------------------------------------ #

    def analyze_hierarchies(
        self,
        current_objects,
        reference_objects,
        filter_meshes: bool = True,
        filter_cameras: bool = False,
        filter_lights: bool = False,
    ) -> Dict[str, Any]:
        """Analyze differences between the current scene and a reference object set.

        Args:
            current_objects: Objects making up the "current" side (usually every object in the
                scene, or a selected subtree).
            reference_objects: Objects making up the reference side — in practice, every object
                belonging to a linked library.
        """
        try:
            self.current_scene_path_map = HierarchyMapBuilder.build_path_map(current_objects)
            self.logger.progress(
                f"Built current scene path map: {len(self.current_scene_path_map)} paths"
            )

            self.reference_scene_path_map = HierarchyMapBuilder.build_path_map(reference_objects)
            self.logger.progress(
                f"Built reference path map: {len(self.reference_scene_path_map)} paths"
            )

            exclude_types: List[str] = []
            if filter_meshes:
                exclude_types.append("MESH")
            if filter_cameras:
                exclude_types.append("CAMERA")
            if filter_lights:
                exclude_types.append("LIGHT")

            if exclude_types:
                for attr in ("current_scene_path_map", "reference_scene_path_map"):
                    pmap = getattr(self, attr)
                    filtered = {
                        path: obj
                        for path, obj in pmap.items()
                        if should_keep_node_by_type(obj, exclude_types, exclude=True)
                    }
                    setattr(self, attr, filtered)

            current_paths = set(self.current_scene_path_map)
            reference_paths = set(self.reference_scene_path_map)

            self.missing_objects = sorted(reference_paths - current_paths)
            self.extra_objects = sorted(current_paths - reference_paths)

            self.log_table(
                data=[
                    ["Current paths", str(len(current_paths))],
                    ["Reference paths", str(len(reference_paths))],
                    ["Missing", str(len(self.missing_objects))],
                    ["Extra", str(len(self.extra_objects))],
                ],
                headers=["Metric", "Count"],
                title="PATH COMPARISON",
            )

            remaining_missing = list(self.missing_objects)
            remaining_extra = list(self.extra_objects)

            reparented, remaining_missing, remaining_extra = self._detect_reparented(
                remaining_missing, remaining_extra
            )
            fuzzy_matches, remaining_missing, remaining_extra = self._detect_fuzzy_renames(
                remaining_missing, remaining_extra
            )
            suffix_matches, remaining_missing, remaining_extra = self._detect_suffix_flattening(
                remaining_missing, remaining_extra
            )
            fuzzy_matches.extend(suffix_matches)

            self.missing_objects = remaining_missing
            self.extra_objects = remaining_extra

            self.differences = {
                "missing": self.missing_objects,
                "extra": self.extra_objects,
                "reparented": reparented,
                "fuzzy_matches": fuzzy_matches,
                "total_missing": len(self.missing_objects),
                "total_extra": len(self.extra_objects),
                "total_reparented": len(reparented),
                "total_fuzzy": len(fuzzy_matches),
            }

            self.log_table(
                data=[
                    ["Truly missing", str(len(self.missing_objects))],
                    ["Truly extra", str(len(self.extra_objects))],
                    ["Reparented", str(len(reparented))],
                    ["Fuzzy renamed", str(len(fuzzy_matches))],
                ],
                headers=["Category", "Count"],
                title="DIFF CATEGORIES",
            )

            return self.differences

        except Exception as e:
            self.logger.error(f"Failed to analyze hierarchies: {e}")
            return {}

    # ------------------------------------------------------------------ #
    # Detection passes (called by analyze_hierarchies) — pure path-string logic,
    # ported verbatim from mayatk (no cmds/bpy dependency in the original either).
    # ------------------------------------------------------------------ #

    def _detect_reparented(
        self, remaining_missing: List[str], remaining_extra: List[str]
    ) -> Tuple[List[Dict], List[str], List[str]]:
        """Detect items that exist in both pools under different parents."""
        reparented: List[Dict] = []
        try:
            missing_by_leaf: Dict[str, List[str]] = {}
            for p in remaining_missing:
                missing_by_leaf.setdefault(p.rsplit("|", 1)[-1], []).append(p)

            extra_by_leaf: Dict[str, List[str]] = {}
            for p in remaining_extra:
                extra_by_leaf.setdefault(p.rsplit("|", 1)[-1], []).append(p)

            matched_missing: set = set()
            matched_extra: set = set()
            for leaf, m_paths in missing_by_leaf.items():
                e_paths = extra_by_leaf.get(leaf, [])
                if len(m_paths) == 1 and len(e_paths) == 1:
                    reparented.append(
                        {
                            "leaf": leaf,
                            "reference_path": m_paths[0],
                            "current_path": e_paths[0],
                        }
                    )
                    matched_missing.add(m_paths[0])
                    matched_extra.add(e_paths[0])

            remaining_missing = [p for p in remaining_missing if p not in matched_missing]
            remaining_extra = [p for p in remaining_extra if p not in matched_extra]

            if reparented:
                self.logger.debug(
                    f"Detected {len(reparented)} reparented items "
                    f"(e.g. {reparented[0]['leaf']}: "
                    f"{reparented[0]['reference_path']} → {reparented[0]['current_path']})"
                )
        except Exception as e:
            self.logger.debug(f"Reparented detection failed: {e}")

        return reparented, remaining_missing, remaining_extra

    def _detect_fuzzy_renames(
        self, remaining_missing: List[str], remaining_extra: List[str]
    ) -> Tuple[List[Dict], List[str], List[str]]:
        """Detect items that were renamed (fuzzy leaf-name matching)."""
        fuzzy_matches: List[Dict] = []
        try:
            if not (remaining_missing and remaining_extra and self.fuzzy_matching):
                return fuzzy_matches, remaining_missing, remaining_extra

            missing_leaves = [p.rsplit("|", 1)[-1] for p in remaining_missing]
            extra_leaves = [p.rsplit("|", 1)[-1] for p in remaining_extra]

            raw_matches = ptk.FuzzyMatcher.find_all_matches(
                missing_leaves, extra_leaves, score_threshold=0.7
            )

            matched_fm_missing: set = set()
            matched_fm_extra: set = set()
            for query_leaf, (best_leaf, score) in raw_matches.items():
                if query_leaf == best_leaf:
                    continue
                ref_path = next(
                    (p for p in remaining_missing if p.rsplit("|", 1)[-1] == query_leaf), None
                )
                cur_path = next(
                    (p for p in remaining_extra if p.rsplit("|", 1)[-1] == best_leaf), None
                )
                if (
                    ref_path
                    and cur_path
                    and ref_path not in matched_fm_missing
                    and cur_path not in matched_fm_extra
                ):
                    fuzzy_matches.append(
                        {"target_name": ref_path, "current_name": cur_path, "score": score}
                    )
                    matched_fm_missing.add(ref_path)
                    matched_fm_extra.add(cur_path)

            remaining_missing = [p for p in remaining_missing if p not in matched_fm_missing]
            remaining_extra = [p for p in remaining_extra if p not in matched_fm_extra]

            if fuzzy_matches:
                self.logger.debug(
                    f"Detected {len(fuzzy_matches)} fuzzy renamed matches "
                    f"(e.g. {fuzzy_matches[0]['target_name']} ↔ "
                    f"{fuzzy_matches[0]['current_name']} score={fuzzy_matches[0]['score']:.2f})"
                )
        except Exception as e:
            self.logger.debug(f"Fuzzy renamed detection failed: {e}")

        return fuzzy_matches, remaining_missing, remaining_extra

    def _detect_suffix_flattening(
        self, remaining_missing: List[str], remaining_extra: List[str]
    ) -> Tuple[List[Dict], List[str], List[str]]:
        """Detect FBX-style name-flattening where parent names are prepended to children.

        e.g. ``Booster_Off_6_Switch`` → ``Overhead_Console_Boosters_Booster_Off_6_Switch``.
        Matched pairs share the same parent path, and the shorter name is a ``_``-delimited
        suffix of the longer name.
        """
        suffix_matches: List[Dict] = []
        try:
            if not (remaining_missing and remaining_extra):
                return suffix_matches, remaining_missing, remaining_extra

            def _group_by_parent(paths):
                result: Dict[str, List[Tuple[str, str]]] = {}
                for p in paths:
                    if "|" in p:
                        parent, leaf = p.rsplit("|", 1)
                    else:
                        parent, leaf = "", p
                    result.setdefault(parent, []).append((leaf, p))
                return result

            missing_by_parent = _group_by_parent(remaining_missing)
            extra_by_parent = _group_by_parent(remaining_extra)

            matched_missing: set = set()
            matched_extra: set = set()

            for parent, m_items in missing_by_parent.items():
                e_items = extra_by_parent.get(parent)
                if not e_items:
                    continue
                for m_leaf, m_path in m_items:
                    if m_path in matched_missing:
                        continue
                    for e_leaf, e_path in e_items:
                        if e_path in matched_extra or m_leaf == e_leaf:
                            continue
                        longer, shorter = (
                            (m_leaf, e_leaf) if len(m_leaf) > len(e_leaf) else (e_leaf, m_leaf)
                        )
                        if (
                            longer.endswith(shorter)
                            and longer[len(longer) - len(shorter) - 1] == "_"
                        ):
                            suffix_matches.append(
                                {"target_name": m_path, "current_name": e_path, "score": 1.0}
                            )
                            matched_missing.add(m_path)
                            matched_extra.add(e_path)
                            break

            if matched_missing:
                remaining_missing = [p for p in remaining_missing if p not in matched_missing]
                remaining_extra = [p for p in remaining_extra if p not in matched_extra]
                self.logger.debug(
                    f"Detected {len(matched_missing)} name-flattening matches (suffix matching)"
                )
        except Exception as e:
            self.logger.debug(f"Suffix matching failed: {e}")

        return suffix_matches, remaining_missing, remaining_extra

    # ------------------------------------------------------------------ #
    # Repair (operate on results from analyze_hierarchies)
    # ------------------------------------------------------------------ #

    def _resolve_node(self, path: str, source: str = "current"):
        """Resolve a diff path to a live object, or ``None`` if not found / no longer valid."""
        path_map = self.current_scene_path_map if source == "current" else self.reference_scene_path_map
        obj = path_map.get(path)
        if obj is None:
            return None
        try:
            obj.name  # touch to detect a removed/invalidated reference
        except ReferenceError:
            return None
        return obj

    @staticmethod
    def _ensure_parent_chain(path: str):
        """Create any missing intermediate Empty objects for *path* and return the immediate
        parent object (or ``None`` for root-level items).

        *path* is a pipe-separated hierarchy path, e.g. ``"Grp_A|Grp_B|Leaf"``. Looks up each
        component as a direct child of the current parent (so duplicate leaf names at different
        hierarchy levels resolve correctly, mirroring mayatk's parent-relative lookup).
        """
        import bpy

        parts = path.split("|")
        if len(parts) <= 1:
            return None  # root-level, no parent needed

        current_parent = None
        for name in parts[:-1]:
            if current_parent is not None:
                existing = next(
                    (c for c in current_parent.children if c.name == name and c.library is None), None
                )
            else:
                # library is None: only match LOCAL objects — bpy.context.scene.objects also
                # includes the linked reference library's own objects (its collection is nested
                # inside the scene), which must never be treated as "already existing" current
                # content.
                existing = next(
                    (
                        o
                        for o in bpy.context.scene.objects
                        if o.name == name and o.parent is None and o.library is None
                    ),
                    None,
                )
            if existing is not None:
                current_parent = existing
            else:
                new_grp = bpy.data.objects.new(name, None)
                bpy.context.scene.collection.objects.link(new_grp)
                if current_parent is not None:
                    new_grp.parent = current_parent
                HierarchySync._finalize_stub_node(new_grp)
                current_parent = new_grp
        return current_parent

    def create_stubs(self, paths: Optional[List[str]] = None) -> List[str]:
        """Create empty placeholder Empties for missing hierarchy paths.

        Makes the current scene's skeleton match the reference without importing actual
        geometry. Each stub is an Empty object parented at the correct position in the hierarchy.

        Returns:
            List of created object names.
        """
        targets = paths if paths is not None else self.differences.get("missing", [])
        if not targets:
            self.logger.notice("No missing items to stub.")
            return []

        created: List[str] = []
        for path in targets:
            leaf = path.rsplit("|", 1)[-1]

            if self.dry_run:
                self.logger.info(f"[DRY-RUN] Would create stub: {path}")
                created.append(leaf)
                continue

            import bpy

            try:
                parent = self._ensure_parent_chain(path)
                pool = (
                    [c for c in parent.children if c.library is None]
                    if parent is not None
                    else [o for o in bpy.context.scene.objects if o.parent is None and o.library is None]
                )
                if any(c.name == leaf for c in pool):
                    self.logger.debug(f"Stub skipped (already exists): {path}")
                    continue

                stub = bpy.data.objects.new(leaf, None)
                bpy.context.scene.collection.objects.link(stub)
                if parent is not None:
                    stub.parent = parent
                self._finalize_stub_node(stub)
                created.append(stub.name)
                self.logger.debug(f"Created stub: {build_path(stub)}")
            except Exception as e:
                self.logger.warning(f"Failed to create stub for {path}: {e}")

        self.logger.result(f"Created {len(created)} stub object(s).")
        return created

    @staticmethod
    def _finalize_stub_node(obj) -> None:
        """Tag and colour a newly created stub Empty.

        Informational only — unlike mayatk's ``lockNode``, Blender has no lock-from-delete
        primitive to enforce protection.
        """
        try:
            obj[HierarchySync.STUB_ATTR] = True
            obj["notes"] = HierarchySync.STUB_NOTE
            ColorId.set_object_color(obj, HierarchySync.STUB_COLOR)
        except Exception:
            pass

    @staticmethod
    def _has_animation_data(obj, check_descendants: bool = False) -> bool:
        """Return True if *obj* has an assigned action, drivers, or constraints.

        Mirror of mayatk's ``_has_animation_data`` — Blender's animation/constraint model
        differs from Maya's (no anim layers, no expression nodes), so this checks the concepts
        that actually exist: ``animation_data.action``, drivers, and constraints.
        """
        def _check(o) -> bool:
            ad = getattr(o, "animation_data", None)
            if ad is not None and (ad.action or ad.drivers):
                return True
            return bool(getattr(o, "constraints", None))

        if _check(obj):
            return True
        if check_descendants:
            return any(_check(d) for d in getattr(obj, "children_recursive", []))
        return False

    @staticmethod
    def _has_animated_ancestor(obj) -> bool:
        """Return True if any ancestor of *obj* carries animation data.

        Mirror of mayatk's ``_has_animated_ancestor`` (used by ``quarantine_extras``): an extra
        parented under an animated object is likely intentionally attached — reparenting it out
        detaches it from that motion (``keep_transform`` only pins the *current* frame).
        """
        parent = getattr(obj, "parent", None)
        while parent is not None:
            if HierarchySync._has_animation_data(parent):
                return True
            parent = parent.parent
        return False

    #: Object-transform channels authored in LOCAL (parent-relative) space — animation on any of
    #: these evaluates differently once the object's parent changes.
    _TRANSFORM_DATA_PATHS = frozenset(
        {
            "location",
            "rotation_euler",
            "rotation_quaternion",
            "rotation_axis_angle",
            "scale",
            "delta_location",
            "delta_rotation_euler",
            "delta_rotation_quaternion",
            "delta_scale",
        }
    )

    @staticmethod
    def _reparent_would_shift_animation(obj) -> bool:
        """True when re-parenting *obj* changes how its animation evaluates.

        Mirror of mayatk's guard. Keyframed or driven transform channels (location / rotation /
        scale, incl. their delta_* variants and NLA-stripped actions) are authored in the
        object's LOCAL space, so under a different parent the same values produce different
        world-space motion — ``keep_transform`` only preserves the current frame. Constraints
        are excluded: like Maya, they target other objects in world space and are unaffected by
        the constrained object's own parent.
        """
        ad = getattr(obj, "animation_data", None)
        if ad is None:
            return False

        # Reuse anim_utils' slot-aware fcurve reader — Blender 5.1 dropped the flat
        # ``Action.fcurves`` list (keys now live in per-slot channelbags).
        from blendertk.anim_utils._anim_utils import get_fcurves, _slot_fcurves

        paths = HierarchySync._TRANSFORM_DATA_PATHS

        def _hits(fcurves) -> bool:
            return any(getattr(fc, "data_path", None) in paths for fc in fcurves)

        if _hits(get_fcurves([obj])):  # keyframed transform channels (active action)
            return True
        if getattr(ad, "drivers", None) and _hits(ad.drivers):  # driven transforms (flat list)
            return True
        for track in getattr(ad, "nla_tracks", []):  # NLA-stacked actions
            for strip in track.strips:
                action = getattr(strip, "action", None)
                if action and _hits(_slot_fcurves(action, getattr(strip, "action_slot", None))):
                    return True
        return False

    def _animation_skip(self, node) -> bool:
        """True if quarantining *node* would detach or shift animated motion.

        Mirror of mayatk's quarantine ``_animation_skip``: skip when the node itself (or a
        descendant) is animated, OR when it hangs under an animated ancestor it would be
        detached from. ``None`` (a node that no longer resolves) never skips.
        """
        return bool(
            node
            and (
                self._has_animation_data(node, check_descendants=True)
                or self._has_animated_ancestor(node)
            )
        )

    def quarantine_extras(
        self,
        group: str = "_QUARANTINE",
        paths: Optional[List[str]] = None,
        skip_animated: bool = True,
    ) -> List[str]:
        """Move extra (scene-only) items to a root-level quarantine Empty.

        Items that exist in the current scene but not in the reference are reparented under
        *group* so they no longer pollute the matched hierarchy. Ancestor deduplication is
        applied: if ``Grp`` and ``Grp|Child`` are both extra, only ``Grp`` is moved.

        Auto-detection: when ``group`` is the default ``"_QUARANTINE"`` and all extras share a
        single root-level ancestor that is itself extra (with 2+ direct extra children), that
        existing object is reused instead of creating a new one.
        """
        targets = paths if paths is not None else self.differences.get("extra", [])
        if not targets:
            self.logger.notice("No extra items to quarantine.")
            return []

        targets_set = set(targets)
        roots_only: List[str] = []
        for p in sorted(targets, key=lambda x: x.count("|")):
            parts = p.split("|")
            if not any(
                "|".join(parts[: i + 1]) in targets_set for i in range(len(parts) - 1)
            ):
                roots_only.append(p)

        if group == "_QUARANTINE" and roots_only:
            root_names = {p.split("|")[0] for p in roots_only}
            if len(root_names) == 1:
                natural_root = next(iter(root_names))
                direct_extra_children = sum(
                    1
                    for p in targets_set
                    if p.startswith(natural_root + "|") and "|" not in p[len(natural_root) + 1 :]
                )
                if natural_root in targets_set and direct_extra_children >= 2:
                    group = natural_root
                    self.logger.info(
                        f"Using existing root object '{group}' as quarantine "
                        f"(all extras are already under it)."
                    )

        already_root: List[str] = []
        needs_move: List[str] = []
        for p in roots_only:
            top = p.split("|")[0]
            if "|" not in p:
                (already_root if p == group else needs_move).append(p)
            elif top == group:
                already_root.append(p)
            else:
                needs_move.append(p)

        if already_root:
            self.logger.info(f"{len(already_root)} extra(s) already under '{group}' — skipped.")

        moved: List[str] = []

        if not needs_move:
            self.logger.notice(
                f"All {len(already_root)} extra(s) are already contained under '{group}'. "
                f"Nothing to move."
            )
            return [p.rsplit("|", 1)[-1] for p in already_root]

        if self.dry_run:
            for p in needs_move:
                node = self._resolve_node(p, source="current")
                if skip_animated and self._animation_skip(node):
                    self.logger.info(f"[DRY-RUN] Would skip (animated): {p}")
                    continue
                self.logger.info(f"[DRY-RUN] Would quarantine: {p}")
                moved.append(p.rsplit("|", 1)[-1])
            self.logger.result(f"[DRY-RUN] Would quarantine {len(moved)} item(s).")
            return moved

        import bpy

        quarantine_obj = None  # created lazily below, only once something actually needs it —
        # never leave a stray, empty "_QUARANTINE" object behind when every item is skipped.

        skipped_animated: List[str] = []
        for path in needs_move:
            node = self._resolve_node(path, source="current")
            if node is None:
                self.logger.debug(f"Quarantine skipped (node not found): {path}")
                continue
            if skip_animated and self._animation_skip(node):
                skipped_animated.append(path)
                continue
            try:
                if quarantine_obj is None:
                    # library is None: never adopt a same-named object from the linked reference
                    # library as the quarantine container (see create_stubs' matching guard).
                    quarantine_obj = next(
                        (o for o in bpy.data.objects if o.name == group and o.library is None), None
                    )
                    if quarantine_obj is None:
                        quarantine_obj = bpy.data.objects.new(group, None)
                        bpy.context.scene.collection.objects.link(quarantine_obj)
                _reparent([node], quarantine_obj, keep_transform=True)
                moved.append(node.name)
                self.logger.debug(f"Quarantined: {build_path(node)}")
            except Exception as e:
                self.logger.warning(f"Failed to quarantine {path}: {e}")

        if skipped_animated:
            for path in skipped_animated:
                self.logger.debug(f"Skipped (animated): {path}")
            self.logger.info(f"{len(skipped_animated)} extra(s) skipped (has animation data).")

        self.logger.result(f"Quarantined {len(moved)} item(s) under '{group}'.")
        return moved

    def fix_fuzzy_renames(self, items: Optional[List[Dict[str, str]]] = None) -> List[str]:
        """Rename nodes identified as fuzzy matches to their reference names.

        Each item is a dict with ``current_name`` (current path) and ``target_name`` (reference
        path), as produced by :meth:`analyze_hierarchies`. Unlike mayatk (where a Maya expression
        embeds the literal node name as text, so renaming a connected node breaks it), Blender
        drivers reference objects by ID pointer, not by name string — renaming never breaks a
        driver, so there is no animated-node rename guard to mirror here.
        """
        targets = items if items is not None else self.differences.get("fuzzy_matches", [])
        if not targets:
            self.logger.notice("No fuzzy renames to fix.")
            return []

        renamed: List[str] = []
        for entry in targets:
            current_path = entry.get("current_name", "")
            reference_path = entry.get("target_name", "")
            if not current_path or not reference_path:
                continue

            cur_leaf = current_path.rsplit("|", 1)[-1]
            ref_leaf = reference_path.rsplit("|", 1)[-1]
            if cur_leaf == ref_leaf:
                continue

            if self.dry_run:
                self.logger.info(f"[DRY-RUN] Would rename: '{cur_leaf}' → '{ref_leaf}'")
                renamed.append(cur_leaf)
                continue

            node = self._resolve_node(current_path, source="current")
            if node is None:
                self.logger.debug(f"Rename skipped (node not found): {current_path}")
                continue

            try:
                old_name = node.name
                # Blender auto-suffixes ".001" on a name collision — no manual counter needed,
                # unlike mayatk's manual _1/_2 conflict resolution.
                node.name = ref_leaf
                renamed.append(node.name)
                self.logger.debug(f"Renamed: '{old_name}' → '{node.name}'")
            except Exception as e:
                self.logger.warning(f"Failed to rename {cur_leaf}: {e}")

        self.logger.result(f"Renamed {len(renamed)} fuzzy-matched item(s).")
        return renamed

    def fix_reparented(
        self,
        items: Optional[List[Dict[str, str]]] = None,
        skip_animated: bool = True,
    ) -> List[str]:
        """Move reparented nodes to match their reference hierarchy position.

        Each item is a dict with ``current_path`` and ``reference_path`` keys, as produced by
        :meth:`analyze_hierarchies`.

        Args:
            items: List of reparented-item dicts. Defaults to ``self.differences["reparented"]``.
            skip_animated: When True (default), nodes whose animation would evaluate differently
                under a new parent (keyframed or driven local transform channels — see
                :meth:`_reparent_would_shift_animation`) are left in place and reported. Nodes
                animated only by constraints still move; like Maya, constrained world behavior is
                unaffected by the object's parent.
        """
        targets = items if items is not None else self.differences.get("reparented", [])
        if not targets:
            self.logger.notice("No reparented items to fix.")
            return []

        fixed: List[str] = []
        skipped_animated: List[str] = []
        for entry in targets:
            current_path = entry.get("current_path", "")
            reference_path = entry.get("reference_path", "")
            if not current_path or not reference_path:
                continue

            node = self._resolve_node(current_path, source="current")

            if skip_animated and node and self._reparent_would_shift_animation(node):
                skipped_animated.append(current_path)
                continue

            if self.dry_run:
                self.logger.info(
                    f"[DRY-RUN] Would reparent: {current_path} -> {reference_path}"
                )
                fixed.append(current_path.rsplit("|", 1)[-1])
                continue

            if node is None:
                self.logger.debug(f"Reparent skipped (node not found): {current_path}")
                continue

            try:
                old_parent = node.parent
                target_parent = self._ensure_parent_chain(reference_path)
                _reparent([node], target_parent, keep_transform=True)
                fixed.append(node.name)
                self.logger.debug(f"Reparented: {node.name} -> {build_path(node)}")
            except Exception as e:
                self.logger.warning(f"Failed to reparent {current_path}: {e}")
                continue

            # Clean up the now-empty source parent (avoids leftover shells). Isolated from the
            # reparent itself so a cleanup failure isn't mislogged as a failed reparent.
            try:
                self._cleanup_empty_source_parent(old_parent)
            except Exception as cleanup_err:
                self.logger.debug(
                    f"Empty-parent cleanup skipped for {current_path}: {cleanup_err}"
                )

        if skipped_animated:
            for path in skipped_animated:
                self.logger.debug(f"Reparent skipped (animated): {path}")
            self.logger.info(
                f"{len(skipped_animated)} reparent(s) skipped — moving these animated nodes "
                f"would change their motion. Disable 'Skip Animated' to move them anyway."
            )

        self.logger.result(f"Fixed {len(fixed)} reparented item(s).")
        return fixed

    def _cleanup_empty_source_parent(self, old_parent) -> None:
        """Delete *old_parent* if it is an empty, unanimated leftover stub shell.

        Only ever removes an auto-created stub/quarantine Empty, never user content: that's
        exactly the shape ``create_stubs`` / ``quarantine_extras`` produce (a childless,
        data-less local Empty). Preserved when it still has children, carries animation, exists
        in the reference hierarchy (deleting it would re-introduce a "missing" diff), or is a
        linked reference object.
        """
        if (
            old_parent is None
            or old_parent.library is not None
            or old_parent.type != "EMPTY"
            or old_parent.children
            or self._has_animation_data(old_parent)
        ):
            return
        if build_path(old_parent) in self.reference_scene_path_map:
            self.logger.debug(
                f"Preserved empty parent '{old_parent.name}' (exists in reference)"
            )
            return

        import bpy

        old_name = old_parent.name
        bpy.data.objects.remove(old_parent, do_unlink=True)
        self.logger.debug(f"Deleted empty source parent: {old_name}")


class ObjectSwapper(ptk.LoggingMixin):
    """Pull matched reference objects into the current scene (mirror of mayatk's ``ObjectSwapper``).

    Where mayatk imports the reference into a temporary namespace and strips it, this APPENDS the
    matched objects fresh from the source ``.blend`` (``bpy.data.libraries.load(link=False)``).
    Blender's native Append yields a fully-local copy of each object plus its mesh data,
    materials, and animation Action in one call, so none of mayatk's namespace-strip or
    anim-curve-transfer machinery is needed. Each pulled object is grafted onto its reference
    hierarchy position — an existing stub or a freshly-rebuilt parent chain — preserving the
    reference world transform; the ancestor dupes Append drags in as dependencies are removed.

    Modes (mirror mayatk):
        * ``pull_mode="Add to Scene"`` — keep Blender's auto ``.001`` suffix on a name collision.
        * ``pull_mode="Merge Hierarchies"`` — replace any existing local object at the target
          path (typically a stub) so the pulled object takes its exact name and slot.
        * ``pull_children`` — append the target's whole subtree, not just the single object.
    """

    def __init__(
        self,
        fuzzy_matching: bool = True,
        dry_run: bool = True,
        pull_mode: str = "Add to Scene",
        pull_children: bool = False,
    ):
        super().__init__()
        self.dry_run = dry_run
        self.fuzzy_matching = fuzzy_matching
        self.pull_mode = pull_mode
        self.pull_children = pull_children

    def pull_objects_from_reference(
        self, target_paths: List[str], source_file, reference_path_map: Dict[str, Any]
    ) -> bool:
        """Append the reference objects at *target_paths* into the current scene.

        Args:
            target_paths: reference hierarchy paths (``build_path`` keys) to pull.
            source_file: path to the reference ``.blend`` to append from.
            reference_path_map: ``{build_path: linked reference object}`` — resolves each path to
                its linked object (for its source name, subtree, and world matrix).

        Returns:
            True if at least one object was pulled (or, in dry-run, would be).
        """
        source_file = Path(source_file)
        if not target_paths:
            self.logger.error("No target objects specified for pull.")
            return False
        if not source_file.exists():
            self.logger.error(f"Source file not found: {source_file}")
            return False

        targets = []
        for path in target_paths:
            ref_obj = reference_path_map.get(path)
            if ref_obj is None:
                self.logger.debug(f"Pull: no reference object for '{path}'")
                continue
            try:
                ref_obj.name  # touch to detect a removed/invalidated reference
            except ReferenceError:
                continue
            targets.append((path, ref_obj))

        if not targets:
            self.logger.warning("No matching objects found in the reference.")
            return False

        # With pull_children, drop targets nested under another target (already pulled with it).
        if self.pull_children:
            paths_set = {p for p, _ in targets}
            targets = [
                (p, o)
                for p, o in targets
                if not any(p != q and p.startswith(q + "|") for q in paths_set)
            ]

        if self.dry_run:
            for path, _ in targets:
                self.logger.info(f"[DRY-RUN] Would pull: {path}")
            self.logger.result(f"[DRY-RUN] Would pull {len(targets)} object(s).")
            return True

        merge = self.pull_mode == "Merge Hierarchies"
        pulled = 0
        for path, ref_obj in targets:
            try:
                if self._pull_one(path, ref_obj, source_file, merge=merge):
                    pulled += 1
            except Exception as e:
                self.logger.error(f"Failed to pull '{path}': {e}")
                self.logger.debug(f"Full traceback: {traceback.format_exc()}")

        self.logger.result(f"Pulled {pulled} object(s) into the scene.")
        return pulled > 0

    def _pull_one(self, ref_path: str, ref_obj, source_file: Path, *, merge: bool) -> bool:
        """Append one reference object (and, if ``pull_children``, its subtree) into the scene."""
        import bpy

        bpy.context.view_layer.update()
        captured_world = ref_obj.matrix_world.copy()

        want_names = [ref_obj.name]
        if self.pull_children:
            want_names += [d.name for d in ref_obj.children_recursive]

        clean_leaf = ref_path.rsplit("|", 1)[-1]

        # Merge: replace the existing local object at the target path (typically a stub) so the
        # pulled object takes its place and the append can reuse the exact name. Mirror mayatk's
        # _safe_merge_delete conservatism — never silently destroy real content: an existing that
        # carries animation (own or descendant) or has children is PRESERVED and the pull skipped.
        if merge:
            existing = self._find_local_at_path(ref_path)
            if existing is not None:
                if existing.children or HierarchySync._has_animation_data(existing, check_descendants=True):
                    self.logger.info(
                        f"Preserved '{existing.name}' (has children or animation) — merge skipped "
                        f"for '{ref_path}'. Clear or move it first, or use Add to Scene."
                    )
                    return False
                bpy.data.objects.remove(existing, do_unlink=True)

        before = set(bpy.data.objects)
        with bpy.data.libraries.load(str(source_file), link=False) as (data_from, data_to):
            present = [n for n in want_names if n in data_from.objects]
            data_to.objects = present
        wanted = [o for o in data_to.objects if o is not None]
        if not wanted:
            self.logger.warning(f"Pull: append produced no object for '{ref_path}'.")
            return False
        newly = [o for o in bpy.data.objects if o not in before]
        # Append drags in the target's ancestor chain as dependencies; those aren't in
        # data_to.objects, so they're the extras to clean up.
        extras = [o for o in newly if o not in set(wanted)]

        # Link the pulled objects into the active scene.
        scene_coll = bpy.context.scene.collection
        for o in wanted:
            if not o.users_collection:
                scene_coll.objects.link(o)

        pulled_root = next(
            (o for o in wanted if strip_dup_suffix(o.name) == ref_obj.name), wanted[0]
        )

        # Detach from the append-dragged ancestor (world preserved), then delete the dupes so
        # they don't collide with the rebuilt parent chain.
        _reparent([pulled_root], None, keep_transform=True)
        for extra in extras:
            try:
                bpy.data.objects.remove(extra, do_unlink=True)
            except Exception:
                pass

        # Graft onto the reference hierarchy position, preserving the reference world transform.
        target_parent = HierarchySync._ensure_parent_chain(ref_path)
        pulled_root.matrix_world = captured_world
        _reparent([pulled_root], target_parent, keep_transform=True)

        # Merge: take the exact clean name (append may have suffixed if a dupe lingered).
        if merge and pulled_root.name != clean_leaf:
            try:
                pulled_root.name = clean_leaf
            except Exception:
                pass

        self.logger.debug(f"Pulled '{ref_path}' -> {build_path(pulled_root)}")
        return True

    @staticmethod
    def _find_local_at_path(ref_path: str):
        """Return the LOCAL scene object whose hierarchy path equals *ref_path*, or None."""
        import bpy

        for o in bpy.context.scene.objects:
            if o.library is None and build_path(o) == ref_path:
                return o
        return None


__all__ = [
    "HierarchySync",
    "HierarchyMapBuilder",
    "ObjectSwapper",
    "build_path",
    "should_keep_node_by_type",
    "stage_reference_blend",
]
