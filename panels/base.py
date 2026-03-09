import tkinter as tk
import theme


class Panel:
    """Base class for all overlay panels."""

    def __init__(self, root: tk.Tk, right_anchor: bool = False):
        self._root        = root
        self._right_anchor = right_anchor
        self._border = tk.Frame(root, bg=theme.PANEL_BORDER)
        self._frame  = tk.Frame(self._border, bg=theme.PANEL_BG)
        self._frame.pack(padx=1, pady=1)
        tk.Frame(self._frame, bg=theme.PANEL_BG,
                 width=theme.PANEL_MIN_W, height=1).pack()

    def show(self):
        if self._right_anchor:
            self._border.place(relx=1.0, y=theme.PANEL_Y, anchor="ne")
        else:
            self._border.place(x=0, y=theme.PANEL_Y)

    def hide(self):
        self._border.place_forget()

    def reload(self, settings: dict):
        pass
