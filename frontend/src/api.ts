// Typed client for the DocMind API.

export interface Workspace {
  id: number;
  name: string;
  description: string;
  created_at: string;
  document_count: number;
}

export interface DocumentInfo {
  id: number;
  workspace_id: number;
  filename: string;
  size_bytes: number;
  page_count: number;
  chunk_count: number;
  status: "processing" | "ready" | "failed";
  error: string | null;
  created_at: string;
}

export interface Source {
  index: number;
  document_id: number;
  filename: string;
  page: number | null;
  chunk_index: number;
  score: number;
  text: string;
}

export interface ChatMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
  sources: Source[];
  created_at: string;
}

export interface Conversation {
  id: number;
  workspace_id: number;
  title: string;
  created_at: string;
}

export interface ConversationDetail extends Conversation {
  messages: ChatMessage[];
}

export interface Health {
  status: string;
  version: string;
  llm_provider: string;
  embedding_provider: string;
  vector_store: string;
  auth_enabled: boolean;
}

const KEY_STORAGE = "docmind.apiKey";

export function getApiKey(): string {
  try {
    return localStorage.getItem(KEY_STORAGE) ?? import.meta.env.VITE_DEFAULT_API_KEY ?? "";
  } catch {
    return import.meta.env.VITE_DEFAULT_API_KEY ?? "";
  }
}

export function setApiKey(key: string): void {
  try {
    localStorage.setItem(KEY_STORAGE, key);
  } catch {
    /* storage unavailable: key lives for this page only */
  }
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("X-API-Key", getApiKey());
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const resp = await fetch(`/api${path}`, { ...init, headers });
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      const body = await resp.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(resp.status, detail);
  }
  return resp.status === 204 ? (undefined as T) : ((await resp.json()) as T);
}

export const api = {
  health: () => request<Health>("/health"),
  listWorkspaces: () => request<Workspace[]>("/workspaces"),
  createWorkspace: (name: string, description = "") =>
    request<Workspace>("/workspaces", {
      method: "POST",
      body: JSON.stringify({ name, description }),
    }),
  deleteWorkspace: (id: number) => request<void>(`/workspaces/${id}`, { method: "DELETE" }),
  listDocuments: (wsId: number) => request<DocumentInfo[]>(`/workspaces/${wsId}/documents`),
  uploadDocument: (wsId: number, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<DocumentInfo>(`/workspaces/${wsId}/documents`, { method: "POST", body: form });
  },
  deleteDocument: (id: number) => request<void>(`/documents/${id}`, { method: "DELETE" }),
  listConversations: (wsId: number) =>
    request<Conversation[]>(`/workspaces/${wsId}/conversations`),
  getConversation: (id: number) => request<ConversationDetail>(`/conversations/${id}`),
  deleteConversation: (id: number) => request<void>(`/conversations/${id}`, { method: "DELETE" }),
};

export interface StreamHandlers {
  onSources: (conversationId: number, sources: Source[]) => void;
  onToken: (text: string) => void;
  onDone: (conversationId: number, messageId: number) => void;
  onError: (message: string) => void;
}

/** POST a question and consume the Server-Sent Events stream. */
export async function askStream(
  wsId: number,
  question: string,
  conversationId: number | null,
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const resp = await fetch(`/api/workspaces/${wsId}/ask/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-API-Key": getApiKey() },
    body: JSON.stringify({ question, conversation_id: conversationId }),
    signal,
  });
  if (!resp.ok || !resp.body) {
    let detail = resp.statusText;
    try {
      detail = (await resp.json()).detail ?? detail;
    } catch {
      /* ignore */
    }
    handlers.onError(`${resp.status}: ${detail}`);
    return;
  }

  const reader = resp.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += value;
    let boundary: number;
    while ((boundary = buffer.indexOf("\n\n")) !== -1) {
      const raw = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      let event = "message";
      let data = "";
      for (const line of raw.split("\n")) {
        if (line.startsWith("event: ")) event = line.slice(7);
        else if (line.startsWith("data: ")) data += line.slice(6);
      }
      const payload = data ? JSON.parse(data) : {};
      if (event === "sources") handlers.onSources(payload.conversation_id, payload.sources);
      else if (event === "token") handlers.onToken(payload.text);
      else if (event === "done") handlers.onDone(payload.conversation_id, payload.message_id);
      else if (event === "error") handlers.onError(payload.detail);
    }
  }
}
