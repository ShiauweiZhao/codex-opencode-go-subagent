"""Translate between Codex Responses requests and OpenCode Go Chat Completions."""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Iterable


JSON = dict[str, Any]
SUPPORTED_MODEL = "deepseek-v4-flash"
_VALID_TOOL_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_DROP_TOOL_TYPES = {
    "computer_use_preview",
    "file_search",
    "image_generation",
    "local_shell",
    "mcp",
    "web_search",
    "web_search_preview",
}


class ProtocolError(ValueError):
    """The request cannot be represented safely on the upstream protocol."""


@dataclass(frozen=True)
class ProtocolContext:
    messages: list[JSON]
    reverse_tool_names: dict[str, str]
    tool_types: dict[str, str]
    tools: list[JSON]


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return _text(content)
    parts: list[str] = []
    for part in content:
        if isinstance(part, str):
            parts.append(part)
        elif isinstance(part, dict):
            typ = part.get("type")
            if typ in {"input_text", "output_text", "text"} or "text" in part:
                parts.append(_text(part.get("text")))
            elif typ in {"input_image", "image_url"}:
                raise ProtocolError("DeepSeek V4 Flash worker accepts text input only")
    return "".join(parts)


def _model_id(raw: Any) -> str:
    model = str(raw or SUPPORTED_MODEL)
    for prefix in ("opencode-go/", "ocg-"):
        if model.startswith(prefix):
            model = model[len(prefix) :]
    if model != SUPPORTED_MODEL:
        raise ProtocolError(f"unsupported model: {raw!r}; expected {SUPPORTED_MODEL}")
    return model


def _sanitize_tool_name(name: str, used: set[str]) -> str:
    if _VALID_TOOL_NAME.fullmatch(name) and name not in used:
        used.add(name)
        return name
    base = re.sub(r"[^A-Za-z0-9_-]", "_", name)[:64] or "tool"
    candidate = base
    suffix = 2
    while candidate in used:
        tail = f"_{suffix}"
        candidate = f"{base[: 64 - len(tail)]}{tail}"
        suffix += 1
    used.add(candidate)
    return candidate


def _convert_tools(tools: Any) -> tuple[list[JSON], dict[str, str], dict[str, str]]:
    if not isinstance(tools, list):
        return [], {}, {}
    converted: list[JSON] = []
    reverse: dict[str, str] = {}
    tool_types: dict[str, str] = {}
    used: set[str] = set()
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        typ = tool.get("type")
        if typ in _DROP_TOOL_TYPES:
            continue
        if typ == "function" and isinstance(tool.get("function"), dict):
            function = dict(tool["function"])
            original = str(function.get("name") or "tool")
        elif typ == "function" and tool.get("name"):
            original = str(tool["name"])
            function = {
                "name": original,
                "description": tool.get("description", ""),
                "parameters": tool.get("parameters") or {"type": "object", "properties": {}},
            }
        elif typ == "custom" and tool.get("name") == "apply_patch":
            original = "apply_patch"
            function = {
                "name": original,
                "description": tool.get("description", "Apply a patch inside the writable workspace."),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "patch": {
                            "type": "string",
                            "description": "The complete apply_patch payload, including Begin Patch and End Patch markers.",
                        }
                    },
                    "required": ["patch"],
                    "additionalProperties": False,
                },
            }
        else:
            continue
        safe = _sanitize_tool_name(original, used)
        reverse[safe] = original
        tool_types[safe] = str(typ)
        function["name"] = safe
        function.setdefault("description", "")
        function.setdefault("parameters", {"type": "object", "properties": {}})
        converted.append({"type": "function", "function": function})
    return converted, reverse, tool_types


def _request_items(value: Any) -> tuple[list[JSON], list[JSON]]:
    if isinstance(value, str):
        return [{"role": "user", "content": value}], []
    if value is None:
        return [], []
    if not isinstance(value, list):
        raise ProtocolError("Responses input must be a string or list")
    messages: list[JSON] = []
    outputs: list[JSON] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        typ = item.get("type")
        if typ in {"function_call_output", "custom_tool_call_output"}:
            call_id = str(item.get("call_id") or "")
            if not call_id:
                raise ProtocolError("function_call_output is missing call_id")
            outputs.append(
                {"role": "tool", "tool_call_id": call_id, "content": _text(item.get("output"))}
            )
            continue
        if typ == "message" or item.get("role"):
            role = str(item.get("role") or "user")
            if role == "developer":
                role = "system"
            if role not in {"system", "user", "assistant"}:
                raise ProtocolError(f"unsupported message role: {role}")
            messages.append({"role": role, "content": _content_text(item.get("content"))})
    return messages, outputs


def build_chat_request(body: JSON, previous: JSON | None = None) -> tuple[JSON, ProtocolContext]:
    """Build one conservative OpenAI-compatible Chat Completions request."""

    model = _model_id(body.get("model"))
    new_messages, tool_outputs = _request_items(body.get("input"))
    messages: list[JSON] = []
    if previous:
        messages.extend(previous.get("messages") or [])
    elif tool_outputs:
        raise ProtocolError("function_call_output requires a previous response")

    instructions = _content_text(body.get("instructions"))
    if instructions and not previous:
        messages.append({"role": "system", "content": instructions})

    if tool_outputs:
        pending = set(previous.get("pending_call_ids") or []) if previous else set()
        supplied = {item["tool_call_id"] for item in tool_outputs}
        if not supplied.issubset(pending):
            unknown = ", ".join(sorted(supplied - pending))
            raise ProtocolError(f"function_call_output does not match previous response: {unknown}")
        if supplied != pending:
            missing = ", ".join(sorted(pending - supplied))
            raise ProtocolError(f"partial function_call_output set; missing: {missing}")
        messages.extend(tool_outputs)
    messages.extend(new_messages)
    if not messages:
        messages.append({"role": "user", "content": ""})

    converted_tools, reverse, tool_types = _convert_tools(body.get("tools"))
    if previous:
        inherited_reverse = dict(previous.get("reverse_tool_names") or {})
        inherited_reverse.update(reverse)
        reverse = inherited_reverse
        inherited_types = dict(previous.get("tool_types") or {})
        inherited_types.update(tool_types)
        tool_types = inherited_types
        if not converted_tools:
            converted_tools = list(previous.get("tools") or [])
    payload: JSON = {"model": model, "messages": messages, "stream": False}
    if converted_tools:
        payload["tools"] = converted_tools
    for source, target in (
        ("temperature", "temperature"),
        ("top_p", "top_p"),
        ("max_output_tokens", "max_tokens"),
        ("max_tokens", "max_tokens"),
        ("presence_penalty", "presence_penalty"),
        ("frequency_penalty", "frequency_penalty"),
    ):
        if body.get(source) is not None:
            payload[target] = body[source]
    return payload, ProtocolContext(
        messages=messages,
        reverse_tool_names=reverse,
        tool_types=tool_types,
        tools=converted_tools,
    )


def build_responses_result(
    body: JSON,
    chat_response: JSON,
    context: ProtocolContext,
    *,
    response_id: str | None = None,
    created_at: int | None = None,
) -> tuple[JSON, JSON]:
    """Map a complete Chat Completions response to a Codex Responses result."""

    choices = chat_response.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ProtocolError("upstream response is missing choices[0]")
    upstream = choices[0].get("message") or {}
    if not isinstance(upstream, dict):
        raise ProtocolError("upstream response message is invalid")

    content = _content_text(upstream.get("content"))
    reasoning = upstream.get("reasoning_content") or upstream.get("reasoning")
    assistant: JSON = {"role": "assistant", "content": content}
    if reasoning:
        assistant["reasoning_content"] = reasoning

    output: list[JSON] = []
    if reasoning:
        output.append({"type": "reasoning", "id": _new_id("rs"), "summary": []})

    replay_calls: list[JSON] = []
    pending: list[str] = []
    for raw in upstream.get("tool_calls") or []:
        if not isinstance(raw, dict):
            continue
        function = raw.get("function") or {}
        raw_name = str(function.get("name") or raw.get("name") or "tool")
        original_name = context.reverse_tool_names.get(raw_name, raw_name)
        arguments = function.get("arguments", raw.get("arguments", "{}"))
        if not isinstance(arguments, str):
            arguments = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
        call_id = str(raw.get("id") or raw.get("call_id") or _new_id("call"))
        replay_calls.append(
            {
                "id": call_id,
                "type": "function",
                "function": {"name": raw_name, "arguments": arguments},
            }
        )
        pending.append(call_id)
        if context.tool_types.get(raw_name) == "custom":
            try:
                custom_arguments = json.loads(arguments)
            except json.JSONDecodeError as error:
                raise ProtocolError("apply_patch tool call arguments must be valid JSON") from error
            patch = custom_arguments.get("patch") if isinstance(custom_arguments, dict) else None
            if not isinstance(patch, str):
                raise ProtocolError("apply_patch tool call is missing string patch")
            output.append(
                {
                    "type": "custom_tool_call",
                    "id": _new_id("ctc"),
                    "call_id": call_id,
                    "name": original_name,
                    "input": patch,
                    "status": "completed",
                }
            )
        else:
            output.append(
                {
                    "type": "function_call",
                    "id": _new_id("fc"),
                    "call_id": call_id,
                    "name": original_name,
                    "arguments": arguments,
                    "status": "completed",
                }
            )
    if replay_calls:
        assistant["tool_calls"] = replay_calls
    elif content:
        output.append(
            {
                "type": "message",
                "id": _new_id("msg"),
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": content, "annotations": []}],
            }
        )

    response_id = response_id or _new_id("resp")
    created_at = int(created_at if created_at is not None else time.time())
    usage = chat_response.get("usage") or {}
    result: JSON = {
        "id": response_id,
        "object": "response",
        "created_at": created_at,
        "status": "completed",
        "error": None,
        "incomplete_details": None,
        "instructions": body.get("instructions"),
        "model": SUPPORTED_MODEL,
        "output": output,
        "parallel_tool_calls": False,
        "previous_response_id": body.get("previous_response_id"),
        "store": False,
        "temperature": body.get("temperature"),
        "top_p": body.get("top_p"),
        "truncation": body.get("truncation", "disabled"),
        "usage": {
            "input_tokens": usage.get("prompt_tokens", usage.get("input_tokens", 0)),
            "output_tokens": usage.get("completion_tokens", usage.get("output_tokens", 0)),
            "total_tokens": usage.get("total_tokens", 0),
        },
        "metadata": body.get("metadata") or {},
    }
    state = {
        "messages": context.messages + [assistant],
        "pending_call_ids": pending,
        "reverse_tool_names": context.reverse_tool_names,
        "tool_types": context.tool_types,
        "tools": context.tools,
    }
    return result, state


def _event(name: str, payload: JSON, *, response_id: str, sequence_number: int) -> bytes:
    data = dict(payload)
    data.setdefault("type", name)
    data.setdefault("response_id", response_id)
    data.setdefault("sequence_number", sequence_number)
    return (
        f"event: {name}\n"
        f"data: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"
    ).encode("utf-8")


def _output_events(
    response: JSON,
    index: int,
    item: JSON,
    emit: Callable[[str, JSON], bytes],
) -> Iterable[bytes]:
    response_id = response["id"]
    yield emit(
        "response.output_item.added",
        {"response_id": response_id, "output_index": index, "item": {**item, "status": "in_progress"}},
    )
    typ = item.get("type")
    if typ == "message":
        content = (item.get("content") or [{}])[0]
        text = _text(content.get("text"))
        yield emit(
            "response.content_part.added",
            {
                "response_id": response_id,
                "output_index": index,
                "content_index": 0,
                "item_id": item["id"],
                "part": {"type": "output_text", "text": "", "annotations": []},
            },
        )
        if text:
            yield emit(
                "response.output_text.delta",
                {
                    "response_id": response_id,
                    "output_index": index,
                    "content_index": 0,
                    "item_id": item["id"],
                    "delta": text,
                },
            )
        yield emit(
            "response.output_text.done",
            {
                "response_id": response_id,
                "output_index": index,
                "content_index": 0,
                "item_id": item["id"],
                "text": text,
            },
        )
        yield emit(
            "response.content_part.done",
            {
                "response_id": response_id,
                "output_index": index,
                "content_index": 0,
                "item_id": item["id"],
                "part": content,
            },
        )
    elif typ == "function_call":
        arguments = _text(item.get("arguments"))
        if arguments:
            yield emit(
                "response.function_call_arguments.delta",
                {
                    "response_id": response_id,
                    "output_index": index,
                    "item_id": item["id"],
                    "delta": arguments,
                },
            )
        yield emit(
            "response.function_call_arguments.done",
            {
                "response_id": response_id,
                "output_index": index,
                "item_id": item["id"],
                "arguments": arguments,
            },
        )
    elif typ == "custom_tool_call":
        tool_input = _text(item.get("input"))
        if tool_input:
            yield emit(
                "response.custom_tool_call_input.delta",
                {
                    "response_id": response_id,
                    "output_index": index,
                    "item_id": item["id"],
                    "delta": tool_input,
                },
            )
        yield emit(
            "response.custom_tool_call_input.done",
            {
                "response_id": response_id,
                "output_index": index,
                "item_id": item["id"],
                "input": tool_input,
            },
        )
    yield emit(
        "response.output_item.done",
        {"response_id": response_id, "output_index": index, "item": item},
    )


def encode_sse(response: JSON) -> bytes:
    """Encode a completed result as a valid Responses SSE event stream."""

    shell = {**response, "status": "in_progress", "output": []}
    sequence_number = 0

    def emit(name: str, payload: JSON) -> bytes:
        nonlocal sequence_number
        sequence_number += 1
        return _event(
            name,
            payload,
            response_id=response["id"],
            sequence_number=sequence_number,
        )

    chunks: list[bytes] = [emit("response.created", {"response": shell})]
    for index, item in enumerate(response.get("output") or []):
        chunks.extend(_output_events(response, index, item, emit))
    chunks.append(emit("response.completed", {"response": response}))
    chunks.append(b"data: [DONE]\n\n")
    return b"".join(chunks)
