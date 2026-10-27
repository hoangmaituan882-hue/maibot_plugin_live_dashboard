"""Convert an AstrBot JSON configuration to a MaiBot TOML configuration."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path


def convert(source: dict) -> dict:
    path = Path(__file__).resolve().parents[1] / "config.py"
    spec = importlib.util.spec_from_file_location("_live_dashboard_config_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    defaults = module.LiveDashboardConfig().model_dump()
    for group, fields in defaults.items():
        nested = source.get(group)
        for key in fields:
            if isinstance(nested, dict) and key in nested:
                fields[key] = nested[key]
            elif key in source and not isinstance(source[key], dict):
                fields[key] = source[key]
    defaults["plugin"]["config_version"] = "1.2.1"
    # Never inherit opt-in consent for a different telemetry receiver.
    defaults["telemetry_config"]["enabled"] = False
    defaults["commands"]["inject_tool_prompt"] = False
    defaults["diary"]["enabled"] = False
    return module.LiveDashboardConfig.model_validate(defaults).model_dump()


def to_toml(config: dict) -> str:
    sections = ["# Live Dashboard MaiBot configuration; keep tokens private."]
    for group, fields in config.items():
        sections.extend(("", f"[{group}]"))
        for key, value in fields.items():
            sections.append(f"{key} = {json.dumps(value, ensure_ascii=False)}")
    return "\n".join(sections) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="AstrBot plugin JSON config")
    parser.add_argument("destination", type=Path, help="New MaiBot config.toml")
    args = parser.parse_args()
    raw = json.loads(args.source.read_text(encoding="utf-8-sig"))
    if not isinstance(raw, dict):
        parser.error("输入文件必须为 JSON 对象。")
    content = to_toml(convert(raw))
    # Exclusive create avoids accidentally overwriting an installed configuration.
    with args.destination.open("x", encoding="utf-8", newline="\n") as file:
        file.write(content)
    print(f"已生成配置：{args.destination}")


if __name__ == "__main__":
    main()
