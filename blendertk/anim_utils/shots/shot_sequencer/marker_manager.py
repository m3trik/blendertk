# !/usr/bin/python
# coding=utf-8
"""Marker persistence for the shot sequencer controller (Blender).

Verbatim mirror of mayatk's ``shot_sequencer.marker_manager`` — the mixin is
fully DCC-agnostic (it only reads the shared ``SequencerWidget``'s markers and
writes them to the ``ShotSequencer`` model), so no Maya→Blender translation is
needed.
"""
from __future__ import annotations

__all__ = ["MarkerManagerMixin"]


class MarkerManagerMixin:
    """Mixin supplying marker CRUD persistence.

    Expects the host class to provide ``sequencer`` (a :class:`ShotSequencer`)
    and ``_get_sequencer_widget()``.
    """

    def on_marker_added(self, marker_id: int, time: float) -> None:
        """Persist a newly added marker."""
        if self.sequencer is None:
            return
        widget = self._get_sequencer_widget()
        if widget is None:
            return
        md = widget.get_marker(marker_id)
        if md is None:
            return
        self.sequencer.markers.append(
            {
                "time": md.time,
                "note": md.note,
                "color": md.color,
                "draggable": md.draggable,
                "style": md.style,
                "line_style": md.line_style,
                "opacity": md.opacity,
            }
        )
        # Markers serialize with the store — without marking it dirty the edit
        # is silently dropped on the next save cycle.
        self.sequencer.store.mark_dirty()

    def on_marker_moved(self, marker_id: int, new_time: float) -> None:
        """Update persisted marker time."""
        self._rebuild_markers_store()

    def on_marker_changed(self, marker_id: int) -> None:
        """Update persisted marker note/color."""
        self._rebuild_markers_store()

    def on_marker_removed(self, marker_id: int) -> None:
        """Remove marker from persistent store."""
        self._rebuild_markers_store()

    def _rebuild_markers_store(self) -> None:
        """Rebuild the sequencer's markers list from the widget's markers."""
        if self.sequencer is None:
            return
        widget = self._get_sequencer_widget()
        if widget is None:
            return
        self.sequencer.markers = [
            {
                "time": md.time,
                "note": md.note,
                "color": md.color,
                "draggable": md.draggable,
                "style": md.style,
                "line_style": md.line_style,
                "opacity": md.opacity,
            }
            for md in widget.markers()
        ]
        self.sequencer.store.mark_dirty()
