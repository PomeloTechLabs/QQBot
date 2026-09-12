# 旧柚技术支持 Agent 运行说明

本机器人不再把模型函数调用自行拼装为 Agent。它把技术支持、网页核验和开源仓库研究交给 GitHub Copilot CLI；Copilot CLI 通过 Ollama 的 OpenAI 兼容接口调用由 `qwen3.8:latest` 派生的本机 `qwen3.8:agent-64k-gpu`。

## 已实现的行为

1. 明确 @机器人 的群内求助立即处理；普通群消息批处理默认关闭。网页控制台“群消息批处理”可启用后再设置条数和最长等待，以降低显卡推理频率。
2. 对技术求助，Agent 优先读取调用方注入的 `data/knowledge_base.md` 检索结果，资料不足或可能过期时才联网核验；开源工程问题优先看 GitHub/README。
3. Copilot 的权限被固定为两个公开网页 MCP：项目内锁定的 `@zhafron/mcp-web-search` 仅开放 `search_web`，用于关键词检索；`src/public_web_mcp.py` 仅开放 `fetch_public_url`，用于打开结果页，且会拒绝 localhost、内网和私有 IP。联网轮次不允许读取本地项目文件，知识库只以调用方注入的短片段提供；机器人显式拒绝 shell、文件写入、下载、浏览器控制和持久记忆。
4. 群内“问题 + 群友解决方案”以及可复用的机器人技术答复会由 Agent 整理成候选，而不是直接写入知识库。管理员可在网页“待审批知识”面板或通过 `/bot learn approve #id` 发布；默认 `auto_publish_learning = false`。
5. 正式技术回答、网页/开源资料核验和知识候选审核使用 Copilot `reasoning_effort = high`，同时由 Qwen 的思考模式生成内部推理；机器人只发送最终整理的结论。批量分流使用 `triage_reasoning_effort = minimal`，避免普通群聊消耗高推理预算。
6. 群消息命中报错、闪退、崩溃、功能失效、兼容性异常或明确建议等信号时，会先通过一次 `think=false` 的短 JSON 分析确认是否为反馈；私聊因为是用户主动找机器人，会逐条做同样的轻量判别。只有模型确认与旧柚、旧柚闪传或旧柚Pro有关，才会记录或进入 GitHub 流程；手机、其他 App、泛泛故障和无关吐槽不会建 Issue。记录会保存群昵称、原文及 LLM 整理字段（App 版本、设备、系统、复现步骤、预期/实际现象）。图片最大 15MB、视频最大 100MB，原始文件均保存在 `data/feedback_media`。
7. Bug 和功能建议都会自动创建 GitHub Issue 到 `yifengling0/VintagePomeloPro`；可用 `submit_feature_requests = false` 只保留 Bug 自动提交。Issue 会携带群昵称、原始描述、App/系统版本和设备信息；图片嵌入已校验的 QQ CDN 链接，视频附链接，本地原始附件仍保留。QQ CDN 链接可能过期；可用 `include_reporter_name = false` 或 `include_media_links = false` 关闭对应外发信息。令牌优先从本机 `config.toml` 的 `[github_issues].token` 读取，环境变量 `GITHUB_TOKEN` 可作为兼容兜底；网页接口和日志不会返回令牌。

## 部署

在 Windows PowerShell（管理员或有当前用户 npm 写入权限的终端）执行：

```powershell
# 安装并启动 Ollama，然后下载用户指定的本地模型
ollama pull qwen3.8:latest

# 安装成熟 Agent 运行时（二选一）
npm install -g @github/copilot
# 或：winget install GitHub.Copilot

# 验证
copilot --version
ollama list
```

Copilot CLI 启动时由机器人临时注入下列设置，不会把密钥交给第三方：

```text
COPILOT_PROVIDER_TYPE=openai
COPILOT_PROVIDER_BASE_URL=http://127.0.0.1:11434/v1
COPILOT_PROVIDER_WIRE_API=completions
COPILOT_MODEL=qwen3.8:agent-64k-gpu
COPILOT_PROVIDER_MAX_PROMPT_TOKENS=65536
```

## 64K 上下文、纯 GPU 与自动压缩

本机将 `OLLAMA_CONTEXT_LENGTH` 部署为 `65536`（64K），并在 `config.toml` 的 `[ollama]` 中固定 `context_length = 65536`；前者覆盖 Copilot 走 OpenAI 兼容接口时的服务端默认值，后者覆盖机器人原生 `/api/chat` 的回退调用。为让 27B 模型和 64K KV 缓存完整放入 22GB 显存，服务启用 Flash Attention 与 `q8_0` KV 缓存量化，并固定单并发。`data/qwen3.8-agent-64k-gpu.Modelfile` 从用户指定的 `qwen3.8:latest` 创建 `qwen3.8:agent-64k-gpu` 标签，并明确 `num_gpu 99`；它不复制模型权重。启动后必须以 `ollama ps` 显示 `100% GPU` 为验收标准。

Copilot CLI 会在长 Agent 会话接近上下文上限时自动生成摘要并替换早期上下文；较大的工具输出也会以预览/文件引用的方式处理。机器人不会永久保留群聊全文：它仅把最近、脱敏后的短片段传给一次支持任务，避免无关聊天占满模型窗口。Agent 配置还要求在阅读大型网页或仓库后先保留与当前问题相关的短摘要。

接着在项目目录启动：

```powershell
cd C:\aidev\QQBot\VintagePomeloBot
python bot.py
```

使用 `/bot status` 应看到 `支持 Agent: Copilot CLI / qwen3.8:latest`。本机已将 `agent.executable` 固定为 Copilot 原生 Windows 二进制，绕过 npm 启动器缺失平台包的问题；其他机器请按实际安装路径修改。

## 运营建议

- 在本机 `config.toml` 的 `[github_issues]` 段填写具备目标仓库 `Issues: write` 权限的 fine-grained token：`token = "..."`，然后重启机器人。令牌不会通过网页接口或日志输出；令牌缺失、仓库不可访问或创建失败时，bug 仍会完整保存在本地反馈记录中，并显示失败原因。环境变量 `GITHUB_TOKEN` 仍可作为兼容兜底。
- 网页研究会先经 `web-search-search_web` 查找结果，再由 `public-web-fetch_public_url` 打开官方页面或 README 核验；不再依赖把搜索 URL 当作普通网页抓取。搜索服务使用 DuckDuckGo 并在包内按需回退，公开搜索源仍可能临时限流；若失败，界面会显示真实工具错误，而不是笼统提示“所有引擎不可用”。运行包固定在 `runtime/mcp_web_search`，升级时应先在测试环境重新做 MCP 握手和检索验证。
- `qwen3.8:latest` 可以工作，但 Copilot 官方文档建议 Agent 使用更大的上下文窗口；若工具调用频繁失败，应先提高 Ollama 的上下文长度，而不是开放 shell/write 权限。
- `monitor_all_group_messages = true` 会开启群消息批量分流，而非逐条调用模型。默认通过 `group_batch_size = 50` 与 `group_batch_wait_seconds = 600`（10 分钟）控制吞吐和最长等待；这两个值可在网页控制台修改。需要极低负载时设为 `false`，保留 @机器人、昵称与产品求助关键词触发。
- 默认 Agent 可访问互联网公开资料。若要收紧权限，设置 `allow_all_web_urls = false` 并通过 `web_allow_urls` 维护 HTTPS 域名白名单；不要加入 `localhost` 或内网 IP。
- 查看候选：`/bot learn list`；审核原文：`/bot learn show #id`；通过/拒绝：`/bot learn approve #id`、`/bot learn reject #id`。

## 测试

```powershell
python -m unittest discover -s tests -v
```

该测试不会启动 QQ、Copilot 或 Ollama；实际部署前还应验证一次 `copilot -p` 能通过本地 Ollama 返回结果。
