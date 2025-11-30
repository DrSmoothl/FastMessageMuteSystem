"""
配置管理模块
"""
import tomli
import tomli_w
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional
from loguru import logger


@dataclass
class NapcatConfig:
    """NapCat连接配置"""
    ws_url: str = "ws://127.0.0.1:3001"
    http_url: str = "http://127.0.0.1:3000"
    access_token: str = ""


@dataclass
class BotConfig:
    """机器人配置"""
    bot_qq: int = 0


@dataclass
class MonitorConfig:
    """监控配置"""
    groups: list[int] = field(default_factory=list)
    admins: list[int] = field(default_factory=list)


@dataclass
class MuteConfig:
    """禁言配置"""
    time_window: int = 10
    message_threshold: int = 5
    mute_duration: int = 60
    mute_multiplier: float = 2.0
    max_mute_duration: int = 3600


@dataclass
class CommandsConfig:
    """命令配置"""
    prefix: str = "/"
    enable_cmd: str = "mute on"
    disable_cmd: str = "mute off"
    status_cmd: str = "mute status"
    reset_cmd: str = "mute reset"


@dataclass
class WhitelistConfig:
    """白名单配置"""
    users: list[int] = field(default_factory=list)
    exempt_admins: bool = True


@dataclass
class LoggingConfig:
    """日志配置"""
    level: str = "INFO"
    file: str = "logs/mute_bot.log"
    max_size: int = 10
    retention: int = 7


@dataclass
class Config:
    """总配置"""
    napcat: NapcatConfig = field(default_factory=NapcatConfig)
    bot: BotConfig = field(default_factory=BotConfig)
    monitor: MonitorConfig = field(default_factory=MonitorConfig)
    mute: MuteConfig = field(default_factory=MuteConfig)
    commands: CommandsConfig = field(default_factory=CommandsConfig)
    whitelist: WhitelistConfig = field(default_factory=WhitelistConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    
    @classmethod
    def load(cls, config_path: str | Path) -> "Config":
        """从TOML文件加载配置"""
        config_path = Path(config_path)
        if not config_path.exists():
            logger.warning(f"配置文件不存在: {config_path}，使用默认配置")
            return cls()
        
        with open(config_path, "rb") as f:
            data = tomli.load(f)
        
        config = cls()
        
        if "napcat" in data:
            config.napcat = NapcatConfig(**data["napcat"])
        if "bot" in data:
            config.bot = BotConfig(**data["bot"])
        if "monitor" in data:
            config.monitor = MonitorConfig(**data["monitor"])
        if "mute" in data:
            config.mute = MuteConfig(**data["mute"])
        if "commands" in data:
            config.commands = CommandsConfig(**data["commands"])
        if "whitelist" in data:
            config.whitelist = WhitelistConfig(**data["whitelist"])
        if "logging" in data:
            config.logging = LoggingConfig(**data["logging"])
        
        logger.info(f"配置文件加载成功: {config_path}")
        return config


# 全局配置实例
_config: Optional[Config] = None


def get_config() -> Config:
    """获取全局配置"""
    global _config
    if _config is None:
        _config = Config.load("config.toml")
    return _config


def reload_config() -> Config:
    """重新加载配置"""
    global _config
    _config = Config.load("config.toml")
    return _config
