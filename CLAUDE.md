# CLAUDE.md

Reference NovaFlow action service: `RunSingleArmPolicy` runs a remote policy
(openpi websocket server) on one NOVA motion group. Read `README.md` first; it
holds the policy server contract and the design rationale.

## Commands

Always `uv`, never bare `pip`/`python`/`pytest`. Two separate projects:

```bash
uv sync && uv run pytest                          # action service (repo root)
cd policies/dummy && uv sync && uv run pytest     # dummy policy server, own lockfile
uv run run.py                                     # register with NovaFlow (needs NOVA_API, NATS_BROKER)
cd policies/dummy && uv run python -m dummy_policy   # ws://localhost:8000
KUBECONFIG=<kubeconfig> skaffold run              # build + install both apps into the cell
```

Format with `uvx ruff format` (and `uvx ruff check --select I --fix` for imports).

## Layout

- `src/actions/single_arm_policy.py`: the action (inputs, registration).
- `src/actions/internal/`: `policy_execution` (NovaFlow glue, env config, halt
  and errors) → `policy_loop` (observe → infer → act, safety checks) →
  `policy_client` (websocket), `action_chunks` (one-waypoint streaming),
  `controller_state` + `controller_clock` (controller state stream),
  `openpi_msgpack` (codec).
- `policies/dummy/`: 15 Hz horizon-1 policy server, deployed as its own app.
- `examples/trees/run_policy.json`: demo flow (parallel + 20 s delay).
- `skaffold.yaml`, `helm/`, `policies/dummy/helm/`: deployment as NOVA `App`s.

## Rules

- **Don't reintroduce novapolicy's `PolicyExecutor` or the `wandelbots-nova`
  SDK.** The README says why. The action uses `wandelbots-api-client` directly.
- **Pins move together.** `wandelbots-novaflow` / `novaflow-types` are exact pins
  that must match the NovaFlow release on the instance; `wandelbots-api-client`
  stays at the version `novaflow-types` pins (26.6.0). No `override-dependencies`:
  a different api-client changes NovaFlow's type ids and breaks registration.
- **Streaming rules are measured, not arbitrary** (`action_chunks.py`): one
  waypoint per chunk, stamped at observation arrival + `lead_ms` on the
  controller clock, ≥ 40 ms spacing, late targets dropped, final waypoint with
  `delay_until_braking: 0`. Change them only with a measurement on a robot.
- **`openpi_msgpack.py` exists twice** (action service and dummy); edit one,
  copy to the other. `tests/test_vendoring.py` fails if they differ.
- Action inputs must be NovaFlow-registry types (primitives, `MotionTarget`,
  enums); `tests/test_actions.py` builds the schema the way registration does.
- Safety guards belong in the behavior tree, not in the action. The action only
  ends the episode on e-stop, a lasting jogging pause, stale state or errors.

## Gotchas

- The app operator injects `NOVA_API=api-gateway:8080` **without a scheme**;
  `_nova_api()` adds `http://`.
- The controller state stream sends `jogger_session_timestamp_ms` as a string,
  and reports the previous session's frozen clock until the first chunk
  (`ControllerClock` handles both). NOVA 26.8 sends no `joint_velocity`.
- In the cell, an app's service is `app-<name>:8080` (the dummy:
  `ws://app-dummy-policy:8080`); from outside, `ws://<instance>/cell/<name>`.
- A behavior-tree `timeout` node _fails_ its child; bound episodes with a
  `parallel` (success threshold 1) next to a `delay` instead.
- Registry and pull secret are placeholders (`<your-registry>`,
  `<your-pull-secret>`) in `skaffold.env`, `.nova` and the helm values.
