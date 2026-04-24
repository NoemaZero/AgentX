"""Bash command classifier — strict translation of readOnlyValidation.ts + autoClassifier."""

from __future__ import annotations

import re
import shlex

# Commands that are always read-only / safe
READ_ONLY_COMMANDS = frozenset({
    "cat", "head", "tail", "less", "more", "wc", "diff", "file",
    "find", "locate", "which", "whereis", "whatis", "type", "command",
    "ls", "dir", "tree", "stat", "du", "df",
    "echo", "printf", "date", "cal", "uptime", "uname", "hostname",
    "whoami", "id", "groups", "env", "printenv", "set",
    "grep", "egrep", "fgrep", "rg", "ag", "ack",
    "sort", "uniq", "cut", "paste", "tr", "fold", "fmt",
    "pwd", "realpath", "dirname", "basename",
    "git log", "git status", "git diff", "git show", "git branch",
    "git remote", "git tag", "git stash list", "git rev-parse",
    "git ls-files", "git shortlog", "git describe", "git blame",
    "python --version", "python3 --version", "node --version",
    "npm --version", "pip --version", "cargo --version", "go version",
    "java -version", "ruby --version", "rustc --version",
})

# Commands that modify the filesystem or system state
DESTRUCTIVE_COMMANDS = frozenset({
    "rm", "rmdir", "mkfs", "dd", "format",
    "sudo", "su", "chown", "chmod", "chgrp",
    "kill", "killall", "pkill",
    "shutdown", "reboot", "halt", "poweroff",
    "systemctl", "service",
    "iptables", "ufw",
    "useradd", "userdel", "usermod", "groupadd", "groupdel",
    "passwd", "chpasswd",
    "mount", "umount",
    "fdisk", "parted", "lvm",
    "crontab",
})

# Commands that are safe for auto-approval (file modifications but expected)
WRITE_COMMANDS = frozenset({
    "mkdir", "touch", "cp", "mv", "ln",
    "git add", "git commit", "git push", "git pull", "git fetch",
    "git checkout", "git switch", "git merge", "git rebase",
    "git stash", "git stash pop", "git stash apply",
    "git reset", "git revert", "git cherry-pick",
    "npm install", "npm ci", "npm run", "npm test", "npm start",
    "npx", "yarn", "pnpm",
    "pip install", "pip uninstall", "pip3 install",
    "cargo build", "cargo test", "cargo run",
    "go build", "go test", "go run", "go get",
    "make", "cmake", "gradle", "mvn",
    "python", "python3", "node", "ruby", "perl",
    "sed", "awk",
    "tee", "patch",
})


def classify_bash_command(command: str) -> str:
    """Classify a bash command as 'read_only', 'write', or 'destructive'.

    Used by the permission system to determine if a Bash tool invocation
    needs user permission.
    """
    if not command.strip():
        return "read_only"

    # Parse the first command (before pipes)
    first_segment = command.split("|")[0].strip()

    # Normalize: remove leading env vars
    normalized = re.sub(r"^(\w+=\S+\s+)*", "", first_segment).strip()

    # Try to extract the base command
    try:
        parts = shlex.split(normalized)
    except ValueError:
        parts = normalized.split()

    if not parts:
        return "read_only"

    base_cmd = parts[0]

    # Check multi-word commands (e.g., "git status")
    two_word = f"{parts[0]} {parts[1]}" if len(parts) > 1 else ""

    # Check read-only first
    if base_cmd in READ_ONLY_COMMANDS or two_word in READ_ONLY_COMMANDS:
        return "read_only"

    # Check destructive
    if base_cmd in DESTRUCTIVE_COMMANDS:
        return "destructive"

    # Check write commands
    if base_cmd in WRITE_COMMANDS or two_word in WRITE_COMMANDS:
        return "write"

    # Default: treat as write (needs permission in non-bypass modes)
    return "write"


def is_read_only_bash(command: str) -> bool:
    """Check if a bash command is read-only."""
    return classify_bash_command(command) == "read_only"
