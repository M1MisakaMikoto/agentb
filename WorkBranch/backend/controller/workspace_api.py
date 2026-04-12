from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from typing import Optional, List

from singleton import get_workspace_service
from service.agent_service.service import WorkspaceService
from controller.VO.result import Result

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


@router.get("")
def list_workspaces(
    service: WorkspaceService = Depends(get_workspace_service),
) -> Result:
    workspace_ids = sorted(service.list_all())
    data = []

    for workspace_id in workspace_ids:
        info = service.get_workspace_info(workspace_id)
        if not info:
            continue
        data.append({
            **info,
            "dir": service.get_workspace_dir(workspace_id)
        })

    return Result.success(data=data)


@router.get("/{workspace_id}")
def get_workspace(
    workspace_id: str,
    service: WorkspaceService = Depends(get_workspace_service),
) -> Result:
    info = service.get_workspace_info(workspace_id)
    if not info:
        raise HTTPException(status_code=404, detail="Workspace not found")

    return Result.success(data={
        **info,
        "dir": service.get_workspace_dir(workspace_id)
    })


@router.get("/{workspace_id}/files")
def list_workspace_files(
    workspace_id: str,
    service: WorkspaceService = Depends(get_workspace_service),
) -> Result:
    info = service.get_workspace_info(workspace_id)
    if not info:
        raise HTTPException(status_code=404, detail="Workspace not found")

    success, files, error_msg = service.list_files(workspace_id)
    if not success:
        raise HTTPException(status_code=400, detail=error_msg)

    return Result.success(data=files)


@router.post("/{workspace_id}/files")
async def upload_files(
    workspace_id: str,
    files: List[UploadFile] = File(...),
    sub_dir: Optional[str] = Form(default=None),
    service: WorkspaceService = Depends(get_workspace_service),
) -> Result:
    info = service.get_workspace_info(workspace_id)
    if not info:
        raise HTTPException(status_code=404, detail="Workspace not found")

    success, saved_files, error_msg = await service.save_uploaded_files(
        workspace_id, files, sub_dir
    )

    if not success:
        raise HTTPException(status_code=400, detail=error_msg)

    return Result.success(data=saved_files)
