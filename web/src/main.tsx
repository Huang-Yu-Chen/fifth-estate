import { StrictMode, lazy } from "react";
import { createRoot } from "react-dom/client";
import { HashRouter, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import "./styles.css";

const Overview = lazy(() => import("./pages/Overview"));
const Coverage = lazy(() => import("./pages/Coverage"));
const Grid = lazy(() => import("./pages/Grid"));
const Events = lazy(() => import("./pages/Events"));
const Unit = lazy(() => import("./pages/Unit"));
const OutletPage = lazy(() => import("./pages/OutletPage"));
const Methodology = lazy(() => import("./pages/Methodology"));

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <HashRouter>
      <Routes>
          <Route element={<Layout />}>
            <Route index element={<Overview />} />
            <Route path="coverage" element={<Coverage />} />
            <Route path="grid" element={<Grid />} />
            <Route path="events" element={<Events />} />
            <Route path="unit/:kind/:id" element={<Unit />} />
            <Route path="outlet/:id" element={<OutletPage />} />
            <Route path="methodology" element={<Methodology />} />
            <Route path="*" element={<Overview />} />
          </Route>
      </Routes>
    </HashRouter>
  </StrictMode>,
);
