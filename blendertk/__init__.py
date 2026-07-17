# !/usr/bin/python
# coding=utf-8
from pythontk.core_utils.module_resolver import bootstrap_package


__package__ = "blendertk"
__version__ = "0.5.14"

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
        "undo_chunk",
        "undo_checkpoint",
        "get_env_info",
        "ensure_image_deps",
        "get_recent_files",
        "get_recent_autosave",
        "get_scene_info",
        "format_scene_info_html",
        "analyze_scene",
        "cleanup_scene",
        "get_areas",
        "get_view3d_context",
        "window_context_override",
        "selected_objects",
        "active_object",
    ],
    "core_utils.preview": ["Preview"],
    # Auto-instancer — mirror of mayatk's ``core_utils.auto_instancer``
    # (``btk.auto_instance`` ↔ ``mtk.auto_instance``); matching math and
    # assembly clustering are shared via pythontk (PointCloud/AssemblySorter).
    "core_utils.auto_instancer._auto_instancer": ["AutoInstancer", "auto_instance"],
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
        "mirror_uvs",
        "pin_uvs",
        "get_texel_density",
        "set_texel_density",
        "get_uv_coords",
        "set_uv_coords",
        "stack_uv_shells",
        "distribute_uv_shells",
        "straighten_uvs",
        "straighten_uv_shells",
        "derive_auto_seams",
        "align_uvs",
        "gather_uv_shells",
        "orient_uv_shells",
        "randomize_uv_shells",
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
        "get_visible_geometry",
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
    # Script Output console — mirror of mayatk's ``env_utils.script_output``. Docks a native
    # Info Log area into the main window (the anchor) and shadows it with a frameless
    # ``uitk.ScriptOutput`` skin (Route 2+); capture (stdout/stderr/logging) runs from startup
    # and the shown/hidden state persists across sessions. Module-level ``show``/``toggle``/
    # ``hide`` drive it from the editors slot; ``begin_capture``/``restore`` from the host.
    "env_utils.script_output": [
        "ScriptConsole",
    ],
    # Maya bridge engine — one-way send of the selection to a fresh Maya (the ``MayaBridgeSlots``
    # panel class is discovered by BlenderUiHandler, not registered). Counterpart of mayatk's
    # ``BlenderBridge``.
    "env_utils.maya_bridge._maya_bridge": [
        "MayaBridge",
    ],
    # Pull direction — import a Maya scene (.ma/.mb) via a headless-Maya FBX
    # round-trip. btk-only by design (Maya opens its own scenes natively);
    # ledgered in tentacle/docs/parity_map.py.
    "env_utils.maya_bridge._scene_import": [
        "MayaSceneImport",
        "import_maya_scene",
    ],
    # Unity Bridge — mirror of mayatk's ``env_utils.unity_bridge._unity_bridge`` (the
    # ``UnityBridgeSlots`` panel is discovered by BlenderUiHandler, not registered here).
    "env_utils.unity_bridge._unity_bridge": [
        "UnityBridge",
    ],
    # Scene Exporter — batch FBX/GLB export task pipeline, mirror of mayatk's
    # ``env_utils.scene_exporter`` (the ``SceneExporterSlots`` panel class is discovered by
    # BlenderUiHandler, not registered).
    "env_utils.scene_exporter._scene_exporter": [
        "SceneExporter",
    ],
    "env_utils.scene_exporter.task_manager": [
        "TaskManager",
    ],
    # Hierarchy Manager — diff/repair a scene hierarchy against a reference .blend linked as a
    # library (mirror of mayatk's ``env_utils.hierarchy_manager._hierarchy_manager``; the
    # ``HierarchyManagerSlots`` panel is discovered by BlenderUiHandler, not registered). Pull
    # (mayatk's ``ObjectSwapper``) isn't ported — see the slots module docstring.
    "env_utils.hierarchy_manager._hierarchy_manager": [
        "HierarchyManager",
    ],
    "light_utils._light_utils": [
        "LightUtils",
        "set_world_hdri",
        "get_world_hdri",
        "clear_world_hdri",
        "set_world_ray_visibility",
        "get_world_ray_visibility",
        "set_world_importance_resolution",
        "get_world_importance_resolution",
    ],
    # Lightmap Baker — engine + co-located panel. The ``LightmapBakerSlots`` class is
    # discovered by ``BlenderUiHandler`` (not registered here), matching the other tool Slots.
    "light_utils.lightmap_baker.lightmap_baker": [
        "LightmapBaker",
    ],
    "ui_utils._ui_utils": [
        "UiUtils",
        "open_editor",
        "find_editor",
        "close_area",
        "close_editor",
        "dock_editor",
        "toggle_editor",
        "toggle_fullscreen_area",
        "toggle_window_bars",
        "main_window",
        "get_editor_types",
        "menu_exists",
        "call_native_menu",
        "popup_message",
    ],
    # Native-window helpers (win32) for hosting Qt widgets around a Blender window: the
    # child-embed primitives behind ``QtDock`` and the owned-top-level ``set_owner`` mode.
    # No bpy dependency (callers pass the region object). Exposed as a class to keep the
    # flat ``btk.*`` namespace clean.
    "ui_utils.blender_window": [
        "BlenderWindow",
    ],
    # The native dock container: hosts ANY Qt widget as the body of a true docked Blender
    # area (a WS_CHILD of the GHOST window glued to the area's content region — no overlay,
    # no polling). Backs ``env_utils.script_output``; reusable for any docked Qt panel.
    "ui_utils.qt_dock": [
        "QtDock",
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
        "to_project_relative",
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
    # Material manifest (baked-map metadata carrier) — mirror of mayatk's
    # ``mat_utils.mat_manifest``; shared by the Marmoset/Substance bridges.
    "mat_utils.mat_manifest": [
        "MatManifest",
    ],
    # Marmoset Bridge — mirror of mayatk's ``mat_utils.marmoset_bridge._marmoset_bridge``
    # (``MarmosetBridgeSlots`` panel discovered by the handler, not registered).
    "mat_utils.marmoset_bridge._marmoset_bridge": [
        "MarmosetBridge",
    ],
    # Substance Bridge — mirror of mayatk's ``mat_utils.substance_bridge._substance_bridge``
    # (``SubstanceBridgeSlots`` panel discovered by the handler, not registered).
    "mat_utils.substance_bridge._substance_bridge": [
        "SubstanceBridge",
    ],
    "anim_utils._anim_utils": [
        "AnimUtils",
        "get_fcurves",
        "scene_has_animation",
        "set_current_frame",
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
        "transfer_keyframes",
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
    # Shots — Blender acquisition + persistence adapter over pythontk's shots
    # engine (``core_utils.engines.shots``).  The shot model / planner / detection
    # math is shared upstream; only ``BlenderShotStore`` (scene hooks) and
    # ``BlenderScenePersistence`` (scene-property JSON) live here.  The co-located
    # Shots panel is discovered by ``BlenderUiHandler``, not registered here.
    "anim_utils.shots._shots": [
        "BlenderShotStore",
        "BlenderScenePersistence",
    ],
    # Shot sequencer engine — timeline-move surface over the shared planner
    # (move/ripple/gap/reorder/trim).  The Shots panel drives it; the visual
    # Sequencer panel is a later phase.
    "anim_utils.shots.shot_sequencer._shot_sequencer": ["ShotSequencer"],
    # Shot Manifest adapter — Blender scene hooks over the shared manifest engine
    # (CSV → shots + fade/audio behaviors).  Co-located panel discovered by
    # BlenderUiHandler, not registered here.
    "anim_utils.shots.shot_manifest._shot_manifest": ["BlenderShotManifest"],
    # Smart Bake — engine + session/restore store, mirror of mayatk's
    # ``anim_utils.smart_bake`` (the ``SmartBakeSlots`` panel is discovered by
    # ``BlenderUiHandler``, not registered here).
    "anim_utils.smart_bake._smart_bake": "SmartBake",
    "anim_utils.smart_bake.bake_session": "RestoreResult",
    # Blendshape Animator — morph-authoring engine (base+target mesh -> keyed shape key, with
    # driver-driven corrective "tween" shapes for a custom curve), mirror of mayatk's
    # ``anim_utils.blendshape_animator.BlendshapeAnimator``. The co-located
    # ``BlendshapeAnimatorSlots`` panel is discovered by ``BlenderUiHandler`` (not registered
    # here), matching the other tool Slots.
    "anim_utils.blendshape_animator._blendshape_animator": [
        "BlendshapeAnimator",
    ],
    # Audio Clips — scene-wide sound-strip CRUD over the Video Sequence Editor, mirror of
    # mayatk's ``audio_utils`` (the ``AudioClipsSlots`` panel is discovered by
    # ``BlenderUiHandler``, not registered here).
    "audio_utils._audio_utils": [
        "AudioUtils",
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
    # Target Weld — interactive drag-a-vertex-onto-another merge tool, the Blender build of
    # Maya's native ``targetWeldCtx`` / ``MergeVertexTool`` (which mayatk drives directly, so
    # there is no ``mtk`` counterpart module — the mirror is name + behavior of the Maya tool
    # itself). Backs tentacle's ``polygons.b043`` / ``b008`` (mergeToCenter) on Blender.
    "edit_utils.target_weld": [
        "TargetWeld",
        "target_weld",
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
