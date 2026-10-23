from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass
from typing import Any

from src.asr.engine import WhisperEngine
from src.asr.streaming import LocalAgreementStreamer
from src.asr.translate import Translator, create_translator, translator_fingerprint
from src.asr.types import CaptionUpdate
from src.audio.backends import AudioStream
from src.audio.capture import AudioRingBuffer, ChunkPump
from src.audio.devices import resolve_backend
from src.config import (
    effective_latency_profile,
    effective_translation_decode,
)
from src.debug.live_metrics import PERF
from src.debug.trace import SessionTracer

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _TxJob:
    seq: int
    text: str
    language: str
    is_partial: bool = False
    gen: int = 0


@dataclass(frozen=True)
class TxCheckpoint:
    src: str
    es: str


@dataclass(frozen=True)
class StickyPlan:
    to_translate: str
    es_prefix: str | None
    append: bool
    emit_es: str | None = None


def plan_sticky_translation(
    checkpoints: list[TxCheckpoint], text: str
) -> StickyPlan:
    """Planifica qué traducir reutilizando el checkpoint-prefijo más largo."""
    best: TxCheckpoint | None = None
    for cp in checkpoints:
        if text.startswith(cp.src) and (
            best is None or len(cp.src) >= len(best.src)
        ):
            best = cp
    if best is not None and best.src == text:
        return StickyPlan(
            to_translate="", es_prefix=best.es, append=False, emit_es=best.es
        )
    if best is not None:
        delta = text[len(best.src) :].strip()
        return StickyPlan(to_translate=delta, es_prefix=best.es, append=False)
    return StickyPlan(to_translate=text, es_prefix=None, append=False)


def plan_off_translation(last_tx_src: str, text: str) -> StickyPlan:
    if last_tx_src and text.startswith(last_tx_src) and text != last_tx_src:
        return StickyPlan(
            to_translate=text[len(last_tx_src) :].strip(),
            es_prefix=None,
            append=True,
        )
    return StickyPlan(to_translate=text, es_prefix=None, append=False)


def translate_confirmed(
    text: str,
    *,
    source_lang: str,
    target_lang: str,
    translation_enabled: bool,
    translator: Translator,
    decode: dict[str, float | int] | None = None,
) -> str | None:
    """Traduce texto. Devuelve None si no aplica o falla."""
    if not translation_enabled:
        return None
    src = (source_lang or "").strip().lower()
    tgt = (target_lang or "es").strip().lower() or "es"
    if not text.strip() or src == tgt:
        return None
    try:
        out = translator.translate(text, src, tgt, decode=decode)
    except Exception:
        logger.exception("Error de traducción; se muestra solo ASR")
        return None
    return out if out is not None else None


class AsrPipeline:
    def __init__(
        self,
        config: dict[str, Any],
        out_queue: queue.Queue[CaptionUpdate],
        translator: Translator | None = None,
        tracer: SessionTracer | None = None,
    ) -> None:
        self.config = config
        self.out_queue = out_queue
        self._tracer = tracer
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._capture: AudioStream | None = None
        self._engine: WhisperEngine | None = None
        self._pump: ChunkPump | None = None
        self._tx_lock = threading.Lock()
        self._translator: Translator = (
            translator if translator is not None else create_translator(config)
        )
        self._tx_fingerprint = translator_fingerprint(config)
        profile = effective_latency_profile(config)
        self._streamer = LocalAgreementStreamer(
            agreement_n=int(profile["agreement_n"]),
            max_latency_sec=float(profile["max_latency_sec"]),
        )
        self._caption_seq = 0
        self._last_committed = ""
        self._last_ui_committed = ""
        self._tx_stop = threading.Event()
        self._tx_thread: threading.Thread | None = None
        self._tx_pending_lock = threading.Lock()
        self._tx_pending_cv = threading.Condition(self._tx_pending_lock)
        self._tx_pending: _TxJob | None = None
        self._tx_busy = False
        self._tx_job_gen = 0
        self._last_tx_committed = ""
        self._tx_checkpoints: list[TxCheckpoint] = []

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return

        source = str(self.config.get("audio_monitor") or "")
        if not source:
            raise RuntimeError(
                "Selecciona un dispositivo de audio (monitor) en configuración."
            )

        profile = effective_latency_profile(self.config)
        buffer = AudioRingBuffer(
            max_seconds=float(self.config.get("buffer_trimming_sec", 15.0)) + 5.0
        )
        self._capture = resolve_backend().open_stream(source, buffer=buffer)
        self._engine = WhisperEngine(
            model_size=str(self.config.get("model", "medium")),
            device=str(self.config.get("device", "cuda")),
            compute_type=str(self.config.get("compute_type", "float16")),
            language=str(self.config.get("language", "en")),
            use_vad=bool(self.config.get("use_vad", True)),
            beam_size=int(profile["beam_size"]),
        )
        self._engine.load()
        self._preload_translator()
        self._capture.start()

        self._stop.clear()
        self._streamer = LocalAgreementStreamer(
            agreement_n=int(profile["agreement_n"]),
            max_latency_sec=float(profile["max_latency_sec"]),
        )
        self._caption_seq = 0
        self._last_committed = ""
        self._last_ui_committed = ""
        with self._tx_pending_lock:
            self._last_tx_committed = ""
            self._tx_checkpoints = []
            self._tx_pending = None
        self._ensure_tx_worker()
        self._thread = threading.Thread(
            target=self._loop, name="asr-pipeline", daemon=True
        )
        self._thread.start()

    def _preload_translator(self) -> None:
        with self._tx_lock:
            enabled = bool(self.config.get("translation_enabled", False))
            translator = self._translator
        if not enabled:
            return
        self._preload_translator_instance(translator)

    def _emit_notice(self, message: str) -> None:
        self.out_queue.put(
            CaptionUpdate(
                text="",
                is_final=False,
                language=str(self.config.get("language") or "en"),
                ts_mono=time.monotonic(),
                notice=message,
            )
        )

    def _notify_translator_device(self, translator: Translator) -> None:
        take = getattr(translator, "take_cpu_fallback_notice", None)
        if not callable(take):
            return
        message = take()
        if message:
            self._emit_notice(message)

    def _preload_translator_instance(self, translator: Translator) -> None:
        load = getattr(translator, "load", None)
        if not callable(load):
            return
        try:
            load()
            self._notify_translator_device(translator)
        except Exception:
            logger.exception(
                "No se pudo precargar el traductor; se intentará en el primer final"
            )

    def _sticky_mode(self) -> str:
        with self._tx_lock:
            mode = str(self.config.get("translation_sticky_mode") or "off").strip().lower()
        return mode if mode in ("off", "committed", "partials") else "off"

    def apply_latency_settings(self, config: dict[str, Any]) -> None:
        """Hot-swap de modo/perfiles de latencia sin reiniciar Whisper ni captura."""
        for key in (
            "latency_mode",
            "latency_profiles",
            "agreement_n",
            "max_latency_sec",
            "min_chunk_seconds",
        ):
            if key in config:
                self.config[key] = config[key]
        profile = effective_latency_profile(self.config)
        self._streamer.agreement_n = max(1, int(profile["agreement_n"]))
        self._streamer.max_latency_sec = max(0.2, float(profile["max_latency_sec"]))
        if self._pump is not None:
            self._pump.min_chunk_seconds = float(profile["min_chunk_seconds"])
        if self._engine is not None:
            self._engine.beam_size = int(profile["beam_size"])

    def apply_translation_settings(self, config: dict[str, Any]) -> None:
        """Hot-swap de flags/decode; recrea Translator solo si cambia motor/enable."""
        fingerprint = translator_fingerprint({**self.config, **config})
        with self._tx_lock:
            prev_sticky = str(
                self.config.get("translation_sticky_mode") or "off"
            ).strip().lower()
            for key in (
                "translation_enabled",
                "translation_target",
                "translation_sticky_mode",
                "translator_model",
                "device",
                "language",
                "translation_decode_preset",
                "translation_profiles",
            ):
                if key in config:
                    self.config[key] = config[key]
            new_sticky = str(
                self.config.get("translation_sticky_mode") or "off"
            ).strip().lower()
            need_recreate = fingerprint != self._tx_fingerprint
            if need_recreate:
                self._translator = create_translator(self.config)
                self._tx_fingerprint = fingerprint
            translator = self._translator
            enabled = bool(self.config.get("translation_enabled", False))
        if prev_sticky != new_sticky:
            with self._tx_pending_lock:
                self._last_tx_committed = ""
                self._tx_checkpoints = []
                self._tx_pending = None
        if need_recreate and enabled:
            threading.Thread(
                target=self._preload_translator_instance,
                args=(translator,),
                name="tx-preload",
                daemon=True,
            ).start()

    def translation_snapshot(
        self,
    ) -> tuple[bool, str, Translator, dict[str, float | int]]:
        with self._tx_lock:
            enabled = bool(self.config.get("translation_enabled", False))
            target = str(self.config.get("translation_target") or "es")
            decode = effective_translation_decode(self.config)
            return enabled, target, self._translator, decode

    def _ensure_tx_worker(self) -> None:
        if self._tx_thread is not None and self._tx_thread.is_alive():
            return
        self._tx_stop.clear()
        self._tx_thread = threading.Thread(
            target=self._tx_loop, name="tx-worker", daemon=True
        )
        self._tx_thread.start()

    def _stop_tx_worker(self, timeout: float = 3.0) -> None:
        self._tx_stop.set()
        with self._tx_pending_cv:
            self._tx_pending = None
            self._tx_pending_cv.notify_all()
        if self._tx_thread is not None:
            self._tx_thread.join(timeout=timeout)
        self._tx_thread = None

    def _schedule_translation(self, job: _TxJob) -> None:
        self._ensure_tx_worker()
        with self._tx_pending_cv:
            prev = self._tx_pending
            self._tx_job_gen += 1
            scheduled = _TxJob(
                seq=job.seq,
                text=job.text,
                language=job.language,
                is_partial=job.is_partial,
                gen=self._tx_job_gen,
            )
            self._tx_pending = scheduled
            self._tx_pending_cv.notify()
        if self._tracer is not None:
            self._tracer.tx_schedule(
                seq=scheduled.seq,
                gen=scheduled.gen,
                is_partial=scheduled.is_partial,
                chars=len(scheduled.text),
                coalesced_prev_gen=prev.gen if prev is not None else None,
            )

    @staticmethod
    def _job_is_newer(candidate: _TxJob, current: _TxJob) -> bool:
        return candidate.gen > current.gen

    def flush_translations(self, timeout: float = 2.0) -> None:
        """Espera a que el worker vacíe el pendiente (tests / apagado ordenado)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._tx_pending_lock:
                idle = self._tx_pending is None and not self._tx_busy
            if idle:
                return
            time.sleep(0.01)
        raise TimeoutError("timeout esperando traducciones pendientes")

    def _tx_loop(self) -> None:
        while not self._tx_stop.is_set():
            with self._tx_pending_cv:
                while self._tx_pending is None and not self._tx_stop.is_set():
                    self._tx_pending_cv.wait(timeout=0.2)
                if self._tx_stop.is_set():
                    return
                job = self._tx_pending
                self._tx_pending = None
                self._tx_busy = True

            assert job is not None
            try:
                self._process_tx_job(job)
            finally:
                with self._tx_pending_lock:
                    self._tx_busy = False
                    self._tx_pending_cv.notify_all()

    def _advance_tx_state(
        self, *, sticky: bool, text: str, full_es: str | None
    ) -> None:
        if sticky:
            kept = [cp for cp in self._tx_checkpoints if text.startswith(cp.src)]
            if full_es is not None:
                kept = [cp for cp in kept if cp.src != text]
                kept.append(TxCheckpoint(text, full_es))
            self._tx_checkpoints = kept
            self._last_tx_committed = text
        else:
            self._last_tx_committed = text

    def _process_tx_job(self, job: _TxJob) -> None:
        while not self._tx_stop.is_set():
            sticky_mode = self._sticky_mode()
            sticky = sticky_mode != "off"
            with self._tx_pending_lock:
                newer = self._tx_pending
                if newer is not None and self._job_is_newer(newer, job):
                    job = newer
                    self._tx_pending = None
                    continue
                if sticky:
                    plan = plan_sticky_translation(list(self._tx_checkpoints), job.text)
                else:
                    plan = plan_off_translation(self._last_tx_committed, job.text)

            if plan.emit_es is not None:
                payload: CaptionUpdate | None = None
                continue_with: _TxJob | None = None
                with self._tx_pending_lock:
                    emit_seq = job.seq
                    if self._last_committed.startswith(job.text) or job.is_partial:
                        emit_seq = max(emit_seq, self._caption_seq)
                    emit_ts = time.monotonic()
                    payload = CaptionUpdate(
                        text=job.text,
                        is_final=not job.is_partial,
                        language=job.language,
                        ts_mono=emit_ts,
                        translated_text=plan.emit_es,
                        seq=emit_seq,
                        translation_append=False,
                    )
                    self._advance_tx_state(
                        sticky=True, text=job.text, full_es=plan.emit_es
                    )
                    newer = self._tx_pending
                    if newer is not None and self._job_is_newer(newer, job):
                        continue_with = newer
                        self._tx_pending = None
                if payload is not None:
                    self.out_queue.put(payload)
                    if self._tracer is not None:
                        self._tracer.tx_done(
                            seq=payload.seq,
                            gen=job.gen,
                            decode_ms=0.0,
                            chars_in=0,
                            chars_out=len(plan.emit_es),
                            is_partial=job.is_partial,
                            reused_checkpoint=True,
                            ts_mono=payload.ts_mono,
                        )
                if continue_with is not None:
                    job = continue_with
                    continue
                return

            to_translate = plan.to_translate
            if not to_translate:
                with self._tx_pending_lock:
                    self._advance_tx_state(
                        sticky=sticky, text=job.text, full_es=plan.es_prefix
                    )
                    newer = self._tx_pending
                    if newer is not None and self._job_is_newer(newer, job):
                        job = newer
                        self._tx_pending = None
                        continue
                return

            translation_enabled, target_lang, translator, decode = (
                self.translation_snapshot()
            )
            decode_t0 = time.monotonic()
            translated = translate_confirmed(
                to_translate,
                source_lang=job.language,
                target_lang=target_lang,
                translation_enabled=translation_enabled,
                translator=translator,
                decode=decode,
            )
            self._notify_translator_device(translator)
            decode_ms = (time.monotonic() - decode_t0) * 1000.0

            # Siempre emitir y avanzar la base aunque llegue un job más nuevo
            # durante el decode (anti-starvation de la línea ES).
            payload = None
            continue_with = None
            with self._tx_pending_lock:
                if translated is not None:
                    if sticky:
                        if plan.es_prefix:
                            full_es = f"{plan.es_prefix} {translated}".strip()
                        else:
                            full_es = translated
                        append = False
                        out_es = full_es
                    else:
                        full_es = None
                        append = plan.append
                        out_es = translated
                    emit_seq = job.seq
                    if (not job.is_partial) and self._last_committed.startswith(
                        job.text
                    ):
                        emit_seq = max(emit_seq, self._caption_seq)
                    elif job.is_partial:
                        emit_seq = max(emit_seq, self._caption_seq)
                    emit_ts = time.monotonic()
                    payload = CaptionUpdate(
                        text=job.text,
                        is_final=not job.is_partial,
                        language=job.language,
                        ts_mono=emit_ts,
                        translated_text=out_es,
                        seq=emit_seq,
                        translation_append=append,
                    )
                    self._advance_tx_state(
                        sticky=sticky, text=job.text, full_es=full_es if sticky else None
                    )
                else:
                    self._advance_tx_state(
                        sticky=sticky, text=job.text, full_es=None
                    )
                newer = self._tx_pending
                if newer is not None and self._job_is_newer(newer, job):
                    continue_with = newer
                    self._tx_pending = None

            if payload is not None:
                self.out_queue.put(payload)
                if self._tracer is not None:
                    self._tracer.tx_done(
                        seq=payload.seq,
                        gen=job.gen,
                        decode_ms=decode_ms,
                        chars_in=len(to_translate),
                        chars_out=len(payload.translated_text or ""),
                        is_partial=job.is_partial,
                        reused_checkpoint=False,
                        ts_mono=payload.ts_mono,
                    )
            elif self._tracer is not None:
                self._tracer.record(
                    "tx_skip",
                    seq=job.seq,
                    gen=job.gen,
                    decode_ms=round(decode_ms, 3),
                    chars_in=len(to_translate),
                    is_partial=job.is_partial,
                )

            if continue_with is not None:
                job = continue_with
                continue
            return

    def _maybe_schedule(self, text: str, *, language: str, seq: int, is_partial: bool) -> None:
        translation_enabled, target_lang, _, _ = self.translation_snapshot()
        src = language.strip().lower()
        tgt = (target_lang or "es").strip().lower() or "es"
        if not translation_enabled or src == tgt or not text.strip():
            return
        self._schedule_translation(
            _TxJob(seq=seq, text=text, language=language, is_partial=is_partial)
        )

    def _emit_committed(self, committed: str, *, language: str, now: float) -> None:
        """Emite confirmado al instante; encola traducción async con coalescing.

        Con `captions_allow_rewrite=false` solo se envía al overlay lo que
        extiende el último texto ya mostrado (o el primer commit tras reset,
        cuando `_last_ui_committed` está vacío).
        El estado interno `_last_committed` sigue al streamer para no
        desincronizar commits posteriores.
        """
        prev = self._last_committed
        is_extension = bool(prev) and committed.startswith(prev) and committed != prev
        if is_extension:
            delta = committed[len(prev) :].strip()
            if not delta:
                self._last_committed = committed
                return

        allow_rewrite = bool(self.config.get("captions_allow_rewrite", True))
        ui_prev = self._last_ui_committed
        if not allow_rewrite and ui_prev:
            ui_extension = committed.startswith(ui_prev) and committed != ui_prev
            if not ui_extension:
                self._last_committed = committed
                return

        self._caption_seq += 1
        seq = self._caption_seq
        self._last_committed = committed
        self._last_ui_committed = committed

        self.out_queue.put(
            CaptionUpdate(
                text=committed,
                is_final=True,
                language=language,
                ts_mono=now,
                seq=seq,
                translation_append=is_extension,
            )
        )
        if self._tracer is not None:
            self._tracer.commit(
                seq=seq, text=committed, is_extension=is_extension, ts_mono=now
            )

        self._maybe_schedule(committed, language=language, seq=seq, is_partial=False)

    def _emit_partial(self, display: str, *, language: str, now: float) -> None:
        if not bool(self.config.get("captions_show_partials", False)):
            return
        self.out_queue.put(
            CaptionUpdate(
                text=display,
                is_final=False,
                language=language,
                ts_mono=now,
                seq=self._caption_seq,
            )
        )
        if self._tracer is not None:
            self._tracer.partial(seq=self._caption_seq, text=display)
        if self._sticky_mode() != "partials":
            return
        self._maybe_schedule(
            display, language=language, seq=self._caption_seq, is_partial=True
        )

    def stop(self, timeout: float = 30.0) -> None:
        """Para captura + ASR. Timeout alto: una inferencia Whisper puede superar 3 s."""
        self._stop.set()
        if self._capture is not None:
            self._capture.stop(timeout=min(timeout, 5.0))
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                logger.warning(
                    "El hilo ASR no terminó en %.1fs; se continúa el apagado", timeout
                )
        self._thread = None
        self._capture = None
        self._pump = None
        self._engine = None
        self._stop_tx_worker(timeout=min(timeout, 5.0))

    def _loop(self) -> None:
        assert self._capture is not None and self._engine is not None
        profile = effective_latency_profile(self.config)
        self._pump = ChunkPump(
            self._capture.buffer,
            min_chunk_seconds=float(profile["min_chunk_seconds"]),
        )
        trim_sec = float(self.config.get("buffer_trimming_sec", 15.0))

        sample_rate = float(self._capture.buffer.sample_rate)
        while not self._stop.is_set():
            pump = self._pump
            if pump is None:
                break
            audio = pump.poll()
            if audio is None:
                time.sleep(0.05)
                continue

            language = str(self.config.get("language", "en"))
            audio_sec = float(audio.size) / sample_rate if audio.size else 0.0
            try:
                PERF.set_asr_busy(True)
                t0 = time.monotonic()
                hypothesis = self._engine.transcribe(audio)
                infer_ms = (time.monotonic() - t0) * 1000.0
            except Exception as exc:
                PERF.set_asr_busy(False)
                if self._tracer is not None:
                    self._tracer.asr_error(str(exc))
                self.out_queue.put(
                    CaptionUpdate(
                        text=f"[ASR error] {exc}",
                        is_final=True,
                        language=language,
                        ts_mono=time.monotonic(),
                    )
                )
                time.sleep(0.5)
                continue

            buf_sec = (
                self._capture.buffer.duration_seconds()
                if self._capture is not None
                else None
            )
            PERF.note_asr_infer(
                infer_ms=infer_ms, audio_sec=audio_sec, buffer_sec=buf_sec
            )

            if self._tracer is not None:
                self._tracer.asr_infer(
                    audio_sec=audio_sec,
                    infer_ms=infer_ms,
                    hyp_len=len(hypothesis or ""),
                    hyp_preview=hypothesis or None,
                )

            if not hypothesis:
                continue

            result = self._streamer.push(hypothesis)
            now = time.monotonic()
            if result.newly_committed:
                self._emit_committed(result.committed, language=language, now=now)
            if result.partial:
                display = (result.committed + " " + result.partial).strip()
                self._emit_partial(display, language=language, now=now)

            if self._capture.buffer.duration_seconds() > trim_sec and result.committed:
                # Conserva cola del buffer para no crecer sin límite en sesiones largas.
                keep = self._capture.buffer.read_all()
                keep_samples = int(
                    self._capture.buffer.sample_rate * min(8.0, trim_sec / 2)
                )
                if keep.size > keep_samples:
                    keep_sec = keep_samples / sample_rate
                    self._capture.buffer.clear()
                    self._capture.buffer.write(keep[-keep_samples:])
                    self._streamer.reset()
                    self._last_committed = ""
                    self._last_ui_committed = ""
                    with self._tx_pending_lock:
                        self._last_tx_committed = ""
                        self._tx_checkpoints = []
                        self._tx_pending = None
                    self.out_queue.put(
                        CaptionUpdate(
                            text="",
                            is_final=True,
                            language=language,
                            ts_mono=time.monotonic(),
                            reset_display=True,
                        )
                    )
                    if self._tracer is not None:
                        self._tracer.buffer_trim(keep_sec=keep_sec)
