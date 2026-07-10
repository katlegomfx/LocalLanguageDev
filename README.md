# LocalLanguageDev (LLD)

LocalLanguageDev is a small local toolkit for repository Q&A and code-application workflows.

It includes:
- `02_app_to_md.py` (repo -> markdown context export)
- `py_scripts/in/02_response_to_py.py` (markdown code blocks -> files)
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
  - `py_scripts/in/02_response_to_py.py`
- Run outputs are saved under `qa_runs/`.


## Future Improvements: Enhanced documentation and streamlined workflows will be prioritized to improve user experience and project efficiency. Continuous integration updates and automated testing enhancements are also planned for future releases.



## Example CLI
```bash
py  mainMixer.py --repo ../execute_lab/flexi_agent --model gpt-oss:20b --question "Give a short architecture summary and propose one safe refactor." --runs-dir qa_runs_lld_test

```

```bash
py  mainMixer.py --repo ../execute_lab/flexi_agent --model ornith --runs-dir qa_runs_ornith
```

```bash
py  mainMixer.py --repo ../LocalLanguageDev --model lfm2.5-thinking --runs-dir qa_runs_lfm --hard-mode
```


```bash
py  mainMixer.py --repo ../LocalLanguageDev --model ornith --runs-dir qa_runs_lfm
```
