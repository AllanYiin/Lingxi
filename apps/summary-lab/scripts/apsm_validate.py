#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

APSM_VERSION = "1.0"
VALID_USAGE_SCENES = {
    "scene_a_personal_blackbox",
    "scene_b_shared_tool",
    "scene_c_internal_tool",
    "scene_d_engineer_maintained",
}
VALID_PROFILE_VALUES: Dict[str, Set[str]] = {
    "user_type": {"personal", "small_team", "internal_team", "engineer_maintained"},
    "usage_duration": {"one_off", "occasional", "long_term"},
    "change_frequency": {"rare", "occasional", "continuous"},
    "failure_cost": {"manual_redo_ok", "multi_user_disruption", "workflow_blocking"},
}

VALID_COMBINATIONS: Set[Tuple[str, str, str]] = {
    ("separated", "node_spa", "python_api"),
    ("separated", "node_spa", "node_api"),
    ("separated", "node_ssr", "python_api"),
    ("separated", "node_ssr", "node_api"),
    ("monorepo", "node_spa", "python_api"),
    ("monorepo", "node_spa", "node_api"),
    ("monorepo", "node_ssr", "python_api"),
    ("monorepo", "node_ssr", "node_api"),
    ("single_service", "python_templates", "python_api"),
    ("single_service", "node_ssr", "node_api"),
    ("single_service", "none", "python_api"),
    ("single_service", "none", "node_api"),
}


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def load_env(path: Path) -> Dict[str, str]:
    env: Dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        env[key.strip()] = value.strip()
    return env


def add_issue(issues: List[Dict[str, str]], code: str, path: str, message: str) -> None:
    issues.append({"code": code, "path": path, "message": message})


def normalize_config(raw: Dict[str, Any]) -> Dict[str, Any]:
    project_profile = raw.get("project_profile", {})
    if not isinstance(project_profile, dict):
        project_profile = {}

    return {
        "name": str(raw.get("name", "")).strip(),
        "apsm_version": str(raw.get("apsm_version", "")).strip(),
        "architecture": str(raw.get("architecture", "")).strip(),
        "frontend": str(raw.get("frontend", "")).strip(),
        "backend": str(raw.get("backend", "")).strip(),
        "version": str(raw.get("version", "")).strip(),
        "archetype": str(raw.get("archetype", "")).strip(),
        "usage_scene": str(raw.get("usage_scene", "")).strip(),
        "project_profile": {str(key): str(value).strip() for key, value in project_profile.items()},
    }


def infer_archetype(cfg: Dict[str, str]) -> str:
    if cfg["architecture"] == "separated":
        return "web_app"
    if cfg["architecture"] == "monorepo":
        return "monorepo"
    if cfg["architecture"] == "single_service" and cfg["frontend"] == "python_templates":
        return "python_fullstack"
    if cfg["architecture"] == "single_service" and cfg["frontend"] == "node_ssr":
        return "fullstack_app"
    if cfg["architecture"] == "single_service" and cfg["frontend"] == "none":
        return "service_api"
    return ""


def infer_usage_scene(project_profile: Dict[str, str]) -> str:
    user_type = project_profile.get("user_type", "")
    usage_duration = project_profile.get("usage_duration", "")
    change_frequency = project_profile.get("change_frequency", "")
    failure_cost = project_profile.get("failure_cost", "")

    if user_type == "engineer_maintained":
        return "scene_d_engineer_maintained"

    if usage_duration == "long_term" and (
        change_frequency == "continuous"
        or user_type == "internal_team"
        or failure_cost == "workflow_blocking"
    ):
        return "scene_c_internal_tool"

    if (
        user_type == "personal"
        and usage_duration in {"one_off", "occasional"}
        and change_frequency in {"rare", "occasional"}
        and failure_cost == "manual_redo_ok"
    ):
        return "scene_a_personal_blackbox"

    return "scene_b_shared_tool"


def validate_config(cfg: Dict[str, Any], errors: List[Dict[str, str]], warnings: List[Dict[str, str]]) -> None:
    for key in ("name", "apsm_version", "architecture", "frontend", "backend", "version"):
        if not cfg.get(key):
            add_issue(errors, "missing_config_field", "project.config.json", f"Missing required field: {key}")

    usage_scene = str(cfg.get("usage_scene", "")).strip()
    project_profile = cfg.get("project_profile", {})
    if not usage_scene:
        add_issue(
            warnings,
            "missing_usage_scene",
            "project.config.json",
            "usage_scene is recommended; strict mode requires it",
        )
    elif usage_scene not in VALID_USAGE_SCENES:
        add_issue(
            errors,
            "invalid_usage_scene",
            "project.config.json",
            f"Unsupported usage_scene: {usage_scene!r}",
        )

    if not isinstance(project_profile, dict) or not project_profile:
        add_issue(
            warnings,
            "missing_project_profile",
            "project.config.json",
            "project_profile is recommended; strict mode requires it",
        )
    else:
        for key, valid_values in VALID_PROFILE_VALUES.items():
            value = str(project_profile.get(key, "")).strip()
            if not value:
                add_issue(
                    warnings,
                    "missing_project_profile_field",
                    "project.config.json",
                    f"project_profile is missing field: {key}",
                )
                continue
            if value not in valid_values:
                add_issue(
                    errors,
                    "invalid_project_profile_field",
                    "project.config.json",
                    f"project_profile.{key} has unsupported value: {value!r}",
                )

        if usage_scene and all(str(project_profile.get(key, "")).strip() for key in VALID_PROFILE_VALUES):
            expected_usage_scene = infer_usage_scene(project_profile)
            if usage_scene != expected_usage_scene:
                add_issue(
                    errors,
                    "invalid_usage_scene_mapping",
                    "project.config.json",
                    f"usage_scene should be {expected_usage_scene!r} for this project_profile, got {usage_scene!r}",
                )

    if cfg.get("apsm_version") and cfg["apsm_version"] != APSM_VERSION:
        add_issue(
            errors,
            "invalid_apsm_version",
            "project.config.json",
            f"Unsupported apsm_version: {cfg['apsm_version']!r}; expected {APSM_VERSION!r}",
        )

    combo = (cfg.get("architecture", ""), cfg.get("frontend", ""), cfg.get("backend", ""))
    if all(combo) and combo not in VALID_COMBINATIONS:
        add_issue(
            errors,
            "invalid_combination",
            "project.config.json",
            "Unsupported architecture/frontend/backend combination: "
            f"{cfg.get('architecture')}/{cfg.get('frontend')}/{cfg.get('backend')}",
        )

    expected_archetype = infer_archetype(cfg)
    if cfg.get("archetype"):
        if cfg["archetype"] != expected_archetype:
            add_issue(
                errors,
                "invalid_archetype",
                "project.config.json",
                f"archetype should be {expected_archetype!r} for this combination, got {cfg['archetype']!r}",
            )
    elif expected_archetype:
        add_issue(
            warnings,
            "missing_archetype",
            "project.config.json",
            f"archetype is recommended; expected {expected_archetype!r}",
        )


def required_env_keys(cfg: Dict[str, Any]) -> Set[str]:
    keys = {"BACKEND_HOST", "BACKEND_PORT"}
    if cfg["architecture"] in {"separated", "monorepo"} and cfg["frontend"] in {"node_spa", "node_ssr"}:
        keys.update({"FRONTEND_HOST", "FRONTEND_PORT", "API_BASE_URL"})
    return keys


def required_runtime_keys(cfg: Dict[str, Any]) -> Set[str]:
    keys: Set[str] = set()
    if cfg["backend"] != "none":
        keys.add("backend_port")
    if cfg["frontend"] in {"node_spa", "node_ssr"}:
        keys.add("frontend_port")
    return keys


def expected_layout(cfg: Dict[str, Any]) -> Dict[str, Set[str]]:
    files = {
        "project.config.json",
        ".env",
        ".env.example",
        "specs/requirements.md",
        "AGENTS.md",
        "requirements.txt",
        "run_app.bat",
        "run_app.command",
        "run_app.sh",
        "README.md",
        "todo.md",
        "scripts/project_launcher.py",
        "scripts/apsm_validate.py",
    }
    dirs = {"scripts", "specs"}
    optional_dirs = {".venv", ".runtime", "logs"}
    optional_files = {
        ".runtime/ports.json",
        ".runtime/launcher_state.json",
        "logs/launcher.log",
    }

    combo = (cfg["architecture"], cfg["frontend"], cfg["backend"])
    if cfg["backend"] != "none":
        optional_files.add("logs/backend.log")
    if cfg["frontend"] in {"node_spa", "node_ssr"}:
        optional_files.add("logs/frontend.log")

    if combo == ("separated", "node_spa", "python_api"):
        dirs.update({"backend", "backend/app", "frontend", "frontend/src", "frontend/public"})
        files.update({"backend/app/__main__.py", "backend/app/main.py", "frontend/package.json"})
    elif combo == ("separated", "node_spa", "node_api"):
        dirs.update({"backend", "backend/src", "frontend", "frontend/src", "frontend/public"})
        files.update({"backend/src/server.ts", "backend/package.json", "frontend/package.json"})
    elif combo == ("separated", "node_ssr", "python_api"):
        dirs.update({"backend", "backend/app", "frontend"})
        files.update({"backend/app/__main__.py", "backend/app/main.py", "frontend/package.json"})
    elif combo == ("separated", "node_ssr", "node_api"):
        dirs.update({"backend", "backend/src", "frontend"})
        files.update({"backend/src/server.ts", "backend/package.json", "frontend/package.json"})
    elif combo == ("monorepo", "node_spa", "python_api"):
        dirs.update({"src", "src/server", "src/server/app", "src/web", "src/web/src", "src/web/public"})
        files.update({"src/server/app/__main__.py", "src/server/app/main.py", "src/web/package.json"})
    elif combo == ("monorepo", "node_spa", "node_api"):
        dirs.update({"src", "src/server", "src/web", "src/web/src", "src/web/public"})
        files.update({"src/server/server.ts", "src/web/package.json"})
    elif combo == ("monorepo", "node_ssr", "python_api"):
        dirs.update({"src", "src/server", "src/server/app", "src/web"})
        files.update({"src/server/app/__main__.py", "src/server/app/main.py", "src/web/package.json"})
    elif combo == ("monorepo", "node_ssr", "node_api"):
        dirs.update({"src", "src/server", "src/web"})
        files.update({"src/server/server.ts", "src/web/package.json"})
    elif combo == ("single_service", "python_templates", "python_api"):
        dirs.update({"src", "src/app", "src/app/templates", "src/app/static"})
        files.update({"src/app/__main__.py", "src/app/main.py"})
    elif combo == ("single_service", "node_ssr", "node_api"):
        dirs.update({"src", "src/web"})
        files.update({"src/web/package.json"})
    elif combo == ("single_service", "none", "python_api"):
        dirs.update({"src", "src/app"})
        files.update({"src/app/__main__.py", "src/app/main.py"})
    elif combo == ("single_service", "none", "node_api"):
        dirs.update({"src", "src/app"})
        files.update({"src/app/package.json", "src/app/server.ts"})

    return {
        "files": files,
        "dirs": dirs,
        "optional_dirs": optional_dirs,
        "optional_files": optional_files,
    }


def validate_layout(project_root: Path, cfg: Dict[str, Any], errors: List[Dict[str, str]], warnings: List[Dict[str, str]]) -> None:
    layout = expected_layout(cfg)
    for rel_dir in sorted(layout["dirs"]):
        path = project_root / rel_dir
        if not path.exists() or not path.is_dir():
            add_issue(errors, "missing_dir", rel_dir, "Required directory is missing")

    for rel_file in sorted(layout["files"]):
        path = project_root / rel_file
        if not path.exists() or not path.is_file():
            add_issue(errors, "missing_file", rel_file, "Required file is missing")

    for rel_dir in sorted(layout["optional_dirs"]):
        path = project_root / rel_dir
        if not path.exists():
            add_issue(warnings, "missing_optional_dir", rel_dir, "Recommended directory is missing")

    for rel_file in sorted(layout["optional_files"]):
        path = project_root / rel_file
        if not path.exists():
            add_issue(warnings, "missing_optional_file", rel_file, "Recommended file is missing")


def validate_env(project_root: Path, cfg: Dict[str, str], errors: List[Dict[str, str]]) -> None:
    env_path = project_root / ".env"
    if not env_path.exists():
        add_issue(errors, "missing_file", ".env", "Environment file is missing")
        return

    env = load_env(env_path)
    for key in sorted(required_env_keys(cfg)):
        if key not in env:
            add_issue(errors, "missing_env_key", ".env", f"Missing required env key: {key}")

    for key in ("BACKEND_PORT", "FRONTEND_PORT"):
        if key not in env:
            continue
        try:
            if int(env[key]) <= 0:
                raise ValueError
        except ValueError:
            add_issue(errors, "invalid_port", ".env", f"{key} must be a positive integer")


def validate_runtime(project_root: Path, cfg: Dict[str, Any], errors: List[Dict[str, str]]) -> None:
    ports_path = project_root / ".runtime" / "ports.json"
    if ports_path.exists():
        try:
            ports_data = load_json(ports_path)
        except json.JSONDecodeError as exc:
            add_issue(errors, "invalid_json", ".runtime/ports.json", f"Invalid JSON: {exc}")
        else:
            for key in sorted(required_runtime_keys(cfg)):
                if key not in ports_data:
                    add_issue(errors, "missing_runtime_key", ".runtime/ports.json", f"Missing required runtime key: {key}")
                    continue
                if not isinstance(ports_data[key], int) or ports_data[key] <= 0:
                    add_issue(errors, "invalid_runtime_port", ".runtime/ports.json", f"{key} must be a positive integer")

    state_path = project_root / ".runtime" / "launcher_state.json"
    if state_path.exists():
        try:
            state_data = load_json(state_path)
        except json.JSONDecodeError as exc:
            add_issue(errors, "invalid_json", ".runtime/launcher_state.json", f"Invalid JSON: {exc}")
            return

        if not isinstance(state_data, dict):
            add_issue(errors, "invalid_runtime_state", ".runtime/launcher_state.json", "launcher_state.json must be a JSON object")
            return

        for key in ("status", "last_event", "updated_at"):
            value = state_data.get(key)
            if not isinstance(value, str) or not value.strip():
                add_issue(errors, "missing_runtime_key", ".runtime/launcher_state.json", f"Missing required runtime key: {key}")


def validate_package_scripts(project_root: Path, cfg: Dict[str, Any], errors: List[Dict[str, str]], warnings: List[Dict[str, str]]) -> None:
    package_paths: List[Tuple[str, Tuple[str, ...]]] = []
    combo = (cfg["architecture"], cfg["frontend"], cfg["backend"])

    if combo == ("separated", "node_spa", "python_api"):
        package_paths.append(("frontend/package.json", ("dev",)))
    elif combo == ("separated", "node_spa", "node_api"):
        package_paths.append(("backend/package.json", ("dev", "start")))
        package_paths.append(("frontend/package.json", ("dev",)))
    elif combo == ("separated", "node_ssr", "python_api"):
        package_paths.append(("frontend/package.json", ("dev", "start")))
    elif combo == ("separated", "node_ssr", "node_api"):
        package_paths.append(("backend/package.json", ("dev", "start")))
        package_paths.append(("frontend/package.json", ("dev", "start")))
    elif combo == ("monorepo", "node_spa", "python_api"):
        package_paths.append(("src/web/package.json", ("dev",)))
    elif combo == ("monorepo", "node_spa", "node_api"):
        package_paths.append(("src/web/package.json", ("dev",)))
    elif combo == ("monorepo", "node_ssr", "python_api"):
        package_paths.append(("src/web/package.json", ("dev", "start")))
    elif combo == ("monorepo", "node_ssr", "node_api"):
        package_paths.append(("src/server/package.json", ("dev", "start")))
        package_paths.append(("src/web/package.json", ("dev", "start")))
    elif combo == ("single_service", "node_ssr", "node_api"):
        package_paths.append(("src/web/package.json", ("dev", "start")))
    elif combo == ("single_service", "none", "node_api"):
        package_paths.append(("src/app/package.json", ("dev", "start")))

    for rel_path, script_names in package_paths:
        path = project_root / rel_path
        if not path.exists():
            continue
        try:
            data = load_json(path)
        except json.JSONDecodeError as exc:
            add_issue(errors, "invalid_json", rel_path, f"Invalid JSON: {exc}")
            continue

        scripts = data.get("scripts")
        if not isinstance(scripts, dict):
            add_issue(errors, "invalid_package_json", rel_path, "package.json is missing scripts object")
            continue

        for script_name in script_names:
            if not isinstance(scripts.get(script_name), str) or not scripts[script_name].strip():
                add_issue(errors, "missing_script", rel_path, f"package.json is missing scripts.{script_name}")

        if "scripts" in data and not any(name in scripts for name in ("dev", "start")):
            add_issue(warnings, "weak_package_scripts", rel_path, "No dev/start scripts found")


def validate_project(project_root: Path) -> Dict[str, Any]:
    errors: List[Dict[str, str]] = []
    warnings: List[Dict[str, str]] = []

    if not project_root.exists() or not project_root.is_dir():
        add_issue(errors, "missing_project_root", str(project_root), "Project root does not exist")
        return {"valid": False, "errors": errors, "warnings": warnings, "config": None}

    config_path = project_root / "project.config.json"
    if not config_path.exists():
        add_issue(errors, "missing_file", "project.config.json", "project.config.json is missing")
        return {"valid": False, "errors": errors, "warnings": warnings, "config": None}

    try:
        raw_cfg = load_json(config_path)
    except json.JSONDecodeError as exc:
        add_issue(errors, "invalid_json", "project.config.json", f"Invalid JSON: {exc}")
        return {"valid": False, "errors": errors, "warnings": warnings, "config": None}

    cfg = normalize_config(raw_cfg)
    validate_config(cfg, errors, warnings)
    if errors:
        return {"valid": False, "errors": errors, "warnings": warnings, "config": cfg}

    validate_layout(project_root, cfg, errors, warnings)
    validate_env(project_root, cfg, errors)
    validate_runtime(project_root, cfg, errors)
    validate_package_scripts(project_root, cfg, errors, warnings)

    return {"valid": len(errors) == 0, "errors": errors, "warnings": warnings, "config": cfg}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate a project against APSM directory rules")
    parser.add_argument("--project", required=True, help="Path to project root")
    parser.add_argument("--json", action="store_true", help="Output JSON result")
    parser.add_argument("--strict", action="store_true", help="Return non-zero when warnings exist")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = validate_project(Path(args.project).resolve())

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"Project: {Path(args.project).resolve()}")
        print(f"Valid: {'YES' if result['valid'] else 'NO'}")
        if result["config"]:
            cfg = result["config"]
            print(
                "Config: "
                f"{cfg['architecture']} / {cfg['frontend']} / {cfg['backend']} "
                f"(apsm_version={cfg['apsm_version']})"
            )
            if cfg.get("usage_scene"):
                print(f"Usage scene: {cfg['usage_scene']}")
        if result["errors"]:
            print("\nErrors:")
            for error in result["errors"]:
                print(f"- [{error['code']}] {error['path']}: {error['message']}")
        if result["warnings"]:
            print("\nWarnings:")
            for warning in result["warnings"]:
                print(f"- [{warning['code']}] {warning['path']}: {warning['message']}")

    if result["errors"]:
        return 1
    if args.strict and result["warnings"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


