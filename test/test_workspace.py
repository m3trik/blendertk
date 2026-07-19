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
    lines.append(f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}")


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
    check("find_workspaces lists marked + plain, suppresses proj/scenes",
          sorted(os.path.basename(p) for p in found) == ["loose", "proj"], f"{found}")
    check("find_workspaces (non-recursive) matches", sorted(
        os.path.basename(p) for p in btk.find_workspaces(tmp)) == ["loose", "proj"])
    check("find_workspaces root-first when root holds .blend",
          btk.find_workspaces(loose_dir)[0] == os.path.normpath(loose_dir))

    # 2. current_workspace — a file inside the marked project resolves to the PROJECT root...
    cur = btk.current_workspace(shot)
    check("current_workspace(scene in marked proj) → proj root",
          cur is not None and _nc(cur.root) == _nc(proj) and cur.is_marked, repr(cur))
    # ...a file in a plain folder resolves to that folder, unmarked.
    cur = btk.current_workspace(loose)
    check("current_workspace(plain folder) → folder, unmarked",
          cur is not None and _nc(cur.root) == _nc(loose_dir) and not cur.is_marked)

    # 3. session pin — governs AMBIENT resolution (Maya's `workspace -o` analogue); an
    #    explicit path always resolves that path, never the unrelated pin.
    btk.set_current_workspace(proj)
    pinned = btk.current_workspace()
    check("pin wins ambient resolution", pinned is not None and _nc(pinned.root) == _nc(proj))
    check("explicit path bypasses the pin",
          _nc(btk.current_workspace(loose).root) == _nc(loose_dir))
    btk.set_current_workspace(None)
    ambient = btk.current_workspace()
    check("clearing the pin restores ambient chain",
          ambient is None or _nc(ambient.root) != _nc(proj))

    # 4. rule-fed accessors — marked project answers from its rules...
    check("workspace_root reports the marked root", _nc(btk.workspace_root(shot)) == _nc(proj))
    check("scenes_dir follows the scene rule",
          _nc(btk.scenes_dir(shot)) == _nc(os.path.join(proj, "scenes")))
    check("source_images_dir follows the sourceImages rule",
          _nc(btk.source_images_dir(shot)) == _nc(os.path.join(proj, "sourceimages")))
    # ...a plain folder keeps the legacy Blender-alone conventions.
    check("plain folder source_images_dir defaults to textures",
          _nc(btk.source_images_dir(loose)) == _nc(os.path.join(loose_dir, "textures")))
    os.makedirs(os.path.join(loose_dir, "textures"))
    check("an existing textures/ folder is preferred once present",
          _nc(btk.source_images_dir(loose)) == _nc(os.path.join(loose_dir, "textures")))
    # Discriminating check: sourceimages/ outranks textures/ in the convention
    # tuple, so its appearance must FLIP the answer away from the default —
    # the textures/ check alone matches the fallback path and cannot fail.
    os.makedirs(os.path.join(loose_dir, "sourceimages"))
    check("sourceimages/ outranks textures/ once present",
          _nc(btk.source_images_dir(loose)) == _nc(os.path.join(loose_dir, "sourceimages")))
    check("explicit empty path → '' (no workspace)", btk.source_images_dir("") == "")
    check("workspace_scenes_dir on marked proj",
          _nc(btk.workspace_scenes_dir(proj)) == _nc(os.path.join(proj, "scenes")))
    check("workspace_scenes_dir on plain folder → ''", btk.workspace_scenes_dir(loose_dir) == "")

    # 5. create_workspace — marker + standard folders, readable back via the shared codec.
    fresh = os.path.join(tmp, "fresh")
    created = btk.create_workspace(fresh)
    check("create_workspace writes the marker", created is not None and created.is_marked)
    check("create_workspace makes the standard folders",
          all(os.path.isdir(os.path.join(fresh, d)) for d in ("scenes", "sourceimages")))
    check("created marker parses to the template",
          ptk.parse_workspace_mel(created.marker_path) == ptk.DEFAULT_FILE_RULES)

    # 5b. workspace templates — the ACTIVE saved template defines how each subsequent new
    #     workspace is built (saved from the Workspace Editor's template combo; sandboxed above).
    check("template rules default to the standard set",
          btk.workspace_template_rules() == ptk.DEFAULT_FILE_RULES)
    btk.save_workspace_template("studio", {"scene": "shots", "sourceImages": "tex"})
    check("saved template becomes the active default",
          btk.workspace_template_rules() == {"scene": "shots", "sourceImages": "tex"})
    check("template listed", "studio" in btk.list_workspace_templates())
    templated = btk.create_workspace(os.path.join(tmp, "templated"))
    check("create_workspace builds from the active template",
          ptk.parse_workspace_mel(templated.marker_path)
          == {"scene": "shots", "sourceImages": "tex"})
    check("named template lookup", btk.workspace_template_rules("studio")["scene"] == "shots")
    # A template saved from the editor's preset combo (uitk PresetManager) carries a
    # "_meta" version block — the rules reader must strip it, never surface it as a rule.
    btk.save_workspace_template("gui", {"_meta": {"version": 1}, "scene": "shots"})
    check("PresetManager _meta block is stripped from template rules",
          btk.workspace_template_rules("gui") == {"scene": "shots"})
    btk.delete_workspace_template("gui")
    btk.delete_workspace_template("studio")
    check("deleted template falls back to the standard set",
          btk.workspace_template_rules() == ptk.DEFAULT_FILE_RULES)

    # 6. promote_workspace — describes the EXISTING layout instead of imposing the template.
    flat = os.path.join(tmp, "flat")
    _touch(flat, "scene_a.blend")
    os.makedirs(os.path.join(flat, "textures"))
    promoted = btk.promote_workspace(flat)
    check("promote marks the folder", promoted is not None and promoted.is_marked)
    check("promote: flat blends → scene rule '.'", promoted.rules.get("scene") == ".")
    check("promote: existing textures/ → sourceImages rule",
          promoted.rules.get("sourceImages") == "textures")
    check("promoted scenes_dir is the root itself", _nc(promoted.scene_dir) == _nc(flat))
    check("promoted workspace_scenes_dir → '' (no separate scene folder)",
          btk.workspace_scenes_dir(flat) == "")

    # 7. promote is non-destructive — foreign rules and hand-written lines survive re-promotion.
    with open(promoted.marker_path, "a", encoding="utf-8") as f:
        f.write('//hand-written note\nworkspace -fr "customRule" "custom";\n')
    re_promoted = btk.promote_workspace(flat)
    with open(re_promoted.marker_path, encoding="utf-8") as f:
        marker_text = f.read()
    check("re-promotion keeps the hand-written comment", "//hand-written note" in marker_text)
    check("re-promotion keeps the foreign rule", re_promoted.rules.get("customRule") == "custom")
    check("re-promotion keeps the layout rules", re_promoted.rules.get("scene") == ".")

    # --- bpy-dependent integration (skipped under the .venv) ---------------------------------
    if HAS_BPY:
        # 8. get_env_info routes through the resolver: a .blend saved inside the marked
        #    project reports the PROJECT root as its workspace.
        bpy.ops.wm.read_factory_settings(use_empty=True)
        saved = btk.save_scene_as(os.path.join(proj, "scenes"), "env_probe")
        info_ws = btk.get_env_info("workspace")
        check("get_env_info('workspace') → marked project root",
              saved is not None and _nc(info_ws) == _nc(proj), f"{info_ws}")

        # 9. save_scene_as resolves {scenes} through the workspace's scene rule.
        shots_proj = os.path.join(tmp, "shots_proj")
        btk.create_workspace(shots_proj, rules={"scene": "shots"})
        bpy.ops.wm.read_factory_settings(use_empty=True)
        sub = btk.save_scene_as(shots_proj, "hero", subfolder="{scenes}/{name}")
        check("save_scene_as {scenes} follows the scene rule",
              sub is not None and _nc(os.path.dirname(sub)).endswith(
                  _nc(os.path.join("shots", "hero"))), str(sub))
    else:
        print("SKIP bpy integration checks (no bpy — run under the Blender harness for those)")

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
