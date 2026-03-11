"""
Full-screen transparent click-through overlay window.
Hosts the crosshair and strength indicator.

Transparency approach:
  - WA_TranslucentBackground (set before show) → Qt sets WS_EX_LAYERED internally.
  - WS_EX_TRANSPARENT (set in showEvent after HWND exists) → entire window is
    click-through; mouse events fall through to whatever is beneath.
  Both flags are required; order matters.
"""
import ctypes

from PySide6.QtCore import Qt, QPoint, QTimer, Slot
from PySide6.QtGui  import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget

_GWL_EXSTYLE       = -20
_WS_EX_TRANSPARENT = 0x00000020

_COLOR_MAP = {
    "green":  "#00ff00",
    "red":    "#ff0000",
    "white":  "#ffffff",
    "pink":   "#ff1493",
    "yellow": "#ffff00",
}


class OverlayWindow(QWidget):

    def __init__(self, settings: dict, engine):
        super().__init__()
        self._settings   = settings
        self._engine     = engine
        self._chVisible  = False   # gated by enabled + window filter
        self._siValue    = None    # strength indicator value (None = hidden)

        # Must be set BEFORE show() so Qt applies WS_EX_LAYERED at window creation.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool          # no taskbar entry, no Alt+Tab
        )

        self.setGeometry(QApplication.primaryScreen().geometry())

        # 500ms poll to gate crosshair via window filter
        self._pollTimer = QTimer(self)
        self._pollTimer.timeout.connect(self._windowPoll)
        self._pollTimer.start(500)

        # Strength indicator auto-hide timer
        self._siTimer = QTimer(self)
        self._siTimer.setSingleShot(True)
        self._siTimer.timeout.connect(self._hideSI)

    def showEvent(self, event):
        super().showEvent(event)
        # HWND is valid now — apply WS_EX_TRANSPARENT so mouse events pass through.
        hwnd  = int(self.winId())
        style = ctypes.windll.user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
        ctypes.windll.user32.SetWindowLongW(
            hwnd, _GWL_EXSTYLE, style | _WS_EX_TRANSPARENT
        )

    # ------------------------------------------------------------------
    # Crosshair visibility poll
    # ------------------------------------------------------------------

    def _windowPoll(self):
        cs = self._settings.get("crosshair", {})
        if not cs.get("enabled", False):
            vis = False
        else:
            wf  = self._settings.get("window_filter", "")
            vis = True if not wf else self._engine.windowMatchesFilter(wf)
        if vis != self._chVisible:
            self._chVisible = vis
            self.update()

    # ------------------------------------------------------------------
    # Strength indicator (called from bridge signal)
    # ------------------------------------------------------------------

    @Slot(int)
    def showStrengthIndicator(self, value: int):
        self._siValue = value
        self._siTimer.start(500)
        self.update()

    def _hideSI(self):
        self._siValue = None
        self.update()

    # ------------------------------------------------------------------
    # Paint
    # ------------------------------------------------------------------

    def paintEvent(self, event):
        if not self._chVisible and self._siValue is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._chVisible:
            self._drawCrosshair(painter)
            self._drawModuleIndicators(painter)
        if self._siValue is not None:
            self._drawSI(painter)
        painter.end()

    def _drawCrosshair(self, painter: QPainter):
        cs      = self._settings["crosshair"]
        style   = cs.get("style", "cross")
        color   = cs.get("color", "green")
        size    = cs.get("size", 10)
        thick   = cs.get("thickness", 2)
        gap     = cs.get("gap", 4)
        outline = cs.get("outline_size", 1)

        fg = QColor(_COLOR_MAP.get(color, "#00ff00"))
        bg = QColor("#000000")

        cx = self.width()  // 2
        cy = self.height() // 2

        has_cross  = style in ("cross",  "dot_cross")
        has_dot    = style in ("dot",    "dot_cross", "circle_dot")
        has_circle = style in ("circle", "circle_dot")

        def draw_cross(c: QColor, w: int):
            painter.setPen(QPen(c, w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawLine(cx - gap - size, cy, cx - gap,        cy)
            painter.drawLine(cx + gap,        cy, cx + gap + size, cy)
            painter.drawLine(cx, cy - gap - size, cx, cy - gap)
            painter.drawLine(cx, cy + gap,        cx, cy + gap + size)

        def draw_dot(c: QColor, r: int):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(c)
            painter.drawEllipse(QPoint(cx, cy), r, r)

        def draw_circle(c: QColor, w: int):
            painter.setPen(QPen(c, w, Qt.PenStyle.SolidLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPoint(cx, cy), size, size)

        # Outlines first
        if outline > 0:
            if has_cross:  draw_cross(bg, thick + outline * 2)
            if has_circle: draw_circle(bg, thick + outline * 2)
            if has_dot:    draw_dot(bg, thick + outline)

        # Foreground colors on top
        if has_cross:  draw_cross(fg, thick)
        if has_circle: draw_circle(fg, thick)
        if has_dot:    draw_dot(fg, thick)

    def _drawModuleIndicators(self, painter: QPainter):
        cs        = self._settings.get("crosshair", {})
        color     = cs.get("color", "green")
        fg        = QColor(_COLOR_MAP.get(color, "#00ff00"))
        bg        = QColor("#000000")

        labels = []
        if self._settings.get("recoil",    {}).get("enabled", False):
            labels.append("R")
        if self._settings.get("rapidfire", {}).get("enabled", False):
            labels.append("RF")
        if not labels:
            return

        cx = self.width()  // 2
        cy = self.height() // 2

        painter.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        fm      = painter.fontMetrics()
        spacing = 8
        h       = fm.height()
        widths  = [fm.horizontalAdvance(l) for l in labels]
        total_w = sum(widths) + spacing * (len(labels) - 1)

        x = cx - total_w // 2
        y = cy + 30

        for i, label in enumerate(labels):
            w = widths[i]
            painter.setPen(bg)
            for dx, dy in ((-1,-1),(-1,0),(-1,1),(0,-1),(0,1),(1,-1),(1,0),(1,1)):
                painter.drawText(x+dx, y+dy, w, h, Qt.AlignmentFlag.AlignLeft, label)
            painter.setPen(fg)
            painter.drawText(x, y, w, h, Qt.AlignmentFlag.AlignLeft, label)
            x += w + spacing

    def _drawSI(self, painter: QPainter):
        text = str(self._siValue)
        sw   = self.width()
        sh   = self.height()
        cx   = sw // 2 - int(sw * 0.02)
        cy   = sh // 2 - int(sh * 0.03)
        x, y, w, h = cx - 18, cy - 12, 36, 24

        painter.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))

        # Shadow / outline
        painter.setPen(QColor("#000000"))
        for dx, dy in ((-1, -1), (-1, 0), (-1, 1),
                       ( 0, -1),           ( 0, 1),
                       ( 1, -1), ( 1, 0), ( 1, 1)):
            painter.drawText(x + dx, y + dy, w, h,
                             Qt.AlignmentFlag.AlignCenter, text)

        # Main text
        painter.setPen(QColor("#ffffff"))
        painter.drawText(x, y, w, h, Qt.AlignmentFlag.AlignCenter, text)
