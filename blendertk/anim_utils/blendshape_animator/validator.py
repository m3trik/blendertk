# !/usr/bin/python
# coding=utf-8
"""Mesh + shape-key setup validation — mirror of mayatk's
``anim_utils.blendshape_animator.validator.Validator``.
"""
import pythontk as ptk


class Validator(ptk.LoggingMixin):
    """Handles validation of meshes and shape-key setups."""

    @classmethod
    def validate_meshes(cls, obj1, obj2) -> bool:
        """Validate that both objects are compatible mesh objects with matching vertex counts."""
        for i, obj in enumerate((obj1, obj2), 1):
            if obj is None:
                cls.logger.error(f"Object {i} does not exist")
                return False
            try:
                name = obj.name
            except ReferenceError:
                cls.logger.error(f"Object {i} does not exist")
                return False
            if getattr(obj, "type", None) != "MESH":
                cls.logger.error(f"Object {i} ({name}) is not a mesh")
                return False

        verts1 = len(obj1.data.vertices)
        verts2 = len(obj2.data.vertices)

        if verts1 != verts2:
            cls.logger.error(
                f"Vertex count mismatch - {obj1.name}: {verts1}, {obj2.name}: {verts2}"
            )
            return False

        cls.logger.info(f"Mesh validation passed - both have {verts1} vertices")
        return True

    @classmethod
    def validate_shape_setup(cls, base_obj, key_name: str) -> bool:
        """Validate the master shape key exists (Blender analogue of mayatk's blendShape
        node/envelope validation — a shape key has no separate "envelope" attribute to check,
        so this validates the piece that actually exists: the key itself and its mute state)."""
        shape_keys = getattr(base_obj.data, "shape_keys", None)
        if shape_keys is None or key_name not in shape_keys.key_blocks:
            cls.logger.error(f"Shape key '{key_name}' does not exist on {base_obj.name}")
            return False

        kb = shape_keys.key_blocks[key_name]
        if kb.mute:
            cls.logger.warning(f"Shape key '{key_name}' is muted")

        return True


__all__ = ["Validator"]
