"""Source documents, not answer rubrics, for offline semantic measurement."""
import hashlib
import json
from pathlib import Path

VERSION = 'semantic-source-bundle-v1'


def resolve_sources(fixture_id, fixture_root):
    root = Path(fixture_root).resolve()
    fixture = (root / fixture_id).resolve()
    if fixture.parent != root:
        raise ValueError('fixture must be a direct child of fixture root')
    manifest_path = fixture / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    dataset = manifest.get('source', {}).get('dataset')
    if dataset == 'finqa':
        # The management commentary is generated from selected answers. Use the
        # underlying report/table, never required_facts or draft/attack values.
        names = ['documents/report.md']
        if manifest.get('source_priority', {}).get(names[0], 1) <= 0:
            raise ValueError('FinQA report is not designated authoritative')
    elif dataset == 'scifact':
        names = sorted(set(manifest.get('required_files', []) + manifest.get('optional_files', [])))
        if not names or any(not n.startswith('documents/') for n in names):
            raise ValueError('unexpected SciFact source layout')
    elif dataset == 'SWE-bench Verified':
        materialization = manifest.get('materialization', {})
        if materialization.get('workspace_state') != 'pre_fix_base_commit':
            raise ValueError('SWE source is not declared pre-fix')
        if (not materialization.get('base_commit') or
                materialization.get('base_commit') != manifest.get('source', {}).get('base_commit')):
            raise ValueError('SWE materialization/source commit mismatch')
        names = sorted(manifest['required_files'])
    else:
        raise ValueError('unsupported authoritative-source policy: ' + str(dataset))
    documents = []
    offset = 0
    for name in names:
        path = (fixture / name).resolve()
        if not path.is_relative_to(fixture) or not path.is_file():
            raise ValueError('missing or escaped source: ' + name)
        raw = path.read_bytes()
        text = raw.decode('utf-8')
        documents.append({'source_id': fixture_id + '/' + name, 'path': str(path),
                          'sha256': hashlib.sha256(raw).hexdigest(), 'text': text,
                          'start': offset, 'end': offset + len(text)})
        offset += len(text) + 2
    if not documents or not any(d['text'].strip() for d in documents):
        raise ValueError('empty authoritative source')
    return {'version': VERSION, 'fixture_id': fixture_id, 'dataset': dataset,
            'manifest_sha256': hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
            # SciFact pair_type encodes answer categories; it is not neutral
            # provenance and must not leak into the blinded review packet.
            'source_identity': {k: v for k, v in manifest.get('source', {}).items() if k in {
                'dataset', 'split', 'instance_id', 'page', 'url', 'doc_ids', 'repo',
                'base_commit', 'version', 'created_at'}}, 'documents': documents,
            'authoritative_text': '\n\n'.join(d['text'] for d in documents),
            # Questions/issue reports supply scope, not a factual answer key.
            'task_context': manifest.get('task_prompt', ''),
            'provenance_limit': 'Current fixture snapshot; hashes do not prove historical wire delivery. '
                                'Source documents require interpretation; no rubric verdict is imported.'}
