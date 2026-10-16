# NovaFlow Policy Action

Reference for running a **learned policy** on a robot as a NovaFlow node. The
policy runs in its own server, reached over a websocket (openpi's protocol). This
service owns the robot: read the joint state, ask the policy for the next target,
stream it to NOVA.

Built for a policy that returns **one joint target per inference, no chunking,
~15 Hz**. Each target goes to the controller as a one-waypoint action chunk with
`delay_until_braking` (NOVA ≥ 26.8.0-112), so the arm keeps moving between
targets instead of braking after each one.

## The action

`RunSingleArmPolicy` drives one motion group. Inputs: `motion_group`,
`policy_url` (default `ws://app-dummy-policy:8080`), `prompt`, `lead_ms`,
`delay_until_braking_ms`, `timeout_s`. It runs until halted;
[`examples/trees/run_policy.json`](examples/trees/run_policy.json) bounds it with
a `parallel` next to a 20 s `delay` (a `Timeout` would fail the flow). Put guards
in a `ReactiveSequence` around it. E-stop, a jogging pause that doesn't clear,
lost robot state or a policy error end the episode with an error.

## Policy server contract

openpi's websocket protocol: on connect the server sends its metadata, then
answers each observation with an action; on an error it sends the traceback as
a text frame and closes. Frames are msgpack with numpy arrays, compatible with
openpi-client. One connection is one episode.

| Frame       | Key                          | Value                                                  |
| ----------- | ---------------------------- | ------------------------------------------------------ |
| metadata    | `dof`, `dt_ms`               | joints driven, step time                               |
| observation | `observation/joint_position` | float32 `[dof]`, rad                                   |
|             | `observation/joint_velocity` | float32 `[dof]`, rad/s                                 |
|             | `prompt`                     | str, if set on the node                                |
| action      | `actions`                    | float32 `[horizon, dof]`, absolute rad; row 0 executed |

[`policies/dummy`](policies/dummy/) is a complete server; a real one replaces
`Episode.infer`.

## Streaming to the robot

Each step ([`policy_loop.py`](src/actions/internal/policy_loop.py)) snapshots the
state, infers, and sends the target ([`action_chunks.py`](src/actions/internal/action_chunks.py)),
stamped at _observation arrival + `lead_ms`_ on the controller's clock.
Waypoints stay ≥ 40 ms apart, late targets are dropped, and on stop the last
target is resent with `delay_until_braking: 0` so the arm stops on it.

| Input                    | Default             | Raise it when                                    |
| ------------------------ | ------------------- | ------------------------------------------------ |
| `lead_ms`                | policy step + 40 ms | the log says _targets arrive later than lead_ms_ |
| `delay_until_braking_ms` | 200                 | inference stalls make the arm brake mid-motion   |

**Why not novapolicy's `PolicyExecutor`?** As of `wandelbots-nova` main
(2026-10-05) it can't run this kind of policy: its jogging session uses the
`/execution/jogging` endpoint NOVA 26.8 no longer serves, it sizes its clock
cushion as `steps × dt_ms` (one step is less than normal jitter, so the robot
freezes), and it never sends `delay_until_braking`. Once those are fixed
upstream, this loop can go back to `PolicyExecutor`.

## Develop and deploy

```bash
uv sync && uv run pytest                          # action service
cd policies/dummy && uv sync && uv run pytest     # dummy policy server
```

Deploy both apps into the cell your kubeconfig points at. First fill in your
registry (`skaffold.env`, `.nova`) and the cell's pull secret for it
(`imagePullSecrets` in both `helm/skaffold-app/values.yaml`):

```bash
docker login <your-registry>
KUBECONFIG=<instance kubeconfig> skaffold run
```

Or `nova app install .` and `nova app install policies/dummy`. Set the dummy's
`DOF` to the robot's joint count.

Dependencies: `wandelbots-novaflow` / `novaflow-types` 0.1.4 and the
`wandelbots-api-client` 26.6.0 they pin. No `wandelbots-nova` SDK: no release
pins 26.6.0, and forcing another version would change NovaFlow's type ids.
