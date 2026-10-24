"""Texture Combine panel's Transfer mode headless test (``_transfer_mode``).
Run: blender --background --factory-startup --python blendertk/test/test_texture_combine_transfer_mode.py

Mirror of mayatk's ``test_texture_combine_transfer_mode`` -- the selection gates, Auto's two
picks, Include Lightmaps end to end, the Material row, Source: All But Active / Auto on real
geometry, and the stored sources -- plus the checks tentacle's ``uv_slot_check`` held for
Blender's former b000 option box (a deselected active object is no side of a transfer). The
run reads the panel's widgets, so the doubles below are the panel's rows by their
``texture_combine.ui`` names; the shared rows' readers (affix, shader) are the panel's own
methods. No Qt: the slots class is built without its UI-loading ``__init__``.
"""

import os
import sys
import traceback
from types import SimpleNamespace as NS

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


class _Progress:
    def __init__(self, log, text):
        self._log, self._text = log, text

    def __enter__(self):
        self._log.append(self._text)
        return self._tick

    def _tick(self, value=None, text=None):
        if text:
            self._log.append(text)
        return True

    def __exit__(self, *exc):
        return False


class _Sb:
    def __init__(self):
        self.messages = []
        self.progress_texts = []

    def message_box(self, text, *args, **kwargs):
        self.messages.append(text)

    def progress(self, ui=None, total=None, text=""):
        return _Progress(self.progress_texts, text)


class _Value:
    """Enough of a combo / field / check / spin for the rows the run reads."""

    def __init__(self, data=None, text="", checked=False, value=-1):
        self._data, self._text, self._checked, self._value = data, text, checked, value
        self.index = None

    def currentData(self):
        return self._data

    def text(self):
        return self._text

    def isChecked(self):
        return self._checked

    def value(self):
        return self._value

    def findData(self, data):
        return 3 if data == "stored" else -1

    def setCurrentIndex(self, index):
        self.index = index


class _Affix:
    """The Material Affix row: ``resolve_affix`` over the same
    ``ptk.StrUtils.split_affix`` the real ``AffixOption`` uses."""

    def __init__(self, ptk, text="", mode="auto"):
        self._text = text
        self.option_box = NS(
            resolve_affix=lambda text=None, default="prefix": ptk.StrUtils.split_affix(
                self._text if text is None else text, mode=mode, default=default
            )
        )

    def text(self):
        return self._text


try:
    import bpy
    import numpy as np
    import pythontk as ptk
    import blendertk as btk

    btk.CoreUtils.ensure_image_deps()
    from blendertk.light_utils.lightmap_baker.lightmap_records import LightmapRecords
    from blendertk.uv_utils._uv_utils import UvUtils
    from blendertk.uv_utils.texture_combine import _transfer_mode
    from blendertk.uv_utils.texture_combine.texture_combine_slots import (
        TextureCombineSlots,
    )

    artifacts = ptk.TempArtifacts("tm_btk_test", policy="scoped")
    tmp = artifacts.dir_path()

    def panel(
        mode="first",
        scope="order",
        transfer="uvs",
        output_name="transfer_result",
        lightmaps=False,
        output_dir="",
        affix="",
        affix_mode="auto",
        shader="source",
    ):
        slots = TextureCombineSlots.__new__(TextureCombineSlots)
        slots.sb = _Sb()
        slots._name_recent = None
        # The panel in Transfer mode: its mode-scoped readers (overwrites_target).
        slots._fields = NS(mode=TextureCombineSlots.MODE_TRANSFER)
        slots.status = lambda text: slots.sb.messages.append(text)
        slots.ui = NS(
            cmb_tt_source=_Value(mode),
            cmb_tt_targets=_Value(scope),
            cmb_tt_transfer=_Value(transfer),
            t_name=_Value(text=output_name),
            t_tt_src_uvset=_Value(text=""),
            t_tt_dst_uvset=_Value(text=""),
            t_output=_Value(text=output_dir),
            cmb_tt_resolution=_Value(0),
            cmb_quality=_Value(2),
            s_padding=_Value(value=-1),
            chk_assign=_Value(checked=True),
            chk_tt_lightmaps=_Value(checked=lightmaps),
            t_affix=_Affix(ptk, affix, affix_mode),
            cmb_shader=_Value(shader),
        )
        slots.transfer = _transfer_mode._TransferMode(slots)
        slots.transfer.sync = lambda: None
        return slots

    uv_calls = []
    space = {"result": None}

    def fake_transfer_uvs(frm, to, **kwargs):
        uv_calls.append(([o.name for o in frm], [o.name for o in to], kwargs))
        return space["result"]

    captured = []
    real_tt = _transfer_mode.TextureTransfer

    class Capturing(real_tt):
        result = {}

        def transfer(self, targets, source=None, **kwargs):
            captured.append(
                (
                    [t.name for t in targets],
                    None if source is None else [s.name for s in source],
                    kwargs,
                )
            )
            return dict(Capturing.result)

    real_uvs = UvUtils.__dict__["transfer_uvs"]  # the classmethod itself
    UvUtils.transfer_uvs = staticmethod(fake_transfer_uvs)

    def capture_textures(result=None):
        Capturing.result = result or {}
        _transfer_mode.TextureTransfer = Capturing

    def release_textures():
        _transfer_mode.TextureTransfer = real_tt

    def reset():
        release_textures()
        del uv_calls[:], captured[:]
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)
        for coll in (bpy.data.meshes, bpy.data.materials, bpy.data.images):
            for item in list(coll):
                coll.remove(item)
        LightmapRecords.revert()

    def select(objs, active=None):
        with btk.CoreUtils.window_context_override():
            for o in list(bpy.context.view_layer.objects):
                o.select_set(o in objs)
            bpy.context.view_layer.objects.active = active

    def cube(name, x=0.0):
        bpy.ops.mesh.primitive_cube_add(location=(x, 0, 0))
        o = bpy.context.active_object
        o.name = name
        return o

    def run(**kwargs):
        slots = panel(**kwargs)
        slots.transfer.run()
        return slots

    try:
        # ---- the UV pass: gates, pairing, footer -----------------------------
        reset()
        select([])
        slots = run()
        check("no selection warns and skips", not uv_calls and slots.sb.messages)

        reset()
        one = cube("tm_one")
        select([one], one)
        slots = run()
        check(
            "one object warns and skips",
            not uv_calls and "Make the source active" in slots.sb.messages[-1],
            slots.sb.messages[-1:],
        )

        reset()
        a, b = cube("tm_a"), cube("tm_b", 3)
        select([a, b], a)
        run()
        check(
            "two objects transfer as one pair, never re-vetted",
            len(uv_calls) == 1
            and uv_calls[0][:2] == (["tm_a"], ["tm_b"])
            and uv_calls[0][2].get("match_by_similarity") is False,
            uv_calls,
        )
        del uv_calls[:]
        check(
            "the footer names the UV pass",
            any("Transfer UV Map" in t for t in run().sb.progress_texts),
        )
        capture_textures()
        check(
            "the footer names the texture pass",
            "Working: Transfer Textures" in run(transfer="textures").sb.progress_texts,
        )

        reset()
        a, b, c = cube("tm_a3"), cube("tm_b3", 3), cube("tm_c3", 6)
        select([a, b, c], a)
        run()
        check(
            "the source pairs against every target",
            len(uv_calls) == 1
            and set(uv_calls[0][0]) == {"tm_a3"}
            and set(uv_calls[0][1]) == {"tm_b3", "tm_c3"},
            uv_calls,
        )

        # The REAL UV pass (the check tentacle's deepened_slots_check held for
        # Blender's former b000): the active mesh's layout lands on the other.
        reset()
        a, b = cube("tm_uv_src"), cube("tm_uv_tgt", 3)
        UvUtils.move_uvs(a, du=2.0)
        select([a, b], a)
        fake = UvUtils.__dict__["transfer_uvs"]
        UvUtils.transfer_uvs = real_uvs
        try:
            run()
        finally:
            UvUtils.transfer_uvs = fake
        from blendertk.uv_utils.texture_transfer import TextureTransfer

        tgt_u = TextureTransfer._uv_layer_vectors(b.data, "UVMap")[:, 0].min()
        check(
            "the UV pass copies the active mesh's layout onto the target",
            tgt_u >= 2.0 - 1e-4,
            f"target min u={tgt_u:.2f}",
        )

        # ---- Auto: the source's materials pick the pass ----------------------
        reset()
        src, tgt = cube("tm_auto_src"), cube("tm_auto_tgt", 3)
        mat = bpy.data.materials.new("tm_auto_mat")
        btk.MatUtils.get_node_tree(mat, create=True)
        bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
        tex = mat.node_tree.nodes.new("ShaderNodeTexImage")
        tex.image = bpy.data.images.new("tm_auto.png", 4, 4)
        tex.image.filepath = os.path.join(tmp, "tm_auto.png")
        mat.node_tree.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
        src.data.materials.append(mat)
        select([src, tgt], src)
        capture_textures()
        slots = run(transfer="auto", output_name="")
        check(
            "a mapped source resolves Auto to the texture pass, named after it",
            not uv_calls
            and len(captured) == 1
            and captured[0][2]["output_name"] == "tm_auto_src",
            f"{captured} {slots.sb.messages[-1:]}",
        )

        reset()
        a, b = cube("tm_plain_a"), cube("tm_plain_b", 3)
        select([a, b], a)
        slots = run(transfer="auto", output_name="")
        check(
            "an unmapped source resolves Auto to the UV pass",
            len(uv_calls) == 1
            and "Working: Transfer UV Map" in slots.sb.progress_texts,
            slots.sb.progress_texts,
        )

        # ---- Include Lightmaps, through the REAL engine ------------------------
        def lit_source_and_target():
            bpy.ops.mesh.primitive_grid_add(x_subdivisions=2, y_subdivisions=2)
            s = bpy.context.active_object
            s.name = "tm_lm_src"
            layer = s.data.uv_layers.new(name="Lightmap", do_init=True)
            s.data.uv_layers.active = s.data.uv_layers[0]
            path = os.path.join(tmp, "tm_lm_src_Lightmap.exr").replace("\\", "/")
            LightmapRecords._write_lightmap(path, np.full((16, 16, 3), 3.0, np.float32))
            LightmapRecords.commit({s.name: path})
            t = s.copy()
            t.data = s.data.copy()
            bpy.context.collection.objects.link(t)
            t.name = "tm_lm_tgt"
            del t[LightmapRecords.LIGHTMAP_INFO_PROP]
            layer = t.data.uv_layers["Lightmap"]
            uv = np.empty(len(t.data.loops) * 2, np.float32)
            layer.uv.foreach_get("vector", uv)
            uv = uv.reshape(-1, 2) - 0.5
            uv = np.stack([-uv[:, 1], uv[:, 0]], 1) + 0.5  # 90 degrees
            layer.uv.foreach_set("vector", uv.astype(np.float32).ravel())
            return s, t

        def _read_now(name):
            """The file *name*'s marker reads now -- the session's until saved."""
            return next(
                (p for n, _m, p in LightmapRecords._reads() if n == name and p), ""
            )

        reset()
        s, t = lit_source_and_target()
        select([s, t], s)
        out_dir = os.path.join(tmp, "out").replace("\\", "/")
        slots = run(
            transfer="auto", output_name="hero", lightmaps=True, output_dir=out_dir
        )
        message = "".join(slots.sb.messages)
        check(
            "Auto carries a lit, untextured source's lightmap (resampled)",
            "resampled into the target's layout" in message
            and LightmapRecords.lightmap_info(t).get("map") == "hero_Lightmap.exr"
            and os.path.isfile(_read_now(t.name))
            # Home in the output folder once the file is saved (_stage).
            and os.path.normcase(LightmapRecords._home(_read_now(t.name)))
            == os.path.normcase(os.path.join(out_dir, "hero_Lightmap.exr"))
            and out_dir in message,
            message[-300:],
        )

        reset()
        s, t = lit_source_and_target()
        select([s, t], s)
        slots = run(transfer="auto", output_name="", lightmaps=False)
        check(
            "unchecked, Auto moves the layout and no lightmap",
            "Working: Transfer UV Map" in slots.sb.progress_texts
            and LightmapRecords.lightmap_info(t) == {},
            slots.sb.progress_texts,
        )

        # ---- the Material row reaches the engine as plain keywords ------------
        def kwargs_of(**kw):
            reset()
            a, b = cube("tm_affix_a"), cube("tm_affix_b", 3)
            select([a, b], a)
            capture_textures()
            slots = run(transfer="textures", **kw)
            return captured[0][2] if len(captured) == 1 else {"_": slots.sb.messages}

        kw = kwargs_of()
        check(
            "a blank affix leaves the naming to the engine",
            (kw.get("assign_prefix"), kw.get("assign_suffix")) == ("", None),
            kw,
        )
        check(
            "Auto reads the underscore",
            kwargs_of(affix="_MAT").get("assign_suffix") == "_MAT"
            and kwargs_of(affix="MAT_").get("assign_prefix") == "MAT_",
        )
        kp = kwargs_of(affix="MAT", affix_mode="prefix")
        ks = kwargs_of(affix="MAT", affix_mode="suffix")
        check(
            "the picker pins an undeclared side",
            (kp.get("assign_prefix"), kp.get("assign_suffix")) == ("MAT", "")
            and (ks.get("assign_prefix"), ks.get("assign_suffix")) == ("", "MAT"),
            f"{kp} {ks}",
        )
        ka = kwargs_of(affix="MAT")
        check(
            "Auto falls back to the suffix",
            (ka.get("assign_prefix"), ka.get("assign_suffix")) == ("", "MAT"),
            ka,
        )
        check(
            "the Shader row: source by default, target on request, no shader type",
            kwargs_of().get("assign_from") == "source"
            and kwargs_of(shader="target").get("assign_from") == "target"
            and "assign_shader_type" not in kwargs_of(shader="target"),
        )
        ko = kwargs_of(shader="in_place", affix="_MAT")
        check(
            "Overwrite target re-uses the material and drops the affix",
            ko.get("assign_from") == "in_place"
            and (ko.get("assign_prefix"), ko.get("assign_suffix")) == ("", None),
            ko,
        )

        # ---- Overwrite target, end to end through the REAL engine -------------
        reset()
        from PIL import Image

        def textured(name, rgb):
            path = os.path.join(tmp, f"{name}.png").replace("\\", "/")
            Image.fromarray(np.full((16, 16, 3), rgb, np.uint8)).save(path)
            mat = bpy.data.materials.new(name)
            btk.MatUtils.get_node_tree(mat, create=True)
            bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
            tex = mat.node_tree.nodes.new("ShaderNodeTexImage")
            tex.image = bpy.data.images.load(path)
            mat.node_tree.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
            return mat, path

        bpy.ops.mesh.primitive_plane_add()
        src = bpy.context.active_object
        src.name = "owSrc"
        src.data.materials.append(textured("owSrcMat", (220, 30, 30))[0])
        tgt = src.copy()
        tgt.data = src.data.copy()
        bpy.context.collection.objects.link(tgt)
        tgt.name = "owTgt"
        tgt.data.materials.clear()
        own_mat, own = textured("owTgtMat", (90, 90, 90))
        tgt.data.materials.append(own_mat)
        select([src, tgt], src)
        out_dir = os.path.join(tmp, "out_in_place").replace("\\", "/")
        slots = run(
            transfer="textures", output_name="", output_dir=out_dir, shader="in_place"
        )
        message = slots.sb.messages[-1] if slots.sb.messages else ""
        with Image.open(own) as img:
            red = np.asarray(img.convert("RGB"), np.float32).mean(axis=(0, 1))
        check(
            "Overwrite target rewrites the target's own map, the report names it",
            "Overwrote <b>1</b> file(s) in place: owTgtMat.png" in message
            and [sl.material.name for sl in tgt.material_slots] == ["owTgtMat"]
            and float(np.abs(red - (220, 30, 30)).max()) < 2.0,
            f"{red.tolist()} {message[-300:]}",
        )
        check(
            "Overwrite target names nothing after the source",
            "Named <b>" not in message,
            message[-300:],
        )

        reset()
        grp = bpy.data.objects.new("hero_GRP", None)
        bpy.context.collection.objects.link(grp)
        body = cube("hero_body_GEO")
        body.parent = grp
        tgt = cube("tm_named_tgt", 3)
        select([body, tgt], body)
        capture_textures()
        run(transfer="textures", output_name="")
        check(
            "a blank Output Name is named after the source",
            len(captured) == 1 and captured[0][2]["output_name"] == "hero_body",
            captured,
        )

        # ---- Source: All But Active / Auto on a JOINED target ------------------
        def joined_scene():
            """srcA (cube), srcB (sphere) and 'comb' = B joined with A (B active)."""
            reset()
            a = cube("srcA")
            bpy.ops.mesh.primitive_uv_sphere_add(
                segments=6, ring_count=4, location=(3, 0, 0)
            )
            b = bpy.context.active_object
            b.name = "srcB"
            copies = []
            for o in (b, a):
                c = o.copy()
                c.data = o.data.copy()
                bpy.context.collection.objects.link(c)
                copies.append(c)
            with bpy.context.temp_override(
                active_object=copies[0], selected_editable_objects=copies
            ):
                bpy.ops.object.join()
            copies[0].name = "comb"
            capture_textures({"m": {"baseColor": "/out/hero_BaseColor.png"}})
            return a, b, copies[0]

        def pairs():
            return [(t, s) for t, s, _ in captured]

        a, b, c = joined_scene()
        select([a, b, c], c)
        run(mode="last", transfer="textures", output_name="hero")
        check(
            "All But Active: the active mesh takes every other mesh's maps",
            pairs() == [(["comb"], ["srcA", "srcB"])],
            pairs(),
        )

        a, b, c = joined_scene()
        select([a, b, c], c)
        slots = run(mode="last", transfer="uvs", output_name="hero")
        check(
            "All But Active refuses the UV pass onto a joined target",
            not uv_calls and "one source per target" in slots.sb.messages[-1],
            slots.sb.messages[-1:],
        )

        a, b, c = joined_scene()
        select([a, b, c], a)  # the joined mesh is NOT the active one
        slots = run(mode="auto", transfer="textures", output_name="hero")
        check(
            "Auto finds the joined target whatever is active",
            pairs() == [(["comb"], ["srcA", "srcB"])]
            and "Source: Auto" in slots.sb.messages[-1],
            f"{pairs()} {slots.sb.messages[-1:]}",
        )

        a, b, c = joined_scene()
        select([a], a)
        run(mode="auto", transfer="textures", output_name="hero")
        check(
            "Auto reads one mesh as its own UV maps",
            pairs() == [(["srcA"], None)],
            pairs(),
        )

        a, b, c = joined_scene()
        select([b, a], b)
        run(mode="auto", transfer="textures", output_name="hero")
        check(
            "Auto falls back to the active mesh as the source",
            pairs() == [(["srcA"], ["srcB"])],
            pairs(),
        )

        a, b, c = joined_scene()
        slots = panel(mode="auto", transfer="textures", output_name="hero")
        slots.transfer.sources = ["srcA", "srcB"]
        select([c], c)
        slots.transfer.run()
        check(
            "Auto reads stored sources that match the selection",
            pairs() == [(["comb"], ["srcA", "srcB"])]
            and "stored source meshes" in slots.sb.messages[-1],
            f"{pairs()} {slots.sb.messages[-1:]}",
        )

        a, b, c = joined_scene()
        bpy.ops.mesh.primitive_torus_add()
        bpy.context.active_object.name = "tm_torus"
        slots = panel(mode="auto", transfer="textures", output_name="hero")
        slots.transfer.sources = ["tm_torus"]
        select([a], a)
        slots.transfer.run()
        check("Auto ignores a stale capture", pairs() == [(["srcA"], None)], pairs())

        reset()
        groups = {}
        for gname in ("kit_GRP", "out_GRP"):
            root = bpy.data.objects.new(gname, None)
            bpy.context.collection.objects.link(root)
            groups[gname] = root
            for i, part in enumerate(("partA", "partB")):
                o = cube(part, i * 3)
                o.parent = root
        capture_textures()
        select(list(groups.values()), groups["kit_GRP"])
        run(mode="first", transfer="textures", output_name="")
        src_names = sorted(o.name for o in groups["kit_GRP"].children)
        tgt_names = sorted(o.name for o in groups["out_GRP"].children)
        check(
            "the active group is the source, paired with the target group",
            len(captured) == 1
            and sorted(captured[0][1]) == src_names
            and sorted(captured[0][0]) == tgt_names,
            captured,
        )
        check(
            "a blank Output Name is named after the source group",
            len(captured) == 1 and captured[0][2]["output_name"] == "kit",
            captured,
        )

        reset()
        feed = cube("feedSrc")
        t1, t2 = cube("feedT1", 3), cube("feedT2", 6)
        capture_textures()
        select([feed, t1, t2], feed)
        run(mode="first", transfer="textures", output_name="hero")
        check(
            "the active mesh feeds every target",
            len(captured) == 1
            and sorted(captured[0][0]) == ["feedT1", "feedT2"]
            and captured[0][1] == ["feedSrc"],
            captured,
        )

        reset()
        meshes = [cube(n, i * 3) for i, n in enumerate(("selA", "selB", "deselC"))]
        capture_textures()
        for mode, hint in (
            ("first", "Make the source active"),
            ("auto", "Make the source active"),
            ("last", "Make the target mesh active"),
        ):
            del captured[:], uv_calls[:]
            select(meshes[:2], meshes[2])  # deselC active, not selected
            slots = run(mode=mode, transfer="textures")
            check(
                f"Source {mode}: a deselected active object is no side of it",
                not captured and not uv_calls and hint in slots.sb.messages[-1],
                f"{captured} {slots.sb.messages[-1:]}",
            )

        # ---- the stored sources ------------------------------------------------
        def stored_panel():
            slots = panel(mode="stored")
            slots.sb.tooltip = ptk.TooltipFormat
            slots.transfer.select_action = NS(widget=NS(setEnabled=lambda *_: None))
            slots.transfer.clear_action = NS(widget=NS(setEnabled=lambda *_: None))
            return slots

        reset()
        slots = stored_panel()
        cap = cube("capCube")
        select([cap], cap)
        slots.transfer.capture_source()
        check(
            "capture stores the selection and switches to Stored Meshes",
            slots.transfer.sources == ["capCube"] and slots.ui.cmb_tt_source.index == 3,
            f"{slots.transfer.sources} {slots.ui.cmb_tt_source.index}",
        )

        reset()
        slots = stored_panel()
        a, b = cube("stA"), cube("stB", 3)
        slots.transfer.sources = ["stA", "stB"]
        select([])
        slots.transfer.select_source()
        check(
            "Select selects the stored meshes",
            sorted(o.name for o in btk.CoreUtils.selected_objects()) == ["stA", "stB"],
        )

        slots.transfer.sources = ["stA", "goneForever"]
        select([])
        slots.transfer.select_source()
        check(
            "Select skips objects deleted since the capture",
            [o.name for o in btk.CoreUtils.selected_objects()] == ["stA"]
            and "1 no longer in the file" in slots.sb.messages[-1],
            slots.sb.messages[-1:],
        )

        slots.transfer.sources = []
        select([b], b)
        slots.transfer.select_source()
        check(
            "Select with nothing stored leaves the selection alone",
            [o.name for o in btk.CoreUtils.selected_objects()] == ["stB"]
            and "Nothing stored" in slots.sb.messages[-1],
            slots.sb.messages[-1:],
        )

        slots.transfer.sources = ["stA", "stB"]
        html = slots.transfer.source_tooltip()
        check(
            "the tooltip lists the stored names",
            "<li>stA</li>" in html and "<li>stB</li>" in html,
        )
        n = ptk.TooltipFormat.STORED_ITEMS_MAX
        slots.transfer.sources = [f"cube{i}" for i in range(n + 4)]
        html = slots.transfer.source_tooltip()
        check(
            "the tooltip truncates a long capture",
            html.count("<li>") == n and "4 more" in html,
        )
        slots.transfer.sources = []
        html = slots.transfer.source_tooltip()
        check(
            "the empty tooltip still explains the button",
            "<li>" not in html and "Store Source Meshes" in html,
        )
        slots.transfer.sources = ["stA", "goneForever"]
        check(
            "the tooltip calls out deleted objects",
            "1 no longer in the file" in slots.transfer.source_tooltip(),
        )
    finally:
        UvUtils.transfer_uvs = real_uvs
        release_textures()
        reset()
        artifacts.cleanup()

except Exception as e:
    lines.append(f"FAIL setup: {e!r}")
    lines.append(traceback.format_exc())

ok = all(line.startswith("OK") for line in lines)
print("\n===TEXTURE-COMBINE-TRANSFER-MODE===")
print("\n".join(lines))
print(
    f"===RESULT: {'PASS' if ok else 'FAIL'}=== "
    f"({sum(1 for line in lines if line.startswith('OK'))}/{len(lines)})"
)
