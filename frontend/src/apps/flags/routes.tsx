import { Route } from "react-router";
import { FlagsPage } from "./FlagsPage";

export const flagsRoutes = (
  <Route path="feature-flags" element={<FlagsPage />} />
);
