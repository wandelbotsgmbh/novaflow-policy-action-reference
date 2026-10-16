"""Shared machinery behind the policy actions (not an action surface itself).

Reading order:

``policy_execution``  NovaFlow glue: inputs, Nova connection, halt/progress/errors.
``policy_loop``       observe → infer → one target per step, safety checks, stop.
``policy_client``     openpi-style websocket client (the policy server contract).
``action_chunks``     one-waypoint action chunks with ``delay_until_braking``.
``controller_state``  joints, velocities, safety and jogging state per controller.
``controller_clock``  the controller's session clock, estimated from its reports.
``openpi_msgpack``    msgpack + numpy, wire-compatible with openpi-client.
"""
