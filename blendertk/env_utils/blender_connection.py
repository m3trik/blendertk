# !/usr/bin/python
# coding=utf-8
"""Launch a FRESH headless Blender to run a script / code string and capture its output — the
Blender counterpart of mayatk's ``env_utils.maya_connection.MayaConnection``
(``btk.BlenderConnection`` ↔ ``mtk.MayaConnection``).

Blender has no persistent command-port protocol and its Python is embedded, so — unlike Maya's
port / standalone / interactive modes — the only model is a **fresh subprocess per run**
(``blender --background --factory-startup --python …``). Session safety is therefore *structural*:
every run is a brand-new process, so this **never attaches to a running Blender** (the package's
hard rule). This formalizes the ``test/Run-Tests.ps1`` harness as a reusable Python class.

The actual launch+wait+capture is delegated to :meth:`pythontk.AppLauncher.run` (no raw
subprocess); only Blender-executable *discovery* is Blender-specific. No ``bpy`` import — this is
the launcher, run from outside Blender (e.g. the workspace ``.venv`` or a parent process).
"""
import glob
import os
import platform
import shutil
import tempfile
from typing import List, Optional

import pythontk as ptk

#: stdout sentinel a suite prints (the ``test/Run-Tests.ps1`` convention).
RESULT_PASS = "===RESULT: PASS==="


class BlenderConnection:
    """Run scripts in fresh headless Blender instances (mirror of ``MayaConnection``'s role)."""

    _ENV_VARS = ("BLENDER_EXE", "BLENDER")  # explicit-exe overrides, checked first

    def __init__(self, blender_exe: Optional[str] = None, factory_startup: bool = True):
        self.blender_exe = blender_exe or self.find_blender()
        if not self.blender_exe or not os.path.isfile(self.blender_exe):
            raise FileNotFoundError(
                "Blender executable not found. Pass blender_exe= or set $BLENDER_EXE "
                f"(got {self.blender_exe!r})."
            )
        self.factory_startup = factory_startup

    # ------------------------------------------------------------------ discovery
    @classmethod
    def find_blender(cls) -> Optional[str]:
        """Locate a Blender executable: ``$BLENDER_EXE`` / ``$BLENDER`` → ``PATH`` → common install
        dirs (highest version wins). Returns the absolute path or ``None``."""
        for var in cls._ENV_VARS:
            p = os.environ.get(var)
            if p and os.path.isfile(p):
                return p
        which = shutil.which("blender")
        if which:
            return which

        system = platform.system().lower()
        candidates: List[str] = []
        if system == "windows":
            roots = {
                os.environ.get("ProgramFiles", r"C:\Program Files"),
                os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
            }
            for root in filter(None, roots):
                candidates += glob.glob(os.path.join(root, "Blender Foundation", "Blender *", "blender.exe"))
        elif system == "darwin":
            candidates += glob.glob("/Applications/Blender*.app/Contents/MacOS/Blender")
        else:  # linux / other unix
            candidates += glob.glob("/usr/share/blender*/blender")
            candidates += glob.glob("/opt/blender*/blender")
            candidates += glob.glob("/usr/local/blender*/blender")
        candidates = [c for c in candidates if os.path.isfile(c)]
        # sort so the highest "Blender <ver>" path is last (best-guess newest)
        return sorted(candidates)[-1] if candidates else None

    # ------------------------------------------------------------------ run
    def _base_args(self) -> List[str]:
        args = ["--background"]
        if self.factory_startup:
            args.append("--factory-startup")
        return args

    def run_script(
        self,
        script_path: str,
        script_args=None,
        *,
        blend_file: Optional[str] = None,
        extra_args=None,
        timeout: Optional[float] = 600,
        output_file: Optional[str] = None,
        env: Optional[dict] = None,
    ):
        """Run *script_path* in a fresh headless Blender; return the ``CompletedProcess``.

        Args:
            script_path: the ``.py`` to run via ``--python``.
            script_args: values passed to the script after ``--`` (read via ``sys.argv``).
            blend_file: a ``.blend`` to open first (default: the factory scene).
            extra_args: extra Blender CLI flags inserted before ``--python``.
            timeout: seconds before ``subprocess.TimeoutExpired`` (``None`` = no limit).
            output_file: redirect combined stdout+stderr to this file (then ``stdout`` is ``None``).
            env: environment mapping for the child (else inherits).
        """
        args: List[str] = []
        if blend_file:
            args.append(blend_file)  # positional: the .blend to open, before the flags
        args += self._base_args()
        if extra_args:
            args += list(extra_args)
        args += ["--python", script_path]
        if script_args:
            args += ["--"] + [str(a) for a in script_args]
        return ptk.AppLauncher.run(
            self.blender_exe, args=args, timeout=timeout, output_file=output_file, env=env
        )

    def run_code(self, code: str, **kwargs):
        """Run a Python *code* string in a fresh headless Blender (via a temp script).

        ``**kwargs`` forward to :meth:`run_script`. Returns the ``CompletedProcess``.
        """
        fd, path = tempfile.mkstemp(suffix=".py", prefix="btk_conn_")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(code)
            return self.run_script(path, **kwargs)
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    def run_result(self, script_path: Optional[str] = None, *, code: Optional[str] = None, **kwargs):
        """Run a script / code string that prints a ``===RESULT: PASS===`` sentinel and report
        pass/fail — the programmatic form of ``Run-Tests.ps1``.

        Pass exactly one of *script_path* or *code*. Returns ``(passed: bool, CompletedProcess)``
        where ``passed`` is True only when stdout contains :data:`RESULT_PASS` (so a crash, a
        timeout-truncated run, or a ``FAIL`` sentinel all read as not-passed). Requires captured
        output — don't combine with ``output_file``.
        """
        if (script_path is None) == (code is None):
            raise ValueError("Pass exactly one of script_path or code.")
        cp = (
            self.run_code(code, **kwargs)
            if code is not None
            else self.run_script(script_path, **kwargs)
        )
        return (RESULT_PASS in (cp.stdout or "")), cp
