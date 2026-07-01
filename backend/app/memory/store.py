import json
import re
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any

from sqlalchemy.orm import Session

from app.models import Memory, MemoryEmbedding, MemoryType, MemorySource
from app.config import settings


class MemoryStore:
    """记忆存储层：封装 CRUD 与基础查询"""

    def __init__(self, db: Session):
        self.db = db

    def add_memory(
        self,
        content: str,
        memory_type: MemoryType = MemoryType.FACT,
        source: MemorySource = MemorySource.MANUAL,
        project_id: str = "default",
        team_id: str = "default",
        created_by: str = "system",
        tags: Optional[List[str]] = None,
        entities: Optional[List[str]] = None,
        related_files: Optional[List[str]] = None,
        confidence: float = 1.0,
        embedding: Optional[List[float]] = None,
        model_name: str = "",
    ) -> Memory:
        """新增一条记忆"""
        memory = Memory(
            content=content,
            memory_type=memory_type,
            source=source,
            project_id=project_id,
            team_id=team_id,
            created_by=created_by,
            tags=tags or [],
            entities=entities or self._extract_entities(content),
            related_files=related_files or [],
            confidence=confidence,
        )
        self.db.add(memory)
        self.db.flush()  # 获取 memory.id

        if embedding:
            self._save_embedding(memory.id, embedding, model_name)

        self.db.commit()
        self.db.refresh(memory)
        return memory

    def get_memory(self, memory_id: int) -> Optional[Memory]:
        return self.db.query(Memory).filter(Memory.id == memory_id).first()

    def list_memories(
        self,
        project_id: Optional[str] = None,
        team_id: Optional[str] = None,
        memory_type: Optional[MemoryType] = None,
        tag: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Memory]:
        """列出记忆，支持多维过滤"""
        query = self.db.query(Memory)
        if project_id:
            query = query.filter(Memory.project_id == project_id)
        if team_id:
            query = query.filter(Memory.team_id == team_id)
        if memory_type:
            query = query.filter(Memory.memory_type == memory_type)
        if tag:
            query = query.filter(Memory.tags.contains([tag]))

        return (
            query.order_by(Memory.updated_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )

    def update_memory(
        self,
        memory_id: int,
        content: Optional[str] = None,
        tags: Optional[List[str]] = None,
        confidence: Optional[float] = None,
    ) -> Optional[Memory]:
        memory = self.get_memory(memory_id)
        if not memory:
            return None
        if content is not None:
            memory.content = content
        if tags is not None:
            memory.tags = tags
        if confidence is not None:
            memory.confidence = confidence
        memory.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(memory)
        return memory

    def delete_memory(self, memory_id: int) -> bool:
        memory = self.get_memory(memory_id)
        if not memory:
            return False
        self.db.delete(memory)
        self.db.commit()
        return True

    def record_recall(self, memory_id: int) -> None:
        """记录一次记忆被召回，更新使用频率"""
        memory = self.get_memory(memory_id)
        if memory:
            memory.usage_count += 1
            memory.last_recalled_at = datetime.utcnow()
            self.db.commit()

    def add_feedback(
        self,
        memory_id: int,
        user_id: str,
        helpful: int,
        comment: str = "",
    ) -> bool:
        from app.models import MemoryFeedback

        memory = self.get_memory(memory_id)
        if not memory:
            return False
        feedback = MemoryFeedback(
            memory_id=memory_id,
            user_id=user_id,
            helpful=helpful,
            comment=comment,
        )
        self.db.add(feedback)
        self.db.commit()
        return True

    def get_stale_memories(
        self,
        days: Optional[int] = None,
        min_confidence: float = 0.0,
    ) -> List[Memory]:
        """获取可能陈旧的记忆，用于人工审核或自动降权"""
        days = days or settings.memory_staleness_days
        threshold = datetime.utcnow() - timedelta(days=days)
        return (
            self.db.query(Memory)
            .filter(Memory.updated_at < threshold)
            .filter(Memory.confidence >= min_confidence)
            .order_by(Memory.usage_count.asc())
            .all()
        )

    def _save_embedding(
        self,
        memory_id: int,
        embedding: List[float],
        model_name: str,
    ) -> None:
        emb = MemoryEmbedding(
            memory_id=memory_id,
            model_name=model_name,
            embedding_json=json.dumps(embedding),
        )
        self.db.add(emb)

    def _extract_entities(self, content: str) -> List[str]:
        """简易实体提取：驼峰命名、大写缩写、路径/文件名"""
        entities = set()
        # 驼峰/帕斯卡命名
        for match in re.finditer(r"\b[A-Z][a-z]+(?:[A-Z][a-z]+)+\b", content):
            entities.add(match.group())
        # 全大写缩写 3-10 字符
        for match in re.finditer(r"\b[A-Z]{3,10}\b", content):
            entities.add(match.group())
        # 文件路径或文件名
        for match in re.finditer(r"[\w/\\.-]+\.(py|js|ts|tsx|jsx|go|rs|java|md|json|yaml|yml|sql)\b", content):
            entities.add(match.group())
        return sorted(list(entities))[:20]
