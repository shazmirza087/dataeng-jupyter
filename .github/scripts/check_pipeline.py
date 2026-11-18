"""Checks that run on every pull request and push to dev, uat and main.

They need no data and no Spark:
1. The environment: WAHAT_ENV from the GitHub environment matches the target branch,
   and the configuration notebook maps that branch to the same environment.
2. Notebooks are valid and saved without outputs.
3. No machine-specific paths in the code.
4. Every script and loader notebook named in the control table exists.
"""
import ast
import json
import os
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
PIPELINE = ROOT / "pipeline"
CONFIG = PIPELINE / "00_shared" / "000_nb_configurations.ipynb"
CONTROL_SQL = PIPELINE / "05_helper_scripts" / "051_populate_control_tables.sql"

errors = []


def fail(message):
    errors.append(message)
    print(f"::error::{message}")


def notebook_code(path):
    notebook = json.loads(path.read_text(encoding="utf-8"))
    return notebook, "\n".join(
        "".join(cell["source"]) for cell in notebook["cells"] if cell["cell_type"] == "code"
    )


# 1. Environment ---------------------------------------------------------
target_branch = os.environ.get("TARGET_BRANCH", "")
wahat_env = os.environ.get("WAHAT_ENV", "")
_, config_code = notebook_code(CONFIG)
mapping = None
for node in ast.walk(ast.parse(config_code)):
    if isinstance(node, ast.Assign) and any(
        isinstance(t, ast.Name) and t.id == "BRANCH_ENVIRONMENTS" for t in node.targets
    ):
        mapping = ast.literal_eval(node.value)
if mapping is None:
    fail("BRANCH_ENVIRONMENTS not found in 000_nb_configurations.")
elif target_branch:
    expected = mapping.get(target_branch, "dev")
    if not wahat_env:
        fail(f"WAHAT_ENV is not set. Add it as a variable on the '{expected}' environment in the repo settings.")
    elif wahat_env != expected:
        fail(f"Branch {target_branch} maps to '{expected}', but the GitHub environment sets WAHAT_ENV to '{wahat_env}'.")
    else:
        print(f"Environment: {target_branch} -> {wahat_env}")

# 2. Notebooks ----------------------------------------------------------
notebooks = sorted(PIPELINE.rglob("*.ipynb"))
for path in notebooks:
    name = path.relative_to(ROOT)
    try:
        notebook, _ = notebook_code(path)
    except (ValueError, KeyError) as problem:
        fail(f"{name} is not a valid notebook: {problem}")
        continue
    for cell in notebook["cells"]:
        if cell["cell_type"] == "code" and (cell.get("outputs") or cell.get("execution_count")):
            fail(f"{name} was saved with outputs. Clear all outputs before committing.")
            break
print(f"Notebooks checked: {len(notebooks)}")

# 3. No machine-specific paths ------------------------------------------
path_pattern = re.compile(r"[A-Za-z]:\\\\[A-Za-z]|[A-Za-z]:\\[A-Za-z]|\\Users\\|/Users/|/home/")
code_files = notebooks + sorted(PIPELINE.rglob("*.sql")) + sorted(PIPELINE.rglob("*.py"))
for path in code_files:
    text = notebook_code(path)[1] if path.suffix == ".ipynb" else path.read_text(encoding="utf-8")
    for line in text.splitlines():
        if path_pattern.search(line):
            fail(f"{path.relative_to(ROOT)} has a machine-specific path: {line.strip()[:80]}")
print(f"Files checked for paths: {len(code_files)}")

# 4. Control table references -------------------------------------------
control = CONTROL_SQL.read_text(encoding="utf-8")
scripts = {p.name for p in PIPELINE.rglob("*.sql")}
loaders = {p.stem for p in PIPELINE.rglob("*.ipynb")}
named_scripts = sorted(set(re.findall(r"'([A-Za-z0-9_]+\.sql)'", control)))
named_loaders = sorted(set(re.findall(r"\('[A-Z_]+', '(?:BRONZE|SILVER|GOLD)', '([0-9A-Za-z_]+)'", control)))
for script in named_scripts:
    if script not in scripts:
        fail(f"The control table names {script}, but no such script exists.")
for loader in named_loaders:
    if loader not in loaders:
        fail(f"The loader registry names {loader}, but no such notebook exists.")
print(f"Control table: {len(named_scripts)} scripts and {len(named_loaders)} loaders found")

if errors:
    print(f"{len(errors)} problem(s) found.")
    sys.exit(1)
print("All checks passed.")
