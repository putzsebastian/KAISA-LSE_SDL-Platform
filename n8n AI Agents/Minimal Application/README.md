# SDL Agent Tools Service

Standalone tool-backend service for AI agent workflows in Self-Driving Laboratories. Provides the HTTP endpoints that n8n agent workflows call as tools during template-based code generation.

## Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/validate` | POST | Static analysis of Python scripts (syntax, imports, structure) |
| `/save` | POST | Save a script to the filesystem (JSON body) |
| `/save/upload` | POST | Save a script via multipart file upload (legacy) |
| `/simulate/opentrons` | POST | Simulate an Opentrons protocol (enabled — `opentrons` SDK bundled) |
| `/health` | GET | Health check with feature availability flags |
| `/docs` | GET | Interactive API documentation (Swagger UI) |

## Quick Start

```bash
# Clone and run
docker compose up -d

# Check health
curl http://localhost:8100/health

# Validate a script
curl -X POST http://localhost:8100/validate \
  -H "Content-Type: application/json" \
  -d '{"script": "import json\nprint(\"hello\")", "script_type": "analysis"}'

# Save a script
curl -X POST http://localhost:8100/save \
  -H "Content-Type: application/json" \
  -d '{"script": "print(\"hello\")", "path": "test/hello.py"}'
```

## How It Works

### Script Validation (`POST /validate`)

Three-level static analysis without executing the script:

1. **Syntax** — AST parsing catches syntax errors with line numbers
2. **Imports** — Checks every `import` and `from ... import` can be resolved. Device-specific packages (Opentrons, UR robots, Tecan, etc.) that are not installed in this container are in an allowlist so they pass validation.
3. **Structure** — Script-type-specific pattern checks:

| `script_type` | Checks for |
|---------------|------------|
| `analysis` | Results folder, experiment_id, JSON output, try/except |
| `orchestration` | HTTP requests to device servers, execution logging |
| `database` | INSERT statements, ON CONFLICT upsert, connection close |
| `report` | HTML content generation |

The response includes a `summary` field formatted for direct consumption by an LLM agent.

### Script Saving (`POST /save`)

Saves script content to a target path. Relative paths are resolved against `SCRIPTS_BASE_DIR` (default: `/data`). Creates parent directories automatically.

### Opentrons Simulation (`POST /simulate/opentrons`)

Runs the Opentrons protocol simulator on the submitted script and returns the formatted run log. The **`opentrons` Python SDK is bundled** (`opentrons==8.5.0` in `requirements.txt`), so this endpoint is **enabled out of the box**. Note this makes the image larger and the build slower. (The endpoint falls back to HTTP 501 only if `opentrons` is removed from `requirements.txt`.)

## Configuration

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `SCRIPTS_BASE_DIR` | `/data` | Base directory for resolving relative script paths |

## Customizing the Known Modules List

The `DEFAULT_KNOWN_MODULES` set in `app.py` contains module names that are valid in generated scripts but not installed in this container (device drivers, lab-specific packages). To adapt for your lab:

1. Edit the set in `app.py`, or
2. Pass extra modules per-request via the `known_modules` field in the validation request

## n8n Integration

Create n8n sub-workflows that call these endpoints as tools for your AI agents:

**Validate Script** tool (sub-workflow):
```
Execute Workflow Trigger → HTTP POST to http://agent-tools:8100/validate → Format Response
```

**Save Script** tool (sub-workflow):
```
Execute Workflow Trigger → HTTP POST to http://agent-tools:8100/save → Format Response
```

**OT Script Simulation** tool (sub-workflow):
```
Execute Workflow Trigger → HTTP POST to http://agent-tools:8100/simulate/opentrons → Format Response
```

When running alongside other Docker services, use the container name (`sdl-agent-tools`) for service-to-service communication.

## Integrating with an SDL Platform

To connect this service to an existing SDL Docker stack, add to your `docker-compose.yml`:

```yaml
agent-tools:
  build:
    context: ./path/to/agent-tools
    dockerfile: Dockerfile
  container_name: sdl-agent-tools
  ports:
    - "8100:8100"
  volumes:
    - ./experiments:/data  # mount your experiment directory
  environment:
    - SCRIPTS_BASE_DIR=/data
  networks:
    - your_network
  restart: unless-stopped
```

## License

See repository root LICENSE file.
