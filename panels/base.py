from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout


class Panel(QWidget):
    """
    Base class for all overlay panels.

    Subclasses add their widgets to self._layout (a QVBoxLayout, top-aligned).

    right_anchor — when True, PanelWindow repositions itself to the right side
                   of the screen when this panel is active (Profiles, Settings).
    """

    def __init__(self, parent: QWidget = None, right_anchor: bool = False):
        super().__init__(parent)
        self.right_anchor = right_anchor

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 14)
        self._layout.setSpacing(0)
        self._layout.setAlignment(Qt.AlignmentFlag.AlignTop)

    def reload(self, settings: dict):
        """Called when a profile is loaded. Override in subclasses."""
        pass
