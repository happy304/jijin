import { Alert, Button, Card, Space, Typography } from 'antd';
import { advisorViewModeLabel, type AdvisorViewMode } from '@/utils/advisorPreferences';

const { Text } = Typography;

export function AdvisorPageHeader({
  viewMode,
  showAdvancedResearch,
  onChangeViewMode,
}: {
  viewMode: AdvisorViewMode;
  showAdvancedResearch: boolean;
  onChangeViewMode: (mode: AdvisorViewMode) => void;
}) {
  return (
    <Card className="soft-card">
      <Space direction="vertical" size={12} style={{ width: '100%' }}>
        <Space align="center" style={{ width: '100%', justifyContent: 'space-between' }} wrap>
          <Text type="secondary">当前查看：{advisorViewModeLabel(viewMode)}</Text>
          <Space wrap>
            <Button type={viewMode === 'novice' ? 'primary' : 'default'} onClick={() => onChangeViewMode('novice')}>新手模式</Button>
            <Button type={viewMode === 'expert' ? 'primary' : 'default'} onClick={() => onChangeViewMode('expert')}>专家模式</Button>
          </Space>
        </Space>

        <Alert
          type="warning"
          showIcon
          message="个人研究辅助，不构成投资建议或交易指令"
          description="本页面用于检查基金池与当前持仓的风险、数据质量和调仓参考区间。基金过往业绩不代表未来表现，模型结果依赖数据质量、计算口径和参数假设，请结合自身风险承受能力独立判断。"
        />

        {!showAdvancedResearch && (
          <Alert
            type="info"
            showIcon
            message="已启用个人默认视图"
            description="OOS/PBO、Walk-Forward、截面 IC 等高级研究入口默认隐藏；如需专家诊断，可在环境变量中开启 Advisor 高级治理后重启服务。"
          />
        )}
      </Space>
    </Card>
  );
}
