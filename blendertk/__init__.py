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
``bpy.ops`` / ``bmesh.ops`` over reimplementing a mayatk algorithm. Parity SSoT:
``tentacle/docs/PARITY_AUDIT.md`` (measured) + ``PARITY_PORTING_PLAN.md`` (port-this-next recipes).
"""

# Unified include dictionary; mirrors the mayatk pattern (list form exposes a class plus
# module-level helpers, e.g. ["CoreUtils", "undoable", "get_env_info"]).
DEFAULT_INCLUDE = {
    "core_utils._core_utils": [
        "CoreUtils",
        "undoable",
        "undo_checkpoint",
        "get_env_info",
        "ensure_image_deps",
        "get_recent_files",
        "get_recent_autosave",
        "get_scene_info",
        "format_scene_info_html",
        "analyze_scene",
        "cleanup_scene",
        "get_view3d_context",
        "selected_objects",
    ],
    "core_utils.preview": ["Preview"],
    # Event-subscription manager — mirror of mayatk's ``core_utils.script_job_manager`` over
    # ``bpy.app.handlers`` (``btk.ScriptJobManager`` ↔ ``mtk.ScriptJobManager``).
    "core_utils.script_job_manager": ["ScriptJobManager"],
    # Diagnostics subpackage — mirror of mayatk's ``core_utils.diagnostics``. The ``->Diagnostics``
    # alias multi-inherits the per-module diag classes into one ``btk.Diagnostics`` namespace
    # (``Diagnostics.find_problem_geometry`` / ``fix_non_orthogonal_axes``); ``find_problem_geometry``
    # re-homed here from ``edit_utils`` (``btk.find_problem_geometry`` still resolves).
    "core_utils.diagnostics->Diagnostics": "*",
    "core_utils.diagnostics.mesh_diag": [
        "MeshDiagnostics",
        "find_problem_geometry",
    ],
    "core_utils.diagnostics.transform_diag": [
        "TransformDiagnostics",
        "fix_non_orthogonal_axes",
    ],
    "xform_utils._xform_utils": [
        "XformUtils",
        "freeze_transforms",
        "restore_transforms",
        "has_stored_transforms",
        "scale_connected_edges",
        "drop_to_grid",
        "center_pivot",
        "transfer_pivot",
        "get_pivot_modes",
        "match_scale",
        "move_to",
        "get_world_bbox",
        "get_bounding_box",
        "get_center_point",
        "get_distance",
        "order_by_distance",
        "aim_object_at_point",
    ],
    # Matrix helpers — mirror of mayatk's ``xform_utils.matrices.Matrices`` (the portable
    # compose/decompose/space-conversion + object-matrix IO subset over ``mathutils.Matrix``;
    # the Maya rigging node-graph builders have no Blender analogue — see the module docstring).
    "xform_utils.matrices": [
        "Matrices",
    ],
    "node_utils._node_utils": [
        "NodeUtils",
        "get_instances",
        "replace_with_instances",
        "uninstance",
        "get_parent",
        "get_children",
        "get_shape",
        "reparent",
    ],
    # DataNodes lives in its own module (mirror of mayatk's ``node_utils.data_nodes``).
    "node_utils.data_nodes": [
        "DataNodes",
    ],
    "cam_utils._cam_utils": [
        "CamUtils",
        "adjust_camera_clipping",
    ],
    "uv_utils._uv_utils": [
        "UvUtils",
        "move_uvs",
        "delete_extra_uv_sets",
        "cleanup_uv_sets",
        "find_lightmap_uv_set",
        "create_lightmap_uvs",
        "transform_uvs",
        "pin_uvs",
        "get_texel_density",
        "set_texel_density",
        "get_uv_coords",
        "set_uv_coords",
        "stack_uv_shells",
        "distribute_uv_shells",
        "straighten_uvs",
    ],
    # RizomUV bridge engine — mirror of mayatk's ``uv_utils.rizom_bridge._rizom_bridge.RizomUVBridge``
    # (the ``RizomBridgeSlots`` panel class is discovered by the handler, not registered).
    "uv_utils.rizom_bridge._rizom_bridge": [
        "RizomUVBridge",
    ],
    "display_utils._display_utils": [
        "DisplayUtils",
        "explode_view",
        "unexplode_view",
        "unexplode_all",
        "is_exploded",
    ],
    # Color ID tool — engine + co-located panel (ColorIdSlots discovered by the handler).
    "display_utils.color_id": [
        "ColorId",
    ],
    # Exploded View tool — co-located ``ExplodedViewSlots`` panel (discovered by the handler, not
    # registered); the explode/unexplode engine lives module-level in ``_display_utils`` above
    # (``explode_view`` / ``unexplode_view`` / ``unexplode_all`` / ``is_exploded``).
    "env_utils._env_utils": [
        "EnvUtils",
        "find_blend_files",
        "list_libraries",
        "linked_blend_paths",
        "is_blend_linked",
        "link_blend_file",
        "reload_library",
        "remove_library",
        "make_library_local",
        "find_workspaces",
        "open_scene",
        "format_scene_name",
        "save_scene_as",
        "rename_scene_file",
        "delete_scene_file",
        "set_reference_display_mode",
        "get_reference_display_mode",
    ],
    # FBX import/export — mirror of mayatk's ``env_utils.fbx_utils.FbxUtils``. ``export_selection_fbx``
    # (the bridges' selection-only export) moved here from ``core_utils``; ``import_fbx`` added.
    "env_utils.fbx_utils": [
        "FbxUtils",
        "export_selection_fbx",
        "import_fbx",
    ],
    # Headless test/launch harness — mirror of mayatk's ``env_utils.maya_connection.MayaConnection``.
    # Launches a FRESH ``blender --background`` per run (session-safe by construction); no bpy.
    "env_utils.blender_connection": [
        "BlenderConnection",
    ],
    # Script Output console — mirror of mayatk's ``env_utils.script_output``. Opens a native,
    # dockable Info Log window (the anchor) and shadows it with a frameless ``uitk.ScriptOutput``
    # skin (Route 2+); module-level ``show``/``toggle``/``hide`` drive it from the editors slot.
    "env_utils.script_output": [
        "ScriptConsole",
    ],
    # Maya bridge engine — one-way send of the selection to a fresh Maya (the ``MayaBridgeSlots``
    # panel class is discovered by BlenderUiHandler, not registered). Counterpart of mayatk's
    # ``BlenderBridge``.
    "env_utils.maya_bridge._maya_bridge": [
        "MayaBridge",
    ],
    "light_utils._light_utils": [
        "LightUtils",
        "set_world_hdri",
        "get_world_hdri",
        "set_world_ray_visibility",
        "get_world_ray_visibility",
    ],
    # Lightmap Baker — engine + co-located panel. The ``LightmapBakerSlots`` class is
    # discovered by ``BlenderUiHandler`` (not registered here), matching the other tool Slots.
    "light_utils.lightmap_baker.lightmap_baker": [
        "LightmapBaker",
    ],
    "ui_utils._ui_utils": [
        "UiUtils",
        "open_editor",
        "get_editor_types",
        "menu_exists",
        "call_native_menu",
    ],
    # Native-window geometry/owner helpers (win32) for shadowing a Qt overlay over a Blender
    # window — backs ``env_utils.script_output``'s area-shadow skin. No bpy dependency (callers
    # pass the region object). Exposed as a class to keep the flat ``btk.*`` namespace clean.
    "ui_utils.blender_window": [
        "BlenderWindow",
    ],
    # App-style setter — match Blender's UI chrome to another DCC's look via Blender's NATIVE
    # interface_theme preset system (ships a canonical Maya.xml theme preset in
    # style_setter/styles/ that shows up in Preferences > Themes > preset dropdown). Exposed as
    # just the class (like Bevel/Bridge/Selection) — its helpers have generic names (install,
    # is_installed, …) that don't belong in the flat btk.* namespace; use btk.StyleSetter.<fn>.
    "ui_utils.style_setter._style_setter": [
        "StyleSetter",
    ],
    "mat_utils._mat_utils": [
        "MatUtils",
        "get_mats",
        "create_mat",
        "assign_mat",
        "find_by_mat_id",
        "select_by_material",
        "reload_textures",
        "get_scene_mats",
        "is_mat_assigned",
        "get_mat_swatch_icon",
        "get_texture_paths",
        "get_texture_info",
        "get_mat_info",
        "format_mat_info_html",
        "format_texture_info_html",
        "find_materials_with_duplicate_textures",
        "reassign_duplicate_materials",
        "delete_unused_materials",
        "graph_materials",
        "get_image_records",
        "get_image_material_map",
        "materials_for_textures",
        "repath_image",
        "resolve_missing_textures",
        "normalize_texture_paths",
        "fix_color_spaces",
        "set_texture_directory",
        "find_and_copy_textures",
        "format_texture_paths_html",
        "get_shader_templates",
        "apply_shader_template",
        "create_shader_template",
        "serialize_material",
        "restore_material",
        "create_pbr_material",
        "create_pbr_materials",
        "MatUpdater",
        "update_materials",
    ],
    # Generic Cycles bake-to-texture primitive — mirror of mayatk's ``mat_utils.texture_baker``.
    # ``LightmapBaker`` (light_utils) composes this; use it directly for one-off/preview bakes.
    "mat_utils.texture_baker": [
        "TextureBaker",
    ],
    # Image-to-Plane tool — engine + co-located panel (``ImageToPlaneSlots`` discovered by the
    # handler, not registered). Mirror of mayatk's ``mat_utils.image_to_plane`` subpackage.
    "mat_utils.image_to_plane._image_to_plane": [
        "ImageToPlane",
    ],
    # Per-object render opacity — engine + co-located panel (``RenderOpacitySlots`` discovered by the
    # handler, not registered). Mirror of mayatk's ``mat_utils.render_opacity`` subpackage.
    "mat_utils.render_opacity._render_opacity": [
        "RenderOpacity",
    ],
    "anim_utils._anim_utils": [
        "AnimUtils",
        "get_fcurves",
        "scene_has_animation",
        "shift_keys",
        "move_keys_to_frame",
        "adjust_key_spacing",
        "align_selected_keyframes",
        "set_visibility_keys",
        "add_intermediate_keys",
        "remove_intermediate_keys",
        "select_keys",
        "invert_keys",
        "snap_keys",
        "set_interpolation",
        "set_stepped",
        "delete_keys",
        "fit_playback_range",
        "copy_keys",
        "paste_keys",
        "optimize_keys",
        "repair_corrupted_curves",
        "tie_keyframes",
        "bake_keys",
        "bake_blend_shapes",
        "get_animation_info",
        "format_animation_info_html",
        "format_animation_info_csv",
        "configure_render_output",
    ],
    # Per-concern key-timing modules — mirror of mayatk's ``anim_utils.scale_keys`` /
    # ``stagger_keys`` (engine class + module-level fn). The fns also stay on ``AnimUtils``
    # via a cycle-safe re-import in ``_anim_utils``.
    "anim_utils.scale_keys": [
        "ScaleKeys",
        "scale_keys",
    ],
    "anim_utils.stagger_keys": [
        "StaggerKeys",
        "stagger_keys",
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
        "average_normals",
        "set_edge_hardness",
        "select_edges_by_angle",
        "clear_custom_split_normals",
        "flip_normals",
        "recalculate_normals",
        "crease_edges",
        "clean_geometry",
        "get_similar_mesh",
        "separate_objects",
        "combine_objects",
        "loft",
        "detach_components",
        "mirror",
        "cut_along_axis",
        "wedge",
        "snap_closest_verts",
        "snap_to_grid",
        "snap_to_surface",
        "get_overlapping_faces",
        "get_overlapping_duplicates",
    ],
    # Category-driven select-by-type — mirror of mayatk's ``edit_utils.selection.Selection``
    # (``btk.Selection`` <-> ``mtk.Selection``), backing the shared ``list000`` "Select by Type"
    # list in ``tentacle/slots/*/selection.py``.
    "edit_utils.selection": [
        "Selection",
    ],
    # Array-duplication tools — one self-contained module per pattern (engine + co-located
    # panel Slots), mirroring mayatk's duplicate_linear / _radial / _grid split. The shared
    # object-array primitives live in ``_edit_utils``; the ``<Tool>Slots`` classes are
    # discovered by ``BlenderUiHandler`` (not registered here), matching how mayatk's tool
    # Slots stay out of its DEFAULT_INCLUDE.
    "edit_utils.duplicate_linear": [
        "DuplicateLinear",
        "duplicate_linear",
    ],
    "edit_utils.duplicate_radial": [
        "DuplicateRadial",
        "duplicate_radial",
    ],
    "edit_utils.duplicate_grid": [
        "DuplicateGrid",
        "duplicate_grid",
    ],
    "edit_utils.curtain": [
        "CurtainUtils",
        "CurtainRig",
        "create_curtain",
        "curtain_rail_from_selection",
    ],
    # Bevel engine — mirror of mayatk's ``edit_utils.bevel.Bevel`` (``btk.Bevel`` ↔ ``mtk.Bevel``).
    # The co-located ``BevelSlots`` panel is discovered by ``BlenderUiHandler`` (not registered
    # here), matching how mayatk's tool Slots stay out of its DEFAULT_INCLUDE.
    "edit_utils.bevel": [
        "Bevel",
    ],
    # Bridge engine — mirror of mayatk's ``edit_utils.bridge.Bridge`` (``btk.Bridge`` ↔ ``mtk.Bridge``).
    # The co-located ``BridgeSlots`` panel is discovered by ``BlenderUiHandler`` (not registered here).
    "edit_utils.bridge": [
        "Bridge",
    ],
    # Snap tool — co-located ``SnapSlots`` panel (discovered by the handler, not registered); the
    # snap engine (``snap_closest_verts`` / ``snap_to_grid`` / ``snap_to_surface``) lives in
    # ``_edit_utils`` above (mirror of mayatk's ``edit_utils.snap.Snap``).
    # Dynamic Pipe tool — engine + co-located ``DynamicPipeSlots`` panel (discovered by the handler,
    # not registered). Mirror of mayatk's ``edit_utils.dynamic_pipe`` (Hook-driven beveled curve in
    # place of Maya's NURBS-circle loft — no native loft in Blender).
    "edit_utils.dynamic_pipe": [
        "DynamicPipe",
    ],
    # Naming tool — engine + co-located ``NamingSlots`` panel (discovered by the handler, not
    # registered). Mirror of mayatk's ``edit_utils.naming`` subpackage.
    "edit_utils.naming._naming": [
        "Naming",
    ],
    # Hotkey macros — mirror of mayatk's ``edit_utils.macros`` (``btk.Macros`` ↔ ``mtk.Macros``).
    # Only ``Macros`` is exposed, matching mayatk (``MacroManager`` is the base, not a public symbol).
    "edit_utils.macros": [
        "Macros",
    ],
    # Procedural rigs — mirror of mayatk's ``rig_utils`` (one self-contained module per rig: engine
    # + co-located ``<rig>.ui`` + ``<Rig>Slots``). ``RigUtils`` is the shared constraint/driver/handle
    # base (mirror of mayatk's ``rig_utils.RigUtils``); each rig engine is exposed too, while the
    # ``<Rig>Slots`` panels are discovered by ``BlenderUiHandler`` (not registered), as mayatk's are.
    "rig_utils._rig_utils": [
        "RigUtils",
    ],
    "rig_utils.controls": [
        "Controls",
        "ControlNodes",
    ],
    "rig_utils.tube_path": [
        "TubePath",
    ],
    "rig_utils.tube_rig": [
        "TubeRig",
        "TubeStrategy",
        "TubeRigBundle",
        "register_strategy",
    ],
    "rig_utils.telescope_rig": [
        "TelescopeRig",
    ],
    "rig_utils.wheel_rig": [
        "WheelRig",
    ],
    "rig_utils.shadow_rig": [
        "ShadowRig",
    ],
    # Curve / NURBS-adjacent tools — mirror of mayatk's ``nurbs_utils``. ``NurbsUtils`` is the shared
    # curve-build / curve→mesh-bake base (Blender's bevel + 2D-fill replace Maya's loft/planarSrf/
    # nurbsToPoly layer); each curve tool engine is exposed too, while the ``<Tool>Slots`` panels are
    # discovered by ``BlenderUiHandler`` (not registered), as mayatk's are.
    "nurbs_utils._nurbs_utils": [
        "NurbsUtils",
    ],
    "nurbs_utils.image_tracer": [
        "ImageTracer",
    ],
    "nurbs_utils.curve_to_tube": [
        "CurveToTube",
    ],
}

bootstrap_package(
    globals(),
    include=DEFAULT_INCLUDE,
)
