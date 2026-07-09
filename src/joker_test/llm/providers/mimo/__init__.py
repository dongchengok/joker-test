"""MiMoProvider —— 小米 MiMo 的便捷封装（Anthropic 协议）。

小米 MiMo（mimo-v2.5，全模态理解）走 Anthropic 协议，x-api-key 认证。
本类是 AnthropicProvider 的薄封装，提供小米默认配置（从 .env 读）。
"""

from __future__ import annotations

from joker_test.llm.providers.anthropic import AnthropicProvider, load_env


class MiMoProvider(AnthropicProvider):
    """小米 MiMo provider（Anthropic 协议，x-api-key 认证，全模态）。

    默认从 .env 读 MIMO_API_KEY/MIMO_BASE_URL/MIMO_MODEL。
    可显式传参覆盖。

    用法::
        provider = MiMoProvider()  # 自动从 .env 读
        msg = provider.simple_converse("你好", [])
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        max_tokens: int = 8192,
    ) -> None:
        config = load_env()
        super().__init__(
            api_key=api_key or config.get("MIMO_API_KEY", ""),
            base_url=base_url or config.get("MIMO_BASE_URL", "https://token-plan-cn.xiaomimimo.com/anthropic"),
            model=model or config.get("MIMO_MODEL", "mimo-v2.5"),
            auth_mode="x-api-key",
            max_tokens=max_tokens,
        )
        if not self._api_key:
            raise ValueError(
                "MiMoProvider 缺 API key。请在 .env 设 MIMO_API_KEY 或传 api_key 参数。"
            )


__all__ = ["MiMoProvider"]
