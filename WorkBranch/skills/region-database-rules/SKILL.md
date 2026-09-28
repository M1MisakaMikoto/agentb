---
name: region-database-rules
description: 本部署数据库的地区字段与编码规则。需要按地区查询/统计，或调用提交类工具（submit_facility_report、submit_facility_forecast 等）需要 regionId 时必读。
---

# 地区字段与编码规则

1. TB_Market 是通用的地区表：地区编码为 TB_Market.Id（如大渡口区、江北区等）；业务表（t_Bridge、t_Road、t_Footbridge 等）的 dq/region_id 字段与 submit_* 工具的 regionId 参数都使用同一套地区编码。
2. 需要地区编码时（按地区查询/统计、submit_facility_report / submit_facility_forecast 等工具需要 regionId 等），直接查询 TB_Market 获取即可，例如：
   SELECT Id, AdminAreaName FROM TB_Market WHERE AdminAreaName LIKE '%大渡口%'
   确认返回的 AdminAreaName 与目标地区一致后，使用返回的 Id。
3. 禁止直接用地区名称与 dq/region_id 字段或 regionId 参数做等值匹配（如 dq = '大渡口区'），否则会得到数量为 0 或参数错误等结果。
