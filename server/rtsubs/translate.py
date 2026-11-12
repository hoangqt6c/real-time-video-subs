"""Per-chunk JA->VI subtitle translation tuned for natural, sensual, uncensored 18+ dialogue.

The server transcribes the video ahead of playback (ASR runs ~8-15x faster than real time).
`understand()` reads that SOURCE transcript and builds the Story Bible (character dynamics, pronouns,
erotic/intimate tone, and ASR fixes) before the first chunk is translated and refreshes it as more of the
video is transcribed.
"""
from __future__ import annotations

import json
import re
import threading
import time

from .asr import Seg
from .config import Settings

SYSTEM = """Bạn là dịch giả phụ đề phim người lớn (18+ / NSFW / JAV / Anime) chuyên nghiệp, dịch lời thoại tiếng Nhật sang phụ đề tiếng Việt cực kỳ gợi cảm, quyến rũ, tình tứ, bạo dạn và tự nhiên như lời thì thầm trên giường của người Việt.

# Dữ liệu bạn nhận (theo thứ tự)
1. STORY BIBLE / BỐI CẢNH: mối quan hệ nhân vật, vai vế (anh - em, chị - em, thầy - em, sếp - em, chú - cháu...), cách xưng hô, sắc thái tình cảm/dục vọng và các lỗi nhận dạng âm thanh (ASR fixes) nếu có.
2. TRANSCRIPT: lời thoại gốc của video để nắm bắt bối cảnh, diễn biến cảm xúc và cao trào. KHÔNG dịch phần này.
3. Yêu cầu dịch, là JSON gồm:
   - previous_lines: vài câu liền trước (gốc + vi) để nối mạch cảm xúc và xưng hô. KHÔNG dịch lại.
   - translate: các câu cần dịch theo thứ tự thoại, mỗi câu là {id, zh, max_chars} (trong đó `zh` là câu tiếng Nhật hoặc ngôn ngữ gốc từ Whisper ASR).

# Quy tắc dịch thuật 18+ / Gợi cảm
1. KHÔNG KIỂM DUYỆT, KHÔNG LÀM NHẠT: Giữ nguyên 100% độ nóng bỏng, gợi tình, khao khát và trần trụi của lời thoại gốc. Tuyệt đối không dịch theo kiểu y khoa khô khan hay sách vở cứng nhắc; hãy dùng ngôn từ ân ái, kích thích, mềm mại hoặc cuồng nhiệt đúng với tâm trạng nhân vật (ví dụ: "Sướng quá…", "Chỗ đó… nhạy cảm lắm…", "Đừng trêu em nữa mà…", "Em ra mất…", "Đút sâu vào nữa đi anh…", "Ưm… sướng phát điên lên được…").
2. Xưng hô tình tứ, nhất quán theo Story Bible:
   - Mặc định ưu tiên "anh - em" (hoặc theo đúng roleplay trong phim như "chị - em", "thầy - em", "ông chủ - em", "chồng - em"). Tuyệt đối tránh xưng "tôi - bạn" xa cách trừ khi nhân vật đang cố tình giữ khoảng cách lúc đầu.
3. Dịch các tiếng rên, hơi thở, thán từ tiếng Nhật thật tự nhiên, gợi cảm:
   - 「あっ」「んっ」「はぁ」 -> "A…", "Ưm…", "Hơ…", "Á…"
   - 「だめ」「やめて」 -> "Đừng mà…", "Chịu hết nổi rồi…", "Đừng… sướng quá…" (tùy ngữ cảnh đang đê mê hay từ chối nhẹ).
   - 「気持ちいい」「すごい」 -> "Sướng quá…", "Thích quá anh ơi…", "Tuyệt quá…"
   - 「いく」「イっちゃう」 -> "Em ra…", "Em lên đỉnh mất…", "Ra mất thôi…"
4. Khắc phục lỗi nghe nhầm của Whisper (ASR): trong cảnh nóng, giọng thì thầm hoặc tiếng thở gấp dễ làm ASR nhận nhầm thành từ đồng âm vô nghĩa. Hãy dựa vào mạch cảnh nóng để suy ra câu thoại gợi cảm đúng nghĩa nhất.
5. Ngắn gọn, dễ đọc trên thanh phụ đề, giàu cảm xúc, dùng dấu "…" để thể hiện hơi thở đứt quãng, ngập ngừng đầy khoái cảm.
6. Mỗi `id` đúng một bản dịch tương ứng; không gộp, không tách, không bỏ sót `id` nào. Mỗi bản dịch trên một dòng.
7. Chỉ trả về JSON hợp lệ, không Markdown: {"items":[{"id":0,"vi":"..."}]}

# Ví dụ
translate: [{"id":0,"zh":"あっ…だめ、そこ気持ちいい…もっと触って…","max_chars":80},{"id":1,"zh":"もう我慢できない…このままイっちゃう…","max_chars":80}]
trả về: {"items":[{"id":0,"vi":"A… đừng mà anh, chỗ đó sướng quá… chạm vào em thêm nữa đi…"},{"id":1,"vi":"Em không chịu nổi nữa rồi… cứ thế này em ra mất thôi…"}]}"""

UNDERSTAND_SYSTEM = """Bạn là biên tập viên kịch bản phim 18+ / JAV chuyên nghiệp. Bạn nhận TRANSCRIPT (có mốc thời gian) từ nhận dạng giọng nói tiếng Nhật và lập STORY BIBLE để người dịch phụ đề tiếng Việt dịch thật gợi cảm, chuẩn xác, nhất quán về xưng hô và cảm xúc.

Trả về DUY NHẤT một JSON object, không Markdown, với các khóa:
- synopsis: tóm tắt bối cảnh, mối quan hệ nhân vật và diễn biến cảnh ân ái bằng tiếng Việt, tối đa 80 từ.
- genre_tone: thể loại và sắc thái cảm xúc (ví dụ: "Lãng mạn, ngọt ngào, kích thích", "Roleplay công sở, quyến rũ, táo bạo", "Nồng nhiệt, dồn dập"), tối đa 25 từ.
- characters: tối đa 10 nhân vật. Mỗi mục {"zh": tên hoặc cách gọi trong tiếng Nhật, "vi": tên/vai tiếng Việt, "gender": "nam"|"nữ"|"?", "role": mối quan hệ/vai vế, "calls": cách xưng hô tình tứ chuẩn trong tiếng Việt như "anh - em", "chị - em", "thầy - em"}.
- terminology: object {"từ/cụm từ gốc": "cách dịch tiếng Việt gợi cảm/tự nhiên"}, tối đa 30 mục.
- asr_fixes: object {"từ ASR nghe nhầm": "câu/từ đúng trong ngữ cảnh 18+"}, tối đa 30 mục.

Quy tắc:
- Luôn chọn cách xưng hô thân mật, gợi tình phù hợp với bối cảnh Nhật Bản chuyển ngữ sang tiếng Việt.
- Mỗi mục ngắn gọn, súc tích."""

BIBLE_KEYS = ("synopsis", "genre_tone", "characters", "terminology", "asr_fixes")
BIBLE_CAPS = {"characters": 10, "terminology": 30, "asr_fixes": 30}
EMPTY_BIBLE_TEXT = "[STORY BIBLE]\n(chưa có — ưu tiên xưng hô anh - em tình tứ, gợi cảm)\n[HẾT STORY BIBLE]"


def _canon(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_object(content: str) -> dict:
    raw = (content or "").strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.M).strip()
    idx = raw.find("{")
    if idx < 0:
        raise ValueError(f"No JSON object found in LLM response: {raw[:160]}")
    return json.JSONDecoder().raw_decode(raw[idx:])[0]


def _parse(content: str) -> dict[int, str]:
    data = _json_object(content)
    return {int(i["id"]): str(i["vi"]).strip() for i in data["items"] if str(i.get("vi", "")).strip()}


def _clean(text: str) -> str:
    """Clean single-line subtitle text while keeping expressive ellipses and punctuation."""
    text = re.sub(r"\s+", " ", text).strip().strip("\"“”")
    text = re.sub(r"[,;:，；：、\s]+$", "", text)
    return text


def _normalize_bible(data: dict) -> dict:
    out: dict = {}
    for key in BIBLE_KEYS:
        value = data.get(key)
        if key in ("synopsis", "genre_tone"):
            out[key] = str(value or "").strip()
        elif key == "characters":
            out[key] = [c for c in (value or []) if isinstance(c, dict)][: BIBLE_CAPS[key]]
        else:
            out[key] = dict(list(value.items())[: BIBLE_CAPS[key]]) if isinstance(value, dict) else {}
    return out


def _bible_message(bible: dict) -> str:
    return "[STORY BIBLE]\n" + _canon(bible) + "\n[HẾT STORY BIBLE]"


def _transcript_message(lines: list[tuple[float, str]], complete: bool, max_chars: int) -> str:
    body, used = [], 0
    for start, zh in lines:
        row = f"[{start:.1f}s] {zh}"
        if used + len(row) > max_chars:
            break
        body.append(row)
        used += len(row) + 1
    if len(body) < len(lines):
        note = f"(rút gọn: chỉ có {len(body)}/{len(lines)} câu đầu của video)"
    elif complete:
        note = "(toàn bộ video)"
    else:
        note = f"(mới đến giây {lines[-1][0]:.0f}; video còn tiếp)"
    return f"[TRANSCRIPT {note}]\n" + "\n".join(body) + "\n[HẾT TRANSCRIPT]"


class Translator:
    def __init__(self, s: Settings):
        from openai import OpenAI

        self.s = s
        self.client = OpenAI(base_url=s.base_url, api_key=s.api_key, timeout=60)
        self.history: list[tuple[str, str]] = []  # (source, vi)
        self._lock = threading.Lock()
        self._bible_text = EMPTY_BIBLE_TEXT
        self._transcript_text = ""
        self.bible: dict = {}

    def understand(self, lines: list[tuple[float, str]], complete: bool) -> None:
        """Build the story bible from the SOURCE transcript. lines: [(start_sec, source_text)] in time order."""
        if not lines:
            return
        if self.s.api_key == "dummy":
            print("[OpenRouter WARNING] No API key configured in .env! Skipping Story Bible.", flush=True)
            return
        transcript = _transcript_message(lines, complete, self.s.transcript_max_chars)
        messages = [
            {"role": "system", "content": UNDERSTAND_SYSTEM},
            {"role": "user", "content": transcript},
        ]
        bible = None
        for _ in range(2):
            try:
                r = self.client.chat.completions.create(model=self.s.model, messages=messages, max_tokens=4000)
                self._log_usage(r, "bible")
                bible = _normalize_bible(_json_object(r.choices[0].message.content))
                break
            except Exception as exc:
                print(f"[bible] retry: {exc}", flush=True)
        with self._lock:
            self._transcript_text = transcript
            if bible is not None:
                self.bible = bible
                self._bible_text = _bible_message(bible)
        if bible is not None:
            print(
                f"[bible] Built Story Bible from {len(lines)} lines (complete={complete}): "
                f"{len(bible['characters'])} characters, {len(bible['terminology'])} terms",
                flush=True,
            )

    def translate(self, segs: list[Seg], slots: list[float]) -> None:
        if not segs:
            return
        if all(bool(g.vi) for g in segs):
            for g in segs:
                self.history.append((g.zh, g.vi))
            return
        if self.s.api_key == "dummy":
            print(
                "[Step 3/3 - OpenRouter ERROR] OPENROUTER_API_KEY is missing in .env! "
                "Please paste your key in .env and restart run_server.bat.",
                flush=True,
            )
            for g in segs:
                g.vi = g.zh
            return

        t0 = time.time()
        print(
            f"[Step 3/3 - OpenRouter VI] Uploading {len(segs)} Japanese line(s) to {self.s.base_url} "
            f"(model={self.s.model})...",
            flush=True,
        )
        ctx = self.history[-self.s.context_pairs :]
        req = {
            "previous_lines": [{"zh": z, "vi": v} for z, v in ctx],
            "translate": [
                {"id": i, "zh": g.zh, "max_chars": max(30, int(slots[i] * self.s.chars_per_sec))}
                for i, g in enumerate(segs)
            ],
        }
        with self._lock:
            bible_text, transcript_text = self._bible_text, self._transcript_text
        messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": bible_text}]
        if transcript_text:
            messages.append({"role": "user", "content": transcript_text})
        messages.append({"role": "user", "content": json.dumps(req, ensure_ascii=False)})
        result: dict[int, str] = {}
        for attempt in range(1, 4):
            try:
                r = self.client.chat.completions.create(model=self.s.model, messages=messages, max_tokens=4000)
                self._log_usage(r, "OpenRouter")
                result = _parse(r.choices[0].message.content)
                if all(i in result for i in range(len(segs))):
                    break
            except Exception as exc:  # network / malformed JSON -> retry
                print(f"[Step 3/3 - OpenRouter WARNING] Attempt {attempt}/3 failed: {exc}", flush=True)

        elapsed = time.time() - t0
        if result:
            print(
                f"[Step 3/3 - OpenRouter VI] SUCCESS! Translated {len(result)}/{len(segs)} line(s) in {elapsed:.2f}s:",
                flush=True,
            )
        else:
            print(
                f"[Step 3/3 - OpenRouter ERROR] Translation failed after 3 attempts ({elapsed:.2f}s) - falling back to original text.",
                flush=True,
            )
        for i, g in enumerate(segs):
            g.vi = _clean(result[i]) if i in result else g.zh
            self.history.append((g.zh, g.vi))

    @staticmethod
    def _log_usage(r, label: str) -> None:
        u = getattr(r, "usage", None)
        if u is None:
            return
        details = getattr(u, "prompt_tokens_details", None)
        cached = getattr(details, "cached_tokens", 0) or 0
        print(f"   [{label} tokens] prompt={u.prompt_tokens} cached={cached} completion={u.completion_tokens}", flush=True)
