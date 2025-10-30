import os
import re
import base64
import requests
from dotenv import load_dotenv
from elevenlabs import VoiceSettings
from elevenlabs.client import ElevenLabs

load_dotenv()

def remove_urls(text):
    return re.sub(r'https?://\S+', '', text)

def text_to_speech_bhashini(text, source_lang='hi', gender='female', sampling_rate=8000):
    url = 'https://dhruva-api.bhashini.gov.in/services/inference/pipeline'
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
    response = requests.post(url, headers=headers, json=data)
    assert response.status_code == 200, f"Error: {response.status_code} {response.text}"
    response_json = response.json()

    audio_content = response_json['pipelineResponse'][0]['audio'][0]['audioContent']
    audio_data = base64.b64decode(audio_content)
    return audio_data


def text_to_speech_elevenlabs(text, voice_id=None, model_id="eleven_flash_v2_5", apply_text_normalization="on"):
    """
    Convert text to speech using ElevenLabs API.
    
    Args:
        text (str): Text to convert to speech
        voice_id (str, optional): Voice ID to use. If None, uses default voice
        model_id (str): Model ID to use for TTS (default: eleven_multilingual_v2)
        apply_text_normalization (str): Controls text normalization. Options: 'auto', 'on', 'off'
    
    Returns:
        bytes: Audio data as bytes
    """
    # Initialize ElevenLabs client
    client = ElevenLabs(api_key=os.getenv('ELEVENLABS_API_KEY'))
    
    # Use a default voice if none provided
    if voice_id is None:
        voice_id = "FmBhnvP58BK0vz65OOj7"  # Viraj
    
    # Generate speech using the text_to_speech method
    audio_generator = client.text_to_speech.convert(
        voice_id=voice_id,
        text=text,
        model_id=model_id,
        voice_settings=VoiceSettings(
            stability=0.1,
            similarity_boost=0.5,
            style=0.0,
            use_speaker_boost=True,
            speed=1.0
        ),
        apply_text_normalization=apply_text_normalization
    )
    
    # Convert generator to bytes - ensure generator completes fully
    audio_chunks = []
    for chunk in audio_generator:
        audio_chunks.append(chunk)
    
    audio_data = b"".join(audio_chunks)
    return audio_data