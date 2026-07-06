from __future__ import annotations

import argparse
import json
import subprocess
import sys
import textwrap
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


EXPORT_SCRIPT = Path("py_scripts/out/02_app_to_md.py")
APPLY_SCRIPT = Path("py_scripts/in/02_md_to_py.py")


@dataclass
class RunContext:
    run_dir: Path
    repo_path: Path
    model: str
    question: str
    context_md: Path
    response_md: Path
    meta_json: Path


def list_repo_choices(execute_lab_dir: Path) -> list[Path]:
    if not execute_lab_dir.exists():
        return []
    return sorted([p for p in execute_lab_dir.iterdir() if p.is_dir()])


def choose_repo(execute_lab_dir: Path, repo_arg: str | None) -> Path:
    if repo_arg:
        repo = Path(repo_arg)
        if not repo.exists() or not repo.is_dir():
            raise FileNotFoundError(f"Repo path does not exist or is not a directory: {repo}")
        return repo

    options = list_repo_choices(execute_lab_dir)
    if not options:
        raise FileNotFoundError(f"No repo folders found under {execute_lab_dir}")

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
    command = [sys.executable, str(EXPORT_SCRIPT), str(repo_path), str(out_md)]
    completed = subprocess.run(
        command,
        text=True,
        capture_output=True,
        check=False,
        shell=False,
    )

    if completed.returncode != 0:
        raise RuntimeError(
            "Failed to build repo markdown context\n"
            f"Command: {' '.join(command)}\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )


def build_prompt(question: str, repo_context: str) -> str:
    instructions = textwrap.dedent(
        """
        You are reviewing a code repository from markdown context.

        Requirements:
        1. Answer the question clearly.
        2. If code changes are needed, include full updated file contents in markdown blocks.
        3. Use this exact edit format for each changed file so it can be applied automatically:
           ### `relative/path/from/repo_root.ext`
           ```language
           FULL FILE CONTENT HERE
           ```
        4. Only include files that must change.
        5. Keep paths relative to repo root.
        """
    ).strip()

    return (
        f"{instructions}\n\n"
        f"Question:\n{question}\n\n"
        f"Repository Context:\n\n{repo_context}"
    )


def ask_model_ollama(model: str, prompt: str) -> str:
    try:
        from ollama import chat
    except ImportError as exc:
        raise RuntimeError(
            "The 'ollama' Python package is not installed in this environment. "
            "Install with: py -m pip install ollama"
        ) from exc

    response = chat(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        stream=False,
    )
    return (response.get("message") or {}).get("content", "")


def apply_changes(response_md: Path, repo_path: Path) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, str(APPLY_SCRIPT), str(response_md), str(repo_path)]
    return subprocess.run(
        command,
        text=True,
        capture_output=True,
        check=False,
        shell=False,
    )


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
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    tail = "\n\n[Context trimmed due to --max-context-chars limit.]\n"
    return text[: max(0, max_chars - len(tail))] + tail


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Repo Q&A mixer: export repo to markdown, ask a model, save output, and optionally apply edits."
        )
    )
    parser.add_argument("--execute-lab-dir", default="execute_lab", help="Base folder containing repos")
    parser.add_argument("--repo", help="Repo path to focus on (default: choose interactively)")
    parser.add_argument("--model", help="Model to use (default: choose interactively)")
    parser.add_argument("--question", help="Question to ask (default: prompt)")
    parser.add_argument("--runs-dir", default="qa_runs", help="Folder for run outputs")
    parser.add_argument(
        "--max-context-chars",
        type=int,
        default=300000,
        help="Trim exported repo markdown to this many characters before sending to model",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Automatically apply model-proposed file blocks into the target repo",
    )
    args = parser.parse_args()

    execute_lab_dir = Path(args.execute_lab_dir)
    repo_path = choose_repo(execute_lab_dir, args.repo)
    model = choose_model(args.model)
    question = get_question(args.question)

    runs_dir = Path(args.runs_dir)
    temp_context_md = runs_dir / "_latest_context.md"
    temp_context_md.parent.mkdir(parents=True, exist_ok=True)

    print(f"[1/4] Exporting repo context from {repo_path} ...")
    run_export(repo_path, temp_context_md)

    context_text = temp_context_md.read_text(encoding="utf-8")
    context_text = maybe_trim_context(context_text, args.max_context_chars)

    prompt = build_prompt(question, context_text)

    print(f"[2/4] Asking model: {model} ...")
    response = ask_model_ollama(model, prompt)

    print("[3/4] Saving run artifacts ...")
    run_ctx = save_run(
        runs_dir=runs_dir,
        repo_path=repo_path,
        model=model,
        question=question,
        context_text=context_text,
        response_text=response,
    )

    print(f"Saved response to: {run_ctx.response_md}")
    print(f"Saved metadata to: {run_ctx.meta_json}")

    should_apply = args.apply
    if not args.apply:
        answer = input("Apply model file edits to repo now? [y/N]: ").strip().lower()
        should_apply = answer in {"y", "yes"}

    if should_apply:
        print("[4/4] Applying edits from response markdown ...")
        completed = apply_changes(run_ctx.response_md, repo_path)
        if completed.stdout:
            print(completed.stdout)
        if completed.stderr:
            print(completed.stderr)
        if completed.returncode != 0:
            print("Apply step failed.")
            return completed.returncode
        print("Apply step completed.")
    else:
        print("[4/4] Apply step skipped.")

    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
