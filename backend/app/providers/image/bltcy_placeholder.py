"""图像生成 Provider 占位。"""


class BltcyImage:
    def __init__(self, api_key: str = "") -> None:
        self.api_key = api_key

    async def generate(self, prompt: str, **kwargs) -> str:
        # 真实实现: 调用图生图/文生图接口,返回 URL
        return f"/static/generated/{abs(hash(prompt)) % 10_000_000}.png"
