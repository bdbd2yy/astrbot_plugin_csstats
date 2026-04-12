import aiohttp
import os

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import At, Image, Plain, Reply
from astrbot.api.star import Context, Star, StarTools, register

from .core.plugin_logic import CsstatsPluginLogic
from .core.report_generator import MatchReportGenerator


@register("csstat", "bdbd2yy", "全平台 cs 战绩查询插件", "2.0.0")
class Csstats(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.data_dir = StarTools.get_data_dir()
        self._session = None
        self.report_generator = MatchReportGenerator()

    async def initialize(self):
        """可选择实现异步的插件初始化方法，当实例化该插件类之后会自动调用该方法。"""
        self.data_dir.mkdir(exist_ok=True)
        self._session = aiohttp.ClientSession()
        self.plugin_logic = CsstatsPluginLogic(
            self._session, self.data_dir, prompt=""
        )
        await self.plugin_logic.initialize_storage()

    def _quoted_chain_result(self, event: AstrMessageEvent, chain: list):
        message_id = getattr(event.message_obj, "message_id", "")
        if message_id:
            return event.chain_result([Reply(id=message_id), *chain])
        return event.chain_result(chain)

    async def _handle_bind_request(
        self,
        event: AstrMessageEvent,
        raw_text: str | None = None,
    ):
        if self._session is None:
            yield event.plain_result("插件会话尚未初始化，请稍后再试")
            return

        request_data = await self.plugin_logic.handle_player_data_request_bind(
            event,
            raw_text=raw_text,
        )
        if request_data.error_msg:
            yield event.plain_result(request_data.error_msg)
            return

        await self.plugin_logic.get_domain(self._session, request_data)
        if request_data.error_msg:
            yield event.plain_result(request_data.error_msg)
            return
        logger.info(
            f"成功获取到 {request_data.player_name} 的domain: {request_data.domain}"
        )

        await self.plugin_logic.get_uuid(self._session, request_data)
        if not request_data.uuid:
            yield event.plain_result(
                f"获取玩家 {request_data.player_name} 的 uuid 信息失败，请检查用户名是否输入正确"
            )
            return
        logger.info(
            f"成功获取到 {request_data.player_name} 的uuid: {request_data.uuid}"
        )

        await self.plugin_logic.save_player_binding(request_data)
        yield event.plain_result(
            f"成功添加用户 {request_data.user_name} 的 {request_data.platform} 平台玩家 {request_data.player_name}。"
        )

    async def _send_report_image(
        self,
        event: AstrMessageEvent,
        image_url: str,
        teammate_names: list,
        target_is_worst: bool,
        worst_player_qq,
        worst_player_name: str,
    ) -> bool:
        try:
            logger.info(f"准备发送战绩图片文件: {image_url}")
            if os.path.exists(image_url):
                logger.info(f"战绩图片文件大小: {os.path.getsize(image_url)} bytes")
            await self.context.send_message(
                event.unified_msg_origin,
                MessageChain([Image.fromFileSystem(image_url)]),
            )
        except Exception as e:
            logger.warning(f"发送战绩图片失败，请联系管理员修复插件问题: {e}")
            return False

        if teammate_names and worst_player_qq and not target_is_worst:
            await self.context.send_message(
                event.unified_msg_origin,
                MessageChain(
                    [
                        Plain("本局最菜队友：" + worst_player_name),
                        At(qq=worst_player_qq),
                        Plain("！"),
                    ]
                ),
            )
        return True

    async def _handle_match_request(
        self,
        event: AstrMessageEvent,
        raw_text: str | None = None,
    ):
        if self._session is None:
            yield self._quoted_chain_result(
                event,
                [Plain("插件会话尚未初始化，请稍后再试")],
            )
            return

        request_data, match_round = await self.plugin_logic.handle_player_data_request_match(
            event,
            raw_text=raw_text,
        )
        if request_data.error_msg:
            logger.error(f"{request_data.error_msg}")
            yield self._quoted_chain_result(event, [Plain(f"{request_data.error_msg}")])
            return

        match_id = await self.plugin_logic.get_match_id(
            self._session, request_data, match_round
        )
        if not match_id:
            logger.error(f"{request_data.error_msg}")
            yield self._quoted_chain_result(event, [Plain(f"{request_data.error_msg}")])
            return
        logger.info(f"查询到match_id:{match_id}")

        match_stats_json = await self.plugin_logic.get_match_stats(
            self._session, match_id, request_data
        )
        if request_data.error_msg:
            logger.error(f"{request_data.error_msg}")
            yield self._quoted_chain_result(event, [Plain(f"{request_data.error_msg}")])
            return
        logger.info(f"成功查询到match_id为{match_id}的详细数据")

        match_data = await self.plugin_logic.process_json(
            match_stats_json,
            match_round,
            request_data.player_name,
            request_data.platform,
            request_data.uuid,
        )
        if match_data.error_msg:
            logger.error(f"{match_data.error_msg}")
            yield self._quoted_chain_result(event, [Plain(f"{match_data.error_msg}")])
            return
        logger.info("成功处理比赛数据")

        stats_text = await self.plugin_logic.handle_to_llm_text(
            match_data, request_data.player_name, request_data.platform
        )
        if match_data.error_msg:
            logger.error(f"{match_data.error_msg}")
            yield self._quoted_chain_result(event, [Plain(f"{match_data.error_msg}")])
            return

        llm_input_text = await self.plugin_logic.build_llm_evaluation_input(
            match_data,
            request_data.player_name,
            stats_text,
        )
        rsp_text = await self.plugin_logic.call_llm_to_generate_evaluation(
            event, self.context, llm_input_text
        )
        rsp_text = rsp_text or "评价生成失败"
        send_text = f"{stats_text}\n{rsp_text}"

        premade_summary = await self.plugin_logic.get_premade_summary(
            match_stats_json,
            request_data.player_name,
            request_data.platform,
            request_data.uuid,
        )
        teammate_names = premade_summary.get("teammate_names", [])
        target_is_worst = premade_summary.get("target_is_worst", False)
        worst_player_qq = premade_summary.get("worst_player_qq")
        worst_player_name = premade_summary.get("worst_player_name", "")

        if teammate_names:
            teammate_text = " ".join(teammate_names)
            prefix_text = f"\n本局你和 {teammate_text} 一起组排，最菜的是 "
            if target_is_worst:
                send_text += f"{prefix_text}你自己！"
            elif worst_player_qq:
                send_text += f"{prefix_text}{worst_player_name}！"
            elif worst_player_name:
                send_text += f"{prefix_text}{worst_player_name}！"

        report_payload = self.plugin_logic.build_match_report_payload(
            match_data,
            request_data.player_name,
            request_data.platform,
            stats_text,
            rsp_text,
            premade_summary,
        )

        try:
            image_url = await self.report_generator.generate_image(
                report_payload,
                self.html_render,
            )
        except Exception as e:
            logger.warning(f"生成战绩图片失败，请联系管理员修复插件问题: {e}")
            image_url = None

        if image_url:
            if await self._send_report_image(
                event,
                image_url,
                teammate_names,
                target_is_worst,
                worst_player_qq,
                worst_player_name,
            ):
                return

        yield self._quoted_chain_result(
            event,
            [Plain("战绩图片发送失败，请联系管理员修复插件问题")],
        )
        return

    async def _handle_help_request(self, event: AstrMessageEvent):
        prefix = "/"
        help_msg = f"""cstatcheck插件使用帮助：
1. 账号绑定

示例: {prefix}bind 5e PlayerName
      {prefix}bind pw UserName

name: 5e平台是游戏名称，完美平台是完美app的用户名
5e/pw/mm 分别代表不同平台，5e 是指5eplay平台，pw 是指完美平台，mm 是指官匹平台，官匹查询目前不可用。
      
2. 战绩查询

示例: {prefix}match 5e 1    （数字是最近第几场，1就是最近第一场）
      {prefix}match pw @某某
      {prefix}match mm @某某
"""
        yield event.plain_result(help_msg)

    @filter.command("bind")
    async def add_player_data(self, event: AstrMessageEvent):
        async for result in self._handle_bind_request(event):
            yield result

    @filter.command("match")
    async def fetch_match_stats(self, event: AstrMessageEvent):
        async for result in self._handle_match_request(event):
            yield result

    @filter.command("cs_help")
    async def cs_help(self, event: AstrMessageEvent):
        async for result in self._handle_help_request(event):
            yield result

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def listen_plain_messages(self, event: AstrMessageEvent):
        raw_text = (event.message_str or "").strip()
        if not raw_text or not raw_text.startswith("/"):
            return

        intent = self.plugin_logic.detect_message_intent(raw_text)
        if not intent:
            return

        logger.info(f"csstats passive listener matched intent: {intent}")
        if intent == "bind":
            async for result in self._handle_bind_request(event, raw_text=raw_text):
                yield result
            return
        if intent == "match":
            async for result in self._handle_match_request(event, raw_text=raw_text):
                yield result
            return
        if intent == "help":
            async for result in self._handle_help_request(event):
                yield result
            return

    async def terminate(self):
        """可选择实现异步的插件销毁方法，当插件被卸载/停用时会调用。"""
        logger.info("cstatscheck 插件正在卸载，开始清理后台任务...")
        if self._session:
            await self._session.close()
        logger.info("cstatscheck 插件已卸载，所有状态已清空。")
