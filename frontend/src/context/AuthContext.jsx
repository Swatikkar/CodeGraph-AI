import {
  createContext,
  useState,
  useEffect,
  useContext,
  useCallback,
} from "react";
import axios from "axios";

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
    const savedToken = localStorage.getItem("token");
    if (savedToken) {
      Promise.resolve().then(() => fetchUser(savedToken));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <AuthContext.Provider value={{ token, user, login, logout, loading }}>
      {children}
    </AuthContext.Provider>
  );
};

// eslint-disable-next-line react-refresh/only-export-components
export const useAuth = () => useContext(AuthContext);
