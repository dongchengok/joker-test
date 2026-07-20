# macOS 兼容设计：MacBackend + SPD 脚本平台化

日期：2026-07-20
状态：已批准（用户确认）

## 背景与目标

joker-test 的 SPD 端到端链路当前是 Windows-only：

- `AirtestBackend` 用 airtest 的 `connect_device("Windows:///?title_re=...")` 连接游戏窗口（`src/joker_test/executor/backends/airtest/backend.py:72`）。
- 已查证 airtest 1.4.3 源码：设备模块只有 `android/`、`ios/`、`linux/`（X11）、`win/`，**无 macOS 桌面设备**，无法在现有实现上加平台分支。
- `scripts/` 下 7 个 e2e 脚本中，6 个直连真游戏：`e2e_spd_real.py`、`e2e_spd_full_real.py`、`e2e_formal.py`、`e2e_traced.py`、`verify_spd_record_e2e.py` 直接 `import win32gui` 等待游戏窗口；这 5 个加上 `e2e_spd_explore_conversation.py` 共 6 个脚本**硬编码实例化 `AirtestBackend`**。第 7 个 `e2e_launch_quit.py` 走 FakeBackend 纯模拟，本就跨平台，不用改。

目标：在 macOS（Apple Silicon）上跑**真实 SPD 端到端测试**，同时保持 Windows 侧行为完全不变。

## 决策记录

| 决策点 | 结论 | 备选（否决原因） |
|---|---|---|
| 游戏本体 | 官方 `ShatteredPD-v3.3.8-macOS.zip` + Homebrew Temurin JRE | 换被测应用（就不是 SPD 端到端了） |
| Mac 执行后端 | 新增 MacBackend（pyobjc Quartz + CGEvent） | 安卓模拟器（链路过重）；mss 全屏截图（窗口定位弱） |
| 改动范围 | 6 个 SPD 脚本全部平台化 + `JOKER_BACKEND=mac` | 只改主链路（留一堆 Windows-only 脚本） |

## 架构

不动 `ExecutorBackend` 协议、不动 AirtestBackend、不动 Windows 侧任何行为。新增一个 macOS 原生 backend 实现同一协议；脚本里的 Windows API 调用抽成跨平台工具函数；backend 选择走现有 `JOKER_BACKEND` 环境变量，新增 `mac` 档位。

## 组件设计

### 1. MacBackend（`src/joker_test/executor/backends/mac/`）

按工程规范 §11.1：`__init__.py` + `backend.py` + `state.py`（state 参考 airtest 包的 `_AirtestState`，OCR 文本提取逻辑同构）。

- **窗口枚举**：`pyobjc-framework-Quartz` 的 `CGWindowListCopyWindowInfo`，按窗口标题子串匹配，拿 `kCGWindowNumber`（windowID）+ `kCGWindowBounds`。
- **截图**：`CGWindowListCreateImage` 按 windowID 截窗口 → numpy BGR ndarray。无子进程开销；窗口被部分遮挡时仍能截（比 airtest Windows 的 G7 后台限制宽松）。
- **输入**：`CGEventCreateMouseEvent` / `CGEventCreateKeyboardEvent` + `CGEventPost(kCGHIDEventTap)`。不引入 pyautogui，坐标系与截图同源（Quartz point）。键盘只映射常用键（escape/enter/方向键/字母数字）。
- **坐标契约**：对外全部归一化 [0,1]，基准 = `screenshot()` 图像尺寸（与协议一致）。**Retina 换算在内部消化**：Quartz bounds 是 point，截图输出是 pixel（Retina 2x）；connect 时实测 `scale = 截图宽 / bounds宽` 并缓存，click 时归一化坐标 → 截图像素 → 除以 scale 换回 point。
- **click_image**：cv2 `matchTemplate` 简单实现（mac 无 airtest Template 可复用）：screenshot + 模板匹配 + 点中心。
- **wait_until / state**：与 AirtestBackend 同构——轮询前自动 screenshot 刷帧、新帧 invalidate state 缓存。
- **type_text**：初版留 `NotImplementedError`（与 M1 的 AirtestBackend 对齐，协议允许渐进实现）。
- **connect 健康检测**：复用 `analyze_screenshot`；截图为空/纯黑时给明确报错，提示去系统设置授权"屏幕录制"。

### 2. 跨平台窗口等待（`src/joker_test/executor/window.py`）

新增公共工具：

```python
def wait_for_window(title_substr: str, timeout: float = 10.0, interval: float = 1.0) -> bool
```

按 `sys.platform` 分派：darwin → Quartz 枚举；win32 → win32gui。两边均**函数内懒导入**，互不污染依赖。5 个含 win32gui 的脚本的等待窗口轮询统一替换为调用它，脚本主体逻辑不变。

### 3. 平台分发工厂（`src/joker_test/executor/backends/factory.py`）

6 个直连真游戏的脚本目前硬编码 `AirtestBackend(...)`。新增工厂函数：

```python
def create_native_backend(window_title: str, ocr: OCRProvider | None = None) -> ExecutorBackend
```

按 `sys.platform` 分派：win32 → AirtestBackend；darwin → MacBackend；其他平台抛 `RuntimeError`（明确提示不支持）。两个 backend 类同样函数内懒导入。6 个脚本把 `AirtestBackend(window_title=..., ocr=...)` 统一替换为 `create_native_backend(...)`，脚本不再关心平台。conftest 的 `airtest` 分支保留原样（显式指定），另加 `mac` 分支。

### 4. 接线

- `tests/conftest.py`：`JOKER_BACKEND=mac` → `MacBackend(window_title="Shattered", ocr=RapidOCRProvider())`。`tests/real/test_launch_quit_real.py` 现有用例直接复用（断言基于 OCR 文本，平台无关），skipif 条件放宽为 `JOKER_BACKEND in {airtest, mac}`。
- `pyproject.toml` 新增 `mac` extra：`pyobjc-framework-Quartz>=10.0; sys_platform=='darwin'`。
- LLM 配置走现有 `.env`（`MIMO_*` 键），本次已配 Kimi k3（`https://api.kimi.com/coding`），不动代码。

### 5. 游戏环境准备（一次性，不入库）

- `brew install --cask temurin`（arm64 JRE；libGDX/lwjgl3 支持 arm64 mac。若启动失败，降级方案：Rosetta + x64 JRE）。
- 下载 `ShatteredPD-v3.3.8-macOS.zip`（GitHub `00-Evan/shattered-pixel-dungeon` releases）解压到 `.test-targets/SPD-mac/`（已 gitignore），`java -jar` 启动，窗口标题 "Shattered Pixel Dungeon"。

### 6. macOS 权限（操作前提）

运行终端的 App（Terminal/iTerm）需授权：

- **屏幕录制**：截取其他 App 窗口的前提（macOS 10.15+）。
- **辅助功能**：`CGEventPost` 模拟鼠标键盘输入的前提。

系统设置 → 隐私与安全性，授权后需重启终端生效。写入 AGENTS.md footgun 与 README。

## 数据流

```
SPD(macOS java) ──Quartz 截图──> MacBackend.screenshot() ──BGR ndarray──> OCR/感知层
                                        │
LLM 决策 ──归一化坐标──> MacBackend.click() ──÷scale 换 point──> CGEventPost ──> SPD
```

## 错误处理

- 窗口未找到：`connect()` 抛带明确信息的 `RuntimeError`（提示游戏未启动/标题不匹配）。
- 未授权屏幕录制：截图全黑/空 → connect 健康检测报"请授权屏幕录制"。
- 未授权辅助功能：`CGEventPost` 静默无效的已知 macOS 行为 → connect 时发一次测试事件不可行，改为在文档和 click 首次调用时的 warning 中提示。
- pyobjc 未安装：导入兜底，提示 `pip install -e .[mac]`（仿 AirtestBackend 的 ImportError 风格）。

## 测试策略

- `tests/test_backends_mac.py`：向 `sys.modules` 注入假 Quartz 模块，单测窗口匹配、Retina 坐标换算、click 事件构造、窗口未找到报错。CI 可跑（不依赖真机真窗）。
- 真机验证（本机手动/按需）：
  - `JOKER_BACKEND=mac pytest tests/real/`
  - `python scripts/e2e_spd_explore_conversation.py`（Kimi k3 + ConversationStrategy 全链路）
- 回归：`pytest`（CI 默认 fake，229 个现有用例不受影响）。

## 风险与对策

| 风险 | 对策 |
|---|---|
| Retina 2x 坐标换算错误 | connect 时实测 scale 而非读 NSScreen 理论值；单测覆盖 |
| Apple Silicon 上 lwjgl3  natives 不兼容 | Temurin arm64 优先；失败则 Rosetta + x64 JRE |
| 未授权权限导致截图黑/点击无效 | connect 健康检测 + 明确报错文案 + 文档 footgun |
| Java 版字体渲染与 Windows 版差异导致 OCR 文本不同 | 属预期差异；tests/real 断言只挑稳定文本（SHATTERED/进入地牢） |

## 明确不做（YAGNI）

- 不改 Windows 侧任何行为。
- 不做 Linux backend。
- 不动 `ExecutorBackend` 协议（协议已支持渐进实现）。
- `type_text` 初版不实现。
- 不做游戏自动启动/停止（脚本假设游戏已启动，与现有约定一致）。
