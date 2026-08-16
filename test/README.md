# blendertk Test Suite

Suites need the real Blender runtime (`bpy`) — **except** the Qt-only ones,
which run under the workspace `.venv` (see below). Hard rule from
[`CLAUDE.md`](../CLAUDE.md): NEVER attach to or test against a **running**
Blender — `run_tests.py` launches a fresh
`blender --background --factory-startup --python <suite>` process per suite.
No exceptions for speed.

## Layout

| Path | What | How it runs |
|:---|:---|:---|
| `test/test_*.py` | Main suites, one per production module | `run_tests.py`, auto-discovered by glob |
| `test/blender_smoke_test.py` | Bootstrap / public-surface smoke test | discovered alongside the main suites |
| `test/*_slot_check.py` | Slot-wiring harnesses (emit the same sentinel) | discovered alongside the main suites |
| `test/*_gui_check.py`, `test/*_live_e2e.py` | Manual GUI / end-to-end scripts | by hand only — not discovered |
| `test/temp_tests/` | Gitignored scratch (repro/probe scripts, saved `.blend`/render artifacts) | ad hoc; suites clean up after themselves |

## Suite conventions

No `base_test.py` / `conftest.py` here — each suite is a **self-contained
script** run by a fresh headless Blender:

- Bootstraps `sys.path` itself (repo root + sibling `pythontk`, plus `uitk`
  for the Qt suites); `import bpy` happens inside the `try` body.
- Collects `OK <name>` / `FAIL <name>` lines via a local `check()` helper and
  ends with one sentinel line `===RESULT: PASS|FAIL===`, ideally carrying an
  `(ok/attempted)` tally (authoritative over the runner's line-counting) or
  `(skipped)`.
- unittest-based suites (e.g. `test_cancel_provider.py`) translate their
  `TestResult` into the same tally — a suite that omits the sentinel is
  recorded FAILED however green its assertions were.
- A suite that dies before its sentinel (import error, crash) is charged one
  failure; one that passes while reporting zero checks is flagged "silent".

## Running

```powershell
python run_tests.py                  # every suite (updates the README badge)
python run_tests.py bevel xform_utils    # named suites (test_ prefix optional; no badge)
python run_tests.py --list           # list discovered suites
python run_tests.py --blender <path>     # explicit blender.exe
python run_tests.py --suite-timeout 900  # per-suite kill timer (default 600s)
python run_tests.py --no-badge       # skip the README badge update

# PowerShell wrapper (thin; prefers the workspace .venv python):
powershell -File blendertk/test/Run-Tests.ps1 [-BlenderExe <path>]

# one suite by hand:
& "C:\Program Files\Blender Foundation\Blender 5.1\blender.exe" `
  --background --factory-startup --python test\test_edit_utils.py
```

`run_tests.py` resolves Blender via `--blender` > `BLENDERTK_BLENDER` >
newest install > PATH, runs each suite in its own fresh headless Blender,
and aggregates the `===RESULT===` sentinels. Totals count **individual
checks** — the unit every sibling package reports
(`m3trik/docs/TEST_BADGE_STANDARD.md`). A hung suite is killed after
`--suite-timeout` and charged a failure instead of stalling the run. The
`docs/README.md` badge updates only on a full, unscoped run (`--no-badge`
skips). Exit code 1 if any suite failed.

## Qt-only suites (workspace `.venv`)

Headless Blender ships no Qt binding, so suites that need **Qt, not bpy**
(`test_blender_ui_handler.py`, `test_blender_native_menus.py`,
`test_cancel_provider.py`, the panel/slots harnesses) print
`===RESULT: PASS=== (skipped)` under the Blender harness; `run_tests.py`
detects the skip and re-runs the suite under the workspace `.venv`
interpreter — that result is the one counted (a suite that skips in both
stays SKIP). Direct run: `.venv\Scripts\python.exe test\test_blender_ui_handler.py`.
These sandbox QSettings via `uitk/test/conftest.py` and refuse to run
without it — never against the live per-user store.

## Writing tests

```python
import sys, os, traceback
# ... sys.path bootstrap (copy an existing suite's header) ...
lines = []
def check(name, cond, detail=""):
    lines.append(f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}")

try:
    import bpy
    import blendertk as btk
    check("decimate reduces faces", ...)
except Exception:
    lines.append(f"FAIL setup: {traceback.format_exc()}")

ok = all(not l.startswith("FAIL") for l in lines)
print("\n".join(lines))
print(f"===RESULT: {'PASS' if ok else 'FAIL'}=== "
      f"({sum(1 for l in lines if l.startswith('OK'))}/{len(lines)})")
```

One suite per production module; new `test_*.py` files are picked up
automatically, no registration needed. Artifacts (saved `.blend`s,
screenshots) go under `test/temp_tests/`, cleaned up by the suite that made
them; reproduction/debug scripts live there too, never in the main suite.
Mirror mayatk's suite names where the modules correspond so parity stays
mechanical.
