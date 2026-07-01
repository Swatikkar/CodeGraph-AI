import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import axios from "axios";
import mermaid from "mermaid";
import {
  Bot,
  ChevronRight,
  FileCode,
  FileText,
  FolderTree,
  GitMerge,
  ImagePlus,
  Loader2,
  Network,
  Send,
  Sparkles,
} from "lucide-react";
import { formatApiError } from "../utils/apiError";

const getAuthHeaders = () => ({
  headers: { Authorization: `Bearer ${localStorage.getItem("token")}` },
});

const FileTreeItem = ({ node, level = 0, onFileSelect, activeFilePath }) => {
  const [isOpen, setIsOpen] = useState(level < 1);
  const isDir = node.type === "directory";
  const isActive = activeFilePath === node.path;

  return (
    <div>
      <button
        type="button"
        className={`flex w-full items-center py-1.5 pr-3 text-left text-sm transition-colors ${
          isActive
            ? "border-r-2 border-emerald-500 bg-zinc-800/80 text-emerald-300"
            : "text-zinc-400 hover:bg-zinc-800/50 hover:text-zinc-200"
        }`}
        style={{ paddingLeft: `${level * 14 + 10}px` }}
        onClick={() => (isDir ? setIsOpen((value) => !value) : onFileSelect(node.path, node.name))}
      >
        {isDir ? (
          <ChevronRight className={`mr-1.5 h-4 w-4 transition-transform ${isOpen ? "rotate-90" : ""}`} />
        ) : (
          <FileText className="ml-5 mr-1.5 h-4 w-4 text-zinc-500" />
        )}
        <span className="truncate">{node.name}</span>
      </button>
      {isDir && isOpen && node.children?.map((child, idx) => (
        <FileTreeItem
          key={`${child.path}-${idx}`}
          node={child}
          level={level + 1}
          onFileSelect={onFileSelect}
          activeFilePath={activeFilePath}
        />
      ))}
    </div>
  );
};

const DiagramView = ({ projectName, mode }) => {
  const [code, setCode] = useState("");
  const [loading, setLoading] = useState(false);
  const diagramRef = useRef(null);

  useEffect(() => {
    mermaid.initialize({ startOnLoad: false, theme: "dark" });
  }, []);

  useEffect(() => {
    async function fetchDiagram() {
      setLoading(true);
      const endpoint = mode === "architecture" ? "architecture" : "dependency-graph";
      try {
        const response = await axios.get(`${import.meta.env.VITE_API_URL}/${endpoint}/${projectName}`, getAuthHeaders());
        const cleanCode = (response.data.mermaid_code || "")
          .replace(/-->\|([^|]+)\|>/g, "-->|$1|")
          .replace(/```mermaid/gi, "")
          .replace(/```/g, "")
          .trim();
        setCode(cleanCode);
      } catch {
        setCode("graph TD\n    Waiting[\"Diagram not generated yet\"]");
      } finally {
        setLoading(false);
      }
    }
    fetchDiagram();
  }, [mode, projectName]);

  useEffect(() => {
    if (!code || !diagramRef.current) return;
    diagramRef.current.removeAttribute("data-processed");
    diagramRef.current.innerHTML = code;
    mermaid.run({ nodes: [diagramRef.current] }).catch(() => {});
  }, [code]);

  return (
    <div className="h-full overflow-auto p-4">
      <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-zinc-300">
        {mode === "architecture" ? <Network className="h-4 w-4 text-emerald-400" /> : <GitMerge className="h-4 w-4 text-emerald-400" />}
        {mode === "architecture" ? "Architecture" : "Links"}
      </div>
      <div className="min-h-[28rem] overflow-auto rounded border border-zinc-800 bg-zinc-950 p-4">
        {loading ? (
          <div className="flex min-h-[20rem] items-center justify-center text-sm text-zinc-500">
            <Loader2 className="mr-2 h-4 w-4 animate-spin" /> Loading diagram
          </div>
        ) : (
          <pre ref={diagramRef} className="mermaid min-w-[760px] text-sm">{code}</pre>
        )}
      </div>
    </div>
  );
};

export default function Workspace() {
  const { projectName } = useParams();
  const [treeData, setTreeData] = useState([]);
  const [activeFile, setActiveFile] = useState({ path: "", name: "", content: "" });
  const [rightTab, setRightTab] = useState("file");
  const [references, setReferences] = useState([]);
  const [chatMessages, setChatMessages] = useState([]);
  const [chatInput, setChatInput] = useState("");
  const [imageFile, setImageFile] = useState(null);
  const [isChatting, setIsChatting] = useState(false);
  const [loadingFile, setLoadingFile] = useState(false);
  const chatEndRef = useRef(null);
  const sseBuffer = useRef("");

  useEffect(() => {
    async function init() {
      const [treeResponse, historyResponse] = await Promise.allSettled([
        axios.get(`${import.meta.env.VITE_API_URL}/project-structure/${projectName}`, getAuthHeaders()),
        axios.get(`${import.meta.env.VITE_API_URL}/chat-history/${projectName}`, getAuthHeaders()),
      ]);
      if (treeResponse.status === "fulfilled") setTreeData(treeResponse.value.data.tree || []);
      if (historyResponse.status === "fulfilled" && historyResponse.value.data.messages?.length) {
        setChatMessages(historyResponse.value.data.messages);
      } else {
        setChatMessages([{ role: "bot", content: `Ready to analyze ${projectName}. Ask me about architecture, bugs, files, or paste an error screenshot.` }]);
      }
    }
    init();
  }, [projectName]);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [chatMessages]);

  const handleFileSelect = async (filePath, fileName) => {
    setActiveFile({ path: filePath, name: fileName, content: "" });
    setRightTab("file");
    setLoadingFile(true);
    try {
      const response = await axios.get(`${import.meta.env.VITE_API_URL}/file-content`, {
        params: { path: filePath, project_name: projectName },
        ...getAuthHeaders(),
      });
      setActiveFile({ path: filePath, name: fileName, content: response.data.content });
    } catch (error) {
      setActiveFile({ path: filePath, name: fileName, content: formatApiError(error, "Could not load file.") });
    } finally {
      setLoadingFile(false);
    }
  };

  const updateBotMessage = (botIndex, updates) => {
    setChatMessages((prev) => {
      if (botIndex >= prev.length) return prev;
      const next = [...prev];
      next[botIndex] = {
        ...next[botIndex],
        ...(typeof updates === "string" ? { content: updates } : updates),
      };
      return next;
    });
  };

  const handleVisionUpload = async () => {
    if (!imageFile) return "";
    const formData = new FormData();
    formData.append("project_name", projectName);
    formData.append("prompt", chatInput || "Analyze this error screenshot for this project.");
    formData.append("file", imageFile);
    const response = await axios.post(`${import.meta.env.VITE_API_URL}/vision/analyze`, formData, {
      headers: {
        Authorization: `Bearer ${localStorage.getItem("token")}`,
        "Content-Type": "multipart/form-data",
      },
    });
    return `\n\nVision analysis:\n${response.data.analysis}`;
  };

  const handleSendMessage = async (event) => {
    event.preventDefault();
    if ((!chatInput.trim() && !imageFile) || isChatting) return;

    const userMessage = chatInput.trim() || "Analyze the attached error screenshot.";
    setChatInput("");
    setIsChatting(true);
    setReferences([]);
    sseBuffer.current = "";

    const botIndex = chatMessages.length + 1;
    setChatMessages((prev) => [...prev, { role: "user", content: userMessage }, { role: "bot", content: "", responseTime: null }]);

    try {
      let accumulated = "";
      if (imageFile) {
        const visionText = await handleVisionUpload();
        accumulated += visionText;
        updateBotMessage(botIndex, accumulated);
        setImageFile(null);
      }

      const response = await fetch(`${import.meta.env.VITE_API_URL}/chat`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${localStorage.getItem("token")}`,
        },
        body: JSON.stringify({
          project_name: projectName,
          query: userMessage + (accumulated ? `\n\n${accumulated}` : ""),
          thread_id: projectName,
          is_approval: false,
        }),
      });
      if (!response.ok) throw new Error(`HTTP error: ${response.status}`);

      const reader = response.body.getReader();
      const decoder = new TextDecoder("utf-8");
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        sseBuffer.current += decoder.decode(value, { stream: true });
        const parts = sseBuffer.current.split("\n\n");
        sseBuffer.current = parts.pop() ?? "";
        for (const part of parts) {
          const line = part.trim();
          if (!line.startsWith("data: ")) continue;
          const data = JSON.parse(line.slice(6));
          if (data.type === "chunk") {
            accumulated += data.content;
            updateBotMessage(botIndex, accumulated);
          } else if (data.type === "response_meta") {
            if (data.elapsed_seconds !== undefined) updateBotMessage(botIndex, { responseTime: `${data.elapsed_seconds}s` });
          } else if (data.type === "provider_switch") {
            const match = String(data.model || "").match(/\(([\d.]+s)\)/);
            if (match) updateBotMessage(botIndex, { responseTime: match[1] });
          } else if (data.type === "tool_start" || data.type === "tool_result") {
            continue;
          } else if (data.type === "reference") {
            setReferences((prev) => [...new Set([...prev, data.path])]);
            setRightTab("references");
          } else if (data.type === "approval_required") {
            accumulated += `\n\n${data.content}`;
            updateBotMessage(botIndex, accumulated);
            setRightTab("patch");
          } else if (data.type === "error") {
            accumulated += `\n\nError: ${data.content}`;
            updateBotMessage(botIndex, accumulated);
          }
        }
      }
    } catch (error) {
      updateBotMessage(botIndex, `Connection error: ${error.message}`);
    } finally {
      setIsChatting(false);
    }
  };

  return (
    <div className="flex h-[calc(100vh-65px)] min-h-0 bg-zinc-950 text-zinc-100">
      <aside className="flex w-80 shrink-0 flex-col border-r border-zinc-800 bg-zinc-950">
        <div className="flex h-14 items-center gap-2 border-b border-zinc-800 px-4">
          <FolderTree className="h-5 w-5 text-emerald-400" />
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold">{projectName}</p>
            <p className="text-xs text-zinc-500">Project files</p>
          </div>
        </div>
        <div className="flex-1 overflow-auto py-2">
          {treeData.length ? treeData.map((node, idx) => (
            <FileTreeItem key={`${node.path}-${idx}`} node={node} onFileSelect={handleFileSelect} activeFilePath={activeFile.path} />
          )) : (
            <div className="p-4 text-sm text-zinc-500">No files loaded.</div>
          )}
        </div>
      </aside>

      <main className="flex min-w-0 flex-1 flex-col">
        <div className="flex h-14 shrink-0 items-center justify-between border-b border-zinc-800 px-5">
          <div className="flex items-center gap-2">
            <Bot className="h-5 w-5 text-emerald-400" />
            <span className="text-sm font-semibold text-emerald-300">CodeGraph Agent</span>
          </div>
          <div className="flex items-center gap-2 text-xs text-zinc-500">
            <Sparkles className="h-4 w-4" /> streaming multi-agent workspace
          </div>
        </div>

        <div className="flex-1 overflow-y-auto p-5">
          <div className="mx-auto flex max-w-4xl flex-col gap-4">
            {chatMessages.map((message, idx) => (
              <div key={idx} className={`flex ${message.role === "user" ? "justify-end" : "justify-start"}`}>
                <div className={`max-w-[82%] rounded-lg border p-3 text-sm leading-relaxed ${
                  message.role === "user"
                    ? "border-emerald-500/20 bg-emerald-500/10 text-emerald-50"
                    : "border-zinc-800 bg-zinc-900 text-zinc-300"
                }`}>
                  {message.role === "bot" && message.responseTime && (
                    <div className="mb-2 text-xs font-medium text-zinc-500">
                      Response time {message.responseTime}
                    </div>
                  )}
                  <div className="whitespace-pre-wrap">{message.content || (isChatting ? "Thinking..." : "")}</div>
                </div>
              </div>
            ))}
            <div ref={chatEndRef} />
          </div>
        </div>

        <form onSubmit={handleSendMessage} className="border-t border-zinc-800 p-4">
          <div className="mx-auto flex max-w-4xl items-center gap-2">
            <label className="flex h-11 w-11 cursor-pointer items-center justify-center rounded-lg border border-zinc-800 bg-zinc-900 text-zinc-400 hover:text-emerald-300">
              <ImagePlus className="h-5 w-5" />
              <input
                type="file"
                accept="image/png,image/jpeg,image/webp"
                className="hidden"
                onChange={(event) => setImageFile(event.target.files?.[0] || null)}
              />
            </label>
            <input
              value={chatInput}
              onChange={(event) => setChatInput(event.target.value)}
              disabled={isChatting}
              placeholder={imageFile ? `Attached: ${imageFile.name}` : "Ask about architecture, bugs, files, or paste an error..."}
              className="h-11 min-w-0 flex-1 rounded-lg border border-zinc-800 bg-zinc-900 px-4 text-sm text-zinc-100 outline-none focus:border-emerald-500/60"
            />
            <button
              type="submit"
              disabled={isChatting || (!chatInput.trim() && !imageFile)}
              className="flex h-11 w-11 items-center justify-center rounded-lg bg-emerald-500 text-zinc-950 hover:bg-emerald-400 disabled:opacity-50"
            >
              {isChatting ? <Loader2 className="h-5 w-5 animate-spin" /> : <Send className="h-5 w-5" />}
            </button>
          </div>
        </form>
      </main>

      <aside className="flex w-[34rem] shrink-0 flex-col border-l border-zinc-800 bg-[#0d0d0d]">
        <div className="flex h-14 items-center gap-2 overflow-x-auto border-b border-zinc-800 px-3">
          <button onClick={() => setRightTab("file")} className={`rounded px-2 py-1 text-xs ${rightTab === "file" ? "bg-zinc-800 text-emerald-300" : "text-zinc-500"}`}>Selected File</button>
          <button onClick={() => setRightTab("references")} className={`rounded px-2 py-1 text-xs ${rightTab === "references" ? "bg-zinc-800 text-emerald-300" : "text-zinc-500"}`}>References</button>
          <button onClick={() => setRightTab("architecture")} className={`rounded px-2 py-1 text-xs ${rightTab === "architecture" ? "bg-zinc-800 text-emerald-300" : "text-zinc-500"}`}>
            <Network className="mr-1 inline h-3.5 w-3.5" /> Architecture
          </button>
          <button onClick={() => setRightTab("links")} className={`rounded px-2 py-1 text-xs ${rightTab === "links" ? "bg-zinc-800 text-emerald-300" : "text-zinc-500"}`}>
            <GitMerge className="mr-1 inline h-3.5 w-3.5" /> Links
          </button>
          <button onClick={() => setRightTab("patch")} className={`rounded px-2 py-1 text-xs ${rightTab === "patch" ? "bg-zinc-800 text-emerald-300" : "text-zinc-500"}`}>Patch</button>
        </div>
        <div className="flex-1 overflow-auto">
          {rightTab === "file" && (
            activeFile.path ? (
              loadingFile ? (
                <div className="flex h-full items-center justify-center text-sm text-zinc-500">
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" /> Loading file
                </div>
              ) : (
                <>
                  <div className="border-b border-zinc-800 px-4 py-3 text-sm text-zinc-300">
                    <FileCode className="mr-2 inline h-4 w-4 text-emerald-400" /> {activeFile.path}
                  </div>
                  <pre className="p-4 text-xs leading-relaxed text-zinc-300"><code>{activeFile.content}</code></pre>
                </>
              )
            ) : (
              <div className="flex h-full items-center justify-center p-6 text-center text-sm text-zinc-600">Select a file from the left panel.</div>
            )
          )}
          {rightTab === "references" && (
            <div className="p-4">
              <h3 className="mb-3 text-sm font-semibold text-zinc-300">Referenced files</h3>
              {references.length ? references.map((reference) => (
                <button key={reference} onClick={() => handleFileSelect(reference, reference.split("/").pop())} className="mb-2 block w-full rounded border border-zinc-800 bg-zinc-900 px-3 py-2 text-left text-xs text-zinc-300 hover:border-emerald-500/40">
                  {reference}
                </button>
              )) : <p className="text-sm text-zinc-600">References will appear after retrieval-backed answers.</p>}
            </div>
          )}
          {rightTab === "architecture" && <DiagramView projectName={projectName} mode="architecture" />}
          {rightTab === "links" && <DiagramView projectName={projectName} mode="dependency" />}
          {rightTab === "patch" && (
            <div className="p-4 text-sm text-zinc-500">
              Patch previews and approval controls will appear here when the debugger proposes a change.
            </div>
          )}
        </div>
      </aside>
    </div>
  );
}
