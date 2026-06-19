# gh-hygiene — AI-Powered GitHub Repo Hygiene Manager

Manage your GitHub repos with natural language. Just chat with the AI and tell it what you want done — it figures out the commands, shows you what it plans to do, and executes only after your confirmation.

Powered by **DeepSeek V4 Flash** for decision-making and **PyGithub** for GitHub API access.

## Quick Start

```bash
# 1. Install
cd Projects/gh-hygiene
pip3 install --only-binary :all: -e ".[dev]"

# 2. Set up credentials (interactive wizard)
python3 -m gh_hygiene.cli auth setup

# 3. Start the web UI (recommended)
python3 -m gh_hygiene.cli serve

# Or use terminal chat
python3 -m gh_hygiene.cli chat
```

Then open **http://localhost:8080** in your browser.

## Authentication

### Setup wizard
```bash
python3 -m gh_hygiene.cli auth setup
```

Prompts you for:
- **GitHub PAT** — stored in macOS Keychain. Needs scopes: `repo`, `delete_repo`
- **DeepSeek API key** — stored in macOS Keychain. Get one at [platform.deepseek.com](https://platform.deepseek.com)

### Token resolution chain
Tokens are resolved in this order:
1. `gh auth token` (if `gh` CLI is installed)
2. macOS Keychain
3. Environment variables (`GITHUB_TOKEN`, `DEEPSEEK_API_KEY`)
4. Config file (`~/.config/gh-hygiene/config.yml`)

### Check status
```bash
python3 -m gh_hygiene.cli auth status
```

## Usage

### Web UI (recommended)

```bash
python3 -m gh_hygiene.cli serve
```

Open **http://localhost:8080** in your browser. Features:
- Message bubbles with markdown rendering
- Collapsible tool call displays (see what the AI is doing under the hood)
- One-click confirm/reject buttons for destructive actions
- Auto-reconnect if the server restarts
- Typing indicators and smooth animations
- Dark theme, gold accent

### Terminal Chat Mode

```bash
python3 -m gh_hygiene.cli chat
```

Just tell it what you want in plain English:

```
You: I've got about 120 repos and I know a bunch are dead. Can you find the ones I haven't touched in a year?

🤖 Let me scan all your repos...

🤖 I found 34 repos with no pushes in over a year. Would you like me to:
   1. Archive all 22 unarchived stale repos?
   2. Show you the list so you can pick?

You: Archive all of them, but show me the list first.

🤖 [shows table of 22 repos]
   
   ⚠️ About to archive 22 repos. Proceed? [y/N]

You: y

🤖 ✅ Done. 22 repos archived.
```

**Safety rules:**
- Destructive actions always preview first (dry run)
- You must explicitly confirm before any changes happen
- Type `y` to confirm, `n` to reject
- Type `exit` to quit, `help` for ideas

### One-Shot Mode

```bash
python3 -m gh_hygiene.cli run "find all repos without README files"
```

Single-turn — plans, previews, and exits. Destructive actions are shown as dry-run only in this mode. Use chat mode for actual destructive actions.

### Direct Commands

All underlying commands are accessible directly:

#### Repos
```bash
# List all repos
python3 -m gh_hygiene.cli repos list --sort pushed

# Filter by visibility
python3 -m gh_hygiene.cli repos list --visibility private

# Audit repos for hygiene issues
python3 -m gh_hygiene.cli repos audit

# Preview which repos would be archived
python3 -m gh_hygiene.cli repos archive --older-than-days 365

# Archive them (requires --confirm)
python3 -m gh_hygiene.cli repos archive --older-than-days 365 --no-dry-run --confirm
```

#### Files
```bash
# Audit for clutter files, large files, merge conflicts
python3 -m gh_hygiene.cli files audit

# Clean .DS_Store and Thumbs.db
python3 -m gh_hygiene.cli files clean --dry-run

# Apply reorganization rules from YAML
python3 -m gh_hygiene.cli files reorg --rules rules.yml --dry-run
```

#### Hygiene
```bash
# Find stale issues (no activity in 90+ days)
python3 -m gh_hygiene.cli hygiene issues --stale-days 90

# Close stale issues
python3 -m gh_hygiene.cli hygiene issues --close --dry-run

# Find merged PR branches to clean up
python3 -m gh_hygiene.cli hygiene prs --merged-older-days 30

# Audit labels across repos
python3 -m gh_hygiene.cli hygiene labels

# Sync standard labels (bug, enhancement, etc.)
python3 -m gh_hygiene.cli hygiene labels --sync --dry-run
```

All commands support `--json` for machine-readable output.

## Things You Can Ask

- "Show me all my repos"
- "Which repos haven't been touched in over a year?"
- "Find stale issues across all my projects"
- "Clean up .DS_Store files from my repos"
- "Audit my repos and tell me what needs attention"
- "Archive any repo I haven't pushed to in 2 years"
- "Check which repos are missing README files"
- "What's my GitHub API rate limit status?"
- "Clean up merged PR branches older than 30 days"
- "Add standard labels to repos missing them"

## Project Structure

```
gh-hygiene/
├── pyproject.toml
├── setup.py
├── README.md
├── src/
│   └── gh_hygiene/
│       ├── cli.py              # Typer CLI entry point
│       ├── auth.py             # Credential management (keychain, env, config)
│       ├── client.py           # PyGithub wrapper (pagination, rate limiting)
│       ├── config.py           # Config file management
│       ├── chat.py             # LLM conversation loop + safety gate
│       ├── server.py           # FastAPI web server + WebSocket chat
│       ├── tools.py            # Tool definitions for function calling
│       ├── display.py          # Rich tables, progress bars, formatting
│       └── commands/
│           ├── repos.py        # List, audit, archive repos
│           ├── files.py        # Audit, clean, reorganize files
│           └── hygiene.py      # Stale issues, PR branches, labels
└── tests/
    ├── test_auth.py
    ├── test_tools.py
    ├── test_chat.py
    ├── test_repos.py
    ├── test_files.py
    └── test_hygiene.py
```

## Running Tests

```bash
python3 -m pytest tests/ -v
```

## Requirements

- Python 3.9+
- macOS (Keychain integration; config file fallback works on Linux)
- GitHub PAT with `repo` and `delete_repo` scopes
- DeepSeek API key (for chat/run/serve modes; direct commands don't need it)
