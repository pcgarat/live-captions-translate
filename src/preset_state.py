"""Estado de los presets de la app, independiente de Qt.

Los tres selectores de preset (general, latencia y decoding de traducción)
comparten el mismo ciclo de vida: catálogo de fábrica inmutable, presets propios
persistidos, y un estado vivo que puede diferir del preset seleccionado. Este
módulo concentra qué opciones muestra cada uno y cómo se comparan, para que la
UI no repita tres veces la misma lógica.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from src.asr.languages import language_label
from src.asr.translate import TRANSLATOR_MODEL_LABELS
from src.config import (
    COMPUTE_TYPE_LABELS,
    FONT_WEIGHT_LABELS,
    SECOND_LINE_LABELS,
    TEXT_ALIGN_LABELS,
    TRANSLATION_PRESET_LABELS,
    TRANSLATION_STICKY_LABELS,
    is_factory_preset,
    latency_preset_label,
)
from src.presets import app_preset_label

DOMAIN_APP = "app"
DOMAIN_LATENCY = "latency"
DOMAIN_DECODE = "decode"


def _yes_no(value: Any) -> str:
    return "Sí" if bool(value) else "No"


def _seconds(value: Any) -> str:
    return f"{float(value):.1f} s"


def _pixels(value: Any) -> str:
    return f"{int(value)} px"


def _percent(value: Any) -> str:
    return f"{round(float(value) * 100)} %"


def _text(value: Any) -> str:
    return str(value)


def _language(value: Any) -> str:
    code = str(value or "")
    return language_label(code) if code else "—"


def _float(value: Any) -> str:
    return f"{float(value):.1f}"


def _audio_source(value: Any) -> str:
    source = str(value or "")
    if not source:
        return "—"
    try:
        from src.audio.devices import describe_audio_source

        return describe_audio_source(source).label
    except Exception:
        # Sin pactl (tests, sesión sin audio) el id en crudo sigue siendo útil.
        return source


def _translator(value: Any) -> str:
    alias = str(value or "")
    return TRANSLATOR_MODEL_LABELS.get(alias, alias or "—")


def _decode_preset(value: Any) -> str:
    key = str(value or "")
    return TRANSLATION_PRESET_LABELS.get(key, key or "—")


@dataclass(frozen=True)
class Option:
    key: str
    label: str
    fmt: Callable[[Any], str] = _text


# Solo opciones visibles y accionables por el usuario. Geometría, lista de
# idiomas instalados, espejos y flags internos no cuentan como «modificado»:
# cambian solos (mover la ventana, instalar un idioma) y marcarían siempre sucio.
APP_OPTIONS: tuple[Option, ...] = (
    Option("language", "Idioma", _language),
    Option("model", "Modelo Whisper"),
    Option(
        "compute_type",
        "Precisión GPU",
        lambda v: COMPUTE_TYPE_LABELS.get(str(v), str(v)),
    ),
    Option("device", "Dispositivo", _text),
    Option("audio_monitor", "Monitor de audio", _audio_source),
    Option("use_vad", "VAD", _yes_no),
    Option("buffer_trimming_sec", "Buffer (s)", _seconds),
    Option("latency_mode", "Preset de latencia", latency_preset_label),
    Option("translation_enabled", "Traducción", _yes_no),
    Option("translation_target", "Idioma destino", _language),
    Option("translator_model", "Traductor", _translator),
    Option(
        "translation_sticky_mode",
        "Modo sticky",
        lambda v: TRANSLATION_STICKY_LABELS.get(str(v), str(v)),
    ),
    Option("translation_decode_preset", "Preset de decoding", _decode_preset),
    Option(
        "second_line_mode",
        "Segunda línea",
        lambda v: SECOND_LINE_LABELS.get(str(v), str(v)),
    ),
    Option("captions_show_partials", "Texto parcial", _yes_no),
    Option("captions_allow_rewrite", "Permitir corrección", _yes_no),
    Option("always_on_top", "Siempre encima", _yes_no),
    Option("font_size", "Tamaño de texto", _pixels),
    Option("font_family", "Tipo de letra", lambda v: str(v or "Sistema")),
    Option("font_weight", "Grosor", lambda v: FONT_WEIGHT_LABELS.get(str(v), str(v))),
    Option("font_color", "Color de texto", _text),
    Option("bg_color", "Color de fondo", _text),
    Option("bg_alpha", "Opacidad del fondo", _percent),
    Option("padding", "Padding", _pixels),
    Option("text_align", "Alineación", lambda v: TEXT_ALIGN_LABELS.get(str(v), str(v))),
)

LATENCY_OPTIONS: tuple[Option, ...] = (
    Option("agreement_n", "Confianza (lecturas)", lambda v: str(int(v))),
    Option("max_latency_sec", "Techo de latencia", _seconds),
    Option("min_chunk_seconds", "Trozo mínimo de audio", _seconds),
    Option("beam_size", "Beam size de Whisper", lambda v: str(int(v))),
)

DECODE_OPTIONS: tuple[Option, ...] = (
    Option("beam_size", "Beam size", lambda v: str(int(v))),
    Option("length_penalty", "Length penalty", _float),
    Option("no_repeat_ngram_size", "No-repeat n-gram", lambda v: str(int(v))),
)

OPTIONS: dict[str, tuple[Option, ...]] = {
    DOMAIN_APP: APP_OPTIONS,
    DOMAIN_LATENCY: LATENCY_OPTIONS,
    DOMAIN_DECODE: DECODE_OPTIONS,
}


def domain_preset_ids(domain: str, cfg: dict[str, Any]) -> list[str]:
    """Ids del catálogo de fábrica seguidos de los propios, sin duplicados."""
    if domain == DOMAIN_APP:
        presets = cfg.get("app_presets") or {}
        return sorted(str(key) for key in presets) if isinstance(presets, dict) else []
    if domain == DOMAIN_LATENCY:
        presets = cfg.get("latency_profiles") or {}
        return sorted(str(key) for key in presets) if isinstance(presets, dict) else []
    presets = cfg.get("translation_profiles") or {}
    return sorted(str(key) for key in presets) if isinstance(presets, dict) else []


def preset_values(
    domain: str, cfg: dict[str, Any], preset_id: str | None
) -> dict[str, Any]:
    """Valores guardados del preset (vacío si no existe)."""
    if not preset_id:
        return {}
    if domain == DOMAIN_APP:
        source = cfg.get("app_presets")
    elif domain == DOMAIN_LATENCY:
        source = cfg.get("latency_profiles")
    else:
        source = cfg.get("translation_profiles")
    if not isinstance(source, dict):
        return {}
    blob = source.get(str(preset_id))
    return dict(blob) if isinstance(blob, dict) else {}


def is_factory(domain: str, preset_id: str | None) -> bool:
    if domain == DOMAIN_APP:
        return is_factory_preset(preset_id)
    key = str(preset_id or "")
    if domain == DOMAIN_LATENCY:
        from src.config import is_factory_latency_preset

        return is_factory_latency_preset(key)
    from src.config import is_factory_translation_preset

    return is_factory_translation_preset(key)


def is_deletable(domain: str, preset_id: str | None) -> bool:
    """Un preset propio se borra; `custom` no, aunque sea editable."""
    if not preset_id:
        return False
    if domain == DOMAIN_DECODE:
        from src.config import is_deletable_translation_preset

        return is_deletable_translation_preset(preset_id)
    return not is_factory(domain, preset_id)


def preset_label(domain: str, preset_id: str | None) -> str:
    key = str(preset_id or "")
    if not key:
        return "(ninguno)"
    if domain == DOMAIN_APP:
        return app_preset_label(key)
    if domain == DOMAIN_LATENCY:
        return latency_preset_label(key)
    return TRANSLATION_PRESET_LABELS.get(key, key)


def _norm(value: Any) -> Any:
    """Normaliza para comparar: floats redondeados, textos sin espacios sobrantes."""
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        return round(value, 3)
    if isinstance(value, int):
        return value
    if value is None:
        return None
    return str(value).strip()


def changed_option_keys(
    domain: str,
    saved: dict[str, Any],
    live: dict[str, Any],
) -> list[str]:
    """Opciones que el preset fija y cuyo valor vivo difiere del guardado."""
    out: list[str] = []
    for option in OPTIONS[domain]:
        if option.key not in saved:
            continue
        # Un monitor vacío en el preset no significa «ninguno»: la app elige el
        # primer dispositivo al arrancar, y eso no es una desviación del preset.
        if option.key == "audio_monitor" and not str(saved[option.key] or ""):
            continue
        if _norm(saved[option.key]) != _norm(live.get(option.key)):
            out.append(option.key)
    return out


def is_modified(
    domain: str,
    cfg: dict[str, Any],
    preset_id: str | None,
    live: dict[str, Any],
) -> bool:
    if not preset_id:
        return False
    saved = preset_values(domain, cfg, preset_id)
    if not saved:
        return False
    return bool(changed_option_keys(domain, saved, live))


def describe(
    domain: str,
    cfg: dict[str, Any],
    preset_id: str | None,
) -> list[tuple[str, str]]:
    """Pares (opción, valor) que fija el preset, listos para «Ver valores»."""
    saved = preset_values(domain, cfg, preset_id)
    if not saved:
        return []
    return [
        (option.label, option.fmt(saved[option.key]))
        for option in OPTIONS[domain]
        if option.key in saved
    ]


def is_partial(domain: str, cfg: dict[str, Any], preset_id: str | None) -> bool:
    """Un preset general de idioma fija solo una parte del estado de la app."""
    if domain != DOMAIN_APP or not preset_id:
        return False
    saved = preset_values(domain, cfg, preset_id)
    if not saved:
        return False
    return len(saved) < len(APP_OPTIONS)

