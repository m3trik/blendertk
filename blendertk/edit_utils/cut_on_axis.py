# !/usr/bin/python
# coding=utf-8
"""Cut On Axis: slice meshes along an axis -- mirror of mayatk's
``edit_utils.cut_on_axis.CutOnAxis`` (name + behavior).

The geometry is the general ``EditUtils.cut_along_axis`` (bmesh ``bisect_plane``:
N slices centered on the pivot, optional delete of the signed-axis half,
optional mirror/symmetrize of the survivor); this class is the API tentacle's
Cut On Axis panel calls, as it calls mayatk's. ``pivot="manip"`` resolves to
Blender's active Transform Pivot Point setting (3D cursor / bounding-box center
/ else the object's own origin), as Mirror's does.
"""

from blendertk.edit_utils._edit_utils import EditUtils


class CutOnAxis:
    # No undo bracket of its own, unlike Maya's: the panel runs it under
    # Blender's Preview, which re-runs it on every refresh and pushes one undo
    # step at the commit; a bracket here would push one per refresh.
    @staticmethod
    def perform_cut_on_axis(
        objects,
        axis="-x",
        cuts=0,
        cut_offset=0,
        cut_spacing=0.0,
        distribution="linear",
        weight_bias=0.5,
        weight_curve=2.0,
        delete=False,
        mirror=False,
        pivot="manip",
        axis_frame=None,
    ):
        """Cut each mesh along *axis*; with *delete*, discard the cut half
        (and with *mirror*, rebuild it from the survivor).

        Parameters:
            objects (list): The mesh objects to cut.
            axis (str): ``'x'``, ``'-x'``, ``'y'``, ``'-y'``, ``'z'`` or ``'-z'``.
            cuts (int): The number of cuts; ``0`` cuts nothing.
            cut_offset (float): Offset from the pivot along the signed axis.
            cut_spacing (float): Distance between adjacent cuts; ``0`` fills
                the axis evenly.
            distribution (str): Progression curve distributing the cuts across
                the span (``ptk.ProgressionCurves`` modes).
            weight_bias (float): Bias for the ``"weighted"`` distribution.
            weight_curve (float): Curve strength for non-linear distributions.
            delete (bool): Delete the faces on the axis side beyond the cuts.
            mirror (bool): After deleting, mirror the surviving half.
            pivot (str): ``"manip"``, ``"object"``, ``"world"`` or ``"center"``.
            axis_frame (str): Which axes to cut along -- ``None`` derives it
                from the pivot (see ``EditUtils.cut_along_axis``).
        """
        if not cuts:
            return
        EditUtils.cut_along_axis(
            objects,
            axis=axis.lower(),
            pivot=pivot,
            amount=cuts,
            offset=cut_offset,
            spacing=cut_spacing,
            distribution=distribution,
            weight_bias=weight_bias,
            weight_curve=weight_curve,
            delete=delete,
            mirror=mirror,
            axis_frame=axis_frame,
        )
