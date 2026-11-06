import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  ApiError,
  askStream,
  getApiKey,
  setApiKey,
  type Conversation,
  type DocumentInfo,
  type Health,
  type Source,
  type Workspace,
} from "./api";
import { Answer } from "./components/Answer";

interface UiMessage {
  key: string;
  role: "user" | "assistant";
  content: string;
  sources: Source[];
  streaming?: boolean;
}

const fmtSize = (n: number) =>
  n > 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`;

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  // Deep links: ?ws=<workspace id>&c=<conversation id>
  const initial = useRef(new URLSearchParams(window.location.search));
  const [wsId, setWsId] = useState<number | null>(Number(initial.current.get("ws")) || null);
  const [documents, setDocuments] = useState<DocumentInfo[]>([]);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [conversationId, setConversationId] = useState<number | null>(null);
  const [messages, setMessages] = useState<UiMessage[]>([]);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const [focus, setFocus] = useState<{ msg: string; n: number } | null>(null);
  const [showSettings, setShowSettings] = useState(false);
  const [keyDraft, setKeyDraft] = useState(getApiKey());
  const fileInput = useRef<HTMLInputElement>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const abort = useRef<AbortController | null>(null);

  const fail = (e: unknown) => setError(e instanceof Error ? e.message : String(e));

  const loadWorkspaces = useCallback(async () => {
    try {
      const list = await api.listWorkspaces();
      setWorkspaces(list);
      setWsId((cur) => (cur && list.some((w) => w.id === cur) ? cur : (list[0]?.id ?? null)));
      setError(null);
    } catch (e) {
      fail(e);
      if (e instanceof ApiError && e.status === 401) setShowSettings(true);
    }
  }, []);

  useEffect(() => {
    api.health().then(setHealth).catch(fail);
    loadWorkspaces();
  }, [loadWorkspaces]);

  const loadWorkspaceData = useCallback(async (id: number) => {
    try {
      const [docs, convs] = await Promise.all([api.listDocuments(id), api.listConversations(id)]);
      setDocuments(docs);
      setConversations(convs);
    } catch (e) {
      fail(e);
    }
  }, []);

  useEffect(() => {
    setConversationId(null);
    setMessages([]);
    setFocus(null);
    if (wsId) loadWorkspaceData(wsId);
    else {
      setDocuments([]);
      setConversations([]);
    }
  }, [wsId, loadWorkspaceData]);

  useEffect(() => {
    const linked = Number(initial.current.get("c"));
    if (wsId && linked) {
      initial.current.delete("c");
      openConversation(linked);
    }
  }, [wsId]); // only reacts to the workspace being set; the linked id is consumed once

  useEffect(() => {
    const params = new URLSearchParams();
    if (wsId) params.set("ws", String(wsId));
    if (conversationId) params.set("c", String(conversationId));
    const query = params.toString();
    window.history.replaceState(null, "", query ? `?${query}` : window.location.pathname);
  }, [wsId, conversationId]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages]);

  const createWorkspace = async () => {
    const name = window.prompt("Workspace name");
    if (!name?.trim()) return;
    try {
      const ws = await api.createWorkspace(name.trim());
      await loadWorkspaces();
      setWsId(ws.id);
    } catch (e) {
      fail(e);
    }
  };

  const deleteWorkspace = async () => {
    const ws = workspaces.find((w) => w.id === wsId);
    if (!ws || !window.confirm(`Delete workspace "${ws.name}" and all its documents?`)) return;
    try {
      await api.deleteWorkspace(ws.id);
      setWsId(null);
      await loadWorkspaces();
    } catch (e) {
      fail(e);
    }
  };

  const upload = async (files: FileList | File[]) => {
    if (!wsId) return;
    for (const file of Array.from(files)) {
      setUploading(file.name);
      try {
        await api.uploadDocument(wsId, file);
        setError(null);
      } catch (e) {
        fail(e);
      }
    }
    setUploading(null);
    loadWorkspaceData(wsId);
    loadWorkspaces();
  };

  const removeDocument = async (doc: DocumentInfo) => {
    if (!window.confirm(`Remove "${doc.filename}"?`)) return;
    try {
      await api.deleteDocument(doc.id);
      if (wsId) loadWorkspaceData(wsId);
    } catch (e) {
      fail(e);
    }
  };

  const openConversation = async (id: number) => {
    try {
      const detail = await api.getConversation(id);
      setConversationId(id);
      setFocus(null);
      setMessages(
        detail.messages.map((m) => ({
          key: `m${m.id}`,
          role: m.role,
          content: m.content,
          sources: m.sources,
        })),
      );
    } catch (e) {
      fail(e);
    }
  };

  const newChat = () => {
    abort.current?.abort();
    setConversationId(null);
    setMessages([]);
    setFocus(null);
  };

  const ask = async (e?: { preventDefault: () => void }) => {
    e?.preventDefault();
    const q = question.trim();
    if (!q || !wsId || busy) return;
    setQuestion("");
    setBusy(true);
    setError(null);
    const key = `a${Date.now()}`;
    setMessages((m) => [
      ...m,
      { key: `u${Date.now()}`, role: "user", content: q, sources: [] },
      { key, role: "assistant", content: "", sources: [], streaming: true },
    ]);
    const patch = (fn: (m: UiMessage) => UiMessage) =>
      setMessages((all) => all.map((m) => (m.key === key ? fn(m) : m)));

    abort.current = new AbortController();
    try {
      await askStream(
        wsId,
        q,
        conversationId,
        {
          onSources: (convId, sources) => {
            setConversationId(convId);
            patch((m) => ({ ...m, sources }));
          },
          onToken: (text) => patch((m) => ({ ...m, content: m.content + text })),
          onDone: () => {
            patch((m) => ({ ...m, streaming: false }));
            api.listConversations(wsId).then(setConversations).catch(fail);
          },
          onError: (msg) => {
            patch((m) => ({ ...m, streaming: false }));
            setError(msg);
          },
        },
        abort.current.signal,
      );
    } catch (err) {
      if (!(err instanceof DOMException && err.name === "AbortError")) fail(err);
    } finally {
      patch((m) => ({ ...m, streaming: false }));
      setBusy(false);
    }
  };

  const saveKey = () => {
    setApiKey(keyDraft.trim());
    setShowSettings(false);
    loadWorkspaces();
  };

  const focusedMessage = focus ? messages.find((m) => m.key === focus.msg) : undefined;
  const lastAssistant = [...messages].reverse().find((m) => m.role === "assistant");
  const panelMessage = focusedMessage ?? lastAssistant;
  const workspace = workspaces.find((w) => w.id === wsId);
  const readyDocs = documents.filter((d) => d.status === "ready").length;

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <img src="/favicon.svg" alt="" width={28} height={28} />
          <span>DocMind</span>
          <button className="icon-btn" title="API key settings" onClick={() => setShowSettings(true)}>
            ⚙
          </button>
        </div>

        <section>
          <div className="section-head">
            <h2>Workspace</h2>
            <button className="link" onClick={createWorkspace}>
              + New
            </button>
          </div>
          {workspaces.length ? (
            <div className="ws-row">
              <select value={wsId ?? ""} onChange={(e) => setWsId(Number(e.target.value))}>
                {workspaces.map((w) => (
                  <option key={w.id} value={w.id}>
                    {w.name}
                  </option>
                ))}
              </select>
              <button className="icon-btn danger" title="Delete workspace" onClick={deleteWorkspace}>
                🗑
              </button>
            </div>
          ) : (
            <p className="muted">Create a workspace to get started.</p>
          )}
        </section>

        {wsId && (
          <section>
            <div className="section-head">
              <h2>Documents</h2>
              <span className="count">{documents.length}</span>
            </div>
            <div
              className={`dropzone ${dragOver ? "over" : ""}`}
              onClick={() => fileInput.current?.click()}
              onDragOver={(e) => {
                e.preventDefault();
                setDragOver(true);
              }}
              onDragLeave={() => setDragOver(false)}
              onDrop={(e) => {
                e.preventDefault();
                setDragOver(false);
                upload(e.dataTransfer.files);
              }}
            >
              {uploading ? (
                <span>
                  <span className="spinner" /> Indexing {uploading}…
                </span>
              ) : (
                <span>
                  Drop PDF, DOCX or TXT here
                  <br />
                  <small>or click to browse</small>
                </span>
              )}
              <input
                ref={fileInput}
                type="file"
                hidden
                multiple
                accept=".pdf,.docx,.txt,.md"
                onChange={(e) => {
                  if (e.target.files) upload(e.target.files);
                  e.target.value = "";
                }}
              />
            </div>
            <ul className="doc-list">
              {documents.map((d) => (
                <li key={d.id} className={d.status}>
                  <span className="doc-icon">{d.filename.split(".").pop()?.toUpperCase()}</span>
                  <div className="doc-meta">
                    <span className="doc-name" title={d.filename}>
                      {d.filename}
                    </span>
                    <small>
                      {d.status === "failed"
                        ? d.error
                        : `${d.page_count} page${d.page_count === 1 ? "" : "s"} · ${d.chunk_count} chunks · ${fmtSize(d.size_bytes)}`}
                    </small>
                  </div>
                  <button className="icon-btn" title="Remove" onClick={() => removeDocument(d)}>
                    ×
                  </button>
                </li>
              ))}
            </ul>
          </section>
        )}

        {wsId && (
          <section className="grow">
            <div className="section-head">
              <h2>Conversations</h2>
              <button className="link" onClick={newChat}>
                + New chat
              </button>
            </div>
            <ul className="conv-list">
              {conversations.map((c) => (
                <li
                  key={c.id}
                  className={c.id === conversationId ? "active" : ""}
                  onClick={() => openConversation(c.id)}
                >
                  {c.title}
                </li>
              ))}
              {!conversations.length && <li className="muted static">No conversations yet</li>}
            </ul>
          </section>
        )}

        {health && (
          <footer className="provider">
            <span className="dot" /> {health.llm_provider} · {health.embedding_provider}
          </footer>
        )}
      </aside>

      <main className="chat">
        <header className="chat-head">
          <div>
            <h1>{workspace?.name ?? "DocMind"}</h1>
            <p className="muted">
              {workspace
                ? `${readyDocs} document${readyDocs === 1 ? "" : "s"} indexed · answers cite their sources`
                : "Chat with your documents"}
            </p>
          </div>
        </header>

        {error && (
          <div className="error" role="alert">
            {error}
            <button className="icon-btn" onClick={() => setError(null)}>
              ×
            </button>
          </div>
        )}

        <div className="messages">
          {!messages.length && (
            <div className="empty">
              <h2>Ask anything about your documents</h2>
              <p className="muted">
                Upload files on the left, then ask a question. Every answer links back to the exact
                passage and page it came from.
              </p>
            </div>
          )}
          {messages.map((m) => (
            <div key={m.key} className={`msg ${m.role}`}>
              {m.role === "user" ? (
                <div className="bubble">{m.content}</div>
              ) : (
                <div className="bubble">
                  {m.content ? (
                    <Answer
                      text={m.content}
                      activeCite={focus?.msg === m.key ? focus.n : null}
                      onCite={(n) => setFocus({ msg: m.key, n })}
                    />
                  ) : (
                    <span className="typing">
                      <i />
                      <i />
                      <i />
                    </span>
                  )}
                  {m.streaming && m.content && <span className="caret" />}
                  {!!m.sources.length && (
                    <div className="source-chips">
                      {m.sources.map((s) => (
                        <button
                          key={s.index}
                          className={`chip ${focus?.msg === m.key && focus.n === s.index ? "active" : ""}`}
                          onClick={() => setFocus({ msg: m.key, n: s.index })}
                        >
                          <b>{s.index}</b> {s.filename}
                          {s.page ? ` · p.${s.page}` : ""}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          ))}
          <div ref={bottom} />
        </div>

        <form className="composer" onSubmit={ask}>
          <textarea
            value={question}
            placeholder={wsId ? "Ask a question about your documents…" : "Create a workspace first"}
            disabled={!wsId}
            rows={1}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) ask(e);
            }}
          />
          <button type="submit" disabled={!wsId || busy || !question.trim()}>
            {busy ? "…" : "Ask"}
          </button>
        </form>
      </main>

      <aside className="sources">
        <h2>Sources</h2>
        {panelMessage?.sources.length ? (
          panelMessage.sources.map((s) => (
            <article
              key={s.index}
              className={`source ${focus?.msg === panelMessage.key && focus.n === s.index ? "active" : ""}`}
              onClick={() => setFocus({ msg: panelMessage.key, n: s.index })}
            >
              <header>
                <span className="badge">{s.index}</span>
                <span className="src-name">{s.filename}</span>
                {s.page && <span className="page">p.{s.page}</span>}
              </header>
              <p>{s.text}</p>
              <div className="score">
                <div style={{ width: `${Math.max(4, Math.min(100, s.score * 100))}%` }} />
                <span>relevance {s.score.toFixed(2)}</span>
              </div>
            </article>
          ))
        ) : (
          <p className="muted">The passages used to answer will appear here.</p>
        )}
      </aside>

      {showSettings && (
        <div className="modal-backdrop" onClick={() => setShowSettings(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h2>API key</h2>
            <p className="muted">
              Sent as the <code>X-API-Key</code> header. Must match one of the server&apos;s{" "}
              <code>API_KEYS</code>. Stored only in this browser.
            </p>
            <input
              type="password"
              value={keyDraft}
              autoFocus
              onChange={(e) => setKeyDraft(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && saveKey()}
              placeholder="your-api-key"
            />
            <div className="modal-actions">
              <button className="secondary" onClick={() => setShowSettings(false)}>
                Cancel
              </button>
              <button onClick={saveKey}>Save</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
