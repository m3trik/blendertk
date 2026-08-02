# !/usr/bin/python
# coding=utf-8
"""Per-object **outliner text colour** for Blender — the true analogue of Maya's
``outlinerColor`` / ``useOutlinerColor``, which blendertk otherwise has no counterpart for.

Blender exposes no per-object outliner colour through Python: ``bpy.types.Object`` has no
``color_tag``/``outliner_color``, and ``SpaceOutliner`` publishes no tree, row, or expansion
state (verified live on 5.1.2 — its whole RNA surface is filters and display toggles). The only
row colouring Blender ships is ``Collection.color_tag``, which tints a *collection* row, not an
object's text.

So the colour is painted. A ``POST_PIXEL`` draw handler on ``SpaceOutliner`` (which Blender does
accept) walks the outliner's internal ``TreeElement`` tree through the ``SpaceOutliner`` struct
pointer, masks each tinted object's label with the theme background, and repaints it in the
object's colour at Blender's own layout position.

**This reads Blender's internal C structs**, which carry no API stability guarantee
(``TreeElement`` is runtime state, not versioned DNA). Everything here is therefore built to
fail *closed*:

* Offsets are **validated against the live process before use** (:meth:`_calibrate`) — the walk
  must resolve real ``bpy.data`` datablock names, or the feature disables itself. They are never
  trusted because a table says so.
* Every dereference goes through :func:`_readable`, an OS memory-protection query, so a wrong
  offset yields ``None`` instead of a segfault in the user's session.
* Any failure anywhere disables the overlay and leaves the outliner exactly as Blender drew it.
  A disabled overlay costs nothing and loses no data — the colours live on the objects.

The colour itself is ordinary, fully-supported data: a ``btk_outliner_color`` custom property on
the object, so it saves with the .blend, survives reload, and is readable without any of the
above. The overlay is only how it becomes *visible*.

Platform: the memory guard is implemented for Windows (``VirtualQuery``); elsewhere the overlay
stays off and the stored colours are simply not drawn (see ``is_supported``).
"""

import ctypes
import sys
from typing import Dict, List, Optional, Sequence, Tuple

import pythontk as ptk

Color = Tuple[float, float, float]

#: Custom property holding an object's outliner colour (saves with the .blend).
COLOR_PROP = "btk_outliner_color"


# ---------------------------------------------------------------------------
# guarded memory access
# ---------------------------------------------------------------------------
class _MemGuard:
    """Reject any address that is not committed, readable process memory.

    A bad dereference from Python is an instant hard crash — no exception to catch — so every
    read below is gated on this. Results are cached per memory region (a ``VirtualQuery`` is a
    syscall; the tree walk performs thousands of reads per redraw and they land in a handful of
    heap regions)."""

    _PAGE_READABLE = (0x02, 0x04, 0x08, 0x20, 0x40, 0x80)  # R, RW, WC, XR, XRW, XWC
    _MEM_COMMIT = 0x1000
    _PAGE_GUARD = 0x100

    def __init__(self):
        self._ranges: List[Tuple[int, int]] = []  # sorted (start, end) of known-readable
        self._ok = sys.platform == "win32"
        if self._ok:
            class _MBI(ctypes.Structure):
                _fields_ = [
                    ("BaseAddress", ctypes.c_void_p),
                    ("AllocationBase", ctypes.c_void_p),
                    ("AllocationProtect", ctypes.c_ulong),
                    ("PartitionId", ctypes.c_ushort),
                    ("RegionSize", ctypes.c_size_t),
                    ("State", ctypes.c_ulong),
                    ("Protect", ctypes.c_ulong),
                    ("Type", ctypes.c_ulong),
                ]

            self._mbi_type = _MBI
            self._query = ctypes.windll.kernel32.VirtualQuery

    @property
    def supported(self) -> bool:
        return self._ok

    def invalidate(self) -> None:
        """Drop the region cache (memory can be freed/reprotected between redraws)."""
        self._ranges.clear()

    def readable(self, addr: int, size: int = 8) -> bool:
        if not self._ok or not addr or addr < 0x10000:
            return False
        end = addr + size
        for start, stop in self._ranges:
            if start <= addr and end <= stop:
                return True
        mbi = self._mbi_type()
        if not self._query(ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)):
            return False
        if mbi.State != self._MEM_COMMIT or (mbi.Protect & self._PAGE_GUARD):
            return False
        if (mbi.Protect & 0xFF) not in self._PAGE_READABLE:
            return False
        base = ctypes.cast(mbi.BaseAddress, ctypes.c_void_p).value or 0
        stop = base + mbi.RegionSize
        if len(self._ranges) < 512:  # bounded cache
            self._ranges.append((base, stop))
        return end <= stop


_GUARD = _MemGuard()


def _qword(addr: int) -> Optional[int]:
    return ctypes.c_uint64.from_address(addr).value if _GUARD.readable(addr, 8) else None


def _int32(addr: int) -> Optional[int]:
    return ctypes.c_int32.from_address(addr).value if _GUARD.readable(addr, 4) else None


def _cstr(addr: int, limit: int = 66) -> Optional[str]:
    """Read a NUL-terminated printable-ASCII string, or None if it isn't one."""
    if not _GUARD.readable(addr, 8):
        return None
    out = bytearray()
    for i in range(limit):
        if not _GUARD.readable(addr + i, 1):
            return None
        b = ctypes.c_ubyte.from_address(addr + i).value
        if b == 0:
            return out.decode("ascii", "replace") if out else None
        if not (0x20 <= b < 0x7F):
            return None
        out.append(b)
    return None


class _OutlinerTintInternal(ptk.LoggingMixin):
    """Struct-layout calibration, tree walking, and the draw handler."""

    #: Struct offsets, x86-64. Confirmed live on Blender 5.1.2 by planting known object names
    #: and matching the resolved IDs; :meth:`_calibrate` re-proves them in the running process
    #: before the overlay is ever allowed to draw, so a layout shift disables rather than crashes.
    _LAYOUT = {
        "tree_head": 0xC0,  # SpaceOutliner.tree (ListBase.first)
        "next": 0x00,  # TreeElement.next
        "subtree": 0x20,  # TreeElement.subtree (ListBase.first)
        "xs": 0x30,  # TreeElement.xs  (draw-time, View2D space)
        "ys": 0x34,  # TreeElement.ys
        "store": 0x38,  # TreeElement.store_elem
        "store_id": 0x08,  # TreeStoreElem.id
        "id_name": 0x28,  # ID.name[66] — 2-char type code + name
    }
    #: Blender's own row text placement (``outliner_draw.c::outliner_draw_tree_element``):
    #: open/close column + icon column, then a small baseline lift.
    _TEXT_DX_UNITS = 2.0  # × UI_UNIT_X
    _TEXT_DX_PAD = 4.0  # × ufac
    _TEXT_DY_PAD = 5.0  # × ufac

    _handle = None  # draw-handler capsule
    _state = "unknown"  # unknown | ok | unsupported | <failure reason>
    _disarming = False  # a deferred disable is already queued
    _walk_budget = 20000  # hard cap on elements visited per redraw
    _walk_max_depth = 200  # tree nesting cap (recursion guard; real trees are <<50)

    # ── calibration ────────────────────────────────────────────────────────
    @classmethod
    def _calibrate(cls, space_ptr: int) -> bool:
        """Prove the offsets against the live process: the walk must resolve at least one
        element whose ID name matches a real datablock. Returns True when the layout holds."""
        import bpy

        _GUARD.invalidate()
        head = _qword(space_ptr + cls._LAYOUT["tree_head"])
        if not head:
            cls._state = "no tree pointer at the expected offset"
            return False
        rows = cls._walk(head)
        if not rows:
            cls._state = "tree walk produced no elements"
            return False
        known = set()
        for coll in (bpy.data.objects, bpy.data.collections, bpy.data.meshes):
            known.update(o.name for o in coll)
        hits = sum(1 for r in rows if r.id_name and r.id_name[2:] in known)
        if not hits:
            cls._state = f"walk resolved no known datablocks ({len(rows)} elements)"
            return False
        cls.logger.debug(f"outliner layout verified: {hits}/{len(rows)} elements resolved")
        cls._state = "ok"
        return True

    # ── tree walk ──────────────────────────────────────────────────────────
    @classmethod
    def _walk(cls, head: int) -> List["_Row"]:
        """Every TreeElement reachable from ``head``, in draw order."""
        out: List[_Row] = []
        budget = [cls._walk_budget]
        cls._walk_chain(head, out, budget, set(), 0)
        return out

    @classmethod
    def _walk_chain(cls, elem: int, out: list, budget: list, seen: set, depth: int) -> None:
        if depth > cls._walk_max_depth:
            return  # never let a deep (or corrupt) chain reach Python's recursion limit
        L = cls._LAYOUT
        while elem and budget[0] > 0 and elem not in seen:
            seen.add(elem)
            budget[0] -= 1
            if not _GUARD.readable(elem, 0x60):
                return
            id_name = None
            store = _qword(elem + L["store"])
            if store and _GUARD.readable(store, 0x10):
                idp = _qword(store + L["store_id"])
                if idp:
                    id_name = _cstr(idp + L["id_name"])
            out.append(
                _Row(
                    id_name=id_name,
                    xs=_int32(elem + L["xs"]),
                    ys=_int32(elem + L["ys"]),
                )
            )
            sub = _qword(elem + L["subtree"])
            if sub and sub != elem:
                cls._walk_chain(sub, out, budget, seen, depth + 1)
            elem = _qword(elem + L["next"]) or 0


class _Row:
    """One outliner tree element: its datablock name and draw-time row position."""

    __slots__ = ("id_name", "xs", "ys")

    def __init__(self, id_name, xs, ys):
        self.id_name = id_name
        self.xs = xs
        self.ys = ys

    @property
    def object_name(self) -> Optional[str]:
        """The object this row draws, or None when the row is not an Object."""
        return self.id_name[2:] if self.id_name and self.id_name.startswith("OB") else None


class OutlinerTint(_OutlinerTintInternal):
    """Per-object outliner text colour (Maya ``outlinerColor`` analogue).

    ``set_color`` / ``get_color`` / ``clear`` store plain custom-property data; :meth:`enable`
    turns on the overlay that makes it visible. Applying a colour enables the overlay for you."""

    # ── stored colour (plain, fully-supported data) ────────────────────────
    @staticmethod
    def set_color(objects: Sequence, color: Color) -> int:
        """Stamp ``color`` as each object's outliner colour; returns how many were stamped."""
        n = 0
        for obj in objects or ():
            if obj is None:
                continue
            obj[COLOR_PROP] = [float(c) for c in color[:3]]
            n += 1
        if n:
            OutlinerTint.enable()
        return n

    @staticmethod
    def get_color(obj) -> Optional[Color]:
        """The object's stored outliner colour, or None."""
        try:
            v = obj.get(COLOR_PROP)
        except (AttributeError, TypeError):
            return None
        return tuple(float(c) for c in v)[:3] if v is not None else None

    @staticmethod
    def clear(objects: Sequence) -> int:
        """Remove the outliner colour from each object; returns how many were cleared."""
        n = 0
        for obj in objects or ():
            if obj is None:
                continue
            try:
                if COLOR_PROP in obj:
                    del obj[COLOR_PROP]
                    n += 1
            except (AttributeError, TypeError):
                continue
        return n

    @classmethod
    def tinted_objects(cls) -> List:
        """Every object in the file carrying an outliner colour."""
        import bpy

        return [o for o in bpy.data.objects if COLOR_PROP in o]

    # ── overlay lifecycle ──────────────────────────────────────────────────
    @classmethod
    def is_supported(cls) -> bool:
        """Whether the overlay can run at all on this platform (memory guard available)."""
        return _GUARD.supported

    @classmethod
    def status(cls) -> str:
        """``"ok"``, ``"unsupported"``, ``"unknown"``, or the reason the overlay stood down."""
        return cls._state

    @classmethod
    def is_enabled(cls) -> bool:
        return cls._handle is not None

    @classmethod
    def enable(cls) -> bool:
        """Register the outliner overlay (idempotent). False when unavailable — the stored
        colours are still intact, they simply aren't drawn."""
        import bpy

        if cls._handle is not None:
            return True
        if not _GUARD.supported:
            cls._state = "unsupported"
            cls.logger.debug(f"outliner overlay unavailable on {sys.platform}")
            return False
        try:
            cls._handle = bpy.types.SpaceOutliner.draw_handler_add(
                cls._draw, (), "WINDOW", "POST_PIXEL"
            )
        except Exception as e:  # draw handlers refused (headless, API change)
            cls._state = f"draw handler refused: {e!r}"
            cls.logger.debug(cls._state)
            return False
        cls._tag_outliners()
        return True

    @classmethod
    def disable(cls) -> None:
        """Remove the overlay; the stored colours are untouched."""
        import bpy

        cls._disarming = False
        if cls._handle is not None:
            try:
                bpy.types.SpaceOutliner.draw_handler_remove(cls._handle, "WINDOW")
            except Exception:
                pass
            cls._handle = None
            cls._tag_outliners()

    @classmethod
    def _stand_down(cls, reason: str) -> None:
        """Disable the overlay from *inside* the draw callback — on the next tick.

        Removing a draw handler while Blender is iterating its handler list is not something to
        do mid-callback, so the removal is deferred to a timer. Guarded by ``_disarming`` because
        the outliner may redraw several times before that timer runs."""
        import bpy

        cls._state = reason
        cls.logger.debug(f"outliner overlay stood down: {reason}")
        if cls._disarming:
            return
        cls._disarming = True

        def _remove():
            cls.disable()
            return None

        try:
            bpy.app.timers.register(_remove, first_interval=0.0)
        except Exception:  # no timer available — fall back to an immediate removal
            cls.disable()

    @staticmethod
    def _tag_outliners() -> None:
        from blendertk.core_utils._core_utils import CoreUtils

        CoreUtils.tag_redraw("OUTLINER")

    # ── the overlay ────────────────────────────────────────────────────────
    @classmethod
    def _draw(cls) -> None:
        """POST_PIXEL callback: mask each tinted object's label and repaint it in its colour.

        Wrapped whole in a bare except and self-disabling: this runs inside Blender's draw loop,
        where a raised exception would spam the console every frame."""
        try:
            cls._draw_impl()
        except Exception as e:
            cls._stand_down(f"draw failed: {e!r}")

    @classmethod
    def _draw_impl(cls) -> None:
        import bpy
        import blf
        import gpu
        from gpu_extras.batch import batch_for_shader

        colors: Dict[str, Color] = {
            o.name: tuple(o[COLOR_PROP])[:3] for o in bpy.data.objects if COLOR_PROP in o
        }
        if not colors:
            return  # nothing tinted — never touch the outliner
        space = getattr(bpy.context, "space_data", None)
        region = getattr(bpy.context, "region", None)
        if space is None or region is None:
            return

        space_ptr = space.as_pointer()
        if cls._state != "ok" and not cls._calibrate(space_ptr):
            cls._stand_down(cls._state)
            return

        _GUARD.invalidate()  # regions can be freed/reprotected between redraws
        head = _qword(space_ptr + cls._LAYOUT["tree_head"])
        if not head:
            return
        rows = cls._walk(head)

        prefs = bpy.context.preferences
        ui = prefs.system.ui_scale
        dx = cls._TEXT_DX_UNITS * (20.0 * ui) + cls._TEXT_DX_PAD * ui
        dy = cls._TEXT_DY_PAD * ui
        # Mask colours must match the row background Blender actually drew, or the mask punches
        # a visible patch. The row is highlighted only when the object is SELECTED (``active``
        # is just the brighter variant of that) — an active-but-unselected row has the plain
        # back, so keying the mask on "is active" alone paints a box where there is no
        # highlight. (The alternate-row stripe is 1.5% white; below perception, ignored.)
        try:
            theme = prefs.themes[0].outliner
            back = tuple(theme.space.back)[:3]
            back_sel = tuple(theme.selected_highlight)[:3]
            back_active = tuple(theme.active)[:3]
        except Exception:
            back = back_sel = back_active = (0.156, 0.156, 0.156)
        try:
            points = prefs.ui_styles[0].widget.points  # honour the user's UI font size
        except Exception:
            points = 11.0

        active_obj = getattr(bpy.context.view_layer.objects, "active", None)
        active_name = getattr(active_obj, "name", None)

        shader = gpu.shader.from_builtin("UNIFORM_COLOR")
        font = 0
        blf.size(font, points * ui)
        view_to_region = region.view2d.view_to_region
        objects = bpy.data.objects
        gpu.state.blend_set("ALPHA")
        try:
            for row in rows:
                name = row.object_name
                if name is None or row.xs is None or row.ys is None:
                    continue
                color = colors.get(name)
                if color is None:
                    continue
                obj = objects.get(name)
                try:
                    selected = obj is not None and obj.select_get()
                except RuntimeError:  # not in this view layer
                    selected = False
                if not selected:
                    mask = back
                else:
                    mask = back_active if name == active_name else back_sel
                rx, ry = view_to_region(float(row.xs), float(row.ys), clip=False)
                tx, ty = rx + dx, ry + dy
                w, h = blf.dimensions(font, name)
                x0, y0 = tx - 2.0, ty - 4.0 * ui
                x1, y1 = x0 + w + 6.0, y0 + h + 8.0 * ui
                batch = batch_for_shader(
                    shader, "TRI_FAN", {"pos": ((x0, y0), (x1, y0), (x1, y1), (x0, y1))}
                )
                shader.bind()
                shader.uniform_float("color", (*mask, 1.0))
                batch.draw(shader)
                blf.color(font, *color, 1.0)
                blf.position(font, tx, ty, 0)
                blf.draw(font, name)
        finally:
            gpu.state.blend_set("NONE")
