from __future__ import annotations

import queue
import threading

from src.asr.pipeline import (
    AsrPipeline,
    TxCheckpoint,
    plan_sticky_translation,
    translate_confirmed,
)
from src.asr.translate import NullTranslator
from src.config import TRANSLATION_FACTORY_PRESETS, validate_config


class RecordingTranslator:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[tuple[str, str, str, dict[str, float | int] | None]] = []
        self.fail = fail

    def translate(
        self,
        text: str,
        source_lang: str,
        target_lang: str = "es",
        decode: dict[str, float | int] | None = None,
    ) -> str:
        self.calls.append((text, source_lang, target_lang, decode))
        if self.fail:
            raise RuntimeError("boom")
        return f"ES:{text}"


def test_translate_confirmed_skips_when_disabled() -> None:
    t = RecordingTranslator()
    assert (
        translate_confirmed(
            "hello",
            source_lang="en",
            target_lang="es",
            translation_enabled=False,
            translator=t,
        )
        is None
    )
    assert t.calls == []


def test_translate_confirmed_skips_same_language() -> None:
    t = RecordingTranslator()
    assert (
        translate_confirmed(
            "hola",
            source_lang="es",
            target_lang="es",
            translation_enabled=True,
            translator=t,
        )
        is None
    )
    assert t.calls == []


def test_translate_confirmed_calls_translator_for_en_es() -> None:
    t = RecordingTranslator()
    decode = {"beam_size": 6, "length_penalty": 1.1, "no_repeat_ngram_size": 3}
    out = translate_confirmed(
        "hello world",
        source_lang="en",
        target_lang="es",
        translation_enabled=True,
        translator=t,
        decode=decode,
    )
    assert out == "ES:hello world"
    assert t.calls == [("hello world", "en", "es", decode)]


def test_translate_confirmed_swallows_errors() -> None:
    t = RecordingTranslator(fail=True)
    out = translate_confirmed(
        "hello",
        source_lang="en",
        target_lang="es",
        translation_enabled=True,
        translator=t,
    )
    assert out is None
    assert len(t.calls) == 1


class FallbackNoticeTranslator:
    def __init__(self) -> None:
        self._pending = "Traducción en CPU (CUDA no disponible). Puede ir más lenta."

    def translate(
        self,
        text: str,
        source_lang: str,
        target_lang: str = "es",
        decode: dict[str, float | int] | None = None,
    ) -> str:
        return f"ES:{text}"

    def take_cpu_fallback_notice(self) -> str | None:
        msg = self._pending
        self._pending = None
        return msg


def test_notify_translator_device_emits_notice_once() -> None:
    q: queue.Queue = queue.Queue()
    cfg = validate_config(
        {
            "translation_enabled": True,
            "language": "en",
            "translation_target": "es",
            "device": "cuda",
        }
    )
    t = FallbackNoticeTranslator()
    pipeline = AsrPipeline(cfg, q, translator=t)
    pipeline._notify_translator_device(t)
    pipeline._notify_translator_device(t)

    notices = []
    while True:
        try:
            item = q.get_nowait()
        except queue.Empty:
            break
        if item.notice:
            notices.append(item.notice)
    assert len(notices) == 1
    assert "CPU" in notices[0]


def test_apply_translation_settings_hot_swaps_without_start() -> None:
    cfg = validate_config(
        {
            "translation_enabled": False,
            "translation_target": "es",
            "translator_model": "nllb-200-distilled-ct2",
            "device": "cpu",
            "language": "en",
            "latency_mode": "stable",
        }
    )
    pipeline = AsrPipeline(cfg, queue.Queue(), translator=NullTranslator())
    assert isinstance(pipeline.translation_snapshot()[2], NullTranslator)

    cfg["translation_enabled"] = True
    pipeline.apply_translation_settings(cfg)
    enabled, target, translator, decode = pipeline.translation_snapshot()
    assert enabled is True
    assert target == "es"
    assert type(translator).__name__ == "NllbCt2Translator"
    assert not getattr(translator, "is_loaded", True)
    assert decode == TRANSLATION_FACTORY_PRESETS["balanced"]

    cfg["translation_enabled"] = False
    pipeline.apply_translation_settings(cfg)
    enabled, _, translator, _ = pipeline.translation_snapshot()
    assert enabled is False
    assert isinstance(translator, NullTranslator)


def test_apply_translation_settings_decode_only_keeps_translator() -> None:
    cfg = validate_config(
        {
            "translation_enabled": True,
            "translator_model": "nllb-200-distilled-ct2",
            "device": "cpu",
            "translation_decode_preset": "balanced",
        }
    )
    pipeline = AsrPipeline(cfg, queue.Queue())
    pipeline.apply_translation_settings(cfg)
    _, _, first, _ = pipeline.translation_snapshot()

    cfg["translation_decode_preset"] = "quality"
    pipeline.apply_translation_settings(cfg)
    _, _, second, decode = pipeline.translation_snapshot()
    assert second is first
    assert decode == TRANSLATION_FACTORY_PRESETS["quality"]


def test_apply_latency_settings_updates_streamer_and_pump() -> None:
    from src.asr.engine import WhisperEngine
    from src.audio.capture import AudioRingBuffer, ChunkPump

    cfg = validate_config({"latency_mode": "stable", "language": "en"})
    pipeline = AsrPipeline(cfg, queue.Queue(), translator=NullTranslator())
    pipeline._engine = WhisperEngine(language="en", beam_size=5)
    pipeline._pump = ChunkPump(AudioRingBuffer(), min_chunk_seconds=0.8)

    cfg["latency_mode"] = "mi-perfil"
    cfg["latency_profiles"] = {
        "stable": dict(cfg["latency_profiles"]["stable"]),
        "low": dict(cfg["latency_profiles"]["low"]),
        "mi-perfil": {
            "agreement_n": 3,
            "max_latency_sec": 2.0,
            "min_chunk_seconds": 0.4,
            "beam_size": 3,
        },
    }
    cfg = validate_config(cfg)
    pipeline.apply_latency_settings(cfg)

    assert pipeline._streamer.agreement_n == 3
    assert abs(pipeline._streamer.max_latency_sec - 2.0) < 1e-9
    assert abs(pipeline._pump.min_chunk_seconds - 0.4) < 1e-9
    assert pipeline._engine.beam_size == 3
    assert pipeline.config["latency_mode"] == "mi-perfil"


def test_emit_committed_translates_delta_and_appends_flag() -> None:
    q: queue.Queue = queue.Queue()
    t = RecordingTranslator()
    cfg = validate_config(
        {
            "translation_enabled": True,
            "language": "en",
            "translation_target": "es",
            "translation_sticky_mode": "off",
            "device": "cpu",
        }
    )
    pipeline = AsrPipeline(cfg, q, translator=t)
    pipeline._emit_committed("Hello", language="en", now=1.0)
    pipeline.flush_translations()
    pipeline._emit_committed("Hello world", language="en", now=2.0)
    pipeline.flush_translations()

    items = []
    while True:
        try:
            items.append(q.get_nowait())
        except queue.Empty:
            break

    # ASR + TX por cada commit (worker al día, sin coalesce)
    assert [i.seq for i in items] == [1, 1, 2, 2]
    assert items[0].translated_text is None
    assert items[1].translated_text == "ES:Hello"
    assert items[1].translation_append is False
    assert items[2].text == "Hello world"
    assert items[2].translated_text is None
    assert items[3].translated_text == "ES:world"
    assert items[3].translation_append is True
    assert t.calls[0][0] == "Hello"
    assert t.calls[1][0] == "world"
    pipeline.stop()


def test_emit_committed_shorten_still_emits_and_allows_growth() -> None:
    """Un acortamiento del streamer no debe bloquear commits posteriores."""
    q: queue.Queue = queue.Queue()
    t = RecordingTranslator()
    cfg = validate_config(
        {
            "translation_enabled": True,
            "language": "en",
            "translation_target": "es",
            "translation_sticky_mode": "off",
            "device": "cpu",
        }
    )
    pipeline = AsrPipeline(cfg, q, translator=t)
    pipeline._emit_committed("Hello world today", language="en", now=1.0)
    pipeline.flush_translations()
    while not q.empty():
        q.get_nowait()
    t.calls.clear()

    pipeline._emit_committed("Hello world", language="en", now=2.0)
    pipeline.flush_translations()
    pipeline._emit_committed("Hello world again", language="en", now=3.0)
    pipeline.flush_translations()

    items = []
    while True:
        try:
            items.append(q.get_nowait())
        except queue.Empty:
            break

    asr = [i for i in items if i.translated_text is None]
    assert [i.text for i in asr] == ["Hello world", "Hello world again"]
    assert any(i.translated_text == "ES:Hello world" for i in items)
    assert any(i.translated_text == "ES:again" for i in items)
    pipeline.stop()


def test_emit_committed_no_rewrite_skips_shrink_and_divergent() -> None:
    q: queue.Queue = queue.Queue()
    t = RecordingTranslator()
    cfg = validate_config(
        {
            "translation_enabled": True,
            "language": "en",
            "translation_target": "es",
            "translation_sticky_mode": "off",
            "captions_allow_rewrite": False,
            "device": "cpu",
        }
    )
    pipeline = AsrPipeline(cfg, q, translator=t)
    pipeline._emit_committed("Hello world", language="en", now=1.0)
    pipeline.flush_translations()
    _drain(q)
    t.calls.clear()

    pipeline._emit_committed("Hello", language="en", now=2.0)
    pipeline._emit_committed("Hello there", language="en", now=3.0)
    pipeline.flush_translations()
    assert t.calls == []
    assert _drain(q) == []

    pipeline._emit_committed("Hello world today", language="en", now=4.0)
    pipeline.flush_translations()
    items = _drain(q)
    asr = [i for i in items if i.translated_text is None]
    assert [i.text for i in asr] == ["Hello world today"]
    assert any(
        i.translated_text in ("ES:today", "ES:Hello world today") for i in items
    )
    pipeline.stop()


def test_tx_worker_coalesces_to_latest_span() -> None:
    """Con backlog: emite lo ya traducido y luego el delta hasta lo último."""
    q: queue.Queue = queue.Queue()
    gate = threading.Event()
    release = threading.Event()

    class BlockingTranslator(RecordingTranslator):
        def translate(
            self,
            text: str,
            source_lang: str,
            target_lang: str = "es",
            decode: dict[str, float | int] | None = None,
        ) -> str:
            gate.set()
            release.wait(timeout=2.0)
            return super().translate(text, source_lang, target_lang, decode)

    t = BlockingTranslator()
    cfg = validate_config(
        {
            "translation_enabled": True,
            "language": "en",
            "translation_target": "es",
            "translation_sticky_mode": "off",
            "device": "cpu",
        }
    )
    pipeline = AsrPipeline(cfg, q, translator=t)
    pipeline._emit_committed("Hello", language="en", now=1.0)
    assert gate.wait(timeout=2.0)

    pipeline._emit_committed("Hello world", language="en", now=2.0)
    pipeline._emit_committed("Hello world today", language="en", now=3.0)
    release.set()
    pipeline.flush_translations()

    tx_items = []
    while True:
        try:
            item = q.get_nowait()
        except queue.Empty:
            break
        if item.translated_text is not None:
            tx_items.append(item)

    assert len(tx_items) >= 2
    assert tx_items[0].translated_text == "ES:Hello"
    assert tx_items[0].translation_append is False
    assert tx_items[-1].seq == 3
    assert tx_items[-1].translated_text == "ES:world today"
    assert tx_items[-1].translation_append is True
    pipeline.stop()


def test_tx_worker_does_not_starve_under_continuous_newer() -> None:
    """Si siempre hay un commit más nuevo al acabar el decode, igual debe emitir."""
    q: queue.Queue = queue.Queue()
    started = threading.Event()
    release = threading.Event()
    translate_count = 0
    lock = threading.Lock()

    class BlockingTranslator(RecordingTranslator):
        def translate(
            self,
            text: str,
            source_lang: str,
            target_lang: str = "es",
            decode: dict[str, float | int] | None = None,
        ) -> str:
            nonlocal translate_count
            with lock:
                translate_count += 1
                n = translate_count
            if n == 1:
                started.set()
                release.wait(timeout=2.0)
            return super().translate(text, source_lang, target_lang, decode)

    t = BlockingTranslator()
    cfg = validate_config(
        {
            "translation_enabled": True,
            "language": "en",
            "translation_target": "es",
            "device": "cpu",
        }
    )
    pipeline = AsrPipeline(cfg, q, translator=t)
    pipeline._emit_committed("One", language="en", now=1.0)
    assert started.wait(timeout=2.0)
    pipeline._emit_committed("One two", language="en", now=2.0)
    pipeline._emit_committed("One two three", language="en", now=3.0)
    pipeline._emit_committed("One two three four", language="en", now=4.0)
    release.set()
    pipeline.flush_translations()

    tx_items = []
    while True:
        try:
            item = q.get_nowait()
        except queue.Empty:
            break
        if item.translated_text is not None:
            tx_items.append(item)

    assert tx_items, "starvation: no llegó ninguna traducción"
    assert any("four" in (i.translated_text or "") or i.seq == 4 for i in tx_items)
    pipeline.stop()


def test_plan_sticky_reuses_prefix_on_tail_rewrite() -> None:
    cps = [
        TxCheckpoint("Hello", "ES:Hello"),
        TxCheckpoint("Hello word", "ES:Hello ES:word"),
    ]
    plan = plan_sticky_translation(cps, "Hello world")
    assert plan.to_translate == "world"
    assert plan.es_prefix == "ES:Hello"
    assert plan.emit_es is None


def test_plan_sticky_exact_checkpoint_skips_translate() -> None:
    cps = [TxCheckpoint("Hello", "ES:Hello")]
    plan = plan_sticky_translation(cps, "Hello")
    assert plan.to_translate == ""
    assert plan.emit_es == "ES:Hello"


def test_sticky_committed_does_not_retranslate_prefix() -> None:
    q: queue.Queue = queue.Queue()
    t = RecordingTranslator()
    cfg = validate_config(
        {
            "translation_enabled": True,
            "language": "en",
            "translation_target": "es",
            "translation_sticky_mode": "committed",
            "device": "cpu",
        }
    )
    pipeline = AsrPipeline(cfg, q, translator=t)
    pipeline._emit_committed("Hello", language="en", now=1.0)
    pipeline.flush_translations()
    pipeline._emit_committed("Hello word", language="en", now=2.0)
    pipeline.flush_translations()
    t.calls.clear()

    pipeline._emit_committed("Hello world", language="en", now=3.0)
    pipeline.flush_translations()

    assert [c[0] for c in t.calls] == ["world"]
    tx = []
    while True:
        try:
            item = q.get_nowait()
        except queue.Empty:
            break
        if item.translated_text is not None:
            tx.append(item)
    assert tx[-1].translated_text == "ES:Hello ES:world"
    assert tx[-1].translation_append is False
    pipeline.stop()


def test_sticky_partials_translates_display() -> None:
    q: queue.Queue = queue.Queue()
    t = RecordingTranslator()
    cfg = validate_config(
        {
            "translation_enabled": True,
            "language": "en",
            "translation_target": "es",
            "translation_sticky_mode": "partials",
            "captions_show_partials": True,
            "device": "cpu",
        }
    )
    pipeline = AsrPipeline(cfg, q, translator=t)
    pipeline._emit_partial("Hello there", language="en", now=1.0)
    pipeline.flush_translations()

    assert t.calls[0][0] == "Hello there"
    tx = [i for i in _drain(q) if i.translated_text is not None]
    assert tx[0].is_final is False
    assert tx[0].translated_text == "ES:Hello there"
    pipeline.stop()


def test_emit_partial_skipped_when_show_partials_false() -> None:
    q: queue.Queue = queue.Queue()
    t = RecordingTranslator()
    cfg = validate_config(
        {
            "translation_enabled": True,
            "language": "en",
            "translation_target": "es",
            "translation_sticky_mode": "partials",
            "captions_show_partials": False,
            "device": "cpu",
        }
    )
    pipeline = AsrPipeline(cfg, q, translator=t)
    pipeline._emit_partial("Hello there", language="en", now=1.0)
    pipeline.flush_translations()
    assert t.calls == []
    assert _drain(q) == []
    pipeline.stop()


def test_apply_translation_settings_resets_sticky_checkpoints() -> None:
    q: queue.Queue = queue.Queue()
    t = RecordingTranslator()
    cfg = validate_config(
        {
            "translation_enabled": True,
            "language": "en",
            "translation_target": "es",
            "translation_sticky_mode": "committed",
            "device": "cpu",
        }
    )
    pipeline = AsrPipeline(cfg, q, translator=t)
    pipeline._emit_committed("Hello", language="en", now=1.0)
    pipeline.flush_translations()
    assert pipeline._tx_checkpoints

    cfg = dict(cfg)
    cfg["translation_sticky_mode"] = "off"
    pipeline.apply_translation_settings(cfg)
    assert pipeline._tx_checkpoints == []
    pipeline.stop()


def _drain(q: queue.Queue) -> list:
    items = []
    while True:
        try:
            items.append(q.get_nowait())
        except queue.Empty:
            break
    return items
