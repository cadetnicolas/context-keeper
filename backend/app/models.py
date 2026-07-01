from datetime import datetime
from enum import Enum as PyEnum
from typing import Optional, List
from sqlalchemy import (
    Column, Integer, String, Text, DateTime, Float,
    Enum, ForeignKey, JSON, create_engine, event
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings

Base = declarative_base()


class MemoryType(str, PyEnum):
    DECISION = "decision"           # 团队技术决策
    LESSON = "lesson"               # 踩坑记录 / 经验教训
    FACT = "fact"                   # 项目事实 / 约束
    PREFERENCE = "preference"       # 团队偏好 / 规范
    TODO = "todo"                   # 技术债 / 待办
    ARCHITECTURE = "architecture"   # 架构设计


class MemorySource(str, PyEnum):
    MANUAL = "manual"               # 手动录入
    AGENT = "agent"                 # AI Agent 自动提取
    GIT = "git"                     # 从 Git 历史提取
    FILE = "file"                   # 从文件导入


class Memory(Base):
    """团队记忆核心实体"""
    __tablename__ = "memories"

    id = Column(Integer, primary_key=True, index=True)
    content = Column(Text, nullable=False)              # 记忆文本内容
    memory_type = Column(Enum(MemoryType), default=MemoryType.FACT)
    source = Column(Enum(MemorySource), default=MemorySource.MANUAL)

    # 项目/团队维度
    project_id = Column(String(128), index=True, default="default")
    team_id = Column(String(128), index=True, default="default")
    created_by = Column(String(128), default="system")  # 创建者 user_id

    # 元数据
    tags = Column(JSON, default=list)                   # 标签列表
    entities = Column(JSON, default=list)               # 命名实体
    related_files = Column(JSON, default=list)          # 关联文件路径
    confidence = Column(Float, default=1.0)             # 置信度 0-1
    usage_count = Column(Integer, default=0)            # 被召回次数

    # 时间戳
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_recalled_at = Column(DateTime, nullable=True)

    # 关系
    embeddings = relationship("MemoryEmbedding", back_populates="memory", cascade="all, delete-orphan")
    feedback = relationship("MemoryFeedback", back_populates="memory", cascade="all, delete-orphan")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "content": self.content,
            "memory_type": self.memory_type.value,
            "source": self.source.value,
            "project_id": self.project_id,
            "team_id": self.team_id,
            "created_by": self.created_by,
            "tags": self.tags or [],
            "entities": self.entities or [],
            "related_files": self.related_files or [],
            "confidence": self.confidence,
            "usage_count": self.usage_count,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "last_recalled_at": self.last_recalled_at.isoformat() if self.last_recalled_at else None,
        }


class MemoryEmbedding(Base):
    """记忆向量嵌入"""
    __tablename__ = "memory_embeddings"

    id = Column(Integer, primary_key=True)
    memory_id = Column(Integer, ForeignKey("memories.id"), nullable=False)
    model_name = Column(String(128), default="")
    # 为兼容 SQLite，向量以 JSON 存储；PostgreSQL + pgvector 可改用 vector 类型
    embedding_json = Column(Text, nullable=True)

    memory = relationship("Memory", back_populates="embeddings")


class MemoryFeedback(Base):
    """记忆质量反馈：用于后续优化和去噪"""
    __tablename__ = "memory_feedback"

    id = Column(Integer, primary_key=True)
    memory_id = Column(Integer, ForeignKey("memories.id"), nullable=False)
    user_id = Column(String(128), default="")
    helpful = Column(Integer, default=0)  # 1=有用, -1=无用, 0=未评价
    comment = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)

    memory = relationship("Memory", back_populates="feedback")


# 数据库引擎与会话
_engine_kwargs = {}
if settings.database_url.startswith("sqlite"):
    _engine_kwargs = {
        "connect_args": {"check_same_thread": False},
        "poolclass": StaticPool,
    }

engine = create_engine(settings.database_url, **_engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db():
    """初始化数据库表"""
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
