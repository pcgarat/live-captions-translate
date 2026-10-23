from __future__ import annotations

from src.config import (
    APP_PRESET_META_KEYS,
    apply_app_preset,
    delete_app_preset,
    list_app_preset_ids,
    save_app_preset,
    save_app_preset_as,
    snapshot_app_config,
    validate_config,
)


def test_app_preset_defaults() -> None:
    from src.config import APP_PRESET_DEFAULT, DEFAULTS, factory_app_preset_ids

    cfg = validate_config({})
    assert cfg["app_preset"] == DEFAULTS["app_preset"]
    assert APP_PRESET_DEFAULT in cfg["app_presets"]
    assert set(cfg["app_presets"]) == set(factory_app_preset_ids())
    assert cfg["app_presets"][APP_PRESET_DEFAULT]["translation_enabled"] is True
    assert abs(
        float(cfg["latency_profiles"]["stable"]["max_latency_sec"]) - 0.4
    ) < 1e-9
    assert cfg["language"] == DEFAULTS["language"]
    assert cfg["model"] == DEFAULTS["model"]


def test_snapshot_excludes_meta_and_includes_geometry() -> None:
    cfg = validate_config(
        {
            "language": "es",
            "font_size": 40,
            "window_pos": [10, 20],
            "window_width": 800,
            "window_height": 200,
            "settings_window_pos": [30, 40],
            "settings_window_width": 700,
            "settings_window_height": 800,
            "app_preset": "should-not-matter",
            "app_presets": {"x": {"language": "en"}},
        }
    )
    # app_preset inválido (x no validará igual) — fuerza mapa real
    cfg = save_app_preset_as(cfg, "directo-es")
    snap = snapshot_app_config(cfg)
    assert "app_preset" not in snap
    assert "app_presets" not in snap
    assert snap["language"] == "es"
    assert snap["font_size"] == 40
    assert snap["window_pos"] == [10, 20]
    assert snap["window_width"] == 800
    assert snap["window_height"] == 200
    assert snap["settings_window_pos"] == [30, 40]
    assert snap["settings_window_width"] == 700
    assert snap["settings_window_height"] == 800
    for key in APP_PRESET_META_KEYS:
        assert key not in snap


def test_save_as_apply_overwrite_delete() -> None:
    base = validate_config({"language": "en", "font_size": 28, "window_pos": [1, 2]})
    created = save_app_preset_as(base, "Directo ES")
    assert created["app_preset"] == "directo-es"
    assert "directo-es" in created["app_presets"]
    assert created["app_presets"]["directo-es"]["language"] == "en"
    assert "directo-es" in list_app_preset_ids(created)
    assert len(list_app_preset_ids(created)) >= 1

    tweaked = validate_config({**created, "language": "fr", "font_size": 50})
    applied = apply_app_preset(tweaked, "directo-es")
    assert applied["language"] == "en"
    assert applied["font_size"] == 28
    assert applied["app_preset"] == "directo-es"
    assert "directo-es" in applied["app_presets"]
    assert applied["app_presets"]["directo-es"]["language"] == "en"

    live = validate_config(
        {**applied, "language": "de", "window_pos": [9, 9], "font_size": 33}
    )
    overwritten = save_app_preset(live)
    assert overwritten["app_presets"]["directo-es"]["language"] == "de"
    assert overwritten["app_presets"]["directo-es"]["window_pos"] == [9, 9]
    assert overwritten["app_presets"]["directo-es"]["font_size"] == 33

    cleared = apply_app_preset(overwritten, None)
    assert cleared["app_preset"] is None
    assert cleared["language"] == "de"
    assert "directo-es" in cleared["app_presets"]

    deleted = delete_app_preset(cleared, "directo-es")
    assert deleted["app_preset"] is None
    assert "directo-es" not in deleted["app_presets"]
    assert deleted["language"] == "de"


def test_delete_factory_presets_rejected() -> None:
    from src.config import factory_app_preset_ids

    cfg = validate_config({})
    for preset_id in factory_app_preset_ids():
        try:
            delete_app_preset(cfg, preset_id)
            raise AssertionError(f"expected factory delete rejected: {preset_id}")
        except ValueError as exc:
            assert "fábrica" in str(exc).lower()
        assert preset_id in validate_config(cfg)["app_presets"]


def test_save_as_rejects_collision() -> None:
    cfg = save_app_preset_as(validate_config({}), "estudio")
    try:
        save_app_preset_as(cfg, "Estudio")
        raise AssertionError("expected collision")
    except ValueError as exc:
        assert "existe" in str(exc).lower()


def test_save_without_active_fails() -> None:
    try:
        save_app_preset(validate_config({"app_preset": None, "app_presets": {}}))
        raise AssertionError("expected no active preset")
    except ValueError as exc:
        assert "activo" in str(exc).lower()


def test_nested_presets_in_blob_are_stripped() -> None:
    cfg = save_app_preset_as(
        validate_config({"language": "en"}),
        "a",
        snapshot_src={
            "language": "es",
            "app_preset": "evil",
            "app_presets": {"nested": {"language": "xx"}},
        },
    )
    blob = cfg["app_presets"]["a"]
    assert "app_presets" not in blob
    assert "app_preset" not in blob
    assert blob["language"] == "es"


def test_invalid_app_preset_id_cleared() -> None:
    cfg = validate_config({"app_preset": "missing", "app_presets": {}})
    assert cfg["app_preset"] is None


def test_apply_unknown_raises() -> None:
    try:
        apply_app_preset(validate_config({}), "nope")
        raise AssertionError("expected unknown preset")
    except ValueError as exc:
        assert "desconocido" in str(exc).lower()


def test_save_as_leaves_live_config_at_saved_values() -> None:
    """Tras guardar, el preset no debe nacer «modificado» respecto a la config."""
    from src.preset_state import DOMAIN_APP, changed_option_keys, preset_values

    base = validate_config({"font_size": 30})
    saved = save_app_preset_as(
        base, "estudio", snapshot_src={**base, "font_size": 44}
    )
    assert saved["font_size"] == 44
    assert changed_option_keys(
        DOMAIN_APP, preset_values(DOMAIN_APP, saved, "estudio"), saved
    ) == []


def test_overwrite_leaves_live_config_at_saved_values() -> None:
    from src.preset_state import DOMAIN_APP, changed_option_keys, preset_values

    cfg = save_app_preset_as(validate_config({"font_size": 30}), "estudio")
    overwritten = save_app_preset(cfg, snapshot_src={**cfg, "font_size": 52})
    assert overwritten["font_size"] == 52
    assert overwritten["app_presets"]["estudio"]["font_size"] == 52
    assert changed_option_keys(
        DOMAIN_APP, preset_values(DOMAIN_APP, overwritten, "estudio"), overwritten
    ) == []


def test_save_on_factory_preset_is_rejected() -> None:
    cfg = apply_app_preset(validate_config({}), "video-en-es")
    try:
        save_app_preset(cfg, snapshot_src={**cfg, "font_size": 44})
        raise AssertionError("expected factory overwrite rejected")
    except ValueError as exc:
        assert "fábrica" in str(exc).lower()
