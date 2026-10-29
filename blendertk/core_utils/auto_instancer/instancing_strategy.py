# !/usr/bin/python
# coding=utf-8
"""AutoInstancer's instancing strategy: pythontk's engine, at its blendertk path.

The decision tree, its config and the strategy enum live once in pythontk's
instancing engine and are re-exported here. Its one scene read, the
prototype's triangle count, is an adapter the AutoInstancer injects
(``count_triangles=AutoInstancer._triangle_count``: the polygon fan count,
``sum(len(p.vertices) - 2)``, the same metric ``get_similar_mesh`` uses).
"""

from pythontk import InstancingStrategy, StrategyConfig, StrategyType

__all__ = ["InstancingStrategy", "StrategyConfig", "StrategyType"]
