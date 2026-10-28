# !/usr/bin/python
# coding=utf-8
"""UV diagnostics -- the Blender counterpart of mayatk's
``core_utils.diagnostics.uv_diag`` (``UvDiagnostics``).

Holds :meth:`UvDiagnostics.is_bakeable_lightmap`, the mirror name of the
reuse-or-regenerate test the lightmap UV stage asks; the check itself is
``UvUtils._is_bakeable_lightmap``, beside ``create_lightmap_uvs`` (uv_utils ranks below
the diagnostics). mayatk answers it with Maya's own ``polyUVOverlap`` /
``polyEvaluate``; Blender has no such query, so the overlap is measured by
``ptk.UvTransfer.layout_overlaps`` over the mesh's loop triangles.
``import bpy`` is never needed: everything is read off the mesh datablock.
"""

# Eager: ``CoreUtils.undoable`` decorates ``cleanup_uv_sets`` while the class body runs.
from blendertk.core_utils._core_utils import CoreUtils
from blendertk.uv_utils._uv_utils import LIGHTMAP_OVERLAP_TOLERANCE, UvUtils


class UvDiagnostics(object):
    """UV layout checks (mirror of ``mtk.UvDiagnostics``)."""

    #: Overlapping texels a layout may carry and still count as clean
    #: (``uv_utils``' ``LIGHTMAP_OVERLAP_TOLERANCE``, where the check lives).
    OVERLAP_TOLERANCE: float = LIGHTMAP_OVERLAP_TOLERANCE

    @classmethod
    def is_bakeable_lightmap(cls, obj, uv_set: str) -> bool:
        """True if *uv_set* is usable as a lightmap: has UVs, non-overlapping, and
        packed within the 0-1 unit square (a single tile).

        Same contract as mayatk's: a layout that passes is REUSED by the lightmap
        UV stage rather than regenerated, so an artist's (or a previous bake's)
        layout survives a re-bake -- and a Maya scene's lightmap set survives the
        bridge's Cycles bake, which writes the layout it baked back into Maya.

        Parameters:
            obj: A mesh object.
            uv_set: The UV layer's name.
        """
        return UvUtils._is_bakeable_lightmap(obj, uv_set, cls.OVERLAP_TOLERANCE)

    @staticmethod
    @CoreUtils.undoable
    def cleanup_uv_sets(
        objects,
        remove_empty=True,
        keep_only_primary=True,
        rename_to_map1=True,
        force_rename=False,
        prefer_largest_area=False,
        dry_run=False,
    ):
        """Clean up the meshes' UV maps -- mirror of mayatk's
        ``Diagnostics.cleanup_uv_sets`` (its defaults): the check is
        ``UvUtils.cleanup_uv_sets``, uv_utils ranking below the diagnostics. One
        undo step. Each result row reads mayatk's field names too (``shape``,
        ``sets_to_delete``).
        """
        return UvUtils.cleanup_uv_sets(
            objects,
            remove_empty=remove_empty,
            keep_only_primary=keep_only_primary,
            rename_to_map1=rename_to_map1,
            force_rename=force_rename,
            prefer_largest_area=prefer_largest_area,
            dry_run=dry_run,
        )
