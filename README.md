# AI Content Miner

> 从海量社交媒体信息中自动完成抓取、智能筛选、研报生成与企业微信推送的知识工作流。

## 项目定位

本项目解决的核心问题是：在信息过载的环境中，如何从大量低质量社交媒体内容中自动筛选高价值信息，并生成结构化研报。

### 平台处理边界

- 小红书和知乎使用 MediaCrawler，并进入规则、语义去重、互动质量和博主画像四层
  筛选。四层原始分数会正规化为 0–10 综合分，再生成逐条报告并推送企业微信。
- Twitter/X 使用独立流程：规则筛选、Embedding 多主题筛选、回复区筛选、
  极简摘要、分层标签、`data/processed/x.jsonl` 和 `reports/x.html`。
- GitHub 使用官方 REST API 搜索最近活跃的公开仓库，仅对最终候选读取 README，
  并通过稳定仓库 ID 去重。首版不采集 Issues、Releases 或 Discussions。
- Reddit 通过指定社区的 `new/.rss` 尽量获取最新帖子，采集阶段只做时间窗口、
  格式校验和帖子 ID 去重；关键词只记录命中情况。随后进入独立的内容规则、主题
  Embedding、质量评分和末端排序限量，再生成极简摘要、结构化 JSONL、聚合页面和
  Reddit 原帖直链通知。RSS 缺失的互动指标不会按零分处罚。
- 主入口对 Twitter、GitHub 和 Reddit 的专用流程做显式路由，平台之间不会串用筛选器。

### 适用场景

- AI Agent、量化投资、科技前沿等领域的每日信息聚合
- 研究团队自动生成行业简报
- 个人知识工作流自动化

## 功能列表

| 功能 | 说明 |
| --- | --- |
| 数据爬取 | 小红书、知乎使用 MediaCrawler；X 使用实验性的 twscrape；Reddit 使用 Atom/RSS。 |
| 平台筛选 | 小红书/知乎使用四层筛选；GitHub、Reddit 和 Twitter 使用各自隔离的专用流程。 |
| 综合评分 | 各平台输出可审计的 0–10 综合分；Reddit 不使用 RSS 不提供的互动指标。 |
| Twitter 信息流 | 使用独立三层筛选、极简摘要、结构化 JSONL 和聚合页面。 |
| Reddit 信息流 | 使用独立筛选、极简摘要、结构化 JSONL、聚合页面和 Reddit 原帖直链。 |
| 多模式输出 | 小红书、知乎等非社交信息流内容按长度生成文本卡片或完整 HTML 研报。 |
| 企业微信推送 | 使用 Markdown V2；Twitter 和 Reddit 推送短摘要与原帖直链。 |
| RadIter 日志 | 记录每次决策过程，为后续持续优化提供依据。 |

## 快速开始

### 前置条件

- Python 3.9 或更高版本；使用 X/twscrape 时需要 Python 3.10 或更高版本
- [uv](https://docs.astral.sh/uv/)（用于安装并运行 MediaCrawler）
- OpenAI API Key，或兼容接口的 API Key
- 小红书、知乎和 GitHub 完整流程需要企业微信机器人 Webhook；Reddit 通知默认开启但
  可通过 `REDDIT_ENABLE_WECOM=False` 关闭；Twitter 通知默认关闭
- GitHub 流程需要从环境变量读取的 `GITHUB_TOKEN`

### 1. 克隆项目

```bash
git clone https://github.com/kassidylee/ai-content-miner.git
cd ai-content-miner
```

### 2. 安装依赖

```bash
python3 -m pip install -r requirements.txt
```

也可以用 `uv` 创建项目独立环境，避免影响系统 Python：

```bash
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -r requirements.txt
```

本项目与 MediaCrawler 使用相互独立的 Python 环境。本项目的
`requirements.txt` 只安装内容处理和推送依赖；MediaCrawler 依赖由其
`uv.lock` 管理，避免把两个项目的 Playwright、pandas 等版本混装。
twscrape 属于本项目依赖，当前固定为 `0.20.1`；其会话数据库和浏览器 Cookie 只保存在
实际运行任务的电脑上。Reddit RSS 使用 Python 标准库解析 Atom，并复用已有的
`requests`，不需要 OAuth、PRAW、登录 Cookie 或额外账号。

### 3. 配置项目

凭证只通过环境变量注入，不要写入 `config.py`。PowerShell 当前会话示例：

```powershell
$env:AI_API_KEY = "your-chat-api-key"
$env:AI_BASE_URL = "https://your-chat-provider.example/v1"
$env:AI_MODEL_NAME = "your-chat-model"
$env:WECOM_WEBHOOK = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=..."
```

通用 Embedding 配置（Twitter、小红书、知乎，以及未单独覆盖的 GitHub/Reddit）可以
直接使用 DashScope 原生 SDK，不需要填写 OpenAI-compatible Base URL：

```powershell
$env:EMBEDDING_PROVIDER = "dashscope"
$env:DASHSCOPE_API_KEY = "your-dashscope-api-key"
$env:EMBEDDING_MODEL = "text-embedding-v4"
```

如果使用其他 OpenAI-compatible Embedding 服务，则改为：

```powershell
$env:EMBEDDING_PROVIDER = "openai"
$env:EMBEDDING_API_KEY = "your-embedding-api-key"
$env:EMBEDDING_BASE_URL = "https://your-provider.example/v1"
$env:EMBEDDING_MODEL = "your-embedding-model"
```

GitHub Embedding 可以使用另一家实际支持 `/embeddings` 的服务：

```powershell
$env:GITHUB_EMBEDDING_API_KEY = "your-embedding-api-key"
$env:GITHUB_EMBEDDING_BASE_URL = "https://api.openai.com/v1"
$env:GITHUB_EMBEDDING_MODEL = "text-embedding-3-small"
```

Reddit 也可以单独覆盖 Embedding 服务：

```powershell
$env:REDDIT_EMBEDDING_API_KEY = "your-embedding-api-key"
$env:REDDIT_EMBEDDING_BASE_URL = "https://api.openai.com/v1"
$env:REDDIT_EMBEDDING_MODEL = "text-embedding-3-small"
```

复制环境变量模板，并在本地 `.env` 中填写敏感配置：

```bash
cp .env.example .env
```

```dotenv
LLM_API_KEY=your-api-key-here
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL_NAME=your-model-name

# 非社交信息流流程必填；Reddit/Twitter 仅在各自通知开关开启时必填。
WECOM_WEBHOOK=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxxxx
```

`.env` 已被 Git 忽略，不得提交。程序启动时会自动加载它；部署环境也可以直接导出
同名环境变量，且外部环境变量优先于 `.env`。

非敏感运行配置继续在 `config.py` 中维护：

```python
# 报告预览地址，必须可被企业员工访问
# 开发环境：http://127.0.0.1:8000/reports（仅本机可访问）
# 生产环境：请填写企业内部可访问的实际地址
REPORT_BASE_URL = "http://127.0.0.1:8000/reports"

# Twitter 通知默认关闭
TWITTER_ENABLE_WECOM = False

# Reddit 通知默认开启；关闭后仍会生成结构化结果和聚合页面
REDDIT_ENABLE_WECOM = True
```

报告预览地址和非敏感开关继续在 `config.py` 中配置。Twitter 和 Reddit 仅在各自
通知开关开启时使用企业微信 Webhook；Reddit 通知直接链接原帖，不依赖
`REPORT_BASE_URL` 或 COS。

其他配置项请参阅 `config.py` 中的注释。

### 4. 配置 MediaCrawler

当前桥接器明确对齐 MediaCrawler commit
`c9a111be73586bdf6fc44536f088e4db6ed86d64`。在项目根目录克隆并安装：

```bash
git clone --recurse-submodules https://github.com/kassidylee/ai-content-miner.git
cd ai-content-miner

git submodule update --init --recursive
cd MediaCrawler
uv sync
cd ..

git checkout c9a111be73586bdf6fc44536f088e4db6ed86d64
uv sync
cd ..
```

该 MediaCrawler 版本要求 Python 3.11 或更高版本，`uv sync` 会按其
`pyproject.toml` 和 `uv.lock` 建立独立环境。MediaCrawler 首次运行通常需要扫码登录；
默认 CDP 模式还需要按其说明准备 Chrome。若 MediaCrawler 位于其他目录，请在
`config.py` 中设置绝对路径 `MEDIACRAWLER_PATH`。

### 5. 启动报告预览服务

```bash
python3 server.py
```

然后在浏览器中访问：

```text
http://127.0.0.1:8000/reports/
```

确认报告目录可以正常访问。

### 可选：测试 X 关键词采集

X 不经过 MediaCrawler。本分支使用 `twscrape==0.20.1` 的异步 `API.search()` 接口，
并把结果转换为项目已有的 JSONL 格式。2026-07-22 已使用一个专用账号完成一次最多
3 条结果的真实只读烟雾测试，搜索、JSONL 写入和下游解析均成功。该功能仍处于实验阶段；
一次验证成功不代表 X 非公开接口能够长期稳定。

先在浏览器中正常登录 X，然后打开浏览器开发者工具：

1. 在 Chrome 中按 `⌥⌘I`，打开 **Application**。
2. 在左侧选择 **Storage → Cookies → https://x.com**。
3. 分别找到 `auth_token` 和 `ct0`，只复制它们的 **Value**。
4. 不要把这些值发到聊天、截图或 GitHub；它们代表已登录会话。

运行本地会话创建脚本：

```bash
.venv/bin/python scripts/setup_twscrape_session.py
```

脚本只询问 X 用户名、`auth_token` 和 `ct0`，不会询问 X 密码。两个 Cookie 输入均不显示，
最终只写入 Git 已忽略的 `.local/x/twscrape.db`，并将文件权限设为仅当前用户可读写。
不同电脑不能通过 Git 同步该数据库，需要各自在本机创建。

随后只测试采集，不调用模型、不生成报告、不发送企业微信：

```bash
.venv/bin/python scripts/smoke_test_twscrape.py "AI Agent" --limit 3
```

采集测试通过后，在 `.env` 中启用 Twitter 路由和 Embedding 强制筛选：

```dotenv
CRAWL_PLATFORM=x
TWITTER_EMBEDDING_ENABLED=true
TWITTER_EMBEDDING_FILTER_MODE=enforce
```

先单独验证 Embedding 服务；该检查只发送一条内置测试文本，不会开始爬取：

```bash
.venv/bin/python scripts/smoke_test_twitter_embedding.py
```

只有会话和 Embedding 两个烟雾测试均成功后，才运行完整 Twitter 工作流：

```bash
.venv/bin/python main.py
```

### 可选：测试 GitHub 仓库搜索

创建一个只读 GitHub Token，并仅在当前 PowerShell 会话中设置。不要把 Token 写入
`config.py`、README、日志或提交记录：

```powershell
$env:GITHUB_TOKEN = "github_pat_your_token"
python scripts/smoke_test_github.py "AI Agent" "LLM" --limit 3
python scripts/smoke_test_github_embedding.py
```

烟雾测试只调用 GitHub REST API 并写入本次运行 JSONL，不调用 LLM、不生成报告、
不发送企业微信，也不写入已处理状态。成功后在 `.env` 中设置平台，并在
`config.py` 中调整非敏感搜索参数：

```dotenv
CRAWL_PLATFORM=github
```

```python
SEARCH_KEYWORDS = ["AI Agent", "LLM"]
GITHUB_LOOKBACK_DAYS = 7
GITHUB_MIN_STARS = 10
```

GitHub 仓库搜索按 `pushed_at` 时间窗口查询，跨关键词使用 GitHub repository ID 去重，
应用 Star 和总量限制后才为最终候选读取 README。完整工作流成功后，仓库 ID 才写入
`data/state/github_seen_ids.json`。
### 可选：测试 Reddit RSS 采集

Reddit 不经过 MediaCrawler。当前方案对配置中明确社区的 `new/.rss` 请求每个社区最多
100 条，采集阶段只保留最近 168 小时、格式有效且未重复的帖子。关键词命中只写入审计
字段，不会在进入 Reddit 专用 pipeline 前淘汰内容。RSS 不提供帖子分数、点赞比例、
评论数或 flair，程序会将这些字段明确标记为不可用，不会编造互动数据。

只测试一个社区的 RSS 请求、Atom 解析和下游加载：

```bash
.venv/bin/python scripts/smoke_test_reddit_rss.py "model" \
  --subreddit LocalLLaMA --limit 3
```

烟雾测试成功后修改：

```python
CRAWL_PLATFORM = "reddit"
REDDIT_RSS_SUBREDDITS = ["LocalLLaMA"]
REDDIT_RSS_KEYWORDS = ["LLM", "model", "agent", "inference"]
REDDIT_RSS_RESULTS_PER_SUBREDDIT = 100
REDDIT_RSS_MAX_CANDIDATES = 0
REDDIT_FINAL_RESULT_LIMIT = 20
```

### 可选：测试 Reddit JSON 采集

Reddit 不经过 MediaCrawler，也不使用官方 Data API。采集器逐个读取
`REDDIT_SUBREDDITS` 中明确社区的 `new.json`，再在本地按关键词和最近 168 小时
过滤。当前不使用 `r/all`、全站搜索、RSS、登录 Cookie 或代理轮换。

只测试一个社区的 JSON 请求、字段转换和下游解析：

```bash
.venv/bin/python scripts/smoke_test_reddit.py "LLM" \
  --subreddit LocalLLaMA --limit 3
```

烟雾测试成功后，将平台改为：

```python
CRAWL_PLATFORM = "reddit"
```

并按需调整明确的社区列表：

```python
REDDIT_SUBREDDITS = ["LocalLLaMA", "MachineLearning", "artificial"]
```

匿名 JSON 没有长期兼容性承诺，可能因本机网络环境或限流返回 `403`、`429`。
采集器会明确报错；遇到 `429` 时会显示 `Retry-After` 并停止本次运行，不会通过
Cookie、代理或 IP 轮换规避限制。

### 6. 运行完整工作流

```bash
python3 main.py --check-config
python3 main.py
```

`--check-config` 只检查必填配置和当前采集器。小红书、知乎会检查 MediaCrawler 路径、
commit 和运行解释器；X 会检查 twscrape 版本、本地会话数据库和已处理状态；Reddit
会检查社区列表、请求参数、User-Agent 和已处理状态，
不会启动爬虫、调用模型或发送企业微信消息。配置、爬虫退出码、当次无数据或推送
失败时，主程序均返回非零退出状态。

## 项目结构

```text
ai-content-miner/
├── config.py                   # 项目配置，不要提交真实凭证
├── main.py                     # 主入口，全自动流水线
├── server.py                   # 静态报告预览服务
├── requirements.txt            # Python 依赖
├── README.md                   # 使用说明
├── steps.sh                    # 一键部署脚本
│
├── analyzer/                   # 分析模块
│   ├── __init__.py
│   ├── filter.py               # 小红书/知乎四层筛选
│   ├── reddit_rules.py         # Reddit 内容与来源规则
│   ├── reddit_embedding.py     # Reddit 多主题语义筛选
│   ├── reddit_enricher.py      # Reddit 极简标题和摘要
│   ├── reddit_quality.py       # Reddit 无互动依赖的质量评分
│   ├── reddit_pipeline.py      # Reddit 三层筛选编排
│   ├── twitter_rules.py        # Twitter 第一层规则
│   ├── twitter_engagement.py   # Twitter 加权互动指标
│   ├── twitter_embedding.py    # Twitter 多主题语义筛选
│   ├── twitter_comments.py     # Twitter 回复区筛选
│   ├── twitter_daily_selector.py # Twitter 每日 8+4 选择
│   ├── twitter_enricher.py     # Twitter 摘要和标签
│   └── twitter_pipeline.py     # Twitter 三层筛选编排
│
├── crawler/                    # 爬虫模块
│   ├── __init__.py
│   ├── base.py                 # 采集器公共结果与接口
│   ├── factory.py              # 按平台选择采集器
│   ├── github_bridge.py        # GitHub REST 仓库搜索与本地去重
│   ├── mediacrawler_bridge.py  # MediaCrawler 调度
│   ├── twscrape_bridge.py      # X 关键词搜索与本地去重
│   └── reddit_rss_bridge.py    # Reddit Atom/RSS 采集
├── scripts/
│   ├── setup_twscrape_session.py # 创建本地 Cookie 会话
│   ├── smoke_test_github.py      # 只读 GitHub 仓库搜索烟雾测试
│   ├── smoke_test_twitter_embedding.py # Twitter Embedding 连通性测试
│   ├── smoke_test_twscrape.py    # 只读 X 搜索烟雾测试
│   └── smoke_test_reddit_rss.py  # 只读 Reddit RSS 烟雾测试
│
├── output/                     # 输出模块
│   ├── __init__.py
│   ├── generator.py            # 文本卡片和 HTML 研报生成
│   ├── reddit_feed.py          # Reddit 聚合信息流页面
│   └── twitter_feed.py         # Twitter 聚合页面
│
├── workflows/
│   ├── reddit.py               # Reddit 独立工作流
│   └── twitter.py              # Twitter 独立工作流
│
├── notifier/                   # 推送模块
│   ├── __init__.py
│   ├── reddit_wecom.py         # Reddit 短摘要与原帖直链通知
│   └── wecom.py                # 企业微信推送与配置校验
│
├── utils/                      # 工具模块
│   ├── __init__.py
│   ├── parser.py               # 文章解析，唯一权威实现
│   ├── reddit_result_store.py  # Reddit JSONL 事实源
│   └── raditer.py              # RadIter 决策日志
│
├── data/                       # 爬取数据，运行时自动创建
├── reports/                    # 生成的报告，运行时自动创建
├── logs/                       # 日志文件，运行时自动创建
└── articles/                   # 手动输入文章，可选
```

## 工作流程

```text
小红书、知乎：
MediaCrawler
  -> 本次运行内容文件加载与标准化
  -> 规则筛选
  -> 语义去重（提供对比样本时启用）
  -> 互动质量筛选
  -> 博主画像筛选
  -> 0–10 综合评分
  -> 输出生成
  -> 企业微信推送

GitHub：
GitHub REST API 仓库搜索
  -> 最近活跃时间和 Star 过滤
  -> 跨关键词 repository ID 去重
  -> 最终候选 README 拉取
  -> 本次运行 JSONL 与统一标准化
  -> 现有筛选、报告和通知链路
  -> 成功后确认已处理状态

Twitter/X：
twscrape
  -> Twitter 专用标准化
  -> Twitter 规则筛选
  -> Twitter 多主题 Embedding 筛选
  -> Twitter 回复区筛选
  -> 极简摘要与分层标签
  -> data/processed/x.jsonl
  -> reports/x.html
  -> 可选企业微信通知

Reddit：
指定 subreddit 的 new/.rss
  -> Atom 解析和正文清洗
  -> 时间窗口、格式校验和 ID 去重
  -> 记录关键词命中（不前置淘汰）
  -> 本次运行 JSONL
  -> 内容与来源规则
  -> 多主题 Embedding 相关性筛选
  -> 相关性、深度、证据、时效和来源质量评分
  -> 按质量分排序并限制最终候选数
  -> 极简标题和摘要
  -> data/processed/reddit.jsonl
  -> reports/reddit.html
  -> 可选企业微信短摘要与 Reddit 原帖直链
```

每次 MediaCrawler 调用都通过 CLI 传入平台、关键词、数量限制、JSONL 格式和
独立输出目录，不会修改 MediaCrawler 的 `config/base_config.py`。当前下游不消费
评论数据，因此调用时明确关闭评论抓取。加载器只读取本次运行产生的
`<type>_contents_<date>.jsonl`，不会递归读取历史输出，也不会把 comments/creators
误当成文章。

twscrape 同样为每次执行建立独立输出目录，只读取该次搜索返回的数据。它按关键词调用
`Latest` 搜索，跨关键词按帖子 ID 去重，保留最近 168 小时的内容，再应用总数量上限。
已处理 ID 仅在完整处理和推送流程成功后写入本地状态；烟雾测试不会确认状态，因此可以
重复测试同一批帖子。

Reddit RSS 同样只读取本次运行的数据。一个社区暂时失败时会继续其他社区；全部社区
失败时本次运行返回失败。多社区之间默认至少等待 31 秒，并尊重响应中的限流重置时间。
每个社区向 RSS 请求最多 100 条，但服务端实际返回数可能更少；默认不在专用 pipeline
之前设置跨社区总量上限。筛选阶段只使用 RSS 确实提供的正文、链接、作者、社区和
发布时间；当 `metrics_available=false` 时不会使用分数、点赞比例或评论数。

## 输出模式

小红书和知乎按内容字数选择输出格式：

| 内容字数 | 输出格式 | 说明 |
| --- | --- | --- |
| 少于 500 字 | 纯文本卡片（`.txt`） | 提取核心观点，不进行深度分析。 |
| 不少于 500 字 | 完整 HTML 研报（`.html`） | 包含评分卡、雷达图和深度精读。 |

中等长度内容（500 至 1500 字）和长内容（超过 1500 字）目前均生成完整 HTML 研报。

Twitter 不生成逐条研报。所有候选推文及筛选审计追加写入
`data/processed/x.jsonl`，最终保留项聚合展示在 `reports/x.html`，主链接直接跳转
原始推文。

Reddit 同样不生成逐条研报。所有候选帖子及筛选审计追加写入
`data/processed/reddit.jsonl`，最终保留项聚合展示在 `reports/reddit.html`。企业微信
按完整帖子边界控制在 4096 字节以内，默认最多推送 5 条，链接直接跳转 Reddit 原帖；
不需要 COS 或 `REPORT_BASE_URL`。

### 短内容卡片示例

```text
### Agent 时代系统研究还能做什么

来源：小红书 | 评分：7.5/10
核心价值：探讨 AI Agent 时代系统研究的发展方向。

维度评分：
- 洞察深度：1.8/2
- 时效性：1.5/2
- 启发性：1.6/2

建议：本文为短内容，系统已提取核心观点。如需深度分析，建议查找原始出处。
```

### 完整 HTML 研报

字数不少于 500 字的内容将生成完整 HTML 研报，包含以下部分：

- 评分卡：SVG 雷达图和评分表，固定展示在报告顶部。
- 简报：包括背景、核心内容和一句话总结。
- 深度精读：包括观点拆解、技术细节、代码解析、应用场景和局限性分析。

## 配置说明

### 环境变量与 `config.py` 关键配置项

| 配置项 | 说明 | 示例 |
| --- | --- | --- |
| `LLM_API_KEY` | `.env` 中的 LLM API Key | `your-api-key-here` |
| `LLM_BASE_URL` | `.env` 中的 OpenAI 兼容 API 地址 | `https://api.openai.com/v1` |
| `LLM_MODEL_NAME` | `.env` 中由 API 服务商提供的实际模型 ID | `your-model-name` |
| `AI_API_KEY` / `AI_BASE_URL` / `AI_MODEL_NAME` | 负责人分支使用的兼容别名；`LLM_*` 已设置时优先使用 `LLM_*` | 同上 |
| `CRAWL_PLATFORM` | `.env` 中选择运行平台 | `x` |
| `EMBEDDING_API_KEY` | `.env` 中独立的 Embedding API Key | `your-embedding-api-key-here` |
| `EMBEDDING_BASE_URL` | `.env` 中的 Embedding API 地址 | `https://api.openai.com/v1` |
| `EMBEDDING_MODEL` | 非 Twitter 路线使用的实际 Embedding 模型 ID | `text-embedding-3-small` |
| `TWITTER_EMBEDDING_MODEL` | 启用 Twitter Embedding 后使用的模型 ID | `text-embedding-3-small` |
| `WECOM_WEBHOOK` | `.env` 中的企业微信 Webhook | `https://qyapi.weixin.qq.com/...` |
| `SCORE_THRESHOLD` | 四层正规化综合分阈值，范围 0–10 | `6.0` |
| `SEMANTIC_DEDUP_THRESHOLD` | 非 Twitter 语义重复判定阈值 | `0.85` |
| `COMMENT_PASS_THRESHOLD` | 非 Twitter 互动质量最低分 | `0.5` |
| `AUTHOR_PROFILE_THRESHOLD` | 非 Twitter 博主画像最低分 | `0.9` |
| `REPORT_BASE_URL` | 报告预览服务地址，必须可被员工访问 | `http://127.0.0.1:8000/reports` |
| `CRAWL_PLATFORM` | 爬取平台 | `xhs`、`zhihu`、`x`、`github` |
| `SEARCH_KEYWORDS` | 搜索关键词； 建议使用带技术意图的组合查询 | `["AI Agent", "大模型"]` |
| `GITHUB_TOKEN` | GitHub API Token，只从环境变量读取 | `github_pat_...` |
| `GITHUB_LOOKBACK_DAYS` | 仅保留最近推送过代码的仓库 | `7` |
| `GITHUB_MIN_STARS` | 仓库最低 Star 数 | `0` |
| `GITHUB_RESULTS_PER_QUERY` | 每个关键词最多读取的候选数 | `20` |
| `GITHUB_README_MAX_CHARS` | 最终候选 README 最大字符数 | `6000` |
| `EMBEDDING_API_KEY` | 通用 Embedding API Key，只从环境变量读取 | - |
| `EMBEDDING_BASE_URL` | 通用 Embedding API 地址 | `https://api.openai.com/v1` |
| `EMBEDDING_MODEL` | 通用 Embedding 模型 | `text-embedding-3-small` |
| `GITHUB_EMBEDDING_API_KEY` | GitHub Embedding 专用 Key，只从环境变量读取 | - |
| `GITHUB_EMBEDDING_BASE_URL` | 支持 `/embeddings` 的 API 地址 | `https://api.openai.com/v1` |
| `GITHUB_EMBEDDING_MODEL` | GitHub 关键词相似度模型 | `text-embedding-3-small` |
| `GITHUB_EMBEDDING_THRESHOLD` | 仓库内容与关键词的最低相似度 | `0.35` |
| `GITHUB_EMBEDDING_FILTER_MODE` | GitHub 语义筛选模式 | `enforce`、`shadow` |
| `GITHUB_QUALITY_MIN_SCORE` | GitHub 质量筛选最低分 | `5.0` |
| `MEDIACRAWLER_PATH` | MediaCrawler 仓库路径 | `./MediaCrawler` |
| `MEDIACRAWLER_COMMIT` | 已对齐并校验的 commit | `c9a111b...` |
| `MEDIACRAWLER_PYTHON` | 未使用 uv 时的 Python >=3.11 解释器，可留空 | `/path/to/python` |
| `TWSCRAPE_DB_FILE` | X 本地会话数据库，不得提交 | `.local/x/twscrape.db` |
| `TWSCRAPE_RESULTS_PER_QUERY` | 每个 X 关键词最多读取条数 | `50` |
| `TWSCRAPE_LOOKBACK_HOURS` | X 帖子的本地时间窗口 | `168` |
| `REDDIT_RSS_SUBREDDITS` | Reddit 明确社区列表 | `["LocalLLaMA"]` |
| `REDDIT_RSS_KEYWORDS` | 仅用于记录 Reddit RSS 关键词命中，不前置淘汰 | `["LLM", "model", "agent"]` |
| `REDDIT_RSS_RESULTS_PER_SUBREDDIT` | 每个社区向 RSS 请求的条目上限 | `100` |
| `REDDIT_RSS_MAX_CANDIDATES` | 进入专用 pipeline 前的跨社区总量上限；`0` 不限制 | `0` |
| `REDDIT_RSS_REQUEST_INTERVAL_SECONDS` | 多社区请求间隔 | `31` |
| `REDDIT_RSS_LOOKBACK_HOURS` | Reddit 本地时间窗口 | `168` |
| `REDDIT_RULE_FILTER` | Reddit 内容长度和排除词规则 | 字典 |
| `REDDIT_EMBEDDING_API_KEY` | Reddit Embedding 专用 Key；默认回退通用配置 | - |
| `REDDIT_EMBEDDING_BASE_URL` | Reddit Embedding API 地址 | `https://api.openai.com/v1` |
| `REDDIT_EMBEDDING_MODEL` | Reddit 主题相似度模型 | `text-embedding-3-small` |
| `REDDIT_INTEREST_TOPICS` | Reddit Embedding 主题及独立阈值 | 列表 |
| `REDDIT_EMBEDDING_FILTER_MODE` | Reddit 语义筛选模式 | `enforce`、`shadow` |
| `REDDIT_QUALITY_WEIGHTS` | Reddit 五维质量评分权重 | 字典 |
| `REDDIT_QUALITY_MIN_SCORE` | Reddit 专用质量筛选最低分 | `6.0` |
| `REDDIT_FINAL_RESULT_LIMIT` | 质量分排序后的最终候选上限 | `20` |
| `REDDIT_PROCESSED_FILE` | Reddit 候选与筛选审计 JSONL | `data/processed/reddit.jsonl` |
| `REDDIT_REPORT_FILE` | Reddit 聚合信息流页面 | `reports/reddit.html` |
| `REDDIT_FEED_RETENTION_DAYS` | 聚合页保留最近多少天 | `30` |
| `REDDIT_FEED_MAX_ITEMS` | 聚合页最多展示条数 | `200` |
| `REDDIT_ENABLE_WECOM` | 是否发送 Reddit 原帖直链通知 | `True` |
| `REDDIT_WECOM_MAX_ITEMS` | Reddit 每次通知最多完整帖子数 | `5` |
| `REDDIT_WECOM_MAX_BYTES` | Reddit 通知字节上限 | `4096` |
| `TWITTER_RULE_FILTER` | 仅供 Twitter 使用的第一层规则 | 字典 |
| `TWITTER_EMBEDDING_ENABLED` | 是否启用 Twitter Embedding 第二层筛选 | `False` |
| `TWITTER_INTEREST_TOPICS` | Twitter Embedding 主题与独立阈值 | 列表 |
| `TWITTER_EMBEDDING_FILTER_MODE` | Twitter 语义筛选模式 | `shadow`、`enforce` |
| `TWITTER_COMMENT_FILTER` | Twitter 回复区筛选阈值 | 字典 |
| `TWITTER_DAILY_PRIMARY_LIMIT` | 每日主推上限 | `8` |
| `TWITTER_DAILY_MORE_LIMIT` | 每日折叠补充上限 | `4` |
| `TWITTER_DAILY_AUTHOR_LIMIT` | 同一作者每日上限 | `1` |
| `TWITTER_DAILY_TOPIC_REPEAT_PENALTY` | 已选同主题每条重排扣分 | `3.0` |
| `TWITTER_DAILY_STANDARD_MAX_AGE_HOURS` | 正常参与日刊排序的最长年龄 | `48` |
| `TWITTER_DAILY_FALLBACK_MAX_AGE_HOURS` | 高互动且有证据内容的最长年龄 | `168` |
| `TWITTER_DAILY_FALLBACK_MIN_SOCIAL_SCORE` | 较旧内容所需最低互动质量 | `0.85` |
| `TWITTER_DAILY_MIN_WEIGHTED_ENGAGEMENT` | 最终发布所需最低加权互动 | `5.0` |
| `TWITTER_DAILY_HISTORY_DAYS` | 同事件重复推送抑制天数 | `7` |
| `TWITTER_TAG_TAXONOMY` | Twitter 受控分层标签 | 列表 |
| `TWITTER_ENABLE_WECOM` | 是否发送 Twitter 摘要通知 | `False` |

## 当前接入边界

- 主入口按平台分流。X 调用 `workflows/twitter.py`，Reddit 调用
  `workflows/reddit.py`；其他平台不会调用两者的专用筛选、信息流或通知模块。
  - GitHub 首版只发现公开仓库；Releases 是下一种建议接入的数据类型，Issues 和
  Discussions 保持独立。
- 小红书、知乎使用四层筛选。当前主入口没有加载历史对比样本，
  因此语义去重层会明确记录为跳过；规则、互动质量和博主画像层仍正常执行。
- 四层筛选保留原始乘积分数，并将中性组合映射为 6 分、最高组合映射为 10 分。
  `SCORE_THRESHOLD` 只与正规化后的 0–10 分比较。
- Lingzao、RAL 和旧 AI 评分器不再由主流程调用。
- 手动 `articles/` 加载不会在自动爬取流程中回退触发。
- MediaCrawler 的 `detail`、`creator` 参数尚未在本项目配置中接入，启动检查会明确
  拒绝这两种模式；当前只支持 `search`。
- 登录方式当前只接入 `qrcode`、`phone`；为避免凭证出现在命令日志或仓库配置中，
  本轮没有接入 cookie 参数。
- X 仅接入 twscrape 的只读关键词搜索，不包含发帖、点赞、关注、私信、账号池扩容、
  CAPTCHA 绕过或代理轮换。
- Reddit 仅低频读取配置社区的 `new/.rss`，不使用 OAuth、PRAW、登录 Cookie、
  搜索 feed 或代理轮换；其独立质量评分明确忽略 RSS 不提供的互动指标。访问规则变化
  或限流仍会使采集失败。
- twscrape 使用非公开 X GraphQL 接口，可能因 X 改版、Cookie 失效、限流或账号验证而
  中断；任何失败都会返回非零状态，不会显示为采集成功。
- X 使用独立三层筛选和结构化聚合页，不调用非 Twitter 四层筛选或逐条报告生成器。
- Reddit 使用独立筛选、极简摘要、结构化聚合页和原帖直链通知，不调用通用逐条报告、
  COS 上传或 `REPORT_BASE_URL` 链路。

## 常见问题

### Q1：MediaCrawler 爬取失败怎么办？

- 确认 `MEDIACRAWLER_PATH` 配置正确。
- 在 `MediaCrawler/` 目录中手动运行测试。
- 检查网络环境，确保 MediaCrawler 可以访问目标社交媒体平台。

### Q2：企业微信中的报告链接无法打开怎么办？

- Reddit 和 Twitter 通知直接链接原帖，不使用本地报告地址；先确认运行环境和员工设备
  可以访问对应原帖。
- 以下检查只适用于包含“在线阅读”报告链接的小红书、知乎、GitHub 等流程：
- 检查 `REPORT_BASE_URL` 是否配置为企业员工可访问的地址。
- 确认 `python3 server.py` 正在运行。
- 使用内网地址时，确认员工设备与服务器位于同一网络。
- 如需从公网访问，可使用 ngrok 等内网穿透工具。

### Q3：API 调用成本较高怎么办？

- 使用成本更低的模型，例如 `gpt-3.5-turbo` 或 `deepseek-chat`。
- 提高 `SCORE_THRESHOLD`，减少进入输出环节的非 Twitter 内容。
- Twitter 可先保持 Embedding `shadow` 模式观察分布，再决定是否切换到 `enforce`。

### Q4：为什么短内容会生成卡片而不是研报？

小红书、知乎等通用报告流程会根据内容字数自动判断输出模式：

- 少于 500 字：生成纯文本卡片，不进行深度分析。
- 不少于 500 字：生成完整 HTML 研报，包括雷达图和深度解析。

Twitter 和 Reddit 使用社交信息流输出，不按字数生成逐条研报。

### Q5：四层评分为什么仍使用 0–10？

四层内部使用乘积分数，原始范围与报告阈值不一致。系统会保留原始分数用于审计，
同时将各层均为中性的组合映射为 6 分，将当前理论最高组合映射为 10 分。报告和企业
微信只使用正规化后的 0–10 综合分。

### Q6：语义去重为什么显示“跳过”？

语义去重需要当前批次或历史文章作为对比样本。当前主入口尚未加载历史样本，因此会
明确记录为跳过，而不是显示为已经执行。后续接入历史事实源后可启用实际对比。

### Q7：配置 Webhook 后推送失败怎么办？

- 检查 Webhook 地址是否包含真实的 `key`，而非占位符。
- 确认运行环境可以访问 `qyapi.weixin.qq.com`。
- 查看控制台输出的详细错误信息，系统不会再静默忽略推送错误。

### Q8：为什么 X Cookie 不能提交到 GitHub？

`auth_token` 和 `ct0` 可以代表已登录的 X 会话，泄露后可能被他人直接使用。项目只同步
采集代码、测试和配置模板；每台实际运行任务的电脑单独创建 `.local/x/twscrape.db`。

### Q9：twscrape 搜索失败怎么办？

- 先运行专用烟雾测试，不要直接启动完整推送流程。
- 确认复制的是 `auth_token` 和 `ct0` 的 Value，而不是 Cookie 名称或整行文本。
- Cookie 失效时，手动移走旧数据库，再重新运行会话创建脚本。
- 如果出现 X 验证、限流或非公开接口变化，应停止重试并重新评估依赖版本。
- 不要使用个人主账号进行高频测试。

### Q10：Reddit RSS 采集失败怎么办？

- 先用 `scripts/smoke_test_reddit_rss.py` 测试一个明确社区；
- 首次测试只配置一个社区；关键词只用于展示命中情况，不会影响帖子是否被采集；
- 遇到 429 时停止手动连试，按错误中的建议时间等待；
- RSS 不包含分数、点赞比例、评论数或 flair，不能通过调整解析器补出这些字段；
- 不要通过登录 Cookie、代理池或 IP 轮换规避访问限制。

## 更新日志

### v1.0.0（2026-07-22）

- 发布初始版本。
- 支持 MediaCrawler 与 lingzao-skill 集成。
- 实现动态评分、来源识别和多模式输出。
- 将评分卡调整至报告顶部。
- 支持企业微信 Markdown V2 推送，并增加配置校验。
- 增加 RadIter 决策日志。
- 修复短内容 `.txt` 报告链接返回 404 的问题。
- 修复来源识别中“当前来源”和“原始来源”的区分问题。
- 修复配置占位值校验问题。

## 许可证

本项目采用 [MIT License](LICENSE)。

本项目通过 Git 子模块依赖
[MediaCrawler](https://github.com/NanmiCoder/MediaCrawler)。
MediaCrawler 使用独立的
[Non-Commercial Learning License 1.1](https://github.com/NanmiCoder/MediaCrawler/blob/main/LICENSE)，
仅限非商业学习和研究用途，不受本项目 MIT License 覆盖。

使用者必须同时遵守 MediaCrawler 的许可证、免责声明、目标平台服务条款以及适用的法律法规。

## 致谢

- [MediaCrawler](https://github.com/NanmiCoder/MediaCrawler)：社交媒体爬虫。
- [twscrape](https://github.com/vladkens/twscrape)：X 非公开 GraphQL 接口的 Python 封装。
- [Reddit](https://www.reddit.com/)：Atom/RSS 内容来源。
- [OpenAI](https://openai.com/)：提供 LLM 能力支持。
