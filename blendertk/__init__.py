# !/usr/bin/python
# coding=utf-8
from pythontk.core_utils.module_resolver import bootstrap_package


__package__ = "blendertk"
__version__ = "0.22.0"

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

# Include spec — mirrors mayatk's namespace layout. Main ``_*_utils`` modules use "*"
# (wildcard AST scan): the resolver publishes each engine class, its public methods, and the
# module-level helpers flat, so a symbol resolves as both ``btk.<method>`` and
# ``btk.<Namespace>.<method>`` with no per-name upkeep. Hide internals with a leading
# underscore — the default predicate ignores ``_``-prefixed names — never by omission.
# Tool modules that co-locate a public ``<Tool>Slots`` panel (name-discovered by
# BlenderUiHandler) stay explicit class-only, so "*" never leaks the Slots onto the surface.
DEFAULT_INCLUDE = {
    "core_utils._core_utils": "*",
    "core_utils.preview": ["Preview"],
    # Auto-instancer — mirror of mayatk's ``core_utils.auto_instancer``
    # (``btk.AutoInstancer`` ↔ ``mtk.AutoInstancer``; one-shot via ``.run_once``);
    # matching math and assembly clustering are shared via pythontk (PointCloud/AssemblySorter).
    "core_utils.auto_instancer._auto_instancer": "AutoInstancer",
    # Event-subscription manager — mirror of mayatk's ``core_utils.script_job_manager`` over
    # ``bpy.app.handlers`` (``btk.ScriptJobManager`` ↔ ``mtk.ScriptJobManager``).
    "core_utils.script_job_manager": ["ScriptJobManager"],
    # Lengths and angles by the kind of value -- mirror of mayatk's ``core_utils.units``
    # (``btk.Units`` ↔ ``mtk.Units``): Blender units and radians to what a record ships.
    "core_utils.units": "Units",
    # Diagnostics subpackage — mirror of mayatk's ``core_utils.diagnostics``. Class-only (a submodule,
    # not a ``_*_utils`` root — kept out of the flat namespace). The ``->Diagnostics`` alias
    # multi-inherits the per-module diag classes into one ``btk.Diagnostics`` namespace; the
    # problem-detection methods are reached as ``btk.Diagnostics.find_problem_geometry`` /
    # ``.fix_non_orthogonal_axes`` (or via ``btk.MeshDiagnostics`` / ``btk.TransformDiagnostics``).
    "core_utils.diagnostics->Diagnostics": "*",
    "core_utils.diagnostics.mesh_diag": "MeshDiagnostics",
    "core_utils.diagnostics.transform_diag": "TransformDiagnostics",
    # Get Scene Info's engine + its section vocabulary (mirror of mtk.SceneAnalyzer /
    # mtk.SceneInfoSection; mayatk keeps the latter in ``audit_records``).
    "core_utils.diagnostics.scene_audit": [
        "SceneAnalyzer",
        "SceneInfoSection",
    ],
    "xform_utils._xform_utils": "*",
    # Matrix helpers — mirror of mayatk's ``xform_utils.matrices.Matrices`` (the portable
    # compose/decompose/space-conversion + object-matrix IO subset over ``mathutils.Matrix``;
    # the Maya rigging node-graph builders have no Blender analogue — see the module docstring).
    "xform_utils.matrices": [
        "Matrices",
    ],
    "node_utils._node_utils": "*",
    # DataNodes lives in its own module (mirror of mayatk's ``node_utils.data_nodes``).
    "node_utils.data_nodes": "*",
    "cam_utils._cam_utils": "*",
    # Per-camera visibility sets — rolled engine for Maya's camera-sets isolate commands
    # (SetExclusiveToCamera family); class-only, use btk.CameraVisibility.<fn>.
    "cam_utils.camera_visibility": [
        "CameraVisibility",
    ],
    "uv_utils._uv_utils": "*",
    # UV-to-UV texture transfer adapter -- mirror of mayatk.uv_utils.texture_transfer
    "uv_utils.texture_transfer": ["TextureTransfer"],
    # Texture Combine engine -- mirror of mayatk's; its co-located
    # ``TextureCombineSlots`` panel is discovered by ``BlenderUiHandler``.
    "uv_utils.texture_combine.texture_combine": ["TextureCombine", "CombineInputs"],
    # RizomUV bridge engine — mirror of mayatk's ``uv_utils.rizom_bridge._rizom_bridge.RizomUVBridge``
    # (the ``RizomBridgeSlots`` panel class is discovered by the handler, not registered).
    "uv_utils.rizom_bridge._rizom_bridge": [
        "RizomUVBridge",
    ],
    "display_utils._display_utils": "*",
    # Color ID — mirror of mayatk's ``display_utils.color_id.ColorId`` (its panel is tentacle's).
    "display_utils.color_id": [
        "ColorId",
    ],
    # Exploded View -- mirror of mayatk's ``ExplodedView`` (``btk.ExplodedView``), over the
    # explode/unexplode engine module-level in ``_display_utils`` above.
    "display_utils.exploded_view": ["ExplodedView"],
    "env_utils._env_utils": "*",
    # FBX import/export — mirror of mayatk's ``env_utils.fbx_utils.FbxUtils`` (``btk.FbxUtils`` ↔
    # ``mtk.FbxUtils``). Scope-limited to the class on purpose: ``FbxUtils.export`` is a generic name
    # that would collide flat with ``UsdUtils.export`` under "*" — the source-side fix for the
    # collision (matching mayatk). Consumers use ``btk.FbxUtils.export_selection_fbx`` / ``.import_fbx``.
    "env_utils.fbx_utils": "FbxUtils",
    # USD import/export over Blender's native USD runtime — mirror of mayatk's
    # ``env_utils.usd.UsdUtils`` (``btk.UsdUtils`` ↔ ``mtk.UsdUtils``). Class-only (like
    # ``fbx_utils``, and matching mayatk): a generic ``export`` would collide flat with
    # ``FbxUtils.export`` under "*". Consumers use ``btk.UsdUtils.export_selection_usd`` /
    # ``.import_usd``. The zero-dep sniffing/packaging floor is shared in ``pythontk.file_utils.usd``.
    "env_utils.usd": "UsdUtils",
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
    "env_utils.maya_bridge._scene_import": "MayaSceneImport",
    # Live browser / WebXR preview — a hand-off bridge whose target is a loopback
    # PreviewServer rather than an app. Mirror of mayatk's ``WebXrPreview``.
    "env_utils.webxr_preview": [
        "WebXrPreview",
    ],
    # Retired 2026-10-20 (removed in 0.21.0): the sections of live-scene state
    # a GLB took beside its FBX -- the materials record states every material.
    "env_utils.scene_state": [
        "SceneState",
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
    # Hierarchy Sync — diff/repair a scene hierarchy against a reference .blend linked as a
    # library (mirror of mayatk's ``env_utils.hierarchy_sync._hierarchy_sync``; the
    # ``HierarchySyncSlots`` panel is discovered by BlenderUiHandler, not registered).
    "env_utils.hierarchy_sync._hierarchy_sync": [
        "HierarchySync",
        # Pull IS ported: hierarchy_sync_slots imports and drives this class, and the
        # mayatk twin registers ``mtk.ObjectSwapper`` — leaving it out broke the
        # name-level mirror the tentacle slots rely on.
        "ObjectSwapper",
    ],
    "env_utils.hierarchy_sync.scene_data_sidecar": "SceneDataSidecar",
    "light_utils._light_utils": "*",
    # Lightmap Baker — engine + co-located panel. The ``LightmapBakerSlots`` class is
    # discovered by ``BlenderUiHandler`` (not registered here), matching the other tool Slots.
    "light_utils.lightmap_baker.lightmap_baker": [
        "LightmapBaker",
    ],
    "light_utils.lightmap_baker.lightmap_records": [
        "LightmapRecords",
    ],
    # The WebXR delivery half of the same tool — everything between a Cycles
    # lightmap EXR and a browser-ready GLB. Registered like its sibling above
    # because it is cross-package public surface: mayatk's ``bake_lightmaps``
    # bridge template is its consumer, and the API registry already publishes it.
    "light_utils.lightmap_baker.web_export": [
        "LightmapWebExport",
    ],
    "ui_utils._ui_utils": "*",
    # Object-type icons (``Object.type`` → uitk named icon), mirror of ``mtk.NodeIcons``.
    "ui_utils.node_icons": "NodeIcons",
    # Switchboard launchable-entry handler + the native-menu driver — mirrors mayatk's
    # ``ui_utils.maya_ui_handler`` / ``ui_utils.maya_native_menus`` (``btk.BlenderUiHandler``
    # ↔ ``mtk.MayaUiHandler``), so the tentacle host composes both DCCs the same way
    # instead of deep-importing one and namespacing the other.
    "ui_utils.blender_ui_handler": [
        "BlenderUiHandler",
    ],
    # The Calculator's host half (scene FPS, current frame) -- mirror of mayatk's.
    "ui_utils.calculator": ["CalculatorController"],
    # Host cancellation strategy for uitk's slot dispatcher — parity twin of
    # ``mtk.MayaCancelProvider``. Qt-side (imports uitk), so it resolves lazily
    # and stays out of the headless engine surface.
    "ui_utils.cancel_provider": [
        "BlenderCancelProvider",
    ],
    "ui_utils.blender_native_menus": [
        "BlenderNativeMenus",
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
    # Session-persistent UI visibility state (hidden editors, viewport overlays, region flags)
    # — Blender keeps these in the .blend, not in prefs, so a startup-script ``UiState.install()``
    # is what makes them stick the way Maya's prefs do. Blender-only; no mayatk twin needed.
    "ui_utils.ui_state": [
        "UiState",
    ],
    # App-style setter — match Blender's UI chrome to another DCC's look via Blender's NATIVE
    # interface_theme preset system (ships a canonical Maya.xml theme preset in
    # style_setter/styles/ that shows up in Preferences > Themes > preset dropdown). Exposed as
    # just the class (like Bevel/Bridge/Selection) — its helpers have generic names (install,
    # is_installed, …) that don't belong in the flat btk.* namespace; use btk.StyleSetter.<fn>.
    "ui_utils.style_setter._style_setter": [
        "StyleSetter",
    ],
    "mat_utils._mat_utils": "*",
    # Structural mirror of ``mtk.ArnoldBridge`` — a documented no-op on Blender, kept
    # registered (not just importable) so duck-typed code across the twins needs no branch.
    "mat_utils.arnold_bridge": "ArnoldBridge",
    # Generic Cycles bake-to-texture primitive — mirror of mayatk's ``mat_utils.texture_baker``.
    # ``LightmapBaker`` (light_utils) composes this; use it directly for one-off/preview bakes.
    "mat_utils.texture_baker": [
        "TextureBaker",
    ],
    # Image-to-Plane — mirror of mayatk's ``mat_utils.image_to_plane`` (its panel is tentacle's).
    "mat_utils.image_to_plane._image_to_plane": [
        "ImageToPlane",
    ],
    # Per-object render effects — engine + co-located panel (``RenderEffectsSlots``
    # discovered by the handler, not registered). Mirror of mayatk's
    # ``mat_utils.render_opacity`` subpackage.
    "mat_utils.render_opacity.render_effects": ["RenderEffects"],
    # Material manifest (baked-map metadata carrier) — mirror of mayatk's
    # ``mat_utils.mat_manifest``; shared by the Marmoset/Substance bridges.
    "mat_utils.mat_manifest": [
        "MatManifest",
    ],
    # The ``materials`` record's producer (``FbxUtils.PRODUCERS``): every
    # exported material stated as glTF states it. Mirror of mayatk's.
    "env_utils.material_records": ["MaterialRecords"],
    # Emissive Groups: the API, pythontk's model over the Blender half (its panel
    # is tentacle's). Mirror of mayatk's ``mat_utils.emissive_groups``.
    "mat_utils.emissive_groups": ["EmissiveGroups", "BlenderEmissiveGroups"],
    # Game Shader — the workflow-profile / map-pipeline / reporting layer around
    # ``MatUtils.create_pbr_material`` (its panel is tentacle's shared Game Shader tool).
    # Mirror of mayatk's ``mat_utils.game_shader`` (``btk.GameShader`` ↔
    # ``mtk.GameShader``, both exposing ``create_network``).
    "mat_utils.game_shader": ["GameShader"],
    # HDR Manager and Shader Templates -- mirrors of mayatk's engines (their panels are
    # tentacle's shared tools).
    "light_utils.hdr_manager": ["HdrManager"],
    "mat_utils.shader_templates": ["ShaderTemplates"],
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
    "anim_utils._anim_utils": "*",
    # Per-concern key-timing modules — mirror of mayatk's ``anim_utils.scale_keys`` /
    # ``stagger_keys`` (engine class + module-level fn). The fns also stay on ``AnimUtils``
    # via a cycle-safe re-import in ``_anim_utils``.
    "anim_utils.scale_keys": "*",
    "anim_utils.stagger_keys": "*",
    # Shots — Blender acquisition + persistence adapter over pythontk's shots
    # engine (``core_utils.engines.shots``).  The shot model / planner / detection
    # math is shared upstream; only ``BlenderShotStore`` (scene hooks) and
    # ``BlenderScenePersistence`` (scene-property JSON) live here.  The co-located
    # Shots panel is discovered by ``BlenderUiHandler``, not registered here.
    "anim_utils.shots._shots": [
        "BlenderShotStore",
        "BlenderScenePersistence",
    ],
    # Shot detection — ``Detection`` (mirror of mayatk's ``anim_utils.shots._detection``):
    # auto / selected-key shot-region discovery over the scene's fcurves.
    "anim_utils.shots._detection": ["Detection"],
    # Animation-segment collection (motion runs split at holds) — mirror of
    # ``mtk.SegmentKeys`` Stage 1; the sequencer's track + sub-row data source.
    "anim_utils.segment_keys": "SegmentKeys",
    # Shot sequencer engine — full mayatk ``ShotSequencer`` surface (define /
    # detect / sequences / fit / key motion / ripple / reorder / respace) over
    # the shared planner; the Sequencer panel is co-located and discovered.
    "anim_utils.shots.shot_sequencer._shot_sequencer": ["ShotSequencer"],
    # Shot Manifest adapter — Blender scene hooks over the shared manifest engine
    # (CSV → shots + fade/audio behaviors).  Co-located panel discovered by
    # BlenderUiHandler, not registered here.
    "anim_utils.shots.shot_manifest._shot_manifest": ["BlenderShotManifest"],
    "anim_utils.shots.shot_manifest.manifest_host": ["ManifestHost"],
    # Key stash — key clips parked out of the working animation (Blender adapter
    # over ``ptk.KeyStash``; same name as mayatk's so tentacle's shared Key Stash
    # tool stays branch-free).
    "anim_utils.key_stash._key_stash": "KeyStash",
    # Smart Bake — engine + session/restore store, mirror of mayatk's
    # ``anim_utils.smart_bake`` (its panel is tentacle's shared Smart Bake tool).
    "anim_utils.smart_bake._smart_bake": "SmartBake",
    "anim_utils.smart_bake.bake_session": "RestoreResult",
    # Blendshape Animator — morph-authoring engine (base+target mesh -> keyed shape key, with
    # driver-driven corrective "tween" shapes for a custom curve), mirror of mayatk's
    # ``anim_utils.blendshape_animator.BlendshapeAnimator`` (its panel is tentacle's shared
    # Blendshape Animator tool).
    "anim_utils.blendshape_animator._blendshape_animator": [
        "BlendshapeAnimator",
    ],
    # Audio Clips — scene-wide sound-strip CRUD over the Video Sequence Editor, mirror of
    # mayatk's ``audio_utils`` (the ``AudioClipsSlots`` panel is discovered by
    # ``BlenderUiHandler``, not registered here).
    "audio_utils._audio_utils": [
        "AudioUtils",
    ],
    # Audio-segment snapshots over VSE sound strips (mirror of mayatk's
    # ``audio_utils.segments.AudioSegment``) — the sequencer's audio tracks.
    "audio_utils.segments": ["AudioSegment"],
    "edit_utils._edit_utils": "*",
    # Component-level mesh operations - mirror of mayatk's ``core_utils.components.Components``
    # (the normals operations tentacle's shared ``slots/normals.py`` calls).
    "edit_utils.components": "Components",
    # Category-driven select-by-type — mirror of mayatk's ``edit_utils.selection.Selection``
    # (``btk.Selection`` <-> ``mtk.Selection``), backing the shared ``list000`` "Select by Type"
    # list in ``tentacle/slots/*/selection.py``.
    # SelectionOrder: rolled ordered-object-selection tracker (Blender records no object
    # click order natively) — backs Reorder Selection + any order-consuming tool.
    "edit_utils.selection": [
        "Selection",
        "SelectionOrder",
    ],
    # Array-duplication tools — one module per pattern, mirroring mayatk's duplicate_linear /
    # _radial / _grid split (their panels are tentacle's). Class-only (matching mayatk): the
    # engine method is reached as ``btk.DuplicateLinear.duplicate_linear`` etc.; the shared
    # object-array primitives live flat in ``_edit_utils``.
    "edit_utils.duplicate_linear": "DuplicateLinear",
    "edit_utils.duplicate_radial": "DuplicateRadial",
    "edit_utils.duplicate_grid": "DuplicateGrid",
    # Curtain: the tool's API and its rig (``mtk.Curtain`` / ``mtk.CurtainRig``
    # twins). The drape engine is ``Rail`` + ``CurtainMesh`` in
    # ``edit_utils.curtain``, unregistered as in mayatk.
    "edit_utils.curtain._curtain": ["Curtain", "CurtainRig"],
    # Bevel engine — mirror of mayatk's ``edit_utils.bevel.Bevel`` (``btk.Bevel`` ↔ ``mtk.Bevel``).
    "edit_utils.bevel": [
        "Bevel",
    ],
    # Bridge engine — mirror of mayatk's ``edit_utils.bridge.Bridge`` (``btk.Bridge`` ↔ ``mtk.Bridge``).
    "edit_utils.bridge": [
        "Bridge",
    ],
    # Target Weld — interactive drag-a-vertex-onto-another merge tool, the Blender build of
    # Maya's native ``targetWeldCtx`` / ``MergeVertexTool`` (which mayatk drives directly, so
    # there is no ``mtk`` counterpart module — the mirror is name + behavior of the Maya tool
    # itself). Backs tentacle's ``polygons.b043`` / ``b008`` (mergeToCenter) on Blender. Class-only:
    # activate via ``btk.TargetWeld.activate``. (The module keeps its interactive tool as module-level
    # functions — the modal-operator body + numpy pick helpers, unit-tested by direct module import —
    # with ``TargetWeld`` the thin public facade over them; none leak onto the flat ``btk.*`` surface.)
    "edit_utils.target_weld": "TargetWeld",
    # Cut On Axis -- mirror of mayatk's ``edit_utils.cut_on_axis.CutOnAxis``.
    "edit_utils.cut_on_axis": ["CutOnAxis"],
    # Snap -- mirror of mayatk's ``edit_utils.snap.Snap``, over the ``_edit_utils`` math above.
    "edit_utils.snap": ["Snap"],
    # Dynamic Pipe — mirror of mayatk's ``edit_utils.dynamic_pipe`` (Hook-driven beveled curve in
    # place of Maya's NURBS-circle loft — no native loft in Blender).
    "edit_utils.dynamic_pipe": [
        "DynamicPipe",
    ],
    # Naming tool engine (its panel is tentacle's shared Naming tool). Mirror of mayatk's
    # ``edit_utils.naming`` subpackage.
    "edit_utils.naming._naming": [
        "Naming",
    ],
    # Hotkey macros — mirror of mayatk's ``edit_utils.macros`` (``btk.Macros`` ↔ ``mtk.Macros``).
    # Only ``Macros`` is exposed, matching mayatk (``MacroManager`` is the base, not a public symbol).
    "edit_utils.macros._macros": [
        "Macros",
    ],
    # Procedural rigs — mirror of mayatk's ``rig_utils`` (one self-contained module per rig: engine
    # + co-located ``<rig>.ui`` + ``<Rig>Slots``). ``RigUtils`` is the shared constraint/driver/handle
    # base (mirror of mayatk's ``rig_utils.RigUtils``); each rig engine is exposed too, while the
    # ``<Rig>Slots`` panels are discovered by ``BlenderUiHandler`` (not registered), as mayatk's are.
    "rig_utils._rig_utils": "*",
    "rig_utils.rig_graph_build": "RigGraphBuilder",
    "rig_utils.rig_graph_extract": "RigGraphExtractor",
    "rig_utils.controls": [
        "Controls",
        "ControlNodes",
    ],
    "rig_utils.tube_rig.tube_path": [
        "TubePath",
    ],
    "rig_utils.tube_rig._tube_rig": [
        "TubeRig",
    ],
    "rig_utils.tube_rig.strategies": [
        "TubeStrategy",
        "TubeRigBundle",
    ],
    "rig_utils.telescope_rig": [
        "TelescopeRig",
    ],
    "rig_utils.wheel_rig": [
        "WheelRig",
    ],
    "rig_utils.shadow_rig._shadow_rig": [
        "ShadowRig",
    ],
    "rig_utils.shadow_rig.shadow_preview": ["ShadowPreview"],
    "rig_utils.articulated_rig._articulated_rig": [
        "ArticulatedRig",
    ],
    "rig_utils.articulated_rig.grab_tool": ["ArticulatedRigGrab"],
    # Curve / NURBS-adjacent tools — mirror of mayatk's ``nurbs_utils``. ``NurbsUtils`` is the shared
    # curve-build / curve→mesh-bake base (Blender's bevel + 2D-fill replace Maya's loft/planarSrf/
    # nurbsToPoly layer); each curve tool engine is exposed too (their panels are tentacle's).
    "nurbs_utils._nurbs_utils": "*",
    "nurbs_utils.image_tracer": [
        "ImageTracer",
    ],
    "nurbs_utils.curve_to_tube": [
        "CurveToTube",
    ],
}

#: Public names that left this package (CODE_STANDARD section 5): no alias, so a
#: read raises MovedNameError naming the new home. ``{name: (new home, version)}``;
#: a move adds its names here in the same change set.
MOVED_NAMES = {
    "DuplicateRadialSlots": (
        "tentacle.slots.tools.duplicate_radial.DuplicateRadialSlots",
        "0.23.0",
    ),
    "EmissiveGroupsSlots": (
        "tentacle.slots.tools.emissive_groups.EmissiveGroupsSlots",
        "0.23.0",
    ),
    # The Shot Manifest panel: tentacle's slots/tools/shot_manifest.py and its
    # private _shot_manifest/ package (the .ui in ui/tools/).
    "ShotManifestSlots": (
        "tentacle.slots.tools.shot_manifest.ShotManifestSlots",
        "0.23.0",
    ),
    "ShotManifestController": (
        "tentacle.slots.tools._shot_manifest.controller.ShotManifestController",
        "0.23.0",
    ),
    "ExportPreviewMixin": (
        "tentacle.slots.tools._shot_manifest.export_preview.ExportPreviewMixin",
        "0.23.0",
    ),
    "HeaderMenuMixin": (
        "tentacle.slots.tools._shot_manifest.header_menu.HeaderMenuMixin",
        "0.23.0",
    ),
    "ManifestEditor": (
        "tentacle.slots.tools._shot_manifest.manifest_editor.ManifestEditor",
        "0.23.0",
    ),
    "ManifestSourceMixin": (
        "tentacle.slots.tools._shot_manifest.manifest_source.ManifestSourceMixin",
        "0.23.0",
    ),
    "MappingPickerMixin": (
        "tentacle.slots.tools._shot_manifest.mapping_picker.MappingPickerMixin",
        "0.23.0",
    ),
    "RangeColumnMixin": (
        "tentacle.slots.tools._shot_manifest.range_column.RangeColumnMixin",
        "0.23.0",
    ),
    "RowMenusMixin": (
        "tentacle.slots.tools._shot_manifest.row_menus.RowMenusMixin",
        "0.23.0",
    ),
    "StoreFollowMixin": (
        "tentacle.slots.tools._shot_manifest.store_follow.StoreFollowMixin",
        "0.23.0",
    ),
    "ManifestTableMixin": (
        "tentacle.slots.tools._shot_manifest.table_presenter.ManifestTableMixin",
        "0.23.0",
    ),
    "ViewCombosMixin": (
        "tentacle.slots.tools._shot_manifest.view_combos.ViewCombosMixin",
        "0.23.0",
    ),
    "ManifestData": (
        "tentacle.slots.tools._shot_manifest.manifest_data.ManifestData",
        "0.23.0",
    ),
    "SETTINGS_NS": (
        "tentacle.slots.tools._shot_manifest.manifest_data.SETTINGS_NS",
        "0.23.0",
    ),
    "HEADERS": (
        "tentacle.slots.tools._shot_manifest.manifest_data.HEADERS",
        "0.23.0",
    ),
    "COL_STEP": (
        "tentacle.slots.tools._shot_manifest.manifest_data.COL_STEP",
        "0.23.0",
    ),
    "COL_SECTION": (
        "tentacle.slots.tools._shot_manifest.manifest_data.COL_SECTION",
        "0.23.0",
    ),
    "COL_DESC": (
        "tentacle.slots.tools._shot_manifest.manifest_data.COL_DESC",
        "0.23.0",
    ),
    "COL_BEHAVIORS": (
        "tentacle.slots.tools._shot_manifest.manifest_data.COL_BEHAVIORS",
        "0.23.0",
    ),
    "COL_START": (
        "tentacle.slots.tools._shot_manifest.manifest_data.COL_START",
        "0.23.0",
    ),
    "COL_END": (
        "tentacle.slots.tools._shot_manifest.manifest_data.COL_END",
        "0.23.0",
    ),
    "STEP_ICON_COLOR": (
        "tentacle.slots.tools._shot_manifest.manifest_data.STEP_ICON_COLOR",
        "0.23.0",
    ),
    "PASTEL_STATUS": (
        "tentacle.slots.tools._shot_manifest.manifest_data.PASTEL_STATUS",
        "0.23.0",
    ),
    "ERROR_COLOR": (
        "tentacle.slots.tools._shot_manifest.manifest_data.ERROR_COLOR",
        "0.23.0",
    ),
    # Small shared tools (tentacle's slots/tools/<tool>.py, the .ui in ui/tools/).
    "BevelSlots": (
        "tentacle.slots.tools.bevel.BevelSlots",
        "0.23.0",
    ),
    "DuplicateLinearSlots": (
        "tentacle.slots.tools.duplicate_linear.DuplicateLinearSlots",
        "0.23.0",
    ),
    "DuplicateGridSlots": (
        "tentacle.slots.tools.duplicate_grid.DuplicateGridSlots",
        "0.23.0",
    ),
    "ExplodedViewSlots": (
        "tentacle.slots.tools.exploded_view.ExplodedViewSlots",
        "0.23.0",
    ),
    "DynamicPipeSlots": (
        "tentacle.slots.tools.dynamic_pipe.DynamicPipeSlots",
        "0.23.0",
    ),
    "CalculatorSlots": (
        "tentacle.slots.tools.calculator.CalculatorSlots",
        "0.23.0",
    ),
    "SnapSlots": (
        "tentacle.slots.tools.snap.SnapSlots",
        "0.23.0",
    ),
    "CutOnAxisSlots": (
        "tentacle.slots.tools.cut_on_axis.CutOnAxisSlots",
        "0.23.0",
    ),
    "MirrorSlots": (
        "tentacle.slots.tools.mirror.MirrorSlots",
        "0.23.0",
    ),
    "ImageTracerSlots": (
        "tentacle.slots.tools.image_tracer.ImageTracerSlots",
        "0.23.0",
    ),
    "CurveToTubeSlots": (
        "tentacle.slots.tools.curve_to_tube.CurveToTubeSlots",
        "0.23.0",
    ),
    "TelescopeRigSlots": (
        "tentacle.slots.tools.telescope_rig.TelescopeRigSlots",
        "0.23.0",
    ),
    "WheelRigSlots": (
        "tentacle.slots.tools.wheel_rig.WheelRigSlots",
        "0.23.0",
    ),
    "BridgeSlots": (
        "tentacle.slots.tools.bridge.BridgeSlots",
        "0.23.0",
    ),
    "ShellXformSlots": (
        "tentacle.slots.tools.shell_xform.ShellXformSlots",
        "0.23.0",
    ),
    "ColorIdSlots": (
        "tentacle.slots.tools.color_id.ColorIdSlots",
        "0.23.0",
    ),
    "ImageToPlaneSlots": (
        "tentacle.slots.tools.image_to_plane.ImageToPlaneSlots",
        "0.23.0",
    ),
    "KeyStashSlots": (
        "tentacle.slots.tools.key_stash.KeyStashSlots",
        "0.23.0",
    ),
    "SmartBakeSlots": (
        "tentacle.slots.tools.smart_bake.SmartBakeSlots",
        "0.23.0",
    ),
    "GameShaderSlots": (
        "tentacle.slots.tools.game_shader.GameShaderSlots",
        "0.23.0",
    ),
    "MatUpdaterSlots": (
        "tentacle.slots.tools.mat_updater.MatUpdaterSlots",
        "0.23.0",
    ),
    "HdrManagerSlots": (
        "tentacle.slots.tools.hdr_manager.HdrManagerSlots",
        "0.23.0",
    ),
    "ShaderTemplatesSlots": (
        "tentacle.slots.tools.shader_templates.ShaderTemplatesSlots",
        "0.23.0",
    ),
    "ArticulatedRigSlots": (
        "tentacle.slots.tools.articulated_rig.ArticulatedRigSlots",
        "0.23.0",
    ),
    "ShadowRigSlots": (
        "tentacle.slots.tools.shadow_rig.ShadowRigSlots",
        "0.23.0",
    ),
    "CurtainSlots": (
        "tentacle.slots.tools.curtain.CurtainSlots",
        "0.23.0",
    ),
    "NamingSlots": (
        "tentacle.slots.tools.naming.NamingSlots",
        "0.23.0",
    ),
    "TubeRigSlots": (
        "tentacle.slots.tools.tube_rig.TubeRigSlots",
        "0.23.0",
    ),
    "BlendshapeAnimatorSlots": (
        "tentacle.slots.tools.blendshape_animator.BlendshapeAnimatorSlots",
        "0.23.0",
    ),
    "COL_FRAME": (
        "tentacle.slots.tools.blendshape_animator.COL_FRAME",
        "0.23.0",
    ),
    "COL_NAME": (
        "tentacle.slots.tools.blendshape_animator.COL_NAME",
        "0.23.0",
    ),
    "COL_STATUS": (
        "tentacle.slots.tools.blendshape_animator.COL_STATUS",
        "0.23.0",
    ),
    "COL_TOPOLOGY": (
        "tentacle.slots.tools.blendshape_animator.COL_TOPOLOGY",
        "0.23.0",
    ),
    "COL_WEIGHT": (
        "tentacle.slots.tools.blendshape_animator.COL_WEIGHT",
        "0.23.0",
    ),
    "MODE_FRAME": (
        "tentacle.slots.tools.blendshape_animator.MODE_FRAME",
        "0.23.0",
    ),
    "MODE_WEIGHT": (
        "tentacle.slots.tools.blendshape_animator.MODE_WEIGHT",
        "0.23.0",
    ),
}

bootstrap_package(
    globals(),
    include=DEFAULT_INCLUDE,
    moved=MOVED_NAMES,
)
