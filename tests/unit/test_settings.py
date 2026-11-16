from pathlib import Path

import pytest

from event_timeline_tiled.settings import Settings


def test_from_env_reads_key_file(tmp_path: Path) -> None:
    key = tmp_path / "adapter.key"
    key.write_text("secret-value\n")
    s = Settings.from_env(
        {
            "ETT_TILED_URL": "http://tiled:8000/",
            "ETT_TILED_API_KEY_FILE": str(key),
            "ETT_CATALOG_ROOTS": " runs/mouse/ , runs/generic",
        }
    )
    assert s.tiled_url == "http://tiled:8000"
    assert s.tiled_api_key == "secret-value"
    assert s.catalog_roots == ("runs/mouse", "runs/generic")
    assert s.open_run_horizon_days == 21


def test_key_never_in_repr() -> None:
    s = Settings(tiled_url="http://tiled:8000", tiled_api_key="secret-value")
    assert "secret-value" not in repr(s)


@pytest.mark.parametrize(
    "env, message",
    [
        ({}, "ETT_TILED_URL"),
        ({"ETT_TILED_URL": "http://x"}, "no Tiled API key"),
        (
            {"ETT_TILED_URL": "http://x", "ETT_TILED_API_KEY": "a", "ETT_TILED_API_KEY_FILE": "b"},
            "not both",
        ),
        ({"ETT_TILED_URL": "ftp://x", "ETT_TILED_API_KEY": "a"}, "http:// or https://"),
        ({"ETT_TILED_URL": "http://u:p@x", "ETT_TILED_API_KEY": "a"}, "credentials"),
        ({"ETT_TILED_URL": "http://x/?a=1", "ETT_TILED_API_KEY": "a"}, "query or fragment"),
        ({"ETT_TILED_URL": "http://x#f", "ETT_TILED_API_KEY": "a"}, "query or fragment"),
        ({"ETT_TILED_URL": "http://x:port", "ETT_TILED_API_KEY": "a"}, "not a valid URL"),
        (
            {
                "ETT_TILED_URL": "http://x",
                "ETT_TILED_API_KEY": "a",
                "ETT_CATALOG_ROOTS": "../metadata",
            },
            "invalid segment",
        ),
        (
            {"ETT_TILED_URL": "http://x", "ETT_TILED_API_KEY": "a", "ETT_CATALOG_ROOTS": "runs//a"},
            "invalid segment",
        ),
        (
            {"ETT_TILED_URL": "http://x", "ETT_TILED_API_KEY": "a", "ETT_CATALOG_ROOTS": "r?x=1"},
            "invalid segment",
        ),
        (
            {"ETT_TILED_URL": "http://x", "ETT_TILED_API_KEY": "a", "ETT_REQUEST_TIMEOUT_S": "inf"},
            "finite",
        ),
        (
            {"ETT_TILED_URL": "http://x", "ETT_TILED_API_KEY": "a", "ETT_REQUEST_TIMEOUT_S": "nan"},
            "finite",
        ),
        (
            {
                "ETT_TILED_URL": "http://x",
                "ETT_TILED_API_KEY": "a",
                "ETT_OPEN_RUN_HORIZON_DAYS": "0",
            },
            "positive",
        ),
    ],
)
def test_invalid_settings_fail_clearly(env: dict[str, str], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        Settings.from_env(env)
