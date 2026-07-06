r"""Take the app code and data and converts it into markdown format for analysis purposes.

Current Usage:
```PS
py .\py_scripts\out\02_app_to_md.py .\execute_lab\flexi_lab\ .\suggest.md
```

"""
import os
import pathspec

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

                # Write a section header for each file
                md.write(f"## {rel}\n\n")
                # Guess language for code block from extension
                ext = os.path.splitext(file)[1].lstrip('.')
                lang = ext if ext else ''
                md.write(f"```{lang}\n")
                try:
                    if lang == 'md':
                        with open(full, 'r', encoding='utf-8') as f:
                            for line in f.readlines():
                                if line.startswith('#'):
                                    md.write(f'##{line}')
                                else:
                                    md.write(f'{line}')
                            md.write("\n```\n\n")
                    else:
                        with open(full, 'r', encoding='utf-8') as f:
                            md.write(f.read())
                except Exception as e:
                    md.write(f"<!-- Could not read file: {e} -->")
                md.write("\n```\n\n")

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Convert app code to Markdown respecting .gitignore')
    parser.add_argument('src', help='Source directory containing app code')
    parser.add_argument('out', help='Output Markdown file')
    args = parser.parse_args()

    code_to_markdown(args.src, args.out)
    print(f'Documentation written to {args.out}')
