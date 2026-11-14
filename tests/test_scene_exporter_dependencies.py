"""Scene Exporter: the FBX carries every file its scene records name (blendertk).

The user's decision (2026-10-05): "all dependencies should be embedded into the
fbx. 0 sidecar." A cube with a committed lightmap and a HORIZON shadow rig is
exported through the real ``SceneExporter.perform_export``: the FBX must embed
its material's texture (``embed_textures``, pinned over any preset), the
lightmap, the silhouette and the horizon map -- ``FbxUtils.embed_dependencies``
(``pythontk.FbxMedia.embed_dependencies``), the same post-write step as mayatk's
-- and nothing may land beside it: processed maps stage in scratch, and the
exporter's own ``.scene_data.json`` record lives in the per-user store. The
Unity half of the contract (only the FBX copied in, every map resolved) is
the Unity package's samples' Edit Mode tests (``<F>SampleTests``) and their
twins' (``m3trik/tests/unity_e2e/``: each sample delivered again, this
exporter's among them, and held to the same tests).

Run: blender --background --factory-startup --python blendertk/tests/test_scene_exporter_dependencies.py
"""

import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
# uitk: the task definitions build their tooltips with its TooltipFormat.
for p in (REPO, os.path.join(MONO, "pythontk"), os.path.join(MONO, "uitk")):
    if p not in sys.path:
        sys.path.insert(0, p)

import pythontk as ptk  # noqa: E402 -- after the sibling paths above

store = ptk.TempArtifacts("btk_export_deps", policy="scoped")
root = store.dir_path()
# Never the real presets store: the exporter reads its FBX preset tier.
os.environ["UITK_PRESETS_ROOT"] = os.path.join(root, "presets")

lines = []


def check(name, cond, detail=""):
    lines.append(
        f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + str(detail)) if detail else ''}"
    )


try:
    import bpy
    from blendertk.mat_utils._mat_utils import MatUtils
    from blendertk.env_utils.scene_exporter._scene_exporter import SceneExporter
    from blendertk.light_utils.lightmap_baker.lightmap_baker import LightmapBaker
    from blendertk.rig_utils.shadow_rig import ShadowRig

    bpy.ops.wm.read_factory_settings(use_empty=True)
    # Saved first, so the rig writes its PNGs to <root>/sourceimages.
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(root, "embedded_deps.blend"))
    images = os.path.join(root, "sourceimages")
    os.makedirs(images, exist_ok=True)

    bpy.ops.mesh.primitive_cube_add(size=2)
    cube = bpy.context.active_object
    cube.name = "Crate"

    # An authored material reading a PNG: a loose-media FBX needed it copied
    # beside the deliverable.
    base_color = os.path.join(images, "Crate_Base_color.png")
    texture = bpy.data.images.new("Crate_Base_color", 4, 4)
    texture.pixels.foreach_set([0.5, 0.25, 0.125, 1.0] * 16)
    texture.filepath_raw = base_color
    texture.file_format = "PNG"
    texture.save()
    material = bpy.data.materials.new("Crate_mat")
    tree = MatUtils.get_node_tree(material, create=True)
    tex_node = tree.nodes.new("ShaderNodeTexImage")
    tex_node.image = texture
    tree.links.new(
        tex_node.outputs["Color"], tree.nodes["Principled BSDF"].inputs["Base Color"]
    )
    cube.data.materials.append(material)

    lightmap = os.path.join(images, "Crate_Lightmap.exr")
    image = bpy.data.images.new("Crate_Lightmap", 8, 8, float_buffer=True)
    image.pixels.foreach_set([0.5, 0.4, 0.3, 1.0] * 64)
    image.filepath_raw = lightmap
    image.file_format = "OPEN_EXR"
    image.save()
    check("the lightmap EXR is on disk", os.path.isfile(lightmap), lightmap)
    # The lightmap UVs on the second UV map, where the record reads every map
    # (a set the mesh lacks is refused).
    cube.data.uv_layers.new(name="Lightmap")
    LightmapBaker().commit_lightmap({cube.name: lightmap})

    rig = ShadowRig.create(
        [cube],
        light_pos=(5, 5, 10),
        texture_res=64,
        rig_type="horizon",
        horizon_size=32,
    )
    rig.bake(1, 3)
    sources = {
        os.path.basename(p): p for p in (lightmap, rig.texture_path, rig.horizon_path)
    }
    check(
        "the rig wrote its silhouette and horizon map",
        all(os.path.isfile(p) for p in sources.values()),
        f"{sources}",
    )

    # A sound strip on the timeline: Unity matches an AudioClip to an
    # ``audio`` event by name, and an event names its TRACK (the
    # strip), not the strip's file -- so the file goes in under the event's
    # name, its extension kept (mirror of mayatk's
    # test_the_shipped_audio_rides_inside_under_its_event_names).
    import json
    import wave

    from blendertk.audio_utils._audio_utils import AudioUtils

    wav = os.path.join(root, "sound", "footstep_take3.wav")
    os.makedirs(os.path.dirname(wav), exist_ok=True)
    with wave.open(wav, "wb") as clip:
        clip.setnchannels(1)
        clip.setsampwidth(2)
        clip.setframerate(22050)
        clip.writeframes(b"\x00\x10" * 2205)
    track = AudioUtils.add_clip(wav, frame_start=1, name="footstep")

    out = os.path.join(root, "deliverable")
    os.makedirs(out, exist_ok=True)
    ok = SceneExporter().perform_export(
        export_dir=out,
        objects=[cube, rig.group, rig.shadow_plane, rig.contact],
        output_name="embedded_deps",
        export_visible=True,
        tasks={"export_data_node": True},
    )
    fbx = os.path.join(out, "embedded_deps.fbx")
    check("the export succeeded", bool(ok) and os.path.isfile(fbx))

    named = ptk.FbxMedia.dependencies(fbx)
    embedded = {row["name"]: row["bytes"] for row in ptk.FbxMedia.embedded(fbx)}
    # The materials record names the material's map too, so the dependency
    # pass embeds it as it does the other records' files (pythontk, 2026-10-31).
    recorded = sorted([*sources, os.path.basename(base_color)])
    check(
        "the records name the lightmap, the silhouette, the horizon map and the material's map",
        sorted(named) == recorded,
        f"named={named}",
    )
    for name, path in sources.items():
        check(
            f"{name} is inside the FBX, whole",
            embedded.get(name) == os.path.getsize(path),
            f"embedded={embedded.get(name)} disk={os.path.getsize(path)}",
        )
    check(
        "the material's texture is inside the FBX, whole",
        embedded.get("Crate_Base_color.png") == os.path.getsize(base_color),
        f"embedded={sorted(embedded)}",
    )
    manifests = [
        json.loads(bytes(value).decode("utf-8"))
        for value in ptk.FbxFile.load(fbx, raw_payloads=False).user_properties(
            ptk.SceneRecords.AUDIO.key
        )
    ]
    check(
        "the FBX's audio record names the strip's sound by its delivered file",
        [c.get("sound") for m in manifests for c in m.get("cues", [])]
        == [f"{track}.wav"],
        f"{manifests}",
    )
    check(
        "the strip's file is inside the FBX, whole, under its sound's name",
        embedded.get(f"{track}.wav") == os.path.getsize(wav),
        f"embedded={sorted(embedded)}",
    )
    # The record's digest of the track is the SHA-256 of the bytes inside --
    # what Unity hashes an extracted or library clip to before it stands in
    # for the track (ptk.AudioUtils.media_digests; mayatk's too). Added:
    # 2026-10-31
    import hashlib

    inside = os.path.join(root, "inside")
    os.makedirs(inside, exist_ok=True)
    written = ptk.FbxMedia.extract(fbx, inside, names=[f"{track}.wav"])
    with open(written.get(f"{track}.wav", wav), "rb") as fh:
        inside_digest = hashlib.sha256(fh.read()).hexdigest()
    check(
        "the audio record's digest of the strip is the SHA-256 of the bytes inside",
        f"{track}.wav" in written
        and [m.get("media") for m in manifests] == [{f"{track}.wav": inside_digest}],
        f"{[m.get('media') for m in manifests]} vs {inside_digest}",
    )
    beside = sorted(os.listdir(out))
    check("nothing ships beside the FBX", beside == ["embedded_deps.fbx"], f"{beside}")
    # Bug: the FBX carried the producer's user name and folders -- the source
    # .blend, each map's path in the staging folder under the user's temp
    # folder -- to whoever opened it; the deliverable names none (pythontk
    # ``FbxMedia.strip_machine_paths``, mirror of mayatk's). Fixed: 2026-11-08
    with open(fbx, "rb") as fh:
        data = fh.read().lower()
    leaked = [
        spelling
        for folder in (os.path.expanduser("~"), root, images)
        for spelling in (folder.replace("\\", "/"), folder.replace("/", "\\"))
        if spelling.lower().encode("utf-8") in data
    ] + ([b"appdata"] if b"appdata" in data else [])
    check("the FBX names no folder of this machine", not leaked, f"{leaked}")
    from blendertk.env_utils.hierarchy_sync.scene_data_sidecar import (
        SceneDataSidecar,
    )

    check(
        "the exporter's record of the export is in the per-user store",
        os.path.isfile(SceneDataSidecar.manifest_path_for(fbx)),
        SceneDataSidecar.manifest_path_for(fbx),
    )

    # A preset owns content choices, never whether the deliverable carries its
    # media: the bundled preset and one that turns Embed Media off are both
    # pinned back to it, their content choices standing.
    preset_out = os.path.join(root, "with_preset")
    os.makedirs(preset_out, exist_ok=True)
    SceneExporter().perform_export(
        export_dir=preset_out,
        objects=[cube],
        output_name="with_preset",
        preset_file="default",
        tasks={"export_data_node": False},
    )
    preset_fbx = os.path.join(preset_out, "with_preset.fbx")
    inside = {row["name"] for row in ptk.FbxMedia.embedded(preset_fbx)}
    check(
        "the bundled preset's export carries the material's texture",
        "Crate_Base_color.png" in inside,
        f"embedded={sorted(inside)}",
    )
    exporter = SceneExporter()
    exporter._fbx_preset_options = {
        "embed_textures": False,
        "path_mode": "AUTO",
        "use_tspace": True,
    }
    resolved = exporter._resolved_fbx_options()
    check(
        "a preset cannot leave an FBX's media outside",
        resolved.get("embed_textures") is True
        and resolved.get("path_mode") == "COPY"
        and resolved.get("use_tspace") is True,
        f"{ {k: resolved.get(k) for k in ('embed_textures', 'path_mode', 'use_tspace')} }",
    )

    # Processed maps stage in scratch for an FBX (it embeds them); only a USD
    # layer, which references its maps, stages them beside itself.
    tm = SceneExporter().task_manager
    tm.begin_run(ptk.ExportRun(export_path=fbx, output_format="fbx"))
    staging, temp = tm._texture_staging_dir("convert")
    outside = not os.path.normcase(os.path.abspath(staging)).startswith(
        os.path.normcase(os.path.abspath(out))
    )
    check("an FBX run stages its processed maps in scratch", temp and outside, staging)
    usd = os.path.join(out, "staged.usd")
    tm.begin_run(ptk.ExportRun(export_path=usd, output_format="usd"))
    staging, temp = tm._texture_staging_dir("convert")
    check(
        "a USD layer still stages its maps beside itself",
        not temp and os.path.dirname(staging) == out,
        staging,
    )

    # Exporting again embeds nothing twice.
    again = ptk.FbxMedia.embed_dependencies(fbx, search_dirs=[images])
    check(
        "a second pass finds every file already inside",
        again["embedded"] == [] and sorted(again["present"]) == recorded,
        f"{again}",
    )

    # The embedded map still resolves with nothing beside the file: imported
    # from a copy elsewhere, its source gone, the material's image has pixels.
    elsewhere = os.path.join(root, "client")
    os.makedirs(elsewhere, exist_ok=True)
    copy = os.path.join(elsewhere, "embedded_deps.fbx")
    with open(fbx, "rb") as src, open(copy, "wb") as dst:
        dst.write(src.read())
    os.remove(base_color)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.fbx(filepath=copy)
    read = [
        node.image
        for mat in bpy.data.materials
        if mat.node_tree
        for node in mat.node_tree.nodes
        if node.type == "TEX_IMAGE" and node.image
    ]
    check(
        "the re-imported material's map resolves from inside the FBX",
        any(
            ptk.FileDependencies.file_name(image.filepath) == "Crate_Base_color.png"
            and image.has_data
            and image.size[0] == 4
            for image in read
        ),
        f"{[(i.name, i.filepath, i.has_data, tuple(i.size)) for i in read]}",
    )

    bpy.ops.wm.read_factory_settings(use_empty=True)

except Exception as e:
    lines.append(f"FAIL setup: {e!r}")
    lines.append(traceback.format_exc())
finally:
    store.cleanup()

ok = all(line.startswith("OK") for line in lines)
for line in lines:
    print(line)
print(f"===RESULT: {'PASS' if ok else 'FAIL'}===")
