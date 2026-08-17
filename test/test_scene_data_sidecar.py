"""blendertk Scene-Data Sidecar headless test — mirror of mayatk's ``test_scene_data_sidecar``.

Covers the DCC-agnostic sidecar manifest I/O (format-v3 single-file contract: no ``.prev``
companions, write-time sweep of v2-era leftovers, ``last_diff`` record, hidden attribute on
Windows, atomic write, versioned base-stem sharing, rename, legacy-name + per-version
migration, compare, diff report text) plus the one bpy-backed helper
(``expand_to_descendants``).

Run: blender --background --factory-startup --python blendertk/test/test_scene_data_sidecar.py
"""
import sys, os, json, tempfile, shutil, traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for p in (REPO, os.path.join(MONO, "pythontk")):
    if p not in sys.path:
        sys.path.insert(0, p)

lines = []


def check(name, cond, detail=""):
    lines.append(f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}")


tmp = tempfile.mkdtemp(prefix="scene_data_sidecar_test_")
try:
    import bpy
    from blendertk.env_utils.hierarchy_sync.scene_data_sidecar import SceneDataSidecar as SD

    export = os.path.join(tmp, "shot_v003.fbx")

    # 1. path derivation + base-stem versioning.
    check("manifest_path_for names the sidecar", os.path.basename(SD.manifest_path_for(export)) == ".shot_v003.scene_data.json")
    check("base_stem strips _vNN", SD.base_stem(export) == "shot")
    check("base_stem manifest shares across versions",
          os.path.basename(SD.manifest_path_for(export, base_stem=True)) == ".shot.scene_data.json")
    check("base_stem doesn't strip mid-name v", SD.base_stem(os.path.join(tmp, "arch_v2_proxy.fbx")) == "arch_v2_proxy")

    # 2. write -> read round trip; format-v3 sectioned structure on disk.
    paths_a = {"Grp", "Grp|A", "Grp|B"}
    mpath = SD.write_manifest(export, paths_a)
    check("write_manifest returns the manifest path", mpath and os.path.isfile(mpath))
    check("read_manifest round-trips the paths", SD.read_manifest(export) == paths_a)
    raw = json.load(open(mpath))
    check("manifest is format 3 with a hierarchy section",
          raw.get("format") == 3 and raw["hierarchy"]["object_count"] == 3 and bool(raw["hierarchy"]["hash"]))
    check("empty data section is omitted", "data_export" not in raw and "paths" not in raw)
    check("no diff section when none recorded", "last_diff" not in raw["hierarchy"])

    # 2b. data snapshot round-trip; hierarchy read unaffected.
    data_a = {"lightmap_metadata": {"sets": [1, 2]}, "note": "x"}
    SD.write_manifest(export, paths_a, data=data_a)
    check("read_data round-trips the data section", SD.read_data(export) == data_a)
    check("hierarchy read unaffected by data section", SD.read_manifest(export) == paths_a)

    # 2c. data churn: hash covers only paths — the hierarchy check must not trip,
    #     and the single-file contract means no .prev shadow copy appears.
    SD.write_manifest(export, paths_a, data={"lightmap_metadata": {"sets": [3]}})
    check("data-only change keeps hierarchy compare matching", SD.compare(export, paths_a) == (True, [], []))
    check("data-only change creates no .prev", not os.path.exists(mpath + ".prev"))

    # 2c-bis. The sidecar ships beside the deliverable, so it records no
    #     authoring-machine paths. `lightmap_metadata.dir` is a build-time hint
    #     for the GLB converter; a recipient can do nothing with a path on
    #     someone else's drive but read the folder names in it.
    hint_export = os.path.join(tmp, "hint.fbx")
    authored = r"O:\Dropbox (Client)\Team Folder\PROD\sourceimages"
    SD.write_manifest(hint_export, {"A"}, data={
        "lightmap_metadata": {"version": 1, "dir": authored,
                              "objects": [{"name": "room", "map": "room_Lightmap.exr"}]},
        "note": "x",
    })
    hint_raw = open(SD.manifest_path_for(hint_export)).read()
    written = json.loads(hint_raw)["data_export"]
    check("authoring locate hint is not recorded",
          "Dropbox (Client)" not in hint_raw and "dir" not in written["lightmap_metadata"])
    check("scrub keeps the payload and sibling channels",
          written["lightmap_metadata"]["objects"] == [{"name": "room", "map": "room_Lightmap.exr"}]
          and written["note"] == "x")

    # 2d. v1 flat manifest content is still readable (hierarchy section shim).
    v1_export = os.path.join(tmp, "v1style.fbx")
    v1_paths = ["A", "A|B"]
    with open(SD.manifest_path_for(v1_export), "w") as f:
        json.dump({"paths": v1_paths, "object_count": 2, "hash": SD._paths_hash(v1_paths)}, f)
    check("v1 flat manifest reads as hierarchy section", SD.read_manifest(v1_export) == set(v1_paths))
    check("v1 flat manifest has no data section", SD.read_data(v1_export) is None)
    check("v1 flat manifest compares via hash fast-path", SD.compare(v1_export, set(v1_paths)) == (True, [], []))

    # 3. single-file contract: a differing rewrite lands atomically (tmp+replace, the live
    #    manifest is never displaced), leaves no .prev and no stray .tmp.
    paths_b = {"Grp", "Grp|A", "Grp|B", "Grp|C"}
    SD.write_manifest(export, paths_b)
    check("rewrite updates the manifest", SD.read_manifest(export) == paths_b)
    check("rewrite creates no .prev", not os.path.exists(mpath + ".prev"))
    check("no stray .tmp left behind", not os.path.isfile(mpath + ".tmp"))

    # 3b. last_diff: recorded when passed, dropped by the next clean write, invisible to reads.
    a_diff = {"missing": ["Grp|Gone"], "extra": ["Grp|New"], "reparented": []}
    SD.write_manifest(export, paths_b, last_diff=a_diff)
    check("last_diff recorded under hierarchy", json.load(open(mpath))["hierarchy"].get("last_diff") == a_diff)
    check("last_diff invisible to compare", SD.compare(export, paths_b) == (True, [], []))
    SD.write_manifest(export, paths_b)
    check("clean write drops last_diff", "last_diff" not in json.load(open(mpath))["hierarchy"])

    # 3c. Windows: the manifest carries the hidden attribute through rewrites
    #     (dot-prefix hides nothing on Windows; os.replace strips the flag, so
    #     the writer re-applies it).
    if os.name == "nt":
        import stat
        check("manifest hidden after write",
              bool(os.stat(mpath).st_file_attributes & stat.FILE_ATTRIBUTE_HIDDEN))

    # 4. write-time sweep: v2-era companions (.prev, v1 names, on-disk diff report)
    #    disappear on the next successful write.
    for leftover in (mpath + ".prev", os.path.join(tmp, ".shot_v003.hierarchy.json.prev"),
                     os.path.join(tmp, ".shot_v003.hierarchy_diff.txt")):
        with open(leftover, "w") as f:
            f.write("{}")
    SD.write_manifest(export, paths_b)
    check("write sweeps v2-era companions",
          not any(os.path.exists(p) for p in (mpath + ".prev",
                                              os.path.join(tmp, ".shot_v003.hierarchy.json.prev"),
                                              os.path.join(tmp, ".shot_v003.hierarchy_diff.txt"))))

    # 5. compare: hash fast-path + missing/extra.
    match, missing, extra = SD.compare(export, paths_b)
    check("compare identical → match, no diff", match and not missing and not extra)
    match, missing, extra = SD.compare(export, {"Grp", "Grp|A"})
    check("compare detects missing", not match and set(missing) == {"Grp|B", "Grp|C"} and not extra)
    match, missing, extra = SD.compare(export, paths_b | {"Grp|D"})
    check("compare detects extra", not match and extra == ["Grp|D"])
    check("compare with no manifest → match (nothing to diff)",
          SD.compare(os.path.join(tmp, "never.fbx"), {"X"}) == (True, [], []))

    # 5b. .prev fallback (transition): v3 never writes .prev, but one left by a v2 writer is
    #     still the last-known-good baseline — a deleted or corrupt manifest compares against
    #     it instead of silently passing; the intact manifest always wins over it.
    fb_export = os.path.join(tmp, "fallback.fbx")
    SD.write_manifest(fb_export, {"A", "A|B", "A|C"})
    fb_manifest = SD.manifest_path_for(fb_export)
    fb_old = ["A", "A|B"]
    with open(fb_manifest + ".prev", "w") as f:  # hand-placed, as a v2 writer left it
        json.dump({"format": 2, "hierarchy": {"paths": fb_old, "object_count": 2,
                                              "hash": SD._paths_hash(fb_old)}}, f)
    match, missing, extra = SD.compare(fb_export, {"A", "A|B"})
    check("intact manifest wins over .prev", not match and missing == ["A|C"])
    os.remove(fb_manifest)
    match, missing, extra = SD.compare(fb_export, {"A", "A|B"})
    check("deleted manifest falls back to .prev baseline", match and not missing and not extra)
    match, missing, extra = SD.compare(fb_export, {"A"})
    check("fallback baseline still detects drift", not match and missing == ["A|B"])
    check("read_manifest falls back to .prev", SD.read_manifest(fb_export) == {"A", "A|B"})
    with open(fb_manifest, "w") as f:  # fresh file (the hidden original was removed above)
        f.write("not json{")
    match, missing, extra = SD.compare(fb_export, {"A", "A|B"})
    check("corrupt manifest falls back to .prev", match and not missing and not extra)

    # 6. rename covers per-file, base-stem, and surviving v2-era .prev variants.
    SD.write_manifest(export, paths_a)
    with open(mpath + ".prev", "w") as f:  # hand-placed v2-era leftover rides along
        json.dump({"paths": ["stale"]}, f)
    new_export = os.path.join(tmp, "shot_v004.fbx")
    renamed = SD.rename(export, new_export)
    check("rename moves the manifest", SD.read_manifest(new_export) == paths_a and SD.read_manifest(export) is None)
    check("rename also moved the .prev", any(r[1].endswith(".prev") for r in renamed))

    # 7. legacy migration: a per-version sidecar is adopted under the base-stem name.
    legacy_export = os.path.join(tmp, "asset_v007.fbx")
    SD.write_manifest(legacy_export, {"L"})  # writes .asset_v007.scene_data.json
    check("find_legacy_manifest finds the versioned sidecar",
          SD.find_legacy_manifest(os.path.join(tmp, "asset_v009.fbx")) is not None)
    migrated = SD.ensure_base_name(os.path.join(tmp, "asset_v009.fbx"))
    check("ensure_base_name migrates legacy to base-stem", migrated and os.path.basename(migrated) == ".asset.scene_data.json")

    # 7b. v1-name migration: same-stem `.hierarchy.json` (and an orphaned .prev)
    #     promote to `.scene_data.json`; v1 content stays readable.
    old_export = os.path.join(tmp, "oldname.fbx")
    with open(os.path.join(tmp, ".oldname.hierarchy.json"), "w") as f:
        json.dump({"paths": ["O"], "object_count": 1}, f)
    with open(os.path.join(tmp, ".oldname.hierarchy.json.prev"), "w") as f:
        json.dump({"paths": ["P"]}, f)
    promoted = SD.migrate_legacy(old_export)
    check("migrate_legacy promotes the v1 name",
          promoted and os.path.basename(promoted) == ".oldname.scene_data.json"
          and not os.path.exists(os.path.join(tmp, ".oldname.hierarchy.json")))
    check("migrate_legacy carries the .prev backup",
          os.path.isfile(promoted + ".prev") and not os.path.exists(os.path.join(tmp, ".oldname.hierarchy.json.prev")))
    check("promoted v1 content stays readable", SD.read_manifest(old_export) == {"O"})
    # Versioned v1-named sidecars promote too; the current naming wins a version tie.
    with open(os.path.join(tmp, ".mixed_v002.hierarchy.json"), "w") as f:
        json.dump({"paths": ["M"]}, f)
    found = SD.find_legacy_manifest(os.path.join(tmp, "mixed_v003.fbx"))
    check("find_legacy_manifest matches v1-named versions", found and found.endswith(".mixed_v002.hierarchy.json"))

    # 7c. deprecated import location still resolves to the same class.
    from blendertk.env_utils.hierarchy_sync.hierarchy_sidecar import HierarchySidecar

    check("hierarchy_sidecar shim aliases SceneDataSidecar", HierarchySidecar is SD)

    # 8. top-level rollup + reparent detection (pure path logic).
    top = SD.get_top_level(["Grp", "Grp|A", "Grp|A|Leaf", "Other"])
    check("get_top_level keeps only shallowest", set(top) == {"Grp", "Other"})
    check("count_descendants counts subtree", SD.count_descendants("Grp", {"Grp", "Grp|A", "Grp|A|Leaf", "Other"}) == 3)
    rep = SD.detect_reparenting(["A", "A|Leaf"], ["NewParent|A", "NewParent|A|Leaf"])
    check("detect_reparenting spots a moved subtree", rep == [("A", "NewParent", 2)])

    # 9. build_clean_path_set is a plain dedup (Blender needs no namespace strip).
    check("build_clean_path_set dedups", SD.build_clean_path_set(["A", "A", "B"]) == {"A", "B"})

    # 10. expand_to_descendants walks children_recursive (bpy).
    bpy.ops.wm.read_factory_settings(use_empty=True)
    root = bpy.data.objects.new("Root", None)
    child = bpy.data.objects.new("Kid", None)
    grand = bpy.data.objects.new("Grand", None)
    for o in (root, child, grand):
        bpy.context.scene.collection.objects.link(o)
    child.parent = root
    grand.parent = child
    expanded = set(SD.expand_to_descendants([root]))
    check("expand_to_descendants includes root + all descendants",
          expanded == {"Root", "Root|Kid", "Root|Kid|Grand"}, str(expanded))

    # 11. format_diff_report returns the report text; nothing lands beside the export.
    report = SD.format_diff_report(["Grp|Gone"], ["Grp|New"])
    check("format_diff_report renders the report",
          "Hierarchy Diff Report" in report and "Missing:  1" in report and "  + Grp|New" in report)
    check("no report file beside the export", not os.path.isfile(SD.diff_report_path_for(export)))

except Exception as e:
    traceback.print_exc()
    check("test raised", False, repr(e))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

passed = sum(1 for line in lines if line.startswith("OK"))
for line in lines:
    print(line)
result = "PASS" if all(line.startswith("OK") for line in lines) else "FAIL"
print(f"===RESULT: {result}=== ({passed}/{len(lines)})")
