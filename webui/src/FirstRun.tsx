import { useEffect, useState } from "react";
import { request } from "./api";

/**
 * First-run setup.
 *
 * The UI used to assume a provider was already configured: open it on a fresh
 * folder and the first message just failed, with nothing saying why. This
 * shows the same readiness `loro get-started` reports, and lets a provider be
 * chosen without leaving the browser.
 *
 * Hosted keys entered here are stored in Loro's OS-keyring-backed vault and
 * are never written to project config or returned to the browser.
 */

type Step = {
  id: string;
  label: string;
  ok: boolean;
  offline?: boolean;
  detail: string;
  action: string;
};

type Readiness = {
  ready?: boolean;
  offline?: boolean;
  workspace?: string;
  steps?: Step[];
  blocking?: string[];
};

type Provider = {
  name: string;
  display_name: string;
  default_model: string;
  api_key_env: string;
  needs_key: boolean;
  credential_ready?: boolean;
};

export function FirstRun({
  onReady,
  setError,
}: {
  onReady: () => void;
  setError: (message: string) => void;
}) {
  const [readiness, setReadiness] = useState<Readiness | null>(null);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [chosen, setChosen] = useState("");
  const [model, setModel] = useState("");
  const [saving, setSaving] = useState(false);
  const [credential, setCredential] = useState("");

  useEffect(() => {
    (async () => {
      try {
        const [state, list] = await Promise.all([
          request<Readiness>("/api/onboarding/readiness"),
          request<{ providers: Provider[]; default_provider?: string; default_model?: string }>("/api/onboarding/providers"),
        ]);
        setReadiness(state);
        setProviders(list.providers || []);
        setChosen(list.default_provider || "");
        setModel(list.default_model || "");
      } catch (problem) {
        setError((problem as Error).message);
      }
    })();
  }, [setError]);

  async function apply(provider: string, chosenModel = "") {
    setSaving(true);
    try {
      const state = await request<Readiness>("/api/onboarding/configure", {
        method: "POST",
        body: JSON.stringify({ provider, model: chosenModel, credential }),
      });
      setCredential("");
      setReadiness(state);
      if (state.ready) onReady();
    } catch (problem) {
      setError((problem as Error).message);
    } finally {
      setSaving(false);
    }
  }

  if (!readiness) return null;

  const selected = providers.find((item) => item.name === chosen);

  return (
    <div className="first-run">
      <div className="first-run-card">
        <span className="parrot" aria-hidden="true">🦜</span>
        <h1>Set up this folder</h1>
        <p>
          Loro works per folder. <b>{readiness.workspace}</b> is not ready yet.
        </p>

        <ol className="first-run-steps">
          {(readiness.steps || []).map((step) => (
            <li key={step.id} className={step.ok ? "done" : "todo"}>
              <span className="mark" aria-hidden="true">{step.ok ? "✓" : "•"}</span>
              <div>
                <b>
                  {step.label}
                  {step.offline && <i className="tag">offline</i>}
                </b>
                <small>{step.detail}</small>
                {!step.ok && <em>{step.action}</em>}
              </div>
            </li>
          ))}
        </ol>

        <div className="first-run-choose">
          <label htmlFor="providerPick">Provider</label>
          <select
            id="providerPick"
            value={chosen}
            onChange={(event) => { const value = event.target.value; setChosen(value); setModel(providers.find((item) => item.name === value)?.default_model || ""); }}
          >
            <option value="">Choose a provider…</option>
            {providers.map((provider) => (
              <option key={provider.name} value={provider.name}>
                {provider.display_name}
                {provider.needs_key ? "" : " — no key needed"}
              </option>
            ))}
          </select>

          {selected && (
            <>
              <label htmlFor="modelPick">Model</label>
              <input
                id="modelPick"
                value={model}
                onChange={(event) => setModel(event.target.value)}
                placeholder={selected.default_model}
              />
              {selected.needs_key && (
                <><label htmlFor="providerCredential">API key <small>{selected.credential_ready ? "leave blank to keep the existing vault entry" : `or export ${selected.api_key_env}`}</small></label><input id="providerCredential" type="password" autoComplete="off" value={credential} onChange={(event) => setCredential(event.target.value)} placeholder={selected.credential_ready ? "Credential already configured" : "Stored in the OS keyring"} /><p className="first-run-key">Keys entered here go to Loro's secure credential vault, never project configuration.</p></>
              )}
            </>
          )}

          <div className="first-run-actions">
            <button
              className="primary-action"
              type="button"
              disabled={!chosen || saving}
              onClick={() => void apply(chosen, model)}
            >
              {saving ? "Saving…" : "Use this provider"}
            </button>
            <button
              className="secondary-action"
              type="button"
              disabled={saving}
              onClick={() => void apply("mock")}
            >
              Try it offline first
            </button>
          </div>
          <p className="first-run-note">
            The offline provider answers without a key, so you can see the whole loop before finding
            one.
          </p>
        </div>
      </div>
    </div>
  );
}
