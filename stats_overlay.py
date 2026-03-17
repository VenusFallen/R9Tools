"""
StatsOverlayWindow — small always-on-top click-through corner overlay for hardware stats.
Uses WA_TranslucentBackground + WS_EX_TRANSPARENT for a composited rounded overlay.
"""
import ctypes

from PySide6.QtCore    import Qt, QRectF, Slot
from PySide6.QtGui     import QColor, QFont, QPainter, QPainterPath
from PySide6.QtWidgets import QApplication, QWidget

from theme import TOPBAR_H, TOPBAR_MARGIN_TOP, TOPBAR_MARGIN_BOTTOM

_GWL_EXSTYLE       = -20
_WS_EX_TRANSPARENT = 0x00000020

_MARGIN       = 12    # px gap from screen edge
_PAD_X        = 8     # horizontal text padding inside the window
_PAD_Y        = 6     # vertical text padding
_LINE_H       = 17    # px per text line
_WIN_W        = 210   # fixed content width
_RADIUS       = 8     # corner radius for rounded rect
_SHADOW_SIZE  = 4     # shadow offset (window extends right+bottom by this)
_TOPBAR_TOTAL = TOPBAR_MARGIN_TOP + TOPBAR_H + TOPBAR_MARGIN_BOTTOM


class StatsOverlayWindow(QWidget):

    def __init__(self, settings: dict):
        super().__init__()
        self._settings  = settings
        self._data: dict = {}
        self._content_h: int = 0

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )

    def showEvent(self, event):
        super().showEvent(event)
        hwnd  = int(self.winId())
        style = ctypes.windll.user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
        ctypes.windll.user32.SetWindowLongW(
            hwnd, _GWL_EXSTYLE, style | _WS_EX_TRANSPARENT)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @Slot(dict)
    def updateStats(self, data: dict):
        """Receive new stats from the poller (called via Qt signal)."""
        self._data = data
        self._refresh()

    def applySettings(self):
        """Call when stats settings change externally (corner, enabled, metrics)."""
        self._refresh()

    # ------------------------------------------------------------------
    # Line builder
    # ------------------------------------------------------------------

    def _visibleLines(self) -> list[tuple[str, str]]:
        """Returns (label, value_str) for each enabled metric that has data."""
        st  = self._settings.get("stats", {})
        d   = self._data
        out = []

        # CPU — combine usage + temp on one line when both enabled
        if st.get("show_cpu_usage") and "cpu_usage" in d:
            val = f"{d['cpu_usage']:.0f}%"
            if st.get("show_cpu_temp") and "cpu_temp" in d:
                val += f"  {d['cpu_temp']:.0f}°C"
            out.append(("CPU", val))
        elif st.get("show_cpu_temp") and "cpu_temp" in d:
            out.append(("CPU TEMP", f"{d['cpu_temp']:.0f}°C"))

        # GPU — combine usage + temp on one line when both enabled
        if st.get("show_gpu_usage") and "gpu_usage" in d:
            val = f"{d['gpu_usage']:.0f}%"
            if st.get("show_gpu_temp") and "gpu_temp" in d:
                val += f"  {d['gpu_temp']:.0f}°C"
            out.append(("GPU", val))
        elif st.get("show_gpu_temp") and "gpu_temp" in d:
            out.append(("GPU TEMP", f"{d['gpu_temp']:.0f}°C"))

        # VRAM
        if st.get("show_gpu_vram") and "gpu_vram_used" in d:
            if "gpu_vram_total" in d:
                out.append(("VRAM",
                    f"{d['gpu_vram_used']:.1f}/{d['gpu_vram_total']:.1f} GB"))
            else:
                out.append(("VRAM", f"{d['gpu_vram_used']:.1f} GB"))

        # RAM
        if st.get("show_ram") and "ram_used" in d:
            if "ram_total" in d:
                out.append(("RAM",
                    f"{d['ram_used']:.1f}/{d['ram_total']:.1f} GB"))
            else:
                out.append(("RAM", f"{d['ram_used']:.1f} GB"))

        return out

    # ------------------------------------------------------------------
    # Visibility / resize
    # ------------------------------------------------------------------

    def _refresh(self):
        if not self._settings.get("stats", {}).get("enabled", False):
            self.hide()
            return

        lines = self._visibleLines()
        if not lines:
            self.hide()
            return

        h = _PAD_Y * 2 + len(lines) * _LINE_H
        self._content_h = h
        self.setFixedSize(_WIN_W + _SHADOW_SIZE, h + _SHADOW_SIZE)
        self._reposition()
        if not self.isVisible():
            self.show()
        self.update()

    def _reposition(self):
        screen = QApplication.primaryScreen().geometry()
        corner = self._settings.get("stats", {}).get("corner", "top_right")
        h      = self._content_h

        x = (screen.x() + _MARGIN
             if "left" in corner
             else screen.x() + screen.width() - _WIN_W - _MARGIN)
        if "top" in corner:
            y = screen.y() + _TOPBAR_TOTAL + _MARGIN
        elif "bottom" in corner:
            y = screen.y() + screen.height() - h - _MARGIN
        else:  # middle_left / middle_right
            y = screen.y() + (screen.height() - h) // 2

        self.move(x, y)

    # ------------------------------------------------------------------
    # Paint
    # ------------------------------------------------------------------

    def paintEvent(self, event):
        lines = self._visibleLines()
        if not lines:
            return

        st       = self._settings.get("stats", {})
        bg_alpha = int(st.get("bg_alpha", 70) * 2.55)
        fg_color = QColor(st.get("text_color", "#ffffff"))
        h        = _PAD_Y * 2 + len(lines) * _LINE_H

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        # Drop shadow (offset by _SHADOW_SIZE, same radius, alpha scaled with bg)
        shadow_alpha = min(200, int(bg_alpha * 0.75))
        shadow_path  = QPainterPath()
        shadow_path.addRoundedRect(
            QRectF(_SHADOW_SIZE, _SHADOW_SIZE, _WIN_W, h), _RADIUS, _RADIUS)
        painter.fillPath(shadow_path, QColor(0, 0, 0, shadow_alpha))

        # Background rounded rect
        bg_path = QPainterPath()
        bg_path.addRoundedRect(QRectF(0, 0, _WIN_W, h), _RADIUS, _RADIUS)
        painter.fillPath(bg_path, QColor(0, 0, 0, bg_alpha))

        # Text (clipped to content area)
        painter.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        fm  = painter.fontMetrics()
        y   = _PAD_Y + fm.ascent()
        dim = QColor("#888888")

        painter.setClipRect(0, 0, _WIN_W, h)

        for label, value in lines:
            lw = fm.horizontalAdvance(label + "  ")

            painter.setPen(dim)
            painter.drawText(_PAD_X, y, label)

            painter.setPen(fg_color)
            painter.drawText(_PAD_X + lw, y, value)

            y += _LINE_H

        painter.end()
