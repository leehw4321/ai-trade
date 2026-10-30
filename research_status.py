"""Read-only, allowlisted summaries of the offline model search artifacts.

This module never imports a broker, changes trading configuration, or starts work.
Missing / incomplete artifacts are reported as unavailable, not as zero returns.
"""
import json
import math
from pathlib import Path


ARTIFACT_DIRECTORY = Path('analysis_runs') / 'model_search'
MAX_SELECTION_BYTES = 1_000_000
MAX_REPORT_BYTES = 2_000_000
PERIODS = ('train', 'validation', 'test')
METRICS = ('return_pct', 'trades', 'max_drawdown_pct', 'win_rate_pct', 'profit_factor')
LIMITATIONS = [
    '과거 10종목 표본 연구이며 업비트 전체 원화 종목의 검증 결과가 아닙니다.',
    '과거 호가를 재현하지 못하므로 수수료·체결 비용 가정과 실제 체결은 다를 수 있습니다.',
    '과거 수익률은 미래 기대수익률이 아니며 실거래 승인이나 자동매매 시작을 의미하지 않습니다.',
]


def _text(value, limit=2000):
    return value[:limit] if isinstance(value, str) else ''


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) else None


def _metrics(value):
    value = value if isinstance(value, dict) else {}
    result = {key: _number(value.get(key)) for key in METRICS}
    trades = result['trades']
    result['trades'] = int(trades) if trades is not None and trades >= 0 and int(trades) == trades else None
    for key in ('start', 'end'):
        if isinstance(value.get(key), str):
            result[key] = _text(value[key], 100)
    return result


def _scope(value):
    if isinstance(value, str):
        return _text(value)
    if not isinstance(value, dict):
        return ''
    # Only descriptive research metadata; no arbitrary result-file contents.
    cleaned = {}
    for key in ('description', 'start', 'end', 'timeframe', 'universe', 'markets', 'costs', 'period'):
        item = value.get(key)
        if isinstance(item, str):
            cleaned[key] = _text(item)
        elif isinstance(item, (int, float)) and not isinstance(item, bool) and math.isfinite(item):
            cleaned[key] = item
        elif isinstance(item, list):
            cleaned[key] = [_text(v, 100) for v in item[:100] if isinstance(v, str)]
    return cleaned


def _unavailable(status, message):
    return {'available': False, 'status': status, 'message': message,
            'selected_config': None, 'selected_metrics': {key: None for key in PERIODS},
            'approved_paper': False, 'paper_only': True,
            'expected_future_return_estimated': False, 'report_available': False,
            'limitations': LIMITATIONS.copy()}


def load_research_status(root):
    directory = Path(root) / ARTIFACT_DIRECTORY
    path = directory / 'selection.json'
    try:
        if path.stat().st_size > MAX_SELECTION_BYTES:
            return _unavailable('invalid', '연구 결과 파일 크기를 확인해야 합니다.')
        data = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return _unavailable('not_ready', '모델 비교 결과가 아직 저장되지 않았습니다.')
    except (OSError, UnicodeError, ValueError):
        return _unavailable('invalid', '연구 결과 파일을 읽을 수 없습니다. 계산 완료 후 다시 확인하세요.')
    if not isinstance(data, dict) or data.get('status') not in ('selected', 'no_qualified_model'):
        return _unavailable('invalid', '완료된 모델 선택 결과가 아닙니다.')
    config = data.get('selected_config')
    config = {key: _text(config.get(key), 200) for key in ('id', 'name', 'family', 'timeframe')} if isinstance(config, dict) else None
    if config is not None and not (config['id'] or config['name']):
        config = None
    raw_metrics = data.get('selected_metrics')
    if data['status'] == 'selected' and (config is None or not isinstance(raw_metrics, dict)
                                       or not all(isinstance(raw_metrics.get(key), dict) for key in PERIODS)):
        return _unavailable('invalid', '모델 또는 학습·검증·미사용 구간 지표가 누락되었습니다.')
    raw_metrics = raw_metrics if isinstance(raw_metrics, dict) else {}
    result = {'available': True, 'status': data['status'],
              'message': '비교 연구 완료 · 격리 모의 검증 후보' if data['status'] == 'selected' else '선정 기준을 충족한 모델 없음',
              'selected_config': config,
              'selected_metrics': {key: _metrics(raw_metrics.get(key)) if isinstance(raw_metrics.get(key), dict) else None for key in PERIODS},
              'selection_rule': _text(data.get('selection_rule')),
              'scope': _scope(data.get('scope')),
              'created_kst': _text(data.get('created_kst'), 100),
              'approved_paper': data['status'] == 'selected' and data.get('approved_paper') is True,
              'paper_only': True, 'expected_future_return_estimated': False,
              'report_available': (directory / 'report.md').is_file(),
              'limitations': LIMITATIONS.copy()}
    return result


def load_research_report(root):
    """Return only the fixed, local Markdown report, or None if unavailable."""
    path = Path(root) / ARTIFACT_DIRECTORY / 'report.md'
    try:
        if path.stat().st_size > MAX_REPORT_BYTES:
            return None
        return path.read_text(encoding='utf-8')
    except (OSError, UnicodeError):
        return None
