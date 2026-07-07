# !/usr/bin/python
# coding=utf-8
"""Weight calculations for shape-key morph animation — mirror of mayatk's
``anim_utils.blendshape_animator.weights.Weights``.

Pure math, no ``bpy`` — DCC-agnostic and headless-testable. Kept self-contained in this
package rather than promoted to ``pythontk`` (YAGNI: nothing else in the ecosystem needs it
yet; promote later if a second consumer shows up).
"""
from typing import List, Tuple


class Weights:
    """Handles weight calculations and consistent rounding precision."""

    PRECISION = 3

    @classmethod
    def round_weight(cls, weight: float) -> float:
        """Round a weight (shape-key value) to a consistent precision."""
        return round(float(weight), cls.PRECISION)

    @classmethod
    def frame_to_weight(cls, frame: int, start_frame: int, end_frame: int) -> float:
        """Convert a timeline frame to the shape-key value it corresponds to, assuming the
        linear 0.0 -> 1.0 master keyframe curve built by ``Keyframes.create_keyframes``."""
        if frame <= start_frame:
            return 0.0
        if frame >= end_frame:
            return 1.0

        frame_range = end_frame - start_frame
        frame_offset = frame - start_frame
        return cls.round_weight(frame_offset / float(frame_range))

    @classmethod
    def generate_weights(
        cls,
        count: int,
        weight_range: Tuple[float, float] = (0.0, 1.0),
        include_endpoints: bool = False,
    ) -> List[float]:
        """Generate ``count`` evenly spaced weights within ``weight_range``.

        ``count`` always equals ``len(returned)`` regardless of ``include_endpoints``.
        With ``include_endpoints=True`` the first and last entries are exactly
        ``weight_range[0]`` and ``weight_range[1]`` (requires ``count >= 2``).
        With ``include_endpoints=False`` the entries lie strictly inside the range.
        """
        if count < 1:
            return []
        start, end = weight_range

        if include_endpoints:
            if count == 1:
                weights = [start]
            else:
                step = (end - start) / float(count - 1)
                weights = [start + step * i for i in range(count)]
        else:
            step = (end - start) / float(count + 1)
            weights = [start + step * i for i in range(1, count + 1)]

        return [cls.round_weight(w) for w in weights]


__all__ = ["Weights"]
