# 来源与移植说明

这是非官方 MaiBot 移植版，原作作者未对本移植版作出维护或兼容承诺。

- 原插件：[Pancakes-Labs/astrbot_plugin_live_dashboard](https://github.com/Pancakes-Labs/astrbot_plugin_live_dashboard)
- 原作作者：DBJD-CR / Pancakes-Labs
- 移植基线提交：`781542c4dc511e5cbdfe1b71e0f9b138200e1f6a`
- 原插件版本记录完整保留在 `UPSTREAM_CHANGELOG.md`。
- 上游面板服务：[Monika-Dream/live-dashboard](https://github.com/Monika-Dream/live-dashboard)，本包仍是该服务的查询客户端。
- 本移植版及源自原插件的服务、渲染器、过滤器、横幅、图标按照随附 `LICENSE` 中的 GNU AGPL v3 条款提供。原作版权声明保留。
- 新增和修改：MaiBot SDK 入口、配置模型、RPC 消息发送、配置更新期间的资源管理、配置迁移工具、测试及文档。1.1.0 移除了原移植版的 Context Item 提示注入，新增轻量状态事实输出及短时查询缓存。
- 匿名遥测保留批量发送、节流、心跳和配置/错误事件；默认关闭，接收地址和 App Key 由安装者填写，未复用原作的遥测接收地址和凭据。
- 移植版源码与安装包：[hoangmaituan882-hue/maibot_plugin_live_dashboard](https://github.com/hoangmaituan882-hue/maibot_plugin_live_dashboard)。`_manifest.json` 的仓库链接指向本移植版，原作链接与版权声明继续保留，并按 AGPL 提供对应源码。

适配参照：MaiBot `062d45cf8bd4272ac57d7ec03fcf405e5a458211`（1.3.15），SDK `835e0e9d1f70ea00885fb49d059f7f2864976d05`（2.16.0）。
