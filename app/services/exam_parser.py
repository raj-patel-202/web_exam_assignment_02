from dataclasses import dataclass


class ExamParseError(ValueError):
    """Raised when an uploaded TXT exam does not match the supported format."""


@dataclass(frozen=True)
class ParsedOption:
    label: str
    text: str


@dataclass(frozen=True)
class ParsedQuestion:
    text: str
    options: tuple[ParsedOption, ...]
    correct_label: str


def parse_exam_text(source: str) -> list[ParsedQuestion]:
    source = source.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    lines = source.split("\n")
    parsed: list[ParsedQuestion] = []

    question_lines: list[str] = []
    options: dict[str, str] = {}
    correct_label: str | None = None
    question_start_line = 0
    reading_question = False

    def finish_question(next_line_number: int) -> None:
        nonlocal question_lines, options, correct_label, reading_question
        if not question_lines and not options and correct_label is None:
            return

        question_text = "\n".join(question_lines).strip()
        number = len(parsed) + 1
        if not question_text:
            raise ExamParseError(
                f"Question {number} (near line {question_start_line or next_line_number}) has no text."
            )

        missing = [label for label in "ABCD" if not options.get(label, "").strip()]
        if missing:
            raise ExamParseError(
                f"Question {number} is missing option(s): {', '.join(missing)}."
            )
        if correct_label not in options:
            raise ExamParseError(
                f"Question {number} must end with A: followed by A, B, C, or D."
            )

        parsed.append(
            ParsedQuestion(
                text=question_text,
                options=tuple(
                    ParsedOption(label=label, text=options[label].strip())
                    for label in "ABCD"
                ),
                correct_label=correct_label,
            )
        )
        question_lines = []
        options = {}
        correct_label = None
        reading_question = False

    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.rstrip()
        if line.startswith("Q:"):
            finish_question(line_number)
            question_start_line = line_number
            question_lines = [line[2:].strip()]
            reading_question = True
            continue

        if len(line) >= 2 and line[0] in "ABCD" and line[1] == ")":
            if not question_lines:
                raise ExamParseError(
                    f"Option {line[0]} on line {line_number} appears before a Q: line."
                )
            label = line[0]
            if label in options:
                raise ExamParseError(
                    f"Question {len(parsed) + 1} contains option {label} more than once."
                )
            options[label] = line[2:].strip()
            reading_question = False
            continue

        if line.startswith("A:"):
            if not question_lines:
                raise ExamParseError(
                    f"Answer key on line {line_number} appears before a Q: line."
                )
            value = line[2:].strip().upper()
            if value not in {"A", "B", "C", "D"}:
                raise ExamParseError(
                    f"Invalid answer key on line {line_number}; expected A, B, C, or D."
                )
            correct_label = value
            reading_question = False
            continue

        if reading_question:
            question_lines.append(line)
        elif line.strip():
            raise ExamParseError(
                f"Unexpected content on line {line_number}: {line.strip()[:60]}"
            )

    finish_question(len(lines) + 1)
    if not parsed:
        raise ExamParseError("The file does not contain any valid Q: question blocks.")
    return parsed


def decode_exam_upload(raw: bytes) -> str:
    if not raw:
        raise ExamParseError("The uploaded TXT file is empty.")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ExamParseError("The exam file must use UTF-8 text encoding.") from exc

