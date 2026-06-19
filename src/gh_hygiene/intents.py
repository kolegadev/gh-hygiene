"""Lightweight intent detection for common queries.

Bypasses the LLM for straightforward tool-invocation queries,
saving 2-3 seconds of initial LLM latency.
"""

from __future__ import annotations

import re
from typing import Optional

# Patterns: tool_name -> list of regex patterns that trigger it
SIMPLE_TOOL_PATTERNS: dict[str, list[str]] = {
    "list_repos": [
        r"(show|list|get|fetch|display|tell|what|which|see|view).*(my\s+)?repos(itories)?",
        r"(my|all\s+my|my\s+account).*repos(itories)?",
        r"repos?(itories)?\s*(I\s+have|I\s+own|in\s+my\s+account|do\s+I\s+have)",
        r"what.*(repos|projects).*(have|own|got)",
    ],
    "audit_repos": [
        r"audit.*(my\s+)?repos(itories)?",
        r"check.*(health|hygiene|status).*(repos|projects)",
        r"(stale|dead|inactive|abandoned).*(repos|projects)",
        r"which\s+repos.*(stale|inactive|old|dead|abandoned)",
    ],
}


def detect_intent(message: str) -> Optional[str]:
    """Try to match the user's message to a known tool intent.

    Returns the tool name if a high-confidence match is found,
    otherwise None (meaning: let the LLM handle it).
    """
    message_lower = message.lower().strip()

    for tool, patterns in SIMPLE_TOOL_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, message_lower):
                return tool

    return None
