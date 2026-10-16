---
name: run-local
description: Run the NovaFlow action service locally for development and testing. Sets up environment, configures NATS connection, and starts the service. Use this when developing or debugging actions.
---

# Run Local

This skill helps you run the NovaFlow action service locally for development and testing.

## Prerequisites

- Nova instance running with NovaFlow installed
- uv package manager installed

## Steps

1. **Check if `.env` file exists**:
   - If not, copy from template: `cp .env.template .env`

2. **Configure NATS connection** in `.env`:

   Set the WebSocket connection to your Nova instance:

   ```dotenv
   NATS_BROKER=ws://<instance-ip>/api/nats
   ```

   Example:

   ```dotenv
   NATS_BROKER=ws://192.168.1.100/api/nats
   ```

3. **Optional: Configure Nova API** (if actions need robot control):

   ```dotenv
   NOVA_API=http://<instance-ip>/api
   ```

4. **Install dependencies**:

   ```bash
   uv sync
   ```

5. **Run the service**:

   ```bash
   uv run run.py
   ```

6. **Start a policy server** (the model lives outside this service, reached
   over a websocket with openpi's protocol):

   ```bash
   cd policies/dummy
   uv sync
   uv run python -m dummy_policy      # ws://localhost:8000, GET /healthz answers OK
   ```

   Point the action's `policy_url` at it (`ws://localhost:8000` locally; the
   deployed one is `ws://app-dummy-policy:8080` in the cell).

7. **Verify startup**:
   - Check logs for "Starting TemplateService..."
   - Check for action registration messages
   - Confirm NATS connection established

## Testing Actions

### Run Unit Tests

```bash
uv run pytest
```

### Run Specific Test

```bash
uv run pytest tests/test_echo.py::test_echo_basic
```

### Run with Verbose Output

```bash
uv run pytest -v
```

### Run with Coverage

```bash
uv run pytest --cov=src
```

## Common Issues

### NATS Connection Failed

- **Symptom**: "Failed to connect to NATS" error
- **Solution**:
  - Check NATS_BROKER URL is correct (should be `ws://<ip>/api/nats`)
  - Verify Nova instance is accessible and running
  - Test connectivity: `curl http://<instance-ip>/api/health` or similar
  - Ensure NovaFlow is installed on the Nova instance

### Service Not Registering

- **Symptom**: Service starts but doesn't appear in NovaFlow
- **Solution**:
  - Check NovaFlow is installed on Nova instance
  - Verify NATS connection is established
  - Check service name doesn't conflict with existing services

### Import Errors

- **Symptom**: "ModuleNotFoundError" or "ImportError"
- **Solution**:
  - Run `uv sync` to install dependencies
  - Check `pyproject.toml` has correct dependencies
  - Verify Python version >= 3.11

### Tests Failing

- **Symptom**: pytest errors or assertion failures
- **Solution**:
  - Check test logic matches implementation
  - Run single test to isolate issue: `pytest tests/test_file.py::test_name -v`
  - Check for missing test dependencies

## Development Workflow

1. **Start service** with `uv run run.py` (leave running)
2. **Make code changes** to actions
3. **Restart service** (Ctrl+C, then `uv run run.py` again)
4. **Test in NovaFlow UI** or run `uv run pytest`
5. **Iterate** until satisfied

## Hot Reload (Not Available)

Currently, the service does not support hot reload. You must restart the service after code changes.

## Debugging

### Enable Verbose Logging

Add to your action file:

```python
import loguru
logger = loguru.logger

logger.debug("Detailed debug message")
logger.info("Info message")
logger.warning("Warning message")
logger.error("Error message")
```

### Check Service Status

The service logs show:

- Action registrations
- Incoming requests
- Execution status
- Errors and exceptions

## Environment Variables

| Variable      | Description               | Example                       |
| ------------- | ------------------------- | ----------------------------- |
| `NOVA_API`    | Nova API endpoint         | `http://192.168.1.100`        |
| `NATS_BROKER` | NATS broker WebSocket URL | `ws://192.168.1.100/api/nats` |

## Next Steps

After running locally:

- Test actions work as expected
- Write/update tests
- Deploy to Nova instance with `/install-action`
