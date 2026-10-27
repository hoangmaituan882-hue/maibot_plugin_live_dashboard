"""MaiBot strong configuration models; all upstream options are retained."""

from datetime import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from maibot_sdk import Field, PluginConfigBase
from pydantic import field_validator


class PluginSection(PluginConfigBase):
    __ui_label__ = "插件设置"
    config_version: str = Field(default="1.2.1", description="配置版本")
    enabled: bool = Field(default=True, description="启用插件")


class ConnectionConfig(PluginConfigBase):
    __ui_label__ = "🌐 连接配置"
    base_url: str = Field(
        default="",
        description="Live Dashboard 服务地址",
        json_schema_extra={
            "label": "Live Dashboard 服务地址",
            "hint": "例如 https://your-domain.com 或 http://localhost:3000。不需要填写 /api/current，插件会自动拼接。",
        },
    )
    auth_token: str = Field(
        default="",
        description="可选鉴权 Token",
        json_schema_extra={
            "label": "可选鉴权 Token",
            "hint": "仅当你的服务端或反向代理要求认证时填写。会以 Authorization: Bearer <token> 形式发送。",
            "x-widget": "password",
        },
    )
    request_timeout_sec: int = Field(
        default=30,
        description="请求超时时间（秒）",
        ge=5,
        le=600,
        json_schema_extra={"label": "请求超时时间（秒）"},
    )


class OutputScopeConfig(PluginConfigBase):
    __ui_label__ = "📟 输出范围配置"
    include_offline_devices: bool = Field(
        default=False,
        description="是否包含离线设备",
        json_schema_extra={"label": "是否包含离线设备"},
    )
    max_devices: int = Field(
        default=10,
        description="最多展示设备数量",
        ge=1,
        json_schema_extra={"label": "最多展示设备数量", "hint": "用于避免回复过长"},
    )
    device_whitelist_keywords: str = Field(
        default="",
        description="设备白名单关键词（命中才显示）",
        json_schema_extra={
            "label": "设备白名单关键词（命中才显示）",
            "hint": "支持逗号/分号/换行分隔；仅匹配设备名。",
        },
    )
    device_blacklist_keywords: str = Field(
        default="",
        description="设备黑名单关键词（命中则隐藏）",
        json_schema_extra={
            "label": "设备黑名单关键词（命中则隐藏）",
            "hint": "支持逗号/分号/换行分隔；仅匹配设备名，黑名单优先级高于白名单。",
        },
    )


class AccessControlConfig(PluginConfigBase):
    __ui_label__ = "🛡️ 访问控制与隐私安全"
    group_blacklist_sessions: str = Field(
        default="",
        description="群组黑名单",
        json_schema_extra={
            "label": "群组黑名单",
            "hint": "支持逗号/分号/换行分隔；命中后该会话内不响应查询。可填写完整会话 UMO（如 default:GroupMessage:123456789），也可仅填写尾部群号。",
        },
    )
    user_blacklist_senders: str = Field(
        default="",
        description="用户黑名单",
        json_schema_extra={
            "label": "用户黑名单",
            "hint": "支持逗号/分号/换行分隔；命中后不响应该用户查询。",
        },
    )
    info_blacklist_keywords: str = Field(
        default="",
        description="信息黑名单关键词（现在/应用/标题命中即替换）",
        json_schema_extra={
            "label": "信息黑名单关键词（现在/应用/标题命中即替换）",
            "hint": "支持逗号/分号/换行分隔；用于隐藏个人姓名、私密应用等敏感词。",
        },
    )
    info_blacklist_replacement: str = Field(
        default="不想让你看到我在干什么喵~",
        description="信息黑名单替换文案",
        json_schema_extra={
            "label": "信息黑名单替换文案",
            "hint": "当现在叙事、应用名或标题命中信息黑名单关键词时，用该文案替换。",
        },
    )


class OutputFieldsConfig(PluginConfigBase):
    __ui_label__ = "🧩 输出字段开关"
    show_platform: bool = Field(
        default=True,
        description="显示设备平台（windows/macos/android）",
        json_schema_extra={"label": "显示设备平台（windows/macos/android）"},
    )
    show_app_name: bool = Field(
        default=True, description="显示当前应用名", json_schema_extra={"label": "显示当前应用名"}
    )
    show_display_title: bool = Field(
        default=True,
        description="显示窗口净化标题字段",
        json_schema_extra={"label": "显示窗口净化标题字段", "hint": "若更重视隐私可关闭"},
    )
    show_battery: bool = Field(
        default=True, description="显示电量信息", json_schema_extra={"label": "显示电量信息"}
    )
    show_music: bool = Field(
        default=True, description="显示音乐信息", json_schema_extra={"label": "显示音乐信息"}
    )
    show_last_seen: bool = Field(
        default=True,
        description="显示最后上报时间",
        json_schema_extra={"label": "显示最后上报时间"},
    )
    show_viewer_count: bool = Field(
        default=False, description="显示访客数量", json_schema_extra={"label": "显示访客数量"}
    )
    show_server_time: bool = Field(
        default=False, description="显示服务端时间", json_schema_extra={"label": "显示服务端时间"}
    )
    show_recent_activities: bool = Field(
        default=False,
        description="显示最近活动列表",
        json_schema_extra={"label": "显示最近活动列表", "hint": "是否展示最近的活动状态。"},
    )
    recent_activities_max: int = Field(
        default=5,
        description="最近活动状态最多展示条数",
        ge=1,
        le=20,
        json_schema_extra={"label": "最近活动状态最多展示条数", "hint": "用于避免回复过长"},
    )


class CachingConfig(PluginConfigBase):
    __ui_label__ = "⚡ 缓存配置"
    status_cache_ttl_sec: int = Field(
        default=10,
        ge=0,
        le=3600,
        description="实时状态缓存（秒），0 关闭",
        json_schema_extra={
            "label": "实时状态缓存（秒）",
            "hint": "仅在查询时刷新，0 表示每次重新查询。",
        },
    )
    timeline_cache_ttl_sec: int = Field(
        default=30,
        ge=0,
        le=3600,
        description="时间线缓存（秒），0 关闭",
        json_schema_extra={
            "label": "时间线缓存（秒）",
            "hint": "不同日期、时区和设备分别缓存；0 关闭。",
        },
    )
    health_cache_ttl_sec: int = Field(
        default=60,
        ge=0,
        le=3600,
        description="健康数据缓存（秒），0 关闭",
        json_schema_extra={
            "label": "健康数据缓存（秒）",
            "hint": "不同日期和设备分别缓存；0 关闭。",
        },
    )
    system_status_cache_ttl_sec: int = Field(
        default=30,
        ge=0,
        le=3600,
        description="服务状态缓存（秒），0 关闭",
        json_schema_extra={
            "label": "服务状态缓存（秒）",
            "hint": "缓存连通性、运行时长及服务端时间；0 关闭。",
        },
    )
    friend_config_cache_ttl_sec: int = Field(
        default=30,
        description="好友面板列表缓存时长（秒）",
        ge=0,
        le=600,
        json_schema_extra={"label": "好友面板列表缓存时长（秒）", "hint": "设置为 0 可禁用缓存"},
    )


class CommandsConfig(PluginConfigBase):
    __ui_label__ = "🛠️ 命令与功能开关"
    enable_system_status_command: bool = Field(
        default=True,
        description="启用服务状态查询",
        json_schema_extra={
            "label": "启用服务状态查询",
            "hint": "查询 Live Dashboard 服务连通性、运行时长与时间。",
        },
    )
    enable_timeline_command: bool = Field(
        default=True,
        description="启用时间线查询",
        json_schema_extra={"label": "启用时间线查询", "hint": "查询指定日期每个应用的使用时长"},
    )
    timeline_max_segments: int = Field(
        default=40,
        description="时间线最多展示片段数",
        ge=5,
        le=200,
        json_schema_extra={"label": "时间线最多展示片段数", "hint": "用于避免回复过长"},
    )
    timeline_show_device_names: bool = Field(
        default=True,
        description="时间线展示设备名",
        json_schema_extra={"label": "时间线展示设备名", "hint": "关闭后仅保留应用名与时长"},
    )
    enable_health_command: bool = Field(
        default=True,
        description="启用健康数据查询",
        json_schema_extra={
            "label": "启用健康数据查询",
            "hint": "查询指定日期的心率/步数/睡眠等数据",
        },
    )
    health_max_records: int = Field(
        default=40,
        description="健康数据最多展示记录数",
        ge=5,
        le=200,
        json_schema_extra={"label": "健康数据最多展示记录数", "hint": "用于避免回复过长"},
    )
    enable_friend_command: bool = Field(
        default=True,
        description="启用好友面板查询",
        json_schema_extra={"label": "启用好友面板查询", "hint": "查询聚合的好友面板状态"},
    )
    enable_forward_messages: bool = Field(
        default=True,
        description="启用长消息合并转发",
        json_schema_extra={"label": "启用长消息合并转发"},
    )
    forward_platforms: str = Field(
        default="qq,aiocqhttp,onebot,onebot11",
        description="支持合并转发的平台",
        json_schema_extra={
            "label": "支持合并转发的平台",
            "hint": "逗号分隔；发送失败自动回退完整文本。",
        },
    )
    forward_char_threshold: int = Field(
        default=512,
        description="合并转发字符阈值",
        ge=100,
        le=20000,
        json_schema_extra={"label": "合并转发字符阈值"},
    )
    text_chunk_size: int = Field(
        default=3000,
        description="文本回退单条字符数",
        ge=200,
        le=20000,
        json_schema_extra={"label": "文本回退单条字符数"},
    )
    inject_tool_prompt: bool = Field(
        default=False,
        description="旧版兼容字段；1.1.0 起不再注入全局提示",
        json_schema_extra={"hidden": True, "disabled": True},
    )


class TelemetryConfigConfig(PluginConfigBase):
    __ui_label__ = "📊 匿名遥测配置"
    enabled: bool = Field(
        default=False,
        description="启用匿名遥测",
        json_schema_extra={
            "label": "启用匿名遥测",
            "hint": "默认关闭；启用后向自行配置的接收地址发送匿名运行统计。",
        },
    )
    endpoint: str = Field(
        default="",
        description="遥测接收地址",
        json_schema_extra={
            "label": "遥测接收地址",
            "hint": "自建兼容批量 ingest API 的 HTTP(S) 地址；留空不发送。",
        },
    )
    app_key: str = Field(
        default="",
        description="遥测 App Key",
        json_schema_extra={"label": "遥测 App Key", "x-widget": "password"},
    )


class TimeSettingsConfig(PluginConfigBase):
    __ui_label__ = "🕒 统一时间设置"
    timezone: str = Field(
        default="",
        description="全插件时区，例如 Asia/Shanghai",
        json_schema_extra={
            "hint": "用于状态时间显示、时间线、健康及日记。留空沿用旧日记时区，新安装默认 Asia/Shanghai。"
        },
    )

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        value = value.strip()
        if value:
            try:
                ZoneInfo(value)
            except (ZoneInfoNotFoundError, ValueError) as exc:
                raise ValueError("时区无效，请使用 Asia/Shanghai 等 IANA 时区") from exc
        return value


class DiaryConfig(PluginConfigBase):
    __ui_label__ = "📔 主人日记"
    enabled: bool = Field(default=False, description="启用主人日记（安静归档，不自动发言）")
    timezone: str = Field(
        default="Asia/Shanghai",
        description="旧版时区兼容字段；请使用统一时间设置",
        json_schema_extra={"hidden": True},
    )
    generation_time: str = Field(default="00:15", description="每日汇总前一天的时间，HH:MM")
    retention_days: int = Field(default=90, ge=1, le=3650, description="日记保留天数")
    backfill_days: int = Field(
        default=3, ge=1, le=7, description="自动补录最近几天（不早于启用日）"
    )
    generation_stream_id: str = Field(
        default="", description="生成日记所用的已有聊天流 ID，留空保存事实版"
    )
    allowed_streams: str = Field(default="", description="允许读取日记的完整聊天流 ID，逗号分隔")
    allowed_users: str = Field(default="", description="允许读取的用户，平台:用户ID，逗号分隔")
    admin_users: str = Field(default="", description="允许生成、删除、导出的管理员，平台:用户ID")
    device_ids: str = Field(default="", description="记录的设备 ID 白名单；留空继承设备名称过滤")
    include_titles: bool = Field(default=False, description="在日记中保留经过过滤的窗口标题")
    reuse_upstream_summary: bool = Field(
        default=False,
        description="可选归档花信分支小结（可能不包含21点后活动）",
        json_schema_extra={
            "hint": "仅在允许标题、所有应用字段且未限制设备/信息关键词时复用；否则使用时间线。"
        },
    )
    generation_timeout_sec: int = Field(
        default=180, ge=10, le=600, description="单篇生成最长等待秒数"
    )

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("日记时区无效，请使用 Asia/Shanghai 等 IANA 时区") from exc
        return value

    @field_validator("generation_time")
    @classmethod
    def valid_time(cls, value: str) -> str:
        if len(value) != 5 or value[2] != ":":
            raise ValueError("日记生成时间应为 HH:MM")
        time.fromisoformat(value)
        return value


class LiveDashboardConfig(PluginConfigBase):
    plugin: PluginSection = Field(default_factory=PluginSection)
    connection: ConnectionConfig = Field(default_factory=ConnectionConfig)
    output_scope: OutputScopeConfig = Field(default_factory=OutputScopeConfig)
    access_control: AccessControlConfig = Field(default_factory=AccessControlConfig)
    output_fields: OutputFieldsConfig = Field(default_factory=OutputFieldsConfig)
    caching: CachingConfig = Field(default_factory=CachingConfig)
    commands: CommandsConfig = Field(default_factory=CommandsConfig)
    telemetry_config: TelemetryConfigConfig = Field(default_factory=TelemetryConfigConfig)
    diary: DiaryConfig = Field(default_factory=DiaryConfig)
    time_settings: TimeSettingsConfig = Field(default_factory=TimeSettingsConfig)
