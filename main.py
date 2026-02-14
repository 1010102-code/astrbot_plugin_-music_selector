import time
import requests
import tempfile
import os
from io import BytesIO
from typing import Dict, Any, List, Optional

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api.message_components import Record, Image as CompImage

# 尝试导入 Pillow 用于图片生成
try:
    from PIL import Image, ImageDraw, ImageFont
    PILLOW_AVAILABLE = True
except ImportError:
    PILLOW_AVAILABLE = False
    logger.warning("Pillow 未安装，将使用纯文本列表（请安装 pillow 以获得图片列表）")

# ==================== 配置 ====================
API_BASE = "http://45.192.109.44"          # 您的 Meting-API 地址
SEARCH_COUNT = 10                          # 每次搜索返回的最大歌曲数
STATE_EXPIRE = 300                          # 用户状态过期时间（秒）

# 字体配置（仅用于图片模式）
try:
    # 常见 Linux 字体路径，请根据实际环境调整
    FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    TITLE_FONT = ImageFont.truetype(FONT_PATH, 24)
    NORMAL_FONT = ImageFont.truetype(FONT_PATH, 18)
    SMALL_FONT = ImageFont.truetype(FONT_PATH, 14)
except:
    TITLE_FONT = ImageFont.load_default()
    NORMAL_FONT = ImageFont.load_default()
    SMALL_FONT = ImageFont.load_default()

# ==================== 插件主类 ====================
@register("music_selector", "YourName", "一个美观的点歌插件，返回图片列表并发送语音", "1.0.0")
class MusicSelectorPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.user_states: Dict[str, Dict[str, Any]] = {}  # 存储用户临时数据

    # ---------- 指令：点歌 ----------
    @filter.command("点歌")
    async def search_music(self, event: AstrMessageEvent, name: Optional[str] = None):
        """点歌指令：发送“点歌 歌名”搜索歌曲，返回图片列表"""
        if not name:
            yield event.plain_result("请提供歌名，例如：点歌 晴天")
            return

        # 调用 Meting-API 搜索
        try:
            resp = requests.get(
                f"{API_BASE}/",
                params={"type": "search", "name": name},
                timeout=10
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.error(f"搜索失败: {e}")
            yield event.plain_result(f"搜索失败：{str(e)}")
            return

        if not data:
            yield event.plain_result("未找到相关歌曲")
            return

        # 取前 SEARCH_COUNT 首
        songs = data[:SEARCH_COUNT]

        # 保存用户状态
        user_key = self._get_user_key(event)
        self.user_states[user_key] = {
            "songs": songs,
            "expire": time.time() + STATE_EXPIRE
        }

        # 生成并发送列表（优先使用图片，否则用纯文本）
        if PILLOW_AVAILABLE:
            img_bytes = self._generate_song_list_image(songs)
            if img_bytes:
                yield event.send(CompImage.from_bytes(img_bytes))
            else:
                yield event.plain_result("生成图片失败，使用文本列表：\n" + self._generate_text_list(songs))
        else:
            yield event.plain_result(self._generate_text_list(songs))

    # ---------- 处理用户选择的数字 ----------
    @filter.message_type()
    async def handle_choice(self, event: AstrMessageEvent):
        """处理用户选择的数字序号（纯数字消息）"""
        text = event.get_message_str().strip()
        if not text.isdigit():
            return

        # 获取用户状态
        user_key = self._get_user_key(event)
        state = self.user_states.get(user_key)
        if not state or time.time() > state['expire']:
            return  # 无有效状态或已过期

        idx = int(text) - 1
        songs = state['songs']
        if idx < 0 or idx >= len(songs):
            yield event.plain_result("序号无效，请重新点歌")
            return

        # 获取选中的歌曲
        song = songs[idx]
        song_id = song.get('id')
        song_name = song.get('name', '未知')
        song_artist = song.get('artist', '未知')

        # 调用 API 获取播放链接
        try:
            url_resp = requests.get(
                f"{API_BASE}/",
                params={"type": "url", "id": song_id},
                timeout=10
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

        # 下载音频并发送语音
        try:
            audio_data = requests.get(audio_url, timeout=15).content
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp:
                tmp.write(audio_data)
                tmp_path = tmp.name

            # 发送语音（使用 Record 组件）
            yield event.send(Record.from_file_sync(tmp_path))

            # 清理临时文件
            os.unlink(tmp_path)
        except Exception as e:
            logger.error(f"发送语音失败: {e}")
            yield event.plain_result(f"发送语音失败：{str(e)}")
            return

        # 记录 INFO 日志
        logger.info(f"点歌成功 - 歌曲：{song_name} 序号：{idx+1} 用户：{event.get_sender_id()}")

        # 删除已使用的状态，防止重复选择
        del self.user_states[user_key]

    # ---------- 辅助方法 ----------
    def _get_user_key(self, event: AstrMessageEvent) -> str:
        """生成用户唯一键（用户ID + 群ID/private）"""
        group_id = event.get_group_id() if event.get_group_id() else "private"
        return f"{event.get_sender_id()}_{group_id}"

    def _generate_song_list_image(self, songs: List[Dict]) -> Optional[bytes]:
        """生成歌曲列表图片，返回 PNG 字节数据，失败返回 None"""
        try:
            # 图片尺寸
            img_width = 600
            row_height = 40
            header_height = 60
            footer_height = 50
            img_height = header_height + len(songs) * row_height + footer_height

            # 创建白色背景
            img = Image.new('RGB', (img_width, img_height), color=(255, 255, 255))
            draw = ImageDraw.Draw(img)

            # 绘制标题
            draw.text((20, 10), "🎵 点歌结果", fill=(0, 0, 0), font=TITLE_FONT)

            # 绘制表头
            y = header_height - row_height
            draw.text((20, y), "序号", fill=(100, 100, 100), font=NORMAL_FONT)
            draw.text((70, y), "歌名", fill=(100, 100, 100), font=NORMAL_FONT)
            draw.text((270, y), "歌手", fill=(100, 100, 100), font=NORMAL_FONT)
            draw.text((430, y), "时长", fill=(100, 100, 100), font=NORMAL_FONT)

            # 绘制分割线
            draw.line([(20, y+30), (img_width-20, y+30)], fill=(200, 200, 200), width=1)

            # 绘制歌曲行
            for idx, song in enumerate(songs, 1):
                y = header_height + (idx-1) * row_height
                # 序号
                draw.text((20, y), str(idx), fill=(0, 0, 0), font=NORMAL_FONT)
                # 歌名（截断）
                name = song.get('name', '未知')
                if len(name) > 12:
                    name = name[:12] + "..."
                draw.text((70, y), name, fill=(0, 0, 0), font=NORMAL_FONT)
                # 歌手
                artist = song.get('artist', '未知')
                if len(artist) > 8:
                    artist = artist[:8] + "..."
                draw.text((270, y), artist, fill=(0, 0, 0), font=NORMAL_FONT)
                # 时长
                duration = song.get('duration', 0)
                minutes = duration // 60
                seconds = duration % 60
                duration_str = f"{minutes:02d}:{seconds:02d}"
                draw.text((430, y), duration_str, fill=(0, 0, 0), font=NORMAL_FONT)

            # 绘制底部提示
            y = img_height - footer_height + 10
            draw.text((20, y), "⏩ 请直接发送数字序号选择要播放的歌曲（5分钟内有效）",
                      fill=(255, 0, 0), font=SMALL_FONT)

            # 转换为字节
            img_bytes = BytesIO()
            img.save(img_bytes, format='PNG')
            return img_bytes.getvalue()
        except Exception as e:
            logger.error(f"生成图片失败: {e}")
            return None

    def _generate_text_list(self, songs: List[Dict]) -> str:
        """生成纯文本列表（当 Pillow 不可用时使用）"""
        lines = ["🎵 为您找到以下歌曲：\n"]
        for idx, song in enumerate(songs, 1):
            name = song.get('name', '未知')
            artist = song.get('artist', '未知')
            duration = song.get('duration', 0)
            minutes = duration // 60
            seconds = duration % 60
            duration_str = f"{minutes:02d}:{seconds:02d}"
            album = song.get('album', '未知')
            lines.append(
                f"{idx}. 《{name}》 - {artist}\n"
                f"   时长：{duration_str}  专辑：{album}\n"
            )
        lines.append("\n⏩ 请直接发送数字序号选择要播放的歌曲（5分钟内有效）")
        return "\n".join(lines)

    async def terminate(self):
        """插件卸载时清理用户状态"""
        self.user_states.clear()
