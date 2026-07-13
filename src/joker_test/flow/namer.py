"""FlowNamer —— LLM 看操作流 + 截图起中文名（用于 rename 录制目录）。

录制产物默认用临时名（时间戳），录制结束后让 LLM 看操作流摘要 + 关键截图，
起一个语义化的中文名（如"进入地牢流程"），rename 目录成 <时间戳>_<中文名>。

设计要点：
- LLM 克制：只调一次 LLM，prompt 精简（操作摘要 + 少量截图，不是全量）
- 状态自洽：只持有 provider
- 容错：LLM 起名失败回退到时间戳名，不阻断流程

用法::

    namer = FlowNamer(provider)
    name, desc = namer.name_flow(flow, screenshots_dir)
    # name = "进入地牢流程"，desc = "从主菜单进入角色选择的流程"
    # 调用方：rename 目录成 <时间戳>_进入地牢流程/
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

from joker_test.flow.semantics import _cv_imread
from joker_test.flow.types import RecordedFlow

if TYPE_CHECKING:
    from joker_test.llm.base import LLMProvider, Message

logger = logging.getLogger(__name__)

# 文件名非法字符清洗（Windows 不允许这些出现在路径里）
_ILLEGAL_CHARS = re.compile(r'[\\/:*?"<>|]')


class FlowNamer:
    """LLM 起名器：看操作流 + 截图，返回语义化的中文名 + 用途说明。

    Args:
        provider: LLM provider（多模态，能看图）
        max_screenshots: 最多喂几张截图（省 token，默认 3）
    """

    def __init__(self, provider: LLMProvider, max_screenshots: int = 3) -> None:
        self._provider = provider
        self._max_screenshots = max_screenshots

    def name_flow(
        self, flow: RecordedFlow, screenshots_dir: Path | None = None
    ) -> tuple[str, str]:
        """给操作流起中文名 + 用途说明。

        Args:
            flow: 录制的操作流
            screenshots_dir: 截图目录（None 不喂图）

        Returns:
            (中文名, 用途说明)。LLM 失败时中文名回退到 flow.name（时间戳），说明为空。
        """
        summary = self._summarize_flow(flow)
        images = self._collect_images(screenshots_dir)

        prompt = (
            "以下是录制的游戏操作流程摘要。请给它起一个**简短的中文名**（4-12 字，"
            "概括操作目的，如'进入地牢流程''购买装备测试'），"
            "并用一句话说明它的用途。\n\n"
            f"<操作流摘要>\n{summary}\n</操作流摘要>\n\n"
            "请用 JSON 回答："
            '{"name": "中文名", "description": "用途说明"}'
        )

        try:
            from joker_test.llm.base import build_user_message  # noqa: PLC0415

            msg = self._provider.create(messages=[build_user_message(prompt, images or None)])
            reply = self._extract_text(msg)
            name, desc = self._parse_name_reply(reply)
            name = self._sanitize_filename(name)
            if not name:
                name = flow.name
                desc = ""
            logger.info("LLM 起名: %s (%s)", name, desc)
            return name, desc
        except Exception as e:  # noqa: BLE001
            logger.warning("LLM 起名失败，回退到时间戳名: %s", e)
            return flow.name, ""

    def _summarize_flow(self, flow: RecordedFlow) -> str:
        """把操作流摘要成文本（给 LLM 看，不喂全量步骤）。"""
        lines = [f"共 {len(flow.steps)} 步操作:"]
        for i, step in enumerate(flow.steps, 1):
            if step.action == "click":
                target = step.note or f"坐标({step.x:.2f},{step.y:.2f})"
                lines.append(f"  {i}. 点击 {target}")
            elif step.action == "click_text":
                lines.append(f"  {i}. 点击文字'{step.text}'")
            elif step.action == "click_image":
                lines.append(f"  {i}. 点击图像'{step.note}'")
            elif step.action == "press_key":
                lines.append(f"  {i}. 按键 {step.key}")
            elif step.action == "type_text":
                lines.append(f"  {i}. 输入 '{step.text}'")
        # 附带关键 OCR 文本（帮 LLM 理解界面）
        all_ocr: list[str] = []
        for step in flow.steps:
            all_ocr.extend(step.ocr_texts_after[:3])
        if all_ocr:
            lines.append(f"界面 OCR 文本样本: {all_ocr[:10]}")
        return "\n".join(lines)

    def _collect_images(self, screenshots_dir: Path | None) -> list[str]:
        """收集截图转 base64（最多 max_screenshots 张，均匀抽样）。"""
        if screenshots_dir is None or not screenshots_dir.is_dir():
            return []
        import base64  # noqa: PLC0415

        import cv2  # noqa: PLC0415

        shots = sorted(screenshots_dir.glob("*.png"))
        if not shots:
            return []
        # 均匀抽样
        if len(shots) > self._max_screenshots:
            step = len(shots) / self._max_screenshots
            shots = [shots[int(i * step)] for i in range(self._max_screenshots)]

        images: list[str] = []
        for shot_path in shots:
            frame = _cv_imread(shot_path)
            if frame is None:
                continue
            success, buf = cv2.imencode(".png", frame)
            if success:
                images.append(base64.b64encode(buf.tobytes()).decode("utf-8"))
        return images

    @staticmethod
    def _extract_text(msg: Message) -> str:
        """从 Message 提取文本。"""
        parts: list[str] = []
        for block in msg.get("content", []):
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "\n".join(parts)

    @staticmethod
    def _parse_name_reply(reply: str) -> tuple[str, str]:
        """从 LLM 回复解析 name + description。"""
        import json  # noqa: PLC0415
        import re  # noqa: PLC0415

        # 抽 ```json 代码块
        code_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", reply, re.DOTALL)
        candidate = code_match.group(1) if code_match else reply
        start = candidate.find("{")
        if start >= 0:
            depth = 0
            for i in range(start, len(candidate)):
                if candidate[i] == "{":
                    depth += 1
                elif candidate[i] == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            data = json.loads(candidate[start : i + 1])
                            return data.get("name", ""), data.get("description", "")
                        except json.JSONDecodeError:
                            break
        return "", ""

    @staticmethod
    def _sanitize_filename(name: str) -> str:
        """清洗文件名非法字符 + 去首尾空白。"""
        cleaned = _ILLEGAL_CHARS.sub("_", name).strip()
        return cleaned[:30]  # 限制长度


__all__ = ["FlowNamer"]
