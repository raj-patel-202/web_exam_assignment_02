import pytest

from app.services.exam_parser import ExamParseError, parse_exam_text


VALID_EXAM = """Q: A multiline question begins here
and continues on this line.
A) First option
B) Second option
C) Third option
D) Fourth option
A: C

Q: A second question?
A) One
B) Two
C) Three
D) Four
A: A
"""


def test_parses_multiline_questions_and_answer_keys():
    questions = parse_exam_text(VALID_EXAM)
    assert len(questions) == 2
    assert questions[0].text == "A multiline question begins here\nand continues on this line."
    assert [option.label for option in questions[0].options] == ["A", "B", "C", "D"]
    assert questions[0].correct_label == "C"


def test_rejects_missing_option():
    with pytest.raises(ExamParseError, match="missing option"):
        parse_exam_text("Q: Incomplete?\nA) One\nB) Two\nC) Three\nA: A")


def test_rejects_invalid_answer_key():
    with pytest.raises(ExamParseError, match="Invalid answer key"):
        parse_exam_text("Q: Invalid?\nA) One\nB) Two\nC) Three\nD) Four\nA: E")

