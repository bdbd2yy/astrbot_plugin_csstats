from dataclasses import dataclass
from datetime import datetime


@dataclass
class PlayerStats:
    playername: str
    uuid: str
    uid: str  # match的json中才有的值
    win: int
    elo_change: float  # 天梯分数变化
    rating: float
    adr: float
    rws: float
    kill: int
    death: int
    headshot_rate: float  # 爆头率


@dataclass
class MatchData:
    match_round: int
    map: str
    start_time: int  # Unix 时间戳 timestamp
    end_time: int
    player_stats: dict[str, PlayerStats]
    teammate_players: list[PlayerStats]
    opponent_players: list[PlayerStats]
    mvp_uid: str
    error_msg: str | None = None
    match_type: str = ""
    team_a_score: int = 0
    team_b_score: int = 0
    player_team: str = "A"

    @property
    def start_datetime(self):
        """
        ts = 1761376186
        dt = datetime.fromtimestamp(ts)
        print(dt)
        2025-10-25 15:09:46
        """
        return datetime.fromtimestamp(self.start_time)

    @property
    def end_datetime(self):
        return datetime.fromtimestamp(self.end_time)

    @property
    def duration(self):
        "返回本局比赛时长(分钟)"
        return (self.end_time - self.start_time) // 60
