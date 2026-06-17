import { Button, Layout, Space, Tag, Typography } from 'antd';
import {
  ApiOutlined,
  BulbOutlined,
  CalendarOutlined,
  GithubOutlined,
  SearchOutlined,
  SafetyCertificateOutlined,
} from '@ant-design/icons';
import { Link, useLocation } from 'react-router-dom';
import dayjs from 'dayjs';

const { Header } = Layout;
const { Text } = Typography;

const PAGE_TITLES: Record<string, { title: string; subtitle: string }> = {
  '/': { title: '研究工作台', subtitle: '数据、筛选、回测与组合检查入口' },
  '/discovery': { title: '基金发现', subtitle: '排行榜、4433 筛选与截面评分' },
  '/funds': { title: '基金检索', subtitle: '本地基金库、净值质量与持仓穿透' },
  '/backtests': { title: '回测分析', subtitle: '验证策略表现、风险与数据质量' },
  '/advisor': { title: '组合检查', subtitle: '个人持仓风险与调仓参考' },
  '/strategies': { title: '策略管理', subtitle: '策略配置、信号与研究模板' },
  '/simulations': { title: '模拟预测', subtitle: '情景推演与收益风险模拟' },
  '/ai': { title: 'AI 助手', subtitle: '自然语言研究与报告辅助' },
  '/settings': { title: '系统设置', subtitle: '功能入口、调度和指标说明' },
};

function getCurrentPage(pathname: string) {
  const root = '/' + (pathname.split('/')[1] || '');
  return PAGE_TITLES[root] || PAGE_TITLES['/'];
}

export function AppHeader() {
  const location = useLocation();
  const currentPage = getCurrentPage(location.pathname);
  const today = dayjs().format('YYYY年MM月DD日');

  return (
    <Header className="app-header">
      <div className="app-header-title">
        <strong>{currentPage.title}</strong>
        <span>{currentPage.subtitle}</span>
      </div>

      <div className="app-header-actions">
        <Tag icon={<CalendarOutlined />} color="blue">
          {today}
        </Tag>
        <Tag icon={<ApiOutlined />} color="success">
          API /api
        </Tag>
        <Space size={8} className="app-header-quick-actions">
          <Link to="/funds">
            <Button size="small" icon={<SearchOutlined />}>检索</Button>
          </Link>
          <Link to="/advisor">
            <Button size="small" type="primary" icon={<BulbOutlined />}>组合检查</Button>
          </Link>
          <Link to="/settings">
            <Button size="small" icon={<SafetyCertificateOutlined />}>设置</Button>
          </Link>
        </Space>
        <Text type="secondary">v0.1.0</Text>
        <a
          href="https://github.com"
          target="_blank"
          rel="noopener noreferrer"
          style={{ color: '#59677d', lineHeight: 0 }}
          aria-label="打开 GitHub"
        >
          <GithubOutlined style={{ fontSize: 20 }} />
        </a>
      </div>
    </Header>
  );
}
