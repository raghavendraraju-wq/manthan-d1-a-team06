import { Route, Routes } from 'react-router';
import { RequireRole } from './ui.jsx';
import Agent from './pages/Agent.jsx';
import CustomerWizard from './pages/CustomerWizard.jsx';
import Finance from './pages/Finance.jsx';
import Home from './pages/Home.jsx';
import Manager from './pages/Manager.jsx';
import { ReturnStatusPage } from './pages/ReturnStatus.jsx';
import StaffSignIn from './pages/StaffSignIn.jsx';

const guarded = (role, page) => <RequireRole role={role}>{page}</RequireRole>;

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Home />} />
      <Route path="/return" element={<CustomerWizard />} />
      <Route path="/returns/:returnId" element={<ReturnStatusPage />} />
      <Route path="/staff" element={<StaffSignIn />} />
      <Route path="/agent" element={guarded('agent', <Agent />)} />
      <Route path="/finance" element={guarded('finance', <Finance />)} />
      <Route path="/manager" element={guarded('manager', <Manager />)} />
      <Route path="*" element={<Home />} />
    </Routes>
  );
}
