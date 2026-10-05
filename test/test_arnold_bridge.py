"""blendertk ArnoldBridge headless test: the no-op mirror keeps mayatk's call shapes.

Blender has no Arnold, so ``btk.ArnoldBridge`` does nothing (its module
docstring says why) -- and exists only so a caller written against
``mtk.ArnoldBridge`` runs unchanged here. Parity of the call shapes is the whole
contract.

Run: blender --background --factory-startup --python blendertk/test/test_arnold_bridge.py
"""

import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for p in (REPO, os.path.join(MONO, "pythontk")):
    if p not in sys.path:
        sys.path.insert(0, p)

lines = []


def check(name, cond, detail=""):
    lines.append(
        f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}"
    )


try:
    import blendertk as btk

    # mayatk's TextureBaker bakes behind ``ArnoldBridge(ambient_occlusion=False)
    # .temporary(materials)`` (Arnold traces the occlusion an AO map fakes); the
    # mirror rejected the keyword with a TypeError, so that shape needed a branch.
    bridge = btk.ArnoldBridge(ambient_occlusion=False)
    check(
        "takes mayatk's ambient_occlusion keyword",
        bridge.ambient_occlusion is False,
        repr(bridge.ambient_occlusion),
    )
    check(
        "ambient_occlusion defaults on, as in mayatk",
        btk.ArnoldBridge().ambient_occlusion is True,
    )
    with bridge.temporary(["any_material"]) as bridged:
        check("the bake guard's shape bridges nothing", bridged == [], repr(bridged))

except Exception as e:
    traceback.print_exc()
    check("arnold bridge test raised", False, repr(e))

passed = sum(1 for line in lines if line.startswith("OK"))
for line in lines:
    print(line)
result = "PASS" if all(line.startswith("OK") for line in lines) else "FAIL"
print(f"===RESULT: {result}=== ({passed}/{len(lines)})")
