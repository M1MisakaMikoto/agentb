#!/usr/bin/env python3
"""
CLI Client for AgentB Backend API Testing

Usage:
    python cli_client.py

Features:
    - User authentication (register, login, logout, profile)
    - Session management (create, list, delete)
    - Conversation interaction (create, send message with SSE streaming)
"""

import json
import os
import sys
from pathlib import Path
from typing import Optional
import httpx
import asyncio
from datetime import datetime


TOKEN_FILE = Path(__file__).parent / ".cli_token"
BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")


class Colors:
    HEADER = "\033[95m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    ENDC = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"


def print_header(text: str):
    print(f"\n{Colors.BOLD}{Colors.HEADER}{'='*60}{Colors.ENDC}")
    print(f"{Colors.BOLD}{Colors.HEADER}  {text}{Colors.ENDC}")
    print(f"{Colors.BOLD}{Colors.HEADER}{'='*60}{Colors.ENDC}\n")


def print_success(text: str):
    print(f"{Colors.GREEN}✓ {text}{Colors.ENDC}")


def print_error(text: str):
    print(f"{Colors.RED}✗ {text}{Colors.ENDC}")


def print_info(text: str):
    print(f"{Colors.CYAN}  {text}{Colors.ENDC}")


def print_dim(text: str):
    print(f"{Colors.DIM}{text}{Colors.ENDC}")


def print_menu(options: list[str]):
    for i, opt in enumerate(options, 1):
        print(f"  {Colors.BOLD}{i}.{Colors.ENDC} {opt}")
    print(f"  {Colors.BOLD}0.{Colors.ENDC} 返回上级/退出")
    print()


def save_token(token: str):
    TOKEN_FILE.write_text(token)


def load_token() -> Optional[str]:
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text().strip()
    return None


def clear_token():
    if TOKEN_FILE.exists():
        TOKEN_FILE.unlink()


class APIClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.token: Optional[str] = load_token()

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        url = f"{self.base_url}{path}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.request(
                method, url, headers=self._headers(), **kwargs
            )
            if response.status_code == 401:
                print_error("认证失败，请重新登录")
                clear_token()
                self.token = None
                return {"code": 401, "message": "Unauthorized", "data": None}
            try:
                return response.json()
            except Exception:
                return {
                    "code": response.status_code,
                    "message": response.text,
                    "data": None,
                }

    async def register(self, name: str, password: str) -> dict:
        return await self._request(
            "POST", "/user/register", json={"name": name, "password": password}
        )

    async def login(self, name: str, password: str) -> dict:
        result = await self._request(
            "POST", "/user/login", json={"name": name, "password": password}
        )
        if result.get("code") == 200 and result.get("data"):
            token = result["data"].get("token")
            if token:
                self.token = token
                save_token(token)
        return result

    async def logout(self) -> dict:
        result = await self._request("POST", "/user/logout")
        clear_token()
        self.token = None
        return result

    async def get_profile(self) -> dict:
        return await self._request("GET", "/user/profile")

    async def list_sessions(self) -> dict:
        return await self._request("GET", "/session/sessions")

    async def create_session(self, title: str = "新会话") -> dict:
        return await self._request("POST", "/session/sessions", json={"title": title})

    async def get_session(self, session_id: int) -> dict:
        return await self._request("GET", f"/session/sessions/{session_id}")

    async def delete_session(self, session_id: int) -> dict:
        return await self._request("DELETE", f"/session/sessions/{session_id}")

    async def list_conversations(self, session_id: int) -> dict:
        return await self._request(
            "GET", f"/session/sessions/{session_id}/conversations"
        )

    async def create_conversation(
        self, session_id: int, user_content: str, workspace_id: Optional[str] = None
    ) -> dict:
        payload = {"user_content": user_content}
        if workspace_id:
            payload["workspace_id"] = workspace_id
        return await self._request(
            "POST", f"/session/sessions/{session_id}/conversations", json=payload
        )

    async def get_conversation(self, conversation_id: str) -> dict:
        return await self._request(
            "GET", f"/session/conversations/{conversation_id}"
        )

    async def delete_conversation(self, conversation_id: str) -> dict:
        return await self._request(
            "DELETE", f"/session/conversations/{conversation_id}"
        )

    async def send_message_stream(
        self, conversation_id: str, message: str
    ):
        url = f"{self.base_url}/session/conversations/{conversation_id}/messages"
        headers = self._headers()
        
        async with httpx.AsyncClient(timeout=300.0) as client:
            async with client.stream(
                "POST",
                url,
                headers=headers,
                json={"message": message},
            ) as response:
                if response.status_code != 200:
                    try:
                        error = await response.aread()
                        print_error(f"请求失败: {error.decode()}")
                    except Exception:
                        print_error(f"请求失败: HTTP {response.status_code}")
                    return
                
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        try:
                            data = json.loads(line[6:])
                            yield data
                        except json.JSONDecodeError:
                            continue
                    elif line.strip() == ": heartbeat":
                        print_dim("  [heartbeat]")


class CLIClient:
    def __init__(self):
        self.api = APIClient(BASE_URL)
        self.current_session_id: Optional[int] = None
        self.current_conversation_id: Optional[str] = None

    def input(self, prompt: str = "") -> str:
        try:
            return input(prompt).strip()
        except EOFError:
            return ""

    def clear_screen(self):
        os.system("cls" if os.name == "nt" else "clear")

    async def run(self):
        while True:
            self.clear_screen()
            print_header("AgentB CLI 客户端")
            
            if self.api.token:
                profile = await self.api.get_profile()
                if profile.get("code") == 200 and profile.get("data"):
                    user = profile["data"]
                    print_info(f"当前用户: {user.get('name', 'Unknown')}")
                else:
                    print_dim("Token已过期，请重新登录")
                    clear_token()
                    self.api.token = None
            
            print()
            print_menu(
                [
                    "用户认证",
                    "会话管理",
                    "对话交互",
                    "健康检查",
                ]
            )
            
            choice = self.input("请选择: ")
            
            if choice == "0":
                print_info("再见！")
                break
            elif choice == "1":
                await self.auth_menu()
            elif choice == "2":
                await self.session_menu()
            elif choice == "3":
                await self.conversation_menu()
            elif choice == "4":
                await self.health_check()
            else:
                print_error("无效选择")
                await self._pause()

    async def auth_menu(self):
        while True:
            self.clear_screen()
            print_header("用户认证")
            
            if self.api.token:
                profile = await self.api.get_profile()
                if profile.get("code") == 200 and profile.get("data"):
                    user = profile["data"]
                    print_info(f"已登录用户: {user.get('name', 'Unknown')}")
                    print_info(f"用户ID: {user.get('id', 'N/A')}")
                    print()
            
            print_menu(
                [
                    "注册新用户",
                    "登录",
                    "查看个人信息",
                    "登出",
                ]
            )
            
            choice = self.input("请选择: ")
            
            if choice == "0":
                break
            elif choice == "1":
                await self.register()
            elif choice == "2":
                await self.login()
            elif choice == "3":
                await self.view_profile()
            elif choice == "4":
                await self.logout()
            else:
                print_error("无效选择")
            
            await self._pause()

    async def register(self):
        print_info("注册新用户")
        name = self.input("用户名: ")
        if not name:
            print_error("用户名不能为空")
            return
        
        password = self.input("密码: ")
        if not password:
            print_error("密码不能为空")
            return
        
        result = await self.api.register(name, password)
        if result.get("code") == 200:
            print_success("注册成功！")
        else:
            print_error(f"注册失败: {result.get('message', 'Unknown error')}")

    async def login(self):
        print_info("用户登录")
        name = self.input("用户名: ")
        if not name:
            print_error("用户名不能为空")
            return
        
        password = self.input("密码: ")
        if not password:
            print_error("密码不能为空")
            return
        
        result = await self.api.login(name, password)
        if result.get("code") == 200:
            print_success("登录成功！")
        else:
            print_error(f"登录失败: {result.get('message', 'Unknown error')}")

    async def view_profile(self):
        if not self.api.token:
            print_error("请先登录")
            return
        
        result = await self.api.get_profile()
        if result.get("code") == 200 and result.get("data"):
            user = result["data"]
            print_success("用户信息:")
            print_info(f"  ID: {user.get('id')}")
            print_info(f"  名称: {user.get('name')}")
            print_info(f"  创建时间: {user.get('created_at')}")
        else:
            print_error(f"获取用户信息失败: {result.get('message')}")

    async def logout(self):
        if not self.api.token:
            print_error("未登录")
            return
        
        result = await self.api.logout()
        print_success("已登出")

    async def session_menu(self):
        if not self.api.token:
            print_error("请先登录")
            await self._pause()
            return
        
        while True:
            self.clear_screen()
            print_header("会话管理")
            
            if self.current_session_id:
                print_info(f"当前会话ID: {self.current_session_id}")
                print()
            
            print_menu(
                [
                    "创建新会话",
                    "列出所有会话",
                    "选择会话",
                    "删除会话",
                ]
            )
            
            choice = self.input("请选择: ")
            
            if choice == "0":
                break
            elif choice == "1":
                await self.create_session()
            elif choice == "2":
                await self.list_sessions()
            elif choice == "3":
                await self.select_session()
            elif choice == "4":
                await self.delete_session()
            else:
                print_error("无效选择")
            
            await self._pause()

    async def create_session(self):
        title = self.input("会话标题 (默认: 新会话): ") or "新会话"
        result = await self.api.create_session(title)
        if result.get("code") == 200 and result.get("data"):
            session_id = result["data"].get("id")
            print_success(f"会话创建成功！ID: {session_id}")
            self.current_session_id = session_id
        else:
            print_error(f"创建会话失败: {result.get('message')}")

    async def list_sessions(self):
        result = await self.api.list_sessions()
        if result.get("code") == 200 and result.get("data"):
            sessions = result["data"]
            if not sessions:
                print_info("暂无会话")
                return
            
            print_success(f"共 {len(sessions)} 个会话:")
            for s in sessions:
                current = " (当前)" if s.get("id") == self.current_session_id else ""
                print_info(
                    f"  [{s.get('id')}] {s.get('title', 'N/A')}{current}"
                )
                print_dim(f"       创建时间: {s.get('created_at', 'N/A')}")
        else:
            print_error(f"获取会话列表失败: {result.get('message')}")

    async def select_session(self):
        await self.list_sessions()
        session_id = self.input("输入会话ID: ")
        if not session_id:
            return
        
        try:
            session_id = int(session_id)
        except ValueError:
            print_error("无效的会话ID")
            return
        
        result = await self.api.get_session(session_id)
        if result.get("code") == 200 and result.get("data"):
            self.current_session_id = session_id
            print_success(f"已选择会话: {session_id}")
        else:
            print_error(f"选择会话失败: {result.get('message')}")

    async def delete_session(self):
        await self.list_sessions()
        session_id = self.input("输入要删除的会话ID: ")
        if not session_id:
            return
        
        try:
            session_id = int(session_id)
        except ValueError:
            print_error("无效的会话ID")
            return
        
        confirm = self.input(f"确认删除会话 {session_id}? (y/n): ")
        if confirm.lower() != "y":
            print_info("已取消")
            return
        
        result = await self.api.delete_session(session_id)
        if result.get("code") == 200:
            print_success("会话已删除")
            if self.current_session_id == session_id:
                self.current_session_id = None
        else:
            print_error(f"删除会话失败: {result.get('message')}")

    async def conversation_menu(self):
        if not self.api.token:
            print_error("请先登录")
            await self._pause()
            return
        
        if not self.current_session_id:
            print_error("请先选择会话")
            await self._pause()
            return
        
        while True:
            self.clear_screen()
            print_header("对话交互")
            print_info(f"当前会话ID: {self.current_session_id}")
            if self.current_conversation_id:
                print_info(f"当前对话ID: {self.current_conversation_id}")
            print()
            
            print_menu(
                [
                    "创建新对话",
                    "列出对话",
                    "发送消息 (流式响应)",
                    "查看对话详情",
                    "删除对话",
                ]
            )
            
            choice = self.input("请选择: ")
            
            if choice == "0":
                break
            elif choice == "1":
                await self.create_conversation()
            elif choice == "2":
                await self.list_conversations()
            elif choice == "3":
                await self.send_message()
            elif choice == "4":
                await self.view_conversation()
            elif choice == "5":
                await self.delete_conversation()
            else:
                print_error("无效选择")
            
            await self._pause()

    async def create_conversation(self):
        user_content = self.input("输入用户消息: ")
        if not user_content:
            print_error("消息不能为空")
            return
        
        workspace_id = self.input("工作空间ID (可选): ") or None
        
        result = await self.api.create_conversation(
            self.current_session_id, user_content, workspace_id
        )
        if result.get("code") == 200 and result.get("data"):
            conv_id = result["data"].get("conversation_id")
            print_success(f"对话创建成功！ID: {conv_id}")
            self.current_conversation_id = conv_id
        else:
            print_error(f"创建对话失败: {result.get('message')}")

    async def list_conversations(self):
        result = await self.api.list_conversations(self.current_session_id)
        if result.get("code") == 200 and result.get("data"):
            conversations = result["data"]
            if not conversations:
                print_info("暂无对话")
                return
            
            print_success(f"共 {len(conversations)} 个对话:")
            for c in conversations:
                current = " (当前)" if c.get("id") == self.current_conversation_id else ""
                state = c.get("state", "unknown")
                print_info(f"  [{c.get('id')}] 状态: {state}{current}")
                print_dim(f"       消息数: {c.get('message_count', 0)}")
        else:
            print_error(f"获取对话列表失败: {result.get('message')}")

    async def send_message(self):
        if not self.current_conversation_id:
            print_error("请先创建或选择对话")
            return
        
        message = self.input("输入消息: ")
        if not message:
            print_error("消息不能为空")
            return
        
        print_info("发送消息中，等待响应...")
        print()
        
        thinking_buffer = ""
        text_buffer = ""
        
        async for event in self.api.send_message_stream(
            self.current_conversation_id, message
        ):
            event_type = event.get("type")
            
            if event_type == "message_created":
                print_dim(f"[消息创建] conversation_id: {event.get('conversation_id')}")
            
            elif event_type == "thinking_start":
                print(f"\n{Colors.YELLOW}[思考中...]{Colors.ENDC}")
            
            elif event_type == "thinking_delta":
                payload = event.get("payload", "")
                thinking_buffer += payload
                print(payload, end="", flush=True)
            
            elif event_type == "thinking_end":
                print(f"{Colors.ENDC}\n")
            
            elif event_type == "text_start":
                print(f"\n{Colors.GREEN}[回复]{Colors.ENDC}")
            
            elif event_type == "text_delta":
                payload = event.get("payload", "")
                text_buffer += payload
                print(payload, end="", flush=True)
            
            elif event_type == "text_end":
                print()
            
            elif event_type == "tool_call":
                meta = event.get("meta", {})
                tool_name = meta.get("name", "unknown")
                print_dim(f"\n[工具调用] {tool_name}")
            
            elif event_type == "tool_res":
                payload = event.get("payload", "")
                print_dim(f"[工具结果] {payload[:100]}...")
            
            elif event_type == "state_change":
                state = event.get("meta", {}).get("state", "unknown")
                print_dim(f"\n[状态变更] {state}")
            
            elif event_type == "done":
                print_success("\n[完成]")
                break
            
            elif event_type == "error":
                content = event.get("content", "Unknown error")
                print_error(f"\n[错误] {content}")
                break
        
        print()

    async def view_conversation(self):
        conv_id = self.input("输入对话ID (留空使用当前): ") or self.current_conversation_id
        if not conv_id:
            print_error("请输入对话ID")
            return
        
        result = await self.api.get_conversation(conv_id)
        if result.get("code") == 200 and result.get("data"):
            conv = result["data"]
            print_success("对话详情:")
            print_info(f"  ID: {conv.get('id')}")
            print_info(f"  状态: {conv.get('state')}")
            print_info(f"  消息数: {conv.get('message_count')}")
            print_info(f"  工作空间: {conv.get('workspace_id', 'N/A')}")
        else:
            print_error(f"获取对话详情失败: {result.get('message')}")

    async def delete_conversation(self):
        conv_id = self.input("输入要删除的对话ID (留空使用当前): ") or self.current_conversation_id
        if not conv_id:
            print_error("请输入对话ID")
            return
        
        confirm = self.input(f"确认删除对话 {conv_id}? (y/n): ")
        if confirm.lower() != "y":
            print_info("已取消")
            return
        
        result = await self.api.delete_conversation(conv_id)
        if result.get("code") == 200:
            print_success("对话已删除")
            if self.current_conversation_id == conv_id:
                self.current_conversation_id = None
        else:
            print_error(f"删除对话失败: {result.get('message')}")

    async def health_check(self):
        print_info("检查服务健康状态...")
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{BASE_URL}/health")
                if response.status_code == 200:
                    print_success(f"服务正常: {response.json()}")
                else:
                    print_error(f"服务异常: HTTP {response.status_code}")
        except Exception as e:
            print_error(f"无法连接服务: {e}")
        
        await self._pause()

    async def _pause(self):
        self.input("\n按回车键继续...")


def main():
    print_dim(f"API Base URL: {BASE_URL}")
    print_dim(f"Token File: {TOKEN_FILE}")
    print()
    
    client = CLIClient()
    try:
        asyncio.run(client.run())
    except KeyboardInterrupt:
        print_info("\n已退出")


if __name__ == "__main__":
    main()
