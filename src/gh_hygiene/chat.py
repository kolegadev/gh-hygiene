"""LLM conversation loop with tool calling and safety gate.

The chat module handles:
  1. Sending user messages + conversation history to DeepSeek
  2. Processing tool calls from the LLM response
  3. Safety gate: destructive tools must use dry_run=True first
  4. User confirmation for destructive actions
  5. Executing tool functions and feeding results back to the LLM
"""

from __future__ import annotations

import json
from typing import Any, Optional

from openai import OpenAI

from .tools import (
    TOOL_DEFINITIONS,
    get_tool_function,
    is_destructive,
    is_read_only,
)
from .display import (
    console,
    print_markdown,
    print_info,
    print_error,
    print_warning,
    print_panel,
    confirm_action,
)

SYSTEM_PROMPT = """You are a GitHub repo hygiene assistant with access to the user's GitHub account (~120 repos).

Your job is to help the user manage their repos through natural conversation. They may not know GitHub well, so explain things clearly.

## Available capabilities:
- **Repo management**: list all repos, audit for issues, archive stale repos, change repo visibility (public/private)
- **File management**: find clutter files (.DS_Store, large files, merge conflicts), clean them up
- **Issue/PR hygiene**: find stale issues, close them, clean up merged PR branches, audit labels
- **Shell fallback**: run arbitrary shell commands (including gh CLI, git commands) when no dedicated tool covers the user's request — always preview first with dry_run=True

## Safety rules (CRITICAL):
1. **Always preview before acting**: For ANY destructive action (archive, delete, close, clean), you MUST call the tool with dry_run=True FIRST. Show the preview to the user and wait for explicit confirmation before calling with dry_run=False.
2. **Never guess**: If you're unsure about what the user wants, ask clarifying questions.
3. **Be transparent**: Always explain what you're doing and why before calling tools.
4. **Batch wisely**: When operating across many repos, batch read-only operations but pause for confirmation on each destructive batch.

## Response style:
- Be conversational, helpful, and concise — not robotic.
- Present findings in clear, scannable formats.
- When suggesting actions, give the user clear numbered options.
- If the user's request is ambiguous, ask one clarifying question at a time.
- Never perform destructive actions without the user explicitly agreeing.

## Context:
- The user has many repos (potentially 100+). Be efficient.
- You are running in a terminal. Output is plain text with Rich formatting.
"""


class ChatSession:
    """Manages a conversation with the LLM, including tool calls."""

    def __init__(self, api_key: str, client: Any):
        """
        Args:
            api_key: DeepSeek API key
            client: GitHubClient instance
        """
        self._llm = OpenAI(api_key=api_key, base_url="https://api.deepseek.com", timeout=60.0)
        self._gh_client = client
        self._messages: list[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT},
        ]
        self._pending_destructive: dict[str, dict] = {}
        # Track tool calls awaiting confirmation: tool_call_id -> {"tool_name": ..., "args": ...}

    def send_message(self, user_text: str) -> str:
        """Process a user message and return the assistant's response text.

        This may trigger multiple LLM round-trips if tool calls are involved.
        """
        self._messages.append({"role": "user", "content": user_text})
        return self._run_loop()

    def _run_loop(self, max_iterations: int = 10) -> str:
        """Core conversation loop: send to LLM, handle tool calls, repeat."""
        for _ in range(max_iterations):
            response = self._call_llm()

            if response is None:
                print_error("Failed to get response from DeepSeek. Check your API key and connection.")
                return "Error: No response from LLM."

            message = response.choices[0].message

            # Text reply — just return it
            if message.content and not message.tool_calls:
                self._messages.append({"role": "assistant", "content": message.content})
                return message.content

            # Tool calls — process them
            if message.tool_calls:
                # Add assistant message with tool calls to history
                self._messages.append({
                    "role": "assistant",
                    "content": message.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in message.tool_calls
                    ],
                })

                # Process each tool call
                tool_results = []
                for tc in message.tool_calls:
                    result = self._handle_tool_call(tc.id, tc.function.name, tc.function.arguments)
                    tool_results.append(result)

                # If any destructive tool was deferred for confirmation, pause the loop
                # and return the preview message to the user
                if self._pending_destructive:
                    preview_text = message.content or "Here's a preview of what I'd like to do."
                    return preview_text

                # Feed tool results back to LLM
                for tc, result in zip(message.tool_calls, tool_results):
                    self._messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(result, default=str),
                    })

                # Continue the loop — LLM will get results and respond
                continue

            # Fallback
            return message.content or ""

        return "I've reached the maximum number of steps. Let's continue from where we left off."

    def confirm_pending(self) -> str:
        """User confirmed the pending destructive action. Execute it and continue."""
        if not self._pending_destructive:
            return "No pending actions to confirm."

        # Execute all pending destructive actions with dry_run=False
        results = []
        for tc_id, info in self._pending_destructive.items():
            tool_name = info["tool_name"]
            args = dict(info["args"])
            args["dry_run"] = False

            fn = get_tool_function(tool_name)
            if fn:
                try:
                    result = fn(**args)
                    results.append({"tool_name": tool_name, "result": "success"})
                except Exception as e:
                    results.append({"tool_name": tool_name, "result": "error", "error": str(e)})
                    print_error(f"Failed to execute {tool_name}: {e}")
            else:
                results.append({"tool_name": tool_name, "result": "error", "error": "function not found"})

        # Feed results back
        for tc_id, result in zip(self._pending_destructive.keys(), results):
            self._messages.append({
                "role": "tool",
                "tool_call_id": tc_id,
                "content": json.dumps(result, default=str),
            })

        self._pending_destructive.clear()

        # Continue the loop
        return self._run_loop()

    def reject_pending(self) -> str:
        """User rejected the pending destructive action. Tell LLM and continue."""
        if not self._pending_destructive:
            return "No pending actions to reject."

        for tc_id, info in self._pending_destructive.items():
            self._messages.append({
                "role": "tool",
                "tool_call_id": tc_id,
                "content": json.dumps({
                    "status": "rejected",
                    "message": f"User declined to execute {info['tool_name']}.",
                }),
            })

        self._pending_destructive.clear()

        # Continue the loop for LLM to respond to the rejection
        return self._run_loop()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _call_llm(self):
        """Make a single call to the DeepSeek API (non-streaming)."""
        try:
            return self._llm.chat.completions.create(
                model="deepseek-chat",
                messages=self._messages,
                tools=TOOL_DEFINITIONS,
                tool_choice="auto",
                temperature=0.7,
                max_tokens=8192,
            )
        except Exception as e:
            print_error(f"LLM API error: {e}")
            return None

    def _call_llm_streaming(self):
        """Stream a response from the DeepSeek API. Yields chunks."""
        try:
            return self._llm.chat.completions.create(
                model="deepseek-chat",
                messages=self._messages,
                tools=TOOL_DEFINITIONS,
                tool_choice="auto",
                temperature=0.7,
                max_tokens=8192,
                stream=True,
            )
        except Exception as e:
            print_error(f"LLM API error: {e}")
            return None

    def _handle_tool_call(self, tool_call_id: str, tool_name: str, arguments_str: str) -> dict:
        """Process a single tool call from the LLM.

        Returns the result dict that will be fed back to the LLM.
        For destructive tools, may store the call in _pending_destructive
        and return a preview result.
        """
        try:
            args = json.loads(arguments_str)
        except json.JSONDecodeError:
            return {"error": f"Invalid JSON arguments: {arguments_str}"}

        fn = get_tool_function(tool_name)
        if not fn:
            return {"error": f"Unknown tool: {tool_name}"}

        print_info(f"Calling tool: {tool_name}")

        # Read-only tools: execute immediately
        if is_read_only(tool_name):
            try:
                result = fn(**args)
                # Truncate large results for the LLM
                return self._truncate_result(result)
            except Exception as e:
                print_error(f"Tool {tool_name} failed: {e}")
                return {"error": str(e)}

        # Destructive tools
        if is_destructive(tool_name):
            dry_run = args.get("dry_run", True)

            if dry_run:
                # Run the dry run preview
                try:
                    result = fn(**args)
                    # Store for later confirmation
                    self._pending_destructive[tool_call_id] = {
                        "tool_name": tool_name,
                        "args": args,
                    }
                    return {
                        "status": "dry_run_preview",
                        "tool": tool_name,
                        "preview": self._truncate_result(result),
                        "message": f"Preview ready. Ask the user if they want to proceed with {tool_name}.",
                    }
                except Exception as e:
                    print_error(f"Tool {tool_name} dry run failed: {e}")
                    return {"error": str(e)}
            else:
                # dry_run=False — this should only happen after confirmation
                try:
                    result = fn(**args)
                    return {"status": "executed", "tool": tool_name, "result": self._truncate_result(result)}
                except Exception as e:
                    print_error(f"Tool {tool_name} failed: {e}")
                    return {"error": str(e)}

        return {"error": f"Unknown tool category for: {tool_name}"}

    def _truncate_result(self, result: Any, max_items: int = 50) -> Any:
        """Truncate large results to avoid blowing up the LLM context."""
        if isinstance(result, list) and len(result) > max_items:
            return result[:max_items] + [
                {"truncated": True, "total_items": len(result), "shown": max_items}
            ]
        if isinstance(result, dict) and "repos" in result:
            repos = result["repos"]
            if isinstance(repos, dict) and len(repos) > max_items:
                keys = list(repos.keys())[:max_items]
                result["repos"] = {k: repos[k] for k in keys}
                result["truncated"] = True
                result["total_repos"] = len(repos)
        return result


# ---------------------------------------------------------------------------
# One-shot mode
# ---------------------------------------------------------------------------

def run_one_shot(
    api_key: str,
    gh_client: Any,
    instruction: str,
) -> None:
    """Run a single instruction through the LLM and execute immediately.

    For one-shot mode, destructive operations are shown as dry runs only.
    The user must use chat mode for actual destructive actions.
    """
    session = ChatSession(api_key, gh_client)

    # Override the _handle_tool_call to force dry_run for destructive tools
    original_handle = session._handle_tool_call

    def safe_handle(tool_call_id, tool_name, arguments_str):
        if is_destructive(tool_name):
            try:
                args = json.loads(arguments_str)
            except json.JSONDecodeError:
                return {"error": "Invalid JSON arguments"}
            args["dry_run"] = True
            arguments_str = json.dumps(args)
        return original_handle(tool_call_id, tool_name, arguments_str)

    session._handle_tool_call = safe_handle

    response = session.send_message(instruction)
    print_markdown(response)
