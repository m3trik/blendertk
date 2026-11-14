#!/usr/bin/python
# coding=utf-8
"""
Main test runner for blendertk.

Runs every headless suite in a FRESH background Blender (session-safety rule:
never attach to a running instance) -- or, with ``--reuse``, in one background
Blender this tooling launched and keeps between runs -- and aggregates the
``===RESULT===`` sentinels. Each suite reports its checks as ``OK <name>`` / ``FAIL <name>``
lines, so the totals here -- and the README badge -- count *individual checks*,
the same unit every sibling package reports. See
m3trik/docs/TEST_BADGE_STANDARD.md.

Run with:
    python run_tests.py                       # every suite
    python run_tests.py bevel xform_utils     # named suites only (no badge)
    python run_tests.py --list                # list available suites
    python run_tests.py --blender <path>      # explicit Blender executable
    python run_tests.py --no-badge            # skip the README badge update
    python run_tests.py --suite-timeout 900   # per-suite kill timer (default 600s)
    python run_tests.py --reuse               # Blender suites in a kept background Blender

``--reuse`` runs each Blender suite as a call in one background Blender
(pythontk's ``SessionOperationHost``, one per checkout): the first run starts
it, later runs reuse it, and it ends by itself ``KEEP_SECONDS`` after its last
suite. Each suite starts from the factory scene, with blendertk and the test
modules imported afresh; a change to other code it loaded (pythontk, uitk)
restarts it, and so does a suite that hangs past its timer. Suites share one
process, so one that leaks state (a persistent handler) can affect a later
one: a fresh run is the reference. Qt-only suites run in the ``.venv`` as always.
"""

import argparse
import re
import shutil
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, List, Optional, Tuple

TEST_DIR = Path(__file__).resolve().parent
PACKAGE_ROOT = TEST_DIR.parent

# ``===RESULT: PASS=== (12/13)`` / ``=== (skipped)`` / bare ``===RESULT: PASS===``.
SENTINEL = re.compile(
    r"^===RESULT: (PASS|FAIL)===(?:\s*\((?:(\d+)/(\d+)|(skipped))\))?"
)
CHECK = re.compile(r"^(OK|FAIL)\b")


def find_venv_python() -> Optional[str]:
    """The workspace ``.venv`` interpreter, host for the Qt-only suites.

    Headless Blender ships no Qt binding, so every suite that needs one
    (``test_blender_ui_handler``, the panel/slots harnesses, …) self-skips under
    the Blender harness and is documented as a ``.venv`` target. Nothing ran
    them automatically, which is how a stale ``mock.patch`` target survived the
    ``menu_harvest`` flat-function -> class migration: it failed only when the
    suite was finally executed by hand. Resolving the venv here lets
    :meth:`BlenderTestRunner.run` re-run those suites instead of reporting SKIP
    and moving on.
    """
    workspace = PACKAGE_ROOT.parent
    for rel in (("Scripts", "python.exe"), ("bin", "python")):
        candidate = workspace.joinpath(".venv", *rel)
        if candidate.exists():
            return str(candidate)
    return None


def find_blender(explicit: Optional[str] = None) -> Optional[str]:
    """Locate Blender: arg > BLENDERTK_BLENDER env > newest install > PATH.

    Installs are found by the product's own globs
    (``BlenderConnection._INSTALL_GLOBS``: Windows, macOS and the Linux layouts).
    """
    if explicit:
        if Path(explicit).exists():
            return explicit
        print(f"[WARNING] --blender path not found: {explicit}")
        return None
    # This checkout's blendertk, installed or not -- otherwise the ImportError
    # below would skip BLENDERTK_BLENDER and the install scan along with it.
    if str(PACKAGE_ROOT) not in sys.path:
        sys.path.insert(0, str(PACKAGE_ROOT))
    try:
        from pythontk import AppLauncher
        from blendertk.env_utils.blender_connection import BlenderConnection

        found = AppLauncher.resolve_app_path(
            env_vars=("BLENDERTK_BLENDER",),
            scan_globs=BlenderConnection._INSTALL_GLOBS,
        )
        if found:
            return found
    except ImportError:
        pass
    return shutil.which("blender")


class BlenderTestRunner:
    """Runs each blendertk suite in its own fresh headless Blender."""

    #: Run in each child before its suite: the temp root, named outright. Pure
    #: stdlib, since it runs before a suite puts pythontk on the path.
    _PIN_TEMP = (
        "import os, tempfile; os.makedirs({0!r}, exist_ok=True); "
        "tempfile.tempdir = {0!r}; "
        # TempArtifacts.root() reads PYTHONTK_TEMP_ROOT before tempfile: an
        # inherited one names the OUTER run's root, not this pin.
        "os.environ['PYTHONTK_TEMP_ROOT'] = {0!r}; "
        # And CoreUtils.ensure_packages installs into the run, never into the
        # user's Blender profile (scripts/addons/modules): what the profile
        # already holds stays importable, read-only.
        "os.environ['BLENDERTK_PACKAGES_DIR'] = os.path.join({0!r}, 'blender_modules')"
    )
    #: Then uitk's settings sandbox (:attr:`sandbox_settings`): the presets root
    #: and every ``QSettings(org, app)`` store redirected into the temp root, so
    #: a suite that builds a panel never reaches the developer's live ones
    #: (``HKCU\\Software\\uitk``). uitk comes from :attr:`_SANDBOX_ROOTS`, the
    #: checkouts every suite puts first on its own path, and the path is left as
    #: it was found. Best-effort and never raising: a child without a Qt binding
    #: has no QSettings to redirect, and one without uitk warns and runs its suite.
    _SANDBOX_SETTINGS = "\n".join(
        (
            "import os, sys",
            "_path = sys.path[:]",
            "sys.path[:0] = {0!r}",
            "try:",
            "    from uitk.testing import TestSandbox",
            "    TestSandbox.presets()",
            "    os.environ.setdefault('QT_API', 'pyside6')  # the suites' own binding",
            "    try:",
            "        TestSandbox.qsettings()",
            "    except ImportError:  # no Qt binding: no QSettings store to reach",
            "        pass",
            "except Exception as error:",
            "    print('[WARN] uitk settings sandbox unavailable (%r): this suite '",
            "          'reaches the live settings stores.' % (error,))",
            "finally:",
            "    sys.path[:] = _path",
        )
    )
    #: Where :attr:`_SANDBOX_SETTINGS` imports uitk (and its pythontk) from.
    _SANDBOX_ROOTS = [str(PACKAGE_ROOT.parent / name) for name in ("pythontk", "uitk")]
    #: Then, in a plain interpreter, the suite as ``python <suite>`` would run it.
    _RUN_AS_MAIN = "\n".join(
        (
            "import os, runpy, sys",
            "sys.argv[:] = sys.argv[1:]",
            "if sys.path[0] == '':  # -c puts the cwd first, a script its own dir",
            "    sys.path[0] = os.path.dirname(os.path.abspath(sys.argv[0]))",
            "runpy.run_path(sys.argv[0], run_name='__main__')",
        )
    )

    #: Seconds a kept Blender (--reuse) waits for the next suite before it ends
    #: by itself.
    KEEP_SECONDS = 900
    #: Seconds a kept Blender has to stop a suite past its timer before it is killed.
    KEEP_GRACE = 5
    #: Kept Blenders a run may look past while other runs on this checkout
    #: hold theirs (each run claims its own; none left: fresh suites).
    SPARE_SESSIONS = 4

    def __init__(
        self,
        blender: str,
        test_dir: Path = TEST_DIR,
        suite_timeout: int = 600,
        reuse: bool = False,
    ):
        self.blender = blender
        self.test_dir = test_dir
        self.suite_timeout = suite_timeout
        #: Run the Blender suites in a kept Blender (``--reuse``).
        self.reuse = reuse
        #: That Blender's session (:meth:`_keep_session`), once the run starts.
        self._session: Any = None
        self.venv_python = find_venv_python()
        #: The run's throwaway temp root (:meth:`_isolate_temp`); None = real TEMP.
        self.temp_root: Optional[str] = None
        #: Each child redirects uitk's settings stores first (:attr:`_SANDBOX_SETTINGS`).
        self.sandbox_settings = True

    def discover(self, patterns: Optional[List[str]] = None) -> List[Path]:
        """Suites are ``test_*.py`` + the smoke test (it emits the same
        sentinel). Utility scripts in this dir (e.g. dump_runtime_surface.py)
        don't emit one and stay excluded.
        """
        suites = sorted(
            f
            for f in self.test_dir.glob("*.py")
            if f.name.startswith("test_")
            or f.name == "blender_smoke_test.py"
        )
        if patterns:
            # A bare stem matches too, so `--list` output runs as printed
            # (`blender_smoke_test` carries no `test_`).
            wanted = set(patterns) | {f"test_{p}" for p in patterns}
            suites = [s for s in suites if s.stem in wanted or s.name in patterns]
        return suites

    @staticmethod
    def _isolate_temp() -> Optional[str]:
        """Route this run's temp into one throwaway root; returns it.

        Nothing here fails -- that is the problem this fixes. A suite whose
        cleanup globs a real-temp pattern deletes whatever else matches, and one
        did: ``test_scene_import``'s orchestration check globbed
        ``maya_to_btk_cache_*`` in the real temp dir and removed the user's
        cached production USD conversion, which costs a multi-minute headless
        Maya run to earn back. Cached conversions are exactly what lives there.

        Activated in the RUNNER rather than in each suite's bootstrap, which is
        both smaller and safer than it sounds: every suite here IS a child (a
        fresh headless Blender, or the ``.venv`` interpreter), and each is
        handed the root -- in ``TMPDIR``/``TEMP``/``TMP``, which
        :meth:`TestSandbox.temp` sets for tools that read them, and pinned
        outright by :meth:`_child_command`, since a Python child that derives
        its temp dir from those can fall past them to the real one. The root is
        released at THIS process's exit, so a bridge artifact handed to a
        detached app still outlives the call that made it -- the case
        ``test_bridges`` asserts -- where per-suite activation would have torn
        it down with the suite.

        Best-effort by design: an unimportable pythontk is already a failing
        run, and it must fail on its own suites rather than here.
        """
        try:
            from pythontk.core_utils.test_sandbox import TestSandbox

            root = TestSandbox.temp()
        except Exception as error:  # noqa: BLE001
            print(f"[WARN] temp isolation unavailable ({error}); suites use real TEMP.")
            return None
        # Announced explicitly so a suite can ASSERT it inherited the redirect
        # rather than assume it. The failure this guards is silent in both
        # directions: a child that did not inherit writes to the real temp and
        # nothing complains, which is exactly how the cached conversion went.
        os.environ["BLENDERTK_TEST_TEMP_ROOT"] = root
        print(f"Temp:    {root}")
        return root

    def _child_command(self, suite: Path, python: Optional[str] = None) -> List[str]:
        """The command line for one suite's child.

        With a temp root, the child is told it before the suite's first line
        (:attr:`_PIN_TEMP`) rather than left to derive it from the inherited
        ``TMPDIR``/``TEMP``/``TMP``. Deriving is how the root was lost: a fresh
        interpreter's ``tempfile`` probes each variable with a throwaway write
        and, on any failure but ``FileExistsError``, moves past all three --
        they name the same root -- to ``~\\AppData\\Local\\Temp``, silently. A
        root that had gone, an ``OSError`` on three probes or a refused create
        each did it (measured, Blender 5.1 and the venv); one full run under a
        concurrent unitytk run did it for real (2026-09-26). A pinned temp dir
        is never probed. If the pin itself fails, the child stops before the
        suite -- Blender through ``--python-exit-code``, a plain interpreter
        through the uncaught error -- so an unpinned suite never runs.

        Then uitk's settings stores (:attr:`_SANDBOX_SETTINGS`), with or
        without a root: a suite kept off them only when it loaded uitk's
        conftest left every other one free to write the developer's live
        QSettings and presets, from a headless Blender too (it imports the
        PySide6 tentacle provisions). That step never stops a child.

        Parameters:
            suite: The suite file to run.
            python: Run it under this interpreter instead of a headless Blender.

        Returns:
            The argument list for ``subprocess.run``.
        """
        pin = self._PIN_TEMP.format(self.temp_root) if self.temp_root else None
        settings = (
            self._SANDBOX_SETTINGS.format(self._SANDBOX_ROOTS)
            if self.sandbox_settings
            else None
        )
        prelude = "\n".join(code for code in (pin, settings) if code)
        if python:
            if not prelude:
                return [python, str(suite)]
            return [python, "-c", f"{prelude}\n{self._RUN_AS_MAIN}", str(suite)]
        cmd = [self.blender, "--background", "--factory-startup"]
        if pin:
            cmd += ["--python-exit-code", "1"]
        if prelude:
            cmd += ["--python-expr", prelude]
        return cmd + ["--python", str(suite)]

    def run_suite(
        self, suite: Path, python: Optional[str] = None
    ) -> Tuple[bool, int, int, bool]:
        """Run one suite and parse its sentinel.

        Parameters:
            suite: The suite file to run.
            python: When given, run the suite directly under this interpreter
                (the Qt-only ``.venv`` targets) instead of in a fresh headless
                Blender. Blender stays the default -- the session-safety rule
                governs the ``bpy`` suites, and these carry no ``bpy`` import.

        Returns:
            Tuple of (passed, ok_checks, failed_checks, skipped).
        """
        if self._session is not None and not python:
            stdout, stderr, timed_out = self._run_kept(suite)
        else:
            stdout, stderr, timed_out = self._run_child(suite, python)
        if timed_out:
            # A hung suite must cost a failure, not stall the whole run: with
            # no timeout, one wedged Blender blocked every suite behind it.
            self._print_detail(
                f"TIMEOUT {suite.name}: no result after {self.suite_timeout}s "
                "(process killed)"
            )
            return False, 0, 1, False
        return self._verdict(suite, stdout.splitlines(), stderr)

    def _run_child(
        self, suite: Path, python: Optional[str] = None
    ) -> Tuple[str, str, bool]:
        """One suite in a fresh child: what it printed to stdout and stderr,
        and whether it ran past its timer (it was killed)."""
        # UTF-8 on both ends of the pipe: a child Python picks its stdout codec
        # from the locale when stdout is a pipe (cp1252 here), so a suite that
        # prints one glyph outside it died with UnicodeEncodeError and read as
        # a failure -- the naming tool's progress bar did exactly that.
        # Unbuffered: a suite's ===RESULT=== is its last line, and a crash in
        # teardown at exit (Qt's, offscreen) dropped a block-buffered one -- a
        # passing suite then read as "died before reporting" (2026-10-23).
        env = dict(
            os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1", PYTHONUNBUFFERED="1"
        )
        # Headless unless a GUI is the point (CODE_STANDARD section 9): a Qt
        # suite -- the .venv leg, or a uitk panel inside a background Blender --
        # must not put a window on the desktop. An explicit value is honored.
        env.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            proc = subprocess.run(
                self._child_command(suite, python),
                # A closed stdin: a child that inherits the launching console
                # reads as interactive (``sys.stdin.isatty()``), and the exporter's
                # console consent then waits on a ``[y/N]`` nobody will type --
                # test_smart_bake's failed-check export sat there until the kill
                # timer charged it a failure (2026-09-04). Nothing a suite does may
                # wait on a human.
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.suite_timeout,
                env=env,
            )
        except subprocess.TimeoutExpired:
            return "", "", True
        return proc.stdout or "", proc.stderr or "", False

    def _keep_session(self, tag: str = "") -> Any:
        """The kept background Blender (--reuse): one per checkout and Blender,
        keyed by its root and the executable, so two worktrees never share one,
        nor a run naming another Blender (--blender); *tag* sets one apart (a
        test's). Made before the run isolates its temp, which would put the
        session's folder in the run's throwaway root."""
        import hashlib

        import pythontk as ptk

        # This checkout's blendertk, installed or not: the kept Blender is
        # handed the roots this process imports it from.
        if str(PACKAGE_ROOT) not in sys.path:
            sys.path.insert(0, str(PACKAGE_ROOT))
        checkout = hashlib.sha1(
            os.path.normcase(f"{PACKAGE_ROOT.parent}|{os.path.abspath(self.blender)}").encode("utf-8")
        ).hexdigest()[:10]
        env = dict(
            os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1", PYTHONUNBUFFERED="1"
        )
        env.setdefault("QT_QPA_PLATFORM", "offscreen")
        batch = ptk.BatchOperationHost(
            self.blender,
            scene_files="blendertk:BlenderSceneFiles",
            launch_args=lambda script: ["--background", "--factory-startup", "--python", script],
            packages=("blendertk",),
            env=env,
            timeout=self.suite_timeout,
            grace=self.KEEP_GRACE,
        )
        for index in range(1 + self.SPARE_SESSIONS):
            session = ptk.SessionOperationHost(
                batch,
                "-".join(p for p in ("tests", checkout, tag, str(index or "")) if p),
                "blender",
                keep=self.KEEP_SECONDS,
                reload=[str(PACKAGE_ROOT)],
            )
            if session.claim():  # no other run on this checkout is using it
                return session
        return None  # every one is another run's: this one runs fresh

    def _run_kept(self, suite: Path) -> Tuple[str, str, bool]:
        """One suite as a call in the kept Blender: what it printed to stdout
        and stderr, and whether it ran past its timer (the session then
        stopped that Blender, and the next suite starts a fresh one).

        Pinned to the kept Blender's own temp root, not the run's: that one
        goes with the run, and the Blender outlives it.
        """
        import tempfile

        import pythontk as ptk

        root = str(self._session.folder / self._session.TEMP_FOLDER)
        pin = (
            self._PIN_TEMP.format(root)
            + f"; os.environ['BLENDERTK_TEST_TEMP_ROOT'] = {root!r}"
        )
        settings = (
            self._SANDBOX_SETTINGS.format(self._SANDBOX_ROOTS)
            if self.sandbox_settings
            else ""
        )
        prelude = "\n".join(code for code in (pin, settings) if code)
        out_dir = tempfile.mkdtemp(prefix="kept_suite_", dir=self.temp_root or None)
        out, err = os.path.join(out_dir, "stdout.txt"), os.path.join(out_dir, "stderr.txt")
        code = (
            "import importlib.util as _util\n"
            f"_spec = _util.spec_from_file_location('_blendertk_run_tests', {str(Path(__file__).resolve())!r})\n"
            "_harness = _util.module_from_spec(_spec)\n"
            "_spec.loader.exec_module(_harness)\n"
            f"_harness.BlenderTestRunner.run_kept_suite({str(suite)!r}, {out!r}, {err!r}, {prelude!r})\n"
        )
        entry = ptk.Operation(
            "blender.python.exec", "pythontk:PythonConsole.run", effect="code", host="blender"
        ).describe()
        outcome = self._session.run(
            entry, ptk.OpRequest(op=entry["id"], args={"code": code}, dry_run=False)
        )
        try:
            texts = []
            for path in (out, err):
                try:
                    with open(path, encoding="utf-8", errors="replace") as handle:
                        texts.append(handle.read())
                except OSError:
                    texts.append("")
        finally:
            shutil.rmtree(out_dir, ignore_errors=True)
        stdout, stderr = texts
        if "timed out after" in (outcome.error or ""):
            return stdout, stderr, True
        if not outcome.ok:  # the Blender's own failure: the suite's stderr tail says it
            stderr = f"{stderr}\n{outcome.error or outcome.status}".strip()
        return stdout, stderr, False

    @staticmethod
    def run_kept_suite(suite: str, out: str, err: str, prelude: str = "") -> int:
        """In a kept Blender (``--reuse``): run *suite* as a fresh ``blender
        --background --factory-startup --python <suite>`` would -- from the
        factory scene, after the run's *prelude*, as ``__main__`` -- what it
        prints going to *out* and *err*.

        Returns:
            Its exit code: what it passed to ``sys.exit``, 1 when it raised.
        """
        import contextlib
        import runpy
        import traceback

        import bpy

        bpy.ops.wm.read_factory_settings()
        code = 0
        argv, path = sys.argv[:], sys.path[:]
        with open(out, "w", encoding="utf-8") as stdout, open(
            err, "w", encoding="utf-8"
        ) as stderr, contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            sys.argv[:] = [argv[0], "--background", "--factory-startup", "--python", suite]
            try:
                if prelude:
                    exec(compile(prelude, "<prelude>", "exec"), {"__name__": "__prelude__"})
                runpy.run_path(suite, run_name="__main__")
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else int(exc.code is not None)
            except Exception:
                traceback.print_exc()
                code = 1
            finally:
                sys.argv[:], sys.path[:] = argv, path
        return code

    def _verdict(self, suite: Path, lines: List[str], stderr: str) -> Tuple[bool, int, int, bool]:
        """A suite's sentinel and checks, read from what it printed."""
        verdicts = [m for m in map(SENTINEL.match, lines) if m]
        passed = len(verdicts) == 1 and verdicts[0].group(1) == "PASS"
        skipped = bool(verdicts) and verdicts[0].group(4) == "skipped"

        if verdicts and verdicts[0].group(2):
            # The suite reported its own tally -- authoritative. It knows things
            # line-counting can't, e.g. a multi-line traceback is ONE check.
            ok = int(verdicts[0].group(2))
            failed = int(verdicts[0].group(3)) - ok
        else:
            checks = [m.group(1) for m in map(CHECK.match, lines) if m]
            ok = checks.count("OK")
            failed = checks.count("FAIL")
        # A suite that died before reporting (import error, hard crash) has no
        # checks to count -- charge it one failure so it can't read as green.
        if not passed and failed == 0:
            failed = 1

        if not passed:
            for ln in lines:
                if ln.startswith("FAIL"):
                    self._print_detail(ln)
            # The suite's own FAIL lines say WHAT failed; a traceback printed to
            # stderr -- by the suite's own `except: traceback.print_exc()`, or by
            # Blender -- says WHY. That tail used to be shown only when the suite
            # produced NO verdict at all, i.e. never for an ordinary failure, so
            # the one piece of output that explained the failure was captured and
            # then thrown away. Same defect the pythontk/tentacle runners had at
            # their TeeStream, in this harness's shape.
            tail = (stderr or "").strip().splitlines()[-5:]
            if tail:
                # Labelled: Blender writes its own warnings here too, and an
                # unlabelled tail reads as more failing checks.
                self._print_detail("-- stderr tail --")
                for ln in tail:
                    self._print_detail(ln)
        return passed, ok, failed, skipped

    @staticmethod
    def _print_detail(line: str) -> None:
        """Print a suite's failure detail without letting the console kill the run.

        A detail string carries whatever the failing check chose to report, and
        that can hold characters the console encoding has no mapping for (a log
        box's ``╔═║``, a mangled ``\\ufffd`` from the child's own replacement).
        On a cp1252 terminal ``print`` then raises UnicodeEncodeError from
        inside the runner's reporting path, so a single failing check aborts the
        whole run with a traceback instead of reporting the failure and moving
        on to the remaining suites.
        """
        text = f"     {line}"
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        try:
            print(text)
        except UnicodeEncodeError:
            print(text.encode(encoding, "replace").decode(encoding, "replace"))

    def run(self, patterns: Optional[List[str]] = None) -> dict:
        """Run the selected suites and return aggregate counts."""
        if self.reuse:
            self._session = self._keep_session()  # before the temp isolation below
            print(
                f"Reuse:   Blender suites run in a kept background Blender; it ends "
                f"{self.KEEP_SECONDS}s after its last suite"
                if self._session is not None
                else "Reuse:   every kept Blender of this checkout is another run's; "
                "suites run fresh"
            )
        self.temp_root = self._isolate_temp()
        suites = self.discover(patterns)
        if not suites:
            print("No suites matched.")
            return {
                "suites": 0,
                "passed": 0,
                "failed": 0,
                "failed_suites": [],
                "skipped_suites": [],
                "silent_suites": [],
            }

        start = time.time()
        totals = {"passed": 0, "failed": 0}
        failed_suites, skipped_suites, silent_suites = [], [], []

        for suite in suites:
            ok, checks_ok, checks_failed, skipped = self.run_suite(suite)
            via = ""
            if skipped and self.venv_python:
                # A Qt-only suite skipped for want of a binding Blender doesn't
                # ship. Run it where it CAN run rather than leaving it unrun --
                # totals are accumulated after this, so the venv result is the
                # one that counts (a suite that skips in both stays a skip).
                ok, checks_ok, checks_failed, skipped = self.run_suite(
                    suite, python=self.venv_python
                )
                via = " [venv]"
            totals["passed"] += checks_ok
            totals["failed"] += checks_failed
            if skipped:
                skipped_suites.append(suite.name)
                print(f"SKIP {suite.name}")
                continue
            status = "PASS" if ok else "FAIL"
            print(
                f"{status} {suite.name}{via} ({checks_ok}/{checks_ok + checks_failed})"
            )
            if not ok:
                failed_suites.append(suite.name)
            elif checks_ok + checks_failed == 0:
                # Passed but reported no checks -- a silent hole in the totals,
                # not a green suite. Name it rather than let it blend in.
                silent_suites.append(suite.name)

        elapsed = time.time() - start
        print("=" * 70)
        print(
            f"Total: {totals['passed'] + totals['failed']} checks, "
            f"{totals['passed']} passed, {totals['failed']} failed "
            f"across {len(suites)} suites ({elapsed:.1f}s)"
        )
        if skipped_suites:
            print(f"SKIPPED ({len(skipped_suites)}): {', '.join(skipped_suites)}")
        if silent_suites:
            print(
                f"[WARNING] passed but reported no checks ({len(silent_suites)}): "
                f"{', '.join(silent_suites)} - they contribute nothing to the "
                f"totals; make them emit an (ok/attempted) sentinel."
            )
        if failed_suites:
            print(f"FAILED suites: {', '.join(failed_suites)}")

        return {
            "suites": len(suites),
            "passed": totals["passed"],
            "failed": totals["failed"],
            "failed_suites": failed_suites,
            "skipped_suites": skipped_suites,
            "silent_suites": silent_suites,
        }

    def update_readme_badge(self, passed: int, failed: int) -> bool:
        """Stamp the README badge via the ecosystem SSoT (``ptk.StatusBadge``)."""
        from pythontk.core_utils.status_badge import StatusBadge

        readme_path = PACKAGE_ROOT / "docs" / "README.md"
        if not StatusBadge.update_test_badge(
            readme_path, passed, failed, test_dir=self.test_dir
        ):
            print(
                "[WARNING] README badge not updated (missing or "
                f"unwritable): {readme_path}"
            )
            return False

        print(f"README badge updated: {StatusBadge.test_status(passed, failed)[0]}")
        return True


def main() -> int:
    """Main entry point. Returns a process exit code (0 = success)."""
    parser = argparse.ArgumentParser(description="Run the blendertk test suite")
    parser.add_argument("suites", nargs="*", help="Specific suites to run")
    parser.add_argument("--blender", help="Path to the Blender executable")
    parser.add_argument("--list", action="store_true", help="List available suites")
    parser.add_argument(
        "--no-badge", action="store_true", help="Skip the README badge update"
    )
    parser.add_argument(
        "--reuse",
        action="store_true",
        help="Run the Blender suites in a background Blender this tooling keeps between runs",
    )
    parser.add_argument(
        "--suite-timeout",
        type=int,
        default=600,
        help="Seconds before a hung suite is killed and charged a failure",
    )
    args = parser.parse_args()

    blender = find_blender(args.blender)
    if not blender:
        print("[ERROR] Blender not found (pass --blender or set BLENDERTK_BLENDER)")
        return 1

    runner = BlenderTestRunner(
        blender, suite_timeout=args.suite_timeout, reuse=args.reuse
    )

    if args.list:
        for suite in runner.discover():
            print(f"  {suite.stem}")
        return 0

    # A name no suite answers to means the run would not cover what was asked
    # for -- it used to print "No suites matched." and exit 0, a typo read as a pass.
    unknown = [name for name in args.suites if not runner.discover([name])]
    if unknown:
        print(f"[ERROR] No such suite(s): {', '.join(unknown)} (see --list)")
        return 2

    print(f"Blender: {blender}\n")
    result = runner.run(args.suites or None)

    # A scoped run must not clobber the badge with a partial count -- it would
    # read as a coverage regression. Nor may a run that discovered nothing.
    if not args.no_badge and not args.suites and result["suites"]:
        runner.update_readme_badge(result["passed"], result["failed"])

    return 1 if result["failed_suites"] else 0


if __name__ == "__main__":
    sys.exit(main())
