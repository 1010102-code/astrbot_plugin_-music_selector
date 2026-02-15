from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

@register("music_selector_test", "YourName", "测试插件", "1.0.0")
class MusicSelectorPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        logger.info("测试插件已加载，等待消息...")

    async def on_message(self, event: AstrMessageEvent):
        """重写此方法来接收所有消息（如果AstrBot支持）"""
        logger.info(f"on_message 收到消息: {event.message_str}")
        # 可以在这里回复测试
        await event.send("收到消息了！")
