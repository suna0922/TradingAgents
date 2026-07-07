"""Shared helpers for invoking an agent with structured output and a graceful fallback.

The Portfolio Manager, Trader, and Research Manager all follow the same
canonical pattern:

1. At agent creation, wrap the LLM with ``with_structured_output(Schema)``
   so the model returns a typed Pydantic instance. If the provider does
   not support structured output (rare; mostly older Ollama models), the
   wrap is skipped and the agent uses free-text generation instead.
2. At invocation, run the structured call and render the result back to
   markdown. If the structured call itself fails for any reason
   (malformed JSON from a weak model, transient provider issue), fall
   back to a plain ``llm.invoke`` so the pipeline never blocks.

Centralising the pattern keeps the agent factories small and ensures
all three agents log the same warnings when fallback fires.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from typing import Any, Callable, Optional, TypeVar

from pydantic import BaseModel

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# Default timeout (seconds) for each LLM invoke call.
# Prevents hangs when the API stops responding (observed with DeepSeek
# where a 400 error could leave the connection open indefinitely).
_INVOKE_TIMEOUT_S = 300  # 5 minutes


def bind_structured(llm: Any, schema: type[T], agent_name: str, method: Optional[str] = None) -> Optional[Any]:
    """Return ``llm.with_structured_output(schema)`` or ``None`` if unsupported.

    Logs a warning when the binding fails so the user understands the
    agent will use free-text generation for every call instead of one-shot fallback.

    Args:
        llm: The LLM client.
        schema: The Pydantic model to bind.
        agent_name: For logging.
        method: Override structured output method (e.g. "json_mode" for DeepSeek
                when function_calling truncates large outputs).
    """
    try:
        if method:
            return llm.with_structured_output(schema, method=method)
        return llm.with_structured_output(schema)
    except (NotImplementedError, AttributeError) as exc:
        logger.warning(
            "%s: provider does not support with_structured_output (%s); "
            "falling back free-text generation",
            agent_name, exc,
        )
        return None


def _invoke_with_timeout(llm: Any, prompt: Any, timeout_s: int, label: str) -> Any:
    """Run ``llm.invoke(prompt)`` in a thread with a hard timeout.

    Returns the result on success, raises ``TimeoutError`` if the call
    exceeds *timeout_s* seconds.  This guards against API hangs where
    the HTTP connection never returns (neither success nor error).
    """
    _diag = logging.getLogger("deepseek_diag")

    def _do_invoke():
        return llm.invoke(prompt)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_do_invoke)
        try:
            return future.result(timeout=timeout_s)
        except FutureTimeoutError:
            _diag.error(
                "[INVOKE-TIMEOUT] label=%s | timeout=%ds | prompt_chars≈%d",
                label,
                timeout_s,
                len(prompt) if isinstance(prompt, str) else len(str(prompt)),
            )
            raise TimeoutError(
                f"{label} LLM invoke exceeded {timeout_s}s timeout — "
                "API appears unresponsive"
            )


def invoke_structured_or_freetext_with_raw(
    structured_llm: Optional[Any],
    plain_llm: Any,
    prompt: Any,
    render: Callable[[T], str],
    agent_name: str,
    timeout_s: int = _INVOKE_TIMEOUT_S,
) -> tuple[str, Optional[T]]:
    """Run the structured call and render to markdown; fall back to free-text on any failure.

    Returns:
        (markdown_string, structured_object_or_None)
        - markdown_string: The rendered markdown (same as invoke_structured_or_freetext)
        - structured_object: The raw Pydantic object if structured call succeeded, else None

    This allows downstream consumers to access both the human-readable markdown
    and the machine-parseable structured data (e.g. trading_rules with trigger_sql).
    """
    _diag = logging.getLogger("deepseek_diag")

    # ── 诊断: 记录进入 PM/Structured 节点的 prompt 大小 ──
    try:
        prompt_str = prompt if isinstance(prompt, str) else str(prompt)
        _diag.warning(
            "[STRUCTURED-ENTER] agent=%s | prompt_chars=%d | has_structured_llm=%s",
            agent_name, len(prompt_str), structured_llm is not None,
        )
    except Exception:
        pass

    if structured_llm is not None:
        try:
            _diag.warning("[STRUCTURED-INVOKE] agent=%s | calling structured_llm.invoke()...", agent_name)
            result = _invoke_with_timeout(structured_llm, prompt, timeout_s, f"{agent_name}(structured)")
            if result is None:
                raise ValueError("structured_llm returned None (schema validation likely failed)")
            _diag.warning("[STRUCTURED-SUCCESS] agent=%s | structured invoke OK", agent_name)
            return render(result), result
        except Exception as exc:
            _diag.error(
                "[STRUCTURED-FAIL] agent=%s | error=%s | falling back to free-text",
                agent_name, str(exc)[:500],
            )

    _diag.warning("[PLAIN-INVOKE] agent=%s | calling plain_llm.invoke()...", agent_name)
    response = _invoke_with_timeout(plain_llm, prompt, timeout_s, f"{agent_name}(plain)")
    _diag.warning("[PLAIN-SUCCESS] agent=%s | plain invoke OK", agent_name)
    return response.content, None


def invoke_structured_or_freetext(
    structured_llm: Optional[Any],
    plain_llm: Any,
    prompt: Any,
    render: Callable[[T], str],
    agent_name: str,
    timeout_s: int = _INVOKE_TIMEOUT_S,
) -> str:
    """Run the structured call and render to markdown; fall back to free-text on any failure.

    Both the structured and plain-text paths are wrapped with *timeout_s*
    guard to prevent indefinite hangs when the provider stops responding.

    ``prompt`` is whatever the underlying LLM accepts (a string for chat
    invocations, a list of message dicts for chat models that take that
    shape). The same value is forwarded to the free-text path so the
    fallback sees the same input the structured call did.
    """
    markdown, _ = invoke_structured_or_freetext_with_raw(
        structured_llm, plain_llm, prompt, render, agent_name, timeout_s
    )
    return markdown
