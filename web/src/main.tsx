import { StrictMode, lazy } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import "./styles.css";

const Overview = lazy(() => import("./pages/Overview"));
const Coverage = lazy(() => import("./pages/Coverage"));
const Grid = lazy(() => import("./pages/Grid"));
const Events = lazy(() => import("./pages/Events"));
const Unit = lazy(() => import("./pages/Unit"));
const OutletPage = lazy(() => import("./pages/OutletPage"));
const Methodology = lazy(() => import("./pages/Methodology"));
const NotFound = lazy(() => import("./pages/NotFound"));

// Links from the old GitHub Pages address used hash routes (…/fifth-estate/#/events?w=30). GitHub redirects
// them here with the hash intact, so turn it into the path; /events.html (the file behind /events) → /events.
const { pathname, search, hash } = window.location;
if (hash.startsWith("#/")) window.history.replaceState(null, "", hash.slice(1));
else if (pathname.endsWith(".html")) window.history.replaceState(null, "", pathname.replace(/(index)?\.html$/, "") + search + hash);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <Routes>
          <Route element={<Layout />}>
            <Route index element={<Overview />} />
            <Route path="coverage" element={<Coverage />} />
            <Route path="grid" element={<Grid />} />
            <Route path="events" element={<Events />} />
            <Route path="unit/:kind/:id" element={<Unit />} />
            <Route path="outlet/:id" element={<OutletPage />} />
            <Route path="methodology" element={<Methodology />} />
            <Route path="*" element={<NotFound />} />
          </Route>
      </Routes>
    </BrowserRouter>
  </StrictMode>,
);
