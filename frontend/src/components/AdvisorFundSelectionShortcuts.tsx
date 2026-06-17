import { Button, Card, Space, Tag, Typography } from 'antd';
import { AdvisorQuickFundTags as QuickFundTags } from '@/components/AdvisorQuickFundTags';

const { Text } = Typography;

interface FavoriteGroup {
  name: string;
  fund_codes: string[];
}

interface AdvisorFundSelectionShortcutsProps {
  recentFunds: string[];
  favoriteGroups: FavoriteGroup[];
  hotFundCodes: string[];
  onPickFund: (code: string) => void;
  onApplyFavoriteGroup: (fundCodes: string[]) => void;
  onSaveCurrentSelection: () => void;
}

export function AdvisorFundSelectionShortcuts({
  recentFunds,
  favoriteGroups,
  hotFundCodes,
  onPickFund,
  onApplyFavoriteGroup,
  onSaveCurrentSelection,
}: AdvisorFundSelectionShortcutsProps) {
  return (
    <Card size="small" style={{ marginBottom: 12 }}>
      <Space direction="vertical" style={{ width: '100%' }} size={8}>
        <QuickFundTags title="最近使用" color="blue" codes={recentFunds} onPick={onPickFund} />
        {favoriteGroups.length > 0 && (
          <Space wrap>
            <Text type="secondary">自选组合</Text>
            {favoriteGroups.map((group) => (
              <Tag key={group.name} color="purple" style={{ cursor: 'pointer' }} onClick={() => onApplyFavoriteGroup(group.fund_codes)}>
                {group.name}
              </Tag>
            ))}
          </Space>
        )}
        <QuickFundTags title="热门基金" color="gold" codes={hotFundCodes} onPick={onPickFund} />
        <Space wrap>
          <Button size="small" onClick={onSaveCurrentSelection}>保存当前选择为自选组合</Button>
          <Text type="secondary">适合把常用基金池、策略基金池沉淀成快捷入口。</Text>
        </Space>
      </Space>
    </Card>
  );
}
