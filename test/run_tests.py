#!/usr/bin/python
# coding=utf-8
"""
Main test runner for blendertk.

Runs every headless suite in a FRESH background Blender (session-safety rule:
never attach to a running instance) and aggregates the ``===RESULT===``
sentinels. Each suite reports its checks as ``OK <name>`` / ``FAIL <name>``
lines, so the totals here -- and the README badge -- count *individual checks*,
the same unit every sibling package reports. See
m3trik/docs/TEST_BADGE_STANDARD.md.

Run with:
    python run_tests.py                       # every suite
    python run_tests.py bevel xform_utils     # named suites only (no badge)
    python run_tests.py --list                # list available suites
    python run_tests.py --blender <path>      # explicit blender.exe
    python run_tests.py --no-badge            # skip the README badge update
    python run_tests.py --suite-timeout 900   # per-suite kill timer (default 600s)
"""
import argparse
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple

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
    """Locate blender.exe: arg > BLENDERTK_BLENDER env > newest install > PATH."""
    if explicit:
        if Path(explicit).exists():
            return explicit
        print(f"[WARNING] --blender path not found: {explicit}")
        return None
    try:
        from pythontk import AppLauncher

        found = AppLauncher.resolve_app_path(
            env_vars=("BLENDERTK_BLENDER",),
            scan_globs=("{program_files}/Blender Foundation/Blender */blender.exe",),
        )
        if found:
            return found
    except ImportError:
        pass
    return shutil.which("blender")


class BlenderTestRunner:
    """Runs each blendertk suite in its own fresh headless Blender."""

    def __init__(
        self, blender: str, test_dir: Path = TEST_DIR, suite_timeout: int = 600
    ):
        self.blender = blender
        self.test_dir = test_dir
        self.suite_timeout = suite_timeout
        self.venv_python = find_venv_python()

    def discover(self, patterns: Optional[List[str]] = None) -> List[Path]:
        """Suites are ``test_*.py`` + the smoke test + the ``*_slot_check.py``
        slot-wiring harnesses (they emit the same sentinel). Utility scripts in
        this dir (e.g. dump_runtime_surface.py) don't emit one and stay excluded.
        """
        suites = sorted(
            f
            for f in self.test_dir.glob("*.py")
            if f.name.startswith("test_")
            or f.name.endswith("_slot_check.py")
            or f.name == "blender_smoke_test.py"
        )
        if patterns:
            wanted = {p if p.startswith("test_") else f"test_{p}" for p in patterns}
            suites = [s for s in suites if s.stem in wanted or s.name in patterns]
        return suites

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
        cmd = (
            [python, str(suite)]
            if python
            else [
                self.blender,
                "--background",
                "--factory-startup",
                "--python",
                str(suite),
            ]
        )
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=self.suite_timeout,
            )
        except subprocess.TimeoutExpired:
            # A hung suite must cost a failure, not stall the whole run: with
            # no timeout, one wedged Blender blocked every suite behind it.
            self._print_detail(
                f"TIMEOUT {suite.name}: no result after {self.suite_timeout}s "
                "(process killed)"
            )
            return False, 0, 1, False
        lines = (proc.stdout or "").splitlines()

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
            if not verdicts:
                tail = (proc.stderr or "").strip().splitlines()[-5:]
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
    parser.add_argument("--blender", help="Path to blender.exe")
    parser.add_argument("--list", action="store_true", help="List available suites")
    parser.add_argument(
        "--no-badge", action="store_true", help="Skip the README badge update"
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

    runner = BlenderTestRunner(blender, suite_timeout=args.suite_timeout)

    if args.list:
        for suite in runner.discover():
            print(f"  {suite.stem}")
        return 0

    print(f"Blender: {blender}\n")
    result = runner.run(args.suites or None)

    # A scoped run must not clobber the badge with a partial count -- it would
    # read as a coverage regression. Nor may a run that discovered nothing.
    if not args.no_badge and not args.suites and result["suites"]:
        runner.update_readme_badge(result["passed"], result["failed"])

    return 1 if result["failed_suites"] else 0


if __name__ == "__main__":
    sys.exit(main())
