"""Deploy the concierge to Agent Runtime (Agent Engine) in Vertex AI.

    python deployment/deploy_agent_engine.py create
    python deployment/deploy_agent_engine.py update --resource-id <id>
    python deployment/deploy_agent_engine.py list
    python deployment/deploy_agent_engine.py delete --resource-id <id>

Requires Google Cloud credentials for the project (a service account with Vertex AI User
and Storage Admin, or `gcloud auth application-default login`) and a staging bucket:

    GOOGLE_CLOUD_PROJECT=ihgapp
    GOOGLE_CLOUD_LOCATION=us-central1
    GOOGLE_CLOUD_STAGING_BUCKET=gs://ihgapp-agent-staging

The deployed app runs on Vertex (`GOOGLE_GENAI_USE_VERTEXAI=TRUE`), so no API key travels
with it; tracing goes to Cloud Trace in the same project. The mocked CRS, rate engine and
attribute store go up with the package, which is the point of the mocks: the deployment is
real, the hotel systems behind it are not yet.
"""

from __future__ import annotations

import argparse
import os
import sys

REQUIREMENTS = [
    "google-cloud-aiplatform[adk,agent_engines]>=1.95.0",
    "google-adk>=1.27.0",
    "pydantic>=2.7",
    "pyyaml>=6.0",
    "opentelemetry-exporter-gcp-trace>=1.7.0",
]

ENV_VARS = {
    "GOOGLE_GENAI_USE_VERTEXAI": "TRUE",
    "STAY_TRACE": "cloud",
}


def _settings() -> tuple[str, str, str]:
    project = os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1").strip()
    bucket = os.environ.get("GOOGLE_CLOUD_STAGING_BUCKET", "").strip()
    missing = [
        name
        for name, value in (
            ("GOOGLE_CLOUD_PROJECT", project),
            ("GOOGLE_CLOUD_STAGING_BUCKET", bucket),
        )
        if not value
    ]
    if missing:
        sys.exit(f"set {', '.join(missing)} before deploying")
    return project, location, bucket


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("create", "update", "list", "delete"))
    parser.add_argument("--resource-id", default="")
    parser.add_argument("--display-name", default="stay-agent-adk")
    args = parser.parse_args()

    import vertexai
    from vertexai import agent_engines

    project, location, bucket = _settings()
    vertexai.init(project=project, location=location, staging_bucket=bucket)

    if args.command == "list":
        for engine in agent_engines.list():
            print(f"{engine.resource_name}\t{engine.display_name}")
        return

    if args.command == "delete":
        if not args.resource_id:
            sys.exit("--resource-id is required to delete")
        agent_engines.get(args.resource_id).delete(force=True)
        print(f"deleted {args.resource_id}")
        return

    # Imported here so `list`/`delete` do not need the model configuration the agent
    # asserts at import time.
    os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "TRUE")
    from stay_agent.agent import root_agent

    app = agent_engines.AdkApp(agent=root_agent, enable_tracing=True)

    if args.command == "create":
        remote = agent_engines.create(
            agent_engine=app,
            display_name=args.display_name,
            description="Hotel concierge over versioned room attributes, with a money corridor.",
            requirements=REQUIREMENTS,
            extra_packages=["stay_agent"],
            env_vars=ENV_VARS,
        )
    else:
        if not args.resource_id:
            sys.exit("--resource-id is required to update")
        remote = agent_engines.update(
            resource_name=args.resource_id,
            agent_engine=app,
            requirements=REQUIREMENTS,
            extra_packages=["stay_agent"],
            env_vars=ENV_VARS,
        )

    print(remote.resource_name)


if __name__ == "__main__":
    main()
