#!/usr/bin/env python3
# main.py (renamed)
from __future__ import annotations
import pathspec

import argparse
import json
import random
import os
import shutil
import re
import subprocess
import sys
import textwrap
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, Any, Union
from pathlib import Path

# --- Config / paths ---
PROMPT_STYLE_DB = Path("prompt_styles.json")
PROMPT_STYLE_USAGE_LOG = Path("prompt_style_usage.jsonl")
MAX_MALFORMED_RETRIES = 5
DEFAULT_HISTORY_WINDOW = 8
MALFORMED_SCORE_PENALTY = -1

EDIT_INTENT_KEYWORDS = (
    "fix",
    "change",
    "modify",
    "update",
    "rewrite",
    "refactor",
    "rename",
    "add",
    "remove",
    "patch",
    "implement",
    "edit",
    "create",
)

FEW_SHOT_VALID_OUTPUT_EXAMPLES = textwrap.dedent(
    """
    VALID OUTPUT EXAMPLES:

    Example A (creating a NEW file - requires full content):
    ### docs/new_file.md
    <<<FILE_CONTENT_START>>>
    # New File
    This is the full content.
    <<<FILE_CONTENT_END>>>

    Example B (editing an EXISTING file - use Search/Replace):
    ### src/main.py
    <<<FILE_CONTENT_START>>>
    <<<SEARCH>>>
    def old_function():
        return "hello"
    <<<REPLACE>>>
    def old_function():
        return "hello world"
        # Added world
    <<<FILE_CONTENT_END>>>
    """
).strip()


# --- Data classes ---
@dataclass
class RunContext:
    run_dir: Path
    repo_path: Path
    model: str
    question: str
    context_md: Path
    response_md: Path
    meta_json: Path


@dataclass
class OutputValidationResult:
    ok: bool
    has_file_blocks: bool
    reasons: list[str]
    parsed_files: Dict[str, str] = field(default_factory=dict)


@dataclass
class ConversationTurn:
    question: str
    answer: str
    run_dir: str
    applied: bool


# -------------------------
# Utilities
# -------------------------
def now_timestamp() -> str:
    return datetime.utcnow().strftime("%Y%m%d_%H%M%S")


def ensure_dir(path: str) -> None:
    Path(path).mkdir(parents=True, exist_ok=True)


def read_text_file(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return None
    except Exception:
        return None


def write_text_file(path: str, content: str) -> None:
    ensure_dir(os.path.dirname(path) or ".")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def safe_relpath(path: str, start: Optional[str] = None) -> str:
    try:
        return os.path.relpath(path, start or os.getcwd())
    except Exception:
        return path


# -------------------------
# Persistent History Utilities
# -------------------------
def conversation_turn_to_dict(turn: ConversationTurn) -> dict:
    return {
        "question": turn.question,
        "answer": turn.answer,
        "run_dir": turn.run_dir,
        "applied": turn.applied,
    }


def dict_to_conversation_turn(data: dict) -> ConversationTurn:
    return ConversationTurn(
        question=data.get("question", ""),
        answer=data.get("answer", ""),
        run_dir=data.get("run_dir", ""),
        applied=data.get("applied", False),
    )


def load_history(history_path: Path) -> list[ConversationTurn]:
    if not history_path.exists():
        return []
    try:
        data = json.loads(history_path.read_text(encoding="utf-8"))
        return [dict_to_conversation_turn(item) for item in data]
    except Exception:
        return []


def save_history(history_path: Path, turns: list[ConversationTurn]) -> None:
    history_path.parent.mkdir(parents=True, exist_ok=True)
    data = [conversation_turn_to_dict(t) for t in turns]
    history_path.write_text(json.dumps(data, indent=2), encoding="utf-8")


# -------------------------
# Enhanced Editing Utilities (Dry-run & Backups)
# -------------------------
def extract_paths_from_response(response_text: str) -> list[str]:
    """Extracts relative file paths from ### headings."""
    paths = []
    for match in re.finditer(r"^###\s+(.+)$", response_text, re.MULTILINE):
        paths.append(match.group(1).strip())
    return paths


def backup_repo_files(repo_path: Path, file_paths: list[str]) -> None:
    """Creates timestamped backups of files about to be modified."""
    if not file_paths:
        return
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = repo_path / ".lld_backups" / ts
    backup_dir.mkdir(parents=True, exist_ok=True)

    backed_up_count = 0
    for rel_path in file_paths:
        src = repo_path / rel_path
        if src.exists():
            dst = backup_dir / rel_path
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            backed_up_count += 1

    if backed_up_count > 0:
        print(
            f"[Safety] Backed up {backed_up_count} file(s) to {backup_dir.relative_to(repo_path)}")
    else:
        print(f"[Safety] No existing files found to backup (creating new files).")


# -------------------------
# Normalization Helpers
# -------------------------
def get_lang_from_path(path: str) -> str:
    ext = Path(path).suffix.lower().lstrip('.')
    lang_map = {
        "py": "python", "js": "javascript", "ts": "typescript",
        "md": "md", "json": "json", "yaml": "yaml", "yml": "yaml",
        "html": "html", "css": "css", "sh": "bash", "bash": "bash",
        "txt": "text", "xml": "xml", "sql": "sql", "rs": "rust",
        "go": "go", "java": "java", "cpp": "cpp", "c": "c", "h": "c",
    }
    return lang_map.get(ext, "")


def normalize_response_to_standard_md(response_text: str) -> str:
    """Converts our safe LLM delimiters back into standard markdown fences for logging."""
    def replace_start(match):
        path = match.group(1)
        lang = get_lang_from_path(path)
        return f"```{lang}"

    pattern_start = re.compile(
        r'(###\s+.+?)\n\s*<<<FILE_CONTENT_START>>>\s*\n')
    text = pattern_start.sub(replace_start, response_text)
    text = text.replace("<<<FILE_CONTENT_END>>>", "```")
    return text


# -------------------------
# Basic helpers
# -------------------------
def list_repo_choices(execute_lab_dir: Path) -> list[Path]:
    if not execute_lab_dir.exists():
        return []
    return sorted([p for p in execute_lab_dir.iterdir() if p.is_dir()])


def choose_repo(execute_lab_dir: Path, repo_arg: str | None) -> Path:
    if repo_arg:
        repo = Path(repo_arg)
        if not repo.exists() or not repo.is_dir():
            raise FileNotFoundError(
                f"Repo path does not exist or is not a directory: {repo}")
        return repo

    options = list_repo_choices(execute_lab_dir)
    if not options:
        raise FileNotFoundError(
            f"No repo folders found under {execute_lab_dir}")

    print("Choose repo to focus on:")
    for i, opt in enumerate(options, start=1):
        print(f"  {i}. {opt.name}")

    while True:
        raw = input("Repo number: ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1]
        print("Invalid selection. Enter one of the listed numbers.")


def discover_ollama_models() -> list[str]:
    try:
        completed = subprocess.run(
            ["ollama", "list"],
            text=True,
            capture_output=True,
            check=False,
            shell=False,
            encoding='utf-8',
            errors='replace'
        )
    except FileNotFoundError:
        return []

    if completed.returncode != 0:
        return []

    models: list[str] = []
    for line in completed.stdout.splitlines()[1:]:
        parts = line.split()
        if parts:
            models.append(parts[0])
    return models


def choose_model(model_arg: str | None) -> str:
    if model_arg:
        return model_arg

    models = discover_ollama_models()
    if not models:
        return input("Model name (example: qwen3.6): ").strip()

    print("Choose model:")
    for i, model in enumerate(models, start=1):
        print(f"  {i}. {model}")

    print("  m. Manual model name")

    while True:
        raw = input("Model choice: ").strip().lower()
        if raw == "m":
            return input("Model name: ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(models):
            return models[int(raw) - 1]
        print("Invalid selection. Enter a listed number or 'm'.")


def get_question(question_arg: str | None) -> str:
    if question_arg:
        return question_arg

    print("Enter your question (single line):")
    q = input("Q: ").strip()
    if not q:
        raise ValueError("Question cannot be empty.")
    return q


# -------------------------
# Git Context Helper
# -------------------------
def get_git_diff_context(repo_path: Path) -> str:
    """Safely fetches current uncommitted changes to give the LLM active context."""
    try:
        result = subprocess.run(
            ["git", "diff", "HEAD"],
            cwd=str(repo_path),
            text=True,
            capture_output=True,
            check=False,
            shell=False,
            encoding='utf-8',
            errors='replace'
        )
        if result.returncode == 0 and result.stdout.strip():
            return f"\n\nACTIVE GIT DIFF (Uncommitted changes in working tree):\n```\n{result.stdout.strip()}\n```\n"
    except Exception:
        pass
    return ""


def load_gitignore(path):
    gitignore_path = os.path.join(path, '.gitignore')
    if os.path.exists(gitignore_path):
        with open(gitignore_path, 'r') as f:
            return pathspec.PathSpec.from_lines('gitwildmatch', f)
    return None


def code_to_markdown(src_dir, out_file):
    spec = load_gitignore(src_dir)
    with open(out_file, 'w', encoding='utf-8') as md:
        md.write(f"# Documentation for {src_dir}\n\n")
        for root, dirs, files in os.walk(src_dir):
            for file in files:
                full = os.path.join(root, file)
                rel = os.path.relpath(full, src_dir)
                if spec and spec.match_file(rel):
                    continue  # skip ignored files

                # Skip binary files by extension
                ext = os.path.splitext(file)[1].lstrip('.').lower()
                binary_extensions = {
                    'pyc', 'pyo', 'so', 'dll', 'exe', 'bin', 'dat',
                    'png', 'jpg', 'jpeg', 'gif', 'bmp', 'ico', 'webp',
                    'zip', 'tar', 'gz', 'rar', '7z', 'whl', 'egg',
                    'pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx',
                    'mp3', 'mp4', 'avi', 'mkv', 'mov', 'wav',
                    'ttf', 'otf', 'woff', 'woff2', 'eot',
                    'db', 'sqlite', 'sqlite3',
                }
                if ext in binary_extensions:
                    md.write(
                        f"## {rel}\n\n```\n<!-- Skipped: binary file -->\n```\n\n")
                    continue

                # Write a section header for each file
                md.write(f"## {rel}\n\n")
                lang = ext if ext else ''
                md.write(f"```{lang}\n")
                try:
                    # Use errors='replace' to handle non-UTF-8 content
                    if lang == 'md':
                        with open(full, 'r', encoding='utf-8', errors='replace') as f:
                            for line in f.readlines():
                                if line.startswith('#'):
                                    md.write(f'##{line}')
                                else:
                                    md.write(f'{line}')
                            md.write("\n```\n\n")
                    else:
                        with open(full, 'r', encoding='utf-8', errors='replace') as f:
                            md.write(f.read())
                except Exception as e:
                    md.write(f"<!-- Could not read file: {e} -->")
                md.write("\n```\n\n")


def run_export(repo_path: Path, out_md: Path) -> str:
    """Run the exporter script to produce a markdown snapshot of the repo."""
    # Use the inline function instead of subprocess
    code_to_markdown(repo_path, out_md)
    return out_md.read_text(encoding='utf-8', errors='replace')


# -------------------------
# Prompt style utilities
# -------------------------
def seed_prompt_styles() -> dict:
    # Made dynamic: no longer hardcodes "Type A / Type B". The LLM's intent dictates the format.
    strict_contract = textwrap.dedent(
        """
        You are an intelligent code-assistant architect. You analyze repositories and execute changes.
        
        CRITICAL OUTPUT CONTRACT:
        1. If your planning phase determined NO code changes are needed:
           - Return ONLY plain prose explaining your answer.
           
        2. If your planning phase determined code changes ARE needed:
           - Return ONLY file blocks. Do NOT include prose before, between, or after file blocks.
           - You MUST obey the strategy you chose in the planning phase (surgical vs full).

           ### relative/path/from/repo/root.ext
           <<<FILE_CONTENT_START>>>
           [If strategy was 'surgical', use <<<SEARCH>>> and <<<REPLACE>>> blocks]
           [If strategy was 'full', provide complete final file content]
           <<<FILE_CONTENT_END>>>

        3. If your planning phase determined a NEW file is needed:
           - Return EXACTLY ONE file block. You may include headings and explanations inside the file block itself.

        4. General file block rules:
           - Heading must be exactly a relative file path (no backticks, forward slashes).
           - NO diff hunks (+/- lines or @@).
           - Content CAN safely contain triple backticks if needed (e.g. for markdown files).

        {few_shot}
        """
    ).strip().format(few_shot=FEW_SHOT_VALID_OUTPUT_EXAMPLES)

    now = datetime.now().isoformat(timespec="seconds")
    styles = [
        {
            "id": "seed_style_contract_v1",
            "name": "Strict Contract",
            "instruction": strict_contract,
            "score": 0, "uses": 0, "successes": 0,
            "generator_model": "seed", "target_models": [], "model_stats": {}, "created_at": now,
        },
        {
            "id": "seed_style_compact_v1",
            "name": "Compact Parser Contract",
            "instruction": (
                "Parse-safe output only. If no changes: prose only. If changes: ONLY file blocks "
                "using <<<FILE_CONTENT_START>>> and <<<FILE_CONTENT_END>>>. Follow the strategy dictated by your planning phase. "
                "No backticked paths, no diff format.\n\n" +
                    f"{FEW_SHOT_VALID_OUTPUT_EXAMPLES}"
            ),
            "score": 0, "uses": 0, "successes": 0,
            "generator_model": "seed", "target_models": [], "model_stats": {}, "created_at": now,
        },
        {
            "id": "seed_style_enforced_v1",
            "name": "Enforced Apply Format",
            "instruction": (
                "Importer-safe output. Prose for explanations. File blocks for edits. "
                "Use <<<FILE_CONTENT_START>>> and <<<FILE_CONTENT_END>>>. "
                "Execute the exact strategy you planned.\n\n" +
                    f"{FEW_SHOT_VALID_OUTPUT_EXAMPLES}"
            ),
            "score": 0, "uses": 0, "successes": 0,
            "generator_model": "seed", "target_models": [], "model_stats": {}, "created_at": now,
        },
    ]
    return {"version": 1, "updated_at": now, "styles": styles}


def load_prompt_styles(db_path: Path = PROMPT_STYLE_DB) -> dict:
    if not db_path.exists():
        data = seed_prompt_styles()
        save_prompt_styles(data, db_path)
        return data
    try:
        data = json.loads(db_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("styles"), list):
            raise ValueError
        return data
    except Exception:
        data = seed_prompt_styles()
        save_prompt_styles(data, db_path)
        return data


def save_prompt_styles(data: dict, db_path: Path = PROMPT_STYLE_DB) -> None:
    data["updated_at"] = datetime.now().isoformat(timespec="seconds")
    db_path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def append_prompt_style_usage(entry: dict, log_path: Path = PROMPT_STYLE_USAGE_LOG) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def style_model_stats(style: dict, target_model: str) -> dict:
    table = style.setdefault("model_stats", {})
    stats = table.get(target_model)
    if not isinstance(stats, dict):
        stats = {"uses": 0, "successes": 0, "score": 0}
        table[target_model] = stats
    stats.setdefault("uses", 0)
    stats.setdefault("successes", 0)
    stats.setdefault("score", 0)
    return stats


def weighted_random_style(styles: list[dict], target_model: str) -> dict:
    if not styles:
        return seed_prompt_styles()["styles"][0]
    weights = [max(1, int(style_model_stats(s, target_model).get(
        "score", s.get("score", 0))) + 2) for s in styles]
    return random.choices(styles, weights=weights, k=1)[0]


def record_style_usage(style: dict, target_model: str) -> None:
    style.setdefault("uses", 0)
    style["uses"] += 1
    style_model_stats(style, target_model)["uses"] += 1
    if target_model not in style.setdefault("target_models", []):
        style["target_models"].append(target_model)


def record_style_outcome(style: dict, target_model: str, valid: bool) -> None:
    style.setdefault("score", 0)
    style.setdefault("successes", 0)
    stats = style_model_stats(style, target_model)
    if valid:
        style["successes"] += 1
        style["score"] += 1
        stats["successes"] += 1
        stats["score"] += 1
    else:
        style["score"] += MALFORMED_SCORE_PENALTY
        stats["score"] += MALFORMED_SCORE_PENALTY


# -------------------------
# Retry guidance builder
# -------------------------
def build_retry_guidance(malformed_reasons: list[str]) -> str:
    reason_lines = "\n".join(
        f"- {reason}" for reason in (malformed_reasons or [])[:6])
    return textwrap.dedent(
        f"""
        RETRY CORRECTION INSTRUCTIONS:
        Your previous output was malformed. Correct these issues:
        {reason_lines}
        
        If making changes, use ONLY file blocks with <<<FILE_CONTENT_START>>> and <<<FILE_CONTENT_END>>>.
        If explaining, use prose ONLY.
        """
    ).strip()


# -------------------------
# Ollama interface
# -------------------------
def ask_model_ollama(model: str, messages: list[dict], capture_thinking: bool = False) -> Union[str, tuple[str, str]]:
    """
    Streams a response from Ollama.
    If capture_thinking=True, returns (content, thinking) tuple.
    Otherwise returns content string only (backward compatible).
    """
    try:
        from ollama import chat
    except ImportError as exc:
        raise RuntimeError(
            "The 'ollama' Python package is not installed.") from exc

    stream = chat(model=model, messages=messages, stream=True)
    in_thinking = False
    content = ""
    thinking = ""

    for chunk in stream:
        message = chunk.get("message") if isinstance(
            chunk, dict) else getattr(chunk, "message", None)
        if not message:
            continue

        chunk_thinking = message.get("thinking") if isinstance(
            message, dict) else getattr(message, "thinking", None)
        chunk_content = message.get("content") if isinstance(
            message, dict) else getattr(message, "content", None)

        if chunk_thinking:
            if not in_thinking:
                in_thinking = True
                print("Thinking:\n", end="", flush=True)
            print(chunk_thinking, end="", flush=True)
            if capture_thinking:
                thinking += chunk_thinking
            continue

        if chunk_content:
            if in_thinking:
                in_thinking = False
                print("\n\nAnswer:\n", end="", flush=True)
            print(chunk_content, end="", flush=True)
            content += chunk_content

    if content:
        print("\n", flush=True)

    if capture_thinking:
        return content, thinking
    return content


# -------------------------
# AGENTIC: Intent Strategist (Replaces hardcoded routing)
# -------------------------
def heuristic_intent_check(question: str) -> Optional[dict]:
    """
    Fast pre-check: if the user's question contains explicit file-creation signals,
    bypass the LLM intent step entirely. This prevents thinking models from
    derailing the pipeline when they put JSON in thinking tokens.
    """
    q_lower = question.lower().strip()

    # Pattern: "write [content] to <filename>" or "write <filename>"
    write_to_match = re.search(r'write\s+.*?\bto\s+[\w./\\-]+\.\w+', q_lower)
    if write_to_match:
        return {"intent": "create", "reasoning": "Heuristic: 'write ... to <file>' pattern detected.", "strategy": "full"}

    # Pattern: "create <filename>" or "create file <filename>"
    create_file_match = re.search(r'create\s+(?:a\s+)?(?:new\s+)?(?:file\s+)?[\w./\\-]+\.\w+', q_lower)
    if create_file_match:
        return {"intent": "create", "reasoning": "Heuristic: 'create <file>' pattern detected.", "strategy": "full"}

    # Pattern: "save to <filename>" or "output to <filename>"
    save_to_match = re.search(r'(?:save|output|dump|export)\s+.*?\bto\s+[\w./\\-]+\.\w+', q_lower)
    if save_to_match:
        return {"intent": "create", "reasoning": "Heuristic: 'save/output to <file>' pattern detected.", "strategy": "full"}

    # Pattern: "generate <filename>"
    generate_match = re.search(r'generate\s+(?:a\s+)?(?:new\s+)?[\w./\\-]+\.\w+', q_lower)
    if generate_match:
        return {"intent": "create", "reasoning": "Heuristic: 'generate <file>' pattern detected.", "strategy": "full"}

    # Pattern: explicit edit keywords targeting a specific file
    edit_file_match = re.search(r'(?:fix|edit|update|modify|patch|refactor)\s+[\w./\\-]+\.\w+', q_lower)
    if edit_file_match:
        return {"intent": "edit", "reasoning": "Heuristic: 'edit <file>' pattern detected.", "strategy": "full"}

    return None


def _extract_json_from_text(text: str) -> Optional[dict]:
    """
    Attempts to extract a JSON object with an 'intent' key from raw text.
    Handles markdown code blocks and raw JSON embedded in prose.
    """
    source = (text or "").strip()
    if not source:
        return None

    # Try: ```json ... ``` or ``` ... ```
    json_block = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', source, re.DOTALL)
    if json_block:
        try:
            parsed = json.loads(json_block.group(1))
            if isinstance(parsed, dict) and "intent" in parsed:
                return parsed
        except Exception:
            pass

    # Try: raw JSON object anywhere in the text
    json_raw = re.search(r'\{[^{}]*"intent"[^{}]*\}', source, re.DOTALL)
    if json_raw:
        try:
            parsed = json.loads(json_raw.group(0))
            if isinstance(parsed, dict) and "intent" in parsed:
                return parsed
        except Exception:
            pass

    # Try: direct parse of the whole string
    try:
        parsed = json.loads(source)
        if isinstance(parsed, dict) and "intent" in parsed:
            return parsed
    except Exception:
        pass

    return None


def ask_model_intent(model: str, question: str, context_summary: str) -> dict:
    """
    Fast planning step. Uses heuristic pre-check first, then LLM as fallback.
    Thinking-model safe: captures thinking tokens and parses JSON from both
    content and thinking output.
    """
    # --- HEURISTIC FAST PATH ---
    heuristic = heuristic_intent_check(question)
    if heuristic:
        return heuristic

    # --- LLM FALLBACK ---
    intent_prompt = textwrap.dedent(
        f"""
        You are an AI planning an action on a codebase.
        Analyze the user's request and the provided context summary.
        
        Return JSON only with these exact keys:
          - "intent": "explain" (no code changes) or "edit" (modify existing files) or "create" (create a new file)
          - "reasoning": "One sentence explaining why."
          - "strategy": "surgical" (use search/replace on existing files) or "full" (output entire files) or "none" (if intent is explain).

        User Request: {question}
        
        Context Summary:
        {context_summary[:3000]}
        """
    ).strip()

    messages = [
        {"role": "system", "content": "You are a fast planning module. Return JSON only. Do NOT use thinking tags. Output ONLY valid JSON."},
        {"role": "user", "content": intent_prompt},
    ]
    raw, thinking_text = ask_model_ollama(model, messages, capture_thinking=True)

    # Try parsing from content first, then from thinking as fallback
    for source in [raw, thinking_text]:
        parsed = _extract_json_from_text(source)
        if parsed:
            return parsed

    # --- IMPROVED KEYWORD FALLBACK ---
    # Check both the LLM output AND the original question
    raw_l = (raw or "").strip().lower()
    q_lower = question.lower()
    combined = f"{raw_l} {q_lower}"

    if "create" in combined or "write" in combined or "generate" in combined or "new file" in combined:
        return {"intent": "create", "reasoning": "Fallback keyword detection (create/write/generate)", "strategy": "full"}
    if any(kw in combined for kw in ("edit", "fix", "add", "modify", "update", "refactor", "patch", "implement")):
        return {"intent": "edit", "reasoning": "Fallback keyword detection (edit/fix/add/...)", "strategy": "full"}

    return {"intent": "explain", "reasoning": "Default fallback", "strategy": "none"}


# -------------------------
# Generated style helper
# -------------------------
def create_generated_style(model: str, target_model: str, malformed_reasons: list[str]) -> dict:
    reason_text = "\n".join(
        f"- {r}" for r in malformed_reasons) if malformed_reasons else "- No reason provided"
    prompt = textwrap.dedent(f"""
        Generate a STRICT instruction block for a coding assistant.
        Target model: {target_model}. Recent malformed reasons: {reason_text}
        Return JSON only: {{"name": "short name", "instruction": "full instruction"}}
        Instruction must enforce: prose for explanations, file blocks with <<<FILE_CONTENT_START>>> and <<<FILE_CONTENT_END>>> for edits.
    """).strip()

    raw = ask_model_ollama(model, [{"role": "system", "content": "Return JSON only."}, {
                           "role": "user", "content": prompt}])
    name, instr = "AI Generated Style", "Output prose for explanations. Output file blocks with our custom heredoc tags for edits."
    try:
        p = json.loads(raw)
        if isinstance(p.get("name"), str):
            name = p["name"].strip()
        if isinstance(p.get("instruction"), str):
            instr = p["instruction"].strip()
    except Exception:
        pass

    return {
        "id": f"ai_style_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}", "name": name, "instruction": instr,
        "score": 0, "uses": 0, "successes": 0, "generator_model": model,
        "target_models": [target_model], "model_stats": {target_model: {"uses": 0, "successes": 0, "score": 0}},
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }


# -------------------------
# Validation and path safety
# -------------------------
def is_valid_relative_path(path: str) -> bool:
    p = path.strip()
    if not p or "`" in p or p.startswith("/") or p.startswith("\\"):
        return False
    if re.match(r"^[a-zA-Z]:", p):
        return False
    if ".." in p.replace("\\", "/").split("/"):
        return False
    return True


def validate_output_for_importer(output: str) -> OutputValidationResult:
    text = (output or "").strip("\n")
    if not text.strip():
        return OutputValidationResult(ok=False, has_file_blocks=False, reasons=["Output is empty."])

    if "<<<FILE_CONTENT_START>>>" not in text:
        return OutputValidationResult(ok=True, has_file_blocks=False, reasons=[])

    lines = text.splitlines()
    reasons: list[str] = []
    i = 0
    has_blocks = False
    parsed_files: Dict[str, str] = {}

    while i < len(lines):
        while i < len(lines) and not lines[i].strip():
            i += 1
        if i >= len(lines):
            break

        header = lines[i].strip()
        m = re.match(r"^###\s+(.+)$", header)
        if not m:
            reasons.append(
                f"Unexpected text outside file block at line {i + 1}: {header[:80]}")
            break

        rel_path = m.group(1).strip()
        if not is_valid_relative_path(rel_path):
            reasons.append(
                f"Invalid file path header at line {i + 1}: {rel_path}")
            break

        i += 1
        while i < len(lines) and not lines[i].strip():
            i += 1
        if i >= len(lines):
            reasons.append(
                "Missing <<<FILE_CONTENT_START>>> tag after file path heading.")
            break

        if lines[i].strip() != "<<<FILE_CONTENT_START>>>":
            reasons.append(
                f"Expected <<<FILE_CONTENT_START>>> tag at line {i + 1}, got: {lines[i].strip()}")
            break

        i += 1
        code_lines: list[str] = []
        while i < len(lines) and lines[i].strip() != "<<<FILE_CONTENT_END>>>":
            code_lines.append(lines[i])
            i += 1

        if i >= len(lines):
            reasons.append(
                "Unclosed file block (missing <<<FILE_CONTENT_END>>>).")
            break

        code_text = "\n".join(code_lines).strip()
        if not code_text:
            reasons.append("File block contains empty code content.")
            break

        if "*** Begin Patch" in code_text or "*** Update File:" in code_text:
            reasons.append(
                "Patch/diff payload detected; use SEARCH/REPLACE blocks instead.")
            break

        has_blocks = True
        parsed_files[rel_path] = code_text
        i += 1

    return OutputValidationResult(ok=len(reasons) == 0, has_file_blocks=has_blocks, reasons=reasons, parsed_files=parsed_files)


# -------------------------
# History / conversation helpers
# -------------------------
def render_history(turns: list[ConversationTurn], max_turns: int) -> str:
    if not turns:
        return ""
    window = turns[-max(1, max_turns):]
    return "\n".join(f"Turn {idx}\nUser:\n{t.question}\n\nAssistant:\n{t.answer}\n" for idx, t in enumerate(window, start=1))


def build_question_with_history(question: str, turns: list[ConversationTurn], max_turns: int) -> str:
    history_text = render_history(turns, max_turns)
    if not history_text:
        return question
    return f"Conversation History (oldest to newest):\n{history_text}\n\nCurrent User Request:\n{question}"


def build_messages_for_model(instruction_style: str, question_payload: str, turns: list[ConversationTurn], context: str | None = None) -> list[dict]:
    messages = []
    sys_content = f"{instruction_style}\n\nRepository Context:\n{context}" if context else instruction_style
    messages.append({"role": "system", "content": sys_content})
    for turn in turns:
        messages.append({"role": "user", "content": turn.question})
        messages.append({"role": "assistant", "content": turn.answer})
    messages.append({"role": "user", "content": question_payload})
    return messages


# -------------------------
# Core processing
# -------------------------
def process_turn(
    *, repo_path: Path, model: str, question: str, runs_dir: Path,
    max_context_chars: int, max_malformed_retries: int, auto_apply: bool,
    turns: list[ConversationTurn], history_window: int, hard_mode: bool,
    save_md_flag: bool = False, dry_run: bool = False,
) -> tuple[int, ConversationTurn]:
    temp_context_md = runs_dir / "_latest_context.md"
    temp_context_md.parent.mkdir(parents=True, exist_ok=True)

    print(f"[1/4] Exporting repo context from {repo_path} ...")
    base_context = run_export(repo_path, temp_context_md)

    git_context = get_git_diff_context(repo_path)
    if git_context:
        base_context += git_context
        print("[1/4] Injected active git diff into context.")

    # -------------------------------------------------------------
    # AGENTIC STEP 1: LLM Strategizes Intent (Replaces Routing)
    # -------------------------------------------------------------
    print("[2/4] Analyzing intent and strategy ...")
    intent_decision = ask_model_intent(model, question, base_context)
    intent_action = intent_decision.get("intent", "explain").lower()
    intent_reasoning = intent_decision.get("reasoning", "")
    intent_strategy = intent_decision.get("strategy", "full").lower()

    strategy_display = intent_strategy.upper(
    ) if intent_action in ("edit", "create") else "N/A"
    print(
        f"      -> Intent: {intent_action.upper()} | Strategy: {strategy_display} | Reason: {intent_reasoning}")

    # The LLM dictates if we need file blocks, unless user forces hard_mode
    require_file_blocks = (intent_action in ("edit", "create")) or hard_mode

    style_db = load_prompt_styles()
    styles = style_db.get("styles", [])
    max_attempts = max(1, min(5, int(max_malformed_retries)))
    attempt_records: list[dict] = []
    malformed_reasons: list[str] = []
    response = ""

    question_payload = build_question_with_history(
        question, turns, history_window)

    # -------------------------------------------------------------
    # AGENTIC STEP 2: LLM Executes based on its own Strategy
    # -------------------------------------------------------------
    if require_file_blocks:
        for attempt in range(1, max_attempts + 1):
            style = weighted_random_style(styles, model) if attempt <= 2 else create_generated_style(
                model, model, malformed_reasons)
            if attempt > 2:
                styles.append(style)
                save_prompt_styles(style_db)

            record_style_usage(style, model)
            model_stats_before = style_model_stats(style, model)
            score_before = int(style.get("score", 0))
            model_score_before = int(model_stats_before.get("score", 0))

            instruction_style = style.get("instruction", "")

            # DYNAMIC PROMPTING: Inject the LLM's own plan back into the prompt
            if intent_action == "create":
                dynamic_directives = textwrap.dedent(f"""
                    PREVIOUS PLANNING PHASE OUTPUT:
                    - You determined the intent is: {intent_action}
                    - Your reasoning: {intent_reasoning}
                    
                    EXECUTION DIRECTIVES BASED ON YOUR PLAN:
                    1. You MUST output exactly one file block using <<<FILE_CONTENT_START>>> and <<<FILE_CONTENT_END>>>.
                    2. Because you are creating a new file, you MAY include headings and prose inside the file block itself.
                    3. Do NOT output diff hunks, patch formats, or explanations *outside* the file block.
                """).strip()
            else:
                dynamic_directives = textwrap.dedent(f"""
                    PREVIOUS PLANNING PHASE OUTPUT:
                    - You determined the intent is: {intent_action}
                    - Your reasoning: {intent_reasoning}
                    - Your chosen strategy: {intent_strategy}
                    
                    EXECUTION DIRECTIVES BASED ON YOUR PLAN:
                    1. You MUST output file blocks using <<<FILE_CONTENT_START>>> and <<<FILE_CONTENT_END>>>.
                    2. Because your strategy was '{intent_strategy}', you MUST {"use <<<SEARCH>>> and <<<REPLACE>>> blocks inside the files." if intent_strategy == "surgical" else "output the FULL, COMPLETE file content for any file you modify."}
                    3. Do NOT include explanatory prose outside the file blocks.
                """).strip()

            instruction_style = f"{instruction_style}\n\n{dynamic_directives}"

            if attempt > 1 and malformed_reasons:
                instruction_style = f"{instruction_style}\n\n{build_retry_guidance(malformed_reasons)}"

            messages = build_messages_for_model(
                instruction_style=instruction_style, question_payload=question_payload, turns=turns, context=base_context)

            print(
                f"[3/4] Executing strategy: {model} (attempt {attempt}/{max_attempts}, style={style.get('id', 'unknown')}) ...")
            response = ask_model_ollama(model, messages)
            validation = validate_output_for_importer(response)

            if validation.ok and require_file_blocks and not validation.has_file_blocks:
                validation = OutputValidationResult(ok=False, has_file_blocks=False, reasons=[
                                                    "Intent was edit/create, but prose-only output was rejected."])

            record_style_outcome(style, model, validation.ok)
            save_prompt_styles(style_db)

            model_stats_after = style_model_stats(style, model)
            usage_entry = {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "attempt": attempt, "style_id": style.get("id"), "style_name": style.get("name"),
                "target_model": model, "intent_action": intent_action, "intent_strategy": intent_strategy,
                "style_score_before": score_before, "style_score_after": int(style.get("score", 0)),
                "model_score_before": model_score_before, "model_score_after": int(model_stats_after.get("score", 0)),
                "hard_mode": hard_mode, "valid": validation.ok, "has_file_blocks": validation.has_file_blocks,
                "reasons": validation.reasons,
            }
            append_prompt_style_usage(usage_entry)
            attempt_records.append(usage_entry)

            if validation.ok:
                break

            malformed_reasons = validation.reasons or ["Malformed output"]
            print("Output malformed. Retrying with adjusted strategy...")
            for reason in malformed_reasons:
                print(f"  - {reason}")

        final_validation = validate_output_for_importer(response)
        if final_validation.ok and require_file_blocks and not final_validation.has_file_blocks:
            final_validation = OutputValidationResult(ok=False, has_file_blocks=False, reasons=[
                                                      "Intent was edit/create, but prose-only output was rejected."])

    else:
        # LLM chose to EXPLAIN
        style = weighted_random_style(styles, model)
        record_style_usage(style, model)
        instruction_style = style.get("instruction", "")

        # Dynamic directive for explanations
        instruction_style = f"{instruction_style}\n\nPLANNING PHASE OUTPUT:\nYou determined intent is 'explain' with reasoning: '{intent_reasoning}'.\nEXECUTE: Provide a clear prose explanation. Do NOT output file blocks."

        messages = build_messages_for_model(
            instruction_style=instruction_style, question_payload=question_payload, turns=turns, context=base_context)
        print(f"[3/4] Generating explanation: {model} ...")
        response = ask_model_ollama(model, messages)
        final_validation = OutputValidationResult(
            ok=True, has_file_blocks=False, reasons=[])

        # Proper logging structure instead of appending __doc__ (None)
        usage_entry = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "attempt": 1,
            "style_id": style.get("id"),
            "style_name": style.get("name"),
            "target_model": model,
            "intent_action": "explain",
            "valid": True,
            "has_file_blocks": False,
            "reasons": [],
        }
        append_prompt_style_usage(usage_entry)
        attempt_records.append(usage_entry)

    # ---------------------------------------------------------------------
    # Common post‑processing
    # ---------------------------------------------------------------------
    if not final_validation.ok:
        print("Failed to produce valid output after retries.")
        for reason in final_validation.reasons:
            print(f"  - {reason}")

    print("[4/4] Saving run artifacts ...")
    normalized_response = normalize_response_to_standard_md(response)
    run_ctx = save_run(runs_dir=runs_dir, repo_path=repo_path, model=model,
                       question=question, context_text=base_context, response_text=normalized_response)
    (run_ctx.run_dir / "prompt_attempts.json").write_text(
        json.dumps(attempt_records, indent=2), encoding="utf-8")
    print(f"Saved response to: {run_ctx.response_md}")

    applied = False

    # SMART APPLY CHECK: Only ask/prompt to apply if file blocks actually exist
    if not final_validation.has_file_blocks:
        print("[Apply] Skipped (no file edits detected in response).")
    else:
        should_apply = False
        # Auto-apply if flagged, OR auto-apply if the user is explicitly asking to create a file
        if auto_apply or intent_action == "create":
            should_apply = True
        elif dry_run:
            should_apply = True
        else:
            should_apply = input(
                "Apply model file edits to repo now? [y/N]: ").strip().lower() in {"y", "yes"}

        if should_apply:
            if not final_validation.ok:
                print("[Apply] Blocked: Output is malformed.")
                return 2, ConversationTurn(question=question, answer=response, run_dir=str(run_ctx.run_dir), applied=False)

            if dry_run:
                print("[Apply] DRY RUN ACTIVE...")
                for p in final_validation.parsed_files.keys():
                    print(f"  -> {p}")
                return 0, ConversationTurn(question=question, answer=response, run_dir=str(run_ctx.run_dir), applied=False)

            print("[Apply] Executing writes based on strategy...")
            backup_repo_files(repo_path, list(
                final_validation.parsed_files.keys()))

            apply_success = True
            for rel_path, content in final_validation.parsed_files.items():
                try:
                    target_file = repo_path / rel_path
                    target_file.parent.mkdir(parents=True, exist_ok=True)

                    if "<<<SEARCH>>>" in content and target_file.exists():
                        if apply_surgical_edit(target_file, content):
                            print(f"  -> Surgically edited: {rel_path}")
                        else:
                            print(f"  -> [Fallback] Full write: {rel_path}")
                            target_file.write_text(content, encoding="utf-8")
                    else:
                        # Print specific messaging if the LLM is creating a new file
                        print(f"  -> Created new file: {rel_path}")
                        target_file.write_text(content, encoding="utf-8")
                except Exception as e:
                    print(f"  -> FAILED: {rel_path} ({e})")
                    apply_success = False

            if not apply_success:
                return 2, ConversationTurn(question=question, answer=response, run_dir=str(run_ctx.run_dir), applied=False)
            print("Apply step completed.")
            applied = True

    print("Done.")
    return 0, ConversationTurn(question=question, answer=response, run_dir=str(run_ctx.run_dir), applied=applied)


def apply_surgical_edit(target_file: Path, content_payload: str) -> bool:
    """Applies a search/replace block to an existing file. Returns True on success."""
    match = re.search(r'<<<SEARCH>>>(.*?)<<<REPLACE>>>(.*)',
                      content_payload, re.DOTALL)
    if not match or not target_file.exists():
        return False

    search_str, replace_str = match.group(1), match.group(2)
    original_text = target_file.read_text(encoding="utf-8")

    if search_str not in original_text:
        print(f"    [Warning] SEARCH block not found in {target_file.name}.")
        return False

    target_file.write_text(original_text.replace(
        search_str, replace_str, 1), encoding="utf-8")
    return True


def save_run(runs_dir: Path, repo_path: Path, model: str, question: str, context_text: str, response_text: str) -> RunContext:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = runs_dir / f"run_{ts}"
    run_dir.mkdir(parents=True, exist_ok=True)
    context_md, response_md, meta_json = run_dir / \
        "repo_context.md", run_dir / "response.md", run_dir / "meta.json"

    context_md.write_text(context_text, encoding="utf-8")
    response_md.write_text(response_text, encoding="utf-8")
    meta_json.write_text(json.dumps({"repo": str(repo_path), "model": model, "question": question,
                         "created_at": datetime.now().isoformat(timespec="seconds")}, indent=2), encoding="utf-8")
    return RunContext(run_dir=run_dir, repo_path=repo_path, model=model, question=question, context_md=context_md, response_md=response_md, meta_json=meta_json)


# -------------------------
# CLI / main
# -------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--runs-dir", default=Path("qa_runs"))
    parser.add_argument("--max-context-chars", type=int, default=200000)
    parser.add_argument("--max-malformed-retries", type=int,
                        default=MAX_MALFORMED_RETRIES)
    parser.add_argument("--auto-apply", action="store_true")
    parser.add_argument("--hard-mode", action="store_true")
    parser.add_argument("--save-md", action="store_true")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview file changes without applying.")
    args = parser.parse_args(argv)

    repo_path = Path(args.repo)
    runs_dir = Path(args.runs_dir)
    runs_dir.mkdir(parents=True, exist_ok=True)
    model = choose_model(args.model)

    history_path = runs_dir / f"history_{repo_path.name}_{model}.json"
    turns = load_history(history_path)
    if turns:
        print(f"Loaded {len(turns)} prior turns from {history_path.name}")

    print("Chat loop ready.")
    print("Commands: /help, /clear, /restart, /history, /newrepo <path>, /exit")

    while True:
        try:
            q = input("Q: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            save_history(history_path, turns)
            return 0

        if not q:
            continue
        if q.startswith("/exit"):
            save_history(history_path, turns)
            return 0
        if q.startswith("/clear") or q.startswith("/restart"):
            turns = []
            save_history(history_path, turns)
            print("History cleared/restarted.")
            continue

        exit_code, turn = process_turn(
            repo_path=repo_path, model=model, question=q, runs_dir=runs_dir,
            max_context_chars=args.max_context_chars, max_malformed_retries=args.max_malformed_retries,
            auto_apply=args.auto_apply, turns=turns, history_window=DEFAULT_HISTORY_WINDOW,
            hard_mode=args.hard_mode, save_md_flag=args.save_md, dry_run=args.dry_run,
        )
        turns.append(turn)
        save_history(history_path, turns)


if __name__ == "__main__":
    raise SystemExit(main())