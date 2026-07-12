# !/usr/bin/python
# coding=utf-8
"""Constants, column layout, and pure helper functions for the Shot Manifest UI.

Blender mirror of mayatk's ``shot_manifest.manifest_data``.  Everything here is
pure Qt/data with two DCC swaps versus the Maya original:

- the status palette is imported from the shared ``pythontk`` engine (single
  source of truth) rather than a mayatk module;
- :func:`try_load_blender_icons` returns ``None`` — Blender has no equivalent to
  Maya's ``:/`` node-type icon resources, so per-node-type icons degrade
  gracefully to uitk's named-icon fallback (documented divergence).

(mayatk's ``manifest_data`` re-exports ``prune_to_top_boundaries`` from
``pythontk``'s ``range_resolver`` for its legacy callers — not mirrored here;
import it from ``pythontk`` directly if ever needed.)
"""
from pythontk import SHOT_PALETTE

# Settings namespace (QSettings)
SETTINGS_NS = "ShotManifest"

# Column headers for the manifest tree widget
HEADERS = ["Step", "Section", "Description", "Behaviors", "Start", "End"]

# Fixed column indices for the unified 6-column layout
COL_STEP = 0
COL_SECTION = 1
COL_DESC = 2  # parent: description, child: object name
COL_BEHAVIORS = 3
COL_START = 4
COL_END = 5

STEP_ICON_COLOR = "#8E8E8E"  # neutral dark grey for parent step rows

# Assessment status colours — shared palette from the pythontk engine (SSoT).
PASTEL_STATUS = SHOT_PALETTE

# Foreground colors for behavior issue states on child rows.
# Valid behaviors are rendered without color.
BEHAVIOR_STATUS_COLORS = {
    "missing": PASTEL_STATUS["missing_behavior"][0],  # warn gold
    "error": PASTEL_STATUS["missing_object"][0],  # error red
}

# Derived from the palette — used for footer error labels
ERROR_COLOR = PASTEL_STATUS["error"][0]


def fmt_behavior(name: str) -> str:
    """``'fade_in'`` → ``'Fade In'``."""
    return name.replace("_", " ").title() if name else ""


def format_behavior_html(behaviors, broken=(), status_color=None) -> str:
    """Return rich-text HTML for a list of behavior names.

    Parameters:
        behaviors: Sequence of raw behavior names to display.
        broken: Subset of *behaviors* that failed verification.
            These are rendered with the ``missing_behavior`` palette
            colour; the rest are left uncoloured.
        status_color: Optional override colour applied to *all* behaviours.
            When set, *broken* is ignored and every behaviour is rendered
            in this colour (e.g. the error colour for missing objects).
    """
    if not behaviors:
        return ""
    spans = []
    if status_color:
        for b in behaviors:
            display = fmt_behavior(b)
            spans.append(f'<span style="color:{status_color}">{display}</span>')
    else:
        broken_set = set(broken)
        for b in behaviors:
            display = fmt_behavior(b)
            if b in broken_set:
                color = BEHAVIOR_STATUS_COLORS.get("missing")
                spans.append(f'<span style="color:{color}">{display}</span>')
            else:
                spans.append(display)
    return "  ".join(spans)


def try_load_blender_icons():
    """Return per-node-type icon provider, or ``None``.

    Blender has no analogue to Maya's ``:/`` node-type icon resources, so this
    returns ``None`` and the manifest table falls back to uitk's named-icon set
    (a documented parity divergence).  Kept as a hook so a future Blender
    object-type → icon map can be dropped in without touching the presenter.
    """
    return None
