# AGENTS.md

面向 AI coding agent 的工作指引。假设读者对本项目一无所知。深度架构信息见 `DESIGN.md`（v1.0，动手改架构前必读），模块实现细节见代码 docstring。

## 1. 项目概览

`joker-test`：面向游戏 QA 团队的 AI 驱动测试平台（Python 包，src/ layout，Apache-2.0）。核心理念 **"LLM 用得克制"**：

- **冒烟/回归（约 80%）**：纯 Python + pytest，CI 高频跑，不依赖 LLM。
- **探索式测试（约 15-20%）**：LLM 生成 Charter（探索章程）→ 探索流水线执行 → 固化为 pytest 用例。

**状态：Pre-Alpha**。已实现：Charter 生成、冒烟链路（生成+执行）、探索流水线（pipeline 包，AgenticOrchestrator 薄编排 5 个 Stage：探索→固化→执行→报告→反思）、UI 探索器（DFS）、LLM 探索器（agentic loop + 双策略 conversation/react_state）、AgentPlugin 插件系统（4 注入点 + PluginManager + OCRPlugin）、PerceptionEngine、Reporter（Json/Html/Multi/Explore）、操作录制（flow 包）、全局 Tracer、配置文件系统、CLI（7 子命令）。规划态：完整三层异步引擎 / Investigator / Meta-CoT Judge / MCP Server / AllureReporter。路线图见 `docs/roadmap/iteration-roadmap.md`。

借鉴 SpecOps（ICSE 2026）架构，但测试范式为 charter-driven + Python 冒烟双线（对比见 README / DESIGN.md §1.3）。

## 2. 技术栈

- **语言**：Python，requires-python `>=3.10`；开发 venv 用 3.12。
- **核心依赖**：pydantic v2、boto3（charter_gen 走 Bedrock）、pillow、Jinja2 + PyYAML（prompt 渲染）、tqdm。
- **可选 extras**（`pyproject.toml`）：
  - `dev`：pytest、mypy、ruff
  - `airtest`：airtest + pocoui + pynput + **numpy<2（必须 pin）**
  - `ocr`：rapidocr_onnxruntime
  - `opencv` / `report`（allure）
- **被测游戏（过渡）**：`.test-targets/SPD/`（Shattered Pixel Dungeon，窗口标题 "Shattered Pixel Dungeon"）。
- **平台**：主要面向 Windows（Airtest 走 Win32 `PrintWindow` 截图）。

## 3. 环境与常用命令

```bash
# ⚠️ 开发必须用仓库内 .venv（Python 3.12，已装 airtest+pocoui）。
# 全局 python 是 3.13，airtest 装不上（numpy<2 冲突）。
source .venv/Scripts/activate          # Git Bash；CMD/PS 用 .venv\Scripts\activate

pip install -e ".[dev]"                # 安装（含 dev 依赖）
pytest                                 # 跑全部测试（testpaths=tests）
pytest tests/test_xxx.py -k name       # 单测
ruff check src tests                   # lint
mypy src                               # 类型检查（非 strict）

# Charter 生成（需 SpecOps-src + AWS 凭据，见 §7 陷阱 1）
python -m joker_test.charter_gen \
    examples/targets.json examples/game_metadata.json outputs/charters \
    --ids 1 --personas 破坏狂 贪婪者 --verbose

# 启动过渡被测游戏
.test-targets/SPD/"Shattered Pixel Dungeon.exe"      # Windows
bash scripts/start_spd_mac.sh                        # macOS（窗口模式启动，详见 §7 陷阱 10）

# macOS 真机测试（需先启动 SPD + 授权终端"屏幕录制"+"辅助功能"）
JOKER_BACKEND=mac pytest tests/real/                 # 真机用例（Win 用 JOKER_BACKEND=airtest）
python scripts/e2e_spd_explore_conversation.py       # LLM 探索端到端（自动按平台选 backend）
```

入口点：`joker-test = joker_test.cli:main`（pyproject `[project.scripts]`）。

**工具链配置**（`pyproject.toml`）：

- pytest：`testpaths=["tests"]`，`addopts="-ra --strict-markers"`，**`python_classes=[]`（不收集 `Test*` 类）**——reporters 里有 `TestCase`/`TestResult` 等数据模型，写测试时不要新建 `Test*` 开头的类。
- ruff：line-length=100，target py310，select `E,F,W,I,B,UP`，ignore `E501`；`scripts/*` 豁免 `E402`。
- mypy：python_version=3.12（对齐 venv 实际版本），非 strict，ignore_missing_imports。ruff 守下限（py310）、mypy 守实际（3.12），两者有意不同。

## 4. 仓库结构与模块划分

```
src/joker_test/
  charter_gen.py               # Charter 生成（Phase 1，依赖 SpecOps-src 调 Bedrock）
  cli.py                       # CLI 入口（7 子命令 + --config，无参数默认 run-all）
  config.py                    # 配置文件加载（joker-test-config.json 不存在时自动生成）
  runner.py                    # pytest 执行 + 结果收集
  reflection.py                # 反思（卡死检测 + 误报审查，ReflectStage 复用）
  trace.py                     # 全局 Tracer（状态灯+统一可折叠时间线，仿 logging 模式）
  pipeline/                    # 探索流水线（AgenticOrchestrator + 5 Stage）
    types.py                   # 数据契约（ExploreConfig + 5 Result + PipelineResult + Risk）
    base.py                    # Stage Protocol + AgenticOrchestrator + build_orchestrator
    stages/                    # explore（智能入口+命中检查+三模式+插件注入）/ solidify / execute / report / reflect
  flow/                        # 操作录制+生成（pynput 监听→LLM 起名→语义化→test_case→试跑验证）
  llm/                         # LLM 抽象（base.py Protocol 对齐 anthropic SDK）
    providers/                 # anthropic/（读 .env 的 MIMO_API_KEY/MIMO_BASE_URL，兼容 MiMo/GLM 端点）+ mock/
  executor/                    # Backend 抽象（base.py Protocol + coords.py + window.py + 全局注册 set/get_active_backend + backends/ fake·airtest·mac·factory）
    backends/                  # airtest/（默认，图像识别为核心）+ fake/（CI 用）
  ocr/                         # OCR 抽象（base.py Protocol + providers/rapidocr/）
  perception/                  # 感知层（backend 无关）：OCR→match→LLM 三层漏斗
    matching/                  # image_matcher + _aircv/（vendored aircv 模板匹配）
  explorer/                    # 界面探索器（UIExplorer DFS + LLMExplorer agentic loop + 双策略 + StateMap）
  generator/                   # 用例生成（StateMap→LLM→pytest 代码 + Pydantic spec）
  reporters/                   # 报告（base.py Protocol + explore.py + json/ html/ multi/ 子包）
  prompts/                     # prompt 工程化（templates/*.md.j2 + data/*.yaml + constants/*.md，loader.py 统一加载）
  plugins/                     # 插件系统
    base.py                    # AgentPlugin Protocol（4 注入点）+ DefaultAgentPlugin
    manager.py                 # PluginManager（拼接注入内容 + 每次调用异常隔离）
    loader.py                  # 从 .py 文件路径动态加载单个外部插件（MVP）
    ocr/                       # OCRPlugin（内置，从 backend.state 提取文字+坐标）
    __init__.py                # BUILTIN_PLUGINS 注册表（内置插件名→类）
tests/                         # 单元测试（test_<模块>.py）+ smoke/ + generated_smoke/ + real/
examples/                      # 示例数据（targets/game_metadata）+ e2e_launch_quit/
scripts/                       # 端到端脚本（真 SPD，如 e2e_spd_explore_conversation.py）
docs/roadmap/                  # 迭代路线图
DESIGN.md                      # 架构权威文档（含 14 条 ADR + 工程规范 §11）
```

**CLI 7 子命令**（`joker_test.cli:main`，无参数等价 `run-all`）：

- `explore`：智能探索入口。`--mode manual|dfs|llm`、`--strategy conversation|react_state`、`--backend airtest|fake`、`--config joker-test-config.json`
- `generate-charter`：Charter 生成（走 SpecOps-src/Bedrock）
- `explore-ui`：DFS 探索产出界面地图
- `run-smoke`：跑冒烟 + 出报告
- `run-all`：一键编排（AgenticOrchestrator 5 Stage 流水线）
- `validate`：校验 charter JSON / testcase Python
- `record`：录制操作流程生成 test_case（`explore --mode manual` 的快捷方式）

`explore`/`run-all`/`explore-ui`/`record` 支持 `--no-trace` 关闭全局 trace（默认开启，atexit 自动收尾）。

**配置文件**：`joker-test-config.json` 首次运行自动在 cwd 生成（已 gitignore）。字段：`plugins`（内置插件名列表）、`plugin_path`（外部插件 .py）、`explore_strategy`、`max_steps`、`backend_name`、`window_title`、`llm.thinking`。

## 5. 架构红线（不要违背）

1. **测试分层**：Python 跑冒烟，LLM 跑探索。**不要把冒烟测试改成 LLM 驱动**（DESIGN §2.1）。
2. **LLM 克制**：只在生成/决策/验证的高价值点用；执行、感知、规则校验一律不用（§2.2）。
3. **插件扩展**：游戏特化逻辑走 `AgentPlugin` Python 类（4 注入点：系统提示词/每轮对话/动作建议/校验，ADR-003/013）。加新内置感知能力 = 写一个插件 + 在 `plugins/__init__.py` 的 `BUILTIN_PLUGINS` 注册一行，不改策略代码。
4. **Backend 抽象**：`ExecutorBackend` 接口，默认 `AirtestBackend`，`FakeBackend` 供 CI。全局注册（ADR-014，`set_active_backend`/`get_active_backend`）。跨分辨率自适应：`parse_coords` 从 `screenshot.shape` 提取尺寸，**不硬编码分辨率**。
5. **Reporter 抽象**：与 Backend 平行，`MultiReporter` 广播并做错误隔离（ADR-009）。
6. **CLI 是统一契约，MCP 可选**（ADR-006）：主集成方式 = CLI + AGENTS.md。
7. **编排可插拔**：独立模式编排走 AgenticOrchestrator（pipeline 包，薄编排 5 Stage）。
8. **感知层 backend 无关**：`perception/` 不依赖任何具体 backend，三层漏斗 OCR（文字）→ match（已知图标）→ LLM（语义兜底）。

## 6. 工程规范速查（完整版见 DESIGN.md §11，违规会被 review 打回）

1. **目录**（§11.1）：有抽象的包 = `__init__.py` + `base.py`（协议）+ 公共工具 + 每个实现一个子文件夹（如 `executor/backends/airtest/`）。数据驱动包（`prompts/`）不套用。
2. **命名**（§11.2）：类 PascalCase / 函数变量 snake_case / 常量 UPPER_SNAKE_CASE / 协议无 `I` 前缀 / 文件 snake_case。
3. **私有边界**（§11.3）：`_` 前缀唯一标准是"包外会不会直接 import"。禁止用 `_` 表达"不重要"。
4. **导出**（§11.4）：每个 `__init__.py` 用 `__all__` 显式声明；禁 `from x import *`。
5. **docstring**（§11.5）：中文 + Google 风格（Args/Returns/Raises）。模块/类/公共函数必须有。标杆：`executor/base.py`。
6. **类型**（§11.6）：公共 API 必须标注，文件顶部 `from __future__ import annotations`。重依赖用 `TYPE_CHECKING` 延迟导入。
7. **import**（§11.7）：分组 `__future__`→标准库→第三方→本项目→同包相对，组间空行。**重依赖（cv2/numpy/airtest）函数内懒导入**。
8. **错误处理**（§11.8）：自定义异常用 `Error` 后缀继承 `Exception`；禁裸 `except:`；跨层异常隔离降级。
9. **日志**（§11.9）：`_LOGGER = logging.getLogger(__name__)`；不在模块顶层 `basicConfig`；用户进度用 `print` 不用 logging。
10. **测试**（§11.10）：`test_<被测模块>.py`，真机测试加 `_real` 后缀放 `tests/real/`；CI 用 FakeBackend + MockProvider；数据模型类加 `__test__ = False`。

## 7. 关键陷阱（footgun）

1. **SpecOps-src 硬依赖（仅 `generate-charter`）**：`charter_gen.py` 运行时依赖外部 `SpecOps-src`（调 AWS Bedrock）。查找顺序：`<repo_root>/SpecOps-src` → 上级目录 → `$SPECOPS_SRC` 环境变量。**独立探索流水线（pipeline）不走 SpecOps-src**，用 AnthropicProvider（.env 配 `MIMO_API_KEY`/`MIMO_BASE_URL`，兼容 MiMo/GLM 端点）。
2. **Windows 编码**：所有 JSON 读写强制 `encoding="utf-8"`。Windows 默认 gbk 会炸，**不要去掉这个参数**。
3. **字段重命名契约**：`write_charter` 把 LLM 输出的 `charter_changes_game_state` 转写成 `env_probing_required`（"yes"/"no"）。这是 Phase 1 → Phase 4 的契约，**不要改字段名**。
4. **numpy<2 必须 pin**：`.venv` 里 airtest 的 cv2 基于 numpy 1.x ABI，numpy 升到 2.x 会让 cv2 立即 `ImportError`。（注：rapidocr 会拉 numpy>=2，实测 airtest 1.4.3 在 numpy 2.x 下可工作但属脆弱状态，见 pyproject `ocr` extra 注释。）
5. **airtest title_re 不加引号**：`connect_device("Windows:///?title_re=.*Shattered.*")` —— 正则不要加单引号。
6. **配置文件自动生成**：`explore`/`run-all` 首次运行会在 cwd 生成 `joker-test-config.json`（已 gitignore）。
7. **全局 backend 注册**：`set_active_backend()` 在程序入口调用一次（CLI/conftest/pipeline）。测试间注意重置。
8. **真机边界**：AirtestBackend 走 Win32 `PrintWindow`，窗口被完全遮挡/最小化时截图白屏/黑屏（G7）。
9. **pytest 不收集 `Test*` 类**（`python_classes=[]`）：测试一律写成 `test_*` 函数。
10. **macOS 真机（MacBackend）**：① 终端 App 需授权"屏幕录制"（截图）+"辅助功能"（CGEvent 输入），改完必须 ⌘Q 完全退出终端再重开；可用 `AXIsProcessTrusted()` 验证。② SPD 必须用 `scripts/start_spd_mac.sh` 启动：官方 .app 的 x86_64 launcher 会加载错误 natives 直接崩（glfwGetMonitorPos NPE），脚本用本地 arm64 JDK + 只保留 macos-arm64 natives；且 SPD 默认全屏会独占 Space 导致截图/点击投递不到，脚本会写 `fullscreen=false` 强制窗口模式。③ `post_click` 的 down/up 必须有间隔（libGDX 按帧轮询输入，瞬时点击会被丢）。④ 点击前要把游戏 App 置前（`activate_app` 对 java CLI 进程不可靠时可用 AppleScript System Events frontmost）。

## 8. 测试策略

- **CI 测试**：`tests/` 下 `test_<模块>.py`，用 `FakeBackend` + `MockProvider`，不碰真机、不碰真 LLM。
- **真机测试**：`tests/real/test_*_real.py`，需要被测游戏窗口在线，CI 不跑。
- **生成的冒烟用例**：`tests/generated_smoke/`（已 gitignore，是流水线产物而非手写测试）。
- **端到端脚本**：`scripts/`（如 `e2e_spd_explore_conversation.py`，真 SPD + ConversationStrategy），手动运行。
- 改动后至少跑：`pytest` + `ruff check src tests` + `mypy src`。

## 9. 安全与凭据

- `.env`（含 `MIMO_API_KEY` 等）已 gitignore，**绝不入库、不打印、不提交**。
- AWS 凭据（Bedrock，charter_gen 用）走环境变量 / boto3 默认链。
- `.claude/settings.local.json`、`.zcode/plans|sessions/`、`SpecOps-src/`、`.test-targets/` 均已 gitignore。
- 运行时产物目录（`outputs/` `reports/` `flows/` `traces/` `logs/` `screenshots/` 等）全部 gitignore，不要把这些目录下的文件当源码改。

## 10. 工作约定

- 改 Charter 生成逻辑前，先读 `DESIGN.md` §5.5 + §8 ADR 列表，确认不违背已采纳决策。
- prompt 工程核心文件改动要谨慎：`prompts/constants/bug_definition.md`、`prompts/constants/analyst_checklist.md`、`prompts/data/personas.yaml`（由 `prompts/loader.py` 统一加载）。
- 新增 Charter schema 字段时，同步更新 `DESIGN.md` §6.1 和 `CLAUDE.md`。
- **类应状态自洽**：新模块让每个类自持所需上下文，避免互相反向引用形成网状依赖。
- 追加架构决策 = 在 `DESIGN.md` §8 追加一条 ADR（现有 ADR-001 ~ ADR-014）。
- 交流一律用中文；代码注释/docstring 也用中文（项目既有约定）。
