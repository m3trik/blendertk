# !/usr/bin/python
# coding=utf-8
"""Scene auto-instancer: convert geometrically identical meshes to instances.

Blender port of mayatk's AutoInstancer. A Blender "instance" is a linked
duplicate (objects sharing one mesh datablock), so member replacement is a
datablock swap plus the matcher's relative-transform correction — the
member object keeps its name, parent, collections and custom properties.
Maya→Blender orchestration mappings:

- DAG paths / UUIDs      → object references resolved by ``session_uid``
  (rename/reparent-proof identity, the UUID analogue).
- leaf-mode replacement  → ``member.data = proto.data`` +
  ``matrix_world @= rel``. The member's own children stay parented (Maya
  must move them to world because a transform child of an instanced
  hierarchy would appear under every instance path; Blender data-sharing
  has no such multiplication).
- hierarchy replacement  → linked-duplicate copy of the prototype subtree
  (``obj.copy()`` shares data), placed via the member's transform, member
  subtree deleted, root renamed to the member's name.
- locked/referenced      → library-linked / library-override objects.
- undo chunk             → none here; the calling slot wraps the run in
  ``@btk.undoable`` (blendertk convention: undo at the slot boundary).
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Sequence, Tuple, Union
from collections import defaultdict

import pythontk as ptk

from blendertk.core_utils.auto_instancer.geometry_matcher import (
    GeometryMatcher,
    _mesh,
)
from blendertk.core_utils.auto_instancer.assembly_reconstructor import (
    AssemblyReconstructor,
    ASSEMBLY_TAG_ATTR,
    _alive,
)
from blendertk.core_utils.auto_instancer.instancing_strategy import (
    InstancingStrategy,
    StrategyConfig,
    StrategyType,
)
from blendertk.node_utils._node_utils import _object_users

# Strategies that convert to instances. Both GPU_INSTANCE and COMBINE
# convert: sharing mesh data saves memory and keeps duplicates
# editable-as-one regardless of the engine-side draw-call decision the
# strategy encodes. KEEP_SEPARATE (needs_individual, or non-static
# non-GPU-instanceable) still blocks conversion.
_CONVERTIBLE_STRATEGIES = (StrategyType.GPU_INSTANCE, StrategyType.COMBINE)


def _natural_key(name: str) -> Tuple:
    """Sort key ordering embedded integers numerically (``Cube2`` < ``Cube10``)."""
    return tuple(
        int(token) if token.isdigit() else token
        for token in re.split(r"(\d+)", name)
    )


def _is_instanced(obj) -> bool:
    """True if the object's mesh datablock is shared with another object."""
    me = _mesh(obj)
    if me is None:
        return False
    return _object_users(me) > 1


def _depth(obj) -> int:
    """Number of ancestors — the Blender analogue of DAG path depth."""
    depth = 0
    node = obj.parent
    while node is not None:
        depth += 1
        node = node.parent
    return depth


def _descendants(obj) -> List[object]:
    return list(obj.children_recursive)


def _is_ancestor(a, b) -> bool:
    """True when *a* is an ancestor of *b* (compared by session_uid — bpy
    recreates RNA wrappers per access, so wrapper identity is unreliable)."""
    uid = a.session_uid
    node = b.parent
    while node is not None:
        if node.session_uid == uid:
            return True
        node = node.parent
    return False


def _prototype_preference_key(candidate: "InstanceCandidate") -> Tuple:
    """Sort key for prototype selection within a group.

    Already-instanced first (extends the existing instance set), then
    natural name order — deterministic across runs.
    """
    obj = candidate.obj
    name = obj.name if obj is not None else ""
    return (
        not (obj is not None and _is_instanced(obj)),
        _natural_key(name),
        name,
    )


class InstanceCandidate:
    """Holds information about an object candidate for instancing.

    ``obj`` re-resolves the live object from its ``session_uid`` on each
    access (name first, scan fallback), so candidates survive the renaming
    and reparenting that instancing performs on the scene — the analogue of
    mayatk's UUID-backed DAG-path re-resolution.
    """

    def __init__(self, obj):
        self._name: str = obj.name
        self._uid: int = obj.session_uid
        # Transform required to align prototype to this candidate
        # (column-convention mathutils.Matrix, or None for identity).
        self.relative_transform = None

    @property
    def obj(self):
        import bpy

        o = bpy.data.objects.get(self._name)
        if o is not None and o.session_uid == self._uid:
            return o
        for o in bpy.data.objects:
            if o.session_uid == self._uid:
                self._name = o.name  # renamed — refresh the fast path
                return o
        return None

    def exists(self) -> bool:
        return self.obj is not None

    def __repr__(self):
        return f"<InstanceCandidate {self._name}>"


class InstanceGroup:
    """A group of objects that are geometrically identical."""

    def __init__(self, prototype: InstanceCandidate):
        self.prototype = prototype
        self.members: List[InstanceCandidate] = []

    def __repr__(self):
        return (
            f"<InstanceGroup prototype={self.prototype._name} "
            f"members={len(self.members)}>"
        )


class AutoInstancer(ptk.LoggingMixin):
    """Convert matching meshes into instances (shared mesh datablocks).

    Destructive operations (deleting originals) only happen after the
    replacement has been fully assembled; a failure on one member leaves
    that member untouched and continues with the rest. Wrap the run in
    ``btk.undoable`` at the calling layer for a single undo step.
    """

    def __init__(
        self,
        tolerance: float = 0.001,
        scale_tolerance: Optional[float] = None,
        require_same_material: Union[bool, int] = True,
        check_uvs: bool = False,
        check_hierarchy: bool = False,
        separate_combined: bool = False,
        combine_assemblies: bool = True,
        combine_non_instanced: bool = True,
        combine_by_material: bool = True,
        combine_by_distance: bool = True,
        combine_distance_threshold: float = 10000.0,
        verbose: bool = True,
        search_radius_mult: float = 1.5,
        # Strategy Config
        is_static: bool = True,
        needs_individual: bool = False,
        will_be_lightmapped: bool = False,
        can_gpu_instance: bool = True,
        log_level: str = "WARNING",
    ) -> None:
        super().__init__()
        self.set_log_level(log_level)
        self._tolerance = tolerance
        # None = flow-dependent default. The assembly flow instances
        # uniformly SCALED copies (scale carried on the instance transform),
        # while plain leaf instancing stays strict so a resized prop is
        # kept distinct. Pass an explicit value to override either way.
        if scale_tolerance is None:
            scale_tolerance = 1.0 if separate_combined else 0.0
        self._scale_tolerance = scale_tolerance
        self._require_same_material = require_same_material
        self._check_uvs = check_uvs
        self.check_hierarchy = check_hierarchy
        self.separate_combined = separate_combined
        self._combine_assemblies = combine_assemblies
        # Game-ready remainder: join whatever did not instance (see
        # _combine_non_instanced). Skipped for non-static / needs_individual.
        self.combine_non_instanced = combine_non_instanced
        self.combine_by_material = combine_by_material
        self.combine_by_distance = combine_by_distance
        self.combine_distance_threshold = combine_distance_threshold
        self._verbose = verbose
        self._search_radius_mult = search_radius_mult

        self.strategy_config = StrategyConfig(
            is_static=is_static,
            needs_individual=needs_individual,
            will_be_lightmapped=will_be_lightmapped,
            can_gpu_instance=can_gpu_instance,
        )
        self.strategy_analyzer = InstancingStrategy(self.strategy_config)

        self.matcher = GeometryMatcher(
            tolerance=tolerance,
            scale_tolerance=scale_tolerance,
            require_same_material=require_same_material,
            check_uvs=check_uvs,
            verbose=verbose,
        )
        self.reconstructor = AssemblyReconstructor(
            matcher=self.matcher,
            combine_assemblies=combine_assemblies,
            search_radius_mult=search_radius_mult,
            verbose=verbose,
        )

        # Diagnostic tally of the last run — why matching meshes did (or did
        # not) become instances. Populated in ``_process_groups``; read by the
        # slot to explain "found matches but instanced nothing" (too simple /
        # count) instead of the generic "no matching geometry".
        self._reset_summary()

    # ------------------------------------------------------------------
    # Run summary (diagnostics)
    # ------------------------------------------------------------------
    @staticmethod
    def default_summary() -> Dict[str, object]:
        """A zeroed run-summary — the shape of :attr:`last_run_summary`.

        Keys mirror mayatk's ``AutoInstancer`` for parity: ``matched_groups``
        (viable groups of >=2 identical meshes discovered), ``instanced_groups``
        / ``instances_created`` (converted to shared datablocks),
        ``simple_groups`` (identical but below the micro-triangle threshold —
        combined instead of instanced), ``kept_separate_groups`` (left as-is by
        the strategy: flagged individual / non-static), ``micro_threshold`` (the
        triangle cutoff in force) and ``details`` (per-skipped-group records for
        the console: ``{"name", "reason", "count", "tris"}``).
        """
        return {
            "matched_groups": 0,
            "instanced_groups": 0,
            "instances_created": 0,
            "simple_groups": 0,
            "kept_separate_groups": 0,
            "micro_threshold": InstancingStrategy.MICRO_TRI_THRESHOLD,
            "details": [],
        }

    def _reset_summary(self) -> None:
        self.last_run_summary = self.default_summary()
        self.last_run_summary["micro_threshold"] = (
            self.strategy_analyzer.MICRO_TRI_THRESHOLD
        )

    @staticmethod
    def format_summary(summary: Dict[str, object], output_count: int) -> str:
        """Human-readable, DCC-agnostic description of a run *summary*.

        ``output_count`` is the number of live result objects the caller kept
        (prototypes + instances + combined remainder). Returns ASCII plain
        text with ``-`` bullets (ASCII so a non-UTF-8 console ``print`` can't
        raise) — the slot shows it in its message box (newlines → ``<br>``)
        and prints it to the console verbatim.
        """
        matched = summary.get("matched_groups", 0)
        if matched == 0:
            if output_count > 0:
                return (
                    "Auto Instance: no geometrically identical meshes to "
                    f"instance; combined loose geometry into {output_count} "
                    "mesh(es)."
                )
            return "Auto Instance: no geometrically identical meshes were found."

        micro = summary.get("micro_threshold", InstancingStrategy.MICRO_TRI_THRESHOLD)
        details = summary.get("details", []) or []
        simple = [d for d in details if d.get("reason") == "too_simple"]
        kept = [d for d in details if d.get("reason") == "kept_separate"]
        instanced = summary.get("instanced_groups", 0)
        instances = summary.get("instances_created", 0)

        def _names(items):
            return ", ".join(
                f"{d['name']} (x{d['count']}, {d['tris']} tris)" for d in items
            )

        lines = [f"Auto Instance: {matched} matching group(s) found."]
        if instanced:
            lines.append(
                f"- Instanced {instanced} group(s) -> {instances} new instance(s)."
            )
        if simple:
            lines.append(
                f"- {len(simple)} group(s) too simple to instance (< {micro} "
                f"tris); combined where possible: {_names(simple)}."
            )
        if kept:
            lines.append(
                f"- {len(kept)} group(s) left separate (flagged individual / "
                f"non-static): {_names(kept)}."
            )
        # Only when no line above explains the outcome (e.g. every group's
        # conversion failed) — otherwise the reasons above already say it.
        if not (instanced or simple or kept):
            lines.append("- Nothing was instanced.")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Configuration properties — forwarded to collaborators so post-init
    # changes stay in sync.
    # ------------------------------------------------------------------
    @property
    def tolerance(self):
        return self._tolerance

    @tolerance.setter
    def tolerance(self, value):
        self._tolerance = value
        if hasattr(self, "matcher"):
            self.matcher.tolerance = value

    @property
    def scale_tolerance(self):
        return self._scale_tolerance

    @scale_tolerance.setter
    def scale_tolerance(self, value):
        self._scale_tolerance = value
        if hasattr(self, "matcher"):
            self.matcher.scale_tolerance = value

    @property
    def require_same_material(self):
        return self._require_same_material

    @require_same_material.setter
    def require_same_material(self, value):
        self._require_same_material = value
        if hasattr(self, "matcher"):
            self.matcher.require_same_material = value

    @property
    def check_uvs(self):
        return self._check_uvs

    @check_uvs.setter
    def check_uvs(self, value):
        self._check_uvs = value
        if hasattr(self, "matcher"):
            self.matcher.check_uvs = value

    @property
    def combine_assemblies(self):
        return self._combine_assemblies

    @combine_assemblies.setter
    def combine_assemblies(self, value):
        self._combine_assemblies = value
        if hasattr(self, "reconstructor"):
            self.reconstructor.combine_assemblies = value

    @property
    def search_radius_mult(self):
        return self._search_radius_mult

    @search_radius_mult.setter
    def search_radius_mult(self, value):
        self._search_radius_mult = value
        if hasattr(self, "reconstructor"):
            self.reconstructor.search_radius_mult = value

    @property
    def verbose(self):
        return self._verbose

    @verbose.setter
    def verbose(self, value):
        self._verbose = value
        if hasattr(self, "matcher"):
            self.matcher.verbose = value
        if hasattr(self, "reconstructor"):
            self.reconstructor.verbose = value

    # ------------------------------------------------------------------
    # Node filters
    # ------------------------------------------------------------------
    @staticmethod
    def _hierarchy_contains_mesh(obj) -> bool:
        """True if *obj* is (or has a descendant that is) a mesh object.

        Meshless objects (cameras, lights, empties) all produce identical
        empty hierarchy signatures and would otherwise be "instanced" into
        each other — i.e. deleted and replaced with empty transforms.
        """
        if _mesh(obj) is not None:
            return True
        return any(_mesh(c) is not None for c in obj.children_recursive)

    @staticmethod
    def _uneditable(obj) -> bool:
        """Library-linked / override objects — cannot be deleted or edited."""
        return bool(obj.library or obj.override_library)

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------
    def run(self, objects: Optional[Sequence[object]] = None) -> List[object]:
        """Discover and instance matching meshes.

        Operates on *objects*, or the selection, or every object in the
        scene (in that order of fallback). Returns the flat list of
        prototypes + converted members, plus the combined remainder meshes
        when ``combine_non_instanced`` is enabled.
        """
        import bpy
        from blendertk.core_utils._core_utils import selected_objects

        if objects is None:
            objects = list(selected_objects())
            if not objects:
                objects = list(bpy.context.scene.objects)
        return self._run([o for o in objects if o is not None])

    @staticmethod
    def _expand_transform_descendants(objects: List[object]) -> List[object]:
        """Each object plus all its descendants, deduped."""
        out: List[object] = []
        seen = set()
        for obj in objects:
            if not _alive(obj):
                continue
            for o in [obj] + _descendants(obj):
                if o.session_uid not in seen:
                    seen.add(o.session_uid)
                    out.append(o)
        return out

    def _run(self, objects: List[object]) -> List[object]:
        self._reset_summary()
        # ``check_hierarchy`` is derived per-run from the flow flags; the
        # instance attribute is user configuration and is never mutated here.
        check_hierarchy = self.check_hierarchy

        if self.separate_combined:
            # Combined meshes nested under selected groups must separate too.
            objects = self._expand_transform_descendants(objects)
            objects = self.reconstructor.separate_combined_meshes(objects)
            objects = self.reconstructor.reassemble_assemblies(objects)

            if self.combine_assemblies:
                objects = self.reconstructor.combine_reassembled_assemblies(objects)
                check_hierarchy = False
            else:
                check_hierarchy = True

            # Canonicalize leaf meshes AFTER reassembly so instancing can
            # match them (absorbs translation/rotation into the transform so
            # PCA-aligned geometry compares equal). Gated on the assembly
            # flow only — for plain leaf-instancing it collapses
            # frozen-vs-non-frozen copies the user wants kept distinct.
            objects = self.reconstructor.canonicalize_leaf_meshes(objects)

        groups = self.find_instance_groups(objects, check_hierarchy=check_hierarchy)

        # Process groups whose MEMBERS sit shallowest first: members are what
        # get deleted, so an ancestor replacement (a whole assembly) must run
        # before groups matching its descendants. Keyed on members, not the
        # prototype: prototype selection (instanced-first) can pick a
        # prototype at a different depth than the members.
        def _group_depth(group: InstanceGroup) -> int:
            objs = [m.obj for m in group.members if m.obj is not None]
            if objs:
                return min(_depth(o) for o in objs)
            proto = group.prototype.obj
            return _depth(proto) if proto is not None else 0

        groups.sort(
            key=lambda g: (
                _group_depth(g),
                _natural_key(g.prototype._name),
                g.prototype._name,
            )
        )

        # When the remainder-combine will run, micro duplicates defer to it
        # (merged beats instanced below the micro threshold) — EXCEPT
        # combined-assembly copies, which instance regardless of size.
        combine_will_run = (
            self.combine_non_instanced
            and self.strategy_config.is_static
            and not self.strategy_config.needs_individual
        )
        defer_micro_except: Optional[set] = None
        if combine_will_run:
            defer_micro_except = set(
                getattr(self.reconstructor, "_combined_assembly_uids", [])
            )

        all_instances, report = self._process_groups(
            groups,
            allowed_strategies=_CONVERTIBLE_STRATEGIES,
            check_hierarchy=check_hierarchy,
            defer_micro_except=defer_micro_except,
        )

        # SECOND PASS: instance leaf geometry inside reconstructed assemblies
        # that did not match as whole hierarchies.
        if self.separate_combined and not self.combine_assemblies:
            self.logger.info("Running second pass: leaf geometry instancing")
            created = [o for entry in report for o in entry["instances"][1:]]
            leaf_candidates = self._collect_leaf_candidates(
                objects, all_instances, created
            )
            leaf_groups = self.find_instance_groups(
                leaf_candidates, check_hierarchy=False
            )
            created, leaf_report = self._process_groups(
                leaf_groups,
                allowed_strategies=_CONVERTIBLE_STRATEGIES,
                check_hierarchy=False,
                defer_micro_except=defer_micro_except,
            )
            all_instances.extend(created)
            report.extend(leaf_report)

        self.reconstructor.cleanup_empty_sources()

        # Game-ready remainder: combine whatever did not become an instance.
        if combine_will_run:
            converted = [o for entry in report for o in entry["instances"]]
            combined = self._combine_non_instanced(objects, converted)
            all_instances.extend(combined)
            self.reconstructor.cleanup_empty_assembly_groups()

        if self.verbose:
            self._log_report(report, len(groups))

        # Filter dead references (hierarchy-mode replacements delete
        # originals; callers get only live objects — the bpy analogue of the
        # Maya slot's objExists filter).
        return [o for o in all_instances if _alive(o)]

    def _combine_non_instanced(
        self, objects: List[object], converted: Sequence[object] = ()
    ) -> List[object]:
        """Join the non-instanced remainder into per-material clusters.

        Instances share their mesh data and stay untouched; loose leftovers
        are combined via ``EditUtils.combine_objects`` — by material and by
        spatial cluster per the settings — to cut draw calls for a
        game-ready result. Assembly PRODUCTS are protected: a combined copy
        that merely failed to instance and parts still parented under an
        assembly group are semantic units the user sorted for, not remainder.
        Descendants of *converted* members are excluded too — a preserved
        child of a leaf-converted member is part of that unit, not remainder
        (Maya reaches the same outcome by moving children to world, out of
        the candidate scope).
        """
        import bpy

        protected = set(
            getattr(self.reconstructor, "_combined_assembly_uids", [])
        )
        assembly_roots = [
            o for o in bpy.data.objects if o.get(ASSEMBLY_TAG_ATTR)
        ]

        def under_assembly(obj) -> bool:
            return any(_is_ancestor(root, obj) for root in assembly_roots)

        # Objects outside the active view layer (excluded collections) can
        # be instanced by the datablock swap but cannot be select_set/joined
        # — feeding one to combine_objects would abort the run mid-scene.
        view_layer_uids = {
            o.session_uid for o in bpy.context.view_layer.objects
        }

        candidates = [
            o
            for o in self._collect_leaf_candidates(objects, [], created=converted)
            if not _is_instanced(o)
            and o.session_uid not in protected
            and not under_assembly(o)
            and not self._uneditable(o)
            and o.session_uid in view_layer_uids
        ]
        if len(candidates) < 2:
            return []

        from blendertk.edit_utils._edit_utils import combine_objects

        result = combine_objects(
            candidates,
            group_by_material=self.combine_by_material,
            cluster_by_distance=self.combine_by_distance,
            threshold=self.combine_distance_threshold,
        )
        if not result:
            return []
        combined = [o for o in ptk.make_iterable(result) if o is not None]
        self.logger.info(
            "Combined %s non-instanced meshes into %s", len(candidates), len(combined)
        )
        return combined

    def _collect_leaf_candidates(
        self,
        objects: List[object],
        processed: List[object],
        created: Sequence[object] = (),
    ) -> List[object]:
        """Mesh objects derived from *objects*, excluding *processed*.

        Scope is limited to the input set and its descendants — the second
        pass must never touch scene content the caller didn't hand in.
        Objects in *created* are excluded together with their descendants.
        """
        excluded = set()
        for o in processed:
            if _alive(o):
                excluded.add(o.session_uid)
        for o in created:
            if _alive(o):
                excluded.update(d.session_uid for d in _descendants(o))

        candidates: List[object] = []
        seen = set()
        for obj in objects:
            if not _alive(obj):
                continue
            for o in [obj] + _descendants(obj):
                uid = o.session_uid
                if uid in seen or uid in excluded:
                    continue
                seen.add(uid)
                if _mesh(o) is not None:
                    candidates.append(o)
        return candidates

    # ------------------------------------------------------------------
    # Group discovery
    # ------------------------------------------------------------------
    def find_instance_groups(
        self,
        objects: Optional[Sequence[object]] = None,
        check_hierarchy: Optional[bool] = None,
    ) -> List[InstanceGroup]:
        """Find groups of identical objects.

        ``check_hierarchy`` overrides the instance setting for this call
        (used internally to keep ``run()`` re-entrant).
        """
        import bpy

        if check_hierarchy is None:
            check_hierarchy = self.check_hierarchy

        # Geometry may have changed since the last discovery (separation,
        # canonicalization, prior conversions) — start from fresh caches.
        self.matcher.clear_cache()
        bpy.context.view_layer.update()

        if objects is None:
            from blendertk.core_utils._core_utils import selected_objects

            objects = list(selected_objects())
            if not objects:
                objects = list(bpy.context.scene.objects)

        candidates = []
        seen = set()
        for obj in objects:
            if not _alive(obj):
                continue
            if obj.session_uid in seen or self._uneditable(obj):
                continue
            seen.add(obj.session_uid)

            if check_hierarchy:
                if not self._hierarchy_contains_mesh(obj):
                    continue
                candidates.append(InstanceCandidate(obj))
            else:
                if _mesh(obj) is not None:
                    candidates.append(InstanceCandidate(obj))

        # Group by signature
        signature_map = defaultdict(list)
        for candidate in candidates:
            obj = candidate.obj
            if check_hierarchy:
                sig = self.matcher.get_hierarchy_signature(obj)
            else:
                sig = self.matcher.get_mesh_signature(obj)
            if sig:
                signature_map[sig].append(candidate)

        # Merge similar signatures if we are in combine mode
        if not check_hierarchy and self.combine_assemblies:
            signature_map = self._merge_similar_signatures(signature_map)

        if self.verbose:
            self.logger.debug(
                "Signature map: %s unique signatures", len(signature_map)
            )

        # Every member is verified via _match_pair regardless of mode —
        # instancing replaces the member's geometry with the prototype's, so
        # exact identity (and the correct relative transform) is required.
        # Merged (near-identical) buckets only widen the candidate pool a
        # prototype is TRIED against; nothing is accepted unverified.
        groups = []
        for sig, potential_matches in signature_map.items():
            potential_matches.sort(key=_prototype_preference_key)

            while potential_matches:
                prototype = potential_matches.pop(0)
                current_group = InstanceGroup(prototype)

                remaining_candidates = []
                for candidate in potential_matches:
                    if self._match_pair(prototype, candidate, check_hierarchy):
                        current_group.members.append(candidate)
                    else:
                        remaining_candidates.append(candidate)

                groups.append(current_group)
                potential_matches = remaining_candidates

        return groups

    def _match_pair(
        self,
        prototype: InstanceCandidate,
        candidate: InstanceCandidate,
        check_hierarchy: bool,
    ) -> bool:
        """Match *candidate* against *prototype*; stores the relative
        transform on the candidate when identical."""
        p_obj = prototype.obj
        c_obj = candidate.obj
        if p_obj is None or c_obj is None:
            return False
        if check_hierarchy:
            is_identical, rel_mtx = self.matcher.are_hierarchies_identical(
                p_obj, c_obj, is_root=True
            )
        else:
            is_identical, rel_mtx = self.matcher.are_meshes_identical(p_obj, c_obj)
        if is_identical:
            # Overwrite even with None: a survivor re-matched against a
            # promoted prototype must not keep the transform it had relative
            # to the old, deleted prototype.
            candidate.relative_transform = rel_mtx
        return is_identical

    def _merge_similar_signatures(self, signature_map):
        """Merge signature buckets that are similar enough.

        Only buckets with identical material and UV signature components are
        merged — geometric similarity must never override
        ``require_same_material`` / ``check_uvs``.
        """
        sorted_keys = sorted(signature_map.keys(), key=lambda x: x[:3])

        merged_map = defaultdict(list)
        processed_sigs = set()

        for i, sig in enumerate(sorted_keys):
            if sig in processed_sigs:
                continue

            merged_map[sig].extend(signature_map[sig])
            processed_sigs.add(sig)

            topo = sig[:3]
            pca = sig[3]

            for j in range(i + 1, len(sorted_keys)):
                other_sig = sorted_keys[j]
                if other_sig in processed_sigs:
                    continue
                if other_sig[4:] != sig[4:]:  # materials / UV sets must match
                    continue

                o_pca = other_sig[3]

                if other_sig[:3] == topo:
                    if pca and o_pca:
                        diff = sum(abs(p1 - p2) for p1, p2 in zip(pca, o_pca))
                        if diff > 0.1:
                            continue
                    elif pca != o_pca:
                        continue

                    merged_map[sig].extend(signature_map[other_sig])
                    processed_sigs.add(other_sig)

                elif pca and o_pca:
                    diff = sum(abs(p1 - p2) for p1, p2 in zip(pca, o_pca))
                    total_mag = sum(pca) + sum(o_pca) + 0.001
                    rel_diff = diff / total_mag

                    if rel_diff < 0.005:
                        self.logger.debug(
                            "Merging near-identical signature %s into %s "
                            "(topology differs; combine mode)",
                            other_sig[:3],
                            sig[:3],
                        )
                        merged_map[sig].extend(signature_map[other_sig])
                        processed_sigs.add(other_sig)

        return merged_map

    # ------------------------------------------------------------------
    # Conversion
    # ------------------------------------------------------------------
    def _process_groups(
        self,
        groups: List[InstanceGroup],
        allowed_strategies: Tuple[StrategyType, ...],
        check_hierarchy: bool,
        defer_micro_except: Optional[set] = None,
    ) -> Tuple[List[object], List[Dict[str, object]]]:
        """Strategy-gate and convert each group; returns (instances, report).

        ``defer_micro_except`` (a set of protected prototype session_uids,
        or ``None`` to disable) hands MICRO duplicates to the
        remainder-combine instead of instancing them: below
        ``MICRO_TRI_THRESHOLD`` the per-draw-call overhead of an instance
        costs more than the merged triangles. Combined-assembly prototypes
        are exempt — an assembly is a unit the user sorted for and
        instances regardless of size.
        """
        # In leaf mode a target's own children are not part of the matched
        # geometry and are preserved; in hierarchy mode they ARE the matched
        # content and are replaced wholesale.
        preserve_children = not check_hierarchy
        all_instances: List[object] = []
        report: List[Dict[str, object]] = []

        for group in groups:
            if not group.members:
                continue

            # An earlier (shallower) group's processing may have deleted this
            # group's prototype or members (ancestor replacements).
            survivors = [m for m in group.members if m.exists()]
            if group.prototype.exists():
                group.members = survivors
            else:
                if len(survivors) < 2:
                    continue
                group = self._rebuild_group_from_survivors(
                    survivors, check_hierarchy
                )
            if not group.members:
                continue

            group_size = len(group.members) + 1
            strategy, tri_count = self._evaluate_group_strategy(group, group_size)

            # A viable group of >=2 identical meshes — record it before the
            # strategy gate so the summary can explain matches that were found
            # but not instanced (too simple / count).
            self.last_run_summary["matched_groups"] += 1
            proto_name = group.prototype._name

            if strategy not in allowed_strategies:
                self.last_run_summary["kept_separate_groups"] += 1
                self.last_run_summary["details"].append(
                    {
                        "name": proto_name,
                        "reason": "kept_separate",
                        "count": group_size,
                        "tris": tri_count,
                    }
                )
                if self.verbose:
                    self.logger.info(
                        "Skipping instancing for %s (Strategy: %s, Count: %s)",
                        group.prototype._name,
                        strategy.name,
                        group_size,
                    )
                continue

            if (
                defer_micro_except is not None
                and strategy is StrategyType.COMBINE
                and tri_count < self.strategy_analyzer.MICRO_TRI_THRESHOLD
                and group.prototype._uid not in defer_micro_except
            ):
                self.last_run_summary["simple_groups"] += 1
                self.last_run_summary["details"].append(
                    {
                        "name": proto_name,
                        "reason": "too_simple",
                        "count": group_size,
                        "tris": tri_count,
                    }
                )
                if self.verbose:
                    self.logger.info(
                        "Deferring %s micro duplicates of %s (%s tris) to the "
                        "remainder combine",
                        group_size,
                        group.prototype._name,
                        tri_count,
                    )
                continue

            created = self._convert_group_to_instances(group, preserve_children)
            if len(created) <= 1:
                continue
            all_instances.extend(created)
            self.last_run_summary["instanced_groups"] += 1
            self.last_run_summary["instances_created"] += len(created) - 1
            report.append(
                {
                    "prototype": group.prototype._name,
                    "instance_count": len(created) - 1,
                    "instances": created,
                }
            )

        return all_instances, report

    def _rebuild_group_from_survivors(
        self, survivors: List[InstanceCandidate], check_hierarchy: bool
    ) -> InstanceGroup:
        """Form a new group from *survivors* with a promoted prototype.

        Survivors matched the old (now deleted) prototype, so their stored
        relative transforms are stale — each is re-matched against the
        promoted prototype (tolerance is not exactly transitive).
        """
        survivors = sorted(survivors, key=_prototype_preference_key)
        prototype = survivors[0]
        group = InstanceGroup(prototype)
        self.logger.debug("Prototype gone; promoted survivor %s", prototype._name)
        for candidate in survivors[1:]:
            if self._match_pair(prototype, candidate, check_hierarchy):
                group.members.append(candidate)
        return group

    def _evaluate_group_strategy(
        self, group: InstanceGroup, group_size: int
    ) -> Tuple[StrategyType, int]:
        """Strategy + triangle count for a group's prototype."""
        proto = group.prototype.obj

        tri_count = 0
        if proto is not None:
            if _mesh(proto) is not None:
                meshes = [proto]
            else:
                # An assembly/group — total the descendant triangle counts.
                meshes = [c for c in proto.children_recursive if _mesh(c)]
            for m in meshes:
                try:
                    tri_count += sum(
                        max(len(p.vertices) - 2, 0) for p in m.data.polygons
                    )
                except Exception:
                    pass
        return (
            self.strategy_analyzer.evaluate(group_size, triangle_count=tri_count),
            tri_count,
        )

    def _convert_group_to_instances(
        self, group: InstanceGroup, preserve_children: bool = True
    ) -> List[object]:
        """Convert all members of a group to instances of the prototype.

        A member that fails to convert is left untouched (its original is
        only deleted after the replacement is fully assembled) and the
        remaining members are still processed.
        """
        proto_obj = group.prototype.obj
        if proto_obj is None:
            return []
        if not group.members:
            return [proto_obj]

        instances: List[object] = []
        for member in group.members:
            try:
                new_instance = self._replace_member_with_instance(
                    group.prototype, member, preserve_children
                )
            except Exception as e:
                self.logger.error(
                    "Failed to instance %s from prototype %s — original kept: %s",
                    member._name,
                    group.prototype._name,
                    e,
                )
                continue
            if new_instance is not None:
                instances.append(new_instance)

        proto_obj = group.prototype.obj  # re-resolve (renames are possible)
        return [proto_obj] + instances

    def _copy_subtree(self, src, collections) -> object:
        """Linked-duplicate copy of *src* and its subtree (data shared)."""
        new = src.copy()
        for coll in collections:
            coll.objects.link(new)
        for child in src.children:
            c_new = self._copy_subtree(child, collections)
            c_new.parent = new
            c_new.matrix_parent_inverse = child.matrix_parent_inverse.copy()
        return new

    def _replace_member_with_instance(
        self,
        prototype: InstanceCandidate,
        member: InstanceCandidate,
        preserve_children: bool,
    ) -> Optional[object]:
        """Replace *member* with an instance of *prototype*.

        Leaf mode: the member object is KEPT — its mesh datablock is swapped
        for the prototype's and the matcher's relative transform is folded
        into its world matrix (name, parent, children, collections and
        custom properties survive; a linked duplicate has no per-instance
        path problem, so children need not move to world as in Maya).

        Hierarchy mode: the prototype subtree is copied as linked duplicates
        placed via the member's transform; the member subtree is deleted and
        the new root takes its name. The replacement is fully built before
        the original is deleted; on failure the partial copy is removed and
        the original left untouched.
        """
        import bpy

        member_obj = member.obj
        proto_obj = prototype.obj
        if member_obj is None or proto_obj is None:
            return None

        # Refuse overlapping hierarchies — instancing a node into its own
        # ancestor/descendant chain creates parent cycles.
        if (
            member_obj.session_uid == proto_obj.session_uid
            or _is_ancestor(proto_obj, member_obj)
            or _is_ancestor(member_obj, proto_obj)
        ):
            self.logger.warning(
                "Skipping %s: overlaps prototype hierarchy %s",
                member_obj.name,
                proto_obj.name,
            )
            return None

        rel = member.relative_transform

        if preserve_children:
            # Already an instance of this prototype (same datablock, no
            # correction to apply) — nothing to do. Without this, a repeat
            # run would re-replicate the prototype's children onto the
            # member every time (same-data pairs always fast-path match
            # with rel=None, so this early-out loses nothing).
            if (
                rel is None
                and member_obj.data is not None
                and proto_obj.data is not None
                and member_obj.data.name_full == proto_obj.data.name_full
            ):
                return member_obj

            # Leaf mode — datablock swap on the kept member object. The
            # prototype's own transform children are replicated onto the
            # member as linked duplicates FIRST (Maya's instance(leaf=True)
            # moves them across too), so a failure there leaves the member
            # untouched.
            new_children = []
            try:
                collections = list(member_obj.users_collection) or [
                    bpy.context.scene.collection
                ]
                for child in proto_obj.children:
                    c_new = self._copy_subtree(child, collections)
                    c_new.parent = member_obj
                    c_new.matrix_parent_inverse = child.matrix_parent_inverse.copy()
                    new_children.append(c_new)
            except Exception:
                for o in new_children:
                    for d in [o] + _descendants(o):
                        try:
                            bpy.data.objects.remove(d, do_unlink=True)
                        except Exception:
                            pass
                raise
            if rel is not None:
                # Folding rel into the member's matrix keeps its GEOMETRY
                # visually fixed but moves its FRAME — compensate the
                # member's own pre-existing children so their world pose is
                # unchanged (child_world = parent_world @ parent_inverse @
                # basis; new parent_world = W @ rel, so parent_inverse' =
                # rel⁻¹ @ parent_inverse). Maya achieves the same by moving
                # children to world before the swap.
                inv = rel.inverted()
                fresh = {c.session_uid for c in new_children}
                for c in member_obj.children:
                    if c.session_uid not in fresh:
                        c.matrix_parent_inverse = inv @ c.matrix_parent_inverse
                member_obj.matrix_world = member_obj.matrix_world @ rel
            member_obj.data = proto_obj.data
            return member_obj

        # Hierarchy mode — linked-duplicate subtree replacement.
        collections = list(member_obj.users_collection) or [
            bpy.context.scene.collection
        ]
        new_root = None
        try:
            new_root = self._copy_subtree(proto_obj, collections)
            new_root.parent = member_obj.parent
            new_root.matrix_parent_inverse = member_obj.matrix_parent_inverse.copy()
            new_root.matrix_basis = member_obj.matrix_basis.copy()
            bpy.context.view_layer.update()
            if rel is not None:
                new_root.matrix_world = new_root.matrix_world @ rel
        except Exception:
            # Discard the partial replacement; the original is untouched.
            if new_root is not None:
                for o in [new_root] + _descendants(new_root):
                    try:
                        bpy.data.objects.remove(o, do_unlink=True)
                    except Exception:
                        pass
            raise

        # Replacement verified — now it is safe to swap.
        member_name = member_obj.name
        doomed = [member_obj] + _descendants(member_obj)
        for o in doomed:
            try:
                bpy.data.objects.remove(o, do_unlink=True)
            except Exception:
                pass
        new_root.name = member_name
        return new_root

    def _log_report(self, report: List[Dict[str, object]], group_count: int) -> None:
        total_instances = sum(entry["instance_count"] for entry in report)
        self.logger.info(
            "AutoInstancer processed %s groups and created %s instances",
            group_count,
            total_instances,
        )
        for entry in report:
            self.logger.info(
                " - %s → %s instances", entry["prototype"], entry["instance_count"]
            )


def auto_instance(
    objects: Optional[Sequence[object]] = None,
    tolerance: float = 0.001,
    scale_tolerance: Optional[float] = None,
    require_same_material: Union[bool, int] = True,
    check_uvs: bool = False,
    check_hierarchy: bool = False,
    separate_combined: bool = False,
    combine_assemblies: bool = True,
    combine_non_instanced: bool = True,
    combine_by_material: bool = True,
    combine_by_distance: bool = True,
    combine_distance_threshold: float = 10000.0,
    search_radius_mult: float = 1.5,
    is_static: bool = True,
    needs_individual: bool = False,
    will_be_lightmapped: bool = False,
    can_gpu_instance: bool = True,
    verbose: bool = True,
    log_level: str = "WARNING",
    return_summary: bool = False,
) -> Union[List[object], Tuple[List[object], Dict[str, object]]]:
    """Find geometrically identical meshes and convert them to instances.

    Convenience wrapper mirroring ``mtk.auto_instance`` — see
    :class:`AutoInstancer` for parameter semantics. Operates on *objects*,
    the selection, or the whole scene (in that order of fallback). Returns
    prototypes + converted members + combined remainder meshes.

    With ``return_summary=True`` returns ``(created, summary)`` where
    ``summary`` is the run's :meth:`AutoInstancer.default_summary`-shaped
    diagnostics (see :attr:`AutoInstancer.last_run_summary`); otherwise
    returns just ``created`` (backward compatible).
    """
    instancer = AutoInstancer(
        tolerance=tolerance,
        scale_tolerance=scale_tolerance,
        require_same_material=require_same_material,
        check_uvs=check_uvs,
        check_hierarchy=check_hierarchy,
        separate_combined=separate_combined,
        combine_assemblies=combine_assemblies,
        combine_non_instanced=combine_non_instanced,
        combine_by_material=combine_by_material,
        combine_by_distance=combine_by_distance,
        combine_distance_threshold=combine_distance_threshold,
        search_radius_mult=search_radius_mult,
        is_static=is_static,
        needs_individual=needs_individual,
        will_be_lightmapped=will_be_lightmapped,
        can_gpu_instance=can_gpu_instance,
        verbose=verbose,
        log_level=log_level,
    )
    created = instancer.run(objects)
    if return_summary:
        return created, instancer.last_run_summary
    return created
