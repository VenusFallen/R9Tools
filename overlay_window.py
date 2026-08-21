"""
Small transparent click-through overlay window, centered on the primary screen.
Hosts the crosshair and strength indicator.

Transparency approach:
  WS_EX_LAYERED with LWA_COLORKEY is used for transparency.  The entire window
  is painted with a magenta background in paintEvent; DWM treats all magenta
  pixels as punch-through holes (click-through and not composited).  Only
  crosshair pixels that differ from magenta are visible.

  This replicates the original tkinter overlay's transparency mechanism
  (wm_attributes -transparentcolor), which did not cause Independent Flip
  disruption or FPS drops — unlike WA_TranslucentBackground (LWA_ALPHA) or
  SetWindowRgn (setMask), both of which forced DWM into software composition mode.

  WS_EX_TRANSPARENT is also applied so all pixels (including visible crosshair
  pixels) pass mouse events through to the game.
"""
import ctypes

from PySide6.QtCore import Qt, QPoint, QTimer, Slot
from PySide6.QtGui  import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget

_GWL_EXSTYLE       = -20
_WS_EX_LAYERED     = 0x00080000
_WS_EX_TRANSPARENT = 0x00000020
_WS_EX_NOACTIVATE  = 0x08000000
_LWA_COLORKEY      = 0x00000001

# Magenta as Win32 COLORREF (R=0xFF, G=0x00, B=0xFF → 0x00FF00FF)
# and as a QColor for paintEvent's background fill.
_COLORKEY      = 0x00FF00FF
_QCOLOR_KEY    = QColor(255, 0, 255)

# Fixed window size — large enough for any crosshair style + indicators + SI.
_OVERLAY_SIZE = 400

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
        self._settings  = settings
        self._engine    = engine
        self._chVisible = False
        self._siValue   = None

        # WA_NoSystemBackground: prevent Qt from auto-filling the window
        # background before paintEvent runs.
        # WindowDoesNotAcceptFocus + WS_EX_NOACTIVATE (set in showEvent) ensure
        # the game never loses focus, avoiding background-mode FPS caps.
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )

        screen = QApplication.primaryScreen().geometry()
        self.setFixedSize(_OVERLAY_SIZE, _OVERLAY_SIZE)
        self.move(
            screen.x() + screen.width()  // 2 - _OVERLAY_SIZE // 2,
            screen.y() + screen.height() // 2 - _OVERLAY_SIZE // 2,
        )

        self._pollTimer = QTimer(self)
        self._pollTimer.timeout.connect(self._windowPoll)
        self._pollTimer.start(500)
        QTimer.singleShot(50, self._windowPoll)

        self._siTimer = QTimer(self)
        self._siTimer.setSingleShot(True)
        self._siTimer.timeout.connect(self._hideSI)

    def showEvent(self, event):
        super().showEvent(event)
        hwnd  = int(self.winId())
        # WS_EX_LAYERED: required for SetLayeredWindowAttributes (color key).
        # WS_EX_TRANSPARENT: all pixels pass mouse events through to the game.
        # WS_EX_NOACTIVATE: window can never steal focus from the game.
        style = ctypes.windll.user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
        ctypes.windll.user32.SetWindowLongW(
            hwnd, _GWL_EXSTYLE,
            style | _WS_EX_LAYERED | _WS_EX_TRANSPARENT | _WS_EX_NOACTIVATE
        )
        ctypes.windll.user32.SetLayeredWindowAttributes(
            hwnd, _COLORKEY, 0, _LWA_COLORKEY
        )

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def refresh(self):
        """Sync visibility from settings and repaint."""
        cs = self._settings.get("crosshair", {})
        if not cs.get("enabled", False):
            self._chVisible = False
            self.hide()
            return
        wf  = self._settings.get("window_filter", "")
        vis = True if not wf else self._engine.windowMatchesFilter(wf)
        self._chVisible = vis
        if vis:
            if not self.isVisible():
                self.show()
            self.update()
        else:
            self.hide()

    # ------------------------------------------------------------------
    # Crosshair visibility poll (window filter check)
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
            if vis:
                if not self.isVisible():
                    self.show()
                self.update()
            else:
                self.hide()

    # ------------------------------------------------------------------
    # Strength indicator
    # ------------------------------------------------------------------

    @Slot(int)
    def showStrengthIndicator(self, value: int):
        prev          = self._siValue
        self._siValue = value
        self._siTimer.start(500)
        if prev != value:
            self.update()

    def _hideSI(self):
        self._siValue = None
        self.update()

    # ------------------------------------------------------------------
    # Paint
    # ------------------------------------------------------------------

    def paintEvent(self, event):
        painter = QPainter(self)
        # Fill entire window with the transparent color key.
        # Every pixel not overwritten by the crosshair draw calls will be
        # punched through by DWM (LWA_COLORKEY) — invisible and click-through.
        painter.fillRect(self.rect(), _QCOLOR_KEY)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._chVisible:
            self._drawCrosshair(painter)
            self._drawModuleIndicators(painter)
        if self._siValue is not None:
            self._drawSI(painter)
        painter.end()

    def _drawCrosshair(self, painter: QPainter):
        cs      = self._settings["crosshair"]
        style   = cs.get("style",        "cross")
        color   = cs.get("color",        "green")
        size    = cs.get("size",         10)
        thick   = cs.get("thickness",    2)
        gap     = cs.get("gap",          4)
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

        if outline > 0:
            if has_cross:  draw_cross(bg, thick + outline * 2)
            if has_circle: draw_circle(bg, thick + outline * 2)
            if has_dot:    draw_dot(bg, thick + outline)

        if has_cross:  draw_cross(fg, thick)
        if has_circle: draw_circle(fg, thick)
        if has_dot:    draw_dot(fg, thick)

    def _drawModuleIndicators(self, painter: QPainter):
        cs    = self._settings.get("crosshair", {})
        color = cs.get("color", "green")
        fg    = QColor(_COLOR_MAP.get(color, "#00ff00"))
        bg    = QColor("#000000")

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
        widths  = [fm.horizontalAdvance(lbl) for lbl in labels]
        total_w = sum(widths) + spacing * (len(labels) - 1)

        x = cx - total_w // 2
        y = cy + 30

        for i, label in enumerate(labels):
            w = widths[i]
            painter.setPen(bg)
            for dx, dy in ((-1,-1),(-1,0),(-1,1),(0,-1),(0,1),(1,-1),(1,0),(1,1)):
                painter.drawText(x + dx, y + dy, w, h,
                                 Qt.AlignmentFlag.AlignLeft, label)
            painter.setPen(fg)
            painter.drawText(x, y, w, h, Qt.AlignmentFlag.AlignLeft, label)
            x += w + spacing

    def _drawSI(self, painter: QPainter):
        text = str(self._siValue)
        cx   = self.width()  // 2 - 8
        cy   = self.height() // 2 - 12
        x, y, w, h = cx - 18, cy - 12, 36, 24

        painter.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))

        painter.setPen(QColor("#000000"))
        for dx, dy in ((-1, -1), (-1, 0), (-1, 1),
                       ( 0, -1),           ( 0, 1),
                       ( 1, -1), ( 1, 0), ( 1, 1)):
            painter.drawText(x + dx, y + dy, w, h,
                             Qt.AlignmentFlag.AlignCenter, text)

        painter.setPen(QColor("#ffffff"))
        painter.drawText(x, y, w, h, Qt.AlignmentFlag.AlignCenter, text)
