"""Application icon — a self-contained tile that stays legible on any
background.

The window/taskbar/tray icon used to ship two monochrome variants (a
dark glyph for light themes, a light glyph for dark) and pick one from
the QPalette window background. That breaks wherever the surface the
icon lands on isn't the window background — most visibly the
taskbar/panel, which many desktops theme dark even under a light app
theme, and which an app has no portable way to inspect (Qt exposes the
window palette, not the panel's colour; the freedesktop
``color-scheme`` portal is commonly unset). The dark glyph then
rendered as a near-invisible smudge on a dark panel.

So instead of guessing the background, the icon carries its own: a light
glyph on a dark rounded-rectangle tile with a faint rim. The light glyph
reads on any panel; the tile reads as a solid badge on light surfaces
and the rim keeps its outline on dark ones. One icon, no palette
branching.

Sibling marks are kept in ``assets/logos/`` for re-theming / a possible
future toggle — all the same glyph, different frame: the coin
(``ICON_CIRCLE``) and the original bare monochrome glyphs
(``ICON_MONO`` for light backgrounds, ``ICON_REVERSED`` for dark). Only
``_ICON`` is wired up.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QIcon

_LOGOS = Path(__file__).parent / "assets" / "logos"

# Active app icon: the background-independent dark tile (see docstring).
_ICON = _LOGOS / "qeth-icon-rounded.svg"

# Alternatives, kept for easy switching (assign one to _ICON to use it):
ICON_CIRCLE = _LOGOS / "qeth-icon-circle.svg"       # coin: glyph on a dark disc
ICON_MONO = _LOGOS / "qeth-icon-mono.svg"           # bare dark glyph, light bg
ICON_REVERSED = _LOGOS / "qeth-icon-reversed.svg"   # bare light glyph, dark bg


def app_icon() -> QIcon:
    """The application icon (window, taskbar, and — via the window icon —
    the tray). Background-independent; see the module docstring."""
    return QIcon(str(_ICON))
