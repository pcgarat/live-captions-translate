from __future__ import annotations

import os
import queue
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from src.asr.types import CaptionUpdate
from src.config import apply_app_preset, validate_config
from src.ui.overlay import SubtitleOverlay


@pytest.fixture(scope="module")
def qapp() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    return app


def _overlay(
    qapp: QtWidgets.QApplication,
    *,
    cfg: dict | None = None,
) -> tuple[SubtitleOverlay, queue.Queue]:
    del qapp
    q: queue.Queue[CaptionUpdate] = queue.Queue()
    base = {
        "language": "en",
        "translation_enabled": True,
        "translation_target": "es",
        "second_line_mode": "live_asr",
        "always_on_top": False,
        "window_width": 800,
        "font_size": 22,
        "font_color": "#FFFFFF",
        "bg_opacity": 0.55,
    }
    if cfg:
        base.update(cfg)
    ov = SubtitleOverlay(text_queue=q, config=base)
    ov._timer.stop()
    return ov, q


def test_append_translation_for_extension(qapp: QtWidgets.QApplication) -> None:
    ov, q = _overlay(qapp)
    now = time.monotonic()
    q.put(
        CaptionUpdate(
            text="Hello",
            is_final=True,
            language="en",
            ts_mono=now,
            translated_text="Hola",
            seq=1,
            translation_append=False,
        )
    )
    q.put(
        CaptionUpdate(
            text="Hello world",
            is_final=True,
            language="en",
            ts_mono=now,
            translated_text="mundo",
            seq=2,
            translation_append=True,
        )
    )
    ov._poll_queue()
    assert ov._final_text == "Hello world"
    assert ov._translated_text == "Hola mundo"
    ov.close()


def test_stale_translation_does_not_overwrite(qapp: QtWidgets.QApplication) -> None:
    ov, q = _overlay(qapp)
    now = time.monotonic()
    q.put(
        CaptionUpdate(
            text="Second phrase",
            is_final=True,
            language="en",
            ts_mono=now,
            translated_text="Segunda frase",
            seq=2,
            translation_append=False,
        )
    )
    ov._poll_queue()
    q.put(
        CaptionUpdate(
            text="First phrase revised",
            is_final=True,
            language="en",
            ts_mono=now,
            translated_text="Primera frase cambiada",
            seq=1,
            translation_append=False,
        )
    )
    ov._poll_queue()
    assert ov._final_text == "Second phrase"
    assert ov._translated_text == "Segunda frase"
    ov.close()


def test_translation_prefix_does_not_shrink_final(
    qapp: QtWidgets.QApplication,
) -> None:
    ov, q = _overlay(qapp)
    now = time.monotonic()
    q.put(
        CaptionUpdate(
            text="Hello world today",
            is_final=True,
            language="en",
            ts_mono=now,
            seq=3,
        )
    )
    ov._poll_queue()
    q.put(
        CaptionUpdate(
            text="Hello",
            is_final=True,
            language="en",
            ts_mono=now,
            translated_text="Hola",
            seq=3,
            translation_append=False,
        )
    )
    ov._poll_queue()
    assert ov._final_text == "Hello world today"
    assert ov._translated_text == "Hola"
    ov.close()


def test_late_translation_applies_if_same_final_text(
    qapp: QtWidgets.QApplication,
) -> None:
    ov, q = _overlay(qapp)
    now = time.monotonic()
    q.put(
        CaptionUpdate(
            text="Hello world",
            is_final=True,
            language="en",
            ts_mono=now,
            seq=2,
        )
    )
    ov._poll_queue()
    q.put(
        CaptionUpdate(
            text="Hello world",
            is_final=True,
            language="en",
            ts_mono=now,
            translated_text="Hola mundo",
            seq=1,
            translation_append=False,
        )
    )
    ov._poll_queue()
    assert ov._caption_seq == 2
    assert ov._translated_text == "Hola mundo"
    ov.close()


def test_reset_caption_stream_accepts_low_seq_after_pipeline_restart(
    qapp: QtWidgets.QApplication,
) -> None:
    """Tras reiniciar ASR el pipeline vuelve a seq=1; el overlay no debe descartarlo."""
    ov, q = _overlay(qapp, cfg={"second_line_mode": "none"})
    now = time.monotonic()
    q.put(
        CaptionUpdate(
            text="Before restart",
            is_final=True,
            language="en",
            ts_mono=now,
            translated_text="Antes",
            seq=40,
        )
    )
    ov._poll_queue()
    assert ov._caption_seq == 40
    assert ov._translated_text == "Antes"

    ov.reset_caption_stream()
    assert ov._caption_seq == 0

    q.put(
        CaptionUpdate(
            text="After restart",
            is_final=True,
            language="en",
            ts_mono=now,
            translated_text="Después",
            seq=1,
        )
    )
    ov._poll_queue()
    assert ov._caption_seq == 1
    assert ov._phrase_final == "After restart"
    assert ov._translated_text.endswith("Después")
    ov.close()


def test_stale_low_seq_is_ignored_without_reset(
    qapp: QtWidgets.QApplication,
) -> None:
    ov, q = _overlay(qapp)
    now = time.monotonic()
    q.put(
        CaptionUpdate(
            text="Live",
            is_final=True,
            language="en",
            ts_mono=now,
            translated_text="En vivo",
            seq=10,
        )
    )
    ov._poll_queue()
    q.put(
        CaptionUpdate(
            text="Stale",
            is_final=True,
            language="en",
            ts_mono=now,
            translated_text="Obsoleto",
            seq=2,
        )
    )
    ov._poll_queue()
    assert ov._caption_seq == 10
    assert ov._phrase_final == "Live"
    assert "Obsoleto" not in ov._translated_text
    ov.close()


def test_new_segment_keeps_old_translation_until_ready(
    qapp: QtWidgets.QApplication,
) -> None:
    ov, q = _overlay(qapp)
    now = time.monotonic()
    q.put(
        CaptionUpdate(
            text="Old",
            is_final=True,
            language="en",
            ts_mono=now,
            translated_text="Viejo",
            seq=1,
        )
    )
    ov._poll_queue()
    q.put(
        CaptionUpdate(
            text="Brand new",
            is_final=True,
            language="en",
            ts_mono=now,
            seq=2,
            translation_append=False,
        )
    )
    ov._poll_queue()
    # Sin prefijo común: no borra scrollback; la divergencia abre frase nueva.
    assert ov._final_text == "Old Brand new"
    assert ov._translated_text == "Viejo"
    q.put(
        CaptionUpdate(
            text="Brand new",
            is_final=True,
            language="en",
            ts_mono=now,
            translated_text="Nuevo",
            seq=2,
            translation_append=False,
        )
    )
    ov._poll_queue()
    assert ov._translated_text == "Viejo Nuevo"
    ov.close()


def test_partial_can_carry_translation(qapp: QtWidgets.QApplication) -> None:
    ov, q = _overlay(qapp, cfg={"captions_show_partials": True})
    now = time.monotonic()
    q.put(
        CaptionUpdate(
            text="Hello",
            is_final=True,
            language="en",
            ts_mono=now,
            translated_text="Hola",
            seq=1,
            translation_append=False,
        )
    )
    ov._poll_queue()
    q.put(
        CaptionUpdate(
            text="Hello world",
            is_final=False,
            language="en",
            ts_mono=now,
            translated_text="Hola mundo",
            seq=1,
            translation_append=False,
        )
    )
    ov._poll_queue()
    assert ov._final_text == "Hello"
    assert ov._partial_text == "world"
    assert ov._translated_text == "Hola mundo"
    ov.close()


def test_notice_shows_without_changing_captions(qapp: QtWidgets.QApplication) -> None:
    ov, q = _overlay(qapp)
    now = time.monotonic()
    q.put(
        CaptionUpdate(
            text="Hello",
            is_final=True,
            language="en",
            ts_mono=now,
            translated_text="Hola",
            seq=1,
        )
    )
    ov._poll_queue()
    q.put(
        CaptionUpdate(
            text="",
            is_final=False,
            language="en",
            ts_mono=now,
            notice="Traducción en CPU (CUDA no disponible). Puede ir más lenta.",
        )
    )
    ov._poll_queue()
    assert ov._final_text == "Hello"
    assert ov._translated_text == "Hola"
    assert not ov.notice_label.isHidden()
    assert "CPU" in ov.notice_label.text()
    ov._clear_notice()
    assert ov.notice_label.isHidden()
    ov.close()


def test_show_partials_false_ignores_partial(qapp: QtWidgets.QApplication) -> None:
    ov, q = _overlay(qapp)
    ov.config["captions_show_partials"] = False
    ov.config["translation_enabled"] = False
    ov._sync_translation_ui()
    now = time.monotonic()
    q.put(
        CaptionUpdate(
            text="Hello",
            is_final=True,
            language="en",
            ts_mono=now,
            seq=1,
        )
    )
    ov._poll_queue()
    q.put(
        CaptionUpdate(
            text="Hello world",
            is_final=False,
            language="en",
            ts_mono=now,
            seq=1,
        )
    )
    ov._poll_queue()
    assert ov._final_text == "Hello"
    assert ov._partial_text == ""
    assert ov.partial_label.isHidden()
    ov.close()


def test_allow_rewrite_false_keeps_confirmed_text(
    qapp: QtWidgets.QApplication,
) -> None:
    ov, q = _overlay(qapp)
    ov.config["captions_allow_rewrite"] = False
    now = time.monotonic()
    q.put(
        CaptionUpdate(
            text="Hello world",
            is_final=True,
            language="en",
            ts_mono=now,
            seq=1,
        )
    )
    ov._poll_queue()
    q.put(
        CaptionUpdate(
            text="Hello there",
            is_final=True,
            language="en",
            ts_mono=now,
            seq=2,
        )
    )
    ov._poll_queue()
    assert ov._final_text == "Hello world"
    q.put(
        CaptionUpdate(
            text="Hello world today",
            is_final=True,
            language="en",
            ts_mono=now,
            seq=3,
        )
    )
    ov._poll_queue()
    assert ov._final_text == "Hello world today"
    ov.close()


def test_allow_rewrite_false_skips_tx_for_ignored_en_rewrite(
    qapp: QtWidgets.QApplication,
) -> None:
    ov, q = _overlay(qapp)
    ov.config["captions_allow_rewrite"] = False
    now = time.monotonic()
    q.put(
        CaptionUpdate(
            text="Hello world",
            is_final=True,
            language="en",
            ts_mono=now,
            translated_text="Hola mundo",
            seq=1,
        )
    )
    ov._poll_queue()
    q.put(
        CaptionUpdate(
            text="Hello there",
            is_final=True,
            language="en",
            ts_mono=now,
            translated_text="Hola allí",
            seq=2,
        )
    )
    ov._poll_queue()
    assert ov._final_text == "Hello world"
    assert ov._translated_text == "Hola mundo"
    ov.close()


def test_divergent_without_prefix_appends_scrollback(
    qapp: QtWidgets.QApplication,
) -> None:
    ov, q = _overlay(qapp)
    now = time.monotonic()
    q.put(
        CaptionUpdate(
            text="Long confirmed monologue about cats",
            is_final=True,
            language="en",
            ts_mono=now,
            seq=1,
        )
    )
    ov._poll_queue()
    q.put(
        CaptionUpdate(
            text="Dogs are great pets",
            is_final=True,
            language="en",
            ts_mono=now,
            seq=2,
        )
    )
    ov._poll_queue()
    assert ov._final_text == (
        "Long confirmed monologue about cats Dogs are great pets"
    )
    assert ov._phrase_final == "Dogs are great pets"
    ov.close()


def test_divergent_with_shared_prefix_rewrites_phrase(
    qapp: QtWidgets.QApplication,
) -> None:
    ov, q = _overlay(qapp)
    now = time.monotonic()
    q.put(
        CaptionUpdate(
            text="First",
            is_final=True,
            language="en",
            ts_mono=now,
            seq=1,
        )
    )
    ov._poll_queue()
    q.put(
        CaptionUpdate(
            text="",
            is_final=True,
            language="en",
            ts_mono=now,
            reset_display=True,
        )
    )
    ov._poll_queue()
    q.put(
        CaptionUpdate(
            text="Hello world",
            is_final=True,
            language="en",
            ts_mono=now,
            seq=2,
        )
    )
    ov._poll_queue()
    q.put(
        CaptionUpdate(
            text="Hello there",
            is_final=True,
            language="en",
            ts_mono=now,
            seq=3,
        )
    )
    ov._poll_queue()
    assert ov._final_text == "First Hello there"
    assert ov._phrase_final == "Hello there"
    ov.close()


def test_reset_keeps_caption_and_appends_next_phrase(
    qapp: QtWidgets.QApplication,
) -> None:
    ov, q = _overlay(qapp)
    ov.config["captions_allow_rewrite"] = False
    now = time.monotonic()
    q.put(
        CaptionUpdate(
            text="First",
            is_final=True,
            language="en",
            ts_mono=now,
            translated_text="Primero",
            seq=1,
        )
    )
    ov._poll_queue()
    q.put(
        CaptionUpdate(
            text="",
            is_final=True,
            language="en",
            ts_mono=now,
            reset_display=True,
        )
    )
    ov._poll_queue()
    assert ov._final_text == "First"
    assert ov._translated_text == "Primero"
    q.put(
        CaptionUpdate(
            text="Second",
            is_final=True,
            language="en",
            ts_mono=now,
            translated_text="Segundo",
            seq=2,
        )
    )
    ov._poll_queue()
    assert ov._final_text == "First Second"
    assert ov._translated_text == "Primero Segundo"
    q.put(
        CaptionUpdate(
            text="Second more",
            is_final=True,
            language="en",
            ts_mono=now,
            translated_text="Segundo mas",
            seq=3,
        )
    )
    ov._poll_queue()
    assert ov._final_text == "First Second more"
    assert ov._translated_text == "Primero Segundo mas"
    ov.close()


def test_reset_appends_when_overlay_overflows(
    qapp: QtWidgets.QApplication,
) -> None:
    ov, q = _overlay(qapp)
    now = time.monotonic()
    packed = "word " * 80
    q.put(
        CaptionUpdate(
            text=packed,
            is_final=True,
            language="en",
            ts_mono=now,
            translated_text=packed,
            seq=1,
        )
    )
    ov._poll_queue()
    q.put(
        CaptionUpdate(
            text="",
            is_final=True,
            language="en",
            ts_mono=now,
            reset_display=True,
        )
    )
    ov._poll_queue()
    q.put(
        CaptionUpdate(
            text="Next",
            is_final=True,
            language="en",
            ts_mono=now,
            translated_text="Siguiente",
            seq=2,
        )
    )
    ov._poll_queue()
    assert ov._final_text.endswith("Next")
    assert packed.strip().split()[0] in ov._final_text
    assert ov._translated_text.endswith("Siguiente")
    ov.close()


def test_resize_hit_test_detects_edges(qapp: QtWidgets.QApplication) -> None:
    from PyQt6 import QtCore

    ov, _q = _overlay(qapp)
    ov.resize(400, 200)
    assert ov._hit_test_resize(QtCore.QPoint(4, 100)) == (True, False, False, False)
    assert ov._hit_test_resize(QtCore.QPoint(396, 100)) == (False, True, False, False)
    assert ov._hit_test_resize(QtCore.QPoint(200, 4)) == (False, False, True, False)
    assert ov._hit_test_resize(QtCore.QPoint(200, 196)) == (False, False, False, True)
    assert ov._hit_test_resize(QtCore.QPoint(200, 100)) is None
    ov.close()


def test_resize_from_right_updates_width(qapp: QtWidgets.QApplication) -> None:
    from PyQt6 import QtCore

    ov, _q = _overlay(qapp, cfg={"window_width": 400, "window_height": 200})
    ov.show()
    qapp.processEvents()
    assert ov.width() == 400
    start = QtCore.QPoint(400, 100)
    ov._begin_resize((False, True, False, False), start)
    ov._continue_resize(start + QtCore.QPoint(80, 0))
    assert ov.width() == 480
    ov._handle_mouse_release()
    assert ov.config["window_width"] == 480
    assert ov.config["window_height"] == 200
    ov.close()


def test_restore_geometry_applies_saved_size(
    qapp: QtWidgets.QApplication,
) -> None:
    ov, _q = _overlay(
        qapp,
        cfg={
            "window_width": 640,
            "window_height": 240,
            "window_pos": [40, 60],
        },
    )
    ov.resize(900, 120)
    ov._restore_geometry()
    assert ov.width() == 640
    assert ov.height() == 240
    ov.close()


def test_restore_geometry_defaults_to_bottom_center(
    qapp: QtWidgets.QApplication,
) -> None:
    from PyQt6 import QtGui

    from src.ui.overlay import _BOTTOM_MARGIN_PX

    ov, _q = _overlay(
        qapp,
        cfg={
            "window_width": 640,
            "window_height": 180,
            "window_pos": None,
        },
    )
    ov._restore_geometry()
    screen = QtGui.QGuiApplication.primaryScreen()
    assert screen is not None
    geo = screen.availableGeometry()
    assert ov.x() == geo.x() + (geo.width() - ov.width()) // 2
    assert ov.y() == max(geo.top(), geo.bottom() - ov.height() - _BOTTOM_MARGIN_PX)
    ov.close()


def test_clamp_to_screens_recenters_when_offscreen(
    qapp: QtWidgets.QApplication,
) -> None:
    from PyQt6 import QtGui

    ov, _q = _overlay(
        qapp,
        cfg={
            "window_width": 500,
            "window_height": 160,
            "window_pos": [-50000, -50000],
        },
    )
    ov._restore_geometry()
    expected = ov._default_bottom_center()
    assert expected is not None
    assert (ov.x(), ov.y()) == expected
    screen = QtGui.QGuiApplication.primaryScreen()
    assert screen is not None
    assert screen.availableGeometry().contains(ov.frameGeometry().center())
    ov.close()


def test_sync_translation_ui_does_not_grow_saved_height(
    qapp: QtWidgets.QApplication,
) -> None:
    ov, _q = _overlay(
        qapp,
        cfg={
            "window_width": 800,
            "window_height": 140,
            "translation_enabled": False,
        },
    )
    ov.resize(800, 140)
    ov._sync_translation_ui()
    assert ov.height() == 140
    ov.close()


def test_caption_scroll_pins_to_bottom(qapp: QtWidgets.QApplication) -> None:
    ov, q = _overlay(qapp)
    ov.resize(800, 220)
    ov.show()
    now = time.monotonic()
    q.put(
        CaptionUpdate(
            text="word " * 80,
            is_final=True,
            language="en",
            ts_mono=now,
            seq=1,
        )
    )
    ov._poll_queue()
    qapp.processEvents()
    ov._apply_caption_geometry()
    qapp.processEvents()
    bar = ov._caption_scroll.verticalScrollBar()
    assert bar.maximum() > 0
    assert bar.value() == bar.maximum()
    ov.close()


def test_reset_display_keeps_caption_until_next_phrase(
    qapp: QtWidgets.QApplication,
) -> None:
    ov, q = _overlay(qapp)
    now = time.monotonic()
    q.put(
        CaptionUpdate(
            text="Hello",
            is_final=True,
            language="en",
            ts_mono=now,
            translated_text="Hola",
            seq=1,
        )
    )
    ov._poll_queue()
    q.put(
        CaptionUpdate(
            text="",
            is_final=True,
            language="en",
            ts_mono=now,
            reset_display=True,
        )
    )
    ov._poll_queue()
    assert ov._final_text == "Hello"
    assert ov._partial_text == ""
    assert ov._translated_text == "Hola"
    assert ov._pending_new_phrase is True
    ov.close()


def test_apply_config_text_align_left(qapp: QtWidgets.QApplication) -> None:
    from PyQt6 import QtCore

    ov, _q = _overlay(qapp)
    assert (
        ov.final_label.alignment() & QtCore.Qt.AlignmentFlag.AlignHCenter
        == QtCore.Qt.AlignmentFlag.AlignHCenter
    )
    cfg = dict(ov.config)
    cfg["text_align"] = "left"
    ov.apply_config(cfg)
    assert (
        ov.final_label.alignment() & QtCore.Qt.AlignmentFlag.AlignLeft
        == QtCore.Qt.AlignmentFlag.AlignLeft
    )
    assert (
        ov.translated_label.alignment() & QtCore.Qt.AlignmentFlag.AlignLeft
        == QtCore.Qt.AlignmentFlag.AlignLeft
    )
    assert (
        ov.partial_label.alignment() & QtCore.Qt.AlignmentFlag.AlignLeft
        == QtCore.Qt.AlignmentFlag.AlignLeft
    )
    ov.close()


def test_second_line_none_hides_asr_with_translation(
    qapp: QtWidgets.QApplication,
) -> None:
    ov, q = _overlay(qapp, cfg={"second_line_mode": "none"})
    ov.show()
    qapp.processEvents()
    now = time.monotonic()
    q.put(
        CaptionUpdate(
            text="Hello",
            is_final=True,
            language="en",
            ts_mono=now,
            translated_text="Hola",
            seq=1,
        )
    )
    ov._poll_queue()
    assert ov._translated_text == "Hola"
    assert ov.final_label.isHidden()
    assert ov.final_label.text() == ""
    ov.close()


def test_second_line_original_without_translation(
    qapp: QtWidgets.QApplication,
) -> None:
    ov, q = _overlay(
        qapp,
        cfg={"translation_enabled": False, "second_line_mode": "original"},
    )
    now = time.monotonic()
    q.put(
        CaptionUpdate(
            text="Hello",
            is_final=True,
            language="en",
            ts_mono=now,
            seq=1,
        )
    )
    q.put(
        CaptionUpdate(
            text="Hello world",
            is_final=False,
            language="en",
            ts_mono=now,
            seq=1,
        )
    )
    ov._poll_queue()
    assert "Hello world" not in ov.final_label.text()
    assert "Hello" in ov.final_label.text()
    ov.close()


def test_second_line_live_asr_without_translation(
    qapp: QtWidgets.QApplication,
) -> None:
    ov, q = _overlay(
        qapp,
        cfg={
            "translation_enabled": False,
            "second_line_mode": "live_asr",
            "captions_show_partials": True,
        },
    )
    now = time.monotonic()
    q.put(
        CaptionUpdate(
            text="Hello",
            is_final=True,
            language="en",
            ts_mono=now,
            seq=1,
        )
    )
    q.put(
        CaptionUpdate(
            text="Hello world",
            is_final=False,
            language="en",
            ts_mono=now,
            seq=1,
        )
    )
    ov._poll_queue()
    assert "Hello world" in ov.final_label.text()
    assert ov.partial_label.isHidden()
    ov.close()


def test_apply_config_typography(qapp: QtWidgets.QApplication) -> None:
    ov, _q = _overlay(qapp)
    # Sin familia elegida: la fuente la pone Qt y el peso sigue siendo el de siempre.
    # (el HUD de depuración trae su propio `font-family: monospace`, sin comillas)
    assert 'font-family: "' not in ov.styleSheet()
    assert "font-weight: 600;" in ov.styleSheet()

    cfg = dict(ov.config)
    cfg["font_family"] = "DejaVu Sans"
    cfg["font_weight"] = "bold"
    ov.apply_config(cfg)
    qss = ov.styleSheet()
    assert qss.count('font-family: "DejaVu Sans";') == 3
    assert qss.count("font-weight: 700;") == 2  # traducción y confirmado, no el parcial
    ov.close()


def test_apply_config_second_line_mode(qapp: QtWidgets.QApplication) -> None:
    ov, q = _overlay(qapp, cfg={"second_line_mode": "live_asr"})
    ov.show()
    qapp.processEvents()
    now = time.monotonic()
    q.put(
        CaptionUpdate(
            text="Hello",
            is_final=True,
            language="en",
            ts_mono=now,
            translated_text="Hola",
            seq=1,
        )
    )
    ov._poll_queue()
    assert ov.final_label.isVisible()

    cfg = dict(ov.config)
    cfg["second_line_mode"] = "none"
    ov.apply_config(cfg)
    assert ov.final_label.isHidden()
    ov.close()


def test_ensure_on_top_timer_does_not_restack_when_hint_ok(
    qapp: QtWidgets.QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El tick periódico no debe raise_/wmctrl si el hint sigue activo."""
    from PyQt6 import QtCore

    ov, _q = _overlay(qapp, cfg={"always_on_top": True})
    ov.show()
    qapp.processEvents()
    assert bool(ov.windowFlags() & QtCore.Qt.WindowType.WindowStaysOnTopHint)

    calls: list[str] = []
    monkeypatch.setattr(ov, "raise_", lambda: calls.append("raise"))
    monkeypatch.setattr(
        ov, "_apply_x11_above", lambda _enabled: calls.append("wmctrl")
    )
    monkeypatch.setattr(
        ov, "_reapply_flags", lambda **_kw: calls.append("reapply")
    )

    ov._ensure_on_top()
    assert calls == []

    ov._ensure_on_top(force_restack=True)
    assert calls == ["raise", "wmctrl"]
    ov.close()


def test_ensure_on_top_repairs_missing_hint(
    qapp: QtWidgets.QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PyQt6 import QtCore

    ov, _q = _overlay(qapp, cfg={"always_on_top": True})
    ov.show()
    qapp.processEvents()

    calls: list[str] = []

    def fake_reapply(*, show_again: bool) -> None:
        del show_again
        calls.append("reapply")

    flags_without_above = ov.windowFlags() & ~QtCore.Qt.WindowType.WindowStaysOnTopHint
    monkeypatch.setattr(ov, "_reapply_flags", fake_reapply)
    monkeypatch.setattr(ov, "raise_", lambda: calls.append("raise"))
    monkeypatch.setattr(
        ov, "_apply_x11_above", lambda _enabled: calls.append("wmctrl")
    )
    monkeypatch.setattr(ov, "windowFlags", lambda: flags_without_above)

    ov._ensure_on_top()
    assert calls == ["reapply", "raise", "wmctrl"]
    ov.close()


def test_refresh_skips_identical_caption_text(
    qapp: QtWidgets.QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    ov, q = _overlay(qapp)
    now = time.monotonic()
    q.put(
        CaptionUpdate(
            text="Hello world",
            is_final=True,
            language="en",
            ts_mono=now,
            translated_text="Hola mundo",
            seq=1,
        )
    )
    ov._poll_queue()

    calls: list[str] = []
    real_set = ov.final_label.setText

    def tracked_set(text: str) -> None:
        calls.append(text)
        real_set(text)

    monkeypatch.setattr(ov.final_label, "setText", tracked_set)
    ov._refresh_caption_texts()
    assert calls == []

    ov._final_text = "Hello there"
    ov._phrase_final = "Hello there"
    ov._refresh_caption_texts()
    assert len(calls) == 1
    assert "Hello there" in calls[0]
    ov.close()


def test_rewrite_refresh_keeps_updates_batched(
    qapp: QtWidgets.QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un rewrite no debe pintar frames intermedios del scroll/labels."""
    ov, q = _overlay(qapp)
    now = time.monotonic()
    q.put(
        CaptionUpdate(
            text="Hello world",
            is_final=True,
            language="en",
            ts_mono=now,
            translated_text="Hola mundo",
            seq=1,
        )
    )
    ov._poll_queue()

    states: list[tuple[bool, bool]] = []
    real_set_text = ov.final_label.setText

    def tracked_set(text: str) -> None:
        states.append((ov.updatesEnabled(), ov._caption_scroll.updatesEnabled()))
        real_set_text(text)

    monkeypatch.setattr(ov.final_label, "setText", tracked_set)
    q.put(
        CaptionUpdate(
            text="Hello there",
            is_final=True,
            language="en",
            ts_mono=now,
            translated_text="Hola alli",
            seq=2,
        )
    )
    ov._poll_queue()
    assert states
    assert all(not overlay_on and not scroll_on for overlay_on, scroll_on in states)
    assert ov.updatesEnabled()
    assert ov._caption_scroll.updatesEnabled()
    assert ov._final_text == "Hello there"
    ov.close()


def test_preset_selector_lists_factory_presets_with_labels(
    qapp: QtWidgets.QApplication,
) -> None:
    full_cfg = validate_config({"language": "en", "app_preset": "video-fr-es"})
    ov, _ = _overlay(qapp, cfg=full_cfg)
    labels = [
        ov.preset_selector.itemText(i) for i in range(ov.preset_selector.count())
    ]
    assert "(ninguno)" in labels
    assert "Predeterminado" in labels
    assert "Vídeo: Francés" in labels
    assert ov.preset_selector.currentData() == "video-fr-es"
    assert ov.preset_selector.currentText() == "Vídeo: Francés"
    ov.close()


def test_selecting_preset_invokes_callback_once(qapp: QtWidgets.QApplication) -> None:
    full_cfg = validate_config({"language": "en"})
    ov, _ = _overlay(qapp, cfg=full_cfg)
    calls: list[str | None] = []
    ov.on_apply_app_preset = calls.append
    idx = ov.preset_selector.findData("video-de-es")
    ov.preset_selector.setCurrentIndex(idx)
    assert calls == ["video-de-es"]
    ov.close()


def test_reentrant_apply_config_updates_selector_without_recursing(
    qapp: QtWidgets.QApplication,
) -> None:
    """El callback puede reaplicar la config al vuelo (patrón del AppController)
    sin que el refresco del combo dispare una segunda llamada al callback."""
    full_cfg = validate_config({"language": "en"})
    ov, _ = _overlay(qapp, cfg=full_cfg)
    calls: list[str | None] = []

    def _apply(preset_id: str | None) -> str | None:
        calls.append(preset_id)
        new_cfg = apply_app_preset(ov.config, preset_id)
        ov.apply_config(new_cfg)
        return None

    ov.on_apply_app_preset = _apply
    idx = ov.preset_selector.findData("video-pt-es")
    ov.preset_selector.setCurrentIndex(idx)
    assert calls == ["video-pt-es"]
    assert ov.config["language"] == "pt"
    assert ov.lang_label.text() == "PT"
    assert ov.preset_selector.currentData() == "video-pt-es"
    ov.close()


def test_apply_config_refreshes_selector_without_triggering_callback(
    qapp: QtWidgets.QApplication,
) -> None:
    """Cuando la config llega de fuera (p. ej. Settings), el combo se sincroniza
    pero no debe volver a pedir la aplicación del preset."""
    full_cfg = validate_config({"language": "en", "app_preset": "video-en-es"})
    ov, _ = _overlay(qapp, cfg=full_cfg)
    calls: list[str | None] = []
    ov.on_apply_app_preset = calls.append
    new_cfg = apply_app_preset(ov.config, "video-it-es")
    ov.apply_config(new_cfg)
    assert calls == []
    assert ov.preset_selector.currentData() == "video-it-es"
    assert ov.preset_selector.currentText() == "Vídeo: Italiano"
    ov.close()


def test_preset_warning_shows_notice(qapp: QtWidgets.QApplication) -> None:
    full_cfg = validate_config({"language": "en"})
    ov, _ = _overlay(qapp, cfg=full_cfg)
    ov._notice_timer.stop()
    ov.on_apply_app_preset = lambda _preset_id: "monitor ausente"
    idx = ov.preset_selector.findData("video-de-es")
    ov.preset_selector.setCurrentIndex(idx)
    assert not ov.notice_label.isHidden()
    assert ov.notice_label.text() == "monitor ausente"
    ov.close()


def test_chrome_slot_anim_preserves_caption_height(
    qapp: QtWidgets.QApplication,
) -> None:
    from src.ui.overlay import _CHROME_CONTROLS_HEIGHT

    ov, _ = _overlay(qapp)
    ov._chrome_hide_timer.stop()
    ov._chrome_anim.setDuration(0)
    ov.resize(800, 220)
    ov._apply_caption_geometry()
    caption_h = ov._caption_scroll.height()
    assert ov._chrome_slot_px == 0
    assert not ov._chrome_target_visible

    ov._reveal_chrome()
    qapp.processEvents()
    assert ov._chrome_target_visible
    assert ov._chrome_slot_px == _CHROME_CONTROLS_HEIGHT
    assert ov._caption_scroll.height() == caption_h
    assert ov.height() >= 220 + _CHROME_CONTROLS_HEIGHT

    ov._hide_chrome_bar()
    qapp.processEvents()
    assert not ov._chrome_target_visible
    assert ov._chrome_slot_px == 0
    assert ov._caption_scroll.height() == caption_h
    ov.close()
