import asyncio
import html
import json
import re
from typing import Optional, Tuple, Dict, Any, List
import aiohttp

from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, AstrBotConfig
import astrbot.api.message_components as Comp

MANIFEST_URL = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
ZENDESK_RELEASE_SECTION = "https://feedback.minecraft.net/api/v2/help_center/en-us/sections/360001186971/articles.json"
ZENDESK_SNAPSHOT_SECTION = "https://feedback.minecraft.net/api/v2/help_center/en-us/sections/360002267532/articles.json"
WIKI_API_URL = "https://minecraft.wiki/api.php"


@register(
    "astrbot_plugin_mc_update",
    "cyone123",
    "Minecraft 版本更新自动检测与 LLM 总结推送插件，支持 QQ 官方机器人",
    "1.0.0"
)
class MinecraftUpdatePlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._check_task: Optional[asyncio.Task] = None
        self._session: Optional[aiohttp.ClientSession] = None

    async def initialize(self):
        """插件初始化，启动后台轮询检测任务"""
        # 初始化存储的最新版本（避免初次启动把已有版本当更新全部推送）
        try:
            last_rel = await self._get_stored_version("mc_last_release")
            if not last_rel:
                manifest = await self._fetch_version_manifest()
                if manifest and "latest" in manifest:
                    cur_rel = manifest["latest"].get("release")
                    cur_snap = manifest["latest"].get("snapshot")
                    if cur_rel:
                        await self._set_stored_version("mc_last_release", cur_rel)
                    if cur_snap:
                        await self._set_stored_version("mc_last_snapshot", cur_snap)
                    logger.info(f"[MC Update] 初始化记录当前最新版本: Release={cur_rel}, Snapshot={cur_snap}")
        except Exception as e:
            logger.warning(f"[MC Update] 初始化版本记录失败: {e}")

        # 启动后台检测轮询任务
        self._check_task = asyncio.create_task(self._check_loop())
        logger.info("[MC Update] Minecraft 更新检测插件已初始化，后台轮询任务已启动")

    async def terminate(self):
        """插件卸载，优雅终止轮询任务与网络会话"""
        if self._check_task:
            self._check_task.cancel()
            try:
                await self._check_task
            except asyncio.CancelledError:
                pass
        if self._session and not self._session.closed:
            await self._session.close()
        logger.info("[MC Update] Minecraft 更新检测插件已卸载")

    async def _get_session(self) -> aiohttp.ClientSession:
        """获取或创建 aiohttp 会话"""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=25)
            self._session = aiohttp.ClientSession(
                timeout=timeout,
                headers={"User-Agent": "AstrBot-MinecraftUpdatePlugin/1.0 (https://github.com/cyone123/astrbot_plugin)"}
            )
        return self._session

    # ==================== 版本持久化辅助 ====================

    async def _get_stored_version(self, key: str) -> Optional[str]:
        """优先使用 AstrBot KV 存储，兼容 config 回退"""
        try:
            val = await self.get_kv_data(key, None)
            if val:
                return str(val)
        except Exception:
            pass
        return self.config.get(key, None)

    async def _set_stored_version(self, key: str, val: str):
        """保存版本号到 KV 存储与 config"""
        try:
            await self.put_kv_data(key, val)
        except Exception:
            pass
        self.config[key] = val
        self.config.save_config()

    # ==================== 后台轮询与更新检测 ====================

    async def _check_loop(self):
        """后台轮询主循环"""
        while True:
            interval = max(60, int(self.config.get("check_interval", 1800)))
            try:
                await asyncio.sleep(interval)
                await self._check_updates()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[MC Update] 检查更新时出现异常: {e}", exc_info=True)

    async def _fetch_version_manifest(self) -> Optional[dict]:
        """获取 Mojang 官方 Version Manifest V2"""
        session = await self._get_session()
        try:
            async with session.get(MANIFEST_URL) as resp:
                if resp.status == 200:
                    return await resp.json(content_type=None)
                else:
                    logger.warning(f"[MC Update] 获取 Version Manifest 状态码非 200: {resp.status}")
        except Exception as e:
            logger.error(f"[MC Update] 请求 Version Manifest 失败: {e}")
        return None

    def _clean_html(self, raw_html: str) -> str:
        """清洗 HTML 内容为纯文本结构"""
        text = re.sub(r'<(script|style).*?</\1>', '', raw_html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'</?(h[1-6]|p|div|section|article)[^>]*>', '\n', text, flags=re.IGNORECASE)
        text = re.sub(r'<li[^>]*>', '\n- ', text, flags=re.IGNORECASE)
        text = re.sub(r'</?li[^>]*>', '', text, flags=re.IGNORECASE)
        text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
        text = re.sub(r'<[^>]+>', '', text)
        text = html.unescape(text)
        lines = [line.strip() for line in text.splitlines()]
        clean_lines = [l for l in lines if l]
        return '\n'.join(clean_lines)

    async def _fetch_changelog(self, version_id: str, version_type: str) -> Tuple[str, str, str]:
        """
        获取指定版本的更新日志与文章链接
        返回: (title, article_url, cleaned_content)
        """
        session = await self._get_session()
        section_url = ZENDESK_RELEASE_SECTION if version_type == "release" else ZENDESK_SNAPSHOT_SECTION

        # 1. 尝试从 Mojang 官方 Zendesk 获取
        try:
            async with session.get(section_url) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    articles = data.get("articles", [])
                    # 匹配标题中含有版本号的文章
                    for art in articles:
                        title = art.get("title", "")
                        # 兼容形如 "Minecraft Java Edition - 26.3" 或 "24w46a"
                        if version_id.lower() in title.lower():
                            art_url = art.get("html_url", "")
                            raw_body = art.get("body", "")
                            cleaned = self._clean_html(raw_body)
                            logger.info(f"[MC Update] 成功在 Zendesk 找到文章: {title} ({art_url})")
                            return title, art_url, cleaned
        except Exception as e:
            logger.warning(f"[MC Update] 从 Zendesk 获取文章失败: {e}")

        # 2. 备选：尝试从 Minecraft.wiki API 获取
        try:
            wiki_page_title = f"Java_Edition_{version_id}"
            params = {
                "action": "query",
                "titles": wiki_page_title,
                "prop": "extracts",
                "explaintext": "1",
                "format": "json"
            }
            async with session.get(WIKI_API_URL, params=params) as resp:
                if resp.status == 200:
                    wiki_data = await resp.json(content_type=None)
                    pages = wiki_data.get("query", {}).get("pages", {})
                    for pid, pdata in pages.items():
                        if pid != "-1" and "extract" in pdata:
                            extract = pdata["extract"].strip()
                            if extract:
                                wiki_url = f"https://minecraft.wiki/w/{wiki_page_title}"
                                logger.info(f"[MC Update] 成功在 Minecraft Wiki 找到内容: {wiki_page_title}")
                                return f"Minecraft Java Edition {version_id}", wiki_url, extract
        except Exception as e:
            logger.warning(f"[MC Update] 从 Minecraft Wiki 获取失败: {e}")

        # 3. 缺省备用信息
        default_url = f"https://www.minecraft.net/article/minecraft-java-edition-{version_id.replace('.', '-')}"
        default_content = f"Minecraft Java Edition 发布了新版本 {version_id}（类型: {version_type}）。请前往官方网站查看详细更新日志。"
        return f"Minecraft Java Edition {version_id}", default_url, default_content

    async def _get_chat_provider_id(self, umo: Optional[str] = None) -> Optional[str]:
        """获取用于总结的聊天模型 Provider ID"""
        configured_id = str(self.config.get("chat_provider_id", "")).strip()
        if configured_id:
            return configured_id
        try:
            if umo:
                prov_id = await self.context.get_current_chat_provider_id(umo=umo)
            else:
                prov_id = await self.context.get_current_chat_provider_id()
            if prov_id:
                return prov_id
        except Exception:
            pass
        return None

    async def _summarize_changelog(
        self,
        version_id: str,
        version_type: str,
        changelog_text: str,
        article_url: str,
        umo: Optional[str] = None
    ) -> str:
        """调用 LLM 生成更新日志的中文精简总结"""
        # 截断过长日志（保留前 6000 字符，通常包含全部核心新特性与改动）
        truncated_changelog = changelog_text[:6000]
        if len(changelog_text) > 6000:
            truncated_changelog += "\n\n...(后续大量详细技术细节与 Bug 修复已省略)..."

        default_prompt_tmpl = (
            "你是一个 Minecraft 资讯播报助手。请根据以下 Minecraft 更新日志内容，用生动友好、结构清晰的中文写一份版本更新速报。\n"
            "要求：\n"
            "1. 突出版本号与版本类型（正式版/快照版）。\n"
            "2. 提炼出 3-5 点最核心、玩家最关心的更新亮点或重要改动。\n"
            "3. 如果有重要 Bug 修复或技术调整，用简明语言概括 1-2 条。\n"
            "4. 总体字数控制在 400 字以内，排版精美，适当使用 Emoji。\n\n"
            "更新日志内容：\n{changelog}"
        )
        prompt_tmpl = self.config.get("custom_prompt", default_prompt_tmpl)
        try:
            prompt = prompt_tmpl.format(changelog=truncated_changelog, version=version_id, type=version_type)
        except Exception:
            prompt = prompt_tmpl.replace("{changelog}", truncated_changelog)

        provider_id = await self._get_chat_provider_id(umo)
        try:
            llm_resp = await self.context.llm_generate(
                chat_provider_id=provider_id,
                prompt=prompt
            )
            if llm_resp and hasattr(llm_resp, "completion_text") and llm_resp.completion_text:
                return llm_resp.completion_text.strip()
        except Exception as e:
            logger.error(f"[MC Update] 调用 LLM 总结失败: {e}")

        # 回退默认简短总结
        type_desc = "正式版 (Release)" if version_type == "release" else "快照/预览版 (Snapshot)"
        return f"🎮 Minecraft Java Edition 发布了新版本：{version_id} ({type_desc})！\n由于大模型总结异常，未能自动生成详细摘要，请点击下方链接查看完整日志。"

    async def _broadcast_update(self, version_id: str, version_type: str, summary: str, article_url: str):
        """向所有订阅的群聊/会话广播更新（完美适配 QQ 官方机器人及多平台）"""
        subscribers = self.config.get("subscribers", [])
        if not subscribers:
            logger.info("[MC Update] 当前没有订阅的群聊或会话，跳过推送")
            return

        type_tag = "正式版" if version_type == "release" else "快照版"
        # 组织适合 QQ 官方机器人的纯文本排版，避免复杂 markdown 被官方平台丢弃
        msg_text = (
            f"📢【Minecraft 版本更新速报】\n"
            f"━━━━━━━━━━━━━━\n"
            f"📌 版本：Java Edition {version_id} ({type_tag})\n\n"
            f"{summary}\n\n"
            f"━━━━━━━━━━━━━━\n"
            f"🔗 官方更新日志：\n{article_url}"
        )

        chain = MessageChain().message(msg_text)

        logger.info(f"[MC Update] 开始向 {len(subscribers)} 个订阅目标推送版本 {version_id}...")
        for umo in subscribers:
            try:
                await self.context.send_message(umo, chain)
                logger.info(f"[MC Update] 成功向 {umo} 推送更新")
            except Exception as e:
                logger.warning(f"[MC Update] 向 {umo} 推送更新失败: {e}")
            # 适当等待以防速率限制
            await asyncio.sleep(0.5)

    async def _check_updates(self, force: bool = False) -> List[Dict[str, Any]]:
        """检查是否有新版本，如发现新版本则生成总结并推送"""
        manifest = await self._fetch_version_manifest()
        if not manifest or "latest" not in manifest:
            return []

        latest_rel = manifest["latest"].get("release")
        latest_snap = manifest["latest"].get("snapshot")
        notify_snapshot = bool(self.config.get("notify_snapshot", False))

        last_rel = await self._get_stored_version("mc_last_release")
        last_snap = await self._get_stored_version("mc_last_snapshot")

        detected_updates = []

        # 1. 检查正式版
        if latest_rel and (force or latest_rel != last_rel):
            logger.info(f"[MC Update] 发现新正式版: {latest_rel} (本地记录: {last_rel})")
            detected_updates.append({"id": latest_rel, "type": "release"})

        # 2. 检查快照版 (若已配置开启)
        if notify_snapshot and latest_snap and (force or latest_snap != last_snap):
            # 如果快照版版本号和正式版相同（发布正式版时 latest.snapshot 也会更新为该版本），避免重复推送
            if latest_snap != latest_rel:
                logger.info(f"[MC Update] 发现新快照版: {latest_snap} (本地记录: {last_snap})")
                detected_updates.append({"id": latest_snap, "type": "snapshot"})

        # 执行获取内容、总结与推送
        for update in detected_updates:
            vid = update["id"]
            vtype = update["type"]
            title, url, content = await self._fetch_changelog(vid, vtype)
            summary = await self._summarize_changelog(vid, vtype, content, url)
            await self._broadcast_update(vid, vtype, summary, url)

            # 更新持久化记录
            if vtype == "release":
                await self._set_stored_version("mc_last_release", vid)
            else:
                await self._set_stored_version("mc_last_snapshot", vid)

        return detected_updates

    # ==================== 指令注册与处理 ====================

    @filter.command("mc")
    async def mc_command(self, event: AstrMessageEvent, action: str = "", arg: str = ""):
        """Minecraft 更新推送管理指令
        用法：
        /mc help - 查看帮助与当前状态
        /mc sub - 订阅当前群的更新推送
        /mc unsub - 取消当前群的更新推送
        /mc check - 立即手动检查是否有新版本
        /mc latest [release/snapshot] - 查看当前最新版本的总结速报
        """
        action = action.strip().lower()
        umo = event.unified_msg_origin
        subscribers: List[str] = self.config.get("subscribers", [])

        if action == "sub":
            if umo in subscribers:
                yield event.plain_result("ℹ️ 当前群聊/会话已在订阅列表中，无需重复订阅。")
                return
            subscribers.append(umo)
            self.config["subscribers"] = subscribers
            self.config.save_config()
            logger.info(f"[MC Update] 会话 {umo} 成功订阅更新推送")
            yield event.plain_result("✅ 成功订阅 Minecraft 版本更新推送！新版本发布时将自动推送到本群。")

        elif action == "unsub":
            if umo not in subscribers:
                yield event.plain_result("ℹ️ 当前群聊/会话尚未订阅 Minecraft 版本更新。")
                return
            subscribers.remove(umo)
            self.config["subscribers"] = subscribers
            self.config.save_config()
            logger.info(f"[MC Update] 会话 {umo} 取消订阅更新推送")
            yield event.plain_result("✅ 已取消当前群聊/会话的 Minecraft 版本更新订阅。")

        elif action == "check":
            yield event.plain_result("🔍 正在检查 Minecraft 最新版本信息，请稍候...")
            manifest = await self._fetch_version_manifest()
            if not manifest or "latest" not in manifest:
                yield event.plain_result("❌ 获取 Minecraft 版本清单失败，请稍后重试。")
                return

            latest_rel = manifest["latest"].get("release")
            latest_snap = manifest["latest"].get("snapshot")
            last_rel = await self._get_stored_version("mc_last_release")
            last_snap = await self._get_stored_version("mc_last_snapshot")

            status_msg = (
                f"📊 【Minecraft 版本状态】\n"
                f"• 当前最新正式版: {latest_rel} (本地已记录: {last_rel})\n"
                f"• 当前最新快照版: {latest_snap} (本地已记录: {last_snap})\n"
                f"• 快照推送: {'已开启' if self.config.get('notify_snapshot', False) else '已关闭'}\n"
            )

            # 触发检测
            updates = await self._check_updates(force=False)
            if updates:
                new_vers = ", ".join([f"{u['id']}({u['type']})" for u in updates])
                status_msg += f"\n🎉 检测到新版本发布并已触发推送：{new_vers}"
            else:
                status_msg += "\n✅ 当前已是最新版本，无新更新发布。"

            yield event.plain_result(status_msg)

        elif action == "latest":
            vtype = "snapshot" if arg.strip().lower() in ["snapshot", "snap", "快照"] else "release"
            yield event.plain_result(f"⏳ 正在拉取 Minecraft 最新{ '快照版' if vtype == 'snapshot' else '正式版' }并由 AI 生成速报，请稍候...")

            manifest = await self._fetch_version_manifest()
            if not manifest or "latest" not in manifest:
                yield event.plain_result("❌ 获取 Minecraft 版本清单失败，请稍后重试。")
                return

            target_version = manifest["latest"].get(vtype)
            if not target_version:
                yield event.plain_result("❌ 未找到对应的版本信息。")
                return

            title, url, content = await self._fetch_changelog(target_version, vtype)
            summary = await self._summarize_changelog(target_version, vtype, content, url, umo=umo)

            type_tag = "正式版" if vtype == "release" else "快照版"
            msg_text = (
                f"📢【Minecraft 版本速报】\n"
                f"━━━━━━━━━━━━━━\n"
                f"📌 版本：Java Edition {target_version} ({type_tag})\n\n"
                f"{summary}\n\n"
                f"━━━━━━━━━━━━━━\n"
                f"🔗 官方更新日志：\n{url}"
            )
            yield event.plain_result(msg_text)

        else:
            # help
            is_sub = umo in subscribers
            interval = self.config.get("check_interval", 1800)
            notify_snap = self.config.get("notify_snapshot", False)

            help_msg = (
                "⛏️【Minecraft 更新推送助手】\n"
                "------------------------------\n"
                "📌 支持指令：\n"
                "• /mc sub - 订阅本群的更新推送\n"
                "• /mc unsub - 取消本群的更新推送\n"
                "• /mc check - 立即检查是否有新版本\n"
                "• /mc latest - 查看最新正式版 AI 速报\n"
                "• /mc latest snapshot - 查看最新快照版 AI 速报\n"
                "• /mc help - 查看本帮助\n"
                "------------------------------\n"
                f"⚙️ 当前状态：\n"
                f"• 本群订阅状态: {'已订阅 ✅' if is_sub else '未订阅 ❌'}\n"
                f"• 轮询间隔: {interval} 秒 ({interval // 60} 分钟)\n"
                f"• 快照推送: {'开启' if notify_snap else '关闭'}\n"
                f"• 订阅总数: {len(subscribers)} 个群聊/会话"
            )
            yield event.plain_result(help_msg)
