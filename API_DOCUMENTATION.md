# Bharat OAN API Documentation

## Overview

Bharat OAN (Bharatvistaar AI API) is an AI-powered Voice Assistant API designed for agricultural support. The API provides capabilities for conversational AI interactions, speech-to-text transcription, text-to-speech conversion, and intelligent suggestions for agricultural queries. It supports multiple languages and integrates with services like Bhashini and Whisper for multilingual support.

## Base URL

```
http://10.128.188.21
```

All API endpoints are prefixed with `/api` unless otherwise specified.

---

## Endpoints

### 1. Root Endpoint

**GET** `/`

Get basic application information.

**Response:**

```json
{
  "app": "Bharatvistaar AI API",
  "environment": "production",
  "debug": false,
  "api_prefix": "/api"
}
```

---

### 2. Chat Endpoint

**GET** `/api/chat/`

Stream chat responses for conversational AI interactions. Supports streaming responses via Server-Sent Events (SSE).

**Query Parameters:**

- `query` (string, required): The user's chat query
- `session_id` (string, optional): Session ID for maintaining conversation context
- `source_lang` (string, default: "hi"): Source language code
- `target_lang` (string, default: "hi"): Target language code
- `user_id` (string, default: "anonymous"): User identifier

**Response:**

- Content-Type: `text/event-stream`
- Streaming response with chat messages

**Example Request:**

```
GET /api/chat/?query=कृषि%20सलाह&session_id=abc123&source_lang=hi&target_lang=hi&user_id=user123
```

---

### 3. Transcribe Endpoint

**POST** `/api/transcribe/`

Transcribe audio content to text using specified transcription service.

**Request Body:**

```json
{
  "audio_content": "base64_encoded_audio_string",
  "service_type": "bhashini" | "whisper",
  "session_id": "optional_session_id"
}
```

**Request Parameters:**

- `audio_content` (string, required): Base64 encoded audio content
- `service_type` (string, required): Transcription service - either "bhashini" or "whisper" (default: "bhashini")
- `session_id` (string, optional): Session ID

**Response:**

```json
{
  "status": "success",
  "text": "transcribed_text",
  "lang_code": "hi",
  "session_id": "session_id"
}
```

**Error Response:**

```json
{
  "status": "error",
  "message": "Invalid service type"
}
```

---

### 4. Text-to-Speech (TTS) Endpoint

**POST** `/api/tts/`

Convert text to speech audio using specified TTS service.

**Request Body:**

```json
{
  "text": "text_to_convert",
  "target_lang": "hi",
  "session_id": "optional_session_id",
  "service_type": "bhashini"
}
```

**Request Parameters:**

- `text` (string, required): Text to convert to speech
- `target_lang` (string, default: "hi"): Language code for TTS
- `session_id` (string, optional): Session ID
- `service_type` (string, required): TTS service - "bhashini" or "eleven_labs" (default: "bhashini")

**Response:**

```json
{
  "status": "success",
  "audio_data": "base64_encoded_audio",
  "session_id": "session_id"
}
```

**Error Response:**

```json
{
  "status": "error",
  "message": "Service type \"service_name\" not supported. Available options: bhashini"
}
```

---

### 5. Suggestions Endpoint

**GET** `/api/suggest/`

Get conversation suggestions for a session. Suggestions are created asynchronously during chat interactions.

**Query Parameters:**

- `session_id` (string, required): Session ID to get suggestions for
- `target_lang` (string, default: "hi"): Target language for suggestions

**Response:**

```json
[
  {
    "suggestion": "suggestion_text_1"
  },
  {
    "suggestion": "suggestion_text_2"
  }
]
```

**Example Request:**

```
GET /api/suggest/?session_id=abc123&target_lang=hi
```

---

### 6. Health Check Endpoints

#### 6.1 Health Check

**GET** `/api/health/`

Comprehensive health check including application metadata and service dependencies.

**Response:**

```json
{
  "app": {
    "name": "Bharatvistaar AI API",
    "environment": "production",
    "uptime_seconds": 3600
  },
  "dependencies": {
    "cache": {
      "status": "healthy",
      "latency_ms": 0
    }
  }
}
```

**Error Response (503):**

```json
{
  "detail": {
    "app": {...},
    "dependencies": {
      "cache": {
        "status": "unhealthy",
        "error": "error_message"
      }
    }
  }
}
```

#### 6.2 Liveness Probe

**GET** `/api/health/live`

Simple liveness check to verify the application is running. Used by Kubernetes.

**Response:**

```json
{
  "status": "alive"
}
```

#### 6.3 Readiness Probe

**GET** `/api/health/ready`

Readiness check to verify the application is ready to handle traffic. Checks cache connection. Used by Kubernetes.

**Response:**

```json
{
  "status": "ready",
  "cache": {
    "status": "healthy",
    "latency_ms": 0
  }
}
```

**Error Response (503):**

```json
{
  "detail": {
    "status": "not ready",
    "cache": {
      "status": "unhealthy",
      "error": "error_message"
    }
  }
}
```

---

### 7. File Endpoint

**GET** `/api/file/{file_hash}`

Serve cached HTML file by hash.

**Path Parameters:**

- `file_hash` (string, required): The hash of the cached HTML file

**Response:**

- Content-Type: `text/html`
- HTML content with cache headers

**Error Responses:**

- `404`: File not found or expired
- `500`: Internal server error

**Example Request:**

```
GET /api/file/abc123def456
```

---

### 8. Token Endpoint

**POST** `/api/token`

Create and return an encrypted JWT token for authentication.

**Request Body (optional):**

```json
{
  "mobile": "1111111111",
  "name": "guest",
  "role": "public",
  "metadata": "additional_metadata"
}
```

**Request Parameters:**

- `mobile` (string, optional): Mobile number (default: "1111111111")
- `name` (string, optional): User name (default: "guest")
- `role` (string, optional): User role (default: "public")
- `metadata` (string, optional): Additional metadata as string (default: "")

**Response:**

```json
{
  "token": "jwt_token_string",
  "expires_in": 2592000
}
```

**Error Response (500):**

```json
{
  "detail": "JWT private key is not configured. Please ensure private_key.pem file exists in the project root."
}
```

**Note:** Token expires in 30 days from creation.

---

## Authentication

Most endpoints support JWT authentication (currently disabled for testing). When enabled, include the JWT token in the Authorization header:

```
Authorization: Bearer <jwt_token>
```

To obtain a token, use the `/api/token` endpoint.

---

## Language Support

The API supports multiple languages through language codes:

- `en`: English
- `hi`: Hindi
- `mr`: Marathi
- Other language codes as supported by Bhashini and other services

---

## Error Handling

The API uses standard HTTP status codes:

- `200`: Success
- `400`: Bad Request (invalid parameters)
- `404`: Not Found
- `500`: Internal Server Error
- `503`: Service Unavailable (health check failures)

Error responses follow this format:

```json
{
  "status": "error",
  "message": "error_description"
}
```

---

## Rate Limiting

The API has a rate limit of 1000 requests per minute (configurable).

---

## CORS

CORS is enabled with configurable allowed origins. By default, all origins are allowed (`*`).

---

## Notes

- All timestamps are in UTC
- Session IDs are auto-generated if not provided
- Audio content must be base64 encoded
- The API uses Redis for caching and session management
- Streaming responses use Server-Sent Events (SSE) format
