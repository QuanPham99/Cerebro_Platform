"""Regression tests for `SemanticRetriever`'s lexical tokenizer (spec 029).

The old tokenizer (`TOKEN = re.compile(r"[a-z0-9]+")`) was ASCII-only and shredded
non-ASCII text such as Vietnamese customer questions into meaningless single-letter
fragments (e.g. "tài khoản" -> ['t', 'i']), silently breaking lexical-only retrieval
(no embedder configured) for every Vietnamese-phrased question.
"""

from __future__ import annotations

from cerebro.retrieval import _tokens


def test_tokenizer_keeps_vietnamese_words_intact():
    assert _tokens("Tôi có bao nhiêu tài khoản đang hoạt động?") == [
        "tôi",
        "có",
        "bao",
        "nhiêu",
        "tài",
        "khoản",
        "đang",
        "hoạt",
        "động",
    ]


def test_tokenizer_still_splits_english_and_underscored_identifiers():
    assert _tokens("customer_id Loan-Payment top10") == ["customer", "id", "loan", "payment", "top10"]
