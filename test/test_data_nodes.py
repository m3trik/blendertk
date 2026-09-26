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

    # --- the fold reaches EVERY scene: the legacy Empty was file-global ------------
    reset()
    _other_scene = bpy.data.scenes.new("Other")
    _legacy = bpy.data.objects.new(DataNodes.INTERNAL, None)
    bpy.context.scene.collection.objects.link(_legacy)
    _legacy["emissive_groups"] = _json.dumps(
        {"schema": 1, "groups": {"g": {"slot": 0}}}
    )
    DataNodes.write(PRIVATE, "probe", "1")  # the first private write folds
    check(
        "fold: the Empty's records reach every local scene, not the current one only",
        all(
            (s.get(DataNodes.INTERNAL) or {}).get("emissive_groups")
            for s in bpy.data.scenes
        ),
        str(
            [
                (s.name, list((s.get(DataNodes.INTERNAL) or {}).keys()))
                for s in bpy.data.scenes
            ]
        ),
    )
    check(
        "fold: the Empty is gone",
        DataNodes._local_object(DataNodes.INTERNAL) is None,
    )
    bpy.data.scenes.remove(_other_scene)

    # --- crossings: a library made local merges its records into this file's --------
    from blendertk.env_utils._env_utils import EnvUtils

    _store = ptk.TempArtifacts("btk_dn_crossing", policy="scoped")
    _SR = ptk.SceneRecords

    def _author_library(path, legacy_groups=None):
        """A library whose objects link by the object fallback (no collection),
        with an object named like the host's (``geo``) and records of its own."""
        bpy.ops.wm.read_factory_settings(use_empty=True)
        geo = bpy.data.objects.new("geo", bpy.data.meshes.new("m"))
        bpy.context.scene.collection.objects.link(geo)
        _SR.AUDIO_FILE_MAP.save(DataNodes, {"2": "b.wav"})
        _SR.SHOT_STORE.save(
            DataNodes,
            {
                "shots": [
                    {
                        "shot_id": 1,
                        "name": "Intro",
                        "start": 0,
                        "end": 5,
                        "objects": ["geo"],
                    }
                ]
            },
        )
        ptk.ExportSnapshot.publish(
            DataNodes, {_SR.LIGHTMAPS: {"objects": [{"name": "geo", "map": "l.exr"}]}}
        )
        if legacy_groups:
            empty = bpy.data.objects.new(DataNodes.INTERNAL, None)
            bpy.context.scene.collection.objects.link(empty)
            empty["emissive_groups"] = _json.dumps(
                {"schema": 1, "groups": legacy_groups}
            )
        bpy.ops.wm.save_as_mainfile(filepath=path)

    def _host_linking(path):
        """A host with its own ``geo``, audio clips, emissive group and export
        carrier, the library linked; returns the library."""
        bpy.ops.wm.read_factory_settings(use_empty=True)
        geo = bpy.data.objects.new("geo", bpy.data.meshes.new("h"))
        bpy.context.scene.collection.objects.link(geo)
        _SR.AUDIO_FILE_MAP.save(DataNodes, {"1": "a.wav"})
        _SR.EMISSIVE_REGISTRY.save(
            DataNodes, {"schema": 1, "groups": {"rim": {"slot": 0, "default": 1.0}}}
        )
        ptk.ExportSnapshot.publish(
            DataNodes, {_SR.SHADOWS: {"planes": [{"name": "p"}]}}
        )
        EnvUtils.link_blend_file(path)
        return next(iter(bpy.data.libraries))

    _lib_path = os.path.join(_store.dir_path(), "lib.blend")
    _author_library(_lib_path, legacy_groups={"glow": {"slot": 0, "default": 0.5}})
    _lib = _host_linking(_lib_path)
    _made = EnvUtils.make_library_local(_lib)
    check("make local: the datablocks came local", _made > 0, str(_made))
    _audio = _SR.AUDIO_FILE_MAP.load(DataNodes) or {}
    check(
        "merge: a mapping record unites (this file's entry kept), the library's "
        "path spelled from ITS project and landed from this file's -- absolute "
        "while this one is unsaved (2026-09-23)",
        _audio.get("1") == "a.wav"
        and os.path.normcase(_audio.get("2", ""))
        == os.path.normcase(
            os.path.join(os.path.dirname(_lib_path), "b.wav").replace("\\", "/")
        ),
        str(_audio),
    )
    _shots = (_SR.SHOT_STORE.load(DataNodes) or {}).get("shots") or [{}]
    check(
        "merge: the shot's member respells to where the move put it",
        _shots[0].get("objects") == ["geo.001"],
        str(_shots),
    )
    _groups = (_SR.EMISSIVE_REGISTRY.load(DataNodes) or {}).get("groups") or {}
    check(
        "merge: the library's pre-group Empty never replaces this file's registry",
        _groups.get("rim", {}).get("slot") == 0
        and _groups.get("glow", {}).get("slot") == 1,
        str(_groups),
    )
    check(
        "merge: no carrier is left holding records nothing reads",
        [o.name for o in bpy.data.objects if o.name.startswith("data_")]
        == [DataNodes.EXPORT],
        str([o.name for o in bpy.data.objects]),
    )
    check(
        "merge: this file's own deliverable stands",
        _SR.SHADOWS.is_present(DataNodes),
    )

    _author_library(_lib_path)
    _lib = _host_linking(_lib_path)
    _asked = []
    _made = EnvUtils.make_library_local(
        _lib, scene_data=lambda summary, name: _asked.append((summary, name)) or None
    )
    check(
        "decide: asked with what arrives; None leaves the library linked",
        _made == 0
        and len(bpy.data.libraries) == 1
        and any("Audio Clips" in line for line in (_asked[0][0] if _asked else [])),
        str(_asked),
    )
    EnvUtils.make_library_local(bpy.data.libraries[0], scene_data="discard")
    check(
        "discard: the library's records go with its carriers",
        _SR.AUDIO_FILE_MAP.load(DataNodes) == {"1": "a.wav"}
        and _SR.SHOT_STORE.load(DataNodes) is None
        and not any(o.name.startswith("data_export.") for o in bpy.data.objects),
        str(_SR.AUDIO_FILE_MAP.load(DataNodes)),
    )

    # A GUI session writes the shot store on a timer, so a script that adds a
    # shot and makes a library local in one go still holds it unwritten: the
    # move stores it first, and the merge keeps it beside the library's.
    from unittest import mock as _mock

    from blendertk.anim_utils.shots._shots import BlenderShotStore

    _author_library(_lib_path)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    BlenderShotStore.clear_active()
    with _mock.patch.object(BlenderShotStore, "_schedule_flush", lambda self: None):
        BlenderShotStore.active().define_shot("HostShot", 10.0, 20.0)
        EnvUtils.link_blend_file(_lib_path)
        EnvUtils.make_library_local(next(iter(bpy.data.libraries)))
    BlenderShotStore.flush_pending()  # the timer write the session held
    BlenderShotStore.clear_active()
    _names = sorted(s.name for s in BlenderShotStore.active().shots)
    check(
        "merge: a shot the host held unwritten is kept beside the library's",
        _names == ["HostShot", "Intro"],
        str(_names),
    )
    BlenderShotStore.clear_active()

    # --- crossings: the portable records as a hand-off's sections ---------------------
    bpy.ops.wm.read_factory_settings(use_empty=True)
    from blendertk.mat_utils.emissive_groups import EmissiveGroups

    bpy.ops.mesh.primitive_cube_add()
    _cube = bpy.context.active_object
    EmissiveGroups.add_group("glow", {_cube.name: [1, 3]})
    _sections = _json.loads(
        _json.dumps(DataNodes.transfer_sections(objects=[_cube.name]))
    )
    _cube_name = _cube.name
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_cube_add()
    _cube = bpy.context.active_object
    _ctx = DataNodes.receive_sections(_sections, resolve={_cube_name: _cube.name}.get)
    check(
        "hand-off: an emissive group lands with its membership",
        EmissiveGroups.list_groups().get("glow", {}).get("faces") == 2,
        str(_ctx.notes),
    )

    class _Named:
        def __init__(self, name):
            self.name = name

    # After the move: the object "Cube" became Cube.001; of two datablocks named
    # "Walk", the action was renamed and the object kept its name.
    _a, _b, _c = _Named("Cube.001"), _Named("Walk.001"), _Named("Walk")
    _rename = DataNodes.library_renames([(_a, "Cube"), (_b, "Walk"), (_c, "Walk")])
    check(
        "renames: a renamed object maps, its ledger key maps through it, an "
        "ambiguous name maps to nothing",
        _rename("Cube") == "Cube.001"
        and _rename("Cube|location|0") == "Cube.001|location|0"
        and _rename("Walk") is None,
        str((_rename("Cube"), _rename("Walk"))),
    )
    _store.cleanup()
    bpy.ops.wm.read_factory_settings(use_empty=True)

    # --- project-relative paths follow the file (mirror of mayatk) -----------------
    # BACKLOG 2026-09-22, decided 2026-09-23: a path record is spelled from the
    # FILE's own project, ``../`` chains included, so a Save As into another
    # project re-spells it (``save_pre`` gets the target while bpy.data.filepath
    # is still the old one), and a Save COPY leaves the open file's alone.
    _paths = ptk.TempArtifacts("btk_dn_paths", policy="scoped")
    _root = _paths.dir_path()
    _proj_a = os.path.join(_root, "shows", "a")
    _proj_b = os.path.join(_root, "shows", "deeper", "b")
    for _proj in (_proj_a, _proj_b):
        os.makedirs(os.path.join(_proj, "scenes"), exist_ok=True)
        with open(os.path.join(_proj, "workspace.mel"), "w") as _fh:
            _fh.write("//Maya 2025 Project Definition\n")
    _lib = os.path.join(_root, "library", "lm")
    _maps = os.path.join(_proj_a, "sourceimages", "lm")
    for _d in (_lib, _maps):
        os.makedirs(_d, exist_ok=True)

    def _abs(base, spelled):
        return os.path.normcase(os.path.normpath(os.path.join(base, spelled)))

    DataNodes.install_path_rebase()
    DataNodes.install_path_rebase()  # idempotent: one pair, not two
    _pre = [
        f
        for f in bpy.app.handlers.save_pre
        if getattr(f, "__name__", "") == "_rebase_before_save"
    ]
    check("the re-base installs one persistent pair", len(_pre) == 1, str(_pre))
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(_proj_a, "scenes", "shot.blend"))
    check(
        "the file's own project is the project it lives in",
        os.path.normcase(DataNodes.project_root() or "") == os.path.normcase(_proj_a),
        str(DataNodes.project_root()),
    )
    ptk.SceneRecords.LIGHTMAP_DIRS.save(
        DataNodes,
        {
            "in.exr": ptk.FileUtils.portable_path(_maps, _proj_a),
            "lib.exr": ptk.FileUtils.portable_path(_lib, _proj_a),
        },
    )
    _stored = ptk.SceneRecords.LIGHTMAP_DIRS.load(DataNodes)
    check(
        "spelled from the project: inside it, and a ../ chain beside it",
        _stored == {"in.exr": "sourceimages/lm", "lib.exr": "../../library/lm"},
        str(_stored),
    )
    # The hierarchy baseline names the file that recorded it (2026-09-24), and
    # that stamp is a path: left spelled from the source's project, a copy at
    # the SAME relative path in another project resolved it to ITSELF and owned
    # its source's baseline.
    from blendertk.env_utils.hierarchy_sync.hierarchy_baseline import (
        HierarchyBaseline as _HB,
    )

    _HB.write({"GRP"})
    check("the source owns its baseline", _HB.inherited_from() is None)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(_proj_b, "scenes", "shot.blend"))
    _from = _HB.inherited_from()
    check(
        "a copy saved into another project still names the baseline's source",
        _from is not None
        and _abs(_proj_b, _from)
        == os.path.normcase(os.path.join(_proj_a, "scenes", "shot.blend")),
        repr(_from),
    )
    _moved = ptk.SceneRecords.LIGHTMAP_DIRS.load(DataNodes)
    check(
        "a Save As into another project re-spells every entry, same folders",
        _moved["lib.exr"] == "../../../library/lm"
        and _abs(_proj_b, _moved["in.exr"]) == os.path.normcase(_maps)
        and _abs(_proj_b, _moved["lib.exr"]) == os.path.normcase(_lib),
        str(_moved),
    )
    bpy.ops.wm.save_as_mainfile(
        filepath=os.path.join(_proj_a, "scenes", "copy.blend"), copy=True
    )
    check(
        "a Save Copy leaves the open file's spellings as they were",
        ptk.SceneRecords.LIGHTMAP_DIRS.load(DataNodes) == _moved,
        str(ptk.SceneRecords.LIGHTMAP_DIRS.load(DataNodes)),
    )
    bpy.ops.wm.open_mainfile(filepath=os.path.join(_proj_a, "scenes", "copy.blend"))
    _copied = ptk.SceneRecords.LIGHTMAP_DIRS.load(DataNodes)
    check(
        "...and the copy on disk carries its own project's spellings",
        _copied == {"in.exr": "sourceimages/lm", "lib.exr": "../../library/lm"},
        str(_copied),
    )
    # A save that FAILS moves nothing: save_pre re-spelled the records for its
    # target, and the file still open is spelled back (save_post_fail).
    _before_fail = ptk.SceneRecords.LIGHTMAP_DIRS.load(DataNodes)
    _blocked = os.path.join(_proj_b, "scenes", "blocked.blend")
    os.makedirs(_blocked)  # a folder where the file would go: the write fails
    try:
        bpy.ops.wm.save_as_mainfile(filepath=_blocked)
    except RuntimeError:
        pass
    check(
        "a Save As that fails leaves the open file's spellings as they were",
        os.path.basename(bpy.data.filepath) == "copy.blend"
        and ptk.SceneRecords.LIGHTMAP_DIRS.load(DataNodes) == _before_fail,
        f"{bpy.data.filepath} {ptk.SceneRecords.LIGHTMAP_DIRS.load(DataNodes)}",
    )
    # A Save Copy of a file NEVER saved: the open file has no project, so its
    # spellings stay absolute -- a copy's must not stay behind in it.
    bpy.ops.wm.read_factory_settings(use_empty=True)
    DataNodes.install_path_rebase()
    _lib_abs = ptk.FileUtils.portable_path(_lib, None)
    ptk.SceneRecords.LIGHTMAP_DIRS.save(DataNodes, {"lib.exr": _lib_abs})
    _unsaved_copy = os.path.join(_proj_a, "scenes", "unsaved_copy.blend")
    bpy.ops.wm.save_as_mainfile(filepath=_unsaved_copy, copy=True)
    check(
        "a Save Copy of an unsaved file leaves its spellings absolute",
        not bpy.data.filepath
        and ptk.SceneRecords.LIGHTMAP_DIRS.load(DataNodes) == {"lib.exr": _lib_abs},
        str(ptk.SceneRecords.LIGHTMAP_DIRS.load(DataNodes)),
    )
    bpy.ops.wm.open_mainfile(filepath=_unsaved_copy)
    check(
        "...and that copy on disk is spelled from its own project",
        ptk.SceneRecords.LIGHTMAP_DIRS.load(DataNodes)
        == {"lib.exr": "../../library/lm"},
        str(ptk.SceneRecords.LIGHTMAP_DIRS.load(DataNodes)),
    )
    DataNodes.remove_path_rebase()
    check(
        "remove takes the pair out",
        not any(
            getattr(f, "__name__", "") == "_rebase_before_save"
            for f in bpy.app.handlers.save_pre
        ),
    )
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _paths.cleanup()

except Exception as e:
    lines.append(f"FAIL setup: {e!r}")
    lines.append(traceback.format_exc())

ok = all(ln.startswith("OK") for ln in lines)
print("\n===DATA-NODES===")
print("\n".join(lines))
print(f"===RESULT: {'PASS' if ok else 'FAIL'}===")
