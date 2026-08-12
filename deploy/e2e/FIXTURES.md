# Distributed regression fixtures

Place the following local-only input files under `.dev/fixture`. The directory
is intentionally ignored by Git because these reports may be large or contain
environment-specific data. Paths and filenames must match exactly.

| Scenario | Required path |
| --- | --- |
| `workspace_upload_image_understanding` | `.dev/fixture/测试图片.png` |
| `workspace_upload_image_understanding_speedbump` | `.dev/table/图像理解测试/e46127c2ce9f5c8cbb8e645d6023c5df.jpg` |
| `workspace_upload_read_table_document` | `.dev/fixture/table_test_document.docx` |
| `qiaozitang_monthly_query` | `.dev/fixture/大渡口1月巡查报告.docx` |
| `qiaozitang_monthly_query` | `.dev/fixture/大渡口2月巡查报告.docx` |
| `qiaozitang_monthly_query` | `.dev/fixture/大渡口3月巡查报告.docx` |
| `qiaozitang_monthly_query` | `.dev/fixture/大渡口4月巡查报告.docx` |
| `qiaozitang_monthly_query` | `.dev/fixture/大渡口5月巡查报告.docx` |
| `bridge_defect_extract_parallel` | `.dev/fixture/07 朝阳寺立交桥.doc` |
| `bridge_defect_extract_parallel` | `.dev/fixture/09 陈家湾桥.doc` |
| `persistent_disease_predict` | `.dev/fixture/周家堰桥+A级.pdf` |
| `persistent_disease_predict` | `.dev/fixture/069周家堰桥+C级.pdf` |
| `persistent_disease_predict` | `.dev/fixture/001周家堰桥+B级.doc` |
| `bridge_predict` | `.dev/fixture/12陈家阁大桥定期检测2018.10.docx` |
| `bridge_predict` | `.dev/fixture/03 陈家阁大桥.doc` |
| `bridge_predict` | `.dev/fixture/09 陈家阁立交.doc` |
| `bridge_predict` | `.dev/fixture/003 陈家阁大桥+C级.doc` |

The `serial_mode`, `pdf_generate`, `rag_search`, SQL, lifecycle, MQ, and generic
parallel scenarios do not require host fixture files.

Run only the fixture preflight with:

```powershell
.venv\Scripts\python.exe WorkBranch/backend/.test/run_e2e_tests.py `
  --no-server --suite distributed_regression --preflight-only
```

## 如何运行图像理解测试

减速带图像理解场景：`workspace_upload_image_understanding_speedbump`
（校验 analyze_image 工具被调用 + 回复包含 减速带/病害/缺损 关键词）。

### 本机（直连后端 8000）

```powershell
# 1. 启动后端（另开终端）
.venv\Scripts\python.exe WorkBranch/backend/run_server.py

# 2. 预检 fixture
$env:AGENTB_E2E_API_BASE_URL='http://127.0.0.1:8000'
.venv\Scripts\python.exe WorkBranch/backend/.test/run_e2e_tests.py `
  --scenario workspace_upload_image_understanding_speedbump --no-server --preflight-only

# 3. 运行场景
$env:AGENTB_E2E_API_BASE_URL='http://127.0.0.1:8000'
.venv\Scripts\python.exe WorkBranch/backend/.test/run_e2e_tests.py `
  --scenario workspace_upload_image_understanding_speedbump --no-server
```

### 部署机（默认 8152 nginx 网关）

```bash
python WorkBranch/backend/.test/run_e2e_tests.py --scenario workspace_upload_image_understanding_speedbump --preflight-only
python WorkBranch/backend/.test/run_e2e_tests.py --scenario workspace_upload_image_understanding_speedbump
```

### 说明

- 缺失 fixture 时控制台打印黄色高亮提示与操作指示（退出码 2），按提示放置文件后重试。
- 场景结束后绿色高亮打印 Agent 回复全文；PASS/FAIL 与最终结论均加粗高亮。
