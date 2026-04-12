# RAG API 文档

基础前缀: `/rag`

## 通用说明
- JSON 接口: `application/json`
- 上传接口: `multipart/form-data`
- 常见错误码: `400/404/409/413/500`

## 1. UI
- `GET /rag/` 返回文件管理页面

## 2. 知识库
- `GET /rag/api/knowledge-bases` 列表
- `POST /rag/api/knowledge-bases` 新增
  - body: `{ "name": "string", "description": "string?" }`
- `PUT /rag/api/knowledge-bases/{kb_id}` 修改
  - body: `{ "name": "string?", "description": "string?" }`
- `DELETE /rag/api/knowledge-bases/{kb_id}` 删除

## 3. 分类
- `GET /rag/api/categories/tree` 分类树
- `POST /rag/api/categories`
  - body: `{ "name": "string", "parent_id": 1 | null }`
- `PUT /rag/api/categories/{category_id}`
  - body: `{ "name": "string?", "parent_id": 1 | null }`
- `DELETE /rag/api/categories/{category_id}?mode=keep_docs|unbind_docs|recursive`

## 4. 文档
- `POST /rag/api/documents/upload`
  - form: `file`, `category_id?`, `kb_id?`
  - 上传后会触发 ingestion
- `GET /rag/api/documents?category_id=&keyword=&page=1&size=20`
- `GET /rag/api/documents/{document_id}`
- `PUT /rag/api/documents/{document_id}`
  - body: `{ "display_name": "string" }`
- `DELETE /rag/api/documents/{document_id}`

## 5. 删除任务
- `GET /rag/api/delete-jobs/{job_id}`
- `POST /rag/api/delete-jobs/{job_id}/retry`

## 6. 文档与分类绑定
- `POST /rag/api/documents/{document_id}/categories/{category_id}`
- `DELETE /rag/api/documents/{document_id}/categories/{category_id}`
- `PUT /rag/api/documents/{document_id}/primary-category/{category_id}`

## 7. 导入任务
- `GET /rag/api/jobs/{job_id}`
## 8. 文件系统兼容接口（DOCS 目录）
- `GET /rag/api/files?path=` 列目录
- `GET /rag/api/file?path=` 读文件
- `POST /rag/api/file` 创建文件或目录
  - body:
    ```json
    {
      "path": "raw/demo.txt",
      "type": "file",
      "content": "hello",
      "overwrite": false
    }
    ```
- `PUT /rag/api/file` 更新文件
  - body:
    ```json
    {
      "path": "raw/demo.txt",
      "content": "new content"
    }
    ```
- `DELETE /rag/api/file?path=` 删除文件或目录

## 9. 内部检索模型（Tool Schema）
`RAGSearchRequest` 关键字段:
- `query` (1~512)
- `top_k` (1~30)
- `mode` (`hybrid`/`vector`)
- `min_score` (0~1, 可选)
- `use_rerank` (bool)
- `rewrite_query` (bool)
- `filters` (可选)
- `kb_id` (可选)

`RAGSearchResponse` 关键字段:
- `ok`, `trace_id`, `query`, `items`, `debug`, `error`

## 10. 示例
### 创建知识库
```bash
curl -X POST "http://127.0.0.1:8000/rag/api/knowledge-bases" \
  -H "Content-Type: application/json" \
  -d '{"name":"产品文档库","description":"用于问答"}'
```

### 上传文档
```bash
curl -X POST "http://127.0.0.1:8000/rag/api/documents/upload" \
  -F "file=@D:/tmp/spec.pdf" \
  -F "kb_id=1"
```
