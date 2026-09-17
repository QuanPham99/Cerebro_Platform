"""Prove the progressive-disclosure graph API stays bounded at a scale the real
golden bundle (~72 objects) can never exercise (spec 026)."""

from __future__ import annotations

from pathlib import Path

from cerebro.bundle import BundleLoader, BundleValidator
from cerebro.retrieval import SemanticRetriever

from fixtures.generate_large_bundle import generate_large_bundle


def _load(tmp_path: Path, **kwargs):
    counts = generate_large_bundle(tmp_path, **kwargs)
    bundle = BundleLoader().load(tmp_path)
    report = BundleValidator().validate(bundle)
    assert report.valid, report.issues
    return bundle, counts


def test_synthetic_bundle_round_trips_through_loader_and_validator(tmp_path: Path):
    bundle, counts = _load(tmp_path)
    assert len(bundle.objects) == counts["total"]
    kinds = {obj.profile_kind for obj in bundle.objects}
    assert kinds == {"domain", "entity", "physical_table"}


def test_overview_tier_stays_bounded_to_domains_and_entities_regardless_of_scale(tmp_path: Path):
    bundle, counts = _load(tmp_path)
    retriever = SemanticRetriever(bundle)
    overview = retriever.graph(tier="overview")
    assert len(overview.nodes) == counts["domain"] + counts["entity"]
    assert {node.profile_kind for node in overview.nodes} == {"domain", "entity"}
    # never the full bundle, even though physical_table objects outnumber them
    assert len(overview.nodes) < counts["total"]


def test_one_hop_expand_stays_small_independent_of_total_bundle_size(tmp_path: Path):
    bundle, _counts = _load(tmp_path, domain_count=60, entities_per_domain=30)
    retriever = SemanticRetriever(bundle)
    seed = "entity.synthetic-0-0"
    expanded = retriever.graph(node_id=seed, depth=1)
    expanded_ids = {node.id for node in expanded.nodes}
    # exactly the entity, its domain, and its physical table - not proportional to
    # the ~3,600-object bundle this scale (60 domains x 30 entities x 2) produces
    assert expanded_ids == {seed, "domain.synthetic-0", "table.synthetic-0-0"}
    assert len(expanded.nodes) == 3


def test_tier_all_remains_reachable_only_as_an_explicit_opt_in(tmp_path: Path):
    bundle, counts = _load(tmp_path)
    retriever = SemanticRetriever(bundle)
    assert len(retriever.graph(tier="all").nodes) == counts["total"]
    assert len(retriever.graph().nodes) == counts["total"]  # tier=None at the retriever layer is unrestricted
    assert len(retriever.graph(tier="overview").nodes) < counts["total"]
