import json
import math
import re
from typing import List, Dict, Tuple, Optional
from collections import defaultdict

import numpy as np
from rank_bm25 import BM25Okapi
from sqlalchemy.orm import Session

from app.models import Memory, MemoryEmbedding
from app.config import settings


class EmbeddingProvider:
    """向量嵌入提供者：本地 sentence-transformers，零外部 API"""

    _instance = None
    _model = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def _load_model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(settings.embedding_model)
        return self._model

    def embed(self, text: str) -> List[float]:
        model = self._load_model()
        return model.encode(text, normalize_embeddings=True).tolist()

    @property
    def model_name(self) -> str:
        return settings.embedding_model


class MemoryRetriever:
    """记忆检索器：BM25 + 向量相似度混合召回 + 陈旧度加权"""

    def __init__(self, db: Session):
        self.db = db
        self.embedder = EmbeddingProvider()

    def search(
        self,
        query: str,
        project_id: str = "default",
        team_id: str = "default",
        top_k: int = 5,
        memory_type: Optional[str] = None,
    ) -> List[Dict]:
        """混合召回检索"""
        # 1. 拉取候选记忆
        candidates = self._get_candidates(project_id, team_id, memory_type)
        if not candidates:
            return []

        # 2. BM25 打分
        bm25_scores = self._bm25_score(query, candidates)

        # 3. 向量相似度打分
        vector_scores = self._vector_score(query, candidates)

        # 4. 融合、去噪、按综合分排序
        combined: Dict[int, Dict] = {}
        for mem in candidates:
            bm25 = bm25_scores.get(mem.id, 0.0)
            vec = vector_scores.get(mem.id, 0.0)

            # 归一化到 0-1（BM25 本身已较稳定，向量余弦在 0-1）
            bm25_norm = min(1.0, bm25 / 10.0) if bm25 > 0 else 0.0
            vec_norm = max(0.0, vec)  # 已 normalize，直接截断

            final_score = (
                settings.bm25_weight * bm25_norm
                + settings.vector_weight * vec_norm
            )

            # 陈旧度惩罚：超过 staleness_days 后线性衰减，最多衰减 30%
            final_score = self._apply_staleness_penalty(mem, final_score)

            combined[mem.id] = {
                "memory": mem,
                "bm25_score": round(bm25_norm, 4),
                "vector_score": round(vec_norm, 4),
                "final_score": round(final_score, 4),
            }

        # 5. 排序并返回
        sorted_results = sorted(
            combined.values(),
            key=lambda x: x["final_score"],
            reverse=True,
        )[:top_k]

        return [
            {
                **r["memory"].to_dict(),
                "scores": {
                    "bm25": r["bm25_score"],
                    "vector": r["vector_score"],
                    "final": r["final_score"],
                },
            }
            for r in sorted_results
        ]

    def _get_candidates(
        self,
        project_id: str,
        team_id: str,
        memory_type: Optional[str] = None,
    ) -> List[Memory]:
        """获取候选记忆：优先最近 200 条，兼顾长期高频记忆"""
        query = self.db.query(Memory).filter(
            Memory.project_id == project_id,
            Memory.team_id == team_id,
        )
        if memory_type:
            query = query.filter(Memory.memory_type == memory_type)

        recent = (
            query.order_by(Memory.updated_at.desc())
            .limit(150)
            .all()
        )

        # 补充高频长期记忆
        hot = (
            self.db.query(Memory)
            .filter(
                Memory.project_id == project_id,
                Memory.team_id == team_id,
            )
            .order_by(Memory.usage_count.desc())
            .limit(50)
            .all()
        )

        seen = set()
        candidates = []
        for mem in recent + hot:
            if mem.id not in seen:
                seen.add(mem.id)
                candidates.append(mem)
        return candidates

    def _bm25_score(
        self,
        query: str,
        memories: List[Memory],
    ) -> Dict[int, float]:
        """基于记忆内容的 BM25 打分"""
        if not memories:
            return {}

        tokenized_corpus = [self._tokenize(m.content) for m in memories]
        bm25 = BM25Okapi(tokenized_corpus)
        tokenized_query = self._tokenize(query)
        scores = bm25.get_scores(tokenized_query)

        return {memories[i].id: float(scores[i]) for i in range(len(memories))}

    def _vector_score(
        self,
        query: str,
        memories: List[Memory],
    ) -> Dict[int, float]:
        """基于预计算嵌入的向量相似度打分"""
        if not memories:
            return {}

        query_vec = np.array(self.embedder.embed(query))
        results = {}

        memory_ids = [m.id for m in memories]
        embeddings = (
            self.db.query(MemoryEmbedding)
            .filter(MemoryEmbedding.memory_id.in_(memory_ids))
            .all()
        )

        emb_map = {e.memory_id: e for e in embeddings}

        for mem in memories:
            emb_record = emb_map.get(mem.id)
            if not emb_record or not emb_record.embedding_json:
                # 无嵌入时给 0 分，但允许 BM25 召回
                results[mem.id] = 0.0
                continue

            mem_vec = np.array(json.loads(emb_record.embedding_json))
            similarity = float(np.dot(query_vec, mem_vec))
            results[mem.id] = similarity

        return results

    def _apply_staleness_penalty(self, memory: Memory, score: float) -> float:
        """陈旧度惩罚：超过阈值后每多一天衰减少量"""
        from datetime import datetime, timedelta

        days = settings.memory_staleness_days
        age = (datetime.utcnow() - memory.updated_at).days
        if age <= days:
            return score

        excess_days = age - days
        penalty = min(0.3, excess_days * 0.005)  # 最多衰减 30%
        return score * (1 - penalty)

    def _tokenize(self, text: str) -> List[str]:
        """轻量英文分词 + 小写 + 去标"""
        text = re.sub(r"[^a-zA-Z0-9_\s]", " ", text)
        tokens = [t.lower() for t in text.split() if len(t) > 1]
        return tokens

    def refresh_embedding(self, memory_id: int) -> bool:
        """为单条记忆重新生成嵌入"""
        memory = self.db.query(Memory).filter(Memory.id == memory_id).first()
        if not memory:
            return False

        existing = (
            self.db.query(MemoryEmbedding)
            .filter(MemoryEmbedding.memory_id == memory_id)
            .first()
        )

        embedding = self.embedder.embed(memory.content)
        if existing:
            existing.embedding_json = json.dumps(embedding)
            existing.model_name = self.embedder.model_name
        else:
            new_emb = MemoryEmbedding(
                memory_id=memory_id,
                model_name=self.embedder.model_name,
                embedding_json=json.dumps(embedding),
            )
            self.db.add(new_emb)

        self.db.commit()
        return True
