import { Layout } from 'antd';
import { Outlet } from 'react-router-dom';
import { AppSidebar } from './AppSidebar';
import { AppHeader } from './AppHeader';
import { useAppStore } from '@/stores';

const { Content } = Layout;

export function AppLayout() {
  const sidebarCollapsed = useAppStore((s) => s.sidebarCollapsed);

  return (
    <Layout className="app-root-layout">
      <AppSidebar />
      <Layout
        className="app-main-layout"
        style={{ marginLeft: sidebarCollapsed ? 80 : 220 }}
      >
        <AppHeader />
        <Content className="app-content">
          <div className="app-content-frame">
            <Outlet />
          </div>
        </Content>
      </Layout>
    </Layout>
  );
}
