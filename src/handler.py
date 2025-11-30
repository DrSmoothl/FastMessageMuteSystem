"""
消息处理器
处理来自 NapCat 的消息事件
"""
from loguru import logger

from .config import get_config
from .napcat_client import get_client
from .spam_detector import get_detector


class MessageHandler:
    """消息处理器"""
    
    def __init__(self):
        self.config = get_config()
        self.client = get_client()
        self.detector = get_detector()
    
    async def handle(self, data: dict):
        """
        处理来自 NapCat 的事件数据
        
        Args:
            data: NapCat 推送的事件数据
        """
        post_type = data.get("post_type")
        
        # 处理消息事件
        if post_type == "message":
            await self._handle_message(data)
        # 处理通知事件 (如禁言通知)
        elif post_type == "notice":
            await self._handle_notice(data)
    
    async def _handle_notice(self, data: dict):
        """处理通知事件"""
        notice_type = data.get("notice_type")
        
        # 群禁言通知
        if notice_type == "group_ban":
            group_id = data.get("group_id")
            user_id = data.get("user_id")
            operator_id = data.get("operator_id")
            duration = data.get("duration", 0)
            sub_type = data.get("sub_type")  # 'ban' 或 'lift_ban'
            
            if sub_type == "ban":
                logger.info(f"禁言通知: 群{group_id} 用户{user_id} 被 {operator_id} 禁言 {duration}秒")
            else:
                logger.info(f"解禁通知: 群{group_id} 用户{user_id} 被 {operator_id} 解除禁言")
    
    async def _handle_message(self, data: dict):
        """处理消息事件"""
        # 只处理群消息
        message_type = data.get("message_type")
        if message_type != "group":
            return
        
        group_id = data.get("group_id")
        user_id = data.get("user_id")
        raw_message = data.get("raw_message", "")
        
        if not group_id or not user_id:
            return
        
        # 检查是否是监控的群
        if group_id not in self.config.monitor.groups:
            return
        
        # 检查是否是命令
        if await self._handle_command(group_id, user_id, raw_message):
            return
        
        # 检查刷屏
        await self._check_spam(group_id, user_id)
    
    async def _handle_command(self, group_id: int, user_id: int, message: str) -> bool:
        """
        处理命令
        
        Returns:
            如果是命令则返回 True
        """
        cmd_config = self.config.commands
        prefix = cmd_config.prefix
        
        # 检查是否以命令前缀开头
        if not message.startswith(prefix):
            return False
        
        # 去掉前缀
        cmd = message[len(prefix):].strip()
        
        # 检查是否是管理员
        is_admin = user_id in self.config.monitor.admins
        
        # 开启刷屏检测
        if cmd == cmd_config.enable_cmd:
            if not is_admin:
                await self._reply(group_id, "❌ 权限不足，只有管理员可以执行此命令")
                return True
            self.detector.set_enabled(group_id, True)
            await self._reply(group_id, "✅ 刷屏检测已开启")
            return True
        
        # 关闭刷屏检测
        if cmd == cmd_config.disable_cmd:
            if not is_admin:
                await self._reply(group_id, "❌ 权限不足，只有管理员可以执行此命令")
                return True
            self.detector.set_enabled(group_id, False)
            await self._reply(group_id, "✅ 刷屏检测已关闭")
            return True
        
        # 查看状态
        if cmd == cmd_config.status_cmd:
            if not is_admin:
                await self._reply(group_id, "❌ 权限不足，只有管理员可以执行此命令")
                return True
            status = self.detector.get_status()
            group_status = status.get(group_id, {"enabled": False, "tracked_users": 0, "total_violations": 0})
            msg = (
                f"📊 刷屏检测状态\n"
                f"状态: {'开启' if group_status['enabled'] else '关闭'}\n"
                f"监控用户数: {group_status['tracked_users']}\n"
                f"累计违规次数: {group_status['total_violations']}\n"
                f"检测窗口: {self.config.mute.time_window}秒\n"
                f"消息阈值: {self.config.mute.message_threshold}条\n"
                f"禁言时长: {self.config.mute.mute_duration}秒"
            )
            await self._reply(group_id, msg)
            return True
        
        # 重置用户记录
        if cmd.startswith(cmd_config.reset_cmd):
            if not is_admin:
                await self._reply(group_id, "❌ 权限不足，只有管理员可以执行此命令")
                return True
            # 解析目标用户
            parts = cmd.split()
            if len(parts) >= 3:
                try:
                    target_user = int(parts[2])
                    self.detector.reset_user(group_id, target_user)
                    await self._reply(group_id, f"✅ 已重置用户 {target_user} 的违规记录")
                except ValueError:
                    await self._reply(group_id, "❌ 请输入有效的QQ号")
            else:
                await self._reply(group_id, f"❌ 用法: {prefix}{cmd_config.reset_cmd} <QQ号>")
            return True
        
        return False
    
    async def _check_spam(self, group_id: int, user_id: int):
        """检查刷屏并执行禁言"""
        # 检查白名单
        if user_id in self.config.whitelist.users:
            return
        
        # 检查是否豁免管理员
        if self.config.whitelist.exempt_admins and user_id in self.config.monitor.admins:
            return
        
        # 记录消息并检测刷屏
        mute_duration = self.detector.record_message(group_id, user_id)
        
        if mute_duration:
            # 执行禁言
            try:
                await self.client.set_group_ban(group_id, user_id, mute_duration)
                # 发送带@的提示消息（不等待响应，避免高并发时超时）
                await self._reply_with_at_async(
                    group_id,
                    user_id,
                    f"⚠️ 检测到刷屏行为，已被禁言 {self._format_duration(mute_duration)}"
                )
                logger.info(f"已禁言用户: 群={group_id}, 用户={user_id}, 时长={mute_duration}秒")
            except Exception as e:
                logger.error(f"禁言失败: {e}")
    
    def _format_duration(self, seconds: int) -> str:
        """格式化时长显示"""
        if seconds < 60:
            return f"{seconds}秒"
        elif seconds < 3600:
            return f"{seconds // 60}分钟"
        else:
            hours = seconds // 3600
            minutes = (seconds % 3600) // 60
            if minutes > 0:
                return f"{hours}小时{minutes}分钟"
            return f"{hours}小时"
    
    async def _reply(self, group_id: int, message: str):
        """发送群消息（等待响应）"""
        try:
            await self.client.send_group_msg(group_id, message)
        except Exception as e:
            logger.error(f"发送消息失败: {e}")
    
    async def _reply_with_at(self, group_id: int, user_id: int, message: str):
        """发送带@的群消息（等待响应）"""
        try:
            await self.client.send_group_msg_with_at(group_id, user_id, message)
        except Exception as e:
            logger.error(f"发送消息失败: {e}")
    
    async def _reply_with_at_async(self, group_id: int, user_id: int, message: str):
        """发送带@的群消息（不等待响应，fire and forget）"""
        try:
            await self.client.send_group_msg_with_at(group_id, user_id, message, wait_response=False)
        except Exception as e:
            logger.error(f"发送消息失败: {e}")
