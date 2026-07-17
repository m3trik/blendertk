# !/usr/bin/python
# coding=utf-8
"""UI utilities — opening Blender editors (the analogue of Maya's editor-window mel commands).

Blender has no floating editor windows per se: an editor is an *area* ``ui_type`` inside a
window. ``open_editor`` opens a new OS window (``wm.window_new``) and switches its area to the
requested editor — the closest Blender-idiomatic match to "open the X editor". **GUI-only**
(``--background`` has no windows), so these are exercised by the GUI harnesses, not headless.

``import bpy`` is deferred into the call bodies (no import side effects).
"""
from blendertk.core_utils._core_utils import window_context_override

# Friendly editor name -> Area.ui_type enum (Blender 4.x/5.x).
EDITOR_TYPES = {
    "3D Viewport": "VIEW_3D",
    "Image Editor": "IMAGE_EDITOR",
    "UV Editor": "UV",
    "Shader Editor": "ShaderNodeTree",
    "Compositor": "CompositorNodeTree",
    "Geometry Nodes": "GeometryNodeTree",
    "Video Sequencer": "SEQUENCE_EDITOR",
    "Movie Clip Editor": "CLIP_EDITOR",
    "Dope Sheet": "DOPESHEET",
    "Timeline": "TIMELINE",
    "Graph Editor": "FCURVES",
    "Drivers": "DRIVERS",
    "NLA Editor": "NLA_EDITOR",
    "Text Editor": "TEXT_EDITOR",
    "Python Console": "CONSOLE",
    "Info Log": "INFO",
    "Outliner": "OUTLINER",
    "Properties": "PROPERTIES",
    "File Browser": "FILES",
    "Asset Browser": "ASSETS",
    "Spreadsheet": "SPREADSHEET",
    "Preferences": "PREFERENCES",
}


def get_editor_types():
    """The friendly-name → ``Area.ui_type`` map understood by :func:`open_editor`."""
    return dict(EDITOR_TYPES)


def _close_window(window):
    """Close ``window`` via ``wm.window_close``; True if closed.

    Private (YAGNI): only :func:`open_editor`'s failure path unwinds a window it opened — the
    ``close_area`` / ``close_editor`` pair is the public surface for closing *areas*. Runs under
    an explicit override for the same reason ``open_editor`` does (``window_close``'s poll reads
    ``context.window``, which is ``None`` under tentacle's Qt event-pump timer).
    """
    import bpy

    if window is None:
        return False
    try:
        with bpy.context.temp_override(window=window):
            res = bpy.ops.wm.window_close()
    except (RuntimeError, TypeError, ReferenceError):
        return False
    return res == {"FINISHED"}


def open_editor(editor, properties_context=None):
    """Open ``editor`` (a friendly name from :data:`EDITOR_TYPES` or a raw ``ui_type``)
    in a new window. Returns the new window, or None when it could not be opened.

    Preferences routes through ``screen.userpref_show`` (Blender's own dedicated path);
    everything else duplicates the current window and switches its area's ``ui_type``.
    ``properties_context`` (only when opening the Properties editor — e.g. ``"VIEW_LAYER"``,
    ``"OBJECT"``, ``"RENDER"``) selects which Properties tab is shown.

    Both window ops run under :func:`window_context_override`: their poll reads ``context.window``,
    which is ``None`` when tentacle drives a slot from its Qt event-pump timer (``window_new.poll()
    failed, context is incorrect``). Neither branch trusts ``windows[-1]`` to be what it opened —
    an op that opened nothing leaves ``[-1]`` pointing at an unrelated window (the *main* one, in
    the common single-window case), so the returned window is identified positively instead: by
    pointer diff, or by carrying the editor. Callers branch on the ``None`` (e.g. the channels
    slots' "could not open" message), so a wrong window is worse than no window — and an unknown
    ``ui_type`` takes the window we just opened back down with it, rather than stranding a
    duplicate viewport on screen behind a "could not open" message.
    """
    import bpy

    ui_type = EDITOR_TYPES.get(editor, editor)
    if ui_type == "PREFERENCES":
        try:
            with window_context_override():
                bpy.ops.screen.userpref_show()
        except RuntimeError:
            return None
        # userpref_show re-focuses an ALREADY-open Preferences window rather than appending a
        # new one, so match on the editor, not on position.
        return next(
            (w for w in reversed(list(bpy.context.window_manager.windows))
             if any(a.ui_type == "PREFERENCES" for a in w.screen.areas)),
            None,
        )
    before = {w.as_pointer() for w in bpy.context.window_manager.windows}
    try:
        with window_context_override():
            bpy.ops.wm.window_new()
    except RuntimeError:  # poll refused (no window at all, fullscreen screen, --background)
        return None
    new = [w for w in bpy.context.window_manager.windows if w.as_pointer() not in before]
    if not new:
        return None
    window = new[0]
    area = window.screen.areas[0]
    try:
        area.ui_type = ui_type
    except TypeError:  # unknown enum for this Blender version / a typo'd name
        _close_window(window)  # we opened it; don't strand a duplicate viewport
        return None
    if properties_context and ui_type == "PROPERTIES":
        try:
            area.spaces.active.context = properties_context
        except (TypeError, AttributeError):
            pass  # tab not available in this Blender version
    return window


def main_window():
    """The main Blender window (the first; ``wm.window_new`` appends, so ``[0]`` stays main)."""
    import bpy

    wins = bpy.context.window_manager.windows
    return wins[0] if wins else None


def _window_region(area):
    """The ``WINDOW`` (content) region of ``area`` — the region an area-op override needs."""
    return next((r for r in area.regions if r.type == "WINDOW"), None)


def find_editor(editor, window=None):
    """Open areas showing ``editor`` (friendly name or raw ``ui_type``) as ``(window, area)`` pairs.

    Matches on ``area.ui_type``, NOT ``area.type`` — a Timeline and a Dope Sheet share
    ``area.type == "DOPESHEET_EDITOR"`` and are told apart only by ``ui_type`` (``"TIMELINE"``
    vs ``"DOPESHEET"``). Scans the main window by default (where the dockable strips live); pass
    ``window`` to scope elsewhere.
    """
    ui_type = EDITOR_TYPES.get(editor, editor)
    win = window or main_window()
    if win is None:
        return []
    return [(win, a) for a in win.screen.areas if a.ui_type == ui_type]


def close_area(window, area):
    """Close exactly ``area`` in ``window`` via ``screen.area_close``; True if closed.

    The single-area primitive behind :func:`close_editor` (which closes every area of a
    ui_type) — used directly by callers that track ONE specific area of their own (e.g. the
    Script Output console's docked anchor) and must not take out an unrelated same-typed area
    a caller didn't create (a user's own manually-opened Info Log area, say). Never closes a
    window's only area.
    """
    import bpy

    if area is None or window is None:
        return False
    try:
        if len(window.screen.areas) <= 1:
            return False
        with bpy.context.temp_override(
            window=window, screen=window.screen, area=area, region=_window_region(area)
        ):
            res = bpy.ops.screen.area_close()
    except (RuntimeError, TypeError, ReferenceError):
        return False
    return res == {"FINISHED"}


def close_editor(editor, window=None):
    """Close every open area showing ``editor`` in the (main) window; returns the count closed.

    The stateless "hide" half of :func:`toggle_editor` — repeatedly finds a match and closes it
    via :func:`close_area` (re-querying between closes, since an ``area_close`` invalidates the
    other area pointers). Prefer :func:`close_area` directly when you're tracking one specific
    area of your own — this closes EVERY area of that type, which can take out an unrelated one.
    """
    closed = 0
    for _ in range(16):  # safety cap; normally exactly one docked strip
        matches = find_editor(editor, window=window)
        if not matches:
            break
        win, area = matches[0]
        if not close_area(win, area):
            break
        closed += 1
    return closed


def dock_editor(editor, edge_size=70, window=None):
    """Dock ``editor`` as a strip along the bottom of the main 3D viewport — the Blender analogue
    of Maya docking its Time Slider at the bottom of the main window.

    Splits the tallest ``VIEW_3D`` in the (main) ``window`` horizontally and switches the new
    bottom strip to ``editor``. Stateless: the strip is re-created at a fixed bottom edge each
    time (Maya's time slider is likewise always at the bottom), so nothing has to be remembered
    across toggles. Returns the docked area, or ``None`` when there's no viewport to dock into /
    the split failed — callers may fall back to :func:`open_editor` (a separate window).
    """
    import bpy

    ui_type = EDITOR_TYPES.get(editor, editor)
    win = window or main_window()
    if win is None:
        return None
    view3ds = [a for a in win.screen.areas if a.type == "VIEW_3D"]
    if not view3ds:
        return None
    target = max(view3ds, key=lambda a: a.height)
    if target.height <= edge_size:
        return None
    factor = max(0.05, min(0.9, edge_size / target.height))
    before = {a.as_pointer() for a in win.screen.areas}
    try:
        with bpy.context.temp_override(
            window=win, screen=win.screen, area=target, region=_window_region(target)
        ):
            res = bpy.ops.screen.area_split(direction="HORIZONTAL", factor=factor)
    except (RuntimeError, TypeError):
        return None
    if res != {"FINISHED"}:
        return None
    # Both halves inherit VIEW_3D; the bottom one (min y) becomes the docked strip, leaving the
    # viewport on top — robust to which half Blender treats as "new".
    halves = [a for a in win.screen.areas if a.as_pointer() not in before] + [target]
    bottom = min(halves, key=lambda a: a.y)
    try:
        bottom.ui_type = ui_type
    except TypeError:
        return None
    return bottom


def toggle_editor(editor, edge_size=70, window=None):
    """Maya-style *docked* toggle for ``editor`` (backs the editors-menu Time & Range button).

    If ``editor`` is open in the main window, close it in place; otherwise dock it as a bottom
    strip (:func:`dock_editor`). Mirrors the feel of Maya's ``ToggleTimeSlider`` /
    ``ToggleRangeSlider`` — the docked editor toggles in place and never spawns a floating
    window. Returns ``True`` if now shown, ``False`` if now hidden.

    Why this lives in blendertk while mayatk has no ``toggle_editor``: Maya toggles main-window
    *chrome* with a single ``mel.eval("ToggleTimeSlider")``; Blender editors are tiled *areas*
    with no "hidden" state, so the same result takes several ``bpy.ops.screen`` steps — exactly
    the reason :func:`open_editor` exists as a helper too. Falls back to a separate
    :func:`open_editor` window only when there's no viewport to dock into.
    """
    win = window or main_window()
    if find_editor(editor, window=win):
        close_editor(editor, window=win)
        return False
    if dock_editor(editor, edge_size=edge_size, window=win) is None:
        # open_editor returns None on poll refusal (windowless Qt-timer
        # context, --background, fullscreen screen) — report THAT, not a
        # blanket True, or the caller's UI state desyncs from reality.
        return open_editor(editor) is not None
    return True


def _window_bars_hidden(window):
    """True when ``window``'s tiled areas reach its very top — i.e. the topbar row is gone.

    The topbar/statusbar *global areas* have no RNA presence at all (not in ``screen.areas``,
    no Space type, no ``show_topbar`` flag — probed on 5.1.2), so their visibility can only be
    read back geometrically: with the topbar visible the topmost area stops one header-height
    (~26 px at 1× UI scale) short of ``window.height``; hidden, it reaches within a pixel or
    two of it. The 4 px threshold is safely inside both regimes at any UI scale.
    """
    top = max((a.y + a.height for a in window.screen.areas), default=0)
    return window.height - top <= 4


def _biggest_area(win):
    """``win``'s largest area — the one a fullscreen toggle acts on by default."""
    return max(win.screen.areas, key=lambda a: a.width * a.height)


def _region_flags(area):
    """Every ``show_region_*`` flag of ``area``'s active space, as a plain dict. The set is
    per-space-type (a VIEW_3D has ``toolbar``/``tool_header``/``asset_shelf``, an OUTLINER only
    ``header``), so it's read off the RNA rather than hardcoded."""
    space = area.spaces.active
    return {
        p.identifier: getattr(space, p.identifier)
        for p in space.bl_rna.properties
        if p.identifier.startswith("show_region_")
    }


def _apply_region_flags(area, flags):
    """Restore a :func:`_region_flags` snapshot onto ``area``'s active space.

    Assigns **only the flags that actually differ**, for two measured reasons: every assignment
    fires ``ED_region_visibility_change_update`` → ``ED_area_init`` (a full area re-init), and
    re-writing an already-correct flag was an observed way to knock a *different* region's
    state loose — restoring a snapshot verbatim flipped an untouched ``show_region_ui`` on.

    Assigned **bare**, deliberately not under a ``temp_override``: the ``show_region_*`` setters
    silently no-op inside one on 5.1 (measured — the value reads back unchanged from within the
    override), so an override here would turn the restore into a no-op and hand the caller a
    side effect it explicitly promises not to have.

    **Precondition: a context window must exist** (callers check) — the ``ED_area_init`` above
    dereferences ``CTX_wm_window``, and with none it is a hard crash, the landmine
    ``QtDock._apply_area_header`` documents.
    """
    space = area.spaces.active
    if space is None:
        return
    stale = {
        name: value
        for name, value in flags.items()
        if getattr(space, name, value) != value
    }
    for name, value in stale.items():
        try:
            setattr(space, name, value)
        except (AttributeError, TypeError):  # flag gone in this Blender version
            pass


def _fullscreen_toggle(win, hide_panels, area=None):
    """Invoke ``screen.screen_full_area`` on ``area`` (default: ``win``'s biggest area) and
    return the operator result. Used both to enter and for the state-matched exit."""
    import bpy

    if area is None:
        area = _biggest_area(win)
    with bpy.context.temp_override(
        window=win, screen=win.screen, area=area, region=_window_region(area)
    ):
        return bpy.ops.screen.screen_full_area(use_hide_panels=hide_panels)


def toggle_fullscreen_area(editor=None, hide_panels=True, window=None):
    """Toggle fullscreen-area mode — one editor fills the window (Ctrl+Alt+Space).

    If the (main) ``window`` is tiled, focus ``editor`` (friendly name or raw ``ui_type``;
    default = the largest area, normally the 3D viewport) — with ``hide_panels`` (default) the
    area covers the ENTIRE window, topbar and statusbar included; ``hide_panels=False`` is the
    Ctrl+Space "maximize" variant (the global bars stay). If already fullscreen, restore the
    previous tiled layout. The exit call must re-pass the props of the state being exited
    (``screen_full_area`` toggles per-state; a mismatched call is a no-op — probed on 5.1.2,
    both directions), and the state is only *guessable* geometrically (a maximize entered with
    the bars hidden covers the full window, same as ``SCREENFULL``) — so the restore branch
    tries the measured guess first and, because a mismatch is a no-op, safely retries with the
    other prop if the window is still fullscreen. Returns True if now fullscreen, False if now
    tiled (or nothing to do). **GUI-only** like :func:`call_native_menu` (``--background`` has
    no window chrome); returns False headless.

    To hide the bars WITHOUT giving up the tiled layout, see :func:`toggle_window_bars`.
    """
    import bpy

    if bpy.app.background:
        return False
    win = window or main_window()
    if win is None:
        return False
    scr = win.screen
    if scr.show_fullscreen:
        guess = _window_bars_hidden(win)
        for hide in (guess, not guess):
            try:
                _fullscreen_toggle(win, hide_panels=hide)
            except (RuntimeError, TypeError):
                pass
            if not win.screen.show_fullscreen:
                break
        return win.screen.show_fullscreen
    areas = list(scr.areas)
    if editor is not None:
        ui_type = EDITOR_TYPES.get(editor, editor)
        areas = [a for a in areas if a.ui_type == ui_type]
    if not areas:
        return False
    target = max(areas, key=lambda a: a.width * a.height)
    try:
        res = _fullscreen_toggle(win, hide_panels, area=target)
    except (RuntimeError, TypeError):
        return False
    return res == {"FINISHED"}


def toggle_window_bars(visible=None, window=None):
    """Show/hide the main window's topbar (File/Edit/Render… menus + workspace tabs) and
    statusbar while KEEPING the tiled editor layout — the areas expand into the space.

    Blender exposes no switch for the topbar (it's a *global area*: not in ``screen.areas``,
    no Space type, no ``show_topbar`` flag), so this drives the one native path that reaches
    it, round-tripping through fullscreen-area mode without an intervening redraw (no visible
    flash): entering ``SCREENFULL`` hides the global bars, and exiting via
    ``screen.back_to_previous`` restores the tiled layout while LEAVING them hidden, whereas
    exiting via ``screen.screen_full_area`` (the state-matched toggle) un-hides them — both
    exits verified on 5.1.2 (``fullscreen_area_gui_check.py`` pins the behavior per Blender
    version). The hidden state is stable across workspace switches, and the two bars ride
    together — the statusbar's own ``screen.show_statusbar`` flag cannot resurrect it while
    the topbar is hidden (probed).

    Only the bars move: ``use_hide_panels`` also hides the *fullscreened area's own* header
    and toolbar, and the ``back_to_previous`` exit — unlike the state-matched one — does not
    put them back (probed), so the round-trip would otherwise leave the viewport's panels
    collapsed as a side effect. The area's ``show_region_*`` flags are therefore snapshotted
    and restored around it, keeping this strictly a bars-only operation.

    ``visible``: True shows, False hides, None (default) toggles. Returns whether the bars
    are now visible (measured back, so a future Blender changing the ``back_to_previous``
    behavior degrades to a truthful no-op) — or None when there is nothing to drive
    (headless; no window; the window is in fullscreen-area mode, which manages the bars
    itself; or there is no **context** window, see below).
    """
    import bpy

    if bpy.app.background:
        return None
    win = window or main_window()
    if win is None or win.screen.show_fullscreen:
        return None
    # All-or-nothing: undoing the round-trip's panel side effect needs a context window (the
    # restore assigns bare — see _apply_region_flags), and without one the bars would hide
    # while the viewport's header/toolbar stayed collapsed — measured, and reported as a clean
    # success. A caller with no context window (the Qt event-pump timer this package's macros
    # can run in) gets an honest "not drivable" instead of a half-applied UI.
    if bpy.context.window is None:
        return None
    hidden = _window_bars_hidden(win)
    visible = hidden if visible is None else bool(visible)
    if visible == (not hidden):
        return visible
    # The layout restores byte-exact, so the biggest area before the round-trip is the same
    # one after — the area whose panels use_hide_panels collapses.
    saved = _region_flags(_biggest_area(win))
    try:
        _fullscreen_toggle(win, hide_panels=True)  # enter SCREENFULL (hides the bars)
        if visible:
            _fullscreen_toggle(win, hide_panels=True)  # state-matched exit: bars come back
        else:
            with bpy.context.temp_override(window=win, screen=win.screen):
                bpy.ops.screen.back_to_previous()  # exit that KEEPS the bars hidden
    except (RuntimeError, TypeError):
        pass
    finally:
        if win.screen.show_fullscreen:  # never strand the window in fullscreen mode
            try:
                _fullscreen_toggle(win, hide_panels=True)
            except (RuntimeError, TypeError):
                pass
        if not win.screen.show_fullscreen:
            _apply_region_flags(_biggest_area(win), saved)
    return not _window_bars_hidden(win)


def menu_exists(menu_idname):
    """True if ``menu_idname`` (e.g. ``"VIEW3D_MT_add"``) is a registered Blender menu.

    Cheap, runtime-only validity check backing the no-dead-links guard on the both-button menu —
    the analogue of validating an editor name against :func:`get_editor_types`.
    """
    import bpy

    return hasattr(bpy.types, menu_idname)


def dispatch_log_link(url, logger=None) -> bool:
    """Handle ``action://`` links emitted by ``logger.log_link()`` in a QTextBrowser.

    Blender port of mayatk's ``UiUtils.dispatch_log_link`` — the log panel of a Switchboard tool
    (e.g. Telescope Rig) renders object names as clickable links; clicking one dispatches here.

    Supported actions:
        ``open``   — open *path* (file or directory) in the OS shell.
                     Accepts ``?path=`` (canonical) or ``?filepath=`` (legacy).
        ``select`` — select *node* (an object name) in the viewport, making it active.
        ``reveal`` — select *node* and frame it in the 3D viewport (Blender has no scripted
                     Outliner "reveal" like Maya's ``showSelected``; framing the viewport is the
                     closest visible feedback).

    Parameters:
        url:    A ``QUrl`` from ``QTextBrowser.anchorClicked``.
        logger: Optional logger for debug/warning messages.

    Returns:
        True if the link was handled, False otherwise.
    """
    import os
    from urllib.parse import parse_qs

    if url.scheme() != "action":
        return False

    action = url.host()
    params = parse_qs(url.query())

    # Non-node actions -------------------------------------------------
    if action == "open":
        # Accept ``path`` (canonical) and ``filepath`` as a back-compat fallback.
        filepath = params.get("path", [""])[0] or params.get("filepath", [""])[0]
        if not filepath:
            return False
        try:
            os.startfile(filepath)
        except OSError as e:
            if logger:
                logger.warning(f"Could not open file: {e}")
            return False
        return True

    # Node-based actions need bpy; defer the import so the ``open`` branch above stays usable
    # in non-Blender contexts.
    import bpy

    node = params.get("node", [""])[0]
    if not node:
        return False

    # bpy.data.objects.get(name) is ambiguous when a linked library object shares the exact same
    # name string as a local one (object names are only unique per-library, not globally) — a
    # "select"/"reveal" link always means the CURRENT scene's object, so prefer a local match
    # (e.g. Hierarchy Sync's diff log links, where a linked reference object commonly shares
    # a name with the local object it's being diffed against).
    obj = next((o for o in bpy.data.objects if o.name == node and o.library is None), None) or bpy.data.objects.get(node)
    if obj is None:
        if logger:
            logger.warning(f"Object not found: {node}")
        return False

    if action in ("select", "reveal"):
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        if action == "reveal":
            import blendertk as btk

            ctx = btk.get_view3d_context()
            if ctx and ctx.get("region"):
                try:
                    with bpy.context.temp_override(**ctx):
                        bpy.ops.view3d.view_selected()
                except RuntimeError:
                    pass
        return True

    if logger:
        logger.debug(f"Unknown log link action: {action}")
    return False


def call_native_menu(menu_idname):
    """Pop Blender's own native menu ``menu_idname`` (e.g. ``"VIEW3D_MT_add"``) at the cursor.

    Invokes Blender's **real** menu via ``bpy.ops.wm.call_menu`` under a VIEW_3D override —
    the popup manages its own lifetime (click / Esc dismisses). The both-button chord menu no
    longer routes through this (it hosts a *harvested Qt clone* in a pinnable window instead —
    see ``ui_utils.menu_harvest``); this stays the one-liner for popping a transient native
    menu at the cursor. **GUI-only** (``--background`` has no window to pop into). Returns the
    operator result set, or ``None`` for an unknown menu / no 3D viewport.
    """
    import bpy
    import blendertk as btk

    # GUI-only: there is no window to pop a menu into headless, and ``wm.call_menu`` faults
    # natively under ``--background`` (EXCEPTION_ACCESS_VIOLATION) — guard before touching it.
    if bpy.app.background:
        return None
    if not hasattr(bpy.types, menu_idname):
        return None
    ctx = btk.get_view3d_context()
    if not ctx or not ctx.get("region"):
        return None
    with bpy.context.temp_override(**ctx):
        return bpy.ops.wm.call_menu("INVOKE_DEFAULT", name=menu_idname)


def popup_message(text, title="Info", icon="INFO"):
    """Show a small native Blender info popup at the cursor (multi-line ``text`` supported).

    The transient, Blender-idiomatic way to tell the user why an action was skipped — e.g. the
    chord menu popping a hint instead of a mode-gated native menu. **GUI-only** like
    :func:`call_native_menu`; headless it logs to the console instead. Runs under a VIEW_3D
    override when the calling context has no window (``bpy.app.timers`` callbacks).
    """
    import bpy
    import blendertk as btk

    if not bpy.app.background:
        lines = [ln for ln in str(text).splitlines() if ln.strip()]

        def _draw(popup, _context):
            for ln in lines:
                popup.layout.label(text=ln)

        wm = bpy.context.window_manager
        if bpy.context.window is not None:
            return wm.popup_menu(_draw, title=title, icon=icon)
        ctx = btk.get_view3d_context()
        if ctx and ctx.get("window"):
            with bpy.context.temp_override(**ctx):
                return wm.popup_menu(_draw, title=title, icon=icon)
    # Headless, or no window to pop into — degrade to the console.
    print(f"{title}: {text}")
    return None


class UiUtils:
    """Namespace mirror (helpers also exposed module-level)."""

    get_editor_types = staticmethod(get_editor_types)
    open_editor = staticmethod(open_editor)
    find_editor = staticmethod(find_editor)
    close_area = staticmethod(close_area)
    close_editor = staticmethod(close_editor)
    dock_editor = staticmethod(dock_editor)
    toggle_editor = staticmethod(toggle_editor)
    toggle_fullscreen_area = staticmethod(toggle_fullscreen_area)
    toggle_window_bars = staticmethod(toggle_window_bars)
    main_window = staticmethod(main_window)
    menu_exists = staticmethod(menu_exists)
    call_native_menu = staticmethod(call_native_menu)
    popup_message = staticmethod(popup_message)
    dispatch_log_link = staticmethod(dispatch_log_link)
