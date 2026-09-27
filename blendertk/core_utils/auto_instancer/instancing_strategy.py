# !/usr/bin/python
# coding=utf-8
"""AutoInstancer's instancing strategy: the Blender binding of the ptk engine.

The decision tree, its config and the strategy enum live once in pythontk's
instancing engine; this module keeps the three names at their blendertk path
and supplies the one scene read, the prototype's triangle count -- the polygon
fan count (``sum(len(p.vertices) - 2)``), the same metric blendertk's
``get_similar_mesh`` uses.
"""

from __future__ import annotations

import pythontk as ptk
from pythontk import StrategyConfig, StrategyType

__all__ = ["InstancingStrategy", "StrategyConfig", "StrategyType"]


class InstancingStrategy(ptk.InstancingStrategy):
    """:class:`pythontk.InstancingStrategy` counting triangles on a Blender object."""

    def _get_triangle_count(self, mesh_node: object) -> int:
        """Fan-triangle count for ``mesh_node``'s mesh, or 0 when it has none.

        Parameters:
            mesh_node: An object carrying a ``data`` mesh datablock.

        Returns:
            The polygon fan count (``n - 2`` per face).
        """
        # Deferred: keeps this strategy module importable without the package root.
        from blendertk.core_utils._core_utils import CoreUtils

        try:
            return CoreUtils._mesh_face_counts(getattr(mesh_node, "data", None))[0]
        except Exception:
            return 0
