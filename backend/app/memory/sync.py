"""
团队记忆同步模块
支持两种模式：
  1. git 模式：导出 JSON 快照，用户可用 git 管理并合并
  2. api 模式：通过 REST API 在团队成员间同步（未来可扩展）
"""

import json
from datetime import datetime
from typing import Dict, List, Any

from sqlalchemy.orm import Session

from app.models import Memory, MemoryType, MemorySource
from app.memory.store import MemoryStore


class SyncManager:
    def __init__(self, db: Session):
        self.db = db

    def export_snapshot(
        self,
        project_id: str = "default",
        team_id: str = "default",
    ) -> Dict[str, Any]:
        """导出指定项目/团队的记忆快照"""
        memories = (
            self.db.query(Memory)
            .filter(
                Memory.project_id == project_id,
                Memory.team_id == team_id,
            )
            .all()
        )

        return {
            "version": "0.1.0",
            "exported_at": datetime.utcnow().isoformat(),
            "project_id": project_id,
            "team_id": team_id,
            "memories": [m.to_dict() for m in memories],
        }

    def import_snapshot(self, snapshot: Dict[str, Any]) -> Dict[str, int]:
        """导入记忆快照，自动合并重复内容（基于 content 去重）"""
        store = MemoryStore(self.db)
        stats = {"created": 0, "updated": 0, "skipped": 0}

        project_id = snapshot.get("project_id", "default")
        team_id = snapshot.get("team_id", "default")

        # 获取现有内容集合用于去重
        existing = {
            m.content: m
            for m in self.db.query(Memory).filter(
                Memory.project_id == project_id,
                Memory.team_id == team_id,
            ).all()
        }

        for item in snapshot.get("memories", []):
            content = item.get("content", "")
            if not content:
                stats["skipped"] += 1
                continue

            if content in existing:
                # 更新元数据（如果更新的时间更晚）
                mem = existing[content]
                item_updated = item.get("updated_at")
                mem_updated = mem.updated_at.isoformat() if mem.updated_at else ""
                if item_updated and item_updated > mem_updated:
                    mem.memory_type = MemoryType(item.get("memory_type", "fact"))
                    mem.tags = item.get("tags", [])
                    mem.confidence = item.get("confidence", 1.0)
                    mem.updated_at = datetime.utcnow()
                    stats["updated"] += 1
                else:
                    stats["skipped"] += 1
            else:
                store.add_memory(
                    content=content,
                    memory_type=MemoryType(item.get("memory_type", "fact")),
                    source=MemorySource(item.get("source", "manual")),
                    project_id=project_id,
                    team_id=team_id,
                    created_by=item.get("created_by", "system"),
                    tags=item.get("tags", []),
                    entities=item.get("entities", []),
                    related_files=item.get("related_files", []),
                    confidence=item.get("confidence", 1.0),
                )
                stats["created"] += 1

        self.db.commit()
        return stats

    def export_to_file(
        self,
        path: str,
        project_id: str = "default",
        team_id: str = "default",
    ) -> None:
        snapshot = self.export_snapshot(project_id, team_id)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2)

    def import_from_file(self, path: str) -> Dict[str, int]:
        with open(path, "r", encoding="utf-8") as f:
            snapshot = json.load(f)
        return self.import_snapshot(snapshot)
