# VintagePomeloBot（小柚）

一个基于 **NapCat (OneBot v11) + 本地大模型（Ollama）+ GitHub Copilot CLI Agent** 的 QQ 群技术支持机器人。它监听群消息，对 @机器人、产品关键词求助等内容进行智能回复，内置本地知识库检索、长期会话记忆、用户反馈收集（自动创建 GitHub Issue）、群聊知识学习和网页 Web 控制台。

> 所有推理默认跑在本机 Ollama 上，聊天内容不出本机；联网核验由 Agent 在受限 MCP 工具下完成。

---

## 功能特性

- **智能触发回复**：@机器人 / 昵称 / 产品求助关键词（strict 模式可要求求助词组合命中）
- **知识库优先路由**：回复前先由轻量模型判断意图，命中知识库的走知识库，闲聊走模型直答
- **游戏兼容性自动收录**：识别群友报告的游戏运行情况（能玩/不能玩都收），自动补全缺失信息后在兼容性仓库创建 Issue；图片证据随 Issue 收录，保留报告者群昵称方便追溯
- **Copilot CLI 支持 Agent**：把技术支持、网页搜索核验、开源仓库研究交给 [GitHub Copilot CLI](https://github.com/github/copilot-cli)，底层仍调用本机 Ollama 模型
- **受限的联网能力**：仅开放两个 MCP 工具（DuckDuckGo 搜索 + 公开网页抓取），拒绝 localhost/内网地址，禁止 shell 与文件写入
- **本地知识库 + 群聊学习**：群友的优质解答可被整理为知识候选，管理员审批后入库（`/bot learn approve`）
- **长期会话记忆**：按 (群, 用户) 维度保存脱敏后的对话记忆，支持摘要压缩
- **反馈收集与自动建 Issue**：识别用户反馈（bug / 建议），归档截图与视频，自动创建 GitHub Issue
- **违禁词过滤**：命中屏蔽词直接回复模板话术，不调用模型
- **入群欢迎语**：可配置模板与群公告条目
- **Web 控制台**：浏览器访问 `http://localhost:8080`，管理知识库、待审批知识、反馈记录、Agent 配置
- **管理员命令**：`/bot status`、`/bot reload`、`/bot kb add`、`/bot learn list` 等
- **Web 控制台**与**多实例锁**：同一配置只允许一个机器人实例运行

---

## 整体架构

```
┌────────────────────────────────────────────────────────────┐
│                NapCat WS Server (OneBot v11)               │
│                ws://localhost:5004                         │
└──────────────────────────┬─────────────────────────────────┘
                           │ WebSocket
                           ▼
┌────────────────────────────────────────────────────────────┐
│  napcat_client.py   收发消息 / 自动重连                      │
│                            │                               │
│  bot_core.py        主调度：过滤 → 路由 → 生成 → 回复        │
│    ├── message_filter        触发判断 / 违禁词               │
│    ├── knowledge_router      知识库 vs 闲聊路由              │
│    ├── knowledge_store       本地知识库检索                  │
│    ├── context_cache         滑动窗口上下文                  │
│    ├── conversation_memory   长期会话记忆                    │
│    ├── copilot_agent         Copilot CLI 支持 Agent          │
│    ├── deepseek_client       云端模型（可选）                │
│    ├── ollama_client         本地模型                       │
│    ├── feedback_handler      反馈收集 → GitHub Issue         │
│    ├── group_knowledge_learner  群聊知识学习                 │
│    └── admin_handler         /bot 管理命令                   │
└──────────────────────────┬─────────────────────────────────┘
                           ▼
              napcat_client.send_group_msg()
                           +
              web_server.py（localhost:8080 控制台）
```

---

## 目录结构

```
VintagePomeloBot/
├── bot.py                  # 主入口：asyncio 事件循环 + 单实例锁
├── config.toml.sample      # 配置样例（复制为 config.toml 后填写）
├── .mcp.json.sample        # Copilot Agent 的 MCP 工具配置样例
├── requirements.txt        # Python 依赖
├── .github/agents/         # Copilot Agent 人设与规则（profile）
├── src/                    # 全部业务代码（见架构图）
├── static/                 # Web 控制台前端页面
├── docs/
│   ├── design.md           # 早期设计方案（Coze 接入、消息过滤、缓存设计）
│   └── agent_runtime.md    # Agent 运行说明（部署、64K 上下文、GPU 调优）
├── tests/                  # 单元测试（不依赖 QQ / Ollama / Copilot）
├── data/                   # 运行数据（gitignore，首次运行自动创建）
├── logs/                   # 日志输出（gitignore）
└── runtime/                # 三方 MCP 依赖（gitignore，见下方重建步骤）
```

---

## 快速开始

### 0. 环境要求

- Windows 10/11（其他平台需自行调整路径）
- Python 3.11+
- [NapCat](https://napneko.github.io/)（QQ 协议端，WebSocket Server 模式）
- [Ollama](https://ollama.com/)（本地推理）
- [GitHub Copilot CLI](https://github.com/github/copilot-cli)（Agent 运行时，可选但推荐）

### 1. 安装依赖

```powershell
cd VintagePomeloBot
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 2. 部署模型与 Agent 运行时

```powershell
# 安装并启动 Ollama 后，下载一个模型（示例）
ollama pull qwen3:32b

# 安装 Copilot CLI（Agent 模式需要）
npm install -g @github/copilot
copilot --version
```

### 3. 重建 MCP 运行时依赖（runtime/ 目录）

`runtime/` 不入库，需要手动重建 web 搜索 MCP：

```powershell
mkdir runtime\mcp_web_search
cd runtime\mcp_web_search
npm install @zhafron/mcp-web-search
```

`public-web` 抓取工具由仓库内的 `src/public_web_mcp.py` 提供，无需额外安装。

### 4. 配置

```powershell
copy config.toml.sample config.toml
copy .mcp.json.sample .mcp.json
```

然后编辑 `config.toml`：

| 必改项 | 说明 |
|--------|------|
| `[bot].qq_id` | 机器人自己的 QQ 号 |
| `[napcat].access_token` | NapCat 配置的鉴权 token |
| `[ollama].model` | 你 `ollama pull` 的模型标签 |
| `[admin].admin_qq_list` | 管理员 QQ 号 |
| `[github_issues].repository` | 反馈自动建 Issue 的目标仓库（不需要就设 `enabled = false`） |

> **安全提示**：`config.toml`、`.mcp.json`、`data/`、`logs/` 均已在 `.gitignore` 中排除，不会被提交。GitHub PAT 优先填在 `config.toml` 或环境变量 `GITHUB_TOKEN`，不要写进任何会提交的文件。

### 5. 启动

```powershell
# 先启动 NapCat（WebSocket Server 模式，监听 ws://localhost:5004）
# 再启动机器人
python bot.py

# 可选参数
python bot.py --config config.toml --debug
```

启动后浏览器打开 [http://localhost:8080](http://localhost:8080) 进入 Web 控制台。

---

## 配置参考

完整字段见 [`config.toml.sample`](config.toml.sample)，每个字段都有注释。核心段落：

| 段 | 作用 |
|----|------|
| `[bot]` | 机器人 QQ 号、昵称（昵称也会触发回复） |
| `[napcat]` | NapCat WS 地址、token、重连间隔 |
| `[deepseek]` | 云端模型（可选，api_key 留空则纯本地） |
| `[ollama]` | 本地模型、上下文长度、温度 |
| `[agent]` | Copilot Agent 开关、推理力度、联网白名单、知识学习 |
| `[filter]` | 触发关键词、违禁词、strict 模式 |
| `[memory]` | 长期会话记忆 |
| `[feedback]` / `[github_issues]` | 反馈收集与自动建 Issue |
| `[compatibility]` | 游戏兼容性自动收录（目标仓库、追问超时、默认值） |
| `[welcome]` | 入群欢迎语与公告 |
| `[admin]` / `[groups]` | 管理员、群黑白名单 |

## 管理员命令

在群里或私聊发送（前缀可配置，默认 `/bot`）：

```
/bot help                     帮助
/bot status                   运行状态
/bot allow <group_id>         群白名单 + / 黑名单 -
/bot deny <group_id>          群黑名单 + / 白名单 -
/bot clearcache [group_id]    清空上下文缓存
/bot reload                   热重载配置
/bot kb add|show|update|del   知识库管理
/bot learn list|show|approve|reject   知识候选审核
/bot feedback records|show    反馈记录查询
/bot compat list|show #id     兼容性报告查询
```

## 游戏兼容性自动收录

群友在聊天中报告的游戏运行情况（无论是"能玩"还是"玩不了"）都会被自动整理成 Issue 发布到 `[compatibility].repository`（如 `yifengling0/VintagePomeloPro-Compatibility`），作为公开的兼容性数据库。

**工作流程**：

1. **触发**：消息命中兼容性关键词（能跑/流畅/闪退/黑屏/模拟器名等，正负面都覆盖）后，由本地模型做一次轻量 JSON 分析确认是否为真实的运行体验报告
2. **自动补全（不打扰用户）**：
   - 小柚 App 版本：未提供 → 默认"最新版"
   - 游戏版本：未提供 → 记"不明"
   - 模拟器/运行方式：按游戏类型推断（PC 游戏 → Winlator/Hokit，手游 → Android 原生），消息里直接提到的模拟器名优先
   - 设备/系统/驱动：能提取就填，不能就留空
3. **追问（仅在必要时）**：只有"不知道是哪个游戏"或"无法判断最终能否运行"时，才向报告者追问一次；回复"不用了"即跳过；超时（`clarify_timeout`，默认 120 秒）按已有信息直接收录
4. **发布**：信息完整时静默收录（不刷屏）；Issue 包含游戏信息、运行环境、报告者群昵称与群组、脱敏原文和图片证据（QQ CDN 链接 + 本地归档于 `data/compatibility_media`）

管理员可用 `/bot compat` 查看最近收录的报告，`/bot compat show #id` 查看详情。GitHub 令牌优先级：`compatibility.token` → 环境变量 `GITHUB_TOKEN` → `github_issues.token`。

## 测试

```powershell
python -m unittest discover -s tests -v
```

测试不依赖 QQ、Ollama 或 Copilot，可离线运行。

## 文档

- [docs/design.md](docs/design.md) —— 架构与模块设计（消息过滤、上下文缓存、OneBot v11 协议、管理命令）
- [docs/agent_runtime.md](docs/agent_runtime.md) —— Agent 运行细节（Copilot CLI 接入 Ollama、64K 上下文与 GPU 调优、知识学习流程、运营建议）

## 安全与隐私

- 仓库不含任何真实配置：QQ 号、token、API key、群号等仅存在于本机 `config.toml`（已 gitignore）
- 默认所有对话推理在本机 Ollama 完成，聊天记录不上传第三方云端
- Agent 的联网工具受限：只能搜索与抓取公开网页，拒绝内网地址，禁止执行命令与读写文件
- 反馈媒体文件保存在本地 `data/feedback_media`；是否外发群昵称/媒体链接可通过 `include_reporter_name` / `include_media_links` 关闭
