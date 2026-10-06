"""Typed artifact / handoff schemas (JSON Schema, draft 2020-12). Shared by agents, verifier, API."""
from __future__ import annotations

from typing import Any

SEVERITIES = ["critical", "high", "medium", "low", "info"]
FINDING_STATES = ["UNVERIFIED", "VERIFIED", "REJECTED"]

SECURITY_FINDING: dict[str, Any] = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "minLength": 1, "maxLength": 80},
        "title": {"type": "string", "minLength": 3, "maxLength": 200},
        "severity": {"enum": SEVERITIES},
        "category": {"type": "string", "maxLength": 80},
        "file": {"type": "string", "maxLength": 500},
        "line": {"type": ["integer", "null"], "minimum": 1},
        "description": {"type": "string", "maxLength": 4000},
        "evidence": {"type": "string", "maxLength": 4000},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "remediation": {"type": "string", "maxLength": 4000},
        "references": {"type": "array", "items": {"type": "string", "maxLength": 500}},
        "package": {"type": ["string", "null"]},
        "version": {"type": ["string", "null"]},
        "priority": {"type": ["integer", "null"], "minimum": 1},
    },
    "required": ["id", "title", "severity", "category", "file", "description", "evidence", "confidence", "remediation"],
}

EVIDENCE: dict[str, Any] = {
    "type": "object",
    "properties": {
        "source": {"type": "string"},
        "url": {"type": "string"},
        "query": {"type": "string"},
        "timestamp": {"type": "string"},
        "snippet": {"type": "string"},
        "agent": {"type": "string"},
        "finding_ids": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["source", "url", "query", "timestamp", "snippet", "agent"],
}

_FINDINGS_ARRAY = {"type": "array", "items": SECURITY_FINDING}
_EVIDENCE_ARRAY = {"type": "array", "items": EVIDENCE}

PLAN_OUTPUT = {
    "type": "object",
    "properties": {
        "stack": {
            "type": "object",
            "properties": {
                "languages": {"type": "array", "items": {"type": "string"}},
                "frameworks": {"type": "array", "items": {"type": "string"}},
                "package_managers": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["languages", "frameworks", "package_managers"],
        },
        "focus_areas": {"type": "array", "items": {"type": "string"}},
        "entry_points": {"type": "array", "items": {"type": "string"}},
        "notes": {"type": "string"},
    },
    "required": ["stack", "focus_areas", "entry_points"],
}

SCAN_OUTPUT = {
    "type": "object",
    "properties": {
        "findings": _FINDINGS_ARRAY,
        "summary": {"type": "string"},
        "files_examined": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["findings", "summary"],
}

DEP_OUTPUT = {
    "type": "object",
    "properties": {
        "findings": _FINDINGS_ARRAY,
        "summary": {"type": "string"},
        "dependencies_examined": {"type": "integer", "minimum": 0},
        "advisories": {"type": "array", "items": {"type": "object"}},
    },
    "required": ["findings", "summary", "dependencies_examined"],
}

RESEARCH_OUTPUT = {
    "type": "object",
    "properties": {"evidence": _EVIDENCE_ARRAY, "summary": {"type": "string"}},
    "required": ["evidence", "summary"],
}

RISK_OUTPUT = {
    "type": "object",
    "properties": {"findings": _FINDINGS_ARRAY, "risk_summary": {"type": "string"}},
    "required": ["findings", "risk_summary"],
}

VERIFICATION_RESULT = {
    "type": "object",
    "properties": {
        "finding_id": {"type": "string"},
        "status": {"enum": ["VERIFIED", "REJECTED", "UNCERTAIN"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "evidenceScore": {"type": "number", "minimum": 0, "maximum": 1},
        "reason": {"type": "string"},
        "missingEvidence": {"type": "array", "items": {"type": "string"}},
        "recommendations": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["finding_id", "status", "confidence", "evidenceScore", "reason", "missingEvidence", "recommendations"],
}

VERIFICATION_OUTPUT = {
    "type": "object",
    "properties": {
        "results": {"type": "array", "items": VERIFICATION_RESULT},
        "verified_findings": _FINDINGS_ARRAY,
        "rejected_findings": _FINDINGS_ARRAY,
        "uncertain_findings": _FINDINGS_ARRAY,
        "summary": {"type": "object"},
    },
    "required": ["results", "verified_findings", "rejected_findings", "uncertain_findings", "summary"],
}

FIX_OUTPUT = {
    "type": "object",
    "properties": {
        "proposals": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "finding_id": {"type": "string"},
                    "summary": {"type": "string"},
                    "patch": {"type": "string", "maxLength": 100000},
                    "files": {"type": "array", "items": {"type": "string"}},
                    "risk": {"enum": ["low", "medium", "high"]},
                },
                "required": ["finding_id", "summary", "patch", "files", "risk"],
            },
        },
        "notes": {"type": "string"},
    },
    "required": ["proposals"],
}

TEST_OUTPUT = {
    "type": "object",
    "properties": {
        "applied": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"finding_id": {"type": "string"}, "status": {"enum": ["APPLIED", "FAILED", "SKIPPED"]},
                               "detail": {"type": "string"}},
                "required": ["finding_id", "status", "detail"],
            },
        },
        "tests": {
            "type": "object",
            "properties": {
                "status": {"enum": ["PASSED", "FAILED", "NOT_AVAILABLE", "SKIPPED"]},
                "command": {"type": "string"},
                "output_excerpt": {"type": "string"},
            },
            "required": ["status"],
        },
        "diff": {"type": "string"},
    },
    "required": ["applied", "tests", "diff"],
}

REPORT_OUTPUT = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "executive_summary": {"type": "string"},
        "markdown": {"type": "string", "minLength": 20},
        "stats": {"type": "object"},
    },
    "required": ["title", "executive_summary", "markdown"],
}

GENERIC_OBJECT = {"type": "object"}

# artifact type -> schema name (used when persisting)
ARTIFACT_SCHEMAS = {
    "security_findings": "SecurityFindingList",
    "evidence": "EvidenceArtifact",
    "verification": "VerificationOutput",
    "fix_proposals": "FixProposals",
    "patch": "UnifiedDiff",
    "test_report": "TestReport",
    "report": "SecurityReport",
    "plan": "ScanPlan",
    "node_output": "NodeOutput",
}
