---
name: install
description: Install the NovaFlow action service to a Nova instance using the Nova CLI. Use this when deploying a new or updated action to a running Nova instance.
---

# Install Action

This skill installs the NovaFlow action service to a Nova instance.

## Prerequisites

- Nova CLI installed and configured
- Container registry configured in `.nova` file
- Logged into the container registry

## Steps

1. Ensure the image registry is set in `.nova`:

   ```yaml
   imageRegistry: your-registry.io/novaflow-action-template
   ```

2. Login to your container registry:

   ```bash
   docker login your-registry.io
   ```

3. Install the action to the Nova instance:
   ```bash
   nova app install .
   ```

## Verification

After installation, the action should appear in the NovaFlow UI and be available for use in behavior trees.

## Troubleshooting

- **Build fails**: Ensure Docker is running and you have network access
- **Push fails**: Verify you're logged into the correct registry
- **Deploy fails**: Check Nova CLI is configured for the target instance
- **Nova CLI issues**: See the [Nova CLI README](https://github.com/wandelbotsgmbh/nova-cli/blob/main/README.md) for installation and configuration details
