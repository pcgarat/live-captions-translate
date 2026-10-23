"""Piezas de UI compartidas por los selectores de preset.

Los tres selectores (general, latencia y decoding) usan los mismos botones y los
mismos diálogos; lo único distinto es el dominio y las acciones de config.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from PyQt6 import QtCore, QtGui, QtWidgets

CONTROL_HEIGHT = 32

CHOICE_OVERWRITE = "overwrite"
CHOICE_NEW = "new"


def preset_action_icon(
    kind: str, *, size: int = 18, color: str = "#c5cad6"
) -> QtGui.QIcon:
    """Iconos: view | save | save_as | delete | reset."""
    pm = QtGui.QPixmap(size, size)
    pm.fill(QtCore.Qt.GlobalColor.transparent)
    painter = QtGui.QPainter(pm)
    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    pen = QtGui.QPen(QtGui.QColor(color))
    pen.setWidthF(1.6)
    pen.setJoinStyle(QtCore.Qt.PenJoinStyle.RoundJoin)
    pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)

    if kind in {"save", "save_as"}:
        painter.drawRoundedRect(3, 2, 12, 14, 1.5, 1.5)
        painter.drawRect(6, 2, 6, 4)
        painter.drawLine(6, 12, 12, 12)
        if kind == "save_as":
            painter.drawLine(14, 4, 14, 8)
            painter.drawLine(12, 6, 16, 6)
    elif kind == "reset":
        # Arco con flecha (restablecer).
        rect = QtCore.QRectF(3.5, 3.5, 11, 11)
        painter.drawArc(rect, 40 * 16, 280 * 16)
        painter.drawLine(13, 4, 15, 6)
        painter.drawLine(13, 4, 11, 6)
    elif kind == "view":
        # Ojo: inspeccionar los valores que fija el preset.
        painter.drawEllipse(QtCore.QRectF(2, 5, 14, 8))
        painter.drawEllipse(QtCore.QRectF(7, 7, 4, 4))
    else:
        painter.drawLine(5, 5, 13, 5)
        painter.drawLine(7, 5, 7, 3)
        painter.drawLine(11, 5, 11, 3)
        painter.drawLine(6, 3, 12, 3)
        painter.drawRoundedRect(5, 5, 8, 10, 1.2, 1.2)
        painter.drawLine(8, 7, 8, 12)
        painter.drawLine(10, 7, 10, 12)

    painter.end()
    return QtGui.QIcon(pm)


def make_action_button(*, name: str, icon_kind: str) -> QtWidgets.QPushButton:
    btn = QtWidgets.QPushButton()
    btn.setObjectName("IconToolButton")
    btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
    btn.setAccessibleName(name)
    btn.setToolTip(name)
    btn.setIcon(preset_action_icon(icon_kind))
    btn.setIconSize(QtCore.QSize(16, 16))
    btn.setFixedSize(CONTROL_HEIGHT, CONTROL_HEIGHT)
    btn.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
    return btn


@dataclass(frozen=True)
class ActionState:
    """Qué botones tienen sentido para el preset seleccionado."""

    active: bool
    modified: bool
    deletable: bool
    factory: bool = False

    @property
    def show_view(self) -> bool:
        return self.active

    @property
    def show_save(self) -> bool:
        """Sobrescribir: solo presets propios y solo si hay cambios."""
        return self.active and self.modified and not self.factory

    @property
    def show_restore(self) -> bool:
        return self.active and self.modified

    @property
    def show_delete(self) -> bool:
        return self.deletable

    @property
    def show_save_as(self) -> bool:
        """Guardar como nuevo: sin preset, o con un preset de fábrica modificado."""
        return not self.active or (self.factory and self.modified)


class PresetActionBar(QtWidgets.QWidget):
    """Fila de botones que se muestran solo cuando aplican.

    No decide nada: recibe el estado y emite señales. Así los tres selectores
    comparten la misma regla de visibilidad.
    """

    view_requested = QtCore.pyqtSignal()
    save_requested = QtCore.pyqtSignal()
    save_as_requested = QtCore.pyqtSignal()
    restore_requested = QtCore.pyqtSignal()
    delete_requested = QtCore.pyqtSignal()

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.setAlignment(QtCore.Qt.AlignmentFlag.AlignLeft)

        self.view_btn = make_action_button(name="Ver valores", icon_kind="view")
        self.save_btn = make_action_button(name="Guardar", icon_kind="save")
        self.save_as_btn = make_action_button(
            name="Guardar como nuevo…", icon_kind="save_as"
        )
        self.restore_btn = make_action_button(
            name="Restaurar valores", icon_kind="reset"
        )
        self.delete_btn = make_action_button(name="Borrar", icon_kind="delete")

        for button, signal in (
            (self.view_btn, self.view_requested),
            (self.save_btn, self.save_requested),
            (self.save_as_btn, self.save_as_requested),
            (self.restore_btn, self.restore_requested),
            (self.delete_btn, self.delete_requested),
        ):
            button.clicked.connect(signal.emit)
            row.addWidget(button, stretch=0)

        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Fixed,
            QtWidgets.QSizePolicy.Policy.Preferred,
        )
        self.configure(ActionState(active=False, modified=False, deletable=False))

    def configure(self, state: ActionState) -> None:
        self.view_btn.setVisible(state.show_view)
        self.save_btn.setVisible(state.show_save)
        self.save_as_btn.setVisible(state.show_save_as)
        self.restore_btn.setVisible(state.show_restore)
        self.delete_btn.setVisible(state.show_delete)

    def visible_buttons(self) -> list[QtWidgets.QPushButton]:
        return [
            btn
            for btn in (
                self.view_btn,
                self.save_btn,
                self.save_as_btn,
                self.restore_btn,
                self.delete_btn,
            )
            if btn.isVisibleTo(self)
        ]


class PresetValuesDialog(QtWidgets.QDialog):
    """Lista de las opciones que fija un preset y el valor que les asigna."""

    def __init__(
        self,
        parent: QtWidgets.QWidget | None,
        *,
        title: str,
        subtitle: str,
        rows: Sequence[tuple[str, str]],
        note: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("SettingsDialog")
        self.setWindowTitle(title)
        self.setModal(True)
        if parent is not None:
            self.setStyleSheet(parent.styleSheet())
        self.setMinimumWidth(420)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(12)

        heading = QtWidgets.QLabel(title)
        heading.setObjectName("DialogTitle")
        root.addWidget(heading)

        sub = QtWidgets.QLabel(subtitle)
        sub.setObjectName("DialogSubtitle")
        sub.setWordWrap(True)
        root.addWidget(sub)

        grid_host = QtWidgets.QWidget()
        grid = QtWidgets.QGridLayout(grid_host)
        grid.setContentsMargins(0, 8, 0, 0)
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(1, 1)
        for index, (label, value) in enumerate(rows):
            name = QtWidgets.QLabel(label)
            name.setObjectName("FieldLabel")
            name.setAlignment(
                QtCore.Qt.AlignmentFlag.AlignRight
                | QtCore.Qt.AlignmentFlag.AlignVCenter
            )
            shown = QtWidgets.QLabel(value)
            shown.setObjectName("ValueChip")
            shown.setTextInteractionFlags(
                QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
            )
            grid.addWidget(name, index, 0)
            grid.addWidget(shown, index, 1)
        if not rows:
            empty = QtWidgets.QLabel("Este preset no fija ninguna opción.")
            empty.setObjectName("FieldHint")
            grid.addWidget(empty, 0, 0, 1, 2)

        scroll = QtWidgets.QScrollArea()
        scroll.setObjectName("SettingsScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        scroll.setWidget(grid_host)
        root.addWidget(scroll, stretch=1)

        if note:
            hint = QtWidgets.QLabel(note)
            hint.setObjectName("FieldHint")
            hint.setWordWrap(True)
            root.addWidget(hint)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Close
        )
        close_btn = buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Close)
        assert close_btn is not None
        close_btn.setText("Cerrar")
        close_btn.setObjectName("PrimaryButton")
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        root.addWidget(buttons)

        self.rows = list(rows)


def ask_save_choice(
    parent: QtWidgets.QWidget | None,
    *,
    preset_label: str,
    is_factory: bool,
) -> str | None:
    """Qué hacer al guardar: `overwrite`, `new` o None (cancelar).

    Un preset de fábrica no se puede sobrescribir, así que ni se pregunta: la
    única salida es guardarlo con otro nombre.
    """
    if is_factory:
        return CHOICE_NEW
    box = QtWidgets.QMessageBox(parent)
    box.setObjectName("SettingsDialog")
    if parent is not None:
        box.setStyleSheet(parent.styleSheet())
    box.setIcon(QtWidgets.QMessageBox.Icon.Question)
    box.setWindowTitle("Guardar preset")
    box.setText(f"Has modificado el preset «{preset_label}».")
    box.setInformativeText("¿Sobrescribirlo o guardarlo como uno nuevo?")
    overwrite = box.addButton(
        f"Sobrescribir «{preset_label}»", QtWidgets.QMessageBox.ButtonRole.AcceptRole
    )
    new = box.addButton(
        "Guardar como nuevo…", QtWidgets.QMessageBox.ButtonRole.ActionRole
    )
    cancel = box.addButton("Cancelar", QtWidgets.QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(new)
    box.exec()
    clicked = box.clickedButton()
    if clicked is overwrite:
        return CHOICE_OVERWRITE
    if clicked is new:
        return CHOICE_NEW
    if clicked is cancel:
        return None
    return None


def ask_preset_name(
    parent: QtWidgets.QWidget | None,
    *,
    title: str,
) -> str | None:
    name, ok = QtWidgets.QInputDialog.getText(
        parent,
        title,
        "Nombre del preset:",
    )
    if not ok:
        return None
    return str(name)
