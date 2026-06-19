# blendertk ↔ mayatk parity backlog

Tracking doc for the **full structural mirror** of mayatk in blendertk. Companion to
[`STRUCTURE.md`](STRUCTURE.md) (the current correspondence map). Update the status column as
phases land; move finished rows into `CHANGELOG.md`.

## Standing decisions (2026-06-14)

1. **Full 1:1 file mirror.** Mirror mayatk's file tree — subpackages where mayatk uses
   subpackages (`<tool>/__init__.py` + `_<tool>.py` + `<tool>_slots.py` + `<tool>.ui`), and split
   per-concern modules (e.g. `anim_utils/scale_keys.py`) rather than folding everything into
   `_<sub>.py`. Class/function names match mayatk.
2. **Extend native ops like mayatk does — unless little benefit.** Maya often had a native path too;
   mayatk added options on top. blendertk should do the same *when the extension is worth it*. Where
   the native Blender op already covers the need and the Maya extras add little (e.g. a codec matrix
   over `render.opengl`, a channel-box over the N-panel), document as N/A rather than rebuild.
3. **DRY the package-manager into pythontk.** One generic interpreter-package-manager in pythontk;
   thin `mayapy-` / `blenderpy-package-manager.bat` wrappers pass the interpreter/scan strategy.

> Note: implementations stay Blender-appropriate even under the full file mirror. Where mayatk's
> heavy machinery (e.g. the rizom `BridgeSlotsBase` preset/param/round-trip system) has little
> Blender benefit, mirror the **structure + class names** but keep the body thin.

## Phases

### Phase 1 — Structural (file moves, no behavior change)
- [x] **DONE 2026-06-14** `uv_utils/rizom_bridge.py` → `uv_utils/rizom_bridge/` subpackage
      (`__init__.py` + `_rizom_bridge.py` engine `RizomUVBridge` + `rizom_bridge_slots.py`
      `RizomBridgeSlots` + `rizom_bridge.ui`). Registration → `uv_utils.rizom_bridge._rizom_bridge`;
      `test_bridges` import updated. Verified: discovery 38/38, bridges test, full suite 24/24.
- [x] **DONE 2026-06-14** `anim_utils/scale_keys.py` (`ScaleKeys` + `scale_keys`),
      `anim_utils/stagger_keys.py` (`StaggerKeys` + `stagger_keys`). Bodies moved out of
      `_anim_utils`; the shared fcurve helpers (`_actions`/`_slot_fcurves`/`_key_range`/
      `_shift_fcurves`) stay canonical in `_anim_utils` and the split modules import them lazily
      in the call body (cycle-safe, mirrors mayatk's deferred-import pattern). `_anim_utils`
      re-imports both fns at top so `AnimUtils.scale_keys`/`stagger_keys` still resolve;
      registration moved to `anim_utils.scale_keys`/`stagger_keys`. Verified: anim test PASS,
      smoke PASS, `btk.scale_keys`/`stagger_keys`/`ScaleKeys`/`StaggerKeys` + `AnimUtils.*` all
      resolve to the same object (no shadowing).

### Phase 2 — Thin real ports (clear Blender analogue, self-contained)
- [x] **DONE 2026-06-14** `xform_utils/matrices.py` → `Matrices`. Ports mayatk's portable pure-math +
      object-matrix IO over `mathutils.Matrix`: `get_matrix`/`set_matrix`/`local_matrix`/`to_matrix`/
      `identity`/`from_srt`/`compose`/`decompose`/`extract_translation`/`inverse`/`mult`/
      `world_to_local`/`local_to_world`/`is_identity`. Convention divergence documented (Blender
      column-major `@` vs Maya row-major `*` — the multiply order flips, semantic result matches).
      Maya rigging node-builders (offsetParentMatrix/blendMatrix/aimMatrix/IK-FK/space-switch) NOT
      ported — Blender rigging is constraint/driver-based (`rig_utils` ⛔). Registered under
      `xform_utils.matrices`. Verified: `test_matrices` 24/24, xform + smoke PASS.
- [x] **DONE 2026-06-14** `env_utils/fbx_utils.py` → `FbxUtils`. Consolidated `export_selection_fbx`
      (moved here from `core_utils`) into `FbxUtils.export(filepath, objects, selection_only,
      **fbx_opts)` (.fbx auto-append + parent-dir creation + whole-scene mode) and added
      `FbxUtils.import_fbx` (returns the created objects). `export_selection_fbx`/`import_fbx` stay
      module-level (`btk.export_selection_fbx` unchanged — thin selection-only alias). mayatk's
      animation-takes / kBeforeExport auto-export machinery NOT ported (Blender FBX emits AnimStacks
      from NLA/actions; no before-export handler — same reason `ScriptJobManager` has no
      `add_om_callback`); MEL plugin/preset/option layer N/A (Blender takes opts as `bpy.ops` kwargs).
      Registered under `env_utils.fbx_utils`. Verified: `test_fbx_utils` 9/9, bridges + smoke PASS.
- [x] **DONE 2026-06-14** `core_utils/diagnostics/` subpackage → `Diagnostics`. `__init__` (docstring),
      `mesh_diag.py` (`MeshDiagnostics`; `find_problem_geometry` re-homed here from `edit_utils` +
      its `_is_convex`/`_is_planar`, importing `_bmesh_each` from `edit_utils` and `_object_mode` from
      the sibling `core_utils._core_utils`), `transform_diag.py`
      (`TransformDiagnostics.fix_non_orthogonal_axes` — detects sheared world axes via column
      orthogonality, fixes via clear-parent-keep-transform; Blender objects can't self-shear so it's
      parent-induced only, documented). `->Diagnostics` alias multi-inherits both into `btk.Diagnostics`.
      `find_problem_geometry` dropped from `EditUtils` (parity — it belongs to `Diagnostics`);
      `btk.find_problem_geometry` still resolves (from `mesh_diag`). Verified: `test_diagnostics`
      13/13 (incl. real shear round-trip), edit_utils + smoke + **all 27 suites** PASS. (`animation_diag`
      / `scene_diag` / `uv_diag` not ported — no driving Blender slot yet; add when one needs them.)

### Phase 3 — Material + anim ports + bridge base (per-item investigation)
- [x] **DONE 2026-06-14** `mat_utils/texture_baker.py` → `TextureBaker`. Extracted the generic
      Cycles bake-to-texture primitive out of `LightmapBaker` (`bake`, scene config/restore, per-object
      bake-to-EXR, material/UV/stem helpers); `LightmapBaker` now **composes** a `TextureBaker`
      (mirror of mayatk's primitive/workflow split) — its `_bake` delegates after `create_lightmap_uvs`.
      Generalized `fused`→`bake_type`/`pass_filter`/`use_pass_color`, `uv_set`→str|callable (preserves
      per-object lightmap-UV targeting). Behavior unchanged (lightmap suite 21/21). Registered under
      `mat_utils.texture_baker`; new `test/texture_baker` 12/12 (real headless bakes + state restore).
- [x] **WON'T BUILD (reasoned) 2026-06-14** `ui_utils/blender_bridge_slots.py` (`BlenderBridgeSlotsBase`).
      `MayaBridgeSlotsBase` is a 4-line `default_output_dir` override on uitk's DCC-agnostic
      `uitk.bridge.BridgeSlotsBase`. In blendertk there are **0 consumers**: `RizomBridgeSlots`
      deliberately inherits its engine directly, and substance/marmoset are tentacle button-launchers
      (below) — the backlog's own "≥2 consumers" bar fails. Revisit if a real Blender bridge panel base
      gains ≥2 consumers (then subclass `uitk.bridge.BridgeSlotsBase` with a Blender `default_output_dir`).
- [x] **WON'T BUILD (reasoned) 2026-06-14** `mat_utils/substance_bridge/`, `mat_utils/marmoset_bridge/`.
      mayatk's are heavy live-RPC subpackages (`substance_rpc`/`marmoset_rpc` + plugin server, templates,
      installers) — no Blender path (Substance has no RPC port; Toolbag needs a custom plugin). blendertk's
      bridge is `_launch_bridge` in `slots/blender/materials.py`: **export FBX (`FbxUtils`) + launch an
      existing extapps workflow panel pre-filled** — the export half is already `FbxUtils`; the launch half
      is irreducibly tentacle-coupled (`sb.handlers.external_app.launch`), not btk-engine material. The thin
      existing approach is correct; a btk subpackage would be a hollow re-export.
- [x] **WON'T BUILD (reasoned) 2026-06-14** `mat_utils/{shader_attribute_map,shader_remapper,mat_transfer,mat_manifest}.py`.
      The whole cluster is built on `ShaderAttributeMap` — normalizing **Maya's zoo of shader types**
      (lambert/blinn/aiStandardSurface, divergent attr names) to canonical PBR slots. Blender has a single
      node-based shader (Principled BSDF), native `material_slot_copy`/`make_links_data`, and blendertk's
      `_mat_utils` already covers the Blender-relevant surface (`create_pbr_material`, `apply_shader_template`,
      `get_mat_info`/`get_texture_info`/`graph_materials`). The normalization premise doesn't hold → little
      benefit. Build a thin manifest/transfer only if a concrete Blender slot needs one.
- [ ] **DEFER (YAGNI)** `anim_utils/segment_keys.py`, `anim_utils/unbake_keys.py` — no Blender anim slot
      needs them (the Blender slots use `bake_keys`/`scale_keys`/`stagger_keys`). Port when one does.
- [ ] **DEFER (YAGNI)** `edit_utils/dynamic_pipe.py` — curve-bevel/skin/geo-nodes pipe tool; no slot wants
      it yet.

### Phase 4 — Dev/test infra
- [x] **DONE 2026-06-14** `env_utils/blender_connection.py` → `BlenderConnection`. Mirror of mayatk's
      `MayaConnection` role: launches a FRESH `blender --background --factory-startup --python` per run
      (session-safe by construction — never attaches to a running Blender). `find_blender` (env →
      PATH → install-dir glob, newest), `run_script`/`run_code`/`run_result` (PASS-sentinel parse);
      launch+capture delegated to `ptk.AppLauncher.run` (no raw subprocess), only exe discovery is
      Blender-specific; no `bpy` (runs outside Blender). Registered under `env_utils.blender_connection`.
      Verified: `test_blender_connection` 11/11 (real child-Blender spawns).
- [x] **DONE 2026-06-14** Package-manager DRY (user chose: generic `.bat` + thin wrappers, to keep the
      polished self-contained UI). Extracted the shared menu/operations into `m3trik/package-manager.bat`
      (interpreter-agnostic: `%1`=python.exe, `%2`=label, `%3`=backup-prefix). `mayapy-package-manager.bat`
      (mayatk) reduced to a thin wrapper (Maya scan → resolve mayapy → `call` generic); new
      `blenderpy-package-manager.bat` (blendertk) is the Blender counterpart (scans `Blender Foundation\
      Blender *`, resolves `<install>\<ver>\python\bin\python.exe`). Each wrapper resolves the generic
      next-to-itself (distribution) or via the monorepo `m3trik` path. Made the decorations **ASCII-only**
      (dropped `CHCP 65001`) to dodge the cmd UTF-8 box-drawing parse bug that split commands. Tests:
      `mayatk/test/test_mayapy_package_manager.py` retargeted (generic = menu structure, wrapper = thin +
      handoff; 17/17 incl. live smoke) and new `blendertk/test/test_blenderpy_package_manager.py` (11/11);
      live blenderpy run detects Blender 5.1 + hands off + exits clean. Note: diverges from #3's literal
      "in pythontk" — the shared logic is batch (the manager's bootstrap role into a *bare* interpreter
      makes a self-contained `.bat` the right call); `pythontk.PackageManager` remains the Python API.

### Phase 5 — Document N/A (native covers it; Maya extras add little benefit)
- [ ] `ui_utils/channel_box` — Blender N-panel/Properties IS the channel box (native wired).
- [ ] `mat_utils/image_to_plane` — native `import_image.to_plane` is full-featured.
- [ ] `anim_utils/playblast_exporter` — `render.opengl` + scene output; codec matrix = low benefit.
- [x] `edit_utils/bevel` — ✅ 2026-06-15 — shipped `Bevel` engine (native `bmesh.ops.bevel`) +
      `BevelSlots` co-located panel (Width/Segments/Profile/Clamp + `Preview`), polygons `b011` →
      `show("bevel")`, mirroring Maya's bevel window. (Interactive *bridge* stays modal-native/deferred.)
- [ ] `node_utils/attributes` — Maya attr model ≠ Blender (custom props/RNA); thin helper only if needed.
- [ ] `ui_utils/node_icons` — Blender ships its own icon enum.
- [ ] `env_utils/script_output` — Blender native console + existing `LoggingMixin`/`TextEditLogHandler`.
- [ ] `anim_utils/smart_bake` — covered by `bake_keys` (native `nla.bake`); extend its options instead.

## Already present (no gap — listed for clarity)
`scale_keys` / `stagger_keys` (own modules — Phase 1 done), `explode_view` / `unexplode_view` /
`is_exploded` (functions in `_display_utils`), `ScriptJobManager`, `DataNodes`, `Macros`,
`RizomUVBridge` engine.
