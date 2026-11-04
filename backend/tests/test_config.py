import pytest

from app.core.config import KIB, CDCConfig, Settings
from app.core.errors import ConfigurationError


def test_cdc_defaults_match_spec():
    c = CDCConfig()
    assert (c.min_chunk_size, c.avg_chunk_size, c.max_chunk_size, c.window_size) == (
        4 * KIB, 16 * KIB, 128 * KIB, 64,
    )
    assert c.boundary_divisor == 12 * KIB


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(min_chunk_size=0),
        dict(window_size=-1),
        dict(min_chunk_size=16 * KIB),  # min == avg
        dict(avg_chunk_size=128 * KIB),  # avg == max
        dict(min_chunk_size=200 * KIB),  # min > max
        dict(window_size=8 * KIB),  # window > min
        dict(avg_chunk_size=True),
        dict(max_chunk_size=1.5),
    ],
)
def test_cdc_invalid_rejected(kwargs):
    with pytest.raises(ConfigurationError):
        CDCConfig(**kwargs)


def test_settings_from_env_defaults(tmp_path):
    s = Settings.from_env({})
    assert s.cdc == CDCConfig()
    assert s.db_path.name == "chunkvault.db"
    assert s.chunks_dir.name == "chunks" and s.staging_dir.name == "staging"
    assert "http://localhost:5173" in s.cors_origins


def test_settings_from_env_overrides(tmp_path):
    s = Settings.from_env(
        {
            "CHUNKVAULT_DATA_DIR": str(tmp_path),
            "CHUNKVAULT_CDC_AVG_CHUNK_SIZE": "32768",
            "CHUNKVAULT_CORS_ORIGINS": "http://a.test:1, http://b.test:2",
        }
    )
    assert s.data_dir == tmp_path
    assert s.db_path == tmp_path / "chunkvault.db"
    assert s.cdc.avg_chunk_size == 32768
    assert s.cors_origins == ("http://a.test:1", "http://b.test:2")


def test_settings_bad_env_rejected():
    with pytest.raises(ConfigurationError):
        Settings.from_env({"CHUNKVAULT_CDC_MIN_CHUNK_SIZE": "abc"})
    with pytest.raises(ConfigurationError):
        Settings.from_env({"CHUNKVAULT_CDC_MIN_CHUNK_SIZE": "99999999"})
    with pytest.raises(ConfigurationError):
        Settings.from_env({"CHUNKVAULT_CORS_ORIGINS": "*"})
    with pytest.raises(ConfigurationError):
        Settings.from_env({"CHUNKVAULT_CORS_ORIGINS": " , "})


def test_ensure_directories(tmp_path):
    s = Settings.from_env({"CHUNKVAULT_DATA_DIR": str(tmp_path / "d")})
    s.ensure_directories()
    assert s.chunks_dir.is_dir() and s.staging_dir.is_dir() and s.benchmarks_dir.is_dir()
