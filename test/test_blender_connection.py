"""blendertk BlenderConnection feature test — launches FRESH headless Blender child processes
and captures their output (mirror of mayatk's ``env_utils.maya_connection``).

Runs inside the harness Blender and spawns child ``blender --background`` runs to verify (each
child is a separate, session-safe process). Uses ``bpy.app.binary_path`` as the exe so the run
checks don't depend on install-dir discovery (which is exercised separately).

Run: blender --background --factory-startup --python blendertk/test/test_blender_connection.py
"""
import sys
import os
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for p in (REPO, os.path.join(MONO, "pythontk")):
    if p not in sys.path:
        sys.path.insert(0, p)

lines = []


def check(name, cond, detail=""):
    lines.append(f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}")


try:
    import bpy
    import blendertk as btk
    from blendertk.env_utils.blender_connection import BlenderConnection, RESULT_PASS

    check("btk.BlenderConnection resolves", btk.BlenderConnection is BlenderConnection)

    # ---- discovery ----------------------------------------------------------
    found = BlenderConnection.find_blender()
    check("find_blender() returns an existing executable", bool(found) and os.path.isfile(found), f"{found}")

    # ---- explicit bad exe -> FileNotFoundError ------------------------------
    try:
        BlenderConnection(blender_exe=os.path.join(tempfile.gettempdir(), "nope_blender.exe"))
        check("bad blender_exe -> FileNotFoundError", False)
    except FileNotFoundError:
        check("bad blender_exe -> FileNotFoundError", True)

    exe = bpy.app.binary_path  # the running Blender — guaranteed valid
    conn = BlenderConnection(blender_exe=exe)

    # ---- session-safety args ------------------------------------------------
    base = conn._base_args()
    check("session-safe base args (--background --factory-startup)",
          base[0] == "--background" and "--factory-startup" in base, f"{base}")
    check("factory_startup=False drops the flag",
          "--factory-startup" not in BlenderConnection(blender_exe=exe, factory_startup=False)._base_args())

    # ---- run_result: PASS sentinel + captures program output (1 child run) --
    passed, cp = conn.run_result(
        code="import bpy\nprint('VER', bpy.app.version_string)\nprint('%s')" % RESULT_PASS,
        timeout=180,
    )
    check("run_result(PASS) -> passed=True", passed is True)
    check("child process exited 0", cp.returncode == 0, f"rc={cp.returncode}")
    check("child output is captured (ran our code)", "VER" in (cp.stdout or ""))

    # ---- run_result: FAIL sentinel -> not passed (1 child run) --------------
    failed_passed, _ = conn.run_result(code="print('===RESULT: FAIL===')", timeout=180)
    check("run_result(FAIL) -> passed=False", failed_passed is False)

    # ---- run_script forwards script_args after `--` (1 child run) -----------
    fd, spath = tempfile.mkstemp(suffix=".py", prefix="btk_conn_args_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(
            "import sys\n"
            "argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []\n"
            "print('ARGS', ' '.join(argv))\n"
        )
    try:
        cp2 = conn.run_script(spath, script_args=["alpha", "beta"], timeout=180)
        check("run_script forwards script_args via sys.argv",
              "ARGS alpha beta" in (cp2.stdout or ""), f"{(cp2.stdout or '')[-120:]}")
    finally:
        os.remove(spath)

    # ---- run_result rejects ambiguous / empty target -----------------------
    try:
        conn.run_result()
        check("run_result() with neither arg -> ValueError", False)
    except ValueError:
        check("run_result() with neither arg -> ValueError", True)

except Exception as e:
    lines.append(f"FAIL setup: {e!r}")
    lines.append(traceback.format_exc())

ok = all(line.startswith("OK") for line in lines)
for line in lines:
    print(line)
print(f"===RESULT: {'PASS' if ok else 'FAIL'}===")
