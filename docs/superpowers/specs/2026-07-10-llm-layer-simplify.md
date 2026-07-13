# LLM 层精简设计

> 日期：2026-07-10
> 状态：待实现

## 1. 目标

精简 LLM 层：删掉冗余 provider 子类和装饰器，AnthropicProvider 用 anthropic SDK + instructor 重写，trace 内置到 provider 内部。

## 2. 最终结构

```
LLMProvider（协议）
  ├── AnthropicProvider（唯一真实实现，SDK+instructor，内置 trace_llm）
  └── MockProvider（CI 测试，内置 trace_llm）
```

## 3. 删除

- BedrockProvider + SpecOps-src 依赖
- GLMProvider（AnthropicProvider 参数配置即可）
- MiMoProvider（同上）
- TracingProvider（trace 内置到 provider，不再需要装饰器）
- converse_json 方法（charter_gen 改用 simple_converse + tool_schema）

## 4. LLMProvider 协议

```python
class LLMProvider(Protocol):
    def simple_converse(
        self, prompt: str, messages: list[Message], *,
        reasoning: int = 0,
        images: list[str] | None = None,
        tool_schema: dict | None = None,
    ) -> Message: ...
```

删掉 converse_json。LLMProvider 接口与 AnthropicProvider 完全对齐。

## 5. AnthropicProvider

用 anthropic SDK + instructor 重写：
- 构造：`AnthropicProvider(api_key, base_url, model, max_tokens)`，默认从 .env 读 MiMo 配置
- simple_converse：用 `instructor.from_anthropic(client)` 包装，传 response_model（Pydantic）做校验
- tool_schema：传给 SDK 的 tools + tool_choice
- 内部调 `trace_llm` 记录每次调用（prompt/reply/耗时/model）
- images：SDK 自动处理 image block

## 6. MockProvider

保留，适配新协议：
- 删 converse_json
- simple_converse 支持 tool_schema 参数（返回固定 tool_use block）
- 内部调 trace_llm（保持一致）

## 7. 调用方更新

- charter_gen：converse_json → simple_converse + tool_schema
- cli：MockProvider import 路径更新；generate-charter 的 --provider mock 保留
- pipeline/base.py build_orchestrator：MockProvider → AnthropicProvider（或保留 Mock 用于 CI）
- scripts：MiMoProvider/GLMProvider → AnthropicProvider
- flow/generator.py、flow/namer.py、flow/verifier.py：TracingProvider 包装去掉
- tests：MockProvider import 路径更新

## 8. .env 配置

只留 MiMo：
```
MIMO_API_KEY=...
MIMO_BASE_URL=https://token-plan-cn.xiaomimimo.com/anthropic
MIMO_MODEL=mimo-v2.5
```
```
