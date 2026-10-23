from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from PyQt6 import QtCore, QtGui, QtWidgets

from src.asr.languages import (
    AVAILABLE_LANGUAGES,
    language_label,
)
from src.asr.translate import (
    DEFAULT_TRANSLATOR_MODEL,
    TRANSLATOR_MODEL_IDS,
    TRANSLATOR_MODEL_LABELS,
)
from src.audio.devices import describe_audio_source, list_audio_sources
from src.config import (
    COMPUTE_TYPE_LABELS,
    COMPUTE_TYPES,
    FONT_WEIGHT_LABELS,
    FONT_WEIGHT_MODES,
    LATENCY_FACTORY_PRESETS,
    SECOND_LINE_LABELS,
    SECOND_LINE_MODES,
    TEXT_ALIGN_LABELS,
    TRANSLATION_FACTORY_PRESETS,
    TRANSLATION_PRESET_LABELS,
    TRANSLATION_STICKY_LABELS,
    TRANSLATION_STICKY_MODES,
    add_translation_user_preset,
    delete_latency_user_preset,
    delete_translation_user_preset,
    latency_preset_label,
    list_app_preset_ids,
    save_latency_preset,
    save_latency_preset_as,
    save_translation_preset,
    validate_config,
)
from src.preset_state import (
    DOMAIN_APP,
    DOMAIN_DECODE,
    DOMAIN_LATENCY,
    describe,
    domain_preset_ids,
    is_deletable,
    is_factory,
    is_modified,
    is_partial,
    preset_label,
)
from src.presets import app_preset_label, is_factory_preset
from src.ui.branding import load_brand_logo_pixmap, repo_or_app_root
from src.ui.fonts import (
    available_caption_fonts,
    font_family_qss,
    font_weight_css,
)
from src.ui.presets_ui import (
    CHOICE_NEW,
    CHOICE_OVERWRITE,
    ActionState,
    PresetActionBar,
    PresetValuesDialog,
    ask_preset_name,
    ask_save_choice,
)

HINT_TRANSLATOR_MODEL = (
    "Motor de traducción al español. Opus-MT usa un modelo dedicado por idioma: "
    "más rápido, menos VRAM y no inventa texto en frases cortas. NLLB es un solo "
    "modelo para 200 idiomas, útil solo si tu idioma no tiene Opus-MT. "
    "Cambiarlo recarga solo el traductor."
)
HINT_FONT_FAMILY = (
    "Tipografía de los subtítulos. Arriba, las elegidas por legibilidad sobre vídeo "
    "que tengas instaladas; tras el separador, el resto de fuentes del sistema. "
    "«Sistema» deja la del escritorio."
)
HINT_FONT_WEIGHT = (
    "Grosor del trazo: más peso se lee mejor sobre fondos claros. Se aplica a la "
    "traducción y al texto confirmado; la línea parcial sigue ligera y en cursiva. "
    "Si la familia solo trae Regular y Negrita, Seminegrita se ve igual que Negrita."
)
HINT_COMPUTE_TYPE = (
    "Precisión de Whisper en GPU. float16 suele ser lo mejor; "
    "int8_float16 libera VRAM con poca pérdida; int8 ahorra más "
    "(útil si Whisper + traductor aprietan). Solo afecta al reconocimiento, no a la traducción."
)
HINT_LATENCY_MODE = (
    "Estable prioriza texto limpio. Baja latencia responde antes y admite más parpadeo. "
    "El icono a la derecha restablece confianza y techo del modo elegido."
)
HINT_CONFIDENCE = (
    "Cuántas lecturas seguidas deben coincidir antes de fijar el texto. "
    "Más alto = más estable; más bajo = más rápido."
)
HINT_MAX_LATENCY = (
    "Si el texto provisional no se fija a tiempo, se fuerza. "
    "Más bajo = menos espera, más riesgo de adelantar de más. "
    "El trozo de audio lo marca el modo (Estable ≈ 0,8 s · Baja ≈ 0,35 s) y se aplica "
    "al guardar sin reiniciar Whisper."
)
HINT_SHOW_PARTIALS = (
    "Muestra la hipótesis en vivo mientras aún puede cambiar. "
    "Si lo apagas, el cartón solo avanza al confirmar."
)
HINT_ALLOW_REWRITE = (
    "Permite corregir la frase actual si Whisper cambia de opinión. "
    "Si lo apagas, el texto solo puede alargarse."
)
HINT_SECOND_LINE = (
    "Con traducción activa: línea 1 = traducción; línea 2 según este selector. "
    "Sin traducción: una sola línea de reconocimiento."
)
HINT_TX_STICKY = (
    "Normal traduce solo lo confirmado. Sticky reutiliza tramos ya traducidos. "
    "Sticky + parciales adelanta la traducción de la hipótesis en vivo."
)
HINT_TX_BEAM = "Amplitud de búsqueda (1–8). Más alto = mejor frase, más carga."
HINT_TX_LENGTH = (
    "Empuja la longitud de la traducción (0,6–1,5). Cerca de 1,0 es neutro."
)
HINT_TX_NGRAM = (
    "Evita bucles de palabras (0–5). 0 lo desactiva; 3 suele bastar en subtítulos."
)

MSG_PARTIALS_STICKY_CONFLICT = (
    "Tienes el texto parcial apagado y el modo Sticky + parciales.\n\n"
    "Ese modo necesita ver la hipótesis en vivo; sin parciales no hace nada.\n\n"
    "Cancelar deshace el último cambio. El otro botón deja una combinación usable."
)


@dataclass(frozen=True)
class SettingsModeConflict:
    message: str
    fix_button_label: str
    # None = no cambiar ese knob al pulsar «compatible».
    fix_show_partials: bool | None = None
    fix_sticky_mode: str | None = None


def detect_partials_sticky_conflict(
    *,
    show_partials: bool,
    sticky_mode: str,
    changed: str,
) -> SettingsModeConflict | None:
    """Conflicto: sin parciales + sticky=partials.

    `changed`: ``partials`` | ``sticky`` | ``save`` — decide el botón de arreglo
    (cambiar la *otra* opción) y el mensaje de acción.
    """
    sticky = str(sticky_mode or "").strip().lower()
    if show_partials or sticky != "partials":
        return None
    if changed == "partials":
        return SettingsModeConflict(
            message=MSG_PARTIALS_STICKY_CONFLICT,
            fix_button_label="Pasar sticky a solo confirmados",
            fix_sticky_mode="committed",
        )
    if changed == "sticky":
        return SettingsModeConflict(
            message=MSG_PARTIALS_STICKY_CONFLICT,
            fix_button_label="Activar texto parcial",
            fix_show_partials=True,
        )
    # Guardar u origen desconocido: preferimos conservar «sin parciales».
    return SettingsModeConflict(
        message=MSG_PARTIALS_STICKY_CONFLICT,
        fix_button_label="Pasar sticky a solo confirmados",
        fix_sticky_mode="committed",
    )


# Cinema lower-third: carbón profundo + ámbar de marquesina (no GNOME blue genérico).
_SETTINGS_QSS = """
QDialog#SettingsDialog {
    background: #16181e;
    color: #e8eaef;
}
QScrollArea#SettingsScroll {
    background: transparent;
    border: none;
}
QWidget#SettingsBody {
    background: transparent;
}
QLabel#DialogTitle {
    color: #f4f5f7;
    font-size: 18px;
    font-weight: 600;
    letter-spacing: 0.2px;
}
QLabel#DialogSubtitle {
    color: #8b93a7;
    font-size: 12px;
}
QLabel#BrandLogo {
    background: transparent;
    border: none;
    padding: 0;
}
QToolTip {
    background-color: #1c1f27;
    color: #f4f5f7;
    border: 1px solid #e8b86d;
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 12px;
    font-weight: 500;
    letter-spacing: 0.2px;
}
QFrame#PreviewStage {
    background: qlineargradient(
        x1:0, y1:0, x2:1, y2:1,
        stop:0 #0c0d10, stop:0.55 #12141a, stop:1 #1a1520
    );
    border: 1px solid #2c3140;
    border-radius: 14px;
}
QLabel#PreviewHint {
    color: #6d758a;
    font-size: 11px;
    letter-spacing: 0.8px;
}
QLabel#PreviewCaption {
    font-weight: 600;
}
QLabel#SectionTitle {
    color: #e8b86d;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 1.4px;
}
QFrame#SectionRule {
    background: #2c3140;
    max-height: 1px;
    min-height: 1px;
    border: none;
}
QLabel#FieldLabel {
    color: #c5cad6;
    font-size: 13px;
}
QLabel#FieldHint {
    color: #7a8296;
    font-size: 11px;
    min-height: 0;
}
QLabel#ValueChip {
    color: #e8b86d;
    font-size: 12px;
    font-weight: 600;
    min-width: 28px;
}
QComboBox, QSpinBox, QDoubleSpinBox {
    background: #22262f;
    color: #eef0f4;
    border: 1px solid #3a4152;
    border-radius: 6px;
    padding: 0px 8px;
    min-height: 32px;
    max-height: 32px;
    font-size: 12px;
}
QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover {
    border-color: #5a6478;
}
QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {
    border-color: #e8b86d;
}
QComboBox::drop-down {
    border: none;
    width: 28px;
}
QComboBox QAbstractItemView {
    background: #22262f;
    color: #eef0f4;
    border: 1px solid #3a4152;
    selection-background-color: #3a3224;
    selection-color: #f7e3bc;
    outline: none;
}
QSlider::groove:horizontal {
    height: 6px;
    background: #2a2f3a;
    border-radius: 3px;
}
QSlider::handle:horizontal {
    width: 16px;
    height: 16px;
    margin: -5px 0;
    background: #e8b86d;
    border-radius: 8px;
}
QSlider::sub-page:horizontal {
    background: #c4923f;
    border-radius: 3px;
}
QPushButton#SecondaryButton {
    background: #22262f;
    color: #d5dae6;
    border: 1px solid #3a4152;
    border-radius: 6px;
    padding: 0px 10px;
    min-height: 32px;
    max-height: 32px;
    font-size: 12px;
}
QPushButton#SecondaryButton:hover {
    background: #2a303c;
    border-color: #5a6478;
}
QPushButton#GhostButton {
    background: transparent;
    color: #9aa3b5;
    border: 1px solid transparent;
    border-radius: 8px;
    padding: 6px 10px;
    text-align: left;
}
QPushButton#GhostButton:hover {
    color: #e8b86d;
    border-color: #3a4152;
}
QPushButton#ColorSwatch {
    min-width: 32px;
    max-width: 32px;
    min-height: 32px;
    max-height: 32px;
    border: 1px solid #4a5164;
    border-radius: 6px;
    padding: 0;
}
QPushButton#ColorSwatch:hover {
    border-color: #e8b86d;
}
QPushButton#AlignToggle {
    min-width: 32px;
    max-width: 32px;
    min-height: 32px;
    max-height: 32px;
    border: 1px solid #3a4152;
    border-radius: 6px;
    background: #22262f;
    padding: 0;
}
QPushButton#AlignToggle:hover {
    border-color: #5a6478;
}
QPushButton#AlignToggle:checked {
    border-color: #e8b86d;
    background: #2a2620;
}
QPushButton#IconToolButton {
    /* Tamaño vía setFixedSize: min/max en QSS + border hinchan el widget y
       roban el spacing del QHBoxLayout (selector pegado al 1.er botón). */
    border: 1px solid #3a4152;
    border-radius: 6px;
    background: #22262f;
    padding: 0;
}
QPushButton#IconToolButton:hover {
    border-color: #e8b86d;
}
QPushButton#IconToolButton:disabled {
    background: #1a1d24;
    border-color: #2a2f3a;
}
QLabel#ColorHex {
    color: #e8b86d;
    font-size: 12px;
    font-weight: 600;
    font-family: "JetBrains Mono", "Cascadia Code", "Ubuntu Mono", monospace;
    min-width: 72px;
}
QPushButton#PrimaryButton {
    background: #e8b86d;
    color: #1a140c;
    border: none;
    border-radius: 6px;
    padding: 0px 16px;
    min-width: 96px;
    min-height: 32px;
    max-height: 32px;
    font-weight: 600;
    font-size: 12px;
}
QPushButton#PrimaryButton:hover {
    background: #f0c784;
}
QPushButton#DialogCancel {
    background: #22262f;
    color: #d5dae6;
    border: 1px solid #3a4152;
    border-radius: 6px;
    padding: 0px 16px;
    min-width: 96px;
    min-height: 32px;
    max-height: 32px;
    font-size: 12px;
}
QPushButton#DialogCancel:hover {
    background: #2a303c;
    border-color: #5a6478;
}
QTabWidget::pane {
    border: 1px solid #2c3140;
    border-radius: 10px;
    top: -1px;
    background: transparent;
}
QTabBar::tab {
    background: #1c1f27;
    color: #8b93a7;
    border: 1px solid #2c3140;
    border-bottom: none;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    padding: 8px 14px;
    margin-right: 4px;
}
QTabBar::tab:selected {
    background: #22262f;
    color: #f4f5f7;
    border-color: #3a4152;
}
QTabBar::tab:hover:!selected {
    color: #e8b86d;
}
"""


# Wordmark en el hueco libre de presets: centrado; tamaño de lectura de marca.
BRAND_LOGO_HEIGHT = 192
BRAND_LOGO_MAX_WIDTH = 480


def _brand_logo_label(parent: QtWidgets.QWidget | None = None) -> QtWidgets.QLabel:
    """Wordmark LIVE CAPTIONS PGL, centrado en el hueco de presets."""
    lab = QtWidgets.QLabel(parent)
    lab.setObjectName("BrandLogo")
    lab.setAlignment(
        QtCore.Qt.AlignmentFlag.AlignHCenter | QtCore.Qt.AlignmentFlag.AlignVCenter
    )
    dpr = float(lab.devicePixelRatioF())
    pm = load_brand_logo_pixmap(height=BRAND_LOGO_HEIGHT, device_pixel_ratio=dpr)
    if pm.isNull():
        lab.setText("LIVE CAPTIONS")
        lab.setStyleSheet(
            "color: #e8b86d; font-size: 14px; font-weight: 700; letter-spacing: 1.2px;"
        )
        lab.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignHCenter | QtCore.Qt.AlignmentFlag.AlignVCenter
        )
        return lab
    lab.setPixmap(pm)
    logical_w = int(round(pm.width() / max(pm.devicePixelRatio(), 1.0)))
    lab.setFixedSize(min(logical_w, BRAND_LOGO_MAX_WIDTH), BRAND_LOGO_HEIGHT)
    lab.setScaledContents(False)
    lab.setSizePolicy(
        QtWidgets.QSizePolicy.Policy.Fixed,
        QtWidgets.QSizePolicy.Policy.Fixed,
    )
    lab.setToolTip("LIVE CAPTIONS PGL")
    return lab


def _preset_controls_with_brand(
    controls: QtWidgets.QWidget,
    logo: QtWidgets.QLabel,
) -> QtWidgets.QWidget:
    """Controles a la izquierda; wordmark centrado en el hueco restante."""
    wrap = QtWidgets.QWidget()
    wrap.setSizePolicy(
        QtWidgets.QSizePolicy.Policy.Expanding,
        QtWidgets.QSizePolicy.Policy.Preferred,
    )
    row = QtWidgets.QHBoxLayout(wrap)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(0)
    row.addWidget(
        controls,
        stretch=0,
        alignment=QtCore.Qt.AlignmentFlag.AlignLeft
        | QtCore.Qt.AlignmentFlag.AlignVCenter,
    )
    # Dos stretches iguales → centro horizontal del hueco libre.
    row.addStretch(1)
    row.addWidget(
        logo,
        stretch=0,
        alignment=QtCore.Qt.AlignmentFlag.AlignHCenter
        | QtCore.Qt.AlignmentFlag.AlignVCenter,
    )
    row.addStretch(1)
    wrap.setMinimumHeight(max(CONTROL_HEIGHT, BRAND_LOGO_HEIGHT))
    return wrap


CONTROL_HEIGHT = 32
CONTROL_WIDTH = {
    "sm": 140,
    "md": 240,
    "lg": 360,
}
# Espacio uniforme: selector↔botones y botones↔botones.
CONTROL_ACTION_GAP = 6
# Columna de control: selector lg + hasta 4 botones icono (ver/guardar/restaurar/borrar).
CONTROL_SLOT_WIDTH = CONTROL_WIDTH["lg"] + 4 * CONTROL_HEIGHT + 4 * CONTROL_ACTION_GAP


def _set_control_height(widget: QtWidgets.QWidget) -> None:
    widget.setFixedHeight(CONTROL_HEIGHT)


def _set_control_width(widget: QtWidgets.QWidget, size: str) -> None:
    widget.setFixedWidth(CONTROL_WIDTH[size])


def _pad_button_icon_gap(
    button: QtWidgets.QPushButton, gap_px: int = 8
) -> None:
    """Añade margen transparente a la derecha del icono (hueco icono→texto)."""
    icon = button.icon()
    if icon.isNull():
        return
    size = button.iconSize()
    if size.width() <= 0 or size.height() <= 0:
        size = QtCore.QSize(16, 16)
    src = icon.pixmap(size)
    if src.isNull():
        return
    out = QtGui.QPixmap(src.width() + gap_px, max(src.height(), size.height()))
    out.fill(QtCore.Qt.GlobalColor.transparent)
    painter = QtGui.QPainter(out)
    y = (out.height() - src.height()) // 2
    painter.drawPixmap(0, y, src)
    painter.end()
    button.setIcon(QtGui.QIcon(out))
    button.setIconSize(QtCore.QSize(size.width() + gap_px, size.height()))


def _size_combo(combo: QtWidgets.QComboBox, size: str) -> QtWidgets.QComboBox:
    _set_control_width(combo, size)
    _set_control_height(combo)
    return combo


def _combo_actions_row(
    combo: QtWidgets.QComboBox,
    *buttons: QtWidgets.QWidget,
) -> QtWidgets.QWidget:
    """Selector + botones a la derecha con el mismo margen entre todos.

    Sin ancho fijo: los botones de preset aparecen y desaparecen según el estado
    del preset, así que el ancho lo negocia el layout.
    """
    wrap = QtWidgets.QWidget()
    row = QtWidgets.QHBoxLayout(wrap)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(CONTROL_ACTION_GAP)
    row.addWidget(combo, stretch=0)
    for button in buttons:
        row.addWidget(button, stretch=0)
    row.addStretch(1)
    wrap.setSizePolicy(
        QtWidgets.QSizePolicy.Policy.Maximum,
        QtWidgets.QSizePolicy.Policy.Preferred,
    )
    return wrap


def _control_slot(widget: QtWidgets.QWidget) -> QtWidgets.QWidget:
    """Hueco de ancho fijo: el control define la altura (sin recortar)."""
    slot = QtWidgets.QWidget()
    slot.setFixedWidth(CONTROL_SLOT_WIDTH)
    slot.setSizePolicy(
        QtWidgets.QSizePolicy.Policy.Fixed,
        QtWidgets.QSizePolicy.Policy.Preferred,
    )
    layout = QtWidgets.QHBoxLayout(slot)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    layout.addWidget(
        widget,
        stretch=0,
        alignment=QtCore.Qt.AlignmentFlag.AlignLeft
        | QtCore.Qt.AlignmentFlag.AlignVCenter,
    )
    layout.addStretch(1)
    return slot


def _field_label(text: str) -> QtWidgets.QLabel:
    """Etiqueta centrada verticalmente con el control (misma altura)."""
    lab = QtWidgets.QLabel(text)
    lab.setObjectName("FieldLabel")
    lab.setAlignment(
        QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter
    )
    lab.setFixedHeight(CONTROL_HEIGHT)
    return lab


def _alignment_icon(mode: str, *, size: int = 20, color: str = "#c5cad6") -> QtGui.QIcon:
    pm = QtGui.QPixmap(size, size)
    pm.fill(QtCore.Qt.GlobalColor.transparent)
    painter = QtGui.QPainter(pm)
    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    pen = QtGui.QPen(QtGui.QColor(color))
    pen.setWidthF(2.0)
    pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    ys = (5, 10, 15)
    if mode == "left":
        spans = ((4, 16), (4, 13), (4, 16))
    else:
        spans = ((5, 15), (6, 14), (5, 15))
    for y, (x1, x2) in zip(ys, spans, strict=True):
        painter.drawLine(x1, y, x2, y)
    painter.end()
    return QtGui.QIcon(pm)


class MarqueeToggle(QtWidgets.QAbstractButton):
    """Interruptor carbón/ámbar alineado con el look cinema del Settings."""

    _TRACK_W = 32
    _TRACK_H = 18
    _KNOB = 14
    _GAP = 8

    def __init__(self, text: str = "", parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setText(text)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        font = self.font()
        font.setPointSize(12)
        self.setFont(font)

    def sizeHint(self) -> QtCore.QSize:
        fm = QtGui.QFontMetrics(self.font())
        label_w = fm.horizontalAdvance(self.text()) if self.text() else 0
        width = self._TRACK_W + (self._GAP + label_w if label_w else 0)
        height = max(22, self._TRACK_H, fm.height())
        return QtCore.QSize(width, height)

    def minimumSizeHint(self) -> QtCore.QSize:
        return self.sizeHint()

    def enterEvent(self, event: QtGui.QEnterEvent) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event: QtCore.QEvent) -> None:
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        del event
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)

        on = self.isChecked()
        hovered = self.underMouse() and self.isEnabled()
        disabled = not self.isEnabled()

        if on:
            track = QtGui.QColor("#e8b86d")
            border = QtGui.QColor("#f0c784" if hovered else "#e8b86d")
            knob = QtGui.QColor("#1a140c")
        else:
            track = QtGui.QColor("#22262f")
            border = QtGui.QColor("#5a6478" if hovered else "#3a4152")
            knob = QtGui.QColor("#c5cad6")

        if disabled:
            track.setAlpha(120)
            border.setAlpha(100)
            knob.setAlpha(120)

        y = (self.height() - self._TRACK_H) / 2
        track_rect = QtCore.QRectF(0, y, self._TRACK_W, self._TRACK_H)
        painter.setPen(QtGui.QPen(border, 1.2))
        painter.setBrush(track)
        painter.drawRoundedRect(track_rect, self._TRACK_H / 2, self._TRACK_H / 2)

        margin = (self._TRACK_H - self._KNOB) / 2
        if on:
            knob_x = self._TRACK_W - margin - self._KNOB
        else:
            knob_x = margin
        knob_rect = QtCore.QRectF(knob_x, y + margin, self._KNOB, self._KNOB)
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(knob)
        painter.drawEllipse(knob_rect)

        if self.text():
            text_color = QtGui.QColor("#6d758a" if disabled else "#e8eaef")
            painter.setPen(text_color)
            text_rect = QtCore.QRectF(
                self._TRACK_W + self._GAP,
                0,
                self.width() - self._TRACK_W - self._GAP,
                self.height(),
            )
            painter.drawText(
                text_rect,
                int(
                    QtCore.Qt.AlignmentFlag.AlignVCenter
                    | QtCore.Qt.AlignmentFlag.AlignLeft
                ),
                self.text(),
            )

        if self.hasFocus():
            focus = QtGui.QPen(QtGui.QColor("#e8b86d"))
            focus.setWidthF(1.0)
            focus.setStyle(QtCore.Qt.PenStyle.DotLine)
            painter.setPen(focus)
            painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(
                track_rect.adjusted(-2, -2, 2, 2),
                self._TRACK_H / 2 + 1,
                self._TRACK_H / 2 + 1,
            )


def _int_slider_row(
    *,
    low: int,
    high: int,
    value: int,
    suffix: str = "",
    format_value: Callable[[int], str] | None = None,
    chip_min_width: int = 36,
    width: str = "lg",
) -> tuple[QtWidgets.QWidget, QtWidgets.QSlider, QtWidgets.QLabel]:
    wrap = QtWidgets.QWidget()
    row = QtWidgets.QHBoxLayout(wrap)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(8)
    row.setAlignment(QtCore.Qt.AlignmentFlag.AlignVCenter)
    slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
    slider.setRange(low, high)
    slider.setValue(value)
    _set_control_height(slider)
    text = format_value(value) if format_value else f"{value}{suffix}"
    chip = QtWidgets.QLabel(text)
    chip.setObjectName("ValueChip")
    chip.setAlignment(
        QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter
    )
    chip.setMinimumWidth(chip_min_width)
    chip.setFixedHeight(CONTROL_HEIGHT)
    row.addWidget(
        slider,
        stretch=1,
        alignment=QtCore.Qt.AlignmentFlag.AlignVCenter,
    )
    row.addWidget(
        chip,
        stretch=0,
        alignment=QtCore.Qt.AlignmentFlag.AlignVCenter,
    )
    _set_control_width(wrap, width)
    wrap.setFixedHeight(CONTROL_HEIGHT)
    return wrap, slider, chip


class _Section(QtWidgets.QWidget):
    def __init__(self, title: str, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 4, 0, 4)
        root.setSpacing(14)

        header = QtWidgets.QHBoxLayout()
        header.setSpacing(10)
        label = QtWidgets.QLabel(title.upper())
        label.setObjectName("SectionTitle")
        header.addWidget(label)
        rule = QtWidgets.QFrame()
        rule.setObjectName("SectionRule")
        rule.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        header.addWidget(rule, stretch=1)
        root.addLayout(header)

        self.body = QtWidgets.QFormLayout()
        self.body.setContentsMargins(0, 6, 0, 0)
        self.body.setHorizontalSpacing(16)
        self.body.setVerticalSpacing(12)
        # AlignTop: la etiqueta (altura = control) queda a la altura del selector,
        # no centrada respecto al hint multilínea de la derecha.
        self.body.setLabelAlignment(
            QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignTop
        )
        self.body.setFormAlignment(QtCore.Qt.AlignmentFlag.AlignTop)
        self.body.setFieldGrowthPolicy(
            QtWidgets.QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow
        )
        root.addLayout(self.body)

    def add_row(
        self,
        label: str,
        widget: QtWidgets.QWidget,
        hint: str | None = None,
    ) -> None:
        self.body.addRow(_field_label(label), _with_side_hint(widget, hint))

    def add_hint(self, text: str) -> None:
        """Texto al mismo ancho que el selector lg (p. ej. bajo el preset general)."""
        hint = QtWidgets.QLabel(text)
        hint.setObjectName("FieldHint")
        hint.setWordWrap(True)
        hint.setFixedWidth(CONTROL_WIDTH["lg"])
        hint.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignTop
        )
        hint.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Fixed,
            QtWidgets.QSizePolicy.Policy.Preferred,
        )
        self.body.addRow("", hint)


def _with_side_hint(
    widget: QtWidgets.QWidget, hint: str | None
) -> QtWidgets.QWidget:
    if not hint:
        return _control_slot(widget)
    wrap = QtWidgets.QWidget()
    wrap.setSizePolicy(
        QtWidgets.QSizePolicy.Policy.Expanding,
        QtWidgets.QSizePolicy.Policy.Preferred,
    )
    row = QtWidgets.QHBoxLayout(wrap)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(12)
    row.setAlignment(QtCore.Qt.AlignmentFlag.AlignTop)
    row.addWidget(
        _control_slot(widget),
        stretch=0,
        alignment=QtCore.Qt.AlignmentFlag.AlignTop,
    )
    hint_lab = QtWidgets.QLabel(hint)
    hint_lab.setObjectName("FieldHint")
    hint_lab.setWordWrap(True)
    hint_lab.setAlignment(
        QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignTop
    )
    hint_lab.setSizePolicy(
        QtWidgets.QSizePolicy.Policy.Expanding,
        QtWidgets.QSizePolicy.Policy.Preferred,
    )
    hint_lab.setMinimumWidth(80)
    row.addWidget(
        hint_lab,
        stretch=1,
        alignment=QtCore.Qt.AlignmentFlag.AlignTop,
    )
    return wrap


def _slider_value_row(
    slider: QtWidgets.QSlider,
    chip: QtWidgets.QLabel,
    *,
    width: str = "lg",
) -> QtWidgets.QWidget:
    wrap = QtWidgets.QWidget()
    row = QtWidgets.QHBoxLayout(wrap)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(8)
    row.setAlignment(QtCore.Qt.AlignmentFlag.AlignVCenter)
    _set_control_height(slider)
    chip.setAlignment(
        QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter
    )
    chip.setFixedHeight(CONTROL_HEIGHT)
    row.addWidget(
        slider,
        stretch=1,
        alignment=QtCore.Qt.AlignmentFlag.AlignVCenter,
    )
    row.addWidget(
        chip,
        stretch=0,
        alignment=QtCore.Qt.AlignmentFlag.AlignVCenter,
    )
    _set_control_width(wrap, width)
    wrap.setFixedHeight(CONTROL_HEIGHT)
    return wrap


class SettingsDialog(QtWidgets.QDialog):
    def __init__(
        self,
        parent: QtWidgets.QWidget | None,
        config: dict[str, Any],
        controller: Any | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("SettingsDialog")
        self.setWindowTitle("Configuración de subtítulos")
        self.setModal(True)
        self.setMinimumWidth(520)
        self.setMinimumHeight(640)
        self.setStyleSheet(_SETTINGS_QSS)
        self._controller = controller
        icon_path = (
            repo_or_app_root()
            / "packaging"
            / "icons"
            / "whisper-live-captions-128.png"
        )
        if icon_path.is_file():
            self.setWindowIcon(QtGui.QIcon(str(icon_path)))

        self._config = dict(config)
        if not isinstance(self._config.get("latency_profiles"), dict):
            self._config["latency_profiles"] = deepcopy(LATENCY_FACTORY_PRESETS)
        self._config = validate_config(self._config)

        width = int(self._config.get("settings_window_width", 560))
        height = int(self._config.get("settings_window_height", 720))
        self.resize(max(520, width), max(640, height))

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(14)

        header = QtWidgets.QVBoxLayout()
        header.setSpacing(2)
        title = QtWidgets.QLabel("Configuración")
        title.setObjectName("DialogTitle")
        header.addWidget(title)
        root.addLayout(header)

        root.addWidget(self._build_app_preset_bar())

        tabs = QtWidgets.QTabWidget()
        tabs.addTab(self._build_general_tab(config), "General")
        tabs.addTab(self._build_latency_tab(config), "Latencia")
        tabs.addTab(self._build_appearance_tab(config), "Apariencia")
        tabs.addTab(self._build_translation_tab(config), "Traducciones")
        root.addWidget(tabs, stretch=1)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Save
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        save_btn = buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Save)
        cancel_btn = buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        assert save_btn is not None and cancel_btn is not None
        save_btn.setText("Guardar")
        save_btn.setObjectName("PrimaryButton")
        _set_control_height(save_btn)
        _pad_button_icon_gap(save_btn)
        cancel_btn.setText("Cancelar")
        cancel_btn.setObjectName("DialogCancel")
        _set_control_height(cancel_btn)
        _pad_button_icon_gap(cancel_btn)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self._loading_profile = False
        self._loading_tx = False
        self._loading_app_preset = False
        self._guarding_mode_conflict = False
        self._latency_draft: dict[str, Any] = {}
        self._tx_draft: dict[str, Any] = {}
        self._prev_sticky_mode = str(
            self.tx_sticky_mode.currentData() or "off"
        )
        self.captions_show_partials.toggled.connect(self._on_show_partials_toggled)
        self.tx_sticky_mode.currentIndexChanged.connect(self._on_sticky_mode_changed)
        self._refresh_latency_preset_combo()
        self._refresh_translation_preset_combo()
        self._refresh_app_preset_bar()
        self._refresh_preview()
        # El estado «modificado» depende de widgets de todas las pestañas.
        self._dirty_timer = QtCore.QTimer(self)
        self._dirty_timer.setInterval(250)
        self._dirty_timer.timeout.connect(self._sync_all_preset_actions)
        self._dirty_timer.start()

    def _sync_all_preset_actions(self) -> None:
        self._sync_app_preset_actions()
        self._sync_latency_actions()
        self._sync_translation_preset_actions()

    def _persist_preset_library(self) -> None:
        """Un alta/borrado de preset debe sobrevivir a cancelar el diálogo."""
        if self._controller is None:
            return
        persist = getattr(self._controller, "persist_preset_library_from_settings", None)
        if callable(persist):
            persist(self)

    def _build_app_preset_bar(self) -> QtWidgets.QWidget:
        bar = _Section("Preset general")

        self.app_preset = QtWidgets.QComboBox()
        _size_combo(self.app_preset, "lg")
        self.app_preset.currentIndexChanged.connect(self._on_app_preset_changed)

        self.app_preset_actions = PresetActionBar()
        self.app_preset_actions.view_requested.connect(self._view_app_preset_values)
        self.app_preset_actions.save_requested.connect(self._save_app_preset_clicked)
        self.app_preset_actions.save_as_requested.connect(self._save_app_preset_as)
        self.app_preset_actions.restore_requested.connect(self._restore_app_preset)
        self.app_preset_actions.delete_requested.connect(self._delete_app_preset)
        # Compatibilidad con los nombres que ya usaban tests y callbacks.
        self.app_preset_view_btn = self.app_preset_actions.view_btn
        self.app_preset_save_btn = self.app_preset_actions.save_btn
        self.app_preset_save_as_btn = self.app_preset_actions.save_as_btn
        self.app_preset_restore_btn = self.app_preset_actions.restore_btn
        self.app_preset_delete_btn = self.app_preset_actions.delete_btn

        controls = _combo_actions_row(self.app_preset, self.app_preset_actions)
        self.brand_logo = _brand_logo_label(bar)
        bar.body.addRow(
            _field_label("Activo"),
            _preset_controls_with_brand(controls, self.brand_logo),
        )

        if self._controller is None:
            self.app_preset.setEnabled(False)
            self.app_preset_actions.setEnabled(False)
        return bar

    def _app_preset_live(self) -> dict[str, Any]:
        """Estado vivo que se compara con el preset general.

        Se lee de los widgets (no de `result_config`, que escribe perfiles y se
        llama en un timer). La geometría y los espejos quedan fuera a propósito.
        """
        audio = self.audio.currentData()
        if audio is None:
            audio = self.audio.currentText()
        return {
            **self._config,
            "language": str(self.language.currentData() or "en"),
            "model": self.model.currentText(),
            "compute_type": str(self.compute_type.currentData() or "float16"),
            "audio_monitor": str(audio or ""),
            "latency_mode": self._current_mode(),
            "translation_decode_preset": self._current_tx_preset(),
            "translator_model": str(
                self.translator_model.currentData() or DEFAULT_TRANSLATOR_MODEL
            ),
            "translation_sticky_mode": str(self.tx_sticky_mode.currentData() or "off"),
            "second_line_mode": str(self.second_line_mode.currentData() or "none"),
            "captions_show_partials": self.captions_show_partials.isChecked(),
            "captions_allow_rewrite": self.captions_allow_rewrite.isChecked(),
            "font_size": int(self.font_size.value()),
            "font_family": self._current_font_family(),
            "font_weight": self._current_font_weight(),
            "font_color": self._swatch_hex(self.font_color_btn),
            "bg_color": self._swatch_hex(self.bg_color_btn),
            "bg_alpha": self.alpha.value() / 100.0,
            "padding": int(self.padding.value()),
            "text_align": self._current_text_align(),
        }

    def _refresh_app_preset_bar(self) -> None:
        self._loading_app_preset = True
        try:
            current = self._config.get("app_preset")
            self.app_preset.clear()
            self.app_preset.addItem("(ninguno)", None)
            for preset_id in list_app_preset_ids(self._config):
                self.app_preset.addItem(app_preset_label(preset_id), preset_id)
            if isinstance(current, str) and current:
                idx = self.app_preset.findData(current)
                self.app_preset.setCurrentIndex(idx if idx >= 0 else 0)
            else:
                self.app_preset.setCurrentIndex(0)
            self.app_preset.setEnabled(self._controller is not None)
            self._sync_app_preset_actions()
        finally:
            self._loading_app_preset = False

    def _sync_app_preset_actions(self) -> None:
        current = self._config.get("app_preset")
        preset_id = current if isinstance(current, str) and current else None
        active = bool(preset_id) and preset_id in (
            self._config.get("app_presets") or {}
        )
        modified = False
        if active and preset_id:
            modified = is_modified(
                DOMAIN_APP, self._config, preset_id, self._app_preset_live()
            )
        state = ActionState(
            active=active,
            modified=modified,
            deletable=active and is_deletable(DOMAIN_APP, preset_id),
            factory=bool(preset_id) and is_factory(DOMAIN_APP, preset_id),
        )
        self.app_preset_actions.configure(state)
        self.app_preset_actions.setEnabled(self._controller is not None)

    def _on_app_preset_changed(self, _index: int = 0) -> None:
        if self._loading_app_preset or self._controller is None:
            return
        preset_id = self.app_preset.currentData()
        warning = self._controller.apply_app_preset_from_settings(preset_id, self)
        if warning:
            QtWidgets.QMessageBox.information(self, "Preset", warning)

    def _view_app_preset_values(self) -> None:
        preset_id = self._config.get("app_preset")
        if not preset_id:
            return
        rows = describe(DOMAIN_APP, self._config, str(preset_id))
        note = None
        if is_partial(DOMAIN_APP, self._config, str(preset_id)):
            note = (
                "Este preset fija solo estas opciones; el resto de la "
                "configuración se conserva como esté."
            )
        elif is_factory(DOMAIN_APP, str(preset_id)):
            note = "Preset de fábrica: sus valores no se pueden modificar."
        PresetValuesDialog(
            self,
            title=f"Valores de «{preset_label(DOMAIN_APP, str(preset_id))}»",
            subtitle="Opciones que aplica este preset al elegirlo.",
            rows=rows,
            note=note,
        ).exec()

    def _save_app_preset_clicked(self) -> None:
        """Guardar: sobrescribir el propio o crear uno nuevo, nunca tocar fábrica."""
        if self._controller is None:
            return
        preset_id = self._config.get("app_preset")
        if not preset_id:
            self._save_app_preset_as()
            return
        factory = is_factory(DOMAIN_APP, str(preset_id))
        choice = ask_save_choice(
            self,
            preset_label=preset_label(DOMAIN_APP, str(preset_id)),
            is_factory=factory,
        )
        if choice == CHOICE_NEW:
            self._save_app_preset_as()
            return
        if choice != CHOICE_OVERWRITE or factory:
            return
        self._controller.save_app_preset_from_settings(self)
        self._sync_app_preset_actions()

    def _save_app_preset_as(self) -> None:
        if self._controller is None:
            return
        name = ask_preset_name(self, title="Guardar preset general")
        if name is None:
            return
        self._controller.save_app_preset_as_from_settings(name, self)

    def _restore_app_preset(self) -> None:
        """Vuelve a los valores del preset activo (no a los de fábrica globales)."""
        if self._controller is None:
            return
        preset_id = self._config.get("app_preset")
        if not preset_id:
            return
        self._controller.apply_app_preset_from_settings(str(preset_id), self)

    def _delete_app_preset(self) -> None:
        if self._controller is None:
            return
        preset_id = self._config.get("app_preset")
        if not preset_id:
            return
        if is_factory_preset(str(preset_id)):
            QtWidgets.QMessageBox.information(
                self,
                "Borrar preset",
                f"El preset de fábrica «{preset_id}» no se puede borrar.",
            )
            return
        box = QtWidgets.QMessageBox(self)
        box.setIcon(QtWidgets.QMessageBox.Icon.Warning)
        box.setWindowTitle("Borrar preset")
        box.setText(f"¿Borrar el preset «{preset_id}»?")
        box.setInformativeText(
            "No se puede deshacer. La configuración actual no cambia."
        )
        box.setStandardButtons(
            QtWidgets.QMessageBox.StandardButton.Yes
            | QtWidgets.QMessageBox.StandardButton.No
        )
        box.setDefaultButton(QtWidgets.QMessageBox.StandardButton.No)
        if box.exec() != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        self._controller.delete_app_preset_from_settings(self)

    def reload_from_config(self, config: dict[str, Any]) -> None:
        """Recarga controles tras apply/save/delete de preset general."""
        self._config = validate_config(config)
        self._guarding_mode_conflict = True
        try:
            self._refresh_language_combo(str(self._config.get("language", "en")))
            model = str(self._config.get("model", "medium"))
            model_idx = self.model.findText(model)
            if model_idx >= 0:
                self.model.setCurrentIndex(model_idx)
            compute_idx = self.compute_type.findData(
                str(self._config.get("compute_type", "float16"))
            )
            self.compute_type.setCurrentIndex(compute_idx if compute_idx >= 0 else 0)
            audio = str(self._config.get("audio_monitor") or "")
            if audio:
                pos = self.audio.findData(audio)
                if pos >= 0:
                    self.audio.setCurrentIndex(pos)
                else:
                    self.audio.insertItem(0, describe_audio_source(audio).label, audio)
                    self.audio.setCurrentIndex(0)

            self._refresh_latency_preset_combo()

            self.captions_show_partials.setChecked(
                bool(self._config.get("captions_show_partials", False))
            )
            self.captions_allow_rewrite.setChecked(
                bool(self._config.get("captions_allow_rewrite", True))
            )

            self._refresh_font_family_combo(str(self._config.get("font_family", "")))
            self._set_font_weight(str(self._config.get("font_weight", "semibold")))
            self.font_size.setValue(int(self._config.get("font_size", 28)))
            self.font_size_value.setText(str(self.font_size.value()))
            self.padding.setValue(int(self._config.get("padding", 24)))
            self.padding_value.setText(str(self.padding.value()))
            self._set_text_align(str(self._config.get("text_align", "center")))
            self._set_swatch(
                self.font_color_btn,
                self.font_color_hex,
                str(self._config.get("font_color", "#ffffff")),
            )
            self._set_swatch(
                self.bg_color_btn,
                self.bg_color_hex,
                str(self._config.get("bg_color", "#000000")),
            )
            self.alpha.setValue(int(float(self._config.get("bg_alpha", 0.55)) * 100))
            self.alpha_value.setText(f"{self.alpha.value()}%")

            second_idx = self.second_line_mode.findData(
                str(self._config.get("second_line_mode", "live_asr"))
            )
            self.second_line_mode.setCurrentIndex(second_idx if second_idx >= 0 else 0)
            sticky = str(self._config.get("translation_sticky_mode", "off"))
            sticky_idx = self.tx_sticky_mode.findData(sticky)
            self.tx_sticky_mode.setCurrentIndex(sticky_idx if sticky_idx >= 0 else 0)
            self._prev_sticky_mode = sticky
            self._refresh_translator_model_combo()

            self._refresh_translation_preset_combo()
            self.apply_saved_geometry()
            self._refresh_preview()
        finally:
            self._guarding_mode_conflict = False
        self._refresh_app_preset_bar()
        self._sync_all_preset_actions()

    def apply_saved_geometry(
        self, *, fallback_center: QtCore.QPoint | None = None
    ) -> None:
        """Restaura tamaño/posición guardados; si no hay pos, centra en fallback."""
        width = max(520, int(self._config.get("settings_window_width", 560)))
        height = max(640, int(self._config.get("settings_window_height", 720)))
        self.resize(width, height)
        pos = self._config.get("settings_window_pos")
        if isinstance(pos, list) and len(pos) == 2:
            try:
                self.move(int(pos[0]), int(pos[1]))
                return
            except (TypeError, ValueError):
                pass
        if fallback_center is not None:
            self.move(
                fallback_center.x() - self.width() // 2,
                fallback_center.y() - self.height() // 2,
            )

    def geometry_snapshot(self) -> dict[str, Any]:
        return {
            "settings_window_pos": [self.x(), self.y()],
            "settings_window_width": self.width(),
            "settings_window_height": self.height(),
        }

    def accept(self) -> None:
        if not self._ensure_modes_compatible(changed="save"):
            return
        if not self._confirm_preset_drafts():
            return
        super().accept()

    def _confirm_preset_drafts(self) -> bool:
        """Cierra el ciclo de presets al aceptar el diálogo.

        Un preset propio absorbe el borrador (equivale a guardar los ajustes).
        Un preset de fábrica no se puede escribir: se ofrece guardarlo como uno
        nuevo, descartar las opciones que fija o cancelar.
        """
        factory_domains: list[str] = []
        if is_factory(DOMAIN_LATENCY, self._current_mode()) and is_modified(
            DOMAIN_LATENCY, self._config, self._current_mode(), self._latency_live()
        ):
            factory_domains.append(DOMAIN_LATENCY)
        if is_factory(DOMAIN_DECODE, self._current_tx_preset()) and is_modified(
            DOMAIN_DECODE, self._config, self._current_tx_preset(), self._tx_live()
        ):
            factory_domains.append(DOMAIN_DECODE)
        app_preset = self._config.get("app_preset")
        app_modified = bool(app_preset) and is_modified(
            DOMAIN_APP, self._config, str(app_preset), self._app_preset_live()
        )
        if app_modified and is_factory(DOMAIN_APP, str(app_preset)):
            factory_domains.append(DOMAIN_APP)

        if factory_domains:
            labels = ", ".join(
                preset_label(domain, self._active_preset_id(domain))
                for domain in factory_domains
            )
            box = QtWidgets.QMessageBox(self)
            box.setObjectName("SettingsDialog")
            box.setStyleSheet(_SETTINGS_QSS)
            box.setIcon(QtWidgets.QMessageBox.Icon.Question)
            box.setWindowTitle("Preset de fábrica modificado")
            box.setText(f"Has modificado el preset de fábrica «{labels}».")
            box.setInformativeText(
                "Los presets de fábrica no se pueden modificar. ¿Qué hago con "
                "los cambios?"
            )
            save_btn = box.addButton(
                "Guardar como nuevo…",
                QtWidgets.QMessageBox.ButtonRole.AcceptRole,
            )
            discard_btn = box.addButton(
                "Descartar cambios", QtWidgets.QMessageBox.ButtonRole.DestructiveRole
            )
            box.addButton("Cancelar", QtWidgets.QMessageBox.ButtonRole.RejectRole)
            box.setDefaultButton(save_btn)
            box.exec()
            clicked = box.clickedButton()
            if clicked is save_btn:
                if not self._save_factory_changes_as(factory_domains):
                    return False
            elif clicked is discard_btn:
                self._discard_factory_changes(factory_domains)
            else:
                return False

        self._commit_latency_draft()
        self._commit_translation_draft()
        return True

    def _active_preset_id(self, domain: str) -> str | None:
        if domain == DOMAIN_APP:
            value = self._config.get("app_preset")
            return str(value) if value else None
        if domain == DOMAIN_LATENCY:
            return self._current_mode()
        return self._current_tx_preset()

    def _save_factory_changes_as(self, domains: list[str]) -> bool:
        """Crea un preset nuevo por dominio con los valores que hay en pantalla."""
        titles = {
            DOMAIN_APP: "Guardar preset general",
            DOMAIN_LATENCY: "Guardar preset de latencia",
            DOMAIN_DECODE: "Guardar preset de decoding",
        }
        base_name: str | None = None
        saved_any = False
        for domain in domains:
            suffix = "" if len(domains) == 1 else f" ({self._active_preset_id(domain)})"
            name = ask_preset_name(
                self,
                title=titles[domain] + suffix,
            )
            if name is None:
                if not saved_any:
                    return False
                continue
            if domain != DOMAIN_APP and base_name is not None:
                # Un nombre para los tres: los presets parciales se llaman igual
                # en sus respectivos catálogos, sin colisionar entre sí.
                name = base_name
            elif base_name is None:
                base_name = name
            try:
                if domain == DOMAIN_APP:
                    if self._controller is None:
                        return False
                    self._controller.save_app_preset_as_from_settings(name, self)
                elif domain == DOMAIN_LATENCY:
                    self._config = save_latency_preset_as(
                        self._config, name, self._latency_live()
                    )
                else:
                    self._config = add_translation_user_preset(
                        self._config, name, self._spins_decode()
                    )
            except ValueError as exc:
                QtWidgets.QMessageBox.warning(self, "Preset", str(exc))
                return False
            saved_any = True
        self._refresh_latency_preset_combo()
        self._refresh_translation_preset_combo()
        self._refresh_app_preset_bar()
        return True

    def _discard_factory_changes(self, domains: list[str]) -> None:
        for domain in domains:
            if domain == DOMAIN_APP:
                if self._controller is not None:
                    preset_id = self._active_preset_id(DOMAIN_APP)
                    if preset_id:
                        self._controller.apply_app_preset_from_settings(preset_id, self)
            elif domain == DOMAIN_LATENCY:
                self._load_latency_preset_into_sliders(self._current_mode())
            else:
                self._load_translation_decode_into_spins()
        self._sync_all_preset_actions()

    def _on_show_partials_toggled(self, checked: bool) -> None:
        if self._guarding_mode_conflict:
            return
        if checked:
            return
        self._ensure_modes_compatible(changed="partials")

    def _on_sticky_mode_changed(self, _index: int) -> None:
        if self._guarding_mode_conflict:
            return
        sticky = str(self.tx_sticky_mode.currentData() or "off")
        if not self._ensure_modes_compatible(changed="sticky"):
            return
        self._prev_sticky_mode = sticky

    def _ensure_modes_compatible(self, *, changed: str) -> bool:
        conflict = detect_partials_sticky_conflict(
            show_partials=self.captions_show_partials.isChecked(),
            sticky_mode=str(self.tx_sticky_mode.currentData() or "off"),
            changed=changed,
        )
        if conflict is None:
            return True
        if not self._prompt_mode_conflict(conflict):
            self._revert_mode_change(changed)
            return False
        self._apply_mode_conflict_fix(conflict)
        return True

    def _prompt_mode_conflict(self, conflict: SettingsModeConflict) -> bool:
        """True = aplicar arreglo compatible; False = cancelar."""
        box = QtWidgets.QMessageBox(self)
        box.setObjectName("SettingsDialog")
        box.setStyleSheet(_SETTINGS_QSS)
        box.setIcon(QtWidgets.QMessageBox.Icon.Warning)
        box.setWindowTitle("Opciones incompatibles")
        box.setText("Combinación no compatible")
        box.setInformativeText(conflict.message)
        cancel_btn = box.addButton(
            "Cancelar", QtWidgets.QMessageBox.ButtonRole.RejectRole
        )
        fix_btn = box.addButton(
            conflict.fix_button_label,
            QtWidgets.QMessageBox.ButtonRole.AcceptRole,
        )
        box.setDefaultButton(fix_btn)
        box.exec()
        clicked = box.clickedButton()
        return clicked is fix_btn and clicked is not cancel_btn

    def _revert_mode_change(self, changed: str) -> None:
        self._guarding_mode_conflict = True
        try:
            if changed == "partials":
                self.captions_show_partials.setChecked(True)
            elif changed == "sticky":
                idx = self.tx_sticky_mode.findData(self._prev_sticky_mode)
                self.tx_sticky_mode.setCurrentIndex(idx if idx >= 0 else 0)
        finally:
            self._guarding_mode_conflict = False

    def _apply_mode_conflict_fix(self, conflict: SettingsModeConflict) -> None:
        self._guarding_mode_conflict = True
        try:
            if conflict.fix_show_partials is not None:
                self.captions_show_partials.setChecked(conflict.fix_show_partials)
            if conflict.fix_sticky_mode is not None:
                idx = self.tx_sticky_mode.findData(conflict.fix_sticky_mode)
                if idx >= 0:
                    self.tx_sticky_mode.setCurrentIndex(idx)
                    self._prev_sticky_mode = conflict.fix_sticky_mode
        finally:
            self._guarding_mode_conflict = False

    def _wrap_scroll(self, body: QtWidgets.QWidget) -> QtWidgets.QScrollArea:
        scroll = QtWidgets.QScrollArea()
        scroll.setObjectName("SettingsScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        scroll.setWidget(body)
        return scroll

    def _new_tab_body(self) -> tuple[QtWidgets.QWidget, QtWidgets.QVBoxLayout]:
        body = QtWidgets.QWidget()
        body.setObjectName("SettingsBody")
        layout = QtWidgets.QVBoxLayout(body)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(18)
        return body, layout

    def _build_general_tab(self, config: dict[str, Any]) -> QtWidgets.QWidget:
        body, body_layout = self._new_tab_body()

        capture = _Section("Captura")
        self.language = QtWidgets.QComboBox()
        _size_combo(self.language, "md")
        capture.add_row("Idioma", self.language)
        self._refresh_language_combo(str(config.get("language", "en")))

        self.model = QtWidgets.QComboBox()
        for name in ["small", "medium", "large-v3-turbo", "large-v3"]:
            self.model.addItem(name)
        current_model = str(config.get("model", "medium"))
        idx = self.model.findText(current_model)
        self.model.setCurrentIndex(idx if idx >= 0 else 1)
        _size_combo(self.model, "sm")
        capture.add_row("Modelo Whisper", self.model)

        self.compute_type = QtWidgets.QComboBox()
        for value in COMPUTE_TYPES:
            self.compute_type.addItem(COMPUTE_TYPE_LABELS[value], value)
        compute = str(config.get("compute_type", "float16"))
        compute_idx = self.compute_type.findData(compute)
        self.compute_type.setCurrentIndex(compute_idx if compute_idx >= 0 else 0)
        _size_combo(self.compute_type, "md")
        capture.add_row("Precisión GPU", self.compute_type, HINT_COMPUTE_TYPE)

        self.audio = QtWidgets.QComboBox()
        audio_error: str | None = None
        try:
            sources = list_audio_sources()
        except Exception as exc:
            sources = []
            audio_error = str(exc)
        for source in sources:
            if not source.id:
                continue
            self.audio.addItem(source.label, source.id)
        current = str(config.get("audio_monitor") or "")
        if current:
            pos = self.audio.findData(current)
            if pos >= 0:
                self.audio.setCurrentIndex(pos)
            else:
                self.audio.insertItem(0, describe_audio_source(current).label, current)
                self.audio.setCurrentIndex(0)
        _size_combo(self.audio, "lg")
        capture.add_row("Audio (monitor)", self.audio)
        if audio_error:
            err = QtWidgets.QLabel(f"No se pudo listar audio: {audio_error}")
            err.setObjectName("FieldHint")
            err.setWordWrap(True)
            capture.body.addRow("", err)
        body_layout.addWidget(capture)

        captions = _Section("Subtítulos")
        self.captions_show_partials = MarqueeToggle("Mostrar texto parcial")
        self.captions_show_partials.setChecked(
            bool(config.get("captions_show_partials", False))
        )
        captions.body.addRow(
            _with_side_hint(self.captions_show_partials, HINT_SHOW_PARTIALS)
        )
        self.captions_allow_rewrite = MarqueeToggle(
            "Permitir reescritura de lo ya mostrado"
        )
        self.captions_allow_rewrite.setChecked(
            bool(config.get("captions_allow_rewrite", True))
        )
        captions.body.addRow(
            _with_side_hint(self.captions_allow_rewrite, HINT_ALLOW_REWRITE)
        )
        body_layout.addWidget(captions)
        body_layout.addStretch(1)
        return self._wrap_scroll(body)

    def _build_latency_tab(self, config: dict[str, Any]) -> QtWidgets.QWidget:
        body, body_layout = self._new_tab_body()

        latency = _Section("Latencia")
        self.latency_preset = QtWidgets.QComboBox()
        _size_combo(self.latency_preset, "lg")
        self.latency_preset.currentIndexChanged.connect(self._on_latency_preset_changed)
        self.latency_actions = PresetActionBar()
        self.latency_actions.view_requested.connect(self._view_latency_values)
        self.latency_actions.save_requested.connect(self._save_latency_clicked)
        self.latency_actions.save_as_requested.connect(self._save_latency_as)
        self.latency_actions.restore_requested.connect(self._restore_latency_preset)
        self.latency_actions.delete_requested.connect(self._delete_latency_preset)
        latency.add_row(
            "Preset",
            _combo_actions_row(self.latency_preset, self.latency_actions),
            HINT_LATENCY_MODE,
        )

        self.confidence = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.confidence.setRange(1, 5)
        self.confidence_value = QtWidgets.QLabel("2")
        self.confidence_value.setObjectName("ValueChip")
        self.confidence_value.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter
        )
        conf_wrap = _slider_value_row(self.confidence, self.confidence_value)
        latency.add_row("Confianza", conf_wrap, HINT_CONFIDENCE)
        self.confidence.valueChanged.connect(self._on_confidence_changed)

        self.max_latency = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.max_latency.setRange(2, 30)  # 0.2–3.0 s en décimas
        self.max_latency_value = QtWidgets.QLabel("3.0")
        self.max_latency_value.setObjectName("ValueChip")
        self.max_latency_value.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter
        )
        lat_wrap = _slider_value_row(self.max_latency, self.max_latency_value)
        latency.add_row("Techo (s)", lat_wrap, HINT_MAX_LATENCY)
        self.max_latency.valueChanged.connect(self._on_max_latency_changed)
        body_layout.addWidget(latency)
        body_layout.addStretch(1)
        return self._wrap_scroll(body)

    def _build_appearance_tab(self, config: dict[str, Any]) -> QtWidgets.QWidget:
        body, body_layout = self._new_tab_body()

        self._preview_stage = QtWidgets.QFrame()
        self._preview_stage.setObjectName("PreviewStage")
        preview_layout = QtWidgets.QVBoxLayout(self._preview_stage)
        preview_layout.setContentsMargins(16, 14, 16, 16)
        preview_layout.setSpacing(10)
        preview_hint = QtWidgets.QLabel("VISTA PREVIA")
        preview_hint.setObjectName("PreviewHint")
        preview_layout.addWidget(preview_hint)
        self._preview_caption = QtWidgets.QLabel(
            "Hello, this is a live caption preview"
        )
        self._preview_caption.setObjectName("PreviewCaption")
        self._preview_caption.setWordWrap(True)
        self._preview_caption.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        preview_layout.addWidget(self._preview_caption)
        body_layout.addWidget(self._preview_stage)

        look = _Section("Texto y colores")
        self.font_family = QtWidgets.QComboBox()
        _size_combo(self.font_family, "lg")
        self._refresh_font_family_combo(str(config.get("font_family", "")))
        self.font_family.currentIndexChanged.connect(self._on_font_style_changed)
        look.add_row("Tipo de letra", self.font_family, HINT_FONT_FAMILY)

        self.font_weight = QtWidgets.QComboBox()
        for weight in FONT_WEIGHT_MODES:
            self.font_weight.addItem(FONT_WEIGHT_LABELS[weight], weight)
        _size_combo(self.font_weight, "md")
        self._set_font_weight(str(config.get("font_weight", "semibold")))
        self.font_weight.currentIndexChanged.connect(self._on_font_style_changed)
        look.add_row("Grosor", self.font_weight, HINT_FONT_WEIGHT)

        font_wrap, self.font_size, self.font_size_value = _int_slider_row(
            low=10, high=100, value=int(config.get("font_size", 28))
        )
        self.font_size.valueChanged.connect(self._on_font_size_changed)
        look.add_row("Tamaño de texto", font_wrap)

        pad_wrap, self.padding, self.padding_value = _int_slider_row(
            low=0, high=100, value=int(config.get("padding", 24))
        )
        self.padding.valueChanged.connect(self._on_padding_changed)
        look.add_row("Padding", pad_wrap)

        align_wrap = QtWidgets.QWidget()
        align_row = QtWidgets.QHBoxLayout(align_wrap)
        align_row.setContentsMargins(0, 0, 0, 0)
        align_row.setSpacing(8)
        align_row.setAlignment(QtCore.Qt.AlignmentFlag.AlignVCenter)
        self._align_group = QtWidgets.QButtonGroup(self)
        self._align_group.setExclusive(True)
        self.align_left_btn = QtWidgets.QPushButton()
        self.align_left_btn.setObjectName("AlignToggle")
        self.align_left_btn.setCheckable(True)
        self.align_left_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.align_left_btn.setIcon(_alignment_icon("left"))
        self.align_left_btn.setIconSize(QtCore.QSize(16, 16))
        self.align_left_btn.setFixedSize(CONTROL_HEIGHT, CONTROL_HEIGHT)
        self.align_left_btn.setAccessibleName(TEXT_ALIGN_LABELS["left"])
        self.align_left_btn.setToolTip(TEXT_ALIGN_LABELS["left"])
        self.align_center_btn = QtWidgets.QPushButton()
        self.align_center_btn.setObjectName("AlignToggle")
        self.align_center_btn.setCheckable(True)
        self.align_center_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.align_center_btn.setIcon(_alignment_icon("center"))
        self.align_center_btn.setIconSize(QtCore.QSize(16, 16))
        self.align_center_btn.setFixedSize(CONTROL_HEIGHT, CONTROL_HEIGHT)
        self.align_center_btn.setAccessibleName(TEXT_ALIGN_LABELS["center"])
        self.align_center_btn.setToolTip(TEXT_ALIGN_LABELS["center"])
        self._align_group.addButton(self.align_left_btn)
        self._align_group.addButton(self.align_center_btn)
        align_row.addWidget(self.align_left_btn)
        align_row.addWidget(self.align_center_btn)
        align_row.addStretch(1)
        self._set_text_align(str(config.get("text_align", "center")))
        self.align_left_btn.toggled.connect(self._on_align_toggled)
        self.align_center_btn.toggled.connect(self._on_align_toggled)
        look.add_row("Alineación", align_wrap)

        font_color_wrap = QtWidgets.QWidget()
        font_color_row = QtWidgets.QHBoxLayout(font_color_wrap)
        font_color_row.setContentsMargins(0, 0, 0, 0)
        font_color_row.setSpacing(10)
        font_color_row.setAlignment(QtCore.Qt.AlignmentFlag.AlignVCenter)
        self.font_color_btn = QtWidgets.QPushButton()
        self.font_color_btn.setObjectName("ColorSwatch")
        self.font_color_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.font_color_btn.setAccessibleName("Color de texto")
        self.font_color_btn.setToolTip("Color de texto")
        _set_control_height(self.font_color_btn)
        self.font_color_hex = QtWidgets.QLabel()
        self.font_color_hex.setObjectName("ColorHex")
        self.font_color_hex.setFixedHeight(CONTROL_HEIGHT)
        self.font_color_hex.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter
        )
        self._set_swatch(
            self.font_color_btn,
            self.font_color_hex,
            str(config.get("font_color", "#ffffff")),
        )
        self.font_color_btn.clicked.connect(self._pick_font_color)
        font_color_row.addWidget(self.font_color_btn)
        font_color_row.addWidget(self.font_color_hex)
        font_color_row.addStretch(1)
        look.add_row("Color de texto", font_color_wrap)

        bg_color_wrap = QtWidgets.QWidget()
        bg_color_row = QtWidgets.QHBoxLayout(bg_color_wrap)
        bg_color_row.setContentsMargins(0, 0, 0, 0)
        bg_color_row.setSpacing(10)
        bg_color_row.setAlignment(QtCore.Qt.AlignmentFlag.AlignVCenter)
        self.bg_color_btn = QtWidgets.QPushButton()
        self.bg_color_btn.setObjectName("ColorSwatch")
        self.bg_color_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.bg_color_btn.setAccessibleName("Color de fondo")
        self.bg_color_btn.setToolTip("Color de fondo")
        _set_control_height(self.bg_color_btn)
        self.bg_color_hex = QtWidgets.QLabel()
        self.bg_color_hex.setObjectName("ColorHex")
        self.bg_color_hex.setFixedHeight(CONTROL_HEIGHT)
        self.bg_color_hex.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter
        )
        self._set_swatch(
            self.bg_color_btn,
            self.bg_color_hex,
            str(config.get("bg_color", "#000000")),
        )
        self.bg_color_btn.clicked.connect(self._pick_bg_color)
        bg_color_row.addWidget(self.bg_color_btn)
        bg_color_row.addWidget(self.bg_color_hex)
        bg_color_row.addStretch(1)
        look.add_row("Color de fondo", bg_color_wrap)

        self.alpha = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.alpha.setRange(5, 100)
        self.alpha.setValue(int(float(config.get("bg_alpha", 0.55)) * 100))
        self.alpha_value = QtWidgets.QLabel(f"{self.alpha.value()}%")
        self.alpha_value.setObjectName("ValueChip")
        self.alpha_value.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter
        )
        alpha_wrap = _slider_value_row(self.alpha, self.alpha_value)
        self.alpha.valueChanged.connect(self._on_alpha_changed)
        look.add_row("Transparencia", alpha_wrap)
        body_layout.addWidget(look)
        body_layout.addStretch(1)
        return self._wrap_scroll(body)

    def _refresh_translator_model_combo(self) -> None:
        """Sincroniza el combo con la config, conservando un repo propio si lo hay."""
        current = str(self._config.get("translator_model") or DEFAULT_TRANSLATOR_MODEL)
        idx = self.translator_model.findData(current)
        if idx < 0:
            self.translator_model.addItem(current, current)
            idx = self.translator_model.findData(current)
        self.translator_model.setCurrentIndex(idx)

    def _build_translation_tab(self, config: dict[str, Any]) -> QtWidgets.QWidget:
        body, body_layout = self._new_tab_body()

        engine = _Section("Motor")
        self.translator_model = QtWidgets.QComboBox()
        for alias in TRANSLATOR_MODEL_IDS:
            self.translator_model.addItem(TRANSLATOR_MODEL_LABELS[alias], alias)
        self._refresh_translator_model_combo()
        _size_combo(self.translator_model, "lg")
        engine.add_row("Traductor", self.translator_model, HINT_TRANSLATOR_MODEL)
        body_layout.addWidget(engine)

        display = _Section("Visualización")
        self.second_line_mode = QtWidgets.QComboBox()
        for mode_key in SECOND_LINE_MODES:
            self.second_line_mode.addItem(SECOND_LINE_LABELS[mode_key], mode_key)
        current_mode = str(config.get("second_line_mode", "live_asr"))
        idx = self.second_line_mode.findData(current_mode)
        self.second_line_mode.setCurrentIndex(idx if idx >= 0 else 0)
        _size_combo(self.second_line_mode, "md")
        display.add_row("Segunda línea", self.second_line_mode, HINT_SECOND_LINE)

        self.tx_sticky_mode = QtWidgets.QComboBox()
        for mode_key in TRANSLATION_STICKY_MODES:
            self.tx_sticky_mode.addItem(TRANSLATION_STICKY_LABELS[mode_key], mode_key)
        sticky = str(config.get("translation_sticky_mode", "off"))
        sticky_idx = self.tx_sticky_mode.findData(sticky)
        self.tx_sticky_mode.setCurrentIndex(sticky_idx if sticky_idx >= 0 else 0)
        _size_combo(self.tx_sticky_mode, "md")
        display.add_row("Modo sticky", self.tx_sticky_mode, HINT_TX_STICKY)
        body_layout.addWidget(display)

        quality = _Section("Calidad de decoding")
        self.tx_preset = QtWidgets.QComboBox()
        self.tx_preset.currentIndexChanged.connect(self._on_tx_preset_changed)
        _size_combo(self.tx_preset, "lg")

        self.tx_actions = PresetActionBar()
        self.tx_actions.view_requested.connect(self._view_translation_values)
        self.tx_actions.save_requested.connect(self._save_translation_clicked)
        self.tx_actions.save_as_requested.connect(self._save_translation_preset_as)
        self.tx_actions.restore_requested.connect(self._restore_translation_preset)
        self.tx_actions.delete_requested.connect(self._delete_translation_preset)
        self.tx_view_btn = self.tx_actions.view_btn
        self.tx_save_btn = self.tx_actions.save_btn
        self.tx_save_as_btn = self.tx_actions.save_as_btn
        self.tx_restore_btn = self.tx_actions.restore_btn
        self.tx_delete_btn = self.tx_actions.delete_btn

        quality.add_row(
            "Preset",
            _combo_actions_row(self.tx_preset, self.tx_actions),
        )

        # Rangos = clamps de config; length_penalty en décimas (0.6–1.5, paso 0.1).
        beam_wrap, self.tx_beam, self.tx_beam_value = _int_slider_row(
            low=1, high=8, value=4
        )
        self.tx_beam.valueChanged.connect(self._on_tx_decode_slider_changed)
        quality.add_row("Beam size", beam_wrap, HINT_TX_BEAM)

        length_wrap, self.tx_length, self.tx_length_value = _int_slider_row(
            low=6,
            high=15,
            value=10,
            format_value=lambda v: f"{v / 10:.1f}",
            chip_min_width=40,
        )
        self.tx_length.valueChanged.connect(self._on_tx_decode_slider_changed)
        quality.add_row("Length penalty", length_wrap, HINT_TX_LENGTH)

        ngram_wrap, self.tx_ngram, self.tx_ngram_value = _int_slider_row(
            low=0, high=5, value=3
        )
        self.tx_ngram.valueChanged.connect(self._on_tx_decode_slider_changed)
        quality.add_row("No-repeat n-gram", ngram_wrap, HINT_TX_NGRAM)
        body_layout.addWidget(quality)
        body_layout.addStretch(1)
        return self._wrap_scroll(body)

    def _set_swatch(
        self,
        button: QtWidgets.QPushButton,
        hex_label: QtWidgets.QLabel,
        hex_color: str,
    ) -> None:
        color = QtGui.QColor(hex_color)
        if not color.isValid():
            color = QtGui.QColor("#000000")
            hex_color = color.name()
        button.setProperty("hexColor", hex_color)
        button.setText("")
        hex_label.setText(hex_color)
        button.setStyleSheet(
            f"""
            QPushButton#ColorSwatch {{
                background: {hex_color};
                border: 1px solid #4a5164;
                border-radius: 6px;
                min-width: 28px;
                max-width: 28px;
                min-height: 28px;
                max-height: 28px;
                padding: 0;
            }}
            QPushButton#ColorSwatch:hover {{
                border-color: #e8b86d;
            }}
            """
        )

    def _swatch_hex(self, button: QtWidgets.QPushButton) -> str:
        raw = button.property("hexColor")
        if isinstance(raw, str) and QtGui.QColor(raw).isValid():
            return raw
        return "#000000"

    def _current_text_align(self) -> str:
        if self.align_left_btn.isChecked():
            return "left"
        return "center"

    def _set_text_align(self, mode: str) -> None:
        want_left = str(mode) == "left"
        self.align_left_btn.blockSignals(True)
        self.align_center_btn.blockSignals(True)
        self.align_left_btn.setChecked(want_left)
        self.align_center_btn.setChecked(not want_left)
        self.align_left_btn.blockSignals(False)
        self.align_center_btn.blockSignals(False)

    def _on_align_toggled(self, checked: bool) -> None:
        if not checked:
            return
        self._refresh_preview()

    def _add_font_family_item(self, family: str) -> None:
        """Cada ítem se pinta con su propia familia: el desplegable es la muestra."""
        self.font_family.addItem(family, family)
        self.font_family.setItemData(
            self.font_family.count() - 1,
            QtGui.QFont(family, 11),
            QtCore.Qt.ItemDataRole.FontRole,
        )

    def _refresh_font_family_combo(self, preferred: str | None = None) -> None:
        current = preferred
        if current is None:
            current = str(self.font_family.currentData() or "")
        self.font_family.blockSignals(True)
        self.font_family.clear()
        self.font_family.addItem("Sistema (por defecto)", "")
        curated, rest = available_caption_fonts()
        for family in curated:
            self._add_font_family_item(family)
        if rest:
            self.font_family.insertSeparator(self.font_family.count())
            for family in rest:
                self._add_font_family_item(family)
        want = str(current or "").strip()
        idx = self.font_family.findData(want)
        if idx < 0 and want:
            # Familia de la config que no está en el sistema: conservarla al guardar.
            self.font_family.addItem(f"{want} (no instalada)", want)
            idx = self.font_family.count() - 1
        self.font_family.setCurrentIndex(max(idx, 0))
        self.font_family.blockSignals(False)

    def _current_font_family(self) -> str:
        return str(self.font_family.currentData() or "")

    def _current_font_weight(self) -> str:
        weight = str(self.font_weight.currentData() or "semibold")
        return weight if weight in FONT_WEIGHT_MODES else "semibold"

    def _set_font_weight(self, weight: str) -> None:
        idx = self.font_weight.findData(str(weight or "semibold"))
        if idx < 0:
            idx = self.font_weight.findData("semibold")
        self.font_weight.blockSignals(True)
        self.font_weight.setCurrentIndex(max(idx, 0))
        self.font_weight.blockSignals(False)

    def _on_font_style_changed(self, _index: int = 0) -> None:
        self._refresh_preview()

    def _on_font_size_changed(self, value: int) -> None:
        self.font_size_value.setText(str(value))
        self._refresh_preview()

    def _on_padding_changed(self, value: int) -> None:
        self.padding_value.setText(str(value))
        self._refresh_preview()

    def _preview_alignment(self) -> QtCore.Qt.AlignmentFlag:
        if self._current_text_align() == "left":
            return QtCore.Qt.AlignmentFlag.AlignLeft
        return QtCore.Qt.AlignmentFlag.AlignHCenter

    def _refresh_preview(self) -> None:
        font_size = int(self.font_size.value())
        font_color = self._swatch_hex(self.font_color_btn)
        bg = QtGui.QColor(self._swatch_hex(self.bg_color_btn))
        if not bg.isValid():
            bg = QtGui.QColor("#000000")
        alpha = self.alpha.value() / 100.0
        bg.setAlphaF(alpha)
        pad = int(self.padding.value())
        rgba = f"rgba({bg.red()}, {bg.green()}, {bg.blue()}, {bg.alphaF():.2f})"
        self._preview_caption.setAlignment(
            self._preview_alignment() | QtCore.Qt.AlignmentFlag.AlignVCenter
        )
        self._preview_caption.setStyleSheet(
            f"""
            QLabel#PreviewCaption {{
                color: {font_color};
                font-size: {font_size}px;
                {font_family_qss(self._current_font_family())}
                font-weight: {font_weight_css(self._current_font_weight())};
                background: {rgba};
                border-radius: 12px;
                padding: {pad}px;
            }}
            """
        )

    def _on_alpha_changed(self, value: int) -> None:
        self.alpha_value.setText(f"{value}%")
        self._refresh_preview()

    def _refresh_language_combo(self, preferred: str | None = None) -> None:
        current = preferred
        if current is None and self.language.count() > 0:
            current = str(self.language.currentData() or "")
        if not current:
            current = str(self._config.get("language", "en"))
        current = str(current).strip().lower() or "en"
        self.language.blockSignals(True)
        self.language.clear()
        for code in AVAILABLE_LANGUAGES:
            self.language.addItem(language_label(code), code)
        idx = self.language.findData(current)
        self.language.setCurrentIndex(idx if idx >= 0 else 0)
        self.language.blockSignals(False)

    def _current_mode(self) -> str:
        data = self.latency_preset.currentData()
        return str(data) if data else "stable"

    def _profiles(self) -> dict[str, dict[str, float | int]]:
        profiles = self._config.setdefault(
            "latency_profiles", deepcopy(LATENCY_FACTORY_PRESETS)
        )
        if not isinstance(profiles, dict):
            profiles = deepcopy(LATENCY_FACTORY_PRESETS)
            self._config["latency_profiles"] = profiles
        return profiles

    def _saved_latency_profile(self, preset_id: str) -> dict[str, Any]:
        profile = self._profiles().get(preset_id)
        if isinstance(profile, dict):
            return dict(profile)
        return dict(LATENCY_FACTORY_PRESETS["stable"])

    def _load_latency_preset_into_sliders(self, preset_id: str) -> None:
        """Carga el preset en los sliders y reinicia el borrador."""
        profile = self._saved_latency_profile(preset_id)
        self._latency_draft = dict(profile)
        self._loading_profile = True
        try:
            self.confidence.setValue(int(profile.get("agreement_n", 2)))
            self.confidence_value.setText(str(int(profile.get("agreement_n", 2))))
            tenths = int(round(float(profile.get("max_latency_sec", 0.8)) * 10))
            self.max_latency.setValue(tenths)
            self.max_latency_value.setText(f"{tenths / 10:.1f}")
        finally:
            self._loading_profile = False

    def _sync_latency_draft(self) -> None:
        """Sliders → borrador. Solo toca lo que la UI deja editar."""
        if self._loading_profile:
            return
        draft = dict(self._latency_draft or {})
        draft["agreement_n"] = int(self.confidence.value())
        draft["max_latency_sec"] = self.max_latency.value() / 10.0
        draft.setdefault(
            "min_chunk_seconds",
            float(LATENCY_FACTORY_PRESETS["stable"]["min_chunk_seconds"]),
        )
        draft.setdefault(
            "beam_size", int(LATENCY_FACTORY_PRESETS["stable"]["beam_size"])
        )
        self._latency_draft = draft

    def _latency_live(self) -> dict[str, Any]:
        self._sync_latency_draft()
        return dict(self._latency_draft or {})

    def _sync_latency_actions(self) -> None:
        preset_id = self._current_mode()
        state = ActionState(
            active=preset_id in self._profiles(),
            modified=is_modified(
                DOMAIN_LATENCY, self._config, preset_id, self._latency_live()
            ),
            deletable=is_deletable(DOMAIN_LATENCY, preset_id),
            factory=is_factory(DOMAIN_LATENCY, preset_id),
        )
        self.latency_actions.configure(state)
        self.latency_actions.setEnabled(True)

    def _refresh_latency_preset_combo(self) -> None:
        current = str(self._config.get("latency_mode", "stable"))
        self.latency_preset.blockSignals(True)
        try:
            self.latency_preset.clear()
            for preset_id in domain_preset_ids(DOMAIN_LATENCY, self._config):
                self.latency_preset.addItem(latency_preset_label(preset_id), preset_id)
            idx = self.latency_preset.findData(current)
            self.latency_preset.setCurrentIndex(idx if idx >= 0 else 0)
        finally:
            self.latency_preset.blockSignals(False)
        self._config["latency_mode"] = self._current_mode()
        self._load_latency_preset_into_sliders(self._current_mode())
        self._sync_latency_actions()

    def _on_latency_preset_changed(self) -> None:
        if self._loading_profile:
            return
        preset_id = self._current_mode()
        self._config["latency_mode"] = preset_id
        self._load_latency_preset_into_sliders(preset_id)
        self._sync_latency_actions()

    def _on_confidence_changed(self, value: int) -> None:
        self.confidence_value.setText(str(value))
        self._sync_latency_draft()
        self._sync_latency_actions()

    def _on_max_latency_changed(self, value: int) -> None:
        self.max_latency_value.setText(f"{value / 10:.1f}")
        self._sync_latency_draft()
        self._sync_latency_actions()

    def _view_latency_values(self) -> None:
        preset_id = self._current_mode()
        rows = describe(DOMAIN_LATENCY, self._config, preset_id)
        note = None
        if is_factory(DOMAIN_LATENCY, preset_id):
            note = "Preset de fábrica: sus valores no se pueden modificar."
        PresetValuesDialog(
            self,
            title=f"Valores de «{preset_label(DOMAIN_LATENCY, preset_id)}»",
            subtitle="Opciones que aplica este preset de latencia.",
            rows=rows,
            note=note,
        ).exec()

    def _save_latency_clicked(self) -> None:
        preset_id = self._current_mode()
        factory = is_factory(DOMAIN_LATENCY, preset_id)
        choice = ask_save_choice(
            self,
            preset_label=preset_label(DOMAIN_LATENCY, preset_id),
            is_factory=factory,
        )
        if choice == CHOICE_NEW:
            self._save_latency_as()
            return
        if choice != CHOICE_OVERWRITE or factory:
            return
        try:
            self._config = save_latency_preset(
                self._config, preset_id, self._latency_live()
            )
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, "Preset", str(exc))
            return
        self._refresh_latency_preset_combo()
        self._persist_preset_library()

    def _save_latency_as(self) -> None:
        name = ask_preset_name(self, title="Guardar preset de latencia")
        if name is None:
            return
        try:
            self._config = save_latency_preset_as(
                self._config, name, self._latency_live()
            )
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, "Preset", str(exc))
            return
        self._refresh_latency_preset_combo()
        self._persist_preset_library()

    def _restore_latency_preset(self) -> None:
        preset_id = self._current_mode()
        self._load_latency_preset_into_sliders(preset_id)
        self._sync_latency_actions()

    def _delete_latency_preset(self) -> None:
        preset_id = self._current_mode()
        if not is_deletable(DOMAIN_LATENCY, preset_id):
            return
        answer = QtWidgets.QMessageBox.question(
            self,
            "Borrar preset",
            f"¿Borrar el preset «{preset_id}»?",
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        try:
            self._config = delete_latency_user_preset(self._config, preset_id)
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, "Preset", str(exc))
            return
        self._refresh_latency_preset_combo()
        self._persist_preset_library()

    def _commit_latency_draft(self) -> None:
        """Persiste el borrador si el preset es propio; fábrica nunca se toca."""
        preset_id = self._current_mode()
        if is_factory(DOMAIN_LATENCY, preset_id):
            self._config["latency_mode"] = preset_id
            return
        if not is_modified(
            DOMAIN_LATENCY, self._config, preset_id, self._latency_live()
        ):
            self._config["latency_mode"] = preset_id
            return
        try:
            self._config = save_latency_preset(
                self._config, preset_id, self._latency_live()
            )
        except ValueError:
            self._config["latency_mode"] = preset_id

    def _current_tx_preset(self) -> str:
        data = self.tx_preset.currentData()
        return str(data) if data else "balanced"

    def _translation_profiles(self) -> dict[str, dict[str, float | int]]:
        profiles = self._config.setdefault(
            "translation_profiles",
            {
                **deepcopy(TRANSLATION_FACTORY_PRESETS),
                "custom": deepcopy(TRANSLATION_FACTORY_PRESETS["balanced"]),
            },
        )
        if not isinstance(profiles, dict):
            profiles = {
                **deepcopy(TRANSLATION_FACTORY_PRESETS),
                "custom": deepcopy(TRANSLATION_FACTORY_PRESETS["balanced"]),
            }
            self._config["translation_profiles"] = profiles
        return profiles

    def _spins_decode(self) -> dict[str, float | int]:
        return {
            "beam_size": int(self.tx_beam.value()),
            "length_penalty": self.tx_length.value() / 10.0,
            "no_repeat_ngram_size": int(self.tx_ngram.value()),
        }

    def _sync_tx_decode_chips(self) -> None:
        self.tx_beam_value.setText(str(self.tx_beam.value()))
        self.tx_length_value.setText(f"{self.tx_length.value() / 10:.1f}")
        self.tx_ngram_value.setText(str(self.tx_ngram.value()))

    def _refresh_translation_preset_combo(self) -> None:
        current = str(self._config.get("translation_decode_preset", "balanced"))
        self.tx_preset.blockSignals(True)
        try:
            self.tx_preset.clear()
            for preset_id in domain_preset_ids(DOMAIN_DECODE, self._config):
                self.tx_preset.addItem(
                    TRANSLATION_PRESET_LABELS.get(preset_id, preset_id), preset_id
                )
            idx = self.tx_preset.findData(current)
            self.tx_preset.setCurrentIndex(
                idx if idx >= 0 else self.tx_preset.findData("balanced")
            )
        finally:
            self.tx_preset.blockSignals(False)
        self._config["translation_decode_preset"] = self._current_tx_preset()
        self._load_translation_decode_into_spins()
        self._sync_translation_preset_actions()

    def _load_translation_decode_into_spins(self) -> None:
        """Carga el preset en los sliders y reinicia el borrador."""
        preset = self._current_tx_preset()
        saved = self._translation_profiles().get(preset)
        decode = (
            dict(saved)
            if isinstance(saved, dict)
            else dict(TRANSLATION_FACTORY_PRESETS.get(preset, {}))
        )
        if not decode:
            decode = dict(TRANSLATION_FACTORY_PRESETS["balanced"])
        self._tx_draft = {
            "beam_size": int(decode.get("beam_size", 4)),
            "length_penalty": float(decode.get("length_penalty", 0.7)),
            "no_repeat_ngram_size": int(decode.get("no_repeat_ngram_size", 3)),
        }
        self._loading_tx = True
        try:
            self.tx_beam.setValue(int(self._tx_draft["beam_size"]))
            self.tx_length.setValue(
                int(round(float(self._tx_draft["length_penalty"]) * 10))
            )
            self.tx_ngram.setValue(int(self._tx_draft["no_repeat_ngram_size"]))
            self._sync_tx_decode_chips()
        finally:
            self._loading_tx = False

    def _tx_live(self) -> dict[str, float | int]:
        return dict(self._tx_draft)

    def _on_tx_preset_changed(self) -> None:
        preset = self._current_tx_preset()
        self._config["translation_decode_preset"] = preset
        self._load_translation_decode_into_spins()
        self._sync_translation_preset_actions()

    def _on_tx_decode_slider_changed(self, *_args: Any) -> None:
        self._sync_tx_decode_chips()
        self._on_tx_decode_edited()

    def _on_tx_decode_edited(self, *_args: Any) -> None:
        """Editar los sliders ya no salta a `custom`: marca el preset modificado."""
        if self._loading_tx:
            return
        self._tx_draft = self._spins_decode()
        self._sync_translation_preset_actions()

    def _sync_translation_preset_actions(self) -> None:
        preset = self._current_tx_preset()
        state = ActionState(
            active=preset in self._translation_profiles(),
            modified=is_modified(DOMAIN_DECODE, self._config, preset, self._tx_live()),
            deletable=is_deletable(DOMAIN_DECODE, preset),
            factory=is_factory(DOMAIN_DECODE, preset),
        )
        self.tx_actions.configure(state)

    def _view_translation_values(self) -> None:
        preset = self._current_tx_preset()
        rows = describe(DOMAIN_DECODE, self._config, preset)
        note = None
        if is_factory(DOMAIN_DECODE, preset):
            note = "Preset de fábrica: sus valores no se pueden modificar."
        PresetValuesDialog(
            self,
            title=f"Valores de «{preset_label(DOMAIN_DECODE, preset)}»",
            subtitle="Opciones de decoding que aplica este preset.",
            rows=rows,
            note=note,
        ).exec()

    def _save_translation_clicked(self) -> None:
        preset = self._current_tx_preset()
        factory = is_factory(DOMAIN_DECODE, preset)
        choice = ask_save_choice(
            self,
            preset_label=preset_label(DOMAIN_DECODE, preset),
            is_factory=factory,
        )
        if choice == CHOICE_NEW:
            self._save_translation_preset_as()
            return
        if choice != CHOICE_OVERWRITE or factory:
            return
        try:
            self._config = save_translation_preset(
                self._config, preset, self._spins_decode()
            )
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, "Preset", str(exc))
            return
        self._refresh_translation_preset_combo()
        self._persist_preset_library()

    def _restore_translation_preset(self) -> None:
        self._load_translation_decode_into_spins()
        self._sync_translation_preset_actions()

    def _commit_translation_draft(self) -> None:
        """Persiste el borrador si el preset es propio; fábrica nunca se toca."""
        preset = self._current_tx_preset()
        self._config["translation_decode_preset"] = preset
        if is_factory(DOMAIN_DECODE, preset):
            return
        if not is_modified(DOMAIN_DECODE, self._config, preset, self._tx_live()):
            return
        try:
            self._config = save_translation_preset(
                self._config, preset, self._spins_decode()
            )
        except ValueError:
            pass

    def _save_translation_preset_as(self) -> None:
        name = ask_preset_name(self, title="Guardar preset de decoding")
        if name is None:
            return
        try:
            self._config = add_translation_user_preset(
                self._config,
                name,
                self._spins_decode(),
            )
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, "Preset", str(exc))
            return
        self._refresh_translation_preset_combo()
        self._persist_preset_library()

    def _delete_translation_preset(self) -> None:
        preset = self._current_tx_preset()
        if not is_deletable(DOMAIN_DECODE, preset):
            return
        answer = QtWidgets.QMessageBox.question(
            self,
            "Borrar preset",
            f"¿Borrar el preset «{preset}»?",
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        try:
            self._config = delete_translation_user_preset(self._config, preset)
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, "Preset", str(exc))
            return
        self._refresh_translation_preset_combo()
        self._persist_preset_library()

    def _pick_color(
        self,
        button: QtWidgets.QPushButton,
        hex_label: QtWidgets.QLabel,
    ) -> None:
        dialog = QtWidgets.QColorDialog(QtGui.QColor(self._swatch_hex(button)), self)
        dialog.setOption(
            QtWidgets.QColorDialog.ColorDialogOption.DontUseNativeDialog, True
        )
        dialog.setWindowTitle("Elegir color")
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        color = dialog.currentColor()
        if color.isValid():
            self._set_swatch(button, hex_label, color.name())
            self._refresh_preview()

    def _pick_font_color(self) -> None:
        self._pick_color(self.font_color_btn, self.font_color_hex)

    def _pick_bg_color(self) -> None:
        self._pick_color(self.bg_color_btn, self.bg_color_hex)

    def result_config(self) -> dict[str, Any]:
        preset = self._current_tx_preset()
        self._config["translation_decode_preset"] = preset
        lang = str(self.language.currentData() or "en").strip().lower() or "en"
        installed = list(AVAILABLE_LANGUAGES.keys())
        audio = self.audio.currentData()
        if audio is None:
            audio = self.audio.currentText()
        cfg = dict(self._config)
        cfg.update(
            {
                "language": lang,
                "installed_languages": installed,
                "model": self.model.currentText(),
                "compute_type": str(self.compute_type.currentData() or "float16"),
                "audio_monitor": str(audio or ""),
                "latency_mode": self._current_mode(),
                "latency_profiles": deepcopy(self._profiles()),
                "font_size": self.font_size.value(),
                "font_family": self._current_font_family(),
                "font_weight": self._current_font_weight(),
                "padding": self.padding.value(),
                "text_align": self._current_text_align(),
                "font_color": self._swatch_hex(self.font_color_btn),
                "bg_color": self._swatch_hex(self.bg_color_btn),
                "bg_alpha": self.alpha.value() / 100.0,
                "captions_show_partials": self.captions_show_partials.isChecked(),
                "captions_allow_rewrite": self.captions_allow_rewrite.isChecked(),
                "second_line_mode": str(
                    self.second_line_mode.currentData() or "live_asr"
                ),
                "translation_sticky_mode": str(
                    self.tx_sticky_mode.currentData() or "off"
                ),
                "translator_model": str(
                    self.translator_model.currentData() or DEFAULT_TRANSLATOR_MODEL
                ),
                "translation_decode_preset": preset,
                "translation_profiles": deepcopy(self._translation_profiles()),
                **self.geometry_snapshot(),
            }
        )
        return validate_config(cfg)
