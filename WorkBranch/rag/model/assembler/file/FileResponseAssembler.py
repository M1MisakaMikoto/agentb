from __future__ import annotations

from rag.model.vo.file.FileListEntryVO import FileListEntryVO
from rag.model.vo.file.FileListVO import FileListVO
from rag.model.vo.file.FileMutationVO import FileMutationVO
from rag.model.vo.file.FileReadVO import FileReadVO


class FileResponseAssembler:
    def to_list_vo(self, payload: dict) -> FileListVO:
        entries = [
            FileListEntryVO(
                name=item["name"],
                path=item["path"],
                type=item["type"],
                size=item.get("size"),
            )
            for item in payload.get("entries", [])
        ]
        return FileListVO(
            root=payload.get("root", ""),
            cwd=payload.get("cwd", ""),
            parent=payload.get("parent"),
            entries=entries,
        )

    def to_read_vo(self, payload: dict) -> FileReadVO:
        return FileReadVO(
            path=payload.get("path", ""),
            name=payload.get("name", ""),
            size=int(payload.get("size", 0)),
            content=payload.get("content", ""),
        )

    def to_mutation_vo(self, payload: dict) -> FileMutationVO:
        return FileMutationVO(
            ok=bool(payload.get("ok", False)),
            path=payload.get("path", ""),
            type=payload.get("type"),
        )
