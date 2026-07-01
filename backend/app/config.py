import os
from pathlib import Path
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "ContextKeeper"
    version: str = "0.1.0"
    debug: bool = os.getenv("CK_DEBUG", "false").lower() == "true"

    # 数据库配置：默认本地 SQLite，团队版可切换 PostgreSQL + pgvector
    database_url: str = os.getenv(
        "CK_DATABASE_URL",
        f"sqlite:///{Path.home()}/.context-keeper/context_keeper.db"
    )

    # 向量模型配置
    embedding_model: str = os.getenv(
        "CK_EMBEDDING_MODEL",
        "sentence-transformers/all-MiniLM-L6-v2"
    )
    embedding_dim: int = int(os.getenv("CK_EMBEDDING_DIM", "384"))

    # 混合召回权重：0=纯BM25，1=纯向量
    vector_weight: float = float(os.getenv("CK_VECTOR_WEIGHT", "0.6"))
    bm25_weight: float = float(os.getenv("CK_BM25_WEIGHT", "0.4"))

    # 记忆陈旧度：默认 90 天后开始衰减
    memory_staleness_days: int = int(os.getenv("CK_MEMORY_STALENESS_DAYS", "90"))

    # 团队同步：默认关闭，可配置为 git 或 api
    sync_mode: str = os.getenv("CK_SYNC_MODE", "none")  # none | git | api
    sync_remote_url: str = os.getenv("CK_SYNC_REMOTE_URL", "")

    # API 服务配置
    host: str = os.getenv("CK_HOST", "127.0.0.1")
    port: int = int(os.getenv("CK_PORT", "8000"))

    class Config:
        env_prefix = "CK_"


settings = Settings()
