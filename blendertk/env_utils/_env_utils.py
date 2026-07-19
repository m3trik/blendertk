# !/usr/bin/python
# coding=utf-8
"""blendertk environment / scene-library utilities — the engine behind the Reference Manager panel.

Maya *file references* map onto Blender **linked libraries** (``bpy.data.libraries`` — File ▸ Link).
This module is the Qt-free, bpy-only engine (unit-testable headless): discover ``.blend`` files under
a folder, list the libraries already linked, link a ``.blend``'s collections (or append a copy), and
reload / remove a library. The Switchboard slot (``reference_manager.py``) is the thin Qt driver.

``import bpy`` is deferred into the call bodies (importing the package surface must not need a
running Blender — the no-import-side-effects rule).
"""
import os

import pythontk as ptk


def _abspath(filepath):
    """Absolute, normalized path of a (possibly ``//`` project-relative) library path, or ''."""
    import bpy

    if not filepath:
        return ""
    try:
        return os.path.normpath(bpy.path.abspath(filepath))
    except Exception:
        return os.path.normpath(filepath)


def find_blend_files(root_dir, recursive=True, filter_text=""):
    """Every ``.blend`` file under ``root_dir`` (recursively by default), optionally name-filtered.

    ``filter_text`` uses ``pythontk`` wildcard semantics (``*foo``, ``foo*``, ``*foo*``; comma /
    semicolon separated). Returns a sorted list of absolute paths.
    """
    if not (root_dir and os.path.isdir(root_dir)):
        return []
    found = []
    walker = os.walk(root_dir) if recursive else [
        (root_dir, [], [f for f in os.listdir(root_dir)
                        if os.path.isfile(os.path.join(root_dir, f))])
    ]
    for dirpath, _dirs, files in walker:
        for f in files:
            if f.lower().endswith(".blend"):
                found.append(os.path.normpath(os.path.join(dirpath, f)))
    if filter_text:
        names = [os.path.basename(p) for p in found]
        kept = set(ptk.filter_list(names, inc=_split_filter(filter_text)))
        found = [p for p in found if os.path.basename(p) in kept]
    return sorted(found)


def _split_filter(filter_text):
    """Split a comma/semicolon filter string into a list of wildcard patterns."""
    patterns = [filter_text]
    for delim in (",", ";"):
        expanded = []
        for p in patterns:
            expanded.extend(s.strip() for s in p.split(delim) if s.strip())
        patterns = expanded
    return patterns or [filter_text]


def list_libraries():
    """Every linked library as a record: ``{name, library, filepath, abspath, exists}``.
    ``library`` is the live ``bpy.types.Library`` datablock (reload / remove use it)."""
    import bpy

    records = []
    for lib in bpy.data.libraries:
        ap = _abspath(lib.filepath)
        records.append({
            "name": lib.name,
            "library": lib,
            "filepath": lib.filepath,
            "abspath": ap,
            "exists": bool(ap and os.path.exists(ap)),
        })
    return records


def linked_blend_paths():
    """Set of normalized absolute paths of the ``.blend`` files currently linked as libraries."""
    return {r["abspath"].lower() for r in list_libraries() if r["abspath"]}


def is_blend_linked(path):
    """True iff ``path`` is already linked as a library."""
    return _abspath(path).lower() in linked_blend_paths()


def link_blend_file(path, link=True, instance=True, target_collection=None):
    """Link (or append, ``link=False``) every collection from ``path`` and instance them into the
    active scene — the closest Blender analogue of adding a Maya file reference. Falls back to
    objects when the file has no collections. Returns the number of datablocks brought in.

    ``target_collection`` links into that collection instead of the active scene's master
    collection — used by Hierarchy Sync to sandbox the reference in a hidden, view-layer-excluded
    collection so it never clutters the outliner/viewport.
    """
    import bpy

    if not (path and os.path.isfile(path)):
        return 0
    with bpy.data.libraries.load(path, link=link) as (data_from, data_to):
        data_to.collections = list(data_from.collections)
        if not data_from.collections:
            data_to.objects = list(data_from.objects)

    dest_coll = target_collection or bpy.context.scene.collection
    count = 0
    for coll in getattr(data_to, "collections", []) or []:
        if coll is None:
            continue
        if instance:
            inst = bpy.data.objects.new(coll.name, None)
            inst.instance_type = "COLLECTION"
            inst.instance_collection = coll
            dest_coll.objects.link(inst)
        else:
            dest_coll.children.link(coll)
        count += 1
    if not count:  # object fallback
        for obj in getattr(data_to, "objects", []) or []:
            if obj is not None:
                dest_coll.objects.link(obj)
                count += 1
    return count


def reload_library(library):
    """Reload a library from disk (``library`` is a datablock or its name). Returns True on success."""
    import bpy

    lib = bpy.data.libraries.get(library) if isinstance(library, str) else library
    if lib is None:
        return False
    try:
        lib.reload()
        return True
    except (RuntimeError, AttributeError):
        return False


def remove_library(library):
    """Remove a library and everything linked from it (datablock or name). Returns True on success."""
    import bpy

    lib = bpy.data.libraries.get(library) if isinstance(library, str) else library
    if lib is None:
        return False
    try:
        bpy.data.libraries.remove(lib, do_unlink=True)
        return True
    except (RuntimeError, ReferenceError):
        return False


def make_library_local(library):
    """Make every datablock linked from ``library`` **local** (a native, editable copy) and drop the
    now-unused library — the Blender analogue of Maya's *import references* (``importContents``).

    ``library`` is a ``bpy.types.Library`` datablock or its name. Returns the number of datablocks
    made local.
    """
    import bpy

    lib = bpy.data.libraries.get(library) if isinstance(library, str) else library
    if lib is None:
        return 0
    count = 0
    # `id.make_local()` clears each datablock's `.library` pointer in place; iterate every ID
    # collection so linked meshes/materials/etc. come local too, not just the objects.
    for attr in dir(bpy.data):
        coll = getattr(bpy.data, attr, None)
        if getattr(coll, "rna_type", None) is None or not hasattr(coll, "__iter__"):
            continue
        for db in list(coll):
            # Compare by `==` not `is`: bpy hands back fresh datablock wrappers, so identity is
            # unreliable; `==` compares the underlying ID (the documented bpy-wrapper gotcha).
            if getattr(db, "library", None) == lib:
                try:
                    db.make_local()
                    count += 1
                except (RuntimeError, ReferenceError, AttributeError):
                    pass
    if count:  # the library has no linked users left → drop it
        try:
            bpy.data.libraries.remove(lib, do_unlink=True)
        except (RuntimeError, ReferenceError):
            pass
    return count


# ----------------------------------------------------------------- workspace / scene files
# The Blender analogue of Maya's project workspace, built on ``pythontk.Workspace``. A workspace
# is a project folder, either *marked* (a ``workspace.mel`` at its root — a shared Maya/Blender
# project whose file rules say where scenes/textures live) or *unmarked* (a directory directly
# holding .blend files — zero-ceremony Blender-alone projects, promotable via
# ``promote_workspace``). These back the Reference Manager's workspace combo + scene table +
# open/save/rename/delete operations and the package-wide current-workspace resolver (no bpy
# except open/save and the current-file lookup → testable on disk).

_current_workspace_root = None  # session pin (Blender has no native `workspace -o`)


def set_current_workspace(root=None):
    """Pin (or clear, with None) the session's current workspace — the Blender analogue of
    Maya's ``workspace -o``. Returns the pinned root (or None when cleared)."""
    global _current_workspace_root
    _current_workspace_root = os.path.normpath(root) if root else None
    return _current_workspace_root


def current_workspace(path=None):
    """The active ``pythontk.Workspace``, or None.

    Ambient resolution (``path=None``): the session pin (:func:`set_current_workspace`)
    → the nearest marked (``workspace.mel``) root containing the saved .blend → the
    .blend's own folder as an unmarked workspace → None (nothing saved, nothing pinned).

    An explicit *path* resolves THAT path (marked ancestor → its own folder) and never
    answers with the unrelated session pin — the pin is global state, like Maya's
    ``workspace -o``, and only governs the ambient chain."""
    if path is None:
        if _current_workspace_root and os.path.isdir(_current_workspace_root):
            return ptk.Workspace.load(_current_workspace_root)
        try:
            import bpy

            path = bpy.data.filepath
        except ImportError:  # headless .venv — no bpy, no open file
            path = ""
    if not path:
        return None
    ws = ptk.Workspace.find_containing(path)
    if ws is not None:
        return ws
    d = path if os.path.isdir(path) else os.path.dirname(os.path.abspath(path))
    return ptk.Workspace.load(d) if os.path.isdir(d) else None


def workspace_root(path=None):
    """Absolute root of the current workspace, or '' — what ``get_env_info("workspace")``
    reports."""
    ws = current_workspace(path)
    return ws.root if ws else ""


def source_images_dir(path=None):
    """The current workspace's texture folder — its ``sourceImages`` rule → an existing
    ``sourceimages``/``textures`` folder → ``textures`` (the legacy Blender-alone default).
    '' when there is no current workspace."""
    ws = current_workspace(path)
    if ws is None:
        return ""
    return ws.resolve_dir(("sourceImages",), ("sourceimages", "textures"), default="textures")


def scenes_dir(path=None):
    """The current workspace's scene folder (``scene`` rule → existing ``scenes`` → the root),
    or ''."""
    ws = current_workspace(path)
    return ws.scene_dir if ws else ""


def workspace_scenes_dir(root):
    """The scene-rule folder of a *marked* workspace at ``root`` (absolute), or '' when
    ``root`` is unmarked / the rule resolves to the root itself — lets callers extend a flat
    folder scan into a shared project's ``scenes/`` without double-listing anything."""
    if not (root and os.path.isdir(root)):
        return ""
    ws = ptk.Workspace.load(root)
    if not ws.is_marked:
        return ""
    sd = ws.scene_dir
    return "" if os.path.normcase(sd) == os.path.normcase(ws.root) else sd


# --- workspace templates (named file-rule sets for building NEW workspaces) -----------------
# ptk.PresetStore-backed, saved from the Workspace Editor. The ACTIVE template is what
# `create_workspace` seeds from when no rules are given — saved templates literally define how
# each subsequent new workspace is built (Maya's Project Window analogue, made persistent).
_TEMPLATE_STORE = None


def _workspace_template_store():
    global _TEMPLATE_STORE
    if _TEMPLATE_STORE is None:
        _TEMPLATE_STORE = ptk.PresetStore("workspace_templates", package="blendertk")
    return _TEMPLATE_STORE


def list_workspace_templates():
    """Saved workspace-template names (the Workspace Editor's Save Template entries)."""
    return _workspace_template_store().list()


def workspace_template_rules(name=None):
    """File rules for building a NEW workspace: the *name*d (default: active / last-saved)
    template, falling back to the standard ``ptk.DEFAULT_FILE_RULES``. Seeds
    :func:`create_workspace` and the Workspace Editor's fresh-path definition."""
    store = _workspace_template_store()
    name = name or store.active
    if name:
        try:
            rules = store.load(name)
        except (KeyError, ValueError):
            rules = None
        if isinstance(rules, dict):
            # Templates saved from the Workspace Editor's preset combo (uitk
            # PresetManager) carry a "_meta" version block — not a rule.
            rules = {str(k): str(v) for k, v in rules.items() if k != "_meta"}
            if rules:
                return rules
    return dict(ptk.DEFAULT_FILE_RULES)


def save_workspace_template(name, rules):
    """Save *rules* as workspace template *name* and make it the active default for new
    workspaces. Returns the saved name."""
    store = _workspace_template_store()
    store.save(name, dict(rules))
    store.active = name
    return name


def delete_workspace_template(name):
    """Delete the user template *name* (the store keeps the active pointer consistent).
    True when a file was removed."""
    return _workspace_template_store().delete(name)


def create_workspace(root, rules=None, create_dirs=True):
    """Create a marked workspace at ``root`` — the Blender counterpart of Maya's File ▸
    Project Window ▸ New. ``rules=None`` seeds from :func:`workspace_template_rules` (the
    active saved template, else the Maya-standard defaults) and creates the rule subfolders.
    Idempotent on an existing project (its rules win). Returns the ``pythontk.Workspace``."""
    if not root:
        return None
    if rules is None:
        rules = workspace_template_rules()
    return ptk.Workspace.create(root, rules=rules, create_dirs=create_dirs)


def promote_workspace(root=None):
    """Mark ``root`` (default: the current workspace folder) as a shared Maya/Blender project
    by writing a ``workspace.mel`` that describes the layout it ALREADY has — scene rule ``.``
    when .blend files sit at the root, ``sourceImages`` → ``textures`` when that's the existing
    texture folder. Creates no subfolders and never clobbers an existing marker's rules."""
    if root is None:
        ws = current_workspace()
        root = ws.root if ws else ""
    if not (root and os.path.isdir(root)):
        return None
    rules = dict(ptk.DEFAULT_FILE_RULES)

    def _has(sub):
        return os.path.isdir(os.path.join(root, sub))

    try:
        flat = any(f.lower().endswith(".blend") for f in os.listdir(root))
    except OSError:
        flat = False
    if flat and not _has("scenes"):
        rules["scene"] = rules["mayaAscii"] = rules["mayaBinary"] = "."
    if _has("textures") and not _has("sourceimages"):
        rules["sourceImages"] = "textures"
    return ptk.Workspace.create(root, rules=rules, create_dirs=False)


def find_workspaces(root_dir, recursive=False):
    """Project folders under ``root_dir`` — marked workspaces (a ``workspace.mel`` at their
    root: shared Maya/Blender projects) plus unmarked ones (a directory directly holding .blend
    files). An unmarked candidate nested inside a marked project (e.g. its ``scenes/`` folder)
    belongs to that project and is not listed. Mirror of mayatk's
    ``find_available_workspaces`` / ``EnvUtils.find_workspaces``.

    ``recursive=False`` (default) only looks at ``root_dir`` and its immediate children —
    mirrors mayatk's workspace-*discovery* toggle (a workspace never nests another workspace's
    scan, only the search for *more* workspace folders goes deeper). Returns absolute dir
    paths, root first, then the rest alphabetically.
    """
    return [
        w.root
        for w in ptk.Workspace.find(root_dir, recursive=recursive, scene_exts=(".blend",))
    ]


def open_scene(path):
    """Open a .blend file (replaces the current file — Maya's ``file -open``). True on success."""
    import bpy

    if not (path and os.path.isfile(path)):
        return False
    try:
        bpy.ops.wm.open_mainfile(filepath=path)
        return True
    except RuntimeError:
        return False


def format_scene_name(name, case=None, suffix=""):
    """Apply a naming convention to a base scene name — ``case`` via :meth:`pythontk.StrUtils.set_case`
    (``None``/"None" leaves it), then append ``suffix`` (not duplicated). Mirror of mayatk's
    ``_format_name``."""
    import pythontk as ptk

    base = name
    if case and case != "None":
        try:
            base = ptk.StrUtils.set_case(base, case)
        except Exception:
            pass
    suffix = (suffix or "").strip()
    if suffix and not base.endswith(suffix):
        base += suffix
    return base


def save_scene_as(directory, name, case=None, suffix="", subfolder="", overwrite=True):
    """Save the current scene as a .blend under ``directory`` with naming conventions applied —
    mirror of mayatk's ``save_scene``. ``case``/``suffix`` format the name; ``subfolder`` is an
    optional path pattern with ``{name}`` / ``{workspace}`` / ``{suffix}`` / ``{scenes}``
    placeholders (``{scenes}`` resolves through the workspace's ``scene`` file rule when
    ``directory`` is a marked workspace — the same ``workspace -q -fre "scene"`` lookup mayatk
    does — falling back to the literal ``"scenes"``). Returns the saved path (or ``None`` if it
    exists and ``overwrite`` is False, or on failure).
    """
    import bpy
    import pythontk as ptk

    if not (directory and name):
        return None
    base = format_scene_name(name, case, suffix)
    target_dir = directory
    if subfolder:
        scene_rule = ptk.Workspace.load(directory).rules.get("scene")
        resolved = ptk.StrUtils.replace_placeholders(
            subfolder,
            name=format_scene_name(name, case, ""),
            workspace=os.path.basename(os.path.normpath(directory)),
            suffix=suffix,
            scenes=scene_rule if scene_rule and not os.path.isabs(scene_rule) else "scenes",
        )
        target_dir = os.path.join(directory, resolved)
    try:
        os.makedirs(target_dir, exist_ok=True)
    except OSError:
        return None
    path = os.path.join(target_dir, base + ".blend")
    if os.path.exists(path) and not overwrite:
        return None
    try:
        bpy.ops.wm.save_as_mainfile(filepath=path)
        return os.path.normpath(path)
    except RuntimeError:
        return None


def rename_scene_file(path, new_base):
    """Rename a .blend on disk (and its ``.blend1`` backup) — mirror of mayatk's ``rename_scene``.
    Returns the new path, or ``None`` (missing source, name clash, or no-op rename)."""
    if not (path and os.path.isfile(path) and new_base):
        return None
    directory = os.path.dirname(path)
    ext = os.path.splitext(path)[1] or ".blend"
    new_path = os.path.join(directory, new_base + ext)
    if os.path.normcase(os.path.normpath(new_path)) == os.path.normcase(os.path.normpath(path)):
        return None
    if os.path.exists(new_path):
        return None
    try:
        os.rename(path, new_path)
    except OSError:
        return None
    backup = path + "1"  # Blender's .blend1 backup
    if os.path.isfile(backup):
        try:
            os.rename(backup, new_path + "1")
        except OSError:
            pass
    return os.path.normpath(new_path)


def delete_scene_file(path):
    """Delete a .blend (and its ``.blend1`` backup) — mirror of mayatk's ``delete_scene``. True on
    success."""
    if not (path and os.path.isfile(path)):
        return False
    try:
        os.remove(path)
    except OSError:
        return False
    backup = path + "1"
    if os.path.isfile(backup):
        try:
            os.remove(backup)
        except OSError:
            pass
    return True


# ----------------------------------------------------------------- reference display modes
# Per-reference display override (Maya's overrideEnabled/overrideDisplayType tri-state) → Blender's
# per-object display_type + hide_select on the objects/instances belonging to a linked library.
_DISPLAY_MODES = ("off", "reference", "template")


def _library_objects(lib):
    """Scene objects belonging to ``lib`` — directly linked objects + the local collection-instance
    empties that instance one of the library's linked collections."""
    import bpy

    objs = []
    for o in bpy.data.objects:
        if o.library == lib:
            objs.append(o)
        elif (
            o.instance_type == "COLLECTION"
            and o.instance_collection is not None
            and o.instance_collection.library == lib
        ):
            objs.append(o)
    return objs


def set_reference_display_mode(library, mode):
    """Set the display override for a linked library's objects — mirror of mayatk's
    ``set_reference_display_mode``. ``mode``: ``"off"`` (normal), ``"reference"`` (locked from
    selection, normal shading), ``"template"`` (wireframe + locked). Returns True if any object
    was updated.
    """
    import bpy

    if mode not in _DISPLAY_MODES:
        raise ValueError(f"Invalid display mode {mode!r}; expected one of {_DISPLAY_MODES}")
    lib = bpy.data.libraries.get(library) if isinstance(library, str) else library
    if lib is None:
        return False
    display_type = "WIRE" if mode == "template" else "TEXTURED"
    hide_select = mode != "off"
    count = 0
    for o in _library_objects(lib):
        o.display_type = display_type
        o.hide_select = hide_select
        count += 1
    return count > 0


def get_reference_display_mode(library):
    """Return the active display mode (``"off"`` / ``"reference"`` / ``"template"``) for a linked
    library — ``"off"`` when its objects disagree (mirror of mayatk's all-must-agree rule)."""
    import bpy

    lib = bpy.data.libraries.get(library) if isinstance(library, str) else library
    if lib is None:
        return "off"
    modes = set()
    for o in _library_objects(lib):
        if not o.hide_select:
            modes.add("off")
        elif o.display_type == "WIRE":
            modes.add("template")
        else:
            modes.add("reference")
    return modes.pop() if len(modes) == 1 else "off"


class EnvUtils:
    """Namespace mirror of mayatk's ``EnvUtils`` (helpers also exposed module-level)."""

    find_blend_files = staticmethod(find_blend_files)
    list_libraries = staticmethod(list_libraries)
    linked_blend_paths = staticmethod(linked_blend_paths)
    is_blend_linked = staticmethod(is_blend_linked)
    link_blend_file = staticmethod(link_blend_file)
    reload_library = staticmethod(reload_library)
    remove_library = staticmethod(remove_library)
    make_library_local = staticmethod(make_library_local)
    find_workspaces = staticmethod(find_workspaces)
    open_scene = staticmethod(open_scene)
    format_scene_name = staticmethod(format_scene_name)
    save_scene_as = staticmethod(save_scene_as)
    rename_scene_file = staticmethod(rename_scene_file)
    delete_scene_file = staticmethod(delete_scene_file)
    set_reference_display_mode = staticmethod(set_reference_display_mode)
    get_reference_display_mode = staticmethod(get_reference_display_mode)
