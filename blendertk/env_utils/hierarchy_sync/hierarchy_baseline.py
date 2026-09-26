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
from typing import List, Optional, Sequence, Set, Tuple

import pythontk as ptk

from blendertk.node_utils.data_nodes import DataNodes
from blendertk.env_utils.hierarchy_sync.scene_data_sidecar import SceneDataSidecar


class HierarchyBaseline:
    """Read, compare and roll forward the file's hierarchy baseline."""

    #: Channel on ``data_internal`` holding the record: the key of
    #: ``ptk.SceneRecords.HIERARCHY_BASELINE``, which every read and write
    #: goes through.
    ATTR_NAME = ptk.SceneRecords.HIERARCHY_BASELINE.key

    @classmethod
    def read(cls) -> Set[str]:
        """Every path the file has recorded, across all scopes.

        Empty when there is no record, the channel is unreadable, the schema is
        not recognised, or the record is not this file's own
        (:meth:`inherited_from`) -- all of which mean the same thing to a
        caller: nothing to diff against.
        """
        try:
            record, own = cls._record()
            return ptk.HierarchyBaseline.decode(record) if own else set()
        except Exception:  # a check must never break the file it inspects
            return set()

    @classmethod
    def inherited_from(cls) -> Optional[str]:
        """Who recorded the baseline this file holds but does not own.

        The record's writer stamp when it names another file still on disk --
        the source of a Save As copy -- and ``""`` when it names none (recorded
        before records were stamped, or while unsaved).  ``None`` when the
        record is this file's own, or there is no readable record.  Mirror of
        mayatk's.
        """
        try:
            record, own = cls._record()
            if own or not ptk.HierarchyBaseline.is_record(record):
                return None
            return ptk.HierarchyBaseline.recorded_by(record) or ""
        except Exception:  # a check must never break the file it inspects
            return None

    @classmethod
    def _record(cls) -> Tuple[object, bool]:
        """``(record, own)``: the stored record, and whether this file owns it
        (``DataNodes.written_here`` of its writer stamp)."""
        record = ptk.SceneRecords.HIERARCHY_BASELINE.load(DataNodes)
        stamp = ptk.HierarchyBaseline.recorded_by(record)
        return record, DataNodes.written_here(stamp)

    @classmethod
    def is_unreadable(cls) -> bool:
        """The channel holds something, but no baseline could be read from it.

        "No record" and "a record nothing can be read from" both leave the check
        with nothing to diff, but they are not the same event: the second means
        a baseline was LOST, and the file should be told rather than quietly
        given a fresh one. Same rule the sidecar-era check applied to an
        unreadable manifest.
        """
        try:
            raw = ptk.SceneRecords.HIERARCHY_BASELINE.read_text(DataNodes)
        except Exception:
            return False
        # is_record, not read(): a valid record that happens to hold no paths
        # decodes to an empty set exactly as a corrupt one does, and calling
        # that "unreadable" would warn about a baseline nothing had lost.
        return bool(raw) and not ptk.HierarchyBaseline.is_record(raw)

    @classmethod
    def compare(
        cls, current_paths: Set[str], roots: Optional[Sequence[str]] = None
    ) -> Tuple[bool, List[str], List[str], bool]:
        """Diff *current_paths* against the baseline, scoped to what is exporting.

        Returns ``(match, missing, extra, is_new_scope)`` -- see
        :meth:`pythontk.HierarchyBaseline.compare`.
        """
        return ptk.HierarchyBaseline.compare(cls.read(), current_paths, roots)

    @classmethod
    def write(
        cls, current_paths: Set[str], roots: Optional[Sequence[str]] = None
    ) -> bool:
        """Roll the exported scope forward, leaving every other scope intact.

        Stamped with this file (``DataNodes.writer_stamp``).  A record the
        file does not own reads empty, so it is replaced rather than merged: a
        Save As copy's first export starts the copy's own record.

        Returns True when the record was written.  Never raises: a baseline the
        file could not record must not fail the export that produced it -- the
        caller warns instead, because a silently stale baseline is what corrupts
        the NEXT run's diff.
        """
        try:
            merged = ptk.HierarchyBaseline.merge(cls.read(), current_paths, roots)
            if not merged:
                # Nothing to record. Writing an empty record would create a
                # channel that says "baseline, no paths" -- indistinguishable
                # from a real one to every reader, and pointless to keep.
                return True
            cls._save(merged)
            return True
        except Exception:
            return False

    @classmethod
    def _save(cls, paths: Set[str]) -> None:
        """Store *paths* as this file's own record."""
        ptk.SceneRecords.HIERARCHY_BASELINE.save(
            DataNodes,
            ptk.HierarchyBaseline.encode(paths, scene=DataNodes.writer_stamp()),
        )

    @classmethod
    def adopt_sidecar(cls, export_path: str, *, base_stem: bool = False) -> bool:
        """Give the file what *export_path* last shipped, where its own
        baseline holds nothing of that deliverable.

        The deliverable's ``.scene_data.json`` sidecar records the hierarchy
        its last export shipped -- THIS deliverable's only: every module of a
        production can export into one folder, and another deliverable's
        sidecar there is another module's history.  Brought up to the current
        naming first (``SceneDataSidecar.migrate_legacy``), never deleted.
        Per scope (``ptk.HierarchyBaseline.adopt``): beside what the record
        holds of other deliverables, never over a scope it already holds.
        Mirror of mayatk's.

        Parameters:
            export_path: The deliverable being exported.
            base_stem: The Output Filename carries a version counter, so every
                version of the deliverable shares one sidecar.

        Returns:
            bool: True when a sidecar was adopted.
        """
        try:
            SceneDataSidecar.migrate_legacy(export_path, base_stem=base_stem)
            adopted = ptk.HierarchyBaseline.adopt(
                cls.read(),
                SceneDataSidecar.read_manifest(export_path, base_stem=base_stem),
            )
            if adopted is None:
                return False
            cls._save(adopted)
            return True
        except Exception:  # a check must never break the file it inspects
            return False

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
