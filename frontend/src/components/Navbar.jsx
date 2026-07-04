import { Link, useNavigate } from "react-router-dom";
import { useEffect, useRef, useState } from "react";
import axios from "axios";
import { useAuth } from "../context/AuthContext";
import { KeyRound, LayoutDashboard, LogOut, Network, UserCircle } from "lucide-react";

export default function Navbar() {
  const { token, logout, user } = useAuth();
  const navigate = useNavigate();
  const menuRef = useRef(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [showPasswordForm, setShowPasswordForm] = useState(false);
  const [passwordForm, setPasswordForm] = useState({ current_password: "", new_password: "" });
  const [passwordStatus, setPasswordStatus] = useState("");

  const handleLogout = () => {
    logout();
    navigate("/login");
  };

  useEffect(() => {
    function handleOutsideClick(event) {
      if (menuRef.current && !menuRef.current.contains(event.target)) {
        setMenuOpen(false);
        setShowPasswordForm(false);
        setPasswordStatus("");
      }
    }
    document.addEventListener("mousedown", handleOutsideClick);
    return () => document.removeEventListener("mousedown", handleOutsideClick);
  }, []);

  const handlePasswordChange = async (event) => {
    event.preventDefault();
    setPasswordStatus("");
    try {
      await axios.post(`${import.meta.env.VITE_API_URL}/auth/change-password`, passwordForm);
      setPasswordForm({ current_password: "", new_password: "" });
      setPasswordStatus("Password changed successfully.");
    } catch (error) {
      setPasswordStatus(error?.response?.data?.detail || "Unable to change password.");
    }
  };

  return (
    <nav className="sticky top-0 z-50 w-full border-b border-zinc-800 bg-zinc-950/80 backdrop-blur-md">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex justify-between items-center h-16">
          {/* Logo Segment */}
          <Link
            to="/"
            className="flex items-center gap-2 text-emerald-400 hover:text-emerald-300 transition-colors"
          >
            <Network className="w-6 h-6" />
            <span className="font-bold text-xl tracking-tight text-zinc-100">
              CodeGraph
            </span>
          </Link>

          {/* Navigation Links */}
          <div className="flex items-center gap-4">
            {token ? (
              <>
                <Link
                  to="/dashboard"
                  className="flex items-center gap-2 text-sm font-medium text-zinc-400 hover:text-zinc-100 transition-colors"
                >
                  <LayoutDashboard className="w-4 h-4" />
                  Dashboard
                </Link>
                <div className="h-4 w-px bg-zinc-800"></div>
                <div className="relative" ref={menuRef}>
                  <button
                    type="button"
                    onClick={() => setMenuOpen((open) => !open)}
                    className="flex h-9 w-9 items-center justify-center rounded-full border border-zinc-800 bg-zinc-900 text-emerald-400 hover:border-emerald-500/60 hover:text-emerald-300 transition-colors"
                    aria-label="Open profile menu"
                  >
                    <UserCircle className="h-5 w-5" />
                  </button>

                  {menuOpen && (
                    <div className="absolute right-0 mt-3 w-80 rounded-lg border border-zinc-800 bg-zinc-950 p-4 shadow-2xl">
                      <div className="mb-3 border-b border-zinc-800 pb-3">
                        <p className="text-xs uppercase tracking-wide text-zinc-500">Signed in as</p>
                        <p className="truncate text-sm font-medium text-zinc-200">{user?.email}</p>
                      </div>

                      {showPasswordForm ? (
                        <form onSubmit={handlePasswordChange} className="space-y-3">
                          <input
                            type="password"
                            value={passwordForm.current_password}
                            onChange={(event) =>
                              setPasswordForm((form) => ({ ...form, current_password: event.target.value }))
                            }
                            minLength={8}
                            maxLength={128}
                            placeholder="Current password"
                            className="w-full rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 outline-none focus:border-emerald-500"
                            required
                          />
                          <input
                            type="password"
                            value={passwordForm.new_password}
                            onChange={(event) =>
                              setPasswordForm((form) => ({ ...form, new_password: event.target.value }))
                            }
                            minLength={8}
                            maxLength={128}
                            placeholder="New password"
                            className="w-full rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 outline-none focus:border-emerald-500"
                            required
                          />
                          {passwordStatus && (
                            <p className="text-xs text-zinc-400">{passwordStatus}</p>
                          )}
                          <div className="flex justify-end gap-2">
                            <button
                              type="button"
                              onClick={() => {
                                setShowPasswordForm(false);
                                setPasswordStatus("");
                              }}
                              className="rounded-md px-3 py-2 text-sm text-zinc-400 hover:text-zinc-100"
                            >
                              Cancel
                            </button>
                            <button
                              type="submit"
                              className="rounded-md bg-emerald-500 px-3 py-2 text-sm font-semibold text-zinc-950 hover:bg-emerald-400"
                            >
                              Save
                            </button>
                          </div>
                        </form>
                      ) : (
                        <div className="space-y-1">
                          <button
                            type="button"
                            onClick={() => setShowPasswordForm(true)}
                            className="flex w-full items-center gap-2 rounded-md px-2 py-2 text-left text-sm text-zinc-300 hover:bg-zinc-900"
                          >
                            <KeyRound className="h-4 w-4 text-zinc-500" />
                            Change password
                          </button>
                          <button
                            onClick={handleLogout}
                            className="flex w-full items-center gap-2 rounded-md px-2 py-2 text-left text-sm text-red-400 hover:bg-red-500/10"
                          >
                            <LogOut className="h-4 w-4" />
                            Logout
                          </button>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              </>
            ) : (
              <>
                <Link
                  to="/login"
                  className="text-sm font-medium text-zinc-400 hover:text-zinc-100 transition-colors"
                >
                  Log In
                </Link>
                <Link
                  to="/signup"
                  className="text-sm font-medium bg-emerald-500 text-zinc-950 px-4 py-2 rounded-md hover:bg-emerald-400 transition-colors shadow-[0_0_15px_rgba(16,185,129,0.2)]"
                >
                  Sign Up
                </Link>
              </>
            )}
          </div>
        </div>
      </div>
    </nav>
  );
}
