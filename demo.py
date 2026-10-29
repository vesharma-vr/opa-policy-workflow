"""
Demo: end-to-end OPA policy generation across multiple domains.

Runs the workflow against several realistic requirements to showcase:
  - Kubernetes admission control
  - Terraform IaC policy
  - HTTP API RBAC
  - CI/CD pipeline gate

Each result is saved under ./policies/<package>.rego and a JSON manifest is
written summarizing the run.

Run:
    python demo.py

Requires AWS credentials with bedrock:InvokeModel permission.
"""

import json
import logging
import sys
from pathlib import Path

from workflow import generate_opa_policy


# Five diverse requirements — chosen to exercise different Rego idioms
# (set-based deny rules, default allow, helper rules, imports).
DEMO_REQUIREMENTS = [
    (
        "Kubernetes admission control: deny pods that use the ':latest' "
        "image tag in any container."
    ),
    (
        "Terraform AWS: deny EC2 instances that don't have a Name tag "
        "or whose instance_type is larger than 'm5.2xlarge'."
    ),
    (
        "HTTP API: allow a request to /admin/* only if the JWT contains "
        "scope 'admin:read' OR 'admin:write'. Deny otherwise."
    ),
    (
        "CI/CD gate: allow a deployment to 'production' only if the "
        "build has 'tests_passed == true' AND 'security_scan_passed == true'."
    ),
    (
        "Storage: deny any S3 bucket policy that grants '*' (everyone) "
        "Principal, unless the action is limited to 's3:GetObject'."
    ),
]


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    )

    out_dir = Path("./policies")
    out_dir.mkdir(exist_ok=True)

    results_manifest = []
    for i, requirement in enumerate(DEMO_REQUIREMENTS, start=1):
        print("\n" + "=" * 70)
        print(f"DEMO {i}/{len(DEMO_REQUIREMENTS)}")
        print("=" * 70)
        print(f"Requirement: {requirement}\n")

        result = generate_opa_policy(requirement, save_dir=str(out_dir))
        print("\n--- Generated Rego ---")
        print(result.rego_code)
        print("\n--- Reasoning ---")
        print(result.reasoning)
        print("\n--- Validation ---")
        print(f"  status:    {result.validation_status.value}")
        print(f"  errors:    {result.validation_errors or '(none)'}")
        print(f"  iterations:{result.iterations}")
        print(f"  saved to:  {result.saved_path}")

        results_manifest.append(result.to_dict())

    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(results_manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"\n\nManifest written to: {manifest_path}")


if __name__ == "__main__":
    sys.exit(main())
