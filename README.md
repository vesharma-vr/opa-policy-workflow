# OPA Policy Generation Workflow (AWS Bedrock)

A Python workflow that uses **AWS Bedrock + Sonnet 5** to generate
**Open Policy Agent (OPA) policies in Rego** from natural-language requirements.

It demonstrates the prompt-engineering techniques from our chat thread:

| Technique | Where it's used |
|---|---|
| **System prompt + role** | `prompt_builder.SYSTEM_PROMPT` (senior policy engineer persona) |
| **Few-shot examples** | `examples.FEWSHOT_EXAMPLES` (3 sample requirement→rego pairs) |
| **Chain-of-thought** | The model is asked for a `### Reasoning` section before the code |
| **Output format constraint** | Single fenced ```rego block, no surrounding prose |
| **Prompt chaining (refinement loop)** | `workflow._build_refinement_messages()` — validation errors are fed back |

---

## Architecture

```
                ┌──────────────────────────────────────────────────┐
                │                  workflow.py                      │
                │   generate_opa_policy(requirement)               │
                └──────────────────────────────────────────────────┘
                          │
                          ▼
              ┌────────────────────────┐
              │   prompt_builder.py    │   system prompt + few-shot
              │   build_messages()     │   + chain-of-thought instruction
              └────────────────────────┘
                          │
                          ▼
              ┌────────────────────────┐
              │   bedrock_client.py    │   boto3 converse() call
              │   BedrockClient       │   model: Sonnet 5        
              └────────────────────────┘
                          │
                          ▼
              ┌────────────────────────┐
              │   validator.py         │   1. extract ```rego block
              │   extract_rego()       │   2. run `opa check`
              │   validate_rego()      │   3. run `opa eval` smoke test
              └────────────────────────┘
                          │
                ┌─────────┴─────────┐
                │                   │
                ▼                   ▼
            VALID              INVALID
            return             feed error back to LLM
                                (max 3 attempts)
```

---

## File Map

| File | Purpose |
|---|---|
| `config.py` | Bedrock & workflow configuration (model, region, retries). |
| `examples.py` | Few-shot Rego examples (K8s, Terraform, HTTP API). |
| `prompt_builder.py` | System prompt + user prompt template. |
| `bedrock_client.py` | boto3 wrapper around Bedrock Converse API. |
| `validator.py` | Rego extraction from markdown + OPA CLI validation. |
| `workflow.py` | Main orchestrator with refinement loop. |
| `demo.py` | End-to-end demo with 5 realistic requirements. |
| `requirements.txt` | Python deps (`boto3` + `botocore`). |

---

## Prerequisites

### 1. AWS credentials

You need an AWS account with access to **Amazon Bedrock** and permission to
invoke Sonnet 5 (`us.anthropic.claude-sonnet-5`).

```bash
# Option A: env vars
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
export AWS_DEFAULT_REGION=us-east-1

# Option B: AWS profile (~/.aws/credentials)
# Then set profile_name in config.BedrockConfig

# Option C: IAM role (EC2/ECS/Lambda) — no extra config needed.
```

Our org's SCP only allows some models — `global.*` profiles, Sonnet 5.5 and
Opus 4.8+ are denied. Verify a model before switching:
`aws bedrock-runtime converse --model-id <id> --messages '[{"role":"user","content":[{"text":"hi"}]}]'`

### 2. Install the OPA binary (for validation)

```bash
# macOS
brew install opa

# Linux
curl -L -o opa https://openpolicyagent.org/downloads/latest/opa_linux_amd64
chmod +x opa && sudo mv opa /usr/local/bin/

# Verify
opa version
```

> If OPA isn't installed, the workflow still generates policies — it just skips
> validation (and so won't run the refinement loop).

### 3. Python environment

```bash
cd /home/z/my-project/download/opa-policy-workflow
python -m venv .venv
source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

---

## Usage

### Single policy

```python
from workflow import generate_opa_policy

result = generate_opa_policy(
    "Deny Kubernetes pods whose image uses the ':latest' tag.",
    save_dir="./policies",
)

print(result.rego_code)
print(result.validation_status)   # ValidationStatus.VALID
print(result.iterations)          # 1 = first-shot success
print(result.saved_path)          # ./policies/k8s_images_latest.rego
```

### CLI

```bash
python workflow.py "Deny K8s pods whose image uses the ':latest' tag."
```

### Demo (5 example policies)

```bash
python demo.py
```

Produces:

```
policies/
├── k8s_image_latest.rego
├── terraform_aws_ec2.rego
├── httpapi_admin.rego
├── cicd_production_deploy.rego
├── storage_s3_bucket_policy.rego
└── manifest.json
```

---

## Customizing

| What you want to change | Where |
|---|---|
| Use a different model (e.g. Sonnet 4.6, Haiku 4.5) | `config.py → BedrockConfig.model_id` |
| Generate test cases too | Edit `prompt_builder.SYSTEM_PROMPT` to allow `test_*` rules |
| Use a different region | `config.py → BedrockConfig.region_name` |
| Disable CLI validation | `config.py → WorkflowConfig.validate_with_opa_cli = False` |
| Add more few-shot examples | `examples.py → FEWSHOT_EXAMPLES` |
| More refinement attempts | `config.py → WorkflowConfig.max_refinement_iterations` |

---

## Sample Output

Given the requirement:

> *"Deny Kubernetes pods that run as root."*

The workflow produces (Rego v1 syntax — `if` keyword, `contains` on set rules):

```rego
package k8s.psps

import future.keywords.in
import data.k8s.pods

default allow = false

allow if {
    count(deny) == 0
}

deny contains msg if {
    pod := pods[_]
    not pod_non_root(pod)
    msg := sprintf(
        "pod %q must set spec.securityContext.runAsNonRoot: true",
        [pod.metadata.name],
    )
}

pod_non_root(pod) if {
    pod.spec.securityContext.runAsNonRoot == true
}

pod_non_root(pod) if {
    c := pod.spec.containers[_]
    c.securityContext.runAsNonRoot == true
}
```

And `result.validation_status == ValidationStatus.VALID`.

> **Note**: OPA v1 (2025+) requires the new Rego syntax: every rule body starts
> with `if`, and partial set rules use `contains`. The workflow is configured
> for Rego v1 — older `rule { ... }` and `rule[k] { ... }` syntax will fail
> `opa check`. If you must target OPA v0.x, you can either downgrade OPA or add
> `import rego.v0` at the top of each generated policy.

---

## How it ties back to prompt engineering

| Prompt-engineering lever | Effect here |
|---|---|
| **System prompt defines role** | "You are a senior policy engineer" — anchors tone & depth |
| **Explicit output format** | Eliminates parsing failures (model knows to emit fenced ```rego) |
| **Few-shot examples** | Model learns the deny-by-default + dual `allow`/`deny[msg]` pattern |
| **Chain-of-thought section** | Model verbalizes its approach before committing code → fewer logic bugs |
| **Validation feedback loop** | Compiler errors become next-turn context → self-healing on syntax mistakes |

---

## Notes & Limitations

- The workflow uses the **Bedrock Converse API** (not the legacy InvokeModel
  with raw Anthropic format). Converse is the recommended path for new builds.
- Rego generated by the model is **syntactically validated** but not
  **semantically tested** — `opa check` ensures it compiles, but you should
  still author test cases with `opa test` for behavior.
- The few-shot examples intentionally cover **Kubernetes, Terraform, and HTTP
  API** domains — three of the most common OPA use cases. Adding domain
  examples (e.g. CI/CD pipelines, Kafka ACLs) will improve output quality
  in those areas.
- Token cost per generation: ~1,500 input tokens (system + few-shot), ~1,000
  output tokens. Check current Bedrock pricing for Sonnet 5.
