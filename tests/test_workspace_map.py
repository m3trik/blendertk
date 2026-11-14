"""blendertk WorkspaceMap headless test -- pythontk's Workspace Map bound to Blender
(``BlenderWorkspaceMapHost``): what a workspace and its scenes are in Blender, the
session's workspace as the scan's root, and the projects it builds.

Mirror of mayatk's ``TestWorkspaceMap`` (``test_workspace.py``); the scan, measure
and tree are pythontk's (``test_workspace_map.py``). Disk-level, so it runs under
the Blender harness and the workspace ``.venv`` alike::

  blender --background --factory-startup --python blendertk/tests/test_workspace_map.py
  python blendertk/tests/test_workspace_map.py
"""

import os
import shutil
import sys
import tempfile
import traceback

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


def _write(path, data=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return path


def _norm(paths):
    return sorted(os.path.normcase(os.path.normpath(p)) for p in paths)


tmp = tempfile.mkdtemp(prefix="btk_ws_map_")
nest = tempfile.mkdtemp(prefix="btk_ws_nest_")
try:
    import blendertk as btk
    from blendertk.env_utils.workspace_map import BlenderWorkspaceMapHost, WorkspaceMap

    # A marked project with its scenes in scenes/, a flat folder of .blend files,
    # a marked project holding no scene, and a folder of non-scenes.
    marked = os.path.join(tmp, "marked")
    _write(os.path.join(marked, "workspace.mel"), b'workspace -fr "scene" "scenes";\n')
    shot = _write(os.path.join(marked, "scenes", "shot.blend"))
    _write(os.path.join(marked, "scenes", "shot.blend1"))  # Blender's backup: no scene
    flat = os.path.join(tmp, "flat")
    loose = _write(os.path.join(flat, "loose.blend"))
    empty = os.path.join(tmp, "empty")
    _write(os.path.join(empty, "workspace.mel"), b'workspace -fr "scene" "scenes";\n')
    _write(os.path.join(tmp, "textures", "wood.png"))

    host = WorkspaceMap.HOST
    check("WorkspaceMap is exposed at the root", btk.WorkspaceMap is WorkspaceMap)
    check(
        "the map is pythontk's, bound to Blender",
        isinstance(host, BlenderWorkspaceMapHost)
        and WorkspaceMap.__mro__[1].__name__ == "WorkspaceMapBase",
    )

    # ---- what Blender calls a workspace and its scenes ------------------------------
    found = host.find_workspaces(tmp, recursive=True)
    check(
        "a workspace is a marked or a .blend folder holding a scene",
        _norm(found) == _norm([marked, flat]),
        found,
    )
    check(
        "a workspace's scenes are its .blend files",
        _norm(host.scene_files(marked)) == _norm([shot])
        and _norm(host.scene_files(flat)) == _norm([loose]),
        host.scene_files(marked),
    )

    # ---- nested workspaces: each row counts its own scenes only -----------------------
    top = _write(os.path.join(nest, "top.blend"))  # the root folder is a workspace too
    inner = _write(os.path.join(nest, "inner", "inner.blend"))
    proj = os.path.join(nest, "proj")
    _write(os.path.join(proj, "workspace.mel"), b'workspace -fr "scene" "scenes";\n')
    proj_scene = _write(os.path.join(proj, "scenes", "c.blend"))
    sub_proj = os.path.join(proj, "sub_proj")
    _write(
        os.path.join(sub_proj, "workspace.mel"), b'workspace -fr "scene" "scenes";\n'
    )
    sub_scene = _write(os.path.join(sub_proj, "scenes", "d.blend"))
    rows = host.find_workspaces(nest, recursive=True)
    check(
        "nested discovery: the folders and projects, a project's scenes/ no row",
        _norm(rows) == _norm([nest, os.path.join(nest, "inner"), proj, sub_proj]),
        rows,
    )
    check(
        "an unmarked row stops at the workspaces nested in it",
        _norm(host.scene_files(nest)) == _norm([top]),
        host.scene_files(nest),
    )
    check(
        "a project row stops at the projects nested in it",
        _norm(host.scene_files(proj)) == _norm([proj_scene])
        and _norm(host.scene_files(sub_proj)) == _norm([sub_scene]),
        host.scene_files(proj),
    )
    check(
        "a nested row keeps its own",
        _norm(host.scene_files(os.path.dirname(inner))) == _norm([inner]),
    )

    # ---- the session's workspace roots the scan ---------------------------------------
    btk.set_current_workspace(None)
    wm = WorkspaceMap()
    check(
        "no session workspace: an empty root",
        wm.current_working_dir == "",
        wm.current_working_dir,
    )
    btk.set_current_workspace(tmp)
    wm = WorkspaceMap()
    check(
        "the pinned workspace is the root",
        os.path.normcase(wm.current_working_dir)
        == os.path.normcase(os.path.normpath(tmp)),
        wm.current_working_dir,
    )
    wm.current_working_dir = tmp
    names = sorted(info["name"] for info in wm.workspace_data.values())
    check(
        "the scan lists the workspaces under the root",
        names == ["flat", "marked"],
        names,
    )
    parent = os.path.basename(os.path.normpath(tmp))
    tree = wm.get_workspace_tree_data("mark")
    check(
        "the tree groups by parent folder and filters by name",
        list(tree) == [parent] and list(tree[parent]["workspaces"]) == ["marked"],
        tree,
    )
    info = tree[parent]["workspaces"]["marked"]
    check("a workspace is measured by its scenes", info["scene_count"] == 1, info)

    # ---- the projects it builds -----------------------------------------------------
    built = wm.create_project("built")
    check(
        "New Project builds a marked project under the root",
        built is not None
        and built.is_marked
        and os.path.isdir(os.path.join(tmp, "built")),
    )
    wm.current_working_dir = flat
    promoted = wm.mark_root_as_project()
    check(
        "Mark Root describes the flat folder's layout",
        promoted is not None and promoted.rules.get("scene") == ".",
        getattr(promoted, "rules", None),
    )
    check("a marked root is a project already", wm.mark_root_as_project() is None)

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
    shutil.rmtree(nest, ignore_errors=True)

passed = sum(1 for line in lines if line.startswith("OK"))
for line in lines:
    print(line)
result = "PASS" if all(line.startswith("OK") for line in lines) else "FAIL"
print(f"===RESULT: {result}=== ({passed}/{len(lines)})")
