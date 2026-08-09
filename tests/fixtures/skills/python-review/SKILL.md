---
name: python-review
description: Review Python code for correctness, security, and maintainability.
license: MIT
compatibility: Requires Loro with read-only file access.
metadata:
  owner: loro-test-suite
  version: "1.0"
allowed-tools: skill.read
---

Review the requested Python code. Lead with concrete findings ordered by severity, cite the
affected file and line, and do not modify files unless the user separately requests a change.

Load `references/CHECKLIST.md` when a detailed review checklist is useful.
