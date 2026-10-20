# !/usr/bin/python
# coding=utf-8
"""Several materials into ONE: one atlas, one material, in place -- no duplicated meshes.

Blender twin of :mod:`mayatk.uv_utils.texture_combine.texture_combine` (name +
behaviour): the adapter over :class:`pythontk.UvCombine` (the plan and the
pack) and :class:`~blendertk.TextureTransfer` (the texel remap). The selected
meshes keep their geometry; what changes is their texture UV map, repacked so
every source material's islands share one 0-1 layout, and the material they
wear -- one copy of the dominant source material, wired to maps resampled from
every source into that layout.

The run, per :meth:`TextureCombine.combine`:

1. **Gather** -- per unique mesh datablock (linked duplicates collapse; every
   object using it still receives the result) its texture UV map (the map the
   materials' Image Texture nodes sample --
   :meth:`TextureTransfer.auto_source_uv_set`), its UV islands, the material
   each face wears and each island's world area (:meth:`TextureCombine.gather`).
   Blender keeps one UV per face corner, so the corners at one vertex with
   one UV are welded into a UV point first: the points and their islands are
   what Maya's UV ids and shells are, and the record stores the same shape.
2. **Plan + pack** -- :class:`pythontk.UvCombine`, exactly as mayatk: islands
   of one material that overlap (stacked copies, mirrored halves) move as one
   rigid piece, sized by world texel density or by each material's own
   proportions, times the overrides the table sets; then packed into one
   square atlas by ``xatlas`` -- provisioned into Blender's Python on first
   use (:meth:`CoreUtils.ensure_packages`), so both DCCs pack identically.
3. **Write** -- the original layout is copied to a transient UV map, the
   packed one written into the texture map, and :meth:`TextureTransfer.transfer`
   reads every source map through the original layout into the packed one,
   assigning the result. The transient map never outlives the call. Then the
   material slots no face wears any more are removed (an FBX ships every slot
   an object carries), and a source material that leaves with no user keeps a
   fake user while the combine is recorded -- a .blend drops an orphan on save,
   and :meth:`TextureCombine.restore` needs it back.
4. **Lightmaps** -- committed lightmaps are bound per object, not per material,
   so the combine never breaks them; but the baker atlases by material, so the
   combined objects' lightmaps are consolidated under the new material
   (:meth:`TextureCombine.consolidate_lightmaps`).
5. **Record** -- ``ptk.SceneRecords.TEXTURE_COMBINE`` on the file's private
   data node (never exported): the original UVs and face materials, the source
   maps read (with their file stamps), the settings and overrides. Nothing is
   destroyed: :meth:`TextureCombine.restore` puts the originals back exactly,
   :meth:`TextureCombine.recombine` re-reads revised source maps into the SAME
   layout, :meth:`TextureCombine.update` re-packs with new settings.

Why the combined layout goes INTO the texture UV map rather than beside it:
an exporter ships every UV map, and a second one is ``TEXCOORD_1`` -- the
lightmap channel. The original layout lives in the record instead, which is
lossless (Blender stores UVs as float32, and so does the record).

Objects and materials travel by NAME (``bpy`` references go stale across an
undo); a combine's meshes are keyed by their mesh datablock's name, with the
object names recorded beside it as the fallback (Maya keys by shape UUID).
"""

import base64
import os
import shutil
import time
import zlib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pythontk as ptk

from blendertk.core_utils._core_utils import CoreUtils
from blendertk.uv_utils.texture_transfer import TextureTransfer

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None


@dataclass
class CombineInputs:
    """What :meth:`TextureCombine.gather` read: the scene side of a combine.

    ``transforms`` are object names; ``shapes`` maps each unique mesh
    datablock's name to its read (``transforms``, ``uv_set``, ``uvs`` -- the
    welded UV points --, ``shell_ids``, ``materials`` / ``face_material``,
    ``uv_counts`` / ``uv_ids`` for the fingerprint); ``shells`` are the islands
    as :class:`pythontk.CombineShell` (key ``(mesh name, shell id)``), each with
    the faces it covers in ``shell_faces`` and its UV points in
    ``shell_points``; ``sources`` maps each source material name to
    ``{"maps", "resolution"}``; ``clusters`` caches
    :meth:`pythontk.UvCombine.clusters` (UV-only, so a re-plan reuses it).
    Mirror of mayatk's.
    """

    transforms: List[str] = field(default_factory=list)
    shapes: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    shells: List[Any] = field(default_factory=list)
    shell_faces: List[Any] = field(default_factory=list)
    shell_points: List[Any] = field(default_factory=list)
    sources: Dict[Optional[str], Dict[str, Any]] = field(default_factory=dict)
    clusters: Optional[List[List[int]]] = None


class _TextureCombineInternal:
    """Scene reads, the record codec and the lightmap re-atlas."""

    #: Where the original layout sits while the transfer reads it -- created
    #: and deleted inside one call, never saved.
    SOURCE_UV_SET = "_texture_combine_src"
    #: The record entry's schema (mayatk's).
    SCHEMA = 1
    #: Corners whose UVs differ by less than this weld into one UV point.
    WELD_EPSILON = 1e-6

    # ------------------------------------------------------------ meshes
    @staticmethod
    def _object(obj):
        """*obj* (a name or an object) as a mesh object, or ``None``."""
        import bpy

        found = bpy.data.objects.get(obj) if isinstance(obj, str) else obj
        if found is None or getattr(found, "type", None) != "MESH":
            return None
        return found

    @staticmethod
    def _users(mesh) -> List[str]:
        """Names of the objects that use *mesh* (its linked duplicates)."""
        import bpy

        return [o.name for o in bpy.data.objects if o.data == mesh]

    @classmethod
    def resolve_meshes(cls, objects=None) -> List[str]:
        """Mesh object names under *objects*, order kept.

        ``None`` reads the selection; empties / parents expand to the meshes
        beneath them. EVERY object using a mesh found is included, picked or
        not: linked duplicates share the UVs a combine moves, so each must
        also receive the material made for them. Mirror of mayatk's.
        """
        import bpy

        if objects is None:
            objects = CoreUtils.selected_objects()
        # One pass over the file for every mesh's users: the panel re-reads
        # the selection on every change.
        users: Dict[Any, List[str]] = {}
        for o in bpy.data.objects:
            if o.type == "MESH" and o.data is not None:
                users.setdefault(o.data.name_full, []).append(o.name)
        out: List[str] = []
        seen = set()
        for obj in ptk.make_iterable(objects):
            node = bpy.data.objects.get(obj) if isinstance(obj, str) else obj
            if node is None:
                continue
            for o in [node, *node.children_recursive]:
                if o.type != "MESH" or o.data is None:
                    continue
                for name in [o.name, *users.get(o.data.name_full, ())]:
                    if name not in seen:
                        seen.add(name)
                        out.append(name)
        return out

    @classmethod
    def _uuid(cls, transform) -> str:
        """The key a combine records a mesh under: its datablock's name."""
        return cls._object(transform).data.name

    @classmethod
    def _weld(cls, loop_vert, loop_uv):
        """``(point of each corner, point uvs)`` -- the corners at one vertex
        with one UV are one point (Maya's UV id).

        Points are numbered by the first corner that reads them, never by
        their coordinates, so a layout moved island by island welds into the
        same points again: what lets a record keyed by point restore it.
        """
        q = np.round(loop_uv / cls.WELD_EPSILON).astype(np.int64)
        key = np.column_stack([loop_vert.astype(np.int64), q])
        _, first, inverse = np.unique(
            key, axis=0, return_index=True, return_inverse=True
        )
        order = np.argsort(first, kind="stable")
        rank = np.empty_like(order)
        rank[order] = np.arange(len(order))
        return rank[inverse.reshape(-1)], loop_uv[first[order]]

    @staticmethod
    def _shell_ids(point_of_loop, counts, n_points) -> "np.ndarray":
        """The UV island of each point: points joined by the faces they share
        (Maya's UV shells -- a face connects every point it reads)."""
        starts = np.concatenate([[0], np.cumsum(counts)[:-1]]).astype(np.int64)
        face_of_loop = np.repeat(np.arange(len(counts)), counts)
        a = point_of_loop
        b = point_of_loop[starts[face_of_loop]]
        labels = np.arange(n_points)
        while True:
            low = np.minimum(labels[a], labels[b])
            new = labels.copy()
            np.minimum.at(new, a, low)
            np.minimum.at(new, b, low)
            while True:  # every point straight to its lowest-known peer
                jumped = new[new]
                if np.array_equal(jumped, new):
                    break
                new = jumped
            if np.array_equal(new, labels):
                break
            labels = new
        _, ids = np.unique(labels, return_inverse=True)
        return ids.reshape(-1).astype(np.int64)

    @staticmethod
    def _loop_uvs(mesh, name: str) -> "np.ndarray":
        """``(corners, 2)`` float64 UVs of the UV map *name*."""
        return TextureTransfer._uv_layer_vectors(mesh, name).astype(np.float64)

    @classmethod
    def _read_shape(cls, transform) -> Dict[str, Any]:
        """One mesh's texture layout, islands, face materials and areas."""
        obj = cls._object(transform)
        if obj is None:
            raise ValueError(f"{transform!r} is not a mesh object")
        if obj.mode == "EDIT":
            obj.update_from_editmode()  # the mesh data lags the edit-mesh
        mesh = obj.data
        if mesh.library is not None:
            raise ValueError(
                f"{obj.name}: its mesh is linked from a library and cannot be edited"
            )
        uv_set = TextureTransfer.auto_source_uv_set(obj)
        loop_uv = cls._loop_uvs(mesh, uv_set)
        if not len(loop_uv):
            raise ValueError(f"{obj.name}: no UVs in {uv_set!r}")
        counts = np.empty(len(mesh.polygons), dtype=np.int32)
        mesh.polygons.foreach_get("loop_total", counts)
        loop_vert = np.empty(len(mesh.loops), dtype=np.int32)
        mesh.loops.foreach_get("vertex_index", loop_vert)
        point_of_loop, uvs = cls._weld(loop_vert, loop_uv)
        shell_ids = cls._shell_ids(point_of_loop, counts, len(uvs))
        # Blender's own triangulation (what the transfer reads through).
        mesh.calc_loop_triangles()
        n_tri = len(mesh.loop_triangles)
        tri_loops = np.empty(n_tri * 3, dtype=np.int32)
        tri_face = np.empty(n_tri, dtype=np.int32)
        mesh.loop_triangles.foreach_get("loops", tri_loops)
        mesh.loop_triangles.foreach_get("polygon_index", tri_face)
        tri_loops = tri_loops.reshape(-1, 3)
        points = TextureTransfer._world_points(obj)
        p = points[loop_vert[tri_loops]]
        tri_area = 0.5 * np.linalg.norm(
            np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0]), axis=1
        )
        mats, face_mat = TextureTransfer.face_materials(obj)
        return {
            "uv_set": uv_set,
            "uvs": uvs,
            "shell_ids": shell_ids,
            "tri_uv": point_of_loop[tri_loops],
            "tri_face": tri_face.astype(np.int64),
            "tri_area": tri_area,
            "materials": [m.name for m in mats],
            "face_material": np.asarray(face_mat, dtype=np.int64),
            "faces": int(len(counts)),
            "uv_counts": counts.astype(np.int32),
            "uv_ids": point_of_loop.astype(np.int32),
        }

    @staticmethod
    def _fingerprint(read: Dict[str, Any]) -> List[int]:
        """``[faces, uv points, crc of the corner point ids]`` -- what must
        still hold for a recorded layout to describe the mesh."""
        crc = zlib.crc32(read["uv_counts"].tobytes())
        crc = zlib.crc32(read["uv_ids"].tobytes(), crc)
        return [int(read["faces"]), int(len(read["uvs"])), int(crc)]

    @staticmethod
    def _material_maps(material: Optional[str]) -> Dict[str, Any]:
        """``{"maps", "resolution"}`` of a source material (by name)."""
        import bpy

        mat = bpy.data.materials.get(material) if material else None
        if mat is None:
            return {"maps": {}, "resolution": 0}
        # Forward slashes: the record keys files by path (mayatk's spelling).
        maps = {
            ch: str(p).replace("\\", "/")
            for ch, p in (TextureTransfer.material_maps(mat) or {}).items()
            if p
        }
        sizes = [ptk.ImgUtils.get_image_size(p) for p in maps.values() if p]
        widths = [max(s) for s in sizes if s]
        return {"maps": dict(maps), "resolution": max(widths) if widths else 0}

    # ------------------------------------------------------------ record
    @staticmethod
    def _encode(array) -> str:
        return base64.b64encode(
            zlib.compress(np.ascontiguousarray(array).tobytes(), 6)
        ).decode("ascii")

    @staticmethod
    def _decode(text: str, dtype) -> "np.ndarray":
        return np.frombuffer(
            zlib.decompress(base64.b64decode(text)), dtype=dtype
        ).copy()

    @classmethod
    def _load(cls) -> Dict[str, Dict[str, Any]]:
        from blendertk.node_utils.data_nodes import DataNodes

        data = ptk.SceneRecords.TEXTURE_COMBINE.load(DataNodes, {})
        return {
            name: entry
            for name, entry in (data or {}).items()
            if isinstance(entry, dict) and int(entry.get("schema", 0)) <= cls.SCHEMA
        }

    @staticmethod
    def _save(data: Dict[str, Dict[str, Any]]) -> None:
        from blendertk.node_utils.data_nodes import DataNodes

        ptk.SceneRecords.TEXTURE_COMBINE.save(DataNodes, data)

    @staticmethod
    def _stamp(path: str) -> Optional[List[float]]:
        try:
            st = os.stat(path)
        except OSError:
            return None
        return [round(st.st_mtime, 3), int(st.st_size)]

    @classmethod
    def _resolve_entry_shape(cls, uuid: str, mesh: Dict[str, Any]) -> Optional[str]:
        """A recorded mesh's first live object: by mesh name, else by object name."""
        import bpy

        data = bpy.data.meshes.get(uuid)
        if data is not None:
            users = cls._users(data)
            if users:
                return users[0]
        for name in mesh.get("transforms") or []:
            if cls._object(name) is not None:
                return name
        return None

    # ------------------------------------------------------------ UV maps
    @staticmethod
    def _set_loop_uvs(layer, values) -> None:
        buf = np.ascontiguousarray(values, dtype=np.float32).reshape(-1)
        try:  # Blender 3.5+: the UV attribute
            layer.uv.foreach_set("vector", buf)
        except (AttributeError, TypeError):  # older: MeshUVLoop.uv
            layer.data.foreach_set("uv", buf)

    @classmethod
    def _write_layout(cls, transform, uv_set: str, points, uv_ids) -> None:
        """Write the UV map *uv_set* from per-point *points* (each corner its
        point's UV)."""
        mesh = cls._object(transform).data
        cls._set_loop_uvs(mesh.uv_layers[uv_set], np.asarray(points)[uv_ids])
        mesh.update()

    @classmethod
    def _drop_source_set(cls, transform) -> None:
        obj = cls._object(transform)
        if obj is None:
            return
        layer = obj.data.uv_layers.get(cls.SOURCE_UV_SET)
        if layer is not None:
            obj.data.uv_layers.remove(layer)

    @classmethod
    def _stage_source_set(cls, transform, points, uv_ids) -> None:
        """The transient source map, holding *points* (a leftover is replaced);
        the mesh's active and render maps are left as they were."""
        cls._drop_source_set(transform)
        mesh = cls._object(transform).data
        layers = mesh.uv_layers
        active = layers.active.name if layers.active else None
        render = next((uv.name for uv in layers if uv.active_render), None)
        if layers.new(name=cls.SOURCE_UV_SET, do_init=False) is None:
            raise ValueError(
                f"{mesh.name}: no room for the transient UV map (Blender holds "
                "eight) -- remove an unused one first"
            )
        layers = mesh.uv_layers  # re-read: adding a layer reallocates them
        cls._set_loop_uvs(layers[cls.SOURCE_UV_SET], np.asarray(points)[uv_ids])
        if active:
            layers.active = layers[active]
        if render:
            for uv in layers:
                uv.active_render = uv.name == render
        mesh.update()

    @staticmethod
    def _map_key(map_name: Any) -> str:
        """A lightmap's identity: its lower-case file name (how markers,
        claims and folder records key a map)."""
        return os.path.basename(str(map_name or "")).lower()

    # ------------------------------------------------------------ slots
    @classmethod
    def _worn(cls, transform) -> Optional[str]:
        """The material covering the most faces of *transform*, by name."""
        mats, per_face = TextureTransfer.face_materials(cls._object(transform))
        worn = per_face[per_face >= 0]
        if not len(worn):
            return None
        return mats[int(np.bincount(worn).argmax())].name

    @classmethod
    def _settle_slots(cls, transforms, sources=()) -> List[str]:
        """Remove the material slots no face of *transforms* wears.

        An FBX ships every slot an object carries, so a slot the combine (or
        a restore) emptied of faces would ride the deliverable as a material
        nothing shows. A *sources* material the removal leaves with no user
        gets a fake user -- a .blend drops orphans on save, and the combine's
        restore reads it back. Returns the names given one.
        """
        kept: List[str] = []
        done = set()
        for name in transforms:
            obj = cls._object(name)
            if obj is None or obj.data.name in done:
                continue
            mesh = obj.data
            done.add(mesh.name)
            index = np.empty(len(mesh.polygons), dtype=np.int32)
            mesh.polygons.foreach_get("material_index", index)
            used = set(np.unique(index).tolist())
            for i in reversed(range(len(obj.material_slots))):
                if i in used:
                    continue
                mat = obj.material_slots[i].material
                # pop remaps the face indices above i; walking down keeps the
                # lower ones -- the ones still to visit -- where they are.
                mesh.materials.pop(index=i)
                if (
                    mat is not None
                    and mat.name in sources
                    and mat.users == 0
                    and not mat.use_fake_user
                ):
                    mat.use_fake_user = True
                    kept.append(mat.name)
        return kept

    @staticmethod
    def _release(names) -> None:
        """Take the fake user :meth:`_settle_slots` gave *names* back."""
        import bpy

        for name in names or ():
            mat = bpy.data.materials.get(name)
            if mat is not None and mat.use_fake_user:
                mat.use_fake_user = False

    @staticmethod
    def _slot_of(obj, material) -> int:
        """*obj*'s slot holding *material*, a new data slot when none does."""
        for i, slot in enumerate(obj.material_slots):
            if slot.material == material:
                return i
        obj.data.materials.append(material)
        return len(obj.material_slots) - 1

    # ------------------------------------------------------------ faces
    @staticmethod
    def _face_ranges(transform: str, indices) -> List[str]:
        """Sorted face *indices* as compact ``<object>.f[a:b]`` strings -- the
        spelling a ``"faces"`` override row names its faces in (mayatk's
        component strings; Blender has none of its own)."""
        out: List[str] = []
        ids = sorted(int(i) for i in indices)
        if not ids:
            return out
        start = prev = ids[0]
        for idx in ids[1:]:
            if idx == prev + 1:
                prev = idx
                continue
            out.append(f"{transform}.f[{start}:{prev}]")
            start = prev = idx
        out.append(f"{transform}.f[{start}:{prev}]")
        return out

    @staticmethod
    def _face_component(component: str) -> Tuple[str, List[int]]:
        """``(object name, face ids)`` of ``<object>.f[3]`` / ``.f[0:9]``
        (an object name may itself hold a ``.``)."""
        node, sep, rest = str(component).rpartition(".f[")
        if not sep:
            return str(component), []
        body = rest.rstrip("]")
        try:
            if ":" in body:
                a, b = body.split(":", 1)
                return node, list(range(int(a), int(b) + 1))
            return node, [int(body)]
        except ValueError:
            return node, []

    # ------------------------------------------------------------ lightmaps
    @classmethod
    def _lightmap_crop(
        cls, info: Dict[str, Any], transform: str, staging
    ) -> Optional[Tuple[str, tuple]]:
        """*transform*'s cell cut out of its atlas, pixel-exact, as
        ``(path, region)`` -- the region in the object's own lightmap layout
        that the crop covers (what :meth:`LightmapBaker.pack_atlas` reads a
        partial map in). Mirror of mayatk's; read and written through bpy."""
        from blendertk.light_utils.lightmap_baker.lightmap_baker import (
            LightmapBaker,
        )
        from blendertk.uv_utils._uv_utils import UvUtils

        path = info.get("path")
        obj = cls._object(transform)
        if not path or not os.path.isfile(path) or obj is None:
            return None
        image = LightmapBaker._read_exr_top_down(path)  # row 0 at the top
        uv_set = info.get("uv_set") or UvUtils.find_lightmap_uv_set(obj)
        if not uv_set or obj.data.uv_layers.get(uv_set) is None:
            return None
        uv = cls._loop_uvs(obj.data, uv_set)
        if not len(uv):
            return None
        sx, sy, ox, oy = [float(v) for v in info.get("scaleOffset") or (1, 1, 0, 0)]
        h, w = image.shape[:2]
        pad = 2.0  # texels of gutter kept around the island
        u0, u1 = uv[:, 0].min() * sx + ox, uv[:, 0].max() * sx + ox
        v0, v1 = uv[:, 1].min() * sy + oy, uv[:, 1].max() * sy + oy
        col0 = max(0, int(np.floor(u0 * w - pad)))
        col1 = min(w, int(np.ceil(u1 * w + pad)))
        row0 = max(0, int(np.floor((1.0 - v1) * h - pad)))  # row 0 is V = 1
        row1 = min(h, int(np.ceil((1.0 - v0) * h + pad)))
        if col1 <= col0 or row1 <= row0:
            return None
        crop = np.ascontiguousarray(image[row0:row1, col0:col1])
        out = staging.path(extension=".exr")
        LightmapBaker._write_lightmap_exr(out, np.flipud(crop))  # bpy: bottom-up
        # The crop's window back in the object's layout frame.
        region = (
            (col0 / w - ox) / sx,
            ((1.0 - row1 / h) - oy) / sy,
            (col1 / w - ox) / sx,
            ((1.0 - row0 / h) - oy) / sy,
        )
        return out, region


class TextureCombine(ptk.LoggingMixin, _TextureCombineInternal):
    """Combine the selected meshes' materials into one atlas and one material.

    Every action is one undo step in the file (the written maps stay on disk)
    and recorded, so it can be restored or re-combined later. Mirror of
    :class:`mayatk.TextureCombine`.
    """

    SIZING = ptk.UvCombine.SIZING
    NORMALIZE = ptk.UvCombine.NORMALIZE
    #: Override row kinds, most specific last (:meth:`apply_rows`).
    ROW_KINDS = ("material", "mesh", "faces")
    #: The packer :class:`pythontk.UvCombine` packs with, as pip installs it.
    PACKAGES = {"xatlas": "xatlas"}

    def __init__(self, log_level="INFO"):
        super().__init__()
        self.logger.setLevel(log_level)

    # ------------------------------------------------------------ queries
    @classmethod
    def names(cls) -> List[str]:
        """The combines this file holds, oldest first."""
        data = cls._load()
        return sorted(data, key=lambda n: data[n].get("created", ""))

    @classmethod
    def entry(cls, name: str) -> Optional[Dict[str, Any]]:
        """A combine's record entry (settings, rows, meshes, sources), or ``None``."""
        return cls._load().get(name)

    @classmethod
    def members(cls, name: str) -> List[str]:
        """The live objects a combine moved (every linked duplicate), by name."""
        entry = cls.entry(name) or {}
        out: List[str] = []
        for uuid, mesh in (entry.get("meshes") or {}).items():
            first = cls._resolve_entry_shape(uuid, mesh)
            if first is None:
                continue
            for user in cls._users(cls._object(first).data):
                if user not in out:
                    out.append(user)
        return out

    @classmethod
    def owner(cls, objects) -> Optional[str]:
        """The combine any of *objects* belongs to, or ``None``."""
        names = set(cls.resolve_meshes(objects))
        uuids = {cls._uuid(t) for t in names}
        for name, entry in cls._load().items():
            meshes = entry.get("meshes") or {}
            if uuids & set(meshes):
                return name
            recorded = {t for m in meshes.values() for t in m.get("transforms") or []}
            if names & recorded:
                return name
        return None

    @classmethod
    def stale(cls, name: str) -> List[str]:
        """The source maps of *name* revised (or gone) since it was combined."""
        entry = cls.entry(name) or {}
        out = []
        for source in (entry.get("sources") or {}).values():
            for path, stamp in (source.get("stamps") or {}).items():
                if cls._stamp(path) != (list(stamp) if stamp else None):
                    out.append(path)
        return sorted(set(out))

    # ------------------------------------------------------------ gather + plan
    def gather(self, objects=None) -> CombineInputs:
        """Read the islands, materials and maps of *objects* (default: selection).

        Raises:
            ValueError: No meshes, or meshes whose texture UV maps are named
                differently (rename them to match -- the transfer reads one
                map name across the run).
        """
        if np is None:
            raise RuntimeError("numpy is required")
        transforms = self.resolve_meshes(objects)
        if not transforms:
            raise ValueError(
                "no meshes to combine -- select the meshes (or their parent)"
            )
        inputs = CombineInputs(transforms=transforms)
        for transform in transforms:
            uuid = self._uuid(transform)
            if uuid in inputs.shapes:
                inputs.shapes[uuid]["transforms"].append(transform)
                continue
            read = self._read_shape(transform)
            read["transforms"] = [transform]
            inputs.shapes[uuid] = read
        sets = {r["uv_set"] for r in inputs.shapes.values()}
        if len(sets) > 1:
            raise ValueError(
                "the meshes' texture UV maps are named differently "
                f"({', '.join(sorted(sets))}) -- rename them to one name first"
            )
        self._build_shells(inputs)
        for shell in inputs.shells:
            if shell.group not in inputs.sources:
                inputs.sources[shell.group] = self._material_maps(shell.group)
        return inputs

    def inputs_for(self, name: str) -> CombineInputs:
        """A stored combine's inputs AS THEY WERE before it ran.

        The live meshes' topology with the recorded original UVs and face
        materials laid back over it, and the recorded source maps -- what a
        panel re-plans an update against, and what :meth:`update` combines
        (islands are UV-connected sets, which a rigid repack never changes).
        """
        entry, live = self._recorded(name)
        if not live:
            raise ValueError(f"{name}: none of its meshes are in the file unchanged")
        inputs = CombineInputs()
        for uuid, (first, mesh, read) in live.items():
            read["transforms"] = self._users(self._object(first).data) or [first]
            read["uvs"] = (
                self._decode(mesh["uvs"], np.float32).reshape(-1, 2).astype(np.float64)
            )
            read["materials"] = list(mesh.get("materials") or [])
            read["face_material"] = self._decode(
                mesh["face_material"], np.int32
            ).astype(np.int64)
            inputs.shapes[uuid] = read
            inputs.transforms.extend(read["transforms"])
        self._build_shells(inputs)
        recorded = entry.get("sources") or {}
        for shell in inputs.shells:
            if shell.group in inputs.sources:
                continue
            src = recorded.get(shell.group)
            inputs.sources[shell.group] = (
                {
                    "maps": dict(src.get("maps") or {}),
                    "resolution": int(src.get("resolution") or 0),
                }
                if src
                else self._material_maps(shell.group)
            )
        return inputs

    @staticmethod
    def _build_shells(inputs: CombineInputs) -> None:
        """One :class:`pythontk.CombineShell` per UV island of every read
        (:meth:`pythontk.UvCombine.island_shells`)."""
        for uuid, read in inputs.shapes.items():
            shells, faces, points = ptk.UvCombine.island_shells(
                uuid,
                read["uvs"],
                read["shell_ids"],
                read["tri_uv"],
                read["tri_face"],
                read["tri_area"],
                read["materials"],
                read["face_material"],
            )
            inputs.shells.extend(shells)
            inputs.shell_faces.extend(faces)
            inputs.shell_points.extend(points)

    def apply_rows(
        self, inputs: CombineInputs, rows: Sequence[Dict[str, Any]] = ()
    ) -> None:
        """Set every island's scale from the override *rows* -- the most
        specific row wins: a ``"faces"`` row (the islands those faces lie in)
        over a ``"mesh"`` row (every island of that mesh, any linked duplicate
        named) over a ``"material"`` row (every island reading it).

        A row is ``{"kind": one of ROW_KINDS, "target": str | [str], "scale":
        float}``; faces are ``<object>.f[a:b]`` strings (:meth:`_face_ranges`).
        """
        by_material, by_shape, by_faces = {}, {}, {}
        for row in rows or ():
            kind, scale = row.get("kind"), float(row.get("scale", 1.0) or 1.0)
            targets = ptk.make_iterable(row.get("target"))
            if kind == "material":
                for t in targets:
                    by_material[t] = scale
            elif kind == "mesh":
                for t in self.resolve_meshes(list(targets)):
                    by_shape[self._uuid(t)] = scale
            elif kind == "faces":
                for comp in targets:
                    node, ids = self._face_component(comp)
                    if self._object(node) is None:
                        continue
                    picked = by_faces.setdefault(self._uuid(node), {})
                    for f in ids:
                        picked[f] = scale
        for shell, faces in zip(inputs.shells, inputs.shell_faces):
            uuid = shell.key[0]
            scale = by_material.get(shell.group, 1.0)
            scale = by_shape.get(uuid, scale)
            picked = by_faces.get(uuid)
            if picked:
                hits = [picked[f] for f in faces.tolist() if f in picked]
                if hits:
                    scale = max(hits)
            shell.scale = scale

    def plan(
        self,
        inputs: CombineInputs,
        *,
        sizing: str = "proportional",
        normalize: str = "resolution",
    ):
        """Estimate the atlas for *inputs* (nothing packed) -- a
        :class:`pythontk.CombinePlan`; cheap after the first call, which
        caches the islands' clusters on *inputs*."""
        if inputs.clusters is None:
            inputs.clusters = ptk.UvCombine.clusters(inputs.shells)
        return ptk.UvCombine.plan(
            inputs.shells,
            sizing=sizing,
            normalize=normalize,
            resolutions={g: s["resolution"] for g, s in inputs.sources.items()},
            clusters=inputs.clusters,
        )

    # ------------------------------------------------------------ actions
    @staticmethod
    def _node_path(transform) -> str:
        """``|``-joined parent chain of *transform*, ``.001`` counters dropped
        (Blender's collision suffix, not part of what an object is called) --
        the long name mayatk's naming reads."""
        import re

        import bpy

        node = (
            bpy.data.objects.get(transform) if isinstance(transform, str) else transform
        )
        chain = []
        while node is not None:
            chain.append(re.sub(r"\.\d+$", "", node.name))
            node = node.parent
        return "|" + "|".join(reversed(chain))

    def default_name(self, transforms: Sequence[str]) -> str:
        """What a blank name stands for: the meshes' common name."""
        name = ptk.StrUtils.common_name(
            [self._node_path(t) for t in transforms],
            strip=ptk.NamingConvention.all_affixes(),
        )
        return name or "combined"

    @CoreUtils._object_mode
    def combine(
        self,
        objects=None,
        *,
        name: Optional[str] = None,
        rows: Sequence[Dict[str, Any]] = (),
        sizing: str = "proportional",
        normalize: str = "resolution",
        map_size: int = 4096,
        padding: Optional[int] = None,
        rotate: bool = True,
        supersample: int = 2,
        texture_padding: int = -1,
        output_dir: Optional[str] = None,
        assign_prefix: str = "",
        assign_suffix: Optional[str] = None,
        lightmaps: bool = True,
        inputs: Optional[CombineInputs] = None,
    ) -> Dict[str, Any]:
        """Combine *objects*' materials into one atlas and one material.

        Parameters:
            objects: Meshes or their parents; ``None`` uses the selection.
            name: The combine's name -- its record key, its maps'
                (``<name>_<Channel>.png``) and its material's core. Blank:
                :meth:`default_name`.
            rows: Scale overrides (:meth:`apply_rows`).
            sizing / normalize: See :class:`pythontk.UvCombine`.
            map_size: The atlas size.
            padding: Gutter between islands in texels; default
                ``ptk.MathUtils.calculate_uv_padding(map_size)``.
            rotate: Let the packer turn islands where that packs tighter.
            supersample / texture_padding / output_dir / assign_*: As
                :meth:`TextureTransfer.transfer` (*texture_padding* is its
                ``padding``: the maps' gutter fill, ``-1`` fills every empty
                texel). mayatk's ``assign_shader_type`` has no twin: a Blender
                material is one node graph, with no shader type to rebuild on.
            lightmaps: Consolidate the combined objects' committed lightmaps
                (:meth:`consolidate_lightmaps`).
            inputs: A :meth:`gather` already made for *objects* (a panel that
                showed the plan passes it; its rows are re-applied).

        Returns:
            ``{"name", "material", "maps": {channel: path}, "meshes": n,
            "groups": {material: CombineGroup}, "utilization", "map_size",
            "lightmaps"}``.

        Raises:
            ValueError: Nothing to combine, a name another combine holds, or a
                mesh already in another combine (restore that one first).
            RuntimeError: The packer (``xatlas``) cannot be provisioned.
        """
        inputs = inputs or self.gather(objects)
        self.apply_rows(inputs, rows)
        name = (name or "").strip() or self.default_name(inputs.transforms)
        data = self._load()
        if name in data:
            raise ValueError(
                f"a combine named {name!r} exists -- update or restore it, or "
                "pick another name"
            )
        for other, entry in data.items():
            taken = set(entry.get("meshes") or {}) & set(inputs.shapes)
            if taken:
                raise ValueError(
                    f"{len(taken)} of these meshes are already in the combine "
                    f"{other!r} -- restore it first"
                )
        if not any(s["maps"] for s in inputs.sources.values()):
            raise ValueError("no source material carries a texture map to combine")

        plan = self.plan(inputs, sizing=sizing, normalize=normalize)
        # Recorded as asked: Auto (None) re-derives from the atlas size on an
        # update, where the resolved count would keep the old atlas's gutter.
        spacing = None if padding is None else int(padding)
        padding = (
            int(round(ptk.MathUtils.calculate_uv_padding(map_size)))
            if spacing is None
            else spacing
        )
        # Blender bundles numpy but not xatlas (the packer ptk.UvPack drives).
        CoreUtils.ensure_packages(self.PACKAGES)
        layout = ptk.UvCombine.pack(
            inputs.shells,
            plan,
            map_size=int(map_size),
            padding=padding,
            margin=ptk.MathUtils.uv_tile_margin(map_size),
            rotate=rotate,
        )
        settings = {
            "sizing": sizing,
            "normalize": normalize,
            "map_size": int(map_size),
            "padding": spacing,
            "rotate": bool(rotate),
            "supersample": int(supersample),
            "texture_padding": int(texture_padding),
            "output_dir": output_dir or "",
            "assign_prefix": assign_prefix or "",
            "assign_suffix": assign_suffix,
            "lightmaps": bool(lightmaps),
        }
        # Per mesh, the packed points: the original ones moved island by
        # island (a point no face references stays where it is).
        packed: Dict[str, Any] = {
            uuid: read["uvs"].copy() for uuid, read in inputs.shapes.items()
        }
        for shell, points, new in zip(inputs.shells, inputs.shell_points, layout.uvs):
            packed[shell.key[0]][points] = new

        with CoreUtils.undo_chunk(f"Texture Combine: {name}"):
            maps, material = self._write_and_transfer(
                inputs,
                packed,
                name=name,
                settings=settings,
                source_materials=None,
            )
            kept = self._settle_slots(
                inputs.transforms, {m for m in inputs.sources if m}
            )
            entry = self._entry(inputs, name, settings, rows, material, maps)
            entry["kept"] = kept
            entry["groups"] = {
                str(g): {
                    "share": round(stats.share, 5),
                    "scale": round(stats.scale, 6),
                    "shells": stats.shells,
                    "stacks": stats.stacks,
                }
                for g, stats in layout.plan.groups.items()
            }
            entry["utilization"] = round(layout.utilization, 4)
            data[name] = entry
            self._save(data)
            lm = (
                self.consolidate_lightmaps(inputs.transforms, name) if lightmaps else {}
            )
        return {
            "name": name,
            "material": material,
            "maps": maps,
            "meshes": len(inputs.transforms),
            "groups": layout.plan.groups,
            "utilization": layout.utilization,
            "map_size": int(map_size),
            "lightmaps": lm,
        }

    def _write_and_transfer(
        self,
        inputs: CombineInputs,
        packed: Dict[str, Any],
        *,
        name: str,
        settings: Dict[str, Any],
        source_materials: Optional[Dict[str, Tuple[List, Any]]],
        original: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Dict[str, str], Optional[str]]:
        """Stage the source layout, write the packed one, transfer, clean up.

        *original* (per mesh) is the layout the maps were painted for when it
        is NOT what the texture map holds now (a re-combine: the map already
        holds the packed layout); ``None`` -- the map holds it.
        """
        staged: List[str] = []
        moved: List[Tuple[str, Dict[str, Any]]] = []
        try:
            for uuid, read in inputs.shapes.items():
                first = read["transforms"][0]
                painted = original[uuid] if original is not None else read["uvs"]
                self._stage_source_set(first, painted, read["uv_ids"])
                staged.append(first)
                if not np.array_equal(packed[uuid], read["uvs"]):
                    self._write_layout(
                        first, read["uv_set"], packed[uuid], read["uv_ids"]
                    )
                    moved.append((first, read))
            uv_set = next(iter(inputs.shapes.values()))["uv_set"]
            extra = {"source_materials": source_materials} if source_materials else {}
            results = TextureTransfer(log_level=self.logger.level).transfer(
                inputs.transforms,
                None,
                source_uv_set=self.SOURCE_UV_SET,
                target_uv_set=uv_set,
                size=settings["map_size"],
                supersample=settings["supersample"],
                padding=settings["texture_padding"],
                output_dir=settings["output_dir"] or None,
                output_name=name,
                assign=True,
                assign_prefix=settings["assign_prefix"],
                assign_suffix=settings["assign_suffix"],
                assign_from="source",
                **extra,
            )
        except BaseException:
            # Put every moved layout back before the error leaves: a failed
            # transfer must not leave the file on a layout no map was made for.
            for first, read in moved:
                try:
                    self._write_layout(
                        first, read["uv_set"], read["uvs"], read["uv_ids"]
                    )
                except Exception as error:  # noqa: BLE001 - best effort, reported
                    self.logger.error(f"Could not restore {first}'s UVs: {error}")
            raise
        finally:
            for first in staged:
                self._drop_source_set(first)
        maps = next(iter(results.values()), {}) if results else {}
        return dict(maps), self._worn(inputs.transforms[0])

    def _entry(self, inputs, name, settings, rows, material, maps) -> Dict[str, Any]:
        meshes = {}
        for uuid, read in inputs.shapes.items():
            meshes[uuid] = {
                "transforms": list(read["transforms"]),
                "uv_set": read["uv_set"],
                "fingerprint": self._fingerprint(read),
                "uvs": self._encode(read["uvs"].astype(np.float32)),
                "materials": list(read["materials"]),
                "face_material": self._encode(read["face_material"].astype(np.int32)),
            }
        sources = {}
        for mat, src in inputs.sources.items():
            if not mat:
                continue
            sources[mat] = {
                "maps": dict(src["maps"]),
                "resolution": int(src["resolution"]),
                "stamps": {p: self._stamp(p) for p in src["maps"].values() if p},
            }
        return {
            "schema": self.SCHEMA,
            "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "material": material,
            "maps": dict(maps),
            "settings": dict(settings),
            "rows": [dict(r) for r in rows or ()],
            "meshes": meshes,
            "sources": sources,
        }

    # ------------------------------------------------------------ revisions
    def _recorded(self, name: str):
        """``(entry, live)`` -- *live* ``{mesh name: (first object, mesh,
        read)}`` for every recorded mesh that still matches its fingerprint."""
        entry = self.entry(name)
        if entry is None:
            raise ValueError(f"no combine named {name!r} in this file")
        live = {}
        for uuid, mesh in (entry.get("meshes") or {}).items():
            first = self._resolve_entry_shape(uuid, mesh)
            if first is None:
                self.logger.warning(f"{name}: a recorded mesh is gone; skipped.")
                continue
            read = self._read_shape(first)
            if self._fingerprint(read) != list(mesh.get("fingerprint") or []):
                self.logger.warning(
                    f"{name}: {first}'s topology changed since the combine; skipped."
                )
                continue
            live[uuid] = (first, mesh, read)
        return entry, live

    @CoreUtils._object_mode
    def recombine(self, name: str) -> Dict[str, Any]:
        """Re-read *name*'s source maps into its CURRENT layout -- a revision.

        The layout stays exactly where it is (no re-pack): each face is read
        through the material it wore and the layout its maps were painted for,
        both from the record. The combined material and maps are replaced in
        place, and the source stamps refreshed.
        """
        entry, live = self._recorded(name)
        if not live:
            raise ValueError(f"{name}: none of its meshes can be re-combined")
        inputs = CombineInputs()
        original, given = {}, {}
        for uuid, (first, mesh, read) in live.items():
            read["transforms"] = self._users(self._object(first).data) or [first]
            inputs.shapes[uuid] = read
            inputs.transforms.extend(read["transforms"])
            original[uuid] = (
                self._decode(mesh["uvs"], np.float32).reshape(-1, 2).astype(np.float64)
            )
            face_mat = self._decode(mesh["face_material"], np.int32)
            for xf in read["transforms"]:
                given[xf] = (list(mesh["materials"]), face_mat)
        settings = dict(entry.get("settings") or {})
        with CoreUtils.undo_chunk(f"Re-combine: {name}"):
            maps, material = self._write_and_transfer(
                inputs,
                {uuid: r["uvs"] for uuid, r in inputs.shapes.items()},
                name=name,
                settings=settings,
                source_materials=given,
                original=original,
            )
            data = self._load()
            stored = data.get(name, entry)
            stored["kept"] = sorted(
                set(stored.get("kept") or [])
                | set(
                    self._settle_slots(
                        inputs.transforms, set(stored.get("sources") or {})
                    )
                )
            )
            for src in (stored.get("sources") or {}).values():
                src["stamps"] = {
                    p: self._stamp(p) for p in (src.get("maps") or {}).values() if p
                }
            stored["maps"] = dict(maps)
            stored["material"] = material or stored.get("material")
            data[name] = stored
            self._save(data)
        return {
            "name": name,
            "material": material,
            "maps": maps,
            "meshes": len(inputs.transforms),
        }

    @CoreUtils._object_mode
    def restore(self, name: str, forget: bool = True) -> List[str]:
        """Put *name*'s meshes back: original UVs and materials, exactly.

        The combined material and maps are left in the file and on disk (the
        material in no slot), the consolidated lightmaps bound as they are
        (lighting does not depend on the material). With *forget* the record
        entry goes too.

        Returns:
            The objects restored (names).
        """
        import bpy

        entry, live = self._recorded(name)
        restored: List[str] = []
        with CoreUtils.undo_chunk(f"Restore Combine: {name}"):
            for uuid, (first, mesh, read) in live.items():
                original = (
                    self._decode(mesh["uvs"], np.float32)
                    .reshape(-1, 2)
                    .astype(np.float64)
                )
                if len(original) != len(read["uvs"]):
                    self.logger.warning(f"{first}: UV count changed; skipped.")
                    continue
                self._write_layout(first, read["uv_set"], original, read["uv_ids"])
                obj = self._object(first)
                me = obj.data
                face_mat = self._decode(mesh["face_material"], np.int32)
                index = np.empty(len(me.polygons), dtype=np.int32)
                me.polygons.foreach_get("material_index", index)
                for i, material in enumerate(mesh.get("materials") or []):
                    faces = np.flatnonzero(face_mat == i)
                    if not len(faces):
                        continue
                    mat = bpy.data.materials.get(material)
                    if mat is None:
                        self.logger.warning(
                            f"{material} is gone; {first} keeps the combined "
                            "material on its faces."
                        )
                        continue
                    index[faces] = self._slot_of(obj, mat)
                me.polygons.foreach_set("material_index", index)
                me.update()
                self._settle_slots([first])
                restored.extend(self._users(me) or [first])
            self._release(entry.get("kept"))
            if forget:
                data = self._load()
                data.pop(name, None)
                self._save(data)
        return restored

    def update(self, name: str, **changes) -> Dict[str, Any]:
        """Re-pack *name* with changed settings or rows, keeping its name.

        Restores the originals, then combines the same meshes again with the
        recorded settings overlaid by *changes* (any :meth:`combine` keyword).
        One undo step (mirror of mayatk): the nested chunks fold into it, so
        Ctrl+Z returns the combine as it was, not the restored originals.
        """
        entry = self.entry(name)
        if entry is None:
            raise ValueError(f"no combine named {name!r} in this file")
        settings = dict(entry.get("settings") or {})
        settings["rows"] = entry.get("rows") or []
        settings.update(changes)
        objects = settings.pop("objects", None)
        with CoreUtils.undo_chunk(f"Update Combine: {name}"):
            members = self.restore(name, forget=True)
            return self.combine(objects or members, name=name, **settings)

    @classmethod
    def forget(cls, name: str) -> bool:
        """Drop *name*'s record entry, leaving the file as it is.

        The source materials it kept alive for a restore (:meth:`_settle_slots`)
        lose that fake user: with no record nothing will read them back.
        """
        data = cls._load()
        entry = data.pop(name, None)
        if entry is None:
            return False
        cls._release(entry.get("kept"))
        cls._save(data)
        return True

    # ------------------------------------------------------------ lightmaps
    def consolidate_lightmaps(
        self, transforms: Sequence[str], name: str
    ) -> Dict[str, Any]:
        """Bring the combined objects' committed lightmaps under the new material.

        A lightmap is bound per object (map + atlas rect), so a combine never
        breaks one; but the baker atlases BY MATERIAL, and the combined objects
        now share one. So:

        * the objects own their one atlas outright -- it becomes
          ``<name>_Lightmap`` (a lossless copy, re-committed);
        * they read several atlases, or share one with other objects -- their
          cells are cut out pixel-exact and re-packed into one
          ``<name>_Lightmap`` atlas (:meth:`LightmapBaker.pack_atlas` with
          ``regions``), the maps nobody reads any more set aside.

        Either way the old map is set aside only by the re-bake guard
        (:meth:`LightmapRecords.superseding`): written by THIS file, inside
        its project, and read by nothing here -- never a file another file may
        still read.

        Objects with no lightmap are listed: the next bake gives them one, in
        this same atlas (it groups by the combined material). Mirror of
        mayatk's.

        Returns:
            ``{"how": None | "kept" | "renamed" | "repacked", "map", "objects",
            "unbaked"}``.
        """
        from blendertk.light_utils.lightmap_baker.lightmap_records import (
            LightmapRecords,
        )

        transforms = [str(t) for t in transforms]
        infos = {t: LightmapRecords.lightmap_info(t) for t in transforms}
        baked = {t: i for t, i in infos.items() if i}
        unbaked = [t for t in transforms if t not in baked]
        result: Dict[str, Any] = {
            "how": None,
            "map": None,
            "objects": sorted(baked),
            "unbaked": unbaked,
        }
        if not baked:
            return result
        stem = ptk.StrUtils.sanitize(name, preserve_case=True)
        maps = {self._map_key(i["map"]): i for i in baked.values()}
        claims = LightmapRecords.claims()
        mine = set(baked)
        if len(maps) == 1:
            key, info = next(iter(maps.items()))
            ext = os.path.splitext(info["map"])[1] or ".exr"
            target = f"{stem}_Lightmap{ext}"
            readers = set(claims.get(key, ()))
            if readers <= mine:
                result["map"] = target
                if self._map_key(target) == key:
                    result["how"] = "kept"
                    return result
                path = info.get("path")
                if not path or not os.path.isfile(path):
                    self.logger.warning(
                        f"{info['map']} is not on disk; lightmaps left as they are."
                    )
                    result["how"] = None
                    return result
                new_path = os.path.join(os.path.dirname(path), target)
                taken = set(claims.get(self._map_key(target), ())) - mine
                if os.path.exists(new_path) and taken:
                    new_path = ptk.FileUtils.unique_path(
                        os.path.dirname(path),
                        f"{stem}_Lightmap",
                        ext,
                        claims=claims,
                        owners=mine,
                    )
                    target = os.path.basename(new_path)
                    result["map"] = target
                # Copied and re-committed, never moved: another file (a Save
                # As copy, a file linking this one) may read the old name,
                # which this file's markers cannot see. The re-bake guard sets
                # it aside only when THIS file wrote it, inside its project,
                # and nothing in the file reads it any more.
                shutil.copy2(path, new_path)
                with LightmapRecords.superseding(list(baked)):
                    LightmapRecords.commit(
                        {t: new_path for t in baked},
                        scale_offsets={
                            t: i.get("scaleOffset") for t, i in baked.items()
                        },
                        intensity=float(info.get("intensity", 1.0)),
                        written=True,
                    )
                result["how"] = "renamed"
                return result
        return self._repack_lightmaps(baked, stem, result)

    def _repack_lightmaps(self, baked: Dict[str, Dict[str, Any]], stem: str, result):
        """Cut every object's cell out of its atlas and pack them into one."""
        from blendertk.light_utils.lightmap_baker.lightmap_baker import (
            LightmapBaker,
        )
        from blendertk.light_utils.lightmap_baker.lightmap_records import (
            LightmapRecords,
        )

        sizes = [
            ptk.ImgUtils.get_image_size(i["path"])
            for i in baked.values()
            if i.get("path")
        ]
        resolution = max((max(s) for s in sizes if s), default=1024)
        located = [i["path"] for i in baked.values() if i.get("path")]
        if not located:
            self.logger.warning("No lightmap is on disk; lightmaps left as they are.")
            return result
        out_dir = os.path.dirname(located[0])
        with ptk.TempArtifacts.scoped("texture_combine_lm") as staging:
            crops, regions = {}, {}
            for transform, info in baked.items():
                cut = self._lightmap_crop(info, transform, staging)
                if cut is None:
                    self.logger.warning(
                        f"{transform}: its lightmap could not be read; left as it is."
                    )
                    continue
                crops[transform], regions[transform] = cut
            if not crops:
                return result
            baker = LightmapBaker(resolution=int(resolution))
            with LightmapRecords.superseding(list(crops)):
                packed = baker.pack_atlas(crops, output_dir=out_dir, regions=regions)
                baker.commit_lightmap(
                    {o: p for o, (p, _) in packed.items()},
                    scale_offsets={o: so for o, (_, so) in packed.items()},
                )
        result["how"] = "repacked"
        result["map"] = sorted({os.path.basename(p) for p, _ in packed.values()})
        return result
