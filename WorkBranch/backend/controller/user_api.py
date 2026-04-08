from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from singleton import get_user_service
from service.user_service.user import UserService
from controller.VO.result import Result

router = APIRouter(prefix="/user", tags=["user"])


class RegisterRequest(BaseModel):
    name: str
    password: str


class LoginRequest(BaseModel):
    name: str
    password: str


class UpdateUserNameBody(BaseModel):
    name: str


@router.post("/register")
async def register(req: RegisterRequest):
    user_service = get_user_service()
    try:
        result = await user_service.register(req.name, req.password)
        return Result.success(data=result)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/login")
async def login(req: LoginRequest):
    user_service = get_user_service()
    try:
        result = await user_service.login(req.name, req.password)
        return Result.success(data=result)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))


@router.post("/logout")
async def logout(request: Request):
    user = request.state.user
    user_service = get_user_service()
    await user_service.logout(user["id"])
    return Result.success()


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
