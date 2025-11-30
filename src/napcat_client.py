"""
NapCat API 客户端
负责与 NapCat 进行 WebSocket 和 HTTP 通信
"""
import asyncio
import json
from typing import Optional, Callable
import websockets
import httpx
from loguru import logger

from .config import get_config


class NapcatClient:
    """NapCat 客户端"""
    
    # 最大并发消息处理任务数
    MAX_CONCURRENT_HANDLERS = 50
    
    def __init__(self):
        self.config = get_config().napcat
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.http_client: Optional[httpx.AsyncClient] = None
        self._message_handlers: list[Callable] = []
        self._running = False
        self._echo_callbacks: dict[str, asyncio.Future] = {}
        self._echo_counter = 0
        # 信号量控制并发
        self._handler_semaphore: Optional[asyncio.Semaphore] = None
    
    def add_message_handler(self, handler: Callable):
        """添加消息处理器"""
        self._message_handlers.append(handler)
    
    async def connect(self):
        """建立 WebSocket 连接"""
        ws_url = self.config.ws_url
        if self.config.access_token:
            ws_url += f"?access_token={self.config.access_token}"
        
        logger.info(f"正在连接到 NapCat: {self.config.ws_url}")
        
        self.ws = await websockets.connect(ws_url)
        self.http_client = httpx.AsyncClient(base_url=self.config.http_url)
        
        logger.info("NapCat 连接成功")
    
    async def disconnect(self):
        """断开连接"""
        self._running = False
        if self.ws:
            await self.ws.close()
            self.ws = None
        if self.http_client:
            await self.http_client.aclose()
            self.http_client = None
        logger.info("已断开 NapCat 连接")
    
    async def listen(self):
        """监听 WebSocket 消息"""
        self._running = True
        # 初始化信号量
        self._handler_semaphore = asyncio.Semaphore(self.MAX_CONCURRENT_HANDLERS)
        
        try:
            async for message in self.ws:
                try:
                    data = json.loads(message)
                    # 先处理 API 响应（同步，立即完成）
                    if "echo" in data and ("status" in data or "retcode" in data):
                        echo = data["echo"]
                        if echo in self._echo_callbacks:
                            self._echo_callbacks[echo].set_result(data)
                            continue
                        logger.debug(f"收到未知 echo 的响应: {echo}")
                        continue
                    
                    # 跳过心跳和生命周期事件
                    if data.get("post_type") == "meta_event":
                        continue
                    
                    # 异步处理其他消息，不阻塞接收
                    asyncio.create_task(self._dispatch_message(data))
                except json.JSONDecodeError:
                    logger.warning(f"无法解析消息: {message}")
                except Exception as e:
                    logger.error(f"处理消息时出错: {e}")
        except websockets.ConnectionClosed:
            logger.warning("WebSocket 连接已关闭")
        except Exception as e:
            logger.error(f"监听消息时出错: {e}")
        finally:
            self._running = False
    
    async def _dispatch_message(self, data: dict):
        """分发消息给处理器（异步执行，带并发控制）"""
        async with self._handler_semaphore:
            for handler in self._message_handlers:
                try:
                    await handler(data)
                except Exception as e:
                    logger.error(f"消息处理器出错: {e}")
    
    def _get_echo(self) -> str:
        """生成唯一的 echo 标识"""
        self._echo_counter += 1
        return f"echo_{self._echo_counter}"
    
    async def call_api(self, action: str, params: dict = None, timeout: float = 10.0) -> dict:
        """
        调用 NapCat API
        
        Args:
            action: API 动作名称
            params: API 参数
            timeout: 超时时间
        
        Returns:
            API 响应数据
        """
        if not self.ws:
            raise RuntimeError("WebSocket 未连接")
        
        echo = self._get_echo()
        request = {
            "action": action,
            "params": params or {},
            "echo": echo
        }
        
        # 创建 Future 等待响应
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self._echo_callbacks[echo] = future
        
        try:
            await self.ws.send(json.dumps(request))
            logger.debug(f"API 请求已发送: {action}, echo={echo}")
            result = await asyncio.wait_for(future, timeout=timeout)
            logger.debug(f"API 响应已收到: {action}, echo={echo}")
            return result
        except asyncio.TimeoutError:
            logger.warning(f"API 调用超时: {action}")
            raise
        finally:
            self._echo_callbacks.pop(echo, None)
    
    async def call_api_no_wait(self, action: str, params: dict = None):
        """
        调用 NapCat API，不等待响应（fire and forget）
        适用于非关键操作如发送提示消息
        
        Args:
            action: API 动作名称
            params: API 参数
        """
        if not self.ws:
            raise RuntimeError("WebSocket 未连接")
        
        request = {
            "action": action,
            "params": params or {},
        }
        
        await self.ws.send(json.dumps(request))
        logger.debug(f"API 请求已发送(不等待): {action}")
    
    # ==================== API 封装 ====================
    
    async def set_group_ban(self, group_id: int, user_id: int, duration: int = 60) -> dict:
        """
        禁言群成员
        
        Args:
            group_id: 群号
            user_id: 用户QQ号
            duration: 禁言时长 (秒)，0表示解除禁言
        
        Returns:
            API 响应
        """
        return await self.call_api("set_group_ban", {
            "group_id": group_id,
            "user_id": user_id,
            "duration": duration
        })
    
    async def send_group_msg(self, group_id: int, message: str | list, wait_response: bool = True) -> dict | None:
        """
        发送群消息
        
        Args:
            group_id: 群号
            message: 消息内容，可以是字符串或消息段数组
                     消息段格式: [{"type": "text", "data": {"text": "内容"}}]
            wait_response: 是否等待响应，默认True
        
        Returns:
            API 响应，如果 wait_response=False 则返回 None
        """
        params = {
            "group_id": group_id,
            "message": message
        }
        if wait_response:
            return await self.call_api("send_group_msg", params)
        else:
            await self.call_api_no_wait("send_group_msg", params)
            return None
    
    async def send_group_msg_with_at(self, group_id: int, user_id: int, text: str, wait_response: bool = True) -> dict | None:
        """
        发送带@的群消息
        
        Args:
            group_id: 群号
            user_id: 要@的用户QQ号
            text: 消息文本
            wait_response: 是否等待响应，默认True
        
        Returns:
            API 响应，如果 wait_response=False 则返回 None
        """
        message = [
            {"type": "at", "data": {"qq": str(user_id)}},
            {"type": "text", "data": {"text": f" {text}"}}
        ]
        return await self.send_group_msg(group_id, message, wait_response)
    
    async def get_group_member_info(self, group_id: int, user_id: int) -> dict:
        """
        获取群成员信息
        
        Args:
            group_id: 群号
            user_id: 用户QQ号
        
        Returns:
            群成员信息
        """
        return await self.call_api("get_group_member_info", {
            "group_id": group_id,
            "user_id": user_id
        })
    
    async def get_login_info(self) -> dict:
        """
        获取登录信息
        
        Returns:
            登录的QQ信息，格式: {"user_id": 123, "nickname": "昵称"}
        """
        return await self.call_api("get_login_info")


# 全局客户端实例
_client: Optional[NapcatClient] = None


def get_client() -> NapcatClient:
    """获取全局客户端"""
    global _client
    if _client is None:
        _client = NapcatClient()
    return _client
