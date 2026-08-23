#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Set, Tuple

# ============================================================
# 全自動設定
# ============================================================

DEFAULT_VENV_DIR = ".venv"
EXCLUDE_DIRS = {".venv", "venv", "__pycache__", ".git", ".idea", ".vscode", "dist", "build", "node_modules"}
BACKEND_HINT_DIRS = {"backend", "server", "api"}
UI_IMPORT_HINTS = {"pyside6", "pyqt6", "pyqt5", "tkinter", "wx", "kivy", "flet", "streamlit", "dearpygui"}

MODULE_ENTRY_CANDIDATES = [
    "src/main.py",
    "src/app.py",
    "backend/main.py",
    "backend/app.py",
    "backend/run_server.py",
    "backend/__main__.py",
]

RELATIVE_IMPORT_FIX_CANDIDATES = [
    "main.py",
    "app.py",
    "src/main.py",
    "src/app.py",
    "src/__main__.py",
    "backend/main.py",
    "backend/app.py",
    "backend/run_server.py",
    "backend/__main__.py",
    "start_backend.py",
    "backend/start_backend.py",
    "scripts/start_backend.py",
    "backend/scripts/start_backend.py",
]

KNOWN_PACKAGE_ROOTS = ("src", "backend", "app")

LOCAL_NAME_BLOCKLIST = {
    "app", "apps",
    "db", "database",
    "config", "configs", "settings",
    "utils", "common", "core",
    "src", "backend", "frontend",
    "main", "server", "run_server",
    "models", "schemas", "routers", "routes",
    "tests", "test",
}

IMPORT_TO_PIP_MAP = {
    "PIL": "pillow",
    "cv2": "opencv-python",
    "sklearn": "scikit-learn",
    "yaml": "pyyaml",
    "Crypto": "pycryptodome",
    "bs4": "beautifulsoup4",
    "dotenv": "python-dotenv",
    "pydantic_settings": "pydantic-settings",
}
PIP_EQUIVALENTS = {
    "pymupdf": {"pymupdf", "fitz"},
}

FRONTEND_PKG_CANDIDATES = [
    "frontend/package.json",
    "client/package.json",
    "web/package.json",
    "ui/package.json",
    "package.json",
]

BACKEND_NODE_PKG_CANDIDATES = [
    "services/api/package.json",
    "services/backend/package.json",
    "backend/package.json",
    "api/package.json",
    "server/package.json",
    "src/server/package.json",
    "src/api/package.json",
    "packages/api/package.json",
    "apps/api/package.json",
]

STATIC_SITE_DIR_CANDIDATES = [
    "dist",
    "build",
    "public",
    "frontend/dist",
    "frontend/build",
    "web/dist",
    "web/build",
    "client/dist",
    "client/build",
    "ui/dist",
    "ui/build",
]

START_BACKEND_SCRIPT_CANDIDATES = [
    "start_backend.py",
    "backend/start_backend.py",
    "scripts/start_backend.py",
    "backend/scripts/start_backend.py",
]

SERVE_LISTEN_RE = re.compile(r"""(?ix)\bnpx\s+serve\b.*?(?:\s-l\s+|\s--listen\s+)(?P<arg>\S+)""")
POSIX_DEFAULT_RE = re.compile(r"""\$\{([A-Za-z_][A-Za-z0-9_]*)[:-][^}]*?(\d{2,6})\}""")
REQ_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*")
NO_MATCH_RE = re.compile(r"No matching distribution found for ([A-Za-z0-9_.-]+)", re.IGNORECASE)
UVICORN_RUN_RE = re.compile(r"\buvicorn\.run\s*\(")

# ============================================================
# Utilities
# ============================================================

def is_windows() -> bool:
    return os.name == "nt"

def norm_rel(root: Path, p: Path) -> str:
    try:
        return str(p.relative_to(root))
    except Exception:
        return str(p)

def run_cmd(cmd: List[str], cwd: Optional[Path] = None) -> Tuple[int, str]:
    try:
        p = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            shell=False,
            check=False,
        )
        return p.returncode, p.stdout
    except FileNotFoundError as e:
        return 127, f"Command not found: {cmd[0]} ({e})"
    except Exception as e:
        return 1, f"Command failed: {' '.join(cmd)} ({e})"

def read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="ignore")

def read_text_quick(p: Path, max_bytes: int = 256 * 1024) -> str:
    try:
        data = p.read_bytes()
        if len(data) > max_bytes:
            data = data[:max_bytes]
        return data.decode("utf-8", errors="ignore")
    except Exception:
        return read_text(p)

def write_text_ascii(p: Path, text: str) -> None:
    # Keep .bat launchers ASCII-only; do not rely on ANSI/CP950 or default chcp 65001 behavior.
    p.write_text(text, encoding="ascii", errors="strict")

def write_text_utf8(p: Path, text: str) -> None:
    p.write_text(text, encoding="utf-8", errors="strict")

def write_text_utf8_lf(p: Path, text: str) -> None:
    # For .sh/.command. Use LF newlines; most tooling handles it well on Unix-like systems.
    p.write_text(text, encoding="utf-8", errors="strict", newline="\n")

def write_json_utf8(path: Path, payload: Dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text_utf8(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

def touch_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)

def safe_int(s: str) -> Optional[int]:
    try:
        v = int(s)
        return v if 1 <= v <= 65535 else None
    except Exception:
        return None

def script_relpath_from_root(root: Path) -> str:
    try:
        rel = Path(__file__).resolve().relative_to(root.resolve())
        return str(rel)
    except Exception:
        return Path(__file__).name

# ============================================================
# Optional config: .launcher.env (no extra CLI options)
# ============================================================

def parse_env_file(env_path: Path) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if not env_path.exists():
        return out
    for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        out[k.strip()] = v.strip()
    return out

def get_launcher_config(root: Path) -> Dict[str, str]:
    # precedence: OS env > .launcher.env
    file_cfg = parse_env_file(root / ".launcher.env")
    cfg = dict(file_cfg)
    for k, v in os.environ.items():
        if k in {
            "BACKEND_HOST", "BACKEND_PORT", "UVICORN_TARGET",
            "FRONTEND_HOST", "FRONTEND_PORT",
            "STATIC_HOST", "STATIC_PORT",
        }:
            cfg[k] = v
    return cfg


def load_project_config(root: Path) -> Dict[str, str]:
    config_path = root / "project.config.json"
    if not config_path.exists():
        return {}
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(key): str(value).strip() for key, value in raw.items()}


def infer_apsm_archetype(project_cfg: Dict[str, str]) -> str:
    architecture = project_cfg.get("architecture", "")
    frontend = project_cfg.get("frontend", "")
    backend = project_cfg.get("backend", "")
    if architecture == "separated":
        return "web_app"
    if architecture == "monorepo":
        return "monorepo"
    if architecture == "single_service" and frontend == "python_templates" and backend == "python_api":
        return "python_fullstack"
    if architecture == "single_service" and frontend == "node_ssr" and backend == "node_api":
        # Compatibility archetype for the current skill's B4 template.
        return "fullstack_app"
    if architecture == "single_service" and frontend == "none" and backend in {"python_api", "node_api"}:
        return "service_api"
    return ""


def seed_apsm_runtime(root: Path, det: "DetectionResult", launcher_cfg: Dict[str, str]) -> List[str]:
    runtime_dir = root / ".runtime"
    logs_dir = root / "logs"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    project_cfg = load_project_config(root)
    backend_expected = project_cfg.get("backend", "") not in {"", "none"}
    frontend_expected = project_cfg.get("frontend", "") in {"node_spa", "node_ssr"}

    backend_port = safe_int(launcher_cfg.get("BACKEND_PORT", "")) or det.backend.get("port")
    frontend_port = None
    if det.frontend.exists:
        frontend_port = det.frontend.port
    elif det.static_site.exists:
        frontend_port = det.static_site.port

    if backend_expected and not isinstance(backend_port, int):
        backend_port = 8000
    if frontend_expected and not isinstance(frontend_port, int):
        frontend_port = safe_int(launcher_cfg.get("FRONTEND_PORT", "")) or 5173

    ports_payload: Dict[str, object] = {}
    if isinstance(backend_port, int) and backend_port > 0:
        ports_payload["backend_port"] = backend_port
    if isinstance(frontend_port, int) and frontend_port > 0:
        ports_payload["frontend_port"] = frontend_port

    apsm_archetype = project_cfg.get("archetype") or infer_apsm_archetype(project_cfg)
    state_payload: Dict[str, object] = {
        "status": "launcher_generated",
        "last_event": "launcher_scripts_generated",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "generated_by": "scripts/project_launcher.py",
        "project_type": det.project_type,
        "backend_mode": det.backend.get("mode", "none"),
        "frontend_mode": (
            "node_frontend"
            if det.frontend.exists
            else "static_site"
            if det.static_site.exists
            else "none"
        ),
    }
    for key in ("apsm_version", "architecture", "frontend", "backend", "version"):
        value = project_cfg.get(key)
        if value:
            state_payload[key] = value
    if apsm_archetype:
        state_payload["archetype"] = apsm_archetype

    write_json_utf8(runtime_dir / "ports.json", ports_payload)
    write_json_utf8(runtime_dir / "launcher_state.json", state_payload)

    touch_file(logs_dir / "launcher.log")
    touch_file(logs_dir / "ensure.log")
    touch_file(logs_dir / "bootstrap.log")
    if backend_expected or det.backend.get("mode") != "none":
        touch_file(logs_dir / "backend.log")
    if frontend_expected or det.frontend.exists or det.static_site.exists:
        touch_file(logs_dir / "frontend.log")

    return [
        ".runtime/ports.json",
        ".runtime/launcher_state.json",
        "logs/launcher.log",
    ]

# ============================================================
# Requirements: parse + auto-generate/fix
# ============================================================

REQ_LINE_RE = re.compile(
    r"""^\s*(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)\s*(?P<spec>(==|>=|<=|~=|!=|>|<).+)?\s*$""",
    re.VERBOSE,
)

@dataclass
class RequirementsInfo:
    path: Optional[Path] = None
    lines: List[str] = field(default_factory=list)
    names: Set[str] = field(default_factory=set)
    had_file: bool = False
    packages: Set[str] = field(default_factory=set)
    directive_lines: List[str] = field(default_factory=list)

def write_requirements(req_path: Path, lines: List[str]) -> None:
    req_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8", errors="ignore")

def _req_base_name(line: str) -> Optional[str]:
    s = line.strip()
    if not s or s.startswith("#"):
        return None
    if s.startswith(("-r ", "--requirement", "-e ", "--editable")):
        return None
    if s.startswith(("-", "--")):
        return None
    s = s.split("#", 1)[0].strip()
    for sep in ["==", ">=", "<=", "~=", "!=", ">", "<", ";", " @ ", "@"]:
        if sep in s:
            s = s.split(sep, 1)[0].strip()
    m = REQ_NAME_RE.match(s)
    return m.group(0).lower() if m else None

def parse_requirements(req_path: Path) -> RequirementsInfo:
    info = RequirementsInfo(path=req_path)
    if not req_path.exists():
        return info
    info.had_file = True
    info.lines = req_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    for line in info.lines:
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith(("-", "--")) or "://" in s or s.startswith("git+"):
            info.directive_lines.append(line)
            continue
        m = REQ_LINE_RE.match(s)
        if not m:
            info.directive_lines.append(line)
            continue
        name = m.group("name").strip().lower()
        info.packages.add(name)
        info.names.add(name)
    return info

def stdlib_names() -> Set[str]:
    names = getattr(sys, "stdlib_module_names", None)
    return set(names) if names else set()

def detect_local_toplevel(root: Path) -> Set[str]:
    local: Set[str] = set()
    for child in root.iterdir():
        if child.name.startswith("."):
            continue
        if child.is_file() and child.suffix == ".py":
            local.add(child.stem.lower())
        if child.is_dir() and (child / "__init__.py").exists():
            local.add(child.name.lower())

    src_dir = root / "src"
    if src_dir.is_dir():
        for c in src_dir.iterdir():
            if c.name.startswith("."):
                continue
            if c.is_file() and c.suffix == ".py":
                local.add(c.stem.lower())
            if c.is_dir() and (c / "__init__.py").exists():
                local.add(c.name.lower())

    return local

def normalize_to_pip_name(mod: str) -> str:
    return IMPORT_TO_PIP_MAP.get(mod, mod).lower().replace("_", "-")

def filter_third_party_candidates(root: Path, imported_modules: Set[str]) -> List[str]:
    stdlib = stdlib_names()
    local = detect_local_toplevel(root)
    out: Set[str] = set()
    for m in imported_modules:
        ml = m.lower()
        if ml in stdlib or ml in local or ml in LOCAL_NAME_BLOCKLIST or ml in {"__future__", "builtins"}:
            continue
        pip_name = normalize_to_pip_name(m)
        for canon, eqs in PIP_EQUIVALENTS.items():
            if pip_name in eqs:
                pip_name = canon
                break
        out.add(pip_name)
    return sorted(out)

def generate_or_fix_requirements(root: Path, pkgs: List[str]) -> None:
    req_path = root / "requirements.txt"
    header = [
        "# Auto-generated requirements.txt",
        "# Generated by project_launcher.py",
        "# Note: versions are intentionally not pinned. Pin after first successful install if needed.",
        "",
    ]
    req_path.write_text("\n".join(header + sorted(set(pkgs))) + "\n", encoding="utf-8")
    print("[FIX] requirements.txt 已自動建立/修正（已排除 stdlib/本地模組/常見黑名單）。")

def ensure_requirements_minimal(root: Path, req_path: Path, imported_modules: Set[str]) -> Tuple[RequirementsInfo, List[str]]:
    actions: List[str] = []
    info = parse_requirements(req_path)
    pkgs = filter_third_party_candidates(root, imported_modules)

    if not info.had_file:
        write_requirements(req_path, ["# Auto-generated by project_launcher.py (minimal)", *pkgs])
        actions.append("created requirements.txt")
        return parse_requirements(req_path), actions

    present = set(info.names)
    normalized_present = set(present)
    for canon, eqs in PIP_EQUIVALENTS.items():
        if present & eqs:
            normalized_present.add(canon)

    missing = [pkg for pkg in pkgs if pkg not in normalized_present]
    if missing:
        lines = info.lines[:]
        if lines and lines[-1].strip():
            lines.append("")
        lines.append("# Auto-added by project_launcher.py (missing imports)")
        lines.extend(missing)
        write_requirements(req_path, lines)
        actions.append(f"append missing: {', '.join(missing)}")
        info = parse_requirements(req_path)

    return info, actions

# ============================================================
# Scan imports (AST)
# ============================================================

@dataclass
class ImportUsage:
    file: Path
    module: str
    lineno: int

@dataclass
class ScanResult:
    imports: List[ImportUsage] = field(default_factory=list)
    syntax_errors: List[Tuple[Path, str]] = field(default_factory=list)

class ImportScanner(ast.NodeVisitor):
    def __init__(self, file_path: Path):
        self.file_path = file_path
        self.imports: List[ImportUsage] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            mod = alias.name.split(".")[0]
            self.imports.append(ImportUsage(self.file_path, mod, node.lineno))
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if getattr(node, "level", 0) and node.level > 0:
            return
        if node.module:
            mod = node.module.split(".")[0]
            self.imports.append(ImportUsage(self.file_path, mod, node.lineno))
        self.generic_visit(node)

def scan_imports(root: Path) -> ScanResult:
    res = ScanResult()
    for p in root.rglob("*.py"):
        if any(part in EXCLUDE_DIRS for part in p.parts):
            continue
        try:
            code = p.read_text(encoding="utf-8", errors="ignore")
            tree = ast.parse(code, filename=str(p))
            sc = ImportScanner(p)
            sc.visit(tree)
            res.imports.extend(sc.imports)
        except SyntaxError as e:
            res.syntax_errors.append((p, f"{e.msg} (line {e.lineno})"))
        except Exception as e:
            res.syntax_errors.append((p, f"Parse error: {e}"))
    return res

def iter_files(root: Path, suffixes: Tuple[str, ...], max_files: int = 20000) -> Iterator[Path]:
    count = 0
    suffixes_l = tuple(s.lower() for s in suffixes)
    for dir_path, dir_names, file_names in os.walk(root):
        dir_names[:] = [name for name in dir_names if name not in EXCLUDE_DIRS and not name.startswith(".")]
        for file_name in file_names:
            if not file_name.lower().endswith(suffixes_l):
                continue
            path = Path(dir_path) / file_name
            if any(part in EXCLUDE_DIRS for part in path.parts):
                continue
            yield path
            count += 1
            if count >= max_files:
                return

def iter_relative_import_fix_candidates(root: Path) -> List[Path]:
    out: List[Path] = []
    seen: Set[Path] = set()
    for rel in RELATIVE_IMPORT_FIX_CANDIDATES:
        p = root / rel
        if p.is_file() and p not in seen:
            seen.add(p)
            out.append(p)
    return out

def module_parts_from_file(root: Path, path: Path) -> Optional[List[str]]:
    try:
        rel = path.relative_to(root)
    except Exception:
        return None
    if rel.suffix.lower() != ".py":
        return None
    parts = list(rel.with_suffix("").parts)
    if not parts:
        return None
    if parts[-1] == "__init__":
        return parts[:-1]
    return parts

def format_import_aliases(names: List[ast.alias]) -> str:
    parts: List[str] = []
    for alias in names:
        if alias.asname:
            parts.append(f"{alias.name} as {alias.asname}")
        else:
            parts.append(alias.name)
    return ", ".join(parts)

def resolve_absolute_import(module_parts: List[str], node: ast.ImportFrom) -> Optional[str]:
    package_parts = module_parts[:-1]
    steps_up = max(node.level - 1, 0)
    if steps_up > len(package_parts):
        return None
    if steps_up:
        package_parts = package_parts[: len(package_parts) - steps_up]
    if node.module:
        package_parts = package_parts + node.module.split(".")
    if not package_parts:
        return None
    return ".".join(package_parts)

def apply_relative_import_fixes(root: Path) -> List[str]:
    actions: List[str] = []

    for path in iter_relative_import_fix_candidates(root):
        module_parts = module_parts_from_file(root, path)
        if not module_parts:
            continue

        original = read_text(path)
        try:
            tree = ast.parse(original, filename=str(path))
        except SyntaxError:
            continue

        lines = original.splitlines()
        replacements: List[Tuple[int, int, str]] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or getattr(node, "level", 0) <= 0:
                continue
            abs_target = resolve_absolute_import(module_parts, node)
            if not abs_target:
                continue
            alias_text = format_import_aliases(node.names)
            if not alias_text:
                continue
            replacement = f"from {abs_target} import {alias_text}"
            current = "\n".join(lines[node.lineno - 1:node.end_lineno]).strip()
            if current == replacement:
                continue
            replacements.append((node.lineno, node.end_lineno, replacement))

        if not replacements:
            continue

        backup_path = path.with_suffix(path.suffix + ".bak")
        shutil.copy2(path, backup_path)

        new_lines = lines[:]
        for start, end, replacement in sorted(replacements, key=lambda item: item[0], reverse=True):
            new_lines[start - 1:end] = [replacement]

        new_text = "\n".join(new_lines)
        if original.endswith("\n"):
            new_text += "\n"
        path.write_text(new_text, encoding="utf-8", newline="\n")

        actions.append(
            f"rewrote relative imports in {norm_rel(root, path)} and wrote backup {norm_rel(root, backup_path)}"
        )

    return actions

# ============================================================
# venv + install + pip check + import test
# ============================================================

def venv_python(root: Path, venv_dir: str) -> Path:
    venv_path = (root / venv_dir).resolve()
    return venv_path / ("Scripts/python.exe" if is_windows() else "bin/python")

def ensure_venv(root: Path, venv_dir: str) -> None:
    vp = venv_python(root, venv_dir)
    if vp.exists():
        return
    rc, out = run_cmd([sys.executable, "-m", "venv", str((root / venv_dir).resolve())], cwd=root)
    print(out.rstrip())
    if rc != 0:
        raise RuntimeError("無法建立虛擬環境。可能權限不足或被防毒攔截。")

def pip_install_requirements(root: Path, venv_dir: str) -> None:
    vp = venv_python(root, venv_dir)
    req = root / "requirements.txt"
    if not req.exists():
        raise RuntimeError("找不到 requirements.txt，無法安裝套件。")

    rc, out = run_cmd([str(vp), "-m", "pip", "install", "--upgrade", "pip"], cwd=root)
    print(out.rstrip())
    rc2, out2 = run_cmd([str(vp), "-m", "pip", "install", "-r", str(req)], cwd=root)
    print(out2.rstrip())
    if rc2 != 0:
        raise RuntimeError("pip install -r requirements.txt 失敗。可能是套件名錯誤、網路/代理、或版本衝突。")

def try_autofix_no_match(req_path: Path, pip_output: str, local_names: Set[str]) -> bool:
    match = NO_MATCH_RE.search(pip_output or "")
    if not match:
        return False
    bad_name = match.group(1).strip().lower()
    if not bad_name or bad_name not in local_names:
        return False

    lines = req_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    changed = False
    rewritten: List[str] = []
    for line in lines:
        base = _req_base_name(line)
        if base and base == bad_name:
            rewritten.append(f"# [AUTO-COMMENTED local-name] {line}")
            changed = True
        else:
            rewritten.append(line)

    if not changed:
        return False

    backup = req_path.with_suffix(".txt.bak")
    try:
        shutil.copyfile(req_path, backup)
    except Exception:
        pass
    write_requirements(req_path, rewritten)
    return True

def pip_check(root: Path, venv_dir: str) -> None:
    vp = venv_python(root, venv_dir)
    rc, out = run_cmd([str(vp), "-m", "pip", "check"], cwd=root)
    print(out.rstrip())
    if rc != 0:
        raise RuntimeError("pip check 發現依賴問題（缺依賴或衝突）。")

def import_test_third_party(root: Path, venv_dir: str, imported_modules: Set[str]) -> None:
    vp = venv_python(root, venv_dir)
    stdlib = stdlib_names()
    local = detect_local_toplevel(root)

    failed: List[str] = []
    for mod in sorted(imported_modules):
        ml = mod.lower()
        if ml in stdlib or ml in local or ml in LOCAL_NAME_BLOCKLIST or ml in {"__future__", "builtins"}:
            continue
        rc, _ = run_cmd([str(vp), "-c", f"import {mod}"], cwd=root)
        if rc != 0:
            failed.append(mod)

    if failed:
        msg = "以下模組無法 import（通常代表 requirements.txt 缺漏、套件名對不到、或版本不相容）：\n" + "\n".join(f"- {m}" for m in failed)
        raise RuntimeError(msg)

# ============================================================
# Backend detection: uvicorn target + module fallback (no hardcode)
# ============================================================

STREAMLIT_PAT = re.compile(r"(?m)^\s*(import\s+streamlit\s+as\s+st|from\s+streamlit\s+import\s+)")

def file_contains(path: Path, pattern: re.Pattern) -> bool:
    try:
        return pattern.search(read_text(path)) is not None
    except Exception:
        return False

# uvicorn xxx:yyy [--host ...] [--port ...]
UVI_CMD_RE = re.compile(
    r"""(?ix)
    (?:python\s+-m\s+uvicorn|uvicorn)\s+
    (?P<target>[A-Za-z0-9_\.]+:[A-Za-z0-9_]+)
    (?P<rest>.*)
    """
)

def parse_host_port_from_args(text: str) -> Tuple[Optional[str], Optional[int]]:
    host = None
    port = None

    m = re.search(r"""(?ix)\s--host(?:\s+|=)(?P<host>[A-Za-z0-9\.\-_:]+)""", text)
    if m:
        host = m.group("host").strip()

    m = re.search(r"""(?ix)\s--port(?:\s+|=)(?P<port>\d{2,5})""", text)
    if m:
        port = safe_int(m.group("port"))

    if port is None:
        m = re.search(r"""(?ix)\s-p(?:\s+|=)(?P<port>\d{2,5})""", text)
        if m:
            port = safe_int(m.group("port"))

    return host, port

def detect_uvicorn_from_text(text: str) -> Tuple[Optional[str], Optional[str], Optional[int]]:
    m = UVI_CMD_RE.search(text)
    if m:
        target = m.group("target")
        rest = m.group("rest") or ""
        host, port = parse_host_port_from_args(rest)
        return target, host, port

    # argv-list 型（務實 window 掃描）
    idx = text.lower().find("uvicorn")
    if idx != -1:
        window = text[idx: idx + 400]
        mm = re.search(r"""(?ix)(?P<target>[A-Za-z0-9_\.]+:[A-Za-z0-9_]+)""", window)
        if mm:
            target = mm.group("target")
            host, port = parse_host_port_from_args(window)
            return target, host, port

    return None, None, None

def detect_node_package_manager(pkg_dir: Path) -> str:
    if (pkg_dir / "pnpm-lock.yaml").is_file():
        return "pnpm"
    if (pkg_dir / "yarn.lock").is_file():
        return "yarn"
    return "npm"

def load_package_scripts(pkg_path: Path) -> Dict[str, str]:
    try:
        data = json.loads(read_text_quick(pkg_path)) or {}
    except Exception:
        return {}
    scripts = data.get("scripts", {}) or {}
    if not isinstance(scripts, dict):
        return {}
    return {str(k): str(v) for k, v in scripts.items()}

def choose_node_backend_script(scripts: Dict[str, str]) -> Tuple[Optional[str], Optional[str]]:
    for name in ("dev", "start:dev", "start", "serve"):
        cmd = scripts.get(name)
        if cmd:
            return name, cmd

    for name, cmd in scripts.items():
        lowered = name.lower()
        if any(token in lowered for token in ("dev", "start", "serve", "api")):
            return name, cmd
    return None, None

def build_node_run_command(pm: str, script_name: str) -> Tuple[str, str]:
    if pm == "pnpm":
        return "pnpm install", f"pnpm {script_name}"
    if pm == "yarn":
        return "yarn install", f"yarn {script_name}"
    return "npm install", f"npm run {script_name}"

def iter_backend_package_jsons(root: Path) -> List[Path]:
    found: List[Path] = []
    seen: Set[Path] = set()

    def add_candidate(path: Path) -> None:
        if not path.is_file():
            return
        if path in seen:
            return
        seen.add(path)
        found.append(path)

    for rel in BACKEND_NODE_PKG_CANDIDATES:
        add_candidate(root / rel)

    frontend_dir_markers = {"frontend", "client", "web", "ui"}
    for pkg in root.rglob("package.json"):
        if any(part in EXCLUDE_DIRS for part in pkg.parts):
            continue
        try:
            rel = pkg.relative_to(root)
        except ValueError:
            continue
        rel_parts = [part.lower() for part in rel.parts[:-1]]
        if not rel_parts:
            continue
        if any(part in frontend_dir_markers for part in rel_parts):
            continue
        if not any(part in BACKEND_HINT_DIRS for part in rel_parts):
            continue
        add_candidate(pkg)

    return found

def detect_node_backend_mode(root: Path, cfg: Dict[str, str]) -> Optional[dict]:
    for pkg in iter_backend_package_jsons(root):
        scripts = load_package_scripts(pkg)
        if not scripts:
            continue

        script_name, script_cmd = choose_node_backend_script(scripts)
        if not script_name or not script_cmd:
            continue

        backend_dir = pkg.parent
        pm = detect_node_package_manager(backend_dir)
        install_cmd, run_cmd = build_node_run_command(pm, script_name)
        host, port = parse_frontend_host_port_from_script(script_cmd)

        for env_name in (".env", ".env.local", ".env.development", ".env.production"):
            env_path = backend_dir / env_name
            if not env_path.is_file():
                continue
            env_host, env_port = parse_env_host_port(read_text_quick(env_path), allow_generic_port=True)
            host = host or env_host
            port = port or env_port

        if cfg.get("BACKEND_HOST"):
            host = cfg["BACKEND_HOST"].strip() or host
        if cfg.get("BACKEND_PORT"):
            cfg_port = safe_int(cfg["BACKEND_PORT"])
            if cfg_port is not None:
                port = cfg_port

        if host is None:
            host = "127.0.0.1"
        if port is None:
            port = 8000

        rel_dir = str(backend_dir.relative_to(root)).replace("/", "\\") if backend_dir != root else "."
        rel_pkg = str(pkg.relative_to(root)).replace("/", "\\")
        return {
            "mode": "node",
            "dir": rel_dir,
            "package_json": rel_pkg,
            "pm": pm,
            "script": script_name,
            "install_cmd": install_cmd,
            "run_cmd": run_cmd,
            "host": host,
            "port": port,
            "notes": [f"Detected Node backend package in {rel_pkg} via script '{script_name}'."],
        }

    return None

def find_backend_start_script(root: Path) -> Optional[str]:
    for rel in START_BACKEND_SCRIPT_CANDIDATES:
        p = root / rel
        if p.is_file():
            return str(p.relative_to(root)).replace("/", "\\")
    return None


def should_skip_backend_text_scan(root: Path, path: Path) -> bool:
    name = path.name.lower()
    if name in {"run_app.bat", "run_app.sh", "run_app.command"}:
        return True
    try:
        rel = path.relative_to(root)
    except ValueError:
        rel = path
    rel_parts = [part.lower() for part in rel.parts]
    if name.startswith("project_launcher") and "scripts" in rel_parts:
        return True
    return False

def infer_uvicorn_target_from_code(root: Path) -> Optional[str]:
    # 保守推：FastAPI/ASGI app assignment
    FASTAPI_HINT_RE = re.compile(r"(?m)^\s*(from\s+fastapi\s+import\s+FastAPI|import\s+fastapi)\b|\bFastAPI\s*\(")
    ASGI_ASSIGN_RE = re.compile(r"(?m)^\s*(app|application)\s*=\s*")

    candidates: List[Tuple[str, Path]] = []
    for py in root.rglob("*.py"):
        if any(part in EXCLUDE_DIRS for part in py.parts):
            continue
        try:
            t = read_text(py)
        except Exception:
            continue
        if not (FASTAPI_HINT_RE.search(t) and ASGI_ASSIGN_RE.search(t)):
            continue

        rel = py.relative_to(root)
        parts = list(rel.parts)
        parts[-1] = Path(parts[-1]).stem
        mod = ".".join(parts)

        if re.search(r"(?m)^\s*app\s*=", t):
            candidates.append((f"{mod}:app", py))
        if re.search(r"(?m)^\s*application\s*=", t):
            candidates.append((f"{mod}:application", py))

    if not candidates:
        return None

    uniq: Dict[str, Path] = {t: f for t, f in candidates}
    items = list(uniq.items())

    def score(item: Tuple[str, Path]) -> Tuple[int, int]:
        target, f = item
        s1 = len(f.parts)
        s2 = 0
        name = f.stem.lower()
        if name in ("main", "app"):
            s2 -= 2
        if "backend" in [x.lower() for x in f.parts]:
            s2 -= 1
        if "app" in [x.lower() for x in f.parts]:
            s2 -= 1
        return (s1, s2)

    items.sort(key=score)
    return items[0][0]

def detect_backend_mode(root: Path, cfg: Dict[str, str]) -> dict:
    """
    返回 dict:
      uvicorn: {mode, target, host, port, notes}
      module : {mode, module, file}
      node   : {mode, dir, pm, script, install_cmd, run_cmd, host, port, notes}
      streamlit fallback
    """
    notes: List[str] = []

    # 0) user-specified override (env/.launcher.env)
    if cfg.get("UVICORN_TARGET"):
        target = cfg["UVICORN_TARGET"].strip()
        host = cfg.get("BACKEND_HOST", "").strip() or None
        port = safe_int(cfg.get("BACKEND_PORT", "")) if cfg.get("BACKEND_PORT") else None
        notes.append("Using UVICORN_TARGET from config.")
        return {"mode": "uvicorn", "target": target, "host": host, "port": port, "notes": notes}

    explicit_script = find_backend_start_script(root)
    if explicit_script:
        notes.append(f"Found explicit backend start script: {explicit_script}")
        return {
            "mode": "script",
            "rel_script": explicit_script,
            "host": cfg.get("BACKEND_HOST", "").strip() or "127.0.0.1",
            "port": safe_int(cfg.get("BACKEND_PORT", "")) or 8000,
            "notes": notes,
        }

    # 1) command/argv-based detection
    for ext in (".bat", ".cmd", ".ps1", ".sh", ".yml", ".yaml", ".md", ".txt", ".py"):
        for p in root.rglob(f"*{ext}"):
            if any(part in EXCLUDE_DIRS for part in p.parts):
                continue
            if should_skip_backend_text_scan(root, p):
                continue
            try:
                t = read_text(p)
            except Exception:
                continue

            target, host, port = detect_uvicorn_from_text(t)
            if target:
                notes.append(f"Found uvicorn target in {p}: {target}")
                return {"mode": "uvicorn", "target": target, "host": host, "port": port, "notes": notes}

    # 2) infer from code
    inferred = infer_uvicorn_target_from_code(root)
    if inferred:
        notes.append("Inferred uvicorn target from code (FastAPI/ASGI assignment).")
        return {"mode": "uvicorn", "target": inferred, "host": None, "port": None, "notes": notes}

    # 3) node backend package fallback (monorepo/services/api, backend/, server/, ...)
    node_backend = detect_node_backend_mode(root, cfg)
    if node_backend:
        return node_backend

    # 4) streamlit fallback (only if truly streamlit)
    for rel in ["streamlit_app.py", "src/streamlit_app.py", "src/app.py", "src/main.py", "app.py", "main.py"]:
        p = root / rel
        if p.is_file() and file_contains(p, STREAMLIT_PAT):
            return {"mode": "streamlit", "file": str(p.relative_to(root)).replace("/", "\\")}

    # 5) module fallback (ensure backend still starts)
    for rel in MODULE_ENTRY_CANDIDATES:
        p = root / rel
        if p.is_file():
            relp = str(p.relative_to(root)).replace("/", "\\")
            mod = relp[:-3].replace("\\", ".")
            return {"mode": "module", "module": mod, "file": relp}

    return {"mode": "none", "notes": notes}

def needs_src_pythonpath_fix(root: Path, entry_module: str) -> bool:
    if not entry_module.startswith("src."):
        return False
    src_dir = root / "src"
    if not src_dir.is_dir():
        return False
    suspects = ["utils", "config", "db", "core", "common", "services", "routers", "models", "schemas"]
    for name in suspects:
        has_src = (src_dir / name).is_dir() or (src_dir / f"{name}.py").is_file()
        has_root = (root / name).is_dir() or (root / f"{name}.py").is_file()
        if has_src and not has_root:
            return True
    return False

# ============================================================
# Frontend detection + Static site detection
# ============================================================

@dataclass
class FrontendInfo:
    exists: bool = False
    dir: str = ""
    pm: str = ""
    script: str = ""
    install_cmd: str = ""
    run_cmd: str = ""
    host: Optional[str] = None
    port: Optional[int] = None
    direct_cmd_for_bat: Optional[str] = None
    port_source: str = "unknown"

@dataclass
class LocalPyEntry:
    exists: bool = False
    entry: Optional[Path] = None
    module_target: Optional[str] = None
    import_root: Optional[Path] = None
    is_gui_like: bool = False

@dataclass
class DetectionResult:
    root: Path
    cfg: Dict[str, str]
    project_type: str
    frontend: FrontendInfo
    backend: dict
    local_py: LocalPyEntry
    static_site: "StaticSiteInfo"

def _infer_import_root(root: Path, file_path: Path) -> Path:
    parts = [part.lower() for part in file_path.parts]
    for marker in ("src", "backend"):
        if marker in parts:
            idx = parts.index(marker)
            candidate = Path(*file_path.parts[: idx + 1])
            try:
                _ = candidate.relative_to(root)
                if candidate.is_dir():
                    return candidate
            except Exception:
                continue
    return root

def _file_to_module(import_root: Path, file_path: Path) -> Optional[str]:
    try:
        rel = file_path.relative_to(import_root)
    except Exception:
        return None
    if rel.suffix.lower() != ".py":
        return None
    parts = list(rel.parts)
    parts[-1] = parts[-1][:-3]
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)

def detect_local_python_entry(root: Path) -> LocalPyEntry:
    candidates: List[Tuple[int, Path]] = []
    for base in [root / "src", root]:
        if not base.is_dir():
            continue
        for py in iter_files(base, (".py",), max_files=8000):
            if py.name not in {"__main__.py", "main.py", "app.py"}:
                continue
            try:
                rel = py.relative_to(base)
            except Exception:
                continue
            parts = [part.lower() for part in rel.parts]
            if any(part in BACKEND_HINT_DIRS for part in parts):
                continue

            score = 0
            if py.name == "__main__.py":
                score += 60
                if len(rel.parts) >= 2:
                    score += 20
            if py.name in {"main.py", "app.py"}:
                score += 15
            score += max(0, 20 - len(rel.parts) * 2)
            candidates.append((score, py))

    if not candidates:
        return LocalPyEntry(exists=False)

    candidates.sort(key=lambda item: item[0], reverse=True)
    best = candidates[0][1]
    import_root = _infer_import_root(root, best)
    module_target = _file_to_module(import_root, best)
    if module_target and best.name == "__main__.py" and module_target.endswith(".__main__"):
        module_target = module_target[: -len(".__main__")]

    text = read_text_quick(best).lower()
    is_gui_like = any(hint in text for hint in UI_IMPORT_HINTS) or "qapplication" in text or "tk(" in text
    return LocalPyEntry(
        exists=True,
        entry=best,
        module_target=module_target,
        import_root=import_root,
        is_gui_like=is_gui_like,
    )

def detect_project(root: Path, cfg: Dict[str, str]) -> DetectionResult:
    frontend = detect_frontend(root, cfg)
    backend = detect_backend_mode(root, cfg)
    local_py = detect_local_python_entry(root)
    static_site = StaticSiteInfo(exists=False)
    if not frontend.exists:
        static_site = detect_static_site(root, cfg)

    if frontend.exists and backend.get("mode") != "none":
        project_type = "web_split"
    elif local_py.exists and local_py.is_gui_like and backend.get("mode") != "none":
        project_type = "embedded_gui_plus_backend"
    elif backend.get("mode") != "none":
        project_type = "backend_only"
    elif frontend.exists:
        project_type = "frontend_only"
    elif local_py.exists:
        project_type = "local_py"
    elif static_site.exists:
        project_type = "static_only"
    else:
        project_type = "unknown"

    return DetectionResult(
        root=root,
        cfg=cfg,
        project_type=project_type,
        frontend=frontend,
        backend=backend,
        local_py=local_py,
        static_site=static_site,
    )

def parse_frontend_host_port_from_script(script: str) -> Tuple[Optional[str], Optional[int]]:
    host = None
    port = None
    m = re.search(r"""(?ix)\s--host(?:\s+|=)(?P<host>[A-Za-z0-9\.\-_:]+)""", script)
    if m:
        host = m.group("host").strip()
    m = re.search(r"""(?ix)\s--hostname(?:\s+|=)(?P<host>[A-Za-z0-9\.\-_:]+)""", script)
    if m:
        host = m.group("host").strip()
    m = re.search(r"""(?ix)\s--port(?:\s+|=)(?P<port>\d{2,5})""", script)
    if m:
        port = safe_int(m.group("port"))
    if port is None:
        m = re.search(r"""(?ix)\s-p(?:\s+|=)(?P<port>\d{2,5})""", script)
        if m:
            port = safe_int(m.group("port"))
    if "http.server" in script:
        m = re.search(r"""(?ix)\bhttp\.server\b(?:\s+(?P<port>\d{2,5}))?""", script)
        if m and port is None:
            port = safe_int(m.group("port") or "")
        m = re.search(r"""(?ix)\s--bind(?:\s+|=)(?P<host>[A-Za-z0-9\.\-_:]+)""", script)
        if m and host is None:
            host = m.group("host").strip()
        m = re.search(r"""(?ix)\s-b(?:\s+|=)(?P<host>[A-Za-z0-9\.\-_:]+)""", script)
        if m and host is None:
            host = m.group("host").strip()
    return host, port

def parse_env_port(env_text: str) -> Tuple[Optional[str], Optional[int]]:
    host = None
    port = None
    m = re.search(r"""(?m)^\s*PORT\s*=\s*(\d{2,5})\s*$""", env_text)
    if m:
        port = safe_int(m.group(1))
    if port is None:
        m = re.search(r"""(?m)^\s*VITE_PORT\s*=\s*(\d{2,5})\s*$""", env_text)
        if m:
            port = safe_int(m.group(1))
    m = re.search(r"""(?m)^\s*HOST\s*=\s*([A-Za-z0-9\.\-_:]+)\s*$""", env_text)
    if m:
        host = m.group(1).strip()
    return host, port

def parse_env_host_port(env_text: str, allow_generic_port: bool = True) -> Tuple[Optional[str], Optional[int]]:
    host = None
    port = None
    for line in (env_text or "").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        key, value = s.split("=", 1)
        key = key.strip().upper()
        value = value.strip().strip('"').strip("'")
        if not value:
            continue
        if host is None and key in {"FRONTEND_HOST", "VITE_HOST", "HOST"}:
            host = value
        if port is None:
            if key in {"FRONTEND_PORT", "VITE_PORT"}:
                port = safe_int(value)
            elif ("PORT" in key) and (("VITE" in key) or ("FRONTEND" in key) or ("DEV" in key)):
                port = safe_int(value)
            elif allow_generic_port and key == "PORT":
                port = safe_int(value)
    return host, port

def detect_vite_host_port(fe_dir: Path) -> Tuple[Optional[str], Optional[int]]:
    for name in ("vite.config.ts", "vite.config.js", "vite.config.mjs", "vite.config.cjs"):
        path = fe_dir / name
        if not path.is_file():
            continue
        text = read_text_quick(path)
        host_match = re.search(r"\bhost\s*:\s*['\"]([^'\"]+)['\"]", text)
        port_match = re.search(r"\bport\s*:\s*(\d+)", text)
        return (
            host_match.group(1).strip() if host_match else None,
            safe_int(port_match.group(1)) if port_match else None,
        )
    return None, None

def parse_npx_serve_for_bat(script: str) -> Tuple[Optional[int], Optional[str]]:
    if not script:
        return None, None

    m = SERVE_LISTEN_RE.search(script)
    if not m:
        return None, None

    arg = m.group("arg").strip().strip('"').strip("'")
    direct_cmd = script

    port = safe_int(arg)
    if port is not None:
        return port, direct_cmd

    md = POSIX_DEFAULT_RE.search(arg)
    if not md:
        return None, None

    default_port = safe_int(md.group(2))
    if default_port is None:
        return None, None

    return default_port, direct_cmd.replace(arg, str(default_port), 1)

def detect_frontend(root: Path, cfg: Dict[str, str]) -> FrontendInfo:
    pkg = next((root / rel for rel in FRONTEND_PKG_CANDIDATES if (root / rel).is_file()), None)
    if not pkg:
        return FrontendInfo(exists=False)

    fe_dir = pkg.parent
    if (fe_dir / "pnpm-lock.yaml").is_file():
        pm = "pnpm"
    elif (fe_dir / "yarn.lock").is_file():
        pm = "yarn"
    else:
        pm = "npm"

    scripts: Dict[str, str] = {}
    try:
        scripts_data = (json.loads(read_text_quick(pkg)) or {}).get("scripts", {}) or {}
        if isinstance(scripts_data, dict):
            scripts = {str(k): str(v) for k, v in scripts_data.items()}
    except Exception:
        scripts = {}

    script_name = "dev" if "dev" in scripts else ("start" if "start" in scripts else ("serve" if "serve" in scripts else "dev"))
    chosen_script = scripts.get(script_name, "")

    host, port = parse_frontend_host_port_from_script(chosen_script)
    port_source = "package_json_script" if port is not None else "unknown"
    direct_cmd_for_bat = None

    serve_port, serve_direct_cmd = parse_npx_serve_for_bat(chosen_script)
    if port is None and serve_port is not None:
        port = serve_port
        port_source = "package_json_script:serve_listen"
    if serve_direct_cmd:
        direct_cmd_for_bat = serve_direct_cmd

    if cfg.get("FRONTEND_HOST"):
        host = cfg["FRONTEND_HOST"].strip() or host
    if cfg.get("FRONTEND_PORT"):
        cfg_port = safe_int(cfg["FRONTEND_PORT"])
        if cfg_port is not None:
            port = cfg_port
            port_source = "launcher_cfg"

    for env_name in (".env", ".env.local", ".env.development", ".env.production"):
        env_path = fe_dir / env_name
        if env_path.is_file():
            env_host, env_port = parse_env_host_port(read_text_quick(env_path), allow_generic_port=True)
            host = host or env_host
            if port is None and env_port is not None:
                port = env_port
                port_source = f"frontend_env:{env_name}"
            else:
                port = port or env_port

    scripts_env = root / "scripts" / ".env"
    if scripts_env.is_file():
        env_host, env_port = parse_env_host_port(read_text_quick(scripts_env), allow_generic_port=True)
        if env_host:
            host = env_host
        if env_port is not None:
            port = env_port
            port_source = "scripts/.env"

    if host is None or port is None:
        vite_host, vite_port = detect_vite_host_port(fe_dir)
        host = host or vite_host
        if port is None and vite_port is not None:
            port = vite_port
            port_source = "vite_config"
        else:
            port = port or vite_port

    if port is None:
        if "vite" in chosen_script.lower() or any((fe_dir / name).is_file() for name in ("vite.config.ts", "vite.config.js", "vite.config.mjs", "vite.config.cjs")):
            port = 5173
        else:
            port = 3000
        port_source = "default"
    if host is None:
        host = "127.0.0.1"

    if pm == "npm":
        install_cmd = "npm install"
        run_cmdline = f"npm run {script_name}"
    elif pm == "pnpm":
        install_cmd = "pnpm install"
        run_cmdline = f"pnpm {script_name}"
    else:
        install_cmd = "yarn install"
        run_cmdline = f"yarn {script_name}"

    return FrontendInfo(
        exists=True,
        dir=str(fe_dir.relative_to(root)).replace("/", "\\") if fe_dir != root else ".",
        pm=pm,
        script=script_name,
        install_cmd=install_cmd,
        run_cmd=run_cmdline,
        host=host,
        port=port,
        direct_cmd_for_bat=direct_cmd_for_bat,
        port_source=port_source,
    )

@dataclass
class StaticSiteInfo:
    exists: bool = False
    dir: str = ""
    host: str = "127.0.0.1"
    port: int = 0

def detect_static_site(root: Path, cfg: Dict[str, str]) -> StaticSiteInfo:
    # If frontend exists (package.json), we don't treat it as static by default.
    # Static only when no package.json.
    host = cfg.get("STATIC_HOST", "").strip() or "127.0.0.1"
    port = safe_int(cfg.get("STATIC_PORT", "")) or 0

    for rel in STATIC_SITE_DIR_CANDIDATES:
        d = root / rel
        if d.is_dir():
            idx = d / "index.html"
            if idx.is_file():
                # If port not specified, pick a safe default different from backend default 8000:
                # Still "default" but deterministic. We'll choose 5173 for dist, 3000 for build.
                if port == 0:
                    base = Path(rel).name.lower()
                    port = 5173 if "dist" in rel.lower() else 3000
                return StaticSiteInfo(exists=True, dir=str(d.relative_to(root)).replace("/", "\\"), host=host, port=port)

    return StaticSiteInfo(exists=False)

def compose_detection_result(
    root: Path,
    cfg: Dict[str, str],
    backend: dict,
    frontend: FrontendInfo,
    static_site: StaticSiteInfo,
    local_py: Optional[LocalPyEntry] = None,
    project_type: Optional[str] = None,
) -> DetectionResult:
    local_py = local_py or LocalPyEntry(exists=False)
    if project_type is None:
        if frontend.exists and backend.get("mode") != "none":
            project_type = "web_split"
        elif local_py.exists and local_py.is_gui_like and backend.get("mode") != "none":
            project_type = "embedded_gui_plus_backend"
        elif backend.get("mode") != "none":
            project_type = "backend_only"
        elif frontend.exists:
            project_type = "frontend_only"
        elif local_py.exists:
            project_type = "local_py"
        elif static_site.exists:
            project_type = "static_only"
        else:
            project_type = "unknown"
    return DetectionResult(root, cfg, project_type, frontend, backend, local_py, static_site)

def bat_pythonpath_prefix(root: Path, import_root: Optional[Path]) -> str:
    if not import_root or import_root == root:
        return ""
    rel = norm_rel(root, import_root).replace("/", "\\")
    return f'set "PYTHONPATH=%CD%\\{rel};%CD%;%PYTHONPATH%"\n'

def shell_pythonpath_prefix(root: Path, import_root: Optional[Path]) -> str:
    if not import_root or import_root == root:
        return ""
    rel = norm_rel(root, import_root).replace("\\", "/")
    return f'PYTHONPATH="$ROOT/{rel}:$ROOT:${{PYTHONPATH:-}}" '

# ============================================================
# BAT generation (ASCII-only + default ports)
# ============================================================

def write_run_app_bat(root: Path, script_name: str, backend: dict,
                      frontend: FrontendInfo,
                      static_site: StaticSiteInfo,
                      cfg: Dict[str, str],
                      venv_dir: str,
                      det: Optional[DetectionResult] = None) -> Path:
    det = det or compose_detection_result(root, cfg, backend, frontend, static_site)
    mode = det.backend.get("mode", "none")
    local_py = det.local_py
    project_type = det.project_type
    has_runnable_frontend = det.frontend.exists or det.static_site.exists

    backend_host = (cfg.get("BACKEND_HOST", "").strip() or det.backend.get("host") or "127.0.0.1")
    backend_port = safe_int(cfg.get("BACKEND_PORT", "")) or det.backend.get("port") or 8000

    backend_start = ""
    gui_start = ""
    backend_url: Optional[str] = None
    frontend_url: Optional[str] = None

    if project_type == "embedded_gui_plus_backend" and mode != "none":
        backend_url = "http://%APP_BACKEND_HOST%:%APP_BACKEND_PORT%"
        if mode == "script":
            rel_script = det.backend.get("rel_script", "")
            backend_start = (
                'start "Backend" cmd /k "set APP_BACKEND_HOST=%APP_BACKEND_HOST% ^&^& '
                'set APP_BACKEND_PORT=%APP_BACKEND_PORT% ^&^& ""%PYEXE%"" '
                f'"{rel_script}" 1>>""%~dp0logs\\backend.log"" 2>>&1"\n'
            )
        elif mode == "uvicorn":
            target = det.backend.get("target", "")
            backend_start = (
                'start "Backend" cmd /k "set APP_BACKEND_HOST=%APP_BACKEND_HOST% ^&^& '
                'set APP_BACKEND_PORT=%APP_BACKEND_PORT% ^&^& ""%PYEXE%"" '
                f'-m uvicorn {target} --host %APP_BACKEND_HOST% --port %APP_BACKEND_PORT% '
                '--log-level info 1>>""%~dp0logs\\backend.log"" 2>>&1"\n'
            )
        elif mode == "module":
            module_name = det.backend.get("module", "")
            py_path_fix = 'set "PYTHONPATH=%CD%\\src;%CD%;%PYTHONPATH%"\n' if needs_src_pythonpath_fix(root, module_name) else ""
            backend_start = (
                py_path_fix
                + 'start "Backend" cmd /k "set APP_BACKEND_HOST=%APP_BACKEND_HOST% ^&^& '
                'set APP_BACKEND_PORT=%APP_BACKEND_PORT% ^&^& ""%PYEXE%"" '
                f'-m {module_name} 1>>""%~dp0logs\\backend.log"" 2>>&1"\n'
            )
        elif mode == "streamlit":
            entry = det.backend.get("file", "")
            backend_start = (
                'set "PYTHONPATH=%CD%\\src;%CD%;%PYTHONPATH%"\n'
                + 'start "Backend" cmd /k "set APP_BACKEND_HOST=%APP_BACKEND_HOST% ^&^& '
                'set APP_BACKEND_PORT=%APP_BACKEND_PORT% ^&^& ""%PYEXE%"" '
                f'-m streamlit run "{entry}" 1>>""%~dp0logs\\backend.log"" 2>>&1"\n'
            )
        elif mode == "node":
            backend_dir = det.backend.get("dir", ".")
            install_cmd = det.backend.get("install_cmd", "npm install")
            run_cmd = det.backend.get("run_cmd", "npm run dev")
            backend_start = (
                'start "Backend" cmd /k "set APP_BACKEND_HOST=%APP_BACKEND_HOST% ^&^& '
                'set APP_BACKEND_PORT=%APP_BACKEND_PORT% ^&^& '
                'set BACKEND_HOST=%APP_BACKEND_HOST% ^&^& '
                'set BACKEND_PORT=%APP_BACKEND_PORT% ^&^& '
                'set HOST=%APP_BACKEND_HOST% ^&^& '
                'set PORT=%APP_BACKEND_PORT% ^&^& '
                f'cd /d ""{backend_dir}"" ^&^& {install_cmd} 1>>""%~dp0logs\\backend.log"" 2>>&1 ^&^& '
                f'{run_cmd} 1>>""%~dp0logs\\backend.log"" 2>>&1"\n'
            )
        else:
            backend_start = 'echo [WARN] Embedded backend mode detected but no backend start command was built.\n'

        if local_py.exists and local_py.entry:
            py_path = bat_pythonpath_prefix(root, local_py.import_root)
            if local_py.module_target:
                gui_start = (
                    py_path
                    + 'start "GUI" cmd /k "set APP_BACKEND_HOST=%APP_BACKEND_HOST% ^&^& '
                    'set APP_BACKEND_PORT=%APP_BACKEND_PORT% ^&^& ""%PYEXE%"" '
                    f'-m {local_py.module_target} 1>>""%~dp0logs\\gui.log"" 2>>&1"\n'
                )
            else:
                gui_rel = norm_rel(root, local_py.entry).replace("/", "\\")
                gui_start = (
                    py_path
                    + 'start "GUI" cmd /k "set APP_BACKEND_HOST=%APP_BACKEND_HOST% ^&^& '
                    'set APP_BACKEND_PORT=%APP_BACKEND_PORT% ^&^& ""%PYEXE%"" '
                    f'"{gui_rel}" 1>>""%~dp0logs\\gui.log"" 2>>&1"\n'
                )
    elif mode == "script":
        rel_script = det.backend.get("rel_script", "")
        backend_start = f'start "Backend" cmd /k """%PYEXE%"" "{rel_script}" 1>>"logs\\backend.log" 2>>&1"\n'
        backend_url = f"http://{backend_host}:{backend_port}"
    elif mode == "uvicorn":
        target = det.backend.get("target", "")
        backend_start = f'start "Backend" cmd /k """%PYEXE%"" -m uvicorn {target} --host {backend_host} --port {backend_port} --log-level info 1>>"logs\\backend.log" 2>>&1"\n'
        backend_url = f"http://{backend_host}:{backend_port}"
    elif mode == "streamlit":
        entry = det.backend.get("file", "")
        backend_start = 'set "PYTHONPATH=%CD%\\src;%CD%;%PYTHONPATH%"\n' + f'start "Backend" cmd /k """%PYEXE%"" -m streamlit run "{entry}" 1>>"logs\\backend.log" 2>>&1"\n'
    elif mode == "node":
        backend_dir = det.backend.get("dir", ".")
        install_cmd = det.backend.get("install_cmd", "npm install")
        run_cmd = det.backend.get("run_cmd", "npm run dev")
        backend_start = (
            f'start "Backend" cmd /k "set APP_BACKEND_HOST={backend_host} ^&^& '
            f'set APP_BACKEND_PORT={backend_port} ^&^& '
            f'set BACKEND_HOST={backend_host} ^&^& '
            f'set BACKEND_PORT={backend_port} ^&^& '
            f'set HOST={backend_host} ^&^& '
            f'set PORT={backend_port} ^&^& '
            f'cd /d ""{backend_dir}"" ^&^& {install_cmd} 1>>"logs\\backend.log" 2>>&1 ^&^& '
            f'{run_cmd} 1>>"logs\\backend.log" 2>>&1"\n'
        )
        backend_url = f"http://{backend_host}:{backend_port}"
    elif mode == "module":
        module_name = det.backend.get("module", "")
        py_path_fix = 'set "PYTHONPATH=%CD%\\src;%CD%;%PYTHONPATH%"\n' if needs_src_pythonpath_fix(root, module_name) else ""
        backend_start = py_path_fix + f'start "Backend" cmd /k """%PYEXE%"" -m {module_name} 1>>"logs\\backend.log" 2>>&1"\n'
    elif local_py.exists and local_py.entry:
        py_path = bat_pythonpath_prefix(root, local_py.import_root)
        if local_py.module_target:
            backend_start = py_path + f'start "LocalPython" cmd /k """%PYEXE%"" -m {local_py.module_target} 1>>"logs\\local_python.log" 2>>&1"\n'
        else:
            app_rel = norm_rel(root, local_py.entry).replace("/", "\\")
            backend_start = py_path + f'start "LocalPython" cmd /k """%PYEXE%"" "{app_rel}" 1>>"logs\\local_python.log" 2>>&1"\n'
    elif has_runnable_frontend:
        backend_start = "echo [4/6] Backend not detected. Skipping backend startup.\n"
    else:
        backend_start = "echo [4/6] No runnable backend, local Python entry, or static site was detected.\n"

    if det.frontend.exists:
        frontend_run_cmd = det.frontend.direct_cmd_for_bat or det.frontend.run_cmd
        frontend_start_dir = "%~dp0." if det.frontend.dir == "." else f"%~dp0{det.frontend.dir}"
        frontend_start = (
            f'echo [5/6] Starting frontend (Node project)...\n'
            f'echo [INFO] Frontend dir: {det.frontend.dir}\n'
            f'cd /d "{det.frontend.dir}"\n'
            'if errorlevel 1 (\n'
            f'  echo [WARN] Frontend cd failed for path: {det.frontend.dir}\n'
            ') else (\n'
            f'  start "Frontend" /d "{frontend_start_dir}" cmd /c "{det.frontend.install_cmd} 1>>""%~dp0logs\\frontend.log"" 2>>&1 ^&^& {frontend_run_cmd} 1>>""%~dp0logs\\frontend.log"" 2>>&1"\n'
            ')\n'
            'cd /d "%~dp0"\n'
        )
        if det.frontend.port:
            frontend_url = f"http://{det.frontend.host or '127.0.0.1'}:{det.frontend.port}"
    elif det.static_site.exists:
        frontend_start = (
            f'echo [5/6] Starting frontend (static site: {det.static_site.dir})...\n'
            f'start "Frontend" cmd /k "cd /d ""{det.static_site.dir}"" ^&^& ""%PYEXE%"" -m http.server {det.static_site.port} --bind {det.static_site.host} 1>>""%~dp0logs\\frontend.log"" 2>>&1"\n'
        )
        frontend_url = f"http://{det.static_site.host}:{det.static_site.port}"
    else:
        frontend_start = "echo [5/6] Frontend not detected. Skipping frontend startup.\n"

    open_block = "echo [6/6] Waiting for the local app...\n"
    if frontend_url:
        health_url = f"{frontend_url}/api/health"
        open_block += f'"%PYEXE%" "scripts\\wait_for_http.py" "{health_url}" 30 "lingxi-summary-lab" 1>>"logs\\launcher.log" 2>>&1\n'
        open_block += "if errorlevel 1 (\n"
        open_block += "  echo [ERROR] The local app did not become ready. See logs\\frontend.log and logs\\launcher.log.\n"
        open_block += ") else (\n"
        open_block += f'  start "" "{frontend_url}"\n'
        open_block += ")\n"
    elif backend_url:
        open_block += f'"%PYEXE%" "scripts\\wait_for_http.py" "{backend_url}" 30 1>>"logs\\launcher.log" 2>>&1\n'
        open_block += "if errorlevel 1 (\n"
        open_block += "  echo [ERROR] The local app did not become ready. See logs\\backend.log and logs\\launcher.log.\n"
        open_block += ") else (\n"
        open_block += f'  start "" "{backend_url}"\n'
        open_block += ")\n"
    else:
        open_block += "echo [ERROR] No local app URL was detected.\n"

    embedded_port_block = ""
    if project_type == "embedded_gui_plus_backend" and mode != "none":
        embedded_port_block = (
            'set "APP_BACKEND_HOST=127.0.0.1"\n'
            'set "APP_BACKEND_PORT="\n'
            'for /f "usebackq delims=" %%P in (`"%PYEXE%" -c "import socket; s=socket.socket(); s.bind((\'127.0.0.1\',0)); print(s.getsockname()[1]); s.close()" 2^>nul`) do set "APP_BACKEND_PORT=%%P"\n'
            'if not defined APP_BACKEND_PORT set "APP_BACKEND_PORT=8000"\n'
        )

    ensure_command = f'"%PYEXE%" "{script_name}" --root "%CD%" --venv "{venv_dir}" --ensure-only 1>>"logs\\ensure.log" 2>>&1'

    bat_text = rf"""@echo off
setlocal ENABLEDELAYEDEXPANSION

REM =========================================
REM One-click install / run (stable)
REM Filename: run_app.bat (ASCII-only)
REM Generated by {script_name}
REM Project type: {project_type}
REM =========================================

pushd "%~dp0"
if not exist "logs" mkdir "logs" >nul 2>nul

echo =========================================
echo   One-click install / run (stable)
echo =========================================
echo.

echo [1/6] Checking Python...
where python >nul 2>&1
if errorlevel 1 (
  echo.
  echo [ERROR] Python not found. Please install Python 3.10+ first.
  echo Download: https://www.python.org/downloads/
  echo.
  pause
  popd
  exit /b 1
)

where node >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Node.js not found. Install Node.js 20 or newer: https://nodejs.org/
  pause
  popd
  exit /b 1
)
where npm >nul 2>&1
if errorlevel 1 (
  echo [ERROR] npm not found. Reinstall Node.js from https://nodejs.org/
  pause
  popd
  exit /b 1
)
where cargo >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Cargo not found. Install Rust stable: https://rustup.rs/
  pause
  popd
  exit /b 1
)

echo [2/6] Creating venv ({venv_dir}) if needed...
if not exist "{venv_dir}\Scripts\python.exe" (
  python -m venv "{venv_dir}" 1>>"logs\bootstrap.log" 2>>&1
  if errorlevel 1 (
    echo [WARN] Failed to create venv. Falling back to system Python.
  )
)

set "PYEXE=%~dp0{venv_dir}\Scripts\python.exe"
if not exist "%PYEXE%" set "PYEXE=python"

echo [3/6] Auto-fix + install dependencies...
{ensure_command}
if errorlevel 1 (
  echo [WARN] Auto-fix or install step reported issues. See logs\ensure.log.
) else (
  echo [OK] Auto-fix and install step completed.
)

{embedded_port_block}

echo [4/6] Starting backend or local app...
echo Start time: %DATE% %TIME%
{backend_start}{gui_start}
{frontend_start}{open_block}

echo.
echo =========================================
echo Startup flow finished.
echo Logs directory: %CD%\logs
echo Close Backend / Frontend / GUI windows to stop services.
echo =========================================
echo.
pause
popd
endlocal
"""
    out_path = root / "run_app.bat"
    write_text_ascii(out_path, bat_text)
    return out_path

def write_run_app_sh(root: Path, script_relpath: str, backend: dict,
                     frontend: FrontendInfo,
                     static_site: StaticSiteInfo,
                     cfg: Dict[str, str],
                     venv_dir: str,
                     filename: str = "run_app.sh",
                     det: Optional[DetectionResult] = None) -> Path:
    det = det or compose_detection_result(root, cfg, backend, frontend, static_site)
    mode = det.backend.get("mode", "none")
    local_py = det.local_py
    project_type = det.project_type
    has_runnable_frontend = det.frontend.exists or det.static_site.exists

    backend_host = (cfg.get("BACKEND_HOST", "").strip() or det.backend.get("host") or "127.0.0.1")
    backend_port = safe_int(cfg.get("BACKEND_PORT", "")) or det.backend.get("port") or 8000

    backend_start = ""
    gui_start = ""
    backend_url = ""
    frontend_url = ""

    if project_type == "embedded_gui_plus_backend" and mode != "none":
        backend_url = 'http://$APP_BACKEND_HOST:$APP_BACKEND_PORT'
        port_block = 'APP_BACKEND_HOST="127.0.0.1"\nAPP_BACKEND_PORT="$("$PYEXE" -c \'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()\' 2>/dev/null)"\n[ -n "$APP_BACKEND_PORT" ] || APP_BACKEND_PORT="8000"\n'
        if mode == "script":
            rel_script = str(det.backend.get("rel_script", "")).replace("\\", "/")
            backend_start = port_block + f'APP_BACKEND_HOST="$APP_BACKEND_HOST" APP_BACKEND_PORT="$APP_BACKEND_PORT" "$PYEXE" "$ROOT/{rel_script}" >>"$ROOT/logs/backend.log" 2>&1 &\nBACKEND_PID=$!\n'
        elif mode == "uvicorn":
            target = det.backend.get("target", "")
            backend_start = port_block + f'APP_BACKEND_HOST="$APP_BACKEND_HOST" APP_BACKEND_PORT="$APP_BACKEND_PORT" "$PYEXE" -m uvicorn {target} --host "$APP_BACKEND_HOST" --port "$APP_BACKEND_PORT" --log-level info >>"$ROOT/logs/backend.log" 2>&1 &\nBACKEND_PID=$!\n'
        elif mode == "module":
            module_name = det.backend.get("module", "")
            py_prefix = 'PYTHONPATH="$ROOT/src:$ROOT:${PYTHONPATH:-}" ' if needs_src_pythonpath_fix(root, module_name) else ""
            backend_start = port_block + f'APP_BACKEND_HOST="$APP_BACKEND_HOST" APP_BACKEND_PORT="$APP_BACKEND_PORT" {py_prefix}"$PYEXE" -m {module_name} >>"$ROOT/logs/backend.log" 2>&1 &\nBACKEND_PID=$!\n'
        elif mode == "streamlit":
            entry = str(det.backend.get("file", "")).replace("\\", "/")
            backend_start = port_block + f'PYTHONPATH="$ROOT/src:$ROOT:${{PYTHONPATH:-}}" APP_BACKEND_HOST="$APP_BACKEND_HOST" APP_BACKEND_PORT="$APP_BACKEND_PORT" "$PYEXE" -m streamlit run "$ROOT/{entry}" >>"$ROOT/logs/backend.log" 2>&1 &\nBACKEND_PID=$!\n'
        elif mode == "node":
            backend_dir = str(det.backend.get("dir", ".")).replace("\\", "/")
            install_cmd = det.backend.get("install_cmd", "npm install")
            run_cmd = det.backend.get("run_cmd", "npm run dev")
            backend_start = port_block + f"""start_backend() {{
  if ! cd "$ROOT/{backend_dir}"; then
    printf '[WARN] Backend cd failed: %s\\n' "$ROOT/{backend_dir}" >>"$ROOT/logs/backend.log"
    return 1
  fi
  if ! {install_cmd} >>"$ROOT/logs/backend.log" 2>&1; then
    printf '[WARN] Backend install failed in %s\\n' "$ROOT/{backend_dir}" >>"$ROOT/logs/backend.log"
  fi
  APP_BACKEND_HOST="$APP_BACKEND_HOST" APP_BACKEND_PORT="$APP_BACKEND_PORT" BACKEND_HOST="$APP_BACKEND_HOST" BACKEND_PORT="$APP_BACKEND_PORT" HOST="$APP_BACKEND_HOST" PORT="$APP_BACKEND_PORT" {run_cmd} >>"$ROOT/logs/backend.log" 2>&1
}}
start_backend &
BACKEND_PID=$!
"""

        if local_py.exists and local_py.entry:
            py_prefix = shell_pythonpath_prefix(root, local_py.import_root)
            if local_py.module_target:
                gui_start = f'APP_BACKEND_HOST="$APP_BACKEND_HOST" APP_BACKEND_PORT="$APP_BACKEND_PORT" {py_prefix}"$PYEXE" -m {local_py.module_target} >>"$ROOT/logs/gui.log" 2>&1 &\nGUI_PID=$!\n'
            else:
                gui_rel = norm_rel(root, local_py.entry).replace("\\", "/")
                gui_start = f'APP_BACKEND_HOST="$APP_BACKEND_HOST" APP_BACKEND_PORT="$APP_BACKEND_PORT" {py_prefix}"$PYEXE" "$ROOT/{gui_rel}" >>"$ROOT/logs/gui.log" 2>&1 &\nGUI_PID=$!\n'
    elif mode == "script":
        rel_script = str(det.backend.get("rel_script", "")).replace("\\", "/")
        backend_start = f'"$PYEXE" "$ROOT/{rel_script}" >>"$ROOT/logs/backend.log" 2>&1 &\nBACKEND_PID=$!\n'
        backend_url = f"http://{backend_host}:{backend_port}"
    elif mode == "uvicorn":
        target = det.backend.get("target", "")
        backend_start = f'"$PYEXE" -m uvicorn {target} --host {backend_host} --port {backend_port} --log-level info >>"$ROOT/logs/backend.log" 2>&1 &\nBACKEND_PID=$!\n'
        backend_url = f"http://{backend_host}:{backend_port}"
    elif mode == "streamlit":
        entry = str(det.backend.get("file", "")).replace("\\", "/")
        backend_start = f'PYTHONPATH="$ROOT/src:$ROOT:${{PYTHONPATH:-}}" "$PYEXE" -m streamlit run "$ROOT/{entry}" >>"$ROOT/logs/backend.log" 2>&1 &\nBACKEND_PID=$!\n'
    elif mode == "node":
        backend_dir = str(det.backend.get("dir", ".")).replace("\\", "/")
        install_cmd = det.backend.get("install_cmd", "npm install")
        run_cmd = det.backend.get("run_cmd", "npm run dev")
        backend_start = f"""start_backend() {{
  if ! cd "$ROOT/{backend_dir}"; then
    printf '[WARN] Backend cd failed: %s\\n' "$ROOT/{backend_dir}" >>"$ROOT/logs/backend.log"
    return 1
  fi
  if ! {install_cmd} >>"$ROOT/logs/backend.log" 2>&1; then
    printf '[WARN] Backend install failed in %s\\n' "$ROOT/{backend_dir}" >>"$ROOT/logs/backend.log"
  fi
  APP_BACKEND_HOST="{backend_host}" APP_BACKEND_PORT="{backend_port}" BACKEND_HOST="{backend_host}" BACKEND_PORT="{backend_port}" HOST="{backend_host}" PORT="{backend_port}" {run_cmd} >>"$ROOT/logs/backend.log" 2>&1
}}
start_backend &
BACKEND_PID=$!
"""
        backend_url = f"http://{backend_host}:{backend_port}"
    elif mode == "module":
        module_name = det.backend.get("module", "")
        py_prefix = 'PYTHONPATH="$ROOT/src:$ROOT:${PYTHONPATH:-}" ' if needs_src_pythonpath_fix(root, module_name) else ""
        backend_start = f'{py_prefix}"$PYEXE" -m {module_name} >>"$ROOT/logs/backend.log" 2>&1 &\nBACKEND_PID=$!\n'
    elif local_py.exists and local_py.entry:
        py_prefix = shell_pythonpath_prefix(root, local_py.import_root)
        if local_py.module_target:
            backend_start = f'{py_prefix}"$PYEXE" -m {local_py.module_target} >>"$ROOT/logs/local_python.log" 2>&1 &\nAPP_PID=$!\n'
        else:
            app_rel = norm_rel(root, local_py.entry).replace("\\", "/")
            backend_start = f'{py_prefix}"$PYEXE" "$ROOT/{app_rel}" >>"$ROOT/logs/local_python.log" 2>&1 &\nAPP_PID=$!\n'
    elif has_runnable_frontend:
        backend_start = 'echo "[INFO] Backend not detected. Skipping backend startup."\n'
    else:
        backend_start = 'echo "[WARN] No runnable backend, local Python entry, or static site was detected."\n'

    if det.frontend.exists:
        fe_dir = str(det.frontend.dir).replace("\\", "/")
        if det.frontend.pm == "npm":
            fe_install = "npm install"
            fe_run = det.frontend.direct_cmd_for_bat or f"npm run {det.frontend.script}"
        elif det.frontend.pm == "pnpm":
            fe_install = "pnpm install"
            fe_run = f"pnpm {det.frontend.script}"
        else:
            fe_install = "yarn install"
            fe_run = f"yarn {det.frontend.script}"
        frontend_block = f"""start_frontend() {{
  if ! cd "$ROOT/{fe_dir}"; then
    printf '[WARN] Frontend cd failed: %s\\n' "$ROOT/{fe_dir}" >>"$ROOT/logs/frontend.log"
    return 1
  fi
  if ! {fe_install} >>"$ROOT/logs/frontend.log" 2>&1; then
    printf '[WARN] Frontend install failed in %s\\n' "$ROOT/{fe_dir}" >>"$ROOT/logs/frontend.log"
  fi
  if ! {fe_run} >>"$ROOT/logs/frontend.log" 2>&1; then
    printf '[WARN] Frontend start failed in %s\\n' "$ROOT/{fe_dir}" >>"$ROOT/logs/frontend.log"
  fi
}}
start_frontend &
FRONTEND_PID=$!
"""
        if det.frontend.port:
            frontend_url = f"http://{det.frontend.host or '127.0.0.1'}:{det.frontend.port}"
    elif det.static_site.exists:
        static_dir = str(det.static_site.dir).replace("\\", "/")
        frontend_block = f"""start_frontend() {{
  if ! cd "$ROOT/{static_dir}"; then
    printf '[WARN] Static frontend cd failed: %s\\n' "$ROOT/{static_dir}" >>"$ROOT/logs/frontend.log"
    return 1
  fi
  if ! "$PYEXE" -m http.server {det.static_site.port} --bind {det.static_site.host} >>"$ROOT/logs/frontend.log" 2>&1; then
    printf '[WARN] Static frontend server failed in %s\\n' "$ROOT/{static_dir}" >>"$ROOT/logs/frontend.log"
  fi
}}
start_frontend &
FRONTEND_PID=$!
"""
        frontend_url = f"http://{det.static_site.host}:{det.static_site.port}"
    else:
        frontend_block = 'echo "[INFO] Frontend not detected. Skipping frontend startup."\n'

    open_block = "sleep 1\n"
    if frontend_url:
        open_block += f'if "$PYEXE" "$ROOT/scripts/wait_for_http.py" "{frontend_url}/api/health" 30 "lingxi-summary-lab" >>"$ROOT/logs/launcher.log" 2>&1; then\n'
        open_block += f'  open_url "{frontend_url}" "Summary Lab"\n'
        open_block += 'else\n  echo "[ERROR] Summary Lab did not become ready. See logs/frontend.log and logs/launcher.log."\nfi\n'
    elif backend_url:
        open_block += f'probe_url "{backend_url}" "Backend"\nopen_url "{backend_url}" "Backend"\n'
    else:
        open_block += 'echo "[ERROR] No local app URL was detected."\n'

    launcher_banner = ""
    if filename == "run_app.command":
        launcher_banner = """echo "[INFO] macOS launcher mode (.command)."
if [ "$OS_NAME" != "Darwin" ]; then
  echo "[WARN] This launcher is intended for macOS. On Linux, use ./run_app.sh instead."
fi
echo "[INFO] If macOS blocks this script the first time, open System Settings > Privacy & Security and allow it, then rerun."
printf '[INFO] macOS launcher started via run_app.command\\n' >>"$ROOT/logs/launcher.log"
echo
"""
    elif filename == "run_app.sh":
        launcher_banner = """echo "[INFO] POSIX shell launcher mode (.sh)."
if [ "$OS_NAME" = "Darwin" ]; then
  echo "[INFO] On macOS, you can also use run_app.command for Finder double-click."
fi
printf '[INFO] POSIX launcher started via run_app.sh\\n' >>"$ROOT/logs/launcher.log"
echo
"""

    sh_text = f"""#!/usr/bin/env bash
set -u

ROOT="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"
cd "$ROOT"

echo "========================================="
echo "  One-click install / run (stable)"
echo "========================================="
echo

PY=""
if command -v python3 >/dev/null 2>&1; then PY="python3"; fi
if [ -z "$PY" ] && command -v python >/dev/null 2>&1; then PY="python"; fi
if [ -z "$PY" ]; then
  echo "[ERROR] Python not found. Please install Python 3.10+ first."
  printf "Press Enter to close..."
  read -r _
  exit 1
fi

if ! command -v node >/dev/null 2>&1; then
  echo "[ERROR] Node.js not found. Install Node.js 20 or newer: https://nodejs.org/"
  exit 1
fi
if ! command -v npm >/dev/null 2>&1; then
  echo "[ERROR] npm not found. Reinstall Node.js from https://nodejs.org/"
  exit 1
fi
if ! command -v cargo >/dev/null 2>&1; then
  echo "[ERROR] Cargo not found. Install Rust stable: https://rustup.rs/"
  exit 1
fi

mkdir -p "$ROOT/logs"
OS_NAME="$(uname -s 2>/dev/null || echo unknown)"
IS_WSL="0"
if [ -n "${{WSL_INTEROP:-}}" ] || grep -qi microsoft /proc/sys/kernel/osrelease 2>/dev/null; then
  IS_WSL="1"
fi
{launcher_banner}

log_warn() {{
  printf '[WARN] %s\\n' "$1"
}}

open_url() {{
  url="$1"
  label="${{2:-URL}}"
  if [ -z "$url" ]; then
    return 0
  fi
  if [ "$IS_WSL" = "1" ]; then
    if command -v powershell.exe >/dev/null 2>&1; then
      powershell.exe -NoProfile -Command "Start-Process '$url'" >>"$ROOT/logs/open.log" 2>&1 && echo "[INFO] Opened $label in Windows browser via powershell.exe." && return 0
    fi
    if command -v cmd.exe >/dev/null 2>&1; then
      cmd.exe /C start "" "$url" >>"$ROOT/logs/open.log" 2>&1 && echo "[INFO] Opened $label in Windows browser via cmd.exe." && return 0
    fi
  fi
  if [ "$OS_NAME" = "Darwin" ] && command -v open >/dev/null 2>&1; then
    open "$url" >>"$ROOT/logs/open.log" 2>&1 && echo "[INFO] Opened $label via macOS open." && return 0
  fi
  if command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$url" >>"$ROOT/logs/open.log" 2>&1 && echo "[INFO] Opened $label via xdg-open." && return 0
  fi
  "$PYEXE" -m webbrowser "$url" >>"$ROOT/logs/open.log" 2>&1 && echo "[INFO] Opened $label via python -m webbrowser." && return 0
  printf '[WARN] Failed to open %s automatically: %s\\n' "$label" "$url" >>"$ROOT/logs/open.log"
  echo "[WARN] Failed to open $label automatically. Please open this URL manually: $url"
  return 1
}}

probe_url() {{
  url="$1"
  label="${{2:-URL}}"
  if [ -z "$url" ]; then
    return 0
  fi
  if "$PYEXE" - "$url" "$label" >>"$ROOT/logs/open.log" 2>&1 <<'PY'
import sys
import time
import urllib.request

url = sys.argv[1]
label = sys.argv[2]
deadline = time.time() + 4.0
last_error = None
while time.time() < deadline:
    try:
        with urllib.request.urlopen(url, timeout=1.5) as response:
            status = getattr(response, "status", "unknown")
            print(f"[READY] {{label}} {{url}} status={{status}}")
            raise SystemExit(0)
    except Exception as exc:  # pragma: no cover - emitted into shell log
        last_error = exc
        time.sleep(0.5)

print(f"[WARN] {{label}} not ready before browser open: {{url}} last_error={{last_error}}")
raise SystemExit(1)
PY
  then
    echo "[INFO] $label responded before browser open: $url"
    return 0
  fi
  echo "[WARN] $label may not be ready yet. Browser will still open: $url"
  return 1
}}

VENV_DIR="{venv_dir}"
if [ ! -x "$ROOT/$VENV_DIR/bin/python" ]; then
  echo "[1/6] Creating venv ($VENV_DIR) if needed..."
  "$PY" -m venv "$ROOT/$VENV_DIR" >>"$ROOT/logs/bootstrap.log" 2>&1 || log_warn "Failed to create venv. Falling back to system Python."
fi

PYEXE="$ROOT/$VENV_DIR/bin/python"
if [ ! -x "$PYEXE" ]; then
  PYEXE="$PY"
fi

echo "[2/6] Auto-fix + install dependencies..."
"$PYEXE" "{script_relpath}" --root "$ROOT" --venv "{venv_dir}" --ensure-only >>"$ROOT/logs/ensure.log" 2>&1 || log_warn "Auto-fix or install step reported issues. See logs/ensure.log."

BACKEND_PID=""
FRONTEND_PID=""
APP_PID=""
GUI_PID=""
cleanup() {{
  set +e
  [ -n "${{GUI_PID:-}}" ] && kill "${{GUI_PID}}" >/dev/null 2>&1 || true
  [ -n "${{FRONTEND_PID:-}}" ] && kill "${{FRONTEND_PID}}" >/dev/null 2>&1 || true
  [ -n "${{BACKEND_PID:-}}" ] && kill "${{BACKEND_PID}}" >/dev/null 2>&1 || true
  [ -n "${{APP_PID:-}}" ] && kill "${{APP_PID}}" >/dev/null 2>&1 || true
}}
trap cleanup EXIT INT TERM

echo "[3/6] Starting backend or local app..."
{backend_start}{gui_start}

echo "[4/6] Starting frontend (if any)..."
{frontend_block}

echo "[5/6] Opening browser URLs..."
{open_block}

echo "[6/6] Logs directory: $ROOT/logs"
if [ -z "${{BACKEND_PID:-}}${{FRONTEND_PID:-}}${{APP_PID:-}}${{GUI_PID:-}}" ]; then
  echo "[WARN] No process was started. Check logs for details."
  printf "Press Enter to close..."
  read -r _
  exit 0
fi

echo "Started. Press Ctrl+C to stop."
wait
printf "Press Enter to close..."
read -r _
"""
    out_path = root / filename
    write_text_utf8_lf(out_path, sh_text)
    return out_path

def write_run_app_command(root: Path, script_relpath: str, backend: dict,
                          frontend: FrontendInfo,
                          static_site: StaticSiteInfo,
                          cfg: Dict[str, str],
                          venv_dir: str,
                          det: Optional[DetectionResult] = None) -> Path:
    # macOS Finder can double-click a .command file (still may require chmod +x after unzip).
    return write_run_app_sh(
        root=root,
        script_relpath=script_relpath,
        backend=backend,
        frontend=frontend,
        static_site=static_site,
        cfg=cfg,
        venv_dir=venv_dir,
        filename="run_app.command",
        det=det,
    )


def ensure_posix_launcher_executable(path: Path) -> None:
    try:
        path.chmod(path.stat().st_mode | 0o755)
    except Exception:
        pass

PACKAGE_EXCLUDE_DIRS = {".venv", "venv", "__pycache__", ".git", ".idea", ".vscode", "node_modules"}
PACKAGE_EXCLUDE_FILES = {
    ".env",
    ".env.local",
    ".env.development",
    ".env.production",
    ".launcher.env",
    "Thumbs.db",
    ".DS_Store",
}

def should_exclude_from_package(rel_path: Path) -> bool:
    if any(part in PACKAGE_EXCLUDE_DIRS for part in rel_path.parts):
        return True
    if rel_path.name in PACKAGE_EXCLUDE_FILES:
        return True
    if rel_path.name.endswith(".bak"):
        return True
    if rel_path.parts and rel_path.parts[0].lower() == "logs":
        return True
    if rel_path.suffix.lower() == ".zip" and rel_path.parts and rel_path.parts[0].lower() == "release":
        return True
    return False

def package_project_zip(root: Path, out_zip: Path) -> Path:
    import zipfile

    out_zip.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in root.rglob("*"):
            if p.is_dir():
                continue
            rel = p.relative_to(root)
            if should_exclude_from_package(rel):
                continue
            arc = str(rel).replace(os.sep, "/")
            zi = zipfile.ZipInfo(arc)
            # Preserve executable bit for Unix launchers if possible.
            if arc in {"run_app.sh", "run_app.command"}:
                zi.external_attr = (0o755 & 0xFFFF) << 16
            else:
                zi.external_attr = (0o644 & 0xFFFF) << 16
            with p.open("rb") as f:
                zf.writestr(zi, f.read())

    return out_zip

# ============================================================
# Full auto pipeline
# ============================================================

def full_auto(root: Path, venv_dir: str, ensure_only: bool = False) -> Tuple[int, str]:
    messages: List[str] = []
    hard_fail = False

    try:
        cfg = get_launcher_config(root)
    except Exception as exc:
        cfg = {}
        messages.append(f"[WARN] load_launcher_config failed: {exc!r}")

    relative_import_actions: List[str] = []
    try:
        relative_import_actions = apply_relative_import_fixes(root)
        for action in relative_import_actions:
            messages.append(f"[FIX] {action}")
    except Exception as exc:
        messages.append(f"[WARN] relative import auto-fix failed: {exc!r}")

    imported_modules: Set[str] = set()
    try:
        scan = scan_imports(root)
        if scan.syntax_errors:
            warn_lines = "\n".join(f"- {norm_rel(root, path)}: {msg}" for path, msg in scan.syntax_errors[:20])
            messages.append("[WARN] Some files could not be parsed while scanning imports:\n" + warn_lines)
        imported_modules = {item.module for item in scan.imports if item.module}
    except Exception as exc:
        messages.append(f"[WARN] import scan failed: {exc!r}")

    req_path = root / "requirements.txt"
    try:
        _, req_actions = ensure_requirements_minimal(root, req_path, imported_modules)
        if req_actions:
            messages.extend(f"[INFO] {action}" for action in req_actions)
        else:
            messages.append("[INFO] requirements.txt did not need changes.")
    except Exception as exc:
        messages.append(f"[WARN] requirements repair failed: {exc!r}")

    venv_ready = False
    try:
        ensure_venv(root, venv_dir)
        venv_ready = True
    except Exception as exc:
        messages.append(f"[WARN] venv creation failed: {exc!r}")

    pip_output = ""
    pip_check_output = ""
    import_failures: List[str] = []
    local_names = detect_local_toplevel(root)

    install_requirements = parse_requirements(req_path) if req_path.is_file() else RequirementsInfo()
    has_python_dependencies = bool(install_requirements.packages or install_requirements.directive_lines)

    if venv_ready and req_path.is_file() and has_python_dependencies:
        vp = venv_python(root, venv_dir)
        rc, upgrade_out = run_cmd([str(vp), "-m", "pip", "install", "--upgrade", "pip"], cwd=root)
        if rc != 0:
            messages.append("[WARN] pip upgrade failed.")
            if upgrade_out.strip():
                messages.append(upgrade_out.strip())

        pip_rc, pip_output = run_cmd([str(vp), "-m", "pip", "install", "-r", str(req_path)], cwd=root)
        if pip_rc != 0 and try_autofix_no_match(req_path, pip_output, local_names):
            messages.append("[FIX] Auto-commented a local package name from requirements.txt and retried pip install.")
            pip_rc, pip_output = run_cmd([str(vp), "-m", "pip", "install", "-r", str(req_path)], cwd=root)
        if pip_rc != 0:
            messages.append("[WARN] pip install -r requirements.txt failed.")
            if pip_output.strip():
                messages.append(pip_output.strip())

        check_rc, pip_check_output = run_cmd([str(vp), "-m", "pip", "check"], cwd=root)
        if check_rc != 0:
            messages.append("[WARN] pip check reported issues.")
            if pip_check_output.strip():
                messages.append(pip_check_output.strip())

        try:
            import_test_third_party(root, venv_dir, imported_modules)
        except Exception as exc:
            import_failures = [line for line in str(exc).splitlines() if line.strip()]
            messages.append("[WARN] import smoke test reported issues.")
            messages.extend(import_failures[:50])
    elif venv_ready and req_path.is_file():
        messages.append("[INFO] requirements.txt has no Python packages; skipped network-dependent pip install.")
    else:
        messages.append("[WARN] venv/pip steps were incomplete, but launcher generation will continue.")

    try:
        det = detect_project(root, cfg)
    except Exception as exc:
        messages.append(f"[WARN] detect_project failed: {exc!r}")
        det = compose_detection_result(root, cfg, {"mode": "none"}, FrontendInfo(exists=False), StaticSiteInfo(exists=False))

    try:
        seeded_runtime = seed_apsm_runtime(root, det, cfg)
        if seeded_runtime:
            messages.append(f"- apsm runtime seeded: {', '.join(seeded_runtime)}")
    except Exception as exc:
        messages.append(f"[WARN] APSM runtime seed failed: {exc!r}")

    out_bat = root / "run_app.bat"
    out_sh = root / "run_app.sh"
    out_cmd = root / "run_app.command"
    if not ensure_only:
        script_rel = script_relpath_from_root(root)
        script_rel_bat = script_rel.replace("/", "\\")
        script_rel_posix = script_rel.replace("\\", "/")
        try:
            out_bat = write_run_app_bat(root, script_rel_bat, det.backend, det.frontend, det.static_site, cfg, venv_dir, det=det)
            out_sh = write_run_app_sh(root, script_rel_posix, det.backend, det.frontend, det.static_site, cfg, venv_dir, filename="run_app.sh", det=det)
            out_cmd = write_run_app_command(root, script_rel_posix, det.backend, det.frontend, det.static_site, cfg, venv_dir, det=det)
            ensure_posix_launcher_executable(out_sh)
            ensure_posix_launcher_executable(out_cmd)
        except Exception as exc:
            hard_fail = True
            messages.append(f"[ERROR] launcher generation failed: {exc!r}")
    else:
        messages.append("[INFO] ensure-only mode: launcher generation skipped.")

    detected_backend_host = cfg.get("BACKEND_HOST", "").strip() or det.backend.get("host") or "127.0.0.1"
    detected_backend_port = safe_int(cfg.get("BACKEND_PORT", "")) or det.backend.get("port") or 8000
    messages.append(f"- detected project type: {det.project_type}")
    messages.append(f"- backend mode: {det.backend.get('mode', 'none')}")
    if det.backend.get("mode") == "script":
        messages.append(f"- backend script: {det.backend.get('rel_script')}")
    if det.backend.get("mode") == "uvicorn":
        messages.append(f"- uvicorn target: {det.backend.get('target')}")
    if det.backend.get("mode") == "node":
        messages.append(
            f"- node backend: dir={det.backend.get('dir')}, pm={det.backend.get('pm')}, script={det.backend.get('script')}"
        )
    if det.backend.get("mode") == "module":
        messages.append(f"- python module: {det.backend.get('module')}")
    if det.backend.get("mode") != "none":
        messages.append(
            f"- backend host/port: {detected_backend_host}:{detected_backend_port} "
            "(目前為設定值或推得值，非執行後回讀的最終 port)"
        )
    if det.frontend.exists:
        messages.append(f"- frontend: dir={det.frontend.dir}, pm={det.frontend.pm}, script={det.frontend.script}")
        messages.append(f"- frontend host/port: {det.frontend.host}:{det.frontend.port} (source={det.frontend.port_source})")
        messages.append("- frontend host/port 來源包含根目錄 .env / .launcher.env / scripts/.env / 環境變數與前端設定，非執行後回讀。")
    if det.local_py.exists and det.local_py.entry:
        messages.append(f"- local python entry: {norm_rel(root, det.local_py.entry)} (gui_like={det.local_py.is_gui_like})")
    if det.static_site.exists:
        messages.append(f"- static site: dir={det.static_site.dir}, host/port={det.static_site.host}:{det.static_site.port}")
    if relative_import_actions:
        messages.append(f"- relative import fixes: {len(relative_import_actions)}")
    if not ensure_only:
        messages.append(f"- output files: {out_bat}, {out_sh}, {out_cmd}")

    if ensure_only:
        failures: List[str] = []
        if not venv_ready:
            failures.append("venv creation failed")
        if req_path.is_file() and pip_output and "failed" in "\n".join(messages).lower():
            failures.append("dependency install or validation reported warnings")
        if not (det.backend.get("mode") != "none" or det.frontend.exists or det.local_py.exists or det.static_site.exists):
            failures.append("no runnable target detected")
        return (1 if failures else 0), "\n".join(messages + ([ "", "[ENSURE-ONLY] FAILURES:" ] + [f" - {item}" for item in failures] if failures else []))

    return (1 if hard_fail else 0), "\n".join(messages)

def generate_launch_scripts(root: Path, venv_dir: str) -> Tuple[Path, Path, Path]:
    cfg = get_launcher_config(root)
    det = detect_project(root, cfg)
    seed_apsm_runtime(root, det, cfg)

    script_rel = script_relpath_from_root(root)
    script_rel_bat = script_rel.replace("/", "\\")
    script_rel_posix = script_rel.replace("\\", "/")

    out_bat = write_run_app_bat(root, script_rel_bat, det.backend, det.frontend, det.static_site, cfg, venv_dir, det=det)
    out_sh = write_run_app_sh(root, script_rel_posix, det.backend, det.frontend, det.static_site, cfg, venv_dir, filename="run_app.sh", det=det)
    out_cmd = write_run_app_command(root, script_rel_posix, det.backend, det.frontend, det.static_site, cfg, venv_dir, det=det)
    ensure_posix_launcher_executable(out_sh)
    ensure_posix_launcher_executable(out_cmd)
    return out_bat, out_sh, out_cmd

# ============================================================
# CLI
# ============================================================

def main() -> int:
    ap = argparse.ArgumentParser(description="All-in-one project launcher (infer entrypoints and configured/default ports; generate launcher scripts).")
    ap.add_argument("--root", type=str, default=".", help="專案根目錄（預設目前資料夾）")
    ap.add_argument("--venv", type=str, default=DEFAULT_VENV_DIR, help="venv 資料夾（預設 .venv）")
    ap.add_argument("--ensure-only", action="store_true", help="只做自動修正/依賴安裝檢查，不產生 launcher")
    ap.add_argument("--package", action="store_true", help="產出 release ZIP（只生成啟動腳本 + 打包；不跑 pip install）")
    ap.add_argument("--package-out", type=str, default="", help="ZIP 輸出路徑（預設：<root>/release/<rootname>.zip）")
    args = ap.parse_args()

    root = Path(args.root).resolve()

    if args.package:
        try:
            out_bat, out_sh, out_cmd = generate_launch_scripts(root, args.venv)
        except Exception as e:
            print(f"[ERROR] 生成啟動腳本失敗：{e}")
            return 1

        if args.package_out:
            out_zip = Path(args.package_out).expanduser().resolve()
        else:
            out_zip = (root / "release" / f"{root.name}.zip").resolve()
        if out_zip.exists():
            from datetime import datetime
            out_zip = out_zip.with_name(f"{out_zip.stem}-{datetime.now().strftime('%Y%m%d-%H%M%S')}{out_zip.suffix}")

        try:
            out_zip = package_project_zip(root, out_zip)
        except Exception as e:
            print(f"[ERROR] 打包 ZIP 失敗：{e}")
            return 1

        print("OK：已生成啟動腳本並完成打包。")
        print(f"- run_app.bat: {out_bat}")
        print(f"- run_app.sh: {out_sh}")
        print(f"- run_app.command: {out_cmd}")
        print(f"- zip: {out_zip}")
        return 0

    code, msg = full_auto(root, args.venv, ensure_only=args.ensure_only)
    print(msg)
    return code

if __name__ == "__main__":
    raise SystemExit(main())
