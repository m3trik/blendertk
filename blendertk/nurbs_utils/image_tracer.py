# !/usr/bin/python
# coding=utf-8
"""Image Tracer tool — Blender port of mayatk's ``nurbs_utils.image_tracer``.

Trace contours from a raster image into editable curves, then optionally fill them into a mesh.
The cv2 contour-extraction is identical to mayatk's; only the curve/mesh construction differs
(``cmds.curve`` / ``planarSrf`` / ``nurbsToPoly`` → :class:`~blendertk.nurbs_utils._nurbs_utils.
NurbsUtils` curve build + 2D-fill bake).

Divergences (documented for parity — see ``tentacle/docs/PARITY_PORTING_PLAN.md``):
  * **One curve object, one cyclic POLY spline per contour** (Maya made one transform per contour).
    Nested contours then become **holes** under Blender's 2D even-odd fill — so ``create_mesh``
    (planarSrf+nurbsToPoly) and ``create_negative_space_mesh`` (boundary rect + contour holes)
    both reduce to setting ``dimensions='2D'`` + ``fill_mode='BOTH'`` and baking.
  * Curves are laid on the **XY ground plane** (Z-up Blender) with image-Y flipped so the trace is
    upright in top view — Maya placed them on XZ (Y-up) as ``(x, 0, y)``.
  * **No ``project_on_plane``** (Maya projected curves onto a NURBS plane; Blender curves are born
    planar on Z=0, so it is vestigial) — the ``b005`` button is hidden.
  * **No ``BluePencilMixin``** — Maya Blue Pencil has no Blender analogue; Grease-Pencil-stroke →
    curve is a deferred opt-in (see the porting plan), so the Blue-Pencil header widgets are dropped.
  * ``import bpy`` is deferred into the call bodies; the Qt-only ``uitk`` ``fmt`` into ``header_init``.
"""
import os

import pythontk as ptk

from blendertk.nurbs_utils._nurbs_utils import NurbsUtils

try:
    import cv2
except ImportError:  # Blender ships no cv2; the .venv does — see the dual-mode test.
    cv2 = None


class ImageTracer(ptk.LoggingMixin):
    """Trace a raster image into curves / filled meshes — Blender mirror of mayatk's ``ImageTracer``."""

    def __init__(self, image_path=None, scale=0.1, simplify=1.0):
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
        img = cv2.imread(image_path)
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
                (float(p[0][0]) * scale, float(height - p[0][1]) * scale, 0.0) for p in contour
            ]
            if len(pts) > 2:
                result.append(pts)
        return result

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

    def create_mesh(self, curve=None, name="traced_mesh"):
        """Fill the traced contours into a mesh (positive space; nested contours become holes).
        Returns the mesh object, or ``None``.
        """
        curve = curve or self.trace_curves(name=name)
        return self._fill_to_mesh(curve, name) if curve is not None else None

    def create_negative_space_mesh(self, curve=None, margin_scale=0.1, name="negative_space_mesh"):
        """Fill the **inverse**: a boundary rectangle (margin-padded bbox) around the contours, with
        the contours as holes — Maya's ``create_negative_space_mesh``. Returns the mesh, or ``None``.
        """
        curve = curve or self.trace_curves(name=name)
        if curve is None:
            return None

        xs, ys = [], []
        for spline in curve.data.splines:
            for p in spline.points:
                xs.append(p.co.x)
                ys.append(p.co.y)
        if not xs:
            return None

        margin = max(max(xs) - min(xs), max(ys) - min(ys)) * margin_scale
        x0, x1 = min(xs) - margin, max(xs) + margin
        y0, y1 = min(ys) - margin, max(ys) + margin
        NurbsUtils.add_spline(
            curve, [(x0, y0, 0), (x1, y0, 0), (x1, y1, 0), (x0, y1, 0)], cyclic=True, kind="POLY"
        )
        return self._fill_to_mesh(curve, name)


class ImageTracerSlots(ptk.LoggingMixin):
    """Switchboard slot wiring for the co-located ``image_tracer.ui`` (mirror of mayatk's
    ``ImageTracerSlots``). Discovered + served by ``BlenderUiHandler``
    (``marking_menu.show("image_tracer")``).
    """

    IMAGE_FILTER = "Images (*.png *.jpg *.jpeg *.bmp *.tif *.tiff);;All Files (*.*)"

    def __init__(self, switchboard, log_level="WARNING"):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.image_tracer
        self.logger.setLevel(log_level)
        self.logger.set_log_prefix("[image_tracer] ")

    def header_init(self, widget):
        """Configure header help text."""
        from uitk.widgets.mixins.tooltip_mixin import fmt

        widget.set_help_text(
            fmt(
                title="Image Tracer",
                body="Trace contours from a raster image into editable curves, then optionally fill "
                "them into a mesh. Nested contours become holes automatically (Blender's 2D fill).",
                steps=[
                    "Browse (▸ on the path field) to an image file.",
                    "Set <b>Scale</b> (world size per pixel) and <b>Simplify</b> (contour epsilon; "
                    "0 = keep every point).",
                    "Press <b>Trace Curves</b>, or <b>Create Mesh</b> / <b>Create Negative Space</b> "
                    "to fill the contours directly.",
                ],
                notes=[
                    "Curves are laid flat on the ground (XY) plane and can be beveled, extruded, or "
                    "used as construction drivers like any other curve.",
                ],
            )
        )

    def txt000_init(self, widget):
        """Configure the path field's option box (▸) as an image file browser."""
        widget.option_box.browse(
            file_types=self.IMAGE_FILTER, title="Select Image", mode="file"
        )

    def b005_init(self, widget):
        """Project on Plane is vestigial in Blender (curves are born planar on Z=0) → hide it."""
        widget.setVisible(False)

    def _tracer(self):
        """Build an ``ImageTracer`` from the UI fields, or ``None`` (with a message) when invalid."""
        path = self.ui.txt000.text()
        if not path:
            self.sb.message_box("Select an image first (browse via the ▸ on the path field).")
            return None
        if cv2 is None:
            self.sb.message_box("OpenCV (cv2) is not installed in this Blender's Python.")
            return None
        simplify = self.ui.s001.value()
        return ImageTracer(
            path, scale=self.ui.s000.value(), simplify=simplify if simplify > 0 else None
        )

    def b002(self):
        """Trace Curves."""
        tracer = self._tracer()
        if not tracer:
            return
        curve = tracer.trace_curves()
        self.sb.message_box(
            f"<hl>Traced {len(curve.data.splines)} contour(s).</hl>" if curve
            else "No contours found (try a lower threshold image or different Simplify)."
        )

    def b003(self):
        """Create Mesh (filled contours, nested = holes)."""
        tracer = self._tracer()
        if not tracer:
            return
        mesh = tracer.create_mesh()
        self.sb.message_box(
            "<hl>Created filled mesh.</hl>" if mesh else "No contours found."
        )

    def b004(self):
        """Create Negative Space (boundary rectangle with contour holes)."""
        tracer = self._tracer()
        if not tracer:
            return
        mesh = tracer.create_negative_space_mesh()
        self.sb.message_box(
            "<hl>Created negative-space mesh.</hl>" if mesh else "No contours found."
        )


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("image_tracer", reload=True)
    ui.show(pos="screen", app_exec=True)
