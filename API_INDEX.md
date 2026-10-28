# blendertk — API Index

_Auto-generated. Do not edit by hand. Compact symbol index — grep this for a name; for full signatures/docs, slice [API_REGISTRY.md](API_REGISTRY.md) (never Read it whole)._

### `__init__.py`
- constants: DEFAULT_INCLUDE, MOVED_NAMES

### `anim_utils/_anim_utils.py` — Animation utilities — key-timing math over ``fcurve.keyframe_points`` (mirror of mayatk's
- `class AnimUtils(_AnimUtilsInternal)`
  - methods: normalize_optimize_level, resolve_optimize_level, key_arrays, key_times, key_interpolations, get_key_curves, shift_key_values, window_indices, shift_keys_in_window, remap_keys_in_window, step_last_key_in_window, evaluable_override, remove_fcurve, get_fcurves, get_animated_extent, has_nla_or_data_animation, scene_has_animation, get_current_frame, set_current_frame, shift_keys, move_keys_to_frame, adjust_key_spacing, align_selected_keyframes, set_visibility_keys, add_intermediate_keys, remove_intermediate_keys, select_keys, invert_keys, snap_keys, set_interpolation, set_stepped, step_visibility_keys, replay_visibility, delete_keys, fit_playback_range, copy_keys, paste_keys, transfer_keyframes, reduce_to_extremes, get_redundant_flat_keys, euler_filter, simplify_curve, optimize_keys, repair_corrupted_curves, tie_keyframes, bake_keys, bake_blend_shapes, get_animation_info, format_animation_info_csv, format_animation_info_html, configure_render_output, clear_key_selection, get_selected_key_times, get_timeline_selection, create_preview_layer, remove_preview_layer, interpolation_value

### `anim_utils/blendshape_animator/_blendshape_animator.py` — Main workflow facade for shape-key morph creation, editing, and export — mirror of mayatk's
- `class BlendshapeAnimator(pythontk.LoggingMixin)`
  - methods: base_mesh, target_mesh, blendshape, create, edit_weight_based, edit_frame_based, edit_apply_tweens, has_setup, selected_base, tween_topology, apply_tweens, delete_tweens, select_tweens, basic_workflow, apply_all_edits, finalize_for_export, from_existing, recover_animation, diagnose_topology_issues, cleanup_topology_mismatches, remove_target_for_export

### `anim_utils/blendshape_animator/applicator.py` — Applies tween mesh edits back to the master shape key — mirror of mayatk's
- `class ApplyStatus(Enum)`
- `class Applicator(pythontk.LoggingMixin)`
  - methods: validate_topology, apply_tweens

### `anim_utils/blendshape_animator/creator.py` — Creates in-between (tween) target meshes for sculpting a custom morph curve — mirror of
- `class Creator(pythontk.LoggingMixin, _CreatorInternal)`
  - methods: create_weight_based_tweens, create_frame_based_tween, tag_tween_mesh, get_existing_weights, find_nearby_weight

### `anim_utils/blendshape_animator/keyframes.py` — Master shape-key value keyframe animation — mirror of mayatk's
- `class Keyframes(pythontk.LoggingMixin)`
  - methods: key_id, key_block, create_keyframes, test_morph, get_frame_range, preserve_sibling_values

### `anim_utils/blendshape_animator/target.py` — Tween mesh wrappers and registry — mirror of mayatk's
- constants: TWEEN_GROUP_NAME
- `class Target`
  - methods: mesh, weight, key_block_name, base_mesh_name, target_frame, update_references
- `class Targets(pythontk.LoggingMixin)`
  - methods: find_all_targets, group_by_weight, update_all_references

### `anim_utils/blendshape_animator/validator.py` — Mesh + shape-key setup validation — mirror of mayatk's
- `class Validator(pythontk.LoggingMixin)`
  - methods: validate_meshes, validate_shape_setup

### `anim_utils/key_stash/_key_stash.py` — Key Stash — park keyframes outside the working animation, retrieve later (Blender).
- `class KeyStash(pythontk.core_utils.engines.key_stash.key_stash_model.KeyStash, _KeyStashInternal)`
  - methods: active, watch_undo, rescale_to_fps, reconcile, merge_carrier, discard_carrier, stash, retrieve, drop, stash_from, select_clip_objects, preview, end_preview

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
  - methods: store_cls, remove_callbacks, save, load, record_changed
- `class BlenderShotStore(pythontk.ShotStore, _BlenderShotStoreInternal)`
  - methods: is_transform_path, fcurve_for_key, from_dict, member_nodes, curve_key, follow_renames, edit_serial, scene_edit, active, rescale_to_fps, has_animation, detect_regions, assess, publish_export_view, export_transfer, apply_transfer, transfer_out, transfer_in, merge_carrier, discard_carrier, iter_action_fcurves, collect_transform_segments, collect_selected_key_entries

### `anim_utils/shots/shot_manifest/_shot_manifest.py` — Blender Shot Manifest adapter — the DCC layer over pythontk's manifest engine.
- `class BlenderShotManifest(ShotManifest, _ShotManifestInternal)`
  - methods: apply_behaviors, rewire_audio, from_csv

### `anim_utils/shots/shot_manifest/behaviors/_behaviors.py` — Behaviors — Blender appliers over the engine's pure keying-recipe core.
- `class Behaviors(pythontk.core_utils.engines.shots.manifest.behaviors.Behaviors, _BehaviorsInternal)`
  - methods: apply_behavior, verify_behavior, apply_audio_clip, compute_duration, apply_to_shots

### `anim_utils/shots/shot_manifest/manifest_host.py` — What the Shot Manifest panel needs from Blender: its store, engine, detection,
- `class ManifestHost`
  - methods: store_cls, manifest_cls, detection, behaviors_cls, range_resolver_cls, available, current_frame, find_nodes, reveal, leaf_name, object_icon

### `anim_utils/shots/shot_manifest/range_resolver.py` — Range resolution for the Shot Manifest build pipeline (Blender-bound facade).
- `class RangeResolver`
  - methods: resolve_ranges

### `anim_utils/shots/shot_sequencer/_shot_sequencer.py` — Blender shot sequencer engine — ripple editing + key motion over the shared planner.
- `class ShotSequencer(_ShotSequencerInternal, pythontk.ShotSequencer)`
  - methods: slide_shot, split_shot, reconcile_all_shots, collect_object_segments, detect_shots, move_curve_keys, recreate_curve_keys, move_attribute_keys, copy_attribute_keys, scale_object_keys, scale_shot_keys

### `anim_utils/shots/shot_sequencer/graph_sync.py` — Graph editor glue: the shot sequencer's curves in uitk's graph editor (Blender).
- `class GraphSync`
  - methods: detach, sync, outline, key_targets, on_key_menu, on_keys_edited, on_tangents_edited, on_keys_deleted, on_keys_inserted, on_tangent_type, on_tangents_broken, on_infinity, on_key_selection

### `anim_utils/shots/shot_sequencer/segment_collector.py` — Segment collection and attribute extraction for the shot sequencer (Blender).
- constants: KEY_PROXIMITY_EPS
- `class SegmentCollector`
  - methods: label_for, attr_label, abbreviate_attrs, collect_segments, active_object_set, extract_attributes, summary_times, build_curve_preview

### `anim_utils/shots/shot_sequencer/sequencer_host.py` — What the Shot Sequencer panel needs from Blender: :class:`SequencerHost`.
- `class SequencerHost`
  - methods: current_time, set_current_time, playback_range, set_playback_range, watch_scene, newly_keyed_members, step_undo, play_controller, bind_timeline_audio, timeline_audio_wav, scene_fps, segment_collector, existing, leaf_name, node_icon, select_objects, claimed_key_times, motion_segments, attribute_curves, audio_segments, audio_track_icon, delete_span_keys, stash_keys, curves_for_attr, curves_for_attrs, curve_id, object_curves, first_key, keyed_at, any_key_at, scale_attribute_keys, commit_curve_moves, delete_keys_at, moving_audio_track, undo_chunk, tangent_from_handle, tangent_type_edit, interpolation_edit, tangent_lock_edit, handle_drag_edit, edit_key_tangents, target_curves, simplify_keys, thin_keys, snap_keys, euler_filter_keys, invert_keys, align_keys, copy_keys, paste_keys, graph_sync_cls, object_exists, full_name, undo_disabled, mirror_selection, select_channels, select_keys, open_attribute_spreadsheet, set_dragged_handles

### `anim_utils/smart_bake/_smart_bake.py` — Smart Bake engine — mirror of mayatk's ``anim_utils.smart_bake._smart_bake`` at the
- `class BakeAnalysis`
  - methods: requires_bake
- `class BakeResult`
  - methods: skip, declined, baked_count, success, describe
- `class SmartBake(_SmartBakeInternal)`
  - methods: analyze, get_time_range, bake, execute, list_sessions, restore, session, run

### `anim_utils/smart_bake/bake_session.py` — Persistence and restore engine for SmartBake's nondestructive manifest — mirror of mayatk's
- `class BakeSessionStore(_BakeSessionStoreInternal)`
  - methods: load, save, push, peek, pop, list_ids, new_session_id, node_ref, resolve_ref, constraint_ref, resolve_constraint, driver_ref, resolve_driver, snapshot_blend_shape_driver, snapshot_blend_shape_action, restore_session
- `class RestoreResult`

### `anim_utils/stagger_keys.py` — Dedicated stagger-keys module to keep AnimUtils lean and testable (mirror of mayatk's
- `class StaggerKeys(_StaggerKeysInternal)`
  - methods: stagger_keys

### `audio_utils/_audio_utils.py` — Scene-wide audio-clip utilities over Blender's Video Sequence Editor (VSE).
- constants: DEFAULT_CHANNEL, MAX_CHANNEL_SEARCH
- `class AudioUtils(pythontk.LoggingMixin)`
  - methods: ensure_sequence_editor, get_sequence_editor, list_clips, read_all_events, get_clip, add_clip, remove_clip, remove_all_clips, rename_clip, replace_clip, move_clip, trim_clip, get_fps, clips_in_range, shift_clips_in_range, cached_waveform, clear_waveform_cache, sync_scene_range, bake_events, track_files, export_record

### `audio_utils/audio_clips.py` — Audio Clips: the Audio Clips panel's engine over Blender's Video Sequence Editor.
- `class AudioClips(pythontk.LoggingMixin)`
  - methods: list_tracks, find_track, get_trim, add_tracks, rename_track, replace_track, remove_track, remove, key_track, trim_track, sync_scene_range, reveal, watch

### `audio_utils/segments.py` — Consumer-facing audio-segment discovery for the sequencer + manifest (Blender).
- `class AudioSegment(_AudioSegmentInternal)`
  - methods: is_audio, collect_all_segments, collect_segments_for_track

### `cam_utils/_cam_utils.py` — Camera utilities — clip-plane adjustment (mirror of mayatk's ``cam_utils``) plus interactive
- `class CamUtils(_CamUtilsInternal)`
  - methods: adjust_camera_clipping, get_view_state, set_view_state, fit_camera_clipping, navigate_view, get_cameras, create_camera, look_through, switch_viewport_camera, align_view_to_selection

### `cam_utils/camera_visibility.py` — Per-camera visibility sets — rolled infrastructure for Maya's camera-sets isolate
- `class CameraVisibility`
  - methods: set_exclusive, set_hidden, remove_from_exclusive, remove_from_hidden, remove_all, remove_all_for_all, get_sets, apply, restore, enable_auto, disable_auto

### `core_utils/_core_utils.py` — Core blendertk utilities — DCC-environment info + cross-cutting decorators.
- `class CoreUtils(pythontk.CoreUtils, _CoreUtilsInternal)`
  - methods: strip_dup_suffix, all_ids, suspended_refresh, undo_chunk, undo_push, visible_override, undoable, undo_checkpoint, get_env_info, ensure_packages, ensure_image_deps, user_config_path, get_recent_files, get_recent_autosave, get_scene_info, format_scene_info_html, analyze_scene, cleanup_scene, selected, active_view_layer, leaf_name, selected_objects, select, active_object, ensure_component_mode, ensure_object_mode, preserved_selection, reorder_objects, get_areas, tag_redraw, get_view3d_context, window_context_override, edit_mode

### `core_utils/auto_instancer/_auto_instancer.py` — Scene auto-instancer: convert geometrically identical meshes to instances.
- `class InstanceCandidate`
  - methods: obj, exists
- `class InstanceGroup`
- `class AutoInstancer(pythontk.LoggingMixin, _AutoInstancerInternal)`
  - methods: default_summary, format_summary, tolerance, scale_tolerance, require_same_material, check_uvs, combine_assemblies, search_radius_mult, verbose, run, find_instance_groups, run_once

### `core_utils/auto_instancer/assembly_reconstructor.py` — Logic for separating and reassembling mesh assemblies (bpy adapter).
- constants: ASSEMBLY_TAG_ATTR, CANONICAL_BAKE_PREFIX
- `class AssemblyReconstructor(_AssemblyReconstructorInternal)`
  - methods: separate_combined_meshes, cleanup_empty_sources, cleanup_empty_assembly_groups, center_transform_on_geometry, canonicalize_transform, canonicalize_leaf_meshes, reassemble_assemblies, combine_reassembled_assemblies

### `core_utils/auto_instancer/geometry_matcher.py` — Geometry analysis and matching logic for AutoInstancer (bpy adapter).
- `class GeometryMatcher(_GeometryMatcherInternal)`
  - methods: clear_cache, invalidate, quantize, get_pca_basis, get_mesh_signature, get_hierarchy_signature, are_meshes_identical, are_meshes_identical_with_transform, are_hierarchies_identical

### `core_utils/auto_instancer/instancing_strategy.py` — AutoInstancer's instancing strategy: the Blender binding of the ptk engine.
- `class InstancingStrategy(pythontk.InstancingStrategy)`

### `core_utils/diagnostics/mesh_diag.py` — Mesh diagnostics — the Blender counterpart of mayatk's ``core_utils.diagnostics.mesh_diag``
- `class MeshDiagnostics(_MeshDiagnosticsInternal)`
  - methods: find_problem_geometry

### `core_utils/diagnostics/scene_audit.py` — Scene audit -- the Blender port of mayatk's ``core_utils.diagnostics.scene_audit``.
- `class SceneInfoSection`
  - methods: normalize
- `class SceneAnalyzer`
  - methods: format_audit_html, format_audit_text

### `core_utils/diagnostics/transform_diag.py` — Transform diagnostics — the Blender counterpart of mayatk's
- `class TransformDiagnostics(_TransformDiagnosticsInternal)`
  - methods: get_non_orthogonal, fix_non_orthogonal_axes

### `core_utils/diagnostics/uv_diag.py` — UV diagnostics -- the Blender counterpart of mayatk's
- `class UvDiagnostics(object)`
  - methods: is_bakeable_lightmap

### `core_utils/preview.py` — Live-preview driver for the tentacle Blender tool panels — the Blender analogue of
- `class Preview`
  - methods: is_enabled, refresh, enable, disable, commit, apply_result_selection

### `core_utils/script_job_manager.py` — Centralized Blender event-subscription manager — the Blender counterpart of mayatk's
- `class ScriptJobManager`
  - methods: instance, reset, subscribe, unsubscribe, unsubscribe_all, connect_cleanup, suppress, resume, suppressed, status, print_status, teardown

### `core_utils/units.py` — Blender's lengths and angles, converted by the kind of value that holds them.
- `class Units(pythontk.HelpMixin)`
  - methods: working_as, working, metres_per, convert, angle

### `display_utils/_display_utils.py` — Display utilities — the exploded-view toggle (mirror of mayatk's
- `class DisplayUtils(_DisplayUtilsInternal)`
  - methods: is_exploded, explode_view, unexplode_view, unexplode_all, get_visible_geometry, set_viewport_overlay

### `display_utils/color_id.py` — Color ID engine: color-code scene objects across channels.
- `class ColorId`
  - methods: assign_id_material, set_object_color, set_vertex_color, set_outliner_color, get_outliner_color, reset_outliner_colors, collection_tag_colors, nearest_collection_tag, add_to_color_set, get_color_set_color, remove_from_color_sets, apply_color, show_channels, has_object_color, get_object_color, get_material_color, get_average_vertex_color, color_difference, get_objects_by_color, select_by_color, get_color, lead_object, scene_objects, reset_colors, reset_vertex_colors

### `display_utils/exploded_view.py` — Exploded View: spread meshes apart from their shared center, and back.
- `class ExplodedView(pythontk.LoggingMixin)`
  - methods: objects, explode, un_explode, toggle_explode, un_explode_all

### `display_utils/outliner_tint.py` — Per-object **outliner text colour** for Blender — the true analogue of Maya's
- constants: COLOR_PROP
- `class OutlinerTint(_OutlinerTintInternal)`
  - methods: set_color, get_color, clear, tinted_objects, is_supported, status, is_enabled, enable, disable

### `edit_utils/_edit_utils.py` — Mesh-editing utilities — reduce/decimate, coplanar dissolve, triangulate / tris-to-quads,
- `class EditUtils(_EditUtilsInternal)`
  - methods: get_symmetry, set_symmetry, hook_bind_inverse, hook_curve_point, decimate, dissolve_coplanar, triangulate, tris_to_quads, subdivide_mesh, boolean_op, set_subdivision, apply_subdivision, set_shading, average_normals, select_edges_by_angle, set_edge_hardness, clear_custom_split_normals, add_custom_split_normals, has_custom_split_normals, flip_normals, recalculate_normals, propagate_normals, conform_normals, extract_reversed_faces, clean_geometry, crease_edges, mirror, mirror_instance, cut_along_axis, wedge, snap_closest_verts, snap_to_grid, snap_to_surface, get_standoff_distances, get_similar_mesh, ungroup_objects, separate_objects, combine_objects, detach_components, get_overlapping_faces, get_overlapping_duplicates, loft

### `edit_utils/bevel.py` — Bevel tool engine.
- `class Bevel`
  - methods: bevel

### `edit_utils/bridge.py` — Bridge: connect two open edge loops with new faces — mirror of mayatk's
- `class Bridge`
  - methods: bridge

### `edit_utils/components.py` — Component-level mesh operations — mirror of mayatk's ``core_utils.components``.
- `class Components(_ComponentsInternal)`
  - methods: crease_edges, transfer_creased_edges, set_edge_hardness, average_normals, set_edge_softness, set_to_face_normals, reverse_normals, toggle_normal_lock, transfer_normals, transfer_mesh_data

### `edit_utils/curtain/_curtain.py` — Procedural draped-cloth (curtain) generator for Blender -- mirror of mayatk's
- `class Rail(pythontk.Polyline)`
  - methods: from_selection, sample_curve
- `class CurtainMesh(CurtainDrape)`
  - methods: create, build
- `class CurtainRig`
  - methods: attach
- `class Curtain`
  - methods: rail_from_selection, create, rail_curve, delete_rail_curve, triangle_count

### `edit_utils/curtain/_curtain_drape.py` — Procedural draped-cloth (curtain) drape engine — pure geometry, no DCC.
- `class CurtainDrape(_CurtainDrapeInternal)`
  - methods: prepare, grid_points, drape

### `edit_utils/cut_on_axis.py` — Cut On Axis: slice meshes along an axis -- mirror of mayatk's
- `class CutOnAxis`
  - methods: perform_cut_on_axis

### `edit_utils/duplicate_grid.py` — Grid array duplication — mirror of mayatk's ``edit_utils.duplicate_grid``.
- constants: GRID_MAX_COPIES
- `class DuplicateGrid`
  - methods: duplicate_grid

### `edit_utils/duplicate_linear.py` — Linear array duplication — mirror of mayatk's ``edit_utils.duplicate_linear``.
- `class DuplicateLinear`
  - methods: duplicate_linear

### `edit_utils/duplicate_radial.py` — Radial array duplication — mirror of mayatk's ``edit_utils.duplicate_radial``.
- `class DuplicateRadial(_DuplicateRadialInternal)`
  - methods: duplicate_radial, regroup_copies

### `edit_utils/dynamic_pipe.py` — Dynamic Pipe tool — Blender port of mayatk's ``edit_utils.dynamic_pipe``.
- `class DynamicPipe(pythontk.LoggingMixin)`
  - methods: from_selection

### `edit_utils/macros/_macros.py` — Hotkey macros — the Blender counterpart of ``mayatk.edit_utils.macros``.
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

### `edit_utils/naming/_naming.py` — Batch object naming — Blender port of mayatk's ``edit_utils.naming.Naming``.
- `class Naming(pythontk.HelpMixin, pythontk.LoggingMixin)`
  - methods: SUFFIX_TYPES, affix_rules, scene_objects, scope_objects, select_by_name, select_link, rename, generate_unique_name, strip_illegal_chars, strip_chars, set_case, type_key, affix_for, suffix_by_type, append_location_based_suffix

### `edit_utils/selection.py` — Category-driven select-by-type — mirror of mayatk's ``edit_utils.selection.Selection``
- `class Selection`
  - methods: loop_multi_select, select_by_type, select_children, select_hierarchy_above, select_hierarchy_below, convert_to, select_face_path, select_vertex_perimeter, select_edge_perimeter, select_face_perimeter, select_border_edges, select_shell_border, select_uv_shell, select_uv_shell_border, select_uv_perimeter, select_uv_edge_loop, get_available_selection_types, get_selection_categories
- `class SelectionOrder`
  - methods: enable, disable, is_enabled, get, set_order

### `edit_utils/snap.py` — Snap: vertices to a target surface, to the closest target vertex, or to the
- `class Snap(pythontk.HelpMixin)`
  - methods: source_and_target, snap_to_surface, snap_to_closest_vertex, snap_to_grid

### `edit_utils/target_weld.py` — Target Weld — interactive drag-a-vertex-onto-another merge tool.
- constants: COLOR_PRESELECT, COLOR_SOURCE, COLOR_LINE, COLOR_HALO, COLOR_PROMPT_BG, COLOR_PROMPT_TEXT, PICK_RADIUS, MARKER_SIZE, HALO_SIZE
- `class TargetWeld(_TargetWeldInternal)`
  - methods: activate, project_points, pick_screen_point, weld_position, dash_segments, weld_pair, target_weld

### `env_utils/_env_utils.py` — blendertk environment / scene-library utilities — the engine behind the Reference Manager panel.
- `class EnvUtils(_EnvUtilsInternal)`
  - methods: find_blend_files, list_libraries, linked_blend_paths, is_blend_linked, link_blend_file, reload_library, remove_library, make_library_local, set_current_workspace, current_workspace, workspace_root, set_scene_source, twin_save_target, scene_source, scene_project_root, scene_workspace, scene_source_images_dir, scene_artifact_path, source_images_dir, texture_search_dirs, scenes_dir, workspace_scenes_dir, list_workspace_templates, workspace_template_rules, save_workspace_template, delete_workspace_template, create_workspace, promote_workspace, find_workspaces, open_scene, new_scene, scene_has_content, scene_has_unsaved_changes, scene_settings, apply_scene_settings, format_scene_name, scene_save_path, save_scene_as, export_scene_as_obj, rename_scene_file, delete_scene_file, set_reference_display_mode, get_reference_display_mode

### `env_utils/blender_connection.py` — Launch a FRESH headless Blender to run a script / code string and capture its output — the
- constants: RESULT_PASS
- `class BlenderConnection`
  - methods: find_blender, run_script, run_code, run_result

### `env_utils/fbx_utils.py` — FBX import / export helpers — the Blender counterpart of mayatk's ``env_utils.fbx_utils``
- `class FbxUtils(_FbxUtilsInternal)`
  - methods: hook, producers, export_context, publish, publish_authored, stagers, stage, begin_export, end_export, export_prepared, scratch_export, enable_export_producer, disable_export_producer, register_export_stager, unregister_export_stager, scene_range_take, bake_range, reset_takes, apply_takes, stage_curve_proxy, apply_takes_from_node, export, import_fbx, scene_settings, export_selection_fbx, embed_dependencies, finish_deliverable

### `env_utils/handoff_export.py` — Blender-side selection + export hooks shared by the hand-off bridge engines.
- `class BlenderExportMixin`
  - methods: lightmap_search_dirs, hdr_codec, scope_closure
- `class BridgeHost`
  - methods: resolve_scope, scope_closure, install_package

### `env_utils/hierarchy_sync/_fbx_stage_worker.py` — Convert an FBX reference to a standalone ``.blend`` inside a FRESH headless Blender.
- `main() -> int`
- constants: WARNING_PREFIX

### `env_utils/hierarchy_sync/_hierarchy_sync.py` — Hierarchy Sync core engine — mirror of mayatk's ``env_utils.hierarchy_sync._hierarchy_sync``.
- `class HierarchyMapBuilder`
  - methods: build_path_map
- `class HierarchySync(pythontk.LoggingMixin)`
  - methods: analyze_hierarchies, create_stubs, quarantine_extras, fix_fuzzy_renames, fix_reparented, get_supported_formats, stage_reference_blend, build_path, delete_objects, should_keep_node_by_type
- `class ObjectSwapper(pythontk.LoggingMixin)`
  - methods: pull_objects_from_reference

### `env_utils/hierarchy_sync/hierarchy_baseline.py` — The scene's hierarchy baseline, stored in the .blend (mirror of mayatk).
- `class HierarchyBaseline(pythontk.HierarchyBaselineStore)`

### `env_utils/hierarchy_sync/hierarchy_sync_session.py` — Hierarchy Sync's host half: the reference a diff runs against, held in the
- `class HierarchySyncSession(pythontk.LoggingMixin)`
  - methods: loggers, fuzzy_matching, is_loaded, load_reference, release_reference, analyze, clear_analysis, repair, pull, current_scene_nodes, hierarchy_structure, clean_path, node_path, node_name, node_alive, rename_nodes, reparent_nodes, delete_nodes, select_nodes, scene_path, workspace, recent_scenes, open_scene, reference_formats

### `env_utils/hierarchy_sync/scene_data_sidecar.py` — Scene-data sidecar -- Blender's scene hook over ``ptk.SceneDataSidecarBase``.
- `class SceneDataSidecar(pythontk.SceneDataSidecarBase)`
  - methods: expand_to_descendants

### `env_utils/material_records.py` — The ``materials`` record's Blender producer -- mirror of mayatk's
- `class MaterialRecords(pythontk.HelpMixin)`
  - methods: export_record, entries

### `env_utils/maya_bridge/_maya_bridge.py` — Maya bridge engine -- export the Blender selection and run a chosen import template in Maya.
- constants: DEFAULTS
- `class MayaBridge(BlenderExportMixin, pythontk.ScriptLaunchBridge)`
  - methods: maya_path, headless_app_path, mayapy_from_maya_exe, params_registry, params_defaults, render_context, list_templates, template_modes, list_template_modes

### `env_utils/maya_bridge/_scene_import.py` — Import a Maya scene (.ma/.mb) into Blender via a headless-Maya round-trip
- constants: BAKE_CACHE_KIND, BAKE_INFO_SOURCE, BAKE_INFO_KEY, BAKE_INFO_TEXT, SUPPORTED_EXTENSIONS, USD_EXTENSIONS, GLTF_EXTENSIONS, BAKE_SOURCE_EXTENSIONS, BAKE_SOURCE_SUFFIX, MAYA_GROUP_EMPTY_DISPLAY_SIZE, FBX_IMPORT_OPTIONS, REDUCE_KEYS_DEFAULT
- `class MayaSceneImport(pythontk.LoggingMixin)`
  - methods: maya_path, mayapy_path, require_mayapy, render_script, convert, import_scene, import_payload, hide_relationship_lines, apply_world, blender_path, require_blender, render_bake_script, bake, bake_scene, bake_dir, bake_slot, bake_info, bake_source, rehome_bake, purge_unused_bakes, mayapy_from_maya_exe, scene_has_complex_animation, find_scenes

### `env_utils/maya_bridge/parameters.py` — Registry of user-tunable Maya-bridge parameters exposed to the panel.
- constants: PARAMS
- `class Parameters(ParamRegistry)`

### `env_utils/maya_bridge/templates/_bake_scene.py` — Import a converted intermediate (USD or FBX) headlessly and save it as a ``.blend`` so a
- `main()`
- constants: SRC_FILE, OUT_BLEND, EXTRA_SYS_PATH, REDUCE_KEYS, BAKE_INFO, STREAM_HOME

### `env_utils/maya_bridge/templates/_import_scene.py` — Open a Maya scene headlessly (mayapy) and export it as FBX for a Blender import.
- `uniquify_short_names(cmds)`
- `fbx_safe_materials(cmds)`
- `skinning_methods(cmds)`
- `scene_node_types(cmds)`
- `scene_settings(cmds)`
- `write_manifest(entries, visibility, node_types, scene, path, lights=(), skins=None, bones=None, scene_data=None, rig=None, machinery=None, stepped=None)`
- `scene_lights(cmds)`
- `stepped_keys(spell)`
- `main()`
- `scene_data_sections(cmds, spell)`
- constants: SRC_PATH, OUT_FBX, EMBED_TEXTURES, INCLUDE_ANIMATION, SMART_BAKE, RIG_MODE, RIG_CAPABILITY, MODERN_SHADER_TYPES, STINGRAY_SHADER_TYPES, STINGRAY_TEX_SLOTS, STINGRAY_SLOT_CHANNELS

### `env_utils/maya_bridge/templates/_import_scene_usd.py` — Open a Maya scene headlessly (mayapy) and export it as USD for a Blender import.
- `usd_safe_materials(cmds)`
- `export_usd(cmds, frame_range=None)`
- `drop_unmergeable_locator_shapes(cmds)`
- `collect_materials(cmds)`
- `uniquify_short_names(cmds)`
- `collect_instance_groups(cmds)`
- `scene_settings(cmds)`
- `write_manifest(cmds, materials=None, shading_groups=None, bones=None, scene_data=None, rig=None, machinery=None)`
- `main()`
- `scene_data_sections(cmds, spell)`
- constants: SRC_PATH, OUT_USD, INCLUDE_ANIMATION, RIG_MODE, RIG_CAPABILITY, STINGRAY_SHADER_TYPES, STINGRAY_TEX_SLOTS, STINGRAY_SLOT_CHANNELS, STINGRAY_DATA_SLOTS

### `env_utils/maya_bridge/templates/_save_scene.py` — Import the bridged FBX into a headless ``mayapy`` and save it as a Maya scene.
- `import_usd(cmds)`
- `import_payload(cmds, mel, engine)`
- `restore_usd_locators(cmds, engine, new_nodes)`
- `covering_take(mel, path)`
- `fallback_take(mel, path, warn)`
- `import_fbx(cmds, mel, engine)`
- `restore_empty_groups(cmds, engine, new_nodes)`
- `rebuild_materials(engine, new_nodes)`
- `rebuild_scene_data(engine, new_nodes)`
- `main()`
- constants: BRIDGE_MODES, FBX_PATH, USD_EXTENSIONS, CARRIER, OUT_FILE, EXTRA_SYS_PATH, SHADER_TYPE, USD_IMPORT_OPTIONS

### `env_utils/maya_bridge/templates/import.py` — Import the bridged payload (FBX or USD) into Maya, with optional clean-slate and
- `covering_take(mel, path)`
- `fallback_take(mel, path, warn)`
- `import_fbx()`
- `import_usd()`
- `import_payload()`
- `restore_usd_locators(new_nodes)`
- `restore_empty_groups(new_nodes)`
- `rebuild_materials(new_nodes)`
- `rebuild_scene_data(new_nodes)`
- `main()`
- constants: BRIDGE_MODES, FBX_PATH, USD_EXTENSIONS, CARRIER, EXTRA_SYS_PATH, CLEAR_SCENE, FRAME_VIEW, SHADER_TYPE, USD_IMPORT_OPTIONS

### `env_utils/pm_doctor.py` — Shadow doctor for embedded-DCC installs (companion of package-manager.bat).
- `find_shadows()`
- `main()`

### `env_utils/reference_manager.py` — Reference Manager engine -- the workspace's scene files, the scene's references
- `class ReferenceManager(pythontk.LoggingMixin, _ReferenceManagerInternal)`
  - methods: current_workspace, current_working_dir, recursive_search, ignore_empty_workspaces, workspace_files, find_available_workspaces, invalidate_workspace_files, current_references, bake_source, row_key, add_reference, remove_references, import_references, update_references, get_reference_display_mode, set_reference_display_mode, follow_bake_sources, purge_unused_bakes, is_foreign, needs_conversion, scene_has_complex_animation, find_foreign_scenes, bake_scene, import_scene, bake_project_root, foreign_scratch_path, discard_stale_scratches, current_scene, scene_modified, save_open_scene, save_scene, new_scene, is_current, open_scene, rename_scene_file, remove_scene_file

### `env_utils/scene_exporter/_scene_exporter.py` — Scene Exporter engine -- Blender port of mayatk's ``env_utils.scene_exporter``.
- `class SceneExporter(pythontk.SceneExporterBase)`
  - methods: perform_export, name_context, resolve_export_path, generate_export_path, list_fbx_presets, fbx_preset_dir, fbx_preset_path, save_fbx_preset, delete_fbx_preset, presets, refresh_fbx_presets, fbx_preset_metadata, restore_fbx_preset, edit_fbx_preset, objects_to_export, load_fbx_export_preset, verify_fbx_preset

### `env_utils/scene_exporter/task_manager.py` — The Scene Exporter's task/check manager -- mirror of mayatk's ``TaskManager``.
- `class TaskManager(TaskFactory, _SceneTasksMixin, _TextureTasksMixin, _AnimationTasksMixin, _TaskChecksMixin, _TaskDefinitionsMixin)`
  - methods: run_tasks, objects, write_scene_data_sidecar, create_glb, set_linear_unit, exclude_hdr, ignore_groups, export_path, begin_run, reassign_duplicate_materials, convert_to_relative_paths, resolve_invalid_texture_paths, convert_textures, optimize_textures, smart_bake, optimize_keys, tie_all_keyframes, snap_keys_to_frame, set_bake_animation_range, export_data_node, ensure_scene_records_published, apply_declared_takes, ship_declared_takes, check_framerate, check_referenced_objects, check_geometry_lod_suffix, check_duplicate_names, check_root_default_transforms, check_hidden_geometry, check_overlapping_duplicate_mesh, check_objects_below_floor, check_duplicate_materials, check_material_compatibility, check_texture_optimization, check_path_length, check_output_writable, check_valid_paths, check_texture_file_size, check_untied_keyframes, check_floating_point_keys, task_definitions, check_definitions, definitions

### `env_utils/scene_state.py` — Read named sections of live-scene state for transport.
- `class SceneState`
  - methods: source, read

### `env_utils/script_output.py` — Blender script-output console — the blendertk analogue of mayatk's ``ScriptConsole``.
- `class ScriptConsole`
  - methods: instance, widget, show, hide, toggle, begin_capture, restore, is_open, teardown

### `env_utils/unity_bridge/_unity_bridge.py` — Unity bridge engine -- export the Blender selection into a Unity project's Assets/.
- `class UnityBridge(BlenderExportMixin, pythontk.HandoffBridge)`
  - methods: list_template_modes, params_registry, params_defaults, list_delivery_modes

### `env_utils/unity_bridge/parameters.py` — User-tunable parameters for the Blender->Unity bridge panel -- mirror of mayatk's
- constants: PARAMS
- `class Parameters(ParamRegistry)`

### `env_utils/upstream_patches.py` — Defects in Blender's own Python that blendertk corrects, each with the probe
- constants: SIBLING_ARMATURES

### `env_utils/usd.py` — USD import / export helpers — the Blender counterpart of mayatk's ``env_utils.usd``
- constants: USD_EXTENSIONS
- `class UsdUtils(_UsdUtilsInternal)`
  - methods: is_usd_file, export, sampling_frame_range, fold_single_mesh_xforms, sanitize_prim_name, hidden_objects, export_prim_path, prim_path, pin_primvar_indices, mark_skinning_methods, collapse_static_xforms, mark_container_skeletons, mark_invisible, mark_orthographic, apply_visibility, activate_uv_map, import_scene, import_usd, bake_transform_caches, honor_reset_xform_stack, skinning_methods, scene_settings, export_selection_usd

### `env_utils/webxr_preview.py` — Push the Blender selection to a live browser / WebXR preview.
- `class WebXrPreview(BlenderExportMixin, pythontk.PreviewBridge)`

### `light_utils/_light_utils.py` — Light utilities — the world-environment (HDRI) helpers behind the HDR Manager panel
- `class LightUtils(_LightUtilsInternal)`
  - methods: set_world_hdri, get_world_hdri, set_world_ray_visibility, get_world_ray_visibility, set_world_importance_resolution, get_world_importance_resolution, clear_world_hdri, world_emits, lights_from_geometry, remove_lights, set_world_environment, lights_from_records, scale_light_energy, set_emission_strength

### `light_utils/hdr_manager.py` — Blender world-HDRI environment manager.
- `class HdrManager(pythontk.LoggingMixin)`
  - methods: hdr_env, hdr_file_path, visibility, rotation, intensity, exposure, resolution, diffuse, specular, create_network, clear, defer_apply

### `light_utils/lightmap_baker/_probe_placement.py` — Where a lightmap bake's reflection probe stands -- Blender's scene for the placement.
- `class ProbePlacement(pythontk.ProbePlacement)`

### `light_utils/lightmap_baker/lightmap_baker.py` — High-level lightmap baking workflow for Blender -> game engines (Unity-first).
- `class LightmapBakeResult`
  - methods: files, folders
- `class LightmapBaker(pythontk.LoggingMixin)`
  - methods: resolution, samples, denoise, device, bounces, adaptive, preset_store, preset_name, from_preset, bake_targets, bake, stale_report, rebake_changed, rebake_report, rebake, map_folders, bake_breakdown, bake_maps, bake_probe, preflight, bake_verdict, bake_separated, bake_atlas, atlas_plan, plan_sizes, pack_atlas, commit_lightmap, revert, revert_lightmap, baked_objects, track, combine_sizes, combine, map_levels, peak_level, scope_meshes, scope_lights, select

### `light_utils/lightmap_baker/lightmap_records.py` — The scene record a lightmap bake leaves in Blender: markers, manifest, and the files they name.
- `class LightmapRecords(pythontk.LoggingMixin)`
  - methods: foreign_writer, baked_objects, lightmap_info, commit, bake_state, scene_state, untracked, track, revert, probe, commit_probe, project_root, superseding, migrate_legacy, migrate_folder_hints, export_record, refresh_export_metadata, claims, wrote, strangers, lightmap_dependencies, search_dirs, heal_lightmap_paths, normalize_lightmap_paths, relocate_lightmaps, repath_lightmaps, rename_lightmap, fork_lightmap, transfer_lightmaps

### `light_utils/lightmap_baker/web_export.py` — Ship a committed lightmap bake in a web (GLB) deliverable.
- `class LightmapWebExport(pythontk.LoggingMixin)`
  - methods: encode_for_web, encode_hdr_for_web, wire_lightmaps, unwire_lightmaps, build_manifest, export_glb, embed_probe, encode_hdr_radiance, wired_for_export

### `mat_utils/_mat_utils.py` — Material utilities — mirror of mayatk's ``MatUtils`` public names where the concepts align:
- constants: SHADER_TEMPLATES
- `class MatUpdater(pythontk.LoggingMixin, _MatUtilsInternal)`
  - methods: shader_type_choices, texture_root, selected_materials, materials_using_textures, update_materials
- `class MatUtils(_MatUtilsInternal)`
  - methods: get_node_tree, get_mats, create_mat, assign_mat, find_by_mat_id, find_unassigned, select_by_material, reload_textures, get_scene_mats, is_mat_assigned, get_mat_swatch_icon, get_texture_paths, get_texture_info, get_mat_info, format_mat_info_html, format_texture_info_html, find_materials_with_duplicate_textures, reassign_duplicate_materials, delete_unused_materials, image_texture_nodes, select_image_nodes, graph_materials, get_image_records, image_paths_scope, repath_image, to_project_relative, resolve_missing_textures, normalize_texture_paths, get_image_material_map, materials_for_textures, fix_color_spaces, set_texture_directory, plan_find_and_copy_textures, find_and_copy_textures, rename_texture_file, sync_material_names, format_texture_paths_html, get_shader_templates, apply_shader_template, create_shader_template, serialize_material, restore_material, resolve_pbr_plan, create_pbr_material, create_pbr_materials, update_materials

### `mat_utils/arnold_bridge.py` — Arnold render-bridge management -- Blender port of mayatk's ``mat_utils.arnold_bridge``.
- `class ArnoldBridge(pythontk.LoggingMixin)`
  - methods: unrenderable_materials, add, remove, rebuild, temporary, get_bridge, has_bridge, authored_plug, base_materials, select_bridged

### `mat_utils/bake_sets.py` — Scene-stored bake sets: named object sets the bake tools read (Blender).
- `class BakeSet`
  - methods: conventional_name, name, respell, collection, exists, members, member_names, select, meshes, define, clear
- `class BakeSourceSet(BakeSet)`
  - methods: companion_path
- `class LightmapExcludeSet(BakeSet)`
- `class LightmapIsolateSet(BakeSet)`
  - methods: groups, meshes

### `mat_utils/emissive_groups.py` — Emissive Groups' Blender half: one boolean FACE attribute per group on its
- `class BlenderEmissiveGroups(_BlenderEmissiveGroupsInternal)`
  - methods: faces, add_members, remove_members, members, select, membership_stores, carrier_weights, weight_exists, ensure_weight, delete_weight, sync_weight, key_weight, foreign_color_sets, write_colors, uv_triangles, mask_path, object_names, face_count, mesh, merge_stores, discard_stores, publish, extra_warnings
- `class EmissiveGroups(pythontk.EmissiveGroupsModel)`
  - methods: create_export_curve_proxies, remove_export_curve_proxies

### `mat_utils/game_shader.py` — Game Shader — auto-build a Principled-BSDF material from a set of PBR textures.
- `class GameShader(pythontk.LoggingMixin, _GameShaderInternal)`
  - methods: shader_type_choices, create_network, assign_network

### `mat_utils/image_to_plane/_image_to_plane.py` — Map image files to textured planes in Blender — port of mayatk's ``mat_utils.image_to_plane``.
- `class ImageToPlane(pythontk.LoggingMixin)`
  - methods: create, remove

### `mat_utils/marmoset_bridge/_marmoset_bridge.py` — Blender-side glue for the Marmoset Toolbag engine -- mirror of mayatk's
- `class MarmosetBridge(pythontk.HandoffBridge, _MarmosetBridgeInternal)`
  - methods: toolbag_path, params_registry, list_template_modes, params_defaults, render_template, baked_texture_dir, toolbag_scene_dir, source_model_path_for, source_material_name, baked_material_name, texture_set_aliases, build_bake_pairs_manifest

### `mat_utils/marmoset_bridge/_marmoset_engine.py` — Drive Marmoset Toolbag from the outside -- launch + templated automation.
- constants: APP, SEND_TO, ROUND_TRIP
- `class MarmosetEngine(pythontk.Deliverer, pythontk.LoggingMixin)`
  - methods: toolbag_path, toolbag_log_path, preflight, deliver, send, render_template, list_templates, template_modes, list_template_modes

### `mat_utils/marmoset_bridge/_toolbag_helpers.py` — Shared helpers for Marmoset Toolbag template scripts.
- constants: SLOT_MAP, MODULE_NEUTRAL_FIELDS, SUBROUTINE_FIXES
- `class ToolbagHelpers(_ToolbagHelpersInternal)`
  - methods: derive_per_run_log_path, begin_log, log, find_material, load_manifest, wire_materials_from_manifest, split_source_target, collect_mesh_objects, apply_sky_preset, frame_in_viewport

### `mat_utils/marmoset_bridge/marmoset_rpc/connection.py` — JSON-RPC client bound to the marmoset_rpc Toolbag plugin.
- constants: DEFAULT_HOST, DEFAULT_PORT
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
- constants: PLUGIN

### `mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/_rpc_core.py` — The in-application half of the RPC pair: registry + marshaller + server.
- `class OpRegistry(_OpRegistryInternal)`
  - methods: register, get, all_ops, describe
- `class MainThreadMarshaller(_MainThreadMarshallerInternal)`
  - methods: is_active, post, run
- `class RpcPlugin(object)`
  - methods: import_ops, port, is_hosted, is_running, address, start, serve, stop, autostart, autostart_safely

### `mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/ops/scene_ops.py` — Scene-inspection ops.
- `summary()`
- `list_materials()`

### `mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/ops/system_ops.py` — Toolbag-specific system ops.
- `version()`

### `mat_utils/marmoset_bridge/parameters.py` — Registry of user-tunable Marmoset Toolbag parameters exposed to the bridge UI.
- constants: PARAMS, SUPERSESSIONS
- `class Parameters(ParamRegistry)`

### `mat_utils/marmoset_bridge/template_params.py` — Plain default values + literal formatting for Marmoset template tokens.
- constants: DEFAULTS
- `class TemplateParams`
  - methods: derive_auto_maps, derive_bake_values, python_literal, defaults, to_context

### `mat_utils/marmoset_bridge/templates/bake.py` — Bake source detail + surface maps onto the target meshes.
- `main()`
- constants: BRIDGE_MODES, MODEL_FILE, SOURCE_MODEL_FILE, PAIRS_FILE, MANIFEST_FILE, OUTPUT_DIR, SAVE_PATH, SHOULD_QUIT, BAKE_SIZE, BAKE_SAMPLES, BAKE_PADDING, BAKE_BITS, OUTPUT_FORMAT, MAP_NORMAL, MAP_AO, MAP_CURVATURE, MAP_THICKNESS, MAP_POSITION, MAP_MATID, MAP_ALBEDO, MAP_ROUGHNESS, MAP_METALNESS, MAP_EMISSIVE, HIGH_SUFFIX, LOW_SUFFIX, SUFFIX_INCLUDE_CHILDREN, CAGE_OFFSET, AUTO_CAGE, IGNORE_BACKFACES, CAGE_STANDOFFS, CAGE_HOST_DIAGONAL, CAGE_REACH_FACTOR, AUTO_CAGE_MARGIN, AUTO_CAGE_FLOOR, AUTO_CAGE_BOUNDS_FLOOR, AUTO_CAGE_CEILING, AUTO_CAGE_GAP_MARGIN

### `mat_utils/marmoset_bridge/templates/import.py` — Open the model in Toolbag and wire materials from the manifest.
- `main()`
- constants: BRIDGE_MODES, MODEL_FILE, MANIFEST_FILE, SAVE_PATH, SHOULD_QUIT

### `mat_utils/marmoset_bridge/templates/lookdev.py` — Open the model in Toolbag, apply a Sky preset, and frame the model.
- `main()`
- constants: BRIDGE_MODES, MODEL_FILE, MANIFEST_FILE, SAVE_PATH, SHOULD_QUIT, SKY_PRESET, FRAME_SELECTION

### `mat_utils/marmoset_bridge/toolbag_log.py` — Marmoset Toolbag log-file resolution, classification, and live tailing.
- `class ToolbagLog`
  - methods: resolve_toolbag_log_path, classify_log_line, dispatch_log_lines, start_toolbag_log_tail

### `mat_utils/mat_manifest.py` — Material-to-texture manifest for bridge workflows -- mirror of mayatk's ``mat_utils.mat_manifest``.
- `class MatManifest(pythontk.HelpMixin)`
  - methods: build, restore

### `mat_utils/render_opacity/render_effects.py` — Render Effects — Blender per-object render-effect channels for engine-ready control (mirror of
- `class RenderEffects(pythontk.LoggingMixin)`
  - methods: ensure_opacity_prop, channel_records, apply_channel_records, objects_with_visibility_keys, create, ensure_highlight_props, key_pulse, preview_channels, selected_objects, has_channel, select_objects, objects_with_channel, channel_colors, channel_color_stops, set_channel_color, stage_export_proxies, remove_export_proxies, finish_export, remove, set_key, key_fade, write_keys, scene_store, scene_recipe, apply_effect, sync_visibility_from_opacity, ensure_connections, prepare_for_export, visibility_tracks, export_record, refresh_export_metadata

### `mat_utils/shader_templates.py` — Shader templates: save a material's node graph as a reusable template, rebuild it.
- `class ShaderTemplates`
  - methods: store, suggested_name, save_template, restore_template

### `mat_utils/substance_bridge/_substance_bridge.py` — Substance 3D Painter bridge -- export Blender selection and hand off to Painter.
- constants: SEND_TO, ROUND_TRIP, TARGET_AUTO, TARGET_NEW, TARGET_CURRENT
- `class SubstanceBridge(SubstanceEngine)`
  - methods: params_registry, send, source_model_path_for

### `mat_utils/substance_bridge/_substance_engine.py` — Drive Substance 3D Painter from the outside -- the DCC-free half of the bridge.
- constants: SEND_TO, ROUND_TRIP, TARGET_AUTO, TARGET_NEW, TARGET_CURRENT
- `class SubstanceEngine(pythontk.HandoffBridge)`
  - methods: painter_path, painter_log_path, instances, find_live_managed, ensure_rpc_plugin, mesh_map_files, list_templates, parse_template, list_template_modes, resolve_painter_log_path

### `mat_utils/substance_bridge/connection.py` — Substance 3D Painter connection module.
- constants: APP
- `class SubstanceConnection(pythontk.LoggingMixin)`
  - methods: open, close, is_alive, attach, find_painter_exe, default_log_path

### `mat_utils/substance_bridge/parameters.py` — Registry of user-tunable Substance Painter parameters exposed to the bridge UI.
- constants: PARAMS
- `class Parameters(ParamRegistry)`
  - methods: render_cli_context, render_js_context

### `mat_utils/substance_bridge/substance_rpc/client.py` — HTTP RPC client for the Painter-side ``substance_rpc`` plugin.
- constants: DEFAULT_RPC_PORT
- `class PainterRpcClient(RpcClient)`
  - methods: wait_until_ready, invoke, eval_js, eval_py, reload_mesh, reload_status, project_info

### `mat_utils/substance_bridge/substance_rpc/installer.py` — Install the substance_rpc plugin into Painter's user plugin folder.
- constants: PLUGIN_NAME
- `class Installer(_InstallerInternal)`
  - methods: user_plugin_dir, is_installed, is_current, install, uninstall

### `mat_utils/substance_bridge/substance_rpc/plugin_src/substance_rpc/__init__.py` — Substance 3D Painter RPC plugin -- entry point.
- `start_server(port=None, host=None)`
- `stop_server()`
- `is_running()`
- `autostart()`
- `start_plugin()`
- `close_plugin()`
- constants: PLUGIN

### `mat_utils/substance_bridge/substance_rpc/plugin_src/substance_rpc/_rpc_core.py` — The in-application half of the RPC pair: registry + marshaller + server.
- `class OpRegistry(_OpRegistryInternal)`
  - methods: register, get, all_ops, describe
- `class MainThreadMarshaller(_MainThreadMarshallerInternal)`
  - methods: is_active, post, run
- `class RpcPlugin(object)`
  - methods: import_ops, port, is_hosted, is_running, address, start, serve, stop, autostart, autostart_safely

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

### `mat_utils/substance_bridge/templates/bake_lighting.py` — Import the FBX as a new project, then bake Iray lighting into diffuse.
- constants: BRIDGE_MODES, LAUNCH_ARGS, RPC_SCRIPT, BUILD_MANIFEST, FBX_OPTIONS, TARGET_INSTANCE

### `mat_utils/substance_bridge/templates/import.py` — Send the FBX to Painter as a new project.
- constants: BRIDGE_MODES, LAUNCH_ARGS, RPC_SCRIPT, BUILD_MANIFEST, FBX_OPTIONS, TARGET_INSTANCE

### `mat_utils/substance_bridge/templates/reimport.py` — Update the running Painter's open project from a fresh FBX export.
- constants: BRIDGE_MODES, LAUNCH_ARGS, RPC_OPS, RPC_SCRIPT, BUILD_MANIFEST, REUSE_RECORDED_EXPORT, TARGET_INSTANCE, NO_CONNECTION_HINT

### `mat_utils/substance_bridge/templates/render.py` — Render the current Painter project via Iray (JS body unverified).
- constants: BRIDGE_MODES, LAUNCH_ARGS, EXPORT_FBX, FBX_OPTIONS, RPC_SCRIPT, BUILD_MANIFEST, TARGET_INSTANCE

### `mat_utils/texture_baker.py` — Bake an object's shaded surface (material under scene lighting) to a texture — the Blender
- `class TextureBaker(pythontk.LoggingMixin)`
  - methods: bake, render_panorama, denoise_image, denoise_images, resolve_meshes, texture_set, texture_set_stem, image_sources, default_output_dir

### `mat_utils/texture_path_editor.py` — Texture Path Editor engine (Blender): the file's texture paths and the commands
- `class TexturePathEditor(pythontk.LoggingMixin)`
  - methods: texture_dir, resolve_path, is_absolute_path, rows, file_nodes, node_exists, stored_path, selected_file_nodes, reload_textures, set_texture_directory, make_paths_absolute, normalize_paths, resolve_missing_textures, unresolved_file_nodes, find_and_copy_textures, set_texture_path, is_file_rename, apply_path_edit, rename_texture, can_rename_shader, rename_shader, rename_file_node, select_objects, select_file_nodes, graph_file_nodes, delete_consequences, delete_file_nodes, lightmap_dependencies, lightmap_labels, normalize_lightmaps, repath_lightmap, rename_lightmap, lightmap_revert_scope, revert_lightmaps

### `node_utils/_node_utils.py` — Node / datablock utilities — instancing via shared object data.
- `class NodeUtils(_NodeUtilsInternal)`
  - methods: get_maya_node_type, set_maya_node_type, is_locator, expand_sets, is_group, get_instances, replace_with_instances, uninstance, get_parent, get_children, get_shape, reparent

### `node_utils/attributes/channels/_channels.py` — Channels — Blender attribute query / mutation logic.
- `class Channels`
  - methods: is_pinned, single_object_mode, pin_targets, get_selected_nodes, collect_channels, get_channel_value, format_value, parse_value, is_locked, toggle_lock, set_lock, classify_connection, is_off_curve, build_table_data, set_channel_value, reset_to_default, toggle_key_at_current_time, set_key_at_current_time, break_connections, set_mute, set_breakdown_key, select_connections, create_attribute, delete_attributes, rename_attribute, rename_node, copy_attr_values, paste_attr_values, can_freeze_selection, freeze_transforms, unfreeze_transforms, has_unfreeze_info, get_filter_kwargs, get_attr_type, get_attr_value, collect_value_strings, set_attribute_value, mute_attrs, unmute_attrs, get_custom_attrs, node_name, select_nodes, get_numeric_values, set_numeric_values, defer, open_editor, watch_scene, unwatch

### `node_utils/data_nodes.py` — The Blender scene store -- mirror of mayatk's ``node_utils.data_nodes``.
- `class DataNodes(pythontk.SceneStoreBase)`
  - methods: get_internal_node, ensure_internal, get_export_node, get_export_nodes, ensure_export, discard_if_empty, read, write, remove, values, dump_export_nodes, carriers_in, library_renames, scene_path, set_scene_source, scene_source, twin_save_target, scene_project_root, scene_workspace, scene_source_images_dir, writer_stamp_of, install_path_rebase, ensure_path_rebase, remove_path_rebase

### `nurbs_utils/_nurbs_utils.py` — Shared curve helpers — Blender mirror of mayatk's ``nurbs_utils.NurbsUtils`` namespace.
- `class NurbsUtils(pythontk.LoggingMixin)`
  - methods: add_spline, create_curve, duplicate_curve, create_plane, curve_to_mesh, straighten_curve, bend_curve, curl_curve, scale_curvature, rebuild_curve, extend_curve

### `nurbs_utils/curve_to_tube.py` — Curve to Tube tool — Blender port of mayatk's ``nurbs_utils.curve_to_tube``.
- `class CurveToTube(pythontk.LoggingMixin)`
  - methods: create, describe

### `nurbs_utils/image_tracer.py` — Image Tracer tool — Blender port of mayatk's ``nurbs_utils.image_tracer``.
- `class ImageTracer(pythontk.LoggingMixin)`
  - methods: trace_curves, create_mesh, create_negative_space_mesh, project_on_plane

### `rig_utils/_rig_utils.py` — Shared procedural-rig primitives — Blender port of mayatk's ``rig_utils.RigUtils``.
- `class RigUtils`
  - methods: resolve_object, create_locator, create_group, parent_keep_transform, create_locator_at_object, remove_locator, create_armature, add_bone_chain, add_bone, set_bone_heads, set_bone_lengths, get_bone_chain_from_root, invert_bone_chain, add_bone_constraint, add_spline_ik, bind_armature, apply_falloff_weights, copy_location, copy_rotation, damped_track, track_to, child_of, refresh_drivers, add_distance_driver, add_transform_driver, add_prop_var, add_transform_var, ensure_custom_prop, remove_driver, lock_channels

### `rig_utils/articulated_rig/_articulated_rig.py` — Articulated Rig engine (Blender) -- rigid parts on hinge, swivel, ball and slide joints.
- `class ArticulatedRig(pythontk.LoggingMixin, _ArticulatedRigInternal)`
  - methods: group, spec, name, group_uuid, scene_data, scene_rigs, for_node, selection, select, parts_of, end_control, solver, ik_blend, analyze, create, teardown, rebuild, insert_joint, remove_joint, edit_joint, set_end_control, set_limits, set_limit_from_pose, set_weight, adjusting, adjust_handles, begin_adjust, end_adjust, joint_ids, joint_id_of, joint, control, controls, state, fk_state, channel_value, set_state, install, uninstall, repair_scene, ensure_solver, match_end_control, switch_ik, reset_pose, record, model, export_record, refresh_export_metadata, grab_begin, held_point, pose_to, grab_to, key_hold

### `rig_utils/articulated_rig/_solver_handler.py` — The end control's live solve (Blender) -- the twin of mayatk's ``_solver_expression.py``.
- `class SolverHandler`
  - methods: install, uninstall, installed, ensure_installed, handler, suspended, rigs, mute_all, solve

### `rig_utils/articulated_rig/grab_tool.py` — The viewport grab for articulated rigs (Blender): press on a rigged part, drag, let
- `class ArticulatedRigGrab(pythontk.LoggingMixin, _ArticulatedRigGrabInternal)`
  - methods: activate, pick, press, drag, release, ray

### `rig_utils/controls.py` — Rig control-shape factory — Blender port of mayatk's ``rig_utils.controls.Controls``.
- `class ControlNodes`
- `class Controls(_ControlsInternal)`
  - methods: register_preset, shapes, create

### `rig_utils/rig_graph_build.py` — Build a RigGraph in Blender -- phase 3 of the rig-transfer stack.
- `class RigGraphBuilder(_RigGraphBuilderInternal, pythontk.HelpMixin)`
  - methods: capability, scope, linear_unit, up_axis, sample_world, commit, remove, build, evaluable

### `rig_utils/rig_graph_extract.py` — Read a Blender rig into a RigGraph -- the Blender side of phase 2.
- `class RigGraphExtractor(_RigGraphExtractorInternal, pythontk.HelpMixin)`
  - methods: extract

### `rig_utils/shadow_rig/_shadow_rig.py` — Shadow Rig engine — tentacle's Shadow Rig panel (``slots/tools/shadow_rig.py``) drives it.
- `class ShadowRig(pythontk.LoggingMixin)`
  - methods: has_mesh_geometry, create_contact_locator, ensure_source, get_or_create_shadow_source, source_is_directional, source_size, source_softness, set_source_softness, planes_lit_by, find_source, shadow_targets, source_selection, delete_orphaned_textures, current_model, create_shadow_plane, create_silhouette_texture, default_texture_dir, create_material, setup_drivers, plane_is_live, plane_is_baked, bake, find_shadow_planes, planes_for_nodes, for_node, for_nodes, bake_planes, unbake_planes, delete, delete_rigs, plane_materials, set_intensity, set_ground_height, detach_planes, from_plane, set_source, rebuild, silhouette_is_stale, auto_recalculate, auto_recalculate_enabled, recalculate_stale, refresh_silhouette, export_record, refresh_export_metadata, unit_scale, plane_record, plane_type, horizon_output_path, bake_horizon, plane_is_atlased, pack_atlas, unpack_atlas, create, create_for_sources, create_horizon_for_sources, create_per_object

### `rig_utils/shadow_rig/shadow_preview.py` — A live viewport preview of a horizon rig: the artist drags the light and
- `class ShadowPreview(_ShadowPreviewInternal, pythontk.LoggingMixin)`
  - methods: refusal, language, is_enabled, status, is_attached, attached_planes, attach, detach, toggle, detach_all, prepare_for_export, fragment_source, vertex_source, params_struct, frame_params

### `rig_utils/telescope_rig.py` — Telescope Rig — the telescoping-segment rig (tentacle's Telescope Rig panel drives it).
- `class TelescopeRigBundle`
  - methods: to_json, from_json
- `class TelescopeRig(pythontk.LoggingMixin)`
  - methods: partition_selection, setup_telescope_rig, scene_bundles, find_bundles, teardown

### `rig_utils/tube_rig/_tube_rig.py` — Tube Rig — Blender port of mayatk's ``rig_utils.tube_rig`` (the engine + strategies).
- `class TubeRig(pythontk.LoggingMixin, _TubeRigInternal)`
  - methods: collection, resolve_centerline, estimate_tube_radius, resolve_sizes, create_root, create_armature, create_joint_chain, create_joints, add_twist, attach_spline_rig, attach_anchor_rig, attach_fk_rig, build_curve, make_control, hook_curve_controls, constrain_end_with_falloff, for_build, selected_tubes, selected_edges, undo_step, create_controls_from_selection, bind_from_selection, constrain_ends_from_selection, build

### `rig_utils/tube_rig/strategies.py` — Tube Rig build strategies (Blender) — mirror of mayatk's ``tube_rig/strategies.py``.
- constants: TUBE_STRATEGIES, RIG_MODES
- `class TubeRigBundle`
- `class TubeStrategy(ABC)`
  - methods: register, defaults, resolve, centerline, build
- `class SplineIKStrategy(TubeStrategy)`
  - methods: build
- `class AnchorStrategy(TubeStrategy)`
  - methods: build
- `class FKChainStrategy(TubeStrategy)`
  - methods: build
- `class RigModeConfig`

### `rig_utils/tube_rig/tube_path.py` — Tube-mesh centerline extraction — Blender port of mayatk's ``rig_utils.tube_rig.tube_path.TubePath`…
- `class TubePath`
  - methods: get_centerline, estimate_radius, get_selected_edges, get_centerline_using_edges

### `rig_utils/wheel_rig.py` — Wheel Rig — wheels that roll as their control moves (tentacle's Wheel Rig panel drives it).
- `class WheelRig(pythontk.LoggingMixin)`
  - methods: rig_name, default_rig_name, from_selection, wheel_size, get_drivers, delete_drivers, rig_rotation

### `ui_utils/_ui_utils.py` — UI utilities — opening Blender editors (the analogue of Maya's editor-window mel commands).
- constants: EDITOR_TYPES
- `class UiUtils(_UiUtilsInternal)`
  - methods: get_editor_types, open_editor, reveal_in_outliner, main_window, find_editor, close_area, close_editor, dock_editor, toggle_editor, toggle_fullscreen_area, toggle_window_bars, menu_exists, get_operator, invoke_operator_dialog, get_tool_types, set_tool, current_tool, dispatch_log_link, call_native_menu, popup_message, annotate, open_pencil

### `ui_utils/blender_native_menus.py` — Symbolic-name -> Blender native-menu resolution + Qt wrapping for the both-button chord menu.
- `class BlenderNativeMenus(pythontk.LoggingMixin)`
  - methods: names, resolve, get_menu

### `ui_utils/blender_ui_handler.py`
- `class BlenderUiHandler(UiHandler)`
  - methods: instance, can_resolve, show

### `ui_utils/blender_window.py` — Native-window (win32/GHOST) helpers for hosting Qt widgets around a Blender window.
- `class BlenderWindow`
  - methods: process_ghost_hwnds, window_hwnd, is_window, client_origin, client_size, region_client_rect, set_clip_children, move_child, keyboard_focus, cursor_over, set_keyboard_focus, set_owner

### `ui_utils/calculator.py` — Calculator tool panel — Switchboard slot wiring for the co-located ``calculator.ui``.
- `class CalculatorController`
  - methods: calculate, convert_unit, get_fps_value, get_current_time, frames_to_sec, sec_to_frames

### `ui_utils/cancel_provider.py` — Blender's answers to uitk's cancellation contract (mayatk parity twin).
- `class BlenderCancelProvider(CancelProvider)`
  - methods: begin, tick, end

### `ui_utils/menu_harvest.py` — Harvest a native Blender menu into a live ``QMenu`` — the Blender half of Maya's wrap.
- `class MenuHarvest(_MenuHarvestInternal)`
  - methods: harvest_menu, invoke_operator, refill_qmenu

### `ui_utils/node_icons.py` — Resolve per-object-type icons for Blender objects (mirror of ``mtk.NodeIcons``).
- constants: ICON_COLOR
- `class NodeIcons`
  - methods: icon_name_for_type, icon_name_for_node, get_icon, audio_icon, get_pixmap

### `ui_utils/qt_dock.py` — Dock any Qt widget into a native Blender area — a true child window, not an overlay.
- `class QtDock`
  - methods: supported, docked, widget, area, content_region, dock, undock, teardown

### `ui_utils/style_setter/_style_setter.py` — Match Blender's app UI chrome to another DCC's look using Blender's NATIVE theme-preset system.
- constants: STYLES_DIR
- `class StyleSetter(_StyleSetterInternal)`
  - methods: list_styles, user_preset_dir, user_preset_path, is_installed, install, list_templates, apply_template, apply_theme_preset, set_style

### `ui_utils/ui_state.py` — Persist Blender's per-session UI visibility state across sessions (``btk.UiState``).
- `class UiState(_UiStateInternal)`
  - methods: state_path, load, save, clear, snapshot_spaces, snapshot_workspace, apply_spaces, close_hidden, apply_workspace, after_restore, install, uninstall

### `uv_utils/_auto_unwrap.py` — External auto-unwrap round-trip: OBJ out, engine, OBJ back, UVs transferred.
- `class AutoUnwrapResult`

### `uv_utils/_uv_utils.py` — UV utilities — UV-coordinate translation and UV-set cleanup (mirror of mayatk's ``UvUtils``
- constants: DEFAULT_UV_SET, LIGHTMAP_UV_SET, LIGHTMAP_OVERLAP_TOLERANCE
- `class UvUtils(_UvUtilsInternal)`
  - methods: calculate_uv_padding, move_uvs, move_to_uv_space, get_uv_bounds, get_uv_triangles, get_uv_world_triangles, get_neighbor_shell_bounds, transfer_uvs_to_similar, scale_uvs, transform_uvs, rotate_uvs, mirror_uvs, pin_uvs, get_texel_density, set_texel_density, delete_extra_uv_sets, cleanup_uv_sets, find_lightmap_uv_set, export_uv_layout, apply_uv_layout, create_lightmap_uvs, auto_unwrap, transfer_uvs, get_uv_shell_sets, get_uv_coords, set_uv_coords, get_similar_uv_shells, stack_uv_shells, straighten_uv_shells, derive_auto_seams, distribute_uv_shells, straighten_uvs, align_uvs, gather_uv_shells, gather_to_udim, orient_uv_shells, orient_shells, randomize_uv_shells

### `uv_utils/rizom_bridge/_rizom_bridge.py` — RizomUV bridge engine — Blender mirror of mayatk's ``RizomUVBridge``.
- constants: APP
- `class RizomUVBridge(pythontk.LoggingMixin, _RizomUVBridgeInternal)`
  - methods: rizom_path, rizom_version, export_path, script_path, build_send_script, send_to_rizomuv, process_with_rizomuv, params_registry, referenced_keys, list_template_modes, run_preset, pack_into_existing, expand_by_materials

### `uv_utils/rizom_bridge/parameters.py` — Registry of user-tunable RizomUV parameters exposed to the bridge UI.
- constants: PARAMS, DERIVED_KEYS, HOST_TOKEN_DEFAULTS, MIN_VERSIONS, FBX_USE_UV_SET_NAMES_MIN_VERSION
- `class Parameters(ParamRegistry)`
  - methods: expand_includes, preset_min_version, referenced_keys, derived_values, render_context, strip_unsupported

### `uv_utils/texture_combine/texture_combine.py` — Several materials into ONE: one atlas, one material, in place -- no duplicated meshes.
- `class CombineInputs`
- `class TextureCombine(pythontk.LoggingMixin, _TextureCombineInternal)`
  - methods: names, entry, members, owner, stale, gather, inputs_for, apply_rows, plan, default_name, combine, recombine, restore, update, forget, leaf_name, select, material_faces, selected_faces, consolidate_lightmaps, resolve_meshes

### `uv_utils/texture_transfer.py` — Transfer a mesh's textures from one UV layout to another -- no rays, no bake.
- `class TextureTransfer(pythontk.LoggingMixin, _TextureTransferInternal)`
  - methods: shader_types, meshes, capture_sources, live_sources, auto_source, selection_sides, transfer, default_output_dir, output_base_dir, resolve_output_dir, assign_results, topology_matches, positions_match, auto_source_uv_set, correspondence, face_materials, material_maps, material_constant, pair_by_name, pair_sources, find_combined

### `xform_utils/_xform_utils.py` — Transform utilities — object-level transform ops (world bbox, freeze, drop-to-grid,
- `class XformUtils(_XformUtilsInternal)`
  - methods: get_world_bbox, freeze_transforms, restore_transforms, has_stored_transforms, store_transforms, get_stored_transforms, scale_connected_edges, drop_to_grid, center_pivot, transfer_pivot, get_pivot_modes, match_scale, move_to, get_bounding_box, get_center_point, get_operation_axis_matrix, get_distance, order_by_distance, aim_object_at_point, restore_original_axes, get_pivot_options, set_rotate_order

### `xform_utils/matrices.py` — Matrix utilities — the Blender counterpart of mayatk's ``xform_utils.matrices``
- `class Matrices`
  - methods: get_matrix, set_matrix, local_matrix, to_matrix, identity, from_srt, compose, decompose, extract_translation, inverse, mult, world_to_local, local_to_world, is_identity
