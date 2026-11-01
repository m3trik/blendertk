# !/usr/bin/python
# coding=utf-8
"""Image Tracer tool — Blender port of mayatk's ``nurbs_utils.image_tracer``.

Trace contours from a raster image into editable curves, then optionally fill them into a mesh.
The cv2 contour-extraction is identical to mayatk's; only the curve/mesh construction differs
(``cmds.curve`` / ``planarSrf`` / ``nurbsToPoly`` / ``projectCurve`` → :class:`~blendertk.
nurbs_utils._nurbs_utils.NurbsUtils` curve build + 2D-fill bake + plane/duplicate primitives).

The panel is tentacle's ``slots/tools/image_tracer.py``: one Slots class for both hosts, over this
class as ``btk.ImageTracer``.

Divergences (documented for parity — see ``tentacle/docs/PARITY_PORTING_PLAN.md``):
  * **One curve object, one cyclic POLY spline per contour** (Maya made one transform per contour).
    Nested contours then become **holes** under Blender's 2D even-odd fill — so ``create_mesh``
    (planarSrf+nurbsToPoly) and ``create_negative_space_mesh`` (boundary rect + contour holes)
    both reduce to setting ``dimensions='2D'`` + ``fill_mode='BOTH'`` and baking.
  * Curves are laid on the **XY ground plane** (Z-up Blender) with image-Y flipped so the trace is
    upright in top view — Maya placed them on XZ (Y-up) as ``(x, 0, y)``.
  * **``project_on_plane`` is a backing-plane + Z-shifted duplicate**, not a true surface
    projection: traced curves are already born planar on Z=0 (Maya's curves are too — both trace
    paths only ever emit flat points), so "projecting" here builds a sized ``NurbsUtils.
    create_plane`` under the curves and a ``NurbsUtils.duplicate_curve`` shifted down onto it —
    a small, real analogue of Maya's ``nurbsPlane`` + ``projectCurve``, useful as a physical
    canvas/backdrop or to flatten a curve supplied with Z variance.
  * **No ``BluePencilMixin`` / Blue Pencil tracing** — Maya's Blue Pencil is an annotation plugin
    with no Blender analogue; a Grease-Pencil-stroke → curve path would need real new architecture
    (deferred — see the porting plan). ``use_blue_pencil`` is accepted (mayatk's signature, which
    the shared panel passes) and refused when True; with no ``open_blue_pencil`` here, the panel
    shows its Use / Open Blue Pencil entries disabled.
  * ``import bpy`` is deferred into the call bodies.
"""

import os

import pythontk as ptk

from blendertk.core_utils._core_utils import CoreUtils
from blendertk.nurbs_utils._nurbs_utils import NurbsUtils

# OpenCV reads OPENCV_IO_ENABLE_OPENEXR once, at import; pythontk's root sets
# it, and this package imports pythontk before any module of its own.
try:
    import cv2
except ImportError:  # Blender ships no cv2; the .venv does — see the dual-mode test.
    cv2 = None


class ImageTracer(ptk.LoggingMixin):
    """Trace a raster image into curves / filled meshes — Blender mirror of mayatk's ``ImageTracer``."""

    def __init__(self, image_path=None, scale=0.1, simplify=1.0, use_blue_pencil=False):
        if use_blue_pencil:  # mayatk's signature; Blender has no Blue Pencil to trace
            raise NotImplementedError(
                "Blue Pencil tracing is Maya's: Blender has no Blue Pencil. Trace an image instead."
            )
        self.image_path = image_path
        self.scale = float(scale)
        self.simplify = simplify

    @staticmethod
    def _contours_from_image(image_path, scale=0.1, simplify=1.0, threshold=127):
        """Pure-cv2 contour extraction (no bpy → unit-testable wherever cv2 lives).

        Reads ``image_path`` → grayscale → binary threshold → ``findContours`` (RETR_TREE, so
        nested holes are kept) → optional ``approxPolyDP`` simplify. Returns a list of contour
        point-lists, each ``[(x, y, 0.0), …]`` scaled and placed on the XY plane with image-Y
        flipped (image origin is top-left → Blender +Y up). Contours with ≤2 points are dropped.
        """
        if cv2 is None:
            raise ImportError("OpenCV (cv2) is required for image tracing.")
        if not image_path or not os.path.exists(image_path):
            raise FileNotFoundError(f"Image not found: {image_path}")
        # Not cv2.imread: on Windows it opens no path that is not ASCII.
        img = ptk.ImgUtils.cv2_read(image_path)
        if img is None:
            raise ValueError(f"Failed to read image: {image_path}")

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        _, thresh = cv2.threshold(gray, threshold, 255, 0)
        contours, _ = cv2.findContours(thresh, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)

        height = img.shape[0]
        result = []
        for contour in contours:
            if simplify:
                contour = cv2.approxPolyDP(contour, simplify, True)
            pts = [
                (float(p[0][0]) * scale, float(height - p[0][1]) * scale, 0.0)
                for p in contour
            ]
            if len(pts) > 2:
                result.append(pts)
        return result

    @CoreUtils.undoable
    def trace_curves(self, name="traced_curve"):
        """Trace the image into ONE curve object — one cyclic POLY spline per contour (so nested
        contours read as holes under 2D fill). Returns the curve object, or ``None`` if no contours.
        """
        contours = self._contours_from_image(self.image_path, self.scale, self.simplify)
        if not contours:
            return None
        obj = NurbsUtils.create_curve(contours[0], name=name, cyclic=True, kind="POLY")
        for pts in contours[1:]:
            NurbsUtils.add_spline(obj, pts, cyclic=True, kind="POLY")
        return obj

    @staticmethod
    def _fill_to_mesh(curve, name):
        """Set a curve to planar 2D even-odd fill and bake it to a mesh (Maya planarSrf+nurbsToPoly).
        Nested splines punch holes automatically. Shared by ``create_mesh`` / ``create_negative_space_mesh``."""
        curve.data.dimensions = "2D"
        curve.data.fill_mode = "BOTH"
        return NurbsUtils.curve_to_mesh(curve, name=name)

    @CoreUtils.undoable
    def create_mesh(self, curve=None, name="traced_mesh"):
        """Fill the traced contours into a mesh (positive space; nested contours become holes).
        Returns the mesh object, or ``None``.
        """
        curve = curve or self.trace_curves(name=name)
        return self._fill_to_mesh(curve, name) if curve is not None else None

    @CoreUtils.undoable
    def create_negative_space_mesh(
        self, curve=None, margin_scale=0.1, name="negative_space_mesh"
    ):
        """Fill the **inverse**: a boundary rectangle (margin-padded bbox) around the contours, with
        the contours as holes — Maya's ``create_negative_space_mesh``. Returns the mesh, or ``None``.
        """
        curve = curve or self.trace_curves(name=name)
        if curve is None:
            return None

        xs, ys = self._curve_point_bounds(curve)
        if not xs:
            return None

        margin = max(max(xs) - min(xs), max(ys) - min(ys)) * margin_scale
        x0, x1 = min(xs) - margin, max(xs) + margin
        y0, y1 = min(ys) - margin, max(ys) + margin
        NurbsUtils.add_spline(
            curve,
            [(x0, y0, 0), (x1, y0, 0), (x1, y1, 0), (x0, y1, 0)],
            cyclic=True,
            kind="POLY",
        )
        return self._fill_to_mesh(curve, name)

    @staticmethod
    def _curve_point_bounds(curve):
        """Flat ``(xs, ys)`` lists of every point in every spline of ``curve`` (world-local, since
        the curve is unrotated/unscaled at creation) — shared bbox helper for the negative-space
        boundary rect and the projection plane sizing."""
        xs, ys = [], []
        for spline in curve.data.splines:
            for p in spline.points:
                xs.append(p.co.x)
                ys.append(p.co.y)
        return xs, ys

    @CoreUtils.undoable
    def project_on_plane(self, curve=None, name="projected_curves"):
        """Project the traced curves onto a construction plane — Blender analogue of Maya's
        ``projectCurve`` onto a ``nurbsPlane``. Traced curves are already planar on Z=0 (Maya's are
        too), so "projecting" here sizes a backing ``NurbsUtils.create_plane`` to the curve's bbox
        and drops a ``NurbsUtils.duplicate_curve`` onto it — a physical canvas/backdrop under the
        curves, or a flattening pass for a curve supplied with Z variance. Returns the duplicate
        curve object, or ``None`` if there are no contours.
        """
        curve = curve or self.trace_curves(name=name)
        if curve is None:
            return None

        xs, ys = self._curve_point_bounds(curve)
        if not xs:
            return None

        width = max(max(xs) - min(xs), 1.0)
        height = max(max(ys) - min(ys), 1.0)
        center_x = (min(xs) + max(xs)) / 2.0
        center_y = (min(ys) + max(ys)) / 2.0

        NurbsUtils.create_plane(
            width=width * 1.5,
            height=height * 1.5,
            location=(center_x, center_y, -1.0),
            name="projection_plane",
        )
        projected = NurbsUtils.duplicate_curve(curve, name=name)
        projected.location.z -= 1.0
        return projected
