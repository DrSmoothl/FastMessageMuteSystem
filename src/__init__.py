"""
快速消息禁言系统
"""
from .config import get_config, reload_config
from .napcat_client import get_client
from .spam_detector import get_detector
from .handler import MessageHandler
from .main import main

__version__ = "0.1.0"
__all__ = [
    "get_config",
    "reload_config", 
    "get_client",
    "get_detector",
    "MessageHandler",
    "main"
]
