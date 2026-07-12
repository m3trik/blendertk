# blendertk ↔ mayatk structure map

**Why this file exists:** `blendertk` mirrors `mayatk` so a change in one can be mirrored to the
other mechanically. Keep the two **structurally parallel** — same subpackage names, same main-
module filenames, same namespace class names — so "where does this live in the other package?"
is always answerable without searching. When you add/move/rename something in one package, update
the counterpart **and** this table.

This is the **structural** SSoT only. Everything measured or per-element lives elsewhere:

| Question | Source of truth |
|:--|:--|
| Which controls/widgets/handlers differ, per panel? | [`tentacle/docs/PARITY_SURFACE.md`](../../tentacle/docs/PARITY_SURFACE.md) (auto-gen) |
| Why is a given Maya element absent/different in Blender? | [`tentacle/docs/parity_map.py`](../../tentacle/docs/parity_map.py) (triage ledger — every conscious divergence, with reason) |
| Coarse depth/coverage numbers | [`tentacle/docs/PARITY_AUDIT.md`](../../tentacle/docs/PARITY_AUDIT.md) (auto-gen) |
| What to port next + how | [`tentacle/docs/PARITY_PORTING_PLAN.md`](../../tentacle/docs/PARITY_PORTING_PLAN.md) |
| Public API surface | [`API_REGISTRY.md`](../API_REGISTRY.md) |

Ship history (what landed when, test tallies, realized-mapping essays) belongs in
`CHANGELOG.md` / git history — not here.

## Standing decisions

1. **Full 1:1 file mirror** (2026-06-14) — mirror mayatk's file tree: subpackages where mayatk
   uses subpackages (`<tool>/__init__.py` + `_<tool>.py` + `<tool>_slots.py` + `<tool>.ui`),
   split per-concern modules rather than folding into `_<sub>.py`. Class/function names match.
   Implementation bodies stay Blender-appropriate (thin where mayatk's machinery has little
   Blender benefit) — mirror **names + behavior**, not signatures or line counts.
2. **Extend native ops like mayatk does — unless little benefit.** Where the native Blender op
   already covers the need, ledger the divergence in `parity_map.py` rather than rebuild.
3. **Every intentional divergence gets a `parity_map.py` entry.** A gap without a ledger entry
   is un-triaged and fails the parity sweep — that is the contract that keeps the mirror honest.

## Subpackage correspondence

Each row is `mayatk/mayatk/<sub>/` ↔ `blendertk/blendertk/<sub>/`. The **main module** is
`_<sub>.py` in both, exposing the **namespace class** (`mtk.<Class>` ↔ `btk.<Class>`).
Co-located tools ship as `<tool>.py` (+`<tool>.ui`) or a `<tool>/` subpackage in **both** packages.

| Subpackage | Namespace class | blendertk | Co-located tools / notes |
|:---|:---|:---:|:---|
| `core_utils` | `CoreUtils` | ✅ | `ScriptJobManager` (`bpy.app.handlers`), `Preview`, `diagnostics/` (`mesh_diag`, `transform_diag`; mayatk's animation/scene/uv diag not ported until a slot needs them), `auto_instancer/` (`btk.auto_instance` ↔ `mtk.auto_instance` — matching math + assembly clustering shared via pythontk `PointCloud.match_clouds`/`AssemblySorter`; both DCCs are thin adapters). Maya's `Components` folds into `edit_utils` (no component-string model). |
| `xform_utils` | `XformUtils` | ✅ | `matrices.py` (`Matrices`) — portable pure-math + object-matrix IO only; Maya rigging node-graph builders are constraint/driver territory (`rig_utils`). |
| `node_utils` | `NodeUtils` | ✅ | `DataNodes` ([data_nodes.md](data_nodes.md) — Empty + custom-property mirror of Maya's carriers); `attributes/channels/` **Channels** tool in both (Blender maps channels onto transform channels + ID props; no `_attributes.py` mirror — Maya's enum/mute/channel-box helpers have no analogue). |
| `cam_utils` | `CamUtils` | ✅ | |
| `uv_utils` | `UvUtils` | ✅ | `rizom_bridge/` (`RizomUVBridge` + `RizomBridgeSlots`). |
| `display_utils` | `DisplayUtils` | ✅ | `color_id`, `exploded_view` (engine module-level in `_display_utils`). |
| `env_utils` | `EnvUtils` | ✅ | `reference_manager`, `fbx_utils.py` (`FbxUtils`), `blender_connection.py` (`BlenderConnection` ↔ `MayaConnection` — fresh `--background` runs only), `script_output.py` (`ScriptConsole` ↔ mayatk's `ScriptConsole` — a dockable native Info-Log window skinned with the shared `uitk.ScriptOutput`; **Route 2+ "shadow a native area"** since Blender areas can't host a Qt HWND the way Maya's `workspaceControl` docks one), `blenderpy-package-manager.bat` (thin wrapper over the shared `m3trik/package-manager.bat`). **Counterpart pair:** `maya_bridge/` (`MayaBridge`) ↔ mayatk's `blender_bridge/` (`BlenderBridge`) — each named after its **target** app; template-driven (`templates/*.py` + `parameters.py` + `send/render_template`). `scene_exporter/` (`SceneExporter` + `TaskManager` — task/check pipeline over the shared `pythontk.TaskFactory`, incl. the `export_data_node` carrier task; see [data_nodes.md](data_nodes.md)). mayatk's `WorkspaceManager` is Maya-pipeline-specific. |
| `light_utils` | `LightUtils` | ✅ | `lightmap_baker`, `hdr_manager`. |
| `ui_utils` | `UiUtils` | ✅ | `BlenderUiHandler` ↔ `MayaUiHandler` (also wraps Blender's native menus for tentacle's both-button chord menu: `BlenderNativeMenus` (`blender_native_menus.py`) ↔ `MayaNativeMenus` — the bare-name → `*_MT_*` id table (Select mode-adaptive); the handler's `can_resolve`/`show` resolve + pop via `wm.call_menu`. Maya harvests live `QAction` rows into a Qt window; Blender has none, so it invokes its own menu — wrap, not recreate); `calculator`; `blender_window.py` (`BlenderWindow` — win32 GHOST enumeration / area→screen geometry / OS-owner helpers backing `script_output`'s area-shadow skin; no bpy dependency — callers pass the region. A focused sibling of tentacle's `tcl_blender._NativeWindow`, which blendertk can't import from a layer above; **DRY reconciliation deferred** to avoid destabilizing the marking-menu ownership). `style_setter/` (`StyleSetter`) mirrors **name + behavior only** (per the standing decision above), not mechanism. **Parity that IS held:** same subpackage (`style_setter/`), same class (`StyleSetter`), same shipped-data dir name (**`styles/`** in both — `styles/Maya.xml` in blendertk, `styles/Blender.json` in mayatk), same public pair `list_templates()` / `apply_template(token)` that the shared tentacle `preferences.ui` theme-selector combo (`cmb003`) drives host-agnostically, same `set_style` behavior. Neither ships a bespoke backup/restore (removed 2026-07-05). On blendertk's side this is a true no-op loss: reverting is just picking the user's own built-in/saved theme back from the same `list_templates()` set, exactly like Blender's own native selector — a backup entry duplicated something Blender's preset system already covered. **mayatk's side is NOT symmetric** — Maya has no built-in "theme" of its own, so removing its backup genuinely leaves `mtk.StyleSetter` with no revert path at all; that's an accepted tradeoff of the explicit removal ask, not a claim that mayatk gained an equivalent native fallback. **The one justified divergence — file *format*, ledgered here not drifted:** blendertk is **pure native** — it defers to Blender's own `interface_theme` preset SYSTEM (`styles/Maya.xml` is a real Blender theme preset; `install()` makes it appear in **Preferences > Themes > preset dropdown**; `list_templates()` returns that whole native set — built-in + user + our `Maya`), with no side-channel (the one thing a native theme can't carry, `view.font_path_ui`, is dropped, not smuggled in a companion, so a theme picked from Blender's own dropdown behaves identically). mayatk **cannot** do the same because **Maya has no native named-preset format OR selector for colors** — its "Colors" editor only Saves/Resets the user's single active prefs file in place (a flat MEL dump, `userRGBColors2.mel`; confirmed via `colorPrefWnd.mel`), never a named template. So mayatk overlays Maya's scriptable "Colors" prefs (`cmds.displayRGBColor`/`colorIndex`/`displayColor` — viewport bg + dormant grid/edge) via a **bespoke `styles/Blender.json`** — JSON, not "native" (there is no native named format to defer to), chosen over a flat `.mel` dump because it carries the structured data the apply step needs (grid/edge resolve to a live `colorIndex` slot per-session, which a static MEL command list couldn't without hardcoding slot numbers). Format differs; everything else is parity. |
| `mat_utils` | `MatUtils` | ✅ | `mat_updater`, `texture_path_editor`, `shader_templates`, `game_shader`, `render_opacity/`, `image_to_plane/`, `texture_baker.py` (`TextureBaker`, composed by `LightmapBaker`), `mat_manifest.py` (`MatManifest` ↔ mayatk's — baked-map metadata sidecar shared by the bridges), `marmoset_bridge/` (`MarmosetBridge` + `MarmosetBridgeSlots`) and `substance_bridge/` (`SubstanceBridge` + `SubstanceBridgeSlots`) — full mirrors of mayatk's live-RPC bridges (only the produce halves are bpy-native; the app-specific engine/RPC/template halves are vendored code-identical with mayatk's copies — Marmoset also in extapps — guarded by extapps `test_vendor_sync.py`; the generic stream machinery they compose is `pythontk.core_utils.process_stream`). mayatk's `ShaderAttributeMap` cluster is still not mirrored — single Principled BSDF + native ops cover it (ledgered). |
| `anim_utils` | `AnimUtils` | ✅ | `scale_keys.py`, `stagger_keys.py` (thin — no segment/overlap/speed machinery, ledgered), `smart_bake/`, `blendshape_animator/`. `shots/` — `BlenderShotStore` + `BlenderScenePersistence` (`_shots.py`): the Blender **acquisition + persistence** adapter over the shared shots engine in `pythontk.core_utils.engines.shots`; the shot model/planner/detection math lives once upstream, this layer only reaches the scene (slotted-action fcurve walk → pythontk boundary math; store JSON on `scene["shot_store"]`). **Panels — all three shipped (parity sweep 0 deltas each):** `shots/` (settings), `shot_manifest/` (`ShotManifestSlots` + `ManifestTableMixin` + `manifest_data` — CSV→shots table over `BlenderShotManifest`; fades via `RenderOpacity`, audio via VSE), and `shot_sequencer/` (`ShotSequencerSlots` + `ShotSequencerController` over the shared `uitk` `SequencerWidget`; the expanded `ShotSequencer` engine + `segment_collector` + 4 controller mixins [`marker_manager`/`shot_nav`/`gap_manager`/`clip_motion`]; OpenMaya callbacks → `bpy.app.handlers`). Ledgered sequencer follow-ups: audio-track display + move-to-shot grouping deferred. The shots model/planner/detection/manifest core lives once upstream in `pythontk.core_utils.engines.shots`. |
| `edit_utils` | `EditUtils` | ✅ | `Macros`, `curtain` (+`CurtainRig`, engine-level like Maya; drape math = vendored `_curtain_drape`, code-identical with mayatk's copy, guarded by extapps' `test_vendor_sync.py`), `mirror`, `cut_on_axis`, `duplicate_linear/radial/grid`, `bevel`, `bridge`, `snap`, `naming/`, `dynamic_pipe` (handler-launched only, like Maya). Object↔Edit round-trips via the shared `_edit_mesh_each`. |
| `audio_utils` | `AudioUtils` | ✅ | `audio_clips` (+`.ui`) — scene-wide sound-strip CRUD over the Video Sequence Editor (`scene.sequence_editor`). Deliberately NOT a mirror of mayatk's DG-node/composite-WAV/scriptJob machinery, none of which the VSE needs (it plays any number of strips natively and decodes MP3/OGG/FLAC itself) — see the module's own docstring + `parity_map.py`'s `audio_clips_slots` row. |
| `nurbs_utils` | `NurbsUtils` | ✅ | `NurbsUtils` base is **relaxed, not a signature mirror** (curve `bevel_depth`/`fill_mode` + one evaluated-mesh bake replace Maya's loft/planarSrf/nurbsToPoly layer): `add_spline` / `create_curve` / `curve_to_mesh`. Tools: `image_tracer.py`, `curve_to_tube.py`. The `nurbs` *menu* stays native `bpy.ops`. |
| `rig_utils` | `RigUtils` | ✅ | Shared base: constraints/drivers/handles/grouping + **armature primitives** (`create_armature`, `add_bone_chain`, `add_spline_ik`, `add_bone_constraint`, `bind_armature`, `_active_mode`). Rigs one module each: `telescope_rig`, `wheel_rig`, `shadow_rig`, `tube_rig` (+`controls.py`, `tube_path.py`); TubeRig's HYBRID panel builds its options body from per-strategy `AttributeSpec` dicts. **Driver gotcha:** build all vars before the expression, `RigUtils.refresh_drivers()` LAST, `@undo_checkpoint`; keep driver expressions branchless. |

**Rule for the ⛔ rows:** do **not** pre-create an empty `*_utils` package to match mayatk.
Create the subpackage only when a Blender slot needs a real engine helper there — at which point
use the **same subpackage + main-module name** as mayatk so the row flips to ✅.

## Conventions (identical in both packages)

- **Main module** per subpackage: `_<sub>.py`, holding module-level functions **and** a
  `<Domain>Utils` namespace class whose members are `staticmethod` wrappers. Registered via the
  root `__init__.py` `DEFAULT_INCLUDE`.
- **Subpackage `__init__.py`** = docstring only (no import side effects).
- **`import bpy` / `import maya.cmds`** deferred into call bodies (resolving the package surface
  must never need a running DCC). Engines stay Qt-free; only Slots touch Qt, lazily.
- **Co-located tool panels** live next to their engine as `<tool>.py` (engine + `<Tool>Slots`) +
  `<tool>.ui` (the tracked Designer SSoT). `<Tool>Slots` is **discovered by the UI handler**, not
  listed in `DEFAULT_INCLUDE`. `<tool>_ui.py` is a generated, gitignored artifact in both.
- Where mayatk splits a tool into a `<tool>/` **subpackage**, blendertk mirrors that layout.

## Registration style

Both call `bootstrap_package(globals(), include=DEFAULT_INCLUDE)`. mayatk's keys are mostly the
legacy bare module name with wildcard export; blendertk uses the **explicit full path + class
list**. Prefer the explicit form for new entries — it's the controlled surface.

## Porting checklist (mirror a change from one package to the other)

1. Same **subpackage** + **main-module filename**? (⛔ row → create it with mayatk's name.)
2. Same **class name** + **public method/function name** (names + behavior, not signatures).
3. New public symbol → add to `DEFAULT_INCLUDE` (verify `btk.<name>` / `mtk.<name>` resolves).
4. New co-located tool → `<tool>.py` + `<tool>.ui`; `<Tool>Slots` discovered by the handler.
5. Intentional divergence → entry in `tentacle/docs/parity_map.py` (status + reason).
6. Update this table + the package `CHANGELOG.md`; refresh registries if the surface changed
   (`python m3trik/scripts/generate_api_registry.py blendertk`); re-run
   `python m3trik/scripts/compare_panel_surface.py --all --write` — it must exit 0.
