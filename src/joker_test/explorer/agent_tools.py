"""Agent 工具层：感知与执行全拆成独立工具（完整 AI agent 做法）。

对比 ConversationStrategy（每步自动注入截图+OCR、单一 execute_action 工具输出一个动作）：
本模块把感知（get_screenshot/get_ocr_text）与执行（click/swipe/...）拆成 9 个独立工具，
LLM 在 agent loop 里按需索取信息、单轮可多个 tool_use。AgentStrategy 驱动 loop，
本模块只负责工具 schema 定义与分发执行。

设计要点：
- schema 为 Anthropic 格式（name/description/input_schema），直接传给 LLMProvider.create(tools=)
- 动作类工具执行前后各截一张图算 pixel_diff（阈值 0.005，与 LLMExplorer 一致）
- 异常不抛出：统一包进 {"success": false, "error": ...} 的 tool_result 文本
- finish 由策略处理（结束探索是决策不是 backend 动作），executor 不执行
"""

from __future__ import annotations

import base64
import json
import logging
from typing import TYPE_CHECKING, Any

from joker_test.explorer.strategy import parse_coords

if TYPE_CHECKING:
    from joker_test.executor.base import ExecutorBackend
    from joker_test.flow.recorder import GlobalRecorder

_LOGGER = logging.getLogger(__name__)

_SCREEN_CHANGE_THRESHOLD = 0.005  # 与 llm_explorer._SCREEN_CHANGE_THRESHOLD 一致
_WAIT_AFTER_ACTION = 1.0  # 动作后等界面稳定（秒），与 llm_explorer._WAIT_AFTER_ACTION 一致
_MAX_OCR_ITEMS = 30
_MAX_OCR_AFTER = 10

# Anthropic 格式的工具 schema（9 个：2 感知 + 6 动作 + finish）
AGENT_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "get_screenshot",
        "description": (
            "获取界面截图（返回图片）。可选 region 参数只截取归一化矩形区域 "
            "(x,y,w,h)，用于放大查看小图标/局部区域；不传则返回全屏。"
            "注意：无论是否裁剪，后续操作的坐标始终是相对全屏的归一化坐标。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "x": {"type": "number", "description": "区域左上角归一化 x（可选）"},
                "y": {"type": "number", "description": "区域左上角归一化 y（可选）"},
                "w": {"type": "number", "description": "区域归一化宽（可选）"},
                "h": {"type": "number", "description": "区域归一化高（可选）"},
            },
        },
    },
    {
        "name": "get_ocr_text",
        "description": (
            "获取当前界面的 OCR 文字及中心坐标，JSON 数组 [{\"text\",\"x\",\"y\"}]，"
            "x/y 为归一化坐标 [0,1]。需要读文字、找按钮位置时调用。"
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "click",
        "description": "在归一化坐标 (x, y) 处单击。纯图标/无文字元素用这个；有文字的按钮优先 click_text。",
        "input_schema": {
            "type": "object",
            "properties": {
                "x": {"type": "number", "description": "归一化横坐标 [0,1]（左0右1）"},
                "y": {"type": "number", "description": "归一化纵坐标 [0,1]（上0下1）"},
            },
            "required": ["x", "y"],
        },
    },
    {
        "name": "click_text",
        "description": "按文字点击按钮（文字来自 get_ocr_text 的结果）。有文字的按钮优先用这个。",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "要点击的文字"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "press_key",
        "description": "按键（如 escape/enter/字母键）。",
        "input_schema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "键名"},
            },
            "required": ["key"],
        },
    },
    {
        "name": "swipe",
        "description": (
            "从 (x1,y1) 拖到 (x2,y2)（归一化坐标）。滑块操作用这个，"
            "例：音量滑块从 10 拖到 5 = 从滑块当前位置向左拖一半（x 减小约滑块长度的一半）。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "x1": {"type": "number", "description": "起点归一化横坐标 [0,1]"},
                "y1": {"type": "number", "description": "起点归一化纵坐标 [0,1]"},
                "x2": {"type": "number", "description": "终点归一化横坐标 [0,1]"},
                "y2": {"type": "number", "description": "终点归一化纵坐标 [0,1]"},
            },
            "required": ["x1", "y1", "x2", "y2"],
        },
    },
    {
        "name": "long_press",
        "description": "在归一化坐标 (x, y) 处长按（默认 2 秒）。",
        "input_schema": {
            "type": "object",
            "properties": {
                "x": {"type": "number", "description": "归一化横坐标 [0,1]"},
                "y": {"type": "number", "description": "归一化纵坐标 [0,1]"},
                "duration": {"type": "number", "description": "长按秒数，默认 2.0"},
            },
            "required": ["x", "y"],
        },
    },
    {
        "name": "match_icon",
        "description": (
            "模板匹配定位图标并点击（无文字小图标的首选，比估算坐标点 click 准得多）。"
            "用法：先用 get_screenshot(region) 放大看清图标，然后给出区域 (x,y,w,h)"
            "（全屏归一化）和图标在区域内的相对位置 (px,py)。工具以 (px,py) 为中心"
            "裁小块在全屏做模板匹配，用匹配峰值的精确坐标点击。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "x": {"type": "number", "description": "区域左上角归一化 x"},
                "y": {"type": "number", "description": "区域左上角归一化 y"},
                "w": {"type": "number", "description": "区域归一化宽"},
                "h": {"type": "number", "description": "区域归一化高"},
                "px": {"type": "number", "description": "图标在区域内的相对横坐标 [0,1]"},
                "py": {"type": "number", "description": "图标在区域内的相对纵坐标 [0,1]"},
            },
            "required": ["x", "y", "w", "h", "px", "py"],
        },
    },
    {
        "name": "back",
        "description": (
            "返回上级界面（按 escape）。警告：桌面游戏在标题/选角等前置界面按 escape "
            "可能直接退出游戏进程，返回上级优先点击界面内的返回按钮，慎用本工具。"
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "inspect_region",
        "description": (
            "检测指定区域内有什么（证伪工具）：返回区域内检测到的可疑组件"
            "（图标/按钮量级）的中心坐标列表 + OCR 文字。怀疑某处有按钮时先"
            "用它确认——返回空列表就是那里什么都没有，不要凭想象点击。"
            "不传区域参数则检测全屏。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "x": {"type": "number", "description": "区域左上角归一化 x（可选）"},
                "y": {"type": "number", "description": "区域左上角归一化 y（可选）"},
                "w": {"type": "number", "description": "区域归一化宽（可选）"},
                "h": {"type": "number", "description": "区域归一化高（可选）"},
            },
        },
    },
    {
        "name": "finish",
        "description": "结束探索。目标完成（goal_completed=true）或确认无法继续时调用。",
        "input_schema": {
            "type": "object",
            "properties": {
                "goal_completed": {"type": "boolean", "description": "目标是否已完成"},
                "summary": {"type": "string", "description": "探索总结（做了什么、结果如何）"},
            },
            "required": ["goal_completed", "summary"],
        },
    },
]

# 动作类工具（执行前后截图算 diff + 录制）；感知类与 finish 不在此列
_ACTION_TOOLS = {"click", "click_text", "press_key", "swipe", "long_press", "back", "match_icon"}


def _draw_grid(
    img: Any,
    origin_x: float = 0.0,
    origin_y: float = 0.0,
    span_x: float = 1.0,
    span_y: float = 1.0,
) -> Any:
    """在截图上叠加 0.1 间隔归一化坐标网格（提升 LLM 视觉定位精度）。

    浅色细线 + 边缘坐标标注。标注值 = origin + frac × span（全屏归一化坐标），
    region 裁剪图传对应的 origin/span，保证标注始终是全屏坐标而非图内相对值。

    Args:
        img: BGR ndarray 截图
        origin_x/origin_y: 图像左上角对应的全屏归一化坐标
        span_x/span_y: 图像宽高对应的全屏归一化跨度

    Returns:
        叠加网格后的图像（原地修改并返回同一对象）。
    """
    import cv2  # noqa: PLC0415

    h, w = img.shape[:2]
    color = (0, 255, 255)  # 黄色，深浅背景上都可读
    for i in range(1, 10):
        frac = i / 10
        x = round(frac * w)
        y = round(frac * h)
        cv2.line(img, (x, 0), (x, h), color, 1)
        cv2.line(img, (0, y), (w, y), color, 1)
        label_x = f"{origin_x + frac * span_x:.2f}"
        label_y = f"{origin_y + frac * span_y:.2f}"
        cv2.putText(img, label_x, (x + 2, 14), cv2.FONT_HERSHEY_SIMPLEX, 0.35, color, 1)
        cv2.putText(img, label_y, (2, y - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.35, color, 1)
    return img


def _find_blobs(
    frame: Any,
    min_area_frac: float = 0.001,
    max_area_frac: float = 0.05,
) -> list[tuple[float, float, float]]:
    """全帧轮廓检测，返回图标量级组件列表 [(中心x, 中心y, 面积px)]（归一化坐标）。

    Canny 边缘 → 外轮廓 → 面积过滤。inspect_region / click 吸附共用。
    """
    import cv2  # noqa: PLC0415

    fh, fw = frame.shape[:2]
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 50, 150)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    blobs: list[tuple[float, float, float]] = []
    for c in contours:
        area = cv2.contourArea(c)
        if area < min_area_frac * fw * fh or area > max_area_frac * fw * fh:
            continue
        m = cv2.moments(c)
        if m["m00"] == 0:
            continue
        blobs.append((m["m10"] / m["m00"] / fw, m["m01"] / m["m00"] / fh, area))
    return blobs


class AgentToolExecutor:
    """按工具名分发执行到 backend。

    Attributes:
        _backend: 游戏操作后端
        _recorder: 操作录制器（None = 不录制）
    """

    def __init__(self, backend: ExecutorBackend, recorder: GlobalRecorder | None = None) -> None:
        self._backend = backend
        self._recorder = recorder

    def execute(self, name: str, inp: dict[str, Any]) -> dict[str, Any]:
        """执行一个工具调用。

        Args:
            name: 工具名（AGENT_TOOL_SCHEMAS 之一）
            inp: 工具输入参数

        Returns:
            {"tool_result_content": [blocks...], "executed_action": dict | None}。
            executed_action 仅动作类工具非 None（{"tool","success","screen_changed"}），
            供策略做卡死（stale）统计。异常不抛出，包进结果文本。
        """
        try:
            if name == "get_screenshot":
                return {
                    "tool_result_content": self._screenshot_blocks(inp),
                    "executed_action": None,
                }
            if name == "get_ocr_text":
                text = json.dumps(self._ocr_items(), ensure_ascii=False)
                return {
                    "tool_result_content": [{"type": "text", "text": text}],
                    "executed_action": None,
                }
            if name == "inspect_region":
                text = json.dumps(self._inspect_region(inp), ensure_ascii=False)
                return {
                    "tool_result_content": [{"type": "text", "text": text}],
                    "executed_action": None,
                }
            return self._execute_action(name, inp)
        except Exception as e:  # noqa: BLE001
            _LOGGER.warning("工具执行失败 %s: %s", name, e, exc_info=True)
            return {
                "tool_result_content": [{"type": "text", "text": self._result_json(False, error=str(e))}],
                "executed_action": {"tool": name, "success": False, "screen_changed": False},
            }

    # ===== 感知工具 =====

    def _screenshot_blocks(self, inp: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """当前截图 → text 简述 + image 块（base64 png，长边压到 1536 内）。

        可选归一化 region (x,y,w,h) 裁剪局部区域（放大查看小图标用）；
        坐标说明里注明裁剪区域，提醒后续操作坐标仍相对全屏。
        图像叠加 0.1 间隔归一化坐标网格，辅助 LLM 视觉定位。

        分辨率取舍：过大（Retina 2880+）会被模型视觉管线二次压缩且费 token；
        过小（≤1024 实测）会把游戏小图标压到模型无法定位的尺寸。
        """
        import cv2  # noqa: PLC0415

        shot = self._backend.screenshot()
        region_desc = ""
        gx, gy, gw, gh = 0.0, 0.0, 1.0, 1.0  # 图像左上角全屏坐标 + 跨度（网格标注用）
        if inp:
            rx, ry = inp.get("x"), inp.get("y")
            rw, rh = inp.get("w"), inp.get("h")
            if None not in (rx, ry, rw, rh):
                fh, fw = shot.shape[:2]
                x1 = max(int(float(rx) * fw), 0)
                y1 = max(int(float(ry) * fh), 0)
                x2 = min(x1 + int(float(rw) * fw), fw)
                y2 = min(y1 + int(float(rh) * fh), fh)
                if x2 > x1 and y2 > y1:
                    shot = shot[y1:y2, x1:x2]
                    gx, gy = float(rx), float(ry)
                    gw, gh = float(rw), float(rh)
                    region_desc = (
                        f"（已裁剪到区域 x={rx},y={ry},w={rw},h={rh}；"
                        "网格标注与后续操作坐标均相对全屏归一化）"
                    )
        h, w = shot.shape[:2]
        long_side = max(h, w)
        # 长边上限 1536：再小会把 ~80px 的游戏图标压到模型无法定位的尺寸；
        # 再大则 token 成本陡增且模型视觉管线仍会二次压缩
        if long_side > 1536:
            scale = 1536 / long_side
            shot = cv2.resize(shot, (round(w * scale), round(h * scale)))
        shot = _draw_grid(shot, gx, gy, gw, gh)
        sh, sw = shot.shape[:2]
        _, buf = cv2.imencode(".png", shot)
        b64 = base64.b64encode(buf.tobytes()).decode("ascii")
        return [
            {
                "type": "text",
                "text": (
                    f"当前界面截图（{sw}x{sh} 像素{region_desc}，图上有 0.1 间隔"
                    "归一化坐标网格，点击/滑动请用归一化 [0,1] 坐标）"
                ),
            },
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": b64},
            },
        ]

    def _ocr_items(self) -> list[dict[str, Any]]:
        """当前帧 OCR 文字 + 中心坐标（归一化，最多 _MAX_OCR_ITEMS 条）。"""
        state = self._backend.state
        items: list[dict[str, Any]] = []
        for text in state.texts[:_MAX_OCR_ITEMS]:
            bbox = state.find_text(text)
            if bbox is not None:
                items.append({
                    "text": text,
                    "x": round(bbox.x + bbox.w / 2, 3),
                    "y": round(bbox.y + bbox.h / 2, 3),
                })
            else:
                items.append({"text": text, "x": None, "y": None})
        return items

    def _inspect_region(self, inp: dict[str, Any]) -> dict[str, Any]:
        """inspect_region 实现：区域内 CV 组件检测 + OCR 文字汇总（证伪工具）。

        Returns:
            {"components": [{x,y,area}], "texts": [...], "note": str}。
            components 为空即"该区域没有可疑组件"（幻觉按钮的证伪证据）。
        """
        frame = self._backend.screenshot()
        fh, fw = frame.shape[:2]
        rx, ry = inp.get("x"), inp.get("y")
        rw, rh = inp.get("w"), inp.get("h")
        if None in (rx, ry, rw, rh):
            x1, y1, x2, y2 = 0, 0, fw, fh
        else:
            x1 = max(int(float(rx) * fw), 0)
            y1 = max(int(float(ry) * fh), 0)
            x2 = min(x1 + int(float(rw) * fw), fw)
            y2 = min(y1 + int(float(rh) * fh), fh)
        blobs = _find_blobs(frame)
        # 组件中心落在区域内的才算；坐标转全屏归一化
        components = [
            {"x": round(cx, 3), "y": round(cy, 3), "area": round(area / (fw * fh), 4)}
            for cx, cy, area in blobs
            if x1 <= cx * fw <= x2 and y1 <= cy * fh <= y2
        ]
        components.sort(key=lambda c: -c["area"])
        texts = [
            item["text"]
            for item in self._ocr_items()
            if item["x"] is not None
            and x1 <= item["x"] * fw <= x2
            and y1 <= item["y"] * fh <= y2
        ]
        note = (
            f"区域内检测到 {len(components)} 个可疑组件、{len(texts)} 段文字"
            if components or texts
            else "该区域未检测到可疑组件或文字（不要凭想象点击这里）"
        )
        return {"components": components[:10], "texts": texts[:10], "note": note}

    # ===== 动作工具 =====

    def _execute_action(self, name: str, inp: dict[str, Any]) -> dict[str, Any]:
        """执行动作类工具：前后截图算 diff + 录制 + 返回结构化结果。

        click/match_icon 未产生界面变化时，在结果里附一张红叉标记实际点击
        位置的截图——让模型看到自己点到了哪里，闭环修正坐标。
        """
        if name not in _ACTION_TOOLS:
            raise ValueError(f"未知工具: {name}")

        before = self._backend.screenshot()
        self._extra: dict[str, Any] = {}
        self._mark_point: tuple[float, float] | None = None
        success = True
        error: str | None = None
        try:
            error = self._dispatch(name, inp, before)
            success = error is None
        except Exception as e:  # noqa: BLE001
            success = False
            error = str(e)

        if success:
            self._backend.wait_until(lambda: True, timeout=_WAIT_AFTER_ACTION)
        after = self._backend.screenshot()
        changed, ratio = self._detect_change(before, after)

        if success:
            self._record(name, inp)

        ocr_after = self._texts_after()
        result_text = self._result_json(
            success, changed=changed, ratio=ratio, error=error,
            ocr_after=ocr_after, extra=self._extra,
        )
        blocks: list[dict[str, Any]] = [{"type": "text", "text": result_text}]
        if (
            name in ("click", "match_icon")
            and success
            and not changed
            and self._mark_point is not None
        ):
            blocks.append({
                "type": "text",
                "text": "未检测到界面变化。下图红叉为实际点击位置，请对照目标修正坐标。",
            })
            blocks.append(self._marker_image_block(after, self._mark_point))
        return {
            "tool_result_content": blocks,
            "executed_action": {"tool": name, "success": success, "screen_changed": changed},
        }

    @staticmethod
    def _marker_image_block(frame: Any, point: tuple[float, float]) -> dict[str, Any]:
        """在帧上给归一化坐标 point 画红叉标记，返回 image 块（长边 ≤1024）。"""
        import cv2  # noqa: PLC0415

        img = frame.copy()
        fh, fw = img.shape[:2]
        px, py = int(point[0] * fw), int(point[1] * fh)
        r = max(min(fw, fh) // 40, 8)
        red = (0, 0, 255)
        cv2.line(img, (px - r, py - r), (px + r, py + r), red, 2)
        cv2.line(img, (px - r, py + r), (px + r, py - r), red, 2)
        cv2.circle(img, (px, py), r + 4, red, 1)
        if max(fh, fw) > 1024:
            scale = 1024 / max(fh, fw)
            img = cv2.resize(img, (round(fw * scale), round(fh * scale)))
        _, buf = cv2.imencode(".png", img)
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": base64.b64encode(buf.tobytes()).decode("ascii"),
            },
        }

    def _dispatch(self, name: str, inp: dict[str, Any], before: Any) -> str | None:
        """把工具调用分发到 backend。

        Args:
            name: 动作工具名
            inp: 工具输入
            before: 动作前截图（给 parse_coords 做绝对像素归一化的尺寸基准）

        Returns:
            错误信息；None 表示成功。
        """
        if name == "click":
            x, y = parse_coords(inp, before)
            if x is None or y is None:
                return "click 缺少坐标 x/y"
            sx, sy = self._snap_to_feature(x, y, before)
            if (sx, sy) != (x, y):
                self._extra["snapped_to"] = [round(sx, 3), round(sy, 3)]
            self._mark_point = (sx, sy)
            self._backend.click(sx, sy)
        elif name == "click_text":
            text = str(inp.get("text", ""))
            if not text:
                return "click_text 缺少 text"
            if not self._backend.click_text(text):
                return f"文本未找到: {text}"
        elif name == "press_key":
            key = str(inp.get("key", ""))
            if not key:
                return "press_key 缺少 key"
            self._backend.press_key(key)
        elif name == "swipe":
            x1, y1 = parse_coords({"x": inp.get("x1"), "y": inp.get("y1")}, before)
            x2, y2 = parse_coords({"x": inp.get("x2"), "y": inp.get("y2")}, before)
            if None in (x1, y1, x2, y2):
                return "swipe 缺少坐标 x1/y1/x2/y2"
            self._backend.swipe(x1, y1, x2, y2)  # type: ignore[arg-type]
        elif name == "long_press":
            x, y = parse_coords(inp, before)
            if x is None or y is None:
                return "long_press 缺少坐标 x/y"
            self._backend.long_press(x, y, duration=float(inp.get("duration", 2.0)))
        elif name == "back":
            self._backend.press_key("escape")
        elif name == "match_icon":
            return self._match_icon(inp, before)
        return None

    def _match_icon(self, inp: dict[str, Any], frame: Any) -> str | None:
        """模板匹配定位图标并点击（match_icon 工具实现）。

        流程：裁剪区域 (x,y,w,h) → 以区域内相对位置 (px,py) 为中心取小块当真值
        模板 → 全屏 matchTemplate → 峰值坐标点击。置信度 <0.6 不点击并报错。
        匹配信息存 self._extra 供结果 JSON 和录制用。
        """
        import cv2  # noqa: PLC0415

        try:
            x, y = float(inp["x"]), float(inp["y"])
            w, h = float(inp["w"]), float(inp["h"])
            px, py = float(inp["px"]), float(inp["py"])
        except (KeyError, TypeError, ValueError):
            return "match_icon 缺少参数 x/y/w/h/px/py"

        fh, fw = frame.shape[:2]
        x1, y1 = max(int(x * fw), 0), max(int(y * fh), 0)
        x2, y2 = min(x1 + int(w * fw), fw), min(y1 + int(h * fh), fh)
        if x2 - x1 < 10 or y2 - y1 < 10:
            return "match_icon 区域太小（<10px）"
        crop = frame[y1:y2, x1:x2]
        ch, cw = crop.shape[:2]

        # 以 (px,py) 为中心取小块当模板：边长 = 区域短边一半（钳制 20-200px）
        side = max(min(int(0.5 * min(cw, ch)), 200), 20)
        half = side // 2
        cx, cy = int(px * cw), int(py * ch)
        px1, py1 = max(cx - half, 0), max(cy - half, 0)
        px2, py2 = min(cx + half, cw), min(cy + half, ch)
        patch = crop[py1:py2, px1:px2]
        if patch.size == 0:
            return "match_icon 模板块为空"
        if float(patch.std()) < 5.0:
            return "match_icon 模板块无特征（纯色区域），请对准图标再试"

        res = cv2.matchTemplate(frame, patch, cv2.TM_CCOEFF_NORMED)
        _, score, _, loc = cv2.minMaxLoc(res)
        ph, pw = patch.shape[:2]
        gx, gy = (loc[0] + pw / 2) / fw, (loc[1] + ph / 2) / fh
        self._extra = {
            "match_score": round(float(score), 3),
            "matched_x": round(gx, 3),
            "matched_y": round(gy, 3),
        }
        if score < 0.6:
            return f"匹配置信度过低({score:.2f})，未点击"
        self._mark_point = (gx, gy)
        self._backend.click(gx, gy)
        return None

    @staticmethod
    def _snap_to_feature(
        x: float, y: float, frame: Any, radius: float = 0.06
    ) -> tuple[float, float]:
        """在 (x,y) 半径内吸附到最近的可疑组件中心（轮廓检测）。

        模型坐标估算常偏 0.05-0.1，吸附负责"最后一厘米"：全帧 Canny 边缘 →
        外轮廓 → 面积过滤（图标量级）→ 取圆心在半径内且最近者（全帧检测避免
        组件被 ROI 边界裁切）。找不到候选返回原坐标。
        深色 pixel-art 背景上只是启发式，配合标记反馈兜底。

        Args:
            x, y: 目标归一化坐标
            frame: 当前帧（BGR）
            radius: 吸附半径（归一化，按长边）

        Returns:
            吸附后的归一化坐标（无候选时为原坐标）。
        """

        fh, fw = frame.shape[:2]
        px, py = x * fw, y * fh
        r = radius * max(fw, fh)
        best: tuple[float, float] | None = None
        best_d = r
        for cx_f, cy_f, _area in _find_blobs(frame):
            cx, cy = cx_f * fw, cy_f * fh
            d = ((cx - px) ** 2 + (cy - py) ** 2) ** 0.5
            if d < best_d:
                best_d = d
                best = (cx_f, cy_f)
        return best if best is not None else (x, y)

    def _record(self, name: str, inp: dict[str, Any]) -> None:
        """同步录制到 GlobalRecorder（映射参考 LLMExplorer._record）。"""
        if self._recorder is None:
            return
        try:
            if name == "click":
                self._recorder.record_action("click", x=inp.get("x"), y=inp.get("y"))
            elif name == "click_text":
                self._recorder.record_action("click_text", text=str(inp.get("text", "")))
            elif name == "press_key":
                self._recorder.record_action("press_key", key=str(inp.get("key", "")))
            elif name == "back":
                self._recorder.record_action("press_key", key="escape")
            elif name in ("swipe", "long_press"):
                x = inp.get("x", inp.get("x1"))
                y = inp.get("y", inp.get("y1"))
                if x is not None and y is not None:
                    self._recorder.record_action("click", x=x, y=y, note=name)
            elif name == "match_icon":
                self._recorder.record_action(
                    "click",
                    x=self._extra.get("matched_x"),
                    y=self._extra.get("matched_y"),
                    note="match_icon",
                )
        except Exception:  # noqa: BLE001
            _LOGGER.warning("录制失败 %s", name, exc_info=True)

    # ===== 结果组装 =====

    def _detect_change(self, before: Any, after: Any) -> tuple[bool, float]:
        """像素 diff 检测界面变化（阈值 0.005，与 llm_explorer 一致）。"""
        try:
            from joker_test.explorer.detection import has_screen_changed  # noqa: PLC0415

            return has_screen_changed(before, after, _SCREEN_CHANGE_THRESHOLD)
        except Exception:  # noqa: BLE001
            return True, 0.0

    def _texts_after(self) -> list[str]:
        """动作后新帧的 OCR 文本（最多 _MAX_OCR_AFTER 条，容错）。"""
        try:
            return list(self._backend.state.texts[:_MAX_OCR_AFTER])
        except Exception:  # noqa: BLE001
            return []

    @staticmethod
    def _result_json(
        success: bool,
        changed: bool = False,
        ratio: float = 0.0,
        error: str | None = None,
        ocr_after: list[str] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> str:
        """动作结果 JSON 文本（tool_result 给 LLM 的事实反馈）。"""
        return json.dumps(
            {
                "success": success,
                "screen_changed": changed,
                "pixel_diff_ratio": round(ratio, 4),
                "error": error,
                "ocr_after": ocr_after or [],
                **(extra or {}),
            },
            ensure_ascii=False,
        )


__all__ = ["AGENT_TOOL_SCHEMAS", "AgentToolExecutor"]
