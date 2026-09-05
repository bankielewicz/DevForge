# DevForge POC

This repository owns the Rust CLI, external policy, fixed test harness, project-local installer, acceptance tests, and GitHub workflows. Its companion https://github.com/bankielewicz/DevForgeAI owns conversational skills, agent definitions, and example project expertise.

Authorized CLI maintenance may modify this repository. An agent assigned only to a DevForgeAI candidate or generated application must not edit this repository to make its work pass. A separate repository is organizational separation; enforce the filesystem boundary through the launcher and keep acceptance in the authority terminal.

Run cargo fmt --check, cargo clippy --locked --all-targets -- -D warnings, cargo build --locked, and python3 -m unittest discover -s tests -p 'test_*.py'. Validate the sibling framework with scripts/validate_framework.py when its artifacts change. Exercise scripts/demo.py for changes affecting the gate lifecycle.

Do not relabel structural checks or fixture execution as model behavioral evaluation. Keep no-fallback isolation and precise failure statuses. Do not change the test expectations merely to accommodate a failing implementation.
