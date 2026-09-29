import {
  createContext,
  useState,
  useEffect,
  useContext,
  useCallback,
} from "react";
import axios from "axios";

const AuthContext = createContext();
axios.defaults.withCredentials = true;

export const AuthProvider = ({ children }) => {
  const [authenticated, setAuthenticated] = useState(false);
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  const fetchUser = useCallback(async () => {
    try {
      const response = await axios.get(`${import.meta.env.VITE_API_URL}/auth/me`);
      setUser(response.data);
      setAuthenticated(true);
      return response.data;
    } catch {
      setUser(null);
      setAuthenticated(false);
      return null;
    } finally {
      setLoading(false);
    }
  }, []);

  const login = useCallback(async () => {
    setLoading(true);
    return fetchUser();
  }, [fetchUser]);

  const logout = useCallback(async () => {
    try {
      await axios.post(`${import.meta.env.VITE_API_URL}/auth/logout`);
    } finally {
      setAuthenticated(false);
      setUser(null);
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    Promise.resolve().then(() => fetchUser());
  }, [fetchUser]);

  return (
    <AuthContext.Provider value={{ token: authenticated, user, login, logout, loading }}>
      {children}
    </AuthContext.Provider>
  );
};

// eslint-disable-next-line react-refresh/only-export-components
export const useAuth = () => useContext(AuthContext);
