"""
快速消息禁言系统 - 主入口
"""
import asyncio
import sys
from pathlib import Path
from loguru import logger

from .config import get_config
from .napcat_client import get_client
from .handler import MessageHandler


def setup_logging():
    """配置日志"""
    config = get_config().logging
    
    # 移除默认的处理器
    logger.remove()
    
    # 添加控制台输出
    logger.add(
        sys.stderr,
        level=config.level,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>"
    )
    
    # 添加文件输出
    log_path = Path(config.file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    
    logger.add(
        config.file,
        level=config.level,
        rotation=f"{config.max_size} MB",
        retention=config.retention,
        encoding="utf-8",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}"
    )


async def run():
    """运行机器人"""
    config = get_config()
    client = get_client()
    handler = MessageHandler()
    
    # 注册消息处理器
    client.add_message_handler(handler.handle)
    
    logger.info("=" * 50)
    logger.info("快速消息禁言系统 v0.1.0")
    logger.info("=" * 50)
    logger.info(f"监控群列表: {config.monitor.groups}")
    logger.info(f"管理员列表: {config.monitor.admins}")
    logger.info(f"刷屏阈值: {config.mute.time_window}秒内 {config.mute.message_threshold}条消息")
    logger.info(f"禁言时长: {config.mute.mute_duration}秒 (最大{config.mute.max_mute_duration}秒)")
    
    while True:
        try:
            await client.connect()
            await client.listen()
        except KeyboardInterrupt:
            logger.info("收到退出信号，正在关闭...")
            break
        except Exception as e:
            logger.error(f"连接断开: {e}")
            logger.info("5秒后尝试重连...")
            await asyncio.sleep(5)
        finally:
            await client.disconnect()


def main():
    """主入口"""
    setup_logging()
    
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("程序已退出")


if __name__ == "__main__":
    main()
