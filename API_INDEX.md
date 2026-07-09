# blendertk — API Index

_Auto-generated. Do not edit by hand. Compact symbol index — grep this for a name; for full signatures/docs, slice [API_REGISTRY.md](API_REGISTRY.md) (never Read it whole)._

_Generated: 2026-07-09_

### `anim_utils/_anim_utils.py` — Animation utilities — key-timing math over ``fcurve.keyframe_points`` (mirror of mayatk's
- `get_fcurves(objects)`
- `scene_has_animation()`
- `set_current_frame(time=None, update=True, relative=False, snap_mode=None, invert_snap=False)`
- `shift_keys(objects, offset)`
- `move_keys_to_frame(objects, frame=None, retain_spacing=True, selected_keys_only=False, align='auto')`
- `adjust_key_spacing(objects, spacing=1, frame=None, relative=False, preserve_keys=False, selected_keys_only=False, exact_gap=False)`
- `align_selected_keyframes(objects, target_frame=None, use_earliest=True)`
- `set_visibility_keys(objects, visible=True, frame=None, when='current', offset=0, group_overlapping=False)`
- `add_intermediate_keys(objects, step=1.0, time_range=None, ignore_visibility=False, percent=None)`
- `remove_intermediate_keys(objects, time_range=None, ignore_visibility=False)`
- `select_keys(objects, time=None, add_to_selection=False)`
- `invert_keys(objects, mode='time', value_pivot=0.0, start_frame=None, relative=True, delete_original=False)`
- `snap_keys(objects, selected_only=False, time_range=None, method='nearest')`
- `set_interpolation(objects, interpolation='CONSTANT', handle=None)`
- `set_stepped(objects, stepped=True)`
- `delete_keys(objects, time=None)`
- `fit_playback_range(objects=None)`
- `copy_keys(source, mode='action')`
- `paste_keys(objects, buffer, target_time=None)`
- `transfer_keyframes(objects, relative=False, optimize=False)`
- `optimize_keys(objects=None, value_tolerance=0.001, remove_static_curves=True, remove_flat_keys=True, simplify_keys=False, stats=None)`
- `repair_corrupted_curves(objects=None, *, delete_unfixable=True, fix_infinite=True, fix_invalid_times=True, time_threshold=100000.0, value_threshold=1000000.0)`
- `tie_keyframes(objects=None, untie=False, frame_range=None, absolute=False)`
- `bake_keys(objects=None, frame_range=None, step=1, only_selected=False, visual_keying=True, clear_constraints=False, clear_parents=False, use_current_action=True, bake_types=None)`
- `bake_blend_shapes(objects=None, frame_range=None, step=1)`
- `get_animation_info(objects=None, by_time=False, ignore_holds=False)`
- `format_animation_info_csv(records)`
- `format_animation_info_html(records)`
- `configure_render_output(scene, file_format='PNG', container=None, codec=None, quality=None)`
- `class AnimUtils`

### `anim_utils/blendshape_animator/_blendshape_animator.py` — Main workflow facade for shape-key morph creation, editing, and export — mirror of mayatk's
- `class BlendshapeAnimator(ptk.LoggingMixin)`
  - methods: create, edit_weight_based, edit_frame_based, edit_apply_tweens, basic_workflow, apply_all_edits, finalize_for_export, from_existing, recover_animation, diagnose_topology_issues, cleanup_topology_mismatches, remove_target_for_export

### `anim_utils/blendshape_animator/applicator.py` — Applies tween mesh edits back to the master shape key — mirror of mayatk's
- `class ApplyStatus(Enum)`
- `class Applicator(ptk.LoggingMixin)`
  - methods: validate_topology, apply_tweens

### `anim_utils/blendshape_animator/blendshape_animator_slots.py` — Switchboard slots controller for the co-located ``blendshape_animator.ui`` — Blender port of
- `class BlendshapeAnimatorSlots(BlendshapeAnimator)`
  - methods: header_init, b000_init, b000, cmb000_init, le001_init, b001_init, b001, b003, b004_init, b004, b005, b006_init, b006, b007, b008_init, b008

### `anim_utils/blendshape_animator/creator.py` — Creates in-between (tween) target meshes for sculpting a custom morph curve — mirror of
- `class Creator(ptk.LoggingMixin)`
  - methods: create_weight_based_tweens, create_frame_based_tween, tag_tween_mesh, get_existing_weights, find_nearby_weight

### `anim_utils/blendshape_animator/keyframes.py` — Master shape-key value keyframe animation — mirror of mayatk's
- `preserve_sibling_values(key_id)`
- `class Keyframes(ptk.LoggingMixin)`
  - methods: key_id, key_block, create_keyframes, test_morph, get_frame_range

### `anim_utils/blendshape_animator/target.py` — Tween mesh wrappers and registry — mirror of mayatk's
- `class Target`
  - methods: mesh, weight, key_block_name, base_mesh_name, target_frame, update_references
- `class Targets(ptk.LoggingMixin)`
  - methods: find_all_targets, group_by_weight, update_all_references

### `anim_utils/blendshape_animator/validator.py` — Mesh + shape-key setup validation — mirror of mayatk's
- `class Validator(ptk.LoggingMixin)`
  - methods: validate_meshes, validate_shape_setup

### `anim_utils/blendshape_animator/weights.py` — Weight calculations for shape-key morph animation — mirror of mayatk's
- `class Weights`
  - methods: round_weight, frame_to_weight, generate_weights

### `anim_utils/scale_keys.py` — Dedicated scale-keys module to keep AnimUtils lean and testable (mirror of mayatk's
- `scale_keys(objects, factor, pivot=None, mode='uniform', absolute=False, group_mode='single_group', snap_mode='none', samples=64, include_rotation=False, split_static=True, merge_touching=False)`
- `class ScaleKeys`

### `anim_utils/smart_bake/_smart_bake.py` — Smart Bake engine — mirror of mayatk's ``anim_utils.smart_bake._smart_bake`` at the
- `class BakeAnalysis`
  - methods: requires_bake
- `class BakeResult`
  - methods: baked_count, success
- `class SmartBake`
  - methods: analyze, get_time_range, bake, execute, list_sessions, restore, session, run

### `anim_utils/smart_bake/bake_session.py` — Persistence and restore engine for SmartBake's nondestructive manifest — mirror of mayatk's
- `node_ref(obj_or_action) -> Optional[Dict[str, str]]`
- `resolve_ref(ref: Optional[Dict[str, str]])`
- `constraint_ref(obj, constraint, bone: Optional[str] = None) -> Dict[str, Any]`
- `resolve_constraint(ref: Optional[Dict[str, Any]])`
- `driver_ref(obj, fcurve) -> Dict[str, Any]`
- `resolve_driver(ref: Optional[Dict[str, Any]])`
- `snapshot_blend_shape_driver(obj, key_block, fcurve) -> Dict[str, Any]`
- `snapshot_blend_shape_action(obj, key_block, fcurve) -> Dict[str, Any]`
- `restore_session(session: dict) -> RestoreResult`
- `class BakeSessionStore`
  - methods: load, save, push, peek, pop, list_ids, new_session_id
- `class RestoreResult`

### `anim_utils/smart_bake/smart_bake_slots.py` — Slots for the Smart Bake tool panel (``smart_bake.ui``) — Blender port of mayatk's
- `class SmartBakeSlots(ptk.LoggingMixin)`
  - methods: cmb_scope_init, cmb_backup_init, header_init, reset_defaults, b000, b001

### `anim_utils/stagger_keys.py` — Dedicated stagger-keys module to keep AnimUtils lean and testable (mirror of mayatk's
- `stagger_keys(objects, start_frame=None, spacing=5, use_intervals=False, invert=False, group_overlapping=False, merge_touching=False, smooth_tangents=False)`
- `class StaggerKeys`

### `audio_utils/_audio_utils.py` — Scene-wide audio-clip utilities over Blender's Video Sequence Editor (VSE).
- `class AudioUtils(ptk.LoggingMixin)`
  - methods: ensure_sequence_editor, get_sequence_editor, list_clips, get_clip, add_clip, remove_clip, remove_all_clips, rename_clip, replace_clip, move_clip, trim_clip, sync_scene_range

### `audio_utils/audio_clips.py` — Audio Clips — scene-wide sound-strip management over Blender's Video Sequence Editor (VSE).
- `class AudioClipsSlots(ptk.LoggingMixin)`
  - methods: header_init, cmb000_init, cmb000, b001, b002, b005, b006, tb001_init, tb001, b003, b004_init, b004

### `cam_utils/_cam_utils.py` — Camera utilities — clip-plane adjustment (mirror of mayatk's ``cam_utils``).
- `adjust_camera_clipping(camera=None, near_clip=None, far_clip=None)`
- `class CamUtils`

### `core_utils/_core_utils.py` — Core blendertk utilities — DCC-environment info + cross-cutting decorators.
- `undoable(fn)`
- `undo_checkpoint(fn)`
- `get_env_info(key=None)`
- `ensure_image_deps(packages=None, add_to_path=True)`
- `get_recent_files(index=None)`
- `get_recent_autosave(filter_time=24, timestamp_format='%H:%M:%S')`
- `get_scene_info(objects=None)`
- `format_scene_info_html(info)`
- `analyze_scene(objects=None, adaptive=True, sections=None)`
- `cleanup_scene(quiet=False)`
- `selected_objects()`
- `active_object()`
- `get_areas(area_type)`
- `get_view3d_context()`
- `class CoreUtils(ptk.CoreUtils)`

### `core_utils/auto_instancer/_auto_instancer.py` — Scene auto-instancer: convert geometrically identical meshes to instances.
- `auto_instance(objects: Optional[Sequence[object]] = None, tolerance: float = 0.001, scale_tolerance: Optional[float] = None, require_same_material: Union[bool, int] = True, check_uvs: bool = False, check_hierarchy: bool = False, separate_combined: bool = False, combine_assemblies: bool = True, combine_non_instanced: bool = True, combine_by_material: bool = True, combine_by_distance: bool = True, combine_distance_threshold: float = 10000.0, search_radius_mult: float = 1.5, is_static: bool = True, needs_individual: bool = False, will_be_lightmapped: bool = False, can_gpu_instance: bool = True, verbose: bool = True, log_level: str = 'WARNING') -> List[object]`
- `class InstanceCandidate`
  - methods: obj, exists
- `class InstanceGroup`
- `class AutoInstancer(ptk.LoggingMixin)`
  - methods: tolerance, scale_tolerance, require_same_material, check_uvs, combine_assemblies, search_radius_mult, verbose, run, find_instance_groups

### `core_utils/auto_instancer/assembly_reconstructor.py` — Logic for separating and reassembling mesh assemblies (bpy adapter).
- `class AssemblyReconstructor`
  - methods: separate_combined_meshes, cleanup_empty_sources, cleanup_empty_assembly_groups, center_transform_on_geometry, canonicalize_transform, canonicalize_leaf_meshes, reassemble_assemblies, combine_reassembled_assemblies

### `core_utils/auto_instancer/geometry_matcher.py` — Geometry analysis and matching logic for AutoInstancer (bpy adapter).
- `class GeometryMatcher`
  - methods: clear_cache, invalidate, quantize, get_pca_basis, get_mesh_signature, get_hierarchy_signature, are_meshes_identical, are_meshes_identical_with_transform, are_hierarchies_identical

### `core_utils/auto_instancer/instancing_strategy.py` — Instancing strategy logic for AutoInstancer (mirror of mayatk's).
- `class StrategyType(Enum)`
- `class StrategyConfig`
- `class InstancingStrategy`
  - methods: evaluate

### `core_utils/diagnostics/mesh_diag.py` — Mesh diagnostics — the Blender counterpart of mayatk's ``core_utils.diagnostics.mesh_diag``
- `find_problem_geometry(objects, *, ngons=False, nonmanifold=False, interior=False, nonplanar=False, loose=False, concave=False, quads=False, zero_area_faces=False, zero_length_edges=False, zero_uv_area=False, planar_tolerance=0.001, area_tolerance=1e-06, edge_length_tolerance=1e-06, uv_area_tolerance=1e-06, select=True)`
- `class MeshDiagnostics`

### `core_utils/diagnostics/transform_diag.py` — Transform diagnostics — the Blender counterpart of mayatk's
- `fix_non_orthogonal_axes(objects=None, dry_run=False, tolerance=1e-05)`
- `class TransformDiagnostics`

### `core_utils/preview.py` — Live-preview driver for the tentacle Blender tool panels — the Blender analogue of
- `class Preview`
  - methods: is_enabled, refresh, enable, disable, commit

### `core_utils/script_job_manager.py` — Centralized Blender event-subscription manager — the Blender counterpart of mayatk's
- `class ScriptJobManager`
  - methods: instance, reset, subscribe, unsubscribe, unsubscribe_all, connect_cleanup, suppress, resume, status, print_status, teardown

### `display_utils/_display_utils.py` — Display utilities — the exploded-view toggle (mirror of mayatk's
- `is_exploded(objects)`
- `explode_view(objects, step=1.2, margin=0.05, max_iterations=50)`
- `unexplode_view(objects)`
- `unexplode_all()`
- `get_visible_geometry(objects=None)`
- `class DisplayUtils`

### `display_utils/color_id.py` — Color ID tool panel — Switchboard slot wiring for the co-located ``color_id.ui``.
- `class ColorId`
  - methods: assign_id_material, set_object_color, set_vertex_color, apply_color, get_object_color, get_material_color, get_average_vertex_color, color_difference, get_objects_by_color, reset_colors, reset_vertex_colors
- `class ColorIdSlots(ptk.LoggingMixin)`
  - methods: header_init, selected_objects, selected_button, target_color, b000, b001, b002, b003

### `display_utils/exploded_view.py` — Exploded View — Switchboard slot wiring for the co-located ``exploded_view.ui``.
- `class ExplodedViewSlots(ptk.LoggingMixin)`
  - methods: header_init, b000, b001, b002, b003

### `edit_utils/_edit_utils.py` — Mesh-editing utilities — reduce/decimate, coplanar dissolve, triangulate / tris-to-quads,
- `hook_bind_inverse(target, obj)`
- `hook_curve_point(curve, point_index, target, name=None, falloff_type='NONE')`
- `decimate(objects, percentage=50.0, preserve_quads=True, symmetry=False, apply=True)`
- `dissolve_coplanar(objects, angle_tolerance=1.0, delimit=None, preserve_borders=True, apply=True)`
- `triangulate(objects)`
- `tris_to_quads(objects, angle=40.0)`
- `subdivide_mesh(objects, cuts=1)`
- `boolean_op(objects, operation='DIFFERENCE', apply=True)`
- `set_subdivision(objects, viewport_levels=None, render_levels=None, ensure=True)`
- `set_shading(objects, smooth=True)`
- `average_normals(objects, by_uv_shell=False)`
- `select_edges_by_angle(objects, low_angle=0.0, high_angle=180.0)`
- `set_edge_hardness(objects, angle=30.0, upper_hardness=0, lower_hardness=180)`
- `clear_custom_split_normals(objects)`
- `flip_normals(objects)`
- `recalculate_normals(objects, inside=False)`
- `clean_geometry(objects, *, merge=True, merge_distance=0.0001, delete_loose=True, degenerate=True, recalculate=True, fill_holes=False)`
- `crease_edges(objects, amount=10.0, angle=None)`
- `mirror(objects, axis='x', pivot='object', merge_mode=1, delete_original=False, uninstance=False, merge_threshold=0.001)`
- `cut_along_axis(objects, axis='x', pivot='center', amount=1, offset=0.0, invert=False, delete=False, mirror=False, merge_threshold=0.0001)`
- `wedge(objects, angle=90.0, divisions=4)`
- `snap_closest_verts(obj_a, obj_b, tolerance=10.0)`
- `snap_to_grid(objects=None, grid_size=1.0, axes='xyz')`
- `snap_to_surface(source_meshes, target, offset=0.0, threshold=None, invert=False)`
- `get_similar_mesh(objects=None, *, tolerance=0.0, inc_orig=False, select=False, vertex=False, edge=False, face=False, triangle=False, shell=False, uvcoord=False, area=False, world_area=False, bounding_box=False)`
- `separate_objects(objects=None, *, by_material=False, rename=False, center_pivots=True)`
- `combine_objects(objects=None, *, group_by_material=False, cluster_by_distance=False, threshold=10000.0)`
- `detach_components(*, duplicate=False, separate=True, separate_each=False)`
- `get_overlapping_faces(objects, delete=False, select=True, round_ndigits=5)`
- `get_overlapping_duplicates(objects=None, retain=None, select=False, delete=False, round_ndigits=5)`
- `loft(objects=None, *, close=False, reverse_normals=False, section_spans=1)`
- `class EditUtils`

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

### `edit_utils/curtain.py` — Curtain (draped-cloth) generation — the Blender build over the shared
- `curtain_rail_from_selection(objects)`
- `create_curtain(rail, name='curtain', **options)`
- `class CurtainUtils`
- `class CurtainRig`
  - methods: attach
- `class CurtainSlots(ptk.LoggingMixin)`
  - methods: header_init, cmb000_init, b001, b002, perform_operation

### `edit_utils/cut_on_axis.py` — Cut-On-Axis tool panel — Switchboard slot wiring for the co-located ``cut_on_axis.ui``.
- `class CutOnAxisSlots(ptk.LoggingMixin)`
  - methods: header_init, perform_operation

### `edit_utils/duplicate_grid.py` — Grid array duplication + its tool panel — mirror of mayatk's ``edit_utils.duplicate_grid``.
- `duplicate_grid(objects, dimensions=(2, 2, 1), spacing=0.0, mode='instance')`
- `class DuplicateGrid`
- `class DuplicateGridSlots(ptk.LoggingMixin)`
  - methods: header_init, b001, perform_operation

### `edit_utils/duplicate_linear.py` — Linear array duplication + its tool panel — mirror of mayatk's ``edit_utils.duplicate_linear``.
- `duplicate_linear(objects, num_copies, translate=(0, 0, 0), rotate=(0, 0, 0), scale=(1, 1, 1), weight_bias=0.5, weight_curve=4, pivot='object', calculation_mode='weighted', instance=True)`
- `class DuplicateLinear`
- `class DuplicateLinearSlots(ptk.LoggingMixin)`
  - methods: header_init, toggle_weight_ui, b001, perform_operation

### `edit_utils/duplicate_radial.py` — Radial array duplication + its tool panel — mirror of mayatk's ``edit_utils.duplicate_radial``.
- `duplicate_radial(objects, num_copies, start_angle=0.0, end_angle=360.0, weight_bias=0.5, weight_curve=0.5, rotate_axis='y', offset=(0, 0, 0), translate=(0, 0, 0), rotate=(0, 0, 0), scale=(1, 1, 1), pivot='object', keep_original=False, instance=False, combine=False, suffix=True)`
- `class DuplicateRadial`
- `class DuplicateRadialSlots(ptk.LoggingMixin)`
  - methods: header_init, b001, perform_operation

### `edit_utils/dynamic_pipe.py` — Dynamic Pipe tool — Blender port of mayatk's ``edit_utils.dynamic_pipe``.
- `class DynamicPipe(ptk.LoggingMixin)`
- `class DynamicPipeSlots(ptk.LoggingMixin)`
  - methods: header_init, b000

### `edit_utils/macro_manager/macro_manager_slots.py` — UI slots for the Macro Manager panel — Blender port of mayatk's
- `class MacroManagerSlots(ptk.LoggingMixin)`
  - methods: header_init, cmb000_init, cmb000, tbl000_init

### `edit_utils/macros.py` — Hotkey macros — the Blender counterpart of ``mayatk.edit_utils.macros``.
- `class DisplayMacros(_ViewportMixin)`
  - methods: m_back_face_culling, m_isolate_selected, m_wireframe, m_shading, m_lighting, m_grid_and_image_planes, m_cycle_display_state, m_smooth_preview, m_frame
- `class EditMacros(_ViewportMixin)`
  - methods: m_multi_component, m_paste_and_rename, m_merge_vertices, m_group
- `class SelectionMacros`
  - methods: m_object_selection, m_vertex_selection, m_edge_selection, m_face_selection, m_invert_selection, m_toggle_UV_select_type
- `class UiMacros(_ViewportMixin)`
  - methods: m_toggle_panels
- `class AnimationMacros`
  - methods: m_set_selected_keys, m_unset_selected_keys
- `class MacroManager`
  - methods: set_macros, call_with_input, set_macro, remove_macros, list_available_macros, macro_label, macro_category, list_categories, macro_help, get_current_bindings, apply_bindings, clear_hotkey, find_conflicts, qt_sequence_to_maya_key, maya_key_to_qt_sequence, list_presets, load_preset, save_preset, delete_preset, get_active_preset, set_active_preset, apply_saved_macros
- `class Macros(MacroManager, DisplayMacros, EditMacros, SelectionMacros, AnimationMacros, UiMacros)`

### `edit_utils/mirror.py` — Mirror tool panel — Switchboard slot wiring for the co-located ``mirror.ui``.
- `class MirrorSlots(ptk.LoggingMixin)`
  - methods: header_init, perform_operation

### `edit_utils/naming/_naming.py` — Batch object naming — Blender port of mayatk's ``edit_utils.naming.Naming``.
- `class Naming(ptk.HelpMixin)`
  - methods: rename, generate_unique_name, strip_illegal_chars, strip_chars, set_case, suffix_by_type, append_location_based_suffix

### `edit_utils/naming/naming_slots.py` — Switchboard slots for the Naming panel — Blender port of mayatk's ``NamingSlots``.
- `class NamingSlots(Naming, ptk.LoggingMixin)`
  - methods: header_init, valid_suffixes, txt000_init, txt000, txt001_init, txt001, tb000_init, tb000, tb001_init, tb001, tb002_init, tb002, tb003_init, tb003

### `edit_utils/selection.py` — Category-driven select-by-type — mirror of mayatk's ``edit_utils.selection.Selection``
- `class Selection`
  - methods: select_by_type, select_children, select_hierarchy_above, select_hierarchy_below, convert_to, select_face_path, select_vertex_perimeter, select_edge_perimeter, select_face_perimeter, select_border_edges, select_shell_border, select_uv_shell, get_available_selection_types, get_selection_categories

### `edit_utils/snap.py` — Snap tool — Switchboard slot wiring for the co-located ``snap.ui``.
- `class SnapSlots(ptk.LoggingMixin)`
  - methods: header_init, b000_init, b000, b001_init, b001, b002_init, b002

### `env_utils/_env_utils.py` — blendertk environment / scene-library utilities — the engine behind the Reference Manager panel.
- `find_blend_files(root_dir, recursive=True, filter_text='')`
- `list_libraries()`
- `linked_blend_paths()`
- `is_blend_linked(path)`
- `link_blend_file(path, link=True, instance=True)`
- `reload_library(library)`
- `remove_library(library)`
- `make_library_local(library)`
- `find_workspaces(root_dir, recursive=False)`
- `open_scene(path)`
- `format_scene_name(name, case=None, suffix='')`
- `save_scene_as(directory, name, case=None, suffix='', subfolder='', overwrite=True)`
- `rename_scene_file(path, new_base)`
- `delete_scene_file(path)`
- `set_reference_display_mode(library, mode)`
- `get_reference_display_mode(library)`
- `class EnvUtils`

### `env_utils/blender_connection.py` — Launch a FRESH headless Blender to run a script / code string and capture its output — the
- `class BlenderConnection`
  - methods: find_blender, run_script, run_code, run_result

### `env_utils/fbx_utils.py` — FBX import / export helpers — the Blender counterpart of mayatk's ``env_utils.fbx_utils``
- `export_selection_fbx(filepath=None, objects=None, **fbx_opts)`
- `import_fbx(filepath, **fbx_opts)`
- `class FbxUtils`
  - methods: export, import_fbx

### `env_utils/handoff_export.py` — Blender-side selection + FBX-export hooks shared by the hand-off bridge engines.
- `class BlenderExportMixin`

### `env_utils/hierarchy_manager/_hierarchy_manager.py` — Hierarchy Manager core engine — mirror of mayatk's ``env_utils.hierarchy_manager._hierarchy_manager…
- `build_path(obj) -> str`
- `should_keep_node_by_type(obj, node_types: List[str], exclude: bool = True) -> bool`
- `class HierarchyMapBuilder`
  - methods: build_path_map
- `class HierarchyManager(ptk.LoggingMixin)`
  - methods: analyze_hierarchies, create_stubs, quarantine_extras, fix_fuzzy_renames, fix_reparented

### `env_utils/hierarchy_manager/hierarchy_manager_slots.py` — Slots for the Hierarchy Manager panel -- Blender port of mayatk's ``env_utils.hierarchy_manager``.
- `class HierarchyManagerController(ptk.LoggingMixin)`
  - methods: workspace, reference_path, analyze_hierarchies, repair_hierarchies, select_objects, populate_reference_tree, refresh_trees, is_path_ignored, clear_ignored_paths, log_diff_results, get_recent_reference_scenes, save_recent_reference_scene
- `class HierarchyManagerSlots(ptk.LoggingMixin)`
  - methods: header_init, tree000_init, tree001_init, cmb_diff_options_init, cmb_pull_options_init, tb002_init, tb003_init, tb001, tb002, tb003, b003, b005, b006, b007, b008, b009, b011, b012, b013, b014, b015, b016, b018, b017, count_tree_items

### `env_utils/hierarchy_manager/hierarchy_sidecar.py` — Hierarchy sidecar manifest management — mirror of mayatk's
- `class HierarchySidecar`
  - methods: base_stem, manifest_path_for, diff_report_path_for, find_legacy_manifest, ensure_base_name, rename, build_clean_path_set, expand_to_descendants, get_top_level, detect_reparenting, write_manifest, read_manifest, count_descendants, write_diff_report, clean_stale_diff, build_full_path_set, compare

### `env_utils/hierarchy_manager/tree_renderer.py` — Tree rendering, formatting, and selection management for the hierarchy manager UI — mirror of
- `class HierarchyTreeRenderer(ptk.LoggingMixin)`
  - methods: populate_current_scene_tree, populate_reference_tree, show_reference_placeholder, show_reference_error, populate_tree_with_hierarchy, apply_difference_formatting, clear_tree_colors, format_tree_differences, apply_ignore_styling, build_item_path, find_tree_item_by_name, get_selected_tree_items, get_selected_object_names

### `env_utils/hierarchy_manager/tree_utils.py` — Tree widget utilities for hierarchy manager UI operations — mirror of mayatk's
- `get_selected_object_names(tree_widget) -> List[str]`
- `get_selected_tree_items(tree_widget) -> list`
- `find_tree_item_by_name(tree_widget, object_name: str)`
- `build_hierarchy_structure(objects: list) -> Tuple[Dict[str, Dict], List[str]]`
- `class TreePathMatcher(ptk.LoggingMixin)`
  - methods: build_tree_index, find_path_matches, log_matching_debug, log_tree_index_debug

### `env_utils/maya_bridge/_maya_bridge.py` — Maya bridge engine -- export the Blender selection and run a chosen import template in Maya.
- `list_templates() -> List[Path]`
- `template_modes(template_path: Path) -> Tuple[str, ...]`
- `list_template_modes() -> List[Tuple[str, str]]`
- `class MayaBridge(BlenderExportMixin, ptk.ScriptLaunchBridge)`
  - methods: maya_path, params_defaults, render_context

### `env_utils/maya_bridge/maya_bridge_slots.py` — Slots for the Maya bridge panel.
- `class MayaBridgeSlots(BridgeSlotsBase)`
  - methods: params_module, template_dir, make_bridge, list_template_modes, b000

### `env_utils/maya_bridge/parameters.py` — Registry of user-tunable Maya-bridge parameters exposed to the panel.
- `referenced_keys(script_text: str) -> 'set[str]'`
- `defaults() -> 'dict[str, Any]'`
- `render_context(values: 'dict[str, Any]') -> 'dict[str, str]'`

### `env_utils/maya_bridge/templates/import.py` — Import the bridged FBX into Maya, with optional clean-slate and frame-on-import behaviors.
- `main()`

### `env_utils/reference_manager.py` — Reference Manager tool panel — Switchboard slot wiring for the co-located ``reference_manager.ui``.
- `class ReferenceManagerSlots(ptk.LoggingMixin)`
  - methods: header_init, txt000_init, cmb000_init, txt001_init, tbl000_init, open_selected, save_scene, rename_selected, delete_selected, open_location_selected, reference_selected, reload_selected, relocate_selected, make_local_selected, remove_selected, set_display, reload_all, make_local_all, remove_all

### `env_utils/scene_exporter/_scene_exporter.py` — Scene Exporter engine -- Blender port of mayatk's ``env_utils.scene_exporter``.
- `class SceneExporter(ptk.LoggingMixin)`
  - methods: perform_export, generate_export_path, format_export_name, generate_log_file_path, setup_file_logging, close_file_handlers, list_fbx_presets, fbx_preset_dir, fbx_preset_path, save_fbx_preset, delete_fbx_preset, load_fbx_export_preset, verify_fbx_preset

### `env_utils/scene_exporter/scene_exporter_slots.py` — Slots for the Scene Exporter panel -- Blender port of mayatk's ``SceneExporterSlots``.
- `class SceneExporterSlots(SceneExporter)`
  - methods: workspace, header_init, presets, cmb000_init, txt000_init, txt001_init, cmb001_init, cmb002_init, cmb004_init, b000, b010, b006, b003, b004, b007, b008, save_output_dir, save_output_name

### `env_utils/scene_exporter/task_factory.py` — Generic task/check pipeline engine -- vendored verbatim from mayatk's identically-named
- `class TaskFactory`
  - methods: run_tasks, run_tasks_by_category

### `env_utils/scene_exporter/task_manager.py` — Blender-specific task/check methods for the Scene Exporter pipeline -- mirror of mayatk's
- `class TaskManager(TaskFactory, _TaskActionsMixin, _TaskChecksMixin)`
  - methods: objects, task_definitions, check_definitions, definitions

### `env_utils/script_output.py` — Blender script-output console — the blendertk analogue of mayatk's ``ScriptConsole``.
- `show(*args, **kwargs) -> ScriptConsole`
- `hide(*args, **kwargs) -> None`
- `toggle(*args, **kwargs)`
- `class ScriptConsole`
  - methods: open, close, is_open

### `env_utils/unity_bridge/_unity_bridge.py` — Unity bridge engine -- export the Blender selection into a Unity project's Assets/.
- `list_delivery_modes() -> List[Tuple[str, str]]`
- `class UnityBridge(BlenderExportMixin, ptk.HandoffBridge)`
  - methods: list_template_modes, params_defaults

### `env_utils/unity_bridge/parameters.py` — User-tunable parameters for the Blender->Unity bridge panel -- mirror of mayatk's
- `referenced_keys(script_text: str) -> 'set[str]'`
- `defaults() -> 'dict[str, Any]'`
- `render_context(values: 'dict[str, Any]') -> 'dict[str, str]'`

### `env_utils/unity_bridge/unity_bridge_slots.py` — Slots for the Unity bridge panel -- mirror of mayatk's
- `class UnityBridgeSlots(BlenderBridgeSlotsBase)`
  - methods: params_module, template_dir, make_bridge, list_template_modes, default_output_dir, b000

### `light_utils/_light_utils.py` — Light utilities — the world-environment (HDRI) helpers behind the HDR Manager panel
- `set_world_hdri(filepath=None, strength=None, rotation=0.0, visible=True, intensity=None, exposure=None)`
- `get_world_hdri()`
- `set_world_ray_visibility(diffuse=None, glossy=None)`
- `get_world_ray_visibility()`
- `set_world_importance_resolution(resolution)`
- `get_world_importance_resolution()`
- `clear_world_hdri()`
- `class LightUtils`

### `light_utils/hdr_manager.py` — Blender world-HDRI environment manager.
- `class HdrManagerSlots(ptk.LoggingMixin)`
  - methods: header_init, cmb000_init, set_hdr_folder, hdr_map, hdr_map_visibility, cmb000, slider000, spn_intensity, spn_exposure, spn_resolution, spn_diffuse, spn_specular, add_hdr, open_sourceimages, clear_network, ctx_reveal_in_explorer

### `light_utils/lightmap_baker/lightmap_baker.py` — High-level lightmap baking workflow for Blender -> game engines (Unity-first).
- `class LightmapBaker(ptk.LoggingMixin)`
  - methods: resolution, samples, preset_store, from_preset, bake_fused, bake_separated, commit_lightmap, revert_lightmap, commit_unlit, revert_unlit, revert
- `class LightmapBakerSlots(ptk.LoggingMixin)`
  - methods: header_init, cmb000_init, cmb000, cmb001_init, cmb002_init, cmb_scope_init, cmb_resolution_init, txt000_init, b000, revert_to_source, open_output

### `mat_utils/_mat_utils.py` — Material utilities — mirror of mayatk's ``MatUtils`` public names where the concepts align:
- `get_mats(objects)`
- `create_mat(mat_type='standard', name='')`
- `assign_mat(objects, material)`
- `find_by_mat_id(material, objects=None)`
- `select_by_material(material, add=False)`
- `reload_textures()`
- `get_scene_mats(inc=None, exc=None, sort=False, as_dict=False, exclude_defaults=True, **filter_kwargs)`
- `is_mat_assigned(mat)`
- `get_mat_swatch_icon(mat, size=(20, 20), fallback_to_blank=True)`
- `get_texture_paths(objects=None, materials=None, absolute=True)`
- `get_texture_info(objects=None, materials=None)`
- `get_mat_info(materials=None, objects=None, optimize_check=False, progress_callback=None, exclude_defaults=False, exclude_unassigned=False, include_textures=True, include_image_metadata=True, **optimize_kwargs)`
- `format_mat_info_html(records)`
- `format_texture_info_html(info_list)`
- `find_materials_with_duplicate_textures(materials=None)`
- `reassign_duplicate_materials(duplicate_groups, delete=True)`
- `delete_unused_materials()`
- `graph_materials(materials, mode=None)`
- `get_image_records()`
- `repath_image(image, new_path, reload=True)`
- `to_project_relative(abspath, blenddir=None)`
- `resolve_missing_textures(search_dir, recursive=True, stem=False, texture=False, fuzzy=False, images=None)`
- `normalize_texture_paths(mode='relative', project_dir=None, images=None)`
- `get_image_material_map()`
- `materials_for_textures(paths)`
- `fix_color_spaces(images=None, force_update=False, dry_run=False)`
- `set_texture_directory(images=None, target_dir=None, mode='rewrite')`
- `find_and_copy_textures(images=None, search_dir=None, dest_dir=None, mode='copy')`
- `format_texture_paths_html(records=None)`
- `get_shader_templates()`
- `apply_shader_template(material, template)`
- `create_shader_template(template, name=None)`
- `serialize_material(material)`
- `restore_material(data, name=None, textures=None)`
- `create_pbr_material(textures, name=None, normal_direction='OpenGL')`
- `create_pbr_materials(textures, name=None, normal_direction='OpenGL', prefix='', suffix='')`
- `update_materials(materials=None, config=None, verbose=False, progress_callback=None)`
- `class MatUpdater(ptk.LoggingMixin)`
  - methods: update_materials
- `class MatUtils`

### `mat_utils/arnold_bridge.py` — Arnold render-bridge management -- Blender port of mayatk's ``mat_utils.arnold_bridge``.
- `class ArnoldBridge(ptk.LoggingMixin)`
  - methods: add, remove, rebuild, get_bridge, has_bridge
- `class ArnoldBridgeSlots(ptk.LoggingMixin, ptk.HelpMixin)`
  - methods: header_init, cmb000_init

### `mat_utils/game_shader.py` — Game Shader tool panel — auto-build a Principled-BSDF material from a set of PBR textures.
- `class GameShaderSlots(ptk.LoggingMixin)`
  - methods: workspace_dir, source_images_dir, header_init, lbl_graph_material, mat_name, mat_prefix, mat_suffix, normal_map_type, txt002_init, b000

### `mat_utils/image_to_plane/_image_to_plane.py` — Map image files to textured planes in Blender — port of mayatk's ``mat_utils.image_to_plane``.
- `class ImageToPlane(ptk.LoggingMixin)`
  - methods: create, remove

### `mat_utils/image_to_plane/image_to_plane_slots.py` — Switchboard slots for the Image to Plane UI — port of mayatk's ``ImageToPlaneSlots``.
- `class ImageToPlaneSlots(ptk.LoggingMixin)`
  - methods: header_init, txt_suffix_init

### `mat_utils/marmoset_bridge/_marmoset_bridge.py` — Blender-side glue for the Marmoset Toolbag engine -- mirror of mayatk's
- `build_bake_pairs_manifest(objects: Sequence, high_suffix: str, low_suffix: str) -> Dict[str, str]`
- `class MarmosetBridge(ptk.HandoffBridge)`
  - methods: toolbag_path, params_defaults, render_template

### `mat_utils/marmoset_bridge/_marmoset_engine.py` — Drive Marmoset Toolbag from the outside -- launch + templated automation.
- `list_templates() -> List[Path]`
- `template_modes(template_path: Path) -> Tuple[str, ...]`
- `list_template_modes() -> List[Tuple[str, str]]`
- `class MarmosetEngine(ptk.Deliverer, ptk.LoggingMixin)`
  - methods: toolbag_path, toolbag_log_path, preflight, deliver, send, render_template

### `mat_utils/marmoset_bridge/_toolbag_helpers.py` — Shared helpers for Marmoset Toolbag template scripts.
- `derive_per_run_log_path(manifest_path)`
- `begin_log(reference_path)`
- `log(msg)`
- `find_material(name, scene_mats)`
- `load_manifest(manifest_path)`
- `wire_materials_from_manifest(manifest_path, verbose=True)`
- `split_high_low(objects, high_suffix, low_suffix, pre_classified=None)`
- `collect_mesh_objects(root)`
- `apply_sky_preset(preset_path)`
- `frame_in_viewport()`

### `mat_utils/marmoset_bridge/marmoset_bridge_slots.py` — Slots for the Marmoset Toolbag bridge panel -- mirror of mayatk's
- `class MarmosetBridgeSlots(BlenderBridgeSlotsBase)`
  - methods: params_module, template_dir, make_bridge, list_template_modes, select_initial_template_index, b000

### `mat_utils/marmoset_bridge/marmoset_rpc/connection.py` — JSON-RPC client bound to the marmoset_rpc Toolbag plugin.
- `class MarmosetConnection(RpcClient)`

### `mat_utils/marmoset_bridge/marmoset_rpc/installer.py` — Install the marmoset_rpc plugin into Toolbag's user plugin folder.
- `user_plugin_dir(toolbag_exe: Optional[str] = None) -> Optional[Path]`
- `is_installed(toolbag_exe: Optional[str] = None) -> bool`
- `install(toolbag_exe: Optional[str] = None, force: bool = False) -> Optional[Path]`
- `uninstall(toolbag_exe: Optional[str] = None) -> bool`

### `mat_utils/marmoset_bridge/marmoset_rpc/job.py` — One-shot batch pipeline for the marmoset_rpc bridge.
- `run_batch(calls: List[Call], host: str = '127.0.0.1', port: int = 8765, stop_on_error: bool = False) -> List[Result]`

### `mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/main_thread.py` — Main-thread marshalling for ops that touch Toolbag's API.
- `run_on_main_thread(fn, *args, timeout=_DEFAULT_TIMEOUT, **kwargs)`
- `is_main_thread_marshalling_active()`

### `mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/ops/scene_ops.py` — Scene-inspection ops.
- `summary()`
- `list_materials()`

### `mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/ops/system_ops.py` — System-level ops: heartbeat, introspection, Toolbag version.
- `ping()`
- `list_ops()`
- `describe_op(op='')`
- `version()`

### `mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/registry.py` — Op registry for the marmoset_rpc plugin.
- `register(name)`
- `get(name)`
- `all_ops()`
- `describe(name=None)`
- `clear()`

### `mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/server.py` — HTTP JSON-RPC server for the marmoset_rpc plugin.
- `start_server(port=None, host='127.0.0.1')`
- `stop_server()`
- `is_running()`
- `autostart()`

### `mat_utils/marmoset_bridge/parameters.py` — Registry of user-tunable Marmoset Toolbag parameters exposed to the bridge UI.
- `referenced_keys(script_text: str) -> 'set[str]'`
- `defaults() -> 'dict[str, Any]'`
- `render_context(values: 'dict[str, Any]') -> 'dict[str, str]'`

### `mat_utils/marmoset_bridge/template_params.py` — Plain default values + literal formatting for Marmoset template tokens.
- `python_literal(value: Any) -> str`
- `defaults() -> Dict[str, Any]`
- `to_context(values: Dict[str, Any]) -> Dict[str, str]`

### `mat_utils/marmoset_bridge/templates/bake.py` — Bake high-poly detail into a low-poly target via Marmoset Toolbag.
- `main()`

### `mat_utils/marmoset_bridge/templates/import.py` — Open the model in Toolbag and wire materials from the manifest.
- `main()`

### `mat_utils/marmoset_bridge/templates/lookdev.py` — Open the model in Toolbag, apply a Sky preset, and frame the model.
- `main()`

### `mat_utils/marmoset_bridge/toolbag_log.py` — Marmoset Toolbag log-file resolution, classification, and live tailing.
- `resolve_toolbag_log_path(toolbag_exe: Optional[str]) -> Optional[str]`
- `classify_log_line(line: str) -> Optional[Tuple[str, str]]`
- `dispatch_log_lines(lines, logger) -> None`
- `start_toolbag_log_tail(log_path: str, start_offset: int, process, logger, poll_interval: float = 0.4, file_wait_timeout: float = 60.0)`

### `mat_utils/mat_manifest.py` — Material-to-texture manifest for bridge workflows -- mirror of mayatk's ``mat_utils.mat_manifest``.
- `class MatManifest(ptk.HelpMixin)`
  - methods: build, restore

### `mat_utils/mat_updater.py` — Material Updater tool panel — Switchboard slot wiring for the co-located ``mat_updater.ui``.
- `class MatUpdaterSlots(MatUpdater)`
  - methods: header_init, selection_mode, move_to_folder, max_size, mask_map_scale, output_extension, old_files_folder, cmb001_init, b001

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
- `list_templates() -> List[Path]`
- `parse_template(template_path: Path) -> Dict[str, Any]`
- `list_template_modes() -> List[Tuple[str, str]]`
- `resolve_painter_log_path(painter_exe: Optional[str] = None) -> Optional[str]`
- `class SubstanceBridge(ptk.HandoffBridge)`
  - methods: painter_path, painter_log_path, instances, find_live_managed, send

### `mat_utils/substance_bridge/connection.py` — Substance 3D Painter connection module.
- `find_painter_exe() -> Optional[str]`
- `default_log_path() -> Optional[str]`
- `class OutputStream`
  - methods: push, subscribe, history, clear_history, wait_for, close, closed
- `class SubstanceConnection(ptk.LoggingMixin)`
  - methods: open, close, is_alive, attach

### `mat_utils/substance_bridge/parameters.py` — Registry of user-tunable Substance Painter parameters exposed to the bridge UI.
- `referenced_keys(script_text: str) -> 'set[str]'`
- `defaults() -> 'dict[str, Any]'`
- `render_cli_context(values: 'dict[str, Any]') -> 'dict[str, str]'`
- `render_js_context(values: 'dict[str, Any]') -> 'dict[str, str]'`

### `mat_utils/substance_bridge/substance_bridge_slots.py` — Slots for the Substance Painter bridge panel -- mirror of mayatk's
- `class SubstanceBridgeSlots(BlenderBridgeSlotsBase)`
  - methods: params_module, template_dir, make_bridge, list_template_modes, select_initial_template_index, b000

### `mat_utils/substance_bridge/substance_rpc/client.py` — JSON-RPC 2.0 client for a Painter-side Python plugin.
- `class PainterRpcClient`
  - methods: url, ping, wait_until_ready, call, eval_js

### `mat_utils/texture_baker.py` — Bake an object's shaded surface (material under scene lighting) to a texture — the Blender
- `class TextureBaker(ptk.LoggingMixin)`
  - methods: bake, resolve_meshes, texture_set_stem, default_output_dir

### `mat_utils/texture_path_editor.py` — Texture Path Editor tool panel — Switchboard slot wiring for the co-located
- `class TexturePathEditorSlots(ptk.LoggingMixin)`
  - methods: header_init, tb_set_texture_directory_init, tb_find_and_copy_textures_init, tb_normalize_paths_init, tb_resolve_missing_textures_init, tbl000_init, setup_formatting, open_source_images, reload_scene_textures, tb_set_texture_directory, tb_find_and_copy_textures, tb_normalize_paths, tb_resolve_missing_textures, select_textures_for_objects, select_broken_paths, select_absolute_paths, row_browse_for_file, select_material, select_file_node, row_show_in_hypershade, delete_file_node, handle_cell_edit, refresh_texture_table, cleanup_scene_callbacks

### `node_utils/_node_utils.py` — Node / datablock utilities — instancing via shared object data.
- `get_instances(objects=None)`
- `replace_with_instances(objects, freeze_transforms=False, center_pivot=False, delete_history=False)`
- `uninstance(objects)`
- `get_parent(obj, all=False)`
- `get_children(obj, recursive=False)`
- `get_shape(obj)`
- `reparent(objects, parent, keep_transform=True)`
- `class NodeUtils`

### `node_utils/attributes/channels/_channels.py` — Channels — Blender attribute query / mutation logic.
- `class Channels`
  - methods: is_pinned, single_object_mode, pin_targets, get_selected_nodes, collect_channels, get_channel_value, format_value, parse_value, is_locked, toggle_lock, set_lock, classify_connection, build_table_data, set_channel_value, reset_to_default, toggle_key_at_current_time, break_connections, set_mute, set_breakdown_key, select_connections, create_attribute, delete_attributes, rename_attribute, rename_node, copy_values, paste_values, freeze_transforms, unfreeze_transforms, has_unfreeze_info

### `node_utils/attributes/channels/channels_slots.py` — UI slots for the Channels panel (``channels.ui``).
- `class ChannelsSlots`
  - methods: apply_launch_config, cmb000_init, cmb000, header_init, show_create_menu, tbl000_init

### `node_utils/data_nodes.py` — Scene-wide export-metadata carrier — mirror of mayatk's ``node_utils.data_nodes``.
- `class DataNodes`
  - methods: get_internal_node, ensure_internal, set_internal_string, get_internal_string, get_export_node, ensure_export, set_export_string, get_export_string

### `nurbs_utils/_nurbs_utils.py` — Shared curve helpers — Blender mirror of mayatk's ``nurbs_utils.NurbsUtils`` namespace.
- `class NurbsUtils(ptk.LoggingMixin)`
  - methods: add_spline, create_curve, duplicate_curve, create_plane, curve_to_mesh

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
  - methods: resolve_object, create_locator, create_group, parent_keep_transform, create_armature, add_bone_chain, get_bone_chain_from_root, invert_bone_chain, add_bone_constraint, add_spline_ik, bind_armature, copy_location, copy_rotation, damped_track, track_to, child_of, refresh_drivers, add_distance_driver, add_transform_driver, add_prop_var, add_transform_var, ensure_custom_prop, remove_driver, lock_channels

### `rig_utils/controls.py` — Rig control-shape factory — Blender port of mayatk's ``rig_utils.controls.Controls``.
- `class ControlNodes`
- `class Controls`
  - methods: register_preset, shapes, create

### `rig_utils/shadow_rig.py` — Shadow Rig — engine + Switchboard slot wiring for the co-located ``shadow_rig.ui``.
- `class ShadowRig(ptk.LoggingMixin)`
  - methods: create_contact_locator, get_or_create_shadow_source, create_shadow_plane, create_silhouette_texture, create_material, setup_drivers, create
- `class ShadowRigSlots(ptk.LoggingMixin)`
  - methods: header_init, b001, perform_operation

### `rig_utils/telescope_rig.py` — Telescope Rig — engine + Switchboard slot wiring for the co-located ``telescope_rig.ui``.
- `class TelescopeRig(ptk.LoggingMixin)`
  - methods: setup_telescope_rig
- `class TelescopeRigSlots(ptk.LoggingMixin)`
  - methods: header_init, build_rig

### `rig_utils/tube_path.py` — Tube-mesh centerline extraction — Blender port of mayatk's ``rig_utils.tube_rig.TubePath``.
- `class TubePath`
  - methods: get_centerline, get_selected_edges, get_centerline_using_edges

### `rig_utils/tube_rig.py` — Tube Rig — Blender port of mayatk's ``rig_utils.tube_rig`` (the engine + strategies + panel).
- `register_strategy(cls)`
- `class TubeRigBundle`
- `class TubeStrategy(ABC)`
  - methods: defaults, resolve, build
- `class SplineIKStrategy(TubeStrategy)`
  - methods: build
- `class AnchorStrategy(TubeStrategy)`
  - methods: build
- `class FKChainStrategy(TubeStrategy)`
  - methods: build
- `class TubeRig(ptk.LoggingMixin)`
  - methods: collection, resolve_centerline, create_root, create_armature, create_joint_chain, attach_spline_rig, build_curve, make_control, hook_curve_controls, build
- `class TubeRigSlots(ptk.LoggingMixin)`
  - methods: header_init, b000

### `rig_utils/wheel_rig.py` — Wheel Rig — engine + Switchboard slot wiring for the co-located ``wheel_rig.ui``.
- `class WheelRig(ptk.LoggingMixin)`
  - methods: rig_name, get_drivers, delete_drivers, rig_rotation
- `class WheelRigSlots(ptk.LoggingMixin)`
  - methods: header_init, rig_name, movement_axis, rotation_axis, resolve_selection, set_wheel_height, s000_init, update_rig_name_placeholder, cleanup, wheel_rig, b000

### `ui_utils/_ui_utils.py` — UI utilities — opening Blender editors (the analogue of Maya's editor-window mel commands).
- `get_editor_types()`
- `open_editor(editor, properties_context=None)`
- `menu_exists(menu_idname)`
- `dispatch_log_link(url, logger=None) -> bool`
- `call_native_menu(menu_idname)`
- `class UiUtils`

### `ui_utils/blender_bridge_slots.py` — Blender-flavored :class:`BridgeSlotsBase` -- adds Blender-side defaults.
- `class BlenderBridgeSlotsBase(BridgeSlotsBase)`
  - methods: default_output_dir

### `ui_utils/blender_native_menus.py` — Symbolic-name -> Blender native-menu resolution for the both-button chord menu.
- `class BlenderNativeMenus`
  - methods: names, resolve

### `ui_utils/blender_ui_handler.py`
- `class BlenderUiHandler(UiHandler)`
  - methods: instance, can_resolve, show, apply_styles

### `ui_utils/blender_window.py` — Native-window geometry helpers for hosting a Qt overlay over a Blender window.
- `class BlenderWindow`
  - methods: process_ghost_hwnds, new_ghost_hwnd, is_window, is_iconic, client_origin, client_size, region_screen_rect, set_owner

### `ui_utils/calculator.py` — Calculator tool panel — Switchboard slot wiring for the co-located ``calculator.ui``.
- `class CalculatorController`
  - methods: calculate, convert_unit, get_fps_value, get_current_time, frames_to_sec, sec_to_frames
- `class CalculatorSlots(ptk.LoggingMixin)`
  - methods: header_init, on_input, on_clear, on_backspace, on_equal, on_convert_units, get_fps, get_current_time, frames_to_sec, sec_to_frames

### `ui_utils/style_setter/_style_setter.py` — Match Blender's app UI chrome to another DCC's look using Blender's NATIVE theme-preset system.
- `list_styles()`
- `user_preset_dir(create=False)`
- `user_preset_path(name)`
- `is_installed(name)`
- `install(overwrite=False)`
- `list_templates()`
- `apply_template(filepath)`
- `apply_theme_preset(name)`
- `set_style(name, install_presets=True, persist=False)`
- `class StyleSetter`

### `uv_utils/_uv_utils.py` — UV utilities — UV-coordinate translation and UV-set cleanup (mirror of mayatk's ``UvUtils``
- `move_uvs(objects, du=0.0, dv=0.0)`
- `transform_uvs(objects, flip_u=False, flip_v=False, angle=0.0, per_shell=False)`
- `mirror_uvs(objects, axis='u', per_shell=True, preserve_position=True)`
- `pin_uvs(objects, pin=True, selected_only=True)`
- `get_texel_density(objects, map_size)`
- `set_texel_density(objects, density=1.0, map_size=4096)`
- `delete_extra_uv_sets(objects)`
- `cleanup_uv_sets(objects, *, remove_empty=True, keep_only_primary=False, rename_to_map1=True, force_rename=False, prefer_largest_area=True, dry_run=False)`
- `find_lightmap_uv_set(obj)`
- `create_lightmap_uvs(objects, uv_set=LIGHTMAP_UV_SET, margin=0.02, quiet=True)`
- `get_uv_coords(objects)`
- `set_uv_coords(objects, snapshot)`
- `stack_uv_shells(objects, tolerance=None)`
- `straighten_uv_shells(objects, mode='LENGTH_AVERAGE')`
- `derive_auto_seams(objects, angle=66.0, margin=0.0)`
- `distribute_uv_shells(objects, axis='u')`
- `straighten_uvs(objects, u=True, v=True, angle=30.0)`
- `class UvUtils`

### `uv_utils/rizom_bridge/_rizom_bridge.py` — RizomUV bridge engine — export the selection and open it in a fresh RizomUV session.
- `class RizomUVBridge(ptk.LoggingMixin)`
  - methods: rizom_path, build_send_script, send

### `uv_utils/rizom_bridge/parameters.py` — Registry of user-tunable RizomUV parameters exposed to the bridge UI.
- `referenced_keys(script_text: str) -> 'set[str]'`
- `defaults() -> 'dict[str, Any]'`
- `render_context(values: 'dict[str, Any]') -> 'dict[str, str]'`

### `uv_utils/rizom_bridge/rizom_bridge_slots.py` — Slots for the RizomUV bridge panel.
- `class RizomBridgeSlots(BridgeSlotsBase)`
  - methods: params_module, template_dir, make_bridge, list_template_modes, select_initial_template_index, cmb000_init, refresh_templates, b000, open_uv_editor

### `uv_utils/uv_transform.py` — Dedicated UV shell-transform panel (Blender).
- `class UvTransformSlots(ptk.LoggingMixin)`
  - methods: header_init, b023, b024, b025, b026, b034, b035, b036, b037, s041, tb005_init, tb005, tb006_init, tb006, tb008_init, tb008, open_uv_editor

### `xform_utils/_xform_utils.py` — Transform utilities — object-level transform ops (world bbox, freeze, drop-to-grid,
- `get_world_bbox(obj)`
- `freeze_transforms(objects, location=True, rotation=False, scale=True, store=True)`
- `restore_transforms(objects, delete_attrs=True)`
- `has_stored_transforms(objects)`
- `scale_connected_edges(objects, scale_factor=1.1)`
- `drop_to_grid(objects, align='Min', origin=False, center_pivot=False)`
- `center_pivot(objects, mode='object')`
- `transfer_pivot(objects, translate=True, rotate=False, scale=False, world_space=True, select_targets_after_transfer=False)`
- `get_pivot_modes()`
- `match_scale(source, target, average=True)`
- `move_to(source, target, pivot='center')`
- `get_bounding_box(objects, value='', world_space=True)`
- `get_center_point(objects)`
- `get_operation_axis_matrix(obj, pivot)`
- `get_distance(a, b)`
- `order_by_distance(objects, reference_point=None, reverse=False)`
- `aim_object_at_point(objects, target_pos, aim_vect=(1, 0, 0), up_vect=(0, 1, 0))`
- `class XformUtils`
  - methods: get_pivot_options

### `xform_utils/matrices.py` — Matrix utilities — the Blender counterpart of mayatk's ``xform_utils.matrices``
- `class Matrices`
  - methods: get_matrix, set_matrix, local_matrix, to_matrix, identity, from_srt, compose, decompose, extract_translation, inverse, mult, world_to_local, local_to_world, is_identity
