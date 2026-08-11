# Loro Enterprise-Beta Reference Bundle

This bundle pins the repository-owned portion of Loro's `0.8` reference deployment. It is for
synthetic non-production validation. It does not provide corporate identity, TLS termination,
immutable retention, production database controls, or provider governance.

1. Review `manifest.json` and replace every organization-owned `TBD` in the enterprise evidence
   register with a controlled reference.
2. Set synthetic values for `LORO_REFERENCE_POSTGRES_PASSWORD` and
   `LORO_AUDIT_COLLECTOR_TOKEN`.
3. Start the local dependencies with `docker compose -f deploy/reference/compose.yaml up -d`.
4. Set `LORO_POSTGRES_DSN`, `LORO_IDENTITY_SUBJECT`, and `LORO_IDENTITY_TENANT`.
5. Pin and distribute `managed.toml` using `LORO_MANAGED_CONFIG` and its documented aggregate
   digest.
6. Run `loro config check --strict`, `loro doctor`, the Postgres migrations, and
   `loro operations benchmark --strict`.

Use the administrator and operator guides in `docs/` for rollout, evidence capture, rollback,
and cleanup. Never use the compose credentials or data for production.
