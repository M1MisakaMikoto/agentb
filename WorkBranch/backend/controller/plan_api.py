"""
Plan API - 计划查询接口

提供计划查看等 API
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional

from singleton import get_workspace_service
from service.agent_service.service import WorkspaceService
from service.agent_service.service.plan_file_service import plan_file_service
from controller.VO.result import Result


router = APIRouter(prefix="/plan", tags=["plan"])


class UpdatePlanRequest(BaseModel):
    workspace_id: str
    plan_content: str


@router.get("/{workspace_id}")
def get_plan(
    workspace_id: str,
    service: WorkspaceService = Depends(get_workspace_service),
) -> Result:
    """
    获取计划内容
    """
    info = service.get_workspace_info(workspace_id)
    if not info:
        raise HTTPException(status_code=404, detail="Workspace not found")
    
    session_id = info.get("session_id", "default")
    
    plan_data = plan_file_service.read_plan(session_id, workspace_id)
    
    if not plan_data.get("success"):
        return Result.success(data={
            "exists": False,
            "content": None,
            "status": None
        })
    
    return Result.success(data={
        "exists": True,
        "content": plan_data.get("content"),
        "status": plan_data.get("meta", {}).get("status"),
        "steps": plan_data.get("meta", {}).get("steps", []),
        "created_at": plan_data.get("meta", {}).get("created_at"),
        "approved_at": plan_data.get("meta", {}).get("approved_at")
    })


@router.post("/update")
def update_plan(
    request: UpdatePlanRequest,
    service: WorkspaceService = Depends(get_workspace_service),
) -> Result:
    """
    更新计划内容（用户编辑后）
    """
    info = service.get_workspace_info(request.workspace_id)
    if not info:
        raise HTTPException(status_code=404, detail="Workspace not found")
    
    session_id = info.get("session_id", "default")
    
    result = plan_file_service.update_plan(
        session_id=session_id,
        workspace_id=request.workspace_id,
        plan_content=request.plan_content
    )
    
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error"))
    
    return Result.success(data={
        "message": result.get("message")
    })


@router.get("/{workspace_id}/status")
def get_plan_status(
    workspace_id: str,
    service: WorkspaceService = Depends(get_workspace_service),
) -> Result:
    """
    获取计划状态
    """
    info = service.get_workspace_info(workspace_id)
    if not info:
        raise HTTPException(status_code=404, detail="Workspace not found")
    
    session_id = info.get("session_id", "default")
    
    status = plan_file_service.get_plan_status(session_id, workspace_id)
    
    return Result.success(data=status)


@router.delete("/{workspace_id}")
def delete_plan(
    workspace_id: str,
    service: WorkspaceService = Depends(get_workspace_service),
) -> Result:
    """
    删除计划文件
    """
    info = service.get_workspace_info(workspace_id)
    if not info:
        raise HTTPException(status_code=404, detail="Workspace not found")
    
    session_id = info.get("session_id", "default")
    
    result = plan_file_service.delete_plan(session_id, workspace_id)
    
    return Result.success(data={
        "deleted": result.get("deleted"),
        "message": result.get("message")
    })
