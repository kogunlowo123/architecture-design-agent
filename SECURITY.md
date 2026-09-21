# Security Policy

## Supported versions

Security fixes are released for the latest minor version on the `main` branch.

| Version | Supported |
| ------- | --------- |
| 0.1.x   | Yes       |

## Reporting a vulnerability

Do not open a public issue for security reports. Use GitHub's private vulnerability reporting (the
**Report a vulnerability** button on this repository's **Security** tab) and include a description and
impact, the affected version or commit, and a minimal reproduction. Please remove any confidential
requirements first. You can expect an acknowledgement within 3 business days and a triage decision within
10 business days.

## Trust boundary

| Input | Trust |
| ----- | ----- |
| Requirements and design files | Untrusted. Parsed with a safe YAML loader, size-limited and schema-validated |
| Pricing file | Operator input, validated to known kinds, fields and non-negative numbers |
| Model responses used for summaries | Untrusted text, accepted only if grounded in supplied facts |

## Security controls

| Threat | Control | Location |
| ------ | ------- | -------- |
| Code execution through file parsing | `yaml.safe_load` only, and a 1 MB size limit | `config.py` |
| Malformed input | Strict pydantic models that forbid unknown fields, bounded numbers and a restricted name pattern | `models.py` |
| Markup injection into reports | Table cells and Mermaid labels escaped, identifiers restricted | `security.py`, `render.py` |
| Path traversal in output names | File names come from a slug of the design name, written inside the chosen directory | `render.write_outputs` |
| Prompt injection into summaries | The model sees only aggregate counts. Free text and names are never sent. Output with numbers absent from the facts is discarded | `agents/summary.py` |
| Secret leakage | API keys are `SecretStr`. Errors and logs are redacted | `config.py`, `logging_setup.py` |
| Vulnerable dependencies | `pip-audit`, CodeQL | `.github/` |

## Known limits

- The catalogue is illustrative. Do not treat its availability or price figures as commitments.
- The threat model is a checklist and does not replace a security review of the actual system.
