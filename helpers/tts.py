import os
import re
import base64
import json
import httpx
from dotenv import load_dotenv
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    retry_if_exception,
)

from helpers.utils import get_logger, curl_escape_single_quoted

load_dotenv()

logger = get_logger(__name__)

_bhashini_client = None


def get_bhashini_tts_client():
    global _bhashini_client
    if _bhashini_client is None:
        _bhashini_client = httpx.Client(
            timeout=httpx.Timeout(
                connect=10.0,
                read=120.0,
                write=60.0,
                pool=10.0
            ),
            limits=httpx.Limits(
                max_connections=20,
                max_keepalive_connections=10
            )
        )
    return _bhashini_client


class BhashiniAPIError(Exception):
    def __init__(self, status_code, message, response_body=None):
        self.status_code = status_code
        self.message = message
        self.response_body = response_body
        super().__init__(f"Bhashini API Error {status_code}: {message}")


def is_retryable_status(exception):
    if isinstance(exception, BhashiniAPIError):
        return exception.status_code in [500, 502, 503, 504, 429]
    return False


def remove_urls(text):
    return re.sub(r'https?://\S+', '', text)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=20),
    retry=retry_if_exception_type((
        httpx.ConnectTimeout,
        httpx.ReadTimeout,
        httpx.ConnectError,
        httpx.RemoteProtocolError
    )) | retry_if_exception(is_retryable_status),
    before_sleep=lambda retry_state: logger.warning(
        f"Bhashini TTS retry {retry_state.attempt_number}: {retry_state.outcome.exception()}"
    )
)
def text_to_speech_bhashini(text, source_lang='hi', gender='female', sampling_rate=8000):
    url = 'https://dhruva-api.bhashini.gov.in/services/inference/pipeline'
    service_id = "tts"
    headers = {
        'Accept': '*/*',
        'Authorization': os.getenv('MEITY_API_KEY_VALUE'),
        'Content-Type': 'application/json',
    }
    data = {
        "pipelineTasks": [
            {
                "taskType": "tts",
                "config": {
                    "language": {
                        "sourceLanguage": source_lang
                    },
                    "serviceId": "",
                    "gender": gender,
                    "samplingRate": sampling_rate
                }
            }
        ],
        "inputData": {
            "input": [
                {
                    "source": text
                }
            ]
        }
    }

    logger.info(
        "TTS Bhashini input | target_lang=%s gender=%s sampling_rate=%s text_length=%s",
        source_lang, gender, sampling_rate, len(text)
    )
    logger.info(
        "TTS Bhashini request payload | serviceId=%s payload=%s",
        service_id, json.dumps(data, ensure_ascii=False)
    )
    payload_str = json.dumps(data, ensure_ascii=False)
    payload_escaped = curl_escape_single_quoted(payload_str)
    curl = (
        "curl -X POST '%s' -H 'Authorization: <MEITY_API_KEY_VALUE>' -H 'Content-Type: application/json' -d '%s'"
    ) % (url, payload_escaped)
    logger.info(
        "TTS Bhashini external API | serviceId=%s curl=%s",
        service_id, curl
    )

    try:
        client = get_bhashini_tts_client()
        response = client.post(url, headers=headers, json=data)

        if response.status_code != 200:
            logger.error(
                "TTS Bhashini failed | status_code=%s serviceId=%s response=%s curl=%s",
                response.status_code, service_id, response.text[:500], curl
            )
            raise BhashiniAPIError(
                status_code=response.status_code,
                message=response.text,
                response_body=response.text
            )

        response_json = response.json()
        audio_content = response_json['pipelineResponse'][0]['audio'][0]['audioContent']
        audio_data = base64.b64decode(audio_content)
        logger.info(
            "TTS Bhashini output | target_lang=%s audio_size_bytes=%s",
            source_lang, len(audio_data)
        )
        return audio_data
    except BhashiniAPIError:
        raise
    except httpx.HTTPStatusError as e:
        logger.error(
            "TTS Bhashini HTTP error | status_code=%s serviceId=%s message=%s curl=%s",
            e.response.status_code if e.response else None, service_id,
            (e.response.text if e.response else str(e))[:500], curl
        )
        raise
    except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError, httpx.RemoteProtocolError) as e:
        logger.error(
            "TTS Bhashini error | serviceId=%s error=%s message=%s curl=%s",
            service_id, type(e).__name__, str(e)[:1000], curl
        )
        raise
    except Exception as e:
        logger.error(
            "TTS Bhashini error | serviceId=%s error=%s message=%s curl=%s",
            service_id, type(e).__name__, str(e)[:1000], curl
        )
        raise