import os
import re
import base64
import httpx
from dotenv import load_dotenv

from helpers.utils import get_logger

load_dotenv()

logger = get_logger(__name__)


def remove_urls(text):
    return re.sub(r'https?://\S+', '', text)


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
    curl_redacted = (
        "curl -X POST '%s' -H 'Authorization: ***' -H 'Content-Type: application/json' "
        "-d '<payload>'"
    ) % url
    logger.info(
        "TTS Bhashini external API | serviceId=%s curl=%s",
        service_id, curl_redacted
    )

    response = httpx.post(
        url,
        headers=headers,
        json=data,
        timeout=httpx.Timeout(30.0, read=60.0)
    )
    assert response.status_code == 200, f"Error: {response.status_code} {response.text}"
    response_json = response.json()
    audio_content = response_json['pipelineResponse'][0]['audio'][0]['audioContent']
    audio_data = base64.b64decode(audio_content)
    logger.info(
        "TTS Bhashini output | target_lang=%s audio_size_bytes=%s",
        source_lang, len(audio_data)
    )
    return audio_data
