"""Atma integration boundary for Vaak GA v2.0.

Atma is a separate product and is not imported here. This sketch shows the
contract: Vaak emits transcript/undertone events, the external Atma service
reasons independently, then returns candidate text through response.create.
"""

# Pseudocode only:
# ws = connect_to_vaak_realtime(session_id)
# event = await ws.recv()
# if event["type"] == "response.requested":
#     candidate = await atma_client.respond(
#         transcript=event["input_text"],
#         session_id=session_id,
#     )
#     await ws.send({"type": "response.create", "text": candidate})
