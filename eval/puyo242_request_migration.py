"""Explicit fixture-only identity migration; no runtime compatibility fallback."""

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
    envelope = decode_envelope(raw)
    assert envelope.kind == EnvelopeKind.REQUEST
    assert target in ("puyo.expected_chain_ranking.v2", "puyo.expected_chain_ranking.v3")
    identity = envelope.section_map[REQUEST_SCHEMA_IDENTITIES_TAG]
    old = next(name for name in (b"puyo.expected_chain_ranking.v2", b"puyo.expected_chain_ranking.v3")
               if identity.payload.count(name) == 1)
    sections = tuple(
        replace(section, payload=section.payload.replace(old, target.encode()))
        if section.tag == REQUEST_SCHEMA_IDENTITIES_TAG else section
        for section in envelope.sections
    )
    encoded = encode_envelope(envelope.kind, envelope.request_id, sections,
                              schema_major=envelope.schema_major, schema_minor=envelope.schema_minor)
    migrated = decode_envelope(encoded)
    assert (envelope.kind, envelope.request_id, envelope.schema_major, envelope.schema_minor) == (
        migrated.kind, migrated.request_id, migrated.schema_major, migrated.schema_minor)
    for before, after in zip(envelope.sections, migrated.sections, strict=True):
        assert before.tag == after.tag and before.version == after.version
        if before.tag != REQUEST_SCHEMA_IDENTITIES_TAG:
            assert before.payload == after.payload
        else:
            assert after.payload.replace(target.encode(), old) == before.payload
    receipt = {
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "effective_sha256": hashlib.sha256(encoded).hexdigest(),
        "from_identity": old.decode(), "to_identity": target,
        "only_ranking_identity_changed": True,
        "root_known_pairs_search_evaluator_execution_bytes_unchanged": True,
        "request_id_unchanged": True,
    }
    return encoded, receipt
