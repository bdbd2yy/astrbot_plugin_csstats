import base64
import binascii
import logging
import os
import tempfile
import uuid
from io import BytesIO
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from PIL import Image as PILImage
from PIL import ImageDraw, ImageFont

logger = logging.getLogger("astrbot")


class MatchReportGenerator:
    def __init__(self, template_dir: str | Path | None = None):
        base_dir = Path(template_dir or (Path(__file__).parent / "templates"))
        self.template_dir = base_dir
        self._shared_output_dir = self._resolve_output_dir()
        self._font_path = self._resolve_font_path()
        self._env = Environment(
            loader=FileSystemLoader(os.fspath(base_dir)),
            autoescape=select_autoescape(["html", "xml"]),
            trim_blocks=True,
            lstrip_blocks=True,
        )

    @staticmethod
    def _resolve_output_dir() -> Path | None:
        for candidate in (
            Path("/AstrBot/data/temp"),
            Path("/AstrBot/data/cache"),
        ):
            try:
                candidate.mkdir(parents=True, exist_ok=True)
                return candidate
            except Exception:
                continue
        return None

    @staticmethod
    def _resolve_font_path() -> str | None:
        for candidate in (
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
            "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
            "C:/Windows/Fonts/msyh.ttc",
            "C:/Windows/Fonts/msyhbd.ttc",
            "C:/Windows/Fonts/simhei.ttf",
        ):
            if os.path.exists(candidate):
                return candidate
        return None

    def _get_font(self, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        if self._font_path:
            try:
                return ImageFont.truetype(self._font_path, size=size)
            except Exception:
                pass
        return ImageFont.load_default()

    @staticmethod
    def _truncate_text(draw, text: str, font, max_width: int) -> str:
        text = str(text or "")
        if not text:
            return ""
        if draw.textbbox((0, 0), text, font=font)[2] <= max_width:
            return text
        ellipsis = "..."
        low = 0
        high = len(text)
        best = ellipsis
        while low <= high:
            mid = (low + high) // 2
            candidate = text[:mid].rstrip() + ellipsis
            if draw.textbbox((0, 0), candidate, font=font)[2] <= max_width:
                best = candidate
                low = mid + 1
            else:
                high = mid - 1
        return best

    @staticmethod
    def _line_height(draw, font) -> int:
        bbox = draw.textbbox((0, 0), "Ag测试", font=font)
        return bbox[3] - bbox[1] + 8

    def _wrap_text(self, draw, text: str, font, max_width: int) -> list[str]:
        if not text:
            return []
        lines: list[str] = []
        for paragraph in str(text).splitlines():
            paragraph = paragraph.strip()
            if not paragraph:
                if lines and lines[-1] != "":
                    lines.append("")
                continue
            current = ""
            for char in paragraph:
                candidate = current + char
                if not current or draw.textbbox((0, 0), candidate, font=font)[2] <= max_width:
                    current = candidate
                    continue
                lines.append(current)
                current = char
            if current:
                lines.append(current)
        return lines or [""]

    def render_html(self, report_payload: dict) -> str:
        template = self._env.get_template("match_report.html")
        return template.render(**report_payload)

    def _write_temp_image(self, image_bytes: bytes, suffix: str = ".png") -> str | None:
        if not image_bytes:
            return None
        if self._shared_output_dir is not None:
            temp_path = self._shared_output_dir / f"csstats_report_{uuid.uuid4().hex}{suffix}"
            temp_path.write_bytes(image_bytes)
            return os.fspath(temp_path)
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
            temp_file.write(image_bytes)
            return temp_file.name

    @staticmethod
    def _preview_invalid_bytes(image_bytes: bytes, limit: int = 160) -> str:
        preview = image_bytes[:limit]
        try:
            text = preview.decode("utf-8", errors="ignore").strip()
        except Exception:
            text = ""
        if text:
            return text.replace("\n", " ")
        return preview.hex()

    def _normalize_image_bytes(self, image_bytes: bytes, source: str = "bytes") -> bytes | None:
        if not image_bytes:
            return None
        try:
            with PILImage.open(BytesIO(image_bytes)) as image:
                image.load()
                if image.mode not in ("RGB", "RGBA", "L"):
                    image = image.convert("RGBA")
                elif image.mode == "L":
                    image = image.convert("RGBA")
                output = BytesIO()
                image.save(output, format="PNG", optimize=True)
                normalized_bytes = output.getvalue()
                logger.info(
                    "csstats report image normalized from %s: format=%s size=%sx%s bytes=%s",
                    source,
                    image.format,
                    image.size[0],
                    image.size[1],
                    len(normalized_bytes),
                )
                return normalized_bytes
        except Exception as exc:
            logger.warning(
                "csstats report image is invalid from %s: %s; preview=%s",
                source,
                exc,
                self._preview_invalid_bytes(image_bytes),
            )
            return None

    def _normalize_image_path(self, file_path: str) -> str | None:
        if not file_path or not os.path.exists(file_path):
            return None
        try:
            with open(file_path, "rb") as file:
                image_bytes = file.read()
        except Exception as exc:
            logger.warning(f"读取战绩图片文件失败: {file_path}, error={exc}")
            return None
        normalized_bytes = self._normalize_image_bytes(image_bytes, source=file_path)
        if not normalized_bytes:
            return None
        return self._write_temp_image(normalized_bytes, ".png")

    def _materialize_render_result(self, image_data, render_type: str) -> str | None:
        if isinstance(image_data, bytes):
            normalized_bytes = self._normalize_image_bytes(
                image_data,
                source=f"render:{render_type}:bytes",
            )
            if normalized_bytes:
                return self._write_temp_image(normalized_bytes, ".png")
            return None

        if isinstance(image_data, str):
            if image_data.startswith("base64://"):
                try:
                    raw_bytes = base64.b64decode(image_data.removeprefix("base64://"))
                except (ValueError, binascii.Error) as exc:
                    logger.warning(
                        f"解析 base64 战绩图片失败: render_type={render_type}, error={exc}"
                    )
                    return None
                normalized_bytes = self._normalize_image_bytes(
                    raw_bytes,
                    source=f"render:{render_type}:base64",
                )
                if normalized_bytes:
                    return self._write_temp_image(normalized_bytes, ".png")
                return None
            if image_data.startswith("file:///"):
                file_path = "/" + image_data.removeprefix("file:///").lstrip("/")
                return self._normalize_image_path(file_path)
            if os.path.exists(image_data):
                return self._normalize_image_path(image_data)
            logger.warning(
                f"战绩图片渲染返回了无法识别的字符串结果: render_type={render_type}, value={image_data[:200]}"
            )
            return None

        logger.warning(
            f"战绩图片渲染返回了不支持的结果类型: render_type={render_type}, type={type(image_data)}"
        )
        return None

    def _render_local_fallback_image(self, report_payload: dict) -> bytes | None:
        try:
            width = 1180
            padding = 24
            gap = 18
            panel_gap = 16
            panel_width = (width - padding * 2 - gap) // 2

            measure_image = PILImage.new("RGB", (width, 200), "#2b313c")
            measure_draw = ImageDraw.Draw(measure_image)
            title_font = self._get_font(30)
            subtitle_font = self._get_font(16)
            panel_title_font = self._get_font(18)
            body_font = self._get_font(16)
            small_font = self._get_font(14)
            row_font = self._get_font(15)

            body_line_height = self._line_height(measure_draw, body_font)
            small_line_height = self._line_height(measure_draw, small_font)
            row_height = self._line_height(measure_draw, row_font) + 2

            comment_lines = self._wrap_text(
                measure_draw,
                report_payload.get("llm_comment", ""),
                body_font,
                width - padding * 2 - 32,
            )
            premade_lines = self._wrap_text(
                measure_draw,
                report_payload.get("premade_text", ""),
                body_font,
                width - padding * 2 - 32,
            )

            table_rows = max(
                len(report_payload.get("teammates", [])),
                len(report_payload.get("opponents", [])),
                1,
            )
            header_height = 98
            overview_height = 150
            table_height = 42 + 34 + table_rows * row_height + 16
            premade_height = 0
            if premade_lines:
                premade_height = 42 + len(premade_lines) * body_line_height + 18
            comment_height = 42 + max(len(comment_lines), 1) * body_line_height + 18
            total_height = (
                padding
                + header_height
                + gap
                + overview_height
                + gap
                + table_height
                + (gap if premade_height else 0)
                + premade_height
                + gap
                + comment_height
                + padding
            )

            image = PILImage.new("RGB", (width, total_height), "#2b313c")
            draw = ImageDraw.Draw(image)

            colors = {
                "card": "#323846",
                "panel": "#2d3440",
                "panel_title": "#39414d",
                "border": "#48515f",
                "title": "#f3f4f6",
                "text": "#d7dde6",
                "muted": "#a7b0be",
                "green": "#6ad14b",
                "red": "#ff6b6b",
                "line": "#434b58",
            }

            def draw_panel(x: int, y: int, w: int, h: int, title: str) -> None:
                draw.rounded_rectangle(
                    (x, y, x + w, y + h),
                    radius=12,
                    fill=colors["panel"],
                    outline=colors["border"],
                    width=1,
                )
                draw.rounded_rectangle(
                    (x, y, x + w, y + 40),
                    radius=12,
                    fill=colors["panel_title"],
                    outline=colors["panel_title"],
                )
                draw.rectangle((x, y + 24, x + w, y + 40), fill=colors["panel_title"])
                draw.text((x + 14, y + 10), title, fill=colors["title"], font=panel_title_font)

            y = padding
            draw.rounded_rectangle(
                (padding, y, width - padding, y + header_height),
                radius=14,
                fill=colors["card"],
                outline=colors["border"],
                width=1,
            )
            title_text = f"{report_payload.get('player_name', '未知玩家')} 的 {report_payload.get('match_round_text', '最近一把')}比赛战绩"
            subtitle_text = (
                f"平台 {str(report_payload.get('platform', '')).upper()} · 地图 {report_payload.get('map_name', '未知地图')} · 时间 {report_payload.get('match_time', '--')}"
            )
            draw.text((padding + 24, y + 20), title_text, fill=colors["title"], font=title_font)
            draw.text((padding + 24, y + 60), subtitle_text, fill=colors["muted"], font=subtitle_font)
            y += header_height + gap

            draw_panel(padding, y, panel_width, overview_height, "Overview")
            draw_panel(padding + panel_width + gap, y, panel_width, overview_height, "Match Info")

            metric_label = "WE" if report_payload.get("platform") in ("pw", "mm") else "RWS"
            overview_lines = [
                f"Rating  {report_payload.get('rating', '--')}    KD  {report_payload.get('kd_text', '--')}    ADR  {report_payload.get('adr', '--')}    {metric_label}  {report_payload.get('rws', '--')}",
                f"Result  {report_payload.get('match_result', '--')}    Elo  {report_payload.get('elo_change_text', '--')}    HS%  {report_payload.get('headshot_rate_text', '--')}    Duration  {report_payload.get('duration_minutes', '--')}m",
                report_payload.get("stats_text", ""),
            ]
            line_y = y + 54
            for idx, line in enumerate(overview_lines):
                font = body_font if idx < 2 else small_font
                color = colors["text"] if idx < 2 else colors["muted"]
                max_width = panel_width - 28
                if idx == 2:
                    wrapped = self._wrap_text(measure_draw, line, font, max_width)
                    for sub_line in wrapped[:2]:
                        draw.text((padding + 14, line_y), sub_line, fill=color, font=font)
                        line_y += small_line_height
                else:
                    draw.text((padding + 14, line_y), line, fill=color, font=font)
                    line_y += body_line_height

            info_x = padding + panel_width + gap + 14
            info_y = y + 54
            info_lines = [
                f"Map: {report_payload.get('map_name', '--')}",
                f"Platform: {str(report_payload.get('platform', '--')).upper()}",
                f"Type: {report_payload.get('match_type', '--')}",
                f"Time: {report_payload.get('match_time', '--')}",
            ]
            for line in info_lines:
                wrapped = self._wrap_text(measure_draw, line, body_font, panel_width - 28)
                for sub_line in wrapped[:2]:
                    draw.text((info_x, info_y), sub_line, fill=colors["text"], font=body_font)
                    info_y += body_line_height

            y += overview_height + gap

            left_x = padding
            right_x = padding + panel_width + gap
            draw_panel(left_x, y, panel_width, table_height, "队伍A")
            draw_panel(right_x, y, panel_width, table_height, "队伍B")

            def draw_team_table(x: int, players: list[dict], include_self: bool) -> None:
                header_y = y + 48
                player_x = x + 14
                rating_x = x + panel_width - 260
                kd_x = x + panel_width - 185
                adr_x = x + panel_width - 120
                rws_x = x + panel_width - 55
                draw.text((player_x, header_y), "Player", fill=colors["muted"], font=small_font)
                draw.text((rating_x, header_y), "Rating", fill=colors["muted"], font=small_font)
                draw.text((kd_x, header_y), "K-D", fill=colors["muted"], font=small_font)
                draw.text((adr_x, header_y), "ADR", fill=colors["muted"], font=small_font)
                draw.text((rws_x, header_y), metric_label, fill=colors["muted"], font=small_font, anchor="ra")
                draw.line((x + 14, header_y + 24, x + panel_width - 14, header_y + 24), fill=colors["line"], width=1)

                row_y = header_y + 34
                for player in players:
                    name = str(player.get("playername", ""))
                    if include_self and player.get("is_self"):
                        name += " 你"
                    name = self._truncate_text(draw, name, row_font, rating_x - player_x - 10)
                    rating_text = str(player.get("rating", "--"))
                    rating_color = colors["text"]
                    try:
                        rating_value = float(player.get("rating", 0))
                        if rating_value >= 1.05:
                            rating_color = colors["green"]
                        elif rating_value < 0.95:
                            rating_color = colors["red"]
                    except Exception:
                        pass
                    draw.text((player_x, row_y), name, fill=colors["text"], font=row_font)
                    draw.text((rating_x, row_y), rating_text, fill=rating_color, font=row_font)
                    draw.text((kd_x, row_y), str(player.get("kd", "--")), fill=colors["text"], font=row_font)
                    draw.text((adr_x, row_y), str(player.get("adr", "--")), fill=colors["text"], font=row_font)
                    draw.text((rws_x, row_y), str(player.get("rws", "--")), fill=colors["text"], font=row_font, anchor="ra")
                    draw.line((x + 14, row_y + row_height - 4, x + panel_width - 14, row_y + row_height - 4), fill=colors["line"], width=1)
                    row_y += row_height

            draw_team_table(left_x, report_payload.get("teammates", []), True)
            draw_team_table(right_x, report_payload.get("opponents", []), False)
            y += table_height

            if premade_lines:
                y += gap
                draw_panel(padding, y, width - padding * 2, premade_height, "Premade")
                text_y = y + 52
                for line in premade_lines:
                    draw.text((padding + 16, text_y), line, fill=colors["text"], font=body_font)
                    text_y += body_line_height
                y += premade_height

            y += gap
            draw_panel(padding, y, width - padding * 2, comment_height, "LLM Comment")
            text_y = y + 52
            for line in (comment_lines or ["评价生成失败"]):
                draw.text((padding + 16, text_y), line, fill=colors["text"], font=body_font)
                text_y += body_line_height

            output = BytesIO()
            image.save(output, format="PNG", optimize=True)
            return output.getvalue()
        except Exception as exc:
            logger.warning(f"本地兜底生成战绩图片失败: {exc}")
            return None

    async def generate_image(self, report_payload: dict, html_render_func) -> str | None:
        html_content = self.render_html(report_payload)
        render_options_list = [
            {
                "full_page": True,
                "type": "png",
                "quality": 95,
                "scale": "device",
                "device_scale_factor_level": "normal",
            },
            {
                "full_page": True,
                "type": "jpeg",
                "quality": 85,
                "scale": "device",
                "device_scale_factor_level": "normal",
            },
        ]

        for render_options in render_options_list:
            render_type = str(render_options.get("type") or "unknown")
            try:
                image_data = await html_render_func(
                    html_content,
                    {},
                    False,
                    render_options,
                )
            except Exception as exc:
                logger.warning(
                    f"战绩图片渲染失败: render_type={render_type}, error={exc}"
                )
                continue

            image_path = self._materialize_render_result(image_data, render_type)
            if image_path:
                logger.info(
                    f"战绩图片生成成功: render_type={render_type}, path={image_path}"
                )
                return image_path

        logger.warning(
            "战绩图片生成失败：HTML 渲染接口未返回有效图片，且已禁用低保真本地兜底渲染。请检查 AstrBot 的 t2i_endpoint 或本地 HTML 截图能力。"
        )
        return None
