# config.py
# AI Content Miner 配置文件
# 敏感值从项目根目录的 .env 读取；可公开配置继续保留在本文件中。

import os

from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
# load_dotenv 默认不覆盖调用方已经导出的环境变量，便于部署环境注入配置。
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

# ============================================================
# 1. LLM API 配置
# ============================================================

# 优先使用项目既有的 LLM_* 名称，同时兼容负责人分支使用的 AI_* 名称。
API_KEY = (
    os.environ.get("LLM_API_KEY")
    or os.environ.get("AI_API_KEY", "")
).strip()
BASE_URL = (
    os.environ.get("LLM_BASE_URL")
    or os.environ.get("AI_BASE_URL")
    or "https://api.openai.com/v1"
).strip()
MODEL_NAME = (
    os.environ.get("LLM_MODEL_NAME")
    or os.environ.get("AI_MODEL_NAME", "")
).strip()

# ============================================================
# 2. 评分与过滤阈值
# ============================================================

# 四层筛选的原始乘积分数会映射到 0–10；各层均为中性评价时为 6 分。
SCORE_THRESHOLD = 6.0
SEMANTIC_DEDUP_THRESHOLD = 0.85
COMMENT_PASS_THRESHOLD = 0.5
AUTHOR_PROFILE_THRESHOLD = 0.9
SHORT_CONTENT_LIMIT = 500
MEDIUM_CONTENT_LIMIT = 1500
MIN_CONTENT_LENGTH = 100

LOW_QUALITY_KEYWORDS = [
    "求助", "求推荐", "有人知道吗", "想问一下",
    "萌新", "求问", "有没有人", "在线等", "急求"
]

# ============================================================
# 3. 博主白名单
# ============================================================

BLOGGER_WHITELIST = {
    "大神A": {"source": "小红书", "weight": 1.3},
    "量化小咖": {"source": "知乎", "weight": 1.2},
    "AI研究员B": {"source": "知乎", "weight": 1.4},
}

# ============================================================
# 4. 内容采集配置
# ============================================================

# 支持平台：xhs（小红书）/ zhihu / x（X）/ github（公开仓库）/ reddit
# xhs、zhihu 使用 MediaCrawler；其余平台使用各自独立的采集器。
CRAWL_PLATFORM = os.environ.get(
    "CRAWL_PLATFORM", "github"
).strip().casefold()

# Twitter 使用带技术意图的组合查询。
SEARCH_KEYWORDS = [
    '"AI Agent" (framework OR benchmark OR "tool calling" OR MCP OR GitHub)',
    '"LLM" (training OR inference OR benchmark OR architecture OR quantization)',
    '"reinforcement learning" (paper OR benchmark OR implementation OR code)',
    '("大模型" OR LLM) (训练 OR 推理 OR 架构 OR 评测 OR 量化 OR 微调 OR 开源)',
    '("AI Agent" OR 智能体) (框架 OR 工具调用 OR MCP OR 开源 OR 实现)',
]

# 小红书、知乎使用普通主题词进行搜索和正文关键词前置筛选。
# 基础主题词。每个主题会与下方三类意图词组合。
CONTENT_SEARCH_KEYWORDS = [
    "AI Agent",
    "智能体",
    "大模型",
    "LLM",
    "强化学习",
    "量化投资",
    "推理模型",
    "多模态",
    "AI Infra",
]

# 面向已有基础知识用户的技术检索意图。
CONTENT_SEARCH_INTENTS = {
    "technical_research": [
        "架构", "评测", "benchmark", "framework", "机制", "方法", "对比", "实验",
    ],
    "engineering_practice": [
        "工程实践", "生产环境", "部署", "性能", "延迟", "成本", "可观测性", "implementation",
    ],
    "failure_review": [
        "失败", "局限", "问题", "踩坑", "复盘", "可靠性", "安全", "failure", "tradeoff",
    ],
}
# 防止主题与意图组合过多；0 表示使用全部组合。
CONTENT_SEARCH_MAX_QUERIES = 60
CRAWL_LIMIT = 100
# MediaCrawler 每个关键词最多采集一页，降低平台风控概率。
MEDIACRAWLER_LIMIT = 20
# 小红书、知乎进入评分前只保留最近三天且命中关键词的内容。
CONTENT_LOOKBACK_DAYS = 3
# 0 表示不限制；默认限制 100 篇，防止一次运行触发过多 Embedding/LLM 请求。
CONTENT_KEYWORD_MAX_ITEMS = 100
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
ARTICLES_DIR = os.path.join(PROJECT_ROOT, "articles")

# ============================================================
# 4.0 通用 Embedding 配置
# ============================================================

# Embedding 可使用 OpenAI 兼容接口或阿里云 DashScope 原生 API。
EMBEDDING_PROVIDER = os.environ.get(
    "EMBEDDING_PROVIDER", "openai"
).strip().casefold()
DASHSCOPE_API_KEY = os.environ.get("DASHSCOPE_API_KEY", "").strip()
_GENERIC_EMBEDDING_API_KEY = os.environ.get(
    "EMBEDDING_API_KEY", ""
).strip()
# 原生模式优先读取专用变量；旧环境仍可回退到 EMBEDDING_API_KEY。
EMBEDDING_API_KEY = (
    (DASHSCOPE_API_KEY or _GENERIC_EMBEDDING_API_KEY)
    if EMBEDDING_PROVIDER == "dashscope"
    else _GENERIC_EMBEDDING_API_KEY
)
EMBEDDING_BASE_URL = os.environ.get(
    "EMBEDDING_BASE_URL", "https://api.openai.com/v1"
).strip()
EMBEDDING_MODEL = os.environ.get(
    "EMBEDDING_MODEL",
    (
        "text-embedding-v3"
        if EMBEDDING_PROVIDER == "dashscope"
        else "text-embedding-3-small"
    ),
).strip()
# text-embedding-v4 的同步接口单次最多接收 10 条文本。
EMBEDDING_BATCH_SIZE = 10
EMBEDDING_TIMEOUT_SECONDS = 60
EMBEDDING_MAX_RETRIES = 2

# ============================================================
# 4.1 GitHub 仓库搜索
# ============================================================

# 令牌只从环境变量读取，禁止写入仓库配置或提交到 Git。
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_API_BASE_URL = "https://api.github.com"
# GitHub 使用简单关键词；不要复用 Twitter 的 AND/OR 查询表达式。
GITHUB_SEARCH_KEYWORDS = [
    "AI Agent",
    "智能体",
    "大模型",
    "LLM",
    "强化学习",
    "推理模型",
    "多模态",
    "AI Infra",
]
GITHUB_RESULTS_PER_QUERY = 20
GITHUB_LOOKBACK_DAYS = 7
GITHUB_MIN_STARS = 0
GITHUB_TIMEOUT_SECONDS = 30
GITHUB_REQUEST_MAX_RETRIES = int(
    os.environ.get("GITHUB_REQUEST_MAX_RETRIES", "3")
)
GITHUB_README_MAX_CHARS = 6000
GITHUB_CODE_EVIDENCE_MAX_CHARS = 9000
GITHUB_CODE_EVIDENCE_MAX_FILES = 3
GITHUB_STATE_FILE = os.path.join(DATA_DIR, "state", "github_seen_ids.json")
GITHUB_SEEN_ID_LIMIT = 5000

# GitHub 专用筛选：仓库规则 -> 搜索关键词 Embedding -> 质量评分。
GITHUB_RULE_FILTER = {
    "min_content_chars": 40,
    "allow_forks": False,
    "allow_archived": False,
    "allow_disabled": False,
    "exclude_keywords": [],
}
# Embedding 服务可与聊天服务分离；未单独设置时才回退到通用 LLM 配置。
GITHUB_EMBEDDING_PROVIDER = os.environ.get(
    "GITHUB_EMBEDDING_PROVIDER", EMBEDDING_PROVIDER
).strip().lower()
GITHUB_EMBEDDING_API_KEY = os.environ.get(
    "GITHUB_EMBEDDING_API_KEY", EMBEDDING_API_KEY
)
GITHUB_EMBEDDING_BASE_URL = os.environ.get(
    "GITHUB_EMBEDDING_BASE_URL", EMBEDDING_BASE_URL
)
GITHUB_EMBEDDING_MODEL = os.environ.get(
    "GITHUB_EMBEDDING_MODEL", EMBEDDING_MODEL
)
GITHUB_EMBEDDING_BATCH_SIZE = 10
GITHUB_EMBEDDING_MAX_CHARS = 6000
GITHUB_EMBEDDING_TIMEOUT_SECONDS = 60
GITHUB_EMBEDDING_MAX_RETRIES = 2
GITHUB_EMBEDDING_FILTER_MODE = "enforce"  # shadow | enforce
GITHUB_EMBEDDING_THRESHOLD = 0.35
GITHUB_QUALITY_MIN_SCORE = 5.0
# ============================================================
# 4.2 MediaCrawler（小红书、知乎）
# ============================================================

MEDIACRAWLER_PATH = os.path.join(PROJECT_ROOT, "MediaCrawler")

# 本项目桥接器已按该 commit 的 CLI 与 JSONL 输出格式完成对齐。
MEDIACRAWLER_COMMIT = "c9a111be73586bdf6fc44536f088e4db6ed86d64"

# 默认使用 MediaCrawler 官方推荐的 uv。仅在未安装 uv 时填写一个
# Python >= 3.11 且已安装 MediaCrawler 依赖的解释器路径。
MEDIACRAWLER_PYTHON = ""
MEDIACRAWLER_LOGIN_TYPE = "qrcode"
# 小红书单次搜索（60 个 query × 每页 20 条）经常超过 15 分钟，
# 900 秒会在正常爬取中被判超时，因此默认放宽到 25 分钟。
MEDIACRAWLER_TIMEOUT_SECONDS = int(
    os.environ.get("MEDIACRAWLER_TIMEOUT_SECONDS", "1500")
)

# 爬取类型：search / detail / creator
CRAWL_TYPE = "search"

# ============================================================
# 4.3 twscrape（X，实验接口）
# ============================================================

# 本项目直接对齐 PyPI twscrape 0.20.1 的异步 API。
TWSCRAPE_EXPECTED_VERSION = "0.20.1"
TWSCRAPE_SEARCH_PRODUCT = "Top"
TWSCRAPE_RESULTS_PER_QUERY = 50
TWSCRAPE_TIMEOUT_SECONDS = 120
TWSCRAPE_ACCOUNT_WAIT_SECONDS = 10
TWSCRAPE_LOOKBACK_HOURS = 168

# 会话数据库包含浏览器 Cookie，只能保存在本机，不得提交 Git。
TWSCRAPE_DB_FILE = os.environ.get(
    "TWSCRAPE_DB_FILE",
    os.path.join(PROJECT_ROOT, ".local", "x", "twscrape.db"),
)
TWSCRAPE_STATE_FILE = os.path.join(DATA_DIR, "state", "twscrape_seen_ids.json")
TWSCRAPE_SEEN_ID_LIMIT = 5000

# ============================================================
# 4.3 Reddit RSS（本地低频采集）
# ============================================================

# RSS 不提供帖子分数、点赞比例、评论数或 flair。采集器读取明确社区的
# new/.rss，在采集阶段只执行时间窗口、格式校验和帖子 ID 去重。
# 关键词命中只作为审计字段记录，不用于前置淘汰。
REDDIT_RSS_SUBREDDITS = ["LocalLLaMA"]
REDDIT_RSS_KEYWORDS = [
    "LLM",
    "model",
    "agent",
    "inference",
    "quantization",
    "大模型",
    "推理",
    "量化",
]
# Reddit RSS 的 limit 参数最高请求 100；服务端实际返回数量可能更少。
REDDIT_RSS_RESULTS_PER_SUBREDDIT = 100
# 0 表示不在进入专用 pipeline 前设置跨社区总量上限。
REDDIT_RSS_MAX_CANDIDATES = 0
REDDIT_RSS_LOOKBACK_HOURS = 168
REDDIT_RSS_REQUEST_TIMEOUT_SECONDS = 30

# 2026-07-24 的真实响应显示当前出口约 30 秒恢复一次 RSS 请求额度。
# 多社区之间默认等待 31 秒；建议先只配置一个社区。
REDDIT_RSS_REQUEST_INTERVAL_SECONDS = 31
REDDIT_RSS_MAX_RESPONSE_BYTES = 5_000_000
REDDIT_RSS_BASE_URL = "https://www.reddit.com"
REDDIT_RSS_USER_AGENT = os.environ.get(
    "REDDIT_RSS_USER_AGENT",
    (
        "python:ai-content-miner:v0.1 "
        "(contact: https://github.com/kassidylee/ai-content-miner)"
    ),
)
REDDIT_RSS_STATE_FILE = os.path.join(
    DATA_DIR, "state", "reddit_rss_seen_ids.json"
)
REDDIT_RSS_SEEN_ID_LIMIT = 5000

# Reddit 专用筛选：内容与来源规则 -> 主题 Embedding -> 内容质量评分。
# RSS 缺少互动字段，因此质量分不读取 score、评论数或点赞比例。
REDDIT_RULE_FILTER = {
    "min_content_chars": 40,
    "exclude_keywords": [],
}
REDDIT_EMBEDDING_API_KEY = os.environ.get(
    "REDDIT_EMBEDDING_API_KEY", EMBEDDING_API_KEY
)
REDDIT_EMBEDDING_BASE_URL = os.environ.get(
    "REDDIT_EMBEDDING_BASE_URL", EMBEDDING_BASE_URL
)
REDDIT_EMBEDDING_MODEL = os.environ.get(
    "REDDIT_EMBEDDING_MODEL", EMBEDDING_MODEL
)
# DashScope text-embedding-v4 的同步接口单批最多接收 10 条文本。
# Reddit 会一次处理完整 RSS 候选集，因此必须在这里分批，而不是减少采集量。
REDDIT_EMBEDDING_BATCH_SIZE = min(EMBEDDING_BATCH_SIZE, 10)
REDDIT_EMBEDDING_MAX_CHARS = 6000
REDDIT_EMBEDDING_FILTER_MODE = "enforce"  # shadow | enforce
REDDIT_INTEREST_TOPICS = [
    {
        "id": "ai-agent",
        "label": "AI Agent",
        "description": (
            "AI Agent、智能体框架、工具调用、任务规划、"
            "多智能体协作、Agent 工作流和相关开源项目"
        ),
        "threshold": 0.35,
    },
    {
        "id": "reasoning-model",
        "label": "推理模型",
        "description": (
            "大语言模型的复杂推理、思维链、test-time compute、"
            "数学推理、代码推理和推理模型训练"
        ),
        "threshold": 0.35,
    },
    {
        "id": "model-systems",
        "label": "模型系统",
        "description": (
            "大模型训练、推理服务、模型部署、量化、微调、"
            "GPU 优化、分布式系统和 AI 基础设施"
        ),
        "threshold": 0.35,
    },
    {
        "id": "open-models",
        "label": "开放模型",
        "description": (
            "开放权重大语言模型、模型发布、基准测试、"
            "模型能力对比、复现实验和开源实现"
        ),
        "threshold": 0.35,
    },
]
REDDIT_QUALITY_WEIGHTS = {
    "relevance": 0.35,
    "depth": 0.25,
    "evidence": 0.20,
    "freshness": 0.10,
    "source_quality": 0.10,
}
REDDIT_QUALITY_MIN_SCORE = 6.0
# 三层筛选后按 Reddit 质量分降序保留的最终候选数量。
REDDIT_FINAL_RESULT_LIMIT = 20

# Reddit 专用社交信息流输出。通过筛选的帖子只生成极简标题/摘要，
# 不进入逐条长研报生成器；全部筛选审计写入结构化 JSONL。
REDDIT_TITLE_MAX_CHARS = 80
REDDIT_ABSTRACT_MAX_CHARS = 180
REDDIT_ENRICHMENT_INPUT_MAX_CHARS = 3000
REDDIT_ENRICHMENT_TEMPERATURE = 0.1
REDDIT_ENRICHMENT_MAX_TOKENS = 300
REDDIT_ENRICHMENT_TIMEOUT_SECONDS = 30
REDDIT_ENRICHMENT_MAX_RETRIES = 1
REDDIT_PROCESSED_FILE = os.path.join(
    DATA_DIR, "processed", "reddit.jsonl"
)
REDDIT_REPORT_FILE = os.path.join(PROJECT_ROOT, "reports", "reddit.html")
REDDIT_FEED_RETENTION_DAYS = 30
REDDIT_FEED_MAX_ITEMS = 200
REDDIT_WECOM_MAX_ITEMS = 5
REDDIT_WECOM_MAX_BYTES = 4096
REDDIT_ENABLE_WECOM = os.environ.get(
    "REDDIT_ENABLE_WECOM", "true"
).strip().casefold() in {"1", "true", "yes", "on"}

# ============================================================
# 4.4 Twitter 专用结构化处理
# ============================================================

# 这些配置只由 Twitter 新流程读取，不影响小红书和知乎的旧筛选链路。
TWITTER_RULE_FILTER = {
    "allowed_languages": ["zh", "en"],
    "allow_replies": False,
    "allow_retweets": False,
    "allow_quotes": True,
    "drop_sensitive": True,
    "min_meaningful_chars": 40,
    # 浏览量只代表曝光，不代表认可，因此不再允许帖子仅凭浏览量通过。
    # 加权互动门槛负责排除完全无人响应的内容；一手技术链接可作为冷启动例外。
    "min_weighted_engagement": 2.0,
    # 不信任 X 搜索结果的宽松匹配，正文必须再次命中至少一个主题词。
    "required_topic_keywords": [
        "AI Agent",
        "AI Agents",
        "Agentic AI",
        "智能体",
        "LLM",
        "Large Language Model",
        "Large Language Models",
        "大模型",
        "Reinforcement Learning",
        "强化学习",
        "Quantitative Trading",
        "Quant Trading",
        "Algorithmic Trading",
        "量化投资",
    ],
    "technical_keywords": [
        "architecture",
        "framework",
        "training",
        "inference",
        "fine-tuning",
        "finetuning",
        "quantization",
        "evaluation",
        "benchmark",
        "paper",
        "experiment",
        "code",
        "open source",
        "implementation",
        "tool calling",
        "dataset",
        "distillation",
        "deployment",
        "repository",
        "repo",
        "GitHub",
        "arXiv",
        "HuggingFace",
        "API",
        "MCP",
        "RAG",
        "架构",
        "框架",
        "训练",
        "推理",
        "微调",
        "量化",
        "评测",
        "基准",
        "论文",
        "实验",
        "代码",
        "开源",
        "实现",
        "工具调用",
        "数据集",
        "蒸馏",
        "部署",
    ],
    "technical_depth_keywords": [
        "architecture",
        "framework",
        "inference",
        "fine-tuning",
        "finetuning",
        "quantization",
        "evaluation",
        "benchmark",
        "paper",
        "experiment",
        "code",
        "implementation",
        "tool calling",
        "dataset",
        "distillation",
        "deployment",
        "MCP",
        "RAG",
        "架构",
        "框架",
        "推理",
        "微调",
        "量化",
        "评测",
        "基准",
        "论文",
        "实验",
        "代码",
        "实现",
        "工具调用",
        "数据集",
        "蒸馏",
        "部署",
    ],
    "business_penalty_keywords": [
        "acquisition",
        "acquire",
        "funding",
        "valuation",
        "revenue",
        "stock",
        "IPO",
        "commercialization",
        "market cap",
        "salary",
        "hiring",
        "startup",
        "industry",
        "geopolitical",
        "capital expenditure",
        "capex",
        "profit",
        "personnel",
        "appointed",
        "promotion",
        "收购",
        "融资",
        "估值",
        "财报",
        "股价",
        "股票",
        "上市",
        "商业化",
        "营收",
        "市值",
        "中美",
        "争霸",
        "军备竞赛",
        "薪资",
        "初创企业",
        "产业",
        "创业",
        "行业格局",
        "资本",
        "二级市场",
        "资本开支",
        "盈利",
        "利润",
        "升任",
        "任命",
        "人事",
        "部门合并",
        "内部通知",
        "掌舵",
    ],
    # 这些词通常直接表达课程售卖、订阅或获客意图。命中后立即淘汰，
    # 不允许广告依靠高相关度、发布时间或主题配额重新进入每日精选。
    "promotion_hard_drop_keywords": [
        "course",
        "webinar",
        "bootcamp",
        "masterclass",
        "enroll",
        "newsletter",
        "subscribe",
        "gumroad",
        "课程",
        "开源课",
        "公开课",
        "训练营",
        "报名",
        "订阅",
        "亲授",
        "付费社群",
        "知识星球",
        "免费领取",
    ],
    # 软推广信号也可能出现在正常技术分享中，因此只扣分，不直接淘汰。
    "promotion_penalty_keywords": [
        "conference",
        "event",
        "hackathon",
        "top 10",
        "list of",
        "recommended",
        "follow",
        "直播",
        "论坛",
        "大会",
        "峰会",
        "招聘",
        "offer",
        "推荐",
        "关注",
        "清单",
        "合集",
    ],
    "evidence_domains": [
        "github.com",
        "arxiv.org",
        "huggingface.co",
        "paperswithcode.com",
    ],
    "min_technical_score": 3,
    "exclude_keywords": [
        "airdrop",
        "giveaway",
        "casino",
        "betting",
        "sportsbook",
        "match winner",
        "handicap",
        "招聘",
        "返利",
        "空投",
        "博彩",
        "下注",
    ],
}

# 互动指标使用统一权重，规则门槛和每日排序共用同一套定义。
# 转发最能体现信息传播，引用和收藏通常比单次点赞包含更强的认可或复用意图。
TWITTER_ENGAGEMENT_WEIGHTS = {
    "like_count": 1.0,
    "reply_count": 1.5,
    "share_count": 3.0,
    "quote_count": 2.5,
    "bookmark_count": 2.0,
}

# 默认关闭，部署时通过 .env 显式启用，避免缺少凭证时阻塞 Twitter 主流程。
TWITTER_EMBEDDING_ENABLED = os.environ.get(
    "TWITTER_EMBEDDING_ENABLED", "false"
).strip().casefold() in {"1", "true", "yes", "on"}
TWITTER_EMBEDDING_MODEL = os.environ.get(
    "TWITTER_EMBEDDING_MODEL",
    EMBEDDING_MODEL,
).strip()
TWITTER_EMBEDDING_BATCH_SIZE = 10
TWITTER_EMBEDDING_MAX_CHARS = 3500

# enforce 会实际删除低相似度内容；shadow 仅记录判定，不改变保留结果。
TWITTER_EMBEDDING_FILTER_MODE = os.environ.get(
    "TWITTER_EMBEDDING_FILTER_MODE", "shadow"
).strip()

TWITTER_INTEREST_TOPICS = [
    {
        "id": "ai-agent",
        "label": "AI Agent",
        "description": (
            "AI Agent、智能体框架、工具调用、任务规划、"
            "多智能体协作、Agent 工作流和相关开源项目"
        ),
        "threshold": 0.35,
        "tag_id": "ai-agent",
    },
    {
        "id": "reasoning-model",
        "label": "推理模型",
        "description": (
            "大语言模型的复杂推理、思维链、test-time compute、"
            "数学推理、代码推理和推理模型训练"
        ),
        "threshold": 0.35,
        "tag_id": "reasoning-model",
    },
    {
        "id": "multimodal",
        "label": "多模态",
        "description": (
            "视觉语言模型、语音模型、图像生成、视频生成、"
            "多模态理解和跨模态推理"
        ),
        "threshold": 0.35,
        "tag_id": "multimodal",
    },
    {
        "id": "ai-infra",
        "label": "AI Infra",
        "description": (
            "大模型训练和推理基础设施、GPU、分布式训练、"
            "推理加速、模型部署、量化和服务框架"
        ),
        "threshold": 0.35,
        "tag_id": "ai-infra",
    },
]

TWITTER_COMMENT_FILTER = {
    "enabled": True,
    "max_replies": 20,
    "timeout_seconds": 30,
    "min_sample_size": 5,
    "min_critical_authors": 3,
    "critical_ratio_threshold": 0.4,
    "weighted_ratio_threshold": 0.6,
    "strong_critical_authors": 2,
    "strong_critical_min_likes": 5,
    "strong_weighted_ratio_threshold": 0.5,
    "critical_keywords": [
        "misleading",
        "incorrect",
        "fabricated",
        "fake",
        "clickbait",
        "no evidence",
        "not reproducible",
        "瞎说",
        "胡扯",
        "错误",
        "造假",
        "误导",
        "不可信",
        "标题党",
    ],
    "ignored_username_suffixes": ["bot"],
}

# 每日选择只限制发布数量，不截断进入内容筛选的候选。
TWITTER_DAILY_PRIMARY_LIMIT = 8
TWITTER_DAILY_MORE_LIMIT = 4
TWITTER_DAILY_AUTHOR_LIMIT = 1
# 主题不设硬配额，只在重排时小幅扣分。这样互动质量仍决定主要顺序，
# 同时避免十二条内容全部集中在一个主题。
TWITTER_DAILY_TOPIC_REPEAT_PENALTY = 3.0
TWITTER_DAILY_HISTORY_DAYS = 7
TWITTER_DAILY_EVENT_TOKEN_OVERLAP = 0.4
TWITTER_DAILY_TIMEZONE = "Asia/Hong_Kong"

# 48 小时以内的内容正常竞争。48 至 168 小时的内容只有在互动进入
# 高分段且带有 GitHub、arXiv 等一手证据时才作为高质量例外保留。
TWITTER_DAILY_STANDARD_MAX_AGE_HOURS = 48
TWITTER_DAILY_FALLBACK_MAX_AGE_HOURS = 168
TWITTER_DAILY_FALLBACK_MIN_SOCIAL_SCORE = 0.85
# 规则层允许带一手链接的冷启动内容继续评估，但最终发布仍需达到
# 绝对互动底线，避免候选不足时用零互动内容填满 8+4。
TWITTER_DAILY_MIN_WEIGHTED_ENGAGEMENT = 5.0

TWITTER_TITLE_MAX_CHARS = 48
TWITTER_ABSTRACT_MAX_CHARS = 180
TWITTER_ENRICHMENT_INPUT_MAX_CHARS = 6000
TWITTER_LONG_MESSAGE_CHARS = 500
TWITTER_ENTITY_LIMIT = 5
TWITTER_ENRICHMENT_TEMPERATURE = 0.1
TWITTER_ENRICHMENT_MAX_TOKENS = 600

TWITTER_TAG_TAXONOMY = [
    {"id": "ai", "label": "AI", "level": 1, "parent_id": None},
    {
        "id": "ai-agent",
        "label": "AI Agent",
        "level": 2,
        "parent_id": "ai",
    },
    {
        "id": "reasoning-model",
        "label": "推理模型",
        "level": 2,
        "parent_id": "ai",
    },
    {
        "id": "multimodal",
        "label": "多模态",
        "level": 2,
        "parent_id": "ai",
    },
    {
        "id": "ai-infra",
        "label": "AI Infra",
        "level": 2,
        "parent_id": "ai",
    },
    {
        "id": "content-form",
        "label": "内容形式",
        "level": 1,
        "parent_id": None,
    },
    {
        "id": "short-message",
        "label": "短消息",
        "level": 2,
        "parent_id": "content-form",
    },
    {
        "id": "long-message",
        "label": "长消息",
        "level": 2,
        "parent_id": "content-form",
    },
    {
        "id": "paper",
        "label": "论文",
        "level": 2,
        "parent_id": "content-form",
    },
    {
        "id": "project-release",
        "label": "项目发布",
        "level": 2,
        "parent_id": "content-form",
    },
]

TWITTER_PROCESSED_FILE = os.path.join(DATA_DIR, "processed", "x.jsonl")
TWITTER_REPORT_FILE = os.path.join(PROJECT_ROOT, "reports", "x.html")
TWITTER_FEED_RETENTION_DAYS = 30
TWITTER_FEED_MAX_ITEMS = 200
TWITTER_FEED_DEBUG_METADATA = False

# Twitter 通知独立开关；非 Twitter 流程继续使用下方企业微信配置。
TWITTER_ENABLE_WECOM = False

# ============================================================
# 4.5 YouTube / YTB_LLM structured feed
# ============================================================

# YouTube is produced by the sibling YTB_LLM project. ai-content-miner treats
# that project as a black-box producer and maps its digest JSON into the
# structured social feed schema used by Reddit/Twitter.
YOUTUBE_YTB_LLM_PATH = os.environ.get(
    "YOUTUBE_YTB_LLM_PATH",
    os.path.abspath(os.path.join(PROJECT_ROOT, "..", "YTB_LLM")),
).strip()
YOUTUBE_PYTHON = os.environ.get(
    "YOUTUBE_PYTHON",
    "/opt/anaconda3/bin/python3",
).strip()
YOUTUBE_PROCESSED_FILE = os.path.join(DATA_DIR, "processed", "youtube.jsonl")
YOUTUBE_REPORT_FILE = os.path.join(PROJECT_ROOT, "reports", "youtube.html")
YOUTUBE_FEED_RETENTION_DAYS = 30
YOUTUBE_FEED_MAX_ITEMS = 200

# ============================================================
# 5. 企业微信推送配置
# ============================================================

# 企业微信机器人 Webhook；WECOM_WEBHOOK_URL 为兼容别名。
WECOM_WEBHOOK = os.environ.get(
    "WECOM_WEBHOOK",
    os.environ.get("WECOM_WEBHOOK_URL", ""),
)
REPORT_BASE_URL = ""

# 腾讯云 COS 私有桶报告分发。凭据只从 .env/环境变量读取。
COS_SECRET_ID = os.environ.get("COS_SECRET_ID", "").strip()
COS_SECRET_KEY = os.environ.get("COS_SECRET_KEY", "").strip()
COS_REGION = os.environ.get("COS_REGION", "ap-hongkong").strip()
COS_BUCKET = os.environ.get("COS_BUCKET", "").strip()
COS_PREFIX = os.environ.get("COS_PREFIX", "reports").strip()
COS_PRESIGNED_EXPIRES = int(os.environ.get("COS_PRESIGNED_EXPIRES", "2592000"))

# ============================================================
# 6. RAL 来源识别配置
# ============================================================

ENABLE_RETRIEVAL = True

# 以下为预留配置，暂未实现
# FETCH_ORIGINAL_CONTENT = True
# CACHE_EXPIRE_SECONDS = 86400

# ============================================================
# 7. 日志与输出配置
# ============================================================

LOG_DIR = os.path.join(PROJECT_ROOT, "logs")
RADITER_LOG_FILE = os.path.join(LOG_DIR, "raditer.log")
REPORT_DIR = os.path.join(PROJECT_ROOT, "reports")
LOG_LEVEL = "INFO"

# ============================================================
# 8. 高级配置
# ============================================================

SCORE_TEMPERATURE = .0
# 评分请求的输出上限。推理模型的思维链也计入 completion_tokens，
# 800 会在 JSON 写到一半时被截断（finish_reason=length），因此留足余量。
SCORE_MAX_TOKENS = int(os.environ.get("SCORE_MAX_TOKENS", "3000"))
REPORT_TEMPERATURE = 0.25
# Cloudflare 代理会在 120 秒后中止响应，研报请求需明显低于该上限。
REPORT_MAX_ITEMS = int(os.environ.get("REPORT_MAX_ITEMS", "10"))
REPORT_INPUT_MAX_CHARS = int(os.environ.get("REPORT_INPUT_MAX_CHARS", "9000"))
REPORT_CODE_EVIDENCE_MAX_CHARS = int(
    os.environ.get("REPORT_CODE_EVIDENCE_MAX_CHARS", "7000")
)
REPORT_MAX_TOKENS = int(os.environ.get("REPORT_MAX_TOKENS", "4200"))
REPORT_TIMEOUT_SECONDS = float(os.environ.get("REPORT_TIMEOUT_SECONDS", "90"))
REPORT_MAX_RETRIES = int(os.environ.get("REPORT_MAX_RETRIES", "3"))
REPORT_RETRY_BACKOFF_SECONDS = int(
    os.environ.get("REPORT_RETRY_BACKOFF_SECONDS", "10")
)
MAX_RETRIES = 3
REQUEST_INTERVAL = 2
ENABLE_LINGZAO_ANALYSIS = True

# ============================================================
# 8.5 多平台统一运行
# ============================================================

# 一次运行覆盖的平台。youtube 默认不启用，因为它依赖外部 YTB_LLM 项目。
MULTI_ENABLED_PLATFORMS = ["xhs", "zhihu", "github", "x", "reddit"]
MULTI_INCLUDE_YOUTUBE = os.environ.get(
    "MULTI_INCLUDE_YOUTUBE", "false"
).strip().casefold() in {"1", "true", "yes", "on"}

# 各平台进入 LLM 复评的候选上限（top-k），不是最终推送配额：
# 最终推送哪些内容由复评分决定，因此各平台条数不必相等。
MULTI_PLATFORM_QUOTA = {
    "xhs": 5,
    "zhihu": 5,
    "github": 5,
    "x": 5,
    "reddit": 5,
    "youtube": 2,
}
MULTI_TOTAL_ITEMS = int(os.environ.get("MULTI_TOTAL_ITEMS", "12"))
# 只为日报前 N 条生成逐条研报，控制 LLM 与 COS 成本。
MULTI_REPORT_TOP_N = int(os.environ.get("MULTI_REPORT_TOP_N", "5"))

# 用现有 analyzer.scorer 的统一口径对跨平台候选复评，再决定最终日报。
MULTI_LLM_RESCORE_ENABLED = os.environ.get(
    "MULTI_LLM_RESCORE_ENABLED", "true"
).strip().casefold() in {"1", "true", "yes", "on"}
MULTI_LLM_CONCURRENCY = int(os.environ.get("MULTI_LLM_CONCURRENCY", "4"))

MULTI_WECOM_MAX_BYTES = int(os.environ.get("MULTI_WECOM_MAX_BYTES", "4096"))
MULTI_DIGEST_DIR = os.path.join(DATA_DIR, "digests")

# ============================================================
# 9. 路径自动创建
# ============================================================

def init_directories():
    dirs = [DATA_DIR, REPORT_DIR, LOG_DIR, ARTICLES_DIR, MULTI_DIGEST_DIR]
    for d in dirs:
        os.makedirs(d, exist_ok=True)

init_directories()

# ============================================================
# 10. 非 Twitter 路线的 Embedding 主题匹配配置
# ============================================================

# 该模型只供小红书、知乎等旧四层筛选使用，Twitter 使用上方独立开关和模型。
# 模型、Key 和 Base URL 已在文件顶部统一从 .env 读取。
EMBEDDING_MAX_CHARS = 6000
# shadow 只记录低相似度结果；enforce 会直接淘汰低于阈值的内容。
EMBEDDING_FILTER_MODE = "shadow"

INTEREST_TOPICS = [
    {
        "id": "ai-agent",
        "label": "AI Agent",
        "description": "AI Agent、智能体框架、工具调用、任务规划、多智能体协作",
        "threshold": 0.35,
        "tag_id": "ai-agent",
    },
    {
        "id": "reasoning-model",
        "label": "推理模型",
        "description": "大语言模型的复杂推理、思维链、数学推理、代码推理",
        "threshold": 0.35,
        "tag_id": "reasoning-model",
    },
    # 根据小红书/知乎的内容特点补充...
]
