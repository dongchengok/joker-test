"""GLMProvider —— 智谱 GLM 的便捷封装（Anthropic 协议）。

智谱 GLM（glm-4-flash/glm-5v-turbo）走 Anthropic 协议，Bearer 认证。
本类是 AnthropicProvider 的薄封装，提供智谱默认配置（从 .env 读）。

实际协议实现在 anthropic/ 子包。这里只做"智谱默认值"预设。
"""

from __future__ import annotations

from joker_test.llm.providers.anthropic import AnthropicProvider, load_env


class GLMProvider(AnthropicProvider):
    """智谱 GLM provider（Anthropic 协议，Bearer 认证）。

    默认从 .env 读 ZHIPU_API_KEY/ZHIPU_BASE_URL/ZHIPU_MODEL。
    可显式传参覆盖。

    用法::
        provider = GLMProvider()  # 自动从 .env 读
        msg = provider.simple_converse("你好", [])
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
    ) -> None:
        config = load_env()
        super().__init__(
            api_key=api_key or config.get("ZHIPU_API_KEY", ""),
            base_url=base_url or config.get("ZHIPU_BASE_URL", "https://open.bigmodel.cn/api/anthropic"),
            model=model or config.get("ZHIPU_MODEL", "glm-4-flash"),
            auth_mode="bearer",
            max_tokens=max_tokens,
        )
        if not self._api_key:
            raise ValueError(
                "GLMProvider 缺 API key。请在 .env 设 ZHIPU_API_KEY 或传 api_key 参数。"
            )


__all__ = ["GLMProvider"]
