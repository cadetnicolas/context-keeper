from typing import List, Optional
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.models import get_db, MemoryType, MemorySource, Memory
from app.memory.store import MemoryStore
from app.memory.retrieval import MemoryRetriever
from app.memory.sync import SyncManager


router = APIRouter(prefix="/api/v1")


# ---------- Pydantic 模型 ----------

class MemoryCreate(BaseModel):
    content: str = Field(..., min_length=1, description="记忆内容")
    memory_type: str = Field(default="fact", description="decision|lesson|fact|preference|todo|architecture")
    source: str = Field(default="manual", description="manual|agent|git|file")
    project_id: str = Field(default="default")
    team_id: str = Field(default="default")
    created_by: str = Field(default="system")
    tags: List[str] = Field(default_factory=list)
    related_files: List[str] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class MemoryUpdate(BaseModel):
    content: Optional[str] = None
    tags: Optional[List[str]] = None
    confidence: Optional[float] = None


class MemoryResponse(BaseModel):
    id: int
    content: str
    memory_type: str
    source: str
    project_id: str
    team_id: str
    created_by: str
    tags: List[str]
    entities: List[str]
    related_files: List[str]
    confidence: float
    usage_count: int
    created_at: str
    updated_at: str

    class Config:
        from_attributes = True


class SearchQuery(BaseModel):
    query: str = Field(..., min_length=1)
    project_id: str = Field(default="default")
    team_id: str = Field(default="default")
    top_k: int = Field(default=5, ge=1, le=20)
    memory_type: Optional[str] = None


class FeedbackCreate(BaseModel):
    user_id: str
    helpful: int = Field(..., ge=-1, le=1)
    comment: Optional[str] = ""


# ---------- 记忆 CRUD ----------

@router.post("/memories", response_model=MemoryResponse)
def create_memory(payload: MemoryCreate, db: Session = Depends(get_db)):
    store = MemoryStore(db)
    retriever = MemoryRetriever(db)

    memory = store.add_memory(
        content=payload.content,
        memory_type=MemoryType(payload.memory_type),
        source=MemorySource(payload.source),
        project_id=payload.project_id,
        team_id=payload.team_id,
        created_by=payload.created_by,
        tags=payload.tags,
        related_files=payload.related_files,
        confidence=payload.confidence,
    )

    # 异步生成嵌入（简单实现：同步生成）
    retriever.refresh_embedding(memory.id)

    return MemoryResponse(**memory.to_dict())


@router.get("/memories", response_model=List[MemoryResponse])
def list_memories(
    project_id: Optional[str] = Query(default="default"),
    team_id: Optional[str] = Query(default="default"),
    memory_type: Optional[str] = Query(default=None),
    tag: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    store = MemoryStore(db)
    memories = store.list_memories(
        project_id=project_id,
        team_id=team_id,
        memory_type=MemoryType(memory_type) if memory_type else None,
        tag=tag,
        limit=limit,
        offset=offset,
    )
    return [MemoryResponse(**m.to_dict()) for m in memories]


@router.get("/memories/{memory_id}", response_model=MemoryResponse)
def get_memory(memory_id: int, db: Session = Depends(get_db)):
    store = MemoryStore(db)
    memory = store.get_memory(memory_id)
    if not memory:
        raise HTTPException(status_code=404, detail="Memory not found")
    return MemoryResponse(**memory.to_dict())


@router.patch("/memories/{memory_id}", response_model=MemoryResponse)
def update_memory(
    memory_id: int,
    payload: MemoryUpdate,
    db: Session = Depends(get_db),
):
    store = MemoryStore(db)
    memory = store.update_memory(
        memory_id=memory_id,
        content=payload.content,
        tags=payload.tags,
        confidence=payload.confidence,
    )
    if not memory:
        raise HTTPException(status_code=404, detail="Memory not found")

    # 内容变更后刷新嵌入
    if payload.content:
        retriever = MemoryRetriever(db)
        retriever.refresh_embedding(memory.id)

    return MemoryResponse(**memory.to_dict())


@router.delete("/memories/{memory_id}")
def delete_memory(memory_id: int, db: Session = Depends(get_db)):
    store = MemoryStore(db)
    success = store.delete_memory(memory_id)
    if not success:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"success": True}


# ---------- 检索 ----------

@router.post("/search")
def search_memories(payload: SearchQuery, db: Session = Depends(get_db)):
    retriever = MemoryRetriever(db)
    results = retriever.search(
        query=payload.query,
        project_id=payload.project_id,
        team_id=payload.team_id,
        top_k=payload.top_k,
        memory_type=payload.memory_type,
    )
    return {"query": payload.query, "count": len(results), "results": results}


# ---------- 反馈 ----------

@router.post("/memories/{memory_id}/feedback")
def add_feedback(
    memory_id: int,
    payload: FeedbackCreate,
    db: Session = Depends(get_db),
):
    store = MemoryStore(db)
    success = store.add_feedback(
        memory_id=memory_id,
        user_id=payload.user_id,
        helpful=payload.helpful,
        comment=payload.comment or "",
    )
    if not success:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"success": True}


# ---------- 团队同步 ----------

@router.post("/sync/export")
def export_snapshot(
    project_id: Optional[str] = Query(default="default"),
    team_id: Optional[str] = Query(default="default"),
    db: Session = Depends(get_db),
):
    """导出团队记忆快照，用于 Git/文件同步"""
    manager = SyncManager(db)
    snapshot = manager.export_snapshot(project_id, team_id)
    return snapshot


@router.post("/sync/import")
def import_snapshot(payload: dict, db: Session = Depends(get_db)):
    """导入团队记忆快照"""
    manager = SyncManager(db)
    stats = manager.import_snapshot(payload)
    return {"success": True, "stats": stats}


# ---------- 健康与统计 ----------

@router.get("/health")
def health():
    return {"status": "ok", "service": "ContextKeeper", "version": "0.1.0"}


@router.get("/stats")
def stats(
    project_id: Optional[str] = Query(default="default"),
    team_id: Optional[str] = Query(default="default"),
    db: Session = Depends(get_db),
):
    query = db.query(Memory).filter(
        Memory.project_id == project_id,
        Memory.team_id == team_id,
    )
    total = query.count()

    type_counts = {}
    for mtype in MemoryType:
        count = query.filter(Memory.memory_type == mtype).count()
        type_counts[mtype.value] = count

    return {
        "project_id": project_id,
        "team_id": team_id,
        "total_memories": total,
        "type_counts": type_counts,
    }
