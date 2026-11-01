# !/usr/bin/python
# coding=utf-8
"""Blender shots — DCC layer over ``pythontk``'s shots engine.

Mirror of mayatk's ``anim_utils.shots`` package.  The shot model, planner, and
detection math are shared (``pythontk.core_utils.engines.shots``); this package
holds Blender's acquisition + persistence adapter (:mod:`_shots`), the Shot
Manifest's host half (:mod:`shot_manifest`) and the Shot Sequencer's engine and
host half (:mod:`shot_sequencer`).  The Shots, Shot Manifest and Shot Sequencer
panels are tentacle's (``slots/tools/``), one for both hosts, over this
package's store and engines.

Public surface is registered via the root ``DEFAULT_INCLUDE`` (see
``blendertk/__init__.py``); this ``__init__`` is docstring-only per the
no-import-side-effects rule.
"""
