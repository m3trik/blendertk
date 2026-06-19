# !/usr/bin/python
# coding=utf-8
"""Curve / NURBS-adjacent utilities — Blender mirror of mayatk's ``nurbs_utils`` subpackage.

Blender's curve object model + native **bevel** (round profile sweep) and **2D fill** (planar
surface with even-odd holes) replace most of Maya's NURBS command layer (``loft`` / ``planarSrf`` /
``nurbsToPoly`` / ``extrude``). So this package holds a small shared engine
(:class:`~blendertk.nurbs_utils._nurbs_utils.NurbsUtils` — build a curve from points, bake a curve
to mesh) plus the co-located curve tools (``image_tracer``, …), exactly like mayatk's split.

Per the no-import-side-effects rule this module is docstring-only; the public surface is registered
from the root ``DEFAULT_INCLUDE``.
"""
