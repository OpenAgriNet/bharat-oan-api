"""FFS diagnostics: explicit summaries only, never request/response bodies or credentials."""
from contextlib import contextmanager
import json
import logging
import math
import time

from langfuse import get_client
from opentelemetry import trace

logger = logging.getLogger(__name__)
FAILURES = {'unavailable', 'setup_incomplete', 'partial_crop_update', 'booking_uncertain', 'booking_failed'}


def numeric_fields(data, fields):
    return {key: data[key] for key in fields
            if type(data.get(key)) is int or
            (type(data.get(key)) is float and math.isfinite(data[key]))}


def result_summary(result):
    data = result.get('data', {})
    summary = {'status': result['status']}
    for key in ('villages', 'plots', 'crops', 'shops'):
        if isinstance(data.get(key), list):
            summary[key + '_count'] = len(data[key])
    for key in ('has_more', 'search_complete', 'complete_supply_only', 'booking_enabled'):
        if type(data.get(key)) is bool:
            summary[key] = data[key]
    if isinstance(data.get('products'), list):
        summary['products'] = [numeric_fields(p, ('group', 'bags', 'kg_per_bag', 'already_purchased'))
                               for p in data['products'] if isinstance(p, dict)]
    booking = data.get('booking')
    if isinstance(booking, dict):
        summary['booking_valid'] = booking.get('is_valid') is True
        summary['bookings_count'] = len(booking.get('bookings', []))
    return summary


def response_summary(status, data):
    """Classify provider messages without copying arbitrary text (which may echo secrets)."""
    summary = {'http_status': status}
    if not isinstance(data, dict):
        summary['provider_reason'] = 'non_json_response'
        return summary
    summary.update(numeric_fields(data, ('code', 'totalPages', 'totalRecords')))
    message = str(data.get('message', '')).lower()
    if 'recaptcha' in message or 'captcha' in message:
        summary['provider_reason'] = 'captcha_rejected'
    elif status == 429:
        summary['provider_reason'] = 'rate_limited'
    elif status == 401:
        summary['provider_reason'] = 'unauthorized'
    elif status >= 400 or data.get('code') != 200:
        summary['provider_reason'] = 'provider_rejected'
    else:
        summary['provider_reason'] = 'success'
    if isinstance(data.get('data'), list):
        summary['records_count'] = len(data['data'])
    return summary


def _emit(level, fields):
    # A telemetry/export/handler failure must not change a farmer workflow.
    try:
        # Preserve bh-dev diagnostics when the deployment suppresses INFO.
        if level == logging.INFO and not logger.isEnabledFor(logging.INFO):
            level = logging.WARNING
        logger.log(level, 'ffs %s', json.dumps(fields, ensure_ascii=False))
    except Exception:
        pass


@contextmanager
def observation(name, *, as_type='span', metadata=None):
    """One nested span and correlated completion log; caller supplies only safe fields."""
    started = time.monotonic()
    span = manager = None
    fields = dict(metadata or {})
    try:
        manager = get_client().start_as_current_observation(name=name, as_type=as_type, metadata=fields)
        span = manager.__enter__()
    except Exception:
        manager = None
    try:
        context = trace.get_current_span().get_span_context()
        if context.is_valid:
            fields.update(trace_id=f'{context.trace_id:032x}', span_id=f'{context.span_id:016x}')
    except Exception:
        pass
    _emit(logging.INFO, dict(fields, event=name, phase='started'))
    output = {}
    try:
        yield output
    except BaseException as error:
        output.update(error_type=type(error).__name__, status='error')
        raise
    finally:
        output['duration_ms'] = round((time.monotonic() - started) * 1000, 2)
        failed = 'error_type' in output or output.get('status') in FAILURES
        level = 'ERROR' if failed else 'DEFAULT'
        try:
            if span is not None:
                span.update(output=output, level=level)
        except Exception:
            pass
        _emit(logging.ERROR if failed else logging.INFO, dict(fields, event=name, phase='completed', **output))
        if manager is not None:
            try:
                # Do not let the SDK automatically capture raw exception messages.
                manager.__exit__(None, None, None)
            except Exception:
                pass
