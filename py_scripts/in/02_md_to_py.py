"""
Extract fenced code blocks from a Markdown file into actual project files.

Path discovery (in order of precedence):
  1. If the opening fence has a file path, e.g. ```typescript src/foo/bar.ts
  2. If the immediately preceding heading contains a file path in backticks,
     e.g. ### `src/foo/bar.ts`
  3. Otherwise, fall back to a sequential numbered filename.
"""

import sys
import os
import re

def extract(md_path, target_dir):
    with open(md_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    if not os.path.exists(target_dir):
        os.makedirs(target_dir, exist_ok=True)

    # Regex to match headings that may contain a file path.
    # Supports both:
    #   ### `src/foo.py`
    #   ### src/foo.py
    heading_path_backtick_re = re.compile(r'^#+\s+`([^`]+)`\s*$')
    heading_path_plain_re = re.compile(r'^#+\s+([^`\n]+?)\s*$')
    # For the fence: capture optional lang, optional extra info, then code.
    # We'll detect the start and end of a fence manually.
    
    current_file_path = None   # from the most recent heading
    in_fence = False
    fence_lang = ''
    fence_info = ''
    fence_content = []
    count = 1

    for line in lines:
        # Check for a heading that contains a file path
        if not in_fence:
            m = heading_path_backtick_re.match(line)
            if m:
                current_file_path = m.group(1).strip()
                continue   # heading line, not part of code

            m = heading_path_plain_re.match(line)
            if m:
                candidate = m.group(1).strip()
                # Avoid treating generic prose headings as paths.
                if '/' in candidate or '\\' in candidate or '.' in os.path.basename(candidate):
                    current_file_path = candidate
                    continue

        # Detect fence start/end
        stripped = line.rstrip()
        fence_match = re.match(r'^```(\S*)(.*)', stripped)  # match opening or closing fence
        if fence_match:
            if not in_fence:
                # Opening fence
                in_fence = True
                fence_lang = fence_match.group(1) or ''
                fence_info = fence_match.group(2).strip()   # e.g. " src/..."
                fence_content = []
            else:
                # Closing fence – process the collected code
                in_fence = False
                code = ''.join(fence_content)

                # Determine target relative path
                relpath = None

                # 1. From the fence info string itself
                if fence_info:
                    relpath = fence_info

                # 2. From the preceding heading (if any)
                if not relpath and current_file_path:
                    relpath = current_file_path
                    current_file_path = None  # consume it

                # 3. Fallback sequential name
                if not relpath:
                    ext_map = {'js': 'js', 'ts': 'ts', 'tsx': 'tsx', 'py': 'py', 'css': 'css'}
                    ext = ext_map.get(fence_lang, 'txt')
                    relpath = f'extracted_{count}.{ext}'
                    count += 1

                outpath = os.path.join(target_dir, relpath)
                outdir = os.path.dirname(outpath)
                if outdir and not os.path.exists(outdir):
                    os.makedirs(outdir, exist_ok=True)

                with open(outpath, 'w', encoding='utf-8') as out:
                    out.write(code.lstrip('\n'))
                print('Wrote', outpath)
        elif in_fence:
            fence_content.append(line)

    # In case the Markdown ended without a closing fence (shouldn't happen, but just in case)
    if in_fence:
        print('Warning: unclosed code fence at end of file – skipping.')

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: 02_md_to_py.py input.md [target_dir]')
        sys.exit(1)
    md = sys.argv[1]
    target = sys.argv[2] if len(sys.argv) > 2 else os.path.join('..', '..', 'mix')
    extract(md, target)