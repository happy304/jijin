import { Button } from 'antd';
import { Link } from 'react-router-dom';
import { HomeOutlined, SearchOutlined } from '@ant-design/icons';

export function NotFoundPage() {
  return (
    <div className="page-shell">
      <section className="section-hero">
        <div className="section-hero-content">
          <div className="page-eyebrow">404 · Not Found</div>
          <h2>页面没有找到</h2>
          <p>
            你访问的地址可能已经移动、删除，或者输入有误。
            可以返回首页，继续基金检索或研究分析。
          </p>
          <div className="section-hero-actions">
            <Link to="/">
              <Button type="primary" icon={<HomeOutlined />}>返回首页</Button>
            </Link>
            <Link to="/funds">
              <Button icon={<SearchOutlined />}>去基金检索</Button>
            </Link>
          </div>
        </div>
      </section>
    </div>
  );
}
