import { Link } from "react-router-dom";
import { Network } from "lucide-react";

export default function Home() {
  return (
    <div className="flex-1 flex flex-col items-center justify-center text-center px-4">
      <Network className="w-16 h-16 text-emerald-500 mb-6" />
      <h1 className="text-5xl font-extrabold tracking-tight mb-4 text-white">
        CodeGraph <span className="text-emerald-500">AI</span>
      </h1>
      <p className="text-xl text-zinc-400 max-w-2xl mb-8">
        The intelligent, interactive architecture explorer. Ingest codebases,
        chat with your architecture, and modify files in real-time.
      </p>
      <Link
        to="/signup"
        className="bg-emerald-500 text-zinc-950 font-bold text-lg px-8 py-3 rounded-lg hover:bg-emerald-400 transition-colors shadow-[0_0_20px_rgba(16,185,129,0.2)]"
      >
        Initialize Workspace
      </Link>
    </div>
  );
}
