from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


_VERSION_RE = re.compile(r"^m_EditorVersion:\s*(?P<version>\S+)\s*$", re.MULTILINE)


@dataclass(frozen=True, slots=True)
class UnityProjectInfo:
    root: Path
    version: str = ""


def read_unity_project(project_root: Path) -> UnityProjectInfo | None:
    root = project_root.resolve()
    version_file = root / "ProjectSettings" / "ProjectVersion.txt"
    manifest = root / "Packages" / "manifest.json"
    assets = root / "Assets"
    if not (version_file.is_file() and manifest.is_file() and assets.is_dir()):
        return None
    version = ""
    try:
        match = _VERSION_RE.search(version_file.read_text(encoding="utf-8", errors="replace"))
        if match:
            version = match.group("version")
    except OSError:
        pass
    return UnityProjectInfo(root=root, version=version)
