from __future__ import annotations

import json
from pathlib import Path

from src.config import (
    DEFAULTS,
    LATENCY_FACTORY_PRESETS,
    LATENCY_LEGACY_PRESET_ID,
    TRANSLATION_FACTORY_PRESETS,
    add_translation_user_preset,
    delete_latency_user_preset,
    delete_translation_user_preset,
    effective_latency_profile,
    effective_translation_decode,
    factory_app_preset_ids,
    latency_user_preset_ids,
    load_config,
    reset_latency_profile,
    save_config,
    save_latency_preset,
    save_latency_preset_as,
    slugify_translation_preset_name,
    validate_config,
)


def test_load_missing_returns_defaults(tmp_path: Path) -> None:
    cfg = load_config(tmp_path / "config.json")
    assert cfg["language"] == DEFAULTS["language"]
    assert cfg["model"] == DEFAULTS["model"]
    assert cfg["latency_mode"] == "stable"
    assert (
        cfg["latency_profiles"]["stable"]
        == validate_config({})["latency_profiles"]["stable"]
    )
    assert (
        cfg["latency_profiles"]["low"] == validate_config({})["latency_profiles"]["low"]
    )
    assert cfg["translation_enabled"] is True
    assert cfg["translation_target"] == "es"
    assert cfg["translation_sticky_mode"] == DEFAULTS["translation_sticky_mode"]
    assert cfg["second_line_mode"] == "none"
    assert cfg["captions_show_partials"] is False
    assert cfg["captions_allow_rewrite"] is True
    assert cfg["installed_languages"] == ["en", "es", "fr", "de", "it", "pt"]
    assert cfg["translator_model"] == DEFAULTS["translator_model"]
    assert cfg["translation_decode_preset"] == DEFAULTS["translation_decode_preset"]
    assert (
        cfg["translation_profiles"]["balanced"]
        == TRANSLATION_FACTORY_PRESETS["balanced"]
    )
    assert (
        cfg["translation_profiles"]["custom"] == TRANSLATION_FACTORY_PRESETS["balanced"]
    )
    assert cfg["app_preset"] == DEFAULTS["app_preset"]
    # Los snapshots no viven en DEFAULTS: se siembran desde el catálogo.
    assert set(cfg["app_presets"]) == set(factory_app_preset_ids())
    assert cfg["text_align"] == "left"
    assert cfg["font_size"] == 26


def test_validate_clamps_ranges() -> None:
    cfg = validate_config(
        {
            "font_size": 999,
            "bg_alpha": 2.5,
            "padding": -3,
            "text_align": "nope",
            "compute_type": "nope",
            "latency_mode": "nope",
            "latency_profiles": {
                "mio": {
                    "agreement_n": 99,
                    "max_latency_sec": 0.1,
                    "min_chunk_seconds": 0.01,
                    "beam_size": 99,
                }
            },
        }
    )
    assert cfg["font_size"] == 100
    assert cfg["bg_alpha"] == 1.0
    assert cfg["padding"] == 0
    assert cfg["text_align"] == "center"
    assert cfg["compute_type"] == "float16"
    assert cfg["latency_mode"] == "stable"
    assert cfg["latency_profiles"]["mio"]["agreement_n"] == 5
    assert cfg["latency_profiles"]["mio"]["max_latency_sec"] == 0.2
    assert cfg["latency_profiles"]["mio"]["min_chunk_seconds"] == 0.2
    assert cfg["latency_profiles"]["mio"]["beam_size"] == 8


def test_window_height_clamps() -> None:
    assert validate_config({})["window_height"] == DEFAULTS["window_height"]
    assert validate_config({"window_height": 500})["window_height"] == 500
    assert validate_config({"window_height": 50})["window_height"] == 120
    assert validate_config({"window_height": 9999})["window_height"] == 1600


def test_settings_window_geometry_defaults_and_clamps() -> None:
    cfg = validate_config({})
    assert cfg["settings_window_pos"] == DEFAULTS["settings_window_pos"]
    assert cfg["settings_window_width"] == DEFAULTS["settings_window_width"]
    assert cfg["settings_window_height"] == DEFAULTS["settings_window_height"]
    clamped = validate_config(
        {
            "settings_window_width": 100,
            "settings_window_height": 9999,
            "settings_window_pos": [10, 20],
        }
    )
    assert clamped["settings_window_width"] == 520
    assert clamped["settings_window_height"] == 1600
    assert clamped["settings_window_pos"] == [10, 20]
    assert validate_config({"settings_window_pos": "bad"})["settings_window_pos"] is None


def test_text_align_normalize() -> None:
    assert validate_config({})["text_align"] == "left"
    assert validate_config({"text_align": "left"})["text_align"] == "left"
    assert validate_config({"text_align": "LEFT"})["text_align"] == "left"
    assert validate_config({"text_align": "right"})["text_align"] == "center"


def test_font_family_normalize() -> None:
    assert validate_config({})["font_family"] == ""
    assert validate_config({"font_family": "  Noto  Sans "})["font_family"] == "Noto Sans"
    assert validate_config({"font_family": None})["font_family"] == ""
    # El valor se interpola en QSS: nada que pueda cerrar la declaración o el bloque.
    assert (
        validate_config({"font_family": 'Fake"; color: red; }'})["font_family"]
        == "Fake color: red"
    )
    assert len(validate_config({"font_family": "X" * 200})["font_family"]) == 64
    # Una familia que no esté instalada se conserva: la config es portable.
    assert (
        validate_config({"font_family": "Helvetica Neue LT Pro"})["font_family"]
        == "Helvetica Neue LT Pro"
    )


def test_font_weight_normalize() -> None:
    assert validate_config({})["font_weight"] == "semibold"
    assert validate_config({"font_weight": "BOLD"})["font_weight"] == "bold"
    assert validate_config({"font_weight": "normal"})["font_weight"] == "normal"
    assert validate_config({"font_weight": "nope"})["font_weight"] == "semibold"
    assert validate_config({"font_weight": 700})["font_weight"] == "semibold"


def test_migrate_legacy_top_level_into_own_preset() -> None:
    """Los knobs de fase 1 no pueden caer en un preset de fábrica (es inmutable)."""
    cfg = validate_config(
        {
            "latency_mode": "low",
            "agreement_n": 3,
            "min_chunk_seconds": 1.2,
        }
    )
    legacy = cfg["latency_profiles"][LATENCY_LEGACY_PRESET_ID]
    assert legacy["agreement_n"] == 3
    assert abs(legacy["min_chunk_seconds"] - 1.2) < 1e-9
    # El resto de knobs hereda los del modo que estaba activo.
    assert legacy["beam_size"] == LATENCY_FACTORY_PRESETS["low"]["beam_size"]
    assert cfg["latency_mode"] == LATENCY_LEGACY_PRESET_ID
    assert cfg["latency_profiles"]["stable"] == LATENCY_FACTORY_PRESETS["stable"]
    assert effective_latency_profile(cfg)["agreement_n"] == 3


def test_save_and_load_roundtrip_preserves_profiles(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    save_config(
        {
            "language": "en",
            "font_size": 40,
            "bg_alpha": 0.33,
            "latency_mode": "mi-perfil",
            "latency_profiles": {
                "stable": dict(LATENCY_FACTORY_PRESETS["stable"]),
                "mi-perfil": {
                    "agreement_n": 1,
                    "max_latency_sec": 0.8,
                    "min_chunk_seconds": 0.4,
                    "beam_size": 3,
                },
            },
        },
        path,
    )
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["latency_mode"] == "mi-perfil"
    assert abs(raw["latency_profiles"]["mi-perfil"]["max_latency_sec"] - 0.8) < 1e-9
    loaded = load_config(path)
    assert loaded["font_size"] == 40
    assert abs(loaded["bg_alpha"] - 0.33) < 1e-9
    assert loaded["latency_mode"] == "mi-perfil"
    assert (
        abs(loaded["latency_profiles"]["mi-perfil"]["min_chunk_seconds"] - 0.4) < 1e-9
    )
    assert loaded["latency_profiles"]["mi-perfil"]["beam_size"] == 3


def test_save_and_load_roundtrip_preserves_translation_flags(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    save_config(
        {
            "language": "en",
            "translation_enabled": True,
            "translation_target": "es",
            "translation_sticky_mode": "partials",
            "second_line_mode": "none",
            "installed_languages": ["en", "es", "fr"],
            "translator_model": "nllb-200-distilled-ct2",
        },
        path,
    )
    loaded = load_config(path)
    assert loaded["translation_enabled"] is True
    assert loaded["translation_target"] == "es"
    assert loaded["translation_sticky_mode"] == "partials"
    assert loaded["second_line_mode"] == "none"
    assert loaded["installed_languages"] == ["en", "es", "fr"]
    assert loaded["translator_model"] == "nllb-200-distilled-ct2"


def test_migrate_legacy_show_asr_line_to_second_line_mode() -> None:
    assert validate_config({"show_asr_line": True})["second_line_mode"] == "live_asr"
    assert validate_config({"show_asr_line": False})["second_line_mode"] == "none"
    migrated = validate_config({"show_asr_line": False, "second_line_mode": "original"})
    assert migrated["second_line_mode"] == "original"
    assert "show_asr_line" not in migrated
    assert (
        validate_config({"second_line_mode": "nope"})["second_line_mode"] == "none"
    )


def test_translation_sticky_mode_defaults_and_clamp() -> None:
    assert (
        validate_config({})["translation_sticky_mode"]
        == DEFAULTS["translation_sticky_mode"]
    )
    assert validate_config({"translation_sticky_mode": "committed"})[
        "translation_sticky_mode"
    ] == "committed"
    assert validate_config({"translation_sticky_mode": "PARTIALS"})[
        "translation_sticky_mode"
    ] == "partials"
    assert validate_config({"translation_sticky_mode": "nope"})[
        "translation_sticky_mode"
    ] == "off"


def test_captions_display_policy_defaults_and_bool() -> None:
    assert validate_config({})["captions_show_partials"] is False
    assert validate_config({})["captions_allow_rewrite"] is True
    assert validate_config({"captions_show_partials": True})[
        "captions_show_partials"
    ] is True
    assert validate_config({"captions_allow_rewrite": 0})[
        "captions_allow_rewrite"
    ] is False


def test_installed_languages_includes_active_language() -> None:
    cfg = validate_config({"language": "fr", "installed_languages": ["en", "es"]})
    assert cfg["language"] == "fr"
    assert cfg["installed_languages"][0] == "fr"
    assert "en" in cfg["installed_languages"]
    assert "es" in cfg["installed_languages"]


def test_effective_latency_profile_uses_preset_beam_size() -> None:
    cfg = validate_config(
        {
            "latency_mode": "mi-perfil",
            "latency_profiles": {
                "mi-perfil": {
                    "agreement_n": 3,
                    "max_latency_sec": 1.1,
                    "min_chunk_seconds": 0.5,
                    "beam_size": 3,
                },
            },
        }
    )
    profile = effective_latency_profile(cfg)
    assert profile["beam_size"] == 3
    assert profile["agreement_n"] == 3
    assert cfg["latency_mode"] == "mi-perfil"


def test_latency_user_presets_crud() -> None:
    base = validate_config({})
    assert latency_user_preset_ids(base) == []

    created = save_latency_preset_as(base, "Mi Perfil", {"agreement_n": 3})
    assert created["latency_mode"] == "mi-perfil"
    assert latency_user_preset_ids(created) == ["mi-perfil"]
    assert created["latency_profiles"]["mi-perfil"]["agreement_n"] == 3

    overwritten = save_latency_preset(
        created, "mi-perfil", {"max_latency_sec": 2.0}
    )
    assert overwritten["latency_profiles"]["mi-perfil"]["max_latency_sec"] == 2.0
    # Lo no enviado hereda del perfil anterior, no se resetea.
    assert overwritten["latency_profiles"]["mi-perfil"]["agreement_n"] == 3

    deleted = delete_latency_user_preset(overwritten, "mi-perfil")
    assert latency_user_preset_ids(deleted) == []
    assert deleted["latency_mode"] == "stable"


def test_factory_latency_presets_are_immutable() -> None:
    cfg = validate_config(
        {
            "latency_profiles": {
                "stable": {
                    "agreement_n": 5,
                    "max_latency_sec": 3.0,
                    "min_chunk_seconds": 5.0,
                    "beam_size": 8,
                }
            }
        }
    )
    assert cfg["latency_profiles"]["stable"] == LATENCY_FACTORY_PRESETS["stable"]

    try:
        save_latency_preset(cfg, "stable", {"agreement_n": 4})
        raise AssertionError("expected factory overwrite rejected")
    except ValueError as exc:
        assert "fábrica" in str(exc).lower()
    try:
        delete_latency_user_preset(cfg, "stable")
        raise AssertionError("expected factory delete rejected")
    except ValueError as exc:
        assert "fábrica" in str(exc).lower()
    try:
        save_latency_preset_as(cfg, "Stable")
        raise AssertionError("expected reserved name rejected")
    except ValueError as exc:
        assert "reservado" in str(exc).lower()


def test_save_latency_as_rejects_collision() -> None:
    cfg = save_latency_preset_as(validate_config({}), "estudio")
    try:
        save_latency_preset_as(cfg, "Estudio")
        raise AssertionError("expected collision")
    except ValueError as exc:
        assert "existe" in str(exc).lower()


def test_reset_latency_profile_restores_factory() -> None:
    cfg = validate_config(
        {
            "latency_mode": "low",
            "latency_profiles": {
                "stable": {
                    "agreement_n": 5,
                    "max_latency_sec": 2.5,
                    "min_chunk_seconds": 1.0,
                },
            },
        }
    )
    reset = reset_latency_profile(cfg, "low")
    assert reset["latency_profiles"]["low"] == LATENCY_FACTORY_PRESETS["low"]
    # Los presets de fábrica se re-sincronizan siempre: `stable` vuelve al suyo.
    assert reset["latency_profiles"]["stable"] == LATENCY_FACTORY_PRESETS["stable"]


def test_translation_decode_defaults_and_factory_resync() -> None:
    cfg = validate_config(
        {
            "translation_decode_preset": "custom",
            "translation_profiles": {
                "balanced": {
                    "beam_size": 1,
                    "length_penalty": 0.6,
                    "no_repeat_ngram_size": 0,
                },
                "custom": {
                    "beam_size": 5,
                    "length_penalty": 1.2,
                    "no_repeat_ngram_size": 4,
                },
            }
        }
    )
    assert cfg["translation_decode_preset"] == "custom"
    assert (
        cfg["translation_profiles"]["balanced"]
        == TRANSLATION_FACTORY_PRESETS["balanced"]
    )
    assert cfg["translation_profiles"]["custom"]["beam_size"] == 5
    assert effective_translation_decode(cfg) == {
        "beam_size": 5,
        "length_penalty": 1.2,
        "no_repeat_ngram_size": 4,
    }


def test_translation_decode_clamps_and_unknown_preset() -> None:
    cfg = validate_config(
        {
            "translation_decode_preset": "nope",
            "translation_profiles": {
                "custom": {
                    "beam_size": 99,
                    "length_penalty": 9.0,
                    "no_repeat_ngram_size": -1,
                }
            },
        }
    )
    assert cfg["translation_decode_preset"] == "balanced"
    assert cfg["translation_profiles"]["custom"]["beam_size"] == 8
    assert abs(cfg["translation_profiles"]["custom"]["length_penalty"] - 1.5) < 1e-9
    assert cfg["translation_profiles"]["custom"]["no_repeat_ngram_size"] == 0


def test_translation_user_preset_add_delete_and_slug() -> None:
    assert slugify_translation_preset_name(" Mi Preset! ") == "mi-preset"
    base = validate_config(
        {
            "translation_decode_preset": "custom",
            "translation_profiles": {
                "custom": {
                    "beam_size": 5,
                    "length_penalty": 1.2,
                    "no_repeat_ngram_size": 2,
                },
            },
        }
    )
    with_user = add_translation_user_preset(base, "Mi Preset")
    assert with_user["translation_decode_preset"] == "mi-preset"
    assert with_user["translation_profiles"]["mi-preset"]["beam_size"] == 5

    try:
        add_translation_user_preset(with_user, "balanced")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass

    deleted = delete_translation_user_preset(with_user, "mi-preset")
    assert "mi-preset" not in deleted["translation_profiles"]
    assert deleted["translation_decode_preset"] == "custom"
    assert deleted["translation_profiles"]["custom"]["beam_size"] == 5

    try:
        delete_translation_user_preset(deleted, "quality")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_save_and_load_roundtrip_preserves_translation_decode(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    save_config(
        {
            "translation_decode_preset": "custom",
            "translation_profiles": {
                "custom": {
                    "beam_size": 5,
                    "length_penalty": 1.2,
                    "no_repeat_ngram_size": 2,
                },
                "live-talk": {
                    "beam_size": 3,
                    "length_penalty": 1.0,
                    "no_repeat_ngram_size": 3,
                },
            },
        },
        path,
    )
    loaded = load_config(path)
    assert loaded["translation_decode_preset"] == "custom"
    assert loaded["translation_profiles"]["custom"]["beam_size"] == 5
    assert loaded["translation_profiles"]["live-talk"]["beam_size"] == 3
    assert (
        loaded["translation_profiles"]["quality"]
        == TRANSLATION_FACTORY_PRESETS["quality"]
    )
