import { BrowserRouter, Routes, Route } from "react-router-dom";
import { AuthProvider, AuthGuard } from "./auth";
import Login from "./pages/Login";
import Signup from "./pages/Signup";
import Dashboard from "./pages/Dashboard";
import Project from "./pages/Project";
import Studio from "./pages/Studio";
import Settings from "./pages/Settings";
import Admin from "./pages/Admin";
import EditorialReview from "./pages/EditorialReview";

export default function App() {
  return (
    <BrowserRouter basename="/studio">
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/signup" element={<Signup />} />
          <Route path="/" element={<AuthGuard><Dashboard /></AuthGuard>} />
          <Route path="/projects/:id" element={<AuthGuard><Project /></AuthGuard>} />
          <Route path="/projects/:id/studio" element={<AuthGuard><Studio /></AuthGuard>} />
          <Route path="/settings" element={<AuthGuard><Settings /></AuthGuard>} />
          <Route path="/admin" element={<AuthGuard><Admin /></AuthGuard>} />
          <Route path="/editorial" element={<AuthGuard><EditorialReview /></AuthGuard>} />
          <Route path="*" element={<AuthGuard><Dashboard /></AuthGuard>} />
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  );
}
