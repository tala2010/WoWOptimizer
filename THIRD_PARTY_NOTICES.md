# 第三方与分发范围

本仓库不是完整插件整合包，也不包含游戏客户端、私人光环包、玩家 SavedVariables、图标/二维码图片或商业授权后台。

| 内容 | 来源/处理 |
|---|---|
| 桌面端、恢复账本、自研插件与新免费入口 | 从项目现有源码整理，以仓库 GPL-2.0-only 公开 |
| WA 批准配置与 R11 差量中的派生片段 | 基于 WeakAuras，按 GPLv2 分发；不把上游代码声明为本项目原创。保留 LICENSE；上游源码见下方 |
| WeakAuras 完整插件及其中独立库 | 不随本仓库打包，用户从上游获取 |
| NDui、Plater、Cell、ElvUI、RangeDisplay、RurutiaSuite、GF_Nameplates、第三方轮转包/光环包 | 不分发完整源码；未确认的嵌入适配代码从公开载荷排除。调用其公开游戏内 API 不代表获得其代码再许可权 |
| Pillow、pystray、lupa、PyInstaller、Python/Tk | 通过依赖安装，不复制其源码。构建二进制时应保留工具链自动携带的许可，并随对应源码/依赖版本清单发布 |

WeakAuras 作者及其贡献者保留上游版权。[官方仓库](https://github.com/WeakAuras/WeakAuras2)及[许可证](https://github.com/WeakAuras/WeakAuras2/blob/main/LICENSE)供核对；R11 研究输入为历史 5.22.0，不代表当前上游版本。

NDui/Plater 的本地副本可读或上游仓库可浏览，都不足以替代针对具体代码的许可核验。本次未把它们的未知许可片段换个文件名后公开。原旧通用补丁器只保留一个明确拒绝使用的接口，避免误报已支持。

依赖许可入口：[Python](https://docs.python.org/3/license.html)、[Pillow](https://github.com/python-pillow/Pillow/blob/main/LICENSE)、[pystray](https://github.com/moses-palmer/pystray/blob/master/COPYING)、[Lupa](https://github.com/scoder/lupa)、[PyInstaller](https://pyinstaller.org/en/stable/license.html)。发布二进制前以实际构建环境的元数据和许可文件为准，不能只依赖此表。

如发现来源或归属遗漏，请在仓库反馈，我们会补正署名、许可证或移除相关内容。项目没有声称得到暴雪或上述插件作者的背书。
