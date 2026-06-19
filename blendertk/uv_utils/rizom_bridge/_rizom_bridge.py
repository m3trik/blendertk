# !/usr/bin/python
# coding=utf-8
"""RizomUV bridge engine — export the selection and open it in a fresh RizomUV session.

Focused Blender mirror of mayatk's ``RizomUVBridge`` *send-to-RizomUV* path (the one-way send): it
exports the current selection to FBX, writes a small RizomUV Lua load-script, and launches RizomUV
detached with ``-cfi <script>``. The Maya round-trip (re-import the UVs back into the scene) is
intentionally **not** mirrored — Blender has strong native UV tooling, so the bridge is for
interactive RizomUV work, not an automated round-trip.

Co-located with its panel (``rizom_bridge_slots.RizomBridgeSlots`` + ``rizom_bridge.ui``) rather
than living in the generic ``UvUtils`` namespace. Qt-only imports stay in the slots. RizomUV is
Windows-only.
"""
import os
import tempfile
import time

import pythontk as ptk

import blendertk as btk


# Candidate RizomUV executable names (AppLauncher.find_app), newest install wins on the dir scan.
_RIZOM_APP_NAMES = ("Rizomuv_VS", "rizomuv", "RizomUV")
# Install-dir fallback (the Rizom installer doesn't register the exe with the Windows App-Paths
# key). Newest ``Rizom Lab\<version>`` folder wins. Shared scan via ``AppLauncher.resolve_app_path``.
_RIZOM_SCAN_GLOBS = (
    r"{program_files}\Rizom Lab\*\Rizomuv_VS.exe",
    r"{program_files}\Rizom Lab\*\rizomuv_RS.exe",
    r"{program_files}\Rizom Lab\*\rizomuv.exe",
)


class RizomUVBridge(ptk.LoggingMixin):
    """Engine: discover the RizomUV exe, export the selection, launch RizomUV with a load-script.

    Named to mirror mayatk's ``RizomUVBridge`` (``btk.RizomUVBridge`` ↔ ``mtk.RizomUVBridge``)."""

    def __init__(self, rizom_path=None):
        self._rizom_path = rizom_path

    @property
    def rizom_path(self):
        """Resolved RizomUV executable path (cached), or None. Discovery runs through the shared
        :meth:`pythontk.AppLauncher.resolve_app_path`: ``AppLauncher.find_app`` for each
        candidate name, then a scan of ``Program Files\\Rizom Lab`` (the Rizom installer doesn't
        register the exe with the Windows App-Paths key, so PATH lookup alone misses it; newest
        ``Rizom Lab\\<version>`` folder wins)."""
        if self._rizom_path:
            return self._rizom_path
        found = ptk.AppLauncher.resolve_app_path(
            app_names=_RIZOM_APP_NAMES,
            scan_globs=_RIZOM_SCAN_GLOBS,
        )
        if found:
            self._rizom_path = found
        return found

    @rizom_path.setter
    def rizom_path(self, value):
        self._rizom_path = value

    @staticmethod
    def _lua_bool(value):
        return "true" if value else "false"

    def _texture_loads(self, objects):
        """RizomUV ``ZomLoadTexture`` calls for the selection's textures (each ``pcall``-wrapped so
        an older RizomUV that lacks the command fails soft — the mesh still loads). '' if none."""
        try:
            paths = btk.get_texture_paths(objects=objects, absolute=True)
        except Exception as error:
            self.logger.warning(f"Texture collection failed: {error}")
            return ""
        existing = [p for p in paths if p and os.path.isfile(p)]
        if not existing:
            return ""
        self.logger.info(f"Binding {len(existing)} texture(s) in RizomUV.")
        return "\n".join(
            'pcall(function() ZomLoadTexture({{File={{Path="{0}"}}}}) end)'.format(
                p.replace("\\", "/")
            )
            for p in existing
        )

    def build_send_script(
        self, fbx_path, objects=None, load_uvs=True, import_groups=True,
        load_uvw_props=True, load_textures=True,
    ):
        """Render the RizomUV Lua load-script (``ZomLoad`` + optional ``ZomLoadTexture`` block).

        Mirrors mayatk's ``send_wrapper.lua`` (no ``ZomSave``/``ZomQuit`` — RizomUV stays open)."""
        load = (
            'ZomLoad({{File={{Path="{path}", ImportGroups={groups}, '
            'XYZUVW={uvs}, UVWProps={props}}}}})'
        ).format(
            path=str(fbx_path).replace("\\", "/"),
            groups=self._lua_bool(import_groups),
            uvs=self._lua_bool(load_uvs),
            props=self._lua_bool(load_uvw_props),
        )
        textures = self._texture_loads(objects) if load_textures else ""
        return f"{load}\n\n{textures}\n" if textures else f"{load}\n"

    def send(self, objects, load_uvs=True, import_groups=True, load_uvw_props=True,
             load_textures=True):
        """Export ``objects`` to FBX and open them in a fresh RizomUV session (one-way).

        Per-send unique FBX + Lua paths so a second send doesn't clobber a still-open earlier
        session (RizomUV's ``-cfi`` watches the script). Returns the written Lua script path."""
        if not objects:
            raise ValueError("No objects specified for sending.")
        exe = self.rizom_path
        if not exe:
            raise RuntimeError(
                "RizomUV executable not found. Install RizomUV (Rizom Lab) or set rizom_path."
            )
        tag = f"{time.time_ns():x}"
        fbx_path = os.path.join(tempfile.gettempdir(), f"riz_send_{tag}.fbx")
        btk.export_selection_fbx(filepath=fbx_path, objects=objects)

        script = self.build_send_script(
            fbx_path, objects=objects, load_uvs=load_uvs, import_groups=import_groups,
            load_uvw_props=load_uvw_props, load_textures=load_textures,
        )
        script_path = os.path.join(tempfile.gettempdir(), f"riz_send_{tag}.lua")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(script)

        proc = ptk.AppLauncher.launch(exe, args=["-cfi", script_path], detached=True)
        if proc is None:
            raise RuntimeError(f"Failed to launch RizomUV: {exe}")
        self.logger.info(f"Sent {len(objects)} object(s) to RizomUV (interactive session).")
        return script_path
