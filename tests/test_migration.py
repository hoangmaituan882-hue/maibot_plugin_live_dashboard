import importlib.util
import tomllib
from pathlib import Path


def test_flat_and_grouped_config_migration():
    path = Path(__file__).resolve().parents[1] / "tools/migrate_config.py"
    spec = importlib.util.spec_from_file_location("migration_tool", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    config = migration.convert(
        {
            "base_url": "http://127.0.0.1:3000",
            "enabled": True,
            "auth_token": "example-token",
            "show_music": False,
            "access_control": {"user_blacklist_senders": "123,456"},
            "telemetry_config": {"enabled": True},
        }
    )
    roundtrip = tomllib.loads(migration.to_toml(config))
    assert roundtrip == config
    assert config["connection"]["auth_token"] == "example-token"
    assert config["access_control"]["user_blacklist_senders"] == "123,456"
    assert not config["output_fields"]["show_music"]
    assert not config["telemetry_config"]["enabled"]
    assert config["plugin"]["config_version"] == "1.2.1"
    assert not config["diary"]["enabled"]
