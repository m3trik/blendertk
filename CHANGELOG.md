# Changelog

## [Unreleased]

- **2026-07-15 — RizomUV bridge round-trip ported (Blender) — `pack` / `unwrap_hard` / `unwrap_organic` / `optimize` now run.** The four preset combo items were previously listed for item-count parity but disabled at runtime (`RizomBridgeSlots._disable_round_trip_presets`) because only the one-way `send` had an engine. `RizomUVBridge.process_with_rizomuv` now implements the full round-trip mirroring mayatk: export `__RZTMP`-suffixed **copies** to a temp FBX → run the chosen Lua preset headlessly (`Rizomuv_VS.exe -cfi`) → re-import the RizomUV-written FBX → transfer the new UVs back onto the originals → remove every temp object. Divergences from the Maya path, by construction: **copies not namespaced duplicates** (Blender's FBX import never overwrites — copies are removed before re-import so imports return under the exact `__RZTMP` names the mapping keys on), and a **direct per-loop UV copy** (bulk `foreach_get`/`foreach_set`) instead of `transferAttributes` (RizomUV rewrites only UVs, so the re-import is topologically identical to the original — exact and context-free in the windowless Qt-timer state; a spatial `data_transfer` is the fallback if the loop count ever diverges). The copies export with `use_mesh_modifiers=False` so a mesh with a Subsurf/Mirror modifier round-trips its **base** topology (not the evaluated cage) and the UV transfer stays exact, matching mayatk's base-poly FBX export; and the cleanup purges the material/image datablocks the FBX re-import creates (diffed against a pre-import snapshot) so repeated runs don't accumulate orphan `*.001` datablocks in the .blend. The Lua assets (`scripts/{pack,unwrap_hard,unwrap_organic,optimize}.lua` + `templates/wrapper.lua`) are **vendored byte-identical** from mayatk (pure RizomUV Lua, DCC-agnostic), and the DCC-agnostic Python (version parsing, wrapper substitution, RizomUV-version gating via `parameters.strip_unsupported`/`MIN_VERSIONS`, the headless run + FBX-modified verification) mirrors mayatk's engine. `parameters.PARAMS` regains the ~15 `ZomPack`/`ZomUnfold`/`ZomOptimize` knobs (pulled back verbatim from mayatk); the slot's `_VersionedParamsProxy` hides knobs gated above the installed Rizom (2020.1 access-violates on newer `ZomPack` fields). Slots drop the disabling machinery — `list_template_modes` globs `scripts/*.lua`, `b000` routes non-`send` presets through `process_with_rizomuv`. **Parity**: the rizom panel is now mechanically 1:1 (`compare_panel_surface.py --panel rizom_bridge`: 0 untriaged / 0 pending / 0 item deltas) — the stale `cmb000` `na` ledger entry is retired. **Verified**: `test_rizom_roundtrip.py` (fresh Blender 5.1, RizomUV stubbed) 16/16 — identity round-trip preserves UVs at max-diff 0.00 (loop order survives the FBX round-trip), a simulated UV delta propagates back, two distinct meshes don't cross-wire, a materialed mesh leaves no orphan material/image datablocks, a Subsurf-modified mesh transfers exactly onto its base (24 loops, not the evaluated cage), and every temp object is cleaned up; `test_rizom_construction.py` (venv) 17/17 — preset resolution, wrapper substitution, and 2020.1-vs-2022 version gating. Note: the round-trip engine imports its Qt-backed `parameters` lazily inside the method (import-time Qt-free, matching the marmoset bridge), so it runs in tentacle's live Blender but not a bare `--background` session without a Qt binding — the same accepted limitation as the other bridges.

- **2026-07-14 — Substance/Marmoset bridge "no selected objects" fixed in the windowless (Qt-timer) context.** Clicking *Send to Painter/Toolbag* from the bridge panel aborted with "Nothing selected to export." even with a valid selection AND output dir. Root cause was in `FbxUtils.export`: it read the selection (and the io_scene_fbx exporter reads it *internally*) via `bpy.context.selected_objects`, which raises `AttributeError` when `bpy.context.window is None` — exactly the state tentacle's `bpy.app.timers`/Qt event pump runs the slots in. `FbxUtils.export` now reads the selection window-independently (`btk.selected_objects`) and runs the `select_all` + `export_scene.fbx` operators under a new `btk.window_context_override()` (supplies the first open window so the operator's screen-context reads resolve). Fixes every bridge that hands off through `export_selection_fbx` (Substance, Marmoset, RizomUV). New `core_utils` helper `window_context_override` (the window-only companion to `get_view3d_context`). Verified live on Blender 5.1 under a forced `temp_override(window=None)`; permanent regression test in `test_bridges.py`.

- **2026-07-14 — Bridge FBX export: translate Maya MEL FBX option names to Blender kwargs.** The Substance/Marmoset templates are vendored verbatim from mayatk and carry Maya MEL names (e.g. `FBXExportEmbeddedTextures`, valid in Maya via `mel.eval FBXExport*`); passed straight to `bpy.ops.export_scene.fbx` they faulted with `keyword "FBXExport…" unrecognized` (surfaced once the windowless-export fix let the export run at all). `FbxUtils.export` now runs a `_translate_fbx_options` pass (the Blender side of the "engine does the idiomatic-per-DCC translation" contract): known Maya names map to their Blender kwarg (`FBXExportEmbeddedTextures`→`embed_textures`/`path_mode="COPY"`), unmapped `FBXExport*` (Maya-only) are dropped, and Blender-native keys pass through so a typo'd real kwarg still errors loudly. Templates stay verbatim. Regression tests in `test_bridges.py`.

- **2026-07-14 — Substance/Marmoset bridge no longer requires an Output Dir (temp-folder fallback).** When neither a typed value nor the `.blend`/workspace default resolves (an **unsaved** file), the panel now falls back to a self-cleaning temp folder instead of erroring — mirroring the Maya bridges (`TEMP_OUTPUT_FALLBACK` opt-in on the shared `uitk.BridgeSlotsBase`; the temp dir is registered for cleanup at process exit). Unity's bridge keeps the hard error (its Output Dir must be a real Unity project). Marmoset/Substance help text updated to drop the stale "(required)".

- **2026-07-13 — LightmapBaker: "Atlas by Material" packing ported (mayatk parity)** — `LightmapBaker.pack_atlas` consolidates a lighting-only bake's per-object maps into one shared, area-weighted EXR per primary material and repacks each object's lightmap UVs into its rect, so the exported mesh samples the atlas directly through UV2 (no engine scaleOffset binding). Groups by dominant `material_index`; the DCC-agnostic layout math is **reused** from pythontk (`ptk.ImgUtils.compute_atlas_layout` / `inset_atlas_rects` / `atlas_pixel_rects` — pure-Python, no cv2, the same helpers mayatk uses); the EXR assembly is bpy-native (image load/scale + a numpy paste + gutter-dilate, since Blender's runtime ships no cv2). The applied rect rides the marker's `uvRect`; `revert_lightmap` now inverts it (re-bake starts from the 0-1 layout). The cmb002 "Atlas by Material" item is enabled (the old `_disable_unsupported_packing_mode` removed) and the slot's bake commits via `commit_lightmap(uv_rects=…)`. Verified headlessly (`test_lightmap_baker.py`): 2 objects sharing a material → 1 shared EXR, source maps consolidated, UVs repacked into their rects, revert exactly restores the 0-1 layout (38/38).

- **2026-07-13 — Selection: UV-domain Convert-To + Back/Front-Facing leaves ported (mayatk parity)** — `Selection.select_uv_shell_border` / `select_uv_perimeter` / `select_uv_edge_loop` (cmb003 "Convert To" UV items) are real bmesh UV-graph helpers keyed off a single UV-boundary test (a mesh-open edge or a UV seam where an edge's two faces assign different UVs to a shared vert). The `_SELECTION_CONFIG` UV category gains `Back-Facing` / `Front-Facing` (object-level signed-UV-area winding, matching the sibling Texture Borders / Unmapped leaves). Verified headlessly (`test_selection.py`) on a smart-projected cube (all-seam) and a seamless grid, incl. UV Edge Loop truncating a loop at a real re-unwrapped seam (uv 3 vs native 6).

- **2026-07-12 — Channels: the single/multi-object toggles are color-coded from session start and can no longer drift apart** (mirror of mayatk, same date). The root "state icons start theme-colored until first clicked" bug was upstream (uitk's first theme sweep wiped explicitly-colored state icons — see uitk's CHANGELOG); the option-box actions here benefit with no code change. The compact-footer target button is rewired onto the centralized mechanism: same state dicts as the txt001 option-box action (shared `_target_toggle_states()`) passed to `footer.add_action_button(states=...)` — color-coded and mode-correct at creation; the manual `_update_compact_btn_state` re-tint dance and `_on_footer_compact_btn_clicked` are deleted (this panel's extra startup/compact-toggle re-tint calls existed precisely to paper over the missing initial sync). `_set_single_object` now syncs both toggle widgets to the controller via `_sync_target_toggles()` (the option-box action is kept as `self._target_action`); leaving compact routes through `_set_single_object(False)` instead of resetting the controller behind the widgets' backs. `compare_panel_surface.py --panel channels`: 0 untriaged, summary unchanged.

- **2026-07-12 — Channels: *Compact View* and *Auto-fit Window* now default ON** (mirror of mayatk). Both header-menu checkboxes ship checked. Auto-fit is pull-based (read on every refresh); compact view is push-only, and state-restore only re-emits `toggled` when the stored value differs from the default, so first launch would show the box checked but the effect unapplied. Added `_sync_compact_default()` (Type-column-style pull reconcile) called from `header_init` and the top of `_refresh_table`. Previously-toggled preferences are preserved by restore. `compare_panel_surface.py --panel channels`: 0 deltas.

- **2026-07-12 — Shots-stack review fixes: scene-swap store invalidation (CRITICAL), manifest behavior guards, sequencer edit-scoping, tube-rig b004 end-control routing.** A full review of the shots/tube-rig/UV changeset surfaced one critical and several correctness bugs — all fixed with fail-first repros + pinning tests:
  - **Scene-swap invalidation now actually fires** (critical). `BlenderScenePersistence` gains Maya-parity scene wiring (`_install_scene_jobs`/`remove_callbacks`/`_on_scene_changed` via the `@persistent` `ScriptJobManager` `load_post` master): the three panels' `add_invalidation_listener` registrations had NO producer — nothing called `_notify_invalidated()`/nulled `_active` on File ▸ New/Open, so an open sequencer's non-persistent `bpy.app` handlers died for the session and, worse, the previous file's still-active store wrote its OLD shots JSON into the NEW scene's `shot_store` property on the next save (cross-scene corruption; repro'd pre-fix: stale_active/listener_dead/leaked all true). `pythontk.ShotStore.clear_active()` tears the backend's job down via the new `remove_callbacks`. Lifecycle pinned in `test_shots_adapter.py` (+7: real `wm.read_homefile` — listener fires once, store nulled, fresh store loads, no leak, teardown drops the job).
  - **Manifest behavior application mirrors mayatk's `apply_to_shots` guards** (`_shot_manifest.py`): locked shots are never modified (the panel help's promise), zero-duration shots skip, an object with existing keys in range is skipped (rebuilds are idempotent — never overwrite user animation), an already-placed VSE strip is never re-added (every Build previously stacked `name.001` duplicates; `reapply_object` also placed one strip PER behavior name), and `_verify_behavior` routes `verify.mode: audio_clip` templates to the strip check (`_audio_exists`) — built audio steps previously assessed `missing_behavior` forever because strip names were looked up in `bpy.data.objects`. Returns mayatk-shaped `{"applied","skipped","failed"}` records with per-entry try/except (a failing entry no longer aborts the batch mid-build; the footer's `failed` tally is real now, and its skip label reads "already keyed/placed"). Dead `gap_threshold` parameter threading removed (`store.detect_regions()` owns the threshold). `test_shot_manifest.py` 24/24 (+7: audio idempotency/verify, locked/zero-duration/existing-keys guards, failed bucket).
  - **Sequencer edit-scoping + handler hygiene** (`shot_sequencer/`): `on_keys_moved` is two-pass per fcurve (chained batch moves like `[(10,12),(12,14)]` stacked both keys on 14); whole-object "Delete Key" scopes to the clip's TRANSFORM fcurves (custom-prop/constraint/modifier curves survive); quaternion channels get W-first, euler-distinct labels (`quatRotate[WXYZ]` — the X-first shared table mislabeled every quat channel by one axis and label collisions made sub-row edits drag both rotation families); `depsgraph_update_post` is scoped to real keyframe edits via an `Action`-in-`depsgraph.updates` filter + an `is_animation_playing` guard (probed on 5.1: key insert → `Action`, selection click/transform-drag → not), so a selection click can no longer silently merge objects into the active shot; `frame_change_post` skips render jobs (`bpy.app.is_job_running("RENDER")` + scene-identity check — no Qt calls off the GUI thread); re-init tears down the prior controller's handlers/listeners (`ui._sequencer_controller` stash) instead of leaking them until `ui.destroyed`; easing-interpolation keys degrade to a straight preview segment (their handles aren't evaluation control points); `extract_attributes` resolves via `bpy.data.objects`. The false "Blender auto-removes a raising handler" rationale in two docstrings is corrected (it doesn't — it prints the traceback; the try-wraps stay). **View-mode option ported** (`_setup_shot_nav` now mirrors mayatk: Current→Adjacent→All cycle + "+" new-shot + refresh options — `_visible_shots`/`_set_view_mode` were fully built but unreachable, while the help text advertised them); the *Show Internal Holds* dead state is deleted, not ported — it rides the ledgered no-hold-sub-splitting divergence. `test_shot_sequencer.py` 65/65 (+6).
  - **Tube-rig b004 routes through the end control** (`tube_rig.py`): `constrain_end_with_falloff` auto-resolves the end's hooked control Empty (new `_end_control` — Spline IK constraint → driver curve → hook `vertex_indices`, scene-derived like mayatk's) and `CHILD_OF`-binds it to the anchor, so the whole end assembly follows; the `control=` parameter was dead (never passed) and on a spline rig only the falloff-weighted skin dragged while the IK curve stayed put. `RigUtils.apply_falloff_weights` rescales ONLY deform-bone groups (Maya `skinPercent` touches skinCluster influences only — it previously scaled every vertex group, corrupting cloth-pin/selection-set data in the radius). `test_tube_rig.py` 42/42 (+4: spline-rig CHILD_OF routing + non-deform-group guard).
  - **Suite/infra**: `test_blender_ui_handler.py`'s PANELS allowlist gains the three shots panels (the "no spurious panels" check was RED — 186/187; now 190/190; the sweep-count claims quoted in the entries below are superseded by the totals here); `Run-Tests.ps1` now also aggregates the `*_slot_check.py` harnesses (they emit the same sentinel but never ran in CI); `pyproject.toml` floors `pythontk>=0.8.85` (this changeset imports `Weights`/`TaskFactory`/`process_stream`/`engines.shots` — absent from 0.8.84); scene-exporter `_NEEDS_SHOTS` tooltip states the true remaining gap (the FBX-take projection, not "Shots port unstarted"); `reapply_object`'s one-sided public name is ledgered in `_shot_manifest.py`'s divergence list; stale "later phases" package docstrings and a wrong controller-class reference corrected.

- **2026-07-12 — Curtain drape engine vendored in-package: `edit_utils/_curtain_drape.py`.** `CurtainDrape` moved down from pythontk, which keeps only the general primitives it composes — the new `ptk.RailSurface` rail→(u,v)-grid primitive plus `Polyline`/`MathUtils`/`BandLimitedNoise` (a single tool's domain math isn't a pythontk engine; see pythontk's CHANGELOG same date). `create_curtain`/`CurtainSlots` now build from the local class — same parameters, same drape, no behavior change. Code-identical twin of `mayatk.edit_utils._curtain_drape`, drift-guarded by extapps' `test_vendor_sync.py` (`TestCurtainEngineDccSync`); the drape math itself is pinned once in mayatk's `test_curtain_drape.py` (12) — one test home per vendored engine, like the Marmoset/Substance copies. **Verified** in a fresh headless Blender 5.1: `test_curtain.py` 25/25 (identical 1921-vert build through the vendored twin, drift 0.00e+00, rig checks green).

- **2026-07-12 — `TaskFactory` + blendshape `Weights` de-vendored to pythontk; Substance connection composes the shared stream primitives.** The scene-exporter `TaskManager` now subclasses `pythontk.TaskFactory` (shared reflection-based task/check pipeline, formerly vendored). Blendshape `creator`/`slots` use `pythontk.Weights` (morph weight math, formerly `blendshape_animator.weights`). `substance_bridge/connection.py` sheds its bundled stream machinery — `OutputStream`/`ProcessReader`/`LogTailer` now come from `pythontk.core_utils.process_stream` (the app-agnostic mechanism); the Painter-specific `SubstanceConnection` shell + `substance_rpc` client + `templates/` and the whole Marmoset engine stay vendored **by design** (app-specific code fails pythontk's app-agnostic charter), kept code-identical with mayatk's copies by extapps' `test_vendor_sync.py` guard. Verified in a fresh Blender: `TaskManager` subclasses the shared `TaskFactory`; `Weights` resolves to pythontk.

- **2026-07-11 — Shot Sequencer PANEL ported (Blender) — the Shots trio is complete.** `anim_utils/shots/shot_sequencer/` gains the full NLE-style timeline panel over the shared `uitk` `SequencerWidget`. **Engine (`ShotSequencer`) expanded** with the surface the panel drives — accessors (`shots`/`sorted_shots`/`markers`/`hidden_objects`/`is`+`set_object_hidden`/`shot_by_id`/`define_shot`), key-motion (`move_object_keys`/`move_stepped_keys`/`move_object_in_shot`), key-scaling (`scale_object_keys`/`resize_object`/`set_shot_duration`/`resize_shot`), `_find_keyed_transforms`, `collect_object_segments`, and `reconcile_all_shots` (a no-op in Blender: flat unique names never go stale like Maya DAG paths). Blender needs none of Maya's cut-and-recreate key dance (`keyframe(timeChange=)` won't slide past an occupied frame) — every move is a direct `keyframe_point.co[0]` write, so the ports are simpler. **Display-data layer** (`segment_collector.py`): `collect_segments`/`active_object_set` delegate to the engine; `extract_attributes`/`build_curve_preview` read Blender fcurves (a keyframe's handles ARE the bezier control points in frame/value space — simpler than Maya's angle+weight reconstruction). **Four controller mixins** ported (`marker_manager` verbatim — DCC-agnostic; `shot_nav` — selection + `scene.frame_start/end`; `gap_manager` — `undo_chunk` swap; `clip_motion` — fcurve key edits + `curves_for_attr`/`scale_attribute_keys`). **Controller + slots** (`shot_sequencer_slots.py`, `ShotSequencerController` + `ShotSequencerSlots`): Maya's OpenMaya undo/redo/time/keyframe callbacks become `bpy.app.handlers` (`undo_post`/`redo_post`/`frame_change_post`/`depsgraph_update_post`, the last debounced + all error-guarded so a raise can't auto-unregister them); `cmds.currentTime`→`scene.frame_current`; the widget bridge (`_sync_to_widget`→tracks/clips/decoration) + all 34 widget signals wire to the mixin/controller handlers; header menu built 1:1. Ledgered follow-ups: sequencer **audio-track display** + **move-to-shot** sequence grouping deferred (the object-animation timeline is fully wired; matches the engine's "no audio shifting" note). Verified: `test_shot_sequencer.py` **58/58** live (engine + display-data), `test_shot_sequencer_panel.py` **8/8** (venv — real discovery+compile, all 34 signals wired to real slots), parity sweep `shot_sequencer` **0 untriaged deltas**; full sweep PASSES (Shots trio out of open work).

- **2026-07-11 — Shot Manifest PANEL ported (Blender).** `anim_utils/shots/shot_manifest/` gains the full UI mirror of mayatk's manifest panel: `manifest_data.py` (constants + `format_behavior_html`, palette from the shared `pythontk` engine, `try_load_blender_icons` → `None` with uitk named-icon fallback), `table_presenter.py` (`ManifestTableMixin` — the tree population/formatting/assessment-colouring/behavior-label surface; flat `_leaf_name`, behavior re-apply routed through the adapter), and `shot_manifest_slots.py` (`ShotManifestSlots`/`ShotManifestController` — CSV load + mapping combo + range editing + build/assess over `BlenderShotManifest`). Scene-change handling uses `BlenderShotStore`'s invalidation registry (not Maya scriptJobs); `cmds.currentTime`/`objExists`/`select`+Outliner-reveal → `scene.frame_current`/`bpy.data.objects`/object-selection; undo via `btk.undo_chunk`; detection delegates to `store.detect_regions()`. Added `BlenderShotManifest.reapply_object(shot, obj)` (the per-object "Apply [behaviors]" primitive: opacity/visibility fades + VSE audio in one undo step). **Fixed** a latent headless bug in `_shots._active_scene`: an unguarded `import bpy` raised instead of honoring its documented "``None`` if headless" contract, which would crash the manifest's first-show auto-populate under `--background`. Verified: `test_shot_manifest_panel.py` **7/7** (venv, real discovery+compile path), `test_shot_manifest.py` **17/17** (live Blender 5.1), `test_shots_slots.py` **10/10** (regression), parity sweep `shot_manifest` **0 untriaged deltas**.

- **2026-07-11 — TubeRig FULLY PORTED: `b004` end-constraints-with-falloff + `enable_twist` (the last two `pending` items).** `b004` *Add End Constraints* needed "a new per-vertex weight-blend algorithm that doesn't exist here" — now two reusable `RigUtils` primitives: **`add_bone`** (single-bone graft onto an existing armature) and **`apply_falloff_weights`** (the Blender/vertex-group analogue of Maya's `SkinUtils.apply_falloff` — a vertex within `radius` of a center gets `w = 1 - d/r` on the target group and its existing influences scale by `(1-w)`, so weight REDISTRIBUTES rather than adds; Maya's `skinPercent` semantics, crease-free at the boundary). `TubeRig.constrain_end_with_falloff` grafts an anchor bone that `COPY_LOCATION`-tracks an external anchor object + paints the falloff; the `b004` slot (+`.ui` button) resolves the armature + two anchors, finds the bound mesh, and assigns each anchor to its nearest tube end by proximity (Blender selection order is unreliable). **`enable_twist`**: Blender's Spline IK provably ignores the driver curve's point tilt (probed), so twist is the one toggle with no native scale-mode — `TubeRig.add_twist` builds a tip roll-control bone + a per-deform-bone `COPY_ROTATION` (local Y, mix `ADD`, constant `influence = 1/N`) that composes AFTER the Spline IK solve; equal local increments accumulate LINEARLY down the parented chain, so rolling the control twists the tip a full turn while the start stays put. DRY: FK + twist share a new `TubeRig._hidden_control_shape`. Verified headlessly on Blender 5.1: `test_tube_rig.py` **37/37** (falloff deform + exact redistribution invariant; a 90° twist roll → tip cross-section 81° vs start 19° = progressive; + the OFF gate), `tube_rig_slot_check.py` **11/11** (b004 slot resolution incl. mesh-selected-too, anchor-driven deform), `test_blender_ui_handler.py` 187/187. Parity sweep: tube_rig **0 pending** — the panel is fully ported (only same-named PushButton/QCheckBox class deltas remain, review-only).

- **2026-07-11 — TubeRig granular step-workflow: Create Joints / Create IK-Controls / Bind + Reverse (b001/b002/b003/chk000, previously ledgered `pending`).** The Maya panel exposes tube-rigging as four hand-driven steps a rigger runs in sequence, not just the one-shot build; the Blender port shipped only the HYBRID one-shot. Added a **Manual Steps (Spline)** group to `tube_rig.ui` (chk000 Reverse + b001–b003) and the `TubeRigSlots.b001/b002/b003` methods over the already-existing engine (`create_joint_chain` = Step 1, `attach_spline_rig` = Step 2, `RigUtils.bind_armature` = Step 3), each resolving from the live selection like the buttons do — b002 resolves the chain via a **mesh-less `TubeRig`** reusing the armature's root (no cross-call rig registry, matching the port's model). New `tube_rig_slot_check.py` harness **9/9** in a fresh headless Blender 5.1: b001 builds the chain (mesh left unbound) → b002 adds Spline IK + controls → b003 binds → **the granular-built rig deforms** (control move bends the mesh, max-x 0.50→3.00), chk000 reverses the chain start, and empty-selection guards message cleanly. `test_tube_rig.py` 26/26, `test_blender_ui_handler.py` 187/187, parity sweep exit 0 — tube_rig open-work 6→2 (only b004 + chk_twist remain). **b004** (constrain both ends to anchors) still `pending`: it needs a new per-vertex falloff weight-blend algorithm, not just a wire-up. (The b001–b003 PushButton / chk000 QCheckBox class deltas vs Maya's QPushButton are review-only, same accepted cross-DCC pattern as b000.)
- **2026-07-11 — TubeRig Spline deform toggles: Stretch / Squash / Volume / Auto-Bend (previously ledgered "no Blender engine counterpart yet").** The Spline-IK hose strategy gained the Maya deform systems, and the "needs a live Blender / no counterpart" deferral was refuted: Blender's Spline IK collapses Maya's per-node deform graphs to native constraint options — **Squash/Volume** = XZ scale `INVERSE_PRESERVE`/`VOLUME_PRESERVE` (Maya's two bools map onto the one enum via `_xz_scale_mode`), **Stretch** = Y `FIT_CURVE` — and **Auto-Bend** is one `add_distance_driver` on the mid control's `delta_location` (the middle bulges as the ends compress; mirrors Maya's `setup_auto_bend` multiplyDivide). Wired through the shared `attach_spline_rig` (the same engine path the granular b002 step drives, so one-shot and step builds can't diverge — `SplineIKStrategy.build` now delegates to it, DRY). **Deform correctness is verified headlessly** via the evaluated depsgraph (`test_tube_rig.py` +4: stretch shrinks the cross-section 0.492→0.386 with volume-preserve / constant without; compression bulges the mid 0.50→1.71 / not when off) — 25/25 in a fresh headless Blender 5.1. `chk_twist` stays `pending`: Blender's Spline IK does **not** propagate driver-curve tilt to bone twist (probed — endpoint tilt in every twist mode leaves the tip bone unrolled), so twist needs a custom bendy-bone / roll-driven chain, not a native toggle. Parity sweep exit 0; tube_rig open-work 9→6 (chk_squash/volume/auto_bend → `replaced → spec`). Granular step buttons (b001–b004/chk000) remain — their engine already exists + deforms (probe 5/5).
- **2026-07-11 — UV ShellXform panel reaches full Maya parity: Align / Orient / Gather / Randomize (previously ledgered `na`).** The Blender `shell_xform` panel shipped only the shared move/flip/rotate/straighten/mirror/distribute subset; the Maya-only **Align** (min/avg/max + linear), **Orient** (shells + to-edge), **Gather**, and **Randomize** groups were ruled `na` ("no bpy analogue"). An inverse audit plus live headless probing (Blender 5.1) overturned all 11. Four new `uv_utils` engine helpers — `align_uvs(objects, axis, mode)` (bmesh; `avg` = arithmetic mean of the selected UVs — the natural reading of Maya's avgU, exact averaging owed a live-Maya check — `linear` projects the selection onto its endpoint line, both selection-scoped in Edit mode), `gather_uv_shells` (bmesh floor-subtract per island), `orient_uv_shells(to_edge=…)` (native `uv.align_rotation` AUTO/EDGE), `randomize_uv_shells(seed=…)` (native `uv.randomize_uv_transform`) — are registered on `btk.*` + `UvUtils`, wired to the 11 `ShellXformSlots` buttons, with the Align/Orient `.ui` `CollapsableGroup`s mirrored byte-for-byte from the Maya twin. Tests: `test_uv_utils.py` +11 (every op incl. Edit-mode partial-selection scoping + seeded determinism) and a new `shell_xform_slot_check.py` slot harness (11/11 — proves each button's axis/mode wiring, e.g. a mislabeled `align_v_min` moving U would fail) in a fresh headless Blender 5.1; `test_blender_ui_handler.py` 187/187; parity sweep exit 0 — shell_xform now matches 1:1 (its 11 Maya-only ops had been ruled `na`, not `pending`, so realizing them doesn't move the open-work-item count). Still owed: a live-Blender panel **layout** pass (button sizing/grouping in a real UV workspace) — can't be verified headlessly.
- **2026-07-11 — Select-by-Type gains the Clusters + Wires leaves (Maya parity; previously ledgered `na`).** The shared `list000` "Select by Type" menu was missing Maya's Clusters and Wires deformer leaves — ruled `na` ("Maya deformer node, no selectable Blender Object") but reachable via the same modifier-carrier idiom the Dynamics leaves already use. `Selection._SELECTION_CONFIG` now maps **Clusters** (Animation category, matching Maya) → meshes carrying a **Hook** modifier and **Wires** (Dynamics category) → meshes carrying a **Curve** modifier, both through the existing `_select_by_modifier` (Blender's Hook = control-object-driven cluster deformer; Curve modifier = curve-driven wire deform). The tentacle `list000` slot builds its menu from `get_selection_categories()`, so no slot change was needed. `tentacle/docs/parity_map.py` reclassifies both leaves `na → done-elsewhere` with the mapping (the static sweep doesn't track `list000` leaves). Tests: `test_selection.py` +5 (Clusters→Hook-carrier, Wires→Curve-carrier, plain-mesh excluded by both, category placement ×2) — 66/66 in a fresh headless Blender 5.1; parity sweep exit 0. No public-surface change (registry untouched).

- **2026-07-11 — `DataNodes.dump()` / `format_dump()` — mirror of mayatk's same-day read/inspect side.** Same names + behavior over Blender custom properties: `dump(decode=True)` walks *every* custom property on the `data_internal` / `data_export` Empties (skipping `_`-prefixed internals like `_RNA_UI`), groups them by object, best-effort JSON-decodes **string** values, and coerces **non-string** values (int/float/`IDPropertyArray`/`IDPropertyGroup`) to a JSON-serializable form via `_jsonable` — keeping non-string channels the way mayatk keeps the audio tool's per-track enums, rather than dropping them. `format_dump()` is the pretty-JSON text form (`default=str` guard), falsy when nothing is stored. Backs tentacle's Blender Scene ▸ Scene Metadata button (parity with Maya). Tests: `test_node_utils.py` +8 dump checks (grouping, decode on/off, cleared-channel skip, non-string channel kept, empty-scene contract, mixed-type JSON round-trip) — PASS in a fresh headless Blender 5.1. Registry regenerated.

- **2026-07-10 — Promoted to a first-class ecosystem package: public repo, PyPI release line, and a hard `tentacle` dependency.** blendertk had mirrored mayatk's API and shipped the tentacle Blender engine for months as a private, off-chain repo; it now releases through the same `m3trik/push.ps1` cascade as the rest of the ecosystem. Changes: the GitHub repo is public; `blendertk` is added to push.ps1's strict/release sets and the auto-cascade graph (it releases in parallel with mayatk — after it, before tentacle — since both consume `uitk`); `tentacle/pyproject.toml` now pins `blendertk>=…` (the runtime import in `slots/blender/*` + `tcl_blender.py` was previously unpinned, install-by-convention); the internal pin-sync map gains `blendertk → {pythontk, uitk}` and `tentacle → …, blendertk`; a `publish.yml` / `bump-dev.yml` / `static-analysis.yml` workflow trio (the last AST-only — pyflakes false-positives on blendertk's deferred `bpy`/Qt imports). Versioning: the local line resumes from the last-published `0.5.0` (a 2023 pre-rewrite release), so the cascade's auto-bump publishes the greenfield rewrite as `0.5.1`, safely above the placeholder so `pip` resolution picks the real package. `unitytk` stays off-chain (floor-pinned, not cascaded), unchanged.

- **2026-07-10 — Color ID panel: fixed swatch sizing + tightened grid layout (mayatk parity).** The 12 color swatches gain explicit min/max sizes and the grid gets explicit margins/spacing, mirroring mayatk's paired `.ui` change so the palette lays out consistently. `.ui`-only — no widget objectName, signal, or slot changes; parity sweep clean.

- **2026-07-10 — Adversarial-review pass: shadow rig parity fixes.** `ShadowRigSlots` no longer stacks raw `clicked.connect`s on `b001`/`b002` — the switchboard auto-wires by objectName, so one click of "Bake to Keyframes" ran twice (bake, then a contradictory "no live drivers" popup); mayatk removed the same connects this same day. The option tooltips lost when the `.ui` was stripped for XML parity (mayatk moved them to code) are ported as `_init_tooltips()` (deferred `fmt` import — headless Blender ships no Qt). `b002` no longer falls back to baking (destructively de-rigging) every plane in the file when a non-empty selection contains none, and the `chk_combine` tooltip describes the actual behavior (both mirroring mayatk's same-day fixes). Lightmap manifest warns when a committed lightmap layer no longer exists instead of silently publishing uvIndex 1 (parity with mayatk). `naming` added to the gesture-scoped pin guard in `test_blender_ui_handler.py` (it gained the pin header this same day but wasn't covered). Macros module docstring updated to the real startup path (`TclBlender` → `Macros.apply_saved_macros()`; no `tentacle_startup.py` ever existed).

- **2026-07-10 — Macro hotkeys set via presets now actually fire: `set_macro` self-registers the `btk.macro` dispatcher operator; `apply_bindings` is per-entry resilient (mirror of mayatk's same-day fix).** The keymap items `set_macro` creates all target the `BTK_OT_macro` operator, but only `set_macros` (the spec-string batch API) ever called `_ensure_operator()` — the preset path (`apply_saved_macros`/panel load → `apply_bindings` → `set_macro`) bypassed it, so a fresh session applying a preset produced keymap items that exist but do NOTHING on keypress ("the hotkeys do nothing"). `set_macro` now calls `_ensure_operator()` itself (idempotent). `apply_bindings` also gains the mayatk-mirrored per-entry try/except so one bad chord logs and continues instead of aborting the rest of the preset. Regression pinned in `test_macros.py` §12c: unregister the operator, bind via bare `set_macro`, assert it re-registered — fails pre-fix; suite 50/50 in a fresh headless Blender 5.1.

- **2026-07-10 — RizomUV bridge: send-flow texture dedupe + header-button objectName consistency (mirror of mayatk's same-day bridge pass).** `_texture_loads` now dedupes texture paths order-preserving before emitting `ZomLoadTexture` calls (shared materials previously produced one call per assignment), and the header menu's UV-Editor button objectName gains its missing underscore (`btn_open_uv_editor`, matching its `btn_*` siblings in both DCCs). mayatk's pass also rebuilt the round-trip auto-unwrap presets (weld-first + Mosaic organic segmentation, live-probed on RizomUV 2020.1) — that pipeline remains unported here (ledgered; `send` is still the only wired preset). Suites: `test_bridges.py` PASS (fresh headless Blender 5.1), `test_blender_ui_handler.py` 186/186.

- **2026-07-10 — Shadow Rig → engine hand-off is plug-and-play through the standard export pipeline (mirror of mayatk's same-day carrier pass).** New `ShadowRig.refresh_export_metadata()` publishes `{"version", "planes": [{name, texture, intensity}]}` onto the shared `data_export` carrier via `btk.DataNodes` — at create/bake, per the blendertk convention (producers publish at authoring time; Blender has no before-FBX-export hook), and cleared when the file has no shadow planes. The record's texture resolves from the material's `shadow_tex` image node (SSoT). Exporting through the Scene Exporter ships the carrier via the existing `export_data_node` task; unitytk's new `ShadowPlaneController.cs` consumes the channel identically for Maya and Blender exports (unlit-transparent material + shadow flags set up automatically on import; round-trip verified against a licensed batch Unity from the Maya side — see mayatk/unitytk CHANGELOGs). Suite 53/53 (new: carrier publish with correct name/texture/intensity record, plane-less refresh clears the channel), fresh headless Blender 5.1; `test_blender_ui_handler.py` 186/186; parity sweep exit 0; registries regenerated.

- **2026-07-10 — Lightmap Baker: `commit_lightmap` gained the `uv_rects` mirror of mayatk's same-day standalone-atlas change.** mayatk's `pack_atlas` now repacks each object's atlas rect into its lightmap UVs (plug-and-play in any engine, no scaleOffset binding); the applied rect rides the commit as `uv_rects` and is recorded on the marker (`uvRect`) purely for revert bookkeeping — never published to the manifest, which keeps an identity `scaleOffset`. blendertk mirrors the parameter + marker semantics so the shared surface stays branch-free (`scale_offsets` remains the legacy engine-applied hook); the atlas packer itself (grouping + EXR assembly + `bmesh` UV repack/restore) stays ledgered as unported and the panel item disabled. unitytk-coupling language removed from docstrings/help (the Unity helper is an optional native-slot binder, not a dependency). Suite grew the mirror checks: `uvRect` recorded for a non-identity rect, no key for identity, manifest scaleOffset stays identity, clean revert — 28/28 green (real Cycles bake, fresh headless Blender 5.1).

- **2026-07-10 — Naming panel (mirroring mayatk): Strip Chars `Trailing` checkbox → `Leading`/`Trailing` combo (`cmb002`); restored the header pin/auto-hide window behavior; Find/Rename fields no longer persist between sessions.** (1) Strip Chars end-to-strip is now an explicit two-item choice; default `Trailing` preserves the prior checked-by-default state, and `tb002` derives `trailing = currentText() == "Trailing"`. Engine `strip_chars` unchanged. (2) `header_init` gains the `config_buttons("menu", "collapse", "pin")` call the sibling gesture-scoped `edit_utils` panels (mirror / cut_on_axis / bridge) already have — restores the pin button + auto-hide instead of the default hide button. (3) `txt000`/`txt001` (Find / Rename) set `restore_state = False` so their text starts empty each session. Parity sweep exit 0.

- **2026-07-10 — Shadow Rig realism + engine-export overhaul (mirror of mayatk's same-day pass) + an orbit-mode rotation sign bug only Blender had.** All the mayatk realism/usability changes land here with branchless drivers: **projected ground anchor** (`k = (Lz−G)/max(Lz−Cz,0.1)` clamped — the shadow slides away from the light as the target rises), **objectHeight-proportional stretch** with keyable `maxStretch` (new stamped `objectHeight` prop), keyable `fadeHeight` **rise fade** in the opacity driver, **light-view silhouette** for `axis='auto'`/`'light'` (points rebased into the light's frame, u-axis `(dy,−dx)` — re-derived for Z-up, not transliterated), `GROUND_OFFSET` unification (0.01; the static Z previously disagreed with the build height), and **bake**: new `bake()`/`bake_planes()`/`find_shadow_planes()` — a context-free visual bake (per-frame evaluated sampling → driver removal → `keyframe_insert`; no `bpy.ops.nla.bake`) that also freezes the shader-side opacity Value node, surfaced as the panel's **Bake to Keyframes** button (`b002`, `.ui` twins kept byte-identical). **Blender-only bug found during the port review:** orbit mode's rotation `atan2(Cx−Lx, Cy−Ly)` — the naive transliteration of Maya's (correct) `atan2(dx, dz)` — pointed the silhouette head along a bearing **mirrored across the Y axis** (Maya-Y-up→Blender-Z-up is a reflection, so orientation formulas need re-derivation: `R_z(t)` sends +Y to `(−sin t, cos t)`, giving `t = atan2(Lx−Cx, Cy−Ly)`); pinned by a headless check asserting rz ≈ 2.356 (not the mirrored −0.785) for a (5,5) light. **Structural:** the orbit plane is now built with its origin on the **heel edge** (`_build_plane(origin='edge')`) so rotation/scale pivot the anchor directly — world-space result identical to Maya's center-pivot + offset, and it keeps every driver expression under Blender's **255-char cap** (the new fragments are written compact for the same reason; stretch's location driver inlines the full scale formula rather than reading the plane's own driven scale channel, which would be a same-ID depsgraph cycle). Suite rewritten against hand-derived reference values (stretch 1.4545, anchor slide −0.0455→−1.75 on rise, riseFade 0.5, orbit rz 2.3562) + bake round-trip (keys land, drivers stripped, pose preserved) and a Blender-5.x layered-action fcurve reader — **49/49** (fresh headless Blender 5.1); `test_blender_ui_handler.py` 186/186 under the `.venv`; parity sweep `--all --write` exit 0; registries regenerated.

- **2026-07-10 — Lightmap baker: `commit_lightmap(intensity=)` now applies into the texels (full mayatk parity; the 2026-07-09 not-applied warning is gone).** The blocker was an unverified float-EXR rewrite path in Blender's runtime — now probe-verified in headless Blender 5.1 (`Image.pixels` is the raw float buffer, so linear HDR data — values > 1 included — round-trips losslessly through load → scale → `save()` as OPEN_EXR within 1e-3). New `_apply_intensity`: bpy-native (no cv2 dependency), scales RGB once per unique file (atlas-shared files dedup by abspath), leaves alpha untouched, and never fails the commit (per-file warn + continue). Suite grew the mirror checks of mayatk's: shared file scaled exactly once (0.25 → 0.5, not 1.0), manifest records the intensity for both objects, clean revert — 24/24 green (real Cycles bake, fresh headless Blender 5.1).

- **2026-07-09 — Lightmap baker: manifest publishes the real UV-layer index (mirror of mayatk's same-day contract fix); intensity ≠ 1 now warns loudly.** `_publish_lightmap_metadata` previously hardcoded `uvIndex: 1`; it now publishes the lightmap layer's actual index from `obj.data.uv_layers` and warns when it isn't 1 (Unity's native lightmaps only sample uv2) instead of hiding a mis-ordered layer. No duplicate-name check was ported — unlike Maya DAG leaves, Blender object names are globally unique, so the Unity join key can't collide within one export (divergence noted in-code). `commit_lightmap(intensity=)`: mayatk now applies a non-1.0 intensity into the texels at commit (Unity ignores the manifest field — see mayatk/unitytk CHANGELOGs same date); blendertk does not yet mirror that texel scaling (needs a verified float-EXR rewrite path in Blender's runtime), so a non-1.0 value logs a loud not-applied warning rather than silently recording a multiplier nothing will ever apply. Note the mayatk change to *per-object* white-carding converges on blendertk's existing semantics — the Cycles diffuse bake (no COLOR pass) always transports light off real neighbor materials, so no blendertk-side change was needed there. Suite green: `test_lightmap_baker.py` 21/21 (real Cycles bake, fresh headless Blender 5.1).

- **2026-07-09 — Macro Manager: the shipped `default` preset is now all-unbound (mirror of mayatk's same-day change).** The shipped default no longer carries a binding set; loading it clears every macro hotkey, and `apply_saved_macros()` (the `tentacle_startup.py` entry point) with no active preset registers nothing — bindings are opt-in via user presets, re-applied at startup via the `.active` sidecar. Panel help text updated; `test_macros.py`'s shipped-default check now asserts the all-unbound contract (49/49, fresh headless Blender 5.1).

- **2026-07-09 — Gesture-scoped tool windows (mayatk mirror): seven panels pin/auto-hide; UV Transform → "Shell Xform".** Mirrors mayatk 1:1 — `reference_manager`, `color_id`, `exploded_view`, `bridge`, `cut_on_axis`, `mirror`, and `shell_xform` declare a `pin` header button in `header_init` (overriding `BlenderUiHandler`'s blanket `"blendertk"→hide` default) so they auto-hide on marking-menu `key_show` release. The `shell_xform` panel's visible title is renamed **Shell Xform**. New regression guard in `test_blender_ui_handler.py` loads each of the seven panels and asserts a `pin` (not `hide`) header button, driving `header_init` explicitly since the offscreen load skips it (186/186).

- **2026-07-08 — Marmoset + Substance bridges ported (full mirrors of mayatk's live-RPC bridges) + shared `MatManifest`.** New `mat_utils/marmoset_bridge/` (`MarmosetBridge` + `MarmosetBridgeSlots` + `.ui`) and `mat_utils/substance_bridge/` (`SubstanceBridge` + `SubstanceBridgeSlots` + `.ui`): the DCC-agnostic engine/RPC/template halves are vendored verbatim from mayatk; only the produce halves are bpy-native. `mat_utils/mat_manifest.py` (`MatManifest`) — the baked-map metadata sidecar both bridges write — registered in `DEFAULT_INCLUDE` (mirror of `mtk.MatManifest`). Both panels added to `test_blender_ui_handler.py`'s discovery/loadability guards; `docs/STRUCTURE.md` mat_utils row updated to match.

- **2026-07-08 — `.venv` ui-handler harness green end-to-end (179/179): a pre-existing segfault root-caused + four broken-at-load panels fixed.** The harness had been dying with a native access violation before printing its summary, masking every failure behind it. Root cause chain: hdr_manager's deferred `_initialize_ui` (a `singleShot(0)`) raised `ModuleNotFoundError: bpy` inside the Qt event dispatch — an exception escaping a native timer callback hard-crashes PySide6 — and the pump that fired it was uitk's `TextEditLogHandler` force-painting from inside a log emit (fixed uitk-side, see its CHANGELOG). Panel fixes, all honoring the bpy-free-load contract: **hdr_manager** `_sync_ui_to_scene` degrades without bpy; **wheel_rig** `update_rig_name_placeholder` guards its bpy import and `ScriptJobManager._ensure_handler` records subscriptions without a runtime (master handler installs on the next in-Blender subscribe); **shader_templates**/**game_shader** `workspace_dir`/`source_images_dir` became lazy properties (were eager `get_env_info` calls at init — also stale-by-design: they now track the current .blend); **tube_rig**'s `.ui` was still mayatk's old static-toolbox layout while its HYBRID slots expect `cmb_preset`/`txt000`/`wgt_options`/`b000` — broken on every load since it shipped; rebuilt minimal (ledger the twin-`.ui` delta stays with the sweep). Harness fixes: the four never-listed panels (arnold_bridge, hierarchy_manager, scene_exporter, smart_bake) joined the discovery guard; an explicit `processEvents()` fires deferred inits before the option-box checks; and both Qt-half suites (`test_blender_ui_handler`, `test_smart_bake`) now activate uitk's QSettings sandbox — they previously read AND wrote the developer's live `uitk\shared` store (a prior run's toggled Compact View came back as the next run's load-time default).
- **2026-07-08 — Post-review hardening.** (1) `_join_copies` no longer mutates the scene before its abort/skip checks: the selectability probe runs first, and single-user conversion + child reparenting apply only to the objects actually about to be joined (previously a not-in-view-layer abort left reparented children and de-shared datablocks behind; reachable via public `combine_objects`). (2) Scene-exporter `export_data_node`'s permanent hide-clear on the carrier was review-flagged for a post-export restore, but the task-revert timing makes that wrong by construction: reverts fire when `run_tasks` returns, which is BEFORE the FBX write, so a restore re-hides the carrier out of the `use_selection` funnel and drops the metadata again (caught by the hidden-carrier round-trip check). The clear stays permanent by design; the timing constraint is now documented on `_get_revert_method` in both DCCs' factories. (3) Bridge `.ui` template tooltips said "mayatk's" inside the Blender panels — corrected. (4) `pythontk>=0.8.80` dependency floor (the `AssemblySorter`/`match_clouds`/`nn_query` APIs the auto-instancer delegates to shipped in 0.8.80); the auto-instancer now uses the public `PointCloud.nn_query` (promoted from private). (5) `.gitattributes` pins `*.bat text eol=crlf` so CI-built wheels ship the package-manager mirror CRLF (cmd.exe mis-scans goto/call labels in LF-only files).

- **2026-07-08 — Parity ports surfaced by the in-depth Maya→Blender re-audit: HDR Manager importance-sampling Resolution + Channels double3/vector create-type (both were false-`na` ledger entries).** (1) **HDR Manager `spn_resolution`** — was disabled in the `.ui` and ledgered `na` ("Cycles importance-samples automatically"), which the audit disproved: Cycles exposes `world.cycles.sample_map_resolution` gated by `sampling_method='MANUAL'`, the direct analogue of Arnold's skydome Resolution. New engine pair `set_world_importance_resolution(resolution)` / `get_world_importance_resolution()` in `light_utils/_light_utils.py` (`MANUAL`+map-size on a positive value, `AUTOMATIC` on 0/None; Cycles-only, `None` off-Cycles — mirrors the `set_world_ray_visibility` guard), added to `DEFAULT_INCLUDE`. The panel enables the spinbox (`.ui` `enabled` removed → matches Maya's `None`, tooltip rewritten), wires `spn_resolution` → the engine, syncs it in `_sync_ui_to_scene`, and adds its reset button (only `spn_samples` stays disabled — Cycles has no per-world sample count, still `na`). (2) **Channels `create_attribute` gains a `vector` type** — Maya's `double3` compound maps to a 3-element float XYZ array custom prop (`obj[name]=(d,d,d)` + `id_properties_ui subtype='XYZ'`); the table already displayed/keyed/renamed vectors (`_value_type`→`'vector'`), only the create form omitted it. `parse_value` now round-trips the `"(x, y, z)"` cell on edit; the panel combo adds `"vector"` and treats it as ranged (default/min/max), with the duplicated `("float","int")` guard collapsed to one `_ranged` local. Maya's `enum` stays unoffered (`na` — no arbitrary-object Blender analogue). **Verified (fresh headless Blender 5.1):** `test_light_utils.py` 25/25 (+3: MANUAL switch + round-trip + AUTOMATIC restore), `test_channels.py` 56/56 (+5: 3-float default, XYZ subtype+range, `'vector'` typing, `(x,y,z)` format, element-wise edit); both panels import clean under the `.venv` Qt harness; `compare_panel_surface.py --all --write` exit 0 (HDR prop-delta count 3→2 as the `spn_resolution` `enabled` delta clears; Channels combo now `double3`→`vector` rename, ledgered in `parity_map` `DEFAULT_DELTAS['channels_slots']`).
- **2026-07-08 — New: dedicated UV Transform tool (`uv_utils/shell_xform.py` → `ShellXformSlots` + `shell_xform.ui`), mirror of mayatk's, co-located with the uv engine and discovered by `BlenderUiHandler`.** Ships the cross-DCC shared subset of the new UV Transform window — a Move pad (`btk.move_uvs`, one whole UV tile per click), **Flip / Rotate** over a shared angle field (`btk.transform_uvs`), and the **Straighten / Mirror / Distribute** option-box tools (`btk.straighten_uvs`/`straighten_uv_shells`/`mirror_uvs`/`distribute_uv_shells`) — resolving the selection through `btk.selected_objects` (tentacle-independent; Qt-only `uitk` imports and icon setup deferred so the module loads bpy-free). Launched from the tentacle UV panel's new single **Transform** button (`marking_menu.show("shell_xform")`); Pin and Stack stay behind as their own buttons. Maya's align/orient/gather/randomize/select-filter ops have no bpy operator analogue, so the Blender panel omits those groups (ledgered `na` in `tentacle/docs/parity_map.py` `CONTROLS["shell_xform"]`; full parity sweep exit 0). Registered in `test/test_blender_ui_handler.py` (discovery + bpy-free load); API_REGISTRY regenerated. **Verified:** the panel is discovered and `ShellXformSlots` loads with all widgets present under the `.venv` Qt harness.
- **2026-07-08 — Fix: tentacle Blender operations reported "nothing selected" while an object WAS selected — selection was read through a window-dependent context member that is empty from the Qt event-pump.** tentacle drives the Blender slots from `QApplication.processEvents()` pumped inside a `bpy.app.timers` callback (`tcl_blender._QtHost.start_pump`), NOT from a Blender operator — a context in which `bpy.context.window` is frequently `None` (proven live: a fresh GUI Blender with a cube selected reads `bpy.context.window is None → bpy.context.selected_objects == []` and `bpy.context.active_object is None`, while `bpy.context.view_layer.objects.selected == [Cube]` / `.active == Cube` in the SAME frame). `selected_objects`/`active_object` are *screen-context* members (they require a context window); the view layer is window-independent (Blender's data model: an `Object` is the transform, `Object.data` the mesh/"shape" — selection lives on the view layer's object bases, not the screen context). Fix: read selection/active from `view_layer.objects`. `core_utils._core_utils.selected_objects()` (the SSoT reader shared by every co-located tool Slot) rewritten onto `view_layer.objects.selected` via a new window-independent `_active_view_layer()` (falls back to the scene's first view layer); new companion `active_object()` (→ `view_layer.objects.active`) added to `DEFAULT_INCLUDE` (so `btk.active_object` resolves), the `CoreUtils` class, and `tentacle.slots.blender._slots_blender.SlotsBlender` (both readers now delegate to the btk SSoT instead of duplicating the `bpy.context.*` read). Every remaining fragile `bpy.context.selected_objects` / `bpy.context.active_object` read reached from the pump context was swept onto the robust readers: tentacle slots (`cameras` new-camera lens, `selection._edit_mesh`+`tb001`, `uv` snapshot, `nurbs` deselect loops) and the blendertk engines/slots (`edit_utils.macros` viewport/edit/selection/animation macros ×13, `edit_utils._edit_utils` `objects=None` fallbacks ×5, `edit_utils.{bridge,bevel,selection}`, `core_utils.preview`, `core_utils.diagnostics.transform_diag`, `core_utils.auto_instancer`, `xform_utils`/`uv_utils`/`anim_utils` save-restore + fallbacks, `mat_utils.{render_opacity,shader_templates,texture_baker,image_to_plane}`) — the save/restore and per-object select cases mattered beyond the empty-selection message: a failed "deselect all" from the pump context would leave multiple meshes selected and silently turn a per-object UV `smart_project`/bake into a multi-object edit. **Verified:** reproduced the divergence and then the fix end-to-end in a fresh GUI Blender 5.1 (window=None: old reads `[]`/`None`, `btk.selected_objects()`==`['Cube']`, `btk.active_object()`==`Cube` — PASS); permanent regression guard added — new `test/test_core_utils.py` reproduces the exact failing condition headlessly via `bpy.context.temp_override(window=None)` (asserts the raw `bpy.context` members DO go empty there while the btk readers stay correct), so a reversion fails CI; full headless suite green (all 61 suites, fresh Blender 5.1 each). **Follow-up (same context probe, worse symptom): `bpy.context.screen` is ALSO `None` from the pump context, so the viewport-toggle slots' `context.screen.areas` loops (display: normals/soft-edge/component-ID overlays, material override, UV distortion; selection: Ignore Backfacing) crashed with `AttributeError` instead of mis-reporting** — proven in a fresh GUI Blender (`'NoneType' object has no attribute 'areas'` under `window=None` while the fix returns the real viewport). New window-independent `get_areas(area_type)` (→ `window_manager.windows[*].screen.areas`, the route `get_view3d_context`/`set_viewport_tool` already proved works from the pump; all-windows is a deliberate superset of the current screen — the display toggles document driving every viewport in lockstep) replaces all six loops; `set_viewport_tool`'s inline redraw loop reuses it (DRY). Empirically ruled OUT (probed, no change needed): `context.scene`/`view_layer`/`collection`/`tool_settings`/`mode` all survive `window=None`, so the widespread `bpy.context.collection` link-target fallbacks are safe; and `view_layer.objects.selected` was confirmed semantically identical to `context.selected_objects` (hidden objects: `hide_set` deselects, `hide_viewport` drops from both reads — no divergence). `test_core_utils.py` extended with the `get_areas` contract (window=None yields the identical area list; even `--background` keeps one window+screen, so the contract is window-independence, not emptiness). Registries regenerated (`active_object` + `get_areas` are the two public additions).
- **2026-07-08 — Blender→Unity FBX metadata hand-off actually ships: real `export_data_node` task + custom-property export defaults (was silently broken end-to-end).** `DataNodes` producers (Lightmap Baker's `lightmap_metadata`) wrote custom properties onto the `data_export` Empty, but no exported FBX could ever carry them: Blender defaults `use_custom_props` **off**, the bridge-oriented `FbxUtils._EXPORT_DEFAULTS` pins mesh-only `object_types` (filtering the carrier Empty out entirely), and the Scene Exporter's "Export Scene Data Node" checkbox was a disabled placeholder citing a stale reason ("needs data_export port" — the port landed with the lightmap baker). Fixes: `_DEFAULT_FBX_OPTIONS` (+ the shipped `default` preset) now set `use_custom_props: true` and `object_types: ["EMPTY", "ARMATURE", "MESH"]` (stored as a list — JSON presets can't hold a set; `FbxUtils.export` now coerces `object_types` iterables for `bpy.ops`); `TaskManager.export_data_node` is a real task mirroring mayatk's (appends the carrier to the export set in every export mode + channel-agnostic entry-count summary log; runs last in `TASK_ORDER`; clears carrier hide state first, since Blender's `use_selection` funnel can only ship selectable, visible objects — unlike Maya, where the hidden carrier exports fine), minus the producer-refresh step (`bpy.app.handlers` has no before-FBX-export event; producers publish at authoring time). `apply_declared_takes` stays a disabled placeholder, its tooltip now correctly citing only the missing Shots port. API parity: `DataNodes.ensure_export()` added (mirrors `mtk.DataNodes.ensure_export`; mayatk simultaneously retired its production-dead `mirror_attr`, so the two DataNodes surfaces now match — see mayatk's CHANGELOG). New docs: `docs/data_nodes.md` (the Blender divergences — Empty + custom props, by-name internal-carrier exclusion, visibility requirement — deferring to mayatk's `data_nodes.md` as the conceptual owner), wired from `docs/README.md` + `STRUCTURE.md` (whose `env_utils` row also stops claiming `SceneExporter` is Maya-only — the port ships). **Verified:** `test_scene_exporter.py` +5 checks incl. a real FBX round-trip — publish `lightmap_metadata` via `DataNodes`, export through `perform_export(tasks={"export_data_node": True})` with the carrier deliberately hidden, wipe scene, re-import with `use_custom_props` → the Empty and the exact payload survive; full suite green (all 60 suites, fresh headless Blender 5.1 each).
- **2026-07-08 — Auto Instance ported: new `core_utils/auto_instancer/` package (`btk.auto_instance` ↔ `mtk.auto_instance`), closing the last non-Shots item on the tentacle parity ledger.** Full port of mayatk's scene auto-instancer (find geometrically identical meshes → convert to instances of one prototype, with optional combined-mesh separation, assembly reconstruction, and game-ready remainder combining). The heavy machinery is **shared, not duplicated**: the 3-stage geometric compare (fast ordered → unordered KDTree K-twin identity with flip-free normal gating → RMS-uniform-scale + robust PCA) and the assembly clustering (same-material touch graph, GCD count splits, distance-consistency assignment, orphan recovery, cross-copy support gate) were extracted to pythontk (`PointCloud.match_clouds`/`pca_basis`/`pca_eigenvalue_signature`, `AssemblySorter` — see that CHANGELOG) and mayatk was refactored onto the same implementation, so both DCCs are thin adapters. Blender adaptations (each documented in the module docstrings): instance = shared mesh datablock, so leaf replacement is a **datablock swap** + `matrix_world @ rel` correction (member keeps its name/parent/children/collections/custom props — children need not move to world as in Maya, since data sharing has no per-instance-path multiplication); hierarchy replacement = linked-duplicate subtree copy; `polySeparate`→`separate_objects` (LOOSE; the source keeps one shell, so `cleanup_empty_sources` is a no-op kept for API parity); Maya group nodes→Empties carrying the `autoInstancerAssembly` custom prop; `polyUnite`→`object.join` with the single-user guard; UUIDs→`session_uid` re-resolution; locked-normal preservation→custom split normals captured in world space and re-set after canonicalization's transform edit; locked/referenced→library/override objects; the undo chunk moves to the slot boundary (`@btk.undoable`, blendertk convention). scipy is optional (pythontk's robust matcher now brute-force-falls-back; install scipy into Blender's Python via the package manager for the Kabsch spin refinement). Registered in `DEFAULT_INCLUDE`; `docs/STRUCTURE.md` core_utils row updated. **Verified:** new `test/test_auto_instancer.py` (fresh headless Blender 5.1, 22/22) — identical/reordered/baked-rotation/baked-scale copies (the rel-transform placement checks world geometry, exercising the pythontk direction fix live), material gate both ways, the mayatk scene-match mirror (3 cubes + 2 spheres + 2 cones joined into one mesh → separate_combined recovers and instances each family `[2,2,3]` with placements preserved), remainder combine per material with instances untouched, canonicalize preserving custom-normal world shading (min dot 1.0) while genuinely rotating the transform, and production safety (camera/empty/light untouched scene-wide, config unmutated, second run stable). **A multi-lens adversarial review pass then confirmed and fixed 8 further defects, and a follow-up self-critique a 9th** (each pinned by a new headless check; suite now 38/38 — the 9th: repeat leaf-mode runs re-replicated the prototype's children onto already-instanced members every run, accumulating objects; a member already sharing the prototype's datablock with no correction to apply is now an early no-op): stale matcher caches after in-place mesh mutation (Blender's join/separate/`mesh.transform` reuse the datablock under its same name, unlike Maya's new-shape-per-polyUnite — new `GeometryMatcher.invalidate` called at every mutating stage; without it the `combine_assemblies` flow canonicalized combined copies from pre-join vertex arrays and they never instanced); leaf-mode rel-fold displacing the member's own pre-existing children in world space (compensated via `matrix_parent_inverse = rel⁻¹ @ parent_inverse`, exact and depsgraph-free — Maya reaches the same contract by moving children to world); leaf-mode not replicating the prototype's own transform children onto members (Maya's `instance(leaf=True)` does — now linked-duplicated across, built before the member is mutated for rollback safety); the remainder combine destroying converted members' preserved children (now excluded via the existing `created=` descendant exclusion) and crashing on candidates outside the active view layer (filtered; `select_set` raises on them); `_join_copies` orphaning children of joined-away sources at their bare `matrix_basis` placement (children now reparent world-preserving onto the survivor — fixes every `combine_objects` caller) and aborting on un-selectable copies (skipped); zero-scale (singular-matrix) objects aborting the whole run from `center_transform_on_geometry`'s unguarded `inverted()` (fallback + moved inside canonicalize's try — log-and-skip like every other canonicalization failure); and zero-vertex meshes crashing `are_meshes_identical_with_transform`'s NN query (empty clouds now match as identity, honoring `match_clouds`' own convention; same latent guard added to mayatk). The review also drove a real second-leaf-pass test (two copies each of two assembly types sharing common parts → 4 tagged assembly groups, cross-type datablock sharing) — the prior flag-combo test never actually reached that pass. Also: `test/Run-Tests.ps1` now globs suites only (`test_*.py` + smoke) — `dump_runtime_surface.py` is a utility without the `===RESULT===` sentinel and made every full run report failure.
- **2026-07-08 — Packaging fix: the blendertk wheel now ships BOTH the `blenderpy-package-manager.bat` wrapper and the shared `package-manager.bat` menu — previously it shipped neither.** blendertk's `MANIFEST.in` and `pyproject.toml` `package-data` both omitted `*.bat`, so the built wheel carried no package-manager scripts at all; the wrapper (which hands off to the interpreter-agnostic menu, resolving it as a sibling first and only then via a monorepo `..\..\..\m3trik\` fallback) was unusable post-install. Both now include `*.bat`, and the menu is committed as a mirror in `env_utils/` (SSoT is `m3trik/package-manager.bat`, synced by `m3trik/scripts/sync_shared_bat.py`; mirrors mayatk's fix for parity). `test_blenderpy_package_manager.py` gains a check that the mirror exists beside the wrapper and matches the SSoT verbatim. Verified: rebuilt wheel contains both `env_utils/*.bat`. (See m3trik CHANGELOG.)
- **2026-07-07 — `Mirror`'s Z-axis radio (`chk004`) now ships checked by default in `mirror.ui`, matching mayatk's mirror tool.** Keeps the two DCCs' out-of-the-box default in parity (`tentacle/docs/PARITY_SURFACE.md` regenerated clean, CI parity gate green).

- **2026-07-06 — `edit_utils/selection.py`'s `Selection` class gains 8 bmesh-based Convert-To conversion methods, closing most of the mayatk `core_utils.components` gap that mattered for the shared `cmb003` combo (see tentacle CHANGELOG for the full port + two bugs it surfaced).** New: `convert_to(obj, mode, contained=False)` (touching vs. contained component conversion via Blender's native `use_expand` flag — no bmesh graph walk needed for this axis at all), `select_face_path`, `select_vertex_perimeter`, `select_edge_perimeter`, `select_face_perimeter` (the one genuine bmesh graph query: faces across a boundary edge of the selection not already in it), `select_border_edges` (naked/open mesh edges among the selection's own edges, or the whole mesh's border when nothing is selected), `select_shell_border`, `select_uv_shell`. Deliberately NOT a literal port of mayatk's 1300-line `core_utils.components.GetComponentsMixin`/`Components` (Maya string-component-descriptor machinery like `polyListComponentConversion`/`get_component_index` has no Blender analogue to mirror name-for-name) — scoped to exactly what the shared slot's Convert-To combo needs, per blendertk's "thin adapter, not a mayatk-sized reimplementation" guardrail; `docs/STRUCTURE.md` already anticipated this ("Maya's `Components` folds into `edit_utils`"). **Verified and permanently pinned** in `test/test_selection.py` (10 new checks, 54/54 total) against real grid/plane/cube geometry (contained vs. touching from an identical seed, a real face-path between two far faces, boundary vertex/edge/face rings with correct counts — incl. that the FACE-mode `select_face_perimeter` write actually flushes down to edges/verts, not left stale — naked-edge detection on an open grid vs. zero on a closed manifold cube vs. the no-selection whole-mesh fallback, shell border, whole-UV-island growth); a same-day self-critique caught that the first pass had only verified this ad-hoc in scratchpad scripts before deleting them, leaving no regression coverage — see the tentacle CHANGELOG for the slot-level end-to-end test this same critique added (20 more checks) and the two bugs (one Maya, one Blender) this work surfaced and fixed.
- **2026-07-05 — `BlenderUiHandler` now wraps Blender's native menus for tentacle's both-button chord menu — the mayatk-parity home for what was wrongly living in a tentacle slot dispatch table.** New `ui_utils/blender_native_menus.py` (`BlenderNativeMenus`) mirrors mayatk's `MayaNativeMenus`: a bare symbolic-name → `VIEW3D_MT_*`/`TOPBAR_MT_*` id table (`view`/`add`/`object`/`mesh`/`vertex`/`edge`/`face`/`mesh_uv`/`mesh_normals`/`curve`/`ctrl_points`/`segments`/`surface`/`armature`/`pose`/`constraints`/`ik`/`render`/`window`/`help`), plus a mode-adaptive `select` (`SELECT_BY_MODE`, like Maya's harvested Select menu). `BlenderUiHandler` gains: `can_resolve` (recognises those names, so a bare-target `MenuButton` resolves to the native menu instead of its hover overlay — the mirror of `MayaUiHandler.can_resolve`); `_register_native_menu_proxies` (pins a per-name proxy in `loaded_ui`, strong-ref because `loaded_ui` is weak, so the switchboard's `get_ui`-based release path resolves to it — Maya's wrappers are lazily built via `handler.get`, but the switchboard never calls `handler.get` on release, so Blender pre-registers); and `show` (pops the real Blender menu via `btk.call_native_menu` — Maya harvests live `QAction` rows into a Qt window, Blender has none to harvest so it invokes its own menu: wrap, not recreate; deferred one tick so the popup lands after the Qt overlay hides). Module imports Blender-free (`bpy` only in the Select-resolve branch + the show-time pop), so the handler still constructs offscreen. Consumed by `tentacle/ui/blender_menus` (pure-`MenuButton` files, no slots — mirror of `ui/maya_menus`; see the tentacle CHANGELOG). **Verified:** live-Blender `tentacle/test/blender/blender_menus_check.py` — every id real, proxy per name, `show` dispatches the right id (incl. Select across Object/Edit-Mesh + fallback) — 21/21 on Blender 5.1; offscreen construction + resolution proven from the tentacle nav test (7 tests / 74 subtests).

- **2026-07-05 — `StyleSetter` drops its backup/restore feature entirely (`BACKUP_NAME`, `_backup_stem`, `backup_current`, `ensure_backup`, `restore_default_style`).** Reasoning: reverting to the user's own look never needed a bespoke snapshot here — Blender's own built-in themes (and the user's own saved presets) already sit in the same preset dirs `list_templates()` scans, so re-selecting one from the combo/dropdown IS the revert, exactly like Blender's own native Themes selector has no separate "restore" concept either. The removed backup was redundant AND, per report, wrong in both style and execution. `set_style` no longer calls `ensure_backup()`. The tentacle `cmb003_init` slot (`slots/blender/preferences.py`) no longer calls `ensure_backup()` or auto-selects a `Default` entry — it just installs + populates the combo, matching the native dropdown's lack of a "currently selected" indicator. `mayatk.StyleSetter` had the parallel backup/restore surface removed the same day for parity (see that CHANGELOG) — even though Maya's side loses its only revert path in the process (Maya has no built-in "theme" to fall back on the way Blender does), since the ask was explicit removal on both sides, not removal-plus-a-new-mechanism. `docs/STRUCTURE.md`'s `ui_utils` row updated (the "same `Default`-backup ... behavior" parity claim is now "same `set_style` behavior ... neither ships a bespoke backup/restore"). Test suite rewritten: the three backup-specific checks (`ensure_backup` creates 'Default', backup name/stem, 'Default' in `list_templates()`) removed; the "revert" proof now applies Blender's own built-in `*Dark*` template (found by scanning `list_templates()`, not hardcoded, since a display-name detail shouldn't make the test fragile) and confirms it lands back on the exact same factory values the old bespoke backup used to restore — demonstrating the stated rationale directly rather than just asserting the API is gone. Verified: `test/test_style_setter.py` 38/38 (fresh headless Blender).
- **2026-07-04 — Feat: Script Output console (`env_utils/script_output.py`) — mayatk-parity, Blender-native (Route 2+).** `show`/`toggle`/`hide` open a real, dockable native **Info Log window** (the *anchor*) and shadow its content region with a *frameless* `uitk.ScriptOutput` *skin* — the SAME syntax-highlighted widget mayatk docks. Rationale: Blender editors are areas Blender paints itself, so a Qt (HWND) widget can't be embedded the way Maya's `workspaceControl` docks one; instead the native window supplies the dock/chrome/fallback and Qt supplies the styled content. The skin is fed by a `sys.stdout`/`sys.stderr` tee + a root `logging` handler (Blender's nearest analogue of "the script editor output"), is OS-**owned** by the anchor (stacks/minimizes/closes with it), and is re-shadowed on a `bpy.app.timers` tick as the anchor moves/resizes. New `ui_utils/blender_window.py` (`BlenderWindow`) does the win32 GHOST-enumeration / area→screen geometry (bottom-left→top-left y-flip, dpr-aware) / owner math — **no bpy dependency** (callers pass the region). A focused sibling of tentacle's `tcl_blender._NativeWindow` (blendertk can't import a layer above; DRY reconciliation deferred to avoid destabilizing the marking-menu ownership). Degrades to the bare native Info Log window off-Windows / without the Qt host. **Verified end-to-end in fresh live Blender 5.1 (12/12):** the skin shadows the anchor content region **pixel-perfect** (`d=(0,0,0,0)`, `[48,131,1824,898]`), OS-owner == anchor, stdout+logging land in the console, the error line colors red `(165,75,75)`, and `close()` restores stdout + removes the skin + closes the anchor window. Coordinate transform pre-validated by a live measurement probe (dpr=1; bpy area px == GHOST client px).
- **2026-07-04 — `style_setter`: extended `styles/Maya.xml` to cover Blender surfaces that were still at factory defaults, using colors verified from Maya's *live* palette.** The theme's chrome was already an accurate, complete map of Maya's UI palette — confirmed by dumping `QApplication.palette()` from a fresh Maya (Maya's dark UI is a compiled Qt style, so the live palette is the only ground truth; there is no `.qss` to read) and diffing the preset against Blender's factory theme (797/797 attrs present; core chrome matched Maya exactly: Window `#444444`, Base `#2b2b2b`, Button `#5d5d5d`, borders `#252525`, Highlight `#5285a6`, Text `#c8c8c8`). What remained "unaffected" was a set of attributes still carrying Blender factory colors. Fixed the ones with a real Maya mapping and neutralized the ones without: (a) slider/progress fills `#4772b3` → Maya Highlight `#5285a6`, and outliner selection rows retinted to Maya's blue family; (b) Node Editor now reads like Maya's — dark canvas (`space.back` `#444444` → `#2b2b2b`) with gray node bodies (`node_backdrop` `#303030` → `#444444`) instead of the inverted dark-nodes-on-light default, grid darkened to `#252525`; node **category** header colors left as-is (Maya has no matching system — mapping them would be inventing, not matching); (c) sequencer/NLA/clip "rainbow" strip colors desaturated toward grayscale (mix 60% to luminance-gray, keep 40% hue so types stay distinguishable) to fit Maya's grayscale editors; selection/state/semantic colors (before/after paths, tweak, keyframe borders) untouched. The Maya-faithful blue viewport gradient (top `#889db3` IS Maya's own `backgroundTop`) was deliberately kept. Only an existing shipped file was edited (no add/rename/delete → no wheel rebuild needed). Verified: `test/test_style_setter.py` 48/48 (fresh headless Blender, incl. new guards that the slider fill equals Maya Highlight and node bodies stay lighter than the dark canvas), plus a live-Blender screenshot of the Shading workspace confirming the Node Editor result.
- **2026-07-04 — Parity: `style_setter`'s shipped-data dir renamed `themes/` → `styles/` (matches mayatk's `style_setter/styles/`), and a stale-`build/` wheel-leak was cleaned.** The dir was `themes/` here and `colors/` in mayatk — a real cross-package drift (neither name fits both; Maya has no "themes", Blender's aren't just "colors"). Both are now `styles/`, matching the shared `StyleSetter` / `list_styles` / `set_style` vocabulary; the file *format* still differs by necessity (native Blender theme `.xml` here vs bespoke `.json` in mayatk — a ledgered divergence, see `docs/STRUCTURE.md`), but the location no longer does. `STYLES_DIR` replaces `THEMES_DIR` (public module const). While verifying the shipped wheel (per the `MANIFEST.in` lesson below), found that `pip wheel` accumulates into a stale `build/lib/` that setuptools does NOT prune — so the wheel carried BOTH the new `styles/Maya.xml` AND the old `themes/Maya.xml`+`Maya.json` (a file I'd *deleted*), which a release could have shipped. `build/` is a gitignored regenerable artifact; deleting it and rebuilding yields a clean wheel (`styles/Maya.xml` only). Takeaway (added to the wheel-verify memory): a rename/delete of a shipped data file isn't real until you `rm -rf build/`, rebuild, and inspect — the source tree being clean isn't enough. Verified: `test/test_style_setter.py` 46/46 (fresh headless Blender), clean-build wheel ships `styles/Maya.xml` only.
- **2026-07-04 — `style_setter` is now PURE native (dropped the `Maya.json` font supplement entirely) and exposes `list_templates()`/`apply_template()` so a UI combo can mirror Blender's own Themes dropdown.** Follow-up to the `StyleSetter` entries below, acting on the design principle that the tool should be nothing but a thin wrapper over Blender's native `interface_theme` preset system. **(1) Supplement removed:** the `themes/Maya.json` companion (which carried `preferences.view.font_path_ui` = Maya's Segoe UI, the one part of Maya's look a native theme XML can't hold) is deleted, along with all its machinery (`apply_supplement`/`_find_supplement`/`_capture_view_supplement`/`_SUPPLEMENT_VIEW_KEYS`, the JSON backup sidecar). Rationale: a side-channel font override is *fundamentally incompatible* with "mirror the native selector" — a theme picked from Blender's OWN Preferences > Themes dropdown (or applied via bare `execute_preset`) would never carry the supplement, so the two paths would diverge. Going native-or-nothing keeps the tool a faithful mirror; the cost is the Maya UI *font* no longer switches (its point sizes still do, via the theme's `ThemeStyle`). **(2) Backup renamed** `"Default Backup"` → `"Default"` (matches the combo's "revert to my own look" entry and the mayatk side). **(3) New `list_templates()`** returns an ordered `{display_name: filepath}` of every native `interface_theme` preset the dropdown sees — Blender's built-ins (`Blender Dark`/`Blender Light`), the user's own, our injected `Maya`, and the `Default` backup — with `display_name` via `bpy.path.display_name` so it reads identically to the native list. **New `apply_template(filepath)`** applies any of them by the filepath token via the same `execute_preset` (reset-to-factory-then-apply). Together they're the pair a theme-selector combo drives. `StyleSetter` gains both static methods and loses `apply_supplement`. Verified: reworked `test/test_style_setter.py` — **46/46** in a fresh headless Blender (pure-native: asserts no `.json` remains in `themes/`, `list_templates()` contains built-ins + `Maya` + `Default`, `apply_template(token)` for both `Maya` and `Default`, the supplement API is gone, plus the existing install/backup/name-based-path/unknown-name checks). Also verified live end-to-end through the tentacle Maya slot chain (see the tentacle CHANGELOG — the combo's token-vs-label indexing bug was caught there).
- **2026-07-04 Fix — the published wheel silently shipped `style_setter` with NO theme data at all: `themes/Maya.xml`/`Maya.json` were dropped from the build, and `Maya.json` had also gone missing from the working tree entirely.** Caught by building an actual wheel locally (`pip wheel . --no-deps`) and inspecting its contents rather than trusting the dev checkout — the same class of "editable install hides a real gap" risk as [[feedback_verify_upstream_api_in_published_not_editable]], just on the *shipping* side instead of the consuming side. Root cause: blendertk has no `MANIFEST.in` at all (unlike mayatk's, which recursively includes data files), and `pyproject.toml`'s `[tool.setuptools.package-data]` glob didn't list `.xml` — so a `.whl` build included only `__init__.py`/`_style_setter.py`, none of `themes/`. Fixed by adding `MANIFEST.in` (`recursive-include blendertk *.ui *.xml *.json`, mirroring mayatk's pattern) and adding `*.xml` to `package-data`; rebuilt and confirmed via direct wheel inspection that both files now land in `blendertk/ui_utils/style_setter/themes/`. Separately, `themes/Maya.json` (the font/view companion) was found missing from disk entirely — present when this feature's test suite last ran clean (37/37, including the font-supplement check) earlier the same day, gone by the time of this fix, with no git history to trace (the whole `style_setter/` directory is still untracked/uncommitted). Recreated from the known-good content documented in this same changelog entry above; re-verified live (fresh headless Blender, `test/test_style_setter.py` 37/37, including `Maya font supplement sets view.font_path_ui to Segoe UI`). Lesson for every future data-file-shipping tool in this package: build and inspect an actual wheel (`pip wheel . --no-deps -w <dir>` + `zipfile` listing), don't assume `package-data`/`MANIFEST.in` cover a new file extension or a non-package subdirectory just because a sibling package's config looks similar.
- **2026-07-04 — New `ui_utils/style_setter/` subpackage (`btk.StyleSetter`) — matches Blender's UI chrome to Maya's look using Blender's NATIVE `interface_theme` preset system, so the Maya theme shows up in Preferences > Themes > preset dropdown.** A "style" is a Blender-native theme preset (`.xml` in the exact format `Preferences > Themes > Export/Install` produces, written by `bpy.ops.wm.interface_theme_preset_add`), NOT a bespoke JSON only our code understands. `install()` copies the shipped `themes/Maya.xml` into Blender's per-user `presets/interface_theme` dir (verified live it then appears in the native Themes dropdown and is pickable by hand); `set_style("Maya")` applies it via the same `bpy.ops.script.execute_preset` the dropdown uses — which I verified **resets to the factory theme and then applies**, so the result is deterministic regardless of the prior theme (the same "default + overlay" determinism the first draft hand-built, but native and free). `restore_default_style()` re-applies the user's own theme, snapshotted once by `ensure_backup()`/`interface_theme_preset_add` into a native "Default Backup" preset before the first switch (so their look is never lost and stays selectable), falling back to `bpy.ops.preferences.reset_default_theme()`. The Maya colors are NOT hand-guessed: sampled empirically from a fresh, disposable Maya 2025 instance (`mayatk.MayaConnection`, `force_new_instance=True` — session-safety rule) via `QApplication.palette()` (Maya's `adskdarkflatui` look is a compiled native QStyle with an *empty* stylesheet, so the palette IS the ground truth) + `cmds.displayRGBColor` for the viewport gradient; landed on the well-known reference values (`Window`/panel-bg `#444444`, `Button`/fill `#5d5d5d`, `Highlight`/accent `#5285a6`, viewport gradient `(0.535,0.617,0.702)→(0.052,0.052,0.052)`), baked into the canonical `Maya.xml` by applying them to a live theme and letting Blender's own preset exporter write the file. The one thing a native theme preset can't carry — `preferences.view.font_path_ui` (Maya's Segoe UI; theme XML covers `Theme`+`ThemeStyle` but not the UI font *path*) — rides in an optional `themes/Maya.json` companion, applied best-effort by `apply_supplement` (a font missing on the target machine is skipped). Deliberately untouched: Blender's icon set (no supported theme swap) and structural layout (docked panels, Properties tabs) — no Maya equivalent. First draft was a custom-JSON RNA-serializer (`capture_style`/`apply_style` walking `preferences.themes[0]`); replaced wholesale after confirming Blender ships a first-class native preset format + machinery that's more standard, shows in the UI, and is less code. New `test/test_style_setter.py` (34/34, fresh headless Blender) — **session-safe**: it redirects `BLENDER_USER_SCRIPTS` to a throwaway temp dir up front (verified live that `user_resource` + `interface_theme_preset_add` honor the env var set at runtime) so install/backup NEVER touch the user's real Themes dropdown/config, and rmtrees it after. Covers: shipped-template presence, `install()` landing `Maya.xml` in the native `preset_paths` scan the dropdown enumerates, `set_style("Maya")` applying the button-fill/roundness/viewport-gradient/font, the auto-backup, `restore_default_style()` reverting to it and clearing the font, and the unknown-name `FileNotFoundError`. Also verified live in the actual Blender GUI (not just headless): screenshotted with the theme applied and the Preferences editor open on the Themes section showing **"Maya" as the active preset** in the native dropdown; earlier before/after pixel-sampling (`System.Drawing.Bitmap.GetPixel` at matching coords) confirmed exact hex matches to the sampled Maya palette (outliner/properties bg `#282828→#444444`, toolbar buttons `#282828→#5d5d5d`).
- **2026-07-04 — Adversarial review of the above `btk.Selection` port found and fixed a real crash: `select_by_type` raised `RuntimeError` for any object outside the active view layer (e.g. sitting in an excluded collection) — a realistic input since both `select_by_type`'s own default and the tentacle `list000` slot run over `bpy.data.objects` (the whole file, not view-layer-scoped).** Verified live (headless Blender 5.1.2) that `Object.select_set()`/`hide_set()` raise for such objects while `hide_get()` does NOT (it silently returns `False`) — so `_hide_get_safe`'s `try: hide_get() / except RuntimeError: hide_viewport` fallback was dead code based on a false premise, and `_apply_selection_mode`'s unguarded `select_set(True)` loop (plus `_select_uv_overlap`'s own direct `select_set` calls) would crash the entire selection sweep the moment any matched object — e.g. via the "Transforms" leaf, which matches literally every object — sat outside the view layer. Added `Selection._safe_select_set` (catches the `RuntimeError`, skips the un-selectable object instead of aborting the whole match set) and used it throughout `_apply_selection_mode`/`_select_uv_overlap`; rewrote `_hide_get_safe` to check `hide_get() or hide_viewport` explicitly instead of the non-firing except branch. Also regenerated `blendertk`/`tentacle`'s `API_INDEX.md`/`API_REGISTRY.md`/`API_REGISTRY.json` (stale since the new `Selection` class/updated `list000` docstring weren't reflected) via `generate_api_registry.py blendertk tentacle`. New regression coverage in `test/test_selection.py` (excluded-collection object present alongside an in-view-layer match — for `Transforms`, `Hidden Geometry`, and UV `Overlapping`/`Non-Overlapping` — all confirmed live to crash before the fix and pass after). `pyflakes` clean; full `test_selection.py`/`test_edit_utils.py`/`test_node_utils.py` re-run PASS; `tentacle` structural suites (22 + 5) and `compare_panel_surface.py --all` (0 untriaged) unaffected.
- **2026-07-04 — New `edit_utils.selection.Selection` engine (`btk.Selection`) — full mirror of mayatk's `Selection._SELECTION_CONFIG` category breadth, closing a real parity gap on the shared `list000` "Select by Type" list.** An audit found `tentacle/slots/blender/selection.py`'s `list000` ran on a hardcoded 3-category/13-leaf `_SELECT_TYPES` dict feeding `bpy.ops.object.select_by_type` directly — a straight omission, not a deliberate divergence (Maya's `_SELECTION_CONFIG` has 6 categories / ~40 leaves and none of the gap was ledgered). `btk.Selection` mirrors mayatk's shape 1:1 (`{category: {label: callable(objects) -> matched}}`, same category + leaf names — Animation/Dynamics/Geometry/Hierarchy/Scene/UV) but every handler body uses Blender-native Object-level primitives instead of Maya's string-node type lookups: `obj.animation_data`/`obj.constraints` (Animation); Fluid/Cloth modifiers, `LATTICE` type, HAIR/EMITTER particle systems, `rigid_body.type` ACTIVE/PASSIVE, `rigid_body_constraint`, `GREASEPENCIL` type (Dynamics); a `_GEOMETRY_TYPES` union, `hide_get()`/`hide_select`, one-representative-per-shared-`data` dedup (Geometry); `get_parent`/`get_children` reused from `node_utils` (Hierarchy); `asset_data`, `empty_display_type`, a 3-way Empty partition — Image Planes / Groups (has children) / Locators (neither) — and `animation_data` for Keyed Locators (Scene); native `bpy.ops.uv.select_overlap()` run per-object in Edit Mode (UV-sync forced on) for Overlapping/Non-Overlapping, `edge.use_seam` for Texture Borders, and `not uv_layers` for Unmapped (UV — confirmed live that `select_overlap` needs ≥2 UV faces to register, not a single degenerate one). Leaves genuinely absent at the Object level (Clusters, IK Handles, Joints, Brushes, Dynamic Constraints, Sculpts, Wires, Templated Geometry, UV Front/Back-Facing) are NOT built — see `tentacle/docs/parity_map.py`'s new `list000:<label>` entries under `HANDLERS["selection"]` for the per-leaf reasoning (Rigid Constraints, initially assumed absent too, turned out to have a real analogue — `rigid_body_constraint` on an Empty — confirmed via a headless probe, so it IS built); `nParticles` is `replaced`→`Particles` (Blender has one unified particle system, no classic/Nucleus split). A few Blender-only bonus leaves with no Maya counterpart (Metaballs/Text/Volumes/Armatures/Light Probes/Speakers) are kept so the old `_SELECT_TYPES` capability doesn't regress. `tentacle/slots/blender/selection.py`'s `list000_init`/`list000` now mirror the Maya slot's shape exactly (`btk.Selection.get_selection_categories()` / `btk.Selection.select_by_type(label, objects, mode="replace")`), replacing the old flat dict + direct `bpy.ops.object.select_by_type` call. New `test/test_selection.py` (bpy-required; verified live in a fresh headless Blender 5.1, all checks PASS) covers every category/leaf with constructed scenes (shared-instance dedup, parent/child chains, Empty partitioning, rigid-body active/passive, hair vs emitter particles, and a genuine 2-face overlapping-vs-clean UV scenario). `pyflakes` clean on every file touched; `tentacle/test/test_slot_integrity.py` + `test_ui_integrity.py` (22/22) and `compare_panel_surface.py --all` (0 untriaged, sweep PASSES) show no regressions.
- **2026-07-04 — Scene Exporter's FBX preset combo (`cmb000`) is real (closes the last tracked gap on `_scene_exporter`; mirrors mayatk's `SceneExporterSlots` Add/Delete/Open Directory/Edit at the capability level).** `cmb000` was a hard-disabled stub (`setEnabled(False)`, "None" only) with a `TODO(blender-parity)` note guessing a JSON-preset design; `b003`/`b004`/`b007`/`b008` didn't exist, ledgered `pending` in `tentacle/docs/parity_map.py`. Before building that guessed design, checked whether Blender's own native operator-preset system (`bl_options={'PRESET'}` → the generic `wm.operator_preset_add`/`bl_operators.presets.AddPresetBase` machinery behind the "+" button in File > Export > FBX) was a better fit — confirmed live (headless probe) that `export_scene.fbx` does carry `'PRESET'` and that `AddPresetBase` is importable, but that machinery only reads/writes through `context.active_operator` (a live, interactively-invoked operator sitting in its own redo panel) with no supported way to drive "add"/"edit" from an unrelated panel button, let alone headlessly — ruled out. Went with the named-JSON-dict design instead, via `pythontk.PresetStore` (the SAME built-in+user two-tier mechanism already used for this exact shape of problem in `edit_utils.macros`/`edit_utils.curtain`/`display_utils.color_id` — no new infra invented). `SceneExporter` (`_scene_exporter.py`) gains `PRESET_NAME`/`PRESET_PACKAGE`, `_preset_store()`, `list_fbx_presets()`, `fbx_preset_dir()`, `fbx_preset_path(name)`, `save_fbx_preset(name, options=None)`, `delete_fbx_preset(name)`; `load_fbx_export_preset`/`verify_fbx_preset` now resolve a real named preset (merged over `_DEFAULT_FBX_OPTIONS`) instead of logging a "not supported" warning, and `perform_export`'s `preset_file` param is renamed `preset_name` (a name, not a file path — the Blender-idiomatic divergence from mayatk's string-node/file-path convention) and now actually threads the resolved kwargs into `export_selection_fbx`. New shipped built-in preset `env_utils/scene_exporter/presets/default.json` (mirrors `_DEFAULT_FBX_OPTIONS`). `scene_exporter_slots.py`'s `cmb000_init` mirrors mayatk's 1:1 by objectName (`b007` Open Preset Directory, `b003` Add — seeded from the current selection or the defaults, `b004` Delete — user tier only, `b008` Edit — `os.startfile`s the preset's JSON since Blender has no per-field editor for an arbitrary FBX-kwargs dict the way Maya's native exporter dialog does); the disabled-state docstring/tooltip/help-text messaging is removed. New `test/test_scene_exporter.py` (bpy-required; 17/17 in a fresh headless Blender): built-in discovery, save/list/tier-resolution, partial-preset-over-defaults merge, clear-to-defaults, unknown-name `RuntimeError`, "duplicate to edit" shadow-then-delete-reverts-to-builtin, built-in-read-only delete no-op, and — the literal parity requirement — a real `perform_export(preset_name=...)` call that writes an FBX using the preset's resolved kwargs, plus an invalid-kwarg preset surfacing a clear `RuntimeError` from the real `bpy.ops.export_scene.fbx` call (proving the dict is genuinely forwarded as `**kwargs`, not just stored). `pyflakes` clean on every file touched; `test_smart_bake.py` (113/113) and `test_fbx_utils.py` (both touch this module) show no regressions. `tentacle/docs/parity_map.py`'s `_scene_exporter` `CONTROLS` block removed (nothing left to ledger); `compare_panel_surface.py --all` sweep confirms (`SceneExporter` row now `0/0/0/0/OK`, open-work items 7→3 — only the out-of-scope Shots XXL rows remain) and `PARITY_SURFACE.md` regenerated.

- **2026-07-04 — Transfer Keys gains a relative/value-offset mode (`chk006`, closes the last real gap on Blender's `tb004`; mirrors `mtk.AnimUtils.transfer_keyframes`).** Blender's `tb004` previously only supported an absolute transfer (`copy_keys(mode="action")` + `paste_keys`, snapping every target to the source's literal values) — the value-relative mode Maya's `chk006` provides (preserve each target's own current pose as the animation's base, applying the source's motion as an offset) was entirely absent, ledgered `pending` in `tentacle/docs/parity_map.py`. New `anim_utils._anim_utils.transfer_keyframes(objects, relative=False, optimize=False)` (`source = objects[0]`, targets = the remainder — the same convention as `xform_utils.transfer_pivot`): built on the existing `copy_keys`/`paste_keys` `"action"`-mode machinery (each target still gets its own independent Action copy — no cross-talk) rather than a parallel keyframe-copy path; when `relative=True`, each target's PRE-transfer value at every `(data_path, array_index)` is snapshotted before pasting, then every pasted fcurve is shifted by `target's own current value − source's OWN earliest keyed value on that fcurve` (mirrors mtk's per-attribute-first-key semantics exactly, not a single global-earliest-frame offset). New read-side helper `_get_path_value` (mirrors the existing `_set_path_value`; both now share a `_resolve_prop_container` path-resolution helper — DRY). `tb004_init` gains the `chk006` "Relative" checkbox (default checked, tooltip "Values relative to the current position." — same objectName/default/wording as Maya's), ordered before `chk035` (Optimize) to match Maya's option order; `tb004` now calls `btk.transfer_keyframes(...)` directly instead of the old inline `optimize_keys`/`copy_keys`/`paste_keys` sequence, passing both checkboxes straight through. `tentacle/docs/parity_map.py`'s `chk006` ledger entry removed (no longer a divergence — same objectName/default now exists on both sides, so it mechanically matches). New `test/test_anim_transfer.py` (15 checks: absolute-mode verbatim copy, relative-mode preserving two DIFFERENTLY-posed targets' own base poses — with an explicit precondition check that those targets carry no prior `animation_data`, offset computed against the source's own first keyed value on a non-zero source base, `optimize=True` (asserting the source's flat run is actually collapsed by `optimize_keys` AND the relative offset is applied correctly to the post-optimize/reduced key set, not just "runs without raising"), degenerate 1-object/no-keys inputs, and target-action independence) — 15/15 PASS in a fresh headless Blender. Also verified end-to-end through the real `tentacle.slots.blender.animation.Animation.tb004`/`tb004_init` methods (option-box construction + both relative branches + the no-target message-box guard) via a throwaway headless probe — all green; probe discarded per the no-scratch-in-repo convention (the permanent regression lives in `test_anim_transfer.py`). `pyflakes` clean on every file touched; the existing anim suite (`test_anim_depth.py` 66/66, `test_anim_invert.py` 8/8, `test_anim_scale.py` 13/13, `test_anim_repair_curves.py` 12/12, `test_mat_anim_utils.py` PASS) shows no regressions. Registries regenerated (`transfer_keyframes` now in `API_INDEX.md`/`API_REGISTRY.md`; `AnimUtils.transfer_keyframes` + `blendertk/__init__.py`'s `DEFAULT_INCLUDE` both updated).

- **2026-07-04 Docs: landing page rewritten.** `docs/README.md` (the pyproject readme) still called the package a "greenfield scaffold" and pointed at the archived `BLENDER_PORT_PLAN` via a broken link; it now describes the active mayatk-parity port, links `STRUCTURE.md` + the tentacle parity docs, and the UTF-8 mojibake is fixed. `CLAUDE.md` Nav links the landing. Part of the monorepo docs-standard pass.

- **2026-07-04 — `adjust_key_spacing(objects=None)` fixed to mean "every scene object", not "nothing" (found during independent verification of the animation.py parity pass).** Every other `objects=None`-accepting function in `anim_utils/_anim_utils.py` (`optimize_keys`, `get_animation_info`, `tie_keyframes`, `repair_corrupted_curves`, `fit_playback_range`) explicitly special-cases `None` as scene-wide — `adjust_key_spacing` was the one holdout, routing straight through `ptk.make_iterable(objects)` (which returns `()` for `None`), so it silently touched zero fcurves. This broke `tentacle/slots/blender/animation.py`'s `tb002` (Adjust Spacing) "Scope: Entire Scene" `cmb036` option outright: with nothing selected, it always reported "No keys at or after the frame." regardless of actual scene content — reproduced live (a fresh headless Blender run driving the real `Animation.tb002` slot method end-to-end, not just the engine function) before fixing. `compare_panel_surface.py` never caught it because the control is structurally present and correctly wired — the bug was a runtime behavior gap, not a structural one. Fixed to match `mtk.adjust_key_spacing`'s own documented contract ("If None, adjusts all scene objects") and the sibling convention in the same module. New regression check in `test/test_anim_depth.py` (`objects=None` shifts keys across two unrelated scene objects with nothing selected); full suite re-run fresh (`test_anim_depth.py` 66/66, `test_anim_scale.py` 13/13, `test_anim_invert.py` 8/8, `test_mat_anim_utils.py` PASS) plus `compare_panel_surface.py --panel`-equivalent pair sweep on animation.py (unchanged: 0 untriaged, 1 pending) and full `--all` sweep (unchanged: only SmartBake's pre-existing, hard-excluded untriaged block).
- **2026-07-04 — Blendshape Animator: fixed a real sibling-value-clobbering bug found by re-running the cross-session test live, and documented a confirmed Blender 5.1 depsgraph limitation the fix doesn't fully cover.** Re-verifying the 2026-07-03 port's own headless test caught a genuine failure (not a stale-summary false positive): `Creator.create_weight_based_tweens`/`_duplicate_at_weight` left a tween-mesh's temporary weight sitting on the master key's `value` while linking the new datablock into the scene, and `Keyframes.test_morph`/`Applicator._rebuild_all_tents`'s `view_layer.update()` calls (needed to force a script-built driver to recompile) reapply EVERY f-curve on a shared `Key` ID at the current frame whenever one was recently inserted — together, either could silently reset a SIBLING `BlendshapeAnimator` session's un-keyed live value on a base mesh carrying more than one master key (e.g. "Smile" + "Frown" on one face mesh). Fixed: `_duplicate_at_weight` now restores `kb.value` before creating/linking the tween object; a new `keyframes.preserve_sibling_values` context manager snapshots+restores every un-driven sibling key's value around both `view_layer.update()` call sites. Investigating further surfaced a SEPARATE, confirmed-by-direct-repro Blender 5.1 engine limitation that the above fix does not (and, after extensive mitigation attempts, cannot within reasonable Python-API means) resolve: once a base mesh carries 2+ independently-keyed master keys and at least one has a driver-based corrective, the FINAL BAKED MESH for one of them (not a stable "first"/"last" rule) can stop reflecting its own mix contribution, even though every underlying RNA value reads back correct — see `applicator.py`'s module docstring for the full investigation (mitigations tried and ruled out: repeated/staggered `view_layer.update()`, `update_tag()`, frame-away-and-back, edit/object mode toggling, full driver remove+re-add, dummy driver variables, key reordering). `BlendshapeAnimator.create` now logs a warning when binding a new master key onto a base mesh that already has another animated one. `test/test_blendshape_animator.py`'s cross-session section no longer asserts the baked mesh for the 2-master-key scenario (documented why inline); it now asserts the RNA-level isolation contract that IS reliable (each key's own value and its corrective's driver-computed tent value stay scoped to that key, uncontaminated by its sibling) — 71/71 PASS in a fresh headless Blender (was 69, 1 failing, before this pass). The single-master-key-per-mesh case (the common single-morph authoring flow) remains fully verified end-to-end including real baked-mesh playback.
- **2026-07-03 — Macro Manager ported as a new co-located `edit_utils/macro_manager` panel (parity restored with mayatk; primarily panel-wiring over the pre-existing `edit_utils.macros` engine).** New `blendertk/edit_utils/macro_manager/` (`macro_manager.ui` + `MacroManagerSlots`, mirror of mayatk's module name/layout/objectNames): a single-table Switchboard interface — category combo (`cmb000`) + wildcard name/description filter (`txt000`) + table (`tbl000`: Macro/Hotkey/Category/Description) driving `blendertk.edit_utils.macros.Macros`, the single source of truth. Hotkey cells capture a chord in-cell via `uitk`'s shortcut-capture delegate (translated Qt↔Maya-style key notation through the engine's existing `qt_sequence_to_maya_key`/`maya_key_to_qt_sequence`); Category cells use a choice-capture delegate seeded from the engine's mixin-derived categories; conflicting hotkeys render in desaturated red with a peer tooltip (`find_conflicts`). Header menu: Clear All Hotkeys / Reset to Default + a semantic-mode `PresetManager` combo (save/load named binding sets — the shipped `default` preset is what `apply_saved_macros()` applies at `tentacle_startup.py` launch, since Blender's own keymap edits don't outlive the process). Per-row context menu: Assign / Clear / Reset to Default. Divergence from mayatk (by design, documented in the module docstring): Maya's hotkey registry is a DCC-persisted store the panel re-queries live; Blender's addon keyconfig only lives for the current process, so "live" means "this session's `Macros._KEYMAPS` bookkeeping", refreshed via `on_show` → `_reload_bindings`. Only the `Macros` engine is registered in `blendertk/__init__.py`'s `DEFAULT_INCLUDE` (already present, not duplicated); the panel itself is discovered by `BlenderUiHandler`, matching every other co-located tool. Launcher: `slots/blender/preferences.py`'s `b011` (`marking_menu.show("macro_manager")`) — same objectName + same host file as mayatk's Maya-side `preferences.py` `b011`. `test/test_macros.py` gains a dispatcher round-trip check (`bpy.ops.btk.macro()` invoked directly — the actual hotkey-press path, not just the underlying `m_*` function call — plus its unknown-macro `CANCELLED` path); 49/49 PASS in a fresh headless Blender. `test_blender_ui_handler.py` already covered the panel's Qt-only discovery/wiring (table population from `list_available_macros`, category combo, header actions, choice-capture delegate, preset combo). Verified: `compare_panel_surface.py --panel macro_manager` clean (0 untriaged / 0 pending / 0 triaged-OK / 0 prop deltas / 0 item deltas).
- **2026-07-03 — `select_keys` gains the current-frame-relative time scopes (mirrors mayatk's `select_keys` time argument).** Independent verification of tonight's animation.py parity pass found `tb013` (Select Keys)'s `cmb041` only exposed 2 of Maya's 7 time-scope items (`All`/`Range`) — a real, un-ledgered gap that fell outside every workstream's assigned item list. `select_keys(objects, time=...)` now also accepts `"current"`/`"before"`/`"before|current"`/`"after"`/`"after|current"` (reusing `delete_keys`'s `_DELETE_KEYS_SCOPES` predicate table — DRY, no new time-window logic); the `None`/`(start, end)` forms are unchanged (backward-compatible). `test_mat_anim_utils.py` gains 6 new checks (each scope + an unknown-scope `ValueError`), fresh headless Blender, no regressions (56 checks total in that file, all PASS).
- **2026-07-03 — Blendshape Animator ported as a new `anim_utils.blendshape_animator` package (parity restored with mayatk; Maya's multi-target blendShape in-betweens rebuilt as driver-driven correctives, not cargo-culted).** New `blendertk/anim_utils/blendshape_animator/` (mirrors mayatk's module name): `_blendshape_animator.py` ships `BlendshapeAnimator`, a morph-authoring engine over Blender's native shape-key system — build a shape key from a source+target mesh (`bpy.ops.object.join_shapes`), key its `value` 0.0→1.0 over a frame range (a shape key's value is *already* directly keyable — no driven-attribute workaround needed, unlike Maya's blendShape weight), then sculpt "tween" meshes at chosen weights/frames to customize the curve. The hard part: Maya's blendShape supports several sculpted in-between targets on ONE weight attribute, interpolated piecewise-linearly — Blender has no such per-key mechanism. Rebuilt with **additive corrective shape keys driven by "tent" (triangular) scripted drivers**, each peaking at its own tween's weight and decaying to zero at its neighbours (`applicator.py`'s module docstring has the algebraic proof this is *exactly* Maya's piecewise-linear in-between interpolation, not an approximation). `creator.py` (weight/frame-based tween mesh creation, duplicate-weight guards), `applicator.py` (writes sculpted deltas into correctives + rebuilds every tent driver's neighbour bounds whenever the control-point set changes), `target.py` (tagged tween-mesh registry via custom properties — Blender `ShapeKey`s don't support custom properties, unlike `Object`s), `keyframes.py` (master-key value fcurve, via the public slot-aware `blendertk.get_fcurves`), `validator.py`, `weights.py` (pure math, ported verbatim). `blendshape_animator_slots.py` + co-located `.ui` (5-section panel: Setup/Edit/Diagnostics/Export, tree000 tween list) mirror mayatk's layout/objectNames 1:1 except dropping "Recover Setup" (rebuilds a corrupted blendShape *node* — no Blender analogue, a shape key isn't a separate node that can end up corrupted that way; ledgered `na`). `from_existing` tracks the joined-in target via a `blendshape_animator_target` custom property (falling back to mayatk's brittle name-pattern heuristic only for pre-convention scenes) rather than mayatk's `TWEEN_NAME_PATTERNS` guesswork. `RigUtils.add_prop_var` gained an optional `id_type` param (default `None` = unchanged `'OBJECT'` behavior for every existing caller) so a driver variable can target a non-Object ID datablock (here, a mesh's `Key`). `BlendshapeAnimator` registered in `blendertk/__init__.py`'s `DEFAULT_INCLUDE`; launcher in `slots/blender/scene.py`'s header "Manage" section (`b015`, `marking_menu.show("blendshape_animator")` — picked to avoid colliding with Maya's own `scene.py` `b013`/"Mesh Converter" objectName). New `test/test_blendshape_animator.py` (fresh headless Blender, 50/50): proves real value-keying/playback round trips — samples the evaluated mesh across the full 0→1 curve after applying two sculpted tweens and checks it against hand-computed piecewise-linear interpolation between control points (not just "no exception"), plus endpoints-exact-preserved, frame-based tween weight formula, topology-mismatch diagnose/cleanup, export finalize (keyframes + correctives survive, source tweens/target cleaned up), `from_existing` rebind, `recover_animation` (lost keyframe range inferred from tween frame metadata), and duplicate-weight tween collapsing. Verified: `compare_panel_surface.py --panel blendshape_animator` clean (0 untriaged / 0 pending / 1 triaged-OK / 0 prop deltas).
- **2026-07-03 — Audio Clips ported as a new `audio_utils` package (parity restored with mayatk, deliberately NOT a mechanism mirror).** New `blendertk/audio_utils/` (mirrors mayatk's module name): `_audio_utils.py` ships `AudioUtils`, a scene-wide sound-clip CRUD engine over Blender's native Video Sequence Editor (`scene.sequence_editor` — add/remove/rename/replace/move/trim a clip + `sync_scene_range`); `audio_clips.py` + co-located `audio_clips.ui` ship `AudioClipsSlots`, discovered by `BlenderUiHandler` like every other tool panel. Mayatk's `audio_utils` exists almost entirely to work around two Maya-only limits — a single-slot Time Slider (forcing a keyed-enum-attr + DG-node + composite-WAV compositor just to have ONE thing to scrub) and WAV/AIFF-only playback (forcing an ffmpeg pre-conversion step) — **neither applies to the VSE**, which plays any number of simultaneous strips natively and decodes MP3/OGG/FLAC/AAC itself; so none of that machinery (nor its callbacks/scriptJob rehydration, nor its FBX-manifest export path) is rebuilt here. What ported for real: browse-to-add (each file becomes a strip at the current frame — Blender's strip model has no unplaced/registered-but-unkeyed state to mirror, so mayatk's two-phase register-then-key collapses into one step), rename/replace/remove one or all clips (`btn_rename_track`/`btn_replace_track`/`btn_remove_audio` objectNames reused verbatim), Move To Current Frame (repositions, trim preserved — the direct behavioral analogue of "Key Audio Event"), head/tail Trim, and Sync Scene Range (fits `scene.frame_start`/`frame_end` to the loaded clips, extend-only or exact-fit). Verified against a live Blender 5.1.2 probe that the VSE's pre-6.0 `frame_start`/`frame_final_start`/`frame_final_end`/`frame_final_duration`/`frame_offset_start`/`frame_offset_end` strip properties are deprecated ("expected to be removed in Blender 6.0") in favor of `content_start`/`left_handle`/`right_handle`/`duration`/`left_handle_offset`/`right_handle_offset` — the engine uses only the new names throughout. `AudioUtils` registered in `blendertk/__init__.py`'s `DEFAULT_INCLUDE`; launcher lives in `slots/blender/scene.py`'s header "Manage" section (`b012`, `marking_menu.show("audio_clips")`) since mayatk's own launcher lives inside the not-yet-ported Shot Manifest panel. New `test/test_audio_utils.py` (fresh headless Blender, 32/32: add/list/get/move/trim/rename-with-collision/replace/remove/remove_all/sync_scene_range, both extend-only and exact-fit, plus the FileNotFoundError paths); `test_blender_ui_handler.py` gains `audio_clips` to the discovered-panels list plus assertions that the combo/spinboxes degrade to empty/zero without raising when no `bpy` is present, and that the option-box menus/actions/checkbox all materialize. `tentacle/docs/parity_map.py`'s `AudioClipsSlots` entry (`pending`, "needs the audio_utils module first") is removed from the no-Blender-twin `PANELS` table; its 12 Maya-only controls (Auto Convert / Export mode / Trim Silence / Suffix Time Range / Channels / Auto End None / Snap To Frame / Next Event / Key All / Stagger / Cleanup Unused) are ledgered `na` under a new `audio_clips_slots` `CONTROLS` row, each reasoned individually. Verified: `compare_panel_surface.py --panel audio_clips` clean (0 untriaged / 0 pending / 12 triaged-OK / 3 prop deltas — button-label wording only).
- **2026-07-03 — Unity Bridge rebuilt as a native co-located blendertk panel (parity restored with mayatk); supersedes the 2026-06-19 removal.** The `env_utils/unity_bridge` package (engine + `UnityBridgeSlots` + `.ui`) is back — a byte-parity mirror of mayatk's (`compare_panel_surface.py --panel unity_bridge`: 0 untriaged / 0 pending / 0 triaged-OK / 0 stale-maya / 0 prop deltas / 0 item deltas). Export the Blender selection to FBX and copy it into a Unity project's `Assets/` via the shared, DCC-agnostic `unitytk.CopyToAssetsDeliverer` (Unity's own asset pipeline ingests anything dropped into `Assets/` on window focus — no live-RPC, no fresh-instance-launch dance needed). `UnityBridge` engine registered in `blendertk/__init__.py`'s `DEFAULT_INCLUDE`; the panel itself is discovered by `BlenderUiHandler` (not registered), matching every other co-located tool. Exposed via `materials.py`'s `b026` (`marking_menu.show("unity_bridge")`, grouped under **Bridges**) — the `slots/blender/scene.py` header comment describing this wiring is corrected (it had drifted to describe the superseded extapps-relay design). `parameters.py`'s docstring/tooltips (verbatim-copied from mayatk's Maya-side wording) are corrected to describe the Blender-side export. New `test/test_unity_bridge.py` (headless Blender — delivery modes, preflight, scope-resolution primitives, and a real end-to-end FBX export + Assets/ copy via the engine's `_preflight`/`_produce`/`_deliver` steps directly, since the public `send()`/`params_defaults()` path needs `uitk.bridge.AttributeSpec` and can't run in a Qt-less headless instance); `test_blender_ui_handler.py` gains `unity_bridge` to the discovered-panels list + `params_defaults()`/single-delivery-mode assertions (the Qt-bound half). `tentacle/docs/parity_map.py`'s `UnityBridgeSlots` ledger entry (previously `pending`/"evaluate") is removed from the no-Blender-twin `PANELS` table alongside a reason note, joining `HierarchyManagerSlots`/`SceneExporterSlots`. Verified: isolated Qt smoke (19/19), fresh headless Blender (15/15), `compare_panel_surface.py --panel unity_bridge` clean.
- **2026-07-03 — Affix-mode picker migrated to the shared uitk `AffixOption`; local `mat_utils/_affix_mode.py` deleted.** The affix picker (Auto / Suffix / Prefix) was a verbatim copy of mayatk's now-removed `_affix_mode` — pure uitk/Qt wiring — so both toolkits now consume the shared `uitk.AffixOption` (`widgets/optionBox/options/affix.py`). `mat_utils/image_to_plane` and `mat_utils/game_shader` use the option-box manager surface (`widget.option_box.set_affix(...)`, `widget.option_box.affix_mode` / `.resolve_affix(...)`); `mat_utils/_affix_mode.py` is deleted. **Parity fix:** `light_utils/lightmap_baker` had no affix picker and hand-rolled a divergent `_resolve_affix` (bare text → `"_" + text`, unlike mayatk); it now adds the standard `txt000_init` picker and delegates to `option_box.resolve_affix(default="suffix")`, matching mayatk 1:1 (the shared `pythontk.StrUtils.split_affix` no longer prepends an underscore to bare text). Updated `test/test_image_to_plane.py` (was asserting a nonexistent `ImageToPlaneSlots._resolve_affix` and a `_Field` stub lacking `option_box`): now checks the `split_affix` primitive the slot delegates to and gives the stub a minimal `option_box`, keeping it runnable in the no-Qt Blender harness. Requires uitk ≥ the 2026-07-03 build shipping `AffixOption`.
- **2026-07-03 — Parity-backlog port pass: HDR Manager drift, Lightmap Baker + Wheel Rig + Tube Rig gaps.** Closed the tracked mayatk→blendertk drift on four co-located panels (all verified against the `compare_panel_surface.py` sweep + fresh headless Blender):
  - **HDR Manager** — ported the full post-port drift: the header now calls `config_buttons("refresh","menu","collapse","hide")` with the refresh wired to a re-scan; a **Clear Network** header action backed by a new `clear_world_hdri()` engine helper (removes the managed Environment-Texture / Mapping / Coord nodes — the Blender analogue of mayatk's skydome-network clear); an **Add HDR(s)…** option-box flow on the map combo (`add_hdr_btn` + `cmb_add_mode` copy/move/link, one dialog picking loose files and/or a folder, importing into the user's HDR folder — mirror of mayatk's sourceimages import); and an inline exact-angle **ValueOption** (`add_value`) on the rotation slider. The `.ui` promotes `slider000`→`Slider` and `spn_intensity`/`spn_exposure`→`DoubleSpinBox` (with the matching `<customwidgets>` entries) so those uitk affordances load. Panel sweep now **0 untriaged / 0 pending / 0 prop-deltas**. Verified: `test_light_utils.py` **22/22** (incl. new clear checks), `test_blender_ui_handler.py` **134/134** (new assertions prove the Add-HDR option box + slider ValueOption materialize, and that the `_norm_path` combo-match fix collapses slash style).
  - **Lightmap Baker** — header gains `config_buttons("menu","collapse","hide")`. The `spn_samples` max (Cycles 4096 vs Arnold's 256) is an accepted renderer-range delta; the `cmb002` Packing combo stays a precisely-scoped pending (needs a Blender-native atlas-consolidation engine — `pack_atlas` port — not shipped as a dead menu item).
  - **Wheel Rig** — `b010` **Get Wheel Size** on the slider option box: reads the selected object's diameter from `obj.dimensions` (max bbox dim perpendicular to the movement axis, mirroring mayatk's math). `test_wheel_rig.py` **14/14**.
  - **Tube Rig** — `.ui` promotes `cmb_preset`→`ComboBox` and `txt000`→`LineEdit` (+ `<customwidgets>`). The granular joints→IK→bind→constrain step-workflow (`b001`–`b004` + reverse `chk000`) + the twist/squash/volume/auto-bend deformation systems remain tracked pending — XL work needing standalone step-engine methods and live-Blender rig-deformation verification (see `tentacle/docs/parity_map.py`). `test_tube_rig.py` **21/21**.
- **2026-07-02 — Parity docs consolidated onto the tentacle ledger system.** `docs/STRUCTURE.md` slimmed 18.3KB→8KB to the durable correspondence map (work-history essays removed; ship records live in CHANGELOG/git); `docs/PARITY_BACKLOG.md` deleted — its standing decisions moved into STRUCTURE.md, its open rows into `tentacle/docs/PARITY_PORTING_PLAN.md`, and its stale rows corrected (dynamic_pipe + image_to_plane had shipped 2026-06-16 while still marked DEFER/N-A). Every intentional mayatk↔blendertk divergence is now machine-readable in `tentacle/docs/parity_map.py`; the `compare_panel_surface.py --all` sweep gates the mirror (see tentacle CHANGELOG 2026-07-02).

- **2026-07-02 `DataNodes.get_export_string` — cleared keys now read back as `None`, matching mayatk.** A key cleared via `set_export_string(key, "")` is stored as `""` on the `data_export` Empty; the getter returned that `""` while mayatk's contract (and its own docstring, "None if absent/empty") returns `None` — a name+behavior parity break for any caller branching on `is None`. Fixed with an `or None` coercion + docstring; the lightmap headless suite's clear-semantics check updated to assert `None` (21/21 green in fresh headless Blender). Part of the mayatk DataNodes critique pass (see mayatk CHANGELOG 2026-07-02) — mayatk's `set_export_string` simultaneously adopted *this* package's clear-without-creating empty-value guard, so the two mirrors now share both semantics.

- **2026-07-01 Test-suite cleanup — fixed a sentinel-format bug in `test_telescope_rig.py` / `test_wheel_rig.py` that made `Run-Tests.ps1` misreport 2 fully-passing suites as FAIL.** Both files independently computed and printed a *correct* `===RESULT=== N/N passed` summary line and exited with the right code (`sys.exit(1 if failed else 0)`), but that exact string never matches `Run-Tests.ps1`'s literal check for `"===RESULT: PASS==="` (colon + explicit PASS/FAIL token) — the format every other one of the 50 suites uses. Root cause: these two files were the only holdouts on an older, pre-standardization sentinel shape (`grep -l` across all suites confirmed 48/50 used the standard form; exactly these 2 didn't). Fixed by aligning both to the standard `result = "PASS" if not failed and lines else "FAIL"` / `print(f"===RESULT: {result}=== ({len(lines) - failed}/{len(lines)})")` pattern used everywhere else — no change to the underlying checks. Verified: both suites individually report `===RESULT: PASS=== (19/19)` and `(14/14)` under a fresh headless Blender; full `Run-Tests.ps1` run now reports **50/50 suites PASS** (was 48/50 — the 2 "failures" were this harness bug, not product bugs). Swept all 50 suites for the same mismatch — no other stragglers. Also reviewed the whole suite for the bug classes found in sibling repos (stale fake-UI stubs, debug prints, duplicate imports, sys.path issues, unsafe try/except Qt-widget-instantiation discriminators, dead/commented-out code, session-safety violations): none found — `BlenderConnection` is fresh-subprocess-only by construction (no `--reuse`/attach path exists), and an AST-based sweep of the whole `blendertk/blendertk/` package (121 files) found zero dead imports and zero discriminator-pattern try/excepts (the two candidates that looked similar on grep — `reference_manager._has_bpy()` and `channels_slots._is_alive()` — only probe an already-existing reference/module, they don't construct new widgets to test capability).
- **2026-06-30 Color Manager renamed to Color ID; square palette swatches (mirrors mayatk).** The `display_utils/color_manager.py` + `.ui` panel is renamed to `color_id` — engine `ColorManager` → `ColorId`, `ColorManagerSlots` → `ColorIdSlots`, `DEFAULT_INCLUDE` entry + handler/marking-menu key `color_manager` → `color_id`, header `COLOR MANAGER` → `COLOR ID`, help/log-prefix updated. The palette swatches adopt uitk `ColorSwatch.keep_square` so they render square (tracking the panel width), and the panel gains three titled sections — **Palette** / **Channels** / **Actions** — mirroring mayatk. Verified offscreen: `test_blender_ui_handler.py` discovers + loads the panel as `color_id` and wires `ColorIdSlots` with no stale `color_manager` panel (**128/128**). Engine test renamed `test_color_manager.py` → `test_color_id.py`.
- **2026-06-25 Preset selectors adopt the new uitk preset template (mirrors mayatk).** Preset selectors (curtain, reference_manager) now use the new uitk preset template (`ComboBox` + `option_box` toolbar with inline naming), matching mayatk. `curtain.ui` preset combo switched from `WidgetComboBox` to `ComboBox`.
- **2026-06-25 Lightmap Baker — fixed-size Resolution dropdown + new Scope selector (mirrors mayatk).** The free `spn_resolution` spinbox became a `cmb_resolution` combobox limited to `256 / 512 / 1024 / 2048 / 4096` (default 1024); `_resolution()` reads the value back and a Quality preset *snaps* the combo to the nearest listed size (`_set_resolution`). New `cmb_scope` combobox (**Selected** / **Visible** / **Scene**, default Selected) gates `b000`'s objects via `_scope_objects()`: Selected = `selected_objects()` (unchanged), Visible = scene meshes with `visible_get()`, Scene = all `scene.objects` meshes — no manual select-all needed for a whole-scene/visible bake. 1:1 with mayatk's `lightmap_baker` (name + behavior). Verified offscreen: `lightmap_baker` now loads + wires `LightmapBakerSlots` under `test_blender_ui_handler.py`, which asserts both combos' contents/defaults and that presets snap the resolution (128/128).
- **2026-06-24 `scene_has_animation()` — mirror of mayatk's `AnimUtils.scene_has_animation` (name + behavior).** Lightweight canonical "does anything move over time?" check: `any(_slot_fcurves(a) for a in bpy.data.actions)` — scans every action's fcurves (slot-aware, so it covers all animated datablocks: objects, shape keys, cameras, materials, lights, …), checks existence not non-flat motion, `False` when bpy is unavailable. Exposed module-level and on the `AnimUtils` namespace (`DEFAULT_INCLUDE` + `staticmethod`). Backs tentacle's Blender Export Playblast early-exit on a static scene. Verified via headless-Blender smoke (empty→False, keyed object→True, surface resolves both ways).
- **2026-06-19 Unity bridge REMOVED from blendertk — Blender now reuses the extapps `unity_workflow`
  panel (marmoset/substance architecture).** The blendertk `env_utils/unity_bridge` package (a
  byte-identical mirror of mayatk's) is deleted. Blender's "Unity Bridge" now follows the same
  "reuse the DCC-agnostic extapps panel" pattern as its Marmoset/Substance bridges: tentacle's
  Blender slot exports the selection to FBX and hands it to the rebuilt `extapps.unity_workflow`
  (file-driven `BridgeSlotsBase`, engine = `unitytk.FileToUnityBridge`) via `set_model_path`. This
  removes the duplicate panel + its `parameters.py`/`_unity_bridge.py`/`.ui`; mayatk keeps its
  native selection-driven `unity_bridge` (Maya was always native, as with marmoset). Removed
  `test_unity_bridge.py`; dropped `unity_bridge` from `test_blender_ui_handler.py`'s expected panels.
  (The option-box / `LAUNCH_MODE` / Studio-removal work below was on the now-deleted panel and is
  superseded — its substance lives on in the shared `unitytk` engine + the extapps panel.)
- **2026-06-19 Unity bridge — Unity Project field gets option-box buttons; project actions off the
  header (parity with mayatk).** Following uitk's `BridgeSlotsBase` switch to option-box buttons on
  the Output Dir row, the **Unity Project** field overrides `_configure_output_dir_options` to show a
  recent-projects history button + an option menu (▾) with **Set Project…** (shared `_pick_output_dir`
  browse), **Open Unity Project**, **New Unity Project…** — moved off the header (`HEADER_MENU_ITEMS`
  is now just Clear Log). Old `...` button gone. `compare_panel_surface.py --panel unity_bridge` clean.
- **2026-06-19 Unity bridge — "Launch Editor" toggle replaced by a "Launch Unity" mode (parity with
  mayatk).** The `LAUNCH_EDITOR` bool is now a `LAUNCH_MODE` choice: *Don't launch* (default, import
  on focus), *Open Editor* (windowed), *Headless (batch)* (`-batchmode -quit`; needs a batch-capable
  license). Logic lives in the shared `unitytk.CopyToAssetsDeliverer`; this slot just exposes the
  param. Fixes the "Send to Unity copied but Unity never opened" report (launch was default-off).
- **2026-06-19 Unity bridge — removed the "Unity Studio" delivery mode (parity with mayatk).**
  *Unity Studio* is a separate paid, browser-based product (created in Unity Cloud's Asset Manager),
  not this desktop FBX hand-off; the same-day two-mode addition had reused the name for a
  version-aware *desktop* launch, which collided with the real product and confused users. Per user
  direction it's dropped: `cmb000` collapses back to the single `copy_to_assets` delivery target
  ("Copy to Project"), all params are always visible, and the Send button is always "Send to Unity". Launch Editor + Unity
  Version + New Unity Project… remain as plain-Unity features. `compare_panel_surface.py --panel
  unity_bridge` clean.
- **2026-06-19 Unity bridge — two delivery modes: Existing Project + Unity Studio (parity with
  mayatk).** The `cmb000` dropdown selects the delivery target (friendly labels over `existing`/
  `studio` stems). *Existing Project* keeps the opt-in Launch Editor toggle; *Unity Studio* adds a
  dynamic **Unity Version** dropdown (from `unitytk.UnityFinder.find_editors()`), a **New Unity
  Project…** header action (`unitytk.UnityLauncher.create_project`), and always launches the chosen
  Editor. Per-mode param visibility + Send-button label tracking; delivery stays DRY on the shared
  `unitytk.CopyToAssetsDeliverer` (version-aware launch). `compare_panel_surface.py --panel
  unity_bridge` clean. (Slot is Qt-bound → exercised via the mayatk twin + headless engine tests.)
- **2026-06-19 Unity bridge — exposed an export Scope option (parity with mayatk).** New `SCOPE`
  choice param (Selected / Entire Scene / Visible Only, default Selected) leading the **Export**
  section; `UnityBridgeSlots.b000` resolves the export set via `_resolve_scope_objects` instead of
  always using the selection — *Entire Scene* / *Visible Only* gather the view-layer's mesh objects
  (visible filtered by `obj.visible_get()`). `compare_panel_surface.py` clean for the unity pair.
  Test: `test_unity_bridge.py` (+2 scope checks, headless bpy).
- **2026-06-19 HDR Manager — removed the separate "Set HDR" button; selecting a map applies it.**
  Parity with mayatk: the world-environment build moved from `b000` (the deleted **Set HDR** button)
  to a new `cmb000` selection handler that calls the renamed `_apply_selection`. `_populate_maps` now
  blocks the combo's signals during its rebuild so a refresh / folder-change can't auto-apply a map.
  `b000` and the `QPushButton` are gone from the slot and the `.ui`; help text updated. The live
  Blender harness (`tentacle/test/blender/panel_slots_check.py`) drives the new select-applies flow
  (empty pick = silent no-op; picking a map applies intensity×2^exposure + rotation).
- **2026-06-19 Bridge slots' header menus declared as data.** Per uitk's new default
  `BridgeSlotsBase.header_init`, the `maya_bridge` + `unity_bridge` slots dropped their hand-rolled
  `header_init` for a `HELP_SPEC` dict (+ a `HEADER_MENU_ITEMS` tuple for unity's Open-Unity-Project
  menu). The thin RizomUV bridge (engine subclass, help-text only — no menu) is unchanged. Behavior,
  objectNames, and help content preserved; `compare_panel_surface.py` clean for the unity/rizom pairs.
- **2026-06-19 Migrated onto pythontk's new `geo_utils` namespace (clean break).** Followed pythontk's
  relocation of the polyline/point-cloud geometry cluster out of `MathUtils`: `curtain` uses
  `ptk.Polyline.make` / `.resample` / `.order_points` (were `CurtainRail.*` / `arrange_points_as_path`);
  `tube_path` uses `ptk.Polyline.from_point_cloud` / `.order_points` (were `centerline_from_points` /
  `arrange_points_as_path`); `tube_rig` uses `ptk.Polyline.resample` (was `dist_points_along_centerline`);
  `_edit_utils.combine_objects` uses `ptk.PointCloud.cluster_by_distance` (was `cluster_points_by_distance`).
  Behavior unchanged — namespace-only.
- **2026-06-19 Unity bridge + bridge-engine migration onto `pythontk.core_utils.app_handoff`.** New
  `env_utils/unity_bridge` panel: export the Blender selection and copy the FBX into a Unity
  project's `Assets/` (via the shared `unitytk.CopyToAssetsDeliverer` Strategy); mirror of mayatk's
  `unity_bridge` (parity-clean per `compare_panel_surface.py`), exposed by a **Unity Bridge** button
  in the tentacle Blender Scene menu. `MayaBridge` rewritten as a thin `pythontk.ScriptLaunchBridge`
  (config = a `ScriptLaunchSpec` dataclass); the Blender selection + FBX export extracted to the new
  shared `env_utils/handoff_export.BlenderExportMixin` (its `_produce` hook, reused by the Unity
  bridge); `RizomUVBridge` exe-discovery consolidated onto `pythontk.AppLauncher.resolve_app_path`.
  New dependency: `unitytk`. Tests: `test_unity_bridge.py`, `test_maya_bridge.py`, `test_bridges.py`
  green under headless Blender.
- **2026-06-17 Parity evaluation — mechanical per-panel surface diff (stop eyeballing).** Manual
  comparison repeatedly missed flat-out-missing header options. New
  `m3trik/scripts/compare_panel_surface.py` AST-diffs a mayatk Slots file against its blendertk twin —
  `config_buttons`, every `menu.add`/option-box/action control (by objectName + label),
  `set_toggle`/`pin`/`add_presets` affordances, and slot defs — and lists what each side is missing.
  Deltas are either covered by a documented `KNOWN_MAYA_ONLY` allowlist or are real gaps. Running it
  exposed two **flat-out-missing** Reference Manager header items: `config_buttons` had only
  `refresh`/`hide` (missing **menu** + **collapse** — the hamburger that opens the header menu and the
  collapse toggle), and the entire **naming-preset** system (`menu.add_presets` + `presets.preset_dir`)
  was absent. Both restored (`preset_dir="blendertk/reference_manager"`). Also documented a real silent
  channels gap the diff caught: Create Attribute has no **enum** type (Blender custom props have no
  Maya-style enum on arbitrary objects). blendertk/CLAUDE.md now mandates the diff before claiming a
  panel is 1:1. reference_manager + channels both diff clean (0 missing; all deltas allowlisted).
  **`--all` sweep added** — diffs *every* mayatk↔blendertk `*Slots` panel **and** every
  `tentacle/slots/maya`↔`blender` file (now also diffing widget-handler defs, `tb###`/`b###`/…), and
  `--all --write` emits `tentacle/docs/PARITY_SURFACE.md` — the name-level companion to
  `PARITY_AUDIT.md` (which the audit now points to, since its line-ratio understates Maya-only-heavy
  panels). First whole-surface run: of 31 paired panels, 12 are clean and the rest have triable
  deltas (HdrManager/LightmapBaker missing `config_buttons`, ColorManager missing presets, …); **12
  Maya panels have no Blender class** and **33 `slots/maya` files have no `slots/blender` counterpart**
  — the authoritative remaining-port list, mechanically derived instead of eyeballed.

- **2026-06-17 Reference Manager — full table + option-box parity (1:1 with mayatk).** The earlier
  port had a 3-column text table (File / Status / Notes) driven by the context menu — the visible
  "not the same tool" gap. Rebuilt it to mayatk's **5-column clickable-icon table**: **FILES |
  reference-toggle | open | display-mode | NOTES**, using the same `widget.actions` icon-column infra
  the channels panel uses. Click the **link** icon to link/unlink (`link_blend_file` /
  `remove_library`), the **open** icon to open the scene (current scene highlighted + italic, not
  selectable — mirror of Maya's `_format_table_item`), and the tri-state **display** icon to cycle
  Normal → Reference → Template (`get`/`set_reference_display_mode`). Double-click the name to rename,
  the Notes cell to edit. **Root Directory** option box gained recent-dir history (pin), **Open
  Directory**, and **Set To Current Workspace** (`b006` / `b001`). **Filter** field option box gained
  the on/off **enable toggle** plus **Ignore Case** and the **Files / Notes / All** target combo
  (moved off the header menu to match Maya's placement; `_filter_options` now reads the two menus +
  the `ToggleOption.is_on` state). Also in this pass (earlier today): the header **Filter / Display**
  section — Filter by Suffix / Folder Structure (`{name}`/`{workspace}`/`{suffix}` via
  `ptk.StrUtils.replace_placeholders`, guarded against malformed patterns), Hide Suffix / Extension,
  Show Notes Column (Notes hidden by default), and **per-root workspace history** (capped 20). Text
  filtering runs in the slot (`_apply_file_filters` + `_note_matches`, reusing `ptk.filter_list`) so
  it honors ignore-case + the target. Still correctly dropped: assemblies / namespaces / `_FileRef` /
  `.mb` (no Blender analogue; `.blend1` backups already excluded). Verified: 17/17 filter/display/
  history logic smoke + offscreen panel-load smokes (5-col table populates, all option-box widgets
  present, live text-filter + enable-toggle narrow/restore rows, Notes hidden by default).

- **2026-06-17 Channels — restore the dropped Auto-fit Window option (1:1 with mayatk).** The
  Blender panel's header menu was missing mayatk's **Auto-fit Window** toggle, documented as a
  "Maya-editor-only nicety." An audit found that wrong: the feature is entirely Qt (header section
  resize modes + scrollbar/`window().resize()` measurement, zero `cmds`/`mel`) — the same
  DCC-agnostic category as the wheel/MMB scrub editing the panel already shares. Ported the
  `chk_auto_fit` header checkbox + `_on_toggle_auto_fit` + `_autofit_window` (pure-Qt, deferred
  twice so `ResizeToContents` columns settle before measuring; skipped on the no-selection
  placeholder so deselecting doesn't collapse the window, matching mayatk's early return) and added the previously-absent
  `_configure_columns` (Name=`ResizeToContents`; off → Value=`Stretch`, Type=`Interactive` 80 px;
  on → all three `ResizeToContents`), wired into `_refresh_table`. This also fixes a latent default
  divergence: the panel never set horizontal-header resize modes at all, falling back to Qt
  defaults instead of mayatk's Name/Value/Type layout. The Channel-Box Qt-signal sync stays out
  (genuinely Maya-only — Blender has no Channel Box), now the only item on the omitted list.
  Verified the resize-mode contract + window-fit under offscreen Qt (10/10 smoke).

- **2026-06-16 TubeRig — multi-strategy procedural tube rig (Spline-IK / Anchor / FK).** New
  `rig_utils/tube_rig.py` (`TubeRig` engine + `TubeStrategy` ABC + `TUBE_STRATEGIES` registry +
  `register_strategy`) — a faithful port of mayatk's multi-strategy `TubeRig`, built on a new
  shared foundation: **`RigUtils`** gained the armature primitives (`create_armature` /
  `add_bone_chain` = Maya joints→bones, `add_spline_ik` + `add_bone_constraint` =
  `ikSplineSolver`→**Spline IK** + the shared pose-bone constraint, `bind_armature` =
  `skinCluster`→armature-deform + auto weights, `_active_mode` for the EDIT/POSE scope);
  **`rig_utils/controls.py`** (`Controls` + `ControlNodes`) = curve-object control widgets via a
  `register_preset` registry; **`rig_utils/tube_path.py`** (`TubePath`) = centerline via the shared
  `ptk.MathUtils.centerline_from_points` + an explicit edge override. The three strategies —
  **Spline** (bone chain + Spline-IK on a hook-driven driver curve, stretch via `y_scale_mode`),
  **Anchor** (two controls + Stretch-To / Damped-Track), **FK** (bones-as-controls, native
  bone-hierarchy FK + curve custom shapes) — each **declare their options as plain Qt-free dicts**
  (`AttributeSpec` kwargs), the single source of both the build defaults and the panel widgets.
  `TubeRigSlots` + co-located `tube_rig.ui` = the **HYBRID docked panel**: the mode combo rebuilds
  the options body from the selected strategy's dicts (`uitk.make_widget`), so a new rig type needs
  no `.ui` edit; wired into blender rigging `cmb002` Quick Rig (`"Tube Rig"`). Divergences vs Maya
  documented (bones-as-controls FK, dict options vs `RigModeConfig`+editability flags). DRY:
  `edit_utils.hook_curve_point` (shared curve-control-point hook bind, also adopted by
  `DynamicPipe`). Tests: `test_tube_rig.py` 21/21 (each strategy DEFORMS — spline bend / anchor
  stretch / FK swing, read via the evaluated depsgraph; rig-grouping hygiene), `test_rig_utils.py`
  37/37 (armature/Spline-IK/Controls/centerline/create_group), handler 122/122 (panel + dynamic
  options load-verified).

- **2026-06-16 CurtainRig — the curtain wire-deformer rig.** `edit_utils/curtain.py` gained
  `CurtainRig` (mirror of mayatk's): Maya's driver **curve** + **wire deformer** (`dropoffDistance`)
  + per-CV **clusters** all fuse into the native **Hook modifier with smooth falloff** — the hook
  `falloff_radius` *is* the wire dropoff and the **control Empties** collapse the curve-CVs +
  clusters into one grabbable handle per pin; a root Empty parents the controls + curtain (Maya's
  rig group). `attach(curtain, controls=5|curve|positions, dropoff, name)` auto-places controls
  along the detected rail (top edge) or reads a curve's CVs. Engine-level only (no panel/nav button,
  exactly like Maya). DRY: introduced `edit_utils.hook_bind_inverse` (the gotcha-laden no-jump bind
  matrix, shared with `DynamicPipe`). Tests: `test_curtain.py` 25/25 (moving a control deforms the
  cloth; no-jump-on-bind; rigid root translate; curve-CV-driven).

- **2026-06-15 Channels — restore the dropped context-menu options (1:1 with mayatk).** The
  Blender panel's right-click menu had been thinned to Lock/Unlock/Reset/Key/Break/Copy/Paste/
  Freeze/Delete; the mayatk menu's animation + transform actions were missing with no documented
  reason. Restored every item that has a native Blender equivalent, reusing the mayatk section
  order (Edit · Values · Animation · Transform · Manage): **Mute / Unmute** (`Channels.set_mute`
  → `fcurve.mute` / driver `mute`, plus a new `"muted"` connection state + olive icon and
  `classify_connection` detection), **Breakdown** (`set_breakdown_key` → keyframe `type='BREAKDOWN'`),
  **Select Connection** (`select_connections` → driver-variable / constraint target object), and
  **Unfreeze Transforms** (`unfreeze_transforms` / `has_unfreeze_info`, wired to the already-existing
  reversible `btk.restore_transforms`; gated enabled/disabled via a context-menu state pass +
  new `btk.has_stored_transforms`). Only Maya's channel-box-exclusive items (Toggle Keyable,
  Hide/Show Selected, Lock-and-Hide) stay out — Blender has no channel box, now documented in the
  panel docstring rather than silently absent. Also fixed: `_custom_prop_keys` was leaking the
  `btk_*_bake` freeze-bookkeeping props into the table as fake custom-property rows. Tests:
  `test_channels.py` 51/51 (was 39; +9 covering mute/breakdown/select-connection/freeze-unfreeze
  round-trip + bake-key hiding).

- **2026-06-15 Channels — faithful panel parity (upgraded past the native-substitute).** New
  `node_utils/attributes/channels/` co-located module (mirror of mayatk's): a `Channels` engine
  (Qt-free, `bpy`-deferred → headless-testable) + `ChannelsSlots` + `channels.ui`, replacing the
  earlier native-substitute where the tentacle Blender *Channels* button just popped Blender's
  Properties editor instead of a real channel editor. A *channel* maps onto an
  object's transform channels (location/rotation/scale, per-axis — angles shown in degrees) and
  its custom (ID) properties; the table mirrors Maya's Name | Lock | Key | Value | Type with
  filters (Custom / Keyable / Locked / Animated / All + invert), value editing, lock toggles
  (transform channels), keyframe set/remove + Ctrl-break, create/delete/rename custom properties,
  reset-to-default, copy/paste, and Freeze Transforms (→ `btk.freeze_transforms`). Connection
  classification covers F-curves (legacy + 4.4+ slotted actions) and drivers. Maya-only machinery
  (channel-box Qt sync, MMB scrub, auto-fit window, the `Attributes` enum helpers) is dropped
  — no Blender analogue. Tentacle `slots/blender/edit.py` `b_channels` now opens the panel
  (`marking_menu.show("channels")`). Tests: `test_channels.py` (engine, headless harness) +
  `test_blender_ui_handler` extended (panel discovery + load, .venv).

## [Unreleased] — 2026-06-14 (Full mayatk parity port — kickoff + Phases 1–3)

Began a full structural mirror of mayatk in blendertk (decided: 1:1 file tree incl. subpackages;
extend native ops the way mayatk does unless little benefit; DRY the package-manager into pythontk).
Tracking doc: [`docs/PARITY_BACKLOG.md`](docs/PARITY_BACKLOG.md) (triage of every listed gap + phases).

- **2026-06-15 Maya bridge — template-driven object send (new capability, counterpart pair — not a
  mayatk port).** New `env_utils/maya_bridge/` co-located subpackage modeled on the marmoset/substance
  bridges: a `MayaBridge` engine (`btk.MayaBridge`, named after its target app like `RizomUVBridge`) +
  a `templates/` dir of executable Maya import recipes (`import` / `import_and_frame` / `new_scene`,
  each declaring `BRIDGE_MODES` + `__KEY__` placeholders) + a `parameters.py` `AttributeSpec` registry
  (Include Materials / Embed Textures / Apply Unit Scale / Include Animation / Triangulate / Frame in
  View). `send(objects, template, mode, params)` derives the Blender FBX export options from the params
  (`INCLUDE_MATERIALS=False` exports material-slot-cleared copies, originals untouched), `render_template`
  substitutes the FBX path + param values, and a **fresh** Maya launches and exec's the rendered script
  via `maya -command "python(exec(open(...)))"` (detached, never attaches to a running Maya; the launched
  Maya loads `fbxmaya` + `FBXImport`s the file). `MayaBridgeSlots` subclasses uitk's `BridgeSlotsBase`
  directly (no `BlenderBridgeSlotsBase` needed — `REQUIRE_OUTPUT_DIR=False`) for the dynamic template
  combo + per-template parameter widgets + presets; discovered by `BlenderUiHandler`
  (`show("maya_bridge")`), exposed via the Scene **header** menu's **Maya Bridge** button. Counterpart of
  mayatk's new `mtk.BlenderBridge` (`btk.MayaBridge` ↔ `mtk.BlenderBridge`). Engine stays Qt-free
  (defers the `parameters`/`uitk.bridge` import) so it resolves under headless `blender --background`.
  Tests: `test_maya_bridge.py` (discovery / template text / MEL builder / real strip-materials export,
  headless) + `test_blender_ui_handler` extended (maya_bridge panel loads + `render_template`, under .venv).

- **2026-06-15 Bevel — faithful panel parity (upgraded past the native-substitute decision).** New
  `edit_utils/bevel` self-contained module (mirror of mayatk's `edit_utils.bevel`): a `Bevel` engine
  (`btk.Bevel` ↔ `mtk.Bevel`) over native **`bmesh.ops.bevel`** driving the Edit-Mode component
  selection, plus a co-located `BevelSlots` panel + `bevel.ui` (Width / Segments / Profile / Clamp
  Overlap + live **`btk.Preview`**), discovered/served by `BlenderUiHandler` (`show("bevel")`).
  `bmesh.ops` over `bpy.ops.mesh.bevel` on purpose — no viewport/region context, so it runs
  identically interactively and headless. Preview snapshots the mesh and re-bevels the SAME captured
  edges each refresh (no stacking — the component selection round-trips the Object↔Edit toggle the
  Preview path drives). Previously bevel was only a `mesh.bevel(offset=0.1)` one-shot ("native-covered,
  no panel needed"). **Verified**: `test/test_bevel.py` 14/14 real-headless (both entry paths,
  empty-selection guard, Preview enable/refresh-no-stack/commit/revert); `test_blender_ui_handler`
  42/42 (discovery + load under `.venv`).

- **2026-06-15 Audit fixes (post-bevel sweep for the same class of incomplete-port bugs).**
  - **Dead Create button on every `Preview` panel — FIXED.** The co-located `.ui` files ship the
    commit button `enabled=false` (copied from mayatk, whose `Preview` re-enables it on preview-on),
    but blendertk's `Preview` never managed the button state — so Mirror / Cut On Axis / Duplicate
    Linear-Radial-Grid had a permanently-disabled **Create** button (you could preview but never
    commit, and a no-preview commit was impossible). Fixed centrally in `Preview.__init__`
    (`commit_button.setEnabled(True)`) — blendertk's `Preview` supports commit-without-preview, so
    the button must always be clickable; one shared change fixes all panels and is robust against
    future ones. Guard added in `test_preview.py` (button enabled after construction). 25/25.
  - **`edit_utils/macros.py` unguarded `import bpy` at module top — FIXED.** It's in
    `DEFAULT_INCLUDE` (`btk.Macros` ↔ `mtk.Macros`), so an unguarded top import broke headless
    package-surface resolution and contradicted the module's own docstring. Now guarded
    (`try/except ImportError → bpy = None`), mirroring how mayatk's `macros` guards `maya.cmds`
    (no module-level `bpy` use; the operator class is built inside `_ensure_operator`). Docstring
    corrected. 30/30.
  - Audited the rest of the package for the bevel-class bug (reading component `.select` from an
    object-mode `bm.from_mesh`): **none found** — every selection-based op correctly reads from
    `bmesh.from_edit_mesh` behind an edit-mode guard. Full suite: **35/35 suites pass.**

- **DCC depth-parity port — engines for the final 8 modules (parity reached 100%).** New helpers
  backing the tentacle Blender slots that closed the last depth gaps:
  - **`edit_utils.average_normals(objects, by_uv_shell=False)`** — soften all edges (smooth shading,
    averaged normals); `by_uv_shell` keeps UV-island-boundary edges sharp (per-shell smoothing) via
    a `_edge_is_uv_seam` test. Mirror of `mtk.Components.average_normals` (polySoftEdge a=180).
  - **`edit_utils.dissolve_coplanar(..., preserve_borders=True)`** — drives the Decimate-PLANAR
    modifier's `use_dissolve_boundaries` (Maya reduce's Keep-Border).
  - **`edit_utils.loft(objects, close=, reverse_normals=, section_spans=)`** + `_resample_polyline` —
    bmesh bridge of profile curves/loops into a mesh surface (Blender has no native NURBS loft);
    arc-length-resamples each profile to a common count and bridges with quads.
  - **`anim_utils.bake_blend_shapes(objects, frame_range=, step=)`** — bakes driven shape-key weights
    to keyframes by sampling the EVALUATED depsgraph per frame (drivers don't show on the original
    datablock), removing the drivers, then writing the samples (nla.bake skips shape keys).
  - **`core_utils.analyze_scene(objects, adaptive=, sections=)`** — the SceneAnalyzer port: a budgeted
    (Adaptive size-scaled vs Generic flat 100k tri budget), sectioned game-readiness audit
    (Summary/Fix-First/Pareto/Offenders/Categories/Textures/Pipeline/Assumptions) returning
    `{section: html}`. Pipeline Integrity lists each missing texture as `name (filepath)` so the
    report is actionable (the path is what tells you where the file should resolve), not just the
    data-block name.
  - **`combine_objects`** gained the `@_object_mode` guard (join requires OBJECT mode — mirrors
    mayatk's mode-agnostic combine).
  All registered in `DEFAULT_INCLUDE` + namespace-mirrored. **Verified**: ruff-clean; `average_normals`/
  `dissolve_coplanar`/`combine_objects` confirmed real-headless this session. A brief headless window
  also ran `test_mat_anim_utils.py` far enough to **catch a real Blender-5.x bug** in
  `bake_blend_shapes` — it used the legacy flat `action.fcurves`, which 5.x drops for slotted actions;
  fixed to use the existing slot-aware `_slot_fcurves` helper (the same accessor `get_fcurves` uses).
  `loft`/`bake_blend_shapes`(fixed)/`analyze_scene` carry headless tests pending a full re-run once the
  O:\Cloud Nextcloud VFS recovers (pythontk's recursive read kept timing out tonight, stalling
  Blender's import). The rigging slot's logic stays inline per the documented "no rig_utils"
  convention (Empties / lock flags / armature show_axes / constraint-influence drivers — native bpy).

- **DCC depth-parity port — `polygons` module (Combine grouping/clustering, header Bridge/Bevel).** New
  **`combine_objects(objects, group_by_material=, cluster_by_distance=, threshold=)`** (edit_utils,
  mirror of mayatk's `EditUtils.combine_objects`): plain join → one mesh; `group_by_material` joins
  one mesh per material-slot set; `cluster_by_distance` further splits each material group by world
  bbox-centre proximity (`threshold` units) via the shared
  `ptk.MathUtils.cluster_points_by_distance` (no duplicated flood-fill). Backs the tentacle blender
  `polygons` slot: built `tb004` Combine option box (`chk003` Group by Material / `chk004` Cluster
  by Distance / `s003` Threshold), added a `header_init` mirroring Maya's two quick-access tools
  (`b007` Bridge Interactive + `b011` Bevel — both also static buttons in the shared
  `polygons#component#submenu.ui`), and re-pointed `b022` Attach at the plain combine (it previously
  re-entered `tb004`, which now reads the option box). `b007` runs the same edge-loop bridge as
  `b006` (Blender has no separate modal bridge — its interactivity is the operator redo panel). Only
  `chk008`/`chk009` directional U/V subdivide stay excused as `divergent` (Blender's `mesh.subdivide`
  is uniform — `s009` Cuts is the equivalent). **Verified** (real Blender headless):
  `test_edit_utils.py` +6 combine cases (plain → 1 mesh/12 faces, <2 → None, group-by-material → 2
  meshes, cluster → 2 vs no-cluster → 1). Module **68% → 100%** (0 unreviewed, 2 excused).

- **DCC depth-parity port — `rendering` module (playblast format engine).** New
  **`configure_render_output(scene, file_format, container=, codec=, quality=)`** (anim_utils,
  mirroring mayatk's `PlayblastExporter` home) — the testable, Qt-free engine behind the rendering
  slot's format/quality picker: sets `image_settings.file_format`, the FFMPEG `container`/`codec`
  for movie output, and maps a 0–100 `quality` to `image_settings.quality` + FFMPEG
  `constant_rate_factor`. **Blender 5.x finding:** the FFMPEG format is gated behind
  `image_settings.media_type='VIDEO'` (it's gone from `file_format` when `media_type='IMAGE'`); the
  helper sets `media_type` accordingly, guarded by `hasattr` for 4.x back-compat. **Verified** (real
  Blender headless): `test_mat_anim_utils.py` +3 cases (PNG, FFMPEG MP4/H264 + quality→CRF=HIGH,
  JPEG). Also moved the `scale_keys`/`stagger_keys` re-export imports to module top (they import
  `_anim_utils` only lazily, so it's cycle-safe) — clears a pre-existing E402 and drops the
  late-import hack; full anim suite still green.

- **DCC depth-parity port — `crease` module (smoothing-angle, replacing a lazy substitute).**
  `crease_edges` now mirrors Maya's `crease_edges(amount, **angle**)` faithfully: dropped the
  Blender-specific `mark_sharp` binary (a workaround for an earlier — wrong — "smoothing angle
  doesn't map" claim) for an **`angle`** parameter that is the Blender equivalent of `polySoftEdge`
  — an edge whose dihedral angle exceeds `angle` is marked sharp/hard, otherwise smooth/soft
  (`angle=0` hardens all, `angle=180` softens all; boundary/wire edges left untouched). `angle=0`
  subsumes the old binary, so the API is smaller, not larger. **Verified** (real Blender headless):
  `test_edit_utils.py` crease cases rewritten — angle=0 hardens all + still creases, genuine
  threshold discrimination vs the cube's real 90° dihedral (45→hard / 135→soft), angle=None leaves
  softness untouched. Also removed a long-standing dead `import bpy` in `decimate` (ruff F401) while
  in-file; `decimate` suite still green. Package source ruff-clean.

- **DCC depth-parity port — `scene` module (Scene Exporter + color-space fix).** Backs the tentacle
  `scene` slot's 28%→89% jump. New **`fix_color_spaces`** (mat_utils, mirror of
  `mtk.Diagnostics.fix_missing_color_spaces`): assigns each FILE image its correct color space by map
  type — data maps (normal/roughness/metallic/height/AO…) → **'Non-Color'**, color maps → **'sRGB'** —
  resolving the role from the filename via the shared `pythontk.MapFactory.resolve_color_space` SSoT
  (unrecognized names left untouched). Uses `bpy.path.basename` to strip Blender's `//` relative
  prefix, which `os.path`/ntpath misreads as a UNC root (the bug the test caught). Registered in
  `DEFAULT_INCLUDE`. The Scene Exporter itself lives in the tentacle slot over the existing
  `FbxUtils.export` (object_types/use_tspace/path_mode kwargs) + native glTF GLB sidecar — no new
  blendertk surface. **Verified** (real Blender headless): `test_mat_anim_utils.py` +7 color-space
  cases (data→Non-Color, correct color map untouched, genuine-changes-only report, unrecognized
  skipped, dry-run, color-map correction, scan-all); `test_fbx_utils.py` +2 exporter-contract cases
  (object_types set incl. CAMERA/LIGHT/ARMATURE + use_tspace + embed writes; GLB sidecar writes).
  Package source ruff-clean (the new code).

- **DCC depth-parity port — `edit` engine (mesh-cleanup depth).** Three additions backing the
  tentacle `edit` slot's Mesh-Cleanup completion (50%→100%): **`find_problem_geometry` gained
  `zero_uv_area`** (+ `uv_area_tolerance`) — a shoelace area over each face's active-UV loops
  (meshes with no UV layer contribute nothing), correcting the docstring's prior "UV-area has no
  bmesh primitive" claim. New **`get_overlapping_faces`** (edit_utils, mirror of
  `mtk.get_overlapping_faces`): groups faces by their rounded local vertex-position multiset and
  flags/deletes coincident duplicates (doubled geometry on distinct vertex sets — the case that DOES
  occur, vs lamina/same-vertex-set faces which bmesh forbids). New **`get_overlapping_duplicates`**
  (edit_utils, mirror of `mtk.get_overlapping_duplicates`): object-level dedup by a world-space
  fingerprint (vertex/face count + rounded world bbox + sampled world verts), with a `retain` set
  for "duplicates OF the given objects, omitting them". All `@_object_mode`-guarded bmesh/object
  ops. `test/test_edit_utils.py` +8 cases (zero-UV ±layer, overlapping-faces select/delete/clean,
  object-dedup coincident/spaced/retain).
- **DCC depth-parity port — `selection` engine (Select Edges By Angle range).** New
  `btk.select_edges_by_angle(objects, low_angle, high_angle)` (edit_utils): selects interior edges
  whose dihedral (face) angle is within a range, across the passed Edit-Mode mesh(es) — the Blender
  analogue of Maya's Select-Edges-By-Angle *range* (native `mesh.edges_select_sharp` takes only a
  single lower threshold). bmesh `calc_face_angle`, boundary edges excluded, replaces the edge
  selection, returns the count. Wired into the tentacle `selection` slot `tb003` (Maya `s006`/`s007`
  Angle Low/High), taking selection from 78%→100% depth. `test/test_edit_utils.py` +3 cases.
  **DI note + bug fixed in one pass:** the helper takes the edit-mode objects as an argument rather
  than reading `bpy.context.objects_in_mode`/`active_object` (both come back empty when read inside a
  deferred-imported function under `--background`). Also fixed a decorator that my insertion had
  displaced: `select_edges_by_angle` must NOT be `@_object_mode` (it edits in Edit Mode), and the
  displacement had silently stripped `@_object_mode` off `set_edge_hardness` — restored (verified
  against `git show HEAD`).
- **DCC depth-parity port — `animation` module finished (engine depth pass).** Extended the
  `anim_utils` engines so the tentacle Blender `animation` slot can close its last 49 depth gaps
  (see tentacle CHANGELOG for the slot wiring) — all pure `keyframe_points` math, headless-testable:
  `adjust_key_spacing` gained `selected_keys_only` + `exact_gap` (shift so the first key at/after the
  frame lands at frame+gap); `move_keys_to_frame` gained `selected_keys_only` + `align`
  (auto/start/end anchor); `add_intermediate_keys`/`remove_intermediate_keys` gained `time_range`
  windowing + `ignore_visibility` (skip `hide_viewport`/`hide_render` curves); `snap_keys` gained
  `selected_only` + `time_range` and now returns the moved-key count; `tie_keyframes` gained
  `absolute` (bookend at the actual keyed extent vs the scene range); `set_visibility_keys` gained
  `when` (current/start/end/both/before_start/after_end, relative to each object's range) + `offset`.
  **`stagger_keys` rewritten** with `start_frame`, `use_intervals`, `invert`, `group_overlapping`
  (+ `merge_touching`) and `smooth_tangents` — overlapping objects re-time together as one block
  (relative timing preserved). New **`format_animation_info_csv`** (mirror of Maya's CSV info flag;
  registered in `DEFAULT_INCLUDE`). All signature additions are keyword/back-compat (existing slot
  calls unchanged). `test/test_anim_depth.py` 21/21 (real Blender headless: grouping/intervals/
  invert/exact-gap/align/time-range/ignore-vis/absolute/when-offset/csv); existing
  `test_anim_invert` 4/4, `test_anim_repair_curves` 12/12, `test_mat_anim_utils` green.
- **DCC depth-parity port — `animation` Phase 1 engine (Repair Corrupted Curves).** New
  `btk.repair_corrupted_curves` (anim_utils; mirror of `mtk.Diagnostics.repair_corrupted_curves`): removes
  corrupted keyframes — NaN/inf (or beyond-threshold) key values/times — and deletes a curve left
  with no valid keys (`delete_unfixable`), each fix independently gated; returns
  `{corrupted_found, curves_repaired, curves_deleted, keys_fixed, details}`. Reuses the existing
  `_remove_fcurve` (5.x slotted-action removal). `test/test_anim_repair_curves.py` 12/12 (real
  NaN/inf/absurd-time fcurves). WIRED into the tentacle `animation` slot as the header option-box
  button `tb015` (mirrors Maya's `header_init`; option box `chk036/037/038` + thresholds `d015/d016`,
  reusing Maya names+labels), closing 6 animation depth-parity gaps → animation depth 33%. (Live
  GUI render of the header option box is the remaining check; logic mirrors the proven `tb019`.)
- **DCC depth-parity port — `uv` Phase 1 (Cleanup UV Sets).** New `btk.cleanup_uv_sets` (uv_utils;
  mirror of `mtk.Diagnostics.cleanup_uv_sets`): pick the primary by largest UV footprint, remove
  empty / keep-only-primary, rename to `map1` with force-overwrite of a name clash, and dry-run;
  returns `UvSetCleanupResult` rows. Wires the tentacle Blender `uv` slot's previously-stubbed
  `tb007` to the full 6-control option box (Maya parity). `test/test_uv_set_cleanup.py` 11/11.
  Closes 6 `uv` depth-parity gaps; with the grounded done-elsewhere/divergent classification of the
  RizomUV-specific packing knobs, `uv` depth 23%→44%. Scoreboard:
  [`tentacle/docs/DCC_PARITY.md`](../tentacle/docs/DCC_PARITY.md) (gen `m3trik/scripts/generate_dcc_parity.py`).
- **Phase 4 — package-manager DRY + `blenderpy-package-manager.bat`.** Added a Blender package
  manager (parity with mayatk's mayapy one) and de-duplicated the shared menu. The ~280-line
  interactive batch UI moved to a single interpreter-agnostic `m3trik/package-manager.bat`
  (`%1`=python.exe, `%2`=label, `%3`=backup-prefix); `blendertk/env_utils/blenderpy-package-manager.bat`
  is a thin wrapper that scans `Blender Foundation\Blender *`, resolves `<install>\<ver>\python\bin\
  python.exe`, and hands off to it (mayatk's `mayapy-package-manager.bat` became the matching thin
  wrapper). Decorations are ASCII-only (no `CHCP 65001` / box-drawing) to avoid the cmd UTF-8
  codepage parse bug. New `test/test_blenderpy_package_manager.py` (11/11, structural + handoff).
  Full suite 30/30. (Per the chosen approach this keeps the self-contained UI; the shared logic is
  batch rather than literally "in pythontk" — `pythontk.PackageManager` stays the Python API.)
- **Phase 4 — `env_utils/blender_connection.py` → `BlenderConnection`.** New headless launch/test
  harness mirroring mayatk's `MayaConnection` role. Blender has no command-port/standalone modes, so
  the only session-safe model is a fresh subprocess per run — `BlenderConnection` formalizes
  `blender --background --factory-startup --python …` as a class: `find_blender` (env `BLENDER_EXE`/
  `BLENDER` → PATH → install-dir glob, newest wins), `run_script` / `run_code` (temp script) /
  `run_result` (parse the `===RESULT: PASS===` sentinel → `(passed, CompletedProcess)`). The actual
  launch+wait+capture delegates to `pythontk.AppLauncher.run` (no raw subprocess); only exe discovery
  is Blender-specific. No `bpy` import (runs from outside Blender, e.g. the `.venv`). Registered under
  `env_utils.blender_connection`; new `test/test_blender_connection.py` (11/11, real child-Blender
  spawns). Full suite 29/29.
- **Phase 3 — `mat_utils/texture_baker.py` → `TextureBaker`.** Extracted the generic Cycles
  bake-to-texture primitive out of `LightmapBaker` (the bake loop, deterministic scene config/restore,
  per-object bake-to-EXR, and the material/UV/stem/output-dir helpers), mirroring mayatk's
  `TextureBaker` (primitive) / `LightmapBaker` (workflow) split. `LightmapBaker` now **composes** a
  `TextureBaker` and delegates `_bake` to it after `create_lightmap_uvs`. Generalized the bake controls
  (`fused` → `bake_type` / `pass_filter` / `use_pass_color`; `uv_set` accepts a name *or* a
  `callable(obj)` so the per-object lightmap-UV targeting is preserved); `default_output_dir(subdir=)`
  parameterized. Behavior unchanged — the lightmap suite stays 21/21. Registered under
  `mat_utils.texture_baker`; new `test/test_texture_baker.py` (12/12, real headless COMBINED +
  lighting-only bakes, naming, non-destructive node cleanup, scene-state restore). Full suite 28/28.
- **Phase 3 — reasoned non-ports** (recorded in `PARITY_BACKLOG.md`, not built): `BlenderBridgeSlotsBase`
  (0 consumers — rizom inherits its engine, substance/marmoset are tentacle button-launchers);
  `substance_bridge`/`marmoset_bridge` subpackages (mayatk's value is live RPC with no Blender path;
  blendertk's bridge is thin export-FBX-+-launch-extapps-panel, the export half already `FbxUtils`, the
  launch half tentacle-coupled); `shader_attribute_map`/`shader_remapper`/`mat_transfer`/`mat_manifest`
  (built on normalizing Maya's many shader types — moot for Blender's single Principled-BSDF shader +
  native `material_slot_copy` + existing `_mat_utils`). `segment_keys`/`unbake_keys`/`dynamic_pipe`
  deferred (no driving Blender slot — YAGNI).

- **Phase 2 — `core_utils/diagnostics/` subpackage → `Diagnostics`.** New diagnostics subpackage
  mirroring mayatk's `core_utils.diagnostics`: `mesh_diag.py` (`MeshDiagnostics`) and
  `transform_diag.py` (`TransformDiagnostics`), aggregated into a `btk.Diagnostics` namespace via the
  resolver `->Diagnostics` alias (multi-inherits the diag classes — `Diagnostics.find_problem_geometry`
  / `Diagnostics.fix_non_orthogonal_axes`). `find_problem_geometry` (+ its `_is_convex`/`_is_planar`)
  **re-homed** here from `edit_utils` to mirror mayatk (mesh problem-detection belongs to
  `Diagnostics`, not `EditUtils`); it imports `_bmesh_each` from `edit_utils._edit_utils` and the
  `_object_mode` guard from the sibling `core_utils._core_utils` (its canonical home — the way
  mayatk's diag modules import `XformUtils`/`NodeUtils`).
  `btk.find_problem_geometry` still resolves (now from `mesh_diag`); dropped from `EditUtils` for
  parity. New `fix_non_orthogonal_axes` detects sheared world axes (column-orthogonality) and fixes
  via clear-parent-keep-transform — a Blender object can't self-shear (its local matrix is always
  Loc·Rot·Scale), so shear is parent-induced; that scope is documented. `animation_diag`/`scene_diag`/
  `uv_diag` not ported (no driving slot yet). New `test/test_diagnostics.py` (13/13, real shear
  round-trip); full suite green (27/27).
- **Phase 2 — `env_utils/fbx_utils.py` → `FbxUtils`.** New FBX module mirroring mayatk's
  `env_utils.fbx_utils.FbxUtils`. `export_selection_fbx` moved here from `core_utils` and
  consolidated into `FbxUtils.export(filepath, objects, selection_only, **fbx_opts)` (now also
  appends `.fbx`, creates parent dirs, and supports whole-scene `selection_only=False`); added
  `FbxUtils.import_fbx` (wraps `bpy.ops.import_scene.fbx`, returns the created objects).
  `btk.export_selection_fbx` is unchanged (thin selection-only alias) so the Substance / Marmoset /
  RizomUV bridges keep working; `import_fbx` is newly exported. NOT ported (no Blender analogue):
  mayatk's animation-takes (`apply_takes` / `FBXExportSplitAnimationIntoTakes`) and the
  kBeforeExport/kAfterExport auto-export hook (Blender FBX emits AnimStacks from NLA/actions; no
  before-export `bpy.app.handlers` event — same gap as `ScriptJobManager.add_om_callback`), and the
  MEL plugin/preset/option layer (Blender takes options as `bpy.ops` kwargs). Registered under
  `env_utils.fbx_utils`; new `test/test_fbx_utils.py` (9/9, import round-trip + whole-scene).
- **Phase 2 — `xform_utils/matrices.py` → `Matrices`.** New matrix-helper module mirroring mayatk's
  `xform_utils.matrices.Matrices`, porting the **portable** subset over `mathutils.Matrix`:
  `get_matrix`/`set_matrix`/`local_matrix`/`to_matrix`/`identity`/`from_srt` (Euler-degree compose)/
  `compose` (quaternion-native)/`decompose`/`extract_translation`/`inverse`/`mult`/`world_to_local`/
  `local_to_world`/`is_identity`. Two documented divergences from mayatk: (1) Blender's column-vector
  convention flips the multiply ORDER (`@`, not Maya's row-major `*`) in the space-conversion/`mult`
  helpers — the *semantic* result matches; (2) mayatk's rigging node-graph builders
  (offsetParentMatrix/blendMatrix/aimMatrix/IK-FK/space-switch) are NOT ported — Blender rigging is
  constraint/driver-based (`rig_utils` is absent by design). Registered under `xform_utils.matrices`;
  new `test/test_matrices.py` (24/24).
- **Phase 1b — `scale_keys` / `stagger_keys` → own modules.** Split the two functions out of
  `anim_utils/_anim_utils.py` into `anim_utils/scale_keys.py` (`ScaleKeys` + `scale_keys`) and
  `anim_utils/stagger_keys.py` (`StaggerKeys` + `stagger_keys`), mirroring mayatk's module + class
  names. Blender key timing is plain `keyframe_points` math, so mayatk's segment/overlap/speed
  machinery is omitted (low benefit) — body stays thin. The shared fcurve helpers
  (`_actions`/`_slot_fcurves`/`_key_range`/`_shift_fcurves`) remain canonical in `_anim_utils`;
  the split modules import them lazily in the call body to keep the back-import cycle-safe (mirrors
  mayatk's deferred-import-into-method pattern). `_anim_utils` re-imports both fns at top so the
  namespace mirror (`AnimUtils.scale_keys`/`stagger_keys`) keeps resolving; registration moved from
  the `_anim_utils` list to `anim_utils.scale_keys`/`stagger_keys`. **Verified:** anim test PASS,
  smoke PASS; `btk.scale_keys`/`stagger_keys`/`ScaleKeys`/`StaggerKeys` + `AnimUtils.*` all resolve
  to the same object (no shadowing). Registry refreshed (36 modules).
- **Phase 1a — `rizom_bridge` → subpackage.** `uv_utils/rizom_bridge.py` split into the
  `uv_utils/rizom_bridge/` subpackage mirroring mayatk exactly: `__init__.py` + `_rizom_bridge.py`
  (`RizomUVBridge` engine) + `rizom_bridge_slots.py` (`RizomBridgeSlots`) + `rizom_bridge.ui`.
  Registration → `uv_utils.rizom_bridge._rizom_bridge`. Implementation unchanged (the thin one-way
  send; mayatk's heavy `BridgeSlotsBase` preset/param/round-trip machinery stays out — low Blender
  benefit). **Verified:** handler discovery 38/38 (slots resolve from the subpackage), bridges test,
  full suite 24/24; `btk.RizomUVBridge` resolves; registry refreshed (34 modules).
- `STRUCTURE.md` updated: the tool-layout convention is now "mirror mayatk's subpackage tree"
  (was "lean one-module-per-tool").

## [Unreleased] — 2026-06-14 (ScriptJobManager — Blender event-subscription manager)

Closed a real infrastructure gap: blendertk had **no** event-subscription manager, while mayatk's
`ScriptJobManager` backs ~17 tools/slots (auto-refresh on selection/scene change, etc.) and the
Blender panels could only pull-refresh on show.

- **`core_utils/script_job_manager.py`** (new) — `ScriptJobManager`, the Blender counterpart of
  mayatk's (`btk.ScriptJobManager` ↔ `mtk.ScriptJobManager`, same module path + class name + public
  API: `instance`/`reset`/`subscribe`/`unsubscribe`/`unsubscribe_all`/`connect_cleanup`/`suppress`/
  `resume`/`status`/`teardown`). Maya event names map onto **`bpy.app.handlers`**, one
  `@persistent` master handler installed per handler list and multiplexed to all subscribers:
  `SceneOpened`/`NewSceneOpened`→`load_post`, `SceneSaved`→`save_post`, `timeChanged`→
  `frame_change_post`, `SelectionChanged`→`depsgraph_update_post` (gated by a selection-set diff so
  it fires only on real changes), `Undo`/`Redo`→`undo_post`/`redo_post`. Ephemeral subscriptions are
  pruned on scene change (mirror of Maya's `killWithScene`); `connect_cleanup(widget, owner)`
  auto-unsubscribes on Qt widget destroy. The `@persistent` decorator is required so handlers
  survive file loads. Registered under `core_utils.script_job_manager`.
  - **Divergence:** no `add_om_callback` — its OpenMaya `MMessage` analogue is `bpy.msgbus` (with
    its own across-load semantics); add a `subscribe_rna` only when a tool needs arbitrary
    RNA-property watching (YAGNI). Documented in the module + STRUCTURE.md.
- **Verified** (real Blender headless): `test_script_job_manager` 19/19 — real `bpy.app.handlers`
  install/remove, `@persistent` marking, multiplex onto one handler, dispatch/suppress/resume,
  ephemeral prune + handler teardown, `unsubscribe_all`, unknown-event `ValueError`, selection-diff
  gating/debounce, `reset` detaches all masters. `btk.ScriptJobManager` resolves; registry refreshed.

## [Unreleased] — 2026-06-14 (Structural parity with mayatk — easy 1:1 mapping)

Aligned blendertk's layout with mayatk so a change in one mirrors mechanically to the other (and
documented the correspondence as the SSoT).

- **`docs/STRUCTURE.md`** (new) — the subpackage / namespace-class / main-module correspondence
  table, the intentionally-absent subpackages (`audio_utils` / `nurbs_utils` / `rig_utils`, with
  the YAGNI rationale: those Blender slots delegate to native `bpy.ops`, no engine layer), the
  shared conventions (main `_<sub>.py`, co-located `<tool>.py` + `<tool>.ui`, `<tool>_ui.py`
  generated/gitignored in both), and a porting checklist. Linked from `CLAUDE.md`.
- **`DataNodes` moved** out of `node_utils/_node_utils.py` into its own
  **`node_utils/data_nodes.py`**, mirroring mayatk's `node_utils/data_nodes.py` exactly (class
  unchanged; `__init__` registers it under `node_utils.data_nodes`; the lightmap-baker import was
  repointed).
- **`edit_utils.macros` registered** in `DEFAULT_INCLUDE` so **`btk.Macros` resolves like
  `mtk.Macros`** (previously the module existed but wasn't on the public surface, so `btk.Macros`
  raised — only the direct import worked). Only `Macros` is exposed, matching mayatk.
- **`RizomBridge` engine class renamed → `RizomUVBridge`** to mirror mayatk's class name, and
  registered as **`btk.RizomUVBridge`** ↔ `mtk.RizomUVBridge` (the `RizomBridgeSlots` panel class
  was already named identically in both and is unchanged).
- **Verified** (real Blender headless): `btk.Macros`/`MacroManager`/`DataNodes`/`NodeUtils` all
  resolve to the expected modules; `test_node_utils` / `test_macros` (30/30) / `test_lightmap_baker`
  (21/21) PASS; API registry refreshed (`DataNodes` now attributed to `data_nodes.py`).

## [Unreleased] — 2026-06-14 (Slot option-box parity: extend built-ins to restore Maya widgets)

Restoring dropped Maya slot **options** by extending Blender built-ins in blendertk (mirroring how
mayatk backs its slots), retaining Maya's exact widgets/objectNames/labels for a seamless transition.

- **`find_problem_geometry`** (edit_utils) gained **`quads` / `zero_area_faces` / `zero_length_edges`**
  (+ `area_tolerance` / `edge_length_tolerance`) — three more of Maya's Mesh-Cleanup checks, all
  bmesh-computable. (Holed / Lamina / Shared-UV / Overlapping / Zero-UV-Area have no bmesh primitive
  and stay out.)
- **`get_similar_mesh`** (edit_utils, new) — object-level *Select Similar* by topology / area /
  bounding-box metrics (vertex/edge/face/triangle/shell/uvcoord/area/world_area/bounding_box),
  compared via the SHARED `ptk.are_similar`. Mirror of mayatk's polyEvaluate-based version.
- **`separate_objects`** (edit_utils, new) — split a mesh into loose parts or one-per-material, with
  optional rename + origin-centering; mirror of mayatk's `separate_objects`.
- **`detach_components`** (edit_utils, new) — detach the active mesh's selected faces, mirror of
  mayatk's `EditUtils.detach_components`: `duplicate` (extract a copy), `separate` (new object vs.
  `mesh.split` in place), `separate_each` (edge-split → one loose object per face). Backs the
  Detach slot's restored **Separate Extracted Faces** / **Separate Each Face** options.
- **`set_edge_hardness`** (edit_utils) gained **`upper_hardness` / `lower_hardness`** — Maya's
  above/below-threshold hardness. Blender edges are binary, so the 0..180 value collapses to
  hard (< 90) / soft (≥ 90); `None` (Maya -1) leaves that bucket as-is. Defaults (0 / 180) reproduce
  the prior mark-sharp-by-angle exactly (backward-compatible).
- **Verified** (real Blender headless): edit_utils suite incl. 15 new checks (quads/zero-area/
  zero-length, get_similar_mesh ×7, separate_objects ×3, detach_components ×9, hardness upper/lower
  ×2) — all PASS.

## [Unreleased] — 2026-06-13 (Co-located tool panels: faithful mayatk layout parity)

Rebuilt the co-located tool-panel `.ui` files to mirror their **mayatk** counterparts (the
panels had diverged into simpler one-off layouts). Faithful structure, custom Blender logic
where needed, dropping only what genuinely has no Blender analogue (documented per panel).

- **Texture Path Editor** — `QListWidget`+buttons → mayatk's **3-column `TableWidget`**
  (Material · Texture Path · Image) with editable path cells (repath) + the grouped header menu
  (General / Path Management / Selection) and a per-row right-click menu (Browse / Select In Scene /
  Shader Editor / Delete). New Qt-free engine fns `btk.get_image_material_map` /
  `set_texture_directory` / `find_and_copy_textures` + `resolve_missing_textures(fuzzy=…)`.
- **Reference Manager** — library list+buttons → mayatk's **Root Directory / mode combo / Filter /
  file `TableWidget`** browse-to-link layout. New `env_utils` engine (`EnvUtils`):
  `find_blend_files` / `list_libraries` / `link_blend_file` / `reload_library` / `remove_library`.
  Maya workspaces→**Link/Append**; namespaces / assemblies / display-modes / notes dropped (N/A).
- **Material Updater**, **Shader Templates**, **Game Shader**, **HDR Manager** — wrapped in
  mayatk's groupbox structure (Presets / Template / Material-Name + Normal-Map / main_group +
  HDR-Map/Levels/Rotation + collapsable output log). Maya/Arnold-only knobs dropped with a note
  (Save-Template + progress bar, shader-type + AiBridge + Output-Template/Ext, HDR resolution +
  Arnold Advanced). **`create_pbr_material` now takes a `name`**; combo objectNames realigned to
  mayatk (`cmb001`/`cmb002`).
- **RizomUV Bridge** — controls wrapped in the `grp_process` groupbox (one-way send kept — the Maya
  round-trip stays out per the existing design).
- **Verified**: `texture_path_editor` + `reference_manager` engine tests 11/11 + 12/12 (real
  Blender headless); handler discovery + `.ui` load-wiring 38/38; mat/light/bridge/ui suites green.
  3 panels now **identical** widget surfaces to mayatk; the rest differ only in documented drops.

## [Unreleased] — 2026-06-13 (Game Shader: PBR-texture → Principled material)

- **`btk.create_pbr_material(textures, name=None, normal_direction="OpenGL")`** (`mat_utils`) +
  co-located **Game Shader** panel (`mat_utils/game_shader`, `marking_menu.show("game_shader")`).
  Blender mirror of mayatk's `GameShader.create_network`: classify a set of PBR texture files via the
  SHARED `ptk.MapFactory.resolve_map_type` (same SSoT as the Material Updater) and wire each into a
  Principled BSDF with the needed conversion nodes — Base Color, Metallic, Roughness (or
  glossiness/smoothness → Invert → Roughness), Normal via a Normal Map node (+ a Separate/Invert/
  Combine **green-flip** for DirectX normals), Ambient Occlusion **multiplied** into Base Color
  (MixRGB), Emission (+ strength), Alpha (+ HASHED blend), Bump/Height → Bump, a packed **ORM** split
  (Separate Color → R=AO, G=Roughness, B=Metallic), and Displacement on the material output. Correct
  color spaces (sRGB for color maps, Non-Color for data). Distinct from Shader Templates (parameter
  presets) — this wires *textures*. **Needs no image library** (node setup only; Blender loads the
  images), so it runs in Blender's Python as-is. The panel picks files or a folder and optionally
  assigns to the selection.
- **Verified**: real Blender headless — full network (color spaces, Normal Map, AO MixRGB,
  glossiness-invert, DirectX green-flip, ORM split, no-classifiable → None); handler discovery +
  `.ui` load-wiring 38/38.

## [Unreleased] — 2026-06-13 (external-app bridges: Substance / Marmoset / RizomUV)

The Blender bridge tools — export the selection and hand it to an external app. Substance/Marmoset
**reuse the DCC-agnostic extapps workflows**; RizomUV (no extapps equivalent) gets a focused bundled
bridge.

- **`btk.export_selection_fbx(filepath=None, objects=None, **fbx_opts)`** (`core_utils`) — the
  non-interactive counterpart of the scene slot's "Export Selection" (which opens Blender's native
  dialog): exports the current selection (or `objects`, restoring the prior selection afterward) to
  an FBX for a hand-off, with bridge-sensible defaults (mesh-only, modifiers applied). Raises if
  nothing is selected. The mirror of mayatk's `FbxUtils` export role; shared by all three bridges.
- **`blendertk.uv_utils.rizom_bridge`** — bundled **RizomUV bridge** (engine `RizomBridge` +
  `RizomBridgeSlots` + `rizom_bridge.ui`, discovered via `marking_menu.show("rizom_bridge")`). Focused
  mirror of mayatk's `RizomUVBridge` *send-to-RizomUV* path: discovers the RizomUV exe
  (`AppLauncher.find_app` + a `Program Files\Rizom Lab\*` scan — the installer doesn't register App
  Paths), exports the selection to FBX, writes a small Lua load-script (`ZomLoad` + optional
  pcall-wrapped `ZomLoadTexture` per on-disk texture — mirrors `send_wrapper.lua`), and launches
  RizomUV detached with `-cfi`. The Maya **round-trip** (re-import UVs into the scene) is intentionally
  **not** mirrored — Blender has strong native UV tooling, so the bridge is for interactive RizomUV
  work. SDK-specific engine bundled with its panel (extapps convention), not in `UvUtils`.
- **Verified**: real Blender headless — `export_selection_fbx` (writes the FBX, restores selection,
  raises on empty), `RizomBridge.build_send_script` (Lua path/booleans/texture block), exe discovery
  (resolved the installed `RizomUV 2020.1` on the dev box, returns None gracefully otherwise); handler
  discovery + `.ui` load-wiring 34/34; uv_utils suite green.

## [Unreleased] — 2026-06-13 (Material Updater + on-demand image-lib provisioning)

Co-located **Material Updater** panel (`mat_utils/mat_updater.{ui,py}`) mirroring mayatk's, plus the
provisioning + engine that make the whole texture-tool class actually work in Blender's Python.

- **`btk.ensure_image_deps(packages=None)`** (`core_utils`) — makes image libraries importable in
  Blender's Python (default `Pillow → PIL`). Blender bundles numpy but **not** PIL/cv2, which the
  shared `ptk.ImgUtils`/`MapFactory` need; this pip-installs the missing wheels into Blender's
  per-version *user-modules* dir via `ptk.PackageManager` driven against Blender's **bundled**
  interpreter (not the `blender.exe` binary) — the same on-demand model tcl_blender uses for Qt, but
  owned in the **library layer** (the entry point stays logic-free). Idempotent, Blender-gated,
  never raises. Two non-obvious robustness fixes baked in: (1) it does **not** trust pip's exit code
  — a `--target` install emits a non-zero "dependency resolver" ERROR when the *base* env has an
  unrelated conflict (an editable extapps wanting qtpy) even though the requested wheel installed
  fine, so it re-checks real importability instead; (2) `_rebind_pil_globals` patches the
  `Image = None` globals that pythontk's image modules cached at import time (they import before
  provisioning can run in Blender), so the already-loaded `MapFactory` picks PIL up without a
  fragile module reload. An explicit empty `packages={}` is a true no-op (guarded on `is not None`).
- **`btk.MatUpdater.update_materials(materials, config, ...)` / `btk.update_materials`**
  (`mat_utils/_mat_utils.py`) — batch-reprocess materials' textures (format / max-size / optimize /
  packed-map gen) via the SHARED pythontk factory (`MapRegistry.resolve_config` +
  `MapFactory.prepare_maps`), then repath each material's image nodes to the results. Collect /
  reconnect glue is Blender-idiomatic (image datablocks). Reconnection is **fallback-aware**: the
  factory canonical-renames inputs on output (e.g. a `Diffuse` node's map becomes `Base_Color`), so
  matching inverts `MapRegistry.get_fallbacks()` to map each original node to the output it became
  (exact type first, then fallback). **Blender divergence (intentional):** repaths existing
  per-channel image nodes; does NOT pack/rewire ORM/MSAO into the shader (Blender's Principled BSDF
  has separate Roughness/Metallic/AO — no packed slot), though packed maps still land on disk for
  engine export. Dry-run / no-texture paths are offline-safe (no provisioning).
- **Verified**: real Blender headless end-to-end (Diffuse PNG → reprocessed `Base_Color.tga`, image
  node repathed) + dry-run/empty/no-op paths; handler discovery + `.ui` load-wiring 30/30; the
  Pillow provision into Blender 5.1's Python 3.13 (cp313 wheel) succeeds.

## [Unreleased] — 2026-06-13 (material tool panels: Texture Path Editor + Shader Templates)

Co-located tool panels mirroring mayatk's material tools (discovered by `BlenderUiHandler`,
served via `marking_menu.show("<tool>")`; engines in `MatUtils`, headless-tested).

- **Texture Path Editor** (`mat_utils/texture_path_editor.{py,ui}`) — list every file texture with
  its path (missing flagged), repath the selected one, resolve missing by searching a folder, and
  normalize paths. Engine: `get_image_records`, `repath_image`, `resolve_missing_textures`
  (basename match, shallowest wins), `normalize_texture_paths` (`relative`/`absolute`/`copy`/`move`
  external-into-project), `format_texture_paths_html`. The Blender datablock analogue of Maya's
  file-node re-pathing (`img.filepath`); subsumes the old native Find-Missing-Files.
- **Shader Templates** (`mat_utils/shader_templates.{py,ui}`) — quick-create / apply a Principled-
  BSDF preset (Metal / Glass / Emission / Skin …). Engine: `get_shader_templates`,
  `create_shader_template`, `apply_shader_template` (version-tolerant — unknown 4.x inputs skipped).
  Documented divergence: Blender's single über-shader makes a template a *parameter preset* rather
  than Maya's full Stingray/Standard-Surface graph rebuild.
- **Tests**: `test_mat_anim_utils` extended (texture-path engine on real temp files + shader
  templates); `test_blender_ui_handler` panel count 23/23. Full suite **20/20**.

## [Unreleased] — 2026-06-13 (slot-gap helpers + mtk library parity)

Backs the next round of tentacle Blender slot parity (animation / scene Get-Info,
Optimize/Bake, Cleanup) and deepens the `btk ↔ mtk` public surface. All headless-testable.

- **`anim_utils`** — `optimize_keys` (pure `keyframe_points` math: drop constant curves after
  writing the held value back to the property — lossless; remove interior flat keys; optional
  collinear `simplify`; returns curves/keys before/after stats), `tie_keyframes` (add/remove
  bookend keys at the playback-range boundaries), `bake_keys` (wraps native `nla.bake` — resolves
  constraints/drivers/parenting via visual keying; auto POSE+OBJECT for armatures), and
  `get_animation_info` + `format_animation_info_html` (per-object range/channel/key report).
  The Graph-Editor `clean`/`decimate` ops can't run `--background`, hence the pure-data approach.
- **`core_utils`** — `get_scene_info` + `format_scene_info_html` (object/poly/material audit — the
  focused Blender analogue of Maya's adaptive game-ready SceneAnalyzer) and `cleanup_scene`
  (purge orphan datablocks with no users / no fake user across the main collections, repeating
  until stable; render-result/viewer images and scene/world are never touched).
- **`xform_utils`** — `get_bounding_box` (combined world/local bbox dict, single-value via `value=`),
  `get_center_point`, `get_distance` (objects / Vectors / 3-seqs), `order_by_distance`,
  `aim_object_at_point` (mirror of Maya's aimConstraint via `to_track_quat`; preserves loc/scale).
- **`node_utils`** — hierarchy mirror: `get_parent` (+ `all=` ancestor chain), `get_children`
  (+ `recursive=`), `get_shape` (`obj.data`), `reparent` (keep-transform).
- **Tests**: `test_mat_anim_utils` (anim + scene-info/cleanup), `test_xform_utils`,
  `test_node_utils` extended — **all 20 suites pass**.

## [Unreleased] — 2026-06-13 (MatUtils Maya mirror)

- **`mat_utils` grown to mirror mayatk's `MatUtils` material workflow** (backs the rewritten
  tentacle Blender materials slot). New, all datablock-level (headless-testable, no PIL —
  texture metadata is read from the Blender image datablock natively):
  - **`get_scene_mats`** (inc/exc name filter, `sort`, `as_dict`; grease-pencil mats dropped;
    `exclude_defaults` accepted for parity — Blender has no built-in defaults),
    **`is_mat_assigned`** (object-user check), **`get_mat_swatch_icon`** (QIcon from
    `material.diffuse_color`; Qt-deferred → None headless).
  - **`get_texture_paths`** / **`get_texture_info`** (walk `TEX_IMAGE` nodes / `bpy.data.images`),
    **`get_mat_info`** (same record schema as mayatk: material/type/textures + native
    width/height/mode/format/bit_depth; `type` = the surface shader node label; optional
    `optimize_check` is best-effort via `ptk.MapOptimizer`, degrading gracefully where Blender's
    Python lacks the image stack).
  - **`format_mat_info_html`** / **`format_texture_info_html`** delegate to `ptk.MatReport`
    (shared SSoT with mayatk — no duplicated formatting).
  - **`find_materials_with_duplicate_textures`** / **`reassign_duplicate_materials`** (group by
    texture-path signature → reassign objects to the canonical mat → delete dupes),
    **`delete_unused_materials`** (object-unassigned, respects fake-user), **`graph_materials`**
    (activate a user object + open the Shader Editor — the Hypershade analogue).
  - **Verified headless**: `test_mat_anim_utils.py` +16 material cases (scene-mats filters,
    is-assigned, texture path/info, get_mat_info schema + surface-type, exclude-unassigned,
    format delegation, duplicate detect/reassign, delete-unused + fake-user protection);
    full suite 20/20.

## [Unreleased] — 2026-06-13 (slot option parity)

- **New helpers backing the tentacle Blender slot option-restoration pass** (see the tentacle
  CHANGELOG for the per-menu UI detail):
  - **`edit_utils.find_problem_geometry`** — the diagnostic half of Maya's Mesh Cleanup
    (`repair=False`): bmesh detection (+ optional select) of n-gons / concave / non-planar /
    interior / non-manifold / loose components, returning per-criterion counts and the natural
    component select mode. (Maya's "lamina" has no analogue — bmesh forbids coincident faces.)
  - **`edit_utils.clear_custom_split_normals`** — clears custom split normals (Maya "unlock
    normals"); imported FBX/Marmoset assets carry them and they silently block re-smoothing.
  - **`edit_utils.dissolve_coplanar(delimit=…)`** — planar Decimate `delimit` flags so a
    coplanar dissolve preserves hard edges / UV borders (Maya "Preserve …" options).
  - **`edit_utils.crease_edges(mark_sharp=…)`** — optionally flags the creased edges sharp too
    (stand-in for Maya's per-edge smoothing angle; Blender crease is binary).
  - **`anim_utils.set_interpolation(objects, interpolation, handle=…)`** — general key
    interpolation/tangent-type setter (CONSTANT/LINEAR/BEZIER/…); `set_stepped` now delegates.
  - **`ui_utils.open_editor(properties_context=…)`** — open the Properties editor on a specific
    tab (VIEW_LAYER / OBJECT / …) so the rendering slot can land on Blender's render-setup /
    per-object render-visibility homes.
  - **Verified headless**: `test_edit_utils` (+detector/clear-normals/delimit/mark-sharp cases),
    `test_mat_anim_utils` (+interpolation/handle/scale-pivot), `test_ui_utils` 21/21; full suite
    20/20.

## [Unreleased] — 2026-06-13 (lightmap baker)

- **`light_utils.lightmap_baker` (new): the Blender counterpart of mayatk's `LightmapBaker`.**
  Bakes scene lighting → a texture per object for game engines (Unity-first). Where the Maya
  workflow had to orchestrate Arnold RTT + an alpha-mask seam dilation + a white-card material
  swap, **Blender ships the whole bake natively in Cycles**, so this is a much thinner adapter:
  - **`btk.create_lightmap_uvs`** / **`find_lightmap_uv_set`** (uv_utils): a packed,
    non-overlapping lightmap UV on a **second** channel (`smart_project(scale_to_bounds)`),
    reusing a pre-existing lightmap-named layer. Index-1 = Unity uv2, so the manifest's
    `uvIndex:1` is automatic — no UV reorder for the lighting-only path.
  - **`btk.LightmapBaker`** engine (Qt-free, `bpy`-deferred): `bake_separated` (lighting-only,
    the default) = Cycles `DIFFUSE` with `pass_filter={'DIRECT','INDIRECT'}` — the **native
    white-card irradiance, no material swap** (Cycles excludes the color pass directly, unlike
    Maya which had to assign a temp white Lambert). `bake_fused` = `COMBINED` (albedo×lighting).
    Seam gutter is the **native `bake.margin`** (no `pythontk.dilate_image` needed). The bake is
    non-destructive to materials (a temp image-texture node is added per material, removed after).
  - **Non-destructive commit/revert**, stamped as object **custom properties** (survive
    save/reload, instance-independent): `commit_lightmap` keeps the full PBR material + UVs and
    stamps a `lightmapInfo` marker; `commit_unlit` assigns an unlit **Emission** material sampling
    the EXR via the lightmap UV (marks it `active_render`) and stamps a `lightmapCommit` restore
    record; `revert`/`revert_lightmap`/`revert_unlit` undo whichever level each object is in.
  - **Unity bridge** = new **`btk.DataNodes`** (node_utils, mirror of mayatk's): a `data_export`
    **Empty** carrying per-producer JSON manifests as custom properties (ride the FBX as user
    props when *Custom Properties* export is on). `_publish_lightmap_metadata` regenerates the
    scene-wide manifest from the markers (additive on bake, subtractive on revert) — camelCase
    keys (`name`/`map`/`uvIndex`/`intensity`/`scaleOffset`) matching unitytk's `LightmapRecord`,
    so the existing `LightmapMetadataImporter`/`Applier` consumes it unchanged.
  - **Quality presets** via `LightmapBaker.preset_store()` / `from_preset` (pythontk
    `PresetStore`, built-in JSON `preview` 256/2, `quest` 1024/4, `desktop` 2048/8; samples =
    Cycles bake samples).
  - **Co-located panel**: `lightmap_baker.ui` + `LightmapBakerSlots` (discovered by
    `BlenderUiHandler`, opened by tentacle `lighting b001` → `marking_menu.show("lightmap_baker")`).
    Mode combo (Lighting Only default / Fused Unlit) + Quality preset + Resolution/Samples dials +
    name affix; `b000` = revert→bake→commit, header menu = Revert to Source / Open Output. The
    panel omits Maya's atlas-by-material **Packing** combo (deferred — Blender ships no cv2 for the
    `assemble_atlas` repack; per-object is the default everywhere anyway).
  - **Verified**: new headless `test_lightmap_baker.py` 21/21 — a **real Cycles bake** on a tiny
    scene (UV2 gen + idempotency, lighting-only bake→EXR-on-disk→material kept, manifest camelCase
    + `uvIndex:1` + data_export Empty, subtractive revert, fused→unlit commit/revert); handler
    discovery 21/21 (`.venv`, panel deep-loads + combos populate); uv_utils/node_utils/uv_shells
    suites regression-clean.

## [Unreleased] — 2026-06-13 (hotkey macros)

- `edit_utils.macros` (new): the Blender counterpart of `mayatk.edit_utils.macros`. A `Macros`
  class of 22 `m_*` viewport/edit/selection/animation toggles (back-face culling, isolate/local
  view, wireframe/shading/lighting cycles, smooth-preview subsurf, frame, object/vertex/edge/face
  select modes, multi-component, invert, paste, merge-by-distance, group-under-empty, set/unset
  transform keys, …) + a `MacroManager` whose `set_macros("m_name, key=1, cat=Display", …)`
  accepts the **identical string spec** Maya's `userSetup.py` uses — only here it parses the key
  (digits→`ONE`…`NINE`, letters→upper, `ctl/alt/sht`→ctrl/alt/shift) and registers **keymap items**
  bound to a single dispatcher operator (`btk.macro`), instead of a Maya runtime command. So the
  same hotkey muscle memory transfers between DCCs. Each macro is bound into the **3D View, Object
  Mode, and Mesh** keymaps (addon keyconfig): the generic `3D View` keymap is evaluated *after* the
  mode keymaps, so a default in `Object Mode`/`Mesh` (e.g. `1`, `f`, `ctl+m`) would otherwise shadow
  a `3D View`-only item — adding the item to those mode keymaps overrides the default for that key.
  `set_macros` is idempotent
  (clears prior items first → Reload-Scripts safe); `remove_macros` for teardown. Runtime-only
  module (defines a `bpy.types.Operator`, imports `bpy` at top) — deliberately **not** in
  `DEFAULT_INCLUDE`, imported explicitly by the Blender startup script. Where concepts diverge
  from Maya it adapts the Blender-idiomatic way (display cycle is a reversible Textured→Wire→Bounds
  draw cycle, since actually hiding an object drops it from the selection; no UV-shell select mode
  in Blender → Vertex/Face toggle). Headless suite `test_macros.py` 30/30 (engine + key translation
  + keymap registration + idempotency).
- Macro **fidelity pass** (mirror the Maya versions more faithfully): viewport macros now act on
  the **active** viewport the key fired in (`context.area`, like Maya's focused panel) instead of
  the first viewport found; `m_wireframe` (3) cycles the **wireframe-on-shaded overlay**
  Off→Full→Reduced (Maya's `wireframeOnShadedActive`), distinct from `m_shading` (4)'s mode switch;
  `m_lighting` (6) switches to Solid only when needed and no longer resets the light each press
  (it stopped yanking you out of Material/Rendered preview); `m_merge_vertices` (ctl+m) now also
  merges in **Object Mode** across the selected meshes (bmesh), not just Edit Mode; `m_group`
  (ctl+g) parents under an Empty at the **selection center** keeping world transforms (Maya's
  group + center-pivot) via a new `center=` option on the shared `_group_under_empty`.

## [Unreleased] — 2026-06-13 (parity gap sweep: Calculator)

A full side-by-side audit of every tentacle Blender slot vs. its Maya counterpart (shared-`.ui`
widget coverage + every co-located panel referenced by `marking_menu.show`) found one real
dead-end: `utilities b002` "Calculator" called `show("calculator")`, but the calculator panel
lived only in mayatk — so the button did nothing in Blender. Now closed:

- `ui_utils.calculator` (new): co-located `CalculatorController` + `CalculatorSlots` +
  `calculator.ui` — the Blender port of mayatk's Calculator. The DCC-agnostic engine (safe
  expression eval + length-unit conversion) is the **shared** `ptk.MathUtils.eval_expression` /
  `convert_length_unit` (no duplication — same engine the Maya panel now delegates to); only the
  time helpers are Blender-specific (`scene.render.fps` / `fps_base`, `scene.frame_current`). The
  Maya panel's `maya_container` animation group becomes a DCC-neutral `dcc_container`. Served by
  `BlenderUiHandler` (`marking_menu.show("calculator")`). Headless engine + display-action suite
  `test_calculator.py` 16/16; discovery test extended to 10 panels (deep-loads the calculator,
  20/20).

## [Unreleased] — 2026-06-12 (parity batch: Color Manager + transfer_pivot)

Closing remaining mayatk↔blendertk feature gaps driven by the tentacle slots.

- `display_utils.color_manager` (new): `ColorManager` engine + co-located `ColorManagerSlots` +
  `color_manager.ui` — the Blender port of mayatk's Color Manager. A swatch palette that
  color-codes objects across three channels: **material** (an ID material's base color),
  **object color** (`obj.color` — Blender's per-object viewport tint, the single analogue of
  Maya's separate outliner + wireframe tints), and **vertex** (a mesh color attribute).
  `apply_color` / `get_objects_by_color` (threshold match) / `reset_colors` (drops only the
  tool's `ID_*` materials, keeps user materials). Served by `BlenderUiHandler`
  (`marking_menu.show("color_manager")`). Headless engine suite `test_color_manager.py` 13/13.
- `xform_utils`: + `transfer_pivot` — move the target objects' origins onto the **first**
  object's origin without moving geometry (3D-cursor → `ORIGIN_CURSOR`; mirror of mayatk's
  `transfer_pivot` source=objects[0] convention). Only Maya's translate pivot maps — Blender's
  origin is a single point, so the rotate/scale-pivot flags are accepted for signature parity
  but no-op. `test_xform_utils.py` extended (origin moved, geometry preserved at zero drift).
- `test_blender_ui_handler.py`: now asserts all **nine** co-located panels discover (+ color_manager).

## [Unreleased] — 2026-06-12 (structure: tool panels co-located in blendertk, mirroring mayatk)

Brought the Blender tool-panel layout in line with the mayatk/tentacle split — panels now live
in blendertk next to their engine, not in tentacle.

- `ui_utils.blender_ui_handler` (new): `BlenderUiHandler` — the Blender analogue of
  `MayaUiHandler`. Scans the blendertk package recursively (`discover_slots`, `source_tags=
  {"blendertk"}`) so a tool's co-located `<tool>.ui` + `<Tool>Slots` are served by
  `marking_menu.show("<tool>")`. Wired into tentacle's `tcl_blender` via `handlers={"ui": …}`.
- **Tool panels moved out of tentacle into blendertk** (co-located with their engine + `.ui`):
  `_curtain_utils.py` → `edit_utils/curtain.py` (+ `CurtainSlots`); new `edit_utils/mirror.py`,
  `edit_utils/cut_on_axis.py` (thin Slots over `EditUtils.mirror` / `.cut_along_axis`);
  `_duplicate_utils.py` split into self-contained `edit_utils/duplicate_linear.py` /
  `duplicate_radial.py` / `duplicate_grid.py` (engine + Slots each; shared object-array
  primitives `_copy_object`/`_group_under_empty`/`_join_copies` moved to `_edit_utils`);
  `light_utils/hdr_manager.py`; `env_utils/reference_manager.py` (new `env_utils` package). The
  eight `.ui` files moved from `tentacle/ui/blender_menus/` to their blendertk module dirs (only
  the `blender#startmenu` nav menu stays in tentacle).
- The relocated `<Tool>Slots` are **tentacle-independent** (`ptk.LoggingMixin` base, `self.sb` +
  `btk.selected_objects` + direct engine calls), with the Qt-only `uitk` `fmt` import **deferred**
  into its method so the engine surface still imports Qt-free under headless Blender. New
  `core_utils.selected_objects` backs the selection reads. `*_ui.py` is now gitignored (the `.ui`
  is the SSoT; the loader regenerates the compiled module).
- Tests: `test_curtain_utils.py`/`test_duplicate_utils.py` → `test_curtain.py`/`test_duplicate.py`
  (public-API tests, unchanged logic); new `test_blender_ui_handler.py` (discovery — 15/15 under
  `.venv`, skips→PASS under Blender). Full suite 16/16; tentacle structural suites 29/29; panel
  slot harness 25/25.

## [Unreleased] — 2026-06-12 (ui_utils: native-menu bridge for the Blender both-button menu)

- `ui_utils._ui_utils` (new helpers): `call_native_menu(menu_idname)` — pop Blender's
  **own** native menu (e.g. `VIEW3D_MT_add`) at the cursor via `bpy.ops.wm.call_menu`
  under a VIEW_3D override; the Blender-idiomatic analogue of Maya's Qt-menu *wrapping*
  (Blender draws its UI in OpenGL — there are no `QMenu`/`QAction` objects to harvest).
  Backs tentacle's both-button menu (`blender#startmenu`). GUI-only **and headless-safe**:
  a `bpy.app.background` guard returns `None` before touching the op, which otherwise faults
  natively (`EXCEPTION_ACCESS_VIOLATION`) under `--background`. `menu_exists(menu_idname)` —
  `hasattr(bpy.types, …)` validity check backing the no-dead-links guard (mirrors how an
  editor name is validated against `get_editor_types`).
- `core_utils.get_view3d_context()` (new, public): the shared "first VIEW_3D area/region"
  context-override resolver — **consolidates the duplicate** that lived in the `cameras` slot
  and in `call_native_menu`'s private `_first_view3d_context` (both now call this). Returns the
  full window/screen/area/region/scene superset so every viewport caller (`view3d.*` ops,
  `wm.call_menu`) is served; `region` may be `None` (callers guard). `cameras.py` migrated to it.
- `test_ui_utils.py` (new — `ui_utils` previously had no test file): `menu_exists`
  real/bogus, both `call_native_menu` guards (unknown menu + headless-safe), the editor-name
  map, and surface resolution (module-level + on `UiUtils`). 19/19 headless.

## [Unreleased] — 2026-06-12 (gap batch 4: curtain over the shared ptk drape engine)

- `edit_utils._curtain_utils` (new): `create_curtain` — the Blender build over
  `ptk.CurtainDrape` (the drape engine extracted from mayatk's curtain tool the same
  day, so both DCCs drape identically from identical parameters): bmesh grid from
  ``grid_points()`` with grid UVs and smooth shading, post-ops thickness → applied
  Solidify, reduce → `decimate`, invert → reversed faces. `curtain_rail_from_selection`
  mirrors mayatk's ``Rail.from_selection`` (edit-mode mesh edges via
  ``ptk.arrange_points_as_path`` / a curve object via its evaluated tessellation /
  2+ objects' world positions — with a ``view_layer.update()`` guard: fresh objects
  have stale ``matrix_world``).
- Headless suite: `test_curtain_utils.py` 14/14 (engine-grid position parity at zero
  drift, post-ops, rail resolution); full aggregate 14/14 suites.

## [Unreleased] — 2026-06-12 (gap batch 3: world-HDRI light_utils)

- `light_utils` (new): `set_world_hdri` / `get_world_hdri` — the world-environment
  backend for the tentacle HDR Manager panel (mirror of mayatk's ``light_utils`` skydome
  contract). Builds an Environment-Texture → Background rig on the scene world with a
  Mapping node for Z rotation (degrees at the API, radians at the node); nodes are
  found-or-created by fixed names so repeated calls update in place; ``filepath=None``
  updates levels only (ValueError when no map is assigned); ``visible=False`` maps to
  ``film_transparent`` (engine-agnostic — the HDR keeps lighting the scene).
- Headless suite: `test_light_utils.py` 15/15 (roundtrip, update-in-place, link topology
  — compare bpy nodes with ``==``, never ``is``: RNA wrappers are recreated per access);
  full aggregate 13/13 suites.

## [Unreleased] — 2026-06-12 (gap batch 2: wedge / snap / explode / UV shells)

- `edit_utils`: + `wedge` (Maya ``WedgePolygon`` via ``bmesh.ops.spin`` — selected faces
  sweep about a selected hinge edge of those faces, active edge wins; sweep oriented
  outward along the average face normal by a probe rotation; the on-axis hinge verts are
  welded afterwards or every step leaves degenerate zero-area quads) and
  + `snap_closest_verts` (``mathutils.kdtree`` over the target's world verts; each source
  vert within tolerance moves exactly onto its nearest — Maya's ``freeze_transforms`` flag
  was a cmds world-query workaround and is not mirrored).
- `display_utils` (new): `explode_view` / `unexplode_view` / `is_exploded` — the
  exploded-view toggle (mirror of mayatk's ``ExplodedView`` workflow): objects move away
  from the group bbox center along their own **geometry-center** offsets (so frozen
  objects with origins at world zero explode too; exactly-centered geometry nudges
  deterministically) until no world bboxes overlap; pre-explode locations are stamped as
  custom props for exact restore.
- `uv_utils`: + UV-island detection (`_uv_islands` — union-find over UV-continuous edges;
  a seam splits the island) backing four new helpers: `stack_uv_shells` (targeted islands
  move to the first island's bbox center; EDIT mode targets selection-touched islands,
  object mode all — Maya's similarity grouping is a documented divergence),
  `distribute_uv_shells` (even centers between the endpoint islands, per object),
  `straighten_uvs` (selected UV edges within the angle of horizontal/vertical snap flat;
  co-located loops on a vert move together so islands never tear), and `get_uv_coords` /
  `set_uv_coords` (snapshot pair for stack/unstack-style toggles).
- Headless suites: `test_wedge_snap_explode.py` 15/15, `test_uv_shells.py` 13/13; full
  aggregate 12/12 suites.

## [Unreleased] — 2026-06-12 (tool-panel backends: mirror / cut-on-axis / duplicate arrays + Preview)

- `edit_utils`: + `mirror` (bmesh duplicate+reflect across an axis plane; merge modes —
  `-1` separate `<name>_mirror` object, `0` in-mesh unwelded, `1` in-mesh with the seam
  welded; pivots object/world/bbox-face; reflection composed exactly in world space —
  `M⁻¹·R_w·M` — so non-uniform object scale can't skew it) and + `cut_along_axis`
  (bmesh `bisect_plane`, Maya semantics: N cuts spaced `span/(amount+1)` centered on the
  pivot; `delete` clears the signed-axis side — `"x"` deletes +X, callers invert the UI
  sign; `delete+mirror` = symmetrize with the seam welded). Shared plane helpers
  `_plane_frame` / `_local_reflection` / `_local_bisect_plane` / `_duplicate_reflect`.
- `edit_utils._duplicate_utils` (new): `duplicate_linear` (per-copy matrix composed from
  the shared `ptk.ProgressionCurves` factor — scale in the source frame, orbit about the
  pivot frame, translate along its axes), `duplicate_radial` (matrix orbit about a world
  axis through the pivot point; full-revolution sweeps drop the shared endpoint; Maya's
  uniform full-translate pre-shift quirk deliberately NOT mirrored), `duplicate_grid`
  (world-bbox + spacing steps, source keeps the origin cell, `GRID_MAX_COPIES` cap).
  All three: `instance` = linked duplicates, `combine` = joined mesh (data made
  single-user first — join chokes on multi-user), else grouped under a world-origin Empty.
- `core_utils.preview` (new): `Preview` — the Blender analogue of mayatk's hermetic
  preview (same slot-facing API: enable checkbox + commit button + `refresh()`), built on
  **snapshot/restore**: name-diff deletes whatever the op created (objects, collections,
  orphaned datablocks), captured sources get their data/matrix/collections restored (a
  deleted source is recreated), commit pushes ONE undo step. Duck-typed widgets — no Qt
  import, headless-testable with stubs.
- Headless suites: `test_mirror_cut.py` 17/17, `test_duplicate_utils.py` 20/20,
  `test_preview.py` 21/21; full aggregate 10/10 suites.
- `xform_utils`: `freeze_transforms` now **stamps the pre-freeze channels** as custom
  props (`btk_{T,R,S}_bake`, composing with existing bakes — the cumulative
  freeze/unfreeze contract: T adds, R quaternion-composes, S multiplies; `store=False`
  opts out) and + `restore_transforms` (un-freeze: composes stored ∘ current back into
  the local transform and counter-shifts the geometry via `data.transform`, so world
  position is preserved — mirror of `mtk.restore_transforms`). + `scale_connected_edges`
  (each CONNECTED set of selected edges scales about its own centroid — union-find over
  shared verts; edit-mode selection-based like `crease_edges`; tuple factors scale in
  local axes, documented divergence). xform suite 23/23.

## [Unreleased] — 2026-06-12 (final anim helpers + aggregate test runner)

- `anim_utils`: + `add_intermediate_keys` (sampled key every ``step`` frames between each
  fcurve's endpoints, bisect-guarded against existing keys), `remove_intermediate_keys`
  (keeps only the endpoints — Maya tooltip semantics), `select_keys`
  (`select_control_point` by all/range, mirror of `mtk.select_keys`). Mat/anim suite 31/31.
- `test/Run-Tests.ps1`: aggregate runner — every suite in its own fresh background Blender,
  `===RESULT===` sentinels collected, non-zero exit on any failure.

## [Unreleased] — 2026-06-12 (stub-deepening batch: uv + anim helpers)

- `uv_utils`: + `get_texel_density` / `set_texel_density` (mirror of mtk — density =
  `sqrt(uv_area / world_area) * map_size`; world area via Newell's method, UV area via the
  shoelace formula — exact for non-convex faces where fan-triangulation overcounts; SET is
  per-OBJECT about its UV bbox center, a documented divergence from Maya's per-shell scale),
  + `transform_uvs` (flip U/V + rotate about the combined UV bbox center — one shared pivot
  so multi-object maps stay aligned), + `pin_uvs` (bmesh `pin_uv`; `selected_only` pins the
  selected verts' UVs — the 3D-edit-mode workflow, no UV editor needed). The mode-aware
  edit/object bmesh pattern extracted from `move_uvs` into `_uv_edit` (now shared by all).
- `anim_utils`: + `adjust_key_spacing` (shift keys at/after a frame), `align_selected_keyframes`
  (moves `select_control_point` keys to the earliest/latest/explicit frame — co-located keys on
  one fcurve merge, as expected), `set_visibility_keys` (keys `hide_viewport`+`hide_render`).
- Headless suites: uv 15/15, mat/anim 25/25.

## [Unreleased] — 2026-06-12 (post-completion stub deepening)

- `anim_utils`: + `move_keys_to_frame(objects, frame=None, retain_spacing=True)` — mirror of
  `mtk.move_keys_to_frame` (backs the shared `animation tb006` Move Keys): one global offset
  lands the selection's earliest key on the target frame preserving relative timing, or
  per-action first-key alignment. Returns the number of actions moved. Shift mechanics
  extracted to `_shift_fcurves` (now shared by `shift_keys`/`stagger_keys`/
  `move_keys_to_frame`). Headless suite extended to 20/20.

## [Unreleased] — 2026-06-12 (Phase-4 completion)

The full tentacle Blender slot surface is now backed — blendertk = 9 util modules:

- `ui_utils`: `open_editor` / `get_editor_types` (new-window `Area.ui_type` switch — the
  Blender analogue of Maya's editor windows; 22 editors).
- `mat_utils`: `get_mats`, `create_mat`, `assign_mat`, `find_by_mat_id`,
  `select_by_material`, `reload_textures` (datablock-level; no shading engines).
- `anim_utils`: `get_fcurves`, `shift/invert/stagger/snap/scale_keys`, `set_stepped`,
  `delete_keys`, `fit_playback_range`, `copy/paste_keys`. **Slot-aware:** Blender 5.x drops
  the legacy `Action.fcurves` (slotted/layered actions) — fcurves resolve via
  `layers → strips → channelbag(slot)` with a legacy fallback; paste assigns the copied
  action and its first slot.
- `edit_utils.boolean_op`: Boolean-modifier orchestration (base = first object).
- `core_utils`: `get_recent_files` (Blender's recent-files.txt) and `get_recent_autosave`
  (temp-dir scan), mirroring the mayatk names.
- New headless suite `test_mat_anim_utils.py` (16 checks); edit_utils suite extended.

## [Unreleased] — 2026-06-12 (review fixes)

Full-port review fixes (regression cases added to `test/test_edit_utils.py`):

- `edit_utils.clean_geometry`: new `merge=True` flag decouples the doubles merge from the
  degenerate dissolve — previously a caller disabling merge by passing `merge_distance=0`
  silently disabled `degenerate` too (its `dist` was the same value). The degenerate threshold
  is now floored (`max(merge_distance, 1e-6)`) so exact-zero geometry dissolves even with
  merging off.
- `core_utils._object_mode`: the mode restore now re-activates the **caller's** active object
  first — helpers select/activate their own targets, and `mode_set` acts on the active object,
  so e.g. invoking `decimate(B)` while editing `A` previously put `B` into edit mode on exit.
  Guards `ReferenceError` for the case where the helper deleted the original active.

## [Unreleased] — 2026-06-12

Util modules added to back the tentacle Blender slot ports (all headless-tested via
`blender --background`):

- `xform_utils`: `freeze_transforms`, `drop_to_grid`, `center_pivot`, `get_pivot_modes`,
  `match_scale`, `move_to`, `get_world_bbox`.
- `node_utils`: `replace_with_instances`, `get_instances`, `uninstance` (linked-data instancing;
  counts object users, not `data.users`, to ignore fake users).
- `cam_utils`: `adjust_camera_clipping` (auto from scene bbox / reset / explicit).
- `edit_utils`: `decimate`, `dissolve_coplanar`, `triangulate`, `tris_to_quads`, `subdivide_mesh`,
  `set_subdivision`, `set_shading`, `set_edge_hardness`, `flip_normals`, `recalculate_normals`,
  `crease_edges` (5.1: crease layer is `bm.edges.layers.float["crease_edge"]`), `clean_geometry`.
- `uv_utils`: `move_uvs`, `delete_extra_uv_sets`.
- `core_utils`: shared `_object_mode` guard (promoted here; used by xform + edit utils);
  `get_env_info` gained `workspace` / `workspace_dir` (the saved `.blend` directory).

## [0.1.0] — 2026-06-11

Initial scaffold.

- `bootstrap_package` wiring mirroring mayatk; public surface registered via `DEFAULT_INCLUDE`.
- `core_utils`: `undoable` (single Blender undo step) + `get_env_info` (scene/env info),
  exposed module-level and on `CoreUtils` (`btk.undoable` / `btk.get_env_info`).
- `pyproject.toml`, `CLAUDE.md`, headless `blender --background` smoke test.
