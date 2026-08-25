#!/usr/bin/env python3
"""修正台帳リプレイの照合規則のテスト。

ブロック全行一致 / 行単位一致 / 部分適用(学習済みルールが文字差分の
一部だけ先に直した状態) / 空白差のみ / 無関係文(素通り) を検証する。

使い方: .venv/bin/python tools/test_replay.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from notebooklm_pdf2pptx.pipeline import Converter


def run() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="replay_test_"))
    pages = tmp / "pages" / "001"
    pages.mkdir(parents=True)
    layout = {
        "blocks": [
            {"lines": [{"text": "介摄"}]},                      # 行単位一致
            {"lines": [{"text": "[文化・体験器扑インフラ]"},      # 部分適用
                       {"text": "教育コンテンツ/行動習慣デザイン"}]},  # 既にafter
            {"lines": [{"text": "EQ・Human Fit 評価/高度な制度設計"}]},  # 空白差のみ
            {"lines": [{"text": "全く無関係の本文"}]},            # 素通り
        ],
        "review": [
            {"text": "[文化・体験器扑インフラ]"},
            {"text": "教育コンテンツ/行動習慣デザイン"},
            {"text": "全く無関係の本文"},
        ],
    }
    (pages / "layout.json").write_text(
        json.dumps(layout, ensure_ascii=False), "utf-8")
    ledger = [
        {"slide": 1, "before": ["介摄"], "after": ["介護"]},
        {"slide": 1,
         "before": ["[文化・体鶏器扑インフラ]", "教育コンテンツ/仔韻習債デデイン"],
         "after": ["[文化・体験設計インフラ]", "教育コンテンツ/行動習慣デザイン"]},
        {"slide": 1, "before": ["EQ-Human Fit詳铺/ 高银制濕银汁"],
         "after": ["EQ・Human Fit評価/高度な制度設計"]},
        {"slide": 1, "before": ["昔の本文"], "after": ["新しい本文"]},
    ]
    (tmp / "fixes_ledger.jsonl").write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in ledger) + "\n", "utf-8")

    dummy = SimpleNamespace(settings=SimpleNamespace(work_dir=tmp),
                            _replay_to_native=Converter._replay_to_native)
    Converter._replay_fix_ledger(dummy, tmp / "pages")

    lay = json.loads((pages / "layout.json").read_text("utf-8"))
    lines = [ln["text"] for b in lay["blocks"] for ln in b["lines"]]
    expect = [
        ("介護", "行単位一致の置換"),
        ("[文化・体験設計インフラ]", "部分適用の置換"),
        ("教育コンテンツ/行動習慣デザイン", "自己到達済み行は不変"),
        ("EQ・Human Fit評価/高度な制度設計", "空白差のみ行の正規化"),
        ("全く無関係の本文", "無関係文は素通り"),
    ]
    failures = []
    for (want, label), got in zip(expect, lines):
        ok = got == want
        print(f"{'OK ' if ok else 'NG!'} {label}: {got!r}")
        if not ok:
            failures.append(label)
    resolved = {r["text"]: r.get("resolved") for r in lay["review"]}
    checks = [
        (bool(resolved["[文化・体験器扑インフラ]"]), "部分適用行のreview解決"),
        (bool(resolved["教育コンテンツ/行動習慣デザイン"]), "自己到達行のreview解決"),
        (not resolved["全く無関係の本文"], "無関係reviewは未解決のまま"),
    ]
    for ok, label in checks:
        print(f"{'OK ' if ok else 'NG!'} {label}")
        if not ok:
            failures.append(label)
    if failures:
        print(f"失敗: {failures}")
        return 1
    print("全テスト成功")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
