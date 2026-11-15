import { Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider } from "./AuthContext";
import Layout from "./components/Layout";
import ActivityPage from "./pages/ActivityPage";
import LibraryPage from "./pages/LibraryPage";
import LoginPage from "./pages/LoginPage";
import SearchPage from "./pages/SearchPage";
import SettingsPage from "./pages/SettingsPage";

export default function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route element={<Layout />}>
          <Route path="/" element={<SearchPage />} />
          <Route path="/library" element={<LibraryPage />} />
          <Route path="/activity" element={<ActivityPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="/search" element={<Navigate to="/" replace />} />
          <Route path="/add" element={<Navigate to="/" replace />} />
          <Route path="/sources" element={<Navigate to="/" replace />} />
          <Route path="/import" element={<Navigate to="/" replace />} />
          <Route path="/subtitles" element={<Navigate to="/library" replace />} />
          <Route path="/tasks" element={<Navigate to="/activity" replace />} />
        </Route>
      </Routes>
    </AuthProvider>
  );
}
