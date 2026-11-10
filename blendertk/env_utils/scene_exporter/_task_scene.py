# !/usr/bin/python
# coding=utf-8
"""Scene-level export tasks (mirror of mayatk's): the working unit and the
export-set filters.
"""

from typing import Any, Dict, List, Optional

import pythontk as ptk

from blendertk.env_utils.scene_exporter._task_data import _TaskDataMixin


class _SceneTasksMixin(_TaskDataMixin):
    """Scene-level export tasks (mirror of mayatk's): the working unit and the
    export-set filters."""

    def set_linear_unit(self, value):
        """Set the scene's unit system + scale for the duration of the export.

        **Staged.** Blender's FBX exporter reads
        ``scene.unit_settings.scale_length`` when it *writes*
        (``apply_unit_scale`` is on by default), so the restore rides
        ``TaskFactory.stage_deferred_restore``: the unit survives the write and
        is undone immediately after it.
        """
        if not value:
            return None
        settings = self._scene().unit_settings
        original = (settings.system, settings.scale_length)

        def restore():
            reverting = self._scene().unit_settings
            reverting.system, reverting.scale_length = original
            self.logger.debug(f"Reverted scene units to {original}.")

        self.stage_deferred_restore("linear_unit", restore)
        system, scale = value
        settings.system = system
        settings.scale_length = scale
        self.logger.debug(f"Changed scene units to {system} (scale_length={scale}).")
        return None

    def exclude_hdr(self) -> None:
        """Remove image-based environment lighting objects from the export set.

        Blender's HDRI normally lives on the World, which is never an object
        and never ships. This covers the two ways one rides on an object -- a
        light whose node tree samples an Environment Texture, and a mesh
        "dome" whose every material's shader is fed by one (the visible-HDR
        sphere) -- and strips them: the Blender analogue of mayatk's
        ``aiSkyDomeLight`` exclusion. Image-based lighting is scene lighting,
        not deliverable geometry, so it should not ride into a game-engine FBX.
        A no-op when the export set carries none. Takes no argument, as
        mayatk's: ``ptk.TaskFactory`` gates a zero-argument task on its
        checkbox (the ``enabled`` flag it once took was retired 2026-09-21).
        """
        if not self.objects:
            return
        excluded = [o for o in self._live_objects() if self._is_environment_object(o)]
        if not excluded:
            self.logger.debug(
                "No HDR environment object in the export set — nothing to exclude."
            )
            return
        names = sorted(o.name for o in excluded)
        self.objects = [o for o in self.objects if o not in excluded]
        self.logger.info(
            f"Excluded {len(excluded)} HDR environment object(s) from export: "
            f"{', '.join(names)}."
        )

    def prepare_lods(self, mode: str = "authored") -> None:
        """Read the export set's LOD groups and ship them as the LODs row asks --
        mirror of mayatk's.

        Blender has no LOD node: a group is an object (an Empty, usually) whose
        children are named ``<base>_LOD0..n`` (``ptk.LodChain``), LOD0 the most
        detailed. Armed on every export by ``ExportRun.from_tasks``;
        ``ptk.LodChain.plan`` decides, this lists the objects and applies it. A
        scene with no level-named object costs one name scan.

        Parameters:
            mode: ``"authored"`` ships each group as one; ``"lod0"`` (Most
                Detailed Only) takes every lower level, and what hangs under
                it, out of the export set -- Blender writes the objects it is
                handed, so they stay out of the file. The scene is never
                touched.
        """
        from blendertk.edit_utils.lods import LodUtils

        objects = self._live_objects()
        if not objects:
            return
        by_path: Dict[str, Any] = {}
        for obj in objects:
            node = obj
            while node is not None:  # each object, and its group node above
                by_path.setdefault(LodUtils._path(node), node)
                node = node.parent
            for child in obj.children_recursive:
                by_path.setdefault(LodUtils._path(child), child)
        if not ptk.LodChain.names_levels(by_path):
            return
        # What the FBX writes: the evaluated mesh, its modifiers applied.
        plan = ptk.LodChain.plan(LodUtils.nodes(by_path), mode)
        self._lod_plan = plan
        for warning in plan["warnings"]:
            self.logger.warning(warning)
        for note in plan["notes"]:
            self.logger.info(note)
        if plan["listing"]:
            self.logger.info(
                f"LOD groups ({len(plan['groups'])}): {'; '.join(plan['listing'])}"
            )
        # Each group's node ships with its levels: a scope that lists meshes
        # alone (Visible) leaves it out, and Blender's writer parents a child
        # whose parent it is not handed to the root -- the file then holds no
        # group. Maya's writer carries a node's ancestors itself.
        shipping = set(self.objects or [])
        for group in plan["groups"]:
            node = by_path.get(group["parent"])
            if node is not None and node not in shipping and any(by_path.get(p) in shipping for p in group["levels"] if p):
                self.objects.append(node)
                shipping.add(node)
        if mode != "lod0":  # authored groups ship as groups in Generate Missing too
            self._mark_lod_groups(plan, by_path)
        if not plan["drop"]:
            return
        gone = set()
        for path in plan["drop"]:
            obj = by_path[path]
            gone.add(obj)
            gone.update(obj.children_recursive)
        self.objects = [o for o in self.objects if o not in gone]
        self.logger.info(
            f"Most Detailed Only: {len(plan['drop'])} lower LOD level(s) left out "
            "of the export."
        )

    @staticmethod
    def _lod_ineligible(obj, named) -> Optional[str]:
        """Why Generate Missing leaves *obj* alone, or None -- mirror of
        mayatk's: a mesh a scene record names, a deforming (an Armature
        modifier, shape keys), instanced (shared mesh data) or moving one (an
        action, NLA strips, drivers or constraints, itself or above), one
        holding other objects, or one too light for a lower level needs
        authored LODs."""
        from blendertk.edit_utils.lods import LodUtils

        name = obj.name
        if name in named or name.rsplit(":", 1)[-1] in named:
            return "named by a scene record"
        return LodUtils.ineligible(obj)

    def generate_lods(self) -> None:
        """Generate Missing (the LODs row): a LOD group for each eligible mesh
        of the export set (``btk.LodUtils.generate``) -- mirror of mayatk's.
        LOD Output (``run.lod_write_back``): Export Copies, the default, makes
        the groups for the write and puts each mesh back after it; Scene (In
        Place) keeps them."""
        from blendertk.edit_utils.lods import LodUtils
        from blendertk.node_utils.data_nodes import DataNodes

        plan = self._lod_plan or {}
        grouped = {p for g in plan.get("groups", []) for p in g["levels"] if p}
        try:
            named = ptk.SceneRecords.named_nodes(
                DataNodes.dump(), skip=(ptk.SceneRecords.LODS.key,)
            )
        except Exception:  # no carrier: nothing names a node
            named = set()
        eligible, skipped = [], {}
        for obj in self._live_objects():
            if obj.type != "MESH" or obj.data is None or ptk.LodChain.parse(obj.name):
                continue
            path = LodUtils._path(obj)
            if any(path == p or path.startswith(p + "|") for p in grouped):
                continue
            reason = self._lod_ineligible(obj, named)
            if reason:
                skipped.setdefault(reason, []).append(obj.name)
                continue
            eligible.append(obj)
        for reason, names in skipped.items():
            self.logger.info(
                f"Generate Missing left {len(names)} mesh(es) alone ({reason}; "
                f"authored LODs needed): {', '.join(sorted(names))}"
            )
        if not eligible:
            return
        keep = self.run.lod_write_back
        originals: List[tuple] = []  # (object, name, parent, parent inverse) per mesh grouped
        groups: List[Any] = []
        if not keep:
            # Staged before the first mesh: a run that stops midway still puts
            # back every mesh it grouped.
            self.stage_deferred_restore(
                "generated_lods", lambda: self._restore_lod_meshes(originals, groups)
            )
        failed: List[str] = []
        for obj in eligible:
            record = (obj, obj.name, obj.parent, obj.matrix_parent_inverse.copy())
            originals.append(record)
            try:
                made_here = LodUtils._generate([obj])  # no undo step: the run puts it back
            except Exception as error:  # noqa: BLE001 -- one mesh never costs the rest
                self._restore_lod_meshes([record], [])
                originals.remove(record)
                failed.append(f"{record[1]} ({error})")
                continue
            if not made_here:
                originals.remove(record)
                continue
            groups.extend(made_here)
        if failed:
            self.logger.warning(
                f"Generate Missing left {len(failed)} mesh(es) as they were (its "
                f"generation failed): {', '.join(sorted(failed))}"
            )
        if not groups:
            return
        gone = {record[0] for record in originals}
        made = [o for g in groups for o in (g, *g.children)]
        self.objects = [o for o in self.objects if o not in gone] + made
        if keep:
            self.record_kept_edit("generated LOD groups")
        self.logger.info(
            f"Generated LOD groups ({len(groups)})"
            + ("" if keep else " -- for the write only")
            + f": {', '.join(g.name for g in groups)}"
        )

    @staticmethod
    def _restore_lod_meshes(originals: List[tuple], groups: List[Any]) -> None:
        """Put each grouped mesh back as it was: its parent and parent inverse
        (its own transform never changed, so its world placement is exact),
        then the groups go with their copies -- a group a failed generation
        left half made too -- then each name back (the group holds it until
        it is gone)."""
        import bpy

        from blendertk.edit_utils.lods import LodUtils

        doomed = list(groups)
        kept = {record[0] for record in originals}
        for obj, _name, parent, inverse in originals:
            try:
                current = obj.parent
                if current is not parent and current is not None and current not in doomed:
                    if current.get(LodUtils.FBX_TYPE) == "LodGroup":
                        doomed.append(current)
                obj.parent = parent
                obj.matrix_parent_inverse = inverse
            except ReferenceError:
                continue
        for group in doomed:
            try:
                for level in list(group.children):
                    if level not in kept:
                        mesh = level.data
                        bpy.data.objects.remove(level, do_unlink=True)
                        if mesh is not None and mesh.users == 0:
                            bpy.data.meshes.remove(mesh)
                bpy.data.objects.remove(group, do_unlink=True)
            except ReferenceError:
                continue
        for obj, name, _parent, _inverse in originals:
            try:
                obj.name = name
            except ReferenceError:
                continue

    def _lod_dropped(self) -> set:
        """Most Detailed Only's lower levels and every object under them."""
        import bpy

        gone = set()
        for path in (self._lod_plan or {}).get("drop") or []:
            obj = bpy.data.objects.get(path.rsplit("|", 1)[-1])
            if obj is not None:
                gone.add(obj)
                gone.update(obj.children_recursive)
        return gone

    def lod_write_selection(self, objects: List[Any]) -> List[Any]:
        """*objects* without the levels :meth:`prepare_lods` left out (and what
        hangs under them) -- mirror of mayatk's: Blender's FBX writer ships the
        objects it is handed, so the selected scope drops them here."""
        gone = self._lod_dropped()
        return [o for o in objects if o not in gone] if gone else list(objects)

    def lod_omitted(self) -> frozenset:
        """The names of the objects the write leaves out though its scope
        holds them -- mirror of mayatk's -- which no record may name
        (``FbxUtils.export_context(omit=)``)."""
        return frozenset(o.name for o in self._lod_dropped())

    def _mark_lod_groups(self, plan: Dict[str, Any], by_path: Dict[str, Any]) -> None:
        """Have the FBX state each valid LOD group as an FbxLODGroup, as Maya's
        writer states a ``lodGroup``: Unreal and Maya read the attribute (Unity
        the names). Blender's exporter writes an Empty's node type from its
        ``fbx_type`` custom property (measured 2026-11-10: ``"LodGroup"``
        writes a LodGroup NodeAttribute, no thresholds), so each group Empty
        carries it for the write and loses it after (staged); an Empty that
        already states a type keeps its own (``LodUtils.FBX_TYPE``, the one
        place the property is named)."""
        from blendertk.edit_utils.lods import LodUtils

        for group in plan["groups"]:
            node = by_path.get(group["parent"])
            if node is None or node.type != "EMPTY":
                continue
            if LodUtils.FBX_TYPE in node.keys():
                continue
            node[LodUtils.FBX_TYPE] = "LodGroup"
            name = node.name

            def unmark(name=name) -> None:
                import bpy

                obj = bpy.data.objects.get(name)
                if obj is not None and LodUtils.FBX_TYPE in obj.keys():
                    del obj[LodUtils.FBX_TYPE]

            self.stage_deferred_restore(f"lod_group_type:{name}", unmark)

    def _state_lod_thresholds(self, fbx_path: str) -> List[str]:
        """Give each LOD group the written FBX holds its switch heights.

        Blender's FBX writer states a group Empty typed ``LodGroup``
        (:meth:`_mark_lod_groups`) with no thresholds (measured 2026-11-10), so
        every reader of the attribute -- Maya, Unreal, our FBX-to-GLB pass --
        switched it at its own defaults. Each group's heights
        (``LodUtils.heights``, for the levels this write ships:
        ``ptk.LodChain.kept_heights``) are written as Maya's writer states them
        (``ptk.FbxMedia.set_lod_thresholds``). Run right after the write,
        before the GLB conversion reads the file. Returns the groups stated.
        """
        import bpy

        from blendertk.edit_utils.lods import LodUtils

        objects = [o for o in (self.objects or []) if o is not None]
        if not ptk.LodChain.names_levels(o.name for o in objects):
            return []
        shipped = {o.name for o in objects}
        empties = {o.name for o in bpy.data.objects if o.type == "EMPTY"}
        heights: Dict[str, List[float]] = {
            entry["name"]: entry["heights"]
            for entry in ptk.LodChain.record_groups(LodUtils.groups(objects), shipped.__contains__, LodUtils._heights_at)
            if entry["name"] in empties
        }
        if not heights:
            return []
        try:
            stated = ptk.FbxMedia.set_lod_thresholds(fbx_path, heights)
        except (OSError, ValueError) as error:
            self.logger.warning(f"LOD groups' switch heights not written to the FBX: {error}")
            return []
        if stated:
            self.logger.info(f"LOD switch heights stated on {len(stated)} FBX LodGroup(s): {', '.join(stated)}.")
        return stated

    @staticmethod
    def _samples_environment(node_tree) -> bool:
        """Does *node_tree* sample an Environment Texture node?"""
        return bool(node_tree) and any(
            node.bl_idname == "ShaderNodeTexEnvironment" for node in node_tree.nodes
        )

    @classmethod
    def _is_environment_object(cls, obj) -> bool:
        """Is *obj* image-based environment lighting (see :meth:`exclude_hdr`)?"""
        from blendertk.mat_utils._mat_utils import MatUtils

        data = getattr(obj, "data", None)
        if obj.type == "LIGHT":
            return cls._samples_environment(MatUtils.get_node_tree(data))
        if obj.type == "MESH":
            materials = [m for m in (getattr(data, "materials", None) or []) if m]
            return bool(materials) and all(
                cls._samples_environment(MatUtils.get_node_tree(m)) for m in materials
            )
        return False

    def ignore_groups(self, names, case_sensitive: bool = False):
        """Remove objects under any top-level object named in the comma-separated
        ``names`` from ``self.objects``.

        Parameters:
            names: Comma-separated object name patterns to exclude (e.g.
                ``"temp, proxy"``). Each entry is a shell-style glob, so
                ``"temp*"`` catches ``temp_01``/``tempRig`` and ``"*_proxy"``
                catches ``hull_proxy``. A pattern with no wildcard character
                still matches only that exact name, as before.
            case_sensitive: Match names exactly. Off by default, so ``"temp"``
                catches ``TEMP``. The UI arms it from the Ignore row's option-box
                toggle; a headless caller passes the pair as the dict the task
                dispatcher unpacks -- ``{"names": "Temp", "case_sensitive": True}``
                -- while a bare string still selects the insensitive default.
        """
        if not names or not str(names).strip() or not self.objects:
            return
        # Parse the patterns here rather than handing ``filter_list`` a raw
        # string: an all-whitespace field must return early, because a filter
        # with no patterns is a no-op that returns the list unfiltered -- here
        # that would mean matching, and so excluding, every root.
        patterns = ptk.split_delimited_string(
            str(names), delimiter=",", strip_whitespace=True, remove_empty=True
        )
        if not patterns:
            return

        import bpy

        from blendertk.node_utils._node_utils import NodeUtils

        # The glob, the case fold and the pattern list all live in
        # ``filter_list``, so the match rules stay identical here and in
        # mayatk's ``ignore_groups``, which this task mirrors.
        roots = [o for o in bpy.data.objects if o.parent is None]
        excluded = set()
        for root in ptk.filter_list(
            roots,
            inc=patterns,
            map_func=lambda o: o.name,
            ignore_case=not case_sensitive,
        ):
            excluded.add(root)
            excluded.update(NodeUtils.get_children(root, recursive=True))
        if excluded:
            before = len(self.objects)
            self.objects = [o for o in self.objects if o not in excluded]
            removed = before - len(self.objects)
            if removed:
                self.logger.debug(
                    f"Excluded {removed} object(s) under ignored group(s): {patterns}."
                )
