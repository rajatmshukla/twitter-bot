#!/usr/bin/env python3
"""Verification tool: ensures only comments and docstrings were changed.

Compares working tree Python files against their version at git HEAD by:
1. Fetching the HEAD version via `git show HEAD:<path>`.
2. Parsing both versions into Python ASTs.
3. Stripping docstrings from modules, classes, and functions in both ASTs.
4. Comparing the AST dumps with annotate_fields=True, include_attributes=False.

Any difference in executable AST nodes results in a FAIL.
"""
import ast
import os
import subprocess
import sys


def strip_docstrings(node):
    """Recursively strip docstrings from Module, Function, and Class nodes."""
    if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        if node.body and isinstance(node.body[0], ast.Expr):
            val = node.body[0].value
            if isinstance(val, ast.Constant) and isinstance(val.value, str):
                node.body = node.body[1:]
    for child in ast.iter_child_nodes(node):
        strip_docstrings(child)
    return node


def normalize_repo_path(path, repo_root):
    """Normalize a path to be relative to the repo root with forward slashes."""
    abs_path = os.path.abspath(path)
    rel = os.path.relpath(abs_path, repo_root)
    return rel.replace("\\", "/")


def get_tracked_python_files(repo_root):
    """Get all tracked .py files at HEAD."""
    out = subprocess.check_output(
        ["git", "ls-tree", "-r", "--name-only", "HEAD"],
        cwd=repo_root,
        text=True,
    )
    return [line.strip() for line in out.splitlines() if line.strip().endswith(".py")]


def verify_file(rel_path, repo_root):
    """Verify a single file against HEAD. Returns (passed: bool, message: str)."""
    # 1. Obtain HEAD content
    try:
        head_proc = subprocess.run(
            ["git", "show", f"HEAD:{rel_path}"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
        head_content = head_proc.stdout
    except subprocess.CalledProcessError as e:
        return False, f"Could not retrieve HEAD:{rel_path}: {e.stderr.strip()}"

    # 2. Obtain working-tree content
    fs_path = os.path.join(repo_root, rel_path.replace("/", os.sep))
    if not os.path.exists(fs_path):
        return False, f"File missing in working tree: {fs_path}"

    try:
        with open(fs_path, "r", encoding="utf-8") as f:
            work_content = f.read()
    except Exception as e:
        return False, f"Error reading {fs_path}: {e}"

    # 3. Parse ASTs
    try:
        head_ast = ast.parse(head_content, filename=f"HEAD:{rel_path}")
    except SyntaxError as e:
        return False, f"SyntaxError parsing HEAD:{rel_path}: {e}"

    try:
        work_ast = ast.parse(work_content, filename=rel_path)
    except SyntaxError as e:
        return False, f"SyntaxError parsing working tree {rel_path}: {e}"

    # 4. Strip docstrings
    strip_docstrings(head_ast)
    strip_docstrings(work_ast)

    # 5. Dump and compare
    head_dump = ast.dump(head_ast, annotate_fields=True, include_attributes=False)
    work_dump = ast.dump(work_ast, annotate_fields=True, include_attributes=False)

    if head_dump == work_dump:
        return True, "PASS"
    else:
        return False, f"FAIL: {rel_path}"


def main():
    """Verify that Python files differ from git HEAD only by comments and docstrings.

    Accepts file paths via CLI arguments or defaults to all tracked Python files.
    Prints AST verification results per file along with a summary count to stdout.
    """
    repo_root = os.path.abspath(
        subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            text=True,
        ).strip()
    )

    args = sys.argv[1:]
    if args:
        targets = [normalize_repo_path(p, repo_root) for p in args]
    else:
        targets = get_tracked_python_files(repo_root)

    fails = []
    passes = 0

    for rel_path in targets:
        passed, msg = verify_file(rel_path, repo_root)
        if passed:
            print(f"PASS: {rel_path}")
            passes += 1
        else:
            print(msg)
            fails.append(rel_path)

    total = len(targets)
    print(f"\nSummary: {passes}/{total} files passed.")
    if fails:
        print(f"{len(fails)} file(s) failed comment-only verification.")
        sys.exit(1)
    else:
        print("All verified files match HEAD AST (excluding docstrings).")
        sys.exit(0)


if __name__ == "__main__":
    main()
