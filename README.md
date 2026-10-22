# 团本优化大师 · 免费开源版

一个仍在验证中的 Windows / 魔兽世界性能工具。我们公开桌面端、备份恢复、数据中枢、诊断插件和 WA 研究补丁，希望大家一起找出有效的优化，也帮助推翻无效的方案。

**免费使用，无激活码、无到期限制、无授权服务器、无自动下载。尚未证明能普遍提升 FPS，更不承诺提升 10%。**

## 免费下载，解压即可使用（不用编译）

**[下载 Windows 64 位免费预览版 ZIP](https://github.com/tala2010/WoWOptimizer/releases/download/v0.1.0-preview/WoWOptimizer-Windows-x64-0.1.0-preview.zip)** · [查看发布说明](https://github.com/tala2010/WoWOptimizer/releases/tag/v0.1.0-preview)

下载 ZIP 后全部解压，双击其中的 `WoWOptimizer.exe`。不需要安装 Python，也不需要激活或连接作者服务器。选择游戏目录、关闭游戏，再按界面提示优化；修改系统设置时会请求管理员权限。

这个公开版的源码和 EXE 均免费提供，无需购买。我们希望让不懂代码的玩家也能直接试用，让愿意研究的朋友能查看原理、复现结果并参与改进。

这是解压即用的社区预览包，没有单独的安装向导。适用于 Windows 10/11 64 位；已在开发机检查启动，尚未完成干净电脑和新免费组合的游戏实测。请先阅读包内中文说明，保留备份；遇到问题可用“恢复设置”并到 Issues 反馈。

## 开发经历与总思路

项目记录从 2026 年 5 月中旬的桌面原型开始，到 10 月整理免费开源版，跨度约五个月。当前公开应用、工具和 Lua 插件源码约 3 万行。现存历史文档明确出现过至少 43 个不同的 `0.5.x` 方案编号，最近 WA 整合路线有 R1–R11 共 11 轮候选，另有 283 份编号研究报告；这些包括失败和未完成研究，不能算成成功版本数。

我们经历了系统调参、插件减负、兼容回归、共享数据中枢和精确事件准入几条路线。最终坚持的方向是：保留原有功能、显示和时序，减少重复读取和确定无关的事件；不能证明等价时保留原生。

**完整介绍：[五个月之后，我们把团本优化大师免费开源](docs/DEVELOPMENT_STORY.md)**。其中公开了路线变化、失败教训、实际效果和未完成问题。代码行数包含注释和空行，也包含派生片段，不是累计新增或全部原创行数；详细统计口径随文列出。

## 从源码运行

需要 Windows 10/11 64 位和 Python 3.11 或以上版本（开发验证环境为 Python 3.14）。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main_gui.py
```

也可安装依赖后双击 `run.cmd`。读取界面不要求管理员；应用或恢复系统设置时程序会提示提权。准备工作请参阅 [使用说明](docs/USAGE.md)。

## 能做什么

- 识别魔兽目录和客户端，提供环境与卡顿线索检查。
- 沿用原桌面端的系统/Config 设置、备份恢复、进程守护和退出恢复机制。
- 通过本地 SHA-256 清单安装自研数据中枢和 Details native-safe 桥；只给命中固定指纹的 WA 文件应用批准补丁，不认识的文件保留原生。
- 提供独立 BareRecorder，按成功 Boss 比较 FPS、P99 和卡顿帧。
- 在 `research/r11` 公开最近一次 R11 的 10 个 WA 文件差量及重建工具，**它没有默认并入桌面一键优化**。

一键优化沿用了会调整阴影、视野等非技能画面设置的历史方案，必须阅读确认窗口；这与 R11 的纯 WA 数据路径研究不同。默认进程调度为 Normal + 全部核心，未将缺乏稳定收益证据的高优先级/P 核绑定作为默认收益承诺。

## 实际效果

历史 R10 十字军 25 人测试中，四个官方可比 Boss 的提升平均为 **6.867%**；按各次总帧数/总时长合并为 **1.572%**。对 R6 的五场卡顿帧比例均下降，但平均 FPS 的 10% 目标未通过。R11 修正了指纹失配并新增事件过滤，**其 FPS 收益尚未得到实机证明**。

以上是特定历史环境的非随机实战观察，不是本免费版的效果验证，也不能归因成每项功能各自的收益。详情与限制见 [真实效果](docs/RESULTS.md)。

## 交流、反馈与一起完善

欢迎玩家试用，也欢迎懂 Python、Lua、魔兽插件或性能分析的朋友一起完善。有效、无效、变慢、报错，都值得记录；我们希望留下可验证的结果，而不是只收集好评。

- **问题反馈、使用交流和建议：[进入 GitHub Issues](https://github.com/tala2010/WoWOptimizer/issues)。** 请尽量说明电脑配置、游戏版本、插件环境、操作步骤和实际表现。
- **Telegram：[t.me/beita2008](https://t.me/beita2008)**
- **微信：`talaliu2010`**（添加时请备注“魔兽优化交流”）。欢迎交流使用体验、优化思路和共同开发。
- **参与开发：** 可以先提 Issue 讨论，也可以按 [贡献指南](CONTRIBUTING.md) 提交 Pull Request。尚未解决的方向见 [待解决问题](docs/ROADMAP.md)。
- 上传截图、日志或配置前，请遮住账号、角色、聊天内容和其他个人信息；请勿公开提交密钥或完整私人配置。

## 原理与项目资料

- [架构与原理](docs/ARCHITECTURE.md)
- [公开范围与第三方说明](THIRD_PARTY_NOTICES.md)
- [已知问题与待解决问题](docs/ROADMAP.md)
- [贡献指南](CONTRIBUTING.md)
- [安全与隐私](SECURITY.md)
- [公开版整理记录](docs/PUBLIC_PREPARATION.md)

```powershell
python -m pip install -r requirements-dev.txt
python -B tools/verify_public_tree.py
python -B -m unittest discover -s tests -v
.\build.ps1
```

生成 `dist/WoWOptimizer.exe`。源码的 GPL 许可证、第三方说明和本说明应随二进制分发，并提供对应源码。现阶段属于社区预览，游戏更新后请先验证兼容性。没有将私人 Git 历史或生产授权后台放入本仓库。

## 许可证

本公开版本采用 **GPL-2.0-only**，全文见 [LICENSE](LICENSE)。包含的 WeakAuras 派生补丁保留其 GPLv2 条件。可以免费使用、学习、修改及按许可证再分发；GPL 也允许商业分发，本项目不额外添加禁止商用条款。
