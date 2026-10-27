"""Snapshots were produced by upstream commit 781542c4, not by the port."""

import json
from pathlib import Path

import pytest
from maibot_plugin_live_dashboard.services.health_renderer import render_health_message
from maibot_plugin_live_dashboard.services.message_renderer import (
    render_dashboard_message_with_count,
)
from maibot_plugin_live_dashboard.services.timeline_renderer import render_timeline_message

CASES = json.loads(
    (Path(__file__).parent / "fixtures/upstream_rendering.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("case", CASES, ids=[f"{c['renderer']}-{i}" for i, c in enumerate(CASES)])
def test_upstream_rendering_parity(case):
    renderer = {
        "status": render_dashboard_message_with_count,
        "timeline": render_timeline_message,
        "health": render_health_message,
    }[case["renderer"]]
    result = renderer(case["payload"], case["config"])
    if isinstance(result, tuple):
        result = list(result)
    assert result == case["expected"]
