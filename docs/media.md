# NDI / RTSP browser preview

Stage Monitor's local media layer converts camera video into WebRTC preview. Browsers do not natively play NDI or RTSP. Device telemetry, PTZ control and video are separate paths.

Install the backend `video` extra. RTSP decoding uses the installed Python video stack. NDI additionally needs its Runtime on the backend computer; binaries are not redistributed. Detection checks normal Windows installation locations and supports `STAGE_NDI_RUNTIME` for an explicit library path.

For Tail Air, configure video source metadata or allow NDI discovery for that camera host. Other RTSP cameras use `generic.rtsp`, host/port and path. Save supported credentials through device settings, not inside source URLs.

The browser starts preview on request. The backend selects a source, negotiates WebRTC and tracks heartbeats. Decoding runs outside the API event loop. Preview is muted and limited to 640 pixels in width. Configurable target FPS limits are 15/25/30/50/60; source rate, decoder, CPU, network and browser determine actual performance. A 60 FPS setting is not a throughput guarantee.

Current limits include eight sessions and four concurrent cameras. Use HTTPS or localhost. Autoplay/codecs vary between browsers; a Play interaction may be required. ONLINE proves telemetry contact, not video health.

This is preview infrastructure, not a broadcast router or replacement for production streaming. Video is not carried in telemetry WebSocket messages. Source metadata for NDI/RTSP/SRT/WebRTC does not imply a decoder for every advertised source type.
