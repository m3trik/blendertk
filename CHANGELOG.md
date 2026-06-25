# Changelog

## [Unreleased]

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
