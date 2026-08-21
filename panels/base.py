from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout

import theme


class Panel(QWidget):
    """
    Base class for all overlay panels.

    Subclasses add their widgets to self._layout (a QVBoxLayout, top-aligned).

    right_anchor — when True, PanelWindow repositions itself to the right side
                   of the screen when this panel is active (Profiles, Settings).
    panel_width  — content width (px) PanelWindow should use for this tab's
                   window when it's active. Defaults to theme.PANEL_W so most
                   tabs don't need to think about this; a tab that needs a
                   wider/landscape layout (e.g. Macros) can pass a larger
                   value. PanelWindow reads this per-tab in _reposition().
    """

    def __init__(self, parent: QWidget = None, right_anchor: bool = False,
                 panel_width: int = None):
        super().__init__(parent)
        self.right_anchor = right_anchor
        self.panel_width  = panel_width if panel_width is not None else theme.PANEL_W

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 14)
        self._layout.setSpacing(0)
        self._layout.setAlignment(Qt.AlignmentFlag.AlignTop)

    def reload(self, settings: dict):
        """Called when a profile is loaded. Override in subclasses."""
        pass
