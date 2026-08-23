# !/usr/bin/python
# coding=utf-8
"""Persist Blender's per-session UI visibility state across sessions (``btk.UiState``).

**Why this exists.** Blender stores every UI visibility toggle — which areas a workspace shows
(the Timeline strip), the viewport overlays (grid / axes), region flags (header / toolbar /
sidebar), shading toggles (x-ray) — in the **.blend file's** screen data, not in
``userpref.blend``. A new session loads ``startup.blend``, so a toggle flipped last session is
gone unless the user re-saves the startup file by hand (Ctrl+U), and opening any file brings
that file's own layout. Maya keeps the same state in its user prefs automatically, so tentacle
users expect it to stick. This is the Blender-side stand-in: a JSON sidecar in Blender's config
dir (beside ``blendertk_script_output.json``), re-applied at launch and after every file load,
refreshed by a cheap change-detection timer — Blender has no quit hook, and by ``atexit`` the
screen data is already freed, so snapshots are taken while the session runs.

**What is tracked** (data-driven, no per-property code):

* ``hidden`` — editors the user closed out of the workspace's loaded layout (by ``ui_type``, so
  Timeline and Dope Sheet are distinct). Re-applied by closing them again; editors the user
  *added* can't be re-created in place and are left alone. A hidden editor stays hidden until
  the user brings it back on screen.
* ``spaces`` — for the largest area of each ``ui_type``, every writable ``show_*`` boolean on
  the space (``show_region_*``, ``show_gizmo*``, …) and on its :attr:`SUBSTRUCTS`
  (``overlay.show_floor``, ``shading.show_xray``, …). Applied to **every** area of that
  ``ui_type``. Enums like ``shading.type`` are deliberately out: appearance, not visibility.

Scope is per **workspace** of the main window, with the active workspace captured each tick and
the others as they are first visited. ``show_region_*`` carries Blender's own landmines (bare
assignment, context window required, diff-before-assign) — all routed through
:meth:`UiUtils._apply_region_flags`' rules. Headless (``--background``) is a documented no-op.

``import bpy`` is deferred into the call bodies (no import side effects).
"""

import json
import os
from typing import Dict, Iterable, List, Optional, Set

import pythontk as ptk

from blendertk.core_utils._core_utils import CoreUtils
from blendertk.ui_utils._ui_utils import UiUtils


class _UiStateInternal(object):
    """Pure helpers behind :class:`UiState` — the parts that need no window."""

    @staticmethod
    def _merge_hidden(
        saved: Iterable[str], loaded: Iterable[str], live: Iterable[str]
    ) -> List[str]:
        """The next ``hidden`` set: ``(saved ∪ (loaded − live)) − live``, sorted.

        An editor becomes hidden when it was in the layout the file loaded with and is no longer
        on screen; it stays hidden while off screen (a file opened with *Load UI* off, or a
        layout already applied, has it in neither ``loaded`` nor ``live`` — that must not read
        as "the user re-opened it"); it is released only when it is live again.
        """
        live_set = set(live)
        return sorted((set(saved) | (set(loaded) - live_set)) - live_set)

    _flag_names: Dict[
        str, List[str]
    ] = {}  # RNA struct identifier -> its show_* flag names

    @classmethod
    def _show_flag_names(cls, struct) -> List[str]:
        """Names of every writable ``show_*`` boolean on an RNA struct, read off ``bl_rna`` so a
        new Blender flag is tracked without a code change (and a removed one is simply absent).
        Cached per struct type — the tick reads these every couple of seconds for the session."""
        rna = struct.bl_rna
        names = cls._flag_names.get(rna.identifier)
        if names is None:
            names = [
                p.identifier
                for p in rna.properties
                if p.type == "BOOLEAN"
                and p.identifier.startswith("show_")
                and not p.is_readonly
                and not getattr(p, "is_array", False)
            ]
            cls._flag_names[rna.identifier] = names
        return names

    @classmethod
    def _show_flags(cls, struct) -> Dict[str, bool]:
        """``{flag: value}`` for :meth:`_show_flag_names` of ``struct``."""
        flags = {}
        for name in cls._show_flag_names(struct):
            try:
                flags[name] = bool(getattr(struct, name))
            except (AttributeError, TypeError):
                pass
        return flags

    @staticmethod
    def _resolve(space, dotted: str):
        """``(struct, attr)`` for a ``"overlay.show_floor"`` style key, or ``(None, attr)`` when
        the path does not resolve on this space (flag/sub-struct gone in this Blender)."""
        head, _, attr = dotted.rpartition(".")
        struct = space
        for part in filter(None, head.split(".")):
            struct = getattr(struct, part, None)
            if struct is None:
                return None, attr
        return struct, attr

    @staticmethod
    def _ui_types(screen) -> List[str]:
        return [a.ui_type for a in screen.areas if a.type != "EMPTY"]

    @staticmethod
    def _largest_by_ui_type(screen) -> Dict[str, object]:
        """The biggest area of each ``ui_type`` — the one a snapshot reads from."""
        best = {}
        for area in screen.areas:
            if area.type == "EMPTY":
                continue
            cur = best.get(area.ui_type)
            if cur is None or area.width * area.height > cur.width * cur.height:
                best[area.ui_type] = area
        return best


class UiState(_UiStateInternal):
    """Session-persistent UI visibility state. All entry points are classmethods on a
    class-level singleton state (mirror of ``ScriptConsole``): ``UiState.install()`` from the
    startup script is the whole integration.

    Lifecycle: :meth:`install` captures the loaded layout, applies the saved state to the
    active workspace **synchronously** (so it runs before anything else docks an area — the
    Script Output console's strip must never count as part of the loaded layout; with no
    context window the first tick does it instead) and starts the tick timer; ``load_pre``
    suspends snapshots, ``load_post`` queues a re-apply; the tick applies what is pending,
    captures a newly visited workspace, and otherwise snapshots the active one and writes the
    sidecar only when something changed.

    A **maximized area** (Ctrl+Space / ``toggle_fullscreen_area``) swaps in a temporary
    one-area screen: every tick and apply skips while ``screen.show_fullscreen`` is set, or
    that screen would read as "the user closed every other editor" and be saved as such.
    """

    _STATE_FILE = "blendertk_ui_state.json"
    _state_dir_override: Optional[str] = None  # tests point this at a scratch dir
    #: Sub-structs of a space whose ``show_*`` flags are tracked too (dotted keys in the JSON).
    SUBSTRUCTS = ("overlay", "shading")
    #: Seconds between change-detection ticks (a snapshot is ~150 RNA reads — cheap).
    INTERVAL = 2.0
    VERSION = 1

    _installed = False
    _state: Dict = {}
    _loaded: Dict[str, List[str]] = {}  # workspace -> ui_types in the layout as loaded
    _applied: Set[str] = set()  # workspaces applied this load
    _pending_apply = False
    _suspended = False
    _timer_fn = (
        None  # the ONE callable object registered (timers unregister by identity)
    )
    _handlers = ()  # (handler_list, fn) pairs added, for unregister

    # -- persisted state --------------------------------------------------------------------
    @classmethod
    def state_path(cls) -> Optional[str]:
        """The sidecar path (Blender's user ``CONFIG`` dir), or None when unresolvable."""
        return CoreUtils.user_config_path(cls._STATE_FILE, base=cls._state_dir_override)

    @classmethod
    def load(cls) -> Dict:
        """The saved state (``{"version", "workspaces": {name: {"hidden", "spaces"}}}``);
        an empty skeleton when missing or unreadable."""
        path = cls.state_path()
        state = {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                state = json.load(f)
        except Exception:  # missing / corrupt / unreadable -> nothing to restore
            state = {}
        if not isinstance(state, dict) or not isinstance(state.get("workspaces"), dict):
            state = {}
        state.setdefault("version", cls.VERSION)
        state.setdefault("workspaces", {})
        return state

    @classmethod
    def save(cls, state: Optional[Dict] = None) -> bool:
        """Write ``state`` (default: the live class state) atomically — the tick writes
        while the session runs, and a half-written sidecar must never be what the next
        session restores. Best-effort; never raises."""
        path = cls.state_path()
        if not path:
            return False
        try:
            ptk.FileUtils.atomic_write_text(
                path,
                json.dumps(
                    state if state is not None else cls._state, indent=1, sort_keys=True
                ),
            )
            return True
        except Exception:
            return False

    @classmethod
    def clear(cls) -> bool:
        """Forget the saved state (delete the sidecar) — the "reset to file defaults" path."""
        cls._state = {"version": cls.VERSION, "workspaces": {}}
        path = cls.state_path()
        try:
            if path and os.path.isfile(path):
                os.remove(path)
            return True
        except OSError:
            return False

    # -- snapshot / apply (window-level; headless-safe for the props half) -------------------
    @classmethod
    def snapshot_spaces(cls, screen) -> Dict[str, Dict[str, bool]]:
        """``{ui_type: {flag: value}}`` read from the largest area of each ``ui_type``."""
        out = {}
        for ui_type, area in cls._largest_by_ui_type(screen).items():
            space = area.spaces.active
            if space is None:
                continue
            flags = cls._show_flags(space)
            for sub in cls.SUBSTRUCTS:
                struct = getattr(space, sub, None)
                if struct is not None and hasattr(struct, "bl_rna"):
                    flags.update(
                        {f"{sub}.{k}": v for k, v in cls._show_flags(struct).items()}
                    )
            out[ui_type] = flags
        return out

    @classmethod
    def snapshot_workspace(cls, window, saved: Optional[Dict] = None) -> Dict:
        """The workspace entry for ``window``'s active workspace: ``hidden`` merged per
        :meth:`_merge_hidden` against ``saved`` (default: the class state) and the loaded
        layout, plus :meth:`snapshot_spaces`."""
        name = window.workspace.name
        if saved is None:
            saved = cls._state.get("workspaces", {}).get(name) or {}
        # Flags of an editor that is off screen right now ride along from ``saved`` so they
        # come back with it; the live read wins for everything on screen.
        spaces = dict(saved.get("spaces") or {})
        spaces.update(cls.snapshot_spaces(window.screen))
        return {
            "hidden": cls._merge_hidden(
                saved.get("hidden", ()),
                cls._loaded.get(name, ()),
                cls._ui_types(window.screen),
            ),
            "spaces": spaces,
        }

    @classmethod
    def apply_spaces(cls, screen, spaces: Dict[str, Dict[str, bool]]) -> int:
        """Apply a :meth:`snapshot_spaces` dict to every matching area; returns the number of
        flags changed. Diff-first and bare-assigned — the rules :meth:`UiUtils._apply_region_flags`
        documents (a verbatim re-assign knocked an unrelated region loose; a ``temp_override``
        turns the ``show_region_*`` setters into silent no-ops). Plain ``show_*`` flags have no
        such trap but gain nothing from being re-written either."""
        changed = 0
        for area in list(screen.areas):
            flags = spaces.get(area.ui_type)
            space = area.spaces.active if flags else None
            if space is None:
                continue
            for key, value in flags.items():
                struct, attr = cls._resolve(space, key)
                if struct is None:
                    continue
                try:
                    if getattr(struct, attr) == value:
                        continue
                    setattr(struct, attr, value)
                    changed += 1
                except (
                    AttributeError,
                    TypeError,
                ):  # flag gone / read-only in this Blender
                    pass
        return changed

    @classmethod
    def close_hidden(cls, window, hidden: Iterable[str]) -> int:
        """Close every area of the ``hidden`` ui_types in ``window``; returns the count closed.
        GUI-only (``screen.area_close`` crashes headless) — a no-op under ``--background``."""
        import bpy

        if bpy.app.background:
            return 0
        closed = 0
        for ui_type in set(hidden):
            closed += UiUtils.close_editor(ui_type, window=window)
        return closed

    @classmethod
    def apply_workspace(cls, window, entry: Optional[Dict] = None) -> bool:
        """Apply a workspace entry (default: the saved one for ``window``'s active workspace):
        the space flags first, then the hidden editors are closed — **in that order**. An
        ``area_close`` leaves the context window NULL for the rest of the callback (measured:
        ``bpy.context.window`` reads None right after ``close_area`` returns), and a
        ``show_region_*`` set after it is the ``ED_area_init`` crash; flags on an area that is
        about to close are harmless. Records the loaded layout for the ``hidden`` bookkeeping
        and marks the workspace applied. False when it can't run (no context window — the
        region setters would crash; see ``_apply_region_flags``)."""
        import bpy

        if bpy.context.window is None or window.screen.show_fullscreen:
            return False
        name = window.workspace.name
        cls._loaded[name] = cls._ui_types(window.screen)
        cls._applied.add(name)
        if entry is None:
            entry = cls._state.get("workspaces", {}).get(name)
        if not entry:
            return True
        cls.apply_spaces(window.screen, entry.get("spaces", {}))
        cls.close_hidden(window, entry.get("hidden", ()))
        return True

    # -- lifecycle ---------------------------------------------------------------------------
    @classmethod
    def install(cls) -> bool:
        """Restore the saved state for the active workspace NOW and start tracking. Call from
        the startup script **before** anything docks its own area (the Script Output restore),
        so a strip another mechanism owns never reads as part of the loaded layout — closing
        *it* later would be the tracker taking out the console. Idempotent; False when there is
        nothing to drive (``--background``, no window)."""
        import bpy

        if bpy.app.background:
            return False
        if cls._installed:
            return True
        window = UiUtils.main_window()
        if window is None:
            return False
        cls._state = cls.load()
        cls._loaded, cls._applied = {}, set()
        cls._suspended = False
        # No context window (a windowless timer state) -> the first tick applies instead.
        cls._pending_apply = not cls.apply_workspace(window)

        handlers = bpy.app.handlers
        wired = []
        for name, fn in (
            ("load_pre", cls._on_load_pre),
            ("load_post", cls._on_load_post),
            ("load_post_fail", cls._on_load_post_fail),  # 4.1+; absent = skipped
        ):
            handler_list = getattr(handlers, name, None)
            if handler_list is not None:
                fn = handlers.persistent(fn)
                handler_list.append(fn)
                wired.append((handler_list, fn))
        cls._handlers = tuple(wired)
        cls._timer_fn = cls._tick
        bpy.app.timers.register(
            cls._timer_fn, first_interval=cls.INTERVAL, persistent=True
        )
        cls._installed = True
        return True

    @classmethod
    def uninstall(cls) -> None:
        """Stop tracking (timer + handlers). The sidecar keeps whatever was last written."""
        import bpy

        for handler_list, fn in cls._handlers:
            try:
                handler_list.remove(fn)
            except ValueError:
                pass
        cls._handlers = ()
        if cls._timer_fn is not None:
            try:
                bpy.app.timers.unregister(cls._timer_fn)
            except ValueError:
                pass
            cls._timer_fn = None
        cls._installed = False

    @staticmethod
    def _on_load_pre(*_args):
        # A file load replaces the screen; a snapshot taken mid-load would record the half
        # state as the user's. Hold until load_post's re-apply has run.
        UiState._suspended = True

    @staticmethod
    def _on_load_post(*_args):
        # The loaded file's own layout just won (Load UI). Re-apply the saved state so the
        # user's preference outranks the file — the Maya model — on the next tick, once the
        # new screen is drawable.
        UiState._pending_apply = True
        UiState._suspended = False

    @staticmethod
    def _on_load_post_fail(*_args):
        # The load failed and the old screen stands: nothing to re-apply, resume snapshots.
        UiState._suspended = False

    @classmethod
    def _tick(cls):
        import bpy

        if not cls._installed or bpy.app.background:
            return None  # stop the timer
        try:
            window = UiUtils.main_window()
            if window is None or cls._suspended or window.screen.show_fullscreen:
                return cls.INTERVAL
            if cls._pending_apply:
                cls._loaded, cls._applied = {}, set()
                if cls.apply_workspace(window):  # else: no context window yet — retry
                    cls._pending_apply = False
                return cls.INTERVAL
            name = window.workspace.name
            if name not in cls._applied:  # first visit this load: apply, don't snapshot
                cls.apply_workspace(window)
                return cls.INTERVAL
            entry = cls.snapshot_workspace(window)
            if entry != cls._state["workspaces"].get(name):
                cls._state["workspaces"][name] = entry
                cls.save()
        except Exception as error:  # never let the tracker take the timer loop down
            print(f"{__name__}: tick skipped: {error!r}")
        return cls.INTERVAL
