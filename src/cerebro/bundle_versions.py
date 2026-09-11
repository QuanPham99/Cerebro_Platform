from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from .bundle import load_validated_bundle
from .generation import activate_bundle, bundle_digest
from .models import (
    BundleVersionCatalog,
    BundleVersionSummary,
    ReviewRecord,
    SemanticBundle,
    SemanticObject,
)
from .retrieval import SemanticRetriever


class BundleVersionNotFound(LookupError):
    pass


class BundleVersionInvalid(ValueError):
    pass


class BundleDefaultLocked(RuntimeError):
    pass


class BundleVersionProtected(RuntimeError):
    pass


@dataclass(frozen=True)
class ResolvedBundleVersion:
    summary: BundleVersionSummary
    path: Path
    bundle: SemanticBundle


class BundleVersionRegistry:
    """Discovers trusted graph versions without accepting client paths."""

    def __init__(
        self,
        *,
        golden_root: Path | str,
        reviewed_root: Path | str,
        active_pointer: Path | str,
        default_change_allowed: bool = True,
    ) -> None:
        self.golden_root = Path(golden_root).resolve()
        self.reviewed_root = Path(reviewed_root).resolve()
        self.active_pointer = Path(active_pointer)
        self.default_change_allowed = default_change_allowed

    @staticmethod
    def _counts(bundle: SemanticBundle) -> tuple[dict[str, int], dict[str, int]]:
        counts: dict[str, int] = {}
        kind_counts: dict[str, int] = {}
        for obj in bundle.objects:
            counts[obj.type] = counts.get(obj.type, 0) + 1
            kind_counts[obj.profile_kind] = kind_counts.get(obj.profile_kind, 0) + 1
        return counts, kind_counts

    def _summary(
        self,
        identifier: str,
        path: Path,
        bundle: SemanticBundle,
        *,
        receipt: ReviewRecord | None,
        is_default: bool,
    ) -> BundleVersionSummary:
        counts, kind_counts = self._counts(bundle)
        parent_version = bundle.manifest_metadata.get("parent_version")
        origin = "golden" if path == self.golden_root else "definition" if parent_version else "generation"
        return BundleVersionSummary(
            id=identifier,
            name=bundle.name,
            version=bundle.version,
            origin=origin,
            is_default=is_default,
            reviewer=receipt.reviewer if receipt else None,
            reviewed_at=receipt.reviewed_at if receipt else None,
            parent_version=str(parent_version) if parent_version else None,
            counts=counts,
            kind_counts=kind_counts,
            generation_mode=bundle.generation_mode,
            source_mode=bundle.source_mode,
            provider=bundle.provider,
            model=bundle.model,
        )

    def _load_golden(self, *, active_root: Path | None = None) -> ResolvedBundleVersion:
        bundle = load_validated_bundle(self.golden_root)
        if bundle.review_state != "approved":
            raise BundleVersionInvalid("Golden bundle is not approved")
        return ResolvedBundleVersion(
            summary=self._summary(
                "golden",
                self.golden_root,
                bundle,
                receipt=None,
                is_default=active_root == self.golden_root,
            ),
            path=self.golden_root,
            bundle=bundle,
        )

    def _reviewed_path(self, identifier: str) -> Path:
        if not identifier or Path(identifier).name != identifier or identifier in {".", ".."}:
            raise BundleVersionNotFound(identifier)
        path = (self.reviewed_root / identifier).resolve()
        if path.parent != self.reviewed_root or not path.is_dir():
            raise BundleVersionNotFound(identifier)
        return path

    def _load_reviewed(
        self,
        identifier: str,
        *,
        active_root: Path | None = None,
    ) -> ResolvedBundleVersion:
        path = self._reviewed_path(identifier)
        try:
            bundle = load_validated_bundle(path)
            if bundle.review_state != "approved":
                raise BundleVersionInvalid("Saved graph is not approved")
            receipt_path = path / "approval.json"
            if not receipt_path.is_file():
                raise BundleVersionInvalid("Saved graph is missing approval.json")
            receipt = ReviewRecord.model_validate_json(receipt_path.read_text(encoding="utf-8"))
            if receipt.decision != "approve" or receipt.reviewed_digest != bundle_digest(path):
                raise BundleVersionInvalid("Saved graph digest does not match its approval receipt")
        except (OSError, ValueError, ValidationError) as exc:
            if isinstance(exc, BundleVersionInvalid):
                raise
            raise BundleVersionInvalid(str(exc)) from exc
        return ResolvedBundleVersion(
            summary=self._summary(
                identifier,
                path,
                bundle,
                receipt=receipt,
                is_default=active_root == path,
            ),
            path=path,
            bundle=bundle,
        )

    def resolve(self, identifier: str, *, active_root: Path | str | None = None) -> ResolvedBundleVersion:
        resolved_active = Path(active_root).resolve() if active_root is not None else None
        if identifier == "golden":
            return self._load_golden(active_root=resolved_active)
        return self._load_reviewed(identifier, active_root=resolved_active)

    def catalog(self, active_root: Path | str) -> BundleVersionCatalog:
        resolved_active = Path(active_root).resolve()
        versions = [self._load_golden(active_root=resolved_active).summary]
        if self.reviewed_root.is_dir():
            for path in sorted(self.reviewed_root.iterdir()):
                if not path.is_dir():
                    continue
                try:
                    versions.append(self._load_reviewed(path.name, active_root=resolved_active).summary)
                except (BundleVersionInvalid, BundleVersionNotFound):
                    continue
        defaults = [item for item in versions if item.is_default]
        history = sorted(
            (item for item in versions if not item.is_default),
            key=lambda item: item.reviewed_at or "",
            reverse=True,
        )
        return BundleVersionCatalog(
            default_id=defaults[0].id if defaults else None,
            default_change_allowed=self.default_change_allowed,
            versions=[*defaults, *history],
        )

    def graph(self, identifier: str) -> dict:
        resolved = self.resolve(identifier)
        return SemanticRetriever(resolved.bundle).graph().model_dump(mode="json")

    def object(self, identifier: str, object_id: str) -> SemanticObject | None:
        resolved = self.resolve(identifier)
        return SemanticRetriever(resolved.bundle).by_id.get(object_id)

    def delete(self, identifier: str, *, active_root: Path | str) -> None:
        if identifier == "golden":
            raise BundleVersionProtected("The golden graph cannot be deleted")
        path = self._reviewed_path(identifier)
        if Path(active_root).resolve() == path:
            raise BundleVersionProtected(
                "The active default graph cannot be deleted — set another version as default first"
            )
        shutil.rmtree(path)

    def set_default(self, identifier: str) -> tuple[ResolvedBundleVersion, dict]:
        if not self.default_change_allowed:
            raise BundleDefaultLocked("The default graph is locked by server configuration")
        resolved = self.resolve(identifier)
        payload = activate_bundle(
            resolved.path,
            trusted_bundle_path=self.golden_root,
            active_pointer=self.active_pointer,
        )
        return resolved, {**payload, "bundle_id": identifier}
