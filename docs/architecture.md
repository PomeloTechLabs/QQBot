# VintagePomeloBot 架构与使用手册

> QQ 群技术支持机器人「小柚」。本文是整体架构与使用方法的完整说明；
> 早期 Coze 方案设计见 [design.md](design.md)（部分已过时），Agent 运行细节见 [agent_runtime.md](agent_runtime.md)。

---

## 一、系统概览

```
                ┌─────────────────────────────────────────────┐
                │                QQ 用户 / 群聊                │
                └──────────────────────┬──────────────────────┘
                                       │ QQ 协议
                ┌──────────────────────▼──────────────────────┐
                │   NapCat (OneBot v11)                       │
                │   · QQ 协议端，WebSocket Server 模式          │
                │   · ws://localhost:5004（token 鉴权）         │
                │   · WebUI: http://localhost:6099             │
                └──────────────────────┬──────────────────────┘
                                       │ WebSocket 长连接 + 心跳
                ┌──────────────────────▼──────────────────────┐
                │   VintagePomeloBot (Python, bot.py)          │
                │                                             │
                │   主调度 bot_core                            │
                │    ├── 触发判断 / 违禁词过滤                  │
                │    ├── 知识库 ↔ 闲聊 路由（Ollama JSON）       │
                │    ├── 技术支持回答（Copilot CLI → Ollama）    │
                │    ├── 反馈收集 → GitHub Issue                │
                │    ├── 游戏兼容性收录 → 兼容性仓库 Issue       │
                │    ├── 群聊知识学习 → 候选审批                 │
                │    └── 长期会话记忆（摘要压缩）                │
                │                                             │
                │   Web 控制台 (Flask, 默认 18080)              │
                └───┬──────────────┬───────────────┬──────────┘
                    │              │               │
        ┌───────────▼───┐  ┌───────▼──────┐  ┌─────▼─────────────┐
        │ Ollama 本地模型 │  │ GitHub Copilot│  │ GitHub Issues API │
        │ /api/chat      │  │ CLI + 2 个 MCP │  │ 反馈 / 兼容性仓库  │
        │ qwen3.8-64k    │  │ (搜索/网页抓取) │  └───────────────────┘
        └───────────────┘  └──────────────┘
```

**职责边界**：机器人自己不做"智能体循环"。推理在本地 Ollama；需要工具调用（联网搜索、网页核验、GitHub 研究）时交给成熟的 GitHub Copilot CLI 运行时，机器人只负责权限边界、进程生命周期和 QQ 格式化。

---

## 二、代码模块总览（src/）

| 模块 | 职责 |
|------|------|
| `bot.py` | 入口：日志初始化、单实例锁（127.0.0.1:17863）、装配 BotCore + WebUI 线程 |
| `bot_core.py` | **主调度中心**：所有入站事件的路由与分发；回复生成、限流（同用户 5s）、上下文/记忆写入 |
| `config.py` | 全部配置的 dataclass 定义、TOML 加载与保存（WebUI 改配置也走这里） |
| `napcat_client.py` | NapCat WebSocket 客户端：长连接、自动重连（指数退避至 60s）、echo 请求/响应配对、收发消息 API |
| `message_filter.py` | 触发判断（@/昵称/关键词 strict 组合）、违禁词匹配、消息段解析（文本/图片/视频提取） |
| `knowledge_router.py` | 轻量 JSON 路由：这条消息走知识库（knowledge）还是闲聊直答（direct），置信度兜底 |
| `knowledge_store.py` | 本地知识库（JSON 存储 + Markdown 渲染），检索注入给回复模型 |
| `knowledge_router` 之外的知识侧 | `group_knowledge_learner` 观察群聊"问题+解决"对话；`knowledge_candidates` 保存待审批候选；`skill_library` 渲染已批准的操作经验 |
| `ollama_client.py` | Ollama 原生 `/api/chat` 异步客户端：轻量 JSON 分类（路由/反馈/兼容性）与兜底直答 |
| `deepseek_client.py` | 云端模型（可选，api_key 为空则不用） |
| `copilot_agent.py` | Copilot CLI 窄适配器：拼装受限提示（TRIAGE_ONLY / TRIAGE_BATCH_ONLY / 支持任务），管理进程生命周期与推理力度，不自己实现工具循环 |
| `agent_activity.py` | 内存态的 Agent 活动事件流，推给网页控制台实时展示（非审计日志） |
| `context_cache.py` | 每 (群, 用户) 的滑动窗口短上下文（默认 3 条 / 5 分钟 TTL） |
| `conversation_memory.py` | 每 (scope, 用户) 的持久会话记忆：滚动对话 + LLM 摘要压缩，超出上限时自动归档 |
| `feedback_handler.py` / `feedback_store.py` / `feedback_media.py` | 反馈链路：识别用户反馈（bug/需求/反馈/待办）→ 本地 JSON+MD 归档 → 附件本地保存 → 自动建 GitHub Issue |
| `github_issue_publisher.py` | 反馈 Issue 发布器：标题/正文组装、报告者信息开关、原文脱敏（邮箱/长编号）、QQ CDN 图片外链 |
| `compatibility_collector.py` | **游戏兼容性收录**：关键词门槛 → 轻量 JSON 分析 → 缺失信息自动补全 →（仅在 @ 场景）追问一次 → 发布；详见第六节 |
| `compatibility_store.py` / `compatibility_publisher.py` | 兼容性报告的本地存储（JSON+MD）与 Issue 发布（标题带 运行正常/异常 标签） |
| `admin_handler.py` | `/bot` 管理命令解析与执行（含权限：管理员 QQ 或群管理） |
| `reply_formatter.py` | 回复净化：去 Markdown 符号、长度截断，适配 QQ 纯文本 |
| `web_server.py` + `static/index.html` | Flask 控制台：配置查看/修改、知识库管理、知识候选审批、Agent 活动流（SSE） |
| `public_web_mcp.py` | 项目内 MCP 工具 `fetch_public_url`：只抓公开网页，拒绝 localhost/内网/私有 IP |

---

## 三、消息处理主流程

一条群消息进入 `bot_core._handle_event` 后按顺序经过：

1. **基础过滤**：非 message 事件走通知处理（入群欢迎）；自己发的消息丢弃；空文本且无附件丢弃；黑/白名单群判定
2. **管理命令**：`/bot ...` 或 @机器人 + 命令 → `admin_handler`，命中即短路返回
3. **违禁词**：群消息命中屏蔽词 → 直接回复违规模板，不调用模型
4. **上下文记录**：文本进 (群,用户) 缓存与群级上下文；同时异步触发群聊知识学习观察
5. **兼容性追问应答**：若机器人刚向该用户追问且未超时，本条消息（含纯图片）作为补充说明合并，不再走常规回复
6. **反馈查询**：`查待办/反馈列表/记录 #id` 等查询关键词 → 直接返回本地记录摘要
7. **触发判断**（`message_filter.should_reply`）：私聊总是回复；群聊需 @机器人 / 昵称 / 产品关键词组合
8. **后台自动收集**（不阻塞回复）：
   - 反馈分析：报错/崩溃等关键词或私聊 → Ollama 轻量 JSON 判别 → 记录 + 建 Issue
   - 兼容性分析：兼容性关键词 → Ollama 轻量 JSON 判别 → 收录流程（见第六节）
9. **普通群消息批分流**（`monitor_all_group_messages=true` 时）：攒批（默认 50 条或 300 秒）→ Copilot 一次判断哪些需要回复 → 选中的逐条走支持回答
10. **正式回复**：带 5s 用户级限流 → 知识库/闲聊路由 → 生成回答（优先 Copilot Agent + 知识库检索注入，失败回退 Ollama 直答）→ 净化 → 发送（群聊自动 @ 提问者）

---

## 四、两个自动收集链路

### 4.1 用户反馈（→ 主仓库 Issue）

- 触发：报错/闪退/崩溃/建议等关键词，或任何私聊（高召回门槛 + 轻量 JSON 精判）
- 记录类型：bug / feature / feedback / todo；bug 与 feature 自动建 Issue（feature 可关）
- Issue 内容：产品、版本、设备、系统、复现步骤、预期/实际（仅整理用户明确给出的事实）、群昵称（可关）、脱敏原文、图片/视频外链（可关）
- 媒体：本地归档于 `data/feedback_media/`（图片 15MB / 视频 100MB 上限）

### 4.2 游戏兼容性收录（→ 兼容性仓库 Issue）

**打扰控制（核心原则）**：

| 场景 | 行为 |
|------|------|
| 普通群聊（未 @） | 仅当分类确认**明确与旧柚相关**（jiuyou_related）才静默收录；无关或拿不准 → 沉默；缺游戏名 → 放弃，绝不追问 |
| @ 机器人 / 昵称点名 / 私聊 | 视为主动对话；缺游戏名或结果不明时**追问一次**（回复"不用了"跳过；120s 超时按已有信息收录） |

**自动补全**：App 版本 → "最新版"；游戏版本 → "不明"；模拟器 → 按游戏类型推断（PC 游戏 → Winlator/Hokit，手游 → Android 原生；消息中明确提到的模拟器名优先）。

**发布**：Issue 标题 `[兼容性][运行正常|运行异常] 游戏名 - 摘要`，正文含游戏信息、运行环境、报告者群昵称与群组时间、脱敏原文、图片证据（QQ CDN 外链 + 本地归档 `data/compatibility_media/`）。QQ 号等长编号一律脱敏，仅本地 JSON 保留。

**令牌优先级**：`compatibility.token` → 环境变量 `GITHUB_TOKEN` → `github_issues.token`。fine-grained PAT 按仓库授权，需给兼容性仓库勾 `Issues: write`。

---

## 五、知识库与学习

- **检索优先**：正式回答前把知识库检索结果（前 5 条）注入 Copilot Agent；Agent 允许联网，但知识库资料优先
- **群聊学习**：观察"用户问题 + 群友解决方案"，由 Copilot 整理成候选（`data/knowledge_candidates.json`），**默认不自动入库**（`auto_publish_learning=false`）
- **机器人答复学习**：高置信度（≥0.9）的正式答复也可提炼为候选
- **审批**：网页控制台"待审批知识"面板或 `/bot learn approve #id` / `reject #id`；批准后写入知识库并同步渲染 `data/knowledge_base.md`

---

## 六、Web 控制台

地址：`http://localhost:<web.port>`（默认 8080，本机配置为 18080；可在 `[web]` 配置段修改或关闭）。

| 功能 | 说明 |
|------|------|
| 配置查看/修改 | `GET/POST /api/config`：可在线调整过滤、缓存、记忆、反馈、兼容性、Agent、Ollama 等段；令牌字段只写不回显 |
| 知识库管理 | 列表 / 新增 / 修改 / 删除 / 重载（对应 `knowledge_base` API） |
| 知识候选审批 | 待审列表 / 批准 / 拒绝 |
| Agent 活动流 | SSE 实时展示 Agent 当前动作（分流、检索、核验等），非审计日志 |

---

## 七、数据文件一览（data/，全部已 gitignore）

| 文件/目录 | 内容 | 敏感性 |
|-----------|------|--------|
| `feedback.json` / `feedback.md` | 反馈记录（308+ 条） | 含群友昵称与聊天原文，勿外传 |
| `feedback_media/` | 反馈截图/视频原件 | 私密 |
| `compatibility_reports.json` / `.md` | 兼容性报告（含发布状态与失败原因） | 同上 |
| `compatibility_media/` | 兼容性报告附件 | 私密 |
| `knowledge_base.json` / `.md` | 已发布知识库 | 低 |
| `knowledge_candidates.json` / `operational_skills.md` | 待审批候选 / 操作经验库 | 低 |
| `conversation_memories/` | 每群每用户的持久会话记忆 | 私密 |

日志在 `logs/`（同样不入库）。

---

## 八、配置说明

完整带注释样例见 [`config.toml.sample`](../config.toml.sample)。要点：

| 段 | 关键项 |
|----|--------|
| `[bot]` | `qq_id` 机器人 QQ（须与 NapCat 登录账号一致）、`nickname` |
| `[napcat]` | WS 地址端口、`access_token`（须与 NapCat OneBot 配置里的 token 一致） |
| `[ollama]` | 模型标签、上下文长度（建议 ≥32k）、think 开关 |
| `[agent]` | `executable`（copilot 路径）、`profile`（对应 `.github/agents/<profile>.agent.md`）、推理力度、联网白名单、`monitor_all_group_messages` 批分流开关 |
| `[filter]` | 触发关键词、违禁词、strict_mode |
| `[feedback]` / `[github_issues]` | 反馈收集与 Issue 目标仓库、令牌 |
| `[compatibility]` | 兼容性仓库、令牌、追问超时、分析冷却、默认值文案 |
| `[web]` | 控制台开关与端口（8080 被占时改掉，如 18080） |
| `[groups]` | 黑/白名单 |

---

## 九、部署与启动

1. **依赖**：Python 3.11+、NapCat、Ollama、GitHub Copilot CLI（`npm install -g @github/copilot`）、Node.js（MCP 运行时用）
2. **安装**：`pip install -r requirements.txt`
3. **MCP 运行时**（不入库）：`mkdir runtime\mcp_web_search && cd runtime\mcp_web_search && npm install @zhafron/mcp-web-search`；网页抓取工具由仓库内 `src/public_web_mcp.py` 提供
4. **配置**：`copy config.toml.sample config.toml` 后按第八节填写；`copy .mcp.json.sample .mcp.json`
5. **NapCat 侧**：OneBot v11 配置里启用 **WebSocket 服务器**（监听 5004），token 与机器人配置一致；**QQ 登录账号必须等于 `bot.qq_id`**
   - ⚠️ NapCat 网络配置按 QQ 账号隔离：换绑机器人 QQ 后，新账号的 `onebot11_<新QQ>.json` 需要把 WS 服务器配置补上（可从旧账号配置复制），否则 QQ 能收发消息但机器人连不上
6. **启动**：`python bot.py`（`--debug` 开详细日志；同配置仅允许一个实例）
7. **验证**：日志出现 `NapCat connected` 与 `[WebUI] Control panel started`；群里 @机器人 应得到回复；`/bot status` 查看各组件状态

---

## 十、管理员命令

前缀默认 `/bot`（`[admin].command_prefix`），管理员 QQ 或群管理可用：

```
/bot help                     帮助
/bot status                   运行状态（连接、缓存、知识库、记录数）
/bot allow|deny <group_id>    群白名单 / 黑名单增删
/bot clearcache [group_id]    清空上下文缓存
/bot addkw <keyword>          追加触发关键词
/bot reload                   提示需重启（热重载占位）
/bot todo [list|all|done <id>|del <id>|clear done]      待办管理
/bot feedback [list|all|records|show #id|done <id>|del <id>|clear done]  反馈管理
/bot record [records|show #id]                          反馈查询
/bot kb status|list|show #id|add <内容>|update #id <内容>|delete #id     知识库管理
/bot learn list|all|show #id|approve #id|reject #id     知识候选审批
/bot compat list|show #id     兼容性报告查询（含发布状态与失败原因）
```

---

## 十一、测试与运维

```powershell
python -m unittest discover -s tests -v   # 85 个用例，不依赖 QQ/Ollama/Copilot，可离线跑
```

- **单实例锁**：占用 `127.0.0.1:17863`，重复启动直接退出
- **日志**：stdout 输出全量 INFO 日志，建议重定向到 `logs/` 保存；NapCat 自身日志在其 `napcat/logs/` 目录
- **常见问题**：
  - 端口 8080 被占 → 改 `[web].port`
  - Issue 创建 403 → PAT 未授权目标仓库（fine-grained 按仓库授权）
  - 机器人收不到消息 → NapCat 5004 未监听：QQ 未登录或 WS 服务未配置（见第九节第 5 步）
  - 换机器人 QQ → 改 `[bot].qq_id` + NapCat 登录新号 + 补新账号 OneBot 配置

---

## 十二、相关文档

- [README.md](../README.md) —— 项目简介、快速开始
- [agent_runtime.md](agent_runtime.md) —— Copilot CLI 接入 Ollama、64K 上下文与 GPU 调优、运营建议
- [design.md](design.md) —— 早期 Coze 方案设计（历史参考，部分已过时）
