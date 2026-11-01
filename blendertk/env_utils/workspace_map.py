# !/usr/bin/python
# coding=utf-8
"""Workspace Map -- Blender's host adapter for ``ptk.WorkspaceMapBase``.

The scan, its cache, each workspace's measure and the folder -> workspace tree
are pythontk's (``file_utils/workspace_map.py``), shared with mayatk. What
stays here is what Blender calls a workspace and its scenes, as
:class:`BlenderWorkspaceMapHost` (a :class:`pythontk.WorkspaceMapHost`):

- ``current_workspace`` -- the session's workspace root (``EnvUtils.workspace_root``:
  the pin, else the open file's), '' with neither: unlike Maya, Blender has no
  default project, and a whole personal folder is no project to scan;
- ``find_workspaces`` -- folders directly holding .blend files and the marked
  folders holding one (``EnvUtils.find_workspaces``);
- ``scene_files`` -- the .blend files a workspace holds: under it, stopping at the
  workspaces nested in it (each is its own row), its scene-rule folder included
  when that lies outside it (a shared project's ``scenes/``);
- ``create_workspace`` / ``promote_workspace`` -- ``EnvUtils``'.

:class:`WorkspaceMap` is the shared class bound to it: configuration only, the
mirror of mayatk's. Its panel is the map of tentacle's Workspaces tool
(``slots/tools/workspaces.py``).
"""

import os
from typing import Iterator, List, Optional

import pythontk as ptk

from blendertk.env_utils._env_utils import EnvUtils


class BlenderWorkspaceMapHost:
    """What the Workspace Map asks of Blender (:class:`pythontk.WorkspaceMapHost`)."""

    @staticmethod
    def current_workspace() -> str:
        """The session's workspace root (the pin, else the open file's), or ''."""
        try:
            root = EnvUtils.workspace_root()
        except Exception:  # noqa: BLE001 - no Blender (the Qt-only harness)
            root = ""
        return os.path.normpath(root) if root and os.path.isdir(root) else ""

    @classmethod
    def find_workspaces(cls, root_dir: str, recursive: bool) -> List[str]:
        """The workspaces under *root_dir* holding a .blend file. Discovery lists an
        unmarked folder only for the .blend it holds directly, so only a marked
        one is checked, and the check stops at its first scene."""
        return [
            path
            for path in EnvUtils.find_workspaces(root_dir, recursive=recursive)
            if not ptk.Workspace.load(path).is_marked
            or next(cls._scenes(path), None) is not None
        ]

    @classmethod
    def scene_files(cls, workspace: str) -> List[str]:
        """The .blend files *workspace* holds (sorted, see :meth:`_scenes`)."""
        return sorted(cls._scenes(workspace))

    @staticmethod
    def _scenes(workspace: str) -> Iterator[str]:
        """Yield the .blend files *workspace* holds, in one walk.

        Its own, not its nested workspaces' (each is a row of its own, and a
        scene counted twice would read as two): the walk stops at a nested
        marked project, and in a folder that is no project, at a folder directly
        holding a .blend (Blender's unmarked workspace). The scene-rule folder
        follows when it lies outside the workspace.
        """
        root = os.path.normpath(workspace)
        if not os.path.isdir(root):
            return
        marked = ptk.Workspace.load(root).is_marked
        marker = ptk.WORKSPACE_MARKER.lower()
        seen = set()
        for dirpath, dirnames, filenames in os.walk(root):
            names = [f for f in filenames if f.lower().endswith(".blend")]
            if dirpath != root and (
                marker in (f.lower() for f in filenames) or (names and not marked)
            ):
                dirnames[:] = []
                continue
            for name in names:
                path = os.path.join(dirpath, name)
                seen.add(os.path.normcase(path))
                yield path
        scene_dir = EnvUtils.workspace_scenes_dir(root)
        if scene_dir:
            for path in EnvUtils.find_blend_files(scene_dir, recursive=True):
                if os.path.normcase(path) not in seen:
                    yield path

    @staticmethod
    def create_workspace(root: str) -> Optional[ptk.Workspace]:
        """A project at *root*, built from the active template."""
        return EnvUtils.create_workspace(root)

    @staticmethod
    def promote_workspace(root: str) -> Optional[ptk.Workspace]:
        """*root* marked by the layout it has (.blend files at the root)."""
        return EnvUtils.promote_workspace(root)


class WorkspaceMap(ptk.WorkspaceMapBase, ptk.HelpMixin):
    """Blender's workspaces under a root folder, as a tree.

    :class:`pythontk.WorkspaceMapBase` bound to :class:`BlenderWorkspaceMapHost`.
    Mirror of mayatk's ``WorkspaceMap``.
    """

    HOST = BlenderWorkspaceMapHost()
