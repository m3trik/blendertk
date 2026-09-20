# !/usr/bin/python
# coding=utf-8
"""The Scene Exporter panel's task and check rows -- mirror of mayatk's.

The declarative definitions -- ``{name: {"widget_type", "object_name",
"setChecked", ...}}`` -- from which the panel builds its widgets and the
export button reads a run back (``ptk.ExportProfile``). Presentation only:
the tooltips describe what each task and check does, and the combo tables
are the shared ``ptk.ExportProfile`` ones (labels persist by index, so a
choice is never inserted above a sentinel). The engine mixins never read
this module. Tooltips are built with uitk's rich-text DSL, imported lazily
so the module still imports Qt-free under ``--background``.
"""

from typing import Dict, Any

import pythontk as ptk

# From this package:
from blendertk.env_utils.scene_exporter._task_data import (
    _LINEAR_UNIT_VALUES,
    _NEEDS_HIERARCHY_MANAGER,
)


class _TaskDefinitionsMixin:
    """The panel's rows: ``task_definitions`` / ``check_definitions``."""

    # The combo tables, shared with mayatk's panel through ExportProfile (one
    # label edit lands in both). Every combo persists by INDEX, so a sentinel
    # keeps its slot and a new choice APPENDS.
    _frame_rate_options: Dict[str, Any] = ptk.ExportProfile.frame_rate_options()
    _scene_unit_options: Dict[str, Any] = {
        k: v for k, v in ptk.insert_into_dict(_LINEAR_UNIT_VALUES, "OFF", None).items()
    }
    _texture_output_options: Dict[str, Any] = ptk.ExportProfile.TEXTURE_OUTPUT_OPTIONS
    _optimize_textures_options: Dict[str, Any] = (
        ptk.ExportProfile.optimize_textures_options()
    )
    _texture_file_type_options: Dict[str, Any] = (
        ptk.ExportProfile.texture_file_type_options()
    )
    _export_mode_options: Dict[str, Any] = ptk.ExportProfile.EXPORT_MODE_OPTIONS
    _bake_range_options: Dict[str, Any] = ptk.ExportProfile.BAKE_RANGE_OPTIONS
    _animation_clips_options: Dict[str, Any] = ptk.ExportProfile.ANIMATION_CLIPS_OPTIONS
    _optimize_keys_options: Dict[str, Any] = ptk.ExportProfile.OPTIMIZE_KEYS_OPTIONS
    _secondary_max_size_options: Dict[str, Any] = (
        ptk.ExportProfile.SECONDARY_MAX_SIZE_OPTIONS
    )
    _uastc_rdo_options: Dict[str, Any] = ptk.ExportProfile.UASTC_RDO_OPTIONS
    _glb_key_reduction_options: Dict[str, Any] = (
        ptk.ExportProfile.GLB_KEY_REDUCTION_OPTIONS
    )

    @property
    def task_definitions(self) -> Dict[str, Dict[str, Any]]:
        """Return the task definitions for the UI.

        Tooltips are built with uitk's rich-text DSL (imported lazily so this
        engine module still imports Qt-free under ``--background``).  Keep the
        ``TooltipFormat.fmt`` call form and literal arguments — that is what
        ``m3trik/scripts/check_tooltips.py`` statically renders and validates.
        """
        from uitk.widgets.mixins.tooltip_mixin import TooltipFormat

        return {
            "export_visible_objects": {
                "widget_type": "ComboBox",
                "panel": "settings",
                "set_row_label": "Scope",
                "setToolTip": TooltipFormat.fmt(
                    title="Export Scope",
                    body="Which objects the export set is built from, resolved "
                    "fresh each time you export.",
                    bullets=[
                        "<b>All Scene Objects</b> — every mesh object in the "
                        "scene, visible or not.",
                        "<b>All Visible Objects</b> — visible geometry only.",
                        "<b>Selected Objects Only</b> — the current selection, "
                        "minus the data_internal Empty (a plain Select All would "
                        "otherwise sweep the bake-session manifest in).",
                    ],
                    notes=[
                        "The data_export metadata carrier is an Empty, not a mesh, "
                        "so <b>Export Scene Data Node</b> is what puts it in the "
                        "set."
                    ],
                ),
                "add": self._export_mode_options,
                "value_method": "currentData",
            },
            "set_linear_unit": {
                "widget_type": "ComboBox",
                "panel": "settings",
                "set_row_label": "Units",
                "setToolTip": TooltipFormat.fmt(
                    title="Linear Unit",
                    body="Unit system and scale length the scene is switched to "
                    "for the FBX write, then switched back.",
                    notes=[
                        "Blender has no named-unit enum, so each option sets the "
                        "(system, scale_length) pair that makes one scene unit "
                        "equal one of the named unit — the scale the receiving "
                        "engine reads.",
                        "<b>OFF</b> writes with the scene's current unit settings.",
                    ],
                ),
                "add": self._scene_unit_options,
            },
            "exclude_hdr": {
                "widget_type": "QCheckBox",
                "panel": "settings",
                "setText": "Exclude HDR Environment",
                "setToolTip": TooltipFormat.fmt(
                    title="Exclude HDR Environment",
                    body="Drop image-based environment lighting from the export "
                    "set: a light whose node tree samples an Environment "
                    "Texture, and a mesh dome whose materials are fed by one.",
                    notes=[
                        "The World's HDRI is never an object and never ships; "
                        "this covers the cases where an HDR rides on one (the "
                        "Blender analogue of Maya's aiSkyDomeLight).",
                        "Scene lighting is not deliverable geometry.",
                    ],
                ),
                "setChecked": True,
            },
            # A mode, not a task: ``ExportRun.from_tasks`` pops it into the flag
            # the FBX write reads. Blender's rig apparatus lives in the armature
            # -- control and mechanism BONES -- so the mirror of mayatk's census
            # is the exporter's own ``use_armature_deform_only``, which keeps a
            # non-deform bone only when deform bones hang under it.
            "drop_rig_apparatus": {
                "widget_type": "QCheckBox",
                "panel": "settings",
                "setText": "Exclude Rig Helpers",
                "setToolTip": TooltipFormat.fmt(
                    title="Exclude Rig Helpers",
                    body="Write only the armature bones that deform a mesh: the "
                    "control and mechanism bones of a rig stay out of the FBX, "
                    "and so out of the GLB built from it.",
                    notes=[
                        "The baked motion is already on the deform bones; the "
                        "rest draw nothing and deform nothing, yet each ships "
                        "animated and the GLB conversion bakes every one at "
                        "every frame.",
                        "A non-deform bone with deform bones under it is kept, "
                        "so the hierarchy that carries them survives.",
                        "Helper OBJECTS (empties or curves a rig uses as "
                        "targets) still ship; Maya's twin also names those.",
                        "The scene is not changed. No effect on a USD export.",
                    ],
                ),
                "setChecked": True,
            },
            "reassign_duplicate_materials": {
                "widget_type": "QCheckBox",
                "group": "Materials",
                "setText": "Reassign Duplicate Materials",
                "setToolTip": TooltipFormat.fmt(
                    title="Reassign Duplicate Materials",
                    body="Collapse materials that are genuinely identical onto a "
                    "single keeper and reassign every object using them.",
                    notes=[
                        "Reports the same materials as <b>Check For Duplicate "
                        "Materials</b>.",
                        "Permanent scene change — not reverted after export.",
                    ],
                ),
                "setChecked": True,
            },
            "convert_to_relative_paths": {
                "widget_type": "QCheckBox",
                "group": "Materials",
                "setText": "Convert To Relative Paths",
                "setToolTip": TooltipFormat.fmt(
                    title="Convert To Relative Paths",
                    body="Rewrite the export materials' texture paths in "
                    "Blender's //-relative project form.",
                    notes=[
                        "Scoped to textures already inside the project. A "
                        "texture stored anywhere else keeps its absolute path — "
                        "an external reference is usually deliberate, and this "
                        "task never relocates it. The log names any it left "
                        "alone.",
                        "Permanent scene change — not reverted after export.",
                    ],
                ),
                "setChecked": True,
            },
            "resolve_invalid_texture_paths": {
                "widget_type": "QCheckBox",
                "group": "Materials",
                "setText": "Resolve Invalid Texture Paths",
                "setToolTip": TooltipFormat.fmt(
                    title="Resolve Invalid Texture Paths",
                    body="Rebind broken texture paths by hunting for the missing "
                    "file under the .blend's own directory. Committed lightmaps "
                    "get the same hunt: a bake marker whose recorded folder no "
                    "longer holds its map is rewritten to where the map was "
                    "found, and the FBX manifest republished.",
                    notes=[
                        "Rebinding by name is a guess — the original file is gone, "
                        "so nothing can verify content.",
                        "Lightmap files are never moved — only the marker's "
                        "recorded folder changes. To gather them into the "
                        "project use Texture Path Editor ▸ Find &amp; Copy.",
                        "Permanent scene change — not reverted after export.",
                    ],
                ),
                "setChecked": True,
            },
            # -- Textures group: the Texture Output gate FIRST, then the three
            # dials it governs directly beneath it, so the gate and the gated
            # read as one block in the Tasks combo. Mirrors mayatk.
            "texture_write_back": {
                "widget_type": "ComboBox",
                "group": "Textures",
                "set_row_label": "Texture Output",
                "setToolTip": TooltipFormat.fmt(
                    title="Texture Output",
                    body="Whether the texture rows below — the <b>Textures</b> "
                    "template conversion and the <b>Optimize Textures</b> "
                    "pass (its size ceiling included) — modify the scene's "
                    "textures, or leave the scene as it was.",
                    bullets=[
                        "<b>Export Copies (Scene Untouched)</b> — "
                        "non-destructive: processed maps are staged for the "
                        "write (a temp folder when the deliverable embeds "
                        "its media, else <b>textures/</b> beside it), the "
                        "images read them for the export, and the scene's "
                        "paths are restored afterwards.",
                        "<b>Scene Files (In Place)</b> — permanent: the "
                        "conversion repaths the materials and the "
                        "optimization overwrites the scene's own texture "
                        "files (originals archived beside each texture in an "
                        "<b>original_textures</b> folder). Not reverted after "
                        "export.",
                    ],
                    notes=[
                        "Inert unless a template is selected or Optimize "
                        "Textures is on.",
                    ],
                ),
                "add": self._texture_output_options,
            },
            "convert_textures": {
                "widget_type": "ComboBox",
                "group": "Textures",
                # The widget keeps the objectName it had as a Settings row, so
                # every saved template key, ``cmb005_init`` and b000's reads
                # stay valid across the move into the Tasks combo.
                "object_name": "cmb005",
                "set_row_label": "Texture Template",
                "setToolTip": TooltipFormat.fmt(
                    title="Texture Template",
                    body="Convert the export's textures to a target texture "
                    "template (a pythontk map-registry workflow) before the "
                    "write — channel packing and shading model re-authored to "
                    "match what the destination engine expects.",
                    bullets=[
                        "<b>As Authored</b> (default) — send textures exactly "
                        "as the scene references them; converts nothing.",
                        "A template — materials are rebuilt through the Map "
                        "Updater, and a paired check fails the export if any "
                        "mask map still does not match.",
                    ],
                    notes=[
                        "Also drives <b>Optimize Textures</b>: the template's "
                        "per-map-type output spec supplies each map's bit "
                        "depth and container, and its size budget is what "
                        "that combo's Template Budget option enforces.",
                        "Where the rebuilt maps land — export copies or the "
                        "scene's own files — is <b>Texture Output</b>.",
                    ],
                ),
            },
            "optimize_textures": {
                "widget_type": "ComboBox",
                "group": "Textures",
                # NOT the old checkbox's objectName: a preset saved before the
                # merge carries optimize_textures (a bool) plus a separate
                # texture_max_size (an index), and letting the bool restore
                # onto this combo would keep the pass while silently dropping
                # the preset's size ceiling. A fresh name makes such a preset
                # trip the PresetManager's uncovered-keys warning instead, so
                # the user re-saves and the template is whole again. (The TASK
                # key stays optimize_textures — b000 decomposes this widget's
                # value back into the optimize_textures + texture_max_size
                # inputs the engine has always taken, so headless callers and
                # TASK_ORDER see no change.) Mirrors mayatk.
                "object_name": "texture_optimize",
                "set_row_label": "Optimize Textures",
                "setToolTip": TooltipFormat.fmt(
                    title="Optimize Textures",
                    body="Run the Map Converter's per-map-type optimization "
                    "pass on the textures shipping with this export — mode "
                    "and bit depth corrected per map type, the export reads "
                    "the optimized copies — with an optional longest-edge "
                    "ceiling: larger maps are downsampled, smaller ones "
                    "never grown.",
                    bullets=[
                        "<b>OFF</b> — ship every map as it is.",
                        "<b>Optimize</b> — the pass without resampling (a "
                        "template's size budget is only reported).",
                        "<b>Optimize + Max 512 … 8192</b> — the pass plus a "
                        "hard pixel ceiling, whatever the template says.",
                        "<b>Optimize + Template Budget</b> — the pass plus "
                        "the selected <b>Textures</b> template's own size "
                        "budget (e.g. glTF/URP 2048, HDRP/Unreal 4096; the "
                        "power-of-two rule is not applied). No resize with "
                        "Textures at <b>As Authored</b> or an unbudgeted "
                        "template.",
                    ],
                    notes=[
                        "With a <b>Textures</b> template selected, the "
                        "template's per-map-type output spec also drives each "
                        "map's container and bit depth (delivery containers "
                        "like KTX2 stay with the GLB half of <b>Texture File "
                        "Type</b>); at <b>As Authored</b> it is a generic "
                        "per-map-type pass and each map keeps its container.",
                        "The ceiling also caps a GLB deliverable's embedded "
                        "copies — one size policy for everything the export "
                        "ships.",
                        "Where the optimized maps go — export copies or the "
                        "scene's own files — is <b>Texture Output</b>.",
                        "Already-optimal maps are left untouched; the paired "
                        "check names anything the pass could not optimize.",
                    ],
                ),
                # Registry-derived: the item list comes from pythontk's
                # container/format registry, so inserting a format upstream
                # shifts every index after it and a template that stored
                # "JPG" would silently start selecting its neighbour. Persist
                # the VALUE (see StateManager.restore_by); indices already on
                # disk are migrated once by _legacy_combo_index. Mirrors mayatk.
                "restore_by": "text",
                "add": self._optimize_textures_options,
            },
            "texture_file_type": {
                "widget_type": "ComboBox",
                "group": "Textures",
                "set_row_label": "Texture File Type",
                "setToolTip": TooltipFormat.fmt(
                    title="Texture File Type",
                    body="Container every texture shipping with this export is "
                    "written in — the maps beside (or inside) the FBX and the "
                    "images embedded in a GLB alike.",
                    bullets=[
                        "<b>Original</b> — keep each source's container; with "
                        "a <b>Textures</b> template selected, the template's "
                        "per-map-type container decides.",
                        "<b>PNG … HDR</b> — write every map as that format.",
                        "<b>KTX2</b> — GPU-compressed Basis for web/XR "
                        "runtimes (UASTC for normals/data, ETC1S for color; "
                        "lightmaps stay lossless WebP). Ships only inside a "
                        "GLB, as KTX2 alone: the smallest deliverable, but it "
                        "needs a basisu-capable viewer (three.js KTX2Loader) "
                        "— Blender, Unreal or stock Unity cannot read its "
                        "textures.",
                        "<b>KTX2 + PNG/JPEG</b> — the same KTX2 set plus a "
                        "standard copy of every map as its KHR_texture_basisu "
                        "fallback (PNG for normals, ORM and alpha; JPEG for "
                        "opaque color; lightmaps stay PNG), so the GLB also "
                        "opens in Blender, Unreal or stock Unity. The copies "
                        "cost about as much again as the KTX2 (146 MB beside "
                        "123 MB on a 4K production assembly).",
                    ],
                    notes=[
                        "Both KTX2 entries need KTX-Software's <b>toktx</b>, "
                        "offered as a managed install when it is missing.",
                        "Naming a type outranks the template's per-map-type "
                        "container, which still supplies bit depth and budget.",
                        "Each destination clamps what it cannot carry: a "
                        "scene file node and an FBX cannot read KTX2, so the "
                        "scene keeps its own container there, and a GLB falls "
                        "back to PNG for anything glTF cannot embed "
                        "(PNG/JPEG/WebP/KTX2 are the ones it can).",
                        "Applied by <b>Optimize Textures</b> for scene maps; "
                        "a GLB deliverable is re-encoded whether or not that "
                        "pass runs.",
                    ],
                ),
                # Registry-derived: the item list comes from pythontk's
                # container/format registry, so inserting a format upstream
                # shifts every index after it and a template that stored
                # "JPG" would silently start selecting its neighbour. Persist
                # the VALUE (see StateManager.restore_by); indices already on
                # disk are migrated once by _legacy_combo_index. Mirrors mayatk.
                "restore_by": "text",
                "add": self._texture_file_type_options,
            },
            "secondary_max_size": {
                "widget_type": "ComboBox",
                "group": "Textures",
                "set_row_label": "Secondary Map Size",
                "setToolTip": TooltipFormat.fmt(
                    title="Secondary Map Size",
                    body="A lower size ceiling for the GLB deliverable's packed "
                    "data maps — metallic-roughness and occlusion — under the "
                    "ceiling Optimize Textures sets. Smooth masks read the same "
                    "at half the resolution; color keeps the primary ceiling "
                    "(the perceptual detail) and so do normal maps (the surface "
                    "detail a resample visibly softens).",
                    bullets=[
                        "<b>Same As Other Maps</b> — one ceiling for every map.",
                        "<b>Max 512 … 2048</b> — the data maps' own ceiling, "
                        "never above the primary.",
                    ],
                    notes=[
                        "GLB only: the FBX and the scene's own maps are untouched.",
                        "Measured on a 4K production assembly: the eight ORM "
                        "packs were 63 MB of a 155 MB GLB; at 2K they cost a "
                        "quarter of that.",
                    ],
                ),
                "restore_by": "text",
                "add": self._secondary_max_size_options,
            },
            "uastc_rdo": {
                "widget_type": "ComboBox",
                "group": "Textures",
                "set_row_label": "KTX2 RDO",
                "setToolTip": TooltipFormat.fmt(
                    title="KTX2 UASTC RDO",
                    body="Rate-distortion optimisation for the GLB's UASTC "
                    "encodes (normal and data maps): the blocks are steered "
                    "toward what the Zstandard stage compresses, at a "
                    "controlled quality cost.",
                    bullets=[
                        "<b>OFF</b> — plain UASTC, the largest encode.",
                        "<b>Light / Standard / Strong</b> — lambda 0.5 / 1 / 2: "
                        "more bytes saved, more quality spent. Normal maps are "
                        "capped at 0.75 whatever the dial says (toktx's own "
                        "guidance).",
                    ],
                    notes=[
                        "Measured on a 4K production set: ORM packs −30% at "
                        "lambda 1 (PSNR 50/44/48 dB); normal maps −1 to −14% at "
                        "their 0.75 cap, a noisy one −45% (PSNR 46–56 dB); the "
                        "encode runs 3–4× longer.",
                        "Only with <b>Texture File Type</b> at KTX2; ETC1S "
                        "(color) has no RDO stage.",
                    ],
                ),
                "restore_by": "text",
                "add": self._uastc_rdo_options,
            },
            "smart_bake": {
                "widget_type": "QCheckBox",
                "group": "Animation",
                "setText": "Smart Bake",
                "setToolTip": TooltipFormat.fmt(
                    title="Smart Bake",
                    body="Bake the rig's indirect animation — constraints "
                    "(including IK), drivers and expressions, driven blend-shape "
                    "weights — down to plain keyframes, which is all an FBX can "
                    "carry.",
                    notes=[
                        "The time range is detected from the driving animation itself.",
                        "Bakes into a fresh Action while muting the identified "
                        "sources; the pre-bake state is restorable afterward via "
                        "SmartBake.restore.",
                    ],
                ),
                "setChecked": True,
            },
            "optimize_keys": {
                "widget_type": "ComboBox",
                "group": "Animation",
                # NOT the old checkbox's objectName (mirror of mayatk): a
                # template saved before this merge carries optimize_keys as a
                # BOOL, and combos persist by index — restoring `true` would
                # silently select index 1 (Static Curves Only), a level nobody
                # chose. A fresh name trips the uncovered-keys warning instead.
                "object_name": "optimize_level",
                "set_row_label": "Optimize Keys",
                "setToolTip": TooltipFormat.fmt(
                    title="Optimize Keys",
                    body="Remove animation data the deliverable does not need — "
                    "including the curves Smart Bake just created — at the "
                    "chosen level.",
                    bullets=[
                        "<b>OFF</b> — ship every curve and key as authored.",
                        "<b>Static Curves Only</b> — delete curves whose value "
                        "never changes; every surviving curve keeps all of its "
                        "keys. The conservative rung.",
                        "<b>Static + Flat Keys</b> — also drop the redundant "
                        "interior keys of a flat run.",
                        "<b>+ Simplify (lossy)</b> — also drop keys that lie on "
                        "the line between their neighbours, within tolerance.",
                        "<b>Reduce To Extremes</b> — reduce smooth "
                        "curves to their extrema with handles refit to the "
                        "baked motion. The one to reach for after <b>Smart "
                        "Bake</b>: a per-frame bake has no redundant flat keys "
                        "for the other levels to find. It thins a bake, it does "
                        "not reverse one — that is the Smart Bake panel Unbake.",
                    ],
                    notes=[
                        "Boundary keys are always kept.",
                        "Permanent scene change — not reverted after export.",
                    ],
                ),
                "add": self._optimize_keys_options,
                # Applied after 'add' (which lands on index 0): index 2 is
                # Static + Flat Keys, exactly what the old checked box did.
                "setCurrentIndex": 2,
            },
            "glb_key_tolerance": {
                "widget_type": "ComboBox",
                "group": "Animation",
                "set_row_label": "GLB Key Tolerance",
                "setToolTip": TooltipFormat.fmt(
                    title="GLB Key Tolerance",
                    body="The GLB half of Optimize Keys. The converter bakes a "
                    "key on every frame of every channel, so the optimisation "
                    "above never reaches the GLB; its clips are reduced to this "
                    "bound instead. Each clip keeps the keys that reproduce "
                    "every original sample within the bound under the viewer's "
                    "own interpolation (slerp for rotations); the first and "
                    "last key always stay, so no clip changes length or origin.",
                    bullets=[
                        "<b>Keep Every Key</b> — the converter's per-frame keys.",
                        "<b>Within 1e-6 … 1e-3</b> — the largest deviation any "
                        "sample may show: scene units (meters) for translation "
                        "and scale, quaternion components for rotation; 1e-4 "
                        "is 0.1 mm / 0.006°.",
                    ],
                    notes=[
                        "Rides Optimize Keys: OFF there keeps every key here. "
                        "GLB only; the FBX keeps its optimised curves.",
                        "Measured on a 4K production assembly: 2.18 M keys, "
                        "6.8% kept within 1e-4 — 23.5 MB of animation to 2.4.",
                        "Stepped channels (visibility gates, fades) lose only "
                        "repeated values, which is lossless.",
                    ],
                ),
                "restore_by": "text",
                "add": self._glb_key_reduction_options,
                # Applied after 'add' (which lands on index 0): index 2 is
                # Within 1e-4, the bound the production measurements used.
                "setCurrentIndex": 2,
            },
            "tie_all_keyframes": {
                "widget_type": "QCheckBox",
                "group": "Animation",
                "setText": "Tie All Keyframes",
                "setToolTip": TooltipFormat.fmt(
                    title="Tie All Keyframes",
                    body="Insert bookend keys at the union keyed extent of the "
                    "whole export set, so every animated channel has a key at "
                    "both range boundaries.",
                    notes=[
                        "Fixes what <b>Check For Untied Keyframes</b> reports.",
                        "Permanent scene change — not reverted after export.",
                    ],
                ),
                "setChecked": True,
            },
            "snap_keys_to_frame": {
                "widget_type": "QCheckBox",
                "group": "Animation",
                "setText": "Snap Keys To Frame",
                "setToolTip": TooltipFormat.fmt(
                    title="Snap Keys To Frame",
                    body="Round every key on the exported objects to the nearest "
                    "whole frame.",
                    notes=[
                        "Fixes what <b>Check For Floating Point Keys</b> reports — "
                        "fractional key times left behind by retiming, scaling, or "
                        "an import at a different rate.",
                        "Permanent scene change — not reverted after export.",
                    ],
                ),
                "setChecked": False,
            },
            "set_bake_animation_range": {
                "widget_type": "ComboBox",
                "group": "Animation",
                # New objectName for the same reason as optimize_level above.
                "object_name": "bake_range",
                "set_row_label": "Bake Range",
                "setToolTip": TooltipFormat.fmt(
                    title="Bake Range",
                    body="Which frames the export bakes — Blender bakes over the "
                    "scene's frame range, so this sets that range for the "
                    "duration of the write.",
                    bullets=[
                        "<b>OFF</b> — export over the scene's range as it is.",
                        "<b>Auto (Shots → Keyframes)</b> — the span of the shots "
                        "declared in the <b>Shots</b> panel; a scene with no "
                        "shots falls back to the keyframe extent. With shots "
                        "authored, this is what keeps animation outside them "
                        "out of the deliverable.",
                        "<b>Keyframe Extent</b> — the full evaluated extent of "
                        "the exported objects: action fcurves, non-muted NLA "
                        "strips, and shape-key curves.",
                        "<b>Scene Animation Range</b> — the scene's own range, "
                        "written back unchanged (Blender bakes over it "
                        "already). Kept for parity with the Maya panel, where "
                        "the bake range is a separate setting.",
                    ],
                    notes=[
                        "Runs last, so it measures the final state of the "
                        "curves — and every mode is widened to cover the clips "
                        "<b>Animation Clips</b> declares, so no "
                        "choice here can ship metadata describing animation the "
                        "file does not contain.",
                        "The original frame range is restored after the write.",
                    ],
                ),
                "add": self._bake_range_options,
                # Applied after 'add' (which lands on index 0): index 1 is Auto.
                # With shots declared this reproduces what the old default pair
                # did (the split's widen won); with none, the keyframe extent
                # the old checkbox measured. The one behavior change is a scene
                # WITH shots and the split switched off — which now clamps to
                # them instead of shipping everything.
                "setCurrentIndex": 1,
            },
            "apply_declared_takes": {
                "widget_type": "ComboBox",
                "group": "Animation",
                # Mirror of mayatk's row (2026-09-13; a checkbox here before).
                # NOT the checkbox's objectName: a template saved when this row
                # was a checkbox carries apply_declared_takes as a BOOL, and
                # combos persist by INDEX -- restoring `true` would select
                # index 1 (Shots Only) and silently stop shipping the sequence.
                # A fresh name trips the PresetManager's uncovered-keys warning
                # instead, so the user re-saves deliberately. (The TASK key
                # stays apply_declared_takes: the method takes the mode, and a
                # headless caller's legacy True still means what it did.)
                "object_name": "animation_clips",
                "set_row_label": "Animation Clips",
                "setToolTip": TooltipFormat.fmt(
                    title="Animation Clips",
                    body="Which animation the deliverable ships: the declared "
                    "shots as separate clips, the whole timeline as one "
                    "continuous clip, or both.",
                    bullets=[
                        "<b>Shots + Full Sequence</b> — both, the historical "
                        "shape. Nothing to choose between if the consumer is "
                        "unknown.",
                        "<b>Shots Only</b> — the shots, without the stack they "
                        "were cut from. For a player that switches clips.",
                        "<b>Full Sequence Only</b> — one continuous clip. For a "
                        "player that seeks a window inside it; each shot's frame "
                        "range still rides in <b>extras.animation_web</b>.",
                    ],
                    notes=[
                        "The two halves hold the SAME performance — the shots are "
                        "cut from the sequence — so a player that reads one never "
                        "reads the other. Measured on a production assembly: the "
                        "sequence alone was 66.5 MB, the shots 43.8 MB.",
                        "Requires shots defined in the Shots panel; with none "
                        "declared every mode ships the one continuous clip.",
                        "This is <b>not</b> what ships the shot metadata — "
                        "<b>Export Scene Data Node</b> already does that, and the "
                        "two share one refresh.",
                        "The <b>FBX</b> leg splits takes for Unity on the two "
                        "shot-bearing modes. Blender's split <b>replaces</b> the "
                        "single scene-range take with the per-shot ones, so the "
                        "FBX ships the same takes for both; the <b>GLB</b> keeps "
                        "the difference, rebuilding its clips per mode.",
                        "Forces Bake Animation on, and widens the scene frame "
                        "range to cover the takes it arms; <b>Bake Range</b> "
                        "then widens to cover them in turn, so the two cannot "
                        "disagree. Both are restored after the write.",
                    ],
                ),
                "add": self._animation_clips_options,
                # Index 2 = "Shots + Full Sequence", what the checkbox this
                # replaced did when ticked -- and it was default-on, so a
                # panel opened without a preset ships exactly what it used to.
                "setCurrentIndex": 2,
            },
            "ignore_groups": {
                "widget_type": "QLineEdit",
                "panel": "settings",
                "set_row_label": "Ignore",
                "setPlaceholderText": "Group names to ignore (comma-separated, wildcards ok)",
                "setToolTip": TooltipFormat.fmt(
                    title="Ignore Groups",
                    body="Comma-separated name patterns of top-level objects to "
                    "drop from the export set.",
                    notes=[
                        "Example: temp, proxy",
                        "Wildcards: <b>*</b> any run of characters, <b>?</b> a "
                        "single one &mdash; <b>temp*</b> catches temp_01 and "
                        "tempRig, <b>*_proxy</b> catches hull_proxy.",
                        "A pattern with no wildcard matches that exact name.",
                        "Leave empty to skip.",
                        "Matching ignores case unless the <b>Aa</b> button beside "
                        "the field is on.",
                    ],
                ),
                "setText": "temp",
                "value_method": "text",
            },
            "export_data_node": {
                "widget_type": "QCheckBox",
                "panel": "settings",
                "setText": "Export Scene Data Node",
                "setToolTip": TooltipFormat.fmt(
                    title="Export Scene Data Node",
                    body="Ship the shared <b>data_export</b> carrier in the export "
                    "so the metadata stamped on it (the Lightmap Baker's "
                    "lightmap_metadata, and any other producer's channel) rides "
                    "into the FBX as user properties.",
                    notes=[
                        "Every export scope is geometry-driven, so the carrier "
                        "would otherwise be dropped.",
                        "No-op when the scene has no carrier.",
                        "A readable copy is also written beside the export as "
                        ".scene_data.json.",
                        "This ships the metadata only — it never changes the "
                        "animation.",
                    ],
                ),
                "setChecked": True,
            },
        }

    @property
    def check_definitions(self) -> Dict[str, Dict[str, Any]]:
        """Return the check definitions for the UI.

        A failed check aborts the export, so each tooltip below leads with what
        makes it fail.  Tooltip authoring rules: see :attr:`task_definitions`.
        """
        from uitk.widgets.mixins.tooltip_mixin import TooltipFormat

        return {
            "check_referenced_objects": {
                "widget_type": "QCheckBox",
                "group": "General",
                "setText": "Check For Referenced Objects",
                "setToolTip": TooltipFormat.fmt(
                    title="Check For Referenced Objects",
                    body="Fails the export when the scene contains linked "
                    "libraries — Blender's analogue of Maya file references.",
                    notes=["Make the data local to pass."],
                ),
                "setChecked": True,
            },
            "check_geometry_lod_suffix": {
                "widget_type": "QCheckBox",
                "group": "Hierarchy & Naming",
                "setText": "Check Geometry LOD Suffix (_LODx)",
                "setToolTip": TooltipFormat.fmt(
                    title="Check Geometry LOD Suffix (_LODx)",
                    body="Lists geometry named with an LOD suffix — '_LOD' alone "
                    "or followed by digits ('_LOD1', '_LOD02'), case-insensitive.",
                    notes=[
                        "Informational only: it reports what it finds and never "
                        "fails the export."
                    ],
                ),
                "setChecked": True,
            },
            "check_duplicate_names": {
                "widget_type": "ComboBox",
                "group": "Hierarchy & Naming",
                "set_row_label": "Duplicate Names",
                "setToolTip": TooltipFormat.fmt(
                    title="Check For Duplicate Names",
                    body="Fails the export when two nodes in the export set "
                    "share a base name. The dial is how wide it looks — each "
                    "step includes the one above it.",
                    bullets=[
                        "<b>Locators</b> — Empties: attach points and sockets, "
                        "which whatever consumes them downstream matches by "
                        "name.",
                        "<b>Locators &amp; Joints</b> — adds armatures and "
                        "their bones, which the FBX writes as the skeleton; "
                        "duplicate bone names break skinning and retargeting "
                        "on import, and bone names are unique only within one "
                        "armature.",
                        "<b>Connected &amp; Animated</b> — adds every object "
                        "carrying a constraint, an action, drivers or NLA "
                        "tracks. Their names are what the take and metadata "
                        "bindings resolve against.",
                        "<b>All Export Objects</b> — every object in the set. "
                        "The strictest setting: expect noise from helper "
                        "hierarchies that collide harmlessly in the FBX.",
                    ],
                    notes=[
                        "Blender's auto '.001' suffix is stripped before "
                        "comparing, so 'pivot' and 'pivot.001' collide — which "
                        "is what a consumer matching them by name downstream "
                        "will see.",
                        "<b>OFF</b> disables the check.",
                    ],
                ),
                "add": self._duplicate_name_options,
                # Applied after 'add' (which lands on index 0): Locators is the
                # scope the check shipped with as a plain checkbox.
                "setCurrentIndex": 1,
            },
            "check_root_default_transforms": {
                "widget_type": "QCheckBox",
                "group": "Hierarchy & Naming",
                "setText": "Check Root Default Transforms",
                "setToolTip": TooltipFormat.fmt(
                    title="Check Root Default Transforms",
                    body="Fails the export when a root group Empty is not at "
                    "identity — location and rotation (0, 0, 0), scale (1, 1, 1).",
                ),
                "setChecked": True,
            },
            "check_hierarchy_vs_existing_fbx": {
                "widget_type": "QCheckBox",
                "group": "Hierarchy & Naming",
                "setText": "Check Hierarchy vs Existing FBX",
                "setToolTip": TooltipFormat.fmt(
                    title="Check Hierarchy vs Existing FBX",
                    body="Would fail the export when the hierarchy differs from "
                    "the previous export — nodes that went missing or appeared, "
                    "the signature of an accidental change.",
                    notes=[_NEEDS_HIERARCHY_MANAGER],
                ),
                "setChecked": False,
                "setEnabled": False,
            },
            "check_hidden_geometry": {
                "widget_type": "QCheckBox",
                "group": "Geometry",
                "setText": "Check For Hidden Geometry",
                "setToolTip": TooltipFormat.fmt(
                    title="Check For Hidden Geometry",
                    body="Fails the export when geometry in the set is hidden.",
                    notes=[
                        "The export is selection-based, so hidden meshes are "
                        "silently dropped from the FBX — this check catches that "
                        "content loss before the write. (Maya's exporter has the "
                        "opposite problem: there hidden geometry ships anyway.)"
                    ],
                ),
                "setChecked": True,
            },
            "check_overlapping_duplicate_mesh": {
                "widget_type": "QCheckBox",
                "group": "Geometry",
                "setText": "Check For Overlapping Duplicates",
                "setToolTip": TooltipFormat.fmt(
                    title="Check For Overlapping Duplicates",
                    body="Fails the export when two meshes occupy the same space — "
                    "typically a duplicate left sitting on top of the original.",
                ),
                "setChecked": True,
            },
            "check_objects_below_floor": {
                # Mirrors mayatk: a depth is a bounded number, so a spin box
                # whose value IS how far geometry may reach below the floor,
                # with 0 reading back as "OFF" -- under a fresh objectName so a
                # checkbox-era template's bool trips the uncovered-keys warning
                # instead of restoring as a depth of 1.0. The TASK key stays.
                "widget_type": "SpinBox",
                "object_name": "floor_depth",
                "group": "Geometry",
                "set_row_label": "Max Depth Below Floor",
                "set_limits": [0, 1000, 0.1, 2],
                "setValue": 0.5,
                "setCustomDisplayValues": {0: "OFF"},
                "setToolTip": TooltipFormat.fmt(
                    title="Max Depth Below Floor",
                    body="Fails the export when geometry reaches deeper than this "
                    "below Z=0 (Blender is Z-up), in scene units.",
                    notes=[
                        "The default of 0.5 lets a shallow penetration pass on "
                        "its own.",
                        "Set to 0 (OFF) to disable.",
                    ],
                ),
                "value_method": "value",
            },
            "check_duplicate_materials": {
                "widget_type": "QCheckBox",
                "group": "Materials & Paths",
                "setText": "Check For Duplicate Materials",
                "setToolTip": TooltipFormat.fmt(
                    title="Check For Duplicate Materials",
                    body="Fails the export when two of the export materials are "
                    "duplicates of each other.",
                    notes=[
                        "The <b>Reassign Duplicate Materials</b> task merges "
                        "exactly what this reports."
                    ],
                ),
                "setChecked": True,
            },
            "check_path_length": {
                # Mirrors mayatk: a bounded character budget is a spin box, the
                # default is THIS machine's OS limit, and 0 reads back as "OFF".
                "widget_type": "SpinBox",
                "group": "Materials & Paths",
                "set_row_label": "Max Path Length",
                "set_limits": [0, 32767, 1, 0],
                "setValue": ptk.FileUtils.path_length_limit(),
                "setCustomDisplayValues": {0: "OFF"},
                "setToolTip": TooltipFormat.fmt(
                    title="Max Path Length",
                    body="Fails the export when the destination, or any texture "
                    "feeding the export materials, resolves to a path longer than "
                    "this many characters.",
                    notes=[
                        "Over-long paths fail late and opaquely, and a path that "
                        "fits on this machine can still break on one without long "
                        "paths enabled (260 characters).",
                        "Set to 0 (OFF) to disable.",
                    ],
                ),
                "value_method": "value",
            },
            "check_valid_paths": {
                "widget_type": "QCheckBox",
                "group": "Materials & Paths",
                "setText": "Check For Valid Paths",
                "setToolTip": TooltipFormat.fmt(
                    title="Check For Valid Paths",
                    body="Fails the export when a texture feeding the export "
                    "materials, a committed lightmap, or a linked library does "
                    "not resolve on disk.",
                    notes=[
                        "Lightmaps have no Image datablock — the bake marker "
                        "records the folder it was committed from. A map that "
                        "folder no longer holds is looked for where the GLB "
                        "conversion looks (the project's texture folders, then "
                        "all of them recursively); found elsewhere it ships and "
                        "is noted, found nowhere it fails the export.",
                        "Images that will not ship (the World/HDR environment "
                        "texture, images left orphaned by a duplicate-material "
                        "cleanup) are not reported.",
                    ],
                ),
                "setChecked": True,
            },
            "check_texture_file_size": {
                # Mirrors mayatk: a bounded MB budget is a spin box, and 0 reads
                # back as "OFF" (the check treats a falsy limit as disabled).
                "widget_type": "SpinBox",
                "group": "Materials & Paths",
                "set_row_label": "Max Size (MB)",
                "set_limits": [0, 4096, 1, 0],
                "setValue": 16,
                "setCustomDisplayValues": {0: "OFF"},
                "setToolTip": TooltipFormat.fmt(
                    title="Max Texture File Size (MB)",
                    body="Fails the export when any texture feeding the export "
                    "materials is larger than this on disk.",
                    notes=[
                        "Catches un-downsized authoring maps — an 8K master left "
                        "wired up — that would bloat the shipped asset.",
                        "Set to 0 (OFF) to disable.",
                    ],
                ),
                "value_method": "value",
            },
            "check_output_writable": {
                "widget_type": "QCheckBox",
                "group": "General",
                "setText": "Check Output File Is Writable",
                "setToolTip": TooltipFormat.fmt(
                    title="Check Output File Is Writable",
                    body="Fails the export when a file it is about to write is "
                    "held open by another process.",
                    notes=[
                        "Windows will not let anything replace a file while a "
                        "viewer, a preview or an engine has it open.",
                        "Runs before the first scene change, so a locked "
                        "destination costs milliseconds instead of the whole "
                        "pipeline — the write is the LAST thing an export does.",
                        "Names the process to close whenever Windows will say.",
                    ],
                ),
                "setChecked": True,
            },
            "check_framerate": {
                "widget_type": "ComboBox",
                "group": "Animation",
                "set_row_label": "Framerate",
                "setToolTip": TooltipFormat.fmt(
                    title="Scene Framerate",
                    body="Fails the export when the scene's FPS is not the "
                    "framerate selected here.",
                    notes=[
                        "Skipped when the scene has no keyframes.",
                        "<b>OFF</b> disables the check.",
                    ],
                ),
                "add": self._frame_rate_options,
            },
            "check_untied_keyframes": {
                "widget_type": "QCheckBox",
                "group": "Animation",
                "setText": "Check For Untied Keyframes",
                "setToolTip": TooltipFormat.fmt(
                    title="Check For Untied Keyframes",
                    body="Fails the export when an animated channel has no bookend "
                    "key at its object's own keyed extent.",
                    notes=[
                        "The <b>Tie All Keyframes</b> task inserts the missing "
                        "bookend keys."
                    ],
                ),
                "setChecked": True,
            },
            "check_floating_point_keys": {
                "widget_type": "QCheckBox",
                "group": "Animation",
                "setText": "Check For Floating Point Keys",
                "setToolTip": TooltipFormat.fmt(
                    title="Check For Floating Point Keys",
                    body="Fails the export when a key sits on a fractional frame.",
                    notes=[
                        "The <b>Snap Keys To Frame</b> task rounds them to whole "
                        "frames."
                    ],
                ),
                "setChecked": True,
            },
        }

    @property
    def definitions(self) -> Dict[str, Dict[str, Any]]:
        """Return all definitions combined for backward compatibility."""
        return {**self.task_definitions, **self.check_definitions}
