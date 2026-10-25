# analyzer/scorer.py
"""
动态评分模块
短内容（<500字）：四维（洞察深度、时效性、启发性、可追溯性）
长内容（>=500字）：五维（相关性、创新性、可复现性、内容质量、社区热度）
"""
import json
import time
from typing import Dict
from openai import OpenAI

import config
from utils.json_extract import extract_json_object

client = OpenAI(api_key=config.API_KEY, base_url=config.BASE_URL)


def score_and_classify(article: Dict, max_retries: int = None) -> Dict:
    if max_retries is None:
        max_retries = config.MAX_RETRIES

    title = article.get("title", "无标题")
    content = article.get("content", "")
    source = article.get("source", "")
    word_count = len(content)

    if word_count < config.SHORT_CONTENT_LIMIT:
        dimensions = ["洞察深度", "时效性", "启发性", "可追溯性"]
        weights = [0.30, 0.25, 0.25, 0.20]
        dim_prompt = """
        1. 洞察深度（0-2）：是否提出独特见解或反思
        2. 时效性（0-2）：内容是否紧跟当前技术热点
        3. 启发性（0-2）：能否引发读者的深入思考
        4. 可追溯性（0-2）：是否提供可验证的引用或线索
        """
        is_short = True
    else:
        dimensions = ["相关性", "创新性", "可复现性", "内容质量", "社区热度"]
        weights = [0.30, 0.25, 0.20, 0.15, 0.10]
        dim_prompt = """
        1. 相关性（0-2）：与 AI Agent 的相关程度
        2. 创新性（0-2）：是否有新观点或方法
        3. 可复现性（0-2）：是否有可操作的技术细节
        4. 内容质量（0-2）：信息密度、逻辑清晰度、干货程度
        5. 社区热度（0-2）：内容的传播力
        """
        is_short = False

    prompt = f"""你是一个专业的内容分析专家。请对以下文章进行评分。

标题：{title}
来源：{source}
字数：{word_count}

文章内容：
{content}

评分维度（每项 0-2 分）：
{dim_prompt}

分类（单选）：访谈 / 论文解读 / 新思想 / 行业分析 / 产品评测 / 教程 / 其他

请返回 JSON：
{{
  "scores": [分值列表],
  "reasons": ["理由1", "理由2", ...],
  "category": "分类",
  "summary": "一句话核心价值（20字内）"
}}"""

    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=config.MODEL_NAME,
                messages=[
                    {"role": "system", "content": "你是严格的分析专家，只返回合法 JSON。"},
                    {"role": "user", "content": prompt}
                ],
                temperature=config.SCORE_TEMPERATURE,
                max_tokens=int(getattr(config, "SCORE_MAX_TOKENS", 2000)),
            )

            raw = response.choices[0].message.content.strip()
            # 只解析第一个完整对象：模型偶尔会把多个 JSON 拼接或加说明文字，
            # 用贪婪正则抓取会触发 `Extra data`。
            result = extract_json_object(raw)
            scores = result.get("scores", [0] * len(dimensions))
            if len(scores) < len(dimensions):
                scores = scores + [0] * (len(dimensions) - len(scores))
            elif len(scores) > len(dimensions):
                scores = scores[:len(dimensions)]

            total = sum(s * w for s, w in zip(scores, weights))
            total_score = round(min(10.0, total * 5), 1)

            if article.get("_low_credibility", False):
                total_score = max(0, total_score - 0.5)

            return {
                "dimensions": dimensions,
                "scores": scores,
                "reasons": result.get("reasons", []),
                "category": result.get("category", "其他"),
                "summary": result.get("summary", "分析完成"),
                "total_score": total_score,
                "article": article,
                "word_count": word_count,
                "is_short": is_short
            }

        except json.JSONDecodeError as e:
            print(f"   ⚠️ JSON 解析失败 (尝试 {attempt+1}/{max_retries}): {e}")
        except Exception as e:
            print(f"   ⚠️ 评分异常 (尝试 {attempt+1}/{max_retries}): {e}")

        if attempt < max_retries - 1:
            time.sleep(config.REQUEST_INTERVAL * (attempt + 1))

    return {
        "article": article,
        "total_score": 0.0,
        "dimensions": dimensions if 'dimensions' in locals() else ["相关性", "创新性", "可复现性", "声誉", "社区热度"],
        "scores": [0] * len(dimensions) if 'dimensions' in locals() else [0, 0, 0, 0, 0],
        "category": "其他",
        "summary": "评分失败",
        "is_short": is_short if 'is_short' in locals() else False,
        "word_count": word_count
    }
