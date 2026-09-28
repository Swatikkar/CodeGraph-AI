import {
  createContext,
  useState,
  useEffect,
  useContext,
  useCallback,
} from "react";
import axios from "axios";
import { isSupabaseAuth, supabase } from "../lib/supabase";

const AuthContext = createContext();

export const AuthProvider = ({ children }) => {
  // 1. Initialize token safely
  const [token, setToken] = useState(() => {
    const savedToken = localStorage.getItem("token");
    if (savedToken) {
      axios.defaults.headers.common["Authorization"] = `Bearer ${savedToken}`;
    }
    return savedToken || null;
  });

  const [user, setUser] = useState(null);

  // 2. SMARTER LOADING STATE: If there's no token, loading is instantly false.
  // This completely removes the need to call setLoading(false) in our effect!
  const [loading, setLoading] = useState(() => !!localStorage.getItem("token"));

  // 3. Declare logout first
  const logout = useCallback(() => {
    if (isSupabaseAuth && supabase) {
      void supabase.auth.signOut();
    }
    localStorage.removeItem("token");
    delete axios.defaults.headers.common["Authorization"];
    setToken(null);
    setUser(null);
    setLoading(false);
  }, []);

  // 4. Declare fetchUser BEFORE login so it exists in memory
  // const fetchUser = useCallback(
  //   async (activeToken) => {
  //     try {
  //       axios.defaults.headers.common["Authorization"] =
  //         `Bearer ${activeToken}`;
  //       const response = await axios.get("http://localhost:8000/api/auth/me");
  //       setUser(response.data);
  //     } catch (error) {
  //       console.error("Token invalid or expired", error);
  //       logout();
  //     } finally {
  //       setLoading(false);
  //     }
  //   },
  //   [logout],
  // );
  const fetchUser = useCallback(
    async (activeToken) => {
      try {
        axios.defaults.headers.common["Authorization"] =
          `Bearer ${activeToken}`;
        // Use the environment variable here:
        const response = await axios.get(
          `${import.meta.env.VITE_API_URL}/auth/me`,
        );
        setUser(response.data);
      } catch (error) {
        console.error("Token invalid or expired", error);
        logout();
      } finally {
        setLoading(false);
      }
    },
    [logout],
  );

  // 5. Declare login last
  const login = useCallback(
    (newToken) => {
      localStorage.setItem("token", newToken);
      axios.defaults.headers.common["Authorization"] = `Bearer ${newToken}`;
      setToken(newToken);
      fetchUser(newToken);
    },
    [fetchUser],
  );

  useEffect(() => {
    if (isSupabaseAuth && supabase) {
      let active = true;
      const restoreSession = async () => {
        const { data } = await supabase.auth.getSession();
        if (active && data.session?.access_token) {
          await fetchUser(data.session.access_token);
        } else if (active) {
          setLoading(false);
        }
      };
      void restoreSession();
      const { data: listener } = supabase.auth.onAuthStateChange((event, session) => {
        if (!active) return;
        if (session?.access_token) {
          void fetchUser(session.access_token);
        } else if (event === "SIGNED_OUT") {
          localStorage.removeItem("token");
          delete axios.defaults.headers.common["Authorization"];
          setToken(null);
          setUser(null);
          setLoading(false);
        }
      });
      return () => {
        active = false;
        listener.subscription.unsubscribe();
      };
    }

    const savedToken = localStorage.getItem("token");
    if (savedToken) {
      Promise.resolve().then(() => fetchUser(savedToken));
    }
  }, [fetchUser, logout]);

  return (
    <AuthContext.Provider value={{ token, user, login, logout, loading }}>
      {children}
    </AuthContext.Provider>
  );
};

// eslint-disable-next-line react-refresh/only-export-components
export const useAuth = () => useContext(AuthContext);
