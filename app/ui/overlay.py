import sys
import queue
import os
import math
import logging
from pathlib import Path
from typing import List, Dict, Tuple, Optional

from PyQt6.QtWidgets import QApplication, QWidget, QLabel, QHBoxLayout, QVBoxLayout, QFrame
from PyQt6.QtCore import Qt, QTimer, QPoint, QRect, pyqtProperty, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QPainter, QColor, QPen, QBrush, QPixmap, QFont, QLinearGradient

logger = logging.getLogger(__name__)

# Colors matching the Arium logo
COLOR_PURPLE = QColor(125, 95, 194)       # #7D5FC2
COLOR_PURPLE_GLOW = QColor(155, 130, 219)  # Light purple glow
COLOR_DEEP_BG = QColor(15, 12, 27, 220)    # Deep purple-black transparent bg
COLOR_TEXT_MAIN = QColor(230, 228, 240)    # Off-white
COLOR_TEXT_MUTED = QColor(176, 174, 198)   # Light greyish purple
COLOR_CYAN = QColor(0, 240, 255)           # Neon cyan for listening
COLOR_RED = QColor(255, 60, 100)           # Soft red for click/acting

class AnimationState:
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    ACTING = "acting"

class AriumOrb(QWidget):
    """
    Custom widget rendering the Arium logo with a dynamic neon glow ring around it.
    Glow size and style changes dynamically based on the current AI state.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(56, 56)
        
        # Load logo
        resources_dir = Path(__file__).parent.parent / "resources"
        logo_path = str(resources_dir / "Arium_logo.png")
        self.logo_pixmap = QPixmap(logo_path)
        if self.logo_pixmap.isNull():
            logger.warning(f"Could not load logo from {logo_path}")
            
        self.state = AnimationState.IDLE
        self.animation_phase = 0.0
        
        # Local animation timer (30ms per frame ~33 FPS)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._update_animation)
        self.timer.start(30)
        
    def set_state(self, state: str):
        self.state = state
        self.update()
        
    def _update_animation(self):
        self.animation_phase += 0.05
        if self.animation_phase > 2 * math.pi:
            self.animation_phase -= 2 * math.pi
        self.update()
        
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        cx, cy = self.width() / 2, self.height() / 2
        
        # Determine glow parameters based on state
        glow_color = COLOR_PURPLE
        pulse_amp = 3.0
        pulse_speed = 1.0
        
        if self.state == AnimationState.IDLE:
            glow_color = COLOR_PURPLE
            pulse_amp = 2.0
            pulse_speed = 1.0
        elif self.state == AnimationState.LISTENING:
            glow_color = COLOR_CYAN
            pulse_amp = 5.0
            pulse_speed = 2.0
        elif self.state == AnimationState.THINKING:
            glow_color = COLOR_PURPLE_GLOW
            pulse_amp = 4.0
            pulse_speed = 3.5
        elif self.state == AnimationState.SPEAKING:
            glow_color = COLOR_PURPLE
            pulse_amp = 6.0
            pulse_speed = 2.5
        elif self.state == AnimationState.ACTING:
            glow_color = COLOR_RED
            pulse_amp = 3.0
            pulse_speed = 4.0

        # Calculate current dynamic glow radius
        phase = self.animation_phase * pulse_speed
        dynamic_glow = pulse_amp * (1.0 + math.sin(phase)) / 2.0
        glow_radius = 21.0 + dynamic_glow
        
        # Draw dynamic outer glowing circle
        for r in range(4):
            opacity = int(100 * (1.0 - r / 4.0) * (0.5 + dynamic_glow / (2.0 * pulse_amp)))
            col = QColor(glow_color.red(), glow_color.green(), glow_color.blue(), opacity)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(col))
            painter.drawEllipse(QPoint(int(cx), int(cy)), int(glow_radius + r), int(glow_radius + r))
            
        # Draw logo pixmap in the center
        if not self.logo_pixmap.isNull():
            target_rect = QRect(10, 10, 36, 36)
            painter.drawPixmap(target_rect, self.logo_pixmap)
        else:
            # Fallback circle if logo is not found
            painter.setPen(QPen(COLOR_TEXT_MAIN, 2))
            painter.setBrush(QBrush(COLOR_PURPLE))
            painter.drawEllipse(QPoint(int(cx), int(cy)), 15, 15)


class StatusWaveWidget(QWidget):
    """
    Visual equalizer-like wave that reacts to state transitions (listening, speaking).
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(60, 30)
        self.state = AnimationState.IDLE
        self.phase = 0.0
        
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(30)
        
    def set_state(self, state: str):
        self.state = state
        self.update()
        
    def _tick(self):
        self.phase += 0.1
        self.update()
        
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        num_bars = 5
        bar_w = 4
        spacing = 6
        start_x = (self.width() - (num_bars * bar_w + (num_bars - 1) * spacing)) / 2
        
        for i in range(num_bars):
            # Base height depending on state
            if self.state == AnimationState.IDLE:
                h = 4
                color = COLOR_PURPLE
            elif self.state == AnimationState.LISTENING:
                h = 6 + int(12 * (1.0 + math.sin(self.phase + i * 0.8)) / 2.0)
                color = COLOR_CYAN
            elif self.state == AnimationState.THINKING:
                h = 6 + int(6 * (1.0 + math.sin(self.phase * 2.0 + i * 1.5)) / 2.0)
                color = COLOR_PURPLE_GLOW
            elif self.state == AnimationState.SPEAKING:
                h = 4 + int(18 * (1.0 + math.sin(self.phase * 1.5 + i * 1.0)) / 2.0)
                color = COLOR_PURPLE
            elif self.state == AnimationState.ACTING:
                h = 10 + int(4 * (1.0 + math.sin(self.phase * 3.0 + i * 2.0)) / 2.0)
                color = COLOR_RED
            else:
                h = 4
                color = COLOR_PURPLE
                
            x = int(start_x + i * (bar_w + spacing))
            y = int((self.height() - h) / 2)
            
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(color))
            painter.drawRoundedRect(QRect(x, y, bar_w, h), 2, 2)


class AriumStatusWidget(QWidget):
    """
    Floating movable panel showing the current state of Arium AI.
    Can be dragged around the screen.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(280, 76)
        
        # Drag state
        self.drag_position = QPoint()
        
        # Layout structure
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)
        
        # Inner styling frame
        self.frame = QFrame(self)
        self.frame.setObjectName("MainFrame")
        self.frame.setStyleSheet(f"""
            QFrame#MainFrame {{
                background-color: rgba(15, 12, 27, 210);
                border: 1.5px solid rgba(125, 95, 194, 150);
                border-radius: 16px;
            }}
        """)
        
        frame_layout = QHBoxLayout(self.frame)
        frame_layout.setContentsMargins(8, 8, 8, 8)
        frame_layout.setSpacing(12)
        
        # Left: Pulsing Orb
        self.orb = AriumOrb(self)
        frame_layout.addWidget(self.orb)
        
        # Center: Info Text
        text_layout = QVBoxLayout()
        text_layout.setSpacing(2)
        text_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        
        self.title_label = QLabel("Arium AI", self)
        self.title_label.setFont(QFont("Outfit", 11, QFont.Weight.Bold))
        self.title_label.setStyleSheet(f"color: rgb({COLOR_TEXT_MAIN.red()}, {COLOR_TEXT_MAIN.green()}, {COLOR_TEXT_MAIN.blue()}); background: transparent;")
        
        self.status_label = QLabel("В режиме ожидания", self)
        self.status_label.setFont(QFont("Inter", 9))
        self.status_label.setStyleSheet(f"color: rgb({COLOR_TEXT_MUTED.red()}, {COLOR_TEXT_MUTED.green()}, {COLOR_TEXT_MUTED.blue()}); background: transparent;")
        
        text_layout.addWidget(self.title_label)
        text_layout.addWidget(self.status_label)
        frame_layout.addLayout(text_layout)
        
        # Right: Wave visualizer
        self.wave = StatusWaveWidget(self)
        frame_layout.addWidget(self.wave)
        
        layout.addWidget(self.frame)
        
        # Position at bottom-right of the screen by default
        self.reset_position()
        
    def reset_position(self):
        screen = QApplication.primaryScreen().geometry()
        x = screen.width() - self.width() - 40
        y = screen.height() - self.height() - 60
        self.move(x, y)
        
    def update_state(self, state: str, text: str = ""):
        self.orb.set_state(state)
        self.wave.set_state(state)
        
        state_texts = {
            AnimationState.IDLE: "В режиме ожидания",
            AnimationState.LISTENING: "Слушаю вас...",
            AnimationState.THINKING: "Обработка запроса...",
            AnimationState.SPEAKING: "Озвучиваю ответ...",
            AnimationState.ACTING: "Выполнение действия..."
        }
        
        display_text = text if text else state_texts.get(state, "Готов")
        self.status_label.setText(display_text)
        
        # Update colors on status text
        if state == AnimationState.LISTENING:
            self.status_label.setStyleSheet(f"color: rgb({COLOR_CYAN.red()}, {COLOR_CYAN.green()}, {COLOR_CYAN.blue()}); background: transparent;")
        elif state == AnimationState.ACTING:
            self.status_label.setStyleSheet(f"color: rgb({COLOR_RED.red()}, {COLOR_RED.green()}, {COLOR_RED.blue()}); background: transparent;")
        else:
            self.status_label.setStyleSheet(f"color: rgb({COLOR_TEXT_MUTED.red()}, {COLOR_TEXT_MUTED.green()}, {COLOR_TEXT_MUTED.blue()}); background: transparent;")
            
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            # Handle PyQt6 globalPosition conversion
            self.drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self.drag_position)
            event.accept()


class OverlayCanvas(QWidget):
    """
    Transparent full-screen click-through window for drawing overlays on a single monitor.
    Handles click ripples, screenshot border glows, and zoom regions.
    """
    def __init__(self, screen_geometry: QRect, monitor_index: int):
        super().__init__()
        self.screen_geometry = screen_geometry
        self.monitor_index = monitor_index
        
        self.setGeometry(screen_geometry)
        
        # Transparent, frameless, stay on top, click-through
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.WindowTransparentForInput |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        
        # Animation properties
        self.ripple_pos: Optional[QPoint] = None
        self._ripple_radius = 0.0
        self._ripple_opacity = 0.0
        self._flash_opacity = 0.0
        
        # Zoom helper properties
        self.zoom_box: Optional[QRect] = None
        self._zoom_grid_opacity = 0.0
        self.zoom_cells: List[Tuple[QRect, str]] = [] # list of (rect, label)
        
        # Custom property animations
        self.ripple_radius_anim = QPropertyAnimation(self, b"rippleRadius")
        self.ripple_opacity_anim = QPropertyAnimation(self, b"rippleOpacity")
        self.flash_anim = QPropertyAnimation(self, b"flashOpacity")
        self.zoom_grid_anim = QPropertyAnimation(self, b"zoomGridOpacity")
        
    # Qt properties for clean animation curves
    @pyqtProperty(float)
    def rippleRadius(self) -> float:
        return self._ripple_radius
    @rippleRadius.setter
    def rippleRadius(self, val: float):
        self._ripple_radius = val
        self.update()
        
    @pyqtProperty(float)
    def rippleOpacity(self) -> float:
        return self._ripple_opacity
    @rippleOpacity.setter
    def rippleOpacity(self, val: float):
        self._ripple_opacity = val
        self.update()
        
    @pyqtProperty(float)
    def flashOpacity(self) -> float:
        return self._flash_opacity
    @flashOpacity.setter
    def flashOpacity(self, val: float):
        self._flash_opacity = val
        self.update()

    @pyqtProperty(float)
    def zoomGridOpacity(self) -> float:
        return self._zoom_grid_opacity
    @zoomGridOpacity.setter
    def zoomGridOpacity(self, val: float):
        self._zoom_grid_opacity = val
        self.update()

    def trigger_click(self, local_x: int, local_y: int):
        """Triggers a pulsing click ripple at target local coordinates."""
        self.ripple_pos = QPoint(local_x, local_y)
        
        # Ripple Radius animation
        self.ripple_radius_anim.stop()
        self.ripple_radius_anim.setDuration(400)
        self.ripple_radius_anim.setStartValue(5.0)
        self.ripple_radius_anim.setEndValue(45.0)
        self.ripple_radius_anim.setEasingCurve(QEasingCurve.Type.OutQuad)
        
        # Ripple Opacity animation
        self.ripple_opacity_anim.stop()
        self.ripple_opacity_anim.setDuration(400)
        self.ripple_opacity_anim.setStartValue(1.0)
        self.ripple_opacity_anim.setEndValue(0.0)
        self.ripple_opacity_anim.setEasingCurve(QEasingCurve.Type.OutQuad)
        
        self.ripple_radius_anim.start()
        self.ripple_opacity_anim.start()
        
    def trigger_screenshot_flash(self):
        """Triggers a glowing border screen flash."""
        self.flash_anim.stop()
        self.flash_anim.setDuration(350)
        self.flash_anim.setStartValue(1.0)
        self.flash_anim.setEndValue(0.0)
        self.flash_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.flash_anim.start()

    def trigger_zoom_preview(self, bounding_rect: QRect, cells_grid: List[Tuple[QRect, str]]):
        """Displays grid and zoom outline bounding box before execution."""
        self.zoom_box = bounding_rect
        self.zoom_cells = cells_grid
        
        self.zoom_grid_anim.stop()
        self.zoom_grid_anim.setDuration(800)
        self.zoom_grid_anim.setStartValue(1.0)
        self.zoom_grid_anim.setEndValue(0.0)
        self.zoom_grid_anim.setEasingCurve(QEasingCurve.Type.InExpo)
        self.zoom_grid_anim.start()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # 1. Screen Border Flash
        if self._flash_opacity > 0:
            border_col = QColor(COLOR_PURPLE.red(), COLOR_PURPLE.green(), COLOR_PURPLE.blue(), int(self._flash_opacity * 220))
            pen = QPen(border_col, 8)
            painter.setPen(pen)
            
            # Simple border
            painter.drawRect(self.rect().adjusted(4, 4, -4, -4))
            
            # Soft neon fill glow on corners
            glow_fill = QColor(COLOR_PURPLE.red(), COLOR_PURPLE.green(), COLOR_PURPLE.blue(), int(self._flash_opacity * 20))
            painter.fillRect(self.rect(), glow_fill)

        # 2. Click Ripple Effect
        if self.ripple_pos is not None and self._ripple_opacity > 0:
            opacity = int(self._ripple_opacity * 255)
            ripple_col = QColor(COLOR_PURPLE.red(), COLOR_PURPLE.green(), COLOR_PURPLE.blue(), opacity)
            
            # Outer ring
            painter.setPen(QPen(ripple_col, 3))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(self.ripple_pos, int(self._ripple_radius), int(self._ripple_radius))
            
            # Middle soft ring
            ripple_col_mid = QColor(COLOR_PURPLE.red(), COLOR_PURPLE.green(), COLOR_PURPLE.blue(), int(opacity * 0.4))
            painter.setPen(QPen(ripple_col_mid, 1))
            painter.drawEllipse(self.ripple_pos, int(self._ripple_radius * 0.6), int(self._ripple_radius * 0.6))
            
            # Glowing core
            core_opacity = int(self._ripple_opacity * 150)
            core_col = QColor(COLOR_RED.red(), COLOR_RED.green(), COLOR_RED.blue(), core_opacity)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(core_col))
            painter.drawEllipse(self.ripple_pos, 6, 6)

        # 3. Zoom Region & Cells Grid Helper
        if self.zoom_box is not None and self._zoom_grid_opacity > 0:
            opacity = int(self._zoom_grid_opacity * 200)
            
            # Draw bounding box
            box_col = QColor(COLOR_PURPLE.red(), COLOR_PURPLE.green(), COLOR_PURPLE.blue(), opacity)
            painter.setPen(QPen(box_col, 2, Qt.PenStyle.DashLine))
            painter.setBrush(QBrush(QColor(COLOR_PURPLE.red(), COLOR_PURPLE.green(), COLOR_PURPLE.blue(), int(self._zoom_grid_opacity * 30))))
            painter.drawRect(self.zoom_box)
            
            # Draw individual cell highlights and ID tags
            text_col = QColor(255, 255, 255, opacity)
            font = QFont("Inter", 10, QFont.Weight.Bold)
            painter.setFont(font)
            
            for cell_rect, cell_label in self.zoom_cells:
                # Highlight borders of targeted cells
                painter.setPen(QPen(QColor(COLOR_CYAN.red(), COLOR_CYAN.green(), COLOR_CYAN.blue(), int(opacity * 0.8)), 1.5))
                painter.drawRect(cell_rect)
                
                # Draw cell ID labels
                label_rect = QRect(cell_rect.x() + 4, cell_rect.y() + 4, 45, 18)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QBrush(QColor(0, 0, 0, int(opacity * 0.7))))
                painter.drawRoundedRect(label_rect, 4, 4)
                
                painter.setPen(QPen(text_col, 1))
                painter.drawText(label_rect, Qt.AlignmentFlag.AlignCenter, cell_label)


class UIController(QFrame):
    """
    Main manager orchestrating events between AriumEngine queue and PyQt widgets.
    """
    def __init__(self, event_queue: queue.Queue):
        super().__init__()
        self.event_queue = event_queue
        self.canvases: List[OverlayCanvas] = []
        
        # Query and create a canvas for each screen
        self._init_screens()
        
        # Create status panel widget
        self.status_widget = AriumStatusWidget()
        self.status_widget.show()
        
        # Event checking timer (30ms interval ~33 FPS)
        self.check_timer = QTimer(self)
        self.check_timer.timeout.connect(self._poll_events)
        self.check_timer.start(30)
        
    def _init_screens(self):
        screens = QApplication.screens()
        logger.info(f"[Overlay] Initializing canvas overlays for {len(screens)} screen(s)...")
        
        for idx, screen in enumerate(screens, start=1):
            geom = screen.geometry()
            canvas = OverlayCanvas(geom, idx)
            canvas.show()
            self.canvases.append(canvas)
            logger.info(f"  Canvas {idx} configured: size={geom.width()}x{geom.height()} at offset=({geom.x()},{geom.y()})")
            
    def _poll_events(self):
        while not self.event_queue.empty():
            try:
                event = self.event_queue.get_nowait()
                self._handle_event(event)
                self.event_queue.task_done()
            except queue.Empty:
                break
            except Exception as e:
                logger.error(f"[Overlay] Error polling queue: {e}", exc_info=True)
                
    def _handle_event(self, event: Dict):
        etype = event.get("type")
        logger.debug(f"[Overlay] UI event received: {etype}")
        
        if etype == "state":
            state = event.get("state", AnimationState.IDLE)
            text = event.get("text", "")
            self.status_widget.update_state(state, text)
            
        elif etype == "click":
            gx = event.get("x", 0)
            gy = event.get("y", 0)
            
            # Find the canvas representing the monitor where click occurred
            for canvas in self.canvases:
                if canvas.screen_geometry.contains(gx, gy):
                    # Convert to local coordinates of that screen
                    lx = gx - canvas.screen_geometry.x()
                    ly = gy - canvas.screen_geometry.y()
                    canvas.trigger_click(lx, ly)
                    break
                    
        elif etype == "screenshot":
            # Determine which monitor idx is active
            monitor_idx = event.get("monitor_index", 1)
            
            for canvas in self.canvases:
                if canvas.monitor_index == monitor_idx:
                    canvas.trigger_screenshot_flash()
                    break
                    
        elif etype == "zoom_preview":
            # Previews target grid cell regions
            monitor_idx = event.get("monitor_index", 1)
            cells_data = event.get("cells_data", [])
            region = event.get("region", {})
            
            # Bounding box relative to monitor local coords
            rx = region.get("x", 0)
            ry = region.get("y", 0)
            rw = region.get("w", 100)
            rh = region.get("h", 100)
            
            bound_rect = QRect(rx, ry, rw, rh)
            
            cells_grid = []
            for cell in cells_data:
                cx = cell.get("x", 0)
                cy = cell.get("y", 0)
                cw = cell.get("w", 10)
                ch = cell.get("h", 10)
                label = cell.get("label", "")
                cells_grid.append((QRect(cx, cy, cw, ch), label))
                
            for canvas in self.canvases:
                if canvas.monitor_index == monitor_idx:
                    canvas.trigger_zoom_preview(bound_rect, cells_grid)
                    break
        elif etype == "close":
            self.close_all()
            QApplication.quit()
                    
    def close_all(self):
        self.check_timer.stop()
        self.status_widget.close()
        for canvas in self.canvases:
            canvas.close()
        self.close()

def launch_overlay(event_queue: queue.Queue):
    """
    Main background thread entry point. Spins up PyQt6 event loop.
    """
    logger.info("[Overlay] Starting background UI thread...")
    
    # Ensure QApplication is initialized
    app = QApplication.instance()
    if not app:
        app = QApplication(sys.argv)
        
    controller = UIController(event_queue)
    
    # Run loop
    app.exec()
    
    logger.info("[Overlay] Background UI loop ended.")
