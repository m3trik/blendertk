# !/usr/bin/python
# coding=utf-8
"""Shader templates: save a material's node graph as a reusable template, rebuild it.

:class:`ShaderTemplates` mirrors mayatk's (name + behavior): tentacle's Shader
Templates panel saves and restores through it. Maya's templates are YAML shading
networks (``GraphSaver`` / ``GraphRestorer``); Blender's are a material's node tree,
captured by :meth:`MatUtils.serialize_material` and rebuilt by
:meth:`MatUtils.restore_material` (JSON), its image nodes rebound to fresh textures
by map type. blendertk ships no templates (mayatk's are Maya networks), so the store
holds the user's alone, in ``<user config>/blendertk/shader_templates/``.

``import bpy`` is deferred into the call bodies.
"""

import json
import logging
import os
from pathlib import Path

import pythontk as ptk

from blendertk.core_utils._core_utils import CoreUtils
from blendertk.mat_utils._mat_utils import MatUtils

#: Default logger for library (non-panel) use; the panel passes its own, so the
#: feedback lands in its log.
_LOGGER = logging.getLogger("ShaderTemplateManager")


class ShaderTemplates:
    """Save and restore a material's node graph as a template (mirror of mayatk's)."""

    #: The node types Save Template leaves out of a capture. mayatk's lists the
    #: scene around a Maya material (its shading group, the objects it is on); a
    #: Blender material's tree holds its shader nodes only, so none here.
    EXCLUDE_TYPES: tuple = ()

    @classmethod
    def store(cls) -> "ptk.PresetStore":
        """The template store: the user's templates, in
        ``<user config>/blendertk/shader_templates/`` (JSON)."""
        return ptk.PresetStore("shader_templates", package="blendertk")

    @staticmethod
    def _material(nodes):
        """The material *nodes* stand for: a material itself, else the active
        material of the first object carrying one (the active object first);
        None without one."""
        import bpy

        if not isinstance(nodes, (list, tuple)):
            nodes = [nodes]
        nodes = [n for n in nodes if n is not None]
        active = CoreUtils.active_object()
        if active is not None and active in nodes:
            nodes.insert(0, active)
        for node in nodes:
            if isinstance(node, bpy.types.Material):
                return node
            material = getattr(node, "active_material", None)
            if material is not None:
                return material
        return None

    @classmethod
    def suggested_name(cls, nodes) -> str:
        """The name Save Template offers for *nodes*: the material it would
        capture, ``""`` without one. Twin of mayatk's, which offers the first
        node's name."""
        material = cls._material(nodes)
        return material.name if material is not None else ""

    @classmethod
    def save_template(cls, nodes, file_path, exclude_types=None, logger=None) -> None:
        """Save the material *nodes* carry (:meth:`_material`) as a template at
        *file_path* -- mirror of mayatk's.

        Parameters:
            nodes: Objects and/or materials (the selection).
            file_path: The template file to write: ``MatUtils.serialize_material``'s
                dict, as JSON.
            exclude_types: Accepted for mayatk's signature (:attr:`EXCLUDE_TYPES`);
                a material's tree holds its shader nodes only.
            logger: Destination for progress and errors (e.g. a panel's
                redirected logger).
        """
        logger = logger or _LOGGER
        material = cls._material(nodes)
        data = MatUtils.serialize_material(material) if material is not None else {}
        if not data.get("nodes"):
            logger.warning("No material selected or provided for template saving.")
            return
        path = str(file_path)
        try:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            Path(path).write_text(ptk.JSON_CODEC.dump(data), encoding="utf-8")
        except OSError as e:
            logger.error(f"Failed to save graph to {path}. Error: {e}")
            return
        # The ONE announcement of the save, as mayatk's GraphSaver makes it: the
        # node count and the path (a link that opens it, where links render;
        # the label truncated, never the href).
        shown = path
        if hasattr(logger, "log_link"):
            shown = logger.log_link(ptk.truncate(path, 60), "open", path=path)
        logger.info(f"Graph information saved to {shown} ({len(data['nodes'])} node(s))")

    @staticmethod
    @CoreUtils.undoable
    def restore_template(file_path, texture_paths=None, name=None, logger=None) -> dict:
        """Rebuild the template at *file_path* as a new material, assigned to the
        selected meshes -- mirror of mayatk's, ONE undo step.

        Its image nodes are rebound to *texture_paths* by map type. A Blender
        material needs an object to carry it (a Maya network stands alone), so the
        new one goes onto the selected meshes. Announced on *logger*: the material,
        as a link that opens it in the Shader Editor, and how many objects took it.

        Parameters:
            file_path: The template file (JSON).
            texture_paths: Texture files to rebind by map type.
            name: The material's name; default the template's (the file's stem).
            logger: Destination for progress and errors.

        Returns:
            dict: ``{material name: material}``; empty when the file can't be read.
        """
        logger = logger or _LOGGER
        try:
            data = json.loads(Path(file_path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            logger.error(f"Failed to load template: {file_path}. Error: {e}")
            return {}
        if not isinstance(data, dict):
            logger.error(f"Malformed template (expected a mapping): {file_path}")
            return {}
        material = MatUtils.restore_material(
            data, name=name or Path(file_path).stem, textures=list(texture_paths or [])
        )
        if material is None:
            return {}
        objects = [o for o in CoreUtils.selected_objects() if o.type == "MESH"]
        if objects:
            MatUtils.assign_mat(objects, material)
        if logger.isEnabledFor(logging.INFO):
            label = material.name
            if hasattr(logger, "log_link"):
                label = logger.log_link(label, "graph", node=material.name)
            lines = [f"Material    : {label}", f"Assigned to : {len(objects)} object(s)"]
            if hasattr(logger, "log_box"):
                logger.log_box("NETWORK CREATED", lines, level="SUCCESS")
            else:
                logger.info(
                    "Network created: "
                    + "; ".join(" ".join(line.split()) for line in lines)
                )
        return {material.name: material}
