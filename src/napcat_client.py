"""
NapCat API 客户端
负责与 NapCat 进行 WebSocket 和 HTTP 通信
"""
import asyncio
import json
import time
from dataclasses import dataclass
from typing import Optional, Callable, Any, Union
import websockets
import httpx
from loguru import logger

from .config import get_config, NapcatConfig


@dataclass
class BanTask:
    """禁言任务"""
    group_id: int
    user_id: int
    duration: int
    future: asyncio.Future[dict[str, Any]]
    created_at: float


class NapcatClient:
    """NapCat 客户端"""
    
    # 最大并发消息处理任务数
    MAX_CONCURRENT_HANDLERS: int = 50
    # 禁言队列处理间隔（秒）
    BAN_QUEUE_INTERVAL: float = 0.8
    # 禁言任务超时时间（秒）
    BAN_TASK_TIMEOUT: float = 30.0
    
    def __init__(self) -> None:
        self.config: NapcatConfig = get_config().napcat
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.http_client: Optional[httpx.AsyncClient] = None
        self._message_handlers: list[Callable[[dict[str, Any]], Any]] = []
        self._running: bool = False
        self._echo_callbacks: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._echo_counter: int = 0
        # 信号量控制并发
        self._handler_semaphore: Optional[asyncio.Semaphore] = None
        # 禁言任务队列
        self._ban_queue: Optional[asyncio.Queue[BanTask]] = None
        self._ban_worker_task: Optional[asyncio.Task[None]] = None
        # 已在队列中的禁言任务（用于去重）
        self._pending_bans: set[tuple[int, int]] = set()
    
    def add_message_handler(self, handler: Callable[[dict[str, Any]], Any]) -> None:
        """添加消息处理器"""
        self._message_handlers.append(handler)
    
    async def connect(self) -> None:
        """建立 WebSocket 连接"""
        ws_url: str = self.config.ws_url
        if self.config.access_token:
            ws_url += f"?access_token={self.config.access_token}"
        
        logger.info(f"正在连接到 NapCat: {self.config.ws_url}")
        
        self.ws = await websockets.connect(ws_url)
        self.http_client = httpx.AsyncClient(base_url=self.config.http_url)
        
        # 初始化并启动禁言队列处理器
        self._ban_queue = asyncio.Queue()
        self._ban_worker_task = asyncio.create_task(self._ban_queue_worker())
        logger.info("禁言队列处理器已启动")
        
        logger.info("NapCat 连接成功")
    
    async def disconnect(self) -> None:
        """断开连接"""
        self._running = False
        
        # 停止禁言队列处理器
        if self._ban_worker_task:
            self._ban_worker_task.cancel()
            try:
                await self._ban_worker_task
            except asyncio.CancelledError:
                pass
            self._ban_worker_task = None
            logger.info("禁言队列处理器已停止")
        
        if self.ws:
            await self.ws.close()
            self.ws = None
        if self.http_client:
            await self.http_client.aclose()
            self.http_client = None
        logger.info("已断开 NapCat 连接")
    
    async def listen(self) -> None:
        """监听 WebSocket 消息"""
        self._running = True
        # 初始化信号量
        self._handler_semaphore = asyncio.Semaphore(self.MAX_CONCURRENT_HANDLERS)
        
        try:
            async for message in self.ws:
                try:
                    data: dict[str, Any] = json.loads(message)
                    # 先处理 API 响应（同步，立即完成）
                    if "echo" in data and ("status" in data or "retcode" in data):
                        echo: str = data["echo"]
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
    
    async def _dispatch_message(self, data: dict[str, Any]) -> None:
        """分发消息给处理器（异步执行，带并发控制）"""
        async with self._handler_semaphore:
            handler: Callable[[dict[str, Any]], Any]
            for handler in self._message_handlers:
                try:
                    await handler(data)
                except Exception as e:
                    logger.error(f"消息处理器出错: {e}")
    
    async def _ban_queue_worker(self) -> None:
        """禁言队列处理器 - 按顺序处理禁言请求"""
        logger.debug("禁言队列处理器开始运行")
        while True:
            try:
                # 从队列获取任务
                task: BanTask = await self._ban_queue.get()
                
                # 检查任务是否已过期（超过30秒还没处理）
                current_time: float = time.time()
                if current_time - task.created_at > self.BAN_TASK_TIMEOUT:
                    logger.warning(f"禁言任务已过期，跳过: 群={task.group_id}, 用户={task.user_id}")
                    if not task.future.done():
                        task.future.set_exception(asyncio.TimeoutError("任务在队列中等待超时"))
                    self._pending_bans.discard((task.group_id, task.user_id))
                    self._ban_queue.task_done()
                    continue
                
                # 执行禁言
                try:
                    logger.debug(f"开始处理禁言任务: 群={task.group_id}, 用户={task.user_id}, 队列剩余={self._ban_queue.qsize()}")
                    result: dict[str, Any] = await self._call_api_internal("set_group_ban", {
                        "group_id": task.group_id,
                        "user_id": task.user_id,
                        "duration": task.duration
                    }, timeout=self.BAN_TASK_TIMEOUT)
                    if not task.future.done():
                        task.future.set_result(result)
                except Exception as e:
                    logger.error(f"禁言执行失败: 群={task.group_id}, 用户={task.user_id}, 错误={e}")
                    if not task.future.done():
                        task.future.set_exception(e)
                finally:
                    self._pending_bans.discard((task.group_id, task.user_id))
                    self._ban_queue.task_done()
                
                # 处理完一个任务后等待一段时间，避免请求过于密集
                await asyncio.sleep(self.BAN_QUEUE_INTERVAL)
                
            except asyncio.CancelledError:
                logger.debug("禁言队列处理器被取消")
                break
            except Exception as e:
                logger.error(f"禁言队列处理器出错: {e}")
                await asyncio.sleep(1)  # 出错后等待1秒再继续
    
    def _get_echo(self) -> str:
        """生成唯一的 echo 标识"""
        self._echo_counter += 1
        return f"echo_{self._echo_counter}"
    
    async def _call_api_internal(self, action: str, params: Optional[dict[str, Any]] = None, timeout: float = 30.0) -> dict[str, Any]:
        """
        内部 API 调用方法（不经过限流，供队列处理器使用）
        
        Args:
            action: API 动作名称
            params: API 参数
            timeout: 超时时间
        
        Returns:
            API 响应数据
        """
        if not self.ws:
            raise RuntimeError("WebSocket 未连接")
        
        echo: str = self._get_echo()
        request: dict[str, Any] = {
            "action": action,
            "params": params or {},
            "echo": echo
        }
        
        # 创建 Future 等待响应
        loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._echo_callbacks[echo] = future
        
        try:
            await self.ws.send(json.dumps(request))
            logger.debug(f"API 请求已发送: {action}, echo={echo}")
            result: dict[str, Any] = await asyncio.wait_for(future, timeout=timeout)
            logger.debug(f"API 响应已收到: {action}, echo={echo}")
            return result
        except asyncio.TimeoutError:
            logger.warning(f"API 调用超时: {action}, echo={echo}, timeout={timeout}s")
            raise
        finally:
            self._echo_callbacks.pop(echo, None)
    
    async def call_api(self, action: str, params: Optional[dict[str, Any]] = None, timeout: float = 30.0) -> dict[str, Any]:
        """
        调用 NapCat API
        
        Args:
            action: API 动作名称
            params: API 参数
            timeout: 超时时间（默认30秒，高并发场景需要更长时间）
        
        Returns:
            API 响应数据
        """
        return await self._call_api_internal(action, params, timeout)
    
    async def call_api_no_wait(self, action: str, params: Optional[dict[str, Any]] = None) -> None:
        """
        调用 NapCat API，不等待响应（fire and forget）
        适用于非关键操作如发送提示消息
        
        Args:
            action: API 动作名称
            params: API 参数
        """
        if not self.ws:
            raise RuntimeError("WebSocket 未连接")
        
        request: dict[str, Any] = {
            "action": action,
            "params": params or {},
        }
        
        await self.ws.send(json.dumps(request))
        logger.debug(f"API 请求已发送(不等待): {action}")
    
    # ==================== API 封装 ====================
    
    async def set_group_ban(self, group_id: int, user_id: int, duration: int = 60, timeout: float = 60.0) -> dict[str, Any]:
        """
        禁言群成员（通过队列处理，避免高并发超时）
        
        Args:
            group_id: 群号
            user_id: 用户QQ号
            duration: 禁言时长 (秒)，0表示解除禁言
            timeout: 等待队列处理的超时时间
        
        Returns:
            API 响应
        """
        # 检查是否已有相同的禁言任务在队列中（去重）
        key: tuple[int, int] = (group_id, user_id)
        if key in self._pending_bans:
            logger.debug(f"禁言任务已在队列中，跳过: 群={group_id}, 用户={user_id}")
            return {"status": "ok", "retcode": 0, "message": "已在队列中"}
        
        # 创建任务并加入队列
        loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        task: BanTask = BanTask(
            group_id=group_id,
            user_id=user_id,
            duration=duration,
            future=future,
            created_at=time.time()
        )
        
        self._pending_bans.add(key)
        await self._ban_queue.put(task)
        queue_size: int = self._ban_queue.qsize()
        logger.info(f"禁言任务已加入队列: 群={group_id}, 用户={user_id}, 时长={duration}秒, 队列长度={queue_size}")
        
        # 等待任务完成
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending_bans.discard(key)
            logger.warning(f"禁言任务等待超时: 群={group_id}, 用户={user_id}")
            raise
    
    async def set_group_ban_direct(self, group_id: int, user_id: int, duration: int = 60, timeout: float = 30.0) -> dict[str, Any]:
        """
        禁言群成员（直接调用，不经过队列，用于紧急情况）
        
        Args:
            group_id: 群号
            user_id: 用户QQ号
            duration: 禁言时长 (秒)，0表示解除禁言
            timeout: 超时时间
        
        Returns:
            API 响应
        """
        return await self._call_api_internal("set_group_ban", {
            "group_id": group_id,
            "user_id": user_id,
            "duration": duration
        }, timeout=timeout)
    
    async def send_group_msg(self, group_id: int, message: Union[str, list[dict[str, Any]]], wait_response: bool = True) -> Optional[dict[str, Any]]:
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
        params: dict[str, Any] = {
            "group_id": group_id,
            "message": message
        }
        if wait_response:
            return await self.call_api("send_group_msg", params)
        else:
            await self.call_api_no_wait("send_group_msg", params)
            return None
    
    async def send_group_msg_with_at(self, group_id: int, user_id: int, text: str, wait_response: bool = True) -> Optional[dict[str, Any]]:
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
        message: list[dict[str, Any]] = [
            {"type": "at", "data": {"qq": str(user_id)}},
            {"type": "text", "data": {"text": f" {text}"}}
        ]
        return await self.send_group_msg(group_id, message, wait_response)
    
    async def get_group_member_info(self, group_id: int, user_id: int) -> dict[str, Any]:
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
    
    async def get_login_info(self) -> dict[str, Any]:
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
