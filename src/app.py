from __future__ import annotations

import os
import queue
import sys
import traceback
from pathlib import Path

# En GNOME/Wayland, "siempre encima" de Qt suele fallar. XWayland (xcb) sí respeta
# WindowStaysOnTopHint / _NET_WM_STATE_ABOVE, como el menú de Chrome.
os.environ.setdefault("QT_QPA_PLATFORM", "xcb")

from PyQt6 import QtCore, QtGui, QtWidgets

from src.asr.pipeline import AsrPipeline
from src.asr.types import CaptionUpdate
from src.audio.devices import list_audio_sources
from src.config import (
    apply_app_preset,
    delete_app_preset,
    load_config,
    resolve_app_root,
    resolve_config_path,
    save_app_preset,
    save_app_preset_as,
    save_config,
    validate_config,
)
from src.debug.trace import (
    SessionTracer,
    debug_trace_enabled,
    resolve_trace_path,
)
from src.ui.overlay import SubtitleOverlay
from src.ui.settings import SettingsDialog

_ASR_RESTART_KEYS = (
    "language",
    "model",
    "audio_monitor",
    "device",
    "compute_type",
    "use_vad",
)
_LATENCY_KEYS = (
    "latency_mode",
    "latency_profiles",
)
_TRANSLATION_KEYS = (
    "translation_enabled",
    "translation_target",
    "translation_sticky_mode",
    "translator_model",
    "translation_decode_preset",
    "translation_profiles",
)


class AppController:
    def __init__(self) -> None:
        self.root = resolve_app_root() or Path.cwd()
        self.config_path = resolve_config_path()
        self.config = load_config(self.config_path)
        self._ensure_audio_device()
        self.queue: queue.Queue[CaptionUpdate] = queue.Queue()
        self.pipeline: AsrPipeline | None = None
        self.overlay: SubtitleOverlay | None = None
        self._shutting_down = False
        self._tracer: SessionTracer | None = None
        if debug_trace_enabled():
            self._tracer = SessionTracer(
                resolve_trace_path(self.root), self.config
            )
            print(
                f"[debug] trazas activas → {self._tracer.path}",
                flush=True,
            )

    def _ensure_audio_device(self) -> None:
        if self.config.get("audio_monitor"):
            return
        try:
            sources = list_audio_sources()
        except Exception:
            return
        if sources:
            self.config["audio_monitor"] = sources[0].id
            save_config(self.config, self.config_path)

    def start(self) -> int:
        qt_app = QtWidgets.QApplication(sys.argv)
        qt_app.setQuitOnLastWindowClosed(True)
        qt_app.setApplicationName("Whisper Live Captions")
        qt_app.setDesktopFileName("whisper-live-captions")
        from src.ui.branding import repo_or_app_root

        icon_path = (
            repo_or_app_root()
            / "packaging"
            / "icons"
            / "whisper-live-captions-128.png"
        )
        if icon_path.is_file():
            qt_app.setWindowIcon(QtGui.QIcon(str(icon_path)))

        self.overlay = SubtitleOverlay(
            text_queue=self.queue,
            config=self.config,
            on_open_settings=self.open_settings,
            on_close_app=self.shutdown,
            on_save_config=self._save_config,
            on_restart_pipeline=self._restart_pipeline_safe,
            on_translation_changed=self._hot_swap_translator,
            on_apply_app_preset=self.apply_app_preset_from_overlay,
        )
        self.overlay.show()

        try:
            self._start_pipeline()
        except Exception as exc:
            QtWidgets.QMessageBox.critical(
                self.overlay,
                "Error al iniciar ASR",
                f"{exc}\n\nAbre ⚙ y elige dispositivo/modelo, luego Guarda.",
            )

        code = qt_app.exec()
        self.shutdown()
        return code

    def _save_config(
        self,
        cfg: dict | None = None,
        *,
        trace_reason: str = "save",
        trace_applied: str | None = None,
    ) -> None:
        if cfg is not None:
            self.config = cfg
        if self.overlay is not None:
            self.config["window_pos"] = self.overlay.current_position()
            self.config["window_width"] = self.overlay.width()
            self.config["window_height"] = self.overlay.height()
        if self._tracer is not None and not self._shutting_down:
            self._tracer.note_config(
                self.config, reason=trace_reason, applied=trace_applied
            )
        save_config(self.config, self.config_path)

    def _start_pipeline(self) -> None:
        if self.pipeline is not None:
            self.pipeline.stop()
        self._drain_caption_queue()
        if self.overlay is not None:
            self.overlay.reset_caption_stream()
        self.pipeline = AsrPipeline(
            self.config, self.queue, tracer=self._tracer
        )
        self.pipeline.start()

    def _drain_caption_queue(self) -> None:
        while True:
            try:
                self.queue.get_nowait()
            except queue.Empty:
                break

    def _restart_pipeline_safe(self) -> None:
        assert self.overlay is not None
        try:
            self._start_pipeline()
        except Exception as exc:
            QtWidgets.QMessageBox.critical(
                self.overlay, "Error al reiniciar ASR", str(exc)
            )

    def _hot_swap_translator(self) -> None:
        if self.pipeline is None:
            return
        try:
            # Settings/presets reasignan self.config; el pipeline debe ver el mismo dict.
            self.pipeline.config = self.config
            self.pipeline.apply_translation_settings(self.config)
        except Exception as exc:
            assert self.overlay is not None
            QtWidgets.QMessageBox.warning(
                self.overlay,
                "Traducción",
                f"No se pudo actualizar el traductor: {exc}\nSe mantiene el ASR.",
            )

    def _classify_config_apply(self, new_cfg: dict) -> tuple[str, bool, bool, bool]:
        asr_restart = any(
            new_cfg.get(k) != self.config.get(k) for k in _ASR_RESTART_KEYS
        )
        latency_only = (not asr_restart) and any(
            new_cfg.get(k) != self.config.get(k) for k in _LATENCY_KEYS
        )
        translation_only = (not asr_restart) and any(
            new_cfg.get(k) != self.config.get(k) for k in _TRANSLATION_KEYS
        )
        if asr_restart:
            applied = "asr_restart"
        elif latency_only and translation_only:
            applied = "latency_and_translation_hot_swap"
        elif latency_only:
            applied = "latency_hot_swap"
        elif translation_only:
            applied = "translation_hot_swap"
        else:
            applied = "none"
        return applied, asr_restart, latency_only, translation_only

    def _apply_config_runtime(
        self,
        new_cfg: dict,
        *,
        trace_reason: str,
        restore_overlay_geometry: bool = False,
    ) -> str:
        assert self.overlay is not None
        applied, asr_restart, latency_only, translation_only = (
            self._classify_config_apply(new_cfg)
        )
        self.config = validate_config(new_cfg)
        self.overlay.apply_config(self.config)
        if restore_overlay_geometry:
            self.overlay._restore_geometry()
        self._save_config(
            self.config, trace_reason=trace_reason, trace_applied=applied
        )
        if asr_restart:
            self._restart_pipeline_safe()
        else:
            if self.pipeline is not None:
                # Evitar config huérfana tras self.config = validate_config(...).
                self.pipeline.config = self.config
            if latency_only and self.pipeline is not None:
                self.pipeline.apply_latency_settings(self.config)
            if translation_only:
                self._hot_swap_translator()
        return applied

    def _merge_live_geometry(self, cfg: dict, settings_dlg: SettingsDialog | None) -> dict:
        assert self.overlay is not None
        out = dict(cfg)
        out["window_pos"] = self.overlay.current_position()
        out["window_width"] = self.overlay.width()
        out["window_height"] = self.overlay.height()
        if settings_dlg is not None:
            out.update(settings_dlg.geometry_snapshot())
        return validate_config(out)

    def _apply_app_preset(
        self, preset_id: str | None, *, dialog_parent: QtWidgets.QWidget | None = None
    ) -> str | None:
        """Aplica preset al instante. Devuelve aviso (p. ej. monitor ausente) o None."""
        assert self.overlay is not None
        parent = dialog_parent if dialog_parent is not None else self.overlay
        previous_monitor = str(self.config.get("audio_monitor") or "")
        try:
            new_cfg = apply_app_preset(self.config, preset_id)
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(parent, "Preset", str(exc))
            return None

        warning: str | None = None
        if preset_id:
            desired = str(new_cfg.get("audio_monitor") or "")
            if desired:
                try:
                    monitors = {source.id for source in list_audio_sources()}
                except Exception:
                    monitors = set()
                if desired not in monitors:
                    new_cfg["audio_monitor"] = previous_monitor
                    new_cfg = validate_config(new_cfg)
                    warning = (
                        f"El monitor «{desired}» no está disponible; "
                        "se mantiene el actual."
                    )

        self._apply_config_runtime(
            new_cfg,
            trace_reason="app_preset_apply",
            restore_overlay_geometry=bool(preset_id),
        )
        return warning

    def apply_app_preset_from_settings(
        self, preset_id: str | None, settings_dlg: SettingsDialog
    ) -> str | None:
        warning = self._apply_app_preset(preset_id, dialog_parent=settings_dlg)
        settings_dlg.reload_from_config(self.config)
        return warning

    def apply_app_preset_from_overlay(self, preset_id: str | None) -> str | None:
        return self._apply_app_preset(preset_id)

    def save_app_preset_from_settings(self, settings_dlg: SettingsDialog) -> None:
        live = self._merge_live_geometry(settings_dlg.result_config(), settings_dlg)
        try:
            self.config = save_app_preset(self.config, snapshot_src=live)
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(settings_dlg, "Preset", str(exc))
            return
        self._save_config(self.config, trace_reason="app_preset_save")
        settings_dlg.reload_from_config(self.config)

    def persist_preset_library_from_settings(
        self, settings_dlg: SettingsDialog
    ) -> None:
        """Guarda altas/borrados de presets de latencia y decoding al momento.

        El preset activo y sus valores aplicados siguen siendo cosa de Guardar;
        esto solo hace que la biblioteca no dependa de que el diálogo se acepte.
        """
        cfg = settings_dlg.result_config()
        for key in ("latency_profiles", "translation_profiles"):
            self.config[key] = cfg[key]
        self._save_config(self.config, trace_reason="preset_library")

    def save_app_preset_as_from_settings(
        self, name: str, settings_dlg: SettingsDialog
    ) -> None:
        live = self._merge_live_geometry(settings_dlg.result_config(), settings_dlg)
        try:
            self.config = save_app_preset_as(
                self.config, name, snapshot_src=live
            )
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(settings_dlg, "Preset", str(exc))
            return
        self._save_config(self.config, trace_reason="app_preset_save_as")
        settings_dlg.reload_from_config(self.config)

    def delete_app_preset_from_settings(self, settings_dlg: SettingsDialog) -> None:
        preset_id = self.config.get("app_preset")
        try:
            self.config = delete_app_preset(self.config, preset_id)
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(settings_dlg, "Preset", str(exc))
            return
        self._save_config(self.config, trace_reason="app_preset_delete")
        settings_dlg.reload_from_config(self.config)

    def open_settings(self) -> None:
        assert self.overlay is not None
        # Sin parent: si Settings es hijo del overlay, el WM (xcb) mueve ambos juntos.
        dlg = SettingsDialog(None, self.config, controller=self)
        if bool(self.config.get("always_on_top", True)):
            dlg.setWindowFlag(QtCore.Qt.WindowType.WindowStaysOnTopHint, True)
        overlay_geo = self.overlay.frameGeometry()
        dlg.apply_saved_geometry(fallback_center=overlay_geo.center())
        accepted = dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted
        # Tamaño/posición de Settings se guardan aunque se cancele.
        self.config.update(dlg.geometry_snapshot())
        self.config = validate_config(self.config)
        if not accepted:
            self._save_config(self.config, trace_reason="settings_geometry")
            return

        new_cfg = validate_config(dlg.result_config())
        # Settings trabaja sobre una copia: no pisar geometría viva del overlay.
        new_cfg = self._merge_live_geometry(new_cfg, dlg)
        self._apply_config_runtime(new_cfg, trace_reason="settings")

    def shutdown(self) -> None:
        if self._shutting_down:
            return
        self._shutting_down = True
        if self.overlay is not None:
            self._save_config()
        if self.pipeline is not None:
            self.pipeline.stop()
            self.pipeline = None
        if self._tracer is not None:
            try:
                path = self._tracer.flush()
                print(f"[debug] trace guardado en {path}", flush=True)
            except Exception as exc:
                print(f"[debug] no se pudo guardar trace: {exc}", flush=True)
            self._tracer = None
        if self.overlay is not None:
            overlay = self.overlay
            self.overlay = None
            overlay.close()
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.quit()


def main() -> None:
    try:
        controller = AppController()
        raise SystemExit(controller.start())
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)


if __name__ == "__main__":
    main()
