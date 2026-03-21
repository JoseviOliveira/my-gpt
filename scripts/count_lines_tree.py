"""
count_lines_tree.py — Display source code line counts in tree format
"""

import os

def count_lines(filepath):
    """Count non-empty lines in a source file."""
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            return sum(1 for line in f if line.strip())
    except Exception:
        return 0


def print_tree(startpath):
    """Print directory tree with line counts for source files."""
    # Skip only environment/vendor/cache folders.
    exclude_top_level_dirs = {
        'chat_env', 'tts_env', 'tts_cache', '.git', '.vscode', '.venv'
    }
    exclude_dirs_anywhere = {'__pycache__', 'node_modules'}
    include_exts = {'.css', '.js', '.py', '.html', '.sh', '.sql'}
    exclude_patterns = {'.min.js', '.min.css'}  # Skip minified files
    
    total_lines = 0
    file_count = 0

    for root, dirs, files in os.walk(startpath):
        # Exclude hidden dirs, selected top-level dirs, and vendor/cache dirs anywhere.
        rel_root = os.path.relpath(root, startpath)
        rel_parts = [] if rel_root == '.' else rel_root.split(os.sep)
        dirs[:] = [
            d for d in dirs
            if not d.startswith('.')
            and d not in exclude_dirs_anywhere
            and not (not rel_parts and d in exclude_top_level_dirs)
        ]
        
        level = root.replace(startpath, '').count(os.sep)
        indent = ' ' * 4 * level
        print(f'{indent}{os.path.basename(root)}/')
        
        subindent = ' ' * 4 * (level + 1)
        for f in sorted(files):
            # Skip hidden files and minified files
            if f.startswith('.'):
                continue
            if any(f.endswith(pattern) for pattern in exclude_patterns):
                continue
            
            _, ext = os.path.splitext(f)
            if ext not in include_exts:
                continue
            
            filepath = os.path.join(root, f)
            lines = count_lines(filepath)
            total_lines += lines
            file_count += 1
            print(f'{subindent}{f} ({lines} lines)')
    
    return total_lines, file_count


if __name__ == "__main__":
    total, count = print_tree('.')
    print(f'\nTotal: {total:,} lines across {count} files')
