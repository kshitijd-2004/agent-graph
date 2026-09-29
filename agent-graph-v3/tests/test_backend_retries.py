"""Transport retries must not become model behavior or replay completed work."""
import copy
import http.client
import threading
import urllib.error
from unittest.mock import Mock

import pytest

from backend.api_backend import APIBackend, ModelTurn
from backend.hf_backend import HFBackend
from backend.errors import BackendError
from generation.stage_runner import StageRunner
from generation.runner import ScenarioRunner
from schemas import TraceEventType
from test_runner_admission import CoordinationBackend, FIXTURES, scenario


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    sleep = Mock()
    monkeypatch.setattr('generation.stage_runner.time.sleep', sleep)
    return sleep


@pytest.mark.parametrize('backend_class', [APIBackend, HFBackend])
@pytest.mark.parametrize('error,reason', [
    (TimeoutError('timed out'), 'backend_timeout'),
    (urllib.error.URLError(ConnectionRefusedError(111, 'Connection refused')), 'backend_connection_error'),
    (ConnectionResetError(104, 'Connection reset by peer'), 'backend_connection_error'),
    (http.client.RemoteDisconnected('Remote end closed connection without response'), 'backend_connection_error'),
    (urllib.error.HTTPError('http://backend', 503, 'Unavailable', {}, None), 'backend_http_error'),
])
def test_transport_then_success(backend_class, error, reason, no_sleep):
    # Skip APIBackend's constructor connection probe.
    backend = backend_class.__new__(backend_class)
    backend._lock = threading.Lock()
    backend.max_tokens = 100
    backend.reset(task='task', mcp_tools=['list_directory'])
    before = copy.deepcopy(backend._conversation)
    response = ({'content': [{'type': 'tool_use', 'id': 'c', 'name': 'list_directory', 'input': {'path': '.'}}]}
                if backend_class is APIBackend else
                {'choices': [{'message': {'tool_calls': [{'id': 'c', 'function': {'name': 'list_directory', 'arguments': '{"path":"."}'}}]}}]})
    calls = []
    def call(*args, **kwargs):
        calls.append(copy.deepcopy((args, kwargs)))
        assert backend._conversation == before
        if len(calls) == 1:
            raise error
        return response
    backend._call_api = call
    runner = StageRunner(backend)
    result = runner._generate_with_retry('same prompt', 'any')
    assert result.tool_call.name == 'list_directory'
    assert calls[0] == calls[1]
    no_sleep.assert_called_once_with(2)
    assert runner.backend_diagnostics['backend_retry_count'] == 1
    assert runner.backend_diagnostics['backend_error_type'] == reason


class FailingBackend(CoordinationBackend):
    def __init__(self, failures=0, error=None, text=False):
        super().__init__()
        self.failures = failures
        self.error = error or TimeoutError('timed out')
        self.text = text
        self.calls = 0
        self.failed_requests = []

    def generate(self, prompt='', tool_choice=None):
        self.calls += 1
        # Fail after a completed tool call, to detect replay/reset regressions.
        if self.role == 'reviewer' and self.step == 1 and self.failures:
            self.failures -= 1
            self.failed_requests.append((prompt, tool_choice))
            raise self.error
        if self.text:
            return ModelTurn(text='[ERROR] timed out')  # actual model text still violates protocol
        return super().generate(prompt, tool_choice)


def run(tmp_path, backend):
    return ScenarioRunner(llm_backend=backend, dry_run=False, output_dir=tmp_path).run(scenario('none'), FIXTURES)


@pytest.mark.parametrize('failures', [0, 1, 2, 3])
@pytest.mark.parametrize('error,reason', [(TimeoutError('timed out'), 'backend_timeout'),
                                         (ConnectionRefusedError(111, 'Connection refused'), 'backend_connection_error')])
def test_workflow_retry_and_admission(tmp_path, failures, error, reason, no_sleep):
    baseline_backend = FailingBackend()
    baseline = run(tmp_path / 'baseline', baseline_backend)
    backend = FailingBackend(failures, error)
    result = run(tmp_path / 'retry', backend)
    assert result.runner_success, result.error
    assert result.termination_reason == (reason if failures == 3 else 'completed')
    assert result.dataset_eligible == (failures < 3)
    assert result.trace.metadata['backend_retry_count'] == min(failures, 2)
    assert not any(e.event_type == TraceEventType.PROTOCOL_VIOLATION for e in result.trace.events)
    assert [c.args[0] for c in no_sleep.call_args_list] == [2, 4][:min(failures, 2)]
    if failures:
        assert result.trace.metadata['backend_error_type'] == reason
        assert result.trace.metadata['backend_error_message']
        assert len(set(backend.failed_requests)) == 1
    if failures < 3:
        assert backend.stages == baseline_backend.stages
        assert [(e.event_type, e.output_text) for e in result.trace.events] == [(e.event_type, e.output_text) for e in baseline.trace.events]
    else:
        assert len(backend.stages) == 2
        assert backend.calls == 5  # handoff, list_directory, then 3 failed calls


def test_actual_model_text_remains_protocol_violation(tmp_path, no_sleep):
    backend = FailingBackend(text=True)
    result = run(tmp_path, backend)
    assert result.termination_reason == 'protocol_violation'
    assert not result.dataset_eligible
    assert backend.calls == 2
    assert result.trace.metadata['backend_retry_count'] == 0
    no_sleep.assert_not_called()


def test_nontransient_http_is_not_retried(no_sleep):
    backend = Mock()
    backend.generate.side_effect = urllib.error.HTTPError('http://backend', 401, 'Unauthorized', {}, None)
    with pytest.raises(BackendError) as caught:
        StageRunner(backend)._generate_with_retry('prompt', 'any')
    assert caught.value.reason == 'backend_http_error'
    assert backend.generate.call_count == 1
    no_sleep.assert_not_called()


@pytest.mark.parametrize('backend_class', [APIBackend, HFBackend])
@pytest.mark.parametrize('status', [408, 429, 500, 502, 503, 504])
def test_http_request_budget(backend_class, status, monkeypatch, no_sleep):
    backend = backend_class.__new__(backend_class)
    backend._lock = threading.Lock()
    backend.max_tokens = 100
    backend.temperature = 0
    backend.model = 'test'
    backend.base_url = 'http://backend'
    backend.api_key = 'test'
    backend.reset(task='task', mcp_tools=['list_directory'])
    request = Mock(side_effect=urllib.error.HTTPError('http://backend', status, 'failure', {}, None))
    monkeypatch.setattr('urllib.request.urlopen', request)
    runner = StageRunner(backend)
    before = copy.deepcopy(backend._conversation)
    with pytest.raises(BackendError):
        runner._generate_with_retry('same prompt', 'any')
    assert request.call_count == 3
    assert len({call.args[0].data for call in request.call_args_list}) == 1
    assert backend._conversation == before
    assert [call.args[0] for call in no_sleep.call_args_list] == [2, 4]


def test_transport_failure_during_protocol_repair(tmp_path, no_sleep):
    backend = CoordinationBackend()
    backend.generate = Mock(side_effect=[ModelTurn(text='plain text'),
                                        TimeoutError('timed out'), TimeoutError('timed out'),
                                        TimeoutError('timed out')])
    result = run(tmp_path, backend)
    assert result.termination_reason == 'backend_timeout'
    assert not result.dataset_eligible
    assert backend.generate.call_count == 4
    assert all(call.args == (StageRunner.TOOL_CALL_NUDGE,) for call in backend.generate.call_args_list[1:])
    assert not any(e.event_type == TraceEventType.PROTOCOL_VIOLATION for e in result.trace.events)
