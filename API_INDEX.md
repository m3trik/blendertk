# blendertk — API Index

_Auto-generated. Do not edit by hand. Compact symbol index — grep this for a name; for full signatures/docs, slice [API_REGISTRY.md](API_REGISTRY.md) (never Read it whole)._

### `anim_utils/_anim_utils.py` — Animation utilities — key-timing math over ``fcurve.keyframe_points`` (mirror of mayatk's
- `class AnimUtils(_AnimUtilsInternal)`
  - methods: key_arrays, key_times, key_interpolations, window_indices, shift_keys_in_window, remap_keys_in_window, step_last_key_in_window, get_fcurves, get_animated_extent, has_nla_or_data_animation, scene_has_animation, set_current_frame, shift_keys, move_keys_to_frame, adjust_key_spacing, align_selected_keyframes, set_visibility_keys, add_intermediate_keys, remove_intermediate_keys, select_keys, invert_keys, snap_keys, set_interpolation, set_stepped, delete_keys, fit_playback_range, copy_keys, paste_keys, transfer_keyframes, unbake_keys, optimize_keys, repair_corrupted_curves, tie_keyframes, bake_keys, bake_blend_shapes, get_animation_info, format_animation_info_csv, format_animation_info_html, configure_render_output, interpolation_value

### `anim_utils/blendshape_animator/_blendshape_animator.py` — Main workflow facade for shape-key morph creation, editing, and export — mirror of mayatk's
- `class BlendshapeAnimator(ptk.LoggingMixin)`
  - methods: create, edit_weight_based, edit_frame_based, edit_apply_tweens, basic_workflow, apply_all_edits, finalize_for_export, from_existing, recover_animation, diagnose_topology_issues, cleanup_topology_mismatches, remove_target_for_export

### `anim_utils/blendshape_animator/applicator.py` — Applies tween mesh edits back to the master shape key — mirror of mayatk's
- `class ApplyStatus(Enum)`
- `class Applicator(ptk.LoggingMixin)`
  - methods: validate_topology, apply_tweens

### `anim_utils/blendshape_animator/blendshape_animator_slots.py` — Switchboard slots controller for the co-located ``blendshape_animator.ui`` — Blender port of
- `class BlendshapeAnimatorSlots(BlendshapeAnimator, _BlendshapeAnimatorSlotsInternal)`
  - methods: header_init, b000_init, b000, cmb000_init, le000_init, le001_init, b001_init, b001, b003, b004_init, b004, b005, b006_init, b006, b007, b008_init, b008

### `anim_utils/blendshape_animator/creator.py` — Creates in-between (tween) target meshes for sculpting a custom morph curve — mirror of
- `class Creator(ptk.LoggingMixin, _CreatorInternal)`
  - methods: create_weight_based_tweens, create_frame_based_tween, tag_tween_mesh, get_existing_weights, find_nearby_weight

### `anim_utils/blendshape_animator/keyframes.py` — Master shape-key value keyframe animation — mirror of mayatk's
- `class Keyframes(ptk.LoggingMixin)`
  - methods: key_id, key_block, create_keyframes, test_morph, get_frame_range, preserve_sibling_values

### `anim_utils/blendshape_animator/target.py` — Tween mesh wrappers and registry — mirror of mayatk's
- `class Target`
  - methods: mesh, weight, key_block_name, base_mesh_name, target_frame, update_references
- `class Targets(ptk.LoggingMixin)`
  - methods: find_all_targets, group_by_weight, update_all_references

### `anim_utils/blendshape_animator/validator.py` — Mesh + shape-key setup validation — mirror of mayatk's
- `class Validator(ptk.LoggingMixin)`
  - methods: validate_meshes, validate_shape_setup

### `anim_utils/scale_keys.py` — Dedicated scale-keys module to keep AnimUtils lean and testable (mirror of mayatk's
- `class ScaleKeys(_ScaleKeysInternal)`
  - methods: scale_keys

### `anim_utils/segment_keys.py` — Animation-segment collection over Blender fcurves (mirror of ``mtk.SegmentKeys``).
- `class SegmentKeys(_SegmentKeysInternal)`
  - methods: collect_segments, shift_curves

### `anim_utils/shots/_detection.py` — Shot-region detection — Blender scene acquisition over the pure engine math.
- `class Detection(_DetectionInternal)`
  - methods: resolve_to_transform, detect_shot_regions, regions_from_selected_keys

### `anim_utils/shots/_shots.py` — Blender shot-store adapter — the DCC layer over ``pythontk``'s shots engine.
- `class BlenderScenePersistence`
  - methods: remove_callbacks, save, load
- `class BlenderShotStore(ShotStore, _BlenderShotStoreInternal)`
  - methods: active, has_animation, detect_regions, assess, publish_export_view, iter_action_fcurves, collect_transform_segments, collect_selected_key_entries

### `anim_utils/shots/shot_manifest/_shot_manifest.py` — Blender Shot Manifest adapter — the DCC layer over pythontk's manifest engine.
- `class BlenderShotManifest(ShotManifest, _ShotManifestInternal)`
  - methods: apply_behaviors, rewire_audio, reapply_object, from_csv

### `anim_utils/shots/shot_manifest/behaviors/_behaviors.py` — Behaviors — Blender appliers over the engine's pure keying-recipe core.
- `class Behaviors(_PyBehaviors, _BehaviorsInternal)`
  - methods: apply_behavior, verify_behavior, apply_audio_clip, compute_duration, apply_to_shots

### `anim_utils/shots/shot_manifest/manifest_data.py` — Constants, column layout, and pure helper functions for the Shot Manifest UI.
- `class ManifestData`
  - methods: fmt_behavior, format_behavior_html, try_load_blender_icons

### `anim_utils/shots/shot_manifest/range_resolver.py` — Range resolution for the Shot Manifest build pipeline (Blender-bound facade).
- `class RangeResolver`
  - methods: resolve_ranges

### `anim_utils/shots/shot_manifest/shot_manifest_slots.py` — Switchboard slots for the Shot Manifest UI (Blender).
- `class ShotManifestController(ManifestTableMixin, ptk.LoggingMixin)`
  - methods: detect, remove_callbacks, build, assess
- `class ShotManifestSlots(ptk.LoggingMixin)`
  - methods: header_init, btn_expand_missing, btn_expand_extra, btn_settings, b002, b003

### `anim_utils/shots/shot_manifest/table_presenter.py` — Tree-widget presentation mixin for the Shot Manifest controller.
- `class ManifestTableMixin(_ManifestTableMixinInternal)`
  - methods: expand_missing, expand_extra

### `anim_utils/shots/shot_sequencer/_shot_sequencer.py` — Blender shot sequencer engine — ripple editing + key motion over the shared planner.
- `class ShotSequencer(_ShotSequencerInternal)`
  - methods: shots, hidden_objects, markers, is_object_hidden, set_object_hidden, sorted_shots, shot_by_id, shot_by_name, reconcile_all_shots, define_shot, collect_object_segments, collect_shot_sequences, move_sequences_to_shot, fit_shot_to_content, trim_shot_to_content, extend_shot_to_fit, detect_shots, detect_next_shot, move_curve_keys, recreate_curve_keys, move_object_keys, move_stepped_keys, scale_object_keys, move_object_in_shot, move_shot, slide_shot, ripple_downstream, ripple_upstream, expand_shot, resize_object, set_shot_duration, resize_shot, set_shot_start, move_shot_to_position, respace, apply_gap, to_dict, from_dict

### `anim_utils/shots/shot_sequencer/clip_motion.py` — Clip motion, resize, and key-scaling logic for the shot sequencer (Blender).
- `class ClipMotionMixin(_ClipMotionMixinInternal)`
  - methods: on_clip_resized, on_clip_moved, on_clips_batch_moved, on_keys_moved, on_keys_deleted, curves_for_attr, scale_attribute_keys

### `anim_utils/shots/shot_sequencer/gap_manager.py` — Gap and range-highlight handlers for the shot sequencer controller (Blender).
- `class GapManagerMixin`
  - methods: on_range_highlight_changed, on_gap_resized, on_gap_left_resized, on_gap_moved, on_gap_lock_changed, on_gap_lock_all, on_gap_unlock_all

### `anim_utils/shots/shot_sequencer/marker_manager.py` — Marker persistence for the shot sequencer controller (Blender).
- `class MarkerManagerMixin(_MarkerManagerMixinInternal)`
  - methods: on_marker_added, on_marker_moved, on_marker_changed, on_marker_removed

### `anim_utils/shots/shot_sequencer/segment_collector.py` — Segment collection and attribute extraction for the shot sequencer (Blender).
- `class SegmentCollector`
  - methods: attr_label, abbreviate_attrs, collect_segments, active_object_set, extract_attributes, build_curve_preview

### `anim_utils/shots/shot_sequencer/shot_nav.py` — Shot navigation and combobox synchronization (Blender).
- `class ShotNavMixin`
  - methods: select_shot, on_shot_block_clicked

### `anim_utils/shots/shot_sequencer/shot_sequencer_slots.py` — Switchboard slots for the Shot Sequencer UI (Blender).
- `class ShotSequencerController(GapManagerMixin, ClipMotionMixin, ShotNavMixin, MarkerManagerMixin, ptk.LoggingMixin, _ShotSequencerControllerInternal)`
  - methods: sequencer, remove_callbacks, on_zone_context_menu, active_shot_id, on_undo, on_redo, refresh, hide_track, show_track, delete_track, on_selection_changed, on_track_selected, on_clip_locked, on_track_menu, on_header_menu, on_clip_renamed, on_playhead_moved, on_clip_menu, on_gap_menu, on_key_selection_changed
- `class ShotEditDialog`
  - methods: show
- `class ShotSequencerSlots(ptk.LoggingMixin)`
  - methods: header_init, btn_colors, spn_snap, btn_shortcuts, btn_shot_settings, cmb_shot

### `anim_utils/shots/shots_slots.py` — Switchboard slots for the Shots settings UI.
- `class ShotsController(ptk.LoggingMixin)`
  - methods: remove_callbacks, refresh_state, on_detection_changed, on_detection_mode_changed, on_initial_length_changed, on_snap_whole_frames_changed, on_fit_mode_changed, on_gap_changed, on_shot_selected, on_shot_name_changed, on_shot_start_changed, on_shot_end_changed, on_shot_desc_changed, on_delete_shot, on_delete_all_shots, on_move_shot, on_trim_empty, on_trim_all_shots
- `class ShotsSlots(ptk.LoggingMixin)`
  - methods: header_init, spn_detection, cmb_detection_mode, spn_initial_length, cmb_fit_mode, chk_snap_whole_frames, cmb_shot_select, txt_shot_name, spn_shot_start, spn_shot_end, txt_shot_desc, b000, btn_delete_all_shots, btn_move_shot, btn_apply_gap, btn_trim_empty, btn_trim_all_shots

### `anim_utils/smart_bake/_smart_bake.py` — Smart Bake engine — mirror of mayatk's ``anim_utils.smart_bake._smart_bake`` at the
- `class BakeAnalysis`
  - methods: requires_bake
- `class BakeResult`
  - methods: baked_count, success
- `class SmartBake(_SmartBakeInternal)`
  - methods: analyze, get_time_range, bake, execute, list_sessions, restore, session, run

### `anim_utils/smart_bake/bake_session.py` — Persistence and restore engine for SmartBake's nondestructive manifest — mirror of mayatk's
- `class BakeSessionStore(_BakeSessionStoreInternal)`
  - methods: load, save, push, peek, pop, list_ids, new_session_id, node_ref, resolve_ref, constraint_ref, resolve_constraint, driver_ref, resolve_driver, snapshot_blend_shape_driver, snapshot_blend_shape_action, restore_session
- `class RestoreResult`

### `anim_utils/smart_bake/smart_bake_slots.py` — Slots for the Smart Bake tool panel (``smart_bake.ui``) — Blender port of mayatk's
- `class SmartBakeSlots(ptk.LoggingMixin)`
  - methods: cmb_scope_init, cmb_backup_init, header_init, reset_defaults, b000, b001

### `anim_utils/stagger_keys.py` — Dedicated stagger-keys module to keep AnimUtils lean and testable (mirror of mayatk's
- `class StaggerKeys(_StaggerKeysInternal)`
  - methods: stagger_keys

### `audio_utils/_audio_utils.py` — Scene-wide audio-clip utilities over Blender's Video Sequence Editor (VSE).
- `class AudioUtils(ptk.LoggingMixin)`
  - methods: ensure_sequence_editor, get_sequence_editor, list_clips, get_clip, add_clip, remove_clip, remove_all_clips, rename_clip, replace_clip, move_clip, trim_clip, get_fps, clips_in_range, shift_clips_in_range, cached_waveform, clear_waveform_cache, sync_scene_range

### `audio_utils/audio_clips.py` — Audio Clips — scene-wide sound-strip management over Blender's Video Sequence Editor (VSE).
- `class AudioClipsSlots(ptk.LoggingMixin)`
  - methods: header_init, cmb000_init, cmb000, b001, b002, b005, b006, tb001_init, tb001, b003, b004_init, b004

### `audio_utils/segments.py` — Consumer-facing audio-segment discovery for the sequencer + manifest (Blender).
- `class AudioSegment(_AudioSegmentInternal)`
  - methods: is_audio, collect_all_segments, collect_segments_for_track

### `cam_utils/_cam_utils.py` — Camera utilities — clip-plane adjustment (mirror of mayatk's ``cam_utils``) plus interactive
- `class CamUtils(_CamUtilsInternal)`
  - methods: adjust_camera_clipping, get_view_state, set_view_state, fit_camera_clipping, navigate_view

### `cam_utils/camera_visibility.py` — Per-camera visibility sets — rolled infrastructure for Maya's camera-sets isolate
- `class CameraVisibility`
  - methods: set_exclusive, set_hidden, remove_from_exclusive, remove_from_hidden, remove_all, remove_all_for_all, get_sets, apply, restore, enable_auto, disable_auto

### `core_utils/_core_utils.py` — Core blendertk utilities — DCC-environment info + cross-cutting decorators.
- `class CoreUtils(ptk.CoreUtils, _CoreUtilsInternal)`
  - methods: strip_dup_suffix, undo_chunk, undoable, undo_checkpoint, get_env_info, ensure_packages, ensure_image_deps, user_config_path, get_recent_files, get_recent_autosave, get_scene_info, format_scene_info_html, analyze_scene, cleanup_scene, selected_objects, active_object, reorder_objects, get_areas, tag_redraw, get_view3d_context, window_context_override

### `core_utils/auto_instancer/_auto_instancer.py` — Scene auto-instancer: convert geometrically identical meshes to instances.
- `class InstanceCandidate`
  - methods: obj, exists
- `class InstanceGroup`
- `class AutoInstancer(ptk.LoggingMixin, _AutoInstancerInternal)`
  - methods: default_summary, format_summary, tolerance, scale_tolerance, require_same_material, check_uvs, combine_assemblies, search_radius_mult, verbose, run, find_instance_groups, run_once

### `core_utils/auto_instancer/assembly_reconstructor.py` — Logic for separating and reassembling mesh assemblies (bpy adapter).
- `class AssemblyReconstructor(_AssemblyReconstructorInternal)`
  - methods: separate_combined_meshes, cleanup_empty_sources, cleanup_empty_assembly_groups, center_transform_on_geometry, canonicalize_transform, canonicalize_leaf_meshes, reassemble_assemblies, combine_reassembled_assemblies

### `core_utils/auto_instancer/geometry_matcher.py` — Geometry analysis and matching logic for AutoInstancer (bpy adapter).
- `class GeometryMatcher(_GeometryMatcherInternal)`
  - methods: clear_cache, invalidate, quantize, get_pca_basis, get_mesh_signature, get_hierarchy_signature, are_meshes_identical, are_meshes_identical_with_transform, are_hierarchies_identical

### `core_utils/auto_instancer/instancing_strategy.py` — Instancing strategy logic for AutoInstancer (mirror of mayatk's).
- `class StrategyType(Enum)`
- `class StrategyConfig`
- `class InstancingStrategy`
  - methods: evaluate

### `core_utils/diagnostics/mesh_diag.py` — Mesh diagnostics — the Blender counterpart of mayatk's ``core_utils.diagnostics.mesh_diag``
- `class MeshDiagnostics(_MeshDiagnosticsInternal)`
  - methods: find_problem_geometry

### `core_utils/diagnostics/transform_diag.py` — Transform diagnostics — the Blender counterpart of mayatk's
- `class TransformDiagnostics(_TransformDiagnosticsInternal)`
  - methods: get_non_orthogonal, fix_non_orthogonal_axes

### `core_utils/preview.py` — Live-preview driver for the tentacle Blender tool panels — the Blender analogue of
- `class Preview`
  - methods: is_enabled, refresh, enable, disable, commit

### `core_utils/script_job_manager.py` — Centralized Blender event-subscription manager — the Blender counterpart of mayatk's
- `class ScriptJobManager`
  - methods: instance, reset, subscribe, unsubscribe, unsubscribe_all, connect_cleanup, suppress, resume, suppressed, status, print_status, teardown

### `display_utils/_display_utils.py` — Display utilities — the exploded-view toggle (mirror of mayatk's
- `class DisplayUtils(_DisplayUtilsInternal)`
  - methods: is_exploded, explode_view, unexplode_view, unexplode_all, get_visible_geometry

### `display_utils/color_id.py` — Color ID tool panel — Switchboard slot wiring for the co-located ``color_id.ui``.
- `class ColorId`
  - methods: assign_id_material, set_object_color, set_vertex_color, set_outliner_color, get_outliner_color, reset_outliner_colors, collection_tag_colors, nearest_collection_tag, add_to_color_set, get_color_set_color, remove_from_color_sets, apply_color, show_channels, has_object_color, get_object_color, get_material_color, get_average_vertex_color, color_difference, get_objects_by_color, reset_colors, reset_vertex_colors
- `class ColorIdSlots(ptk.LoggingMixin)`
  - methods: header_init, selected_objects, selected_button, target_color, b000, b001, b002, b003

### `display_utils/exploded_view.py` — Exploded View — Switchboard slot wiring for the co-located ``exploded_view.ui``.
- `class ExplodedViewSlots(ptk.LoggingMixin)`
  - methods: header_init, b000, b001, b002, b003

### `display_utils/outliner_tint.py` — Per-object **outliner text colour** for Blender — the true analogue of Maya's
- `class OutlinerTint(_OutlinerTintInternal)`
  - methods: set_color, get_color, clear, tinted_objects, is_supported, status, is_enabled, enable, disable

### `edit_utils/_curtain_drape.py` — Procedural draped-cloth (curtain) drape engine — pure geometry, no DCC.
- `class CurtainDrape(_CurtainDrapeInternal)`
  - methods: prepare, grid_points, drape

### `edit_utils/_edit_utils.py` — Mesh-editing utilities — reduce/decimate, coplanar dissolve, triangulate / tris-to-quads,
- `class EditUtils(_EditUtilsInternal)`
  - methods: hook_bind_inverse, hook_curve_point, decimate, dissolve_coplanar, triangulate, tris_to_quads, subdivide_mesh, boolean_op, set_subdivision, apply_subdivision, set_shading, average_normals, select_edges_by_angle, set_edge_hardness, clear_custom_split_normals, add_custom_split_normals, has_custom_split_normals, flip_normals, recalculate_normals, propagate_normals, conform_normals, extract_reversed_faces, clean_geometry, crease_edges, mirror, mirror_instance, cut_along_axis, wedge, snap_closest_verts, snap_to_grid, snap_to_surface, get_standoff_distances, get_similar_mesh, ungroup_objects, separate_objects, combine_objects, detach_components, get_overlapping_faces, get_overlapping_duplicates, loft

### `edit_utils/bevel.py` — Bevel tool — engine + Switchboard slot wiring for the co-located ``bevel.ui``.
- `class Bevel`
  - methods: bevel
- `class BevelSlots(ptk.LoggingMixin)`
  - methods: header_init, perform_operation

### `edit_utils/bridge.py` — Bridge tool — engine + Switchboard slot wiring for the co-located ``bridge.ui``.
- `class Bridge`
  - methods: bridge
- `class BridgeSlots(ptk.LoggingMixin)`
  - methods: header_init, perform_operation

### `edit_utils/curtain.py` — Curtain (draped-cloth) generation — the Blender build over the vendored
- `class CurtainUtils`
  - methods: curtain_rail_from_selection, create_curtain
- `class CurtainRig`
  - methods: attach
- `class CurtainSlots(ptk.LoggingMixin)`
  - methods: header_init, cmb000_init, b001, b002, perform_operation

### `edit_utils/cut_on_axis.py` — Cut-On-Axis tool panel — Switchboard slot wiring for the co-located ``cut_on_axis.ui``.
- `class CutOnAxisSlots(ptk.LoggingMixin)`
  - methods: header_init, toggle_weight_ui, perform_operation

### `edit_utils/duplicate_grid.py` — Grid array duplication + its tool panel — mirror of mayatk's ``edit_utils.duplicate_grid``.
- `class DuplicateGrid`
  - methods: duplicate_grid
- `class DuplicateGridSlots(ptk.LoggingMixin)`
  - methods: header_init, b001, perform_operation

### `edit_utils/duplicate_linear.py` — Linear array duplication + its tool panel — mirror of mayatk's ``edit_utils.duplicate_linear``.
- `class DuplicateLinear`
  - methods: duplicate_linear
- `class DuplicateLinearSlots(ptk.LoggingMixin)`
  - methods: header_init, toggle_weight_ui, b001, perform_operation

### `edit_utils/duplicate_radial.py` — Radial array duplication + its tool panel — mirror of mayatk's ``edit_utils.duplicate_radial``.
- `class DuplicateRadial(_DuplicateRadialInternal)`
  - methods: duplicate_radial
- `class DuplicateRadialSlots(ptk.LoggingMixin)`
  - methods: header_init, s015_init, s016_init, b001, perform_operation

### `edit_utils/dynamic_pipe.py` — Dynamic Pipe tool — Blender port of mayatk's ``edit_utils.dynamic_pipe``.
- `class DynamicPipe(ptk.LoggingMixin)`
- `class DynamicPipeSlots(ptk.LoggingMixin)`
  - methods: header_init, b000

### `edit_utils/macros.py` — Hotkey macros — the Blender counterpart of ``mayatk.edit_utils.macros``.
- `class DisplayMacros(_ViewportMixin)`
  - methods: m_back_face_culling, m_isolate_selected, m_wireframe, m_shading, m_lighting, m_cycle_background, m_grid, m_grid_and_image_planes, m_cycle_display_state, m_smooth_preview, m_frame
- `class EditMacros(_ViewportMixin)`
  - methods: m_multi_component, m_paste_and_rename, m_merge_vertices, m_group, m_ungroup
- `class SelectionMacros`
  - methods: m_object_selection, m_vertex_selection, m_edge_selection, m_face_selection, m_invert_selection, m_toggle_UV_select_type
- `class UiMacros(_ViewportMixin)`
  - methods: m_toggle_panels
- `class AnimationMacros`
  - methods: m_set_selected_keys, m_unset_selected_keys
- `class MacroManager`
  - methods: set_macros, call_with_input, set_macro, remove_macros, list_available_macros, macro_label, macro_category, list_categories, macro_help, get_current_bindings, apply_bindings, clear_hotkey, find_conflicts, qt_sequence_to_maya_key, maya_key_to_qt_sequence, list_presets, load_preset, save_preset, delete_preset, get_active_preset, set_active_preset, apply_saved_macros, editor_categories, get_editor_registry, apply_editor_binding, export_bindings, import_bindings, show_editor
- `class Macros(MacroManager, DisplayMacros, EditMacros, SelectionMacros, AnimationMacros, UiMacros)`

### `edit_utils/mirror.py` — Mirror tool panel — Switchboard slot wiring for the co-located ``mirror.ui``.
- `class MirrorSlots(ptk.LoggingMixin)`
  - methods: header_init, prepare_operation, perform_operation

### `edit_utils/naming/_naming.py` — Batch object naming — Blender port of mayatk's ``edit_utils.naming.Naming``.
- `class Naming(ptk.HelpMixin, ptk.LoggingMixin)`
  - methods: SUFFIX_TYPES, affix_rules, scene_objects, rename, generate_unique_name, strip_illegal_chars, strip_chars, set_case, type_key, suffix_by_type, append_location_based_suffix

### `edit_utils/naming/naming_slots.py` — Switchboard slots for the Naming panel — Blender port of mayatk's ``NamingSlots``.
- `class NamingSlots(Naming)`
  - methods: header_init, scope, dry_run, file_scope, base_names, valid_suffixes, txt000_init, txt000, txt001_init, txt001, tb000_init, tb000, tb001_init, tb001, tb002_init, tb002, tb003_init, tb003

### `edit_utils/selection.py` — Category-driven select-by-type — mirror of mayatk's ``edit_utils.selection.Selection``
- `class Selection`
  - methods: loop_multi_select, select_by_type, select_children, select_hierarchy_above, select_hierarchy_below, convert_to, select_face_path, select_vertex_perimeter, select_edge_perimeter, select_face_perimeter, select_border_edges, select_shell_border, select_uv_shell, select_uv_shell_border, select_uv_perimeter, select_uv_edge_loop, get_available_selection_types, get_selection_categories
- `class SelectionOrder`
  - methods: enable, disable, is_enabled, get, set_order

### `edit_utils/snap.py` — Snap tool — Switchboard slot wiring for the co-located ``snap.ui``.
- `class SnapSlots(ptk.LoggingMixin)`
  - methods: header_init, b000_init, b000, b001_init, b001, b002_init, b002

### `edit_utils/target_weld.py` — Target Weld — interactive drag-a-vertex-onto-another merge tool.
- `class TargetWeld(_TargetWeldInternal)`
  - methods: activate, project_points, pick_screen_point, weld_position, dash_segments, weld_pair, target_weld

### `env_utils/_env_utils.py` — blendertk environment / scene-library utilities — the engine behind the Reference Manager panel.
- `class EnvUtils(_EnvUtilsInternal)`
  - methods: find_blend_files, list_libraries, linked_blend_paths, is_blend_linked, link_blend_file, reload_library, remove_library, make_library_local, set_current_workspace, current_workspace, workspace_root, source_images_dir, texture_search_dirs, scenes_dir, workspace_scenes_dir, list_workspace_templates, workspace_template_rules, save_workspace_template, delete_workspace_template, create_workspace, promote_workspace, find_workspaces, open_scene, new_scene, scene_has_content, scene_has_unsaved_changes, scene_settings, apply_scene_settings, format_scene_name, save_scene_as, export_scene_as_obj, rename_scene_file, delete_scene_file, set_reference_display_mode, get_reference_display_mode

### `env_utils/blender_connection.py` — Launch a FRESH headless Blender to run a script / code string and capture its output — the
- `class BlenderConnection`
  - methods: find_blender, run_script, run_code, run_result

### `env_utils/fbx_utils.py` — FBX import / export helpers — the Blender counterpart of mayatk's ``env_utils.fbx_utils``
- `class FbxUtils(_FbxUtilsInternal)`
  - methods: run_export_preparers, reset_takes, apply_takes, apply_takes_from_node, export, import_fbx, scene_settings, export_selection_fbx

### `env_utils/handoff_export.py` — Blender-side selection + export hooks shared by the hand-off bridge engines.
- `class BlenderExportMixin`
  - methods: lightmap_search_dirs

### `env_utils/hierarchy_sync/_fbx_stage_worker.py` — Convert an FBX reference to a standalone ``.blend`` inside a FRESH headless Blender.
- `main() -> int`

### `env_utils/hierarchy_sync/_hierarchy_sync.py` — Hierarchy Sync core engine — mirror of mayatk's ``env_utils.hierarchy_sync._hierarchy_sync``.
- `class HierarchyMapBuilder`
  - methods: build_path_map
- `class HierarchySync(ptk.LoggingMixin)`
  - methods: analyze_hierarchies, create_stubs, quarantine_extras, fix_fuzzy_renames, fix_reparented, get_supported_formats, stage_reference_blend, build_path, delete_objects, should_keep_node_by_type
- `class ObjectSwapper(ptk.LoggingMixin)`
  - methods: pull_objects_from_reference

### `env_utils/hierarchy_sync/hierarchy_sync_slots.py` — Slots for the Hierarchy Sync panel -- Blender port of mayatk's ``env_utils.hierarchy_sync``.
- `class HierarchySyncController(ptk.LoggingMixin)`
  - methods: workspace, reference_path, analyze_hierarchies, repair_hierarchies, pull_objects, select_objects, populate_reference_tree, refresh_trees, is_path_ignored, clear_ignored_paths, log_diff_results, get_recent_reference_scenes, save_recent_reference_scene
- `class HierarchySyncSlots(ptk.LoggingMixin)`
  - methods: header_init, tree000_init, tree001_init, cmb_diff_options_init, cmb_pull_options_init, tb002_init, tb003_init, tb001, tb002, tb003, b003, b005, b006, b007, b008, b009, b011, b012, b013, b014, b015, b016, b018, b017, count_tree_items

### `env_utils/hierarchy_sync/scene_data_sidecar.py` — Scene-data sidecar manifest management — mirror of mayatk's
- `class SceneDataSidecar`
  - methods: base_stem, manifest_path_for, diff_report_path_for, find_legacy_manifest, ensure_base_name, migrate_legacy, rename, build_clean_path_set, expand_to_descendants, get_top_level, detect_reparenting, write_manifest, read_manifest, read_data, count_descendants, format_diff_report, clean_stale_diff, build_full_path_set, compare

### `env_utils/hierarchy_sync/tree_renderer.py` — Tree rendering, formatting, and selection management for the hierarchy sync UI — mirror of
- `class HierarchyTreeRenderer(ptk.LoggingMixin)`
  - methods: populate_current_scene_tree, populate_reference_tree, show_reference_placeholder, show_reference_error, populate_tree_with_hierarchy, apply_difference_formatting, clear_tree_colors, format_tree_differences, apply_ignore_styling, build_item_path, find_tree_item_by_name, get_selected_tree_items, get_selected_object_names

### `env_utils/hierarchy_sync/tree_utils.py` — Tree widget utilities for hierarchy sync UI operations — mirror of mayatk's
- `class TreePathMatcher(ptk.LoggingMixin, _TreePathMatcherInternal)`
  - methods: build_tree_index, find_path_matches, log_matching_debug, log_tree_index_debug, get_selected_object_names, get_selected_tree_items, find_tree_item_by_name, build_hierarchy_structure

### `env_utils/maya_bridge/_maya_bridge.py` — Maya bridge engine -- export the Blender selection and run a chosen import template in Maya.
- `class MayaBridge(BlenderExportMixin, ptk.ScriptLaunchBridge)`
  - methods: maya_path, headless_app_path, mayapy_from_maya_exe, params_defaults, render_context, list_templates, template_modes, list_template_modes

### `env_utils/maya_bridge/_scene_import.py` — Import a Maya scene (.ma/.mb) into Blender via a headless-Maya round-trip
- `class MayaSceneImport(ptk.LoggingMixin)`
  - methods: maya_path, mayapy_path, require_mayapy, render_script, convert, import_scene, blender_path, require_blender, render_bake_script, bake, bake_scene, bake_source, mayapy_from_maya_exe, scene_has_complex_animation, find_scenes

### `env_utils/maya_bridge/maya_bridge_slots.py` — Slots for the Maya bridge panel.
- `class MayaBridgeSlots(BlenderBridgeSlotsBase)`
  - methods: params_module, template_dir, make_bridge, list_template_modes, b000

### `env_utils/maya_bridge/parameters.py` — Registry of user-tunable Maya-bridge parameters exposed to the panel.
- `class Parameters`
  - methods: referenced_keys, defaults, render_context

### `env_utils/maya_bridge/templates/_bake_scene.py` — Import a converted intermediate (USD or FBX) headlessly and save it as a ``.blend`` so a
- `import_source(bpy)`
- `apply_manifest(engine, imported)`
- `tag_node_types(engine, imported)`
- `apply_instances(engine, imported)`
- `apply_visibility(engine, imported)`
- `apply_scene(engine, is_usd)`
- `main()`

### `env_utils/maya_bridge/templates/_import_scene.py` — Open a Maya scene headlessly (mayapy) and export it as FBX for a Blender import.
- `fbx_safe_materials(cmds)`
- `scene_node_types(cmds)`
- `scene_settings(cmds)`
- `write_manifest(entries, visibility, node_types, scene, path)`
- `main()`

### `env_utils/maya_bridge/templates/_import_scene_usd.py` — Open a Maya scene headlessly (mayapy) and export it as USD for a Blender import.
- `usd_safe_materials(cmds)`
- `export_usd(cmds)`
- `collect_materials(cmds)`
- `collect_instance_groups(cmds)`
- `scene_settings(cmds)`
- `write_manifest(cmds, materials=None, shading_groups=None)`
- `main()`

### `env_utils/maya_bridge/templates/_save_scene.py` — Import the bridged FBX into a headless ``mayapy`` and save it as a Maya scene.
- `import_usd(cmds)`
- `import_payload(cmds, mel, engine)`
- `restore_usd_locators(cmds, engine, new_nodes)`
- `import_fbx(cmds, mel, engine)`
- `restore_empty_groups(cmds, engine, new_nodes)`
- `rebuild_materials(engine, new_nodes)`
- `main()`

### `env_utils/maya_bridge/templates/import.py` — Import the bridged payload (FBX or USD) into Maya, with optional clean-slate and
- `import_fbx()`
- `import_usd()`
- `import_payload()`
- `restore_usd_locators(new_nodes)`
- `restore_empty_groups(new_nodes)`
- `rebuild_materials(new_nodes)`
- `main()`

### `env_utils/pm_doctor.py` — Shadow doctor for embedded-DCC installs (companion of package-manager.bat).
- `find_shadows()`
- `main()`

### `env_utils/reference_manager.py` — Reference Manager tool panel — Switchboard slot wiring for the co-located ``reference_manager.ui``.
- `class ReferenceManagerSlots(ptk.LoggingMixin)`
  - methods: header_init, txt000_init, cmb000_init, txt001_init, tbl000_init, new_workspace, mark_workspace, open_selected, save_scene, rename_selected, delete_selected, open_location_selected, toggle_reference_selected, unlink_import_selected, reload_all, make_local_all, remove_all

### `env_utils/scene_exporter/_scene_exporter.py` — Scene Exporter engine -- Blender port of mayatk's ``env_utils.scene_exporter``.
- `class SceneExporter(ptk.LoggingMixin)`
  - methods: confirm, perform_export, generate_export_path, format_export_name, generate_log_file_path, setup_file_logging, close_file_handlers, list_fbx_presets, fbx_preset_dir, fbx_preset_path, save_fbx_preset, delete_fbx_preset, load_fbx_export_preset, verify_fbx_preset

### `env_utils/scene_exporter/scene_exporter_slots.py` — Slots for the Scene Exporter panel -- Blender port of mayatk's ``SceneExporterSlots``.
- `class SceneExporterSlots(SceneExporter)`
  - methods: confirm, workspace, header_init, presets, cmb000_init, txt000_init, txt001_init, cmb001_init, cmb002_init, cmb007_init, cmb008_init, ignore_groups_init, cmb004_init, cmb005_init, b000, b010, b012, b006, b007, b008, save_output_dir, save_output_name

### `env_utils/scene_exporter/task_manager.py` — Blender-specific task/check methods for the Scene Exporter pipeline -- mirror of mayatk's
- `class TaskManager(TaskFactory, _TaskActionsMixin, _TaskChecksMixin)`
  - methods: objects, task_definitions, check_definitions, definitions, set_linear_unit, exclude_hdr, ignore_groups, reassign_duplicate_materials, convert_to_relative_paths, resolve_invalid_texture_paths, smart_bake, optimize_keys, tie_all_keyframes, snap_keys_to_frame, set_bake_animation_range, export_data_node, apply_declared_takes, check_framerate, check_referenced_objects, check_geometry_lod_suffix, check_duplicate_locator_names, check_root_default_transforms, check_hidden_geometry, check_overlapping_duplicate_mesh, check_objects_below_floor, check_duplicate_materials, convert_textures, optimize_textures, check_material_compatibility, check_texture_optimization, check_path_length, check_valid_paths, check_texture_file_size, check_untied_keyframes, check_floating_point_keys

### `env_utils/scene_state.py` — Read named sections of live-scene state for transport.
- `class SceneState`
  - methods: source, read

### `env_utils/script_output.py` — Blender script-output console — the blendertk analogue of mayatk's ``ScriptConsole``.
- `class ScriptConsole`
  - methods: instance, widget, show, hide, toggle, begin_capture, restore, is_open, teardown

### `env_utils/unity_bridge/_unity_bridge.py` — Unity bridge engine -- export the Blender selection into a Unity project's Assets/.
- `class UnityBridge(BlenderExportMixin, ptk.HandoffBridge)`
  - methods: list_template_modes, params_defaults, list_delivery_modes

### `env_utils/unity_bridge/parameters.py` — User-tunable parameters for the Blender->Unity bridge panel -- mirror of mayatk's
- `class Parameters`
  - methods: referenced_keys, defaults, render_context

### `env_utils/unity_bridge/unity_bridge_slots.py` — Slots for the Unity bridge panel -- mirror of mayatk's
- `class UnityBridgeSlots(BlenderBridgeSlotsBase)`
  - methods: params_module, template_dir, make_bridge, list_template_modes, default_output_dir, b000

### `env_utils/usd.py` — USD import / export helpers — the Blender counterpart of mayatk's ``env_utils.usd``
- `class UsdUtils(_UsdUtilsInternal)`
  - methods: is_usd_file, export, sampling_frame_range, fold_single_mesh_xforms, sanitize_prim_name, hidden_objects, export_prim_path, prim_path, mark_invisible, apply_visibility, activate_uv_map, import_scene, import_usd, bake_transform_caches, scene_settings, export_selection_usd

### `env_utils/webxr_preview.py` — Push the Blender selection to a live browser / WebXR preview.
- `class WebXrPreview(BlenderExportMixin, ptk.PreviewBridge)`

### `env_utils/workspace_editor.py` — blendertk Workspace Editor — the minimal take on Maya's File ▸ Project Window: one
- `class WorkspaceEditorSlots(ptk.LoggingMixin)`
  - methods: header_init, txt000_init, tbl000_init, add_rule, reset_row, remove_row, reset_rules, clear_rules, create_project, open_folder

### `light_utils/_light_utils.py` — Light utilities — the world-environment (HDRI) helpers behind the HDR Manager panel
- `class LightUtils(_LightUtilsInternal)`
  - methods: set_world_hdri, get_world_hdri, set_world_ray_visibility, get_world_ray_visibility, set_world_importance_resolution, get_world_importance_resolution, clear_world_hdri, world_emits, lights_from_geometry, remove_lights, set_world_environment, lights_from_records, scale_light_energy, set_emission_strength

### `light_utils/hdr_manager.py` — Blender world-HDRI environment manager.
- `class HdrManagerSlots(ptk.LoggingMixin)`
  - methods: header_init, cmb000_init, set_hdr_folder, hdr_map, hdr_map_visibility, cmb000, slider000, spn_intensity, spn_exposure, spn_resolution, spn_diffuse, spn_specular, add_hdr, open_sourceimages, clear_network, ctx_reveal_in_explorer

### `light_utils/lightmap_baker/lightmap_baker.py` — High-level lightmap baking workflow for Blender -> game engines (Unity-first).
- `class LightmapBaker(ptk.LoggingMixin)`
  - methods: resolution, samples, denoise, device, preset_store, from_preset, bake_separated, commit_lightmap, bake_atlas, atlas_plan, plan_sizes, pack_atlas, normalize_lightmap_paths, lightmap_dependencies, search_dirs, heal_lightmap_paths, relocate_lightmaps, repath_lightmaps, refresh_export_metadata, revert_lightmap, revert
- `class LightmapBakerSlots(ptk.LoggingMixin, ptk.HelpMixin)`
  - methods: header_init, cmb000_init, cmb000, cmb002_init, cmb_scope_init, cmb_resolution_init, txt_output_dir_init, txt000_init, b000, revert_to_source, open_output

### `light_utils/lightmap_baker/web_export.py` — Ship a committed lightmap bake in a web (GLB) deliverable.
- `class LightmapWebExport(ptk.LoggingMixin)`
  - methods: encode_for_web, wire_lightmaps, unwire_lightmaps, build_manifest, export_glb, wired_for_export

### `mat_utils/_mat_utils.py` — Material utilities — mirror of mayatk's ``MatUtils`` public names where the concepts align:
- `class MatUpdater(ptk.LoggingMixin, _MatUtilsInternal)`
  - methods: update_materials
- `class MatUtils(_MatUtilsInternal)`
  - methods: get_mats, create_mat, assign_mat, find_by_mat_id, find_unassigned, select_by_material, reload_textures, get_scene_mats, is_mat_assigned, get_mat_swatch_icon, get_texture_paths, get_texture_info, get_mat_info, format_mat_info_html, format_texture_info_html, find_materials_with_duplicate_textures, reassign_duplicate_materials, delete_unused_materials, image_texture_nodes, select_image_nodes, graph_materials, get_image_records, image_paths_scope, repath_image, to_project_relative, resolve_missing_textures, normalize_texture_paths, get_image_material_map, materials_for_textures, fix_color_spaces, set_texture_directory, plan_find_and_copy_textures, find_and_copy_textures, format_texture_paths_html, get_shader_templates, apply_shader_template, create_shader_template, serialize_material, restore_material, resolve_pbr_plan, create_pbr_material, create_pbr_materials, update_materials

### `mat_utils/arnold_bridge.py` — Arnold render-bridge management -- Blender port of mayatk's ``mat_utils.arnold_bridge``.
- `class ArnoldBridge(ptk.LoggingMixin)`
  - methods: add, remove, rebuild, get_bridge, has_bridge
- `class ArnoldBridgeSlots(ptk.LoggingMixin, ptk.HelpMixin)`
  - methods: header_init, cmb000_init

### `mat_utils/emissive_groups.py` — Emissive groups — mirror of mayatk's ``mat_utils.emissive_groups``.
- `class EmissiveGroups(_EmissiveGroupsInternal, ptk.LoggingMixin, ptk.HelpMixin)`
  - methods: add_group, remove_group, list_groups, select_group, set_default, make_weights_keyable, remove_keyable_weights, key_weight, create_export_curve_proxies, remove_export_curve_proxies, compact_slots, validate, bake_vertex_colors, bake_mask, refresh_export_metadata
- `class EmissiveGroupsSlots(ptk.LoggingMixin, ptk.HelpMixin)`
  - methods: header_init, txt000_init, tbl000_init, b000, b001, b002, b003, tb000_init, tb000, select_members, remove_group, weights_all_on, weights_all_off, make_weights_keyable, key_weights, remove_keyable_weights, compact_slots, republish_export

### `mat_utils/game_shader.py` — Game Shader — auto-build a Principled-BSDF material from a set of PBR textures.
- `class GameShader(ptk.LoggingMixin, _GameShaderInternal)`
  - methods: create_network
- `class GameShaderSlots(GameShader)`
  - methods: workspace_dir, source_images_dir, header_init, lbl_graph_material, mat_name, mat_prefix, mat_suffix, normal_map_type, output_extension, cmb002_init, opacity_mode, cmb003_init, txt000_init, txt002_init, b000

### `mat_utils/image_to_plane/_image_to_plane.py` — Map image files to textured planes in Blender — port of mayatk's ``mat_utils.image_to_plane``.
- `class ImageToPlane(ptk.LoggingMixin)`
  - methods: create, remove

### `mat_utils/image_to_plane/image_to_plane_slots.py` — Switchboard slots for the Image to Plane UI — port of mayatk's ``ImageToPlaneSlots``.
- `class ImageToPlaneSlots(ptk.LoggingMixin)`
  - methods: header_init, txt_suffix_init

### `mat_utils/marmoset_bridge/_marmoset_bridge.py` — Blender-side glue for the Marmoset Toolbag engine -- mirror of mayatk's
- `class MarmosetBridge(ptk.HandoffBridge, _MarmosetBridgeInternal)`
  - methods: toolbag_path, params_defaults, render_template, baked_texture_dir, build_bake_pairs_manifest

### `mat_utils/marmoset_bridge/_marmoset_engine.py` — Drive Marmoset Toolbag from the outside -- launch + templated automation.
- `class MarmosetEngine(ptk.Deliverer, ptk.LoggingMixin)`
  - methods: toolbag_path, toolbag_log_path, preflight, deliver, send, render_template, list_templates, template_modes, list_template_modes

### `mat_utils/marmoset_bridge/_toolbag_helpers.py` — Shared helpers for Marmoset Toolbag template scripts.
- `class ToolbagHelpers(_ToolbagHelpersInternal)`
  - methods: derive_per_run_log_path, begin_log, log, find_material, load_manifest, wire_materials_from_manifest, split_source_target, collect_mesh_objects, apply_sky_preset, frame_in_viewport

### `mat_utils/marmoset_bridge/marmoset_bridge_slots.py` — Slots for the Marmoset Toolbag bridge panel -- mirror of mayatk's
- `class MarmosetBridgeSlots(BlenderBridgeSlotsBase)`
  - methods: params_module, template_dir, make_bridge, list_template_modes, select_initial_template_index, b000

### `mat_utils/marmoset_bridge/marmoset_rpc/connection.py` — JSON-RPC client bound to the marmoset_rpc Toolbag plugin.
- `class MarmosetConnection(RpcClient, _MarmosetConnectionInternal)`

### `mat_utils/marmoset_bridge/marmoset_rpc/installer.py` — Install the marmoset_rpc plugin into Toolbag's user plugin folder.
- `class Installer(_InstallerInternal)`
  - methods: user_plugin_dir, is_installed, install, uninstall

### `mat_utils/marmoset_bridge/marmoset_rpc/job.py` — One-shot batch pipeline for the marmoset_rpc bridge.
- `class BatchJob`
  - methods: run_batch

### `mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/__init__.py` — Marmoset Toolbag RPC plugin -- entry point.
- `start_server(port=None, host=None)`
- `stop_server()`
- `is_running()`
- `autostart()`

### `mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/_rpc_core.py` — The in-application half of the RPC pair: registry + marshaller + server.
- `class OpRegistry(_OpRegistryInternal)`
  - methods: register, get, all_ops, describe
- `class MainThreadMarshaller(_MainThreadMarshallerInternal)`
  - methods: is_active, run
- `class RpcPlugin(object)`
  - methods: import_ops, port, is_hosted, is_running, address, start, stop, autostart, autostart_safely

### `mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/ops/scene_ops.py` — Scene-inspection ops.
- `summary()`
- `list_materials()`

### `mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/ops/system_ops.py` — Toolbag-specific system ops.
- `version()`

### `mat_utils/marmoset_bridge/parameters.py` — Registry of user-tunable Marmoset Toolbag parameters exposed to the bridge UI.
- `class Parameters`
  - methods: referenced_keys, defaults, render_context

### `mat_utils/marmoset_bridge/template_params.py` — Plain default values + literal formatting for Marmoset template tokens.
- `class TemplateParams`
  - methods: derive_auto_maps, derive_bake_values, python_literal, defaults, to_context

### `mat_utils/marmoset_bridge/templates/bake.py` — Bake source detail + surface maps onto the target meshes.
- `main()`

### `mat_utils/marmoset_bridge/templates/import.py` — Open the model in Toolbag and wire materials from the manifest.
- `main()`

### `mat_utils/marmoset_bridge/templates/lookdev.py` — Open the model in Toolbag, apply a Sky preset, and frame the model.
- `main()`

### `mat_utils/marmoset_bridge/toolbag_log.py` — Marmoset Toolbag log-file resolution, classification, and live tailing.
- `class ToolbagLog`
  - methods: resolve_toolbag_log_path, classify_log_line, dispatch_log_lines, start_toolbag_log_tail

### `mat_utils/mat_manifest.py` — Material-to-texture manifest for bridge workflows -- mirror of mayatk's ``mat_utils.mat_manifest``.
- `class MatManifest(ptk.HelpMixin)`
  - methods: build, restore

### `mat_utils/mat_updater.py` — Material Updater tool panel — Switchboard slot wiring for the co-located ``mat_updater.ui``.
- `class MatUpdaterSlots(MatUpdater)`
  - methods: header_init, selection_mode, move_to_folder, cmb001_init, b001

### `mat_utils/render_opacity/_render_opacity.py` — Render Opacity — Blender per-object opacity for engine-ready transparency (mirror of mayatk's
- `class RenderOpacity(ptk.LoggingMixin)`
  - methods: objects_with_visibility_keys, create, remove, key_fade, sync_visibility_from_opacity, ensure_connections, prepare_for_export

### `mat_utils/render_opacity/render_opacity_slots.py` — Switchboard slots for the Render Opacity panel (``render_opacity.ui``).
- `class RenderOpacitySlots(ptk.LoggingMixin)`
  - methods: header_init, tb000_init, tb000

### `mat_utils/shader_templates.py` — Shader Templates tool panel — Switchboard slot wiring for the co-located
- `class ShaderTemplatesSlots(ptk.LoggingMixin)`
  - methods: workspace_dir, source_images_dir, template_name, header_init, lbl_graph_material, lbl_open_templates_dir, cmb002_init, refresh_templates, rename_template_safe, lbl000, lbl001, lbl002, b000, b001, b002

### `mat_utils/substance_bridge/_substance_bridge.py` — Substance 3D Painter bridge -- export Blender selection and hand off to Painter.
- `class HighPolySet`
  - methods: collection, exists, members, define, clear
- `class SubstanceBridge(ptk.HandoffBridge)`
  - methods: painter_path, painter_log_path, instances, find_live_managed, send, ensure_rpc_plugin, high_poly_path_for, mesh_map_files, list_templates, parse_template, list_template_modes, resolve_painter_log_path

### `mat_utils/substance_bridge/connection.py` — Substance 3D Painter connection module.
- `class SubstanceConnection(ptk.LoggingMixin)`
  - methods: open, close, is_alive, attach, find_painter_exe, default_log_path

### `mat_utils/substance_bridge/parameters.py` — Registry of user-tunable Substance Painter parameters exposed to the bridge UI.
- `class Parameters`
  - methods: referenced_keys, defaults, affix_parts, render_cli_context, render_js_context

### `mat_utils/substance_bridge/substance_bridge_slots.py` — Slots for the Substance Painter bridge panel -- mirror of mayatk's
- `class SubstanceBridgeSlots(BlenderBridgeSlotsBase)`
  - methods: live_param_tooltip_blocks, set_bake_source_from_selection, select_bake_source, clear_bake_source, params_module, template_dir, make_bridge, list_template_modes, select_initial_template_index, b000

### `mat_utils/substance_bridge/substance_rpc/client.py` — HTTP RPC client for the Painter-side ``substance_rpc`` plugin.
- `class PainterRpcClient(RpcClient)`
  - methods: wait_until_ready, invoke, eval_js, eval_py, reload_mesh, reload_status, project_info

### `mat_utils/substance_bridge/substance_rpc/installer.py` — Install the substance_rpc plugin into Painter's user plugin folder.
- `class Installer(_InstallerInternal)`
  - methods: user_plugin_dir, is_installed, is_current, install, uninstall

### `mat_utils/substance_bridge/substance_rpc/plugin_src/substance_rpc/__init__.py` — Substance 3D Painter RPC plugin -- entry point.
- `start_server(port=None, host=None)`
- `stop_server()`
- `is_running()`
- `autostart()`
- `start_plugin()`
- `close_plugin()`

### `mat_utils/substance_bridge/substance_rpc/plugin_src/substance_rpc/_rpc_core.py` — The in-application half of the RPC pair: registry + marshaller + server.
- `class OpRegistry(_OpRegistryInternal)`
  - methods: register, get, all_ops, describe
- `class MainThreadMarshaller(_MainThreadMarshallerInternal)`
  - methods: is_active, run
- `class RpcPlugin(object)`
  - methods: import_ops, port, is_hosted, is_running, address, start, stop, autostart, autostart_safely

### `mat_utils/substance_bridge/substance_rpc/plugin_src/substance_rpc/ops/project_ops.py` — Project-level ops: inspect the open project and reload its mesh.
- `project_info()`
- `mesh_reload(mesh_path='', preserve_strokes=True, import_cameras=False)`
- `mesh_reload_status()`

### `mat_utils/substance_bridge/substance_rpc/plugin_src/substance_rpc/ops/setup_ops.py` — Project-setup ops: resolution, the baking high poly, and mesh maps.
- `teardown()`
- `set_resolution(size=0)`
- `set_high_poly(mesh_path='')`
- `apply_mesh_maps(manifest_path='')`
- `pending_setup()`

### `mat_utils/substance_bridge/substance_rpc/plugin_src/substance_rpc/ops/system_ops.py` — Painter-specific system ops: version reporting and script evaluation.
- `version()`
- `eval_python(script='')`
- `js_evaluate(script='')`

### `mat_utils/texture_baker.py` — Bake an object's shaded surface (material under scene lighting) to a texture — the Blender
- `class TextureBaker(ptk.LoggingMixin)`
  - methods: bake, denoise_image, resolve_meshes, texture_set_stem, default_output_dir

### `mat_utils/texture_path_editor.py` — Texture Path Editor tool panel — Switchboard slot wiring for the co-located
- `class TexturePathEditorSlots(ptk.LoggingMixin)`
  - methods: header_init, tb_set_texture_directory_init, tb_normalize_paths_init, tb_resolve_missing_textures_init, tbl000_init, setup_formatting, open_source_images, reload_scene_textures, tb_set_texture_directory, tb_find_and_copy_textures, tb_normalize_paths, make_paths_absolute, tb_resolve_missing_textures, select_textures_for_objects, select_broken_paths, select_absolute_paths, row_browse_for_file, select_material, select_file_node, row_show_in_hypershade, delete_file_node, handle_cell_edit, refresh_texture_table, cleanup_scene_callbacks

### `node_utils/_node_utils.py` — Node / datablock utilities — instancing via shared object data.
- `class NodeUtils(_NodeUtilsInternal)`
  - methods: get_instances, replace_with_instances, uninstance, get_parent, get_children, get_shape, reparent

### `node_utils/attributes/channels/_channels.py` — Channels — Blender attribute query / mutation logic.
- `class Channels`
  - methods: is_pinned, single_object_mode, pin_targets, get_selected_nodes, collect_channels, get_channel_value, format_value, parse_value, is_locked, toggle_lock, set_lock, classify_connection, build_table_data, set_channel_value, reset_to_default, toggle_key_at_current_time, break_connections, set_mute, set_breakdown_key, select_connections, create_attribute, delete_attributes, rename_attribute, rename_node, copy_values, paste_values, freeze_transforms, unfreeze_transforms, has_unfreeze_info

### `node_utils/attributes/channels/channels_slots.py` — UI slots for the Channels panel (``channels.ui``).
- `class ChannelsSlots`
  - methods: apply_launch_config, cmb000_init, cmb000, header_init, show_create_menu, tbl000_init

### `node_utils/data_nodes.py` — Scene-wide export-metadata carrier — mirror of mayatk's ``node_utils.data_nodes``.
- `class DataNodes`
  - methods: get_internal_node, ensure_internal, set_internal_string, get_internal_string, get_export_node, ensure_export, set_export_string, get_export_string, set_export_json, dump, format_dump

### `nurbs_utils/_nurbs_utils.py` — Shared curve helpers — Blender mirror of mayatk's ``nurbs_utils.NurbsUtils`` namespace.
- `class NurbsUtils(ptk.LoggingMixin)`
  - methods: add_spline, create_curve, duplicate_curve, create_plane, curve_to_mesh, straighten_curve, bend_curve, curl_curve, scale_curvature, rebuild_curve, extend_curve

### `nurbs_utils/curve_to_tube.py` — Curve to Tube tool — Blender port of mayatk's ``nurbs_utils.curve_to_tube``.
- `class CurveToTube(ptk.LoggingMixin)`
  - methods: create
- `class CurveToTubeSlots(ptk.LoggingMixin)`
  - methods: header_init, b001, perform_operation

### `nurbs_utils/image_tracer.py` — Image Tracer tool — Blender port of mayatk's ``nurbs_utils.image_tracer``.
- `class ImageTracer(ptk.LoggingMixin)`
  - methods: trace_curves, create_mesh, create_negative_space_mesh, project_on_plane
- `class ImageTracerSlots(ptk.LoggingMixin)`
  - methods: header_init, txt000_init, browse_image, chk000, b002, b003, b004, b005

### `rig_utils/_rig_utils.py` — Shared procedural-rig primitives — Blender port of mayatk's ``rig_utils.RigUtils``.
- `class RigUtils`
  - methods: resolve_object, create_locator, create_group, parent_keep_transform, create_armature, add_bone_chain, add_bone, get_bone_chain_from_root, invert_bone_chain, add_bone_constraint, add_spline_ik, bind_armature, apply_falloff_weights, copy_location, copy_rotation, damped_track, track_to, child_of, refresh_drivers, add_distance_driver, add_transform_driver, add_prop_var, add_transform_var, ensure_custom_prop, remove_driver, lock_channels

### `rig_utils/controls.py` — Rig control-shape factory — Blender port of mayatk's ``rig_utils.controls.Controls``.
- `class ControlNodes`
- `class Controls(_ControlsInternal)`
  - methods: register_preset, shapes, create

### `rig_utils/shadow_rig.py` — Shadow Rig — engine + Switchboard slot wiring for the co-located ``shadow_rig.ui``.
- `class ShadowRig(ptk.LoggingMixin)`
  - methods: create_contact_locator, get_or_create_shadow_source, create_shadow_plane, create_silhouette_texture, create_material, setup_drivers, bake, find_shadow_planes, bake_planes, delete, delete_rigs, refresh_export_metadata, create
- `class ShadowRigSlots(ptk.LoggingMixin)`
  - methods: header_init, b001, b002, perform_operation

### `rig_utils/telescope_rig.py` — Telescope Rig — engine + Switchboard slot wiring for the co-located ``telescope_rig.ui``.
- `class TelescopeRigBundle`
  - methods: to_json, from_json
- `class TelescopeRig(ptk.LoggingMixin)`
  - methods: setup_telescope_rig, scene_bundles, find_bundles, teardown
- `class TelescopeRigSlots(ptk.LoggingMixin)`
  - methods: header_init, build_rig, remove_rig

### `rig_utils/tube_path.py` — Tube-mesh centerline extraction — Blender port of mayatk's ``rig_utils.tube_rig.TubePath``.
- `class TubePath`
  - methods: get_centerline, get_selected_edges, get_centerline_using_edges

### `rig_utils/tube_rig.py` — Tube Rig — Blender port of mayatk's ``rig_utils.tube_rig`` (the engine + strategies + panel).
- `class TubeRigBundle`
- `class TubeStrategy(ABC)`
  - methods: register, defaults, resolve, build
- `class SplineIKStrategy(TubeStrategy)`
  - methods: build
- `class AnchorStrategy(TubeStrategy)`
  - methods: build
- `class FKChainStrategy(TubeStrategy)`
  - methods: build
- `class TubeRig(ptk.LoggingMixin, _TubeRigInternal)`
  - methods: collection, resolve_centerline, create_root, create_armature, create_joint_chain, add_twist, attach_spline_rig, build_curve, make_control, hook_curve_controls, constrain_end_with_falloff, build
- `class TubeRigSlots(ptk.LoggingMixin)`
  - methods: txt000_init, header_init, b000, b001, b002, b003, b004

### `rig_utils/wheel_rig.py` — Wheel Rig — engine + Switchboard slot wiring for the co-located ``wheel_rig.ui``.
- `class WheelRig(ptk.LoggingMixin)`
  - methods: rig_name, get_drivers, delete_drivers, rig_rotation
- `class WheelRigSlots(ptk.LoggingMixin)`
  - methods: header_init, rig_name, movement_axis, rotation_axis, resolve_selection, set_wheel_height, txt000_init, s000_init, update_rig_name_placeholder, cleanup, wheel_rig, b000

### `ui_utils/_ui_utils.py` — UI utilities — opening Blender editors (the analogue of Maya's editor-window mel commands).
- `class UiUtils(_UiUtilsInternal)`
  - methods: get_editor_types, open_editor, reveal_in_outliner, main_window, find_editor, close_area, close_editor, dock_editor, toggle_editor, toggle_fullscreen_area, toggle_window_bars, menu_exists, dispatch_log_link, call_native_menu, popup_message

### `ui_utils/blender_bridge_slots_base.py` — Blender-flavored :class:`BridgeSlotsBase` -- adds Blender-side defaults.
- `class BlenderBridgeSlotsBase(BridgeSlotsBase)`
  - methods: default_output_dir, resolve_scope_objects

### `ui_utils/blender_native_menus.py` — Symbolic-name -> Blender native-menu resolution + Qt wrapping for the both-button chord menu.
- `class BlenderNativeMenus(ptk.LoggingMixin)`
  - methods: names, resolve, get_menu

### `ui_utils/blender_ui_handler.py`
- `class BlenderUiHandler(UiHandler)`
  - methods: instance, can_resolve, show, default_persistence

### `ui_utils/blender_window.py` — Native-window (win32/GHOST) helpers for hosting Qt widgets around a Blender window.
- `class BlenderWindow`
  - methods: process_ghost_hwnds, window_hwnd, is_window, client_origin, client_size, region_client_rect, set_clip_children, move_child, keyboard_focus, cursor_over, set_keyboard_focus, set_owner

### `ui_utils/calculator.py` — Calculator tool panel — Switchboard slot wiring for the co-located ``calculator.ui``.
- `class CalculatorController`
  - methods: calculate, convert_unit, get_fps_value, get_current_time, frames_to_sec, sec_to_frames
- `class CalculatorSlots(ptk.LoggingMixin)`
  - methods: header_init, on_input, on_clear, on_backspace, on_equal, on_convert_units, get_fps, get_current_time, frames_to_sec, sec_to_frames

### `ui_utils/cancel_provider.py` — Blender's answers to uitk's cancellation contract (mayatk parity twin).
- `class BlenderCancelProvider(CancelProvider)`
  - methods: begin, tick, end

### `ui_utils/menu_harvest.py` — Harvest a native Blender menu into a live ``QMenu`` — the Blender half of Maya's wrap.
- `class MenuHarvest(_MenuHarvestInternal)`
  - methods: harvest_menu, invoke_operator, refill_qmenu

### `ui_utils/node_icons.py` — Resolve per-object-type icons for Blender objects (mirror of ``mtk.NodeIcons``).
- `class NodeIcons`
  - methods: icon_name_for_type, icon_name_for_node, get_icon, get_pixmap

### `ui_utils/qt_dock.py` — Dock any Qt widget into a native Blender area — a true child window, not an overlay.
- `class QtDock`
  - methods: supported, docked, widget, area, content_region, dock, undock, teardown

### `ui_utils/style_setter/_style_setter.py` — Match Blender's app UI chrome to another DCC's look using Blender's NATIVE theme-preset system.
- `class StyleSetter(_StyleSetterInternal)`
  - methods: list_styles, user_preset_dir, user_preset_path, is_installed, install, list_templates, apply_template, apply_theme_preset, set_style

### `ui_utils/ui_state.py` — Persist Blender's per-session UI visibility state across sessions (``btk.UiState``).
- `class UiState(_UiStateInternal)`
  - methods: state_path, load, save, clear, snapshot_spaces, snapshot_workspace, apply_spaces, close_hidden, apply_workspace, install, uninstall

### `uv_utils/_auto_unwrap.py` — External auto-unwrap round-trip: OBJ out, engine, OBJ back, UVs transferred.
- `class AutoUnwrapResult`

### `uv_utils/_uv_utils.py` — UV utilities — UV-coordinate translation and UV-set cleanup (mirror of mayatk's ``UvUtils``
- `class UvUtils(_UvUtilsInternal)`
  - methods: calculate_uv_padding, move_uvs, get_uv_bounds, get_neighbor_shell_bounds, transfer_uvs_to_similar, scale_uvs, transform_uvs, mirror_uvs, pin_uvs, get_texel_density, set_texel_density, delete_extra_uv_sets, cleanup_uv_sets, find_lightmap_uv_set, export_uv_layout, create_lightmap_uvs, auto_unwrap, transfer_uvs, get_uv_coords, set_uv_coords, get_similar_uv_shells, stack_uv_shells, straighten_uv_shells, derive_auto_seams, distribute_uv_shells, straighten_uvs, align_uvs, gather_uv_shells, gather_to_udim, orient_uv_shells, randomize_uv_shells

### `uv_utils/rizom_bridge/_rizom_bridge.py` — RizomUV bridge engine — Blender mirror of mayatk's ``RizomUVBridge``.
- `class RizomUVBridge(ptk.LoggingMixin, _RizomUVBridgeInternal)`
  - methods: rizom_path, rizom_version, export_path, script_path, build_send_script, send, process_with_rizomuv, expand_by_materials

### `uv_utils/rizom_bridge/parameters.py` — Registry of user-tunable RizomUV parameters exposed to the bridge UI.
- `class Parameters`
  - methods: expand_includes, preset_min_version, referenced_keys, defaults, derived_values, render_context, strip_unsupported

### `uv_utils/rizom_bridge/rizom_bridge_slots.py` — Slots for the RizomUV bridge panel.
- `class RizomBridgeSlots(BlenderBridgeSlotsBase)`
  - methods: params_module, template_dir, make_bridge, list_template_modes, b000, open_uv_editor

### `uv_utils/shell_xform.py` — Dedicated UV shell-transform panel (Blender).
- `class ShellXformSlots(ptk.LoggingMixin)`
  - methods: header_init, cmb_move_scope_init, b023, b024, b025, b026, gather_to_udim, b034, b035, b036, b037, s041, tb005_init, tb005, tb006_init, tb006, tb008_init, tb008, align_u_min, align_u_avg, align_u_max, align_v_min, align_v_avg, align_v_max, linear_align, orient_shells, orient_edges, gather_shells, randomize_shells, open_uv_editor

### `uv_utils/texture_transfer.py` — Transfer a mesh's textures from one UV layout to another -- no rays, no bake.
- `class TextureTransfer(ptk.LoggingMixin, _TextureTransferInternal)`
  - methods: transfer, default_output_dir, output_base_dir, resolve_output_dir, assign_results, topology_matches, positions_match, auto_source_uv_set, correspondence, face_materials, material_maps, material_constant, pair_by_name

### `xform_utils/_xform_utils.py` — Transform utilities — object-level transform ops (world bbox, freeze, drop-to-grid,
- `class XformUtils(_XformUtilsInternal)`
  - methods: get_world_bbox, freeze_transforms, restore_transforms, has_stored_transforms, store_transforms, get_stored_transforms, scale_connected_edges, drop_to_grid, center_pivot, transfer_pivot, get_pivot_modes, match_scale, move_to, get_bounding_box, get_center_point, get_operation_axis_matrix, get_distance, order_by_distance, aim_object_at_point, restore_original_axes, get_pivot_options

### `xform_utils/matrices.py` — Matrix utilities — the Blender counterpart of mayatk's ``xform_utils.matrices``
- `class Matrices`
  - methods: get_matrix, set_matrix, local_matrix, to_matrix, identity, from_srt, compose, decompose, extract_translation, inverse, mult, world_to_local, local_to_world, is_identity
