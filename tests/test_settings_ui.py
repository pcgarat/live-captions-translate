from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from src.config import TRANSLATION_FACTORY_PRESETS, validate_config
from src.ui.presets_ui import CHOICE_OVERWRITE
from src.ui.settings import (
    HINT_ALLOW_REWRITE,
    HINT_SHOW_PARTIALS,
    SettingsDialog,
    detect_partials_sticky_conflict,
)


@pytest.fixture(scope="module")
def qapp() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    return app


def _shown(widget: QtWidgets.QWidget) -> bool:
    """Visibilidad solicitada, sin depender de que el diálogo esté en pantalla."""
    return not widget.isHidden()


def test_settings_audio_combo_uses_source_labels(
    qapp: QtWidgets.QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.audio.backends import AudioSource

    sources = [
        AudioSource(
            id="alsa_output.pci-0000_00_1f.3.analog-stereo.monitor",
            label="Salida ALSA · pci-0000_00_1f.3.analog-stereo",
            backend="pulse",
            is_loopback=True,
        )
    ]
    monkeypatch.setattr("src.ui.settings.list_audio_sources", lambda: sources)
    dlg = SettingsDialog(None, validate_config({"audio_monitor": sources[0].id}))
    assert dlg.audio.currentData() == sources[0].id
    assert dlg.audio.currentText() == sources[0].label
    assert dlg.result_config()["audio_monitor"] == sources[0].id
    dlg.close()


def test_settings_audio_keeps_unplugged_device(
    qapp: QtWidgets.QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un dispositivo guardado que ya no esté conectado no se pierde al guardar."""
    monkeypatch.setattr("src.ui.settings.list_audio_sources", lambda: [])
    dlg = SettingsDialog(
        None, validate_config({"audio_monitor": "bluez_output.AA_BB.1.monitor"})
    )
    assert dlg.audio.currentData() == "bluez_output.AA_BB.1.monitor"
    assert dlg.audio.currentText() == "Bluetooth · AA:BB"
    assert dlg.result_config()["audio_monitor"] == "bluez_output.AA_BB.1.monitor"
    dlg.close()


def test_settings_audio_error_is_shown(
    qapp: QtWidgets.QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom() -> list:
        raise RuntimeError("No se encontró `pactl`.")

    monkeypatch.setattr("src.ui.settings.list_audio_sources", boom)
    dlg = SettingsDialog(None, validate_config({"audio_monitor": ""}))
    hints = [
        lab.text()
        for lab in dlg.findChildren(QtWidgets.QLabel)
        if lab.text().startswith("No se pudo listar audio")
    ]
    assert hints and "pactl" in hints[0]
    dlg.close()


def test_settings_edit_decode_marks_preset_modified(
    qapp: QtWidgets.QApplication,
) -> None:
    """Editar un slider ya no salta a `custom`: el preset queda *modificado*."""
    dlg = SettingsDialog(
        None, validate_config({"translation_decode_preset": "balanced"})
    )
    assert dlg.tx_preset.currentData() == "balanced"
    assert isinstance(dlg.tx_beam, QtWidgets.QSlider)
    assert isinstance(dlg.tx_length, QtWidgets.QSlider)
    assert isinstance(dlg.tx_ngram, QtWidgets.QSlider)
    assert not _shown(dlg.tx_save_btn)
    assert not _shown(dlg.tx_restore_btn)

    dlg.tx_beam.setValue(7)
    assert dlg.tx_preset.currentData() == "balanced"
    # Fábrica modificada: ni sobrescribir ni borrar, pero sí guardar como nuevo.
    assert _shown(dlg.tx_save_btn) is False
    assert _shown(dlg.tx_save_as_btn) is True
    assert _shown(dlg.tx_restore_btn) is True
    cfg = dlg.result_config()
    assert cfg["translation_decode_preset"] == "balanced"
    # La fábrica no se escribe: los sliders son un borrador del diálogo.
    assert cfg["translation_profiles"]["balanced"] == (
        TRANSLATION_FACTORY_PRESETS["balanced"]
    )
    assert abs(float(cfg["translation_profiles"]["custom"]["length_penalty"]) - 0.7) < 1e-9
    dlg.tx_length.setValue(11)  # 1.1
    assert dlg.tx_length_value.text() == "1.1"
    dlg.close()


def test_settings_decode_own_preset_can_be_overwritten(
    qapp: QtWidgets.QApplication,
) -> None:
    from src.config import add_translation_user_preset

    cfg = add_translation_user_preset(
        validate_config({"translation_decode_preset": "balanced"}), "mio"
    )
    dlg = SettingsDialog(None, cfg)
    assert dlg.tx_preset.currentData() == "mio"
    dlg.tx_beam.setValue(7)
    assert _shown(dlg.tx_save_btn) is True
    assert _shown(dlg.tx_save_as_btn) is False
    assert _shown(dlg.tx_delete_btn) is True
    # El borrador se persiste al aceptar; `result_config` lo refleja después.
    dlg._commit_translation_draft()
    assert dlg.result_config()["translation_profiles"]["mio"]["beam_size"] == 7
    dlg.close()


def test_settings_factory_preset_in_result(qapp: QtWidgets.QApplication) -> None:
    dlg = SettingsDialog(None, validate_config({}))
    idx = dlg.tx_preset.findData("quality")
    dlg.tx_preset.setCurrentIndex(idx)
    cfg = dlg.result_config()
    assert cfg["translation_decode_preset"] == "quality"
    assert (
        cfg["translation_profiles"]["quality"] == TRANSLATION_FACTORY_PRESETS["quality"]
    )
    # Fábrica limpia: solo «Ver valores»; nada de guardar, borrar ni restaurar.
    assert _shown(dlg.tx_view_btn) is True
    assert _shown(dlg.tx_save_btn) is False
    assert _shown(dlg.tx_save_as_btn) is False
    assert _shown(dlg.tx_restore_btn) is False
    assert _shown(dlg.tx_delete_btn) is False
    dlg.close()


def test_settings_captions_display_toggles(qapp: QtWidgets.QApplication) -> None:
    dlg = SettingsDialog(
        None,
        validate_config(
            {"captions_show_partials": True, "captions_allow_rewrite": True}
        ),
    )
    assert dlg.captions_show_partials.isChecked()
    assert dlg.captions_allow_rewrite.isChecked()
    assert not (dlg.captions_show_partials.toolTip() or "")
    assert not (dlg.captions_allow_rewrite.toolTip() or "")
    assert HINT_SHOW_PARTIALS
    assert HINT_ALLOW_REWRITE
    dlg.captions_show_partials.setChecked(False)
    dlg.captions_allow_rewrite.setChecked(False)
    cfg = dlg.result_config()
    assert cfg["captions_show_partials"] is False
    assert cfg["captions_allow_rewrite"] is False
    dlg.close()


def test_settings_translator_model(qapp: QtWidgets.QApplication) -> None:
    from src.asr.translate import TRANSLATOR_MODEL_1_3B, TRANSLATOR_MODEL_600M

    dlg = SettingsDialog(
        None, validate_config({"translator_model": TRANSLATOR_MODEL_1_3B})
    )
    assert dlg.translator_model.currentData() == TRANSLATOR_MODEL_1_3B
    idx = dlg.translator_model.findData(TRANSLATOR_MODEL_600M)
    assert idx >= 0
    dlg.translator_model.setCurrentIndex(idx)
    assert dlg.result_config()["translator_model"] == TRANSLATOR_MODEL_600M
    dlg.close()


def test_settings_translator_model_keeps_custom_repo(
    qapp: QtWidgets.QApplication,
) -> None:
    """Un repo propio en config no se pierde al guardar desde la UI."""
    dlg = SettingsDialog(
        None, validate_config({"translator_model": "yo/mi-conversion-ct2"})
    )
    assert dlg.translator_model.currentData() == "yo/mi-conversion-ct2"
    assert dlg.result_config()["translator_model"] == "yo/mi-conversion-ct2"
    dlg.close()


def test_settings_compute_type(qapp: QtWidgets.QApplication) -> None:
    dlg = SettingsDialog(
        None, validate_config({"compute_type": "int8_float16"})
    )
    assert dlg.compute_type.currentData() == "int8_float16"
    idx = dlg.compute_type.findData("int8")
    assert idx >= 0
    dlg.compute_type.setCurrentIndex(idx)
    cfg = dlg.result_config()
    assert cfg["compute_type"] == "int8"
    dlg.close()


def test_settings_appearance_tab_text_align(qapp: QtWidgets.QApplication) -> None:
    from PyQt6 import QtCore

    dlg = SettingsDialog(None, validate_config({"text_align": "center"}))
    assert dlg._current_text_align() == "center"
    dlg._set_text_align("left")
    dlg._refresh_preview()
    cfg = dlg.result_config()
    assert cfg["text_align"] == "left"
    assert (
        dlg._preview_caption.alignment() & QtCore.Qt.AlignmentFlag.AlignLeft
        == QtCore.Qt.AlignmentFlag.AlignLeft
    )
    dlg.close()


def test_settings_appearance_sliders_and_colors(
    qapp: QtWidgets.QApplication,
) -> None:
    dlg = SettingsDialog(
        None,
        validate_config(
            {
                "font_size": 32,
                "padding": 18,
                "font_color": "#ffcc00",
                "bg_color": "#112233",
            }
        ),
    )
    assert isinstance(dlg.font_size, QtWidgets.QSlider)
    assert isinstance(dlg.padding, QtWidgets.QSlider)
    assert dlg.font_size.value() == 32
    assert dlg.padding.value() == 18
    assert dlg.font_size_value.text() == "32"
    assert dlg._swatch_hex(dlg.font_color_btn) == "#ffcc00"
    assert dlg.font_color_hex.text() == "#ffcc00"
    dlg.font_size.setValue(40)
    assert dlg.font_size_value.text() == "40"
    dlg._set_swatch(dlg.bg_color_btn, dlg.bg_color_hex, "#abcdef")
    cfg = dlg.result_config()
    assert cfg["font_size"] == 40
    assert cfg["bg_color"] == "#abcdef"
    dlg.close()


def test_settings_font_family_and_weight(qapp: QtWidgets.QApplication) -> None:
    from src.ui.fonts import available_caption_fonts

    curated, rest = available_caption_fonts()
    family = (curated or rest)[0]
    dlg = SettingsDialog(
        None, validate_config({"font_family": family, "font_weight": "bold"})
    )
    assert dlg.font_family.currentData() == family
    assert dlg.font_weight.currentData() == "bold"
    assert dlg.font_family.itemData(0) == ""

    idx = dlg.font_family.findData("")
    dlg.font_family.setCurrentIndex(idx)
    weight_idx = dlg.font_weight.findData("normal")
    dlg.font_weight.setCurrentIndex(weight_idx)
    cfg = dlg.result_config()
    assert cfg["font_family"] == ""
    assert cfg["font_weight"] == "normal"
    dlg.close()


def test_settings_font_family_missing_is_kept(qapp: QtWidgets.QApplication) -> None:
    """Una familia que no esté en el sistema no se pierde al guardar."""
    dlg = SettingsDialog(None, validate_config({"font_family": "Fuente Fantasma"}))
    assert dlg.font_family.currentData() == "Fuente Fantasma"
    assert "no instalada" in dlg.font_family.currentText()
    assert dlg.result_config()["font_family"] == "Fuente Fantasma"
    dlg.close()


def test_settings_preview_reflects_typography(qapp: QtWidgets.QApplication) -> None:
    from src.ui.fonts import available_caption_fonts

    curated, rest = available_caption_fonts()
    family = (curated or rest)[0]
    dlg = SettingsDialog(None, validate_config({}))
    idx = dlg.font_family.findData(family)
    dlg.font_family.setCurrentIndex(idx)
    dlg.font_weight.setCurrentIndex(dlg.font_weight.findData("bold"))
    qss = dlg._preview_caption.styleSheet()
    assert f'font-family: "{family}";' in qss
    assert "font-weight: 700;" in qss
    dlg.close()


def test_detect_partials_sticky_conflict_none_when_compatible() -> None:
    assert (
        detect_partials_sticky_conflict(
            show_partials=True, sticky_mode="partials", changed="partials"
        )
        is None
    )
    assert (
        detect_partials_sticky_conflict(
            show_partials=False, sticky_mode="committed", changed="sticky"
        )
        is None
    )


def test_detect_partials_sticky_conflict_fix_targets() -> None:
    turned_off = detect_partials_sticky_conflict(
        show_partials=False, sticky_mode="partials", changed="partials"
    )
    assert turned_off is not None
    assert turned_off.fix_sticky_mode == "committed"
    assert turned_off.fix_show_partials is None

    set_sticky = detect_partials_sticky_conflict(
        show_partials=False, sticky_mode="partials", changed="sticky"
    )
    assert set_sticky is not None
    assert set_sticky.fix_show_partials is True
    assert set_sticky.fix_sticky_mode is None


def test_settings_conflict_cancel_reverts_partials(
    qapp: QtWidgets.QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    dlg = SettingsDialog(
        None,
        validate_config(
            {
                "translation_sticky_mode": "partials",
                "captions_show_partials": True,
            }
        ),
    )
    monkeypatch.setattr(dlg, "_prompt_mode_conflict", lambda _c: False)
    dlg.captions_show_partials.setChecked(False)
    assert dlg.captions_show_partials.isChecked() is True
    assert dlg.tx_sticky_mode.currentData() == "partials"
    dlg.close()


def test_settings_conflict_fix_changes_sticky(
    qapp: QtWidgets.QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    dlg = SettingsDialog(
        None,
        validate_config(
            {
                "translation_sticky_mode": "partials",
                "captions_show_partials": True,
            }
        ),
    )
    monkeypatch.setattr(dlg, "_prompt_mode_conflict", lambda _c: True)
    dlg.captions_show_partials.setChecked(False)
    assert dlg.captions_show_partials.isChecked() is False
    assert dlg.tx_sticky_mode.currentData() == "committed"
    dlg.close()


def test_settings_conflict_fix_enables_partials_from_sticky(
    qapp: QtWidgets.QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    dlg = SettingsDialog(
        None,
        validate_config(
            {
                "captions_show_partials": False,
                "translation_sticky_mode": "off",
            }
        ),
    )
    monkeypatch.setattr(dlg, "_prompt_mode_conflict", lambda _c: True)
    idx = dlg.tx_sticky_mode.findData("partials")
    dlg.tx_sticky_mode.setCurrentIndex(idx)
    assert dlg.captions_show_partials.isChecked() is True
    assert dlg.tx_sticky_mode.currentData() == "partials"
    dlg.close()


def test_settings_geometry_snapshot_and_restore(qapp: QtWidgets.QApplication) -> None:
    from PyQt6 import QtCore

    dlg = SettingsDialog(
        None,
        validate_config(
            {
                "settings_window_width": 700,
                "settings_window_height": 800,
                "settings_window_pos": [120, 80],
            }
        ),
    )
    dlg.apply_saved_geometry()
    assert dlg.width() == 700
    assert dlg.height() == 800
    assert dlg.x() == 120
    assert dlg.y() == 80
    dlg.resize(750, 820)
    dlg.move(30, 40)
    snap = dlg.geometry_snapshot()
    assert snap == {
        "settings_window_pos": [30, 40],
        "settings_window_width": 750,
        "settings_window_height": 820,
    }
    cfg = dlg.result_config()
    assert cfg["settings_window_width"] == 750
    assert cfg["settings_window_height"] == 820
    assert cfg["settings_window_pos"] == [30, 40]

    dlg2 = SettingsDialog(
        None,
        validate_config({"settings_window_pos": None}),
    )
    dlg2.apply_saved_geometry(fallback_center=QtCore.QPoint(500, 500))
    assert dlg2.x() == 500 - dlg2.width() // 2
    assert dlg2.y() == 500 - dlg2.height() // 2
    dlg.close()
    dlg2.close()


class _FakeAppPresetController:
    def __init__(self) -> None:
        self.applied: list[str | None] = []
        self.saved = 0
        self.saved_as: list[str] = []
        self.deleted = 0
        self.cfg = validate_config({"app_preset": None, "app_presets": {}})

    def apply_app_preset_from_settings(self, preset_id, settings_dlg):
        self.applied.append(preset_id)
        from src.config import apply_app_preset

        self.cfg = apply_app_preset(self.cfg, preset_id)
        settings_dlg.reload_from_config(self.cfg)
        return None

    def save_app_preset_from_settings(self, settings_dlg) -> None:
        self.saved += 1
        from src.config import save_app_preset

        self.cfg = save_app_preset(self.cfg, snapshot_src=settings_dlg.result_config())
        settings_dlg.reload_from_config(self.cfg)

    def save_app_preset_as_from_settings(self, name: str, settings_dlg) -> None:
        self.saved_as.append(name)
        from src.config import save_app_preset_as

        self.cfg = save_app_preset_as(
            self.cfg, name, snapshot_src=settings_dlg.result_config()
        )
        settings_dlg.reload_from_config(self.cfg)

    def delete_app_preset_from_settings(self, settings_dlg) -> None:
        self.deleted += 1
        from src.config import delete_app_preset

        self.cfg = delete_app_preset(self.cfg)
        settings_dlg.reload_from_config(self.cfg)


def test_app_preset_bar_disabled_without_controller(
    qapp: QtWidgets.QApplication,
) -> None:
    dlg = SettingsDialog(None, validate_config({}))
    assert dlg.app_preset_save_btn.isEnabled() is False
    assert dlg.app_preset_save_as_btn.isEnabled() is False
    assert dlg.app_preset_delete_btn.isEnabled() is False
    assert dlg.brand_logo.pixmap() is not None
    assert not dlg.brand_logo.pixmap().isNull()
    assert dlg.brand_logo.height() == 192
    dlg.close()


def test_brand_logo_loads_from_assets(qapp: QtWidgets.QApplication) -> None:
    from src.ui.branding import brand_logo_path, load_brand_logo_pixmap

    assert brand_logo_path().is_file()
    pm = load_brand_logo_pixmap(height=192, device_pixel_ratio=1.0)
    assert not pm.isNull()
    assert pm.height() == 192


def test_app_preset_bar_save_as_and_guardar(
    qapp: QtWidgets.QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctrl = _FakeAppPresetController()
    dlg = SettingsDialog(None, ctrl.cfg, controller=ctrl)
    # Sin preset no hay «Ver valores»; la única acción es crear uno.
    assert dlg.app_preset.currentData() is None
    assert _shown(dlg.app_preset_view_btn) is False
    assert _shown(dlg.app_preset_save_btn) is False
    assert _shown(dlg.app_preset_save_as_btn) is True
    assert _shown(dlg.app_preset_restore_btn) is False
    assert _shown(dlg.app_preset_delete_btn) is False

    monkeypatch.setattr(
        QtWidgets.QInputDialog,
        "getText",
        lambda *args, **kwargs: ("Directo ES", True),
    )
    dlg._save_app_preset_as()
    assert ctrl.saved_as == ["Directo ES"]
    assert dlg.app_preset.currentData() == "directo-es"
    assert _shown(dlg.app_preset_view_btn) is True
    assert _shown(dlg.app_preset_save_btn) is False
    assert _shown(dlg.app_preset_delete_btn) is True

    dlg.font_size.setValue(42)
    dlg._sync_app_preset_actions()
    assert _shown(dlg.app_preset_save_btn) is True
    assert _shown(dlg.app_preset_restore_btn) is True
    assert _shown(dlg.app_preset_save_as_btn) is False

    # Un preset propio modificado pregunta si sobrescribir; aquí decimos que sí.
    monkeypatch.setattr(
        "src.ui.settings.ask_save_choice", lambda *a, **k: CHOICE_OVERWRITE
    )
    dlg._save_app_preset_clicked()
    assert ctrl.saved == 1
    assert ctrl.cfg["app_presets"]["directo-es"]["font_size"] == 42
    dlg.close()


def test_app_preset_factory_bar_is_read_only(
    qapp: QtWidgets.QApplication,
) -> None:
    """Fábrica limpia: solo «Ver valores»; borrar y guardar encima, nunca."""
    from src.config import apply_app_preset
    from src.presets import is_factory_preset

    ctrl = _FakeAppPresetController()
    # `default` es un snapshot completo: fija también la apariencia, así que
    # tocar el tamaño de texto es una modificación del preset.
    ctrl.cfg = apply_app_preset(validate_config({}), "default")
    active = ctrl.cfg["app_preset"]
    assert is_factory_preset(active)
    dlg = SettingsDialog(None, ctrl.cfg, controller=ctrl)
    assert dlg.app_preset.currentData() == active
    assert _shown(dlg.app_preset_view_btn) is True
    assert _shown(dlg.app_preset_save_btn) is False
    assert _shown(dlg.app_preset_save_as_btn) is False
    assert _shown(dlg.app_preset_delete_btn) is False

    # Modificar una opción que fija el preset enciende el ciclo de guardado.
    dlg.font_size.setValue(42)
    dlg._sync_app_preset_actions()
    assert _shown(dlg.app_preset_save_btn) is False
    assert _shown(dlg.app_preset_save_as_btn) is True
    assert _shown(dlg.app_preset_restore_btn) is True
    dlg.close()


def test_app_preset_restore_reverts_to_preset_values(
    qapp: QtWidgets.QApplication,
) -> None:
    """Restaurar devuelve las opciones del preset, no las de fábrica globales."""
    from src.config import apply_app_preset

    ctrl = _FakeAppPresetController()
    ctrl.cfg = apply_app_preset(validate_config({}), "default")
    dlg = SettingsDialog(None, ctrl.cfg, controller=ctrl)
    original = int(dlg.font_size.value())
    dlg.font_size.setValue(original + 7)
    dlg._sync_app_preset_actions()
    assert _shown(dlg.app_preset_restore_btn) is True

    dlg._restore_app_preset()
    assert dlg.font_size.value() == original
    assert _shown(dlg.app_preset_restore_btn) is False
    dlg.close()


def test_app_preset_view_values_lists_options(
    qapp: QtWidgets.QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.presets import is_factory_preset

    seen: dict[str, object] = {}

    def fake_exec(self) -> int:
        seen["title"] = self.windowTitle()
        seen["rows"] = list(self.rows)
        return 0

    monkeypatch.setattr("src.ui.presets_ui.PresetValuesDialog.exec", fake_exec)
    ctrl = _FakeAppPresetController()
    ctrl.cfg = validate_config({})
    active = ctrl.cfg["app_preset"]
    assert is_factory_preset(active)
    dlg = SettingsDialog(None, ctrl.cfg, controller=ctrl)
    dlg._view_app_preset_values()

    rows = seen["rows"]
    assert isinstance(rows, list) and rows
    labels = [label for label, _ in rows]
    assert "Modelo Whisper" in labels
    assert "Idioma" in labels
    # Geometría y espejos quedan fuera de «Ver valores».
    assert "Ventana X" not in labels
    dlg.close()


def test_app_preset_reload_updates_controls(qapp: QtWidgets.QApplication) -> None:
    dlg = SettingsDialog(None, validate_config({"font_size": 28}))
    dlg.reload_from_config(
        validate_config(
            {
                "font_size": 55,
                "font_weight": "bold",
                "language": "es",
                "latency_mode": "low",
                "text_align": "left",
                "settings_window_width": 600,
                "settings_window_height": 700,
                "settings_window_pos": [11, 22],
            }
        )
    )
    assert dlg.font_size.value() == 55
    assert dlg.font_weight.currentData() == "bold"
    assert dlg.language.currentData() == "es"
    assert dlg.latency_preset.currentData() == "low"
    assert dlg._current_text_align() == "left"
    assert dlg.width() == 600
    assert dlg.height() == 700
    dlg.close()


def test_latency_factory_preset_is_clean(qapp: QtWidgets.QApplication) -> None:
    """Latencia de fábrica sin tocar: solo «Ver valores»."""
    dlg = SettingsDialog(None, validate_config({"latency_mode": "stable"}))
    assert dlg.latency_preset.currentData() == "stable"
    assert _shown(dlg.latency_actions.view_btn) is True
    assert _shown(dlg.latency_actions.save_btn) is False
    assert _shown(dlg.latency_actions.save_as_btn) is False
    assert _shown(dlg.latency_actions.restore_btn) is False
    assert _shown(dlg.latency_actions.delete_btn) is False

    # Mover el techo de latencia marca el preset como modificado.
    dlg.max_latency.setValue(dlg.max_latency.value() + 5)
    dlg._sync_latency_actions()
    assert dlg.latency_preset.currentData() == "stable"
    assert _shown(dlg.latency_actions.save_btn) is False
    assert _shown(dlg.latency_actions.save_as_btn) is True
    assert _shown(dlg.latency_actions.restore_btn) is True
    dlg.close()


def test_latency_restore_discards_the_draft(qapp: QtWidgets.QApplication) -> None:
    dlg = SettingsDialog(None, validate_config({"latency_mode": "stable"}))
    original = dlg.max_latency.value()
    dlg.max_latency.setValue(original + 5)
    assert dlg._latency_live()["max_latency_sec"] != original / 10.0

    dlg._restore_latency_preset()
    assert dlg.max_latency.value() == original
    assert _shown(dlg.latency_actions.restore_btn) is False
    dlg.close()


def test_latency_save_as_creates_own_preset(
    qapp: QtWidgets.QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    dlg = SettingsDialog(None, validate_config({"latency_mode": "stable"}))
    dlg.confidence.setValue(4)
    monkeypatch.setattr(
        "src.ui.settings.ask_preset_name", lambda *a, **k: "Mi latencia"
    )
    dlg._save_latency_clicked()

    assert dlg.latency_preset.currentData() == "mi-latencia"
    profiles = dlg.result_config()["latency_profiles"]
    assert profiles["mi-latencia"]["agreement_n"] == 4
    # El beam y el trozo mínimo viajan con el preset, no se inventan al guardar.
    assert profiles["mi-latencia"]["beam_size"] == profiles["stable"]["beam_size"]
    assert _shown(dlg.latency_actions.delete_btn) is True
    assert _shown(dlg.latency_actions.save_btn) is False
    dlg.close()


def test_latency_own_preset_overwrite_and_delete(
    qapp: QtWidgets.QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.config import save_latency_preset_as

    cfg = save_latency_preset_as(validate_config({}), "mi-latencia")
    dlg = SettingsDialog(None, cfg)
    assert dlg.latency_preset.currentData() == "mi-latencia"

    dlg.confidence.setValue(3)
    dlg._sync_latency_actions()
    assert _shown(dlg.latency_actions.save_btn) is True
    assert _shown(dlg.latency_actions.save_as_btn) is False

    monkeypatch.setattr(
        "src.ui.settings.ask_save_choice", lambda *a, **k: CHOICE_OVERWRITE
    )
    dlg._save_latency_clicked()
    assert dlg.result_config()["latency_profiles"]["mi-latencia"]["agreement_n"] == 3

    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "question",
        lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Yes,
    )
    dlg._delete_latency_preset()
    assert "mi-latencia" not in dlg.result_config()["latency_profiles"]
    assert dlg.latency_preset.currentData() == "stable"
    dlg.close()


def test_latency_view_values_lists_the_knobs(
    qapp: QtWidgets.QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, object] = {}

    def fake_exec(self) -> int:
        seen["rows"] = dict(self.rows)
        return 0

    monkeypatch.setattr("src.ui.presets_ui.PresetValuesDialog.exec", fake_exec)
    dlg = SettingsDialog(None, validate_config({"latency_mode": "low"}))
    dlg._view_latency_values()
    rows = seen["rows"]
    assert isinstance(rows, dict)
    assert rows["Techo de latencia"] == "1.5 s"
    assert rows["Beam size de Whisper"] == "1"
    dlg.close()


def test_action_state_table() -> None:
    """La tabla de botones del spec, en un solo sitio."""
    from src.ui.presets_ui import ActionState

    factory_clean = ActionState(active=True, modified=False, deletable=False, factory=True)
    assert factory_clean.show_view is True
    assert factory_clean.show_save is False
    assert factory_clean.show_save_as is False
    assert factory_clean.show_restore is False

    factory_dirty = ActionState(active=True, modified=True, deletable=False, factory=True)
    assert factory_dirty.show_save is False
    assert factory_dirty.show_save_as is True
    assert factory_dirty.show_restore is True
    assert factory_dirty.show_delete is False

    own_clean = ActionState(active=True, modified=False, deletable=True)
    assert own_clean.show_save is False
    assert own_clean.show_save_as is False
    assert own_clean.show_delete is True

    own_dirty = ActionState(active=True, modified=True, deletable=True)
    assert own_dirty.show_save is True
    assert own_dirty.show_save_as is False
    assert own_dirty.show_restore is True

    none = ActionState(active=False, modified=False, deletable=False)
    assert none.show_view is False
    assert none.show_save is False
    assert none.show_restore is False
    assert none.show_delete is False
    assert none.show_save_as is True


def test_accept_with_modified_factory_can_discard(
    qapp: QtWidgets.QApplication,
) -> None:
    """Aceptar con fábrica modificada ofrece guardar, descartar o cancelar."""
    dlg = SettingsDialog(None, validate_config({}))
    dlg.tx_beam.setValue(7)
    original = dlg.tx_beam.value()

    old_exec = QtWidgets.QMessageBox.exec
    old_clicked = QtWidgets.QMessageBox.clickedButton
    texts: list[str] = []

    def fake_exec(self) -> int:
        texts.extend(button.text() for button in self.buttons())
        chosen = [
            button
            for button in self.buttons()
            if button.text().startswith("Descartar")
        ][0]
        self._chosen = chosen
        return 0

    def fake_clicked(self):
        return getattr(self, "_chosen", None)

    QtWidgets.QMessageBox.exec = fake_exec
    QtWidgets.QMessageBox.clickedButton = fake_clicked
    try:
        assert dlg._confirm_preset_drafts() is True
    finally:
        QtWidgets.QMessageBox.exec = old_exec
        QtWidgets.QMessageBox.clickedButton = old_clicked

    assert any("Guardar como nuevo" in text for text in texts)
    # Descartar devuelve el slider a los valores del preset de fábrica.
    assert dlg.tx_beam.value() == TRANSLATION_FACTORY_PRESETS["balanced"]["beam_size"]
    assert dlg.tx_beam.value() != original
    assert _shown(dlg.tx_actions.restore_btn) is False
    dlg.close()
