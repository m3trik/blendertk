# !/usr/bin/python
# coding=utf-8
from pythontk.core_utils.module_resolver import bootstrap_package


__package__ = "blendertk"
__version__ = "0.1.0"

"""blendertk — Blender utilities that do for the tentacle Blender slots what mayatk does
for the Maya slots.

The public surface **mirrors mayatk's names** (``btk.X`` ↔ ``mtk.X``) so the shared
tentacle slots stay branch-free. Lazy attribute resolution via pythontk's
``bootstrap_package`` keeps this module lean; helpers are implemented lazily as the
Blender slots demand them (do not pre-create empty ``*_utils`` groups — YAGNI).

Mirror at the *name + behavior* level, not signatures (mayatk speaks string-node idioms,
bpy speaks object refs). Where a domain's data model diverges (rigging, NURBS, shader
graphs) the name mirror is relaxed in favor of Blender-idiomatic names. Prefer a native
``bpy.ops`` / ``bmesh.ops`` over reimplementing a mayatk algorithm — see
``tentacle/docs/BLENDER_PORT_PLAN.md`` §5.
"""

# Unified include dictionary; mirrors the mayatk pattern (list form exposes a class plus
# module-level helpers, e.g. ["CoreUtils", "undoable", "get_env_info"]).
DEFAULT_INCLUDE = {
    "core_utils._core_utils": [
        "CoreUtils",
        "undoable",
        "get_env_info",
        "get_recent_files",
        "get_recent_autosave",
    ],
    "xform_utils._xform_utils": [
        "XformUtils",
        "freeze_transforms",
        "drop_to_grid",
        "center_pivot",
        "get_pivot_modes",
        "match_scale",
        "move_to",
        "get_world_bbox",
    ],
    "node_utils._node_utils": [
        "NodeUtils",
        "get_instances",
        "replace_with_instances",
        "uninstance",
    ],
    "cam_utils._cam_utils": [
        "CamUtils",
        "adjust_camera_clipping",
    ],
    "uv_utils._uv_utils": [
        "UvUtils",
        "move_uvs",
        "delete_extra_uv_sets",
    ],
    "ui_utils._ui_utils": [
        "UiUtils",
        "open_editor",
        "get_editor_types",
    ],
    "mat_utils._mat_utils": [
        "MatUtils",
        "get_mats",
        "create_mat",
        "assign_mat",
        "find_by_mat_id",
        "select_by_material",
        "reload_textures",
    ],
    "anim_utils._anim_utils": [
        "AnimUtils",
        "get_fcurves",
        "shift_keys",
        "move_keys_to_frame",
        "invert_keys",
        "stagger_keys",
        "snap_keys",
        "scale_keys",
        "set_stepped",
        "delete_keys",
        "fit_playback_range",
        "copy_keys",
        "paste_keys",
    ],
    "edit_utils._edit_utils": [
        "EditUtils",
        "decimate",
        "dissolve_coplanar",
        "boolean_op",
        "triangulate",
        "tris_to_quads",
        "subdivide_mesh",
        "set_subdivision",
        "set_shading",
        "set_edge_hardness",
        "flip_normals",
        "recalculate_normals",
        "crease_edges",
        "clean_geometry",
    ],
}

bootstrap_package(
    globals(),
    include=DEFAULT_INCLUDE,
)
