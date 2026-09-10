"""Explicit migration of frozen request identities, never runtime fallback."""

import hashlib
from dataclasses import replace

from agents.deep_chain_native import (
    EXPECTED_CHAIN_RANKING_RULE_VERSION,
    REQUEST_SCHEMA_IDENTITIES_TAG,
    EnvelopeKind,
    decode_envelope,
    encode_envelope,
)


def migrate_frozen_request(raw, target=EXPECTED_CHAIN_RANKING_RULE_VERSION):
    identities = tuple(f"puyo.expected_chain_ranking.v{i}".encode() for i in (2, 3, 4))
    assert target.encode() in identities
    before = decode_envelope(raw)
    assert before.kind == EnvelopeKind.REQUEST
    payload = before.section_map[REQUEST_SCHEMA_IDENTITIES_TAG].payload
    matches = [identity for identity in identities if payload.count(identity) == 1]
    assert len(matches) == 1
    old = matches[0]
    sections = tuple(
        replace(section, payload=section.payload.replace(old, target.encode()))
        if section.tag == REQUEST_SCHEMA_IDENTITIES_TAG else section
        for section in before.sections
    )
    encoded = encode_envelope(before.kind, before.request_id, sections,
                              schema_major=before.schema_major, schema_minor=before.schema_minor)
    after = decode_envelope(encoded)
    assert (before.kind, before.request_id, before.schema_major, before.schema_minor) == (
        after.kind, after.request_id, after.schema_major, after.schema_minor)
    for a, b in zip(before.sections, after.sections, strict=True):
        assert (a.tag, a.version) == (b.tag, b.version)
        assert a.payload == (b.payload.replace(target.encode(), old)
                             if a.tag == REQUEST_SCHEMA_IDENTITIES_TAG else b.payload)
    return encoded, {
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "effective_sha256": hashlib.sha256(encoded).hexdigest(),
        "from_identity": old.decode(), "to_identity": target,
        "only_ranking_identity_changed": True,
        "all_other_section_bytes_and_request_id_unchanged": True,
    }
