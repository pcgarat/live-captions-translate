from __future__ import annotations

import json
import os
from collections.abc import Iterable
from copy import deepcopy
from pathlib import Path
from typing import Any

from src.asr.languages import AVAILABLE_LANGUAGES
from src.asr.translate import DEFAULT_TRANSLATOR_MODEL
from src.presets import (
    APP_FACTORY_PRESET_IDS,
    APP_FACTORY_PRESET_OVERRIDES,
    APP_PRESET_DEFAULT,
    is_factory_preset,
)

# `beam_size` viaja en el perfil para que un preset de latencia sea autocontenido:
# antes se derivaba del nombre del modo (`stable` → 5, `low` → 1) y un preset de
# usuario no podía elegirlo. Es lo único que no viene de `DEFAULTS`: allí un
# perfil es un mapa plano de knobs y el beam es una decisión del catálogo.
LATENCY_FACTORY_BEAMS: dict[str, int] = {"stable": 5, "low": 1}
LATENCY_PRESET_LABELS: dict[str, str] = {
    "stable": "Estable — más preciso",
    "low": "Baja latencia — más rápido",
}

# Decoding NLLB (fábrica). `custom` no es fábrica: perfil editable persistente.
# Los tres perfiles se diferencian solo en `beam_size`, que es el knob de latencia.
# `length_penalty` 0.7 en todos: medido en RTX 4060, con 1.0 el decoder prefiere
# hipótesis largas y rellena los fragmentos cortos («oui» → «Sí, sí.», «então» →
# «Entonces...»); con 0.7 salen limpios sin degradar las frases largas. Es el
# mínimo que admite el clamp, y 0.4 no mejora de forma apreciable.
# `no_repeat_ngram_size` 3 también en `fast`: cortar bucles de repetición no
# cuesta latencia medible y es el peor fallo posible en pantalla.
TRANSLATION_FACTORY_PRESETS: dict[str, dict[str, float | int]] = {
    "fast": {"beam_size": 2, "length_penalty": 0.7, "no_repeat_ngram_size": 3},
    "balanced": {"beam_size": 4, "length_penalty": 0.7, "no_repeat_ngram_size": 3},
    "quality": {"beam_size": 6, "length_penalty": 0.7, "no_repeat_ngram_size": 3},
}
TRANSLATION_FACTORY_PRESET_IDS = frozenset(TRANSLATION_FACTORY_PRESETS)
# `custom` es un preset propio especial: se puede sobrescribir, pero no borrar y
# su nombre no se puede reutilizar.
TRANSLATION_SPECIAL_PRESET_IDS = frozenset({"custom"})
TRANSLATION_RESERVED_PRESET_IDS = (
    TRANSLATION_FACTORY_PRESET_IDS | TRANSLATION_SPECIAL_PRESET_IDS
)
TRANSLATION_PRESET_LABELS: dict[str, str] = {
    "fast": "Rápido",
    "balanced": "Equilibrado",
    "quality": "Calidad",
    "custom": "Custom",
}

# Con traducción ON: qué mostrar como segunda línea (nunca más de 2 líneas de caption).
SECOND_LINE_MODES = ("live_asr", "original", "none")
SECOND_LINE_LABELS: dict[str, str] = {
    "live_asr": "Reconocimiento en vivo",
    "original": "Idioma original confirmado",
    "none": "Nada",
}

# Alineación horizontal del texto en el overlay.
TEXT_ALIGN_MODES = ("center", "left")
TEXT_ALIGN_LABELS: dict[str, str] = {
    "center": "Centro",
    "left": "Izquierda",
}

# Grosor del texto del overlay. `semibold` = el 600 que ya se pintaba antes de la fase 2.10.
FONT_WEIGHT_MODES = ("normal", "semibold", "bold")
FONT_WEIGHT_LABELS: dict[str, str] = {
    "normal": "Normal",
    "semibold": "Seminegrita",
    "bold": "Negrita",
}

# La familia se interpola en QSS: fuera lo que pueda cerrar la declaración o el bloque.
_FONT_FAMILY_FORBIDDEN = '"\';{}'
_FONT_FAMILY_MAX_LEN = 64

# Cuantización faster-whisper (CTranslate2). Solo Whisper; NLLB usa int8 aparte.
COMPUTE_TYPES = ("float16", "int8_float16", "int8")
COMPUTE_TYPE_LABELS: dict[str, str] = {
    "float16": "float16 — calidad (recomendado)",
    "int8_float16": "int8_float16 — menos VRAM",
    "int8": "int8 — máximo ahorro",
}

# Sticky: reutilizar tramos ya traducidos; partials también traduce la hipótesis.
TRANSLATION_STICKY_MODES = ("off", "committed", "partials")
TRANSLATION_STICKY_LABELS: dict[str, str] = {
    "off": "Normal (como ahora)",
    "committed": "Sticky (solo confirmados)",
    "partials": "Sticky + parciales",
}

# Meta de presets generales: no se anidan dentro de cada snapshot.
APP_PRESET_META_KEYS = frozenset({"app_preset", "app_presets"})

CONFIG_NAME = "config.json"
CONFIG_EXAMPLE_NAME = "config.example.json"
APP_ID = "whisper-live-captions"
ENV_APP_ROOT = "WLCL_APP_ROOT"
ENV_CONFIG_DIR = "WLCL_CONFIG_DIR"


def _builtin_defaults() -> dict[str, Any]:
    """Fallback si no hay config.example.json (p. ej. empaquetado mínimo)."""
    snap = {
        "language": "en",
        "model": "small",
        "compute_type": "float16",
        "device": "cuda",
        "audio_monitor": "",
        "buffer_trimming_sec": 15.0,
        "use_vad": True,
        "latency_mode": "stable",
        "latency_profiles": {
            "stable": {
                "agreement_n": 1,
                "max_latency_sec": 0.4,
                "min_chunk_seconds": 0.8,
                "beam_size": 5,
            },
            "low": {
                "agreement_n": 1,
                "max_latency_sec": 1.5,
                "min_chunk_seconds": 0.35,
                "beam_size": 1,
            },
        },
        "installed_languages": list(AVAILABLE_LANGUAGES.keys()),
        "translation_enabled": True,
        "translation_target": "es",
        "translation_sticky_mode": "off",
        "second_line_mode": "none",
        "captions_show_partials": False,
        "captions_allow_rewrite": True,
        "translator_model": DEFAULT_TRANSLATOR_MODEL,
        "translation_decode_preset": "custom",
        "translation_profiles": {
            **deepcopy(TRANSLATION_FACTORY_PRESETS),
            "custom": deepcopy(TRANSLATION_FACTORY_PRESETS["balanced"]),
        },
        "always_on_top": True,
        "font_size": 26,
        "font_family": "",
        "font_weight": "semibold",
        "font_color": "#ffffff",
        "bg_color": "#000000",
        "bg_alpha": 0.6,
        "padding": 20,
        "text_align": "left",
        "window_pos": None,
        "window_width": 900,
        "window_height": 170,
        "settings_window_pos": None,
        "settings_window_width": 858,
        "settings_window_height": 920,
        "opt_prefer_low_latency": False,
        "opt_prefer_fast_translation": False,
        "opt_nllb_on_cpu": False,
        "opt_tx_delta_only": True,
        "opt_tx_coalesce_emit": True,
        "opt_perf_metrics": True,
        "opt_short_caption_beam_cap": True,
        "debug_hud": False,
    }
    # `app_presets` se puebla por siembra desde el catálogo, no aquí: así los
    # snapshots no se anidan dentro de DEFAULTS (que es la base de cada snapshot).
    return {**deepcopy(snap), "app_preset": APP_PRESET_DEFAULT, "app_presets": {}}


def resolve_app_root() -> Path | None:
    """Raíz de la app instalada (`WLCL_APP_ROOT`), o None en modo desarrollo."""
    raw = str(os.environ.get(ENV_APP_ROOT, "") or "").strip()
    if not raw:
        return None
    return Path(raw).expanduser().resolve()


def resolve_config_dir() -> Path:
    """Dir de config: XDG si instalada; cwd en desarrollo."""
    raw = str(os.environ.get(ENV_CONFIG_DIR, "") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    if resolve_app_root() is not None:
        xdg = str(os.environ.get("XDG_CONFIG_HOME", "") or "").strip()
        base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
        return (base / APP_ID).resolve()
    return Path.cwd().resolve()


def resolve_config_path() -> Path:
    return resolve_config_dir() / CONFIG_NAME


def resolve_example_config_path() -> Path:
    """`config.example.json` junto a la app instalada o a la raíz del repo."""
    app_root = resolve_app_root()
    if app_root is not None:
        return app_root / CONFIG_EXAMPLE_NAME
    return Path(__file__).resolve().parent.parent / CONFIG_EXAMPLE_NAME


def _shipped_defaults() -> dict[str, Any]:
    """Defaults = config.example.json del repo/install cuando existe."""
    base = _builtin_defaults()
    example = resolve_example_config_path()
    if not example.is_file():
        # Fallback: ejemplo junto al paquete src/ (install o repo).
        example = Path(__file__).resolve().parent.parent / CONFIG_EXAMPLE_NAME
    if not example.is_file():
        return base
    try:
        raw = json.loads(example.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return base
    if not isinstance(raw, dict):
        return base
    merged = deepcopy(base)
    merged.update(deepcopy(raw))
    return merged


DEFAULTS: dict[str, Any] = _shipped_defaults()


def default_config_path(root: Path | None = None) -> Path:
    if root is not None:
        return Path(root) / CONFIG_NAME
    return resolve_config_path()


def default_example_config_path(root: Path | None = None) -> Path:
    if root is not None:
        return Path(root) / CONFIG_EXAMPLE_NAME
    return resolve_example_config_path()


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _clamp_profile(
    raw: dict[str, Any] | None, factory: dict[str, float | int]
) -> dict[str, float | int]:
    src = factory if not isinstance(raw, dict) else {**factory, **raw}
    return {
        "agreement_n": int(_clamp(int(src["agreement_n"]), 1, 5)),
        "max_latency_sec": float(_clamp(float(src["max_latency_sec"]), 0.2, 3.0)),
        "min_chunk_seconds": float(_clamp(float(src["min_chunk_seconds"]), 0.2, 5.0)),
        "beam_size": int(_clamp(int(src["beam_size"]), 1, 8)),
    }


def _latency_factory_presets() -> dict[str, dict[str, float | int]]:
    """Presets de latencia de fábrica = `latency_profiles` de los defaults.

    Se derivan para que una instalación nueva no nazca con un preset de fábrica
    «modificado»: los knobs son los mismos que el usuario ve en su config, y lo
    único que añade el catálogo es el `beam_size` de cada modo.
    """
    raw = DEFAULTS.get("latency_profiles")
    raw = raw if isinstance(raw, dict) else {}
    fallback = {"agreement_n": 1, "max_latency_sec": 0.4, "min_chunk_seconds": 0.8}
    out: dict[str, dict[str, float | int]] = {}
    for preset_id, beam in LATENCY_FACTORY_BEAMS.items():
        saved = raw.get(preset_id)
        base = saved if isinstance(saved, dict) else fallback
        out[preset_id] = _clamp_profile(
            {**base, "beam_size": beam}, {**fallback, "beam_size": beam}
        )
    return out


LATENCY_FACTORY_PRESETS: dict[str, dict[str, float | int]] = _latency_factory_presets()
LATENCY_FACTORY_PRESET_IDS = frozenset(LATENCY_FACTORY_PRESETS)


LATENCY_LEGACY_PRESET_ID = "personalizado"

_LATENCY_LEGACY_KNOBS = ("agreement_n", "max_latency_sec", "min_chunk_seconds")


def _legacy_latency_knobs(data: dict[str, Any]) -> dict[str, Any]:
    """Knobs de fase 1 que vivían sueltos en la raíz de la config."""
    return {key: data[key] for key in _LATENCY_LEGACY_KNOBS if key in data}


def _migrate_latency_profiles(
    data: dict[str, Any], mode: str
) -> dict[str, dict[str, float | int]]:
    """Perfiles de latencia: fábrica inmutable + presets propios conservados.

    Igual que los perfiles de decoding: las claves de fábrica se re-sincronizan
    en cada validación, así que editarlas exige guardarlas con otro nombre. Los
    presets propios se clampean contra `stable`, que actúa de referencia.
    """
    profiles_in = data.get("latency_profiles")
    profiles: dict[str, dict[str, float | int]] = {
        name: deepcopy(preset) for name, preset in LATENCY_FACTORY_PRESETS.items()
    }
    fallback = LATENCY_FACTORY_PRESETS["stable"]

    if isinstance(profiles_in, dict):
        for name, raw in profiles_in.items():
            key = _normalize_latency_preset_id(name)
            if (
                not key
                or key in LATENCY_FACTORY_PRESET_IDS
                or not isinstance(raw, dict)
            ):
                continue
            profiles[key] = _clamp_profile(raw, fallback)
        return profiles

    # Config fase 1: los knobs top-level pasan a un preset propio visible, para
    # no perder silenciosamente los valores que el usuario tenía puestos.
    legacy = _legacy_latency_knobs(data)
    if legacy:
        profiles[LATENCY_LEGACY_PRESET_ID] = _clamp_profile(
            {**LATENCY_FACTORY_PRESETS[mode], **legacy},
            LATENCY_FACTORY_PRESETS[mode],
        )
    return profiles


def _latency_profiles_migrate_source(
    raw: dict[str, Any], cfg: dict[str, Any]
) -> dict[str, Any]:
    """Elige la fuente de migración: input, legacy top-level, o DEFAULTS ya en cfg."""
    if isinstance(raw.get("latency_profiles"), dict):
        return raw
    if any(
        key in raw for key in ("agreement_n", "max_latency_sec", "min_chunk_seconds")
    ):
        return raw
    return {"latency_profiles": cfg.get("latency_profiles")}


def _normalize_latency_preset_id(raw: Any) -> str:
    return slugify_translation_preset_name(str(raw or ""))


def latency_user_preset_ids(cfg: dict[str, Any]) -> list[str]:
    profiles = cfg.get("latency_profiles")
    if not isinstance(profiles, dict):
        return []
    return sorted(
        key
        for key in (_normalize_latency_preset_id(name) for name in profiles)
        if key and key not in LATENCY_FACTORY_PRESET_IDS
    )


def latency_preset_label(preset_id: str) -> str:
    key = _normalize_latency_preset_id(preset_id)
    return LATENCY_PRESET_LABELS.get(key, key)


def is_factory_latency_preset(preset_id: str | None) -> bool:
    return _normalize_latency_preset_id(preset_id) in LATENCY_FACTORY_PRESET_IDS


def effective_latency_profile(cfg: dict[str, Any]) -> dict[str, float | int]:
    mode = _normalize_latency_preset_id(cfg.get("latency_mode") or "stable")
    profiles = cfg.get("latency_profiles")
    if not isinstance(profiles, dict):
        profiles = LATENCY_FACTORY_PRESETS
    raw = profiles.get(mode)
    if not isinstance(raw, dict):
        mode = "stable"
        raw = profiles.get(mode)
    return _clamp_profile(
        raw if isinstance(raw, dict) else None,
        LATENCY_FACTORY_PRESETS.get(mode, LATENCY_FACTORY_PRESETS["stable"]),
    )


def restore_latency_preset(
    cfg: dict[str, Any], preset_id: str | None = None
) -> dict[str, Any]:
    """Devuelve el preset a sus valores de referencia (fábrica o guardados)."""
    out = deepcopy(cfg)
    active = _normalize_latency_preset_id(preset_id or out.get("latency_mode"))
    profiles = out.get("latency_profiles")
    if not isinstance(profiles, dict):
        profiles = deepcopy(LATENCY_FACTORY_PRESETS)
    else:
        profiles = deepcopy(profiles)
    if active in LATENCY_FACTORY_PRESET_IDS:
        profiles[active] = deepcopy(LATENCY_FACTORY_PRESETS[active])
    out["latency_profiles"] = profiles
    out["latency_mode"] = active
    return validate_config(out)


def reset_latency_profile(
    cfg: dict[str, Any], mode: str | None = None
) -> dict[str, Any]:
    """Compatibilidad: restaura el modo a sus valores de fábrica."""
    return restore_latency_preset(cfg, mode)


def save_latency_preset(
    cfg: dict[str, Any],
    preset_id: str | None,
    profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Sobrescribe un preset propio y lo deja activo. Fábrica → ValueError."""
    out = deepcopy(cfg)
    key = _normalize_latency_preset_id(preset_id or out.get("latency_mode"))
    if not key:
        raise ValueError("No hay preset de latencia activo")
    if key in LATENCY_FACTORY_PRESET_IDS:
        raise ValueError(f"El preset de fábrica «{key}» no se puede sobrescribir")
    profiles = out.get("latency_profiles")
    if not isinstance(profiles, dict):
        profiles = deepcopy(LATENCY_FACTORY_PRESETS)
    else:
        profiles = deepcopy(profiles)
    if key not in profiles:
        raise ValueError(f"Preset de latencia desconocido: {key}")
    # Los knobs no enviados conservan lo que ya tenía el preset: sobrescribir
    # desde la UI manda solo lo que los sliders pueden editar.
    base = profiles[key] if isinstance(profiles[key], dict) else None
    profiles[key] = _clamp_profile(
        {**base, **profile} if isinstance(profile, dict) and base else profile,
        LATENCY_FACTORY_PRESETS["stable"],
    )
    out["latency_profiles"] = profiles
    out["latency_mode"] = key
    return validate_config(out)


def save_latency_preset_as(
    cfg: dict[str, Any],
    name: str,
    profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    slug = _normalize_latency_preset_id(name)
    if not slug:
        raise ValueError("Nombre de preset vacío o inválido")
    if slug in LATENCY_FACTORY_PRESET_IDS:
        raise ValueError(f"El nombre «{slug}» está reservado")
    out = deepcopy(cfg)
    profiles = out.get("latency_profiles")
    if not isinstance(profiles, dict):
        profiles = deepcopy(LATENCY_FACTORY_PRESETS)
    else:
        profiles = deepcopy(profiles)
    if slug in profiles:
        raise ValueError(f"Ya existe un preset «{slug}»")
    profiles[slug] = _clamp_profile(
        profile if isinstance(profile, dict) else effective_latency_profile(out),
        LATENCY_FACTORY_PRESETS["stable"],
    )
    out["latency_profiles"] = profiles
    out["latency_mode"] = slug
    return validate_config(out)


def delete_latency_user_preset(
    cfg: dict[str, Any], preset_id: str | None = None
) -> dict[str, Any]:
    out = deepcopy(cfg)
    key = _normalize_latency_preset_id(preset_id or out.get("latency_mode"))
    if not key:
        raise ValueError("No hay preset de latencia para borrar")
    if key in LATENCY_FACTORY_PRESET_IDS:
        raise ValueError(f"El preset de fábrica «{key}» no se puede borrar")
    profiles = out.get("latency_profiles")
    if not isinstance(profiles, dict):
        profiles = deepcopy(LATENCY_FACTORY_PRESETS)
    else:
        profiles = deepcopy(profiles)
    if key not in profiles:
        raise ValueError(f"Preset de latencia desconocido: {key}")
    del profiles[key]
    out["latency_profiles"] = profiles
    if _normalize_latency_preset_id(out.get("latency_mode")) == key:
        out["latency_mode"] = "stable"
    return validate_config(out)


def _clamp_translation_decode(
    raw: dict[str, Any] | None,
    fallback: dict[str, float | int],
) -> dict[str, float | int]:
    src = fallback if not isinstance(raw, dict) else {**fallback, **raw}
    return {
        "beam_size": int(_clamp(int(src["beam_size"]), 1, 8)),
        "length_penalty": float(_clamp(float(src["length_penalty"]), 0.6, 1.5)),
        "no_repeat_ngram_size": int(_clamp(int(src["no_repeat_ngram_size"]), 0, 5)),
    }


def _migrate_translation_profiles(
    data: dict[str, Any],
) -> dict[str, dict[str, float | int]]:
    balanced = TRANSLATION_FACTORY_PRESETS["balanced"]
    profiles: dict[str, dict[str, float | int]] = {
        name: deepcopy(preset) for name, preset in TRANSLATION_FACTORY_PRESETS.items()
    }
    profiles["custom"] = deepcopy(balanced)

    profiles_in = data.get("translation_profiles")
    if not isinstance(profiles_in, dict):
        return profiles

    custom_raw = profiles_in.get("custom")
    profiles["custom"] = _clamp_translation_decode(
        custom_raw if isinstance(custom_raw, dict) else None,
        balanced,
    )

    for name, raw in profiles_in.items():
        key = str(name).strip().lower()
        if not key or key in TRANSLATION_RESERVED_PRESET_IDS:
            continue
        if not isinstance(raw, dict):
            continue
        profiles[key] = _clamp_translation_decode(raw, balanced)
    return profiles


def _normalize_translation_decode_preset(
    raw: Any, profiles: dict[str, dict[str, float | int]]
) -> str:
    preset = str(raw or "balanced").strip().lower() or "balanced"
    if preset in profiles:
        return preset
    return "balanced"


def effective_translation_decode(cfg: dict[str, Any]) -> dict[str, float | int]:
    profiles = cfg.get("translation_profiles")
    if not isinstance(profiles, dict):
        profiles = DEFAULTS["translation_profiles"]
    preset = _normalize_translation_decode_preset(
        cfg.get("translation_decode_preset"), profiles
    )
    if preset in TRANSLATION_FACTORY_PRESETS:
        return deepcopy(TRANSLATION_FACTORY_PRESETS[preset])
    raw = profiles.get(preset)
    fallback = TRANSLATION_FACTORY_PRESETS["balanced"]
    return _clamp_translation_decode(raw if isinstance(raw, dict) else None, fallback)


def slugify_translation_preset_name(name: str) -> str:
    raw = str(name or "").strip().lower()
    out: list[str] = []
    prev_dash = False
    for ch in raw:
        if ch.isalnum():
            out.append(ch)
            prev_dash = False
        elif ch in (" ", "-", "_"):
            if out and not prev_dash:
                out.append("-")
                prev_dash = True
    slug = "".join(out).strip("-_")
    return slug


def restore_translation_preset(
    cfg: dict[str, Any], preset_id: str | None = None
) -> dict[str, Any]:
    """Devuelve el preset a sus valores de referencia (fábrica o guardados).

    Como los valores guardados ya están en config, restaurar es solo dejar de
    usar el borrador de la UI; este helper existe para que config sea la única
    fuente de verdad y para los presets de fábrica.
    """
    out = deepcopy(cfg)
    key = str(preset_id or out.get("translation_decode_preset") or "balanced")
    key = key.strip().lower() or "balanced"
    out["translation_decode_preset"] = key
    return validate_config(out)


def add_translation_user_preset(
    cfg: dict[str, Any],
    name: str,
    decode: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Añade o actualiza un preset de usuario y lo deja activo. Raises ValueError si el nombre no vale."""
    slug = slugify_translation_preset_name(name)
    if not slug:
        raise ValueError("Nombre de preset vacío o inválido")
    if slug in TRANSLATION_RESERVED_PRESET_IDS:
        raise ValueError(f"El nombre «{slug}» está reservado")

    out = deepcopy(cfg)
    profiles = _migrate_translation_profiles(out)
    values = _clamp_translation_decode(
        decode if isinstance(decode, dict) else effective_translation_decode(out),
        TRANSLATION_FACTORY_PRESETS["balanced"],
    )
    profiles[slug] = values
    out["translation_profiles"] = profiles
    out["translation_decode_preset"] = slug
    return validate_config(out)


def delete_translation_user_preset(
    cfg: dict[str, Any], preset_id: str
) -> dict[str, Any]:
    """Borra un preset de usuario. Si era el activo, pasa a custom con snapshot actual."""
    key = str(preset_id or "").strip().lower()
    if not key or key in TRANSLATION_RESERVED_PRESET_IDS:
        raise ValueError("No se puede borrar un preset de fábrica o Custom")

    out = deepcopy(cfg)
    profiles = _migrate_translation_profiles(out)
    if key not in profiles:
        raise ValueError(f"Preset desconocido: {key}")

    snapshot = effective_translation_decode(
        {**out, "translation_profiles": profiles, "translation_decode_preset": key}
    )
    del profiles[key]
    profiles["custom"] = snapshot
    out["translation_profiles"] = profiles
    if str(out.get("translation_decode_preset", "")).strip().lower() == key:
        out["translation_decode_preset"] = "custom"
    return validate_config(out)


def is_factory_translation_preset(preset_id: str | None) -> bool:
    return str(preset_id or "").strip().lower() in TRANSLATION_FACTORY_PRESET_IDS


def is_deletable_translation_preset(preset_id: str | None) -> bool:
    """Un preset propio se puede borrar; fábrica y `custom` (del sistema) no."""
    key = str(preset_id or "").strip().lower()
    return bool(key) and key not in TRANSLATION_RESERVED_PRESET_IDS


def translation_user_preset_ids(cfg: dict[str, Any]) -> list[str]:
    """Presets propios, incluido `custom` (el selector lo muestra con su label)."""
    profiles = cfg.get("translation_profiles")
    if not isinstance(profiles, dict):
        return []
    return sorted(
        key
        for key in profiles
        if str(key).strip().lower() not in TRANSLATION_FACTORY_PRESET_IDS
    )


def save_translation_preset(
    cfg: dict[str, Any],
    preset_id: str,
    decode: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Sobrescribe un preset propio (incluido `custom`). Fábrica → ValueError."""
    key = str(preset_id or "").strip().lower()
    if not key:
        raise ValueError("No hay preset de decoding activo")
    if key in TRANSLATION_FACTORY_PRESET_IDS:
        raise ValueError(
            f"El preset de fábrica «{key}» no se puede sobrescribir; "
            "guárdalo con otro nombre."
        )
    out = deepcopy(cfg)
    profiles = _migrate_translation_profiles(out)
    if key not in profiles:
        raise ValueError(f"Preset desconocido: {key}")
    profiles[key] = _clamp_translation_decode(
        decode if isinstance(decode, dict) else effective_translation_decode(out),
        TRANSLATION_FACTORY_PRESETS["balanced"],
    )
    out["translation_profiles"] = profiles
    out["translation_decode_preset"] = key
    return validate_config(out)


def _normalize_installed_languages(raw: Any, language: str) -> list[str]:
    if isinstance(raw, list):
        langs = [str(item).strip().lower() for item in raw if str(item).strip()]
    else:
        langs = list(DEFAULTS["installed_languages"])
    # Mantener orden de aparición; language activo siempre disponible en el menú.
    seen: set[str] = set()
    out: list[str] = []
    for code in langs:
        if code not in seen:
            seen.add(code)
            out.append(code)
    if language and language not in seen:
        out.insert(0, language)
    if not out:
        out = list(DEFAULTS["installed_languages"])
    return out


def _normalize_translation_sticky_mode(raw: Any) -> str:
    mode = str(raw or "").strip().lower() or "off"
    if mode not in TRANSLATION_STICKY_MODES:
        return "off"
    return mode


def _normalize_second_line_mode(raw: dict[str, Any]) -> str:
    if "second_line_mode" in raw:
        mode = str(raw.get("second_line_mode") or "").strip().lower()
    elif "show_asr_line" in raw:
        # Legacy bool: True → ASR en vivo; False → sin segunda línea.
        mode = "live_asr" if bool(raw.get("show_asr_line")) else "none"
    else:
        mode = str(DEFAULTS["second_line_mode"])
    if mode not in SECOND_LINE_MODES:
        return str(DEFAULTS["second_line_mode"])
    return mode


def _normalize_font_family(raw: Any) -> str:
    """Familia lista para QSS; `""` = fuente por defecto de Qt.

    No se comprueba que esté instalada: la config es portable entre máquinas y Qt
    ya hace fallback. Solo se sanea lo que rompería la hoja de estilo.
    """
    text = str(raw or "")
    cleaned = "".join(ch for ch in text if ch not in _FONT_FAMILY_FORBIDDEN)
    return " ".join(cleaned.split())[:_FONT_FAMILY_MAX_LEN]


def _normalize_font_weight(raw: Any) -> str:
    weight = str(raw or "").strip().lower()
    return weight if weight in FONT_WEIGHT_MODES else "semibold"


def _normalize_window_pos(raw: Any) -> list[int] | None:
    if raw is None:
        return None
    if (
        not isinstance(raw, (list, tuple))
        or len(raw) != 2
        or not all(isinstance(v, (int, float)) for v in raw)
    ):
        return None
    return [int(raw[0]), int(raw[1])]


def slugify_app_preset_name(name: str) -> str:
    return slugify_translation_preset_name(name)


def snapshot_app_config(
    cfg: dict[str, Any], *, keys: Iterable[str] | None = None
) -> dict[str, Any]:
    """Copia validada del estado de app sin meta de presets generales.

    Con `keys` el snapshot es **parcial**: solo esas claves. Al aplicarlo, el
    resto de la config se queda como está. Es lo que permite que un preset de
    idioma no arrastre geometría ni apariencia.
    """
    raw = {
        key: value
        for key, value in dict(cfg).items()
        if key not in APP_PRESET_META_KEYS
    }
    validated = validate_config(raw, _skip_app_presets=True)
    allowed = None if keys is None else {str(key) for key in keys}
    return {
        key: deepcopy(value)
        for key, value in validated.items()
        if key not in APP_PRESET_META_KEYS and (allowed is None or key in allowed)
    }


_FACTORY_APP_PRESETS: dict[str, dict[str, Any]] | None = None


def factory_app_presets() -> dict[str, dict[str, Any]]:
    """Snapshots de fábrica = config de fábrica + overrides del catálogo.

    Se calcula al primer uso (no al importar) porque depende de `DEFAULTS`,
    que a su vez se lee de `config.example.json`.
    """
    global _FACTORY_APP_PRESETS
    if _FACTORY_APP_PRESETS is None:
        base = {
            key: value
            for key, value in DEFAULTS.items()
            if key not in APP_PRESET_META_KEYS
        }
        # Sin overrides (`default`) el snapshot es completo: restaura todo.
        # Con overrides es parcial: solo idioma y modelos, para poder cambiar de
        # preset sin perder geometría, tipografía ni perfiles de latencia.
        _FACTORY_APP_PRESETS = {
            preset_id: snapshot_app_config(
                {**deepcopy(base), **deepcopy(overrides)},
                keys=overrides.keys() or None,
            )
            for preset_id, overrides in APP_FACTORY_PRESET_OVERRIDES.items()
        }
    return deepcopy(_FACTORY_APP_PRESETS)


def factory_app_preset_ids() -> list[str]:
    return sorted(APP_FACTORY_PRESET_IDS)


def _normalize_app_presets(raw: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    seen_lower: set[str] = set()
    factory_lower = {pid.lower() for pid in APP_FACTORY_PRESET_IDS}
    for key, blob in raw.items():
        name = str(key).strip()
        if not name or not isinstance(blob, dict):
            continue
        low = name.lower()
        # Un id de fábrica escrito con otra caja lo sustituye su snapshot canónico.
        if low in seen_lower or low in factory_lower:
            continue
        seen_lower.add(low)
        # `keys=blob` conserva la parcialidad de los presets que la tengan.
        out[name] = snapshot_app_config(blob, keys=blob.keys())
    return out


def _normalize_app_preset_id(
    raw: Any, presets: dict[str, dict[str, Any]]
) -> str | None:
    if raw is None:
        return None
    key = str(raw).strip()
    if not key:
        return None
    if key in presets:
        return key
    low = key.lower()
    for name in presets:
        if name.lower() == low:
            return name
    return None


def list_app_preset_ids(cfg: dict[str, Any]) -> list[str]:
    presets = cfg.get("app_presets")
    if not isinstance(presets, dict):
        return []
    return sorted(str(key) for key in presets.keys())


def apply_app_preset(cfg: dict[str, Any], preset_id: str | None) -> dict[str, Any]:
    """Fusiona un snapshot **sobre cfg** conservando app_presets.

    El snapshot gana en las claves que trae; las que no trae (presets parciales)
    mantienen el valor actual. None → solo limpia app_preset.
    """
    out = validate_config(deepcopy(cfg))
    presets = deepcopy(out.get("app_presets") or {})
    if not isinstance(presets, dict):
        presets = {}
    if preset_id is None or str(preset_id).strip() == "":
        out["app_preset"] = None
        out["app_presets"] = presets
        return validate_config(out)
    key = _normalize_app_preset_id(preset_id, presets)
    if key is None:
        raise ValueError(f"Preset desconocido: {str(preset_id).strip()}")
    merged = {
        **out,
        **deepcopy(presets[key]),
        "app_presets": presets,
        "app_preset": key,
    }
    return validate_config(merged)


def save_app_preset(
    cfg: dict[str, Any], snapshot_src: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Sobrescribe el preset **propio** activo. Los de fábrica son inmutables."""
    out = validate_config(deepcopy(cfg))
    preset_id = out.get("app_preset")
    presets = out.get("app_presets")
    if not isinstance(preset_id, str) or not preset_id:
        raise ValueError("No hay preset activo para sobrescribir")
    if not isinstance(presets, dict) or preset_id not in presets:
        raise ValueError(f"Preset activo desconocido: {preset_id}")
    if is_factory_preset(preset_id):
        raise ValueError(
            f"El preset de fábrica «{preset_id}» no se puede sobrescribir; "
            "guárdalo con otro nombre."
        )
    src = snapshot_src if snapshot_src is not None else out
    presets = deepcopy(presets)
    presets[preset_id] = snapshot_app_config(src)
    out["app_presets"] = presets
    # El estado vivo absorbe lo guardado: si no, el preset recién guardado nace
    # ya «modificado» respecto a una config que se quedó en los valores viejos.
    return validate_config({**out, **deepcopy(presets[preset_id])})


def save_app_preset_as(
    cfg: dict[str, Any],
    name: str,
    snapshot_src: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Crea un preset nuevo (falla si el slug ya existe) y lo deja activo."""
    slug = slugify_app_preset_name(name)
    if not slug:
        raise ValueError("Nombre de preset vacío o inválido")
    out = validate_config(deepcopy(cfg))
    presets = deepcopy(out.get("app_presets") or {})
    if not isinstance(presets, dict):
        presets = {}
    if slug in presets:
        raise ValueError(f"Ya existe un preset «{slug}»")
    src = snapshot_src if snapshot_src is not None else out
    presets[slug] = snapshot_app_config(src)
    out["app_presets"] = presets
    out["app_preset"] = slug
    # Igual que al sobrescribir: el estado vivo pasa a ser el del preset nuevo.
    return validate_config({**out, **deepcopy(presets[slug])})


def delete_app_preset(
    cfg: dict[str, Any], preset_id: str | None = None
) -> dict[str, Any]:
    """Borra un preset de usuario y deja app_preset en null.

    Los presets de fábrica no se pueden borrar: se resiembran en cada
    validación, así que borrarlos solo confundiría.
    """
    out = validate_config(deepcopy(cfg))
    presets = deepcopy(out.get("app_presets") or {})
    if not isinstance(presets, dict):
        presets = {}
    requested = preset_id if preset_id is not None else out.get("app_preset")
    if requested is None or str(requested).strip() == "":
        raise ValueError("No hay preset para borrar")
    key = _normalize_app_preset_id(requested, presets)
    if key is None:
        raise ValueError(f"Preset desconocido: {str(requested).strip()}")
    if is_factory_preset(key):
        raise ValueError(f"El preset de fábrica «{key}» no se puede borrar")
    del presets[key]
    out["app_presets"] = presets
    out["app_preset"] = None
    return validate_config(out)


def validate_config(
    data: dict[str, Any], *, _skip_app_presets: bool = False
) -> dict[str, Any]:
    raw = dict(data)
    cfg = deepcopy(DEFAULTS)
    cfg.update(raw)

    cfg["font_size"] = int(_clamp(int(cfg["font_size"]), 10, 100))
    cfg["font_family"] = _normalize_font_family(cfg.get("font_family"))
    cfg["font_weight"] = _normalize_font_weight(cfg.get("font_weight"))
    cfg["bg_alpha"] = float(_clamp(float(cfg["bg_alpha"]), 0.05, 1.0))
    cfg["padding"] = int(_clamp(int(cfg["padding"]), 0, 100))
    align = str(cfg.get("text_align") or "").strip().lower()
    cfg["text_align"] = align if align in TEXT_ALIGN_MODES else "center"
    cfg["window_width"] = int(_clamp(int(cfg["window_width"]), 300, 2400))
    height = cfg.get("window_height")
    if height is None:
        cfg["window_height"] = None
    else:
        cfg["window_height"] = int(_clamp(int(height), 120, 1600))
    cfg["settings_window_width"] = int(
        _clamp(int(cfg.get("settings_window_width", 560)), 520, 2000)
    )
    settings_h = cfg.get("settings_window_height")
    if settings_h is None:
        cfg["settings_window_height"] = int(DEFAULTS["settings_window_height"])
    else:
        cfg["settings_window_height"] = int(_clamp(int(settings_h), 640, 1600))
    cfg["buffer_trimming_sec"] = float(
        _clamp(float(cfg["buffer_trimming_sec"]), 5.0, 60.0)
    )
    cfg["language"] = str(cfg["language"]).strip().lower() or "en"
    cfg["model"] = str(cfg["model"]).strip() or "medium"
    compute = str(cfg.get("compute_type") or "float16").strip().lower()
    cfg["compute_type"] = compute if compute in COMPUTE_TYPES else "float16"
    cfg["latency_mode"] = _normalize_latency_preset_id(
        cfg.get("latency_mode") or "stable"
    )
    cfg["use_vad"] = bool(cfg["use_vad"])
    cfg["always_on_top"] = bool(cfg.get("always_on_top", True))
    cfg["debug_hud"] = bool(cfg.get("debug_hud", False))
    cfg["audio_monitor"] = str(cfg.get("audio_monitor") or "")
    cfg["translation_enabled"] = bool(cfg.get("translation_enabled", False))
    cfg["translation_sticky_mode"] = _normalize_translation_sticky_mode(
        cfg.get("translation_sticky_mode")
    )
    cfg["second_line_mode"] = _normalize_second_line_mode(raw)
    cfg.pop("show_asr_line", None)
    cfg["captions_show_partials"] = bool(cfg.get("captions_show_partials", False))
    cfg["captions_allow_rewrite"] = bool(cfg.get("captions_allow_rewrite", True))
    cfg["translation_target"] = (
        str(cfg.get("translation_target") or "es").strip().lower() or "es"
    )
    cfg["translator_model"] = (
        str(cfg.get("translator_model") or DEFAULT_TRANSLATOR_MODEL).strip()
        or DEFAULT_TRANSLATOR_MODEL
    )
    cfg["installed_languages"] = _normalize_installed_languages(
        cfg.get("installed_languages"),
        cfg["language"],
    )

    cfg["latency_profiles"] = _migrate_latency_profiles(
        _latency_profiles_migrate_source(raw, cfg),
        cfg["latency_mode"],
    )
    latency_mode = _normalize_latency_preset_id(cfg.get("latency_mode"))
    if "latency_profiles" not in raw and _legacy_latency_knobs(raw):
        # Los knobs sueltos de fase 1 viven en un preset propio: son valores del
        # usuario y la fábrica ya no se puede editar en sitio.
        latency_mode = LATENCY_LEGACY_PRESET_ID
    cfg["latency_mode"] = (
        latency_mode if latency_mode in cfg["latency_profiles"] else "stable"
    )
    cfg["translation_profiles"] = _migrate_translation_profiles(raw)
    cfg["translation_decode_preset"] = _normalize_translation_decode_preset(
        cfg.get("translation_decode_preset"),
        cfg["translation_profiles"],
    )

    # Espejo derivado para lecturas legacy / depuración; la fuente de verdad es el profile.
    profile = effective_latency_profile(cfg)
    cfg["agreement_n"] = int(profile["agreement_n"])
    cfg["max_latency_sec"] = float(profile["max_latency_sec"])
    cfg["min_chunk_seconds"] = float(profile["min_chunk_seconds"])

    cfg["window_pos"] = _normalize_window_pos(cfg.get("window_pos"))
    cfg["settings_window_pos"] = _normalize_window_pos(cfg.get("settings_window_pos"))

    if _skip_app_presets:
        cfg["app_preset"] = None
        cfg["app_presets"] = {}
        return cfg

    presets_raw = (
        raw.get("app_presets") if "app_presets" in raw else cfg.get("app_presets")
    )
    presets = _normalize_app_presets(presets_raw)
    # Fábrica inmutable: se re-sincroniza siempre desde el catálogo, así que una
    # copia editada de un preset de fábrica en disco no sobrevive a validar.
    factory = factory_app_presets()
    for preset_id, snapshot in factory.items():
        presets[preset_id] = snapshot
    cfg["app_presets"] = presets
    preset_id_raw = (
        raw.get("app_preset") if "app_preset" in raw else cfg.get("app_preset")
    )
    cfg["app_preset"] = _normalize_app_preset_id(preset_id_raw, cfg["app_presets"])

    return cfg


def load_config(path: Path | None = None) -> dict[str, Any]:
    config_path = path or default_config_path()
    if not config_path.exists():
        return validate_config({})
    with config_path.open("r", encoding="utf-8") as fh:
        raw = json.load(fh)
    if not isinstance(raw, dict):
        return validate_config({})
    return validate_config(raw)


def save_config(cfg: dict[str, Any], path: Path | None = None) -> Path:
    config_path = path or default_config_path()
    validated = validate_config(cfg)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with config_path.open("w", encoding="utf-8") as fh:
        json.dump(validated, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return config_path
