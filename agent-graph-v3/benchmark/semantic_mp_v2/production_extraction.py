"""Bounded document extraction with explicit uncovered spans and disk replay.

No model and no inferred coverage. A recognized line is not a certificate for
the surrounding document. Markdown quotations retain reported stance. Arbitrary
prose, tables, source code and biological entailment remain unresolved.
"""
from dataclasses import asdict, dataclass, replace
import hashlib
import json
from pathlib import Path
import re

from .extraction import Extractor, Fact, Result, alignment_key, deterministic, unit_value

VERSION = 'semantic-production-extraction-v1'


@dataclass
class AttributedFact(Fact):
    attributed_source: str | None = None
    source_attribution_status: str = 'unresolved'


@dataclass
class DocumentResult(Result):
    unresolved_spans: list | None = None


def fact(subject, predicate, value, unit, scope, stance, text, source=None):
    return AttributedFact(subject, predicate, value, unit, scope, stance, 'fact',
        [{'start': 0, 'end': len(text), 'quote': text}],
        alignment_key(subject, predicate, unit, scope, 'fact'),
        attributed_source=source,
        source_attribution_status='explicit' if source else 'own_assertion' if stance == 'asserted' else 'unresolved')


def parse_line(text):
    """Only full, anchored constructions; never strip negation or a qualifier."""
    body = text
    stance, source = 'asserted', None
    if body.startswith('> '):
        stance, source, body = 'quoted', 'markdown_blockquote', body[2:]
    for prefix, state in [('Rejected: ', 'rejected'), ('Quoted: ', 'quoted'),
                          ('Hypothetical: ', 'hypothetical'), ('Uncertain: ', 'uncertain')]:
        if body.startswith(prefix):
            if stance != 'asserted':
                return None  # Nested commitment requires interpretation.
            stance, body = state, body[len(prefix):]
            break
    # Presentation wrappers only around the entire proposition.
    body = re.sub(r'^[-*] ', '', body)
    if body.startswith('**') and body.endswith('**') and body.count('**') == 2:
        body = body[2:-2]
    if not re.search(r'\w', body) and any(c in body for c in '`"\'#:>'):
        return None  # A quotation/code delimiter is not semantic emptiness.
    scientific = re.fullmatch(r'Claim (\d+): (?:\*\*)?(SUPPORTED|CONTRADICTED|INSUFFICIENT|NOT ENOUGH INFO|NEI)(?:\*\*)?\.?', body)
    if scientific:
        value = scientific[2].lower()
        if value in ('not enough info', 'nei'):
            value = 'insufficient'
        return [fact('claim ' + scientific[1], 'verdict', value, '', '', stance, text, source)]
    reported = re.fullmatch(r'Verified: (Paper [A-Z]) (supports|contradicts) Claim (\d+) \((.+)\)', body, re.S)
    if reported:
        # A paper-specific verdict is not silently merged into the task-wide verdict.
        return [fact('claim ' + reported[3], 'verdict',
                     'supported' if reported[2] == 'supports' else 'contradicted', '',
                     reported[1] + '; claim text: ' + reported[4], stance, text, reported[1])]
    defect = re.fullmatch(r'Review of ([\w.]+): no defect corresponding to "([^"\n]+)" is present\. The current implementation already satisfies the expected behavior in this case\.', body)
    if defect:
        return [fact(defect[1], 'code_defect', 'absent', '', defect[2], stance, text, source)]
    explicit_defect = re.fullmatch(r'Defect in ([\w./]+): (present|absent)\.?', body)
    if explicit_defect:
        return [fact(explicit_defect[1], 'code_defect', explicit_defect[2], '', '', stance, text, source)]
    # Explicit units and explicit dates/scopes only. Currency is never inferred
    # from an absent dollar sign, or from the unit of a neighboring claim.
    numeric = re.fullmatch(r'([A-Za-z][A-Za-z0-9 ,_()/.-]*?): ([+-]?\d[\d,]*(?:\.\d+)?) (USD millions|USD thousands|USD|million|percent|%|ratio|count)\.?', body)
    if numeric and re.search(r'\b(?:not|no|never|if|unless|may|might|perhaps|reported|rejected|quoted|hypothetical|uncertain|incorrect|wrong|example)\b', numeric[1], re.I):
        return None
    if numeric:
        value, unit = unit_value(numeric[2], numeric[3])
        return [fact(numeric[1], 'amount', value, unit, '', stance, text, source)]
    attribution = re.fullmatch(r'Verified from ([A-Za-z /_.-]+): (.+)', body)
    if attribution and source is None:
        source = attribution[1]
    result = deterministic(body)
    if result is not None and result.complete:
        # Evidence refers to the original full line, including all stance markers.
        return [AttributedFact(**(asdict(p) | {
            'stance': stance if stance != 'asserted' else p.stance,
            'evidence': [{'start': 0, 'end': len(text), 'quote': text}],
            'attributed_source': source,
            'source_attribution_status': 'explicit' if source else 'unresolved'})) for p in result.propositions]
    return None


def extract_document(text):
    claims, uncovered = [], []
    for match in re.finditer(r'[^\n]+', text):
        raw = match.group()
        line = raw.strip()
        if not line:
            continue
        start = match.start() + len(raw) - len(raw.lstrip())
        parsed = parse_line(line)
        if parsed is None:
            uncovered.append({'start': start, 'end': start + len(line), 'quote': line,
                              'reason': 'unrecognized_complete_clause'})
        else:
            for claim in parsed:
                claims.append(replace(claim, evidence=[dict(s, start=s['start'] + start,
                    end=s['end'] + start) for s in claim.evidence]))
    return DocumentResult(claims, not uncovered, 'partial' if uncovered else 'assessed',
        [{'code': 'uncovered_document_spans'}] if uncovered else [],
        'deterministic_document_grammar', True, uncovered)


class ProductionExtractor(Extractor):
    """Offline-only; cache identities include implementation and exact input."""
    def __init__(self, cache_dir=None):
        super().__init__()
        self.cache_dir = Path(cache_dir) if cache_dir is not None else None
        self.disk_hits = 0
        self._configuration = None

    def configuration(self):
        if self._configuration is not None:
            return self._configuration
        config = super().configuration()
        config.update(production_version=VERSION, model=None, live_enabled=False,
                      coverage='all nonblank input lines must be recognized',
                      implementation_sha256={p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in [Path(__file__), Path(__file__).with_name('extraction.py'),
                                    Path(__file__).parent.parent / 'semantic_mp/extraction.py']})
        self._configuration = config
        return config

    def extract(self, text):
        if not isinstance(text, str):
            raise TypeError('observable text must be a string')
        identity = {'configuration': self.configuration(), 'text': text}
        key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        if key in self.cache:
            self.hits += 1
            return self.cache[key]
        path = self.cache_dir / (key + '.json') if self.cache_dir is not None else None
        if path is not None and path.exists():
            saved = json.loads(path.read_text())
            if saved['identity'] != identity:
                raise ValueError('extraction cache identity mismatch')
            payload = saved['result']
            expected = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
            if saved['result_sha256'] != expected:
                raise ValueError('extraction cache content mismatch')
            payload['propositions'] = [AttributedFact(**p) for p in payload['propositions']]
            result = DocumentResult(**payload)
            self.disk_hits += 1
        else:
            result = extract_document(text)
            if path is not None:
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                payload = asdict(result)
                record = {'identity': identity, 'result': payload,
                          'result_sha256': hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()}
                tmp = path.with_suffix('.tmp')
                tmp.write_text(json.dumps(record, sort_keys=True))
                tmp.replace(path)
        self.cache[key] = result
        return result
