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
