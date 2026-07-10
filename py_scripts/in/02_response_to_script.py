# py_scripts/in/02_response_to_script.py
import argparse
import os
import re
from pathlib import Path
from typing import List, Tuple

FENCE_HEADER_RE = re.compile(r"^###\s+(.+)$")
OPEN_FENCE_RE = re.compile(r"^```(.*)$")

LANG_TO_EXT = {
    "py": ".py",
    "python": ".py",
    "md": ".md",
    "markdown": ".md",
    "js": ".js",
    "javascript": ".js",
    "ts": ".ts",
    "typescript": ".ts",
    "json": ".json",
    "yml": ".yml",
    "yaml": ".yml",
    "txt": ".txt",
    "sh": ".sh",
    "bash": ".sh",
    "zsh": ".sh",
    "go": ".go",
    "java": ".java",
    "html": ".html",
    "css": ".css",
    "rs": ".rs",
    "rust": ".rs",
    "c": ".c",
    "cpp": ".cpp",
    "h": ".h",
    "hpp": ".hpp",
    "rb": ".rb",
    "php": ".php",
    # extend as needed
}


def is_safe_relative_path(rel: str) -> bool:
    p = rel.strip()
    if not p:
        return False
    if p.startswith("/") or p.startswith("\\"):
        return False
    if ".." in p.replace("\\", "/").split("/"):
        return False
    if "`" in p:
        return False
    if re.match(r"^[a-zA-Z]:", p):
        return False
    return True


def parse_file_blocks(md_text: str) -> List[Tuple[str, str, str]]:
    """
    Returns list of tuples: (rel_path, fence_lang, content)
    """
    lines = md_text.splitlines()
    i = 0
    blocks = []
    while i < len(lines):
        # skip blank lines
        while i < len(lines) and not lines[i].strip():
            i += 1
        if i >= len(lines):
            break

        header_m = FENCE_HEADER_RE.match(lines[i].strip())
        if not header_m:
            # skip unexpected lines until next header
            i += 1
            continue

        rel_path = header_m.group(1).strip()
        i += 1
        # skip blank lines
        while i < len(lines) and not lines[i].strip():
            i += 1
        if i >= len(lines):
            break

        open_fence_m = OPEN_FENCE_RE.match(lines[i].strip())
        if not open_fence_m:
            # missing opening fence; skip this header
            i += 1
            continue

        fence_lang = open_fence_m.group(1).strip().lower()
        i += 1
        content_lines = []
        while i < len(lines) and lines[i].strip() != "```":
            content_lines.append(lines[i])
            i += 1

        if i >= len(lines):
            # unclosed fence: accept what we have
            content = "\n".join(content_lines)
            blocks.append((rel_path, fence_lang, content))
            break

        # skip closing fence
        i += 1
        content = "\n".join(content_lines)
        blocks.append((rel_path, fence_lang, content))

    return blocks


def lang_to_extension(lang: str, rel_path: str) -> str:
    if not lang:
        ext = Path(rel_path).suffix
        return ext if ext else ".txt"
    lang = lang.lower().strip()
    if lang in LANG_TO_EXT:
        return LANG_TO_EXT[lang]
    # if lang looks like an extension (e.g., "py"), try that
    if lang.startswith("."):
        return lang
    # fallback to rel_path extension or .txt
    ext = Path(rel_path).suffix
    return ext if ext else ".txt"


def write_blocks_to_repo(blocks: List[Tuple[str, str, str]], repo_root: Path) -> None:
    for rel_path, fence_lang, content in blocks:
        if not is_safe_relative_path(rel_path):
            print(f"Skipping unsafe path: {rel_path}")
            continue
        target = repo_root / rel_path
        # if rel_path has no extension, infer from fence_lang
        if not target.suffix:
            ext = lang_to_extension(fence_lang, rel_path)
            target = target.with_suffix(ext)
        target.parent.mkdir(parents=True, exist_ok=True)
        # write content exactly as provided (strip leading blank lines)
        target.write_text(content.lstrip("\n"), encoding="utf-8")
        print(f"Wrote {target}")


def save_parsed_md(blocks: List[Tuple[str, str, str]], out_path: Path) -> None:
    with out_path.open("w", encoding="utf-8") as f:
        f.write(f"# Parsed file blocks from {out_path.name}\n\n")
        for rel_path, fence_lang, content in blocks:
            lang = fence_lang or ""
            f.write(f"### {rel_path}\n")
            f.write(f"```{lang}\n")
            f.write(content.rstrip() + "\n")
            f.write("```\n\n")
    print(f"Saved parsed blocks audit to {out_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Apply file-blocks from response.md into repo")
    parser.add_argument(
        "response_md", help="Path to response markdown containing file blocks")
    parser.add_argument(
        "repo_path", help="Target repository root to write files into")
    parser.add_argument("--save-md", action="store_true",
                        help="Also save parsed file blocks to response_md.parsed.md")
    args = parser.parse_args()

    response_md = Path(args.response_md)
    repo_root = Path(args.repo_path)

    if not response_md.exists():
        print(f"Response markdown not found: {response_md}")
        raise SystemExit(2)
    if not repo_root.exists() or not repo_root.is_dir():
        print(f"Repo path not found or not a directory: {repo_root}")
        raise SystemExit(3)

    md_text = response_md.read_text(encoding="utf-8")
    blocks = parse_file_blocks(md_text)
    if not blocks:
        print("No file blocks found in response markdown.")
        raise SystemExit(4)

    write_blocks_to_repo(blocks, repo_root)

    if args.save_md:
        parsed_md_path = response_md.with_suffix(
            response_md.suffix + ".parsed.md")
        save_parsed_md(blocks, parsed_md_path)


if __name__ == "__main__":
    main()
