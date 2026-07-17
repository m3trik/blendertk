"""blendertk Hierarchy Sidecar headless test — mirror of mayatk's ``test_hierarchy_sidecar``.

Covers the DCC-agnostic sidecar manifest I/O (atomic write, versioned base-stem sharing, rename,
legacy migration, compare, diff report) plus the one bpy-backed helper (``expand_to_descendants``).

Run: blender --background --factory-startup --python blendertk/test/test_hierarchy_sidecar.py
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


tmp = tempfile.mkdtemp(prefix="hier_sidecar_test_")
try:
    import bpy
    from blendertk.env_utils.hierarchy_sync.hierarchy_sidecar import HierarchySidecar as HS

    export = os.path.join(tmp, "shot_v003.fbx")

    # 1. path derivation + base-stem versioning.
    check("manifest_path_for names the sidecar", os.path.basename(HS.manifest_path_for(export)) == ".shot_v003.hierarchy.json")
    check("base_stem strips _vNN", HS.base_stem(export) == "shot")
    check("base_stem manifest shares across versions",
          os.path.basename(HS.manifest_path_for(export, base_stem=True)) == ".shot.hierarchy.json")
    check("base_stem doesn't strip mid-name v", HS.base_stem(os.path.join(tmp, "arch_v2_proxy.fbx")) == "arch_v2_proxy")

    # 2. write -> read round trip.
    paths_a = {"Grp", "Grp|A", "Grp|B"}
    mpath = HS.write_manifest(export, paths_a)
    check("write_manifest returns the manifest path", mpath and os.path.isfile(mpath))
    check("read_manifest round-trips the paths", HS.read_manifest(export) == paths_a)

    # 3. atomic write: a differing rewrite preserves the old baseline as .prev, and never leaves a
    #    stray .tmp behind. (The regression this guards: moving current->.prev BEFORE writing left
    #    no manifest at all if the write failed.)
    paths_b = {"Grp", "Grp|A", "Grp|B", "Grp|C"}
    HS.write_manifest(export, paths_b)
    check("rewrite updates the manifest", HS.read_manifest(export) == paths_b)
    prev = mpath + ".prev"
    check("prior baseline preserved as .prev", os.path.isfile(prev) and set(json.load(open(prev))["paths"]) == paths_a)
    check("no stray .tmp left behind", not os.path.isfile(mpath + ".tmp"))

    # 4. identical rewrite doesn't churn .prev.
    os.remove(prev)
    HS.write_manifest(export, paths_b)
    check("identical rewrite skips .prev churn", not os.path.isfile(prev))

    # 5. compare: hash fast-path + missing/extra.
    match, missing, extra = HS.compare(export, paths_b)
    check("compare identical → match, no diff", match and not missing and not extra)
    match, missing, extra = HS.compare(export, {"Grp", "Grp|A"})
    check("compare detects missing", not match and set(missing) == {"Grp|B", "Grp|C"} and not extra)
    match, missing, extra = HS.compare(export, paths_b | {"Grp|D"})
    check("compare detects extra", not match and extra == ["Grp|D"])
    check("compare with no manifest → match (nothing to diff)",
          HS.compare(os.path.join(tmp, "never.fbx"), {"X"}) == (True, [], []))

    # 5b. .prev fallback: a deleted or corrupt manifest compares against the preserved backup
    #     instead of silently passing; the intact manifest always wins over .prev.
    fb_export = os.path.join(tmp, "fallback.fbx")
    HS.write_manifest(fb_export, {"A", "A|B"})
    HS.write_manifest(fb_export, {"A", "A|B", "A|C"})  # differing -> rolls {A, A|B} to .prev
    fb_manifest = HS.manifest_path_for(fb_export)
    match, missing, extra = HS.compare(fb_export, {"A", "A|B"})
    check("intact manifest wins over .prev", not match and missing == ["A|C"])
    os.remove(fb_manifest)
    match, missing, extra = HS.compare(fb_export, {"A", "A|B"})
    check("deleted manifest falls back to .prev baseline", match and not missing and not extra)
    match, missing, extra = HS.compare(fb_export, {"A"})
    check("fallback baseline still detects drift", not match and missing == ["A|B"])
    check("read_manifest falls back to .prev", HS.read_manifest(fb_export) == {"A", "A|B"})
    with open(fb_manifest, "w") as f:
        f.write("not json{")
    match, missing, extra = HS.compare(fb_export, {"A", "A|B"})
    check("corrupt manifest falls back to .prev", match and not missing and not extra)

    # 6. rename covers per-file, base-stem, and .prev variants.
    HS.write_manifest(export, paths_b)  # ensure a .prev exists
    HS.write_manifest(export, paths_a)  # differing -> creates .prev
    new_export = os.path.join(tmp, "shot_v004.fbx")
    renamed = HS.rename(export, new_export)
    check("rename moves the manifest", HS.read_manifest(new_export) == paths_a and HS.read_manifest(export) is None)
    check("rename also moved the .prev", any(r[1].endswith(".prev") for r in renamed))

    # 7. legacy migration: a per-version sidecar is adopted under the base-stem name.
    legacy_export = os.path.join(tmp, "asset_v007.fbx")
    HS.write_manifest(legacy_export, {"L"})  # writes .asset_v007.hierarchy.json
    check("find_legacy_manifest finds the versioned sidecar",
          HS.find_legacy_manifest(os.path.join(tmp, "asset_v009.fbx")) is not None)
    migrated = HS.ensure_base_name(os.path.join(tmp, "asset_v009.fbx"))
    check("ensure_base_name migrates legacy to base-stem", migrated and os.path.basename(migrated) == ".asset.hierarchy.json")

    # 8. top-level rollup + reparent detection (pure path logic).
    top = HS.get_top_level(["Grp", "Grp|A", "Grp|A|Leaf", "Other"])
    check("get_top_level keeps only shallowest", set(top) == {"Grp", "Other"})
    check("count_descendants counts subtree", HS.count_descendants("Grp", {"Grp", "Grp|A", "Grp|A|Leaf", "Other"}) == 3)
    rep = HS.detect_reparenting(["A", "A|Leaf"], ["NewParent|A", "NewParent|A|Leaf"])
    check("detect_reparenting spots a moved subtree", rep == [("A", "NewParent", 2)])

    # 9. build_clean_path_set is a plain dedup (Blender needs no namespace strip).
    check("build_clean_path_set dedups", HS.build_clean_path_set(["A", "A", "B"]) == {"A", "B"})

    # 10. expand_to_descendants walks children_recursive (bpy).
    bpy.ops.wm.read_factory_settings(use_empty=True)
    root = bpy.data.objects.new("Root", None)
    child = bpy.data.objects.new("Kid", None)
    grand = bpy.data.objects.new("Grand", None)
    for o in (root, child, grand):
        bpy.context.scene.collection.objects.link(o)
    child.parent = root
    grand.parent = child
    expanded = set(HS.expand_to_descendants([root]))
    check("expand_to_descendants includes root + all descendants",
          expanded == {"Root", "Root|Kid", "Root|Kid|Grand"}, str(expanded))

    # 11. write_diff_report writes a readable file.
    dpath = HS.write_diff_report(export, ["Grp|Gone"], ["Grp|New"])
    check("write_diff_report writes the report", dpath and os.path.isfile(dpath) and "Missing" in open(dpath).read())

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
