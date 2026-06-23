"""触碰热点的文案 + 音频 cue 映射(纯字符串路径,无 URL 拼接)。

V1 状态语义:
- lamp  : 想念/温暖   → cue: /audio/parent-msg-01.mp3
- cloud : 平静/安心   → (无音频)
- bear  : 想听故事   → cue: /audio/child-msg-01.mp3
- star  : 远方/秘密   → (无音频)
- book  : 等回应     → cue: /audio/parent-msg-01.mp3
"""

MESSAGE_BY_HOTSPOT: dict[str, str] = {
    "lamp": "灯亮着,有人在远方也想你。",
    "cloud": "云朵慢慢飘过,世界很安静。",
    "bear": "小熊也想听故事,你愿意讲一句吗?",
    "star": "星星眨了眨眼,像在告诉你一个小秘密。",
    "book": "翻到这一页,等一句回应吧。",
}

# 触碰后短提示音(cue)路径
CUE_BY_HOTSPOT: dict[str, str] = {
    "lamp": "/audio/parent-msg-01.mp3",
    "bear": "/audio/child-msg-01.mp3",
    "book": "/audio/parent-msg-01.mp3",
}
