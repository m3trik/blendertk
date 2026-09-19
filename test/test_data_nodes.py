"""blendertk.node_utils.data_nodes headless test -- the scene store (no viewport).
Run: blender --background --factory-startup --python blendertk/test/test_data_nodes.py

One suite per module (root CLAUDE.md): mirror of mayatk/test/test_data_nodes.py at the
behaviour level.  Covers the two carriers (the ``data_internal`` scene property group,
the ``data_export`` Empty), the ``ptk.SceneStoreBase`` contract, the fold of a file saved
before the group, the record layer, and the retired channel methods.
"""

import json as _json
import os
import pathlib as _pathlib
import sys
import traceback
import warnings

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)  # blendertk/
MONO = os.path.dirname(REPO)  # _scripts/
for p in (REPO, os.path.join(MONO, "pythontk")):
    if p not in sys.path:
        sys.path.insert(0, p)

lines = []


def check(name, cond, detail=""):
    lines.append(
        f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}"
    )


try:
    import bpy
    import pythontk as ptk

    from blendertk.node_utils.data_nodes import DataNodes

    PRIVATE, DELIVERABLE = ptk.Scope.PRIVATE, ptk.Scope.DELIVERABLE

    def reset():
        bpy.ops.object.select_all(action="DESELECT")
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)
        scene = bpy.context.scene
        for key in list(scene.keys()):
            del scene[key]

    # --- the store contract: read / write / values per scope ---------------------
    reset()
    check("read on an empty scene -> None", DataNodes.read(PRIVATE, "x") is None)
    check(
        "clear never creates the private group",
        DataNodes.write(PRIVATE, "x", "") is None
        and bpy.context.scene.get(DataNodes.INTERNAL) is None,
    )
    check(
        "clear never creates the export Empty",
        DataNodes.write(DELIVERABLE, "x", None) is None
        and bpy.data.objects.get(DataNodes.EXPORT) is None,
    )
    check(
        "private write creates the scene group and round-trips",
        DataNodes.write(PRIVATE, "app_state", '{"open": true}') == DataNodes.INTERNAL
        and DataNodes.read(PRIVATE, "app_state") == '{"open": true}'
        and bpy.context.scene.get(DataNodes.INTERNAL) is not None,
    )
    check(
        "the private carrier is NOT an object",
        bpy.data.objects.get(DataNodes.INTERNAL) is None,
    )
    check(
        "deliverable write creates the Empty and round-trips",
        DataNodes.write(DELIVERABLE, "wire", "abc") == DataNodes.EXPORT
        and DataNodes.read(DELIVERABLE, "wire") == "abc"
        and bpy.data.objects.get(DataNodes.EXPORT) is not None,
    )
    check(
        "scopes do not mix",
        DataNodes.read(DELIVERABLE, "app_state") is None
        and DataNodes.read(PRIVATE, "wire") is None,
    )
    check(
        "clear an existing key -> carrier name; reads back None",
        DataNodes.write(DELIVERABLE, "wire", "") == DataNodes.EXPORT
        and DataNodes.read(DELIVERABLE, "wire") is None
        and "wire" in DataNodes.get_export_node(create=False).keys(),
    )
    check(
        "clear a key the carrier does not hold -> None",
        DataNodes.write(DELIVERABLE, "probe", "") is None,
    )

    # non-string props (the audio tool's per-track flags) are values, not channels
    DataNodes.get_internal_node()["audio_clip_voice"] = 1
    check(
        "a non-string property is not a channel",
        DataNodes.read(PRIVATE, "audio_clip_voice") is None,
    )
    check(
        "values reports every property",
        DataNodes.values(PRIVATE)
        == {"app_state": '{"open": true}', "audio_clip_voice": 1},
        f"{DataNodes.values(PRIVATE)}",
    )

    # --- dump / format_dump (inherited from ptk.SceneStoreBase) -------------------
    data = DataNodes.dump()
    check(
        "dump groups by carrier name + decodes JSON",
        data[DataNodes.INTERNAL] == {"app_state": {"open": True}, "audio_clip_voice": 1}
        and data[DataNodes.EXPORT] == {},
        f"{data}",
    )
    DataNodes.write(PRIVATE, "dead", "y")
    DataNodes.write(PRIVATE, "dead", "")
    check(
        "dump skips a cleared channel",
        "dead" not in DataNodes.dump()[DataNodes.INTERNAL],
    )
    check(
        "dump decode=False keeps the raw string",
        DataNodes.dump(decode=False)[DataNodes.INTERNAL]["app_state"]
        == '{"open": true}',
    )
    parsed = _json.loads(DataNodes.format_dump())
    check(
        "format_dump -> valid JSON (mixed types)",
        parsed[DataNodes.INTERNAL]["audio_clip_voice"] == 1
        and parsed[DataNodes.INTERNAL]["app_state"] == {"open": True},
    )
    reset()
    check(
        "format_dump empty -> ''",
        DataNodes.format_dump() == ""
        and DataNodes.dump() == {DataNodes.INTERNAL: {}, DataNodes.EXPORT: {}},
    )

    # --- dump_export_nodes (mirror of mayatk's per-carrier dump) -------------------
    check(
        "dump_export_nodes: no carrier -> {} and nothing created",
        DataNodes.dump_export_nodes() == {}
        and bpy.data.objects.get(DataNodes.EXPORT) is None,
    )
    DataNodes.write(DELIVERABLE, "own", '{"a": [1, 2]}')
    DataNodes.write(PRIVATE, "private_probe", "x")
    check(
        "dump_export_nodes: the carrier's channels, decoded, no private records",
        DataNodes.dump_export_nodes() == {DataNodes.EXPORT: {"own": {"a": [1, 2]}}},
        f"{DataNodes.dump_export_nodes()}",
    )
    reset()

    # --- the fold: a file saved before the scene group -----------------------------
    reset()
    legacy = bpy.data.objects.new(DataNodes.INTERNAL, None)
    bpy.context.scene.collection.objects.link(legacy)
    legacy["smart_bake_sessions"] = "[1]"
    legacy["hierarchy_baseline"] = '{"format": 1}'
    bpy.context.scene["shot_store"] = '{"shots": []}'
    bpy.context.scene["key_stash"] = '{"clips": []}'
    check(
        "unfolded file: records read through the store, from the old locations",
        DataNodes.read(PRIVATE, "smart_bake_sessions") == "[1]"
        and DataNodes.read(PRIVATE, "hierarchy_baseline") == '{"format": 1}'
        and DataNodes.read(PRIVATE, "shot_store") == '{"shots": []}'
        and DataNodes.read(PRIVATE, "key_stash") == '{"clips": []}'
        and set(DataNodes.values(PRIVATE))
        == {"smart_bake_sessions", "hierarchy_baseline", "shot_store", "key_stash"},
    )
    check(
        "a read never migrates (it may run where Blender forbids data edits)",
        bpy.data.objects.get(DataNodes.INTERNAL) is not None
        and "shot_store" in bpy.context.scene.keys()
        and bpy.context.scene.get(DataNodes.INTERNAL) is None,
    )
    DataNodes.write(PRIVATE, "probe", "x")
    check(
        "the first write folds: the Empty is gone, the scene props moved in",
        bpy.data.objects.get(DataNodes.INTERNAL) is None
        and "shot_store" not in bpy.context.scene.keys()
        and "key_stash" not in bpy.context.scene.keys()
        and DataNodes.read(PRIVATE, "smart_bake_sessions") == "[1]"
        and DataNodes.read(PRIVATE, "shot_store") == '{"shots": []}'
        and DataNodes.read(PRIVATE, "probe") == "x",
    )
    check(
        "fold: nothing else in the scene was touched",
        bpy.data.objects.get(DataNodes.EXPORT) is None,
    )
    reset()
    legacy = bpy.data.objects.new(DataNodes.INTERNAL, None)
    bpy.context.scene.collection.objects.link(legacy)
    legacy["stale"] = "old"
    DataNodes.write(PRIVATE, "stale", None)
    check(
        "a clear of a legacy-only record folds first, so the old copy is gone too",
        DataNodes.read(PRIVATE, "stale") is None
        and bpy.data.objects.get(DataNodes.INTERNAL) is None,
    )

    # A fold removes every LOCAL legacy source, so one found beside the group was
    # written AFTER the group existed -- by an older blendertk that reopened the
    # file. Its value is the newer one, for the read and the fold alike.
    reset()
    DataNodes.write(PRIVATE, "hierarchy_baseline", "group-old")
    DataNodes.write(PRIVATE, "shot_store", "group-old")
    DataNodes.write(PRIVATE, "untouched", "kept")
    legacy = bpy.data.objects.new(DataNodes.INTERNAL, None)
    bpy.context.scene.collection.objects.link(legacy)
    legacy["hierarchy_baseline"] = "legacy-new"
    bpy.context.scene["shot_store"] = "legacy-new"
    check(
        "a legacy source beside the group reads as the newer value",
        DataNodes.read(PRIVATE, "hierarchy_baseline") == "legacy-new"
        and DataNodes.read(PRIVATE, "shot_store") == "legacy-new"
        and DataNodes.values(PRIVATE).get("shot_store") == "legacy-new",
        f"{DataNodes.values(PRIVATE)}",
    )
    DataNodes.write(PRIVATE, "probe", "x")
    check(
        "the fold keeps the legacy value over the group's older one",
        DataNodes.get_internal_node(create=False).get("hierarchy_baseline")
        == "legacy-new"
        and DataNodes.get_internal_node(create=False).get("shot_store") == "legacy-new"
        and DataNodes.read(PRIVATE, "untouched") == "kept"
        and bpy.data.objects.get(DataNodes.INTERNAL) is None
        and "shot_store" not in bpy.context.scene.keys(),
    )

    # --- a LINKED module's carriers are the library's, never this file's -----------
    # Measured 2026-09-18: bpy.data.objects.get(name) returns a library object when
    # the file has no local one, so the fold adopted a linked module's private
    # records (its bake-restore manifests) as this file's own.
    reset()
    _lib_file = os.path.join(HERE, "temp_tests", "btk_data_nodes_lib.blend")
    os.makedirs(os.path.dirname(_lib_file), exist_ok=True)
    _coll = bpy.data.collections.new("DataNodesLib")
    _lib_internal = bpy.data.objects.new(DataNodes.INTERNAL, None)
    _lib_internal["smart_bake_sessions"] = '["lib"]'
    _lib_export = bpy.data.objects.new(DataNodes.EXPORT, None)
    _lib_export["lightmap_metadata"] = '{"lib": 1}'
    _coll.objects.link(_lib_internal)
    _coll.objects.link(_lib_export)
    bpy.data.libraries.write(_lib_file, {_coll})
    for _o in (_lib_internal, _lib_export):
        bpy.data.objects.remove(_o, do_unlink=True)
    bpy.data.collections.remove(_coll)
    with bpy.data.libraries.load(_lib_file, link=True) as (_src, _dst):
        _dst.collections = ["DataNodesLib"]
    bpy.context.scene.collection.children.link(_dst.collections[0])
    _linked_internal = next(
        o for o in bpy.data.objects if o.name == DataNodes.INTERNAL and o.library
    )
    try:
        check(
            "a linked data_internal Empty is not read as this file's records",
            DataNodes.read(PRIVATE, "smart_bake_sessions") is None
            and "smart_bake_sessions" not in DataNodes.values(PRIVATE),
        )
        DataNodes.write(PRIVATE, "probe", "x")
        check(
            "the fold adopts nothing from a linked Empty and leaves it in place",
            "smart_bake_sessions" not in DataNodes.get_internal_node(create=False)
            and _linked_internal.get("smart_bake_sessions") == '["lib"]',
        )
        check(
            "get_export_node resolves the file's OWN carrier, never a linked one",
            DataNodes.get_export_node(create=False) is None,
        )
        DataNodes.write(DELIVERABLE, "own", '{"own": 1}')
        _nodes = DataNodes.get_export_nodes()
        check(
            "get_export_nodes: the file's own carrier first, then a linked module's",
            [o.library is None for o in _nodes] == [True, False]
            and all(o.name == DataNodes.EXPORT for o in _nodes)
            and _nodes[0] is DataNodes.get_export_node(create=False),
            f"{[o.name_full for o in _nodes]}",
        )
        _dumped = DataNodes.dump_export_nodes()
        check(
            "dump_export_nodes: every carrier, keyed by its full name",
            _dumped.get(DataNodes.EXPORT) == {"own": {"own": 1}}
            and _dumped.get(_nodes[1].name_full) == {"lightmap_metadata": {"lib": 1}},
            f"{_dumped}",
        )
    finally:
        for _lib in list(bpy.data.libraries):
            bpy.data.libraries.remove(_lib)
        try:
            os.remove(_lib_file)
        except OSError:
            pass
    reset()
    check(
        "get_export_nodes: none in an empty scene", DataNodes.get_export_nodes() == []
    )

    # --- the export carrier: unlinked-carrier heal, node access ----------------------
    reset()
    check(
        "get_export_node(create=False) on an empty scene -> None",
        DataNodes.get_export_node(create=False) is None,
    )
    DataNodes.write(DELIVERABLE, "wire", "abc")
    carrier = DataNodes.get_export_node(create=False)
    bpy.context.scene.collection.objects.unlink(carrier)
    check("unlinked carrier setup", carrier.name not in bpy.context.scene.objects)
    DataNodes.write(DELIVERABLE, "wire2", "def")
    check(
        "a write relinks an unlinked carrier into the scene",
        carrier.name in bpy.context.scene.objects
        and DataNodes.read(DELIVERABLE, "wire2") == "def",
    )
    check("ensure_export is idempotent", DataNodes.ensure_export() is carrier)
    check(
        "channel name constants (mtk parity)",
        DataNodes.FBX_TAKES == "fbx_takes"
        and DataNodes.SHOT_METADATA == "shot_metadata",
    )

    # --- the record layer over the store --------------------------------------------
    reset()
    ptk.SceneRecords.LIGHTMAPS.save(DataNodes, {"objects": [{"name": "a"}]})
    payload = ptk.SceneRecords.LIGHTMAPS.load(DataNodes)
    check(
        "record save/load round-trips with the declared version",
        payload["objects"] == [{"name": "a"}]
        and payload["version"] == ptk.SceneRecords.LIGHTMAPS.version
        and _json.loads(DataNodes.get_export_node(create=False)["lightmap_metadata"])[
            "version"
        ]
        == ptk.SceneRecords.LIGHTMAPS.version,
    )
    check(
        "a falsy payload clears; a clear never creates",
        ptk.SceneRecords.LIGHTMAPS.save(DataNodes, None) == DataNodes.EXPORT
        and ptk.SceneRecords.LIGHTMAPS.load(DataNodes) is None
        and ptk.SceneRecords.SHADOWS.save(DataNodes, {}) is None,
    )
    ptk.SceneRecords.EMISSIVE_REGISTRY.save(DataNodes, {"slots": {"a": 0}})
    check(
        "private and deliverable records with one key stay apart",
        ptk.SceneRecords.EMISSIVE_GROUPS.load(DataNodes) is None
        and ptk.SceneRecords.EMISSIVE_REGISTRY.load(DataNodes)["slots"] == {"a": 0},
    )
    ptk.SceneRecords.LIGHTMAPS.save(DataNodes, {"dir": _pathlib.PurePosixPath("a/b")})
    check(
        "a non-JSON-native value is recorded as its string (default=str)",
        ptk.SceneRecords.LIGHTMAPS.load(DataNodes)["dir"] == "a/b",
    )
    reset()
    ptk.ExportSnapshot.publish(
        DataNodes,
        {ptk.SceneRecords.LIGHTMAPS: {"objects": []}, ptk.SceneRecords.SHADOWS: None},
    )
    handoff = ptk.SceneRecords.HANDOFF.load(DataNodes)
    check(
        "publish commits and stamps the handoff",
        handoff is not None
        and list(handoff["reads"]) == ["data_export.lightmap_metadata"],
    )
    ptk.ExportSnapshot.publish(DataNodes, {ptk.SceneRecords.LIGHTMAPS: None})
    check(
        "a handoff describing nothing is cleared",
        not ptk.SceneRecords.HANDOFF.is_present(DataNodes),
    )

    # --- retired channel methods keep working for one release, and warn --------------
    reset()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        set_ok = DataNodes.set_internal_string("probe", "one") == DataNodes.INTERNAL
        get_ok = DataNodes.get_internal_string("probe") == "one"
        DataNodes.set_export_json("probe", {"version": 1, "items": [1, 2]})
        json_ok = _json.loads(DataNodes.get_export_string("probe")) == {
            "version": 1,
            "items": [1, 2],
        }
        DataNodes.set_internal_json("rec", {"a": 1})
        ijson_ok = DataNodes.get_internal_json("rec") == {"a": 1}
        none_ok = DataNodes.set_export_json("probe2", {}) is None
    check(
        "retired string/JSON methods alias the store",
        set_ok and get_ok and json_ok and ijson_ok and none_ok,
    )
    check(
        "retired methods warn",
        any(issubclass(w.category, DeprecationWarning) for w in caught),
    )
    check(
        "retired write is readable through the store",
        DataNodes.read(PRIVATE, "probe") == "one",
    )

except Exception as e:
    lines.append(f"FAIL setup: {e!r}")
    lines.append(traceback.format_exc())

ok = all(ln.startswith("OK") for ln in lines)
print("\n===DATA-NODES===")
print("\n".join(lines))
print(f"===RESULT: {'PASS' if ok else 'FAIL'}===")
