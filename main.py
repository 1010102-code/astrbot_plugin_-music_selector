from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter

@register("music_selector", "YourName", "点歌插件", "1.0.0")
class MusicSelectorPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        logger.info("插件已加载，等待消息...")

    # 方法1：重写 on_message 钩子（最底层）
    async def on_message(self, event: AstrMessageEvent):
        logger.info(f"[on_message] 收到消息: {event.message_str}")
        await event.send("收到消息了！")

    # 方法2：保留事件监听器（如果版本支持）
    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_any_message(self, event: AstrMessageEvent):
        logger.info(f"[event_message_type] 收到消息: {event.message_str}")
        await event.send("收到消息了！")
