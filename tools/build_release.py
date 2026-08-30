#!/usr/bin/env python3
"""Build and validate a complete LingXi release bundle.

The bundle contains the Windows CLI, Python abi3 wheel, browser WASM package,
and Windows C FFI. Only the redistributable model assets approved in ASSETS.md
are copied into the release.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
TARGET = ROOT / "target"
DIST = ROOT / "dist"
VERSION = "0.4.5"
PLATFORM = "windows-x86_64"

REQUIRED_ASSET_HASHES = {
    "dict.bin": "8B29D53505374518B81DB1481F658B078068C4909B3FEACDFC9F0A4E1B1DB056",
    "hmm_bmes.bin": "D15F411F506B68300EEBB343A10C70268CA6DC389C3D6A439FF61ABBCC30FAFA",
    "hmm_pos.bin": "791EBB87CDB13D9CD0BEB53B1D2E5E4BA3B2A284EB8BE0E8279529080E7499E2",
}
OPTIONAL_ASSETS = ("affect.bin",)


def run(
    command: list[str],
    *,
    cwd: Path = ROOT,
    input_text: str | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    print(f"$ {' '.join(command)}  (cwd={cwd})", flush=True)
    result = subprocess.run(
        command,
        cwd=cwd,
        input=input_text,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        env=env,
    )
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    if result.returncode != 0:
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(command)}")
    return result


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def workspace_version() -> str:
    in_workspace_package = False
    for line in (ROOT / "Cargo.toml").read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped == "[workspace.package]":
            in_workspace_package = True
            continue
        if in_workspace_package and stripped.startswith("["):
            break
        if in_workspace_package and stripped.startswith("version"):
            return stripped.split("=", 1)[1].strip().strip('"')
    raise RuntimeError("cannot find [workspace.package] version")


def verify_source_assets() -> list[str]:
    if workspace_version() != VERSION:
        raise RuntimeError(
            f"release script is {VERSION}, but Cargo workspace is {workspace_version()}"
        )
    assets = []
    for name, expected in REQUIRED_ASSET_HASHES.items():
        path = ASSETS / name
        if not path.is_file():
            raise RuntimeError(f"missing required model asset: {path}")
        actual = sha256(path).upper()
        if actual != expected:
            raise RuntimeError(f"unapproved model asset {name}: {actual} != {expected}")
        assets.append(name)
    for name in OPTIONAL_ASSETS:
        if (ASSETS / name).is_file():
            assets.append(name)
    print("Verified release assets:")
    for name in assets:
        print(f"  {name}: {sha256(ASSETS / name)}")
    return assets


def copy_assets(destination: Path, names: list[str]) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for name in names:
        shutil.copy2(ASSETS / name, destination / name)


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.strip() + "\n", encoding="utf-8", newline="\n")


def build_binaries() -> tuple[Path, Path, Path]:
    run(
        [
            "cargo",
            "build",
            "--release",
            "--locked",
            "-p",
            "lingxi-cli",
            "-p",
            "lingxi-ffi",
        ]
    )
    release = TARGET / "release"
    required = (
        release / "lingxi.exe",
        release / "lingxi_ffi.dll",
        release / "lingxi_ffi.lib",
    )
    for path in required:
        if not path.is_file():
            raise RuntimeError(f"build did not produce {path}")
    import_lib = release / "lingxi_ffi.dll.lib"
    if not import_lib.is_file():
        raise RuntimeError(f"build did not produce {import_lib}")
    return required


def build_wheel() -> Path:
    run([sys.executable, str(ROOT / "tools" / "build_wheel.py")])
    wheels = sorted((TARGET / "wheels").glob(f"lingxi-{VERSION}-*.whl"))
    if not wheels:
        raise RuntimeError(f"no lingxi-{VERSION} wheel was produced")
    return max(wheels, key=lambda path: path.stat().st_mtime)


def build_browser_wasm() -> Path:
    output = TARGET / "release-packaging" / f"browser-wasm-{VERSION}"
    if output.exists():
        shutil.rmtree(output)
    tool_state = TARGET / "release-packaging" / "wasm-pack-state"
    temp_dir = tool_state / "tmp"
    cargo_install = tool_state / "cargo-install"
    wasm_pack_cache = tool_state / "cache"
    for directory in (temp_dir, cargo_install, wasm_pack_cache):
        directory.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment.update(
        {
            "TEMP": str(temp_dir),
            "TMP": str(temp_dir),
            "CARGO_INSTALL_ROOT": str(cargo_install),
            "WASM_PACK_CACHE": str(wasm_pack_cache),
        }
    )
    wasm_pack = shutil.which("wasm-pack.cmd") or shutil.which("wasm-pack")
    if not wasm_pack:
        raise RuntimeError("wasm-pack is not installed or not on PATH")
    run(
        [
            wasm_pack,
            "build",
            "--release",
            "--target",
            "web",
            "--out-dir",
            str(output),
        ],
        cwd=ROOT / "crates" / "lingxi-wasm",
        env=environment,
    )
    return output


def validate_cli(executable: Path) -> None:
    text = "金管會今天表示不得刪除資料，台北市場上漲120點。"
    pos = run(
        [str(executable), "--assets", str(ASSETS), "--format", "annotated-json"],
        input_text=text + "\n",
    )
    payload = json.loads(pos.stdout.strip())
    tokens = payload.get("tokens", payload)
    if not tokens or not all(token.get("t") or token.get("tag") for token in tokens):
        raise RuntimeError("CLI POS smoke test returned no tagged tokens")
    summary = run(
        [str(executable), "--assets", str(ASSETS), "--summary", "1"],
        input_text=text + "\n",
    )
    document = json.loads(summary.stdout.strip())
    if document.get("schemaVersion") != 2 or not document.get("text"):
        raise RuntimeError("CLI summary smoke test did not return schema v2")


class LingxiUtf8(ctypes.Structure):
    _fields_ = [("len", ctypes.c_size_t), ("data", ctypes.c_void_p)]


def read_ffi_json(library: ctypes.CDLL, pointer: int) -> object:
    if not pointer:
        raise RuntimeError("FFI returned NULL")
    value = ctypes.cast(pointer, ctypes.POINTER(LingxiUtf8)).contents
    payload = ctypes.string_at(value.data, value.len).decode("utf-8")
    library.lingxi_utf8_free(pointer)
    return json.loads(payload)


def validate_ffi(library_path: Path) -> None:
    library = ctypes.CDLL(str(library_path))
    library.lingxi_new_from_dir.argtypes = [ctypes.c_char_p]
    library.lingxi_new_from_dir.restype = ctypes.c_void_p
    library.lingxi_free.argtypes = [ctypes.c_void_p]
    library.lingxi_annotate_json.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_size_t,
    ]
    library.lingxi_annotate_json.restype = ctypes.c_void_p
    library.lingxi_extract_summary_json.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_size_t,
    ]
    library.lingxi_extract_summary_json.restype = ctypes.c_void_p
    library.lingxi_utf8_free.argtypes = [ctypes.c_void_p]

    handle = library.lingxi_new_from_dir(os.fsencode(ASSETS))
    if not handle:
        raise RuntimeError("FFI could not load release assets")
    try:
        encoded = "金管會今天表示不得刪除資料，台北市場上漲120點。".encode()
        buffer = ctypes.create_string_buffer(encoded)
        annotated = read_ffi_json(
            library,
            library.lingxi_annotate_json(handle, buffer, len(encoded)),
        )
        if not annotated or not all(token.get("tag") for token in annotated):
            raise RuntimeError("FFI POS smoke test returned no tagged tokens")
        summary = read_ffi_json(
            library,
            library.lingxi_extract_summary_json(handle, buffer, len(encoded), 1),
        )
        if summary.get("schemaVersion") != 2 or not summary.get("text"):
            raise RuntimeError("FFI summary smoke test did not return schema v2")
    finally:
        library.lingxi_free(handle)


def validate_wheel(wheel: Path, asset_names: list[str]) -> None:
    with zipfile.ZipFile(wheel) as archive:
        entries = set(archive.namelist())
        cached = sorted(
            entry for entry in entries if "__pycache__" in entry or entry.endswith(".pyc")
        )
        if cached:
            raise RuntimeError(f"wheel contains Python cache files: {cached}")
        bundled_bins = {
            Path(entry).name for entry in entries if entry.startswith("lingxi/assets/") and entry.endswith(".bin")
        }
        if bundled_bins != set(asset_names):
            raise RuntimeError(
                f"wheel model set mismatch: {sorted(bundled_bins)} != {sorted(asset_names)}"
            )
        for name, expected in REQUIRED_ASSET_HASHES.items():
            actual = hashlib.sha256(archive.read(f"lingxi/assets/{name}")).hexdigest().upper()
            if actual != expected:
                raise RuntimeError(f"wheel contains wrong {name}: {actual}")

    install_dir = TARGET / "release-packaging" / f"python-smoke-{VERSION}"
    if install_dir.exists():
        shutil.rmtree(install_dir)
    run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-deps",
            "--target",
            str(install_dir),
            str(wheel),
        ]
    )
    smoke = (
        "import lingxi; "
        "tokens=lingxi.tokenize('金管會今天表示不得刪除資料'); "
        "assert tokens and all(t.tag for t in tokens); "
        "summary=lingxi.extract_summary('第一段說明市場。\\n\\n第二段不得刪除資料。', 1); "
        "assert summary.schema_version == 2 and summary.text; "
        "print({'pos': [(t.word, t.tag) for t in tokens], 'summary': summary.text})"
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(install_dir)
    run([sys.executable, "-c", smoke], env=environment)


def configure_browser_package(source: Path, destination: Path, asset_names: list[str]) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for name in (
        "lingxi_wasm_bg.wasm",
        "lingxi_wasm.js",
        "lingxi_wasm.d.ts",
        "lingxi_wasm_bg.wasm.d.ts",
    ):
        shutil.copy2(source / name, destination / name)
    shutil.copy2(ROOT / "LICENSE", destination / "LICENSE")
    copy_assets(destination / "assets", asset_names)

    package = json.loads((source / "package.json").read_text(encoding="utf-8"))
    package["version"] = VERSION
    package["private"] = True
    package["files"] = [
        "lingxi_wasm_bg.wasm",
        "lingxi_wasm.js",
        "lingxi_wasm.d.ts",
        "lingxi_wasm_bg.wasm.d.ts",
        "assets/*.bin",
        "LICENSE",
        "README.md",
        "release-manifest.json",
        "smoke.mjs",
    ]
    (destination / "package.json").write_text(
        json.dumps(package, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    write_text(
        destination / "README.md",
        f"""
# LingXi Browser WASM {VERSION}

This browser ES module bundles the approved Traditional Chinese segmentation,
BMES and LXA3 POS models. It exposes tokenization/POS and schema v2 structured
extractive summaries without sending source text to a server.

```js
import init, {{ Segmenter }} from "./lingxi_wasm.js";
const bytes = async (url) => new Uint8Array(await (await fetch(url)).arrayBuffer());
await init();
const segmenter = Segmenter.fromAssets(
  await bytes("./assets/dict.bin"),
  await bytes("./assets/hmm_bmes.bin"),
  await bytes("./assets/hmm_pos.bin"),
  await bytes("./assets/affect.bin"),
  undefined,
);
console.log(segmenter.tokenize("金管會今天表示不得刪除資料"));
console.log(segmenter.extractSummary("第一段。\\n\\n第二段不得刪除資料。", 1));
```

Serve this directory over HTTP(S). Run `node smoke.mjs` for a local runtime check.
""",
    )
    write_text(
        destination / "smoke.mjs",
        """
import { readFileSync } from "node:fs";
import init, { Segmenter } from "./lingxi_wasm.js";

const bytes = (path) => new Uint8Array(readFileSync(new URL(path, import.meta.url)));
await init({ module_or_path: bytes("./lingxi_wasm_bg.wasm") });
const segmenter = Segmenter.fromAssets(
  bytes("./assets/dict.bin"),
  bytes("./assets/hmm_bmes.bin"),
  bytes("./assets/hmm_pos.bin"),
  bytes("./assets/affect.bin"),
  undefined,
);
const text = "金管會今天表示不得刪除資料，台北市場上漲120點。";
const tokens = segmenter.tokenize(text);
if (!tokens.length || tokens.some((token) => !token.tag)) {
  throw new Error(`POS smoke test failed: ${JSON.stringify(tokens)}`);
}
const summary = segmenter.extractSummary(text, 1);
if (summary.schemaVersion !== 2 || !summary.text) {
  throw new Error(`summary smoke test failed: ${JSON.stringify(summary)}`);
}
console.log(JSON.stringify({ tokens, summary: summary.text }, null, 2));
""",
    )
    browser_manifest = {
        "schema_version": 1,
        "name": "lingxi-browser-wasm",
        "version": VERSION,
        "target": "web",
        "features": ["segmentation", "pos", "summary-schema-v2"],
        "assets": {name: sha256(ASSETS / name) for name in asset_names},
    }
    (destination / "release-manifest.json").write_text(
        json.dumps(browser_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def make_zip(source: Path, archive: Path) -> None:
    if archive.exists():
        archive.unlink()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as output:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                output.write(path, Path(source.name) / path.relative_to(source))


def assemble_release(
    asset_names: list[str], wheel: Path, browser_build: Path
) -> tuple[Path, list[Path]]:
    release_root = DIST / f"lingxi-{VERSION}"
    if release_root.exists():
        shutil.rmtree(release_root)
    cli_dir = release_root / f"cli-{PLATFORM}"
    ffi_dir = release_root / f"ffi-{PLATFORM}"
    python_dir = release_root / "python"
    browser_dir = release_root / "browser-wasm"
    cli_dir.mkdir(parents=True)
    ffi_dir.mkdir(parents=True)
    python_dir.mkdir(parents=True)

    shutil.copy2(TARGET / "release" / "lingxi.exe", cli_dir / "lingxi.exe")
    copy_assets(cli_dir / "assets", asset_names)
    write_text(
        cli_dir / "README.txt",
        f"""
LingXi CLI {VERSION} for Windows x86_64

POS:
  "金管會今天表示不得刪除資料" | .\\lingxi.exe --assets .\\assets --format annotated-json

Schema v2 summary:
  Get-Content article.txt | .\\lingxi.exe --assets .\\assets --summary 3
""",
    )

    for name in ("lingxi_ffi.dll", "lingxi_ffi.dll.lib", "lingxi_ffi.lib"):
        shutil.copy2(TARGET / "release" / name, ffi_dir / name)
    shutil.copytree(ROOT / "crates" / "lingxi-ffi" / "include", ffi_dir / "include")
    copy_assets(ffi_dir / "assets", asset_names)
    write_text(
        ffi_dir / "README.txt",
        f"""
LingXi C FFI {VERSION} for Windows x86_64

Files include the DLL, MSVC import/static libraries, public header, and approved
models. Use lingxi_new_from_dir(), lingxi_annotate_json() for POS, and
lingxi_extract_summary_json() for schema v2 summaries. Free each result with its
matching API.
""",
    )

    packaged_wheel = python_dir / wheel.name
    shutil.copy2(wheel, packaged_wheel)
    write_text(
        python_dir / "README.txt",
        f"""
LingXi Python {VERSION} for CPython 3.9+ abi3 on Windows x86_64

Install:
  python -m pip install --force-reinstall {wheel.name}

POS:
  lingxi.tokenize("金管會今天表示不得刪除資料")

Schema v2 summary:
  lingxi.extract_summary(text, max_blocks=3)
""",
    )

    configure_browser_package(browser_build, browser_dir, asset_names)
    run(["node", "smoke.mjs"], cwd=browser_dir)

    archives = [
        DIST / f"lingxi-cli-{VERSION}-{PLATFORM}.zip",
        DIST / f"lingxi-ffi-{VERSION}-{PLATFORM}.zip",
        DIST / f"lingxi-browser-wasm-{VERSION}-web.zip",
    ]
    for source, archive in zip((cli_dir, ffi_dir, browser_dir), archives):
        make_zip(source, archive)

    artifacts = archives + [packaged_wheel]
    manifest = {
        "schema_version": 3,
        "name": "lingxi",
        "version": VERSION,
        "release_date": date.today().isoformat(),
        "platform": "windows-x86_64 plus browser-wasm",
        "features": ["segmentation", "pos-lxa3", "summary-schema-v2"],
        "asset_formats": {"dict": "LXA2", "bmes": "LXA2", "pos": "LXA3-i16"},
        "assets": [
            {"name": name, "bytes": (ASSETS / name).stat().st_size, "sha256": sha256(ASSETS / name)}
            for name in asset_names
        ],
        "targets": {
            "cli": {"format": "Windows executable", "validation": "passed-pos-summary"},
            "ffi": {"format": "Windows DLL and import/static libraries", "validation": "passed-pos-summary"},
            "python": {"format": "CPython 3.9+ abi3 wheel", "validation": "passed-pos-summary"},
            "browser": {"format": "wasm-pack web ES module", "validation": "passed-pos-summary"},
        },
        "artifacts": [
            {
                "path": str(path.relative_to(DIST)).replace("\\", "/"),
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
            for path in artifacts
        ],
    }
    (release_root / "release-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    write_text(
        release_root / "README.txt",
        f"""
LingXi {VERSION} release bundle (Windows x86_64 + Browser WASM)

Includes Python, browser WASM, CLI and C FFI packages. Every target contains the
approved LXA3 POS model and exposes schema v2 structured summaries. Model hashes
and per-artifact hashes are recorded in release-manifest.json and SHA256SUMS.txt.
""",
    )
    sums = "\n".join(
        f"{sha256(path)}  {str(path.relative_to(DIST)).replace(os.sep, '/')}" for path in artifacts
    )
    write_text(release_root / "SHA256SUMS.txt", sums)
    return release_root, artifacts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-tests",
        action="store_true",
        help="skip cargo workspace tests before building (target smoke tests still run)",
    )
    args = parser.parse_args()

    asset_names = verify_source_assets()
    if not args.skip_tests:
        run(["cargo", "test", "--workspace", "--locked"])
    build_binaries()
    validate_cli(TARGET / "release" / "lingxi.exe")
    validate_ffi(TARGET / "release" / "lingxi_ffi.dll")
    wheel = build_wheel()
    validate_wheel(wheel, asset_names)
    browser_build = build_browser_wasm()
    release_root, artifacts = assemble_release(asset_names, wheel, browser_build)

    print(f"\nRelease ready: {release_root}")
    for artifact in artifacts:
        print(f"  {artifact} ({artifact.stat().st_size} bytes, sha256={sha256(artifact)})")


if __name__ == "__main__":
    main()
