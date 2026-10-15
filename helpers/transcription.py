import os
import base64
import httpx
import json
from dotenv import load_dotenv
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    retry_if_exception,
)
from langcodes import Language
from openai import OpenAI
from io import BytesIO

from helpers.utils import get_logger, curl_escape_single_quoted, payload_for_log, text_for_log

load_dotenv()

logger = get_logger(__name__)

LOG_TEXT_MAX_CHARS = 500

WHISPER_EN_ASR_SERVICE_ID = "ai4bharat/whisper-medium-en--gpu--t4"
INDO_ARYAN_ASR_SERVICE_ID = "ai4bharat/conformer-multilingual-indo_aryan-gpu--t4"
DRAVIDIAN_ASR_SERVICE_ID = "ai4bharat/conformer-multilingual-dravidian-gpu--t4"
MULTILINGUAL_ASR_SERVICE_ID = "bhashini/ai4bharat/conformer-multilingual-asr"

# ISO 639 codes supported by Bhashini family-specific ASR models (see Bhashini API docs).
INDO_ARYAN_LANGS = frozenset({"hi", "bn", "mr", "ur", "or", "pa", "gu", "sa"})
DRAVIDIAN_LANGS = frozenset({"kn", "ml", "ta", "te"})

_bhashini_client = None

BHASHINI_PIPELINE_URL = os.getenv(
    "BHASHINI_API_URL",
    "https://dhruva-api.bhashini.gov.in/services/inference/pipeline",
)


class BhashiniAPIError(Exception):
    def __init__(self, status_code, message, response_body=None):
        self.status_code = status_code
        self.message = message
        self.response_body = response_body
        super().__init__(f"Bhashini API Error {status_code}: {message}")


def is_retryable_status(exception):
    """Check if we should retry based on status code"""
    if isinstance(exception, BhashiniAPIError):
        return exception.status_code in [500, 502, 503, 504, 429]
    return False


def get_bhashini_timeout() -> httpx.Timeout:
    """Bhashini outbound timeouts (env-tunable for slow networks / large audio)."""
    return httpx.Timeout(
        connect=float(os.getenv("BHASHINI_CONNECT_TIMEOUT", "30")),
        read=float(os.getenv("BHASHINI_READ_TIMEOUT", "180")),
        write=float(os.getenv("BHASHINI_WRITE_TIMEOUT", "180")),
        pool=float(os.getenv("BHASHINI_POOL_TIMEOUT", "30")),
    )


def get_bhashini_request_timeout(audio_base64_len: int) -> httpx.Timeout:
    """Extend write timeout for large base64 audio uploads."""
    base = get_bhashini_timeout()
    extra_write = max(0.0, (audio_base64_len / 50_000) * 10)
    return httpx.Timeout(
        connect=base.connect,
        read=base.read,
        write=base.write + extra_write,
        pool=base.pool,
    )


def reset_bhashini_client() -> None:
    global _bhashini_client
    if _bhashini_client is not None:
        try:
            _bhashini_client.close()
        except Exception:
            pass
        _bhashini_client = None


def _get_bhashini_headers() -> dict[str, str]:
    api_key = (os.getenv("MEITY_API_KEY_VALUE") or "").strip()
    if not api_key:
        raise BhashiniAPIError(
            status_code=500,
            message="MEITY_API_KEY_VALUE is not configured",
        )
    return {
        "Accept": "*/*",
        "User-Agent": "Thunder Client (https://www.thunderclient.com)",
        "Authorization": api_key,
        "Content-Type": "application/json",
    }


def _bhashini_retry_before_sleep(retry_state, label: str) -> None:
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    if isinstance(exc, (httpx.ConnectTimeout, httpx.ConnectError, httpx.RemoteProtocolError)):
        reset_bhashini_client()
    logger.warning(
        "Bhashini %s retry %s: %s",
        label,
        retry_state.attempt_number,
        exc,
    )


def get_bhashini_asr_service_id(source_lang: str) -> tuple[str, str]:
    """Pick the Bhashini ASR serviceId from a detected or requested language code."""
    lang = (source_lang or "").strip().lower()
    if lang == "en":
        return WHISPER_EN_ASR_SERVICE_ID, "english"
    if lang in DRAVIDIAN_LANGS:
        return DRAVIDIAN_ASR_SERVICE_ID, "dravidian"
    if lang in INDO_ARYAN_LANGS:
        return INDO_ARYAN_ASR_SERVICE_ID, "indo_aryan"
    return MULTILINGUAL_ASR_SERVICE_ID, "multilingual_fallback"


def get_bhashini_client():
    global _bhashini_client
    if _bhashini_client is None:
        _bhashini_client = httpx.Client(
            timeout=get_bhashini_timeout(),
            limits=httpx.Limits(
                max_connections=20,
                max_keepalive_connections=10,
            ),
        )
    return _bhashini_client


def base64_to_audio_file(base64_string: str, filename: str = "audio.wav") -> BytesIO:
    audio_bytes = base64.b64decode(base64_string)
    audio_file = BytesIO(audio_bytes)
    audio_file.name = filename
    return audio_file


def convert_audio_to_base64(filepath: str) -> str:
    with open(filepath, "rb") as audio_file:
        encoded_string = base64.b64encode(audio_file.read()).decode('utf-8')
    return encoded_string


def transcribe_whisper(audio_base64: str):
    logger.info(
        "Transcribe Whisper input | audio_base64_len=%s",
        len(audio_base64),
    )
    openai_client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))
    response = openai_client.audio.transcriptions.create(
        model="whisper-1",
        file=base64_to_audio_file(audio_base64),
        response_format="verbose_json"
    )
    lang_code = Language.find(response.language).language
    text = response.text
    logger.info(
        "Transcribe Whisper output | lang_code=%s result_length=%s text=%s",
        lang_code,
        len(text) if text else 0,
        (text or "")[:LOG_TEXT_MAX_CHARS],
    )
    return lang_code, text


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=20),
    retry=retry_if_exception_type((
        httpx.ConnectTimeout, 
        httpx.ReadTimeout, 
        httpx.ConnectError,
        httpx.RemoteProtocolError
    )) | retry_if_exception(is_retryable_status),
    before_sleep=lambda retry_state: _bhashini_retry_before_sleep(retry_state, "transcribe"),
)
def transcribe_bhashini(audio_base64: str, source_lang: str):
    source_lang = (source_lang or "").strip().lower()
    service_id, service_reason = get_bhashini_asr_service_id(source_lang)

    url = BHASHINI_PIPELINE_URL
    headers = _get_bhashini_headers()
    request_timeout = get_bhashini_request_timeout(len(audio_base64))

    data = {
        "pipelineTasks": [
            {
                "taskType": "asr",
                "config": {
                    "serviceId": service_id,
                    "language": {
                        "sourceLanguage": source_lang,
                    },
                    "audioFormat": "wav",
                    "samplingRate": 16000,
                    "preProcessors": ["vad"],
                }
            }
        ],
        "inputData": {
            "audio": [
                {
                    "audioContent": audio_base64
                }
            ]
        }
    }

    logger.info(
        "Transcribe Bhashini input | source_lang=%s serviceId=%s service_reason=%s audio_base64_len=%s",
        source_lang, service_id, service_reason, len(audio_base64),
    )
    payload_safe = payload_for_log(data)
    logger.info(
        "Transcribe Bhashini request payload | serviceId=%s payload=%s",
        service_id, json.dumps(payload_safe, ensure_ascii=False)
    )
    payload_safe_str = json.dumps(payload_safe, ensure_ascii=False)
    payload_log_escaped = curl_escape_single_quoted(payload_safe_str)
    curl_for_log = (
        "curl -X POST '%s' -H 'Authorization: <MEITY_API_KEY_VALUE>' -H 'Content-Type: application/json' -d '%s'"
    ) % (url, payload_log_escaped)
    logger.info(
        "Transcribe Bhashini external API | serviceId=%s curl=%s",
        service_id, curl_for_log
    )

    client = get_bhashini_client()

    try:
        response = client.post(
            url,
            headers=headers,
            content=json.dumps(data),
            timeout=request_timeout,
        )

        if response.status_code != 200:
            logger.error(
                "Transcribe Bhashini failed | status_code=%s serviceId=%s response=%s curl=%s",
                response.status_code, service_id, text_for_log(response.text), curl_for_log
            )
            raise BhashiniAPIError(
                status_code=response.status_code,
                message=response.text,
                response_body=response.text
            )

        response_json = response.json()
        result = response_json['pipelineResponse'][0]['output'][0]['source']
        logger.info(
            "Transcribe Bhashini response | serviceId=%s payload=%s",
            service_id,
            text_for_log(json.dumps(response_json, ensure_ascii=False)),
        )
        logger.info(
            "Transcribe Bhashini output | source_lang=%s serviceId=%s result_length=%s text=%s",
            source_lang,
            service_id,
            len(result) if result else 0,
            (result or "")[:LOG_TEXT_MAX_CHARS],
        )
        return result

    except BhashiniAPIError:
        raise
    except httpx.ConnectTimeout:
        logger.error(
            "Transcribe Bhashini connect timeout | serviceId=%s audio_base64_len=%s "
            "hint=check outbound HTTPS to dhruva-api.bhashini.gov.in or raise BHASHINI_CONNECT_TIMEOUT curl=%s",
            service_id, len(audio_base64), curl_for_log,
        )
        raise
    except httpx.HTTPStatusError as e:
        logger.error(
            "Transcribe Bhashini HTTP error | status_code=%s serviceId=%s message=%s curl=%s",
            e.response.status_code, service_id, text_for_log(e.response.text or str(e)), curl_for_log
        )
        raise BhashiniAPIError(
            status_code=e.response.status_code,
            message=str(e),
            response_body=e.response.text
        )
    except Exception as e:
        logger.error(
            "Transcribe Bhashini error | serviceId=%s error=%s message=%s curl=%s",
            service_id, type(e).__name__, str(e)[:1000], curl_for_log
        )
        raise


# Audio Language Detection (ALD) service id. Configurable via env so dev/prod can differ.
ALD_SERVICE_ID = os.getenv(
    'BHASHINI_ALD_SERVICE_ID',
    'bhashini/iitmandi/audio-lang-detection/gpu'
)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=20),
    retry=retry_if_exception_type((
        httpx.ConnectTimeout,
        httpx.ReadTimeout,
        httpx.ConnectError,
        httpx.RemoteProtocolError
    )) | retry_if_exception(is_retryable_status),
    before_sleep=lambda retry_state: _bhashini_retry_before_sleep(retry_state, "lang-detect"),
)
def detect_audio_language_bhashini(audio_base64: str) -> str:
    """Detect the spoken language of base64 wav audio via Bhashini ALD.

    Returns the detected ISO language code (e.g. 'hi', 'en', 'bn') which can be
    fed directly into transcribe_bhashini as the source language.
    """
    url = BHASHINI_PIPELINE_URL
    headers = _get_bhashini_headers()
    request_timeout = get_bhashini_request_timeout(len(audio_base64))

    data = {
        "pipelineTasks": [
            {
                "taskType": "audio-lang-detection",
                "config": {
                    "serviceId": ALD_SERVICE_ID,
                    "audioFormat": "wav",
                }
            }
        ],
        "inputData": {
            "audio": [
                {
                    "audioContent": audio_base64
                }
            ]
        }
    }

    logger.info(
        "Detect language Bhashini input | audio_base64_len=%s",
        len(audio_base64)
    )
    payload_safe = payload_for_log(data)
    payload_safe_str = json.dumps(payload_safe, ensure_ascii=False)
    logger.info(
        "Detect language Bhashini request payload | serviceId=%s payload=%s",
        ALD_SERVICE_ID, payload_safe_str,
    )
    payload_log_escaped = curl_escape_single_quoted(payload_safe_str)
    curl_for_log = (
        "curl -X POST '%s' -H 'Authorization: <MEITY_API_KEY_VALUE>' -H 'Content-Type: application/json' -d '%s'"
    ) % (url, payload_log_escaped)
    logger.info(
        "Detect language Bhashini external API | serviceId=%s curl=%s",
        ALD_SERVICE_ID, curl_for_log
    )

    client = get_bhashini_client()

    try:
        response = client.post(
            url,
            headers=headers,
            content=json.dumps(data),
            timeout=request_timeout,
        )

        if response.status_code != 200:
            logger.error(
                "Detect language Bhashini failed | status_code=%s serviceId=%s response=%s curl=%s",
                response.status_code, ALD_SERVICE_ID, text_for_log(response.text), curl_for_log
            )
            raise BhashiniAPIError(
                status_code=response.status_code,
                message=response.text,
                response_body=response.text
            )

        response_json = response.json()
        lang_prediction = response_json['pipelineResponse'][0]['output'][0]['langPrediction']
        detected_language_code = (lang_prediction[0]['langCode'] or "").strip().lower()
        selected_service_id, service_reason = get_bhashini_asr_service_id(detected_language_code)
        logger.info(
            "Detect language Bhashini response | payload=%s",
            text_for_log(json.dumps(response_json, ensure_ascii=False)),
        )
        logger.info(
            "Detect language Bhashini output | detected_lang=%s lang_prediction=%s "
            "selected_asr_serviceId=%s service_reason=%s",
            detected_language_code,
            json.dumps(lang_prediction, ensure_ascii=False),
            selected_service_id,
            service_reason,
        )
        return detected_language_code

    except BhashiniAPIError:
        raise
    except httpx.ConnectTimeout:
        logger.error(
            "Detect language Bhashini connect timeout | serviceId=%s audio_base64_len=%s "
            "hint=check outbound HTTPS to dhruva-api.bhashini.gov.in or raise BHASHINI_CONNECT_TIMEOUT curl=%s",
            ALD_SERVICE_ID, len(audio_base64), curl_for_log,
        )
        raise
    except httpx.HTTPStatusError as e:
        logger.error(
            "Detect language Bhashini HTTP error | status_code=%s serviceId=%s message=%s curl=%s",
            e.response.status_code, ALD_SERVICE_ID, text_for_log(e.response.text or str(e)), curl_for_log
        )
        raise BhashiniAPIError(
            status_code=e.response.status_code,
            message=str(e),
            response_body=e.response.text
        )
    except Exception as e:
        logger.error(
            "Detect language Bhashini error | serviceId=%s error=%s message=%s curl=%s",
            ALD_SERVICE_ID, type(e).__name__, str(e)[:1000], curl_for_log
        )
        raise
