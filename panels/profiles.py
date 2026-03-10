from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
)

import profiles as prof
import theme
from panels.base import Panel


class ProfilesPanel(Panel):

    def __init__(self, parent, profileData: dict, onLoad, onSave, onDelete):
        super().__init__(parent, right_anchor=True)
        self._profileData = profileData
        self._onLoad      = onLoad
        self._onSave      = onSave
        self._onDelete    = onDelete
        self._build()

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build(self):
        title = QLabel("Profiles")
        title.setStyleSheet(
            f"color: {theme.ACCENT}; font: bold 10pt 'Segoe UI';"
            f" padding: 8px 10px 2px 10px;")
        self._layout.addWidget(title)

        self._layout.addWidget(_sep())

        # Active profile label
        activeLbl = QLabel("Active Profile:")
        activeLbl.setStyleSheet("padding: 0px 10px;")
        self._layout.addWidget(activeLbl)

        # Combobox + quick-save
        comboRow = QFrame()
        cl = QHBoxLayout(comboRow)
        cl.setContentsMargins(10, 2, 10, 8)
        cl.setSpacing(4)
        self._combo = QComboBox(comboRow)
        self._combo.setMinimumWidth(140)
        cl.addWidget(self._combo)
        self._quickSaveBtn = QPushButton("Save", comboRow)
        self._quickSaveBtn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._quickSaveBtn.clicked.connect(self._onQuickSave)
        cl.addWidget(self._quickSaveBtn)
        cl.addStretch()
        self._layout.addWidget(comboRow)

        self._refreshCombo()
        self._combo.currentTextChanged.connect(self._onSelect)

        self._layout.addWidget(_sep())

        # Name entry + save + delete
        nameLbl = QLabel("Profile Name:")
        nameLbl.setStyleSheet("padding: 4px 10px 2px 10px;")
        self._layout.addWidget(nameLbl)

        actionRow = QFrame()
        al = QHBoxLayout(actionRow)
        al.setContentsMargins(10, 0, 10, 10)
        al.setSpacing(4)
        self._nameEntry = QLineEdit(actionRow)
        self._nameEntry.setPlaceholderText("Enter name...")
        self._nameEntry.setMinimumWidth(100)
        al.addWidget(self._nameEntry)
        saveBtn = QPushButton("Save", actionRow)
        saveBtn.setCursor(Qt.CursorShape.PointingHandCursor)
        saveBtn.clicked.connect(self._onSaveClick)
        al.addWidget(saveBtn)
        delBtn = QPushButton("Delete", actionRow)
        delBtn.setCursor(Qt.CursorShape.PointingHandCursor)
        delBtn.setStyleSheet(
            f"QPushButton {{ color: #ff6666; background-color: {theme.BTN_BG}; border: none; padding: 3px 8px; }}"
            f"QPushButton:hover {{ background-color: {theme.HOVER_BG}; }}")
        delBtn.clicked.connect(self._onDeleteClick)
        al.addWidget(delBtn)
        self._layout.addWidget(actionRow)

    # ------------------------------------------------------------------
    # Combo management
    # ------------------------------------------------------------------

    def refreshCombo(self):
        self._combo.blockSignals(True)
        names = prof.profileNames(self._profileData)
        self._combo.clear()
        self._combo.addItems(names)
        active = self._profileData["active"]
        self._combo.setCurrentText(active if active in names else names[0])
        self._combo.blockSignals(False)
        self._updateQuickSaveBtn()

    _refreshCombo = refreshCombo

    def _updateQuickSaveBtn(self):
        is_default = self._combo.currentText() == prof.DEFAULT_NAME
        self._quickSaveBtn.setEnabled(not is_default)

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _onSelect(self, name: str):
        if name:
            self._onLoad(name)
        self._updateQuickSaveBtn()

    def _onQuickSave(self):
        name = self._combo.currentText()
        if name:
            self._onSave(name)

    def _onSaveClick(self):
        name = self._nameEntry.text().strip()
        if name:
            self._onSave(name)

    def _onDeleteClick(self):
        name = self._nameEntry.text().strip()
        if name:
            self._onDelete(name)
            self._nameEntry.clear()


def _sep():
    s = QFrame()
    s.setFrameShape(QFrame.Shape.HLine)
    s.setFixedHeight(1)
    s.setStyleSheet(f"background-color: {theme.PANEL_BORDER};")
    return s
