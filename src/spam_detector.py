"""
刷屏检测器
追踪用户消息频率并检测刷屏行为
"""
import json
import time
from collections import deque, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from loguru import logger

from .config import get_config

# 持久化文件路径
STATE_FILE = Path("data/mute_state.json")


@dataclass
class UserMessageRecord:
    """用户消息记录"""
    # 使用 deque 存储时间戳，自动限制长度，O(1) 操作
    timestamps: deque = field(default_factory=lambda: deque(maxlen=100))
    # 累计违规次数
    violation_count: int = 0
    # 上次禁言时间
    last_mute_time: float = 0
    # 上次禁言时长
    last_mute_duration: int = 0
    # 是否正在禁言中（防止重复触发）
    is_muted: bool = False


class SpamDetector:
    """刷屏检测器"""
    
    def __init__(self):
        self.config = get_config().mute
        # 用户消息记录: {group_id: {user_id: UserMessageRecord}}
        self._records: dict[int, dict[int, UserMessageRecord]] = defaultdict(
            lambda: defaultdict(UserMessageRecord)
        )
        # 各群功能开关状态: {group_id: bool}
        self._enabled: dict[int, bool] = {}
        # 加载持久化状态
        self._load_state()
    
    def _load_state(self):
        """从文件加载群开关状态"""
        if STATE_FILE.exists():
            try:
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                # JSON 的 key 是字符串，需要转回 int
                self._enabled = {int(k): v for k, v in data.get("enabled", {}).items()}
                logger.info(f"已加载群开关状态: {len(self._enabled)} 个群")
            except Exception as e:
                logger.error(f"加载状态文件失败: {e}")
    
    def _save_state(self):
        """保存群开关状态到文件"""
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump({"enabled": self._enabled}, f, ensure_ascii=False, indent=2)
            logger.debug(f"已保存群开关状态")
        except Exception as e:
            logger.error(f"保存状态文件失败: {e}")
    
    def is_enabled(self, group_id: int) -> bool:
        """检查某群是否启用了刷屏检测"""
        return self._enabled.get(group_id, False)
    
    def set_enabled(self, group_id: int, enabled: bool):
        """设置某群的刷屏检测开关"""
        self._enabled[group_id] = enabled
        self._save_state()  # 持久化
        logger.info(f"群 {group_id} 刷屏检测已{'开启' if enabled else '关闭'}")
    
    def record_message(self, group_id: int, user_id: int) -> Optional[int]:
        """
        记录用户消息并检测是否刷屏
        
        Args:
            group_id: 群号
            user_id: 用户QQ号
        
        Returns:
            如果触发禁言，返回禁言时长 (秒)，否则返回 None
        """
        if not self.is_enabled(group_id):
            return None
        
        now = time.time()
        record = self._records[group_id][user_id]
        
        # 如果正在禁言中，跳过检测
        if record.is_muted:
            # 检查禁言是否已结束
            if now < record.last_mute_time + record.last_mute_duration:
                return None
            record.is_muted = False
        
        # 添加当前消息时间戳 (deque 自动限制长度)
        record.timestamps.append(now)
        
        # 统计时间窗口内的消息数量
        window_start = now - self.config.time_window
        recent_count = sum(1 for ts in record.timestamps if ts > window_start)
        
        # 检查是否超过阈值
        if recent_count >= self.config.message_threshold:
            # 触发刷屏
            record.violation_count += 1
            record.is_muted = True
            
            # 计算禁言时长
            mute_duration = self._calculate_mute_duration(record)
            
            record.last_mute_time = now
            record.last_mute_duration = mute_duration
            
            # 清空时间戳，防止连续触发
            record.timestamps.clear()
            
            logger.warning(
                f"检测到刷屏: 群={group_id}, 用户={user_id}, "
                f"违规次数={record.violation_count}, 禁言时长={mute_duration}秒"
            )
            
            return mute_duration
        
        return None
    
    def _calculate_mute_duration(self, record: UserMessageRecord) -> int:
        """计算禁言时长"""
        # 基础时长 * 倍数 ^ (违规次数 - 1)
        duration = int(
            self.config.mute_duration * 
            (self.config.mute_multiplier ** (record.violation_count - 1))
        )
        # 不超过最大时长
        return min(duration, self.config.max_mute_duration)
    
    def reset_user(self, group_id: int, user_id: int):
        """重置用户的违规记录"""
        if group_id in self._records and user_id in self._records[group_id]:
            del self._records[group_id][user_id]
            logger.info(f"已重置用户记录: 群={group_id}, 用户={user_id}")
    
    def get_user_stats(self, group_id: int, user_id: int) -> dict:
        """获取用户的统计信息"""
        record = self._records[group_id][user_id]
        return {
            "violation_count": record.violation_count,
            "recent_messages": len(record.timestamps),
            "last_mute_time": record.last_mute_time,
            "last_mute_duration": record.last_mute_duration
        }
    
    def get_status(self) -> dict:
        """获取整体状态"""
        status = {}
        for group_id, enabled in self._enabled.items():
            group_records = self._records.get(group_id, {})
            total_violations = sum(r.violation_count for r in group_records.values())
            status[group_id] = {
                "enabled": enabled,
                "tracked_users": len(group_records),
                "total_violations": total_violations
            }
        return status


# 全局检测器实例
_detector: Optional[SpamDetector] = None


def get_detector() -> SpamDetector:
    """获取全局检测器"""
    global _detector
    if _detector is None:
        _detector = SpamDetector()
    return _detector
