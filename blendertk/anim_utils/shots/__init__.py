# !/usr/bin/python
# coding=utf-8
"""Blender shots — DCC layer over ``pythontk``'s shots engine.

Mirror of mayatk's ``anim_utils.shots`` package.  The shot model, planner, and
detection math are shared (``pythontk.core_utils.engines.shots``); this package
holds Blender's acquisition + persistence adapter (:mod:`_shots`) and the three
co-located panels — Shots (settings), :mod:`shot_manifest`, and
:mod:`shot_sequencer` — discovered by :class:`BlenderUiHandler`.

Public surface is registered via the root ``DEFAULT_INCLUDE`` (see
``blendertk/__init__.py``); this ``__init__`` is docstring-only per the
no-import-side-effects rule.
"""
