# !/usr/bin/python
# coding=utf-8
"""Scene-level export tasks (mirror of mayatk's): the working unit and the
export-set filters.
"""

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

    def exclude_hdr(self, *legacy_enabled: bool, enabled: bool = True) -> None:
        """Remove image-based environment lighting objects from the export set.

        Blender's HDRI normally lives on the World, which is never an object
        and never ships. This covers the two ways one rides on an object -- a
        light whose node tree samples an Environment Texture, and a mesh
        "dome" whose every material's shader is fed by one (the visible-HDR
        sphere) -- and strips them: the Blender analogue of mayatk's
        ``aiSkyDomeLight`` exclusion. Image-based lighting is scene lighting,
        not deliverable geometry, so it should not ride into a game-engine FBX.
        A no-op when the export set carries none.

        Parameters:
            *legacy_enabled: Deprecated, removed next release: the released
                ``exclude_hdr(enabled)`` call, where a falsy flag skips the
                exclusion. It takes no argument now, as mayatk's. Variadic, with
                *enabled* keyword-only, because ``ptk.TaskFactory`` gates a task
                on its checkbox only while it takes nothing positional; a named
                parameter would be handed the checkbox value instead.
            enabled: Deprecated, removed next release: the same flag by keyword.
        """
        if not enabled or (legacy_enabled and not legacy_enabled[0]):
            return
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

    @staticmethod
    def _samples_environment(node_tree) -> bool:
        """Does *node_tree* sample an Environment Texture node?"""
        return bool(node_tree) and any(
            node.bl_idname == "ShaderNodeTexEnvironment" for node in node_tree.nodes
        )

    @classmethod
    def _is_environment_object(cls, obj) -> bool:
        """Is *obj* image-based environment lighting (see :meth:`exclude_hdr`)?"""
        data = getattr(obj, "data", None)
        if obj.type == "LIGHT":
            return bool(getattr(data, "use_nodes", False)) and cls._samples_environment(
                getattr(data, "node_tree", None)
            )
        if obj.type == "MESH":
            materials = [m for m in (getattr(data, "materials", None) or []) if m]
            return bool(materials) and all(
                m.use_nodes and cls._samples_environment(m.node_tree) for m in materials
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
