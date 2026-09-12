# VintagePomeloBot 设计方案

> 一个轻量级、命令行运行的 QQ 群机器人，连接 NapCat WebSocket Server 接收消息，通过 Coze API 访问知识库回答"旧柚" App 相关问题。

---

## 一、整体架构

```
┌─────────────────────────────────────────────────────────────┐
│                     NapCat WS Server                        │
│              ws://localhost:5004  (OneBot v11)               │
└───────────────────────┬─────────────────────────────────────┘
                        │ WebSocket (long connection)
                        ▼
┌─────────────────────────────────────────────────────────────┐
│                  napcat_client.py                            │
│  · 维持长连接 + 自动重连                                       │
│  · 接收原始 OneBot v11 事件 JSON                              │
│  · 提供 send_group_msg / send_private_msg 发送接口            │
└───────────────────────┬─────────────────────────────────────┘
                        │ raw event dict
                        ▼
┌─────────────────────────────────────────────────────────────┐
│                     bot_core.py                             │
│  主调度中心：路由所有入站事件                                   │
│                                                             │
│  ┌───────────────┐  ┌─────────────────┐  ┌──────────────┐  │
│  │message_filter │  │ context_cache   │  │admin_handler │  │
│  │ 判断是否需要   │  │ 用户消息历史缓存  │  │ 管理员命令    │  │
│  │ 回复          │  │ (滑动窗口)       │  │ 处理         │  │
│  └───────┬───────┘  └────────┬────────┘  └──────────────┘  │
│          │ 需要回复           │ 携带历史消息                    │
│          └──────────────┬────┘                              │
└─────────────────────────┼───────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                    coze_client.py                           │
│  · SSE 流式调用 /v3/chat                                     │
│  · 解析 answer 事件返回最终回复文本                            │
└───────────────────────┬─────────────────────────────────────┘
                        │ 回复文本
                        ▼
              napcat_client.send_group_msg()
```

---

## 二、项目文件结构

```
VintagePomeloBot/
├── bot.py                 # 主入口：asyncio 事件循环，启动机器人
├── config.toml            # 用户配置文件（Bot / NapCat / Coze / 过滤 / 缓存 / 管理员）
├── requirements.txt       # 依赖包列表
├── README.md
├── docs/
│   └── design.md          # 本文件
└── src/
    ├── __init__.py
    ├── config.py          # dataclass 配置加载（读取 config.toml）
    ├── napcat_client.py   # WebSocket 连接 NapCat + 发送 API
    ├── coze_client.py     # Coze /v3/chat SSE 客户端
    ├── message_filter.py  # 消息是否需要回复的判断逻辑
    ├── context_cache.py   # 用户消息历史缓冲区（滑动窗口）
    ├── admin_handler.py   # 管理员命令解析与执行
    └── bot_core.py        # 核心事件调度逻辑
```

---

## 三、配置文件设计（config.toml）

```toml
[bot]
qq_id       = "10000"         # 机器人自身 QQ 号
nickname    = "小柚"           # 机器人昵称（用于触发检测）

[napcat]
host              = "localhost"
port              = 5004
access_token      = "<your_napcat_access_token>"
reconnect_interval = 5   # 断线重连间隔（秒）

[coze]
api_key   = "sat_xxx..."
bot_id    = "<your_coze_bot_id>"
user_id   = "vintagebot_user"
timeout   = 60

[filter]
# 和"旧柚"app 相关的关键词（满足才视为相关话题）
app_keywords  = ["旧柚", "jiuyou", "jiu you", "vintage pomelo"]
# 求助/咨询类关键词（配合 app_keywords 才触发 LLM 调用）
help_keywords = ["怎么", "如何", "为什么", "帮", "问", "bug", "错误", "崩了", "失败", "不行", "?", "？"]
# 是否要求 app_keywords + help_keywords 同时出现才触发（false=只要包含 app_keywords 就触发）
strict_mode   = true

[cache]
window_size  = 3     # 每个 (group_id, user_id) 保留最近 N 条消息
ttl_seconds  = 300   # 缓存条目超时时间（秒）

[admin]
admin_qq_list  = []          # 管理员 QQ 号列表，字符串格式
command_prefix = "/bot"

[groups]
# 白名单(whitelist)：只处理列表中的群；黑名单(blacklist)：屏蔽列表中的群
mode       = "blacklist"
group_list = []
```

---

## 四、消息过滤逻辑

### 4.1 触发回复的条件（满足任一即触发）

| 优先级 | 条件 | 说明 |
|-------|------|------|
| 1 | `@机器人` | OneBot v11 消息段中出现 `{"type":"at","data":{"qq":"<bot_qq>"}}` |
| 2 | 消息中包含机器人昵称 | 配置的 `bot.nickname` 关键词（如"小柚"） |
| 3 | 旧柚 App 相关咨询 | `app_keywords` 与 `help_keywords` 组合判断（见 4.2） |

### 4.2 旧柚 App 相关判断

- **strict_mode = true**（默认）：消息必须同时包含 `app_keywords` 中任一词 **且** `help_keywords` 中任一词
- **strict_mode = false**：消息包含 `app_keywords` 中任一词即触发

### 4.3 不触发的情况

- 消息来自机器人自身（`user_id == bot.qq_id`，防自循环）
- 群不在白名单中（或在黑名单中）
- 纯闲聊、与旧柚无关的内容
- 其他群友互相对话且未@机器人

---

## 五、消息历史缓存设计

### 5.1 缓存键与数据结构

```
cache_key = (group_id, user_id)
deque(maxlen=window_size) → [{"role": "user", "content": "...", "ts": float}, ...]
```

### 5.2 缓存行为

1. **每条群消息均进缓存**（无论是否触发回复），保留最近 `window_size` 条
2. **触发回复时**：取出该用户缓存的所有消息（最多 `window_size` 条）作为 `additional_messages` 历史消息一起发送给 Coze
3. **缓存超时**：条目超过 `ttl_seconds` 秒未更新则自动清理（通过时间戳比较）

### 5.3 发送给 Coze 的消息组装

```python
additional_messages = [
    # 历史消息（older → newer）
    {"role": "user", "content": "上条消息", "content_type": "text"},
    {"role": "user", "content": "上上条消息", "content_type": "text"},
    # 当前触发消息
    {"role": "user", "content": "当前消息", "content_type": "text"},
]
```

> 注：Coze 的 `additional_messages` 用户历史消息叠加在当前提问前，帮助 LLM 理解上下文。

---

## 六、Coze 接入方案（参考 test_coze.py）

- **接口**：`POST https://api.coze.cn/v3/chat`（SSE 流式）
- **认证**：`Authorization: Bearer <api_key>`
- **核心字段**：

```json
{
  "bot_id": "<bot_id>",
  "user_id": "<user_id>",
  "stream": true,
  "additional_messages": [
    {"role": "user", "content": "...", "content_type": "text"}
  ]
}
```

- **响应解析**：监听 SSE 事件，提取 `event: conversation.message.delta` 或 `event: conversation.message.completed` 中 `type == "answer"` 的 `content` 字段（参考 test_coze.py 的 flush 逻辑）
- **超时配置**：通过 `httpx.AsyncClient(timeout=60)`

---

## 七、NapCat WebSocket 通信协议（OneBot v11）

### 7.1 接收消息事件（群消息）

```json
{
  "post_type": "message",
  "message_type": "group",
  "group_id": 12345678,
  "user_id": 87654321,
  "sender": {"nickname": "某用户", "card": "群名片"},
  "message": [
    {"type": "at", "data": {"qq": "10000"}},
    {"type": "text", "data": {"text": "小柚 旧柚怎么登录？"}}
  ],
  "raw_message": "[CQ:at,qq=10000] 旧柚怎么登录？"
}
```

### 7.2 发送群消息 API

WebSocket 发送以下 JSON（Action 调用方式）：

```json
{
  "action": "send_group_msg",
  "params": {
    "group_id": 12345678,
    "message": [
      {"type": "at", "data": {"qq": "87654321"}},
      {"type": "text", "data": {"text": "\n这里是回复内容..."}}
    ]
  },
  "echo": "<uuid>"
}
```

> 回复时自动 `@` 提问用户，提升群聊体验。

### 7.3 连接参数（对应 NapCat 配置）

- NapCat 采用 `direct` 模式：机器人作为 **WebSocket 客户端**主动连接 NapCat
- 地址：`ws://localhost:5004`
- 鉴权：请求头 `Authorization: Bearer <your_napcat_access_token>`

---

## 八、管理员命令设计

触发方式：管理员在群里或私聊发送 `<command_prefix> <command>`，如 `/bot status`。

| 命令 | 说明 |
|------|------|
| `/bot help` | 显示帮助信息 |
| `/bot status` | 查看运行状态（连接状态、缓存条数等） |
| `/bot allow <group_id>` | 将某群加入白名单 / 从黑名单移除 |
| `/bot deny <group_id>` | 将某群加入黑名单 / 从白名单移除 |
| `/bot clearcache [group_id]` | 清空消息历史缓存（可指定群） |
| `/bot reload` | 重新加载 config.toml（热重载） |
| `/bot addkw <keyword>` | 动态添加触发关键词（运行时生效） |

> 管理员检查：`sender.user_id` 在 `admin.admin_qq_list` 中才执行命令。

---

## 九、异常处理与稳定性

| 场景 | 处理方式 |
|------|---------|
| NapCat WS 断线 | 指数退避自动重连（最大间隔 60 秒） |
| Coze API 超时 / 报错 | 捕获异常，向用户发送"抱歉，暂时无法回答"提示 |
| 消息缓存内存占用 | TTL 机制 + 定时清理协程（每 60 秒扫描一次） |
| 防刷屏 / 速率限制 | 每个 (group_id, user_id) 限速：同一用户 5 秒内只触发一次 LLM 调用 |
| 自循环检测 | 忽略来自 `bot.qq_id` 自身的消息 |

---

## 十、依赖包

```
websockets>=12.0     # WebSocket 客户端（连接 NapCat）
httpx>=0.27.0        # Coze API HTTP 客户端（SSE 流式）
tomli>=2.0.0         # Python < 3.11 的 TOML 解析（3.11+ 用内置 tomllib）
```

---

## 十一、启动方式

```bash
cd VintagePomeloBot
python bot.py
# 或指定配置文件
python bot.py --config /path/to/config.toml
```

命令行参数（可选）：
- `--config <path>`：指定配置文件路径，默认 `./config.toml`
- `--debug`：开启详细日志输出

---

## 十二、开发顺序建议

1. `config.py` — 配置加载与验证
2. `napcat_client.py` — WebSocket 连接 + 收发消息
3. `coze_client.py` — 复用 test_coze.py 逻辑，封装为 async 函数
4. `message_filter.py` — 消息过滤规则
5. `context_cache.py` — 历史消息缓存
6. `bot_core.py` — 组装以上模块，完成主调度
7. `admin_handler.py` — 管理员命令
8. `bot.py` — 命令行入口，启动事件循环
