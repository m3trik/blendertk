# coding=utf-8
"""Behaviors — load and apply keying recipes (Blender).

Mirror of mayatk's ``shot_manifest.behaviors`` package.  A behavior template
defines attribute keyframe patterns (e.g. fade-in, fade-out) anchored to a time
range's start or end.

The pure core (template discovery/loading, schema, keyframe math) lives in
``pythontk.core_utils.engines.shots.manifest.behaviors`` — JSON templates
shared with mayatk; built-ins ship with the engine and user templates go under
``user_config_root()/shots/manifest_behaviors/``.  The Blender appliers live
in :mod:`._behaviors`.

Package facade: the public API is re-exported here, so
``from ...behaviors import X`` keeps working and ``mock.patch`` of
``...behaviors.X`` still takes effect for callers that read the name off this
package (the lazy ``from ...behaviors import Behaviors`` other modules do at
call time).

To intercept an *intra-class* call — one ``Behaviors`` method calling another
(``apply_behavior`` → ``apply_audio_clip``, ``verify_behavior`` →
``_verify_audio_clip``) — patch ``...behaviors._behaviors.<Class>.<name>``,
where the call is actually resolved.
"""

from blendertk.anim_utils.shots.shot_manifest.behaviors._behaviors import (
    Behaviors,
    load_behavior,
    list_behaviors,
    resolve_keys,
    templates,
)
from pythontk.core_utils.engines.shots.manifest.behaviors._spec import (  # noqa: F401
    BehaviorSpec,
)

__all__ = [
    "load_behavior",
    "list_behaviors",
    "resolve_keys",
    "Behaviors",
    "templates",
    "BehaviorSpec",
]
