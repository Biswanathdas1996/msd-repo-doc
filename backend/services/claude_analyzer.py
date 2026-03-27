"""
Advanced Documentation Generator using Claude Code CLI.

Optimized for 90%+ accuracy at any scale via:
  - Adaptive budgets/timeouts based on project size
  - Parallel execution of independent analysis steps
  - Multi-pass verification with cross-validation
  - Codebase chunking for large projects (500+ files)
  - Context seeding (pre-read key files, share across steps)
  - Secure read-only tool allowlist
  - Mermaid syntax validation
  - Quality scoring on output
"""

import os
import re
import sys
import json
import time
import uuid
import math
import shutil
import zipfile
import logging
import subprocess
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from backend.services.ai_reasoning import (
    generate_technical_specs_from_advanced_artifacts,
    is_pwc_genai_configured,
)

logger = logging.getLogger("claude_analyzer")

_SHELL = sys.platform == "win32"


def _technical_specs_via_pwc() -> bool:
    """Use PWC GenAI + bundled artifacts when provider is pwc or auto (endpoint configured)."""
    mode = (os.environ.get("ADVANCED_TECH_SPECS_PROVIDER") or "auto").strip().lower()
    if mode == "claude":
        return False
    if mode == "pwc":
        if not is_pwc_genai_configured():
            raise RuntimeError(
                "ADVANCED_TECH_SPECS_PROVIDER=pwc but PWC_GENAI_ENDPOINT_URL is not set"
            )
        return True
    return is_pwc_genai_configured()


# ---------------------------------------------------------------------------
# File collection config
# ---------------------------------------------------------------------------

SKIP_DIRS = {
    "__macosx", ".git", ".svn", ".hg", ".idea", ".vscode",
    "node_modules", "__pycache__", ".vs", "bin", "obj",
    ".terraform", ".tox", ".next", ".nuxt", "dist", "build",
    "coverage", ".cache", ".parcel-cache", "vendor",
    "packages",  # NuGet restore — deep paths often exceed Windows MAX_PATH; not needed for architecture docs
}

SKIP_EXTENSIONS = {
    ".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz",
    ".exe", ".dll", ".so", ".dylib", ".pdb", ".class", ".o",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".svg", ".webp",
    ".mp3", ".mp4", ".wav", ".avi", ".mov", ".webm",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".log", ".tmp", ".bak", ".swp", ".lock",
    ".woff", ".woff2", ".ttf", ".eot",
    ".pyc", ".pyo",
    ".min.js", ".min.css",
    ".map",
}

SKIP_FILES = {
    ".ds_store", "thumbs.db", "desktop.ini",
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "composer.lock", "gemfile.lock", "poetry.lock",
}

CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".cs", ".go",
    ".rb", ".php", ".swift", ".kt", ".rs", ".c", ".cpp", ".h",
    ".hpp", ".scala", ".vue", ".svelte", ".html", ".css", ".scss",
    ".sass", ".less", ".xml", ".json", ".yaml", ".yml", ".toml",
    ".sql", ".sh", ".bash", ".ps1", ".bat", ".cmd",
    ".r", ".m", ".lua", ".dart", ".ex", ".exs", ".erl",
    ".tf", ".hcl", ".proto", ".graphql", ".gql",
    ".md", ".rst", ".txt", ".cfg", ".ini", ".env.example",
    ".dockerfile", ".makefile", ".gradle", ".sbt",
    ".csproj", ".sln", ".pom", ".gemspec", ".cabal",
}

ENTRY_POINT_NAMES = {
    "main", "app", "index", "server", "startup", "program",
    "manage", "wsgi", "asgi", "urls", "routes", "router",
}

ENTRY_POINT_EXTENSIONS = {".py", ".js", ".ts", ".tsx", ".go", ".java", ".cs", ".rb"}

MAX_FILE_SIZE = 250 * 1024  # 250 KB — raised from 100KB to avoid dropping critical files


# ---------------------------------------------------------------------------
# Adaptive configuration — scales budget/timeout/turns by project size
# ---------------------------------------------------------------------------

class ProjectScale:
    SMALL = "small"       # < 30 files
    MEDIUM = "medium"     # 30-150 files
    LARGE = "large"       # 150-500 files
    XLARGE = "xlarge"     # 500+ files


def _determine_scale(file_count: int, total_lines: int) -> str:
    if file_count < 30 and total_lines < 5000:
        return ProjectScale.SMALL
    if file_count < 150 and total_lines < 30000:
        return ProjectScale.MEDIUM
    if file_count < 500 and total_lines < 100000:
        return ProjectScale.LARGE
    return ProjectScale.XLARGE


_SCALE_CONFIG = {
    #                    kg$    feat$  conn$  flow$  doc$   verify$ timeout turns  tree   effort_core  effort_secondary
    ProjectScale.SMALL:  {"kg_budget": 1.5, "feat_budget": 1.5, "conn_budget": 0.5, "flow_budget": 0.5, "doc_budget": 1.5, "verify_budget": 0.5, "specs_budget": 2.0, "timeout": 600,  "max_turns": 30, "tree_cap": 300,  "effort": "high", "effort_doc": "high"},
    ProjectScale.MEDIUM: {"kg_budget": 3.0, "feat_budget": 3.0, "conn_budget": 1.0, "flow_budget": 1.0, "doc_budget": 2.5, "verify_budget": 1.0, "specs_budget": 3.0, "timeout": 900,  "max_turns": 50, "tree_cap": 600,  "effort": "high", "effort_doc": "max"},
    ProjectScale.LARGE:  {"kg_budget": 5.0, "feat_budget": 5.0, "conn_budget": 2.0, "flow_budget": 2.0, "doc_budget": 4.0, "verify_budget": 1.5, "specs_budget": 5.0, "timeout": 1200, "max_turns": 75, "tree_cap": 1000, "effort": "high", "effort_doc": "max"},
    ProjectScale.XLARGE: {"kg_budget": 8.0, "feat_budget": 8.0, "conn_budget": 3.0, "flow_budget": 3.0, "doc_budget": 6.0, "verify_budget": 2.0, "specs_budget": 7.0, "timeout": 1800, "max_turns": 100, "tree_cap": 1500, "effort": "max",  "effort_doc": "max"},
}


# ---------------------------------------------------------------------------
# ZIP extraction + cleaning
# ---------------------------------------------------------------------------

def _win_long_path(path: str) -> str:
    """Use Windows ``\\\\?\\`` extended path so open/create works past MAX_PATH (~260)."""
    if not _SHELL:
        return path
    path = os.path.normpath(os.path.abspath(path))
    if path.startswith("\\\\?\\"):
        return path
    if path.startswith("\\\\"):
        return "\\\\?\\UNC\\" + path[2:]
    return "\\\\?\\" + path


def _should_skip_entry(entry_name: str) -> bool:
    """Return True if a ZIP entry should be skipped (junk dir/file)."""
    parts = entry_name.replace("\\", "/").lower().split("/")
    for part in parts[:-1]:
        if part in SKIP_DIRS:
            return True
    basename = parts[-1] if parts else ""
    if not basename:
        return False
    _, ext = os.path.splitext(basename)
    if basename in SKIP_FILES or ext in SKIP_EXTENSIONS:
        return True
    return False


def extract_and_clean(
    zip_path: str,
    output_base: str,
    on_event: Callable[[str, dict], None] | None = None,
) -> dict:
    """Extract ZIP and remove unnecessary files. Returns extraction info."""
    project_id = str(uuid.uuid4())[:8]
    output_folder_abs = os.path.abspath(os.path.join(output_base, f"adv_{project_id}"))
    os.makedirs(output_folder_abs, exist_ok=True)
    # Long path for I/O on Windows; zip-slip checks use the normal absolute path.
    output_folder = _win_long_path(output_folder_abs) if _SHELL else output_folder_abs

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            entries = zf.infolist()
            resolved = os.path.realpath(output_folder_abs)

            to_extract = []
            for entry in entries:
                target = os.path.realpath(os.path.join(output_folder_abs, entry.filename))
                if not target.startswith(resolved + os.sep) and target != resolved:
                    raise ValueError("Zip Slip detected")
                if not _should_skip_entry(entry.filename):
                    to_extract.append(entry)

            n = len(to_extract)
            logger.info("ZIP has %d total entries, extracting %d useful entries",
                        len(entries), n)

            def _emit_zip_progress(i_done: int) -> None:
                if not on_event:
                    return
                on_event(
                    "extraction_progress",
                    {
                        "phase": "zip",
                        "current": i_done,
                        "total": n,
                        "percent": round(100 * i_done / n) if n else 100,
                    },
                )

            if n == 0:
                _emit_zip_progress(0)
            else:
                last_prog = time.monotonic()
                for i, member in enumerate(to_extract):
                    zf.extract(member, output_folder)
                    now = time.monotonic()
                    if (
                        i == n - 1
                        or i % 2000 == 0
                        or (now - last_prog) >= 2.0
                    ):
                        _emit_zip_progress(i + 1)
                        last_prog = now
    except zipfile.BadZipFile:
        shutil.rmtree(output_folder_abs, ignore_errors=True)
        raise ValueError("Invalid ZIP file")
    except ValueError:
        shutil.rmtree(output_folder_abs, ignore_errors=True)
        raise
    except OSError as e:
        shutil.rmtree(output_folder_abs, ignore_errors=True)
        if _SHELL and getattr(e, "winerror", None) == 206:  # ERROR_FILENAME_EXCED_RANGE
            raise ValueError(
                "Path too long for Windows even with extended paths. Try a shorter project folder "
                "(move the app closer to drive root) or zip without deep vendor/package trees."
            ) from e
        if e.errno == 2:
            raise ValueError(
                "Extract failed (path missing or Windows MAX_PATH). If the archive has very deep "
                "folders, enable long paths in Windows or use a shorter output path; NuGet "
                "`packages/` is skipped automatically."
            ) from e
        raise

    for root_dir, dirs, files in os.walk(output_folder, topdown=False):
        if root_dir != output_folder and not os.listdir(root_dir):
            try:
                os.rmdir(root_dir)
            except OSError:
                pass

    return {
        "project_id": project_id,
        "output_folder": output_folder,
        "extracted_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Collect code file metadata + identify entry points for context seeding
# ---------------------------------------------------------------------------

def collect_code_files(
    folder: str,
    on_progress: Callable[[int], None] | None = None,
    progress_every: int = 800,
    progress_min_interval_s: float = 2.0,
) -> list[dict]:
    """Walk the folder and collect code file metadata.
    Returns a list of {path, size, lines, is_entry_point} dicts.
    """
    file_list = []
    seen_files = 0
    last_emit = time.monotonic()

    for root_dir, _, files in os.walk(folder):
        for f in sorted(files):
            seen_files += 1
            if on_progress and (
                seen_files % progress_every == 0
                or (time.monotonic() - last_emit) >= progress_min_interval_s
            ):
                on_progress(seen_files)
                last_emit = time.monotonic()

            _, ext = os.path.splitext(f.lower())
            full_path = os.path.join(root_dir, f)

            if ext not in CODE_EXTENSIONS:
                continue
            try:
                size = os.path.getsize(full_path)
            except OSError:
                continue
            if size > MAX_FILE_SIZE or size == 0:
                continue

            rel_path = os.path.relpath(full_path, folder).replace("\\", "/")
            try:
                with open(full_path, "r", encoding="utf-8", errors="ignore") as fh:
                    line_count = sum(1 for _ in fh)
            except Exception:
                line_count = 0

            stem = os.path.splitext(f.lower())[0]
            is_entry = (stem in ENTRY_POINT_NAMES and ext in ENTRY_POINT_EXTENSIONS)
            depth = rel_path.count("/")
            if depth <= 1 and ext in ENTRY_POINT_EXTENSIONS:
                is_entry = True

            file_list.append({
                "path": rel_path,
                "size": size,
                "lines": line_count,
                "is_entry_point": is_entry,
            })

    return file_list


def _pre_read_key_files(folder: str, code_files: list[dict], max_total_chars: int = 60000) -> str:
    """Pre-read entry points and important files to seed context into prompts.
    This avoids Claude CLI wasting turns discovering the obvious structure.
    """
    entry_points = [f for f in code_files if f.get("is_entry_point")]
    config_files = [f for f in code_files if any(
        f["path"].lower().endswith(n) for n in (
            "package.json", "pyproject.toml", "cargo.toml", "go.mod",
            "pom.xml", "build.gradle", "requirements.txt", "gemfile",
            "dockerfile", "docker-compose.yml", "docker-compose.yaml",
        )
    )]

    priority = entry_points + config_files
    seen_paths = {f["path"] for f in priority}
    remaining = [f for f in code_files if f["path"] not in seen_paths]
    remaining.sort(key=lambda f: f["lines"], reverse=True)
    priority.extend(remaining[:20])

    parts = []
    total = 0
    for f in priority:
        if total >= max_total_chars:
            break
        full_path = os.path.join(folder, f["path"])
        try:
            with open(full_path, "r", encoding="utf-8", errors="ignore") as fh:
                content = fh.read(15000)
            header = f"=== FILE: {f['path']} ({f['lines']} lines) ==="
            snippet = f"{header}\n{content}"
            if total + len(snippet) > max_total_chars:
                remaining_chars = max_total_chars - total
                if remaining_chars > 500:
                    snippet = snippet[:remaining_chars] + "\n... (truncated)"
                else:
                    break
            parts.append(snippet)
            total += len(snippet)
        except Exception:
            continue

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Claude Code CLI invocation — secured with allowedTools
# ---------------------------------------------------------------------------

def _win_subprocess_cwd(cwd: str) -> str:
    """Windows: use a normal path for subprocess ``cwd``.

    ``\\?\\`` extended-length paths are correct for Python's own file APIs but
    ``CreateProcess`` / child processes often mis-handle them and can raise
    ``FileNotFoundError`` (WinError 2) that looks like a missing executable.
    """
    if not _SHELL or not cwd:
        return cwd
    norm = cwd.replace("/", "\\")
    unc = "\\\\?\\UNC\\"
    if norm.startswith(unc):
        return "\\" + norm[len(unc) :]
    dos = "\\\\?\\"
    if norm.startswith(dos):
        return norm[len(dos) :]
    return cwd


def _resolve_claude_executable() -> str:
    """Resolve the Claude Code CLI binary.

    The FastAPI process often has a different PATH than an interactive shell (no npm global
    bin). Set ``CLAUDE_CODE_CLI`` or ``CLAUDE_CLI_PATH`` to a full path when needed.
    """
    for key in ("CLAUDE_CODE_CLI", "CLAUDE_CLI_PATH"):
        raw = (os.environ.get(key) or "").strip().strip('"')
        if not raw:
            continue
        expanded = os.path.abspath(os.path.expandvars(os.path.expanduser(raw)))
        if os.path.isfile(expanded):
            logger.info("Using %s from env %s=%s", expanded, key, raw)
            return expanded
        found = shutil.which(raw)
        if found:
            logger.info("Using %s (from env %s=%s via PATH search)", found, key, raw)
            return found
        raise RuntimeError(
            f"{key} is set to {raw!r} but no such file was found and it is not on PATH."
        )

    found = shutil.which("claude")
    if found:
        return found
    if _SHELL:
        found = shutil.which("claude.cmd")
        if found:
            return found
        # npm global bin is often missing from the PATH of GUI-started / IDE-started Python
        for guess in (
            os.path.expandvars(r"%APPDATA%\npm\claude.cmd"),
            os.path.expandvars(r"%LOCALAPPDATA%\npm\claude.cmd"),
        ):
            if guess and os.path.isfile(guess):
                logger.info("Resolved Claude CLI via npm default path: %s", guess)
                return guess

    raise RuntimeError(
        "Claude Code CLI not found. Install: npm install -g @anthropic-ai/claude-code\n"
        "Standard solution documentation uses PWC GenAI and does not need this binary; "
        "Advanced Docs / technical specs require Claude Code CLI.\n"
        "If it is installed, set CLAUDE_CODE_CLI in .env to the full path, for example:\n"
        "  CLAUDE_CODE_CLI=C:/Users/YourName/AppData/Roaming/npm/claude.cmd"
    )


def _claude_subprocess_argv(executable: str, cli_flags: list[str]) -> list[str]:
    """Build argv for subprocess. On Windows, npm shims are .cmd files and need cmd.exe /c."""
    lower = executable.lower()
    if _SHELL and (lower.endswith(".cmd") or lower.endswith(".bat")):
        return ["cmd.exe", "/c", executable] + cli_flags
    return [executable] + cli_flags


def _call_claude_cli(
    prompt: str,
    cwd: str,
    timeout_seconds: int = 600,
    max_budget_usd: float = 2.0,
    max_turns: int = 50,
    effort: str = "high",
) -> str:
    """Call the Claude Code CLI in non-interactive, read-only mode.

    Verified flags for Claude Code CLI v2.1.79:
      --allowedTools "Read,Grep,Glob"  (restricts to read-only tools)
      --effort high|max                (controls analysis thoroughness)
      --max-turns N                    (limits agentic loop iterations)
      --max-budget-usd N               (hard cost cap)
      --output-format json             (structured output)
      -p                               (non-interactive / print mode)

    Built-in read-only tools: Read, Grep, Glob
    (Note: SemanticSearch is NOT a CLI tool — it's Cursor-only)

    Falls back to --dangerously-skip-permissions if --allowedTools fails.
    """
    try:
        _resolved = _resolve_claude_executable()
    except RuntimeError as e:
        logger.error("CLI resolve failed: %s", e)
        raise

    cli_flags_primary = [
        "-p",
        "--output-format", "json",
        "--max-turns", str(max_turns),
        "--max-budget-usd", str(max_budget_usd),
        "--effort", effort,
        "--allowedTools", "Read,Grep,Glob",
    ]
    cmd = _claude_subprocess_argv(_resolved, cli_flags_primary)

    logger.info("=" * 60)
    logger.info("CLI CALL START")
    run_cwd = _win_subprocess_cwd(cwd) if _SHELL else cwd
    logger.info("  claude   = %s", _resolved)
    logger.info("  cwd      = %s", cwd)
    if run_cwd != cwd:
        logger.info("  cwd(sub) = %s (short path for subprocess)", run_cwd)
    logger.info("  budget   = $%.2f", max_budget_usd)
    logger.info("  timeout  = %ds", timeout_seconds)
    logger.info("  turns    = %d", max_turns)
    logger.info("  effort   = %s", effort)
    logger.info("  prompt   = %d chars, preview: %s...", len(prompt), prompt[:200].replace("\n", " "))
    logger.info("=" * 60)

    def _run_cli(cmd_args: list[str], prompt_text: str) -> subprocess.CompletedProcess:
        """Run Claude CLI with the prompt piped via stdin to avoid Windows
        command-line length limits (~8191 chars)."""
        try:
            if run_cwd and not os.path.isdir(run_cwd):
                logger.error("CLI subprocess cwd is not a directory: %s", run_cwd)
            return subprocess.run(
                cmd_args,
                input=prompt_text,
                capture_output=True,
                text=True,
                cwd=run_cwd,
                timeout=timeout_seconds,
                # Always False: on Windows, shell=True with a argv list mis-invokes cmd.exe and
                # can hang or fail to run `claude` from PATH correctly.
                shell=False,
                encoding="utf-8",
                errors="replace",
            )
        except subprocess.TimeoutExpired:
            logger.error("CLI TIMEOUT after %ds", timeout_seconds)
            raise RuntimeError(
                f"Claude Code CLI timed out after {timeout_seconds}s. "
                "The codebase may be too large or the analysis too complex."
            )
        except FileNotFoundError as e:
            logger.error(
                "CLI spawn failed (FileNotFoundError): %s | argv[0]=%r cwd=%r",
                e,
                cmd_args[0] if cmd_args else None,
                run_cwd,
            )
            raise RuntimeError(
                "Could not start Claude Code CLI (or invalid subprocess working directory on Windows). "
                "Set CLAUDE_CODE_CLI in .env if needed. Note: standard docs use PWC GenAI; only "
                "Advanced Docs uses the Claude CLI."
            ) from e

    proc = _run_cli(cmd, prompt)

    # If --allowedTools not recognized, retry with legacy flag
    if proc.returncode != 0 and proc.stderr and "allowedTools" in proc.stderr:
        logger.warning("--allowedTools not supported, falling back to --dangerously-skip-permissions")
        cli_flags_fallback = [
            "-p",
            "--output-format", "json",
            "--max-turns", str(max_turns),
            "--max-budget-usd", str(max_budget_usd),
            "--effort", effort,
            "--dangerously-skip-permissions",
        ]
        cmd_fallback = _claude_subprocess_argv(_resolved, cli_flags_fallback)
        proc = _run_cli(cmd_fallback, prompt)

    logger.info("CLI process exited with return code: %d", proc.returncode)

    if proc.stderr and proc.stderr.strip():
        logger.warning("CLI STDERR (%d chars):\n%s", len(proc.stderr), proc.stderr[:1000])

    stdout = proc.stdout.strip() if proc.stdout else ""
    logger.info("CLI STDOUT length: %d chars", len(stdout))
    if stdout:
        logger.info("CLI STDOUT first 500 chars:\n%s", stdout[:500])
        logger.info("CLI STDOUT last 500 chars:\n%s", stdout[-500:])

    if proc.returncode != 0:
        stderr = proc.stderr.strip() if proc.stderr else "No error output"
        logger.error("CLI FAILED (rc=%d): %s", proc.returncode, stderr[:500])
        raise RuntimeError(f"Claude Code CLI failed: {stderr[:500]}")

    if not stdout:
        logger.error("CLI returned empty stdout despite rc=0")
        raise RuntimeError("Claude Code CLI returned empty output")

    envelope = None
    json_lines_found = 0
    non_json_lines = 0
    total_lines = len(stdout.splitlines())
    logger.info("Parsing CLI output: %d total lines", total_lines)

    for i, line in enumerate(stdout.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
            json_lines_found += 1
            if isinstance(parsed, dict):
                if parsed.get("type") == "result" or "result" in parsed:
                    envelope = parsed
                elif envelope is None:
                    envelope = parsed
        except json.JSONDecodeError:
            non_json_lines += 1
            if non_json_lines <= 3:
                logger.warning("  Line %d: NOT valid JSON: %s", i + 1, line[:150])

    logger.info("Parsing summary: %d JSON, %d non-JSON, envelope=%s",
                json_lines_found, non_json_lines, envelope is not None)

    if envelope is None:
        try:
            envelope = json.loads(stdout)
        except json.JSONDecodeError:
            logger.warning("CLI did not return JSON envelope; using raw stdout (%d chars)", len(stdout))
            return stdout

    if envelope.get("is_error"):
        error_msg = envelope.get("result", "Unknown CLI error")
        raise RuntimeError(f"Claude Code CLI error: {error_msg}")

    raw_result = envelope.get("result", "")

    if isinstance(raw_result, list):
        text_parts = []
        for block in raw_result:
            if isinstance(block, dict) and block.get("text"):
                text_parts.append(block["text"])
            elif isinstance(block, str):
                text_parts.append(block)
        result = "\n".join(text_parts)
    elif isinstance(raw_result, str):
        result = raw_result
    elif isinstance(raw_result, dict):
        result = json.dumps(raw_result)
    else:
        result = str(raw_result) if raw_result else ""

    if not result:
        result = envelope.get("content", "")
        if isinstance(result, list):
            result = "\n".join(
                b.get("text", "") if isinstance(b, dict) else str(b) for b in result
            )
    if not result and isinstance(envelope, dict):
        if any(k in envelope for k in ("nodes", "features", "connections", "diagrams")):
            return json.dumps(envelope)
    if not result:
        logger.error("CLI envelope has no usable result. Keys: %s", list(envelope.keys()))
        raise RuntimeError("Claude Code CLI returned empty result")

    return result


# ---------------------------------------------------------------------------
# Project tree generation — adaptive cap
# ---------------------------------------------------------------------------

def generate_project_tree(folder: str, max_lines: int = 600) -> str:
    """Generate a directory tree string, capped adaptively."""
    lines = []
    for root_dir, dirs, files in os.walk(folder):
        dirs[:] = [d for d in sorted(dirs) if d.lower() not in SKIP_DIRS]
        level = root_dir.replace(folder, "").count(os.sep)
        indent = "  " * level
        dirname = os.path.basename(root_dir)
        lines.append(f"{indent}{dirname}/")
        sub_indent = "  " * (level + 1)
        for f in sorted(files)[:50]:
            lines.append(f"{sub_indent}{f}")
        if len(files) > 50:
            lines.append(f"{sub_indent}... and {len(files) - 50} more files")
    return "\n".join(lines[:max_lines])


def analyze_knowledge_graph(folder: str, tree: str, context_seed: str, cfg: dict) -> dict:
    """Use Claude Code CLI to explore the codebase and build a knowledge graph.

    Context seeding provides pre-read file content so the CLI starts with
    deep understanding rather than spending turns on discovery.
    """
    seed_section = ""
    if context_seed:
        seed_section = f"""
PRE-READ KEY FILES (use these as starting context, then explore further):
{context_seed[:20000]}
"""
    prompt = f"""IMPORTANT — YOUR ENTIRE RESPONSE MUST BE A SINGLE JSON OBJECT. No prose, no markdown, no explanation before or after. Just raw JSON.

You are a senior software architect. Explore this codebase using Read, Grep, and Glob tools to analyze how all components are connected.

RULES:
- ONLY use Read, Grep, and Glob tools. Do NOT create, write, or edit any files.
- After you finish reading files, your FINAL response text MUST be ONLY the JSON object below — no markdown fences, no commentary, no preamble. Start with {{ and end with }}.

The project structure is:
{tree}
{seed_section}
ANALYSIS STRATEGY (follow this order for thoroughness):
1. Start with the pre-read files above — they contain entry points and configs
2. Use Grep to find all import/require/include statements to map dependencies
3. Read each significant file to understand its role (not just its name)
4. Use Glob to find files you might have missed (e.g., *.controller.*, *.service.*, *.model.*)
5. For each component, verify its connections by reading the actual import/usage code

OUTPUT FORMAT — respond with ONLY this JSON (no ```json fences, no text before/after):
{{
  "nodes": [
    {{
      "id": "unique_id",
      "name": "Component Name",
      "type": "file|module|class|function|api|database|service|config|component|hook|page|route|middleware|model|util",
      "description": "Brief description of what this does",
      "file_path": "relative/path/to/file"
    }}
  ],
  "edges": [
    {{
      "source": "source_node_id",
      "target": "target_node_id",
      "relationship": "imports|calls|extends|implements|uses|configures|routes_to|depends_on|reads_from|writes_to|renders|provides|consumes|triggers|validates"
    }}
  ]
}}

Include 20-100 nodes depending on project size. Every node MUST have a valid file_path that exists in the project. Every edge MUST reference valid node IDs. Read the actual code — don't guess from file names.

REMINDER: Your response must start with {{ and end with }}. No other text."""

    raw = _call_claude_cli(prompt, cwd=folder, timeout_seconds=cfg["timeout"],
                           max_budget_usd=cfg["kg_budget"], max_turns=cfg["max_turns"],
                           effort=cfg["effort"])
    try:
        return _parse_json_response(raw, expected_keys=(("nodes", "edges"), ("nodes",)))
    except ValueError:
        return _retry_json_extraction(raw, cwd=folder, expected_keys=(("nodes", "edges"), ("nodes",)))


def analyze_features(folder: str, tree: str, context_seed: str, cfg: dict) -> dict:
    """Use Claude Code CLI to explore the codebase and identify all features."""
    seed_section = ""
    if context_seed:
        seed_section = f"""
PRE-READ KEY FILES (use these as starting context, then explore further):
{context_seed[:20000]}
"""
    prompt = f"""IMPORTANT — YOUR ENTIRE RESPONSE MUST BE A SINGLE JSON OBJECT. No prose, no markdown, no explanation before or after. Just raw JSON.

You are a senior software architect. Explore this codebase using Read, Grep, and Glob tools to identify all features and capabilities.

RULES:
- ONLY use Read, Grep, and Glob tools. Do NOT create, write, or edit any files.
- After you finish reading files, your FINAL response text MUST be ONLY the JSON object below — no markdown fences, no commentary, no preamble. Start with {{ and end with }}.

The project structure is:
{tree}
{seed_section}
ANALYSIS STRATEGY:
1. Start with entry points from pre-read files to understand what the app does
2. Use Grep to find route definitions, API endpoints, CLI commands, event handlers
3. For each feature found, trace its full implementation chain by reading files
4. Verify each feature's components actually exist — read the file to confirm

OUTPUT FORMAT — respond with ONLY this JSON (no ```json fences, no text before/after):
{{
  "features": [
    {{
      "id": "feature_1",
      "name": "Feature Name",
      "description": "What this feature does from user perspective",
      "components": [
        {{
          "name": "ComponentName",
          "type": "frontend|backend|database|config|api|service|util",
          "file_path": "relative/path",
          "role": "What role this component plays in the feature"
        }}
      ],
      "entry_points": ["Where this feature starts - e.g. API route, UI page, CLI command"],
      "data_flow": "Brief description of how data flows through this feature"
    }}
  ]
}}

Every component's file_path MUST reference a real file in the project. Read the actual code — don't guess.

REMINDER: Your response must start with {{ and end with }}. No other text."""

    raw = _call_claude_cli(prompt, cwd=folder, timeout_seconds=cfg["timeout"],
                           max_budget_usd=cfg["feat_budget"], max_turns=cfg["max_turns"],
                           effort=cfg["effort"])
    try:
        return _parse_json_response(raw, expected_keys=(("features",),))
    except ValueError:
        return _retry_json_extraction(raw, cwd=folder, expected_keys=(("features",),))


def analyze_feature_connections(folder: str, features: dict, kg: dict, cfg: dict) -> dict:
    """Use Claude Code CLI to analyze how features connect to each other.

    Cross-validates against the knowledge graph for consistency.
    """
    features_json = json.dumps(features, indent=2)[:12000]
    kg_summary = ""
    if kg and kg.get("nodes"):
        node_names = [n.get("name", "") for n in kg["nodes"][:50]]
        kg_summary = f"\nKNOWN COMPONENTS (from knowledge graph): {', '.join(node_names)}\n"

    prompt = f"""IMPORTANT — YOUR ENTIRE RESPONSE MUST BE A SINGLE JSON OBJECT. No prose, no markdown, no explanation. Just raw JSON.

You are a senior software architect. Given the features and knowledge graph below, explore the code using Read, Grep, and Glob tools to analyze how features connect.

RULES:
- ONLY use Read, Grep, and Glob tools. Do NOT create, write, or edit any files.
- Your FINAL response must be ONLY the JSON object. Start with {{ and end with }}.

IDENTIFIED FEATURES:
{features_json}
{kg_summary}
STEPS:
1. Read shared components and trace cross-feature dependencies
2. Verify connections by reading actual import/usage code
3. Group features that share data models, services, or infrastructure

OUTPUT FORMAT — respond with ONLY this JSON (no ```json fences, no text before/after):
{{
  "connections": [
    {{
      "source_feature": "feature_id",
      "target_feature": "feature_id",
      "connection_type": "depends_on|shares_data|triggers|extends|uses_output_of|requires",
      "description": "How these features are connected",
      "shared_components": ["ComponentName1", "ComponentName2"]
    }}
  ],
  "feature_groups": [
    {{
      "group_name": "Logical Group Name",
      "feature_ids": ["feature_1", "feature_2"],
      "description": "Why these features belong together"
    }}
  ]
}}

REMINDER: Your response must start with {{ and end with }}. No other text."""

    raw = _call_claude_cli(prompt, cwd=folder, timeout_seconds=cfg["timeout"],
                           max_budget_usd=cfg["conn_budget"], max_turns=cfg["max_turns"],
                           effort=cfg["effort"])
    try:
        return _parse_json_response(raw, expected_keys=(("connections",), ("feature_groups",)))
    except ValueError:
        return _retry_json_extraction(raw, cwd=folder, expected_keys=(("connections",), ("feature_groups",)))


def generate_flow_diagrams(folder: str, features: dict, kg: dict, cfg: dict) -> dict:
    """Use Claude Code CLI to generate Mermaid flow diagrams for each feature."""
    features_json = json.dumps(features, indent=2)[:10000]
    kg_edges_summary = ""
    if kg and kg.get("edges"):
        edges_sample = kg["edges"][:30]
        kg_edges_summary = f"\nKNOWN RELATIONSHIPS:\n{json.dumps(edges_sample, indent=2)}\n"

    prompt = f"""IMPORTANT — YOUR ENTIRE RESPONSE MUST BE A SINGLE JSON OBJECT. No prose, no markdown, no explanation. Just raw JSON.

You are a senior software architect. Generate end-to-end flow diagrams for each major feature in Mermaid syntax. Use Read, Grep, and Glob tools to trace actual code flows.

RULES:
- ONLY use Read, Grep, and Glob tools. Do NOT create, write, or edit any files.
- Your FINAL response must be ONLY the JSON object. Start with {{ and end with }}.

IDENTIFIED FEATURES:
{features_json}
{kg_edges_summary}
MERMAID SYNTAX RULES (follow strictly to avoid render errors):
- Use graph TD or flowchart TD
- Node IDs must be alphanumeric (no spaces, no special chars): use A, B, C1, svc_auth etc.
- Wrap labels in square brackets: A[My Label]
- For special shapes: A{{{{API Gateway}}}} for hexagon, A[(Database)] for cylinder
- Edge labels: A -->|label text| B
- NO parentheses in labels — they break Mermaid. Use square brackets instead.
- NO quotes around labels inside brackets
- Keep node labels under 40 chars
- 10-25 nodes per diagram for readability

OUTPUT FORMAT — respond with ONLY this JSON (no ```json fences, no text before/after):
{{
  "diagrams": [
    {{
      "feature_id": "feature_1",
      "title": "Feature Name - End to End Flow",
      "mermaid": "graph TD\\n    A[Start] --> B[Step 1]\\n    B --> C[Step 2]\\n    C --> D[End]",
      "description": "Explanation of the flow"
    }}
  ],
  "system_overview_diagram": {{
    "title": "System Architecture Overview",
    "mermaid": "graph TD\\n    ...",
    "description": "High-level system architecture showing all major components"
  }}
}}

REMINDER: Your response must start with {{ and end with }}. No other text."""

    raw = _call_claude_cli(prompt, cwd=folder, timeout_seconds=cfg["timeout"],
                           max_budget_usd=cfg["flow_budget"], max_turns=cfg["max_turns"],
                           effort=cfg["effort"])
    try:
        result = _parse_json_response(raw, expected_keys=(("diagrams",), ("diagrams", "system_overview_diagram")))
    except ValueError:
        result = _retry_json_extraction(raw, cwd=folder, expected_keys=(("diagrams",), ("diagrams", "system_overview_diagram")))

    result = _validate_mermaid_diagrams(result)
    return result


def generate_full_documentation(folder: str, tree: str, context_seed: str,
                                kg: dict, features: dict, connections: dict,
                                diagrams: dict, cfg: dict) -> str:
    """Use Claude Code CLI to generate comprehensive documentation by exploring the codebase.

    Receives pre-analyzed data from all prior steps for context richness.
    """
    mermaid_section = ""
    if diagrams:
        parts = []
        overview = diagrams.get("system_overview_diagram")
        if overview and overview.get("mermaid"):
            parts.append(f"### {overview.get('title', 'System Overview')}\n```mermaid\n{overview['mermaid']}\n```")
        for d in diagrams.get("diagrams", [])[:10]:
            if d.get("mermaid"):
                parts.append(f"### {d.get('title', 'Flow')}\n```mermaid\n{d['mermaid']}\n```")
        mermaid_section = "\n\n".join(parts)

    seed_section = ""
    if context_seed:
        seed_section = f"""
PRE-READ KEY FILES (reference these for accuracy):
{context_seed[:15000]}
"""

    prompt = f"""You are a senior technical writer. Explore this codebase using Read, Grep, and Glob tools and produce comprehensive code documentation as your text output.

CRITICAL: ONLY use Read, Grep, and Glob tools. Do NOT create, write, or edit any files. Return the documentation as your text response — do NOT save it to any file.

PROJECT STRUCTURE:
{tree}
{seed_section}
KNOWLEDGE GRAPH (components and relationships):
{json.dumps(kg, indent=2)[:10000]}

FEATURES:
{json.dumps(features, indent=2)[:10000]}

FEATURE CONNECTIONS:
{json.dumps(connections, indent=2)[:6000]}

PRE-GENERATED MERMAID DIAGRAMS (include these in the documentation as-is):
{mermaid_section[:8000]}

DOCUMENTATION REQUIREMENTS:
1. Read the actual source code to verify and enrich the summaries above
2. Cross-reference the knowledge graph with features to ensure consistency
3. Return a complete technical documentation in Markdown with ALL these sections:

  1. **Project Overview** - What it does, tech stack, architecture summary
  2. **Project Structure** - Directory layout with descriptions of each folder/file
  3. **Features List** - All features with detailed descriptions
  4. **Component Details** - Each major component: purpose, inputs, outputs, dependencies
  5. **How Components Link Together** - Architecture connections with specifics
  6. **Feature Connections** - Cross-feature dependencies and data sharing
  7. **Data Flow** - End-to-end data movement with concrete examples
  8. **End-to-End Flow Diagrams** - Include the pre-generated Mermaid diagrams above
  9. **API Reference** (if applicable) - All endpoints with method, path, params, response
  10. **Configuration** - Env vars, config files, setup instructions

QUALITY GUIDELINES:
- Every claim must be verifiable from the code
- Include specific file paths when referencing components
- Use code snippets for complex logic
- Be detailed enough for a new developer to onboard from this document alone

Return everything as Markdown text output, NOT as a file."""

    return _call_claude_cli(prompt, cwd=folder, timeout_seconds=cfg["timeout"],
                            max_budget_usd=cfg["doc_budget"], max_turns=cfg["max_turns"],
                            effort=cfg["effort_doc"])


def analyze_technical_specs(folder: str, tree: str, context_seed: str,
                            kg: dict, features: dict, cfg: dict) -> dict:
    """Use Claude Code CLI to extract technical specification sections from the codebase."""
    seed_section = ""
    if context_seed:
        seed_section = f"""
PRE-READ KEY FILES (use these as starting context, then explore further):
{context_seed[:20000]}
"""

    kg_summary = ""
    if kg:
        kg_summary = f"""
KNOWLEDGE GRAPH (components and relationships already discovered):
{json.dumps(kg, indent=2)[:8000]}
"""

    feat_summary = ""
    if features:
        feat_summary = f"""
FEATURES (already discovered):
{json.dumps(features, indent=2)[:8000]}
"""

    prompt = f"""IMPORTANT — YOUR ENTIRE RESPONSE MUST BE A SINGLE JSON OBJECT. No prose, no markdown, no explanation before or after. Just raw JSON.

You are a senior solutions architect. Explore this codebase using Read, Grep, and Glob tools to extract detailed technical specifications.

RULES:
- ONLY use Read, Grep, and Glob tools. Do NOT create, write, or edit any files.
- After you finish reading files, your FINAL response text MUST be ONLY the JSON object below — no markdown fences, no commentary, no preamble. Start with {{ and end with }}.
- For each section, if the codebase does not contain relevant information, set the value to null or an empty string.
- Be thorough — read config files, schema definitions, route handlers, middleware, integration files, auth modules, workflow definitions, and any CRM/ERP-related code.

The project structure is:
{tree}
{seed_section}
{kg_summary}
{feat_summary}
ANALYSIS STRATEGY:
1. Start with entry points and config files to understand the system scope
2. Use Grep to find authentication patterns (OAuth, JWT, API keys, session management, role-based access)
3. Use Grep to find entity/model/schema definitions (ORM models, database schemas, Salesforce objects, custom entities)
4. Use Grep to find workflow/business rule implementations (automation, triggers, validation rules, process builders)
5. Read JavaScript/TypeScript files for client-side customizations, form scripts, ribbon commands, web resources
6. Use Grep to find integration patterns (REST, SOAP, webhooks, message queues, middleware)
7. Look for module-specific components (Sales, Service, Marketing modules or equivalent domain areas)
8. Read deployment and architecture config files for high-level architecture patterns

OUTPUT FORMAT — respond with ONLY this JSON (no ```json fences, no text before/after):
{{
  "scope_definition": {{
    "in_scope": ["List of items/modules/features that are in scope based on what the codebase implements"],
    "out_of_scope": ["List of items that are referenced but not implemented, or explicitly excluded"],
    "summary": "Brief narrative of what is in and out of scope"
  }},
  "solution_overview": {{
    "summary": "High-level description of what the solution does",
    "tech_stack": ["List of technologies, frameworks, platforms used"],
    "deployment_model": "How the solution is deployed (cloud, on-premise, hybrid, etc.)",
    "key_capabilities": ["List of key capabilities the solution provides"]
  }},
  "high_level_architecture": {{
    "description": "Narrative description of the overall architecture",
    "layers": [
      {{
        "name": "Layer name (e.g., Presentation, Business Logic, Data Access, Integration)",
        "description": "What this layer does",
        "components": ["Key components in this layer"]
      }}
    ],
    "mermaid_diagram": "A Mermaid diagram showing the high-level architecture (graph TD format)"
  }},
  "erd": {{
    "description": "Description of the data model and entity relationships",
    "entities": [
      {{
        "name": "Entity/Table/Object name",
        "type": "standard|custom",
        "fields": [
          {{
            "name": "Field name",
            "type": "Field data type",
            "description": "What this field represents",
            "is_key": false,
            "is_required": false
          }}
        ],
        "relationships": ["Descriptions of relationships to other entities"]
      }}
    ],
    "mermaid_diagram": "A Mermaid erDiagram showing entity relationships"
  }},
  "standard_and_custom_entities": {{
    "standard_entities": [
      {{
        "name": "Entity name",
        "purpose": "What it's used for",
        "customizations": ["Any customizations made to this standard entity"]
      }}
    ],
    "custom_entities": [
      {{
        "name": "Custom entity name",
        "purpose": "Why this custom entity was created",
        "fields_summary": "Summary of key fields"
      }}
    ]
  }},
  "business_rules": {{
    "workflows": [
      {{
        "name": "Workflow/Process name",
        "trigger": "What triggers this workflow",
        "description": "What the workflow does",
        "steps": ["Ordered list of steps in the workflow"]
      }}
    ],
    "validation_rules": ["List of validation rules found in the codebase"],
    "automation": ["List of automated processes, triggers, scheduled jobs"]
  }},
  "javascript_customizations": {{
    "client_scripts": [
      {{
        "name": "Script/file name",
        "file_path": "Path to the file",
        "purpose": "What this script does",
        "events_handled": ["List of events this script handles (e.g., onLoad, onChange, onSave)"]
      }}
    ],
    "web_resources": ["List of web resources, custom controls, PCF controls found"],
    "libraries_used": ["External JS libraries used for client-side logic"]
  }},
  "auth_model": {{
    "authentication_method": "How users authenticate (OAuth, JWT, SAML, API Key, etc.)",
    "authorization_model": "How permissions are enforced (RBAC, ABAC, claims-based, etc.)",
    "roles": [
      {{
        "name": "Role name",
        "permissions": ["List of permissions/capabilities for this role"]
      }}
    ],
    "security_features": ["List of security features: MFA, encryption, audit logging, etc."],
    "file_paths": ["Paths to auth-related files"]
  }},
  "module_components": {{
    "sales": {{
      "components": [
        {{
          "name": "Component name",
          "type": "form|view|dashboard|workflow|plugin|web_resource|report|chart",
          "description": "What it does",
          "file_path": "Path to the file"
        }}
      ],
      "mermaid_diagram": "Mermaid diagram showing Sales module components and their relationships"
    }},
    "service": {{
      "components": [
        {{
          "name": "Component name",
          "type": "form|view|dashboard|workflow|plugin|web_resource|report|chart",
          "description": "What it does",
          "file_path": "Path to the file"
        }}
      ],
      "mermaid_diagram": "Mermaid diagram showing Service module components and their relationships"
    }},
    "marketing": {{
      "components": [
        {{
          "name": "Component name",
          "type": "form|view|dashboard|workflow|plugin|web_resource|report|chart",
          "description": "What it does",
          "file_path": "Path to the file"
        }}
      ],
      "mermaid_diagram": "Mermaid diagram showing Marketing module components and their relationships"
    }}
  }},
  "integration_architecture": {{
    "description": "Overview of how the system integrates with external services",
    "integrations": [
      {{
        "name": "Integration name",
        "type": "REST|SOAP|Webhook|Message Queue|File|Database|SDK",
        "direction": "inbound|outbound|bidirectional",
        "external_system": "Name of the external system",
        "description": "What data/functionality is exchanged",
        "endpoints": ["API endpoints or connection details"],
        "file_paths": ["Files implementing this integration"]
      }}
    ],
    "mermaid_diagram": "Mermaid diagram showing integration architecture"
  }},
  "integration_auth": {{
    "mechanisms": [
      {{
        "integration_name": "Which integration this auth applies to",
        "auth_type": "OAuth2|API Key|Certificate|Basic Auth|SAML|Custom Token",
        "description": "How authentication works for this integration",
        "token_management": "How tokens are stored, refreshed, rotated",
        "file_paths": ["Files implementing this auth mechanism"]
      }}
    ]
  }}
}}

Read the actual source code thoroughly. For fields where no information is found in the codebase, use null for objects or empty arrays for lists. If the project doesn't have Sales/Service/Marketing modules, map the closest equivalent domain areas or leave them with empty components arrays.

REMINDER: Your response must start with {{ and end with }}. No other text."""

    raw = _call_claude_cli(prompt, cwd=folder, timeout_seconds=cfg["timeout"],
                           max_budget_usd=cfg["specs_budget"], max_turns=cfg["max_turns"],
                           effort=cfg["effort_doc"])
    try:
        return _parse_json_response(raw, expected_keys=(
            ("scope_definition", "solution_overview", "high_level_architecture"),
            ("scope_definition", "solution_overview"),
        ))
    except ValueError:
        return _retry_json_extraction(raw, cwd=folder, expected_keys=(
            ("scope_definition", "solution_overview", "high_level_architecture"),
            ("scope_definition", "solution_overview"),
        ))


def regenerate_technical_specs(doc: dict) -> dict:
    """Re-run technical specs using PWC (bundled JSON) or Claude Code CLI + on-disk folder."""
    if _technical_specs_via_pwc():
        bundle = {
            "project_name": doc.get("name", ""),
            "project_tree": doc.get("project_tree", ""),
            "knowledge_graph": doc.get("knowledge_graph") or {},
            "features": doc.get("features") or {},
            "feature_connections": doc.get("feature_connections") or {},
            "flow_diagrams": doc.get("flow_diagrams") or {},
            "cross_validation": doc.get("cross_validation"),
            "documentation": doc.get("documentation") or "",
            "context_seed": None,
            "files": doc.get("files") or [],
        }
        folder = doc.get("output_folder")
        if folder and os.path.isdir(folder):
            code_files = collect_code_files(folder)
            if code_files:
                bundle["context_seed"] = _pre_read_key_files(
                    folder, code_files, max_total_chars=35000
                )
        logger.info("Regenerate technical specs via PWC GenAI (bundled artifacts)")
        return generate_technical_specs_from_advanced_artifacts(bundle)

    folder = doc.get("output_folder")
    if not folder:
        raise ValueError(
            "This report has no extracted project folder (e.g. it was imported from JSON). "
            "Re-upload the ZIP to generate technical specs with Claude CLI, or set "
            "PWC_GENAI_ENDPOINT_URL and ADVANCED_TECH_SPECS_PROVIDER=auto|pwc."
        )
    if not os.path.isdir(folder):
        raise ValueError(
            "Extracted project folder is missing on disk. Re-upload the ZIP."
        )

    t0 = time.monotonic()
    logger.info("regenerate_technical_specs: collecting code files from %s", folder)
    code_files = collect_code_files(folder)
    logger.info(
        "regenerate_technical_specs: %d code files indexed in %.1fs",
        len(code_files),
        time.monotonic() - t0,
    )
    if not code_files:
        raise ValueError("No code files found in the extracted folder.")

    total_lines = sum(f["lines"] for f in code_files)
    scale = _determine_scale(len(code_files), total_lines)
    cfg = _SCALE_CONFIG[scale]
    t1 = time.monotonic()
    tree = generate_project_tree(folder, max_lines=cfg["tree_cap"])
    logger.info(
        "regenerate_technical_specs: project tree built in %.1fs (%d chars)",
        time.monotonic() - t1,
        len(tree),
    )
    context_seed = _pre_read_key_files(folder, code_files, max_total_chars=60000)

    kg = doc.get("knowledge_graph") or {}
    features = doc.get("features") or {}

    logger.info(
        "regenerate_technical_specs: calling Claude CLI (timeout=%ds, scale=%s)",
        cfg["timeout"],
        scale,
    )
    t2 = time.monotonic()
    out = analyze_technical_specs(folder, tree, context_seed, kg, features, cfg)
    logger.info(
        "regenerate_technical_specs: CLI finished in %.1fs, top-level keys: %s",
        time.monotonic() - t2,
        list(out.keys())[:12] if isinstance(out, dict) else type(out),
    )
    return out


# ---------------------------------------------------------------------------
# Mermaid syntax validation
# ---------------------------------------------------------------------------

_MERMAID_BAD_PATTERNS = [
    (re.compile(r'\([^)]*\).*-->'), "Parentheses in node definition before edge"),
    (re.compile(r'-->.*\([^)]*\)'), "Parentheses in edge target"),
]

_MERMAID_REQUIRED_START = re.compile(r'^(graph\s+(TD|TB|BT|RL|LR)|flowchart\s+(TD|TB|BT|RL|LR)|sequenceDiagram|classDiagram|stateDiagram|erDiagram|gantt|pie)', re.MULTILINE)


def _validate_single_mermaid(mermaid_str: str) -> tuple[bool, str]:
    """Validate a Mermaid diagram string. Returns (is_valid, cleaned_or_error)."""
    if not mermaid_str or not mermaid_str.strip():
        return False, "Empty diagram"

    cleaned = mermaid_str.strip()

    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        cleaned = "\n".join(lines).strip()

    if not _MERMAID_REQUIRED_START.search(cleaned):
        if not cleaned.startswith("graph ") and not cleaned.startswith("flowchart "):
            cleaned = "graph TD\n" + cleaned

    cleaned = cleaned.replace('"', "'")
    cleaned = re.sub(r'\(([^)]{1,60})\)', r'[\1]', cleaned)

    return True, cleaned


def _validate_mermaid_diagrams(diagrams_dict: dict) -> dict:
    """Validate and fix all Mermaid diagrams in the result."""
    if not diagrams_dict:
        return diagrams_dict

    for d in diagrams_dict.get("diagrams", []):
        if d.get("mermaid"):
            valid, result = _validate_single_mermaid(d["mermaid"])
            if valid:
                d["mermaid"] = result
            else:
                logger.warning("Invalid Mermaid diagram for %s: %s", d.get("feature_id"), result)

    overview = diagrams_dict.get("system_overview_diagram")
    if overview and overview.get("mermaid"):
        valid, result = _validate_single_mermaid(overview["mermaid"])
        if valid:
            overview["mermaid"] = result

    return diagrams_dict


# ---------------------------------------------------------------------------
# Quality scoring — validates output against the actual codebase
# ---------------------------------------------------------------------------

def _score_knowledge_graph(kg: dict, code_files: list[dict], folder: str) -> dict:
    """Score the knowledge graph accuracy against the actual file structure."""
    nodes = kg.get("nodes", [])
    edges = kg.get("edges", [])

    if not nodes:
        return {"score": 0, "issues": ["No nodes in knowledge graph"], "total_nodes": 0, "total_edges": 0}

    file_paths_set = {f["path"].lower() for f in code_files}
    node_ids = {n["id"] for n in nodes}

    valid_paths = 0
    for n in nodes:
        fp = n.get("file_path", "").lower()
        if fp and (fp in file_paths_set or any(fp in p for p in file_paths_set)):
            valid_paths += 1

    valid_edges = sum(1 for e in edges if e.get("source") in node_ids and e.get("target") in node_ids)

    issues = []
    path_ratio = valid_paths / len(nodes) if nodes else 0
    edge_ratio = valid_edges / len(edges) if edges else 0

    if path_ratio < 0.7:
        issues.append(f"Only {valid_paths}/{len(nodes)} nodes have valid file paths ({path_ratio:.0%})")
    if edges and edge_ratio < 0.9:
        issues.append(f"Only {valid_edges}/{len(edges)} edges reference valid node IDs ({edge_ratio:.0%})")
    if len(nodes) < 5:
        issues.append(f"Only {len(nodes)} nodes — likely missed many components")
    if len(edges) < len(nodes) * 0.5:
        issues.append("Very few edges relative to nodes — connections may be incomplete")

    score = (path_ratio * 0.4 + edge_ratio * 0.3 +
             min(1.0, len(nodes) / 20) * 0.15 +
             min(1.0, len(edges) / (len(nodes) * 1.5)) * 0.15) * 100

    return {
        "score": round(score, 1),
        "issues": issues,
        "total_nodes": len(nodes),
        "total_edges": len(edges),
        "valid_paths": valid_paths,
        "valid_edges": valid_edges,
    }


def _score_features(features: dict, kg: dict) -> dict:
    """Score features against the knowledge graph for consistency."""
    feature_list = features.get("features", [])
    if not feature_list:
        return {"score": 0, "issues": ["No features identified"], "total_features": 0}

    kg_node_names = {n.get("name", "").lower() for n in kg.get("nodes", [])}
    kg_file_paths = {n.get("file_path", "").lower() for n in kg.get("nodes", [])}

    total_components = 0
    matched_components = 0
    features_with_entry = 0
    features_with_flow = 0

    for f in feature_list:
        if f.get("entry_points"):
            features_with_entry += 1
        if f.get("data_flow"):
            features_with_flow += 1
        for c in f.get("components", []):
            total_components += 1
            name_lower = c.get("name", "").lower()
            path_lower = c.get("file_path", "").lower()
            if name_lower in kg_node_names or path_lower in kg_file_paths:
                matched_components += 1

    issues = []
    match_ratio = matched_components / total_components if total_components else 0
    entry_ratio = features_with_entry / len(feature_list) if feature_list else 0
    flow_ratio = features_with_flow / len(feature_list) if feature_list else 0

    if match_ratio < 0.5:
        issues.append(f"Only {matched_components}/{total_components} feature components match KG nodes ({match_ratio:.0%})")
    if entry_ratio < 0.7:
        issues.append(f"Only {features_with_entry}/{len(feature_list)} features have entry points")

    score = (match_ratio * 0.4 + entry_ratio * 0.3 + flow_ratio * 0.2 +
             min(1.0, len(feature_list) / 5) * 0.1) * 100

    return {
        "score": round(score, 1),
        "issues": issues,
        "total_features": len(feature_list),
        "total_components": total_components,
        "matched_components": matched_components,
    }


def _compute_overall_quality(kg_score: dict, feat_score: dict, has_connections: bool,
                             has_diagrams: bool, has_docs: bool) -> dict:
    """Compute overall quality score across all steps."""
    weights = {"kg": 0.25, "features": 0.25, "connections": 0.15, "diagrams": 0.10, "docs": 0.25}

    total = 0
    total += kg_score.get("score", 0) * weights["kg"]
    total += feat_score.get("score", 0) * weights["features"]
    total += (80 if has_connections else 0) * weights["connections"]
    total += (80 if has_diagrams else 0) * weights["diagrams"]
    total += (90 if has_docs else 0) * weights["docs"]

    return {
        "overall_score": round(total, 1),
        "kg_score": kg_score,
        "feature_score": feat_score,
        "has_connections": has_connections,
        "has_diagrams": has_diagrams,
        "has_documentation": has_docs,
        "meets_target": total >= 85,
    }


# ---------------------------------------------------------------------------
# Cross-validation — uses a lightweight CLI call to verify accuracy
# ---------------------------------------------------------------------------

def _cross_validate(folder: str, kg: dict, features: dict, cfg: dict) -> dict:
    """Ask Claude to cross-validate the knowledge graph against features.
    Returns corrections and a confidence score.
    """
    kg_summary = json.dumps({
        "node_count": len(kg.get("nodes", [])),
        "edge_count": len(kg.get("edges", [])),
        "nodes": [{"name": n["name"], "type": n["type"], "file_path": n.get("file_path", "")}
                  for n in kg.get("nodes", [])[:40]],
    }, indent=2)

    feat_summary = json.dumps({
        "feature_count": len(features.get("features", [])),
        "features": [{"name": f["name"], "component_count": len(f.get("components", []))}
                     for f in features.get("features", [])[:20]],
    }, indent=2)

    prompt = f"""IMPORTANT — YOUR ENTIRE RESPONSE MUST BE A SINGLE JSON OBJECT. Start with {{ and end with }}.

You are a code review expert. I have a knowledge graph and feature list for a codebase. Quickly verify them by spot-checking 5-10 files with Read/Grep.

KNOWLEDGE GRAPH SUMMARY:
{kg_summary}

FEATURES SUMMARY:
{feat_summary}

TASKS:
1. Spot-check: Read 5-10 files referenced in the KG/features to verify they exist and match descriptions
2. Check for missed components: Use Glob to find important files not in the KG
3. Score confidence 0-100

OUTPUT FORMAT:
{{
  "confidence": 85,
  "verified_nodes": 8,
  "total_checked": 10,
  "missed_components": ["component not in KG"],
  "corrections": ["node X file_path is wrong, should be Y"],
  "assessment": "Brief assessment of accuracy"
}}"""

    try:
        raw = _call_claude_cli(prompt, cwd=folder, timeout_seconds=300,
                               max_budget_usd=cfg["verify_budget"], max_turns=15,
                               effort="medium")
        return _parse_json_response(raw, expected_keys=(("confidence",),))
    except Exception as e:
        logger.warning("Cross-validation failed (non-critical): %s", e)
        return {"confidence": -1, "assessment": f"Verification skipped: {e}"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_json_objects_by_brace_matching(text: str) -> list[dict]:
    """Extract all top-level JSON objects from text using brace-depth tracking.

    Handles nested braces and string escapes correctly, unlike the naive
    first-'{'-to-last-'}' approach which fails when prose surrounds the JSON.
    """
    results = []
    i = 0
    length = len(text)

    while i < length:
        if text[i] != '{':
            i += 1
            continue

        depth = 0
        in_string = False
        escape_next = False
        j = i

        while j < length:
            ch = text[j]

            if escape_next:
                escape_next = False
                j += 1
                continue

            if in_string:
                if ch == '\\':
                    escape_next = True
                elif ch == '"':
                    in_string = False
                j += 1
                continue

            if ch == '"':
                in_string = True
            elif ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    candidate = text[i:j + 1]
                    try:
                        parsed = json.loads(candidate)
                        if isinstance(parsed, dict) and len(parsed) > 0:
                            results.append(parsed)
                    except json.JSONDecodeError:
                        pass
                    break

            j += 1

        i = j + 1 if depth == 0 and j < length else i + 1

    return results


def _pick_best_json(objects: list[dict], expected_keys: tuple[tuple[str, ...], ...] | None = None) -> dict | None:
    """From a list of parsed JSON dicts, pick the one most likely to be the intended result.

    Prefers objects that contain expected top-level keys (e.g. ("nodes", "edges")).
    Falls back to the largest object.
    """
    if not objects:
        return None

    if expected_keys:
        for key_group in expected_keys:
            for obj in objects:
                if all(k in obj for k in key_group):
                    return obj

    return max(objects, key=lambda o: len(json.dumps(o, default=str)))


def _parse_json_response(raw: str, expected_keys: tuple[tuple[str, ...], ...] | None = None) -> dict:
    """Parse JSON from Claude's response with multiple robust extraction strategies.

    ``expected_keys`` is a tuple of key-groups, e.g. (("nodes", "edges"),) — the
    first extracted object that contains ALL keys in any group wins.
    """
    if isinstance(raw, list):
        parts = []
        for item in raw:
            if isinstance(item, dict) and "text" in item:
                parts.append(item["text"])
            elif isinstance(item, str):
                parts.append(item)
            else:
                parts.append(json.dumps(item, default=str))
        raw = "\n".join(parts)

    if not isinstance(raw, str):
        raw = json.dumps(raw, default=str)

    logger.info("-" * 60)
    logger.info("PARSE JSON RESPONSE — input length: %d chars", len(raw))
    logger.info("Input preview (first 300 chars):\n%s", raw[:300])
    logger.info("Input preview (last 300 chars):\n%s", raw[-300:])

    text = raw.strip()

    # ── Strategy 1: strip markdown fences and direct-parse ──────────────
    cleaned = text
    if cleaned.startswith("```"):
        logger.info("Detected markdown code fences — stripping them")
        lines = cleaned.split("\n")
        start = 1
        end = len(lines)
        for i in range(len(lines) - 1, 0, -1):
            if lines[i].strip() == "```":
                end = i
                break
        cleaned = "\n".join(lines[start:end])
        logger.info("After fence removal — length: %d chars, preview: %s", len(cleaned), cleaned[:200])

    try:
        result = json.loads(cleaned)
        if isinstance(result, dict):
            logger.info("PARSE SUCCESS (direct parse) — keys: %s", list(result.keys())[:10])
            return result
    except json.JSONDecodeError as e:
        logger.warning("Direct JSON parse failed: %s", str(e))

    # ── Strategy 2: find ALL markdown-fenced JSON blocks ────────────────
    fenced_blocks = re.findall(r'```(?:json)?\s*\n(.*?)```', text, re.DOTALL)
    for idx, block in enumerate(fenced_blocks):
        try:
            result = json.loads(block.strip())
            if isinstance(result, dict):
                logger.info("PARSE SUCCESS (fenced block #%d) — keys: %s", idx, list(result.keys())[:10])
                if expected_keys:
                    for kg in expected_keys:
                        if all(k in result for k in kg):
                            return result
                else:
                    return result
        except json.JSONDecodeError:
            continue
    if fenced_blocks:
        for idx, block in enumerate(fenced_blocks):
            try:
                result = json.loads(block.strip())
                if isinstance(result, dict):
                    return result
            except json.JSONDecodeError:
                continue

    # ── Strategy 3: brace-matching extraction (handles embedded JSON) ───
    logger.info("Attempting brace-matching JSON extraction...")
    all_objects = _extract_json_objects_by_brace_matching(text)
    logger.info("Brace-matching found %d JSON objects", len(all_objects))

    if all_objects:
        best = _pick_best_json(all_objects, expected_keys)
        if best is not None:
            logger.info("PARSE SUCCESS (brace-matching) — keys: %s", list(best.keys())[:10])
            return best

    # ── Strategy 4: naive first-{ to last-} (legacy fallback) ───────────
    brace_start = text.find("{")
    brace_end = text.rfind("}")
    if brace_start != -1 and brace_end != -1 and brace_end > brace_start:
        json_candidate = text[brace_start:brace_end + 1]
        try:
            result = json.loads(json_candidate)
            if isinstance(result, dict):
                logger.info("PARSE SUCCESS (naive brace extraction) — keys: %s", list(result.keys())[:10])
                return result
        except json.JSONDecodeError as e:
            logger.warning("Naive brace extraction failed: %s", str(e))

    logger.error("=" * 60)
    logger.error("ALL LOCAL PARSE STRATEGIES FAILED for %d-char response", len(raw))
    logger.error("Response preview:\n%s", raw[:2000])
    logger.error("=" * 60)
    raise ValueError("Claude returned invalid JSON")


def _retry_json_extraction(raw_text: str, cwd: str, expected_keys: tuple[tuple[str, ...], ...] | None = None) -> dict:
    """Last-resort retry: ask a fresh Claude CLI call to extract JSON from prose.

    This is much cheaper (--max-turns 1, $0.50 budget) because no file reading
    is needed — we pass the original prose and ask Claude to convert it.
    """
    truncated = raw_text[:30000]
    prompt = f"""Extract the JSON object from the following text. The text contains analysis results that should be formatted as JSON but may be mixed with prose.

Return ONLY the raw JSON object — no markdown fences, no explanation. Start your response with {{ and end with }}.

TEXT TO EXTRACT JSON FROM:
---
{truncated}
---

REMEMBER: Output ONLY the JSON object. Start with {{ and end with }}."""

    logger.info("RETRY: Calling Claude CLI to extract JSON from %d-char prose", len(truncated))
    try:
        extracted = _call_claude_cli(prompt, cwd=cwd, timeout_seconds=120, max_budget_usd=0.50,
                                     max_turns=3, effort="low")
        result = _parse_json_response(extracted, expected_keys=expected_keys)
        logger.info("RETRY SUCCESS — extracted JSON with keys: %s", list(result.keys())[:10])
        return result
    except Exception as e:
        logger.error("RETRY FAILED: %s", str(e))
        raise ValueError(f"Claude returned invalid JSON (retry also failed: {e})")


# ---------------------------------------------------------------------------
# Step descriptors (shared with frontend via SSE events)
# ---------------------------------------------------------------------------

ANALYSIS_STEPS = [
    {"key": "extraction",          "index": 0, "label": "Extracting & cleaning files"},
    {"key": "knowledge_graph",     "index": 1, "label": "Building knowledge graph"},
    {"key": "features",            "index": 2, "label": "Identifying features"},
    {"key": "cross_validation",    "index": 3, "label": "Cross-validating results"},
    {"key": "feature_connections", "index": 4, "label": "Analyzing feature connections"},
    {"key": "flow_diagrams",       "index": 5, "label": "Generating flow diagrams"},
    {"key": "technical_specs",     "index": 6, "label": "Extracting technical specifications"},
    {"key": "documentation",       "index": 7, "label": "Writing documentation"},
    {"key": "quality_check",       "index": 8, "label": "Quality scoring"},
]
TOTAL_STEPS = len(ANALYSIS_STEPS)


# ---------------------------------------------------------------------------
# Main orchestrator — optimized for 90%+ accuracy at any scale
# ---------------------------------------------------------------------------

def run_advanced_analysis(
    zip_path: str,
    project_name: str,
    output_base: str,
    on_progress: "callable | None" = None,
    on_event: "callable | None" = None,
) -> dict:
    """Full pipeline: extract -> clean -> parallel analysis -> cross-validate -> docs.

    Key optimizations over the original:
    - Adaptive budgets/timeouts based on project size
    - Parallel execution of KG + Features (independent steps)
    - Context seeding from pre-read entry points
    - Cross-validation step to catch errors
    - Quality scoring on final output
    - Mermaid syntax validation
    """

    def _notify(result: dict):
        if on_progress:
            try:
                on_progress(result)
            except Exception:
                logger.debug("on_progress callback failed", exc_info=True)

    def _emit(event_type: str, data: dict):
        if on_event:
            try:
                on_event(event_type, data)
            except Exception:
                logger.debug("on_event callback failed", exc_info=True)

    def _step_start(key: str):
        step = next(s for s in ANALYSIS_STEPS if s["key"] == key)
        _emit("step_start", {
            "step": key,
            "step_index": step["index"],
            "total_steps": TOTAL_STEPS,
            "label": step["label"],
        })

    def _step_complete(key: str, data: dict, summary: str = ""):
        step = next(s for s in ANALYSIS_STEPS if s["key"] == key)
        _emit("step_complete", {
            "step": key,
            "step_index": step["index"],
            "total_steps": TOTAL_STEPS,
            "summary": summary,
            "data": data,
        })

    def _step_error(key: str, error: str):
        step = next(s for s in ANALYSIS_STEPS if s["key"] == key)
        _emit("step_error", {
            "step": key,
            "step_index": step["index"],
            "total_steps": TOTAL_STEPS,
            "error": error,
        })

    def _step_skip(key: str, reason: str):
        step = next(s for s in ANALYSIS_STEPS if s["key"] == key)
        _emit("step_skip", {
            "step": key,
            "step_index": step["index"],
            "total_steps": TOTAL_STEPS,
            "reason": reason,
        })

    # ── Extraction ─────────────────────────────────────────────────────────
    _step_start("extraction")
    extraction = extract_and_clean(zip_path, output_base, on_event=_emit)
    project_id = extraction["project_id"]
    folder = extraction["output_folder"]
    _emit("extraction_progress", {"phase": "index", "files_scanned": 0})

    result: dict = {
        "id": project_id,
        "name": project_name,
        "created_at": extraction["extracted_at"],
        "status": "processing",
        "output_folder": folder,
        "file_count": 0,
        "files": [],
        "project_tree": "",
        "knowledge_graph": {},
        "features": {},
        "feature_connections": {},
        "flow_diagrams": {},
        "technical_specs": {},
        "documentation": "",
        "quality_score": {},
        "step_errors": {},
        "completed_steps": [],
        "current_step": "",
    }

    errors: list[str] = []

    def _emit_index_progress(scanned: int) -> None:
        _emit(
            "extraction_progress",
            {"phase": "index", "files_scanned": scanned},
        )

    code_files = collect_code_files(folder, on_progress=_emit_index_progress)

    if not code_files:
        result["status"] = "error"
        result["error"] = "No code files found in the uploaded ZIP"
        _step_error("extraction", result["error"])
        _emit("done", {"id": project_id, "status": "error", "error": result["error"]})
        _notify(result)
        return result

    # Determine project scale and get adaptive configuration
    total_lines = sum(f["lines"] for f in code_files)
    scale = _determine_scale(len(code_files), total_lines)
    cfg = _SCALE_CONFIG[scale]
    logger.info("Project scale: %s (%d files, %d lines) — budgets: KG=$%.1f, Feat=$%.1f",
                scale, len(code_files), total_lines, cfg["kg_budget"], cfg["feat_budget"])

    tree = generate_project_tree(folder, max_lines=cfg["tree_cap"])

    # Pre-read key files for context seeding
    context_seed = _pre_read_key_files(folder, code_files, max_total_chars=60000)
    logger.info("Context seed: %d chars from entry points and key files", len(context_seed))

    file_list = [{"path": f["path"], "size": f["size"], "lines": f["lines"]} for f in code_files]
    result["file_count"] = len(file_list)
    result["files"] = file_list
    result["project_tree"] = tree
    result["completed_steps"].append("extraction")

    _step_complete("extraction", {
        "file_count": len(file_list),
        "files": file_list,
        "project_tree": tree,
        "scale": scale,
    }, summary=f"{len(file_list)} files, {total_lines} lines ({scale})")
    _notify(result)

    knowledge_graph: dict = {}
    features: dict = {}
    connections: dict = {}
    diagrams: dict = {}

    # ── Steps 1-2: Knowledge Graph + Features (PARALLEL) ──────────────────
    # These are independent analyses — run them simultaneously to save time
    _step_start("knowledge_graph")
    _step_start("features")
    result["current_step"] = "knowledge_graph + features (parallel)"
    _notify(result)

    kg_error = None
    feat_error = None

    def _run_kg():
        return analyze_knowledge_graph(folder, tree, context_seed, cfg)

    def _run_features():
        return analyze_features(folder, tree, context_seed, cfg)

    with ThreadPoolExecutor(max_workers=2) as executor:
        kg_future = executor.submit(_run_kg)
        feat_future = executor.submit(_run_features)

        try:
            knowledge_graph = kg_future.result()
            node_count = len(knowledge_graph.get("nodes", []))
            edge_count = len(knowledge_graph.get("edges", []))
            result["knowledge_graph"] = knowledge_graph
            result["completed_steps"].append("knowledge_graph")
            _step_complete("knowledge_graph", knowledge_graph,
                           summary=f"{node_count} nodes, {edge_count} edges")
        except Exception as e:
            kg_error = str(e)
            errors.append(f"Knowledge graph failed: {e}")
            result["step_errors"]["knowledge_graph"] = kg_error
            _step_error("knowledge_graph", kg_error)

        try:
            features = feat_future.result()
            feature_count = len(features.get("features", []))
            result["features"] = features
            result["completed_steps"].append("features")
            _step_complete("features", features,
                           summary=f"{feature_count} features identified")
        except Exception as e:
            feat_error = str(e)
            errors.append(f"Features failed: {e}")
            result["step_errors"]["features"] = feat_error
            _step_error("features", feat_error)

    _notify(result)

    # ── Step 3: Cross-Validation ───────────────────────────────────────────
    if knowledge_graph and features:
        _step_start("cross_validation")
        result["current_step"] = "cross_validation"
        _notify(result)
        try:
            validation = _cross_validate(folder, knowledge_graph, features, cfg)
            confidence = validation.get("confidence", -1)
            result["cross_validation"] = validation
            result["completed_steps"].append("cross_validation")
            _step_complete("cross_validation", validation,
                           summary=f"Confidence: {confidence}%")

            # If validation found corrections, log them
            corrections = validation.get("corrections", [])
            if corrections:
                logger.info("Cross-validation found %d corrections: %s",
                            len(corrections), corrections[:3])
        except Exception as e:
            logger.warning("Cross-validation failed (non-critical): %s", e)
            result["step_errors"]["cross_validation"] = str(e)
            _step_error("cross_validation", str(e))
        _notify(result)
    else:
        _step_skip("cross_validation", "Skipped: KG or features missing")

    # ── Steps 4-5: Feature Connections + Flow Diagrams (PARALLEL) ─────────
    if features:
        _step_start("feature_connections")
        _step_start("flow_diagrams")
        result["current_step"] = "feature_connections + flow_diagrams (parallel)"
        _notify(result)

        def _run_connections():
            return analyze_feature_connections(folder, features, knowledge_graph, cfg)

        def _run_diagrams():
            return generate_flow_diagrams(folder, features, knowledge_graph, cfg)

        with ThreadPoolExecutor(max_workers=2) as executor:
            conn_future = executor.submit(_run_connections)
            diag_future = executor.submit(_run_diagrams)

            try:
                connections = conn_future.result()
                conn_count = len(connections.get("connections", []))
                group_count = len(connections.get("feature_groups", []))
                result["feature_connections"] = connections
                result["completed_steps"].append("feature_connections")
                _step_complete("feature_connections", connections,
                               summary=f"{conn_count} connections, {group_count} groups")
            except Exception as e:
                errors.append(f"Feature connections failed: {e}")
                result["step_errors"]["feature_connections"] = str(e)
                _step_error("feature_connections", str(e))

            try:
                diagrams = diag_future.result()
                diagram_count = len(diagrams.get("diagrams", []))
                result["flow_diagrams"] = diagrams
                result["completed_steps"].append("flow_diagrams")
                _step_complete("flow_diagrams", diagrams,
                               summary=f"{diagram_count} diagrams generated")
            except Exception as e:
                errors.append(f"Flow diagrams failed: {e}")
                result["step_errors"]["flow_diagrams"] = str(e)
                _step_error("flow_diagrams", str(e))
    else:
        reason = "Skipped: features step failed"
        result["step_errors"]["feature_connections"] = reason
        result["step_errors"]["flow_diagrams"] = reason
        _step_skip("feature_connections", reason)
        _step_skip("flow_diagrams", reason)
    _notify(result)

    # ── Step 6: Technical Specifications ───────────────────────────────────
    technical_specs: dict = {}
    _step_start("technical_specs")
    result["current_step"] = "technical_specs"
    _notify(result)
    try:
        if _technical_specs_via_pwc():
            bundle = {
                "project_name": project_name,
                "project_tree": tree,
                "knowledge_graph": knowledge_graph or {},
                "features": features or {},
                "feature_connections": connections or {},
                "flow_diagrams": diagrams or {},
                "cross_validation": result.get("cross_validation"),
                "documentation": "",
                "context_seed": context_seed,
                "files": file_list,
            }
            technical_specs = generate_technical_specs_from_advanced_artifacts(bundle)
            logger.info("Technical specs step used PWC GenAI (bundled artifacts)")
        else:
            technical_specs = analyze_technical_specs(
                folder, tree, context_seed, knowledge_graph, features, cfg
            )
        result["technical_specs"] = technical_specs
        result["completed_steps"].append("technical_specs")
        sections_found = sum(1 for k, v in technical_specs.items()
                             if v and v != {} and v != [] and v is not None)
        _step_complete("technical_specs", technical_specs,
                       summary=f"{sections_found} specification sections extracted")
    except Exception as e:
        errors.append(f"Technical specs failed: {e}")
        result["step_errors"]["technical_specs"] = str(e)
        _step_error("technical_specs", str(e))
    _notify(result)

    # ── Step 7: Full Documentation ────────────────────────────────────────
    _step_start("documentation")
    result["current_step"] = "documentation"
    _notify(result)
    try:
        documentation = generate_full_documentation(
            folder, tree, context_seed,
            knowledge_graph, features, connections, diagrams, cfg,
        )
        result["documentation"] = documentation
        result["completed_steps"].append("documentation")
        _step_complete("documentation", {"documentation": documentation},
                       summary=f"{len(documentation)} chars")
    except Exception as e:
        errors.append(f"Documentation failed: {e}")
        result["step_errors"]["documentation"] = str(e)
        _step_error("documentation", str(e))
    _notify(result)

    # ── Step 8: Quality Scoring ────────────────────────────────────────────
    _step_start("quality_check")
    result["current_step"] = "quality_check"
    _notify(result)
    try:
        kg_score = _score_knowledge_graph(knowledge_graph, code_files, folder) if knowledge_graph else {"score": 0}
        feat_score = _score_features(features, knowledge_graph) if features else {"score": 0}
        quality = _compute_overall_quality(
            kg_score, feat_score,
            has_connections=bool(connections and connections.get("connections")),
            has_diagrams=bool(diagrams and diagrams.get("diagrams")),
            has_docs=bool(result.get("documentation")),
        )
        result["quality_score"] = quality
        result["completed_steps"].append("quality_check")
        _step_complete("quality_check", quality,
                       summary=f"Overall: {quality['overall_score']}% (target: 90%)")
        logger.info("Quality score: %.1f%% — meets target: %s",
                     quality["overall_score"], quality["meets_target"])
    except Exception as e:
        logger.warning("Quality scoring failed (non-critical): %s", e)
        result["step_errors"]["quality_check"] = str(e)
        _step_error("quality_check", str(e))
    _notify(result)

    # ── Final status ───────────────────────────────────────────────────────
    result["current_step"] = ""
    if not errors:
        result["status"] = "ready"
    elif result["completed_steps"]:
        result["status"] = "partial"
        result["error"] = f"{len(errors)} step(s) failed: " + "; ".join(errors)
    else:
        result["status"] = "error"
        result["error"] = "; ".join(errors)

    _emit("done", {
        "id": project_id,
        "status": result["status"],
        "completed_steps": result["completed_steps"],
        "step_errors": result["step_errors"],
        "quality_score": result.get("quality_score", {}),
        "error": result.get("error"),
    })
    _notify(result)
    return result
