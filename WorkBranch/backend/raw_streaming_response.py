"""
自定义流式响应类 - 绕过 StreamingResponse 的缓冲问题
"""
from starlette.responses import Response
from starlette.types import Receive, Scope, Send
import json


class RawStreamingResponse(Response):
    """纯 ASGI 流式响应 - 不使用 StreamingResponse 的缓冲机制"""
    
    def __init__(self, content_iterator, status_code=200, headers=None, **kwargs):
        # 不调用 super().__init__() 设置 content
        # 直接初始化必要的属性
        self.status_code = status_code
        self.body = b""
        self.background = None  # FastAPI 需要这个属性
        
        # 初始化 headers (MutableHeaders)
        from starlette.datastructures import MutableHeaders
        # MutableHeaders 的 raw 参数需要 List[Tuple[bytes, bytes]]
        raw_headers = [
            (b"content-type", b"text/event-stream; charset=utf-8"),
            (b"cache-control", b"no-cache, no-store, must-revalidate"),
            (b"pragma", b"no-cache"),
            (b"expires", b"0"),
            (b"x-accel-buffering", b"no"),
            (b"connection", b"keep-alive"),
        ]
        
        # 添加自定义头
        if headers:
            for key, value in headers.items():
                if value is not None:
                    k = key.encode('latin-1') if isinstance(key, str) else key
                    v = str(value).encode('latin-1')
                    raw_headers.append((k, v))
        
        self._headers = MutableHeaders(raw=raw_headers)
        
        # 保存迭代器
        self.content_iterator = content_iterator
    
    @property
    def headers(self):
        return self._headers
    
    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        # 构建响应头列表 (ASGI 格式)
        asgi_headers = []
        for key, value in self._headers.items():
            # MutableHeaders.items() 返回 str, str
            if isinstance(key, str):
                key = key.encode('latin-1')
            if isinstance(value, str):
                value = value.encode('latin-1')
            asgi_headers.append((key, value))
        
        # 发送响应开始（只发送一次）
        await send({
            "type": "http.response.start",
            "status": self.status_code,
            "headers": asgi_headers,
        })
        
        print(f"[RawStream] ✓ 响应头已发送，开始流式传输...")
        
        # 流式发送内容
        try:
            async for chunk in self.content_iterator:
                if isinstance(chunk, str):
                    chunk = chunk.encode('utf-8')
                
                await send({
                    "type": "http.response.body",
                    "body": chunk,
                    "more_body": True,
                })
                
                print(f"[RawStream] ✓ 已发送 {len(chunk)} bytes")
                
        except Exception as e:
            print(f"[RawStream] ✗ 错误: {e}")
            import traceback
            traceback.print_exc()
            error_data = f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n".encode()
            await send({
                "type": "http.response.body",
                "body": error_data,
                "more_body": True,
            })
        
        # 发送结束标记
        print(f"[RawStream] ✓ 发送结束标记 (more_body=False)")
        await send({
            "type": "http.response.body",
            "body": b"",
            "more_body": False,
        })
