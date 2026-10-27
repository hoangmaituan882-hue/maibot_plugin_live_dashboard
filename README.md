# Live Dashboard 视奸面板 · MaiBot 移植版

将 [Pancakes-Labs 的 AstrBot Live Dashboard 插件](https://github.com/Pancakes-Labs/astrbot_plugin_live_dashboard)移植到 MaiBot 新版插件 SDK。保留原插件的查询、渲染、过滤、好友代理和模型工具功能；无需安装 AstrBot。此为非官方移植版。

[下载安装包](https://github.com/hoangmaituan882-hue/maibot_plugin_live_dashboard/releases/latest) · [移植版源码](https://github.com/hoangmaituan882-hue/maibot_plugin_live_dashboard)

**1.2.1**：时间线模型工具改为返回简洁事实与代表性片段，由原生Planner → Replyer按人设回答；时间线、健康和好友查询支持今天、昨天、前天等日期。新增统一时区，保留旧日记时区设置。继续提供短时缓存与默认关闭的[主人日记](docs/owner-diary.md)，不注入全局Prompt，不主动发言，也不增加原生记忆同步或麦麦评价。

## 安装

需要 **MaiBot 1.3.15 / Python 3.12+ / maibot-plugin-sdk 2.16.0+**。本包面向 MaiBot 1.3 系列新版插件系统。

1. 在 MaiBot WebUI 的插件页从本地 ZIP 安装；或将本目录完整放入 `MaiBot/plugins/maibot_plugin_live_dashboard/`，确保目录内直接包含 `_manifest.json` 和 `plugin.py`。
2. 宿主根据 manifest 安装 `httpx`、`aiohttp`、`tzdata` 依赖。手动补装时，在 **MaiBot 自己的 Python 环境**执行 `python -m pip install -r plugins/maibot_plugin_live_dashboard/requirements.txt`；使用 uv 管理环境时可用 `uv pip install -r plugins/maibot_plugin_live_dashboard/requirements.txt`。
3. 启动或重载插件。打开插件配置，填写「连接配置 → Live Dashboard 服务地址」，例如 `http://127.0.0.1:3000`；仅在服务要求鉴权时填写 Token。
4. 在聊天中发送 `/视奸`。插件首次加载会根据配置模型自动生成 `config.toml`，源码包不含个人配置。
5. 若希望别人自然语言询问你的近况，在 MaiBot 原有的人设设置中说明主人是谁、常用昵称，以及 Live Dashboard 对应主人的设备。插件自身不维护身份绑定，也不把提问者默认当作面板主人。

Live Dashboard 服务需要已经部署并有设备上报数据。本插件负责查询，设备上报端和面板服务器的部署方式沿用上游项目。MaiBot 在容器内时，`127.0.0.1` 指向容器自身，应填写容器能访问到的面板地址。

## 命令

| 命令 | 别名 | 功能 |
| --- | --- | --- |
| `/视奸` | `/ldb`、`/livedashboard`、`/设备状态`、`/状态面板` | 在线设备、当前应用、状态、标题、电量、音乐、上报时间、最近活动 |
| `/视奸时间线 [日期]` | `/视奸历史` | 当天或指定日期的应用使用时长、活动片段 |
| `/视奸健康 [日期]` | `/健康数据` | 心率、步数、睡眠等健康指标 |
| `/视奸服务状态` | — | 连通性、运行时长、服务端时间 |
| `/视奸好友 [名称] [子命令] [日期]` | `/好友面板` | 列出或查询好友面板 |

日期可用`YYYY-MM-DD`、`今天/今日`、`昨天/昨日`、`前天`、`大前天`或`2026年10月26日`；不填写表示配置时区的今天。到插件「统一时间设置」填写IANA时区，例如`Asia/Shanghai`。留空兼容旧`diary.timezone`，新安装默认Asia/Shanghai。请求按目标日期的时区偏移发送（UTC+8为`tz=-480`）；夏令时切换日会查询两个窗口并合并去重，不依赖服务器或容器的本地时区。

```text
/视奸
/视奸历史 2026-10-27
/健康数据 2026-10-27
/视奸好友
/视奸好友 小明
/视奸好友 小明 时间线 2026-10-27
/视奸好友 小明 健康
/视奸好友 小明 配置
/好友面板 alice timeline 2026-10-27
```

好友名称或 ID 按不区分大小写的子串匹配。好友查询通过自己的面板 `/api/proxy` 只读代理执行，不直接请求好友面板地址。子命令支持 `状态/status`、`时间线/timeline`、`健康/health/健康数据`、`配置/config`。

## 模型自主调用

保留原插件的四个工具，另新增主人日记工具。首次注册时仅实时状态工具默认启用，其他工具默认停用，可在 MaiBot 官方组件管理中启用；实际启用状态以宿主保存的组件设置为准：

- `query_live_dashboard_status`：实时状态。
- `query_live_dashboard_timeline(date?)`：当日应用统计与时间线事实，最多30项应用统计和20个代表性片段，明确标记省略细节；不再给模型发送整篇预制面板文本。
- `query_live_dashboard_health(date?)`：健康数据。
- `query_friend_dashboard_status(name?, action?, date?)`：好友状态；额外支持列出好友及 `timeline`、`health`、`config` 子查询。
- `query_owner_diary(date?, end_date?)`：只读查询已归档的主人日记，最多7天，默认停用；需另行启用日记并配置访问权限。

可以自然语言提问「主人现在在干什么」「他在用什么应用」。使用说明属于工具自身的描述，由官方工具发现机制提供给 Planner；不需要在 Prompt 管理中添加全局文案。实时状态工具返回 JSON 事实文本，包含经过过滤的设备活动、应用、音乐、上报时间，以及本次查询的缓存时效信息；不会直接发送消息或预先写好回复。

Planner 按需调用工具，再将与问题有关的事实通过官方 `reply_reference` 交给 Replyer 自然表达。插件不直接操纵这项参数，不拦截回复，具体是否查询及如何表达仍由模型、人设和上下文决定。离线设备会明确标记为最后上报，缓存查询时间和设备上报时间分开记录。原有手动命令继续发送完整面板文本。

可以询问“主人今天主要用了什么应用”“主人昨天在忙什么”。时间线工具仍需在官方组件管理中启用。时间线事实保留所选日期、时区、缓存时效、应用汇总及片段省略标记；设备多台并用的时长不能相加解释为主人总活动时长。手动`/视奸时间线`继续使用原有面板样式与展示条数设置。

## 短时缓存

在插件配置的「缓存配置」分组可分别设置：

| 查询 | 配置键 | 默认缓存 |
| --- | --- | --- |
| 实时设备状态 | `status_cache_ttl_sec` | 10 秒 |
| 应用时间线 | `timeline_cache_ttl_sec` | 30 秒 |
| 健康数据 | `health_cache_ttl_sec` | 60 秒 |
| 服务状态 | `system_status_cache_ttl_sec` | 30 秒 |
| 好友面板列表 | `friend_config_cache_ttl_sec` | 30 秒 |

设为 `0` 可分别关闭。缓存仅在有人查询时填充或过期刷新，没有后台轮询；命令和模型工具复用同一查询服务的成功结果。缓存命中不会延长有效期。时间线/健康查询按日期、时区和设备区分；好友代理按面板 ID、子查询和日期区分。

四类查询只缓存成功的 JSON 响应；鉴权失败、网络错误、服务端失败和无效数据不缓存。过期后刷新失败会报告错误，不回退旧结果。同一时刻的相同请求合并执行，单个调用取消不会打断其他等待者。每个查询客户端最多保留 32 项，缓存只存在内存中，停用、重载或配置更新时清理。黑名单和功能开关在查询前检查，缓存不会绕过权限。

## 配置与隐私控制

全部原有配置项已映射到 MaiBot 的可视化配置模型：

| 配置分组 | 包含的设置 |
| --- | --- |
| `plugin` | 插件总开关、配置版本 |
| `connection` | 服务根地址、Bearer Token、请求超时 |
| `output_scope` | 离线设备开关、设备数量、设备名称白名单/黑名单 |
| `access_control` | 群黑名单、用户黑名单、信息关键词过滤及替换文案 |
| `output_fields` | 平台、应用、标题、电量、音乐、上报时间、访客数、服务时间、最近活动及条数 |
| `caching` | 实时状态、时间线、健康、服务状态和好友列表的缓存时长；设 0 关闭 |
| `diary` | 每日主人日记；调度、时区、生成会话、保留期限和独立读写权限，默认关闭 |
| `time_settings` | 统一时区，作用于日期解析、时间线/健康/好友查询、状态时间显示和日记；留空兼容旧日记时区 |
| `commands` | 各查询功能开关、时间线/健康数据上限、设备名显示 |
| `telemetry_config` | 可选匿名统计；默认关闭，接收地址与 App Key 可配置 |

群黑名单支持群号、MaiBot 聊天流 ID、`平台:群号`，也兼容原插件 `default:GroupMessage:群号` 写法。用户黑名单支持用户 ID 和 `平台:用户ID`。多个值可用逗号、分号或换行分隔。命令和模型工具执行前都检查，好友子查询也遵守时间线/健康功能开关。

设备名称白/黑名单作用于实时状态、时间线模型事实和日记；时间线模型结果也过滤服务端汇总中的隐藏设备，存在设备限制时不展示无法确认归属的汇总。手动时间线与健康数据继续沿用原面板展示范围。信息关键词过滤和读侧NSFW兜底继续保留，模型事实也遵守应用名、窗口标题开关。

移植版新增 `commands.enable_forward_messages`、`forward_platforms`、`forward_char_threshold` 和 `text_chunk_size`。支持的平台中，多设备状态或长文本会合并转发；不支持或发送失败时按配置长度分段回退，保留全部文本。具体合并转发支持程度取决于 MaiBot 适配器。旧 `inject_tool_prompt` 字段仅为配置兼容保留并隐藏，1.1.0 中无论其旧值如何，都不会注册提示注入 Hook。

配置保存后会建立新的查询资源并清空旧缓存；已经执行的查询使用原有配置完成后才释放旧连接。持久化数据使用 MaiBot 分配的 `ctx.paths.data_dir`。匿名统计开启时会使用该目录保存实例 ID；未启用时不会产生统计网络请求。

## 从 AstrBot 配置迁移

支持原有扁平 JSON 和分组 JSON。使用 MaiBot 的 Python 环境执行：

```text
python tools/migrate_config.py 原插件配置.json config.toml
```

输出文件必须尚不存在，以免覆盖现有配置。Token 和名单会保留在本地输出文件中，命令不会打印它们。迁移时匿名统计和旧版提示注入开关重置为关闭。将生成文件放到插件目录，再重载插件即可。

从本插件1.0.0、1.1.0或1.2.0升级时，保留个人`config.toml`，更新源码后重载；Runner会迁移到1.2.1并补齐统一时间设置。已有日记时区会继续生效，不会自动开启日记或模型工具。原有组件启用设置由宿主管理。日记启用方法与独立权限见[主人日记使用说明](docs/owner-diary.md)。

## 测试与开发

```text
uv sync --group dev
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
```

设置环境变量 `MAIBOT_SOURCE` 为 MaiBot 1.3.15 源码目录后，测试还会使用真实的清单校验器、PluginLoader、Runner 和组件注册表，验证旧配置升级及官方组件开关。只安装 SDK 时，这部分宿主集成测试会跳过。测试使用模拟数据与本机 HTTP 服务，无需私人面板地址或模型密钥。

完整对照表见 `PORTING.md`，验证结果见 `VALIDATION.md`。构建安装包：

```text
python tools/build_release.py ../maibot_plugin_live_dashboard-1.2.1.zip
```

原作来源、源码基线与许可见 `NOTICE.md`、`LICENSE`。本移植版源码与安装包在上述独立仓库提供，按 AGPL 保留源码与版权信息。
