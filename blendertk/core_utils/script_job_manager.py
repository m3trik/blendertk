# !/usr/bin/python
# coding=utf-8
"""Centralized Blender event-subscription manager — the Blender counterpart of mayatk's
``core_utils.script_job_manager.ScriptJobManager`` (``btk.ScriptJobManager`` ↔
``mtk.ScriptJobManager``), so the shared tentacle slots stay branch-free.

Maya unifies ``cmds.scriptJob`` events under one owner / unsubscribe / widget-destroy cleanup
path; this does the same over **``bpy.app.handlers``**. Maya event names map onto Blender
handlers (one ``@persistent`` master handler installed per handler list, multiplexed to any
number of subscriber callbacks):

==================  ==========================================================
Maya event          Blender backing
==================  ==========================================================
``SceneOpened``     ``bpy.app.handlers.load_post``
``NewSceneOpened``  ``bpy.app.handlers.load_post`` (Blender doesn't distinguish open vs. new)
``SceneSaved``      ``bpy.app.handlers.save_post``
``timeChanged``     ``bpy.app.handlers.frame_change_post``
``SelectionChanged``  ``bpy.app.handlers.depsgraph_update_post`` (filtered by a selection diff)
``Undo`` / ``Redo``   ``bpy.app.handlers.undo_post`` / ``redo_post``
==================  ==========================================================

The master handlers are ``@persistent`` so they survive file loads (a non-persistent handler is
dropped on load and would never fire again). Mirrors mayatk's ephemeral behaviour: an
``ephemeral=True`` subscription is pruned the next time a scene-change event fires.

Usage::

    from blendertk.core_utils.script_job_manager import ScriptJobManager  # or btk.ScriptJobManager

    mgr = ScriptJobManager.instance()
    mgr.subscribe("SceneOpened", self._on_scene, owner=self)
    mgr.subscribe("SelectionChanged", self._on_sel, owner=self, ephemeral=True)
    mgr.connect_cleanup(self.ui, owner=self)  # auto-unsubscribe on Qt widget destroy

Divergence from mayatk: there is no ``add_om_callback`` — its OpenMaya ``MMessage`` analogue is
``bpy.msgbus``, which has its own survival-across-load semantics; add a ``subscribe_rna`` only
when a tool actually needs arbitrary RNA-property watching (YAGNI). ``import bpy`` is deferred
into the call bodies (no import side effects).
"""
from __future__ import annotations

import itertools
import logging
from typing import Any, Callable, Dict, FrozenSet, List, Optional, Set


logger = logging.getLogger(__name__)

# Maya event name -> bpy.app.handlers list name (one master handler installed per list).
_HANDLER_EVENTS: Dict[str, str] = {
    "SceneOpened": "load_post",
    "NewSceneOpened": "load_post",
    "SceneSaved": "save_post",
    "timeChanged": "frame_change_post",
    "SelectionChanged": "depsgraph_update_post",
    "Undo": "undo_post",
    "Redo": "redo_post",
}
# Scene-change events that prune ephemeral subscriptions (mirror of mayatk).
_SCENE_CHANGE_EVENTS: FrozenSet[str] = frozenset({"SceneOpened", "NewSceneOpened"})
# Events dispatched only when the selection set actually changed (depsgraph fires constantly).
_SELECTION_EVENTS: FrozenSet[str] = frozenset({"SelectionChanged"})


class _Subscription:
    """Internal subscription record for an event listener."""

    __slots__ = ("token", "event", "callback", "owner", "ephemeral")

    def __init__(self, token, event, callback, owner, ephemeral):
        self.token = token
        self.event = event
        self.callback = callback
        self.owner = owner
        self.ephemeral = ephemeral


class ScriptJobManager:
    """Centralized Blender event dispatcher (mirror of mayatk's ``ScriptJobManager``).

    Installs at most one ``@persistent`` master handler per ``bpy.app.handlers`` list and
    multiplexes it to any number of subscriber callbacks. Obtain the singleton via
    :meth:`instance`.
    """

    _instance: Optional["ScriptJobManager"] = None

    @classmethod
    def instance(cls) -> "ScriptJobManager":
        """Return the module-wide singleton, creating it on first access."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Tear down the singleton and allow a fresh one to be created (tests / reload)."""
        if cls._instance is not None:
            cls._instance.teardown()
            cls._instance = None

    # ------------------------------------------------------------------ init
    def __init__(self):
        self._subs: Dict[int, _Subscription] = {}
        self._events: Dict[str, List[int]] = {}  # event -> [tokens]
        self._handlers: Dict[str, Callable] = {}  # handler-list name -> installed master fn
        self._counter = itertools.count(1)
        self._connected_widgets: Set[int] = set()
        self._suppressed: Set[int] = set()
        self._last_selection: FrozenSet[str] = frozenset()

    # -------------------------------------------------------------- public API
    def subscribe(
        self,
        event: str,
        callback: Callable,
        *,
        owner: Any = None,
        ephemeral: bool = False,
    ) -> int:
        """Register *callback* (called with no args) for a Maya-named *event*.

        Parameters
        ----------
        event : str
            One of the keys in :data:`_HANDLER_EVENTS` (``"SceneOpened"``,
            ``"SelectionChanged"``, ``"timeChanged"``, …).
        callback : callable
            Invoked with no arguments each time the event fires.
        owner : object, optional
            Grouping key for :meth:`unsubscribe_all` / :meth:`connect_cleanup`.
        ephemeral : bool
            If ``True``, pruned automatically the next time a scene-change event fires.

        Returns
        -------
        int
            Opaque token for :meth:`unsubscribe`.

        Raises
        ------
        ValueError
            If *event* has no Blender backing.
        """
        if event not in _HANDLER_EVENTS:
            raise ValueError(
                f"Unknown event {event!r}; supported: {sorted(_HANDLER_EVENTS)}"
            )
        token = next(self._counter)
        self._subs[token] = _Subscription(token, event, callback, owner, ephemeral)
        self._events.setdefault(event, []).append(token)
        self._ensure_handler(_HANDLER_EVENTS[event])
        return token

    def unsubscribe(self, token: int) -> None:
        """Remove a single subscription by *token*."""
        self._suppressed.discard(token)
        sub = self._subs.pop(token, None)
        if sub is None:
            return
        tokens = self._events.get(sub.event)
        if tokens:
            try:
                tokens.remove(token)
            except ValueError:
                pass
            if not tokens:
                del self._events[sub.event]
        self._prune_handler(_HANDLER_EVENTS[sub.event])

    def unsubscribe_all(self, owner: Any) -> None:
        """Remove every subscription registered under *owner*."""
        for token in [t for t, s in self._subs.items() if s.owner is owner]:
            self.unsubscribe(token)

    def connect_cleanup(self, widget, owner: Any) -> None:
        """Connect *widget*.destroyed → :meth:`unsubscribe_all` for *owner* (Qt).

        Safe to call multiple times for the same *widget*. The Qt signal is used directly
        (no import) so this stays headless-safe until a real widget is passed.
        """
        wid = id(widget)
        if wid in self._connected_widgets:
            return
        self._connected_widgets.add(wid)
        widget.destroyed.connect(lambda *_: self._on_widget_destroyed(wid, owner))

    def suppress(self, token: int) -> None:
        """Temporarily silence a subscription without removing it."""
        if token in self._subs:
            self._suppressed.add(token)

    def resume(self, token: int) -> None:
        """Re-enable a previously suppressed subscription."""
        self._suppressed.discard(token)

    def status(self) -> Dict[str, Any]:
        """Snapshot of installed handlers and current subscriptions."""
        return {
            "installed_handlers": sorted(self._handlers),
            "subscriptions": [
                {
                    "token": s.token,
                    "event": s.event,
                    "owner": repr(s.owner),
                    "ephemeral": s.ephemeral,
                    "suppressed": s.token in self._suppressed,
                }
                for s in self._subs.values()
            ],
        }

    def print_status(self) -> None:
        """Pretty-print :meth:`status` for interactive debugging."""
        s = self.status()
        print("ScriptJobManager status")
        print(f"  installed handlers ({len(s['installed_handlers'])}): {s['installed_handlers']}")
        print(f"  subscriptions ({len(s['subscriptions'])}):")
        for sub in s["subscriptions"]:
            flags = [f for f, on in (("ephemeral", sub["ephemeral"]), ("suppressed", sub["suppressed"])) if on]
            tag = f" ({', '.join(flags)})" if flags else ""
            print(f"    #{sub['token']} {sub['event']} owner={sub['owner']}{tag}")

    def teardown(self) -> None:
        """Remove every installed master handler and drop all subscriptions."""
        for name in list(self._handlers):
            self._remove_handler(name)
        self._subs.clear()
        self._events.clear()
        self._suppressed.clear()
        self._connected_widgets.clear()

    # -------------------------------------------------------------- internals
    def _ensure_handler(self, name: str) -> None:
        """Install the ``@persistent`` master handler for the bpy handler list *name*."""
        if name in self._handlers:
            return
        import bpy

        events = [e for e, h in _HANDLER_EVENTS.items() if h == name]

        @bpy.app.handlers.persistent
        def _master(*_args, _events=tuple(events)):
            for event in _events:
                if event in _SELECTION_EVENTS:
                    current = self._current_selection()
                    if current == self._last_selection:
                        continue
                    self._last_selection = current
                self._dispatch(event)

        getattr(bpy.app.handlers, name).append(_master)
        self._handlers[name] = _master
        if name == "depsgraph_update_post":
            # Seed the baseline so the first depsgraph fire dispatches SelectionChanged only on a
            # genuine change (not on the selection that already existed at subscribe time).
            self._last_selection = self._current_selection()

    def _prune_handler(self, name: str) -> None:
        """Remove the master handler for *name* once no event maps to a live subscription."""
        if any(_HANDLER_EVENTS[s.event] == name for s in self._subs.values()):
            return
        self._remove_handler(name)

    def _remove_handler(self, name: str) -> None:
        """Detach and forget the master handler for bpy handler list *name* (best effort)."""
        fn = self._handlers.pop(name, None)
        if fn is None:
            return
        try:
            import bpy

            handler_list = getattr(bpy.app.handlers, name)
            if fn in handler_list:
                handler_list.remove(fn)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("ScriptJobManager: failed to remove %r handler (%s)", name, exc)

    def _dispatch(self, event: str) -> None:
        """Dispatch *event* to its current subscribers, then prune ephemerals on scene change."""
        for token in list(self._events.get(event, [])):
            if token in self._suppressed:
                continue
            sub = self._subs.get(token)
            if sub is not None:
                try:
                    sub.callback()
                except Exception as exc:
                    logger.debug("ScriptJobManager: %r listener error: %s", event, exc)
        if event in _SCENE_CHANGE_EVENTS:
            self._prune_ephemerals()

    def _current_selection(self) -> FrozenSet[str]:
        """The selected-object name set (for SelectionChanged diffs), via the shared
        ``selected_objects`` reader so the selection idiom stays single-sourced."""
        from blendertk.core_utils._core_utils import selected_objects

        try:
            return frozenset(o.name for o in selected_objects())
        except Exception:
            return self._last_selection

    def _prune_ephemerals(self) -> None:
        """Remove every ephemeral subscription (scene changed)."""
        for token in [t for t, s in self._subs.items() if s.ephemeral]:
            self.unsubscribe(token)

    def _on_widget_destroyed(self, wid: int, owner: Any) -> None:
        """Qt widget destroyed → clean up all of *owner*'s subscriptions."""
        self._connected_widgets.discard(wid)
        self.unsubscribe_all(owner)
