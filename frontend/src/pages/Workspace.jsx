import { useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import axios from "axios";
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
  X,
} from "lucide-react";
import { formatApiError } from "../utils/apiError";

const getAuthHeaders = () => ({
  headers: { Authorization: `Bearer ${localStorage.getItem("token")}` },
});

const buildLineDiff = (oldText = "", newText = "") => {
  const oldLines = oldText.split("\n");
  const newLines = newText.split("\n");
  const table = Array.from({ length: oldLines.length + 1 }, () =>
    Array(newLines.length + 1).fill(0),
  );

  for (let i = oldLines.length - 1; i >= 0; i -= 1) {
    for (let j = newLines.length - 1; j >= 0; j -= 1) {
      table[i][j] =
        oldLines[i] === newLines[j]
          ? table[i + 1][j + 1] + 1
          : Math.max(table[i + 1][j], table[i][j + 1]);
    }
  }

  const diff = [];
  let oldIndex = 0;
  let newIndex = 0;
  while (oldIndex < oldLines.length && newIndex < newLines.length) {
    if (oldLines[oldIndex] === newLines[newIndex]) {
      diff.push({ type: "context", text: oldLines[oldIndex], oldLine: oldIndex + 1, newLine: newIndex + 1 });
      oldIndex += 1;
      newIndex += 1;
    } else if (table[oldIndex + 1][newIndex] >= table[oldIndex][newIndex + 1]) {
      diff.push({ type: "remove", text: oldLines[oldIndex], oldLine: oldIndex + 1, newLine: "" });
      oldIndex += 1;
    } else {
      diff.push({ type: "add", text: newLines[newIndex], oldLine: "", newLine: newIndex + 1 });
      newIndex += 1;
    }
  }
  while (oldIndex < oldLines.length) {
    diff.push({ type: "remove", text: oldLines[oldIndex], oldLine: oldIndex + 1, newLine: "" });
    oldIndex += 1;
  }
  while (newIndex < newLines.length) {
    diff.push({ type: "add", text: newLines[newIndex], oldLine: "", newLine: newIndex + 1 });
    newIndex += 1;
  }

  return diff;
};

const PatchDiffView = ({ originalContent, newContent }) => {
  if (typeof originalContent !== "string") {
    return (
      <pre className="max-h-[32rem] overflow-auto p-3 text-xs leading-relaxed text-zinc-300">
        <code>{newContent}</code>
      </pre>
    );
  }

  const diff = buildLineDiff(originalContent, newContent);
  const changedCount = diff.filter((line) => line.type !== "context").length;

  if (!changedCount) {
    return (
      <div className="p-3 text-xs text-zinc-500">
        The proposed patch does not change this file.
      </div>
    );
  }

  return (
    <div className="max-h-[32rem] overflow-auto font-mono text-xs leading-relaxed">
      {diff.map((line, index) => {
        const styles = {
          context: "bg-zinc-950 text-zinc-400",
          remove: "border-l-2 border-red-500 bg-red-950/35 text-red-200",
          add: "border-l-2 border-emerald-500 bg-emerald-950/35 text-emerald-200",
        };
        const marker = line.type === "remove" ? "-" : line.type === "add" ? "+" : " ";
        return (
          <div key={`${line.type}-${index}`} className={`grid grid-cols-[3.25rem_3.25rem_1.5rem_1fr] px-3 ${styles[line.type]}`}>
            <span className="select-none text-right text-zinc-600">{line.oldLine}</span>
            <span className="select-none text-right text-zinc-600">{line.newLine}</span>
            <span className="select-none text-center">{marker}</span>
            <span className="whitespace-pre-wrap break-words">{line.text || " "}</span>
          </div>
        );
      })}
    </div>
  );
};

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
  const [isExpanded, setIsExpanded] = useState(false);
  const diagramRef = useRef(null);
  const expandedDiagramRef = useRef(null);
  const mermaidRef = useRef(null);
  const title = mode === "architecture" ? "Architecture" : "Links";

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
    let cancelled = false;
    async function renderDiagram() {
      const module = await import("mermaid");
      if (cancelled || !diagramRef.current) return;
      const renderer = mermaidRef.current || module.default;
      if (!mermaidRef.current) {
        renderer.initialize({ startOnLoad: false, theme: "dark" });
        mermaidRef.current = renderer;
      }
      diagramRef.current.removeAttribute("data-processed");
      diagramRef.current.innerHTML = code;
      await renderer.run({ nodes: [diagramRef.current] });
    }
    renderDiagram().catch(() => {});
    return () => { cancelled = true; };
  }, [code]);

  useEffect(() => {
    if (!code || !isExpanded || !expandedDiagramRef.current) return;
    let cancelled = false;
    async function renderExpandedDiagram() {
      const module = await import("mermaid");
      if (cancelled || !expandedDiagramRef.current) return;
      const renderer = mermaidRef.current || module.default;
      if (!mermaidRef.current) {
        renderer.initialize({ startOnLoad: false, theme: "dark" });
        mermaidRef.current = renderer;
      }
      expandedDiagramRef.current.removeAttribute("data-processed");
      expandedDiagramRef.current.innerHTML = code;
      await renderer.run({ nodes: [expandedDiagramRef.current] });
    }
    renderExpandedDiagram().catch(() => {});
    return () => { cancelled = true; };
  }, [code, isExpanded]);

  useEffect(() => {
    if (!isExpanded) return undefined;
    const handleKeyDown = (event) => {
      if (event.key === "Escape") setIsExpanded(false);
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isExpanded]);

  return (
    <div className="h-full overflow-auto p-4">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-sm font-semibold text-zinc-300">
          {mode === "architecture" ? <Network className="h-4 w-4 text-emerald-400" /> : <GitMerge className="h-4 w-4 text-emerald-400" />}
          {title}
        </div>
        <span className="text-xs text-zinc-600">Double-click to enlarge</span>
      </div>
      <div
        className="min-h-[28rem] cursor-zoom-in overflow-auto rounded border border-zinc-800 bg-zinc-950 p-4"
        onDoubleClick={() => {
          if (!loading && code) setIsExpanded(true);
        }}
        title="Double-click to enlarge"
      >
        {loading ? (
          <div className="flex min-h-[20rem] items-center justify-center text-sm text-zinc-500">
            <Loader2 className="mr-2 h-4 w-4 animate-spin" /> Loading diagram
          </div>
        ) : (
          <pre ref={diagramRef} className="mermaid min-w-[760px] text-sm">{code}</pre>
        )}
      </div>
      {isExpanded && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 p-6 backdrop-blur-sm"
          onMouseDown={() => setIsExpanded(false)}
          role="dialog"
          aria-modal="true"
          aria-label={`${title} diagram preview`}
        >
          <div
            className="flex h-[88vh] w-[92vw] max-w-7xl flex-col overflow-hidden rounded-lg border border-zinc-800 bg-zinc-950 shadow-2xl"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <div className="flex h-12 shrink-0 items-center justify-between border-b border-zinc-800 px-4">
              <div className="flex items-center gap-2 text-sm font-semibold text-zinc-200">
                {mode === "architecture" ? <Network className="h-4 w-4 text-emerald-400" /> : <GitMerge className="h-4 w-4 text-emerald-400" />}
                {title}
              </div>
              <button
                type="button"
                onClick={() => setIsExpanded(false)}
                className="flex h-8 w-8 items-center justify-center rounded border border-zinc-800 text-zinc-400 hover:border-zinc-600 hover:text-zinc-100"
                aria-label="Close diagram preview"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            <div className="flex-1 overflow-auto p-6">
              <pre ref={expandedDiagramRef} className="mermaid min-w-[1100px] text-base">{code}</pre>
            </div>
          </div>
        </div>
      )}
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
  const imagePreviewUrl = useMemo(
    () => (imageFile ? URL.createObjectURL(imageFile) : ""),
    [imageFile],
  );
  const [isChatting, setIsChatting] = useState(false);
  const [patchPreview, setPatchPreview] = useState(null);
  const [patchStatus, setPatchStatus] = useState("");
  const [isApprovingPatch, setIsApprovingPatch] = useState(false);
  const [loadingFile, setLoadingFile] = useState(false);
  const chatEndRef = useRef(null);
  const sseBuffer = useRef("");
  const activeStreamController = useRef(null);

  useEffect(() => {
    const controller = new AbortController();
    async function init() {
      const [treeResponse, historyResponse] = await Promise.allSettled([
        axios.get(`${import.meta.env.VITE_API_URL}/project-structure/${projectName}`, { ...getAuthHeaders(), signal: controller.signal }),
        axios.get(`${import.meta.env.VITE_API_URL}/chat-history/${projectName}`, { ...getAuthHeaders(), signal: controller.signal }),
      ]);
      if (treeResponse.status === "fulfilled") setTreeData(treeResponse.value.data.tree || []);
      if (historyResponse.status === "fulfilled" && historyResponse.value.data.messages?.length) {
        setChatMessages(historyResponse.value.data.messages);
      } else {
        setChatMessages([{ role: "bot", content: `Ready to analyze ${projectName}. Ask me about architecture, bugs, files, or paste an error screenshot.` }]);
      }
    }
    init();
    return () => {
      controller.abort();
      activeStreamController.current?.abort();
    };
  }, [projectName]);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [chatMessages]);

  useEffect(() => {
    return () => {
      if (imagePreviewUrl) URL.revokeObjectURL(imagePreviewUrl);
    };
  }, [imagePreviewUrl]);

  const handleImageSelect = (file) => {
    if (!file || !file.type.startsWith("image/")) return;
    setImageFile(file);
  };

  const handleInputPaste = (event) => {
    const imageItem = Array.from(event.clipboardData?.items || []).find((item) =>
      item.type.startsWith("image/"),
    );
    const pastedFile = imageItem?.getAsFile();
    if (!pastedFile) return;
    event.preventDefault();
    const extension = pastedFile.type.split("/")[1] || "png";
    handleImageSelect(new File([pastedFile], `pasted-screenshot.${extension}`, { type: pastedFile.type }));
  };

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

  const fetchProjectFileContent = async (filePath) => {
    if (activeFile.path === filePath && activeFile.content) return activeFile.content;
    const response = await axios.get(`${import.meta.env.VITE_API_URL}/file-content`, {
      params: { path: filePath, project_name: projectName },
      ...getAuthHeaders(),
    });
    return response.data.content;
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
    const userImagePreviewUrl = imagePreviewUrl;
    setChatInput("");
    setIsChatting(true);
    setReferences([]);
    setPatchStatus("");
    sseBuffer.current = "";
    activeStreamController.current?.abort();
    const streamController = new AbortController();
    activeStreamController.current = streamController;

    const botIndex = chatMessages.length + 1;
    setChatMessages((prev) => [
      ...prev,
      { role: "user", content: userMessage, imageUrl: userImagePreviewUrl },
      { role: "bot", content: "", responseTime: null },
    ]);

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
        signal: streamController.signal,
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
          } else if (data.type === "patch_preview") {
            let originalContent = null;
            try {
              originalContent = await fetchProjectFileContent(data.file_path);
            } catch (error) {
              console.warn("Could not fetch original file for diff preview", error);
            }
            setPatchPreview({
              proposalId: data.proposal_id,
              filePath: data.file_path,
              newContent: data.new_content,
              originalContent,
              summary: data.summary,
            });
            setPatchStatus("Patch proposal is ready for review.");
            setRightTab("patch");
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
      if (error.name !== "AbortError") {
        updateBotMessage(botIndex, `Connection error: ${error.message}`);
      }
    } finally {
      if (activeStreamController.current === streamController) {
        activeStreamController.current = null;
      }
      setIsChatting(false);
    }
  };

  const handleApprovePatch = async () => {
    if (!patchPreview || isApprovingPatch) return;
    setIsApprovingPatch(true);
    setPatchStatus("Applying approved patch...");
    try {
      const response = await fetch(`${import.meta.env.VITE_API_URL}/chat`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${localStorage.getItem("token")}`,
        },
        body: JSON.stringify({
          project_name: projectName,
          query: "Approve proposed patch.",
          thread_id: projectName,
          is_approval: true,
          approval_proposal_id: patchPreview.proposalId,
        }),
      });
      if (!response.ok) throw new Error(`HTTP error: ${response.status}`);
      const reader = response.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let buffer = "";
      let statusMessage = "Patch approved and applied.";
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split("\n\n");
        buffer = parts.pop() ?? "";
        for (const part of parts) {
          const line = part.trim();
          if (!line.startsWith("data: ")) continue;
          const data = JSON.parse(line.slice(6));
          if (data.type === "tool_result" && data.content) statusMessage = data.content;
          if (data.type === "error" && data.content) statusMessage = data.content;
        }
      }
      setPatchStatus(statusMessage);
      if (patchPreview.filePath === activeFile.path) {
        await handleFileSelect(activeFile.path, activeFile.name);
      }
    } catch (error) {
      setPatchStatus(`Patch approval failed: ${error.message}`);
    } finally {
      setIsApprovingPatch(false);
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
                  {message.imageUrl && (
                    <img
                      src={message.imageUrl}
                      alt="Attached screenshot"
                      className="mb-3 max-h-64 rounded border border-zinc-700 object-contain"
                    />
                  )}
                  <div className="whitespace-pre-wrap">{message.content || (isChatting ? "Thinking..." : "")}</div>
                </div>
              </div>
            ))}
            <div ref={chatEndRef} />
          </div>
        </div>

        <form onSubmit={handleSendMessage} className="border-t border-zinc-800 p-4">
          <div className="mx-auto max-w-4xl">
            {imageFile && imagePreviewUrl && (
              <div className="mb-3 flex items-center gap-3 rounded-lg border border-zinc-800 bg-zinc-900 p-2">
                <img
                  src={imagePreviewUrl}
                  alt="Selected screenshot preview"
                  className="h-16 w-24 rounded border border-zinc-700 object-cover"
                />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm text-zinc-200">{imageFile.name}</p>
                  <p className="text-xs text-zinc-500">Screenshot attached</p>
                </div>
                <button
                  type="button"
                  onClick={() => setImageFile(null)}
                  className="flex h-8 w-8 items-center justify-center rounded border border-zinc-800 text-zinc-400 hover:border-red-500/40 hover:text-red-300"
                  aria-label="Remove attached screenshot"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>
            )}
            <div className="flex items-center gap-2">
            <label className="flex h-11 w-11 cursor-pointer items-center justify-center rounded-lg border border-zinc-800 bg-zinc-900 text-zinc-400 hover:text-emerald-300">
              <ImagePlus className="h-5 w-5" />
              <input
                type="file"
                accept="image/png,image/jpeg,image/webp"
                className="hidden"
                onChange={(event) => handleImageSelect(event.target.files?.[0])}
              />
            </label>
            <input
              value={chatInput}
              onChange={(event) => setChatInput(event.target.value)}
              onPaste={handleInputPaste}
              disabled={isChatting}
              placeholder={imageFile ? "Ask a question about the attached screenshot..." : "Ask about architecture, bugs, files, or paste an error screenshot..."}
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
            <div className="space-y-4 p-4 text-sm">
              {patchPreview ? (
                <>
                  <div>
                    <h3 className="text-sm font-semibold text-zinc-200">Patch review</h3>
                    <p className="mt-1 text-xs text-zinc-500">{patchPreview.summary}</p>
                  </div>
                  <div className="rounded border border-zinc-800 bg-zinc-950">
                    <div className="border-b border-zinc-800 px-3 py-2 text-xs font-medium text-emerald-300">
                      {patchPreview.filePath}
                    </div>
                    <PatchDiffView
                      originalContent={patchPreview.originalContent}
                      newContent={patchPreview.newContent}
                    />
                  </div>
                  {patchStatus && (
                    <div className="rounded border border-zinc-800 bg-zinc-900 px-3 py-2 text-xs text-zinc-300">
                      {patchStatus}
                    </div>
                  )}
                  <button
                    type="button"
                    onClick={handleApprovePatch}
                    disabled={isApprovingPatch || !patchPreview.newContent}
                    className="w-full rounded bg-emerald-500 px-3 py-2 text-sm font-semibold text-zinc-950 hover:bg-emerald-400 disabled:opacity-50"
                  >
                    {isApprovingPatch ? "Applying patch..." : "Approve patch"}
                  </button>
                </>
              ) : (
                <div className="flex h-full items-center justify-center p-6 text-center text-sm text-zinc-600">
                  Patch previews and approval controls will appear here when the debugger proposes a change.
                </div>
              )}
            </div>
          )}
        </div>
      </aside>
    </div>
  );
}
