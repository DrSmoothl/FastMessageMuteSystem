# 快速消息禁言系统

一个与 NapCat 交互的 QQ 群刷屏自动禁言程序。

## 功能特性

- 🔍 **刷屏检测**: 自动检测用户在短时间内发送大量消息的行为
- 🔇 **自动禁言**: 检测到刷屏后自动禁言，支持递增禁言时长
- 👥 **多群监控**: 支持同时监控多个群
- ⚙️ **命令控制**: 管理员可通过命令开关功能
- 📋 **白名单**: 支持设置不受禁言的用户
- 📝 **TOML 配置**: 使用简洁的 TOML 格式配置文件

## 安装

### 前置要求

- Python 3.10+
- NapCat (OneBot 11 协议实现)

### 使用 uv (推荐)

```bash
# 克隆仓库
git clone https://github.com/DrSmoothl/FastMessageMuteSystem.git
cd FastMessageMuteSystem

# 安装 uv (如果还没安装)
pip install uv

# 安装依赖
uv sync
```

### 使用 pip

```bash
# 克隆仓库
git clone https://github.com/DrSmoothl/FastMessageMuteSystem.git
cd FastMessageMuteSystem

# 安装
pip install -e .
```

## 配置

### 1. 创建配置文件

复制配置模板并修改：

```bash
# Linux/macOS
cp config.example.toml config.toml

# Windows (PowerShell)
Copy-Item config.example.toml config.toml

# Windows (CMD)
copy config.example.toml config.toml
```

### 2. 编辑配置文件

编辑 `config.toml` 文件，填写你的实际配置：

```toml
[napcat]
ws_url = "ws://127.0.0.1:3002"    # NapCat WebSocket 地址
http_url = "http://127.0.0.1:3000" # NapCat HTTP API 地址
access_token = ""                  # 访问令牌（如果设置了）

[bot]
bot_qq = 123456789  # 你的机器人QQ号

[monitor]
groups = [123456789, 987654321]  # 要监控的群号列表
admins = [111111111]              # 管理员QQ号列表

[mute]
time_window = 20       # 检测时间窗口 (秒)
message_threshold = 4  # 消息数量阈值
mute_duration = 36000  # 禁言时长 (秒)，默认10小时
mute_multiplier = 2.0  # 累计违规禁言时长倍数
max_mute_duration = 360000  # 最大禁言时长 (秒)
```

### 3. 运行

```bash
# 使用 uv
uv run mute-bot

# 或使用 pip 安装后
mute-bot
```

## 命令

| 命令 | 说明 |
|------|------|
| `/mute on` | 开启刷屏检测 |
| `/mute off` | 关闭刷屏检测 |
| `/mute status` | 查看当前状态 |
| `/mute reset <QQ号>` | 重置用户违规记录 |

## 项目结构

```
FastMessageMuteSystem/
├── config.example.toml  # 配置文件模板
├── config.toml          # 实际配置文件（需自行创建，不提交到 git）
├── pyproject.toml       # 项目配置
├── README.md            # 说明文档
├── data/                # 持久化数据目录
│   └── mute_state.json  # 禁言状态数据
├── logs/                # 日志目录
└── src/
    ├── __init__.py
    ├── main.py          # 主入口
    ├── config.py        # 配置管理
    ├── napcat_client.py # NapCat 客户端
    ├── spam_detector.py # 刷屏检测器
    └── handler.py       # 消息处理器
```

**注意**: `config.toml`、`data/` 和 `logs/` 已添加到 `.gitignore`，不会被提交到版本控制。

## License
AGPL-v3 or later
