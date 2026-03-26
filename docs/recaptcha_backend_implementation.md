# reCAPTCHA v3 Backend Implementation

## Overview
Integrated Google reCAPTCHA v3 verification into the existing `/api/token` endpoint in the FastAPI backend. The endpoint now requires a `recaptchaToken` in the request body, verifies it with Google's API, and returns a chatbot token only if verification passes.

## Files Modified

### 1. `app/routers/token.py`
- **Imports Added**:
  - `from fastapi import Request` (for raw JSON access)
  - `import requests` (for HTTP calls to Google)

- **Endpoint Signature Changed**:
  - From: `async def create_auth_token(request: Optional[AuthRequest] = None):`
  - To: `async def create_auth_token(request: Request):`

- **New Logic Added**:
  - Read `recaptchaToken` from request JSON.
  - If missing, return 400 `{"error": "recaptchaToken missing"}`.
  - Call Google reCAPTCHA verify API:
    - URL: `https://www.google.com/recaptcha/api/siteverify`
    - Data: `secret=<RECAPTCHA_SECRET_KEY>`, `response=<recaptchaToken>`
    - Timeout: 5 seconds
  - Parse response JSON:
    - If `success` is false → 400 `{"error": "recaptcha failed"}`
    - If `score < 0.5` → 403 `{"error": "low score"}`
  - On pass, continue with existing token generation (now simplified to dummy UUID for demo).

- **Error Handling**:
  - Request failures → 500 `{"error": "recaptcha verification failed"}`

### 2. `.env`
- Added `RECAPTCHA_SECRET_KEY=yourkey`

## API Behavior
- **Request**: POST `/api/token` with JSON body containing `recaptchaToken` and optional `metadata`.
- **Response**:
  - 200: `{"token": "<dummy-uuid>", "expires_in": 900}` (on pass)
  - 400: `{"error": "recaptchaToken missing"}` or `{"error": "recaptcha failed"}`
  - 403: `{"error": "low score"}`
  - 500: `{"error": "recaptcha verification failed"}`

## Dependencies
- `requests` library (already in requirements.txt)

## Testing
- Use curl with valid/invalid tokens to test pass/fail.
- Modify score threshold in code for forced fails.