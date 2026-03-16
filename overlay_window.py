"""
Small transparent click-through overlay window, centered on the primary screen.
Hosts the crosshair and strength indicator.

Transparency approach:
  setMask(QRegion) is used instead of WA_TranslucentBackground (WS_EX_LAYERED).
  WS_EX_LAYERED forces DWM to perform HDR-to-SDR tone-mapping on the entire
  display in borderless windowed mode, causing severe FPS drops and a washed-out
  image regardless of window size. setMask() creates a region-clipped opaque
  window: pixels outside the crosshair shape are absent from the window entirely
  (click-through and not composited by DWM at all).

  WS_EX_TRANSPARENT is still applied via SetWindowLongW so the in-mask pixels
  (the crosshair shape itself) also pass mouse events through to the game.
"""
import ctypes

from PySide6.QtCore import Qt, QPoint, QTimer, Slot
from PySide6.QtGui  import QColor, QFont, QPainter, QPen, QRegion
from PySide6.QtWidgets import QApplication, QWidget

_GWL_EXSTYLE       = -20
_WS_EX_TRANSPARENT = 0x00000020

# Window footprint — must be large enough for max crosshair + indicators + SI.
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

        # No WA_TranslucentBackground — setMask() is used instead.
        # WA_NoSystemBackground prevents Qt from auto-filling the window background;
        # paintEvent fills black explicitly before drawing, matching the outline color.
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
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
        # Apply WS_EX_TRANSPARENT so in-mask pixels pass mouse events through.
        hwnd  = int(self.winId())
        style = ctypes.windll.user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
        ctypes.windll.user32.SetWindowLongW(
            hwnd, _GWL_EXSTYLE, style | _WS_EX_TRANSPARENT
        )

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def refresh(self):
        """Sync _chVisible from current settings, recompute mask, and repaint."""
        cs = self._settings.get("crosshair", {})
        if not cs.get("enabled", False):
            self._chVisible = False
        else:
            wf = self._settings.get("window_filter", "")
            self._chVisible = True if not wf else self._engine.windowMatchesFilter(wf)
        self._refreshMask()

    # ------------------------------------------------------------------
    # Mask management
    # ------------------------------------------------------------------

    def _computeMask(self) -> QRegion:
        """Build a QRegion covering exactly the crosshair + indicator pixels."""
        cx  = _OVERLAY_SIZE // 2
        cy  = _OVERLAY_SIZE // 2
        rgn = QRegion()
        pad = 2  # extra pixels per element to absorb antialiasing fringe

        if self._chVisible:
            cs      = self._settings.get("crosshair", {})
            style   = cs.get("style",        "cross")
            size    = cs.get("size",         10)
            thick   = cs.get("thickness",    2)
            gap     = cs.get("gap",          4)
            outline = cs.get("outline_size", 1)

            has_cross  = style in ("cross",  "dot_cross")
            has_dot    = style in ("dot",    "dot_cross", "circle_dot")
            has_circle = style in ("circle", "circle_dot")

            total_pen = thick + outline * 2   # pen width used for the outline pass
            half_t    = total_pen // 2 + pad  # half-thickness of crosshair arms
            arm_ext   = size + outline + pad  # arm length from the gap edge

            if has_cross:
                rgn |= QRegion(cx - gap - arm_ext, cy - half_t, arm_ext, half_t * 2)  # left
                rgn |= QRegion(cx + gap,           cy - half_t, arm_ext, half_t * 2)  # right
                rgn |= QRegion(cx - half_t, cy - gap - arm_ext, half_t * 2, arm_ext)  # top
                rgn |= QRegion(cx - half_t, cy + gap,           half_t * 2, arm_ext)  # bottom

            if has_dot:
                r = thick + outline + pad
                rgn |= QRegion(cx - r, cy - r, r * 2, r * 2,
                               QRegion.RegionType.Ellipse)

            if has_circle:
                outer_r = size + total_pen // 2 + pad
                inner_r = max(0, size - total_pen // 2 - pad)
                outer   = QRegion(cx - outer_r, cy - outer_r,
                                  outer_r * 2, outer_r * 2,
                                  QRegion.RegionType.Ellipse)
                if inner_r > 1:
                    inner = QRegion(cx - inner_r, cy - inner_r,
                                    inner_r * 2, inner_r * 2,
                                    QRegion.RegionType.Ellipse)
                    rgn = rgn.united(outer.subtracted(inner))
                else:
                    rgn |= outer

            # Module indicators (R, RF) drawn below crosshair center.
            has_r  = self._settings.get("recoil",    {}).get("enabled", False)
            has_rf = self._settings.get("rapidfire", {}).get("enabled", False)
            if has_r or has_rf:
                # "R" + "RF" with spacing fits in ~52x18 px at 8pt bold
                rgn |= QRegion(cx - 26, cy + 27, 52, 18)

        if self._siValue is not None:
            # SI text box is 36x24 centered near (sw//2 - 2%, sh//2 - 3%);
            # add 2px padding for the outline strokes
            sw, sh = _OVERLAY_SIZE, _OVERLAY_SIZE
            si_cx  = sw // 2 - int(sw * 0.02)
            si_cy  = sh // 2 - int(sh * 0.03)
            rgn |= QRegion(si_cx - 20, si_cy - 14, 42, 28)

        return rgn

    def _refreshMask(self):
        """Recompute and apply the window mask, showing or hiding as needed."""
        mask = self._computeMask()
        if mask.isEmpty():
            self.hide()
        else:
            self.setMask(mask)
            if not self.isVisible():
                self.show()
            self.update()

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
            self._refreshMask()

    # ------------------------------------------------------------------
    # Strength indicator
    # ------------------------------------------------------------------

    @Slot(int)
    def showStrengthIndicator(self, value: int):
        self._siValue = value
        self._siTimer.start(500)
        self._refreshMask()

    def _hideSI(self):
        self._siValue = None
        self._refreshMask()

    # ------------------------------------------------------------------
    # Paint
    # ------------------------------------------------------------------

    def paintEvent(self, event):
        if not self._chVisible and self._siValue is None:
            return
        painter = QPainter(self)
        # Fill black so the outline layer (also black) starts on a clean base.
        # The window mask limits visibility to only crosshair-shaped pixels,
        # so this fill is invisible outside those pixels.
        painter.fillRect(self.rect(), QColor(0, 0, 0))
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

        painter.setPen(QColor("#000000"))
        for dx, dy in ((-1, -1), (-1, 0), (-1, 1),
                       ( 0, -1),           ( 0, 1),
                       ( 1, -1), ( 1, 0), ( 1, 1)):
            painter.drawText(x + dx, y + dy, w, h,
                             Qt.AlignmentFlag.AlignCenter, text)

        painter.setPen(QColor("#ffffff"))
        painter.drawText(x, y, w, h, Qt.AlignmentFlag.AlignCenter, text)
