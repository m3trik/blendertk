# !/usr/bin/python
# coding=utf-8
"""Match Blender's app UI chrome to another DCC's look using Blender's NATIVE theme-preset system.

A "style"/"template" here is a Blender-native ``interface_theme`` preset — an ``.xml`` in Blender's
own format (the one ``Preferences > Themes > Export/Install`` produces, written by
``bpy.ops.wm.interface_theme_preset_add``). Everything is native, with no side-channel of our own:

* :func:`install` copies the shipped preset(s) into Blender's per-user preset dir, where they appear
  in the **Preferences > Themes** dropdown next to Blender's built-in and the user's own themes.
* :func:`list_templates` enumerates that same set (Blender's built-ins + the user's own + our
  injected ``Maya``) — so a UI combo built from it *is* Blender's own Themes list.
* :func:`apply_template` applies one via the same ``bpy.ops.script.execute_preset`` the dropdown uses
  (which resets to the factory theme and *then* applies, so the outcome is deterministic regardless of
  the theme that was active before).

Shipped in ``styles/`` next to this module (dir name kept in step with mayatk's `style_setter`
for parity — both packages ship their styles under ``styles/``):

* ``Maya.xml`` — a full theme matching Maya 2025's Qt look (its ``adskdarkflatui`` native QStyle).
  The colors were sampled empirically from a live Maya instance's ``QApplication.palette()`` (see the
  package ``CHANGELOG`` / the ``reference_blender_dcc_style_matching`` memo), not guessed.

Reverting to the user's own look needs no bespoke backup: Blender's own built-in themes (and the
user's own saved presets, if any) already sit in the same preset dirs :func:`list_templates` scans,
so re-selecting one is exactly how Blender's native dropdown reverts too.

Scope boundary: Blender's icon set and structural layout (docked-panel model, Properties-editor tabs)
have no Maya equivalent and are untouched — this only recolors chrome both apps share. The one part of
Maya's look a native theme preset *cannot* carry — ``preferences.view.font_path_ui`` (the UI font file
path; theme XML covers the ``Theme`` + ``ThemeStyle`` structs but not the font on ``preferences.view``)
— is deliberately left as native-or-nothing: there is **no supplement side-channel**, so the tool stays
a pure mirror of Blender's own theme system (a theme picked from Blender's native dropdown behaves
identically to one picked through us).

``import bpy`` is deferred into the call bodies (no import side effects).
"""
import os
import glob
import filecmp

import pythontk as ptk

_HERE = os.path.dirname(__file__)
# Shipped styles live under ``styles/`` — same dir name as mayatk's ``style_setter/styles/`` (parity
# with the ``StyleSetter`` / ``list_styles`` / ``set_style`` vocabulary), even though the file format
# differs (native Blender theme ``.xml`` here vs bespoke ``.json`` there — see the module docstring).
STYLES_DIR = os.path.join(_HERE, "styles")

# Blender's preset category (under the per-user scripts dir) + the menu ``execute_preset`` drives
# for themes.
_PRESET_SUBDIR = "presets/interface_theme"
_PRESET_MENU = "USERPREF_MT_interface_theme_presets"


# ---- shipped-template discovery ------------------------------------------------------------
def list_styles():
    """Names of the shipped theme presets (e.g. ``["Maya"]``)."""
    return sorted(os.path.splitext(os.path.basename(p))[0] for p in glob.glob(os.path.join(STYLES_DIR, "*.xml")))


def _shipped_xml(name):
    return os.path.join(STYLES_DIR, f"{name}.xml")


# ---- user-preset-dir plumbing (what Blender's dropdown reads from) -------------------------
def user_preset_dir(create=False):
    """Blender's per-user ``presets/interface_theme`` dir — the dropdown's writable source."""
    import bpy

    return bpy.utils.user_resource("SCRIPTS", path=_PRESET_SUBDIR, create=create)


def user_preset_path(name):
    """Path a preset named ``name`` would have in the user preset dir."""
    return os.path.join(user_preset_dir(), f"{name}.xml")


def is_installed(name):
    """True if ``<name>.xml`` is in the user preset dir (i.e. selectable in the dropdown)."""
    return os.path.isfile(user_preset_path(name))


# Legacy artifacts from the pre-native backup/restore design retired 2026-07-05 (see the package
# CHANGELOG / test_style_setter.py): it saved the user's own look as ``Default_Backup.json`` and
# wrote the Maya theme into the dropdown as a ``Default`` preset. Both linger in the user preset
# dir, where :func:`list_templates` surfaces "Default" as a phantom duplicate of Maya. The current
# native-only design never creates them, so :func:`install` clears them on upgrade; a fresh user
# never had them.
_LEGACY_BACKUP_SIDECAR = "Default_Backup.json"
_LEGACY_DEFAULT_PRESET = "Default.xml"


def _is_shipped_copy(path):
    """True if ``path`` is byte-identical to one of our shipped style XMLs — i.e. it's our stale
    copy, not a user's own theme that merely shares the name. Silent on ``OSError`` (a file
    vanishing mid-check just yields False)."""
    for name in list_styles():
        shipped = _shipped_xml(name)
        try:
            if os.path.isfile(shipped) and filecmp.cmp(path, shipped, shallow=False):
                return True
        except OSError:
            continue
    return False


def _purge_legacy_default_preset():
    """Remove the retired ``Default`` preset + its ``Default_Backup.json`` sidecar from the user
    preset dir (see :data:`_LEGACY_DEFAULT_PRESET`).

    The ``.xml`` is removed only when it's a shipped-style copy (:func:`_is_shipped_copy`) — never
    a user's own theme that merely happens to be named "Default". The ``.json`` sidecar is ours
    unambiguously (Blender never writes ``.json`` into a preset dir). Best-effort and silent: a
    missing file or an ``OSError`` just leaves the dropdown as-is."""
    d = user_preset_dir()
    if not d or not os.path.isdir(d):
        return
    # (filename, guard) — the guard gates removal of a same-named user theme; None = always ours.
    for filename, guard in ((_LEGACY_BACKUP_SIDECAR, None), (_LEGACY_DEFAULT_PRESET, _is_shipped_copy)):
        path = os.path.join(d, filename)
        if not os.path.isfile(path) or (guard and not guard(path)):
            continue
        try:
            os.remove(path)
        except OSError:
            pass


def install(overwrite=False):
    """Copy the shipped theme presets into Blender's user preset dir so they appear in
    Preferences > Themes > preset dropdown. Idempotent; returns the names copied this call
    (empty when everything was already installed and ``overwrite`` is False)."""
    _purge_legacy_default_preset()  # drop the retired 'Default' phantom (pre-2026-07-05 builds)
    dest = user_preset_dir(create=True)
    copied = []
    for name in list_styles():
        target = os.path.join(dest, f"{name}.xml")
        if os.path.exists(target) and not overwrite:
            continue
        ptk.FileUtils.copy_file(_shipped_xml(name), dest, overwrite=True)
        copied.append(name)
    return copied


# ---- the full native template set (built-in + user + our injected) -------------------------
def list_templates():
    """Ordered ``{display_name: filepath}`` of every native ``interface_theme`` preset the Themes
    dropdown sees — Blender's built-ins, the user's own, and our injected ``Maya``.
    ``display_name`` is Blender's own (``bpy.path.display_name``: underscores → spaces,
    title-cased), so a combo built from this reads identically to the native dropdown. ``filepath``
    is the token to hand back to :func:`apply_template`."""
    import bpy

    out = {}
    for d in bpy.utils.preset_paths("interface_theme"):
        if not os.path.isdir(d):
            continue
        for f in sorted(os.listdir(d)):
            if f.endswith(".xml"):
                stem = os.path.splitext(f)[0]
                out[bpy.path.display_name(stem)] = os.path.join(d, f)
    return out


def apply_template(filepath):
    """Apply any native theme preset by its ``.xml`` filepath (the token from :func:`list_templates`)
    via Blender's ``execute_preset`` — which resets to the factory theme and then applies, so the
    outcome is deterministic regardless of the current theme."""
    import bpy

    bpy.ops.script.execute_preset(filepath=filepath, menu_idname=_PRESET_MENU)
    _redraw_all()


def apply_theme_preset(name):
    """Apply a shipped/installed theme preset by NAME (not path). Uses the installed copy if present,
    else the shipped file directly; raises ``FileNotFoundError`` for an unknown name."""
    path = user_preset_path(name) if is_installed(name) else _shipped_xml(name)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"No theme preset named {name!r} (looked in {STYLES_DIR} and the user preset dir).")
    apply_template(path)


def _redraw_all():
    import bpy

    try:
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                area.tag_redraw()
    except Exception:
        pass


def set_style(name, install_presets=True, persist=False):
    """Switch Blender's UI to the named shipped style (e.g. ``"Maya"``) via its native theme preset.

    Installs the shipped presets into the dropdown first (so e.g. "Maya" is pickable there by hand
    too). Reverting is just picking the user's own (built-in or saved) theme from that same
    dropdown/combo afterward — Blender's native preset system already preserves it, so there is no
    bespoke backup step here.

    Parameters:
        name: A shipped style from :func:`list_styles` (e.g. ``"Maya"``).
        install_presets: Copy the shipped presets into the dropdown first (default True).
        persist: Also write Blender's user preferences to disk (``wm.save_userpref``) so the change
            survives a restart — otherwise it's live-session only.
    """
    if install_presets:
        install()
    apply_theme_preset(name)
    if persist:
        import bpy

        bpy.ops.wm.save_userpref()


class StyleSetter:
    """Public namespace for the style-setter helpers (``btk.StyleSetter.set_style("Maya")`` …).

    This class is the registered public surface (mirroring how the other blendertk tool classes —
    ``Bevel``/``Bridge``/``Selection`` — are exposed as just the class, not a spray of generically-
    named module functions into the flat ``btk.*`` namespace).

    :func:`list_templates` + :func:`apply_template` are the pair a UI theme-selector combo drives —
    ``list_templates()`` gives it Blender's whole native Themes list (built-in + user + our injected
    ``Maya``), ``apply_template(token)`` applies whichever was picked, exactly like Blender's own
    dropdown. Reverting to the user's own look is just picking their built-in/own theme from that
    same list — Blender's native preset system already covers it, so there is no bespoke backup.
    """

    list_styles = staticmethod(list_styles)
    list_templates = staticmethod(list_templates)
    apply_template = staticmethod(apply_template)
    user_preset_dir = staticmethod(user_preset_dir)
    user_preset_path = staticmethod(user_preset_path)
    is_installed = staticmethod(is_installed)
    install = staticmethod(install)
    apply_theme_preset = staticmethod(apply_theme_preset)
    set_style = staticmethod(set_style)
