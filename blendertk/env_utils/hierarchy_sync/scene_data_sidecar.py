# !/usr/bin/python
# coding=utf-8
"""Scene-data sidecar -- Blender's host adapter for ``ptk.SceneDataSidecarBase``.

The sidecar's file format, naming, migration, manifest I/O, diff report and
comparison are pythontk's (``core_utils/engines/scene_export/
scene_data_sidecar.py``), shared with mayatk; see that module for the v3
format.  The one scene read here is :meth:`BlenderSidecarHost.expand_to_descendants`,
which walks a live object's subtree via ``children_recursive`` (mayatk's uses
``cmds.listRelatives``).

The rest of :class:`BlenderSidecarHost` (a :class:`pythontk.SidecarHost`) is
the pure host's: its paths carry no namespace to strip
(``_hierarchy_sync.build_path``), nothing is construction data, and a recorded
set is NOT closed under ancestors -- Blender's ``use_selection`` export drops
unselected parents (probe-verified, Blender 5.1), so the export set alone IS
what ships and a bare group entry is a real difference.
:class:`SceneDataSidecar` is the shared class bound to it: configuration only.
"""

import pythontk as ptk


class BlenderSidecarHost(ptk.PureSidecarHost):
    """What the scene-data sidecar asks of Blender (:class:`pythontk.SidecarHost`):
    the descendant walk; the rest is the pure host's."""

    @staticmethod
    def expand_to_descendants(objects) -> list:
        """Return hierarchy paths for *objects* plus all their descendants.

        Mirror of mayatk's version, which used ``cmds.listRelatives(allDescendents=True)``
        on DAG path strings; here *objects* are live ``bpy.types.Object`` references and
        descendants come from Blender's own ``children_recursive``.
        """
        from blendertk.env_utils.hierarchy_sync._hierarchy_sync import HierarchySync

        all_paths = []
        for obj in objects:
            all_paths.append(HierarchySync.build_path(obj))
            for descendant in obj.children_recursive:
                all_paths.append(HierarchySync.build_path(descendant))
        return all_paths


class SceneDataSidecar(ptk.SceneDataSidecarBase):
    """The scene-data sidecar stored alongside Blender exports.

    :class:`pythontk.SceneDataSidecarBase` bound to :class:`BlenderSidecarHost`
    (one sidecar per export stem, ``base_stem=True`` for a versioned series).
    """

    HOST = BlenderSidecarHost()
