/**
 * Natural Language Strategy Generation component.
 *
 * Provides a text input where users can describe a strategy in natural
 * language. Calls the POST /ai/strategy-gen endpoint and displays the
 * generated configuration for user confirmation.
 *
 * Requirements: 11.15, 11.16
 */

import { useState } from 'react';
import {
  Card,
  Input,
  Button,
  Alert,
  Descriptions,
  Tag,
  Space,
  Typography,
  Spin,
} from 'antd';
import { BulbOutlined, CheckCircleOutlined, WarningOutlined } from '@ant-design/icons';
import { useGenerateStrategy, type StrategyGenResponse } from '@/api/ai';

const { TextArea } = Input;
const { Text } = Typography;

interface NLStrategyGenProps {
  /** Called when user confirms the generated strategy config */
  onConfirm?: (config: StrategyGenResponse) => void;
}

export function NLStrategyGen({ onConfirm }: NLStrategyGenProps) {
  const [description, setDescription] = useState('');
  const [result, setResult] = useState<StrategyGenResponse | null>(null);

  const generateMutation = useGenerateStrategy();

  const handleGenerate = async () => {
    if (!description.trim()) return;

    try {
      const data = await generateMutation.mutateAsync({ description: description.trim() });
      setResult(data);
    } catch {
      // Error handled by API interceptor
    }
  };

  const handleConfirm = () => {
    if (result && onConfirm) {
      onConfirm(result);
    }
  };

  return (
    <Card
      title={
        <Space>
          <BulbOutlined />
          <span>用自然语言描述策略</span>
        </Space>
      }
      style={{ marginBottom: 16 }}
    >
      <TextArea
        rows={3}
        placeholder="例如：帮我做一个动量轮动策略，每月从5只基金中选表现最好的3只，用Sharpe比率评分"
        value={description}
        onChange={(e) => setDescription(e.target.value)}
        maxLength={2000}
        showCount
      />

      <Button
        type="primary"
        icon={<BulbOutlined />}
        onClick={handleGenerate}
        loading={generateMutation.isPending}
        disabled={!description.trim()}
        style={{ marginTop: 12 }}
      >
        AI 生成策略配置
      </Button>

      {generateMutation.isPending && (
        <div style={{ marginTop: 16, textAlign: 'center' }}>
          <Spin tip="正在生成策略配置..." />
        </div>
      )}

      {result && (
        <div style={{ marginTop: 16 }}>
          {result.is_valid ? (
            <Alert
              type="success"
              icon={<CheckCircleOutlined />}
              message="策略配置生成成功"
              description={result.reasoning}
              showIcon
              style={{ marginBottom: 12 }}
            />
          ) : (
            <Alert
              type="warning"
              icon={<WarningOutlined />}
              message="策略配置存在问题"
              description={
                <ul style={{ margin: 0, paddingLeft: 16 }}>
                  {result.validation_errors.map((err, i) => (
                    <li key={i}>{err}</li>
                  ))}
                </ul>
              }
              showIcon
              style={{ marginBottom: 12 }}
            />
          )}

          <Descriptions bordered size="small" column={1}>
            <Descriptions.Item label="策略类型">
              <Tag color="blue">{result.strategy_type}</Tag>
            </Descriptions.Item>
            <Descriptions.Item label="策略名称">{result.name}</Descriptions.Item>
            <Descriptions.Item label="参数">
              <Text code>{JSON.stringify(result.params, null, 2)}</Text>
            </Descriptions.Item>
            <Descriptions.Item label="基金池">
              <Text code>{JSON.stringify(result.universe, null, 2)}</Text>
            </Descriptions.Item>
          </Descriptions>

          {result.is_valid && onConfirm && (
            <Button
              type="primary"
              onClick={handleConfirm}
              style={{ marginTop: 12 }}
            >
              使用此配置
            </Button>
          )}
        </div>
      )}
    </Card>
  );
}
