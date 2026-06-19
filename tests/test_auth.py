"""Tests for authentication module."""

from unittest.mock import patch, MagicMock
import os

from gh_hygiene.auth import (
    get_github_token,
    get_deepseek_api_key,
    get_token_source,
    get_deepseek_source,
    clear_stored_tokens,
    KEYRING_SERVICE,
    KEYRING_ACCOUNT_GITHUB,
    KEYRING_ACCOUNT_DEEPSEEK,
    ENV_GITHUB_TOKEN,
    ENV_DEEPSEEK_KEY,
)


class TestGitHubToken:
    """Test GitHub token resolution chain."""

    def test_token_from_gh_cli(self):
        """Token is found from gh CLI."""
        with patch("gh_hygiene.auth._token_from_gh_cli", return_value="gh-token-123"):
            token = get_github_token()
            assert token == "gh-token-123"

    def test_token_from_keychain(self):
        """Token falls back to keychain when gh CLI is unavailable."""
        with patch("gh_hygiene.auth._token_from_gh_cli", return_value=None):
            with patch("gh_hygiene.auth._token_from_keychain", return_value="keychain-token-456"):
                token = get_github_token()
                assert token == "keychain-token-456"

    def test_token_from_env(self):
        """Token falls back to env var."""
        with patch("gh_hygiene.auth._token_from_gh_cli", return_value=None):
            with patch("gh_hygiene.auth._token_from_keychain", return_value=None):
                with patch.dict(os.environ, {ENV_GITHUB_TOKEN: "env-token-789"}):
                    token = get_github_token()
                    assert token == "env-token-789"

    def test_token_from_config(self):
        """Token falls back to config file."""
        with patch("gh_hygiene.auth._token_from_gh_cli", return_value=None):
            with patch("gh_hygiene.auth._token_from_keychain", return_value=None):
                with patch.dict(os.environ, {}, clear=True):
                    with patch("gh_hygiene.auth.get_config_value", return_value="config-token"):
                        token = get_github_token()
                        assert token == "config-token"

    def test_token_not_found(self):
        """Token is None when no source has it."""
        with patch("gh_hygiene.auth._token_from_gh_cli", return_value=None):
            with patch("gh_hygiene.auth._token_from_keychain", return_value=None):
                with patch.dict(os.environ, {}, clear=True):
                    with patch("gh_hygiene.auth.get_config_value", return_value=None):
                        token = get_github_token()
                        assert token is None

    def test_token_source_gh_cli(self):
        """Source is 'gh CLI' when token comes from gh."""
        with patch("gh_hygiene.auth._token_from_gh_cli", return_value="token"):
            assert get_token_source() == "gh CLI"

    def test_token_source_keychain(self):
        """Source is 'macOS Keychain' when token comes from keychain."""
        with patch("gh_hygiene.auth._token_from_gh_cli", return_value=None):
            with patch("gh_hygiene.auth._token_from_keychain", return_value="token"):
                assert get_token_source() == "macOS Keychain"

    def test_token_source_env(self):
        """Source shows env var name."""
        with patch("gh_hygiene.auth._token_from_gh_cli", return_value=None):
            with patch("gh_hygiene.auth._token_from_keychain", return_value=None):
                with patch.dict(os.environ, {ENV_GITHUB_TOKEN: "token"}):
                    assert get_token_source() == f"${ENV_GITHUB_TOKEN}"

    def test_token_source_none(self):
        """Source is 'none' when no token found."""
        with patch("gh_hygiene.auth._token_from_gh_cli", return_value=None):
            with patch("gh_hygiene.auth._token_from_keychain", return_value=None):
                with patch.dict(os.environ, {}, clear=True):
                    with patch("gh_hygiene.auth.get_config_value", return_value=None):
                        assert get_token_source() == "none"


class TestDeepSeekKey:
    """Test DeepSeek API key resolution chain."""

    def test_key_from_keychain(self):
        """Key found in keychain."""
        with patch("gh_hygiene.auth._token_from_keychain", return_value="ds-key-123"):
            key = get_deepseek_api_key()
            assert key == "ds-key-123"

    def test_key_from_env(self):
        """Key falls back to env var."""
        with patch("gh_hygiene.auth._token_from_keychain", return_value=None):
            with patch.dict(os.environ, {ENV_DEEPSEEK_KEY: "ds-env-key"}):
                key = get_deepseek_api_key()
                assert key == "ds-env-key"

    def test_key_from_config(self):
        """Key falls back to config."""
        with patch("gh_hygiene.auth._token_from_keychain", return_value=None):
            with patch.dict(os.environ, {}, clear=True):
                with patch("gh_hygiene.auth.get_config_value", return_value="ds-config-key"):
                    key = get_deepseek_api_key()
                    assert key == "ds-config-key"

    def test_key_not_found(self):
        """Key is None when no source has it."""
        with patch("gh_hygiene.auth._token_from_keychain", return_value=None):
            with patch.dict(os.environ, {}, clear=True):
                with patch("gh_hygiene.auth.get_config_value", return_value=None):
                    key = get_deepseek_api_key()
                    assert key is None

    def test_deepseek_source_keychain(self):
        """Source is keychain when key is there."""
        with patch("gh_hygiene.auth._token_from_keychain", return_value="ds-key"):
            assert get_deepseek_source() == "macOS Keychain"

    def test_deepseek_source_env(self):
        """Source is env var name."""
        with patch("gh_hygiene.auth._token_from_keychain", return_value=None):
            with patch.dict(os.environ, {ENV_DEEPSEEK_KEY: "key"}):
                assert get_deepseek_source() == f"${ENV_DEEPSEEK_KEY}"


class TestStoreAndClear:
    """Test storing and clearing credentials."""

    def test_store_github_token(self):
        """Storing token uses keyring."""
        with patch("gh_hygiene.auth.keyring.set_password") as mock_set:
            from gh_hygiene.auth import store_github_token
            store_github_token("my-token")
            mock_set.assert_called_once_with(KEYRING_SERVICE, KEYRING_ACCOUNT_GITHUB, "my-token")

    def test_store_deepseek_key(self):
        """Storing DeepSeek key uses keyring."""
        with patch("gh_hygiene.auth.keyring.set_password") as mock_set:
            from gh_hygiene.auth import store_deepseek_key
            store_deepseek_key("my-key")
            mock_set.assert_called_once_with(KEYRING_SERVICE, KEYRING_ACCOUNT_DEEPSEEK, "my-key")

    def test_clear_stored_tokens(self):
        """Clearing removes from keychain and config."""
        with patch("gh_hygiene.auth.keyring.delete_password") as mock_del:
            with patch("gh_hygiene.auth.set_config_value") as mock_set:
                clear_stored_tokens()
                assert mock_del.call_count == 2
                assert mock_set.call_count == 2
