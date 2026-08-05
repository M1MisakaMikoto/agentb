# Distributed regression fixtures

Place the following local-only input files under `.dev/fixture`. The directory
is intentionally ignored by Git because these reports may be large or contain
environment-specific data. Paths and filenames must match exactly.

| Scenario | Required path |
| --- | --- |
| `workspace_upload_image_understanding` | `.dev/fixture/测试图片.png` |
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
