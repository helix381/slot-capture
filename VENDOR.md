# Decoder provenance

The initial decoder snapshot comes from the user's existing local implementation:

- `E:/AI/skills/slot-reverse-capture/scripts/slot_capture.py`
- `E:/AI/skills/slot-reverse-capture/scripts/bole_value_codec.py`

Copied on 2026-10-28. The only initial decoder change is a package-aware relative import so the standalone regression runner and the packaged CLI can share it. Machine accounting rules are separate and derive from the audited Mia research runs. Original captures are not bundled in the executable.

v0.2.0 adds passive `tick` / `keepalive` counters and per-connection request/response pairing for the connection probe. This changes neither the wire codec nor reward interpretation. Reconnect SYN clears pending heartbeat state.
