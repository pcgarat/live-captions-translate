from __future__ import annotations

from src.config import (
    apply_app_preset,
    save_app_preset_as,
    save_latency_preset_as,
    validate_config,
)
from src.preset_state import (
    DOMAIN_APP,
    DOMAIN_DECODE,
    DOMAIN_LATENCY,
    changed_option_keys,
    describe,
    domain_preset_ids,
    is_deletable,
    is_factory,
    is_modified,
    is_partial,
    preset_label,
    preset_values,
)


def test_domain_preset_ids_list_factory_and_user_presets() -> None:
    cfg = validate_config({})
    assert {"stable", "low"} <= set(domain_preset_ids(DOMAIN_LATENCY, cfg))
    assert {"fast", "balanced", "quality", "custom"} <= set(
        domain_preset_ids(DOMAIN_DECODE, cfg)
    )

    created = save_latency_preset_as(cfg, "mio")
    assert "mio" in domain_preset_ids(DOMAIN_LATENCY, created)


def test_factory_and_deletable_flags() -> None:
    cfg = validate_config({})
    assert is_factory(DOMAIN_LATENCY, "stable") is True
    assert is_factory(DOMAIN_DECODE, "balanced") is True
    assert is_factory(DOMAIN_APP, "default") is True

    save_latency_preset_as(cfg, "mio")
    assert is_factory(DOMAIN_LATENCY, "mio") is False
    assert is_deletable(DOMAIN_LATENCY, "mio") is True
    # `custom` es propio pero del sistema: se puede sobrescribir, no borrar.
    assert is_deletable(DOMAIN_DECODE, "custom") is False


def test_preset_label_reads_well() -> None:
    assert preset_label(DOMAIN_LATENCY, "stable") == "Estable — más preciso"
    assert preset_label(DOMAIN_DECODE, "quality") == "Calidad"
    assert preset_label(DOMAIN_APP, "video-en-es") == "Vídeo: Inglés"
    assert preset_label(DOMAIN_APP, None) == "(ninguno)"


def test_preset_values_and_partiality() -> None:
    cfg = validate_config({})
    assert preset_values(DOMAIN_LATENCY, cfg, "stable")["beam_size"] == 5
    assert preset_values(DOMAIN_DECODE, cfg, None) == {}
    # Los presets de vídeo son parciales; `default` fija todo.
    assert is_partial(DOMAIN_APP, cfg, "video-en-es") is True
    assert is_partial(DOMAIN_APP, cfg, "default") is False
    # La parcialidad solo aplica al dominio de app.
    assert is_partial(DOMAIN_LATENCY, cfg, "stable") is False


def test_describe_formats_only_fixed_options() -> None:
    cfg = validate_config({})
    decode = describe(DOMAIN_DECODE, cfg, "balanced")
    assert decode == [
        ("Beam size", "4"),
        ("Length penalty", "0.7"),
        ("No-repeat n-gram", "3"),
    ]
    latency = dict(describe(DOMAIN_LATENCY, cfg, "low"))
    assert latency["Confianza (lecturas)"] == "1"
    assert latency["Techo de latencia"] == "1.5 s"
    assert latency["Beam size de Whisper"] == "1"

    video = dict(describe(DOMAIN_APP, cfg, "video-en-es"))
    assert video["Idioma"] == "Inglés (en)"
    assert video["Modelo Whisper"] == "large-v3-turbo"
    # Un preset parcial no lista lo que no fija.
    assert "Tamaño de texto" not in video
    assert describe(DOMAIN_APP, cfg, None) == []


def test_changed_option_keys_ignores_unrelated_and_geometry() -> None:
    cfg = validate_config({})
    saved = preset_values(DOMAIN_LATENCY, cfg, "stable")
    live = {
        **cfg,
        **saved,
        # Geometría y espejos no son opciones del dominio de latencia.
        "window_pos": [1, 2],
        "font_size": 44,
    }
    assert changed_option_keys(DOMAIN_LATENCY, saved, live) == []

    live["max_latency_sec"] = 2.4
    assert changed_option_keys(DOMAIN_LATENCY, saved, live) == ["max_latency_sec"]
    assert is_modified(DOMAIN_LATENCY, cfg, "stable", live) is True
    assert is_modified(DOMAIN_LATENCY, cfg, "stable", {**cfg, **saved}) is False
    assert is_modified(DOMAIN_LATENCY, cfg, None, live) is False


def test_empty_audio_monitor_in_preset_is_not_dirty() -> None:
    """La app elige el primer monitor al arrancar; eso no desvía el preset."""
    cfg = apply_app_preset(validate_config({}), "default")
    live = {**cfg, **preset_values(DOMAIN_APP, cfg, "default")}
    live["audio_monitor"] = "alsa_output.x.monitor"
    assert (
        changed_option_keys(DOMAIN_APP, preset_values(DOMAIN_APP, cfg, "default"), live)
        == []
    )


def test_floats_compare_with_tolerance() -> None:
    saved = {"beam_size": 4, "length_penalty": 0.7, "no_repeat_ngram_size": 3}
    live = {"beam_size": 4, "length_penalty": 0.70000001, "no_repeat_ngram_size": 3}
    assert changed_option_keys(DOMAIN_DECODE, saved, live) == []
    live["length_penalty"] = 0.8
    assert changed_option_keys(DOMAIN_DECODE, saved, live) == ["length_penalty"]


def test_user_app_preset_detects_changes() -> None:
    cfg = save_app_preset_as(validate_config({"font_size": 30}), "estudio")
    saved = preset_values(DOMAIN_APP, cfg, "estudio")
    assert changed_option_keys(DOMAIN_APP, saved, {**cfg, "font_size": 30}) == []
    assert changed_option_keys(DOMAIN_APP, saved, {**cfg, "font_size": 31}) == [
        "font_size"
    ]
