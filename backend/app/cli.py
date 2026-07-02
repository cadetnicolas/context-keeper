"""
context-keeper-serve 命令入口
仅启动 HTTP REST API + Dashboard，不启动 MCP stdio
适合独立运行查看 Dashboard，或团队共享服务器部署
"""

import os
import sys


def serve():
    """启动 HTTP REST API 服务"""
    try:
        import uvicorn
        from app.models import init_db
        from app.config import settings

        print(f"[ContextKeeper] Starting HTTP server on http://{settings.host}:{settings.port}")
        print(f"[ContextKeeper] Dashboard: http://{settings.host}:{settings.port}/static/index.html")
        print(f"[ContextKeeper] API Docs:  http://{settings.host}:{settings.port}/docs")

        init_db()
        uvicorn.run(
            "app.main:app",
            host=settings.host,
            port=settings.port,
            reload=os.getenv("CK_DEBUG", "false").lower() == "true",
        )
    except KeyboardInterrupt:
        print("\n[ContextKeeper] Server stopped.")
        sys.exit(0)
