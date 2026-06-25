# blendertk — API Registry

_Auto-generated. Do not edit by hand. Refresh via `m3trik/scripts/generate_api_registry.py`._

_Generated: 2026-06-25_

## Index

- [`anim_utils/_anim_utils.py`](#anim_utils--_anim_utils) — Animation utilities — key-timing math over ``fcurve.keyframe_points`` (mirror of mayatk's
- [`anim_utils/scale_keys.py`](#anim_utils--scale_keys) — Dedicated scale-keys module to keep AnimUtils lean and testable (mirror of mayatk's
- [`anim_utils/stagger_keys.py`](#anim_utils--stagger_keys) — Dedicated stagger-keys module to keep AnimUtils lean and testable (mirror of mayatk's
- [`cam_utils/_cam_utils.py`](#cam_utils--_cam_utils) — Camera utilities — clip-plane adjustment (mirror of mayatk's ``cam_utils``).
- [`core_utils/_core_utils.py`](#core_utils--_core_utils) — Core blendertk utilities — DCC-environment info + cross-cutting decorators.
- [`core_utils/diagnostics/mesh_diag.py`](#core_utils--diagnostics--mesh_diag) — Mesh diagnostics — the Blender counterpart of mayatk's ``core_utils.diagnostics.mesh_diag``
- [`core_utils/diagnostics/transform_diag.py`](#core_utils--diagnostics--transform_diag) — Transform diagnostics — the Blender counterpart of mayatk's
- [`core_utils/preview.py`](#core_utils--preview) — Live-preview driver for the tentacle Blender tool panels — the Blender analogue of
- [`core_utils/script_job_manager.py`](#core_utils--script_job_manager) — Centralized Blender event-subscription manager — the Blender counterpart of mayatk's
- [`display_utils/_display_utils.py`](#display_utils--_display_utils) — Display utilities — the exploded-view toggle (mirror of mayatk's
- [`display_utils/color_manager.py`](#display_utils--color_manager) — Color Manager tool panel — Switchboard slot wiring for the co-located ``color_manager.ui``.
- [`display_utils/exploded_view.py`](#display_utils--exploded_view) — Exploded View — Switchboard slot wiring for the co-located ``exploded_view.ui``.
- [`edit_utils/_edit_utils.py`](#edit_utils--_edit_utils) — Mesh-editing utilities — reduce/decimate, coplanar dissolve, triangulate / tris-to-quads,
- [`edit_utils/bevel.py`](#edit_utils--bevel) — Bevel tool — engine + Switchboard slot wiring for the co-located ``bevel.ui``.
- [`edit_utils/bridge.py`](#edit_utils--bridge) — Bridge tool — engine + Switchboard slot wiring for the co-located ``bridge.ui``.
- [`edit_utils/curtain.py`](#edit_utils--curtain) — Curtain (draped-cloth) generation — the Blender build over the shared
- [`edit_utils/cut_on_axis.py`](#edit_utils--cut_on_axis) — Cut-On-Axis tool panel — Switchboard slot wiring for the co-located ``cut_on_axis.ui``.
- [`edit_utils/duplicate_grid.py`](#edit_utils--duplicate_grid) — Grid array duplication + its tool panel — mirror of mayatk's ``edit_utils.duplicate_grid``.
- [`edit_utils/duplicate_linear.py`](#edit_utils--duplicate_linear) — Linear array duplication + its tool panel — mirror of mayatk's ``edit_utils.duplicate_linear``.
- [`edit_utils/duplicate_radial.py`](#edit_utils--duplicate_radial) — Radial array duplication + its tool panel — mirror of mayatk's ``edit_utils.duplicate_radial``.
- [`edit_utils/dynamic_pipe.py`](#edit_utils--dynamic_pipe) — Dynamic Pipe tool — Blender port of mayatk's ``edit_utils.dynamic_pipe``.
- [`edit_utils/macros.py`](#edit_utils--macros) — Hotkey macros — the Blender counterpart of ``mayatk.edit_utils.macros``.
- [`edit_utils/mirror.py`](#edit_utils--mirror) — Mirror tool panel — Switchboard slot wiring for the co-located ``mirror.ui``.
- [`edit_utils/naming/_naming.py`](#edit_utils--naming--_naming) — Batch object naming — Blender port of mayatk's ``edit_utils.naming.Naming``.
- [`edit_utils/naming/naming_slots.py`](#edit_utils--naming--naming_slots) — Switchboard slots for the Naming panel — Blender port of mayatk's ``NamingSlots``.
- [`edit_utils/snap.py`](#edit_utils--snap) — Snap tool — Switchboard slot wiring for the co-located ``snap.ui``.
- [`env_utils/_env_utils.py`](#env_utils--_env_utils) — blendertk environment / scene-library utilities — the engine behind the Reference Manager panel.
- [`env_utils/blender_connection.py`](#env_utils--blender_connection) — Launch a FRESH headless Blender to run a script / code string and capture its output — the
- [`env_utils/fbx_utils.py`](#env_utils--fbx_utils) — FBX import / export helpers — the Blender counterpart of mayatk's ``env_utils.fbx_utils``
- [`env_utils/handoff_export.py`](#env_utils--handoff_export) — Blender-side selection + FBX-export hooks shared by the hand-off bridge engines.
- [`env_utils/maya_bridge/_maya_bridge.py`](#env_utils--maya_bridge--_maya_bridge) — Maya bridge engine -- export the Blender selection and run a chosen import template in Maya.
- [`env_utils/maya_bridge/maya_bridge_slots.py`](#env_utils--maya_bridge--maya_bridge_slots) — Slots for the Maya bridge panel.
- [`env_utils/maya_bridge/parameters.py`](#env_utils--maya_bridge--parameters) — Registry of user-tunable Maya-bridge parameters exposed to the panel.
- [`env_utils/maya_bridge/templates/import.py`](#env_utils--maya_bridge--templates--import) — Import the bridged FBX into Maya, with optional clean-slate and frame-on-import behaviors.
- [`env_utils/reference_manager.py`](#env_utils--reference_manager) — Reference Manager tool panel — Switchboard slot wiring for the co-located ``reference_manager.ui``.
- [`light_utils/_light_utils.py`](#light_utils--_light_utils) — Light utilities — the world-environment (HDRI) helpers behind the HDR Manager panel
- [`light_utils/hdr_manager.py`](#light_utils--hdr_manager) — HDR Manager tool panel — Switchboard slot wiring for the co-located ``hdr_manager.ui``.
- [`light_utils/lightmap_baker/lightmap_baker.py`](#light_utils--lightmap_baker--lightmap_baker) — High-level lightmap baking workflow for Blender -> game engines (Unity-first).
- [`mat_utils/_mat_utils.py`](#mat_utils--_mat_utils) — Material utilities — mirror of mayatk's ``MatUtils`` public names where the concepts align:
- [`mat_utils/game_shader.py`](#mat_utils--game_shader) — Game Shader tool panel — auto-build a Principled-BSDF material from a set of PBR textures.
- [`mat_utils/image_to_plane/_image_to_plane.py`](#mat_utils--image_to_plane--_image_to_plane) — Map image files to textured planes in Blender — port of mayatk's ``mat_utils.image_to_plane``.
- [`mat_utils/image_to_plane/image_to_plane_slots.py`](#mat_utils--image_to_plane--image_to_plane_slots) — Switchboard slots for the Image to Plane panel — port of mayatk's ``ImageToPlaneSlots``.
- [`mat_utils/mat_updater.py`](#mat_utils--mat_updater) — Material Updater tool panel — Switchboard slot wiring for the co-located ``mat_updater.ui``.
- [`mat_utils/render_opacity/_render_opacity.py`](#mat_utils--render_opacity--_render_opacity) — Render Opacity — Blender per-object opacity for engine-ready transparency (mirror of mayatk's
- [`mat_utils/render_opacity/render_opacity_slots.py`](#mat_utils--render_opacity--render_opacity_slots) — Switchboard slots for the Render Opacity panel (``render_opacity.ui``).
- [`mat_utils/shader_templates.py`](#mat_utils--shader_templates) — Shader Templates tool panel — Switchboard slot wiring for the co-located
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
- [`rig_utils/tube_rig.py`](#rig_utils--tube_rig) — Tube Rig — Blender port of mayatk's ``rig_utils.tube_rig`` (the engine + strategies).
- [`rig_utils/wheel_rig.py`](#rig_utils--wheel_rig) — Wheel Rig — engine + Switchboard slot wiring for the co-located ``wheel_rig.ui``.
- [`ui_utils/_ui_utils.py`](#ui_utils--_ui_utils) — UI utilities — opening Blender editors (the analogue of Maya's editor-window mel commands).
- [`ui_utils/blender_ui_handler.py`](#ui_utils--blender_ui_handler)
- [`ui_utils/calculator.py`](#ui_utils--calculator) — Calculator tool panel — Switchboard slot wiring for the co-located ``calculator.ui``.
- [`uv_utils/_uv_utils.py`](#uv_utils--_uv_utils) — UV utilities — UV-coordinate translation and UV-set cleanup (mirror of mayatk's ``UvUtils``
- [`uv_utils/rizom_bridge/_rizom_bridge.py`](#uv_utils--rizom_bridge--_rizom_bridge) — RizomUV bridge engine — export the selection and open it in a fresh RizomUV session.
- [`uv_utils/rizom_bridge/rizom_bridge_slots.py`](#uv_utils--rizom_bridge--rizom_bridge_slots) — Switchboard slot wiring for the RizomUV Bridge panel (``rizom_bridge.ui``).
- [`xform_utils/_xform_utils.py`](#xform_utils--_xform_utils) — Transform utilities — object-level transform ops (world bbox, freeze, drop-to-grid,
- [`xform_utils/matrices.py`](#xform_utils--matrices) — Matrix utilities — the Blender counterpart of mayatk's ``xform_utils.matrices``

---

<a id="anim_utils--_anim_utils"></a>
### `anim_utils/_anim_utils.py`

Animation utilities — key-timing math over ``fcurve.keyframe_points`` (mirror of mayatk's

- [`get_fcurves(objects)`](blendertk/blendertk/anim_utils/_anim_utils.py#L55) — All fcurves across the given objects' actions (slot-aware;
- [`scene_has_animation()`](blendertk/blendertk/anim_utils/_anim_utils.py#L63) — True if the blend file contains any action carrying fcurves (keyed motion).
- [`shift_keys(objects, offset)`](blendertk/blendertk/anim_utils/_anim_utils.py#L96) — Shift every key of the given objects by ``offset`` frames.
- [`move_keys_to_frame(objects, frame=None, retain_spacing=True, selected_keys_only=False, align='auto')`](blendertk/blendertk/anim_utils/_anim_utils.py#L101) — Move the objects' keys so they align to ``frame`` (default: the current frame).
- [`adjust_key_spacing(objects, spacing=1, frame=None, selected_keys_only=False, exact_gap=False)`](blendertk/blendertk/anim_utils/_anim_utils.py#L167) — Add (+) or remove (−) ``spacing`` frames of space at ``frame`` (default: the current
- [`align_selected_keyframes(objects, target_frame=None, use_earliest=True)`](blendertk/blendertk/anim_utils/_anim_utils.py#L211) — Move the SELECTED keyframes (``select_control_point``, e.g.
- [`set_visibility_keys(objects, visible=True, frame=None, when='current', offset=0)`](blendertk/blendertk/anim_utils/_anim_utils.py#L270) — Key viewport + render visibility (``hide_viewport``/``hide_render``) — mirror of
- [`add_intermediate_keys(objects, step=1.0, time_range=None, ignore_visibility=False)`](blendertk/blendertk/anim_utils/_anim_utils.py#L295) — Insert sampled keys every ``step`` frames between each fcurve's first and last key
- [`remove_intermediate_keys(objects, time_range=None, ignore_visibility=False)`](blendertk/blendertk/anim_utils/_anim_utils.py#L337) — Remove every key strictly between each fcurve's first and last (keeps only the
- [`select_keys(objects, time=None, add_to_selection=False)`](blendertk/blendertk/anim_utils/_anim_utils.py#L362) — Select keyframe points (``select_control_point`` — visible in the Dope Sheet /
- [`invert_keys(objects, mode='time', value_pivot=0.0)`](blendertk/blendertk/anim_utils/_anim_utils.py#L378) — Mirror keys to reverse motion — Blender analogue of Maya's invert (modes mirror its X/Y/both).
- [`snap_keys(objects, selected_only=False, time_range=None)`](blendertk/blendertk/anim_utils/_anim_utils.py#L406) — Snap keys to whole frames — mirror of ``mtk.snap_keys_to_frames`` (nearest rounding).
- [`set_interpolation(objects, interpolation='CONSTANT', handle=None)`](blendertk/blendertk/anim_utils/_anim_utils.py#L431) — Set fcurve key ``interpolation`` (``CONSTANT`` / ``LINEAR`` / ``BEZIER`` / ``SINE`` …) on
- [`set_stepped(objects, stepped=True)`](blendertk/blendertk/anim_utils/_anim_utils.py#L448) — Set stepped (CONSTANT) or smooth (BEZIER) interpolation on every key.
- [`delete_keys(objects)`](blendertk/blendertk/anim_utils/_anim_utils.py#L453) — Remove all animation from the given objects.
- [`fit_playback_range(objects=None)`](blendertk/blendertk/anim_utils/_anim_utils.py#L463) — Set the scene frame range to the keyed extent of ``objects`` (or every scene object).
- [`copy_keys(source)`](blendertk/blendertk/anim_utils/_anim_utils.py#L480) — Return the action carrying ``source``'s keys (the copy buffer for :func:`paste_keys`).
- [`paste_keys(objects, action)`](blendertk/blendertk/anim_utils/_anim_utils.py#L486) — Link a COPY of ``action`` to each target (independent keys, mirror of Maya paste).
- [`optimize_keys(objects=None, value_tolerance=0.001, remove_static_curves=True, remove_flat_keys=True, simplify_keys=False, stats=None)`](blendertk/blendertk/anim_utils/_anim_utils.py#L577) — Remove redundant animation data — mirror of ``mtk.AnimUtils.optimize_keys``.
- [`repair_corrupted_curves(objects=None, *, delete_unfixable=True, fix_infinite=True, fix_invalid_times=True, time_threshold=100000.0, value_threshold=1000000.0)`](blendertk/blendertk/anim_utils/_anim_utils.py#L627) — Detect and repair corrupted animation fcurves — mirror of
- [`tie_keyframes(objects=None, untie=False, frame_range=None, absolute=False)`](blendertk/blendertk/anim_utils/_anim_utils.py#L696) — Add (tie) or remove (untie) bookend keys at the playback-range boundaries — mirror of
- [`bake_keys(objects=None, frame_range=None, step=1, only_selected=False, visual_keying=True, clear_constraints=False, clear_parents=False, bake_types=None)`](blendertk/blendertk/anim_utils/_anim_utils.py#L749) — Bake animation to plain keyframes — the Blender analogue of Maya's Smart Bake (wraps the
- [`bake_blend_shapes(objects=None, frame_range=None, step=1)`](blendertk/blendertk/anim_utils/_anim_utils.py#L800) — Bake driven/animated blend-shape (shape-key) weights to explicit keyframes — the Blender
- [`get_animation_info(objects=None, by_time=False)`](blendertk/blendertk/anim_utils/_anim_utils.py#L865) — Per-object animation summary — mirror of ``mtk`` get-animation-info.
- [`format_animation_info_csv(records)`](blendertk/blendertk/anim_utils/_anim_utils.py#L900) — Render :func:`get_animation_info` records as CSV (paste into a spreadsheet) — mirror of
- [`format_animation_info_html(records)`](blendertk/blendertk/anim_utils/_anim_utils.py#L926) — Render :func:`get_animation_info` records as an HTML table for the text-view dialog.
- [`configure_render_output(scene, file_format='PNG', container=None, codec=None, quality=None)`](blendertk/blendertk/anim_utils/_anim_utils.py#L963) — Apply playblast/render output settings to ``scene.render`` — the engine behind the rendering
- **[`class AnimUtils`](blendertk/blendertk/anim_utils/_anim_utils.py#L1000)** — Namespace mirror (helpers also exposed module-level).

<a id="anim_utils--scale_keys"></a>
### `anim_utils/scale_keys.py`

Dedicated scale-keys module to keep AnimUtils lean and testable (mirror of mayatk's

- [`scale_keys(objects, factor, pivot=None)`](blendertk/blendertk/anim_utils/scale_keys.py#L14) — Scale key times by ``factor`` about ``pivot`` (defaults to each action's first key).
- **[`class ScaleKeys`](blendertk/blendertk/anim_utils/scale_keys.py#L32)** — Namespace mirror of mayatk's ``ScaleKeys`` (``scale_keys`` also exposed module-level).

<a id="anim_utils--stagger_keys"></a>
### `anim_utils/stagger_keys.py`

Dedicated stagger-keys module to keep AnimUtils lean and testable (mirror of mayatk's

- [`stagger_keys(objects, start_frame=None, spacing=5, use_intervals=False, invert=False, group_overlapping=False, merge_touching=False, smooth_tangents=False)`](blendertk/blendertk/anim_utils/stagger_keys.py#L27) — Re-time selected objects so their animations play one after another (mirror of ``mtk``
- **[`class StaggerKeys`](blendertk/blendertk/anim_utils/stagger_keys.py#L89)** — Namespace mirror of mayatk's ``StaggerKeys`` (``stagger_keys`` also exposed module-level).

<a id="cam_utils--_cam_utils"></a>
### `cam_utils/_cam_utils.py`

Camera utilities — clip-plane adjustment (mirror of mayatk's ``cam_utils``).

- [`adjust_camera_clipping(camera=None, near_clip=None, far_clip=None)`](blendertk/blendertk/cam_utils/_cam_utils.py#L61) — Adjust near/far clip planes of camera object(s) — mirror of ``mtk.adjust_camera_clipping``.
- **[`class CamUtils`](blendertk/blendertk/cam_utils/_cam_utils.py#L89)** — Namespace mirror of mayatk's ``CamUtils`` (helper also exposed module-level).

<a id="core_utils--_core_utils"></a>
### `core_utils/_core_utils.py`

Core blendertk utilities — DCC-environment info + cross-cutting decorators.

- [`undoable(fn)`](blendertk/blendertk/core_utils/_core_utils.py#L18) — Wrap ``fn`` so its changes collapse into a single Blender undo step.
- [`undo_checkpoint(fn)`](blendertk/blendertk/core_utils/_core_utils.py#L44) — Like :func:`undoable`, but pushes the restore point BEFORE ``fn`` runs (not after).
- [`get_env_info(key=None)`](blendertk/blendertk/core_utils/_core_utils.py#L100) — Return Blender scene / environment info (mirror of ``mtk.get_env_info``).
- [`ensure_image_deps(packages=None, add_to_path=True)`](blendertk/blendertk/core_utils/_core_utils.py#L140) — Make image-processing libraries importable in Blender's Python (default: Pillow → ``PIL``).
- [`get_recent_files(index=None)`](blendertk/blendertk/core_utils/_core_utils.py#L260) — Recently-opened .blend paths, most recent first (mirror of ``mtk.get_recent_files``).
- [`get_recent_autosave(filter_time=24, timestamp_format='%H:%M:%S')`](blendertk/blendertk/core_utils/_core_utils.py#L278) — Recent autosave .blend files as ``(path, timestamp)`` pairs, newest first
- [`get_scene_info(objects=None)`](blendertk/blendertk/core_utils/_core_utils.py#L309) — Scene audit record — the Blender analogue of Maya's Get Scene Info (a focused
- [`format_scene_info_html(info)`](blendertk/blendertk/core_utils/_core_utils.py#L360) — Render a :func:`get_scene_info` record as an HTML report for the text-view dialog.
- [`analyze_scene(objects=None, adaptive=True, sections=None)`](blendertk/blendertk/core_utils/_core_utils.py#L399) — Game-readiness scene audit — the Blender port of mayatk's ``SceneAnalyzer`` (the budgeted,
- [`cleanup_scene(quiet=False)`](blendertk/blendertk/core_utils/_core_utils.py#L515) — Purge orphan datablocks (0 users, no fake user) across the main collections — the
- [`selected_objects()`](blendertk/blendertk/core_utils/_core_utils.py#L552) — The current object selection, filtered of ``None`` (mirror of Maya's
- [`get_view3d_context()`](blendertk/blendertk/core_utils/_core_utils.py#L565) — Context-override dict targeting the first VIEW_3D area/region, or ``None`` if there is no
- **[`class CoreUtils(ptk.CoreUtils)`](blendertk/blendertk/core_utils/_core_utils.py#L594)** — Blender ``CoreUtils`` — extends pythontk's DCC-agnostic ``CoreUtils`` (mirrors

<a id="core_utils--diagnostics--mesh_diag"></a>
### `core_utils/diagnostics/mesh_diag.py`

Mesh diagnostics — the Blender counterpart of mayatk's ``core_utils.diagnostics.mesh_diag``

- [`find_problem_geometry(objects, *, ngons=False, nonmanifold=False, interior=False, nonplanar=False, loose=False, concave=False, quads=False, zero_area_faces=False, zero_length_edges=False, zero_uv_area=False, planar_tolerance=0.001, area_tolerance=1e-06, edge_length_tolerance=1e-06, uv_area_tolerance=1e-06, select=True)`](blendertk/blendertk/core_utils/diagnostics/mesh_diag.py#L71) — Find (and optionally **select**) problem mesh components — the diagnostic half of Maya's
- **[`class MeshDiagnostics`](blendertk/blendertk/core_utils/diagnostics/mesh_diag.py#L197)** — Mesh problem-detection (mirror of mayatk's ``MeshDiagnostics``).

<a id="core_utils--diagnostics--transform_diag"></a>
### `core_utils/diagnostics/transform_diag.py`

Transform diagnostics — the Blender counterpart of mayatk's

- [`fix_non_orthogonal_axes(objects=None, dry_run=False, tolerance=1e-05)`](blendertk/blendertk/core_utils/diagnostics/transform_diag.py#L37) — Bake out non-orthogonal (sheared) world axes — shear breaks FBX export (mirror of
- **[`class TransformDiagnostics`](blendertk/blendertk/core_utils/diagnostics/transform_diag.py#L99)** — Transform/shear diagnostics (mirror of mayatk's ``TransformDiagnostics``).

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
- **[`class DisplayUtils`](blendertk/blendertk/display_utils/_display_utils.py#L104)** — Namespace mirror of mayatk's ``DisplayUtils`` (helpers also exposed module-level).

<a id="display_utils--color_manager"></a>
### `display_utils/color_manager.py`

Color Manager tool panel — Switchboard slot wiring for the co-located ``color_manager.ui``.

- **[`class ColorManager`](blendertk/blendertk/display_utils/color_manager.py#L25)** — Engine: apply / select-by / reset object colors across material, object-color, and vertex
  - `ColorManager.assign_id_material(obj, color: Color)` *(static)* — Assign an ID material named ``ID_<HEX>`` with ``color`` as its base color to ``obj``
  - `ColorManager.set_object_color(obj, color: Color)` *(static)* — Set the object's viewport display color (``obj.color`` — Object-color shading).
  - `ColorManager.set_vertex_color(obj, color: Color, name: str = 'Color')` *(static)* — Write ``color`` to every corner of a mesh color attribute (created/reused, set active).
  - `ColorManager.apply_color(cls, objects: Sequence, color: Optional[Color] = None, apply_to_material: bool = False, apply_to_object: bool = False, apply_to_vertex: bool = False) -> None` *(class)* — Apply ``color`` (random when None) to each object across the enabled channels.
  - `ColorManager.get_object_color(obj) -> Optional[Color]` *(static)* — The object's viewport display color (``obj.color`` RGB), or None.
  - `ColorManager.get_material_color(obj) -> Optional[Color]` *(static)* — Base color of the object's active material (Principled base, else diffuse), or None.
  - `ColorManager.get_average_vertex_color(obj) -> Optional[Color]` *(static)* — Average of the active mesh color attribute, or None when there is none.
  - `ColorManager.color_difference(c1: Color, c2: Color) -> float` *(static)* — Average absolute per-channel RGB difference.
  - `ColorManager.get_objects_by_color(cls, target_color: Color, threshold: float = 0.1, check_material: bool = False, check_object: bool = False, check_vertex: bool = False) -> List` *(class)* — View-layer mesh objects whose color (on any enabled channel) is within ``threshold``.
  - `ColorManager.reset_colors(cls, objects: Sequence, reset_material: bool = True, reset_object: bool = True, reset_vertex: bool = True) -> None` *(class)* — Clear color assignments on ``objects`` for the chosen channels.
  - `ColorManager.reset_vertex_colors(obj) -> None` *(static)* — Remove every color attribute from a mesh object.
- **[`class ColorManagerSlots(ptk.LoggingMixin)`](blendertk/blendertk/display_utils/color_manager.py#L221)** — Switchboard slot wiring for the Color Manager panel (swatch palette + 3 channels).
  - `ColorManagerSlots.header_init(self, widget)` — Configure header help text.
  - `ColorManagerSlots.b000(self) -> None` — Reset Colors (Ctrl+click resets every object in the scene).
  - `ColorManagerSlots.b001(self) -> None` — Set Color — apply the active color to the selected objects on the enabled channels.
  - `ColorManagerSlots.b002(self) -> None` — Select By Color — select scene objects matching the active color (enabled channels).
  - `ColorManagerSlots.b003(self) -> None` — Get Color — read the active object's color into the selected swatch.

<a id="display_utils--exploded_view"></a>
### `display_utils/exploded_view.py`

Exploded View — Switchboard slot wiring for the co-located ``exploded_view.ui``.

- **[`class ExplodedViewSlots(ptk.LoggingMixin)`](blendertk/blendertk/display_utils/exploded_view.py#L26)** — Switchboard slot wiring for the Exploded View panel (mirror of mayatk's ``ExplodedViewSlots``).
  - `ExplodedViewSlots.header_init(self, widget)` — Configure header help text.
  - `ExplodedViewSlots.b000(self)` — Explode.
  - `ExplodedViewSlots.b001(self)` — Un-Explode (selected).
  - `ExplodedViewSlots.b002(self)` — Un-Explode All.
  - `ExplodedViewSlots.b003(self)` — Toggle Explode.

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
- [`mirror(objects, axis='x', pivot='object', merge_mode=1, delete_original=False, uninstance=False, merge_threshold=0.001)`](blendertk/blendertk/edit_utils/_edit_utils.py#L573) — Mirror mesh object(s) across an axis plane — mirror of ``mtk.EditUtils.mirror``.
- [`cut_along_axis(objects, axis='x', pivot='center', amount=1, offset=0.0, invert=False, delete=False, mirror=False, merge_threshold=0.0001)`](blendertk/blendertk/edit_utils/_edit_utils.py#L635) — Cut mesh object(s) along an axis — mirror of ``mtk.EditUtils.cut_along_axis``.
- [`wedge(objects, angle=90.0, divisions=4)`](blendertk/blendertk/edit_utils/_edit_utils.py#L712) — Wedge the selected faces about a selected hinge edge — mirror of Maya's
- [`snap_closest_verts(obj_a, obj_b, tolerance=10.0)`](blendertk/blendertk/edit_utils/_edit_utils.py#L779) — Snap each vertex of ``obj_a`` onto the closest vertex of ``obj_b`` within
- [`snap_to_grid(objects=None, grid_size=1.0, axes='xyz')`](blendertk/blendertk/edit_utils/_edit_utils.py#L808) — Snap to the nearest grid point — mirror of ``mtk.Snap.snap_to_grid``.
- [`snap_to_surface(source_meshes, target, offset=0.0, threshold=None, invert=False)`](blendertk/blendertk/edit_utils/_edit_utils.py#L846) — Project the source meshes' vertices onto the closest point of ``target``'s surface —
- [`get_similar_mesh(objects=None, *, tolerance=0.0, inc_orig=False, select=False, vertex=False, edge=False, face=False, triangle=False, shell=False, uvcoord=False, area=False, world_area=False, bounding_box=False)`](blendertk/blendertk/edit_utils/_edit_utils.py#L1024) — Find scene mesh objects similar to ``objects`` by topology / area / bounding-box metrics —
- [`separate_objects(objects=None, *, by_material=False, rename=False, center_pivots=True)`](blendertk/blendertk/edit_utils/_edit_utils.py#L1071) — Separate mesh(es) into loose parts, or one object per material (``by_material``) — Blender
- [`combine_objects(objects=None, *, group_by_material=False, cluster_by_distance=False, threshold=10000.0)`](blendertk/blendertk/edit_utils/_edit_utils.py#L1140) — Combine mesh objects into one — Blender mirror of mayatk's ``EditUtils.combine_objects``
- [`detach_components(*, duplicate=False, separate=True, separate_each=False)`](blendertk/blendertk/edit_utils/_edit_utils.py#L1183) — Detach the active mesh's selected faces — Blender mirror of mayatk's
- [`get_overlapping_faces(objects, delete=False, select=True, round_ndigits=5)`](blendertk/blendertk/edit_utils/_edit_utils.py#L1237) — Find faces geometrically coincident with another face — doubled geometry on *distinct*
- [`get_overlapping_duplicates(objects=None, retain=None, select=False, delete=False, round_ndigits=5)`](blendertk/blendertk/edit_utils/_edit_utils.py#L1279) — Find duplicate mesh objects overlapping in world space — mirror of
- [`loft(objects=None, *, close=False, reverse_normals=False, section_spans=1)`](blendertk/blendertk/edit_utils/_edit_utils.py#L1367) — Loft a mesh surface across a sequence of profile curves / mesh edge-loops — a Blender mesh
- **[`class EditUtils`](blendertk/blendertk/edit_utils/_edit_utils.py#L1430)** — Namespace mirror of mayatk's ``EditUtils`` (helpers also exposed module-level).

<a id="edit_utils--bevel"></a>
### `edit_utils/bevel.py`

Bevel tool — engine + Switchboard slot wiring for the co-located ``bevel.ui``.

- **[`class Bevel`](blendertk/blendertk/edit_utils/bevel.py#L24)** — Native ``bmesh.ops.bevel`` engine (mirror of mayatk's ``Bevel``).
  - `Bevel.bevel(objects=None, width=0.1, segments=1, profile=0.5, clamp_overlap=True, affect='EDGES', offset_type='OFFSET')` *(static)* — Bevel the selected edges (or vertices) of the given mesh objects.
- **[`class BevelSlots(ptk.LoggingMixin)`](blendertk/blendertk/edit_utils/bevel.py#L91)** — Switchboard slot wiring for the bevel UI (live preview + width / segments / profile).
  - `BevelSlots.header_init(self, widget)` — Configure header help text.
  - `BevelSlots.perform_operation(self, objects)`

<a id="edit_utils--bridge"></a>
### `edit_utils/bridge.py`

Bridge tool — engine + Switchboard slot wiring for the co-located ``bridge.ui``.

- **[`class Bridge`](blendertk/blendertk/edit_utils/bridge.py#L28)** — Native ``bmesh.ops.bridge_loops`` engine (mirror of mayatk's ``Bridge``).
  - `Bridge.bridge(objects=None, divisions=0, offset=0, merge=False)` *(static)* — Bridge the two selected open edge loops of each given mesh with new faces.
- **[`class BridgeSlots(ptk.LoggingMixin)`](blendertk/blendertk/edit_utils/bridge.py#L90)** — Switchboard slot wiring for the bridge UI (live preview + divisions / offset).
  - `BridgeSlots.header_init(self, widget)` — Configure header help text.
  - `BridgeSlots.perform_operation(self, objects)`

<a id="edit_utils--curtain"></a>
### `edit_utils/curtain.py`

Curtain (draped-cloth) generation — the Blender build over the shared

- [`curtain_rail_from_selection(objects)`](blendertk/blendertk/edit_utils/curtain.py#L38) — Resolve a rail polyline from a Blender selection.
- [`create_curtain(rail, name='curtain', **options)`](blendertk/blendertk/edit_utils/curtain.py#L86) — Create a pleated, gravity-draped curtain mesh from a rail polyline.
- **[`class CurtainUtils`](blendertk/blendertk/edit_utils/curtain.py#L149)** — Namespace mirror of mayatk's curtain module (helpers also exposed module-level).
- **[`class CurtainRig`](blendertk/blendertk/edit_utils/curtain.py#L161)** — Make grabbable control handles drive a finished curtain — Blender mirror of mayatk's
  - `CurtainRig.attach(curtain, controls=5, dropoff=2.0, name=None)` *(static)* — Rig *curtain* with control-empty handles that pull the cloth via hooks.
- **[`class CurtainSlots(ptk.LoggingMixin)`](blendertk/blendertk/edit_utils/curtain.py#L315)** — Switchboard slot wiring for the curtain UI (live preview + rail resolution).
  - `CurtainSlots.header_init(self, widget)` — Configure header help text.
  - `CurtainSlots.cmb000_init(self, widget)` — Wire the in-panel preset selector (built-in + user tiers) — mirror of the Maya panel.
  - `CurtainSlots.perform_operation(self, objects)`
  - `CurtainSlots.b001(self)` — Reset all fields to their default values.
  - `CurtainSlots.b002(self)` — Set Position (s025-27) to the selection's combined bounding-box center.

<a id="edit_utils--cut_on_axis"></a>
### `edit_utils/cut_on_axis.py`

Cut-On-Axis tool panel — Switchboard slot wiring for the co-located ``cut_on_axis.ui``.

- **[`class CutOnAxisSlots(ptk.LoggingMixin)`](blendertk/blendertk/edit_utils/cut_on_axis.py#L21)** — Switchboard slot wiring for the cut-on-axis UI (live preview).
  - `CutOnAxisSlots.header_init(self, widget)` — Configure header help text.
  - `CutOnAxisSlots.perform_operation(self, objects)`

<a id="edit_utils--duplicate_grid"></a>
### `edit_utils/duplicate_grid.py`

Grid array duplication + its tool panel — mirror of mayatk's ``edit_utils.duplicate_grid``.

- [`duplicate_grid(objects, dimensions=(2, 2, 1), spacing=0.0, mode='instance')`](blendertk/blendertk/edit_utils/duplicate_grid.py#L27) — Duplicate object(s) into a 3D grid — mirror of mayatk's ``DuplicateGrid.duplicate_grid``.
- **[`class DuplicateGrid`](blendertk/blendertk/edit_utils/duplicate_grid.py#L76)** — Namespace mirror of mayatk's ``DuplicateGrid`` (helper also exposed module-level).
- **[`class DuplicateGridSlots(ptk.LoggingMixin)`](blendertk/blendertk/edit_utils/duplicate_grid.py#L87)** — Switchboard slot wiring for the Duplicate-Grid panel.
  - `DuplicateGridSlots.header_init(self, widget)` — Configure header help text.
  - `DuplicateGridSlots.b001(self)` — Reset to Defaults: Resets all UI widgets to their default values.
  - `DuplicateGridSlots.perform_operation(self, objects)`

<a id="edit_utils--duplicate_linear"></a>
### `edit_utils/duplicate_linear.py`

Linear array duplication + its tool panel — mirror of mayatk's ``edit_utils.duplicate_linear``.

- [`duplicate_linear(objects, num_copies, translate=(0, 0, 0), rotate=(0, 0, 0), scale=(1, 1, 1), weight_bias=0.5, weight_curve=4.0, pivot='object', calculation_mode='weighted', instance=True)`](blendertk/blendertk/edit_utils/duplicate_linear.py#L23) — Duplicate object(s) along a linear path — mirror of mayatk's
- **[`class DuplicateLinear`](blendertk/blendertk/edit_utils/duplicate_linear.py#L75)** — Namespace mirror of mayatk's ``DuplicateLinear`` (helper also exposed module-level).
- **[`class DuplicateLinearSlots(ptk.LoggingMixin)`](blendertk/blendertk/edit_utils/duplicate_linear.py#L86)** — Switchboard slot wiring for the Duplicate-Linear panel.
  - `DuplicateLinearSlots.header_init(self, widget)` — Configure header help text.
  - `DuplicateLinearSlots.toggle_weight_ui(self)` — Disable the weight spinners for modes that don't use them.
  - `DuplicateLinearSlots.b001(self)` — Reset to Defaults: Resets all UI widgets to their default values.
  - `DuplicateLinearSlots.perform_operation(self, objects)`

<a id="edit_utils--duplicate_radial"></a>
### `edit_utils/duplicate_radial.py`

Radial array duplication + its tool panel — mirror of mayatk's ``edit_utils.duplicate_radial``.

- [`duplicate_radial(objects, num_copies, start_angle=0.0, end_angle=360.0, weight_bias=0.5, weight_curve=0.5, rotate_axis='y', offset=(0, 0, 0), translate=(0, 0, 0), rotate=(0, 0, 0), scale=(1, 1, 1), pivot='object', keep_original=False, instance=False, combine=False)`](blendertk/blendertk/edit_utils/duplicate_radial.py#L39) — Duplicate object(s) in a radial pattern — mirror of mayatk's
- **[`class DuplicateRadial`](blendertk/blendertk/edit_utils/duplicate_radial.py#L129)** — Namespace mirror of mayatk's ``DuplicateRadial`` (helper also exposed module-level).
- **[`class DuplicateRadialSlots(ptk.LoggingMixin)`](blendertk/blendertk/edit_utils/duplicate_radial.py#L140)** — Switchboard slot wiring for the Duplicate-Radial panel.
  - `DuplicateRadialSlots.header_init(self, widget)` — Configure header help text.
  - `DuplicateRadialSlots.b001(self)` — Reset to Defaults: Resets all UI widgets to their default values.
  - `DuplicateRadialSlots.perform_operation(self, objects)`

<a id="edit_utils--dynamic_pipe"></a>
### `edit_utils/dynamic_pipe.py`

Dynamic Pipe tool — Blender port of mayatk's ``edit_utils.dynamic_pipe``.

- **[`class DynamicPipe(ptk.LoggingMixin)`](blendertk/blendertk/edit_utils/dynamic_pipe.py#L28)** — Build a pipe-style mesh driven by a chain of handle objects (Empties/locators) — Blender
- **[`class DynamicPipeSlots(ptk.LoggingMixin)`](blendertk/blendertk/edit_utils/dynamic_pipe.py#L130)** — Switchboard slot wiring for the co-located ``dynamic_pipe.ui`` (mirror of mayatk's
  - `DynamicPipeSlots.header_init(self, widget)` — Configure header help text.
  - `DynamicPipeSlots.b000(self)` — Initialize Pipe — build the pipe from the selected handle objects (name-ordered).

<a id="edit_utils--macros"></a>
### `edit_utils/macros.py`

Hotkey macros — the Blender counterpart of ``mayatk.edit_utils.macros``.

- **[`class DisplayMacros(_ViewportMixin)`](blendertk/blendertk/edit_utils/macros.py#L69)**
  - `DisplayMacros.m_back_face_culling(cls)` *(class)* — Toggle Back-Face Culling in the viewport.
  - `DisplayMacros.m_isolate_selected(cls)` *(class)* — Isolate the current selection (toggle Local View).
  - `DisplayMacros.m_wireframe(cls)` *(class)* — Cycle the wireframe-on-shaded overlay: Off -> Full -> Reduced (mirrors Maya's
  - `DisplayMacros.m_shading(cls)` *(class)* — Cycle viewport shading: Wireframe -> Solid -> Material Preview.
  - `DisplayMacros.m_lighting(cls)` *(class)* — Cycle Solid-mode viewport lighting Studio -> MatCap -> Flat (Maya's displayLights
  - `DisplayMacros.m_grid_and_image_planes(cls)` *(class)* — Toggle the floor grid and reference image-empties together.
  - `DisplayMacros.m_cycle_display_state(cls)` *(class)* — Cycle the selected objects' draw type: Textured -> Wireframe -> Bounds (driven by the
  - `DisplayMacros.m_smooth_preview(cls)` *(class)* — Toggle a live Subdivision-Surface preview on the selected meshes.
  - `DisplayMacros.m_frame(cls)` *(class)* — Frame the selection (or the whole scene when nothing is selected).
- **[`class EditMacros(_ViewportMixin)`](blendertk/blendertk/edit_utils/macros.py#L194)**
  - `EditMacros.m_multi_component()` *(static)* — Multi-component selection — enable vertex+edge+face select together (edit mode).
  - `EditMacros.m_paste_and_rename(cls)` *(class)* — Paste objects (Blender's paste adds no 'pasted__' prefix, so no rename needed).
  - `EditMacros.m_merge_vertices(tolerance=0.0001)` *(static)* — Merge vertices by distance — on the active mesh in Edit Mode, or across every selected
  - `EditMacros.m_group()` *(static)* — Group the selected objects under an Empty at the selection's center, keeping their
- **[`class SelectionMacros`](blendertk/blendertk/edit_utils/macros.py#L241)**
  - `SelectionMacros.m_object_selection()` *(static)* — Object selection mask — leave edit mode (object mode).
  - `SelectionMacros.m_vertex_selection(cls)` *(class)* — Vertex selection mask (edit mode).
  - `SelectionMacros.m_edge_selection(cls)` *(class)* — Edge selection mask (edit mode).
  - `SelectionMacros.m_face_selection(cls)` *(class)* — Face selection mask (edit mode).
  - `SelectionMacros.m_invert_selection()` *(static)* — Invert the current selection (component-aware).
  - `SelectionMacros.m_toggle_UV_select_type()` *(static)* — Toggle UV select mode between Vertex and Face (Blender's ``uv_select_mode`` enum is
- **[`class UiMacros(_ViewportMixin)`](blendertk/blendertk/edit_utils/macros.py#L293)**
  - `UiMacros.m_toggle_panels(cls)` *(class)* — Toggle the 3D viewport's header, tool, and side (N) regions together.
- **[`class AnimationMacros`](blendertk/blendertk/edit_utils/macros.py#L308)**
  - `AnimationMacros.m_set_selected_keys(cls)` *(class)* — Set keys on the selected objects' transform channels at the current frame.
  - `AnimationMacros.m_unset_selected_keys(cls)` *(class)* — Remove keys on the selected objects' transform channels at the current frame.
- **[`class MacroManager`](blendertk/blendertk/edit_utils/macros.py#L334)** — Register ``m_*`` macros to Blender hotkeys from the same string spec Maya uses.
  - `MacroManager.set_macros(cls, *args)` *(class)* — Register a macro per spec string (``"m_name, key=1, cat=Display"``).
  - `MacroManager.call_with_input(func, input_string)` *(static)* — Parse ``"arg, key=val, ..."`` into positional/keyword args and call ``func``.
  - `MacroManager.set_macro(cls, name, key=None, cat=None, ann=None)` *(class)* — Bind macro ``name`` to ``key`` (e.g.
  - `MacroManager.remove_macros(cls)` *(class)* — Remove every keymap item this manager added (clean teardown / reload).
- **[`class Macros(MacroManager, DisplayMacros, EditMacros, SelectionMacros, AnimationMacros, UiMacros)`](blendertk/blendertk/edit_utils/macros.py#L462)** — Concrete macro holder — combines every macro mixin with the manager (mirror of mayatk).

<a id="edit_utils--mirror"></a>
### `edit_utils/mirror.py`

Mirror tool panel — Switchboard slot wiring for the co-located ``mirror.ui``.

- **[`class MirrorSlots(ptk.LoggingMixin)`](blendertk/blendertk/edit_utils/mirror.py#L22)** — Switchboard slot wiring for the mirror UI (live preview + axis/pivot/merge combos).
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

- **[`class NamingSlots(Naming, ptk.LoggingMixin)`](blendertk/blendertk/edit_utils/naming/naming_slots.py#L18)** — Switchboard slots for the Naming panel.
  - `NamingSlots.valid_suffixes(self)` *(property)* — Current type suffixes from the tb003 option-box fields (empties filtered out).
  - `NamingSlots.header_init(self, widget)` — Scope combo + help text.
  - `NamingSlots.txt000_init(self, widget)`
  - `NamingSlots.txt000(self, widget)` — Find — select objects whose name matches the pattern.
  - `NamingSlots.txt001_init(self, widget)`
  - `NamingSlots.txt001(self, widget)` — Rename — replace matched names with the given pattern.
  - `NamingSlots.tb000_init(self, widget)`
  - `NamingSlots.tb000(self, widget)` — Convert Case.
  - `NamingSlots.tb001_init(self, widget)`
  - `NamingSlots.tb001(self, widget)` — Suffix By Location.
  - `NamingSlots.tb002_init(self, widget)`
  - `NamingSlots.tb002(self, widget)` — Strip Chars.
  - `NamingSlots.tb003_init(self, widget)`
  - `NamingSlots.tb003(self, widget)` — Suffix By Type.

<a id="edit_utils--snap"></a>
### `edit_utils/snap.py`

Snap tool — Switchboard slot wiring for the co-located ``snap.ui``.

- **[`class SnapSlots(ptk.LoggingMixin)`](blendertk/blendertk/edit_utils/snap.py#L26)** — Switchboard slot wiring for the Snap panel (mirror of mayatk's ``SnapSlots``).
  - `SnapSlots.header_init(self, widget)` — Configure header help text.
  - `SnapSlots.b000_init(self, widget)` — Snap to Surface option box.
  - `SnapSlots.b000(self)` — Snap to Surface.
  - `SnapSlots.b001_init(self, widget)` — Snap to Closest Vertex option box.
  - `SnapSlots.b001(self)` — Snap to Closest Vertex.
  - `SnapSlots.b002_init(self, widget)` — Snap to Grid option box.
  - `SnapSlots.b002(self)` — Snap to Grid.

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
- [`find_workspaces(root_dir)`](blendertk/blendertk/env_utils/_env_utils.py#L198) — Project folders under ``root_dir`` — the root itself when it directly holds .blend files,
- [`open_scene(path)`](blendertk/blendertk/env_utils/_env_utils.py#L228) — Open a .blend file (replaces the current file — Maya's ``file -open``).
- [`format_scene_name(name, case=None, suffix='')`](blendertk/blendertk/env_utils/_env_utils.py#L241) — Apply a naming convention to a base scene name — ``case`` via :meth:`pythontk.StrUtils.set_case`
- [`save_scene_as(directory, name, case=None, suffix='', subfolder='', overwrite=True)`](blendertk/blendertk/env_utils/_env_utils.py#L259) — Save the current scene as a .blend under ``directory`` with naming conventions applied —
- [`rename_scene_file(path, new_base)`](blendertk/blendertk/env_utils/_env_utils.py#L293) — Rename a .blend on disk (and its ``.blend1`` backup) — mirror of mayatk's ``rename_scene``.
- [`delete_scene_file(path)`](blendertk/blendertk/env_utils/_env_utils.py#L318) — Delete a .blend (and its ``.blend1`` backup) — mirror of mayatk's ``delete_scene``.
- [`set_reference_display_mode(library, mode)`](blendertk/blendertk/env_utils/_env_utils.py#L360) — Set the display override for a linked library's objects — mirror of mayatk's
- [`get_reference_display_mode(library)`](blendertk/blendertk/env_utils/_env_utils.py#L383) — Return the active display mode (``"off"`` / ``"reference"`` / ``"template"``) for a linked
- **[`class EnvUtils`](blendertk/blendertk/env_utils/_env_utils.py#L402)** — Namespace mirror of mayatk's ``EnvUtils`` (helpers also exposed module-level).

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

- [`export_selection_fbx(filepath=None, objects=None, **fbx_opts)`](blendertk/blendertk/env_utils/fbx_utils.py#L121) — Export the selection (or ``objects``) to an FBX file for an external-app hand-off.
- [`import_fbx(filepath, **fbx_opts)`](blendertk/blendertk/env_utils/fbx_utils.py#L131) — Import an FBX file;
- **[`class FbxUtils`](blendertk/blendertk/env_utils/fbx_utils.py#L40)** — FBX import / export over ``bpy.ops`` (mirror of mayatk's ``FbxUtils`` export surface).
  - `FbxUtils.export(filepath=None, objects=None, selection_only=True, **fbx_opts)` *(static)* — Export to an FBX file — the consolidated counterpart of mayatk's ``FbxUtils.export``.
  - `FbxUtils.import_fbx(filepath, **fbx_opts)` *(static)* — Import an FBX file (wrapper over ``bpy.ops.import_scene.fbx``).

<a id="env_utils--handoff_export"></a>
### `env_utils/handoff_export.py`

Blender-side selection + FBX-export hooks shared by the hand-off bridge engines.

- **[`class BlenderExportMixin`](blendertk/blendertk/env_utils/handoff_export.py#L23)** — The Blender producer hooks for hand-off bridges (``_resolve_objects`` + ``_produce``).

<a id="env_utils--maya_bridge--_maya_bridge"></a>
### `env_utils/maya_bridge/_maya_bridge.py`

Maya bridge engine -- export the Blender selection and run a chosen import template in Maya.

- [`list_templates() -> List[Path]`](blendertk/blendertk/env_utils/maya_bridge/_maya_bridge.py#L71) — User-visible templates in ``templates/`` (skips underscore-prefixed).
- [`template_modes(template_path: Path) -> Tuple[str, ...]`](blendertk/blendertk/env_utils/maya_bridge/_maya_bridge.py#L76) — Modes a template declares via ``BRIDGE_MODES``;
- [`list_template_modes() -> List[Tuple[str, str]]`](blendertk/blendertk/env_utils/maya_bridge/_maya_bridge.py#L81) — ``[(stem, mode), ...]`` for every (template, mode) pairing.
- **[`class MayaBridge(BlenderExportMixin, ptk.ScriptLaunchBridge)`](blendertk/blendertk/env_utils/maya_bridge/_maya_bridge.py#L86)** — Export the Blender selection and run a chosen Maya import template.
  - `MayaBridge.maya_path(self) -> Optional[str]` *(property)*
  - `MayaBridge.maya_path(self, value: Optional[str]) -> None`
  - `MayaBridge.params_defaults(self) -> Dict[str, Any]`
  - `MayaBridge.render_context(self, params: Dict[str, Any]) -> Dict[str, str]`

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

<a id="light_utils--_light_utils"></a>
### `light_utils/_light_utils.py`

Light utilities — the world-environment (HDRI) helpers behind the HDR Manager panel

- [`set_world_hdri(filepath=None, strength=1.0, rotation=0.0, visible=True)`](blendertk/blendertk/light_utils/_light_utils.py#L52) — Set (or update) the world environment from an HDR image.
- [`get_world_hdri()`](blendertk/blendertk/light_utils/_light_utils.py#L97) — The current world-HDRI state as a dict (``filepath``/``strength``/``rotation``/
- [`set_world_ray_visibility(diffuse=None, glossy=None)`](blendertk/blendertk/light_utils/_light_utils.py#L120) — Toggle whether the world environment contributes to **diffuse** / **glossy** lighting — the
- [`get_world_ray_visibility()`](blendertk/blendertk/light_utils/_light_utils.py#L141) — The world's diffuse/glossy ray-visibility as ``{diffuse, glossy}``, or ``None`` (no world /
- **[`class LightUtils`](blendertk/blendertk/light_utils/_light_utils.py#L153)** — Namespace mirror of mayatk's ``light_utils`` (helpers also exposed module-level).

<a id="light_utils--hdr_manager"></a>
### `light_utils/hdr_manager.py`

HDR Manager tool panel — Switchboard slot wiring for the co-located ``hdr_manager.ui``.

- **[`class HdrManagerSlots(ptk.LoggingMixin)`](blendertk/blendertk/light_utils/hdr_manager.py#L27)** — Switchboard slot wiring for the HDR Manager panel.
  - `HdrManagerSlots.header_init(self, widget)` — Configure header menu (reveal / open folder) + help text.
  - `HdrManagerSlots.cmb000_init(self, widget)` — Populate the HDR map combo (the folder field restores before first show).
  - `HdrManagerSlots.txt000(self, text, widget)` — HDR folder changed — re-scan the map list.
  - `HdrManagerSlots.b001(self)` — Browse for the HDR folder.
  - `HdrManagerSlots.cmb000(self, index, widget)` — HDR map selection — build/update the world environment from the pick.
  - `HdrManagerSlots.spn_intensity(self, value, widget)` — Intensity — live update.
  - `HdrManagerSlots.spn_exposure(self, value, widget)` — Exposure (stops) — live update.
  - `HdrManagerSlots.slider000(self, value, widget)` — Rotation — live update.
  - `HdrManagerSlots.chk000(self, state, widget)` — Visible — live update.
  - `HdrManagerSlots.chk_diffuse(self, state, widget)` — Diffuse contribution (Cycles ray visibility) — live update.
  - `HdrManagerSlots.chk_glossy(self, state, widget)` — Glossy contribution (Cycles ray visibility) — live update.
  - `HdrManagerSlots.reveal_selected(self)` — Reveal the selected HDR file in the OS file manager.
  - `HdrManagerSlots.open_folder(self)` — Open the HDR folder in the OS file manager.

<a id="light_utils--lightmap_baker--lightmap_baker"></a>
### `light_utils/lightmap_baker/lightmap_baker.py`

High-level lightmap baking workflow for Blender -> game engines (Unity-first).

- **[`class LightmapBaker(ptk.LoggingMixin)`](blendertk/blendertk/light_utils/lightmap_baker/lightmap_baker.py#L52)** — Orchestrate the Blender lightmap workflow: UV2 -> Cycles bake -> engine export prep.
  - `LightmapBaker.resolution(self) -> int` *(property)*
  - `LightmapBaker.resolution(self, value: int) -> None`
  - `LightmapBaker.samples(self) -> int` *(property)*
  - `LightmapBaker.samples(self, value: int) -> None`
  - `LightmapBaker.preset_store() -> 'ptk.PresetStore'` *(static)* — Shared store of lightmap quality presets (built-in + user tiers).
  - `LightmapBaker.from_preset(cls, name: str, **overrides) -> 'LightmapBaker'` *(class)* — Construct a baker from a named quality preset (``resolution`` / ``samples``).
  - `LightmapBaker.bake_fused(self, objects=None, **kwargs) -> Dict[str, str]` — Bake a **fused** (albedo x lighting) HDR lightmap per object.
  - `LightmapBaker.bake_separated(self, objects=None, prefix: str = 'lightmap_irr_', **kwargs) -> Dict[str, str]` — Bake a **lighting-only** irradiance lightmap per object (the default path).
  - `LightmapBaker.commit_lightmap(self, mapping: Dict[str, str], intensity: float = 1.0, scale_offsets: Optional[Dict[str, List[float]]] = None) -> Dict[str, str]` — Record a lighting-only bake for the engine (changes nothing about the material/UVs).
  - `LightmapBaker.revert_lightmap(self, objects=None) -> List[str]` — Undo :meth:`commit_lightmap` -- drop the markers + republish.
  - `LightmapBaker.commit_unlit(self, mapping: Dict[str, str]) -> Dict[str, str]` — Make the fused bake each object's live appearance (non-destructive).
  - `LightmapBaker.revert_unlit(self, objects=None) -> List[str]` — Undo :meth:`commit_unlit` -- restore the source material slots + drop the marker.
  - `LightmapBaker.revert(self, objects=None) -> List[str]` — Undo any lightmap wiring -- fused commit and/or lighting-only marker.
- **[`class LightmapBakerSlots(ptk.LoggingMixin)`](blendertk/blendertk/light_utils/lightmap_baker/lightmap_baker.py#L425)** — Switchboard slots for the co-located ``lightmap_baker.ui`` panel.
  - `LightmapBakerSlots.header_init(self, widget) -> None` — Configure the header menu + help text.
  - `LightmapBakerSlots.cmb000_init(self, widget) -> None` — Populate the Quality combobox from the shared preset store.
  - `LightmapBakerSlots.cmb000(self, index, widget) -> None` — Apply the selected preset's dials to Resolution / Samples.
  - `LightmapBakerSlots.cmb001_init(self, widget) -> None` — Populate the bake-level (Mode) combobox;
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
- [`find_materials_with_duplicate_textures()`](blendertk/blendertk/mat_utils/_mat_utils.py#L323) — Groups of materials that reference the *same* set of texture files — mirror of
- [`reassign_duplicate_materials(duplicate_groups, delete=True)`](blendertk/blendertk/mat_utils/_mat_utils.py#L335) — Reassign every object using a duplicate to the group's first (canonical) material, then
- [`delete_unused_materials()`](blendertk/blendertk/mat_utils/_mat_utils.py#L360) — Delete materials assigned to no object — mirror of Maya's *Delete Unused Materials*.
- [`graph_materials(materials, mode=None)`](blendertk/blendertk/mat_utils/_mat_utils.py#L379) — Open the Shader Editor focused on ``materials`` — the Blender analogue of Maya's
- [`get_image_records()`](blendertk/blendertk/mat_utils/_mat_utils.py#L408) — Every FILE-backed image datablock as a record for the Texture Path Editor:
- [`repath_image(image, new_path, reload=True)`](blendertk/blendertk/mat_utils/_mat_utils.py#L432) — Point ``image`` (datablock or name) at ``new_path`` and reload it — mirror of the Texture
- [`resolve_missing_textures(search_dir, recursive=True, stem=False, texture=False, fuzzy=False)`](blendertk/blendertk/mat_utils/_mat_utils.py#L490) — Repath missing FILE images within ``search_dir`` — the Blender analogue of Maya's Texture
- [`normalize_texture_paths(mode='relative', project_dir=None)`](blendertk/blendertk/mat_utils/_mat_utils.py#L562) — Normalize FILE image paths — mirror of the Texture Path Editor's 'Normalize Paths'.
- [`get_image_material_map()`](blendertk/blendertk/mat_utils/_mat_utils.py#L621) — ``{image-name: [material names]}`` for every FILE image referenced by a material's shader
- [`materials_for_textures(paths)`](blendertk/blendertk/mat_utils/_mat_utils.py#L638) — Scene materials whose shader graph references an image at one of ``paths`` (matched by
- [`fix_color_spaces(images=None, force_update=False, dry_run=False)`](blendertk/blendertk/mat_utils/_mat_utils.py#L678) — Assign each texture image its correct color space by map type — the Blender counterpart of
- [`set_texture_directory(images=None, target_dir=None, mode='rewrite')`](blendertk/blendertk/mat_utils/_mat_utils.py#L764) — Repath each image so its file lives directly under ``target_dir`` — mirror of the Texture
- [`find_and_copy_textures(images=None, search_dir=None, dest_dir=None, mode='copy')`](blendertk/blendertk/mat_utils/_mat_utils.py#L794) — Search ``search_dir`` recursively for the textures used by ``images`` (matched by basename),
- [`format_texture_paths_html(records=None)`](blendertk/blendertk/mat_utils/_mat_utils.py#L831) — Render :func:`get_image_records` as an HTML table for the panel/report (missing flagged).
- [`get_shader_templates()`](blendertk/blendertk/mat_utils/_mat_utils.py#L878) — The available Principled-BSDF template names (mirror of Maya's Shader Templates list).
- [`apply_shader_template(material, template)`](blendertk/blendertk/mat_utils/_mat_utils.py#L908) — Apply a Principled-BSDF template preset to ``material``'s shader.
- [`create_shader_template(template, name=None)`](blendertk/blendertk/mat_utils/_mat_utils.py#L923) — Create a new node-based material configured from a Principled-BSDF ``template`` — mirror of
- [`serialize_material(material)`](blendertk/blendertk/mat_utils/_mat_utils.py#L952) — Capture a material's shader node graph as a portable, JSON-safe dict — the Blender analogue of
- [`restore_material(data, name=None, textures=None)`](blendertk/blendertk/mat_utils/_mat_utils.py#L1002) — Rebuild a material from a :func:`serialize_material` dict — the Blender analogue of mayatk's
- [`create_pbr_material(textures, name=None, normal_direction='OpenGL')`](blendertk/blendertk/mat_utils/_mat_utils.py#L1103) — Build a Principled-BSDF material from a set of PBR texture files — Blender mirror of mayatk's
- [`create_pbr_materials(textures, name=None, normal_direction='OpenGL', prefix='', suffix='')`](blendertk/blendertk/mat_utils/_mat_utils.py#L1334) — Batch builder — Blender mirror of mayatk's ``GameShader.create_network`` batch path.
- [`update_materials(materials=None, config=None, verbose=False, progress_callback=None)`](blendertk/blendertk/mat_utils/_mat_utils.py#L1546) — Module-level alias for :meth:`MatUpdater.update_materials` (``btk.update_materials``).
- **[`class MatUpdater(ptk.LoggingMixin)`](blendertk/blendertk/mat_utils/_mat_utils.py#L1375)** — Batch texture reprocessor for scene materials — Blender mirror of mayatk's ``MatUpdater``.
  - `MatUpdater.update_materials(cls, materials=None, config=None, verbose=False, progress_callback=None)` *(class)* — Reprocess the textures of ``materials`` and repath their image nodes to the results.
- **[`class MatUtils`](blendertk/blendertk/mat_utils/_mat_utils.py#L1553)** — Namespace mirror of mayatk's ``MatUtils`` (helpers also exposed module-level).

<a id="mat_utils--game_shader"></a>
### `mat_utils/game_shader.py`

Game Shader tool panel — auto-build a Principled-BSDF material from a set of PBR textures.

- **[`class GameShaderSlots(ptk.LoggingMixin)`](blendertk/blendertk/mat_utils/game_shader.py#L30)** — Switchboard slot wiring for the Game Shader panel.
  - `GameShaderSlots.header_init(self, widget)` — Configure header help text.
  - `GameShaderSlots.cmb001_init(self, widget)` — Normal-map direction (mayatk's normal_output_grp combo).
  - `GameShaderSlots.b000(self)` — Create from Files…
  - `GameShaderSlots.b001(self)` — Create from Folder…

<a id="mat_utils--image_to_plane--_image_to_plane"></a>
### `mat_utils/image_to_plane/_image_to_plane.py`

Map image files to textured planes in Blender — port of mayatk's ``mat_utils.image_to_plane``.

- **[`class ImageToPlane(ptk.LoggingMixin)`](blendertk/blendertk/mat_utils/image_to_plane/_image_to_plane.py#L20)** — Create textured planes from image files (mirror of mayatk's ``ImageToPlane``).
  - `ImageToPlane.create(cls, image_paths, mat_type='standard', suffix='_MAT', prefix='', plane_height=10.0, group=False, group_name='imagePlanes_GRP')` *(class)* — Create textured planes for one or more images.
  - `ImageToPlane.remove(cls, objects=None)` *(class)* — Remove planes and their auto-created materials/images (orphans only) — mirror of

<a id="mat_utils--image_to_plane--image_to_plane_slots"></a>
### `mat_utils/image_to_plane/image_to_plane_slots.py`

Switchboard slots for the Image to Plane panel — port of mayatk's ``ImageToPlaneSlots``.

- **[`class ImageToPlaneSlots(ptk.LoggingMixin)`](blendertk/blendertk/mat_utils/image_to_plane/image_to_plane_slots.py#L18)** — Switchboard slots for the Image to Plane panel.
  - `ImageToPlaneSlots.header_init(self, widget)` — Configure header menu + help text.

<a id="mat_utils--mat_updater"></a>
### `mat_utils/mat_updater.py`

Material Updater tool panel — Switchboard slot wiring for the co-located ``mat_updater.ui``.

- **[`class MatUpdaterSlots(MatUpdater)`](blendertk/blendertk/mat_utils/mat_updater.py#L24)** — Switchboard slot wiring for the Material Updater panel.
  - `MatUpdaterSlots.header_init(self, widget)` — Build the processing options in the header menu (mirror of the Maya panel's, minus the
  - `MatUpdaterSlots.cmb001_init(self, widget)` — Populate the workflow-preset combo.
  - `MatUpdaterSlots.b001(self)` — Update Materials — assemble config from the panel and run the engine.

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

- **[`class RenderOpacitySlots(ptk.LoggingMixin)`](blendertk/blendertk/mat_utils/render_opacity/render_opacity_slots.py#L15)** — Slots for the Render Opacity panel (Create / Key fade / Remove).
  - `RenderOpacitySlots.header_init(self, widget)`
  - `RenderOpacitySlots.tb000_init(self, widget)` — Configure the Key Render Opacity option box.
  - `RenderOpacitySlots.tb000(self, widget)` — Key Render Opacity — key a fade on the opacity property (+ mirror to visibility).

<a id="mat_utils--shader_templates"></a>
### `mat_utils/shader_templates.py`

Shader Templates tool panel — Switchboard slot wiring for the co-located

- **[`class ShaderTemplatesSlots(ptk.LoggingMixin)`](blendertk/blendertk/mat_utils/shader_templates.py#L31)** — Switchboard slot wiring for the Shader Templates panel.
  - `ShaderTemplatesSlots.header_init(self, widget)` — Build the header menu (Save / Load Textures / manage) + help text.
  - `ShaderTemplatesSlots.cmb002_init(self, widget)` — Populate the template combo: built-in parameter presets + saved graph templates.
  - `ShaderTemplatesSlots.b000(self)` — Create New — rebuild the template into a fresh material, assigned to the selection.
  - `ShaderTemplatesSlots.b001(self)` — Apply to Selected — write a built-in parameter preset onto the selection's materials.
  - `ShaderTemplatesSlots.save_template(self)` — Capture the active material's node graph into the user store.
  - `ShaderTemplatesSlots.load_textures(self)` — Pick texture files to rebind by map type when a graph template is restored.
  - `ShaderTemplatesSlots.rename_template(self)` — Rename the selected saved template.
  - `ShaderTemplatesSlots.delete_template(self)` — Delete the selected saved template.
  - `ShaderTemplatesSlots.open_templates_folder(self)` — Reveal the saved-templates folder in the OS file manager.

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

- **[`class TexturePathEditorSlots(ptk.LoggingMixin)`](blendertk/blendertk/mat_utils/texture_path_editor.py#L31)** — Switchboard slot wiring for the Texture Path Editor panel.
  - `TexturePathEditorSlots.header_init(self, widget)` — Build the header menu (General / Path Management / Selection) + help text.
  - `TexturePathEditorSlots.tb_set_texture_directory_init(self, widget)` — Set Directory option-box: the relocate-mode combobox.
  - `TexturePathEditorSlots.tb_find_and_copy_textures_init(self, widget)` — Find & Copy option-box: the copy/move combobox (also swaps the button text).
  - `TexturePathEditorSlots.tb_normalize_paths_init(self, widget)` — Normalize Paths option-box: the external-texture-mode combobox.
  - `TexturePathEditorSlots.tb_resolve_missing_textures_init(self, widget)` — Resolve Missing option-box: the search strategy checkboxes (safest-first cascade).
  - `TexturePathEditorSlots.tbl000_init(self, widget)` — Build the row context menu once, then (re)populate the table.
  - `TexturePathEditorSlots.open_textures_folder(self)` — Open <blend>/textures (or the .blend folder) in the file explorer.
  - `TexturePathEditorSlots.reload_scene_textures(self)` — Force Blender to re-read every image from disk.
  - `TexturePathEditorSlots.tb_set_texture_directory(self, widget=None)` — Repath images (selection or all) so their files live under a chosen directory.
  - `TexturePathEditorSlots.tb_find_and_copy_textures(self, widget=None)` — Search a folder for the images' textures, copy/move to a destination, repath.
  - `TexturePathEditorSlots.tb_normalize_paths(self, widget=None)` — Rewrite paths relative to the saved .blend;
  - `TexturePathEditorSlots.tb_resolve_missing_textures(self, widget=None)` — Search a folder for replacements for missing textures (by name).
  - `TexturePathEditorSlots.select_textures_for_objects(self)` — Select rows whose image is used by a material on the scene selection.
  - `TexturePathEditorSlots.select_broken_paths(self)` — Select rows whose texture file is missing.
  - `TexturePathEditorSlots.select_absolute_paths(self)` — Select rows whose path is absolute (not a // project-relative path).
  - `TexturePathEditorSlots.row_browse_for_file(self, selection=None)` — Repath the selected row's image to a file chosen in a browser (single selection).
  - `TexturePathEditorSlots.select_material(self, selection=None)` — Select scene objects using the materials of the selected rows.
  - `TexturePathEditorSlots.row_show_in_shader_editor(self, selection=None)` — Open Blender's Shader Editor (the Hypershade analogue).
  - `TexturePathEditorSlots.delete_image(self, selection=None)` — Remove the selected image datablock(s).
  - `TexturePathEditorSlots.handle_cell_edit(self, row, col)` — Editing a path cell repaths that row's image;
  - `TexturePathEditorSlots.refresh_texture_table(self)` — Manual refresh trigger from the header refresh button.

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
  - `Channels.single_object_mode(self, value)`
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
  - `ChannelsSlots.cmb000_init(self, widget)` — Populate the filter combobox + wire its invert action (bpy-free).
  - `ChannelsSlots.cmb000(self, index)` — Filter changed — refresh table.
  - `ChannelsSlots.header_init(self, widget)` — Populate the header menu (Qt-only;
  - `ChannelsSlots.show_create_menu(self, *args)` — Show the *Create Attribute* popup (a custom-property form).
  - `ChannelsSlots.tbl000_init(self, widget)` — One-time table setup (action columns + context menu + signals), then a guarded refresh.

<a id="node_utils--data_nodes"></a>
### `node_utils/data_nodes.py`

Scene-wide export-metadata carrier — mirror of mayatk's ``node_utils.data_nodes``.

- **[`class DataNodes`](blendertk/blendertk/node_utils/data_nodes.py#L11)** — Scene-wide export-metadata carrier (mirror of mayatk's ``node_utils.DataNodes``).
  - `DataNodes.get_export_node(create=True)` *(static)* — The ``data_export`` Empty (created + linked to the scene when *create*).
  - `DataNodes.set_export_string(key, value)` *(static)* — Set custom property *key* on the carrier to *value* (string).
  - `DataNodes.get_export_string(key)` *(static)* — The carrier's *key* custom property, or ``None`` (no carrier / key).

<a id="nurbs_utils--_nurbs_utils"></a>
### `nurbs_utils/_nurbs_utils.py`

Shared curve helpers — Blender mirror of mayatk's ``nurbs_utils.NurbsUtils`` namespace.

- **[`class NurbsUtils(ptk.LoggingMixin)`](blendertk/blendertk/nurbs_utils/_nurbs_utils.py#L16)** — Shared Blender curve primitives (mirror of mayatk's ``NurbsUtils``).
  - `NurbsUtils.add_spline(curve, points, cyclic=False, kind='POLY')` *(static)* — Append a spline of ``points`` (each an ``(x, y, z)``) to an existing curve.
  - `NurbsUtils.create_curve(cls, points, name='curve', cyclic=False, kind='POLY', dimensions='3D', link=True, collection=None)` *(class)* — Build a curve object from a point list — mirror of mayatk's ``cmds.curve`` usage.
  - `NurbsUtils.curve_to_mesh(curve_obj, name=None, link=True, keep_curve=False, collection=None)` *(static)* — Bake a curve object's **evaluated** geometry (its bevel sweep / 2D fill) to a new mesh

<a id="nurbs_utils--curve_to_tube"></a>
### `nurbs_utils/curve_to_tube.py`

Curve to Tube tool — Blender port of mayatk's ``nurbs_utils.curve_to_tube``.

- **[`class CurveToTube(ptk.LoggingMixin)`](blendertk/blendertk/nurbs_utils/curve_to_tube.py#L36)** — Sweep a circular profile along curve(s) to build a tube — Blender mirror of mayatk's
  - `CurveToTube.create(cls, curves, output_type='nurbs', radius=1.0, sections=8, path_divisions=1, degree=3, caps=True, quads=True, live=False, name='tube')` *(class)* — Build a tube along each given curve.
- **[`class CurveToTubeSlots(ptk.LoggingMixin)`](blendertk/blendertk/nurbs_utils/curve_to_tube.py#L163)** — Switchboard slot wiring for the Curve to Tube panel (hermetic Preview), mirror of mayatk's
  - `CurveToTubeSlots.header_init(self, widget)` — Configure header help text.
  - `CurveToTubeSlots.b001(self)` — Reset to Defaults.
  - `CurveToTubeSlots.perform_operation(self, objects)` — Build the tube(s) from the selected curves (Preview entry point).

<a id="nurbs_utils--image_tracer"></a>
### `nurbs_utils/image_tracer.py`

Image Tracer tool — Blender port of mayatk's ``nurbs_utils.image_tracer``.

- **[`class ImageTracer(ptk.LoggingMixin)`](blendertk/blendertk/nurbs_utils/image_tracer.py#L35)** — Trace a raster image into curves / filled meshes — Blender mirror of mayatk's ``ImageTracer``.
  - `ImageTracer.trace_curves(self, name='traced_curve')` — Trace the image into ONE curve object — one cyclic POLY spline per contour (so nested
  - `ImageTracer.create_mesh(self, curve=None, name='traced_mesh')` — Fill the traced contours into a mesh (positive space;
  - `ImageTracer.create_negative_space_mesh(self, curve=None, margin_scale=0.1, name='negative_space_mesh')` — Fill the **inverse**: a boundary rectangle (margin-padded bbox) around the contours, with
- **[`class ImageTracerSlots(ptk.LoggingMixin)`](blendertk/blendertk/nurbs_utils/image_tracer.py#L128)** — Switchboard slot wiring for the co-located ``image_tracer.ui`` (mirror of mayatk's
  - `ImageTracerSlots.header_init(self, widget)` — Configure header help text.
  - `ImageTracerSlots.txt000_init(self, widget)` — Configure the path field's option box (▸) as an image file browser.
  - `ImageTracerSlots.b005_init(self, widget)` — Project on Plane is vestigial in Blender (curves are born planar on Z=0) → hide it.
  - `ImageTracerSlots.b002(self)` — Trace Curves.
  - `ImageTracerSlots.b003(self)` — Create Mesh (filled contours, nested = holes).
  - `ImageTracerSlots.b004(self)` — Create Negative Space (boundary rectangle with contour holes).

<a id="rig_utils--_rig_utils"></a>
### `rig_utils/_rig_utils.py`

Shared procedural-rig primitives — Blender port of mayatk's ``rig_utils.RigUtils``.

- **[`class RigUtils`](blendertk/blendertk/rig_utils/_rig_utils.py#L20)** — Constraint / driver / handle / grouping / armature helpers shared by the procedural rigs.
  - `RigUtils.resolve_object(obj)` *(static)* — An object or its name → the ``bpy`` object (``None`` if missing).
  - `RigUtils.create_locator(name='locator', location=(0, 0, 0), display_type='PLAIN_AXES', size=1.0, collection=None)` *(static)* — Create an Empty — Blender's analogue of Maya's spaceLocator (a rig handle).
  - `RigUtils.create_group(name='rig_grp', location=(0, 0, 0), children=None)` *(static)* — Create an Empty used as a transform group, parenting ``children`` under it (keeping
  - `RigUtils.parent_keep_transform(child, parent)` *(static)* — Parent ``child`` to ``parent`` without moving it in world space (Maya ``parent`` default).
  - `RigUtils.create_armature(name='armature', location=(0, 0, 0), collection=None)` *(static)* — Create an empty Armature object (Maya's joint-chain container).
  - `RigUtils.add_bone_chain(armature, points, prefix='bone', connect=True)` *(static)* — Build a connected bone chain through world-space *points* — Maya's ``generate_joint_chain``
  - `RigUtils.add_bone_constraint(armature, bone_name, ctype, target=None, subtarget=None, **props)` *(static)* — Add a **pose-bone** constraint (``ctype`` e.g.
  - `RigUtils.add_spline_ik(armature, bone_name, curve, chain_count, name='Spline IK', **props)` *(static)* — Add a **Spline IK** bone constraint to pose bone *bone_name* so *chain_count* bones up the
  - `RigUtils.bind_armature(mesh, armature, auto_weights=True)` *(static)* — Bind *mesh* to *armature* (Maya ``skinCluster`` analogue).
  - `RigUtils.copy_location(obj, target, influence=1.0)` *(static)* — Maya pointConstraint → COPY_LOCATION.
  - `RigUtils.copy_rotation(obj, target, influence=1.0)` *(static)* — Maya orientConstraint → COPY_ROTATION.
  - `RigUtils.damped_track(obj, target, track_axis='TRACK_Y')` *(static)* — Single-axis aim (Maya aimConstraint, no up-vector) → DAMPED_TRACK.
  - `RigUtils.track_to(obj, target, track_axis='TRACK_Y', up_axis='UP_Z')` *(static)* — Aim with an up-vector (full Maya aimConstraint) → TRACK_TO.
  - `RigUtils.child_of(obj, target, set_inverse=True)` *(static)* — Maya parentConstraint(maintainOffset=True) → CHILD_OF (inverse bound at the current pose).
  - `RigUtils.refresh_drivers(objects)` *(static)* — Force-recompile every driver on ``objects`` — call ONCE after building a rig's drivers.
  - `RigUtils.add_distance_driver(obj, data_path, index, a, b, expression='dist', var_name='dist')` *(static)* — Drive ``obj.<data_path>[index]`` from the live distance between objects ``a`` and ``b``
  - `RigUtils.add_transform_driver(obj, data_path, index, target, transform_type, space='WORLD_SPACE', expression=None, var_name='var')` *(static)* — Drive ``obj.<data_path>[index]`` from a single transform channel of ``target`` (a
  - `RigUtils.add_prop_var(fcurve, name, id_obj, data_path)` *(static)* — Append a ``SINGLE_PROP`` variable to an existing driver fcurve — e.g.
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

- **[`class ShadowRig(ptk.LoggingMixin)`](blendertk/blendertk/rig_utils/shadow_rig.py#L46)** — Projected-shadow rig for engine export (mirror of mayatk's ``ShadowRig``).
  - `ShadowRig.create_contact_locator(self)` — Empty at the footprint's lowest point (min-Z), parented to the first target so it tracks.
  - `ShadowRig.get_or_create_shadow_source(self, position=(5.0, 5.0, 10.0), source_name='shadow_source')` — Reuse an existing source Empty by name, else create one (Z-up default: high on +Z).
  - `ShadowRig.create_shadow_plane(self)` — Create a flat quad on the XY ground (normal +Z), centered at the footprint, with the
  - `ShadowRig.create_silhouette_texture(self, size=512, axis='auto', recursive=True, **kwargs)` — Rasterize the targets' world silhouette to an RGBA PNG via
  - `ShadowRig.create_material(self)` — Unlit black Emission mixed with a Transparent BSDF by ``tex.alpha × opacity`` (opacity a
  - `ShadowRig.setup_drivers(self)` — Build the transform + opacity drivers for the active mode, then force one recompile.
  - `ShadowRig.create(cls, targets, light_pos=(5.0, 5.0, 10.0), texture_res=512, axis='auto', source_name='shadow_source', recursive=True, mode='stretch', ground_height=0.0)` *(class)* — Build a projected-shadow rig for ``targets`` (mirror of mayatk's ``ShadowRig.create``).
- **[`class ShadowRigSlots(ptk.LoggingMixin)`](blendertk/blendertk/rig_utils/shadow_rig.py#L456)** — Switchboard slot wiring for the Shadow Rig panel.
  - `ShadowRigSlots.header_init(self, widget)` — Configure header help text.
  - `ShadowRigSlots.b001(self)` — Reset to Defaults — restore all UI widgets to their default values.
  - `ShadowRigSlots.perform_operation(self, objects)` — Build the shadow rig for the selected target(s).

<a id="rig_utils--telescope_rig"></a>
### `rig_utils/telescope_rig.py`

Telescope Rig — engine + Switchboard slot wiring for the co-located ``telescope_rig.ui``.

- **[`class TelescopeRig(ptk.LoggingMixin)`](blendertk/blendertk/rig_utils/telescope_rig.py#L26)** — Constraint + driver telescoping-segment rig (mirror of mayatk's ``TelescopeRig``).
  - `TelescopeRig.setup_telescope_rig(self, base_locator, end_locator, segments, collapsed_distance=1.0)` — Wire a telescoping rig between two handles.
- **[`class TelescopeRigSlots(ptk.LoggingMixin)`](blendertk/blendertk/rig_utils/telescope_rig.py#L97)** — Switchboard slot wiring for the Telescope Rig panel.
  - `TelescopeRigSlots.header_init(self, widget)` — Configure header help text.
  - `TelescopeRigSlots.build_rig(self)`

<a id="rig_utils--tube_path"></a>
### `rig_utils/tube_path.py`

Tube-mesh centerline extraction — Blender port of mayatk's ``rig_utils.tube_rig.TubePath``.

- **[`class TubePath`](blendertk/blendertk/rig_utils/tube_path.py#L22)** — Extract centerline paths from tube meshes (static helpers;
  - `TubePath.get_centerline(mesh, num_joints=10, precision=None, edges=None)` *(static)* — Unified centerline dispatcher — mirror of mayatk's ``TubePath.get_centerline``.
  - `TubePath.get_centerline_using_edges(mesh, edges)` *(static)* — Centerline from an explicit edge selection — mirror of mayatk's

<a id="rig_utils--tube_rig"></a>
### `rig_utils/tube_rig.py`

Tube Rig — Blender port of mayatk's ``rig_utils.tube_rig`` (the engine + strategies).

- [`register_strategy(cls)`](blendertk/blendertk/rig_utils/tube_rig.py#L180) — Register a custom :class:`TubeStrategy` subclass (keyed by ``cls.name``) — the extension point
- **[`class TubeRigBundle`](blendertk/blendertk/rig_utils/tube_rig.py#L44)** — Result of a strategy build — mirror of mayatk's ``TubeRigBundle``.
- **[`class TubeStrategy(ABC)`](blendertk/blendertk/rig_utils/tube_rig.py#L59)** — Base tube-rig strategy.
  - `TubeStrategy.defaults(self) -> dict`
  - `TubeStrategy.resolve(self, opts: Optional[dict]) -> dict` — Merge caller *opts* over the declared defaults (``None`` values fall back to default).
  - `TubeStrategy.build(self, rig: 'TubeRig', **opts) -> TubeRigBundle`
- **[`class SplineIKStrategy(TubeStrategy)`](blendertk/blendertk/rig_utils/tube_rig.py#L82)**
  - `SplineIKStrategy.build(self, rig, **opts)`
- **[`class AnchorStrategy(TubeStrategy)`](blendertk/blendertk/rig_utils/tube_rig.py#L112)**
  - `AnchorStrategy.build(self, rig, **opts)`
- **[`class FKChainStrategy(TubeStrategy)`](blendertk/blendertk/rig_utils/tube_rig.py#L141)**
  - `FKChainStrategy.build(self, rig, **opts)`
- **[`class TubeRig(ptk.LoggingMixin)`](blendertk/blendertk/rig_utils/tube_rig.py#L192)** — Rig a tube mesh via a named strategy — Blender mirror of mayatk's ``TubeRig``.
  - `TubeRig.collection(self)` *(property)*
  - `TubeRig.resolve_centerline(self, num_joints)` — The tube's centerline (world points) for *num_joints*, raising if the mesh isn't a
  - `TubeRig.create_root(self)`
  - `TubeRig.create_armature(self, centerline)` — Armature + bone chain along *centerline*, parented under the rig root.
  - `TubeRig.build_curve(self, points, count)` — A low-res NURBS driver curve (``count`` control points resampled along *points*) for the
  - `TubeRig.make_control(self, shape, name, size, location, root, color=(1, 1, 0), axis='x')` — Create a control curve at *location*, parented under *root* (keeping its world pos).
  - `TubeRig.hook_curve_controls(self, curve, radius, root)` — One control per curve control-point, each Hook-bound to its point (the live-reshape
  - `TubeRig.build(self, strategy='spline', **opts) -> TubeRigBundle` — Build the rig with the named *strategy* (``"spline"`` / ``"anchor"`` / ``"fk"`` or a
- **[`class TubeRigSlots(ptk.LoggingMixin)`](blendertk/blendertk/rig_utils/tube_rig.py#L307)** — Switchboard slot wiring for the co-located ``tube_rig.ui`` — the **HYBRID** panel.
  - `TubeRigSlots.header_init(self, widget)` — Configure header help text.
  - `TubeRigSlots.b000(self)` — Build Rig — run the selected strategy on the selected tube mesh.

<a id="rig_utils--wheel_rig"></a>
### `rig_utils/wheel_rig.py`

Wheel Rig — engine + Switchboard slot wiring for the co-located ``wheel_rig.ui``.

- **[`class WheelRig(ptk.LoggingMixin)`](blendertk/blendertk/rig_utils/wheel_rig.py#L22)** — Auto-rolling wheel rig (mirror of mayatk's ``WheelRig``).
  - `WheelRig.rig_name(self)` *(property)*
  - `WheelRig.rig_rotation(self, movement_axis='LOC_Z', rotation_index=None, wheel_height=1.0, wheels=None)` — Drive each wheel's rotation from the control's travel along ``movement_axis``.
- **[`class WheelRigSlots(ptk.LoggingMixin)`](blendertk/blendertk/rig_utils/wheel_rig.py#L100)** — Switchboard slot wiring for the Wheel Rig panel.
  - `WheelRigSlots.cmb000_init(self, widget)`
  - `WheelRigSlots.s000_init(self, widget)`
  - `WheelRigSlots.header_init(self, widget)` — Configure header help text.
  - `WheelRigSlots.wheel_rig(self)`

<a id="ui_utils--_ui_utils"></a>
### `ui_utils/_ui_utils.py`

UI utilities — opening Blender editors (the analogue of Maya's editor-window mel commands).

- [`get_editor_types()`](blendertk/blendertk/ui_utils/_ui_utils.py#L40) — The friendly-name → ``Area.ui_type`` map understood by :func:`open_editor`.
- [`open_editor(editor, properties_context=None)`](blendertk/blendertk/ui_utils/_ui_utils.py#L45) — Open ``editor`` (a friendly name from :data:`EDITOR_TYPES` or a raw ``ui_type``)
- [`menu_exists(menu_idname)`](blendertk/blendertk/ui_utils/_ui_utils.py#L75) — True if ``menu_idname`` (e.g.
- [`call_native_menu(menu_idname)`](blendertk/blendertk/ui_utils/_ui_utils.py#L86) — Pop Blender's own native menu ``menu_idname`` (e.g.
- **[`class UiUtils`](blendertk/blendertk/ui_utils/_ui_utils.py#L111)** — Namespace mirror (helpers also exposed module-level).

<a id="ui_utils--blender_ui_handler"></a>
### `ui_utils/blender_ui_handler.py`

- **[`class BlenderUiHandler(UiHandler)`](blendertk/blendertk/ui_utils/blender_ui_handler.py#L13)** — UI Handler for Blender applications.
  - `BlenderUiHandler.instance(cls, switchboard: Switchboard = None, **kwargs) -> 'BlenderUiHandler'` *(class)* — Return the BlenderUiHandler singleton, bootstrapping if needed.
  - `BlenderUiHandler.apply_styles(self, ui, style=None)` — Give blendertk-sourced tool panels a hide button instead of a pin.

<a id="ui_utils--calculator"></a>
### `ui_utils/calculator.py`

Calculator tool panel — Switchboard slot wiring for the co-located ``calculator.ui``.

- **[`class CalculatorController`](blendertk/blendertk/ui_utils/calculator.py#L18)** — DCC-agnostic math engine + Blender time helpers.
  - `CalculatorController.calculate(expression)` *(static)* — Safely evaluate a math expression (delegates to the shared engine).
  - `CalculatorController.convert_unit(value, from_unit, to_unit)` *(static)* — Convert a length value between units (delegates to the shared engine).
  - `CalculatorController.get_fps_value()` *(static)* — Scene frame rate (falls back to 24.0).
  - `CalculatorController.get_current_time()` *(static)* — Current frame as a string.
  - `CalculatorController.frames_to_sec(cls, frames)` *(class)*
  - `CalculatorController.sec_to_frames(cls, seconds)` *(class)*
- **[`class CalculatorSlots(ptk.LoggingMixin)`](blendertk/blendertk/ui_utils/calculator.py#L72)** — Switchboard slot wiring for the Calculator panel.
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

<a id="uv_utils--_uv_utils"></a>
### `uv_utils/_uv_utils.py`

UV utilities — UV-coordinate translation and UV-set cleanup (mirror of mayatk's ``UvUtils``

- [`move_uvs(objects, du=0.0, dv=0.0)`](blendertk/blendertk/uv_utils/_uv_utils.py#L45) — Translate the UVs of the given mesh object(s) by ``(du, dv)`` — "move to UV space"
- [`transform_uvs(objects, flip_u=False, flip_v=False, angle=0.0)`](blendertk/blendertk/uv_utils/_uv_utils.py#L90) — Flip and/or rotate (``angle`` degrees, CCW) the UVs of the given mesh object(s) about
- [`pin_uvs(objects, pin=True, selected_only=True)`](blendertk/blendertk/uv_utils/_uv_utils.py#L119) — Pin/unpin UVs (bmesh ``pin_uv``).
- [`get_texel_density(objects, map_size)`](blendertk/blendertk/uv_utils/_uv_utils.py#L163) — Texel density (px per scene unit) of the meshes' faces against a ``map_size`` map —
- [`set_texel_density(objects, density=1.0, map_size=4096)`](blendertk/blendertk/uv_utils/_uv_utils.py#L187) — Scale each object's UVs (about its own UV bbox center) to the target texel density —
- [`delete_extra_uv_sets(objects)`](blendertk/blendertk/uv_utils/_uv_utils.py#L211) — Remove all but the first UV map on the given mesh object(s) — "Cleanup UV Sets".
- [`cleanup_uv_sets(objects, *, remove_empty=True, keep_only_primary=False, rename_to_map1=True, force_rename=False, prefer_largest_area=True, dry_run=False)`](blendertk/blendertk/uv_utils/_uv_utils.py#L251) — Standardize / clean up the UV sets (``uv_layers``) of the given mesh object(s).
- [`find_lightmap_uv_set(obj)`](blendertk/blendertk/uv_utils/_uv_utils.py#L355) — Name of *obj*'s existing lightmap UV layer, or ``None`` (mirror of
- [`create_lightmap_uvs(objects, uv_set=LIGHTMAP_UV_SET, margin=0.02, quiet=True)`](blendertk/blendertk/uv_utils/_uv_utils.py#L376) — Ensure each mesh has a packed, non-overlapping lightmap UV layer (UV2).
- [`get_uv_coords(objects)`](blendertk/blendertk/uv_utils/_uv_utils.py#L502) — Snapshot the active-layer UV coordinates per object (``{name: [(u, v), …]}`` in
- [`set_uv_coords(objects, snapshot)`](blendertk/blendertk/uv_utils/_uv_utils.py#L524) — Restore a :func:`get_uv_coords` snapshot (objects whose topology changed since the
- [`stack_uv_shells(objects)`](blendertk/blendertk/uv_utils/_uv_utils.py#L548) — Stack UV islands — move each targeted island so its bbox center coincides with the
- [`distribute_uv_shells(objects, axis='u')`](blendertk/blendertk/uv_utils/_uv_utils.py#L576) — Distribute UV islands evenly along ``axis`` (``"u"`` or ``"v"``) — the first and
- [`straighten_uvs(objects, u=True, v=True, angle=30.0)`](blendertk/blendertk/uv_utils/_uv_utils.py#L611) — Straighten the selected UV edges — edges within ``angle`` degrees of horizontal
- **[`class UvUtils`](blendertk/blendertk/uv_utils/_uv_utils.py#L660)** — Namespace mirror of mayatk's ``UvUtils`` (helpers also exposed module-level).

<a id="uv_utils--rizom_bridge--_rizom_bridge"></a>
### `uv_utils/rizom_bridge/_rizom_bridge.py`

RizomUV bridge engine — export the selection and open it in a fresh RizomUV session.

- **[`class RizomUVBridge(ptk.LoggingMixin)`](blendertk/blendertk/uv_utils/rizom_bridge/_rizom_bridge.py#L35)** — Engine: discover the RizomUV exe, export the selection, launch RizomUV with a load-script.
  - `RizomUVBridge.rizom_path(self)` *(property)* — Resolved RizomUV executable path (cached), or None.
  - `RizomUVBridge.rizom_path(self, value)`
  - `RizomUVBridge.build_send_script(self, fbx_path, objects=None, load_uvs=True, import_groups=True, load_uvw_props=True, load_textures=True)` — Render the RizomUV Lua load-script (``ZomLoad`` + optional ``ZomLoadTexture`` block).
  - `RizomUVBridge.send(self, objects, load_uvs=True, import_groups=True, load_uvw_props=True, load_textures=True)` — Export ``objects`` to FBX and open them in a fresh RizomUV session (one-way).

<a id="uv_utils--rizom_bridge--rizom_bridge_slots"></a>
### `uv_utils/rizom_bridge/rizom_bridge_slots.py`

Switchboard slot wiring for the RizomUV Bridge panel (``rizom_bridge.ui``).

- **[`class RizomBridgeSlots(RizomUVBridge)`](blendertk/blendertk/uv_utils/rizom_bridge/rizom_bridge_slots.py#L13)** — Slots wired to ``rizom_bridge.ui``.
  - `RizomBridgeSlots.header_init(self, widget)` — Configure header help text.
  - `RizomBridgeSlots.b000(self)` — Send to RizomUV.

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
- [`get_distance(a, b)`](blendertk/blendertk/xform_utils/_xform_utils.py#L391) — Distance between two points — each an object (world origin), ``Vector``, or 3-sequence
- [`order_by_distance(objects, reference_point=None, reverse=False)`](blendertk/blendertk/xform_utils/_xform_utils.py#L397) — Order ``objects`` by distance from ``reference_point`` (an object / Vector / 3-seq;
- [`aim_object_at_point(objects, target_pos, aim_vect=(1, 0, 0), up_vect=(0, 1, 0))`](blendertk/blendertk/xform_utils/_xform_utils.py#L416) — Aim ``objects`` at a world-space point — mirror of ``mtk.aim_object_at_point`` (which uses
- **[`class XformUtils`](blendertk/blendertk/xform_utils/_xform_utils.py#L437)** — Namespace mirror of mayatk's ``XformUtils`` (helpers also exposed module-level).
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
