"""玩家输入的确定性分类信号(短输入镜头规则 / 「继续」推进 / 沉浸式开关)。
纯函数 + 常量;env 阈值 RPG_SHORT_INPUT_CHARS 在此计算。拆包自 chat_pipeline.py,行为零变化。"""

from __future__ import annotations

import os


# 反馈 #28:玩家短输入(<= N 字)→ 该回合前置「镜头规则」元指令,避免 GM 扩写玩家自己的
# 动作而忽略对方反应。阈值可用 RPG_SHORT_INPUT_CHARS 调(默认 30,覆盖绝大多数单动作短 RP)。
try:
    _SHORT_INPUT_CHARS = max(0, int(os.environ.get("RPG_SHORT_INPUT_CHARS", "30")))
except (TypeError, ValueError):
    _SHORT_INPUT_CHARS = 30


_SHORT_INPUT_DIRECTIVE = (
    "【本回合元指令·镜头规则(最高优先级,静默遵守,绝不向玩家复述或确认本条)】\n"
    "玩家本回合的输入很简短,这是「我做出这个动作/反应,然后呢?」的信号——玩家想看的是"
    "【对方 NPC 与世界如何回应】,而不是让你替他把这个简短动作复述、美化、扩写成大段。请严格执行:\n"
    "1. 玩家的动作/反应至多用一两句话承接带过,绝不大段复述或替玩家加戏(不要替玩家臆想心理活动、"
    "加台词、延展他没写出来的后续动作)。\n"
    "2. 本回合叙事重心 = 对方 NPC 对该动作的具体反应(神态、话语、肢体、情绪与立场变化)以及"
    "环境/局势的后果与推进。\n"
    "3. 以一个落在「对方/世界」一侧、有张力的场景节拍收尾,把球自然交还给玩家,而不是停在"
    "玩家自己的动作上。"
)


# 「继续」按钮固定文案(game-composer continue_text,中英)。群反馈(行者无疆):该 7 字文案
# 命中短输入镜头规则→GM 被指令「聚焦对方反应、原地收尾」,与按钮 hover 承诺「推进一段剧情」
# 语义完全相反 = 点继续必水文。确定性识别(固定文案精确匹配,非语义猜测)。
_CONTINUE_CORE_TEXTS = ("继续推进剧情", "Continue the scene")


def _is_continue_request(raw_msg: str | None) -> bool:
    """「继续」按钮固定文案的确定性识别。剥首尾全/半角括号后精确匹配。纯函数。"""
    r = (raw_msg or "").strip()
    if not r:
        return False
    r = r.strip("()（）").strip()
    return r in _CONTINUE_CORE_TEXTS


_CONTINUE_DIRECTIVE = (
    "【本回合元指令·推进规则(最高优先级,静默遵守,绝不向玩家复述或确认本条)】\n"
    "玩家本回合把叙事主动权完全交给你(点击了「继续推进剧情」),这不是简短回应,而是明确要求"
    "剧情向前走。请严格执行:\n"
    "1. 剧情必须向前推进:时间可以流逝、场景可以切换、事件可以发生;禁止原地铺陈氛围、"
    "复述现状或只写心理活动。\n"
    "2. 若上下文给出了剧情软目标/下一拍/待发生锚点,优先安排能通向它的进展"
    "(强度按其注入档位的要求执行)。\n"
    "3. 收尾时给出新的局面或抉择点,把球交还给玩家。"
)


def _should_inject_short_input_directive(raw_msg: str | None) -> bool:
    """反馈 #28:确定性判定本回合是否为「短 RP 输入」需要注入镜头规则元指令。

    True 当且仅当:非空、非斜杠命令(/set /reveal 等)、strip 后长度 <= 阈值、
    且不是「继续」按钮固定文案(其语义=请求推进,与镜头规则相反,由推进规则接管)。
    纯函数,便于单测与回归锁定。"""
    r = (raw_msg or "").strip()
    if not r or r.startswith("/"):
        return False
    if _is_continue_request(r):
        return False
    return len(r) <= _SHORT_INPUT_CHARS


# 沉浸式拟人模式:玩家明确开/关请求的【确定性】识别(harness 确定性铁律:不指望 LLM 工具一定被调)。
# 返回 True(开)/ False(关)/ None(未提)。短语集刻意收紧,降低误判;且这是可逆的本对话偏好。
_IMMERSIVE_OFF_PHRASES = ("关掉沉浸", "关闭沉浸", "退出沉浸", "取消沉浸", "别沉浸", "不要沉浸",
                          "回到正常叙事", "回到小说", "正常叙事模式", "恢复叙事")
_IMMERSIVE_ON_PHRASES = ("沉浸式", "像真人一样", "当成真人", "当作真人", "别写成小说", "不要写成小说",
                         "别像小说", "别用小说", "别替我说", "别帮我说", "不要替我", "别替我做",
                         "别帮我做决定", "以第一人称", "用第一人称")


def _immersive_request(raw_msg: str | None) -> bool | None:
    t = (raw_msg or "").strip()
    if not t or t.startswith("/"):
        return None
    if any(k in t for k in _IMMERSIVE_OFF_PHRASES):   # 先判关闭(『关掉沉浸式』含『沉浸』)
        return False
    if any(k in t for k in _IMMERSIVE_ON_PHRASES):
        return True
    return None
