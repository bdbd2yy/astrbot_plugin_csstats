from pathlib import Path
import re

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.star import Context

from ..models.match_data import MatchData

PROMPT_PATH = Path(__file__).parent / "prompts" / "cs_comment_prompt.txt"
_LLM_SYSTEM_PROMPT = PROMPT_PATH.read_text(encoding="utf-8")
_COMMENT_PATTERN = re.compile(r"<comment>\s*([\s\S]*?)\s*</comment>", re.IGNORECASE)


class CsAiLogic:
    def _extract_comment_text(self, completion_text: str | None) -> str | None:
        if not completion_text:
            return None
        text = completion_text.strip()
        match = _COMMENT_PATTERN.search(text)
        raw_comment = match.group(1) if match else text
        raw_comment = raw_comment.replace("\r\n", "\n").replace("\r", "\n")
        raw_comment = re.sub(r"[*_`#>]", "", raw_comment)
        raw_comment = re.sub(r"^[ \t]*[-•]+[ \t]*", "", raw_comment, flags=re.MULTILINE)
        raw_comment = re.sub(r"\n{3,}", "\n\n", raw_comment)
        lines = [line.strip() for line in raw_comment.split("\n")]
        cleaned_lines = []
        previous_blank = False
        for line in lines:
            if not line:
                if not previous_blank and cleaned_lines:
                    cleaned_lines.append("")
                previous_blank = True
                continue
            cleaned_lines.append(line)
            previous_blank = False
        while cleaned_lines and cleaned_lines[-1] == "":
            cleaned_lines.pop()
        comment = "\n".join(cleaned_lines).strip()
        return comment or None

    async def call_llm_to_generate_evaluation(
        self, event: AstrMessageEvent, context: Context, stats_text: str
    ) -> str | None:
        prov = context.get_using_provider(umo=event.unified_msg_origin)
        if prov:
            llm_resp = await prov.text_chat(
                prompt=f"{stats_text}",
                context=[
                    {
                        "role": "user",
                        "content": "5eplayer 薛定谔的哥本哈根 最近一场比赛战绩：\nMap: 炙热沙城2 \n比赛结果: 失败 \nRating: 0.91  \nADR: 53.16 \nElo变化: 12.78",
                    },
                    {
                        "role": "assistant",
                        "content": "<comment>你这把 rating 和 ADR 都在队伍下沿徘徊，枪线和残局都没顶住。\n队友虽然也没多亮眼，但真正把局势送进垃圾时间的人还是你，对面甚至不用全员发力就能把你这侧当突破口。</comment>",
                    },
                    {
                        "role": "user",
                        "content": "5eplayer Mr_Bip 最近一场比赛战绩：\nMap: 炙热沙城2 \n比赛结果: 失败 \nRating: 1.59  \nADR: 121.05 \nElo变化: 27.71",
                    },
                    {
                        "role": "assistant",
                        "content": "<comment>你这把 rating 和 ADR 断层领跑，数据已经是标准尽力局。\n问题是队友火力集体掉线，对面双核一抬手你这边就只剩你还在回枪，这种局输得像把一个人硬塞进了四个观众里。</comment>",
                    },
                ],
                system_prompt=_LLM_SYSTEM_PROMPT,
            )
            logger.info(llm_resp)
            return self._extract_comment_text(llm_resp.completion_text)
        return None

    async def handle_to_llm_text(
        self,
        match_data: MatchData,
        player_send: str | None,
        platform: str,
    ) -> str:
        player_key = player_send or ""
        player_stats = match_data.player_stats.get(player_key)
        def _format_match_round_text(match_round: int) -> str:
            if match_round <= 1:
                return "最近一把"
            return f"上{match_round}把"

        text = ""
        if player_stats:
            if player_stats.win == 1:
                match_result = "胜利"
                elo_sign = "+"
            else:
                match_result = "失败"
                elo_sign = "-"
            match_type_text = ""
            if platform in ("pw", "mm"):
                match_type_text = f"比赛类型: {match_data.match_type or '未知'}\n"
            text = (
                f"{platform}player {player_stats.playername} 的{_format_match_round_text(match_data.match_round)}比赛战绩:\n"
                f"{match_type_text}"
                f"比赛时间: {match_data.start_datetime}   比赛时长: {match_data.duration}min\n"
                f"Map: {match_data.map} 比赛结果: {match_result} \n"
                f"Elo变化: {elo_sign}{abs(player_stats.elo_change)}\n"
                f"kd: {player_stats.kill}-{player_stats.death}\n"
                f"rating: {player_stats.rating}\n"
                f"adr: {player_stats.adr}\n"
                f"爆头率: {player_stats.headshot_rate * 100:.2f}% "
            )

        if not text:
            match_data.error_msg = "生成评价战绩错误"
        return text

    async def build_llm_evaluation_input(
        self,
        match_data: MatchData,
        player_send: str | None,
        public_text: str,
    ) -> str:
        player_key = player_send or ""
        player_stats = match_data.player_stats.get(player_key)
        if not player_stats:
            return public_text

        teammate_lines = []
        for teammate in match_data.teammate_players:
            teammate_lines.append(
                f"- {teammate.playername}: rating {teammate.rating}, kd {teammate.kill}-{teammate.death}, adr {teammate.adr}, rws {teammate.rws}"
            )

        opponent_lines = []
        for opponent in match_data.opponent_players:
            opponent_lines.append(
                f"- {opponent.playername}: rating {opponent.rating}, kd {opponent.kill}-{opponent.death}, adr {opponent.adr}, rws {opponent.rws}"
            )

        teammate_block = "\n".join(teammate_lines) if teammate_lines else "- 无"
        opponent_block = "\n".join(opponent_lines) if opponent_lines else "- 无"

        extra_context = (
            "\n\n[仅供评价使用的对局上下文，不要原样复述]\n"
            "你需要结合以下逐人数据判断是你在拖累大哥、还是你在燃尽带队、还是对手整体太强。\n"
            "队友(不含本人)逐人数据:\n"
            f"{teammate_block}\n"
            "对手逐人数据:\n"
            f"{opponent_block}"
        )

        return public_text + extra_context
