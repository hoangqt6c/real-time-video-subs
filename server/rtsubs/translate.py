"""Per-chunk JA->VI subtitle translation tuned for natural, sensual, uncensored 18+ dialogue,
with Male/Female speaker voice tags and page synopsis/summary context.
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
1. BỐI CẢNH TRANG WEB & STORY BIBLE: tiêu đề phim, mã phim (ví dụ MEYD-916), tóm tắt nội dung cốt truyện từ trang web (NTR, hàng xóm, vợ chồng, công sở, chị em, thầy trò...), tên nhân vật, vai vế và cách xưng hô.
2. TRANSCRIPT: lời thoại gốc của video để nắm bắt bối cảnh, diễn biến cảm xúc và cao trào. KHÔNG dịch phần này.
3. Yêu cầu dịch, là JSON gồm:
   - previous_lines: vài câu liền trước (gốc + vi + speaker) để nối mạch cảm xúc và xưng hô. KHÔNG dịch lại.
   - translate: các câu cần dịch theo thứ tự thoại, mỗi câu là {id, speaker, zh, max_chars} trong đó:
     * `speaker`: "Nam" (giọng đàn ông) hoặc "Nữ" (giọng phụ nữ) được phân tích trực tiếp từ tần số âm thanh giọng nói.
     * `zh`: câu tiếng Nhật từ Whisper ASR.

# Quy tắc dịch thuật 18+ & Xưng hô theo Giọng Nam / Giọng Nữ (CỰC KỲ QUAN TRỌNG)
1. PHÂN BIỆT RÕ GIỌNG NAM (`"speaker": "Nam"`) VÀ GIỌNG NỮ (`"speaker": "Nữ"`):
   - Khi `"speaker": "Nữ"` (phụ nữ đang nói/rên):
     * Mặc định xưng **"em"** - gọi người đàn ông là **"anh"** (ví dụ: "A… chỗ đó sướng quá anh ơi…", "Đừng trêu em nữa mà…", "Em ra mất anh ơi…").
     * Nếu TÓM TẮT PHIM cho biết nhân vật nữ lớn tuổi hơn / vai trên (chị gái, đàn chị, sếp nữ, cô giáo, mẹ kế, phụ nữ có chồng quyến rũ trai trẻ) -> xưng **"chị"** (hoặc **"cô"**) và gọi đối phương là **"em"**!
   - Khi `"speaker": "Nam"` (đàn ông đang nói):
     * Mặc định xưng **"anh"** - gọi người phụ nữ là **"em"** (ví dụ: "Em ướt hết rồi này…", "Thả lỏng ra cho anh đút vào nào…", "Sướng không em?").
     * Nếu đàn ông là trai trẻ nói với chị gái/phụ nữ lớn tuổi hơn theo TÓM TẮT PHIM -> xưng **"em"** - gọi **"chị"**. Nếu là sếp/thầy/chú/bố chồng -> xưng đúng vai vế gợi cảm theo cốt truyện.
     * TUYỆT ĐỐI KHÔNG dịch câu của `"speaker": "Nam"` thành lời của phụ nữ (không dịch giọng Nam thành "Em sướng quá anh ơi").
2. BÁM SÁT TÓM TẮT PHIM (VIDEO SUMMARY):
   - Đọc kỹ phần Tóm tắt nội dung phim (ví dụ: ngoại tình NTR với hàng xóm, trao đổi cặp đôi ở phòng thông nhau, chồng xuất tinh sớm, v.v.) để dịch đúng tên nhân vật, đúng hoàn cảnh lén lút/kích thích và đúng tâm lý nhân vật.
3. KHÔNG KIỂM DUYỆT, KHÔNG LÀM NHẠT:
   - Giữ nguyên 100% độ nóng bỏng, gợi tình, khao khát và trần trụi của lời thoại gốc. Tuyệt đối không dịch theo kiểu y khoa khô khan hay sách vở cứng nhắc; hãy dùng ngôn từ ân ái, kích thích, mềm mại hoặc cuồng nhiệt đúng với tâm trạng nhân vật (ví dụ: "Sướng quá…", "Chỗ đó… nhạy cảm lắm…", "Đừng mà… chồng em ở phòng bên cạnh đấy…", "Đút sâu vào nữa đi anh…", "Ưm… sướng phát điên lên được…").
4. Dịch các tiếng rên, hơi thở, thán từ tiếng Nhật thật tự nhiên, gợi cảm theo đúng giới tính `speaker`:
   - 「あっ」「んっ」「はぁ」 -> "A…", "Ưm…", "Hơ…", "Á…"
   - 「だめ」「やめて」 -> "Đừng mà…", "Chịu hết nổi rồi…", "Đừng… sướng quá…"
   - 「気持ちいい」「すごい」 -> (Nữ): "Sướng quá anh ơi…", "Tuyệt quá…" / (Nam): "Sướng thật đấy em à…", "Khít quá em ơi…"
   - 「いく」「イっちゃう」 -> (Nữ): "Em ra…", "Em lên đỉnh mất…" / (Nam): "Anh ra đây…", "Anh bắn vào trong nhé…"
5. Khắc phục lỗi nghe nhầm của Whisper (ASR): trong cảnh nóng, giọng thì thầm hoặc tiếng thở gấp dễ làm ASR nhận nhầm thành từ đồng âm vô nghĩa. Hãy dựa vào TÓM TẮT PHIM, giới tính `speaker` và mạch cảnh nóng để suy ra câu thoại gợi cảm đúng nghĩa nhất.
6. Mỗi `id` đúng một bản dịch tương ứng; không gộp, không tách, không bỏ sót `id` nào. Chỉ trả về JSON hợp lệ, không Markdown: {"items":[{"id":0,"vi":"..."}]}

# Ví dụ
translate: [{"id":0,"speaker":"Nữ","zh":"あっ…だめ、そこ気持ちいい…もっと触って…","max_chars":80},{"id":1,"speaker":"Nam","zh":"ほら、もうこんなに濡れてるじゃないか…","max_chars":80}]
trả về: {"items":[{"id":0,"vi":"A… đừng mà anh, chỗ đó sướng quá… chạm vào em thêm nữa đi…"},{"id":1,"vi":"Nhìn xem, chẳng phải chỗ này của em đã ướt đẫm hết rồi sao…"}]}"""

UNDERSTAND_SYSTEM = """Bạn là biên tập viên kịch bản phim 18+ / JAV chuyên nghiệp. Bạn nhận THÔNG TIN & TÓM TẮT PHIM TỪ TRANG WEB cùng với TRANSCRIPT (có mốc thời gian và nhãn giọng Nam/Nữ) từ nhận dạng giọng nói tiếng Nhật, và lập STORY BIBLE để người dịch phụ đề tiếng Việt dịch thật gợi cảm, chuẩn xác, nhất quán về xưng hô và cảm xúc.

Trả về DUY NHẤT một JSON object, không Markdown, với các khóa:
- synopsis: tóm tắt bối cảnh, mối quan hệ nhân vật (ví dụ: NTR hàng xóm, trao đổi cặp đôi, vợ chồng, chị em, sếp - nhân viên...) và diễn biến cảnh ân ái bằng tiếng Việt, tối đa 100 từ.
- genre_tone: thể loại và sắc thái cảm xúc (ví dụ: "NTR lén lút, kích thích, nồng nhiệt", "Lãng mạn, ngọt ngào", "Roleplay công sở, táo bạo"), tối đa 30 từ.
- characters: tối đa 10 nhân vật. Mỗi mục {"zh": tên hoặc cách gọi trong tiếng Nhật, "vi": tên/vai tiếng Việt, "gender": "nam"|"nữ", "role": mối quan hệ/vai vế, "calls": cách xưng hô tình tứ chuẩn trong tiếng Việt như "anh - em", "chị - em", "thầy - em"}.
- terminology: object {"từ/cụm từ gốc": "cách dịch tiếng Việt gợi cảm/tự nhiên"}, tối đa 30 mục.
- asr_fixes: object {"từ ASR nghe nhầm": "câu/từ đúng trong ngữ cảnh 18+"}, tối đa 30 mục.

Quy tắc:
- Kết hợp thông tin từ TÓM TẮT TRANG WEB (tên nhân vật như Yuito, Nono, bối cảnh phòng thông nhau, NTR...) với lời thoại trong TRANSCRIPT.
- Luôn chọn cách xưng hô thân mật, gợi tình phù hợp với vai vế Nam/Nữ của từng nhân vật."""

BIBLE_KEYS = ("synopsis", "genre_tone", "characters", "terminology", "asr_fixes")
BIBLE_CAPS = {"characters": 10, "terminology": 30, "asr_fixes": 30}
EMPTY_BIBLE_TEXT = "[STORY BIBLE]\n(chưa có — ưu tiên xưng hô theo đúng giới tính giọng nói Nam='anh', Nữ='em' tình tứ, gợi cảm)\n[HẾT STORY BIBLE]"


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


def _bible_message(bible: dict, page_context_block: str = "") -> str:
    parts = []
    if page_context_block:
        parts.append(page_context_block)
    parts.append("[STORY BIBLE]\n" + _canon(bible) + "\n[HẾT STORY BIBLE]")
    return "\n\n".join(parts)


def _transcript_message(lines: list[tuple[float, str, str]], complete: bool, max_chars: int) -> str:
    body, used = [], 0
    for item in lines:
        start = item[0]
        zh = item[1]
        gender = item[2] if len(item) > 2 else "unknown"
        g_tag = "Nam" if gender == "male" else ("Nữ" if gender == "female" else "?")
        row = f"[{start:.1f}s][{g_tag}] {zh}"
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
    def __init__(
        self,
        s: Settings,
        video_code: str = "",
        page_title: str = "",
        page_summary: str = "",
        initial_bible: dict | None = None,
    ):
        from openai import OpenAI

        self.s = s
        self.client = OpenAI(base_url=s.base_url, api_key=s.api_key, timeout=60)
        self.history: list[tuple[str, str, str]] = []  # (source, vi, speaker_label)
        self._lock = threading.Lock()
        self.video_code = (video_code or "").strip()
        self.page_title = (page_title or "").strip()
        self.page_summary = (page_summary or "").strip()
        self.page_context_block = self._build_page_context_block()
        self.bible: dict = initial_bible if isinstance(initial_bible, dict) and initial_bible else {}
        if self.bible:
            self._bible_text = _bible_message(self.bible, self.page_context_block)
        elif self.page_context_block:
            self._bible_text = self.page_context_block + "\n\n" + EMPTY_BIBLE_TEXT
        else:
            self._bible_text = EMPTY_BIBLE_TEXT
        self._transcript_text = ""

    def _build_page_context_block(self) -> str:
        lines = []
        if self.video_code:
            lines.append(f"- Mã phim (Code): {self.video_code}")
        if self.page_title:
            lines.append(f"- Tiêu đề phim: {self.page_title}")
        if self.page_summary:
            lines.append(f"- Tóm tắt nội dung & diễn viên trên trang web:\n{self.page_summary}")
        if not lines:
            return ""
        return "[THÔNG TIN & TÓM TẮT CỐT TRUYỆN TỪ TRANG WEB]\n" + "\n".join(lines) + "\n[HẾT TÓM TẮT TRANG WEB]"

    def understand(self, lines: list[tuple[float, str, str]], complete: bool) -> None:
        """Build the story bible from the page summary + SOURCE transcript."""
        if not lines and not self.page_context_block:
            return
        if self.s.api_key == "dummy":
            print("[OpenRouter WARNING] No API key configured in .env! Skipping Story Bible.", flush=True)
            return
        transcript = _transcript_message(lines, complete, self.s.transcript_max_chars) if lines else ""
        user_content = (
            (self.page_context_block + "\n\n" + transcript).strip() if self.page_context_block else transcript
        )
        if not user_content:
            return
        messages = [
            {"role": "system", "content": UNDERSTAND_SYSTEM},
            {"role": "user", "content": user_content},
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
            if transcript:
                self._transcript_text = transcript
            if bible is not None:
                self.bible = bible
                self._bible_text = _bible_message(bible, self.page_context_block)
        if bible is not None:
            print(
                f"[bible] Built Story Bible from page summary + {len(lines)} lines (complete={complete}): "
                f"{len(bible['characters'])} characters, {len(bible['terminology'])} terms",
                flush=True,
            )

    def translate(self, segs: list[Seg], slots: list[float]) -> None:
        if not segs:
            return
        if all(bool(g.vi) for g in segs):
            for g in segs:
                spk = "Nam" if g.gender == "male" else "Nữ"
                self.history.append((g.zh, g.vi, spk))
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
        male_cnt = sum(1 for g in segs if g.gender == "male")
        female_cnt = len(segs) - male_cnt
        print(
            f"[Step 3/3 - OpenRouter VI] Uploading {len(segs)} Japanese line(s) "
            f"(Nữ={female_cnt}, Nam={male_cnt}) to {self.s.base_url} (model={self.s.model})...",
            flush=True,
        )
        ctx = self.history[-self.s.context_pairs :]
        req = {
            "previous_lines": [{"speaker": spk, "zh": z, "vi": v} for z, v, spk in ctx],
            "translate": [
                {
                    "id": i,
                    "speaker": "Nam" if g.gender == "male" else "Nữ",
                    "zh": g.zh,
                    "max_chars": max(30, int(slots[i] * self.s.chars_per_sec)),
                }
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
            spk = "Nam" if g.gender == "male" else "Nữ"
            self.history.append((g.zh, g.vi, spk))

    @staticmethod
    def _log_usage(r, label: str) -> None:
        u = getattr(r, "usage", None)
        if u is None:
            return
        details = getattr(u, "prompt_tokens_details", None)
        cached = getattr(details, "cached_tokens", 0) or 0
        print(f"   [{label} tokens] prompt={u.prompt_tokens} cached={cached} completion={u.completion_tokens}", flush=True)
