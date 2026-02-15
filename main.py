import time
import requests
import tempfile
import os
import re
from io import BytesIO
from typing import Dict, Any, List, Optional

from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import Record, Image as CompImage

# ==================== 默认配置 ====================
API_BASE = "http://45.192.109.44"                     # Meting-API 地址
SEARCH_API = "https://music.163.com/api/search/get/web"   # 网易云搜索 API
SEARCH_COUNT = 10                                      # 默认搜索返回数量
STATE_EXPIRE = 300                                     # 用户状态过期时间（秒）
TIMEOUT = 15                                           # 网络请求超时（秒）
DOWNLOAD_TIMEOUT = 30                                  # 音频下载超时（秒）

# ==================== 图片字体配置 ====================
try:
    from PIL import Image, ImageDraw, ImageFont
    PILLOW_AVAILABLE = True
    try:
        FONT_PATH = "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"
        TITLE_FONT = ImageFont.truetype(FONT_PATH, 24)
        NORMAL_FONT = ImageFont.truetype(FONT_PATH, 18)
        SMALL_FONT = ImageFont.truetype(FONT_PATH, 14)
    except:
        TITLE_FONT = ImageFont.load_default()
        NORMAL_FONT = ImageFont.load_default()
        SMALL_FONT = ImageFont.load_default()
        logger.warning("中文字体未找到，图片中的中文可能显示异常。建议安装 fonts-wqy-microhei")
except ImportError:
    PILLOW_AVAILABLE = False
    logger.warning("Pillow 未安装，将使用纯文本列表。请执行 pip install pillow 以获得图片列表。")

# ==================== 插件主类 ====================
@register("music_selector", "YourName", "点歌插件", "1.0.0")
class MusicSelectorPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.user_states: Dict[str, Dict[str, Any]] = {}
        logger.info("点歌插件已加载，使用 on_message 钩子")

    async def on_message(self, event: AstrMessageEvent):
        """
        重写 Star 的 on_message 方法，接收所有消息
        """
        text = event.message_str.strip()
        if not text:
            return

        logger.info(f"收到消息: {text}")

        # ---------- 处理“点歌”指令 ----------
        if text.startswith("点歌"):
            # 提取歌名：去除“点歌”前缀并去除首尾空格
            name = text[2:].strip()
            await self._handle_search(event, name)
            return

        # ---------- 处理数字选择 ----------
        if text.isdigit():
            await self._handle_choice(event, text)
            return

        # 其他消息忽略

    # ---------- 搜索处理 ----------
    async def _handle_search(self, event: AstrMessageEvent, name: str):
        if not name:
            yield event.plain_result("请提供歌名，例如：点歌 晴天")
            return

        params = {
            "s": name,
            "type": 1,
            "offset": 0,
            "total": True,
            "limit": SEARCH_COUNT
        }
        try:
            resp = requests.get(SEARCH_API, params=params, timeout=TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            raw_songs = data.get('result', {}).get('songs', [])
        except Exception as e:
            logger.error(f"搜索失败: {e}")
            yield event.plain_result(f"搜索失败，请稍后重试。错误：{str(e)}")
            return

        if not raw_songs:
            yield event.plain_result("未找到相关歌曲")
            return

        # 格式化歌曲信息
        songs = []
        for song in raw_songs[:SEARCH_COUNT]:
            song_id = song.get('id')
            song_name = song.get('name', '未知')
            artists = song.get('artists', [])
            artist = artists[0].get('name', '未知') if artists else '未知'
            duration_ms = song.get('duration', 0)
            duration = duration_ms // 1000 if duration_ms else 0
            album = song.get('album', {}).get('name', '未知')
            songs.append({
                'id': song_id,
                'name': song_name,
                'artist': artist,
                'duration': duration,
                'album': album
            })

        # 保存用户状态
        user_key = self._get_user_key(event)
        self.user_states[user_key] = {
            "songs": songs,
            "expire": time.time() + STATE_EXPIRE
        }

        # 发送列表
        if PILLOW_AVAILABLE:
            img_bytes = self._generate_song_list_image(songs)
            if img_bytes:
                yield event.send(CompImage.from_bytes(img_bytes))
            else:
                yield event.plain_result("生成图片失败，使用文本列表：\n" + self._generate_text_list(songs))
        else:
            yield event.plain_result(self._generate_text_list(songs))

    # ---------- 数字选择处理 ----------
    async def _handle_choice(self, event: AstrMessageEvent, num_str: str):
        user_key = self._get_user_key(event)
        state = self.user_states.get(user_key)
        if not state:
            return  # 无点歌状态
        if time.time() > state['expire']:
            del self.user_states[user_key]
            yield event.plain_result("⏰ 点歌已过期，请重新发送“点歌 歌名”搜索。")
            return

        idx = int(num_str) - 1
        songs = state['songs']
        if idx < 0 or idx >= len(songs):
            yield event.plain_result("序号无效，请重新点歌")
            return

        song = songs[idx]
        song_id = song.get('id')
        song_name = song.get('name', '未知')
        song_artist = song.get('artist', '未知')

        # 获取播放链接
        audio_url = None
        try:
            url_resp = requests.get(
                f"{API_BASE}/",
                params={"type": "url", "id": song_id},
                timeout=TIMEOUT
            )
            url_resp.raise_for_status()
            url_data = url_resp.json()
            audio_url = url_data.get('url')
            if not audio_url:
                raise Exception("未获取到播放链接")
        except Exception as e:
            logger.error(f"获取音频失败: {e}")
            yield event.plain_result(f"获取音频失败：{str(e)}")
            return

        # 验证音频 URL
        try:
            head_resp = requests.head(audio_url, timeout=TIMEOUT)
            if head_resp.status_code != 200:
                raise Exception("音频链接无效")
            content_type = head_resp.headers.get('Content-Type', '')
            if not content_type.startswith('audio/'):
                logger.warning(f"音频链接 Content-Type 异常: {content_type}")
        except Exception as e:
            logger.error(f"音频 URL 验证失败: {e}")
            yield event.plain_result("获取的音频链接无效，请稍后重试")
            return

        # 流式下载并发送
        tmp_path = None
        try:
            with requests.get(audio_url, stream=True, timeout=DOWNLOAD_TIMEOUT) as r:
                r.raise_for_status()
                with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp:
                    for chunk in r.iter_content(chunk_size=8192):
                        tmp.write(chunk)
                    tmp_path = tmp.name

            yield event.send(Record.from_file_sync(tmp_path))
            logger.info(f"点歌成功 - 歌曲：{song_name} 序号：{idx+1} 用户：{event.get_sender_id()}")
        except Exception as e:
            logger.error(f"发送语音失败: {e}")
            yield event.plain_result(f"发送语音失败：{str(e)}")
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

        del self.user_states[user_key]

    # ---------- 辅助方法 ----------
    def _get_user_key(self, event: AstrMessageEvent) -> str:
        group_id = event.get_group_id() if event.get_group_id() else "private"
        return f"{event.get_sender_id()}_{group_id}"

    def _generate_song_list_image(self, songs: List[Dict]) -> Optional[bytes]:
        # ... 与之前版本相同，这里省略以节省篇幅，您可复制之前优化版的图片生成代码 ...
        # 请将之前提供的图片生成代码复制至此，或直接使用文本列表。
        # 为了完整性，此处返回 None，实际应包含图片生成逻辑。
        return None

    def _generate_text_list(self, songs: List[Dict]) -> str:
        lines = ["🎵 为您找到以下歌曲：\n"]
        for idx, song in enumerate(songs, 1):
            name = song.get('name', '未知')
            artist = song.get('artist', '未知')
            duration = song.get('duration', 0)
            minutes = duration // 60
            seconds = duration % 60
            duration_str = f"{minutes:02d}:{seconds:02d}"
            album = song.get('album', '未知')
            lines.append(f"{idx}. 《{name}》 - {artist} [{duration_str}] {album}")
        lines.append("\n⏩ 请直接发送数字序号选择要播放的歌曲（5分钟内有效）")
        return "\n".join(lines)

    async def terminate(self):
        self.user_states.clear()
