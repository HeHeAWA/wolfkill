# -*- coding: utf-8 -*-
"""
狼人杀（纯文字）配置文件
- API Key 默认取自环境变量 WEREWOLF_API_KEY，未设置时使用下方硬编码的 Key
- 默认 1 名真人玩家 + 5 名 AI 玩家（共 6 人）
- 角色配比见 ROLES_CONFIG，键为角色名，值为人数，所有人数之和即为总人数
"""
import os

# ============ OpenRouter API ============
# OpenRouter 兼容 OpenAI 接口格式
API_KEY = os.getenv("WEREWOLF_API_KEY")  # 仅从环境变量读取，禁止硬编码密钥提交到仓库
BASE_URL = "https://openrouter.ai/api/v1/chat/completions"

# 默认模型（可在运行时用 --model 覆盖）。已实测可在本 Key 下【免额度】调用：
#   "tencent/hy3:free"              （腾讯混元，中文好，默认，免额度）
# 其他免费备选（偶尔限流 429，可换用）：
#   "meta-llama/llama-3.3-70b-instruct:free"
#   "qwen/qwen3-next-80b-a3b-instruct:free"
#   "nousresearch/hermes-3-llama-3.1-405b:free"
# 付费模型（需额度）：
#   "deepseek/deepseek-chat" / "openai/gpt-4o-mini"
MODEL = os.getenv("WEREWOLF_MODEL", "tencent/hy3:free")

# 免费模型限流较严：每次调用后短暂间隔，降低 429 概率
CALL_DELAY = float(os.getenv("WEREWOLF_CALL_DELAY", "0.5"))

# 请求可靠性
MAX_RETRIES = 3
RETRY_BACKOFF = 2          # 重试退避秒数
REQUEST_TIMEOUT = 60       # 单次请求超时秒数

# 离线测试开关：True 时 AI 用确定性逻辑代替真实 API 调用（不消耗额度）
USE_MOCK = os.getenv("WEREWOLF_MOCK", "0") in ("1", "true", "True")

# 全自动观战模式：True 时真人玩家也由 AI 接管，无需任何键盘输入
AUTO_HUMAN = os.getenv("WEREWOLF_AUTO", "0") in ("1", "true", "True")

# 机密信息是否显示到玩家屏幕：默认 False（狼队讨论/守护/用药等只写后台日志）。
# 仅 --debug 或 --auto(观战) 模式会置为 True，方便旁观 AI 内部决策。
REVEAL_SECRET = os.getenv("WEREWOLF_REVEAL", "0") in ("1", "true", "True")

# 真人狼人与 AI 狼队友自由讨论的最大轮数（输入空行或"结束"可提前结束）
WOLF_DISCUSS_ROUNDS = int(os.getenv("WEREWOLF_WOLF_DISCUSS_ROUNDS", "6"))

# ============ 游戏配置 ============
# 是否包含真人玩家
HUMAN_PLAYER = True

# 角色配比（人数之和 = 总人数）。默认 8 人：1 真人 + 7 AI
# 含：2 狼人 + 1 预言家 + 1 女巫 + 1 猎人 + 1 守卫 + 2 平民
# 想加人时，只需在这里增删角色/人数即可（AI 名字池会自动扩展）。
ROLES_CONFIG = {
    "狼人": 2,
    "预言家": 1,
    "女巫": 1,
    "猎人": 1,
    "守卫": 1,
    "平民": 2,
}

# 发言长度限制（字符数），防止 AI 长篇大论
SPEECH_MAX_CHARS = 120

# AI 名字池（按顺序分配给 AI 玩家，超过则自动生成 "玩家N"）
# 所有 AI 统一为「狼人杀高手」性格：逻辑缜密、极其擅长伪装与推理，
# 会严格依据场上的发言、投票、死亡等公开信息推断身份，非常会玩。
AI_EXPERT_PERSONA = (
    "你是一名精通狼人杀的高手：逻辑缜密、极其擅长伪装与心理博弈，"
    "会严格依据场上的发言、投票、死亡等公开信息去推断各方身份，"
    "发言自然且有说服力，非常会玩、极其难缠。"
)
AI_PROFILES = [
    ("阿杰", AI_EXPERT_PERSONA),
    ("小敏", AI_EXPERT_PERSONA),
    ("老李", AI_EXPERT_PERSONA),
    ("婷婷", AI_EXPERT_PERSONA),
    ("大壮", AI_EXPERT_PERSONA),
    ("慧姐", AI_EXPERT_PERSONA),
    ("小天", AI_EXPERT_PERSONA),
    ("阿强", AI_EXPERT_PERSONA),
    ("丽丽", AI_EXPERT_PERSONA),
    ("胖虎", AI_EXPERT_PERSONA),
    ("雪儿", AI_EXPERT_PERSONA),
    ("志明", AI_EXPERT_PERSONA),
]
