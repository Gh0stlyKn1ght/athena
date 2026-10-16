"""Capability D: Ground-Truth Citation Verifier & Hallucination Gate."""

from __future__ import annotations

from athena.git_inspector import RawRangeExtraction
from athena.models import (
    EvidenceRecord,
    LogicalChangeStory,
    PotentialBreakingChangeFinding,
)


def verify_and_hydrate_stories(
    stories: list[LogicalChangeStory],
    extraction: RawRangeExtraction,
) -> tuple[list[LogicalChangeStory], int]:
    """Validate evidence_ids on LogicalChangeStory items against ground-truth evidence_registry.
    Drops ungrounded stories that reference neither valid evidence_ids nor real changed files/commits.
    Returns (verified_stories, stripped_claims_count).
    """
    registry = extraction.evidence_registry
    valid_files = {f.file_path: f for f in extraction.changed_files}
    valid_commits = {c.commit_hash: c for c in extraction.commits}
    valid_short_commits = {c.short_hash: c.commit_hash for c in extraction.commits}

    verified: list[LogicalChangeStory] = []
    stripped_count = 0

    for story in stories:
        # Filter affected_files to only real changed files in revision range
        clean_files: list[str] = []
        for fpath in story.affected_files:
            norm = fpath.replace("\\", "/").strip()
            if norm in valid_files:
                clean_files.append(norm)
            else:
                stripped_count += 1

        # Filter commit_hashes to only real commits in revision range
        clean_commits: list[str] = []
        for ch in story.commit_hashes:
            ch_clean = ch.strip()
            if ch_clean in valid_commits:
                clean_commits.append(ch_clean)
            elif ch_clean in valid_short_commits:
                clean_commits.append(valid_short_commits[ch_clean])
            else:
                stripped_count += 1

        # Validate evidence_ids against ground-truth registry
        clean_ev_ids: list[str] = []
        for eid in story.evidence_ids:
            if eid in registry:
                clean_ev_ids.append(eid)
            else:
                stripped_count += 1

        # If LLM omitted evidence_ids but cited valid changed files, bind file evidence deterministically
        if not clean_ev_ids and clean_files:
            for cf in clean_files:
                clean_ev_ids.extend(valid_files[cf].evidence_ids[:2])

        # Deduplicate while preserving order
        clean_ev_ids = list(dict.fromkeys(clean_ev_ids))
        if not clean_ev_ids:
            stripped_count += 1
            continue

        # Backfill affected_files and commit_hashes from verified evidence if empty
        hydrated_ev: list[EvidenceRecord] = [registry[eid] for eid in clean_ev_ids]
        if not clean_files:
            clean_files = list(dict.fromkeys(ev.file_path for ev in hydrated_ev if ev.file_path in valid_files))
        if not clean_commits:
            clean_commits = list(dict.fromkeys(ev.commit_hash for ev in hydrated_ev if ev.commit_hash in valid_commits))

        story.affected_files = clean_files
        story.commit_hashes = clean_commits
        story.evidence_ids = clean_ev_ids
        story.evidence = hydrated_ev
        if not story.observed_facts:
            story.observed_facts = [
                f"Modified {', '.join(clean_files[:4]) or 'repository files'} ({len(hydrated_ev)} verified evidence record(s))."
            ]
        verified.append(story)

    return verified, stripped_count


def verify_and_hydrate_breaking_findings(
    findings: list[PotentialBreakingChangeFinding],
    extraction: RawRangeExtraction,
) -> tuple[list[PotentialBreakingChangeFinding], int]:
    """Validate PotentialBreakingChangeFinding items against ground-truth evidence_registry.
    Enforces framing as 'potential breaking changes requiring human review' and strips ungrounded claims.
    """
    registry = extraction.evidence_registry
    valid_files = {f.file_path: f for f in extraction.changed_files}

    verified: list[PotentialBreakingChangeFinding] = []
    stripped_count = 0

    for finding in findings:
        norm_file = finding.file_path.replace("\\", "/").strip()
        clean_ev_ids = [eid for eid in finding.evidence_ids if eid in registry]
        invalid_ev_count = len(finding.evidence_ids) - len(clean_ev_ids)
        stripped_count += invalid_ev_count

        if not clean_ev_ids and norm_file in valid_files:
            clean_ev_ids = list(valid_files[norm_file].evidence_ids[:2])

        clean_ev_ids = list(dict.fromkeys(clean_ev_ids))
        if not clean_ev_ids:
            stripped_count += 1
            continue

        finding.file_path = norm_file
        finding.evidence_ids = clean_ev_ids
        finding.evidence = [registry[eid] for eid in clean_ev_ids]

        # Enforce mandatory framing: potential breaking change requiring human review
        if "requires human review" not in finding.title.lower() and "potential" not in finding.title.lower():
            finding.title = f"Potential Breaking Change: {finding.title} (Requires Human Review)"

        if not finding.uncertainty_notes:
            finding.uncertainty_notes = (
                "Static diff and AST analysis identifies structural contract changes; "
                "runtime call behavior and external consumer impact require human review."
            )

        if not finding.potential_risks_requiring_verification:
            finding.potential_risks_requiring_verification = [
                "Requires human review to verify whether downstream callers depend on the previous contract."
            ]

        verified.append(finding)

    return verified, stripped_count
