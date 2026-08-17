import json
import os
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path


from codex_opencode_go_bridge.protocol import (
    ProtocolError,
    build_chat_request,
    build_responses_result,
    encode_sse,
)


class ProtocolTests(unittest.TestCase):
    def test_rejects_codex_auto_review_instead_of_sending_it_to_deepseek(self):
        with self.assertRaisesRegex(ProtocolError, "unsupported model"):
            build_chat_request(
                {
                    "model": "codex-auto-review",
                    "input": "Review one requested tool call.",
                }
            )

    def test_rejects_every_other_model_instead_of_falling_back(self):
        with self.assertRaisesRegex(ProtocolError, "unsupported model"):
            build_chat_request({"model": "gpt-5.6-sol", "input": "Do not proxy review."})

    def test_build_chat_request_maps_instructions_input_and_tools(self):
        body = {
            "model": "deepseek-v4-flash",
            "instructions": "Stay within the assigned scope.",
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Inspect the log"}],
                }
            ],
            "tools": [
                {
                    "type": "function",
                    "name": "functions.exec-command",
                    "description": "Run a command",
                    "parameters": {
                        "type": "object",
                        "properties": {"cmd": {"type": "string"}},
                        "required": ["cmd"],
                    },
                },
                {"type": "web_search_preview"},
            ],
            "parallel_tool_calls": True,
            "reasoning": {"effort": "high"},
            "store": False,
            "stream": True,
        }

        payload, context = build_chat_request(body)

        self.assertEqual(payload["model"], "deepseek-v4-flash")
        self.assertEqual(payload["messages"][0], {"role": "system", "content": body["instructions"]})
        self.assertEqual(payload["messages"][-1], {"role": "user", "content": "Inspect the log"})
        self.assertEqual(len(payload["tools"]), 1)
        self.assertEqual(payload["tools"][0]["function"]["name"], "functions_exec-command")
        self.assertEqual(context.reverse_tool_names, {"functions_exec-command": "functions.exec-command"})
        self.assertFalse(payload["stream"])
        self.assertEqual(payload["reasoning_effort"], "high")
        for unsupported in ("parallel_tool_calls", "reasoning", "store", "tool_choice"):
            self.assertNotIn(unsupported, payload)

    def test_build_chat_request_forwards_max_reasoning_effort(self):
        payload, _ = build_chat_request(
            {
                "model": "deepseek-v4-flash",
                "input": "Solve the bounded assignment.",
                "reasoning": {"effort": "max"},
            }
        )

        self.assertEqual(payload["reasoning_effort"], "max")

    def test_build_chat_request_forwards_low_reasoning_effort(self):
        payload, _ = build_chat_request(
            {
                "model": "deepseek-v4-flash",
                "input": "Solve the bounded assignment.",
                "reasoning": {"effort": "low"},
            }
        )

        self.assertEqual(payload["reasoning_effort"], "low")

    def test_build_chat_request_omits_reasoning_effort_when_reasoning_absent(self):
        payload, _ = build_chat_request(
            {
                "model": "deepseek-v4-flash",
                "input": "Solve the bounded assignment.",
            }
        )

        self.assertNotIn("reasoning_effort", payload)

    def test_build_chat_request_rejects_malformed_reasoning(self):
        for reasoning in ("high", {}, {"effort": 1}, {"effort": None}):
            with self.subTest(reasoning=reasoning), self.assertRaisesRegex(
                ProtocolError, "reasoning"
            ):
                build_chat_request(
                    {
                        "model": "deepseek-v4-flash",
                        "input": "Solve the bounded assignment.",
                        "reasoning": reasoning,
                    }
                )

    def test_build_chat_request_rejects_unsupported_reasoning_effort(self):
        for effort in ("medium", "ultra", "x-high"):
            with self.subTest(effort=effort), self.assertRaisesRegex(
                ProtocolError, "unsupported reasoning effort"
            ):
                build_chat_request(
                    {
                        "model": "deepseek-v4-flash",
                        "input": "Solve the bounded assignment.",
                        "reasoning": {"effort": effort},
                    }
                )

    def test_build_chat_request_maps_apply_patch_custom_tool_to_upstream_function(self):
        body = {
            "model": "deepseek-v4-flash",
            "input": "Implement the requested change.",
            "tools": [
                {
                    "type": "custom",
                    "name": "apply_patch",
                    "description": "Apply a patch to files in the writable workspace.",
                    "format": {"type": "grammar", "syntax": "lark", "definition": "start: /.+/s"},
                }
            ],
        }

        payload, context = build_chat_request(body)

        tool = payload["tools"][0]["function"]
        self.assertEqual(tool["name"], "apply_patch")
        self.assertEqual(tool["parameters"]["required"], ["patch"])
        self.assertEqual(context.tool_types, {"apply_patch": "custom"})

    def test_build_chat_request_adds_structured_apply_patch_for_exec_command(self):
        payload, context = build_chat_request(
            {
                "model": "deepseek-v4-flash",
                "input": "Implement the bounded change.",
                "tools": [
                    {
                        "type": "function",
                        "name": "exec_command",
                        "description": "Run a shell command.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "cmd": {"type": "string"},
                                "workdir": {"type": "string"},
                            },
                            "required": ["cmd"],
                        },
                    }
                ],
            }
        )

        tools = {item["function"]["name"]: item["function"] for item in payload["tools"]}
        self.assertEqual(set(tools), {"exec_command", "apply_patch"})
        self.assertEqual(tools["apply_patch"]["parameters"]["required"], ["patch", "workdir"])
        self.assertEqual(context.reverse_tool_names["apply_patch"], "exec_command")
        self.assertEqual(context.tool_types["apply_patch"], "safe_exec_apply_patch")

    def test_build_responses_result_safely_quotes_structured_apply_patch(self):
        body = {
            "model": "deepseek-v4-flash",
            "input": "Implement the bounded change.",
            "tools": [
                {
                    "type": "function",
                    "name": "exec_command",
                    "parameters": {
                        "type": "object",
                        "properties": {"cmd": {"type": "string"}},
                        "required": ["cmd"],
                    },
                }
            ],
        }
        _, context = build_chat_request(body)
        patch = (
            "*** Begin Patch\n"
            "*** Add File: quoted.txt\n"
            "+single ' double \" dollar $(touch SENTINEL) backtick `touch SENTINEL` ; & |\n"
            "*** End Patch"
        )

        result, state = build_responses_result(
            body,
            {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_safe_patch",
                                    "type": "function",
                                    "function": {
                                        "name": "apply_patch",
                                        "arguments": json.dumps(
                                            {"patch": patch, "workdir": "/tmp/authorized repo"}
                                        ),
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
            context,
            response_id="resp_safe_patch",
            created_at=123,
        )

        call = result["output"][0]
        arguments = json.loads(call["arguments"])
        self.assertEqual(call["type"], "function_call")
        self.assertEqual(call["name"], "exec_command")
        self.assertEqual(arguments["workdir"], "/tmp/authorized repo")
        self.assertEqual(arguments["cmd"], f"apply_patch {shlex.quote(patch)}")
        self.assertEqual(shlex.split(arguments["cmd"]), ["apply_patch", patch])
        self.assertEqual(state["tool_types"]["apply_patch"], "safe_exec_apply_patch")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            capture = root / "captured-patch"
            sentinel = root / "SENTINEL"
            shim = root / "apply_patch"
            shim.write_text('#!/bin/sh\nprintf %s "$1" > "$CAPTURE_FILE"\n')
            shim.chmod(0o700)
            environment = dict(os.environ)
            environment["PATH"] = f"{root}{os.pathsep}{environment.get('PATH', '')}"
            environment["CAPTURE_FILE"] = str(capture)

            subprocess.run(
                ["/bin/sh", "-c", arguments["cmd"]],
                cwd=root,
                env=environment,
                check=True,
            )

            self.assertEqual(capture.read_text(), patch)
            self.assertFalse(sentinel.exists())

    def test_build_responses_result_rejects_invalid_safe_patch_arguments(self):
        body = {
            "model": "deepseek-v4-flash",
            "input": "Implement the bounded change.",
            "tools": [
                {
                    "type": "function",
                    "name": "exec_command",
                    "parameters": {"type": "object", "properties": {}},
                }
            ],
        }
        _, context = build_chat_request(body)

        invalid_arguments = (
            "not-json",
            json.dumps({"patch": "missing markers", "workdir": "/tmp/repo"}),
            json.dumps({"patch": "*** Begin Patch\n*** End Patch", "workdir": ""}),
            json.dumps({"patch": "*** Begin Patch\n\x00*** End Patch", "workdir": "/tmp/repo"}),
        )
        for arguments in invalid_arguments:
            with self.subTest(arguments=arguments), self.assertRaises(ProtocolError):
                build_responses_result(
                    body,
                    {
                        "choices": [
                            {
                                "message": {
                                    "content": "",
                                    "tool_calls": [
                                        {
                                            "id": "call_invalid_patch",
                                            "type": "function",
                                            "function": {
                                                "name": "apply_patch",
                                                "arguments": arguments,
                                            },
                                        }
                                    ],
                                }
                            }
                        ]
                    },
                    context,
                )

    def test_build_chat_request_drops_unknown_custom_tools(self):
        payload, _ = build_chat_request(
            {
                "model": "deepseek-v4-flash",
                "input": "Do not expose arbitrary custom tools.",
                "tools": [{"type": "custom", "name": "dangerous_writer"}],
            }
        )

        self.assertNotIn("tools", payload)

    def test_build_responses_result_maps_apply_patch_back_to_custom_tool_call(self):
        body = {
            "model": "deepseek-v4-flash",
            "input": "Implement the requested change.",
            "tools": [
                {
                    "type": "custom",
                    "name": "apply_patch",
                    "description": "Apply a patch.",
                }
            ],
        }
        _, context = build_chat_request(body)

        result, state = build_responses_result(
            body,
            {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_patch",
                                    "type": "function",
                                    "function": {
                                        "name": "apply_patch",
                                        "arguments": json.dumps(
                                            {"patch": "*** Begin Patch\n*** End Patch"}
                                        ),
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
            context,
            response_id="resp_patch",
            created_at=123,
        )

        call = result["output"][0]
        self.assertEqual(call["type"], "custom_tool_call")
        self.assertEqual(call["name"], "apply_patch")
        self.assertEqual(call["input"], "*** Begin Patch\n*** End Patch")
        self.assertEqual(state["tool_types"], {"apply_patch": "custom"})

        continuation, _ = build_chat_request(
            {
                "model": "deepseek-v4-flash",
                "previous_response_id": "resp_patch",
                "input": [
                    {
                        "type": "custom_tool_call_output",
                        "call_id": "call_patch",
                        "output": "Done!",
                    }
                ],
            },
            previous=state,
        )
        self.assertEqual(continuation["messages"][-1]["tool_call_id"], "call_patch")

    def test_build_chat_request_continues_a_tool_call_and_preserves_reasoning(self):
        previous = {
            "messages": [
                {"role": "system", "content": "scope"},
                {"role": "user", "content": "read file"},
                {
                    "role": "assistant",
                    "content": "",
                    "reasoning_content": "need a tool",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "functions_exec", "arguments": "{\"path\":\"a\"}"},
                        }
                    ],
                },
            ],
            "pending_call_ids": ["call_1"],
        }
        body = {
            "model": "deepseek-v4-flash",
            "previous_response_id": "resp_1",
            "input": [
                {"type": "function_call_output", "call_id": "call_1", "output": "file contents"}
            ],
        }

        payload, _ = build_chat_request(body, previous=previous)

        self.assertEqual(payload["messages"][-2]["reasoning_content"], "need a tool")
        self.assertEqual(
            payload["messages"][-1],
            {"role": "tool", "tool_call_id": "call_1", "content": "file contents"},
        )

    def test_build_chat_request_rejects_orphan_tool_output(self):
        body = {
            "model": "deepseek-v4-flash",
            "input": [{"type": "function_call_output", "call_id": "missing", "output": "x"}],
        }
        with self.assertRaisesRegex(ProtocolError, "previous response"):
            build_chat_request(body)

    def test_tool_definitions_and_name_mapping_survive_continuation(self):
        first_body = {
            "model": "deepseek-v4-flash",
            "input": "inspect",
            "tools": [
                {
                    "type": "function",
                    "name": "functions.exec",
                    "parameters": {"type": "object", "properties": {}},
                }
            ],
        }
        _, first_context = build_chat_request(first_body)
        _, first_state = build_responses_result(
            first_body,
            {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {"name": "functions_exec", "arguments": "{}"},
                                }
                            ],
                        }
                    }
                ]
            },
            first_context,
            response_id="resp_1",
            created_at=100,
        )
        second_body = {
            "model": "deepseek-v4-flash",
            "previous_response_id": "resp_1",
            "input": [{"type": "function_call_output", "call_id": "call_1", "output": "ok"}],
        }

        second_payload, second_context = build_chat_request(second_body, previous=first_state)
        second_result, _ = build_responses_result(
            second_body,
            {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_2",
                                    "type": "function",
                                    "function": {"name": "functions_exec", "arguments": "{}"},
                                }
                            ],
                        }
                    }
                ]
            },
            second_context,
            response_id="resp_2",
            created_at=101,
        )

        self.assertEqual(second_payload["tools"][0]["function"]["name"], "functions_exec")
        self.assertEqual(second_result["output"][0]["name"], "functions.exec")

    def test_build_responses_result_maps_tool_call_and_records_replay_state(self):
        body = {"model": "deepseek-v4-flash", "input": "inspect"}
        _, context = build_chat_request(body)
        chat_response = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "I will inspect it.",
                        "reasoning_content": "tool required",
                        "tool_calls": [
                            {
                                "id": "call_7",
                                "type": "function",
                                "function": {
                                    "name": "functions_exec",
                                    "arguments": "{\"cmd\":\"pwd\"}",
                                },
                            }
                        ],
                    }
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13},
        }

        result, state = build_responses_result(
            body,
            chat_response,
            context,
            response_id="resp_7",
            created_at=123,
        )

        self.assertEqual(result["id"], "resp_7")
        self.assertEqual(result["output"][0]["type"], "reasoning")
        self.assertEqual(result["output"][1]["type"], "function_call")
        self.assertEqual(result["output"][1]["call_id"], "call_7")
        self.assertFalse(any(item.get("type") == "message" for item in result["output"]))
        self.assertEqual(state["pending_call_ids"], ["call_7"])
        self.assertEqual(state["messages"][-1]["reasoning_content"], "tool required")
        self.assertEqual(result["usage"]["input_tokens"], 10)

    def test_build_responses_result_maps_final_text(self):
        body = {"model": "deepseek-v4-flash", "input": "inspect"}
        _, context = build_chat_request(body)
        result, state = build_responses_result(
            body,
            {"choices": [{"message": {"content": "done"}}]},
            context,
            response_id="resp_done",
            created_at=456,
        )

        message = result["output"][0]
        self.assertEqual(message["type"], "message")
        self.assertEqual(message["content"][0]["text"], "done")
        self.assertEqual(state["pending_call_ids"], [])

    def test_encode_sse_emits_codex_responses_event_sequence(self):
        body = {"model": "deepseek-v4-flash", "input": "inspect"}
        _, context = build_chat_request(body)
        result, _ = build_responses_result(
            body,
            {"choices": [{"message": {"content": "done"}}]},
            context,
            response_id="resp_stream",
            created_at=789,
        )

        wire = encode_sse(result).decode("utf-8")

        self.assertIn("event: response.created", wire)
        self.assertIn("event: response.output_text.delta", wire)
        self.assertIn("event: response.completed", wire)
        self.assertTrue(wire.endswith("data: [DONE]\n\n"))
        completed_data = [
            line.removeprefix("data: ")
            for line in wire.splitlines()
            if line.startswith("data: {") and '"type":"response.completed"' in line
        ]
        self.assertEqual(json.loads(completed_data[0])["response"]["id"], "resp_stream")

    def test_encode_sse_numbers_every_json_event_monotonically(self):
        body = {"model": "deepseek-v4-flash", "input": "inspect"}
        _, context = build_chat_request(body)
        result, _ = build_responses_result(
            body,
            {"choices": [{"message": {"content": "done"}}]},
            context,
            response_id="resp_sequence",
            created_at=790,
        )

        events = [
            json.loads(line[6:])
            for line in encode_sse(result).decode().splitlines()
            if line.startswith("data: {")
        ]
        self.assertEqual([event["sequence_number"] for event in events], list(range(1, len(events) + 1)))
        self.assertTrue(all(event["response_id"] == "resp_sequence" for event in events))


if __name__ == "__main__":
    unittest.main()
