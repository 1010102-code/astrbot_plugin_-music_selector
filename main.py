import time
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger

@register("music_selector_test", "YourName", "测试点歌插件", "1.0.0")
class MusicSelectorPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        logger.info("测试插件已加载")

    @filter.command("点歌")
    async def search_music(self, event: AstrMessageEvent, name: str = None):
        logger.info(f"点歌指令被触发，原始消息: {event.message_str}")
        logger.info(f"参数 name: {name}")
        yield event.plain_result(f"你点了歌，参数是: {name}")

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def handle_all(self, event: AstrMessageEvent):
        logger.info(f"收到所有消息: {event.message_str}")
