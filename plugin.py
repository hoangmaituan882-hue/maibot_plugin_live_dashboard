"""MaiBot entry point for the full Live Dashboard port (AGPL-3.0-only)."""

from __future__ import annotations

import asyncio
import re
from typing import Any

from maibot_sdk import Command, MaiBotPlugin, Tool
from maibot_sdk.types import ToolParameterInfo, ToolParamType

from .config import LiveDashboardConfig
from .diary.service import DiaryError, DiaryService
from .runtime import QueryRuntime
from .services.telemetry_utils import track_feature_safely
from .utils.banner import print_banner
from .utils.config_parser import get_bool_value, get_int_value, get_text_value, parse_list_config

# The upstream allows 600-second HTTP requests; health can require two requests.
_QUERY_TIMEOUT_MS = 1_250_000
_ERROR_PREFIXES = (
    "未配置 Live Dashboard 地址",
    "日期格式不正确：",
    "请求超时：",
    "鉴权失败：",
    "请求失败：",
    "网络错误：",
    "响应异常：",
    "发生未预期错误：",
    "服务地址不合法：",
)
_FRIEND_ACTIONS = {
    "状态": "status",
    "status": "status",
    "时间线": "timeline",
    "timeline": "timeline",
    "健康": "health",
    "健康数据": "health",
    "health": "health",
    "配置": "config",
    "config": "config",
}


def _split_message_blocks(message: str) -> list[str]:
    return [block.strip() for block in message.split("\n\n") if block.strip()] or [message]


def _parse_friend_args(raw: str) -> tuple[str, str, str | None]:
    tokens = raw.split()
    if not tokens:
        return "", "status", None
    action = _FRIEND_ACTIONS.get(tokens[1].lower(), "status") if len(tokens) > 1 else "status"
    date = tokens[2] if len(tokens) > 2 and action in ("timeline", "health") else None
    return tokens[0], action, date


def _identity(context: dict[str, Any]) -> tuple[str, str, str, str]:
    message = context.get("message")
    message = message if isinstance(message, dict) else {}
    info = message.get("message_info")
    info = info if isinstance(info, dict) else {}
    user = info.get("user_info") or {}
    group = info.get("group_info") or {}
    user = user if isinstance(user, dict) else {}
    group = group if isinstance(group, dict) else {}
    return (
        str(
            context.get("stream_id") or context.get("session_id") or message.get("session_id") or ""
        ),
        str(context.get("user_id") or user.get("user_id") or ""),
        str(context.get("group_id") or group.get("group_id") or ""),
        str(context.get("platform") or message.get("platform") or "").lower(),
    )


def _denied(config: dict[str, Any], context: dict[str, Any]) -> str | None:
    if not get_bool_value(config, "plugin.enabled", True):
        return "Live Dashboard 插件已禁用。"
    stream, user, group, platform = _identity(context)
    group_blocks = parse_list_config(get_text_value(config, "group_blacklist_sessions"))
    user_blocks = parse_list_config(get_text_value(config, "user_blacklist_senders"))
    if (user_blocks and not user) or (group_blocks and not stream):
        return "无法确认查询者身份或会话，已拒绝查询。"
    group_targets = {stream}
    if group:
        group_targets.update((group, f"{platform}:{group}", f"default:GroupMessage:{group}"))
    if any(
        blocked in group_targets or (group and blocked.endswith(f":{group}"))
        for blocked in group_blocks
    ):
        return "该群组已禁用状态查询喵。"
    if user and any(blocked in (user, f"{platform}:{user}") for blocked in user_blocks):
        return "你已被禁止使用该查询喵。"
    return None


class LiveDashboardPlugin(MaiBotPlugin):
    config_model = LiveDashboardConfig

    def __init__(self) -> None:
        super().__init__()
        self._runtime: QueryRuntime | None = None
        self._diary: DiaryService | None = None
        self._retiring: set[asyncio.Task] = set()

    def get_components(self) -> list[dict[str, Any]]:
        components = super().get_components()
        for component in components:
            if component["type"] == "TOOL" and component["name"] != "query_live_dashboard_status":
                component["metadata"]["enabled"] = False
        return components

    async def on_load(self) -> None:
        self._runtime = QueryRuntime(self.get_plugin_config_data(), self.ctx.paths.data_dir)
        self._runtime.start()
        await self._replace_diary()
        print_banner()
        self.ctx.logger.info(
            "Live Dashboard 轻量版已加载；实时状态工具默认启用，其他查询工具可按需启用。"
        )

    async def on_unload(self) -> None:
        diary, self._diary = self._diary, None
        if diary is not None:
            await diary.close()
        current, self._runtime = self._runtime, None
        if current is not None:
            await current.close()
        if self._retiring:
            await asyncio.gather(*tuple(self._retiring), return_exceptions=True)

    async def on_config_update(self, scope: str, config_data: dict, version: str) -> None:
        del version
        if scope != "self":
            return
        # Validate before changing anything. Active queries retain their original runtime.
        self.set_plugin_config(config_data)
        await self._replace_diary()
        replacement = QueryRuntime(self.get_plugin_config_data(), self.ctx.paths.data_dir)
        previous, self._runtime = self._runtime, replacement
        replacement.start()
        if previous is not None:
            task = asyncio.create_task(previous.close())
            self._retiring.add(task)
            task.add_done_callback(self._retiring.discard)

    async def _replace_diary(self) -> None:
        previous, self._diary = self._diary, None
        if previous is not None:
            await previous.close()
        diary = DiaryService(self.get_plugin_config_data(), self.ctx.paths.data_dir, self.ctx)
        try:
            diary.start()
        except Exception as exc:
            await diary.close()
            self.ctx.logger.warning(
                "主人日记初始化失败（%s），原有查询继续可用。", type(exc).__name__
            )
            return
        self._diary = diary

    async def _query(
        self,
        kind: str,
        context: dict,
        *,
        date: str | None = None,
        name: str = "",
        action: str = "status",
        device_id: str | None = None,
        source: str = "command",
    ) -> tuple[str, bool, int, dict]:
        runtime = self._runtime
        if runtime is None:
            return "Live Dashboard 插件尚未加载或已经停止。", False, 0, {}
        date = date.strip() or None if isinstance(date, str) else date
        action = _FRIEND_ACTIONS.get(action.strip().lower(), action.strip().lower())
        name = name.strip()
        async with runtime.lease():
            config = runtime.config
            denied = _denied(config, context)
            if denied:
                await track_feature_safely(
                    runtime.telemetry, f"{source}_{kind}", {"success": False, "blocked": True}
                )
                return denied, False, 0, config
            toggles = {
                "timeline": "时间线",
                "health": "健康数据",
                "friend": "好友面板",
                "system_status": "服务状态",
            }
            if kind in toggles and not get_bool_value(config, f"enable_{kind}_command", True):
                return f"{toggles[kind]}查询已禁用喵。", False, 0, config
            # Friend subqueries honor the same feature switches as direct queries.
            if (
                kind == "friend"
                and action in ("timeline", "health")
                and not get_bool_value(config, f"enable_{action}_command", True)
            ):
                return f"{toggles[action]}查询已禁用喵。", False, 0, config
            count = 0
            if kind == "status":
                text, count = await runtime.dashboard.query_and_render(facts=(source == "llm_tool"))
                success = not text.startswith(_ERROR_PREFIXES)
            elif kind == "timeline":
                text, count = await runtime.timeline.query_and_render(
                    date=date or None, device_id=device_id, facts=source == "llm_tool"
                )
                success = not text.startswith(_ERROR_PREFIXES)
            elif kind == "health":
                text = await runtime.health.query_and_render(date=date or None, device_id=device_id)
                success = not text.startswith(_ERROR_PREFIXES)
            elif kind == "system_status":
                text = await runtime.system.query_and_render()
                success = not text.startswith(_ERROR_PREFIXES)
            else:
                text, success = await self._query_friend(
                    runtime, name, action, date, facts=source == "llm_tool"
                )
            await track_feature_safely(
                runtime.telemetry, f"{source}_{kind}", {"success": success, "blocked": False}
            )
            return text, success, count, config

    @staticmethod
    async def _query_friend(
        runtime: QueryRuntime, name: str, action: str, date: str | None, *, facts: bool = False
    ) -> tuple[str, bool]:
        if not name.strip():
            dashboards, error = await runtime.friends.list_friends()
            if error:
                return error, False
            if not dashboards:
                return "当前未配置任何好友面板喵。", True
            names = "、".join(str(item.get("name") or item.get("id") or "?") for item in dashboards)
            return (
                f"👥 当前已配置的好友面板：\n{names}\n\n使用 /视奸好友 <名称> [时间线/健康/配置] [日期] 查询。",
                True,
            )
        action = _FRIEND_ACTIONS.get(action.lower(), action.lower())
        if action == "timeline":
            return await runtime.friends.query_friend_timeline(name, date=date or None, facts=facts)
        if action == "health":
            return await runtime.friends.query_friend_health(name, date=date or None)
        if action == "config":
            return await runtime.friends.query_friend_config(name)
        if action != "status":
            return (
                "好友子命令应为 状态/status、时间线/timeline、健康/health 或 配置/config。",
                False,
            )
        return await runtime.friends.query_friend_status(name)

    async def _send(
        self, text: str, context: dict, config: dict, *, force_forward: bool = False
    ) -> bool:
        stream, _, _, platform = _identity(context)
        if not stream:
            return False
        platforms = parse_list_config(
            get_text_value(config, "forward_platforms", "qq,aiocqhttp,onebot,onebot11"),
            to_lower=True,
        )
        threshold = get_int_value(config, "forward_char_threshold", 512, min_value=100)
        if (
            get_bool_value(config, "enable_forward_messages", True)
            and platform in platforms
            and (force_forward or len(text) > threshold)
        ):
            nodes = [
                {
                    "user_id": "0",
                    "nickname": "Live Dashboard",
                    "segments": [{"type": "text", "content": block}],
                }
                for block in _split_message_blocks(text)
            ]
            try:
                if await self.ctx.send.forward(nodes, stream):
                    return True
            except Exception:
                self.ctx.logger.debug("合并转发不可用，回退完整文本。")
        chunk_size = get_int_value(config, "text_chunk_size", 3000, min_value=200)
        for offset in range(0, len(text), chunk_size):
            if not await self.ctx.send.text(
                text[offset : offset + chunk_size], stream, set_reply=(offset == 0)
            ):
                return False
        return True

    async def _command(self, kind: str, context: dict, **query: Any) -> tuple[bool, str, bool]:
        text, success, count, config = await self._query(kind, context, **query)
        sent = await self._send(
            text, context, config, force_forward=(kind == "status" and count >= 2)
        )
        return success and sent, text, True

    @staticmethod
    def _command_arg(context: dict, name: str, pattern: str) -> str:
        groups = context.get("matched_groups")
        if isinstance(groups, dict) and name in groups:
            return str(groups.get(name) or "").strip()
        match = re.fullmatch(
            pattern, str(context.get("text") or context.get("raw_message") or "").strip()
        )
        return str(match.groupdict().get(name) or "").strip() if match else ""

    @Command(
        "live_dashboard_status",
        description="查询实时设备状态",
        pattern=r"^/?(?:视奸|ldb|livedashboard|设备状态|状态面板)\s*$",
        aliases=["/ldb", "/livedashboard", "/设备状态", "/状态面板"],
        timeout_ms=_QUERY_TIMEOUT_MS,
    )
    async def query_live_dashboard(self, **kwargs):
        return await self._command("status", kwargs)

    @Command(
        "live_dashboard_timeline",
        description="查询应用使用时间线",
        pattern=r"^/?(?:视奸时间线|视奸历史)(?:\s+(?P<date>\S+))?\s*$",
        aliases=["/视奸历史"],
        timeout_ms=_QUERY_TIMEOUT_MS,
    )
    async def query_timeline(self, **kwargs):
        date = self._command_arg(
            kwargs, "date", r"/?(?:视奸时间线|视奸历史)(?:\s+(?P<date>\S+))?\s*"
        )
        return await self._command("timeline", kwargs, date=date or None)

    @Command(
        "live_dashboard_health",
        description="查询健康数据",
        pattern=r"^/?(?:视奸健康|健康数据)(?:\s+(?P<date>\S+))?\s*$",
        aliases=["/健康数据"],
        timeout_ms=_QUERY_TIMEOUT_MS,
    )
    async def query_health(self, **kwargs):
        date = self._command_arg(kwargs, "date", r"/?(?:视奸健康|健康数据)(?:\s+(?P<date>\S+))?\s*")
        return await self._command("health", kwargs, date=date or None)

    @Command(
        "live_dashboard_system_status",
        description="检查上游服务状态",
        pattern=r"^/?视奸服务状态\s*$",
        timeout_ms=_QUERY_TIMEOUT_MS,
    )
    async def query_system_status(self, **kwargs):
        return await self._command("system_status", kwargs)

    @Command(
        "live_dashboard_friend",
        description="查询好友面板及其时间线、健康和配置",
        pattern=r"^/?(?:视奸好友|好友面板)(?:\s+(?P<args>.*))?\s*$",
        aliases=["/好友面板"],
        timeout_ms=_QUERY_TIMEOUT_MS,
    )
    async def query_friend(self, **kwargs):
        raw = self._command_arg(kwargs, "args", r"/?(?:视奸好友|好友面板)(?:\s+(?P<args>.*))?\s*")
        name, action, date = _parse_friend_args(raw)
        return await self._command("friend", kwargs, name=name, action=action, date=date)

    async def _diary_action(
        self, action: str, context: dict, *, date: str = "", end_date: str = ""
    ) -> dict:
        service = self._diary
        if service is None:
            return {"success": False, "content": "主人日记尚未加载或已经停止。"}
        try:
            denied = _denied(service.config, context)
            if denied:
                raise DiaryError(denied)
            service.require_enabled()
            stream, user, _, platform = _identity(context)
            manage = action in ("生成", "重新生成", "删除", "清空", "导出", "状态")
            if not service.authorized(stream=stream, user=user, platform=platform, admin=manage):
                raise DiaryError(
                    "未授权读取或管理主人日记，请在日记配置中明确设置用户或聊天流权限。"
                )
            if action in ("生成", "重新生成"):
                if end_date:
                    raise DiaryError("每次只能生成一天日记。")
                entry = await service.generate(date, force=action == "重新生成")
                text = service.render([entry])
            elif action == "删除":
                if end_date:
                    raise DiaryError("每次只能删除一天日记。")
                text = f"已删除 {await service.delete(date)} 篇日记及其修订记录。"
            elif action == "清空":
                if date or end_date:
                    raise DiaryError("清空命令不接受日期；删除某天请使用 删除。")
                text = f"已清空当前面板和时区的 {await service.delete(None)} 篇日记及其修订记录。"
            elif action == "状态":
                text = (
                    f"主人日记已启用；时区 {service.zone}，每天 {service.options['generation_time']} 汇总前一天；"
                    f"保留 {service.options['retention_days']} 天；生成会话"
                    + (
                        "已配置。"
                        if service.options["generation_stream_id"]
                        else "未配置，使用事实版。"
                    )
                    + "日记不会自动发送，也不会写入原生长期记忆。"
                    + f"\n当前平台：{platform}；当前聊天流 ID：{stream}。"
                )
            elif action == "导出":
                paths = service.export(date, end_date)
                text = (
                    "已导出为服务端本地 Markdown 文件（含完整结构化事实）：\n"
                    + "\n".join(str(path) for path in paths)
                    if paths
                    else "指定日期尚无日记，无法导出。"
                )
            else:
                text = service.render(service.read(date, end_date))
            return {"success": True, "content": text}
        except DiaryError as exc:
            return {"success": False, "content": str(exc)}
        except Exception as exc:
            self.ctx.logger.warning("主人日记操作失败：%s", type(exc).__name__)
            return {"success": False, "content": "日记操作失败，请查看服务端日志中的错误类型。"}

    @Command(
        "owner_diary",
        description="主人日记：查看/生成/重新生成/删除/清空/导出/状态，日期可填昨天或 YYYY-MM-DD",
        pattern=r"^/?主人日记(?:\s+(?P<args>.*))?\s*$",
        timeout_ms=_QUERY_TIMEOUT_MS,
    )
    async def owner_diary_command(self, **kwargs):
        raw = self._command_arg(kwargs, "args", r"/?主人日记(?:\s+(?P<args>.*))?\s*")
        parts = raw.split()
        action = (
            parts.pop(0)
            if parts and parts[0] in ("查看", "生成", "重新生成", "删除", "清空", "导出", "状态")
            else "查看"
        )
        if len(parts) > 2:
            result = {"success": False, "content": "用法：/主人日记 [操作] [日期] [结束日期]"}
        else:
            result = await self._diary_action(
                action,
                kwargs,
                date=parts[0] if parts else "",
                end_date=parts[1] if len(parts) > 1 else "",
            )
        config = self.get_plugin_config_data()
        sent = await self._send(result["content"], kwargs, config)
        return result["success"] and sent, result["content"], True

    @Tool(
        "query_owner_diary",
        description=(
            "只读查询面板主人的已归档日记，主人身份以人设为准。询问过去某日或最近几天活动时使用；"
            "date 默认昨天，可用 YYYY-MM-DD、昨天、前天；end_date 可选，每次最多7天。"
            "查询现在的活动请使用实时状态工具；日记缺失时不编造。"
            "将相关日记日期和活动事实带入 reply_reference，由原生 Replyer 自然回复。"
        ),
        parameters=[
            ToolParameterInfo(
                name="date",
                param_type=ToolParamType.STRING,
                description="起始日期，默认昨天",
                required=False,
            ),
            ToolParameterInfo(
                name="end_date",
                param_type=ToolParamType.STRING,
                description="可选结束日期，包含该日",
                required=False,
            ),
        ],
        timeout_ms=10000,
    )
    async def query_owner_diary_tool(self, date: str = "", end_date: str = "", **kwargs):
        return await self._diary_action("查看", kwargs, date=date, end_date=end_date)

    async def _tool(self, kind: str, context: dict, **query) -> dict:
        text, success, _, _ = await self._query(kind, context, source="llm_tool", **query)
        return {"success": success, "content": text}

    @Tool(
        "query_live_dashboard_status",
        description=(
            "查询配置面板主人的当前设备活动、前台应用、音乐和上报时间；主人是谁以人设说明为准，结果不代表提问者的设备。"
            "在有人询问主人当前在做什么时使用。结果为观测事实；回复时将相关事实与上报时间带入 reply_reference，"
            "由原生回复模型组织语言。离线或旧上报不能当作当前活动。"
        ),
        timeout_ms=_QUERY_TIMEOUT_MS,
    )
    async def query_live_dashboard_status_tool(self, **kwargs):
        return await self._tool("status", kwargs)

    @Tool(
        "query_live_dashboard_timeline",
        description=(
            "查询面板主人今天、昨天、前天或指定日期的应用使用统计和活动时间线；主人身份以人设为准。"
            "询问今天主要用了什么、昨天忙什么时使用；当前正在做什么请用实时状态工具。"
            "返回过滤后的统计和代表性片段，可能省略细节；不是当前状态，不推断未记录的活动。"
            "将相关日期、时区和事实通过 reply_reference 交给原生 Replyer 按人设回答。"
        ),
        parameters=[
            ToolParameterInfo(
                name="date",
                param_type=ToolParamType.STRING,
                description="可选 YYYY-MM-DD、今天、昨天、前天；省略表示配置时区的今天",
                required=False,
            )
        ],
        timeout_ms=_QUERY_TIMEOUT_MS,
    )
    async def query_live_dashboard_timeline_tool(self, date: str | None = None, **kwargs):
        return await self._tool("timeline", kwargs, date=date)

    @Tool(
        "query_live_dashboard_health",
        description="查询配置时区下指定日期的心率、步数、睡眠等健康数据；date 可填今天、昨天、前天或 YYYY-MM-DD，省略表示今天。",
        parameters=[
            ToolParameterInfo(
                name="date",
                param_type=ToolParamType.STRING,
                description="可选 YYYY-MM-DD、今天、昨天或前天",
                required=False,
            )
        ],
        timeout_ms=_QUERY_TIMEOUT_MS,
    )
    async def query_live_dashboard_health_tool(self, date: str | None = None, **kwargs):
        return await self._tool("health", kwargs, date=date)

    @Tool(
        "query_friend_dashboard_status",
        description="查询好友面板。name 为面板名或 ID，留空列出面板；action 可选 status/timeline/health/config；date 是可选日期。",
        parameters=[
            ToolParameterInfo(
                name="name",
                param_type=ToolParamType.STRING,
                description="好友面板名称或 ID，省略列出面板",
                required=False,
            ),
            ToolParameterInfo(
                name="action",
                param_type=ToolParamType.STRING,
                description="status/timeline/health/config，默认 status",
                required=False,
            ),
            ToolParameterInfo(
                name="date",
                param_type=ToolParamType.STRING,
                description="时间线或健康数据的日期，可用 YYYY-MM-DD、今天、昨天或前天",
                required=False,
            ),
        ],
        timeout_ms=_QUERY_TIMEOUT_MS,
    )
    async def query_friend_dashboard_status_tool(
        self, name: str = "", action: str = "status", date: str | None = None, **kwargs
    ):
        return await self._tool("friend", kwargs, name=name, action=action, date=date)


def create_plugin() -> LiveDashboardPlugin:
    return LiveDashboardPlugin()
