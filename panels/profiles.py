import tkinter as tk
from tkinter import ttk
import profiles as prof

from theme import PANEL_BG, ACCENT, LABEL_FG, BTN_BG, BTN_FG, DIM
from panels.base import Panel


class ProfilesPanel(Panel):
    def __init__(self, root: tk.Tk, profileData: dict,
                 onLoad, onSave, onDelete):
        super().__init__(root, right_anchor=True)
        self._profileData = profileData
        self._onLoad      = onLoad
        self._onSave      = onSave
        self._onDelete    = onDelete
        self._build()

    def _build(self):
        tk.Label(self._frame, text="Profiles",
                 fg=ACCENT, bg=PANEL_BG,
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=10, pady=(8, 2))

        ttk.Separator(self._frame, orient="horizontal").pack(fill="x", padx=8, pady=4)

        # Active profile label
        tk.Label(self._frame, text="Active Profile:", fg=LABEL_FG, bg=PANEL_BG,
                 font=("Segoe UI", 9)).pack(anchor="w", padx=10)

        # Combobox + quick-save button
        comboRow = tk.Frame(self._frame, bg=PANEL_BG)
        comboRow.pack(fill="x", padx=10, pady=(2, 8))

        self._combo = ttk.Combobox(comboRow, state="readonly",
                                    font=("Segoe UI", 9), width=18)
        self._combo.pack(side="left", padx=(0, 4))
        self._refreshCombo()
        self._combo.bind("<<ComboboxSelected>>", self._onSelect)

        self._quickSaveBtn = tk.Button(comboRow, text="Save",
                                       command=self._onQuickSave,
                                       bg=BTN_BG, fg=BTN_FG, relief="flat",
                                       font=("Segoe UI", 9), padx=6,
                                       cursor="hand2")
        self._quickSaveBtn.pack(side="left")
        self._updateQuickSaveBtn()

        ttk.Separator(self._frame, orient="horizontal").pack(fill="x", padx=8, pady=4)

        # Name field label
        tk.Label(self._frame, text="Profile Name:", fg=LABEL_FG, bg=PANEL_BG,
                 font=("Segoe UI", 9)).pack(anchor="w", padx=10, pady=(4, 2))

        # Name entry + Save + Delete
        actionRow = tk.Frame(self._frame, bg=PANEL_BG)
        actionRow.pack(fill="x", padx=10, pady=(0, 10))

        self._nameVar = tk.StringVar()
        tk.Entry(actionRow, textvariable=self._nameVar,
                 font=("Segoe UI", 9), bg="#333333", fg=BTN_FG,
                 relief="flat", insertbackground=BTN_FG,
                 width=14).pack(side="left", padx=(0, 4))

        tk.Button(actionRow, text="Save",
                  command=self._onSaveClick,
                  bg=BTN_BG, fg=BTN_FG, relief="flat",
                  font=("Segoe UI", 9), padx=6,
                  cursor="hand2").pack(side="left", padx=(0, 2))

        tk.Button(actionRow, text="Delete",
                  command=self._onDeleteClick,
                  bg=BTN_BG, fg="#ff6666", relief="flat",
                  font=("Segoe UI", 9), padx=6,
                  cursor="hand2").pack(side="left")

    # ------------------------------------------------------------------
    # Combo management
    # ------------------------------------------------------------------

    def refreshCombo(self):
        names = prof.profileNames(self._profileData)
        self._combo["values"] = names
        active = self._profileData["active"]
        self._combo.set(active if active in names else names[0])
        self._updateQuickSaveBtn()

    def _updateQuickSaveBtn(self):
        if not hasattr(self, "_quickSaveBtn"):
            return
        is_default = self._combo.get() == prof.DEFAULT_NAME
        self._quickSaveBtn.config(state="disabled" if is_default else "normal",
                                  fg=DIM if is_default else BTN_FG)

    # internal alias used during build before the public name is needed
    _refreshCombo = refreshCombo

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _onSelect(self, _=None):
        name = self._combo.get()
        if name:
            self._onLoad(name)
        self._updateQuickSaveBtn()

    def _onQuickSave(self):
        name = self._combo.get()
        if name:
            self._onSave(name)

    def _onSaveClick(self):
        name = self._nameVar.get().strip()
        if name:
            self._onSave(name)

    def _onDeleteClick(self):
        name = self._nameVar.get().strip()
        if name:
            self._onDelete(name)
