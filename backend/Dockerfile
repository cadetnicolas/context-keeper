FROM python:3.11-slim

WORKDIR /app

# 安装系统依赖（sentence-transformers 需要编译工具）
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

# 创建数据目录
RUN mkdir -p /data
ENV CK_DATABASE_URL=sqlite:///data/context_keeper.db

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
