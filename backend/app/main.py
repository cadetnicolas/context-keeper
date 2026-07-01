from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from app.models import init_db
from app.api.routes import router as api_router
from app.config import settings


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
        description="团队级 AI 编码 Agent 共享记忆层",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 挂载静态资源（Dashboard）
    app.mount("/static", StaticFiles(directory="app/dashboard/static"), name="static")

    # 注册 API 路由
    app.include_router(api_router)

    @app.get("/")
    def root():
        return {
            "service": settings.app_name,
            "version": settings.version,
            "docs": "/docs",
            "dashboard": "/static/index.html",
        }

    @app.on_event("startup")
    def on_startup():
        init_db()

    return app


app = create_app()
