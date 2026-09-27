# !/usr/bin/python
# coding=utf-8
"""The scene's hierarchy baseline, stored in the .blend (mirror of mayatk).

ONE baseline per file, on the ``data_internal`` carrier -- it stays with the
.blend and never exports -- beside the other scene-private records kept there.

It used to live in the export's ``.scene_data.json`` sidecar, keyed by the
deliverable's file stem.  That made the baseline a property of the NAME rather
than of the file: renaming the Output Filename -- a prefix, a regex, a literal
name -- pointed the next export at a different sidecar and silently started a
fresh baseline, so the first export after any rename passed no matter what had
changed.  Here there is no key: the file holds its own record, which survives
the output being renamed, the .blend being renamed or moved, and the file being
handed to another machine.

What it must NOT survive is a Save As into another module: the copy carries the
record verbatim, and its first export was diffed against what the SOURCE
exported (mayatk, 2026-09-24).  So the record is stamped with the file that
recorded it (``DataNodes.writer_stamp``), and a record whose writer is another
file still on disk is that file's (``DataNodes.written_here``): set aside,
replaced by this file's own at its first export.  Where its own record holds
nothing of the deliverable being exported, that deliverable's sidecar stands in
(:meth:`HierarchyBaseline.adopt_sidecar`), each deliverable's scope at its own
first export.  Mirror of mayatk.

The set algebra is ``ptk.HierarchyBaseline`` (shared with mayatk); this class is
only its storage and its migration.  Scope is derived at compare time from the
roots being exported, so one record serves every export a file makes.

Note the path sets themselves are NOT closed under ancestors here as they are in
mayatk: Blender's ``use_selection`` export drops unselected parents, so the set
alone is what ships (probe-verified 2026-08-17).  The caller supplies the set
its own export writes; only the algebra is shared.
"""

import os

import pythontk as ptk

from blendertk.node_utils.data_nodes import DataNodes
from blendertk.env_utils.hierarchy_sync.scene_data_sidecar import SceneDataSidecar


class HierarchyBaseline(ptk.HierarchyBaselineStore):
    """Read, compare and roll forward the file's hierarchy baseline.

    The storage (``read`` / ``inherited_from`` / ``is_unreadable`` /
    ``compare`` / ``write`` / ``adopt_sidecar``) is
    :class:`pythontk.HierarchyBaselineStore`'s, shared with mayatk; this class
    supplies Blender's scene store and sidecar.  A recorded set is read back
    as-is (the base ``_close``): ``use_selection`` ships exactly the set.
    """

    STORE = DataNodes
    SIDECAR = SceneDataSidecar

    #: The sidecar names the baseline used to live under, per export stem --
    #: the current one and the v1 spelling, because a scene that never
    #: re-exported since v1 still has its history under the old name and must
    #: not lose it on the way in.
    _LEGACY_SUFFIXES = (".scene_data.json", ".hierarchy.json")

    @classmethod
    @ptk.Deprecation.symbol(
        "HierarchyBaseline.adopt_sidecar", remove_in="0.13.0", since="2026-09-24"
    )
    def migrate_from_sidecar(cls, export_dir: str) -> int:
        """Adopt every on-disk baseline in *export_dir* into the file, once.

        Superseded by :meth:`adopt_sidecar`, which adopts only the deliverable
        being exported: a folder several modules export into holds the other
        modules' histories too.  Runs only when the file has no record of its
        own.  Returns the number of sidecars adopted.
        """
        if cls.read():
            return 0
        try:
            names = [
                n
                for n in os.listdir(export_dir)
                if n.startswith(".") and n.endswith(cls._LEGACY_SUFFIXES)
            ]
        except OSError:
            return 0

        adopted, paths = 0, set()
        for name in names:
            record = ptk.FileUtils.read_json(os.path.join(export_dir, name))
            if not isinstance(record, dict):
                continue
            section = record.get("hierarchy")
            if not isinstance(section, dict):
                # v1 sidecars are flat: the paths sit at the top level.
                section = record
            found = section.get("paths")
            if isinstance(found, list):
                paths.update(p for p in found if isinstance(p, str))
                adopted += 1
        if paths:
            cls._save(paths)
        return adopted
