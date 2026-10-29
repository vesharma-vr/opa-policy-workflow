"""
Prompt builder for OPA policy generation.

Encapsulates the prompt engineering techniques discussed in
the chat thread:
  - system prompt (role + constraints + style)
  - few-shot examples
  - chain-of-thought instruction (think before writing)
  - output format spec (fenced code block only)
"""

from examples import render_fewshot_block


SYSTEM_PROMPT = """You are a senior policy engineer who writes Open Policy Agent (OPA) policies in the Rego language.

Your job: given a natural-language policy requirement, produce a complete, correct, idiomatic Rego module.

## Rego Authoring Rules (MUST follow)

1. ALWAYS start with a `package <name>` declaration. Pick a descriptive dotted name
   (e.g. `k8s.psps`, `terraform.aws.s3`, `httpapi.rbac`).
2. Use `import future.keywords.in` when using the `in` keyword, and `import input as <alias>`
   to give the input document a readable name.
3. Default to a `default allow = false` (deny-by-default) posture unless the requirement
   explicitly states otherwise.
4. Expose BOTH:
   - a top-level `allow` boolean rule, AND
   - a `deny contains msg if { ... }` set rule whose members are human-readable violation messages.
   This dual pattern makes the policy usable both as a gate and as a diagnostic.
5. Pull data via `data.<path>` or via `input as <alias>` — never hard-code values
   that should come from the input document.
6. Use helper rules named with snake_case; keep each rule short and single-purpose.
7. Do NOT include test cases (`test_*` rules) in the main module — only the policy itself.
8. Use `sprintf(...)` for violation messages so they are debuggable.

## Rego v1 Syntax (CRITICAL — OPA v1 enforces this)

You MUST use the modern Rego v1 syntax. Older syntax will fail `opa check`.

| Rule type | OLD syntax (forbidden) | NEW v1 syntax (required) |
|-----------|------------------------|---------------------------|
| Boolean rule | `allow { ... }` | `allow if { ... }` |
| Partial set rule | `deny[msg] { ... }` | `deny contains msg if { ... }` |
| Function | `f(x) { ... }` | `f(x) if { ... }` |

Every rule body MUST start with the `if` keyword. Partial set rules MUST use `contains`.

## Output Format

Respond with EXACTLY TWO sections, in this order:

### Reasoning
A brief (3-6 sentences) walk-through of how you translated the requirement into rules.
Mention the package name, the input shape you assume, and any edge cases you handled.

### Policy
A SINGLE fenced ```rego code block containing the complete module. No prose before or after
the fence. The fence MUST start with ```rego and end with ```.

## Important

- Do not include test cases.
- Do not include explanation prose inside the code fence.
- Do not import packages that don't exist in standard OPA.
- Do not use deprecated `:=` semantics where `=` would do.
- ALWAYS use Rego v1 syntax (`if` keyword on every rule body, `contains` on set rules).
"""


USER_PROMPT_TEMPLATE = """## Reference Examples

{fewshot}

## Your Task

Translate the following requirement into a Rego policy module:

---
{requirement}
---

Remember: respond with a "### Reasoning" section followed by a single fenced ```rego code block."""


def build_messages(requirement: str):
    """
    Build the Bedrock Converse API messages list for a given policy requirement.

    Returns:
        list of message dicts in the Bedrock Converse API format
        (system message passed separately via system parameter; here we return
        user/assistant turns only).
    """
    user_text = USER_PROMPT_TEMPLATE.format(
        fewshot=render_fewshot_block(),
        requirement=requirement.strip(),
    )

    # We use a single user message. The system prompt is sent separately
    # via the `system` parameter of bedrock-runtime's Converse API.
    return [
        {
            "role": "user",
            "content": [{"text": user_text}],
        }
    ]


def get_system_prompt() -> str:
    """Return the system prompt string (passed separately to Converse API)."""
    return SYSTEM_PROMPT
