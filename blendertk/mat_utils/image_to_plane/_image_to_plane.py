# !/usr/bin/python
# coding=utf-8
"""Map image files to textured planes in Blender — port of mayatk's ``mat_utils.image_to_plane``.

``ImageToPlane`` creates one plane per image, sized to the source aspect ratio, with a Principled-BSDF
material wired to the image (alpha → Principled Alpha when the image carries an alpha channel).

Divergence from mayatk (documented for parity bookkeeping — see
``tentacle/docs/PARITY_PORTING_PLAN.md``): Maya offers Stingray PBS / standardSurface / lambert
shader types; Blender has the single Principled BSDF, so ``mat_type`` is accepted for API parity but
always builds a Principled material. Planes are built **upright in the XZ plane** (facing the front
view) rather than driven by Maya's ``axis`` normal vector. ``import bpy`` is deferred into the call
bodies.
"""
import os

import pythontk as ptk


class ImageToPlane(ptk.LoggingMixin):
    """Create textured planes from image files (mirror of mayatk's ``ImageToPlane``).

    All public methods are class-level — no instance state required. Each image produces a plane
    whose width/height matches the source pixel ratio plus a Principled material with the image as
    Base Color (and Alpha when present). Planes are created at the world origin.
    """

    @classmethod
    def create(
        cls,
        image_paths,
        mat_type="standard",
        suffix="_MAT",
        prefix="",
        plane_height=10.0,
        group=False,
        group_name="imagePlanes_GRP",
    ):
        """Create textured planes for one or more images.

        Parameters:
            image_paths (list): Paths to image files.
            mat_type (str): Accepted for mayatk API parity; Blender always builds a Principled material.
            suffix (str): Appended to the image stem for material naming.
            prefix (str): Prepended to the image stem for material naming.
            plane_height (float): Plane height in scene units (width = height × image aspect).
            group (bool): Parent all created planes under a single Empty.
            group_name (str): Name of that Empty when ``group`` is True.

        Returns:
            dict: ``{image_stem: plane_object, ...}`` (plus ``"__group__"`` when grouped).
        """
        results = {}
        for path in image_paths:
            path = os.path.normpath(path)
            if not os.path.isfile(path):
                cls.logger.warning(f"Image not found: {path}")
                continue
            try:
                plane = cls._create_single(path, mat_type, suffix, prefix, plane_height)
                results[os.path.splitext(os.path.basename(path))[0]] = plane
            except Exception:
                cls.logger.error(f"Failed to create plane for {path}", exc_info=True)

        if group and results:
            results["__group__"] = cls._group(list(results.values()), group_name)
        return results

    @classmethod
    def remove(cls, objects=None):
        """Remove planes and their auto-created materials/images (orphans only) — mirror of
        mayatk's ``ImageToPlane.remove``. ``None`` → the current selection. Returns the count removed."""
        import bpy
        from blendertk.core_utils._core_utils import selected_objects

        if objects is None:
            objects = list(selected_objects())
        count = 0
        for obj in list(objects):
            if obj is None or obj.name not in bpy.data.objects:
                continue
            mats = [ms.material for ms in obj.material_slots if ms.material]
            imgs = []
            for mat in mats:
                if mat.use_nodes:
                    imgs += [
                        n.image for n in mat.node_tree.nodes
                        if n.type == "TEX_IMAGE" and n.image
                    ]
            mesh = obj.data if obj.type == "MESH" else None
            bpy.data.objects.remove(obj, do_unlink=True)
            # The mesh datablock outlives the object and still references the material, so purge
            # it first — otherwise mat.users never reaches 0 and the material/image leak.
            if mesh is not None and mesh.users == 0:
                bpy.data.meshes.remove(mesh)
            for mat in mats:
                if mat.users == 0:
                    bpy.data.materials.remove(mat)
            for img in set(imgs):
                if img.users == 0:
                    bpy.data.images.remove(img)
            count += 1
        return count

    # ------------------------------------------------------------------ internals
    @classmethod
    def _create_single(cls, image_path, mat_type, suffix, prefix, plane_height):
        import bpy

        stem = os.path.splitext(os.path.basename(image_path))[0]
        image = bpy.data.images.load(image_path, check_existing=True)
        w, h = image.size
        aspect = (w / h) if h else 1.0
        plane = cls._make_plane(stem, plane_height * aspect, plane_height)
        mat = cls._make_material(f"{prefix}{stem}{suffix}", image, has_alpha=image.channels == 4)
        plane.data.materials.append(mat)
        return plane

    @staticmethod
    def _make_plane(name, width, height):
        """An upright (XZ-plane, +Y normal) quad centered at the origin, UV-unwrapped 0–1."""
        import bpy

        hw, hh = width / 2.0, height / 2.0
        verts = [(-hw, 0, -hh), (hw, 0, -hh), (hw, 0, hh), (-hw, 0, hh)]
        mesh = bpy.data.meshes.new(name)
        mesh.from_pydata(verts, [], [(0, 1, 2, 3)])
        mesh.update()
        uv = mesh.uv_layers.new(name="UVMap")
        for loop_index, coord in enumerate([(0, 0), (1, 0), (1, 1), (0, 1)]):
            uv.data[loop_index].uv = coord
        obj = bpy.data.objects.new(name, mesh)
        bpy.context.collection.objects.link(obj)
        return obj

    @staticmethod
    def _make_material(name, image, has_alpha):
        import bpy

        image.colorspace_settings.name = "sRGB"  # color image (albedo), not data
        mat = bpy.data.materials.new(name)
        mat.use_nodes = True
        nt = mat.node_tree
        bsdf = nt.nodes.get("Principled BSDF") or next(
            (n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None
        )
        tex = nt.nodes.new("ShaderNodeTexImage")
        tex.image = image
        if bsdf:
            nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
            if has_alpha:
                nt.links.new(tex.outputs["Alpha"], bsdf.inputs["Alpha"])
        if has_alpha and hasattr(mat, "blend_method"):  # pre-4.2 EEVEE; 4.2+ derives it from alpha
            mat.blend_method = "HASHED"
        return mat

    @staticmethod
    def _group(objects, name):
        import bpy

        empty = bpy.data.objects.new(name, None)
        bpy.context.collection.objects.link(empty)
        for o in objects:
            o.parent = empty
        return empty
