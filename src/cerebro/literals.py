"""Versioned canonical-question token spans, resolved only in local memory."""
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import re

from .provenance import QueryFault

WORDS = dict(zip("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split(), range(20)))
WORDS.update(dict(zip("twenty thirty forty fifty sixty seventy eighty ninety".split(), range(20, 100, 10))))


def spans(question):
    result = []
    i = 0
    while i < len(question):
        if question[i] in " ,;()[]{}?!":
            i += 1
            continue
        if question[i] in "\"'":
            quote, start = question[i], i + 1
            end = question.find(quote, start)
            if end < 0 or "\\" in question[start:end]:
                raise QueryFault("invalid_question_span")
            result.append((start, end))
            i = end + 1
        else:
            start = i
            while i < len(question) and question[i] not in " ,;()[]{}?!":
                i += 1
            result.append((start, i))
    return tuple(result)


def resolve_literal(ref, question, snapshot, expected=None):
    if ref.kind == "governed":
        literal = next((x for x in snapshot.governed_literals if x.literal_id == ref.literal_id), None)
        if literal is None:
            raise QueryFault("ungrounded_governed_literal")
        typ, raw = literal.data_type, literal.value
    else:
        if (ref.start, ref.end) not in spans(question):
            raise QueryFault("invalid_question_span")
        typ, raw = ref.data_type, question[ref.start:ref.end]
    if expected and typ != expected:
        raise QueryFault("literal_type_mismatch")
    try:
        text = str(raw)
        if typ == "string":
            return text
        if typ == "integer":
            if text.lower() in WORDS:
                return WORDS[text.lower()]
            if not re.fullmatch(r"[+-]?[0-9]+", text):
                raise ValueError()
            return int(text)
        if typ == "decimal":
            if not re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)", text):
                raise ValueError()
            return Decimal(text)
        if typ == "boolean":
            if text.lower() not in ("true", "false"):
                raise ValueError()
            return text.lower() == "true"
        if typ == "date":
            if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", text):
                raise ValueError()
            return date.fromisoformat(text).isoformat()
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?", text):
            raise ValueError()
        return datetime.fromisoformat(text).isoformat()
    except (ValueError, InvalidOperation):
        raise QueryFault("unparseable_question_literal") from None
