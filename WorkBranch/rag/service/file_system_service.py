from __future__ import annotations

from pathlib import Path
from typing import Any, Literal


class FileSystemService:
    """File CRUD service under managed root directory."""

    def __init__(self, managed_root: Path) -> None:
        self.managed_root = managed_root.resolve()

    def _resolve_under_root(self, relative_path: str) -> Path:
        rel = relative_path.strip().replace("\\", "/").lstrip("/")
        target = (self.managed_root / rel).resolve()
        if target != self.managed_root and self.managed_root not in target.parents:
            raise ValueError("Path is outside allowed root")
        return target

    def _to_rel(self, path: Path) -> str:
        if path == self.managed_root:
            return ""
        return str(path.relative_to(self.managed_root)).replace("\\", "/")

    def list_files(self, path: str = "") -> dict[str, Any]:
        base = self._resolve_under_root(path or "")
        if not base.exists():
            raise FileNotFoundError("Directory not found")
        if not base.is_dir():
            raise NotADirectoryError("Path is not a directory")

        entries = []
        for item in sorted(base.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
            entries.append(
                {
                    "name": item.name,
                    "path": self._to_rel(item),
                    "type": "dir" if item.is_dir() else "file",
                    "size": item.stat().st_size if item.is_file() else None,
                }
            )

        return {
            "root": str(self.managed_root),
            "cwd": self._to_rel(base),
            "parent": self._to_rel(base.parent) if base != self.managed_root else None,
            "entries": entries,
        }

    def read_file(self, path: str) -> dict[str, Any]:
        target = self._resolve_under_root(path)
        if not target.exists():
            raise FileNotFoundError("File not found")
        if not target.is_file():
            raise IsADirectoryError("Path is not a file")

        try:
            content = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            content = target.read_text(encoding="utf-8", errors="replace")

        return {
            "path": self._to_rel(target),
            "name": target.name,
            "size": target.stat().st_size,
            "content": content,
        }

    def create_file(self, path: str, item_type: Literal["file", "dir"], content: str, overwrite: bool) -> dict[str, Any]:
        target = self._resolve_under_root(path)
        if target.exists() and not overwrite:
            raise FileExistsError("Path already exists")

        if item_type == "dir":
            target.mkdir(parents=True, exist_ok=True)
            return {"ok": True, "path": self._to_rel(target), "type": "dir"}

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"ok": True, "path": self._to_rel(target), "type": "file"}

    def update_file(self, path: str, content: str) -> dict[str, Any]:
        target = self._resolve_under_root(path)
        if not target.exists():
            raise FileNotFoundError("File not found")
        if not target.is_file():
            raise IsADirectoryError("Path is not a file")
        target.write_text(content, encoding="utf-8")
        return {"ok": True, "path": self._to_rel(target)}

    def delete_path(self, path: str) -> dict[str, Any]:
        target = self._resolve_under_root(path)
        if not target.exists():
            raise FileNotFoundError("Path not found")
        if target == self.managed_root:
            raise ValueError("Cannot delete root directory")

        if target.is_dir():
            if any(target.iterdir()):
                raise RuntimeError("Directory is not empty")
            target.rmdir()
            return {"ok": True, "path": self._to_rel(target), "type": "dir"}

        target.unlink()
        return {"ok": True, "path": self._to_rel(target), "type": "file"}
