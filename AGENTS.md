# AGENTS.md

面向 ZCode agent 的精简工作指引。深度信息见 `DESIGN.md`（v1.0，动手前必读）。

## 仓库是什么

`joker-test`：面向游戏 QA 的 AI 驱动测试平台。核心理念 **"LLM 用得克制"** —— 冒烟/回归（80%）用 Python+pytest，探索式（15-20%）用 LLM 生成 Charter + 引擎执行。

**状态：Pre-Alpha（M0-M6 + 探索流水线 + AgentPlugin 插件系统已完成）**。Charter 生成、冒烟链路（生成+执行）、探索流水线（pipeline 包：探索→固化→执行→报告→反思，AgenticOrchestrator 薄编排 5 个 Stage）、UI 探索器（DFS）、LLM 探索器（agentic loop + 双策略 conversation/react_state）、**AgentPlugin 插件系统**（4 注入点 + PluginManager + OCRPlugin + 配置文件）、Reporter（Json/Html/Multi/Explore）、CLI（7 子命令 + `--config` 配置文件）、PerceptionEngine、操作录制（flow 包）、全局 Tracer（状态灯+统一可折叠时间线）均已实现。完整三层异步引擎 / Investigator / Meta-CoT Judge / MCP Server / AllureReporter 为规划态。路线图见 `docs/roadmap/iteration-roadmap.md`。

## 仓库结构

```
src/joker_test/                # 全链路已实现
  charter_gen.py               # Charter 生成（Phase 1）
  cli.py                       # CLI 入口（7 子命令 + --config 配置文件 + 无参数默认 run-all）
  config.py                    # 配置文件加载（joker-test-config.json 自动生成）
  runner.py                    # pytest 执行 + 结果收集
  reflection.py                # 反思（卡死检测 + 误报审查，ReflectStage 复用）
  trace.py                     # 全局 Tracer（状态灯+统一可折叠时间线，仿 logging 模式）
  pipeline/                    # 探索流水线（AgenticOrchestrator + 5 Stage）
    types.py                   # 数据契约（ExploreConfig + 5 Result + PipelineResult + Risk）
    base.py                    # Stage Protocol + AgenticOrchestrator + build_orchestrator
    stages/                    # explore（智能入口+命中检查+三模式+插件注入）/ solidify / execute / report / reflect
  flow/                        # 操作录制+生成（pynput监听→LLM起名→语义化→test_case→试跑验证）
  llm/                         # LLM 抽象（Protocol 对齐 anthropic SDK + Anthropic + Mock）
  executor/                    # Backend 抽象（Protocol + 全局注册 set/get_active_backend + Fake/Airtest + coords）
  ocr/                         # OCR 抽象（Protocol + RapidOCR）
  explorer/                    # 界面探索器（UIExplorer DFS + LLMExplorer agentic loop + ExploreStrategy 双策略 + StateMap）
  generator/                   # 用例生成（StateMap→LLM→pytest代码+spec）
  reporters/                   # 报告（Protocol + Json/Html/Multi + ExploreReporter 综合探索报告）
  prompts/                     # prompt 工程化（Jinja2 + XML 标签 + md/yaml）
  plugins/                     # 插件系统（AgentPlugin 4注入点 + PluginManager + OCRPlugin + loader）
    base.py                    # AgentPlugin Protocol + DefaultAgentPlugin
    manager.py                 # PluginManager（拼接注入内容 + 异常隔离）
    ocr/                       # OCRPlugin（内置，从 backend.state 提取文字+坐标）
examples/e2e_launch_quit/      # 端到端示例（进入退出游戏场景）
scripts/e2e_spd_explore_conversation.py  # 真 SPD 端到端脚本（ConversationStrategy）
.test-targets/SPD/             # 过渡被测游戏（Shattered Pixel Dungeon）
DESIGN.md / AGENTS.md / docs/roadmap/    # 设计文档 + 工作指引 + 迭代路线
pyproject.toml                 # src/ layout，Python 3.12（venv）
```

注意：`tests/` 已建立（smoke/real/generated_smoke/pipeline，CI 用 FakeBackend + MockProvider）。CLI `joker_test.cli:main` 已实现 7 个子命令：`explore`（智能探索入口）/`generate-charter`/`explore-ui`/`run-smoke`/`run-all`（AgenticOrchestrator 流水线）/`validate`/`record`，无参数默认等价 `run-all`。`explore` 支持 `--mode manual|dfs|llm` 三种探索方式 + `--strategy conversation|react_state` 探索策略 + `--config joker-test-config.json` 配置文件（自动生成，含插件列表/后端/策略）。`explore`/`run-all`/`explore-ui`/`record` 支持 `--no-trace` 关闭全局 trace（默认开启，状态灯+统一可折叠时间线，详见 DESIGN.md §11.9）。

## 常用命令

```bash
# ⚠️ 开发环境用 .venv（Python 3.12，已装 airtest+pocoui）
# 全局 python 是 3.13，airtest 装不上（numpy<2 冲突），务必用 .venv
source .venv/Scripts/activate          # Git Bash；CMD/PS 用 .venv\Scripts\activate

pip install -e ".[dev]"                              # 安装（含 dev 依赖）
pytest                                               # 跑测试（testpaths=tests/）
pytest tests/test_xxx.py -k name                     # 单测
ruff check src tests                                 # lint（line-length=100，ignore E501）
mypy src                                             # 类型检查（非 strict）

# 跑 Charter 生成（需 SpecOps-src + AWS 凭据，见下方 footgun）
python -m joker_test.charter_gen \
    examples/targets.json examples/game_metadata.json outputs/charters \
    --ids 1 --personas 破坏狂 贪婪者 --verbose

# 启动过渡被测游戏（M1-M5 用）
.test-targets/SPD/"Shattered Pixel Dungeon.exe"      # 窗口标题 "Shattered Pixel Dungeon"
```

## ⚠️ 关键陷阱（footgun）

1. **SpecOps-src 硬依赖（charter_gen 专用）**：`charter_gen.py` 运行时依赖外部 `SpecOps-src`（调 AWS Bedrock 生成 Charter）。查找顺序（`charter_gen.py` 顶部约 33-45 行）：`<repo_root>/SpecOps-src` → `<repo_root>/../../SpecOps-src` → `$SPECOPS_SRC` 环境变量。**注意：独立探索流水线（pipeline）不走 SpecOps-src，用 AnthropicProvider（见 .env 配 MiMo/GLM 端点）。** 只有 `generate-charter` 子命令需要 SpecOps-src。
2. **Windows 编码**：所有 JSON 读写强制 `encoding="utf-8"`。Windows 默认 gbk 会炸，**不要去掉这个参数**。
3. **字段重命名契约**：`write_charter` 会把 LLM 输出的 `charter_changes_game_state` 转写成 `env_probing_required`（"yes"/"no"）。这是 Phase 1 → Phase 4 的契约，**不要改字段名**。
4. **日志**：只在 `--verbose` 时启用，写入 `<output_dir>/generation.log`。
5. **numpy<2 必须 pin**：`.venv` 里 airtest 的 cv2 基于 numpy 1.x ABI。装任何包时若 numpy 被升到 2.x，cv2 立即 `ImportError`。
6. **airtest title_re 不加引号**：`connect_device("Windows:///?title_re=.*Shattered.*")` —— 正则不要加单引号。
7. **配置文件自动生成**：首次运行 `explore`/`run-all` 会在 cwd 自动生成 `joker-test-config.json`。已加入 `.gitignore`。
8. **全局 backend 注册**：`set_active_backend()` 在程序入口调用一次（CLI/conftest/pipeline）。`parse_coords` 无截图时从全局 backend 获取帧尺寸。测试间注意重置。

## 架构红线（不要违背）

1. **测试分层**：Python 跑冒烟，LLM 跑探索。**不要把冒烟测试改成 LLM 驱动**（`DESIGN.md` 2.1）。
2. **LLM 克制**：只在生成/决策/验证的高价值点用；执行、感知、规则校验一律不用（`DESIGN.md` 2.2）。
3. **插件扩展**：游戏特化逻辑（数据/规则/工具）走 `AgentPlugin` Python 类（4 注入点，ADR-003/013），通过配置文件选择激活插件列表。
4. **Backend 抽象**：`ExecutorBackend` 接口，**默认 `AirtestBackend`（图像识别为核心，引擎无关）**，Poco 为 Unity 可选增强。全局注册（ADR-014，`set_active_backend`/`get_active_backend`），跨机器/跨游戏自适应（`parse_coords` 从 `screenshot.shape` 提取尺寸，不硬编码）。
5. **Reporter 抽象**：与 Backend 平行，`MultiReporter` 广播并做错误隔离（ADR-009）。
6. **CLI 是统一契约，MCP 可选**：编排者都面向同一套 CLI；主集成方式 = CLI + AGENTS.md，MCP 降为可选增强（ADR-006）。
7. **编排策略可插拔**：独立模式编排走 AgenticOrchestrator（pipeline 包，薄编排 5 个 Stage：探索→固化→执行→报告→反思）。
8. **感知层 backend 无关**：`perception/` 包是跨 backend 的感知层，**不依赖任何具体 backend**。三层漏斗：OCR（文字）→ match（已知图标）→ LLM（语义兜底）。
9. **AgentPlugin 注入点**：插件通过 4 个注入点（系统提示词/每轮对话/动作建议/校验）向探索流程提供信息。`PluginManager` 负责拼接+异常隔离。加新感知能力只需写一个插件 + 注册一行（`BUILTIN_PLUGINS`），不改策略代码（ADR-013）。

## 工程规范速查（详见 DESIGN.md §11）

动手写代码前必看，违规会被 review 打回。完整规范见 `DESIGN.md` §11.1-11.10。

1. **目录**（§11.1）：有抽象的包 = `__init__.py` + `base.py`（协议）+ `<utils>.py`（公共工具）+ `<impl>/`（每个实现一个子文件夹）。数据驱动包（如 `prompts/`）不套用。
2. **命名**（§11.2）：类 PascalCase / 函数变量方法 snake_case / 常量 UPPER_SNAKE_CASE / 协议无 `I` 前缀 / 文件 snake_case 默认无前缀。
3. **私有边界**（§11.3）：`_` 前缀**唯一标准**是"包外会不会直接 import"。会 → 无前缀；不会 → `_`。禁止用 `_` 表达"不重要"。
4. **导出**（§11.4）：每个 `__init__.py` 用 `__all__` 显式声明，`_` 前缀不导出。**禁 `from x import *`**。
5. **docstring**（§11.5）：中文 + Google 风格（Args/Returns/Raises）。模块/类/公共函数必须有，私有可简化。标杆：`executor/base.py`。
6. **类型**（§11.6）：公共 API 必须标注，顶部加 `from __future__ import annotations`。重依赖用 `TYPE_CHECKING` 延迟导入。
7. **import**（§11.7）：分组 `__future__`→标准库→第三方→本项目→同包相对，组间空行。**重依赖（cv2/numpy/airtest）函数内懒导入**。
8. **错误处理**（§11.8）：自定义异常用 `Error` 后缀继承 `Exception`。禁裸 `except:`。跨层用异常隔离降级。
9. **日志**（§11.9）：`_LOGGER = logging.getLogger(__name__)`。不在模块顶层配 `basicConfig`。用户进度用 `print` 不用 logging。
10. **测试**（§11.10）：`test_<被测模块>.py`，真机加 `_real` 后缀放 `tests/real/`。CI 用 Fake+Mock。数据模型加 `__test__ = False`。

## 工作约定

- 改 Charter 生成逻辑前，先读 `DESIGN.md` §5.5 + ADR 列表，确认不违背已采纳决策。
- `BUG_DEFINITION`、`ANALYST_CHECKLIST`、`DEFAULT_PERSONAS`（`charter_gen.py` 顶部常量）是 prompt 工程核心，改动要谨慎。
- 新增 Charter schema 字段时，同步更新 `DESIGN.md` §6.1 和 `CLAUDE.md`。
- **类应状态自洽**：实现新模块时让每个类自持所需上下文，避免互相反向引用形成网状依赖。
- 交流一律用中文。
