import time
import requests
import tempfile
import os
import re
import aiohttp
import asyncio
from io import BytesIO
from typing import Dict, Any, List, Optional

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
import astrbot.api.message_components as Comp

# ==================== 您的 API 配置（完全保留） ====================
API_BASE = "http://45.192.109.44"                     # Meting-API 地址
SEARCH_API = "https://music.163.com/api/search/get/web"   # 网易云搜索 API
SEARCH_COUNT = 10                                      # 默认搜索返回数量
STATE_EXPIRE = 40                                      # 用户状态过期时间（秒）【改为40秒】
TIMEOUT = 15                                           # 网络请求超时（秒）
DOWNLOAD_TIMEOUT = 30                                  # 音频下载超时（秒）

# ==================== 图片生成模块（完全保留您原有的逻辑） ====================
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

def generate_song_list_image(songs: List[Dict]) -> Optional[bytes]:
    """生成歌曲列表图片（您的原有函数）"""
    if not PILLOW_AVAILABLE:
        return None
    try:
        img_width = 600
        row_height = 40
        header_height = 60
        footer_height = 50
        img_height = header_height + len(songs) * row_height + footer_height

        img = Image.new('RGB', (img_width, img_height), color=(255, 255, 255))
        draw = ImageDraw.Draw(img)

        draw.text((20, 10), "🎵 点歌结果", fill=(0, 0, 0), font=TITLE_FONT)

        y = header_height - row_height
        draw.text((20, y), "序号", fill=(100, 100, 100), font=NORMAL_FONT)
        draw.text((70, y), "歌名", fill=(100, 100, 100), font=NORMAL_FONT)
        draw.text((270, y), "歌手", fill=(100, 100, 100), font=NORMAL_FONT)
        draw.text((430, y), "时长", fill=(100, 100, 100), font=NORMAL_FONT)

        draw.line([(20, y+30), (img_width-20, y+30)], fill=(200, 200, 200), width=1)

        for idx, song in enumerate(songs, 1):
            y = header_height + (idx-1) * row_height
            draw.text((20, y), str(idx), fill=(0, 0, 0), font=NORMAL_FONT)
            name = song.get('name', '未知')
            if len(name) > 12:
                name = name[:12] + "..."
            draw.text((70, y), name, fill=(0, 0, 0), font=NORMAL_FONT)
            artist = song.get('artist', '未知')
            if len(artist) > 8:
                artist = artist[:8] + "..."
            draw.text((270, y), artist, fill=(0, 0, 0), font=NORMAL_FONT)
            duration = song.get('duration', 0)
            minutes = duration // 60
            seconds = duration % 60
            duration_str = f"{minutes:02d}:{seconds:02d}"
            draw.text((430, y), duration_str, fill=(0, 0, 0), font=NORMAL_FONT)

        y = img_height - footer_height + 10
        draw.text((20, y), "⏩ 请直接发送数字序号选择要播放的歌曲（5分钟内有效）",
                  fill=(255, 0, 0), font=SMALL_FONT)

        img_bytes = BytesIO()
        img.save(img_bytes, format='PNG')
        return img_bytes.getvalue()
    except Exception as e:
        logger.error(f"生成图片失败: {e}")
        return None

def generate_text_list(songs: List[Dict]) -> str:
    """生成纯文本列表（您的原有函数）"""
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
            f"{idx}. 《{name}》 - {artist} [{duration_str}] {album}"
        )
    lines.append("\n⏩ 请直接发送数字序号选择要播放的歌曲（40秒内有效）")  # 提示改为40秒
    return "\n".join(lines)

# ==================== 插件主类 ====================
@register("nekomusic", "YourName", "点歌插件（改进版）", "1.0.0")
class Main(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        # 存储每个会话的搜索结果，格式: {session_id: {"songs": [], "expire": timestamp, "user_id": id}}
        self.search_results = {}

    @filter.regex(r"^点歌.*")
    async def search_music(self, event: AstrMessageEvent):
        """点歌指令：点歌 歌名"""
        text = event.message_str.strip()
        keyword = text[2:].strip()  # 去掉“点歌”二字
        if not keyword:
            yield event.plain_result("请输入要搜索的歌曲名称，例如：点歌 晴天")
            return

        # 使用网易云搜索 API 获取歌曲列表
        params = {
            "s": keyword,
            "type": 1,
            "offset": 0,
            "total": True,
            "limit": SEARCH_COUNT
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(SEARCH_API, params=params, timeout=TIMEOUT) as resp:
                    if resp.status != 200:
                        yield event.plain_result(f"搜索失败，API返回状态码：{resp.status}")
                        return
                    data = await resp.json()
                    raw_songs = data.get('result', {}).get('songs', [])
        except Exception as e:
            logger.error(f"搜索失败: {e}")
            yield event.plain_result(f"搜索失败：{str(e)}")
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

        # 保存到会话，同时记录发起点歌的用户ID
        session_id = event.session_id
        user_id = event.get_sender_id()
        self.search_results[session_id] = {
            "songs": songs,
            "expire": time.time() + STATE_EXPIRE,
            "user_id": user_id
        }

        # 生成并发送列表
        if PILLOW_AVAILABLE:
            img_bytes = generate_song_list_image(songs)
            if img_bytes:
                yield event.chain_result([
                    Comp.Plain(f"🎵 搜索结果：{keyword}\n共找到 {len(songs)} 首歌曲\n💡 直接发送数字序号即可播放（40秒内有效）"),
                    Comp.Image.fromBytes(img_bytes)
                ])
            else:
                yield event.plain_result("图片生成失败，使用文本列表：\n" + generate_text_list(songs))
        else:
            yield event.plain_result(generate_text_list(songs))

    @filter.regex(r"^\d+$")
    async def play_music(self, event: AstrMessageEvent):
        """播放音乐：用户直接发送数字序号（无需引用）"""
        # 获取会话ID
        session_id = event.session_id
        # 处理可能包含额外信息的会话ID（如Telegram的#xxx）
        match_id = session_id.split('#')[0] if '#' in session_id else session_id

        # 查找该会话是否有搜索结果
        search_data = self.search_results.get(match_id) or self.search_results.get(session_id)
        if not search_data:
            # 没有搜索结果，忽略
            return

        # 检查是否过期
        if time.time() > search_data['expire']:
            # 过期后清理
            if match_id in self.search_results:
                del self.search_results[match_id]
            elif session_id in self.search_results:
                del self.search_results[session_id]
            # 不给提示，直接忽略（也可以给提示，但用户可能还没意识到）
            # 为了友好，可以发一句提示，但可能会被滥用
            # yield event.plain_result("⏰ 点歌已过期，请重新搜索")
            return

        # 验证发送者是否为发起点歌的用户
        current_user = event.get_sender_id()
        if str(current_user) != str(search_data['user_id']):
            # 不是同一个人，忽略（也可以给提示）
            # yield event.plain_result("只有发起点歌的用户才能选择歌曲")
            return

        index = int(event.message_str.strip()) - 1
        songs = search_data['songs']
        if index < 0 or index >= len(songs):
            yield event.plain_result(f"序号无效，请输入 1-{len(songs)} 之间的数字")
            return

        song = songs[index]
        song_id = song['id']
        song_name = song['name']

        # 获取播放链接
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{API_BASE}/", params={"type": "url", "id": song_id}, timeout=TIMEOUT) as resp:
                    if resp.status != 200:
                        yield event.plain_result(f"获取音频失败，状态码：{resp.status}")
                        return
                    data = await resp.json()
                    audio_url = data.get('url')
                    if not audio_url:
                        yield event.plain_result("未获取到播放链接")
                        return
        except Exception as e:
            logger.error(f"获取音频URL失败: {e}")
            yield event.plain_result(f"获取音频失败：{str(e)}")
            return

        # 下载音频并发送
        yield event.plain_result(f"🎵 正在发送《{song_name}》，请稍候...")

        tmp_path = None
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(audio_url, timeout=DOWNLOAD_TIMEOUT) as resp:
                    if resp.status != 200:
                        yield event.plain_result(f"下载音频失败，状态码：{resp.status}")
                        return
                    audio_data = await resp.read()

            # 保存临时文件
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp:
                tmp.write(audio_data)
                tmp_path = tmp.name

            # 发送语音（使用 Comp.Record）
            yield event.chain_result([
                Comp.Record(file=tmp_path)
            ])
            logger.info(f"点歌成功：{song_name}")

        except Exception as e:
            logger.error(f"发送语音失败: {e}")
            yield event.plain_result(f"发送语音失败：{str(e)}")
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

        # 播放完成后清理会话（可选，可保留以便再次选择？按需）
        # 如果您希望用户能连续选择多首，可以不清除，但需要处理重复使用。这里先清除。
        if match_id in self.search_results:
            del self.search_results[match_id]
        elif session_id in self.search_results:
            del self.search_results[session_id]
