"""Regression test for spec 030 AC-2: scripts/validate_customer_questions.py's mirrored
CUSTOMER_USERS/OUT_OF_SCOPE_QUESTIONS must stay byte-identical to the TypeScript source of
truth in apps/web/src/CustomerWorkspace.tsx.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKSPACE_TSX = REPO_ROOT / "apps" / "web" / "src" / "CustomerWorkspace.tsx"
SCRIPT_PATH = REPO_ROOT / "scripts" / "validate_customer_questions.py"

_STRING_RE = re.compile(r"'((?:[^'\\]|\\.)*)'")


def _unescape(raw: str) -> str:
    return raw.replace("\\'", "'").replace('\\\\', '\\')


def _extract_users_from_tsx(source: str) -> list[dict]:
    block_match = re.search(
        r"const CUSTOMER_USERS: CustomerUser\[\] = \[(.*?)\n\]\n",
        source,
        re.DOTALL,
    )
    assert block_match, "Could not locate CUSTOMER_USERS array in CustomerWorkspace.tsx"
    block = block_match.group(1)

    user_objects = re.findall(r"\{\s*id: '.*?\n\s*\},", block, re.DOTALL)
    assert len(user_objects) == 5, f"Expected 5 customer users, found {len(user_objects)}"

    users = []
    for obj in user_objects:
        user_id = re.search(r"id: '([^']*)'", obj).group(1)
        label = re.search(r"label: '([^']*)'", obj).group(1)
        customer_id = re.search(r"customerId: '([^']*)'", obj).group(1)
        questions_match = re.search(r"questions: \[(.*?)\],\s*\}", obj, re.DOTALL)
        questions = [_unescape(m) for m in _STRING_RE.findall(questions_match.group(1))]
        users.append({"id": user_id, "label": label, "customer_id": customer_id, "questions": questions})
    return users


def _extract_out_of_scope_from_tsx(source: str) -> list[str]:
    block_match = re.search(
        r"const OUT_OF_SCOPE_QUESTIONS = \[(.*?)\]\n",
        source,
        re.DOTALL,
    )
    assert block_match, "Could not locate OUT_OF_SCOPE_QUESTIONS array in CustomerWorkspace.tsx"
    return [_unescape(m) for m in _STRING_RE.findall(block_match.group(1))]


def _load_script_module():
    spec = importlib.util.spec_from_file_location("validate_customer_questions", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_customer_users_mirror_matches_tsx_source_of_truth():
    source = WORKSPACE_TSX.read_text(encoding="utf-8")
    tsx_users = _extract_users_from_tsx(source)
    module = _load_script_module()

    assert [u["id"] for u in module.CUSTOMER_USERS] == [u["id"] for u in tsx_users]
    for script_user, tsx_user in zip(module.CUSTOMER_USERS, tsx_users):
        assert script_user["label"] == tsx_user["label"]
        assert script_user["customer_id"] == tsx_user["customer_id"]
        assert script_user["questions"] == tsx_user["questions"]


def test_out_of_scope_questions_mirror_matches_tsx_source_of_truth():
    source = WORKSPACE_TSX.read_text(encoding="utf-8")
    tsx_out_of_scope = _extract_out_of_scope_from_tsx(source)
    module = _load_script_module()

    assert module.OUT_OF_SCOPE_QUESTIONS == tsx_out_of_scope
