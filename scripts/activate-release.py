import argparse
import os
import re
import shutil
from pathlib import Path


def activate_release(root: Path, app: str, release: str, source: Path) -> Path:
    entries = {"home": "index.html", "history": "history.html", "lol-rag": "index.html"}
    if app not in entries or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,100}", release):
        raise ValueError("Invalid app or release")
    if not root.is_absolute():
        raise ValueError("Deployment root must be absolute")
    root = root.resolve()
    source = source.resolve()
    releases = root / app / "releases"
    if source == releases or releases in source.parents or source == root:
        raise ValueError("Build source must not be an existing release")
    if not (source / entries[app]).is_file():
        raise ValueError("Build entry is missing")
    if any(path.is_symlink() for path in source.rglob("*")):
        raise ValueError("Build must not contain symlinks")
    app_root = root / app
    app_root.mkdir(parents=True, exist_ok=True)
    destination = app_root / "releases" / release
    if destination.exists():
        raise ValueError("Release already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination)
    current = app_root / "current"
    if current.exists() and not current.is_symlink():
        raise ValueError("Current must be a release symlink")
    if current.is_symlink() and (current / "assets").is_dir():
        previous_assets = current / "assets"
        for original in previous_assets.rglob("*"):
            target = destination / "assets" / original.relative_to(previous_assets)
            if original.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            elif not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(original, target)
    for path in [destination, *destination.rglob("*")]:
        path.chmod(0o755 if path.is_dir() else 0o644)
    next_link = app_root / f".next-{release}"
    if next_link.is_symlink():
        next_link.unlink()
    next_link.symlink_to(destination, target_is_directory=True)
    os.replace(next_link, current)
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("app", choices=["home", "history", "lol-rag"])
    parser.add_argument("release")
    parser.add_argument("source", type=Path)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    print(activate_release(args.root, args.app, args.release, args.source))
