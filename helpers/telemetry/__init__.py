"""Telemetry is split by channel.

- base.py: the shared Ekstep 2.2 event format.
- chat.py: chat/web/app events, sent by app.tasks.telemetry.send_telemetry.
- voice.py: phone-call events, sent by app.voice.telemetry.send_voice_telemetry.

Import from the submodule for your channel; this package re-exports nothing so the
two channels cannot be mixed by accident.
"""
