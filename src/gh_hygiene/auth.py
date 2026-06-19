"""Authentication management for gh-hygiene.

Token resolution chain (tried in order):
  1. `gh auth token` subprocess
  2. macOS Keychain via keyring
  3. Environment variables (GITHUB_TOKEN / DEEPSEEK_API_KEY)
  4. Config file (~/.config/gh-hygiene/config.yml)

Also manages the DeepSeek API key with the same chain.
"""

from __future__ import annotations

import os
import subprocess
from typing import Optional

import keyring

from .config import get_config_value, set_config_value

KEYRING_SERVICE = "gh-hygiene"
KEYRING_ACCOUNT_GITHUB = "github.com"
KEYRING_ACCOUNT_DEEPSEEK = "deepseek"

ENV_GITHUB_TOKEN = "GITHUB_TOKEN"
ENV_DEEPSEEK_KEY = "DEEPSEEK_API_KEY"


# ---------------------------------------------------------------------------
# GitHub token resolution
# ---------------------------------------------------------------------------


def get_github_token() -> Optional[str]:
    """Resolve a GitHub token through the full chain.

    Returns None if no token can be found.
    """
    # 1. gh CLI
    token = _token_from_gh_cli()
    if token:
        return token

    # 2. macOS Keychain
    token = _token_from_keychain(KEYRING_ACCOUNT_GITHUB)
    if token:
        return token

    # 3. Environment variable
    token = os.environ.get(ENV_GITHUB_TOKEN)
    if token:
        return token

    # 4. Config file
    token = get_config_value("github_token")
    if token:
        return token

    return None


def _token_from_gh_cli() -> Optional[str]:
    """Try to get a token from the gh CLI."""
    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def _token_from_keychain(account: str) -> Optional[str]:
    """Try to get a secret from the macOS Keychain."""
    try:
        return keyring.get_password(KEYRING_SERVICE, account)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# DeepSeek API key resolution
# ---------------------------------------------------------------------------


def get_deepseek_api_key() -> Optional[str]:
    """Resolve a DeepSeek API key through the full chain."""
    # 1. Keychain
    key = _token_from_keychain(KEYRING_ACCOUNT_DEEPSEEK)
    if key:
        return key

    # 2. Environment variable
    key = os.environ.get(ENV_DEEPSEEK_KEY)
    if key:
        return key

    # 3. Config file
    key = get_config_value("deepseek_api_key")
    if key:
        return key

    return None


# ---------------------------------------------------------------------------
# Setup helpers
# ---------------------------------------------------------------------------


def store_github_token(token: str) -> None:
    """Store the GitHub PAT in the macOS Keychain."""
    _store_in_keychain(KEYRING_ACCOUNT_GITHUB, token)


def store_deepseek_key(key: str) -> None:
    """Store the DeepSeek API key in the macOS Keychain."""
    _store_in_keychain(KEYRING_ACCOUNT_DEEPSEEK, key)


def _store_in_keychain(account: str, secret: str) -> None:
    """Store a secret in the macOS Keychain."""
    try:
        keyring.set_password(KEYRING_SERVICE, account, secret)
    except Exception:
        # Fall back to config file if keychain is unavailable
        if account == KEYRING_ACCOUNT_GITHUB:
            set_config_value("github_token", secret)
        else:
            set_config_value("deepseek_api_key", secret)


def get_token_source() -> str:
    """Return a human-readable string describing where the GitHub token came from."""
    if _token_from_gh_cli():
        return "gh CLI"
    if _token_from_keychain(KEYRING_ACCOUNT_GITHUB):
        return "macOS Keychain"
    if os.environ.get(ENV_GITHUB_TOKEN):
        return f"${ENV_GITHUB_TOKEN}"
    if get_config_value("github_token"):
        return "config file"
    return "none"


def get_deepseek_source() -> str:
    """Return a human-readable string describing where the DeepSeek key came from."""
    if _token_from_keychain(KEYRING_ACCOUNT_DEEPSEEK):
        return "macOS Keychain"
    if os.environ.get(ENV_DEEPSEEK_KEY):
        return f"${ENV_DEEPSEEK_KEY}"
    if get_config_value("deepseek_api_key"):
        return "config file"
    return "none"


def clear_stored_tokens() -> None:
    """Remove stored tokens from keychain and config."""
    try:
        keyring.delete_password(KEYRING_SERVICE, KEYRING_ACCOUNT_GITHUB)
    except Exception:
        pass
    try:
        keyring.delete_password(KEYRING_SERVICE, KEYRING_ACCOUNT_DEEPSEEK)
    except Exception:
        pass
    set_config_value("github_token", None)
    set_config_value("deepseek_api_key", None)
