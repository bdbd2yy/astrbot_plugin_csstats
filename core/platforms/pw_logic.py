from datetime import datetime

import aiohttp
from tenacity import retry, stop_after_attempt, wait_fixed

from astrbot.api import logger

from ...models.match_data import MatchData, PlayerStats
from ...models.player_data import PlayerDataRequest


class PerfectWorldPlatformLogic:
    def _common_headers(self) -> dict:
        return {
            "Content-Type": "application/json;charset=UTF-8",
            "User-Agent": "okhttp/4.11.0",
            "appversion": "3.7.9.203",
            "device": "rGPSR1772436611LrL5aF8eKG3",
            "token": "8e1233748353756e1d84a321753a98d599a9de48",
        }

    @retry(stop=stop_after_attempt(3), wait=wait_fixed(1))
    async def get_wanmeiid_and_steamid(
        self, session: aiohttp.ClientSession, request_data: PlayerDataRequest
    ):
        url = "https://gwapi.pwesports.cn/acty/api/v1/search"
        payload = {
            "text": request_data.player_name,
            "searchType": "USER",
            "circleId": "0",
            "page": 1,
            "pageSize": 20,
            "gameTypeStr": "1,2",
            "platform": "android",
            "sortType": 1,
        }
        timeout = aiohttp.ClientTimeout(total=15)
        async with session.post(
            url, json=payload, headers=self._common_headers(), timeout=timeout
        ) as resp:
            try:
                resp.raise_for_status()
            except Exception as exc:
                logger.error(f"完美搜索玩家失败：HTTP {resp.status}，错误：{exc}")
                request_data.error_msg = "完美平台绑定失败，请检查网络后重试"
                return
            data = await resp.json()
            result = data.get("result", [])
            users = []
            for item in result:
                if item.get("itemType") == "USER":
                    users.extend(item.get("data", []))
            if not users:
                request_data.error_msg = "完美平台绑定失败，未找到该玩家"
                return

            target = None
            target_name = (request_data.player_name or "").strip().lower()
            for user in users:
                if str(user.get("name", "")).strip().lower() == target_name:
                    target = user
                    break
            if target is None:
                target = users[0]

            request_data.domain = str(target.get("wanmeiId") or "")
            request_data.uuid = str(
                target.get("steamId64Str") or target.get("steamId64") or ""
            )
            request_data.player_name = str(
                target.get("name") or request_data.player_name
            )
            if not request_data.uuid:
                request_data.error_msg = "完美平台绑定失败，未获取到 SteamId"

    @retry(stop=stop_after_attempt(3), wait=wait_fixed(1))
    async def get_domain(
        self, session: aiohttp.ClientSession, request_data: PlayerDataRequest
    ):
        if request_data.domain and request_data.uuid:
            return
        await self.get_wanmeiid_and_steamid(session, request_data)

    @retry(stop=stop_after_attempt(3), wait=wait_fixed(1))
    async def get_uuid(
        self, session: aiohttp.ClientSession, request_data: PlayerDataRequest
    ):
        if request_data.uuid:
            return
        await self.get_wanmeiid_and_steamid(session, request_data)

    async def get_match_id(
        self,
        session: aiohttp.ClientSession,
        request_data: PlayerDataRequest,
        match_round: int,
    ):
        _ = match_round
        if not request_data.uuid:
            request_data.error_msg = "完美平台查询失败，绑定信息缺少 SteamId"
            return None
        url = "https://api.wmpvp.com/api/csgo/home/match/list"
        try:
            steam_id = int(request_data.uuid or "0")
        except ValueError:
            steam_id = 0
        if steam_id <= 0:
            request_data.error_msg = "完美平台查询失败，SteamId 无效"
            return None
        payload = {
            "toSteamId": steam_id,
            "mySteamId": 0,
            "csgoSeasonId": "recent",
            "pvpType": -1,
            "page": 1,
            "pageSize": 11,
            "dataSource": 3,
        }
        logger.info(f"payload: {payload}")
        timeout = aiohttp.ClientTimeout(total=15)
        async with session.post(
            url, json=payload, headers=self._common_headers(), timeout=timeout
        ) as resp:
            try:
                resp.raise_for_status()
            except Exception as exc:
                logger.error(f"完美拉取战绩失败：HTTP {resp.status}，错误：{exc}")
                request_data.error_msg = "完美平台查询失败，请检查网络后重试"
                return None
            data = await resp.json()
            if data.get("statusCode") != 0:
                request_data.error_msg = (
                    data.get("errorMessage") or "完美平台查询失败，请稍后重试"
                )
                return None
            match_list = data.get("data", {}).get("matchList", [])
            if not match_list or match_round <= 0 or match_round > len(match_list):
                request_data.error_msg = f"获取玩家 {request_data.player_name} 的{match_round * '上'}比赛的数据失败，请稍后重试"
                return None
            match_id = match_list[match_round - 1].get("matchId", "")
        return match_id

    @retry(stop=stop_after_attempt(3), wait=wait_fixed(1))
    async def get_match_stats(
        self,
        session: aiohttp.ClientSession,
        match_id: str,
        request_data: PlayerDataRequest,
    ):
        _ = match_id
        url = "https://api.wmpvp.com/api/v1/csgo/match"
        try:
            steam_id = int(request_data.uuid or "0")
        except ValueError:
            steam_id = 0
        if steam_id <= 0:
            request_data.error_msg = "完美平台查询失败，SteamId 无效"
            return None
        payload = {"matchId": match_id, "platform": "admin", "dataSource": "3"}
        timeout = aiohttp.ClientTimeout(total=15)
        async with session.post(
            url, json=payload, headers=self._common_headers(), timeout=timeout
        ) as resp:
            try:
                resp.raise_for_status()
            except Exception as exc:
                logger.error(f"完美拉取战绩失败：HTTP {resp.status}，错误：{exc}")
                request_data.error_msg = "完美平台查询失败，请检查网络后重试"
                return None
            data = await resp.json()
            if data.get("statusCode") != 0:
                request_data.error_msg = (
                    data.get("errorMessage") or "完美平台查询失败，请稍后重试"
                )
                return None
            return data.get("data", {})

    @staticmethod
    def _parse_score(value) -> int:
        try:
            return int(float(value or 0))
        except (TypeError, ValueError):
            return 0

    async def process_json(
        self,
        json_data,
        match_round: int,
        player_send: str,
        player_uuid: str | None = None,
    ) -> MatchData:
        base_info = json_data.get("base") or {}
        start_time = self._parse_time_to_timestamp(base_info.get("startTime"))
        if start_time <= 0:
            start_time = int(datetime.now().timestamp())
        end_time = self._parse_time_to_timestamp(base_info.get("endTime"))
        if end_time <= 0:
            duration_minutes = int(base_info.get("duration") or 30)
            end_time = start_time + max(duration_minutes, 1) * 60

        map_name = base_info.get("map") or base_info.get("mapEn") or "未知地图"
        team_a_score = self._parse_score(
            base_info.get("team1Score")
            or base_info.get("score1")
            or base_info.get("aScore")
            or base_info.get("teamAScore")
        )
        team_b_score = self._parse_score(
            base_info.get("team2Score")
            or base_info.get("score2")
            or base_info.get("bScore")
            or base_info.get("teamBScore")
        )
        match_data = MatchData(
            match_round=match_round,
            map=map_name,
            start_time=start_time,
            end_time=end_time,
            player_stats={},
            teammate_players=[],
            opponent_players=[],
            mvp_uid="",
            error_msg=None,
            match_type=str(
                base_info.get("mode")
                or base_info.get("mode2")
                or base_info.get("matchType")
                or ""
            ),
            team_a_score=team_a_score,
            team_b_score=team_b_score,
            player_team="A",
        )

        players = json_data.get("players") or []
        target_player = self._resolve_player(players, player_uuid, player_send or "")
        if target_player is None:
            match_data.error_msg = f"未在比赛数据中找到玩家 {player_send}"
            return match_data

        target_team = self._resolve_player_team(target_player, base_info)
        if target_team <= 0:
            match_data.error_msg = f"未在比赛数据中识别玩家 {player_send} 的队伍"
            return match_data
        match_data.player_team = "A" if target_team == 1 else "B"
        mvp_player = next((item for item in players if bool(item.get("mvp"))), None)
        if mvp_player is not None:
            match_data.mvp_uid = str(mvp_player.get("playerId") or "")

        target_name = str(target_player.get("nickName") or player_send)
        target_stats = self._extract_player_data(target_player, target_name, base_info)
        match_data.player_stats[target_name] = target_stats
        if target_name != player_send:
            match_data.player_stats[player_send] = target_stats

        for player_raw in players:
            player_name = str(
                player_raw.get("nickName") or player_raw.get("playerId") or ""
            )
            if player_name == target_name:
                continue
            pdata = self._extract_player_data(player_raw, player_name, base_info)
            player_team = self._resolve_player_team(player_raw, base_info)
            if player_team <= 0:
                continue
            if player_team == target_team:
                match_data.teammate_players.append(pdata)
            else:
                match_data.opponent_players.append(pdata)

        return match_data

    @staticmethod
    def _resolve_player(players, player_uuid: str | None, player_name: str):
        if player_uuid:
            player = PerfectWorldPlatformLogic._resolve_player_by_uuid(
                players, player_uuid
            )
            if player is not None:
                return player
        return PerfectWorldPlatformLogic._resolve_player_by_name(players, player_name)

    @staticmethod
    def _resolve_player_by_uuid(players, player_uuid: str):
        target_uuid = str(player_uuid or "").strip()
        if not target_uuid:
            return None
        for player in players:
            if str(player.get("playerId") or "").strip() == target_uuid:
                return player
        return None

    @staticmethod
    def _parse_time_to_timestamp(time_str) -> int:
        if not time_str:
            return 0
        try:
            return int(
                datetime.strptime(str(time_str), "%Y-%m-%d %H:%M:%S").timestamp()
            )
        except ValueError:
            return 0

    @staticmethod
    def _resolve_player_by_name(players, player_name):
        target_name = str(player_name or "").strip()
        if not target_name:
            return None
        target_lower = target_name.lower()
        for player in players:
            if str(player.get("nickName") or "").strip().lower() == target_lower:
                return player
        return None

    @staticmethod
    def _extract_player_data(player_raw, player_name: str, base_info) -> PlayerStats:
        team = PerfectWorldPlatformLogic._resolve_player_team(player_raw, base_info)
        win_team = int(base_info.get("winTeam") or 0)
        kill = int(player_raw.get("kill") or 0)
        headshot_ratio = float(player_raw.get("headShotRatio") or 0.0)
        if headshot_ratio > 1:
            headshot_ratio = headshot_ratio / 100
        return PlayerStats(
            playername=player_name,
            uuid=str(player_raw.get("playerId") or ""),
            uid="",
            win=1 if team == win_team and win_team > 0 else 0,
            elo_change=float(player_raw.get("pvpScoreChange") or 0.0),
            rating=float(player_raw.get("pwRating") or player_raw.get("rating") or 0.0),
            adr=float(player_raw.get("adpr") or 0.0),
            rws=float(player_raw.get("rws") or 0.0),
            kill=kill,
            death=int(player_raw.get("death") or 0),
            headshot_rate=headshot_ratio,
        )

    @staticmethod
    def _resolve_player_team(player_raw, base_info) -> int:
        team = int(player_raw.get("team") or 0)
        if team in (1, 2):
            return team

        player_id = str(player_raw.get("playerId") or "")
        team_1_ids = {
            item.strip()
            for item in str(base_info.get("team1Info") or "").split(",")
            if item
        }
        team_2_ids = {
            item.strip()
            for item in str(base_info.get("team2Info") or "").split(",")
            if item
        }
        if player_id in team_1_ids:
            return 1
        if player_id in team_2_ids:
            return 2
        return 0

    @staticmethod
    def _worst_player_key(player_stats: PlayerStats) -> tuple:
        return (
            player_stats.rating,
            player_stats.adr,
            player_stats.kill - player_stats.death,
            player_stats.kill,
        )

    async def get_premade_summary(
        self,
        user_data,
        json_data,
        player_send,
        player_uuid: str | None = None,
    ):
        default_result = {
            "teammate_names": [],
            "worst_player_qq": None,
            "worst_player_name": "",
            "target_is_worst": False,
        }
        request_player_uuid = str(player_uuid or "").strip()
        uuid_to_bound_player = {}
        for qq_id, player_info in user_data.items():
            platform_data = player_info.get("platform_data", {}).get("pw")
            if platform_data:
                bound_player_uuid = str(platform_data.get("uuid") or "")
                player_name = str(platform_data.get("name") or "")
            else:
                legacy_platform = str(player_info.get("platform") or "").lower()
                if legacy_platform != "pw":
                    continue
                bound_player_uuid = str(player_info.get("uuid") or "")
                player_name = str(player_info.get("name") or "")

            if bound_player_uuid:
                uuid_to_bound_player[bound_player_uuid] = {
                    "qq_id": qq_id,
                    "name": player_name,
                }

        if not uuid_to_bound_player:
            return default_result

        base_info = json_data.get("base") or {}
        players = json_data.get("players") or []
        target_player = self._resolve_player(
            players, request_player_uuid, player_send or ""
        )
        if target_player is None:
            return default_result

        target_uuid = str(target_player.get("playerId") or "")
        target_name = (
            str(target_player.get("nickName") or player_send or "").strip().lower()
        )
        target_team = self._resolve_player_team(target_player, base_info)
        if target_team <= 0:
            return default_result

        bound_team_players = []
        for player_raw in players:
            if self._resolve_player_team(player_raw, base_info) != target_team:
                continue

            current_player_uuid = str(player_raw.get("playerId") or "")
            if (
                not current_player_uuid
                or current_player_uuid not in uuid_to_bound_player
            ):
                continue

            player_name = str(player_raw.get("nickName") or current_player_uuid)
            bound_info = uuid_to_bound_player[current_player_uuid]
            is_target_player = bool(
                target_uuid and current_player_uuid == target_uuid
            ) or (not target_uuid and player_name.strip().lower() == target_name)
            bound_team_players.append(
                {
                    "name": player_name,
                    "qq_id": bound_info.get("qq_id", ""),
                    "is_target": is_target_player,
                    "stats": self._extract_player_data(
                        player_raw, player_name, base_info
                    ),
                }
            )

        teammate_names = [
            player["name"] for player in bound_team_players if not player["is_target"]
        ]
        if not teammate_names:
            return default_result

        worst_player = min(
            bound_team_players,
            key=lambda player: self._worst_player_key(player["stats"]),
        )
        target_is_worst = bool(worst_player["is_target"])
        worst_player_qq = None if target_is_worst else worst_player["qq_id"]
        return {
            "teammate_names": teammate_names,
            "worst_player_qq": worst_player_qq,
            "worst_player_name": worst_player["name"],
            "target_is_worst": target_is_worst,
        }
