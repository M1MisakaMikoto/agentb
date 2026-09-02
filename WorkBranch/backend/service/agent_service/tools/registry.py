from typing import Dict, List, Optional, Callable, Any
from dataclasses import dataclass


@dataclass
class ToolDefinition:
    """工具定义"""
    name: str
    description: str
    params: str
    category: str = "general"
    executor: Optional[Callable] = None


class ToolRegistry:
    """工具注册表"""

    _instance = None
    _tools: Dict[str, ToolDefinition] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def register(cls, tool: ToolDefinition) -> None:
        cls._tools[tool.name] = tool

    @classmethod
    def get(cls, name: str) -> Optional[ToolDefinition]:
        return cls._tools.get(name)

    @classmethod
    def get_all(cls) -> Dict[str, ToolDefinition]:
        return cls._tools.copy()

    @classmethod
    def get_by_category(cls, category: str) -> List[ToolDefinition]:
        return [t for t in cls._tools.values() if t.category == category]

    @classmethod
    def get_tool_prompt(cls, allowed_tools: List[str]) -> str:
        if not allowed_tools:
            return "当前没有可用工具。"

        lines = ["## 工具列表"]
        for name in allowed_tools:
            tool = cls._tools.get(name)
            if tool and tool.params:
                lines.append(tool.params)

        return "\n".join(lines)


ALL_TOOLS = {
    "read_file": {
        "name": "read_file",
        "description": "读取文件内容",
        "params": 'read_file:{"file_path":"(文件路径)","start_line":"(第几行开始读，本参数可不填)","end_line":"(第几行结束读，本参数可不填)"}'
    },
    "write_file": {
        "name": "write_file",
        "description": "写入文件",
        "params": 'write_file:{"file_path":"(文件路径)","content":"(写入内容)","mode":"(write或append，本参数可不填)"}'
    },
    "delete_file": {
        "name": "delete_file",
        "description": "删除文件或目录",
        "params": 'delete_file:{"file_path":"(文件路径)"}'
    },
    "list_dir": {
        "name": "list_dir",
        "description": "列出目录内容",
        "params": 'list_dir:{"directory":"(目录路径，本参数可不填)","recursive":"(是否递归，本参数可不填)","show_hidden":"(是否显示隐藏文件，本参数可不填)"}'
    },
    "create_dir": {
        "name": "create_dir",
        "description": "创建目录",
        "params": 'create_dir:{"directory":"(目录路径)"}'
    },
    "explore_code": {
        "name": "explore_code",
        "description": "探索代码库",
        "params": 'explore_code:{"query":"(查询内容)","search_type":"(file/code/structure，本参数可不填)","file_pattern":"(文件匹配模式，本参数可不填)","max_results":"(最多返回多少条，本参数可不填)"}'
    },
    "explore_internet": {
        "name": "explore_internet",
        "description": "搜索互联网获取信息",
        "params": 'explore_internet:{"query":"(搜索内容)","max_results":"(最多返回多少条，本参数可不填)"}'
    },
    "thinking": {
        "name": "thinking",
        "description": "思考工具，用于分析问题、梳理思路",
        "params": 'thinking:{"task_description":"(思考任务描述，例如：分析xxx的实现方案)"}'
    },
    "chat": {
        "name": "chat",
        "description": "与用户对话工具，用于向用户输出最终回复",
        "params": 'chat:{"description": "(必填)本次回复的主题/话题，简洁描述要回复什么)"}'
    },
    "call_explore_agent": {
        "name": "call_explore_agent",
        "description": "调用探索子代理",
        "params": 'call_explore_agent:{"task_description":"(交给探索子代理的任务描述)"}'
    },
    "call_review_agent": {
        "name": "call_review_agent",
        "description": "调用审查子代理",
        "params": 'call_review_agent:{"task_description":"(交给审查子代理的任务描述)"}'
    },
    "call_prediction_agent": {
        "name": "call_prediction_agent",
        "description": "调用桥梁检测预测子代理，用于BCI计算、趋势预测和规范查询",
        "params": 'call_prediction_agent:{"task_description":"(交给预测子代理的任务描述，例如：基于历史检测报告计算BCI并预测未来状况)"}'
    },
    "call_plan_agent": {
        "name": "call_plan_agent",
        "description": "调用计划子代理生成/重新生成执行计划",
        "params": 'call_plan_agent:{"task_description":"(交给计划子代理的任务描述，可附上之前相关文件说明)","feedback":"(可选：leader 对上一版计划的修改意见，提供时按意见重新生成)"}'
    },
    "ask_user_question": {
        "name": "ask_user_question",
        "description": "向用户提问并等待回复（交互式；awaiting 期间图暂停）",
        "params": 'ask_user_question:{"question":"(必填)要问用户的问题","options":"(可选)选项列表","context":"(可选)附带摘要"}'
    },
    "update_todo": {
        "name": "update_todo",
        "description": "用完整列表覆盖更新 TODO 状态",
        "params": 'update_todo:{"todos": ["(todo内容1)", "(todo内容2)"...],"doingIdx": (当前todo进行到第几项了，从0开始数)}'
    },
    "rag_search": {
        "name": "rag_search",
        "description": "在知识库中进行语义检索",
        "params": 'rag_search:{"query":"(查询内容)","kb_ids":"(知识库ID列表，本参数可不填)","top_k":"(返回条数，本参数可不填)","min_score":"(最低相关度，本参数可不填)"}'
    },
    "list_workspace_files": {
        "name": "list_workspace_files",
        "description": "列出当前工作区内所有文件和目录",
        "params": 'list_workspace_files:{}'
    },
    "get_workspace_info": {
        "name": "get_workspace_info",
        "description": "获取当前工作区信息",
        "params": 'get_workspace_info:{}'
    },
    "search_files": {
        "name": "search_files",
        "description": "在工作区内搜索文件",
        "params": 'search_files:{"pattern":"(文件名模式，支持通配符*)"}'
    },
    "read_document": {
        "name": "read_document",
        "description": "[兼容]读取PDF、Word、Excel文档内容（推荐使用document工具）",
        "params": 'read_document:{"file_path":"(文档路径)","start_idx":"(起始索引，默认0)","max_length":"(最大长度，默认10000)","include_metadata":"(含元数据，默认true)"}'
    },
    "document": {
        "name": "document",
        "description": "统一文档操作工具(类似fopen)，支持PDF/DOC/DOCX/XLS/XLSX的读写追加修改。r=读 w=写 a=追加 u=修改 s=搜索(grep)。重要提示：文件过大时(如>1MB)，请使用 s 操作配合 pattern 参数搜索关键词(如'病害'、'裂缝'、'BCI')快速定位目标内容，而不要逐段读取整文件",
        "params": 'document:{"operation":"(必填)r|w|a|u|s","file_path":"(必填)文档路径","content":"(文本内容, PDF/Word用)","data":"(JSON数组, Excel用, 如{\\"Sheet1\\":[[行1],[行2]]})","target":"(update定位, 如段落索引/单元格A1)","field":"(字段类型, paragraph/metadata/cell)","metadata":"(文档元数据, {author,title})","start_idx":"(r读取起点；s搜索起点，默认0；后续页传上次next_start_idx)","max_length":"(最大读取长度)","include_metadata":"(是否包含元数据)","pattern":"(搜索正则，用于 s 操作)","case_sensitive":"(大小写敏感，默认false)","context":"(匹配上下文行数，默认2)","max_results":"(最大结果行数，默认50)"}'
    },
    "sql_query": {
        "name": "sql_query",
        "description": "执行只读 SQL 查询或结构探查；支持 query(SELECT)、show_databases(列出数据库)、show_tables(列出表)、describe(查看表结构)、show_create(查看建表语句)，以及面向设施表（自动发现或指定）索引查询的 facility_trend/facility_report",
        "params": 'sql_query:{"mode":"(query|show_databases|show_tables|describe|show_create|facility_trend|facility_report，必填)","query":"(query 模式必填；其他模式忽略；不同设施表的字段映射: 桥梁表t_Bridge/mc=名称, id=ID；道路表t_Road/mc=名称；人行桥表t_Footbridge/mc=名称)","database":"(数据库名称，可选；不传或传 default 时使用配置中的默认数据库；当前默认数据库通常为 BTManager；show_databases 模式忽略)","table":"(表名；describe/show_create 模式必填；query/facility_* 模式可用于指定设施表)","limit":"(仅 query/facility_* 模式生效，默认100，最大1000)","table_name":"(facility_* 模式可选，指定设施表名；不指定则自动发现)","start_time":"(facility_* 可选，例 2026-05-01 00:00:00)","end_time":"(facility_* 可选，建议与start_time一起传)","device_type_name":"(facility_* 可选，设施类型如桥梁/道路/人行桥，用于自动发现表)","device_id":"(facility_* 可选，等值过滤)","content_keyword":"(facility_* 可选，LIKE过滤)","group_by":"(facility_trend 可选: hour|day|device_type|device)"}'
    },
    # --- Prediction Tools ---
    "calculate_bci": {
        "name": "calculate_bci",
        "description": "计算桥梁技术状况指数(BCI)，基于CJJ 99-2017加权扣分法",
        "params": 'calculate_bci:{"historical_reports":"(历史报告列表)","target_year":"(目标年份，默认2024)","standard":"(规范标准，默认CJJ 99-2017)"}'
    },
    "predict_trend": {
        "name": "predict_trend",
        "description": "预测桥梁退化趋势，支持线性回归/多项式/指数三种模型",
        "params": 'predict_trend:{"historical_bci":"(BCI历史数据列表，格式示例：[{\"year\":2018,\"bci\":81.8},{\"year\":2020,\"bci\":78.5}]，必填)","method":"(预测方法：linear_regression/polynomial/exponential，默认linear_regression)"}'
    },
    "query_standard": {
        "name": "query_standard",
        "description": "查询桥梁检测行业规范(CJJ 99-2017/CJJ/T 233-2015/JTG H11-2004)",
        "params": 'query_standard:{"bci_score":"(BCI分数，用于等级判定)","standard_version":"(规范版本)","query_type":"(查询类型：general/grade/formula/maintenance)"}'
    },
    "bridge_report_parser": {
        "name": "bridge_report_parser",
        "description": "桥梁检测报告解析 - 从历史报告(.docx/.doc)提取BCI数据、部件评分、病害描述，同时保留原报告格式供生成预测报告参考",
        "params": 'bridge_report_parser:{"file_paths":"(必填)历史报告文件路径列表，如[\"报告2018.docx\",\"报告2020.docx\"]","include_format_template":"(可选)是否包含原报告格式，默认true"}'
    },
    # --- AI 研判工具 ---
    "submit_ai_judgment_issue": {
        "name": "submit_ai_judgment_issue",
        "description": "提交 AI 研判问题 - 将设施问题提交到 AI 研判系统，等待 AI 分析并返回研判结果",
        "params": 'submit_ai_judgment_issue:{"facilityId":"(设施ID，必填)","facilityName":"(设施名称，必填)","title":"(问题标题，必填)","description":"(问题描述，可选)","regionId":"(区域ID，必填，从元数据中获取)"}'
    },
    # --- 设施研判报告工具 ---
    "submit_facility_report": {
        "name": "submit_facility_report",
        "description": "生成设施研判报告 - 将检测报告(DOCX)上传后自动生成研判报告。注意：若尚无DOCX文件，先用 document w 工具生成DOCX（file_path 必须用 .docx 结尾，传入Markdown内容即可自动转换为DOCX），再传 reportFile 给本工具。",
        "params": 'submit_facility_report:{"reportName":"(报告名称，必填)","facilityId":"(设施ID，必填)","facilityName":"(设施名称，必填)","reportFile":"(报告DOCX文件本地路径，必填)","regionId":"(区域ID，必填，从元数据中获取)"}'
    },
    "submit_facility_forecast": {
        "name": "submit_facility_forecast",
        "description": "提交设施预测报告 - 将桥梁预测分析结果(DOCX)上传到系统。调用 POST /v1/facility/forecast/report 接口。若尚无DOCX文件，先用 document w 工具生成DOCX（file_path 必须用 .docx 结尾，传入Markdown内容即可自动转换为DOCX）。",
        "params": 'submit_facility_forecast:{"facilityId":"(设施ID，必填)","predictYear":"(预测年份，必填)","reportFile":"(报告DOCX文件本地路径，必填)","facilityName":"(设施名称，可选)","predictedHealthScore":"(预测健康分数，可选)","predictedRiskLevel":"(风险等级，可选: 高/中/低)","summary":"(预测结论摘要，可选)"}'
    },
    # --- 日常巡查记录工具 ---
    "submit_dailypatrol_record": {
        "name": "submit_dailypatrol_record",
        "description": "提交日常巡查记录 - 将日常巡查任务记录（Agent回写版本）提交到后端系统。支持主表信息+检测指标明细(dtoList)一并提交。",
        "params": 'submit_dailypatrol_record:{'
                   '"title":"(巡查标题，必填，max100)",'
                   '"xcdate":"(巡查日期-时间戳毫秒，必填)",'
                   '"typeid":"(设施类型，必填)",'
                   '"typename":"(设施类型名称，必填，max100)",'
                   '"nameid":"(设施名称ID，必填)",'
                   '"ssname":"(设施名称，必填，max100)",'
                   '"xcunitname":"(巡查单位名称，必填，max100)",'
                   '"dq":"(地区，必填)",'
                   '"isdjrw":"(是否定检任务，必填)",'
                   '"isyhby":"(是否需要养护保养，必填)",'
                   '"xcperson":"(巡查人姓名，必填)",'
                   '"xcphone":"(巡查人电话，必填)",'
                   '"xcunitid":"(巡查单位ID，必填)",'
                   '"userId":"(用户ID，可选)",'
                   '"status":"(保养状态，可选)",'
                   '"remark":"(说明，可选)",'
                   '"source":"(数据来源，可选)",'
                   '"dzdtisvalid":"(坐标是否有效距离，可选)",'
                   '"dzdt":"(电子地图坐标，可选)",'
                   '"xcbegintime":"(开始时间戳，可选)",'
                   '"xcendtime":"(结束时间戳，可选)",'
                   '"checktodate":"(截止日期时间戳，可选)",'
                   '"photoannex":"(照片附件，可选)",'
                   '"qrdzdt":"(二维码巡查坐标，可选)",'
                   '"reveal":"(是否展示0/1，可选)",'
                   '"videoModel":"(是否视频巡查1/0，可选)",'
                   '"dtoList":"(检测指标明细列表，可选)"'
                   '}'
    },
    # --- 图像理解工具 ---
    "analyze_image": {
        "name": "analyze_image",
        "description": "分析工作区图片（视觉理解） - 读取工作区图片并调用视觉模型返回文本分析结果",
        "params": 'analyze_image:{"image_path":"(工作区相对路径，必填，来自用户消息 [图片: 文件名])","task":"(分析要求，必填)"}'
    },
}

# get_tool_prompt injects params only, so keep the search-to-read contract here.
ALL_TOOLS["document"]["params"] += (
    " For DOC/DOCX operation r, prefer 1-indexed line pagination with offset "
    "(default 1) and limit (default 2000, max 2000). Read results use line-numbered "
    "content in LINE_NUM|CONTENT format and return JSON metadata including "
    "total_lines, file_size, truncated, extracted_document, and hint. Use the "
    "returned next offset to continue. Character pagination with start_idx and "
    "max_length remains supported and stops at a complete line when possible; "
    "those results also include read_range and next_start_idx."
    " Search returns one result per matching line in document order and groups all "
    "matches on that line in occurrences. Results include pattern, snippet, character "
    "offsets, segment number, total_matches, returned_matches, next_start_idx, and "
    "read_hint. Pass next_start_idx to operation s start_idx for the next page, or pass "
    "read_hint.start_idx and read_hint.max_length to operation r for targeted reading."
)


FILE_TOOLS = {"read_file", "write_file", "delete_file", "list_dir", "create_dir"}
EXPLORE_TOOLS = {"explore_code", "explore_internet"}
SUBAGENT_TOOLS = {
    "call_explore_agent",
    "call_review_agent",
    "call_prediction_agent",
    "call_plan_agent",
}
TODO_TOOLS = {"update_todo"}
RAG_TOOLS = {"rag_search"}
WORKSPACE_TOOLS = {"list_workspace_files", "get_workspace_info", "search_files"}
DOCUMENT_TOOLS = {"document", "read_document"}
SQL_TOOLS = {"sql_query"}
PREDICTION_TOOLS = {"calculate_bci", "predict_trend", "query_standard", "bridge_report_parser"}
AI_JUDGMENT_TOOLS = {"submit_ai_judgment_issue"}
FACILITY_REPORT_TOOLS = {"submit_facility_report", "submit_facility_forecast"}
DAILYPATROL_TOOLS = {"submit_dailypatrol_record"}
