# !/usr/bin/python
# coding=utf-8
"""BlenderSceneFiles -- the ``blender`` operation host: calls in a Blender the tooling starts.

blendertk's runner for ``python -m pythontk ops`` and every other front end of
``ptk.OperationCatalog``: an operation declared with ``host="blender"`` (see
``blendertk/operations.py``) runs in a NEW ``blender --background
--factory-startup`` per call, or, for a front end that lives long (the MCP
server), in the one such Blender ``ptk.SessionOperationHost`` keeps for its
session; never in a Blender a person has open (session safety by
construction, as for ``BlenderConnection``).
:meth:`BlenderSceneFiles.operation_host` is the ``ptk.BatchOperationHost``
registered under the ``pythontk.operation_hosts`` entry points; the class
itself is the ``ptk.SceneFiles`` that runner builds inside Blender: it opens
the request's .blend and saves it when a finished live run changed it.
Mirror of mayatk's ``MayaSceneFiles``.
"""

from typing import List, Sequence

import pythontk as ptk


class BlenderSceneFiles:
    """A batch run's .blend, inside a fresh Blender (see the module docstring)."""

    #: Whether what this Blender holds has changes no file has: the kept
    #: instance's own ledger. Not read from ``bpy.data.is_dirty``: in background
    #: Blender (5.1, measured) a data-API edit never sets that flag and a save
    #: never clears it once set, so it can say neither (a person's GUI Blender
    #: is answered from the flag and the scene's content instead:
    #: ``EnvUtils.scene_has_unsaved_changes``). A live change sets it
    #: (``changed``); a save of the open file or a load, the code's own
    #: included, clears it; a copy saved elsewhere does not.
    _held = False

    def __init__(self) -> None:
        import bpy
        from bpy.app.handlers import persistent

        for handlers, handler in (
            (bpy.app.handlers.save_post, BlenderSceneFiles._saved),
            (bpy.app.handlers.load_post, BlenderSceneFiles._loaded),
        ):
            if handler not in handlers:  # once per Blender, however often built
                handlers.append(persistent(handler))

    @staticmethod
    def _saved(path: str = "", *_args) -> None:
        """``save_post``: the open file saved where it is clears the ledger."""
        import os

        import bpy

        def key(name: str) -> str:
            return os.path.normcase(os.path.abspath(name)) if name else ""

        if not path or key(path) == key(bpy.data.filepath):
            BlenderSceneFiles._held = False

    @staticmethod
    def _loaded(*_args) -> None:
        """``load_post``: a file opened replaces what was held."""
        BlenderSceneFiles._held = False

    @classmethod
    def operation_host(cls) -> "ptk.BatchOperationHost":
        """The ``blender`` runner: a fresh background Blender per call
        (``pythontk.operation_hosts``)."""
        from blendertk.env_utils.blender_connection import BlenderConnection

        return ptk.BatchOperationHost(
            BlenderConnection.find_blender,
            scene_files="blendertk:BlenderSceneFiles",
            launch_args=lambda script: ["--background", "--factory-startup", "--python", script],
            packages=("blendertk",),
        )

    def open(self, paths: Sequence[str]) -> None:
        """Open the one .blend *paths* names (Blender holds one file at a time).

        Raises:
            ValueError: More than one file was asked for.
        """
        import bpy

        if len(paths) > 1:
            raise ValueError(f"Blender opens one .blend per run; the request names {len(paths)}")
        for path in paths:
            bpy.ops.wm.open_mainfile(filepath=path)

    def modified(self) -> List[str]:
        """The open .blend, when it has unsaved changes."""
        import bpy

        path = bpy.data.filepath
        return [path] if path and self._held else []

    def unsaved(self) -> bool:
        """Whether the open .blend (a new one included) has changes no file has."""
        return self._held

    def changed(self) -> None:
        """Mark what this Blender holds changed (``ptk.SceneFiles``): before a
        live run of code, after one whose journal names a change."""
        BlenderSceneFiles._held = True

    def save(self, paths: Sequence[str]) -> None:
        """Save the open .blend where it is (Blender keeps its ``.blend1`` backup)."""
        import bpy

        if paths:
            bpy.ops.wm.save_mainfile()

    def close(self) -> None:
        """Nothing to release: Blender exits when the script returns."""
