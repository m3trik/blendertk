# !/usr/bin/python
# coding=utf-8
"""Master shape-key value keyframe animation — mirror of mayatk's
``anim_utils.blendshape_animator.keyframes.Keyframes``.

Maya keyframes the ``blendShape.weight[0]`` attribute; the direct Blender analogue is the
master shape key's own ``value`` — already a first-class keyable float (no driven-attribute
workaround needed). Reading the fcurve back uses the public, slot-aware
``blendertk.get_fcurves`` (mirrors the private ``_slot_fcurves`` helper ``_anim_utils.py``
already uses internally for the same "Blender 4.4+ layered/slotted actions" reason — reusing
the public entry point here instead of duplicating that logic).
"""
from contextlib import contextmanager
from typing import Tuple

import pythontk as ptk

from blendertk.anim_utils.blendshape_animator.validator import Validator


@contextmanager
def preserve_sibling_values(key_id):
    """Snapshot every *un-driven* key block's ``value`` on ``key_id`` and restore it on exit.

    ``bpy.context.view_layer.update()`` doesn't just settle the depsgraph — if a keyframe was
    recently inserted anywhere on ``key_id`` (e.g. another :class:`BlendshapeAnimator` session's
    ``create_keyframes`` on a DIFFERENT master key sharing the same base mesh), it re-applies
    ANIMATION for every f-curve on that ID at the current frame, silently overwriting any
    manually-set (not-yet-keyed) sibling value — clobbering a co-authored morph's live preview
    that never touched the timeline. Driven keys (correctives) are excluded: their value is
    owned by their driver and must be left to recompute, not pinned to a stale snapshot.
    """
    if key_id is None:
        yield
        return

    driven_paths = set()
    anim = key_id.animation_data
    if anim is not None:
        driven_paths = {d.data_path for d in anim.drivers}

    snapshot = {
        kb.name: kb.value
        for kb in key_id.key_blocks
        if f'key_blocks["{kb.name}"].value' not in driven_paths
    }
    try:
        yield
    finally:
        for kb in key_id.key_blocks:
            if kb.name in snapshot:
                kb.value = snapshot[kb.name]


class Keyframes(ptk.LoggingMixin):
    """Core shape-key value animation functionality."""

    def __init__(self, base_obj, target_obj, key_name: str):
        super().__init__()
        self.base_obj = base_obj
        self.target_obj = target_obj
        self.key_name = key_name
        self.validator = Validator()

    @property
    def key_id(self):
        """The mesh's ``Key`` ID datablock (``mesh.shape_keys``) — the animatable owner of
        every key block's ``value``, analogous to the blendShape node owning ``weight[0]``."""
        return self.base_obj.data.shape_keys

    @property
    def key_block(self):
        key_id = self.key_id
        return key_id.key_blocks.get(self.key_name) if key_id else None

    def _value_fcurve(self):
        """The fcurve driving ``key_blocks[key_name].value``, or ``None`` if unkeyed."""
        import blendertk as btk

        key_id = self.key_id
        if key_id is None:
            return None
        path = f'key_blocks["{self.key_name}"].value'
        return next((fc for fc in btk.get_fcurves([key_id]) if fc.data_path == path), None)

    def create_keyframes(self, start_frame: int, end_frame: int) -> bool:
        """Create linear keyframe animation on the master key's value, 0.0 -> 1.0."""
        kb = self.key_block
        if kb is None:
            self.logger.error(f"Shape key '{self.key_name}' not found")
            return False

        try:
            existing = self._value_fcurve()
            if existing is not None:
                for kp in reversed(list(existing.keyframe_points)):
                    existing.keyframe_points.remove(kp, fast=True)
                existing.update()

            kb.value = 0.0
            kb.keyframe_insert(data_path="value", frame=start_frame)
            kb.value = 1.0
            kb.keyframe_insert(data_path="value", frame=end_frame)

            fc = self._value_fcurve()
            if fc is not None:
                for kp in fc.keyframe_points:
                    kp.interpolation = "LINEAR"
                fc.update()

            self.logger.info(f"Created keyframes: {start_frame} to {end_frame}")
            return True
        except RuntimeError as e:
            self.logger.error(f"Creating keyframes: {e}")
            return False

    def test_morph(self) -> bool:
        """Test the shape key by temporarily setting its value to 0.5."""
        if not self.validator.validate_shape_setup(self.base_obj, self.key_name):
            return False

        import bpy

        kb = self.key_block
        with preserve_sibling_values(self.key_id):
            kb.value = 0.5
            bpy.context.view_layer.update()
            self.logger.info("Shape key test: value set to 0.5")
            self.logger.info(
                f"Check if {self.base_obj.name} changed shape (should morph, not move)"
            )
        return True

    def get_frame_range(self) -> Tuple[int, int]:
        """Return (start, end) frame range from keyframes on the master key's value."""
        fc = self._value_fcurve()
        keys = [kp.co.x for kp in fc.keyframe_points] if fc is not None else []
        if len(keys) < 2:
            raise ValueError("No valid keyframe range found")
        return int(min(keys)), int(max(keys))


__all__ = ["Keyframes", "preserve_sibling_values"]
