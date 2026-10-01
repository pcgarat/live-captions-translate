from __future__ import annotations

import html
import os
import queue
import subprocess
from collections.abc import Callable
from typing import Any

from PyQt6 import QtCore, QtGui, QtWidgets

from src.asr.languages import AVAILABLE_LANGUAGES, language_label
from src.asr.types import CaptionUpdate
from src.config import list_app_preset_ids
from src.debug.live_metrics import PERF, format_perf_chip
from src.debug.trace import debug_hud_enabled, debug_trace_enabled
from src.debug.vram import query_vram
from src.presets import app_preset_label
from src.ui.fonts import font_family_qss, font_weight_css

# Cola visible del overlay: suficiente para scrollear, sin crecer sin límite.
_MAX_DISPLAY_CHARS = 4000
# Viewport fijo: 3 líneas legibles con interlineado holgado.
_CAPTION_VISIBLE_LINES = 3
_CAPTION_LINE_GAP_PX = 12
_CAPTION_BLOCK_SPACING = 10
_CAPTION_LINE_HEIGHT = "1.45"
_RESIZE_MARGIN = 8
_MIN_WINDOW_WIDTH = 300
_MAX_WINDOW_WIDTH = 2400
_MIN_WINDOW_HEIGHT = 120
_MAX_WINDOW_HEIGHT = 1600
# Margen inferior en primer arranque / reclamp (no pegar al borde).
_BOTTOM_MARGIN_PX = 48
# Fila de controles (idioma, traducción, preset, ajustes, cerrar).
_CHROME_CONTROLS_HEIGHT = 44
# Evita parpadeo al cruzar widgets o al abrir un popup.
_CHROME_HIDE_DELAY_MS = 400
# Aparición/desaparición del hueco del chrome (captions no reflow).
_CHROME_ANIM_MS = 200

# (left, right, top, bottom)
ResizeEdge = tuple[bool, bool, bool, bool]


class _ChromeSlot(QtWidgets.QWidget):
    """Hueco animable: la sizeHint vertical sigue el alto fijado (puede ser 0)."""

    def sizeHint(self) -> QtCore.QSize:
        hint = super().sizeHint()
        return QtCore.QSize(hint.width(), self.height())

    def minimumSizeHint(self) -> QtCore.QSize:
        hint = super().minimumSizeHint()
        return QtCore.QSize(hint.width(), self.minimumHeight())


class SubtitleOverlay(QtWidgets.QWidget):
    def __init__(
        self,
        text_queue: queue.Queue[CaptionUpdate],
        config: dict[str, Any],
        on_open_settings: Callable[[], None] | None = None,
        on_close_app: Callable[[], None] | None = None,
        on_save_config: Callable[[dict[str, Any]], None] | None = None,
        on_restart_pipeline: Callable[[], None] | None = None,
        on_translation_changed: Callable[[], None] | None = None,
        on_apply_app_preset: Callable[[str | None], str | None] | None = None,
    ) -> None:
        super().__init__()
        self.text_queue = text_queue
        self.config = config
        self.on_open_settings = on_open_settings
        self.on_close_app = on_close_app
        self.on_save_config = on_save_config
        self.on_restart_pipeline = on_restart_pipeline
        self.on_translation_changed = on_translation_changed
        self.on_apply_app_preset = on_apply_app_preset
        self._loading_preset_selector = False
        self._final_text = ""
        self._partial_text = ""
        self._translated_text = ""
        self._phrase_final = ""
        self._phrase_translated = ""
        self._pending_new_phrase = False
        self._caption_seq = 0
        self._drag_offset: QtCore.QPoint | None = None
        self._resize_edge: ResizeEdge | None = None
        self._resize_start_pos: QtCore.QPoint | None = None
        self._resize_start_geom: QtCore.QRect | None = None
        self._always_on_top = bool(config.get("always_on_top", True))
        self._ignore_move_save = False
        self._save_pos_timer = QtCore.QTimer(self)
        self._save_pos_timer.setSingleShot(True)
        self._save_pos_timer.timeout.connect(self._persist_geometry)
        self._notice_timer = QtCore.QTimer(self)
        self._notice_timer.setSingleShot(True)
        self._notice_timer.timeout.connect(self._clear_notice)
        self._chrome_hide_timer = QtCore.QTimer(self)
        self._chrome_hide_timer.setSingleShot(True)
        self._chrome_hide_timer.timeout.connect(self._hide_chrome_bar)
        self._chrome_slot_px = 0
        self._chrome_target_visible = False
        self._chrome_anim = QtCore.QVariantAnimation(self)
        self._chrome_anim.setDuration(_CHROME_ANIM_MS)
        self._chrome_anim.setEasingCurve(QtCore.QEasingCurve.Type.InOutCubic)
        self._chrome_anim.valueChanged.connect(self._on_chrome_slot_value)
        self._build_ui()
        self._apply_style()
        self._build_context_menu()
        self._sync_translation_ui()

        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._poll_queue)
        self._timer.start(80)

        self._keep_above_timer = QtCore.QTimer(self)
        self._keep_above_timer.timeout.connect(self._ensure_on_top)
        self._keep_above_timer.start(1500)

        self._hud_timer = QtCore.QTimer(self)
        self._hud_timer.timeout.connect(self._refresh_debug_hud)
        self._sync_debug_hud()

    def _window_flags(self) -> QtCore.Qt.WindowType:
        flags = (
            QtCore.Qt.WindowType.Window
            | QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.CustomizeWindowHint
        )
        if self._always_on_top:
            flags |= QtCore.Qt.WindowType.WindowStaysOnTopHint
        return flags

    def _build_ui(self) -> None:
        self.setWindowTitle("Subtítulos en directo")
        self.setWindowFlags(self._window_flags())
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_Hover, True)
        self.setMouseTracking(True)
        self.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        self.panel = QtWidgets.QFrame()
        self.panel.setObjectName("captionPanel")
        self.panel.setMouseTracking(True)
        self.panel.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.panel.customContextMenuRequested.connect(
            lambda pos: self._show_context_menu(self.panel.mapToParent(pos))
        )
        panel_layout = QtWidgets.QVBoxLayout(self.panel)
        pad = int(self.config.get("padding", 24))
        panel_layout.setContentsMargins(pad, pad // 2, pad, pad // 2)
        # Sin spacing: el chrome colapsado a 0 no debe dejar un hueco fantasma de 6px.
        panel_layout.setSpacing(0)

        self.chrome_bar = _ChromeSlot()
        self.chrome_bar.setObjectName("chromeBar")
        self.chrome_bar.setMouseTracking(True)
        self.chrome_bar.setFixedHeight(0)
        self.chrome_bar.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Preferred,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        top = QtWidgets.QHBoxLayout(self.chrome_bar)
        top.setContentsMargins(6, 4, 6, 4)
        top.setSpacing(6)

        self.lang_label = QtWidgets.QLabel(
            str(self.config.get("language", "en")).upper()
        )
        self.lang_label.setObjectName("langLabel")
        self.lang_label.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.lang_label.setToolTip("Clic para cambiar idioma ASR")
        top.addWidget(self.lang_label)

        self.translate_btn = QtWidgets.QPushButton("ES")
        self.translate_btn.setObjectName("translateToggle")
        self.translate_btn.setCheckable(True)
        self.translate_btn.setChecked(
            bool(self.config.get("translation_enabled", False))
        )
        self.translate_btn.setFixedWidth(36)
        self.translate_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.translate_btn.setToolTip("Traducir a español (solo texto confirmado)")
        self.translate_btn.toggled.connect(self._on_translate_toggled)
        top.addWidget(self.translate_btn)

        self.preset_selector = QtWidgets.QComboBox()
        self.preset_selector.setObjectName("presetSelector")
        self.preset_selector.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.preset_selector.setToolTip("Preset general: se aplica al elegirlo")
        self.preset_selector.setSizeAdjustPolicy(
            QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToContents
        )
        self._refresh_preset_selector()
        self.preset_selector.currentIndexChanged.connect(self._on_preset_selected)
        top.addWidget(self.preset_selector)
        top.addStretch(1)

        self.settings_btn = QtWidgets.QPushButton("⚙")
        self.settings_btn.setFixedWidth(36)
        self.settings_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.settings_btn.clicked.connect(self._handle_settings)
        self.close_btn = QtWidgets.QPushButton("✕")
        self.close_btn.setFixedWidth(36)
        self.close_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.close_btn.clicked.connect(self._handle_close)
        top.addWidget(self.settings_btn)
        top.addWidget(self.close_btn)
        self._chrome_opacity = QtWidgets.QGraphicsOpacityEffect(self.chrome_bar)
        self._chrome_opacity.setOpacity(0.0)
        self.chrome_bar.setGraphicsEffect(self._chrome_opacity)
        self.chrome_bar.hide()
        panel_layout.addWidget(self.chrome_bar)

        self.debug_hud_label = QtWidgets.QLabel("")
        self.debug_hud_label.setObjectName("debugHud")
        self.debug_hud_label.setWordWrap(False)
        self.debug_hud_label.setTextInteractionFlags(
            QtCore.Qt.TextInteractionFlag.NoTextInteraction
        )
        self.debug_hud_label.setToolTip(
            "ASR = último infer · q = cola captions→UI · "
            "buf = audio en anillo · VRAM = GPU 0 (NVML)"
        )
        self.debug_hud_label.setVisible(False)
        panel_layout.addWidget(self.debug_hud_label)

        self.notice_label = QtWidgets.QLabel("")
        self.notice_label.setObjectName("noticeLabel")
        self.notice_label.setWordWrap(True)
        self.notice_label.setVisible(False)

        self._caption_scroll = QtWidgets.QScrollArea()
        self._caption_scroll.setObjectName("captionScroll")
        self._caption_scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self._caption_scroll.setWidgetResizable(False)
        self._caption_scroll.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._caption_scroll.setVerticalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._caption_scroll.setAlignment(QtCore.Qt.AlignmentFlag.AlignBottom)
        self._caption_scroll.viewport().setAutoFillBackground(False)

        self._caption_body = QtWidgets.QWidget()
        self._caption_body.setObjectName("captionBody")
        body_layout = QtWidgets.QVBoxLayout(self._caption_body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(_CAPTION_BLOCK_SPACING)

        self.translated_label = QtWidgets.QLabel("")
        self.translated_label.setWordWrap(True)
        self.translated_label.setObjectName("translatedCaption")

        self.final_label = QtWidgets.QLabel("")
        self.final_label.setWordWrap(True)
        self.final_label.setObjectName("finalCaption")

        self.partial_label = QtWidgets.QLabel("")
        self.partial_label.setWordWrap(True)
        self.partial_label.setObjectName("partialCaption")
        self._apply_text_align()

        body_layout.addWidget(self.translated_label)
        body_layout.addWidget(self.final_label)
        body_layout.addWidget(self.partial_label)
        self._caption_scroll.setWidget(self._caption_body)

        panel_layout.addWidget(self.notice_label)
        panel_layout.addWidget(self._caption_scroll, stretch=1)
        root.addWidget(self.panel)

        for widget in (
            self,
            self.panel,
            self.chrome_bar,
            self._caption_scroll,
            self._caption_body,
            self.lang_label,
            self.notice_label,
            self.translated_label,
            self.final_label,
            self.partial_label,
        ):
            widget.installEventFilter(self)
            widget.setMouseTracking(True)
            widget.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)

        self.lang_label.customContextMenuRequested.connect(
            lambda pos: self._show_context_menu(self.lang_label.mapTo(self, pos))
        )
        self.translated_label.customContextMenuRequested.connect(
            lambda pos: self._show_context_menu(self.translated_label.mapTo(self, pos))
        )
        self.final_label.customContextMenuRequested.connect(
            lambda pos: self._show_context_menu(self.final_label.mapTo(self, pos))
        )
        self.partial_label.customContextMenuRequested.connect(
            lambda pos: self._show_context_menu(self.partial_label.mapTo(self, pos))
        )

        width = self._clamp_window_width(int(self.config.get("window_width", 900)))
        self.resize(width, self._window_height())
        self._restore_geometry()
        self._apply_caption_geometry()

    def _saved_position(self) -> list[int] | None:
        pos = self.config.get("window_pos")
        if isinstance(pos, list) and len(pos) == 2:
            try:
                return [int(pos[0]), int(pos[1])]
            except (TypeError, ValueError):
                return None
        return None

    def _default_bottom_center(self) -> tuple[int, int] | None:
        """Abajo-centro del monitor primario con margen inferior."""
        screen = QtGui.QGuiApplication.primaryScreen()
        if screen is None:
            return None
        geo = screen.availableGeometry()
        x = geo.x() + (geo.width() - self.width()) // 2
        y = geo.bottom() - self.height() - _BOTTOM_MARGIN_PX
        y = max(geo.top(), y)
        return x, y

    def _clamp_to_screens(self, x: int, y: int) -> tuple[int, int]:
        screens = QtGui.QGuiApplication.screens()
        if not screens:
            return x, y
        point = QtCore.QPoint(x, y)
        for screen in screens:
            if screen.geometry().contains(point):
                return x, y
        # Si quedó fuera (cambio de monitor), ancla abajo-centro del primario.
        fallback = self._default_bottom_center()
        return fallback if fallback is not None else (x, y)

    def _restore_geometry(self) -> None:
        """Restaura tamaño + posición guardados (el WM puede resetear al show/flags)."""
        width = self._clamp_window_width(int(self.config.get("window_width", 900)))
        height = self._window_height()
        self._ignore_move_save = True
        try:
            if self.width() != width or self.height() != height:
                self.resize(width, height)
            saved = self._saved_position()
            if saved is not None:
                x, y = self._clamp_to_screens(saved[0], saved[1])
                self.move(x, y)
            else:
                fallback = self._default_bottom_center()
                if fallback is not None:
                    self.move(*fallback)
        finally:
            self._ignore_move_save = False

    def _restore_position(self) -> None:
        self._restore_geometry()

    def _base_window_height(self) -> int:
        """Altura persistida: sin el hueco temporal del chrome."""
        return max(_MIN_WINDOW_HEIGHT, self.height() - self._chrome_slot_px)

    def _schedule_persist_geometry(self) -> None:
        if self._ignore_move_save or not self.isVisible():
            return
        self.config["window_pos"] = [self.x(), self.y()]
        self.config["window_width"] = self.width()
        self.config["window_height"] = self._base_window_height()
        self._save_pos_timer.start(250)

    def _persist_geometry(self) -> None:
        self.config["window_pos"] = [self.x(), self.y()]
        self.config["window_width"] = self.width()
        self.config["window_height"] = self._base_window_height()
        if self.on_save_config is not None:
            self.on_save_config(self.config)

    def _reapply_flags(self, *, show_again: bool) -> None:
        """setWindowFlags recrea la ventana nativa y pierde geometría si no se restaura."""
        was_chrome = self._chrome_target_visible
        if self.isVisible():
            self.config["window_pos"] = [self.x(), self.y()]
            self.config["window_width"] = self.width()
            self.config["window_height"] = self._base_window_height()
        self._ignore_move_save = True
        try:
            self.setWindowFlags(self._window_flags())
            self._chrome_slot_px = 0
            self._chrome_target_visible = False
            self.chrome_bar.setFixedHeight(0)
            self._chrome_opacity.setOpacity(0.0)
            self._restore_geometry()
            if show_again:
                self.show()
        finally:
            self._ignore_move_save = False
        if was_chrome:
            self._reveal_chrome()

    def _build_context_menu(self) -> None:
        self._menu = QtWidgets.QMenu(self)
        self._menu.setStyleSheet(
            """
            QMenu {
                background: #2b2b2b;
                color: #f0f0f0;
                border: 1px solid #555;
                padding: 4px;
            }
            QMenu::item {
                padding: 6px 28px 6px 12px;
            }
            QMenu::item:selected {
                background: #3584e4;
            }
            QMenu::separator {
                height: 1px;
                background: #555;
                margin: 4px 8px;
            }
            """
        )

        self._act_hide = self._menu.addAction("Ocultar")
        self._act_hide.triggered.connect(self.hide)

        self._act_above = self._menu.addAction("Siempre encima")
        self._act_above.setCheckable(True)
        self._act_above.setChecked(self._always_on_top)
        self._act_above.toggled.connect(self.set_always_on_top)

        self._menu.addSeparator()
        self._act_debug_hud = self._menu.addAction("HUD debug")
        self._act_debug_hud.setCheckable(True)
        self._act_debug_hud.setChecked(debug_hud_enabled(self.config))
        self._act_debug_hud.toggled.connect(self._on_debug_hud_toggled)

        self._menu.addSeparator()
        self._act_settings = self._menu.addAction("Configuración…")
        self._act_settings.triggered.connect(self._handle_settings)
        self._act_close = self._menu.addAction("Cerrar")
        self._act_close.triggered.connect(self._handle_close)

    def _show_context_menu(self, pos: QtCore.QPoint) -> None:
        self._reveal_chrome()
        self._act_above.blockSignals(True)
        self._act_above.setChecked(self._always_on_top)
        self._act_above.blockSignals(False)
        forced = debug_trace_enabled() or bool(
            (os.environ.get("WLCL_DEBUG_HUD") or "").strip()
        )
        self._act_debug_hud.blockSignals(True)
        self._act_debug_hud.setChecked(debug_hud_enabled(self.config))
        self._act_debug_hud.setEnabled(not forced)
        self._act_debug_hud.setToolTip(
            "Activo por WLCL_DEBUG_TRACE / WLCL_DEBUG_HUD"
            if forced
            else "Chip VRAM + latencia ASR (solo diagnóstico)"
        )
        self._act_debug_hud.blockSignals(False)
        try:
            self._menu.exec(self.mapToGlobal(pos))
        finally:
            if not self.underMouse():
                self._schedule_hide_chrome()

    def _reveal_chrome(self) -> None:
        self._chrome_hide_timer.stop()
        if self._chrome_target_visible:
            return
        self._chrome_target_visible = True
        self._animate_chrome_slot(_CHROME_CONTROLS_HEIGHT)

    def _schedule_hide_chrome(self) -> None:
        self._chrome_hide_timer.start(_CHROME_HIDE_DELAY_MS)

    def _should_keep_chrome(self) -> bool:
        if self.underMouse():
            return True
        if self._menu.isVisible():
            return True
        popup = QtWidgets.QApplication.activePopupWidget()
        return popup is not None

    def _hide_chrome_bar(self) -> None:
        if self._should_keep_chrome():
            self._chrome_hide_timer.start(_CHROME_HIDE_DELAY_MS)
            return
        if not self._chrome_target_visible and self._chrome_slot_px == 0:
            return
        self._chrome_target_visible = False
        self._animate_chrome_slot(0)

    def _on_chrome_slot_value(self, value: object) -> None:
        new = int(value)  # type: ignore[arg-type]
        old = self._chrome_slot_px
        if new == old:
            return
        delta = new - old
        self._chrome_slot_px = new
        if new > 0 and self.chrome_bar.isHidden():
            self.chrome_bar.show()
        self.chrome_bar.setFixedHeight(new)
        t = new / _CHROME_CONTROLS_HEIGHT if _CHROME_CONTROLS_HEIGHT else 0.0
        self._chrome_opacity.setOpacity(t)
        self._grow_window_for_chrome(delta)
        if new == 0:
            self.chrome_bar.hide()

    def _animate_chrome_slot(self, target: int) -> None:
        target = max(0, min(int(target), _CHROME_CONTROLS_HEIGHT))
        if (
            self._chrome_anim.state() == QtCore.QAbstractAnimation.State.Running
            and int(self._chrome_anim.endValue() or -1) == target
        ):
            return
        if target > 0:
            self.chrome_bar.show()
        self._chrome_anim.stop()
        self._chrome_anim.setStartValue(self._chrome_slot_px)
        self._chrome_anim.setEndValue(target)
        self._chrome_anim.start()

    def _grow_window_for_chrome(self, delta: int) -> None:
        """Crece/encoge hacia arriba para que el área de captions no cambie de tamaño."""
        if delta == 0:
            return
        self._ignore_move_save = True
        try:
            geo = self.geometry()
            # Borde inferior fijo: los subtítulos no se mueven en pantalla.
            new_height = max(1, geo.height() + delta)
            new_top = geo.y() - delta
            self.setGeometry(geo.x(), new_top, geo.width(), new_height)
        finally:
            self._ignore_move_save = False
        self._apply_caption_geometry()

    def enterEvent(self, event: QtGui.QEnterEvent) -> None:
        self._reveal_chrome()
        super().enterEvent(event)

    def leaveEvent(self, event: QtCore.QEvent) -> None:
        self._schedule_hide_chrome()
        super().leaveEvent(event)

    def _on_debug_hud_toggled(self, enabled: bool) -> None:
        self.config["debug_hud"] = bool(enabled)
        if self.on_save_config:
            self.on_save_config(self.config)
        self._sync_debug_hud()

    def _sync_debug_hud(self) -> None:
        on = debug_hud_enabled(self.config)
        self.debug_hud_label.setVisible(on)
        if on:
            if not self._hud_timer.isActive():
                self._hud_timer.start(1000)
            self._refresh_debug_hud()
        else:
            self._hud_timer.stop()
            self.debug_hud_label.setText("")

    def _refresh_debug_hud(self) -> None:
        if not debug_hud_enabled(self.config):
            return
        snap = PERF.snapshot()
        vram = query_vram()
        text = format_perf_chip(
            snap,
            vram_used_mb=vram.used_mb if vram else None,
            vram_total_mb=vram.total_mb if vram else None,
        )
        self.debug_hud_label.setText(text)

    def set_always_on_top(self, enabled: bool) -> None:
        self._always_on_top = bool(enabled)
        self.config["always_on_top"] = self._always_on_top
        self._reapply_flags(show_again=True)
        self._ensure_on_top(force_restack=True)
        self._persist_geometry()

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        super().showEvent(event)
        self._restore_geometry()
        QtCore.QTimer.singleShot(0, self._restore_geometry)
        QtCore.QTimer.singleShot(50, self._restore_geometry)
        QtCore.QTimer.singleShot(
            0, lambda: self._ensure_on_top(force_restack=True)
        )
        QtCore.QTimer.singleShot(
            200, lambda: self._ensure_on_top(force_restack=True)
        )

    def moveEvent(self, event: QtGui.QMoveEvent) -> None:
        super().moveEvent(event)
        self._schedule_persist_geometry()

    def _ensure_on_top(self, *, force_restack: bool = False) -> None:
        """Mantiene always-on-top sin restackear en cada tick del timer.

        El timer solo repara si se perdió WindowStaysOnTopHint. raise_/wmctrl
        en cada 1.5 s recomponen la ventana translúcida y provocan parpadeo.
        """
        if not self.isVisible() or not self._always_on_top:
            return
        flags = self.windowFlags()
        hint_missing = not bool(flags & QtCore.Qt.WindowType.WindowStaysOnTopHint)
        if hint_missing:
            self._reapply_flags(show_again=True)
            force_restack = True
        if not force_restack:
            return
        self.raise_()
        self._apply_x11_above(True)

    def _apply_x11_above(self, enabled: bool) -> None:
        """Refuerza _NET_WM_STATE_ABOVE en XWayland/X11 (como el menú de GNOME)."""
        if QtGui.QGuiApplication.platformName() != "xcb":
            return
        wid = int(self.winId())
        if wid <= 0:
            return
        action = "add" if enabled else "remove"
        # wmctrl es opcional; si no está, el hint de Qt suele bastar bajo xcb.
        try:
            subprocess.run(
                ["wmctrl", "-i", "-r", hex(wid), "-b", f"{action},above"],
                check=False,
                capture_output=True,
                timeout=1.5,
            )
        except (FileNotFoundError, subprocess.SubprocessError):
            pass

    def _caption_h_align(self) -> QtCore.Qt.AlignmentFlag:
        align = str(self.config.get("text_align") or "center").strip().lower()
        if align == "left":
            return QtCore.Qt.AlignmentFlag.AlignLeft
        return QtCore.Qt.AlignmentFlag.AlignHCenter

    def _apply_text_align(self) -> None:
        h = self._caption_h_align()
        self.notice_label.setAlignment(h)
        bottom = h | QtCore.Qt.AlignmentFlag.AlignBottom
        self.translated_label.setAlignment(bottom)
        self.final_label.setAlignment(bottom)
        self.partial_label.setAlignment(bottom)

    def _line_slot_height(self) -> int:
        font_size = int(self.config.get("font_size", 28))
        return font_size + _CAPTION_LINE_GAP_PX

    def _chrome_height(self) -> int:
        """Padding + hueco actual del chrome (animado) + notice."""
        pad = int(self.config.get("padding", 24))
        extra = pad + self._chrome_slot_px
        if self.notice_label.isVisible():
            extra += max(self.notice_label.sizeHint().height(), 18)
        return extra

    def _default_window_height(self) -> int:
        return self._chrome_height() + max(
            _CAPTION_VISIBLE_LINES * self._line_slot_height(), 96
        )

    def _min_window_height(self) -> int:
        return max(
            _MIN_WINDOW_HEIGHT,
            self._chrome_height() + self._line_slot_height(),
        )

    def _window_height(self) -> int:
        saved = self.config.get("window_height")
        if isinstance(saved, (int, float)):
            return int(
                max(
                    _MIN_WINDOW_HEIGHT,
                    min(int(saved), _MAX_WINDOW_HEIGHT),
                )
            )
        return self._default_window_height()

    def _caption_viewport_height(self) -> int:
        if self.height() > 0:
            available = self.height() - self._chrome_height()
            return max(available, self._line_slot_height())
        return max(_CAPTION_VISIBLE_LINES * self._line_slot_height(), 96)

    @staticmethod
    def _clamp_window_width(width: int) -> int:
        return max(_MIN_WINDOW_WIDTH, min(width, _MAX_WINDOW_WIDTH))

    def _event_window_pos(
        self, obj: QtCore.QObject, event: QtGui.QMouseEvent
    ) -> QtCore.QPoint:
        pos = event.position().toPoint()
        if obj is self:
            return pos
        if isinstance(obj, QtWidgets.QWidget):
            return obj.mapTo(self, pos)
        return pos

    def _hit_test_resize(self, window_pos: QtCore.QPoint) -> ResizeEdge | None:
        margin = _RESIZE_MARGIN
        left = window_pos.x() <= margin
        right = window_pos.x() >= self.width() - margin
        top = window_pos.y() <= margin
        bottom = window_pos.y() >= self.height() - margin
        if not (left or right or top or bottom):
            return None
        return (left, right, top, bottom)

    @staticmethod
    def _cursor_for_edge(edge: ResizeEdge) -> QtCore.Qt.CursorShape:
        left, right, top, bottom = edge
        if (left and top) or (right and bottom):
            return QtCore.Qt.CursorShape.SizeFDiagCursor
        if (right and top) or (left and bottom):
            return QtCore.Qt.CursorShape.SizeBDiagCursor
        if left or right:
            return QtCore.Qt.CursorShape.SizeHorCursor
        return QtCore.Qt.CursorShape.SizeVerCursor

    def _update_hover_cursor(self, window_pos: QtCore.QPoint) -> None:
        if self._resize_edge is not None or self._drag_offset is not None:
            return
        edge = self._hit_test_resize(window_pos)
        cursor = (
            self._cursor_for_edge(edge)
            if edge is not None
            else QtCore.Qt.CursorShape.SizeAllCursor
        )
        self.setCursor(cursor)
        self.panel.setCursor(cursor)

    def _begin_resize(self, edge: ResizeEdge, global_pos: QtCore.QPoint) -> None:
        self._resize_edge = edge
        self._resize_start_pos = global_pos
        self._resize_start_geom = self.geometry()
        self.setCursor(self._cursor_for_edge(edge))
        self.panel.setCursor(self._cursor_for_edge(edge))

    def _continue_resize(self, global_pos: QtCore.QPoint) -> None:
        if (
            self._resize_edge is None
            or self._resize_start_pos is None
            or self._resize_start_geom is None
        ):
            return
        delta = global_pos - self._resize_start_pos
        geom = QtCore.QRect(self._resize_start_geom)
        left, right, top, bottom = self._resize_edge
        min_w = _MIN_WINDOW_WIDTH
        min_h = self._min_window_height()

        if left:
            new_left = geom.left() + delta.x()
            new_width = geom.right() - new_left + 1
            if new_width >= min_w:
                geom.setLeft(new_left)
        if right:
            new_width = geom.width() + delta.x()
            if new_width >= min_w:
                geom.setWidth(new_width)
        if top:
            new_top = geom.top() + delta.y()
            new_height = geom.bottom() - new_top + 1
            if new_height >= min_h:
                geom.setTop(new_top)
        if bottom:
            new_height = geom.height() + delta.y()
            if new_height >= min_h:
                geom.setHeight(new_height)

        geom.setWidth(self._clamp_window_width(geom.width()))
        geom.setHeight(
            max(min_h, min(geom.height(), _MAX_WINDOW_HEIGHT)),
        )
        self.setGeometry(geom)

    def _set_caption_label(self, label: QtWidgets.QLabel, text: str) -> bool:
        """Actualiza el label solo si el texto plano cambió. True si hubo cambio."""
        plain = text.strip() if text else ""
        if label.property("captionPlain") == plain:
            return False
        label.setProperty("captionPlain", plain)
        if not plain:
            label.setTextFormat(QtCore.Qt.TextFormat.PlainText)
            label.setText("")
            return True
        escaped = html.escape(plain).replace("\n", "<br/>")
        label.setTextFormat(QtCore.Qt.TextFormat.RichText)
        label.setText(
            f'<div style="line-height:{_CAPTION_LINE_HEIGHT};">{escaped}</div>'
        )
        return True

    def _apply_caption_geometry(self) -> None:
        self._caption_scroll.setFixedHeight(self._caption_viewport_height())
        self._sync_caption_body_size()
        self._scroll_captions_to_bottom()

    def _sync_caption_body_size(self) -> bool:
        """Ajusta anchos/alturas del cuerpo. True si cambió la geometría."""
        viewport = self._caption_scroll.viewport()
        width = max(viewport.width(), self.width() - 48, 80)
        changed = False
        if self._caption_body.width() != width:
            self._caption_body.setFixedWidth(width)
            changed = True
        total = 0
        visible = 0
        for label in (
            self.translated_label,
            self.final_label,
            self.partial_label,
        ):
            if label.width() != width:
                label.setFixedWidth(width)
                changed = True
            if label.isHidden() or not label.text():
                if label.height() != 0:
                    label.setFixedHeight(0)
                    changed = True
                continue
            height = max(label.heightForWidth(width), label.fontMetrics().height())
            if label.height() != height:
                label.setFixedHeight(height)
                changed = True
            total += height
            visible += 1
        if visible:
            total += _CAPTION_BLOCK_SPACING * (visible - 1)
        body_h = max(total, 1)
        if self._caption_body.width() != width or self._caption_body.height() != body_h:
            self._caption_body.resize(width, body_h)
            changed = True
        return changed

    def _scroll_captions_to_bottom(self) -> None:
        bar = self._caption_scroll.verticalScrollBar()
        if bar.value() != bar.maximum():
            bar.setValue(bar.maximum())

    def _trim_display(self, text: str) -> str:
        if len(text) <= _MAX_DISPLAY_CHARS:
            return text
        cut = text[-_MAX_DISPLAY_CHARS:]
        space = cut.find(" ")
        return cut[space + 1 :].lstrip() if space >= 0 else cut

    def _apply_style(self) -> None:
        font_size = int(self.config.get("font_size", 28))
        family_qss = font_family_qss(str(self.config.get("font_family", "")))
        weight = font_weight_css(str(self.config.get("font_weight", "semibold")))
        font_color = str(self.config.get("font_color", "#ffffff"))
        bg = str(self.config.get("bg_color", "#000000"))
        alpha = float(self.config.get("bg_alpha", 0.55))
        color = QtGui.QColor(bg)
        color.setAlphaF(alpha)
        rgba = f"rgba({color.red()}, {color.green()}, {color.blue()}, {color.alphaF():.2f})"
        partial = QtGui.QColor(font_color)
        partial.setAlphaF(0.55)
        partial_rgba = f"rgba({partial.red()}, {partial.green()}, {partial.blue()}, {partial.alphaF():.2f})"

        asr_size = max(12, font_size - 4) if self._translation_active() else font_size
        self.setStyleSheet(
            f"""
            QFrame#captionPanel {{
                background: {rgba};
                border-radius: 12px;
            }}
            QScrollArea#captionScroll {{
                background: transparent;
                border: none;
            }}
            QWidget#captionBody {{
                background: transparent;
            }}
            QLabel#translatedCaption {{
                color: {font_color};
                font-size: {font_size}px;
                {family_qss}
                font-weight: {weight};
            }}
            QLabel#finalCaption {{
                color: {font_color};
                font-size: {asr_size}px;
                {family_qss}
                font-weight: {weight};
            }}
            QLabel#partialCaption {{
                color: {partial_rgba};
                font-size: {max(12, asr_size - 6)}px;
                {family_qss}
                font-style: italic;
            }}
            QLabel#langLabel {{
                color: {partial_rgba};
                font-size: 12px;
            }}
            QLabel#noticeLabel {{
                color: #ffd27a;
                font-size: 12px;
                font-weight: 600;
            }}
            QLabel#debugHud {{
                color: rgba(180, 220, 180, 0.75);
                font-size: 11px;
                font-family: monospace;
            }}
            QWidget#chromeBar {{
                background: transparent;
                border-radius: 8px;
            }}
            QPushButton#translateToggle {{
                background: transparent;
                color: {partial_rgba};
                border: 1px solid {partial_rgba};
                border-radius: 4px;
                font-size: 12px;
                font-weight: 600;
                padding: 2px 4px;
            }}
            QPushButton#translateToggle:checked {{
                color: {font_color};
                border-color: {font_color};
                background: rgba(255, 255, 255, 0.12);
            }}
            QComboBox#presetSelector {{
                background: transparent;
                color: {partial_rgba};
                border: 1px solid {partial_rgba};
                border-radius: 4px;
                font-size: 12px;
                font-weight: 600;
                padding: 2px 18px 2px 6px;
            }}
            QComboBox#presetSelector::drop-down {{
                border: none;
                width: 16px;
            }}
            QComboBox#presetSelector QAbstractItemView {{
                background: #2b2b2b;
                color: #f0f0f0;
                selection-background-color: #3584e4;
                border: 1px solid #555;
            }}
            QPushButton {{
                background: transparent;
                color: {font_color};
                border: none;
                font-size: 16px;
            }}
            QPushButton:hover {{
                color: #ffd27a;
            }}
            """
        )

    def apply_config(self, config: dict[str, Any]) -> None:
        self.config = config
        pad = int(config.get("padding", 24))
        layout = self.panel.layout()
        if layout is not None:
            layout.setContentsMargins(pad, pad // 2, pad, pad // 2)
        self.lang_label.setText(str(config.get("language", "en")).upper())
        self.translate_btn.blockSignals(True)
        self.translate_btn.setChecked(bool(config.get("translation_enabled", False)))
        self.translate_btn.blockSignals(False)
        self._refresh_preset_selector()
        if not self._show_partials():
            self._partial_text = ""
        self.resize(
            self._clamp_window_width(int(config.get("window_width", 900))),
            self._window_height(),
        )
        self._apply_text_align()
        self._apply_style()
        self._sync_translation_ui()
        self._sync_debug_hud()
        self._apply_caption_geometry()
        desired = bool(config.get("always_on_top", True))
        if desired != self._always_on_top:
            self.set_always_on_top(desired)
        else:
            self._ensure_on_top(force_restack=True)

    def reset_caption_stream(self) -> None:
        """Alinea el overlay con un pipeline ASR nuevo (seq reiniciado en 0)."""
        self._caption_seq = 0
        self._partial_text = ""
        self._phrase_final = ""
        self._phrase_translated = ""
        self._pending_new_phrase = True

    def _show_partials(self) -> bool:
        return bool(self.config.get("captions_show_partials", False))

    def _allow_rewrite(self) -> bool:
        return bool(self.config.get("captions_allow_rewrite", True))

    def _translation_active(self) -> bool:
        if not bool(self.config.get("translation_enabled", False)):
            return False
        lang = str(self.config.get("language", "en")).strip().lower()
        target = (
            str(self.config.get("translation_target") or "es").strip().lower() or "es"
        )
        return lang != target

    def _second_line_mode(self) -> str:
        mode = str(self.config.get("second_line_mode", "live_asr")).strip().lower()
        if mode not in ("live_asr", "original", "none"):
            return "live_asr"
        return mode

    def _second_line_display_text(self) -> str:
        mode = self._second_line_mode()
        if mode == "live_asr":
            if not self._show_partials():
                return self._final_text
            parts = [part for part in (self._final_text, self._partial_text) if part]
            return " ".join(parts).strip()
        if mode == "original":
            return self._final_text
        # none: sin traducción sigue mostrando el ASR confirmado; con TX oculta la 2ª línea.
        if self._translation_active():
            return ""
        return self._final_text

    def _sync_translation_ui(self) -> None:
        translation_on = self._translation_active()
        mode = self._second_line_mode()
        self.translated_label.setVisible(translation_on)
        if not translation_on:
            self._translated_text = ""
            self.translated_label.setText("")
        if translation_on:
            self.final_label.setVisible(mode != "none")
        else:
            self.final_label.setVisible(True)
        self.partial_label.setVisible(False)
        self._refresh_caption_texts()
        self._apply_style()
        # Solo crecer al default si el usuario aún no eligió altura (None).
        if self.config.get("window_height") is None:
            needed = max(self.height(), self._default_window_height())
            if self.height() < needed:
                self.resize(self.width(), needed)
        self._apply_caption_geometry()

    def _refresh_caption_texts(self) -> None:
        """Pinta captions sin frames intermedios (evita parpadeo en rewrites)."""
        translation_on = self._translation_active()
        second_text = self._second_line_display_text()
        # Un solo paint al final: setText+resize intermedios flashaban el panel.
        self.setUpdatesEnabled(False)
        self._caption_scroll.setUpdatesEnabled(False)
        try:
            text_changed = False
            text_changed |= self._set_caption_label(
                self.translated_label,
                self._translated_text if translation_on else "",
            )
            text_changed |= self._set_caption_label(self.final_label, second_text)
            text_changed |= self._set_caption_label(self.partial_label, "")
            geom_changed = self._sync_caption_body_size()
            if text_changed or geom_changed:
                self._scroll_captions_to_bottom()
        finally:
            self._caption_scroll.setUpdatesEnabled(True)
            self.setUpdatesEnabled(True)

    def _on_translate_toggled(self, enabled: bool) -> None:
        self.config["translation_enabled"] = bool(enabled)
        if not enabled:
            self._translated_text = ""
            self._phrase_translated = ""
            self._caption_seq = 0
        self._sync_translation_ui()
        if self.on_save_config is not None:
            self.on_save_config(self.config)
        if self.on_translation_changed is not None:
            self.on_translation_changed()

    def _show_language_menu(self) -> None:
        self._reveal_chrome()
        menu = QtWidgets.QMenu(self)
        for lang in AVAILABLE_LANGUAGES:
            action = menu.addAction(language_label(lang))
            action.triggered.connect(
                lambda _checked=False, c=lang: self._set_language(c)
            )
        try:
            menu.exec(self.lang_label.mapToGlobal(self.lang_label.rect().bottomLeft()))
        finally:
            if not self.underMouse():
                self._schedule_hide_chrome()

    def _refresh_preset_selector(self) -> None:
        self._loading_preset_selector = True
        try:
            current = self.config.get("app_preset")
            self.preset_selector.clear()
            self.preset_selector.addItem("(ninguno)", None)
            for preset_id in list_app_preset_ids(self.config):
                self.preset_selector.addItem(app_preset_label(preset_id), preset_id)
            idx = self.preset_selector.findData(current) if current else -1
            self.preset_selector.setCurrentIndex(idx if idx >= 0 else 0)
        finally:
            self._loading_preset_selector = False

    def _on_preset_selected(self, _index: int = 0) -> None:
        if self._loading_preset_selector or self.on_apply_app_preset is None:
            return
        preset_id = self.preset_selector.currentData()
        warning = self.on_apply_app_preset(preset_id)
        if warning:
            self._show_notice(warning)

    def _set_language(self, language: str) -> None:
        lang = language.strip().lower() or "en"
        if lang == str(self.config.get("language", "en")).lower():
            return
        self.config["language"] = lang
        installed = list(self.config.get("installed_languages") or [])
        if lang not in installed:
            self.config["installed_languages"] = [lang, *installed]
        self.lang_label.setText(lang.upper())
        self._sync_translation_ui()
        if self.on_save_config is not None:
            self.on_save_config(self.config)
        # Idioma ASR va en WhisperEngine: requiere reinicio del pipeline.
        if self.on_restart_pipeline is not None:
            self.on_restart_pipeline()

    @staticmethod
    def _common_word_prefix(left: str, right: str) -> str:
        words_l = left.split()
        words_r = right.split()
        shared: list[str] = []
        for a, b in zip(words_l, words_r):
            if a != b:
                break
            shared.append(a)
        return " ".join(shared)

    def _replace_phrase_suffix(
        self, display: str, old_phrase: str, new_phrase: str
    ) -> str:
        if old_phrase and display.endswith(old_phrase):
            prefix = display[: -len(old_phrase)].rstrip()
            return f"{prefix} {new_phrase}".strip() if prefix else new_phrase
        if display:
            return f"{display} {new_phrase}".strip()
        return new_phrase

    def _begin_final_phrase(self, text: str) -> None:
        if self._final_text:
            self._final_text = f"{self._final_text} {text}".strip()
        else:
            self._final_text = text
        self._phrase_final = text
        self._pending_new_phrase = False
        self._final_text = self._trim_display(self._final_text)

    def _begin_translated_phrase(self, text: str) -> None:
        if self._translated_text:
            self._translated_text = f"{self._translated_text} {text}".strip()
        else:
            self._translated_text = text
        self._phrase_translated = text

    def _apply_translated_text(self, text: str, *, append: bool) -> None:
        if not text:
            return
        if append and self._phrase_translated:
            old = self._phrase_translated
            self._phrase_translated = f"{old} {text}".strip()
            self._translated_text = self._replace_phrase_suffix(
                self._translated_text, old, self._phrase_translated
            )
        elif append and self._translated_text:
            self._translated_text = f"{self._translated_text} {text}".strip()
            self._phrase_translated = (
                f"{self._phrase_translated} {text}".strip()
                if self._phrase_translated
                else text
            )
        elif not self._phrase_translated:
            self._begin_translated_phrase(text)
        elif text.startswith(self._phrase_translated):
            self._translated_text = self._replace_phrase_suffix(
                self._translated_text, self._phrase_translated, text
            )
            self._phrase_translated = text
        elif self._phrase_translated.startswith(text):
            # Anti-shrink de la frase ES actual.
            pass
        elif self._allow_rewrite() and self._common_word_prefix(
            self._phrase_translated, text
        ):
            # Misma enunciación corregida: sustituye solo la frase actual.
            self._translated_text = self._replace_phrase_suffix(
                self._translated_text, self._phrase_translated, text
            )
            self._phrase_translated = text
        elif self._allow_rewrite():
            # Hipótesis nueva sin solape: no borrar scrollback; nueva frase.
            self._begin_translated_phrase(text)
        self._translated_text = self._trim_display(self._translated_text)

    def _apply_final_text(self, text: str) -> bool:
        """Actualiza el EN confirmado. Devuelve True si cambió el buffer."""
        if not text:
            return False
        before = self._final_text
        if self._pending_new_phrase or not self._phrase_final:
            self._begin_final_phrase(text)
            return self._final_text != before
        if text.startswith(self._phrase_final):
            self._final_text = self._replace_phrase_suffix(
                self._final_text, self._phrase_final, text
            )
            self._phrase_final = text
            self._final_text = self._trim_display(self._final_text)
            return self._final_text != before
        # Anti-shrink: un prefijo del confirmado no pisa la frase (tampoco con rewrite).
        if self._phrase_final.startswith(text):
            return False
        if not self._allow_rewrite():
            return False
        if self._common_word_prefix(self._phrase_final, text):
            # Corrección de la misma frase (comparten prefijo de palabras).
            self._final_text = self._replace_phrase_suffix(
                self._final_text, self._phrase_final, text
            )
            self._phrase_final = text
            self._final_text = self._trim_display(self._final_text)
            return self._final_text != before
        # Commit divergente (p. ej. force-commit del streamer): el scrollback
        # se conserva y el texto nuevo empieza otra frase.
        self._begin_final_phrase(text)
        return self._final_text != before

    def _should_apply_translation(self, item_text: str, *, final_changed: bool) -> bool:
        if self._phrase_final == item_text or final_changed:
            return True
        return (
            self._allow_rewrite()
            and bool(item_text)
            and self._phrase_final.startswith(item_text)
        )

    def _show_notice(self, message: str, *, duration_ms: int = 8000) -> None:
        text = message.strip()
        if not text:
            return
        self.notice_label.setText(text)
        self.notice_label.setVisible(True)
        self._notice_timer.start(max(1000, duration_ms))

    def _clear_notice(self) -> None:
        self.notice_label.setText("")
        self.notice_label.setVisible(False)

    def _poll_queue(self) -> None:
        if debug_hud_enabled(self.config):
            PERF.note_out_queue(self.text_queue.qsize())
        updated = False
        while True:
            try:
                item = self.text_queue.get_nowait()
            except queue.Empty:
                break
            if item.reset_display:
                self._partial_text = ""
                self._phrase_final = ""
                self._phrase_translated = ""
                self._pending_new_phrase = True
                updated = True
                continue
            if item.notice:
                self._show_notice(item.notice)
            caption_payload = bool(item.text) or item.translated_text is not None
            if not caption_payload:
                continue
            if item.is_final:
                if item.seq < self._caption_seq:
                    if (
                        item.translated_text is not None
                        and item.text == self._phrase_final
                    ):
                        self._apply_translated_text(
                            item.translated_text, append=item.translation_append
                        )
                        updated = True
                    continue
                if item.seq > self._caption_seq:
                    self._caption_seq = item.seq
                final_changed = self._apply_final_text(item.text)
                self._partial_text = ""
                if item.translated_text is not None and self._should_apply_translation(
                    item.text, final_changed=final_changed
                ):
                    self._apply_translated_text(
                        item.translated_text, append=item.translation_append
                    )
            else:
                if not self._show_partials():
                    continue
                if (
                    item.translated_text is not None
                    and item.seq >= self._caption_seq
                ):
                    self._apply_translated_text(
                        item.translated_text, append=item.translation_append
                    )
                if self._phrase_final and item.text.startswith(self._phrase_final):
                    self._partial_text = item.text[len(self._phrase_final) :].strip()
                else:
                    self._partial_text = item.text
            updated = True
        if updated:
            self._refresh_caption_texts()

    def current_position(self) -> list[int]:
        return [self.x(), self.y()]

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        self._apply_caption_geometry()

    def _handle_mouse_press(self, event: QtGui.QMouseEvent, *, window_pos: QtCore.QPoint) -> bool:
        if event.button() == QtCore.Qt.MouseButton.RightButton:
            self._show_context_menu(
                self.mapFromGlobal(event.globalPosition().toPoint())
            )
            return True
        if event.button() != QtCore.Qt.MouseButton.LeftButton:
            return False
        edge = self._hit_test_resize(window_pos)
        if edge is not None:
            self._begin_resize(edge, event.globalPosition().toPoint())
            return True
        return self._begin_drag(event)

    def _handle_mouse_move(self, event: QtGui.QMouseEvent, *, window_pos: QtCore.QPoint) -> bool:
        if self._resize_edge is None and self._drag_offset is None:
            self._reveal_chrome()
        if (
            self._resize_edge is not None
            and event.buttons() & QtCore.Qt.MouseButton.LeftButton
        ):
            self._continue_resize(event.globalPosition().toPoint())
            return True
        if (
            self._drag_offset is not None
            and event.buttons() & QtCore.Qt.MouseButton.LeftButton
        ):
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            return True
        self._update_hover_cursor(window_pos)
        return False

    def _handle_mouse_release(self) -> None:
        if self._resize_edge is not None:
            self._persist_geometry()
        self._resize_edge = None
        self._resize_start_pos = None
        self._resize_start_geom = None
        self._drag_offset = None
        self.unsetCursor()
        self.panel.unsetCursor()

    def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if isinstance(event, QtGui.QMouseEvent):
            window_pos = self._event_window_pos(obj, event)
            if event.type() == QtCore.QEvent.Type.MouseButtonPress:
                if obj in (
                    self.settings_btn,
                    self.close_btn,
                    self.translate_btn,
                    self.preset_selector,
                ):
                    return False
                if (
                    obj is self.lang_label
                    and event.button() == QtCore.Qt.MouseButton.LeftButton
                ):
                    self._show_language_menu()
                    return True
                if self._handle_mouse_press(event, window_pos=window_pos):
                    return True
            if event.type() == QtCore.QEvent.Type.MouseMove:
                if self._handle_mouse_move(event, window_pos=window_pos):
                    return True
            if event.type() == QtCore.QEvent.Type.MouseButtonRelease:
                self._handle_mouse_release()
        return super().eventFilter(obj, event)

    def _begin_drag(self, event: QtGui.QMouseEvent) -> bool:
        handle = self.windowHandle()
        if handle is not None and handle.startSystemMove():
            return True
        self._drag_offset = (
            event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        )
        return True

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._handle_mouse_press(event, window_pos=event.position().toPoint()):
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._handle_mouse_move(event, window_pos=event.position().toPoint()):
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        self._handle_mouse_release()
        super().mouseReleaseEvent(event)

    def _handle_settings(self) -> None:
        if self.on_open_settings:
            self.on_open_settings()

    def _handle_close(self) -> None:
        if self.on_close_app:
            self.on_close_app()
        else:
            self.close()
