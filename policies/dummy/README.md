# Dummy policy server

A policy server with a real policy's shape and no model: joint state in, one
absolute joint target out, at **15 Hz**, no chunking. It speaks openpi's
websocket protocol, the contract the action service expects (see the root
README). Use it to build and test the whole path before a model exists, and as
the template for a real server: replace `Episode.infer` in
[`policy.py`](dummy_policy/policy.py) and keep [`server.py`](dummy_policy/server.py).

It deploys as its own app and depends on neither `wandelbots-nova` nor
`novaflow`: the robot is the action's concern.

## Protocol

1. Client connects; the server sends its metadata:
   `{"name": ..., "dof": 6, "dt_ms": 66.7, "action_horizon": 1}`.
2. Client sends `{"observation/joint_position": float32[dof], ...}`; the server
   answers `{"actions": float32[1, dof]}`.
3. On an error the server sends the traceback as a text frame and closes (1011).

Frames are msgpack with numpy arrays ([`openpi_msgpack.py`](dummy_policy/openpi_msgpack.py),
a verbatim copy of the action service's). One connection is one episode.
`GET /healthz` returns 200.

## Motion

Every joint follows the same sine around the pose seen at the start of the
episode: amplitude 0.12 rad, period 6 s, clamped to ±0.35 rad. The first target
is the current pose. The anchor is kept across episodes while the robot stays
within 0.35 rad of it, so repeated halted episodes don't walk the arm away (see
the docstring in `policy.py`).

## Configuration

| Variable    | Default |                                                |
| ----------- | ------- | ---------------------------------------------- |
| `PORT`      | `8000`  | must match `port` in `.nova` / the helm values |
| `DOF`       | `6`     | joints of the robot; the action checks it      |
| `LOG_LEVEL` | `INFO`  |                                                |

## Run and test

```bash
uv sync
uv run pytest
uv run python -m dummy_policy        # ws://localhost:8000
```

Deployed with the root `skaffold.yaml`, or `nova app install policies/dummy`. In
the cell it is `ws://app-dummy-policy:8080`.
