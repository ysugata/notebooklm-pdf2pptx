"""OCRテキストの決定論的な文字化け修正。

NotebookLM系スライドの画像はAI生成特有の崩れ字を含み、OCR(50言語統一
モデル)は (1) 中国語字体・旧字体を返す、(2) 形の似た別の漢字に誤読する。
生成AIに頼らず、次の2段で修正する:

1. 異体字正規化: 簡体字・旧字体→日本の標準字体への無条件写像(安全)。
2. 辞書検証つき形近字修復: 漢字連続部分が「1文字ずつの断片」に分割される
   (=単語として成立しない)場合のみ、形近字表の候補へ置換を試し、
   置換後が単一の辞書形態素になる場合に限り採用する。
   SudachiPy(ローカル形態素解析)が無い環境では正規化のみ動作する。

全ての置換は呼び出し側でreviewに記録され、決定論的(同入力→同出力)。
"""
from __future__ import annotations

import re

# --- 1. 異体字・簡体字・旧字体 → 日本標準字体 (無条件で安全な写像のみ) ---
VARIANTS: dict[str, str] = {str(k): str(v) for k, v in {
    # 簡体字
    "认": "認", "证": "証", "识": "識", "语": "語", "说": "説", "请": "請",
    "读": "読", "谈": "談", "课": "課", "论": "論", "订": "訂", "计": "計",
    "记": "記", "货": "貨", "贸": "貿", "费": "費", "资": "資", "质": "質",
    "购": "購", "贩": "販", "输": "輸", "运": "運", "远": "遠", "过": "過",
    "还": "還", "进": "進", "连": "連", "达": "達", "迁": "遷", "选": "選",
    "电": "電", "车": "車", "东": "東", "乐": "楽", "买": "買", "卖": "売",
    "门": "門", "问": "問", "间": "間", "闻": "聞", "开": "開", "关": "関",
    "长": "長", "队": "隊", "阶": "階", "际": "際", "陆": "陸", "级": "級",
    "红": "紅", "纪": "紀", "组": "組", "细": "細", "终": "終", "经": "経",
    "结": "結", "统": "統", "继": "継", "绩": "績", "总": "総", "练": "練",
    "县": "県", "观": "観", "览": "覧", "规": "規", "视": "視", "览": "覧",
    "农": "農", "劳": "労", "动": "動", "务": "務", "为": "為", "办": "弁",
    "极": "極", "构": "構", "标": "標", "术": "術", "环": "環",
    "现": "現", "产": "産", "养": "養", "亲": "親", "热": "熱", "点": "点",
    "国": "国", "会": "会", "对": "対", "个": "個", "们": "們", "华": "華",
    "汉": "漢", "汇": "彙", "报": "報", "护": "護", "扬": "揚",
    "职": "職", "联": "連", "聪": "聡", "脑": "脳", "医": "医", "变": "変",
    # 旧字体
    "亞": "亜", "惡": "悪", "壓": "圧", "圍": "囲", "醫": "医", "爲": "為",
    "應": "応", "櫻": "桜", "奧": "奥", "價": "価", "畫": "画", "擴": "拡",
    "覺": "覚", "學": "学", "樂": "楽", "觀": "観", "氣": "気", "歸": "帰",
    "舊": "旧", "據": "拠", "擧": "挙", "區": "区", "驅": "駆", "經": "経",
    "繼": "継", "縣": "県", "檢": "検", "權": "権", "顯": "顕", "驗": "験",
    "嚴": "厳", "廣": "広", "鑛": "鉱", "號": "号", "國": "国", "濟": "済",
    "產": "産", "參": "参", "慘": "惨", "實": "実", "寫": "写", "釋": "釈",
    "壽": "寿", "收": "収", "從": "従", "獸": "獣", "縱": "縦", "燒": "焼",
    "條": "条", "淨": "浄", "狀": "状", "讓": "譲", "眞": "真", "圖": "図",
    "數": "数", "聲": "声", "靜": "静", "專": "専", "戰": "戦", "錢": "銭",
    "雙": "双", "壯": "壮", "爭": "争", "總": "総", "續": "続", "體": "体",
    "對": "対", "帶": "帯", "滯": "滞", "臺": "台", "單": "単", "團": "団",
    "斷": "断", "遲": "遅", "晝": "昼", "廳": "庁", "鐵": "鉄", "轉": "転",
    "傳": "伝", "燈": "灯", "當": "当", "黨": "党", "獨": "独", "讀": "読",
    "腦": "脳", "廢": "廃", "賣": "売", "發": "発", "變": "変", "邊": "辺",
    "辯": "弁", "寶": "宝", "豐": "豊", "滿": "満", "藥": "薬", "餘": "余",
    "譽": "誉", "樣": "様", "來": "来", "亂": "乱", "兩": "両", "禮": "礼",
    "靈": "霊", "勞": "労", "灣": "湾", "齒": "歯", "齡": "齢", "會": "会",
    "與": "与", "攜": "携", "敎": "教", "內": "内", "戶": "戸", "步": "歩",
}.items()}

# --- 2. 形近字 (OCRが混同しやすい形の似た漢字のグループ) ---
# 一般的な混同グループ。グループ内の相互置換のみを試す。
CONFUSABLE_GROUPS: list[str] = [
    "欧改", "関閔閲閑", "網綱鋼", "微徴徹", "未末", "士土", "千干于",
    "験険検倹", "積績", "講構購溝", "招紹昭", "復複腹", "続統", "緑録縁",
    "議義儀犠", "識織職", "場揚湯", "提堤", "待侍持特", "街衝衛衡",
    "州洲", "制製", "象像", "遣遺遷", "適摘敵滴", "底低抵", "貫慣",
    "設誤設", "輸諭輪", "書晝", "員買貢", "労栄営", "策索", "探深",
    "縄繩", "接授援", "府符附", "都部", "防妨坊", "折析祈", "科料斗", "探深突",
    "瞻職聴", "遴避選", "赣続",
    "維推唯", "億憶臆", "促捉", "季委秀", "各名", "処拠処", "永氷水",
    "田由甲申", "白百日目自", "人入八", "大太犬", "王玉主", "止正",
    "貝見具", "刀力", "工エ王", "口ロ", "夕タ", "二ニ", "力カ", "十†",
]
_CONFUSABLE: dict[str, str] = {}
for group in CONFUSABLE_GROUPS:
    for ch in group:
        _CONFUSABLE[ch] = "".join(c for c in group if c != ch)

_KANJI_RUN = re.compile(r"[一-鿿々]{2,6}")
_KANJI_CHAR = re.compile(r"[一-鿿々]")


def normalize_variants(text: str) -> tuple[str, list[tuple[str, str]]]:
    """簡体字・旧字体を日本標準字体へ正規化する。戻り値: (新テキスト, 置換一覧)"""
    fixes = []
    out = []
    for ch in text:
        rep = VARIANTS.get(ch)
        if rep is not None and rep != ch:
            fixes.append((ch, rep))
            out.append(rep)
        else:
            out.append(ch)
    return "".join(out), fixes


class TextRepairer:
    """辞書検証つきの形近字修復 (SudachiPyがある場合のみ有効)。"""

    def __init__(self) -> None:
        self._tokenizer = None
        try:
            from sudachipy import Dictionary
            self._tokenizer = Dictionary().create()
        except Exception:
            self._tokenizer = None

    @property
    def available(self) -> bool:
        return self._tokenizer is not None

    def _is_single_word(self, run: str) -> bool:
        """runが単一の辞書形態素(非OOV)としてトークン化されるか。"""
        ms = self._tokenizer.tokenize(run)
        return (len(ms) == 1 and not ms[0].is_oov()
                and ms[0].surface() == run)

    def _analyze(self, run: str):
        """トークン列と壊れ指標を返す。

        「壊れ」= OOV、または1文字漢字断片が**隣接して連続**する箇所。
        単独の1文字漢字形態素(助数詞の人・日、接頭辞の約・全 等)は
        日本語として正常なので壊れとみなさない。改+州、機+閔のような
        「1文字断片の連続」だけが誤読の兆候。
        """
        ms = list(self._tokenizer.tokenize(run))
        is_single = [len(m.surface()) == 1 and bool(_KANJI_CHAR.match(m.surface()))
                     for m in ms]
        n_oov = sum(1 for m in ms if m.is_oov())
        n_pairs = sum(1 for i in range(len(ms) - 1)
                      if is_single[i] and is_single[i + 1])
        return ms, is_single, n_oov, n_pairs

    def _quality(self, run: str) -> tuple[int, int, int]:
        """トークン化の品質 (OOV数, 隣接1文字断片ペア数, 形態素数)。"""
        ms, _is_single, n_oov, n_pairs = self._analyze(run)
        return (n_oov, n_pairs, len(ms))

    def _looks_broken(self, run: str) -> bool:
        """漢字連続が「1文字断片」やOOVを含む=単語として成立していないか。"""
        n_oov, n_single, n = self._quality(run)
        if n <= 1 and n_oov == 0:
            return False
        return n_oov > 0 or n_single > 0

    def repair(self, text: str, max_fixes: int = 3) -> tuple[str, list[tuple[str, str]]]:
        """形近字置換で辞書語に修復できる箇所だけを直す。

        判定は必ず「行全体」のトークン化品質で行う。孤立した漢字連続だけを
        見ると、送り仮名に続く正常な漢字(見据えた・4年目 等)まで壊れて
        見えるため。採用条件: 行に壊れの兆候(OOVまたは1文字漢字断片)が
        あり、形近字1字の置換で行全体の品質が厳密に改善する場合のみ。
        """
        if self._tokenizer is None:
            return text, []
        fixes: list[tuple[str, str]] = []
        result = text
        for _ in range(max_fixes):
            base_q = self._quality(result)
            if base_q[0] == 0 and base_q[1] == 0:
                break  # 行に壊れの兆候なし
            # 置換候補の位置は「壊れた形態素の中」に限定する。壊れ=OOV、
            # または隣接する1文字漢字断片の連続に参加している形態素。
            # 健全な形態素(送り仮名つき語幹・数値に付く助数詞等)を触ると
            # 辞書的に成立する別語への誤修正が起こるため。
            ms, is_single, _oov, _pairs = self._analyze(result)
            in_pair = [False] * len(ms)
            for i in range(len(ms) - 1):
                if is_single[i] and is_single[i + 1]:
                    in_pair[i] = in_pair[i + 1] = True
            broken_pos: set[int] = set()
            offset = 0
            for idx, m in enumerate(ms):
                surface = m.surface()
                start = result.find(surface, offset)
                if start < 0:
                    break
                offset = start + len(surface)
                if m.is_oov() or in_pair[idx]:
                    broken_pos.update(range(start, start + len(surface)))
            def _healed_word_at(trial: str, pos: int) -> bool:
                """置換位置を含む形態素が2文字以上の漢字辞書語になったか。"""
                offset2 = 0
                for m in self._tokenizer.tokenize(trial):
                    s = m.surface()
                    start2 = trial.find(s, offset2)
                    if start2 < 0:
                        return False
                    offset2 = start2 + len(s)
                    if start2 <= pos < start2 + len(s):
                        if not (len(s) >= 2 and not m.is_oov()
                                and all(_KANJI_CHAR.match(c) for c in s)):
                            return False
                        # 人名(姓)としてだけ辞書に載る稀少語(八的・入中等)への
                        # 「治癒」は偽物なので棄却する
                        pos_tags = m.part_of_speech()
                        return not (pos_tags[1] == "固有名詞"
                                    and pos_tags[2] == "人名")
                return False

            best = None
            for pos, ch in enumerate(result):
                if pos not in broken_pos or not _KANJI_CHAR.match(ch):
                    continue
                for cand in _CONFUSABLE.get(ch, ""):
                    # 修復先は漢字に限定(カタカナ等の形近字は照合用途のみ)
                    if not _KANJI_CHAR.match(cand):
                        continue
                    trial = result[:pos] + cand + result[pos + 1:]
                    q = self._quality(trial)
                    # 採用条件: 行品質が厳密改善し、かつ置換位置を含む
                    # 形態素が2文字以上の漢字辞書語として成立(局所的な治癒)
                    if (q < base_q and q[0] <= base_q[0] and q[1] <= base_q[1]
                            and _healed_word_at(trial, pos)):
                        if best is None or q < best[0]:
                            best = (q, pos, ch, cand, trial)
            if best is None:
                break
            _q, _pos, old, new, trial = best
            result = trial
            fixes.append((old, new))
        return result, fixes

    def apply(self, text: str) -> tuple[str, list[tuple[str, str]]]:
        """正規化+修復をまとめて適用する。"""
        text, fixes = normalize_variants(text)
        if self._tokenizer is not None:
            text, more = self.repair(text)
            fixes.extend(more)
        return text, fixes
