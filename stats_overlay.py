"""
StatsOverlayWindow — small always-on-top click-through corner overlay for hardware stats.

Transparency approach:
  WS_EX_LAYERED with LWA_COLORKEY is used for transparency, matching the crosshair
  overlay.  paintEvent fills the entire window with magenta first; DWM punches through
  any pixel that exactly matches the key color.  The rounded-rect background and text
  overwrite the magenta with opaque content.  No setMask / QRegion needed.

bg_alpha (0–100) controls background shade: 0 = subtle gray, 100 = solid black.
Text remains fully opaque regardless of bg_alpha.
"""
import ctypes

from PySide6.QtCore    import Qt, QRectF, QTimer, Slot
from PySide6.QtGui     import QColor, QFont, QPainter, QPainterPath
from PySide6.QtWidgets import QApplication, QWidget

from theme import TOPBAR_H, TOPBAR_MARGIN_TOP, TOPBAR_MARGIN_BOTTOM

_GWL_EXSTYLE       = -20
_WS_EX_LAYERED     = 0x00080000
_WS_EX_TRANSPARENT = 0x00000020
_WS_EX_NOACTIVATE  = 0x08000000
_LWA_COLORKEY      = 0x00000001

# Magenta as Win32 COLORREF (R=0xFF, G=0x00, B=0xFF → 0x00FF00FF)
# and as a QColor for paintEvent's background fill.
_COLORKEY     = 0x00FF00FF
_QCOLOR_KEY   = QColor(255, 0, 255)

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
        self._settings        = settings
        self._data: dict      = {}
        self._content_h: int  = 0
        self._prev_render_key = None

        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )

    def showEvent(self, event):
        super().showEvent(event)
        hwnd  = int(self.winId())
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
    # Visibility / resize / position
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
        if h != self._content_h:
            self._content_h = h
            self.setFixedSize(_WIN_W + _SHADOW_SIZE, h + _SHADOW_SIZE)

        self._reposition()

        st         = self._settings.get("stats", {})
        render_key = (lines, st.get("bg_alpha", 70), st.get("text_color", "#ffffff"))

        if not self.isVisible():
            self.show()
        elif render_key != self._prev_render_key:
            self.update()

        self._prev_render_key = render_key

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
        else:
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
        bg_alpha = st.get("bg_alpha", 70)
        fg_color = QColor(st.get("text_color", "#ffffff"))
        h        = _PAD_Y * 2 + len(lines) * _LINE_H

        shade        = int(136 * (1.0 - bg_alpha / 100.0))
        shadow_shade = max(0, shade - 24)
        bg_color     = QColor(shade, shade, shade)
        shadow_color = QColor(shadow_shade, shadow_shade, shadow_shade)

        dim_val = int(221 - 85 * (bg_alpha / 100.0))
        dim     = QColor(dim_val, dim_val, dim_val)

        painter = QPainter(self)

        # Fill entire window with the transparent color key.
        # All pixels not overwritten below will be punched through by DWM.
        painter.fillRect(self.rect(), _QCOLOR_KEY)

        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        # Drop shadow
        painter.fillRect(_SHADOW_SIZE, _SHADOW_SIZE, _WIN_W, h, shadow_color)

        # Rounded rect background
        bg_path = QPainterPath()
        bg_path.addRoundedRect(QRectF(0, 0, _WIN_W, h), _RADIUS, _RADIUS)
        painter.fillPath(bg_path, bg_color)

        # Text
        painter.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        fm = painter.fontMetrics()
        y  = _PAD_Y + fm.ascent()

        painter.setClipRect(0, 0, _WIN_W, h)

        for label, value in lines:
            lw = fm.horizontalAdvance(label + "  ")

            painter.setPen(dim)
            painter.drawText(_PAD_X, y, label)

            painter.setPen(fg_color)
            painter.drawText(_PAD_X + lw, y, value)

            y += _LINE_H

        painter.end()
