"""
ContextKeeper MCP Server
兼容 Model Context Protocol (MCP) 标准，供 Cursor / Claude Code / Codex 调用。
使用 stdio 传输，提供两个核心工具：
  - contextkeeper_recall: 根据当前任务召回相关团队记忆
  - contextkeeper_remember: 将新决策/教训写入团队记忆

启动方式：
  python -m app.mcp.server
"""

import json
import sys
from typing import Any, Dict, List

from app.models import init_db, MemoryType, MemorySource, get_db
from app.memory.store import MemoryStore
from app.memory.retrieval import MemoryRetriever, EmbeddingProvider


class MCPStdioTransport:
    """简单的 MCP stdio 传输层"""

    def send(self, message: Dict[str, Any]):
        payload = json.dumps(message)
        sys.stdout.write(payload + "\n")
        sys.stdout.flush()

    def read(self) -> Dict[str, Any]:
        line = sys.stdin.readline()
        if not line:
            raise EOFError()
        return json.loads(line.strip())


class ContextKeeperMCPServer:
    def __init__(self):
        self.transport = MCPStdioTransport()
        init_db()

    def run(self):
        # 发送初始化通知
        self.transport.send({
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        })

        while True:
            try:
                request = self.transport.read()
            except EOFError:
                break

            response = self._handle(request)
            if response:
                self.transport.send(response)

    def _handle(self, request: Dict[str, Any]) -> Dict[str, Any] | None:
        method = request.get("method")
        req_id = request.get("id")

        if method == "initialize":
            return self._handle_initialize(req_id)

        if method == "tools/list":
            return self._handle_tools_list(req_id)

        if method == "tools/call":
            return self._handle_tools_call(req_id, request.get("params", {}))

        # 其他方法忽略或返回空
        return None

    def _handle_initialize(self, req_id) -> Dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "serverInfo": {
                    "name": "ContextKeeper",
                    "version": "0.1.0",
                },
                "capabilities": {
                    "tools": {},
                },
            },
        }

    def _handle_tools_list(self, req_id) -> Dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "tools": [
                    {
                        "name": "contextkeeper_recall",
                        "description": "Recall relevant team memories for the current coding task. Call this when starting a new task or conversation to load project context.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "query": {
                                    "type": "string",
                                    "description": "Description of the current task or question",
                                },
                                "project_id": {
                                    "type": "string",
                                    "description": "Project identifier",
                                    "default": "default",
                                },
                                "team_id": {
                                    "type": "string",
                                    "description": "Team identifier",
                                    "default": "default",
                                },
                                "top_k": {
                                    "type": "integer",
                                    "description": "Number of memories to recall",
                                    "default": 5,
                                },
                            },
                            "required": ["query"],
                        },
                    },
                    {
                        "name": "contextkeeper_remember",
                        "description": "Store a new team memory (decision, lesson, preference, architecture note). Call this when the team makes an important decision or learns a lesson.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "content": {
                                    "type": "string",
                                    "description": "The memory content to store",
                                },
                                "memory_type": {
                                    "type": "string",
                                    "enum": ["decision", "lesson", "fact", "preference", "todo", "architecture"],
                                    "description": "Type of memory",
                                    "default": "fact",
                                },
                                "project_id": {
                                    "type": "string",
                                    "description": "Project identifier",
                                    "default": "default",
                                },
                                "team_id": {
                                    "type": "string",
                                    "description": "Team identifier",
                                    "default": "default",
                                },
                                "created_by": {
                                    "type": "string",
                                    "description": "User who created this memory",
                                    "default": "agent",
                                },
                                "tags": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "Optional tags",
                                    "default": [],
                                },
                                "related_files": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "Related file paths",
                                    "default": [],
                                },
                            },
                            "required": ["content"],
                        },
                    },
                ],
            },
        }

    def _handle_tools_call(self, req_id, params: Dict[str, Any]) -> Dict[str, Any]:
        name = params.get("name")
        arguments = params.get("arguments", {})

        db = next(get_db())
        try:
            if name == "contextkeeper_recall":
                result = self._tool_recall(db, arguments)
            elif name == "contextkeeper_remember":
                result = self._tool_remember(db, arguments)
            else:
                result = {"error": f"Unknown tool: {name}"}
        finally:
            db.close()

        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(result, ensure_ascii=False, indent=2),
                    }
                ],
            },
        }

    def _tool_recall(self, db, args: Dict[str, Any]) -> Dict[str, Any]:
        query = args.get("query", "")
        project_id = args.get("project_id", "default")
        team_id = args.get("team_id", "default")
        top_k = int(args.get("top_k", 5))

        retriever = MemoryRetriever(db)
        results = retriever.search(
            query=query,
            project_id=project_id,
            team_id=team_id,
            top_k=top_k,
        )

        # 记录召回
        store = MemoryStore(db)
        for r in results:
            store.record_recall(r["id"])

        return {
            "success": True,
            "query": query,
            "count": len(results),
            "memories": results,
        }

    def _tool_remember(self, db, args: Dict[str, Any]) -> Dict[str, Any]:
        content = args.get("content", "")
        if not content:
            return {"success": False, "error": "content is required"}

        memory_type_str = args.get("memory_type", "fact")
        memory_type = MemoryType(memory_type_str)

        project_id = args.get("project_id", "default")
        team_id = args.get("team_id", "default")
        created_by = args.get("created_by", "agent")
        tags = args.get("tags", [])
        related_files = args.get("related_files", [])

        # 生成嵌入
        embedder = EmbeddingProvider()
        embedding = embedder.embed(content)

        store = MemoryStore(db)
        memory = store.add_memory(
            content=content,
            memory_type=memory_type,
            source=MemorySource.AGENT,
            project_id=project_id,
            team_id=team_id,
            created_by=created_by,
            tags=tags,
            related_files=related_files,
            embedding=embedding,
            model_name=embedder.model_name,
        )

        return {
            "success": True,
            "memory_id": memory.id,
            "content": memory.content,
            "memory_type": memory.memory_type.value,
        }


def main():
    server = ContextKeeperMCPServer()
    server.run()


if __name__ == "__main__":
    main()
