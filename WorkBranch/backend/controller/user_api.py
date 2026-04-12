from fastapi import APIRouter, Request
from pydantic import BaseModel

from singleton import get_user_service
from controller.VO.result import Result

router = APIRouter(prefix="/user", tags=["user"])


class UpdateUserNameBody(BaseModel):
    name: str


@router.get("/profile")
def get_profile(request: Request):
    user = request.state.user
    return Result.success(data=user)


@router.put("/profile/name")
async def update_user_name(
    request: Request,
    body: UpdateUserNameBody,
):
    user = request.state.user
    user_service = get_user_service()
    updated_user = await user_service.update_user_name_by_id(user["id"], body.name)
    return Result.success(data=updated_user)
