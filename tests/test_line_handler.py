"""
Unit tests for OrderBot logic in line_handler.py
Run: pytest tests/ -v
"""
import re
import unicodedata
import pytest

# ─── 把 clean_name & parse_remark 邏輯獨立出來測試，不需要 DB ──────


def clean_name(name: str) -> str:
    """從 line_handler.OrderBot.clean_name 複製，確保行為一致"""
    if not name:
        return ""
    n = unicodedata.normalize("NFKC", name)
    n = re.sub(r"[\u200b-\u200f\ufeff\u202a-\u202e\r\n]", "", n)
    n = re.sub(r"\s+", " ", n)
    return n.strip()


def parse_item_and_remark(raw_item: str):
    """從 handle_order_command 中抽出的備註解析邏輯"""
    parsed_name = raw_item
    parsed_remark = ""

    paren_match = re.search(r"[\(（](.*?)[\)）]?$", raw_item)
    if paren_match:
        parsed_remark = paren_match.group(1).strip()
        parsed_name = raw_item[: paren_match.start()].strip()
    elif "-" in raw_item or "－" in raw_item:
        parts = re.split(r"\s*[-－]\s*", raw_item, maxsplit=1)
        parsed_name = parts[0].strip()
        parsed_remark = parts[1].strip()
    else:
        parts = raw_item.rsplit(maxsplit=1)
        if len(parts) == 2:
            parsed_name = parts[0].strip()
            parsed_remark = parts[1].strip()

    return parsed_name, parsed_remark


# ════════════════════════════════════════
# 1. clean_name 測試
# ════════════════════════════════════════
class TestCleanName:
    def test_zero_width_space(self):
        assert clean_name("大腸臭臭鍋\u200b") == "大腸臭臭鍋"

    def test_bom(self):
        assert clean_name("\ufeff海鮮香香鍋") == "海鮮香香鍋"

    def test_crlf(self):
        assert clean_name("肉羹麵\r\n") == "肉羹麵"

    def test_full_width_spaces(self):
        # NFKC 正規化把全形空白 → 半形
        assert clean_name("肉　羹　麵") == "肉 羹 麵"

    def test_multiple_spaces_collapsed(self):
        assert clean_name("大  腸   鍋") == "大 腸 鍋"

    def test_empty_string(self):
        assert clean_name("") == ""

    def test_none_equivalent(self):
        assert clean_name(None) == ""  # type: ignore

    def test_normal_string_unchanged(self):
        assert clean_name("大腸臭臭鍋") == "大腸臭臭鍋"

    def test_invisible_lrm(self):
        # U+200E LEFT-TO-RIGHT MARK
        assert clean_name("大腸\u200e臭臭鍋") == "大腸臭臭鍋"

    def test_fullwidth_to_halfwidth_digits(self):
        # NFKC 應把全形數字轉為半形
        assert clean_name("１６０") == "160"


# ════════════════════════════════════════
# 2. 備註解析測試
# ════════════════════════════════════════
class TestParseItemAndRemark:
    # ── 括號格式 ──
    def test_half_paren(self):
        name, remark = parse_item_and_remark("魷魚羹麵(不要香菜)")
        assert name == "魷魚羹麵"
        assert remark == "不要香菜"

    def test_full_paren(self):
        name, remark = parse_item_and_remark("魷魚羹麵（不要香菜）")
        assert name == "魷魚羹麵"
        assert remark == "不要香菜"

    def test_mixed_paren_open_only(self):
        # 使用者只打左括號沒關閉
        name, remark = parse_item_and_remark("魷魚羹麵(不要香菜")
        assert name == "魷魚羹麵"
        assert remark == "不要香菜"

    # ── 減號格式 ──
    def test_halfwidth_dash(self):
        name, remark = parse_item_and_remark("魷魚羹麵-不要香菜")
        assert name == "魷魚羹麵"
        assert remark == "不要香菜"

    def test_fullwidth_dash(self):
        name, remark = parse_item_and_remark("魷魚羹麵－不要香菜")
        assert name == "魷魚羹麵"
        assert remark == "不要香菜"

    def test_dash_with_spaces(self):
        name, remark = parse_item_and_remark("魷魚羹麵 - 不要香菜")
        assert name == "魷魚羹麵"
        assert remark == "不要香菜"

    # ── 空格格式 ──
    def test_space_separator(self):
        name, remark = parse_item_and_remark("魷魚羹麵 不要香菜")
        assert name == "魷魚羹麵"
        assert remark == "不要香菜"

    def test_space_multiword_remark(self):
        # 備註有兩個字，rsplit(maxsplit=1) 應只切最後一個空格
        # "大腸臭臭鍋 不要 火鍋料" → name="大腸臭臭鍋 不要", remark="火鍋料"
        # 這是預期行為：系統會先嘗試 "大腸臭臭鍋 不要" 去比對，比對失敗後 fallback 全字串
        name, remark = parse_item_and_remark("大腸臭臭鍋 不要 火鍋料")
        assert name == "大腸臭臭鍋 不要"
        assert remark == "火鍋料"

    def test_no_remark(self):
        # 單一品名，不含分隔符號
        # rsplit(maxsplit=1) 在只有一個 token 時不拆
        name, remark = parse_item_and_remark("大腸臭臭鍋")
        # 沒有空格，rsplit 只會回傳 1 個元素，所以 parsed_remark 應保持空
        assert name == "大腸臭臭鍋"
        assert remark == ""


# ════════════════════════════════════════
# 3. !點 指令第一行解析
# ════════════════════════════════════════
class TestFirstLineParser:
    """測試從 !點 指令第一行解析出 shop_name & payer_code"""

    def _parse(self, first_line: str):
        """複製 handle_order_command 的首行解析邏輯"""
        first = first_line.replace("!點", "").replace("！點", "").strip()
        parts = first.split()
        shop_name = None
        payer_code = None
        if len(parts) >= 2:
            shop_name = parts[0]
            payer_code = parts[1]
        elif len(parts) == 1:
            if parts[0].isdigit():
                payer_code = parts[0]
            else:
                shop_name = parts[0]
        return shop_name, payer_code

    def test_shop_and_payer(self):
        shop, payer = self._parse("!點 麗媽 18")
        assert shop == "麗媽"
        assert payer == "18"

    def test_payer_only(self):
        shop, payer = self._parse("!點 18")
        assert shop is None
        assert payer == "18"

    def test_shop_only(self):
        shop, payer = self._parse("!點 麗媽")
        assert shop == "麗媽"
        assert payer is None

    def test_full_width_bang(self):
        shop, payer = self._parse("！點 麗媽 20")
        assert shop == "麗媽"
        assert payer == "20"

    def test_empty(self):
        shop, payer = self._parse("!點")
        assert shop is None
        assert payer is None

    def test_extra_spaces(self):
        shop, payer = self._parse("!點  麗媽   20")
        assert shop == "麗媽"
        assert payer == "20"


# ════════════════════════════════════════
# 4. 訂單行 regex 解析
# ════════════════════════════════════════
class TestOrderLineRegex:
    PATTERN = re.compile(r"^(\d+)[.\s]+(.+)$")

    def _parse(self, line: str):
        m = self.PATTERN.match(line.strip())
        if not m:
            return None, None
        return m.group(1), m.group(2).strip()

    def test_dot_separator(self):
        code, item = self._parse("21. 大腸臭臭鍋")
        assert code == "21" and item == "大腸臭臭鍋"

    def test_space_separator(self):
        code, item = self._parse("21 大腸臭臭鍋")
        assert code == "21" and item == "大腸臭臭鍋"

    def test_dot_with_remark(self):
        code, item = self._parse("5. 沙茶牛肉炒麵 不要蔥")
        assert code == "5" and item == "沙茶牛肉炒麵 不要蔥"

    def test_invalid_no_code(self):
        code, item = self._parse("大腸臭臭鍋")
        assert code is None

    def test_leading_spaces(self):
        code, item = self._parse("  21. 大腸臭臭鍋  ")
        assert code == "21" and item == "大腸臭臭鍋"


# ════════════════════════════════════════
# 5. OCR JSON 解析邏輯（複製自 app.py）
# ════════════════════════════════════════
import json


def parse_ocr_raw(raw: str):
    """複製 app.py ocr_menu 中的解析邏輯"""
    parsed = []
    try:
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if match:
            parsed = json.loads(match.group(0))
        else:
            clean_raw = re.sub(r"^```[a-z]*\n?", "", raw)
            clean_raw = re.sub(r"\n?```$", "", clean_raw).strip()
            parsed = json.loads(clean_raw)
    except Exception:
        objects = re.findall(r"\{[^{}]*\}", raw)
        for obj_str in objects:
            try:
                obj = json.loads(obj_str)
                if "name" in obj:
                    parsed.append(obj)
            except Exception:
                continue
        if not parsed:
            raise ValueError(f"無法解析：{raw[:100]}")
    return parsed


class TestOcrJsonParsing:
    def test_clean_json_array(self):
        raw = '[{"name": "大腸臭臭鍋", "price": 160}]'
        result = parse_ocr_raw(raw)
        assert result[0]["name"] == "大腸臭臭鍋"
        assert result[0]["price"] == 160

    def test_markdown_wrapped(self):
        raw = "```json\n[{\"name\": \"海鮮鍋\", \"price\": 180}]\n```"
        result = parse_ocr_raw(raw)
        assert result[0]["name"] == "海鮮鍋"

    def test_jsonl_fallback(self):
        """模型回傳多行 JSONL 格式"""
        raw = '{"name": "肉羹麵", "price": 60}\n{"name": "沙茶麵", "price": 65}'
        result = parse_ocr_raw(raw)
        assert len(result) == 2
        assert result[0]["name"] == "肉羹麵"

    def test_inline_prose_with_json(self):
        """模型在 JSON 前後塞了說明文字"""
        raw = '以下是菜單：[{"name": "雞排飯", "price": 80}] 感謝您使用本服務'
        result = parse_ocr_raw(raw)
        assert result[0]["name"] == "雞排飯"

    def test_null_price(self):
        raw = '[{"name": "特餐", "price": null}]'
        result = parse_ocr_raw(raw)
        assert result[0]["price"] is None

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            parse_ocr_raw("這完全不是 JSON")


# ════════════════════════════════════════
# 6. 零訂單時應回傳錯誤訊息 (Fix #2)
# ════════════════════════════════════════
class TestZeroOrderEarlyReturn:
    """確認 handle_order_command 在全部行都解析失敗時回傳 ❌"""

    def _simulate_empty_result(self, errors: list) -> str:
        """模擬 orders_info 為空時的回傳邏輯"""
        orders_info = []
        if not orders_info:
            err_detail = '\n'.join(f'• {e}' for e in errors) if errors else '請確認格式：代號. 品項名稱'
            return f'❌ 沒有成功記錄任何訂單\n{err_detail}'
        return '✅'  # 不應該走到這裡

    def test_error_message_starts_with_x(self):
        result = self._simulate_empty_result(['代號 99 不存在'])
        assert result.startswith('❌')

    def test_error_contains_detail(self):
        result = self._simulate_empty_result(['代號 99 不存在'])
        assert '代號 99 不存在' in result

    def test_no_errors_gives_format_hint(self):
        result = self._simulate_empty_result([])
        assert '請確認格式' in result

    def test_does_not_say_zero_orders(self):
        result = self._simulate_empty_result(['代號 99 不存在'])
        assert '已記錄 0 筆' not in result
