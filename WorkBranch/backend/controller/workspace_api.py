from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from typing import Optional, List
import os
import glob
import mimetypes
import logging

logger = logging.getLogger(__name__)

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
    # 先从内存查找，找不到则从磁盘查找
    info = service.get_workspace_info(workspace_id)
    if not info:
        workspace_dir = service.get_workspace_dir(workspace_id)
        if not workspace_dir:
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


@router.get("/{workspace_id}/files/test")
def test_endpoint(workspace_id: str):
    return {"status": "ok", "workspace_id": workspace_id}


@router.get("/{workspace_id}/files/{filename}")
def download_workspace_file(
    workspace_id: str,
    filename: str,
    service: WorkspaceService = Depends(get_workspace_service),
):
    logger.info(f"[download_file] workspace_id={workspace_id}, filename={filename}")

    # 尝试从注册信息获取目录
    workspace_dir = service.get_workspace_dir(workspace_id)
    logger.info(f"[download_file] workspace_dir from registry: {workspace_dir}")

    # 如果没有注册信息，尝试从磁盘直接查找
    if not workspace_dir:
        base = service.base_dir
        pattern = os.path.join(base, "*", workspace_id)
        matches = glob.glob(pattern)
        logger.info(f"[download_file] glob pattern={pattern}, matches={matches}")
        if matches:
            workspace_dir = matches[0]

    if not workspace_dir or not os.path.isdir(workspace_dir):
        logger.warning(f"[download_file] workspace dir not found: {workspace_dir}")
        raise HTTPException(status_code=404, detail="Workspace not found")

    file_path = os.path.join(workspace_dir, filename)
    logger.info(f"[download_file] file_path={file_path}, exists={os.path.isfile(file_path)}")

    # 安全校验：确保文件在workspace目录内
    real_workspace = os.path.realpath(workspace_dir)
    real_file = os.path.realpath(file_path)
    if not real_file.startswith(real_workspace):
        raise HTTPException(status_code=403, detail="Access denied")

    if not os.path.isfile(file_path):
        # 尝试解码URL编码的文件名
        import urllib.parse
        decoded_filename = urllib.parse.unquote(filename)
        file_path = os.path.join(workspace_dir, decoded_filename)
        logger.info(f"[download_file] retry with decoded: {file_path}, exists={os.path.isfile(file_path)}")
        if not os.path.isfile(file_path):
            raise HTTPException(status_code=404, detail=f"File not found: {filename}")

    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"

    return FileResponse(
        path=file_path,
        filename=os.path.basename(filename),
        media_type=content_type,
    )
