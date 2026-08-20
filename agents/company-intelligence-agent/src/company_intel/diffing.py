from __future__ import annotations

from .identity import company_key, normalize_name
from .models import CompanyChange, CompanyRecord


COMPARE_FIELDS = ("name", "official_url", "description", "aliases")


def compare_companies(
    previous: list[CompanyRecord], current: list[CompanyRecord]
) -> list[CompanyChange]:
    previous_by_key = {company_key(item.name, item.official_url): item for item in previous}
    current_by_key = {company_key(item.name, item.official_url): item for item in current}
    changes: list[CompanyChange] = []

    for key in sorted(current_by_key.keys() - previous_by_key.keys()):
        item = current_by_key[key]
        renamed_from = _find_alias_match(item, previous)
        if renamed_from is not None:
            changes.append(
                CompanyChange(
                    change_type="renamed",
                    company_key=key,
                    before=renamed_from,
                    after=item,
                    changed_fields=("name",),
                )
            )
        else:
            changes.append(CompanyChange("added", key, after=item))

    renamed_previous = {
        change.before for change in changes if change.change_type == "renamed" and change.before
    }
    for key in sorted(previous_by_key.keys() - current_by_key.keys()):
        item = previous_by_key[key]
        if item not in renamed_previous:
            changes.append(CompanyChange("removed", key, before=item))

    for key in sorted(previous_by_key.keys() & current_by_key.keys()):
        before = previous_by_key[key]
        after = current_by_key[key]
        changed_fields = tuple(
            field for field in COMPARE_FIELDS if getattr(before, field) != getattr(after, field)
        )
        if changed_fields:
            change_type = "renamed" if changed_fields == ("name",) else "changed"
            changes.append(
                CompanyChange(change_type, key, before=before, after=after, changed_fields=changed_fields)
            )

    return changes


def _find_alias_match(
    current: CompanyRecord, previous: list[CompanyRecord]
) -> CompanyRecord | None:
    alias_keys = {normalize_name(alias) for alias in current.aliases}
    if not alias_keys:
        return None
    for candidate in previous:
        if normalize_name(candidate.name) in alias_keys:
            return candidate
    return None

