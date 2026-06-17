import { Card, Descriptions, Tag } from 'antd';
import type { FundDetail } from '@/api/funds';
import { fundTypeLabel } from '@/utils/fundType';

export interface FundBasicInfoCardProps {
  fund: FundDetail;
}

export function FundBasicInfoCard({ fund }: FundBasicInfoCardProps) {
  return (
    <Card title="基础信息" style={{ marginBottom: 16 }}>
      <Descriptions column={{ xs: 1, sm: 2, md: 3 }} bordered size="small">
        <Descriptions.Item label="基金代码">{fund.code}</Descriptions.Item>
        <Descriptions.Item label="基金名称">{fund.name}</Descriptions.Item>
        <Descriptions.Item label="基金类型">
          {fundTypeLabel(fund.fund_type)}
        </Descriptions.Item>
        <Descriptions.Item label="基金子类型">{fund.sub_type || '-'}</Descriptions.Item>
        <Descriptions.Item label="基金公司">{fund.company_id || '-'}</Descriptions.Item>
        <Descriptions.Item label="成立日期">{fund.inception_date || '-'}</Descriptions.Item>
        <Descriptions.Item label="业绩基准">{fund.benchmark || '-'}</Descriptions.Item>
        <Descriptions.Item label="币种">{fund.currency}</Descriptions.Item>
        <Descriptions.Item label="状态">
          <Tag color={fund.status === 'active' ? 'green' : 'default'}>
            {fund.status === 'active' ? '正常' : fund.status}
          </Tag>
        </Descriptions.Item>
        <Descriptions.Item label="是否可申购">
          <Tag color={fund.is_purchasable ? 'green' : 'red'}>
            {fund.is_purchasable ? '是' : '否'}
          </Tag>
        </Descriptions.Item>
        <Descriptions.Item label="申购限额">
          {fund.purchase_limit ? `${parseFloat(fund.purchase_limit).toLocaleString()} 元` : '无限制'}
        </Descriptions.Item>
        <Descriptions.Item label="数据来源">{fund.source || '-'}</Descriptions.Item>
      </Descriptions>
    </Card>
  );
}
