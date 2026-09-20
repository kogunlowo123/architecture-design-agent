# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses semantic versioning.

## [Unreleased]

### Changed

- The container image declares a `HEALTHCHECK` and no longer writes bytecode files.

## [0.1.0]

### Added

- Requirements schema, driver weighting and open questions.
- Weighted style matrix for five architecture styles with disqualification rules.
- Component agent that sizes a graph for each style, with AWS, Azure, GCP and generic product names.
- Reliability model with zone redundancy, standby regions and single-point-of-failure detection.
- Cost model with price overrides, budget check and cost levers.
- STRIDE threat model with control detection.
- Twenty-two numbered review rules, usable on generated or hand-written designs.
- Draft ADRs, Markdown, JSON, YAML and Mermaid output.
- Optional model-written summary that only sees aggregate facts.
- Command-line interface, Docker image and CI workflows.
