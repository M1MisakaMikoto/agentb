# RAG 模块 README

## 1. 模块简介
`rag/` 是项目的检索增强模块，负责:
- 文档/目录管理（知识库、分类、文档）
- 文档 ingestion（切分、向量化、索引）
- 检索与重排（RAG search）

在当前项目中，`backend/app.py` 已直接挂载 RAG 路由，因此启动 backend 时会同时提供 RAG HTTP 能力。

## 2. 目录结构（核心）
- `controller/`: API 入口与适配器
- `service/`: 业务逻辑（RAG_service、ingestion 等）
- `DAO/`: 数据访问（元数据与向量库）
- `tool_schema/`: 请求/响应模型定义
- `tools/`: Tool 运行封装
- `ui/`: 文件管理页面

## 3. 运行方式
### 方式 A（推荐）
在 `agentb/WorkBranch` 根目录执行:
```bash
start-dev.bat
```
该方式会启动前端+backend，backend 内已包含 RAG 路由。

### 方式 B（仅后端）
```bash
python run_dev.py --backend-only
```

## 4. API 入口
- 基础前缀: `/rag`
- 详细接口文档见: `rag/api.md`

## 5. 关键数据与存储
- 元数据库: `rag/file_meta.sqlite3`
- 其他元数据库: `rag/rag_metadata.sqlite3`
- 文档根目录: `DOCS/`（由 `file_controller.py` 管理）

## 6. 检索流程（简述）
1. 上传文档 `/rag/api/documents/upload`
2. 触发 ingestion，写入向量与元数据
3. 查询时构造 `RAGSearchRequest`
4. `RAG_service` 执行召回、融合评分、可选 rerank
5. 返回 `RAGSearchResponse`

## 7. 常见问题
### 7.1 上传后检索不到
- 检查 ingestion 任务状态 `/rag/api/jobs/{job_id}`
- 检查文档是否绑定到目标知识库/分类

### 7.2 接口 413
- 单文件大小超过 100MB（上传限制）

### 7.3 404/409
- 404 常见于资源不存在
- 409 常见于名称冲突或唯一约束冲突

## 8. 开发建议
- 新增接口时同步更新 `rag/api.md`
- 变更 `tool_schema` 时同步更新调用方说明
- 保持 DTO/VO 与实际响应一致
