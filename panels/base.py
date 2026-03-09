import tkinter as tk
from theme import PANEL_BG, PANEL_Y


class Panel:
    """Base class for all overlay panels.

    Provides the standard 1px violet border frame and show/hide/reload interface.
    Subclasses build their widgets into self._frame and call super().__init__(root)
    before _build().

    right_anchor=True anchors the panel to the top-right of the screen instead
    of the top-left, used for program-wide panels (Settings, Profiles).
    """

    def __init__(self, root: tk.Tk, right_anchor: bool = False):
        self._root        = root
        self._right_anchor = right_anchor
        self._border = tk.Frame(root, bg="#EE82EE")
        self._frame  = tk.Frame(self._border, bg=PANEL_BG)
        self._frame.pack(padx=1, pady=1)

    def show(self):
        if self._right_anchor:
            self._border.place(relx=1.0, y=PANEL_Y, anchor="ne")
        else:
            self._border.place(x=0, y=PANEL_Y)

    def hide(self):
        self._border.place_forget()

    def reload(self, settings: dict):
        pass
