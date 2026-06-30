import { useState, useEffect, useRef, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import axios from "axios";
import { useAuth } from "../context/AuthContext";
import {
  FolderGit2,
  Plus,
  GitBranch,
  X,
  Loader2,
  ChevronRight,
  UploadCloud,
  AlertCircle,
  Trash2,
} from "lucide-react";

export default function Dashboard() {
  const { user } = useAuth();
  const navigate = useNavigate();

  const [projects, setProjects] = useState([]);
  const [loading, setLoading] = useState(true);
  const [isModalOpen, setIsModalOpen] = useState(false);

  const [ingestMode, setIngestMode] = useState("github");
  const [projectName, setProjectName] = useState("");
  const [repoUrl, setRepoUrl] = useState("");
  const [selectedFile, setSelectedFile] = useState(null);
  const [error, setError] = useState("");

  // FIX: Use a ref to hold the interval so it's never recreated on projects state change
  const pollIntervalRef = useRef(null);
  const isPollingRef = useRef(false);
  const maxZipBytes = Number(import.meta.env.VITE_MAX_ZIP_BYTES || 1073741824);
  const maxZipLabel = `${Math.round(maxZipBytes / 1024 / 1024 / 1024)} GB`;

  const displayName = user?.email ? user.email.split("@")[0] : "Developer";

  const fetchProjects = useCallback(async () => {
    try {
      const response = await axios.get(
        `${import.meta.env.VITE_API_URL}/projects`,
      );
      const fetched = response.data.projects || [];
      setProjects(fetched);
      return fetched;
    } catch (error) {
      console.error("Failed to fetch projects", error);
      return [];
    } finally {
      setLoading(false);
    }
  }, []);

  const stopPolling = useCallback(() => {
    if (pollIntervalRef.current) {
      clearInterval(pollIntervalRef.current);
      pollIntervalRef.current = null;
    }
    isPollingRef.current = false;
  }, []);

  const startPolling = useCallback(() => {
    // FIX: Guard — never create more than one interval at a time
    if (isPollingRef.current) return;
    isPollingRef.current = true;

    pollIntervalRef.current = setInterval(async () => {
      try {
        const response = await axios.get(
          `${import.meta.env.VITE_API_URL}/projects`,
        );
        const fetched = response.data.projects || [];
        setProjects(fetched);

        // Stop polling automatically once all projects are done
        const stillProcessing = fetched.some(
          (p) => p.status !== "ready" && p.status !== "error",
        );
        if (!stillProcessing) {
          stopPolling();
        }
      } catch (err) {
        console.error("Polling error:", err);
      }
    }, 60000); // Poll every 60 seconds during ingestion — prevents hammering the server while Ollama is running
  }, [stopPolling]);

  // On mount: fetch once, then start polling only if needed
  // FIX: Named async function avoids react-hooks/set-state-in-effect ESLint warning
  useEffect(() => {
    async function initialize() {
      const fetched = await fetchProjects();
      const hasProcessing = fetched.some(
        (p) => p.status !== "ready" && p.status !== "error",
      );
      if (hasProcessing) startPolling();
    }
    initialize();

    // Cleanup on unmount
    return () => stopPolling();
  }, [fetchProjects, startPolling, stopPolling]);

  const handleDelete = async (e, targetProject) => {
    e.stopPropagation();
    if (
      !window.confirm(
        `Are you sure you want to permanently delete "${targetProject}"? This action cannot be undone.`,
      )
    ) {
      return;
    }
    try {
      await axios.delete(
        `${import.meta.env.VITE_API_URL}/projects/${targetProject}`,
      );
      setProjects((prev) => prev.filter((p) => p.name !== targetProject));
    } catch (err) {
      console.error("Failed to delete project", err);
      alert("An error occurred while trying to delete the project.");
    }
  };

  const handleIngest = async (e) => {
    e.preventDefault();
    setError("");

    setIsModalOpen(false);
    const pendingProjectName = projectName;

    // Optimistic UI: Drop the tile immediately with a starting status
    setProjects((prev) => [
      ...prev,
      { name: pendingProjectName, status: "Initializing..." },
    ]);

    try {
      if (ingestMode === "github") {
        await axios.post(`${import.meta.env.VITE_API_URL}/process-git`, {
          repo_url: repoUrl,
          project_name: pendingProjectName,
        });
      } else {
        if (!selectedFile) throw new Error("Please select a valid zip file.");
        if (selectedFile.size > maxZipBytes) {
          throw new Error(`ZIP file exceeds the ${maxZipLabel} upload limit.`);
        }
        const formData = new FormData();
        formData.append("file", selectedFile);
        formData.append("project_name", pendingProjectName);
        await axios.post(
          `${import.meta.env.VITE_API_URL}/process-zip`,
          formData,
          { headers: { "Content-Type": "multipart/form-data" } },
        );
      }

      setRepoUrl("");
      setProjectName("");
      setSelectedFile(null);

      // Start polling to track the new project's progress
      await fetchProjects();
      startPolling();
    } catch (err) {
      setProjects((prev) => prev.filter((p) => p.name !== pendingProjectName));
      setIsModalOpen(true);
      setProjectName(pendingProjectName);

      let errorMessage = "Failed to ingest project. Please try again.";
      if (err.response?.data?.detail) {
        if (Array.isArray(err.response.data.detail)) {
          errorMessage = err.response.data.detail.map((e) => e.msg).join(" | ");
        } else {
          errorMessage = String(err.response.data.detail);
        }
      } else if (err.message) {
        errorMessage = err.message;
      }
      setError(errorMessage);
    }
  };

  return (
    <div className="flex-1 p-8">
      <div className="max-w-7xl mx-auto">
        <div className="flex justify-between items-end mb-8 border-b border-zinc-800 pb-6">
          <div>
            <h1 className="text-3xl font-bold text-zinc-100 tracking-tight">
              Welcome,{" "}
              <span className="text-emerald-400 capitalize">{displayName}</span>
            </h1>
            <p className="text-zinc-400 mt-1">
              Select a workspace or ingest a new codebase to begin analysis.
            </p>
          </div>
          <button
            onClick={() => setIsModalOpen(true)}
            className="flex items-center gap-2 bg-emerald-500 text-zinc-950 font-semibold px-4 py-2 rounded-lg hover:bg-emerald-400 transition-all shadow-[0_0_15px_rgba(16,185,129,0.15)]"
          >
            <Plus className="w-5 h-5" />
            New Project
          </button>
        </div>

        {loading ? (
          <div className="flex flex-col items-center justify-center py-20 text-emerald-500">
            <Loader2 className="w-10 h-10 animate-spin mb-4" />
            <p>Loading your workspaces...</p>
          </div>
        ) : projects.length === 0 ? (
          <div className="border border-dashed border-zinc-800 rounded-xl p-12 text-center bg-zinc-900/30">
            <FolderGit2 className="w-16 h-16 text-zinc-600 mx-auto mb-4" />
            <h3 className="text-xl font-medium text-zinc-300 mb-2">
              No projects found
            </h3>
            <p className="text-zinc-500 mb-6">
              You haven't ingested any codebases yet.
            </p>
            <button
              onClick={() => setIsModalOpen(true)}
              className="text-emerald-400 hover:text-emerald-300 font-medium"
            >
              + Ingest your first repository
            </button>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {projects.map((project, idx) => {
              const isReady = project.status === "ready";
              const isError = project.status === "error";
              const isProcessing = !isReady && !isError;

              return (
                <div
                  key={idx}
                  onClick={() =>
                    isReady ? navigate(`/workspace/${project.name}`) : null
                  }
                  className={`group bg-zinc-900 border rounded-xl p-6 flex flex-col transition-all relative ${
                    isReady
                      ? "cursor-pointer border-zinc-800 hover:border-emerald-500/50 hover:shadow-[0_0_20px_rgba(16,185,129,0.05)]"
                      : isError
                        ? "border-red-500/30 opacity-75"
                        : "border-zinc-800 opacity-75 cursor-not-allowed"
                  }`}
                >
                  {!isProcessing && (
                    <button
                      onClick={(e) => handleDelete(e, project.name)}
                      className="absolute top-4 right-4 p-2 bg-zinc-950/80 text-zinc-500 hover:text-red-400 rounded-md opacity-0 group-hover:opacity-100 transition-all border border-zinc-800 hover:border-red-500/50 z-10"
                      title="Delete Project"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  )}

                  <div className="flex items-start justify-between mb-4">
                    <div
                      className={`p-3 rounded-lg border border-zinc-800 transition-transform ${isReady ? "bg-zinc-950 text-emerald-400 group-hover:scale-110" : "bg-zinc-900 text-zinc-500"}`}
                    >
                      <FolderGit2 className="w-6 h-6" />
                    </div>

                    {isProcessing && (
                      <Loader2 className="w-5 h-5 text-emerald-500 animate-spin mr-8" />
                    )}
                    {isReady && (
                      <ChevronRight className="w-5 h-5 text-zinc-600 group-hover:text-emerald-400 transition-colors mr-8" />
                    )}
                    {isError && (
                      <AlertCircle className="w-5 h-5 text-red-500 mr-8" />
                    )}
                  </div>

                  <h3 className="text-lg font-bold text-zinc-100 truncate pr-8">
                    {project.name}
                  </h3>

                  <p
                    className={`text-sm mt-1 font-medium ${
                      isProcessing
                        ? "text-emerald-500 animate-pulse"
                        : isError
                          ? "text-red-400"
                          : "text-zinc-500"
                    }`}
                  >
                    {isProcessing
                      ? project.status
                      : isError
                        ? "Ingestion Failed"
                        : "Ready for analysis"}
                  </p>
                </div>
              );
            })}
          </div>
        )}

        {isModalOpen && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm px-4">
            <div className="bg-zinc-900 border border-zinc-800 rounded-xl w-full max-w-lg overflow-hidden shadow-2xl">
              <div className="flex justify-between items-center p-6 border-b border-zinc-800 bg-zinc-950/50">
                <h2 className="text-xl font-bold flex items-center gap-2">
                  <FolderGit2 className="w-5 h-5 text-emerald-400" /> Ingest
                  Codebase
                </h2>
                <button
                  onClick={() => setIsModalOpen(false)}
                  className="text-zinc-500 hover:text-zinc-300"
                >
                  <X className="w-5 h-5" />
                </button>
              </div>

              <form onSubmit={handleIngest} className="p-6">
                {error && (
                  <div className="mb-4 p-3 bg-red-500/10 border border-red-500/50 text-red-400 rounded text-sm">
                    {error}
                  </div>
                )}

                <div className="flex bg-zinc-950 p-1 rounded-lg mb-6 border border-zinc-800">
                  <button
                    type="button"
                    onClick={() => setIngestMode("github")}
                    className={`flex-1 flex items-center justify-center gap-2 py-2 text-sm font-medium rounded-md transition-all ${ingestMode === "github" ? "bg-zinc-800 text-zinc-100 shadow" : "text-zinc-500 hover:text-zinc-300"}`}
                  >
                    <GitBranch className="w-4 h-4" /> GitHub URL
                  </button>
                  <button
                    type="button"
                    onClick={() => setIngestMode("upload")}
                    className={`flex-1 flex items-center justify-center gap-2 py-2 text-sm font-medium rounded-md transition-all ${ingestMode === "upload" ? "bg-zinc-800 text-zinc-100 shadow" : "text-zinc-500 hover:text-zinc-300"}`}
                  >
                    <UploadCloud className="w-4 h-4" /> Zip Upload
                  </button>
                </div>

                <div className="space-y-4">
                  <div>
                    <label className="block text-sm font-medium text-zinc-400 mb-1">
                      Project Name
                    </label>
                    <input
                      type="text"
                      required
                      placeholder="e.g., Starlette_Core"
                      className="w-full bg-zinc-950 border border-zinc-800 rounded-lg px-4 py-2.5 focus:outline-none focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500 transition-colors"
                      value={projectName}
                      onChange={(e) => setProjectName(e.target.value)}
                    />
                  </div>

                  {ingestMode === "github" ? (
                    <div>
                      <label className="block text-sm font-medium text-zinc-400 mb-1">
                        GitHub URL
                      </label>
                      <input
                        type="url"
                        required
                        placeholder="https://github.com/encode/starlette.git"
                        className="w-full bg-zinc-950 border border-zinc-800 rounded-lg px-4 py-2.5 focus:outline-none focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500 transition-colors"
                        value={repoUrl}
                        onChange={(e) => setRepoUrl(e.target.value)}
                      />
                    </div>
                  ) : (
                    <div>
                      <label className="block text-sm font-medium text-zinc-400 mb-1">
                        Upload Archive (.zip, max {maxZipLabel})
                      </label>
                      <div className="relative">
                        <input
                          type="file"
                          accept=".zip"
                          required
                          onChange={(e) => setSelectedFile(e.target.files[0])}
                          className="w-full bg-zinc-950 border border-zinc-800 rounded-lg px-4 py-2 file:mr-4 file:py-2 file:px-4 file:rounded-md file:border-0 file:text-sm file:font-semibold file:bg-zinc-800 file:text-zinc-300 hover:file:bg-zinc-700 cursor-pointer text-zinc-400 focus:outline-none focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500 transition-colors"
                        />
                      </div>
                    </div>
                  )}
                </div>

                <div className="mt-8 flex justify-end gap-3">
                  <button
                    type="button"
                    onClick={() => setIsModalOpen(false)}
                    className="px-4 py-2 text-zinc-400 hover:text-zinc-200 font-medium"
                  >
                    Cancel
                  </button>
                  <button
                    type="submit"
                    className="flex items-center gap-2 bg-emerald-500 hover:bg-emerald-400 text-zinc-950 font-semibold px-6 py-2 rounded-lg transition-all disabled:opacity-50"
                  >
                    Start Ingestion
                  </button>
                </div>
              </form>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
