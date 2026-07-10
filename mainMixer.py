#!/usr/bin/env python3
# mainMixer.py (patched)
from __future__ import annotations

import argparse
import json
import random
import os
import shutil
import re
import subprocess
import sys
import textwrap
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Dict, Any
from pathlib import Path

# --- Config / paths ---
EXPORT_SCRIPT = Path("py_scripts/out/02_app_to_md.py")
APPLY_SCRIPT = Path("py_scripts/in/02_response_to_script.py")
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
    "improve",
    "clarify",
)

FEW_SHOT_VALID_OUTPUT_EXAMPLES = textwrap.dedent(
    """
    VALID OUTPUT EXAMPLES:

    Example A (no code changes needed):
    The current code already satisfies the request. No file edits are required.

    Example B (code changes needed):
    ### src/example.py
    ```py
    def greet() -> str:
        return "hello"
    ```
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


def run_export(repo_path: Path, out_md: Path) -> None:
    """
    Run the exporter script to produce a markdown snapshot of the repo.
    The file (e.g., qa_runs/_latest_context.md) is read and passed into the model.
    """
    command = [sys.executable, str(EXPORT_SCRIPT), str(repo_path), str(out_md)]
    completed = subprocess.run(
        command, text=True, capture_output=True, check=False, shell=False)
    if completed.returncode != 0:
        raise RuntimeError(
            "Failed to build repo markdown context\n"
            f"Command: {' '.join(command)}\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )


# -------------------------
# Prompt style utilities
# -------------------------
def seed_prompt_styles() -> dict:
    strict_contract = textwrap.dedent(
        """
        You are reviewing a code repository from markdown context.

        CRITICAL OUTPUT CONTRACT (must be followed exactly):
        1. If no code changes are needed:
           - Return only plain prose.
           - Do not include any fenced code blocks.

        2. If code changes are needed:
           - Return only one or more file blocks in this exact format.
           - Do not include prose before, between, or after file blocks.

           ### relative/path/from/repo_root.ext
           ```language
           FULL FILE CONTENT ONLY
           ```

        3. File block rules:
           - The heading line must be exactly a relative file path.
           - Do not wrap the path in backticks.
           - Use forward slashes in paths.
           - Do not output diff hunks, +/- lines, or patch format.
           - Do not output partial snippets; each file block must contain complete final file content.
           - Only include files that must change.

        4. This response will be parsed automatically by a strict importer.
           Any extra prose or malformed blocks can break apply.

        {few_shot}
        """
    ).strip().format(few_shot=FEW_SHOT_VALID_OUTPUT_EXAMPLES)

    now = datetime.now().isoformat(timespec="seconds")
    styles = [
        {
            "id": "seed_style_contract_v1",
            "name": "Strict Contract",
            "instruction": strict_contract,
            "score": 0,
            "uses": 0,
            "successes": 0,
            "generator_model": "seed",
            "target_models": [],
            "model_stats": {},
            "created_at": now,
        },
        {
            "id": "seed_style_compact_v1",
            "name": "Compact Parser Contract",
            "instruction": (
                "Return output that can be parsed automatically. "
                "If no file edits are needed, return prose only with no fences. "
                "If file edits are needed, return ONLY repeated blocks in this exact pattern: "
                "### relative/path/from/repo_root.ext then a fenced code block containing FULL final file content. "
                "No backticks around paths, no diff format, no extra prose.\n\n"
                f"{FEW_SHOT_VALID_OUTPUT_EXAMPLES}"
            ),
            "score": 0,
            "uses": 0,
            "successes": 0,
            "generator_model": "seed",
            "target_models": [],
            "model_stats": {},
            "created_at": now,
        },
        {
            "id": "seed_style_enforced_v1",
            "name": "Enforced Apply Format",
            "instruction": (
                "Output must be importer-safe. "
                "Allowed output type A: prose only, no fenced blocks. "
                "Allowed output type B: file blocks only, each as: "
                "### relative/path/from/repo_root.ext followed by ```lang and complete file content and closing ```. "
                "Reject all other formats mentally: no patch hunks, no bullets, no explanations when output type B is used.\n\n"
                f"{FEW_SHOT_VALID_OUTPUT_EXAMPLES}"
            ),
            "score": 0,
            "uses": 0,
            "successes": 0,
            "generator_model": "seed",
            "target_models": [],
            "model_stats": {},
            "created_at": now,
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
    except Exception:
        data = seed_prompt_styles()
        save_prompt_styles(data, db_path)
        return data

    if not isinstance(data, dict) or not isinstance(data.get("styles"), list):
        data = seed_prompt_styles()
        save_prompt_styles(data, db_path)
        return data

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
        seeded = seed_prompt_styles()
        return seeded["styles"][0]

    weights: list[int] = []
    for style in styles:
        model_stats = style_model_stats(style, target_model)
        style_score = int(style.get("score", 0))
        model_score = int(model_stats.get("score", 0))
        model_uses = int(model_stats.get("uses", 0))
        effective_score = model_score if model_uses > 0 else style_score
        weights.append(max(1, effective_score + 2))

    return random.choices(styles, weights=weights, k=1)[0]


def record_style_usage(style: dict, target_model: str) -> None:
    style.setdefault("uses", 0)
    style["uses"] += 1
    stats = style_model_stats(style, target_model)
    stats["uses"] += 1
    targets = style.setdefault("target_models", [])
    if target_model not in targets:
        targets.append(target_model)


def record_style_outcome(style: dict, target_model: str, valid: bool) -> None:
    style.setdefault("score", 0)
    style.setdefault("successes", 0)
    stats = style_model_stats(style, target_model)

    if valid:
        style["successes"] += 1
        style["score"] += 1
        stats["successes"] += 1
        stats["score"] += 1
        return

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
        Your previous output was malformed for auto-apply.
        Correct these issues exactly:
        {reason_lines}

        Required valid output modes:
        1. No code changes needed:
           - plain prose only
           - no fenced code blocks

        2. Code changes needed:
           - file blocks only, no extra prose:
             ### relative/path/from/repo_root.ext
             ```language
             FULL FILE CONTENT ONLY
             ```

        Strictly forbidden:
        - diff format (*** Begin Patch, +/-, @@)
        - prose mixed with file blocks
        - backticks around heading paths
        - partial snippets
        """
    ).strip()


# -------------------------
# Ollama interface (messages list)
# -------------------------
def ask_model_ollama(model: str, messages: list[dict]) -> str:
    """
    Streaming call to Ollama using a messages list (system/user/assistant roles).
    messages should be a list of dicts like: {"role": "system", "content": "..."}
    """
    try:
        from ollama import chat
    except ImportError as exc:
        raise RuntimeError(
            "The 'ollama' Python package is not installed in this environment. "
            "Install with: py -m pip install ollama"
        ) from exc

    stream = chat(model=model, messages=messages, stream=True)

    in_thinking = False
    content = ""
    thinking = ""

    for chunk in stream:
        message = chunk.get("message") if isinstance(
            chunk, dict) else getattr(chunk, "message", None)
        if message is None:
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
            thinking += chunk_thinking
            continue

        if chunk_content:
            if in_thinking:
                in_thinking = False
                print("\n\nAnswer:\n", end="", flush=True)
            print(chunk_content, end="", flush=True)
            content += chunk_content

    if thinking or content:
        print("\n", flush=True)

    return content


# -------------------------
# Generated style helper (uses messages)
# -------------------------
def create_generated_style(model: str, target_model: str, malformed_reasons: list[str]) -> dict:
    reason_text = "\n".join(
        f"- {reason}" for reason in malformed_reasons) if malformed_reasons else "- No reason provided"
    generation_prompt = textwrap.dedent(
        f"""
        Generate a STRICT instruction block to force parser-compatible output for a coding assistant.
        Target model using this style: {target_model}

        Recent malformed reasons:
        {reason_text}

        Return JSON only:
        {{
          "name": "short style name",
          "instruction": "full instruction text"
        }}

        Instruction must enforce:
        - prose-only with no fences when no code changes
        - OR file-block-only output in this exact pattern when changes exist:
          ### relative/path/from/repo_root.ext
          ```language
          FULL FILE CONTENT ONLY
          ```
        - no backticks around path header
        - no diff/patch format
        - no explanatory prose around file blocks
        """
    ).strip()

    messages = [
        {"role": "system", "content": "You are a style generator that returns JSON only."},
        {"role": "user", "content": generation_prompt},
    ]
    raw = ask_model_ollama(model, messages)

    style_name = "AI Generated Strict Style"
    instruction = (
        "Return parser-compatible output only. "
        "No-code-change case: prose only and no fenced code blocks. "
        "Code-change case: ONLY repeated file blocks with heading path and full-file fenced content. "
        "No diff format, no backticked paths, no extra prose."
    )

    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            if isinstance(parsed.get("name"), str) and parsed["name"].strip():
                style_name = parsed["name"].strip()
            if isinstance(parsed.get("instruction"), str) and parsed["instruction"].strip():
                instruction = parsed["instruction"].strip()
    except Exception:
        pass

    return {
        "id": f"ai_style_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}",
        "name": style_name,
        "instruction": instruction,
        "score": 0,
        "uses": 0,
        "successes": 0,
        "generator_model": model,
        "target_models": [target_model],
        "model_stats": {target_model: {"uses": 0, "successes": 0, "score": 0}},
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }


# -------------------------
# Validation and path safety
# -------------------------
def is_valid_relative_path(path: str) -> bool:
    p = path.strip()
    if not p:
        return False
    if "`" in p:
        return False
    if p.startswith("/") or p.startswith("\\"):
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

    if "```" not in text:
        return OutputValidationResult(ok=True, has_file_blocks=False, reasons=[])

    lines = text.splitlines()
    reasons: list[str] = []
    i = 0
    has_blocks = False

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
                "Missing opening code fence after file path heading.")
            break

        open_fence = lines[i].strip()
        if not open_fence.startswith("```"):
            reasons.append(f"Expected opening code fence at line {i + 1}.")
            break

        lang = open_fence[3:].strip().lower()
        if lang == "diff":
            reasons.append(
                "Diff code fences are not allowed; full file content is required.")
            break

        i += 1
        code_lines: list[str] = []
        while i < len(lines) and lines[i].strip() != "```":
            code_lines.append(lines[i])
            i += 1

        if i >= len(lines):
            reasons.append("Unclosed code fence.")
            break

        code_text = "\n".join(code_lines).strip()
        if not code_text:
            reasons.append("File block contains empty code content.")
            break

        if "*** Begin Patch" in code_text or "*** Update File:" in code_text:
            reasons.append(
                "Patch/diff payload detected; full file content expected.")
            break

        has_blocks = True
        i += 1

    return OutputValidationResult(ok=len(reasons) == 0, has_file_blocks=has_blocks, reasons=reasons)


# -------------------------
# History / conversation helpers
# -------------------------
def intent_requires_edits(question: str) -> bool:
    q = (question or "").lower()
    return any(keyword in q for keyword in EDIT_INTENT_KEYWORDS)


def render_history(turns: list[ConversationTurn], max_turns: int) -> str:
    if not turns:
        return ""

    window = turns[-max(1, max_turns):]
    chunks: list[str] = []
    for idx, turn in enumerate(window, start=1):
        chunks.append(
            (
                f"Turn {idx}\n"
                f"User:\n{turn.question}\n\n"
                f"Assistant:\n{turn.answer}\n"
            )
        )
    return "\n".join(chunks)


def build_question_with_history(question: str, turns: list[ConversationTurn], max_turns: int) -> str:
    history_text = render_history(turns, max_turns)
    if not history_text:
        return question

    return (
        "Conversation History (oldest to newest):\n"
        f"{history_text}\n\n"
        "Current User Request:\n"
        f"{question}"
    )


def build_messages_for_model(
    instruction_style: str,
    question_payload: str,
    turns: list[ConversationTurn],
    context: str | None = None,
) -> list[dict]:
    """
    Build a messages list with system role first, then alternating user/assistant from history,
    and finally the current user request.
    """
    messages: list[dict] = []
    # system role first
    if context:
        messages.append(
            {"role": "system", "content": f"{instruction_style}\n\nRepository Context:\n{context}"})
    else:
        messages.append(
            {"role": "system", "content": instruction_style}
        )

    # history: each ConversationTurn contains question and answer
    for turn in turns:
        messages.append({"role": "user", "content": turn.question})
        messages.append({"role": "assistant", "content": turn.answer})

    # current user request
    messages.append({"role": "user", "content": question_payload})
    return messages


# -------------------------
# Route selection helper
# -------------------------
def ask_model_route(model: str, repo_context: str, question: str, styles_hint: str = "") -> dict:
    """
    Ask the model to choose a route for handling the request.
    Returns a dict like: {"route": "explain"|"apply"|"plan", "save_md": true|false, "reason": "..."}
    This is a short non-streaming call expecting JSON output.
    """
    route_prompt = textwrap.dedent(
        f"""
        You are an assistant that chooses a single handling route for the user's request.
        Return JSON only with keys:
          - route: one of "explain", "apply", "plan"
          - save_md: boolean (whether the apply step should also save a parsed .md)
          - reason: short explanation (one sentence)

        Context hint:
        {styles_hint}

        Question:
        {question}

        Repository context (trimmed):
        {repo_context[:4000]}

        Choose the route and return JSON only.
        """
    ).strip()

    messages = [
        {"role": "system", "content": "Decide a single route for handling a repo edit or explanation request. Return JSON only."},
        {"role": "user", "content": route_prompt},
    ]
    raw = ask_model_ollama(model, messages)
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        raw_l = (raw or "").strip().lower()
        if "apply" in raw_l:
            return {"route": "apply", "save_md": False, "reason": "Detected edit intent"}
        if "plan" in raw_l:
            return {"route": "plan", "save_md": False, "reason": "Detected planning intent"}
        return {"route": "explain", "save_md": False, "reason": "Defaulting to explain"}
    return {"route": "explain", "save_md": False, "reason": "Default"}


# -------------------------
# Core processing
# -------------------------
def process_turn(
    *,
    repo_path: Path,
    model: str,
    question: str,
    runs_dir: Path,
    max_context_chars: int,
    max_malformed_retries: int,
    auto_apply: bool,
    turns: list[ConversationTurn],
    history_window: int,
    hard_mode: bool,
    save_md_flag: bool = False,
) -> tuple[int, ConversationTurn]:
    temp_context_md = runs_dir / "_latest_context.md"
    temp_context_md.parent.mkdir(parents=True, exist_ok=True)

    print(f"[1/4] Exporting repo context from {repo_path} ...")
    run_export(repo_path, temp_context_md)

    context_text = temp_context_md.read_text(encoding="utf-8")
    # context_text = maybe_trim_context(context_text, max_context_chars)

    style_db = load_prompt_styles()
    styles = style_db.get("styles", [])
    max_attempts = max(1, min(5, int(max_malformed_retries)))
    attempt_records: list[dict] = []
    malformed_reasons: list[str] = []
    response = ""

    question_payload = build_question_with_history(
        question, turns, history_window)

    route_decision = ask_model_route(
        model=model, repo_context=context_text, question=question,
        styles_hint="Use importer-safe output when edits are required.")
    chosen_route = (route_decision.get("route") or "explain").lower()
    route_save_md = bool(route_decision.get("save_md", False))

    require_file_blocks = intent_requires_edits(
        question) or hard_mode or (chosen_route == "apply")
    save_md_flag = save_md_flag or route_save_md

    # ---------------------------------------------------------------------
    # CASE 1: Edits are required → enforce strict output with retries
    # ---------------------------------------------------------------------
    if require_file_blocks:
        for attempt in range(1, max_attempts + 1):
            if attempt <= 2:
                style = weighted_random_style(styles, model)
            else:
                style = create_generated_style(
                    model=model, target_model=model, malformed_reasons=malformed_reasons)
                styles.append(style)
                save_prompt_styles(style_db)

            record_style_usage(style, model)
            model_stats_before = style_model_stats(style, model)
            score_before = int(style.get("score", 0))
            model_score_before = int(model_stats_before.get("score", 0))

            instruction_style = style.get("instruction", "")
            if require_file_blocks:
                explicit_example = textwrap.dedent(
                    """
                    IMPORTANT: The user requested edits. Output MUST be file blocks only.
                    Return one or more file blocks in this exact pattern and nothing else:

                    ### README.md
                    ```md
                    <FULL final README.md content here>
                    ```

                    Do not include any prose outside file blocks. Do not output diffs, explanations, or patch hunks.
                    """
                ).strip()
                instruction_style = f"{instruction_style}\n\n{explicit_example}"

            if attempt > 1 and malformed_reasons:
                instruction_style = f"{instruction_style}\n\n{build_retry_guidance(malformed_reasons)}"

            messages = build_messages_for_model(
                instruction_style=instruction_style, question_payload=question_payload, turns=turns, context=context_text)

            print(
                f"[2/4] Asking model: {model} (attempt {attempt}/{max_attempts}, style={style.get('id', 'unknown')}) ...")
            response = ask_model_ollama(model, messages)
            validation = validate_output_for_importer(response)

            if validation.ok and require_file_blocks and not validation.has_file_blocks:
                validation = OutputValidationResult(
                    ok=False,
                    has_file_blocks=False,
                    reasons=[
                        "Hard mode / edit intent requires file blocks for edit requests; prose-only output was rejected.",
                    ],
                )

            record_style_outcome(style, model, validation.ok)
            save_prompt_styles(style_db)

            model_stats_after = style_model_stats(style, model)
            score_after = int(style.get("score", 0))
            model_score_after = int(model_stats_after.get("score", 0))

            usage_entry = {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "attempt": attempt,
                "style_id": style.get("id"),
                "style_name": style.get("name"),
                "style_generator_model": style.get("generator_model", "seed"),
                "target_model": model,
                "style_score_before": score_before,
                "style_score_after": score_after,
                "model_score_before": model_score_before,
                "model_score_after": model_score_after,
                "hard_mode": hard_mode,
                "require_file_blocks": require_file_blocks,
                "route": chosen_route,
                "valid": validation.ok,
                "has_file_blocks": validation.has_file_blocks,
                "reasons": validation.reasons,
            }
            append_prompt_style_usage(usage_entry)
            attempt_records.append(usage_entry)

            if validation.ok:
                break

            malformed_reasons = validation.reasons or ["Malformed output"]
            print(
                "Output malformed for importer format. Retrying with a new prompt style...")
            for reason in malformed_reasons:
                print(f"  - {reason}")

        final_validation = validate_output_for_importer(response)
        if final_validation.ok and require_file_blocks and not final_validation.has_file_blocks:
            final_validation = OutputValidationResult(
                ok=False,
                has_file_blocks=False,
                reasons=[
                    "Hard mode / edit intent requires file blocks for edit-intent requests; prose-only output was rejected.",
                ],
            )

    # ---------------------------------------------------------------------
    # CASE 2: No edits required (explain/plan) → single call, lenient validation
    # ---------------------------------------------------------------------
    else:
        style = weighted_random_style(styles, model)
        record_style_usage(style, model)
        instruction_style = style.get("instruction", "")
        messages = build_messages_for_model(
            instruction_style=instruction_style, question_payload=question_payload,
            turns=turns, context=context_text)
        print(
            f"[2/4] Asking model: {model} (explain/plan mode, style={style.get('id', 'unknown')}) ...")
        response = ask_model_ollama(model, messages)

        validation = validate_output_for_importer(response)

        usage_entry = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "attempt": 1,
            "style_id": style.get("id"),
            "style_name": style.get("name"),
            "style_generator_model": style.get("generator_model", "seed"),
            "target_model": model,
            "style_score_before": 0,
            "style_score_after": 0,
            "model_score_before": 0,
            "model_score_after": 0,
            "hard_mode": hard_mode,
            "require_file_blocks": require_file_blocks,
            "route": chosen_route,
            "valid": True,
            "has_file_blocks": validation.has_file_blocks,
            "reasons": [],
        }
        append_prompt_style_usage(usage_entry)
        attempt_records.append(usage_entry)

        final_validation = OutputValidationResult(
            ok=True,
            has_file_blocks=validation.has_file_blocks,
            reasons=[],
        )

    # ---------------------------------------------------------------------
    # Common post‑processing: save artifacts, apply if requested
    # ---------------------------------------------------------------------
    if not final_validation.ok:
        print("Failed to produce parser-compatible output after retries.")
        for reason in final_validation.reasons:
            print(f"  - {reason}")

    print("[3/4] Saving run artifacts ...")
    run_ctx = save_run(
        runs_dir=runs_dir,
        repo_path=repo_path,
        model=model,
        question=question,
        context_text=context_text,
        response_text=response,
    )

    (run_ctx.run_dir / "prompt_attempts.json").write_text(
        json.dumps(attempt_records, indent=2), encoding="utf-8")

    print(f"Saved response to: {run_ctx.response_md}")
    print(f"Saved metadata to: {run_ctx.meta_json}")

    should_apply = auto_apply
    if not auto_apply:
        answer = input(
            "Apply model file edits to repo now? [y/N]: ").strip().lower()
        should_apply = answer in {"y", "yes"}

    applied = False
    if should_apply:
        if not final_validation.ok:
            print("[4/4] Apply blocked: output is malformed for importer format.")
            for reason in final_validation.reasons:
                print(f"  - {reason}")
            return 2, ConversationTurn(question=question, answer=response, run_dir=str(run_ctx.run_dir), applied=False)

        if not final_validation.has_file_blocks:
            print("[4/4] Apply blocked: no file blocks were found in model output.")
            return 2, ConversationTurn(question=question, answer=response, run_dir=str(run_ctx.run_dir), applied=False)

        print("[4/4] Applying edits from response markdown ...")
        completed = apply_changes(run_ctx.response_md, repo_path, save_md_flag)
        if completed.stdout:
            print(completed.stdout)
        if completed.stderr:
            print(completed.stderr)
        if completed.returncode != 0:
            print("Apply step failed.")
            return completed.returncode, ConversationTurn(question=question, answer=response, run_dir=str(run_ctx.run_dir), applied=False)
        print("Apply step completed.")
        applied = True
    else:
        print("[4/4] Apply step skipped.")

    print("Done.")
    return 0, ConversationTurn(question=question, answer=response, run_dir=str(run_ctx.run_dir), applied=applied)


def apply_changes(response_md: Path, repo_path: Path, save_md: bool = False) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, str(APPLY_SCRIPT), str(
        response_md), str(repo_path)]
    if save_md:
        command.append("--save-md")
    return subprocess.run(command, text=True, capture_output=True, check=False, shell=False)


def save_run(
    runs_dir: Path,
    repo_path: Path,
    model: str,
    question: str,
    context_text: str,
    response_text: str,
) -> RunContext:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = runs_dir / f"run_{timestamp}"
    run_dir.mkdir(parents=True, exist_ok=True)

    context_md = run_dir / "repo_context.md"
    response_md = run_dir / "response.md"
    meta_json = run_dir / "meta.json"

    context_md.write_text(context_text, encoding="utf-8")
    response_md.write_text(response_text, encoding="utf-8")
    meta_json.write_text(
        json.dumps(
            {
                "repo": str(repo_path),
                "model": model,
                "question": question,
                "created_at": datetime.now().isoformat(timespec="seconds"),
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    return RunContext(
        run_dir=run_dir,
        repo_path=repo_path,
        model=model,
        question=question,
        context_md=context_md,
        response_md=response_md,
        meta_json=meta_json,
    )


def maybe_trim_context(text: str, max_chars: int) -> str:
    if not max_chars or len(text) <= max_chars:
        return text
    # Trim preserving start and end for context variety
    head = text[: max_chars // 2]
    tail = text[-(max_chars // 2):]
    return head + "\n\n...TRIMMED...\n\n" + tail


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
    args = parser.parse_args(argv)

    repo_path = Path(args.repo)
    runs_dir = Path(args.runs_dir)
    runs_dir.mkdir(parents=True, exist_ok=True)

    model = choose_model(args.model)
    print("Chat loop ready.")
    print("Commands: /help, /clear, /restart, /history, /newrepo <path>, /exit")

    turns: list[ConversationTurn] = []
    while True:
        try:
            q = input("Q: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

        if not q:
            continue
        if q.startswith("/exit"):
            return 0
        if q.startswith("/clear"):
            turns = []
            print("Conversation history cleared.")
            continue
        if q.startswith("/restart"):
            turns = []
            print("Conversation history restarted and context will refresh on next turn.")
            continue

        # process single turn
        exit_code, turn = process_turn(
            repo_path=repo_path,
            model=model,
            question=q,
            runs_dir=runs_dir,
            max_context_chars=args.max_context_chars,
            max_malformed_retries=args.max_malformed_retries,
            auto_apply=args.auto_apply,
            turns=turns,
            history_window=DEFAULT_HISTORY_WINDOW,
            hard_mode=args.hard_mode,
            save_md_flag=args.save_md,
        )
        turns.append(turn)


if __name__ == "__main__":
    raise SystemExit(main())
