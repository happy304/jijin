import { Alert } from 'antd';

export function AdvisorAdvancedResearchNotice() {
  return (
    <Alert
      type="info"
      showIcon
      style={{ marginBottom: 16 }}
      message="高级研究入口"
      description="本区域包含引擎健康、OOS/PBO、Walk-Forward 等专家诊断，默认个人模式下隐藏；可在环境变量中开启 Advisor 高级治理后使用。"
    />
  );
}
