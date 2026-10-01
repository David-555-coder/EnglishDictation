from __future__ import annotations

import hashlib
import re
from dataclasses import asdict
from pathlib import Path

from docx import Document

from src.models.article import Article, Paragraph, RunStyle, TextSegment, WordItem
from src.models.rules import RecognitionRules
from src.parser.word_structure import iter_paragraphs, iter_runs, theme_colors, is_heading
from src.utils.color_utils import run_is_target, effective_format
from src.utils.text_utils import ENGLISH_TARGET_RE, extract_chinese_note, normalize_answer, sentence_bounds


class DocxParser:
    """一次性解析 Word；绝不修改或保存原始文件。"""

    def parse(self, path: str | Path, rules=None, source_path=None) -> Article:
        document_path = Path(path).resolve()
        source = Path(source_path or path).resolve()
        rules = rules or RecognitionRules()
        if document_path.suffix.casefold() != ".docx":
            raise ValueError("只支持 .docx Word 文件")
        document = Document(str(document_path))
        colors = theme_colors(document)
        paragraphs: list[Paragraph] = []
        items: list[WordItem] = []
        for index, paragraph in enumerate(iter_paragraphs(document)):
            parsed, found = self._parse_paragraph(source, index, paragraph, rules, colors)
            paragraphs.append(parsed)
            items.extend(found)
        return Article(str(source), source.name, paragraphs, items,
                       {"rules": asdict(rules), "document_path": str(document_path), "parser_version": 3})

    def _parse_paragraph(self, source: Path, paragraph_index: int, paragraph, rules, colors) -> tuple[Paragraph, list[WordItem]]:
        runs = list(iter_runs(paragraph))
        text = "".join(run.text for run in runs)
        flags: list[bool] = []
        styles: list[RunStyle] = []
        for run in runs:
            style = self._style(run, colors)
            target = run_is_target(run, rules.format_mode, colors) and not (rules.skip_headings and is_heading(paragraph))
            flags.extend([target] * len(run.text))
            styles.extend([style] * len(run.text))
        if len(flags) < len(text):
            flags.extend([False] * (len(text) - len(flags)))
            styles.extend([RunStyle()] * (len(text) - len(styles)))

        if rules.format_mode == "any":
            ranges = ([] if rules.skip_headings and is_heading(paragraph)
                      else self._adjacent_chinese_ranges(text, runs))
        else:
            ranges = []
            cursor = 0
            while cursor < len(text):
                if not flags[cursor]:
                    cursor += 1
                    continue
                end = cursor + 1
                while end < len(text) and flags[end]:
                    end += 1
                # 只在有格式的连续区域中寻找英文，因此中文注释永远不会被挖掉。
                for match in ENGLISH_TARGET_RE.finditer(text[cursor:end]):
                    start, stop = cursor + match.start(), cursor + match.end()
                    # 格式与中文条件由本篇规则决定；中文本身始终保留。
                    if match.group().strip() and (not rules.require_chinese or extract_chinese_note(text, stop)):
                        ranges.append((start, stop))
                cursor = end

        items: list[WordItem] = []
        segments: list[TextSegment] = []
        cursor = 0
        for ordinal, (start, end) in enumerate(ranges):
            if start > cursor:
                segments.append(TextSegment(text[cursor:start], style=styles[cursor] if cursor < len(styles) else RunStyle()))
            answer = text[start:end]
            sentence_start, sentence_end = sentence_bounds(text, start, end)
            item_id = hashlib.sha1(f"{source}|{paragraph_index}|{start}|{end}|{answer}".encode("utf-8")).hexdigest()[:20]
            style = styles[start] if start < len(styles) else RunStyle()
            item = WordItem(
                id=item_id, answer=answer, normalized_answer=normalize_answer(answer),
                chinese=extract_chinese_note(text, end), sentence=text[sentence_start:sentence_end].strip(),
                paragraph_text=text, filename=source.name, source_path=str(source),
                paragraph_index=paragraph_index, start=start, end=end,
                sentence_start=sentence_start, sentence_end=sentence_end, style=style,
            )
            items.append(item)
            segments.append(TextSegment(answer, target_id=item_id, style=style))
            cursor = end
        if cursor < len(text) or not segments:
            segments.append(TextSegment(text[cursor:], style=styles[cursor] if cursor < len(styles) else RunStyle()))
        parsed = Paragraph(paragraph_index, text, segments, int(paragraph.alignment) if paragraph.alignment is not None else None,
                           getattr(getattr(paragraph, "style", None), "name", None))
        return parsed, items

    @staticmethod
    def _adjacent_chinese_ranges(text, runs) -> list[tuple[int, int]]:
        """宽松模式：优先取中文释义前的独立 Word run。

        无格式文本没有可靠的短语边界。如果上下文、答案和释义全在同一 run，
        保守地只取最后一个英文词，避免把整句上下文误挖掉。这类少数多词短语
        可在“手动调整挖空”中合并。
        """
        spans = []
        position = 0
        for run in runs:
            stop = position + len(run.text)
            spans.append((position, stop))
            position = stop

        ranges = []
        for match in ENGLISH_TARGET_RE.finditer(text):
            broad_start, stop = match.start(), match.end()
            if not extract_chinese_note(text, stop):
                continue
            run_start = broad_start
            for start, end in spans:
                if start <= stop - 1 < end:
                    run_start = start
                    break
            if run_start > broad_start:
                candidate_start = run_start
                while candidate_start < stop and text[candidate_start].isspace():
                    candidate_start += 1
                if not ENGLISH_TARGET_RE.fullmatch(text[candidate_start:stop]):
                    candidate_start = stop
            else:
                # ENGLISH_TARGET_RE 会把整个空格相连的句段视为一次匹配，再取其最后一词。
                tail = text[broad_start:stop]
                single_words = list(re.finditer(r"[A-Za-z]+(?:[’'][A-Za-z]+|[-‑][A-Za-z]+)*", tail))
                candidate_start = stop
                if single_words:
                    candidate_start = broad_start + single_words[-1].start()
            if candidate_start < stop:
                ranges.append((candidate_start, stop))
        return ranges

    @staticmethod
    def _style(run, colors) -> RunStyle:
        bold, color = effective_format(run, colors)
        return RunStyle(
            font_name=run.font.name,
            font_size_pt=run.font.size.pt if run.font.size else None,
            bold=bold, italic=bool(run.italic), underline=bool(run.underline),
            color_rgb=str(color) if color else None,
        )
