from __future__ import annotations

from src.asr.languages import AVAILABLE_LANGUAGES
from src.asr.translate import (
    NLLB_1_3B_CT2_MODEL_ID,
    NLLB_600M_CT2_MODEL_ID,
    TRANSLATOR_MODEL_1_3B,
    TRANSLATOR_MODEL_OPUS_MT,
    resolve_translator_model_id,
)
from src.config import (
    APP_PRESET_DEFAULT,
    apply_app_preset,
    factory_app_preset_ids,
    save_app_preset,
    save_app_preset_as,
    validate_config,
)
from src.presets import is_factory_preset, video_preset_id


def test_factory_catalog_covers_every_language() -> None:
    ids = factory_app_preset_ids()
    expected = {APP_PRESET_DEFAULT} | {
        video_preset_id(code) for code in AVAILABLE_LANGUAGES
    }
    assert set(ids) == expected
    assert video_preset_id("en") == "video-en-es"
    # El origen español no lleva sufijo de destino: no se traduce a sí mismo.
    assert video_preset_id("es") == "video-es"
    assert all(is_factory_preset(pid) for pid in ids)
    assert not is_factory_preset("mi-preset")


def test_empty_config_seeds_factory_presets() -> None:
    cfg = validate_config({})
    assert set(cfg["app_presets"]) == set(factory_app_preset_ids())


def test_upgrade_seeds_new_presets_and_keeps_user_ones() -> None:
    """Config de una versión previa: gana los video-* sin perder lo del usuario."""
    legacy = validate_config({})
    legacy = save_app_preset_as(legacy, "mi-setup")
    legacy["app_presets"] = {
        APP_PRESET_DEFAULT: legacy["app_presets"][APP_PRESET_DEFAULT],
        "mi-setup": legacy["app_presets"]["mi-setup"],
    }

    upgraded = validate_config(legacy)
    assert "mi-setup" in upgraded["app_presets"]
    for preset_id in factory_app_preset_ids():
        assert preset_id in upgraded["app_presets"]


def test_user_edits_to_factory_preset_do_not_survive() -> None:
    """La fábrica es inmutable: una copia editada en config se descarta al validar."""
    cfg = apply_app_preset(validate_config({}), "video-de-es")
    tampered = {
        **cfg,
        "app_presets": {
            **cfg["app_presets"],
            "video-de-es": {**cfg["app_presets"]["video-de-es"], "font_size": 44},
        },
    }
    reloaded = validate_config(tampered)
    assert reloaded["app_presets"]["video-de-es"] != tampered["app_presets"][
        "video-de-es"
    ]
    assert "font_size" not in reloaded["app_presets"]["video-de-es"]

    try:
        save_app_preset({**cfg, "font_size": 44})
        raise AssertionError("expected factory overwrite rejected")
    except ValueError as exc:
        assert "fábrica" in str(exc).lower()

    # El camino correcto: guardar como nuevo conserva los valores vivos.
    saved = save_app_preset_as(cfg, "mi-video-de", snapshot_src={**cfg, "font_size": 44})
    assert saved["app_presets"]["mi-video-de"]["font_size"] == 44
    assert saved["app_preset"] == "mi-video-de"


def test_video_preset_applies_recommended_models() -> None:
    cfg = apply_app_preset(validate_config({}), "video-fr-es")
    assert cfg["language"] == "fr"
    assert cfg["model"] == "large-v3-turbo"
    assert cfg["compute_type"] == "int8_float16"
    assert cfg["translation_enabled"] is True
    assert cfg["translation_target"] == "es"
    assert cfg["translator_model"] == TRANSLATOR_MODEL_OPUS_MT
    assert cfg["translation_decode_preset"] == "balanced"
    assert cfg["second_line_mode"] == "none"
    assert cfg["app_preset"] == "video-fr-es"


def test_spanish_preset_does_not_translate() -> None:
    cfg = apply_app_preset(validate_config({}), "video-es")
    assert cfg["language"] == "es"
    assert cfg["translation_enabled"] is False
    assert cfg["model"] == "large-v3-turbo"


def test_video_presets_are_partial_and_default_is_full() -> None:
    cfg = validate_config({})
    video = cfg["app_presets"]["video-en-es"]
    assert "window_pos" not in video
    assert "font_size" not in video
    assert "latency_profiles" not in video
    assert "audio_monitor" not in video
    assert "installed_languages" not in video
    # `default` sí restaura todo: es la config de referencia del producto.
    assert "window_pos" in cfg["app_presets"][APP_PRESET_DEFAULT]
    assert "font_size" in cfg["app_presets"][APP_PRESET_DEFAULT]


def test_video_presets_preserve_user_latency_and_appearance() -> None:
    """Un preset de vídeo fija idioma y modelos; no pisa lo que es del usuario."""
    tuned = validate_config(
        {
            "font_size": 40,
            "text_align": "center",
            "window_pos": [120, 900],
            "window_width": 1400,
            "audio_monitor": "algun.monitor",
            "latency_mode": "mi-perfil",
            "latency_profiles": {
                "mi-perfil": {
                    "agreement_n": 3,
                    "max_latency_sec": 1.2,
                    "min_chunk_seconds": 1.0,
                    "beam_size": 4,
                },
            },
        }
    )
    cfg = apply_app_preset(tuned, "video-it-es")
    assert cfg["language"] == "it"
    assert cfg["model"] == "large-v3-turbo"
    assert cfg["font_size"] == 40
    assert cfg["text_align"] == "center"
    assert cfg["window_pos"] == [120, 900]
    assert cfg["window_width"] == 1400
    assert cfg["audio_monitor"] == "algun.monitor"
    assert cfg["latency_mode"] == "mi-perfil"
    assert cfg["latency_profiles"]["mi-perfil"]["agreement_n"] == 3


def test_video_preset_keeps_installed_languages_and_adds_its_own() -> None:
    cfg = apply_app_preset(
        validate_config({"installed_languages": ["en", "es"]}), "video-de-es"
    )
    assert cfg["language"] == "de"
    # No pisa la lista del usuario, pero su idioma queda disponible en el menú.
    assert set(cfg["installed_languages"]) == {"de", "en", "es"}


def test_switching_between_video_presets_keeps_geometry() -> None:
    cfg = apply_app_preset(
        validate_config({"window_pos": [10, 20], "font_size": 34}), "video-en-es"
    )
    switched = apply_app_preset(cfg, "video-pt-es")
    assert switched["language"] == "pt"
    assert switched["window_pos"] == [10, 20]
    assert switched["font_size"] == 34


def test_default_preset_restores_full_config() -> None:
    from src.config import DEFAULTS

    tuned = validate_config({"font_size": 40, "window_pos": [120, 900]})
    restored = apply_app_preset(tuned, APP_PRESET_DEFAULT)
    assert restored["font_size"] == DEFAULTS["font_size"]
    assert restored["window_pos"] == DEFAULTS["window_pos"]


def test_translator_aliases_resolve_to_repos() -> None:
    assert resolve_translator_model_id(TRANSLATOR_MODEL_1_3B) == NLLB_1_3B_CT2_MODEL_ID
    assert (
        resolve_translator_model_id("nllb-200-distilled-ct2") == NLLB_600M_CT2_MODEL_ID
    )
    assert resolve_translator_model_id(NLLB_1_3B_CT2_MODEL_ID) == NLLB_1_3B_CT2_MODEL_ID
    assert resolve_translator_model_id("NLLB-200-Distilled-1.3B-CT2") == (
        NLLB_1_3B_CT2_MODEL_ID
    )
    # Un repo propio se respeta tal cual.
    assert resolve_translator_model_id("yo/mi-conversion-ct2") == "yo/mi-conversion-ct2"
    # Sin valor manda el default, que ya no es NLLB.
    assert resolve_translator_model_id("") == TRANSLATOR_MODEL_OPUS_MT
    assert resolve_translator_model_id(None) == TRANSLATOR_MODEL_OPUS_MT


def test_prefetch_derives_models_from_factory_presets() -> None:
    """La precarga sale del catálogo: no hay una lista paralela que se desincronice."""
    import importlib.util
    from pathlib import Path

    from src.asr.opusmt import DE_ES, EN_ES, ITC_ES, SLA_ES, ZLE_ES

    script = Path(__file__).resolve().parent.parent / "scripts" / "prefetch-models.py"
    spec = importlib.util.spec_from_file_location("prefetch_models", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    whisper, nllb, opus = module.required_models()
    assert whisper == ["large-v3-turbo"]
    # Ningún preset de fábrica usa NLLB: no hay que bajar 1,4 GB para nada.
    assert nllb == []
    # Ocho idiomas, cinco modelos: fr/it/pt comparten `tc-big-itc-itc` y cs/pl
    # comparten `tc-big-sla-es`.
    assert set(opus) == {EN_ES, DE_ES, ITC_ES, ZLE_ES, SLA_ES}
