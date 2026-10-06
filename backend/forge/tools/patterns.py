"""Deterministic security pattern rules (no model involved). Candidates only: agents + the verifier decide truth."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

from forge.sandbox import MAX_FILE_BYTES, is_probably_text, iter_files, read_text_limited
from forge.tools.base import ToolContext

MASK = "****"


@dataclass(frozen=True)
class Rule:
    id: str
    category: str  # secrets | insecure_api | injection | dangerous_config | crypto
    severity: str
    rx: re.Pattern[str]
    description: str
    cwe: str = ""
    secret_group: int | None = None  # regex group to mask in snippets
    exts: tuple[str, ...] = ()  # restrict to extensions (empty = all text files)
    min_entropy: float | None = None


def _r(pat: str, flags: int = 0) -> re.Pattern[str]:
    return re.compile(pat, flags)


RULES: list[Rule] = [
    # ---- secrets
    Rule("secret.aws_access_key", "secrets", "high", _r(r"\b(AKIA[0-9A-Z]{16})\b"), "AWS access key id", "CWE-798", 1),
    Rule("secret.private_key", "secrets", "critical", _r(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----"),
         "Private key committed to the repository", "CWE-321"),
    Rule("secret.github_token", "secrets", "critical", _r(r"\b(gh[pousr]_[A-Za-z0-9]{36,255})\b"), "GitHub token", "CWE-798", 1),
    Rule("secret.slack_token", "secrets", "high", _r(r"\b(xox[abprs]-[A-Za-z0-9\-]{10,})\b"), "Slack token", "CWE-798", 1),
    Rule("secret.stripe_live", "secrets", "critical", _r(r"\b(sk_live_[0-9A-Za-z]{16,})\b"), "Stripe live secret key", "CWE-798", 1),
    Rule("secret.google_api_key", "secrets", "high", _r(r"\b(AIza[0-9A-Za-z_\-]{35})\b"), "Google API key", "CWE-798", 1),
    Rule("secret.generic_assignment", "secrets", "medium",
         _r(r"""(?i)\b(?:api[_-]?key|secret[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret|passwd|password)\b\s*[:=]\s*["']([^"'\s]{8,})["']"""),
         "Hardcoded credential-like assignment", "CWE-798", 1, min_entropy=3.0),
    Rule("secret.db_url_password", "secrets", "high", _r(r"\b[a-z][a-z0-9+.\-]*://[^/\s:@]+:([^@\s/]{4,})@[^\s/]+"),
         "Connection string with embedded password", "CWE-798", 1),
    # ---- insecure APIs
    Rule("api.eval", "insecure_api", "high", _r(r"\beval\s*\("), "Dynamic code evaluation (eval)", "CWE-95",
         exts=(".py", ".js", ".ts", ".jsx", ".tsx", ".php", ".rb")),
    Rule("api.exec_py", "insecure_api", "high", _r(r"(?<![\w.])exec\s*\("), "Dynamic code execution (exec)", "CWE-95", exts=(".py",)),
    Rule("api.new_function", "insecure_api", "high", _r(r"\bnew Function\s*\("), "Function constructor evaluates strings", "CWE-95",
         exts=(".js", ".ts", ".jsx", ".tsx")),
    Rule("api.pickle_loads", "insecure_api", "high", _r(r"\bpickle\.loads?\s*\("), "Unsafe deserialization (pickle)", "CWE-502", exts=(".py",)),
    Rule("api.yaml_load", "insecure_api", "medium", _r(r"\byaml\.load\s*\((?![^)]*Loader\s*=\s*(?:yaml\.)?Safe)"),
         "yaml.load without SafeLoader", "CWE-502", exts=(".py",)),
    Rule("api.subprocess_shell", "insecure_api", "high", _r(r"subprocess\.\w+\([^)]*shell\s*=\s*True"),
         "subprocess with shell=True", "CWE-78", exts=(".py",)),
    Rule("api.os_system", "insecure_api", "medium", _r(r"\bos\.(?:system|popen)\s*\("), "Shell command execution", "CWE-78", exts=(".py",)),
    Rule("api.child_process_exec", "insecure_api", "high", _r(r"\bchild_process\b.*\bexec(?:Sync)?\s*\(|\bexec(?:Sync)?\s*\(\s*[`'\"].*\$\{"),
         "child_process.exec with interpolation", "CWE-78", exts=(".js", ".ts")),
    Rule("api.inner_html", "insecure_api", "medium", _r(r"\.innerHTML\s*=|dangerouslySetInnerHTML"), "Raw HTML injection sink (XSS)", "CWE-79",
         exts=(".js", ".ts", ".jsx", ".tsx", ".html", ".vue")),
    Rule("api.document_write", "insecure_api", "medium", _r(r"\bdocument\.write\s*\("), "document.write sink (XSS)", "CWE-79",
         exts=(".js", ".ts", ".jsx", ".tsx", ".html")),
    Rule("api.tls_verify_off", "insecure_api", "high", _r(r"verify\s*=\s*False|rejectUnauthorized\s*:\s*false|InsecureSkipVerify\s*:\s*true|NODE_TLS_REJECT_UNAUTHORIZED\s*=\s*['\"]?0"),
         "TLS certificate verification disabled", "CWE-295"),
    Rule("api.php_unserialize", "insecure_api", "high", _r(r"\bunserialize\s*\(\s*\$_(?:GET|POST|REQUEST|COOKIE)"), "Unserialize of user input", "CWE-502", exts=(".php",)),
    # ---- injection
    Rule("inj.sql_concat", "injection", "high",
         _r(r"""(?i)\b(?:execute|query|raw|exec)\s*\(\s*(?:f["']|["'][^"']*["']\s*(?:\+|%)|["'][^"']*\{[^}]*\}[^"']*["']\s*\.format)[^)]*\b(?:select|insert|update|delete|where)\b"""),
         "SQL built by string interpolation/concatenation", "CWE-89"),
    Rule("inj.sql_template_literal", "injection", "high",
         _r(r"""(?i)(?:query|execute)\s*\(\s*`[^`]*\b(?:select|insert|update|delete)\b[^`]*\$\{"""), "SQL template literal with interpolation", "CWE-89",
         exts=(".js", ".ts")),
    Rule("inj.sql_fstring", "injection", "high",
         _r(r"""(?i)\bf["'][^\n]*?\b(?:select\s[^\n]*?\sfrom\s|insert\s+into\s|update\s+\w+\s+set\s|delete\s+from\s)[^\n]*?\{"""),
         "SQL in an f-string", "CWE-89", exts=(".py",)),
    Rule("inj.php_sql_var", "injection", "high", _r(r"""(?i)mysqli?_query\s*\([^;]*\$_(?:GET|POST|REQUEST)"""), "SQL with user input (PHP)", "CWE-89", exts=(".php",)),
    Rule("inj.path_join_user", "injection", "medium", _r(r"""open\s*\(\s*(?:request\.|req\.|f["'])"""), "File opened from request-controlled path", "CWE-22",
         exts=(".py", ".js", ".ts")),
    Rule("inj.ssrf_request", "injection", "medium", _r(r"""(?:requests\.(?:get|post)|urllib\.request\.urlopen|axios\.(?:get|post)|fetch)\s*\(\s*(?:request\.|req\.(?:query|body|params))"""),
         "Outbound request to a request-controlled URL (SSRF)", "CWE-918"),
    # ---- crypto
    Rule("crypto.weak_hash", "crypto", "medium", _r(r"(?i)\b(?:md5|sha1)\s*\(|hashlib\.(?:md5|sha1)\b|createHash\(\s*['\"](?:md5|sha1)['\"]"),
         "Weak hash function", "CWE-327"),
    Rule("crypto.math_random", "crypto", "low", _r(r"Math\.random\s*\(\s*\)"), "Math.random is not cryptographically secure", "CWE-338",
         exts=(".js", ".ts", ".jsx", ".tsx")),
    Rule("crypto.py_random_token", "crypto", "low", _r(r"\brandom\.(?:random|randint|choice)\s*\(.*(?:token|secret|password|key)"),
         "Non-cryptographic RNG used for a secret", "CWE-338", exts=(".py",)),
    # ---- dangerous config
    Rule("cfg.debug_true", "dangerous_config", "medium", _r(r"^\s*DEBUG\s*=\s*True\b|app\.run\([^)]*debug\s*=\s*True"), "Debug mode enabled", "CWE-489",
         exts=(".py",)),
    Rule("cfg.cors_wildcard", "dangerous_config", "medium",
         _r(r"""(?i)allow_origins\s*=\s*\[\s*["']\*["']|Access-Control-Allow-Origin["']?\s*[:,]\s*["']\*|cors\(\s*\)|origin\s*:\s*["']\*["']"""),
         "Wildcard CORS", "CWE-942"),
    Rule("cfg.chmod_777", "dangerous_config", "medium", _r(r"chmod\s+(?:-R\s+)?0?777\b"), "World-writable permissions", "CWE-732"),
    Rule("cfg.ssh_no_hostkey", "dangerous_config", "medium", _r(r"StrictHostKeyChecking\s+no|StrictHostKeyChecking=no"), "SSH host key checking disabled", "CWE-295"),
    Rule("cfg.hardcoded_secret_key", "dangerous_config", "high", _r(r"""(?i)\bSECRET_KEY\s*=\s*["'][^"']{6,}["']"""), "Hardcoded framework secret key", "CWE-798"),
    Rule("cfg.csrf_disabled", "dangerous_config", "medium", _r(r"(?i)csrf[_-]?(?:protection|enabled)?\s*[:=]\s*(?:false|off)|@csrf_exempt|csrf\(\)\.disable\(\)"),
         "CSRF protection disabled", "CWE-352"),
    Rule("cfg.docker_root", "dangerous_config", "low", _r(r"^\s*USER\s+root\b"), "Container runs as root", "CWE-250", exts=("Dockerfile",)),
]

CATEGORIES = sorted({r.category for r in RULES})


def _entropy(s: str) -> float:
    if not s:
        return 0.0
    freq = {c: s.count(c) for c in set(s)}
    return -sum(n / len(s) * math.log2(n / len(s)) for n in freq.values())


_PLACEHOLDER = re.compile(r"(?i)^(?:x+|\*+|changeme|change[_-]?me|example|your[_-].*|<.*>|\$\{.*\}|\{\{.*\}\}|todo|none|null|test|dummy|password|secret|xxxx.*|\.\.\.)$")


def mask(line: str, m: re.Match[str], group: int | None) -> str:
    if group is None:
        return line.strip()[:240]
    s, e = m.span(group)
    val = line[s:e]
    shown = val[:4] if len(val) > 8 else val[:1]
    return (line[:s] + shown + MASK + line[e:]).strip()[:240]


_TEST_PATH = re.compile(r"(?i)(^|/)(tests?|__tests__|spec|fixtures?|examples?|docs?)/|\.test\.|\.spec\.")


def scan_file(rules: list[Rule], rel: str, p) -> list[dict[str, Any]]:
    """Run rules over one file; returns candidate dicts (secrets masked)."""
    hits: list[dict[str, Any]] = []
    name, suffix = p.name, p.suffix.lower()
    for lineno, line in enumerate(read_text_limited(p).splitlines(), 1):
        if len(line) > 1500:
            continue
        for r in rules:
            if r.exts and suffix not in r.exts and name not in r.exts:
                continue
            m = r.rx.search(line)
            if not m:
                continue
            if r.secret_group is not None:
                val = m.group(r.secret_group)
                if _PLACEHOLDER.match(val):
                    continue
                if r.min_entropy and _entropy(val) < r.min_entropy:
                    continue
            hits.append({
                "rule_id": r.id, "category": r.category, "severity_hint": r.severity, "file": rel, "line": lineno,
                "snippet": mask(line, m, r.secret_group), "cwe": r.cwe, "description": r.description,
                "in_test_path": bool(_TEST_PATH.search(rel)),
            })
    return hits


def pattern_scan(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    base = ctx.require_repo()
    cats = set(args.get("categories") or CATEGORIES)
    max_results = min(int(args.get("max_results") or 150), 400)
    rules = [r for r in RULES if r.category in cats]
    hits: list[dict[str, Any]] = []
    scanned = 0
    for rel, p in iter_files(base, args.get("glob")):
        if p.stat().st_size > MAX_FILE_BYTES or not is_probably_text(p):
            continue
        scanned += 1
        hits.extend(scan_file(rules, rel, p))
        if len(hits) >= max_results:
            return {"files_scanned": scanned, "candidates": hits[:max_results], "truncated": True, "rules": len(rules)}
    return {"files_scanned": scanned, "candidates": hits, "truncated": False, "rules": len(rules)}
