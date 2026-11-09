import { Route } from "react-router";
import { DashboardPage } from "./pages/DashboardPage";
import { NewRefundPage } from "./pages/NewRefundPage";
import { NotificationsPage } from "./pages/NotificationsPage";
import { QueuePage } from "./pages/QueuePage";
import { RefundDetailPage } from "./pages/RefundDetailPage";
import { WorkflowRulesPage } from "./pages/WorkflowRulesPage";

export const refundsRoutes = (
  <Route path="refunds">
    <Route index element={<DashboardPage />} />
    <Route path="queue" element={<QueuePage />} />
    <Route path="new" element={<NewRefundPage />} />
    <Route path="notifications" element={<NotificationsPage />} />
    <Route path="rules" element={<WorkflowRulesPage />} />
    <Route path=":id" element={<RefundDetailPage />} />
  </Route>
);
