"""
Few-shot example library for OPA/Rego v1 policy generation.

Each example pairs a natural-language policy requirement with a well-formed
Rego v1 module. The generator prepends these to the user's request so the model
learns:
  - the expected Rego v1 idiom (package, import, default deny, `if` keyword, `contains` for set rules)
  - the desired output format (fenced code block only)
  - the typical structure (deny[] / allow[] rules + helper rules)

OPA v1 (released 2025) requires:
  - `if` keyword before every rule body
  - `contains` keyword for partial set rules (e.g. `deny contains msg if { ... }`)

Keep examples small (under ~40 lines each) so they don't blow up context.
"""

FEWSHOT_EXAMPLES = [
    {
        "requirement": (
            "Deny Kubernetes pods that run as root (runAsNonRoot is false "
            "or not set)."
        ),
        "rego": """package k8s.psps

import future.keywords.in
import data.k8s.pods

# Default: deny everything that hasn't been explicitly allowed.
default allow = false

# A pod is allowed only if NO container violates the non-root rule.
allow if {
    count(deny) == 0
}

deny contains msg if {
    pod := pods[_]
    not pod_non_root(pod)
    msg := sprintf("pod %q must set spec.securityContext.runAsNonRoot: true", [pod.metadata.name])
}

# Helper: returns true if pod declares runAsNonRoot: true at pod OR container level.
pod_non_root(pod) if {
    pod.spec.securityContext.runAsNonRoot == true
}

pod_non_root(pod) if {
    c := pod.spec.containers[_]
    c.securityContext.runAsNonRoot == true
}
""",
    },
    {
        "requirement": (
            "Allow Terraform AWS S3 buckets only if they enable "
            "versioning AND server-side encryption (AES-256 or KMS)."
        ),
        "rego": """package terraform.aws.s3

import future.keywords.in
import input as tf

# Default deny: a bucket must satisfy ALL required controls.
default allow = false

allow if {
    count(deny) == 0
}

deny contains msg if {
    bucket := tf.resource.aws_s3_bucket[_]
    not bucket_has_versioning(bucket)
    msg := sprintf("bucket %q must enable versioning", [bucket.name])
}

deny contains msg if {
    bucket := tf.resource.aws_s3_bucket[_]
    not bucket_has_encryption(bucket)
    msg := sprintf("bucket %q must enable server-side encryption (AES-256 or KMS)", [bucket.name])
}

bucket_has_versioning(bucket) if {
    some v
    tf.resource.aws_s3_bucket_versioning[v].bucket == bucket.id
    tf.resource.aws_s3_bucket_versioning[v].versioning[0].enabled == true
}

bucket_has_encryption(bucket) if {
    some e
    tf.resource.aws_s3_bucket_server_side_encryption_configuration[e].bucket == bucket.id
    rule := tf.resource.aws_s3_bucket_server_side_encryption_configuration[e].rule[0]
    rule.apply_server_side_encryption_by_default.sse_algorithm in {"AES256", "aws:kms"}
}
""",
    },
    {
        "requirement": (
            "Allow HTTP API requests only if the caller has role 'admin' "
            "OR owns the resource being accessed."
        ),
        "rego": """package httpapi.rbac

import future.keywords.in
import input as req

default allow = false

allow if {
    is_admin
}

allow if {
    is_owner
}

# An admin is any user whose JWT carries role == "admin".
is_admin if {
    req.user.role == "admin"
}

# Ownership: the resource owner field must equal the requester's user id.
is_owner if {
    req.resource.owner == req.user.id
}

# Diagnostic rules: explain WHY a request was denied.
deny contains msg if {
    not allow
    msg := sprintf("user %q (role=%q) is not allowed to access resource %q", [req.user.id, req.user.role, req.resource.id])
}
""",
    },
]


def render_fewshot_block() -> str:
    """Render the few-shot examples into a single text block for the prompt."""
    blocks = []
    for i, ex in enumerate(FEWSHOT_EXAMPLES, start=1):
        blocks.append(
            f"### Example {i}\n"
            f"Requirement: {ex['requirement']}\n\n"
            f"```rego\n{ex['rego']}\n```"
        )
    return "\n\n".join(blocks)
