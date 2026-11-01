# !/usr/bin/python
# coding=utf-8
"""Blender shot sequencer — the timeline engine and the panel's host half.

Mirror of mayatk's ``anim_utils.shots.shot_sequencer`` at the public-method level
(:class:`ShotSequencer`).  The collision-safe planning is shared upstream
(``pythontk.core_utils.engines.shots.shot_plan`` / ``shot_apply``); this package
supplies the Blender key-mover injected into ``apply``, the two scene measures
the planner can't do (pivot key move, content-range trim), and the Shot
Sequencer panel's host half: :class:`SequencerHost` (every scene call the panel
makes), ``SegmentCollector`` and ``GraphSync`` (the graph view).  The panel is
tentacle's (``slots/tools/shot_sequencer.py``), one for both hosts.
"""
