# blendertk — API Registry

_Auto-generated. Do not edit by hand. Refresh via `m3trik/scripts/generate_api_registry.py`._

_Generated: 2026-07-17_

## Index

- [`anim_utils/_anim_utils.py`](#anim_utils--_anim_utils) — Animation utilities — key-timing math over ``fcurve.keyframe_points`` (mirror of mayatk's
- [`anim_utils/blendshape_animator/_blendshape_animator.py`](#anim_utils--blendshape_animator--_blendshape_animator) — Main workflow facade for shape-key morph creation, editing, and export — mirror of mayatk's
- [`anim_utils/blendshape_animator/applicator.py`](#anim_utils--blendshape_animator--applicator) — Applies tween mesh edits back to the master shape key — mirror of mayatk's
- [`anim_utils/blendshape_animator/blendshape_animator_slots.py`](#anim_utils--blendshape_animator--blendshape_animator_slots) — Switchboard slots controller for the co-located ``blendshape_animator.ui`` — Blender port of
- [`anim_utils/blendshape_animator/creator.py`](#anim_utils--blendshape_animator--creator) — Creates in-between (tween) target meshes for sculpting a custom morph curve — mirror of
- [`anim_utils/blendshape_animator/keyframes.py`](#anim_utils--blendshape_animator--keyframes) — Master shape-key value keyframe animation — mirror of mayatk's
- [`anim_utils/blendshape_animator/target.py`](#anim_utils--blendshape_animator--target) — Tween mesh wrappers and registry — mirror of mayatk's
- [`anim_utils/blendshape_animator/validator.py`](#anim_utils--blendshape_animator--validator) — Mesh + shape-key setup validation — mirror of mayatk's
- [`anim_utils/scale_keys.py`](#anim_utils--scale_keys) — Dedicated scale-keys module to keep AnimUtils lean and testable (mirror of mayatk's
- [`anim_utils/shots/_shots.py`](#anim_utils--shots--_shots) — Blender shot-store adapter — the DCC layer over ``pythontk``'s shots engine.
- [`anim_utils/shots/shot_manifest/_shot_manifest.py`](#anim_utils--shots--shot_manifest--_shot_manifest) — Blender Shot Manifest adapter — the DCC layer over pythontk's manifest engine.
- [`anim_utils/shots/shot_manifest/manifest_data.py`](#anim_utils--shots--shot_manifest--manifest_data) — Constants, column layout, and pure helper functions for the Shot Manifest UI.
- [`anim_utils/shots/shot_manifest/shot_manifest_slots.py`](#anim_utils--shots--shot_manifest--shot_manifest_slots) — Switchboard slots for the Shot Manifest UI (Blender).
- [`anim_utils/shots/shot_manifest/table_presenter.py`](#anim_utils--shots--shot_manifest--table_presenter) — Tree-widget presentation mixin for the Shot Manifest controller.
- [`anim_utils/shots/shot_sequencer/_shot_sequencer.py`](#anim_utils--shots--shot_sequencer--_shot_sequencer) — Blender shot sequencer engine — timeline moves over the shared shots planner.
- [`anim_utils/shots/shot_sequencer/clip_motion.py`](#anim_utils--shots--shot_sequencer--clip_motion) — Clip motion, resize, and key-scaling logic for the shot sequencer (Blender).
- [`anim_utils/shots/shot_sequencer/gap_manager.py`](#anim_utils--shots--shot_sequencer--gap_manager) — Gap and range-highlight handlers for the shot sequencer controller (Blender).
- [`anim_utils/shots/shot_sequencer/marker_manager.py`](#anim_utils--shots--shot_sequencer--marker_manager) — Marker persistence for the shot sequencer controller (Blender).
- [`anim_utils/shots/shot_sequencer/segment_collector.py`](#anim_utils--shots--shot_sequencer--segment_collector) — Segment collection and attribute extraction for the shot sequencer (Blender).
- [`anim_utils/shots/shot_sequencer/shot_nav.py`](#anim_utils--shots--shot_sequencer--shot_nav) — Shot navigation and combobox synchronization (Blender).
- [`anim_utils/shots/shot_sequencer/shot_sequencer_slots.py`](#anim_utils--shots--shot_sequencer--shot_sequencer_slots) — Switchboard slots for the Shot Sequencer UI (Blender).
- [`anim_utils/shots/shots_slots.py`](#anim_utils--shots--shots_slots) — Switchboard slots for the Shots settings UI.
- [`anim_utils/smart_bake/_smart_bake.py`](#anim_utils--smart_bake--_smart_bake) — Smart Bake engine — mirror of mayatk's ``anim_utils.smart_bake._smart_bake`` at the
- [`anim_utils/smart_bake/bake_session.py`](#anim_utils--smart_bake--bake_session) — Persistence and restore engine for SmartBake's nondestructive manifest — mirror of mayatk's
- [`anim_utils/smart_bake/smart_bake_slots.py`](#anim_utils--smart_bake--smart_bake_slots) — Slots for the Smart Bake tool panel (``smart_bake.ui``) — Blender port of mayatk's
- [`anim_utils/stagger_keys.py`](#anim_utils--stagger_keys) — Dedicated stagger-keys module to keep AnimUtils lean and testable (mirror of mayatk's
- [`audio_utils/_audio_utils.py`](#audio_utils--_audio_utils) — Scene-wide audio-clip utilities over Blender's Video Sequence Editor (VSE).
- [`audio_utils/audio_clips.py`](#audio_utils--audio_clips) — Audio Clips — scene-wide sound-strip management over Blender's Video Sequence Editor (VSE).
- [`cam_utils/_cam_utils.py`](#cam_utils--_cam_utils) — Camera utilities — clip-plane adjustment (mirror of mayatk's ``cam_utils``).
- [`core_utils/_core_utils.py`](#core_utils--_core_utils) — Core blendertk utilities — DCC-environment info + cross-cutting decorators.
- [`core_utils/auto_instancer/_auto_instancer.py`](#core_utils--auto_instancer--_auto_instancer) — Scene auto-instancer: convert geometrically identical meshes to instances.
- [`core_utils/auto_instancer/assembly_reconstructor.py`](#core_utils--auto_instancer--assembly_reconstructor) — Logic for separating and reassembling mesh assemblies (bpy adapter).
- [`core_utils/auto_instancer/geometry_matcher.py`](#core_utils--auto_instancer--geometry_matcher) — Geometry analysis and matching logic for AutoInstancer (bpy adapter).
- [`core_utils/auto_instancer/instancing_strategy.py`](#core_utils--auto_instancer--instancing_strategy) — Instancing strategy logic for AutoInstancer (mirror of mayatk's).
- [`core_utils/diagnostics/mesh_diag.py`](#core_utils--diagnostics--mesh_diag) — Mesh diagnostics — the Blender counterpart of mayatk's ``core_utils.diagnostics.mesh_diag``
- [`core_utils/diagnostics/transform_diag.py`](#core_utils--diagnostics--transform_diag) — Transform diagnostics — the Blender counterpart of mayatk's
- [`core_utils/preview.py`](#core_utils--preview) — Live-preview driver for the tentacle Blender tool panels — the Blender analogue of
- [`core_utils/script_job_manager.py`](#core_utils--script_job_manager) — Centralized Blender event-subscription manager — the Blender counterpart of mayatk's
- [`display_utils/_display_utils.py`](#display_utils--_display_utils) — Display utilities — the exploded-view toggle (mirror of mayatk's
- [`display_utils/color_id.py`](#display_utils--color_id) — Color ID tool panel — Switchboard slot wiring for the co-located ``color_id.ui``.
- [`display_utils/exploded_view.py`](#display_utils--exploded_view) — Exploded View — Switchboard slot wiring for the co-located ``exploded_view.ui``.
- [`edit_utils/_curtain_drape.py`](#edit_utils--_curtain_drape) — Procedural draped-cloth (curtain) drape engine — pure geometry, no DCC.
- [`edit_utils/_edit_utils.py`](#edit_utils--_edit_utils) — Mesh-editing utilities — reduce/decimate, coplanar dissolve, triangulate / tris-to-quads,
- [`edit_utils/bevel.py`](#edit_utils--bevel) — Bevel tool — engine + Switchboard slot wiring for the co-located ``bevel.ui``.
- [`edit_utils/bridge.py`](#edit_utils--bridge) — Bridge tool — engine + Switchboard slot wiring for the co-located ``bridge.ui``.
- [`edit_utils/curtain.py`](#edit_utils--curtain) — Curtain (draped-cloth) generation — the Blender build over the vendored
- [`edit_utils/cut_on_axis.py`](#edit_utils--cut_on_axis) — Cut-On-Axis tool panel — Switchboard slot wiring for the co-located ``cut_on_axis.ui``.
- [`edit_utils/duplicate_grid.py`](#edit_utils--duplicate_grid) — Grid array duplication + its tool panel — mirror of mayatk's ``edit_utils.duplicate_grid``.
- [`edit_utils/duplicate_linear.py`](#edit_utils--duplicate_linear) — Linear array duplication + its tool panel — mirror of mayatk's ``edit_utils.duplicate_linear``.
- [`edit_utils/duplicate_radial.py`](#edit_utils--duplicate_radial) — Radial array duplication + its tool panel — mirror of mayatk's ``edit_utils.duplicate_radial``.
- [`edit_utils/dynamic_pipe.py`](#edit_utils--dynamic_pipe) — Dynamic Pipe tool — Blender port of mayatk's ``edit_utils.dynamic_pipe``.
- [`edit_utils/macros.py`](#edit_utils--macros) — Hotkey macros — the Blender counterpart of ``mayatk.edit_utils.macros``.
- [`edit_utils/mirror.py`](#edit_utils--mirror) — Mirror tool panel — Switchboard slot wiring for the co-located ``mirror.ui``.
- [`edit_utils/naming/_naming.py`](#edit_utils--naming--_naming) — Batch object naming — Blender port of mayatk's ``edit_utils.naming.Naming``.
- [`edit_utils/naming/naming_slots.py`](#edit_utils--naming--naming_slots) — Switchboard slots for the Naming panel — Blender port of mayatk's ``NamingSlots``.
- [`edit_utils/selection.py`](#edit_utils--selection) — Category-driven select-by-type — mirror of mayatk's ``edit_utils.selection.Selection``
- [`edit_utils/snap.py`](#edit_utils--snap) — Snap tool — Switchboard slot wiring for the co-located ``snap.ui``.
- [`edit_utils/target_weld.py`](#edit_utils--target_weld) — Target Weld — interactive drag-a-vertex-onto-another merge tool.
- [`env_utils/_env_utils.py`](#env_utils--_env_utils) — blendertk environment / scene-library utilities — the engine behind the Reference Manager panel.
- [`env_utils/blender_connection.py`](#env_utils--blender_connection) — Launch a FRESH headless Blender to run a script / code string and capture its output — the
- [`env_utils/fbx_utils.py`](#env_utils--fbx_utils) — FBX import / export helpers — the Blender counterpart of mayatk's ``env_utils.fbx_utils``
- [`env_utils/handoff_export.py`](#env_utils--handoff_export) — Blender-side selection + FBX-export hooks shared by the hand-off bridge engines.
- [`env_utils/hierarchy_manager/_hierarchy_manager.py`](#env_utils--hierarchy_manager--_hierarchy_manager) — Hierarchy Manager core engine — mirror of mayatk's ``env_utils.hierarchy_manager._hierarchy_manager…
- [`env_utils/hierarchy_manager/hierarchy_manager_slots.py`](#env_utils--hierarchy_manager--hierarchy_manager_slots) — Slots for the Hierarchy Manager panel -- Blender port of mayatk's ``env_utils.hierarchy_manager``.
- [`env_utils/hierarchy_manager/hierarchy_sidecar.py`](#env_utils--hierarchy_manager--hierarchy_sidecar) — Hierarchy sidecar manifest management — mirror of mayatk's
- [`env_utils/hierarchy_manager/tree_renderer.py`](#env_utils--hierarchy_manager--tree_renderer) — Tree rendering, formatting, and selection management for the hierarchy manager UI — mirror of
- [`env_utils/hierarchy_manager/tree_utils.py`](#env_utils--hierarchy_manager--tree_utils) — Tree widget utilities for hierarchy manager UI operations — mirror of mayatk's
- [`env_utils/maya_bridge/_maya_bridge.py`](#env_utils--maya_bridge--_maya_bridge) — Maya bridge engine -- export the Blender selection and run a chosen import template in Maya.
- [`env_utils/maya_bridge/_scene_import.py`](#env_utils--maya_bridge--_scene_import) — Import a Maya scene (.ma/.mb) into Blender via a headless-Maya FBX round-trip.
- [`env_utils/maya_bridge/maya_bridge_slots.py`](#env_utils--maya_bridge--maya_bridge_slots) — Slots for the Maya bridge panel.
- [`env_utils/maya_bridge/parameters.py`](#env_utils--maya_bridge--parameters) — Registry of user-tunable Maya-bridge parameters exposed to the panel.
- [`env_utils/maya_bridge/templates/_import_scene.py`](#env_utils--maya_bridge--templates--_import_scene) — Open a Maya scene headlessly (mayapy) and export it as FBX for a Blender import.
- [`env_utils/maya_bridge/templates/import.py`](#env_utils--maya_bridge--templates--import) — Import the bridged FBX into Maya, with optional clean-slate and frame-on-import behaviors.
- [`env_utils/reference_manager.py`](#env_utils--reference_manager) — Reference Manager tool panel — Switchboard slot wiring for the co-located ``reference_manager.ui``.
- [`env_utils/scene_exporter/_scene_exporter.py`](#env_utils--scene_exporter--_scene_exporter) — Scene Exporter engine -- Blender port of mayatk's ``env_utils.scene_exporter``.
- [`env_utils/scene_exporter/scene_exporter_slots.py`](#env_utils--scene_exporter--scene_exporter_slots) — Slots for the Scene Exporter panel -- Blender port of mayatk's ``SceneExporterSlots``.
- [`env_utils/scene_exporter/task_manager.py`](#env_utils--scene_exporter--task_manager) — Blender-specific task/check methods for the Scene Exporter pipeline -- mirror of mayatk's
- [`env_utils/script_output.py`](#env_utils--script_output) — Blender script-output console — the blendertk analogue of mayatk's ``ScriptConsole``.
- [`env_utils/unity_bridge/_unity_bridge.py`](#env_utils--unity_bridge--_unity_bridge) — Unity bridge engine -- export the Blender selection into a Unity project's Assets/.
- [`env_utils/unity_bridge/parameters.py`](#env_utils--unity_bridge--parameters) — User-tunable parameters for the Blender->Unity bridge panel -- mirror of mayatk's
- [`env_utils/unity_bridge/unity_bridge_slots.py`](#env_utils--unity_bridge--unity_bridge_slots) — Slots for the Unity bridge panel -- mirror of mayatk's
- [`light_utils/_light_utils.py`](#light_utils--_light_utils) — Light utilities — the world-environment (HDRI) helpers behind the HDR Manager panel
- [`light_utils/hdr_manager.py`](#light_utils--hdr_manager) — Blender world-HDRI environment manager.
- [`light_utils/lightmap_baker/lightmap_baker.py`](#light_utils--lightmap_baker--lightmap_baker) — High-level lightmap baking workflow for Blender -> game engines (Unity-first).
- [`mat_utils/_mat_utils.py`](#mat_utils--_mat_utils) — Material utilities — mirror of mayatk's ``MatUtils`` public names where the concepts align:
- [`mat_utils/arnold_bridge.py`](#mat_utils--arnold_bridge) — Arnold render-bridge management -- Blender port of mayatk's ``mat_utils.arnold_bridge``.
- [`mat_utils/game_shader.py`](#mat_utils--game_shader) — Game Shader tool panel — auto-build a Principled-BSDF material from a set of PBR textures.
- [`mat_utils/image_to_plane/_image_to_plane.py`](#mat_utils--image_to_plane--_image_to_plane) — Map image files to textured planes in Blender — port of mayatk's ``mat_utils.image_to_plane``.
- [`mat_utils/image_to_plane/image_to_plane_slots.py`](#mat_utils--image_to_plane--image_to_plane_slots) — Switchboard slots for the Image to Plane UI — port of mayatk's ``ImageToPlaneSlots``.
- [`mat_utils/marmoset_bridge/_marmoset_bridge.py`](#mat_utils--marmoset_bridge--_marmoset_bridge) — Blender-side glue for the Marmoset Toolbag engine -- mirror of mayatk's
- [`mat_utils/marmoset_bridge/_marmoset_engine.py`](#mat_utils--marmoset_bridge--_marmoset_engine) — Drive Marmoset Toolbag from the outside -- launch + templated automation.
- [`mat_utils/marmoset_bridge/_toolbag_helpers.py`](#mat_utils--marmoset_bridge--_toolbag_helpers) — Shared helpers for Marmoset Toolbag template scripts.
- [`mat_utils/marmoset_bridge/marmoset_bridge_slots.py`](#mat_utils--marmoset_bridge--marmoset_bridge_slots) — Slots for the Marmoset Toolbag bridge panel -- mirror of mayatk's
- [`mat_utils/marmoset_bridge/marmoset_rpc/connection.py`](#mat_utils--marmoset_bridge--marmoset_rpc--connection) — JSON-RPC client bound to the marmoset_rpc Toolbag plugin.
- [`mat_utils/marmoset_bridge/marmoset_rpc/installer.py`](#mat_utils--marmoset_bridge--marmoset_rpc--installer) — Install the marmoset_rpc plugin into Toolbag's user plugin folder.
- [`mat_utils/marmoset_bridge/marmoset_rpc/job.py`](#mat_utils--marmoset_bridge--marmoset_rpc--job) — One-shot batch pipeline for the marmoset_rpc bridge.
- [`mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/main_thread.py`](#mat_utils--marmoset_bridge--marmoset_rpc--plugin_src--marmoset_rpc--main_thread) — Main-thread marshalling for ops that touch Toolbag's API.
- [`mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/ops/scene_ops.py`](#mat_utils--marmoset_bridge--marmoset_rpc--plugin_src--marmoset_rpc--ops--scene_ops) — Scene-inspection ops.
- [`mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/ops/system_ops.py`](#mat_utils--marmoset_bridge--marmoset_rpc--plugin_src--marmoset_rpc--ops--system_ops) — System-level ops: heartbeat, introspection, Toolbag version.
- [`mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/registry.py`](#mat_utils--marmoset_bridge--marmoset_rpc--plugin_src--marmoset_rpc--registry) — Op registry for the marmoset_rpc plugin.
- [`mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/server.py`](#mat_utils--marmoset_bridge--marmoset_rpc--plugin_src--marmoset_rpc--server) — HTTP JSON-RPC server for the marmoset_rpc plugin.
- [`mat_utils/marmoset_bridge/parameters.py`](#mat_utils--marmoset_bridge--parameters) — Registry of user-tunable Marmoset Toolbag parameters exposed to the bridge UI.
- [`mat_utils/marmoset_bridge/template_params.py`](#mat_utils--marmoset_bridge--template_params) — Plain default values + literal formatting for Marmoset template tokens.
- [`mat_utils/marmoset_bridge/templates/bake.py`](#mat_utils--marmoset_bridge--templates--bake) — Bake high-poly detail into a low-poly target via Marmoset Toolbag.
- [`mat_utils/marmoset_bridge/templates/import.py`](#mat_utils--marmoset_bridge--templates--import) — Open the model in Toolbag and wire materials from the manifest.
- [`mat_utils/marmoset_bridge/templates/lookdev.py`](#mat_utils--marmoset_bridge--templates--lookdev) — Open the model in Toolbag, apply a Sky preset, and frame the model.
- [`mat_utils/marmoset_bridge/toolbag_log.py`](#mat_utils--marmoset_bridge--toolbag_log) — Marmoset Toolbag log-file resolution, classification, and live tailing.
- [`mat_utils/mat_manifest.py`](#mat_utils--mat_manifest) — Material-to-texture manifest for bridge workflows -- mirror of mayatk's ``mat_utils.mat_manifest``.
- [`mat_utils/mat_updater.py`](#mat_utils--mat_updater) — Material Updater tool panel — Switchboard slot wiring for the co-located ``mat_updater.ui``.
- [`mat_utils/render_opacity/_render_opacity.py`](#mat_utils--render_opacity--_render_opacity) — Render Opacity — Blender per-object opacity for engine-ready transparency (mirror of mayatk's
- [`mat_utils/render_opacity/render_opacity_slots.py`](#mat_utils--render_opacity--render_opacity_slots) — Switchboard slots for the Render Opacity panel (``render_opacity.ui``).
- [`mat_utils/shader_templates.py`](#mat_utils--shader_templates) — Shader Templates tool panel — Switchboard slot wiring for the co-located
- [`mat_utils/substance_bridge/_substance_bridge.py`](#mat_utils--substance_bridge--_substance_bridge) — Substance 3D Painter bridge -- export Blender selection and hand off to Painter.
- [`mat_utils/substance_bridge/connection.py`](#mat_utils--substance_bridge--connection) — Substance 3D Painter connection module.
- [`mat_utils/substance_bridge/parameters.py`](#mat_utils--substance_bridge--parameters) — Registry of user-tunable Substance Painter parameters exposed to the bridge UI.
- [`mat_utils/substance_bridge/substance_bridge_slots.py`](#mat_utils--substance_bridge--substance_bridge_slots) — Slots for the Substance Painter bridge panel -- mirror of mayatk's
- [`mat_utils/substance_bridge/substance_rpc/client.py`](#mat_utils--substance_bridge--substance_rpc--client) — JSON-RPC 2.0 client for a Painter-side Python plugin.
- [`mat_utils/texture_baker.py`](#mat_utils--texture_baker) — Bake an object's shaded surface (material under scene lighting) to a texture — the Blender
- [`mat_utils/texture_path_editor.py`](#mat_utils--texture_path_editor) — Texture Path Editor tool panel — Switchboard slot wiring for the co-located
- [`node_utils/_node_utils.py`](#node_utils--_node_utils) — Node / datablock utilities — instancing via shared object data.
- [`node_utils/attributes/channels/_channels.py`](#node_utils--attributes--channels--_channels) — Channels — Blender attribute query / mutation logic.
- [`node_utils/attributes/channels/channels_slots.py`](#node_utils--attributes--channels--channels_slots) — UI slots for the Channels panel (``channels.ui``).
- [`node_utils/data_nodes.py`](#node_utils--data_nodes) — Scene-wide export-metadata carrier — mirror of mayatk's ``node_utils.data_nodes``.
- [`nurbs_utils/_nurbs_utils.py`](#nurbs_utils--_nurbs_utils) — Shared curve helpers — Blender mirror of mayatk's ``nurbs_utils.NurbsUtils`` namespace.
- [`nurbs_utils/curve_to_tube.py`](#nurbs_utils--curve_to_tube) — Curve to Tube tool — Blender port of mayatk's ``nurbs_utils.curve_to_tube``.
- [`nurbs_utils/image_tracer.py`](#nurbs_utils--image_tracer) — Image Tracer tool — Blender port of mayatk's ``nurbs_utils.image_tracer``.
- [`rig_utils/_rig_utils.py`](#rig_utils--_rig_utils) — Shared procedural-rig primitives — Blender port of mayatk's ``rig_utils.RigUtils``.
- [`rig_utils/controls.py`](#rig_utils--controls) — Rig control-shape factory — Blender port of mayatk's ``rig_utils.controls.Controls``.
- [`rig_utils/shadow_rig.py`](#rig_utils--shadow_rig) — Shadow Rig — engine + Switchboard slot wiring for the co-located ``shadow_rig.ui``.
- [`rig_utils/telescope_rig.py`](#rig_utils--telescope_rig) — Telescope Rig — engine + Switchboard slot wiring for the co-located ``telescope_rig.ui``.
- [`rig_utils/tube_path.py`](#rig_utils--tube_path) — Tube-mesh centerline extraction — Blender port of mayatk's ``rig_utils.tube_rig.TubePath``.
- [`rig_utils/tube_rig.py`](#rig_utils--tube_rig) — Tube Rig — Blender port of mayatk's ``rig_utils.tube_rig`` (the engine + strategies + panel).
- [`rig_utils/wheel_rig.py`](#rig_utils--wheel_rig) — Wheel Rig — engine + Switchboard slot wiring for the co-located ``wheel_rig.ui``.
- [`ui_utils/_ui_utils.py`](#ui_utils--_ui_utils) — UI utilities — opening Blender editors (the analogue of Maya's editor-window mel commands).
- [`ui_utils/blender_bridge_slots.py`](#ui_utils--blender_bridge_slots) — Blender-flavored :class:`BridgeSlotsBase` -- adds Blender-side defaults.
- [`ui_utils/blender_native_menus.py`](#ui_utils--blender_native_menus) — Symbolic-name -> Blender native-menu resolution + Qt wrapping for the both-button chord menu.
- [`ui_utils/blender_ui_handler.py`](#ui_utils--blender_ui_handler)
- [`ui_utils/blender_window.py`](#ui_utils--blender_window) — Native-window (win32/GHOST) helpers for hosting Qt widgets around a Blender window.
- [`ui_utils/calculator.py`](#ui_utils--calculator) — Calculator tool panel — Switchboard slot wiring for the co-located ``calculator.ui``.
- [`ui_utils/menu_harvest.py`](#ui_utils--menu_harvest) — Harvest a native Blender menu into a live ``QMenu`` — the Blender half of Maya's wrap.
- [`ui_utils/qt_dock.py`](#ui_utils--qt_dock) — Dock any Qt widget into a native Blender area — a true child window, not an overlay.
- [`ui_utils/style_setter/_style_setter.py`](#ui_utils--style_setter--_style_setter) — Match Blender's app UI chrome to another DCC's look using Blender's NATIVE theme-preset system.
- [`uv_utils/_uv_utils.py`](#uv_utils--_uv_utils) — UV utilities — UV-coordinate translation and UV-set cleanup (mirror of mayatk's ``UvUtils``
- [`uv_utils/rizom_bridge/_rizom_bridge.py`](#uv_utils--rizom_bridge--_rizom_bridge) — RizomUV bridge engine — Blender mirror of mayatk's ``RizomUVBridge``.
- [`uv_utils/rizom_bridge/parameters.py`](#uv_utils--rizom_bridge--parameters) — Registry of user-tunable RizomUV parameters exposed to the bridge UI.
- [`uv_utils/rizom_bridge/rizom_bridge_slots.py`](#uv_utils--rizom_bridge--rizom_bridge_slots) — Slots for the RizomUV bridge panel.
- [`uv_utils/shell_xform.py`](#uv_utils--shell_xform) — Dedicated UV shell-transform panel (Blender).
- [`xform_utils/_xform_utils.py`](#xform_utils--_xform_utils) — Transform utilities — object-level transform ops (world bbox, freeze, drop-to-grid,
- [`xform_utils/matrices.py`](#xform_utils--matrices) — Matrix utilities — the Blender counterpart of mayatk's ``xform_utils.matrices``

---

<a id="anim_utils--_anim_utils"></a>
### `anim_utils/_anim_utils.py`

Animation utilities — key-timing math over ``fcurve.keyframe_points`` (mirror of mayatk's

- [`get_fcurves(objects)`](blendertk/blendertk/anim_utils/_anim_utils.py#L55) — All fcurves across the given objects' actions (slot-aware;
- [`scene_has_animation()`](blendertk/blendertk/anim_utils/_anim_utils.py#L63) — True if the blend file contains any action carrying fcurves (keyed motion).
- [`set_current_frame(time=None, update=True, relative=False, snap_mode=None, invert_snap=False)`](blendertk/blendertk/anim_utils/_anim_utils.py#L80) — Set the scene's current frame, with optional relative offset and clean-number snapping —
- [`shift_keys(objects, offset)`](blendertk/blendertk/anim_utils/_anim_utils.py#L145) — Shift every key of the given objects by ``offset`` frames.
- [`move_keys_to_frame(objects, frame=None, retain_spacing=True, selected_keys_only=False, align='auto')`](blendertk/blendertk/anim_utils/_anim_utils.py#L150) — Move the objects' keys so they align to ``frame`` (default: the current frame).
- [`adjust_key_spacing(objects, spacing=1, frame=None, relative=False, preserve_keys=False, selected_keys_only=False, exact_gap=False)`](blendertk/blendertk/anim_utils/_anim_utils.py#L216) — Add (+) or remove (−) ``spacing`` frames of space at ``frame`` (default: the current
- [`align_selected_keyframes(objects, target_frame=None, use_earliest=True)`](blendertk/blendertk/anim_utils/_anim_utils.py#L307) — Move the SELECTED keyframes (``select_control_point``, e.g.
- [`set_visibility_keys(objects, visible=True, frame=None, when='current', offset=0, group_overlapping=False)`](blendertk/blendertk/anim_utils/_anim_utils.py#L372) — Key viewport + render visibility (``hide_viewport``/``hide_render``) — mirror of
- [`add_intermediate_keys(objects, step=1.0, time_range=None, ignore_visibility=False, percent=None)`](blendertk/blendertk/anim_utils/_anim_utils.py#L424) — Insert sampled keys every ``step`` frames between each fcurve's first and last key
- [`remove_intermediate_keys(objects, time_range=None, ignore_visibility=False)`](blendertk/blendertk/anim_utils/_anim_utils.py#L486) — Remove every key strictly between each fcurve's first and last (keeps only the
- [`select_keys(objects, time=None, add_to_selection=False)`](blendertk/blendertk/anim_utils/_anim_utils.py#L511) — Select keyframe points (``select_control_point`` — visible in the Dope Sheet /
- [`invert_keys(objects, mode='time', value_pivot=0.0, start_frame=None, relative=True, delete_original=False)`](blendertk/blendertk/anim_utils/_anim_utils.py#L546) — Mirror keys to reverse motion — Blender analogue of Maya's invert (modes mirror its X/Y/both
- [`snap_keys(objects, selected_only=False, time_range=None, method='nearest')`](blendertk/blendertk/anim_utils/_anim_utils.py#L643) — Snap keys to whole frames (or "clean" numbers) — mirror of ``mtk.snap_keys_to_frames``.
- [`set_interpolation(objects, interpolation='CONSTANT', handle=None)`](blendertk/blendertk/anim_utils/_anim_utils.py#L675) — Set fcurve key ``interpolation`` (``CONSTANT`` / ``LINEAR`` / ``BEZIER`` / ``SINE`` …) on
- [`set_stepped(objects, stepped=True)`](blendertk/blendertk/anim_utils/_anim_utils.py#L692) — Set stepped (CONSTANT) or smooth (BEZIER) interpolation on every key.
- [`delete_keys(objects, time=None)`](blendertk/blendertk/anim_utils/_anim_utils.py#L706) — Remove animation from the given objects — mirror of ``mtk.delete_keys``.
- [`fit_playback_range(objects=None)`](blendertk/blendertk/anim_utils/_anim_utils.py#L748) — Set the scene frame range to the keyed extent of ``objects`` (or every scene object).
- [`copy_keys(source, mode='action')`](blendertk/blendertk/anim_utils/_anim_utils.py#L765) — Return a copy-buffer for :func:`paste_keys` — mirror of ``mtk.AnimUtils.copy_keys`` (same
- [`paste_keys(objects, buffer, target_time=None)`](blendertk/blendertk/anim_utils/_anim_utils.py#L802) — Paste a copy-buffer from :func:`copy_keys` onto ``objects`` — mirror of
- [`transfer_keyframes(objects, relative=False, optimize=False)`](blendertk/blendertk/anim_utils/_anim_utils.py#L887) — Transfer keyframes from the first object (source) onto the rest (targets) — mirror of
- [`optimize_keys(objects=None, value_tolerance=0.001, remove_static_curves=True, remove_flat_keys=True, simplify_keys=False, stats=None)`](blendertk/blendertk/anim_utils/_anim_utils.py#L1070) — Remove redundant animation data — mirror of ``mtk.AnimUtils.optimize_keys``.
- [`repair_corrupted_curves(objects=None, *, delete_unfixable=True, fix_infinite=True, fix_invalid_times=True, time_threshold=100000.0, value_threshold=1000000.0)`](blendertk/blendertk/anim_utils/_anim_utils.py#L1120) — Detect and repair corrupted animation fcurves — mirror of
- [`tie_keyframes(objects=None, untie=False, frame_range=None, absolute=False)`](blendertk/blendertk/anim_utils/_anim_utils.py#L1189) — Add (tie) or remove (untie) bookend keys at the playback-range boundaries — mirror of
- [`bake_keys(objects=None, frame_range=None, step=1, only_selected=False, visual_keying=True, clear_constraints=False, clear_parents=False, use_current_action=True, bake_types=None)`](blendertk/blendertk/anim_utils/_anim_utils.py#L1242) — Bake animation to plain keyframes — the Blender analogue of Maya's Smart Bake (wraps the
- [`bake_blend_shapes(objects=None, frame_range=None, step=1)`](blendertk/blendertk/anim_utils/_anim_utils.py#L1300) — Bake driven/animated blend-shape (shape-key) weights to explicit keyframes — the Blender
- [`get_animation_info(objects=None, by_time=False, ignore_holds=False)`](blendertk/blendertk/anim_utils/_anim_utils.py#L1383) — Per-object animation summary — mirror of ``mtk`` get-animation-info.
- [`format_animation_info_csv(records)`](blendertk/blendertk/anim_utils/_anim_utils.py#L1423) — Render :func:`get_animation_info` records as CSV (paste into a spreadsheet) — mirror of
- [`format_animation_info_html(records)`](blendertk/blendertk/anim_utils/_anim_utils.py#L1449) — Render :func:`get_animation_info` records as an HTML table for the text-view dialog.
- [`configure_render_output(scene, file_format='PNG', container=None, codec=None, quality=None)`](blendertk/blendertk/anim_utils/_anim_utils.py#L1486) — Apply playblast/render output settings to ``scene.render`` — the engine behind the rendering
- **[`class AnimUtils`](blendertk/blendertk/anim_utils/_anim_utils.py#L1523)** — Namespace mirror (helpers also exposed module-level).

<a id="anim_utils--blendshape_animator--_blendshape_animator"></a>
### `anim_utils/blendshape_animator/_blendshape_animator.py`

Main workflow facade for shape-key morph creation, editing, and export — mirror of mayatk's

- **[`class BlendshapeAnimator(ptk.LoggingMixin)`](blendertk/blendertk/anim_utils/blendshape_animator/_blendshape_animator.py#L43)** — Main workflow facade for shape-key morph animation.
  - `BlendshapeAnimator.create(self, base_obj=None, target_obj=None, start_frame: Optional[int] = None, end_frame: Optional[int] = None, name: str = 'morph', test_setup: bool = True) -> bool` — Set up a basic morph animation between two mesh objects.
  - `BlendshapeAnimator.edit_weight_based(self, weights: Optional[List[float]] = None, count: int = 3, weight_range: Tuple[float, float] = (0.0, 1.0)) -> List[Target]` — Create tweens at specific weights or evenly spaced.
  - `BlendshapeAnimator.edit_frame_based(self, frames: Optional[List[int]] = None, target_frame: Optional[int] = None) -> List[Target]` — Create tweens at specific animation frames.
  - `BlendshapeAnimator.edit_apply_tweens(self, tweens: Optional[List[Target]] = None) -> List[Target]` — Apply tween mesh edits back to the master shape key's correctives.
  - `BlendshapeAnimator.basic_workflow(cls, base_obj=None, target_obj=None, start_frame: Optional[int] = None, end_frame: Optional[int] = None, name: str = 'morph') -> Optional['BlendshapeAnimator']` *(class)* — Complete basic workflow: create setup with tweens ready for editing.
  - `BlendshapeAnimator.apply_all_edits(self) -> bool` — Apply all tween edits to the current setup.
  - `BlendshapeAnimator.finalize_for_export(self, cleanup_scene: bool = True, delete_construction_history: bool = True, hide_target_mesh: bool = True, delete_inbetween_meshes: bool = True) -> bool` — Finalize the morph animation and clean up the scene for export.
  - `BlendshapeAnimator.from_existing(cls, base_obj=None) -> Optional['BlendshapeAnimator']` *(class)* — Create an animator bound to an existing shape-key setup on ``base_obj``.
  - `BlendshapeAnimator.recover_animation(self) -> bool` — Recover lost animation keyframes on the master shape key's value.
  - `BlendshapeAnimator.diagnose_topology_issues(self) -> bool` — Diagnose topology mismatches between the base mesh and in-between meshes.
  - `BlendshapeAnimator.cleanup_topology_mismatches(self, delete_mismatched: bool = True, apply_valid_only: bool = True) -> bool` — Clean up topology mismatches by deleting bad meshes and applying good ones.
  - `BlendshapeAnimator.remove_target_for_export(self) -> bool` — Remove the target mesh object for a clean export.

<a id="anim_utils--blendshape_animator--applicator"></a>
### `anim_utils/blendshape_animator/applicator.py`

Applies tween mesh edits back to the master shape key — mirror of mayatk's

- **[`class ApplyStatus(Enum)`](blendertk/blendertk/anim_utils/blendshape_animator/applicator.py#L78)**
- **[`class Applicator(ptk.LoggingMixin)`](blendertk/blendertk/anim_utils/blendshape_animator/applicator.py#L84)** — Applies tween mesh edits to the master shape key's corrective in-betweens.
  - `Applicator.validate_topology(self, tweens: List[Target]) -> List[Target]` — Filter ``tweens`` to those matching the base mesh's vertex count.
  - `Applicator.apply_tweens(self, tweens: Optional[List[Target]] = None, skip_duplicates: bool = True, validate_topology: bool = False) -> List[Tuple[Target, ApplyStatus]]` — Apply tween mesh edits to corrective in-between shape keys.

<a id="anim_utils--blendshape_animator--blendshape_animator_slots"></a>
### `anim_utils/blendshape_animator/blendshape_animator_slots.py`

Switchboard slots controller for the co-located ``blendshape_animator.ui`` — Blender port of

- **[`class BlendshapeAnimatorSlots(BlendshapeAnimator)`](blendertk/blendertk/anim_utils/blendshape_animator/blendshape_animator_slots.py#L68)** — Controller wiring blendshape_animator.ui to the BlendshapeAnimator domain class.
  - `BlendshapeAnimatorSlots.header_init(self, widget) -> None` — Configure header buttons + about menu.
  - `BlendshapeAnimatorSlots.b000_init(self, widget) -> None` — Create Setup button — option_box exposes an alternative entrypoint.
  - `BlendshapeAnimatorSlots.b000(self, widget) -> None` — Create Setup.
  - `BlendshapeAnimatorSlots.cmb000_init(self, widget) -> None` — Populate the edit-mode combo.
  - `BlendshapeAnimatorSlots.le001_init(self, widget) -> None` — CSV weights field — option_box menu offers preset lists.
  - `BlendshapeAnimatorSlots.b001_init(self, widget) -> None` — Add Tweens — option_box exposes count + group / prefix overrides.
  - `BlendshapeAnimatorSlots.b001(self, widget) -> None` — Add Tweens — dispatches by mode.
  - `BlendshapeAnimatorSlots.b003(self, widget) -> None` — Diagnose Topology.
  - `BlendshapeAnimatorSlots.b004_init(self, widget) -> None` — Cleanup Topology Mismatches — option_box for the two flags.
  - `BlendshapeAnimatorSlots.b004(self, widget) -> None` — Clean up in-between meshes whose topology doesn't match the base mesh.
  - `BlendshapeAnimatorSlots.b005(self, widget) -> None` — Recover Animation.
  - `BlendshapeAnimatorSlots.b006_init(self, widget) -> None` — Apply All Edits — option_box for skip_duplicates, validate_topology.
  - `BlendshapeAnimatorSlots.b006(self, widget) -> None` — Apply All Edits — bulk apply with optional flags from the option_box.
  - `BlendshapeAnimatorSlots.b007(self, widget) -> None` — Remove Target Mesh.
  - `BlendshapeAnimatorSlots.b008_init(self, widget) -> None` — Finalize for Export — option_box for the four boolean flags.
  - `BlendshapeAnimatorSlots.b008(self, widget) -> None` — Finalize the shape-key setup for export (scene cleanup, hide source).

<a id="anim_utils--blendshape_animator--creator"></a>
### `anim_utils/blendshape_animator/creator.py`

Creates in-between (tween) target meshes for sculpting a custom morph curve — mirror of

- **[`class Creator(ptk.LoggingMixin)`](blendertk/blendertk/anim_utils/blendshape_animator/creator.py#L22)** — Creates in-between target mesh objects for custom morph curves.
  - `Creator.create_weight_based_tweens(self, weights: List[float], group_name: str = Targets.GROUP_NAME, name_prefix: str = 'morph_ib') -> List[Target]` — Create tween meshes at specific weight values.
  - `Creator.create_frame_based_tween(self, target_frame: int) -> Optional[Target]` — Create a tween mesh at a specific animation frame.
  - `Creator.tag_tween_mesh(self, obj, weight: float, target_frame: Optional[int] = None) -> None` — Add metadata custom properties to ``obj``.
  - `Creator.get_existing_weights(self) -> Set[float]` — All in-between weights known for the current master shape key.
  - `Creator.find_nearby_weight(self, target_weight: float, existing_weights: Set[float], tolerance: float = 0.01) -> Optional[float]` — Find a nearby weight that doesn't conflict with existing weights.

<a id="anim_utils--blendshape_animator--keyframes"></a>
### `anim_utils/blendshape_animator/keyframes.py`

Master shape-key value keyframe animation — mirror of mayatk's

- [`preserve_sibling_values(key_id)`](blendertk/blendertk/anim_utils/blendshape_animator/keyframes.py#L22) — Snapshot every *un-driven* key block's ``value`` on ``key_id`` and restore it on exit.
- **[`class Keyframes(ptk.LoggingMixin)`](blendertk/blendertk/anim_utils/blendshape_animator/keyframes.py#L55)** — Core shape-key value animation functionality.
  - `Keyframes.key_id(self)` *(property)* — The mesh's ``Key`` ID datablock (``mesh.shape_keys``) — the animatable owner of
  - `Keyframes.key_block(self)` *(property)*
  - `Keyframes.create_keyframes(self, start_frame: int, end_frame: int) -> bool` — Create linear keyframe animation on the master key's value, 0.0 -> 1.0.
  - `Keyframes.test_morph(self) -> bool` — Test the shape key by temporarily setting its value to 0.5.
  - `Keyframes.get_frame_range(self) -> Tuple[int, int]` — Return (start, end) frame range from keyframes on the master key's value.

<a id="anim_utils--blendshape_animator--target"></a>
### `anim_utils/blendshape_animator/target.py`

Tween mesh wrappers and registry — mirror of mayatk's

- **[`class Target`](blendertk/blendertk/anim_utils/blendshape_animator/target.py#L30)** — Represents a single tween (in-between) target mesh object.
  - `Target.mesh(self) -> str` *(property)* — The tween object's name (string) — mirrors mayatk's ``Target.mesh`` string attribute.
  - `Target.weight(self) -> float` *(property)*
  - `Target.key_block_name(self) -> str` *(property)* — The master shape key this tween belongs to (mirrors mayatk's ``blendshape_name``).
  - `Target.base_mesh_name(self) -> str` *(property)*
  - `Target.target_frame(self) -> Optional[int]` *(property)*
  - `Target.update_references(self, new_key_block_name: str, new_base_mesh_name: str) -> None` — Update this tween's references to a new master shape key / base mesh.
- **[`class Targets(ptk.LoggingMixin)`](blendertk/blendertk/anim_utils/blendshape_animator/target.py#L74)** — Manages collections of tween mesh objects.
  - `Targets.find_all_targets(cls, key_block_name: Optional[str] = None, base_mesh_name: Optional[str] = None) -> List[Target]` *(class)* — Find tagged tween mesh objects in the scene (deduplicated).
  - `Targets.group_by_weight(cls, tweens: List[Target]) -> Dict[float, List[Target]]` *(class)* — Group tweens by weight value, handling duplicates.
  - `Targets.update_all_references(cls, new_key_block_name: str, new_base_mesh_name: str, old_key_block_name: Optional[str] = None, old_base_mesh_name: Optional[str] = None) -> int` *(class)* — Update tween mesh references to a new master shape key / base mesh.

<a id="anim_utils--blendshape_animator--validator"></a>
### `anim_utils/blendshape_animator/validator.py`

Mesh + shape-key setup validation — mirror of mayatk's

- **[`class Validator(ptk.LoggingMixin)`](blendertk/blendertk/anim_utils/blendshape_animator/validator.py#L9)** — Handles validation of meshes and shape-key setups.
  - `Validator.validate_meshes(cls, obj1, obj2) -> bool` *(class)* — Validate that both objects are compatible mesh objects with matching vertex counts.
  - `Validator.validate_shape_setup(cls, base_obj, key_name: str) -> bool` *(class)* — Validate the master shape key exists (Blender analogue of mayatk's blendShape

<a id="anim_utils--scale_keys"></a>
### `anim_utils/scale_keys.py`

Dedicated scale-keys module to keep AnimUtils lean and testable (mirror of mayatk's

- [`scale_keys(objects, factor, pivot=None, mode='uniform', absolute=False, group_mode='single_group', snap_mode='none', samples=64, include_rotation=False, split_static=True, merge_touching=False)`](blendertk/blendertk/anim_utils/scale_keys.py#L95) — Scale (retime) keyframes uniformly or via motion-aware speed normalization — mirror of
- **[`class ScaleKeys`](blendertk/blendertk/anim_utils/scale_keys.py#L219)** — Namespace mirror of mayatk's ``ScaleKeys`` (``scale_keys`` also exposed module-level).

<a id="anim_utils--shots--_shots"></a>
### `anim_utils/shots/_shots.py`

Blender shot-store adapter — the DCC layer over ``pythontk``'s shots engine.

- [`iter_action_fcurves(obj)`](blendertk/blendertk/anim_utils/shots/_shots.py#L87) — Yield every fcurve driving *obj*, across Blender 5.1's slotted actions.
- [`collect_transform_segments(scene=None, gap_threshold: float = 5.0) -> List[Dict[str, Any]]`](blendertk/blendertk/anim_utils/shots/_shots.py#L151) — Gather per-object animation segments for auto shot detection.
- [`collect_selected_key_entries(scene=None) -> List[Tuple[float, float, str]]`](blendertk/blendertk/anim_utils/shots/_shots.py#L182) — Gather ``(time, value, object)`` triples from currently selected keyframes.
- **[`class BlenderScenePersistence`](blendertk/blendertk/anim_utils/shots/_shots.py#L208)** — Persist the store as a JSON string on a scene custom property.
  - `BlenderScenePersistence.remove_callbacks(self) -> None` — Tear down every SJM subscription owned by this backend.
  - `BlenderScenePersistence.save(self, data: Dict[str, Any]) -> None`
  - `BlenderScenePersistence.load(self) -> Optional[Dict[str, Any]]`
- **[`class BlenderShotStore(ShotStore)`](blendertk/blendertk/anim_utils/shots/_shots.py#L311)** — :class:`pythontk.ShotStore` with the scene hooks bound to Blender.
  - `BlenderShotStore.active(cls) -> 'BlenderShotStore'` *(class)* — Return the active store, auto-installing the Blender backend once.
  - `BlenderShotStore.has_animation() -> bool` *(static)* — True if any scene object has a moving-or-keyed transform fcurve.
  - `BlenderShotStore.detect_regions(self) -> List[Dict[str, Any]]` — Detect shot candidates from the scene using the store's settings.
  - `BlenderShotStore.assess(self) -> Dict[int, str]` — Flag shots whose stored objects no longer exist in the file.

<a id="anim_utils--shots--shot_manifest--_shot_manifest"></a>
### `anim_utils/shots/shot_manifest/_shot_manifest.py`

Blender Shot Manifest adapter — the DCC layer over pythontk's manifest engine.

- **[`class BlenderShotManifest(ShotManifest)`](blendertk/blendertk/anim_utils/shots/shot_manifest/_shot_manifest.py#L50)** — :class:`pythontk.ShotManifest` with the scene hooks bound to Blender.
  - `BlenderShotManifest.apply_behaviors(self) -> dict` — Key fade behaviors via ``RenderOpacity`` and place audio as VSE strips.
  - `BlenderShotManifest.reapply_object(self, shot, obj) -> bool` — Re-key every behavior on a single *obj* over *shot*'s range.
  - `BlenderShotManifest.from_csv(cls, filepath, store=None, columns=None, post_process=None)` *(class)* — Parse a CSV and return a ready-to-build engine.

<a id="anim_utils--shots--shot_manifest--manifest_data"></a>
### `anim_utils/shots/shot_manifest/manifest_data.py`

Constants, column layout, and pure helper functions for the Shot Manifest UI.

- [`fmt_behavior(name: str) -> str`](blendertk/blendertk/anim_utils/shots/shot_manifest/manifest_data.py#L50) — ``'fade_in'`` → ``'Fade In'``.
- [`format_behavior_html(behaviors, broken=(), status_color=None) -> str`](blendertk/blendertk/anim_utils/shots/shot_manifest/manifest_data.py#L55) — Return rich-text HTML for a list of behavior names.
- [`try_load_blender_icons()`](blendertk/blendertk/anim_utils/shots/shot_manifest/manifest_data.py#L86) — Return per-node-type icon provider, or ``None``.

<a id="anim_utils--shots--shot_manifest--shot_manifest_slots"></a>
### `anim_utils/shots/shot_manifest/shot_manifest_slots.py`

Switchboard slots for the Shot Manifest UI (Blender).

- **[`class ShotManifestController(ManifestTableMixin, ptk.LoggingMixin)`](blendertk/blendertk/anim_utils/shots/shot_manifest/shot_manifest_slots.py#L64)** — Business logic for the Shot Manifest UI.
  - `ShotManifestController.detect(self, gap: Optional[float] = None) -> None` — Detect animation regions in the scene and populate the table.
  - `ShotManifestController.remove_callbacks(self) -> None` — Remove store listener and invalidation subscription (call on teardown).
  - `ShotManifestController.browse_csv(self) -> None` — Open a file dialog and load the selected CSV.
  - `ShotManifestController.build(self) -> None` — Build or update shots in the store from loaded steps.
  - `ShotManifestController.assess(self, skip_key_check: bool = False) -> None` — Compare CSV steps against the live Blender shots and color the tree.
- **[`class ShotManifestSlots(ptk.LoggingMixin)`](blendertk/blendertk/anim_utils/shots/shot_manifest/shot_manifest_slots.py#L1955)** — Switchboard slot class — routes UI events to the controller.
  - `ShotManifestSlots.header_init(self, widget)` — Header menu is configured once in controller.__init__.
  - `ShotManifestSlots.btn_expand_missing(self)` — Expand all step rows that have missing objects or behaviors.
  - `ShotManifestSlots.btn_expand_extra(self)` — Expand all step rows that have scene-discovered extra objects.
  - `ShotManifestSlots.btn_settings(self)` — Open the shared shots settings panel.
  - `ShotManifestSlots.b002(self)` — Assess shots against live Blender scene.
  - `ShotManifestSlots.b003(self)` — Build shots from loaded steps (or auto-detect from scene).

<a id="anim_utils--shots--shot_manifest--table_presenter"></a>
### `anim_utils/shots/shot_manifest/table_presenter.py`

Tree-widget presentation mixin for the Shot Manifest controller.

- **[`class ManifestTableMixin`](blendertk/blendertk/anim_utils/shots/shot_manifest/table_presenter.py#L50)** — Presentation methods for the manifest tree widget.
  - `ManifestTableMixin.expand_missing(self) -> None` — Expand all step rows that have missing objects, behaviors, or additional objects.
  - `ManifestTableMixin.expand_extra(self) -> None` — Expand all step rows that have scene-discovered extra objects.

<a id="anim_utils--shots--shot_sequencer--_shot_sequencer"></a>
### `anim_utils/shots/shot_sequencer/_shot_sequencer.py`

Blender shot sequencer engine — timeline moves over the shared shots planner.

- **[`class ShotSequencer`](blendertk/blendertk/anim_utils/shots/shot_sequencer/_shot_sequencer.py#L51)** — Timeline-move engine for a :class:`~blendertk.BlenderShotStore`.
  - `ShotSequencer.shots(self)` *(property)*
  - `ShotSequencer.hidden_objects(self) -> set` *(property)*
  - `ShotSequencer.markers(self)` *(property)*
  - `ShotSequencer.is_object_hidden(self, obj_name: str) -> bool`
  - `ShotSequencer.set_object_hidden(self, obj_name: str, hidden: bool = True) -> None`
  - `ShotSequencer.sorted_shots(self)`
  - `ShotSequencer.shot_by_id(self, shot_id: int)`
  - `ShotSequencer.shot_by_name(self, name: str)`
  - `ShotSequencer.define_shot(self, name, start, end, objects=None, metadata=None, locked=False, description='')` — Define a shot;
  - `ShotSequencer.reconcile_all_shots(self) -> bool` — No-op in Blender (documented divergence).
  - `ShotSequencer.ripple_downstream(self, shot_id: int, after_frame: float, delta: float) -> None` — Shift every shot starting at/after *after_frame* by *delta* (pivot excluded).
  - `ShotSequencer.ripple_upstream(self, shot_id: int, before_frame: float, delta: float) -> None` — Shift every shot ending at/before *before_frame* by *delta* (pivot excluded).
  - `ShotSequencer.respace(self, gap: float = 0, start_frame: float = 1) -> None` — Lay all shots out sequentially from *start_frame* with *gap* spacing.
  - `ShotSequencer.slide_shot(self, shot_id: int, new_start: float, direction: str = 'downstream', _enforce: bool = True) -> None` — Move a shot to *new_start*, rippling neighbours to preserve spacing.
  - `ShotSequencer.move_shot(self, shot_id: int, new_start: float) -> None` — Move a shot's start to *new_start*, rippling downstream shots.
  - `ShotSequencer.move_object_keys(self, obj: str, old_start: float, old_end: float, new_start: float) -> None` — Offset *obj*'s keys in ``[old_start, old_end]`` so the run begins at *new_start*.
  - `ShotSequencer.move_stepped_keys(self, obj: str, old_time: float, new_time: float, attr_name: Optional[str] = None, eps: float = 0.001) -> None` — Move the key(s) at *old_time* to *new_time*.
  - `ShotSequencer.scale_object_keys(self, obj: str, old_start: float, old_end: float, new_start: float, new_end: float) -> None` — Scale one object's keys from ``[old_start, old_end]`` into ``[new_start, new_end]``.
  - `ShotSequencer.move_object_in_shot(self, shot_id: int, obj: str, old_start: float, old_end: float, new_start: float, prevent_overlap: bool = False) -> None` — Move one object's keys within a shot, growing the shot + rippling when it overruns.
  - `ShotSequencer.resize_object(self, shot_id: int, obj: str, old_start: float, old_end: float, new_start: float, new_end: float) -> None` — Scale one object's keys and ripple downstream shots by the tail delta.
  - `ShotSequencer.set_shot_duration(self, shot_id: int, new_duration: float) -> None` — Change a shot's duration (start fixed), scaling its keys + rippling downstream.
  - `ShotSequencer.resize_shot(self, shot_id: int, new_start: float, new_end: float, _enforce: bool = True) -> None` — Resize a shot to ``[new_start, new_end]``, scaling all keys and rippling both edges.
  - `ShotSequencer.apply_gap(self, gap: float, scope: str = 'all', shot_id: Optional[int] = None) -> bool` — Apply *gap* to shots per *scope* (``all`` / ``start`` / ``end`` / ``start_end``).
  - `ShotSequencer.move_shot_to_position(self, shot_id: int, target_pos: int) -> None` — Reorder *shot_id* to 1-based timeline position *target_pos*.
  - `ShotSequencer.collect_object_segments(self, shot_id: int, ignore: Optional[str] = None, motion_rate: float = 0.001, ignore_holds: bool = True) -> List[dict]` — Per-object keyed-span segments within a shot — the sequencer track data.
  - `ShotSequencer.fit_shot_to_content(self, shot_id: int, mode: str = 'trim') -> Tuple[float, float]` — Resize a shot to its keyed content, rippling neighbours by the deltas.
  - `ShotSequencer.trim_shot_to_content(self, shot_id: int) -> Tuple[float, float]` — Trim empty space from a shot's start and end (bounds move inward only).

<a id="anim_utils--shots--shot_sequencer--clip_motion"></a>
### `anim_utils/shots/shot_sequencer/clip_motion.py`

Clip motion, resize, and key-scaling logic for the shot sequencer (Blender).

- [`curves_for_attr(obj_name: str, attr_name: str) -> list`](blendertk/blendertk/anim_utils/shots/shot_sequencer/clip_motion.py#L32) — Return the fcurves driving *attr_name* (a ``translateX``-style label) on *obj_name*.
- [`scale_attribute_keys(obj_name: str, attr_name: str, old_start: float, old_end: float, new_start: float, new_end: float) -> None`](blendertk/blendertk/anim_utils/shots/shot_sequencer/clip_motion.py#L72) — Scale only the fcurves driving *attr_name* on *obj_name* (sub-row clip resize).
- **[`class ClipMotionMixin`](blendertk/blendertk/anim_utils/shots/shot_sequencer/clip_motion.py#L109)** — Mixin supplying clip move, resize, and batch-move handlers.
  - `ClipMotionMixin.on_clip_resized(self, clip_id: int, new_start: float, new_duration: float) -> None` — Resize a clip — attribute sub-row (scale one channel) or main track (``resize_object``).
  - `ClipMotionMixin.on_clip_moved(self, clip_id: int, new_start: float) -> None` — Handle clip move — routes to audio (deferred) or shot-level logic.
  - `ClipMotionMixin.on_clips_batch_moved(self, moves) -> None` — Handle a batch of clip moves (group drag), syncing once at the end.
  - `ClipMotionMixin.on_keys_moved(self, clip_id: int, changes: list) -> None` — Move individual keyframes on the fcurves, then refresh.
  - `ClipMotionMixin.on_keys_deleted(self, clip_id: int, times: list) -> None` — Delete individual keyframes from the fcurves, then refresh.

<a id="anim_utils--shots--shot_sequencer--gap_manager"></a>
### `anim_utils/shots/shot_sequencer/gap_manager.py`

Gap and range-highlight handlers for the shot sequencer controller (Blender).

- **[`class GapManagerMixin`](blendertk/blendertk/anim_utils/shots/shot_sequencer/gap_manager.py#L20)** — Mixin supplying gap-overlay and range-highlight handlers.
  - `GapManagerMixin.on_range_highlight_changed(self, start: float, end: float) -> None` — Update the active shot boundaries when the range highlight is dragged.
  - `GapManagerMixin.on_gap_resized(self, original_next_start: float, new_next_start: float) -> None` — Handle right-edge gap drag (a shot's ``.start``).
  - `GapManagerMixin.on_gap_left_resized(self, original_prev_end: float, new_prev_end: float) -> None` — Handle left-edge gap drag (a shot's ``.end``).
  - `GapManagerMixin.on_gap_moved(self, old_start: float, old_end: float, new_start: float, new_end: float) -> None` — Handle body gap drag — slide the gap while preserving its width.
  - `GapManagerMixin.on_gap_lock_changed(self, gap_start: float, gap_end: float, locked: bool) -> None` — Handle a single gap's lock state being toggled via context menu.
  - `GapManagerMixin.on_gap_lock_all(self) -> None` — Lock all gaps so they are preserved during respace.
  - `GapManagerMixin.on_gap_unlock_all(self) -> None` — Unlock all gaps so they follow the global gap value.

<a id="anim_utils--shots--shot_sequencer--marker_manager"></a>
### `anim_utils/shots/shot_sequencer/marker_manager.py`

Marker persistence for the shot sequencer controller (Blender).

- **[`class MarkerManagerMixin`](blendertk/blendertk/anim_utils/shots/shot_sequencer/marker_manager.py#L15)** — Mixin supplying marker CRUD persistence.
  - `MarkerManagerMixin.on_marker_added(self, marker_id: int, time: float) -> None` — Persist a newly added marker.
  - `MarkerManagerMixin.on_marker_moved(self, marker_id: int, new_time: float) -> None` — Update persisted marker time.
  - `MarkerManagerMixin.on_marker_changed(self, marker_id: int) -> None` — Update persisted marker note/color.
  - `MarkerManagerMixin.on_marker_removed(self, marker_id: int) -> None` — Remove marker from persistent store.

<a id="anim_utils--shots--shot_sequencer--segment_collector"></a>
### `anim_utils/shots/shot_sequencer/segment_collector.py`

Segment collection and attribute extraction for the shot sequencer (Blender).

- [`attr_label(fcurve) -> str`](blendertk/blendertk/anim_utils/shots/shot_sequencer/segment_collector.py#L47) — ``location[0]`` → ``translateX`` (mayatk-style channel label).
- [`collect_segments(sequencer, shot, visible_shots, segment_cache, shifted_out_keys, logger)`](blendertk/blendertk/anim_utils/shots/shot_sequencer/segment_collector.py#L63) — Collect per-object animation segments for visible shots.
- [`active_object_set(shot, segments_by_shot) -> set`](blendertk/blendertk/anim_utils/shots/shot_sequencer/segment_collector.py#L111) — Objects that have actual animation segments in the active shot.
- [`extract_attributes(segments) -> list`](blendertk/blendertk/anim_utils/shots/shot_sequencer/segment_collector.py#L116) — Transform-channel labels (``translateX``…) keyed within the segments.
- [`build_curve_preview(fcurve, t_start, t_end)`](blendertk/blendertk/anim_utils/shots/shot_sequencer/segment_collector.py#L146) — Bézier shape data for one Blender fcurve, clipped to ``[t_start, t_end]``.

<a id="anim_utils--shots--shot_sequencer--shot_nav"></a>
### `anim_utils/shots/shot_sequencer/shot_nav.py`

Shot navigation and combobox synchronization (Blender).

- **[`class ShotNavMixin`](blendertk/blendertk/anim_utils/shots/shot_sequencer/shot_nav.py#L16)** — Mixin supplying shot selection and navigation.
  - `ShotNavMixin.select_shot(self, shot_id: int) -> None` — Set the view playback range to the shot and select its objects.
  - `ShotNavMixin.on_shot_block_clicked(self, shot_name: str) -> None` — Select a shot by name when its block is clicked in the shot lane.

<a id="anim_utils--shots--shot_sequencer--shot_sequencer_slots"></a>
### `anim_utils/shots/shot_sequencer/shot_sequencer_slots.py`

Switchboard slots for the Shot Sequencer UI (Blender).

- **[`class ShotSequencerController(GapManagerMixin, ClipMotionMixin, ShotNavMixin, MarkerManagerMixin, ptk.LoggingMixin)`](blendertk/blendertk/anim_utils/shots/shot_sequencer/shot_sequencer_slots.py#L65)** — Business logic controller bridging SequencerWidget ↔ ShotSequencer.
  - `ShotSequencerController.sequencer(self) -> Optional[ShotSequencer]` *(property)*
  - `ShotSequencerController.remove_callbacks(self) -> None` — Detach all scene handlers + listeners (call on teardown).
  - `ShotSequencerController.on_zone_context_menu(self, zone: str, time: float, global_pos) -> None`
  - `ShotSequencerController.active_shot_id(self) -> Optional[int]` *(property)*
  - `ShotSequencerController.on_undo(self) -> None` — Widget undo_requested — restore the shot-boundary snapshot + refresh.
  - `ShotSequencerController.on_redo(self) -> None`
  - `ShotSequencerController.refresh(self) -> None`
  - `ShotSequencerController.hide_track(self, track_names) -> None`
  - `ShotSequencerController.show_track(self, track_name: str) -> None`
  - `ShotSequencerController.delete_track(self, track_names) -> None`
  - `ShotSequencerController.on_selection_changed(self, clip_ids: list) -> None`
  - `ShotSequencerController.on_track_selected(self, track_names: list) -> None`
  - `ShotSequencerController.on_clip_locked(self, clip_id: int, locked: bool) -> None`
  - `ShotSequencerController.on_track_menu(self, menu, track_names) -> None`
  - `ShotSequencerController.on_header_menu(self, menu) -> None` — Header background context menu — no domain actions this phase.
  - `ShotSequencerController.on_clip_renamed(self, clip_id: int, new_label: str) -> None` — Renaming a clip is display-only in Blender (object names own identity).
  - `ShotSequencerController.on_playhead_moved(self, frame: float) -> None` — Widget playhead drag → set the scene frame.
  - `ShotSequencerController.on_clip_menu(self, menu, clip_id: int) -> None` — Add Delete-key + lock actions to a clip's context menu.
  - `ShotSequencerController.on_gap_menu(self, menu, gap_start: float, gap_end: float) -> None` — Gap overlay context menu — no domain actions this phase.
  - `ShotSequencerController.on_key_selection_changed(self, key_groups: list) -> None` — Per-key selection changed — footer feedback only.
- **[`class ShotSequencerSlots(ptk.LoggingMixin)`](blendertk/blendertk/anim_utils/shots/shot_sequencer/shot_sequencer_slots.py#L1278)** — Switchboard slot class — routes UI events to the controller.
  - `ShotSequencerSlots.header_init(self, widget)` — Build the header menu controls (mirror of mayatk's sequencer header).
  - `ShotSequencerSlots.btn_colors(self)` — Open the attribute color configuration dialog.
  - `ShotSequencerSlots.spn_snap(self, value)` — Set the snap interval on the sequencer widget.
  - `ShotSequencerSlots.btn_shortcuts(self)` — Open the sequencer shortcut editor.
  - `ShotSequencerSlots.btn_shot_settings(self)` — Open the shared shots settings panel.
  - `ShotSequencerSlots.cmb_shot(self, index)` — Handle direct combobox selection of a shot or marker.

<a id="anim_utils--shots--shots_slots"></a>
### `anim_utils/shots/shots_slots.py`

Switchboard slots for the Shots settings UI.

- **[`class ShotsController(ptk.LoggingMixin)`](blendertk/blendertk/anim_utils/shots/shots_slots.py#L30)** — Business logic for the Shots settings panel.
  - `ShotsController.remove_callbacks(self) -> None` — Remove store listeners and invalidation subscription (call on teardown).
  - `ShotsController.refresh_state(self) -> None` — Central enable/disable refresh for all Shots UI widgets.
  - `ShotsController.on_detection_changed(self, value: float) -> None`
  - `ShotsController.on_detection_mode_changed(self, index: int) -> None`
  - `ShotsController.on_initial_length_changed(self, value: float) -> None`
  - `ShotsController.on_snap_whole_frames_changed(self, checked: bool) -> None`
  - `ShotsController.on_fit_mode_changed(self, index: int) -> None`
  - `ShotsController.on_gap_changed(self, value, scope: str = 'all') -> None`
  - `ShotsController.on_shot_selected(self, index: int) -> None` — User picked a different shot from the combobox.
  - `ShotsController.on_shot_name_changed(self, text: str) -> None`
  - `ShotsController.on_shot_start_changed(self, value: float) -> None`
  - `ShotsController.on_shot_end_changed(self, value: float) -> None`
  - `ShotsController.on_shot_desc_changed(self, text: str) -> None`
  - `ShotsController.on_delete_shot(self) -> None` — Delete the active shot after confirmation.
  - `ShotsController.on_delete_all_shots(self) -> None` — Delete every shot after confirmation.
  - `ShotsController.on_move_shot(self) -> None` — Move the active shot to the position specified by spn_move_to.
  - `ShotsController.on_trim_empty(self) -> None` — Trim empty space from the active shot's start and end.
  - `ShotsController.on_trim_all_shots(self) -> None` — Trim empty space from every shot.
- **[`class ShotsSlots(ptk.LoggingMixin)`](blendertk/blendertk/anim_utils/shots/shots_slots.py#L823)** — Switchboard slot class — routes UI events to the controller.
  - `ShotsSlots.header_init(self, widget)` — Configure header help text.
  - `ShotsSlots.spn_detection(self, value)` — Detection threshold changed.
  - `ShotsSlots.cmb_detection_mode(self, index)` — Detection mode combobox changed.
  - `ShotsSlots.spn_initial_length(self, value)` — Initial shot length changed.
  - `ShotsSlots.cmb_fit_mode(self, index)` — Fit mode combobox changed.
  - `ShotsSlots.chk_snap_whole_frames(self, checked)` — Snap-to-whole-frames checkbox toggled.
  - `ShotsSlots.cmb_shot_select(self, index)` — Shot selector combobox changed.
  - `ShotsSlots.txt_shot_name(self, text=None)` — Shot name edited.
  - `ShotsSlots.spn_shot_start(self, value)` — Shot start frame changed.
  - `ShotsSlots.spn_shot_end(self, value)` — Shot end frame changed.
  - `ShotsSlots.txt_shot_desc(self, text=None)` — Shot description edited.
  - `ShotsSlots.b000(self)` — Delete the selected shot.
  - `ShotsSlots.btn_delete_all_shots(self)` — Delete all shots.
  - `ShotsSlots.btn_move_shot(self)` — Move shot to the position in spn_move_to.
  - `ShotsSlots.btn_apply_gap(self)` — Apply gap value with the scope selected in the option box.
  - `ShotsSlots.btn_trim_empty(self)` — Trim empty space from the selected shot.
  - `ShotsSlots.btn_trim_all_shots(self)` — Trim empty space from every shot.

<a id="anim_utils--smart_bake--_smart_bake"></a>
### `anim_utils/smart_bake/_smart_bake.py`

Smart Bake engine — mirror of mayatk's ``anim_utils.smart_bake._smart_bake`` at the

- **[`class BakeAnalysis`](blendertk/blendertk/anim_utils/smart_bake/_smart_bake.py#L54)** — Analysis result for one bake-relevant unit — either a whole object or one of its pose
  - `BakeAnalysis.requires_bake(self) -> bool` *(property)* — True if any live source was found for this unit.
- **[`class BakeResult`](blendertk/blendertk/anim_utils/smart_bake/_smart_bake.py#L86)** — Result container for ``SmartBake.bake()``.
  - `BakeResult.baked_count(self) -> int` *(property)* — Number of objects successfully baked.
  - `BakeResult.success(self) -> bool` *(property)* — True if any objects were baked.
- **[`class SmartBake`](blendertk/blendertk/anim_utils/smart_bake/_smart_bake.py#L293)** — Intelligent bake+restore with automatic detection of what needs baking.
  - `SmartBake.analyze(self) -> Dict[str, BakeAnalysis]` — Analyze objects to determine what needs baking.
  - `SmartBake.get_time_range(self, analysis: Optional[Dict[str, BakeAnalysis]] = None) -> Tuple[int, int]` — Determine the optimal bake time range from driver/constraint-target animation.
  - `SmartBake.bake(self, analysis: Optional[Dict[str, BakeAnalysis]] = None, time_range: Optional[Tuple[int, int]] = None) -> BakeResult` — Bake every driven source :func:`analyze` found.
  - `SmartBake.execute(self) -> BakeResult` — High-level entry point: :func:`analyze` then :func:`bake` in one call.
  - `SmartBake.list_sessions(cls) -> List[str]` *(class)* — Ids of restorable bake sessions recorded on this scene's ``data_internal`` carrier,
  - `SmartBake.restore(cls, session_id: Optional[str] = None) -> 'RestoreResult'` *(class)* — Reverse a bake session recorded by ``bake(restorable=True)``.
  - `SmartBake.session(cls, **kwargs)` *(class)* — Context manager: bake on enter, restore on exit.
  - `SmartBake.run(cls, **kwargs) -> BakeResult` *(class)* — Quick entry point for a one-shot bake: ``cls(**kwargs).execute()``.

<a id="anim_utils--smart_bake--bake_session"></a>
### `anim_utils/smart_bake/bake_session.py`

Persistence and restore engine for SmartBake's nondestructive manifest — mirror of mayatk's

- [`node_ref(obj_or_action) -> Optional[Dict[str, str]]`](blendertk/blendertk/anim_utils/smart_bake/bake_session.py#L54) — Return a plain-name reference ``{"name", "kind"}`` for a live object or action.
- [`resolve_ref(ref: Optional[Dict[str, str]])`](blendertk/blendertk/anim_utils/smart_bake/bake_session.py#L69) — Resolve a :func:`node_ref` back to a live ``bpy.types.Object``/``Action``, or ``None``.
- [`constraint_ref(obj, constraint, bone: Optional[str] = None) -> Dict[str, Any]`](blendertk/blendertk/anim_utils/smart_bake/bake_session.py#L82) — Reference a constraint on ``obj`` (or, for an armature, on pose bone ``bone``).
- [`resolve_constraint(ref: Optional[Dict[str, Any]])`](blendertk/blendertk/anim_utils/smart_bake/bake_session.py#L90) — Resolve a :func:`constraint_ref` back to a live ``bpy.types.Constraint``, or ``None``.
- [`driver_ref(obj, fcurve) -> Dict[str, Any]`](blendertk/blendertk/anim_utils/smart_bake/bake_session.py#L105) — Reference a driver ``fcurve`` on ``obj.animation_data.drivers``.
- [`resolve_driver(ref: Optional[Dict[str, Any]])`](blendertk/blendertk/anim_utils/smart_bake/bake_session.py#L114) — Resolve a :func:`driver_ref` back to a live driver ``FCurve``, or ``None``.
- [`snapshot_blend_shape_driver(obj, key_block, fcurve) -> Dict[str, Any]`](blendertk/blendertk/anim_utils/smart_bake/bake_session.py#L138) — Serialize a shape-key driver before ``bake_blend_shapes`` removes it.
- [`snapshot_blend_shape_action(obj, key_block, fcurve) -> Dict[str, Any]`](blendertk/blendertk/anim_utils/smart_bake/bake_session.py#L229) — Serialize a shape-key's own (non-driver) keyframes before ``bake_blend_shapes`` resamples
- [`restore_session(session: dict) -> RestoreResult`](blendertk/blendertk/anim_utils/smart_bake/bake_session.py#L440) — Reverse everything recorded in *session*.
- **[`class BakeSessionStore`](blendertk/blendertk/anim_utils/smart_bake/bake_session.py#L326)** — LIFO stack of bake-session manifests on the ``data_internal`` Empty.
  - `BakeSessionStore.load(cls) -> List[dict]` *(class)* — Return all persisted sessions (oldest first).
  - `BakeSessionStore.save(cls, sessions: List[dict]) -> None` *(class)*
  - `BakeSessionStore.push(cls, session: dict) -> None` *(class)*
  - `BakeSessionStore.peek(cls, session_id: Optional[str] = None) -> Optional[dict]` *(class)* — Return the latest session (or the one matching *session_id*).
  - `BakeSessionStore.pop(cls, session_id: Optional[str] = None) -> Optional[dict]` *(class)* — Remove and return the latest session (or the matching one).
  - `BakeSessionStore.list_ids(cls) -> List[str]` *(class)*
  - `BakeSessionStore.new_session_id(cls) -> str` *(class)* — A fresh, collision-safe session id.
- **[`class RestoreResult`](blendertk/blendertk/anim_utils/smart_bake/bake_session.py#L414)** — Result container for ``SmartBake.restore()``.

<a id="anim_utils--smart_bake--smart_bake_slots"></a>
### `anim_utils/smart_bake/smart_bake_slots.py`

Slots for the Smart Bake tool panel (``smart_bake.ui``) — Blender port of mayatk's

- **[`class SmartBakeSlots(ptk.LoggingMixin)`](blendertk/blendertk/anim_utils/smart_bake/smart_bake_slots.py#L32)** — Controller wiring ``smart_bake.ui`` to the :class:`SmartBake` engine.
  - `SmartBakeSlots.cmb_scope_init(self, widget) -> None`
  - `SmartBakeSlots.cmb_backup_init(self, widget) -> None`
  - `SmartBakeSlots.header_init(self, widget) -> None` — Configure header menu, refresh button, and help text.
  - `SmartBakeSlots.reset_defaults(self) -> None` — Header menu: reset every field in this panel to its registry default.
  - `SmartBakeSlots.b000(self, widget) -> None` — Bake.
  - `SmartBakeSlots.b001(self, widget) -> None` — Unbake.

<a id="anim_utils--stagger_keys"></a>
### `anim_utils/stagger_keys.py`

Dedicated stagger-keys module to keep AnimUtils lean and testable (mirror of mayatk's

- [`stagger_keys(objects, start_frame=None, spacing=5, use_intervals=False, invert=False, group_overlapping=False, merge_touching=False, smooth_tangents=False)`](blendertk/blendertk/anim_utils/stagger_keys.py#L27) — Re-time selected objects so their animations play one after another (mirror of ``mtk``
- **[`class StaggerKeys`](blendertk/blendertk/anim_utils/stagger_keys.py#L89)** — Namespace mirror of mayatk's ``StaggerKeys`` (``stagger_keys`` also exposed module-level).

<a id="audio_utils--_audio_utils"></a>
### `audio_utils/_audio_utils.py`

Scene-wide audio-clip utilities over Blender's Video Sequence Editor (VSE).

- **[`class AudioUtils(ptk.LoggingMixin)`](blendertk/blendertk/audio_utils/_audio_utils.py#L66)** — Scene-wide audio-clip CRUD over Blender's Video Sequence Editor.
  - `AudioUtils.ensure_sequence_editor(scene=None)` *(static)* — Return *scene*'s sequence editor, creating it if this is the first strip.
  - `AudioUtils.get_sequence_editor(scene=None)` *(static)* — Return *scene*'s sequence editor, or ``None`` when it doesn't exist yet.
  - `AudioUtils.list_clips(cls, scene=None) -> List[Dict]` *(class)* — Return every sound strip in *scene* as a plain dict, sorted by visible start frame.
  - `AudioUtils.get_clip(cls, name: str, scene=None) -> Optional[Dict]` *(class)* — Return info for the sound strip named *name*, or ``None``.
  - `AudioUtils.add_clip(cls, filepath, frame_start=None, channel=None, name=None, scene=None) -> str` *(class)* — Add *filepath* as a new sound strip.
  - `AudioUtils.remove_clip(cls, name: str, scene=None) -> bool` *(class)* — Remove the sound strip named *name*, and its ``bpy.data.sounds`` datablock if orphaned.
  - `AudioUtils.remove_all_clips(cls, scene=None) -> int` *(class)* — Remove every sound strip in *scene*.
  - `AudioUtils.rename_clip(cls, name: str, new_name: str, scene=None) -> Optional[str]` *(class)* — Rename a clip.
  - `AudioUtils.replace_clip(cls, name: str, new_filepath: str, scene=None) -> bool` *(class)* — Swap *name*'s underlying audio file, keeping its position/channel/trim.
  - `AudioUtils.move_clip(cls, name: str, frame_start, scene=None) -> bool` *(class)* — Reposition *name* so its content begins at *frame_start* (trim is preserved).
  - `AudioUtils.trim_clip(cls, name: str, offset_start=None, offset_end=None, scene=None) -> bool` *(class)* — Trim *name*'s head/tail without moving its overall position.
  - `AudioUtils.sync_scene_range(cls, scene=None, extend_only: bool = True) -> Tuple[int, int]` *(class)* — Fit *scene*'s frame range to the loaded clips.

<a id="audio_utils--audio_clips"></a>
### `audio_utils/audio_clips.py`

Audio Clips — scene-wide sound-strip management over Blender's Video Sequence Editor (VSE).

- **[`class AudioClipsSlots(ptk.LoggingMixin)`](blendertk/blendertk/audio_utils/audio_clips.py#L45)** — Switchboard slots for the Audio Clips panel.
  - `AudioClipsSlots.header_init(self, widget)` — Help text only — no header menu items apply (see module docstring).
  - `AudioClipsSlots.cmb000_init(self, widget)` — Browse option box + clip-management menu;
  - `AudioClipsSlots.cmb000(self, index, widget)` — Selection only informs Move/Trim/the option-box actions — no side effect.
  - `AudioClipsSlots.b001(self)` — Rename Selected — rename the clip currently shown in the combo.
  - `AudioClipsSlots.b002(self)` — Replace Selected — swap the selected clip's source file.
  - `AudioClipsSlots.b005(self)` — Remove Selected — delete the clip currently shown in the combo.
  - `AudioClipsSlots.b006(self)` — Remove All — delete every clip in the scene.
  - `AudioClipsSlots.tb001_init(self, widget)` — Move option box — reveal-in-Sequencer + a Sync Scene Range shortcut.
  - `AudioClipsSlots.tb001(self, widget=None)` — Move the selected clip so it starts at the current frame (trim is preserved).
  - `AudioClipsSlots.b003(self)` — Apply Trim — trim the selected clip's head/tail to the ``s000``/``s001`` values.
  - `AudioClipsSlots.b004_init(self, widget)`
  - `AudioClipsSlots.b004(self, widget=None)` — Fit the scene frame range to the loaded clips.

<a id="cam_utils--_cam_utils"></a>
### `cam_utils/_cam_utils.py`

Camera utilities — clip-plane adjustment (mirror of mayatk's ``cam_utils``).

- [`adjust_camera_clipping(camera=None, near_clip=None, far_clip=None)`](blendertk/blendertk/cam_utils/_cam_utils.py#L63) — Adjust near/far clip planes of camera object(s) — mirror of ``mtk.adjust_camera_clipping``.
- **[`class CamUtils`](blendertk/blendertk/cam_utils/_cam_utils.py#L91)** — Namespace mirror of mayatk's ``CamUtils`` (helper also exposed module-level).

<a id="core_utils--_core_utils"></a>
### `core_utils/_core_utils.py`

Core blendertk utilities — DCC-environment info + cross-cutting decorators.

- [`undo_chunk(name: str = '')`](blendertk/blendertk/core_utils/_core_utils.py#L20) — Collapse every change made inside the block into ONE Blender undo step.
- [`undoable(fn)`](blendertk/blendertk/core_utils/_core_utils.py#L63) — Wrap ``fn`` so its changes collapse into a single Blender undo step.
- [`undo_checkpoint(fn)`](blendertk/blendertk/core_utils/_core_utils.py#L83) — Like :func:`undoable`, but pushes the restore point BEFORE ``fn`` runs (not after).
- [`get_env_info(key=None)`](blendertk/blendertk/core_utils/_core_utils.py#L139) — Return Blender scene / environment info (mirror of ``mtk.get_env_info``).
- [`ensure_image_deps(packages=None, add_to_path=True)`](blendertk/blendertk/core_utils/_core_utils.py#L179) — Make image-processing libraries importable in Blender's Python (default: Pillow → ``PIL``).
- [`get_recent_files(index=None)`](blendertk/blendertk/core_utils/_core_utils.py#L299) — Recently-opened .blend paths, most recent first (mirror of ``mtk.get_recent_files``).
- [`get_recent_autosave(filter_time=24, timestamp_format='%H:%M:%S')`](blendertk/blendertk/core_utils/_core_utils.py#L317) — Recent autosave .blend files as ``(path, timestamp)`` pairs, newest first
- [`get_scene_info(objects=None)`](blendertk/blendertk/core_utils/_core_utils.py#L348) — Scene audit record — the Blender analogue of Maya's Get Scene Info (a focused
- [`format_scene_info_html(info)`](blendertk/blendertk/core_utils/_core_utils.py#L399) — Render a :func:`get_scene_info` record as an HTML report for the text-view dialog.
- [`analyze_scene(objects=None, adaptive=True, sections=None)`](blendertk/blendertk/core_utils/_core_utils.py#L438) — Game-readiness scene audit — the Blender port of mayatk's ``SceneAnalyzer`` (the budgeted,
- [`cleanup_scene(quiet=False)`](blendertk/blendertk/core_utils/_core_utils.py#L554) — Purge orphan datablocks (0 users, no fake user) across the main collections — the
- [`selected_objects()`](blendertk/blendertk/core_utils/_core_utils.py#L612) — The current object selection, filtered of ``None`` (mirror of Maya's
- [`active_object()`](blendertk/blendertk/core_utils/_core_utils.py#L631) — The active object, resolved window-independently (``view_layer.objects.active``).
- [`get_areas(area_type)`](blendertk/blendertk/core_utils/_core_utils.py#L643) — All areas of ``area_type`` (``"VIEW_3D"``, ``"IMAGE_EDITOR"``, …) across every open
- [`get_view3d_context()`](blendertk/blendertk/core_utils/_core_utils.py#L663) — Context-override dict targeting the first VIEW_3D area/region, or ``None`` if there is no
- [`window_context_override()`](blendertk/blendertk/core_utils/_core_utils.py#L693) — Yield with a valid ``window`` in context when ``bpy.context.window`` is ``None``.
- **[`class CoreUtils(ptk.CoreUtils)`](blendertk/blendertk/core_utils/_core_utils.py#L721)** — Blender ``CoreUtils`` — extends pythontk's DCC-agnostic ``CoreUtils`` (mirrors

<a id="core_utils--auto_instancer--_auto_instancer"></a>
### `core_utils/auto_instancer/_auto_instancer.py`

Scene auto-instancer: convert geometrically identical meshes to instances.

- [`auto_instance(objects: Optional[Sequence[object]] = None, tolerance: float = 0.001, scale_tolerance: Optional[float] = None, require_same_material: Union[bool, int] = True, check_uvs: bool = False, check_hierarchy: bool = False, separate_combined: bool = False, combine_assemblies: bool = True, combine_non_instanced: bool = True, combine_by_material: bool = True, combine_by_distance: bool = True, combine_distance_threshold: float = 10000.0, search_radius_mult: float = 1.5, is_static: bool = True, needs_individual: bool = False, will_be_lightmapped: bool = False, can_gpu_instance: bool = True, verbose: bool = True, log_level: str = 'WARNING') -> List[object]`](blendertk/blendertk/core_utils/auto_instancer/_auto_instancer.py#L1069) — Find geometrically identical meshes and convert them to instances.
- **[`class InstanceCandidate`](blendertk/blendertk/core_utils/auto_instancer/_auto_instancer.py#L114)** — Holds information about an object candidate for instancing.
  - `InstanceCandidate.obj(self)` *(property)*
  - `InstanceCandidate.exists(self) -> bool`
- **[`class InstanceGroup`](blendertk/blendertk/core_utils/auto_instancer/_auto_instancer.py#L150)** — A group of objects that are geometrically identical.
- **[`class AutoInstancer(ptk.LoggingMixin)`](blendertk/blendertk/core_utils/auto_instancer/_auto_instancer.py#L164)** — Convert matching meshes into instances (shared mesh datablocks).
  - `AutoInstancer.tolerance(self)` *(property)*
  - `AutoInstancer.scale_tolerance(self)` *(property)*
  - `AutoInstancer.require_same_material(self)` *(property)*
  - `AutoInstancer.check_uvs(self)` *(property)*
  - `AutoInstancer.combine_assemblies(self)` *(property)*
  - `AutoInstancer.search_radius_mult(self)` *(property)*
  - `AutoInstancer.verbose(self)` *(property)*
  - `AutoInstancer.run(self, objects: Optional[Sequence[object]] = None) -> List[object]` — Discover and instance matching meshes.
  - `AutoInstancer.find_instance_groups(self, objects: Optional[Sequence[object]] = None, check_hierarchy: Optional[bool] = None) -> List[InstanceGroup]` — Find groups of identical objects.

<a id="core_utils--auto_instancer--assembly_reconstructor"></a>
### `core_utils/auto_instancer/assembly_reconstructor.py`

Logic for separating and reassembling mesh assemblies (bpy adapter).

- **[`class AssemblyReconstructor`](blendertk/blendertk/core_utils/auto_instancer/assembly_reconstructor.py#L54)** — Handles the separation and intelligent reassembly of combined meshes.
  - `AssemblyReconstructor.separate_combined_meshes(self, objects: List[object]) -> List[object]` — Separate any combined (multi-shell) meshes into their shells.
  - `AssemblyReconstructor.cleanup_empty_sources(self) -> None` — No-op (API parity with mayatk).
  - `AssemblyReconstructor.cleanup_empty_assembly_groups(self) -> None` — Delete assembly group Empties this run created that have emptied.
  - `AssemblyReconstructor.center_transform_on_geometry(self, obj) -> None` — Move the transform to the center of its geometry without moving it.
  - `AssemblyReconstructor.canonicalize_transform(self, obj) -> None` — Align the transform's rotation to the geometry's PCA axes.
  - `AssemblyReconstructor.canonicalize_leaf_meshes(self, objects: List[object]) -> List[object]` — Canonicalize all leaf mesh objects for instancing.
  - `AssemblyReconstructor.reassemble_assemblies(self, objects: List[object]) -> List[object]` — Reassemble separated shells into logical assemblies.
  - `AssemblyReconstructor.combine_reassembled_assemblies(self, objects: List[object]) -> List[object]` — Combine each copy of a repeated assembly type into a single mesh.

<a id="core_utils--auto_instancer--geometry_matcher"></a>
### `core_utils/auto_instancer/geometry_matcher.py`

Geometry analysis and matching logic for AutoInstancer (bpy adapter).

- **[`class GeometryMatcher`](blendertk/blendertk/core_utils/auto_instancer/geometry_matcher.py#L50)** — Handles geometric analysis and comparison.
  - `GeometryMatcher.clear_cache(self) -> None` — Drop cached point arrays and pair results (call after scene edits).
  - `GeometryMatcher.invalidate(self, me) -> None` — Drop cached data for ONE mesh datablock (call after mutating it).
  - `GeometryMatcher.quantize(self, value: float, precision: int = 4) -> float` — Round a value to a specific precision to ignore float noise.
  - `GeometryMatcher.get_pca_basis(self, obj)` — Stabilized PCA rotation basis for the object's mesh.
  - `GeometryMatcher.get_mesh_signature(self, obj) -> Optional[Tuple]` — Lightweight signature for quick rejection.
  - `GeometryMatcher.get_hierarchy_signature(self, obj) -> Tuple` — Recursive signature generation for hierarchy comparison.
  - `GeometryMatcher.are_meshes_identical(self, o1, o2) -> Tuple[bool, Optional[object]]` — Detailed geometric comparison via ``PointCloud.match_clouds``.
  - `GeometryMatcher.are_meshes_identical_with_transform(self, o1, o2, matrix) -> bool` — True when *o1* transformed by *matrix* matches *o2* in parent space.
  - `GeometryMatcher.are_hierarchies_identical(self, o1, o2, expected_transform=None, is_root: bool = False) -> Tuple[bool, Optional[object]]` — Detailed hierarchy comparison.

<a id="core_utils--auto_instancer--instancing_strategy"></a>
### `core_utils/auto_instancer/instancing_strategy.py`

Instancing strategy logic for AutoInstancer (mirror of mayatk's).

- **[`class StrategyType(Enum)`](blendertk/blendertk/core_utils/auto_instancer/instancing_strategy.py#L16)**
- **[`class StrategyConfig`](blendertk/blendertk/core_utils/auto_instancer/instancing_strategy.py#L24)**
- **[`class InstancingStrategy`](blendertk/blendertk/core_utils/auto_instancer/instancing_strategy.py#L31)** — Determines the best instancing strategy for a group of objects.
  - `InstancingStrategy.evaluate(self, group_size: int, mesh_node: Optional[object] = None, triangle_count: Optional[int] = None) -> StrategyType` — Evaluate the strategy for a given group.

<a id="core_utils--diagnostics--mesh_diag"></a>
### `core_utils/diagnostics/mesh_diag.py`

Mesh diagnostics — the Blender counterpart of mayatk's ``core_utils.diagnostics.mesh_diag``

- [`find_problem_geometry(objects, *, ngons=False, nonmanifold=False, interior=False, nonplanar=False, loose=False, concave=False, quads=False, zero_area_faces=False, zero_length_edges=False, zero_uv_area=False, planar_tolerance=0.001, area_tolerance=1e-06, edge_length_tolerance=1e-06, uv_area_tolerance=1e-06, select=True)`](blendertk/blendertk/core_utils/diagnostics/mesh_diag.py#L71) — Find (and optionally **select**) problem mesh components — the diagnostic half of Maya's
- **[`class MeshDiagnostics`](blendertk/blendertk/core_utils/diagnostics/mesh_diag.py#L197)** — Mesh problem-detection (mirror of mayatk's ``MeshDiagnostics``).

<a id="core_utils--diagnostics--transform_diag"></a>
### `core_utils/diagnostics/transform_diag.py`

Transform diagnostics — the Blender counterpart of mayatk's

- [`fix_non_orthogonal_axes(objects=None, dry_run=False, tolerance=1e-05)`](blendertk/blendertk/core_utils/diagnostics/transform_diag.py#L37) — Bake out non-orthogonal (sheared) world axes — shear breaks FBX export (mirror of
- **[`class TransformDiagnostics`](blendertk/blendertk/core_utils/diagnostics/transform_diag.py#L100)** — Transform/shear diagnostics (mirror of mayatk's ``TransformDiagnostics``).

<a id="core_utils--preview"></a>
### `core_utils/preview.py`

Live-preview driver for the tentacle Blender tool panels — the Blender analogue of

- **[`class Preview`](blendertk/blendertk/core_utils/preview.py#L29)** — Snapshot-based live preview: ``Preview(slot, ui.chk000, ui.b000, …)``.
  - `Preview.is_enabled(self)` *(property)*
  - `Preview.refresh(self, *args)` — Roll back to the snapshot and re-run the operation (parameter-change hook).
  - `Preview.enable(self)`
  - `Preview.disable(self)` — Roll back and drop the snapshot (the un-check path).
  - `Preview.commit(self)` — Keep the current result and push one undo step.

<a id="core_utils--script_job_manager"></a>
### `core_utils/script_job_manager.py`

Centralized Blender event-subscription manager — the Blender counterpart of mayatk's

- **[`class ScriptJobManager`](blendertk/blendertk/core_utils/script_job_manager.py#L79)** — Centralized Blender event dispatcher (mirror of mayatk's ``ScriptJobManager``).
  - `ScriptJobManager.instance(cls) -> 'ScriptJobManager'` *(class)* — Return the module-wide singleton, creating it on first access.
  - `ScriptJobManager.reset(cls) -> None` *(class)* — Tear down the singleton and allow a fresh one to be created (tests / reload).
  - `ScriptJobManager.subscribe(self, event: str, callback: Callable, *, owner: Any = None, ephemeral: bool = False) -> int` — Register *callback* (called with no args) for a Maya-named *event*.
  - `ScriptJobManager.unsubscribe(self, token: int) -> None` — Remove a single subscription by *token*.
  - `ScriptJobManager.unsubscribe_all(self, owner: Any) -> None` — Remove every subscription registered under *owner*.
  - `ScriptJobManager.connect_cleanup(self, widget, owner: Any) -> None` — Connect *widget*.destroyed → :meth:`unsubscribe_all` for *owner* (Qt).
  - `ScriptJobManager.suppress(self, token: int) -> None` — Temporarily silence a subscription without removing it.
  - `ScriptJobManager.resume(self, token: int) -> None` — Re-enable a previously suppressed subscription.
  - `ScriptJobManager.status(self) -> Dict[str, Any]` — Snapshot of installed handlers and current subscriptions.
  - `ScriptJobManager.print_status(self) -> None` — Pretty-print :meth:`status` for interactive debugging.
  - `ScriptJobManager.teardown(self) -> None` — Remove every installed master handler and drop all subscriptions.

<a id="display_utils--_display_utils"></a>
### `display_utils/_display_utils.py`

Display utilities — the exploded-view toggle (mirror of mayatk's

- [`is_exploded(objects)`](blendertk/blendertk/display_utils/_display_utils.py#L27) — True when any of the given objects carries an exploded-view origin stamp.
- [`explode_view(objects, step=1.2, margin=0.05, max_iterations=50)`](blendertk/blendertk/display_utils/_display_utils.py#L32) — Push the given objects apart for inspection — each moves away from the group's bbox
- [`unexplode_view(objects)`](blendertk/blendertk/display_utils/_display_utils.py#L79) — Restore the exact pre-explode locations stamped by :func:`explode_view`.
- [`unexplode_all()`](blendertk/blendertk/display_utils/_display_utils.py#L96) — Restore every exploded object in the scene, regardless of selection (mirror of mayatk's
- [`get_visible_geometry(objects=None)`](blendertk/blendertk/display_utils/_display_utils.py#L104) — Mesh objects visible in the current view layer — mirror of mayatk's
- **[`class DisplayUtils`](blendertk/blendertk/display_utils/_display_utils.py#L119)** — Namespace mirror of mayatk's ``DisplayUtils`` (helpers also exposed module-level).

<a id="display_utils--color_id"></a>
### `display_utils/color_id.py`

Color ID tool panel — Switchboard slot wiring for the co-located ``color_id.ui``.

- **[`class ColorId`](blendertk/blendertk/display_utils/color_id.py#L31)** — Engine: apply / select-by / reset object colors across material, object-color, and vertex
  - `ColorId.assign_id_material(obj, color: Color)` *(static)* — Assign an ID material named ``ID_<HEX>`` with ``color`` as its base color to ``obj``
  - `ColorId.set_object_color(obj, color: Color)` *(static)* — Set the object's viewport display color (``obj.color`` — Object-color shading).
  - `ColorId.set_vertex_color(obj, color: Color, name: str = 'Color')` *(static)* — Write ``color`` to every corner of a mesh color attribute (created/reused, set active).
  - `ColorId.apply_color(cls, objects: Sequence, color: Optional[Color] = None, apply_to_material: bool = False, apply_to_object: bool = False, apply_to_vertex: bool = False) -> None` *(class)* — Apply ``color`` (random when None) to each object across the enabled channels.
  - `ColorId.get_object_color(obj) -> Optional[Color]` *(static)* — The object's viewport display color (``obj.color`` RGB), or None.
  - `ColorId.get_material_color(obj) -> Optional[Color]` *(static)* — Base color of the object's active material (Principled base, else diffuse), or None.
  - `ColorId.get_average_vertex_color(obj) -> Optional[Color]` *(static)* — Average of the active mesh color attribute, or None when there is none.
  - `ColorId.color_difference(c1: Color, c2: Color) -> float` *(static)* — Average absolute per-channel RGB difference.
  - `ColorId.get_objects_by_color(cls, target_color: Color, threshold: float = 0.1, check_material: bool = False, check_object: bool = False, check_vertex: bool = False) -> List` *(class)* — View-layer mesh objects whose color (on any enabled channel) is within ``threshold``.
  - `ColorId.reset_colors(cls, objects: Sequence, reset_material: bool = True, reset_object: bool = True, reset_vertex: bool = True) -> None` *(class)* — Clear color assignments on ``objects`` for the chosen channels.
  - `ColorId.reset_vertex_colors(obj) -> None` *(static)* — Remove every color attribute from a mesh object.
- **[`class ColorIdSlots(ptk.LoggingMixin)`](blendertk/blendertk/display_utils/color_id.py#L227)** — Switchboard slot wiring for the Color ID panel (swatch palette + channels + presets).
  - `ColorIdSlots.header_init(self, widget)` — Configure header help text and preset combobox.
  - `ColorIdSlots.selected_objects(self) -> List` *(property)* — Return the currently selected objects, or an empty list if none are selected.
  - `ColorIdSlots.selected_button(self)` *(property)* — Return the currently checked swatch button in the palette group.
  - `ColorIdSlots.target_color(self) -> Optional[Color]` *(property)* — Return the color of the selected swatch, or None if no swatch is selected.
  - `ColorIdSlots.b000(self) -> None` — Reset Colors (Ctrl+click resets every object in the scene).
  - `ColorIdSlots.b001(self) -> None` — Set Color — apply the active color to the selected objects on the enabled channels.
  - `ColorIdSlots.b002(self) -> None` — Select By Color — select scene objects matching the active color (enabled channels).
  - `ColorIdSlots.b003(self) -> None` — Get Color — read the active object's color into the selected swatch.

<a id="display_utils--exploded_view"></a>
### `display_utils/exploded_view.py`

Exploded View — Switchboard slot wiring for the co-located ``exploded_view.ui``.

- **[`class ExplodedViewSlots(ptk.LoggingMixin)`](blendertk/blendertk/display_utils/exploded_view.py#L31)** — Switchboard slot wiring for the Exploded View panel (mirror of mayatk's ``ExplodedViewSlots``).
  - `ExplodedViewSlots.header_init(self, widget)` — Configure header help text.
  - `ExplodedViewSlots.b000(self)` — Explode.
  - `ExplodedViewSlots.b001(self)` — Un-Explode (selected).
  - `ExplodedViewSlots.b002(self)` — Un-Explode All.
  - `ExplodedViewSlots.b003(self)` — Toggle Explode.

<a id="edit_utils--_curtain_drape"></a>
### `edit_utils/_curtain_drape.py`

Procedural draped-cloth (curtain) drape engine — pure geometry, no DCC.

- **[`class CurtainDrape`](blendertk/blendertk/edit_utils/_curtain_drape.py#L62)** — Drape a grid into a pleated, gravity-sagged curtain — pure math.
  - `CurtainDrape.prepare(self) -> Tuple[int, int, List[Tuple[Vec, Vec, Vec]]]` — Precompute the per-build state and return ``(u_segs, v_segs, frames)``.
  - `CurtainDrape.grid_points(self) -> Tuple[int, int, List[Vec]]` — The full draped grid: ``(u_segs, v_segs, points)``.
  - `CurtainDrape.drape(self, u, v, pos, tan, normal) -> Vec` — Place one cloth vertex.

<a id="edit_utils--_edit_utils"></a>
### `edit_utils/_edit_utils.py`

Mesh-editing utilities — reduce/decimate, coplanar dissolve, triangulate / tris-to-quads,

- [`hook_bind_inverse(target, obj)`](blendertk/blendertk/edit_utils/_edit_utils.py#L87) — The ``matrix_inverse`` a Hook modifier needs so its geometry does **not jump** at bind time.
- [`hook_curve_point(curve, point_index, target, name=None, falloff_type='NONE')`](blendertk/blendertk/edit_utils/_edit_utils.py#L107) — Hook control point *point_index* of *curve* to *target* so moving the target moves that point
- [`decimate(objects, percentage=50.0, preserve_quads=True, symmetry=False, apply=True)`](blendertk/blendertk/edit_utils/_edit_utils.py#L122) — Reduce mesh density via a Decimate (COLLAPSE) modifier — mirror of ``mtk.EditUtils.decimate``.
- [`dissolve_coplanar(objects, angle_tolerance=1.0, delimit=None, preserve_borders=True, apply=True)`](blendertk/blendertk/edit_utils/_edit_utils.py#L143) — Dissolve near-coplanar faces via a Decimate (PLANAR) modifier — mirror of
- [`triangulate(objects)`](blendertk/blendertk/edit_utils/_edit_utils.py#L165) — Triangulate all faces of the given mesh object(s) (bmesh, headless).
- [`tris_to_quads(objects, angle=40.0)`](blendertk/blendertk/edit_utils/_edit_utils.py#L173) — Merge adjacent triangles back into quads where the face/shape angle is within ``angle``
- [`subdivide_mesh(objects, cuts=1)`](blendertk/blendertk/edit_utils/_edit_utils.py#L191) — Subdivide every edge ``cuts`` times, grid-filling faces (bmesh, headless) — "Add Divisions".
- [`boolean_op(objects, operation='DIFFERENCE', apply=True)`](blendertk/blendertk/edit_utils/_edit_utils.py#L202) — Boolean the first mesh by the remaining ones via Boolean modifiers (the §5 map for
- [`set_subdivision(objects, viewport_levels=None, render_levels=None, ensure=True)`](blendertk/blendertk/edit_utils/_edit_utils.py#L219) — Set Subdivision-Surface levels on the given mesh object(s), kept **live** (non-destructive
- [`set_shading(objects, smooth=True)`](blendertk/blendertk/edit_utils/_edit_utils.py#L243) — Set smooth (averaged vertex normals) or flat (face normals) shading on all faces — the
- [`average_normals(objects, by_uv_shell=False)`](blendertk/blendertk/edit_utils/_edit_utils.py#L269) — Average vertex normals by softening edges — Blender mirror of ``mtk.Components.average_normals``
- [`select_edges_by_angle(objects, low_angle=0.0, high_angle=180.0)`](blendertk/blendertk/edit_utils/_edit_utils.py#L298) — Select interior edges whose dihedral (face) angle is within ``[low_angle, high_angle]``
- [`set_edge_hardness(objects, angle=30.0, upper_hardness=0, lower_hardness=180)`](blendertk/blendertk/edit_utils/_edit_utils.py#L329) — Smooth-shade, then mark interior edges hard/soft by their dihedral angle relative to
- [`clear_custom_split_normals(objects)`](blendertk/blendertk/edit_utils/_edit_utils.py#L363) — Clear custom split normals on the given mesh object(s) — the Blender analogue of Maya's
- [`flip_normals(objects)`](blendertk/blendertk/edit_utils/_edit_utils.py#L383) — Reverse face winding / normals (bmesh ``reverse_faces``, headless).
- [`recalculate_normals(objects, inside=False)`](blendertk/blendertk/edit_utils/_edit_utils.py#L391) — Recalculate consistent face normals, outward by default / inward if ``inside`` (bmesh).
- [`clean_geometry(objects, *, merge=True, merge_distance=0.0001, delete_loose=True, degenerate=True, recalculate=True, fill_holes=False)`](blendertk/blendertk/edit_utils/_edit_utils.py#L405) — Clean mesh geometry — merge doubles, dissolve degenerate (zero-area) faces, remove loose
- [`crease_edges(objects, amount=10.0, angle=None)`](blendertk/blendertk/edit_utils/_edit_utils.py#L454) — Set Subdivision-Surface edge crease on the given mesh object(s) — mirror of Maya's
- [`mirror(objects, axis='x', pivot='object', merge_mode=1, delete_original=False, uninstance=False, merge_threshold=0.001)`](blendertk/blendertk/edit_utils/_edit_utils.py#L596) — Mirror mesh object(s) across an axis plane — mirror of ``mtk.EditUtils.mirror``.
- [`cut_along_axis(objects, axis='x', pivot='center', amount=1, offset=0.0, invert=False, delete=False, mirror=False, merge_threshold=0.0001)`](blendertk/blendertk/edit_utils/_edit_utils.py#L658) — Cut mesh object(s) along an axis — mirror of ``mtk.EditUtils.cut_along_axis``.
- [`wedge(objects, angle=90.0, divisions=4)`](blendertk/blendertk/edit_utils/_edit_utils.py#L735) — Wedge the selected faces about a selected hinge edge — mirror of Maya's
- [`snap_closest_verts(obj_a, obj_b, tolerance=10.0)`](blendertk/blendertk/edit_utils/_edit_utils.py#L802) — Snap each vertex of ``obj_a`` onto the closest vertex of ``obj_b`` within
- [`snap_to_grid(objects=None, grid_size=1.0, axes='xyz')`](blendertk/blendertk/edit_utils/_edit_utils.py#L831) — Snap to the nearest grid point — mirror of ``mtk.Snap.snap_to_grid``.
- [`snap_to_surface(source_meshes, target, offset=0.0, threshold=None, invert=False)`](blendertk/blendertk/edit_utils/_edit_utils.py#L869) — Project the source meshes' vertices onto the closest point of ``target``'s surface —
- [`get_similar_mesh(objects=None, *, tolerance=0.0, inc_orig=False, select=False, vertex=False, edge=False, face=False, triangle=False, shell=False, uvcoord=False, area=False, world_area=False, bounding_box=False)`](blendertk/blendertk/edit_utils/_edit_utils.py#L1073) — Find scene mesh objects similar to ``objects`` by topology / area / bounding-box metrics —
- [`separate_objects(objects=None, *, by_material=False, rename=False, center_pivots=True)`](blendertk/blendertk/edit_utils/_edit_utils.py#L1120) — Separate mesh(es) into loose parts, or one object per material (``by_material``) — Blender
- [`combine_objects(objects=None, *, group_by_material=False, cluster_by_distance=False, threshold=10000.0)`](blendertk/blendertk/edit_utils/_edit_utils.py#L1189) — Combine mesh objects into one — Blender mirror of mayatk's ``EditUtils.combine_objects``
- [`detach_components(*, duplicate=False, separate=True, separate_each=False)`](blendertk/blendertk/edit_utils/_edit_utils.py#L1230) — Detach the active mesh's selected faces — Blender mirror of mayatk's
- [`get_overlapping_faces(objects, delete=False, select=True, round_ndigits=5)`](blendertk/blendertk/edit_utils/_edit_utils.py#L1284) — Find faces geometrically coincident with another face — doubled geometry on *distinct*
- [`get_overlapping_duplicates(objects=None, retain=None, select=False, delete=False, round_ndigits=5)`](blendertk/blendertk/edit_utils/_edit_utils.py#L1326) — Find duplicate mesh objects overlapping in world space — mirror of
- [`loft(objects=None, *, close=False, reverse_normals=False, section_spans=1)`](blendertk/blendertk/edit_utils/_edit_utils.py#L1414) — Loft a mesh surface across a sequence of profile curves / mesh edge-loops — a Blender mesh
- **[`class EditUtils`](blendertk/blendertk/edit_utils/_edit_utils.py#L1477)** — Namespace mirror of mayatk's ``EditUtils`` (helpers also exposed module-level).

<a id="edit_utils--bevel"></a>
### `edit_utils/bevel.py`

Bevel tool — engine + Switchboard slot wiring for the co-located ``bevel.ui``.

- **[`class Bevel`](blendertk/blendertk/edit_utils/bevel.py#L25)** — Native ``bmesh.ops.bevel`` engine (mirror of mayatk's ``Bevel``).
  - `Bevel.bevel(objects=None, width=0.1, segments=1, profile=0.5, clamp_overlap=True, affect='EDGES', offset_type='OFFSET')` *(static)* — Bevel the selected edges (or vertices) of the given mesh objects.
- **[`class BevelSlots(ptk.LoggingMixin)`](blendertk/blendertk/edit_utils/bevel.py#L95)** — Switchboard slot wiring for the bevel UI — 1:1 mirror of mayatk's ``BevelSlots`` (same
  - `BevelSlots.header_init(self, widget)` — Configure header help text.
  - `BevelSlots.perform_operation(self, objects)`

<a id="edit_utils--bridge"></a>
### `edit_utils/bridge.py`

Bridge tool — engine + Switchboard slot wiring for the co-located ``bridge.ui``.

- **[`class Bridge`](blendertk/blendertk/edit_utils/bridge.py#L45)** — Native ``bmesh.ops.bridge_loops`` engine (mirror of mayatk's ``Bridge``).
  - `Bridge.bridge(objects=None, divisions=0, smoothing_angle=None, offset=0, merge=False)` *(static)* — Bridge the two selected open edge loops of each given mesh with new faces.
- **[`class BridgeSlots(ptk.LoggingMixin)`](blendertk/blendertk/edit_utils/bridge.py#L117)** — Switchboard slot wiring for the bridge UI — 1:1 mirror of mayatk's ``BridgeSlots`` (same
  - `BridgeSlots.header_init(self, widget)` — Configure header help text.
  - `BridgeSlots.perform_operation(self, objects)`

<a id="edit_utils--curtain"></a>
### `edit_utils/curtain.py`

Curtain (draped-cloth) generation — the Blender build over the vendored

- [`curtain_rail_from_selection(objects)`](blendertk/blendertk/edit_utils/curtain.py#L41) — Resolve a rail polyline from a Blender selection.
- [`create_curtain(rail, name='curtain', **options)`](blendertk/blendertk/edit_utils/curtain.py#L89) — Create a pleated, gravity-draped curtain mesh from a rail polyline.
- **[`class CurtainUtils`](blendertk/blendertk/edit_utils/curtain.py#L152)** — Namespace mirror of mayatk's curtain module (helpers also exposed module-level).
- **[`class CurtainRig`](blendertk/blendertk/edit_utils/curtain.py#L164)** — Make grabbable control handles drive a finished curtain — Blender mirror of mayatk's
  - `CurtainRig.attach(curtain, controls=5, dropoff=2.0, name=None)` *(static)* — Rig *curtain* with control-empty handles that pull the cloth via hooks.
- **[`class CurtainSlots(ptk.LoggingMixin)`](blendertk/blendertk/edit_utils/curtain.py#L318)** — Switchboard slot wiring for the curtain UI (live preview + rail resolution + presets).
  - `CurtainSlots.header_init(self, widget)` — Configure header help text (the preset combo lives in the panel).
  - `CurtainSlots.cmb000_init(self, widget)` — Wire the in-panel preset selector (built-in + user tiers) — mirror of the Maya panel.
  - `CurtainSlots.b001(self)` — Reset to Defaults.
  - `CurtainSlots.b002(self)` — Set Position to the bounding-box center of the selected object(s).
  - `CurtainSlots.perform_operation(self, objects)` — Build the curtain from the resolved rail (Preview entry point).

<a id="edit_utils--cut_on_axis"></a>
### `edit_utils/cut_on_axis.py`

Cut-On-Axis tool panel — Switchboard slot wiring for the co-located ``cut_on_axis.ui``.

- **[`class CutOnAxisSlots(ptk.LoggingMixin)`](blendertk/blendertk/edit_utils/cut_on_axis.py#L21)** — Switchboard slot wiring for the cut-on-axis UI (live preview).
  - `CutOnAxisSlots.header_init(self, widget)` — Configure header help text.
  - `CutOnAxisSlots.perform_operation(self, objects)`

<a id="edit_utils--duplicate_grid"></a>
### `edit_utils/duplicate_grid.py`

Grid array duplication + its tool panel — mirror of mayatk's ``edit_utils.duplicate_grid``.

- [`duplicate_grid(objects, dimensions=(2, 2, 1), spacing=0.0, mode='instance')`](blendertk/blendertk/edit_utils/duplicate_grid.py#L29) — Duplicate object(s) into a 3D grid — mirror of mayatk's ``DuplicateGrid.duplicate_grid``.
- **[`class DuplicateGrid`](blendertk/blendertk/edit_utils/duplicate_grid.py#L78)** — Namespace mirror of mayatk's ``DuplicateGrid`` (helper also exposed module-level).
- **[`class DuplicateGridSlots(ptk.LoggingMixin)`](blendertk/blendertk/edit_utils/duplicate_grid.py#L89)** — Switchboard slot wiring for the Duplicate-Grid panel — 1:1 objectName mirror of
  - `DuplicateGridSlots.header_init(self, widget)` — Configure header help text.
  - `DuplicateGridSlots.b001(self)` — Reset to Defaults: Resets all UI widgets to their default values.
  - `DuplicateGridSlots.perform_operation(self, objects)`

<a id="edit_utils--duplicate_linear"></a>
### `edit_utils/duplicate_linear.py`

Linear array duplication + its tool panel — mirror of mayatk's ``edit_utils.duplicate_linear``.

- [`duplicate_linear(objects, num_copies, translate=(0, 0, 0), rotate=(0, 0, 0), scale=(1, 1, 1), weight_bias=0.5, weight_curve=4, pivot='object', calculation_mode='weighted', instance=True)`](blendertk/blendertk/edit_utils/duplicate_linear.py#L24) — Duplicate object(s) along a linear path — mirror of mayatk's
- **[`class DuplicateLinear`](blendertk/blendertk/edit_utils/duplicate_linear.py#L88)** — Namespace mirror of mayatk's ``DuplicateLinear`` (helper also exposed module-level).
- **[`class DuplicateLinearSlots(ptk.LoggingMixin)`](blendertk/blendertk/edit_utils/duplicate_linear.py#L99)** — Switchboard slot wiring for the Duplicate-Linear panel — 1:1 objectName mirror of
  - `DuplicateLinearSlots.header_init(self, widget)` — Configure header help text.
  - `DuplicateLinearSlots.toggle_weight_ui(self)` — Disable weight UI components if the current calculation mode doesn't use them.
  - `DuplicateLinearSlots.b001(self)` — Reset to Defaults: Resets all UI widgets to their default values.
  - `DuplicateLinearSlots.perform_operation(self, objects)` — Perform the linear duplication operation.

<a id="edit_utils--duplicate_radial"></a>
### `edit_utils/duplicate_radial.py`

Radial array duplication + its tool panel — mirror of mayatk's ``edit_utils.duplicate_radial``.

- [`duplicate_radial(objects, num_copies, start_angle=0.0, end_angle=360.0, weight_bias=0.5, weight_curve=0.5, rotate_axis='y', offset=(0, 0, 0), translate=(0, 0, 0), rotate=(0, 0, 0), scale=(1, 1, 1), pivot='object', keep_original=False, instance=False, combine=False, suffix=True)`](blendertk/blendertk/edit_utils/duplicate_radial.py#L40) — Duplicate object(s) in a radial pattern — mirror of mayatk's
- **[`class DuplicateRadial`](blendertk/blendertk/edit_utils/duplicate_radial.py#L138)** — Namespace mirror of mayatk's ``DuplicateRadial`` (helper also exposed module-level).
- **[`class DuplicateRadialSlots(ptk.LoggingMixin)`](blendertk/blendertk/edit_utils/duplicate_radial.py#L149)** — Switchboard slot wiring for the Duplicate-Radial panel.
  - `DuplicateRadialSlots.header_init(self, widget)` — Configure header help text.
  - `DuplicateRadialSlots.b001(self)` — Reset to Defaults: Resets all UI widgets to their default values.
  - `DuplicateRadialSlots.perform_operation(self, objects)`

<a id="edit_utils--dynamic_pipe"></a>
### `edit_utils/dynamic_pipe.py`

Dynamic Pipe tool — Blender port of mayatk's ``edit_utils.dynamic_pipe``.

- **[`class DynamicPipe(ptk.LoggingMixin)`](blendertk/blendertk/edit_utils/dynamic_pipe.py#L36)** — Build a pipe-style mesh driven by a chain of handle objects (Empties/locators) — Blender
- **[`class DynamicPipeSlots(ptk.LoggingMixin)`](blendertk/blendertk/edit_utils/dynamic_pipe.py#L138)** — Switchboard slot wiring for the co-located ``dynamic_pipe.ui`` (mirror of mayatk's
  - `DynamicPipeSlots.header_init(self, widget)` — Configure header help text.
  - `DynamicPipeSlots.b000(self)` — Initialize Pipe — build pipe from the current selection (name-ordered).

<a id="edit_utils--macros"></a>
### `edit_utils/macros.py`

Hotkey macros — the Blender counterpart of ``mayatk.edit_utils.macros``.

- **[`class DisplayMacros(_ViewportMixin)`](blendertk/blendertk/edit_utils/macros.py#L85)**
  - `DisplayMacros.m_back_face_culling(cls)` *(class)* — Toggle Back-Face Culling in the viewport.
  - `DisplayMacros.m_isolate_selected(cls)` *(class)* — Isolate the current selection (toggle Local View).
  - `DisplayMacros.m_wireframe(cls)` *(class)* — Cycle the wireframe-on-shaded overlay: Off -> Full -> Reduced (mirrors Maya's
  - `DisplayMacros.m_shading(cls)` *(class)* — Cycle viewport shading: Wireframe -> Solid -> Material Preview.
  - `DisplayMacros.m_lighting(cls)` *(class)* — Cycle Solid-mode viewport lighting Studio -> MatCap -> Flat (Maya's displayLights
  - `DisplayMacros.m_grid(cls)` *(class)* — Toggle the floor grid (with its X/Y axis lines — together they ARE Blender's
  - `DisplayMacros.m_grid_and_image_planes(cls)` *(class)* — Toggle the floor grid and reference image-empties together (the grid leads).
  - `DisplayMacros.m_cycle_display_state(cls)` *(class)* — Cycle the selected objects' draw type: Textured -> Wireframe -> Bounds (driven by the
  - `DisplayMacros.m_smooth_preview(cls)` *(class)* — Toggle a live Subdivision-Surface preview on the selected meshes.
  - `DisplayMacros.m_frame(cls)` *(class)* — Frame the selection (or the whole scene when nothing is selected).
- **[`class EditMacros(_ViewportMixin)`](blendertk/blendertk/edit_utils/macros.py#L225)**
  - `EditMacros.m_multi_component()` *(static)* — Multi-component selection — enable vertex+edge+face select together (edit mode).
  - `EditMacros.m_paste_and_rename(cls)` *(class)* — Paste objects (Blender's paste adds no 'pasted__' prefix, so no rename needed).
  - `EditMacros.m_merge_vertices(tolerance=0.0001)` *(static)* — Merge vertices by distance — on the active mesh in Edit Mode, or across every selected
  - `EditMacros.m_group()` *(static)* — Group the selected objects under an Empty at the selection's center, keeping their
- **[`class SelectionMacros`](blendertk/blendertk/edit_utils/macros.py#L272)**
  - `SelectionMacros.m_object_selection()` *(static)* — Object selection mask — leave edit mode (object mode).
  - `SelectionMacros.m_vertex_selection(cls)` *(class)* — Vertex selection mask (edit mode).
  - `SelectionMacros.m_edge_selection(cls)` *(class)* — Edge selection mask (edit mode).
  - `SelectionMacros.m_face_selection(cls)` *(class)* — Face selection mask (edit mode).
  - `SelectionMacros.m_invert_selection()` *(static)* — Invert the current selection (component-aware).
  - `SelectionMacros.m_toggle_UV_select_type()` *(static)* — Toggle UV select mode between Vertex and Face (Blender's ``uv_select_mode`` enum is
- **[`class UiMacros(_ViewportMixin)`](blendertk/blendertk/edit_utils/macros.py#L325)**
  - `UiMacros.m_toggle_panels(cls, toggle_menu: bool = True, toggle_panels: bool = True)` *(class)* — Toggle the main window's bars (topbar + statusbar) and the 3D viewport's header,
- **[`class AnimationMacros`](blendertk/blendertk/edit_utils/macros.py#L368)**
  - `AnimationMacros.m_set_selected_keys(cls)` *(class)* — Set keys on the selected objects' transform channels at the current frame.
  - `AnimationMacros.m_unset_selected_keys(cls)` *(class)* — Remove keys on the selected objects' transform channels at the current frame.
- **[`class MacroManager`](blendertk/blendertk/edit_utils/macros.py#L394)** — Register ``m_*`` macros to Blender hotkeys from the same string spec Maya uses.
  - `MacroManager.set_macros(cls, *args)` *(class)* — Register a macro per spec string (``"m_name, key=1, cat=Display"``).
  - `MacroManager.call_with_input(func, input_string)` *(static)* — Parse ``"arg, key=val, ..."`` into positional/keyword args and call ``func``.
  - `MacroManager.set_macro(cls, name, key=None, cat=None, ann=None)` *(class)* — Bind macro ``name`` to ``key`` (e.g.
  - `MacroManager.remove_macros(cls)` *(class)* — Remove every keymap item this manager added (clean teardown / reload).
  - `MacroManager.list_available_macros(cls) -> Dict[str, str]` *(class)* — Discover every ``m_*`` macro callable, mapped to its annotation.
  - `MacroManager.macro_label(cls, name: str) -> str` *(class)* — Humanize a macro name for display, e.g.
  - `MacroManager.macro_category(cls, name: str) -> str` *(class)* — Default category for a macro, derived from the ``*Macros`` mixin that
  - `MacroManager.list_categories(cls) -> List[str]` *(class)* — Sorted distinct default categories across all discoverable macros.
  - `MacroManager.macro_help(cls, name: str) -> str` *(class)* — Return a macro's full (dedented) docstring — the single source of
  - `MacroManager.get_current_bindings(cls) -> Dict[str, dict]` *(class)* — Return the *live* key + category for every available macro.
  - `MacroManager.apply_bindings(cls, bindings: Dict[str, dict]) -> None` *(class)* — Apply a binding set ``{name: {"key", "cat"}}``.
  - `MacroManager.clear_hotkey(cls, name: str, key: Optional[str] = None) -> None` *(class)* — Unbind ``name``'s hotkey across every keymap it was registered into
  - `MacroManager.find_conflicts(cls, bindings: Dict[str, dict]) -> Dict[str, List[str]]` *(class)* — Return ``{normalized_key: [macro, ...]}`` for keys bound more than once.
  - `MacroManager.qt_sequence_to_maya_key(cls, sequence: str) -> str` *(class)* — Convert a Qt key-sequence string (``"Ctrl+Shift+I"``) to the
  - `MacroManager.maya_key_to_qt_sequence(cls, key: str) -> str` *(class)* — Convert a Maya-style key token (``"ctl+sht+i"``) to a Qt key-sequence
  - `MacroManager.list_presets(cls) -> List[str]` *(class)* — Return all preset names (built-in + user, user shadows built-in).
  - `MacroManager.load_preset(cls, name: str) -> Dict[str, dict]` *(class)* — Return the binding set stored under *name* (``_meta`` stripped).
  - `MacroManager.save_preset(cls, name: str, bindings: Optional[Dict[str, dict]] = None) -> str` *(class)* — Save *bindings* (default: the current bindings) as user preset *name*.
  - `MacroManager.delete_preset(cls, name: str) -> bool` *(class)* — Delete a *user* preset (built-ins are read-only).
  - `MacroManager.get_active_preset(cls) -> Optional[str]` *(class)* — The last-selected/applied preset name, or ``None``.
  - `MacroManager.set_active_preset(cls, name: Optional[str]) -> None` *(class)* — Set (or clear, with ``None``) the active-preset pointer.
  - `MacroManager.apply_saved_macros(cls, name: Optional[str] = None) -> None` *(class)* — Apply a saved preset/template's bindings on demand.
  - `MacroManager.editor_categories(cls) -> List[str]` *(class)* — Mixin-derived categories plus any custom category carried by the
  - `MacroManager.get_editor_registry(cls, category: str) -> List[dict]` *(class)* — Editor-shaped entries for every macro in *category*.
  - `MacroManager.apply_editor_binding(cls, name: str, sequence: str) -> None` *(class)* — Apply a Qt key sequence captured in the editor (``""`` clears).
  - `MacroManager.export_bindings(cls) -> Dict[str, dict]` *(class)* — The persist-worthy subset of the live bindings — every macro with a
  - `MacroManager.import_bindings(cls, data: Optional[Dict[str, dict]]) -> int` *(class)* — Apply a loaded binding set (the preset ``value_applier``): release
  - `MacroManager.show_editor(cls, parent=None)` *(class)* — Open the Macro Manager — the unified uitk ``ShortcutEditor`` over
- **[`class Macros(MacroManager, DisplayMacros, EditMacros, SelectionMacros, AnimationMacros, UiMacros)`](blendertk/blendertk/edit_utils/macros.py#L1052)** — Concrete macro holder — combines every macro mixin with the manager (mirror of mayatk).

<a id="edit_utils--mirror"></a>
### `edit_utils/mirror.py`

Mirror tool panel — Switchboard slot wiring for the co-located ``mirror.ui``.

- **[`class MirrorSlots(ptk.LoggingMixin)`](blendertk/blendertk/edit_utils/mirror.py#L28)** — Switchboard slot wiring for the mirror UI (live preview + axis/pivot/merge combos).
  - `MirrorSlots.header_init(self, widget)` — Configure header help text.
  - `MirrorSlots.perform_operation(self, objects)`

<a id="edit_utils--naming--_naming"></a>
### `edit_utils/naming/_naming.py`

Batch object naming — Blender port of mayatk's ``edit_utils.naming.Naming``.

- **[`class Naming(ptk.HelpMixin)`](blendertk/blendertk/edit_utils/naming/_naming.py#L22)** — Batch find / rename / suffix scene objects (mirror of mayatk's ``Naming``).
  - `Naming.rename(cls, objects, to, fltr='', regex=False, ignore_case=False, retain_suffix=False, valid_suffixes=None)` *(class)* — Rename objects by pattern — Blender mirror of mayatk's ``Naming.rename``.
  - `Naming.generate_unique_name(cls, base_name, suffix='_', padding=3)` *(class)* — A unique object name based on ``base_name`` (``Cube`` → ``Cube_001``) — mirror of
  - `Naming.strip_illegal_chars(input_data, replace_with='_')` *(static)* — Replace characters outside ``[A-Za-z0-9_]`` (engine-export-safe naming).
  - `Naming.strip_chars(cls, objects, num_chars=1, trailing=False)` *(class)* — Delete ``num_chars`` leading (or ``trailing``) characters from each object's name —
  - `Naming.set_case(objects, case='capitalize')` *(static)* — Rename objects by Python string case op — ``upper`` / ``lower`` / ``capitalize`` /
  - `Naming.suffix_by_type(cls, objects, group_suffix='_GRP', locator_suffix='_LOC', joint_suffix='_JNT', mesh_suffix='_GEO', nurbs_curve_suffix='_CRV', camera_suffix='_CAM', light_suffix='_LGT', custom_suffixes=None, strip_trailing_ints=False, strip_trailing_underscores=False, strip_trailing_padding=True)` *(class)* — Append a conventional type suffix (stripping any existing known suffix) — mirror of
  - `Naming.append_location_based_suffix(cls, objects, first_obj_as_ref=False, alphabetical=False, strip_trailing_ints=True, strip_defined_suffixes=True, valid_suffixes=None, reverse=False, independent_groups=False)` *(class)* — Suffix objects by their distance from a reference point (origin, or the first object's

<a id="edit_utils--naming--naming_slots"></a>
### `edit_utils/naming/naming_slots.py`

Switchboard slots for the Naming panel — Blender port of mayatk's ``NamingSlots``.

- **[`class NamingSlots(Naming, ptk.LoggingMixin)`](blendertk/blendertk/edit_utils/naming/naming_slots.py#L23)** — Switchboard slots for the Naming panel.
  - `NamingSlots.header_init(self, widget)` — Configure header menu with tool description and workflow instructions.
  - `NamingSlots.valid_suffixes(self)` *(property)* — Get current valid suffixes from tb003 widget fields.
  - `NamingSlots.txt000_init(self, widget)` — Initialize Find
  - `NamingSlots.txt000(self, widget)` — Find: filter/select scene objects whose name matches the search pattern.
  - `NamingSlots.txt001_init(self, widget)` — Initialize Rename
  - `NamingSlots.txt001(self, widget)` — Rename: rename matched objects (find → replace, with regex / suffix options).
  - `NamingSlots.tb000_init(self, widget)` — Initialize Convert Case
  - `NamingSlots.tb000(self, widget)` — Convert Case
  - `NamingSlots.tb001_init(self, widget)` — Initialize Suffix By Location
  - `NamingSlots.tb001(self, widget)` — Suffix By Location
  - `NamingSlots.tb002_init(self, widget)` — Initialize Strip Chars
  - `NamingSlots.tb002(self, widget)` — Strip Chars: remove a number of leading/trailing characters from the selected names.
  - `NamingSlots.tb003_init(self, widget)` — Initialize Suffix By Type
  - `NamingSlots.tb003(self, widget)` — Suffix By Type

<a id="edit_utils--selection"></a>
### `edit_utils/selection.py`

Category-driven select-by-type — mirror of mayatk's ``edit_utils.selection.Selection``

- **[`class Selection`](blendertk/blendertk/edit_utils/selection.py#L33)** — Namespace mirror of mayatk's ``Selection`` (category-driven select-by-type).
  - `Selection.select_by_type(selection_type, objects=None, mode='replace')` *(static)* — Select objects by category or leaf type (mirror of ``mtk.Selection.select_by_type``).
  - `Selection.select_children(objects)` *(static)* — The immediate children of the given objects (one level below only).
  - `Selection.select_hierarchy_above(objects)` *(static)* — All ancestor objects above the given objects (full parent chain).
  - `Selection.select_hierarchy_below(objects)` *(static)* — All descendant objects below the given objects (full child subtree).
  - `Selection.convert_to(obj, mode, contained=False)` *(static)* — Convert the current Edit-Mode selection to `mode` ('VERT'/'EDGE'/'FACE') — Maya
  - `Selection.select_face_path(obj)` *(static)* — Face Path: the shortest face-adjacency path between exactly two selected faces —
  - `Selection.select_vertex_perimeter(obj)` *(static)* — Vertex Perimeter: the vertices of the boundary loop around the current face-region
  - `Selection.select_edge_perimeter(obj)` *(static)* — Edge Perimeter: the boundary edge loop around the current face-region selection —
  - `Selection.select_face_perimeter(obj)` *(static)* — Face Perimeter: the ring of faces immediately surrounding the current face-region
  - `Selection.select_border_edges(obj)` *(static)* — Border Edges: the naked (open, single-face) mesh edges among the current
  - `Selection.select_shell_border(obj)` *(static)* — Shell Border: the naked/open boundary edges of the connected shell(s) touching the
  - `Selection.select_uv_shell(obj)` *(static)* — UV Shell: every face sharing a UV island with the current selection — Maya's
  - `Selection.select_uv_shell_border(obj)` *(static)* — UV Shell Border: the UV-island boundary edges of the UV shell(s) touching the current
  - `Selection.select_uv_perimeter(obj)` *(static)* — UV Perimeter: the boundary of the current selection's UV footprint — Maya's
  - `Selection.select_uv_edge_loop(obj)` *(static)* — UV Edge Loop: the topological edge loop through the selection, truncated at UV seams —
  - `Selection.get_available_selection_types()` *(static)* — A flat, sorted list of every leaf selection-type label.
  - `Selection.get_selection_categories()` *(static)* — Dict of category -> leaf-label list (mirror of ``mtk.Selection.get_selection_categories``).

<a id="edit_utils--snap"></a>
### `edit_utils/snap.py`

Snap tool — Switchboard slot wiring for the co-located ``snap.ui``.

- **[`class SnapSlots(ptk.LoggingMixin)`](blendertk/blendertk/edit_utils/snap.py#L28)** — Switchboard slot wiring for the Snap panel (mirror of mayatk's ``SnapSlots``).
  - `SnapSlots.header_init(self, widget)` — Configure header help text.
  - `SnapSlots.b000_init(self, widget)` — Initialize Snap to Surface button option box.
  - `SnapSlots.b000(self)` — Snap to Surface button.
  - `SnapSlots.b001_init(self, widget)` — Initialize Snap to Closest Vertex button option box.
  - `SnapSlots.b001(self)` — Snap to Closest Vertex button.
  - `SnapSlots.b002_init(self, widget)` — Initialize Snap to Grid button option box.
  - `SnapSlots.b002(self)` — Snap to Grid button.

<a id="edit_utils--target_weld"></a>
### `edit_utils/target_weld.py`

Target Weld — interactive drag-a-vertex-onto-another merge tool.

- [`project_points(mvp: np.ndarray, coords: np.ndarray, width: float, height: float) -> Tuple[np.ndarray, np.ndarray]`](blendertk/blendertk/edit_utils/target_weld.py#L81) — Project ``coords`` (N,3) through the 4x4 ``mvp`` into pixel space.
- [`pick_screen_point(mouse_xy: Sequence[float], points_xy: np.ndarray, depths: np.ndarray, radius: float = PICK_RADIUS, exclude: Optional[int] = None) -> Optional[int]`](blendertk/blendertk/edit_utils/target_weld.py#L107) — Index of the best pick candidate within ``radius`` px of ``mouse_xy``, or ``None``.
- [`weld_position(src_co, tgt_co, merge_to_center: bool = False)`](blendertk/blendertk/edit_utils/target_weld.py#L134) — The merged vertex's final position: the target (Maya Target Weld) or the midpoint
- [`dash_segments(p0, p1, dash: float = DASH_LEN, gap: float = GAP_LEN)`](blendertk/blendertk/edit_utils/target_weld.py#L142) — 2D dashed-line vertex pairs from ``p0`` to ``p1`` (flat list of (x, y) endpoints,
- [`weld_pair(bm, v_src, v_tgt, merge_to_center: bool = False) -> None`](blendertk/blendertk/edit_utils/target_weld.py#L164) — Merge ``v_src`` into ``v_tgt`` on ``bm`` (both verts of the same BMesh;
- [`target_weld(merge_to_center: bool = False) -> bool`](blendertk/blendertk/edit_utils/target_weld.py#L570) — Activate the interactive Target Weld tool (mirror of Maya's ``MergeVertexTool``).
- **[`class TargetWeld`](blendertk/blendertk/edit_utils/target_weld.py#L619)** — Namespace class (mirror of the co-located-tool convention;

<a id="env_utils--_env_utils"></a>
### `env_utils/_env_utils.py`

blendertk environment / scene-library utilities — the engine behind the Reference Manager panel.

- [`find_blend_files(root_dir, recursive=True, filter_text='')`](blendertk/blendertk/env_utils/_env_utils.py#L30) — Every ``.blend`` file under ``root_dir`` (recursively by default), optionally name-filtered.
- [`list_libraries()`](blendertk/blendertk/env_utils/_env_utils.py#L65) — Every linked library as a record: ``{name, library, filepath, abspath, exists}``.
- [`linked_blend_paths()`](blendertk/blendertk/env_utils/_env_utils.py#L83) — Set of normalized absolute paths of the ``.blend`` files currently linked as libraries.
- [`is_blend_linked(path)`](blendertk/blendertk/env_utils/_env_utils.py#L88) — True iff ``path`` is already linked as a library.
- [`link_blend_file(path, link=True, instance=True)`](blendertk/blendertk/env_utils/_env_utils.py#L93) — Link (or append, ``link=False``) every collection from ``path`` and instance them into the
- [`reload_library(library)`](blendertk/blendertk/env_utils/_env_utils.py#L128) — Reload a library from disk (``library`` is a datablock or its name).
- [`remove_library(library)`](blendertk/blendertk/env_utils/_env_utils.py#L142) — Remove a library and everything linked from it (datablock or name).
- [`make_library_local(library)`](blendertk/blendertk/env_utils/_env_utils.py#L156) — Make every datablock linked from ``library`` **local** (a native, editable copy) and drop the
- [`find_workspaces(root_dir, recursive=False)`](blendertk/blendertk/env_utils/_env_utils.py#L198) — Project folders under ``root_dir`` — the root itself when it directly holds .blend files,
- [`open_scene(path)`](blendertk/blendertk/env_utils/_env_utils.py#L243) — Open a .blend file (replaces the current file — Maya's ``file -open``).
- [`format_scene_name(name, case=None, suffix='')`](blendertk/blendertk/env_utils/_env_utils.py#L256) — Apply a naming convention to a base scene name — ``case`` via :meth:`pythontk.StrUtils.set_case`
- [`save_scene_as(directory, name, case=None, suffix='', subfolder='', overwrite=True)`](blendertk/blendertk/env_utils/_env_utils.py#L274) — Save the current scene as a .blend under ``directory`` with naming conventions applied —
- [`rename_scene_file(path, new_base)`](blendertk/blendertk/env_utils/_env_utils.py#L313) — Rename a .blend on disk (and its ``.blend1`` backup) — mirror of mayatk's ``rename_scene``.
- [`delete_scene_file(path)`](blendertk/blendertk/env_utils/_env_utils.py#L338) — Delete a .blend (and its ``.blend1`` backup) — mirror of mayatk's ``delete_scene``.
- [`set_reference_display_mode(library, mode)`](blendertk/blendertk/env_utils/_env_utils.py#L380) — Set the display override for a linked library's objects — mirror of mayatk's
- [`get_reference_display_mode(library)`](blendertk/blendertk/env_utils/_env_utils.py#L403) — Return the active display mode (``"off"`` / ``"reference"`` / ``"template"``) for a linked
- **[`class EnvUtils`](blendertk/blendertk/env_utils/_env_utils.py#L422)** — Namespace mirror of mayatk's ``EnvUtils`` (helpers also exposed module-level).

<a id="env_utils--blender_connection"></a>
### `env_utils/blender_connection.py`

Launch a FRESH headless Blender to run a script / code string and capture its output — the

- **[`class BlenderConnection`](blendertk/blendertk/env_utils/blender_connection.py#L30)** — Run scripts in fresh headless Blender instances (mirror of ``MayaConnection``'s role).
  - `BlenderConnection.find_blender(cls) -> Optional[str]` *(class)* — Locate a Blender executable: ``$BLENDER_EXE`` / ``$BLENDER`` → ``PATH`` → common install
  - `BlenderConnection.run_script(self, script_path: str, script_args=None, *, blend_file: Optional[str] = None, extra_args=None, timeout: Optional[float] = 600, output_file: Optional[str] = None, env: Optional[dict] = None)` — Run *script_path* in a fresh headless Blender;
  - `BlenderConnection.run_code(self, code: str, **kwargs)` — Run a Python *code* string in a fresh headless Blender (via a temp script).
  - `BlenderConnection.run_result(self, script_path: Optional[str] = None, *, code: Optional[str] = None, **kwargs)` — Run a script / code string that prints a ``===RESULT: PASS===`` sentinel and report

<a id="env_utils--fbx_utils"></a>
### `env_utils/fbx_utils.py`

FBX import / export helpers — the Blender counterpart of mayatk's ``env_utils.fbx_utils``

- [`export_selection_fbx(filepath=None, objects=None, **fbx_opts)`](blendertk/blendertk/env_utils/fbx_utils.py#L176) — Export the selection (or ``objects``) to an FBX file for an external-app hand-off.
- [`import_fbx(filepath, **fbx_opts)`](blendertk/blendertk/env_utils/fbx_utils.py#L186) — Import an FBX file;
- **[`class FbxUtils`](blendertk/blendertk/env_utils/fbx_utils.py#L72)** — FBX import / export over ``bpy.ops`` (mirror of mayatk's ``FbxUtils`` export surface).
  - `FbxUtils.export(filepath=None, objects=None, selection_only=True, **fbx_opts)` *(static)* — Export to an FBX file — the consolidated counterpart of mayatk's ``FbxUtils.export``.
  - `FbxUtils.import_fbx(filepath, **fbx_opts)` *(static)* — Import an FBX file (wrapper over ``bpy.ops.import_scene.fbx``).

<a id="env_utils--handoff_export"></a>
### `env_utils/handoff_export.py`

Blender-side selection + FBX-export hooks shared by the hand-off bridge engines.

- **[`class BlenderExportMixin`](blendertk/blendertk/env_utils/handoff_export.py#L23)** — The Blender producer hooks for hand-off bridges (``_resolve_objects`` + ``_produce``).

<a id="env_utils--hierarchy_manager--_hierarchy_manager"></a>
### `env_utils/hierarchy_manager/_hierarchy_manager.py`

Hierarchy Manager core engine — mirror of mayatk's ``env_utils.hierarchy_manager._hierarchy_manager…

- [`build_path(obj) -> str`](blendertk/blendertk/env_utils/hierarchy_manager/_hierarchy_manager.py#L40) — Pipe-joined hierarchy path from the root down to ``obj`` (e.g.
- [`should_keep_node_by_type(obj, node_types: List[str], exclude: bool = True) -> bool`](blendertk/blendertk/env_utils/hierarchy_manager/_hierarchy_manager.py#L55) — Filter by Blender object type — mirror of mayatk's shape-type filter.
- **[`class HierarchyMapBuilder`](blendertk/blendertk/env_utils/hierarchy_manager/_hierarchy_manager.py#L61)** — Builds hierarchy path maps for Blender objects (mirror of mayatk's ``HierarchyMapBuilder``).
  - `HierarchyMapBuilder.build_path_map(objects) -> Dict[str, Any]` *(static)* — Map every object in ``objects`` to its hierarchy path (see :func:`build_path`).
- **[`class HierarchyManager(ptk.LoggingMixin)`](blendertk/blendertk/env_utils/hierarchy_manager/_hierarchy_manager.py#L76)** — Core hierarchy analysis and repair manager (mirror of mayatk's ``HierarchyManager``).
  - `HierarchyManager.analyze_hierarchies(self, current_objects, reference_objects, filter_meshes: bool = True, filter_cameras: bool = False, filter_lights: bool = False) -> Dict[str, Any]` — Analyze differences between the current scene and a reference object set.
  - `HierarchyManager.create_stubs(self, paths: Optional[List[str]] = None) -> List[str]` — Create empty placeholder Empties for missing hierarchy paths.
  - `HierarchyManager.quarantine_extras(self, group: str = '_QUARANTINE', paths: Optional[List[str]] = None, skip_animated: bool = True) -> List[str]` — Move extra (scene-only) items to a root-level quarantine Empty.
  - `HierarchyManager.fix_fuzzy_renames(self, items: Optional[List[Dict[str, str]]] = None) -> List[str]` — Rename nodes identified as fuzzy matches to their reference names.
  - `HierarchyManager.fix_reparented(self, items: Optional[List[Dict[str, str]]] = None) -> List[str]` — Move reparented nodes to match their reference hierarchy position.

<a id="env_utils--hierarchy_manager--hierarchy_manager_slots"></a>
### `env_utils/hierarchy_manager/hierarchy_manager_slots.py`

Slots for the Hierarchy Manager panel -- Blender port of mayatk's ``env_utils.hierarchy_manager``.

- **[`class HierarchyManagerController(ptk.LoggingMixin)`](blendertk/blendertk/env_utils/hierarchy_manager/hierarchy_manager_slots.py#L68)** — Controller for hierarchy management operations.
  - `HierarchyManagerController.workspace(self)` *(property)*
  - `HierarchyManagerController.reference_path(self) -> str` *(property)* — The current reference scene path.
  - `HierarchyManagerController.analyze_hierarchies(self, reference_path: str, fuzzy_matching: bool = True, dry_run: bool = True, filter_meshes: bool = False) -> bool` — Link the reference file (or reuse the cached link) and diff it against the scene.
  - `HierarchyManagerController.repair_hierarchies(self, create_stubs: bool = True, quarantine_extras: bool = True, quarantine_group: str = '_QUARANTINE', skip_animated: bool = True, fix_reparented: bool = True, fix_fuzzy_renames: bool = True, dry_run: bool = True) -> bool` — Run repair operations on the current scene to match the reference hierarchy.
  - `HierarchyManagerController.select_objects(object_names: List[str]) -> int` *(static)* — Select objects in the Blender scene by name.
  - `HierarchyManagerController.populate_reference_tree(self, tree_widget, reference_path: str = None)` — Populate the reference tree — handles cache, library link, and rendering.
  - `HierarchyManagerController.refresh_trees(self, restore_selection: bool = True)` — Refresh both tree widgets with current hierarchy data.
  - `HierarchyManagerController.is_path_ignored(self, tree_widget, path)`
  - `HierarchyManagerController.clear_ignored_paths(self)`
  - `HierarchyManagerController.log_diff_results(self)` — Log detailed hierarchy difference analysis results using rich formatting.
  - `HierarchyManagerController.get_recent_reference_scenes(self) -> List[str]` — Get recent reference scenes from settings.
  - `HierarchyManagerController.save_recent_reference_scene(self, scene_path: str)` — Save reference scene to recent list.
- **[`class HierarchyManagerSlots(ptk.LoggingMixin)`](blendertk/blendertk/env_utils/hierarchy_manager/hierarchy_manager_slots.py#L711)** — Slots class for hierarchy management UI operations.
  - `HierarchyManagerSlots.header_init(self, widget)` — Initialize the header widget.
  - `HierarchyManagerSlots.tree000_init(self, widget)` — Initialize the reference/linked hierarchy tree widget.
  - `HierarchyManagerSlots.tree001_init(self, widget)` — Initialize the current scene hierarchy tree widget.
  - `HierarchyManagerSlots.cmb_diff_options_init(self, widget)` — Populate the diff-options WidgetComboBox below the Diff button.
  - `HierarchyManagerSlots.cmb_pull_options_init(self, widget)` — Pull options — disabled (Pull isn't ported yet;
  - `HierarchyManagerSlots.tb002_init(self, widget)` — Pull button — disabled (see module docstring).
  - `HierarchyManagerSlots.tb003_init(self, widget)` — Initialize the fix/repair toggle button with options menu.
  - `HierarchyManagerSlots.tb001(self, state=None)` — Run the diff analysis using settings from cmb_diff_options.
  - `HierarchyManagerSlots.tb002(self, state=None)` — Pull — not yet ported (see module docstring).
  - `HierarchyManagerSlots.tb003(self, state=None)` — Toggle button for fix/repair operations.
  - `HierarchyManagerSlots.b003(self)` — Browse for reference scene file.
  - `HierarchyManagerSlots.b005(self)` — Refresh current scene hierarchy tree.
  - `HierarchyManagerSlots.b006(self)` — Select checked objects in the scene.
  - `HierarchyManagerSlots.b007(self)` — Expand all items in current scene tree.
  - `HierarchyManagerSlots.b008(self)` — Collapse all items in current scene tree.
  - `HierarchyManagerSlots.b009(self)` — Refresh reference hierarchy tree.
  - `HierarchyManagerSlots.b011(self)` — Show differences between hierarchies.
  - `HierarchyManagerSlots.b012(self)` — Analyze hierarchies and perform comparison (no auto-select/expand — see tb001).
  - `HierarchyManagerSlots.b013(self)` — Ignore selected items in the reference tree.
  - `HierarchyManagerSlots.b014(self)` — Unignore selected items in the reference tree.
  - `HierarchyManagerSlots.b015(self)` — Ignore selected items in the current scene tree.
  - `HierarchyManagerSlots.b016(self)` — Unignore selected items in the current scene tree.
  - `HierarchyManagerSlots.b018(self)` — Delete selected objects from the Blender scene and refresh the tree.
  - `HierarchyManagerSlots.b017(self)` — Rename current-scene items to match reference names.
  - `HierarchyManagerSlots.count_tree_items(self, tree_widget)` — Count total items in a tree widget.

<a id="env_utils--hierarchy_manager--hierarchy_sidecar"></a>
### `env_utils/hierarchy_manager/hierarchy_sidecar.py`

Hierarchy sidecar manifest management — mirror of mayatk's

- **[`class HierarchySidecar`](blendertk/blendertk/env_utils/hierarchy_manager/hierarchy_sidecar.py#L24)** — Manages hierarchy sidecar files stored alongside export files.
  - `HierarchySidecar.base_stem(cls, export_path: str) -> str` *(class)* — Return the export stem with any trailing ``_vNN`` suffix stripped.
  - `HierarchySidecar.manifest_path_for(cls, export_path: str, *, base_stem: bool = False) -> str` *(class)* — Return the sidecar manifest path for an export file.
  - `HierarchySidecar.diff_report_path_for(cls, export_path: str, *, base_stem: bool = False) -> str` *(class)* — Return the sidecar diff report path for an export file.
  - `HierarchySidecar.find_legacy_manifest(cls, export_path: str) -> Optional[str]` *(class)* — Return the path of a legacy per-version sidecar to migrate from.
  - `HierarchySidecar.ensure_base_name(cls, export_path: str) -> Optional[str]` *(class)* — Migrate a legacy per-version manifest to the base-stem name.
  - `HierarchySidecar.rename(cls, old_export_path: str, new_export_path: str) -> list` *(class)* — Rename sidecar files to match a renamed export file.
  - `HierarchySidecar.build_clean_path_set(paths) -> set` *(static)* — Dedup a set of hierarchy path strings.
  - `HierarchySidecar.expand_to_descendants(objects) -> list` *(static)* — Return hierarchy paths for *objects* plus all their descendants.
  - `HierarchySidecar.get_top_level(paths) -> list` *(static)* — Return only paths whose ancestor is *not* also in the set.
  - `HierarchySidecar.detect_reparenting(missing: list, extra: list) -> list` *(static)* — Detect nodes that were reparented rather than added/removed.
  - `HierarchySidecar.write_manifest(cls, export_path: str, paths, *, base_stem: bool = False) -> Optional[str]` *(class)* — Write *paths* to the sidecar manifest for *export_path*.
  - `HierarchySidecar.read_manifest(cls, export_path: str, *, base_stem: bool = False) -> Optional[Set[str]]` *(class)* — Read the manifest for *export_path*.
  - `HierarchySidecar.count_descendants(top_path: str, all_paths) -> int` *(static)* — Count *top_path* plus its descendants in *all_paths*.
  - `HierarchySidecar.write_diff_report(cls, export_path: str, missing: list, extra: list, reparented: list = None, *, base_stem: bool = False) -> Optional[str]` *(class)* — Write a human-readable diff report to the sidecar text file.
  - `HierarchySidecar.clean_stale_diff(cls, export_path: str, *, base_stem: bool = False) -> None` *(class)* — Remove a stale diff report left over from a previous failure.
  - `HierarchySidecar.build_full_path_set(cls, objects) -> set` *(class)* — Expand *objects* to descendants, then dedup.
  - `HierarchySidecar.compare(cls, export_path: str, current_paths: set, *, base_stem: bool = False) -> Tuple[bool, list, list]` *(class)* — Compare *current_paths* against the stored manifest.

<a id="env_utils--hierarchy_manager--tree_renderer"></a>
### `env_utils/hierarchy_manager/tree_renderer.py`

Tree rendering, formatting, and selection management for the hierarchy manager UI — mirror of

- **[`class HierarchyTreeRenderer(ptk.LoggingMixin)`](blendertk/blendertk/env_utils/hierarchy_manager/tree_renderer.py#L22)** — Owns all QTreeWidget population, diff-colour formatting, ignore styling, selection
  - `HierarchyTreeRenderer.populate_current_scene_tree(self, tree_widget)` — Populate the current scene hierarchy tree (objects not linked from the reference
  - `HierarchyTreeRenderer.populate_reference_tree(self, tree_widget, objects, reference_name='Reference Scene')` — Populate the reference hierarchy tree with pre-fetched objects.
  - `HierarchyTreeRenderer.show_reference_placeholder(self, tree_widget, reference_name='Reference Scene')` — Show a 'Browse for Reference Scene' placeholder in an empty tree.
  - `HierarchyTreeRenderer.show_reference_error(self, tree_widget, reference_name='Reference Scene', message='File Not Found')` — Show an error or status message in the reference tree.
  - `HierarchyTreeRenderer.populate_tree_with_hierarchy(self, tree_widget, objects, tree_type='current')` — Populate tree widget with proper hierarchy nesting.
  - `HierarchyTreeRenderer.apply_difference_formatting(self, tree001, tree000)` — Apply color formatting to tree widgets based on hierarchy differences.
  - `HierarchyTreeRenderer.clear_tree_colors(self, tree_widget)` — Remove foreground/background colors from every item in a tree widget.
  - `HierarchyTreeRenderer.format_tree_differences(self, tree_widget, tree_type, tree_matcher, by_full, by_last)` — Format a specific tree widget based on differences.
  - `HierarchyTreeRenderer.apply_ignore_styling(self, tree_widget)` — Apply or remove strikethrough + dim styling for ignored items.
  - `HierarchyTreeRenderer.build_item_path(item)` *(static)* — Build a pipe-separated hierarchy path from a QTreeWidgetItem.
  - `HierarchyTreeRenderer.find_tree_item_by_name(self, tree_widget, object_name)` — Find a tree item by object name (first column).
  - `HierarchyTreeRenderer.get_selected_tree_items(self, tree_widget)` — Get selected items from a tree widget.
  - `HierarchyTreeRenderer.get_selected_object_names(self, tree_widget)` — Extract object names from selected tree widget items.

<a id="env_utils--hierarchy_manager--tree_utils"></a>
### `env_utils/hierarchy_manager/tree_utils.py`

Tree widget utilities for hierarchy manager UI operations — mirror of mayatk's

- [`get_selected_object_names(tree_widget) -> List[str]`](blendertk/blendertk/env_utils/hierarchy_manager/tree_utils.py#L108) — Extract object names from selected tree widget items.
- [`get_selected_tree_items(tree_widget) -> list`](blendertk/blendertk/env_utils/hierarchy_manager/tree_utils.py#L117) — Get all selected items from tree widget.
- [`find_tree_item_by_name(tree_widget, object_name: str)`](blendertk/blendertk/env_utils/hierarchy_manager/tree_utils.py#L141) — Find tree widget item by object name (or hierarchy path).
- [`build_hierarchy_structure(objects: list) -> Tuple[Dict[str, Dict], List[str]]`](blendertk/blendertk/env_utils/hierarchy_manager/tree_utils.py#L154) — Build hierarchical structure from Blender objects.
- **[`class TreePathMatcher(ptk.LoggingMixin)`](blendertk/blendertk/env_utils/hierarchy_manager/tree_utils.py#L21)** — Tree path matching functionality for UI tree widgets.
  - `TreePathMatcher.build_tree_index(self, widget)` — Build tree indices for fast item lookup: full hierarchy path, and last component.
  - `TreePathMatcher.find_path_matches(self, target_path: str, by_full: dict, by_last: dict, strict: bool = False)` — Find tree items matching a target path — exact full-path match, falling back to
  - `TreePathMatcher.log_matching_debug(self, path, candidates, strategy, prefix='')` — Log debug information about path matching.
  - `TreePathMatcher.log_tree_index_debug(self, by_full, by_last, tree_type)` — Log debug information about tree indices.

<a id="env_utils--maya_bridge--_maya_bridge"></a>
### `env_utils/maya_bridge/_maya_bridge.py`

Maya bridge engine -- export the Blender selection and run a chosen import template in Maya.

- [`list_templates() -> List[Path]`](blendertk/blendertk/env_utils/maya_bridge/_maya_bridge.py#L71) — User-visible templates in ``templates/`` (skips underscore-prefixed).
- [`template_modes(template_path: Path) -> Tuple[str, ...]`](blendertk/blendertk/env_utils/maya_bridge/_maya_bridge.py#L76) — Modes a template declares via ``BRIDGE_MODES``;
- [`list_template_modes() -> List[Tuple[str, str]]`](blendertk/blendertk/env_utils/maya_bridge/_maya_bridge.py#L81) — ``[(stem, mode), ...]`` for every (template, mode) pairing.
- **[`class MayaBridge(BlenderExportMixin, ptk.ScriptLaunchBridge)`](blendertk/blendertk/env_utils/maya_bridge/_maya_bridge.py#L86)** — Export the Blender selection and run a chosen Maya import template.
  - `MayaBridge.maya_path(self) -> Optional[str]` *(property)*
  - `MayaBridge.params_defaults(self) -> Dict[str, Any]`
  - `MayaBridge.render_context(self, params: Dict[str, Any]) -> Dict[str, str]`

<a id="env_utils--maya_bridge--_scene_import"></a>
### `env_utils/maya_bridge/_scene_import.py`

Import a Maya scene (.ma/.mb) into Blender via a headless-Maya FBX round-trip.

- [`mayapy_from_maya_exe(maya_exe: str) -> Optional[str]`](blendertk/blendertk/env_utils/maya_bridge/_scene_import.py#L52) — Return the ``mayapy`` interpreter beside *maya_exe*, or ``None`` if absent.
- [`import_maya_scene(src_path: str, **kwargs: Any) -> List[Any]`](blendertk/blendertk/env_utils/maya_bridge/_scene_import.py#L393) — Import a Maya scene (.ma/.mb) into the current Blender scene.
- **[`class MayaSceneImport(ptk.LoggingMixin)`](blendertk/blendertk/env_utils/maya_bridge/_scene_import.py#L64)** — Engine: convert a Maya scene to FBX via headless Maya, then import it.
  - `MayaSceneImport.maya_path(self) -> Optional[str]` *(property)* — The Maya GUI executable (explicit, or discovered via the bridge's AppSpec).
  - `MayaSceneImport.mayapy_path(self) -> Optional[str]` *(property)* — The headless ``mayapy`` interpreter derived from :attr:`maya_path`.
  - `MayaSceneImport.require_mayapy(self) -> str` — Return :attr:`mayapy_path` or raise an error naming what's missing.
  - `MayaSceneImport.render_script(self, src_path: str, out_fbx: str, *, embed_textures: bool = False, include_animation: bool = True) -> str` — Render the Maya-side conversion script (exposed for tests/preview).
  - `MayaSceneImport.convert(self, src_path: str, out_fbx: str, *, timeout: float = 600, **script_opts: Any) -> 'ptk.ScriptRunResult'` — Convert *src_path* to *out_fbx* in a fresh ``mayapy`` (blocking).
  - `MayaSceneImport.import_scene(self, src_path: str, *, cleanup: bool = True, use_cache: bool = True, timeout: float = 600, fbx_options: Optional[Dict[str, Any]] = None, **script_opts: Any) -> List[Any]` — Import the Maya scene at *src_path*;

<a id="env_utils--maya_bridge--maya_bridge_slots"></a>
### `env_utils/maya_bridge/maya_bridge_slots.py`

Slots for the Maya bridge panel.

- **[`class MayaBridgeSlots(BridgeSlotsBase)`](blendertk/blendertk/env_utils/maya_bridge/maya_bridge_slots.py#L33)** — Slots wired to ``maya_bridge.ui`` via :class:`BridgeSlotsBase`.
  - `MayaBridgeSlots.params_module(self)` *(property)*
  - `MayaBridgeSlots.template_dir(self) -> Path` *(property)*
  - `MayaBridgeSlots.make_bridge(self) -> MayaBridge`
  - `MayaBridgeSlots.list_template_modes(self)`
  - `MayaBridgeSlots.b000(self)` — Send the selected objects to Maya with the chosen template.

<a id="env_utils--maya_bridge--parameters"></a>
### `env_utils/maya_bridge/parameters.py`

Registry of user-tunable Maya-bridge parameters exposed to the panel.

- [`referenced_keys(script_text: str) -> 'set[str]'`](blendertk/blendertk/env_utils/maya_bridge/parameters.py#L106) — Registered keys present in *script_text* (delegates to uitk.bridge).
- [`defaults() -> 'dict[str, Any]'`](blendertk/blendertk/env_utils/maya_bridge/parameters.py#L111) — Return ``{key: default}`` for every registered parameter.
- [`render_context(values: 'dict[str, Any]') -> 'dict[str, str]'`](blendertk/blendertk/env_utils/maya_bridge/parameters.py#L116) — Format *values* for ``StrUtils.replace_delimited`` using Python literals.

<a id="env_utils--maya_bridge--templates--_import_scene"></a>
### `env_utils/maya_bridge/templates/_import_scene.py`

Open a Maya scene headlessly (mayapy) and export it as FBX for a Blender import.

- [`fbx_safe_materials(cmds)`](blendertk/blendertk/env_utils/maya_bridge/templates/_import_scene.py#L248) — Swap every FBX-hostile shader for an equivalent phong on its shading group.
- [`write_texture_manifest(entries, path)`](blendertk/blendertk/env_utils/maya_bridge/templates/_import_scene.py#L310) — Sidecar for the textures FBX cannot carry, consumed by MayaSceneImport.
- [`main()`](blendertk/blendertk/env_utils/maya_bridge/templates/_import_scene.py#L323)

<a id="env_utils--maya_bridge--templates--import"></a>
### `env_utils/maya_bridge/templates/import.py`

Import the bridged FBX into Maya, with optional clean-slate and frame-on-import behaviors.

- [`main()`](blendertk/blendertk/env_utils/maya_bridge/templates/import.py#L26)

<a id="env_utils--reference_manager"></a>
### `env_utils/reference_manager.py`

Reference Manager tool panel — Switchboard slot wiring for the co-located ``reference_manager.ui``.

- **[`class ReferenceManagerSlots(ptk.LoggingMixin)`](blendertk/blendertk/env_utils/reference_manager.py#L54)** — Switchboard slot wiring for the Reference Manager panel.
  - `ReferenceManagerSlots.header_init(self, widget)` — Header refresh button, Recursive toggle, Naming presets, bulk Operations, help text.
  - `ReferenceManagerSlots.txt000_init(self, widget)` — Root Directory — browse + recent-dir history + Open / Set-To-Current actions (mirror of Maya).
  - `ReferenceManagerSlots.cmb000_init(self, widget)` — Workspace combo — project folders under the root (replaces Maya's workspace combo).
  - `ReferenceManagerSlots.txt001_init(self, widget)` — Filter field — enable toggle + ignore-case + target combo, plus live re-filter (mirror of Maya).
  - `ReferenceManagerSlots.tbl000_init(self, widget)` — One-time table setup (clickable action columns + context menu + signals), then populate.
  - `ReferenceManagerSlots.open_selected(self)` — Open the selected .blend (replaces the current file;
  - `ReferenceManagerSlots.save_scene(self)` — Save the current scene into the workspace with the header naming conventions.
  - `ReferenceManagerSlots.rename_selected(self)` — Rename the selected .blend on disk.
  - `ReferenceManagerSlots.delete_selected(self)` — Delete the selected .blend file(s) from disk (confirmed).
  - `ReferenceManagerSlots.open_location_selected(self)` — Reveal the selected .blend in the OS file manager (any row).
  - `ReferenceManagerSlots.reference_selected(self, link=True)` — Link (or Append) the selected .blend file(s) as references.
  - `ReferenceManagerSlots.reload_selected(self)` — Reload the selected file's library from disk.
  - `ReferenceManagerSlots.relocate_selected(self)` — Point the selected file's library at a different .blend (native file browser).
  - `ReferenceManagerSlots.make_local_selected(self)` — Make the selected file's library data local (Maya's per-reference Import).
  - `ReferenceManagerSlots.remove_selected(self)` — Remove the selected file's library (and everything linked from it).
  - `ReferenceManagerSlots.set_display(self, mode)` — Set the per-reference display mode (off / reference / template) on the selection.
  - `ReferenceManagerSlots.reload_all(self)` — Reload every linked library from disk (Maya's Update References).
  - `ReferenceManagerSlots.make_local_all(self)` — Make every linked library's data local (Maya's Unlink-and-Import All).
  - `ReferenceManagerSlots.remove_all(self)` — Remove every linked library and its data (Maya's Un-Reference All).

<a id="env_utils--scene_exporter--_scene_exporter"></a>
### `env_utils/scene_exporter/_scene_exporter.py`

Scene Exporter engine -- Blender port of mayatk's ``env_utils.scene_exporter``.

- **[`class SceneExporter(ptk.LoggingMixin)`](blendertk/blendertk/env_utils/scene_exporter/_scene_exporter.py#L87)**
  - `SceneExporter.perform_export(self, export_dir: str, objects: Optional[Union[List, Callable]] = None, preset_name: Optional[str] = None, output_name: Optional[str] = None, export_visible: bool = True, create_log_file: bool = False, timestamp: bool = False, name_regex: Optional[str] = None, log_level: str = 'WARNING', hide_log_file: Optional[bool] = None, log_handler: Optional[object] = None, tasks: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, bool]]` — Perform the export operation, including initialization and task management.
  - `SceneExporter.generate_export_path(self, version_format: str = '') -> str` — Generate the full export file path.
  - `SceneExporter.format_export_name(self, name: str) -> str` — Format the export name using a regex pattern and replacement (e.g.
  - `SceneExporter.generate_log_file_path(self, export_path: str) -> str` — Generate the log file path based on the export path.
  - `SceneExporter.setup_file_logging(self, log_file_path: str)` — Setup file logging to log actions during export.
  - `SceneExporter.close_file_handlers(self)` — Close and remove file handlers after logging is complete.
  - `SceneExporter.list_fbx_presets(cls) -> List[str]` *(class)* — All FBX export-option preset names (built-in + user;
  - `SceneExporter.fbx_preset_dir(cls) -> str` *(class)* — Writable directory FBX export-option presets are saved to (the "Open Preset
  - `SceneExporter.fbx_preset_path(cls, name: str) -> Optional[str]` *(class)* — Filesystem path *name* resolves to (built-in or user tier), or ``None`` if it
  - `SceneExporter.save_fbx_preset(cls, name: str, options: Optional[Dict[str, Any]] = None) -> str` *(class)* — Save *options* (default: :data:`_DEFAULT_FBX_OPTIONS`) as user preset *name*.
  - `SceneExporter.delete_fbx_preset(cls, name: str) -> bool` *(class)* — Delete the *user* FBX export-option preset *name* (built-ins are read-only).
  - `SceneExporter.load_fbx_export_preset(self, name: Optional[str] = None, verify: bool = False) -> Optional[dict]` — Load a named FBX export-option preset so the next :meth:`perform_export` call
  - `SceneExporter.verify_fbx_preset(self) -> dict` — Return (and log) the FBX export kwargs the next :meth:`perform_export` call will

<a id="env_utils--scene_exporter--scene_exporter_slots"></a>
### `env_utils/scene_exporter/scene_exporter_slots.py`

Slots for the Scene Exporter panel -- Blender port of mayatk's ``SceneExporterSlots``.

- **[`class SceneExporterSlots(SceneExporter)`](blendertk/blendertk/env_utils/scene_exporter/scene_exporter_slots.py#L36)**
  - `SceneExporterSlots.workspace(self)` *(property)*
  - `SceneExporterSlots.header_init(self, widget)` — Initialize the header widget.
  - `SceneExporterSlots.presets(self) -> Dict[str, Optional[str]]` *(property)* — FBX export-option presets available for ``cmb000``, keyed by name (``"None"``
  - `SceneExporterSlots.cmb000_init(self, widget) -> None` — Init FBX export-option preset combo (mirror of mayatk's ``cmb000_init`` -- see
  - `SceneExporterSlots.txt000_init(self, widget) -> None` — Init Output Directory
  - `SceneExporterSlots.txt001_init(self, widget) -> None` — Init Output Name
  - `SceneExporterSlots.cmb001_init(self, widget) -> None` — Auto-generate Export Settings UI from task definitions using WidgetComboBox.
  - `SceneExporterSlots.cmb002_init(self, widget) -> None` — Auto-generate Check Settings UI from check definitions using WidgetComboBox.
  - `SceneExporterSlots.cmb004_init(self, widget) -> None` — Init Output Format — FBX (default), GLB, or FBX + GLB.
  - `SceneExporterSlots.b000(self) -> None` — Export: run the scene export with the configured tasks and settings.
  - `SceneExporterSlots.b010(self) -> None` — Set Output Directory
  - `SceneExporterSlots.b006(self) -> None` — Open Output Directory
  - `SceneExporterSlots.b003(self) -> None` — Add Preset -- save a new named FBX export-option preset, seeded from the currently
  - `SceneExporterSlots.b004(self) -> None` — Delete Preset -- remove the currently-selected FBX export-option preset from disk
  - `SceneExporterSlots.b007(self) -> None` — Open Preset Directory.
  - `SceneExporterSlots.b008(self) -> None` — Edit Preset -- open the selected preset's JSON file in the OS's default editor so
  - `SceneExporterSlots.save_output_dir(self, output_dir: str) -> None` — Record the output directory into the recent values plugin.
  - `SceneExporterSlots.save_output_name(self, output_name: str) -> None` — Record the output filename into the recent values plugin.

<a id="env_utils--scene_exporter--task_manager"></a>
### `env_utils/scene_exporter/task_manager.py`

Blender-specific task/check methods for the Scene Exporter pipeline -- mirror of mayatk's

- **[`class TaskManager(TaskFactory, _TaskActionsMixin, _TaskChecksMixin)`](blendertk/blendertk/env_utils/scene_exporter/task_manager.py#L645)** — Contains all task/check UI definitions for the Scene Exporter -- mirror of mayatk's
  - `TaskManager.objects(self)` *(property)*
  - `TaskManager.task_definitions(self) -> Dict[str, Dict[str, Any]]` *(property)* — Return the task definitions for the UI.
  - `TaskManager.check_definitions(self) -> Dict[str, Dict[str, Any]]` *(property)* — Return the check definitions for the UI.
  - `TaskManager.definitions(self) -> Dict[str, Dict[str, Any]]` *(property)* — Return all definitions combined for backward compatibility.

<a id="env_utils--script_output"></a>
### `env_utils/script_output.py`

Blender script-output console — the blendertk analogue of mayatk's ``ScriptConsole``.

- [`show(*args, **kwargs) -> ScriptConsole`](blendertk/blendertk/env_utils/script_output.py#L516) — Dock + show the Script Output console (reuses the persistent instance/widget if one
- [`hide(*args, **kwargs) -> None`](blendertk/blendertk/env_utils/script_output.py#L522) — Undock + hide the Script Output console (capture keeps running in the background).
- [`toggle(*args, **kwargs)`](blendertk/blendertk/env_utils/script_output.py#L527) — Toggle the Script Output console shown/hidden.
- [`begin_capture() -> ScriptConsole`](blendertk/blendertk/env_utils/script_output.py#L536) — Start the stdout/stderr/logging capture now (idempotent; UI-free).
- [`restore() -> ScriptConsole`](blendertk/blendertk/env_utils/script_output.py#L542) — Start capture and re-open the console if it was open when the previous session
- **[`class ScriptConsole`](blendertk/blendertk/env_utils/script_output.py#L222)** — Singleton orchestrator: capture + native-docked ``uitk.ScriptOutput``, with the
  - `ScriptConsole.instance(cls) -> 'ScriptConsole'` *(class)*
  - `ScriptConsole.widget(self)` *(property)* — The live ``uitk.ScriptOutput`` (or None) — for tests/diagnostics.
  - `ScriptConsole.begin_capture(self) -> 'ScriptConsole'` — Start recording stdout/stderr/logging into the transcript buffer NOW (idempotent).
  - `ScriptConsole.restore(self) -> 'ScriptConsole'` — Reinstate the previous session's console — the Blender analogue of Maya's
  - `ScriptConsole.show(self) -> 'ScriptConsole'` — Dock the console into the main window and persist visible=True.
  - `ScriptConsole.hide(self) -> None` — Undock (persisting the user's strip height) and persist visible=False.
  - `ScriptConsole.is_open(self) -> bool`
  - `ScriptConsole.teardown(self) -> None` — Full un-install for a HOST RELOAD (``tb.reload()``): undock the area, drop the

<a id="env_utils--unity_bridge--_unity_bridge"></a>
### `env_utils/unity_bridge/_unity_bridge.py`

Unity bridge engine -- export the Blender selection into a Unity project's Assets/.

- [`list_delivery_modes() -> List[Tuple[str, str]]`](blendertk/blendertk/env_utils/unity_bridge/_unity_bridge.py#L42) — ``[(mode_stem, ""), ...]`` for the panel's delivery combo.
- **[`class UnityBridge(BlenderExportMixin, ptk.HandoffBridge)`](blendertk/blendertk/env_utils/unity_bridge/_unity_bridge.py#L47)** — Export the Blender selection and copy it into a Unity project's ``Assets/``.
  - `UnityBridge.list_template_modes(self)`
  - `UnityBridge.params_defaults(self)`

<a id="env_utils--unity_bridge--parameters"></a>
### `env_utils/unity_bridge/parameters.py`

User-tunable parameters for the Blender->Unity bridge panel -- mirror of mayatk's

- [`referenced_keys(script_text: str) -> 'set[str]'`](blendertk/blendertk/env_utils/unity_bridge/parameters.py#L140) — Registered keys present in *script_text* (delegates to uitk.bridge).
- [`defaults() -> 'dict[str, Any]'`](blendertk/blendertk/env_utils/unity_bridge/parameters.py#L145) — Return ``{key: default}`` for every registered parameter.
- [`render_context(values: 'dict[str, Any]') -> 'dict[str, str]'`](blendertk/blendertk/env_utils/unity_bridge/parameters.py#L150) — Format *values* for substitution (kept for API parity;

<a id="env_utils--unity_bridge--unity_bridge_slots"></a>
### `env_utils/unity_bridge/unity_bridge_slots.py`

Slots for the Unity bridge panel -- mirror of mayatk's

- **[`class UnityBridgeSlots(BlenderBridgeSlotsBase)`](blendertk/blendertk/env_utils/unity_bridge/unity_bridge_slots.py#L36)** — Slots wired to ``unity_bridge.ui`` via :class:`BlenderBridgeSlotsBase`.
  - `UnityBridgeSlots.params_module(self)` *(property)*
  - `UnityBridgeSlots.template_dir(self) -> Path` *(property)*
  - `UnityBridgeSlots.make_bridge(self) -> UnityBridge`
  - `UnityBridgeSlots.list_template_modes(self)`
  - `UnityBridgeSlots.default_output_dir(self) -> str`
  - `UnityBridgeSlots.b000(self)` — Export per the chosen Scope and copy the FBX into the Unity project.

<a id="light_utils--_light_utils"></a>
### `light_utils/_light_utils.py`

Light utilities — the world-environment (HDRI) helpers behind the HDR Manager panel

- [`set_world_hdri(filepath=None, strength=None, rotation=0.0, visible=True, intensity=None, exposure=None)`](blendertk/blendertk/light_utils/_light_utils.py#L52) — Set (or update) the world environment from an HDR image.
- [`get_world_hdri()`](blendertk/blendertk/light_utils/_light_utils.py#L121) — The current world-HDRI state as a dict (``filepath``/``strength``/``intensity``/
- [`set_world_ray_visibility(diffuse=None, glossy=None)`](blendertk/blendertk/light_utils/_light_utils.py#L155) — Toggle whether the world environment contributes to **diffuse** / **glossy** lighting — the
- [`get_world_ray_visibility()`](blendertk/blendertk/light_utils/_light_utils.py#L176) — The world's diffuse/glossy ray-visibility as ``{diffuse, glossy}``, or ``None`` (no world /
- [`set_world_importance_resolution(resolution)`](blendertk/blendertk/light_utils/_light_utils.py#L188) — Set the world environment's importance-sampling **map resolution** — the Cycles analogue of
- [`get_world_importance_resolution()`](blendertk/blendertk/light_utils/_light_utils.py#L212) — The world's importance-sampling map resolution when in **manual** mode, else ``None``
- [`clear_world_hdri()`](blendertk/blendertk/light_utils/_light_utils.py#L225) — Remove the btk-managed HDRI environment (env / mapping / coord nodes) from the world.
- **[`class LightUtils`](blendertk/blendertk/light_utils/_light_utils.py#L245)** — Namespace mirror of mayatk's ``light_utils`` (helpers also exposed module-level).

<a id="light_utils--hdr_manager"></a>
### `light_utils/hdr_manager.py`

Blender world-HDRI environment manager.

- **[`class HdrManagerSlots(ptk.LoggingMixin)`](blendertk/blendertk/light_utils/hdr_manager.py#L61)** — Switchboard slots for the HDR Manager UI.
  - `HdrManagerSlots.header_init(self, widget) -> None` — Configure header menu and refresh button.
  - `HdrManagerSlots.cmb000_init(self, widget) -> None` — Wire the HDR dropdown: option-box plugins, context menu, auto-refresh.
  - `HdrManagerSlots.set_hdr_folder(self) -> None` — Header-menu action — choose the folder scanned for HDR maps.
  - `HdrManagerSlots.hdr_map(self) -> Optional[str]` *(property)* — Selected HDR file path from the combobox.
  - `HdrManagerSlots.hdr_map_visibility(self) -> bool` *(property)* — Render 'Visible' flag — read from the rotation slider's render toggle.
  - `HdrManagerSlots.cmb000(self, index, widget) -> None` — HDR map selection — the panel's sole apply action.
  - `HdrManagerSlots.slider000(self, value, widget) -> None` — Rotate the HDR around Z.
  - `HdrManagerSlots.spn_intensity(self, value, widget) -> None` — Set the world's HDR intensity (brightness multiplier).
  - `HdrManagerSlots.spn_exposure(self, value, widget) -> None` — Set the world's HDR exposure (in stops).
  - `HdrManagerSlots.spn_resolution(self, value, widget) -> None` — Importance-sampling map resolution — switches the world to manual Cycles sampling and
  - `HdrManagerSlots.spn_diffuse(self, value, widget) -> None` — Diffuse contribution — any value >0 enables it, 0 disables it (Cycles ray
  - `HdrManagerSlots.spn_specular(self, value, widget) -> None` — Specular/glossy contribution — any value >0 enables it, 0 disables it (Cycles ray
  - `HdrManagerSlots.add_hdr(self) -> None` — Add HDR(s) from one dialog — pick loose files and/or a whole folder.
  - `HdrManagerSlots.open_sourceimages(self) -> None` — Open the HDR folder in the OS file manager.
  - `HdrManagerSlots.clear_network(self) -> None` — Delete the world HDRI network and reset the UI to defaults.
  - `HdrManagerSlots.ctx_reveal_in_explorer(self) -> None` — Reveal the environment's HDR texture file in the OS file manager.

<a id="light_utils--lightmap_baker--lightmap_baker"></a>
### `light_utils/lightmap_baker/lightmap_baker.py`

High-level lightmap baking workflow for Blender -> game engines (Unity-first).

- **[`class LightmapBaker(ptk.LoggingMixin)`](blendertk/blendertk/light_utils/lightmap_baker/lightmap_baker.py#L62)** — Orchestrate the Blender lightmap workflow: UV2 -> Cycles bake -> engine export prep.
  - `LightmapBaker.resolution(self) -> int` *(property)*
  - `LightmapBaker.samples(self) -> int` *(property)*
  - `LightmapBaker.preset_store() -> 'ptk.PresetStore'` *(static)* — Shared store of lightmap quality presets (built-in + user tiers).
  - `LightmapBaker.from_preset(cls, name: str, **overrides) -> 'LightmapBaker'` *(class)* — Construct a baker from a named quality preset (``resolution`` / ``samples``).
  - `LightmapBaker.bake_fused(self, objects=None, **kwargs) -> Dict[str, str]` — Bake a **fused** (albedo x lighting) HDR lightmap per object.
  - `LightmapBaker.bake_separated(self, objects=None, prefix: str = 'lightmap_irr_', **kwargs) -> Dict[str, str]` — Bake a **lighting-only** irradiance lightmap per object (the default path).
  - `LightmapBaker.commit_lightmap(self, mapping: Dict[str, str], intensity: float = 1.0, scale_offsets: Optional[Dict[str, List[float]]] = None, uv_rects: Optional[Dict[str, List[float]]] = None) -> Dict[str, str]` — Record a lighting-only bake for the engine (changes nothing about the material/UVs).
  - `LightmapBaker.pack_atlas(self, mapping: Dict[str, str], output_dir: Optional[str] = None, prefix: str = '', suffix: str = '_Lightmap') -> Dict[str, Tuple[str, List[float]]]` — Consolidate ``{object_name: per_object_exr}`` into one atlas EXR per primary material.
  - `LightmapBaker.revert_lightmap(self, objects=None) -> List[str]` — Undo :meth:`commit_lightmap` -- restore any atlas UV remap, drop the markers, republish.
  - `LightmapBaker.commit_unlit(self, mapping: Dict[str, str]) -> Dict[str, str]` — Make the fused bake each object's live appearance (non-destructive).
  - `LightmapBaker.revert_unlit(self, objects=None) -> List[str]` — Undo :meth:`commit_unlit` -- restore the source material slots + drop the marker.
  - `LightmapBaker.revert(self, objects=None) -> List[str]` — Undo any lightmap wiring -- fused commit and/or lighting-only marker.
- **[`class LightmapBakerSlots(ptk.LoggingMixin)`](blendertk/blendertk/light_utils/lightmap_baker/lightmap_baker.py#L833)** — Switchboard slots for the co-located ``lightmap_baker.ui`` panel.
  - `LightmapBakerSlots.header_init(self, widget) -> None` — Configure the header chrome (menu / collapse / hide), menu, help text.
  - `LightmapBakerSlots.cmb000_init(self, widget) -> None` — Populate the Quality combobox from the shared preset store.
  - `LightmapBakerSlots.cmb000(self, index, widget) -> None` — Apply the selected preset's dials to Resolution / Samples.
  - `LightmapBakerSlots.cmb001_init(self, widget) -> None` — Populate the bake-level (Mode) combobox;
  - `LightmapBakerSlots.cmb002_init(self, widget) -> None` — Populate the Packing combobox;
  - `LightmapBakerSlots.cmb_scope_init(self, widget) -> None` — Populate the Scope combobox;
  - `LightmapBakerSlots.cmb_resolution_init(self, widget) -> None` — Populate the Resolution combobox (value carried as item data);
  - `LightmapBakerSlots.txt000_init(self, widget) -> None` — Add the Prefix / Suffix / Auto picker to the name-affix field.
  - `LightmapBakerSlots.b000(self) -> None` — Bake lightmaps for the selection in the chosen Mode (revert → bake → commit).
  - `LightmapBakerSlots.revert_to_source(self) -> None` — Undo the bake wiring on the selected objects (or all baked ones).
  - `LightmapBakerSlots.open_output(self) -> None` — Open the most recent output folder in the file browser.

<a id="mat_utils--_mat_utils"></a>
### `mat_utils/_mat_utils.py`

Material utilities — mirror of mayatk's ``MatUtils`` public names where the concepts align:

- [`get_mats(objects)`](blendertk/blendertk/mat_utils/_mat_utils.py#L18) — Unique materials assigned to the given object(s), in slot order.
- [`create_mat(mat_type='standard', name='')`](blendertk/blendertk/mat_utils/_mat_utils.py#L28) — Create a new material (mirror of ``mtk.MatUtils.create_mat``).
- [`assign_mat(objects, material)`](blendertk/blendertk/mat_utils/_mat_utils.py#L50) — Assign ``material`` to the given object(s) — whole-object assignment (all slots).
- [`find_by_mat_id(material, objects=None)`](blendertk/blendertk/mat_utils/_mat_utils.py#L64) — Objects using ``material`` (mirror of ``mtk.find_by_mat_id`` at the object level).
- [`select_by_material(material, add=False)`](blendertk/blendertk/mat_utils/_mat_utils.py#L79) — Select every scene object using ``material`` (optionally adding to the selection).
- [`reload_textures()`](blendertk/blendertk/mat_utils/_mat_utils.py#L94) — Reload every image datablock from disk (mirror of ``mtk.MatUtils.reload_textures``).
- [`get_scene_mats(inc=None, exc=None, sort=False, as_dict=False, exclude_defaults=True, **filter_kwargs)`](blendertk/blendertk/mat_utils/_mat_utils.py#L112) — Scene materials with flexible name filtering — mirror of ``mtk.MatUtils.get_scene_mats``.
- [`is_mat_assigned(mat)`](blendertk/blendertk/mat_utils/_mat_utils.py#L132) — True iff ``mat`` is assigned to at least one object (mirror of ``mtk.is_mat_assigned`` —
- [`get_mat_swatch_icon(mat, size=(20, 20), fallback_to_blank=True)`](blendertk/blendertk/mat_utils/_mat_utils.py#L139) — A ``QIcon`` filled with ``mat``'s viewport display color — mirror of
- [`get_texture_paths(objects=None, materials=None, absolute=True)`](blendertk/blendertk/mat_utils/_mat_utils.py#L214) — Unique texture file paths in scope — mirror of ``mtk.MatUtils.get_texture_paths``.
- [`get_texture_info(objects=None, materials=None)`](blendertk/blendertk/mat_utils/_mat_utils.py#L230) — Image metadata for the textures in scope — mirror of ``mtk.MatUtils.get_texture_info``.
- [`get_mat_info(materials=None, objects=None, optimize_check=False, progress_callback=None, exclude_defaults=False, exclude_unassigned=False, include_textures=True, include_image_metadata=True, **optimize_kwargs)`](blendertk/blendertk/mat_utils/_mat_utils.py#L263) — Aggregate per-material info (name, type, textures + image metadata) — mirror of
- [`format_mat_info_html(records)`](blendertk/blendertk/mat_utils/_mat_utils.py#L312) — Render :func:`get_mat_info` output as styled HTML (delegates to ``pythontk.MatReport``).
- [`format_texture_info_html(info_list)`](blendertk/blendertk/mat_utils/_mat_utils.py#L317) — Render :func:`get_texture_info` output as styled HTML (delegates to ``pythontk.MatReport``).
- [`find_materials_with_duplicate_textures(materials=None)`](blendertk/blendertk/mat_utils/_mat_utils.py#L323) — Groups of materials that reference the *same* set of texture files — mirror of
- [`reassign_duplicate_materials(duplicate_groups, delete=True)`](blendertk/blendertk/mat_utils/_mat_utils.py#L337) — Reassign every object using a duplicate to the group's first (canonical) material, then
- [`delete_unused_materials()`](blendertk/blendertk/mat_utils/_mat_utils.py#L362) — Delete materials assigned to no object — mirror of Maya's *Delete Unused Materials*.
- [`graph_materials(materials, mode=None)`](blendertk/blendertk/mat_utils/_mat_utils.py#L381) — Open the Shader Editor focused on ``materials`` — the Blender analogue of Maya's
- [`get_image_records()`](blendertk/blendertk/mat_utils/_mat_utils.py#L410) — Every FILE-backed image datablock as a record for the Texture Path Editor:
- [`repath_image(image, new_path, reload=True)`](blendertk/blendertk/mat_utils/_mat_utils.py#L434) — Point ``image`` (datablock or name) at ``new_path`` and reload it — mirror of the Texture
- [`to_project_relative(abspath, blenddir=None)`](blendertk/blendertk/mat_utils/_mat_utils.py#L451) — Convert an absolute path to a Blender ``//``-relative path when it falls under the saved
- [`resolve_missing_textures(search_dir, recursive=True, stem=False, texture=False, fuzzy=False, images=None)`](blendertk/blendertk/mat_utils/_mat_utils.py#L519) — Repath missing FILE images within ``search_dir`` — the Blender analogue of Maya's Texture
- [`normalize_texture_paths(mode='relative', project_dir=None, images=None)`](blendertk/blendertk/mat_utils/_mat_utils.py#L593) — Normalize FILE image paths — mirror of the Texture Path Editor's 'Normalize Paths'.
- [`get_image_material_map()`](blendertk/blendertk/mat_utils/_mat_utils.py#L655) — ``{image-name: [material names]}`` for every FILE image referenced by a material's shader
- [`materials_for_textures(paths)`](blendertk/blendertk/mat_utils/_mat_utils.py#L672) — Scene materials whose shader graph references an image at one of ``paths`` (matched by
- [`fix_color_spaces(images=None, force_update=False, dry_run=False)`](blendertk/blendertk/mat_utils/_mat_utils.py#L712) — Assign each texture image its correct color space by map type — the Blender counterpart of
- [`set_texture_directory(images=None, target_dir=None, mode='rewrite')`](blendertk/blendertk/mat_utils/_mat_utils.py#L805) — Repath each image so its file lives directly under ``target_dir`` — mirror of the Texture
- [`find_and_copy_textures(images=None, search_dir=None, dest_dir=None, mode='copy')`](blendertk/blendertk/mat_utils/_mat_utils.py#L835) — Search ``search_dir`` recursively for the textures used by ``images`` (matched by basename),
- [`format_texture_paths_html(records=None)`](blendertk/blendertk/mat_utils/_mat_utils.py#L872) — Render :func:`get_image_records` as an HTML table for the panel/report (missing flagged).
- [`get_shader_templates()`](blendertk/blendertk/mat_utils/_mat_utils.py#L919) — The available Principled-BSDF template names (mirror of Maya's Shader Templates list).
- [`apply_shader_template(material, template)`](blendertk/blendertk/mat_utils/_mat_utils.py#L949) — Apply a Principled-BSDF template preset to ``material``'s shader.
- [`create_shader_template(template, name=None)`](blendertk/blendertk/mat_utils/_mat_utils.py#L964) — Create a new node-based material configured from a Principled-BSDF ``template`` — mirror of
- [`serialize_material(material)`](blendertk/blendertk/mat_utils/_mat_utils.py#L993) — Capture a material's shader node graph as a portable, JSON-safe dict — the Blender analogue of
- [`restore_material(data, name=None, textures=None)`](blendertk/blendertk/mat_utils/_mat_utils.py#L1043) — Rebuild a material from a :func:`serialize_material` dict — the Blender analogue of mayatk's
- [`create_pbr_material(textures, name=None, normal_direction='OpenGL')`](blendertk/blendertk/mat_utils/_mat_utils.py#L1144) — Build a Principled-BSDF material from a set of PBR texture files — Blender mirror of mayatk's
- [`create_pbr_materials(textures, name=None, normal_direction='OpenGL', prefix='', suffix='')`](blendertk/blendertk/mat_utils/_mat_utils.py#L1375) — Batch builder — Blender mirror of mayatk's ``GameShader.create_network`` batch path.
- [`update_materials(materials=None, config=None, verbose=False, progress_callback=None)`](blendertk/blendertk/mat_utils/_mat_utils.py#L1603) — Module-level alias for :meth:`MatUpdater.update_materials` (``btk.update_materials``).
- **[`class MatUpdater(ptk.LoggingMixin)`](blendertk/blendertk/mat_utils/_mat_utils.py#L1416)** — Batch texture reprocessor for scene materials — Blender mirror of mayatk's ``MatUpdater``.
  - `MatUpdater.update_materials(cls, materials=None, config=None, verbose=False, progress_callback=None)` *(class)* — Reprocess the textures of ``materials`` and repath their image nodes to the results.
- **[`class MatUtils`](blendertk/blendertk/mat_utils/_mat_utils.py#L1610)** — Namespace mirror of mayatk's ``MatUtils`` (helpers also exposed module-level).

<a id="mat_utils--arnold_bridge"></a>
### `mat_utils/arnold_bridge.py`

Arnold render-bridge management -- Blender port of mayatk's ``mat_utils.arnold_bridge``.

- **[`class ArnoldBridge(ptk.LoggingMixin)`](blendertk/blendertk/mat_utils/arnold_bridge.py#L38)** — Structural mirror of mayatk's ``ArnoldBridge`` -- no-op on Blender (see module docstring).
  - `ArnoldBridge.add(self, materials: Optional[Union[str, List[str]]] = None, objects: Optional[Union[str, List[str]]] = None, force: bool = False) -> List[str]`
  - `ArnoldBridge.remove(self, materials: Optional[Union[str, List[str]]] = None, objects: Optional[Union[str, List[str]]] = None) -> List[str]`
  - `ArnoldBridge.rebuild(self, materials: Optional[Union[str, List[str]]] = None, objects: Optional[Union[str, List[str]]] = None) -> List[str]`
  - `ArnoldBridge.get_bridge(self, material) -> Optional[str]`
  - `ArnoldBridge.has_bridge(self, material) -> bool`
- **[`class ArnoldBridgeSlots(ptk.LoggingMixin, ptk.HelpMixin)`](blendertk/blendertk/mat_utils/arnold_bridge.py#L77)** — Switchboard slots for the ``arnold_bridge.ui`` panel -- every control disabled.
  - `ArnoldBridgeSlots.header_init(self, widget) -> None` — Configure the help text (no menu actions -- the panel is inert).
  - `ArnoldBridgeSlots.cmb000_init(self, widget) -> None` — Populate with mayatk's scope labels for visual parity (the combo itself stays

<a id="mat_utils--game_shader"></a>
### `mat_utils/game_shader.py`

Game Shader tool panel — auto-build a Principled-BSDF material from a set of PBR textures.

- **[`class GameShaderSlots(ptk.LoggingMixin)`](blendertk/blendertk/mat_utils/game_shader.py#L35)** — Switchboard slot wiring for the Game Shader panel.
  - `GameShaderSlots.workspace_dir(self) -> str` *(property)*
  - `GameShaderSlots.source_images_dir(self) -> str` *(property)*
  - `GameShaderSlots.header_init(self, widget)` — Initialize the header widget.
  - `GameShaderSlots.lbl_graph_material(self)` — Graph the most recently created material in the Shader Editor.
  - `GameShaderSlots.mat_name(self) -> str` *(property)* — Get the material name from the user input text field.
  - `GameShaderSlots.mat_prefix(self) -> str` *(property)* — Return the affix text when it resolves as a prefix, else empty string.
  - `GameShaderSlots.mat_suffix(self) -> str` *(property)* — Return the affix text when it resolves as a suffix, else empty string.
  - `GameShaderSlots.normal_map_type(self) -> str` *(property)* — Get the normal map direction from the comboBox's current text.
  - `GameShaderSlots.txt002_init(self, widget)` — Add a prefix/suffix/auto-mode picker to the affix field.
  - `GameShaderSlots.b000(self)` — Create Network — pick PBR texture files and build Principled material(s) from them.

<a id="mat_utils--image_to_plane--_image_to_plane"></a>
### `mat_utils/image_to_plane/_image_to_plane.py`

Map image files to textured planes in Blender — port of mayatk's ``mat_utils.image_to_plane``.

- **[`class ImageToPlane(ptk.LoggingMixin)`](blendertk/blendertk/mat_utils/image_to_plane/_image_to_plane.py#L20)** — Create textured planes from image files (mirror of mayatk's ``ImageToPlane``).
  - `ImageToPlane.create(cls, image_paths, mat_type='standard', suffix='_MAT', prefix='', plane_height=10.0, group=False, group_name='imagePlanes_GRP')` *(class)* — Create textured planes for one or more images.
  - `ImageToPlane.remove(cls, objects=None)` *(class)* — Remove planes and their auto-created materials/images (orphans only) — mirror of

<a id="mat_utils--image_to_plane--image_to_plane_slots"></a>
### `mat_utils/image_to_plane/image_to_plane_slots.py`

Switchboard slots for the Image to Plane UI — port of mayatk's ``ImageToPlaneSlots``.

- **[`class ImageToPlaneSlots(ptk.LoggingMixin)`](blendertk/blendertk/mat_utils/image_to_plane/image_to_plane_slots.py#L32)** — Switchboard slots for the Image to Plane panel.
  - `ImageToPlaneSlots.header_init(self, widget)` — Configure header menu.
  - `ImageToPlaneSlots.txt_suffix_init(self, widget)` — Add a prefix/suffix/auto-mode picker to the affix field.

<a id="mat_utils--marmoset_bridge--_marmoset_bridge"></a>
### `mat_utils/marmoset_bridge/_marmoset_bridge.py`

Blender-side glue for the Marmoset Toolbag engine -- mirror of mayatk's

- [`build_bake_pairs_manifest(objects: Sequence, high_suffix: str, low_suffix: str) -> Dict[str, str]`](blendertk/blendertk/mat_utils/marmoset_bridge/_marmoset_bridge.py#L82) — Build the ``{mesh_name: 'high'|'low'}`` sidecar for the bake -- mirror of mayatk's
- **[`class MarmosetBridge(ptk.HandoffBridge)`](blendertk/blendertk/mat_utils/marmoset_bridge/_marmoset_bridge.py#L117)** — Export the Blender selection to Marmoset Toolbag with templated automation.
  - `MarmosetBridge.toolbag_path(self) -> Optional[str]` *(property)*
  - `MarmosetBridge.params_defaults(self) -> Dict[str, Any]`
  - `MarmosetBridge.render_template(self, *args, **kwargs) -> Optional[str]` — Render a Toolbag script body (delegates to the engine deliverer).

<a id="mat_utils--marmoset_bridge--_marmoset_engine"></a>
### `mat_utils/marmoset_bridge/_marmoset_engine.py`

Drive Marmoset Toolbag from the outside -- launch + templated automation.

- [`list_templates() -> List[Path]`](blendertk/blendertk/mat_utils/marmoset_bridge/_marmoset_engine.py#L59) — Return user-visible templates in ``templates/`` (skips underscore-prefixed).
- [`template_modes(template_path: Path) -> Tuple[str, ...]`](blendertk/blendertk/mat_utils/marmoset_bridge/_marmoset_engine.py#L64) — Return the modes declared by *template_path*'s ``BRIDGE_MODES`` constant.
- [`list_template_modes() -> List[Tuple[str, str]]`](blendertk/blendertk/mat_utils/marmoset_bridge/_marmoset_engine.py#L73) — Return ``[(stem, mode), ...]`` for every (template, mode) pairing.
- **[`class MarmosetEngine(ptk.Deliverer, ptk.LoggingMixin)`](blendertk/blendertk/mat_utils/marmoset_bridge/_marmoset_engine.py#L83)** — Export-agnostic Marmoset Toolbag automation -- a hand-off :class:`pythontk.Deliverer`.
  - `MarmosetEngine.toolbag_path(self) -> Optional[str]` *(property)* — Resolve the Toolbag executable path.
  - `MarmosetEngine.toolbag_log_path(self) -> Optional[str]` *(property)* — Resolve Toolbag's application log file (script prints + tracebacks).
  - `MarmosetEngine.preflight(self, bridge, request) -> bool` — Validate the (template, mode) before the bridge produces its payload.
  - `MarmosetEngine.deliver(self, bridge, payload, request) -> Optional[Dict[str, Any]]` — Hand the produced model + manifests to Toolbag via :meth:`send`.
  - `MarmosetEngine.send(self, model_path: str, manifest_path: Optional[str] = None, pairs_path: Optional[str] = None, output_dir: Optional[str] = None, output_name: Optional[str] = None, toolbag_exe: Optional[str] = None, template: str = 'import', mode: str = SEND_TO, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]` — Render *template* in *mode* against *model_path* and hand off to Toolbag.
  - `MarmosetEngine.render_template(self, template: str, model_path: str, manifest_path: str, output_dir: str, mode: str = SEND_TO, params: Optional[Dict[str, Any]] = None, headless: Optional[bool] = None, pairs_path: Optional[str] = None) -> Optional[str]` — Return the rendered Toolbag Python script body, or *None* on miss.

<a id="mat_utils--marmoset_bridge--_toolbag_helpers"></a>
### `mat_utils/marmoset_bridge/_toolbag_helpers.py`

Shared helpers for Marmoset Toolbag template scripts.

- [`derive_per_run_log_path(manifest_path)`](blendertk/blendertk/mat_utils/marmoset_bridge/_toolbag_helpers.py#L41) — Return the ``<base>.toolbag.log`` path next to *manifest_path*.
- [`begin_log(reference_path)`](blendertk/blendertk/mat_utils/marmoset_bridge/_toolbag_helpers.py#L55) — Start a fresh log file alongside *reference_path*.
- [`log(msg)`](blendertk/blendertk/mat_utils/marmoset_bridge/_toolbag_helpers.py#L75) — Print *msg* and (best-effort) append it to the active log file.
- [`find_material(name, scene_mats)`](blendertk/blendertk/mat_utils/marmoset_bridge/_toolbag_helpers.py#L153) — Return the Toolbag material whose name matches *name*.
- [`load_manifest(manifest_path)`](blendertk/blendertk/mat_utils/marmoset_bridge/_toolbag_helpers.py#L168) — Return the ``materials`` dict from a MatManifest JSON sidecar.
- [`wire_materials_from_manifest(manifest_path, verbose=True)`](blendertk/blendertk/mat_utils/marmoset_bridge/_toolbag_helpers.py#L185) — Wire every texture slot in *manifest_path* onto matching Toolbag mats.
- [`split_high_low(objects, high_suffix, low_suffix, pre_classified=None)`](blendertk/blendertk/mat_utils/marmoset_bridge/_toolbag_helpers.py#L309) — Group *objects* into ``(highs, lows, others)`` by name suffix.
- [`collect_mesh_objects(root)`](blendertk/blendertk/mat_utils/marmoset_bridge/_toolbag_helpers.py#L391) — Recursively gather ``mset.MeshObject`` descendants of *root*.
- [`apply_sky_preset(preset_path)`](blendertk/blendertk/mat_utils/marmoset_bridge/_toolbag_helpers.py#L431) — Load a ``.tbsky`` preset onto the scene's existing SkyObject.
- [`frame_in_viewport()`](blendertk/blendertk/mat_utils/marmoset_bridge/_toolbag_helpers.py#L455) — Frame the imported scene in the viewport (best-effort).

<a id="mat_utils--marmoset_bridge--marmoset_bridge_slots"></a>
### `mat_utils/marmoset_bridge/marmoset_bridge_slots.py`

Slots for the Marmoset Toolbag bridge panel -- mirror of mayatk's

- **[`class MarmosetBridgeSlots(BlenderBridgeSlotsBase)`](blendertk/blendertk/mat_utils/marmoset_bridge/marmoset_bridge_slots.py#L30)** — Slots wired to ``marmoset_bridge.ui`` via :class:`BlenderBridgeSlotsBase`.
  - `MarmosetBridgeSlots.params_module(self)` *(property)*
  - `MarmosetBridgeSlots.template_dir(self) -> Path` *(property)*
  - `MarmosetBridgeSlots.make_bridge(self) -> MarmosetBridge`
  - `MarmosetBridgeSlots.list_template_modes(self)`
  - `MarmosetBridgeSlots.select_initial_template_index(self, pairs)` — Prefer 'bake (roundtrip)' then 'bake (send_to)', else first entry.
  - `MarmosetBridgeSlots.b000(self)` — Process selected objects with the chosen template + mode.

<a id="mat_utils--marmoset_bridge--marmoset_rpc--connection"></a>
### `mat_utils/marmoset_bridge/marmoset_rpc/connection.py`

JSON-RPC client bound to the marmoset_rpc Toolbag plugin.

- **[`class MarmosetConnection(RpcClient)`](blendertk/blendertk/mat_utils/marmoset_bridge/marmoset_rpc/connection.py#L46)** — JSON-RPC client bound to Toolbag's default port + finder.

<a id="mat_utils--marmoset_bridge--marmoset_rpc--installer"></a>
### `mat_utils/marmoset_bridge/marmoset_rpc/installer.py`

Install the marmoset_rpc plugin into Toolbag's user plugin folder.

- [`user_plugin_dir(toolbag_exe: Optional[str] = None) -> Optional[Path]`](blendertk/blendertk/mat_utils/marmoset_bridge/marmoset_rpc/installer.py#L38) — Resolve ``%LOCALAPPDATA%\Marmoset Toolbag <N>\plugins``.
- [`is_installed(toolbag_exe: Optional[str] = None) -> bool`](blendertk/blendertk/mat_utils/marmoset_bridge/marmoset_rpc/installer.py#L71) — True if the plugin is present at the resolved user plugin dir.
- [`install(toolbag_exe: Optional[str] = None, force: bool = False) -> Optional[Path]`](blendertk/blendertk/mat_utils/marmoset_bridge/marmoset_rpc/installer.py#L79) — Install the plugin into Toolbag's user plugin folder.
- [`uninstall(toolbag_exe: Optional[str] = None) -> bool`](blendertk/blendertk/mat_utils/marmoset_bridge/marmoset_rpc/installer.py#L98) — Remove the plugin from the user plugin folder.

<a id="mat_utils--marmoset_bridge--marmoset_rpc--job"></a>
### `mat_utils/marmoset_bridge/marmoset_rpc/job.py`

One-shot batch pipeline for the marmoset_rpc bridge.

- [`run_batch(calls: List[Call], host: str = '127.0.0.1', port: int = 8765, stop_on_error: bool = False) -> List[Result]`](blendertk/blendertk/mat_utils/marmoset_bridge/marmoset_rpc/job.py#L30) — Connect to a running Toolbag's marmoset_rpc plugin and fire calls.

<a id="mat_utils--marmoset_bridge--marmoset_rpc--plugin_src--marmoset_rpc--main_thread"></a>
### `mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/main_thread.py`

Main-thread marshalling for ops that touch Toolbag's API.

- [`run_on_main_thread(fn, *args, timeout=_DEFAULT_TIMEOUT, **kwargs)`](blendertk/blendertk/mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/main_thread.py#L50) — Run *fn* on the Qt main thread;
- [`is_main_thread_marshalling_active()`](blendertk/blendertk/mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/main_thread.py#L113) — True if :func:`run_on_main_thread` will actually marshal a call.

<a id="mat_utils--marmoset_bridge--marmoset_rpc--plugin_src--marmoset_rpc--ops--scene_ops"></a>
### `mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/ops/scene_ops.py`

Scene-inspection ops.

- [`summary()`](blendertk/blendertk/mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/ops/scene_ops.py#L14) — High-level snapshot of the current Toolbag scene.
- [`list_materials()`](blendertk/blendertk/mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/ops/scene_ops.py#L39) — Material names in the current scene.

<a id="mat_utils--marmoset_bridge--marmoset_rpc--plugin_src--marmoset_rpc--ops--system_ops"></a>
### `mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/ops/system_ops.py`

System-level ops: heartbeat, introspection, Toolbag version.

- [`ping()`](blendertk/blendertk/mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/ops/system_ops.py#L8) — Heartbeat -- proves the plugin is alive.
- [`list_ops()`](blendertk/blendertk/mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/ops/system_ops.py#L14) — Sorted list of every registered op name.
- [`describe_op(op='')`](blendertk/blendertk/mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/ops/system_ops.py#L20) — Return the JSON-friendly description of *op* or all ops if empty.
- [`version()`](blendertk/blendertk/mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/ops/system_ops.py#L31) — Toolbag build number (e.g.

<a id="mat_utils--marmoset_bridge--marmoset_rpc--plugin_src--marmoset_rpc--registry"></a>
### `mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/registry.py`

Op registry for the marmoset_rpc plugin.

- [`register(name)`](blendertk/blendertk/mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/registry.py#L21) — Decorator: register *fn* under *name*.
- [`get(name)`](blendertk/blendertk/mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/registry.py#L36) — Return the op function for *name*, or None.
- [`all_ops()`](blendertk/blendertk/mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/registry.py#L41) — Sorted list of every registered op name.
- [`describe(name=None)`](blendertk/blendertk/mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/registry.py#L46) — Return a JSON-friendly description of one op or all ops.
- [`clear()`](blendertk/blendertk/mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/registry.py#L82) — Reset the registry (test-only).

<a id="mat_utils--marmoset_bridge--marmoset_rpc--plugin_src--marmoset_rpc--server"></a>
### `mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/server.py`

HTTP JSON-RPC server for the marmoset_rpc plugin.

- [`start_server(port=None, host='127.0.0.1')`](blendertk/blendertk/mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/server.py#L97) — Start the HTTP server in a daemon thread.
- [`stop_server()`](blendertk/blendertk/mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/server.py#L117) — Shut down the server (mostly useful for tests / hot-reload).
- [`is_running()`](blendertk/blendertk/mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/server.py#L130)
- [`autostart()`](blendertk/blendertk/mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/server.py#L154) — Start the server on plugin load, gated to the Toolbag host.

<a id="mat_utils--marmoset_bridge--parameters"></a>
### `mat_utils/marmoset_bridge/parameters.py`

Registry of user-tunable Marmoset Toolbag parameters exposed to the bridge UI.

- [`referenced_keys(script_text: str) -> 'set[str]'`](blendertk/blendertk/mat_utils/marmoset_bridge/parameters.py#L235) — Registered keys present in *script_text* (delegates to uitk.bridge).
- [`defaults() -> 'dict[str, Any]'`](blendertk/blendertk/mat_utils/marmoset_bridge/parameters.py#L240) — Return ``{key: default}`` for every registered parameter.
- [`render_context(values: 'dict[str, Any]') -> 'dict[str, str]'`](blendertk/blendertk/mat_utils/marmoset_bridge/parameters.py#L245) — Format *values* for ``StrUtils.replace_delimited`` using Python literals.

<a id="mat_utils--marmoset_bridge--template_params"></a>
### `mat_utils/marmoset_bridge/template_params.py`

Plain default values + literal formatting for Marmoset template tokens.

- [`python_literal(value: Any) -> str`](blendertk/blendertk/mat_utils/marmoset_bridge/template_params.py#L49) — Format *value* as a Python source literal for template substitution.
- [`defaults() -> Dict[str, Any]`](blendertk/blendertk/mat_utils/marmoset_bridge/template_params.py#L60) — Return a copy of :data:`DEFAULTS`.
- [`to_context(values: Dict[str, Any]) -> Dict[str, str]`](blendertk/blendertk/mat_utils/marmoset_bridge/template_params.py#L65) — Map ``{KEY: value}`` to ``{KEY: python-literal-string}``.

<a id="mat_utils--marmoset_bridge--templates--bake"></a>
### `mat_utils/marmoset_bridge/templates/bake.py`

Bake high-poly detail into a low-poly target via Marmoset Toolbag.

- [`main()`](blendertk/blendertk/mat_utils/marmoset_bridge/templates/bake.py#L123)

<a id="mat_utils--marmoset_bridge--templates--import"></a>
### `mat_utils/marmoset_bridge/templates/import.py`

Open the model in Toolbag and wire materials from the manifest.

- [`main()`](blendertk/blendertk/mat_utils/marmoset_bridge/templates/import.py#L32)

<a id="mat_utils--marmoset_bridge--templates--lookdev"></a>
### `mat_utils/marmoset_bridge/templates/lookdev.py`

Open the model in Toolbag, apply a Sky preset, and frame the model.

- [`main()`](blendertk/blendertk/mat_utils/marmoset_bridge/templates/lookdev.py#L41)

<a id="mat_utils--marmoset_bridge--toolbag_log"></a>
### `mat_utils/marmoset_bridge/toolbag_log.py`

Marmoset Toolbag log-file resolution, classification, and live tailing.

- [`resolve_toolbag_log_path(toolbag_exe: Optional[str]) -> Optional[str]`](blendertk/blendertk/mat_utils/marmoset_bridge/toolbag_log.py#L29) — Return the path to Toolbag's application log, robust to version bumps.
- [`classify_log_line(line: str) -> Optional[Tuple[str, str]]`](blendertk/blendertk/mat_utils/marmoset_bridge/toolbag_log.py#L83) — Map a Toolbag log line to ``(level, line)`` for routing into a logger.
- [`dispatch_log_lines(lines, logger) -> None`](blendertk/blendertk/mat_utils/marmoset_bridge/toolbag_log.py#L134) — Forward each classified line to *logger* at its routed level.
- [`start_toolbag_log_tail(log_path: str, start_offset: int, process, logger, poll_interval: float = 0.4, file_wait_timeout: float = 60.0)`](blendertk/blendertk/mat_utils/marmoset_bridge/toolbag_log.py#L148) — Tail *log_path* from *start_offset* in a daemon thread.

<a id="mat_utils--mat_manifest"></a>
### `mat_utils/mat_manifest.py`

Material-to-texture manifest for bridge workflows -- mirror of mayatk's ``mat_utils.mat_manifest``.

- **[`class MatManifest(ptk.HelpMixin)`](blendertk/blendertk/mat_utils/mat_manifest.py#L31)** — Builds and restores a material-to-texture manifest for bridge workflows.
  - `MatManifest.build(cls, objects: List) -> Dict[str, Any]` *(class)* — Build a manifest from the materials assigned to *objects*.
  - `MatManifest.restore(cls, mat_name: str, manifest: Dict[str, Any], source_mat_name: Optional[str] = None) -> int` *(class)* — Reconnect image textures to the material named *mat_name* from a manifest.

<a id="mat_utils--mat_updater"></a>
### `mat_utils/mat_updater.py`

Material Updater tool panel — Switchboard slot wiring for the co-located ``mat_updater.ui``.

- **[`class MatUpdaterSlots(MatUpdater)`](blendertk/blendertk/mat_utils/mat_updater.py#L34)** — Switchboard slot wiring for the Material Updater panel.
  - `MatUpdaterSlots.header_init(self, widget)` — Format global options in the header menu (mirror of the Maya panel's, minus the
  - `MatUpdaterSlots.selection_mode(self)` *(property)*
  - `MatUpdaterSlots.move_to_folder(self)` *(property)*
  - `MatUpdaterSlots.max_size(self)` *(property)*
  - `MatUpdaterSlots.mask_map_scale(self)` *(property)*
  - `MatUpdaterSlots.output_extension(self)` *(property)*
  - `MatUpdaterSlots.old_files_folder(self)` *(property)*
  - `MatUpdaterSlots.cmb001_init(self, widget)` — Initialize Presets
  - `MatUpdaterSlots.b001(self)` — Update Materials

<a id="mat_utils--render_opacity--_render_opacity"></a>
### `mat_utils/render_opacity/_render_opacity.py`

Render Opacity — Blender per-object opacity for engine-ready transparency (mirror of mayatk's

- **[`class RenderOpacity(ptk.LoggingMixin)`](blendertk/blendertk/mat_utils/render_opacity/_render_opacity.py#L31)** — Per-object opacity: keyable ``opacity`` prop + Principled-Alpha driver + visibility mirror.
  - `RenderOpacity.objects_with_visibility_keys(cls, objects) -> list` *(class)* — The subset of *objects* that already have keyframes on render visibility.
  - `RenderOpacity.create(cls, objects=None, mode: str = 'attribute', delete_visibility_keys: bool = False)` *(class)* — Add the ``opacity`` prop to *objects* and drive each material's Principled Alpha from it.
  - `RenderOpacity.remove(cls, objects=None, mode=None)` *(class)* — Remove the opacity prop, its Alpha drivers, and its anim curves from *objects*.
  - `RenderOpacity.key_fade(cls, objects=None, start=0, end=15, direction='in', auto_create=True, tangent='LINEAR')` *(class)* — Key an opacity fade (linear) and mirror it to render visibility (stepped).
  - `RenderOpacity.sync_visibility_from_opacity(cls, objects=None) -> None` *(class)* — Rebuild the ``hide_render`` curve from the ``opacity`` curve (stepped, hidden when ≤ 0).
  - `RenderOpacity.ensure_connections(cls, objects=None) -> None` *(class)* — Re-establish the Alpha driver on objects that have ``opacity`` but lost it (e.g.
  - `RenderOpacity.prepare_for_export(cls, objects=None) -> list` *(class)* — Dual-key safety net before FBX export: for every object with an animated ``opacity`` but

<a id="mat_utils--render_opacity--render_opacity_slots"></a>
### `mat_utils/render_opacity/render_opacity_slots.py`

Switchboard slots for the Render Opacity panel (``render_opacity.ui``).

- **[`class RenderOpacitySlots(ptk.LoggingMixin)`](blendertk/blendertk/mat_utils/render_opacity/render_opacity_slots.py#L20)** — Switchboard slots for the Render Opacity UI.
  - `RenderOpacitySlots.header_init(self, widget)` — Configure header menu.
  - `RenderOpacitySlots.tb000_init(self, widget)` — Key Render Opacity Init — configure option-box menu.
  - `RenderOpacitySlots.tb000(self, widget)` — Key Render Opacity — key a fade on the opacity property (+ mirror to visibility).

<a id="mat_utils--shader_templates"></a>
### `mat_utils/shader_templates.py`

Shader Templates tool panel — Switchboard slot wiring for the co-located

- **[`class ShaderTemplatesSlots(ptk.LoggingMixin)`](blendertk/blendertk/mat_utils/shader_templates.py#L50)** — Switchboard slot wiring for the Shader Templates panel.
  - `ShaderTemplatesSlots.workspace_dir(self) -> str` *(property)*
  - `ShaderTemplatesSlots.source_images_dir(self) -> str` *(property)*
  - `ShaderTemplatesSlots.template_name(self)` *(property)*
  - `ShaderTemplatesSlots.header_init(self, widget)` — Initialize the header widget.
  - `ShaderTemplatesSlots.lbl_graph_material(self)` — Open the last restored material in the Shader Editor.
  - `ShaderTemplatesSlots.lbl_open_templates_dir(self)` — Open the shader templates directory in the OS file manager.
  - `ShaderTemplatesSlots.cmb002_init(self, widget)` — Initialize the ComboBox for shader templates.
  - `ShaderTemplatesSlots.refresh_templates(self, widget)` — Refresh the list of templates.
  - `ShaderTemplatesSlots.rename_template_safe(self, widget, new_name)` — Safe rename that checks for None.
  - `ShaderTemplatesSlots.lbl000(self)` — Set the ComboBox as editable to allow renaming.
  - `ShaderTemplatesSlots.lbl001(self)` — Delete the selected template.
  - `ShaderTemplatesSlots.lbl002(self)` — Open the selected template in the default editor.
  - `ShaderTemplatesSlots.b000(self)` — Create shader network using selected template.
  - `ShaderTemplatesSlots.b001(self)` — Load texture maps and update GUI.
  - `ShaderTemplatesSlots.b002(self)` — Save current graph as a new shader template.

<a id="mat_utils--substance_bridge--_substance_bridge"></a>
### `mat_utils/substance_bridge/_substance_bridge.py`

Substance 3D Painter bridge -- export Blender selection and hand off to Painter.

- [`list_templates() -> List[Path]`](blendertk/blendertk/mat_utils/substance_bridge/_substance_bridge.py#L106) — Return user-visible templates in ``templates/`` (skips underscore-prefixed).
- [`parse_template(template_path: Path) -> Dict[str, Any]`](blendertk/blendertk/mat_utils/substance_bridge/_substance_bridge.py#L124) — Read a template's metadata constants without executing the file.
- [`list_template_modes() -> List[Tuple[str, str]]`](blendertk/blendertk/mat_utils/substance_bridge/_substance_bridge.py#L186) — Return ``[(stem, mode), ...]`` for every (template, mode) pairing.
- [`resolve_painter_log_path(painter_exe: Optional[str] = None) -> Optional[str]`](blendertk/blendertk/mat_utils/substance_bridge/_substance_bridge.py#L198) — Return the path to Painter's application log.
- **[`class SubstanceBridge(ptk.HandoffBridge)`](blendertk/blendertk/mat_utils/substance_bridge/_substance_bridge.py#L215)** — Export Blender selection to Substance Painter via a chosen template.
  - `SubstanceBridge.painter_path(self) -> Optional[str]` *(property)* — Resolve the Painter executable path via :func:`find_painter_exe`.
  - `SubstanceBridge.painter_log_path(self) -> Optional[str]` *(property)* — Path to Painter's application ``log.txt``, or *None* if absent.
  - `SubstanceBridge.instances(self) -> List[SubstanceConnection]` *(property)* — Live snapshot of managed connections (oldest -> newest, dead pruned).
  - `SubstanceBridge.find_live_managed(self) -> Optional[SubstanceConnection]` — Return the most-recently-launched managed instance whose RPC pings.
  - `SubstanceBridge.send(self, objects: Optional[List[str]] = None, output_dir: Optional[str] = None, output_name: Optional[str] = None, painter_exe: Optional[str] = None, fbx_options: Optional[Dict[str, Any]] = None, template: str = 'import', mode: str = SEND_TO, target: Union[str, int] = TARGET_AUTO, params: Optional[Dict[str, Any]] = None, **legacy_kwargs: Any) -> Optional[Dict[str, Any]]` — Export *objects*, render *template* in *mode*, hand off to Painter.

<a id="mat_utils--substance_bridge--connection"></a>
### `mat_utils/substance_bridge/connection.py`

Substance 3D Painter connection module.

- [`find_painter_exe() -> Optional[str]`](blendertk/blendertk/mat_utils/substance_bridge/connection.py#L55) — Single source of truth for Painter executable discovery.
- [`default_log_path() -> Optional[str]`](blendertk/blendertk/mat_utils/substance_bridge/connection.py#L69) — Return the standard Substance Painter log path, or None if absent.
- **[`class SubstanceConnection(ptk.LoggingMixin)`](blendertk/blendertk/mat_utils/substance_bridge/connection.py#L82)** — Launch Painter and expose its stdio, log, and RPC under one object.
  - `SubstanceConnection.open(self) -> 'SubstanceConnection'` — Launch Painter and start readers, tailer, and RPC client.
  - `SubstanceConnection.close(self, terminate: bool = False, timeout: float = 5.0) -> None` — Stop readers and tailer;
  - `SubstanceConnection.is_alive(self) -> bool` — True if Painter is reachable through this connection.
  - `SubstanceConnection.attach(cls, port: int, host: str = '127.0.0.1', log_path: Optional[str] = None, tail_log_from_start: bool = False, verify_alive: bool = True, verify_timeout: float = 2.0) -> 'SubstanceConnection'` *(class)* — Bind to a running Painter on *port* without launching anything.

<a id="mat_utils--substance_bridge--parameters"></a>
### `mat_utils/substance_bridge/parameters.py`

Registry of user-tunable Substance Painter parameters exposed to the bridge UI.

- [`referenced_keys(script_text: str) -> 'set[str]'`](blendertk/blendertk/mat_utils/substance_bridge/parameters.py#L174) — Registered keys present in *script_text* (delegates to uitk.bridge).
- [`defaults() -> 'dict[str, Any]'`](blendertk/blendertk/mat_utils/substance_bridge/parameters.py#L179) — Return ``{key: default}`` for every registered parameter.
- [`render_cli_context(values: 'dict[str, Any]') -> 'dict[str, str]'`](blendertk/blendertk/mat_utils/substance_bridge/parameters.py#L184) — Format *values* for ``LAUNCH_ARGS`` -- raw, no quoting.
- [`render_js_context(values: 'dict[str, Any]') -> 'dict[str, str]'`](blendertk/blendertk/mat_utils/substance_bridge/parameters.py#L189) — Format *values* for ``RPC_SCRIPT`` -- JS-literal quoting/escaping.

<a id="mat_utils--substance_bridge--substance_bridge_slots"></a>
### `mat_utils/substance_bridge/substance_bridge_slots.py`

Slots for the Substance Painter bridge panel -- mirror of mayatk's

- **[`class SubstanceBridgeSlots(BlenderBridgeSlotsBase)`](blendertk/blendertk/mat_utils/substance_bridge/substance_bridge_slots.py#L34)** — Slots wired to ``substance_bridge.ui`` via :class:`BlenderBridgeSlotsBase`.
  - `SubstanceBridgeSlots.params_module(self)` *(property)*
  - `SubstanceBridgeSlots.template_dir(self) -> Path` *(property)*
  - `SubstanceBridgeSlots.make_bridge(self) -> SubstanceBridge`
  - `SubstanceBridgeSlots.list_template_modes(self)`
  - `SubstanceBridgeSlots.select_initial_template_index(self, pairs)` — Default the panel to ``import (send_to)`` when it's available.
  - `SubstanceBridgeSlots.b000(self)` — Process the selected objects with the chosen template + mode.

<a id="mat_utils--substance_bridge--substance_rpc--client"></a>
### `mat_utils/substance_bridge/substance_rpc/client.py`

JSON-RPC 2.0 client for a Painter-side Python plugin.

- **[`class PainterRpcClient`](blendertk/blendertk/mat_utils/substance_bridge/substance_rpc/client.py#L24)** — JSON-RPC 2.0 client for a Painter-side JSON server.
  - `PainterRpcClient.url(self) -> str` *(property)*
  - `PainterRpcClient.ping(self, timeout: float = 1.0) -> bool` — Return True if a TCP connection succeeds.
  - `PainterRpcClient.wait_until_ready(self, timeout: float = 60.0, poll_interval: float = 0.5) -> bool` — Poll the port until it accepts connections, or *timeout* expires.
  - `PainterRpcClient.call(self, method: str, params: Optional[dict] = None) -> dict` — Send a JSON-RPC method call.
  - `PainterRpcClient.eval_js(self, script: str) -> dict` — Convenience: execute a JavaScript snippet via ``eval``.

<a id="mat_utils--texture_baker"></a>
### `mat_utils/texture_baker.py`

Bake an object's shaded surface (material under scene lighting) to a texture — the Blender

- **[`class TextureBaker(ptk.LoggingMixin)`](blendertk/blendertk/mat_utils/texture_baker.py#L28)** — Generic Cycles bake-to-texture primitive (mirror of mayatk's ``TextureBaker``).
  - `TextureBaker.bake(self, objects=None, *, bake_type: str = 'COMBINED', pass_filter: Optional[set] = None, use_pass_color: bool = True, output_dir: Optional[str] = None, prefix: str = '', suffix: str = '', margin: Optional[int] = None, uv_set=None, stem: Optional[Any] = None, on_progress: Optional[Callable[[int, int, str], bool]] = None, colorspace: str = 'Non-Color') -> Dict[str, str]` — Bake each object's shaded surface to a per-object EXR.
  - `TextureBaker.resolve_meshes(objects) -> List[Any]` *(static)* — Normalize ``objects`` (refs / names / None=selection) to mesh objects.
  - `TextureBaker.texture_set_stem(obj) -> Optional[str]` *(static)* — Base name of *obj*'s existing texture set (e.g.
  - `TextureBaker.default_output_dir(subdir: str = 'baked_textures') -> str` *(static)* — ``<subdir>`` next to the saved .blend, else under the OS temp dir.

<a id="mat_utils--texture_path_editor"></a>
### `mat_utils/texture_path_editor.py`

Texture Path Editor tool panel — Switchboard slot wiring for the co-located

- **[`class TexturePathEditorSlots(ptk.LoggingMixin)`](blendertk/blendertk/mat_utils/texture_path_editor.py#L39)** — Switchboard slot wiring for the Texture Path Editor panel.
  - `TexturePathEditorSlots.header_init(self, widget)` — Build the header menu (General / Path Management / Selection) + help text.
  - `TexturePathEditorSlots.tb_set_texture_directory_init(self, widget)` — Populate the Set Directory option-box with the relocate-mode combobox.
  - `TexturePathEditorSlots.tb_find_and_copy_textures_init(self, widget)` — Populate the Find & Copy option-box with the copy/move combobox.
  - `TexturePathEditorSlots.tb_normalize_paths_init(self, widget)` — Populate the Normalize Paths option-box with the external-mode combobox.
  - `TexturePathEditorSlots.tb_resolve_missing_textures_init(self, widget)` — Populate the Resolve Missing option-box with the strategy checkboxes.
  - `TexturePathEditorSlots.tbl000_init(self, widget)` — Build the row context menu once, then (re)populate the table.
  - `TexturePathEditorSlots.setup_formatting(self, widget, records)` — Mark the path cell invalid (red) when its file is missing;
  - `TexturePathEditorSlots.open_source_images(self)` — Open the project's textures directory in the file explorer.
  - `TexturePathEditorSlots.reload_scene_textures(self)` — Force Blender to re-read every image from disk.
  - `TexturePathEditorSlots.tb_set_texture_directory(self, widget=None)` — Repath images (selection or all) so their files live under a chosen directory.
  - `TexturePathEditorSlots.tb_find_and_copy_textures(self, widget=None)` — Search a folder for the images' textures, copy/move to a destination, repath.
  - `TexturePathEditorSlots.tb_normalize_paths(self, widget=None)` — Rewrite (selected, or all) paths relative to the saved .blend;
  - `TexturePathEditorSlots.tb_resolve_missing_textures(self, widget=None)` — Search a folder for replacements for missing (selected, or all) textures.
  - `TexturePathEditorSlots.select_textures_for_objects(self)` — Select rows whose image is used by a material on the scene selection.
  - `TexturePathEditorSlots.select_broken_paths(self)` — Select rows whose texture file is missing.
  - `TexturePathEditorSlots.select_absolute_paths(self)` — Select rows whose path is absolute (not a // project-relative path).
  - `TexturePathEditorSlots.row_browse_for_file(self, selection=None)` — Open a file dialog and repath the selected row's image (single selection only).
  - `TexturePathEditorSlots.select_material(self, selection=None)` — Select scene objects using the materials of the selected rows.
  - `TexturePathEditorSlots.select_file_node(self, selection=None)` — Disabled (see the row-menu tooltip) — retained for structural parity with mayatk's
  - `TexturePathEditorSlots.row_show_in_hypershade(self, selection=None)` — Graph the selected row's material(s) in the Shader Editor (Hypershade analogue).
  - `TexturePathEditorSlots.delete_file_node(self, selection=None)` — Remove the selected image datablock(s).
  - `TexturePathEditorSlots.handle_cell_edit(self, row, col)` — Editing a path cell repaths that row's image;
  - `TexturePathEditorSlots.refresh_texture_table(self)` — Manual refresh trigger from the header refresh button.
  - `TexturePathEditorSlots.cleanup_scene_callbacks(self)` — Clean up scene-change subscriptions via ScriptJobManager.

<a id="node_utils--_node_utils"></a>
### `node_utils/_node_utils.py`

Node / datablock utilities — instancing via shared object data.

- [`get_instances(objects=None)`](blendertk/blendertk/node_utils/_node_utils.py#L24) — Return objects that share their data with another object (Maya-style instances).
- [`replace_with_instances(objects, freeze_transforms=False, center_pivot=False, delete_history=False)`](blendertk/blendertk/node_utils/_node_utils.py#L41) — Make ``objects[1:]`` instances of ``objects[0]`` by sharing its data — Blender's linked
- [`uninstance(objects)`](blendertk/blendertk/node_utils/_node_utils.py#L71) — Break the instance link — make each object's data single-user (mirror of ``mtk.uninstance``).
- [`get_parent(obj, all=False)`](blendertk/blendertk/node_utils/_node_utils.py#L84) — The object's parent — mirror of ``mtk.get_parent``.
- [`get_children(obj, recursive=False)`](blendertk/blendertk/node_utils/_node_utils.py#L97) — The object's children — mirror of ``mtk.get_children``.
- [`get_shape(obj)`](blendertk/blendertk/node_utils/_node_utils.py#L105) — The object's data datablock (mesh/curve/…) — the Blender analogue of Maya's shape node
- [`reparent(objects, parent, keep_transform=True)`](blendertk/blendertk/node_utils/_node_utils.py#L112) — Parent ``objects`` under ``parent`` (``None`` to unparent) — mirror of ``mtk.reparent``.
- **[`class NodeUtils`](blendertk/blendertk/node_utils/_node_utils.py#L131)** — Namespace mirror of mayatk's ``NodeUtils`` (instance helpers also exposed module-level).

<a id="node_utils--attributes--channels--_channels"></a>
### `node_utils/attributes/channels/_channels.py`

Channels — Blender attribute query / mutation logic.

- **[`class Channels`](blendertk/blendertk/node_utils/attributes/channels/_channels.py#L22)** — Blender attribute query / mutation logic.
  - `Channels.is_pinned(self)` *(property)*
  - `Channels.single_object_mode(self)` *(property)*
  - `Channels.pin_targets(self, objects)` — Pin the manager to a fixed object list;
  - `Channels.get_selected_nodes(self)` — Return the target object list.
  - `Channels.collect_channels(cls, objects, filter_key='custom', invert=False)` *(class)* — Return the channel descriptors shared across all *objects* for the given filter.
  - `Channels.get_channel_value(cls, obj, descriptor)` *(class)* — Return the raw Python value for *descriptor* on *obj* (``None`` on failure).
  - `Channels.format_value(cls, val)` *(class)* — Convert a channel value to a display string (``"*"`` marks a mixed multi-selection).
  - `Channels.parse_value(cls, text, descriptor)` *(class)* — Convert user-entered *text* to a Python value for *descriptor* (``None`` = skip).
  - `Channels.is_locked(cls, obj, descriptor)` *(class)* — Lock state for *descriptor*.
  - `Channels.toggle_lock(cls, objects, descriptor)` *(class)* — Toggle the lock state of *descriptor* across *objects* (transform channels only).
  - `Channels.set_lock(cls, objects, descriptors, lock)` *(class)* — Lock or unlock *descriptors* across all *objects* (transform channels only).
  - `Channels.classify_connection(cls, obj, descriptor)` *(class)* — Classify what drives *descriptor* on *obj*.
  - `Channels.build_table_data(cls, objects, filter_key='custom', invert=False)` *(class)* — Build ``(rows, states)`` for the table.
  - `Channels.set_channel_value(cls, objects, descriptor, text)` *(class)* — Parse *text* and set *descriptor* on all *objects*.
  - `Channels.reset_to_default(cls, objects, descriptors)` *(class)* — Reset *descriptors* to their default values across all *objects*.
  - `Channels.toggle_key_at_current_time(cls, objects, descriptor)` *(class)* — Set or remove a keyframe on *descriptor* at the current frame across *objects*.
  - `Channels.break_connections(cls, objects, descriptor)` *(class)* — Remove the animation / driver on *descriptor* across *objects* (Maya's break-connection).
  - `Channels.set_mute(cls, objects, descriptors, mute=True)` *(class)* — Mute / unmute the F-curve (or driver) on each descriptor across *objects*.
  - `Channels.set_breakdown_key(cls, objects, descriptors)` *(class)* — Set a breakdown key on *descriptors* at the current frame across *objects*.
  - `Channels.select_connections(cls, objects, descriptor)` *(class)* — Select the object(s) driving *descriptor* on the primary object.
  - `Channels.create_attribute(cls, objects, name, attr_type, min_val=None, max_val=None, default_val=0.0)` *(class)* — Create a custom (ID) property on *objects*.
  - `Channels.delete_attributes(cls, objects, descriptors)` *(class)* — Delete custom *descriptors* across all *objects* (transform channels are skipped).
  - `Channels.rename_attribute(cls, objects, old_name, new_name)` *(class)* — Rename a custom property on *objects* (preserves value + UI metadata).
  - `Channels.rename_node(obj, new_name)` *(static)* — Rename the object datablock and return its (possibly suffixed) new name.
  - `Channels.copy_values(self, objects, descriptors)` — Copy *descriptors*' values from the primary object into the instance clipboard.
  - `Channels.paste_values(self, objects)` — Paste previously copied values onto *objects* (matched by channel name).
  - `Channels.freeze_transforms(cls, objects, descriptors=None)` *(class)* — Apply (freeze) transforms on *objects*, restricted to the touched channel groups.
  - `Channels.unfreeze_transforms(cls, objects)` *(class)* — Restore previously frozen transforms on *objects* (Maya's Unfreeze Transforms).
  - `Channels.has_unfreeze_info(objects)` *(static)* — Return ``True`` when at least one of *objects* carries stored freeze data.

<a id="node_utils--attributes--channels--channels_slots"></a>
### `node_utils/attributes/channels/channels_slots.py`

UI slots for the Channels panel (``channels.ui``).

- **[`class ChannelsSlots`](blendertk/blendertk/node_utils/attributes/channels/channels_slots.py#L30)** — Switchboard slots for the Channels panel.
  - `ChannelsSlots.apply_launch_config(self, targets=None, filter=None, search=None)` — Configure the window from an external launch call (mirror of mayatk).
  - `ChannelsSlots.cmb000_init(self, widget)` — Populate the filter combobox + wire its invert action (bpy-free).
  - `ChannelsSlots.cmb000(self, index)` — Filter changed — refresh table.
  - `ChannelsSlots.header_init(self, widget)` — Populate the header menu (Qt-only;
  - `ChannelsSlots.show_create_menu(self, *args)` — Show the *Create Attribute* popup (a custom-property form).
  - `ChannelsSlots.tbl000_init(self, widget)` — One-time table setup (action columns + context menu + signals), then a guarded refresh.

<a id="node_utils--data_nodes"></a>
### `node_utils/data_nodes.py`

Scene-wide export-metadata carrier — mirror of mayatk's ``node_utils.data_nodes``.

- **[`class DataNodes`](blendertk/blendertk/node_utils/data_nodes.py#L11)** — Scene-wide export-metadata carrier (mirror of mayatk's ``node_utils.DataNodes``).
  - `DataNodes.get_internal_node(create=True)` *(static)* — The ``data_internal`` Empty (created + linked to the scene when *create*).
  - `DataNodes.ensure_internal()` *(static)* — Get or create the ``data_internal`` Empty.
  - `DataNodes.set_internal_string(key, value)` *(static)* — Set custom property *key* on the internal carrier to *value* (string) — see
  - `DataNodes.get_internal_string(key)` *(static)* — The internal carrier's *key* custom property, or ``None`` — see ``_get_string``;
  - `DataNodes.get_export_node(create=True)` *(static)* — The ``data_export`` Empty (created + linked to the scene when *create*).
  - `DataNodes.ensure_export()` *(static)* — Get or create the ``data_export`` Empty.
  - `DataNodes.set_export_string(key, value)` *(static)* — Set custom property *key* on the carrier to *value* (string) — see ``_set_string``
  - `DataNodes.get_export_string(key)` *(static)* — The carrier's *key* custom property, or ``None`` — see ``_get_string``;
  - `DataNodes.dump(decode=True)` *(static)* — Every tool-authored channel on both carriers, grouped by object — mirror of
  - `DataNodes.format_dump(decode=True)` *(static)* — Pretty-printed JSON of :meth:`dump`, or ``""`` when nothing is stored — mirror of

<a id="nurbs_utils--_nurbs_utils"></a>
### `nurbs_utils/_nurbs_utils.py`

Shared curve helpers — Blender mirror of mayatk's ``nurbs_utils.NurbsUtils`` namespace.

- **[`class NurbsUtils(ptk.LoggingMixin)`](blendertk/blendertk/nurbs_utils/_nurbs_utils.py#L16)** — Shared Blender curve primitives (mirror of mayatk's ``NurbsUtils``).
  - `NurbsUtils.add_spline(curve, points, cyclic=False, kind='POLY')` *(static)* — Append a spline of ``points`` (each an ``(x, y, z)``) to an existing curve.
  - `NurbsUtils.create_curve(cls, points, name='curve', cyclic=False, kind='POLY', dimensions='3D', link=True, collection=None)` *(class)* — Build a curve object from a point list — mirror of mayatk's ``cmds.curve`` usage.
  - `NurbsUtils.duplicate_curve(curve_obj, name=None, link=True)` *(static)* — A curve-data duplicate of ``curve_obj``, linked into the same collection(s) — the
  - `NurbsUtils.create_plane(width=1.0, height=1.0, location=(0.0, 0.0, 0.0), name='plane', link=True, collection=None)` *(static)* — Build a simple rectangular mesh plane centered at ``location`` — Blender analogue of
  - `NurbsUtils.curve_to_mesh(curve_obj, name=None, link=True, keep_curve=False, collection=None)` *(static)* — Bake a curve object's **evaluated** geometry (its bevel sweep / 2D fill) to a new mesh

<a id="nurbs_utils--curve_to_tube"></a>
### `nurbs_utils/curve_to_tube.py`

Curve to Tube tool — Blender port of mayatk's ``nurbs_utils.curve_to_tube``.

- **[`class CurveToTube(ptk.LoggingMixin)`](blendertk/blendertk/nurbs_utils/curve_to_tube.py#L45)** — Sweep a circular profile along curve(s) to build a tube — Blender mirror of mayatk's
  - `CurveToTube.create(cls, curves, output_type='nurbs', radius=1.0, sections=8, path_divisions=1, degree=3, caps=True, quads=True, live=False, name='tube')` *(class)* — Build a tube along each given curve.
- **[`class CurveToTubeSlots(ptk.LoggingMixin)`](blendertk/blendertk/nurbs_utils/curve_to_tube.py#L172)** — Switchboard slot wiring for the Curve to Tube panel — structural mirror of mayatk's
  - `CurveToTubeSlots.header_init(self, widget)` — Configure header help text.
  - `CurveToTubeSlots.b001(self)` — Reset to Defaults.
  - `CurveToTubeSlots.perform_operation(self, objects)` — Build the tube(s) from the selected curves (Preview entry point).

<a id="nurbs_utils--image_tracer"></a>
### `nurbs_utils/image_tracer.py`

Image Tracer tool — Blender port of mayatk's ``nurbs_utils.image_tracer``.

- **[`class ImageTracer(ptk.LoggingMixin)`](blendertk/blendertk/nurbs_utils/image_tracer.py#L48)** — Trace a raster image into curves / filled meshes — Blender mirror of mayatk's ``ImageTracer``.
  - `ImageTracer.trace_curves(self, name='traced_curve')` — Trace the image into ONE curve object — one cyclic POLY spline per contour (so nested
  - `ImageTracer.create_mesh(self, curve=None, name='traced_mesh')` — Fill the traced contours into a mesh (positive space;
  - `ImageTracer.create_negative_space_mesh(self, curve=None, margin_scale=0.1, name='negative_space_mesh')` — Fill the **inverse**: a boundary rectangle (margin-padded bbox) around the contours, with
  - `ImageTracer.project_on_plane(self, curve=None, name='projected_curves')` — Project the traced curves onto a construction plane — Blender analogue of Maya's
- **[`class ImageTracerSlots(ptk.LoggingMixin)`](blendertk/blendertk/nurbs_utils/image_tracer.py#L182)** — Switchboard slot wiring for the co-located ``image_tracer.ui`` (structural mirror of
  - `ImageTracerSlots.header_init(self, widget)` — Initialize the header widget.
  - `ImageTracerSlots.txt000_init(self, widget)` — Configure the path field's option box (▸) as an image file browser.
  - `ImageTracerSlots.browse_image(self)` — Kept for structural parity with mayatk (unused in practice — txt000's option-box
  - `ImageTracerSlots.chk000(self, state)` — Use Blue Pencil (disabled — kept wired for structural parity, see header_init).
  - `ImageTracerSlots.b002(self)` — Trace the source image into curves.
  - `ImageTracerSlots.b003(self)` — Build a mesh from the traced curves.
  - `ImageTracerSlots.b004(self)` — Build a mesh from the traced negative space.
  - `ImageTracerSlots.b005(self)` — Project the traced result onto a plane.

<a id="rig_utils--_rig_utils"></a>
### `rig_utils/_rig_utils.py`

Shared procedural-rig primitives — Blender port of mayatk's ``rig_utils.RigUtils``.

- **[`class RigUtils`](blendertk/blendertk/rig_utils/_rig_utils.py#L20)** — Constraint / driver / handle / grouping / armature helpers shared by the procedural rigs.
  - `RigUtils.resolve_object(obj)` *(static)* — An object or its name → the ``bpy`` object (``None`` if missing).
  - `RigUtils.create_locator(name='locator', location=(0, 0, 0), display_type='PLAIN_AXES', size=1.0, collection=None)` *(static)* — Create an Empty — Blender's analogue of Maya's spaceLocator (a rig handle).
  - `RigUtils.create_group(name='rig_grp', location=(0, 0, 0), children=None)` *(static)* — Create an Empty used as a transform group, parenting ``children`` under it (keeping
  - `RigUtils.parent_keep_transform(child, parent)` *(static)* — Parent ``child`` to ``parent`` without moving it in world space (Maya ``parent`` default).
  - `RigUtils.create_armature(name='armature', location=(0, 0, 0), collection=None)` *(static)* — Create an empty Armature object (Maya's joint-chain container).
  - `RigUtils.add_bone_chain(armature, points, prefix='bone', connect=True, radius=None)` *(static)* — Build a connected bone chain through world-space *points* — Maya's ``generate_joint_chain``
  - `RigUtils.add_bone(armature, name, head, tail, parent=None, connect=False, radius=None, deform=True)` *(static)* — Add ONE bone to an existing armature at world-space *head*/*tail* — the single-bone
  - `RigUtils.get_bone_chain_from_root(armature, bone_name=None, reverse=False)` *(static)* — Walk a single-path bone chain from a root bone — mirror of mayatk's
  - `RigUtils.invert_bone_chain(armature, bone_names)` *(static)* — Rebuild *bone_names* (head->tail order) with reversed hierarchy — mirror of mayatk's
  - `RigUtils.add_bone_constraint(armature, bone_name, ctype, target=None, subtarget=None, **props)` *(static)* — Add a **pose-bone** constraint (``ctype`` e.g.
  - `RigUtils.add_spline_ik(armature, bone_name, curve, chain_count, name='Spline IK', **props)` *(static)* — Add a **Spline IK** bone constraint to pose bone *bone_name* so *chain_count* bones up the
  - `RigUtils.bind_armature(mesh, armature, auto_weights=True)` *(static)* — Bind *mesh* to *armature* (Maya ``skinCluster`` analogue).
  - `RigUtils.apply_falloff_weights(mesh, group_name, center, radius, profile='linear', add_group=True)` *(static)* — Distance-falloff vertex weights — the Blender (vertex-group) analogue of mayatk's
  - `RigUtils.copy_location(obj, target, influence=1.0)` *(static)* — Maya pointConstraint → COPY_LOCATION.
  - `RigUtils.copy_rotation(obj, target, influence=1.0)` *(static)* — Maya orientConstraint → COPY_ROTATION.
  - `RigUtils.damped_track(obj, target, track_axis='TRACK_Y')` *(static)* — Single-axis aim (Maya aimConstraint, no up-vector) → DAMPED_TRACK.
  - `RigUtils.track_to(obj, target, track_axis='TRACK_Y', up_axis='UP_Z')` *(static)* — Aim with an up-vector (full Maya aimConstraint) → TRACK_TO.
  - `RigUtils.child_of(obj, target, set_inverse=True)` *(static)* — Maya parentConstraint(maintainOffset=True) → CHILD_OF (inverse bound at the current pose).
  - `RigUtils.refresh_drivers(objects)` *(static)* — Force-recompile every driver on ``objects`` — call ONCE after building a rig's drivers.
  - `RigUtils.add_distance_driver(obj, data_path, index, a, b, expression='dist', var_name='dist')` *(static)* — Drive ``obj.<data_path>[index]`` from the live distance between objects ``a`` and ``b``
  - `RigUtils.add_transform_driver(obj, data_path, index, target, transform_type, space='WORLD_SPACE', expression=None, var_name='var')` *(static)* — Drive ``obj.<data_path>[index]`` from a single transform channel of ``target`` (a
  - `RigUtils.add_prop_var(fcurve, name, id_obj, data_path, id_type=None)` *(static)* — Append a ``SINGLE_PROP`` variable to an existing driver fcurve — e.g.
  - `RigUtils.add_transform_var(fcurve, name, target, transform_type, space='WORLD_SPACE')` *(static)* — Append a ``TRANSFORMS`` variable (a single transform channel of *target*) to an existing
  - `RigUtils.ensure_custom_prop(obj, name, value, min_value=None, max_value=None)` *(static)* — Set a keyable custom property (Maya's ``addAttr`` analogue), creating it if absent and
  - `RigUtils.remove_driver(obj, data_path, index=None)` *(static)* — Remove a driver on ``obj.<data_path>[index]`` if present (idempotent;
  - `RigUtils.lock_channels(obj, location=None, rotation=None, scale=None)` *(static)* — Lock the given transform channels (each a 3-tuple of bools, or ``None`` to leave as-is).

<a id="rig_utils--controls"></a>
### `rig_utils/controls.py`

Rig control-shape factory — Blender port of mayatk's ``rig_utils.controls.Controls``.

- **[`class ControlNodes`](blendertk/blendertk/rig_utils/controls.py#L30)** — Return bundle of :meth:`Controls.create` — mirror of mayatk's ``ControlNodes``.
- **[`class Controls`](blendertk/blendertk/rig_utils/controls.py#L94)** — Rig control-shape factory (curve-object widgets) — Blender mirror of mayatk's ``Controls``.
  - `Controls.register_preset(cls, name, builder)` *(class)* — Register a custom shape *builder* (``() -> List[(points, cyclic)]``, pure geometry in the
  - `Controls.shapes(cls) -> List[str]` *(class)* — Sorted names of the registered shapes (for a UI combo / validation).
  - `Controls.create(cls, shape='circle', name='ctrl', size=1.0, axis='y', color=None, location=(0, 0, 0), group=False, collection=None, return_nodes=False)` *(class)* — Build a control curve object in *shape*, scaled by *size*, oriented by *axis*, optionally

<a id="rig_utils--shadow_rig"></a>
### `rig_utils/shadow_rig.py`

Shadow Rig — engine + Switchboard slot wiring for the co-located ``shadow_rig.ui``.

- **[`class ShadowRig(ptk.LoggingMixin)`](blendertk/blendertk/rig_utils/shadow_rig.py#L73)** — Projected-shadow rig for engine export (mirror of mayatk's ``ShadowRig``).
  - `ShadowRig.create_contact_locator(self)` — Empty at the footprint's lowest point (min-Z), parented to the first target so it tracks.
  - `ShadowRig.get_or_create_shadow_source(self, position=(5.0, 5.0, 10.0), source_name='shadow_source')` — Reuse an existing source Empty by name, else create one (Z-up default: high on +Z).
  - `ShadowRig.create_shadow_plane(self)` — Create a flat quad on the XY ground (normal +Z), centered at the footprint, with the
  - `ShadowRig.create_silhouette_texture(self, size=512, axis='auto', recursive=True, **kwargs)` — Rasterize the targets' world silhouette to an RGBA PNG via
  - `ShadowRig.create_material(self)` — Unlit black Emission mixed with a Transparent BSDF by ``tex.alpha × opacity`` (opacity a
  - `ShadowRig.setup_drivers(self)` — Build the transform + opacity drivers for the active mode, then force one recompile.
  - `ShadowRig.bake(self, start=None, end=None)` — Bake this rig's driven channels to keyframes and remove the drivers (FBX-ready).
  - `ShadowRig.find_shadow_planes(cls, objects=None)` *(class)* — Shadow planes = objects carrying the stamped ``basePlaneSize`` custom prop.
  - `ShadowRig.bake_planes(cls, planes=None, start=None, end=None)` *(class)* — Bake shadow planes' driven channels to keyframes and remove the drivers so the
  - `ShadowRig.refresh_export_metadata(cls)` *(class)* — Republish the ``shadow_metadata`` channel on the ``data_export`` carrier
  - `ShadowRig.create(cls, targets, light_pos=(5.0, 5.0, 10.0), texture_res=512, axis='auto', source_name='shadow_source', recursive=True, mode='stretch', ground_height=0.0)` *(class)* — Build a projected-shadow rig for ``targets`` (mirror of mayatk's ``ShadowRig.create``).
- **[`class ShadowRigSlots(ptk.LoggingMixin)`](blendertk/blendertk/rig_utils/shadow_rig.py#L707)** — Switchboard slot wiring for the Shadow Rig panel.
  - `ShadowRigSlots.header_init(self, widget)` — Configure header help text.
  - `ShadowRigSlots.b001(self)` — Reset to Defaults — restore all UI widgets to their default values.
  - `ShadowRigSlots.b002(self)` — Bake to Keyframes: bake selected (or all) shadow planes' drivers to keys over the
  - `ShadowRigSlots.perform_operation(self, objects)` — Build the shadow rig for the selected target(s).

<a id="rig_utils--telescope_rig"></a>
### `rig_utils/telescope_rig.py`

Telescope Rig — engine + Switchboard slot wiring for the co-located ``telescope_rig.ui``.

- **[`class TelescopeRig(ptk.LoggingMixin)`](blendertk/blendertk/rig_utils/telescope_rig.py#L34)** — Constraint + driver telescoping-segment rig (mirror of mayatk's ``TelescopeRig``).
  - `TelescopeRig.setup_telescope_rig(self, base_locator, end_locator, segments, collapsed_distance=1.0)` — Wire a telescoping rig between two handles.
- **[`class TelescopeRigSlots(ptk.LoggingMixin)`](blendertk/blendertk/rig_utils/telescope_rig.py#L140)** — Switchboard slot wiring for the Telescope Rig panel.
  - `TelescopeRigSlots.header_init(self, widget)` — Configure header help text.
  - `TelescopeRigSlots.build_rig(self)`

<a id="rig_utils--tube_path"></a>
### `rig_utils/tube_path.py`

Tube-mesh centerline extraction — Blender port of mayatk's ``rig_utils.tube_rig.TubePath``.

- **[`class TubePath`](blendertk/blendertk/rig_utils/tube_path.py#L22)** — Extract centerline paths from tube meshes (static helpers;
  - `TubePath.get_centerline(mesh, num_joints=10, precision=None, edges=None)` *(static)* — Unified centerline dispatcher — mirror of mayatk's ``TubePath.get_centerline``.
  - `TubePath.get_selected_edges(mesh)` *(static)* — The mesh's selected EDIT-mode edges — mirror of mayatk's optional
  - `TubePath.get_centerline_using_edges(mesh, edges)` *(static)* — Centerline from an explicit edge selection — mirror of mayatk's

<a id="rig_utils--tube_rig"></a>
### `rig_utils/tube_rig.py`

Tube Rig — Blender port of mayatk's ``rig_utils.tube_rig`` (the engine + strategies + panel).

- [`register_strategy(cls)`](blendertk/blendertk/rig_utils/tube_rig.py#L205) — Register a custom :class:`TubeStrategy` subclass (keyed by ``cls.name``) — the extension point
- **[`class TubeRigBundle`](blendertk/blendertk/rig_utils/tube_rig.py#L59)** — Result of a strategy build — mirror of mayatk's ``TubeRigBundle``.
- **[`class TubeStrategy(ABC)`](blendertk/blendertk/rig_utils/tube_rig.py#L84)** — Base tube-rig strategy.
  - `TubeStrategy.defaults(self) -> dict`
  - `TubeStrategy.resolve(self, opts: Optional[dict]) -> dict` — Merge caller *opts* over the declared defaults (``None`` values fall back to default).
  - `TubeStrategy.build(self, rig: 'TubeRig', **opts) -> TubeRigBundle`
- **[`class SplineIKStrategy(TubeStrategy)`](blendertk/blendertk/rig_utils/tube_rig.py#L107)**
  - `SplineIKStrategy.build(self, rig, **opts)`
- **[`class AnchorStrategy(TubeStrategy)`](blendertk/blendertk/rig_utils/tube_rig.py#L146)**
  - `AnchorStrategy.build(self, rig, **opts)`
- **[`class FKChainStrategy(TubeStrategy)`](blendertk/blendertk/rig_utils/tube_rig.py#L175)**
  - `FKChainStrategy.build(self, rig, **opts)`
- **[`class TubeRig(ptk.LoggingMixin)`](blendertk/blendertk/rig_utils/tube_rig.py#L217)** — Rig a tube mesh via a named strategy — Blender mirror of mayatk's ``TubeRig``.
  - `TubeRig.collection(self)` *(property)*
  - `TubeRig.resolve_centerline(self, num_joints, precision=None, edges=None)` — The tube's centerline (world points) for *num_joints*, raising if the mesh isn't a
  - `TubeRig.create_root(self)`
  - `TubeRig.create_armature(self, centerline, radius=None)` — Armature + bone chain along *centerline*, parented under the rig root.
  - `TubeRig.create_joint_chain(self, centerline, radius=1.0, reverse=False)` — Bones-only build step — mirror of mayatk's ``generate_joint_chain`` + lazy
  - `TubeRig.add_twist(self, armature, bones, radius=1.0)` — Progressive roll twist for a Spline-IK chain — Blender's Spline IK ignores the driver
  - `TubeRig.attach_spline_rig(self, armature, bones, num_controls=3, radius=1.0, enable_stretch=True, enable_squash=False, enable_volume=False, enable_auto_bend=False, enable_twist=False)` — Curve + Spline IK + hooked controls on an EXISTING bone chain — mirror of mayatk's
  - `TubeRig.build_curve(self, points, count)` — A low-res NURBS driver curve (``count`` control points resampled along *points*) for the
  - `TubeRig.make_control(self, shape, name, size, location, root, color=(1, 1, 0), axis='x')` — Create a control curve at *location*, parented under *root* (keeping its world pos).
  - `TubeRig.hook_curve_controls(self, curve, radius, root)` — One control per curve control-point, each Hook-bound to its point (the live-reshape
  - `TubeRig.constrain_end_with_falloff(self, armature, bones, anchor, mesh, falloff=5.0, bone_index=-1, control=None)` — Constrain one end of a BOUND tube rig to an external *anchor* object with a distance-falloff
  - `TubeRig.build(self, strategy='spline', **opts) -> TubeRigBundle` — Build the rig with the named *strategy* (``"spline"`` / ``"anchor"`` / ``"fk"`` or a
- **[`class TubeRigSlots(ptk.LoggingMixin)`](blendertk/blendertk/rig_utils/tube_rig.py#L547)** — Switchboard slot wiring for the co-located ``tube_rig.ui`` — the **HYBRID** panel.
  - `TubeRigSlots.header_init(self, widget)` — Configure header help text.
  - `TubeRigSlots.b000(self)` — Build Rig — run the selected strategy on the selected tube mesh.
  - `TubeRigSlots.b001(self)` — Step 1 — create the joint/bone chain from the selected tube mesh's centerline (no controls
  - `TubeRigSlots.b002(self)` — Step 2 — add the curve + Spline IK + hooked controls onto the selected armature's EXISTING
  - `TubeRigSlots.b003(self)` — Step 3 — bind the selected tube mesh to the selected armature (Armature modifier + automatic
  - `TubeRigSlots.b004(self)` — Utility — Constrain Both Ends to Anchors: select the rig's armature and TWO anchor objects,

<a id="rig_utils--wheel_rig"></a>
### `rig_utils/wheel_rig.py`

Wheel Rig — engine + Switchboard slot wiring for the co-located ``wheel_rig.ui``.

- **[`class WheelRig(ptk.LoggingMixin)`](blendertk/blendertk/rig_utils/wheel_rig.py#L29)** — Handles basic wheel rigging by linking rotation to linear control movement.
  - `WheelRig.rig_name(self) -> str` *(property)*
  - `WheelRig.get_drivers(self)` — Return every rotation driver fcurve currently attached to this rig's wheels.
  - `WheelRig.delete_drivers(self) -> None` — Remove this rig's rotation drivers from its wheels.
  - `WheelRig.rig_rotation(self, movement_axis: str = 'LOC_Z', rotation_index: int = None, wheel_height: float = 1.0, wheels: list = None, use_world_space: bool = False) -> list` — Rig wheels to rotate based on control movement.
- **[`class WheelRigSlots(ptk.LoggingMixin)`](blendertk/blendertk/rig_utils/wheel_rig.py#L233)** — Switchboard slot wiring for the Wheel Rig panel.
  - `WheelRigSlots.header_init(self, widget)` — Configure header menu with mode toggle and instructions.
  - `WheelRigSlots.rig_name(self) -> str` *(property)* — Get the rig name from the text box.
  - `WheelRigSlots.movement_axis(self) -> str` *(property)* — Get the control travel channel from the axis combo box.
  - `WheelRigSlots.rotation_axis(self) -> int` *(property)* — Get the wheel ``rotation_euler`` index that corresponds to the selected movement axis.
  - `WheelRigSlots.resolve_selection(self)` — Resolve the current selection into control (driver) and wheels.
  - `WheelRigSlots.set_wheel_height(self)` — Get the wheel height from the selected object's bounding box.
  - `WheelRigSlots.s000_init(self, widget)` — Initialize the wheel height field's option-box menu.
  - `WheelRigSlots.update_rig_name_placeholder(self)` — Update the rig name placeholder based on the driver (active object).
  - `WheelRigSlots.cleanup(self)` — Unsubscribe from the centralized ScriptJobManager.
  - `WheelRigSlots.wheel_rig(self)` *(property)* — Get or create the wheel rig attached to the selected control.
  - `WheelRigSlots.b000(self)` — Create or update Wheel Rig.

<a id="ui_utils--_ui_utils"></a>
### `ui_utils/_ui_utils.py`

UI utilities — opening Blender editors (the analogue of Maya's editor-window mel commands).

- [`get_editor_types()`](blendertk/blendertk/ui_utils/_ui_utils.py#L41) — The friendly-name → ``Area.ui_type`` map understood by :func:`open_editor`.
- [`open_editor(editor, properties_context=None)`](blendertk/blendertk/ui_utils/_ui_utils.py#L66) — Open ``editor`` (a friendly name from :data:`EDITOR_TYPES` or a raw ``ui_type``)
- [`main_window()`](blendertk/blendertk/ui_utils/_ui_utils.py#L125) — The main Blender window (the first;
- [`find_editor(editor, window=None)`](blendertk/blendertk/ui_utils/_ui_utils.py#L138) — Open areas showing ``editor`` (friendly name or raw ``ui_type``) as ``(window, area)`` pairs.
- [`close_area(window, area)`](blendertk/blendertk/ui_utils/_ui_utils.py#L153) — Close exactly ``area`` in ``window`` via ``screen.area_close``;
- [`close_editor(editor, window=None)`](blendertk/blendertk/ui_utils/_ui_utils.py#L178) — Close every open area showing ``editor`` in the (main) window;
- [`dock_editor(editor, edge_size=70, window=None)`](blendertk/blendertk/ui_utils/_ui_utils.py#L198) — Dock ``editor`` as a strip along the bottom of the main 3D viewport — the Blender analogue
- [`toggle_editor(editor, edge_size=70, window=None)`](blendertk/blendertk/ui_utils/_ui_utils.py#L242) — Maya-style *docked* toggle for ``editor`` (backs the editors-menu Time & Range button).
- [`toggle_fullscreen_area(editor=None, hide_panels=True, window=None)`](blendertk/blendertk/ui_utils/_ui_utils.py#L343) — Toggle fullscreen-area mode — one editor fills the window (Ctrl+Alt+Space).
- [`toggle_window_bars(visible=None, window=None)`](blendertk/blendertk/ui_utils/_ui_utils.py#L393) — Show/hide the main window's topbar (File/Edit/Render… menus + workspace tabs) and
- [`menu_exists(menu_idname)`](blendertk/blendertk/ui_utils/_ui_utils.py#L461) — True if ``menu_idname`` (e.g.
- [`dispatch_log_link(url, logger=None) -> bool`](blendertk/blendertk/ui_utils/_ui_utils.py#L472) — Handle ``action://`` links emitted by ``logger.log_link()`` in a QTextBrowser.
- [`call_native_menu(menu_idname)`](blendertk/blendertk/ui_utils/_ui_utils.py#L556) — Pop Blender's own native menu ``menu_idname`` (e.g.
- [`popup_message(text, title='Info', icon='INFO')`](blendertk/blendertk/ui_utils/_ui_utils.py#L582) — Show a small native Blender info popup at the cursor (multi-line ``text`` supported).
- **[`class UiUtils`](blendertk/blendertk/ui_utils/_ui_utils.py#L612)** — Namespace mirror (helpers also exposed module-level).

<a id="ui_utils--blender_bridge_slots"></a>
### `ui_utils/blender_bridge_slots.py`

Blender-flavored :class:`BridgeSlotsBase` -- adds Blender-side defaults.

- **[`class BlenderBridgeSlotsBase(BridgeSlotsBase)`](blendertk/blendertk/ui_utils/blender_bridge_slots.py#L24)** — Adds a Blender-flavored ``default_output_dir`` to :class:`BridgeSlotsBase`.
  - `BlenderBridgeSlotsBase.default_output_dir(self) -> str` — The saved ``.blend`` file's directory, or ``""`` if unsaved.

<a id="ui_utils--blender_native_menus"></a>
### `ui_utils/blender_native_menus.py`

Symbolic-name -> Blender native-menu resolution + Qt wrapping for the both-button chord menu.

- **[`class BlenderNativeMenus(ptk.LoggingMixin)`](blendertk/blendertk/ui_utils/blender_native_menus.py#L21)** — Resolve the chord menu's symbolic node names to Blender ``*_MT_*`` menu idnames.
  - `BlenderNativeMenus.names(cls)` *(class)* — Every symbolic node name this handler resolves (mapping + mode-adaptive keys).
  - `BlenderNativeMenus.resolve(cls, name)` *(class)* — The Blender menu idname for symbolic ``name`` (mode-aware for Select / Rig), or
  - `BlenderNativeMenus.get_menu(self, name)` — Build (or refresh) the Qt clone of native menu ``name``;

<a id="ui_utils--blender_ui_handler"></a>
### `ui_utils/blender_ui_handler.py`

- **[`class BlenderUiHandler(UiHandler)`](blendertk/blendertk/ui_utils/blender_ui_handler.py#L10)** — UI Handler for Blender applications.
  - `BlenderUiHandler.instance(cls, switchboard: Switchboard = None, **kwargs) -> 'BlenderUiHandler'` *(class)* — Return the BlenderUiHandler singleton, bootstrapping if needed.
  - `BlenderUiHandler.can_resolve(self, name: str) -> bool` — Recognise the native Blender menus this handler wraps on demand.
  - `BlenderUiHandler.show(self, ui, pos=None, force: bool = False, **kwargs)` — Swap a native-menu proxy for its wrapped, freshly-harvested menu window;
  - `BlenderUiHandler.apply_styles(self, ui, style=None)` — Give blendertk-sourced tool panels a hide button instead of a pin.

<a id="ui_utils--blender_window"></a>
### `ui_utils/blender_window.py`

Native-window (win32/GHOST) helpers for hosting Qt widgets around a Blender window.

- **[`class BlenderWindow`](blendertk/blendertk/ui_utils/blender_window.py#L33)** — Static win32 helpers for GHOST-window enumeration, geometry, embedding, ownership.
  - `BlenderWindow.process_ghost_hwnds(cls)` *(class)* — List of visible GHOST-window HWNDs owned by THIS process (``[]`` off-Windows).
  - `BlenderWindow.window_hwnd(cls, bpy_window)` *(class)* — The GHOST hwnd of a SPECIFIC already-open ``bpy.types.Window``, or None.
  - `BlenderWindow.is_window(cls, hwnd) -> bool` *(class)*
  - `BlenderWindow.client_origin(cls, hwnd)` *(class)* — Screen (x, y) of the window's client-area top-left, or None.
  - `BlenderWindow.client_size(cls, hwnd)` *(class)* — (width, height) of the window's client area in physical pixels, or None.
  - `BlenderWindow.region_client_rect(cls, hwnd, region)` *(class)* — Map a bpy ``region`` inside GHOST window ``hwnd`` to a parent-CLIENT rect.
  - `BlenderWindow.set_clip_children(cls, hwnd) -> bool` *(class)* — Set ``WS_CLIPCHILDREN`` on ``hwnd`` (idempotent);
  - `BlenderWindow.move_child(cls, hwnd, rect) -> bool` *(class)* — Place child window ``hwnd`` at ``rect`` = (x, y, w, h) in its parent's client
  - `BlenderWindow.keyboard_focus(cls)` *(class)* — ``GetFocus()`` — the hwnd receiving this thread's keystrokes, or 0/None.
  - `BlenderWindow.cursor_over(cls, hwnd) -> bool` *(class)* — True when the pointer is over ``hwnd`` (or one of its child windows).
  - `BlenderWindow.set_keyboard_focus(cls, hwnd) -> bool` *(class)* — ``SetFocus(hwnd)`` — route this thread's keystrokes to ``hwnd``;
  - `BlenderWindow.set_owner(cls, widget, owner_hwnd)` *(class)* — Make Qt ``widget`` an *owned* window of ``owner_hwnd`` (``GWLP_HWNDPARENT``).

<a id="ui_utils--calculator"></a>
### `ui_utils/calculator.py`

Calculator tool panel — Switchboard slot wiring for the co-located ``calculator.ui``.

- **[`class CalculatorController`](blendertk/blendertk/ui_utils/calculator.py#L24)** — DCC-agnostic math engine + Blender time helpers.
  - `CalculatorController.calculate(expression)` *(static)* — Safely evaluate a math expression (delegates to the shared engine).
  - `CalculatorController.convert_unit(value, from_unit, to_unit)` *(static)* — Convert a length value between units (delegates to the shared engine).
  - `CalculatorController.get_fps_value()` *(static)* — Scene frame rate (falls back to 24.0).
  - `CalculatorController.get_current_time()` *(static)* — Current frame as a string.
  - `CalculatorController.frames_to_sec(cls, frames)` *(class)*
  - `CalculatorController.sec_to_frames(cls, seconds)` *(class)*
- **[`class CalculatorSlots(ptk.LoggingMixin)`](blendertk/blendertk/ui_utils/calculator.py#L78)** — Switchboard slot wiring for the Calculator panel.
  - `CalculatorSlots.header_init(self, widget)` — Configure header help text.
  - `CalculatorSlots.on_input(self, text)`
  - `CalculatorSlots.on_clear(self)`
  - `CalculatorSlots.on_backspace(self)`
  - `CalculatorSlots.on_equal(self)`
  - `CalculatorSlots.on_convert_units(self)`
  - `CalculatorSlots.get_fps(self)`
  - `CalculatorSlots.get_current_time(self)`
  - `CalculatorSlots.frames_to_sec(self)`
  - `CalculatorSlots.sec_to_frames(self)`

<a id="ui_utils--menu_harvest"></a>
### `ui_utils/menu_harvest.py`

Harvest a native Blender menu into a live ``QMenu`` — the Blender half of Maya's wrap.

- [`harvest_menu(idname)`](blendertk/blendertk/ui_utils/menu_harvest.py#L173) — Execute ``idname``'s ``draw`` against a recorder;
- [`invoke_operator(op_idname, props=None)`](blendertk/blendertk/ui_utils/menu_harvest.py#L214) — Run an operator one timer tick later, ``INVOKE_DEFAULT``, under a VIEW_3D override.
- [`refill_qmenu(qmenu, idname)`](blendertk/blendertk/ui_utils/menu_harvest.py#L257) — Rebuild ``qmenu``'s actions from a fresh harvest of ``idname``;

<a id="ui_utils--qt_dock"></a>
### `ui_utils/qt_dock.py`

Dock any Qt widget into a native Blender area — a true child window, not an overlay.

- **[`class QtDock`](blendertk/blendertk/ui_utils/qt_dock.py#L56)** — Host a Qt widget as the body of a true docked Blender area.
  - `QtDock.supported(cls) -> bool` *(class)* — True when embedding can work here: Windows + a live QApplication.
  - `QtDock.docked(self) -> bool` *(property)* — True while the widget is embedded over a LIVE docked area — a user closing/
  - `QtDock.widget(self)` *(property)* — The hosted widget (or None) — for callers/tests.
  - `QtDock.area(self)` *(property)* — Our docked ``bpy.types.Area`` (or None).
  - `QtDock.content_region(self)` — The WINDOW (content) region of our area, or None when the area is gone —
  - `QtDock.dock(self, widget, height: Optional[int] = None) -> bool` — Dock ``widget``: create/dock the placeholder area, embed the widget as a
  - `QtDock.undock(self) -> None` — Release the widget (un-parent to a hidden top-level, ready to re-dock) and
  - `QtDock.teardown(self) -> None` — Full uninstall for a host reload: :meth:`undock` + drop the widget reference.

<a id="ui_utils--style_setter--_style_setter"></a>
### `ui_utils/style_setter/_style_setter.py`

Match Blender's app UI chrome to another DCC's look using Blender's NATIVE theme-preset system.

- [`list_styles()`](blendertk/blendertk/ui_utils/style_setter/_style_setter.py#L56) — Names of the shipped theme presets (e.g.
- [`user_preset_dir(create=False)`](blendertk/blendertk/ui_utils/style_setter/_style_setter.py#L66) — Blender's per-user ``presets/interface_theme`` dir — the dropdown's writable source.
- [`user_preset_path(name)`](blendertk/blendertk/ui_utils/style_setter/_style_setter.py#L73) — Path a preset named ``name`` would have in the user preset dir.
- [`is_installed(name)`](blendertk/blendertk/ui_utils/style_setter/_style_setter.py#L78) — True if ``<name>.xml`` is in the user preset dir (i.e.
- [`install(overwrite=False)`](blendertk/blendertk/ui_utils/style_setter/_style_setter.py#L83) — Copy the shipped theme presets into Blender's user preset dir so they appear in
- [`list_templates()`](blendertk/blendertk/ui_utils/style_setter/_style_setter.py#L99) — Ordered ``{display_name: filepath}`` of every native ``interface_theme`` preset the Themes
- [`apply_template(filepath)`](blendertk/blendertk/ui_utils/style_setter/_style_setter.py#L118) — Apply any native theme preset by its ``.xml`` filepath (the token from :func:`list_templates`)
- [`apply_theme_preset(name)`](blendertk/blendertk/ui_utils/style_setter/_style_setter.py#L128) — Apply a shipped/installed theme preset by NAME (not path).
- [`set_style(name, install_presets=True, persist=False)`](blendertk/blendertk/ui_utils/style_setter/_style_setter.py#L148) — Switch Blender's UI to the named shipped style (e.g.
- **[`class StyleSetter`](blendertk/blendertk/ui_utils/style_setter/_style_setter.py#L171)** — Public namespace for the style-setter helpers (``btk.StyleSetter.set_style("Maya")`` …).

<a id="uv_utils--_uv_utils"></a>
### `uv_utils/_uv_utils.py`

UV utilities — UV-coordinate translation and UV-set cleanup (mirror of mayatk's ``UvUtils``

- [`move_uvs(objects, du=0.0, dv=0.0)`](blendertk/blendertk/uv_utils/_uv_utils.py#L47) — Translate the UVs of the given mesh object(s) by ``(du, dv)`` — "move to UV space"
- [`transform_uvs(objects, flip_u=False, flip_v=False, angle=0.0, per_shell=False)`](blendertk/blendertk/uv_utils/_uv_utils.py#L92) — Flip and/or rotate (``angle`` degrees, CCW) the UVs of the given mesh object(s).
- [`mirror_uvs(objects, axis='u', per_shell=True, preserve_position=True)`](blendertk/blendertk/uv_utils/_uv_utils.py#L146) — Mirror UVs across U or V — mirror of ``mtk.UvUtils.mirror_uvs``.
- [`pin_uvs(objects, pin=True, selected_only=True)`](blendertk/blendertk/uv_utils/_uv_utils.py#L254) — Pin/unpin UVs (bmesh ``pin_uv``).
- [`get_texel_density(objects, map_size)`](blendertk/blendertk/uv_utils/_uv_utils.py#L298) — Texel density (px per scene unit) of the meshes' faces against a ``map_size`` map —
- [`set_texel_density(objects, density=1.0, map_size=4096)`](blendertk/blendertk/uv_utils/_uv_utils.py#L322) — Scale each object's UVs (about its own UV bbox center) to the target texel density —
- [`delete_extra_uv_sets(objects)`](blendertk/blendertk/uv_utils/_uv_utils.py#L346) — Remove all but the first UV map on the given mesh object(s) — "Cleanup UV Sets".
- [`cleanup_uv_sets(objects, *, remove_empty=True, keep_only_primary=False, rename_to_map1=True, force_rename=False, prefer_largest_area=True, dry_run=False)`](blendertk/blendertk/uv_utils/_uv_utils.py#L386) — Standardize / clean up the UV sets (``uv_layers``) of the given mesh object(s).
- [`find_lightmap_uv_set(obj)`](blendertk/blendertk/uv_utils/_uv_utils.py#L490) — Name of *obj*'s existing lightmap UV layer, or ``None`` (mirror of
- [`create_lightmap_uvs(objects, uv_set=LIGHTMAP_UV_SET, margin=0.02, quiet=True)`](blendertk/blendertk/uv_utils/_uv_utils.py#L511) — Ensure each mesh has a packed, non-overlapping lightmap UV layer (UV2).
- [`get_uv_coords(objects)`](blendertk/blendertk/uv_utils/_uv_utils.py#L674) — Snapshot the active-layer UV coordinates per object (``{name: [(u, v), …]}`` in
- [`set_uv_coords(objects, snapshot)`](blendertk/blendertk/uv_utils/_uv_utils.py#L696) — Restore a :func:`get_uv_coords` snapshot (objects whose topology changed since the
- [`stack_uv_shells(objects, tolerance=None)`](blendertk/blendertk/uv_utils/_uv_utils.py#L720) — Stack UV islands on top of each other.
- [`straighten_uv_shells(objects, mode='LENGTH_AVERAGE')`](blendertk/blendertk/uv_utils/_uv_utils.py#L781) — Rectangularize the targeted UV shell(s) — mirror of Maya's ``texStraightenShell`` — via
- [`derive_auto_seams(objects, angle=66.0, margin=0.0)`](blendertk/blendertk/uv_utils/_uv_utils.py#L842) — Auto-detect UV seams via a temporary Smart UV Project pass — mirror of Maya's
- [`distribute_uv_shells(objects, axis='u')`](blendertk/blendertk/uv_utils/_uv_utils.py#L883) — Distribute UV islands evenly along ``axis`` (``"u"`` or ``"v"``) — the first and
- [`straighten_uvs(objects, u=True, v=True, angle=30.0)`](blendertk/blendertk/uv_utils/_uv_utils.py#L918) — Straighten the selected UV edges — edges within ``angle`` degrees of horizontal
- [`align_uvs(objects, axis='u', mode='avg')`](blendertk/blendertk/uv_utils/_uv_utils.py#L1000) — Align the selected UVs — mirror of Maya's ``performAlignUV`` (min/avg/max) and
- [`gather_uv_shells(objects)`](blendertk/blendertk/uv_utils/_uv_utils.py#L1050) — Gather the targeted UV shells back into the 0-1 tile — mirror of Maya's ``UVGatherShells``:
- [`orient_uv_shells(objects, to_edge=False)`](blendertk/blendertk/uv_utils/_uv_utils.py#L1076) — Orient the selected UV shells — mirror of Maya's ``texOrientShells`` /
- [`randomize_uv_shells(objects, seed=0)`](blendertk/blendertk/uv_utils/_uv_utils.py#L1093) — Randomly offset the selected UV shells — mirror of Maya's ``RandomizeShells`` (a per-shell
- **[`class UvUtils`](blendertk/blendertk/uv_utils/_uv_utils.py#L1110)** — Namespace mirror of mayatk's ``UvUtils`` (helpers also exposed module-level).

<a id="uv_utils--rizom_bridge--_rizom_bridge"></a>
### `uv_utils/rizom_bridge/_rizom_bridge.py`

RizomUV bridge engine — Blender mirror of mayatk's ``RizomUVBridge``.

- **[`class RizomUVBridge(ptk.LoggingMixin)`](blendertk/blendertk/uv_utils/rizom_bridge/_rizom_bridge.py#L93)** — Engine: discover the RizomUV exe, export the selection, run RizomUV (send or round-trip).
  - `RizomUVBridge.rizom_path(self)` *(property)* — Resolved RizomUV executable path (cached), or None.
  - `RizomUVBridge.rizom_version(self) -> 'tuple[int, ...]'` *(property)* — The installed Rizom version, parsed from the install-dir name (mirror of mayatk).
  - `RizomUVBridge.export_path(self)` *(property)* — Lazy temp FBX path for the round-trip (POSIX string).
  - `RizomUVBridge.script_path(self)` *(property)* — The prepared Lua script file path as a POSIX string.
  - `RizomUVBridge.build_send_script(self, fbx_path, objects=None, load_uvs=True, import_groups=True, load_uvw_props=True, load_textures=True)` — Render the RizomUV Lua load-script (``ZomLoad`` + optional ``ZomLoadTexture`` block).
  - `RizomUVBridge.send(self, objects, load_uvs=True, import_groups=True, load_uvw_props=True, load_textures=True)` — Export ``objects`` to FBX and open them in a fresh RizomUV session (one-way).
  - `RizomUVBridge.process_with_rizomuv(self, objects, uv_script=None, preset=None, params=None)` — Run the full export -> RizomUV -> re-import -> transfer-UVs-back workflow.

<a id="uv_utils--rizom_bridge--parameters"></a>
### `uv_utils/rizom_bridge/parameters.py`

Registry of user-tunable RizomUV parameters exposed to the bridge UI.

- [`referenced_keys(script_text: str) -> 'set[str]'`](blendertk/blendertk/uv_utils/rizom_bridge/parameters.py#L327) — Registered keys present in *script_text* (delegates to uitk.bridge).
- [`defaults() -> 'dict[str, Any]'`](blendertk/blendertk/uv_utils/rizom_bridge/parameters.py#L332) — Return ``{key: default}`` for every registered parameter.
- [`render_context(values: 'dict[str, Any]') -> 'dict[str, str]'`](blendertk/blendertk/uv_utils/rizom_bridge/parameters.py#L337) — Format *values* for placeholder substitution using Lua literals.
- [`strip_unsupported(script_text: str, version: 'tuple[int, ...]') -> str`](blendertk/blendertk/uv_utils/rizom_bridge/parameters.py#L382) — Drop every line that references a placeholder requiring a newer Rizom.

<a id="uv_utils--rizom_bridge--rizom_bridge_slots"></a>
### `uv_utils/rizom_bridge/rizom_bridge_slots.py`

Slots for the RizomUV bridge panel.

- **[`class RizomBridgeSlots(BridgeSlotsBase)`](blendertk/blendertk/uv_utils/rizom_bridge/rizom_bridge_slots.py#L57)** — Slots wired to ``rizom_bridge.ui`` via :class:`BridgeSlotsBase`.
  - `RizomBridgeSlots.params_module(self)` *(property)*
  - `RizomBridgeSlots.template_dir(self) -> Path` *(property)*
  - `RizomBridgeSlots.make_bridge(self) -> RizomUVBridge`
  - `RizomBridgeSlots.list_template_modes(self)` — Return ``[(stem, ""), ...]`` for every bundled ``.lua`` script.
  - `RizomBridgeSlots.b000(self)` — Run the chosen preset: round-trip, or one-way send when ``send`` is picked.
  - `RizomBridgeSlots.open_uv_editor(self)` — Open Blender's UV Editor in a new window.

<a id="uv_utils--shell_xform"></a>
### `uv_utils/shell_xform.py`

Dedicated UV shell-transform panel (Blender).

- **[`class ShellXformSlots(ptk.LoggingMixin)`](blendertk/blendertk/uv_utils/shell_xform.py#L28)** — Switchboard slots for the Shell Xform panel (``shell_xform.ui``).
  - `ShellXformSlots.header_init(self, widget)` — Header menu — Open UV Editor + panel help.
  - `ShellXformSlots.b023(self)` — Move To UV Space: Left
  - `ShellXformSlots.b024(self)` — Move To UV Space: Down
  - `ShellXformSlots.b025(self)` — Move To UV Space: Up
  - `ShellXformSlots.b026(self)` — Move To UV Space: Right
  - `ShellXformSlots.b034(self)` — Flip U: mirror the selection's UV maps horizontally about their bbox center.
  - `ShellXformSlots.b035(self)` — Flip V: mirror the selection's UV maps vertically about their bbox center.
  - `ShellXformSlots.b036(self)` — Rotate the selection's UV maps counter-clockwise by the s041 angle.
  - `ShellXformSlots.b037(self)` — Rotate the selection's UV maps clockwise by the s041 angle.
  - `ShellXformSlots.s041(self, value, widget)` — Rotate Angle — passive input; read by the Rotate buttons (b036/b037).
  - `ShellXformSlots.tb005_init(self, widget)`
  - `ShellXformSlots.tb005(self, widget)` — Straighten UV (selected UV edges within the angle threshold snap flat;
  - `ShellXformSlots.tb006_init(self, widget)`
  - `ShellXformSlots.tb006(self, widget)` — Distribute (space the targeted UV shells evenly along U or V).
  - `ShellXformSlots.tb008_init(self, widget)`
  - `ShellXformSlots.tb008(self, widget)` — Mirror UVs (footprint-preserving reassignment by default;
  - `ShellXformSlots.align_u_min(self)` — Align the selected UVs to their minimum U (left).
  - `ShellXformSlots.align_u_avg(self)` — Align the selected UVs to their average U (center).
  - `ShellXformSlots.align_u_max(self)` — Align the selected UVs to their maximum U (right).
  - `ShellXformSlots.align_v_min(self)` — Align the selected UVs to their minimum V (bottom).
  - `ShellXformSlots.align_v_avg(self)` — Align the selected UVs to their average V (center).
  - `ShellXformSlots.align_v_max(self)` — Align the selected UVs to their maximum V (top).
  - `ShellXformSlots.linear_align(self)` — Linearly align the selected UVs between their two end points.
  - `ShellXformSlots.orient_shells(self)` — Orient each shell to run parallel with its nearest U/V axis (Align Rotation).
  - `ShellXformSlots.orient_edges(self)` — Orient the shell so its selected edge runs along U or V.
  - `ShellXformSlots.gather_shells(self)` — Gather the selected shells together toward the 0-1 UV space.
  - `ShellXformSlots.randomize_shells(self)` — Randomly offset the selected shells.
  - `ShellXformSlots.open_uv_editor(self)` — Open Blender's UV Editor.

<a id="xform_utils--_xform_utils"></a>
### `xform_utils/_xform_utils.py`

Transform utilities — object-level transform ops (world bbox, freeze, drop-to-grid,

- [`get_world_bbox(obj)`](blendertk/blendertk/xform_utils/_xform_utils.py#L17) — Return ``(min, max)`` ``Vector``s of ``obj``'s bounding box in world space.
- [`freeze_transforms(objects, location=True, rotation=False, scale=True, store=True)`](blendertk/blendertk/xform_utils/_xform_utils.py#L70) — Apply (bake) the given transform channels into the object data — Blender's
- [`restore_transforms(objects, delete_attrs=True)`](blendertk/blendertk/xform_utils/_xform_utils.py#L91) — Un-freeze: compose the stored pre-freeze channels back into the local transform
- [`has_stored_transforms(objects)`](blendertk/blendertk/xform_utils/_xform_utils.py#L126) — Map each object → whether it carries pre-freeze bake data (mirror of
- [`scale_connected_edges(objects, scale_factor=1.1)`](blendertk/blendertk/xform_utils/_xform_utils.py#L160) — Scale each CONNECTED set of selected edges about that set's own centroid — mirror
- [`drop_to_grid(objects, align='Min', origin=False, center_pivot=False)`](blendertk/blendertk/xform_utils/_xform_utils.py#L197) — Drop each object so its bbox ``Min`` / ``Mid`` / ``Max`` sits on the ground (Z=0).
- [`center_pivot(objects, mode='object')`](blendertk/blendertk/xform_utils/_xform_utils.py#L225) — Move each object's origin (Blender's single pivot) — mirror of Maya's Center Pivot.
- [`transfer_pivot(objects, translate=True, rotate=False, scale=False, world_space=True, select_targets_after_transfer=False)`](blendertk/blendertk/xform_utils/_xform_utils.py#L260) — Transfer the object **origin** from the first object to the rest — mirror of Maya's
- [`get_pivot_modes()`](blendertk/blendertk/xform_utils/_xform_utils.py#L312) — Center-pivot mode keys understood by :func:`center_pivot`.
- [`match_scale(source, target, average=True)`](blendertk/blendertk/xform_utils/_xform_utils.py#L317) — Uniformly rescale ``source`` object(s) to match ``target``'s bounding-box size.
- [`move_to(source, target, pivot='center')`](blendertk/blendertk/xform_utils/_xform_utils.py#L334) — Move ``source`` object(s) so their pivot aligns with the ``target``'s pivot point.
- [`get_bounding_box(objects, value='', world_space=True)`](blendertk/blendertk/xform_utils/_xform_utils.py#L348) — Combined bounding box of ``objects`` — mirror of ``mtk.get_bounding_box`` (name + behavior).
- [`get_center_point(objects)`](blendertk/blendertk/xform_utils/_xform_utils.py#L376) — Bounding-box center of ``objects`` as an ``(x, y, z)`` tuple (mirror of
- [`get_operation_axis_matrix(obj, pivot)`](blendertk/blendertk/xform_utils/_xform_utils.py#L382) — World pivot frame (orientation + position, scale stripped) for a per-object linear/
- [`get_distance(a, b)`](blendertk/blendertk/xform_utils/_xform_utils.py#L441) — Distance between two points — each an object (world origin), ``Vector``, or 3-sequence
- [`order_by_distance(objects, reference_point=None, reverse=False)`](blendertk/blendertk/xform_utils/_xform_utils.py#L447) — Order ``objects`` by distance from ``reference_point`` (an object / Vector / 3-seq;
- [`aim_object_at_point(objects, target_pos, aim_vect=(1, 0, 0), up_vect=(0, 1, 0))`](blendertk/blendertk/xform_utils/_xform_utils.py#L466) — Aim ``objects`` at a world-space point — mirror of ``mtk.aim_object_at_point`` (which uses
- **[`class XformUtils`](blendertk/blendertk/xform_utils/_xform_utils.py#L487)** — Namespace mirror of mayatk's ``XformUtils`` (helpers also exposed module-level).
  - `XformUtils.get_pivot_options()` *(static)* — Pivot keys understood by :func:`move_to` (mirror of ``mtk.XformUtils.get_pivot_options``).

<a id="xform_utils--matrices"></a>
### `xform_utils/matrices.py`

Matrix utilities — the Blender counterpart of mayatk's ``xform_utils.matrices``

- **[`class Matrices`](blendertk/blendertk/xform_utils/matrices.py#L33)** — Matrix helpers over ``mathutils.Matrix`` (mirror of mayatk's ``Matrices`` pure-math API).
  - `Matrices.get_matrix(obj, space='world')` *(static)* — Return a COPY of *obj*'s matrix in ``space`` (``world`` / ``local`` / ``basis`` /
  - `Matrices.set_matrix(obj, value, space='world')` *(static)* — Set *obj*'s matrix in ``space`` (``world`` / ``local`` / ``basis``) from a
  - `Matrices.local_matrix(obj)` *(static)* — *obj*'s local matrix (mirror of mayatk's ``local_matrix``).
  - `Matrices.to_matrix(matrix_like)` *(static)* — Coerce to a ``mathutils.Matrix`` — accepts a Matrix (copied), a bpy object (its
  - `Matrices.identity()` *(static)* — A 4×4 identity matrix (mirror of mayatk's ``identity``).
  - `Matrices.from_srt(translate=(0.0, 0.0, 0.0), rotate_euler_deg=(0.0, 0.0, 0.0), scale=(1.0, 1.0, 1.0), rotate_order='XYZ')` *(static)* — Compose a matrix from translation, Euler rotation (DEGREES), and scale — mirror of
  - `Matrices.compose(translate=(0.0, 0.0, 0.0), rotation=None, scale=(1.0, 1.0, 1.0))` *(static)* — Compose a matrix from translation, a ``Quaternion`` (or ``Euler`` / 3×3) rotation, and
  - `Matrices.decompose(m, rotate_order='XYZ')` *(static)* — Decompose *m* into ``(translation, rotation_degrees, scale)`` 3-tuples — mirror of
  - `Matrices.extract_translation(m)` *(static)* — The translation component of *m* as an ``(x, y, z)`` tuple (mirror of mayatk).
  - `Matrices.inverse(m)` *(static)* — The inverse of *m* (``inverted_safe`` — returns a usable matrix for singular input,
  - `Matrices.mult(*mats)` *(static)* — Right-to-left matrix product: ``mult(A, B)`` returns ``A @ B`` (apply B, then A) —
  - `Matrices.world_to_local(world_matrix, parent_world_matrix)` *(static)* — World → local relative to a parent: ``local = parent_world⁻¹ @ world`` (Blender order;
  - `Matrices.local_to_world(local_matrix, parent_world_matrix)` *(static)* — Local → world: ``world = parent_world @ local`` (Blender order;
  - `Matrices.is_identity(m, tolerance=1e-09)` *(static)* — True if *m* equals identity within ``tolerance`` per element (mirror of mayatk).
