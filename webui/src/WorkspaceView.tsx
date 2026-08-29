import { useEffect, useState } from "react";
import { request, requestBlob } from "./api";

type FileItem = { path: string; size: number; media_type: string; previewable: boolean };
type WorkspaceItem = { name: string; path: string; active: boolean; launch_argv: string[] };

export function WorkspaceView({ setError }: { setError: (message: string) => void }) {
  const [artifacts, setArtifacts] = useState<FileItem[]>([]);
  const [changes, setChanges] = useState({ status: "", diff: "", staged_diff: "" });
  const [workspaces, setWorkspaces] = useState<WorkspaceItem[]>([]);
  const [selected, setSelected] = useState<FileItem | null>(null);
  const [preview, setPreview] = useState("");
  const [previewUrl, setPreviewUrl] = useState("");

  async function refresh() {
    try {
      const [workspace, catalog] = await Promise.all([
        request<{ artifacts: FileItem[]; changes: typeof changes }>("/api/workspace/artifacts"),
        request<{ workspaces: WorkspaceItem[] }>("/api/workspaces"),
      ]);
      setArtifacts(workspace.artifacts);
      setChanges(workspace.changes);
      setWorkspaces(catalog.workspaces);
    } catch (reason) { setError(String(reason)); }
  }

  useEffect(() => { void refresh(); }, []);
  useEffect(() => () => { if (previewUrl) URL.revokeObjectURL(previewUrl); }, [previewUrl]);

  async function open(item: FileItem) {
    try {
      const blob = await requestBlob(`/api/workspace/file?path=${encodeURIComponent(item.path)}`);
      if (previewUrl) URL.revokeObjectURL(previewUrl);
      setSelected(item);
      if (item.media_type.startsWith("text/") || /json|yaml|javascript/.test(item.media_type)) {
        setPreview(await blob.text());
        setPreviewUrl("");
      } else {
        setPreview("");
        setPreviewUrl(URL.createObjectURL(blob));
      }
    } catch (reason) { setError(String(reason)); }
  }

  async function download(item: FileItem) {
    try {
      const blob = await requestBlob(`/api/workspace/file?path=${encodeURIComponent(item.path)}&download=true`);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url; anchor.download = item.path.split("/").pop() || "artifact"; anchor.click();
      URL.revokeObjectURL(url);
    } catch (reason) { setError(String(reason)); }
  }

  async function copyLaunch(item: WorkspaceItem) {
    await navigator.clipboard.writeText(item.launch_argv.map((part) => JSON.stringify(part)).join(" "));
  }

  return <div className="page workspace-page">
    <header className="page-header"><div><small>GOVERNED OUTPUTS</small><h1>Workspace</h1><p>Review artifacts and repository changes without widening runtime authority.</p></div><button onClick={() => void refresh()}>Refresh</button></header>
    <section className="workspace-switcher"><h2>Projects</h2>{workspaces.map((item) => <article key={item.path}><div><b>{item.name}</b><small>{item.path}</small></div>{item.active ? <span>Active</span> : <button onClick={() => void copyLaunch(item)}>Copy launch command</button>}</article>)}</section>
    <div className="artifact-layout"><aside className="artifact-list"><h2>Artifacts</h2>{artifacts.map((item) => <button key={item.path} className={selected?.path === item.path ? "selected" : ""} onClick={() => void open(item)}><b>{item.path}</b><small>{Math.ceil(item.size / 1024)} KB · {item.media_type}</small></button>)}</aside>
      <section className="artifact-preview">{selected ? <><header><div><small>PREVIEW</small><h2>{selected.path}</h2></div><button onClick={() => void download(selected)}>Download</button></header>{preview ? <pre>{preview}</pre> : previewUrl && selected.media_type.startsWith("image/") ? <img src={previewUrl} alt={selected.path} /> : previewUrl ? <iframe src={previewUrl} title={selected.path} /> : null}</> : <div className="empty-panel"><span>◇</span><h2>Select an artifact</h2><p>Text, images, and PDFs can be reviewed here.</p></div>}</section></div>
    <section className="change-review"><h2>Repository changes</h2><div className="change-grid"><article><h3>Status</h3><pre>{changes.status || "Clean workspace"}</pre></article><article><h3>Unstaged diff</h3><pre>{changes.diff || "No unstaged changes"}</pre></article><article><h3>Staged diff</h3><pre>{changes.staged_diff || "No staged changes"}</pre></article></div></section>
  </div>;
}
