import { Layout, Menu } from 'antd';
import {
  DashboardOutlined,
  FundOutlined,
  RocketOutlined,
  ExperimentOutlined,
  LineChartOutlined,
  SettingOutlined,
  RobotOutlined,
  ThunderboltOutlined,
  BulbOutlined,
} from '@ant-design/icons';
import { useNavigate, useLocation } from 'react-router-dom';
import { useFeatureProfile } from '@/api/settings';
import { useAppStore } from '@/stores';
import type { MenuProps } from 'antd';

const { Sider } = Layout;

// 后端配置不可用时默认展示完整导航；只有显式 VITE_PERSONAL_MODE=true 才收敛。
const fallbackPersonalMode = import.meta.env.VITE_PERSONAL_MODE === 'true';

const coreMenuItems: MenuProps['items'] = [
  {
    key: '/',
    icon: <DashboardOutlined />,
    label: '概览',
  },
  {
    key: '/discovery',
    icon: <RocketOutlined />,
    label: '基金发现',
  },
  {
    key: '/funds',
    icon: <FundOutlined />,
    label: '基金检索',
  },
  {
    key: '/backtests',
    icon: <LineChartOutlined />,
    label: '回测分析',
  },
  {
    key: '/advisor',
    icon: <BulbOutlined />,
    label: '组合检查',
  },
  {
    key: '/settings',
    icon: <SettingOutlined />,
    label: '系统设置',
  },
];

const researchMenuItems: MenuProps['items'] = [
  {
    key: '/strategies',
    icon: <ExperimentOutlined />,
    label: '策略管理',
  },
  {
    key: '/simulations',
    icon: <ThunderboltOutlined />,
    label: '模拟预测',
  },
];

const aiMenuItems: MenuProps['items'] = [
  {
    key: '/ai',
    icon: <RobotOutlined />,
    label: 'AI 助手',
  },
];

export function AppSidebar() {
  const navigate = useNavigate();
  const location = useLocation();
  const collapsed = useAppStore((s) => s.sidebarCollapsed);
  const menuModeOverride = useAppStore((s) => s.menuModeOverride);
  const aiMenuVisibleOverride = useAppStore((s) => s.aiMenuVisibleOverride);
  const { data: featureProfile } = useFeatureProfile();

  const serverPersonalMode = featureProfile?.personal_mode ?? fallbackPersonalMode;
  const personalMode = menuModeOverride === 'full' ? false : menuModeOverride === 'personal' ? true : serverPersonalMode;
  const showAiMenu = aiMenuVisibleOverride ?? (featureProfile ? featureProfile.feature_ai === true : true);
  const menuItems: MenuProps['items'] = personalMode
    ? coreMenuItems
    : [
        ...(coreMenuItems || []),
        ...(researchMenuItems || []),
        ...(showAiMenu ? aiMenuItems || [] : []),
      ];

  const handleMenuClick: MenuProps['onClick'] = ({ key }) => {
    navigate(key);
  };

  // Determine selected key from current path
  const selectedKey = '/' + (location.pathname.split('/')[1] || '');

  return (
    <Sider
      className="app-sidebar"
      collapsible
      collapsed={collapsed}
      onCollapse={(value) => useAppStore.getState().setSidebarCollapsed(value)}
      width={220}
    >
      <div className="app-sidebar-brand">
        <div className="app-sidebar-logo">FQ</div>
        {!collapsed && (
          <div className="app-sidebar-brand-text">
            <strong>基金量化平台</strong>
            <span>Personal Research Terminal</span>
          </div>
        )}
      </div>

      {!collapsed && <div className="app-sidebar-section-label">WORKSPACE</div>}
      <Menu
        className="app-sidebar-menu"
        theme="dark"
        mode="inline"
        selectedKeys={[selectedKey]}
        items={menuItems}
        onClick={handleMenuClick}
      />

      {!collapsed && (
        <div className="app-sidebar-footer">
          <strong>{personalMode ? '个人模式' : '完整研究模式'}</strong>
          <span>先看数据质量，再做筛选、回测和组合检查；平台结果不构成投资建议。</span>
        </div>
      )}
    </Sider>
  );
}
