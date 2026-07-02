"""
记忆检索器：BM25 + 向量混合召回 + 陈旧度加权

向量化引擎：onnxruntime（无 PyTorch 依赖）
模型文件：backend/models/all-MiniLM-L6-v2/（已内置，无需联网下载）
"""

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional

import numpy as np
from rank_bm25 import BM25Okapi
from sqlalchemy.orm import Session

from app.models import Memory, MemoryEmbedding
from app.config import settings

# 内置模型目录（与 backend/ 同级，打包进插件）
_MODEL_DIR = Path(__file__).parent.parent.parent / "models" / "all-MiniLM-L6-v2"


class EmbeddingProvider:
    """向量嵌入提供者：onnxruntime + 内置模型，完全离线，无需 PyTorch"""

    _instance = None
    _session = None
    _tokenizer = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def _load(self):
        """惰性加载模型（仅在第一次调用 embed 时加载，耗时约 0.5s）"""
        if self._session is not None:
            return

        import onnxruntime as ort
        from tokenizers import Tokenizer

        model_path = _MODEL_DIR / "model.onnx"
        tokenizer_path = _MODEL_DIR / "tokenizer.json"

        if not model_path.exists():
            raise FileNotFoundError(
                f"Bundled ONNX model not found at {model_path}. "
                "Please reinstall the ContextKeeper extension."
            )

        self._tokenizer = Tokenizer.from_file(str(tokenizer_path))
        self._tokenizer.enable_padding(pad_id=0, pad_token="[PAD]", length=128)
        self._tokenizer.enable_truncation(max_length=128)

        opts = ort.SessionOptions()
        opts.log_severity_level = 3          # 静默运行
        opts.intra_op_num_threads = 2
        self._session = ort.InferenceSession(
            str(model_path),
            sess_options=opts,
            providers=["CPUExecutionProvider"],
        )

    def embed(self, text: str) -> List[float]:
        """生成 384 维归一化向量"""
        self._load()

        encoded = self._tokenizer.encode(text)

        input_ids       = np.array([encoded.ids],              dtype=np.int64)
        attention_mask  = np.array([encoded.attention_mask],   dtype=np.int64)
        token_type_ids  = np.zeros_like(input_ids)

        outputs = self._session.run(
            None,
            {
                "input_ids":      input_ids,
                "attention_mask": attention_mask,
                "token_type_ids": token_type_ids,
            },
        )

        # Mean pooling（对有效 token 取均值）
        token_embeddings = outputs[0].astype(np.float32)   # (1, seq, 384)
        mask = attention_mask[:, :, np.newaxis].astype(np.float32)
        sum_emb  = (token_embeddings * mask).sum(axis=1)
        sum_mask = mask.sum(axis=1).clip(min=1e-9)
        mean_emb = sum_emb / sum_mask                       # (1, 384)

        # L2 归一化
        norm = np.linalg.norm(mean_emb, axis=1, keepdims=True).clip(min=1e-9)
        return (mean_emb / norm)[0].tolist()

    @property
    def model_name(self) -> str:
        return "all-MiniLM-L6-v2-onnx"


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
        candidates = self._get_candidates(project_id, team_id, memory_type)
        if not candidates:
            return []

        bm25_scores   = self._bm25_score(query, candidates)
        vector_scores = self._vector_score(query, candidates)

        combined: Dict[int, Dict] = {}
        for mem in candidates:
            bm25 = bm25_scores.get(mem.id, 0.0)
            vec  = vector_scores.get(mem.id, 0.0)

            bm25_norm = min(1.0, bm25 / 10.0) if bm25 > 0 else 0.0
            vec_norm  = max(0.0, vec)

            final_score = (
                settings.bm25_weight * bm25_norm
                + settings.vector_weight * vec_norm
            )
            final_score = self._apply_staleness_penalty(mem, final_score)

            combined[mem.id] = {
                "memory":       mem,
                "bm25_score":   round(bm25_norm, 4),
                "vector_score": round(vec_norm,   4),
                "final_score":  round(final_score, 4),
            }

        sorted_results = sorted(
            combined.values(),
            key=lambda x: x["final_score"],
            reverse=True,
        )[:top_k]

        return [
            {
                **r["memory"].to_dict(),
                "scores": {
                    "bm25":   r["bm25_score"],
                    "vector": r["vector_score"],
                    "final":  r["final_score"],
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
        query = self.db.query(Memory).filter(
            Memory.project_id == project_id,
            Memory.team_id == team_id,
        )
        if memory_type:
            query = query.filter(Memory.memory_type == memory_type)

        recent = (
            query.order_by(Memory.updated_at.desc()).limit(150).all()
        )
        hot = (
            self.db.query(Memory)
            .filter(Memory.project_id == project_id, Memory.team_id == team_id)
            .order_by(Memory.usage_count.desc())
            .limit(50)
            .all()
        )

        seen, candidates = set(), []
        for mem in recent + hot:
            if mem.id not in seen:
                seen.add(mem.id)
                candidates.append(mem)
        return candidates

    def _bm25_score(self, query: str, memories: List[Memory]) -> Dict[int, float]:
        if not memories:
            return {}
        corpus = [self._tokenize(m.content) for m in memories]
        bm25   = BM25Okapi(corpus)
        scores = bm25.get_scores(self._tokenize(query))
        return {memories[i].id: float(scores[i]) for i in range(len(memories))}

    def _vector_score(self, query: str, memories: List[Memory]) -> Dict[int, float]:
        if not memories:
            return {}

        query_vec = np.array(self.embedder.embed(query))
        results   = {}

        memory_ids = [m.id for m in memories]
        embeddings = (
            self.db.query(MemoryEmbedding)
            .filter(MemoryEmbedding.memory_id.in_(memory_ids))
            .all()
        )
        emb_map = {e.memory_id: e for e in embeddings}

        for mem in memories:
            rec = emb_map.get(mem.id)
            if not rec or not rec.embedding_json:
                results[mem.id] = 0.0
                continue
            mem_vec    = np.array(json.loads(rec.embedding_json))
            results[mem.id] = float(np.dot(query_vec, mem_vec))

        return results

    def _apply_staleness_penalty(self, memory: Memory, score: float) -> float:
        days = settings.memory_staleness_days
        age  = (datetime.utcnow() - memory.updated_at).days
        if age <= days:
            return score
        penalty = min(0.3, (age - days) * 0.005)
        return score * (1 - penalty)

    def _tokenize(self, text: str) -> List[str]:
        text = re.sub(r"[^a-zA-Z0-9_\s]", " ", text)
        return [t.lower() for t in text.split() if len(t) > 1]

    def refresh_embedding(self, memory_id: int) -> bool:
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
            existing.model_name     = self.embedder.model_name
        else:
            self.db.add(MemoryEmbedding(
                memory_id      = memory_id,
                model_name     = self.embedder.model_name,
                embedding_json = json.dumps(embedding),
            ))

        self.db.commit()
        return True
