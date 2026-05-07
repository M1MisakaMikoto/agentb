from __future__ import annotations

from typing import Dict, List

from rag.model.do.file.CategoryDO import CategoryDO
from rag.model.entity.file.CategoryNodeEntity import CategoryNodeEntity
from rag.model.vo.file.CategoryTreeNodeVO import CategoryTreeNodeVO
from rag.model.vo.file.CategoryTreeResponseVO import CategoryTreeResponseVO


class CategoryTreeAssembler:
    def to_tree_response(self, rows: List[CategoryDO]) -> CategoryTreeResponseVO:
        by_id: Dict[int, CategoryNodeEntity] = {}
        roots: List[CategoryNodeEntity] = []

        for row in rows:
            by_id[row.id] = CategoryNodeEntity(
                id=row.id,
                name=row.name,
                parent_id=row.parent_id,
                created_at=row.created_at,
            )

        for node in by_id.values():
            pid = node.parent_id
            if pid is not None and pid in by_id:
                by_id[pid].children.append(node)
            else:
                roots.append(node)

        return CategoryTreeResponseVO(items=[self._to_node_vo(node) for node in roots])

    def _to_node_vo(self, node: CategoryNodeEntity) -> CategoryTreeNodeVO:
        return CategoryTreeNodeVO(
            id=node.id,
            name=node.name,
            parent_id=node.parent_id,
            created_at=node.created_at,
            children=[self._to_node_vo(child) for child in node.children],
        )
