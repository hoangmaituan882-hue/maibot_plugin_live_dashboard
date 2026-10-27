"""Optional tests against the actual MaiBot source, without starting chat adapters or LLMs."""

from __future__ import annotations

import ast
import importlib
import os
import shutil
import sys
from pathlib import Path

import msgpack
import pytest
from maibot_sdk import PluginContext, PluginPaths

ROOT = Path(__file__).resolve().parents[1]
HOST_SOURCE = os.environ.get("MAIBOT_SOURCE")
pytestmark = pytest.mark.skipif(
    not HOST_SOURCE, reason="Set MAIBOT_SOURCE to a MaiBot 1.3.15 checkout"
)


def host_import(module):
    sys.path.insert(0, str(Path(HOST_SOURCE).resolve()))
    return importlib.import_module(module)


@pytest.mark.parametrize("previous_version", ["1.0.0", "1.1.0", "1.2.0"])
async def test_actual_manifest_loader_runner_and_msgpack(tmp_path, monkeypatch, previous_version):
    monkeypatch.setenv("MAIBOT_HOST_VERSION", "1.3.15")
    loader_type = host_import("src.plugin_runtime.runner.plugin_loader").PluginLoader
    runner_type = host_import("src.plugin_runtime.runner.runner_main").PluginRunner
    envelope_type = host_import("src.plugin_runtime.protocol.envelope").Envelope
    plugins = tmp_path / "plugins"
    shutil.copytree(
        ROOT,
        plugins / "live_dashboard",
        ignore=shutil.ignore_patterns(
            "__pycache__", ".pytest_cache", ".ruff_cache", "config.toml", ".venv", "logs"
        ),
    )
    # Exercise the genuine Runner's migration from the previously delivered configuration.
    (plugins / "live_dashboard/config.toml").write_text(
        f'[plugin]\nconfig_version = "{previous_version}"\nenabled = true\n'
        '[commands]\ninject_tool_prompt = true\n[diary]\ntimezone = "Europe/Berlin"\n',
        encoding="utf-8",
    )
    loader = loader_type(host_version="1.3.15")
    loaded = loader.discover_and_load([str(plugins)])
    assert not loader.failed_plugins
    assert len(loaded) == 1
    meta = loaded[0]
    loader.set_loaded_plugin(meta)
    assert meta.plugin_id == "local.live-dashboard"
    assert not loader.manifest_validator.warnings
    assert set(meta.capabilities_required) == {
        "send.text",
        "send.forward",
        "maisaka.reply.generate",
    }
    instance = meta.instance
    runner = runner_type("tcp://127.0.0.1:1", "test-only", [str(plugins)])
    runner._loader = loader
    assert runner._prepare_plugin_activation(meta) is None
    assert not meta.config_error
    assert (Path(meta.plugin_dir) / "config.toml").is_file()
    assert instance.config.plugin.config_version == "1.2.1"
    from maibot_plugin_live_dashboard.utils.query_time import query_zone

    assert str(query_zone(instance.get_plugin_config_data())) == "Europe/Berlin"
    assert instance.config.diary.enabled is False
    assert instance.config.caching.status_cache_ttl_sec == 10
    assert instance.config.caching.health_cache_ttl_sec == 60
    assert all(c["type"] != "HOOK_HANDLER" for c in instance.get_components())
    sent = []

    async def rpc(method, plugin_id, payload, **kwargs):
        sent.append(payload)
        return {"success": True}

    instance._set_context(
        PluginContext(meta.plugin_id, rpc, PluginPaths(tmp_path / "data", tmp_path / "runtime"))
    )
    await instance.on_load()
    meta.component_handlers = {
        c["name"]: c["metadata"]["handler_name"] for c in instance.get_components()
    }
    try:
        for number, (method, component) in enumerate(
            [
                ("plugin.invoke_tool", "query_live_dashboard_status"),
                ("plugin.invoke_command", "live_dashboard_status"),
            ],
            1,
        ):
            request = envelope_type(
                request_id=number,
                message_type="request",
                method=method,
                plugin_id=meta.plugin_id,
                payload={
                    "component_name": component,
                    "args": {"stream_id": "test-stream", "platform": "telegram", "user_id": "u1"},
                },
            )
            # Match the real wire representation before calling the genuine Runner dispatcher.
            wire = msgpack.packb(request.model_dump(mode="json"), use_bin_type=True)
            decoded = envelope_type.model_validate(msgpack.unpackb(wire, raw=False))
            reply = await runner._handle_invoke(decoded)
            assert reply.error is None
            assert reply.payload["success"] is True
            result = reply.payload["result"]
            text = result["content"] if isinstance(result, dict) else result[1]
            assert "未配置 Live Dashboard 地址" in text
        assert len(sent) == 1
        assert sent[0]["capability"] == "send.text"
        assert not runner._inflight_rpcs
    finally:
        await instance.on_unload()


def test_actual_host_component_registry(plugin):
    registry_module = host_import("src.plugin_runtime.host.component_registry")
    hook_module = host_import("src.plugin_runtime.host.hook_spec_registry")
    schema_module = host_import("src.plugin_runtime.hook_schema_utils")
    source_file = Path(HOST_SOURCE) / "src/maisaka/chat_loop_service.py"
    source = ast.parse(source_file.read_text(encoding="utf-8"))
    function = next(
        node
        for node in source.body
        if isinstance(node, ast.FunctionDef) and node.name == "register_maisaka_hook_specs"
    )
    # Extract only the actual registration function to avoid importing unrelated LLM clients.
    namespace = {
        "List": list,
        "HookSpec": hook_module.HookSpec,
        "HookSpecRegistry": hook_module.HookSpecRegistry,
        "build_object_schema": schema_module.build_object_schema,
    }
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(source_file), "exec"), namespace)
    hooks = hook_module.HookSpecRegistry()
    namespace["register_maisaka_hook_specs"](hooks)
    registry = registry_module.ComponentRegistry(hook_spec_registry=hooks)
    declarations = [
        {
            "name": component["name"],
            "component_type": component["type"],
            "metadata": component["metadata"],
        }
        for component in plugin.get_components()
    ]
    assert registry.register_plugin_components("local.live-dashboard", declarations) == 11
    assert registry.find_command_by_text("/视奸时间线 2026-10-27") is not None
    assert registry.get_component("local.live-dashboard.query_live_dashboard_status").enabled
    optional = "local.live-dashboard.query_live_dashboard_timeline"
    assert not registry.get_component(optional).enabled
    assert registry.set_component_enabled(optional, True)
    assert registry.get_component(optional).enabled
