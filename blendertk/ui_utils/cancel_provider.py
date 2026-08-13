# !/usr/bin/python
# coding=utf-8
"""Blender's answers to uitk's cancellation contract (mayatk parity twin).

Blender has no equivalent of Maya's ``MComputation``: a script has no way to
ask the host "did the user press Esc?" mid-execution. ``wm.progress_update``
draws a cursor-based progress readout but reports nothing back, and modal
operators only receive Esc through the event loop the script is blocking.

So this provider supplies the *pump-independent key-hold probe* as its only
source (inherited from uitk's base provider), which reads physical key state
without needing the event loop — the same mechanism, minus the host peek that
only Maya offers. Cancellation semantics, the public surface and the slot-side
contract are identical to :class:`mayatk.MayaCancelProvider`, so tentacle slots
stay branch-free.

Transaction: **not supported yet** — declared, not faked. Maya can bracket a
slot in one undo chunk and reverse it exactly; Blender's undo is a stack of
whole-state snapshots with no "chunk" concept and no public API to query the
current history index, so a cancelled slot that ran N operators would need N
``ed.undo()`` steps that nothing can count reliably. Stepping back the wrong
number of times leaves the file in an arbitrary intermediate state — strictly
worse than leaving the partial work visible, where at least the user can see
what happened and undo it themselves. ``supports_rollback`` is therefore
``False`` and the dispatcher reports the gap instead of half-performing it.
See the backlog entry for the ``ed.undo_history``-based implementation, which
needs live verification in a GUI Blender before it can be trusted.
"""
from __future__ import annotations

from typing import Any, Optional

from uitk.managers.cancel_manager import CancelProvider

# NOTE: ``bpy`` is imported inside call bodies, never at module scope — the
# package surface must resolve without a running Blender (blendertk hard rule).


class _BlenderBracket:
    """State for one in-flight operation."""

    __slots__ = ("label", "total")

    def __init__(self, label: str):
        self.label = label
        self.total = 0


class BlenderCancelProvider(CancelProvider):
    """Blender host strategy for :class:`uitk.CancelManager`.

    Install once per session::

        btk.BlenderCancelProvider.install()
    """

    name = "blender"

    #: Blender's Qt panels are hosted as child windows of the Blender window;
    #: dispatching queued input mid-slot would nest a second slot into
    #: half-mutated scene state, exactly as in Maya.
    exclude_user_input = True

    #: No chunk equivalent, no way to count the steps to unwind — see the
    #: module docstring. Declared false rather than half-performed.
    supports_rollback = False

    # ``install``, the bracket stack and the reporting hooks come from the base
    # class; Blender has no native message stream to override them with, so the
    # default module logger (which the Script Output console captures) stands.

    # ------------------------------------------------------------------
    # Transaction + host bracket
    # ------------------------------------------------------------------
    def begin(self, scope, label: str = "", rollback: bool = False) -> Any:
        """Start Blender's progress readout for the operation.

        *rollback* is accepted for contract parity and ignored — see
        :attr:`supports_rollback`. The dispatcher reports the gap.

        The bracket is created before ``bpy`` is touched, so the operation is
        still tracked (and :meth:`end` still balances) if the readout can't be
        started — including in a Qt-only environment with no ``bpy`` at all.
        """
        bracket = self.open_bracket(_BlenderBracket(label))

        try:
            import bpy

            wm = bpy.context.window_manager
            if wm is not None:
                wm.progress_begin(0, 100)
        except Exception:
            pass

        return bracket

    def tick(
        self,
        value: Optional[int] = None,
        total: Optional[int] = None,
        text: Optional[str] = None,
    ) -> None:
        """Mirror progress into Blender's cursor progress readout."""
        bracket = self.current_bracket
        if bracket is None:
            return
        try:
            import bpy

            wm = bpy.context.window_manager
            if wm is None or value is None:
                return
            if total and total > 0:
                bracket.total = total
                wm.progress_update(int(100 * value / total))
            else:
                wm.progress_update(int(value))
        except Exception:
            pass

    def end(self, token: Any, cancelled: bool = False, rollback: bool = False) -> None:
        """End the progress readout. Rollback is not performed (see the class)."""
        # None for a junk token and for a double close, so progress_end() runs
        # exactly once per begin().
        bracket = self.close_bracket(token)
        if bracket is None:
            return

        try:
            import bpy

            wm = bpy.context.window_manager
            if wm is not None:
                wm.progress_end()
        except Exception:
            pass

        if cancelled and rollback:
            self.report_warning(
                f"'{bracket.label}' cancelled - partial changes were left in "
                "place; Blender cannot roll back a cancelled slot. Use Ctrl+Z."
            )
