# !/usr/bin/python
# coding=utf-8
"""Scene audit -- the Blender port of mayatk's ``core_utils.diagnostics.scene_audit``.

The engine behind Get Scene Info: a sectioned game-readiness report over the scene's
meshes, materials and images, built with ``ptk.ReportDoc`` in the same sections, the same
budget model and the same look as ``mtk.SceneAnalyzer``, so the two DCCs' reports read
alike.

Meshes are measured as they SHIP: the evaluated mesh (modifiers applied, as the FBX and
glTF exporters write it by default) of every object in scope -- a selected root brings
every object under it -- plus every instance the depsgraph expands on top: collection,
geometry-node and particle instances, owned by the object that generates them. A mesh
with no faces renders nothing and is not measured. Instances of one evaluated mesh
(linked duplicates, a collection instanced fifty times) count once as "unique" and once
per instance as "rendered". Texture costs are counted once per image FILE and judged per
material, never repeated on every mesh that wears the material.

``import bpy`` is deferred into the call bodies so the module resolves headlessly. Every
public module-level class here joins ``btk.Diagnostics`` (the ``->Diagnostics`` alias
takes them all), so imported helpers stay private or annotation-only.
"""

from __future__ import annotations

import math
import os
import time
from typing import TYPE_CHECKING, Callable, Dict, Iterable, List, Optional, Set, Tuple

import pythontk as ptk

if (
    TYPE_CHECKING
):  # a class since Python 3.11 -- imported at run time it joins Diagnostics
    from typing import Any

_Doc = ptk.ReportDoc


class SceneInfoSection:
    """Get Scene Info's report sections -- mirror of ``mtk.SceneInfoSection``.

    ``ALL`` is the render order and ``LABELS`` the headings, both drift-tested against
    mayatk's. The dependency sets gate :class:`SceneAnalyzer`'s collection: a section set
    that shows no material data skips the material walk, and one that shows no texture
    data skips the image reads (a header read and a size stat per file).
    """

    OVERVIEW = "overview"
    SUMMARY = "summary"
    FIX_FIRST = "fix_first"
    PARETO = "pareto"
    OFFENDERS = "offenders"
    MATERIALS = "materials"
    TEXTURES = "textures"
    PIPELINE = "pipeline"
    ASSUMPTIONS = "assumptions"
    #: RETIRED (until 2026-09-24) -- a multi-material-meshes table; :attr:`MATERIALS`
    #: replaced it. :meth:`normalize` still resolves it, warning until 0.13.0.
    CATEGORIES = "categories"

    ALL: Tuple[str, ...] = (
        OVERVIEW,
        SUMMARY,
        FIX_FIRST,
        PARETO,
        OFFENDERS,
        MATERIALS,
        TEXTURES,
        PIPELINE,
        ASSUMPTIONS,
    )

    LABELS: Dict[str, str] = {
        OVERVIEW: "Scene Overview",
        SUMMARY: "Executive Summary",
        FIX_FIRST: "Fix First",
        PARETO: "Top Contributors",
        OFFENDERS: "Top Issues by Asset",
        MATERIALS: "Materials",
        TEXTURES: "Textures",
        PIPELINE: "Pipeline Integrity",
        ASSUMPTIONS: "Notes & Assumptions",
    }

    # The material walk: whatever reads what a mesh wears. Pareto is mayatk's member
    # alone -- its slot counts come off mayatk's material caches, where here they come
    # with the meshes themselves -- which leaves this set equal to _NEEDS_TEXTURES
    # today; the two gate different work and stay apart.
    _NEEDS_MATERIALS: Set[str] = {
        SUMMARY,
        FIX_FIRST,
        OFFENDERS,
        MATERIALS,
        TEXTURES,
        PIPELINE,
    }

    # The image reads: whatever surfaces texture data -- the offenders' too, a mesh's
    # oversized unique texture set being one of its issues (mayatk's set).
    _NEEDS_TEXTURES: Set[str] = {
        SUMMARY,
        FIX_FIRST,
        OFFENDERS,
        MATERIALS,
        TEXTURES,
        PIPELINE,
    }

    _resolve_retired = staticmethod(
        ptk.Deprecation.values(
            {CATEGORIES: MATERIALS},
            what="SceneInfoSection key",
            remove_in="0.13.0",
            since="2026-09-24",
            reason="The Materials section lists every material with its cost.",
        )
    )

    @classmethod
    def normalize(cls, sections: Optional[Iterable[str]]) -> List[str]:
        """*sections* as known keys, de-duplicated in the caller's order.

        ``None`` means all, in :attr:`ALL` order; unknown keys are dropped (a UI may pass
        through whatever the user picked); a retired key resolves to its replacement,
        with a deprecation warning.

        Parameters:
            sections: Section keys, or ``None``.

        Returns:
            The keys to render, in the order given.
        """
        if sections is None:
            return list(cls.ALL)
        out: List[str] = []
        for key in sections:
            key = cls._resolve_retired(key)
            if key in cls.LABELS and key not in out:
                out.append(key)
        return out


class SceneAnalyzer:
    """Get Scene Info in Blender -- mirror of ``mtk.SceneAnalyzer``'s sectioned report."""

    #: Budgets: ``mtk.AuditProfile``'s defaults, mirrored -- blendertk cannot import
    #: mayatk, and ``test/test_scene_audit.py`` fails the moment the two drift. Adaptive
    #: scales :attr:`MAX_TRIS` linearly with the world-space diagonal of a mesh's largest
    #: instance up to :attr:`REFERENCE_DIAG_CM`, floored at :attr:`MIN_TRIS`; Generic is a
    #: flat :attr:`MAX_TRIS` per mesh.
    MAX_TRIS = 20_000
    MIN_TRIS = 500
    REFERENCE_DIAG_CM = 200.0
    MAX_SLOTS = 4
    MAX_UVS = 2
    #: Scene texture memory budget, block-compressed GPU MB (mips included).
    TEXTURE_BUDGET_MB = 512.0
    #: Texels a unique texture set needs per meter of its object's diagonal
    #: (``mtk.SceneAnalyzer.TEXELS_PER_METER``); a 2K+ set over twice that is oversized.
    TEXELS_PER_METER = 512
    #: Rows a ranked table shows before folding the rest into a footer.
    TABLE_ROWS = 12

    #: Principled-BSDF socket -> logical channel, for an image whose filename carries
    #: no map-type token (the manifest's socket map, inverted).
    _SOCKET_CHANNELS: Dict[str, str] = {}

    _SEVERITY_TONES = {"high": "error", "medium": "warn", "low": "dim"}
    _SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}

    # ------------------------------------------------------------------ #
    # Public entry points
    # ------------------------------------------------------------------ #
    @classmethod
    def format_audit_html(
        cls,
        adaptive: bool = False,
        objects=None,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        sections: Optional[Iterable[str]] = None,
        scope: Optional[str] = None,
    ) -> Dict[str, str]:
        """Run the audit and return ``{"_header": html, section: html, ...}``.

        Parameters:
            adaptive: The Adaptive (Game Ready) budget profile, else Generic.
            objects: Objects (or their names) to audit, each with everything under
                it. ``None`` resolves *scope* instead.
            progress_callback: Optional ``callback(current, 100, message)``, ticked
                through the collection (``mtk.SceneAnalyzer``'s shape).
            sections: :class:`SceneInfoSection` keys; ``None`` means all. The
                collection skips what no requested section shows.
            scope: With no *objects*: ``"all"`` (every scene object) or
                ``"selection"`` (default).

        Returns:
            ``{"_header": html, section: html, ...}`` in the requested order.
        """
        return cls._format(
            "to_html", adaptive, objects, progress_callback, sections, scope
        )

    @classmethod
    def format_audit_text(
        cls,
        adaptive: bool = False,
        objects=None,
        sections: Optional[Iterable[str]] = None,
        scope: Optional[str] = None,
    ) -> Dict[str, str]:
        """:meth:`format_audit_html` as plain text (console / log)."""
        return cls._format("to_text", adaptive, objects, None, sections, scope)

    @classmethod
    def _format(
        cls, render, adaptive, objects, progress_callback, sections, scope
    ) -> Dict[str, str]:
        from blendertk.core_utils._core_utils import CoreUtils

        selected = SceneInfoSection.normalize(sections)
        wanted = set(selected)
        if objects is not None:
            pool, scope_name = list(ptk.make_iterable(objects)), "custom"
        elif scope == "all":
            pool, scope_name = None, "all"
        else:
            # Not bpy.context.selected_objects: a screen-context member, empty
            # whenever tentacle drives the slot from its Qt timer (no window).
            pool, scope_name = list(CoreUtils.selected_objects()), "selection"
        start = time.perf_counter()
        audit = cls._collect(
            pool,
            bool(adaptive),
            collect_materials=bool(wanted & SceneInfoSection._NEEDS_MATERIALS),
            collect_textures=bool(wanted & SceneInfoSection._NEEDS_TEXTURES),
            progress_callback=progress_callback,
        )
        audit["scope"] = scope_name
        audit["seconds"] = time.perf_counter() - start
        result = {"_header": getattr(cls._doc_header(audit), render)()}
        for key in selected:
            result[key] = getattr(getattr(cls, f"_doc_{key}")(audit), render)()
        return result

    # ------------------------------------------------------------------ #
    # Collection
    # ------------------------------------------------------------------ #
    @classmethod
    def _socket_channels(cls) -> Dict[str, str]:
        if not cls._SOCKET_CHANNELS:
            from blendertk.mat_utils.mat_manifest import _SLOT_SOCKETS

            cls._SOCKET_CHANNELS = {
                socket: channel
                for channel, sockets in _SLOT_SOCKETS.items()
                for socket in sockets
            }
        return cls._SOCKET_CHANNELS

    @classmethod
    def _slot_map_type(cls, targets: Iterable[str], node) -> Optional[str]:
        """The map type implied by the surface inputs an image reaches.

        *targets* is ``_MatUtilsInternal._texture_socket_targets``' answer (the walk
        downstream through utility nodes and node groups). Several channels (a packed
        map through a Separate Color) take the first in the manifest's slot order, as
        mayatk's slot walk does. A ``Normal`` input reached through a Bump node is
        height data, not a normal map.
        """
        channels = cls._socket_channels()
        found = {channels[t] for t in targets if t in channels}
        if found == {"normal"} and any(
            link.to_node.type == "BUMP" for out in node.outputs for link in out.links
        ):
            return ptk.MapRegistry.resolve_type_from_channel("bump")
        for channel in dict.fromkeys(channels.values()):
            if channel in found:
                return ptk.MapRegistry.resolve_type_from_channel(channel)
        return None

    @staticmethod
    def _transparency(material) -> str:
        """``opaque`` / ``masked`` / ``blend``: whether the Principled Alpha is used,
        and how the material renders it.

        Blender 4.2+ reads ``surface_render_method`` (Blended sorts and blends,
        Dithered alpha-tests) -- a pre-4.2 file keeps a stale ``blend_method`` that
        the new setting never writes back, so it is not consulted there. Before 4.2,
        ``blend_method`` decides: Blend blends, Clip / Hashed alpha-test, Opaque
        renders (and exports) opaque even with the Alpha linked.
        """
        from blendertk.mat_utils._mat_utils import _MatUtilsInternal

        node = _MatUtilsInternal._principled_node(material)
        alpha = node.inputs.get("Alpha") if node is not None else None
        if alpha is None or (not alpha.is_linked and alpha.default_value >= 0.999):
            return "opaque"
        method = getattr(material, "surface_render_method", None)
        if method is not None:
            return "blend" if method == "BLENDED" else "masked"
        legacy = getattr(material, "blend_method", "OPAQUE")
        return {"BLEND": "blend", "CLIP": "masked", "HASHED": "masked"}.get(
            legacy, "opaque"
        )

    @staticmethod
    def _visible(obj, view_layer) -> bool:
        """Does *obj* render in *view_layer* (the Scene Exporter's own test)?"""
        try:
            if view_layer is not None:
                return obj.visible_get(view_layer=view_layer)
            return obj.visible_get()
        except RuntimeError:  # not in that view layer
            return False

    @staticmethod
    def _world_diag_cm(bound_box, matrix, to_cm: float) -> float:
        """World-space bounding-box diagonal of one instance, in centimeters."""
        from mathutils import Vector

        points = [matrix @ Vector(corner) for corner in bound_box]
        if not points:
            return 0.0
        return (
            to_cm
            * sum(
                (max(p[i] for p in points) - min(p[i] for p in points)) ** 2
                for i in range(3)
            )
            ** 0.5
        )

    @staticmethod
    def _image_key(image) -> str:
        """The key a texture is costed under: its FILE (the normalized absolute
        path), not the datablock -- the same file loaded twice (``wood.png`` and
        ``wood.png.001`` from two appended assets) is one texture. A packed or
        pathless image is its own datablock."""
        from blendertk.mat_utils._mat_utils import _MatUtilsInternal

        path = _MatUtilsInternal._abspath(image)
        packed = getattr(image, "packed_file", None) is not None
        return os.path.normcase(
            f"datablock:{image.as_pointer()}" if packed or not path else path
        )

    @classmethod
    def _image_info(cls, image, images: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        """One record per image FILE (:meth:`_image_key`), created on first sight and
        shared after. Size comes from the file HEADER where possible; a packed image
        is never missing; a tiled image counts every tile on disk.
        """
        from blendertk.mat_utils._mat_utils import _MatUtilsInternal

        key = cls._image_key(image)
        info = images.get(key)
        if info is not None:
            return info
        path = _MatUtilsInternal._abspath(image)
        packed = getattr(image, "packed_file", None) is not None
        files = [] if packed else _MatUtilsInternal._udim_tile_paths(image)
        size = ptk.ImgUtils.get_image_size(files[0]) if files else None
        if not size:
            try:
                size = tuple(image.size)
            except Exception:  # noqa: BLE001 -- an unloadable image
                size = (0, 0)
        if packed:
            size_bytes = getattr(image.packed_file, "size", 0) or 0
        else:
            size_bytes = sum(os.path.getsize(f) for f in files)
        try:
            map_type = ptk.MapFactory.resolve_map_type(image.filepath or image.name)
        except Exception:  # noqa: BLE001 -- not a path-like value
            map_type = None
        info = images[key] = {
            "key": key,
            "name": image.name,
            "path": (path or image.filepath or image.name).replace("\\", "/"),
            "exists": bool(files) or packed,
            "packed": packed,
            "width": int(size[0]),
            "height": int(size[1]),
            "tiles": max(1, len(files)),
            "size_bytes": size_bytes,
            "map_type": map_type or None,
            "materials": set(),  # names, for the report
            "meshes": set(),  # in-scope unique meshes
        }
        return info

    @classmethod
    def _material_entry(
        cls,
        material,
        images: Dict[str, Dict[str, Any]],
        collect_textures: bool = True,
    ) -> Dict:
        """A material's facts and the images its SURFACE reads (a node that reaches
        neither the surface shader nor the material output renders nothing and ships
        nothing, so it is not audited). *collect_textures* False reads no image."""
        from blendertk.mat_utils._mat_utils import _MatUtilsInternal

        entry = {
            "name": material.name,
            "label": material.name_full,
            "type": _MatUtilsInternal._mat_surface_type(material),
            "transparency": cls._transparency(material),
            "maps": [],
            "other": [],
            "meshes": set(),
            "instances": 0,
            # Filled from the images once every material is read (see _collect).
            "max_res": 0,
            "gpu_mb": 0.0,
            "missing": [],
        }
        tree = getattr(material, "node_tree", None)
        if (
            not collect_textures
            or tree is None
            or not getattr(material, "use_nodes", True)
        ):
            return entry
        surface = _MatUtilsInternal._surface_shader_node(material)
        for chain, node in _MatUtilsInternal._iter_image_nodes(tree):
            if node.image.source not in ("FILE", "TILED"):
                continue
            targets = _MatUtilsInternal._texture_socket_targets(surface, node, chain)
            if not targets:
                continue
            info = cls._image_info(node.image, images)
            if not info["map_type"]:
                info["map_type"] = cls._slot_map_type(targets, node)
            role = "maps" if info["map_type"] else "other"
            if info["key"] not in entry[role]:
                entry[role].append(info["key"])
            info["materials"].add(material.name)
        return entry

    @staticmethod
    def _mesh_facts(mesh) -> Dict[str, Any]:
        """Counts for one (evaluated) mesh, read once however many instances share it."""
        from blendertk.core_utils._core_utils import _CoreUtilsInternal

        tris, ngons = _CoreUtilsInternal._mesh_face_counts(mesh)
        count = len(mesh.polygons)
        indices = [0] * count
        if count:
            mesh.polygons.foreach_get("material_index", indices)
        return {
            "tris": tris,
            "ngons": ngons,
            "verts": len(mesh.vertices),
            "uv_sets": len(mesh.uv_layers),
            "used_slots": sorted(set(indices)),
        }

    @staticmethod
    def _resolve_pool(pool: Iterable[Any]) -> List[Any]:
        """*pool*'s objects -- or their names, as mayatk takes names -- each followed by
        everything under it, de-duplicated in order.

        Selecting an asset's root (an imported FBX's Empty, a parent mesh) audits the
        meshes under it, as ``mtk.SceneAnalyzer`` resolves a group to its mesh
        descendants. Keyed by ``as_pointer``: bpy hands back a fresh wrapper per access.
        """
        import bpy

        out: List[Any] = []
        seen: Set[int] = set()
        for item in pool:
            obj = bpy.data.objects.get(item) if isinstance(item, str) else item
            if not hasattr(obj, "as_pointer"):
                continue
            for member in (obj, *getattr(obj, "children_recursive", ())):
                if member.as_pointer() not in seen:
                    seen.add(member.as_pointer())
                    out.append(member)
        return out

    @classmethod
    def _collect(
        cls,
        pool: Optional[List[Any]],
        adaptive: bool,
        collect_materials: bool = True,
        collect_textures: bool = True,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> Dict[str, Any]:
        """The audit as plain data: overview, one record per unique evaluated mesh (with
        its instances), materials and image files. *pool* ``None`` is the whole scene.

        *collect_materials* / *collect_textures* False skip the material walk / the
        image reads (:class:`SceneInfoSection`'s dependency sets): no requested section
        shows them. *progress_callback* is ticked ``(percent, 100, message)`` between
        steps, never inside the depsgraph's instance iterator.
        """
        import bpy

        from blendertk.core_utils._core_utils import _CoreUtilsInternal

        from blendertk.mat_utils._mat_utils import _MatUtilsInternal

        def tick(percent: int, message: str) -> None:
            if progress_callback:
                progress_callback(percent, 100, message)

        # Scene-wide facts first, whatever the sections: the header names the file,
        # and Fix First / Pipeline read its missing libraries.
        tick(0, "Reading scene overview...")
        overview = cls._collect_overview()

        tick(1, "Resolving targets...")
        scene = bpy.context.scene
        depsgraph = bpy.context.evaluated_depsgraph_get()
        to_cm = scene.unit_settings.scale_length * 100.0
        objects = list(scene.objects) if pool is None else cls._resolve_pool(pool)
        owners = {o.as_pointer() for o in objects}

        meshes: Dict[int, Dict[str, Any]] = {}
        materials: Dict[int, Dict[str, Any]] = {}
        images: Dict[str, Dict[str, Any]] = {}

        def add(label: str, select: str, evaluated, matrix, source_data) -> None:
            mesh = evaluated.data
            # A mesh with no faces renders nothing -- a vertex-only instancer, loose
            # edge guides, a geometry-nodes object whose output is only instances
            # (those count below, through its instances) -- and uses no slot, so it
            # read as "No material assigned" (TextureBaker skips it too).
            if mesh is None or not len(mesh.polygons):
                return
            key = mesh.as_pointer()
            record = meshes.get(key)
            if record is None:
                record = meshes[key] = {
                    "key": key,
                    "name": label,
                    "select": select,
                    "instances": [],
                    "sources": set(),  # the authored mesh datablocks behind it
                    **cls._mesh_facts(mesh),
                }
            if source_data is not None:
                record["sources"].add(source_data.as_pointer())
            slots = evaluated.material_slots
            worn = [
                slots[i].material.original
                if i < len(slots) and slots[i].material
                else None
                for i in record["used_slots"]
            ]
            record["instances"].append(
                {
                    "label": label,
                    "select": select,
                    "diag": cls._world_diag_cm(evaluated.bound_box, matrix, to_cm),
                    "materials": [m for m in worn if m is not None],
                    "slots": len(worn),
                    "redundant": len(worn) - len({id_key(m) for m in worn}),
                }
            )

        def id_key(material) -> Any:
            return material.as_pointer() if material is not None else None

        # The objects in scope that render, evaluated (modifiers applied). Hidden
        # ones and those in an excluded collection -- an instancing source, say --
        # render nothing, and the Scene Exporter ships only what is visible.
        view_layer = _CoreUtilsInternal._active_view_layer()
        count = len(objects)
        for index, obj in enumerate(objects):
            tick(
                5 + int(index / count * 45),
                f"Measuring {obj.name} ({index + 1}/{count})",
            )
            if getattr(obj, "type", None) != "MESH" or obj.data is None:
                continue
            if not cls._visible(obj, view_layer):
                continue
            evaluated = obj.evaluated_get(depsgraph)
            add(obj.name, obj.name, evaluated, evaluated.matrix_world.copy(), obj.data)
        # What the depsgraph instances on top of them -- collection, geometry-node
        # and particle instances -- owned by the object that generates them.
        tick(50, "Counting instances...")
        for inst in depsgraph.object_instances:
            if not inst.is_instance or inst.object.type != "MESH":
                continue
            if inst.object.data is None:
                continue
            parent = inst.parent.original if inst.parent else None
            if parent is None or (
                pool is not None and parent.as_pointer() not in owners
            ):
                continue
            source = inst.object.original
            if source.as_pointer() == parent.as_pointer():
                # A geometry-nodes GEOMETRY instance (Instance on Points of an Object
                # Info's geometry): the depsgraph hands back the instancer itself, so
                # the instanced mesh names the row and no authored source is known.
                label, source_data = f"{inst.object.data.name} ({parent.name})", None
            else:
                label, source_data = f"{source.name} ({parent.name})", source.data
            add(label, parent.name, inst.object, inst.matrix_world.copy(), source_data)

        # Scene-wide: the authored meshes wearing each material, and the materials
        # reading each image file -- "is this texture set unique to one mesh?" must
        # see the meshes (and a trim sheet's other materials) outside the scope too.
        # Only that judgement reads them, and it is a texture finding.
        wearers: Dict[int, set] = {}
        readers: Dict[str, set] = {}
        if collect_textures:
            worn_materials: Dict[int, Any] = {}
            for obj in scene.objects:
                if obj.type == "MESH" and obj.data is not None:
                    for slot in obj.material_slots:
                        if slot.material:
                            pointer = slot.material.as_pointer()
                            wearers.setdefault(pointer, set()).add(
                                obj.data.as_pointer()
                            )
                            worn_materials[pointer] = slot.material
            for pointer, material in worn_materials.items():
                tree = getattr(material, "node_tree", None)
                for _chain, node in (
                    _MatUtilsInternal._iter_image_nodes(tree) if tree else ()
                ):
                    readers.setdefault(cls._image_key(node.image), set()).add(pointer)

        # --- per unique mesh: budget, slots, materials ---------------------------
        if collect_materials:
            tick(60, "Collecting material data...")
        for index, record in enumerate(meshes.values()):
            if collect_materials:
                tick(
                    60 + int(index / len(meshes) * 35),
                    f"Collecting material data ({index + 1}/{len(meshes)})",
                )
            record["diag"] = max((i["diag"] for i in record["instances"]), default=0.0)
            if adaptive and cls.REFERENCE_DIAG_CM > 0:
                ratio = min(1.0, record["diag"] / cls.REFERENCE_DIAG_CM)
                record["budget"] = max(cls.MIN_TRIS, int(cls.MAX_TRIS * ratio))
            else:
                record["budget"] = cls.MAX_TRIS
            record["over"] = record["tris"] - record["budget"]
            record["count"] = len(record["instances"])
            record["slots"] = max((i["slots"] for i in record["instances"]), default=0)
            record["redundant"] = max(
                (i["redundant"] for i in record["instances"]), default=0
            )
            record["draw_calls"] = sum(max(1, i["slots"]) for i in record["instances"])
            worn: Dict[int, Any] = {}
            for instance in record["instances"] if collect_materials else ():
                # Once per instance, however many of its slots hold the material.
                own = {m.as_pointer(): m for m in instance["materials"]}
                for material in own.values():
                    worn.setdefault(material.as_pointer(), material)
                    entry = materials.get(material.as_pointer())
                    if entry is None:
                        entry = materials[material.as_pointer()] = cls._material_entry(
                            material, images, collect_textures
                        )
                    entry["instances"] += 1
                    entry["meshes"].add(record["key"])
            record["materials"] = [materials[k]["name"] for k in worn]
            record["material_keys"] = list(worn)

        # --- images: GPU cost, who wears them ------------------------------------
        registry = ptk.MapRegistry() if images else None
        for info in images.values():
            info["gpu_bytes"], info["raw_bytes"] = registry.estimate_gpu_bytes(
                info["width"], info["height"], info["map_type"], info["tiles"]
            )
        for entry in materials.values():
            for key in entry["maps"] + entry["other"]:
                images[key]["meshes"] |= entry["meshes"]
            maps = [images[k] for k in entry["maps"]]
            entry["max_res"] = max(
                (max(i["width"], i["height"]) for i in maps), default=0
            )
            entry["gpu_mb"] = sum(i["gpu_bytes"] for i in maps) / 2**20
            entry["missing"] = [
                images[k]["path"]
                for k in entry["maps"] + entry["other"]
                if not images[k]["exists"]
            ]
        for record in meshes.values():
            # Every finding is shown by a section that walks the materials; without
            # them a mesh would read as wearing none.
            record["findings"] = (
                cls._mesh_findings(record, materials, images, wearers, readers)
                if collect_materials
                else []
            )
        materials_ranked = sorted(
            materials.values(),
            key=lambda m: (m["gpu_mb"], m["instances"]),
            reverse=True,
        )
        tick(100, "Done")
        return {
            "adaptive": adaptive,
            "overview": overview,
            "meshes": sorted(
                meshes.values(),
                key=lambda r: r["tris"] * r["count"],
                reverse=True,
            ),
            "materials": materials_ranked,
            "images": images,
            # Once per audit: Summary, Fix First, Textures and Pipeline all read it.
            "textures": cls._texture_totals(materials_ranked, images, registry),
        }

    @classmethod
    def _mesh_findings(
        cls, record, materials, images, wearers, readers
    ) -> List[Tuple[str, str, str]]:
        """``[(severity, kind, message)]``: what is the MESH's own to fix (mayatk's
        ``_calculate_score`` kinds); texture costs are judged per material instead."""
        findings = []
        if record["over"] > 0:
            findings.append(
                (
                    "high",
                    "high_poly",
                    f"High poly: {record['tris']:,} tris (budget {record['budget']:,}, "
                    f"+{record['over']:,})",
                )
            )
        if record["slots"] > cls.MAX_SLOTS:
            redundant = record["redundant"]
            findings.append(
                (
                    "high",
                    "draw_call_split",
                    f"{record['slots']} material slots"
                    + (f" ({redundant} redundant)" if redundant else "")
                    + f" (budget {cls.MAX_SLOTS})",
                )
            )
        if record["uv_sets"] > cls.MAX_UVS:
            findings.append(
                (
                    "low",
                    "extra_uv_sets",
                    f"{record['uv_sets']} UV sets (budget {cls.MAX_UVS})",
                )
            )
        if record["ngons"]:
            findings.append(
                (
                    "high" if record["ngons"] > 100 or record["over"] > 0 else "low",
                    "ngons",
                    cls._count(record["ngons"], "n-gon"),
                )
            )
        if not record["materials"]:
            findings.append(("medium", "unassigned", "No material assigned"))
        # A texture set nobody else in the SCENE wears -- through any material that
        # reads the file -- at more resolution than the object's size can show
        # (shared sets are judged in Textures instead).
        unique = [
            images[key]
            for mat_key in record["material_keys"]
            for key in materials[mat_key]["maps"]
            if all(
                wearers.get(m, set()) <= record["sources"] for m in readers.get(key, ())
            )
        ]
        max_res = max((max(i["width"], i["height"]) for i in unique), default=0)
        ideal = (record["diag"] / 100.0) * cls.TEXELS_PER_METER
        if max_res >= 2048 and max_res > ideal * 2.0:
            suggested = 1 << max(9, int(math.ceil(math.log2(max(ideal, 1.0)))))
            findings.append(
                (
                    "medium",
                    "oversized_texture",
                    f"Unique {max_res}px texture set on a {record['diag']:,.0f} cm "
                    f"object (~{suggested}px suffices)",
                )
            )
        return findings

    @staticmethod
    def _collect_overview() -> Dict[str, Any]:
        """Scene-wide facts: the file, units and time setup, and an object census."""
        import bpy
        from blendertk.env_utils._env_utils import EnvUtils

        scene = bpy.context.scene
        path = bpy.data.filepath or ""
        try:
            size_mb = os.path.getsize(path) / 2**20 if path else 0.0
        except OSError:
            size_mb = 0.0
        types: Dict[str, int] = {}
        for obj in scene.objects:
            types[obj.type] = types.get(obj.type, 0) + 1
        meshes = [o for o in scene.objects if o.type == "MESH" and o.data]
        units = scene.unit_settings
        libraries = [lib.filepath for lib in bpy.data.libraries]
        missing = []
        for lib in bpy.data.libraries:
            try:
                if not os.path.isfile(bpy.path.abspath(lib.filepath)):
                    missing.append(lib.filepath)
            except Exception:  # noqa: BLE001
                pass
        return {
            "path": path,
            "size_mb": size_mb,
            "units": f"{units.system.lower()} ({units.length_unit.lower()}, "
            f"scale {units.scale_length:g})",
            "settings": EnvUtils.scene_settings(scene),
            "counts": {
                "objects": len(scene.objects),
                "mesh_objects": len(meshes),
                "mesh_data": len({o.data.as_pointer() for o in meshes}),
                "armatures": types.get("ARMATURE", 0),
                "bones": sum(
                    len(o.data.bones)
                    for o in scene.objects
                    if o.type == "ARMATURE" and o.data
                ),
                "curves": types.get("CURVE", 0) + types.get("CURVES", 0),
                "empties": types.get("EMPTY", 0),
                "lights": types.get("LIGHT", 0),
                "cameras": types.get("CAMERA", 0),
                "materials": len([m for m in bpy.data.materials if m.users]),
                "images": len(
                    [i for i in bpy.data.images if i.source in ("FILE", "TILED")]
                ),
                "actions": len(bpy.data.actions),
                "shape_keys": len(bpy.data.shape_keys),
                "collections": len(bpy.data.collections),
            },
            "libraries": libraries,
            "missing_libraries": missing,
        }

    # ------------------------------------------------------------------ #
    # Rendering -- one ptk.ReportDoc per section, as mayatk's.
    # ------------------------------------------------------------------ #
    @staticmethod
    def _count(n: int, noun: str, plural: Optional[str] = None) -> str:
        return f"{n:,} {noun if n == 1 else (plural or noun + 's')}"

    @staticmethod
    def _mb(value: float) -> str:
        return f"{value:,.1f} MB" if value < 100 else f"{value:,.0f} MB"

    @staticmethod
    def _object_link(name: str, target: Optional[str] = None) -> "ptk.ReportDoc.Inline":
        """*name* as a link that selects *target* (default: the object named)."""
        return _Doc.action(name, "select", node=target or name)

    @staticmethod
    def _material_link(
        name: str, label: Optional[str] = None
    ) -> "ptk.ReportDoc.Inline":
        """*label* (``name_full``: a linked material shows its library) opening material
        *name* in the Shader Editor -- Blender's analogue of selecting a shader node."""
        return _Doc.action(label or name, "graph", node=name)

    @classmethod
    def _mesh_link(cls, record) -> "ptk.ReportDoc.Inline":
        return cls._object_link(record["name"], record["select"])

    @classmethod
    def _heading(cls, key: str) -> "ptk.ReportDoc":
        return _Doc().heading(SceneInfoSection.LABELS[key])

    @staticmethod
    def _texture_totals(materials, images, registry) -> Dict[str, Any]:
        """Scene texture totals over the files *materials* read: memory, the
        resolution histogram, half-resolution candidates, missing and non-surface
        files. *registry* is the audit's ``ptk.MapRegistry`` (``None`` when no image
        was read)."""
        maps = {k for m in materials for k in m["maps"]}
        others = {k for m in materials for k in m["other"]} - maps
        infos = [images[k] for k in maps if images[k]["exists"]]
        hist = {"4k+": 0, "2k": 0, "1k": 0, "512": 0, "<512": 0}
        candidates, savings = 0, 0.0
        for i in infos:
            dim = max(i["width"], i["height"])
            key = (
                "4k+"
                if dim >= 4096
                else "2k"
                if dim >= 2048
                else "1k"
                if dim >= 1024
                else "512"
                if dim >= 512
                else "<512"
            )
            hist[key] += 1
            if dim >= 4096 and not registry.is_resolution_critical(i["map_type"]):
                candidates += 1
                savings += i["gpu_bytes"] * 0.75 / 2**20
        return {
            "infos": sorted(
                infos, key=lambda i: (i["gpu_bytes"], i["size_bytes"]), reverse=True
            ),
            "gpu_mb": sum(i["gpu_bytes"] for i in infos) / 2**20,
            "raw_mb": sum(i["raw_bytes"] for i in infos) / 2**20,
            "disk_mb": sum(i["size_bytes"] for i in infos) / 2**20,
            "hist": hist,
            "candidates": candidates,
            "savings_mb": savings,
            # Every file the materials read, surface map or not (mayatk records
            # a missing file before it classifies the file's role).
            "missing": sorted(
                (images[k] for k in maps | others if not images[k]["exists"]),
                key=lambda i: i["path"],
            ),
            "other": sorted((images[k] for k in others), key=lambda i: i["name"]),
        }

    @classmethod
    def _instances_wearing(cls, audit, names) -> int:
        """Instances wearing any material in *names* -- each counted once."""
        return sum(
            1
            for record in audit["meshes"]
            for instance in record["instances"]
            if any(m.name in names for m in instance["materials"])
        )

    @classmethod
    def _doc_header(cls, audit) -> "ptk.ReportDoc":
        path = audit["overview"]["path"]
        doc = _Doc().heading(
            f"Scene Info — {os.path.basename(path)}" if path else "Scene Info", level=1
        )
        scope = {"all": "Entire scene", "selection": "Selection"}.get(
            audit["scope"], "Objects"
        )
        profile = "Adaptive (Game Ready)" if audit["adaptive"] else "Generic"
        parts = [scope, f"{profile} profile"]
        instances = sum(r["count"] for r in audit["meshes"])
        if instances:
            parts.append(cls._count(instances, "mesh instance"))
        parts.append(f"analyzed in {audit['seconds']:.1f} s")
        return doc.text(" · ".join(parts), tone="dim")

    @classmethod
    def _doc_overview(cls, audit) -> "ptk.ReportDoc":
        doc = cls._heading("overview")
        o = audit["overview"]
        c = o["counts"]
        s = o["settings"]
        rows: List[Tuple[str, Any]] = []
        if o["path"]:
            rows.append(
                (
                    "File",
                    [
                        _Doc.file(
                            os.path.dirname(o["path"]), os.path.basename(o["path"])
                        ),
                        f"  ({cls._mb(o['size_mb'])})",
                    ],
                )
            )
        else:
            rows.append(("File", _Doc.span("untitled (never saved)", tone="warn")))
        rows.append(("Units", f"{o['units']} · Z-up · {s['fps']:g} fps"))
        rows.append(
            (
                "Frames",
                f"playback {s['frame_start']:g}–{s['frame_end']:g} · "
                f"animation {s['anim_start']:g}–{s['anim_end']:g}",
            )
        )
        rows.append(
            (
                "Geometry",
                f"{cls._count(c['mesh_objects'], 'mesh object')} of "
                f"{cls._count(c['mesh_data'], 'mesh datablock')}",
            )
        )

        def group(*pairs):
            return " · ".join(
                cls._count(c[key], noun, plural)
                for key, noun, plural in pairs
                if c.get(key)
            )

        for label, pairs in (
            (
                "Objects",
                (
                    ("objects", "object", None),
                    ("empties", "empty", "empties"),
                    ("curves", "curve", None),
                ),
            ),
            (
                "Rigging",
                (
                    ("armatures", "armature", None),
                    ("bones", "bone", None),
                    ("shape_keys", "shape-key set", None),
                ),
            ),
            ("Animation", (("actions", "action", None),)),
            ("Shading", (("materials", "material", None), ("images", "image", None))),
            (
                "Scene",
                (
                    ("lights", "light", None),
                    ("cameras", "camera", None),
                    ("collections", "collection", None),
                ),
            ),
        ):
            text = group(*pairs)
            if text:
                rows.append((label, text))
        if o["libraries"]:
            text = ", ".join(os.path.basename(p) for p in o["libraries"])
            if o["missing_libraries"]:
                missing = len(o["missing_libraries"])
                text = [text, _Doc.span(f"  ({missing:,} missing)", tone="warn")]
            rows.append(("Libraries", text))
        return doc.fields(rows)

    @classmethod
    def _doc_summary(cls, audit) -> "ptk.ReportDoc":
        doc = cls._heading("summary")
        meshes = audit["meshes"]
        if not meshes:
            return doc.text("No meshes in scope.", tone="warn")
        instances = sum(r["count"] for r in meshes)
        instanced = sum(1 for r in meshes if r["count"] > 1)
        t = audit["textures"]
        blend = {m["name"] for m in audit["materials"] if m["transparency"] == "blend"}
        masked = {
            m["name"] for m in audit["materials"] if m["transparency"] == "masked"
        }
        extra = []
        for names, label in ((blend, "alpha-blended"), (masked, "alpha-tested")):
            count = sum(1 for r in meshes if names & set(r["materials"]))
            if count:
                extra.append(f"{cls._count(count, 'mesh', 'meshes')} {label}")
        rows: List[Tuple[str, Any]] = [
            (
                "Meshes",
                f"{cls._count(instances, 'instance')} of "
                f"{cls._count(len(meshes), 'unique mesh', 'unique meshes')}"
                + (f" ({instanced:,} instanced)" if instanced else ""),
            ),
            (
                "Triangles",
                f"{sum(r['tris'] * r['count'] for r in meshes):,} rendered · "
                f"{sum(r['tris'] for r in meshes):,} unique",
            ),
            ("Vertices", f"{sum(r['verts'] * r['count'] for r in meshes):,} rendered"),
            (
                "Draw calls",
                f"~{sum(r['draw_calls'] for r in meshes):,} (one per material slot "
                "per instance, before batching)",
            ),
            (
                "Materials",
                cls._count(len(audit["materials"]), "material")
                + " in use"
                + (f" ({', '.join(extra)})" if extra else ""),
            ),
        ]
        if t["infos"]:
            over = t["gpu_mb"] > cls.TEXTURE_BUDGET_MB
            rows.append(
                (
                    "Textures",
                    [
                        f"{cls._count(len(t['infos']), 'map')} · "
                        f"{t['hist']['4k+']:,} at 4K+ · {cls._mb(t['disk_mb'])} on disk · ",
                        _Doc.span(
                            f"~{cls._mb(t['gpu_mb'])} GPU (budget "
                            f"{cls._mb(cls.TEXTURE_BUDGET_MB)})",
                            tone="error" if over else None,
                        ),
                    ],
                )
            )
        rows.append(
            (
                "Budget",
                f"{cls._count(sum(1 for r in meshes if r['over'] > 0), 'mesh', 'meshes')}"
                " over the triangle budget · "
                f"{sum(1 for r in meshes if r['slots'] > cls.MAX_SLOTS):,} over "
                f"{cls.MAX_SLOTS} material slots",
            )
        )
        return doc.fields(rows)

    @classmethod
    def _fix_actions(cls, audit) -> List[Tuple[str, str, List[Tuple[str, str]]]]:
        """``[(severity, message, [(label, object to select)])]``, most severe first --
        mayatk's ``_scene_fix_actions`` rules, where Blender has the data."""
        meshes = audit["meshes"]
        t = audit["textures"]
        actions: List[Tuple[str, str, List[Tuple[str, str]]]] = []

        def targets(records):
            return [(r["name"], r["select"]) for r in records]

        if t["missing"]:
            users = {m for i in t["missing"] for m in i["materials"]}
            actions.append(
                (
                    "high",
                    f"Relink {cls._count(len(t['missing']), 'missing texture file')} "
                    f"used by {cls._count(len(users), 'material')}.",
                    [],
                )
            )
        if t["gpu_mb"] > cls.TEXTURE_BUDGET_MB:
            n = t["candidates"]
            maps = (
                "the non-detail 4K map" if n == 1 else f"the {n:,} non-detail 4K maps"
            )
            hint = (
                f" Halving {maps} (AO / roughness / metallic ...) frees "
                f"~{t['savings_mb']:,.0f} MB."
                if n
                else " Downscale the largest maps (see Textures)."
            )
            actions.append(
                (
                    "high",
                    f"Texture memory ~{t['gpu_mb']:,.0f} MB GPU (budget "
                    f"{cls.TEXTURE_BUDGET_MB:,.0f} MB).{hint}",
                    [],
                )
            )
        over = sorted(
            (r for r in meshes if r["over"] > 0),
            key=lambda r: r["over"] * r["count"],
            reverse=True,
        )
        if over:
            excess = sum(r["over"] * r["count"] for r in over)
            actions.append(
                (
                    "high" if excess > 100_000 else "medium",
                    f"Triangle budget exceeded on {cls._count(len(over), 'mesh', 'meshes')}: "
                    f"{excess:,} rendered triangles to cut (Decimate / retopo).",
                    targets(over),
                )
            )
        split = [r for r in meshes if r["slots"] > cls.MAX_SLOTS]
        if split:
            calls = sum((r["slots"] - cls.MAX_SLOTS) * r["count"] for r in split)
            actions.append(
                (
                    "medium",
                    f"More than {cls.MAX_SLOTS} material slots on "
                    f"{cls._count(len(split), 'mesh', 'meshes')}: merging saves "
                    f"{cls._count(calls, 'draw call')}.",
                    targets(split),
                )
            )
        bare = [r for r in meshes if not r["materials"]]
        if bare:
            actions.append(
                (
                    "medium",
                    f"No material on {cls._count(len(bare), 'mesh', 'meshes')}: the "
                    "engine picks its own default.",
                    targets(bare),
                )
            )
        oversized = [
            r
            for r in meshes
            if any(kind == "oversized_texture" for _s, kind, _m in r["findings"])
        ]
        if oversized:
            actions.append(
                (
                    "medium",
                    "Oversized unique texture sets on "
                    f"{cls._count(len(oversized), 'mesh', 'meshes')}: more resolution "
                    "than the object's size can show (see Top Issues by Asset).",
                    targets(oversized),
                )
            )
        blend = [m for m in audit["materials"] if m["transparency"] == "blend"]
        if blend:
            instances = cls._instances_wearing(audit, {m["name"] for m in blend})
            actions.append(
                (
                    "low",
                    f"{cls._count(len(blend), 'alpha-blended material')} on "
                    f"{cls._count(instances, 'instance')}: alpha-test (Dithered / "
                    "Alpha Clip) is cheaper wherever hard edges will do.",
                    [],
                )
            )
        ngons = [r for r in meshes if r["ngons"]]
        if ngons:
            actions.append(
                (
                    "low",
                    f"N-gons on {cls._count(len(ngons), 'mesh', 'meshes')} "
                    f"({cls._count(sum(r['ngons'] for r in ngons), 'face')}): "
                    "triangulation at export can shade them differently.",
                    targets(ngons),
                )
            )
        extra = [r for r in meshes if r["uv_sets"] > cls.MAX_UVS]
        if extra:
            actions.append(
                (
                    "low",
                    f"More than {cls.MAX_UVS} UV sets on "
                    f"{cls._count(len(extra), 'mesh', 'meshes')}.",
                    targets(extra),
                )
            )
        missing_libs = audit["overview"]["missing_libraries"]
        if missing_libs:
            n = len(missing_libs)
            actions.append(
                (
                    "low",
                    f"{cls._count(n, 'missing library', 'missing libraries')}: "
                    f"{'its' if n == 1 else 'their'} linked data is in neither this "
                    "audit nor an export (File > External Data > Find Missing Files).",
                    [],
                )
            )
        actions.sort(key=lambda a: cls._SEVERITY_RANK[a[0]])
        return actions

    @classmethod
    def _doc_fix_first(cls, audit) -> "ptk.ReportDoc":
        doc = cls._heading("fix_first")
        actions = cls._fix_actions(audit)
        if not actions:
            if not audit["meshes"]:
                return doc.text("No meshes in scope.", tone="dim")
            return doc.text(
                "Nothing over budget and no pipeline hazards found.", tone="ok"
            )
        if not audit["meshes"]:  # the scene's own hazards, with nothing in scope
            doc.text("No meshes in scope; the scene itself:", tone="dim")
        rows = []
        for index, (severity, message, targets) in enumerate(actions, 1):
            cell: List[Any] = [message]
            if targets:
                cell += [
                    _Doc.span("  e.g. ", tone="dim"),
                    _Doc.join(cls._object_link(n, s) for n, s in targets[:3]),
                ]
                if len(targets) > 3:
                    cell.append(_Doc.span(f" +{len(targets) - 3:,} more", tone="dim"))
            rows.append(
                [
                    index,
                    _Doc.span(
                        severity.upper(), tone=cls._SEVERITY_TONES[severity], bold=True
                    ),
                    cell,
                ]
            )
        return doc.table(["#", "Severity", "Issue"], rows, align="rll")

    @classmethod
    def _doc_pareto(cls, audit) -> "ptk.ReportDoc":
        doc = cls._heading("pareto")
        meshes = audit["meshes"]
        if not meshes:
            return doc.text("No meshes in scope.", tone="dim")
        total = sum(r["tris"] * r["count"] for r in meshes) or 1
        ranked = meshes[: cls.TABLE_ROWS]  # collected heaviest first
        running, rows = 0, []
        for index, r in enumerate(ranked, 1):
            rendered = r["tris"] * r["count"]
            running += rendered
            rows.append(
                [
                    index,
                    cls._mesh_link(r),
                    f"{r['tris']:,}",
                    f"×{r['count']:,}",
                    f"{rendered:,}",
                    f"{rendered / total * 100:.1f}%",
                    f"{running / total * 100:.1f}%",
                ]
            )
        top = (
            "the top mesh carries"
            if len(ranked) == 1
            else f"the top {len(ranked)} carry"
        )
        doc.table(
            ["#", "Mesh", "Tris", "Instances", "Rendered", "Share", "Cumulative"],
            rows,
            align="rlrrrrr",
            title=f"Rendered triangles, heaviest first: {top} "
            f"{running / total * 100:.0f}%",
        )
        multi = sorted(
            (r for r in meshes if r["slots"] > 1),
            key=lambda r: r["draw_calls"],
            reverse=True,
        )[: cls.TABLE_ROWS]
        if multi:
            calls = sum(r["draw_calls"] for r in meshes) or 1
            doc.table(
                ["Mesh", "Slots", "Instances", "Draw calls", "Share"],
                [
                    [
                        cls._mesh_link(r),
                        r["slots"],
                        f"×{r['count']:,}",
                        f"{r['draw_calls']:,}",
                        f"{r['draw_calls'] / calls * 100:.1f}%",
                    ]
                    for r in multi
                ],
                align="lrrrr",
                title="Multi-material meshes by draw calls",
            )
        return doc

    @classmethod
    def _doc_offenders(cls, audit) -> "ptk.ReportDoc":
        doc = cls._heading("offenders")
        flagged = sorted(
            (r for r in audit["meshes"] if r["findings"]),
            key=lambda r: (max(r["over"], 0) * r["count"], r["tris"]),
            reverse=True,
        )
        if not flagged:
            if audit["meshes"]:
                return doc.text("No mesh-level issues.", tone="ok")
            return doc.text("No meshes in scope.", tone="dim")
        shown = flagged[: cls.TABLE_ROWS]
        return doc.table(
            ["Mesh", "Inst", "Tris / budget", "Issues"],
            [
                [
                    cls._mesh_link(r),
                    f"×{r['count']:,}",
                    f"{r['tris']:,} / {r['budget']:,}",
                    _Doc.join(
                        [
                            _Doc.span(message, tone=cls._SEVERITY_TONES[severity])
                            for severity, _kind, message in r["findings"]
                        ],
                        sep="; ",
                    ),
                ]
                for r in shown
            ],
            align="lrrl",
            title=f"{cls._count(len(flagged), 'mesh', 'meshes')} with issues, worst "
            "first (triangles over budget × instances)",
            footer=f"… {len(flagged) - len(shown):,} more"
            if len(flagged) > len(shown)
            else None,
        )

    @classmethod
    def _doc_materials(cls, audit) -> "ptk.ReportDoc":
        doc = cls._heading("materials")
        if not audit["materials"]:
            return doc.text("No materials in scope.", tone="dim")
        rows = []
        for m in audit["materials"]:
            notes = []
            if m["missing"]:
                notes.append(
                    _Doc.span(
                        cls._count(len(m["missing"]), "missing texture file"),
                        tone="error",
                    )
                )
            if m["transparency"] == "blend":
                notes.append(
                    _Doc.span(
                        "Alpha-blended (sorted, overdraw on every pixel it covers)",
                        tone="warn",
                    )
                )
            elif m["transparency"] == "masked":
                notes.append(_Doc.span("Alpha-tested (masked)", tone="dim"))
            rows.append(
                [
                    cls._material_link(m["name"], m["label"]),
                    _Doc.span(m["type"], tone="dim"),
                    f"{len(m['meshes']):,}",
                    f"{m['instances']:,}",
                    f"{len(m['maps']):,}",
                    f"{m['max_res']:,}" if m["max_res"] else "–",
                    cls._mb(m["gpu_mb"]) if m["gpu_mb"] else "–",
                    _Doc.join(notes, sep="; "),
                ]
            )
        return doc.table(
            [
                "Material",
                "Type",
                "Meshes",
                "Instances",
                "Maps",
                "Max px",
                "GPU (est.)",
                "Notes",
            ],
            rows,
            align="llrrrrrl",
            title=f"{cls._count(len(audit['materials']), 'material')} in scope, by "
            "texture memory",
        )

    @classmethod
    def _doc_textures(cls, audit) -> "ptk.ReportDoc":
        doc = cls._heading("textures")
        t = audit["textures"]
        if not t["infos"]:
            doc.text("No surface maps in scope.", tone="dim")
        else:
            hist = t["hist"]
            doc.fields(
                [
                    (
                        "Memory",
                        f"~{cls._mb(t['gpu_mb'])} GPU compressed · "
                        f"{cls._mb(t['raw_mb'])} uncompressed · "
                        f"{cls._mb(t['disk_mb'])} on disk",
                    ),
                    (
                        "Resolution",
                        " · ".join(
                            f"{label} {hist[key]:,}"
                            for key, label in (
                                ("4k+", "4K+"),
                                ("2k", "2K"),
                                ("1k", "1K"),
                                ("512", "512"),
                                ("<512", "<512"),
                            )
                        ),
                    ),
                ]
            )
            if t["candidates"]:
                doc.text(
                    f"{cls._count(t['candidates'], 'non-detail map')} at 4K+ (AO / "
                    "roughness / metallic ...) would read the same at half "
                    f"resolution: ~{cls._mb(t['savings_mb'])} GPU saved.",
                    tone="warn" if t["gpu_mb"] > cls.TEXTURE_BUDGET_MB else None,
                )
            shown = t["infos"][: cls.TABLE_ROWS]
            doc.table(
                ["Image", "Type", "Size", "Disk", "GPU (est.)", "Materials", "Meshes"],
                [
                    [
                        _Doc.file(i["path"], i["name"])
                        if not i["packed"]
                        else i["name"],
                        (i["map_type"] or "").replace("_", " "),
                        f"{i['width']}×{i['height']}"
                        + (f" ×{i['tiles']}" if i["tiles"] > 1 else ""),
                        cls._mb(i["size_bytes"] / 2**20)
                        + (" (packed)" if i["packed"] else ""),
                        cls._mb(i["gpu_bytes"] / 2**20),
                        f"{len(i['materials']):,}",
                        f"{len(i['meshes']):,}",
                    ]
                    for i in shown
                ],
                align="llrrrrr",
                title="Heaviest maps by GPU memory",
                footer=f"… {len(t['infos']) - len(shown):,} more"
                if len(t["infos"]) > len(shown)
                else None,
            )
        if t["other"]:
            doc.text(
                [
                    _Doc.span(
                        f"Not counted: {cls._count(len(t['other']), 'non-surface image')} "
                        "(read by the material, but neither its name nor the input it "
                        "reaches names a map type): ",
                        tone="dim",
                    ),
                    _Doc.join(i["name"] for i in t["other"]),
                ]
            )
        return doc

    @classmethod
    def _doc_pipeline(cls, audit) -> "ptk.ReportDoc":
        doc = cls._heading("pipeline")
        missing_libs = audit["overview"]["missing_libraries"]
        if not audit["meshes"]:
            doc.text("No meshes in scope.", tone="dim")
            if missing_libs:
                doc.text(
                    cls._count(
                        len(missing_libs), "missing library", "missing libraries"
                    ),
                    tone="warn",
                )
            return doc
        t = audit["textures"]
        problems = 0
        if t["missing"]:
            problems += 1
            doc.table(
                ["Missing file", "Materials"],
                [
                    [
                        _Doc.span(i["path"], tone="error"),
                        ", ".join(sorted(i["materials"])),
                    ]
                    for i in t["missing"][: cls.TABLE_ROWS]
                ],
                align="ll",
                wrap=[0, 1],
                title=f"Unresolved texture files: {len(t['missing']):,} (packed "
                "images live in the .blend and are never missing)",
            )
        bare = [r for r in audit["meshes"] if not r["materials"]]
        if bare:
            problems += 1
            doc.text(
                [
                    _Doc.span(
                        f"{cls._count(len(bare), 'mesh', 'meshes')} without a material: ",
                        tone="warn",
                    ),
                    _Doc.join(cls._mesh_link(r) for r in bare[: cls.TABLE_ROWS]),
                ]
            )
        if missing_libs:
            problems += 1
            doc.text(
                [
                    _Doc.span(
                        f"{cls._count(len(missing_libs), 'missing library', 'missing libraries')}"
                        " (not audited): ",
                        tone="warn",
                    ),
                    _Doc.join(os.path.basename(p) for p in missing_libs),
                ]
            )
        if not problems:
            files = len(t["infos"])
            clean = []
            if files:
                clean.append(
                    "the texture file resolves"
                    if files == 1
                    else f"all {files:,} texture files resolve"
                )
            clean.append("every mesh has a material")
            text = "; ".join(clean) + "."
            doc.text(text[0].upper() + text[1:], tone="ok")
        return doc

    @classmethod
    def _doc_assumptions(cls, audit) -> "ptk.ReportDoc":
        doc = cls._heading("assumptions")
        budget = (
            f"Adaptive triangle budget: {cls.MAX_TRIS:,} at a {cls.REFERENCE_DIAG_CM:g} "
            "cm world-space diagonal, scaled linearly with size (largest instance), "
            f"floor {cls.MIN_TRIS:,}."
            if audit["adaptive"]
            else f"Generic triangle budget: a flat {cls.MAX_TRIS:,} per mesh."
        )
        return doc.items(
            [
                "Meshes are measured as they ship: visible objects, evaluated "
                "(modifiers applied) -- hidden ones and excluded collections render "
                "nothing and the Scene Exporter skips them, and a mesh with no faces "
                "renders nothing either. A selected object brings everything under "
                "it. Rendered counts include every instance the depsgraph expands "
                "(collection, geometry-node and particle instances); unique counts "
                "measure each evaluated mesh once.",
                "Triangles are fan counts (n - 2 per face). Draw calls: one per "
                "material slot with faces, per instance, before engine batching.",
                budget,
                "GPU memory: a block-compressed estimate with a full mip chain -- "
                "BC4 / BC1 (0.5 B/px) for single-channel and RGB maps, BC5 / BC7 "
                "(1 B/px) for normal maps and maps with alpha. Uncompressed RGBA8 "
                "is shown for reference.",
                "Surface maps are classified by the ptk map taxonomy (filename "
                "first, the shader input they reach second); images neither names "
                "are listed but not counted, and image nodes that reach nothing are "
                "not read at all.",
                "Texture costs are counted once per image file and judged per "
                "material, not per mesh that wears it; a UDIM set costs every tile.",
            ]
        )


# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------
