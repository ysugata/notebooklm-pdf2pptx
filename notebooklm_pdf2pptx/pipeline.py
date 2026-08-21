"""パイプライン統括: acquire → ocr → measure → solve → group → remove → compose → qa

100ページ級への対応:
- ページ毎に work/pages/NNN/ へ中間物を保存し、入力+設定が不変ならスキップ(レジューム)。
- 画像は常に1ページ分のみメモリへ。
- LaMaは「複雑背景を含むページ」だけを集めて最後に1バッチで実行(モデルロード1回)。
"""
from __future__ import annotations

import hashlib
import json
import os
import unicodedata
import shutil
import time
from pathlib import Path

import cv2
import numpy as np

from .config import Settings, JP_FONT_CANDIDATES, LATIN_FONT_CANDIDATES
from .fontlib import FontLibrary
from .inkstats import measure_ink
from .lines import SolvedLine, group_lines
from .ocr import (OcrEngine, char_ink_heights, fix_circle_glyphs, is_warped,
                  restore_spaces, split_mixed_sizes)
from .pages import (CanvasPage, load_image_pages, load_pdf_pages,
                    load_pptx_pages, resolve_input)
from . import removal
from .solver import (Candidates, arbitrate_edge_confusables, collect_candidates,
                     has_japanese, refine_by_template, refine_line, resolve_with)


def _covered_fraction(bbox, covers) -> float:
    """インクbboxが不透明カバー矩形群に覆われる面積割合。"""
    x0, y0, x1, y1 = bbox
    area = max((x1 - x0) * (y1 - y0), 1.0)
    covered = 0.0
    for cx0, cy0, cx1, cy1 in covers:
        w = min(x1, cx1) - max(x0, cx0)
        h = min(y1, cy1) - max(y0, cy0)
        if w > 0 and h > 0:
            covered += w * h
    return min(covered / area, 1.0)


def _hash_inputs(paths: list[Path], settings: Settings) -> str:
    digest = hashlib.sha256()
    for path in paths:
        stat = path.stat()
        digest.update(str(path).encode())
        digest.update(str(stat.st_mtime_ns).encode())
        digest.update(str(stat.st_size).encode())
    relevant = {
        "ocr_upscale": settings.ocr_upscale,
        "ocr_model_type": settings.ocr_model_type,
        "min_confidence": settings.min_confidence,
        "inpaint": settings.inpaint,
        "render_scale": settings.render_scale,
        "non_portable_penalty": settings.non_portable_penalty,
        "version": 9,
    }
    digest.update(json.dumps(relevant, sort_keys=True).encode())
    return digest.hexdigest()[:16]


class Converter:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.library = FontLibrary(extra_dirs=[settings.fonts_dir])
        self.candidates: Candidates = collect_candidates(
            self.library, settings, JP_FONT_CANDIDATES, LATIN_FONT_CANDIDATES
        )
        self._ocr: OcrEngine | None = None

    @property
    def ocr(self) -> OcrEngine:
        if self._ocr is None:
            self._ocr = OcrEngine(self.settings)
        return self._ocr

    # ------------------------------------------------------------------
    def convert(self, input_path: Path, output_path: Path) -> dict:
        settings = self.settings
        kind, paths = resolve_input(input_path)
        run_hash = _hash_inputs(paths, settings)
        pages_dir = settings.work_dir / "pages"
        pages_dir.mkdir(parents=True, exist_ok=True)

        if kind == "pdf":
            page_iter = load_pdf_pages(paths[0], settings, settings.pages or None)
        elif kind == "pptx":
            page_iter = load_pptx_pages(paths[0], settings, settings.pages or None)
        else:
            page_iter = load_image_pages(paths, settings)

        processed: list[dict] = []
        lama_queue: list[int] = []
        started = time.time()
        for page in page_iter:
            record = self._process_page(page, pages_dir, run_hash)
            processed.append(record)
            if record["lama_pending"]:
                lama_queue.append(record["number"])
            print(
                f"page {record['number']:>3}: elements={record['n_elements']:>3} "
                f"flat={record['n_flat']:>3} complex={record['n_complex']:>3} "
                f"[{record['cache']}] {time.time() - started:6.1f}s",
                flush=True,
            )

        if lama_queue:
            self._run_lama(pages_dir, lama_queue)

        from .pptx_writer import build_presentation

        report = build_presentation(processed, pages_dir, output_path, settings, self.library)

        if settings.qa_enabled:
            from .qa import run_qa

            report["qa"] = run_qa(processed, pages_dir, settings, self.library)

        report_path = settings.work_dir / "report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report

    # ------------------------------------------------------------------
    def _process_page(self, page: CanvasPage, pages_dir: Path, run_hash: str) -> dict:
        settings = self.settings
        page_dir = pages_dir / f"{page.number:03d}"
        marker = page_dir / "done.json"
        if marker.is_file():
            try:
                record = json.loads(marker.read_text(encoding="utf-8"))
                if record.get("hash") == run_hash:
                    record["cache"] = "cached"
                    return record
            except Exception:
                pass
        if page_dir.exists():
            shutil.rmtree(page_dir)
        page_dir.mkdir(parents=True)

        image = page.image
        h, w = image.shape[:2]
        cv2.imwrite(str(page_dir / "source.png"), image)

        pt_per_px = self._pt_per_px(page)

        solved_lines: list[SolvedLine] = []
        review: list[dict] = []
        flat_masks: list[np.ndarray] = []
        complex_mask = np.zeros((h, w), np.uint8)
        n_flat = n_complex = 0

        if page.needs_ocr:
            recognized = []
            for raw in self.ocr.recognize(image):
                recognized.extend(split_mixed_sizes(raw, image))
            for line in recognized:
                ink = measure_ink(image, line.bbox)
                if ink is None:
                    continue
                # 不透明カバー(持ち越し画像・塗り図形)に大半が隠れる行は、
                # 原本で不可視のため復元も除去もしない(隠しテキストの蘇生防止)
                if page.cover_rects_px and _covered_fraction(
                        ink.ink_bbox, page.cover_rects_px) >= 0.6:
                    continue
                # アーチ状・波状の変形テキストは直線ボックスで再現できないため
                # 画像のまま保持する(除去も復元もしない)
                if is_warped(line, image):
                    review.append({"text": line.text,
                                   "confidence": round(line.confidence, 3),
                                   "bbox": [round(v, 1) for v in line.bbox],
                                   "reason": "曲線・変形テキスト(画像のまま保持)"})
                    continue
                text = restore_spaces(line, image)
                # 欧文主体の行はNFKC正規化(全角コロン・括弧等→ASCII)。
                # OCRが全角記号を返すと欧文フォントの未収録グリフになり、
                # フォント選択と幅計算が乱れるため。
                if not has_japanese(text):
                    text = unicodedata.normalize("NFKC", text)
                # 丸記号のみの行(評価表の◎○●等): OCRのラテン文字化を
                # インクの輪郭入れ子構造の実測で正しい記号へ戻す
                text = fix_circle_glyphs(text, line, image)
                # サイズ混在行(分割不可のもの): インクbbox高は最大文字に
                # 引っ張られるため、文字高さの中央値を初期サイズのヒントにする
                size_hint = 1.0
                heights = [v for v in char_ink_heights(line, image) if v > 3]
                if len(heights) >= 6:
                    median_h = sorted(heights)[len(heights) // 2]
                    if ink.ink_h > median_h * 1.30:
                        size_hint = median_h / ink.ink_h
                style = refine_line(text, ink, image, self.candidates,
                                    pt_per_px, settings, size_scale_hint=size_hint,
                                    det_height_px=line.bbox[3] - line.bbox[1])
                # 極性リトライ: 明るい帯の上の白文字等では「bbox内少数派」則が
                # 外れ、バーの影を「暗い文字」と誤計測することがある。
                # 照合が弱い場合のみ逆極性で計測し直し、NCCが良い方を採用する。
                if style is None or (style.ncc or 0) < 0.35:
                    ink_alt = measure_ink(image, line.bbox,
                                          force_polarity=not ink.text_brighter)
                    if ink_alt is not None:
                        style_alt = refine_line(
                            text, ink_alt, image, self.candidates,
                            pt_per_px, settings, size_scale_hint=size_hint,
                            det_height_px=line.bbox[3] - line.bbox[1])
                        if style_alt is not None and (style_alt.ncc or 0) > (
                                (style.ncc or 0) if style else -1.0):
                            ink, style = ink_alt, style_alt
                if style is None:
                    continue
                # 行頭・行末の紛らわしい字形(装飾の波ダッシュ等)をNCCで裁定
                text, style = arbitrate_edge_confusables(
                    image, text, style, ink, pt_per_px, settings)
                # 単語ボックスに現れない「落ちたスペース」をレイアウトで復元:
                # camelCase境界・×等の記号境界について、解決済みフォントの
                # 送り幅から予測した位置に実際の空白列があるかを計測し、
                # 挿入して照合が改善する場合のみ採用する(二重の自己検証)
                text, style = self._restore_layout_spaces(
                    text, style, ink, image, line, pt_per_px)
                # 変形テキストの後段判定: 直線テンプレートとの相関が極端に低く、
                # かつ検出枠の高さが解決サイズの2倍を超える場合はアーチ状等の
                # 装飾文字(is_warpedが背景ノイズで拾えないケースの受け皿)。
                # 直線ボックスでは再現できないため画像のまま保持する。
                det_h = line.bbox[3] - line.bbox[1]
                size_px_solved = style.size_pt / pt_per_px
                if style.ncc < 0.15 and (det_h > size_px_solved * 2.0
                                          or line.confidence < 0.80):
                    review.append({"text": text,
                                   "confidence": round(line.confidence, 3),
                                   "bbox": [round(v, 1) for v in line.bbox],
                                   "reason": "曲線・変形テキスト(画像のまま保持)"})
                    continue
                solved_lines.append(
                    SolvedLine(text=text, style=style, ink_bbox=ink.ink_bbox,
                               confidence=line.confidence, source="ocr", ink=ink,
                               glow=removal.detect_glow(image, ink, pt_per_px))
                )
                if line.confidence < settings.review_confidence:
                    review.append({"text": text, "confidence": round(line.confidence, 3),
                                   "bbox": [round(v, 1) for v in line.bbox]})
                elif style.missing:
                    review.append({"text": text, "confidence": round(line.confidence, 3),
                                   "bbox": [round(v, 1) for v in line.bbox],
                                   "missing_glyphs": style.missing})
                elif style.ncc == 0:
                    review.append({"text": text, "confidence": round(line.confidence, 3),
                                   "bbox": [round(v, 1) for v in line.bbox],
                                   "reason": "照合失敗(サイズ・位置は推定値)"})
                kind = removal.classify_region(image, ink, settings)
                if settings.inpaint == "flat":
                    kind = "flat"
                if kind == "flat":
                    flat_masks.append(removal.region_mask(ink, (h, w)))
                    n_flat += 1
                else:
                    complex_mask = np.maximum(
                        complex_mask, removal.complex_mask(image, ink, settings))
                    n_complex += 1

        # PDFレンダリングページの可視テキストは画像に焼き込まれているので除去対象
        if page.source_kind == "pdf-render":
            for overlay in page.overlay_texts:
                ink = measure_ink(image, overlay.bbox_px)
                if ink is None:
                    continue
                if removal.classify_region(image, ink, settings) == "flat":
                    flat_masks.append(removal.region_mask(ink, (h, w)))
                    n_flat += 1
                else:
                    complex_mask = np.maximum(
                        complex_mask, removal.complex_mask(image, ink, settings))
                    n_complex += 1

        blocks = group_lines(solved_lines, w)
        self._unify_blocks(blocks, image, pt_per_px)
        self._measure_color_runs(blocks, image, pt_per_px)

        # 背景生成
        background = removal.remove_flat(image, flat_masks)
        lama_pending = False
        if int(complex_mask.max()) > 0:
            mode = settings.inpaint
            use_lama = mode in ("auto", "lama") and removal.lama_available(settings)
            if use_lama:
                cv2.imwrite(str(page_dir / "lama_input.png"), background)
                cv2.imwrite(str(page_dir / "lama_mask.png"), complex_mask)
                lama_pending = True
            else:
                background = removal.classic_complex_fallback(background, complex_mask)
        cv2.imwrite(str(page_dir / "background.png"), background)

        layout = {
            "number": page.number,
            "canvas_size": [w, h],
            "pt_per_px": pt_per_px,
            "source_kind": page.source_kind,
            "blocks": [self._block_dict(block) for block in blocks],
            "overlay_texts": [self._overlay_text_dict(o, page) for o in page.overlay_texts]
            if page.source_kind == "pdf-native" else [],
            "review": review,
        }
        (page_dir / "layout.json").write_text(
            json.dumps(layout, ensure_ascii=False, indent=1), encoding="utf-8")

        for index, xml in enumerate(page.native_xml):
            (page_dir / f"native_{index:02d}.xml").write_text(xml, encoding="utf-8")

        for index, overlay in enumerate(page.overlay_images):
            (page_dir / f"overlay_{index:02d}.{overlay.ext}").write_bytes(overlay.data)
        overlay_images = [
            {"file": f"overlay_{index:02d}.{o.ext}", "bbox": list(o.bbox_px)}
            for index, o in enumerate(page.overlay_images)
        ]

        record = {
            "hash": run_hash,
            "number": page.number,
            "canvas_size": [w, h],
            "pt_per_px": pt_per_px,
            "source_kind": page.source_kind,
            "n_elements": len(solved_lines) + len(page.overlay_texts),
            "n_flat": n_flat,
            "n_complex": n_complex,
            "lama_pending": lama_pending,
            "overlay_images": overlay_images,
            "native_xml": [f"native_{i:02d}.xml" for i in range(len(page.native_xml))],
            "slide_emu": list(page.slide_emu) if page.slide_emu else None,
            "n_review": len(review),
            "cache": "fresh",
        }
        marker.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        return record

    # ------------------------------------------------------------------
    def _unify_blocks(self, blocks, image, pt_per_px: float) -> None:
        """複数行ブロック内のフェイス・サイズの揺れを、最良一致の行の値へ統一する。

        NCC(テンプレート照合)が最も高い行のフェイス・サイズを採用し、
        他の行は同フェイス・同サイズ固定でテンプレート照合し位置・字間だけ更新する。
        """
        for block in blocks:
            if len(block.lines) < 2:
                continue
            best = max(block.lines, key=lambda l: (l.style.ncc, -l.style.score))
            face = best.style.face
            size_px = int(round(best.style.size_pt / pt_per_px))
            for line in block.lines:
                if line is best or line.ink is None:
                    continue
                refined = refine_by_template(
                    image, line.text, face, line.style, line.ink,
                    pt_per_px, self.settings, size_lock_px=size_px)
                if refined is not None:
                    line.style = refined[0]
                else:
                    fallback = resolve_with(line.text, line.ink, face,
                                            best.style.size_pt, pt_per_px, self.settings)
                    if fallback is not None:
                        line.style = fallback

    # ------------------------------------------------------------------
    def _restore_layout_spaces(self, text, style, ink, image, line, pt_per_px):
        """OCR単語ボックスに現れないスペース欠落をレイアウト検証で復元する。

        NotebookLM系スライドは「Human Capital」「A × B」のような固有名詞・
        記号周りのスペースをOCRが落としやすい(単語ボックスが1つに融合する
        ため restore_spaces のギャップ復元も届かない)。解決済みフォントの
        送り幅で各文字の予測x位置を割り出し、境界候補(小文字→大文字の
        camelCase遷移、×・| の記号境界、英数字と和文の遷移)に十分な幅の
        空白列が実在する場合のみスペースを挿入して解き直す。
        挿入はNCCが改善する場合のみ採用(実測とテンプレート照合の二重検証)。
        """
        settings = self.settings
        if ink.core_mask_local is None or style is None:
            return text, style

        import re
        jp = re.compile(r"[　-ヿ㐀-鿿豈-﫿]")

        def boundary_candidate(prev: str, cur: str) -> bool:
            if prev == " " or cur == " ":
                return False
            if prev.islower() and cur.isupper():        # camelCase
                return True
            if prev in "×|" or cur in "×|":             # 記号境界
                return True
            prev_jp, cur_jp = bool(jp.match(prev)), bool(jp.match(cur))
            ascii_alnum = ((prev.isascii() and prev.isalnum())
                           or (cur.isascii() and cur.isalnum()))
            if prev_jp != cur_jp and ascii_alnum:
                return True                              # 和文↔ASCII英数字の遷移
            return False

        if not any(boundary_candidate(text[i - 1], text[i])
                   for i in range(1, len(text))):
            return text, style

        from .fontlib import _load_pil_font

        for _iteration in range(6):
            size_px = style.size_pt / pt_per_px
            if size_px < 7:
                break
            try:
                font = _load_pil_font(style.face.path, style.face.index,
                                      max(7, int(round(size_px))))
            except Exception:
                break
            spacing_px = style.char_spacing_pt / pt_per_px
            core = ink.core_mask_local
            col_ink = (core > 0).any(axis=0)
            x = style.origin_x_px - ink.region[0]
            candidates: list[tuple[float, int]] = []
            for i, ch in enumerate(text):
                if i > 0 and boundary_candidate(text[i - 1], ch):
                    a = max(0, int(x - 0.10 * size_px))
                    b = min(len(col_ink), int(x + 0.45 * size_px))
                    run = best_run = 0
                    for v in col_ink[a:b]:
                        run = 0 if v else run + 1
                        best_run = max(best_run, run)
                    if best_run >= 0.20 * size_px:
                        candidates.append((float(best_run), i))
                x += font.getlength(ch) + spacing_px
            if not candidates:
                break
            _w, pos = max(candidates)
            trial = text[:pos] + " " + text[pos:]
            new_style = refine_line(trial, ink, image, self.candidates,
                                    pt_per_px, settings,
                                    det_height_px=line.bbox[3] - line.bbox[1])
            if new_style is None or (new_style.ncc or 0) <= (style.ncc or 0) + 0.005:
                break
            text, style = trial, new_style
        return text, style

    def _measure_color_runs(self, blocks, image, pt_per_px: float) -> None:
        """行内の文字別色を計測し、色が切り替わる位置で色ランに分割する。

        タイトル等の部分強調(白地に金色・赤色のアクセント)は1行1色に
        潰すと原本の印象が大きく変わる。解決済みフォントの送り幅で行を
        文字セルへ水平分割し、セルごとにコアインクの純色を計測、
        隣接セルの色距離が閾値を超える位置でランを切る。
        書式(フォント・サイズ・字間)はランで共有し、色だけを分ける。
        """
        from .fontlib import _load_pil_font

        for block in blocks:
            for line in block.lines:
                ink = line.ink
                if ink is None or getattr(ink, "core_mask_local", None) is None:
                    continue
                text = line.text
                if sum(1 for c in text if not c.isspace()) < 2:
                    continue
                style = line.style
                size_px = style.size_pt / pt_per_px
                if size_px < 7:
                    continue
                try:
                    font = _load_pil_font(style.face.path, style.face.index,
                                          max(7, int(round(size_px))))
                except Exception:
                    continue
                spacing_px = style.char_spacing_pt / pt_per_px
                rx0, ry0, _rx1, _ry1 = ink.region
                core = ink.core_mask_local
                rgb = cv2.cvtColor(
                    image[ry0:ry0 + core.shape[0], rx0:rx0 + core.shape[1]],
                    cv2.COLOR_BGR2RGB).astype(np.float32)
                bg = np.asarray(ink.bg_color, np.float32)
                # 文字セル境界: 解決済みの送り幅・字間で水平分割
                x = style.origin_x_px - rx0
                colors: list = []
                for ch in text:
                    adv = font.getlength(ch)
                    a, b = int(x), int(round(x + adv))
                    x += adv + spacing_px
                    if ch.isspace() or b - max(a, 0) < 2:
                        colors.append(None)
                        continue
                    a = max(0, a)
                    b = min(core.shape[1], b)
                    if b <= a:
                        colors.append(None)
                        continue
                    sel = core[:, a:b] > 0
                    px = rgb[:, a:b][sel]
                    if len(px) < 12:
                        colors.append(None)
                        continue
                    # 純色: 背景から遠い上位30%のメディアン (inkstatsと同一規則)
                    dist = np.linalg.norm(px - bg, axis=1)
                    chosen = px[dist >= np.percentile(dist, 70)]
                    if len(chosen) < 4:
                        chosen = px
                    colors.append(np.median(chosen, axis=0))
                # 色の異同判定: 輝度正規化クロマ距離 + 輝度比の2軸。
                # 細字・小サイズはエッジ混色で「白が暗い白」に測れるため、
                # 単純RGB距離だと偽分割する。同系色(白と灰白)はクロマが
                # ほぼ一致するので統合され、金・赤等の実アクセントだけが分かれる。
                def is_different(c1, c2) -> bool:
                    l1 = max(float(np.mean(c1)), 1.0)
                    l2 = max(float(np.mean(c2)), 1.0)
                    chroma = float(np.linalg.norm(c1 / l1 * 160.0 - c2 / l2 * 160.0))
                    lum_ratio = max(l1, l2) / min(l1, l2)
                    return (chroma > self.settings.color_run_chroma_delta
                            or lum_ratio > self.settings.color_run_lum_ratio)

                runs: list = []   # [start, end, color_sum, n]
                current = None
                for idx, col in enumerate(colors):
                    if col is None:
                        if current is not None:
                            current[1] = idx + 1
                        continue
                    if current is not None and not is_different(
                            col, current[2] / current[3]):
                        current[1] = idx + 1
                        current[2] = current[2] + col
                        current[3] += 1
                    else:
                        current = [idx, idx + 1, col.copy(), 1]
                        runs.append(current)
                if os.environ.get("NBLM_DEBUG_RUNS"):
                    dbg = [(text[i], None if c is None else [int(v) for v in c])
                           for i, c in enumerate(colors)]
                    print(f"[runs] {text[:16]!r} n_runs={len(runs)} chars={dbg}")
                # ラン数上限: 文字ごとに色が明滅するのはノイズ(計測不良)だが、
                # 長いタイトルの多段強調(白/金の交互等)は正当。行長に応じて許容。
                if len(runs) < 2 or len(runs) > max(6, len(text) // 4):
                    continue
                # 連続性の正規化(空白・低インク字は前ランに帰属)と端の拡張
                runs[0][0] = 0
                for i in range(len(runs) - 1):
                    runs[i][1] = runs[i + 1][0]
                runs[-1][1] = len(text)
                means = [r[2] / r[3] for r in runs]
                if any(not is_different(means[i], means[i + 1])
                       for i in range(len(means) - 1)):
                    continue
                line.color_runs = [
                    [int(r[0]), int(r[1]),
                     [int(np.clip(round(v), 0, 255)) for v in (r[2] / r[3])]]
                    for r in runs
                ]

    def _pt_per_px(self, page: CanvasPage) -> float:
        """キャンバスpx → スライドpt の変換係数。

        既定はスライド幅13.333in=960pt。PPTX入力では出力サイズを
        入力スライドに合わせるため、その幅(pt)を基準にする。
        """
        w = page.size[0]
        slide_w_pt = page.slide_emu[0] / 12700.0 if page.slide_emu else 960.0
        return slide_w_pt / w

    def _block_dict(self, block) -> dict:
        return {
            "align": block.align,
            "pitch_px": block.pitch_px,
            "lines": [
                {
                    "text": line.text,
                    "confidence": round(line.confidence, 4),
                    "source": line.source,
                    "ink_bbox": list(line.ink_bbox),
                    "font_family": line.style.face.typeface,
                    "font_path": line.style.face.path,
                    "font_index": line.style.face.index,
                    "size_pt": line.style.size_pt,
                    "char_spacing_pt": line.style.char_spacing_pt,
                    "bold": line.style.bold,
                    "color": list(line.style.color),
                    "gradient": [list(line.style.gradient[0]), list(line.style.gradient[1])]
                    if line.style.gradient else None,
                    "origin_x_px": round(line.style.origin_x_px, 2),
                    "baseline_y_px": round(line.style.baseline_y_px, 2),
                    "advance_w_px": round(line.style.advance_w_px, 2),
                    "match_score": round(line.style.score, 4),
                    "ncc": line.style.ncc,
                    "glow": line.glow,
                    "color_runs": line.color_runs,
                }
                for line in block.lines
            ],
        }

    def _overlay_text_dict(self, overlay, page: CanvasPage) -> dict:
        return {
            "text": overlay.text,
            "bbox_px": list(overlay.bbox_px),
            "font": overlay.font,
            "size_pt_pdf": overlay.size_pt_pdf,
            "px_per_pt": page.px_per_pt or 1.0,
            "color": list(overlay.color_rgb),
            "bold": overlay.bold,
            "italic": overlay.italic,
            "origin_y_px": overlay.origin_y_px,
        }

    # ------------------------------------------------------------------
    def _run_lama(self, pages_dir: Path, numbers: list[int]) -> None:
        staging = self.settings.work_dir / "lama_batch"
        image_dir = staging / "images"
        mask_dir = staging / "masks"
        out_dir = staging / "out"
        for directory in (image_dir, mask_dir, out_dir):
            if directory.exists():
                shutil.rmtree(directory)
            directory.mkdir(parents=True)
        for number in numbers:
            page_dir = pages_dir / f"{number:03d}"
            shutil.copy2(page_dir / "lama_input.png", image_dir / f"{number:03d}.png")
            shutil.copy2(page_dir / "lama_mask.png", mask_dir / f"{number:03d}.png")
        print(f"LaMa: {len(numbers)}ページを一括修復中...", flush=True)
        removal.run_lama_batch(image_dir, mask_dir, out_dir, self.settings)
        for number in numbers:
            result = out_dir / f"{number:03d}.png"
            page_dir = pages_dir / f"{number:03d}"
            if result.is_file():
                shutil.copy2(result, page_dir / "background.png")
                # 完了を記録し、次回以降のキャッシュ再実行でLaMaが再燃しないようにする
                marker = page_dir / "done.json"
                try:
                    record = json.loads(marker.read_text(encoding="utf-8"))
                    record["lama_pending"] = False
                    marker.write_text(json.dumps(record, ensure_ascii=False),
                                      encoding="utf-8")
                except Exception:
                    pass
