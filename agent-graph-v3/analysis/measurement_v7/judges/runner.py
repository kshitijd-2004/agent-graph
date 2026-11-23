"""
Judge runner: one independent annotation job per packet item.

A "job" is (judge, item_token, prompt_version).  Successful responses are
cached to ``<cache_dir>/<job_key>.json`` so a repeated run never re-issues a
paid call; the cache entry carries the full provenance the brief requires:

  provider, exact model identifier, prompt version + hash, inference params,
  input hash, output hash, timestamp, token usage, cost (where available),
  retry count, and any failure record.

A job that FAILS after retries is NOT replaced by a same-family model and
NOT fabricated — it is written to the failure ledger as
``status: failed`` with the error, and the item is excluded from agreement
counts (it is a missing annotation, not a negative label).
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import time
from typing import Any, Dict, List, Optional

from providers import JudgeConfig


def _host_of(url: Optional[str]) -> Optional[str]:
    import urllib.parse
    return (urllib.parse.urlparse(url).netloc if url else None)


class JudgeClient:
    """Deterministic, cache-backed, provenance-logging judge client.

    Transport: the Anthropic-messages API (``anthropic`` provider class)
    via urllib, or the OpenAI chat-completions API (``openai_compatible``).
    No third-party SDK is required; the call is a single POST so the exact
    bytes sent and received are what get hashed.
    """

    def __init__(self, cfg: JudgeConfig,
                 max_retries: int = 3, retry_wait_s: float = 10.0):
        self.cfg = cfg
        self.max_retries = max_retries
        self.retry_wait_s = retry_wait_s
        os.makedirs(cfg.cache_dir, exist_ok=True)
        self.call_log_path = os.path.join(cfg.cache_dir, 'call_log.jsonl')

    # ── cache ────────────────────────────────────────────────────────────────
    def _cache_key(self, prompt: Dict[str, str], judge_name: str,
                   item_token: str) -> str:
        ident = (f'{judge_name}|{prompt["version"]}|{item_token}|'
                 f'{prompt["prompt_sha256"]}|{self.cfg.model}')
        return hashlib.sha256(ident.encode()).hexdigest()

    def _cache_path(self, key: str) -> str:
        return os.path.join(self.cfg.cache_dir, key + '.json')

    def _load_cache(self, key: str) -> Optional[Dict[str, Any]]:
        p = self._cache_path(key)
        if os.path.exists(p):
            try:
                return json.load(open(p))
            except Exception:
                return None
        return None

    def _save_cache(self, key: str, rec: Dict[str, Any]) -> None:
        with open(self._cache_path(key), 'w') as f:
            json.dump(rec, f, indent=2, sort_keys=True)

    def _log_call(self, rec: Dict[str, Any]) -> None:
        with open(self.call_log_path, 'a') as f:
            f.write(json.dumps(rec, sort_keys=True) + '\n')

    # ── transport ────────────────────────────────────────────────────────────
    def _post_anthropic(self, prompt: Dict[str, str]) -> Dict[str, Any]:
        import urllib.request, urllib.error
        tok = self.cfg.credential()
        url = self.cfg.base_url.rstrip('/') + '/v1/messages'
        body = {
            'model': self.cfg.model,
            'max_tokens': self.cfg.max_tokens,
            'temperature': self.cfg.temperature,
            'system': prompt['system'],
            'messages': [{'role': 'user', 'content': prompt['user']}],
        }
        if self.cfg.top_p is not None:
            body['top_p'] = self.cfg.top_p
        if self.cfg.extra.get('seed') is not None:
            body['seed'] = self.cfg.extra.get('seed')
        data = json.dumps(body).encode()
        req = urllib.request.Request(url, data=data, headers={
            'x-api-key': tok, 'authorization': f'Bearer {tok}',
            'anthropic-version': '2023-06-01',
            'content-type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise RuntimeError(f'HTTP {e.code}: {e.read()[:300]!r}')

    def _post_openai(self, prompt: Dict[str, str]) -> Dict[str, Any]:
        import urllib.request, urllib.error
        tok = self.cfg.credential()
        url = self.cfg.base_url.rstrip('/') + '/chat/completions'
        body = {
            'model': self.cfg.model,
            'max_tokens': self.cfg.max_tokens,
            'temperature': self.cfg.temperature,
            'messages': [{'role': 'system', 'content': prompt['system']},
                         {'role': 'user', 'content': prompt['user']}],
        }
        data = json.dumps(body).encode()
        req = urllib.request.Request(url, data=data, headers={
            'authorization': f'Bearer {tok}',
            'content-type': 'application/json'})
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.loads(r.read())

    def _post(self, prompt: Dict[str, str]) -> Dict[str, Any]:
        if self.cfg.provider == 'anthropic':
            return self._post_anthropic(prompt)
        if self.cfg.provider == 'openai_compatible':
            return self._post_openai(prompt)
        raise ValueError(f'unknown provider {self.cfg.provider}')

    @staticmethod
    def _extract_text(resp: Dict[str, Any]) -> str:
        if 'content' in resp and isinstance(resp['content'], list):
            return ''.join(c.get('text', '') for c in resp['content']
                           if isinstance(c, dict))
        if 'choices' in resp:
            return resp['choices'][0]['message']['content']
        raise RuntimeError('no extractable text in response: '
                           + list(resp.keys()))

    @staticmethod
    def _usage(resp: Dict[str, Any]) -> Dict[str, Any]:
        u = resp.get('usage') or {}
        out = {k: u.get(k) for k in
               ('prompt_tokens', 'completion_tokens', 'total_tokens',
                'input_tokens', 'output_tokens', 'cache_read_input_tokens',
                'cache_creation_input_tokens')}
        cost = u.get('cost_usd')
        if cost is not None:
            out['cost_usd'] = cost
        return out

    # ── one job ──────────────────────────────────────────────────────────────
    def annotate(self, prompt: Dict[str, str], judge_name: str,
                 item_token: str, input_hash: str) -> Dict[str, Any]:
        """Run one annotation job.  Returns a record with provenance.

        On success the record is cached (``status: ok`` or ``cache_hit``); on
        final failure it is written to the call log as ``status: failed`` and
        returned with that status — never a fabricated label.
        """
        if not self.cfg.is_configured():
            rec = {'status': 'unconfigured', 'judge': judge_name,
                   'model': self.cfg.model, 'provider': self.cfg.provider,
                   'note': ('credential/endpoint not configured; this judge '
                             'is a config requirement, not a live annotation')}
            self._log_call(rec)
            return rec
        key = self._cache_key(prompt, judge_name, item_token)
        cached = self._load_cache(key)
        if cached is not None:
            cached['cache_hit'] = True
            return cached
        retries = 0
        last_err: Optional[str] = None
        t0 = time.time()
        while retries <= self.max_retries:
            try:
                resp = self._post(prompt)
                text = self._extract_text(resp)
                out_hash = hashlib.sha256(text.encode()).hexdigest()
                rec = {
                    'status': 'ok',
                    'judge': judge_name,
                    'provider': self.cfg.provider,
                    'provider_host': _host_of(self.cfg.base_url),
                    'model': self.cfg.model,
                    'prompt_version': prompt['version'],
                    'prompt_sha256': prompt['prompt_sha256'],
                    'inference_params': self.cfg.inference_params(),
                    'input_sha256': input_hash,
                    'output_sha256': out_hash,
                    'timestamp': datetime.datetime.now(
                        datetime.timezone.utc).isoformat(),
                    'retries': retries,
                    'usage': self._usage(resp),
                    'text': text,
                    'elapsed_s': round(time.time() - t0, 2),
                }
                self._save_cache(key, rec)
                self._log_call({k: v for k, v in rec.items()
                                if k != 'text'})
                return rec
            except Exception as e:           # noqa: BLE001
                last_err = f'{type(e).__name__}: {e}'
                retries += 1
                if retries <= self.max_retries:
                    time.sleep(self.retry_wait_s)
        rec = {'status': 'failed', 'judge': judge_name,
               'model': self.cfg.model, 'provider': self.cfg.provider,
               'retries': retries, 'error': last_err,
               'timestamp': datetime.datetime.now(
                   datetime.timezone.utc).isoformat()}
        self._log_call(rec)
        return rec


def run_jobs(judge_name: str, cfg: JudgeConfig, jobs: List[Dict[str, Any]]
             ) -> List[Dict[str, Any]]:
    """Run a batch of jobs.  A job is
    ``{'prompt': {...}, 'item_token': ..., 'input_hash': ...}``."""
    client = JudgeClient(cfg)
    out: List[Dict[str, Any]] = []
    for j in jobs:
        rec = client.annotate(j['prompt'], judge_name, j['item_token'],
                              j['input_hash'])
        rec['item_token'] = j['item_token']
        out.append(rec)
    return out
