# !/usr/bin/python
# coding=utf-8
"""Blender shot sequencer — timeline engine + panel over pythontk's shots planner.

Mirror of mayatk's ``anim_utils.shots.shot_sequencer`` at the public-method level
(:class:`ShotSequencer`).  The collision-safe planning is shared upstream
(``pythontk.core_utils.engines.shots.shot_plan`` / ``shot_apply``); this package
supplies the Blender key-mover injected into ``apply``, the two scene measures
the planner can't do (pivot key move, content-range trim), and the visual
Sequencer *panel* (``shot_sequencer_slots`` + the four controller mixins over
the shared uitk ``SequencerWidget``), discovered by ``BlenderUiHandler``.
"""
