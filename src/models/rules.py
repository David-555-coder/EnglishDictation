from dataclasses import dataclass


@dataclass(frozen=True)
class RecognitionRules:
    format_mode: str = "red_or_bold"
    require_chinese: bool = True
    skip_headings: bool = True

    def __post_init__(self):
        if self.format_mode not in {"red_or_bold", "red_and_bold", "red", "bold", "any"}:
            raise ValueError("不支持的识别规则")
