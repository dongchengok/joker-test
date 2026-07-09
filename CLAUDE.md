# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目语言约定

- **所有交流用中文**（用户全局规则）。
- **使用 superpowers 技能** 处理任务（brainstorming → writing-plans → TDD → executing-plans）。
- **类应状态自洽**，减少类间依赖，避免网状依赖结构。

## 这是什么

`joker-test` 是面向游戏 QA 团队的 AI 驱动测试平台，核心理念 **"LLM 用得克制"**：
- 冒烟/回归（80%）：Python + pytest，无 LLM，CI 高频跑
- 探索式（15-20%）：LLM 生成 Charter + 三层引擎执行，找未知 bug

完整设计在 `DESIGN.md`（v0.8），动手前必读。

## 当前实现状态（Pre-Alpha，M0-M6 雏形已完成）

**已实现**：Charter 生成、冒烟链路（生成+执行+报告）、UI 探索器、LLM 探索器、M4 简化版探索引擎（战术+确定性 Judge）、插件系统、Reporter（Json/Html/Multi）、CLI（6 子命令：generate-charter/explore-ui/run-smoke/run-all/validate/record）、PerceptionEngine（OCR+图像匹配+LLM 识图）、LLM 多 provider（Mock/Bedrock/GLM/MiMo/Anthropic）、OCR（RapidOCR）、操作录制（flow 包：pynput 录制→LLM 起名→坐标语义化→生成 test_case）、全局 Tracer（流程跟踪，惰性初始化+atexit 自动收尾）。

**规划态**（未实现）：完整三层异步引擎（L1 60FPS/L2/L3）、Investigator、Meta-CoT Judge、MCP Server、AllureReporter、CoverageMapReporter。详见 `docs/roadmap/iteration-roadmap.md`。

## 关键外部依赖（footgun）

`charter_gen.py` **运行时硬依赖 SpecOps-src** 的 `converse.py` 和 `operate.py`（调用 AWS Bedrock）。
查找顺序（见 `charter_gen.py:33-45`）：
1. `<repo_root>/SpecOps-src`
2. `<repo_root>/../../SpecOps-src`（开发时上溯）
3. `$SPECOPS_SRC` 环境变量

找不到时直接 `raise ImportError`，错误信息已列出 3 种解法。规划 ADR-002 计划把 Bedrock 调用改成可选项，**修改此依赖关系前先看 DESIGN.md ADR-002**。

## 常用命令

```bash
# 安装（开发模式，含 dev 依赖）
pip install -e ".[dev]"

# 跑 charter 生成（必须先有 SpecOps-src + AWS 凭据）
python -m joker_test.charter_gen \
    examples/targets.json examples/game_metadata.json outputs/charters \
    --ids 1 --personas 破坏狂 贪婪者 --verbose

# 或装完包后
joker-test generate-charter <targets.json> <game_meta.json> <output_dir>

# 测试 / lint / 类型检查（dev extras）
pytest                       # testpaths = tests/
pytest tests/test_foo.py -k name   # 单测
ruff check src tests
mypy src

# Bedrock 凭据
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
export AWS_DEFAULT_REGION=us-east-1
```

注意：`tests/` 已建立（smoke/real/generated_smoke/，CI 用 FakeBackend）。Python 3.10+，Windows 优先（README 和 pyproject 都声明了）。

## 架构核心理念（不要违背）

1. **测试分层** —— Python 跑冒烟，LLM 跑探索。**不要把冒烟测试改成 LLM 驱动**（DESIGN.md 2.1）。
2. **LLM 克制使用** —— 只在生成/决策/验证的高价值点用。执行、感知、规则校验一律不用（DESIGN.md 2.2 表格）。
3. **插件扩展** —— 游戏特化逻辑（数据/规则/工具/Reporter）全部走 Python 插件类（Protocol），不走配置文件（ADR-003）。
4. **Backend 抽象** —— `ExecutorBackend` 接口，默认 `AirtestBackend`（图像识别为核心，引擎无关），`FakeBackend` CI 用（ADR-002，R-ADR-5 修订）。
5. **Reporter 抽象** —— 与 Backend 平行，已实现 Json/Html/Multi（`MultiReporter` 广播 + 错误隔离），Allure/CoverageMap 规划中（ADR-009）。
6. **CLI 统一契约，MCP 可选增强** —— 人/harness/orchestrator 都面向 CLI；MCP 降为可选（ADR-006，R-ADR-8 修订）。

## Charter 生成器（已实现，重点）

`src/joker_test/charter_gen.py` 流程：
1. 读 `targets.json` + `game_metadata.json`（强制 `encoding="utf-8"`，**Windows 默认 gbk 会炸**）
2. 按 `batch`（默认 2）切片 targets
3. 双 specialist 架构（同一 conversation）：
   - **Architect**（`reasoning=16000`）：生成 Charter 草稿
   - **Analyst**（`reasoning=32000`）：按 6 条 checklist 反思修订
4. `operate.converse_json` 提取结构化 Charter 数组
5. 每个 Charter 写独立 JSON 文件

### 关键契约

- **Persona**：5 种玩家人格（破坏狂/贪婪者/急躁鬼/完美主义/混乱中立），定义在 `DEFAULT_PERSONAS`，可被 `game_metadata.json["personas"]` 覆盖。
- **Heuristics**：QA 经验库，同上可覆盖。
- **文件名格式**：`T{tid:02d}_C{cid:02d}_{persona}_{system}.json`，会清洗 Windows 非法字符。
- **字段重命名陷阱**（`write_charter` 行 322-325）：写入文件时把 LLM 输出的 `charter_changes_game_state` 转成 `env_probing_required`（"yes"/"no"）。这是 Phase 1 → Phase 4 的契约，**不要改字段名**。
- **日志只在 `--verbose` 时启用**，写入 `<output_dir>/generation.log`。

## Charter JSON Schema（DESIGN.md 5.1）

```json
{
  "charter_id": int, "target_id": int,
  "persona": "破坏狂 | 贪婪者 | 急躁鬼 | 完美主义 | 混乱中立",
  "target_system": string, "target_description": string, "load_save": string,
  "goal": string,
  "exploration_targets": string[], "heuristics": string[], "expected_behaviors": string[],
  "coverage_dimensions": {"region": [], "function": [], "operation": [], "state": []},
  "time_budget_minutes": int,
  "env_probing_required": "yes | no",
  "severity_threshold": "P0 | P1 | P2"
}
```

## 代码组织

- `src/` layout（`pyproject.toml` 中 `[tool.setuptools.packages.find] where = ["src"]`）
- 包入口：`src/joker_test/__init__.py`（仅版本号）
- CLI 入口：`joker_test.cli:main`（已实现 6 子命令：generate-charter/explore-ui/run-smoke/run-all/validate/record；run-smoke/run-all/explore-ui 当前只接 FakeBackend，真游戏走 scripts/e2e_traced.py（设 `JOKER_BACKEND=airtest`））
- 全局 Tracer：默认开启（惰性建，atexit 自动写 `traces/<时间戳>_run/`）。`explore-ui`/`run-all`/`record` 支持 `--no-trace` 关闭。业务代码直接调 `trace_event`/`trace_stage`（模块级函数，零参数），不感知。详见 DESIGN.md §11.9。
- 示例数据：`examples/{targets,game_metadata}.json`（虚构 ARPG）
- 输出目录约定：`outputs/charters/`（gitignored）、`traces/`（trace 产物，gitignored）

## 工作约定

- 修改 charter 生成逻辑前，先看 DESIGN.md 第 4.1 节和 ADR 列表，确认没违背已采纳决策。
- 新增字段到 Charter schema 时，同步更新 DESIGN.md 5.1 和本文件。
- `BUG_DEFINITION` 和 `ANALYST_CHECKLIST` 常量（`charter_gen.py` 顶部）是 prompt 工程的核心，改动要谨慎。
- 用户全局规则要求 **类状态自洽**：实现新模块时优先让每个类自己持有需要的全部上下文，避免互相反向引用形成网状。
