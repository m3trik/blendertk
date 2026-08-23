# !/usr/bin/python
# coding=utf-8
"""Range resolution for the Shot Manifest build pipeline (Blender-bound facade).

Mirror of mayatk's ``shot_manifest.range_resolver``.  The resolver math lives
once, DCC-agnostic, in
:mod:`pythontk.core_utils.engines.shots.manifest.range_resolver` (shared with
mayatk).  This facade binds its injectable ``duration_fn`` to blendertk's
:meth:`~blendertk.anim_utils.shots.shot_manifest.behaviors.Behaviors.compute_duration`
— the Blender-bound one that probes audio sources against the scene FPS and
resolves placed-strip paths — so audio steps size to their clip length in the
table before they are built.
"""

from typing import Callable, Dict, List, Optional, Tuple

from pythontk.core_utils.engines.shots.manifest.range_resolver import (
    RangeResolver as _PyRangeResolver,
)

# ``prune_to_top_boundaries`` / ``resolve_ranges`` live on the engine class;
# re-exported under their historical names (blendertk's ``RangeResolver`` wraps them).
prune_to_top_boundaries = _PyRangeResolver.prune_to_top_boundaries  # noqa: F401
_engine_resolve_ranges = _PyRangeResolver.resolve_ranges

from pythontk import BuilderStep  # noqa: E402


class RangeResolver:
    """RangeResolver — module namespace."""

    @staticmethod
    def resolve_ranges(
        steps: List[BuilderStep],
        user_ranges: Dict[str, Tuple[Optional[float], Optional[float]]],
        gap_starts: List[float],
        gap_end_map: Dict[float, float],
        gap: float,
        use_selected_keys: bool,
        last_resolved: List[Tuple[str, float, Optional[float], bool]],
        from_step_idx: int = 0,
        default_duration: float = 0,
        duration_fn: Optional[Callable[..., float]] = None,
    ) -> List[Tuple[str, float, Optional[float], bool]]:
        """Compute a resolved ``(start, end)`` for every step.

        See :func:`pythontk.core_utils.engines.shots.manifest.range_resolver.resolve_ranges`
        for the full parameter reference.  When *duration_fn* is ``None`` the
        Blender-bound ``behaviors.compute_duration`` is injected (imported lazily
        from the package so the established mock seam keeps working).
        """
        if duration_fn is None:
            from blendertk.anim_utils.shots.shot_manifest.behaviors import Behaviors

            duration_fn = Behaviors.compute_duration
        return _engine_resolve_ranges(
            steps,
            user_ranges,
            gap_starts,
            gap_end_map,
            gap,
            use_selected_keys,
            last_resolved,
            from_step_idx=from_step_idx,
            default_duration=default_duration,
            duration_fn=duration_fn,
        )
