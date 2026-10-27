"""blendertk workspace-system headless test — the current-workspace resolver, marker-aware
workspace discovery, rule-fed folder accessors, and create/promote (all built on
``pythontk.Workspace`` / the shared ``workspace.mel`` format).

Runs under the Blender harness (bpy present → the save/get_env_info integration checks run)
and equally under the workspace ``.venv`` (disk-level checks only)::

  blender --background --factory-startup --python blendertk/test/test_workspace.py
  python blendertk/test/test_workspace.py
"""

import os
import shutil
import sys
import tempfile
import traceback

# cp1252 consoles can't encode "→" in check names — degrade instead of dying mid-report.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(errors="replace")
        except (ValueError, OSError):
            pass

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for p in (REPO, os.path.join(MONO, "pythontk")):
    if p not in sys.path:
        sys.path.insert(0, p)

lines = []


def check(name, cond, detail=""):
    lines.append(
        f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + str(detail)) if detail else ''}"
    )


def _touch(*parts):
    path = os.path.join(*parts)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("")
    return path


def _nc(p):
    return os.path.normcase(os.path.normpath(p))


tmp = tempfile.mkdtemp(prefix="btk_ws_test_")
# Sandbox the preset store BEFORE any template call — never touch the live
# workspace-template presets (same rule as test_macros.py / PresetStore .active memory).
os.environ["UITK_PRESETS_ROOT"] = os.path.join(tmp, "presets")
try:
    try:
        import bpy  # noqa: F401

        HAS_BPY = True
    except ImportError:
        HAS_BPY = False

    import pythontk as ptk
    import blendertk as btk

    # --- fixture: a marked shared project + a plain Blender folder ---------------------------
    proj = os.path.join(tmp, "proj")
    ws = ptk.Workspace.create(proj)  # workspace.mel + standard subfolders
    shot = _touch(proj, "scenes", "shot.blend")
    loose_dir = os.path.join(tmp, "loose")
    loose = _touch(loose_dir, "thing.blend")

    # 1. find_workspaces — marked projects count even without loose .blend files at their root;
    #    a marked project's scenes/ folder is not its own workspace; plain blend dirs still count.
    found = btk.find_workspaces(tmp, recursive=True)
    check(
        "find_workspaces lists marked + plain, suppresses proj/scenes",
        sorted(os.path.basename(p) for p in found) == ["loose", "proj"],
        f"{found}",
    )
    check(
        "find_workspaces (non-recursive) matches",
        sorted(os.path.basename(p) for p in btk.find_workspaces(tmp))
        == ["loose", "proj"],
    )
    check(
        "find_workspaces root-first when root holds .blend",
        btk.find_workspaces(loose_dir)[0] == os.path.normpath(loose_dir),
    )

    # 2. current_workspace — a file inside the marked project resolves to the PROJECT root...
    cur = btk.current_workspace(shot)
    check(
        "current_workspace(scene in marked proj) → proj root",
        cur is not None and _nc(cur.root) == _nc(proj) and cur.is_marked,
        repr(cur),
    )
    # ...a file in a plain folder resolves to that folder, unmarked.
    cur = btk.current_workspace(loose)
    check(
        "current_workspace(plain folder) → folder, unmarked",
        cur is not None and _nc(cur.root) == _nc(loose_dir) and not cur.is_marked,
    )

    # 3. session pin — governs AMBIENT resolution (Maya's `workspace -o` analogue); an
    #    explicit path always resolves that path, never the unrelated pin.
    btk.set_current_workspace(proj)
    pinned = btk.current_workspace()
    check(
        "pin wins ambient resolution",
        pinned is not None and _nc(pinned.root) == _nc(proj),
    )
    check(
        "explicit path bypasses the pin",
        _nc(btk.current_workspace(loose).root) == _nc(loose_dir),
    )
    btk.set_current_workspace(None)
    ambient = btk.current_workspace()
    check(
        "clearing the pin restores ambient chain",
        ambient is None or _nc(ambient.root) != _nc(proj),
    )

    # 3b. scene_source -- a file opened in place of a source scene (the Reference
    #     Manager's open-as-new scratch) stands in for it; None forgets it.
    stand_in = os.path.join(tmp, "scratch", "shot_ma.blend")
    btk.DataNodes.set_scene_source(stand_in, shot)
    check(
        "scene_source names the scene a file stands in for",
        _nc(btk.DataNodes.scene_source(stand_in) or "") == _nc(shot),
    )
    check(
        "...and nothing for any other file",
        btk.DataNodes.scene_source(loose) is None,
    )
    from blendertk.node_utils.data_nodes import DataNodes as _DN

    check(
        "the mapping lives on DataNodes (the part that owns the scene path)",
        _nc(_DN.scene_source(stand_in) or "") == _nc(shot),
    )
    btk.DataNodes.set_scene_source(stand_in, None)
    check(
        "set_scene_source(None) forgets it", btk.DataNodes.scene_source(stand_in) is None
    )

    # 4. rule-fed accessors — marked project answers from its rules...
    check(
        "workspace_root reports the marked root",
        _nc(btk.workspace_root(shot)) == _nc(proj),
    )
    check(
        "scenes_dir follows the scene rule",
        _nc(btk.scenes_dir(shot)) == _nc(os.path.join(proj, "scenes")),
    )
    check(
        "source_images_dir follows the sourceImages rule",
        _nc(btk.source_images_dir(shot)) == _nc(os.path.join(proj, "sourceimages")),
    )
    # ...a plain folder takes the ONE project tree mayatk names (2026-10-13: the
    # Blender-alone default was textures/, so the two DCCs disagreed about the
    # same project's texture folder); an existing textures/ still matches.
    check(
        "plain folder source_images_dir defaults to sourceimages (mayatk's)",
        _nc(btk.source_images_dir(loose))
        == _nc(os.path.join(loose_dir, "sourceimages")),
    )
    os.makedirs(os.path.join(loose_dir, "textures"))
    check(
        "an existing textures/ folder is preferred once present",
        _nc(btk.source_images_dir(loose)) == _nc(os.path.join(loose_dir, "textures")),
    )
    # Discriminating check: sourceimages/ outranks textures/ in the convention
    # tuple, so its appearance must FLIP the answer away from the default —
    # the textures/ check alone matches the fallback path and cannot fail.
    os.makedirs(os.path.join(loose_dir, "sourceimages"))
    check(
        "sourceimages/ outranks textures/ once present",
        _nc(btk.source_images_dir(loose))
        == _nc(os.path.join(loose_dir, "sourceimages")),
    )
    check("explicit empty path → '' (no workspace)", btk.source_images_dir("") == "")
    check(
        "workspace_scenes_dir on marked proj",
        _nc(btk.workspace_scenes_dir(proj)) == _nc(os.path.join(proj, "scenes")),
    )
    check(
        "workspace_scenes_dir on plain folder → ''",
        btk.workspace_scenes_dir(loose_dir) == "",
    )

    # 5. create_workspace — marker + standard folders, readable back via the shared codec.
    fresh = os.path.join(tmp, "fresh")
    created = btk.create_workspace(fresh)
    check(
        "create_workspace writes the marker", created is not None and created.is_marked
    )
    check(
        "create_workspace makes the standard folders",
        all(os.path.isdir(os.path.join(fresh, d)) for d in ("scenes", "sourceimages")),
    )
    check(
        "created marker parses to the template",
        ptk.Workspace.parse_workspace_mel(created.marker_path)
        == ptk.DEFAULT_FILE_RULES,
    )

    # 5b. workspace templates — the ACTIVE saved template defines how each subsequent new
    #     workspace is built (saved from the Workspace Editor's template combo; sandboxed above).
    check(
        "template rules default to the standard set",
        btk.workspace_template_rules() == ptk.DEFAULT_FILE_RULES,
    )
    btk.save_workspace_template("studio", {"scene": "shots", "sourceImages": "tex"})
    check(
        "saved template becomes the active default",
        btk.workspace_template_rules() == {"scene": "shots", "sourceImages": "tex"},
    )
    check("template listed", "studio" in btk.list_workspace_templates())
    templated = btk.create_workspace(os.path.join(tmp, "templated"))
    check(
        "create_workspace builds from the active template",
        ptk.Workspace.parse_workspace_mel(templated.marker_path)
        == {"scene": "shots", "sourceImages": "tex"},
    )
    check(
        "named template lookup",
        btk.workspace_template_rules("studio")["scene"] == "shots",
    )
    # A template saved from the editor's preset combo (uitk PresetManager) carries a
    # "_meta" version block — the rules reader must strip it, never surface it as a rule.
    btk.save_workspace_template("gui", {"_meta": {"version": 1}, "scene": "shots"})
    check(
        "PresetManager _meta block is stripped from template rules",
        btk.workspace_template_rules("gui") == {"scene": "shots"},
    )
    # The store is ptk.WorkspaceTemplates — UNnamespaced, so mayatk reads the same
    # templates (a workspace.mel is a shared project; so is the recipe for one).
    check(
        "templates live in the shared, unnamespaced store",
        ptk.WorkspaceTemplates.store().package == ""
        and ptk.WorkspaceTemplates.rules("studio")
        == {"scene": "shots", "sourceImages": "tex"},
        str(ptk.WorkspaceTemplates.store().user_dir),
    )
    btk.delete_workspace_template("gui")
    btk.delete_workspace_template("studio")
    check(
        "deleted template falls back to the standard set",
        btk.workspace_template_rules() == ptk.DEFAULT_FILE_RULES,
    )

    # 5c. Workspace Editor rule-key resolution — a row ADDED while the table shows Maya's
    #     nice names has no stored key, so its typed text is the key candidate. Without the
    #     reverse lookup the label went into workspace.mel verbatim (workspace -fr "Render
    #     Data" …), a rule name Maya does not recognize.
    from blendertk.env_utils.workspace_editor import WorkspaceEditorSlots as _WES

    check(
        "nice label resolves to its rule key",
        _WES._key_for_label("Render Data") == "renderData",
    )
    check(
        "nice-label lookup is case-insensitive",
        _WES._key_for_label("  source images ") == "sourceImages",
    )
    check(
        "an unknown label passes through as a custom rule",
        _WES._key_for_label("myCustomRule") == "myCustomRule",
    )
    check("a raw key is returned unchanged", _WES._key_for_label("scene") == "scene")
    check("blank stays blank", _WES._key_for_label("   ") == "")

    # 5d. Signal re-wire across a slots rebuild. The tbl000/txt000 QWidgets outlive the
    #     Slots instance (a UI reload leaves is_initialized stamped on them), so the
    #     handler bound by a DEAD instance must be replaced, not merely joined — a stale
    #     _on_item_changed carries that instance's stale removal-diff baseline and would
    #     delete rules on the next edit. Fake signal: the semantics, not Qt, are the point.
    class _FakeSignal:
        def __init__(self):
            self.handlers = []

        def connect(self, handler):
            self.handlers.append(handler)

        def disconnect(self, handler):
            if handler not in self.handlers:
                raise TypeError("not connected")
            self.handlers.remove(handler)

    class _FakeWidget:
        def __init__(self):
            self.itemChanged = _FakeSignal()

    def _handler_a():
        pass

    def _handler_b():
        pass

    fake = _FakeWidget()
    _WES._rewire(fake, "itemChanged", _handler_a)
    check(
        "first wire connects", fake.itemChanged.handlers == [_handler_a]
    )
    _WES._rewire(fake, "itemChanged", _handler_b)  # the "rebuilt instance" pass
    check(
        "a rebuild replaces the dead instance's handler",
        fake.itemChanged.handlers == [_handler_b],
        str(fake.itemChanged.handlers),
    )
    _WES._rewire(fake, "itemChanged", _handler_b)  # same instance, re-shown
    check(
        "re-running _init never stacks duplicates",
        fake.itemChanged.handlers == [_handler_b],
    )

    # 6. promote_workspace — describes the EXISTING layout instead of imposing the template.
    flat = os.path.join(tmp, "flat")
    _touch(flat, "scene_a.blend")
    os.makedirs(os.path.join(flat, "textures"))
    promoted = btk.promote_workspace(flat)
    check("promote marks the folder", promoted is not None and promoted.is_marked)
    check("promote: flat blends → scene rule '.'", promoted.rules.get("scene") == ".")
    check(
        "promote: existing textures/ → sourceImages rule",
        promoted.rules.get("sourceImages") == "textures",
    )
    check(
        "promoted scenes_dir is the root itself", _nc(promoted.scene_dir) == _nc(flat)
    )
    check(
        "promoted workspace_scenes_dir → '' (no separate scene folder)",
        btk.workspace_scenes_dir(flat) == "",
    )

    # 7. promote is non-destructive — foreign rules and hand-written lines survive re-promotion.
    with open(promoted.marker_path, "a", encoding="utf-8") as f:
        f.write('//hand-written note\nworkspace -fr "customRule" "custom";\n')
    re_promoted = btk.promote_workspace(flat)
    with open(re_promoted.marker_path, encoding="utf-8") as f:
        marker_text = f.read()
    check(
        "re-promotion keeps the hand-written comment",
        "//hand-written note" in marker_text,
    )
    check(
        "re-promotion keeps the foreign rule",
        re_promoted.rules.get("customRule") == "custom",
    )
    check("re-promotion keeps the layout rules", re_promoted.rules.get("scene") == ".")

    # --- the scene-source family has one owner -----------------------------------------------
    # EnvUtils' copies forwarded to DataNodes, except set_scene_source, which also armed the
    # twin's save -- and the flat btk.set_scene_source reached the record-only DataNodes one
    # by module order. DataNodes owns all six; EnvUtils' names forward, with a warning, until
    # 0.23.0. Consolidated: 2026-10-25.
    import warnings

    for name in (
        "set_scene_source",
        "scene_source",
        "scene_workspace",
        "scene_project_root",
        "scene_source_images_dir",
        "twin_save_target",
    ):
        check(f"btk.{name} is DataNodes'", getattr(btk, name) == getattr(btk.DataNodes, name))
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        forwarded = btk.EnvUtils.scene_source(os.path.join(os.sep, "nowhere", "twin.blend"))
    check(
        "the retired EnvUtils.scene_source warns and forwards",
        forwarded is None and any(issubclass(w.category, DeprecationWarning) for w in caught),
        [str(w.message) for w in caught],
    )

    # --- bpy-dependent integration (skipped under the .venv) ---------------------------------
    if HAS_BPY:
        # 8. get_env_info routes through the resolver: a .blend saved inside the marked
        #    project reports the PROJECT root as its workspace.
        bpy.ops.wm.read_factory_settings(use_empty=True)
        saved = btk.save_scene_as(os.path.join(proj, "scenes"), "env_probe")
        info_ws = btk.get_env_info("workspace")
        check(
            "get_env_info('workspace') → marked project root",
            saved is not None and _nc(info_ws) == _nc(proj),
            f"{info_ws}",
        )

        # 9. save_scene_as resolves {scenes} through the workspace's scene rule.
        shots_proj = os.path.join(tmp, "shots_proj")
        btk.create_workspace(shots_proj, rules={"scene": "shots"})
        bpy.ops.wm.read_factory_settings(use_empty=True)
        sub = btk.save_scene_as(shots_proj, "hero", subfolder="{scenes}/{name}")
        check(
            "save_scene_as {scenes} follows the scene rule",
            sub is not None
            and _nc(os.path.dirname(sub)).endswith(_nc(os.path.join("shots", "hero"))),
            str(sub),
        )

        # 10. scene_project_root -- the boundary a lightmap bake writes and retires
        #     files in. A pin on a folder of several marked projects yields to the
        #     file's own (BACKLOG 2026-10-04, decided 2026-10-05): the broad pin opened
        #     the other projects' folders to this file's bake. A pin inside the file's
        #     project is narrower, and still names the boundary.
        broad = os.path.join(tmp, "broad")
        mine = os.path.join(broad, "mine")
        ptk.Workspace.create(mine)
        ptk.Workspace.create(os.path.join(broad, "theirs"))
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.ops.wm.save_as_mainfile(filepath=os.path.join(mine, "scenes", "room.blend"))
        btk.set_current_workspace(broad)
        check(
            "scene_project_root: a broad pin yields to the file's own marked project",
            _nc(btk.DataNodes.scene_project_root() or "") == _nc(mine),
            str(btk.DataNodes.scene_project_root()),
        )
        # ptk.Workspace.for_scene, the one rule both DCCs share (2026-10-13): a
        # project's own marker names it more exactly than a pin below it.
        btk.set_current_workspace(os.path.join(mine, "scenes"))
        check(
            "scene_project_root: a pin inside the file's project yields to its marker",
            _nc(btk.DataNodes.scene_project_root() or "") == _nc(mine),
            str(btk.DataNodes.scene_project_root()),
        )
        # An unmarked file inside the pin belongs to the pinned project, not to
        # its own folder (mayatk's rule for the active project).
        loose_proj = os.path.join(tmp, "loose_proj")
        os.makedirs(os.path.join(loose_proj, "scenes", "shots"))
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.ops.wm.save_as_mainfile(
            filepath=os.path.join(loose_proj, "scenes", "shots", "room.blend")
        )
        btk.set_current_workspace(loose_proj)
        check(
            "scene_project_root: an unmarked file inside the pin is the pin's",
            _nc(btk.DataNodes.scene_project_root() or "") == _nc(loose_proj),
            str(btk.DataNodes.scene_project_root()),
        )
        bpy.ops.wm.read_factory_settings(use_empty=True)
        check(
            "scene_project_root: an unsaved file is the pin's",
            _nc(btk.DataNodes.scene_project_root() or "") == _nc(loose_proj),
            str(btk.DataNodes.scene_project_root()),
        )
        btk.set_current_workspace(None)
        check(
            "scene_project_root: unsaved with no pin is None (never temp)",
            btk.DataNodes.scene_project_root() is None,
        )

        # 11. The FIRST save of an opened-as-new scratch twin (temp) lands in its
        #     source's project scenes folder, named <stem>_<ext>.blend (maintainer
        #     default, 2026-10-13): Ctrl+S saved real work into the 30-day temp sweep.
        twin_proj = os.path.join(tmp, "twin_proj")
        ptk.Workspace.create(twin_proj)
        twin_src = os.path.join(twin_proj, "scenes", "room.ma")
        open(twin_src, "w").close()
        twin_store = ptk.TempArtifacts("btk_twin_save_test", policy="scoped")
        try:
            twin = os.path.join(twin_store.dir_path(), "room_ma.blend")
            bpy.ops.wm.read_factory_settings(use_empty=True)
            bpy.ops.wm.save_as_mainfile(filepath=twin)
            btk.DataNodes.set_scene_source(twin, twin_src)
            bpy.data.objects.new("work", None)
            bpy.ops.wm.save_mainfile()
            target = os.path.join(twin_proj, "scenes", "room_ma.blend")
            check(
                "a twin's first save lands in its source's project scenes folder",
                _nc(bpy.data.filepath) == _nc(target) and os.path.isfile(target),
                bpy.data.filepath,
            )
            check(
                "...the temp copy is gone, and the saved file is no stand-in",
                not os.path.exists(twin) and btk.DataNodes.scene_source(target) is None,
            )
            # A second twin of the same source never overwrites the first save.
            bpy.ops.wm.read_factory_settings(use_empty=True)
            bpy.ops.wm.save_as_mainfile(filepath=twin)
            btk.DataNodes.set_scene_source(twin, twin_src)
            bpy.ops.wm.save_mainfile()
            check(
                "an existing scene of that name is never overwritten",
                _nc(os.path.dirname(bpy.data.filepath))
                == _nc(os.path.join(twin_proj, "scenes"))
                and _nc(bpy.data.filepath) != _nc(target),
                bpy.data.filepath,
            )
            # A Save COPY of a twin (SmartBake's backup) writes elsewhere and
            # leaves the open file the twin: it is never redirected.
            bpy.ops.wm.read_factory_settings(use_empty=True)
            bpy.ops.wm.save_as_mainfile(filepath=twin)
            btk.DataNodes.set_scene_source(twin, twin_src)
            _before = sorted(os.listdir(os.path.join(twin_proj, "scenes")))
            _copy = os.path.join(twin_store.dir_path(), "room_backup.blend")
            bpy.ops.wm.save_as_mainfile(filepath=_copy, copy=True)
            check(
                "a Save Copy of a twin is not redirected (the twin stays open)",
                _nc(bpy.data.filepath) == _nc(twin)
                and os.path.isfile(twin)
                and btk.DataNodes.scene_source(twin) is not None
                and sorted(os.listdir(os.path.join(twin_proj, "scenes"))) == _before,
                f"open={bpy.data.filepath} scenes={os.listdir(os.path.join(twin_proj, 'scenes'))}",
            )
            # A redirect that cannot save keeps the twin a stand-in (the next
            # save tries again) and leaves ONE hook armed.
            _stock_target = btk.DataNodes.twin_save_target
            btk.DataNodes.twin_save_target = classmethod(
                lambda cls, scene=None: (
                    os.path.join(twin, "not_a_dir", "x.blend")
                    if _nc(scene or "") == _nc(twin)
                    else None
                )
            )
            try:
                bpy.ops.wm.save_mainfile()
            finally:
                btk.DataNodes.twin_save_target = _stock_target
            check(
                "a failed redirect keeps the twin a stand-in, hook armed once",
                _nc(bpy.data.filepath) == _nc(twin)
                and btk.DataNodes.scene_source(twin) is not None
                and [
                    h.__name__ for h in bpy.app.handlers.save_post
                ].count("_btk_twin_save_post")
                == 1,
                str([h.__name__ for h in bpy.app.handlers.save_post]),
            )
            # Linked while the twin sat in temp, a project file on ANOTHER drive
            # stays absolute (no relative path crosses drives); saved into the
            # project, it goes relative.
            _xd_proj = os.path.join(HERE, "temp_tests", "btk_twin_xdrive")
            shutil.rmtree(_xd_proj, ignore_errors=True)
            ptk.Workspace.create(_xd_proj)
            try:
                _xd_src = os.path.join(_xd_proj, "scenes", "hall.ma")
                open(_xd_src, "w").close()
                _xd_lib = os.path.join(_xd_proj, "cache", "bridge", "lib.blend")
                os.makedirs(os.path.dirname(_xd_lib), exist_ok=True)
                bpy.ops.wm.read_factory_settings(use_empty=True)
                _coll = bpy.data.collections.new("LIBC")
                bpy.data.libraries.write(_xd_lib, {_coll}, fake_user=True)
                bpy.ops.wm.read_factory_settings(use_empty=True)
                _xd_twin = os.path.join(twin_store.dir_path(), "hall_ma.blend")
                bpy.ops.wm.save_as_mainfile(filepath=_xd_twin)
                btk.DataNodes.set_scene_source(_xd_twin, _xd_src)
                btk.link_blend_file(_xd_lib, link=True, relative=True)
                _cross = (
                    os.path.splitdrive(_xd_twin)[0].lower()
                    != os.path.splitdrive(_xd_lib)[0].lower()
                )
                bpy.ops.wm.save_mainfile()
                _paths = [lib.filepath for lib in bpy.data.libraries]
                check(
                    "a twin saved into the project links its project files "
                    "relative (also across drives)"
                    + ("" if _cross else " [temp and project share a drive here]"),
                    _paths
                    and all(p.startswith("//") for p in _paths)
                    and _nc(os.path.dirname(bpy.data.filepath))
                    == _nc(os.path.join(_xd_proj, "scenes")),
                    f"{_paths} open={bpy.data.filepath}",
                )
            finally:
                bpy.ops.wm.read_factory_settings(use_empty=True)
                shutil.rmtree(_xd_proj, ignore_errors=True)
        finally:
            bpy.ops.wm.read_factory_settings(use_empty=True)
            twin_store.cleanup()
    else:
        print(
            "SKIP bpy integration checks (no bpy — run under the Blender harness for those)"
        )

except Exception as e:
    traceback.print_exc()
    check("test raised", False, repr(e))
finally:
    try:
        import blendertk as btk

        btk.set_current_workspace(None)
    except Exception:
        pass
    shutil.rmtree(tmp, ignore_errors=True)

passed = sum(1 for line in lines if line.startswith("OK"))
for line in lines:
    print(line)
result = "PASS" if all(line.startswith("OK") for line in lines) else "FAIL"
print(f"===RESULT: {result}=== ({passed}/{len(lines)})")
