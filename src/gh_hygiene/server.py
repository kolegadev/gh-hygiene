"""FastAPI web server for gh-hygiene chat UI.

Provides a WebSocket endpoint that wraps the ChatSession,
plus a lightweight REST API for status/health checks.

Start with: gh-hygiene serve
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Body
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from .auth import get_github_token, get_deepseek_api_key, get_token_source, get_github_username, store_github_token, store_deepseek_key
from .client import GitHubClient
from .chat import ChatSession
from .tools import register_tool, run_shell_command
from .commands.repos import list_repos, audit_repos, archive_repos, change_visibility
from .commands.files import audit_files, clean_files, reorganize_files
from .commands.hygiene import (
    list_stale_issues,
    close_stale_issues,
    clean_pr_branches,
    audit_labels,
)
from .display import print_info, print_error, print_success

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(title="gh-hygiene", version="0.1.0")

# Path to the single-page frontend
UI_DIR = Path(__file__).parent / "ui"
UI_DIR.mkdir(exist_ok=True)


@app.get("/")
async def root():
    """Serve the chat UI from disk file (updated by cli serve or _generate_ui)."""
    index_path = UI_DIR / "index.html"
    content = index_path.read_text() if index_path.exists() else _get_ui_html()
    headers = {
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
        "Expires": "0",
    }
    return HTMLResponse(content=content, headers=headers)


@app.get("/api/health")
async def health():
    """Health check endpoint."""
    gh_configured = get_github_token() is not None
    ds_configured = get_deepseek_api_key() is not None
    return {
        "status": "ok",
        "github_configured": gh_configured,
        "deepseek_configured": ds_configured,
        "github_source": get_token_source() if gh_configured else "none",
    }


@app.get("/api/account")
async def account():
    """Return the current GitHub account info."""
    username = get_github_username()
    return {
        "github_user": username,
        "github_configured": username is not None,
        "deepseek_configured": get_deepseek_api_key() is not None,
        "github_source": get_token_source(),
    }


@app.post("/api/settings")
async def update_settings(data: dict = Body(...)):
    """Update GitHub PAT and/or DeepSeek API key."""
    gh_token = (data.get("github_token") or "").strip()
    ds_key = (data.get("deepseek_key") or "").strip()

    updates = []
    if gh_token:
        store_github_token(gh_token)
        updates.append("github_token")
    if ds_key:
        store_deepseek_key(ds_key)
        updates.append("deepseek_key")

    return {
        "status": "ok",
        "updated": updates,
        "message": "Settings saved. Reconnect to use new credentials.",
    }


# ---------------------------------------------------------------------------
# WebSocket chat
# ---------------------------------------------------------------------------


class WebSocketChatSession:
    """Wraps ChatSession for WebSocket communication.

    Instead of reading from stdin and writing to stdout,
    this class yields events through a WebSocket.
    Supports intent fast-paths for common queries.
    """

    def __init__(self, ws: WebSocket, session: ChatSession, client: Any = None):
        self.ws = ws
        self.session = session
        self.client = client
        self._confirm_future: Optional[asyncio.Future] = None
        self._cancel_event = asyncio.Event()
        self._is_processing = False

    async def run(self):
        """Main loop: listen for client messages, process through LLM."""
        try:
            while True:
                data = await self.ws.receive_text()
                msg = json.loads(data)
                msg_type = msg.get("type", "message")

                if msg_type == "message":
                    await self._handle_message(msg.get("content", ""))

                elif msg_type == "confirm":
                    await self._handle_confirm()

                elif msg_type == "reject":
                    await self._handle_reject()

                elif msg_type == "stop":
                    if self._is_processing:
                        self._cancel_event.set()
                        await self._send({"type": "status", "content": "Cancelling..."})

                elif msg_type == "ping":
                    await self.ws.send_text(json.dumps({"type": "pong"}))

        except WebSocketDisconnect:
            pass

    async def _handle_message(self, content: str):
        """Send user message, checking for intent fast-paths first."""
        self._is_processing = True
        self._cancel_event.clear()

        try:
            # Try intent fast-path for common queries
            if self.client:
                from .intents import detect_intent
                intent = detect_intent(content)

                if intent == "list_repos":
                    await self._fast_list_repos(content)
                    return

                if intent == "audit_repos":
                    # Fall through to LLM for audit (needs LLM analysis)
                    pass

            # Default: use LLM
            self.session._messages.append({"role": "user", "content": content})
            await self._run_llm_loop()
        finally:
            self._is_processing = False

    async def _fast_list_repos(self, user_message: str):
        """Fast-path: return cached repo list directly, then LLM follow-up."""
        try:
            # Get cached repos (pre-fetched on connect)
            repos = await asyncio.to_thread(self.client.get_repo_dicts_cached)

            # Send tool_result directly to UI
            repo_list = []
            for r in repos:
                repo_list.append({
                    "name": r["name"],
                    "description": r.get("description", ""),
                    "visibility": r["visibility"],
                    "language": r.get("language", ""),
                    "last_push": r["last_push"],
                    "archived": r["archived"],
                    "stars": r.get("stars", 0),
                })

            await self._send({
                "type": "tool_result",
                "tool": "list_repos",
                "total": len(repo_list),
                "content": repo_list,
                "message": f"Found {len(repo_list)} repositories across your account.",
            })

            # Fast LLM follow-up: just a natural language confirmation
            self.session._messages.append({"role": "user", "content": user_message})
            self.session._messages.append({
                "role": "system",
                "content": f"You just showed the user a list of their {len(repo_list)} GitHub repos as cards. Say ONE brief sentence acknowledging this (e.g., mention a notable stat like how many are public/private or the most common language). Do NOT list repos again.",
            })
            await self._run_llm_loop(max_iterations=2)

        except Exception as e:
            await self._send({"type": "error", "content": f"Failed to fetch repos: {e}"})

    async def _run_llm_loop(self, max_iterations: int = 50):
        """Core LLM loop adapted for WebSocket, with cancellation support."""
        for i in range(max_iterations):
            await self._send({"type": "thinking", "content": f"Step {i+1}/{max_iterations}..."})

            if self._cancel_event.is_set():
                self.session._messages.append({
                    "role": "system",
                    "content": "The user interrupted this task. Acknowledge and ask what they'd like to do next."
                })
                response = await asyncio.to_thread(self.session._call_llm)
                if response and response.choices[0].message.content:
                    self.session._messages.append({"role": "assistant", "content": response.choices[0].message.content})
                    await self._send({"type": "text", "content": response.choices[0].message.content})
                await self._send({"type": "cancelled", "content": "Task interrupted."})
                return

            try:
                response = await asyncio.wait_for(
                    asyncio.to_thread(self.session._call_llm),
                    timeout=90.0
                )
            except asyncio.TimeoutError:
                await self._send({"type": "error", "content": "DeepSeek API timed out after 90s. The query may be too complex — try breaking it into smaller steps."})
                return

            if response is None:
                await self._send({"type": "error", "content": "Failed to get response from DeepSeek. Check your API key."})
                return

            message = response.choices[0].message
            finish_reason = response.choices[0].finish_reason

            # Text reply — send to client and return
            if message.content and not message.tool_calls:
                self.session._messages.append({"role": "assistant", "content": message.content})
                await self._send({"type": "text", "content": message.content})
                return

            # Tool calls
            if message.tool_calls:
                # Send tool call info to client
                tool_info = []
                for tc in message.tool_calls:
                    try:
                        args = json.loads(tc.function.arguments)
                    except json.JSONDecodeError:
                        args = {}
                    tool_info.append({
                        "id": tc.id,
                        "name": tc.function.name,
                        "args": args,
                    })

                await self._send({
                    "type": "tool_calls",
                    "content": message.content or "",
                    "tools": tool_info,
                })

                # Add assistant message to history
                self.session._messages.append({
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
                all_read_only = True
                for tc in message.tool_calls:
                    if self._cancel_event.is_set():
                        await self._send({"type": "cancelled", "content": "Task interrupted."})
                        return

                    await self._send({"type": "thinking", "content": f"Running {tc.function.name}..."})
                    try:
                        result = await asyncio.wait_for(
                            asyncio.to_thread(
                                self.session._handle_tool_call,
                                tc.id, tc.function.name, tc.function.arguments
                            ),
                            timeout=120.0
                        )
                    except asyncio.TimeoutError:
                        await self._send({"type": "error", "content": f"Tool {tc.function.name} timed out after 120s."})
                        result = {"error": f"Tool timed out after 120s"}

                    # Check if this is a destructive tool waiting for confirmation
                    if self.session._pending_destructive:
                        all_read_only = False
                        tools = [info["tool_name"] for info in self.session._pending_destructive.values()]
                        await self._send({
                            "type": "confirm_request",
                            "content": f"Ready to execute: {', '.join(tools)}",
                            "tools": tools,
                        })
                        return  # Pause and wait for confirmation

                    # Feed result back
                    self.session._messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(result, default=str),
                    })

                # If all tools were read-only, continue the loop
                if all_read_only:
                    await self._send({
                        "type": "thinking",
                        "content": "Tools executed, continuing...",
                    })

                    if self._cancel_event.is_set():
                        await self._send({"type": "cancelled", "content": "Task interrupted."})
                        return

                    continue
                else:
                    return

            # Neither text nor tool calls — LLM stopped silently
            if finish_reason == "length":
                self.session._messages.append({
                    "role": "system",
                    "content": "Your previous response was cut off (max tokens reached). Continue from where you left off."
                })
                await self._send({"type": "thinking", "content": "Response truncated, continuing..."})
                continue
            elif finish_reason == "stop":
                # LLM thinks it's done but returned no content — ask it to produce something
                self.session._messages.append({
                    "role": "system",
                    "content": "You stopped without producing any output. If you're done with the task, summarize what happened. Otherwise, continue."
                })
                await self._send({"type": "thinking", "content": "LLM stopped — asking to continue..."})
                continue
            else:
                await self._send({"type": "error", "content": f"LLM returned empty response (finish_reason={finish_reason}). Try asking again."})
                return

            return

        # Max iterations reached — ask LLM to summarize and wrap up
        self.session._messages.append({
            "role": "system",
            "content": "You've reached the maximum number of tool-calling steps for this request. Do NOT call any more tools. Summarize what you've accomplished so far, what's pending, and suggest the user ask a follow-up question to continue."
        })
        response = await asyncio.to_thread(self.session._call_llm)
        if response and response.choices[0].message.content:
            self.session._messages.append({"role": "assistant", "content": response.choices[0].message.content})
            await self._send({"type": "text", "content": response.choices[0].message.content})
        else:
            await self._send({"type": "error", "content": "Reached maximum steps. Please continue with a follow-up question."})

    async def _handle_confirm(self):
        """User confirmed pending destructive actions."""
        if not self.session._pending_destructive:
            await self._send({"type": "error", "content": "No pending actions to confirm."})
            return

        # Execute all pending actions
        results = []
        for tc_id, info in self.session._pending_destructive.items():
            tool_name = info["tool_name"]
            args = dict(info["args"])
            args["dry_run"] = False

            fn = register_tool.__wrapped__ if hasattr(register_tool, '__wrapped__') else None
            from .tools import get_tool_function
            fn = get_tool_function(tool_name)
            if fn:
                try:
                    result = await asyncio.to_thread(fn, **args)
                    results.append({"tool_name": tool_name, "result": "success"})
                    await self._send({
                        "type": "status",
                        "content": f"✅ {tool_name} completed successfully.",
                    })
                except Exception as e:
                    results.append({"tool_name": tool_name, "result": "error", "error": str(e)})
                    await self._send({
                        "type": "error",
                        "content": f"Failed to execute {tool_name}: {e}",
                    })
            else:
                results.append({"tool_name": tool_name, "result": "error", "error": "function not found"})

        # Feed results back
        for tc_id, result in zip(self.session._pending_destructive.keys(), results):
            self.session._messages.append({
                "role": "tool",
                "tool_call_id": tc_id,
                "content": json.dumps(result, default=str),
            })

        self.session._pending_destructive.clear()

        # Continue the LLM loop
        await self._run_llm_loop()

    async def _handle_reject(self):
        """User rejected pending destructive actions."""
        if not self.session._pending_destructive:
            await self._send({"type": "error", "content": "No pending actions to reject."})
            return

        for tc_id, info in self.session._pending_destructive.items():
            self.session._messages.append({
                "role": "tool",
                "tool_call_id": tc_id,
                "content": json.dumps({
                    "status": "rejected",
                    "message": f"User declined to execute {info['tool_name']}.",
                }),
            })

        self.session._pending_destructive.clear()
        await self._send({"type": "status", "content": "❌ Action cancelled."})

        # Continue the LLM loop
        await self._run_llm_loop()

    async def _send(self, data: dict):
        """Send JSON data through the WebSocket."""
        await self.ws.send_text(json.dumps(data, default=str))


@app.websocket("/ws/chat")
async def websocket_chat(ws: WebSocket):
    """WebSocket endpoint for chat with caching and intent fast-paths."""
    await ws.accept()

    # Check auth
    gh_token = get_github_token()
    ds_key = get_deepseek_api_key()

    if not gh_token or not ds_key:
        await ws.send_text(json.dumps({
            "type": "error",
            "content": "Authentication not configured. Run 'gh-hygiene auth setup' first.",
        }))
        await ws.close()
        return

    # Set up session
    client = GitHubClient(gh_token)
    _register_tools_for_ui(client)

    # Pre-fetch repos in background on connect
    asyncio.create_task(_prefetch_repos(client))

    session = ChatSession(ds_key, client)
    ws_session = WebSocketChatSession(ws, session, client)

    # Send connected info with account name
    gh_user = client.user.login if client.user else "unknown"
    await ws.send_text(json.dumps({
        "type": "connected",
        "github_user": gh_user,
        "content": f"👋 Hello! I'm connected to your GitHub account as **{gh_user}**. I can help you manage your repos — just tell me what you need.",
    }))

    await ws_session.run()


async def _prefetch_repos(client: GitHubClient):
    """Pre-fetch repos in background to warm the cache."""
    try:
        await asyncio.to_thread(client.get_all_repos_cached)
    except Exception:
        pass


def _register_tools_for_ui(client: GitHubClient):
    """Register tool function implementations for the web UI."""
    register_tool("list_repos", lambda **kw: list_repos(client, **kw))
    register_tool("audit_repos", lambda **kw: audit_repos(client, **kw))
    register_tool("archive_repos", lambda **kw: archive_repos(client, **kw))
    register_tool("change_visibility", lambda **kw: change_visibility(client, **kw))
    register_tool("audit_files", lambda **kw: audit_files(client, **kw))
    register_tool("clean_files", lambda **kw: clean_files(client, **kw))
    register_tool("reorganize_files", lambda **kw: reorganize_files(client, **kw))
    register_tool("list_stale_issues", lambda **kw: list_stale_issues(client, **kw))
    register_tool("close_stale_issues", lambda **kw: close_stale_issues(client, **kw))
    register_tool("clean_pr_branches", lambda **kw: clean_pr_branches(client, **kw))
    register_tool("audit_labels", lambda **kw: audit_labels(client, **kw))
    register_tool("get_rate_limit", lambda **kw: client.get_rate_limit())
    register_tool("run_shell_command", lambda **kw: run_shell_command(**kw))


# ---------------------------------------------------------------------------
# Server launcher
# ---------------------------------------------------------------------------


def start_server(host: str = "127.0.0.1", port: int = 8080):
    """Start the uvicorn server."""
    # Ensure the UI file exists (generate it from the package if needed)
    ui_path = UI_DIR / "index.html"
    if not ui_path.exists():
        _generate_ui(ui_path)

    print_success(f"🚀 gh-hygiene UI starting at http://{host}:{port}")
    print_info("Press Ctrl+C to stop.")

    uvicorn.run(
        "gh_hygiene.server:app",
        host=host,
        port=port,
        log_level="warning",
    )


def _generate_ui(path: Path):
    """Generate the default UI HTML file."""
    html = _get_ui_html()
    path.write_text(html)


def _get_ui_html() -> str:
    """Return the complete UI HTML."""
    return r'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>gh-hygiene v2 — Repo Manager</title>
<style>
  :root {
    --bg: #0d0d0d;
    --surface: #161616;
    --surface2: #1e1e1e;
    --border: #2a2a2a;
    --text: #d4d4d4;
    --text-dim: #6b6b6b;
    --accent: #d4a853;
    --accent-glow: rgba(212,168,83,0.15);
    --green: #4ec9b0;
    --red: #f44747;
    --blue: #569cd6;
    --radius: 8px;
    --font: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    --mono: "SF Mono", "JetBrains Mono", "Fira Code", monospace;
  }
  * { margin:0; padding:0; box-sizing:border-box; }
  body {
    font-family: var(--font);
    background: var(--bg);
    color: var(--text);
    height: 100vh;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }

  /* Header */
  header {
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    padding: 12px 20px;
    display: flex;
    align-items: center;
    gap: 12px;
    flex-shrink: 0;
  }
  header .logo {
    font-size: 18px;
    font-weight: 700;
    color: var(--accent);
    letter-spacing: -0.3px;
  }
  header .logo span { color: var(--text-dim); font-weight: 400; }
  header .status {
    margin-left: auto;
    font-size: 12px;
    display: flex;
    align-items: center;
    gap: 6px;
    color: var(--text-dim);
  }
  .status-dot {
    width: 7px; height: 7px;
    border-radius: 50%;
    background: var(--green);
    box-shadow: 0 0 6px rgba(78,201,176,0.4);
  }
  .status-dot.off { background: var(--red); box-shadow: 0 0 6px rgba(244,71,71,0.4); }

  /* Chat area */
  .chat-container {
    flex: 1;
    overflow-y: auto;
    padding: 20px;
    display: flex;
    flex-direction: column;
    gap: 16px;
  }
  .chat-container::-webkit-scrollbar { width: 6px; }
  .chat-container::-webkit-scrollbar-track { background: transparent; }
  .chat-container::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }

  /* Messages */
  .msg {
    display: flex;
    flex-direction: column;
    max-width: 85%;
    animation: fadeIn 0.25s ease;
  }
  @keyframes fadeIn { from { opacity:0; transform:translateY(6px); } to { opacity:1; transform:translateY(0); } }
  .msg.user { align-self: flex-end; }
  .msg.assistant { align-self: flex-start; }
  .msg.system { align-self: center; max-width: 100%; }

  .msg-bubble {
    padding: 12px 16px;
    border-radius: var(--radius);
    line-height: 1.55;
    font-size: 14px;
    word-wrap: break-word;
  }
  .msg.user .msg-bubble {
    background: var(--accent);
    color: #1a1a1a;
    border-bottom-right-radius: 2px;
  }
  .msg.assistant .msg-bubble {
    background: var(--surface2);
    border: 1px solid var(--border);
    border-bottom-left-radius: 2px;
  }
  .msg.system .msg-bubble {
    background: var(--surface);
    border: 1px solid var(--border);
    font-size: 13px;
    color: var(--text-dim);
    text-align: center;
    padding: 8px 16px;
  }
  .msg.system.error .msg-bubble {
    border-color: rgba(244,71,71,0.3);
    color: var(--red);
  }
  .msg-bubble p { margin-bottom: 8px; }
  .msg-bubble p:last-child { margin-bottom: 0; }
  .msg-bubble code {
    font-family: var(--mono);
    font-size: 13px;
    background: rgba(255,255,255,0.06);
    padding: 2px 6px;
    border-radius: 3px;
  }
  .msg-bubble pre {
    background: rgba(0,0,0,0.3);
    border-radius: 6px;
    padding: 12px;
    overflow-x: auto;
    margin: 8px 0;
    font-family: var(--mono);
    font-size: 12px;
    line-height: 1.5;
  }

  /* Tool call display */
  .tool-call {
    margin-top: 6px;
    border: 1px solid var(--border);
    border-radius: var(--radius);
    overflow: hidden;
    font-size: 13px;
  }
  .tool-call-header {
    background: var(--surface);
    padding: 8px 12px;
    display: flex;
    align-items: center;
    gap: 8px;
    cursor: pointer;
    user-select: none;
  }
  .tool-call-header:hover { background: var(--surface2); }
  .tool-call-icon { font-size: 14px; }
  .tool-call-name { font-family: var(--mono); color: var(--blue); font-size: 12px; }
  .tool-call-chevron { margin-left: auto; color: var(--text-dim); transition: transform 0.2s; }
  .tool-call.open .tool-call-chevron { transform: rotate(180deg); }
  .tool-call-body {
    display: none;
    padding: 10px 12px;
    background: rgba(0,0,0,0.2);
    font-family: var(--mono);
    font-size: 12px;
    white-space: pre-wrap;
    color: var(--text-dim);
    max-height: 200px;
    overflow-y: auto;
  }
  .tool-call.open .tool-call-body { display: block; }

  /* Confirmation bar */
  .confirm-bar {
    align-self: center;
    background: var(--surface);
    border: 1px solid var(--accent);
    border-radius: var(--radius);
    padding: 14px 20px;
    display: flex;
    align-items: center;
    gap: 14px;
    animation: fadeIn 0.25s ease;
  }
  .confirm-bar .text { font-size: 14px; color: var(--accent); flex: 1; }
  .confirm-bar button {
    padding: 8px 18px;
    border-radius: 5px;
    border: none;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.15s;
  }
  .btn-confirm {
    background: var(--accent);
    color: #1a1a1a;
  }
  .btn-confirm:hover { filter: brightness(1.1); }
  .btn-reject {
    background: transparent;
    color: var(--text-dim);
    border: 1px solid var(--border) !important;
  }
  .btn-reject:hover { color: var(--text); border-color: var(--text-dim) !important; }

  /* Input area */
  .input-container {
    background: var(--surface);
    border-top: 1px solid var(--border);
    padding: 14px 20px;
    display: flex;
    gap: 10px;
    flex-shrink: 0;
  }
  .input-container input {
    flex: 1;
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 12px 16px;
    color: var(--text);
    font-size: 14px;
    font-family: var(--font);
    outline: none;
    transition: border-color 0.15s;
  }
  .input-container input:focus { border-color: var(--accent); }
  .input-container input::placeholder { color: var(--text-dim); }
  .input-container button {
    background: var(--accent);
    color: #1a1a1a;
    border: none;
    border-radius: var(--radius);
    padding: 0 20px;
    font-size: 14px;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.15s;
  }
  .input-container button:hover { filter: brightness(1.1); }
  .input-container button:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }
  .input-container button.btn-stop {
    background: var(--red);
    animation: pulseStop 1.5s infinite;
  }
  @keyframes pulseStop {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.7; }
  }

  /* Settings modal */
  .settings-btn {
    background: none;
    border: 1px solid var(--border);
    border-radius: var(--radius);
    color: var(--text-dim);
    font-size: 16px;
    padding: 4px 8px;
    cursor: pointer;
    transition: all 0.15s;
    margin-right: 8px;
  }
  .settings-btn:hover { color: var(--text); border-color: var(--text-dim); }
  .modal-overlay {
    display: none;
    position: fixed;
    top: 0; left: 0; right: 0; bottom: 0;
    background: rgba(0,0,0,0.7);
    z-index: 100;
    align-items: center;
    justify-content: center;
  }
  .modal-overlay.open { display: flex; }
  .modal {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 24px;
    width: 90%;
    max-width: 440px;
    animation: fadeIn 0.2s ease;
  }
  .modal h2 { font-size: 18px; color: var(--accent); margin-bottom: 20px; }
  .modal label { font-size: 12px; color: var(--text-dim); display: block; margin-bottom: 4px; text-transform: uppercase; letter-spacing: 0.5px; }
  .modal input {
    width: 100%;
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 10px 12px;
    color: var(--text);
    font-size: 13px;
    font-family: var(--mono);
    margin-bottom: 14px;
    outline: none;
    transition: border-color 0.15s;
  }
  .modal input:focus { border-color: var(--accent); }
  .modal .account-info {
    background: var(--surface2);
    border-radius: var(--radius);
    padding: 10px 14px;
    margin-bottom: 16px;
    font-size: 13px;
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .modal .account-info .dot {
    width: 8px; height: 8px;
    border-radius: 50%;
    background: var(--green);
    flex-shrink: 0;
  }
  .modal .account-info .dot.off { background: var(--red); }
  .modal .account-info .label { color: var(--text-dim); }
  .modal .account-info .value { color: var(--accent); font-weight: 600; }
  .modal .account-info .source { color: var(--text-dim); font-size: 11px; }
  .modal .btn-row { display: flex; gap: 10px; justify-content: flex-end; margin-top: 6px; }
  .modal .btn-row button {
    padding: 8px 18px;
    border-radius: 5px;
    border: none;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.15s;
  }
  .modal .btn-save { background: var(--accent); color: #1a1a1a; }
  .modal .btn-save:hover { filter: brightness(1.1); }
  .modal .btn-cancel { background: transparent; color: var(--text-dim); border: 1px solid var(--border); }
  .modal .btn-cancel:hover { color: var(--text); }
  .modal .saved-msg {
    text-align: center;
    color: var(--green);
    font-size: 13px;
    margin-top: 10px;
    display: none;
  }

  /* Loading dots */
  .typing-dots {
    display: flex;
    gap: 4px;
    padding: 4px 0;
  }
  .typing-dots span {
    width: 6px; height: 6px;
    background: var(--text-dim);
    border-radius: 50%;
    animation: bounce 1.2s infinite;
  }
  .typing-dots span:nth-child(2) { animation-delay: 0.2s; }
  .typing-dots span:nth-child(3) { animation-delay: 0.4s; }
  @keyframes bounce { 0%,60%,100% { transform:translateY(0); } 30% { transform:translateY(-6px); } }
</style>
</head>
<body>

<header>
  <div class="logo">gh-hygiene <span>/ repo manager</span></div>
  <div style="background:var(--accent);color:#1a1a1a;font-size:10px;padding:2px 8px;border-radius:3px;font-weight:700;" id="version-tag">v2</div>
  <div class="status">
    <button class="settings-btn" id="settings-btn" title="Settings">⚙</button>
    <span id="github-user" style="color:var(--accent);font-weight:600;font-size:12px;margin-right:12px;"></span>
    <div class="status-dot" id="status-dot"></div>
    <span id="status-text">connecting...</span>
  </div>
</header>

<div class="chat-container" id="chat"></div>

<div class="input-container">
  <input id="msg-input" type="text" placeholder="Tell me what to do with your repos..." disabled />
  <button id="send-btn" disabled>Send</button>
</div>

<!-- Settings Modal -->
<div class="modal-overlay" id="modal-overlay">
  <div class="modal">
    <h2>Settings</h2>
    <div class="account-info" id="settings-account">
      <div class="dot" id="settings-dot"></div>
      <div>
        <div>Connected as <span class="value" id="settings-user">...</span></div>
        <div class="source" id="settings-source">loading...</div>
      </div>
    </div>
    <label>GitHub Personal Access Token</label>
    <input type="password" id="settings-gh-token" placeholder="ghp_... (leave blank to keep current)" />
    <label>DeepSeek API Key</label>
    <input type="password" id="settings-ds-key" placeholder="sk-... (leave blank to keep current)" />
    <div class="btn-row">
      <button class="btn-cancel" onclick="closeSettings()">Cancel</button>
      <button class="btn-save" onclick="saveSettings()">Save &amp; Reconnect</button>
    </div>
    <div class="saved-msg" id="saved-msg">Saved! Reconnecting...</div>
  </div>
</div>

<script>
// --- Debug banner ---
const DEBUG = document.createElement('div');
DEBUG.style.cssText = 'position:fixed;top:8px;right:8px;background:#111;color:#4ec9b0;font:10px monospace;padding:3px 8px;z-index:999;max-width:360px;max-height:24px;overflow:hidden;border-radius:4px;border:1px solid #2a2a2a;opacity:0.75;transition:max-height 0.3s;cursor:pointer;';
DEBUG.id = 'debug-log';
DEBUG.title = 'Click to expand';
DEBUG.onclick = function() {
  if (DEBUG.style.maxHeight === '200px') {
    DEBUG.style.maxHeight = '24px';
    DEBUG.title = 'Click to expand';
  } else {
    DEBUG.style.maxHeight = '200px';
    DEBUG.style.overflowY = 'auto';
    DEBUG.title = 'Click to collapse';
  }
};
const DBG_CLOSE = document.createElement('span');
DBG_CLOSE.textContent = '×';
DBG_CLOSE.style.cssText = 'position:absolute;top:1px;right:4px;cursor:pointer;color:#6b6b6b;font-size:12px;display:none;';
DBG_CLOSE.onclick = function(e) { e.stopPropagation(); DEBUG.style.display = 'none'; };
DEBUG.appendChild(DBG_CLOSE);
const DBG_TEXT = document.createElement('div');
DBG_TEXT.style.cssText = 'white-space:nowrap;overflow:hidden;text-overflow:ellipsis;';
DEBUG.appendChild(DBG_TEXT);
document.body.appendChild(DEBUG);

let _dbgTimeout = null;
function debug(msg) {
  DEBUG.style.display = 'block';
  DBG_TEXT.textContent = msg;
  if (msg.includes('FATAL') || msg.includes('ERROR')) {
    DEBUG.style.color = '#f44747';
    DEBUG.style.borderColor = 'rgba(244,71,71,0.4)';
    DEBUG.style.maxHeight = '200px';
    DEBUG.style.overflowY = 'auto';
    DBG_CLOSE.style.display = 'inline';
    DBG_TEXT.style.whiteSpace = 'pre-wrap';
    DBG_TEXT.style.wordBreak = 'break-all';
    if (_dbgTimeout) clearTimeout(_dbgTimeout);
  } else if (msg.includes('WS onopen fired')) {
    if (_dbgTimeout) clearTimeout(_dbgTimeout);
    _dbgTimeout = setTimeout(function() { DEBUG.style.display = 'none'; }, 4000);
  } else {
    DBG_TEXT.style.whiteSpace = 'nowrap';
    DBG_TEXT.textContent = msg;
    DEBUG.style.maxHeight = '24px';
    DEBUG.style.overflowY = 'hidden';
  }
}

try {
debug('Script starting...');

const chat = document.getElementById('chat');
const input = document.getElementById('msg-input');
const sendBtn = document.getElementById('send-btn');
const statusDot = document.getElementById('status-dot');
const statusText = document.getElementById('status-text');

debug('Elements found: chat=' + !!chat + ' input=' + !!input + ' sendBtn=' + !!sendBtn + ' statusDot=' + !!statusDot + ' statusText=' + !!statusText);

let ws = null;
let pendingConfirm = false;
let isProcessing = false;

function connect() {
  debug('connect() called');
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const host = location.hostname === 'localhost' || location.hostname === '::1'
    ? '127.0.0.1:' + location.port
    : location.host;
  const url = protocol + '//' + host + '/ws/chat';
  debug('WebSocket URL: ' + url);
  
  try {
    statusText.textContent = 'connecting to ' + url + '...';
  } catch(e) {
    debug('statusText ERROR: ' + e.message);
  }

  try {
    ws = new WebSocket(url);
    debug('WebSocket created, readyState=' + ws.readyState);
  } catch(e) {
    debug('WebSocket constructor ERROR: ' + e.message);
    return;
  }

  ws.onopen = () => {
    debug('WS onopen fired');
    setStatus(true, 'connected');
    input.disabled = false;
    sendBtn.disabled = false;
    input.focus();
  };

  ws.onclose = (e) => {
    debug('WS onclose fired, code=' + e.code + ' reason=' + (e.reason || 'none') + ' wasClean=' + e.wasClean);
    setStatus(false, 'disconnected (code ' + e.code + ')');
    input.disabled = true;
    sendBtn.disabled = true;
    setTimeout(connect, 3000);
  };

  ws.onmessage = (e) => {
    try {
      const msg = JSON.parse(e.data);
      const info = msg.type === 'thinking' ? (' (' + (msg.content||'') + ')') :
                   msg.type === 'tool_calls' ? (' [' + (msg.tools||[]).map(t=>t.name).join(', ') + ']') :
                   msg.type === 'error' ? (': ' + (msg.content||'')) :
                   msg.type === 'confirm_request' ? (' [' + (msg.tools||[]).join(', ') + ']') :
                   '';
      debug('WS msg: type=' + msg.type + info);
      handleMessage(msg);
    } catch (err) {
      debug('WS parse error: ' + err.message);
      setStatus(false, 'parse error: ' + err.message);
    }
  };

  ws.onerror = (e) => {
    debug('WS onerror fired, readyState=' + ws.readyState);
    setStatus(false, 'ws error');
  };
}

function setStatus(ok, text) {
  statusDot.className = 'status-dot' + (ok ? '' : ' off');
  statusText.textContent = text;
}

function setIsProcessing(processing) {
  isProcessing = processing;
  if (processing) {
    sendBtn.textContent = 'Stop';
    sendBtn.className = 'btn-stop';
    sendBtn.disabled = false;
  } else {
    sendBtn.textContent = 'Send';
    sendBtn.className = '';
    sendBtn.disabled = false;
  }
}

function handleMessage(msg) {
  removeTyping();

  switch (msg.type) {
    case 'connected':
      setStatus(true, 'connected');
      setIsProcessing(false);
      if (msg.github_user) {
        document.getElementById('github-user').textContent = '@' + msg.github_user;
      }
      addMessage('assistant', msg.content);
      break;
    case 'text':
      setIsProcessing(false);
      addMessage('assistant', msg.content);
      break;
    case 'text_chunk':
      appendStreamingText(msg.content);
      break;
    case 'text_end':
      finalizeStreamingText();
      break;
    case 'tool_calls':
      addToolCalls(msg.content, msg.tools);
      addTyping();
      break;
    case 'confirm_request':
      setIsProcessing(false);
      addConfirmBar(msg.content, msg.tools);
      pendingConfirm = true;
      break;
    case 'thinking':
      addTyping();
      break;
    case 'status':
      addMessage('system', msg.content);
      break;
    case 'cancelled':
      setIsProcessing(false);
      if (msg.content) addMessage('system', msg.content, true);
      break;
    case 'error':
      setIsProcessing(false);
      addMessage('system', msg.content, true);
      break;
    case 'tool_result':
      addToolResult(msg);
      break;
    case 'pong':
      break;
  }
  scrollDown();
}

function addMessage(role, content, isError = false) {
  const div = document.createElement('div');
  div.className = 'msg ' + role + (isError ? ' error' : '');
  
  const bubble = document.createElement('div');
  bubble.className = 'msg-bubble';
  bubble.innerHTML = renderMarkdown(content);
  div.appendChild(bubble);
  
  chat.appendChild(div);
}

function addToolResult(msg) {
  removeTyping();
  const div = document.createElement('div');
  div.className = 'msg assistant';
  div.style.maxWidth = '95%';
  
  // Header
  const header = document.createElement('div');
  header.style.cssText = 'font-size:13px;color:var(--text-dim);margin-bottom:8px;';
  header.textContent = msg.message || (msg.tool + ' results');
  div.appendChild(header);
  
  // Repo list grid
  const grid = document.createElement('div');
  grid.style.cssText = 'display:flex;flex-wrap:wrap;gap:6px;';
  
  const repos = Array.isArray(msg.content) ? msg.content : [];
  const show = repos.slice(0, 50); // Show first 50 in grid
  const remaining = repos.length - show.length;
  
  show.forEach(r => {
    const card = document.createElement('div');
    card.style.cssText = 'background:var(--surface2);border:1px solid var(--border);border-radius:6px;padding:8px 10px;font-size:13px;min-width:200px;flex:1;max-width:320px;';
    card.innerHTML = `
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:3px;">
        <strong style="color:var(--accent);font-size:13px;">${esc(r.name)}</strong>
        <span style="font-size:10px;color:var(--text-dim);">${r.visibility === 'private' ? '🔒' : '🌐'}</span>
      </div>
      <div style="color:var(--text-dim);font-size:11px;line-height:1.4;">${esc(r.description || '')}</div>
      <div style="display:flex;gap:8px;margin-top:4px;font-size:10px;color:var(--text-dim);">
        ${r.language ? '<span style="color:var(--blue);">' + esc(r.language) + '</span>' : ''}
        <span>⭐ ' + (r.stars || 0) + '</span>
        <span>' + esc(r.last_push || '') + '</span>
      </div>
    `;
    grid.appendChild(card);
  });
  div.appendChild(grid);
  
  if (remaining > 0) {
    const more = document.createElement('div');
    more.style.cssText = 'text-align:center;padding:8px;color:var(--text-dim);font-size:12px;margin-top:4px;';
    more.textContent = '... and ' + remaining + ' more repos';
    div.appendChild(more);
  }
  
  // Total count
  const total = document.createElement('div');
  total.style.cssText = 'text-align:right;font-size:11px;color:var(--text-dim);margin-top:8px;border-top:1px solid var(--border);padding-top:6px;';
  total.textContent = 'Total: ' + repos.length + ' repositories';
  div.appendChild(total);
  
  chat.appendChild(div);
}

function addToolCalls(text, tools) {
  const div = document.createElement('div');
  div.className = 'msg assistant';

  if (text) {
    const bubble = document.createElement('div');
    bubble.className = 'msg-bubble';
    bubble.innerHTML = renderMarkdown(text);
    div.appendChild(bubble);
  }

  tools.forEach(t => {
    const tc = document.createElement('div');
    tc.className = 'tool-call';
    tc.innerHTML = `
      <div class="tool-call-header" onclick="this.parentElement.classList.toggle('open')">
        <span class="tool-call-icon">🔧</span>
        <span class="tool-call-name">${esc(t.name)}</span>
        <span class="tool-call-chevron">▾</span>
      </div>
      <div class="tool-call-body">${esc(JSON.stringify(t.args, null, 2))}</div>
    `;
    div.appendChild(tc);
  });

  chat.appendChild(div);
}

function addConfirmBar(text, tools) {
  const div = document.createElement('div');
  div.className = 'confirm-bar';
  div.id = 'confirm-bar';
  div.innerHTML = `
    <span class="text">⚠️ ${esc(text)}</span>
    <button class="btn-confirm" onclick="confirmAction()">✓ Confirm</button>
    <button class="btn-reject" onclick="rejectAction()">✗ Cancel</button>
  `;
  chat.appendChild(div);
}

function addTyping() {
  removeTyping();
  const div = document.createElement('div');
  div.className = 'msg assistant';
  div.id = 'typing-indicator';
  div.innerHTML = '<div class="msg-bubble"><div class="typing-dots"><span></span><span></span><span></span></div></div>';
  chat.appendChild(div);
  scrollDown();
}

function removeTyping() {
  const el = document.getElementById('typing-indicator');
  if (el) el.remove();
}

function renderMarkdown(text) {
  if (!text) return '';
  return text
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/```([\s\S]*?)```/g, '<pre>$1</pre>')
    .replace(/^### (.+)/gm, '<strong>$1</strong>')
    .replace(/^## (.+)/gm, '<strong>$1</strong>')
    .replace(/^# (.+)/gm, '<strong>$1</strong>')
    .replace(/^- (.+)/gm, '• $1')
    .replace(/\n/g, '<br>');
}

function esc(s) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

let streamingEl = null;

function appendStreamingText(content) {
  if (!streamingEl) {
    removeTyping();
    streamingEl = document.createElement('div');
    streamingEl.className = 'msg assistant';
    const bubble = document.createElement('div');
    bubble.className = 'msg-bubble';
    bubble.id = 'streaming-bubble';
    streamingEl.appendChild(bubble);
    chat.appendChild(streamingEl);
  }
  const bubble = document.getElementById('streaming-bubble');
  if (bubble) {
    bubble.innerHTML += renderMarkdown(content.replace(/\n/g, '<br>'));
  }
  scrollDown();
}

function finalizeStreamingText() {
  streamingEl = null;
}

function scrollDown() {
  chat.scrollTop = chat.scrollHeight;
}

function sendMessage() {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;

  if (isProcessing) {
    ws.send(JSON.stringify({ type: 'stop' }));
    return;
  }

  const text = input.value.trim();
  if (!text || pendingConfirm) return;

  addMessage('user', text);
  input.value = '';
  setIsProcessing(true);
  addTyping();

  ws.send(JSON.stringify({ type: 'message', content: text }));
}

function confirmAction() {
  if (!ws || !pendingConfirm) return;
  removeConfirmBar();
  pendingConfirm = false;
  setIsProcessing(true);
  addTyping();
  ws.send(JSON.stringify({ type: 'confirm' }));
}

function rejectAction() {
  if (!ws || !pendingConfirm) return;
  removeConfirmBar();
  pendingConfirm = false;
  setIsProcessing(true);
  addTyping();
  ws.send(JSON.stringify({ type: 'reject' }));
}

function removeConfirmBar() {
  const el = document.getElementById('confirm-bar');
  if (el) el.remove();
}

// --- Settings Modal ---

async function openSettings() {
  debug('openSettings() called');
  const overlay = document.getElementById('modal-overlay');
  overlay.classList.add('open');

  try {
    const resp = await fetch('/api/account');
    const data = await resp.json();

    document.getElementById('settings-user').textContent = data.github_user || 'not connected';
    document.getElementById('settings-source').textContent = 'via ' + data.github_source;
    const dot = document.getElementById('settings-dot');
    dot.className = dot.className.replace(' off', '');
    if (!data.github_configured) dot.className += ' off';

    document.getElementById('settings-gh-token').value = '';
    document.getElementById('settings-ds-key').value = '';
    document.getElementById('saved-msg').style.display = 'none';
  } catch (e) {
    document.getElementById('settings-user').textContent = 'error';
    document.getElementById('settings-source').textContent = e.message;
  }
}

function closeSettings() {
  document.getElementById('modal-overlay').classList.remove('open');
}

async function saveSettings() {
  debug('saveSettings() called');
  const ghToken = document.getElementById('settings-gh-token').value.trim();
  const dsKey = document.getElementById('settings-ds-key').value.trim();
  debug('ghToken length=' + ghToken.length + ' dsKey length=' + dsKey.length);

  if (!ghToken && !dsKey) {
    document.getElementById('saved-msg').textContent = 'Enter a token or key to save.';
    document.getElementById('saved-msg').style.color = 'var(--red)';
    document.getElementById('saved-msg').style.display = 'block';
    return;
  }

  const body = {};
  if (ghToken) body.github_token = ghToken;
  if (dsKey) body.deepseek_key = dsKey;

  const saveBtn = document.querySelector('.btn-save');
  saveBtn.textContent = 'Saving...';
  saveBtn.disabled = true;

  try {
    debug('POST /api/settings...');
    const resp = await fetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await resp.json();
    debug('Settings response: ' + JSON.stringify(data));

    if (data.status === 'ok') {
      document.getElementById('saved-msg').style.color = 'var(--green)';
      document.getElementById('saved-msg').textContent = 'Saved! Reloading...';
      document.getElementById('saved-msg').style.display = 'block';
      setTimeout(function() {
        window.location.reload(true) || window.location.reload() || (window.location.href = window.location.href);
      }, 800);
    } else {
      throw new Error(data.message || 'Unknown error');
    }
  } catch (e) {
    debug('saveSettings ERROR: ' + e.message);
    document.getElementById('saved-msg').textContent = 'Error: ' + e.message;
    document.getElementById('saved-msg').style.color = 'var(--red)';
    document.getElementById('saved-msg').style.display = 'block';
    saveBtn.textContent = 'Save & Reconnect';
    saveBtn.disabled = false;
  }
}

document.getElementById('settings-btn').addEventListener('click', openSettings);
document.getElementById('modal-overlay').addEventListener('click', (e) => {
  if (e.target === e.currentTarget) closeSettings();
});

input.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') { e.preventDefault(); sendMessage(); }
});
sendBtn.addEventListener('click', sendMessage);

connect();
} catch(e) {
  debug('FATAL SCRIPT ERROR: ' + e.message + ' (line ' + e.lineNumber + ')');
  statusText.textContent = 'JS Error: ' + e.message;
}
</script>

</body>
</html>'''
