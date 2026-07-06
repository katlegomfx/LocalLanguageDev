# LocalLanguageDev (LLD)

LocalLanguageDev is a small local toolkit for repository Q&A and code-application workflows.

It includes:
- `02_app_to_md.py` (repo -> markdown context export)
- `py_scripts/in/02_md_to_py.py` (markdown code blocks -> files)
- `mainMixer.py` (interactive/model-driven orchestration)

## Use Cases
- Ask questions about a local project repo
- Generate proposed file updates from a model response
- Save model runs and apply proposed changes to a target repo

## Quick Start

1. Install dependencies:

```bash
py -m pip install -r requirements.txt
```

2. Run the mixer:

```bash
py mainMixer.py
```

3. Or run non-interactively:

```bash
py mainMixer.py --repo ../some_project --model gpt-oss:20b --question "Refactor logging setup" --apply
```

## Notes
- `mainMixer.py` expects export/apply scripts at:
  - `py_scripts/out/02_app_to_md.py`
  - `py_scripts/in/02_md_to_py.py`
- Run outputs are saved under `qa_runs/`.
